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
