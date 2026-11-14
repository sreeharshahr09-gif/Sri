# Sidewall deflection analysis

Reads the lab's **Load Deflection Report** workbooks (.xlsx) and works out how
the tire sidewall moves under load: bulge, slide toward the rim, tilt, bending
strain, and where along the sidewall each happens. One file or hundreds;
single tire or side-by-side comparison.

## Browser tool (no install)

Open **`Sidewall_Deflection_Tool.html`** (repo root) in Chrome or Edge.
Choose or drop one or many report files. Everything runs inside the page; files
are never uploaded and it works offline.

- **Single tire**: headline numbers, the sidewall shape coloured by deflection
  (blue = less, red = more; colour by outward push, slide toward rim, tilt or
  bending), bulge / tilt / bending along the sidewall, zone breakdown. Every
  chart has a plain-English "What it shows / How to read it / For this tire" box.
- **Analysis range**: the sidewall start (rim side) and the shoulder edge in
  each scan are found automatically and can be changed per tire by dragging the
  dashed lines or typing a height. Adjusted tires are marked everywhere.
- **Compare tires**: sortable table, overlays, pick-a-metric bar chart, CSV export.
- **Curve smoothing** changes only how curves look; faint lines keep the detail
  and the numbers do not change.

Source: `web/engine.js` (file reading + analysis) and `web/app.html` (UI).
Rebuild the single file after editing either:

```bash
python sidewall/web/build.py
```

## Command line (batch)

```bash
pip install -r requirements.txt
python -m sidewall data/ --out output/batch
python -m sidewall report1.xlsx report2.xlsx --out output/pair
python -m sidewall report.xlsx --edge-inflated 225 --z-start 15
```

Per file: `metrics.json`, `zones.csv`, `matched_profile.csv`, `raw_scan.csv`,
`overview.png`, `data_quality.png`. With 2+ files: `comparison.csv` (same
columns as the browser tool's export) and `comparison.png`.

## How the file is read

The scan is not on the visible sheet. It is the datasheet of the embedded
MS Graph chart: row 0 = Laser Distance (mm), row 1 = Z Position (mm), ~700
points. The stage scans one way with the tire inflated, reverses, and scans
back with the load on. Test details (size, load, pressure, lab radial and
horizontal deflection) come from the sheet. Two-row pastes (tab-separated)
and two-column x,z text files also work.

## Method

1. **Clean**: split at the turnaround; drop dropouts (300.15) and repeated
   readings (the laser updates slower than Z is logged).
2. **Shoulder travel**: the textured band at the top of the scan is the shoulder.
   Its texture lines up between the two scans after a pure vertical shift; that
   shift is how much the sidewall span shortened. The lab radial deflection
   minus this is the part taken by the tread/crown.
3. **Smooth**: profiles lightly (5 mm); slope and curvature over 45 mm. Shorter
   windows pick up lettering texture rather than bending.
4. **Follow the material**: cords make the sidewall almost inextensible, so the
   same distance along the sidewall from the rim end is the same rubber in both
   scans. Displacement, tilt and curvature change are compared that way, not at
   equal scanner height.
5. **Zones**: thirds of the sidewall from the rim: share of bulge, share of
   bending (integral of curvature change squared), tilt, and surface strain =
   (rubber over ply) x curvature change.

The browser engine is a port of `analysis.py`; both give the same numbers
(checked to 1e-11 on the sample files, automatic and manual ranges).

## Things to confirm per test

- Rim end of the scan (`--rim-end`, default: low Z end) - set by the rig.
- Rubber thickness over the ply for strain (`--h-mm`, default 5 mm).
- About 15-25 mm of sidewall just under the shoulder folds out of the laser's
  view when loaded (`hidden_sidewall_when_loaded_mm`); values in the top part
  of the charts are less certain.
