"""
Stage 4 核心节点：上下文压缩（Compaction）。

问题（你在 Stage 3 日志里亲眼见过）：
  每次调用都重发全部历史，工具结果又特别胖。
  深度调研十几步后，上下文会膨胀到几十万 token：
  - 钱：每圈都为全部历史付全价
  - 质量：context rot——上下文越长，模型对早期内容的召回越差

解法（Anthropic《Effective Context Engineering》）：
  历史超过阈值时，把"旧历史"压缩成一段摘要，只保留"最近几条"原文，
  用 [system + 摘要 + 最近消息] 重建一个短上下文继续干活。
  摘要要保住四类信息：任务目标 / 关键发现（带来源）/ 未完成事项 / 约束条件。
"""
import json

import config
from common.llm_client import call_llm


def render_messages_for_summary(messages: list) -> str:
    """把历史消息渲染成纯文本，交给摘要模型读。
    工具结果只保留前500字符——摘要模型不需要全文，只需要'发现了什么'。"""
    lines = []
    for m in messages:
        role = m.get("role")
        if role == "user":
            lines.append(f"[用户] {m['content']}")
        elif role == "assistant":
            if m.get("tool_calls"):
                for tc in m["tool_calls"]:
                    args = tc["function"]["arguments"]
                    lines.append(f"[assistant决策] 调用 {tc['function']['name']}({args[:200]})")
            if m.get("content"):
                lines.append(f"[assistant输出] {m['content'][:800]}")
        elif role == "tool":
            lines.append(f"[工具结果] {m['content'][:500]}")
    return "\n".join(lines)


def _safe_split(messages: list, keep_recent: int) -> int:
    """找切分点：recent 部分不能以 tool 消息开头。
    原因：tool 消息必须紧跟在带 tool_calls 的 assistant 消息后面，
    若把它的'上级'切掉了，这条 tool 消息就成了孤儿，API 直接报 400。"""
    split = max(0, len(messages) - keep_recent)
    while split < len(messages) and messages[split].get("role") == "tool":
        split += 1
    return split


def _as_dict(m):
    """历史里的 assistant 消息可能是 pydantic 对象（loop 里直接 append 的返回值），
    统一转成 dict，后续 .get() 才安全。"""
    if hasattr(m, "model_dump"):
        return m.model_dump()
    return m


def compact_messages(client, messages: list,
                     keep_recent: int = None) -> tuple[list, str, dict]:
    """
    压缩历史。输入完整 messages，输出 (新messages, 摘要文本, 统计stats)。

    结构: [system] + [user: 历史摘要] + [最近N条原文]
    stats 供 GUI 下钻展示：压缩了什么、怎么压的、效果如何。
    """
    messages = [_as_dict(m) for m in messages]  # 先全部规范化为 dict
    keep_recent = keep_recent or config.COMPACT_KEEP_RECENT
    system = messages[0] if messages[0].get("role") == "system" else None
    body = messages[1:] if system else messages

    split = _safe_split(body, keep_recent)
    old, recent = body[:split], body[split:]

    summary_prompt = [
        {"role": "system",
         "content": "你是研究档案员。把一份 agent 研究过程的对话历史压缩成摘要，"
                    "必须保留：1.原始任务目标；2.全部关键发现（含数据和来源域名）；"
                    "3.尚未完成的事项；4.任何约束或注意点。"
                    "用要点列出，总长不超过800字。无关的中间过程一律丢弃。"},
        {"role": "user",
         "content": render_messages_for_summary(old)},
    ]
    msg, usage = call_llm(client, summary_prompt)
    summary = msg.content or ""

    # ★全量 IO：与主循环 step 节点同构——压缩调用的完整请求体/响应对象/token 明细
    resp_json = msg.model_dump() if hasattr(msg, "model_dump") else {"content": str(msg)}
    usage_detail = {
        "prompt": getattr(usage, "prompt_tokens", None),
        "completion": getattr(usage, "completion_tokens", None),
        "cached": getattr(getattr(usage, "prompt_tokens_details", None), "cached_tokens", None),
        "reasoning": getattr(getattr(usage, "completion_tokens_details", None), "reasoning_tokens", None),
    }

    new_messages = []
    if system:
        new_messages.append(system)
    # 只注入摘要 user 消息，直接衔接 recent 原文。
    # 教训：不要人造 assistant 确认消息——DeepSeek 思考模式要求每条 assistant
    # 消息都带 reasoning_content，人造消息没有它会被 API 400 拒收。
    new_messages.append({"role": "user",
                         "content": f"[历史压缩摘要——此前研究进展]\n{summary}\n\n请基于以上进展继续研究。"})
    stats = {"old_messages": len(old), "kept_recent": len(recent),
             "summary_chars": len(summary),
             "total_before": len(messages), "total_after": len(new_messages),
             "method": "LLM摘要(保留目标/发现/待办/约束) + 最近原文保留",
             "purpose": "对抗context rot与token成本：旧历史有损压缩，事实由笔记兜底",
             # ★ 全量 IO：交给摘要模型的完整请求体（messages，无 tools）+ 完整响应对象
             "request_json": {"model": config.MODEL, "messages": summary_prompt},
             "response_json": resp_json,
             "usage_detail": usage_detail,
             # 文本版（兼容旧前端/快速阅读）
             "summarizer_input": render_messages_for_summary(old),
             "summarizer_output": summary,
             "usage": usage,
             "tokens": usage.total_tokens if usage else 0}
    new_messages.extend(recent)
    return new_messages, summary, stats



def maybe_compact(client, messages: list, prompt_tokens: int) -> tuple[list, str | None, dict | None]:
    """agent loop 每轮调用后检查：输入token超阈值就压缩，没超就原样返回。"""
    if prompt_tokens > config.MAX_CONTEXT_TOKENS:
        return compact_messages(client, messages)
    return messages, None, None


# ---------- 单页即时萃取（Map 阶段） ----------
PAGE_EXTRACT_THRESHOLD = 800  # 正文超过 800 字符触发即时萃取


def extract_page_facts(client, raw_text: str, goal: str = "") -> tuple[str, any]:
    """
    单页即时萃取（Map-Reduce 范式之 Map 阶段）：
    将 2000~4000 字的长网页提炼为 250~450 字的高纯度事实/数据要点，
    大幅降低长文倾倒对主 Agent 上下文的冲击。
    返回: (extracted_text, usage)
    """
    if not raw_text or len(raw_text) <= PAGE_EXTRACT_THRESHOLD or client is None:
        return raw_text, None

    goal_prompt = f"【当前调研课题】：{goal}\n\n" if goal else ""
    prompt = [
        {"role": "system",
         "content": "你是一个严谨的研究速读助理。你的任务是从网页提取高信噪比的核心要点。\n"
                    "【提取硬性规则】：\n"
                    "1. 提取所有关键事实、具体数据（价格、数字、限额、时间、规格、百分比）；\n"
                    "2. 提取与调研目标相关的核心结论、业务模式与产品功能，彻底剔除免责声明、导航栏残留、版权说明及无关客套话；\n"
                    "3. 严格忠于原文，严禁任何脑补或主观推论；证据中不确定的如实保留；\n"
                    "4. 输出为 Markdown 清晰要点列表（≤400字）。"},
        {"role": "user",
         "content": f"{goal_prompt}【网页正文原文】：\n{raw_text[:6000]}"}
    ]
    try:
        msg, usage = call_llm(client, prompt)
        extracted = (msg.content or "").strip()
        if len(extracted) >= 20:
            return extracted, usage
    except Exception:
        pass
    # 异常或提取过短时降级返回原文
    return raw_text, None
