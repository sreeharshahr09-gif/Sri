# 2W Tread Pattern Evaluation Tool

Takes a rolled-out (2D unwrapped, 360°) two-wheeler tread pattern and evaluates,
as a function of **both** circumferential rotation angle θ **and lean angle γ**,
how much of the pattern is in contact and how stiff that contact is — so that
pitch-sequence-induced flaws (uneven stiffness, noise-prone pitch orders,
shoulder imbalance) can be caught before a mould is cut.

Output is a single self-contained interactive HTML file: no server, no network,
no build step to view it.

> **This is a baseline / proof-of-pipeline build.** Real tread geometry and the
> real stiffness formula are not available yet, so three models are placeholders.
> Each is isolated behind one function so it can be swapped without touching
> anything else — see [Swap-in points](#swap-in-points). The pipeline around them
> (rasterisation, the FFT sweep, zoning, order analysis, centroid decomposition,
> groove-angle geometry) is exact given its inputs.

## Quick start

```bash
pip install -r requirements.txt
python build_report.py                 # writes out/tread_report.html
```

Open `out/tread_report.html` in any browser. The terminal also prints a summary
table and the flag list, which is enough to compare design variants in CI.

```bash
python build_report.py --pitch-mode tonal_group -o out/tonal.html   # a deliberately tonal sequence
python build_report.py --lean 0,15,30,45 --resolution 0.25          # finer sweep
python build_report.py --curvature-correction                       # digitised competitor pattern
python build_report.py --pattern my_pattern.json                    # analyse a saved Pattern
python -m pytest tests/ -q
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

## Swap-in points

| Placeholder | Where | Replace with |
|---|---|---|
| Synthetic pitch/block generator | `tread_eval/synthetic.py` → `generate_pattern()` | a DXF parser emitting the same `Pattern` schema |
| Geometric Kx/Ky formula | `tread_eval/stiffness.py` → `block_stiffness()` | the Winkler beam-mechanics model (Okonieski et al. 2003) from the existing block-stiffness HTML tool |
| Parametric CP-vs-lean model | `tread_eval/contact_patch.py` → `contact_patch()` | measured footprint-vs-lean data, fed through `contact_patch_from_footprint()` which bypasses the parametric model entirely |
| Guessed crown profile | `CrownProfile.dual_radius()` | `CrownProfile.from_cross_section(y_projected, z)` from a measured section |
| Flag thresholds | `tread_eval/metrics.py` → `THRESHOLDS` | values calibrated against real noise/wear correlation |

Each is one function. Nothing else in the pipeline reads their internals.

### What the placeholders actually do

**Block stiffness.** A block is a short rubber column bonded top and bottom.
Bulk shear `G·A/h` stiffened by the bonded-pad shape factor `(1 + 2kS²)`, in
series with cantilever bending `3EI/h³`, using the polygon's own second moments
so the result is directional. Draft angle tapers the load-bearing area; sipe
density (NSD) adds compliance, more in the circumferential direction than the
lateral one.

**Contact patch vs. lean.** Winkler elastic-foundation contact on a doubly
curved crown. The interference `u = δ − x²/2R_eff − (y−y_c)²/2R_lat` makes the
patch an ellipse with `a = √(2·R_eff·δ)`, `b = √(2·R_lat·δ)`, and requiring the
pressure to carry the load closes it: `δ = √(Fz / (k_f·π·√(R_eff·R_lat)))`.
Lean enters geometrically, not by fitting: the contact point walks to where the
tread tangent is horizontal, the (much smaller) lateral radius there narrows the
patch, and the normal load rises as `Fz/cos γ`. The patch is clipped at the
tread edge, and that clipping is reported rather than hidden.

**Travel direction.** A leaning tyre corners, so the patch spins about the
vertical at `V/R_path`. A point `x` ahead of the patch centre travels at
`atan(x/R_path)` to the centreline — which is why groove angles are scored
against travel and not against the tyre axis, and why the groove chart shows a
swing band that is zero upright and several degrees at lean.

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
  schema.py         Pattern / Pitch / Block / CrownProfile — the source-agnostic contract
  synthetic.py      SWAP 1 — placeholder pattern generator
  stiffness.py      SWAP 2 — placeholder per-block Kx/Ky
  contact_patch.py  SWAP 3 — placeholder footprint vs. lean
  raster.py         polygon fill, seam splitting, weight maps, curvature correction
  sweep.py          the FFT θ×γ sweep
  metrics.py        diagnostics and flags (THRESHOLDS live here)
  summary.py        terminal summary
  report.py         payload assembly + HTML rendering
  assets/           template.html, app.css, app.js
build_report.py     CLI
tests/              geometry and pipeline tests
out/tread_report.html   the deliverable (regenerate with build_report.py)
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

## Defaults

Taken from the reference mould drawing supplied with the brief: a 182.78 mm
repeat arrayed 11 times (circumference 2010.58 mm) across a 159 mm developed
tread width, each repeat subdivided into three pitches — 33 pitches, 264 blocks.
