"""Specific-gravity lookup, keyed by component / layer name.

Values are in g/cm3. These are **placeholder** compound densities in the
typical range for tyre rubber compounds and reinforcement; replace them with
the real compound table before trusting weights (see CLAUDE.md open decisions).

Matching is case-insensitive: keys are stored upper-cased and layer names are
upper-cased before lookup, so ``Tread``/``TREAD``/``tread`` all resolve.
"""

from __future__ import annotations

# Placeholder specific gravities (g/cm3). Order is only for readability.
DEFAULT_SG_TABLE: dict[str, float] = {
    "TREAD": 1.15,
    "SIDEWALL": 1.10,
    "APEX": 1.12,
    "INNERLINER": 1.20,
    "BELT-1": 1.30,
    "BELT-2": 1.30,
    "PLY-1": 1.25,
    "PLY-2": 1.25,
    "BEAD": 7.85,       # steel bead wire bundle
    "BEAD-FILLER": 1.12,
    "CHAFER": 1.18,
}

# Fallback SG used when a layer has no table entry (and no override supplied).
FALLBACK_SG: float = 1.10


def normalise_key(name: str) -> str:
    """Canonical (upper-cased, stripped) form of a component/layer name."""
    return name.strip().upper()


def build_lookup(table: dict[str, float] | None = None) -> dict[str, float]:
    """Return a copy of *table* (or the default) with normalised keys."""
    src = DEFAULT_SG_TABLE if table is None else table
    return {normalise_key(k): float(v) for k, v in src.items()}


def lookup_sg(
    layer_name: str,
    table: dict[str, float] | None = None,
    fallback: float | None = FALLBACK_SG,
) -> tuple[float, bool]:
    """Look up the specific gravity for *layer_name*.

    Returns ``(sg, matched)`` where ``matched`` is ``True`` when the name was
    found in the table. When not found and *fallback* is not ``None``, the
    fallback SG is returned with ``matched=False`` so callers can flag it.
    """
    lut = build_lookup(table)
    key = normalise_key(layer_name)
    if key in lut:
        return lut[key], True
    if fallback is None:
        raise KeyError(f"No specific gravity for layer {layer_name!r}")
    return fallback, False
