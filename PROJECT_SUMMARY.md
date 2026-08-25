# Tread Pattern Evaluation Tool — project summary

A record of what was built, what was found wrong and fixed, and where things
stand. Written at commit `d2023e1` on branch `claude/tool-building-continuation-mczgtl`.

---

## 1. What the tool is

Takes a rolled-out (2D unwrapped, 360°) tread pattern and evaluates, as a
function of **both** circumferential rotation angle θ **and** lean angle γ, how
much of the pattern is in contact and how stiff that contact is — so that
pitch-sequence flaws (uneven stiffness, noise-prone orders, shoulder imbalance)
can be caught before a mould is cut.

Two ways to use it, sharing one verified compute core:

| | |
|---|---|
| **`tread_tool.html`** | A standalone browser app, ~5.9 MB, one file. Open it, load a DXF, type inputs, drag the patch, press Run. No server, no install, no network — the sweep runs in a Web Worker. |
| **`build_report.py`** | A command-line report generator. Config file or flags in, static HTML report out. Good for batch runs and CI, and it is the oracle the browser engine is verified against. |

Build the browser app with `python build_app.py`.

---

## 2. How the session went

### 2.1 The pivot that shaped everything

The tool began as a Python program that *wrote* an HTML report. Three separate
times you went looking for input boxes inside that report, and then asked
directly:

> *"You mean to say that this is not a standalone HTML tool?"*

That was the right question. The output was standalone HTML; the **tool** was
not. Your v6.4 stiffness tool is a real browser application, and this needed to
be too. The whole compute pipeline was ported to JavaScript so the page itself
does the work — with Python kept deliberately as a test oracle, so the numbers
on screen stay tied to a verified implementation.

### 2.2 Then a four-category audit

You asked for an iterative audit in priority order: **physics → bugs → data flow
→ UI**, looping until a clean pass. That ran to eleven passes across the four
categories.

### 2.3 Then feature work

Multi-class support (2W / PCR / TBR), rib divisions, design comparison, PDF
reporting, and the designer's crosshair / linked-zoom / order-chart requests.

---

## 3. What the audit found

The striking thing: **almost none of these were crashes.** They were plausible-
looking wrong numbers. That is why the test suite grew the way it did.

### Critical — silent wrong answers

| # | Issue | Why it mattered |
|---|---|---|
| 1 | **The crown was silently ignored after import** | Crown radii were baked in at DXF load. Typing a new crown and pressing Run recomputed the *entire lean sweep* against the old one. The crown decides where the tyre touches at every lean, so the whole γ axis was wrong with nothing on screen to say so. Proof: a 300/300 mm crown can only reach 15.2°, so five of nine lean angles are geometrically impossible — all nine were being reported as valid. |
| 2 | **Non-power-of-2 FFT returned NaN** | `fftRadix2` only reaches `n` when `n` is a power of two. Any other length returned NaN, which propagated through every aggregate with no error at all. |
| 3 | **An empty input box returned `Kz: NaN`** | Clearing a field sent NaN into the compute and got NaN back, presented as a result. |
| 4 | **A DXF importing 0 blocks was accepted** | Treated as an empty tyre. Produced a full page of zero-valued charts with nothing to explain them — the most confusing way this tool could fail. |

### Major

| # | Issue | Fix |
|---|---|---|
| 5 | **The contact patch did not narrow with lean** | It held full width until it hung off the tread edge and was clipped flat. Real 2W patches narrow onto the shoulder as the crown radius collapses. Now scaled by the Winkler trend, with a toggle to opt out. |
| 6 | **The two engines disagreed on the order spectrum** | Python reported a fraction of the mean; JS reported absolute amplitude — under an identical axis label. Now matched, units stated, cross-engine test pins them. |
| 7 | **Partial sipes made blocks *stiffer*** | +2% (parallel) / +4% (free). Guided-beam compliance goes as `L³`, so stacking layers at each sipe root over-stiffens more than the sipe softens. Inherited from v6.4, so a corrected `continuous` beam model was added as an *option* — the default still matches your reference bit-for-bit. |
| 8 | **A worker death hung the page forever** | No `onerror` handler; the overlay spun with no way back. All failures now route through one exit. |
| 9 | **The "load rises with lean" checkbox didn't do that** | It pinned the patch load; the real flag came from a *hidden* input that was always checked and unreachable. |
| 10 | **Exports described the wrong run** | Settings were read live at export time, results came from the last Run. Edit NSD then export and the file claimed 4.0 mm produced numbers computed at 8.5 mm. Settings are now frozen at dispatch. |
| 11 | **No export at all** | Results could be read on screen and then only retyped. |
| 12 | **40% of sweep time was discarded work** | A pressure-weighted correlation nothing read, plus a discrete block count that was computed, shipped, and never drawn. |
| 13 | **The CLI leaked raw tracebacks** for ordinary mistakes | Missing file, bad number, unwritable path. |
| 14 | **`--resolution 0` ran silently at a 64-column grid** | Garbage resolution that looked entirely normal. |
| 15 | **A patch positioned off the tread was accepted** | Zero contact at every angle — a confident page of zeros. |
| 16 | **Tooltip coverage was 4 of 26 inputs** | Crown radius, sipe depth and boundary mode are not guessable from their labels. |

### Verified correct — checked against hand calculations, no change needed

Gent shape factor and the whole Kz chain (exact) · Castigliano index convention
(Kx uses `Iyy`, Ky uses `Ixx`) · free vs parallel end coefficients · draft sign
· Kxy antisymmetry under mirroring · parallel-axis theorem · curvature
correction `1/cos φ` · zone partition · **Winkler pressure integrating to the
stated load (rel 8e-8)** · contact point where `φ(y) = γ` · through-sipe = two
parallel sub-blocks · **a seam-straddling block bit-identical to a mid-tread
one** · load conservation `p₀·A = Fz/cos γ` (rel ≤ 1e-16).

### Judgement calls worth recording

- **Three "failures" were my test expectations being wrong, not the code**: a
  lean beyond a crown's reach, a rasterised area I had predicted from continuum
  geometry, and a monotonicity assumption that is genuinely two-regime (the load
  rise beats the radius collapse until the shoulder). I corrected the tests.
- **The nested-loop finding was narrower than it looked.** I checked before
  changing anything: the rasteriser writes land pixels idempotently, so the θ
  sweep already measured the union correctly. Only the banner's summed estimate
  over-counted. I made the banner honest rather than rewriting geometry that was
  already right.
- **The sipe artifact is deliberately still the default**, pinned by a test, so
  your v6.4 parity cannot drift silently.

---

## 4. Features

### Physics per tyre class — not just presets

Selecting 2W / PCR / TBR changes the calculation, not only the input boxes:

| | 2W | PCR | TBR |
|---|---|---|---|
| Crown break (where the shoulder starts, frac of half-width) | 0.45 | 0.72 | 0.85 |
| Max reachable lean, 250 mm tread | 46° | 10° | 4° |
| Zone split (centre / intermediate) | 0.34 / 0.72 | 0.40 / 0.78 | 0.45 / 0.82 |
| Load rises as `Fz/cos γ` | yes | no | no |
| Lean sweep | 0–40° | 0–10° | 0–8° |

The crown break matters most: it sets the tread tangent at every lateral
position, so it decides the contact point at each lean and the maximum lean at
all. The `Fz/cos γ` rise is now 2W-only — a car or truck barely cambers, and its
cornering load comes from weight transfer, which this model does not carry.

### The rest

- **Project metadata** — project, tread/design, tyre type, size, designer.
  Frozen with the run and carried into every export and the PDF cover.
- **Divisions (ribs)** — cut the tread into lateral bands, evenly spaced or at
  stated positions. Every parameter reported per band; cuts drawn on the pattern.
  Implemented by restricting the row accumulation of the *existing* correlation,
  so N bands cost N inverse transforms rather than N sweeps — and the bands sum
  to the whole-tread total **exactly**, which a test asserts.
- **In-session comparison** — *Add this design to comparison* sits under Run with
  a count badge. Load another DXF, change anything, run, add. Overlaid charts and
  a difference table; per-row remove. Loading exported JSON still works for
  designs from another day or machine.
- **Exports** — CSV (every θ×γ sample with a settings header), JSON (full run),
  text summary, and a **PDF report**: cover with project details and date,
  headline numbers, per-lean table, every chart as drawn, and
  `INTERNAL USE ONLY — Apollo Tyres` on the cover and every page footer.
  jsPDF is vendored, so the file stays offline and self-contained.
- **Crosshair and linked zoom** — a dashed line follows the cursor through every
  sweep row and down onto the pattern, with a readout of all values at that
  angle. Zoom any row and all rows plus the pattern follow; double-click resets.
- **Order-chart explanation** — a panel saying what an order is, what the
  amplitude means, what the marked bars are, and how to read the shape.
- **Mean Kx and Ky** on the dashboard cards.

### From the design-review minutes

Four items, taken in order, each finished and verified before the next started.

- **One pitch → the whole tread.** A drawing of a single pitch is replicated
  around the circumference, with an explicit pitch count or a full length
  sequence for a modulated array. Replication happens at the *chain* stage,
  before the planar arrangement, so welding, face traversal, seam wrapping and
  tie-bar detection all run on the finished tread and need no special cases.
  Pitch-boundary cut lines are stripped at interior joins — they are drawing
  artefacts there — and re-added as caps at the global seam, where a ring rib
  genuinely needs closing.
  **The whole-pitch vs groove-only scaling decision is not made by the tool.**
  Both conventions are implemented and the choice is a required input, because
  which one a modulated pitch uses is a design-office convention, not a fact
  the geometry can supply.
  A **closure diagnostic** reports whether the drawn pitch actually tiles:
  the boundary mismatch in mm, which features fail to meet, and whether the
  sequence adds up to the circumference. A pattern that does not close is
  reported, not silently welded shut.
- **Drop-first crown.** The crown can now be given the way it is actually
  dimensioned on a drawing — a lateral position and a drop — instead of as
  radii. The radii are solved from the drops (`z(L) = z₀ + R[cos φ₀ −
  cos(φ₀ + L/R)]`, strictly decreasing in R, bisected on log R), and the
  multi-arc profile itself is now integrated in **closed form** with the arc
  breakpoints as grid nodes, which removed a 35 µm edge-drop error the old
  trapezoidal integration carried. Arcs and drops round-trip to fourteen
  digits. Giving both at once is refused rather than silently ranked.
- **Report fixes and an interactive review pack.** Section chips choose what
  goes into the PDF; the aspect ratio of every captured figure is preserved in
  both axes instead of only in height; the crown survives the settings snapshot
  (it did not, so every export said "no crown resolved"); and there is now an
  **interactive review pack** — a self-contained HTML file with live, zoomable
  charts, for the reviewers who wanted something better than a flat PDF.
- **A measured contact patch.** *Shape → measured* imports a real footprint from
  DXF (largest closed loop) or CSV (two numeric columns), sharing its conventions
  with the Python pipeline and checked file-for-file against it. File units,
  lateral placement and the lean the footprint was taken at are all explicit
  inputs, because each is silent and expensive if wrong. The outline then goes
  through the identical pipeline — the only thing that changes is the shape.
  Lean scaling switches itself off on import, with a reason: applying the
  Winkler narrowing trend to a shape you measured is modelling on top of a
  measurement. On the sample tyre the measured outline gives 4147 mm² against
  4335 mm² for the idealised shape, and 1.88% area fluctuation against 1.55%.

### Tie bars read from a coloured HATCH

Ported from the designer's own v2.3 build, then extended where the new code
around it demanded it.

A tie bar has no outline of its own, so the tool has always had to guess from
area and adjacency which regions were bars. A **filled HATCH on a `TIEBAR`
layer** is not a guess — it is the designer saying so — and it is now read
directly, in both DXF boundary styles (polyline paths with bulges, and edge
lists of line / arc / ellipse / spline edges, arc direction flags honoured),
through `INSERT` expansion, and with the colour it was drawn in.

- **Regions, not polygons.** A hatch can carry islands, so the geometry core
  gained hole-aware area, centroid and second moments (holes subtract by the
  parallel-axis theorem; a hole's wall *adds* perimeter, because it is a free
  surface the Gent shape factor counts), hole-aware stiffness (draft closes a
  hole while it opens the outside — a hole is a core pin), and a rasteriser that
  punches the hole out of the land mask so contact area is the net one. The
  solid path is untouched and still taken whenever a region has no holes, so no
  number the tool had already reported moved.
- **Reconciliation, reported.** Where the detector found the same face, one bar
  results and the hatched definition wins. Where the detector had called it a
  *block*, the block is dropped with a warning. Diagnostics gives all four
  counts — detected, kept, hatched, both — so a merge is never silent.
- **Bonding.** A hatched bar is drawn independently of the block outlines, so
  exact corner matching finds nothing along its long side; hatched bars fall
  back to matching by collinear overlap. Without this the coupling tab would
  have been silently empty on every hatched drawing.
- **Through the pitch.** A bar hatched once in a drawn pitch rides the identical
  per-instance transform the linework rides, so it appears once per pitch.
  Under uniform scaling it stretches with its pitch; under groove-only it keeps
  its length, because a tie bar is land, not void.
- **Back out again.** *DXF + HATCH* writes the tread as the tool understood it —
  blocks as polylines, bars as colour-filled HATCHes with their holes — and
  re-importing it reproduces the tread exactly. *Project JSON* saves the tread
  and every control and loads back in; it is the only export that does.

Two corrections to the version this was ported from: the ACI greyscale ramp
(250–255) was linearly interpolated and missed white by 34 counts, and is now
tabulated; and hatched regions are now included in the groove-only land spans,
without which a bar drawn where no block reaches would have been stretched as
though it were an open groove.

### The layout, and four requests from the design office

- **One tab bar, two rows.** Setup was a full-width dashboard with the result
  tabs beneath it, so reaching a chart meant scrolling past every input and
  changing one number meant scrolling back. Everything is now behind one pinned
  bar under the header: **Set up** on top, **Results** below, each setup tab
  tinted with its own section's hue. Run sits at the right-hand end of the setup
  row and opens the sweep when it finishes — unless a result tab is already
  open, in which case it leaves you there. Result tabs are listed from the start
  so the run's output can be seen in advance, but cannot be opened until there
  is something behind them.
- **Kxy in the comparison.** The cross stiffness was computed per block and
  thrown away at the rasteriser. It is the off-diagonal of the same 2×2 as Kx
  and Ky and the only map that can be **negative**, so nothing on its path may
  clamp it: an angled lug couples one way, its mirror the other, and on a
  symmetric pattern they cancel — which is the reading. The comparison table
  needed two fixes to state that honestly: a CoV against a mean of −0.003 came
  out at 47 000%, and the decimal places now follow the magnitude rather than
  being fixed at one.
- **Every compared design's tread, stacked.** Two curves that differ tell you
  there *is* a difference; the patterns beneath them tell you what it is. A
  comparison entry now carries the tread as well as the numbers, and so does the
  JSON run export — 119 KB against a 3.4 MB run — so a run loaded from a file
  months later is not a curve with no pattern under it. A held design is frozen
  in the wear state its run had.
- **One patch band, three tabs.** The band follows the open tab: the sweep, the
  coupling tab, and the comparison. It is one angle everywhere — drag it on one
  tab and the others are looking at the same place — while each tab keeps its
  own zoom. On the comparison stack each row's band is as wide as *that* tyre's
  patch is at that angle, because a fixed arc length subtends a different angle
  on a different circumference.

---

## 5. How the numbers are kept honest

A three-link chain, each link tested:

```
v6.4 reference JS  ──(~1e-9)──  Python engine  ──(<2e-3)──  Browser JS engine
       │                             │                            │
  verify/tool_v64_reference.js   tread_eval/*.py              app/engine.js
```

- **342 tests** (from 153 at the start of the audit).
- `tests/test_physics.py` checks every equation against a **closed form worked
  out by hand**, not against a previous run — a golden-value test would have
  blessed the bugs above.
- `tests/test_robustness.py` covers 25+ degenerate and hostile inputs across
  both engines.
- **Structural tests** guard the *shape* of the pipeline: no dead transport
  (anything the worker ships must be rendered or exported), no orphan controls
  (every template input is read and vice versa), every input documented, exports
  wired and frozen.
- `app/selftest.js` — 13 engine checks under Node.
- `app/couplingaudit.js` — 58 checks on the tie-bar network: linear algebra
  against closed forms, the assembled system against equilibrium, a three-node
  case against a hand solution.
- `app/slipaudit.js` — 43 checks on the brush-model slip response: the textbook
  closed forms for a rectangular patch (Cα = Ky·a, t = a/3) and an elliptical
  one (Cα/Ky = 8a/3π, t = 3πa/32), the FFT against a direct summation to 1e-15,
  and the direction sensitivity that no other quantity on the page has.
- `app/unitsaudit.js` — 73 checks on dimensional consistency, proved by
  geometric similarity: scale every length by λ and the load by λ², and all 24
  outputs land on the power of λ their units demand, to five decimals.
- `app/pitchaudit.js` — 52 checks on pitch replication: the replicated tread
  against the same tread laid out by hand in a DXF (two routes sharing nothing
  but the importer), hand-computed land ratios for both scaling conventions,
  and the closure diagnostics on a pattern that deliberately does not tile.
- `app/crownaudit.js` — 67 checks on the crown: a single arc against its closed
  form, arcs round-tripped to drops and back to fourteen digits, scale
  invariance, and proof that the two-radius blend and class fallbacks are
  untouched by the drop-first path.
- `app/hatchaudit.js` — 73 checks on HATCH tie-bar import: hole-aware area,
  centroid and second moments against closed forms; the no-holes case proved
  bit-identical to the solid path; both DXF boundary styles, arc edges and every
  colour route; INSERT expansion; the merge with the automatic detector; the
  collinear-overlap bonding a hatched bar needs; replication through a pitch
  sequence under both scaling conventions; and a full round trip out through
  `patternToDxf` and back.
- `app/browsertest.js` — a real Chromium run: load, compute, crown
  reconciliation, all three exports, drag interaction, the report's section
  list and page count, the review pack opened as its own page, zero page errors.
- `app/casecheck.js` — two complete tyres (2W blocked tread with a 40° lean
  sweep; TBR ribs with 38 tie bars, 9 mm worn, 4 ribs) driven end to end through
  the built page, with 35 physical statements checked on the numbers that come
  out — not against a stored baseline.

---

## 6. Where things stand

**Complete.** All four audit categories closed on clean passes; all requested
features delivered and verified in a real browser.

### Known limitations — none of them defects

1. **Flag thresholds are uncalibrated.** The *ranking* between two designs is
   trustworthy; a single absolute verdict is not.
2. **The `layered` sipe model is still the default**, deliberately, to preserve
   v6.4 parity — with its known stiffening artifact at shallow sipe depths.
   Switch to `continuous` when bit-matching no longer matters.
3. **Tyre-class values are engineering-typical defaults**, not measured
   profiles. The crown radii and break are all overridable when you have the
   real cross-section.
4. **The bundled sample is a uniform array** with no pitch modulation, so its
   order content is a single clean tone. The banner says so on load.
5. **Depth is always an assumption.** A 2D tread plan carries no NSD, draft or
   sipes; those are your inputs and are recorded as assumed.
6. **Pitch replication is validated against a synthetic tiling**, not yet
   against a production pitch drawing with its real length sequence. Two open
   dependencies on the design office: which scaling convention a modulated
   pitch uses (whole-pitch or groove-only), and a real pitch DXF to check
   against.
7. **A footprint imported at one lean is only valid at that lean.** The tool
   records the lean it was measured at and refuses to scale it; carrying it to
   another lean is an extrapolation you would be making, not one it makes.

### One process note

Partway through the feature work the sandbox checkout was rolled back to before
the multi-class/bands/PDF commit — that work briefly vanished from the working
tree. It was intact on the remote and was recovered with a fetch and reset.
Nothing was lost. If anything ever looks missing, the branch history is the
source of truth.

---

## 7. Commit history

| Commit | What |
|---|---|
| `0091252` | Baseline pipeline |
| `99f3390` | Real stiffness model, DXF import, contact-patch definition, light theme |
| `8468a14` | Plain-language reader's guide |
| `7637cec` | Config-file inputs, standard patch shapes, θ-axis pattern strip |
| `33fc7b9` | **Standalone browser app — full JS port** |
| `7d9f782` | Physics passes 1–2: FFT corruption, lean scaling, validation |
| `249592b` | Physics pass 3: sipe stiffening artifact (opt-in fix) |
| `2e67f75` | Bugs: fail loudly instead of returning silent garbage |
| `322b5ef` | Bugs pass 2: clean CLI errors, reject off-tread patches |
| `64a2c73` | Data flow: close the pipeline, remove dead ends, add export |
| `f1e2c15` | Data flow pass 2: stale-crown dependency, trim payload |
| `5557205` | UI: make the workflow completable unaided |
| `df8875f` | Confirming passes; freeze exported settings |
| `be6aa0c` | **Multi-class, rib divisions, in-session comparison, PDF** |
| `d2023e1` | **Crosshair, linked zoom, order-chart explanation** |
| `f101bf7` | Real tread arc profiles: single and multi-arc crowns |
| `6f7ecf8` | Group repeated tie bars so a family is one edit |
| `fecbabc` | Rolled-out tread under the coupling curve |
| `d7fcf71` | **Slip response: Cα, Cκ and the pneumatic trail** |
| `cb83b52` | Pin the tread to the foot of the window; row chips |
| `5897d50` | **Build the whole tread from one drawn pitch** |
| `9bd0123` | **Drop-first crown, with the radii solved** |
| `410cca5` | **Report fixes and the interactive review pack** |
| `814d5ee` | **Import a measured contact patch** |
| `d3810c3` | **Read tie bars from a coloured HATCH** |
| `834bfc8` | **One tab bar, inputs above results** |
| `6c1f195` | Compare designs on Kxy, the cross stiffness |
| `e44e00e` | Stack every compared design's tread |
| `9a7c52c` | The patch band on the coupling and compare tabs |

---

## 8. Repository layout

```
tread_tool.html          the standalone browser app (built artifact)
build_app.py             inlines engine + worker + UI + CSS + Plotly + jsPDF + guide
build_report.py          command-line report generator

app/
  engine.js              the JS compute core (DXF, raster, FFT sweep, stiffness, shapes)
  worker.js              Web Worker glue
  ui.js                  main-thread application logic
  template.html          page skeleton
  style.css              theme-aware styles
  vendor_jspdf.js        vendored jsPDF (MIT)
  selftest.js            engine checks under Node
  couplingaudit.js       tie-bar network audit
  slipaudit.js           brush-model slip response audit
  unitsaudit.js          dimensional consistency audit
  pitchaudit.js          pitch replication and closure audit
  crownaudit.js          crown arcs, drops and the solver between them
  hatchaudit.js          HATCH tie bars, holes and the DXF round trip
  browsertest.js         Playwright smoke test
  casecheck.js           two complete tyres end to end through the built page
  parity.js              JS↔Python parity harness

tread_eval/              the Python pipeline (schema, stiffness, dxf, raster,
                         sweep, metrics, contact_patch, cp_shapes, report, config)
verify/                  v6.4 functions extracted verbatim, the reference oracle
tests/                   342 tests
data/                    the Tramplr sample DXF, tie-bar and pitch fixtures,
                         hatch fixtures and their generators, footprints
GUIDE.md                 plain-language guide, embedded in the app as a tab
README.md                how to run both paths
```
