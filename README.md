# 2W Tread Pattern Evaluation Tool

Takes a rolled-out (2D unwrapped, 360°) two-wheeler tread pattern and evaluates,
as a function of **both** circumferential rotation angle θ **and lean angle γ**,
how much of the pattern is in contact and how stiff that contact is — so that
pitch-sequence-induced flaws (uneven stiffness, noise-prone pitch orders,
shoulder imbalance) can be caught before a mould is cut.

There are two ways to use it, sharing one verified compute core:

1. **`tread_tool.html` — a standalone browser app.** Open the file in a browser,
   load a DXF, type the inputs, drag the contact-patch shape, and the charts
   update in place. No server, no install, no network — everything runs locally
   in a Web Worker. This is the interactive tool, the counterpart to the v6.4
   stiffness tool.
2. **`build_report.py` — a command-line report generator.** Give it a config
   file or flags and it writes a static HTML report. Good for batch runs, CI,
   and as the test oracle the browser app is verified against.

The browser app's engine (`app/engine.js`) is a JavaScript port of the Python
pipeline, cross-checked against it to a tight tolerance
(`tests/test_browser_engine.py`), which is in turn verified against the v6.4
reference — so the numbers the page shows are the numbers the test suite checks.

### What rests on what

| | Status |
|---|---|
| **Block stiffness** | **Verified.** Okonieski et al. (2003) beam mechanics + Gent (1959) compression, ported from *Tread Pattern Stiffness Estimation Tool v6.4* and checked against that tool's own JavaScript over randomised geometry — agreement to ~1 part in 10¹¹. |
| **Tread geometry** | **Real**, when a tread-plan DXF is imported. Depth (NSD, draft, sipes) is not in a 2D drawing and must be supplied; it is recorded as an assumption. |
| **Contact patch** | **Your choice.** Import a measured footprint of any shape, generate one from load + inflation pressure, or let the crown-based model produce it. Every lean angle is labelled with which of those it used. |
| **Rasterisation, FFT sweep, zoning, orders, centroid split, groove geometry** | Exact given their inputs — no fitted constants. |
| **Crown profile** | Assumed (dual-radius) unless a measured cross-section is supplied. |
| **Flag thresholds** | Uncalibrated. The *ranking* between designs is trustworthy; a single verdict is not. |

The report states this per-run in its own header, so it never claims more than it has.

## The standalone browser app (`tread_tool.html`)

The quickest way to try it: build the single file and open it.

```bash
pip install -r requirements.txt   # numpy, scipy, plotly (build-time only)
python build_app.py               # writes tread_tool.html (self-contained)
```

Then open `tread_tool.html` in any modern browser (double-click it). Load your
own tread-plan DXF with **Load DXF…**, or click **Load sample** to try the
bundled Tramplr plan. Set the block depth / draft / sipes / compound on the
left, pick a contact-patch shape and drag its handles, then **Run** — the sweep
runs in a background Web Worker (about 2–4 s) and every chart updates in place:
the θ sweep with the rolled-out pattern beneath it on the same angle axis, the
θ×γ lean map, order content, zone balance, the contact patch, and a diagnostics
table. A dark/light toggle and a built-in *How to read this* guide are included.

Nothing is uploaded; the file works offline from `file://`.

## Running it on your own tyre (command-line report)

The `build_report.py` path is a command-line program: you give it inputs, it
writes a static HTML **report** — there is no file-upload box inside that
report (for the interactive box, use `tread_tool.html` above).

The easiest way in is a config file holding every input:

```bash
pip install -r requirements.txt

python build_report.py --write-example-config my_tyre.json
```

That writes a commented `my_tyre.json`. Point `pattern.dxf` at your drawing, set
the non-skid depth and the rest, then:

```bash
python build_report.py --config my_tyre.json
```

Everything is in one file you can keep beside the design, diff, and re-run. Use
`--show-config` to print the effective settings without running.

### Or straight from the command line

```bash
python build_report.py --dxf my_plan.dxf --nsd 8.5 --draft 3 --sipes 1
```

Flags override the config, so the two mix freely. `--save-config out.json` turns
a working command line into a reusable file:

```bash
python build_report.py --dxf my_plan.dxf --nsd 8.5 --save-config my_tyre.json
```

### The inputs

| Config | Flag | What it is |
|---|---|---|
| `pattern.dxf` | `--dxf` | your tread-plan DXF |
| `pattern.nsd_mm` | `--nsd` | non-skid depth (block height) — **not in a 2D drawing, you must supply it** |
| `pattern.draft_deg` | `--draft` | mould draft angle |
| `pattern.lateral_sipes` | `--sipes` | sipes per block (or `sipes_by_zone`) |
| `pattern.n_pitches` | `--n-pitches` | override the inferred pitch count |
| `pattern.dxf_layers` | `--dxf-layers` | import only these DXF layers |
| `tyre.crown_r_center_mm` / `_shoulder_mm` | `--crown-r-center` / `-shoulder` | crown radii — these set where the patch sits at each lean |
| `tyre.crown_section_csv` | `--crown-section` | a measured cross-section instead of the radii above |
| `compound.shore_a` | `--shore` | tread compound hardness |
| `load.vertical_N` | `--load` | vertical load |
| `contact_patch.*` | `--cp*` | see [Contact patch](#contact-patch) |
| `analysis.lean_angles` | `--lean` | lean angles to sweep |
| `analysis.resolution_mm` | `--resolution` | raster resolution |
| `output.path` | `-o` | where to write the report |

`--help` lists every flag. Unknown keys in a config file are an error rather
than a silent no-op, so a typo stops the run instead of being ignored.

Every value the run used is recorded in the report's **About** tab, so a report
always says how it was produced.

```bash
python build_report.py                 # no inputs: synthetic pattern, generated patch
python -m pytest tests/ -q             # includes the stiffness cross-check
```

Open `out/tread_report.html` in any browser. The terminal also prints a summary
table and the flag list, which is enough to compare design variants in CI.

**New to the charts?** [`GUIDE.md`](GUIDE.md) explains every graph and number in
plain language — what it shows, how to read it, and what good and bad look like.
The same guide is embedded in the report as the **How to read this** tab, so it
travels with the file.

```bash
python build_report.py --lean 0,15,30,45 --resolution 0.25          # finer sweep
python build_report.py --curvature-correction                       # digitised competitor pattern
python build_report.py --shore 65 --boundary free                   # compound / boundary condition
python build_report.py --cp-model rhyne --inflation 250             # footprint from inflation pressure
python -m pytest tests/ -q                                          # includes the stiffness cross-check
```

## What it computes

| | |
|---|---|
| **Contact area vs. θ** | rubber area actually inside the patch, at every rotation position, for each lean angle |
| **In-contact stiffness** | per-block Kx/Ky aggregated over the blocks in the patch, weighted by how much of each block is inside |
| **Zoned land/sea** | land ratio and mass per unit circumference by lateral bin and by zone (shoulder / intermediate / centre) |
| **Order content** | FFT of area(θ) onto rotation orders, plus the pitch sequence's own spectrum and its spectral concentration |
| **Blocks in contact vs. lean** | the headline 2W diagnostic — the patch shrinks with lean, holds fewer blocks, averages less, ripples more |
| **Patch shape vs. lean** | length, width, area, compactness, aspect ratio, pressure skew, zone share |
| **Centroid wander** | split into the *geometric* part (lean + crown put it there) and the *residual* part (what the pattern adds); only the residual is actionable |
| **Groove angle** | measured against the **instantaneous travel direction**, not the tyre centreline, with the spin-induced swing that lean introduces |
| **Pitch sequence** | length histogram, modulation, adjacent-step ratio, spectral concentration, left/right shoulder phase |
| **Wear proxies** | block slenderness, sipe density, in-contact block-count fluctuation |
| **Flags** | each of the above scored against a threshold and ranked by severity |

## How it works

```
synthetic.py ─┐
              ├─► Pattern (schema.py) ─► raster.py ─► sweep.py ─► metrics.py ─► report.py ─► one HTML file
 (DXF parser) ┘                              ▲            ▲
                                     stiffness.py   contact_patch.py
```

**Everything downstream of `Pattern` is source-agnostic.** The synthetic
generator and any future DXF parser both emit the same schema, so nothing else
in the pipeline needs to know which one produced the geometry.

### The sweep is one FFT, not a loop over angles

The pattern is rasterised once into a stack of per-pixel weight maps (rubber
area, per-zone area, Kx density, Ky density, per-block area fraction, lateral
moment). For any weight map `W` and contact-patch kernel `K`, the in-patch
aggregate at every rotation position is a circular cross-correlation:

```
c[j] = Σ_{x,y} W[y,x]·K[y,x−j]  =  irfft( rfft(W) · conj(rfft(K)) ).sum(over rows)
```

One FFT pair gives all `nx` rotation positions at once, and the map transforms
are cached across the lean sweep because only the kernel changes with γ. A
default run — 5.7k × 450 px raster, five lean angles, full 360° at 0.35 mm
resolution — takes about 8 seconds. `tests/test_pipeline.py` checks the FFT
result against a brute-force masked sum, because the whole speed argument rests
on that equivalence.

Per-block questions the correlation *cannot* answer ("is this block more than
half in contact?") are done separately by windowed label counting over only the
columns the patch spans.

### Coordinates

`x` is circumferential in mm, wrapping at the circumference. `y` is lateral in
mm from the tread centreline — **developed (arc-length) width**, which is what a
flattened mould drawing gives you. Blocks may cross the seam; the rasteriser
splits them.

The crown profile is stored parametrised by that same arc length, as tangent
angle `φ(y)`, drop `z(y)`, projected position `y_proj(y)` and local radius
`r(y)`, with `dφ/dy = 1/r`. Keeping one curve with arc length as the parameter
is what makes its three consumers agree: the lean model inverts `φ`, the
curvature correction is `1/cos φ`, and the contact model reads `r`.

## Block stiffness

`tread_eval/stiffness.py` is a port of `effectiveK()` / `computeKz()` from
*Tread Pattern Stiffness Estimation Tool v6.4*:

- **Shear (Kx, Ky, Kxy)** — Castigliano slice integration along the block
  height. At each height the draft-tapered cross-section contributes a bending
  compliance weighted by the local moment arm and a shear compliance from its
  area; the 2×2 inverse gives the stiffness matrix, so `Ixy` couples the two
  directions. Sipes split the block into sub-blocks acting as parallel springs
  within a layer, and layers stack in series up the height.
- **Compression (Kz)** — Gent shape factor `S = A_net/(NSD·P)`, effective
  modulus `E_eff = E(1 + 2kS²)` with the bulk-compressibility correction, then
  `Kz = E_eff·A/NSD`.
- **Compound** — Shore A maps to E and the Gent k through Gent's Table 8.1.

**NSD is non-skid depth** — the block height in mm. It is `Block.height`.

### Verifying the port

`verify/tool_v64_reference.js` holds those functions extracted verbatim from the
HTML tool. `tests/test_stiffness_vs_tool.py` executes them with node and
compares against the Python over randomised geometry — prismatic, drafted,
siped, and drafted-and-siped — plus `polygonProps`, `offsetPoly` and the Shore
tables. Worst observed relative error is ~1e-11. If node is absent the tests
skip and say the port is unverified.

```bash
python -m pytest tests/test_stiffness_vs_tool.py -q -s   # prints the worst error per quantity
```

One deliberate inheritance: the reference clips sipe lines to the polygon with
Liang–Barsky, which treats the block as convex. For a concave block that
slightly over-reports the sipe slot length, so `Kz` is a little conservative
there. Kept as-is so the two implementations agree; the shear path does a proper
polygon split and is unaffected.

## Contact patch

The patch is an **input**, not a hidden assumption, and it can be **any closed
shape** — the sweep only rasterises the outline, so a traced footprint with
ragged edges behaves exactly like a generated ellipse.

### Sources, in order of trust

| Source | What it is |
|---|---|
| `measured` | A footprint you imported at that lean angle: outline and/or pressure map. |
| `interpolated` | Two measured footprints bracket that lean angle; shapes are morphed on a common radial parametrisation. |
| `transferred` | A measured footprint at *another* lean angle, rescaled by the length/width/centroid trend the model predicts. **The measured shape is kept; only the change with lean is modelled.** |
| `rhyne` | Generated from load and inflation pressure — the same relations as `computeContactPatch()` in the stiffness tool, with a `2w` row added. |
| `winkler` | Generated from the crown profile by elastic-foundation contact. The only source that is lean-native from first principles, hence the last-resort default. |

`CPLibrary.patch_for(gamma)` applies that order and records which rung it landed
on. The report's **Contact patch** tab shows the outline and provenance of every
lean angle, and never draws a generated patch the same way as a measured one.

**Why this matters:** a static upright footprint is easy to capture; a leaned one
needs a rig almost nobody has. Importing a single upright footprint upgrades
*every* lean angle from `generated` to `transferred`.

### Standard shapes, with your own dimensions

If you know the footprint's *dimensions* but not its outline, state a shape:

```bash
python build_report.py --cp-shape rounded --cp-length 92 --cp-width 50 --cp-corner-radius 12
```

`rectangle`, `rounded`, `stadium`, `ellipse`, `superellipse`, `trapezoid`,
`diamond`. `length` is circumferential, `width` lateral; every shape also takes
`y_center`, `rotation` and `load_N`. In a config file you can give one per lean
angle and the tool interpolates between them:

```json
"contact_patch": {
  "shapes": [
    {"gamma_deg": 0,  "shape": "rounded",   "length": 92,  "width": 50, "corner_radius": 12},
    {"gamma_deg": 40, "shape": "trapezoid", "length": 105, "width": 34, "taper": 0.25}
  ]
}
```

Editing numbers and re-running is the way to explore: the sweep is a full
recompute (a few seconds), not a live redraw, so shapes are not draggable in the
report.

### Importing a measured footprint

```bash
# an outline: .csv of x,y in mm, or .json, or .dxf (largest closed loop)
python build_report.py --cp footprint_00deg.dxf --cp-gamma 0 --cp-load 1500

# a pressure map from film or FEA
python build_report.py --cp pressure_00deg.csv --cp-pressure --cp-pressure-dx 0.5 --cp-pressure-dy 0.5

# several lean angles via a manifest
python build_report.py --cp footprints.json
```

**Lateral placement.** A footprint traced in CAD has an arbitrary origin. The
circumferential origin never matters (the outline is re-centred), but the
lateral one does. By default (`--cp-lateral auto`) an outline that does not fit
on the tread is re-centred and the tool says so. For a **leaned** footprint the
lateral position is real information — pass `--cp-y` or `--cp-lateral absolute`.

Manifest format:

```json
{ "units": "mm", "pressure_units": "MPa",
  "patches": [
    {"gamma_deg": 0,  "outline": "fp_00.csv", "load_N": 1500},
    {"gamma_deg": 25, "pressure": "fp_25.csv", "load_N": 1650}
  ] }
```

`--no-cp-transfer` disables rescaling if you would rather see the generated
patch than a derived one. `--cp-units` handles cm/inch files.

### The Winkler generator

`u = δ − x²/2R_eff − (y−y_c)²/2R_lat` makes the patch an ellipse with
`a = √(2·R_eff·δ)`, `b = √(2·R_lat·δ)`; requiring the pressure to carry the load
closes it: `δ = √(Fz / (k_f·π·√(R_eff·R_lat)))`. Lean enters geometrically: the
contact point walks to where the tread tangent is horizontal, the much smaller
lateral radius there narrows the patch, and the load rises as `Fz/cos γ`. The
patch is clipped at the tread edge, and the clipping is reported.

**Travel direction.** A leaning tyre corners, so the patch spins about the
vertical at `V/R_path`. A point `x` ahead of the centre travels at
`atan(x/R_path)` to the centreline — which is why groove angles are scored
against travel, and why the groove chart shows a swing band that is zero upright
and several degrees at lean.

## DXF import

```bash
python build_report.py --dxf tread_plan.dxf --nsd 8.5 --draft 3 --sipes 1
```

A tread plan exports as a soup of LINE/ARC entities with no polygon structure,
so `tread_eval/dxf.py` stitches segments into chains by shared endpoints, closes
blocks that wrap across the seam by translating one half a full circumference,
drops construction geometry, and shifts the lateral origin to the centreline.
No `ezdxf` dependency — the entity subset a tread plan needs is small.

It also reports two things the drawing does not state outright:

- the **geometric repeat** (smallest circumferential shift that maps the layout
  onto itself), which is not the same as the pitch spacing when lateral bands
  are staggered;
- whether the drawing is a **uniform array** — if every repeat is identical, the
  drawing carries no pitch modulation, and the order analysis will show the
  single-order tone of an array rather than the real tyre.

Depth attributes are not in a 2D drawing. `--nsd`, `--draft` and `--sipes` (or
per-zone values via `BlockDefaults`) supply them, and they are recorded in
`Pattern.meta['assumed']` and shown in the report.

### Remaining swap-in points

| Assumed | Where | Replace with |
|---|---|---|
| Crown profile | `CrownProfile.dual_radius()` | `CrownProfile.from_cross_section(y_projected, z)` from a measured section |
| Flag thresholds | `tread_eval/metrics.py` → `THRESHOLDS` | values calibrated against measured noise/wear |
| `2w` Rhyne constants | `tread_eval/contact_patch.py` → `_RHYNE_RATIO`, `_RHYNE_K_WC` | fitted to one static footprint |

## Curvature correction

Off by default. A mould DXF of your own tyre is already correctly developed, so
applying an arc-length factor would double-count. Turn it on
(`--curvature-correction`) for competitor patterns digitised flat, where the
shoulder is compressed by the projection: the correction multiplies lateral
pixel areas by `1/cos φ(y)`, which is 1.0 at the crown and >1.2 at the shoulder.

Ink-and-paper digitisation is not a target of this build, but nothing in the
schema precludes it. `Pattern.validate()` already reports the pitch-sequence
closure error, which is exactly how stitching distortion announces itself.

## Layout

```
tread_eval/
  schema.py         Pattern / Pitch / Block / Sipe / CrownProfile — the source-agnostic contract
  dxf.py            tread-plan DXF import (production geometry source)
  synthetic.py      placeholder pattern generator, for when there is no DXF
  stiffness.py      Okonieski/Gent block stiffness — verified against the v6.4 tool
  contact_patch.py  patch of any shape: measured, interpolated, transferred, generated
  cp_io.py          importing footprints (outline / pressure map / manifest)
  raster.py         polygon fill, seam splitting, weight maps, curvature correction
  sweep.py          the FFT θ×γ sweep
  metrics.py        diagnostics and flags (THRESHOLDS live here)
  summary.py        terminal summary
  config.py         the config file: every input, one place
  cp_shapes.py      standard patch shapes (rectangle, rounded, ellipse, ...)
  report.py         payload assembly + HTML rendering
  markdown.py       minimal Markdown -> HTML, so GUIDE.md stays single-source
  assets/           template.html, app.css, app.js
build_report.py     CLI
GUIDE.md            plain-language reader's guide (also embedded as a report tab)
verify/             functions extracted verbatim from the v6.4 HTML tool
data/               sample tread plan and footprint
tests/              geometry, pipeline, DXF/CP, and the stiffness cross-check
out/                generated reports
```

`plotly` is a **build-time** dependency only — the report embeds its bundle, so
the HTML itself needs nothing installed and no network.

## Decisions taken where the brief left them open

- **Lean increments** default to `0, 10, 20, 30, 40` degrees. The default crown
  profile supports lean to 45.6°; asking for more pins the contact point at the
  tread edge, and the tool says so rather than silently clamping.
- **Zone boundaries** are fraction-of-width (34% / 72% of the half width), not
  fixed mm bands, so the zoning generalises across tyre sizes. One function,
  `schema.zone_bounds()`, if that is ever revisited.
- **Lean-specific footprints as direct input** are already possible via
  `contact_patch_from_footprint()` and `sweep_lean(..., patch_override=...)`.

## Sample data

`data/130_80R17_Tramplr_XR_tread_plan.dxf` — a real tread plan: 2193.40 mm
circumference × 159.00 mm developed width, 168 blocks (3 wrapping the seam),
land ratio 0.690. Its geometric repeat is 182.784 mm arrayed 12 times, and every
repeat is identical, so the drawing carries no pitch modulation.

The synthetic generator defaults to the same 182.78 mm repeat for comparison.

## Interface

The report is one self-contained HTML file with a dark/light/auto theme toggle
(remembered per browser, following the OS in auto). Chart colours are read from
the stylesheet at draw time, so both themes are defined in exactly one place.
