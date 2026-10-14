"""Command line.

  python -m tyretraction analyse --master MASTER.xlsx --curves CURVE_DIR --out out/
  python -m tyretraction analyse ... --overnight --hours 10     # extended grid, resumable
  python -m tyretraction predict --model out/predictor.joblib --input new_tyres.csv --out predictions/
  python -m tyretraction template new_tyres.csv                 # blank input sheet for predict
  python -m tyretraction demo-data demo_data/                   # synthetic data in the real format
"""
import argparse
import os
import sys


def main(argv=None):
    ap = argparse.ArgumentParser(prog="tyretraction", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("analyse", help="full analysis + report + predictor")
    a.add_argument("--master", required=True, help="master workbook (sheet 'Master File') or CSV")
    a.add_argument("--curves", help="folder with one µ-slip file per test (name starts with the Sl No)")
    a.add_argument("--out", default="out", help="output folder")
    a.add_argument("--id-regex", default=r"^\s*(\d+)",
                   help="regex whose first group is the Sl No in a curve file name (default: leading number)")
    a.add_argument("--column-map", help="JSON {their column name: our name} if the master layout differs")
    a.add_argument("--sheet", default="Master File")
    a.add_argument("--reference", help="reference tyre id 'Size|Pattern|Group' (default: auto-detect)")
    a.add_argument("--overnight", action="store_true",
                   help="extended experiment grid (feature sets, weightings, 3 seeds); resumable")
    a.add_argument("--hours", type=float, help="time budget for the experiment grid; rerun to resume")
    a.add_argument("--no-report", action="store_true")

    p = sub.add_parser("predict", help="predict curves for untested tyres")
    p.add_argument("--model", required=True)
    p.add_argument("--input", required=True, help="CSV, one row per tyre (see 'template')")
    p.add_argument("--out", default="predictions")

    t = sub.add_parser("template", help="write a blank input CSV for 'predict'")
    t.add_argument("path")

    r = sub.add_parser("report", help="rebuild the report from an existing out/ folder")
    r.add_argument("--out", default="out")

    d = sub.add_parser("demo-data", help="write synthetic data in the real file format")
    d.add_argument("path", nargs="?", default="demo_data")

    args = ap.parse_args(argv)
    if args.cmd == "analyse":
        from .pipeline import run
        run(args.master, args.curves, args.out, args.id_regex, args.column_map, extended=args.overnight,
            hours=args.hours, reference=args.reference, report=not args.no_report, sheet=args.sheet)
    elif args.cmd == "predict":
        import pandas as pd
        from .predictor import TractionPredictor
        pred = TractionPredictor.load(args.model)
        rows = pd.read_csv(args.input)
        os.makedirs(args.out, exist_ok=True)
        summary = []
        for res in pred.predict(rows):
            name = str(res["name"]).replace("/", "-").replace(" ", "_")
            res["curve"].to_csv(os.path.join(args.out, f"{name}_curve.csv"), index=False)
            rec = dict(name=res["name"], case=res["case"], curve_method=res["route"], basis=res["basis"])
            for k, v in res["landmarks"].items():
                rec[k] = v
                rec[k + "_lo90"], rec[k + "_hi90"] = res["ranges"][k]
            rec.update(res["params"])
            rec["warnings"] = " | ".join(res["warnings"])
            summary.append(rec)
        pd.DataFrame(summary).to_csv(os.path.join(args.out, "predictions.csv"), index=False)
        print(f"wrote {len(summary)} predictions to {args.out}/")
    elif args.cmd == "template":
        from .predictor import write_template
        write_template(args.path)
        print(f"wrote {args.path}")
    elif args.cmd == "report":
        import joblib
        from .report import build_report
        R = joblib.load(os.path.join(args.out, "results.joblib"))
        print(build_report(R, args.out))
    elif args.cmd == "demo-data":
        from .synthetic import generate
        issues = generate(args.path)
        print(f"wrote {args.path}/master.xlsx and {args.path}/curves/; planted issues: {issues}")


if __name__ == "__main__":
    sys.exit(main())
