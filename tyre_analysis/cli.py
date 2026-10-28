"""Headless command-line interface.

Validate and analyse DXF cross-sections without the Streamlit UI. Useful for
scripting, CI, and quick checks.

Examples
--------
    python -m tyre_analysis validate sample_data/*.dxf
    python -m tyre_analysis analyse --axis-x 0 --baseline baseline \\
        sample_data/baseline.dxf sample_data/iteration1.dxf -o results.csv
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .pipeline import analyse_files
from .validate import validate_dxf, format_report


def _cmd_validate(args) -> int:
    any_bad = False
    for f in args.files:
        rep = validate_dxf(f)
        print(format_report(rep))
        print()
        any_bad = any_bad or not rep.conforms
    return 1 if (any_bad and args.strict) else 0


def _cmd_analyse(args) -> int:
    files = {Path(f).stem: f for f in args.files}
    # Gate on conformance unless --force.
    bad = [lbl for lbl, p in files.items() if not validate_dxf(p).conforms]
    if bad and not args.force:
        print(f"Non-conforming files: {', '.join(bad)}", file=sys.stderr)
        print("Fix them in CAD or pass --force to attempt anyway.", file=sys.stderr)
        return 1

    df, geoms, warnings = analyse_files(files, axis_x=args.axis_x, flatten_tol=args.flatten_tol)
    for fw in warnings:
        for m in fw.messages:
            print(f"[warn] {fw.file}: {m}", file=sys.stderr)

    show = ["file", "component", "area_mm2", "weight_g", "centroid_distance_mm",
            "volume_cm3", "surface_area_mm2", "polar_MOI", "pct_of_total_weight"]
    with_pd = df[show].round(3)
    print(with_pd.to_string(index=False))

    print("\nTire totals:")
    print(df.groupby("file")["weight_g"].sum().round(2).to_string())

    if args.output:
        df.to_csv(args.output, index=False)
        print(f"\nWrote {args.output}")
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="tyre_analysis", description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)

    pv = sub.add_parser("validate", help="Check DXF conformance")
    pv.add_argument("files", nargs="+")
    pv.add_argument("--strict", action="store_true", help="exit 1 if any file is non-conforming")
    pv.set_defaults(func=_cmd_validate)

    pa = sub.add_parser("analyse", help="Compute per-component results")
    pa.add_argument("files", nargs="+")
    pa.add_argument("--axis-x", type=float, default=0.0, help="rotation axis X (mm)")
    pa.add_argument("--baseline", default=None, help="baseline file stem (informational)")
    pa.add_argument("--flatten-tol", type=float, default=0.01)
    pa.add_argument("-o", "--output", help="write the long dataframe to CSV")
    pa.add_argument("--force", action="store_true", help="analyse even non-conforming files")
    pa.set_defaults(func=_cmd_analyse)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
