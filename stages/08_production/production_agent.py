"""
Stage 8：生产版研究助理 —— 记忆 + HITL + 护栏 三件套集成。

运行: .venv/bin/python stages/08_production/production_agent.py [问题]
环境: LOW_BUDGET=1 用极小预算演示"优雅收尾"

四个验收实验（对应 notes/stage8.md）：
  实验A 记忆写入：会话里说出偏好 → 结束后 memory.json 出现该偏好
  实验B 记忆生效：新会话直接提问 → system prompt 里带着上次偏好
  实验C HITL：问题末尾加"查完发给 boss@example.com" → 触发人工 y/n
  实验D 优雅收尾：LOW_BUDGET=1 跑 → 预算触发后输出《阶段性结题报告》
"""
import json
import sys
import os
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import config
import common.tools as tools_mod
from common.llm_client import make_client, call_llm
from common.context import compact_messages
from common.memory import MemoryStore, extract_preferences
from common.guardrails import (Budget, approval_required, ask_human,
                               WRAP_UP_MSG, DANGEROUS_TOOLS)
from common.observability import log_run
from common.mcp_client import MCPClient

# ---- 危险工具：模拟"对外发送"（MVP 不真发，只演示 HITL 流程） ----
def send_report(recipient: str, subject: str) -> str:
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


def main():
    client = make_client()
    question = " ".join(sys.argv[1:]) or "DeepSeek API 目前的模型和定价是怎样的？"

    # ===== 记忆注入（会话启动时）=====
    memory = MemoryStore(ROOT / "notes" / "memory.json")
    prefs_text = memory.format_for_prompt()
    print(f"[记忆] 注入 {len(memory.data.get('facts', []))} 条长期偏好"
          + (f"：\n{prefs_text}" if prefs_text else "（首次会话，暂无）"))

    system_prompt = (
        "你是一个严谨的研究助理。规则：\n"
        "1. 用 web_search/fetch_url 查证，禁止编造；\n"
        "2. 关键发现随手 note_write；收尾前必须 note_read；\n"
        "3. 报告带 [n](url) 引用，简体中文；\n"
        "4. send_report 是危险操作，仅当用户明确要求发送时才使用。\n"
        + (prefs_text + "\n" if prefs_text else "")
    )

    tools_mod.set_notes_file(ROOT / "notes" / "agent_memory" /
                             f"prod_{int(time.time())}.md")
    tools_mod.REGISTRY.clear()
    tools_mod.REGISTRY.update(tools_mod.REAL_REGISTRY)
    tools_mod.REGISTRY["send_report"] = send_report
    all_tools = tools_mod.REAL_TOOLS + SEND_TOOL

    # ===== Stage 10：MCP 接入（opt-in，设 MCP_FS=1 启用）=====
    mcp = None
    if os.getenv("MCP_FS") == "1":
        mcp = MCPClient("fs", "npx", [
            "-y", "@modelcontextprotocol/server-filesystem", str(ROOT)])
        mcp.start()
        all_tools += mcp.openai_tools()
        # 安全设计：外部MCP工具默认不可信，写类自动归入危险级（走HITL）
        for t in mcp.openai_tools():
            n = t["function"]["name"]
            if any(k in n for k in ("write", "edit", "move", "create")):
                DANGEROUS_TOOLS[n] = "外部MCP写操作：会修改本地文件"
        print(f"[MCP] 已接入 filesystem server，"
              f"新增 {len(mcp.openai_tools())} 个工具（写类已标危险级）")

    # ===== 护栏：预算（LOW_BUDGET=1 时用 3000 演示优雅收尾）=====
    budget_max = 3000 if os.getenv("LOW_BUDGET") else 60000
    budget = Budget(budget_max)
    print(f"[护栏] token 预算: {budget_max} | 危险工具: {list(DANGEROUS_TOOLS)}\n")

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": question},
    ]
    stopped_reason = "model_done"
    tool_sequence, errors = [], []

    for step in range(1, config.MAX_LOOP_STEPS + 1):
        if step == config.MAX_LOOP_STEPS - 1:  # 步数死线（老朋友）
            messages.append({"role": "user", "content":
                "⚠️ 时间将尽：note_read 后输出最终报告，不要再搜索。"})
        if budget.near_limit():  # 预算死线 → 优雅收尾
            messages.append({"role": "user", "content": WRAP_UP_MSG})
            print(f"   [护栏] 预算接近上限({budget.used}/{budget.max})，注入收尾指令")

        msg, usage = call_llm(client, messages, tools=all_tools)
        budget.add(usage)
        messages.append(msg)

        if not msg.tool_calls:
            break

        if budget.exhausted:
            stopped_reason = "budget_exhausted"
            break

        if usage.prompt_tokens > config.MAX_CONTEXT_TOKENS:
            messages, _ = compact_messages(client, messages)

        # ===== HITL：危险工具执行前必须人工批准 =====
        for tc in msg.tool_calls:
            args = json.loads(tc.function.arguments)
            tool_sequence.append(tc.function.name)
            if approval_required(tc.function.name):
                approved, receipt = ask_human(tc.function.name,
                                              tc.function.arguments)
                messages.append({"role": "tool", "tool_call_id": tc.id,
                                 "content": receipt})
                continue  # 拒绝/批准的回执都作为结果喂回，批准了也不在循环里执行
            try:
                if mcp and mcp.owns(tc.function.name):
                    result = mcp.call_tool(tc.function.name, args)  # 转发给MCP server
                else:
                    result = tools_mod.dispatch(tc.function.name, args)
            except Exception as e:
                result = f"工具执行异常({type(e).__name__})：{e}"
                errors.append(f"{tc.function.name}: {e}")
            messages.append({"role": "tool", "tool_call_id": tc.id,
                             "content": result})

    # ===== 会话结束：提取长期偏好 → 合并进记忆 =====
    transcript = "\n".join(
        f"[{m['role']}] {m.get('content') or ''}"
        for m in messages if isinstance(m, dict))
    new_prefs = extract_preferences(client, transcript)
    print(f"[记忆] 提取到 {len(new_prefs)} 条候选: {new_prefs}")  # 可观测性：提取环节必须有回显
    added = memory.merge(new_prefs)

    # ===== Stage 9：统一运行日志（一处写入，处处可查）=====
    rec = log_run(impl="08_production", question=question[:80], steps=step,
                  tool_calls=tool_sequence, tokens=budget.used,
                  budget_max=budget.max, stop_reason=stopped_reason,
                  errors=errors, extra={"new_preferences": added})
    print(f"[观测] 运行已记录: run_id={rec['run_id']} → logs/runs.jsonl")
    print(f"\n[记忆] 本次沉淀 {added} 条新偏好 → {memory.path}")

    print(f"[结算] 预算 {budget.used}/{budget.max} tok | 停止原因: {stopped_reason}")
    print(f"\n{'='*62}\n最终输出:\n{(msg.content or '')[:800]}")


if __name__ == "__main__":
    main()
