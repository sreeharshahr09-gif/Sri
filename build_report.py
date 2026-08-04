#!/usr/bin/env python3
"""Build the interactive 2W tread pattern evaluation report.

Typical use:

    python build_report.py                        # defaults, writes out/tread_report.html
    python build_report.py --pitch-mode uniform   # compare against a tonal sequence
    python build_report.py --pattern my.json      # analyse a saved Pattern (e.g. from a DXF parser)
    python build_report.py --curvature-correction # for competitor patterns digitised flat

The three placeholder models (pattern geometry, block stiffness, contact patch
vs. lean) are each behind one module; see README.md for the swap-in points.
"""

from __future__ import annotations

import argparse
import json
import sys
import time

from tread_eval.contact_patch import CPParams, max_supported_lean
from tread_eval.cp_io import build_library, load_cp_any
from tread_eval.dxf import BlockDefaults, load_pattern as load_dxf_pattern
from tread_eval.raster import make_grid, rasterise
from tread_eval.report import write_report
from tread_eval.schema import Pattern
from tread_eval.stiffness import StiffnessParams
from tread_eval.summary import print_summary
from tread_eval.sweep import sweep
from tread_eval.synthetic import SyntheticParams, generate_pattern


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)

    src = p.add_argument_group("pattern source")
    src.add_argument("--dxf", help="tread-plan DXF to analyse (the production input)")
    src.add_argument("--pattern", help="load a Pattern from JSON instead of generating one")
    src.add_argument("--nsd", type=float, default=8.0,
                     help="non-skid depth (block height) in mm for DXF import (default: 8.0)")
    src.add_argument("--draft", type=float, default=3.0, help="mould draft angle, deg (default: 3.0)")
    src.add_argument("--sipes", type=int, default=0,
                     help="lateral sipes per block for DXF import (default: 0)")
    src.add_argument("--n-pitches", type=int, help="override the pitch count inferred from the DXF")
    src.add_argument("--pitch-mode", default="random", choices=["random", "tonal_group", "uniform"],
                     help="synthetic pitch sequencing strategy (default: random)")
    src.add_argument("--circumference", type=float, help="override tyre circumference, mm")
    src.add_argument("--tread-width", type=float, help="override tread width, mm")
    src.add_argument("--seed", type=int, help="synthetic generator seed")
    src.add_argument("--save-pattern", help="write the generated Pattern to JSON")

    an = p.add_argument_group("analysis")
    an.add_argument("--lean", default="0,10,20,30,40",
                    help="comma-separated lean angles in degrees (default: 0,10,20,30,40)")
    an.add_argument("--resolution", type=float, default=0.35,
                    help="raster resolution in mm (default: 0.35; 0.2-0.5 resolves sipes)")
    an.add_argument("--curvature-correction", action="store_true",
                    help="apply the lateral arc-length correction (leave OFF for mould DXFs)")
    an.add_argument("--load", type=float, default=CPParams.vertical_load, help="upright vertical load, N")
    an.add_argument("--wheel-radius", type=float, default=CPParams.wheel_radius, help="free rolling radius, mm")
    an.add_argument("--no-lean-load-rise", action="store_true",
                    help="keep Fz constant with lean instead of Fz/cos(gamma)")
    an.add_argument("--slip-angle", type=float, default=0.0,
                    help="steady sideslip added to the travel direction, deg")
    an.add_argument("--shore", type=float, default=60.0, help="tread compound Shore A (default: 60)")
    an.add_argument("--poisson", type=float, default=0.49, help="Poisson ratio (default: 0.49)")
    an.add_argument("--boundary", choices=["parallel", "free"], default="parallel",
                    help="block top boundary condition; parallel = held flat by friction (default)")

    cp = p.add_argument_group("contact patch")
    cp.add_argument("--cp", help="measured footprint: outline (.csv/.json/.dxf), pressure map, or a manifest (.json)")
    cp.add_argument("--cp-gamma", type=float, default=0.0,
                    help="lean angle of a single imported footprint, deg (default: 0)")
    cp.add_argument("--cp-pressure", action="store_true",
                    help="treat --cp as a pressure map rather than an outline")
    cp.add_argument("--cp-units", default="mm", choices=["mm", "cm", "m", "in"])
    cp.add_argument("--cp-pressure-units", default="MPa", choices=["MPa", "kPa", "bar", "psi", "N/mm2"])
    cp.add_argument("--cp-pressure-dx", type=float, help="pressure map cell size in x, mm")
    cp.add_argument("--cp-pressure-dy", type=float, help="pressure map cell size in y, mm")
    cp.add_argument("--cp-load", type=float, help="normal load the imported footprint was taken at, N")
    cp.add_argument("--cp-model", choices=["winkler", "rhyne"], default="winkler",
                    help="generator used where no measurement is available (default: winkler)")
    cp.add_argument("--no-cp-transfer", action="store_true",
                    help="do not rescale a measured footprint to unmeasured lean angles; generate instead")
    cp.add_argument("--inflation", type=float, default=250.0, help="inflation pressure, kPa (rhyne)")
    cp.add_argument("--section-width", type=float, default=130.0, help="section width, mm (rhyne)")
    cp.add_argument("--aspect-ratio", type=float, default=80.0, help="aspect ratio, %% (rhyne)")
    cp.add_argument("--rim-diameter", type=float, default=17.0, help="rim diameter, in (rhyne)")
    cp.add_argument("--tyre-type", default="2w", choices=["2w", "pcr", "tbr", "lt"])
    an.add_argument("--discrete-samples", type=int, default=360,
                    help="theta samples for the discrete block count (default: 360)")

    out = p.add_argument_group("output")
    out.add_argument("-o", "--output", default="out/tread_report.html")
    out.add_argument("--theta-points", type=int, default=720,
                    help="theta samples embedded in the report (default: 720)")
    out.add_argument("--plotly", choices=["embed", "cdn"], default="embed",
                     help="embed the Plotly bundle (self-contained) or link a CDN")
    out.add_argument("--plotly-js", help="explicit path to plotly.min.js")
    out.add_argument("--notes", default="", help="free-text note recorded in the report")
    out.add_argument("--dump-metrics", help="also write the computed payload to this JSON path")
    out.add_argument("--quiet", action="store_true")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    t0 = time.time()

    if args.dxf:
        defaults = BlockDefaults(
            height=args.nsd, draft_angle=args.draft, n_lateral_sipes=args.sipes
        )
        pattern, import_report = load_dxf_pattern(
            args.dxf, defaults, n_pitches=args.n_pitches
        )
        if not args.quiet:
            print(import_report.summary())
    elif args.pattern:
        pattern = Pattern.load_json(args.pattern)
    else:
        sp = SyntheticParams(pitch_mode=args.pitch_mode)
        if args.circumference:
            sp.circumference = args.circumference
        if args.tread_width:
            sp.tread_width = args.tread_width
        if args.seed is not None:
            sp.seed = args.seed
        pattern = generate_pattern(sp)

    problems = pattern.validate()
    if problems:
        print("Pattern validation problems:", file=sys.stderr)
        for pr in problems:
            print("  - " + pr, file=sys.stderr)

    if args.save_pattern:
        pattern.save_json(args.save_pattern)

    lean_angles = [float(v) for v in args.lean.split(",") if v.strip()]
    max_lean = max_supported_lean(pattern.crown())
    over = [g for g in lean_angles if g > max_lean + 1e-6]
    if over and not args.quiet:
        print(
            f"note: lean {over} exceeds what the crown profile reaches ({max_lean:.1f} deg); "
            "the contact point is pinned at the tread edge there.",
            file=sys.stderr,
        )

    cp_params = CPParams(
        vertical_load=args.load,
        wheel_radius=args.wheel_radius,
        load_rises_with_lean=not args.no_lean_load_rise,
        lateral_slip_angle=args.slip_angle,
        inflation_kpa=args.inflation,
        section_width_mm=args.section_width,
        aspect_ratio=args.aspect_ratio,
        rim_diameter_in=args.rim_diameter,
        tyre_type=args.tyre_type,
    )
    stiffness_params = StiffnessParams(
        shore_a=args.shore, poisson=args.poisson, mode=args.boundary
    )

    imported = None
    if args.cp:
        imported = load_cp_any(
            args.cp, pattern.tread_width, gamma_deg=args.cp_gamma, units=args.cp_units,
            pressure_units=args.cp_pressure_units, dx=args.cp_pressure_dx,
            dy=args.cp_pressure_dy, load_n=args.cp_load, as_pressure=args.cp_pressure,
        )
        if not args.quiet:
            print(imported.summary())
    library = build_library(
        pattern.crown(), pattern.tread_width, cp_params,
        generator=args.cp_model, imported=imported,
        allow_transfer=not args.no_cp_transfer,
    )

    grid = make_grid(pattern, args.resolution)
    if not args.quiet:
        print(f"pattern : {pattern.name}  {len(pattern.blocks)} blocks, {len(pattern.pitches)} pitches")
        print(f"raster  : {grid.nx} x {grid.ny} px at {grid.dx:.3f} mm")

    pack = rasterise(pattern, grid, curvature_correction=args.curvature_correction,
                     stiffness_params=stiffness_params)
    results = sweep(pattern, pack, lean_angles, cp_params, args.discrete_samples, library=library)
    if not args.quiet:
        print("\ncontact patch source by lean angle:")
        for row in library.coverage(lean_angles):
            print(f"  {row['gamma_deg']:5.1f} deg  {row['source']:<13} {row['provenance']}")

    payload = write_report(
        args.output, pattern, pack, results, cp_params, stiffness_params,
        theta_points=args.theta_points, plotly_mode=args.plotly,
        plotly_js=args.plotly_js, notes=args.notes,
    )

    if args.dump_metrics:
        with open(args.dump_metrics, "w") as fh:
            json.dump(payload, fh, indent=1)

    if not args.quiet:
        print_summary(payload)
        import os

        size = os.path.getsize(args.output) / 1e6
        print(f"\nwrote {args.output}  ({size:.1f} MB, {time.time() - t0:.1f} s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
