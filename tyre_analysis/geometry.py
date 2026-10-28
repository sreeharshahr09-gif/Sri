"""Per-component geometry from flattened closed loops.

Covers area/centroid (shapely), boundary-curve centroid (arc-length weighted,
distinct from the area centroid -- needed for Pappus's 2nd theorem), and the
``integral r^3 dA`` moment used for the mass polar moment of inertia of the
revolved solid.

Sign handling: areas are always taken as ``abs`` and polygons are re-oriented
to a canonical winding before any moment integral, because input DXF curves are
not guaranteed CW or CCW. A winding-order sign flip corrupted an Ixy result in
a related tool once; we do not repeat it here.
"""

from __future__ import annotations

from dataclasses import dataclass

from shapely.geometry import Polygon, MultiPolygon
from shapely.geometry.polygon import orient


@dataclass
class ComponentGeometry:
    layer: str
    area_mm2: float
    centroid_x: float
    centroid_y: float
    boundary_length_mm: float
    boundary_centroid_x: float
    boundary_centroid_y: float
    r3_moment_mm5: float          # integral of |x - axis_x|^3 dA
    n_holes: int
    polygon: Polygon | MultiPolygon

    def area_centroid_distance(self, axis_x: float) -> float:
        return abs(self.centroid_x - axis_x)

    def boundary_centroid_distance(self, axis_x: float) -> float:
        return abs(self.boundary_centroid_x - axis_x)


def build_polygon(loops: list[list[tuple[float, float]]]) -> Polygon | MultiPolygon:
    """Combine one or more closed loops on a layer into a polygon.

    A single loop -> Polygon. Multiple loops -> the largest becomes a shell and
    any loop contained within it becomes a hole; disjoint loops become separate
    parts of a MultiPolygon. This gives shapely-native hole support when one
    component nests inside another (e.g. a bead bundle void).
    """
    raw = [Polygon(l) for l in loops if len(l) >= 4]
    raw = [p for p in raw if p.is_valid and p.area > 0]
    if not raw:
        raise ValueError("No valid polygon could be built from the supplied loops")
    if len(raw) == 1:
        return orient(raw[0], sign=1.0)

    raw.sort(key=lambda p: p.area, reverse=True)
    shells: list[Polygon] = []
    holes: dict[int, list] = {}
    for poly in raw:
        placed = False
        for idx, shell in enumerate(shells):
            if shell.contains(poly.representative_point()):
                holes.setdefault(idx, []).append(poly.exterior.coords[:])
                placed = True
                break
        if not placed:
            shells.append(poly)

    parts = []
    for idx, shell in enumerate(shells):
        parts.append(Polygon(shell.exterior.coords[:], holes.get(idx, [])))
    poly = parts[0] if len(parts) == 1 else MultiPolygon(parts)
    return orient(poly, sign=1.0) if isinstance(poly, Polygon) else poly


def _iter_rings(polygon: Polygon | MultiPolygon):
    """Yield (coords, is_hole) for every ring, oriented canonically."""
    polys = [polygon] if isinstance(polygon, Polygon) else list(polygon.geoms)
    for p in polys:
        p = orient(p, sign=1.0)
        yield p.exterior.coords[:], False
        for interior in p.interiors:
            yield interior.coords[:], True


def boundary_centroid(polygon: Polygon | MultiPolygon) -> tuple[float, float, float]:
    """Arc-length-weighted centroid of the boundary curve and its total length.

    Returns ``(cx, cy, total_length)``. This is the centroid of the *curve*
    (each segment weighted by its length, located at its midpoint) -- the
    quantity Pappus's 2nd theorem needs -- not the area centroid.
    """
    sum_len = 0.0
    sum_x = 0.0
    sum_y = 0.0
    for coords, _hole in _iter_rings(polygon):
        for (x0, y0), (x1, y1) in zip(coords[:-1], coords[1:]):
            seg = ((x1 - x0) ** 2 + (y1 - y0) ** 2) ** 0.5
            if seg == 0:
                continue
            sum_len += seg
            sum_x += seg * (x0 + x1) * 0.5
            sum_y += seg * (y0 + y1) * 0.5
    if sum_len == 0:
        c = polygon.centroid
        return c.x, c.y, 0.0
    return sum_x / sum_len, sum_y / sum_len, sum_len


def r3_area_moment(polygon: Polygon | MultiPolygon, axis_x: float) -> float:
    """Integral of ``|x - axis_x|^3 dA`` over the (holed) region.

    Computed by Green's theorem as a boundary line integral:
    ``integral u^3 dA = contour integral (u^4 / 4) dy`` with ``u = x - axis_x``.
    Holes subtract automatically because their rings carry opposite winding.
    The per-edge integral uses the exact closed form for
    ``integral_0^1 (u0 + t*du)^4 dt``.
    """
    total = 0.0
    for coords, is_hole in _iter_rings(polygon):
        ring_sum = 0.0
        for (x0, y0), (x1, y1) in zip(coords[:-1], coords[1:]):
            u0 = x0 - axis_x
            u1 = x1 - axis_x
            dy = y1 - y0
            du = u1 - u0
            if abs(du) < 1e-14:
                edge_int = u0 ** 4
            else:
                edge_int = (u1 ** 5 - u0 ** 5) / (5.0 * du)
            ring_sum += dy * edge_int / 4.0
        total += ring_sum
    # Canonical orientation (shell CCW, holes CW) makes `total` the region
    # integral with holes removed; abs guards against any residual sign issue.
    return abs(total)


def axis_crossing(polygon: Polygon | MultiPolygon, axis_x: float, tol: float = 1e-9):
    """Check that the whole region lies on one side of the rotation axis.

    Pappus's theorems require the region not to cross the axis. Returns
    ``(crosses: bool, min_dx: float, max_dx: float)`` where dx = x - axis_x.
    """
    xs = []
    for coords, _hole in _iter_rings(polygon):
        xs.extend(x for x, _ in coords)
    min_dx = min(xs) - axis_x
    max_dx = max(xs) - axis_x
    crosses = (min_dx < -tol) and (max_dx > tol)
    return crosses, min_dx, max_dx


def geometry_from_polygon(
    name: str, polygon: Polygon | MultiPolygon, axis_x: float
) -> ComponentGeometry:
    """Build a :class:`ComponentGeometry` from an already-assembled polygon.

    Used by the manual loop builder, which produces shapely polygons directly
    (by unioning picked faces) rather than from per-layer DXF loops.
    """
    if isinstance(polygon, Polygon):
        polygon = orient(polygon, sign=1.0)
    c = polygon.centroid
    bcx, bcy, blen = boundary_centroid(polygon)
    n_holes = (
        len(polygon.interiors)
        if isinstance(polygon, Polygon)
        else sum(len(p.interiors) for p in polygon.geoms)
    )
    return ComponentGeometry(
        layer=name,
        area_mm2=abs(polygon.area),
        centroid_x=c.x,
        centroid_y=c.y,
        boundary_length_mm=blen,
        boundary_centroid_x=bcx,
        boundary_centroid_y=bcy,
        r3_moment_mm5=r3_area_moment(polygon, axis_x),
        n_holes=n_holes,
        polygon=polygon,
    )


def compute_geometry(
    layer: str, loops: list[list[tuple[float, float]]], axis_x: float
) -> ComponentGeometry:
    polygon = build_polygon(loops)
    c = polygon.centroid
    bcx, bcy, blen = boundary_centroid(polygon)
    n_holes = (
        len(polygon.interiors)
        if isinstance(polygon, Polygon)
        else sum(len(p.interiors) for p in polygon.geoms)
    )
    return ComponentGeometry(
        layer=layer,
        area_mm2=abs(polygon.area),
        centroid_x=c.x,
        centroid_y=c.y,
        boundary_length_mm=blen,
        boundary_centroid_x=bcx,
        boundary_centroid_y=bcy,
        r3_moment_mm5=r3_area_moment(polygon, axis_x),
        n_holes=n_holes,
        polygon=polygon,
    )
