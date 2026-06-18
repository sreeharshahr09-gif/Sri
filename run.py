#!/usr/bin/env python3
"""
CLI entry point for the hybrid zero-degree cord patent analysis.

Usage:
    python run.py                      # uses config.yaml
    python run.py --config my.yaml
    python run.py --demo               # run on the bundled synthetic sample
"""

from __future__ import annotations

import argparse
import sys

from src.pipeline import run


def main() -> int:
    ap = argparse.ArgumentParser(description="Hybrid zero-degree cord patent analysis")
    ap.add_argument("--config", default="config.yaml", help="path to config.yaml")
    ap.add_argument("--demo", action="store_true",
                    help="run on bundled synthetic sample (data/sample)")
    args = ap.parse_args()

    if args.demo:
        # Point the pipeline at the sample data without editing config.yaml.
        import tempfile, yaml
        from src.config import Config
        cfg = Config.load(args.config).raw
        cfg["paths"]["raw_dir"] = "data/sample"
        cfg["paths"]["output_dir"] = "output_demo"
        with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as fh:
            yaml.safe_dump(cfg, fh)
            tmp = fh.name
        run(tmp)
        return 0

    run(args.config)
    return 0


if __name__ == "__main__":
    sys.exit(main())
