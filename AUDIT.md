# Audit — `Piezo_artifact.ipynb`

Tire inner-liner circumferential strain model + piezoelectric charge estimate,
10.00R20 medium-duty truck tire.

**Verdict.** The notebook runs, produces all requested artifacts, and the code is clean
and readable. But the strain field does **not** satisfy the physical specification it was
written against: the compression zones land *inside* the contact patch instead of before
and after it (requirements 1 and 3), strain at the shoulders stays at 46% of peak
(requirement 5), the waveform has a genuine discontinuity (requirement 6), and the
strain history violates closed-loop kinematics. Separately, `speed` is declared adjustable
but has no effect on any result, and the saved notebook shows two contradictory charge
plots from different model states.

Every number below was reproduced by executing the model — see `audit/measure_original.py`.

---

## Summary of findings

| # | Severity | Finding |
|---|---|---|
| **P1** | High | Compression zones fall inside the contact patch, not outside it |
| **P2** | High | Strain history violates closed-loop kinematics, `∮ε ds ≠ 0` |
| **P3** | High | Contact patch length inconsistent with the stated load and pressure |
| **P4** | High | Peak strain unanchored physically; load scaling is the wrong mechanism |
| **P5** | Medium | Tensile peak far too narrow — bell, not plateau |
| **P6** | Medium | Shoulder falloff too weak; docstring numerically wrong |
| **P7** | Medium | `speed` declared adjustable but never used |
| **P8** | Low | Width profile uses section width instead of tread contact width |
| **Z1** | High | Charge sampled at a point instead of averaged over the patch footprint |
| **Z2** | High | Electrode topology self-contradictory: `d33` charge + parallel-plate capacitance |
| **Z3** | Medium | No current or power — the quantities the stated objective needs |
| **Z4** | Low | Mismatched material card (`εr` bulk PZT, `E` composite-like) |
| **S1** | High | Saved outputs stale and out of order; two contradictory charge plots |
| **S2** | Medium | Cells 5 and 10 are duplicate code |
| **S3** | Medium | Hard-zeroing creates a real C⁰ discontinuity; done via a Python loop |
| **S4** | Low | Comment describes a bug that cannot occur |
| **S5** | Low | Heatmap y-axis inverted relative to the 3D plot in the same cell |
| **S6** | Low | Heatmap tick half-cell misregistration |
| **S7** | Low | Aspect-ratio distortion misrepresents patch geometry ~4× |
| **S8** | Low | Only 20 samples across the entire contact event |
| **S9** | Low | Unused `Axes3D` import (obsolete since matplotlib 3.2) |
| **S10** | Medium | `.npy` export carries no axes, units or parameters |
| **S11** | Medium | No validation or assertions anywhere |

---

## Requirement compliance

| Req | Asked for | Status |
|---|---|---|
| 1 | Compression **before entering** the patch | ❌ compression sits inside the patch (P1) |
| 2 | High tension at patch centre | ✅ |
| 3 | Compression **after exiting** the patch | ❌ same as 1 (P1) |
| 4 | Max strain near tread centreline | ✅ |
| 5 | Reduced strain toward both shoulders | ⚠️ only to 46% of peak at the shoulder (P6) |
| 6 | Smooth, no discontinuities | ❌ 1.47 µε step from hard-zeroing (S3) |
| 7 | A few thousand microstrain | ✅ 2500 µε — plausible, but by assertion not derivation (P4) |
| A | 2D strain map | ⚠️ produced, but domain excludes the compression zones (P1) |
| B | 3D surface | ✅ |
| C | One-revolution strain history | ⚠️ produced; spatial axis only, no time (P7) |
| D | NumPy export | ⚠️ produced; no axes or metadata (S10) |
| E | Realistic analytical functions, not sine waves | ✅ Gaussians used as asked |
| F | Adjustable load, pressure, speed, patch length, peak strain | ⚠️ `speed` has no effect (P7) |
| G | Comments explaining the physics of each region | ✅ genuinely good, though P1 means they describe behaviour the code doesn't produce |

---

## Physics and modelling

### P1 — Compression zones are inside the contact patch (High)

The core defect. `f_x` is a difference of Gaussians with `σ_t = L/4` and `σ_c = L/1.5`;
its sign change is fixed by the ratio of those two widths, which has no relationship to
the patch edge at `L/2`. Measured:

```
contact patch half-length          66.0 mm
tension → compression crossing     48.2 mm   ← inside the patch
peak compression at                85.4 mm,  −894 µε
strain at the contact edge ±L/2    −694 µε   ← should be strongly tensile
fraction of patch length in compression   27%
```

So the model puts the liner into 694 µε of *compression* at the exact moment it is
pressed flat against the road, which is where the tension should be largest. Requirements
1 and 3 ask for compression *before entry* and *after exit*; the code delivers compression
*within* the patch and the deepest compression 85 mm out, well past it.

This is also why the 2D map hides the problem: its domain is exactly `[−L/2, +L/2]`, so
the region where the compression zones were supposed to appear is off-canvas entirely.

**Fix.** Decouple the two length scales. Put the tensile plateau on the patch
(`|s| < L/2`) and centre the compression lobes just *outside* `±L/2`, where the carcass
actually bends into and out of the flat. In the corrected model the crossing moves to
104.3 mm against a patch edge of 99.9 mm — outside, as required.

### P2 — Strain history violates closed-loop kinematics (High)

A material point on a closed loop must return to its original length after one revolution,
so to first order `∮ ε ds = 0`. The model:

```
∮ ε ds                             −22,977 µε·mm     (should be ~0)
tension area / compression area     0.86             (should be ~1.0)
```

Confirmed analytically: the integral equals `√(2π)/(1−K) · (σ_t − K·σ_c) · ε_peak`, which
is nonzero for any `K` unless `σ_t = K·σ_c`. The modelled tire loses ~23 µm of
circumference per revolution.

This matters practically, not just aesthetically: a piezo harvester is AC-coupled, so a
net-nonzero strain integral becomes a net charge that has to go somewhere, and it biases
any energy-per-revolution figure derived from the waveform.

**Fix.** The physical balance is a *small* compressive offset distributed over the whole
free circumference, not deeper local lobes: a 2500 µε pulse over ~130 mm needs only about
90 µε spread over the remaining 3.2 m. Compute that offset from the constraint rather than
tuning it. Corrected model: `∮ε ds = −6×10⁻⁶ µε·mm`, area ratio 1.000.

### P3 — Contact patch length inconsistent with load and pressure (High)

`contact_patch_length = 0.132` is commented as "derived from typical heavy truck tire
contact patches," but it contradicts the notebook's own load and pressure:

```
F / p  = 40 kN / 827 kPa            48,368 mm²   required contact area
132 mm × 270 mm                     35,640 mm²   as modelled
⇒ implied mean contact pressure     1,122 kPa    = 1.36 × inflation
⇒ using realistic 210 mm tread width: 1,443 kPa  = 1.74 × inflation
```

Measured mean-contact-pressure ratios for radial truck tires sit around 1.1–1.3, so 1.74
is out of range. Vertical equilibrium at this load and pressure needs **~180 mm** at full
section width, or **~230 mm** at a realistic tread contact width.

Worth confirming this value wasn't inherited from the piezo patch length, which is also
132 mm.

*Input plausibility, separate from the code:* 40 kN on a single 10.00R20 is well above its
single-tire rating (roughly 26–27 kN at 830 kPa). If that figure is really an axle or dual
load, the per-tire value should be halved — this changes the patch length substantially.
Flagging rather than changing, since it was specified explicitly.

### P4 — Peak strain unanchored; load scaling uses the wrong mechanism (High)

`peak_strain = 2500` is a bare constant, and cell 9 scales it as `∝ load`. The underlying
mechanism says otherwise. Inside the patch the belt is flattened, so curvature changes
from `1/R` to ≈0 and a fibre at offset `h` from the neutral axis sees `ε ≈ h·Δκ = h/R`:

```
h = 1.0 mm → 1887 µε        h = 2.0 mm → 3774 µε
h = 1.5 mm → 2830 µε        h = 3.0 mm → 5660 µε
```

2500 µε corresponds to `h ≈ 1.33 mm`, which is reasonable — so the *magnitude* is fine,
but it is an assertion rather than a derivation, and nothing ties it to the tire.

The scaling is the real error: **once the belt is flat it cannot flatten further**, so
additional load mostly extends the flat region rather than deepening the strain. The
notebook's `∝ load` rule reports 3125 µε at 50 kN, attributing to amplitude what
physically goes into duration.

That said, peak strain is *not* strictly load-independent, and an earlier draft of this
audit overstated the point. The `h/R` ceiling is geometric and fixed, but it is only
*reached* insofar as the belt actually conforms to the road. The belt is a tensioned beam
with bending stiffness `D` under membrane load `N = pR`, so it relaxes into and out of the
flat over `λ = √(D/N) ≈ 30 mm`, giving `ε_peak = (h/R)·η` with `η = 1 − e^(−L/2λ)`. The
dependence is therefore **saturating**, not absent:

```
        L        conformity η   peak strain   vs 40 kN
15 kN    75 mm      0.713          1857 µε      0.74
25 kN   125 mm      0.875          2279 µε      0.91
40 kN   200 mm      0.964          2511 µε      1.00
55 kN   275 mm      0.990          2577 µε      1.03
70 kN   349 mm      0.997          2596 µε      1.03
```

Over 25–55 kN, patch length changes ×2.20 while peak strain changes ×1.13. So the
notebook's error is one of *degree and mechanism*, not of direction — it is least wrong
below rated load, where the belt genuinely fails to conform, and most wrong above it,
where the ceiling has been reached. Pressure enters twice and largely self-cancels
(shorter patch = less conformity, tighter belt = shorter λ = more conformity), netting
~1% over 650–1000 kPa toward lower strain at higher pressure.

The patch-length rule `L ∝ (F/p)^0.5` has the same problem — it assumes the patch grows
isotropically, but tread width is fixed by construction, so all growth goes into length
and `L ∝ F/p`:

```
        notebook L      fixed-width physics L
25 kN     114 mm              134 mm
40 kN     132 mm              179 mm
55 kN     148 mm              224 mm
```

The error compounds with load, and it inverts the harvesting conclusion: extra load buys a
*longer* pulse (more energy per revolution), not a taller one.

### P5 — Tensile peak far too narrow (Medium)

Tensile FWHM is 58 mm on a 132 mm patch — the strain is a narrow bell centred on the patch
midpoint. Physically the patch is flat over most of its length, so the liner strain should
be a **plateau** with roll-off near the edges. A super-Gaussian, `exp(−(s/a)^(2m))` with
`m ≈ 3`, gives that shape while staying smooth. This also has a direct effect on Z1: a
flat top means a large sensor patch averages far less signal away.

### P6 — Shoulder falloff too weak; docstring numerically wrong (Medium)

```
σ_y = W/2.5 = 108 mm
f_y at the shoulder (y = 135 mm)   45.8%  → still 1145 µε at the very tread edge
```

Requirement 5 asks for reduced strain toward the shoulders; retaining 46% at the edge is
a weak reduction, and the shoulders are the part of the tread most free to lift away from
the road.

The docstring is also wrong: it claims strain "reduces to ~37% at `y = tire_width/2.5`",
but `exp(−y²/2σ²)` at `y = σ` is `exp(−0.5) = 60.7%`. 37% (1/e) occurs at `√2·σ = 153 mm`
— beyond the tire itself.

### P7 — `speed` declared adjustable but never used (Medium)

`speed` appears exactly twice in the notebook: its own definition and its comment. It
influences nothing. Requirement F lists it as an adjustable parameter, and for a harvesting
study it is arguably the most important one — it sets the excitation frequency:

```
rotation frequency        3.34 Hz   → 300 ms period
patch residence time      11.9 ms   → ~4% duty cycle
pulse bandwidth           ~84 Hz
```

None of these appear. The consequence is Z3: without a time axis there is no `dQ/dt`, so
no current and no power.

### P8 — Width profile uses section width, not contact width (Low)

The 2D map spans `±135 mm`, the full section half-width. Section width includes the
sidewall bulge, which never contacts the road; tread contact width is roughly 78% of it
(~210 mm here). The map therefore assigns strain to material that isn't in contact.

---

## Piezoelectric model

### Z1 — Charge sampled at a point, not averaged over the patch (High)

`Q = d33·E·A·ε` is evaluated using the strain at a single centreline point, then multiplied
by the full 1320 mm² patch area. But the patch is 132 mm long — the *same order as the
contact patch itself* — so it is never uniformly strained.

```
Q_peak, point strain (as coded)     44.29 µC
Q_peak, averaged over 132×10 mm     17.15 µC
⇒ overestimate                      2.6×
```

The patch senses a mean of 968 µε where the code assumes 2500 µε. Fixing P5 reduces this
penalty a lot — with a flat-topped profile the averaging loss drops to ~3% — which is a
useful design result in itself: *plateau-shaped strain is what makes a large-area patch
viable.*

### Z2 — Electrode topology is self-contradictory (High)

Two mutually exclusive device geometries are assumed in the same model:

- `Q = d33·(E·ε)·A` with an **in-plane** strain implies **in-plane poling**, i.e.
  interdigitated electrodes (an MFC-P1-style device).
- `C = εr·ε₀·A/t` with `A` = planar face area and `t` = thickness is a parallel-plate
  capacitor, which describes **through-thickness** electrodes — a `d31` device.

A patch cannot be both. Compounding this, for an IDE device the area carrying the force is
the cross-section normal to the fibre direction (width × thickness ≈ 2 mm²), not the
1320 mm² planar face, so `A` is likely wrong by orders of magnitude in the charge relation.

The prescribed formula was kept as specified, but the resulting figures — 434 V peak,
14.5 mJ/rev, ~48 mW — should be read as an **optimistic upper bound**, not a prediction.
Published in-tire MFC harvesters generally report tens of µW to a few mW. Waveform *shape*
and *timing* are unaffected; only the scale is in question.

### Z3 — No current or power (Medium)

The stated objective is simulating piezoelectric energy harvesting, but the notebook stops
at charge and open-circuit voltage. Neither `i = dQ/dt` nor average power appears — the two
quantities that determine whether a harvester design is viable. This is a direct consequence
of P7 (no time axis).

### Z4 — Mismatched material card (Low)

`εr = 1700` is bulk PZT-5H; `E = 24.4 GPa` is far below bulk PZT (~60 GPa) and closer to a
fibre composite. `d33 = 550 pC/N` is high for a composite. The three constants appear drawn
from different material families. Not necessarily wrong, but the source should be recorded.

---

## Software and reproducibility

### S1 — Saved outputs are stale and out of order (High)

Execution counts as saved:

```
cell:  2   3   4   5   7   9  10  11
exec:  1   2   6   7  11  10   4   5
```

Cell 10 ran at count 4, *before* cell 4 (count 6) produced the strain history it depends on.
Cells 5 and 10 contain byte-identical code, yet their saved plots differ:

```
cell 5  (exec 7) charge plot md5 = c46f4dd061f330a1fd33d381797a6cd2
cell 10 (exec 4) charge plot md5 = 21b44650b5a98443f405332f3a0f45e9
identical? False
```

So the notebook ships **two different charge waveforms for the same computation**, and cell
10's is from a superseded model state. A reader going top to bottom has no way to tell which
is current. Restart-and-run-all before committing.

### S2 — Duplicate cells (Medium)

Cell 5 ("Re-running the Piezoelectric Charge Waveform Calculation…") duplicates cell 10 in
full, and appears *before* it. One should be deleted.

### S3 — Hard-zeroing creates a real discontinuity (Medium)

Cell 4 zeroes strain outside `±2.5·L`, which requirement 6 explicitly forbids:

```
analytic strain at the truncation boundary   −1.473 µε
largest |strain| zeroed                       1.452 µε   ← the C⁰ jump
```

Small in absolute terms, but it is a true step, and a harvester circuit responds to `dQ/dt`,
where a step is a spike. Verified by grid refinement — for a continuous function the largest
adjacent-sample jump halves when the grid is halved; across a step it plateaus:

```
original model:   9.33e0 → 4.67e0 → 2.33e0 → 1.47e0   ratios 2.00, 2.00, 1.58 → heading to 1.0
corrected model:  9.61e0 → 4.80e0 → 2.40e0 → 1.20e0   ratios 2.00, 2.00, 2.00 ✓
```

The plateau value 1.47 µε is exactly the discontinuity height. The truncation is also
unnecessary — the function already decays there — and is implemented as a 500-iteration
Python loop where a boolean mask would do.

### S4 — Comment describes an impossible bug (Low)

> `# This corrects the issue where x=0 far from contact resulted in peak_strain.`

`true_relative_x_positions` is monotonic, so `x = 0` occurs exactly once, at the patch
centre, where `peak_strain` is correct. The comment appears vestigial from an earlier
formulation and misleads about why the masking exists.

### S5 — Heatmap y-axis inverted relative to the 3D plot (Low)

`sns.heatmap` draws row 0 at the top, so `y_min = −135 mm` is plotted *above* `y_max = +135 mm`
— inverted with respect to the 3D surface in the same cell. Labels are self-consistent, so
nothing looks wrong today only because `f_y` is symmetric. Add camber, ply steer or any
asymmetric loading and the map silently mirrors.

### S6 — Heatmap tick misregistration (Low)

Ticks are placed at `np.linspace(0, 199, 5)`, but in `sns.heatmap` cell `i` spans `[i, i+1]`
with its data point at `i+0.5`. Every label is off by half a cell, and the last is off by a
full one (0.66 mm). Minor here; worth fixing since it scales with coarser grids.

### S7 — Aspect-ratio distortion (Low)

A 132 × 270 mm patch (about twice as wide as it is long) is drawn on a 200 × 100 grid in a
12 × 6 in figure, stretching the circumferential axis ~4.1× relative to the width axis. The
strain footprint reads as elongated along the rolling direction when it is in fact much
wider than long. Use `pcolormesh` with `set_aspect("equal")`.

### S8 — Contact event badly under-sampled (Low)

500 samples per revolution = 6.66 mm spacing, giving **20 samples across the entire contact
event** and a 0.8% peak underestimate. Adequate for a plot, not for differentiating to get
current. The corrected model uses 20,001 (1,199 samples in-patch).

### S9 — Unused import (Low)

`from mpl_toolkits.mplot3d import Axes3D` has been unnecessary since matplotlib 3.2 and is
never referenced.

### S10 — Export lacks axes and metadata (Medium)

`np.save` writes a bare `(100, 200)` array. Nothing records which axis is which, that units
are microstrain, what the extents are, or which load/pressure produced it — so the file is
not interpretable on its own. Ship an `.npz` with axes and parameters alongside it.

### S11 — No validation (Medium)

Nothing checks that the model does what the prose claims. Every defect above (compression in
the wrong place, nonzero loop integral, discontinuity, 46% shoulder strain) is mechanically
detectable in a few lines, and none would have survived a `restart-and-run-all` with
assertions in place.

---

## What was delivered alongside this audit

- `tire_strain_model.py` — corrected reference implementation
- `Piezo_artifact_corrected.ipynb` — executed notebook, all A–G deliverables
- `audit/measure_original.py`, `audit/measure_original_part2.py` — the measurement scripts behind every number above

Corrected model, same inputs:

```
contact patch length      199.7 mm     (from vertical equilibrium, was 132 mm)
peak tensile strain       2511 µε      (= (h/R)·η, h = 1.38 mm, η = 0.964 — derived)
peak compression          −999 µε      centred outside ±L/2 ✓
∮ ε ds                    −6e−06 µε·mm (was −22,977) ✓
tension/compression area   1.000       (was 0.86) ✓
shoulder strain           6.7% of peak (was 45.8%) ✓
C⁰ refinement ratios       2.00 2.00 2.00 — continuous ✓
rotation frequency         3.34 Hz, 18.0 ms residence, 6.0% duty cycle
peak current               5.22 mA     (scales with speed — new)
```

Remaining limitations of the corrected model, stated plainly: `ε(s,y)` is separable, so the
patch is assumed equally long at the shoulders as at the centreline; it is quasi-static, so
viscoelastic phase lag and high-speed standing waves are absent; free rolling only, so no
tractive/braking asymmetry or cornering; and the absolute piezo magnitude still carries the
Z2 caveat. Waveform shape and timing are the trustworthy outputs — absolute harvested power
is an upper bound.
