# How to read this report

A plain-language guide to every chart and number. No tyre-mechanics background
assumed. If you only read one section, read **Start here** and **The five
minutes that matter**.

---

## Start here

The tool answers one question, over and over:

> **As the wheel turns, and as the bike leans over, what part of the tread is
> actually touching the road — and how stiff is it?**

Everything else follows from that. Two things vary:

- **θ (theta) — rotation angle.** Where the wheel is in its turn, 0° to 360°.
  θ = 0 and θ = 360 are the same place on the tyre.
- **γ (gamma) — lean angle.** How far the bike is banked over. 0° is upright,
  40° is hard cornering.

At every combination of θ and γ, the tool works out which blocks are inside the
contact patch and adds up their area and stiffness. Charts that plot against θ
show you *what happens as the wheel spins*. Charts that plot against γ show you
*what happens as you lean into a corner*.

### The one idea behind most of the diagnostics

A tyre would ideally present the *same* amount of rubber to the road at every
instant. Real patterns can't — blocks and grooves alternate. So the contact area
ripples up and down as the wheel turns.

**That ripple is what you're hunting.** A big ripple means the force under the
tyre pulses, and pulsing force is noise, vibration and uneven wear. A small
ripple means the tyre feels smooth and wears evenly.

The ripple gets worse when fewer blocks are in contact at once, because there's
less averaging. And on a motorcycle, the contact patch **shrinks as you lean**.
That's the whole reason this tool sweeps lean angle instead of just looking at
the tyre upright.

---

## Vocabulary

| Term | What it means |
|---|---|
| **Contact patch** | The bit of tyre actually touching the road at one instant. Roughly the size of a credit card, sometimes smaller. |
| **Block** (or lug) | One raised island of rubber in the tread. |
| **Groove** | The channel between blocks. |
| **Land ratio** | Fraction of the surface that is rubber rather than groove. 0.69 means 69% rubber. Higher = more grip on dry, less water clearing. |
| **Pitch** | One repeat of the pattern going around the tyre. A tyre might have 24 pitches. |
| **Order** | How many times something happens per wheel revolution. "Order 24" = 24 times per revolution. Noise engineers think in orders. |
| **NSD** | Non-skid depth — the block height in mm. How deep the tread is. |
| **Kx / Ky / Kz** | Stiffness. Kx resists braking and acceleration forces, Ky resists cornering forces, Kz resists the tyre being squashed vertically. Units are N/mm: a Kx of 500 means it takes 500 newtons to shove the block 1 mm forwards. |
| **CoV** | Coefficient of variation — the ripple size as a percentage of the average. 5% CoV means the value wobbles by about ±5%. **Lower is better.** |
| **Zone** | The tread split into three lateral bands: **centre** (used upright, straight-line), **intermediate** (used in the transition into a corner), **shoulder** (used at full lean). |
| **Tie bar** | A raised strip in the bottom of a groove, bridging two blocks. Its height is *less* than the NSD, so on a new tyre it sits below the road and touches nothing. It comes into contact part-worn and stiffens the block row against heel-and-toe wear. Common on TBR, sometimes on PCR. |
| **Wear** | How much tread has been worn off, in mm. Every block's bending length is NSD − wear, so a worn tread is stiffer. Wear is also what brings tie bars into contact. |
| **Cα / Cκ** | Slip stiffness. How much force the tread makes per unit of slip — Cα in N/rad for cornering, Cκ in N per unit slip ratio for braking and drive. Not the same as Ky and Kx: see *Slip response* below. |
| **Pneumatic trail** | How far behind the centre of the contact patch the lateral force actually acts, in mm. It is what makes a steering wheel self-centre. |

---

## Units

**One system throughout.** Every number crossing a function boundary in this
tool is in it, and nothing is converted anywhere:

| Quantity | Unit | Examples |
|---|---|---|
| length | **mm** | circumference, tread width, NSD, crown radii, trail |
| area | **mm²** | block area, contact area, patch area |
| force | **N** | vertical load, Fx, Fy |
| pressure / modulus | **N/mm² (= MPa)** | E, G, bulk modulus, contact pressure |
| stiffness | **N/mm** | Kx, Ky, Kz, tie-bar link stiffness |
| moment | **N·mm** | aligning moment Mz |
| angle | **degrees** at every input and output; radians only inside a formula | lean γ, rotation θ, draft |
| slip stiffness | **N** (per unit slip ratio), **N/rad**, **N·mm/rad** | Cκ, Cα, Cmz |
| dimensionless | — | Shore A, Gent k, Poisson ν, shape factor S, land ratio, order amplitude, coupling gain |

The commonest way to get this wrong is the modulus. **E is in N/mm², which is
the same as MPa** — a tread compound is roughly 1.5 to 12. If you type 6890
(kPa) or 6 890 000 (Pa) the tool refuses the run and says so, rather than
returning stiffnesses a thousand times too large.

This is not a claim on paper: `app/unitsaudit.js` proves it by measurement.
Scale every length in a problem by λ and the load by λ², leave the compound
alone, and every output must move by the power of λ its units demand — areas by
λ², stiffnesses by λ, Cα by λ², the trail by λ, and the dimensionless ones not
at all. A single term carrying the wrong power of a length pulls its total off
the predicted exponent immediately. All 24 outputs land on their exponent to
five decimal places, and the reachable lean angle and the coupling gain do not
move at all.

One honest wrinkle it does surface: **the patch area is rasterised**. The sweep
measures a whole number of pixels, so it differs from the exact area of the
patch outline by a fraction of a percent, and the reported patch load is under
the load you entered by that same fraction. Both areas are reported, and a
physics note appears if the gap ever exceeds 3%. The **land ratio is unaffected**
either way — the contact area is measured on the same grid, so the error largely
cancels — but the absolute areas, the stiffnesses and the reported patch load all
carry it, which matters when comparing against measured data.

---

## Building the tread from one pitch

A design office draws **one pitch**. Tick *the DXF is one pitch* in
**1 · Tread plan** and the tool repeats it into the whole rolled-out tread —
after which every other tab behaves exactly as it does for a drawing supplied
whole. Blocks, tie bars, seam wrapping and the sweep cannot tell the difference.

### What you have to supply

**Pitch length.** Not the drawing's extent. If the blocks stop before the pitch
boundary — which they usually do, the groove between one pitch and the next is
part of the pitch — the drawing is shorter than the pitch and the tool cannot
see by how much. It will only take the extent when you ask for that same length
throughout; the moment a sequence asks for a different length, the ratio between
the two decides every scaled coordinate and the pitch length must be stated.

**Pitching**, either as a single length × count, or as **named lengths and a
sequence** — `A=32.4, B=36.0, C=40.2` with `ABCBACAB…`. The circumference is
whatever the sequence adds up to. That is the multi-pitch case a PCR or TBR
actually uses, and the sequence is what spreads the tread noise across orders
instead of concentrating it at the pitch count.

### The one thing the tool will not decide

When a pitch is stretched to a different length, two conventions are in use:

| | what happens |
|---|---|
| **uniform** | the whole pitch scales — a block in a long pitch is longer |
| **groove only** | blocks keep their circumferential length; the grooves absorb the difference |

**Both are used in industry and they give different answers.** On the bundled
test pattern the same sequence gives a land ratio of 0.644 one way and 0.664 the
other, and different order content with it. The tool therefore refuses a
stretching sequence that does not name a convention, rather than picking one and
letting it look like a result. Get the answer from your design office.

### Closure: does the drawn pitch actually tile?

Your own designs close exactly. Competitor patterns digitised from a photograph
often do not — the geometry at one boundary is a few tenths out from the other,
and repeating it would leave gaps.

Before building anything the tool pairs up the outline points on the two
circumferential boundaries and measures the mismatch. What it does next depends
on what it finds:

- **Equal numbers of points on both edges** — they are meant to join, so any
  difference in y is a defect. The import **stops** and says by how much, at
  which y positions, and what snap tolerance would close it. Set a
  **boundary snap limit** to have the two edges pulled together; the run then
  warns that the tread is no longer exactly the drawing, and says how many
  points moved.
- **Different numbers** — the tool cannot tell a missing rib from a legitimate
  blocked pattern with a groove at the pitch boundary, so it **warns and
  continues** rather than guessing.
- **Nothing reaching the far boundary** — the join is a clean lateral groove and
  nothing has to meet.

### Cut lines

Most pitch drawings carry the two lines where the pitch was cut out of the
tread. They are **not tread edges**. Left in, they sit inside a continuous rib
at every join and chop it into one block per pitch, which gets the rib's shape
factor — and so its vertical stiffness — badly wrong. The tool finds them by
their twin at the opposite boundary, removes them before repeating, and puts
them back only at the two ends, where they are what closes a rib that runs the
whole way round. A boundary outline with **no** twin is kept as a real tread
edge, and you are told about it.

---

## The crown, either way round

The crown decides the contact point at every lean, the patch size there, the
effective rolling radius, and the maximum lean the tyre can reach at all. It can
be specified two ways, chosen with **Crown given as** in *5 · Load & advanced*.

**Arc radii** — the mould drawing's own language. `800@45mm, 300@88mm, 90`
means 800 mm out to 45 mm from the centreline, then 300 mm to 88 mm, then 90 mm
to the edge.

**Drop** — the way a designer actually works. You know the width and how far the
profile has fallen at each station, and the radii follow. Same format, with the
drop in place of the radius: `1.2@36mm, 4@60mm, 9.5`. Stations follow the same
convention as arcs — a value of 1 or less is a fraction of the half width, above
1 is millimetres, and `mm` or `%` can be written explicitly.

**The tool solves the radii and shows you what they are.** That readout is the
answer to "what arcs give me this drop?", which is the question the arc-radius
field could never answer.

### How it is solved

On one arc of radius R, starting at developed position y₀ with tangent φ₀ and
drop z₀, the drop after a further developed length L is

```
z(L) = z₀ + R · [ cos φ₀ − cos(φ₀ + L/R) ]
```

exactly — not a small-angle form. That is strictly decreasing in R, so each
arc's radius is a one-dimensional root find, solved outward from the centreline
with φ and z carried along. **Tangent continuity is automatic**, because each arc
inherits φ₀ from the one before.

Round-tripping is exact: take a crown built from radii, read its drops, solve the
radii back, and you get the numbers you started with to fourteen digits.

### Two things worth knowing

**The radii need not fall monotonically.** A drop set that looks progressive can
imply a *flatter* middle arc — because by the time the profile reaches a station
it is already leaning, and a straight continuation alone drops a certain amount.
If you ask for less than that, the tool refuses and tells you what the straight
continuation would give.

**Precedence, now visible.** Drops override arc radii, which override the
two-radius cells, which fall back to the tyre class. The two-radius cells are now
**greyed out** the moment something above them is typed, rather than sitting
there looking live. Giving both drops and arcs at once is refused rather than
silently ranked.

---

## Compound: where the modulus comes from

Every stiffness in this tool scales with Young's modulus **E**. There are two ways to set it, and the panel always shows what was used:

- **From Shore A** — E and the Gent shape coefficient *k* are read off Gent's table (*Engineering with Rubber*, Table 8.1), which has five rows: 30, 40, 50, 60 and 70 A. Values between rows are interpolated (E geometrically, *k* linearly), so 59 A and 61 A no longer differ by 72%. Outside 30–70 A the nearest row is used and you are warned.
- **Enter E directly** — type E in N/mm² and the coefficient *k*. Use this when you have measured the compound, or when it is harder or softer than the table covers.

The shear modulus is always **G = E / 2(1+ν)**. Bending uses E, shear uses G, and the vertical stiffness uses **E_eff = E(1 + 2kS²)** with the bulk-modulus correction, where S is the block's shape factor.

| Shore A | E (N/mm²) | k |
|---|---|---|
| 30 | 1.50 | 0.93 |
| 40 | 2.50 | 0.85 |
| 50 | 4.00 | 0.73 |
| 60 | 6.89 | 0.64 |
| 70 | 12.00 | 0.57 |

---

## Tie bars and the wear state

Put the groove bottom at zero. A block's top starts at **NSD** and, after the tyre has worn *w* mm, sits at **NSD − w**. A tie bar is a raised strip of that groove bottom of height **h < NSD**, so its top starts **NSD − h below the road and touches nothing**.

It engages once **w ≥ NSD − h**. From then on it is flush with the blocks and is simply more land at the same height — which is the point of a tie bar: it arrives part-worn and stiffens the block row against the heel-and-toe wear that is developing by then.

So at the default **wear = 0** the bars are listed and drawn but contribute nothing, which is the truth for a new tyre. Raise **Tread worn** in *6 · Wear & tie bars* and watch the contact area step up and the θ-fluctuation drop as they come in — that step is the tie bar doing its job, and its position on the wear axis is a design decision you can now see.

**Where the bars come from.** Two routes. If the drawing marks them with a
filled HATCH on a `TIEBAR` layer, those are read directly and taken as
authoritative — see *Tie bars you coloured in* below. Otherwise they are found
geometrically, as small closed regions bridging two blocks. The table says which
route found each bar; hover its ID.

### 150 bars, two edits

A mould repeats the same bar around the circumference, so a drawing with 150
tie bars usually has only two or three *distinct* ones. Setting each one is
transcription, not design.

The panel groups them automatically. Bars that match on shape and lateral
position are shown as **one row**, and editing that row sets every bar in the
family. A 476-bar drawing collapses to two rows; the 38-bar rib sample to one.

**Group bars by** offers four options:

| Mode | Groups bars that share |
|---|---|
| **shape + position** (default) | zone, plan shape and lateral position — the mould feature |
| **shape only** | zone and plan shape, wherever they sit |
| **zone** | just centre / intermediate / shoulder |
| **individually** | nothing; the ungrouped list |

Matching is by tolerance, not by bucket: two bars a thousandth of a millimetre
apart in area are always one group. Mirror pairs at +y and −y group together,
and the row's **y range** shows it.

### include

One checkbox per row: **is this region a tie bar at all?** Untick it and the
region is excluded from everything — no contact area, no coupling stiffness,
not in the network. Use it for something the detector picked up that is really
open groove.

There is deliberately **no way to force a bar into contact**. A bar shorter than
the blocks around it cannot touch the road while they do, so "in contact anyway"
is not a what-if — it is an impossible geometry, and the model would have had to
place an 8 mm element at the road surface beside 16 mm blocks.

A bar that genuinely *does* reach the tread surface as moulded is expressed
honestly: give it **height = NSD**. Its engagement wear is then 0 and it is in
contact from new, with the geometry matching the claim.

**Show every bar individually** opens the full list underneath, so a single bar
can still be overridden. Do that and its group reads **mixed** until you set the
group again — the tool will not pick one member's value and present it as the
family's.

**How they are detected.** A tie bar usually has no outline of its own: its two long sides *are* the groove walls of the blocks either side. The importer builds the planar arrangement of every line in the drawing and takes its enclosed regions, so a bar falls out as its own region even though it shares most of its boundary with its neighbours. A region counts as a tie bar if it shares an edge with two or more blocks and its area is below the **tie-bar area limit** (default half the typical block). That is only the first guess — **6 · Wear & tie bars** in the setup panel lists every candidate with its area, height, engagement wear and a checkbox, so you can reject one, promote another, or set heights bar by bar. It sits with the other inputs, before **Run**, because that is what it is: the heights you set here decide what the sweep computes.

---

## Camber and the tread arc profile

The crown is stored as a **local radius at every lateral position**, `r(y)`,
where `y` is developed arc length from the centreline. Everything about lean
comes from integrating it:

- **tangent angle** `φ(y) = ∫ dy / r(y)`
- **drop below the centreline** `z(y) = ∫ sin φ dy`
- **projected (flat) width** `y_proj(y) = ∫ cos φ dy`

### What camber does

At lean angle **γ** the tyre touches where the tread tangent has rotated to
horizontal — the point where **φ(y) = γ**. Everything else follows from that
one inversion:

| Quantity | How it is obtained |
|---|---|
| contact point `y_c` | solve `φ(y_c) = γ` |
| lateral radius at contact | `R_lat = r(y_c)` |
| effective rolling radius | `R_eff = wheel radius − z(y_c)` |
| patch semi-axes | Winkler: `δ = √(Fz / (k·π·√(R_eff·R_lat)))`, `a = √(2 R_eff δ)`, `b = √(2 R_lat δ)` |
| load at lean | `Fz / cos γ` when *load rises with lean* is ticked |
| **maximum reachable lean** | `max │φ│` — the steepest tangent the profile reaches |

Past that maximum there is **no contact point at all**: the tyre would be
riding its tread edge. Those lean angles are dropped from the sweep with a
note, not extrapolated.

On the bundled 2W sample (159 mm tread, 125/55 mm crown) the contact point
walks from the centreline to 61 mm out at 30° of lean, the lateral radius
collapses 125 → 82 mm, and the patch comes out **7% longer and 11% narrower**
than upright. That narrowing is the whole reason the tool sweeps lean.

### Single and multi-arc profiles

Type the real specification in **5 · Load & advanced → Tread arc radii**:

| Spec | Meaning |
|---|---|
| `300` | one arc, constant radius to the edge |
| `125@0.45, 55` | 125 mm out to 45% of the half width, then 55 mm to the edge |
| `800@45mm, 300@88mm, 90` | three arcs with breakpoints in mm from the centreline |
| `800@45%, 300@80%, 90` | the same, as percentages of the half width |

A breakpoint of 1 or less is read as a fraction of the half width, above 1 as
millimetres; `mm` and `%` can be written explicitly. Breakpoints are
**developed arc length**, the same `y` as everywhere else in the tool — not
projected width. Up to 8 arcs.

The arcs meet **tangentially**: `φ` is an integral, so it stays continuous
however abruptly the radius steps. Curvature *is* discontinuous at each
breakpoint, which is exactly what a multi-arc profile is.

### Leaving it blank does not mean flat

There is no flat option, and there should not be — a flat crown has infinite
radius, so `φ` is zero everywhere and the tyre can reach no lean at all.

Blank falls back to the **two-radius blend** from the *Crown R centre* and
*Crown R shoulder* fields. Leave those blank too and it uses the **selected
tyre type's** defaults:

| Tyre type | R centre | R shoulder | max reachable lean on a 220 mm tread |
|---|---|---|---|
| 2W | 125 mm | 55 mm | 64° |
| PCR | 700 mm | 90 mm | 13° |
| TBR | 1500 mm | 120 mm | 5.8° |

The panel always names the radii actually in use and says when they came from
the class defaults, because which crown you got decides every lean angle in the
sweep. Changing the tyre type changes them immediately.

The blend goes smoothly from centre to shoulder rather than switching at a
point, which is **smoother than a real profile** through the shoulder
transition — fine as a default, but type the real arcs when you have them.

The **Contact patch** tab draws the resolved profile with the breakpoints
marked and the contact point shown at every lean angle in the run, so a typed
specification never has to be taken on trust.

### What is not modelled

- **Camber thrust** and lateral force generation. This is a contact and
  stiffness model, not a force model.
- **Carcass deformation under camber** — the belt is treated as rigid.
- **Asymmetric crowns.** The profile is mirrored about the centreline.
- The **projection correction** (*curvature correction* checkbox) weights each
  raster row by `1/cos φ`. It is for patterns digitised in projection — an ink
  impression or a photograph. A mould DXF is already developed, so leave it
  off or the correction is applied twice.

---

## Land percentage vs θ

The second row of the θ sweep is **land in the patch**: how much of the contact
patch is rubber rather than groove at that instant, as a percentage.

It is the contact-area row normalised by the patch area, and the reason both
are shown is that they move for different reasons. Contact area falls when the
patch itself shrinks — leaning a motorcycle does exactly that — *and* when the
pattern under it opens up. Land percentage only moves for the second. If area
drops while land holds, the patch got smaller; if both drop, the pattern under
the patch got emptier.

The dotted line is the mean, so the swing about it can be read straight off.
That swing is the same ripple the Order-content tab decomposes, expressed in
the units a designer thinks in.

---

## Slip response: from stiffness to force

The bottom three rows of the θ sweep — **Cκ**, **Cα** and the **pneumatic
trail** — are the only ones on the page that are forces rather than stiffnesses.

Kx and Ky say how hard the rubber in the patch is. They are not forces, because
a tyre only makes a force when it *slips*. The **brush model** supplies the
missing step. A tread element enters the patch stuck to the road with no
deflection. While it is stuck, the carcass keeps moving, so by the time the
element is a distance `s` past the leading edge it has been dragged by

```
longitudinal   u = κ · s          κ = slip ratio
lateral        u = tan α · s      α = slip angle
```

Each element then pushes back with its own stiffness times that deflection.
Adding up over everything in the patch:

| quantity | definition | units |
|---|---|---|
| **Cκ** = ∂Fx/∂κ | Σ kx,i · si | N per unit slip ratio |
| **Cα** = ∂Fy/∂α | Σ ky,i · si | N/rad — × π/180 for N per degree |
| **Cmz** | −Σ ky,i · si · ui | N·mm/rad |
| **trail t** | Cmz / Cα | mm behind the patch centre |

### Why this is not Ky in disguise

These are **first moments about the leading edge**, not sums. Rubber near the
exit has been dragged the furthest and is worth far more than rubber that has
only just entered — an element at the trailing edge counts for the full contact
length, one at the leading edge for nothing. So Cα can move *opposite* to Ky
over a revolution, and on a real pattern it swings several times harder: on the
bundled 2W sample, Ky fluctuates 1.6% per revolution and Cα 6.2%.

The consequence worth designing around: **the Cα curve is the Ky curve delayed
by the trail**. A block generates its cornering force about a third of a patch
length after it looks like it should.

### It knows which way the tyre is rolling

Nothing else on this page does. Because `s` is measured *from the leading edge*,
reversing the rolling direction swaps which edge that is, and a fore-aft
asymmetric tread — a directional pattern, a stepped block, a swept rib — gives a
different curve forwards and backwards. Mirror the tread and Ky simply mirrors
its curve; Cα does not.

Rotation runs in the direction of travel, so a block **enters the patch at high
θ and leaves at low θ**.

Note that the *mean* of Cα over a revolution cannot see any of this. The mean of
a circular correlation is (mean of the pattern) × (sum of the kernel), so it
depends only on the total rubber and the patch shape — arrange the blocks any
way you like and the mean is identical. **The design information is entirely in
the curve**, which is exactly why the tool plots it against θ rather than
reporting a number.

### What it is not

**This is the tread's share, not the tyre's cornering stiffness.** The tread is
one spring in series with the carcass and the belt, and on a real tyre the
carcass usually dominates. The absolute value here will not match a Flat-Trac
measurement and is not meant to. Compare designs on the same settings, and read
the *variation over θ* — that part is what the pattern controls.

Two more limits worth knowing:

- **No friction limit.** These are slopes at zero slip. The peak of a real
  Fx(κ) or Fy(α) curve is μ·Fz — compound and load, not pattern — so saturation
  is deliberately not modelled. The pattern lives in the slope.
- **No camber thrust.** At lean a tyre makes lateral force at zero slip angle,
  from the crown geometry rather than from slip. That is a different mechanism
  and is not added into Cα.

### Where else it appears

The **Lean map**, **Order content**, **Ribs** and **Compare** tabs all accept
Cα and Cκ as their metric. The rib breakdown is the interesting one: it answers
*which rib actually generates the cornering force*, which is not the same
question as which rib has the stiffest rubber. The per-rib values sum exactly to
the tread total.

---

## Ten rows on one screen

The θ sweep now stacks ten rows, which is taller than any screen. Two controls
sit above it.

**Pin pattern to the bottom** (on by default) glues the rolled-out tread to the
foot of the window. It stays there for the whole height of the stack and lets go
at the end of the section, so whichever curve you are reading — the top row or
the bottom one — the tread it belongs to is on screen, on the same θ axis, with
the contact-patch band running between them. Turn it off if you would rather
have the height back.

**The row chips** switch individual rows on and off. The stack is *rebuilt*
rather than hidden, so four rows get the full height of four rows instead of a
tenth of the page each. Each chip is coloured to match its curve and labelled
with its units — which is what tells Cκ (N) from Kx (N/mm). The chart title says
how many rows are hidden, and the last row on cannot be switched off.

A useful four for a first look: **contact area, Kz, Ky, Cα**. Area and Kz are
the ride and wear story, Ky and Cα are the handling one, and the difference
between the last two is the whole point of the brush model.

---

## The contact-patch band

On the **θ sweep + pattern** tab the contact patch is drawn where it actually
sits on the rolled-out tread, and a translucent band of the same
circumferential extent runs up through every sweep row above it. That turns
"there is a dip near 140°" and "these are the blocks under the patch there"
into one picture instead of two.

**Drag the band** to slide the patch around the tyre, or type an exact angle in
the box. It moves along **θ only**: where the patch sits laterally is decided by
the crown profile and the lean angle (or by the y-centre field in
*3 · Contact patch*), so being able to shove it sideways on this chart would
contradict the physics that put it there.

Drag anywhere *outside* the band to zoom — every chart and the pattern zoom
together. Double-click resets, including a double-click on the band itself.

A patch parked across θ = 0 is drawn in both halves, at the left and right ends
of the strip, because that is what the tyre does at the seam.

---

## Tie bars you coloured in

A tie bar is hard to find automatically. It has no outline of its own — it is
the closed region left between two blocks — so the tool has to guess from area
and adjacency which regions are bars and which are just small blocks. The guess
is usually right and occasionally not, and either way it is a guess.

**A filled HATCH on a layer called `TIEBAR` is not a guess.** If your drawing
marks the bars by colouring them in, the tool reads them directly and takes them
as authoritative.

### What it reads

- **Both boundary styles.** A hatch whose paths are polylines (bulges included)
  and one whose paths are edge lists of lines, arcs, ellipses and splines. Arc
  edges carry their own direction flag, which is honoured — get that wrong and
  the boundary crosses itself.
- **Holes.** A hatch with an island in it — a stone ejector through the bar —
  comes in as a region with a hole. Its area, its stiffness and the contact area
  it adds once worn into are all the **net** ones.
- **Colour.** Entity true colour (24-bit) first, then entity ACI, then the
  layer's own colour. Bars keep the colour they were drawn in everywhere they
  appear: on the rolled-out tread, on the tie-bar plan, on the coupling links,
  and as a swatch beside the ID in the table. That is what lets you tell one
  family of bars from another at a glance.
- **Bars inside blocks.** A hatch in a `BLOCK` definition reached through an
  `INSERT` is expanded like any other geometry, inheriting the INSERT's layer
  the way CAD resolves it — so a pattern arrayed from a block still finds its
  bars.

### How it reconciles with the automatic detector

Both run. The detector is still there and is still what finds bars in every
drawing that carries no hatches at all. Where they meet:

| Situation | What happens |
|---|---|
| Both found the same face | One bar. The hatched definition wins, because it may carry holes and a colour the linework never had. |
| The detector called it a **block** | The block is dropped and it becomes a bar, with a warning saying so. A drawn `TIEBAR` layer outranks an area heuristic. |
| Only the hatch found it | The bar stands on the hatch alone — which is the whole point on a drawing where the bars are not closed by linework. |

*8 · Diagnostics* reports all four numbers — detected, kept, hatched, found both
ways — so a merge is never silent.

**Bonding still comes from geometry.** A hatched bar is drawn independently of
the block outlines, so its long side is one edge where the tool has split the
rib wall at several corners. Exact corner matching finds nothing there, so
hatched bars fall back to matching by **collinear overlap**: same line, same
stretch of it. Detected bars are untouched by this.

### With a pitch drawing

A bar hatched once in a drawn pitch becomes a bar in **every** pitch, riding the
identical transform the linework rides. Under **uniform** scaling it stretches
with its pitch; under **groove-only** it keeps its length, because a tie bar is
rubber and rubber is land, not void.

### Taking it back out

Two exports in *7 · Export* describe the **tread**, not the run, so both are
live the moment a drawing is imported:

- **DXF + HATCH** — blocks as closed polylines on `TREAD`, every tie bar as a
  colour-filled HATCH on `TIEBAR`, holes included. Re-importing that file gives
  the same tread back, so a bar the tool *inferred* comes back as a bar that was
  *drawn*. Useful for handing the interpretation back to CAD.
- **Project JSON** — the imported tread with its bars, colours and holes, plus
  every box on the page, and the results if there are any. It is the only export
  that loads back in: **Load project…** restores the session.

---

## Importing a measured footprint

Every shape in *3 · Contact patch* down to the superellipse is an idealisation.
The last entry in the list, **measured — import a footprint**, is not: it takes
the outline of a real footprint and sweeps that instead.

**What it accepts.** A traced ink footprint, a digitised photograph, or a CAD
outline, as either:

- **DXF** — the **largest closed loop** in the file is taken as the boundary.
  Everything else in the drawing is ignored, so a scan with dimension lines,
  a border and a title block still imports cleanly.
- **CSV** — two numeric columns, **x then y**. Header rows are skipped.

Both routes are the same code the Python pipeline uses, checked file-for-file
against it, so a footprint gives the same outline whichever way it goes in.

**Three things it makes you say out loud**, because each of them is silent and
expensive if wrong:

- **File units.** mm, cm, m or inches. Getting this wrong rescales every area,
  pressure and stiffness on the page without any of them looking odd. The tool
  prints the imported size in mm the moment the file loads — check it against
  the tyre before you read anything else.
- **Lateral placement.** *Auto* keeps the file's own y coordinates when they
  already land on this tread, and otherwise re-centres and tells you it did —
  which is what makes a CAD export that happens to be drawn at y = 95…145 just
  work. *Centre* always moves it to the lateral centre. *As drawn* trusts the
  file exactly; if that puts the outline off the tread, the run stops rather
  than reporting a patch that was clipped to nothing.
- **Measured at lean.** The lean the footprint was actually taken at. It is
  recorded on the report and in every export, so an upright footprint is never
  later mistaken for one taken at full lean.

**What changes downstream: only the shape.** The imported outline replaces the
generated one and then goes through the identical pipeline — placed on the
tread, clipped at the edges, given a pressure from the load over *its own*
area, and swept by the same FFT. Length, width and corner radius grey out,
because the file decides them. The patch's own centroid decides where it sits
laterally, not the crown's estimate.

**Lean scaling switches itself off on import**, with a line saying so. The
Winkler trend narrows a *generated* patch as lean increases; applying it to a
shape you measured would be modelling on top of a measurement. If you have a
footprint per lean, import them one at a time and read each run on its own.

It is worth doing. On the sample tyre the measured outline gives 4147 mm² of
patch against 4335 mm² for the idealised rounded rectangle of the same stated
size, and a contact-area fluctuation of **1.88% against 1.55%** — the corners
the real footprint does not have were smoothing the ripple.

---

## Tie-bar coupling: the tread as a network

Everywhere else this tool treats a block as an **independent spring**. Its stiffness comes from its own outline and nothing else, and the patch total is the sum of the springs it covers. That assumption is what makes the θ sweep a single FFT — and it is exactly wrong for a tie bar, whose whole purpose is to make neighbouring blocks *not* independent.

The **Tie-bar coupling** tab solves the real thing. Blocks and tie bars are nodes, each with two degrees of freedom and its own stiffness to the belt. Every bar is bonded to the blocks it touches by a link carrying the rubber between them:

- **axial** along the span, `E·A/d`
- **shear** across it, `G·A/d`

where `A` is the bonded wall area (shared wall length × bar height) and `d` the distance from that wall to the bar's centre — about half the groove width. The assembly is solved, and every case falls out with no special handling: a bar with a free wall on one side, two bars of different heights on the same block, a whole rib tied end to end.

### Reading it

**Uncoupled** is the tread as independent springs. **Coupled** is the same tread solved as a network. Both are measured the same way, so the **gain** between them is the tie bars' contribution and nothing else. Use the gain; the absolute values use a force-controlled definition and will not match the θ-sweep tab exactly.

**Kxy** is the cross term: push the tread circumferentially and it also moves laterally. Bars square to the tread give none. A **diagonal** bar does, and that is a real steering effect you would otherwise never see.

The gain is **largest when few blocks are loaded** and smallest when the patch covers a whole tied rib. That is not an artefact — blocks moving together put no load into the bars at all.

Below the curve is the **same rolled-out tread**, on the same θ axis — zoom either and both follow. Blocks are dimmed, tie bars are violet, and the thin violet lines are the bonded links the solver actually assembled: one per shared wall, joining a bar to the block it is tied to. This is the fastest way to check a drawing. **A bar with no line touches nothing along a shared edge**, so it contributes no coupling stiffness however solid it looks on the pattern — usually its outline does not quite meet the groove wall. A bar drawn dotted is still below the tread surface: it adds no contact area yet, but it *is* already coupling the blocks either side of it, which is why the gain is there from new.

### Two mechanisms, at opposite ends of the tyre's life

The coupling works **from new**, and its relative value *falls* as the tread wears: a block stiffens as roughly 1/L³ as it shortens, while the bar only shrinks once the tread reaches it. On a 16 mm NSD with an 8.8 mm bar the model gives ×1.32 at zero wear falling to ×1.05 at 12 mm.

The contact-area contribution is the opposite — nothing until worn into, then land like any block. **Tie bars earn their keep across the whole life because the two mechanisms hand over, not because they add up.**

### What it assumes

- A bar is a linear spring bonded over (wall length × bar height). No bulging, no contact non-linearity.
- One point per block, two translational degrees of freedom. The bar sits below the block top, so it really applies a couple as well as a force; that moment is **not** modelled, which makes the bars read slightly *soft*.
- Coupling is whatever the DXF adjacency says. Two blocks that merely abut are not linked — the span would be zero.
- Rigid belt, uniform patch pressure, small displacement, linear elastic — as everywhere else in the tool.

There is **no calibration factor**. The bar's stiffness comes from its geometry and the compound, like everything else.

---

## Taking it out of the tool: reports and review packs

Both exports carry **exactly the sections ticked under Include** in
*7 · Export* — not everything, every time. A design review wants four charts and
a headline, and which four changes every meeting. A section whose chart has not
been produced is shown struck through rather than hidden, so the list of what a
report *can* carry stays the same from run to run.

### PDF report

The formal record: cover, settings, headline numbers, the per-lean table, the
physics notes, and each ticked chart on its own page.

Two things it does that are worth knowing:

- **The rolled-out pattern is cut into segments** stacked down the page. A tread
  is six to fourteen times longer than it is wide, so at page width it was an
  unreadable ribbon a centimetre tall. Split into five 72° segments, the blocks
  are the size they need to be to look at.
- **Greek is spelled out** — "theta 0° to 72°", not "θ". The PDF's built-in
  fonts have no Greek, and embedding a Unicode font would add a few hundred KB
  to every file for four letters. The charts are images and keep the real
  symbols.

### Interactive review pack

**A PDF is a picture of a chart.** In a review the question is always *"what is
it at 140 degrees?"*, and a picture cannot answer it.

The review pack is one HTML file with only the ticked sections and **the charts
still live** — hover for values, drag to zoom, double-click to reset. It has no
inputs, no compute engine, no DXF and no way back to them: a read-only view of
one run, which is what should leave the department.

Open it by double-clicking. It needs nothing installed and touches no network —
the plotting library travels inside it, which is why it is about 5 MB whatever
it contains. The contact-patch band is drawn in, so where the patch sits is
visible without having to ask.

---

## The five minutes that matter

If you're short on time, look at these four things in this order:

1. **The banner at the top.** It tells you what's real data and what's modelled
   in *this particular run*. Everything below is only as good as that.
2. **Diagnostics tab.** Red items first. Each one names the number, the lean
   angle it's worst at, and what it means.
3. **Lean sweep → Fluctuation vs. lean angle.** The single most 2W-specific
   chart. Does the ripple get worse as you lean?
4. **Order content → the tall bars.** One dominant bar means a tonal whine at
   that order. A spread of small bars means broadband hiss, which is much less
   annoying.

Then come back for the detail.

---

## Running it on your own tyre

The report you are reading is an **output**. Inputs go in on the command line or
in a config file — there is no upload box in the page itself.

```bash
python build_report.py --write-example-config my_tyre.json
# edit my_tyre.json: point `pattern.dxf` at your drawing, set nsd_mm, etc.
python build_report.py --config my_tyre.json
```

The **About** tab records every setting this particular report was built with, so
you can always see — and reproduce — how a number was produced.

Two inputs matter more than the rest and neither is in a 2D drawing:

- **`nsd_mm`** — non-skid depth, i.e. block height. It sets both the bending
  length of every block and the Gent shape factor, so it drives stiffness hard.
- **crown radii** — they decide where the contact patch sits at each lean angle
  and how much it narrows. A measured cross-section (`crown_section_csv`) removes
  the guess.

## Reading the header

**The chips** across the top are the run's facts: circumference, tread width,
how many blocks and pitches, the raster resolution the maths ran at, the
steepest lean the crown profile can reach, and whether curvature correction is
on.

**The coloured banner** below them is the honesty statement. It lists what came
from real data ("tread geometry (from DXF)") and what is modelled or assumed
("crown profile (guessed)"). **If a number surprises you, check the banner
first** — the surprise may be an assumption, not a finding.

**The theme buttons** (Auto / Light / Dark) at the right change the colour
scheme. Auto follows your operating system.

---

## Getting around: one tab bar, two rows

Everything is behind one bar under the title, and it stays there while you
scroll.

**Set up** — the top row — is everything you supply: the drawing, the things a
2D drawing cannot tell you (depth, compound, sipes), the contact patch, the ribs,
the load, the tie bars, and the exports. Each tab is tinted with its section's
own colour, so the tab and the card it opens are recognisably the same thing.

**Results** — the bottom row — is everything the run produced. Those tabs are
listed from the start, so you can see what the run is going to give you, but
they stay greyed until there are results. *How to read this* is the exception:
it is worth reading before the first run.

**Run** sits at the right-hand end of the setup row and is reachable from every
tab. Pressing it opens the θ sweep — but if you are already on a result tab it
leaves you there, so re-running to compare two settings does not throw you back
to the top.

The import summary and the physics notes sit above the panels rather than
inside one, because they qualify every number on every tab.

---

## Controls bar
- **Lean angle γ** — pick which lean angle is *highlighted*. On most charts all
  lean angles are drawn together; the selected one is bold and opaque, the rest
  are faded. This lets you follow one condition through without losing context.
- **Rotation θ** — slide the wheel round. The ▶ button animates it.
- **Overlay** — toggles for the pressure contours, travel-direction arrows and
  centroid markers on the pattern map.

---

## Tab 1 — Pattern & patch

### Rolled-out pattern with contact patch

**What it is.** The tread cut open and laid flat, like peeling the label off a
bottle. Left–right is the way the wheel rolls; up–down is across the tyre from
one shoulder to the other. The window follows the θ slider.

**How to read it.**
- Coloured shapes are blocks, tinted by zone — blue **centre**, yellow
  **intermediate**, pink **shoulder**. Black between them is groove.
- The **white outline** is the contact patch: the only part touching the road
  right now.
- **Dotted rings inside it** are pressure contours — labelled 25% p₀, 50%, 75%
  of the peak pressure. Tightly bunched rings mean pressure falls off fast from
  the middle.
- **Short cyan lines** show which way the rubber is actually travelling. Upright
  they're all parallel. Leaned over they fan out, because a cornering tyre
  pivots as well as rolls.
- **Two markers** in the middle: a grey circle for where the patch centre *is*,
  a red ✕ for where the rubber's centre of area actually sits. If they separate,
  the pattern is pulling the effective contact off-centre.

**What to look for.** Drag θ slowly and watch how many blocks the white outline
covers. If it swings between, say, 1 and 3 blocks, that's exactly the ripple the
rest of the report quantifies. Then switch γ to 40° and watch the patch move out
to the shoulder and get narrower.

### Crown section

**What it is.** A cross-section of the tyre, as if you sliced it like a bagel.
The grey curve is the tread surface, the dashed cyan line is the road, the red
dot is where they touch.

**How to read it.** Change the lean angle and the red dot walks outward along
the curve. That's the physical reason the contact patch moves toward the
shoulder when you lean — the tyre is simply rolling onto a different part of
itself.

### Patch stats (the list below the crown section)

| Row | What it tells you |
|---|---|
| patch length / width | Size of the contact patch in mm. Watch it narrow with lean. |
| patch area | mm². A credit card is about 4600 mm² for scale. |
| lateral centre | How far off the centreline the patch sits. 0 upright, ~74 mm at 40°. |
| peak pressure | Highest contact pressure, MPa. Rises as the patch shrinks. |
| normal load | The vertical force being carried, newtons. |
| lateral radius | How sharply the tyre is curved where it's touching. Small = shoulder. |
| path radius | The corner radius this lean angle implies. ∞ = going straight. |
| compactness | Perimeter²/area. **4π ≈ 12.6 is a perfect circle**; higher means a longer, thinner or more ragged patch. |
| aspect L/W | Length ÷ width. Above 1 means longer than it is wide. |
| clipped by tread edge | **"yes" is a warning** — the patch is running off the edge of the tread, so those numbers lean more on the model. Normal near maximum lean. |

### Full revolution overview

**What it is.** Contact area for the entire 360° of the wheel, one curve per
lean angle, **with the rolled-out tread pattern drawn underneath on the same
rotation axis**. The white vertical line is where the θ slider is. Small ticks
along the bottom mark pitch boundaries.

**How to use the strip.** Find a dip in the curve, read straight down, and you
are looking at the piece of tread that caused it — usually a groove crossing the
patch. That is the whole point of putting them on a shared axis: it turns "the
area drops at 137°" into "the area drops because *this* groove is passing
through".

**How to read it.** The **wiggliness is the story**, not the height. A flat line
would be a perfect tyre. Regular sawtooth = a repeating pattern beating
regularly. Irregular = a varied pitch sequence.

Curves for different lean angles sit at different heights simply because the
patch is a different size — that's expected and not a finding by itself.

---

## Tab 2 — Contact & stiffness

### Every θ signal, aligned with the tread pattern ⭐

**What it is.** Contact area, K<sub>x</sub>, K<sub>y</sub>, K<sub>z</sub> and
block count stacked in one figure, all sharing a single rotation axis, with the
rolled-out pattern along the bottom on that same axis.

**How to read it.** Pick any feature — a dip, a spike, a flat spot — and read
vertically. Every row is showing you the same instant of the same revolution, and
the bottom row shows you which blocks were under the patch at that instant.

This is the chart for answering "*why* does the curve do that". A dip in all four
signals at once means a groove crossing the whole patch. A dip in K<sub>x</sub>
alone with area unchanged means the blocks in contact swapped for softer ones.

The individual charts below repeat these signals larger, with anomaly bands
marked, when you want to look at one in detail.

### The individual signal charts

All of them share the same x-axis: rotation angle θ, 0–360°. One curve per lean
angle, the selected one bold.

### Contact area vs. rotation angle

**What it is.** How many mm² of rubber are inside the patch, as the wheel turns.

**How to read it.** Look at the *peak-to-trough* swing. A curve that runs between
1900 and 2100 mm² is wobbling ±5%; between 1500 and 2500 it's wobbling ±25% and
that will be felt.

**Red shaded bands** mark places where the area changes abnormally fast — a
block snapping into or out of contact. Isolated narrow bands are normal. Lots of
them, or wide ones, mean abrupt transitions.

### In-contact stiffness Kx and Ky

**What it is.** Add up the stiffness of every block currently in the patch. Kx is
the fore–aft direction (braking, accelerating), Ky is sideways (cornering).
Blocks only partly inside count in proportion.

**How to read it.** Same as area: the *ripple* matters more than the level. If Kx
swings a lot through a revolution, the tyre's response to a brake input depends
on where the wheel happens to be — which the rider feels as a slight
inconsistency, and which shows up as uneven wear.

Ky mattering most at high lean, Kx most upright, is a reasonable rule of thumb.

### Kxy — the cross term

**What it is.** The off-diagonal of the same 2×2 stiffness. It says how much a
deflection along the tyre produces a force *across* it — which is exactly what an
angled lug does. It is the only stiffness on the page that can be **negative**.

**How to read it — two ways, and both matter.**

- **The swing** is how strongly the pattern couples the two directions. A
  straight rib has no cross term at all: Kxy is exactly zero, everywhere. An
  angled or directional block pattern swings as each lug enters and leaves the
  patch.
- **The mean** says whether that coupling is *balanced*. On a symmetric pattern
  the left-leaning lugs cancel the right-leaning ones and the mean sits at
  essentially zero however large the swing. A mean that is a real fraction of
  the swing is a pattern with a built-in pull.

Because a balanced pattern's mean is near zero, a CoV against it would be
arithmetic rather than a reading — so wherever Kxy is summarised the tool shows
**± the half-swing** instead, and absolute rather than percentage differences.

It is offered on the **Compare** tab, and it is in the CSV export as
`kxy_N_per_mm`.

### Anisotropy Kx/Ky

**What it is.** The ratio of the two. **1.0 means the tyre is equally stiff in
both directions.**

**How to read it.** Above 1, the pattern resists braking better than cornering;
below 1, the reverse. What matters is less the value than **how much it moves**
through a revolution — a ratio that swings means the tyre's character changes
depending on wheel position.

### Blocks in contact

**What it is.** How many blocks are in the patch at once. It's fractional
("effective") because a block half inside counts as 0.5.

**How to read it.** **This is the most important number in the report and the
easiest to understand.** More blocks = more averaging = smoother everything.

- **Below ~2**: very little averaging. Every block transition shows up in the
  force. Expect a ripply area curve.
- **4 to 8**: comfortable.
- Big-lug patterns naturally sit low. That's a design consequence, not an error.

---

## Tab 3 — Order content

### Rotation order content of contact area

**What it is.** The contact-area wiggle broken into "how many times per
revolution". This is the noise engineer's view.

**How to read it.**
- x-axis = order = **events per wheel revolution**. Order 24 means something
  happens 24 times per turn of the wheel.
- y-axis = how big that component is, as a % of the average contact area.
- **One tall isolated bar = a pure tone.** A whine at a single frequency, which
  the human ear picks out easily and finds annoying.
- **Many small bars = broadband.** Sounds like hiss. Much less objectionable
  even at the same total energy.

**The design goal is usually to flatten this chart**, not to reduce its total —
spreading the same energy across many orders is what pitch modulation is for.

**Rough frequency conversion.** At 60 km/h a 2 m circumference wheel turns about
8 times a second, so order 24 lands near 200 Hz — squarely in the range you can
hear.

### Pitch length sequence

**What it is.** The length of each pitch, going round the tyre. The dashed line
is the average.

**How to read it.** All bars the same height = **no pitch modulation** — the
pattern simply repeats, which concentrates noise into one order. Bars varying =
the designer has deliberately varied the pitch to smear the noise out.

Watch for a big jump between two *neighbouring* bars: an abrupt change can be
felt as a beat even when the overall spread is good.

### Spectrum of the pitch sequence itself

**What it is.** The same order analysis, but of the pitch lengths rather than the
contact response. It looks at the *design intent* independent of any contact
model.

**How to read it.** One dominant bar = tonal sequence. Many small bars = well
spread. Compare it with the contact-area orders: if both peak at the same order,
you've traced the noise straight back to the pitch sequence.

### Polar summary

**What it is.** The same contact-area curve, wrapped into a circle so it looks
like the tyre. Radius = contact area relative to its own average, so 1.0 is a
perfect circle.

**How to read it.** **A smooth circle is good.** Lumps, flat spots or a
star-shaped outline show you *where around the tyre* the weak spots are — which
is hard to see on a straight line chart.

---

## Tab 4 — Lean sweep

Everything here plots against lean angle γ instead of rotation.

### Fluctuation vs. lean angle ⭐

**What it is.** The headline chart. For each lean angle, how big is the ripple
(as CoV, a percentage) in contact area, Kx, Ky and block count.

**How to read it.** **Lower is better, everywhere.** The shape of the curve is
the message:

- **Rising with lean** — the classic 2W problem. The patch shrinks when you lean,
  holds fewer blocks, averages less, ripples more. Exactly when the rider is
  most committed and least tolerant of surprises.
- **Flat** — the pattern behaves consistently through the lean range. Good.
- **Peaking mid-lean then falling** — often means the shoulder blocks are smaller
  and more numerous than the centre ones, so averaging partially recovers at full
  lean. Worth understanding rather than "fixing".

### Blocks in contact vs. lean

**What it is.** Three lines: the **effective** (fractional) count, the
**discrete** count of blocks more than half inside, and the **worst θ** — the
fewest blocks at any point in the revolution.

**How to read it.** The **worst θ line is the one that bites.** An average of 3
blocks with a worst case of 1 means there's a point in every revolution where
the tyre is standing on a single block.

### Patch size and shape vs. lean

**What it is.** Patch length, width (left axis, mm) and area (right axis, mm²).

**How to read it.** The expected motorcycle signature is **width falling, length
rising, area falling** as lean increases — the patch turns from a stubby oval
into a narrow stripe as the tyre rolls onto its tightly curved shoulder. If your
chart doesn't do that, question the crown profile.

### Centroid wander: geometric vs. residual

**What it is.** Where the effective centre of contact sits, split into two parts.

- The **grey line (geometric)** is where lean and the crown profile *have* to put
  it. Not a flaw — it's just geometry, and it will be large (tens of mm).
- The **bars (residual)** are what the block layout adds on top. Split further
  into a constant **bias** and the **wander** that varies as the wheel turns, plus
  the peak-to-peak spread.

**How to read it.** **Ignore the grey line for design purposes** — you cannot
change it without changing the tyre profile. **The bars are the actionable part.**
A few mm of residual wander means the pattern is tugging the effective contact
point side to side once per pitch, which is felt as a slight weave and shows as
asymmetric wear.

### Per-lean summary table

Every number above in one table. Useful for copying into a report, and for
spotting the "clipped: yes" rows where the patch runs off the tread.

---

## Tab 5 — Zones

### Land ratio by lateral position

**What it is.** Going across the tread from one shoulder to the other, what
fraction is rubber? Bars are coloured by zone; the dashed white line is the
overall average.

**How to read it.** **Deep dips are grooves** — expected and fine. What matters
is the *general level* of each region:

- A zone much lower than the others has less rubber carrying load there, so it
  runs hotter and wears faster.
- A big difference between the left and right halves means the tyre behaves
  differently leaning left versus right. That's usually unintended.

### Zone share of the contact patch vs. lean

**What it is.** A stacked bar per lean angle showing which zones actually carry
the contact.

**How to read it.** This should be nearly **100% centre when upright** and
progressively **hand over to the shoulder as lean increases**. It's the clearest
picture in the whole report of "which part of the tread is doing the work when".

If the shoulder is still barely involved at high lean, either the pattern's zone
boundaries don't match how the tyre is used, or the crown profile is wrong.

### Zone statistics

Land ratio, rubber per mm of circumference, block count and mean block area for
each zone. Note that **land ratio here is geometric** (where the rubber is) while
**block count is by ownership** (which zone a block's centre falls in) — they
answer different questions, so they won't line up exactly.

---

## Tab 6 — Grooves

### Groove angle relative to instantaneous rolling direction

**What it is.** How obliquely the grooves cross the direction the rubber is
actually travelling.

**How to read it.**
- **90° = a groove running straight across** the direction of travel. Good at
  pumping water sideways out of the patch; noisier, because the whole edge
  strikes the road at once.
- **0° = a groove running along** the direction of travel. Quieter; poorer at
  clearing water across the patch.
- Most patterns sweep progressively from steep at the centre to shallow at the
  shoulder. The white line shows that sweep.

**The shaded bands** are the part unique to motorcycles. A leaning tyre is
cornering, so the patch pivots slightly as it rolls. The *same groove* therefore
meets the flow at a different angle entering and leaving the patch. The band
width is that swing. It is **zero when upright** (no pivot) and grows with lean —
usually a few degrees. Wide bands mean the groove's effective angle is smeared,
so its water-clearing behaviour is less consistent than the drawing suggests.

### Block property distribution

**What it is.** One dot per block: plan area across, stiffness Kx up, coloured by
zone. Hover for that block's height, draft and sipe count.

**How to read it.** Look for **outliers**. A block far below the trend is much
softer than its neighbours and will squirm and wear early. Tight clusters per
zone mean a consistent design; a wide scatter within one zone means blocks in the
same rib behave quite differently from one another.

---

## Tab 7 — Contact patch

### Contact patch definition

**What it is.** The outline of the patch at every lean angle, all overlaid, drawn
in tread coordinates. Dashed grey lines are the tread edges.

**How to read it.**
- **Solid outline = measured.** A real footprint you supplied.
- **Dotted outline = not measured.** Derived from a measurement, or generated.
- Watch the outlines march outward and narrow as lean increases. Any outline
  touching the dashed tread edge is running off the tyre.

### Where each patch came from

The provenance table. `measured` → `interpolated` → `transferred` → `winkler` /
`rhyne`, in descending order of how much you should trust it. The last column
spells out exactly how each was obtained.

**Why this matters.** Contact area, stiffness — everything — is computed through
this window. If the window is modelled, the numbers inherit that. This table
stops a generated patch from ever being mistaken for a measured one.

### Supplying your own

The instructions for importing a footprint. The short version: **one upright
static footprint is worth a lot** — it upgrades every lean angle from "generated"
to "transferred", because the measured shape is kept and only the change with
lean is modelled.

---

## Tab 8 — Diagnostics

### Design flags

Each check as a card, worst first.

- **red `flag`** — outside the expected range, look at it
- **amber `watch`** — borderline
- **green `ok`** — fine

Each card gives the number, where it was worst, and a sentence on why it matters.

**Important caveat: the thresholds are uncalibrated engineering judgement**, not
correlated against measured noise or wear data. So:

- **Comparing two designs is trustworthy.** If design A has 5% area CoV and design
  B has 9%, B really is rippling more.
- **A single verdict is not.** "flag" does not mean the tyre will fail. It means
  this number is high relative to a guess.

### Pitch sequence table

| Row | How to read it |
|---|---|
| pitch count | How many repeats around the tyre. |
| mean / min / max length | The spread of pitch lengths in mm. |
| distinct lengths | **1 means no modulation at all.** More is generally better for noise. |
| modulation index | (max − min) / mean. 0 = uniform; 0.3–0.4 is a healthy spread. |
| largest adjacent step | Biggest jump between *neighbouring* pitches. Large jumps spread the spectrum but can be felt as a beat. |
| dominant sequence order | Which order the sequence concentrates on, and how strongly. |
| spectral concentration | 0–1. **~0.5 is noise-like and good. Near 1.0 means all the energy sits on one order** — a pure tone. |
| closure error | Should be ~0. Non-zero means the pitches don't add up to the circumference, which points at a digitising or import problem. |
| shoulder phase | How far the two shoulders are offset from each other, in pitches. **Near 0 means both shoulders strike together** and their excitation adds up. An offset interleaves them, which is usually deliberate and better. |

### Wear proxies

| Column | How to read it |
|---|---|
| slenderness | Block height ÷ its smallest plan dimension. **Higher = more squirm.** A tall narrow block bends under load, which shows as heel-and-toe wear and lost stiffness. Below ~0.5 is stubby and stiff; above ~1.4 is worth attention. |
| p90 | The 90th percentile — the worst blocks, not the average. |
| mean sipes | Average sipes per block in that zone. |
| mean / min area | Block sizes. A very small min area next to a large mean means a few undersized blocks that will wear first. |

---

## Tab 9 — About

Per-run provenance, in detail: which parts are verified, which are exact, which
are assumed. Plus the exact settings the run used — load, pressure, compound
hardness, resolution, lean angles.

**Read this before quoting any absolute number to anyone.**

---

## Common questions

**Why do the lean-angle curves sit at different heights?**
Because the contact patch is a different size at each lean angle. That's
expected. Compare the *ripple*, not the level.

**Is a high land ratio good?**
It depends. More rubber means more dry grip and more even wear, but less room to
clear water. 0.6–0.75 is typical for a road motorcycle tyre.

**The contact area at 40° lean is much lower than upright. Is that a problem?**
Not by itself — the patch really is smaller when leaned. Check whether the
*ripple* got worse, and whether "clipped by tread edge" says yes (in which case
part of the patch is hanging off the tyre and the model is being stretched).

**What if a chart looks flat and boring?**
That's usually good news. Flat means consistent.

**Why is my stiffness value different from the standalone stiffness tool?**
It shouldn't be. The block-level model is a verified port, agreeing to about 1
part in 10¹¹. But this tool reports the **sum over all blocks in the contact
patch**, not one block in isolation — so the numbers are naturally larger and
change with θ and γ.

**Everything is flagged red. Now what?**
Check the banner and the About tab first. If the crown profile or the contact
patch is modelled rather than measured, the flags may be reacting to the model
rather than to your pattern. Fix the inputs, then re-read the flags.

**Which single change usually helps most?**
Getting more blocks into the contact patch — smaller blocks, or more of them.
Nearly every ripple metric improves with better spatial averaging.
