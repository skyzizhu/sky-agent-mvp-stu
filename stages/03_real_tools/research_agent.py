"""
Stage 3：真实工具 —— agent 从玩具变成真正的研究助理。

运行: .venv/bin/python stages/03_real_tools/research_agent.py
学习目标:
  1. mock 换真工具：循环一行不用改 —— 这验证了"工具只是循环里的可替换零件"
  2. 工具结果必须裁剪：一次网页抓取可能几万字，不截断一次就撑爆上下文
  3. system prompt 的作用：给 agent 立"行为准则"（先搜后答、标注来源、多角度验证）
  4. 观察 token 消耗对比 Stage 2：真实工具的结果更胖，成本感受更直观
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from common.llm_client import make_client, call_llm, save_trace, show_step
from common.tools import REAL_TOOLS, REAL_REGISTRY, dispatch, REGISTRY

SYSTEM_PROMPT = (
    "你是一个严谨的研究助理。规则：\n"
    "1. 回答事实性问题前必须先用 web_search 搜索，禁止凭记忆编造；\n"
    "2. 搜索摘要足够就直接回答；不够再用 fetch_url 打开具体网页；\n"
    "3. 结论中注明信息来源（域名）；信息有冲突时如实指出；\n"
    "4. 用简体中文回答，控制在300字以内。"
)


def research_agent(client, question: str, max_steps: int = 15):
    """和 Stage 2 的循环一模一样，只是换了工具集 —— 循环本体零修改。"""
    REGISTRY.clear()
    REGISTRY.update(REAL_REGISTRY)  # 本阶段只暴露真实工具

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": question},
    ]
    total_tokens = 0

    for step in range(1, max_steps + 1):
        msg, usage = call_llm(client, messages, tools=REAL_TOOLS)
        total_tokens += usage.total_tokens
        messages.append(msg)

        if not msg.tool_calls:  # 停止条件A：模型认为研究完成
            show_step(step, msg)
            print(f"   [停止] 任务完成 | 总token: {total_tokens}")
            save_trace("stage3_research", messages, tools=REAL_TOOLS,
                       extra={"total_steps": step, "total_tokens": total_tokens})
            return msg.content

        results = []
        for tc in msg.tool_calls:
            args = json.loads(tc.function.arguments)
            results.append((tc, dispatch(tc.function.name, args)))
        for tc, result in results:
            messages.append({"role": "tool", "tool_call_id": tc.id, "content": result})

        show_step(step, msg, result=results[-1][1])
        print(f"   [累计token: {total_tokens}]")

    save_trace("stage3_research", messages, tools=REAL_TOOLS,
               extra={"total_steps": max_steps, "stop_reason": "max_steps"})
    return "（达到最大步数，研究未完成）"


def main():
    client = make_client()
    question = "DeepSeek 最新的模型是什么？有什么特点？"
    print(f"任务: {question}\n")
    answer = research_agent(client, question)
    print(f"\n{'='*60}\n✅ 研究结论:\n{answer}")


if __name__ == "__main__":
    main()
