"""
报告投递服务（独立于 Agent 框架）。

职责：报告定稿之后的"统一投递流程"——
  Agent 循环中模型通过 request_send 工具**自主判断并登记**投递意图
  （要不要发、发什么、发给谁，是模型的判断）→ 报告定稿后本服务
  按 content_hint 动态组装邮件正文（LLM）→ 推送确认卡（真实内容预览）
  → 等待用户决定 → MailService 发送。

与内核的边界：本模块不 import agent_core——
  只接收定稿文本、笔记文本、登记信息与 emit/wait_decision 两个注入函数。
"""
from services.mail_service import MailService

CONFIRM_TIMEOUT = 600   # 确认卡片等待上限（秒），超时视为取消


def compose_body(question: str, final_report: str, notes_text: str,
                 content_hint: str = "", prior_report: str = "") -> str:
    """按模型的 content_hint（源自用户措辞）从素材中动态组装邮件正文。

    要完整最终报告 → 原样输出定稿；要某阶段/节点/部分 → 抽取对应素材；
    要摘要/结论 → 精炼。素材含本次定稿、会话历史报告（更早轮次定稿）、调研笔记。
    LLM 不可用时兜底发本次定稿。
    """
    try:
        from common.llm_client import make_client, call_llm
        client = make_client()
        sources = f"【最终报告（本次定稿）】\n{(final_report or '')[:20000]}\n\n"
        if (prior_report or '').strip():
            sources += f"【会话历史报告（更早轮次的定稿）】\n{prior_report[:20000]}\n\n"
        sources += f"【调研笔记（全过程发现）】\n{(notes_text or '')[:8000]}"
        want = (content_hint or "").strip() or "最终报告全文"
        prompt = (
            f"用户的研究请求：{question}\n"
            f"用户要求发送的内容：{want}\n\n"
            f"可用素材：\n\n{sources}\n\n"
            "任务：严格按\"用户要求发送的内容\"从素材中组织邮件正文：\n"
            "- 要完整最终报告 → 原样输出定稿全文（保留引用链接，不要改写删减）；\n"
            "- 要某个阶段/节点/部分 → 从对应素材中抽取组织；\n"
            "- 要摘要/结论 → 精炼输出。\n"
            "只输出邮件正文本身，不要任何解释或前后缀。"
        )
        msg, _ = call_llm(client, [{"role": "user", "content": prompt}])
        return (msg.content or "").strip() or (final_report or "")
    except Exception:
        return final_report or ""


def post_run_delivery(emit, question: str, final_report: str,
                      notes_text: str, wait_decision,
                      pending: dict | None = None, prior_report: str = "",
                      timeout: int = CONFIRM_TIMEOUT):
    """统一投递流程：在宿主的运行线程里执行（阻塞等用户决定，不占 Agent 循环）。

    emit          : 事件推送函数（webapp 注入 agent.emit，实时 + 落盘 + 回放同源）
    wait_decision : 阻塞等待用户决定的函数（webapp 注入），返回 bool
    pending       : 模型在循环中经 request_send 登记的投递意图
                    {recipient, content_hint, subject}；未登记 = 用户没要求发送，静默结束
    prior_report  : 会话中更早轮次的定稿报告（支持"把那份报告发我"跨轮取材）
    """
    if not pending:
        return   # 模型未登记任何投递意图：用户没要求发送，静默结束

    recipient = (pending.get("recipient") or "").strip()
    if not recipient:
        emit("send_result", status="error",
             error="投递登记缺少收件人邮箱，无法发送")
        return
    content_hint = (pending.get("content_hint") or "").strip()
    subject = (pending.get("subject") or "").strip() or f"研究报告：{(question or '')[:50]}"

    body = compose_body(question, final_report, notes_text,
                        content_hint=content_hint, prior_report=prior_report)
    if not body.strip():
        emit("send_result", status="error", recipient=recipient,
             error="邮件正文为空：研究未产出可发送内容")
        return

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
