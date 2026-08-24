#!/usr/bin/env python3
"""Generate the single-pitch DXF fixtures used to test pitch replication.

Three files, all one pitch of a four-rib TBR-style tread on a 200 mm width:

  pitch_base_tbr.dxf    closes exactly -- the shoulder ribs run across the
                        pitch boundary and their edges line up to the micron
  pitch_open_tbr.dxf    the same drawing with the right-hand boundary points
                        pushed 0.4 mm off, which is what a digitised competitor
                        pattern looks like
  pitch_broken_tbr.dxf  one boundary edge deleted, so the two sides no longer
                        even have the same number of points -- the hard failure

Run:  python3 data/make_pitch_dxf.py
"""
import os

HERE = os.path.dirname(os.path.abspath(__file__))

PITCH = 40.0          # circumferential length of one pitch, mm
WIDTH = 200.0         # tread width, mm
GROOVE = 6.0          # lateral groove width between ribs, mm
LAT_GAP = 8.0         # circumferential gap cut across the intermediate ribs


def rect(x0, y0, x1, y1):
    """Four LINEs, counter-clockwise."""
    return [((x0, y0), (x1, y0)), ((x1, y0), (x1, y1)),
            ((x1, y1), (x0, y1)), ((x0, y1), (x0, y0))]


def build_blocks():
    """A blocked pitch: all four rows are cut, leaving a full-width lateral
    groove between one pitch and the next.  Nothing touches either boundary, so
    it tiles trivially -- and because there is an x-band with no land anywhere
    across the width, groove-only scaling has somewhere to put the extra length.
    """
    half = WIDTH / 2
    bands = [(-half + 1.0, -half + 42.0), (-half + 48.0, -8.0),
             (8.0, half - 48.0), (half - 42.0, half - 1.0)]
    lines = []
    for y0, y1 in bands:
        # one block per row, 30 mm of the 40 mm pitch, so 10 mm is groove
        lines += rect(2.0, y0, 32.0, y1)
    return lines


def build(offset=0.0, drop_edge=False):
    """One pitch.

    Shoulder ribs are continuous, so they cross both boundaries -- that is what
    makes closure testable.  The two intermediate ribs are cut into blocks with
    a lateral groove inside the pitch, so they contribute void bands in x, which
    is what groove-only scaling needs.

    `offset` shifts every point that sits on the RIGHT boundary by that much in
    y, to fake a digitising error.  `drop_edge` deletes one boundary segment.
    """
    half = WIDTH / 2
    lines = []

    # rib y-bands, centre outwards: shoulder / intermediate / intermediate / shoulder
    bands = [
        (-half, -half + 42.0, "shoulder"),
        (-half + 42.0 + GROOVE, -8.0, "intermediate"),
        (8.0, half - 42.0 - GROOVE, "intermediate"),
        (half - 42.0, half, "shoulder"),
    ]

    for y0, y1, kind in bands:
        if kind == "shoulder":
            # runs the full pitch, so it meets both boundaries
            lines += [((0.0, y0), (PITCH, y0)),
                      ((PITCH, y0), (PITCH, y1)),
                      ((PITCH, y1), (0.0, y1)),
                      ((0.0, y1), (0.0, y0))]
        else:
            # two blocks with a lateral groove between them, both inside
            # the pitch -- no boundary contact
            lines += rect(1.0, y0, (PITCH - LAT_GAP) / 2, y1)
            lines += rect((PITCH + LAT_GAP) / 2, y0, PITCH - 1.0, y1)

    if offset:
        lines = [tuple((x, y + offset if abs(x - PITCH) < 1e-9 else y) for (x, y) in seg)
                 for seg in lines]
    if drop_edge:
        # remove the first segment that lies wholly on the right boundary
        for i, ((xa, _), (xb, _)) in enumerate(lines):
            if abs(xa - PITCH) < 1e-9 and abs(xb - PITCH) < 1e-9:
                del lines[i]
                break
    return lines


def tile(lines, n, pitch):
    """The same pitch laid out n times by hand, as a whole-tread drawing.

    Used to prove that replication produces the same tread as drawing it out --
    the strongest check there is on the replicator, because the two routes share
    no code beyond the importer itself.
    """
    out = []
    for i in range(n):
        dx = i * pitch
        for (x0, y0), (x1, y1) in lines:
            out.append(((x0 + dx, y0), (x1 + dx, y1)))

    # Drop the cut lines that now sit at every INTERIOR join.  A real whole-tread
    # drawing does not have them: a rib that runs the whole way round is drawn as
    # one outline, not as one rectangle per pitch with a wall between each.  The
    # two at the very ends stay, because they close the ring.
    def interior_wall(seg):
        (xa, _), (xb, _) = seg
        if abs(xa - xb) > 1e-9:
            return False
        k = xa / pitch
        return abs(k - round(k)) < 1e-9 and 0 < round(k) < n

    return [seg for seg in out if not interior_wall(seg)]


def write(path, lines):
    out = ["0", "SECTION", "2", "ENTITIES"]
    for (x0, y0), (x1, y1) in lines:
        out += ["0", "LINE", "8", "TREAD",
                "10", f"{x0:.6f}", "20", f"{y0:.6f}",
                "11", f"{x1:.6f}", "21", f"{y1:.6f}"]
    out += ["0", "ENDSEC", "0", "EOF"]
    with open(path, "w") as fh:
        fh.write("\n".join(out) + "\n")
    print(f"wrote {os.path.basename(path)}  ({len(lines)} lines)")


if __name__ == "__main__":
    write(os.path.join(HERE, "pitch_base_tbr.dxf"), build())
    write(os.path.join(HERE, "pitch_open_tbr.dxf"), build(offset=0.4))
    write(os.path.join(HERE, "pitch_broken_tbr.dxf"), build(drop_edge=True))
    write(os.path.join(HERE, "pitch_blocks_tbr.dxf"), build_blocks())
    write(os.path.join(HERE, "pitch_whole_x6.dxf"), tile(build(), 6, PITCH))
