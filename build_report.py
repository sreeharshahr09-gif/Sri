#!/usr/bin/env python3
"""Build the interactive 2W tread pattern evaluation report.

Every input can be given either on the command line or in a config file. The
config file is the better home for anything that describes the *tyre* rather
than the run:

    python build_report.py --write-example-config my_tyre.json   # start here
    # edit my_tyre.json -- point `pattern.dxf` at your drawing, set NSD etc.
    python build_report.py --config my_tyre.json

Command-line flags override the config, so a config file is a set of defaults:

    python build_report.py --config my_tyre.json --lean 0,15,30 --nsd 9

Or skip the config entirely for a one-off:

    python build_report.py --dxf tread_plan.dxf --nsd 8.5 --draft 3 --sipes 1

`--save-config out.json` writes back everything the run actually used, which is
the easy way to turn a working command line into a reusable file.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time

from tread_eval.config import Config, write_example
from tread_eval.contact_patch import max_supported_lean
from tread_eval.cp_io import build_library, load_cp_any
from tread_eval.cp_shapes import shape_patch
from tread_eval.dxf import load_pattern as load_dxf_pattern
from tread_eval.raster import make_grid, rasterise
from tread_eval.report import write_report
from tread_eval.schema import Pattern
from tread_eval.summary import print_summary
from tread_eval.sweep import sweep
from tread_eval.synthetic import SyntheticParams, generate_pattern


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )

    cfg = p.add_argument_group("configuration file")
    cfg.add_argument("--config", help="JSON file holding every input (see --write-example-config)")
    cfg.add_argument("--save-config", metavar="PATH",
                     help="write the effective configuration (config + overrides) to PATH")
    cfg.add_argument("--write-example-config", metavar="PATH",
                     help="write a commented example config to PATH and exit")
    cfg.add_argument("--show-config", action="store_true",
                     help="print the effective configuration and exit without running")

    src = p.add_argument_group("pattern source (overrides config.pattern)")
    src.add_argument("--dxf", help="tread-plan DXF to analyse")
    src.add_argument("--pattern", help="a previously saved Pattern JSON")
    src.add_argument("--synthetic", action="store_true", help="generate a placeholder pattern")
    src.add_argument("--pitch-mode", choices=["random", "tonal_group", "uniform"],
                     help="synthetic pitch sequencing strategy")
    src.add_argument("--nsd", type=float, help="non-skid depth (block height), mm")
    src.add_argument("--draft", type=float, help="mould draft angle, deg")
    src.add_argument("--sipes", type=int, help="lateral sipes per block")
    src.add_argument("--n-pitches", type=int, help="override the pitch count inferred from the DXF")
    src.add_argument("--circumference", type=float, help="override tyre circumference, mm")
    src.add_argument("--tread-width", type=float, help="override tread width, mm")
    src.add_argument("--dxf-layers", help="comma-separated DXF layers to import (default: all)")
    src.add_argument("--seed", type=int, help="synthetic generator seed")
    src.add_argument("--save-pattern", help="write the imported/generated Pattern to JSON")

    an = p.add_argument_group("analysis (overrides config.analysis / compound / load)")
    an.add_argument("--lean", help="comma-separated lean angles in degrees")
    an.add_argument("--resolution", type=float, help="raster resolution in mm (0.2-0.5 resolves sipes)")
    an.add_argument("--curvature-correction", action="store_true",
                    help="apply the lateral arc-length correction (leave off for mould DXFs)")
    an.add_argument("--load", type=float, help="upright vertical load, N")
    an.add_argument("--wheel-radius", type=float, help="free rolling radius, mm")
    an.add_argument("--no-lean-load-rise", action="store_true",
                    help="keep Fz constant with lean instead of Fz/cos(gamma)")
    an.add_argument("--slip-angle", type=float, help="steady sideslip added to travel direction, deg")
    an.add_argument("--shore", type=float, help="tread compound Shore A")
    an.add_argument("--poisson", type=float, help="Poisson ratio")
    an.add_argument("--boundary", choices=["parallel", "free"], help="block top boundary condition")
    an.add_argument("--crown-r-center", type=float, help="crown radius at the centre, mm")
    an.add_argument("--crown-r-shoulder", type=float, help="crown radius at the shoulder, mm")
    an.add_argument("--crown-section", help="measured cross-section CSV (y_projected,z in mm)")
    an.add_argument("--discrete-samples", type=int, help="theta samples for the discrete block count")

    cp = p.add_argument_group("contact patch (overrides config.contact_patch)")
    cp.add_argument("--cp", help="measured footprint: outline (.csv/.json/.dxf), pressure map, or manifest")
    cp.add_argument("--cp-gamma", type=float, help="lean angle of a single imported footprint, deg")
    cp.add_argument("--cp-pressure", action="store_true", help="treat --cp as a pressure map")
    cp.add_argument("--cp-units", choices=["mm", "cm", "m", "in"], help="units of the imported file")
    cp.add_argument("--cp-pressure-units", choices=["MPa", "kPa", "bar", "psi", "N/mm2"])
    cp.add_argument("--cp-pressure-dx", type=float, help="pressure map cell size in x, mm")
    cp.add_argument("--cp-pressure-dy", type=float, help="pressure map cell size in y, mm")
    cp.add_argument("--cp-load", type=float, help="normal load the footprint was taken at, N")
    cp.add_argument("--cp-lateral", choices=["auto", "centre", "absolute"],
                    help="how to place an imported outline across the tread (default: auto)")
    cp.add_argument("--cp-y", type=float, help="lateral centre of the imported footprint, mm")
    cp.add_argument("--cp-shape", help="standard shape: rectangle, rounded, stadium, ellipse, "
                                       "superellipse, trapezoid, diamond")
    cp.add_argument("--cp-length", type=float, help="patch length (circumferential), mm")
    cp.add_argument("--cp-width", type=float, help="patch width (lateral), mm")
    cp.add_argument("--cp-corner-radius", type=float, help="corner radius for --cp-shape rounded, mm")
    cp.add_argument("--cp-exponent", type=float, help="exponent for --cp-shape superellipse")
    cp.add_argument("--cp-taper", type=float, help="taper for --cp-shape trapezoid, -1..1")
    cp.add_argument("--cp-rotation", type=float, help="rotate the shape, deg")
    cp.add_argument("--cp-model", choices=["winkler", "rhyne"], help="generator where nothing else applies")
    cp.add_argument("--no-cp-transfer", action="store_true",
                    help="do not rescale a measured footprint to unmeasured lean angles")
    cp.add_argument("--inflation", type=float, help="inflation pressure, kPa (rhyne)")
    cp.add_argument("--section-width", type=float, help="section width, mm (rhyne)")
    cp.add_argument("--aspect-ratio", type=float, help="aspect ratio, %% (rhyne)")
    cp.add_argument("--rim-diameter", type=float, help="rim diameter, in (rhyne)")
    cp.add_argument("--tyre-type", choices=["2w", "pcr", "tbr", "lt"])

    out = p.add_argument_group("output (overrides config.output)")
    out.add_argument("-o", "--output", help="HTML report path")
    out.add_argument("--theta-points", type=int, help="theta samples embedded in the report")
    out.add_argument("--plotly", choices=["embed", "cdn"], help="embed the Plotly bundle or link a CDN")
    out.add_argument("--plotly-js", help="explicit path to plotly.min.js")
    out.add_argument("--notes", help="free-text note recorded in the report")
    out.add_argument("--dump-metrics", help="also write the computed payload to this JSON path")
    out.add_argument("--quiet", action="store_true")
    return p.parse_args(argv)


def apply_overrides(cfg: Config, a: argparse.Namespace) -> Config:
    """Fold command-line flags onto a config. Only flags actually given win."""
    def setif(obj, attr, value):
        if value is not None:
            setattr(obj, attr, value)

    setif(cfg.pattern, "dxf", a.dxf)
    setif(cfg.pattern, "pattern_json", a.pattern)
    if a.synthetic:
        cfg.pattern.synthetic = True
    setif(cfg.pattern, "pitch_mode", a.pitch_mode)
    setif(cfg.pattern, "nsd_mm", a.nsd)
    setif(cfg.pattern, "draft_deg", a.draft)
    setif(cfg.pattern, "lateral_sipes", a.sipes)
    setif(cfg.pattern, "n_pitches", a.n_pitches)
    setif(cfg.pattern, "circumference_mm", a.circumference)
    setif(cfg.pattern, "tread_width_mm", a.tread_width)
    setif(cfg.pattern, "seed", a.seed)
    if a.dxf_layers:
        cfg.pattern.dxf_layers = [s.strip() for s in a.dxf_layers.split(",") if s.strip()]
    if a.curvature_correction:
        cfg.pattern.curvature_correction = True

    setif(cfg.tyre, "wheel_radius_mm", a.wheel_radius)
    setif(cfg.tyre, "inflation_kpa", a.inflation)
    setif(cfg.tyre, "section_width_mm", a.section_width)
    setif(cfg.tyre, "aspect_ratio", a.aspect_ratio)
    setif(cfg.tyre, "rim_diameter_in", a.rim_diameter)
    setif(cfg.tyre, "tyre_type", a.tyre_type)
    setif(cfg.tyre, "crown_r_center_mm", a.crown_r_center)
    setif(cfg.tyre, "crown_r_shoulder_mm", a.crown_r_shoulder)
    setif(cfg.tyre, "crown_section_csv", a.crown_section)

    setif(cfg.compound, "shore_a", a.shore)
    setif(cfg.compound, "poisson", a.poisson)
    setif(cfg.compound, "boundary", a.boundary)

    setif(cfg.load, "vertical_N", a.load)
    setif(cfg.load, "slip_angle_deg", a.slip_angle)
    if a.no_lean_load_rise:
        cfg.load.rises_with_lean = False

    setif(cfg.contact_patch, "model", a.cp_model)
    if a.no_cp_transfer:
        cfg.contact_patch.allow_transfer = False
    if a.cp:
        imp = dict(cfg.contact_patch.import_)
        imp["path"] = a.cp
        for key, val in [
            ("gamma_deg", a.cp_gamma), ("units", a.cp_units),
            ("pressure_units", a.cp_pressure_units), ("pressure_dx", a.cp_pressure_dx),
            ("pressure_dy", a.cp_pressure_dy), ("load_N", a.cp_load),
            ("lateral", a.cp_lateral), ("y_center", a.cp_y),
        ]:
            if val is not None:
                imp[key] = val
        if a.cp_pressure:
            imp["as_pressure"] = True
        cfg.contact_patch.import_ = imp

    # A shape given on the command line replaces the config's shape list, so
    # "--cp-shape rectangle" is a complete statement rather than an addition.
    shape_flags = {
        "shape": a.cp_shape, "length": a.cp_length, "width": a.cp_width,
        "corner_radius": a.cp_corner_radius, "exponent": a.cp_exponent,
        "taper": a.cp_taper, "rotation": a.cp_rotation,
    }
    if any(v is not None for v in shape_flags.values()):
        spec = {k: v for k, v in shape_flags.items() if v is not None}
        spec.setdefault("shape", "rounded")
        spec["gamma_deg"] = a.cp_gamma if a.cp_gamma is not None else 0.0
        cfg.contact_patch.shapes = [spec]

    if a.lean:
        try:
            cfg.analysis.lean_angles = [float(v) for v in a.lean.split(",") if v.strip()]
        except ValueError as exc:
            raise ValueError(
                f"--lean must be a comma-separated list of angles in degrees, got {a.lean!r} ({exc})"
            ) from None
        if not cfg.analysis.lean_angles:
            raise ValueError("--lean listed no angles")
    setif(cfg.analysis, "resolution_mm", a.resolution)
    setif(cfg.analysis, "discrete_samples", a.discrete_samples)

    setif(cfg.output, "path", a.output)
    setif(cfg.output, "theta_points", a.theta_points)
    setif(cfg.output, "plotly", a.plotly)
    setif(cfg.output, "notes", a.notes)
    return cfg


def load_the_pattern(cfg: Config, quiet: bool):
    """Build a Pattern from whichever source the config names."""
    p = cfg.pattern
    import_report = None

    if p.dxf:
        if not os.path.exists(p.dxf):
            raise SystemExit(f"error: DXF not found: {p.dxf}")
        pattern, import_report = load_dxf_pattern(
            p.dxf,
            p.block_defaults(),
            circumference=p.circumference_mm,
            tread_width=p.tread_width_mm,
            n_pitches=p.n_pitches,
            layers=set(p.dxf_layers) if p.dxf_layers else None,
        )
        if not quiet:
            print(import_report.summary())
    elif p.pattern_json:
        pattern = Pattern.load_json(p.pattern_json)
    else:
        sp = SyntheticParams(pitch_mode=p.pitch_mode)
        if p.circumference_mm:
            sp.circumference = p.circumference_mm
        if p.tread_width_mm:
            sp.tread_width = p.tread_width_mm
        if p.seed is not None:
            sp.seed = p.seed
        pattern = generate_pattern(sp)

    # The crown profile is a tyre property, so it comes from the config rather
    # than from whatever the pattern source guessed.
    pattern.crown_radius_profile = cfg.tyre.crown(pattern.tread_width)
    if cfg.name:
        pattern.name = cfg.name
    return pattern, import_report


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    t0 = time.time()

    if args.write_example_config:
        write_example(args.write_example_config)
        print(f"wrote an example configuration to {args.write_example_config}")
        print("Edit it (point `pattern.dxf` at your drawing), then run:")
        print(f"  python build_report.py --config {args.write_example_config}")
        return 0

    try:
        cfg = Config.from_file(args.config) if args.config else Config()
    except (ValueError, json.JSONDecodeError) as exc:
        print(f"error in {args.config}: {exc}", file=sys.stderr)
        return 2
    cfg = apply_overrides(cfg, args)

    if args.show_config:
        print(json.dumps(cfg.to_dict(), indent=2))
        return 0
    if args.save_config:
        cfg.save(args.save_config)
        if not args.quiet:
            print(f"wrote the effective configuration to {args.save_config}")

    if not args.quiet:
        for line in cfg.describe_sources():
            print(line)
        print()

    pattern, _ = load_the_pattern(cfg, args.quiet)

    problems = pattern.validate()
    if problems:
        print("Pattern validation problems:", file=sys.stderr)
        for pr in problems:
            print("  - " + pr, file=sys.stderr)

    if args.save_pattern:
        pattern.save_json(args.save_pattern)

    lean_angles = list(cfg.analysis.lean_angles)
    crown = pattern.crown()
    max_lean = max_supported_lean(crown)
    over = [g for g in lean_angles if g > max_lean + 1e-6]
    if over and not args.quiet:
        print(
            f"note: lean {over} exceeds what the crown profile reaches ({max_lean:.1f} deg); "
            "the contact point is pinned at the tread edge there.\n",
            file=sys.stderr,
        )

    cp_params = cfg.cp_params()
    stiffness_params = cfg.compound.params()

    # ---- contact patch: imported measurements, then stated shapes, then model
    imported = None
    imp = cfg.contact_patch.import_
    if imp.get("path"):
        if not os.path.exists(imp["path"]):
            raise SystemExit(f"error: contact patch file not found: {imp['path']}")
        imported = load_cp_any(
            imp["path"], pattern.tread_width,
            gamma_deg=imp.get("gamma_deg", 0.0),
            units=imp.get("units", "mm"),
            pressure_units=imp.get("pressure_units", "MPa"),
            dx=imp.get("pressure_dx"), dy=imp.get("pressure_dy"),
            load_n=imp.get("load_N"), as_pressure=bool(imp.get("as_pressure")),
            lateral=imp.get("lateral", "auto"), y_center=imp.get("y_center"),
        )
        if not args.quiet:
            print(imported.summary())

    library = build_library(
        crown, pattern.tread_width, cp_params,
        generator=cfg.contact_patch.model, imported=imported,
        allow_transfer=cfg.contact_patch.allow_transfer,
    )

    try:
        specs = cfg.contact_patch.shape_specs()
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    for spec in specs:
        # Registered like a measurement, so the same "nearest source wins"
        # resolution applies; the provenance still says it was user-specified.
        library.add_measured(shape_patch(spec, crown, pattern.tread_width, cp_params))

    grid = make_grid(pattern, cfg.analysis.resolution_mm)
    if not args.quiet:
        print(f"\npattern : {pattern.name}  {len(pattern.blocks)} blocks, {len(pattern.pitches)} pitches")
        print(f"raster  : {grid.nx} x {grid.ny} px at {grid.dx:.3f} mm")

    pack = rasterise(pattern, grid, curvature_correction=cfg.pattern.curvature_correction,
                     stiffness_params=stiffness_params)
    results = sweep(pattern, pack, lean_angles, cp_params,
                    cfg.analysis.discrete_samples, library=library)

    if not args.quiet:
        print("\ncontact patch source by lean angle:")
        for row in library.coverage(lean_angles):
            print(f"  {row['gamma_deg']:5.1f} deg  {row['source']:<13} {row['provenance']}")

    payload = write_report(
        cfg.output.path, pattern, pack, results, cp_params, stiffness_params,
        theta_points=cfg.output.theta_points, plotly_mode=cfg.output.plotly,
        plotly_js=args.plotly_js, notes=cfg.output.notes, config=cfg,
    )

    if args.dump_metrics:
        with open(args.dump_metrics, "w") as fh:
            json.dump(payload, fh, indent=1)

    if not args.quiet:
        print_summary(payload)
        size = os.path.getsize(cfg.output.path) / 1e6
        print(f"\nwrote {cfg.output.path}  ({size:.1f} MB, {time.time() - t0:.1f} s)")
    return 0


def _cli() -> int:
    """Entry point that turns expected failures into readable messages.

    Everything a user can get wrong -- a missing file, a bad number, a config
    typo, an unwritable output path -- surfaces as a one-line ``error:`` rather
    than a traceback.  Set ``TREAD_DEBUG=1`` to get the traceback back when the
    failure is genuinely a bug in this code.
    """
    try:
        return main()
    except SystemExit:
        raise
    except KeyboardInterrupt:
        print("\ninterrupted", file=sys.stderr)
        return 130
    except (ValueError, OSError) as exc:
        if os.environ.get("TREAD_DEBUG"):
            raise
        print(f"error: {exc}", file=sys.stderr)
        print("(set TREAD_DEBUG=1 for the full traceback)", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(_cli())
