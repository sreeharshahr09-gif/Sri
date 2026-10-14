"""Outbound message model, recipient validation, and the send journal.

Delivery guarantees: we do NOT claim exactly-once email. The journal records
preparing -> (submitted | failed_before_submit | uncertain) -> confirmed_delivered.
A crash after possible submission leaves the record `uncertain` for
reconciliation instead of a blind resend.
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel, Field

NO_RESEND_STATES = {"submitted", "confirmed_delivered", "uncertain"}


class OutboundMessage(BaseModel):
    subject: str
    from_address: str
    to: list[str]
    cc: list[str] = Field(default_factory=list)
    html: str
    text: str
    attachments: list[Path] = Field(default_factory=list)


class SendDecision(BaseModel):
    allowed: bool
    reason: str
    identity: str


def send_identity(digest_type: str, period: str, sender: str) -> str:
    """Stable identity: content re-renders, model changes, recipient edits,
    schedule changes and retries must not resubmit an already-sent edition."""
    raw = f"{digest_type}|{period}|{sender.lower()}"
    return hashlib.sha256(raw.encode()).hexdigest()


def validate_recipients(cfg, to: list[str], cc: list[str]) -> None:
    """Allowlist check in trusted code. The LLM can never change this."""
    allowed = {d.lower() for d in cfg.recipients.allowed_domains}
    if not allowed:
        raise ValueError("recipient allowlist is empty - refusing to validate")
    for addr in [*to, *cc]:
        domain = addr.rsplit("@", 1)[-1].lower()
        if domain not in allowed:
            raise ValueError(f"recipient {addr!r} outside allowed domains {sorted(allowed)}")
    from_domain = cfg.email.from_address.rsplit("@", 1)[-1].lower()
    if from_domain not in allowed:
        raise ValueError(f"from address {cfg.email.from_address!r} outside allowed domains")


def validate_message(cfg, msg: OutboundMessage) -> None:
    validate_recipients(cfg, msg.to, msg.cc)
    if msg.from_address.lower() != cfg.email.from_address.lower():
        raise ValueError("From address does not match the approved work account")
    max_bytes = cfg.email.max_attachment_mb * 1024 * 1024
    for att in msg.attachments:
        p = Path(att)
        if not p.exists():
            raise ValueError(f"attachment missing: {p}")
        if p.suffix.lower() not in (".pdf", ".html", ".json"):
            raise ValueError(f"attachment type not permitted: {p.suffix}")
        if p.stat().st_size > max_bytes:
            raise ValueError(f"attachment too large: {p.name}")


# --------------------------------------------------------------------------- #
# Journal operations (transactional, in the application DB)
# --------------------------------------------------------------------------- #

def prepare_send(db, *, digest_type: str, period: str, cfg, msg: OutboundMessage,
                 state: str = "preparing") -> SendDecision:
    """Insert-or-check. Returns whether a new submission may proceed."""
    import json
    identity = send_identity(digest_type, period, msg.from_address)
    row = db._conn.execute("SELECT * FROM send_journal WHERE identity=?", (identity,)).fetchone()
    if row:
        if row["state"] in NO_RESEND_STATES:
            return SendDecision(allowed=False,
                                reason=f"already {row['state']} (provider_id={row['provider_id']})",
                                identity=identity)
        # retry is fine after clean failures / preview / blocked
        with db.tx() as c:
            c.execute(
                "UPDATE send_journal SET state=?, subject=?, recipients=?, attachments=?, last_error=NULL WHERE identity=?",
                (state, msg.subject, json.dumps({"to": msg.to, "cc": msg.cc}),
                 json.dumps([str(a) for a in msg.attachments]), identity))
        return SendDecision(allowed=True, reason="retry after non-submitted state", identity=identity)
    with db.tx() as c:
        c.execute(
            """INSERT INTO send_journal(identity, digest_type, period, sender, recipients, subject,
               state, attachments, created_at) VALUES (?,?,?,?,?,?,?,?,?)""",
            (identity, digest_type, period, msg.from_address,
             json.dumps({"to": msg.to, "cc": msg.cc}), msg.subject, state,
             json.dumps([str(a) for a in msg.attachments]),
             datetime.now(timezone.utc).isoformat()))
    return SendDecision(allowed=True, reason="new send", identity=identity)


def mark(db, identity: str, state: str, *, provider: str | None = None,
         provider_id: str | None = None, error: str | None = None) -> None:
    ts = datetime.now(timezone.utc).isoformat()
    with db.tx() as c:
        col = {"submitted": "submitted_at", "confirmed_delivered": "confirmed_at"}.get(state)
        extra = f", {col}=?" if col else ""
        params: list = [state, provider, provider_id, error]
        if col:
            params.append(ts)
        c.execute(
            f"UPDATE send_journal SET state=?, provider=COALESCE(?, provider), "
            f"provider_id=COALESCE(?, provider_id), last_error=?, attempts=attempts+1{extra} "
            f"WHERE identity=?", (*params, identity))


def journal_status(db, limit: int = 50) -> list[dict]:
    rows = db._conn.execute(
        "SELECT * FROM send_journal ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
    return [dict(r) for r in rows]


def uncertain_sends(db) -> list[dict]:
    rows = db._conn.execute(
        "SELECT * FROM send_journal WHERE state='uncertain' ORDER BY created_at DESC").fetchall()
    return [dict(r) for r in rows]
