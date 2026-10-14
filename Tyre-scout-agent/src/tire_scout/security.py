"""Untrusted-content handling and report scrubbing.

Everything fetched from the web, parsed from PDFs/transcripts, or read from old
emails is DATA. It is wrapped in delimiters before the LLM sees it, scanned for
instruction-like patterns (logged, never obeyed), and outgoing reports are
scrubbed of personal/legacy metadata.
"""
from __future__ import annotations

import re

# -- injection heuristics (detection for logging/flagging; delimiters + the
#    guard prompt are the actual defence) ------------------------------------ #
INJECTION_PATTERNS = [
    r"ignore (all |any )?(previous|prior|above) instructions",
    r"send (an |the )?(e-?mail|message) to",
    r"add .{0,40} to the (recipient|to|cc) list",
    r"reveal|print|exfiltrate .{0,40}(api key|secret|password|token|config)",
    r"run|execute .{0,30}(command|shell|powershell|cmd)",
    r"you are now .{0,30}(mode|assistant|agent)",
    r"do not follow .{0,30}(rules|instructions|guidelines)",
]
_INJECTION_RE = re.compile("|".join(f"({p})" for p in INJECTION_PATTERNS), re.IGNORECASE)


def scan_for_injection(text: str) -> list[str]:
    """Return matched suspicious patterns (for audit logging/flagging)."""
    return sorted({m.group(0).strip()[:80] for m in _INJECTION_RE.finditer(text or "")})


# -- outgoing-report scrubber ------------------------------------------------- #
PERSONAL_GMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@gmail\.com", re.IGNORECASE)
# local paths can contain spaces (OneDrive - Apollo Tyres Limited) -> match to EOL
FS_PATH_RE = re.compile(r"[A-Za-z]:\\Users\\[^\n<\"']*")
ONEDRIVE_PATH_RE = re.compile(r"[A-Za-z]:\\[^\n<\"']*OneDrive[^\n<\"']*")
RECEIVED_HEADER_RE = re.compile(r"^(received|from|sent|to|subject):\s.*$", re.IGNORECASE | re.MULTILINE)
EXTERNAL_BANNER_RE = re.compile(
    r"(caution|warning)[!:：]?\s*(this )?(e-?mail )?(is |was |originated )?(from )?(external|outside)",
    re.IGNORECASE)
TRACKING_IMG_RE = re.compile(r'<img[^>]+(?:width=["\']?1["\']?[^>]+height=["\']?1["\']?|'
                             r'height=["\']?1["\']?[^>]+width=["\']?1["\']?)[^>]*>', re.IGNORECASE)
TRACKING_URL_RE = re.compile(r"https?://[^\s\"'<>]*(?:mailtrack|track\.|/open\?|beacon)[^\s\"'<>]*",
                             re.IGNORECASE)


def scrub_report(text: str) -> str:
    """Remove personal references and legacy mail metadata from outgoing
    content. Technical limitations and source attribution are preserved."""
    if not text:
        return text
    out = PERSONAL_GMAIL_RE.sub("[removed personal address]", text)
    out = FS_PATH_RE.sub("[removed local path]", out)
    out = ONEDRIVE_PATH_RE.sub("[removed local path]", out)
    out = TRACKING_IMG_RE.sub("", out)
    out = TRACKING_URL_RE.sub("[removed tracking link]", out)
    out = EXTERNAL_BANNER_RE.sub("[removed legacy warning banner]", out)
    # drop legacy forwarded-mail header lines only when they are standalone lines
    lines = []
    for line in out.splitlines():
        if RECEIVED_HEADER_RE.match(line.strip()) and len(line) < 300:
            continue
        lines.append(line)
    return "\n".join(lines)
