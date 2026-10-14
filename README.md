# tyretraction — tyre µ-slip analysis and prediction

Statistical analysis, data-quality checks, Pacejka fitting and honest machine-learning
prediction of µ-slip curves (peak µ, slip at peak µ, µ slide, µ lock, B C D E) for
untested PCR tyres, from:

* one **master** workbook (sheet `Master File`, the 36-column DTT layout), and
* one **µ-slip file per test** (two columns, SR and Mu), named with the test's Sl No
  at the start, e.g. `123. Trial_3.xlsx`.

Everything runs locally. The output is one HTML report with explained graphs, a
data-quality list, an experiment ledger and a saved predictor.

## Install

```bash
pip install -r requirements.txt
```

## Run on your data

```bash
python -m tyretraction analyse --master "DTT Tool for Traction prediction - Apollo Master.xlsx" \
       --curves path/to/curve_files --out out
```

Open `out/report.html`. Typical run time is 5–10 minutes for ~550 tests.

| option | meaning |
|---|---|
| `--id-regex` | how to find the Sl No in a file name; default: the leading number (`^\s*(\d+)`) |
| `--column-map map.json` | if the master columns differ: `{"their header": "our name", ...}` (names in `tyretraction/schema.py`) |
| `--reference "Size\|Pattern\|Group"` | force the reference tyre (default: detected automatically) |
| `--overnight --hours 10` | extended experiment grid (3 feature sets × 4 imbalance options × 3 seeds); resumable — rerun the same command to continue |
| `--no-report` | skip the HTML report |

Without `--curves` the analysis runs on the master landmarks alone (no curve
checks, Pacejka fits or curve routes).

## Predict untested tyres

```bash
python -m tyretraction template new_tyres.csv          # blank input sheet
# fill one row per tyre: size, pattern, compound group, surface, load, pressure,
# speed, temperatures, DMA and footprint values (leave unknowns empty)
python -m tyretraction predict --model out/predictor.joblib --input new_tyres.csv --out predictions
```

`predictions/predictions.csv` holds, per tyre: the case (A/B/C), peak µ, slip at
peak, µ slide and µ lock with 90% ranges, B C D E, and warnings (inputs outside the
tested range, missing compound data). One `<name>_curve.csv` per tyre holds the
full 0–1 slip curve with its 90% band.

## What the analysis does

1. **Matching and loading.** Files are matched to master rows by ID. Percent slip,
   negative sign conventions and the rolling phase before braking are handled.
2. **Data quality.** Duplicate IDs, text in numeric cells, out-of-range values,
   missing/unreadable/unmatched files, curves without a lock region, noise, spikes,
   master-vs-curve disagreements, tests far from their repeats, poor Magic Formula
   fits. Written to `data_quality_flags.csv` with a recommended action each.
3. **Statistics.** Test counts and imbalance, compound × size coverage, the
   accuracy ceiling set by repeat scatter, a fair split of the variation into
   dry/wet, conditions, test day, tyre and repeat scatter, condition effects within
   the same tyre, and correlations per configuration.
4. **Test-day correction.** A reference tyre run in many separate blocks is
   detected; its drift between sessions is measured, and day-corrected landmarks are
   produced (assumes Sl No follows test order).
5. **Pacejka.** Magic Formula fitted on slip 0–0.90 (free, and with C fixed per
   surface), fit quality, and how repeatable each parameter is between repeat tests.
   µ lock stays a separate landmark because real curves drop sharply as the wheel
   locks, which the Magic Formula cannot follow.
6. **Honest evaluation.** Three meanings of "untested":
   **A** tested tyre at a new condition, **B** known compound in a new size,
   **C** new compound. Each has its own train/test split; a random split is shown
   only to expose how much it overstates accuracy. Six models × three ways of
   handling unbalanced sizes (as is, sizes re-weighted, repeats averaged) are
   compared per case and landmark, with 90% prediction ranges whose coverage is
   checked.
7. **Curves.** Three ways of predicting a whole curve are compared on hidden tyres:
   landmarks → Magic Formula, B D E directly, and learned curve shapes.
8. **Predictor.** For each new tyre it picks the case, the best model for that case,
   and returns landmarks, ranges, B C D E and the curve; all agree exactly.

## Try it on synthetic data

```bash
python -m tyretraction demo-data demo_data      # 543 tests in the real file format, with planted problems
python -m tyretraction analyse --master demo_data/master.xlsx --curves demo_data/curves --out out_demo
```

`docs/example_report.html` is the report from this synthetic data.

## Tests

```bash
python -m pytest tests
```
