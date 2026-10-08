from __future__ import annotations

import pandas as pd
import pytest

from analyst_agent.config import SandboxConfig
from analyst_agent.data import Dataset
from analyst_agent.llm import Completion
from analyst_agent.sandbox import Sandbox


@pytest.fixture
def sales_df() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "region": ["West", "East", "West", "North", "East", "West"],
            "sales": [100.0, 250.5, 300.0, 80.0, 120.0, 50.0],
            "units": [1, 3, 4, 1, 2, 1],
            "date": pd.date_range("2024-01-01", periods=6, freq="MS"),
        }
    )


@pytest.fixture
def dataset(sales_df) -> Dataset:
    return Dataset(df=sales_df, name="sales.csv", sha256="0" * 64, source_format="delimited text")


@pytest.fixture
def sandbox():
    sb = Sandbox(SandboxConfig(timeout_seconds=30, memory_limit_mb=0))
    yield sb
    sb.close()


class ScriptedLLM:
    """Returns pre-written replies in order and records the conversations it saw."""

    def __init__(self, replies: list[str]):
        self.replies = list(replies)
        self.calls: list[list[dict[str, str]]] = []

    def chat(self, messages):
        self.calls.append([dict(m) for m in messages])
        if not self.replies:
            raise AssertionError("ScriptedLLM ran out of replies")
        return Completion(content=self.replies.pop(0), latency_s=0.01, prompt_tokens=10, completion_tokens=5)
