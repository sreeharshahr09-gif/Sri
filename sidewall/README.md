# Sidewall deflection analysis

Reads the lab's **Load Deflection Report** workbooks and works out how the tire
sidewall moves under load: bulge, slide toward the rim, tilt, bending strain,
and where along the sidewall each happens. One file or hundreds; single tire or
side-by-side comparison; PDF and HTML reports.

## Browser tool (no install)

Open **`Sidewall_Deflection_Tool.html`** (repo root) in Chrome or Edge. Choose or
drop one or many files. Everything runs inside the page; files are never
uploaded and it works offline.

- **Single tire**: headline numbers, the sidewall shape coloured by deflection
  (blue = less, red = more; colour by outward push, slide toward rim, tilt or
  bending), bulge / tilt / bending along the sidewall, zone breakdown. Every
  chart has a plain-English "What it shows / How to read it / For this tire" box.
- **Test details**: check or fill in size, load, pressure, radial deflection etc.
  for any tire (e.g. when a file's labels were not recognised). Every number updates.
- **Scan as recorded**: the full laser record (distance vs Z), like the lab's own
  chart, with lost readings shown apart, so you can see exactly what is analysed.
- **Analysis range**: the rim-side starting point in each scan and the tread-side
  end are found automatically and can be changed per tire by dragging the dashed
  lines on the "scan as recorded" chart or typing a height.
- **Orientation**: every chart, in the tool, the reports and the Python figures,
  puts the rim at the top and the tread / flat contact at the bottom.
- **Compare tires**: sortable table (with load, pressure and rim), overlays,
  pick-a-metric bar chart, CSV export.
- **Same test conditions?** When compared tires were tested on different rims,
  loads or pressures (loads and pressures more than 2 % apart), the values are
  shown in red and a "Check:" note says which tire used what. The same notes
  appear in the HTML / PDF report and the Python batch output. Rim, load and
  pressure can be filled in or corrected under "Test details".
- **Interactive charts** (tool and HTML report): hover read-out with crosshair
  and dots, linked across a tire's charts; drag-box zoom, +/- buttons,
  Ctrl+wheel, Shift+drag pan, double-click reset; click to pin readings (table,
  copy for Excel); legend toggles; colour-by switch on the shape chart; full
  screen; data table with CSV / copy; PNG and SVG export.
- **Report**: project details form (title, project, request no., customer,
  prepared/reviewed by, date, objective, conclusions, logo), choice of tires and
  sections, then **Download PDF** (fixed, vector, for print) or **Download
  interactive HTML** (single offline file: the interactive charts above plus a
  contents bar with scroll tracking, foldable sections, sortable summary table
  whose rows highlight that tire in every chart, light/dark, print). If a viewer
  blocks scripts, the HTML report still shows every chart as a picture.
- **Paste columns**: if a file will not import, paste the laser-distance and
  Z-position columns from Excel (two boxes, or both columns in one box), with a
  preview before adding the tire.

Source files: `web/engine.js` (file reading + analysis), `web/charts.js`
(interactive charts, shared with the HTML report), `web/app.html` (UI),
`web/report.js` (reports, PDF writer), `web/report_runtime.js` (navigation inside
the HTML report). Rebuild the single file after editing:

```bash
python sidewall/web/build.py
```

## What the importer accepts

The reader looks at the file's content, not its extension, and searches
everywhere a scan can be kept:

| Where the scan is | Example |
|---|---|
| Embedded MS Graph chart datasheet (current lab template) | `xl/embeddings/oleObject1.bin` |
| Sheet columns or rows, any sheet, with or without headers | "Z Position (mm)" / "Laser Distance (mm)" |
| Native Excel chart (cached values, even with the data sheet empty) | `xl/charts/chart1.xml` |
| Old Excel format | `.xls` (sheets and embedded charts) |
| Text / CSV | tab, comma or semicolon separated; decimal commas; UTF-8 or UTF-16 |

The scan pair is picked by shape (the Z channel goes out and comes back), so
extra columns such as sample number or time are ignored. Test details are found
by label wording (e.g. "Tyre Size" / "Tire Size:", "Load (daN)" / "Load (kg)",
"Radial Deflection" / "Vertical Deflection") with units converted to daN, psi,
mm, kg and °C. Sensor dropouts are detected by their gap to the real readings
(300.15, 999, 0, blank cells...). If no scan is found, the message says where the
reader looked; use **Paste columns**.

Checked on format variants of a real report (relabelled + other units, scan on a
sheet in columns or rows, chart cache only, `.xls`, European CSV, UTF-16 text):
identical results to the original file, in both the browser and Python.

## Command line (batch)

```bash
pip install -r requirements.txt
python -m sidewall data/ --out output/batch
python -m sidewall report1.xlsx report2.xls --out output/pair
python -m sidewall report.xlsx --edge-inflated 225 --z-start 15
```

Per file: `metrics.json`, `zones.csv`, `matched_profile.csv`, `raw_scan.csv`,
`overview.png`, `data_quality.png`. With 2+ files: `comparison.csv` (same
columns as the browser tool's export) and `comparison.png`; different rims, loads or
pressures are printed as "CHECK:" lines and shown on `comparison.png`. Unreadable files are
skipped with the reason.

## Method

1. **Clean**: split at the turnaround; drop dropouts and repeated readings (the
   laser updates slower than Z is logged).
2. **Starting points and rim drop**: the rim is at the top of the scan (high Z).
   The scanner stays with the contact plate, so under load the rim moves down
   toward it. The inflated starting point is the sharp peak where the profile
   meets the rim; the loaded one is the top of the step where the loaded profile
   meets the rim band. Their difference is the rim drop seen by the scan, shown
   next to the machine's radial deflection. How well the rim-band texture lines
   up after that shift is reported as a cross-check.
3. **Smooth**: profiles lightly (5 mm); slope and curvature over 45 mm. Shorter
   windows pick up lettering texture rather than bending.
4. **Follow the material**: cords make the sidewall almost inextensible, so the
   same distance along the sidewall from the rim-side starting point is the same rubber in both
   scans. Displacement, tilt and curvature change are compared that way.
5. **Zones**: thirds of the sidewall from the rim: share of bulge, share of
   bending (integral of curvature change squared), tilt, and surface strain =
   (rubber over ply) x curvature change.

The browser engine is a port of `analysis.py` / `readers.py`; both give the same
numbers (checked to 1e-11 on all sample and variant files).

## Things to confirm per test

- The rim must be at the high-Z end of the scan, as in the lab rig.
- Rubber thickness over the ply for strain (`--h-mm`, default 5 mm).
- About 20 mm of sidewall at the tread end rolls into the contact and out of the
  laser's view when loaded (`hidden_sidewall_when_loaded_mm`); values at the
  tread-side end are less certain.
- The rim drop in the scan can differ from the machine's radial deflection by a
  few mm (preload, or the scan plane not being at the contact centre).
