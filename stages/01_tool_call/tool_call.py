"""
Stage 1：Function Calling 单步 —— 理解"工具只是结构化输出"。

运行（在项目根目录）: python3 stages/01_tool_call/tool_call.py
学习目标:
  1. 工具声明的 JSON Schema 怎么写（name/description/parameters）
  2. 模型不执行任何东西！它只返回 tool_calls（"点菜"），执行永远在你的代码里
  3. 工具结果如何以 role="tool" 消息回填，模型才能"看到"结果
  4. 本阶段刻意【不写循环】：手动走完一条完整链路
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # 让脚本能找到项目根

from common.llm_client import make_client, call_llm, save_trace, show_step
from common.tools import TOOLS, dispatch  # 工具声明与执行在 common/tools.py，有详细注释


def main():
    client = make_client()
    messages = [
        {"role": "system", "content": "你是一个助手，可使用工具。回答前先用工具获取事实。"},
        {"role": "user", "content": "北京现在多少度？比 35 摄氏度高还是低？"},
    ]

    # ===== 第 1 次请求：模型决定"点哪道菜" =====
    msg, _ = call_llm(client, messages, tools=TOOLS)
    show_step(1, msg)
    assert msg.tool_calls, "预期模型会要求调用工具；若没有，检查工具描述是否清晰"
    messages.append(msg)  # 注意：assistant 的 tool_calls 消息也必须进历史！

    # ===== 中间环节：你的代码执行工具 =====
    tc = msg.tool_calls[0]
    args = json.loads(tc.function.arguments)  # 模型给的参数是 JSON 字符串，要解析
    result = dispatch(tc.function.name, args)
    print(f"   [工具执行] {tc.function.name}({args}) -> {result}")

    # ===== 节点：结果回填。tool_call_id 把结果和调用一一对应 =====
    messages.append({"role": "tool", "tool_call_id": tc.id, "content": result})

    # ===== 第 2 次请求：模型看到结果，生成最终回答 =====
    msg, _ = call_llm(client, messages, tools=TOOLS)
    show_step(2, msg)

    save_trace("stage1_tool_call", messages)
    print(f"\n最终回答:\n{msg.content}")
    print("\n思考题：如果任务需要'查天气→算差值→再回答'三次往返呢？")
    print("       手动串 N 次请求不可扩展 —— 这就是 Stage 2 要写循环的原因。")


if __name__ == "__main__":
    main()
