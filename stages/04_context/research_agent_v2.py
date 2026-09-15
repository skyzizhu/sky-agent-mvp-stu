"""
Stage 4：上下文工程 —— 给越来越胖的循环"瘦身"。

运行: .venv/bin/python stages/04_context/research_agent_v2.py
学习目标:
  1. compaction：历史超限就用摘要重建上下文，观察 token 如何被"按住"不再滚雪球
  2. 结构化笔记：agent 把关键发现写入 NOTES.md（上下文之外），写报告前读回
  3. 对比感受：Stage 3 同样的调研，token 是单调暴涨的；v2 有了安全阀
"""
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import config
import common.tools as tools_mod
from common.llm_client import make_client, call_llm, save_trace, show_step
from common.context import maybe_compact

QUESTION = ("调研 Coze、Dify、文心智能体平台 三个产品：各自定位是什么？"
            "收费模式是什么？最后对比成一段结论。要求每个产品都要查证。")

SYSTEM_PROMPT = (
    "你是一个严谨的研究助理。规则：\n"
    "1. 用 web_search/fetch_url 查证事实，禁止凭记忆编造；\n"
    "2. ★每查证完一个产品/子问题，必须立即 note_write 记录发现"
    "（含数据和来源域名）——上下文随时可能被压缩，只有笔记是可靠的；\n"
    "3. ★写最终回答前的最后一个动作必须是 note_read：读完全部笔记，"
    "严格以笔记为依据作答；若笔记里没有某项信息，说明该项未查证，如实说明；\n"
    "4. 结论注明来源，用简体中文，控制在400字以内。"
)


def main():
    # 初始化笔记文件（每次运行清空重来，便于观察）
    tools_mod.NOTES_FILE = ROOT / "notes" / "agent_memory" / "NOTES.md"
    tools_mod.NOTES_FILE.parent.mkdir(parents=True, exist_ok=True)
    tools_mod.NOTES_FILE.write_text("", encoding="utf-8")

    from common.tools import REAL_TOOLS, REAL_REGISTRY, dispatch
    client = make_client()
    tools_mod.REGISTRY.clear()
    tools_mod.REGISTRY.update(REAL_REGISTRY)

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": QUESTION},
    ]

    stats = {"total_prompt": 0, "total_completion": 0,
             "compactions": 0, "peak_prompt": 0}

    # 本阶段任务较深（3产品×2维度），放宽到30步；config 里的 20 是通用默认
    for step in range(1, 31):
        msg, usage = call_llm(client, messages, tools=REAL_TOOLS)
        messages.append(msg)

        # ---- token 记账（本阶段的主角）----
        stats["total_prompt"] += usage.prompt_tokens
        stats["total_completion"] += usage.completion_tokens
        stats["peak_prompt"] = max(stats["peak_prompt"], usage.prompt_tokens)

        if not msg.tool_calls:
            show_step(step, msg)
            save_trace("stage4_research", messages, tools=REAL_TOOLS,
                       extra={**stats, "total_steps": step})
            _report(stats, tools_mod.NOTES_FILE)
            return msg.content

        # ---- ★ compaction 检查：上一轮输入超阈值，先压缩再继续 ----
        if usage.prompt_tokens > config.MAX_CONTEXT_TOKENS:
            from common.context import compact_messages
            before = len(json.dumps(messages, ensure_ascii=False, default=str))
            messages, summary = compact_messages(client, messages)
            after = len(json.dumps(messages, ensure_ascii=False, default=str))
            stats["compactions"] += 1
            print(f"\n   ★ 第{stats['compactions']}次压缩: 上下文 {before}→{after} 字符"
                  f"（摘要保住了任务目标和关键发现）\n   摘要预览: {summary[:150]}...")

        for tc in msg.tool_calls:
            args = json.loads(tc.function.arguments)
            result = dispatch(tc.function.name, args)
            messages.append({"role": "tool", "tool_call_id": tc.id,
                             "content": result})

        show_step(step, msg)
        print(f"   [第{step}轮] 输入{usage.prompt_tokens} tok "
              f"(阈值{config.MAX_CONTEXT_TOKENS}) / 累计{stats['total_prompt']} tok")

    print("达到最大步数，强制退出")
    _report(stats, tools_mod.NOTES_FILE)


def _report(stats, notes_file):
    print(f"""
{'='*60}
📊 Token 账单（对照 Stage 3: 同样3步就烧了6k+，且单调暴涨）
   总输入: {stats['total_prompt']} tok | 总输出: {stats['total_completion']} tok
   单轮峰值: {stats['peak_prompt']} tok (阈值 {config.MAX_CONTEXT_TOKENS})
   压缩次数: {stats['compactions']}
   笔记文件: {notes_file} ({notes_file.stat().st_size if notes_file.exists() else 0} 字节)
""")


if __name__ == "__main__":
    main()
