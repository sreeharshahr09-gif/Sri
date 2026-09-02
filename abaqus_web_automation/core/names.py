"""Abaqus identifier handling.

Every layer (set creation, surface creation, material/BC/load references, ODB
extraction) must agree on the *same* Abaqus identifier for a given project name.
v0.6 sanitised only some of those places, so a set called ``Tread Contact`` was
created in Abaqus as ``Tread Contact`` but referenced as ``SURF_Tread_Contact``,
raising a KeyError inside the generated script.  All name mangling now goes
through this module.
"""

from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Tuple

# Abaqus limits object names to 80 characters.  Generated names carry a prefix
# (SURF_, N_, PROP_, COUP_, SEC_), so base names are kept shorter to leave room.
MAX_NAME = 80
MAX_BASE_NAME = 72

_INVALID = re.compile(r"[^A-Za-z0-9_]")


def abaqus_name(name: Any, prefix: str = "S", max_length: int = MAX_BASE_NAME) -> str:
    """Return an Abaqus-legal identifier derived from ``name``.

    Deterministic: the same input always maps to the same output, which is what
    lets the generator reference a set it created earlier.
    """
    s = _INVALID.sub("_", str(name if name is not None else "").strip())
    s = s.strip("_") or prefix
    if s[0].isdigit():
        s = "%s_%s" % (prefix, s)
    return s[:max_length]


def prefixed(prefix: str, name: Any) -> str:
    """SURF_/N_-style derived name that stays inside the Abaqus length limit."""
    return (prefix + abaqus_name(name))[:MAX_NAME]


def name_collisions(names: Iterable[Any]) -> List[str]:
    """Report project names that would sanitise to the same Abaqus identifier.

    ``a b`` and ``a-b`` both become ``a_b``, which would silently overwrite a set
    inside Abaqus.  Callers surface these as validation errors instead of
    generating a broken model.
    """
    seen: Dict[str, str] = {}
    collisions: List[str] = []
    for raw in names:
        key = str(raw)
        safe = abaqus_name(key)
        if safe in seen and seen[safe] != key:
            collisions.append(
                "'%s' and '%s' both become the Abaqus name '%s'." % (seen[safe], key, safe)
            )
        seen.setdefault(safe, key)
    return collisions


def safe_filename(name: Any, fallback: str = "ALL") -> str:
    """Filesystem-safe fragment for generated CSV file names."""
    s = _INVALID.sub("_", str(name if name is not None else "").strip())
    return (s.strip("_") or fallback)[:60]
