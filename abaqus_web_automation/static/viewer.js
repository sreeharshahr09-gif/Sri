/* Geometry viewer and entity picking.
 *
 * This is the only JavaScript in the application. It draws the tessellated STEP
 * model, lets you click faces / edges / bodies, and writes the resulting ids
 * into a hidden field of an ordinary HTML form. Project state, validation and
 * package generation all live in Python.
 */
'use strict';

const $ = s => document.querySelector(s);
const canvas = $('#glcanvas');
const gl = canvas.getContext('webgl', { antialias: true });

const view = {
  mode: 'face',
  selection: new Set(),
  faceBody: new Map(),      // face id -> body id
  sipe: new Set(window.SIPE_PREVIEW || []),
  assigned: { face: new Set(), edge: new Set(), body: new Set() },
  tris: [], triFaceIds: [], lines: [], lineEdgeIds: [],
  bbox: null
};

for (const s of (window.SETS || [])) {
  for (const id of s.entity_ids) view.assigned[s.kind].add(id);
}

let prog, posBuf, colBuf, linePosBuf, lineColBuf, aPos, aCol, uMVP;
const camera = { yaw: -0.75, pitch: 0.55, dist: 10, target: [0, 0, 0], pan: [0, 0, 0], fov: 45 };
let drag = null;

function shader(type, src) {
  const s = gl.createShader(type);
  gl.shaderSource(s, src); gl.compileShader(s);
  if (!gl.getShaderParameter(s, gl.COMPILE_STATUS)) throw new Error(gl.getShaderInfoLog(s));
  return s;
}
function initGL() {
  const vs = 'attribute vec3 aPos; attribute vec3 aCol; uniform mat4 uMVP; varying vec3 vCol;' +
             'void main(){gl_Position=uMVP*vec4(aPos,1.0);vCol=aCol;}';
  const fs = 'precision mediump float; varying vec3 vCol; void main(){gl_FragColor=vec4(vCol,1.0);}';
  prog = gl.createProgram();
  gl.attachShader(prog, shader(gl.VERTEX_SHADER, vs));
  gl.attachShader(prog, shader(gl.FRAGMENT_SHADER, fs));
  gl.linkProgram(prog);
  aPos = gl.getAttribLocation(prog, 'aPos');
  aCol = gl.getAttribLocation(prog, 'aCol');
  uMVP = gl.getUniformLocation(prog, 'uMVP');
  posBuf = gl.createBuffer(); colBuf = gl.createBuffer();
  linePosBuf = gl.createBuffer(); lineColBuf = gl.createBuffer();
  gl.enable(gl.DEPTH_TEST);
  gl.enable(gl.POLYGON_OFFSET_FILL); gl.polygonOffset(1, 1);
}

/* --- small matrix helpers --- */
const sub = (a, b) => [a[0] - b[0], a[1] - b[1], a[2] - b[2]];
const add2 = (a, b) => [a[0] + b[0], a[1] + b[1], a[2] + b[2]];
const scale = (a, s) => [a[0] * s, a[1] * s, a[2] * s];
const dot = (a, b) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
const cross = (a, b) => [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];
const norm = a => { const l = Math.hypot(a[0], a[1], a[2]) || 1; return scale(a, 1 / l); };
function matMul(a, b) {
  const o = new Array(16).fill(0);
  for (let r = 0; r < 4; r++) for (let c = 0; c < 4; c++) for (let k = 0; k < 4; k++) o[c * 4 + r] += a[k * 4 + r] * b[c * 4 + k];
  return o;
}
function perspective(fovy, aspect, n, f) {
  const t = 1 / Math.tan(fovy * Math.PI / 360);
  return [t / aspect, 0, 0, 0, 0, t, 0, 0, 0, 0, (f + n) / (n - f), -1, 0, 0, (2 * f * n) / (n - f), 0];
}
function lookAt(eye, center, up) {
  const z = norm(sub(eye, center)), x = norm(cross(up, z)), y = cross(z, x);
  return [x[0], y[0], z[0], 0, x[1], y[1], z[1], 0, x[2], y[2], z[2], 0,
          -dot(x, eye), -dot(y, eye), -dot(z, eye), 1];
}
function mvp() {
  const t = add2(camera.target, camera.pan);
  const cp = Math.cos(camera.pitch), sp = Math.sin(camera.pitch);
  const cy = Math.cos(camera.yaw), sy = Math.sin(camera.yaw);
  const eye = add2(t, [camera.dist * cp * cy, camera.dist * cp * sy, camera.dist * sp]);
  return matMul(perspective(camera.fov, canvas.width / canvas.height,
                            Math.max(camera.dist / 1000, 1e-5), camera.dist * 100 + 1),
                lookAt(eye, t, [0, 0, 1]));
}
function project(p, m) {
  const x = p[0], y = p[1], z = p[2];
  const X = m[0] * x + m[4] * y + m[8] * z + m[12], Y = m[1] * x + m[5] * y + m[9] * z + m[13];
  const Z = m[2] * x + m[6] * y + m[10] * z + m[14], W = m[3] * x + m[7] * y + m[11] * z + m[15];
  return [((X / W) * 0.5 + 0.5) * canvas.clientWidth, (1 - ((Y / W) * 0.5 + 0.5)) * canvas.clientHeight, Z / W];
}

function buildGeometry(payload) {
  const v = payload.mesh.vertices, t = payload.mesh.triangles, ids = payload.mesh.triangle_face_ids;
  for (const f of payload.faces) view.faceBody.set(f.id, f.body_id);
  for (let i = 0; i < t.length; i += 3) {
    view.triFaceIds.push(ids[i / 3]);
    for (let k = 0; k < 3; k++) {
      const vi = t[i + k] * 3;
      view.tris.push(v[vi], v[vi + 1], v[vi + 2]);
    }
  }
  for (const e of payload.edges) {
    for (let i = 0; i < e.polyline.length - 1; i++) {
      view.lines.push(...e.polyline[i], ...e.polyline[i + 1]);
      view.lineEdgeIds.push(e.id);
    }
  }
  view.bbox = payload.bbox;
  gl.bindBuffer(gl.ARRAY_BUFFER, posBuf);
  gl.bufferData(gl.ARRAY_BUFFER, new Float32Array(view.tris), gl.STATIC_DRAW);
  gl.bindBuffer(gl.ARRAY_BUFFER, linePosBuf);
  gl.bufferData(gl.ARRAY_BUFFER, new Float32Array(view.lines), gl.STATIC_DRAW);
  fitView();
  rebuildColors();
}

function rebuildColors() {
  const fc = [];
  for (const fid of view.triFaceIds) {
    const body = view.faceBody.get(fid);
    let c = [0.72, 0.78, 0.82];
    if (view.assigned.face.has(fid) || view.assigned.body.has(body)) c = [0.35, 0.68, 0.65];
    if (view.sipe.has(fid)) c = [0.55, 0.34, 0.78];
    if (view.mode === 'face' && view.selection.has(fid)) c = [0.95, 0.56, 0.18];
    if (view.mode === 'body' && view.selection.has(body)) c = [0.95, 0.56, 0.18];
    fc.push(...c, ...c, ...c);
  }
  gl.bindBuffer(gl.ARRAY_BUFFER, colBuf);
  gl.bufferData(gl.ARRAY_BUFFER, new Float32Array(fc), gl.DYNAMIC_DRAW);

  const ec = [];
  for (const id of view.lineEdgeIds) {
    let c = [0.18, 0.25, 0.31];
    if (view.assigned.edge.has(id)) c = [0.12, 0.53, 0.43];
    if (view.mode === 'edge' && view.selection.has(id)) c = [0.95, 0.4, 0.12];
    ec.push(...c, ...c);
  }
  gl.bindBuffer(gl.ARRAY_BUFFER, lineColBuf);
  gl.bufferData(gl.ARRAY_BUFFER, new Float32Array(ec), gl.DYNAMIC_DRAW);
  draw();
}

function draw() {
  if (!gl) return;
  gl.clearColor(0.88, 0.91, 0.94, 1);
  gl.clear(gl.COLOR_BUFFER_BIT | gl.DEPTH_BUFFER_BIT);
  if (!view.tris.length) return;
  gl.useProgram(prog);
  gl.uniformMatrix4fv(uMVP, false, new Float32Array(mvp()));
  gl.enableVertexAttribArray(aPos); gl.enableVertexAttribArray(aCol);
  gl.bindBuffer(gl.ARRAY_BUFFER, posBuf); gl.vertexAttribPointer(aPos, 3, gl.FLOAT, false, 0, 0);
  gl.bindBuffer(gl.ARRAY_BUFFER, colBuf); gl.vertexAttribPointer(aCol, 3, gl.FLOAT, false, 0, 0);
  gl.drawArrays(gl.TRIANGLES, 0, view.tris.length / 3);
  gl.disable(gl.DEPTH_TEST);
  gl.bindBuffer(gl.ARRAY_BUFFER, linePosBuf); gl.vertexAttribPointer(aPos, 3, gl.FLOAT, false, 0, 0);
  gl.bindBuffer(gl.ARRAY_BUFFER, lineColBuf); gl.vertexAttribPointer(aCol, 3, gl.FLOAT, false, 0, 0);
  gl.drawArrays(gl.LINES, 0, view.lines.length / 3);
  gl.enable(gl.DEPTH_TEST);
}

function modelDiagonal() {
  const b = view.bbox;
  return b ? (Math.hypot(b[3] - b[0], b[4] - b[1], b[5] - b[2]) || 1) : 1;
}

function fitView() {
  const b = view.bbox;
  if (!b) return;
  camera.target = [(b[0] + b[3]) / 2, (b[1] + b[4]) / 2, (b[2] + b[5]) / 2];
  camera.pan = [0, 0, 0];
  camera.dist = modelDiagonal() * 1.5;
  draw();
}

/* Keep the camera within a sensible range of the model so a fast wheel or
   trackpad gesture cannot bury the camera inside the geometry or fling it so
   far away that the model disappears. */
function clampDistance(d) {
  const diag = modelDiagonal();
  return Math.min(Math.max(d, diag * 0.05), diag * 50);
}

function resize() {
  const dpr = Math.min(devicePixelRatio || 1, 2);
  const w = Math.floor(canvas.clientWidth * dpr), h = Math.floor(canvas.clientHeight * dpr);
  if (canvas.width !== w || canvas.height !== h) {
    canvas.width = w; canvas.height = h; gl.viewport(0, 0, w, h);
  }
  draw();
}

/* --- picking --- */
function pointInTri(px, py, a, b, c) {
  const v0 = [c[0] - a[0], c[1] - a[1]], v1 = [b[0] - a[0], b[1] - a[1]], v2 = [px - a[0], py - a[1]];
  const d00 = v0[0] * v0[0] + v0[1] * v0[1], d01 = v0[0] * v1[0] + v0[1] * v1[1];
  const d02 = v0[0] * v2[0] + v0[1] * v2[1], d11 = v1[0] * v1[0] + v1[1] * v1[1];
  const d12 = v1[0] * v2[0] + v1[1] * v2[1];
  const inv = 1 / (d00 * d11 - d01 * d01 || 1e-12);
  const u = (d11 * d02 - d01 * d12) * inv, v = (d00 * d12 - d01 * d02) * inv;
  return u >= 0 && v >= 0 && u + v <= 1;
}
function segDist(px, py, a, b) {
  const vx = b[0] - a[0], vy = b[1] - a[1], wx = px - a[0], wy = py - a[1];
  const t = Math.max(0, Math.min(1, (vx * wx + vy * wy) / ((vx * vx + vy * vy) || 1)));
  return { d: Math.hypot(px - (a[0] + t * vx), py - (a[1] + t * vy)), z: a[2] + t * (b[2] - a[2]) };
}
function pick(x, y) {
  if (!view.tris.length) return null;
  const M = mvp();
  if (view.mode === 'edge') {
    let best = null;
    for (let i = 0; i < view.lines.length; i += 6) {
      const a = project(view.lines.slice(i, i + 3), M), b = project(view.lines.slice(i + 3, i + 6), M);
      const sd = segDist(x, y, a, b);
      if (sd.d < 8 && (!best || sd.z < best.z)) best = { id: view.lineEdgeIds[i / 6], z: sd.z };
    }
    return best ? best.id : null;
  }
  let best = null;
  for (let i = 0; i < view.tris.length; i += 9) {
    const a = project(view.tris.slice(i, i + 3), M);
    const b = project(view.tris.slice(i + 3, i + 6), M);
    const c = project(view.tris.slice(i + 6, i + 9), M);
    if (pointInTri(x, y, a, b, c)) {
      const z = (a[2] + b[2] + c[2]) / 3;
      if (!best || z < best.z) best = { fid: view.triFaceIds[i / 9], z };
    }
  }
  if (!best) return null;
  return view.mode === 'face' ? best.fid : (view.faceBody.get(best.fid) || null);
}

/* --- form wiring --- */
function syncForm() {
  $('#setIds').value = [...view.selection].join(',');
  $('#setKind').value = view.mode;
  $('#selInfo').textContent = view.selection.size
    ? `${view.selection.size} ${view.mode}(s) selected.`
    : 'Click geometry in the viewer to select.';
  rebuildColors();
}

document.querySelectorAll('.mode').forEach(b => b.onclick = () => {
  document.querySelectorAll('.mode').forEach(x => x.classList.remove('active'));
  b.classList.add('active');
  view.mode = b.dataset.mode;
  view.selection.clear();
  syncForm();
});
const clearBtn = $('#clearSel');
if (clearBtn) clearBtn.onclick = () => { view.selection.clear(); syncForm(); };

document.querySelectorAll('.pickSet').forEach(b => b.onclick = () => {
  view.mode = b.dataset.kind;
  document.querySelectorAll('.mode').forEach(x => x.classList.toggle('active', x.dataset.mode === view.mode));
  view.selection = new Set((b.dataset.ids || '').split(',').filter(Boolean));
  $('#setName').value = b.dataset.name;
  syncForm();
});

canvas.addEventListener('pointerdown', e => {
  if (e.button !== 0) return;
  // Pointer capture keeps the drag bound to this element, so a release outside
  // the window still reaches us. Without it a button released off-window left
  // the drag active and the model followed the mouse with no button held.
  try { canvas.setPointerCapture(e.pointerId); } catch (_) {}
  drag = { id: e.pointerId, x: e.clientX, y: e.clientY, shift: e.shiftKey, moved: false };
});
function endDrag(e) {
  if (!drag) return;
  const moved = drag.moved;
  try { canvas.releasePointerCapture(drag.id); } catch (_) {}
  drag = null;
  if (moved || e.type !== 'pointerup') return;
  const r = canvas.getBoundingClientRect();
  const x = e.clientX - r.left, y = e.clientY - r.top;
  if (x < 0 || y < 0 || x > r.width || y > r.height) return;
  const id = pick(x, y);
  if (!id) return;
  view.selection.has(id) ? view.selection.delete(id) : view.selection.add(id);
  syncForm();
}
canvas.addEventListener('pointerup', endDrag);
canvas.addEventListener('pointercancel', endDrag);
window.addEventListener('blur', () => { drag = null; });
window.addEventListener('pointermove', e => {
  if (!drag) return;
  // Belt and braces: if no button is held any more, the release was missed.
  if (e.buttons === 0) { drag = null; return; }
  const dx = e.clientX - drag.x, dy = e.clientY - drag.y;
  if (Math.hypot(dx, dy) > 2) drag.moved = true;
  drag.x = e.clientX; drag.y = e.clientY;
  if (drag.shift) {
    const s = camera.dist / 500;
    camera.pan[0] -= dx * s * Math.sin(camera.yaw);
    camera.pan[1] += dx * s * Math.cos(camera.yaw);
    camera.pan[2] += dy * s;
  } else {
    camera.yaw -= dx * 0.008;
    camera.pitch = Math.max(-1.55, Math.min(1.55, camera.pitch + dy * 0.008));
  }
  draw();
});
function pageCanScroll() {
  return document.documentElement.scrollHeight > window.innerHeight + 1;
}

canvas.addEventListener('wheel', e => {
  // On the desktop layout the page cannot scroll, so a plain wheel zooms. On the
  // narrow stacked layout the page does scroll, and swallowing the wheel there
  // would zoom the model while the user was only trying to scroll the page --
  // so the gesture only zooms with Ctrl held.
  if (pageCanScroll() && !e.ctrlKey && !e.metaKey) return;
  e.preventDefault();
  // deltaY is reported in pixels, lines or pages depending on the device.
  // Normalising first keeps one wheel notch feeling the same everywhere; v0.6
  // scaled the raw value and also multiplied the distance by 0.2 every event.
  const unit = e.deltaMode === 1 ? 16 : e.deltaMode === 2 ? 400 : 1;
  const steps = Math.max(-4, Math.min(4, (e.deltaY * unit) / 120));
  camera.dist = clampDistance(camera.dist * Math.exp(steps * 0.12));
  draw();
}, { passive: false });

// Tell the user which rule is in force right now.
function updateHint() {
  const hint = document.querySelector('.hint');
  if (!hint) return;
  hint.textContent = 'Drag: rotate \u00b7 ' + (pageCanScroll() ? 'Ctrl+Wheel' : 'Wheel') +
    ': zoom \u00b7 Shift+drag: pan \u00b7 Click: select \u00b7 Fit resets the view';
}
window.addEventListener('resize', updateHint);

const views = {
  fit: () => fitView(),
  front: () => { camera.yaw = -Math.PI / 2; camera.pitch = 0; draw(); },
  right: () => { camera.yaw = 0; camera.pitch = 0; draw(); },
  top: () => { camera.pitch = Math.PI / 2 - 0.001; draw(); }
};
document.querySelectorAll('[data-view]').forEach(b => b.onclick = () => views[b.dataset.view]());

initGL();
new ResizeObserver(resize).observe(canvas);
resize();
updateHint();

fetch('/api/mesh')
  .then(r => r.ok ? r.json() : null)
  .then(payload => { if (payload && payload.mesh) { buildGeometry(payload); resize(); } })
  .catch(() => {});
