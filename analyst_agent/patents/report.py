"""Report content as a list of blocks, rendered by the app and by the HTML export alike."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pandas as pd


@dataclass
class Block:
    kind: str  # "heading" | "markdown" | "table" | "plotly" | "metrics" | "note"
    title: str = ""
    text: str = ""
    table: pd.DataFrame | None = None
    figure: Any = None  # plotly Figure
    metrics: list[tuple[str, str]] = field(default_factory=list)
    level: int = 2
    caption: str = ""


def heading(title: str, level: int = 2) -> Block:
    return Block("heading", title=title, level=level)


def markdown(text: str, title: str = "") -> Block:
    return Block("markdown", title=title, text=text)


def note(text: str) -> Block:
    return Block("note", text=text)


def table(df: pd.DataFrame, title: str = "", caption: str = "") -> Block:
    return Block("table", title=title, table=df, caption=caption)


def chart(fig, title: str = "", caption: str = "") -> Block:
    return Block("plotly", title=title, figure=fig, caption=caption)


def metrics(items: list[tuple[str, str]], title: str = "") -> Block:
    return Block("metrics", title=title, metrics=items)
