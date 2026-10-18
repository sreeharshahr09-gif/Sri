"""Per-patent extraction: the model reads every patent and fills the study's columns.

Patents go to the model in small batches sized to its context window. Replies are validated
(categories must come from the plan), patents missing from a reply are retried one at a time,
and every answer is cached on disk, so a stopped or crashed run resumes where it left off.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import pandas as pd

from ..llm import LLMClient, LLMError, RequestLimitReached
from .llmjson import ReplyCutOff, chat_text, json_objects, structured_client
from .plan import AnalysisPlan
from .semantic import CACHE_DIR

PROMPT_VERSION = 1
MAX_BATCH = 6
NOT_STATED = "not stated"


@dataclass
class FieldSpec:
    key: str  # JSON key the model writes
    column: str  # column header in the enriched table
    instruction: str
    choices: list[str] | None = None  # allowed values, validated
    multi: bool = False  # several choices allowed, joined with "; "
    limit: int = 160  # characters kept


def build_fields(plan: AnalysisPlan) -> list[FieldSpec]:
    segments = list(plan.segments)
    problems = [p.name for p in plan.problems]
    fields = [
        FieldSpec("topic", "Topic", "the specific technical topic, at most 8 words"),
        FieldSpec("segment", "Segment", "the product segment(s) the patent is for",
                  choices=segments + ["Not specified"] if segments else None, multi=True),
        FieldSpec("problem", plan.problem_label or "Problem",
                  f"the {(plan.problem_label or 'problem').lower()} the patent addresses",
                  choices=problems + ["Other", "Not specified"] if problems else None, multi=True),
        FieldSpec("tech_group", "Technology group", "the technology group of the solution",
                  choices=[b.name for b in plan.tech_buckets] or None),
        FieldSpec("complexity", "Complexity", "how complex the solution is to implement",
                  choices=[c.name for c in plan.complexity_levels] or None),
        FieldSpec("component", "Component", "the part, material or system that is changed, at most 5 words"),
        FieldSpec("performance_target", "Performance target", "the performance it improves, a short phrase"),
        FieldSpec("design_lever", "Design lever", "what is changed to get there (the design, material or process "
                                                   "variable), a short phrase"),
        FieldSpec("mechanism", "Mechanism", "how it works, one sentence", limit=300),
        FieldSpec("core_claim", "Core claim", "the essence of the main independent claim in one plain sentence",
                  limit=400),
        FieldSpec("key_parameters", "Key parameters", "claimed numbers and ranges with units, e.g. \"groove depth "
                                                       "60-80 % of tread depth\"; \"none\" if there are none",
                  limit=240),
        FieldSpec("trade_offs", "Trade-offs", "what it balances or sacrifices, e.g. \"wet grip vs rolling "
                                               "resistance\"; \"not stated\" if the text does not say"),
        FieldSpec("relevance", "Relevance", "relevance to the study scope: High = directly addresses it, "
                                             "Partial = related but secondary, Low = not about it",
                  choices=["High", "Partial", "Low"]),
        FieldSpec("confidence", "Confidence", "how sure you are, given how much the text says",
                  choices=["High", "Medium", "Low"]),
    ]
    taken = {f.column.lower() for f in fields}
    for extra in plan.extra_fields:
        if extra.name.lower() in taken:
            continue
        taken.add(extra.name.lower())
        key = re.sub(r"[^a-z0-9]+", "_", extra.name.lower()).strip("_") or f"extra_{len(fields)}"
        fields.append(FieldSpec(f"x_{key}", extra.name, extra.description or extra.name))
    return fields


def system_prompt(plan: AnalysisPlan, fields: list[FieldSpec]) -> str:
    descriptions = {
        "problem": {p.name: p.description for p in plan.problems},
        "tech_group": {b.name: b.description for b in plan.tech_buckets},
        "complexity": {c.name: c.description for c in plan.complexity_levels},
    }
    lines = [
        "You extract structured facts from patents for an R&D patent landscape study.",
        f"Study: {plan.title}.",
    ]
    if plan.objective:
        lines.append(f"Objective: {plan.objective}")
    if plan.in_scope:
        lines.append(f"Scope: {plan.in_scope}")
    lines += ["", "For every patent, fill these fields:"]
    for f in fields:
        text = f'- "{f.key}": {f.instruction}'
        if f.choices:
            desc = descriptions.get(f.key, {})
            options = "; ".join(f"{c} ({desc[c]})" if desc.get(c) else c for c in f.choices)
            text += (". One or more of: " if f.multi else ". Exactly one of: ") + options
            if f.multi:
                text += ' (separate several with "; ")'
        lines.append(text)
    lines += [
        "",
        ('Reply with ONLY a JSON list with one object per patent, in the order given. Each object starts with '
         '"ref" (the patent\'s reference, e.g. "P1") followed by the fields above.'),
        ("Take every value from the patent text; do not invent numbers or facts. Keep values short. "
         f'Write "{NOT_STATED}" when the text does not say.'),
    ]
    return "\n".join(lines)


def patent_prompt(row: pd.Series) -> str:
    """The text of one patent as the model sees it (also the cache key's source)."""

    def part(label: str, value, limit: int, head: int = 0) -> str | None:
        text = " ".join(str(value or "").split())
        if not text or text.lower() == "nan":
            return None
        if len(text) <= limit:
            return f"{label}: {text}"
        if head:  # the opening and the end: a claim's characterising part ("wherein ...") comes last
            start = text[:head].rsplit(" ", 1)[0]
            end = text[-(limit - head):].split(" ", 1)[-1]
            return f"{label}: {start} … {end}"
        return f"{label}: {text[:limit]}…"

    year = "" if pd.isna(row.get("year")) else str(int(row["year"]))
    head = " | ".join(x for x in [str(row.get("id") or ""), year, str(row.get("assignee") or "")] if x)
    parts = [head, part("Title", row.get("title"), 300), part("Abstract", row.get("abstract"), 1000),
             part("Problem", row.get("problem"), 300), part("Method", row.get("method"), 300),
             part("Advantages", row.get("advantage"), 300), part("First claim", row.get("first_claim"), 1100, head=300)]
    return "\n".join(p for p in parts if p)


# --------------------------------------------------------------------------- reading replies


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(text).lower())


_UNSTATED = {"notstated", "notspecified", "unspecified", "none", "na", "unknown", "notapplicable", "notmentioned"}


def match_choice(value: str, choices: list[str]) -> str | None:
    v = _norm(value)
    if not v:
        return None
    if v in _UNSTATED:
        return "Not specified" if "Not specified" in choices else None
    for c in choices:
        if _norm(c) == v:
            return c
    for c in choices:
        n = _norm(c)
        if len(v) >= 3 and (n.startswith(v) or v.startswith(n) or (len(n) >= 4 and n in v)):
            return c
    return None


def clean_value(spec: FieldSpec, value) -> tuple[str, bool]:
    """(value as stored, whether it had to be corrected)."""
    if isinstance(value, list):
        value = "; ".join(str(v) for v in value)
    text = " ".join(str(value if value is not None else "").split())
    if not spec.choices:
        return (text[: spec.limit] or NOT_STATED), False
    fallback = "Not specified" if "Not specified" in spec.choices else ""
    if spec.multi:
        picked, corrected = [], False
        for part in re.split(r"[;|,]", text):
            if not part.strip():
                continue
            # "camber wear and cupping": every choice named in the part, else the closest one
            hits = [c for c in spec.choices if len(_norm(c)) >= 4 and _norm(c) in _norm(part)]
            if not hits:
                hits = [h for h in [match_choice(part, spec.choices)] if h]
            if not hits:
                corrected = True
                hits = ["Other"] if "Other" in spec.choices else []
            picked += [h for h in hits if h not in picked]
        if len(picked) > 1 and "Not specified" in picked:
            picked.remove("Not specified")
        return ("; ".join(picked) or fallback), corrected or not picked
    hit = match_choice(text, spec.choices)
    return (hit or fallback), hit is None


def _ref(obj: dict) -> int | None:
    digits = re.findall(r"\d+", str(obj.get("ref", obj.get("id", ""))))
    return int(digits[0]) if digits else None


# --------------------------------------------------------------------------- the run


@dataclass
class ExtractionResult:
    table: pd.DataFrame  # one row per patent (index as corpus.std): field columns + "Extraction"
    fields: list[FieldSpec]
    stats: dict = field(default_factory=dict)
    info: str = ""
    stopped: bool = False
    stop_reason: str = ""  # why it stopped, if not at the user's request (e.g. the service's daily limit)


def batch_size_for(context_tokens: int) -> int:
    """Patents per request: about 1,600 tokens each (prompt + reply, with room for a model that thinks first)
    after the instructions and the reply's reserve."""
    return max(1, min(MAX_BATCH, (int(context_tokens or 16384) - 3000) // 1600))


def _signature(system: str, model: str) -> str:
    return hashlib.sha256(f"{PROMPT_VERSION}\x1f{model}\x1f{system}".encode()).hexdigest()[:16]


def _key(prompt: str) -> str:
    return hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:24]


class _Cache:
    def __init__(self, path: Path | None):
        self.path = path
        self.items: dict[str, dict] = {}
        if path is None:
            return
        try:
            for line in path.read_text(encoding="utf-8").splitlines():
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue  # a line cut short by a crash
                if isinstance(rec, dict) and "key" in rec and isinstance(rec.get("values"), dict):
                    self.items[rec["key"]] = rec["values"]
        except OSError:
            pass

    def add(self, key: str, values: dict) -> None:
        self.items[key] = values
        if self.path is None:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps({"key": key, "values": values}, ensure_ascii=False) + "\n")
        except OSError:
            pass


def extract_details(
    std: pd.DataFrame,
    plan: AnalysisPlan,
    llm,
    batch_size: int = 0,
    seed: int = 42,
    use_cache: bool = True,
    on_progress: Callable[[int, int], None] | None = None,
    should_stop: Callable[[], bool] | None = None,
) -> ExtractionResult:
    fields = build_fields(plan)
    system = system_prompt(plan, fields)
    model_name = llm.config.model if isinstance(llm, LLMClient) else type(llm).__name__
    context = llm.config.context_tokens if isinstance(llm, LLMClient) else 16384
    size = batch_size or batch_size_for(context)
    cache = _Cache(CACHE_DIR / "extract" / f"{_signature(system, model_name)}.jsonl" if use_cache else None)
    started = time.perf_counter()

    prompts = {idx: patent_prompt(row) for idx, row in std.iterrows()}
    values: dict = {}
    status: dict = {}
    corrected = 0
    for idx, prompt in prompts.items():
        if _key(prompt) in cache.items:
            values[idx], status[idx] = cache.items[_key(prompt)], "from cache"
    todo = [idx for idx in std.index if idx not in values]
    total = len(std)
    stats = {"patents": total, "from cache": len(values), "extracted": 0, "failed": 0, "model calls": 0,
             "values corrected": 0, "batch size": size}

    reason = ""  # why the last reply gave no answers, for the patents that fail

    def ask(batch: list) -> dict[int, dict]:
        """One request for a batch; returns {position in batch: object}."""
        nonlocal reason
        user = "\n\n".join(f"P{i + 1} | {prompts[idx]}" for i, idx in enumerate(batch))
        client = llm
        if isinstance(llm, LLMClient):
            cfg = llm.config
            client = structured_client(llm, 600 * len(batch) + 1500, read_timeout=max(cfg.read_timeout, 600),
                                       temperature=min(cfg.temperature, 0.2), seed=seed)
        messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        objs: list[dict] = []
        for attempt in range(2):
            stats["model calls"] += 1
            reply = chat_text(client, messages)
            objs = json_objects(reply)
            if objs:
                break
            reason = "the model's reply was empty" if not reply.strip() else "the model's reply held no JSON answers"
            messages += [{"role": "assistant", "content": reply[:3000] or "(empty)"},
                         {"role": "user", "content": "That was not a valid JSON list. Reply with only the JSON list."}]
        by_pos = {}
        for obj in objs:
            ref = _ref(obj)
            if ref is not None and 1 <= ref <= len(batch):
                by_pos.setdefault(ref - 1, obj)
        if not by_pos and len(objs) == len(batch):  # no usable refs: trust the order
            by_pos = dict(enumerate(objs))
        return by_pos

    def store(idx, obj: dict, how: str) -> None:
        nonlocal corrected
        row = {}
        for spec in fields:
            value, fixed = clean_value(spec, obj.get(spec.key))
            row[spec.column] = value
            if fixed and obj.get(spec.key) not in (None, ""):
                corrected += 1
        values[idx], status[idx] = row, how
        cache.add(_key(prompts[idx]), row)

    def report() -> None:
        if on_progress:
            on_progress(len(values) + stats["failed"], total)

    stopped = False
    stop_reason = ""
    hard_failures = 0
    last_error = ""
    report()
    queue = [todo[i : i + size] for i in range(0, len(todo), size)]
    while queue:
        if should_stop is not None and should_stop():
            stopped = True
            break
        batch = queue.pop(0)
        try:
            got = ask(batch)
            hard_failures, last_error = 0, ""
        except ReplyCutOff as exc:  # the model answers, but ran out of room: smaller requests may work
            got, last_error = {}, str(exc)
        except RequestLimitReached as exc:  # every further request would be refused: stop, keep what is done
            stopped, stop_reason = True, str(exc)
            break
        except LLMError as exc:
            got = {}
            last_error = str(exc)
            hard_failures += 1
            if hard_failures >= 3:  # the model is gone: don't grind through every batch
                for idx in batch + [i for b in queue for i in b]:
                    status[idx] = f"failed: {exc}"[:200]
                stats["failed"] += len(batch) + sum(len(b) for b in queue)
                queue = []
                break
        retry = []
        for pos, idx in enumerate(batch):
            if pos in got:
                store(idx, got[pos], "extracted")
                stats["extracted"] += 1
            elif len(batch) > 1:
                retry.append([idx])  # missing from a batch reply: once more on its own
            else:
                status[idx] = f"failed: {last_error or reason or 'no usable answer'}"[:300]
                stats["failed"] += 1
        queue = retry + queue
        report()

    for idx in std.index:
        status.setdefault(idx, "not run (request limit reached)" if stop_reason else "not run (stopped)")
    columns = [f.column for f in fields]
    table = pd.DataFrame.from_dict({idx: values.get(idx, {}) for idx in std.index}, orient="index")
    table = table.reindex(index=std.index, columns=columns)
    table["Extraction"] = pd.Series(status)
    stats["values corrected"] = int(corrected)
    stats["seconds"] = round(time.perf_counter() - started, 1)
    info = (f"{model_name}, {size} patents per request, temperature ≤ 0.2, seed {seed}; categories checked against "
            f"the plan ({stats['values corrected']} values corrected); "
            f"{stats['extracted']} extracted now, {stats['from cache']} from the cache, {stats['failed']} failed")
    return ExtractionResult(table=table, fields=fields, stats=stats, info=info, stopped=stopped,
                            stop_reason=stop_reason)
