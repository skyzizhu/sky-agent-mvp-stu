"""
Stage 11：Agent 可注入内核（事件化改造的产物）。

设计原则（依赖注入式重构）：核心决策流程/工具/提示词/护栏**零逻辑改动**，
只把循环里两个写死的 IO 口抽成可注入接口：
  emit(type, **payload)     输出口：CLI 注入 print 渲染；Web 注入 SSE 队列推送
  approver(name, args)      审批口：CLI 注入终端 input；Web 注入"网页按钮+POST"
  stop_check()              中止检查：CLI 恒 False；Web 查中止按钮状态
注入 CLI 实现 = 现在的终端 agent；注入 Web 实现 = GUI 版 agent。

对外：ResearchAgent(emit, approver, stop_check, budget_max, mcp_fs).run(question)
"""
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import config
import common.tools as tools_mod
from common.llm_client import make_client, call_llm
from common.context import compact_messages
from common.memory import MemoryStore, extract_preferences
from common.guardrails import (Budget, approval_required, ask_human,
                               WRAP_UP_MSG, DANGEROUS_TOOLS)
from common.observability import log_run


def _cli_emit(type: str, **p):
    """CLI 默认渲染：与 Stage 8 终端体验一致。"""
    if type == "memory_inject":
        print(f"[记忆] 注入 {p['count']} 条长期偏好" +
              (f"：\n{p['text']}" if p.get("text") else "（首次会话，暂无）"))
    elif type == "mcp":
        print(f"[MCP] {p['msg']}")
    elif type == "guardrail":
        print(f"[护栏] token 预算: {p['max']} | 危险工具: {p['dangerous']}")
    elif type == "step":
        print(f"    [第{p['step']}步] " +
              (f"点菜: {','.join(p['tools'])}" if p["tools"] else "✍️ 输出最终报告")
              + f" (输入{p['prompt_tokens']}tok)")
    elif type == "tool_result":
        print(f"      ↳ {p['name']} → {str(p['result'])[:120]}")
    elif type == "compact":
        print(f"   ★ 压缩: 上下文 {p['before']}→{p['after']} 字符")
    elif type == "budget":
        print(f"   [护栏] 预算接近上限({p['used']}/{p['max']})，注入收尾指令")
    elif type == "memory_extract":
        print(f"[记忆] 提取到 {p['count']} 条候选: {p['prefs']}")
    elif type == "memory_saved":
        print(f"[记忆] 本次沉淀 {p['added']} 条新偏好")
    elif type == "final":
        print(f"[结算] 预算 {p['used']}/{p['max']} tok | 停止原因: {p['stop_reason']}")


def _cli_approver(tool: str, args_json: str):
    return ask_human(tool, args_json)


def send_report(recipient: str, subject: str) -> str:
    """危险工具示例：模拟对外发送（MVP 不真发）。"""
    return json.dumps({"status": "sent(mock)", "recipient": recipient,
                       "subject": subject}, ensure_ascii=False)


SEND_TOOL = [{
    "type": "function",
    "function": {
        "name": "send_report",
        "description": "把最终研究报告发送到指定邮箱。【危险操作】仅在用户明确要求发送时使用。",
        "parameters": {
            "type": "object",
            "properties": {
                "recipient": {"type": "string", "description": "收件人邮箱"},
                "subject": {"type": "string", "description": "邮件主题"},
            },
            "required": ["recipient", "subject"],
        },
    },
}]


class ResearchAgent:
    """生产研究 agent 内核：逻辑与 Stage 8 完全一致，IO 可注入。"""

    def __init__(self, emit=None, approver=None, stop_check=None,
                 budget_max=None, use_mcp=False, impl="agent_core"):
        self.emit = emit or _cli_emit
        self.approver = approver or _cli_approver
        self.stop_check = stop_check or (lambda: False)
        self.budget_max = budget_max or (3000 if os.getenv("LOW_BUDGET") else 60000)
        self.use_mcp = use_mcp
        self.impl = impl
        self.stop_reason = "model_done"
        self._wrap_injected = False

    # ---------- 主流程（逻辑与 Stage 8 逐行对应） ----------
    def run(self, question: str) -> dict:
        client = make_client()

        # 记忆注入
        memory = MemoryStore(ROOT / "notes" / "memory.json")
        prefs_text = memory.format_for_prompt()
        self.emit("memory_inject", count=len(memory.data.get("facts", [])),
                  text=prefs_text)

        system_prompt = (
            "你是一个严谨的研究助理。规则：\n"
            "1. 用 web_search/fetch_url 查证，禁止编造；\n"
            "2. 关键发现随手 note_write；收尾前必须 note_read；\n"
            "3. 报告带 [n](url) 引用，简体中文；\n"
            "4. send_report 是危险操作，仅当用户明确要求发送时才使用。\n"
            + (prefs_text + "\n" if prefs_text else "")
        )

        # 工具装配
        tools_mod.set_notes_file(ROOT / "notes" / "agent_memory" /
                                 f"run_{int(time.time())}.md")
        tools_mod.REGISTRY.clear()
        tools_mod.REGISTRY.update(tools_mod.REAL_REGISTRY)
        tools_mod.REGISTRY["send_report"] = send_report
        all_tools = tools_mod.REAL_TOOLS + SEND_TOOL

        self.mcp_clients = []
        if self.use_mcp:
            for spec in config.MCP_SERVERS:   # 多 server：逐个启动，工具全合并
                self.mcp_clients.append(self._setup_mcp(spec))
            for c in self.mcp_clients:
                all_tools += c.openai_tools()
                for t in c.openai_tools():
                    n = t["function"]["name"]
                    if any(k in n for k in ("write", "edit", "move", "create")):
                        DANGEROUS_TOOLS[n] = "外部MCP写操作：会修改本地文件"
            total = sum(len(c.openai_tools()) for c in self.mcp_clients)
            self.emit("mcp", msg=f"已接入 {len(self.mcp_clients)} 个 MCP server，"
                                 f"新增 {total} 个工具（写类已标危险级）")

        budget = Budget(self.budget_max)
        self.emit("guardrail", max=budget.max,
                  dangerous=list(DANGEROUS_TOOLS))

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": question},
        ]
        stopped_reason = "model_done"
        tool_sequence, errors = [], []
        budget_warned = False
        step = 0
        final = ""

        for step in range(1, config.MAX_LOOP_STEPS + 1):
            # 中止检查（用户点了⏹）：注入一次收尾指令
            if self.stop_check() and not self._wrap_injected:
                self._wrap_injected = True
                stopped_reason = "user_stop"
                messages.append({"role": "user", "content": WRAP_UP_MSG})
                self.emit("budget", used=budget.used, max=budget.max,
                          note="用户请求中止，注入收尾指令")
            if step == config.MAX_LOOP_STEPS - 1:
                messages.append({"role": "user", "content":
                    "⚠️ 时间将尽：note_read 后输出最终报告，不要再搜索。"})
            if budget.near_limit() and not budget_warned:
                budget_warned = True
                messages.append({"role": "user", "content": WRAP_UP_MSG})
                self.emit("budget", used=budget.used, max=budget.max)

            msg, usage = call_llm(client, messages, tools=all_tools)
            budget.add(usage)
            messages.append(msg)
            self.emit("step", step=step,
                      tools=[tc.function.name for tc in (msg.tool_calls or [])],
                      prompt_tokens=usage.prompt_tokens)

            if not msg.tool_calls:
                final = msg.content or ""
                break

            if budget.exhausted:
                stopped_reason = "budget_exhausted"
                break

            if usage.prompt_tokens > config.MAX_CONTEXT_TOKENS:
                before = len(json.dumps(messages, ensure_ascii=False, default=str))
                messages, _ = compact_messages(client, messages)
                after = len(json.dumps(messages, ensure_ascii=False, default=str))
                self.emit("compact", before=before, after=after)

            for tc in msg.tool_calls:
                tool_sequence.append(tc.function.name)
                args = json.loads(tc.function.arguments)

                if approval_required(tc.function.name):  # HITL
                    self.emit("approval_request", tool=tc.function.name,
                              args=tc.function.arguments,
                              reason=DANGEROUS_TOOLS.get(tc.function.name, ""))
                    approved, receipt = self.approver(tc.function.name,
                                                      tc.function.arguments)
                    self.emit("approval_result", tool=tc.function.name,
                              approved=approved)
                    messages.append({"role": "tool", "tool_call_id": tc.id,
                                     "content": receipt})
                    continue

                try:
                    owner = next((c for c in self.mcp_clients
                                  if c.owns(tc.function.name)), None)
                    result = (owner.call_tool(tc.function.name, args)
                              if owner else
                              tools_mod.dispatch(tc.function.name, args))
                except Exception as e:
                    result = f"工具执行异常({type(e).__name__})：{e}"
                    errors.append(f"{tc.function.name}: {e}")
                self.emit("tool_result", name=tc.function.name,
                          result=str(result)[:200])
                messages.append({"role": "tool", "tool_call_id": tc.id,
                                 "content": result})
        else:
            final = "（达到最大步数未能完成任务，请缩小问题范围后重试）"
            stopped_reason = "max_steps"

        # 会话结束：记忆提取 + 运行日志
        transcript = "\n".join(
            f"[{m['role']}] {m.get('content') or ''}"
            for m in messages if isinstance(m, dict))
        new_prefs = extract_preferences(client, transcript)
        self.emit("memory_extract", count=len(new_prefs), prefs=new_prefs)
        added = memory.merge(new_prefs)
        self.emit("memory_saved", added=added)

        rec = log_run(impl=self.impl, question=question[:80], steps=step,
                      tool_calls=tool_sequence, tokens=budget.used,
                      budget_max=budget.max, stop_reason=stopped_reason,
                      errors=errors, extra={"new_preferences": added})
        self.emit("final", used=budget.used, max=budget.max,
                  stop_reason=stopped_reason, run_id=rec["run_id"],
                  steps=step, answer=final)

        return {"answer": final, "stop_reason": stopped_reason,
                "steps": step, "tokens": budget.used, "errors": errors,
                "run_id": rec["run_id"]}

    def _setup_mcp(self, spec: dict):
        from common.mcp_client import MCPClient
        mcp = MCPClient(spec["name"], spec["command"], spec["args"])
        mcp.start()
        if mcp.error:
            raise RuntimeError(f"MCP server '{spec['name']}' 启动失败: {mcp.error[:300]}")
        return mcp
