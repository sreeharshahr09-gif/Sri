#!/usr/bin/env python3
"""Generate the DXF fixtures used to test HATCH tie-bar import.

A designer marking tie bars on a tread plan colours them in.  These files put
that in front of the importer in every shape it will actually meet:

  hatch_tiebars.dxf      four ribs, three grooves, tie bars drawn as SOLID
                         HATCHes on a TIEBAR layer.  Two are polyline-boundary
                         hatches, two are edge-list hatches (line edges), one
                         carries a rectangular hole, one is coloured by entity
                         ACI and one by true colour, the rest inherit the
                         layer's colour.  The bars are ALSO drawn as linework,
                         so the automatic detector finds them too and the merge
                         has something to merge.
  hatch_only.dxf         the same tread with the bars hatched but NOT drawn as
                         linework, so nothing but the hatch identifies them.
                         This is the case the geometric detector cannot do.
  hatch_pitch.dxf        one pitch, with one hatched tie bar in it, for the
                         replication path -- a bar drawn once must appear once
                         per pitch on the finished tread.
  hatch_arc.dxf          one hatch whose boundary is an edge list of two lines
                         and two arcs (a stadium), to exercise the arc edge and
                         the direction flag.

Run:  python3 data/make_hatch_dxf.py
"""
import math
import os

HERE = os.path.dirname(os.path.abspath(__file__))

CIRC = 480.0          # circumference of the whole-tread fixtures, mm
WIDTH = 200.0         # tread width, mm
GROOVE = 8.0          # lateral groove width, mm
PITCH = 60.0          # one pitch of hatch_pitch.dxf, mm

TIEBAR_ACI = 6        # magenta, the layer colour


def rect_lines(x0, y0, x1, y1):
    """Four LINEs, counter-clockwise."""
    return [((x0, y0), (x1, y0)), ((x1, y0), (x1, y1)),
            ((x1, y1), (x0, y1)), ((x0, y1), (x0, y0))]


def rect_loop(x0, y0, x1, y1):
    """Four corners, counter-clockwise, no repeated closing point."""
    return [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]


# ---------------------------------------------------------------------------
# DXF writing
# ---------------------------------------------------------------------------


def _layer_table(layers):
    out = ["0", "SECTION", "2", "TABLES", "0", "TABLE", "2", "LAYER",
           "70", str(len(layers))]
    for name, aci in layers:
        out += ["0", "LAYER", "100", "AcDbSymbolTableRecord",
                "100", "AcDbLayerTableRecord", "2", name, "70", "0",
                "62", str(aci), "6", "CONTINUOUS"]
    out += ["0", "ENDTAB", "0", "ENDSEC"]
    return out


def _num(v):
    return f"{v:.6f}"


def hatch_polyline(loops, layer="TIEBAR", aci=None, true_color=None, handle=None):
    """A HATCH whose boundary paths are polylines (path flag bit 2).

    `loops` is a list of point lists; the first is the outer boundary and the
    rest are holes, which is how the flags are written -- though the reader
    decides by nesting, not by the flags.
    """
    out = ["0", "HATCH", "100", "AcDbEntity", "8", layer]
    if handle:
        out += ["5", handle]
    if true_color is not None:
        out += ["420", str(true_color)]
    elif aci is not None:
        out += ["62", str(aci)]
    out += ["100", "AcDbHatch",
            "10", "0.0", "20", "0.0", "30", "0.0",
            "210", "0.0", "220", "0.0", "230", "1.0",
            "2", "SOLID", "70", "1", "71", "0",
            "91", str(len(loops))]
    for i, loop in enumerate(loops):
        # 3 = external + polyline, 2 = polyline (a hole)
        out += ["92", "3" if i == 0 else "2",
                "72", "0",              # no bulges
                "73", "1",              # closed
                "93", str(len(loop))]
        for x, y in loop:
            out += ["10", _num(x), "20", _num(y)]
        out += ["97", "0"]              # no source boundary objects
    out += ["75", "0", "76", "1", "98", "0"]
    return out


def hatch_edges(loops, layer="TIEBAR", aci=None, true_color=None):
    """A HATCH whose boundary paths are edge lists of LINE edges (type 1)."""
    out = ["0", "HATCH", "100", "AcDbEntity", "8", layer]
    if true_color is not None:
        out += ["420", str(true_color)]
    elif aci is not None:
        out += ["62", str(aci)]
    out += ["100", "AcDbHatch",
            "10", "0.0", "20", "0.0", "30", "0.0",
            "210", "0.0", "220", "0.0", "230", "1.0",
            "2", "SOLID", "70", "1", "71", "0",
            "91", str(len(loops))]
    for i, loop in enumerate(loops):
        out += ["92", "1" if i == 0 else "0",   # external / ordinary, edge list
                "93", str(len(loop))]
        for j, (x, y) in enumerate(loop):
            x2, y2 = loop[(j + 1) % len(loop)]
            out += ["72", "1",
                    "10", _num(x), "20", _num(y),
                    "11", _num(x2), "21", _num(y2)]
        out += ["97", "0"]
    out += ["75", "0", "76", "1", "98", "0"]
    return out


def hatch_stadium(x0, x1, y0, y1, layer="TIEBAR", aci=None):
    """An edge-list HATCH: two lines and two arcs, i.e. a stadium lying in x.

    The arcs are written with the counter-clockwise flag set, and their start
    and end angles are the ones AutoCAD would write, so a reader that ignores
    the flag or the direction produces a self-crossing loop.
    """
    r = (y1 - y0) / 2.0
    cy = (y0 + y1) / 2.0
    xa, xb = x0 + r, x1 - r
    out = ["0", "HATCH", "100", "AcDbEntity", "8", layer]
    if aci is not None:
        out += ["62", str(aci)]
    out += ["100", "AcDbHatch",
            "10", "0.0", "20", "0.0", "30", "0.0",
            "210", "0.0", "220", "0.0", "230", "1.0",
            "2", "SOLID", "70", "1", "71", "0",
            "91", "1",
            "92", "1", "93", "4"]
    # bottom line, left to right
    out += ["72", "1", "10", _num(xa), "20", _num(y0), "11", _num(xb), "21", _num(y0)]
    # right cap: arc centred (xb, cy), -90 -> +90 counter-clockwise
    out += ["72", "2", "10", _num(xb), "20", _num(cy), "40", _num(r),
            "50", "270.0", "51", "90.0", "73", "1"]
    # top line, right to left
    out += ["72", "1", "10", _num(xb), "20", _num(y1), "11", _num(xa), "21", _num(y1)]
    # left cap: arc centred (xa, cy), 90 -> 270 counter-clockwise
    out += ["72", "2", "10", _num(xa), "20", _num(cy), "40", _num(r),
            "50", "90.0", "51", "270.0", "73", "1"]
    out += ["97", "0", "75", "0", "76", "1", "98", "0"]
    return out


def write(path, lines, hatches, layers=(("0", 7), ("TREAD", 7), ("TIEBAR", TIEBAR_ACI))):
    out = _layer_table(list(layers))
    out += ["0", "SECTION", "2", "ENTITIES"]
    for (x0, y0), (x1, y1) in lines:
        out += ["0", "LINE", "8", "TREAD",
                "10", _num(x0), "20", _num(y0),
                "11", _num(x1), "21", _num(y1)]
    for h in hatches:
        out += h
    out += ["0", "ENDSEC", "0", "EOF"]
    with open(path, "w") as fh:
        fh.write("\n".join(out) + "\n")
    print(f"wrote {os.path.basename(path)}  ({len(lines)} lines, {len(hatches)} hatches)")


# ---------------------------------------------------------------------------
# the treads themselves
# ---------------------------------------------------------------------------

def rib_bands():
    """Four circumferential ribs separated by three lateral grooves."""
    half = WIDTH / 2
    rib = (WIDTH - 3 * GROOVE) / 4.0
    bands, y = [], -half
    for _ in range(4):
        bands.append((y, y + rib))
        y += rib + GROOVE
    return bands


def bar_spans():
    """Where the tie bars sit: in each groove, at four circumferential stations.

    Returned as (x0, x1, y0, y1) in tread coordinates, y measured from the
    centreline -- the bar spans the groove, so its y range IS the groove.
    """
    bands = rib_bands()
    out = []
    for i in range(len(bands) - 1):
        y0, y1 = bands[i][1], bands[i + 1][0]
        for k in range(4):
            x0 = 30.0 + k * (CIRC / 4.0)
            out.append((x0, x0 + 24.0, y0, y1))
    return out


def build_tread(with_bar_linework):
    """Four ribs cut into blocks, plus optional linework closing each tie bar.

    Every rib is cut into eight blocks by lateral grooves, so the drawing has
    real blocks with the bars bridging between them.
    """
    lines = []
    nb = 8
    step = CIRC / nb
    for y0, y1 in rib_bands():
        for k in range(nb):
            lines += rect_lines(k * step + 3.0, y0, (k + 1) * step - 3.0, y1)
    if with_bar_linework:
        for x0, x1, y0, y1 in bar_spans():
            # only the two walls across the groove: the rib edges are already
            # drawn above, so this closes the bar exactly like a real drawing
            lines += [((x0, y0), (x0, y1)), ((x1, y0), (x1, y1))]
    return lines


def build_hatches():
    """One HATCH per tie bar, in four flavours, cycling by index."""
    hatches = []
    for i, (x0, x1, y0, y1) in enumerate(bar_spans()):
        loop = rect_loop(x0, y0, x1, y1)
        flavour = i % 4
        if flavour == 0:
            # polyline boundary, layer colour
            hatches.append(hatch_polyline([loop], handle=f"A{i:03X}"))
        elif flavour == 1:
            # polyline boundary with a hole -- a stone ejector through the bar
            cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
            hole = rect_loop(cx - 4, cy - 1.5, cx + 4, cy + 1.5)
            hatches.append(hatch_polyline([loop, hole], aci=30, handle=f"A{i:03X}"))
        elif flavour == 2:
            # edge-list boundary, entity ACI
            hatches.append(hatch_edges([loop], aci=3))
        else:
            # edge-list boundary, 24-bit true colour
            hatches.append(hatch_edges([loop], true_color=0x1E90FF))
    return hatches


def build_pitch():
    """One pitch: two ribs cut into one block each, with one hatched bar."""
    half = WIDTH / 2
    rib = (WIDTH - GROOVE) / 2.0
    bands = [(-half, -half + rib), (-half + rib + GROOVE, half)]
    lines = []
    for y0, y1 in bands:
        lines += rect_lines(2.0, y0, PITCH - 2.0, y1)
    y0, y1 = bands[0][1], bands[1][0]
    x0, x1 = 20.0, 40.0
    lines += [((x0, y0), (x0, y1)), ((x1, y0), (x1, y1))]
    return lines, [hatch_polyline([rect_loop(x0, y0, x1, y1)], aci=30)]


if __name__ == "__main__":
    write(os.path.join(HERE, "hatch_tiebars.dxf"),
          build_tread(with_bar_linework=True), build_hatches())
    write(os.path.join(HERE, "hatch_only.dxf"),
          build_tread(with_bar_linework=False), build_hatches())
    plines, phatch = build_pitch()
    write(os.path.join(HERE, "hatch_pitch.dxf"), plines, phatch)
    write(os.path.join(HERE, "hatch_arc.dxf"),
          build_tread(with_bar_linework=False),
          [hatch_stadium(30.0, 54.0, -GROOVE / 2, GROOVE / 2, aci=30)])
