/* Parity harness: read a pattern + sweep request as JSON on stdin, run one
 * lean via the browser engine, print the aggregate arrays as JSON on stdout.
 * tests/test_browser_engine.py compares this to the verified Python sweep. */
const E = require("./engine.js");

let raw = "";
process.stdin.on("data", (d) => (raw += d));
process.stdin.on("end", () => {
  const req = JSON.parse(raw);
  const p = req.pattern;
  // Rehydrate the crown from the Python to_dict layout.
  const c = p.crown_radius_profile;
  const pattern = {
    tyre_circumference: p.tyre_circumference,
    tread_width: p.tread_width,
    pitches: p.pitches,
    blocks: p.blocks,
    crown: { y: c.y, phi: c.phi, z: c.z, y_proj: c.y_proj, r: c.r, measured: !!c.measured },
    name: p.name, source: p.source, meta: p.meta || {},
  };
  const grid = E.makeGrid(pattern, req.nx, req.ny);
  const pack = E.rasterise(pattern, grid, req.stiffParams, !!req.curvatureCorrection);
  const r = E.sweepLean(pattern, pack, req.gamma, req.spec, req.cpParams, null, req.discreteSamples || 360);
  const stride = req.stride || 1;
  const sub = (a) => { const o = []; for (let i = 0; i < a.length; i += stride) o.push(a[i]); return o; };
  process.stdout.write(JSON.stringify({
    contact_area: sub(r.contact_area), kx: sub(r.kx), ky: sub(r.ky), kz: sub(r.kz),
    block_count: sub(r.block_count), patch_area: r.patch_area, patch_perimeter: r.patch_perimeter,
  }));
});
