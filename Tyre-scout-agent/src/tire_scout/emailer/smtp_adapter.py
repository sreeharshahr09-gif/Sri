"""SMTP adapter for an approved SMTP/OAuth arrangement. Credentials come only
from environment variables. Use only if the organization permits SMTP AUTH."""
from __future__ import annotations

import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formatdate
from pathlib import Path

from ..config import get_secret
from .base import SubmissionError
from .journal import OutboundMessage


class SmtpAdapter:
    name = "smtp"

    def __init__(self, cfg):
        self.cfg = cfg

    def submit(self, msg: OutboundMessage) -> str:
        c = self.cfg.email.smtp
        user = get_secret(c.username_env)
        password = get_secret(c.password_env)
        if not (c.host and user and password):
            raise SubmissionError("SMTP host/credentials not configured (env vars missing)")
        eml = EmailMessage()
        eml["Subject"] = msg.subject
        eml["From"] = msg.from_address
        eml["To"] = ", ".join(msg.to)
        if msg.cc:
            eml["Cc"] = ", ".join(msg.cc)
        eml["Date"] = formatdate(localtime=True)
        eml.set_content(msg.text)
        eml.add_alternative(msg.html, subtype="html")
        for att in msg.attachments:
            eml.add_attachment(Path(att).read_bytes(), maintype="application",
                               subtype=Path(att).suffix.lstrip("."), filename=Path(att).name)
        context = ssl.create_default_context()
        try:
            if c.use_ssl:
                with smtplib.SMTP_SSL(c.host, c.port, context=context, timeout=60) as s:
                    s.login(user, password)
                    refused = s.send_message(eml)
            else:
                with smtplib.SMTP(c.host, c.port, timeout=60) as s:
                    s.starttls(context=context)
                    s.login(user, password)
                    refused = s.send_message(eml)
        except smtplib.SMTPResponseException as exc:
            # 4xx/5xx before DATA end => clean failure; timeout during DATA is ambiguous
            raise SubmissionError(f"SMTP rejected: {exc.smtp_code}", ambiguous=False) from exc
        except (TimeoutError, ConnectionError, ssl.SSLError) as exc:
            raise SubmissionError(f"SMTP connection problem: {exc}", ambiguous=True) from exc
        if refused:
            raise SubmissionError(f"recipients refused: {list(refused)}")
        return f"smtp://{c.host}/{eml['Message-ID'] or 'submitted'}"
