/*
 * viewer.js -- binds the parsed model to the WebGL renderer.
 *
 * Builds one drawable per mesh source (a part as placed by each instance, plus
 * any mesh an instance defines itself), wires mouse navigation, resolves set
 * highlighting and reports what the user clicked on.
 */
(function (global) {
  'use strict';

  var G = global.InpGeometry;
  var P = global.InpParser;

  /* Distinguishable at a glance, and readable against the dark viewport. */
  var PALETTE = [
    [0.42, 0.61, 0.85], [0.85, 0.55, 0.35], [0.47, 0.75, 0.55],
    [0.80, 0.47, 0.62], [0.60, 0.55, 0.85], [0.85, 0.75, 0.40],
    [0.40, 0.74, 0.78], [0.78, 0.45, 0.45], [0.55, 0.70, 0.42],
    [0.68, 0.60, 0.50]
  ];

  function Viewer(canvas, opts) {
    this.canvas = canvas;
    this.opts = opts || {};
    this.renderer = new global.InpGL.Renderer(canvas);
    this.model = null;
    this.meshes = [];          /* surface drawables with model metadata */
    this.edgeObjs = [];
    this.meshEdgeObjs = [];
    this.lineObjs = [];
    this.pointObjs = [];
    this.analyticObjs = [];
    this.nodeCloudObjs = [];
    this.highlightPoints = null;
    this.bounds = null;
    this.onPick = null;
    this.onHover = null;

    this.display = {
      shaded: true,
      edges: 'feature',        /* none | feature | mesh */
      nodes: false,
      opacity: 1,
      analytical: true,
      perspective: true,
      upAxis: 1,
      colorBy: 'instance'      /* instance | part | elementType */
    };

    this._needsRender = true;
    this._installControls();
    this._loop();
  }

  /* ------------------------------------------------------------- building */

  Viewer.prototype.setModel = function (model) {
    this.renderer.clear();
    this.meshes = [];
    this.edgeObjs = [];
    this.meshEdgeObjs = [];
    this.lineObjs = [];
    this.pointObjs = [];
    this.analyticObjs = [];
    this.nodeCloudObjs = [];
    this.highlightPoints = null;
    this.model = model;
    this.bounds = null;

    var self = this;
    var sources = this._collectSources(model);
    var colorIdx = 0;

    sources.forEach(function (src) {
      if (src.elements.count === 0 && src.nodes.count === 0) return;
      var mesh = G.buildDisplayMesh(src.nodes, src.elements, src.transform, {
        featureAngle: 30
      });

      var color = PALETTE[colorIdx % PALETTE.length];
      colorIdx++;

      var surf = null;
      if (mesh.triangleCount > 0) {
        surf = self.renderer.addSurface({
          name: src.label,
          key: src.key,
          color: color,
          positions: mesh.positions,
          normals: mesh.normals,
          triElem: mesh.triElem,
          elemTri: mesh.elemTri,
          elementIds: src.elements.ids,
          bounds: mesh.bounds,
          meta: src
        });
        self.meshes.push(surf);
      }

      if (mesh.edges.length) {
        self.edgeObjs.push(self.renderer.addLines({
          name: src.label + ' edges', key: src.key,
          color: [0.06, 0.07, 0.10, 0.9],
          positions: mesh.edges, meta: src
        }));
      }
      if (mesh.meshEdges.length) {
        var me = self.renderer.addLines({
          name: src.label + ' mesh', key: src.key,
          color: [0.10, 0.12, 0.16, 0.55],
          positions: mesh.meshEdges, meta: src
        });
        me.visible = false;
        self.meshEdgeObjs.push(me);
      }
      if (mesh.lines.length) {
        self.lineObjs.push(self.renderer.addLines({
          name: src.label + ' beams', key: src.key,
          color: color.concat([1]),
          positions: mesh.lines, meta: src
        }));
      }
      if (mesh.points.length) {
        self.pointObjs.push(self.renderer.addPoints({
          name: src.label + ' points', key: src.key,
          color: color.concat([1]), size: 7,
          positions: mesh.points, meta: src
        }));
      }

      /* Every node, shown on demand. Reference points of rigid bodies are the
         only geometry some instances have, so they must be reachable. */
      var cloud = self.renderer.addPoints({
        name: src.label + ' nodes', key: src.key,
        color: [0.95, 0.95, 0.98, 0.9], size: 2.5,
        positions: mesh.nodeXYZ, meta: src
      });
      cloud.visible = false;
      self.nodeCloudObjs.push(cloud);

      src.mesh = mesh;
      self.bounds = G.mergeBounds(self.bounds, mesh.bounds);
    });

    /* Analytical rigid surfaces (roads, punches, dies). */
    var sweep = this.bounds ? Math.max(
      this.bounds.max[0] - this.bounds.min[0],
      this.bounds.max[1] - this.bounds.min[1],
      this.bounds.max[2] - this.bounds.min[2]
    ) : 100;

    this._analyticalSources(model).forEach(function (a) {
      var geo = G.buildAnalyticalSurface(a.block, a.transform, sweep);
      if (!geo || !geo.positions.length) return;
      var obj = self.renderer.addSurface({
        name: a.label, key: a.key,
        color: [0.55, 0.57, 0.62],
        opacity: 0.75,
        positions: geo.positions,
        normals: geo.normals,
        pickable: false,
        meta: { analytical: true, block: a.block, label: a.label }
      });
      self.analyticObjs.push(obj);
      self.bounds = G.mergeBounds(self.bounds, geo.bounds);
    });

    this.applyDisplay();
    this.fitAll();
    this.invalidate();
    return { sources: sources.length, bounds: this.bounds };
  };

  /*
   * Every drawable mesh in the model: each instance contributes its part's
   * mesh (transformed into assembly coordinates) and any mesh of its own. A
   * file with no assembly falls back to drawing the parts where they sit.
   */
  Viewer.prototype._collectSources = function (model) {
    var out = [];
    var asm = model.assembly;

    if (asm && asm.instances.length) {
      asm.instances.forEach(function (inst) {
        var xf = G.instanceTransform(inst.translation, inst.rotation);
        if (inst.part && inst.part.elements.count > 0) {
          out.push({
            kind: 'instance-part', label: inst.name, key: 'inst:' + inst.name,
            instance: inst, part: inst.part, transform: xf,
            nodes: inst.part.nodes, elements: inst.part.elements
          });
        }
        if (inst.nodes.count > 0 || inst.elements.count > 0) {
          out.push({
            kind: 'instance-own', label: inst.name + ' (local)',
            key: 'instlocal:' + inst.name,
            instance: inst, part: inst.part, transform: xf,
            nodes: inst.nodes, elements: inst.elements
          });
        }
      });
      if (asm.nodes.count > 0 || asm.elements.count > 0) {
        out.push({
          kind: 'assembly', label: 'Assembly mesh', key: 'assembly',
          instance: null, part: null, transform: null,
          nodes: asm.nodes, elements: asm.elements
        });
      }
    } else {
      model.parts.forEach(function (p) {
        out.push({
          kind: 'part', label: p.name, key: 'part:' + p.name,
          instance: null, part: p, transform: null,
          nodes: p.nodes, elements: p.elements
        });
      });
    }
    return out;
  };

  Viewer.prototype._analyticalSources = function (model) {
    var out = [];
    function scan(container, label, xf, keyPrefix) {
      container.surfaces.forEach(function (b) {
        var type = (P.getParam(b, 'type') || '').toUpperCase();
        if (!type || type === 'ELEMENT' || type === 'NODE') return;
        var name = P.getParam(b, 'name') || 'surface';
        out.push({ block: b, label: label + '.' + name, transform: xf, key: keyPrefix + name });
      });
    }
    if (model.assembly) {
      model.assembly.instances.forEach(function (inst) {
        var xf = G.instanceTransform(inst.translation, inst.rotation);
        scan(inst.container, inst.name, xf, 'anasurf:' + inst.name + ':');
        if (inst.part) scan(inst.part.container, inst.name, xf, 'anasurf:' + inst.name + ':part:');
      });
      scan(model.assembly.container, 'Assembly', null, 'anasurf:assembly:');
    }
    model.parts.forEach(function (p) {
      if (model.assembly && model.assembly.instances.length) return;
      scan(p.container, p.name, null, 'anasurf:part:' + p.name + ':');
    });
    return out;
  };

  /* -------------------------------------------------------------- display */

  Viewer.prototype.applyDisplay = function () {
    var d = this.display;
    var self = this;

    this.meshes.forEach(function (m) {
      m.visible = d.shaded && self._srcVisible(m.meta);
      m.opacity = d.opacity;
    });
    this.edgeObjs.forEach(function (o) {
      o.visible = d.edges === 'feature' && self._srcVisible(o.meta);
    });
    this.meshEdgeObjs.forEach(function (o) {
      o.visible = d.edges === 'mesh' && self._srcVisible(o.meta);
    });
    this.lineObjs.forEach(function (o) { o.visible = self._srcVisible(o.meta); });
    this.pointObjs.forEach(function (o) { o.visible = self._srcVisible(o.meta); });
    this.nodeCloudObjs.forEach(function (o) {
      o.visible = d.nodes && self._srcVisible(o.meta);
    });
    this.analyticObjs.forEach(function (o) { o.visible = d.analytical; });

    /* With shading off, edges must carry the image, so brighten them. */
    var lit = d.shaded;
    this.edgeObjs.forEach(function (o) {
      o.color = lit ? [0.06, 0.07, 0.10, 0.9] : [0.62, 0.72, 0.88, 0.95];
    });
    this.meshEdgeObjs.forEach(function (o) {
      o.color = lit ? [0.10, 0.12, 0.16, 0.5] : [0.55, 0.65, 0.80, 0.7];
    });

    this.renderer.perspective = d.perspective;
    this.renderer.upAxis = d.upAxis;
    this.invalidate();
  };

  Viewer.prototype._srcVisible = function (meta) {
    if (!meta) return true;
    if (meta.hidden) return false;
    return true;
  };

  Viewer.prototype.setSourceVisible = function (key, visible) {
    this.meshes.concat(this.edgeObjs, this.meshEdgeObjs, this.lineObjs,
      this.pointObjs, this.nodeCloudObjs).forEach(function (o) {
      if (o.meta && o.key === key) o.meta.hidden = !visible;
    });
    this.applyDisplay();
  };

  Viewer.prototype.sourceList = function () {
    var seen = {}, out = [];
    this.meshes.concat(this.lineObjs, this.pointObjs).forEach(function (o) {
      if (!o.meta || seen[o.key]) return;
      seen[o.key] = 1;
      out.push({ key: o.key, label: o.meta.label, hidden: !!o.meta.hidden, color: o.color });
    });
    return out;
  };

  /* ------------------------------------------------------------ selection */

  /*
   * Highlight a resolved set. Element sets light up the elements themselves;
   * node sets draw a point cloud at the member nodes.
   */
  Viewer.prototype.highlight = function (selection) {
    var self = this;
    this.meshes.forEach(function (m) { self.renderer.setHighlight(m, null); });
    if (this.highlightPoints) {
      this.renderer.remove(this.highlightPoints);
      this.highlightPoints = null;
    }
    if (!selection || !selection.ids || !selection.ids.length) { this.invalidate(); return null; }

    var found = 0;
    if (selection.kind === 'elset') {
      this.meshes.forEach(function (m) {
        var src = m.meta;
        if (!src || !src.elements) return;
        if (!self._selectionAppliesTo(selection, src)) return;
        var idxs = [];
        for (var i = 0; i < selection.ids.length; i++) {
          var ix = src.elements.map[selection.ids[i]];
          if (ix !== undefined) idxs.push(ix);
        }
        if (idxs.length) {
          self.renderer.setHighlight(m, idxs);
          found += idxs.length;
        }
      });
    } else {
      var pts = [];
      this.meshes.concat(this.nodeCloudObjs).forEach(function (m) { m._done = false; });
      var seenKeys = {};
      var all = this.meshes.concat(this.nodeCloudObjs);
      all.forEach(function (m) {
        var src = m.meta;
        if (!src || !src.nodes || !src.mesh || seenKeys[m.key]) return;
        if (!self._selectionAppliesTo(selection, src)) return;
        seenKeys[m.key] = 1;
        for (var i = 0; i < selection.ids.length; i++) {
          var ix = src.nodes.map[selection.ids[i]];
          if (ix === undefined) continue;
          pts.push(src.mesh.nodeXYZ[ix * 3], src.mesh.nodeXYZ[ix * 3 + 1], src.mesh.nodeXYZ[ix * 3 + 2]);
          found++;
        }
      });
      if (pts.length) {
        this.highlightPoints = this.renderer.addPoints({
          name: 'selection', key: '__selection',
          color: [1.0, 0.78, 0.25, 1], size: 6,
          positions: new Float32Array(pts)
        });
      }
    }
    this.invalidate();
    return found;
  };

  /*
   * A set defined inside an instance (or on a part) only applies to the mesh
   * of that instance; an unqualified assembly set applies wherever it resolves.
   */
  Viewer.prototype._selectionAppliesTo = function (selection, src) {
    if (selection.instance) return src.instance === selection.instance;
    if (selection.part) return src.part === selection.part || src.kind === 'part';
    return true;
  };

  Viewer.prototype.fitAll = function () {
    this.renderer.fit(this.bounds);
    this.invalidate();
  };

  Viewer.prototype.fitTo = function (bounds) {
    this.renderer.fit(bounds, 1.6);
    this.invalidate();
  };

  Viewer.prototype.setView = function (name) {
    this.renderer.setView(name);
    this.invalidate();
  };

  /* ------------------------------------------------------------- controls */

  Viewer.prototype._installControls = function () {
    var self = this;
    var canvas = this.canvas;
    var dragging = null, lastX = 0, lastY = 0, moved = 0;

    canvas.addEventListener('contextmenu', function (e) { e.preventDefault(); });

    canvas.addEventListener('pointerdown', function (e) {
      canvas.setPointerCapture(e.pointerId);
      dragging = (e.button === 0 && !e.shiftKey && !e.ctrlKey) ? 'orbit' : 'pan';
      lastX = e.clientX; lastY = e.clientY; moved = 0;
    });

    canvas.addEventListener('pointermove', function (e) {
      if (!dragging) return;
      var dx = e.clientX - lastX, dy = e.clientY - lastY;
      lastX = e.clientX; lastY = e.clientY;
      moved += Math.abs(dx) + Math.abs(dy);
      var cam = self.renderer.camera;
      if (dragging === 'orbit') {
        cam.yaw -= dx * 0.008;
        cam.pitch += dy * 0.008;
        var lim = Math.PI / 2 - 0.02;
        cam.pitch = Math.max(-lim, Math.min(lim, cam.pitch));
      } else {
        /* Pan in the camera plane, scaled so the grab point tracks the cursor. */
        var eye = self.renderer.eye();
        var f = [cam.target[0] - eye[0], cam.target[1] - eye[1], cam.target[2] - eye[2]];
        var fl = Math.hypot(f[0], f[1], f[2]) || 1;
        f = [f[0] / fl, f[1] / fl, f[2] / fl];
        var up = self.renderer.upVector();
        var r = [
          f[1] * up[2] - f[2] * up[1],
          f[2] * up[0] - f[0] * up[2],
          f[0] * up[1] - f[1] * up[0]
        ];
        var rl = Math.hypot(r[0], r[1], r[2]) || 1;
        r = [r[0] / rl, r[1] / rl, r[2] / rl];
        var u = [
          r[1] * f[2] - r[2] * f[1],
          r[2] * f[0] - r[0] * f[2],
          r[0] * f[1] - r[1] * f[0]
        ];
        var scale = cam.distance * 2 * Math.tan(cam.fov * Math.PI / 360) / canvas.clientHeight;
        for (var i = 0; i < 3; i++) {
          cam.target[i] += (-dx * r[i] + dy * u[i]) * scale;
        }
      }
      self.invalidate();
    });

    function endDrag(e) {
      if (dragging && moved < 4 && e.button === 0) self._click(e);
      dragging = null;
    }
    canvas.addEventListener('pointerup', endDrag);
    canvas.addEventListener('pointercancel', function () { dragging = null; });

    canvas.addEventListener('wheel', function (e) {
      e.preventDefault();
      var cam = self.renderer.camera;
      var k = Math.exp((e.deltaY > 0 ? 1 : -1) * -0.12);
      cam.distance = Math.max(1e-6, cam.distance * k);
      self.invalidate();
    }, { passive: false });

    canvas.addEventListener('dblclick', function (e) {
      var hit = self._pickAt(e);
      if (hit && hit.point) {
        self.renderer.camera.target = hit.point;
        self.invalidate();
      }
    });
  };

  Viewer.prototype._pickAt = function (e) {
    var rect = this.canvas.getBoundingClientRect();
    var hit = this.renderer.pick(e.clientX - rect.left, e.clientY - rect.top);
    if (!hit) return null;
    /* Centroid of the picked triangle, used as the orbit target. */
    var obj = hit.object;
    var meta = obj.meta;
    var info = {
      object: obj,
      elementIndex: hit.elementIndex,
      elementId: hit.elementId,
      source: meta,
      point: null
    };
    if (meta && meta.mesh) {
      var o = hit.triangle * 9;
      var p = meta.mesh.positions;
      info.point = [
        (p[o] + p[o + 3] + p[o + 6]) / 3,
        (p[o + 1] + p[o + 4] + p[o + 7]) / 3,
        (p[o + 2] + p[o + 5] + p[o + 8]) / 3
      ];
      if (hit.elementIndex >= 0 && meta.elements) {
        info.elementType = meta.elements.types[meta.elements.typeOf[hit.elementIndex]];
        info.connectivity = meta.elements.conn[hit.elementIndex];
      }
    }
    return info;
  };

  Viewer.prototype._click = function (e) {
    var info = this._pickAt(e);
    if (this.onPick) this.onPick(info);
  };

  /* ---------------------------------------------------------------- frame */

  Viewer.prototype.invalidate = function () { this._needsRender = true; };

  Viewer.prototype._loop = function () {
    var self = this;
    function frame() {
      if (self._needsRender) {
        self._needsRender = false;
        try { self.renderer.render(); } catch (err) { /* keep the loop alive */ }
      }
      global.requestAnimationFrame(frame);
    }
    global.requestAnimationFrame(frame);
  };

  global.InpViewer = { Viewer: Viewer, PALETTE: PALETTE };
})(typeof window !== 'undefined' ? window : globalThis);
