/* Node self-tests for the browser engine.
 * Run: node app/selftest.js
 *
 * Checks:
 *   1. stiffness matches the verbatim v6.4 reference (verify/tool_v64_reference.js)
 *   2. the FFT correlation reproduces a brute-force masked sum
 *   3. the Tramplr DXF imports to the known geometry (168 blocks, land ~0.69)
 */
const assert = require("assert");
const fs = require("fs");
const path = require("path");
const E = require("./engine.js");
const REF = require("../verify/tool_v64_reference.js");

let passed = 0;
function check(name, fn) {
  try { fn(); console.log("  ok   " + name); passed++; }
  catch (e) { console.log("  FAIL " + name + "\n       " + e.message); process.exitCode = 1; }
}

console.log("engine self-tests");

// --- 1. stiffness vs reference ----------------------------------------
function randomPoly(seed) {
  // deterministic pseudo-random convex-ish quad
  let s = seed;
  const rnd = () => { s = (s * 1103515245 + 12345) & 0x7fffffff; return s / 0x7fffffff; };
  const w = 4 + rnd() * 20, h = 4 + rnd() * 20;
  return [[0, 0], [w, 0.5 * rnd() * 2], [w + rnd() * 2, h], [rnd() * 2, h]];
}

check("blockStiffness matches reference effectiveK/computeKz", () => {
  const params = { shore_a: 60, poisson: 0.49, mode: "parallel", bulk_modulus: 1100, n_slices: 40 };
  for (let seed = 1; seed <= 30; seed++) {
    const poly = randomPoly(seed);
    const nsd = 5 + (seed % 7);
    const draft = (seed % 5) - 2; // -2..2 deg
    const block = { polygon: poly, height: nsd, draft_angle: draft, n_lateral_sipes: 0, sipes: [], sipe_depth_fraction: 0.6, sipe_width: 0.5, shore_a: null };
    const mine = E.blockStiffness(block, params);
    const E_ = REF.shoreE(60), nu = 0.49;
    const ref = REF.effectiveK({ vertices: poly, nsd: nsd, E: E_, nu: nu, draft: draft, mode: "parallel", explicitSipes: [] });
    const refKz = REF.computeKz({ vertices: poly, nsd: nsd, E: E_, perfShore: 60, explicitSipes: [] });
    const rel = (a, b) => Math.abs(a - b) / (Math.abs(b) + 1e-9);
    assert(rel(mine.kx, ref.Kx) < 1e-9, `seed ${seed} kx ${mine.kx} vs ${ref.Kx}`);
    assert(rel(mine.ky, ref.Ky) < 1e-9, `seed ${seed} ky ${mine.ky} vs ${ref.Ky}`);
    assert(rel(mine.kz, refKz.Kz) < 1e-9, `seed ${seed} kz ${mine.kz} vs ${refKz.Kz}`);
  }
});

check("blockStiffness with sipes matches reference", () => {
  const params = { shore_a: 60, poisson: 0.49, mode: "parallel", bulk_modulus: 1100, n_slices: 40 };
  const poly = [[0, 0], [20, 0], [20, 12], [0, 12]];
  const nsd = 8;
  const sipes = [{ p1: [7, -3], p2: [7, 15], depth: 5, width: 0.5 }, { p1: [13, -3], p2: [13, 15], depth: 5, width: 0.5 }];
  const block = { polygon: poly, height: nsd, draft_angle: 0, sipes: sipes, n_lateral_sipes: 0, sipe_depth_fraction: 0.6, sipe_width: 0.5, shore_a: null };
  const mine = E.blockStiffness(block, params);
  const E_ = REF.shoreE(60);
  const ref = REF.effectiveK({ vertices: poly, nsd: nsd, E: E_, nu: 0.49, draft: 0, mode: "parallel", explicitSipes: sipes });
  const refKz = REF.computeKz({ vertices: poly, nsd: nsd, E: E_, perfShore: 60, explicitSipes: sipes });
  const rel = (a, b) => Math.abs(a - b) / (Math.abs(b) + 1e-9);
  assert(rel(mine.kx, ref.Kx) < 1e-9, `kx ${mine.kx} vs ${ref.Kx}`);
  assert(rel(mine.ky, ref.Ky) < 1e-9, `ky ${mine.ky} vs ${ref.Ky}`);
  assert(rel(mine.kz, refKz.Kz) < 1e-9, `kz ${mine.kz} vs ${refKz.Kz}`);
});

// --- 2. FFT correlation vs brute force --------------------------------
check("FFT correlation reproduces brute-force masked sum", () => {
  const nx = 256, ny = 8;
  const grid = { nx, ny, dx: 1, dy: 1, circumference: nx, tread_width: ny };
  const N = nx * ny;
  const map = new Float32Array(N);
  let s = 7;
  const rnd = () => { s = (s * 1103515245 + 12345) & 0x7fffffff; return s / 0x7fffffff; };
  for (let i = 0; i < N; i++) map[i] = rnd();
  // kernel: a small blob centred on column 0, wrapping
  const kernel = new Float32Array(N);
  for (let r = 2; r < 6; r++) for (let dc = -3; dc <= 3; dc++) { const c = ((dc % nx) + nx) % nx; kernel[r * nx + c] = 1; }
  const cache = new E.MapFFTCache({ grid });
  const spec = cache.get("m", map);
  const ker = (function () {
    const half = (nx >> 1) + 1, re = new Float64Array(ny * half), im = new Float64Array(ny * half), row = new Float64Array(nx);
    for (let r = 0; r < ny; r++) { const base = r * nx; for (let c = 0; c < nx; c++) row[c] = kernel[base + c]; const f = E.rfftRow(row, nx); re.set(f.re, r * half); im.set(f.im, r * half); }
    return { re, im };
  })();
  // engine correlate
  const half = (nx >> 1) + 1;
  const accRe = new Float64Array(half), accIm = new Float64Array(half);
  for (let r = 0; r < ny; r++) for (let f = 0; f < half; f++) {
    const b = r * half; const mr = spec.re[b + f], mi = spec.im[b + f], kr = ker.re[b + f], ki = ker.im[b + f];
    accRe[f] += mr * kr + mi * ki; accIm[f] += mi * kr - mr * ki;
  }
  const corr = E.irfft(accRe, accIm, nx);
  // brute force: c[j] = sum_{x,y} map[y,x]*kernel[y,x-j]
  for (let j = 0; j < nx; j += 17) {
    let acc = 0;
    for (let y = 0; y < ny; y++) for (let x = 0; x < nx; x++) { const xk = (((x - j) % nx) + nx) % nx; acc += map[y * nx + x] * kernel[y * nx + xk]; }
    assert(Math.abs(acc - corr[j]) < 1e-6, `j=${j} brute ${acc} vs fft ${corr[j]}`);
  }
});

// --- 3. Tramplr DXF import --------------------------------------------
check("Tramplr DXF imports to known geometry", () => {
  const text = fs.readFileSync(path.join(__dirname, "..", "data", "130_80R17_Tramplr_XR_tread_plan.dxf"), "utf8");
  const { pattern, report } = E.loadPattern(text, { height: 8.0, draft_angle: 3.0 }, {});
  console.log(`       blocks=${report.n_blocks} wrapped=${report.n_wrapped} land=${report.land_ratio.toFixed(3)} ` +
    `C=${report.circumference.toFixed(1)} W=${report.tread_width.toFixed(1)} pitches=${pattern.pitches.length} repeat=${(pattern.meta.geometric_repeat_mm||0).toFixed(1)}`);
  assert(report.n_blocks >= 150 && report.n_blocks <= 180, `expected ~168 blocks, got ${report.n_blocks}`);
  assert(report.land_ratio > 0.6 && report.land_ratio < 0.75, `expected land ~0.69, got ${report.land_ratio}`);
  assert(Math.abs(report.circumference - 2193.4) < 5, `circumference ${report.circumference}`);
  assert(Math.abs(report.tread_width - 159.0) < 5, `tread width ${report.tread_width}`);
});

console.log(`\n${passed} checks passed`);
