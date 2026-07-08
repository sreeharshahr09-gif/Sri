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
- **✏ Bridge tool** (`B`) — click two open endpoints and a straight LINE is
  drawn between them to close the profile manually: endpoints snap under the
  cursor, a dashed preview shows the closing line with its length, and the
  new line inherits the profile's layer.
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
- Two formats:
  - **Clean (R12)** *(recommended)* — writes a minimal, self-consistent DXF
    containing only the exported geometry: fresh HEADER with recomputed
    extents, a minimal LAYER table, and the entities converted to
    widely-readable R12 records (arcs preserved as polyline bulges;
    ellipses/splines/inserts flattened). No handles, dictionaries, or
    leftover dimension-association objects — so strict CAM / stiffness-tool
    readers load the closed loops reliably.
  - **Faithful** — copies the original HEADER/TABLES/BLOCKS/OBJECTS verbatim.
    Preserves everything, but if you deleted dimensions/text their
    association objects can be left dangling; use only when you need the
    original blocks/metadata.
- Optional filters: **geometry only** and **closed profiles only**.
- Optional **recenter near origin** — shifts all exported entities by one
  common offset (relative layout preserved) so tools that can't display
  geometry placed far from the origin still show it.

> **Why "clean" exists:** a real export from this tool was rejected by a
> downstream stiffness tool. The loops were geometrically valid closed
> polylines, but the faithful export had carried over the original
> `OBJECTS` section, which still held `DIMASSOC` objects pointing at the
> handles of dimensions that had been deleted. Lenient readers silently drop
> those; strict readers fail to load the model space, so the loops never
> appear. The clean R12 export emits no such baggage.

## Usage

1. Open `dxf-studio.html` in a browser.
2. Load a DXF → drawings are detected and analyzed automatically.
3. Check the ⚠/✓ badges; adjust the snap tolerance if corners that should
   touch are reported open.
4. Click **⚡ Prepare for stiffness tool** (or use the individual cleanup
   buttons), review the result, then **Export selected → DXF** with
   *Closed profiles only* enabled.
