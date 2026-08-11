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
      var wholePattern = m.pattern;
      // Wear shortens every block and decides which tie bars have reached the
      // road. Do it once, here, so the rest of the pipeline sees one block list
      // and never has to know a tie bar from a block.
      E.validateWear(m.wear, wholePattern);
      var split = E.effectiveBlocks(wholePattern, m.wear, { separate: true });
      var pattern = Object.assign({}, wholePattern, { blocks: split.blocks.concat(split.tiebars) });
      // crown arrays survive structured clone as plain arrays; the engine reads
      // them by index so no rehydration is needed.
      var grid = E.makeGrid(pattern, m.gridNx, m.gridNy);
      var pack = E.rasterise(pattern, grid, m.stiffParams, m.curvatureCorrection, m.zoneFracs);
      var tRaster = Date.now() - t0;

      var cache = new E.MapFFTCache(pack);
      var notes = [];

      // Past the crown's steepest tangent there is no contact point: the
      // position saturates at the tread edge and every derived number is an
      // extrapolation. Drop those leans rather than report them as computed.
      var maxLean = E.maxSupportedLean(pattern.crown);
      var leans = [], dropped = [];
      for (var li = 0; li < m.leans.length; li++) {
        if (m.leans[li] <= maxLean + 1e-9) leans.push(m.leans[li]);
        else dropped.push(m.leans[li]);
      }
      if (dropped.length) {
        notes.push("lean " + dropped.join("°, ") + "° exceeds the crown's maximum reachable lean of " +
          maxLean.toFixed(1) + "° and was skipped — past that angle the tyre is riding the tread edge. " +
          "Supply a wider-arc crown (smaller shoulder radius) if the real tyre reaches these angles.");
      }
      if (!leans.length) throw new Error(
        "no requested lean angle is reachable on this crown (maximum " + maxLean.toFixed(1) + "°)");

      if (!m.stiffParams || m.stiffParams.modulus_mode !== "direct") {
        var shoreNote = E.shoreRangeWarning(m.stiffParams.shore_a);
        if (shoreNote) notes.push(shoreNote);
      }
      var cp = E.compoundProperties(m.stiffParams);
      notes.push("compound: E = " + cp.E.toFixed(3) + " N/mm², G = " + cp.G.toFixed(3) +
        " N/mm², Gent k = " + cp.k.toFixed(3) + " (" + cp.source + ").");

      var nTie = (wholePattern.tiebars || []).length;
      if (nTie) {
        var engaged = split.tiebars.length;
        notes.push(nTie + " tie bar(s) in this drawing; " + engaged + " in contact at " +
          (m.wear || 0).toFixed(1) + " mm wear. A bar of height h reaches the road only once the " +
          "tread has worn NSD - h, so at zero wear they carry nothing.");
      }
      if (m.wear > 0) {
        var lost = (wholePattern.blocks || []).length - split.blocks.length;
        notes.push("tread worn " + (+m.wear).toFixed(2) + " mm: every block's bending length is NSD - wear, " +
          "so the tread is stiffer than new" + (lost ? " (" + lost + " block(s) worn away entirely)" : "") + ".");
      }

      var results = [];
      for (var i = 0; i < leans.length; i++) {
        var r = E.sweepLean(pattern, pack, leans[i], m.spec, m.cpParams, cache, m.discreteSamples, m.bandEdges);
        results.push(packResult(r, m.stride || 1));
        self.postMessage({ type: "progress", done: i + 1, total: leans.length });
      }
      // per-block stiffness summary for the diagnostics tab
      var stiffSummary = summariseStiffness(pattern, pack);
      // Any patch clipped by the tread edge is geometry the model did not
      // intend -- surface it rather than let it read as a real narrowing.
      var clippedAt = [];
      for (var ci = 0; ci < results.length; ci++)
        if (results[ci].patch.clipped) clippedAt.push(results[ci].gamma_deg);
      if (clippedAt.length)
        notes.push("the contact patch is clipped by the tread edge at " + clippedAt.join("°, ") +
          "° — the patch is wider than the tread has room for there, so its area is truncated, not physical.");

      self.postMessage({
        type: "done",
        results: results,
        stiffness: stiffSummary,
        notes: notes,
        maxLean: maxLean,
        timing: { raster: tRaster, total: Date.now() - t0 },
        grid: { nx: grid.nx, ny: grid.ny, dx: grid.dx, dy: grid.dy },
        bandEdges: m.bandEdges || null,
        compound: cp,
        // Which bars this run actually had in contact. The page draws the
        // pattern strip from this, not from the live inputs, so the strip can
        // never show a bar as land that the curves above it did not include.
        wear: { mm: m.wear || 0, n_blocks: split.blocks.length,
                n_tiebars_total: nTie, n_tiebars_engaged: split.tiebars.length,
                engaged_ids: split.tiebars.map(function (t) { return t.id; }) },
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
      bands: r.bands ? r.bands.map(function (b) {
        return { index: b.index, y_lo: b.y_lo, y_hi: b.y_hi, width_mm: b.width_mm,
                 contact_area: sub(b.contact_area), kx: sub(b.kx), ky: sub(b.ky),
                 kz: sub(b.kz), block_count: sub(b.block_count) };
      }) : null,
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
