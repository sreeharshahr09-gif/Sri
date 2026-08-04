"""DXF import -- turns a real tread-plan drawing into a :class:`Pattern`.

This replaces the synthetic generator as the production source of geometry
(swap point 1 of the brief).  A mould tread plan is typically exported as a soup
of LINE and ARC entities with no polygon structure at all, so the work here is:

1. read LINE / ARC / LWPOLYLINE / POLYLINE entities and flatten arcs to chords;
2. stitch segments into chains by shared endpoints;
3. close chains that wrap across the ``x = 0`` / ``x = circumference`` seam by
   translating one end a full circumference and joining;
4. drop construction geometry (the centreline, borders, zero-length segments);
5. shift the lateral origin to the tread centreline and classify zones.

Only a dependency-free parser is used -- no ezdxf -- because the entity subset a
tread plan needs is small and vendoring a parser keeps the tool installable
anywhere.

Per-block attributes a 2D outline cannot carry
----------------------------------------------
A tread plan has no depth information.  Non-skid depth, draft angle and sipe
counts must therefore come from the caller (:class:`BlockDefaults`), either as
one value for the whole tyre or per zone.  They are recorded in
``Pattern.meta['assumed']`` so the report can say which numbers were assumed
rather than measured.
"""

from __future__ import annotations

import math
import os
from collections import defaultdict
from dataclasses import dataclass, field

import numpy as np

from .schema import Block, CrownProfile, Pattern, Pitch, classify_zone

Point = tuple[float, float]


# ---------------------------------------------------------------------------
# low-level DXF reading
# ---------------------------------------------------------------------------
def read_dxf_entities(path: str) -> list[dict]:
    """Read group-code pairs from the ENTITIES section into per-entity dicts."""
    with open(path, "r", errors="replace") as fh:
        raw = [line.rstrip("\n").rstrip("\r") for line in fh]
    pairs = [(raw[i].strip(), raw[i + 1].strip()) for i in range(0, len(raw) - 1, 2)]

    entities: list[dict] = []
    in_entities = False
    current: dict | None = None
    for idx, (code, value) in enumerate(pairs):
        if code == "0" and value == "SECTION" and idx + 1 < len(pairs) and pairs[idx + 1][1] == "ENTITIES":
            in_entities = True
            continue
        if code == "0" and value == "ENDSEC" and in_entities:
            in_entities = False
            continue
        if not in_entities:
            continue
        if code == "0":
            current = {"type": value}
            entities.append(current)
        elif current is not None:
            current.setdefault(code, []).append(value)
    return entities


def _flatten_arc(cx: float, cy: float, r: float, a0: float, a1: float, sagitta: float = 0.02) -> list[Point]:
    """Chord-flatten an arc so the mid-chord deviation stays under ``sagitta`` mm."""
    if a1 <= a0:
        a1 += 360.0
    sweep = math.radians(a1 - a0)
    if r <= 0 or sweep <= 0:
        return []
    # sagitta = r(1 - cos(dtheta/2))  ->  dtheta = 2 acos(1 - s/r)
    ratio = max(-1.0, min(1.0, 1.0 - sagitta / r))
    dtheta = 2.0 * math.acos(ratio) if r > sagitta else sweep
    n = max(2, int(math.ceil(sweep / max(dtheta, 1e-6))))
    ang = np.radians(np.linspace(a0, a1, n + 1))
    return [(float(cx + r * math.cos(a)), float(cy + r * math.sin(a))) for a in ang]


def entities_to_segments(
    entities: list[dict],
    layers: set[str] | None = None,
    sagitta: float = 0.02,
) -> list[list[Point]]:
    """Flatten supported entities into open polylines (2+ points each)."""
    segments: list[list[Point]] = []
    for e in entities:
        layer = (e.get("8") or ["0"])[0]
        if layers and layer not in layers:
            continue
        try:
            if e["type"] == "LINE":
                p = [
                    (float(e["10"][0]), float(e["20"][0])),
                    (float(e["11"][0]), float(e["21"][0])),
                ]
                if math.dist(p[0], p[1]) < 1e-9:
                    continue
                segments.append(p)
            elif e["type"] == "ARC":
                pts = _flatten_arc(
                    float(e["10"][0]), float(e["20"][0]), float(e["40"][0]),
                    float(e["50"][0]), float(e["51"][0]), sagitta,
                )
                if len(pts) >= 2:
                    segments.append(pts)
            elif e["type"] == "CIRCLE":
                pts = _flatten_arc(
                    float(e["10"][0]), float(e["20"][0]), float(e["40"][0]), 0.0, 360.0, sagitta
                )
                if len(pts) >= 3:
                    segments.append(pts)
            elif e["type"] in ("LWPOLYLINE", "POLYLINE"):
                xs = [float(v) for v in e.get("10", [])]
                ys = [float(v) for v in e.get("20", [])]
                pts = list(zip(xs, ys))
                if len(pts) >= 2:
                    closed = bool(int((e.get("70") or ["0"])[0]) & 1)
                    if closed and math.dist(pts[0], pts[-1]) > 1e-9:
                        pts.append(pts[0])
                    segments.append([(float(a), float(b)) for a, b in pts])
        except (KeyError, IndexError, ValueError):
            continue
    return segments


# ---------------------------------------------------------------------------
# chain stitching
# ---------------------------------------------------------------------------
def _polygon_area(pts: list[Point]) -> float:
    a = np.asarray(pts, dtype=float)
    x, y = a[:, 0], a[:, 1]
    return 0.5 * abs(float(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1))))


def build_loops(
    segments: list[list[Point]], tol: float = 1e-4
) -> tuple[list[list[Point]], list[list[Point]]]:
    """Stitch segments into (closed_loops, open_chains) by shared endpoints."""
    def key(p: Point) -> tuple[int, int]:
        return (round(p[0] / tol), round(p[1] / tol))

    adjacency: dict[tuple[int, int], list[tuple[int, int]]] = defaultdict(list)
    for i, seg in enumerate(segments):
        adjacency[key(seg[0])].append((i, 0))
        adjacency[key(seg[-1])].append((i, 1))

    used = [False] * len(segments)
    closed: list[list[Point]] = []
    open_chains: list[list[Point]] = []

    for i in range(len(segments)):
        if used[i]:
            continue
        chain = list(segments[i])
        used[i] = True

        # Walk forward from the tail: pick the unused segment touching it and
        # orient that segment so it *starts* at the tail.
        while True:
            nxt = next(((j, e) for (j, e) in adjacency[key(chain[-1])] if not used[j]), None)
            if nxt is None:
                break
            j, end = nxt
            used[j] = True
            seg = segments[j] if end == 0 else segments[j][::-1]
            chain += seg[1:]

        # Walk backward from the head: orient the segment so it *ends* at the head.
        while True:
            nxt = next(((j, e) for (j, e) in adjacency[key(chain[0])] if not used[j]), None)
            if nxt is None:
                break
            j, end = nxt
            used[j] = True
            seg = segments[j][::-1] if end == 0 else segments[j]
            chain = seg[:-1] + chain

        if math.dist(chain[0], chain[-1]) < tol * 10:
            closed.append(chain[:-1])  # drop the repeated closing vertex
        else:
            open_chains.append(chain)
    return closed, open_chains


def close_across_seam(
    open_chains: list[list[Point]], circumference: float, tol: float = 1e-3
) -> tuple[list[list[Point]], list[list[Point]]]:
    """Join chains whose ends sit on opposite seams into wrapped closed loops.

    A block straddling ``x = 0`` is drawn as two pieces in a tread plan.  Shift
    the piece at ``x = C`` back by one circumference and it butts against the
    piece at ``x = 0``; the join is accepted only when *both* pairs of endpoints
    line up, so unrelated open geometry is never fused by accident.
    """
    left = [c for c in open_chains if min(p[0] for p in c) < tol]
    right = [c for c in open_chains if max(p[0] for p in c) > circumference - tol]
    left = [c for c in left if c not in right]
    used_right: set[int] = set()
    loops: list[list[Point]] = []
    leftovers: list[list[Point]] = []

    for lc in left:
        l_ends = sorted([lc[0], lc[-1]], key=lambda p: p[1])
        matched = None
        for ri, rc in enumerate(right):
            if ri in used_right:
                continue
            shifted = [(x - circumference, y) for x, y in rc]
            r_ends = sorted([shifted[0], shifted[-1]], key=lambda p: p[1])
            if all(math.dist(a, b) < tol for a, b in zip(l_ends, r_ends)):
                matched = (ri, shifted)
                break
        if matched is None:
            leftovers.append(lc)
            continue
        ri, shifted = matched
        used_right.add(ri)
        if math.dist(lc[-1], shifted[0]) > math.dist(lc[-1], shifted[-1]):
            shifted = shifted[::-1]
        loop = lc + shifted[1:]
        if math.dist(loop[0], loop[-1]) < tol:
            loop = loop[:-1]
        loops.append(loop)

    leftovers += [rc for ri, rc in enumerate(right) if ri not in used_right]
    others = [c for c in open_chains if c not in left and c not in right]
    return loops, leftovers + others


# ---------------------------------------------------------------------------
# assembly
# ---------------------------------------------------------------------------
@dataclass
class BlockDefaults:
    """Depth attributes a 2D tread plan cannot supply, so the caller must.

    Values may be given per zone; ``None`` entries fall back to the flat value.
    Everything set here is recorded as an assumption in ``Pattern.meta``.
    """

    height: float = 8.0  # NSD, mm
    draft_angle: float = 3.0  # deg
    n_lateral_sipes: int = 0
    sipe_depth_fraction: float = 0.6
    shore_a: float | None = None
    height_by_zone: dict[str, float] = field(default_factory=dict)
    draft_by_zone: dict[str, float] = field(default_factory=dict)
    sipes_by_zone: dict[str, int] = field(default_factory=dict)

    def for_zone(self, zone: str) -> dict:
        return {
            "height": self.height_by_zone.get(zone, self.height),
            "draft_angle": self.draft_by_zone.get(zone, self.draft_angle),
            "n_lateral_sipes": self.sipes_by_zone.get(zone, self.n_lateral_sipes),
            "sipe_depth_fraction": self.sipe_depth_fraction,
            "shore_a": self.shore_a,
        }


@dataclass
class ImportReport:
    """What the importer did, so the result can be trusted or questioned."""

    path: str
    n_entities: int
    n_segments: int
    n_closed: int
    n_wrapped: int
    n_discarded_open: int
    n_discarded_small: int
    circumference: float
    tread_width: float
    land_ratio: float
    warnings: list[str] = field(default_factory=list)

    def summary(self) -> str:
        lines = [
            f"DXF import: {os.path.basename(self.path)}",
            f"  {self.n_entities} entities -> {self.n_segments} segments -> "
            f"{self.n_closed + self.n_wrapped} blocks ({self.n_wrapped} wrapped across the seam)",
            f"  extents {self.circumference:.3f} x {self.tread_width:.3f} mm, land ratio {self.land_ratio:.4f}",
        ]
        if self.n_discarded_open or self.n_discarded_small:
            lines.append(
                f"  discarded {self.n_discarded_open} unclosed chains, {self.n_discarded_small} tiny loops"
            )
        lines += [f"  ! {w}" for w in self.warnings]
        return "\n".join(lines)


def load_pattern(
    path: str,
    defaults: BlockDefaults | None = None,
    circumference: float | None = None,
    tread_width: float | None = None,
    crown: CrownProfile | None = None,
    min_block_area: float = 5.0,
    layers: set[str] | None = None,
    name: str | None = None,
    pitch_length: float | None = None,
    n_pitches: int | None = None,
) -> tuple[Pattern, ImportReport]:
    """Read a tread-plan DXF into a :class:`Pattern`.

    ``circumference`` and ``tread_width`` default to the drawing extents, which
    is right for a tread plan drawn to size.  Override either when the drawing
    carries a border or when the true rolling circumference differs from the
    developed plan length.
    """
    defaults = defaults or BlockDefaults()
    _validate_defaults(defaults)
    _validate_options(circumference, tread_width, n_pitches, min_block_area)
    entities = read_dxf_entities(path)
    segments = entities_to_segments(entities, layers=layers)
    warnings: list[str] = []

    if not segments:
        raise ValueError(
            f"no usable LINE/ARC/POLYLINE entities found in {path}. Check that the file "
            "contains the tread outline as geometry (not blocks/xrefs), and that it was "
            "exported as ASCII DXF rather than binary."
        )

    # A malformed coordinate must not reach the extents: one NaN there poisons
    # the circumference, the grid and every number downstream.
    clean = [s for s in segments if all(math.isfinite(x) and math.isfinite(y) for x, y in s)]
    if len(clean) != len(segments):
        warnings.append(f"{len(segments) - len(clean)} entit(ies) had non-finite coordinates and were skipped")
        segments = clean
    if not segments:
        raise ValueError(f"every entity in {path} had non-finite coordinates")

    all_pts = np.asarray([p for s in segments for p in s], dtype=float)
    x_min, x_max = float(all_pts[:, 0].min()), float(all_pts[:, 0].max())
    y_min, y_max = float(all_pts[:, 1].min()), float(all_pts[:, 1].max())
    circ = circumference or (x_max - x_min)
    width = tread_width or (y_max - y_min)
    if not (math.isfinite(circ) and circ > 0 and math.isfinite(width) and width > 0):
        raise ValueError(
            f"DXF extents are degenerate: {circ} x {width} mm. The drawing must span a "
            "positive distance in both directions; a single straight line or a file with "
            "corrupt coordinates will produce this."
        )

    # Drop construction geometry: a lone straight segment running the whole
    # drawing is a centreline or border, never a block edge.  Only look for one
    # when the drawing is big enough to carry block detail too -- on a
    # four-segment drawing every edge spans the extents, and a loose threshold
    # here deletes the only block in the file.
    can_have_construction = len(segments) >= 8
    kept = []
    for s in segments:
        if can_have_construction and len(s) == 2 and math.dist(s[0], s[1]) > 0.9 * circ:
            continue
        kept.append([(x - x_min, y - y_min) for x, y in s])
    if len(kept) != len(segments):
        warnings.append(f"ignored {len(segments) - len(kept)} full-span construction line(s)")
    segments = kept

    closed, open_chains = build_loops(segments)
    wrapped, leftover = close_across_seam(open_chains, circ)
    if leftover:
        warnings.append(
            f"{len(leftover)} chain(s) did not close and were discarded -- the drawing may have "
            "gaps, or geometry outside the tread band"
        )

    loops = closed + wrapped
    small = [c for c in loops if _polygon_area(c) < min_block_area]
    loops = [c for c in loops if _polygon_area(c) >= min_block_area]

    # An import that yields no blocks is a failed import, not an empty tyre.
    # Left to run it produces all-zero charts with nothing to explain them.
    if not loops:
        why = []
        if small:
            why.append(f"{len(small)} closed loop(s) were smaller than the {min_block_area} mm^2 minimum block area")
        if leftover:
            why.append(f"{len(leftover)} outline(s) did not close -- the drawing may have gaps at block corners")
        if not closed and not wrapped:
            why.append("no closed outline could be stitched from the segments at all")
        raise ValueError(
            f"{os.path.basename(path)} imported 0 tread blocks. "
            + (("; ".join(why) + ". ") if why else "")
            + "Check that the tread outlines are on the imported layer, that corners meet "
            "exactly, and that the drawing units are millimetres."
        )

    half = width / 2.0
    blocks: list[Block] = []
    for i, loop in enumerate(sorted(loops, key=lambda c: min(p[0] for p in c))):
        poly = [(float(x), float(y - half)) for x, y in loop]
        cy = float(np.mean([p[1] for p in poly]))
        zone = classify_zone(abs(cy), width)
        attrs = defaults.for_zone(zone)
        blocks.append(Block(id=f"D{i:04d}", pitch_id="", polygon=poly, zone=zone, **attrs))

    pitches = _infer_pitches(blocks, circ, pitch_length, n_pitches)
    geometric_repeat = _geometric_repeat(blocks, circ)
    if geometric_repeat and abs(geometric_repeat - pitches[0].circumferential_length) > 0.01:
        warnings.append(
            f"pitch spacing {pitches[0].circumferential_length:.3f} mm is a sub-multiple of the "
            f"{geometric_repeat:.3f} mm geometric repeat -- lateral bands are staggered, so blocks "
            "strike more often than the drawing repeats"
        )
    for b in blocks:
        cx = float(np.mean([p[0] for p in b.polygon])) % circ
        b.pitch_id = next(
            (p.id for p in pitches if p.circumferential_start <= cx < p.circumferential_end),
            pitches[-1].id if pitches else "",
        )

    # A loop lying inside another is usually a hole (a dimple, a stone ejector,
    # an inner detail line), not a second block.  The sweep is unaffected -- the
    # rasteriser writes land pixels idempotently, so it measures the union -- but
    # the land ratio below sums polygon areas and would double-count it.
    nested = _count_nested_loops(blocks)
    if nested:
        warnings.append(
            f"{nested} outline(s) lie inside another outline and are being counted as separate "
            "blocks; if they are holes rather than blocks the land ratio is an over-estimate "
            "(the theta sweep itself measures the union and is unaffected)"
        )

    land = sum(b.area() for b in blocks)
    uniform = _pitch_lengths_are_uniform(blocks, circ, geometric_repeat)
    if uniform:
        warnings.append(
            "every repeat is geometrically identical -- this drawing carries no pitch modulation. "
            "If the production mould modulates the pitch sequence, supply it, or the order analysis "
            "will show the single-order tone of a uniform array rather than the real tyre"
        )
    report = ImportReport(
        path=path,
        n_entities=len(entities),
        n_segments=len(segments),
        n_closed=len(closed),
        n_wrapped=len(wrapped),
        n_discarded_open=len(leftover),
        n_discarded_small=len(small),
        circumference=circ,
        tread_width=width,
        land_ratio=land / (circ * width),
        warnings=warnings,
    )

    pattern = Pattern(
        tyre_circumference=circ,
        tread_width=width,
        pitches=pitches,
        blocks=blocks,
        crown_radius_profile=crown or CrownProfile.dual_radius(width),
        name=name or os.path.splitext(os.path.basename(path))[0],
        source="dxf",
        meta={
            "dxf_path": os.path.abspath(path),
            "geometric_repeat_mm": geometric_repeat,
            "uniform_array": uniform,
            "import": {
                "n_entities": len(entities),
                "n_blocks": len(blocks),
                "n_wrapped": len(wrapped),
                "n_discarded_open": len(leftover),
                "warnings": warnings,
            },
            # Everything below came from the caller, not from the drawing.
            "assumed": {
                "height_mm": defaults.height,
                "height_by_zone": defaults.height_by_zone,
                "draft_deg": defaults.draft_angle,
                "draft_by_zone": defaults.draft_by_zone,
                "n_lateral_sipes": defaults.n_lateral_sipes,
                "sipes_by_zone": defaults.sipes_by_zone,
                "crown_profile": "measured" if (crown and crown.measured) else "dual-radius guess",
            },
        },
    )
    return pattern, report


def _validate_defaults(d: BlockDefaults) -> None:
    """Reject depth attributes that would silently zero every stiffness."""
    heights = [d.height, *d.height_by_zone.values()]
    for h in heights:
        if not math.isfinite(h) or h <= 0:
            raise ValueError(f"NSD (block height) must be a positive number of mm, got {h!r}")
    for a in [d.draft_angle, *d.draft_by_zone.values()]:
        if not math.isfinite(a) or abs(a) >= 90.0:
            raise ValueError(
                f"draft angle must be a finite angle strictly between -90 and 90 degrees, got {a!r}"
            )
    for n in [d.n_lateral_sipes, *d.sipes_by_zone.values()]:
        if int(n) != n or n < 0:
            raise ValueError(f"lateral sipe count must be a non-negative whole number, got {n!r}")
        if n > 50:
            raise ValueError(f"lateral sipe count of {n} is implausible for a tread block (limit 50)")
    if not math.isfinite(d.sipe_depth_fraction) or not 0.0 <= d.sipe_depth_fraction <= 1.0:
        raise ValueError(
            f"sipe depth fraction must lie between 0 and 1 (fraction of NSD), got {d.sipe_depth_fraction!r}"
        )


def _validate_options(
    circumference: float | None, tread_width: float | None,
    n_pitches: int | None, min_block_area: float,
) -> None:
    for name, v in (("circumference", circumference), ("tread width", tread_width)):
        if v is not None and (not math.isfinite(v) or v <= 0):
            raise ValueError(f"{name} override must be a positive number of mm, got {v!r}")
    if n_pitches is not None and n_pitches != 0:
        if int(n_pitches) != n_pitches or n_pitches < 1:
            raise ValueError(f"pitch count must be a whole number >= 1, got {n_pitches!r}")
        if n_pitches > 5000:
            raise ValueError(f"pitch count of {n_pitches} is implausible (limit 5000)")
    if not math.isfinite(min_block_area) or min_block_area < 0:
        raise ValueError(f"minimum block area must be a non-negative number of mm^2, got {min_block_area!r}")


def _point_in_polygon(pt: tuple[float, float], poly: list[Point]) -> bool:
    x, y = pt
    inside = False
    n = len(poly)
    for i in range(n):
        ax, ay = poly[i]
        bx, by = poly[(i + 1) % n]
        if ay == by:
            continue
        if (ay > y) != (by > y):
            xc = (bx - ax) * (y - ay) / (by - ay) + ax
            if x < xc:
                inside = not inside
    return inside


def _count_nested_loops(blocks: list[Block]) -> int:
    """How many block outlines sit wholly inside another one.

    Bounding boxes are compared first so this stays cheap for a few hundred
    blocks; only survivors get the point-in-polygon test.
    """
    boxes, cents = [], []
    for b in blocks:
        a = np.asarray(b.polygon, dtype=float)
        boxes.append((a[:, 0].min(), a[:, 0].max(), a[:, 1].min(), a[:, 1].max()))
        cents.append(b.centroid())
    n = 0
    for i, bi in enumerate(boxes):
        for j, bj in enumerate(boxes):
            if i == j:
                continue
            if bi[0] < bj[0] or bi[1] > bj[1] or bi[2] < bj[2] or bi[3] > bj[3]:
                continue
            if (bi[1] - bi[0]) >= (bj[1] - bj[0]) and (bi[3] - bi[2]) >= (bj[3] - bj[2]):
                continue
            if _point_in_polygon(cents[i], blocks[j].polygon):
                n += 1
                break
    return n


def _geometric_repeat(blocks: list[Block], circumference: float, tol: float = 0.05) -> float | None:
    """Smallest circumferential shift that maps the whole block layout onto itself.

    This is the drawing's true repeat unit, which is not the same thing as the
    pitch spacing when lateral bands are staggered: the density of block strikes
    can repeat twice as often as the geometry does.
    """
    if len(blocks) < 4:
        return None
    pts = np.array(
        [[float(np.mean([p[0] for p in b.polygon])) % circumference,
          float(np.mean([p[1] for p in b.polygon]))] for b in blocks]
    )
    # The repeat must divide the circumference a whole number of times, so test
    # C/n directly.  Walking n downward returns the largest repeat unit that
    # works, which is the fundamental -- C/24 would also "work" for a pattern
    # that repeats every C/12 only if the staggered bands genuinely coincide,
    # and for this family they do not.
    best: float | None = None
    for n in range(2, min(201, len(blocks) + 1)):
        shift = circumference / n
        moved = pts.copy()
        moved[:, 0] = (moved[:, 0] + shift) % circumference
        if _point_sets_match(pts, moved, tol):
            best = shift
    return best


def _point_sets_match(a: np.ndarray, b: np.ndarray, tol: float) -> bool:
    """True when every point of b has a partner in a within tol."""
    for p in b:
        d = np.hypot(a[:, 0] - p[0], a[:, 1] - p[1])
        if float(d.min()) > tol:
            return False
    return True


def _pitch_lengths_are_uniform(blocks: list[Block], circumference: float, repeat: float | None) -> bool:
    """True when the drawing is a plain array with no pitch modulation."""
    if not repeat:
        return False
    n = circumference / repeat
    return abs(n - round(n)) < 1e-3 and round(n) >= 2


def _infer_pitches(
    blocks: list[Block],
    circumference: float,
    pitch_length: float | None,
    n_pitches: int | None,
) -> list[Pitch]:
    """Recover the pitch sequence from block positions.

    A tread plan does not label its pitches.  With an explicit pitch length or
    count the split is exact; otherwise the repeat is estimated from the
    circumferential spectrum of block-centroid density, which peaks at the pitch
    order for any pattern with a regular repeat.
    """
    if n_pitches and n_pitches > 0:
        length = circumference / n_pitches
    elif pitch_length and pitch_length > 0:
        length = pitch_length
        n_pitches = max(1, int(round(circumference / length)))
    else:
        n_pitches = _estimate_pitch_count(blocks, circumference)
        length = circumference / n_pitches

    return [
        Pitch(id=f"P{i:03d}", circumferential_start=i * length, circumferential_length=length)
        for i in range(int(n_pitches))
    ]


def _estimate_pitch_count(blocks: list[Block], circumference: float, max_order: int = 200) -> int:
    """Fundamental circumferential repeat order of the block layout.

    A periodic layout puts energy on the repeat order *and all its harmonics*,
    and for a crisp pattern a harmonic is often the tallest bar -- so taking the
    spectral peak alone returns a multiple of the true pitch count.  Take the
    greatest common divisor of the strong peaks instead, which lands on the
    fundamental.
    """
    if not blocks:
        return 1
    n = 4096
    density = np.zeros(n)
    for b in blocks:
        cx = float(np.mean([p[0] for p in b.polygon])) % circumference
        density[int(cx / circumference * n) % n] += b.area()
    spec = np.abs(np.fft.rfft(density - density.mean()))[: max_order + 1]
    if spec.size < 2:
        return 1
    body = spec[1:]
    peak = float(body.max())
    if peak <= 0:
        return 1
    strong = [i + 1 for i, v in enumerate(body) if v > 0.35 * peak]
    fundamental = strong[0]
    for order in strong[1:]:
        fundamental = math.gcd(fundamental, order)
    return max(1, fundamental)
