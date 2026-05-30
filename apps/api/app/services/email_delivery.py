from __future__ import annotations

import json
import os
import smtplib
from dataclasses import dataclass
from email.message import EmailMessage
from pathlib import Path

from app.services.config import DATA_DIR


class EmailDeliveryUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class EmailDeliveryStatus:
    configured: bool
    mode: str
    sender: str | None = None


def _truthy(value: str | None, *, default: bool = True) -> bool:
    if value is None:
        return default
    return value.strip().lower() not in {"0", "false", "no", "off"}


def _public_origin() -> str:
    return (os.getenv("OPENCLASS_PUBLIC_ORIGIN") or os.getenv("OPENCLASS_WEB_ORIGIN") or "").strip()


def _delivery_mode() -> str:
    explicit = (os.getenv("OPENCLASS_EMAIL_DELIVERY") or "").strip().lower()
    if explicit:
        return explicit
    host = (os.getenv("OPENCLASS_SMTP_HOST") or "").strip()
    sender = (os.getenv("OPENCLASS_SMTP_FROM") or "").strip()
    if host and sender:
        return "smtp"
    origin = _public_origin()
    if not origin or "localhost" in origin or "127.0.0.1" in origin:
        return "log"
    return "unconfigured"


def delivery_status() -> EmailDeliveryStatus:
    mode = _delivery_mode()
    sender = (os.getenv("OPENCLASS_SMTP_FROM") or "").strip() or None
    return EmailDeliveryStatus(configured=mode in {"smtp", "log"}, mode=mode, sender=sender)


def send_transactional_email(*, to_email: str, subject: str, text_body: str) -> None:
    mode = _delivery_mode()
    if mode == "log":
        _write_log_email(to_email=to_email, subject=subject, text_body=text_body)
        return
    if mode != "smtp":
        raise EmailDeliveryUnavailable("邮件服务尚未配置，请先配置 SMTP 环境变量")

    host = (os.getenv("OPENCLASS_SMTP_HOST") or "").strip()
    sender = (os.getenv("OPENCLASS_SMTP_FROM") or "").strip()
    if not host or not sender:
        raise EmailDeliveryUnavailable("邮件服务尚未配置，请先配置 SMTP 主机和发件人")

    port = int((os.getenv("OPENCLASS_SMTP_PORT") or "587").strip())
    username = (os.getenv("OPENCLASS_SMTP_USERNAME") or "").strip()
    password = os.getenv("OPENCLASS_SMTP_PASSWORD") or ""
    use_starttls = _truthy(os.getenv("OPENCLASS_SMTP_STARTTLS"), default=True)

    message = EmailMessage()
    message["From"] = sender
    message["To"] = to_email
    message["Subject"] = subject
    message.set_content(text_body)

    with smtplib.SMTP(host, port, timeout=12) as smtp:
        if use_starttls:
            smtp.starttls()
        if username:
            smtp.login(username, password)
        smtp.send_message(message)


def _write_log_email(*, to_email: str, subject: str, text_body: str) -> None:
    log_dir = Path(DATA_DIR) / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    path = log_dir / "email-delivery.jsonl"
    record = {
        "to": to_email,
        "subject": subject,
        "body": text_body,
    }
    with path.open("a", encoding="utf-8") as file:
        file.write(json.dumps(record, ensure_ascii=False) + "\n")
