"""CLI: python -m sidewall <scan file> [--out DIR] [options]"""
import argparse
import csv
import json
from pathlib import Path

import numpy as np

from .analysis import analyze, load_scan
from . import plots


def main(argv=None):
    ap = argparse.ArgumentParser(description="Inflated vs loaded sidewall profile analysis")
    ap.add_argument("scan", help="two-row paste (x row, z row) or two-column x,z file")
    ap.add_argument("--out", default="output", help="output folder")
    ap.add_argument("--h-mm", type=float, default=5.0,
                    help="distance from carcass ply to outer surface, for bending strain (default 5)")
    ap.add_argument("--load-kN", type=float, default=None, help="applied load, to report vertical stiffness")
    ap.add_argument("--rim-end", choices=["top", "bottom"], default="top",
                    help="which end of the scan is the rim (labels zones only)")
    ap.add_argument("--window-mm", type=float, default=45.0,
                    help="length scale for slope/curvature smoothing (default 45)")
    a = ap.parse_args(argv)

    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    r = analyze(a.scan, h_mm=a.h_mm, load_kN=a.load_kN, rim_end=a.rim_end, window_mm=a.window_mm)

    (out / "metrics.json").write_text(json.dumps(r.metrics, indent=2, default=float))
    with open(out / "zones.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(r.zones[0]))
        w.writeheader()
        w.writerows(r.zones)
    mt = r.matched
    cols = dict(s_mm=mt.s, z_inflated=mt.z_inf, z_loaded=mt.z_load, u_inflated=mt.u_inf,
                u_loaded=mt.u_load, outward_disp=mt.du, vertical_disp=mt.dz, rotation_deg=mt.dtheta,
                kappa_inflated_per_m=1000 * mt.kappa_inf, kappa_loaded_per_m=1000 * mt.kappa_load,
                dkappa_per_m=1000 * mt.dkappa)
    np.savetxt(out / "matched_profile.csv", np.column_stack(list(cols.values())), delimiter=",",
               header=",".join(cols), comments="", fmt="%.4f")

    plots.overview(r, out / "overview.png")
    x, z = load_scan(a.scan)
    plots.data_quality(r, out / "data_quality.png", x, z)

    m = r.metrics
    print(f"vertical deflection  {m['vertical_deflection_mm']:.1f} mm")
    print(f"max-width growth     {m['max_width']['growth_mm']:.1f} mm  (point drops {m['max_width']['point_drop_mm']:.0f} mm)")
    print(f"peak bulge           naive {m['naive_same_z']['peak_mm']:.1f} mm | matched {m['matched']['peak_outward_mm']:.1f} mm")
    print(f"arc length deficit   {m['arc_length']['deficit_mm']:.1f} mm ({m['arc_length']['deficit_pct']:.1f}%)")
    print(f"wrote {out}/")


if __name__ == "__main__":
    main()
