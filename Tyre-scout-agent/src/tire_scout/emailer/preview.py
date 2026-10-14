"""Preview adapter: never touches a mail provider. Writes the exact outgoing
message (EML + HTML) to a local outbox so layout and privacy can be reviewed."""
from __future__ import annotations

import hashlib
from email.message import EmailMessage
from email.utils import formatdate
from pathlib import Path

from .journal import OutboundMessage


class PreviewAdapter:
    name = "preview"

    def __init__(self, cfg):
        self.cfg = cfg
        self.outbox = Path(cfg.general.data_dir) / "outbox"
        self.outbox.mkdir(parents=True, exist_ok=True)

    def submit(self, msg: OutboundMessage) -> str:
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
            data = Path(att).read_bytes()
            eml.add_attachment(data, maintype="application",
                               subtype=Path(att).suffix.lstrip("."), filename=Path(att).name)
        digest = hashlib.sha256(eml.as_bytes()).hexdigest()[:16]
        path = self.outbox / f"{digest}.eml"
        path.write_bytes(eml.as_bytes())
        (self.outbox / f"{digest}.html").write_text(msg.html, encoding="utf-8")
        return f"preview://{digest}"
