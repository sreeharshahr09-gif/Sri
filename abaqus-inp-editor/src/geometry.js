/*
 * geometry.js -- element topology and display-mesh construction.
 *
 * Turns node/element arrays into what the viewer draws:
 *   - the exterior surface of solid meshes (faces referenced by one element),
 *   - shell/membrane elements drawn directly,
 *   - beam/truss elements drawn as lines,
 *   - feature edges (boundary edges plus creases past an angle threshold).
 *
 * Triangles are emitted flat-shaded (vertices not shared between faces) which
 * is both cheaper to build and the correct look for an FE mesh.
 */
(function (global) {
  'use strict';

  /*
   * Face tables use zero-based corner indices in Abaqus connectivity order.
   * Only corner nodes participate: a C3D10 is topologically a C3D4, so the
   * mid-side nodes are ignored for display.
   */
  var TOPO = {
    tet: {
      dim: 3, corners: 4,
      faces: [[0, 1, 2], [0, 3, 1], [1, 3, 2], [2, 3, 0]]
    },
    hex: {
      dim: 3, corners: 8,
      faces: [[0, 1, 2, 3], [4, 7, 6, 5], [0, 4, 5, 1], [1, 5, 6, 2], [2, 6, 7, 3], [3, 7, 4, 0]]
    },
    wedge: {
      dim: 3, corners: 6,
      faces: [[0, 1, 2], [3, 5, 4], [0, 3, 4, 1], [1, 4, 5, 2], [2, 5, 3, 0]]
    },
    pyramid: {
      dim: 3, corners: 5,
      faces: [[0, 1, 2, 3], [0, 1, 4], [1, 2, 4], [2, 3, 4], [3, 0, 4]]
    },
    tri: { dim: 2, corners: 3, faces: [[0, 1, 2]] },
    quad: { dim: 2, corners: 4, faces: [[0, 1, 2, 3]] },
    line: { dim: 1, corners: 2, faces: [] },
    point: { dim: 0, corners: 1, faces: [] }
  };

  /*
   * Classify an Abaqus element type name. The explicit table covers the common
   * families; anything unknown falls back to the node count, which is right
   * often enough to still draw something useful.
   */
  function classify(type, nodeCount) {
    var t = String(type || '').toUpperCase().replace(/[^A-Z0-9]/g, '');

    /* Continuum 3-D. */
    if (/^C3D4/.test(t) || /^C3D10/.test(t) || /^DC3D4/.test(t) || /^DC3D10/.test(t) ||
        /^AC3D4/.test(t) || /^C3D4T/.test(t)) return 'tet';
    if (/^C3D8/.test(t) || /^C3D20/.test(t) || /^DC3D8/.test(t) || /^DC3D20/.test(t) ||
        /^AC3D8/.test(t) || /^SC8/.test(t) || /^COH3D8/.test(t)) return 'hex';
    if (/^C3D6/.test(t) || /^C3D15/.test(t) || /^DC3D6/.test(t) || /^SC6/.test(t) ||
        /^COH3D6/.test(t)) return 'wedge';
    if (/^C3D5/.test(t)) return 'pyramid';

    /* Shell, membrane, surface, rigid and 2-D continuum: drawn as their face. */
    if (/^(S|STRI|M3D|R3D|DS|SFM3D|CPS|CPE|CPEG|CAX|DCAX|ACAX|CGAX|COH2D)/.test(t)) {
      if (/3$|3R$|65$|6$|3T$/.test(t) || nodeCount === 3 || nodeCount === 6) return 'tri';
      return 'quad';
    }

    /* Structural line elements. */
    if (/^(B2|B3|T2D|T3D|PIPE|ELBOW|FRAME|CONN|DASHPOT|SPRING|GAPUNI|DCOUP3D)/.test(t)) return 'line';
    if (/^(MASS|ROTARYI)/.test(t)) return 'point';

    /* Unknown: guess from the corner count. */
    if (nodeCount >= 8) return 'hex';
    if (nodeCount === 6 || nodeCount === 15) return 'wedge';
    if (nodeCount === 5) return 'pyramid';
    if (nodeCount === 4) return 'tet';
    if (nodeCount === 3) return 'tri';
    if (nodeCount === 2) return 'line';
    return 'point';
  }

  function topoFor(type, nodeCount) {
    return TOPO[classify(type, nodeCount)];
  }

  /*
   * 3x4 affine transform for an instance: translate, then rotate about the
   * axis given in assembly coordinates (the order Abaqus applies them in).
   */
  function instanceTransform(translation, rotation) {
    var t = translation || [0, 0, 0];
    var m = [1, 0, 0, t[0], 0, 1, 0, t[1], 0, 0, 1, t[2]];
    if (!rotation) return m;

    var ax = rotation[0], ay = rotation[1], az = rotation[2];
    var bx = rotation[3], by = rotation[4], bz = rotation[5];
    var deg = rotation[6];
    var dx = bx - ax, dy = by - ay, dz = bz - az;
    var len = Math.sqrt(dx * dx + dy * dy + dz * dz);
    if (len < 1e-12 || !deg) return m;
    dx /= len; dy /= len; dz /= len;

    var a = deg * Math.PI / 180, c = Math.cos(a), s = Math.sin(a), C = 1 - c;
    /* Rodrigues rotation matrix. */
    var r = [
      c + dx * dx * C, dx * dy * C - dz * s, dx * dz * C + dy * s,
      dy * dx * C + dz * s, c + dy * dy * C, dy * dz * C - dx * s,
      dz * dx * C - dy * s, dz * dy * C + dx * s, c + dz * dz * C
    ];
    /* Compose: rotate about point a, applied after the translation. */
    var out = new Array(12);
    for (var row = 0; row < 3; row++) {
      for (var col = 0; col < 3; col++) {
        out[row * 4 + col] = r[row * 3] * m[col] + r[row * 3 + 1] * m[4 + col] + r[row * 3 + 2] * m[8 + col];
      }
      var tx = m[3] - ax, ty = m[7] - ay, tz = m[11] - az;
      out[row * 4 + 3] = ax * (row === 0 ? 1 : 0) + ay * (row === 1 ? 1 : 0) + az * (row === 2 ? 1 : 0) +
        r[row * 3] * tx + r[row * 3 + 1] * ty + r[row * 3 + 2] * tz;
    }
    return out;
  }

  function applyTransform(m, x, y, z, out) {
    out[0] = m[0] * x + m[1] * y + m[2] * z + m[3];
    out[1] = m[4] * x + m[5] * y + m[6] * z + m[7];
    out[2] = m[8] * x + m[9] * y + m[10] * z + m[11];
    return out;
  }

  /*
   * Build the display mesh for one node/element set.
   *
   * Returns flat-shaded triangles with a per-triangle element index, plus line
   * segments for beam elements and a transformed copy of every node position.
   *
   *   positions  Float32Array  3 * 3 * triangleCount
   *   normals    Float32Array  matching
   *   triElem    Int32Array    element index per triangle
   *   elemTri    { start, count } per element index, for highlighting
   *   nodeXYZ    Float32Array  transformed node coordinates
   *   edges      Float32Array  feature-edge line segments
   *   lines      Float32Array  beam/truss segments
   */
  function buildDisplayMesh(nodes, elements, transform, opts) {
    opts = opts || {};
    var featureAngle = opts.featureAngle === undefined ? 30 : opts.featureAngle;
    var m = transform || [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0];

    var nCount = nodes.count;
    var nodeXYZ = new Float32Array(nCount * 3);
    var tmp = [0, 0, 0];
    var i, j, k;
    for (i = 0; i < nCount; i++) {
      applyTransform(m, nodes.xyz[i * 3], nodes.xyz[i * 3 + 1], nodes.xyz[i * 3 + 2], tmp);
      nodeXYZ[i * 3] = tmp[0];
      nodeXYZ[i * 3 + 1] = tmp[1];
      nodeXYZ[i * 3 + 2] = tmp[2];
    }

    var nodeMap = nodes.map;
    var eCount = elements.count;

    /*
     * Pass 1: collect every face of every solid element into a hash keyed by
     * its sorted corner ids. Faces seen once are on the exterior.
     */
    var faceMap = new Map();
    var surfaceFaces = [];   /* {elem, corners:[nodeIndex...]} */
    var lineSegs = [];
    var pointIdx = [];
    var skipped = 0;

    for (i = 0; i < eCount; i++) {
      var conn = elements.conn[i];
      var type = elements.types[elements.typeOf[i]];
      var topo = topoFor(type, conn.length);
      if (!topo) { skipped++; continue; }

      if (topo.dim === 0) {
        var pi = nodeMap[conn[0]];
        if (pi !== undefined) pointIdx.push(pi);
        continue;
      }
      if (topo.dim === 1) {
        var a = nodeMap[conn[0]], b = nodeMap[conn[1]];
        if (a !== undefined && b !== undefined) lineSegs.push(a, b);
        continue;
      }
      if (topo.dim === 2) {
        /* Shell-like: the element is its own face, always visible. */
        var fc = [];
        var ok = true;
        for (j = 0; j < topo.corners; j++) {
          var ix = nodeMap[conn[j]];
          if (ix === undefined) { ok = false; break; }
          fc.push(ix);
        }
        if (ok) surfaceFaces.push({ elem: i, corners: fc });
        continue;
      }

      /* Solid: hash each face. */
      for (j = 0; j < topo.faces.length; j++) {
        var face = topo.faces[j];
        var idx = new Array(face.length);
        var good = true;
        for (k = 0; k < face.length; k++) {
          var gi = nodeMap[conn[face[k]]];
          if (gi === undefined) { good = false; break; }
          idx[k] = gi;
        }
        if (!good) continue;
        var sorted = idx.slice().sort(function (p, q) { return p - q; });
        var key = sorted.join(',');
        var hit = faceMap.get(key);
        if (hit === undefined) faceMap.set(key, { elem: i, corners: idx, n: 1 });
        else hit.n++;
      }
    }

    faceMap.forEach(function (f) {
      if (f.n === 1) surfaceFaces.push(f);
    });
    faceMap.clear();

    /* Faces come out of the map in insertion order, which follows element
       order, so triangles for one element stay contiguous. */
    surfaceFaces.sort(function (p, q) { return p.elem - q.elem; });

    /* Pass 2: triangulate, flat shaded. */
    var triCount = 0;
    for (i = 0; i < surfaceFaces.length; i++) {
      triCount += surfaceFaces[i].corners.length - 2;
    }

    var positions = new Float32Array(triCount * 9);
    var normals = new Float32Array(triCount * 9);
    var triElem = new Int32Array(triCount);
    var elemTri = new Map();

    var t = 0;
    for (i = 0; i < surfaceFaces.length; i++) {
      var f2 = surfaceFaces[i];
      var c = f2.corners;
      var start = t;
      for (j = 1; j + 1 < c.length; j++) {
        writeTri(positions, normals, t, nodeXYZ, c[0], c[j], c[j + 1]);
        triElem[t] = f2.elem;
        t++;
      }
      var prev = elemTri.get(f2.elem);
      if (prev === undefined) elemTri.set(f2.elem, { start: start, count: t - start });
      else prev.count = t - prev.start;
    }

    var edgeSets = buildEdges(surfaceFaces, nodeXYZ, featureAngle);

    var lines = new Float32Array(lineSegs.length * 3);
    for (i = 0; i < lineSegs.length; i++) {
      lines[i * 3] = nodeXYZ[lineSegs[i] * 3];
      lines[i * 3 + 1] = nodeXYZ[lineSegs[i] * 3 + 1];
      lines[i * 3 + 2] = nodeXYZ[lineSegs[i] * 3 + 2];
    }

    var points = new Float32Array(pointIdx.length * 3);
    for (i = 0; i < pointIdx.length; i++) {
      points[i * 3] = nodeXYZ[pointIdx[i] * 3];
      points[i * 3 + 1] = nodeXYZ[pointIdx[i] * 3 + 1];
      points[i * 3 + 2] = nodeXYZ[pointIdx[i] * 3 + 2];
    }

    return {
      positions: positions,
      normals: normals,
      triElem: triElem,
      elemTri: elemTri,
      nodeXYZ: nodeXYZ,
      edges: edgeSets.feature,
      meshEdges: edgeSets.all,
      lines: lines,
      points: points,
      triangleCount: triCount,
      surfaceFaceCount: surfaceFaces.length,
      skipped: skipped,
      bounds: boundsOf(nodeXYZ)
    };
  }

  function writeTri(pos, nor, t, xyz, ia, ib, ic) {
    var o = t * 9;
    var ax = xyz[ia * 3], ay = xyz[ia * 3 + 1], az = xyz[ia * 3 + 2];
    var bx = xyz[ib * 3], by = xyz[ib * 3 + 1], bz = xyz[ib * 3 + 2];
    var cx = xyz[ic * 3], cy = xyz[ic * 3 + 1], cz = xyz[ic * 3 + 2];
    pos[o] = ax; pos[o + 1] = ay; pos[o + 2] = az;
    pos[o + 3] = bx; pos[o + 4] = by; pos[o + 5] = bz;
    pos[o + 6] = cx; pos[o + 7] = cy; pos[o + 8] = cz;

    var ux = bx - ax, uy = by - ay, uz = bz - az;
    var vx = cx - ax, vy = cy - ay, vz = cz - az;
    var nx = uy * vz - uz * vy;
    var ny = uz * vx - ux * vz;
    var nz = ux * vy - uy * vx;
    var len = Math.sqrt(nx * nx + ny * ny + nz * nz) || 1;
    nx /= len; ny /= len; nz /= len;
    for (var q = 0; q < 3; q++) {
      nor[o + q * 3] = nx;
      nor[o + q * 3 + 1] = ny;
      nor[o + q * 3 + 2] = nz;
    }
  }

  /*
   * Edge extraction over the exterior faces, in one pass, producing two sets:
   *
   *   all      every edge of every exterior face -- the surface mesh wireframe
   *   feature  only edges bordering a single face, or where the two faces
   *            meeting there differ by more than the threshold angle, which
   *            gives clean part outlines without the interior mesh lines
   */
  function buildEdges(faces, xyz, angleDeg) {
    var cosLimit = Math.cos(angleDeg * Math.PI / 180);
    var map = new Map();
    var i, j;

    for (i = 0; i < faces.length; i++) {
      var c = faces[i].corners;
      var n = faceNormal(xyz, c);
      for (j = 0; j < c.length; j++) {
        var a = c[j], b = c[(j + 1) % c.length];
        var key = a < b ? (a + ',' + b) : (b + ',' + a);
        var e = map.get(key);
        if (e === undefined) map.set(key, { a: a, b: b, n: n, count: 1 });
        else { e.count++; e.n2 = n; }
      }
    }

    var keep = [], every = [];
    map.forEach(function (e) {
      every.push(e);
      if (e.count !== 2) { keep.push(e); return; }
      var d = e.n[0] * e.n2[0] + e.n[1] * e.n2[1] + e.n[2] * e.n2[2];
      if (d < cosLimit) keep.push(e);
    });
    map.clear();

    return { feature: packEdges(keep, xyz), all: packEdges(every, xyz) };
  }

  function packEdges(list, xyz) {
    var out = new Float32Array(list.length * 6);
    for (var i = 0; i < list.length; i++) {
      var o = i * 6, e = list[i];
      out[o] = xyz[e.a * 3]; out[o + 1] = xyz[e.a * 3 + 1]; out[o + 2] = xyz[e.a * 3 + 2];
      out[o + 3] = xyz[e.b * 3]; out[o + 4] = xyz[e.b * 3 + 1]; out[o + 5] = xyz[e.b * 3 + 2];
    }
    return out;
  }

  function faceNormal(xyz, c) {
    var ax = xyz[c[0] * 3], ay = xyz[c[0] * 3 + 1], az = xyz[c[0] * 3 + 2];
    var bx = xyz[c[1] * 3], by = xyz[c[1] * 3 + 1], bz = xyz[c[1] * 3 + 2];
    var cx = xyz[c[2] * 3], cy = xyz[c[2] * 3 + 1], cz = xyz[c[2] * 3 + 2];
    var ux = bx - ax, uy = by - ay, uz = bz - az;
    var vx = cx - ax, vy = cy - ay, vz = cz - az;
    var nx = uy * vz - uz * vy, ny = uz * vx - ux * vz, nz = ux * vy - uy * vx;
    var l = Math.sqrt(nx * nx + ny * ny + nz * nz) || 1;
    return [nx / l, ny / l, nz / l];
  }

  function boundsOf(xyz) {
    var min = [Infinity, Infinity, Infinity];
    var max = [-Infinity, -Infinity, -Infinity];
    for (var i = 0; i < xyz.length; i += 3) {
      for (var k = 0; k < 3; k++) {
        var v = xyz[i + k];
        if (v < min[k]) min[k] = v;
        if (v > max[k]) max[k] = v;
      }
    }
    if (!isFinite(min[0])) { min = [0, 0, 0]; max = [0, 0, 0]; }
    return { min: min, max: max };
  }

  function mergeBounds(a, b) {
    if (!a) return b;
    if (!b) return a;
    return {
      min: [Math.min(a.min[0], b.min[0]), Math.min(a.min[1], b.min[1]), Math.min(a.min[2], b.min[2])],
      max: [Math.max(a.max[0], b.max[0]), Math.max(a.max[1], b.max[1]), Math.max(a.max[2], b.max[2])]
    };
  }

  /*
   * Analytical rigid surfaces (*SURFACE, TYPE=CYLINDER / SEGMENTS / REVOLUTION)
   * are described by a 2-D profile of START / LINE / CIRCL segments. We sweep
   * that profile so the road, punch or die is visible next to the mesh.
   *
   * Without an explicit generator-axis data line Abaqus takes the local z
   * direction; the sweep length is a fraction of the model size, so treat this
   * as an indication of where the surface is rather than its exact extent.
   */
  function buildAnalyticalSurface(block, transform, sweepLength) {
    var P = global.InpParser;
    var rows = P.dataRows(block).filter(function (r) { return !r.comment && r.fields; });
    var type = (P.getParam(block, 'type') || '').toUpperCase();
    if (rows.length === 0) return null;

    var profile = [];
    var cursor = null;
    var i;

    /* A leading data line without a START/LINE/CIRCL tag defines the axis. */
    var first = rows[0].fields[0].trim().toUpperCase();
    var startRow = (first === 'START') ? 0 : (rows.length > 1 ? 1 : 0);

    for (i = startRow; i < rows.length; i++) {
      var f = rows[i].fields;
      var tag = f[0].trim().toUpperCase();
      var nums = f.slice(1).map(parseFloat).filter(isFinite);
      if (tag === 'START') {
        cursor = [nums[0] || 0, nums[1] || 0];
        profile.push(cursor.slice());
      } else if (tag === 'LINE' && cursor) {
        cursor = [nums[0] || 0, nums[1] || 0];
        profile.push(cursor.slice());
      } else if (tag === 'CIRCL' && cursor) {
        /* end point (x,y) then centre (cx,cy) */
        var ex = nums[0], ey = nums[1], cx = nums[2], cy = nums[3];
        if ([ex, ey, cx, cy].every(isFinite)) {
          var a0 = Math.atan2(cursor[1] - cy, cursor[0] - cx);
          var a1 = Math.atan2(ey - cy, ex - cx);
          var r = Math.hypot(cursor[0] - cx, cursor[1] - cy);
          var sweep = a1 - a0;
          while (sweep > Math.PI) sweep -= 2 * Math.PI;
          while (sweep < -Math.PI) sweep += 2 * Math.PI;
          var steps = Math.max(8, Math.ceil(Math.abs(sweep) / (Math.PI / 24)));
          for (var s = 1; s <= steps; s++) {
            var ang = a0 + sweep * (s / steps);
            profile.push([cx + r * Math.cos(ang), cy + r * Math.sin(ang)]);
          }
          cursor = [ex, ey];
        }
      }
    }

    if (profile.length < 2) return null;

    var half = (sweepLength || 100) / 2;
    var m = transform || [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0];
    var tris = [];
    var nors = [];
    var tmpA = [0, 0, 0], tmpB = [0, 0, 0], tmpC = [0, 0, 0], tmpD = [0, 0, 0];

    for (i = 0; i + 1 < profile.length; i++) {
      var p0 = profile[i], p1 = profile[i + 1];
      applyTransform(m, p0[0], p0[1], -half, tmpA);
      applyTransform(m, p1[0], p1[1], -half, tmpB);
      applyTransform(m, p1[0], p1[1], half, tmpC);
      applyTransform(m, p0[0], p0[1], half, tmpD);
      pushQuad(tris, nors, tmpA, tmpB, tmpC, tmpD);
    }

    return {
      type: type,
      positions: new Float32Array(tris),
      normals: new Float32Array(nors),
      profile: profile,
      bounds: boundsOf(new Float32Array(tris))
    };
  }

  function pushQuad(tris, nors, a, b, c, d) {
    var ux = b[0] - a[0], uy = b[1] - a[1], uz = b[2] - a[2];
    var vx = d[0] - a[0], vy = d[1] - a[1], vz = d[2] - a[2];
    var nx = uy * vz - uz * vy, ny = uz * vx - ux * vz, nz = ux * vy - uy * vx;
    var l = Math.sqrt(nx * nx + ny * ny + nz * nz) || 1;
    nx /= l; ny /= l; nz /= l;
    [[a, b, c], [a, c, d]].forEach(function (tri) {
      tri.forEach(function (p) {
        tris.push(p[0], p[1], p[2]);
        nors.push(nx, ny, nz);
      });
    });
  }

  global.InpGeometry = {
    TOPO: TOPO,
    classify: classify,
    topoFor: topoFor,
    instanceTransform: instanceTransform,
    applyTransform: applyTransform,
    buildDisplayMesh: buildDisplayMesh,
    buildAnalyticalSurface: buildAnalyticalSurface,
    boundsOf: boundsOf,
    mergeBounds: mergeBounds
  };
})(typeof window !== 'undefined' ? window : globalThis);
