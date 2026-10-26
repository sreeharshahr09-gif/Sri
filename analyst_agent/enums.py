"""Base for the fixed vocabularies (statuses, event kinds, output kinds) shared by the core and the UI."""

from __future__ import annotations

from enum import Enum


class StrEnum(str, Enum):
    """An enum whose members are their string values.

    Members compare and hash equal to the plain string and print, format and serialise to JSON as it, so
    saved runs and transcripts stay plain text and older ones load unchanged. A misspelt member name fails
    at once, where a misspelt string literal would silently never match. (enum.StrEnum needs Python 3.11.)
    """

    __hash__ = str.__hash__

    def __str__(self) -> str:
        return str(self.value)
