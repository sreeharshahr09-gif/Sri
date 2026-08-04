/* Web Worker glue.  In the built page this text is appended after engine.js
 * inside a Blob, so `self.TreadEngine` is defined by the time this runs.  Kept
 * as its own file for readability and so the build step can inline it. */
(function () {
  "use strict";
  var E = self.TreadEngine;

  self.onmessage = function (ev) {
    var m = ev.data;
    if (m.cmd !== "sweep") return;
    try {
      var t0 = Date.now();
      var pattern = m.pattern;
      // crown arrays survive structured clone as plain arrays; the engine reads
      // them by index so no rehydration is needed.
      var grid = E.makeGrid(pattern, m.gridNx, m.gridNy);
      var pack = E.rasterise(pattern, grid, m.stiffParams, m.curvatureCorrection);
      var tRaster = Date.now() - t0;

      var cache = new E.MapFFTCache(pack);
      var leans = m.leans;
      var results = [];
      for (var i = 0; i < leans.length; i++) {
        var r = E.sweepLean(pattern, pack, leans[i], m.spec, m.cpParams, cache, m.discreteSamples);
        results.push(packResult(r, m.stride || 1));
        self.postMessage({ type: "progress", done: i + 1, total: leans.length });
      }
      // per-block stiffness summary for the diagnostics tab
      var stiffSummary = summariseStiffness(pattern, pack);
      self.postMessage({
        type: "done",
        results: results,
        stiffness: stiffSummary,
        timing: { raster: tRaster, total: Date.now() - t0 },
        grid: { nx: grid.nx, ny: grid.ny, dx: grid.dx, dy: grid.dy },
      });
    } catch (err) {
      self.postMessage({ type: "error", message: String(err && err.message ? err.message : err) });
    }
  };

  // Downsample the dense per-angle arrays for transport + plotting.
  function packResult(r, stride) {
    function sub(arr) {
      if (stride <= 1) return Array.prototype.slice.call(arr);
      var out = [];
      for (var i = 0; i < arr.length; i += stride) out.push(arr[i]);
      return out;
    }
    var zone = {};
    for (var z in r.zone_area) zone[z] = sub(r.zone_area[z]);
    return {
      gamma_deg: r.gamma_deg,
      theta_deg: sub(r.theta_deg),
      contact_area: sub(r.contact_area),
      land_ratio: sub(r.land_ratio),
      kx: sub(r.kx), ky: sub(r.ky), kz: sub(r.kz),
      block_count: sub(r.block_count),
      centroid_y: sub(r.centroid_y),
      zone_area: zone,
      block_count_discrete: Array.prototype.slice.call(r.block_count_discrete),
      theta_discrete: Array.prototype.slice.call(r.theta_discrete),
      patch: { source: r.patch.source, provenance: r.patch.provenance, y_center: r.patch.y_center,
        a: r.patch.a, b: r.patch.b, clipped: r.patch.clipped, outline: r.patch.outline,
        normal_load: r.patch.normal_load, peak_pressure: r.patch.peak_pressure },
      patch_area: r.patch_area, patch_perimeter: r.patch_perimeter, patch_load: r.patch_load,
      shape: r.shape,
    };
  }

  function summariseStiffness(pattern, pack) {
    var byZone = { center: [], intermediate: [], shoulder: [] };
    var all = [];
    for (var i = 0; i < pattern.blocks.length; i++) {
      var b = pattern.blocks[i], s = pack.stiffness[i];
      var rec = { id: b.id, zone: b.zone, kx: s.kx, ky: s.ky, kz: s.kz, area: s.area, shapeFactor: s.shapeFactor, slenderness: s.slenderness };
      all.push(rec);
      if (byZone[b.zone]) byZone[b.zone].push(rec);
    }
    function agg(list, key) {
      if (!list.length) return { mean: 0, min: 0, max: 0, cov: 0, n: 0 };
      var mean = 0, mn = Infinity, mx = -Infinity;
      for (var i = 0; i < list.length; i++) { var v = list[i][key]; mean += v; if (v < mn) mn = v; if (v > mx) mx = v; }
      mean /= list.length;
      var vv = 0; for (var j = 0; j < list.length; j++) { var d = list[j][key] - mean; vv += d * d; }
      var std = Math.sqrt(vv / list.length);
      return { mean: mean, min: mn, max: mx, cov: mean ? std / Math.abs(mean) : 0, n: list.length };
    }
    var out = { zones: {}, overall: {} };
    var keys = ["kx", "ky", "kz", "area", "slenderness"];
    for (var z in byZone) { out.zones[z] = {}; for (var k = 0; k < keys.length; k++) out.zones[z][keys[k]] = agg(byZone[z], keys[k]); }
    for (var kk = 0; kk < keys.length; kk++) out.overall[keys[kk]] = agg(all, keys[kk]);
    out.n_blocks = all.length;
    return out;
  }
})();
