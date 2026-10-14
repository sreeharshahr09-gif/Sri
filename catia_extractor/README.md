# CATIA V5 data extractor

`catia_extract.py` opens CATIA V5 files and writes out everything it can read about them:
features, dimensions, parameters, sketches, mass properties, assembly structure and drawing
annotations.

CATIA files use a closed binary format, so the script runs CATIA itself through its COM
automation API. To run it you need:

- Windows with **CATIA V5** installed and licensed (it attaches to a running session or starts one)
- Python 3.9+ and `pip install -r requirements.txt`

## Usage

```bat
python catia_extract.py Bracket.CATPart
python catia_extract.py Gearbox.CATProduct --deep --xlsx
python catia_extract.py C:\models --recursive --csv -o results
python catia_extract.py            &:: no file given = the document active in CATIA
```

| Option | Effect |
|---|---|
| `-o DIR` | Output folder (default `catia_extract_output`) |
| `-r` | Search input folders recursively |
| `--csv` | Also write flat CSV tables (all input files combined, one `file` column) |
| `--xlsx` | Also write `catia_extract.xlsx` with one sheet per table (needs `openpyxl`) |
| `--deep` | For assemblies, also do a full part extraction of every part they contain |
| `--no-bbox` | Skip the bounding box |
| `--fast` | Skip the bounding box, sketch and wireframe coordinates, and per-component mass |
| `--visible` | Show the CATIA window if the script starts CATIA |
| `-v` / `-vv` | Tracebacks on failure / debug log of every member CATIA rejected |

Each input produces `<file>.json` with the complete result. Documents the script opens are
closed afterwards **without saving**. Documents that were already open are left open.

## What is extracted

**CATPart**
- Document info: path, size, modified date, read-only flag
- Properties: part number, revision, nomenclature, definition, description, source, user properties
- Material (part and per body), mass, volume, wet area, density, centre of gravity, inertia matrix
- Bounding box of the main body (min/max/size in X, Y, Z)
- Bodies → features (Pad, Pocket, Hole, Shaft, Groove, Fillet, Chamfer, Shell, Draft, Patterns,
  Boolean operations, ...). Each feature has its type, active/inactive state and profile sketch,
  plus every parameter it owns (e.g. `FirstLimit\Length = 20mm`). Common features also get
  readable details such as limit modes, hole type, thread, counterbore, pattern counts and spacing.
- Sketches: placement (origin and H/V axes), element counts, 2D geometry (lines, circles,
  points, ellipses with coordinates, lengths and radii), and constraints with type, value,
  driving/reference mode and the geometry they constrain
- Geometrical sets (normal and ordered, nested): wireframe and surface elements with point
  coordinates, curve lengths and surface areas
- Axis systems, 3D constraints, relations/formulas (with input and output parameters)
- All parameters, plus a `dimensions` list of every physical dimension (length, angle, ...)
  with the feature it belongs to
- A summary with counts per feature type

**CATProduct**
- Full product tree: instance name, part number, revision, nomenclature, source, file,
  position (rotation axes and origin), mass per component, assembly constraints
- Bill of materials with total quantities
- Assembly mass properties, parameters and relations
- `--deep`: the full CATPart extraction for every part in the assembly

**CATDrawing**
- Sheets (paper size, width/height, scale, projection method)
- Views (position, scale, angle, linked 3D document)
- Dimensions (value, CATIA dimension type code, tolerances) and texts

### CSV/Excel tables

`parts`, `parameters`, `dimensions`, `features`, `sketches`, `sketch_constraints`,
`wireframe_surfaces`, `relations`, `components`, `bom`, `assembly_constraints`,
`drawing_views`, `drawing_dimensions`, `drawing_texts`. Only the tables that have rows are
written.

## Notes and units

- Lengths are in mm and angles in degrees, as CATIA's parameters report them.
  Values from `Product.Analyze` and the measure tools are written as the API returns them:
  mass in kg, volume in m³, areas in m². Check them once against *Measure Inertia* on a known
  part if exact units matter to you.
- Enum values are labelled where the mapping is well known. The raw CATIA code is kept next to
  the label (`type_code`, `status_code`, ...) so you can check it against the CATIA V5
  Automation documentation.
- Everything is read best-effort. If one property is missing on a given CATIA release or
  feature, it is skipped and the rest of the extraction carries on. Use `-vv` to see what was skipped.
- The bounding box is computed by adding temporary extremum points to a scratch geometrical
  set. The set is deleted afterwards and the document is never saved. It is skipped for
  documents that were already open in your CATIA session, so your work is never touched.
- Features the script has no specific reader for (Rib, Slot, Stiffener, Mirror, User Pattern,
  ...) still have their type, state, profile and all of their own parameters extracted.
