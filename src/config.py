"""Config loading and the unified patent record schema."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml


@dataclass
class Config:
    raw: dict

    @classmethod
    def load(cls, path: str | Path = "config.yaml") -> "Config":
        with open(path, "r", encoding="utf-8") as fh:
            return cls(yaml.safe_load(fh))

    def __getitem__(self, key):
        return self.raw[key]

    def get(self, key, default=None):
        return self.raw.get(key, default)


# Canonical columns every ingested record is normalized to. Keeping this in one
# place means every parser (CSV/XML/PDF) targets the same schema.
CANONICAL_FIELDS = [
    "doc_id",        # publication number, e.g. US10action...A1 / EP...B1
    "title",
    "abstract",
    "claims",        # concatenated claim text (may be empty)
    "description",   # detailed description full text (may be empty)
    "assignee",
    "year",          # int priority or publication year
    "cpc",           # semicolon-joined CPC codes
    "source_file",   # provenance
]
