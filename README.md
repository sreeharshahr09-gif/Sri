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

### Save & resume a session (JSON project)
- **Save project** writes the entire working session to a `.json` file —
  parsed geometry, every edit (deletes, moves, rotations, joined polylines),
  drawing grouping and names, layer/class visibility, topology tolerance and
  export settings, theme and view.
- **Open project** restores it exactly, recomputing all derived geometry and
  topology, so a large, complicated tire profile can be picked up right where
  it was left off — no need to redo the cleanup. Round-trip is lossless: an
  export from a restored session is byte-identical to the original.

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
- Closure is judged at the **snap tolerance**, not a hard floating-point
  epsilon, so a loop that is geometrically closed but left with a tiny
  sub-unit gap (common in real CAD output, and in 3D polylines whose
  "closed" flag was never set) is correctly recognised as closed — and
  **exported** with the closed flag set, instead of silently dropping out of
  a downstream tool that only accepts closed profiles.
- **Join & close chains** — welds touching LINE/ARC segments (within
  tolerance) into single LWPOLYLINEs with correct arc bulges; loops get the
  closed flag, and gaps ≤ tolerance are healed.
- **✏ Bridge tool** (`B`) — click two open endpoints and a straight LINE is
  drawn between them to close the profile manually: endpoints snap under the
  cursor, a dashed preview shows the closing line with its length, and the
  new line inherits the profile's layer.
- **⟶ Extend** (`E`) — a free line end that stops just short of a crossing
  line is the usual reason a tie-bar region will not close. Click the end and
  it is stretched along its own direction until it meets the nearest piece of
  geometry (hover shows the reach and the exact distance). **Extend all free
  ends** fixes every such shortfall in the selection at once, limited by a
  **max gap** so nothing shoots across the drawing. Undoable.
- **⋈ Weld** (`W`) — when a line is *broken in the middle* and the two halves
  are slightly offset, the ends are parallel so Extend can never reach: weld
  pulls both ends onto their midpoint instead, closing the break exactly.
  Hover shows the pair and the gap distance; **Weld all free ends** fixes
  every such break in the selection, capped by **max gap**. If nothing is in
  range the tool reports the nearest actual gap so you know what to set.
- **Delete open-ended** — removes geometry not part of any closed loop.
- **⚡ Prepare for stiffness tool** — one click: strips
  dimensions/text/construction/hatches/frames, joins & closes chains, then
  deletes anything still open. Fully undoable.

### Edit
- Two selection modes: whole **drawings** or individual **entities**
  (click / box-select, Shift add, Alt remove).
- Delete (`Del`), move/translate (drag with the Move tool, `M`), rotate
  (`R` / `Shift+R` about the selection centre), and full **undo / redo**
  (`Ctrl+Z` / `Ctrl+Y`, multi-level) — every edit is reversible in both
  directions.

### Draw & trim
- **Line** tool (`L`) — click start, click end. A live preview shows the
  segment and its length; points **snap** to nearby existing vertices/
  endpoints (green ▫ marker).
- **Polyline** tool (`P`) — click each vertex; `Enter` or double-click
  finishes an open polyline, `C` (or clicking the first vertex) closes it,
  `Backspace` drops the last vertex, `Esc` cancels. Same snapping.
- New geometry joins the single selected drawing (or a dedicated *Drawn
  geometry* group), so it flows straight into the topology check and export.
- **Trim** tool (`X`) — click the part of a **straight line** you want to
  cut away; it's removed between that line's nearest intersections with the
  surrounding geometry (splitting the line in two if the piece is in the
  middle). Undoable like everything else. Trim targets straight lines — run
  it before **Join & close** while segments are still separate.

### Workspace
- **Dark / light theme** toggle (`T`) — follows the OS preference on first
  run and remembers your choice; the canvas, panels and markers all adapt.
- Live **cursor coordinate readout** and an **origin (0,0) marker** on the
  canvas — quick to confirm where geometry sits (the far-from-origin case
  that trips up some downstream tools).
- **Section readout** in the closed-profile card: net enclosed **area**
  (outer loops minus holes) and total **perimeter** of the selected
  drawings' closed profiles — the numbers a stiffness/section tool works
  from.

### Tie-bar hatch
- **Hatch** tool (`H`) — click inside *any* enclosed region to mark it with a
  **real DXF HATCH** entity on a dedicated **TIEBAR** layer, so downstream
  tools (e.g. TLPT) recognise the hatched region as a tie bar automatically.
  Click a hatch again to remove it; **Hatch selected regions** hatches every
  closed loop in the selection at once.
- **Automatic region detection** — a tie bar is naturally bounded by pieces
  of several entities (block edges + groove boundaries) and is *never* a
  single closed loop. The tool builds a planar arrangement of every visible
  geometry segment (splitting them at all intersections and T-junctions) and
  extracts the minimal enclosing face around the click, so you can hatch the
  region directly without first joining anything. The boundary only needs to
  close within the snap tolerance.
- Pattern is selectable (**Solid** or **ANSI31** diagonal). The hatch stays
  in sync through move/rotate/undo and is saved in JSON projects.
- Because a real HATCH needs DXF 2000, an export that contains a hatch is
  written as **AC1015** automatically (a full, valid file with symbol
  tables and block records); exports with no hatch stay R12. Tie-bar
  hatches are always included in the export, regardless of the
  geometry-only / closed-only filters.
- If a click finds no region, the boundary has a real gap: use **⟶ Extend**
  on the end that stops short (or **Bridge**/draw the missing edge), then
  fill again.

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
