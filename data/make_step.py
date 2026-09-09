#!/usr/bin/env python3
"""Generate the STEP fixtures used to test tread-surface land/sea measurement.

Every file here has a land area that is known EXACTLY by hand, printed when the
file is written, so the reader can be checked against arithmetic rather than
against its own previous output.

  step_plate_holes.step   one flat top face 100 x 60 with two rectangular voids
                          and one circular void cut through it -- the ordinary
                          case, a face with holes
  step_blocks.step        the same land, drawn instead as separate block-top
                          faces with no holes, which is how most CAD kernels
                          actually emit a tread
  step_band_cyl.step      a cylindrical tread band, R = 300, 30 degrees of arc,
                          200 wide, with three lateral grooves -- the case that
                          has to be developed before it can be measured
  step_spline_edge.step   a rectangle whose right-hand edge is a cubic B-spline
                          with collinear control points, so it IS a straight
                          line and the area stays exact: a de Boor check with a
                          closed form
  step_inch.step          step_plate_holes in inches, so the unit context has
                          to be read and applied
  step_toroidal.step      one planar face and one on a torus, which does not
                          develop -- it must be reported, not silently dropped

Run:  python3 data/make_step.py
"""
import math
import os

HERE = os.path.dirname(os.path.abspath(__file__))


class Step:
    """A minimal ISO 10303-21 writer.

    Entities are appended in order and referred to by the ``#n`` id returned
    when they are added, which is all the B-rep below needs.
    """

    def __init__(self, name):
        self.name = name
        self.lines = []
        self.n = 0

    def add(self, text):
        self.n += 1
        self.lines.append("#%d = %s;" % (self.n, text))
        return "#%d" % self.n

    # -- geometry primitives ------------------------------------------------
    def pt(self, x, y, z):
        return self.add("CARTESIAN_POINT('',(%s,%s,%s))" % (num(x), num(y), num(z)))

    def dirn(self, x, y, z):
        return self.add("DIRECTION('',(%s,%s,%s))" % (num(x), num(y), num(z)))

    def a2p(self, origin, axis, ref):
        return self.add("AXIS2_PLACEMENT_3D('',%s,%s,%s)"
                        % (self.pt(*origin), self.dirn(*axis), self.dirn(*ref)))

    def vertex(self, p):
        return self.add("VERTEX_POINT('',%s)" % self.pt(*p))

    def line_edge(self, v0, v1, p0, p1):
        """An EDGE_CURVE whose geometry is a LINE from p0 towards p1."""
        d = [p1[i] - p0[i] for i in range(3)]
        L = math.sqrt(sum(c * c for c in d)) or 1.0
        vec = self.add("VECTOR('',%s,%s)" % (self.dirn(*[c / L for c in d]), num(L)))
        ln = self.add("LINE('',%s,%s)" % (self.pt(*p0), vec))
        return self.add("EDGE_CURVE('',%s,%s,%s,.T.)" % (v0, v1, ln))

    def arc_edge(self, v0, v1, centre, axis, ref, radius):
        """An EDGE_CURVE on a CIRCLE, swept counter-clockwise about `axis`."""
        circ = self.add("CIRCLE('',%s,%s)" % (self.a2p(centre, axis, ref), num(radius)))
        return self.add("EDGE_CURVE('',%s,%s,%s,.T.)" % (v0, v1, circ))

    def loop(self, edges):
        """`edges` are either refs (traversed forwards) or (ref, forward) pairs.

        A loop that runs an edge backwards says so with ORIENTED_EDGE .F. --
        reversing the EDGE_CURVE's own vertices instead would leave the loop
        self-crossing, and its area meaningless.
        """
        oriented = []
        for e in edges:
            ref, fwd = e if isinstance(e, tuple) else (e, True)
            oriented.append(self.add("ORIENTED_EDGE('',*,*,%s,%s)"
                                     % (ref, ".T." if fwd else ".F.")))
        return self.add("EDGE_LOOP('',(%s))" % ",".join(oriented))

    def polygon_loop(self, pts, z=None):
        """A closed EDGE_LOOP through `pts`, straight edges throughout."""
        pts3 = [(p[0], p[1], z if z is not None else p[2]) for p in pts]
        vs = [self.vertex(p) for p in pts3]
        edges = []
        for i in range(len(pts3)):
            j = (i + 1) % len(pts3)
            edges.append(self.line_edge(vs[i], vs[j], pts3[i], pts3[j]))
        return self.loop(edges)

    def circle_loop(self, cx, cy, z, r):
        """A closed EDGE_LOOP of two half-circles -- how CAD usually emits one."""
        a = self.vertex((cx + r, cy, z))
        b = self.vertex((cx - r, cy, z))
        e1 = self.arc_edge(a, b, (cx, cy, z), (0, 0, 1), (1, 0, 0), r)
        e2 = self.arc_edge(b, a, (cx, cy, z), (0, 0, 1), (1, 0, 0), r)
        return self.loop([e1, e2])

    def plane_face(self, z, outer, inners=(), normal=(0, 0, 1), ref=(1, 0, 0)):
        surf = self.add("PLANE('',%s)" % self.a2p((0, 0, z), normal, ref))
        bounds = [self.add("FACE_OUTER_BOUND('',%s,.T.)" % outer)]
        bounds += [self.add("FACE_BOUND('',%s,.T.)" % h) for h in inners]
        return self.add("ADVANCED_FACE('',(%s),%s,.T.)" % (",".join(bounds), surf))

    # -- file ---------------------------------------------------------------
    def write(self, path, faces, inch=False):
        shell = self.add("OPEN_SHELL('',(%s))" % ",".join(faces))
        self.add("SHELL_BASED_SURFACE_MODEL('%s',(%s))" % (self.name, shell))
        head = [
            "ISO-10303-21;",
            "HEADER;",
            "FILE_DESCRIPTION((''),'2;1');",
            "FILE_NAME('%s','2026-01-01T00:00:00',(''),(''),'','','');" % self.name,
            "FILE_SCHEMA(('AUTOMOTIVE_DESIGN { 1 0 10303 214 1 1 1 1 }'));",
            "ENDSEC;",
            "DATA;",
        ]
        body = list(self.lines)
        # The unit context goes last so its ids do not disturb the geometry.
        n = self.n
        ctx = []

        def emit(text):
            nonlocal n
            n += 1
            ctx.append("#%d = %s;" % (n, text))
            return "#%d" % n

        mm = emit("( LENGTH_UNIT() NAMED_UNIT(*) SI_UNIT(.MILLI.,.METRE.) )")
        if inch:
            expo = emit("DIMENSIONAL_EXPONENTS(1.,0.,0.,0.,0.,0.,0.)")
            meas = emit("LENGTH_MEASURE_WITH_UNIT(LENGTH_MEASURE(25.4),%s)" % mm)
            length = emit("( CONVERSION_BASED_UNIT('INCH',%s) LENGTH_UNIT() NAMED_UNIT(%s) )"
                          % (meas, expo))
        else:
            length = mm
        ang = emit("( NAMED_UNIT(*) PLANE_ANGLE_UNIT() SI_UNIT($,.RADIAN.) )")
        sol = emit("( NAMED_UNIT(*) SI_UNIT($,.STERADIAN.) SOLID_ANGLE_UNIT() )")
        unc = emit("UNCERTAINTY_MEASURE_WITH_UNIT(LENGTH_MEASURE(1.E-07),%s,"
                   "'distance_accuracy_value','confusion accuracy')" % length)
        emit("( GEOMETRIC_REPRESENTATION_CONTEXT(3) "
             "GLOBAL_UNCERTAINTY_ASSIGNED_CONTEXT((%s)) "
             "GLOBAL_UNIT_ASSIGNED_CONTEXT((%s,%s,%s)) REPRESENTATION_CONTEXT('','') )"
             % (unc, length, ang, sol))

        tail = ["ENDSEC;", "END-ISO-10303-21;"]
        with open(path, "w") as fh:
            fh.write("\n".join(head + body + ctx + tail) + "\n")
        return path


def num(v):
    """A Part 21 real: it must carry a decimal point."""
    if isinstance(v, int):
        return "%d." % v
    s = repr(float(v))
    return s if ("." in s or "E" in s or "e" in s) else s + "."


def rect(x0, y0, x1, y1):
    return [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]


# ---------------------------------------------------------------------------
# the fixtures
# ---------------------------------------------------------------------------

PLATE_W, PLATE_H = 100.0, 60.0
TOP_Z, BOT_Z = 10.0, 2.0          # so the groove is 8 mm deep
VOIDS = [(10.0, 10.0, 30.0, 20.0), (60.0, 35.0, 80.0, 45.0)]   # 20x10 each
HOLE = (50.0, 20.0, 5.0)           # cx, cy, r


def plate_land():
    a = PLATE_W * PLATE_H
    for x0, y0, x1, y1 in VOIDS:
        a -= (x1 - x0) * (y1 - y0)
    a -= math.pi * HOLE[2] ** 2
    return a


def build_plate_holes(path, inch=False):
    """One top face with the voids as inner bounds, plus a groove floor below."""
    s = Step("plate_holes")
    k = 1.0 / 25.4 if inch else 1.0
    outer = s.polygon_loop([(x * k, y * k) for x, y in rect(0, 0, PLATE_W, PLATE_H)], z=TOP_Z * k)
    inners = [s.polygon_loop([(x * k, y * k) for x, y in rect(*v)], z=TOP_Z * k) for v in VOIDS]
    inners.append(s.circle_loop(HOLE[0] * k, HOLE[1] * k, TOP_Z * k, HOLE[2] * k))
    top = s.plane_face(TOP_Z * k, outer, inners)
    # The groove floor: one face per void, at the bottom of the groove. Its
    # depth below the top face is what makes the NSD measurable.
    floors = []
    for x0, y0, x1, y1 in VOIDS:
        floors.append(s.plane_face(BOT_Z * k,
                                   s.polygon_loop([(x * k, y * k) for x, y in rect(x0, y0, x1, y1)],
                                                  z=BOT_Z * k)))
    floors.append(s.plane_face(BOT_Z * k, s.circle_loop(HOLE[0] * k, HOLE[1] * k, BOT_Z * k, HOLE[2] * k)))
    return s.write(path, [top] + floors, inch=inch)


def build_blocks(path):
    """The same land as separate block faces -- no holes anywhere."""
    s = Step("blocks")
    # Cut the plate into a grid of blocks whose total area is the same as the
    # holed plate would give if the voids were full-width. Kept simple and
    # exact: five bands separated by four 4 mm lateral grooves.
    faces, land = [], 0.0
    gap, n = 4.0, 5
    band = (PLATE_H - (n - 1) * gap) / n
    for i in range(n):
        y0 = i * (band + gap)
        faces.append(s.plane_face(TOP_Z, s.polygon_loop(rect(0, y0, PLATE_W, y0 + band), z=TOP_Z)))
        land += PLATE_W * band
    floor = s.plane_face(BOT_Z, s.polygon_loop(rect(0, 0, PLATE_W, PLATE_H), z=BOT_Z))
    s.write(path, faces + [floor])
    return path, land


CYL_R, CYL_W = 300.0, 200.0
CYL_ARC_DEG = 30.0
CYL_GROOVES = [(30.0, 45.0), (80.0, 95.0), (140.0, 152.0)]     # axial bands cut away


def build_band_cyl(path):
    """A cylindrical tread band with lateral grooves.

    The cylinder's axis is x, so a point at angle t and axial position u is
    (u, R sin t, R cos t). Developed, the face is (R*t) by u -- and the area in
    that development is the true surface area, because a cylinder is
    developable.
    """
    s = Step("band_cyl")
    surf = s.add("CYLINDRICAL_SURFACE('',%s,%s)"
                 % (s.a2p((0, 0, 0), (1, 0, 0), (0, 0, 1)), num(CYL_R)))
    t1 = math.radians(CYL_ARC_DEG)

    def p(u, t):
        return (u, CYL_R * math.sin(t), CYL_R * math.cos(t))

    # Land runs the full arc; the grooves cut across it axially.
    bands, prev = [], 0.0
    for g0, g1 in CYL_GROOVES:
        bands.append((prev, g0))
        prev = g1
    bands.append((prev, CYL_W))

    faces, land = [], 0.0
    for u0, u1 in bands:
        if u1 - u0 <= 0:
            continue
        corners = [(u0, 0.0), (u1, 0.0), (u1, t1), (u0, t1)]
        vs = [s.vertex(p(u, t)) for u, t in corners]
        edges = []
        for i in range(4):
            j = (i + 1) % 4
            (ua, ta), (ub, tb) = corners[i], corners[j]
            if abs(ta - tb) < 1e-12:
                edges.append(s.line_edge(vs[i], vs[j], p(ua, ta), p(ub, tb)))
            else:
                # A constant-u edge on a cylinder is a circular arc about the
                # axis, centred on the axis at that u. The CIRCLE is always
                # emitted counter-clockwise; a loop that needs it the other way
                # round flags the ORIENTED_EDGE instead.
                # The circle's axis is -x, not +x: with ref = +z that makes
                # INCREASING t the counter-clockwise direction in the circle's
                # own frame, which is the direction Part 21 parameterises it in.
                # With +x it would run the other way and every "forward" arc
                # would sweep 330 degrees instead of 30.
                ccw = tb > ta
                a, b = (vs[i], vs[j]) if ccw else (vs[j], vs[i])
                edges.append((s.arc_edge(a, b, (ua, 0, 0), (-1, 0, 0), (0, 0, 1), CYL_R), ccw))
        bnd = s.add("FACE_OUTER_BOUND('',%s,.T.)" % s.loop(edges))
        faces.append(s.add("ADVANCED_FACE('',(%s),%s,.T.)" % (bnd, surf)))
        land += (u1 - u0) * CYL_R * t1
    s.write(path, faces)
    return path, land, CYL_R * t1 * CYL_W


SPL_W, SPL_H = 40.0, 25.0


def build_spline_edge(path):
    """A rectangle whose right edge is a CUBIC B-spline with collinear control
    points. A B-spline through collinear controls IS the straight segment, so
    the area stays exactly SPL_W * SPL_H -- which turns de Boor evaluation into
    a check against a closed form rather than against a picture."""
    s = Step("spline_edge")
    z = TOP_Z
    corners = [(0.0, 0.0), (SPL_W, 0.0), (SPL_W, SPL_H), (0.0, SPL_H)]
    vs = [s.vertex((x, y, z)) for x, y in corners]
    edges = [s.line_edge(vs[0], vs[1], (0, 0, z), (SPL_W, 0, z))]
    # the spline edge, bottom-right up to top-right
    ctrl = [(SPL_W, SPL_H * f) for f in (0.0, 1 / 3, 2 / 3, 1.0)]
    cps = [s.pt(x, y, z) for x, y in ctrl]
    spl = s.add("B_SPLINE_CURVE_WITH_KNOTS('',3,(%s),.UNSPECIFIED.,.F.,.F.,"
                "(4,4),(0.,1.),.UNSPECIFIED.)" % ",".join(cps))
    edges.append(s.add("EDGE_CURVE('',%s,%s,%s,.T.)" % (vs[1], vs[2], spl)))
    edges.append(s.line_edge(vs[2], vs[3], (SPL_W, SPL_H, z), (0, SPL_H, z)))
    edges.append(s.line_edge(vs[3], vs[0], (0, SPL_H, z), (0, 0, z)))
    bnd = s.add("FACE_OUTER_BOUND('',%s,.T.)" % s.loop(edges))
    surf = s.add("PLANE('',%s)" % s.a2p((0, 0, z), (0, 0, 1), (1, 0, 0)))
    face = s.add("ADVANCED_FACE('',(%s),%s,.T.)" % (bnd, surf))
    s.write(path, [face])
    return path, SPL_W * SPL_H


def build_toroidal(path):
    """One measurable planar face and one on a torus, which does not develop."""
    s = Step("toroidal")
    flat = s.plane_face(TOP_Z, s.polygon_loop(rect(0, 0, 50, 40), z=TOP_Z))
    tor = s.add("TOROIDAL_SURFACE('',%s,%s,%s)"
                % (s.a2p((0, 0, 0), (0, 0, 1), (1, 0, 0)), num(40.0), num(5.0)))
    loop = s.polygon_loop(rect(0, 0, 10, 10), z=0.0)
    bnd = s.add("FACE_OUTER_BOUND('',%s,.T.)" % loop)
    bad = s.add("ADVANCED_FACE('',(%s),%s,.T.)" % (bnd, tor))
    s.write(path, [flat, bad])
    return path, 50.0 * 40.0


if __name__ == "__main__":
    p = build_plate_holes(os.path.join(HERE, "step_plate_holes.step"))
    print("%-24s land %12.4f mm^2  (envelope %.1f, %d faces)"
          % (os.path.basename(p), plate_land(), PLATE_W * PLATE_H, 1 + len(VOIDS) + 1))

    p, land = build_blocks(os.path.join(HERE, "step_blocks.step"))
    print("%-24s land %12.4f mm^2  (envelope %.1f)"
          % (os.path.basename(p), land, PLATE_W * PLATE_H))

    p, land, env = build_band_cyl(os.path.join(HERE, "step_band_cyl.step"))
    print("%-24s land %12.4f mm^2  (developed envelope %.4f)"
          % (os.path.basename(p), land, env))

    p, land = build_spline_edge(os.path.join(HERE, "step_spline_edge.step"))
    print("%-24s land %12.4f mm^2" % (os.path.basename(p), land))

    p = build_plate_holes(os.path.join(HERE, "step_inch.step"), inch=True)
    print("%-24s land %12.4f mm^2  (written in inches)"
          % (os.path.basename(p), plate_land()))

    p, land = build_toroidal(os.path.join(HERE, "step_toroidal.step"))
    print("%-24s land %12.4f mm^2  (plus one torus face that cannot be measured)"
          % (os.path.basename(p), land))
