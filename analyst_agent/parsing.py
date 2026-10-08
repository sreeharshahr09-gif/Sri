"""Robust extraction of code and final answers from free-form model replies."""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass

_THINK = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
_FENCE = re.compile(r"```[ \t]*([\w+-]*)[^\n]*\n(.*?)```", re.DOTALL)
_OPEN_FENCE = re.compile(r"```[ \t]*(python|py|python3)?[ \t]*\n", re.IGNORECASE)
_FINAL = re.compile(
    r"^[ \t>#*_-]*final[ \t]+answer\b[ \t*_]*(?::[ \t*_]*|$)",
    re.IGNORECASE | re.MULTILINE,
)
_LEGACY_CODE = re.compile(r"^[ \t-]*code[ \t]*:[ \t]*", re.IGNORECASE | re.MULTILINE)
_PY_LANGS = {"python", "py", "python3", "ipython", "pycon"}


@dataclass
class ParsedReply:
    raw: str
    thought: str = ""
    code: str | None = None
    final_answer: str | None = None
    truncated_code: bool = False  # an opening fence without a closing one


def strip_thinking(text: str) -> str:
    text = _THINK.sub("", text)
    # An unterminated <think> (e.g. truncated output) hides everything after it.
    lowered = text.lower()
    if "<think>" in lowered:
        text = text[: lowered.index("<think>")]
    return text.replace("</think>", "").strip()


def _is_python(source: str) -> bool:
    try:
        ast.parse(source)
    except SyntaxError:
        return False
    return True


def _clean_code(code: str) -> str:
    lines = code.strip("\n").splitlines()
    # Strip interactive prompts if the model wrote a console transcript.
    if lines and all(ln.startswith((">>> ", "... ")) or not ln.strip() for ln in lines if ln.strip()):
        lines = [ln[4:] if ln.startswith((">>> ", "... ")) else ln for ln in lines]
    return "\n".join(lines).strip()


def extract_code(text: str) -> tuple[str | None, int, int]:
    """Return (code, start, end) of the python fenced blocks, joined in order."""
    blocks: list[str] = []
    first_start, last_end = -1, -1
    for match in _FENCE.finditer(text):
        lang = match.group(1).lower()
        body = _clean_code(match.group(2))
        if not body:
            continue
        if lang in _PY_LANGS or (not lang and _is_python(body)):
            blocks.append(body)
            if first_start < 0:
                first_start = match.start()
            last_end = match.end()
    if not blocks:
        return None, -1, -1
    return "\n\n".join(blocks), first_start, last_end


def parse_reply(text: str) -> ParsedReply:
    raw = text
    text = strip_thinking(text)
    reply = ParsedReply(raw=raw)

    final_match = _FINAL.search(text)
    head = text[: final_match.start()] if final_match else text
    code, start, _ = extract_code(head)

    if code:
        reply.code = code
        reply.thought = head[:start].strip()
        # Anything claimed as a final answer alongside unexecuted code is ignored on purpose:
        # answers must be written after seeing the results.
        return reply

    if final_match:
        reply.thought = head.strip()
        reply.final_answer = text[final_match.end():].strip()
        return reply

    fence = _OPEN_FENCE.search(text)
    if fence and text.count("```") % 2 == 1:
        reply.truncated_code = True
        reply.thought = text[: fence.start()].strip()
        return reply

    legacy = _LEGACY_CODE.search(text)
    if legacy:
        candidate = _clean_code(text[legacy.end():])
        if candidate and _is_python(candidate):
            reply.code = candidate
            reply.thought = text[: legacy.start()].strip()
            return reply

    reply.final_answer = text.strip()
    return reply
