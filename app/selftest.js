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

// --- 4. physics: hand-calc reference ----------------------------------
check("Kx/Ky/Kz match the closed-form hand calculation", () => {
  // rect 20 x 10 mm, NSD 8 mm, Shore 60 -> E=6.89, nu=0.49, k=0.64
  const L = 20, W = 8 && 10, H = 8, E_ = 6.89, NU = 0.49, KG = 0.64;
  const G_ = E_ / (2 * (1 + NU));
  const area = L * W, perim = 2 * (L + W);
  const S = area / (H * perim);
  const eu = E_ * (1 + 2 * KG * S * S);
  const eEff = eu / (1 + eu / 1100);
  const kzHand = (eEff * area) / H;
  const Iyy = (W * L ** 3) / 12, Ixx = (L * W ** 3) / 12;
  const cB = H ** 3 / (12 * E_), cS = H / (G_ * area);
  const kxHand = 1 / (cB / Iyy + cS), kyHand = 1 / (cB / Ixx + cS);

  const got = E.blockStiffness(
    { polygon: [[0, 0], [L, 0], [L, W], [0, W]], height: H, draft_angle: 0, sipes: [], n_lateral_sipes: 0 },
    { shore_a: 60, poisson: NU, mode: "parallel", bulk_modulus: 1100, n_slices: 40 });
  const rel = (a, b) => Math.abs(a - b) / Math.abs(b);
  assert(rel(got.kz, kzHand) < 1e-9, `kz ${got.kz} vs hand ${kzHand}`);
  assert(rel(got.kx, kxHand) < 3e-4, `kx ${got.kx} vs hand ${kxHand}`);
  assert(rel(got.ky, kyHand) < 3e-4, `ky ${got.ky} vs hand ${kyHand}`);
  assert(rel(got.kz, 208.928315) < 1e-6, `kz vs worked number: ${got.kz}`);
});

// --- 5. the FFT must refuse a length it cannot transform ---------------
check("non-power-of-two FFT length is refused, not silently NaN", () => {
  let threw = false;
  try { E.fftRadix2(new Float64Array(100), new Float64Array(100), false); }
  catch (e) { threw = true; assert(/power of two/.test(e.message), "message must name the cause: " + e.message); }
  assert(threw, "fftRadix2(100) must throw -- it used to return NaN and poison the whole sweep");
  let threw2 = false;
  try { E.makeGrid({ tyre_circumference: 1000, tread_width: 100 }, 500, 32); }
  catch (e) { threw2 = true; assert(/power of two/.test(e.message), e.message); }
  assert(threw2, "makeGrid must reject a non-power-of-two nx");
  // and the valid sizes the UI offers must all still work
  for (const nx of [1024, 2048, 4096]) E.makeGrid({ tyre_circumference: 1000, tread_width: 100 }, nx, 32);
});

// --- 6. lean scaling + validation parity with Python -------------------
check("lean scaling narrows the patch and matches the Winkler trend", () => {
  const crown = E.crownDualRadius(159);
  const params = { vertical_load: 1500, wheel_radius: 320, load_rises_with_lean: true };
  const sf = E.leanScaleFactors(crown, 40, params, 0);
  assert(sf[1] > 0.7 && sf[1] < 0.85, `width scale at 40deg should be ~0.81, got ${sf[1]}`);
  assert(sf[0] > 1.0, `length scale should exceed 1, got ${sf[0]}`);
  const base = { shape: "rounded", length: 90, width: 50, corner_radius: 12 };
  const up = E.shapePatch(Object.assign({}, base, { gamma_deg: 0 }), crown, 159, params);
  const lean = E.shapePatch(Object.assign({}, base, { gamma_deg: 40 }), crown, 159, params);
  assert(E.patchWidth(lean) < 0.75 * E.patchWidth(up), "leaned patch must be narrower");
  const held = E.shapePatch(Object.assign({}, base, { gamma_deg: 40, scale_with_lean: false }), crown, 159, params);
  assert(Math.abs(E.patchLength(held) - 90) < 1e-6, "opting out must hold the stated length");
});

check("invalid patch geometry is rejected with a specific message", () => {
  const crown = E.crownDualRadius(159);
  const params = { vertical_load: 1500, wheel_radius: 320, load_rises_with_lean: true };
  const cases = [
    [{ shape: "rounded", length: 0, width: 50 }, /length must be positive/],
    [{ shape: "rounded", length: 90, width: -50 }, /width must be positive/],
    [{ shape: "rounded", length: NaN, width: 50 }, /finite number/],
    [{ shape: "hexagon", length: 90, width: 50 }, /unknown contact patch shape/],
    [{ shape: "trapezoid", length: 90, width: 50, taper: 1 }, /taper must lie strictly/],
  ];
  for (const [spec, re] of cases) {
    let threw = false;
    try { E.shapePatch(spec, crown, 159, params); }
    catch (e) { threw = true; assert(re.test(e.message), `wrong message for ${JSON.stringify(spec)}: ${e.message}`); }
    assert(threw, `must reject ${JSON.stringify(spec)}`);
  }
});

check("shore hardness outside the Gent table is flagged", () => {
  assert(E.shoreRangeWarning(60) === null, "60A is inside the table");
  const w = E.shoreRangeWarning(95);
  assert(w && /70/.test(w) && /outside the validated/.test(w), "95A must warn and name the substitute: " + w);
});

check("max supported lean is the steepest tread tangent", () => {
  const crown = E.crownDualRadius(159);
  const ml = E.maxSupportedLean(crown);
  assert(ml > 40 && ml < 55, `dual-radius 159mm crown should reach ~45 deg, got ${ml}`);
  // beyond it the contact point pins to the tread edge
  assert(Math.abs(E.crownContactLateral(crown, 89) - 79.5) < 1e-6, "must saturate at the tread edge");
});

// --- 7. order spectrum definition --------------------------------------
check("order spectrum is normalised by the mean, like the Python report", () => {
  // A +-4% modulation at order 12 on a mean of 50 must read as 0.04 at order 12,
  // NOT as the absolute amplitude 2.0. The two implementations share one guide.
  const n = 2048, mean = 50, amp = 2.0, order = 12;
  const sig = new Float64Array(n);
  for (let i = 0; i < n; i++) sig[i] = mean + amp * Math.sin((2 * Math.PI * order * i) / n);
  const sp = E.orderSpectrum(sig, 40);
  const k = sp.orders.indexOf(order);
  assert(Math.abs(sp.amplitude[k] - amp / mean) < 1e-9,
    `order ${order} should be ${amp / mean} (fraction of mean), got ${sp.amplitude[k]}`);
  // every other order must be essentially empty
  for (let i = 0; i < sp.orders.length; i++)
    if (sp.orders[i] !== order) assert(sp.amplitude[i] < 1e-9, `spurious energy at order ${sp.orders[i]}`);
});

check("order spectrum of a non-power-of-two signal stays clean", () => {
  const n = 360, mean = 10, amp = 1.0, order = 12;
  const sig = new Float64Array(n);
  for (let i = 0; i < n; i++) sig[i] = mean + amp * Math.sin((2 * Math.PI * order * i) / n);
  const sp = E.orderSpectrum(sig, 30);
  const pairs = sp.orders.map((o, i) => [o, sp.amplitude[i]]).sort((a, b) => b[1] - a[1]);
  assert(pairs[0][0] === order, `peak should be order ${order}, got ${pairs[0][0]}`);
  // linear interpolation must keep the leakage far below the peak
  assert(pairs[1][1] < 0.02 * pairs[0][1],
    `resampling leakage too high: ${pairs[1][1]} vs peak ${pairs[0][1]}`);
});

// --- 8. sipe models ----------------------------------------------------
check("continuous sipe model: zero-depth no-op, monotonic, never stiffens", () => {
  const poly = [[0, 0], [20, 0], [20, 10], [0, 10]], NSD = 8, E_ = 6.89, NU = 0.49;
  const sipe = (d, w) => [{ p1: [10, -5], p2: [10, 15], depth: d, width: w == null ? 1e-6 : w }];
  for (const mode of ["parallel", "free"]) {
    const plain = E.effectiveK(poly, NSD, E_, NU, 0, mode, [], 40).Kx;
    const zero = E.effectiveK(poly, NSD, E_, NU, 0, mode, sipe(0, 0.5), 40, "continuous").Kx;
    assert(Math.abs(zero - plain) < 1e-9, `${mode}: zero-depth sipe must be a no-op (${zero} vs ${plain})`);
    let prev = Infinity;
    for (const d of [0.5, 1, 2, 4, 6, 8]) {
      const k = E.effectiveK(poly, NSD, E_, NU, 0, mode, sipe(d, 0.5), 40, "continuous").Kx;
      assert(k <= plain + 1e-9, `${mode}: sipe depth ${d} stiffened the block (${k} > ${plain})`);
      assert(k <= prev + 1e-9, `${mode}: not monotonic at depth ${d} (${k} > ${prev})`);
      prev = k;
    }
  }
  // the layered default must still show the reference artifact
  const plainP = E.effectiveK(poly, NSD, E_, NU, 0, "parallel", [], 40).Kx;
  const shallow = E.effectiveK(poly, NSD, E_, NU, 0, "parallel", sipe(2, 0.5), 40, "layered").Kx;
  assert(shallow > plainP, "layered model must still reproduce v6.4 bit-for-bit, artifact included");
});

console.log(`\n${passed} checks passed`);
