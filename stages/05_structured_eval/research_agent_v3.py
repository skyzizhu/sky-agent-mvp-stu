"""
Stage 5：结构化输出 —— agent 从"自由发挥"到"按合同交付"。

运行: .venv/bin/python stages/05_structured_eval/research_agent_v3.py
学习目标:
  1. Plan：先用一次独立的 LLM 调用产出 JSON 研究大纲（结构化输出，JSON mode）
  2. Execute：按大纲逐项调研（复用 Stage 2/4 的循环+压缩+笔记）
  3. Report：最终报告必须带 [n](url) 引用编号——可核验的交付物
  4. 为什么先规划：大纲=确定性骨架，模型照单执行不易漏项（workflow与agent的混合）
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import config
import common.tools as tools_mod
from common.llm_client import make_client, call_llm
from common.context import compact_messages

SYSTEM_RESEARCH = (
    "你是一个严谨的研究助理。规则：\n"
    "1. 按研究大纲逐项用 web_search/fetch_url 查证，禁止编造；\n"
    "2. 每查证完一项立即 note_write（要点+数据+来源域名）；\n"
    "3. 写报告前的最后动作必须是 note_read，以笔记为准；\n"
    "4. ★报告中每个事实都要带引用编号，格式：结论[1]，并在末尾列出"
    "引用列表：[1](https://域名/路径) 来源名。"
)


# ---------- 节点A：规划（结构化输出，JSON mode） ----------
def plan(client, question: str) -> dict:
    """一次独立调用产出 JSON 大纲。response_format=json_object 强制合法JSON。"""
    resp = client.chat.completions.create(
        model=config.MODEL,
        messages=[
            {"role": "system",
             "content": "你是研究规划师。针对用户问题输出JSON研究大纲，格式："
                        '{"goal":"一句话目标","sub_questions":[{"id":1,"q":"子问题",'
                        '"what_to_verify":"要查证什么"}],"constraints":["约束"]}。'
                        "硬性要求：子问题不超过3个、what_to_verify一句话以内——"
                        "大纲必须在约10步内可调研完成，贪多会导致任务永远做不完。只规划，不执行。"},
            {"role": "user", "content": question},
        ],
        response_format={"type": "json_object"},  # ★JSON mode：模型层强制合法JSON
    )
    return json.loads(resp.choices[0].message.content)


# ---------- 节点B：按大纲调研（复用 Stage 2/4 的循环） ----------
def research(client, question: str, outline: dict, max_steps: int = 12):
    from common.tools import REAL_TOOLS, REAL_REGISTRY, dispatch
    tools_mod.REGISTRY.clear()
    tools_mod.REGISTRY.update(REAL_REGISTRY)

    messages = [
        {"role": "system",
         "content": SYSTEM_RESEARCH + "\n\n研究大纲：\n" + json.dumps(outline, ensure_ascii=False)},
        {"role": "user", "content": question},
    ]
    evidence = []  # 收集工具返回的关键证据，供评测judge核对"答案是否被证据支撑"

    for step in range(1, max_steps + 1):
        # ★ 收敛护栏（runtime的确定性职责）：剩最后2步时注入"死线"消息，
        # 强制模型收尾。教训：agent不会自己收敛——"永远还差一点"是默认行为，
        # 收敛必须由代码管理，不能指望模型自觉。
        if step == max_steps - 1:
            messages.append({"role": "user",
                             "content": "⚠️ 研究时间即将用尽。请立即：1) note_read 读取全部笔记；"
                                        "2) 直接输出带引用的最终报告。不要再发起新的搜索或抓取。"})
        msg, usage = call_llm(client, messages, tools=REAL_TOOLS)
        messages.append(msg)

        # 过程可视性：没有它，调优就是盲人摸象（Stage 2 的教训）
        names = [tc.function.name for tc in (msg.tool_calls or [])]
        print(f"    [第{step}步] {'点菜:' + ','.join(names) if names else '✍️ 输出最终报告'}"
              f" (输入{usage.prompt_tokens}tok)")

        if not msg.tool_calls:  # 模型给出最终报告
            return msg.content, evidence, messages, usage

        if usage.prompt_tokens > config.MAX_CONTEXT_TOKENS:
            messages, _ = compact_messages(client, messages)

        for tc in msg.tool_calls:
            args = json.loads(tc.function.arguments)
            result = dispatch(tc.function.name, args)
            messages.append({"role": "tool", "tool_call_id": tc.id, "content": result})
            if tc.function.name in ("web_search", "fetch_url"):
                evidence.append(result[:600])  # 留证给judge

    return "（步数耗尽，未完成）", evidence, messages, usage


def run(question: str, max_steps: int = 12) -> dict:
    """对外入口：评测脚本调用它。返回答案+证据+统计。"""
    client = make_client()
    tools_mod.NOTES_FILE = ROOT / "notes" / "agent_memory" / f"NOTES_{int(__import__('time').time())}.md"
    tools_mod.NOTES_FILE.parent.mkdir(parents=True, exist_ok=True)

    outline = plan(client, question)
    print(f"  大纲: {len(outline.get('sub_questions', []))} 个子问题: "
          f"{[s['q'] for s in outline.get('sub_questions', [])]}")
    answer, evidence, messages, usage = research(client, question, outline, max_steps)
    return {"question": question, "outline": outline, "answer": answer,
            "evidence": evidence, "usage": {"prompt": usage.prompt_tokens,
                                            "completion": usage.completion_tokens}}


if __name__ == "__main__":
    q = "DeepSeek API 目前的模型和定价是怎样的？"
    print(f"任务: {q}\n")
    r = run(q)
    print(f"\n{'='*60}\n📄 研究报告:\n{r['answer']}")
    print(f"\n[统计] 输入{r['usage']['prompt']} tok / 输出{r['usage']['completion']} tok / 证据{len(r['evidence'])}条")
