"""
邮件发送服务（独立于 Agent 框架）。

职责边界：
  - 只做一件事：把一封邮件发出去（SMTP/SSL/STARTTLS、UTF-8 头、失败诊断）。
  - 不关心内容怎么来（那是调用方的事），不 import 任何 Agent 代码。
  - .env 三项（SMTP_HOST/USER/AUTH_CODE）配齐才真实发送；
    缺任何一项降级为模拟发送（sent(mock)），演示与生产同一份代码。

用法：
    from services.mail_service import MailService
    MailService().send(to="a@b.com", subject="报告", body="...")
"""
import json
import smtplib
import ssl
from email.header import Header
from email.mime.text import MIMEText
from email.utils import formataddr

import config


class MailService:
    """SMTP 邮件发送器。host/user/auth_code 缺省时读 config（.env）。"""

    def __init__(self, host: str = "", port: int | None = None,
                 user: str = "", auth_code: str = ""):
        self.host = host or config.SMTP_HOST
        self.port = port or config.SMTP_PORT
        self.user = user or config.SMTP_USER
        self.auth_code = auth_code or config.SMTP_AUTH_CODE

    @property
    def configured(self) -> bool:
        """三项（host/user/auth_code）齐备才允许真实发送。"""
        return bool(self.host and self.user and self.auth_code)

    def send(self, to: str, subject: str, body: str) -> dict:
        """发送一封文本邮件，返回结果 dict（永不抛异常，错误转 status=error）。"""
        if not (to and subject and body and body.strip()):
            return {"status": "error",
                    "error": "收件人/主题/正文存在空值，拒绝发送"}
        if not self.configured:
            return {"status": "sent(mock)",
                    "note": "未配置 SMTP（.env 的 SMTP_HOST/SMTP_USER/SMTP_AUTH_CODE），本次为模拟发送",
                    "recipient": to, "subject": subject, "content_chars": len(body)}
        try:
            msg = MIMEText(body, "plain", "utf-8")
            msg["Subject"] = Header(subject, "utf-8")
            msg["From"] = formataddr((str(Header("研究 Agent", "utf-8")), self.user))
            msg["To"] = to
            if int(self.port) == 465:   # 隐式 SSL（163/126/yeah 推荐）
                server = smtplib.SMTP_SSL(self.host, self.port,
                                          context=ssl.create_default_context(), timeout=30)
            else:                       # 587/25 走 STARTTLS 明文升级
                server = smtplib.SMTP(self.host, self.port, timeout=30)
                server.starttls(context=ssl.create_default_context())
            with server:
                server.login(self.user, self.auth_code)
                server.sendmail(self.user, [to], msg.as_string())
            return {"status": "sent", "recipient": to, "subject": subject,
                    "content_chars": len(body)}
        except Exception as e:
            return {"status": "error", "recipient": to, "subject": subject,
                    "error": f"{type(e).__name__}: {str(e)[:200]}"}

    def to_json(self, result: dict) -> str:
        return json.dumps(result, ensure_ascii=False)
