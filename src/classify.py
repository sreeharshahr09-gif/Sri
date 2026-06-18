"""
Tag each passage with the report categories the boss asked about.

This is deliberately weak-supervision (phrase matching against the
CATEGORY_FRAMEWORK lexicons) rather than a trained classifier: it is transparent,
needs no labels, and is trivially auditable/editable by a domain expert. A
passage can carry several categories (e.g. an "advantage" that is also a
"trade-off").
"""

from __future__ import annotations

import pandas as pd

from . import domain


def tag_categories(passages: pd.DataFrame) -> pd.DataFrame:
    passages = passages.copy()
    lower = passages["passage"].str.lower()

    for cat, spec in domain.CATEGORY_FRAMEWORK.items():
        phrases = spec["phrases"]
        # Count phrase hits; >0 means the category applies to this passage.
        hits = sum(lower.str.count(p) for p in phrases)
        passages[f"cat_{cat}"] = hits
    return passages


def category_columns() -> list[str]:
    return [f"cat_{c}" for c in domain.category_names()]
