"""Generate CONVENTION-CONFORMING sample DXFs for demos and tests.

Each component is written as ONE closed LWPOLYLINE (with bulges on curved
components to exercise arc flattening) on its own named layer. This is what a
correctly prepared tyre cross-section export should look like -- contrast with
a raw drawing where everything lands on layer '0'.

Produces a baseline and one modified iteration (a lighter tread + a shifted
apex) so the comparison / delta / waterfall UI has something to show.

Usage:  python scripts/make_sample_dxf.py [output_dir]
"""

from __future__ import annotations

import sys
from pathlib import Path

import ezdxf

# Axis (tyre centreline) sits at x = 0; all geometry is on the +x side, so the
# cross-section never crosses the rotation axis (Pappus requirement).

# component -> list of (x, y, bulge) vertices for a closed LWPOLYLINE.
# Bulge != 0 makes that edge a circular arc (tan(theta/4)).
def _baseline() -> dict[str, list[tuple[float, float, float]]]:
    return {
        # Tread: broad band at the outer radius, crowned top (bulge on top edge).
        "TREAD": [
            (150.0, 300.0, 0.0), (230.0, 300.0, 0.0),
            (230.0, 318.0, -0.15), (150.0, 318.0, 0.0),
        ],
        # Belts: two thin layers just under the tread.
        "BELT-1": [
            (152.0, 292.0, 0.0), (228.0, 292.0, 0.0),
            (228.0, 299.0, 0.0), (152.0, 299.0, 0.0),
        ],
        "BELT-2": [
            (155.0, 284.0, 0.0), (225.0, 284.0, 0.0),
            (225.0, 291.0, 0.0), (155.0, 291.0, 0.0),
        ],
        # Sidewall: tall thin curved wall from shoulder down to bead.
        "SIDEWALL": [
            (150.0, 300.0, 0.0), (158.0, 300.0, 0.2),
            (172.0, 150.0, 0.0), (172.0, 120.0, 0.0),
            (160.0, 120.0, 0.0), (160.0, 150.0, -0.2),
            (146.0, 300.0, 0.0),
        ],
        # Ply: single carcass ply following the sidewall, turned up at bead.
        "PLY-1": [
            (156.0, 300.0, 0.0), (159.0, 300.0, 0.15),
            (168.0, 130.0, 0.0), (168.0, 118.0, 0.0),
            (164.0, 118.0, 0.0), (164.0, 130.0, -0.15),
            (153.0, 300.0, 0.0),
        ],
        # Apex (bead filler): tapered triangle above the bead.
        "APEX": [
            (160.0, 118.0, 0.0), (170.0, 118.0, 0.0), (163.0, 175.0, 0.0),
        ],
        # Bead: steel bundle, near-circular (bulges) at the rim.
        "BEAD": [
            (160.0, 108.0, 0.414), (170.0, 108.0, 0.414),
            (170.0, 118.0, 0.414), (160.0, 118.0, 0.414),
        ],
    }


def _iteration1() -> dict[str, list[tuple[float, float, float]]]:
    """A design change: thinner tread (weight down) + apex moved radially out."""
    b = _baseline()
    # Tread gauge reduced from 18mm to 13mm (top edge lowered).
    b["TREAD"] = [
        (150.0, 300.0, 0.0), (230.0, 300.0, 0.0),
        (230.0, 313.0, -0.15), (150.0, 313.0, 0.0),
    ]
    # Apex made slightly smaller and pushed outward (radially higher).
    b["APEX"] = [
        (161.0, 120.0, 0.0), (169.0, 120.0, 0.0), (164.0, 182.0, 0.0),
    ]
    return b


def write_profile(components: dict, path: Path) -> None:
    doc = ezdxf.new("R2018")  # AC1032, >= R2000
    doc.header["$INSUNITS"] = 4  # millimetres
    msp = doc.modelspace()
    for layer_name, verts in components.items():
        if layer_name not in doc.layers:
            doc.layers.add(layer_name)
        # LWPOLYLINE format: (x, y, start_width, end_width, bulge)
        points = [(x, y, 0.0, 0.0, bulge) for (x, y, bulge) in verts]
        msp.add_lwpolyline(points, format="xyseb", close=True, dxfattribs={"layer": layer_name})
    doc.saveas(str(path))


def main(out_dir: str = "sample_data") -> None:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    write_profile(_baseline(), out / "baseline.dxf")
    write_profile(_iteration1(), out / "iteration1.dxf")
    print(f"Wrote {out/'baseline.dxf'} and {out/'iteration1.dxf'}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "sample_data")
