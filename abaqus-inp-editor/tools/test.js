#!/usr/bin/env node
/*
 * test.js -- checks the parts of the editor that do not need a browser:
 * parsing, model building, surface extraction and export fidelity.
 *
 *   node tools/test.js [path/to/model.inp ...]
 *
 * With no arguments it runs against the bundled demo model. Any extra .inp
 * files given on the command line are put through the same round-trip checks,
 * which is the quickest way to try the tool against a real job file.
 */
'use strict';

const fs = require('fs');
const path = require('path');

const root = path.resolve(__dirname, '..');
global.window = global;
require(path.join(root, 'src/parser.js'));
require(path.join(root, 'src/model.js'));
require(path.join(root, 'src/geometry.js'));
require(path.join(root, 'src/writer.js'));
require(path.join(root, 'src/demo.js'));

const { InpParser: P, InpModel: M, InpGeometry: G, InpWriter: W } = global;

let passed = 0;
let failed = 0;

function check(name, cond, detail) {
  if (cond) {
    passed++;
    console.log('  ok   ' + name);
  } else {
    failed++;
    console.log('  FAIL ' + name + (detail ? '  (' + detail + ')' : ''));
  }
}

function section(title) {
  console.log('\n' + title);
}

/* ------------------------------------------------------------- unit tests */

section('parser');
{
  const r = P.parse('*Elset, elset="My Set, A", internal, generate\n1, 10, 1\n');
  const b = r.blocks[0];
  check('quoted parameter value keeps its comma', P.getParam(b, 'elset') === 'My Set, A',
    P.getParam(b, 'elset'));
  check('flag parameter has no value', P.hasParam(b, 'internal') && P.getParam(b, 'internal') === null);
  check('keyword name is normalised for lookup', b.key === 'ELSET');
  check('data line is captured', P.dataRows(b).length === 1);
}
{
  const r = P.parse('*Element, type=C3D20,\n elset=Solids\n1, 1, 2, 3, 4,\n5, 6, 7, 8\n');
  const b = r.blocks[0];
  check('continued keyword card is joined', P.getParam(b, 'elset') === 'Solids');
  const rows = P.dataRows(b);
  check('continued data line is one row', rows.length === 1 && rows[0].fields.length === 9,
    rows.length + ' rows / ' + (rows[0] && rows[0].fields.length) + ' fields');
}
{
  const r = P.parse('** a comment\n** another\n*Node\n1, 0., 0., 0.\n');
  check('comments attach to the following card', r.blocks[0].comments.length === 2);
}

section('sets');
{
  const r = P.parse('*Nset, nset=A, generate\n1, 9, 2\n');
  const ids = M.expandSet(r.blocks[0]);
  check('GENERATE expands with an increment', ids.join(',') === '1,3,5,7,9', ids.join(','));
}

section('geometry');
{
  check('C3D10 classifies as a tetrahedron', G.classify('C3D10H', 10) === 'tet');
  check('C3D8R classifies as a hexahedron', G.classify('C3D8R', 8) === 'hex');
  check('S4R classifies as a quad face', G.classify('S4R', 4) === 'quad');
  check('B31 classifies as a line', G.classify('B31', 2) === 'line');
  check('unknown 6-node type falls back to a wedge', G.classify('ZZZ', 6) === 'wedge');

  /* Two hexes sharing a face: the shared face must not reach the surface. */
  const inp = [
    '*Node',
    '1, 0., 0., 0.', '2, 1., 0., 0.', '3, 1., 1., 0.', '4, 0., 1., 0.',
    '5, 0., 0., 1.', '6, 1., 0., 1.', '7, 1., 1., 1.', '8, 0., 1., 1.',
    '9, 0., 0., 2.', '10, 1., 0., 2.', '11, 1., 1., 2.', '12, 0., 1., 2.',
    '*Element, type=C3D8',
    '1, 1, 2, 3, 4, 5, 6, 7, 8',
    '2, 5, 6, 7, 8, 9, 10, 11, 12'
  ].join('\n');
  const parsed = P.parse(inp);
  const nodes = M.readNodes(parsed.blocks.filter(b => b.key === 'NODE'));
  const elems = M.readElements(parsed.blocks.filter(b => b.key === 'ELEMENT'));
  const mesh = G.buildDisplayMesh(nodes, elems, null, { featureAngle: 30 });
  check('interior face is dropped from the surface', mesh.surfaceFaceCount === 10,
    mesh.surfaceFaceCount + ' faces');
  check('surface is triangulated', mesh.triangleCount === 20, mesh.triangleCount + ' triangles');
  check('bounds span the stacked blocks', mesh.bounds.max[2] === 2 && mesh.bounds.min[2] === 0);
}
{
  /* Rotation is applied about the given axis after the translation. */
  const xf = G.instanceTransform([0, 0, 0], [0, 0, 0, 0, 0, 1, 90]);
  const out = G.applyTransform(xf, 1, 0, 0, [0, 0, 0]);
  const near = (a, b) => Math.abs(a - b) < 1e-9;
  check('90° rotation about z maps x onto y', near(out[0], 0) && near(out[1], 1),
    out.join(','));
}

/* ------------------------------------------------------- round-trip tests */

function roundTrip(label, text) {
  section('round trip: ' + label);
  const t0 = Date.now();
  const model = M.build(P.parse(text));
  const parseMs = Date.now() - t0;
  const stats = M.stats(model);
  console.log('  ' + stats.nodes.toLocaleString() + ' nodes, ' +
    stats.elements.toLocaleString() + ' elements, ' + stats.blocks + ' cards, ' +
    parseMs + ' ms');

  const normalised = text.replace(/\r\n/g, '\n');
  const out = W.write(model);
  check('untouched export reproduces the source exactly',
    out === normalised || out === normalised + '\n',
    'in ' + normalised.length + ' bytes, out ' + out.length);

  /* One parameter edit must change exactly one line. */
  const target = model.steps[0] ? model.steps[0].block : model.blocks[1];
  P.setParam(target, '__test_flag', 'ABC');
  const edited = W.write(model);
  const A = normalised.split('\n'), B = edited.split('\n');
  let diff = 0;
  for (let i = 0; i < Math.max(A.length, B.length); i++) if (A[i] !== B[i]) diff++;
  check('one parameter edit changes one line', diff === 1, diff + ' lines changed');

  /* Mesh edits stay surgical too. */
  const nodeBlock = model.parts.map(p => p.container.nodeBlocks[0]).filter(Boolean)[0];
  if (nodeBlock) {
    const rows = P.dataRows(nodeBlock);
    rows[0].fields[1] = '-1.5';
    P.markRow(nodeBlock, rows[0]);
    const edited2 = W.write(model);
    const C = edited2.split('\n');
    let diff2 = 0;
    for (let i = 0; i < Math.max(A.length, C.length); i++) if (A[i] !== C[i]) diff2++;
    check('a coordinate edit adds exactly one more changed line', diff2 === 2,
      diff2 + ' lines changed');
  }

  /* Every part must produce drawable geometry or be legitimately empty. */
  model.parts.forEach(part => {
    if (part.elements.count === 0) return;
    const mesh = G.buildDisplayMesh(part.nodes, part.elements, null, { featureAngle: 30 });
    check('part "' + part.name + '" builds a surface',
      mesh.triangleCount > 0 && mesh.skipped === 0,
      mesh.triangleCount + ' triangles, ' + mesh.skipped + ' elements skipped');
  });
}

roundTrip('demo model', global.DEMO_INP);

process.argv.slice(2).forEach(file => {
  roundTrip(path.basename(file), fs.readFileSync(file, 'utf8'));
});

console.log('\n' + passed + ' passed, ' + failed + ' failed');
process.exit(failed ? 1 : 0);
