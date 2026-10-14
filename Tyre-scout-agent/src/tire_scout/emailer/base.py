"""Email adapter protocol and factory. Adapters are replaceable; the choice is
configuration, not code. Every adapter submits through the journal."""
from __future__ import annotations

from typing import Protocol

from .journal import OutboundMessage


class SubmissionError(Exception):
    """Raised when submission failed *before* anything reached the provider."""
    ambiguous: bool = False

    def __init__(self, message: str, ambiguous: bool = False):
        super().__init__(message)
        self.ambiguous = ambiguous  # True => maybe submitted -> journal 'uncertain'


class EmailAdapter(Protocol):
    name: str

    def submit(self, msg: OutboundMessage) -> str:
        """Submit and return a provider ID. Raises SubmissionError on failure."""
        ...


def build_adapter(cfg) -> EmailAdapter:
    if cfg.email.adapter == "preview":
        from .preview import PreviewAdapter
        return PreviewAdapter(cfg)
    if cfg.email.adapter == "smtp":
        from .smtp_adapter import SmtpAdapter
        return SmtpAdapter(cfg)
    if cfg.email.adapter == "outlook_com":
        from .outlook_com import OutlookComAdapter
        return OutlookComAdapter(cfg)
    if cfg.email.adapter == "graph":
        from .graph import GraphAdapter
        return GraphAdapter(cfg)
    raise ValueError(f"unknown email adapter: {cfg.email.adapter}")
