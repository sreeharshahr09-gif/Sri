"""CLI: python -m sidewall <files or folders ...> [--out DIR] [options]

One file  -> DIR/<file stem>/ with metrics, profiles and figures.
Many files (or a folder) -> the same per tire, plus DIR/comparison.csv and
DIR/comparison.png comparing all tires.
"""
import argparse
import csv
import glob
import json
from pathlib import Path

import numpy as np

from .analysis import analyze, load_scan_meta
from . import plots

PATTERNS = ("*.xlsx", "*.xlsm", "*.tsv", "*.txt", "*.csv")


def expand(inputs):
    files = []
    for item in inputs:
        p = Path(item)
        if p.is_dir():
            for pat in PATTERNS:
                files += sorted(f for f in p.glob(pat) if not f.name.startswith("~$"))
        elif any(ch in item for ch in "*?["):
            files += [Path(f) for f in sorted(glob.glob(item))]
        else:
            files.append(p)
    return files


def summary_row(r):
    m, t = r.metrics, r.metrics["test"]
    d, mw, mt = m["deflection"], m["max_width"], m["matched"]
    zones = {z["zone"]: z for z in r.zones}
    peak = max(m["hinges"], key=lambda h: h["surface_strain_pct"]) if m["hinges"] else {}
    return {
        "file": t.get("file"), "tire": r.label, "size": t.get("size"), "brand": t.get("brand"),
        "serial": t.get("serial"), "test_date": t.get("test_date"), "feature": t.get("feature"),
        "load_daN": t.get("load_daN"), "pressure_psi": t.get("pressure_psi"), "rim": t.get("rim"),
        "radial_deflection_lab_mm": d["radial_lab_mm"],
        "shoulder_travel_mm": d["shoulder_travel_mm"],
        "crown_share_mm": d["crown_share_mm"],
        "sidewall_share_pct": d["sidewall_share_pct"],
        "vertical_stiffness_N_per_mm": m["vertical_stiffness_N_per_mm"],
        "horizontal_deflection_lab_mm": t.get("horizontal_deflection_mm"),
        "horizontal_deflection_raw_mm": m["lab_style"]["horizontal_deflection_mm"],
        "horizontal_deflection_smoothed_mm": mw["growth_mm"],
        "bulge_per_mm_deflection": m["bulge_per_mm_deflection"],
        "bulge_per_kN_mm": m["bulge_per_kN_mm"],
        "widest_point_inflated_pct_from_rim": 100 * mw["inflated_frac_from_rim"],
        "widest_point_loaded_pct_from_rim": 100 * mw["loaded_frac_from_rim"],
        "peak_outward_deflection_mm": mt["peak_outward_mm"],
        "bulge_area_mm2": mt["bulge_area_mm2"],
        "max_rotation_deg": mt["max_rotation_deg"],
        "peak_bending_strain_pct": peak.get("surface_strain_pct"),
        "peak_bending_height_mm": peak.get("z_inflated"),
        "rim_side_bending_share_pct": zones["rim-side"]["share_of_bending_energy_pct"],
        "middle_bending_share_pct": zones["middle"]["share_of_bending_energy_pct"],
        "shoulder_side_bending_share_pct": zones["shoulder-side"]["share_of_bending_energy_pct"],
        "hidden_sidewall_when_loaded_mm": m["arc_length"]["deficit_mm"],
        "noise_mm": max(m["noise_floor_mm"].values()),
        "range_manual": m["range"]["manual"], "range_z_start_mm": m["range"]["z_start"],
        "range_edge_inflated_mm": m["range"]["edge_inflated"], "range_edge_loaded_mm": m["range"]["edge_loaded"],
    }


def write_one(r, path, out):
    out.mkdir(parents=True, exist_ok=True)
    (out / "metrics.json").write_text(json.dumps(r.metrics, indent=2, default=float))
    with open(out / "zones.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(r.zones[0]))
        w.writeheader()
        w.writerows(r.zones)
    mt = r.matched
    cols = dict(arc_from_rim_mm=mt.s, z_inflated=mt.z_inf, z_loaded=mt.z_load, u_inflated=mt.u_inf,
                u_loaded=mt.u_load, outward_deflection=mt.du, vertical_move=mt.dz,
                rotation_deg=mt.dtheta, kappa_inflated_per_m=1000 * mt.kappa_inf,
                kappa_loaded_per_m=1000 * mt.kappa_load, dkappa_per_m=1000 * mt.dkappa)
    np.savetxt(out / "matched_profile.csv", np.column_stack(list(cols.values())), delimiter=",",
               header=",".join(cols), comments="", fmt="%.4f")
    x, z, _ = load_scan_meta(path)
    np.savetxt(out / "raw_scan.csv", np.column_stack([x, z]), delimiter=",",
               header="laser_distance_mm,z_position_mm", comments="", fmt="%.6f")
    plots.overview(r, out / "overview.png")
    plots.data_quality(r, out / "data_quality.png", x, z)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Inflated vs loaded sidewall profile analysis")
    ap.add_argument("inputs", nargs="+", help="lab report .xlsx files, two-row pastes, folders or globs")
    ap.add_argument("--out", default="output", help="output folder")
    ap.add_argument("--h-mm", type=float, default=5.0,
                    help="distance from carcass ply to outer surface, for bending strain (default 5)")
    ap.add_argument("--load-kN", type=float, default=None,
                    help="applied load if the file does not carry one")
    ap.add_argument("--rim-end", choices=["bottom", "top"], default="bottom",
                    help="which end of the scan is the rim (default bottom)")
    ap.add_argument("--window-mm", type=float, default=45.0,
                    help="length scale for slope/curvature (default 45)")
    ap.add_argument("--smooth-mm", type=float, default=5.0,
                    help="smoothing of plotted profiles (default 5)")
    ap.add_argument("--z-start", type=float, default=None,
                    help="manual start of the sidewall at the rim end (scanner height, mm)")
    ap.add_argument("--edge-inflated", type=float, default=None,
                    help="manual shoulder edge in the inflated scan (scanner height, mm)")
    ap.add_argument("--edge-loaded", type=float, default=None,
                    help="manual shoulder edge in the loaded scan (scanner height, mm)")
    a = ap.parse_args(argv)

    files = expand(a.inputs)
    if not files:
        ap.error("no input files found")
    out = Path(a.out)
    results, rows = [], []
    for f in files:
        try:
            r = analyze(f, h_mm=a.h_mm, load_kN=a.load_kN, rim_end=a.rim_end,
                        window_mm=a.window_mm, smooth_mm=a.smooth_mm, z_start=a.z_start,
                        edge_inflated=a.edge_inflated, edge_loaded=a.edge_loaded)
        except Exception as e:  # keep the batch going; report the bad file
            print(f"SKIP {f.name}: {e}")
            continue
        write_one(r, f, out / f.stem)
        results.append(r)
        rows.append(summary_row(r))
        d = r.metrics["deflection"]
        lab = f"{d['radial_lab_mm']:.1f}" if d["radial_lab_mm"] else "  - "
        print(f"{r.label[:48]:48s}  radial {lab} mm  shoulder {d['shoulder_travel_mm']:5.1f} mm"
              f"  bulge {r.metrics['max_width']['growth_mm']:5.1f} mm")

    if len(results) > 1:
        with open(out / "comparison.csv", "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0]))
            w.writeheader()
            w.writerows(rows)
        plots.comparison(results, out / "comparison.png")
    print(f"{len(results)} of {len(files)} file(s) analysed -> {out}/")


if __name__ == "__main__":
    main()
