"""
Stage 7：子 agent（Worker）—— 专项调研员。

和主 agent（v3）的区别：
  1. 没有规划节点——任务书由 orchestrator 写好派下来，它只管执行
  2. 无权拆题、无权派工——防止无限套娃
  3. 最终产出是【浓缩结论】（≤300字要点+来源），不是完整报告——
     它读几万 token 的网页，只把高信号结论交回主 agent（上下文隔离的关键）

run_sub_agent(client, brief, worker_id) 是对外入口，线程安全（可并行）。
"""
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import config
import common.tools as tools_mod
from common.llm_client import make_client, call_llm
from common.context import compact_messages

WORKER_SYSTEM = (
    "你是一名专项调研员，只负责接收到的这一项任务，不要越界调研其他内容。\n"
    "规则：\n"
    "1. 用 web_search/fetch_url 查证，禁止编造；\n"
    "2. 关键发现随手 note_write（要点+数据+来源域名）；\n"
    "3. 收工前最后一个动作必须是 note_read；\n"
    "4. 最终输出=调研结论（不是报告）：≤300字要点式，每条带来源域名，"
    "没查到的项如实标注'未查到'。"
)


def run_sub_agent(client, brief: dict, worker_id: str, max_steps: int = 10,
                  stagger: float = 0.0) -> dict:
    """
    brief: orchestrator 派下来的任务书 {"task": 完整任务描述, "focus": 重点}
    stagger: 错峰启动秒数（并行worker同时打搜索接口会触发限流，错峰缓解）
    返回: {"worker_id", "findings"(浓缩结论), "steps", "tokens", "seconds", "ok"}
    """
    t0 = time.time()
    if stagger:
        time.sleep(stagger)
    # 线程安全的独立笔记文件（每个 worker 一份，互不干扰）
    tools_mod.set_notes_file(ROOT / "notes" / "agent_memory" /
                             f"w{worker_id}_{int(t0)}.md")
    tools_mod.REGISTRY.clear()
    tools_mod.REGISTRY.update(tools_mod.REAL_REGISTRY)

    messages = [
        {"role": "system",
         "content": WORKER_SYSTEM + f"\n\n【你的任务书】\n{json.dumps(brief, ensure_ascii=False)}"},
        {"role": "user", "content": brief["task"]},
    ]

    tokens = 0
    findings = ""
    for step in range(1, max_steps + 1):
        # ★ 收敛护栏（Stage 5 的教训移植）：剩最后2步时注入死线，强制收尾。
        # 子agent是"专项调研员"，更容易陷进调研出不来，护栏是刚需。
        if step == max_steps - 1:
            messages.append({"role": "user",
                             "content": "⚠️ 调研时间即将用尽。立即：1) note_read 读取笔记；"
                                        "2) 输出你的调研结论（≤300字要点+来源域名）。"
                                        "不要再发起新的搜索。已查到的部分有多少写多少，"
                                        "没查到的如实标注。"})
        msg, usage = call_llm(client, messages, tools=tools_mod.REAL_TOOLS)
        tokens += usage.total_tokens
        messages.append(msg)

        if not msg.tool_calls:  # 它的"最终输出"=浓缩结论
            findings = msg.content or ""
            break

        if usage.prompt_tokens > config.MAX_CONTEXT_TOKENS:
            messages, _, _ = compact_messages(client, messages)

        for tc in msg.tool_calls:
            args = json.loads(tc.function.arguments)
            result = tools_mod.dispatch(tc.function.name, args)
            messages.append({"role": "tool", "tool_call_id": tc.id, "content": result})
    else:
        findings = f"（worker{worker_id} 步数耗尽，未完成）"

    return {"worker_id": worker_id, "brief": brief["task"], "findings": findings,
            "steps": step, "tokens": tokens,
            "seconds": round(time.time() - t0), "ok": "步数耗尽" not in findings}
