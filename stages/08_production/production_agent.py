"""
Stage 8 CLI 入口 —— Stage 11 事件化改造后，终端版 = ResearchAgent + CLI 注入。
逻辑全部在 common/agent_core.py；本文件只剩"注入 CLI 实现并启动"。
原 Stage 8 的四个验收实验（记忆写入/生效、HITL、优雅收尾）行为不变。

运行: .venv/bin/python stages/08_production/production_agent.py [问题]
环境: LOW_BUDGET=1 演示优雅收尾；MCP_FS=1 接入文件系统 MCP server
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from common.agent_core import ResearchAgent
import config


def main():
    question = " ".join(sys.argv[1:]) or "DeepSeek API 目前的模型和定价是怎样的？"
    agent = ResearchAgent(impl="08_production",
                      use_mcp=bool(config.MCP_SERVERS))  # 不注入 = 默认 CLI emit/approver
    result = agent.run(question)
    print(f"\n{'='*62}\n最终输出:\n{result['answer'][:800]}")


if __name__ == "__main__":
    main()
