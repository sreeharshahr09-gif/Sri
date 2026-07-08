# Sri — DXF Studio

**`dxf-studio.html`** — a self-contained, browser-based DXF tool for preparing
2D drawings for downstream analysis (e.g. a stiffness tool that only accepts
closed, non-open-ended profiles). No install, no upload: open the file in any
modern browser and drop a DXF in.

## Features

### Load & detect
- Minimal DXF parser (group-code level) — LINE, LWPOLYLINE, POLYLINE+VERTEX
  (with bulge arcs), CIRCLE, ARC, ELLIPSE, SPLINE, POINT, TEXT, MTEXT, SOLID,
  3DFACE, INSERT (block refs, recursive), DIMENSION (renders its block),
  XLINE/RAY, HATCH.
- Splits a multi-drawing file into individual drawings by **spatial
  clustering** (tunable gap), **layer**, or **block/INSERT**. Merge, rename,
  hide, or delete detected drawings.

### Classify & filter
- Every entity is auto-classified: **geometry / dimensions / text /
  construction & center lines / hatches / frames-borders** (by entity type,
  layer-name heuristics, and linetype).
- Frame detection only flags full-bbox rectangles that contain annotation
  text — a rectangular part outline is never mistaken for a border.
- Per-class and per-layer visibility toggles; hidden entities are excluded
  from export. One-click *hide* or *delete* of all non-geometry.

### Closed-profile check (for stiffness tools)
- Endpoint-connectivity analysis with a snap tolerance: every drawing is
  badged **✓ closed** or **⚠ open**, and each open endpoint is marked with a
  red ✚ on the canvas.
- **Join & close chains** — welds touching LINE/ARC segments (within
  tolerance) into single LWPOLYLINEs with correct arc bulges; loops get the
  closed flag, and gaps ≤ tolerance are healed.
- **Delete open-ended** — removes geometry not part of any closed loop.
- **⚡ Prepare for stiffness tool** — one click: strips
  dimensions/text/construction/hatches/frames, joins & closes chains, then
  deletes anything still open. Fully undoable.

### Edit
- Two selection modes: whole **drawings** or individual **entities**
  (click / box-select, Shift add, Alt remove).
- Delete (`Del`), move/translate (drag with the Move tool, `M`), undo
  (`Ctrl+Z`, multi-level).

### Export
- Export selected drawings into one DXF, or each into its own file.
- Optional filters: **geometry only** and **closed profiles only**.
- Unedited records are emitted byte-for-byte, so coordinates are preserved
  exactly; header, tables, and block definitions are carried over so the
  output stays valid.

## Usage

1. Open `dxf-studio.html` in a browser.
2. Load a DXF → drawings are detected and analyzed automatically.
3. Check the ⚠/✓ badges; adjust the snap tolerance if corners that should
   touch are reported open.
4. Click **⚡ Prepare for stiffness tool** (or use the individual cleanup
   buttons), review the result, then **Export selected → DXF** with
   *Closed profiles only* enabled.
