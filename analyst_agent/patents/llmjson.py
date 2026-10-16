"""Asking the local model for JSON and getting usable JSON back."""

from __future__ import annotations

import json
import re
from dataclasses import replace

from ..llm import LLMClient, LLMError
from ..parsing import _loads_lenient, strip_thinking


class JSONReplyError(ValueError):
    pass


def extract_json(text: str):
    """The first JSON object (or array) in a model reply, tolerating fences and small syntax slips."""
    text = strip_thinking(text or "")
    fenced = re.search(r"```(?:json)?\s*\n(.*?)```", text, re.DOTALL)
    if fenced:
        text = fenced.group(1)
    starts = [i for i in (text.find("{"), text.find("[")) if i >= 0]
    if not starts:
        raise JSONReplyError("no JSON object in the reply")
    start = min(starts)
    closer = "}" if text[start] == "{" else "]"
    end = text.rfind(closer)
    if end <= start:
        raise JSONReplyError("the JSON object is not closed (reply cut off?)")
    chunk = text[start : end + 1]
    try:
        return json.loads(chunk)
    except json.JSONDecodeError:
        if closer == "]":
            fixed = re.sub(r",\s*([}\]])", r"\1", chunk)
            try:
                return json.loads(fixed)
            except json.JSONDecodeError as exc:
                raise JSONReplyError(str(exc)) from exc
        try:
            return _loads_lenient(chunk)
        except (ValueError, SyntaxError) as exc:
            raise JSONReplyError(str(exc)) from exc


def ask_json(llm, system: str, user: str, retries: int = 1, max_tokens: int | None = None):
    """Chat with the model and parse JSON from its reply, asking once more on a bad reply."""
    if max_tokens and isinstance(llm, LLMClient):
        llm = LLMClient(replace(llm.config, max_tokens=max_tokens), session=llm.session)
    messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    last: Exception | None = None
    for _ in range(retries + 1):
        reply = llm.chat(messages).content
        try:
            return extract_json(reply)
        except JSONReplyError as exc:
            last = exc
            messages += [
                {"role": "assistant", "content": reply[:4000] or "(empty)"},
                {"role": "user", "content": f"That was not valid JSON ({exc}). Reply with only the JSON, nothing else."},
            ]
    raise JSONReplyError(str(last))


__all__ = ["JSONReplyError", "LLMError", "ask_json", "extract_json"]
