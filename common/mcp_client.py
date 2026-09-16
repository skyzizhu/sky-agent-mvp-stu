"""
Stage 10：MCP 客户端 —— 让 agent 接入外部 MCP server 的工具生态。

核心认知（与项目已学的对应关系）：
- MCP 工具的 inputSchema 就是 JSON Schema —— 和我们手写的 tools 菜单卡同源，
  转换几乎是"换个信封"
- MCP 只标准化"工具的发现(list_tools)、描述(schema)、调用(call_tool)"，
  不碰 agent 的循环/上下文 —— 我们的核心代码零改动
- 架构：后台线程跑 asyncio 事件循环，维持与 server 的 stdio 会话常驻；
  主线程用 run_coroutine_threadsafe 提交调用（同步接口给同步的 agent loop）

用法:
    mcp = MCPClient("fs", "npx", ["-y", "@modelcontextprotocol/server-filesystem", "/some/dir"])
    mcp.start()
    tools = mcp.openai_tools()      # 直接合并进请求的 tools 参数
    result = mcp.call_tool("mcp_fs_read_file", {"path": "/some/dir/a.md"})
"""
import asyncio
import json
import threading


class MCPClient:
    def __init__(self, name: str, command: str, args: list):
        self.name = name
        self.prefix = f"mcp_{name}_"
        self.command, self.args = command, args
        self._tools = []
        self._loop = None
        self._session = None
        self._ready = threading.Event()
        self.error = None

    # ---------- 生命周期 ----------
    def start(self, timeout: int = 60):
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        if not self._ready.wait(timeout):
            raise RuntimeError(f"MCP server '{self.name}' 启动超时: {self.error}")

    def _run(self):
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self._loop.run_until_complete(self._main())

    async def _main(self):
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client
        params = StdioServerParameters(command=self.command, args=self.args)
        try:
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    self._session = session
                    resp = await session.list_tools()
                    # ★ 关键转换：MCP 的 inputSchema 就是 JSON Schema，
                    #   套上 OpenAI tools 的信封即可直接使用
                    self._tools = [{
                        "type": "function",
                        "function": {
                            "name": self.prefix + t.name,
                            "description": (t.description or t.name)[:600],
                            # 注意：SDK 的 pydantic 对象用蛇形命名 input_schema
                            # （对应协议JSON里的 inputSchema），别用驼峰
                            "parameters": t.input_schema or
                                          {"type": "object", "properties": {}},
                        },
                    } for t in resp.tools]
                    self._ready.set()
                    while True:          # 常驻保活，session 不能关
                        await asyncio.sleep(1)
        except Exception as e:
            import traceback
            self.error = f"{type(e).__name__}: {e}\n{traceback.format_exc()}"
            self._ready.set()

    def _submit(self, coro, timeout: int = 120):
        return asyncio.run_coroutine_threadsafe(coro, self._loop).result(timeout)

    # ---------- 对外接口 ----------
    def openai_tools(self) -> list:
        """转成 OpenAI tools 格式，直接合并进请求的 tools 参数。"""
        return self._tools

    def owns(self, tool_name: str) -> bool:
        return tool_name.startswith(self.prefix)

    def call_tool(self, tool_name: str, args: dict) -> str:
        """执行 MCP 工具，返回文本结果（回填给模型）。"""
        bare = tool_name[len(self.prefix):]

        async def _call(session):
            resp = await session.call_tool(bare, args)
            parts = []
            for c in resp.content:
                if hasattr(c, "text"):
                    parts.append(c.text)
            text = "\n".join(parts) or json.dumps(
                {"isError": resp.isError}, ensure_ascii=False)
            return text

        return self._submit(_call(self._session))
