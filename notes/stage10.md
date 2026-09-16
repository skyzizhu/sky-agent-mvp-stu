# Stage 10 复盘：MCP 客户端接入（2026-09-16）

## 建了什么
- `common/mcp_client.py`：MCPClient——后台线程 asyncio 会话常驻，
  list_tools 转换为 OpenAI tools 格式（前缀防冲突），call_tool 同步转发
- `production_agent.py`：`MCP_FS=1` 启用，接官方 filesystem server（npx），
  dispatch 分支转发 MCP 调用；写类工具自动升级危险级走 HITL

## 端到端验证
任务"列出项目根目录 .md 文件并说明用途"：
- 发现 14 个工具（read/write/edit/move/create_directory...）
- agent 自主用 search_files + list_directory + read_file 完成调研
- 输出准确的文件清单表格（README.md / AGENT_NODES.md 及用途）
- 记忆注入 ✓ / 记忆提取 ✓ / RunLog 记录 ✓ —— 三件套与 MCP 自然协作

## 踩坑（第4次同族bug）
- pydantic 蛇形命名：Tool.input_schema ≠ 协议JSON的 inputSchema
- anyio TaskGroup 把真异常包进 ExceptionGroup，必须打 traceback
- 教训固化：第三方 SDK 的 pydantic 对象，先 dir() 看真名再用

## 核心认知（与已学的连接）
- MCP 工具 inputSchema = 我们手写的 JSON Schema 菜单卡（同源，换信封即可）
- MCP 只标准化工具的发现/描述/调用，不碰循环与上下文 → agent 主权不变
- 接入外部生态时，安全件要自动跟随：外部工具默认不可信，写类走 HITL

## 剩余选修
- [ ] LangGraph/smolagents 重写对比
- [ ] 反向：把自己的 web_search 包成 MCP server 供他人接入
