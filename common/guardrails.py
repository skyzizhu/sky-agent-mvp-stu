"""
Stage 8 节点18/19：人工确认（HITL）+ 护栏。

三件套：
1. Budget：token 预算记账，agent loop 每轮汇报消耗，超支触发"优雅收尾"
   ——不是硬崩，而是注入死线消息让模型总结已有发现、交代未完成项
2. 危险分级：每个工具标 readonly / sensitive；sensitive 级执行前必须人工 y/n
   （判断标准见 AGENT_NODES：不可逆/对外发送/花钱/权限/低置信）
3. graceful_finish：强停时的标准动作=汇报已有进展+列出未完成项+说明如何续跑
"""
import json
import time

import config


# ---------- 工具危险分级注册表 ----------
DANGEROUS_TOOLS = {
    # "工具名": "危险原因（展示给用户看，辅助批准决策）"
    "send_report": "对外发送：会把内容发给外部邮箱，发出后无法撤回",
}


class Budget:
    """token 预算。用真实计费值（usage）记账。
    max_total_tokens=None 表示不限额度：水位永不触发，
    但步数上限/死线注入/熔断器等控制仍然生效（不限 ≠ 失控）。"""

    def __init__(self, max_total_tokens: int | None):
        self.max = max_total_tokens   # None = 不限额
        self.used = 0

    def add(self, usage) -> int:
        self.used += usage.total_tokens
        return self.used

    @property
    def exhausted(self) -> bool:
        return self.max is not None and self.used >= self.max

    @property
    def remaining(self) -> int | None:
        if self.max is None:
            return None
        return max(0, self.max - self.used)

    def near_limit(self, ratio: float = 0.8) -> bool:
        return self.max is not None and self.used >= self.max * ratio


def approval_required(tool_name: str) -> bool:
    return tool_name in DANGEROUS_TOOLS


def ask_human(tool_name: str, args_json: str) -> tuple[bool, str]:
    """
    HITL 的最小实现：终端 y/n。
    生产中这里换成审批 IM 消息/工单系统，但"暂停-询问-回填结果"的骨架不变。
    返回 (是否批准, 给模型看的回执文本)。
    """
    reason = DANGEROUS_TOOLS.get(tool_name, "")
    print(f"\n{'!'*62}")
    print(f"⚠️  人工确认 | 危险类型: {reason}")
    print(f"    工具: {tool_name}")
    print(f"    参数: {args_json[:300]}")
    ans = input("    批准执行? (y=批准 / n=拒绝): ").strip().lower()
    approved = ans == "y"
    receipt = ("用户已批准执行。" if approved else
               "用户【拒绝】了此操作。请尊重该决定：不要重复尝试同一动作，"
               "改为直接在回答中输出报告内容，并告知用户可自行转发。")
    print(f"    → {'✅ 已批准' if approved else '❌ 已拒绝，回执已喂给模型'}\n{'!'*62}\n")
    return approved, receipt


# ---------- 优雅收尾 ----------
WRAP_UP_MSG = ("⚠️ 预算即将用尽。请立即停止调研，执行收尾：\n"
               "1) note_read 读取全部笔记；\n"
               "2) 输出《阶段性结题报告》：已确认的发现（带来源）+ 未完成项清单"
               " + 建议的续跑方式。不要再发起新的搜索。")
