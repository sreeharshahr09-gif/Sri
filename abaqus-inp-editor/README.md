# Abaqus INP Editor

A browser front end for Abaqus `.inp` input files. It reads a file, splits it
into readable sections, draws the model, lets you edit any part of it, and
writes a valid `.inp` back out.

No install, no server, no network: `abaqus-inp-editor.html` is one
self-contained file. Open it in a browser and drop a file on it.

---

## Using it

**The quick way** — open `abaqus-inp-editor.html` (double-click it, or drag it
onto a browser window). Drop an `.inp` file on the page, or press
**Load a demo model** to look around first.

**From source** — open `index.html` instead. It loads the same code from
`src/`, which is easier to debug. After changing anything under `src/`, run
`node tools/build.js` to regenerate the single-file version.

### Layout

| Pane | What it does |
|---|---|
| Left | Section tree: parts, assembly and instances, materials, interactions, boundary conditions, steps. Filter it with the search box. |
| Middle | The editor for whatever is selected — parameters, data tables, raw text. |
| Right | The model, drawn. Orbit with the left mouse button, pan with shift or the right button, zoom with the wheel, double-click to re-centre. |

Drag the bars between panes to resize them.

### Editing

Every keyword card is editable three ways, on tabs:

- **Parameters** — the `key=value` pairs on the card itself. Leave a value
  empty to write a bare flag such as `GENERATE` or `NLGEOM`.
- **Data** — the lines under the card, as a spreadsheet. Click a cell, type,
  press Enter. Large blocks are windowed and searchable, so a 108,000-row
  `*NODE` card behaves like any other.
- **Raw** — the whole card as text, for anything the grid cannot express.

You can also add and delete cards: materials and their behaviour cards, steps,
boundary conditions, loads, amplitudes and contact pairs. Instance positions
get real translation and rotation fields rather than bare data lines.

`Ctrl+O` opens a file, `Ctrl+E` (or `Ctrl+S`) exports one.

### Export fidelity

This was the main design constraint. **Cards you have not edited are written
back byte for byte from the file you opened**, and edits are written as
narrowly as possible — changing one node coordinate rewrites that one line,
not the surrounding 108,000. So the diff between the original file and the
export shows your changes and nothing else.

The export dialog lists exactly which cards changed before you save. If the
browser blocks the download (some sandboxed viewers do), use **Copy to
clipboard** instead.

---

## What it understands

Parts, assembly, instances (with translation and rotation), node and element
blocks, node and element sets including `GENERATE` and set-of-sets, sections,
surfaces, materials and their behaviour cards, interaction properties,
contact pairs and ties, constraints, amplitudes, initial conditions, boundary
conditions, loads, steps with their procedures, and output requests.

**Anything else still works.** An unrecognised keyword is not dropped or
mangled: it is kept in full, listed under *Other cards*, editable through the
same parameter/data/raw tabs, and exported unchanged. That matters because no
list of Abaqus keywords is ever complete.

### Drawing

The viewer extracts the exterior surface of solid meshes — faces referenced by
exactly one element — and draws that, so a 69,826-element tetrahedral model
becomes ~15,000 triangles and stays responsive. Shells and membranes are drawn
as their own faces, beams and trusses as lines, point elements as points.

- Element families: tetrahedra (`C3D4`/`C3D10`), hexahedra (`C3D8`/`C3D20`),
  wedges (`C3D6`/`C3D15`), pyramids, shells, membranes, rigid and 2-D continuum
  elements, beams, trusses and connectors. Unknown types fall back to a guess
  from their node count.
- Feature edges (creases past 30°) or the full surface mesh, on a toggle.
- Click any element to see its id, type and connectivity; **Open in editor**
  jumps to its row in the connectivity table.
- **Show in 3D** on any set highlights its elements or nodes in the viewport.
- Analytical rigid surfaces (`*SURFACE, TYPE=CYLINDER` and friends) are swept
  from their `START`/`LINE`/`CIRCL` profile so the road or punch is visible.

The renderer is hand-written WebGL — no Three.js, no CDN — which is what keeps
the whole tool to one offline file.

---

## Known limits

Worth knowing before you rely on it:

- **Analytical rigid surfaces are indicative, not exact.** When the generator
  axis is not given explicitly, the profile is swept along local *z* over a
  span scaled to the model. The surface sits in the right place and has the
  right profile; treat its extent as a sketch.
- **`*INCLUDE` is not resolved.** The card is preserved and exported, but the
  referenced file is not pulled in, so its contents do not appear in the tree
  or the view.
- **Mid-side nodes are ignored for display.** A C3D10 is drawn with flat faces
  like a C3D4. The nodes are all still there in the data and in the export.
- **Edits are not validated against Abaqus rules.** The editor will let you
  write a parameter Abaqus rejects. It guarantees the file's *structure*, not
  that your model solves.
- **Mesh edits do not redraw automatically.** Changing coordinates or
  connectivity shows a *Rebuild view* prompt above the viewport; rebuilding a
  large model takes a second or two, so it is left to you to ask for.
- Everything is in memory in one browser tab. Files past a few hundred MB will
  strain it.

---

## Layout of the source

```
abaqus-inp-editor.html   built single-file app (generated)
index.html               same app, loading src/ directly
src/
  parser.js              keyword cards, quoting, continuation, comments
  model.js               parts, assembly, instances, sets, steps
  geometry.js            element topology, exterior surface, feature edges
  writer.js              export, with byte-exact reproduction of untouched cards
  gl.js                  minimal WebGL renderer with colour-index picking
  viewer.js              model to scene, navigation, highlighting
  ui.js                  DOM helpers, virtualised grid, modals, toasts
  panels.js              the per-section editors
  app.js                 shell: file I/O, tree, export
  demo.js                embedded demo model (generated)
  styles.css
tools/
  build.js               inline everything into the single-file build
  test.js                round-trip and geometry checks
  make-demo.py           regenerates the demo model
samples/
  demo-bracket.inp       the demo model as a file
```

## Development

```sh
node tools/test.js                     # unit + round-trip checks on the demo model
node tools/test.js path/to/real.inp    # same checks against a real job file
node tools/build.js                    # rebuild abaqus-inp-editor.html
python3 tools/make-demo.py             # regenerate the demo model
```

`tools/test.js` is the fastest way to sanity-check the tool against a file of
your own: it verifies that the file re-exports byte for byte and that each
part builds a drawable surface, without opening a browser.
