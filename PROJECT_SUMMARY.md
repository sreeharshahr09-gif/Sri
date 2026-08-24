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

---

## 5. How the numbers are kept honest

A three-link chain, each link tested:

```
v6.4 reference JS  ──(~1e-9)──  Python engine  ──(<2e-3)──  Browser JS engine
       │                             │                            │
  verify/tool_v64_reference.js   tread_eval/*.py              app/engine.js
```

- **254 tests** (from 153 at the start of the audit).
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
- `app/unitsaudit.js` — 64 checks on dimensional consistency, proved by
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
- `app/browsertest.js` — a real Chromium run: load, compute, crown
  reconciliation, all three exports, drag interaction, zero page errors.
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
  browsertest.js         Playwright smoke test
  casecheck.js           two complete tyres end to end through the built page
  parity.js              JS↔Python parity harness

tread_eval/              the Python pipeline (schema, stiffness, dxf, raster,
                         sweep, metrics, contact_patch, cp_shapes, report, config)
verify/                  v6.4 functions extracted verbatim, the reference oracle
tests/                   254 tests
data/                    the Tramplr sample DXF
GUIDE.md                 plain-language guide, embedded in the app as a tab
README.md                how to run both paths
```
