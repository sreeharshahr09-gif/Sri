"""Asking the model for JSON and getting usable JSON back."""

from __future__ import annotations

import json
import re
from dataclasses import replace

from ..llm import LLMClient, LLMError, ReplyCutOff
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


def structured_client(llm, max_tokens: int | None = None, **settings):
    """The client for a structured task: no thinking, the given reply budget and other settings."""
    if not isinstance(llm, LLMClient):
        return llm  # a test double or another ChatModel: used as it is
    cfg = replace(llm.config, no_think=True, **settings)
    if max_tokens:
        cfg = replace(cfg, max_tokens=max_tokens)
    return type(llm)(cfg, session=llm.session)


def chat_text(llm, messages: list[dict]) -> str:
    """The reply text; when the reply budget ran out before any answer, once more with twice the budget
    (as far as the context window allows)."""
    try:
        return llm.chat(messages).content
    except ReplyCutOff:
        if not isinstance(llm, LLMClient):
            raise
        cfg = llm.config
        prompt_tokens = sum(len(m.get("content") or "") for m in messages) // 3
        room = cfg.context_tokens - prompt_tokens - 256
        if room <= cfg.max_tokens:
            raise
        bigger = type(llm)(replace(cfg, max_tokens=min(cfg.max_tokens * 2, room)), session=llm.session)
        return bigger.chat(messages).content


def json_objects(text: str) -> list[dict]:
    """The objects of a JSON list reply; from a reply cut off mid-list, the complete ones."""
    try:
        data = extract_json(text)
    except JSONReplyError:
        data = None
    if isinstance(data, list):
        return [d for d in data if isinstance(d, dict)]
    if isinstance(data, dict):
        inner = next((v for v in data.values() if isinstance(v, list)), None)
        return [d for d in inner if isinstance(d, dict)] if inner is not None else [data]
    text = strip_thinking(text or "")
    out, depth, start, in_str, escape = [], 0, None, False, False
    for i, ch in enumerate(text):
        if in_str:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}" and depth:
            depth -= 1
            if depth == 0 and start is not None:
                try:
                    obj = json.loads(text[start : i + 1])
                except json.JSONDecodeError:
                    obj = None
                if isinstance(obj, dict):
                    out.append(obj)
    return out


def ask_json(llm, system: str, user: str, retries: int = 1, max_tokens: int | None = None):
    """Chat with the model (without thinking) and parse JSON from its reply, asking once more on a bad reply."""
    llm = structured_client(llm, max_tokens)
    messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    last: Exception | None = None
    for _ in range(retries + 1):
        reply = chat_text(llm, messages)
        try:
            return extract_json(reply)
        except JSONReplyError as exc:
            last = exc
            messages += [
                {"role": "assistant", "content": reply[:4000] or "(empty)"},
                {"role": "user", "content": f"That was not valid JSON ({exc}). Reply with only the JSON, nothing else."},
            ]
    raise JSONReplyError(str(last))


def ask_json_list(llm, system: str, user: str, retries: int = 1, max_tokens: int | None = None) -> list[dict]:
    """Like ask_json for a JSON list, but keeps the complete objects of a reply that was cut off."""
    llm = structured_client(llm, max_tokens)
    messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    for _ in range(retries + 1):
        reply = chat_text(llm, messages)
        objs = json_objects(reply)
        if objs:
            return objs
        messages += [
            {"role": "assistant", "content": reply[:3000] or "(empty)"},
            {"role": "user", "content": "That was not a valid JSON list. Reply with only the JSON list."},
        ]
    raise JSONReplyError("no JSON objects in the reply")


__all__ = ["JSONReplyError", "LLMError", "ReplyCutOff", "ask_json", "ask_json_list", "chat_text", "extract_json",
           "json_objects", "structured_client"]
