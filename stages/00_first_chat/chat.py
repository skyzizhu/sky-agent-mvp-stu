"""
Stage 0：第一次对话 —— 理解 messages / role / 多轮对话的本质。

运行: python3 stages/00_first_chat/chat.py
学习目标:
  1. 看懂 Chat Completions API 的请求体：messages 是一个"历史数组"
  2. 理解多轮对话 = 每次把全部历史重发一遍（无状态的补全，不是有状态的聊天）
  3. token 计费方式：input tokens 随轮数线性增长
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # 让脚本能找到项目根

from common.llm_client import make_client, call_llm, save_trace


def main():
    client = make_client()

    # messages 数组是唯一的状态载体。role 有四种：system / user / assistant / tool
    messages = [
        {"role": "system", "content": "你是一个简洁的助手，回答不超过三句话。"},
    ]

    print("Stage 0 多轮对话（输入 q 退出）\n")
    turn = 0
    while True:
        user_input = input("\n你: ").strip()
        if user_input.lower() in ("q", "quit", "exit"):
            break
        turn += 1
        messages.append({"role": "user", "content": user_input})

        # 调用模型。注意：发过去的是【整个 messages 数组】
        msg, usage = call_llm(client, messages)
        messages.append({"role": "assistant", "content": msg.content})

        print(f"\n🤖: {msg.content}")
        # 关键观察点：input_tokens 是否比上一轮多出了上一轮的 input+output
        print(f"   [第{turn}轮] input={usage.prompt_tokens} tok, "
              f"output={usage.completion_tokens} tok")

        if turn == 1:
            print("\n---- 第 1 轮请求体的完整结构（学习用）----")
            print(json.dumps(messages[:2], ensure_ascii=False, indent=2))
            print("------------------------------------------\n")

    save_trace("stage0_chat", messages)


if __name__ == "__main__":
    main()
