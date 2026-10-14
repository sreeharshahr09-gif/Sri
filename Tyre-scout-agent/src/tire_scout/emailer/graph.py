"""Microsoft Graph adapter (client credentials). Only usable if the tenant
grants application Mail.Send consent - verify with IT before configuring."""
from __future__ import annotations

import base64
import json
from pathlib import Path

import httpx

from ..config import get_secret
from .base import SubmissionError
from .journal import OutboundMessage


class GraphAdapter:
    name = "graph"

    def __init__(self, cfg):
        self.cfg = cfg

    def _token(self) -> str:
        g = self.cfg.email.graph
        secret = get_secret(g.client_secret_env)
        if not (g.tenant_id and g.client_id and secret):
            raise SubmissionError("Graph tenant/client/secret not configured")
        resp = httpx.post(
            f"https://login.microsoftonline.com/{g.tenant_id}/oauth2/v2.0/token",
            data={"client_id": g.client_id, "client_secret": secret,
                  "scope": "https://graph.microsoft.com/.default", "grant_type": "client_credentials"},
            timeout=30)
        if resp.status_code != 200:
            raise SubmissionError(f"Graph token request failed: HTTP {resp.status_code}")
        return resp.json()["access_token"]

    def submit(self, msg: OutboundMessage) -> str:
        token = self._token()
        body = {
            "message": {
                "subject": msg.subject,
                "body": {"contentType": "HTML", "content": msg.html},
                "toRecipients": [{"emailAddress": {"address": a}} for a in msg.to],
                "ccRecipients": [{"emailAddress": {"address": a}} for a in msg.cc],
                "attachments": [
                    {"@odata.type": "#microsoft.graph.fileAttachment",
                     "name": Path(a).name,
                     "contentBytes": base64.b64encode(Path(a).read_bytes()).decode()}
                    for a in msg.attachments
                ],
            },
            "saveToSentItems": True,
        }
        try:
            resp = httpx.post(
                f"https://graph.microsoft.com/v1.0/users/{msg.from_address}/sendMail",
                headers={"Authorization": f"Bearer {token}"},
                content=json.dumps(body), timeout=60)
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            raise SubmissionError(f"Graph send transport error: {exc}", ambiguous=True) from exc
        if resp.status_code not in (200, 202):
            raise SubmissionError(f"Graph sendMail failed: HTTP {resp.status_code} {resp.text[:200]}")
        return f"graph://{msg.from_address}"
