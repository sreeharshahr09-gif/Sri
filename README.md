# Tyre Cross-Section Analysis Tool

Takes 1–5 2D DXF tyre cross-section profiles (exported from AutoCAD), extracts
per-component geometry, and computes **area, centroid, volume (Pappus), surface
area, weight, and polar moment of inertia** — with a Streamlit UI for comparing
design iterations (baseline vs. modified profiles) to support weight-reduction
work.

See [`CLAUDE.md`](CLAUDE.md) for the full design brief and rationale.

## Install & run

```bash
pip install -r requirements.txt

# Generate conforming demo profiles
python scripts/make_sample_dxf.py

# Launch the UI
streamlit run app.py
# → upload sample_data/baseline.dxf and sample_data/iteration1.dxf

# …or use the headless CLI
python -m tyre_analysis validate sample_data/*.dxf
python -m tyre_analysis analyse --axis-x 0 sample_data/baseline.dxf sample_data/iteration1.dxf -o results.csv
```

## ⚠️ Required CAD convention (read this first)

The tool identifies components **by layer name** and computes on **closed
curves**. Each DXF must therefore have:

- **One component per named layer** — `TREAD`, `SIDEWALL`, `APEX`, `BELT-1`,
  `PLY-1`, `BEAD`, … (layer name = component name).
- **One closed curve per component** — an `LWPOLYLINE` (bulges for arcs) or
  closed `SPLINE`. Use AutoCAD `BOUNDARY` to close raw line/arc geometry.
- **Raw curves, not REGIONs** — REGIONs serialise as ACIS blobs that
  ezdxf/shapely cannot read.
- DXF **R2000 (AC1015) or later**; units in **mm**.

> **Note on the provided sample (`sample_data/real_sample_1.dxf`).** That real
> export does **not** follow this convention: every entity is on layer `0` as
> open lines/arcs/splines (no component layers, nothing closed). The tool
> correctly reports it as non-conforming and refuses to produce numbers for it.
> To use real drawings, prepare them per the convention above. Run
> `python -m tyre_analysis validate <file>.dxf` to see exactly what to fix.

## Two ingestion paths

1. **Layer-based (main page)** — for files that follow the convention above:
   one closed curve per named layer. Fast and unambiguous.
2. **Manual loop builder (`pages/1_Manual_Loop_Builder.py`)** — for raw drawings
   that *don't* (e.g. the provided sample: a full section, everything on layer
   `0`, all curves open). It reconstructs the geometry, auto-detects candidate
   closed **faces** (`shapely.polygonize`, the same set AutoCAD `BOUNDARY`
   "pick internal point" would offer), and lets you **lasso/click the faces for
   each component and name them**. It auto-suggests the rotation axis (the drawn
   centreline / mid-section) and works one symmetric half at a time so Pappus
   distances stay correct. Assignments can be saved/loaded as JSON.

   > The provided `real_sample_1.dxf` reconstructs into a full tyre section about
   > x ≈ 176 with ~14 candidate faces per half — the big face is the air cavity
   > (skip it); the thin bands are the components.

## What it computes (per component)

| Quantity | How |
|---|---|
| Area, centroid | shapely, always `abs(area)`, winding-order normalised |
| Volume | Pappus 1st: `V = 2π·d·A`, `d` = area-centroid distance from axis |
| Surface area | Pappus 2nd: `S = 2π·d_arc·L`, `d_arc` = **boundary-curve** centroid (arc-length weighted), `L` = perimeter |
| Weight | `volume_cm³ × specific_gravity` (case-insensitive layer lookup) |
| Polar MOI | Mass moment of the revolved solid about the axis: `I = 2π·ρ·∫r³dA` (Green's-theorem polygon sum) |
| Radius of gyration | `√(I / mass)` |

All results land in one long tidy dataframe (`tyre_analysis.pipeline.COLUMNS`);
every comparison/total/delta is a groupby/pivot on that single table.

The math is validated analytically against a revolved rectangle (= hollow
cylinder, whose volume, surface, polar MOI and radius of gyration all have known
closed forms) — see `tests/test_geometry.py`.

## UI features

- **Conformance gate** — non-conforming files are rejected with an actionable message.
- **Tire-level summary** — total weight, total polar MOI, CG offset from axis, per file.
- **Component comparison table** — magnitude-scaled delta heatmap (green/red
  direction configurable per component), amber flag on centroid shifts beyond tolerance.
- **Graphics** — cross-section overlay, centroid map with shift arrows, polar-MOI
  bar chart (kept separate from weight), and a weight-change waterfall.
- **Parametric sensitivity** — quick uniform gauge-offset (`buffer`) or area-scale
  what-ifs (explicitly *not* a substitute for redrawing complex shape changes).

## Layout

```
tyre_analysis/
  dxf_ingest.py   DXF → flattened closed loops (tolerance-based, not fixed-nseg)
  geometry.py     area/centroid, boundary centroid, ∫r³dA, hole & winding handling
  physics.py      Pappus volume/surface, weight, polar MOI, radius of gyration
  validate.py     conformance checking (the non-conforming-file gate)
  pipeline.py     files → one long dataframe
  reporting.py    comparison tables + Plotly figures (no Streamlit)
  sg_table.py     specific-gravity lookup (PLACEHOLDER values — replace)
  cli.py          headless validate/analyse
app.py            Streamlit UI
scripts/          conforming sample-DXF generator
tests/            analytic + round-trip tests
```

## Open items (need your input)

- **Real SG values** per compound — `sg_table.py` ships placeholders.
- Confirm the centroid-shift amber tolerance (default 1 mm).
- **Manual-builder cross-file reuse**: assignments save as face ids tied to one
  file + reconstruction params. Reusing them across design iterations (where
  face ids differ) would need matching by representative point instead — easy to
  add if you need to build many iterations quickly.
