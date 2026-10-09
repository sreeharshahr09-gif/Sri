"""Numeric grounding audit: can each number in the answer be traced to executed output?

A lightweight check against hallucinated statistics. A number counts as traced when some
value in the evidence (sandbox output, tables, the dataset profile) rounds to it at the
precision it was written with, also allowing percent and k/M/B scaling. Untraced numbers
are not necessarily wrong (they may be derived in the model's head), but they deserve a look.
"""

from __future__ import annotations

import bisect
import math
import re
from dataclasses import dataclass, field

_ANSWER_NUMBER = re.compile(
    r"(?<![\w.])([<>≤≥~≈]\s*)?(-?\d{1,3}(?:,\d{3})+(?:\.\d+)?|-?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?)"
    r"(\s*(?:%|percent\b|[kKmMbB]\b|thousand\b|million\b|billion\b|pp\b))?"
)
_EVIDENCE_NUMBER = re.compile(r"-?\d{1,3}(?:,\d{3})+(?:\.\d+)?|-?\d*\.?\d+(?:[eE][-+]?\d+)?")
_SCALE = {"k": 1e3, "thousand": 1e3, "m": 1e6, "million": 1e6, "b": 1e9, "billion": 1e9}


@dataclass
class GroundingReport:
    checked: list[str] = field(default_factory=list)
    untraced: list[str] = field(default_factory=list)

    @property
    def traced_count(self) -> int:
        return len(self.checked) - len(self.untraced)

    @property
    def fully_traced(self) -> bool:
        return not self.untraced

    def to_dict(self) -> dict:
        return {"checked": self.checked, "untraced": self.untraced}

    @classmethod
    def from_dict(cls, data: dict) -> "GroundingReport":
        return cls(checked=list(data.get("checked", [])), untraced=list(data.get("untraced", [])))


def _decimals(token: str) -> int:
    mantissa = token.lower().split("e")[0]
    return len(mantissa.split(".")[1]) if "." in mantissa else 0


def _evidence_values(texts: list[str]) -> list[float]:
    values: set[float] = set()
    for text in texts:
        if not text:
            continue
        for match in _EVIDENCE_NUMBER.finditer(text):
            try:
                value = float(match.group(0).replace(",", ""))
            except ValueError:
                continue
            if math.isfinite(value):
                values.add(value)
                values.add(abs(value))
    return sorted(values)


def _has_value_in(values: list[float], low: float, high: float) -> bool:
    i = bisect.bisect_left(values, low)
    return i < len(values) and values[i] <= high


def check_grounding(answer: str, evidence: list[str], question: str = "") -> GroundingReport:
    values = _evidence_values(evidence)
    question_numbers = {m.group(2).replace(",", "") for m in _ANSWER_NUMBER.finditer(question)}
    # Ignore numbers inside inline code / code blocks and markdown list markers.
    text = re.sub(r"```.*?```|`[^`]*`", " ", answer, flags=re.DOTALL)
    text = re.sub(r"^\s*\d+[.)]\s", " ", text, flags=re.MULTILINE)

    report = GroundingReport()
    for match in _ANSWER_NUMBER.finditer(text):
        qualifier, token, suffix = match.group(1), match.group(2), (match.group(3) or "").strip().lower()
        plain = token.replace(",", "")
        try:
            number = float(plain)
        except ValueError:
            continue
        if qualifier:  # thresholds such as "p < 0.05" are not claims about the data
            continue
        if plain in question_numbers:
            continue
        if not suffix and number.is_integer() and abs(number) < 10:
            continue  # small counts and ordinals are too ambiguous to audit
        if not suffix and number.is_integer() and 1900 <= number <= 2100:
            continue  # years

        label = match.group(0).strip()
        report.checked.append(label)
        if not _traced(number, _decimals(token), suffix, values):
            report.untraced.append(label)
    return report


def _traced(number: float, decimals: int, suffix: str, values: list[float]) -> bool:
    half_ulp = 0.5 * 10 ** (-decimals)
    candidates: list[tuple[float, float]] = []
    scale = _SCALE.get(suffix)
    if scale:
        candidates.append((number * scale, half_ulp * scale))
    elif suffix in ("%", "percent", "pp"):
        candidates.append((number, half_ulp))  # already a percentage in the output
        candidates.append((number / 100, half_ulp / 100))  # a fraction in the output
    else:
        candidates.append((number, half_ulp))
    for target, tolerance in candidates:
        # Rounding tolerance plus a small relative slack for differently rounded displays.
        tol = tolerance + abs(target) * 1e-9
        for t in {target, abs(target)}:
            if _has_value_in(values, t - tol, t + tol):
                return True
    return False


# --------------------------------------------------------------------------- file citations

_CITATION = re.compile(
    r"(?<![\w/.-])((?:\.{0,2}/)?[\w.\-]+(?:/[\w.\- ]+)*\.[A-Za-z0-9]{1,8})"  # path with extension
    r"(?::|#L|\s+lines?\s+|,\s*lines?\s+)(\d+)(?:\s*(?:-|–|to)\s*L?(\d+))?",
    re.IGNORECASE,
)


@dataclass
class Citation:
    path: str
    start: int
    end: int
    status: str  # "verified" (seen in this run), "unseen" (exists, not opened), "invalid"
    detail: str = ""

    @property
    def label(self) -> str:
        return f"{self.path}:{self.start}" + (f"-{self.end}" if self.end != self.start else "")


@dataclass
class CitationReport:
    citations: list[Citation] = field(default_factory=list)

    def by_status(self, status: str) -> list[Citation]:
        return [c for c in self.citations if c.status == status]

    def to_dict(self) -> dict:
        return {"citations": [c.__dict__ for c in self.citations]}

    @classmethod
    def from_dict(cls, data: dict) -> "CitationReport":
        return cls([Citation(**c) for c in data.get("citations", [])])


def check_citations(answer: str, line_counts, seen: dict[str, list[tuple[int, int]]]) -> CitationReport:
    """Check `path:line` references in an answer.

    `line_counts(path)` returns (normalized_path, n_lines) or raises ValueError for a path that
    does not exist; `seen` maps normalized paths to line ranges the agent actually viewed.
    """
    report = CitationReport()
    keys: set[tuple[str, int, int]] = set()
    for match in _CITATION.finditer(answer):
        path, start = match.group(1), int(match.group(2))
        end = int(match.group(3)) if match.group(3) else start
        if end < start:
            start, end = end, start
        try:
            norm, n_lines = line_counts(path)
        except ValueError as exc:
            cite = Citation(path, start, end, "invalid", str(exc))
        else:
            if start < 1 or end > n_lines:
                cite = Citation(norm, start, end, "invalid", f"the file has {n_lines} lines")
            elif any(a <= end and start <= b for a, b in seen.get(norm, [])):
                cite = Citation(norm, start, end, "verified")
            else:
                cite = Citation(norm, start, end, "unseen", "these lines were not opened in this answer")
        key = (cite.path, cite.start, cite.end)
        if key not in keys:
            keys.add(key)
            report.citations.append(cite)
    return report
