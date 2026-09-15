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
                     keep_recent: int = None) -> tuple[list, str]:
    """
    压缩历史。输入完整 messages，输出 (新messages, 摘要文本)。

    结构: [system] + [user: 历史摘要] + [assistant: 确认] + [最近N条原文]
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
    resp = client.chat.completions.create(model=config.MODEL,
                                          messages=summary_prompt)
    summary = resp.choices[0].message.content

    new_messages = []
    if system:
        new_messages.append(system)
    # 只注入摘要 user 消息，直接衔接 recent 原文。
    # 教训：不要人造 assistant 确认消息——DeepSeek 思考模式要求每条 assistant
    # 消息都带 reasoning_content，人造消息没有它会被 API 400 拒收。
    new_messages.append({"role": "user",
                         "content": f"[历史压缩摘要——此前研究进展]\n{summary}\n\n请基于以上进展继续研究。"})
    new_messages.extend(recent)
    return new_messages, summary


def maybe_compact(client, messages: list, prompt_tokens: int) -> tuple[list, str | None]:
    """agent loop 每轮调用后检查：输入token超阈值就压缩，没超就原样返回。"""
    if prompt_tokens > config.MAX_CONTEXT_TOKENS:
        return compact_messages(client, messages)
    return messages, None
