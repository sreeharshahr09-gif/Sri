"""Classic Outlook COM adapter (Windows desktop). Requires a signed-in Outlook
profile for the approved work account. Inspect the existing watcher integration
before choosing this; it needs an interactive Windows session for Task
Scheduler runs."""
from __future__ import annotations

from pathlib import Path

from .base import SubmissionError
from .journal import OutboundMessage


class OutlookComAdapter:
    name = "outlook_com"

    def __init__(self, cfg):
        self.cfg = cfg
        try:
            import win32com.client  # type: ignore
            self._client = win32com.client
        except ImportError as exc:  # not Windows / pywin32 missing
            raise SubmissionError(
                "Outlook COM unavailable (requires Windows + pywin32 + signed-in Outlook)") from exc

    def submit(self, msg: OutboundMessage) -> str:
        try:
            outlook = self._client.Dispatch("Outlook.Application")
            mail = outlook.CreateItem(0)  # olMailItem
            mail.Subject = msg.subject
            mail.To = ";".join(msg.to)
            if msg.cc:
                mail.CC = ";".join(msg.cc)
            mail.HTMLBody = msg.html
            for att in msg.attachments:
                mail.Attachments.Add(str(Path(att).resolve()))
            # verify the sending account is the approved work account
            accounts = outlook.Session.Accounts
            matched = None
            for i in range(1, accounts.Count + 1):
                if accounts.Item(i).SmtpAddress.lower() == msg.from_address.lower():
                    matched = accounts.Item(i)
                    break
            if matched is None:
                raise SubmissionError(
                    f"approved account {msg.from_address} not found in Outlook profile")
            mail.SendUsingAccount = matched
            mail.Send()
            return f"outlook://{msg.from_address}/{mail.Subject}"
        except SubmissionError:
            raise
        except Exception as exc:  # COM errors during Send are ambiguous
            raise SubmissionError(f"Outlook COM send error: {exc}", ambiguous=True) from exc
