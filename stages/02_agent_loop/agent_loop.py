"""
Stage 2：★ Agent Loop —— 整个学习的核心。

运行: python3 stages/02_agent_loop/agent_loop.py
学习目标:
  1. Agent = LLM + 工具 + 循环 + 停止条件，亲手写出来这个循环
  2. 两个停止条件：模型不再要求调工具（认为做完了）/ 达到最大轮数（防失控）
  3. ReAct：每一步 = 思考(content) → 行动(tool_calls) → 观察(tool 结果)
  4. 养成读 trace 的习惯：agent 调试 = 分析"模型的决策序列"
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # 让脚本能找到项目根

from common.llm_client import make_client, call_llm, save_trace, show_step
from common.tools import TOOLS, dispatch  # 复用 Stage 1 定义的工具

SYSTEM_PROMPT = (
    "你是一个严谨的研究助理。回答任何事实性问题前，先用工具获取事实，"
    "不要凭记忆编造。获取到足够信息后，给出简明结论并注明数据来自哪次工具调用。"
)


def agent_loop(client, user_question: str, max_steps: int = 20):
    """
    Agent Loop 主函数 —— 全项目的心脏。

    输入: 用户的自然语言问题
    输出: assistant 的最终回答（字符串）
    """
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_question},
    ]

    for step in range(1, max_steps + 1):
        # ---- 循环体第 1 步：把当前全部历史交给模型，问"下一步干什么" ----
        msg, usage = call_llm(client, messages, tools=TOOLS)
        messages.append(msg)

        # ---- 停止条件 A：模型不再要求调工具 => 它认为任务完成了 ----
        if not msg.tool_calls:
            show_step(step, msg)
            print(f"   [停止] 模型未请求工具 => 任务完成")
            print(f"   [本轮 token] input={usage.prompt_tokens}, output={usage.completion_tokens}")
            save_trace("stage2_agent_loop", messages,
                       extra={"total_steps": step, "stop_reason": "model_done"})
            return msg.content

        # ---- 循环体第 2 步：执行模型要求的每个工具（可能一次要多个）----
        results = []
        for tc in msg.tool_calls:
            args = json.loads(tc.function.arguments)
            result = dispatch(tc.function.name, args)
            results.append((tc, result))

        # ---- 循环体第 3 步：结果回填（每条 tool_calls 都要有对应 role=tool 消息）----
        for tc, result in results:
            messages.append({"role": "tool", "tool_call_id": tc.id, "content": result})

        show_step(step, msg, result=results[-1][1])

    # ---- 停止条件 B：轮数用尽，强制退出（防止无限循环烧钱）----
    print("   [停止] 达到最大轮数，强制退出")
    save_trace("stage2_agent_loop", messages,
               extra={"total_steps": max_steps, "stop_reason": "max_steps"})
    return "（达到最大步数未能完成任务）以下是目前的进展：" + (messages[-1]["content"] or "")


def main():
    client = make_client()
    question = "北京现在多少度？比 35 摄氏度高还是低？"
    print(f"任务: {question}\n")
    answer = agent_loop(client, question)
    print(f"\n{'='*60}\n✅ 最终回答:\n{answer}")

    # 进阶实验（自己改着玩，体会 loop 的涌现能力）:
    # 1. question = "北京、上海、广州今天哪个温度最高？差多少？"  -> 观察多轮多工具调用
    # 2. 把 max_steps 改成 2        -> 观察强制停止时会发生什么
    # 3. 把 SYSTEM_PROMPT 的"先用工具"删掉 -> 观察模型会不会偷懒直接编答案
    # 4. 在 REGISTRY 里加一个故意返回错误的工具 -> 观察模型如何自我纠错


if __name__ == "__main__":
    main()
