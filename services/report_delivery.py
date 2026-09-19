"""
报告投递服务（独立于 Agent 框架）。

职责：报告定稿之后的"统一投递流程"——
  检测用户输入中的发送意图 → 按用户想要的内容动态组装邮件正文（LLM）
  → 推送确认卡片（带真实内容预览）→ 等待用户决定 → MailService 发送。

与内核的边界：本模块不 import agent_core——
  只接收 final 文本、笔记文本与 emit/wait_decision 两个注入函数，
  Agent 内核既不感知邮件，也不被投递流程影响。
"""
import re

from services.mail_service import MailService

# 用户输入同时出现"邮箱地址"+"发送动词"才算发送意图（缺一不触发）
EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.\-]+")
SEND_KEYWORDS = ("发送", "发到", "发给我", "发邮件", "邮件发", "发邮箱", "寄到", "发我邮箱")

CONFIRM_TIMEOUT = 600   # 确认卡片等待上限（秒），超时视为取消


def detect_send_intent(question: str) -> dict | None:
    """返回 {'recipient': 邮箱} 或 None（无发送意图）。"""
    emails = EMAIL_RE.findall(question or "")
    if not emails:
        return None
    if not any(k in question for k in SEND_KEYWORDS):
        return None
    return {"recipient": emails[0]}


def compose_body(question: str, final_report: str, notes_text: str) -> str:
    """按用户对'发送什么内容'的要求，从素材中动态组装邮件正文。

    用户要最终报告 → 完整定稿；要某阶段/节点/第一步 → 从笔记抽取对应部分；
    要摘要/结论 → 精炼；没细说 → 完整定稿。LLM 不可用时兜底发完整报告。
    """
    try:
        from common.llm_client import make_client, call_llm
        client = make_client()
        prompt = (
            f"用户的研究请求：{question}\n\n"
            "研究已完成。可用素材有两份：\n\n"
            "【最终报告（定稿）】\n"
            f"{(final_report or '')[:8000]}\n\n"
            "【调研笔记（全过程发现，按子问题组织）】\n"
            f"{(notes_text or '')[:6000]}\n\n"
            "用户还要求把内容发送到邮箱。请严格按用户对\"发送什么内容\"的要求，"
            "组织邮件正文：\n"
            "- 用户要最终报告 → 完整输出定稿报告（保留引用链接）；\n"
            "- 用户要某个阶段/节点/第一步的内容 → 从调研笔记中抽取对应部分；\n"
            "- 用户要摘要/结论 → 精炼输出；\n"
            "- 用户没有明确说要什么 → 输出完整定稿报告。\n"
            "只输出邮件正文本身，不要任何解释或前后缀。"
        )
        msg, _ = call_llm(client, [{"role": "user", "content": prompt}])
        return (msg.content or "").strip() or (final_report or "")
    except Exception:
        return final_report or ""


def post_run_delivery(emit, question: str, final_report: str,
                      notes_text: str, wait_decision, timeout: int = CONFIRM_TIMEOUT):
    """统一投递流程：在宿主的运行线程里执行（阻塞等用户决定，不占 Agent 循环）。

    emit          : 事件推送函数（webapp 注入 agent.emit，实时 + 落盘 + 回放同源）
    wait_decision : 阻塞等待用户决定的函数（webapp 注入），返回 bool
    """
    intent = detect_send_intent(question)
    if not intent:
        return   # 无发送意图：静默结束，不多话

    recipient = intent["recipient"]
    body = compose_body(question, final_report, notes_text)
    if not body.strip():
        emit("send_result", status="error", recipient=recipient,
             error="邮件正文为空：研究未产出可发送内容")
        return
    subject = f"研究报告：{(question or '')[:50]}"

    # 统一确认：带真实内容预览，用户点了才发
    emit("send_request", recipient=recipient, subject=subject,
         body_chars=len(body), preview=body[:600])
    approved = wait_decision(timeout)

    if approved:
        result = MailService().send(to=recipient, subject=subject, body=body)
    else:
        result = {"status": "cancelled",
                  "note": "用户取消发送（或确认超时），报告仅在界面展示"}
    emit("send_result", **result)
