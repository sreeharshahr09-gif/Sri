"""Robust extraction of code and final answers from free-form model replies."""

from __future__ import annotations

import ast
import json
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


# --------------------------------------------------------------------------- tool calls

_TOOL_FENCE = re.compile(r"```[ \t]*(?:tool|json|tool_call)[ \t]*\n(.*?)```", re.DOTALL | re.IGNORECASE)
_TOOL_TAG = re.compile(r"<tool_call>\s*(.*?)\s*(?:</tool_call>|$)", re.DOTALL | re.IGNORECASE)


@dataclass
class ParsedAction:
    """One step of the workspace agent: a tool call, a code block or a final answer."""

    raw: str
    thought: str = ""
    tool: str | None = None
    args: dict | None = None
    code: str | None = None
    final_answer: str | None = None
    error: str | None = None  # a malformed tool call, explained for the model
    truncated: bool = False


def _loads_lenient(text: str) -> dict:
    text = text.strip()
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        cleaned = re.sub(r",\s*([}\]])", r"\1", text)  # trailing commas
        try:
            value = json.loads(cleaned)
        except json.JSONDecodeError:
            # Python-style dicts (single quotes, True/None) from models that mix the two.
            value = ast.literal_eval(cleaned)
    if not isinstance(value, dict):
        raise ValueError("a tool call must be a JSON object")
    return value


def _normalize_call(obj: dict) -> tuple[str, dict]:
    if "tool" in obj:
        name = obj["tool"]
        args = obj.get("args") or obj.get("arguments") or {k: v for k, v in obj.items() if k != "tool"}
    elif "name" in obj:  # OpenAI / Qwen native style
        name = obj["name"]
        args = obj.get("arguments") or obj.get("parameters") or {}
    else:
        raise ValueError('missing "tool" field')
    if isinstance(args, str):
        args = _loads_lenient(args) if args.strip() else {}
    if not isinstance(name, str) or not isinstance(args, dict):
        raise ValueError("tool name must be a string and arguments an object")
    return name.strip(), args


def parse_action(text: str) -> ParsedAction:
    raw = text
    text = strip_thinking(text.replace("<tool_call>", "\n<tool_call>"))
    action = ParsedAction(raw=raw)

    final_match = _FINAL.search(text)
    head = text[: final_match.start()] if final_match else text

    candidates: list[tuple[int, str, str]] = []  # (position, kind, payload)
    for m in _TOOL_FENCE.finditer(head):
        candidates.append((m.start(), "tool", m.group(1)))
    for m in _TOOL_TAG.finditer(head):
        candidates.append((m.start(), "tool", m.group(1)))
    code, code_start, _ = extract_code(head)
    if code:
        candidates.append((code_start, "code", code))

    if candidates:
        pos, kind, payload = min(candidates, key=lambda c: c[0])
        action.thought = head[:pos].strip()
        if kind == "code":
            # An unlabelled fence holding {"tool": ...} is a dict literal, not analysis code.
            try:
                obj = _loads_lenient(payload)
            except (ValueError, SyntaxError):
                obj = None
            if isinstance(obj, dict) and ("tool" in obj or "name" in obj):
                kind = "tool"
            else:
                action.code = payload
                return action
        try:
            action.tool, action.args = _normalize_call(_loads_lenient(payload))
        except (ValueError, SyntaxError) as exc:
            action.error = f"Could not parse the tool call ({exc}). Send one valid JSON object."
        return action

    if final_match:
        action.thought = head.strip()
        action.final_answer = text[final_match.end():].strip()
        return action

    if text.count("```") % 2 == 1:
        action.truncated = True
        action.thought = text[: text.rfind("```")].strip()
        return action

    action.final_answer = text.strip()
    return action
