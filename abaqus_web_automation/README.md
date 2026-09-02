# STEP → Abaqus Automation Platform v0.7

A local web application that turns CAD STEP geometry into a repeatable Abaqus analysis workflow.

```text
STEP → browser viewer (pick geometry) → Python → Abaqus build/run → ODB → CSV
```

## What changed in v0.7

The browser now does exactly one job: **show the model and let you click faces, edges and
bodies.** Project state, defaults, validation, sipe detection, package generation and the
solver run all live in Python and are delivered as ordinary server-rendered HTML forms.

| | v0.6.2 | v0.7 |
|---|---|---|
| JavaScript | ~290 lines: viewer, editors, state, validation, polling | ~290 lines of viewer only |
| Project state | in the browser, lost on refresh | `workspace/project.json`, survives restarts |
| Forms | built and re-rendered by JavaScript | server-rendered HTML |
| Errors | `[object Object]` | a readable list at the top of the page |
| Run status | JavaScript polling | a page that refreshes itself while work is outstanding |

## Installation

```text
conda env create -f environment.yml
conda activate abaqus-web-automation
python app.py
```

Then open <http://127.0.0.1:8765>.

## Workflow

1. **Upload STEP.** Python tessellates it and the viewer displays it.
2. **Create named sets.** Choose Faces / Edges / Bodies, click geometry, type a name,
   press *Create / update set*. *Select* on an existing set loads it back into the viewer
   so you can adjust and re-save it.
3. **Detect sipe walls** (optional). Set the radial axis, max gap and min wall depth, press
   *Detect sipes*. Candidates are highlighted purple. Press *Apply* to create
   `SIPE_001_SIDE_A/_SIDE_B/_WALLS`, `ALL_SIPE_WALLS` and one self-contact per sipe.
4. **Upload the material `.inc` library** and pick the material code and body set.
5. **Fill in** solver, mesh, road, steps, contacts, BCs, loads, outputs, sweeps. Press
   *Save settings* at any time.
6. **Generate analysis package.** Problems are listed in red at the top of the page.
7. **Run in Abaqus.** The status page refreshes itself until every case finishes.

## Material library

Constitutive data are **not** recreated in the browser. The generated `build_and_run.py`
writes the Abaqus input file, then inserts:

```text
*Solid Section, elset=<body set>, material=<material code>   (inside the part)
*Include, input=<library>.inc, PASSWORD=<password>           (after *End Assembly)
```

The `.inc` file is copied next to each generated case, so the relative `*Include` resolves.
Encrypted libraries expose no readable `*Material, name=` headers; type the code manually
and Abaqus validates it during input processing.

## Analytical rigid road

Enable **Create road automatically**, then set the normal axis, active side, length/width,
centre, tread contact face set and friction. The model gets an analytical rigid surface and
a reference point named `RP_ROAD`; use it in the Boundary Conditions section for preload,
sliding or torsional motion.

The generated script reads the extrusion direction off the created surface rather than
assuming it, so the road lands at the centre you asked for on any Abaqus release.

## Multi-step motion

For preload followed by motion, use **Modify existing BC**:

```text
Step 1: Preload       BC RoadMotion, operation = Create, U3 = -preload
Step 2: Longitudinal  operation = Modify existing BC, Target BC = RoadMotion, U1 = 10
                      (blank fields are left unchanged)
```

Validation rejects a modification that acts in the same step as, or before, the step that
creates the BC.

## Element types

Nearly incompressible rubber normally needs **hybrid** elements. The default is `C3D10H`;
choosing `C3D10`/`C3D4` with Abaqus/Standard raises a warning about volumetric locking.
Element types are restricted per solver (`C3D10M`/`C3D4` for Explicit).

## Outputs

`extract_odb.py` loops over **all steps and all frames** and writes:

- `History_All_Steps.csv` — every history region and variable
- `RP_History_Wide.csv` — U/UR/RF/RM histories in a wide table
- `Energy_History.csv` — global energy histories when present
- `<VAR>__<REGION>__ALL_STEPS.csv` — one file per configured field output
- `extraction_summary.csv`

CSV files are opened through a helper that works on both the Python 2 interpreter shipped
with Abaqus ≤2023 and the Python 3 interpreter in 2024+.

## Units

Abaqus imposes no units; use one consistent system. For N-mm-MPa:

```text
U mm · UR rad · RF N · RM N·mm · S MPa · LE - · CPRESS MPa · COPEN mm · CSLIP mm
```

## Tests

```text
python -m pytest tests/ -q
```

The pure-Python tests (names, validation, parametric expansion, project mutations) run
anywhere. The geometry tests skip automatically if CadQuery is not installed.

## Validation status

STEP parsing, geometry metadata, sipe detection, parametric expansion, project validation,
generated-script syntax, the INP material-injection step, all HTTP endpoints, and the full
generate→run→status cycle (with a stub launcher) are covered by automated tests.

Abaqus itself is proprietary and is not installed in the development environment, so the
generated Abaqus API calls still need an acceptance run on your workstation. Keep the first
solver run small. Abaqus-specific commands are confined to `core/abaqus_generator.py`, so a
release-specific correction does not touch STEP parsing, selection or the project schema.

## Layout

```text
app.py                     HTTP routes; renders pages, handles form posts
core/project.py            project schema, defaults, form parsing, persistence
core/names.py              Abaqus identifier rules (shared by generator and validation)
core/validation.py         all project checks
core/cad.py                STEP import and tessellation
core/sipe_detection.py     geometric sipe-wall candidate detection
core/material_library.py   *Material, name= scanning
core/parametric.py         sweep expansion
core/abaqus_generator.py   build_and_run.py and extract_odb.py generation
core/runner.py             launches Abaqus and tracks run status
templates/                 server-rendered HTML
static/viewer.js           the only JavaScript: viewer and picking
```
