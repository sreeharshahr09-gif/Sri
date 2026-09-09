/* STEP (ISO 10303-21) reader: tread surfaces and what they measure.
 * =================================================================
 *
 * A tread STEP model is a boundary representation -- a set of trimmed faces,
 * each a patch of some surface cut out by loops of curves. The one question
 * this module answers is the one an engineer actually asks of it:
 *
 *     of the tread surface I point at, how much is rubber and how much is
 *     groove?
 *
 * That needs the AREA of each face, which needs the face's trimming loops
 * walked and measured in a frame the surface can be flattened into. So this is
 * deliberately NOT a general B-rep kernel. It reads the entity graph, walks
 * faces to their loops to their curves, and measures faces on the two surface
 * types a tread top actually is:
 *
 *     PLANE                 a rolled-out or sectioned model
 *     CYLINDRICAL_SURFACE   a tread band on the tyre, which develops exactly
 *
 * Anything else -- a torus, a general B-spline surface -- is REPORTED as
 * unmeasurable rather than dropped, because a face silently missing from a land
 * ratio is a wrong answer that looks like a right one.
 *
 * Where a closed form exists it is used rather than sampled. A straight edge
 * and a circular arc both have exact contributions to the contour integral for
 * area (Green's theorem), so a circular groove opening on a planar face is
 * measured EXACTLY, not as a fine polygon. Only B-splines are sampled.
 */
(function (root, factory) {
  if (typeof module === "object" && module.exports) module.exports = factory();
  else root.TreadStep = factory();
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  // =====================================================================
  // 1. Part 21 parsing
  // =====================================================================
  //
  // The physical file format is simple enough to read directly, and doing so
  // beats a regex: these files run to tens of megabytes and the string
  // literals inside them contain every character the grammar uses.

  var STAR = { star: true };

  function isDigit(c) { return c >= "0" && c <= "9"; }

  // One scan of the DATA section. Returns {byId, order} where byId maps the
  // numeric id to {id, type, args} -- or, for the "complex" instances Part 21
  // uses for multiply-inherited entities, {id, type:"", parts:[{type,args}]}.
  function parse(text) {
    var i = text.indexOf("DATA;");
    if (i < 0) throw new Error(
      "this file has no DATA section, so it is not a STEP part 21 file. " +
      "Check that it was exported as STEP (.step or .stp) and not as a native CAD format.");
    var end = text.indexOf("END-ISO-10303-21", i);
    if (end < 0) end = text.length;
    var n = end;
    var byId = Object.create(null), order = [];
    var header = text.slice(0, i);

    i += 5;
    while (i < n) {
      // Skip whitespace and comments between statements.
      while (i < n) {
        var c = text.charCodeAt(i);
        if (c === 32 || c === 9 || c === 10 || c === 13) { i++; continue; }
        if (text[i] === "/" && text[i + 1] === "*") {
          var ce = text.indexOf("*/", i + 2);
          i = ce < 0 ? n : ce + 2;
          continue;
        }
        break;
      }
      if (i >= n) break;
      if (text.startsWith("ENDSEC", i)) break;
      if (text[i] !== "#") {                       // not an instance; skip it
        var semi = skipToSemicolon(text, i, n);
        i = semi + 1;
        continue;
      }
      var j = i + 1, id = 0;
      while (j < n && isDigit(text[j])) { id = id * 10 + (text.charCodeAt(j) - 48); j++; }
      while (j < n && (text[j] === " " || text[j] === "\t")) j++;
      if (text[j] !== "=") { i = skipToSemicolon(text, i, n) + 1; continue; }
      j++;
      var stop = skipToSemicolon(text, j, n);
      var body = text.slice(j, stop).trim();
      var ent = parseInstance(id, body);
      if (ent) { byId[id] = ent; order.push(id); }
      i = stop + 1;
    }
    return { byId: byId, order: order, header: header };
  }

  // The end of a statement is the first ';' that is not inside a string.
  function skipToSemicolon(text, i, n) {
    var inStr = false;
    while (i < n) {
      var ch = text[i];
      if (inStr) {
        if (ch === "'") {
          if (text[i + 1] === "'") { i += 2; continue; }   // '' is a literal quote
          inStr = false;
        }
        i++;
        continue;
      }
      if (ch === "'") { inStr = true; i++; continue; }
      if (ch === "/" && text[i + 1] === "*") {
        var ce = text.indexOf("*/", i + 2);
        i = ce < 0 ? n : ce + 2;
        continue;
      }
      if (ch === ";") return i;
      i++;
    }
    return n;
  }

  function parseInstance(id, body) {
    if (!body) return null;
    if (body[0] === "(") {
      // A complex instance: ( NAME(args) NAME(args) ... )
      var parts = [], p = 1;
      while (p < body.length) {
        while (p < body.length && /\s/.test(body[p])) p++;
        if (body[p] === ")" || p >= body.length) break;
        var s = p;
        while (p < body.length && body[p] !== "(") p++;
        var nm = body.slice(s, p).trim().toUpperCase();
        var res = parseList(body, p);
        parts.push({ type: nm, args: res.value });
        p = res.next;
      }
      return { id: id, type: "", parts: parts };
    }
    var k = body.indexOf("(");
    if (k < 0) return null;
    var type = body.slice(0, k).trim().toUpperCase();
    var r = parseList(body, k);
    return { id: id, type: type, args: r.value };
  }

  // Parse a parenthesised, comma-separated argument list starting at `pos`
  // (which must point at the '('). Returns {value, next}.
  function parseList(s, pos) {
    var out = [], i = pos + 1, n = s.length;
    for (;;) {
      while (i < n && /[\s,]/.test(s[i])) i++;
      if (i >= n) break;
      if (s[i] === ")") { i++; break; }
      var r = parseValue(s, i);
      out.push(r.value);
      i = r.next;
    }
    return { value: out, next: i };
  }

  function parseValue(s, i) {
    var n = s.length, c = s[i];
    if (c === "(") return parseList(s, i);
    if (c === "'") {
      var buf = "";
      i++;
      while (i < n) {
        if (s[i] === "'") {
          if (s[i + 1] === "'") { buf += "'"; i += 2; continue; }
          i++; break;
        }
        buf += s[i++];
      }
      return { value: buf, next: i };
    }
    if (c === "#") {
      var j = i + 1, id = 0;
      while (j < n && isDigit(s[j])) { id = id * 10 + (s.charCodeAt(j) - 48); j++; }
      return { value: { r: id }, next: j };
    }
    if (c === ".") {
      var e = s.indexOf(".", i + 1);
      if (e < 0) e = i;
      return { value: { e: s.slice(i + 1, e) }, next: e + 1 };
    }
    if (c === "$") return { value: null, next: i + 1 };
    if (c === "*") return { value: STAR, next: i + 1 };
    // A number, or a typed value like LENGTH_MEASURE(1.0)
    var st = i;
    while (i < n && !/[,)]/.test(s[i])) {
      if (s[i] === "(") { var r2 = parseList(s, i); i = r2.next; continue; }
      if (s[i] === "'") { var r3 = parseValue(s, i); i = r3.next; continue; }
      i++;
    }
    var tok = s.slice(st, i).trim();
    var num = Number(tok);
    if (tok !== "" && isFinite(num) && /^[-+0-9.]/.test(tok)) return { value: num, next: i };
    // A typed value: keep the inner list, which is what carries the number.
    var k = tok.indexOf("(");
    if (k > 0) {
      var inner = parseList(tok, k).value;
      return { value: { typed: tok.slice(0, k).trim().toUpperCase(), value: inner[0] }, next: i };
    }
    return { value: tok, next: i };
  }

  // =====================================================================
  // 2. the model: resolution and units
  // =====================================================================

  function Model(text) {
    var p = parse(text);
    this.byId = p.byId;
    this.order = p.order;
    this.header = p.header;
    this.warnings = [];
    this.unit = readUnits(this);
  }

  Model.prototype.get = function (v) {
    if (!v || typeof v !== "object" || v.r == null) return null;
    return this.byId[v.r] || null;
  };
  // The named part of a complex instance, or the instance itself when its own
  // type matches. Written once because half the geometry below needs it.
  Model.prototype.as = function (ent, type) {
    if (!ent) return null;
    if (ent.type === type) return ent;
    if (ent.parts) {
      for (var i = 0; i < ent.parts.length; i++)
        if (ent.parts[i].type === type) return ent.parts[i];
    }
    return null;
  };
  Model.prototype.all = function (type) {
    var out = [];
    for (var i = 0; i < this.order.length; i++) {
      var e = this.byId[this.order[i]];
      if (e && (e.type === type || this.as(e, type))) out.push(e);
    }
    return out;
  };

  var SI_PREFIX = {
    EXA: 1e18, PETA: 1e15, TERA: 1e12, GIGA: 1e9, MEGA: 1e6, KILO: 1e3,
    HECTO: 1e2, DECA: 1e1, DECI: 1e-1, CENTI: 1e-2, MILLI: 1e-3, MICRO: 1e-6,
    NANO: 1e-9, PICO: 1e-12, FEMTO: 1e-15, ATTO: 1e-18,
  };

  // Every length in the file is in the file's own unit. Everything this module
  // reports is in MILLIMETRES, so the factor is resolved once here -- a model
  // drawn in inches read as millimetres is out by 25.4 in length and 645 in
  // area, which is the kind of error that looks plausible.
  function readUnits(model) {
    var best = null;
    for (var i = 0; i < model.order.length; i++) {
      var e = model.byId[model.order[i]];
      if (!e) continue;
      var conv = model.as(e, "CONVERSION_BASED_UNIT");
      if (conv && model.as(e, "LENGTH_UNIT")) {
        var name = String(conv.args[0] || "").toUpperCase();
        var mw = model.get(conv.args[1]);
        var factor = null;
        if (mw && mw.type === "LENGTH_MEASURE_WITH_UNIT") {
          var v = mw.args[0];
          var mag = typeof v === "number" ? v : (v && v.value);
          var base = siLengthFactor(model, model.get(mw.args[1]));
          if (typeof mag === "number" && base != null) factor = mag * base;
        }
        if (factor != null) return { name: name || "converted", mm: factor, source: "conversion-based unit" };
      }
      if (model.as(e, "LENGTH_UNIT") && model.as(e, "SI_UNIT")) {
        var f = siLengthFactor(model, e);
        if (f != null) best = { name: siName(model, e), mm: f, source: "SI unit" };
      }
    }
    if (best) return best;
    model.warnings.push(
      "this file states no length unit, so its numbers are taken as MILLIMETRES. " +
      "If the model was drawn in inches every area below is out by a factor of 645.");
    return { name: "mm (assumed)", mm: 1, source: "assumed" };
  }

  function siLengthFactor(model, ent) {
    var si = model.as(ent, "SI_UNIT");
    if (!si) return null;
    var pref = si.args[0], nm = si.args[1];
    var unitName = nm && nm.e ? nm.e.toUpperCase() : "";
    if (unitName !== "METRE") return null;
    var scale = pref && pref.e ? (SI_PREFIX[pref.e.toUpperCase()] || 1) : 1;
    return scale * 1000;                            // metres -> millimetres
  }

  function siName(model, ent) {
    var si = model.as(ent, "SI_UNIT");
    var p = si && si.args[0] && si.args[0].e ? si.args[0].e.toLowerCase() : "";
    return (p ? p.slice(0, 1) : "") + "m";
  }

  // =====================================================================
  // 3. placements and vectors
  // =====================================================================

  function sub(a, b) { return [a[0] - b[0], a[1] - b[1], a[2] - b[2]]; }
  function dot(a, b) { return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]; }
  function cross(a, b) {
    return [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];
  }
  function norm(a) { return Math.sqrt(dot(a, a)); }
  function unit(a) { var L = norm(a); return L > 1e-15 ? [a[0] / L, a[1] / L, a[2] / L] : [0, 0, 0]; }

  Model.prototype.point = function (ref) {
    var e = this.get(ref);
    var c = e && (e.type === "CARTESIAN_POINT" ? e : this.as(e, "CARTESIAN_POINT"));
    if (!c || !c.args[1]) return null;
    var v = c.args[1];
    return [+v[0] || 0, +v[1] || 0, +(v.length > 2 ? v[2] : 0) || 0];
  };

  Model.prototype.direction = function (ref) {
    var e = this.get(ref);
    var d = e && (e.type === "DIRECTION" ? e : this.as(e, "DIRECTION"));
    if (!d || !d.args[1]) return null;
    var v = d.args[1];
    return unit([+v[0] || 0, +v[1] || 0, +(v.length > 2 ? v[2] : 0) || 0]);
  };

  // AXIS2_PLACEMENT_3D -> an orthonormal frame {o, x, y, z}. The reference
  // direction is only a hint at the x axis: Part 21 allows it to be
  // non-perpendicular, so it is projected off z and re-normalised.
  Model.prototype.placement = function (ref) {
    var e = this.get(ref);
    var a = e && this.as(e, "AXIS2_PLACEMENT_3D");
    if (!a) a = e && this.as(e, "AXIS2_PLACEMENT_2D");
    if (!a) return null;
    var o = this.point(a.args[1]) || [0, 0, 0];
    var z = this.direction(a.args[2]) || [0, 0, 1];
    var r = this.direction(a.args[3]);
    if (!r) r = Math.abs(z[0]) < 0.9 ? [1, 0, 0] : [0, 1, 0];
    var x = unit(sub(r, [z[0] * dot(r, z), z[1] * dot(r, z), z[2] * dot(r, z)]));
    if (norm(x) < 0.5) x = unit(cross(Math.abs(z[0]) < 0.9 ? [1, 0, 0] : [0, 1, 0], z));
    return { o: o, x: x, y: cross(z, x), z: z };
  };

  // =====================================================================
  // 4. curves
  // =====================================================================
  //
  // Each edge is reduced to a list of SEGMENTS in the face's own 2D frame, and
  // a segment is either a straight line, a circular arc, or a sampled
  // polyline. Keeping arcs as arcs is the point: their contribution to the
  // area integral has a closed form, so a circular groove opening measures
  // exactly instead of as a fine-grained polygon.

  var DEFAULT_SAG = 0.002;                        // mm, for sampled curves only

  // Strip the wrappers CAD puts around the curve that actually carries shape.
  Model.prototype.baseCurve = function (ref, depth) {
    var e = this.get(ref);
    depth = depth || 0;
    if (!e || depth > 8) return e;
    if (e.type === "SURFACE_CURVE" || e.type === "SEAM_CURVE" ||
        e.type === "INTERSECTION_CURVE" || e.type === "BOUNDED_SURFACE_CURVE")
      return this.baseCurve(e.args[1], depth + 1);
    if (e.type === "TRIMMED_CURVE") return this.baseCurve(e.args[1], depth + 1);
    if (e.type === "PCURVE") return e;
    return e;
  };

  // The 3D points along one edge, from `pStart` to `pEnd`, as segments.
  // `sense` is the combined EDGE_CURVE.same_sense and ORIENTED_EDGE.orientation.
  Model.prototype.edgeSegments = function (curveRef, pStart, pEnd, sense, sag) {
    var e = this.baseCurve(curveRef);
    if (!e) return [{ kind: "line", a: pStart, b: pEnd }];
    var t = e.type || "";

    if (t === "LINE" || t === "") return [{ kind: "line", a: pStart, b: pEnd }];

    if (t === "CIRCLE" || t === "ELLIPSE") {
      var pl = this.placement(e.args[1]);
      if (!pl) return [{ kind: "line", a: pStart, b: pEnd }];
      if (t === "CIRCLE") {
        var R = +e.args[2] || 0;
        return [{ kind: "arc", frame: pl, r: R,
                  a0: angleOf(pl, pStart), a1: angleOf(pl, pEnd), ccw: sense !== false,
                  a: pStart, b: pEnd }];
      }
      var r1 = +e.args[2] || 0, r2 = +e.args[3] || 0;
      return [{ kind: "poly", pts: sampleEllipse(pl, r1, r2, pStart, pEnd, sense, sag) }];
    }

    if (t === "POLYLINE") {
      var pts = (e.args[1] || []).map(function (p) { return this.point(p); }, this).filter(Boolean);
      if (sense === false) pts.reverse();
      return pts.length >= 2 ? [{ kind: "poly", pts: pts }] : [{ kind: "line", a: pStart, b: pEnd }];
    }

    var bs = this.as(e, "B_SPLINE_CURVE_WITH_KNOTS");
    if (bs) {
      var sampled = this.sampleBSpline(e, bs, sag);
      if (sampled && sampled.length >= 2) {
        // Line the sampled curve up with the edge's own vertices: a B-spline
        // edge may be stored in either direction.
        if (dist(sampled[0], pStart) > dist(sampled[sampled.length - 1], pStart)) sampled.reverse();
        return [{ kind: "poly", pts: sampled }];
      }
    }
    // A curve type this reader does not evaluate. Fall back to the chord and
    // say so, rather than pretending the edge is missing.
    return [{ kind: "line", a: pStart, b: pEnd, approximated: t }];
  };

  function dist(a, b) { return Math.sqrt(sub(a, b).reduce(function (s, v) { return s + v * v; }, 0)); }

  function angleOf(frame, p) {
    var d = sub(p, frame.o);
    return Math.atan2(dot(d, frame.y), dot(d, frame.x));
  }

  function sampleEllipse(pl, r1, r2, pStart, pEnd, sense, sag) {
    var a0 = angleOf(pl, pStart), a1 = angleOf(pl, pEnd);
    var sweep = a1 - a0;
    if (sense !== false) { while (sweep <= 1e-12) sweep += 2 * Math.PI; }
    else { while (sweep >= -1e-12) sweep -= 2 * Math.PI; }
    var rmax = Math.max(r1, r2);
    var n = arcSteps(rmax, Math.abs(sweep), sag);
    var out = [];
    for (var i = 0; i <= n; i++) {
      var t = a0 + (sweep * i) / n;
      var c = Math.cos(t) * r1, s = Math.sin(t) * r2;
      out.push([pl.o[0] + pl.x[0] * c + pl.y[0] * s,
                pl.o[1] + pl.x[1] * c + pl.y[1] * s,
                pl.o[2] + pl.x[2] * c + pl.y[2] * s]);
    }
    return out;
  }

  function arcSteps(r, sweep, sag) {
    sag = sag || DEFAULT_SAG;
    if (!(r > 0)) return 2;
    var step = r > sag ? 2 * Math.acos(Math.max(-1, Math.min(1, 1 - sag / r))) : sweep;
    return Math.max(8, Math.ceil(sweep / Math.max(step, 1e-9)));
  }

  // de Boor evaluation of a (possibly rational) B-spline. The knot vector in
  // Part 21 is stored as distinct knots plus multiplicities, so it is expanded
  // first.
  Model.prototype.sampleBSpline = function (ent, bs, sag) {
    var degree = +bs.args[1] || 0;
    var cps = (bs.args[2] || []).map(function (p) { return this.point(p); }, this);
    if (cps.some(function (p) { return !p; }) || cps.length < degree + 1) return null;
    var mult = bs.args[6] || [], knots = bs.args[7] || [];
    if (!mult.length || mult.length !== knots.length) return null;
    var kv = [];
    for (var i = 0; i < knots.length; i++)
      for (var m = 0; m < (+mult[i] || 0); m++) kv.push(+knots[i]);
    if (kv.length !== cps.length + degree + 1) return null;

    var rat = this.as(ent, "RATIONAL_B_SPLINE_CURVE");
    var w = rat && rat.args[0] ? rat.args[0].map(Number) : null;
    if (w && w.length !== cps.length) w = null;

    var t0 = kv[degree], t1 = kv[cps.length];
    if (!(t1 > t0)) return null;
    // Sample density from the control polygon's size: a flat curve needs few
    // points, a tight one needs many, and neither is known before looking.
    var span = 0;
    for (var c = 1; c < cps.length; c++) span += dist(cps[c], cps[c - 1]);
    var n = Math.max(2, Math.min(2000, Math.ceil(span / Math.max(sag || DEFAULT_SAG, 1e-6) / 40)));
    if (degree <= 1) n = cps.length - 1;
    var out = [];
    for (var s = 0; s <= n; s++) out.push(deBoor(kv, cps, w, degree, t0 + ((t1 - t0) * s) / n));
    return out;
  };

  function deBoor(kv, cps, w, p, t) {
    var n = cps.length;
    var k = p;
    while (k < n - 1 && t >= kv[k + 1]) k++;
    var d = [];
    for (var j = 0; j <= p; j++) {
      var idx = Math.min(n - 1, Math.max(0, k - p + j));
      var wt = w ? w[idx] : 1;
      d.push([cps[idx][0] * wt, cps[idx][1] * wt, cps[idx][2] * wt, wt]);
    }
    for (var r = 1; r <= p; r++) {
      for (var jj = p; jj >= r; jj--) {
        var i0 = k - p + jj;
        var den = kv[i0 + p - r + 1] - kv[i0];
        var a = den > 1e-15 ? (t - kv[i0]) / den : 0;
        for (var q = 0; q < 4; q++) d[jj][q] = (1 - a) * d[jj - 1][q] + a * d[jj][q];
      }
    }
    var ww = d[p][3] || 1;
    return [d[p][0] / ww, d[p][1] / ww, d[p][2] / ww];
  }

  // =====================================================================
  // 5. surfaces, and flattening a face into 2D
  // =====================================================================

  // Describe a face's surface, and give the map that flattens a 3D point onto
  // it. `develop` is exact for both supported types: a plane is already flat,
  // and a cylinder unrolls without stretching, so an area measured in the
  // flattened frame IS the surface area.
  Model.prototype.surfaceOf = function (ref) {
    var e = this.get(ref);
    if (!e) return { kind: "missing" };
    var pln = this.as(e, "PLANE");
    if (pln) {
      var f = this.placement(pln.args[1]);
      if (!f) return { kind: "unsupported", type: "PLANE (no placement)" };
      return {
        kind: "plane", frame: f, normal: f.z,
        offset: dot(f.z, f.o),
        develop: function (p) { var d = sub(p, f.o); return [dot(d, f.x), dot(d, f.y)]; },
        planar: true,
      };
    }
    var cyl = this.as(e, "CYLINDRICAL_SURFACE");
    if (cyl) {
      var fc = this.placement(cyl.args[1]);
      var R = +cyl.args[2] || 0;
      if (!fc || !(R > 0)) return { kind: "unsupported", type: "CYLINDRICAL_SURFACE (degenerate)" };
      return {
        kind: "cylinder", frame: fc, radius: R, axis: fc.z, origin: fc.o,
        // (R*theta, h). theta must be unwrapped along a loop before use, which
        // developLoop below does -- a face straddling theta = 0 would otherwise
        // fold back on itself.
        develop: function (p) {
          var d = sub(p, fc.o);
          return [R * Math.atan2(dot(d, fc.y), dot(d, fc.x)), dot(d, fc.z)];
        },
        wrap: 2 * Math.PI * R,
        planar: false,
      };
    }
    var t = e.type;
    if (!t && e.parts) t = e.parts.map(function (p) { return p.type; }).join("+");
    return { kind: "unsupported", type: t || "unknown" };
  };

  // =====================================================================
  // 6. face area
  // =====================================================================

  // The exact contribution of one straight segment to 2 * signed area.
  function lineTwiceArea(a, b) { return a[0] * b[1] - b[0] * a[1]; }

  // ...and of one circular arc, from Green's theorem. With
  // x = cx + R cos t, y = cy + R sin t,
  //     x y' - y x' = R^2 + cx R cos t + cy R sin t
  // which integrates in closed form. This is what makes a circular groove
  // opening measure exactly rather than as a many-sided polygon.
  function arcTwiceArea(cx, cy, R, a0, a1) {
    return R * R * (a1 - a0)
         + cx * R * (Math.sin(a1) - Math.sin(a0))
         - cy * R * (Math.cos(a1) - Math.cos(a0));
  }

  // Flatten one loop's segments into the face's 2D frame. Circular arcs stay
  // arcs when the surface is planar AND the arc's own plane is the face's --
  // otherwise the arc is not a circle in the flattened frame and is sampled.
  function developLoop(surf, segs, sag) {
    var out = [], last = null, unwrapRef = null;
    var isCyl = surf.kind === "cylinder";

    function push2(p3) {
      var q = surf.develop(p3);
      if (isCyl) {
        // Unwrap theta so a face crossing the seam does not fold back.
        if (unwrapRef != null) {
          var half = surf.wrap / 2;
          while (q[0] - unwrapRef > half) q[0] -= surf.wrap;
          while (q[0] - unwrapRef < -half) q[0] += surf.wrap;
        }
        unwrapRef = q[0];
      }
      return q;
    }

    for (var i = 0; i < segs.length; i++) {
      var s = segs[i];
      if (s.kind === "arc" && surf.kind === "plane" &&
          Math.abs(Math.abs(dot(s.frame.z, surf.normal)) - 1) < 1e-9) {
        // A circle whose axis is the face normal projects to a circle: keep it.
        var c2 = push2(s.frame.o);
        var a0 = angleIn(surf, s.frame, s.a0);
        var a1 = angleIn(surf, s.frame, s.a1);
        var sweep = a1 - a0;
        var ccw = s.ccw !== false;
        if (dot(s.frame.z, surf.normal) < 0) ccw = !ccw;
        if (ccw) { while (sweep <= 1e-12) sweep += 2 * Math.PI; }
        else { while (sweep >= -1e-12) sweep -= 2 * Math.PI; }
        var start2 = push2(s.a), end2 = push2(s.b);
        out.push({ kind: "arc", c: c2, r: s.r, a0: a0, a1: a0 + sweep, start: start2, end: end2 });
        last = end2;
        continue;
      }
      if (s.kind === "arc") {
        var pts = sampleArc(s, sag).map(push2);
        for (var k = 0; k < pts.length - 1; k++) out.push({ kind: "line", a: pts[k], b: pts[k + 1] });
        last = pts[pts.length - 1];
        continue;
      }
      if (s.kind === "poly") {
        var q2 = s.pts.map(push2);
        for (var m = 0; m < q2.length - 1; m++) out.push({ kind: "line", a: q2[m], b: q2[m + 1] });
        last = q2[q2.length - 1];
        continue;
      }
      var a2 = push2(s.a), b2 = push2(s.b);
      out.push({ kind: "line", a: a2, b: b2 });
      last = b2;
    }
    void last;
    return out;
  }

  // An angle measured in the arc's own frame, re-expressed in the face's.
  function angleIn(surf, arcFrame, ang) {
    var p = [arcFrame.o[0] + Math.cos(ang) * arcFrame.x[0] + Math.sin(ang) * arcFrame.y[0],
             arcFrame.o[1] + Math.cos(ang) * arcFrame.x[1] + Math.sin(ang) * arcFrame.y[1],
             arcFrame.o[2] + Math.cos(ang) * arcFrame.x[2] + Math.sin(ang) * arcFrame.y[2]];
    var c = surf.develop(arcFrame.o), q = surf.develop(p);
    return Math.atan2(q[1] - c[1], q[0] - c[0]);
  }

  function sampleArc(s, sag) {
    var sweep = s.a1 - s.a0;
    if (s.ccw !== false) { while (sweep <= 1e-12) sweep += 2 * Math.PI; }
    else { while (sweep >= -1e-12) sweep -= 2 * Math.PI; }
    var n = arcSteps(s.r, Math.abs(sweep), sag);
    var out = [];
    for (var i = 0; i <= n; i++) {
      var t = s.a0 + (sweep * i) / n;
      out.push([s.frame.o[0] + s.r * (Math.cos(t) * s.frame.x[0] + Math.sin(t) * s.frame.y[0]),
                s.frame.o[1] + s.r * (Math.cos(t) * s.frame.x[1] + Math.sin(t) * s.frame.y[1]),
                s.frame.o[2] + s.r * (Math.cos(t) * s.frame.x[2] + Math.sin(t) * s.frame.y[2])]);
    }
    return out;
  }

  // Signed area of a developed loop, and its perimeter. Straight pieces and
  // circular arcs both contribute in closed form; the loop is closed by a
  // straight chord if the segments leave a gap.
  function loopArea(dev) {
    if (!dev.length) return { area: 0, perimeter: 0, pts: [] };
    var two = 0, per = 0, pts = [], cur = null, first = null;
    for (var i = 0; i < dev.length; i++) {
      var s = dev[i];
      if (s.kind === "arc") {
        if (first == null) { first = s.start; cur = s.start; pts.push(s.start); }
        if (cur && (Math.abs(cur[0] - s.start[0]) > 1e-9 || Math.abs(cur[1] - s.start[1]) > 1e-9)) {
          two += lineTwiceArea(cur, s.start);
          per += Math.hypot(s.start[0] - cur[0], s.start[1] - cur[1]);
          pts.push(s.start);
        }
        two += arcTwiceArea(s.c[0], s.c[1], s.r, s.a0, s.a1);
        per += Math.abs(s.r * (s.a1 - s.a0));
        // Points for drawing only; the area above is already exact.
        var n = Math.max(6, Math.min(180, Math.ceil(Math.abs(s.a1 - s.a0) / 0.15)));
        for (var k = 1; k <= n; k++) {
          var t = s.a0 + ((s.a1 - s.a0) * k) / n;
          pts.push([s.c[0] + s.r * Math.cos(t), s.c[1] + s.r * Math.sin(t)]);
        }
        cur = pts[pts.length - 1];
        continue;
      }
      if (first == null) { first = s.a; cur = s.a; pts.push(s.a); }
      if (cur && (Math.abs(cur[0] - s.a[0]) > 1e-9 || Math.abs(cur[1] - s.a[1]) > 1e-9)) {
        two += lineTwiceArea(cur, s.a);
        per += Math.hypot(s.a[0] - cur[0], s.a[1] - cur[1]);
        pts.push(s.a);
      }
      two += lineTwiceArea(s.a, s.b);
      per += Math.hypot(s.b[0] - s.a[0], s.b[1] - s.a[1]);
      pts.push(s.b);
      cur = s.b;
    }
    if (cur && first) {
      two += lineTwiceArea(cur, first);
      per += Math.hypot(first[0] - cur[0], first[1] - cur[1]);
    }
    return { area: two / 2, perimeter: per, pts: pts };
  }

  // =====================================================================
  // 7. reading the faces
  // =====================================================================

  Model.prototype.faceLoops = function (face, sag) {
    var bounds = face.args[1] || [], out = [];
    for (var i = 0; i < bounds.length; i++) {
      var b = this.get(bounds[i]);
      if (!b) continue;
      var isOuter = b.type === "FACE_OUTER_BOUND";
      var loop = this.get(b.args[1]);
      var el = loop && this.as(loop, "EDGE_LOOP");
      var segs = [];
      if (el) {
        var oes = el.args[1] || [];
        for (var j = 0; j < oes.length; j++) {
          var oe = this.get(oes[j]);
          if (!oe || oe.type !== "ORIENTED_EDGE") continue;
          var fwd = !(oe.args[4] && oe.args[4].e === "F");
          var ec = this.get(oe.args[3]);
          if (!ec || ec.type !== "EDGE_CURVE") continue;
          var same = !(ec.args[4] && ec.args[4].e === "F");
          var v0 = this.point(this.get(ec.args[1]) ? this.get(ec.args[1]).args[1] : null);
          var v1 = this.point(this.get(ec.args[2]) ? this.get(ec.args[2]).args[1] : null);
          if (!v0 || !v1) continue;
          var a = fwd ? v0 : v1, bp = fwd ? v1 : v0;
          var s = this.edgeSegments(ec.args[3], a, bp, fwd === same, sag);
          for (var q = 0; q < s.length; q++) segs.push(s[q]);
        }
      } else {
        var vl = loop && this.as(loop, "VERTEX_LOOP");
        if (vl) continue;                            // a degenerate point loop
      }
      if (segs.length) out.push({ outer: isOuter, segs: segs });
    }
    return out;
  };

  // Measure every ADVANCED_FACE in the model. Returns one record per face with
  // its area in mm^2, its surface descriptor, and its developed outline for
  // drawing. A face whose surface does not develop gets area null and a reason.
  Model.prototype.readFaces = function (opts) {
    opts = opts || {};
    var sag = opts.sagitta || DEFAULT_SAG;
    var k = this.unit.mm;                            // file units -> mm
    var faces = this.all("ADVANCED_FACE"), out = [];
    for (var i = 0; i < faces.length; i++) {
      var f = faces[i], af = this.as(f, "ADVANCED_FACE");
      var surf = this.surfaceOf(af.args[2]);
      var rec = { id: f.id, surface: surf.kind, area: null, reason: null,
                  loops: [], perimeter: 0, holes: 0, approximated: 0 };
      if (surf.kind !== "plane" && surf.kind !== "cylinder") {
        rec.reason = surf.type || surf.kind;
        out.push(rec);
        continue;
      }
      var loops = this.faceLoops(af, sag / Math.max(k, 1e-9));
      if (!loops.length) { rec.reason = "no bounded loop"; out.push(rec); continue; }
      var measured = [];
      for (var L = 0; L < loops.length; L++) {
        for (var s2 = 0; s2 < loops[L].segs.length; s2++)
          if (loops[L].segs[s2].approximated) rec.approximated++;
        var dev = developLoop(surf, loops[L].segs, sag / Math.max(k, 1e-9));
        var m = loopArea(dev);
        measured.push({ outer: loops[L].outer, a: m.area, per: m.perimeter, pts: m.pts });
      }
      // Which loop is the outside: the file usually says so, and when it does
      // not the largest loop is the only defensible reading.
      var outerIdx = measured.findIndex(function (m) { return m.outer; });
      if (outerIdx < 0) {
        outerIdx = 0;
        for (var z = 1; z < measured.length; z++)
          if (Math.abs(measured[z].a) > Math.abs(measured[outerIdx].a)) outerIdx = z;
      }
      var area = Math.abs(measured[outerIdx].a), per = measured[outerIdx].per;
      for (var h = 0; h < measured.length; h++) {
        if (h === outerIdx) continue;
        area -= Math.abs(measured[h].a);
        per += measured[h].per;
        rec.holes++;
      }
      // Every length scales by the unit factor, so every area scales by its
      // square. Done once, here, rather than at each vertex.
      rec.area = Math.max(0, area) * k * k;
      rec.perimeter = per * k;
      rec.loops = measured.map(function (m, ix) {
        return { outer: ix === outerIdx, pts: m.pts.map(function (p) { return [p[0] * k, p[1] * k]; }) };
      });
      rec.surfaceInfo = surf.kind === "plane"
        ? { normal: surf.normal, offset: surf.offset * k }
        : { axis: surf.axis, origin: surf.origin.map(function (v) { return v * k; }), radius: surf.radius * k };
      out.push(rec);
    }
    return out;
  };

  // =====================================================================
  // 8. surface families
  // =====================================================================
  //
  // A tread top is one surface cut into hundreds of faces. Asking the user to
  // tick two hundred boxes would be absurd, so faces that lie on the SAME
  // surface -- the same plane, or the same cylinder -- are grouped, and the
  // tread is then a single pick.

  function canonicalDir(d) {
    // A plane's normal and its negation describe the same plane; fix the sign
    // so the two do not land in different families.
    for (var i = 0; i < 3; i++) {
      if (Math.abs(d[i]) > 1e-9) return d[i] > 0 ? d.slice() : [-d[0], -d[1], -d[2]];
    }
    return d.slice();
  }

  function familyKey(rec, tol) {
    var q = function (v) { return Math.round(v / tol) * tol; };
    if (rec.surface === "plane") {
      var n = canonicalDir(rec.surfaceInfo.normal);
      var off = dot(n, rec.surfaceInfo.normal) < 0 ? -rec.surfaceInfo.offset : rec.surfaceInfo.offset;
      return "P|" + n.map(function (v) { return q(v).toFixed(6); }).join(",") + "|" + q(off).toFixed(4);
    }
    var a = canonicalDir(rec.surfaceInfo.axis);
    // Two cylinders match when they share an axis LINE and a radius, so the
    // origin is reduced to its component across the axis.
    var o = rec.surfaceInfo.origin;
    var perp = sub(o, [a[0] * dot(o, a), a[1] * dot(o, a), a[2] * dot(o, a)]);
    return "C|" + a.map(function (v) { return q(v).toFixed(6); }).join(",") + "|" +
           q(rec.surfaceInfo.radius).toFixed(4) + "|" +
           perp.map(function (v) { return q(v).toFixed(4); }).join(",");
  }

  // Group measured faces into surface families, largest total area first --
  // on a tread model the top surface is almost always the biggest, so the
  // default pick is usually right and always visible.
  function groupFaces(recs, opts) {
    opts = opts || {};
    var tol = opts.tolerance || 1e-4;
    var map = Object.create(null), out = [];
    for (var i = 0; i < recs.length; i++) {
      var r = recs[i];
      if (r.area == null) continue;
      var key = familyKey(r, tol);
      var g = map[key];
      if (!g) {
        g = map[key] = {
          key: key, kind: r.surface, faces: [], area: 0, perimeter: 0, holes: 0,
          info: r.surfaceInfo,
        };
        out.push(g);
      }
      g.faces.push(r);
      g.area += r.area;
      g.perimeter += r.perimeter;
      g.holes += r.holes;
    }
    for (var j = 0; j < out.length; j++) {
      var gg = out[j];
      gg.n = gg.faces.length;
      gg.bbox = familyBBox(gg);
      gg.label = familyLabel(gg);
    }
    out.sort(function (a, b) { return b.area - a.area; });
    return out;
  }

  function familyBBox(g) {
    var x0 = Infinity, x1 = -Infinity, y0 = Infinity, y1 = -Infinity;
    for (var i = 0; i < g.faces.length; i++) {
      var ls = g.faces[i].loops;
      for (var j = 0; j < ls.length; j++) {
        var pts = ls[j].pts;
        for (var k = 0; k < pts.length; k++) {
          if (pts[k][0] < x0) x0 = pts[k][0];
          if (pts[k][0] > x1) x1 = pts[k][0];
          if (pts[k][1] < y0) y0 = pts[k][1];
          if (pts[k][1] > y1) y1 = pts[k][1];
        }
      }
    }
    if (!isFinite(x0)) return null;
    return { x0: x0, x1: x1, y0: y0, y1: y1, w: x1 - x0, h: y1 - y0, area: (x1 - x0) * (y1 - y0) };
  }

  function familyLabel(g) {
    if (g.kind === "plane") {
      var n = g.info.normal;
      var ax = Math.abs(n[2]) > 0.999 ? "Z" : Math.abs(n[1]) > 0.999 ? "Y"
             : Math.abs(n[0]) > 0.999 ? "X" : "tilted";
      return "plane ⊥ " + ax + " at " + g.info.offset.toFixed(3) + " mm";
    }
    return "cylinder R " + g.info.radius.toFixed(3) + " mm";
  }

  // How far one family sits from another, measured along the tread family's own
  // outward direction: up the canonical normal for a plane, outward in radius
  // for a cylinder.
  //
  // SIGNED on purpose. A positive value means the second family lies BELOW the
  // first, which is a groove depth -- the non-skid depth a 2D tread plan cannot
  // carry, measured here from the model instead of typed in. A negative value
  // means the surface picked as the floor is actually outboard of the tread, so
  // the two picks are the wrong way round, and saying so beats reporting a
  // plausible-looking depth for an inverted selection. Null means the surfaces
  // are not parallel (or not coaxial) and one is not a floor of the other at all.
  function familyOffsetBelow(ref, other) {
    if (!ref || !other || ref.kind !== other.kind) return null;
    if (ref.kind === "plane") {
      var nr = canonicalDir(ref.info.normal), no = canonicalDir(other.info.normal);
      if (Math.abs(Math.abs(dot(nr, no)) - 1) > 1e-6) return null;
      return ref.info.offset - other.info.offset;
    }
    if (Math.abs(Math.abs(dot(canonicalDir(ref.info.axis), canonicalDir(other.info.axis))) - 1) > 1e-6)
      return null;
    return ref.info.radius - other.info.radius;
  }

  // Which family is most likely the tread surface.
  //
  // NOT the largest: a solid floor plate under a blocked tread is one big face
  // while the tread itself is two hundred small ones, and picking by area gets
  // it exactly backwards. The tread is the surface that touches the road, so it
  // is the OUTERMOST one -- the greatest offset along its normal, or the
  // greatest radius. Ties break on area.
  //
  // This is a suggestion, and the page says so: a model built upside down would
  // fool it, and only the engineer knows which surface they meant.
  function suggestTread(groups) {
    if (!groups.length) return null;
    // Work within the commonest surface kind, so one stray plane in a
    // cylindrical model does not win.
    var count = {};
    groups.forEach(function (g) { count[g.kind] = (count[g.kind] || 0) + g.area; });
    var kind = Object.keys(count).sort(function (a, b) { return count[b] - count[a]; })[0];
    var pool = groups.filter(function (g) { return g.kind === kind; });
    if (!pool.length) return null;
    var best = pool[0];
    for (var i = 1; i < pool.length; i++) {
      var d = familyOffsetBelow(pool[i], best);
      if (d == null) { if (pool[i].area > best.area) best = pool[i]; continue; }
      if (d > 1e-9 || (Math.abs(d) <= 1e-9 && pool[i].area > best.area)) best = pool[i];
    }
    return best;
  }

  // The groove floor under a chosen tread: the family with the most area that
  // lies below it. The biggest floor is the main groove bottom; a shallower
  // sipe floor or a stone-ejector shelf is smaller and is not what NSD means.
  function suggestFloor(groups, tread) {
    var best = null, bestArea = -1;
    for (var i = 0; i < groups.length; i++) {
      var g = groups[i];
      if (g === tread) continue;
      var d = familyOffsetBelow(tread, g);
      if (d == null || d <= 1e-9) continue;
      if (g.area > bestArea) { best = g; bestArea = g.area; }
    }
    return best;
  }

  // =====================================================================
  // 9. the measurement
  // =====================================================================

  // Land, sea and everything that follows from them, for a chosen set of
  // families. `envelope` is the reference area the ratio is taken against:
  // without one there is no ratio, and different choices give different
  // answers, so it is always reported alongside.
  function measure(groups, selectedKeys, opts) {
    opts = opts || {};
    var sel = groups.filter(function (g) { return selectedKeys.indexOf(g.key) >= 0; });
    if (!sel.length) return null;
    var land = 0, per = 0, nFaces = 0, holes = 0;
    for (var i = 0; i < sel.length; i++) {
      land += sel[i].area; per += sel[i].perimeter;
      nFaces += sel[i].n; holes += sel[i].holes;
    }
    // The reference envelope. Stated dimensions win when given; otherwise the
    // bounding box of what was picked, in the surface's own developed frame.
    var bbox = null;
    for (var j = 0; j < sel.length; j++) {
      var b = sel[j].bbox;
      if (!b) continue;
      bbox = bbox ? { x0: Math.min(bbox.x0, b.x0), x1: Math.max(bbox.x1, b.x1),
                      y0: Math.min(bbox.y0, b.y0), y1: Math.max(bbox.y1, b.y1) } : { x0: b.x0, x1: b.x1, y0: b.y0, y1: b.y1 };
    }
    if (bbox) { bbox.w = bbox.x1 - bbox.x0; bbox.h = bbox.y1 - bbox.y0; bbox.area = bbox.w * bbox.h; }
    var envW = opts.width != null && opts.width > 0 ? opts.width : (bbox ? bbox.w : 0);
    var envH = opts.height != null && opts.height > 0 ? opts.height : (bbox ? bbox.h : 0);
    var envelope = envW * envH;
    var envSource = (opts.width > 0 || opts.height > 0) ? "stated" : "bounding box of the selection";

    var depth = null, floor = null, depthNote = null;
    if (opts.floorKey) {
      floor = groups.find(function (g) { return g.key === opts.floorKey; }) || null;
      if (floor) {
        var sep = familyOffsetBelow(sel[0], floor);
        if (sep == null) depthNote = "the surface chosen as the groove floor is not parallel to the tread, so there is no single depth between them";
        else if (sep <= 0) depthNote = "the surface chosen as the groove floor lies OUTSIDE the tread surface by " +
          Math.abs(sep).toFixed(3) + " mm — the two picks look the wrong way round";
        else depth = sep;
      }
    }
    var sea = envelope > 0 ? Math.max(0, envelope - land) : 0;
    return {
      land_mm2: land,
      sea_mm2: sea,
      envelope_mm2: envelope,
      envelope_w: envW, envelope_h: envH, envelope_source: envSource,
      bbox: bbox,
      land_ratio: envelope > 0 ? land / envelope : 0,
      sea_ratio: envelope > 0 ? sea / envelope : 0,
      n_faces: nFaces,
      n_holes: holes,
      edge_length_mm: per,
      // Biting edge per unit of tread. Two patterns of the same land ratio can
      // differ several-fold here, and it is what wet grip trades against.
      edge_density: envelope > 0 ? per / envelope : 0,
      groove_depth_mm: depth,
      groove_depth_note: depthNote,
      // A groove is not a prism -- it has draft, and radiused corners -- so
      // this is an upper bound on the void, not the void.
      groove_volume_mm3: depth != null ? sea * depth : null,
      selected: sel,
      floor: floor,
    };
  }

  // One call, for the page: read, measure, group.
  function readModel(text, opts) {
    var m = new Model(text);
    var faces = m.readFaces(opts);
    var groups = groupFaces(faces, opts);
    var unmeasured = faces.filter(function (f) { return f.area == null; });
    var byReason = {};
    for (var i = 0; i < unmeasured.length; i++) {
      var r = unmeasured[i].reason || "unknown";
      byReason[r] = (byReason[r] || 0) + 1;
    }
    if (unmeasured.length)
      m.warnings.push(unmeasured.length + " of " + faces.length + " face(s) are on surfaces this " +
        "reader cannot measure and are NOT counted: " +
        Object.keys(byReason).map(function (k) { return k + " x" + byReason[k]; }).join(", ") +
        ". Only planes and cylinders develop without distortion; anything else needs a real " +
        "surface integral. If the tread surface is one of these, the numbers below are not it.");
    var approx = faces.reduce(function (s, f) { return s + (f.approximated || 0); }, 0);
    if (approx)
      m.warnings.push(approx + " edge(s) are on curve types this reader does not evaluate and were " +
        "taken as straight chords, so the areas they bound are slightly under-measured.");
    if (!faces.length)
      throw new Error(
        "no ADVANCED_FACE entities were found in this STEP file. It may carry only " +
        "wireframe or mesh geometry, or be an assembly whose parts are in separate files.");
    return {
      model: m, faces: faces, groups: groups,
      unit: m.unit, warnings: m.warnings,
      n_entities: m.order.length,
      n_unmeasured: unmeasured.length,
    };
  }

  return {
    parse: parse, Model: Model, readModel: readModel,
    groupFaces: groupFaces, measure: measure,
    familyOffsetBelow: familyOffsetBelow, familyKey: familyKey,
    suggestTread: suggestTread, suggestFloor: suggestFloor,
    loopArea: loopArea, arcTwiceArea: arcTwiceArea, deBoor: deBoor,
    DEFAULT_SAG: DEFAULT_SAG,
  };
});
