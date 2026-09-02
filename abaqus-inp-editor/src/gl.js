/*
 * gl.js -- a small self-contained WebGL renderer.
 *
 * Deliberately dependency free so the editor is a single file that works from
 * a local disk with no network. It draws exactly what an FE model needs:
 * flat-shaded triangle soups, line sets, point sets, a per-element highlight
 * channel and colour-coded picking.
 */
(function (global) {
  'use strict';

  /* ------------------------------------------------------------- matrices */

  var M4 = {
    identity: function () {
      return new Float32Array([1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]);
    },
    perspective: function (fovy, aspect, near, far) {
      var f = 1 / Math.tan(fovy / 2), nf = 1 / (near - far);
      return new Float32Array([
        f / aspect, 0, 0, 0,
        0, f, 0, 0,
        0, 0, (far + near) * nf, -1,
        0, 0, 2 * far * near * nf, 0
      ]);
    },
    ortho: function (l, r, b, t, n, f) {
      return new Float32Array([
        2 / (r - l), 0, 0, 0,
        0, 2 / (t - b), 0, 0,
        0, 0, -2 / (f - n), 0,
        -(r + l) / (r - l), -(t + b) / (t - b), -(f + n) / (f - n), 1
      ]);
    },
    lookAt: function (eye, center, up) {
      var z0 = eye[0] - center[0], z1 = eye[1] - center[1], z2 = eye[2] - center[2];
      var len = Math.sqrt(z0 * z0 + z1 * z1 + z2 * z2) || 1;
      z0 /= len; z1 /= len; z2 /= len;
      var x0 = up[1] * z2 - up[2] * z1;
      var x1 = up[2] * z0 - up[0] * z2;
      var x2 = up[0] * z1 - up[1] * z0;
      len = Math.sqrt(x0 * x0 + x1 * x1 + x2 * x2);
      if (!len) { x0 = 1; x1 = 0; x2 = 0; } else { x0 /= len; x1 /= len; x2 /= len; }
      var y0 = z1 * x2 - z2 * x1;
      var y1 = z2 * x0 - z0 * x2;
      var y2 = z0 * x1 - z1 * x0;
      return new Float32Array([
        x0, y0, z0, 0,
        x1, y1, z1, 0,
        x2, y2, z2, 0,
        -(x0 * eye[0] + x1 * eye[1] + x2 * eye[2]),
        -(y0 * eye[0] + y1 * eye[1] + y2 * eye[2]),
        -(z0 * eye[0] + z1 * eye[1] + z2 * eye[2]), 1
      ]);
    },
    multiply: function (a, b) {
      var o = new Float32Array(16), i, j, k, s;
      for (i = 0; i < 4; i++) {
        for (j = 0; j < 4; j++) {
          s = 0;
          for (k = 0; k < 4; k++) s += a[k * 4 + j] * b[i * 4 + k];
          o[i * 4 + j] = s;
        }
      }
      return o;
    }
  };

  /* -------------------------------------------------------------- shaders */

  var SURFACE_VS = [
    'attribute vec3 aPos;',
    'attribute vec3 aNormal;',
    'attribute float aHi;',
    'uniform mat4 uMVP;',
    'varying vec3 vN;',
    'varying float vHi;',
    'void main() {',
    '  vN = aNormal;',
    '  vHi = aHi;',
    '  gl_Position = uMVP * vec4(aPos, 1.0);',
    '}'
  ].join('\n');

  var SURFACE_FS = [
    'precision mediump float;',
    'varying vec3 vN;',
    'varying float vHi;',
    'uniform vec3 uColor;',
    'uniform vec3 uHiColor;',
    'uniform vec3 uEyeDir;',
    'uniform float uOpacity;',
    'void main() {',
    '  vec3 n = normalize(vN);',
    /* Face winding is not reliable across mixed element types, so light both
       sides and take the absolute value of the head-light term. */
    '  float head = abs(dot(n, -uEyeDir));',
    '  vec3 keyDir = normalize(vec3(0.35, 0.75, 0.55));',
    '  float key = abs(dot(n, keyDir));',
    '  float amb = 0.34 + 0.16 * (0.5 + 0.5 * n.y);',
    '  float lum = amb + 0.52 * head + 0.24 * key;',
    '  vec3 base = mix(uColor, uHiColor, clamp(vHi, 0.0, 1.0));',
    '  vec3 c = base * lum;',
    /* A cheap rim term separates overlapping parts. */
    '  float rim = pow(1.0 - head, 3.0) * 0.16;',
    '  c += rim;',
    '  gl_FragColor = vec4(clamp(c, 0.0, 1.0), uOpacity);',
    '}'
  ].join('\n');

  var FLAT_VS = [
    'attribute vec3 aPos;',
    'uniform mat4 uMVP;',
    'uniform float uPointSize;',
    'void main() {',
    '  gl_Position = uMVP * vec4(aPos, 1.0);',
    '  gl_PointSize = uPointSize;',
    '}'
  ].join('\n');

  var FLAT_FS = [
    'precision mediump float;',
    'uniform vec4 uColor;',
    'uniform float uRound;',
    'void main() {',
    '  if (uRound > 0.5) {',
    '    vec2 d = gl_PointCoord - vec2(0.5);',
    '    if (dot(d, d) > 0.25) discard;',
    '  }',
    '  gl_FragColor = uColor;',
    '}'
  ].join('\n');

  var PICK_VS = [
    'attribute vec3 aPos;',
    'attribute vec3 aId;',
    'uniform mat4 uMVP;',
    'varying vec3 vId;',
    'void main() {',
    '  vId = aId;',
    '  gl_Position = uMVP * vec4(aPos, 1.0);',
    '}'
  ].join('\n');

  var PICK_FS = [
    'precision mediump float;',
    'varying vec3 vId;',
    'void main() { gl_FragColor = vec4(vId, 1.0); }'
  ].join('\n');

  function compile(gl, type, src) {
    var s = gl.createShader(type);
    gl.shaderSource(s, src);
    gl.compileShader(s);
    if (!gl.getShaderParameter(s, gl.COMPILE_STATUS)) {
      throw new Error('shader: ' + gl.getShaderInfoLog(s));
    }
    return s;
  }

  function program(gl, vs, fs, attrs) {
    var p = gl.createProgram();
    gl.attachShader(p, compile(gl, gl.VERTEX_SHADER, vs));
    gl.attachShader(p, compile(gl, gl.FRAGMENT_SHADER, fs));
    gl.linkProgram(p);
    if (!gl.getProgramParameter(p, gl.LINK_STATUS)) {
      throw new Error('link: ' + gl.getProgramInfoLog(p));
    }
    var o = { program: p, a: {}, u: {} };
    attrs.forEach(function (n) { o.a[n] = gl.getAttribLocation(p, n); });
    var count = gl.getProgramParameter(p, gl.ACTIVE_UNIFORMS);
    for (var i = 0; i < count; i++) {
      var name = gl.getActiveUniform(p, i).name.replace(/\[0\]$/, '');
      o.u[name] = gl.getUniformLocation(p, name);
    }
    return o;
  }

  /* ------------------------------------------------------------- renderer */

  function Renderer(canvas) {
    var gl = canvas.getContext('webgl', {
      antialias: true, alpha: false, preserveDrawingBuffer: false, depth: true
    }) || canvas.getContext('experimental-webgl');
    if (!gl) throw new Error('WebGL is not available in this browser.');

    this.canvas = canvas;
    this.gl = gl;
    this.objects = [];
    this.background = [0.086, 0.098, 0.125];
    this.upAxis = 1;             /* 0=X 1=Y 2=Z */
    this.showTriad = true;
    this.perspective = true;

    this.camera = {
      target: [0, 0, 0], distance: 10, yaw: 0.9, pitch: 0.45, fov: 40, roll: 0
    };

    this.progSurface = program(gl, SURFACE_VS, SURFACE_FS, ['aPos', 'aNormal', 'aHi']);
    this.progFlat = program(gl, FLAT_VS, FLAT_FS, ['aPos']);
    this.progPick = program(gl, PICK_VS, PICK_FS, ['aPos', 'aId']);

    gl.enable(gl.DEPTH_TEST);
    gl.depthFunc(gl.LEQUAL);
    gl.enable(gl.BLEND);
    gl.blendFunc(gl.SRC_ALPHA, gl.ONE_MINUS_SRC_ALPHA);

    this._pickFB = null;
    this._pickTex = null;
    this._pickDepth = null;
    this._pickSize = [0, 0];
    this._nextPickBase = 1;
    this._pickRegistry = [];
  }

  Renderer.prototype.buffer = function (data, target) {
    var gl = this.gl;
    var b = gl.createBuffer();
    gl.bindBuffer(target || gl.ARRAY_BUFFER, b);
    gl.bufferData(target || gl.ARRAY_BUFFER, data, gl.STATIC_DRAW);
    return b;
  };

  /*
   * A surface object. `triElem` maps each triangle to a model element index so
   * picking and set highlighting can talk in element terms.
   */
  Renderer.prototype.addSurface = function (spec) {
    var gl = this.gl;
    var triCount = spec.positions.length / 9;
    var obj = {
      kind: 'surface',
      name: spec.name || '',
      key: spec.key || spec.name,
      color: spec.color || [0.62, 0.68, 0.78],
      hiColor: spec.hiColor || [1.0, 0.72, 0.2],
      opacity: spec.opacity === undefined ? 1 : spec.opacity,
      visible: spec.visible !== false,
      pickable: spec.pickable !== false,
      triCount: triCount,
      triElem: spec.triElem || null,
      elemTri: spec.elemTri || null,
      elementIds: spec.elementIds || null,
      meta: spec.meta || null,
      posBuf: this.buffer(spec.positions),
      norBuf: this.buffer(spec.normals),
      hi: new Float32Array(triCount * 3),
      hiBuf: null,
      hiDirty: false,
      idBuf: null,
      pickBase: 0,
      bounds: spec.bounds || null
    };
    obj.hiBuf = gl.createBuffer();
    gl.bindBuffer(gl.ARRAY_BUFFER, obj.hiBuf);
    gl.bufferData(gl.ARRAY_BUFFER, obj.hi, gl.DYNAMIC_DRAW);
    this.objects.push(obj);
    return obj;
  };

  Renderer.prototype.addLines = function (spec) {
    var obj = {
      kind: 'lines',
      name: spec.name || '',
      key: spec.key || spec.name,
      color: spec.color || [0.1, 0.12, 0.16, 0.85],
      visible: spec.visible !== false,
      count: spec.positions.length / 3,
      posBuf: this.buffer(spec.positions),
      meta: spec.meta || null
    };
    this.objects.push(obj);
    return obj;
  };

  Renderer.prototype.addPoints = function (spec) {
    var obj = {
      kind: 'points',
      name: spec.name || '',
      key: spec.key || spec.name,
      color: spec.color || [1, 0.4, 0.2, 1],
      size: spec.size || 5,
      round: spec.round !== false,
      visible: spec.visible !== false,
      count: spec.positions.length / 3,
      posBuf: this.buffer(spec.positions),
      meta: spec.meta || null
    };
    this.objects.push(obj);
    return obj;
  };

  Renderer.prototype.updatePoints = function (obj, positions) {
    var gl = this.gl;
    gl.bindBuffer(gl.ARRAY_BUFFER, obj.posBuf);
    gl.bufferData(gl.ARRAY_BUFFER, positions, gl.STATIC_DRAW);
    obj.count = positions.length / 3;
  };

  Renderer.prototype.remove = function (obj) {
    var i = this.objects.indexOf(obj);
    if (i >= 0) this.objects.splice(i, 1);
    var gl = this.gl;
    ['posBuf', 'norBuf', 'hiBuf', 'idBuf'].forEach(function (k) {
      if (obj[k]) gl.deleteBuffer(obj[k]);
    });
  };

  Renderer.prototype.clear = function () {
    var self = this;
    this.objects.slice().forEach(function (o) { self.remove(o); });
    this.objects = [];
    this._pickRegistry = [];
    this._nextPickBase = 1;
  };

  /* Highlight: set of element indices, or null to clear. */
  Renderer.prototype.setHighlight = function (obj, elementIndices) {
    if (obj.kind !== 'surface') return;
    obj.hi.fill(0);
    if (elementIndices && obj.elemTri) {
      var hi = obj.hi;
      elementIndices.forEach(function (ei) {
        var span = obj.elemTri.get(ei);
        if (!span) return;
        var from = span.start * 3, to = (span.start + span.count) * 3;
        for (var v = from; v < to; v++) hi[v] = 1;
      });
    }
    obj.hiDirty = true;
  };

  Renderer.prototype._flushHighlight = function (obj) {
    if (!obj.hiDirty) return;
    var gl = this.gl;
    gl.bindBuffer(gl.ARRAY_BUFFER, obj.hiBuf);
    gl.bufferSubData(gl.ARRAY_BUFFER, 0, obj.hi);
    obj.hiDirty = false;
  };

  /* ---------------------------------------------------------------- camera */

  Renderer.prototype.upVector = function () {
    var u = [0, 0, 0];
    u[this.upAxis] = 1;
    return u;
  };

  Renderer.prototype.eye = function () {
    var c = this.camera;
    var cp = Math.cos(c.pitch), sp = Math.sin(c.pitch);
    var cy = Math.cos(c.yaw), sy = Math.sin(c.yaw);
    var dir;
    if (this.upAxis === 1) dir = [cp * sy, sp, cp * cy];
    else if (this.upAxis === 2) dir = [cp * cy, cp * sy, sp];
    else dir = [sp, cp * cy, cp * sy];
    return [
      c.target[0] + dir[0] * c.distance,
      c.target[1] + dir[1] * c.distance,
      c.target[2] + dir[2] * c.distance
    ];
  };

  Renderer.prototype.viewProjection = function (width, height) {
    var c = this.camera;
    var eye = this.eye();
    var view = M4.lookAt(eye, c.target, this.upVector());
    var span = Math.max(c.distance, 1e-6);
    var near = span * 0.002, far = span * 60;
    var proj;
    if (this.perspective) {
      proj = M4.perspective(c.fov * Math.PI / 180, width / height, near, far);
    } else {
      var h = span * Math.tan(c.fov * Math.PI / 360);
      var w = h * width / height;
      proj = M4.ortho(-w, w, -h, h, -far, far);
    }
    return { mvp: M4.multiply(proj, view), eye: eye, view: view, proj: proj };
  };

  Renderer.prototype.fit = function (bounds, margin) {
    if (!bounds) return;
    margin = margin || 1.25;
    var c = this.camera;
    c.target = [
      (bounds.min[0] + bounds.max[0]) / 2,
      (bounds.min[1] + bounds.max[1]) / 2,
      (bounds.min[2] + bounds.max[2]) / 2
    ];
    var dx = bounds.max[0] - bounds.min[0];
    var dy = bounds.max[1] - bounds.min[1];
    var dz = bounds.max[2] - bounds.min[2];
    var radius = Math.max(Math.sqrt(dx * dx + dy * dy + dz * dz) / 2, 1e-6);
    c.distance = radius / Math.tan(c.fov * Math.PI / 360) * margin;
  };

  Renderer.prototype.setView = function (name) {
    var c = this.camera;
    var v = {
      iso: [0.9, 0.45], front: [0, 0], back: [Math.PI, 0],
      right: [Math.PI / 2, 0], left: [-Math.PI / 2, 0],
      top: [0, Math.PI / 2 - 0.001], bottom: [0, -Math.PI / 2 + 0.001]
    }[name];
    if (v) { c.yaw = v[0]; c.pitch = v[1]; }
  };

  /* ---------------------------------------------------------------- render */

  Renderer.prototype.resize = function () {
    var canvas = this.canvas;
    var dpr = Math.min(global.devicePixelRatio || 1, 2);
    var w = Math.max(1, Math.floor(canvas.clientWidth * dpr));
    var h = Math.max(1, Math.floor(canvas.clientHeight * dpr));
    if (canvas.width !== w || canvas.height !== h) {
      canvas.width = w;
      canvas.height = h;
    }
    return { w: w, h: h };
  };

  Renderer.prototype.render = function () {
    var gl = this.gl;
    var size = this.resize();
    var vp = this.viewProjection(size.w, size.h);

    gl.viewport(0, 0, size.w, size.h);
    gl.clearColor(this.background[0], this.background[1], this.background[2], 1);
    gl.clear(gl.COLOR_BUFFER_BIT | gl.DEPTH_BUFFER_BIT);

    var eye = vp.eye, c = this.camera;
    var ed = [c.target[0] - eye[0], c.target[1] - eye[1], c.target[2] - eye[2]];
    var el = Math.sqrt(ed[0] * ed[0] + ed[1] * ed[1] + ed[2] * ed[2]) || 1;
    ed = [ed[0] / el, ed[1] / el, ed[2] / el];

    this._drawScene(vp.mvp, ed);
    if (this.showTriad) this._drawTriad(size);
  };

  Renderer.prototype._drawScene = function (mvp, eyeDir) {
    var gl = this.gl, self = this;
    var opaque = [], transparent = [];

    this.objects.forEach(function (o) {
      if (!o.visible) return;
      if (o.kind === 'surface' && o.opacity < 0.999) transparent.push(o);
      else opaque.push(o);
    });

    function drawSurfaces(list) {
      var p = self.progSurface;
      gl.useProgram(p.program);
      gl.uniformMatrix4fv(p.u.uMVP, false, mvp);
      gl.uniform3fv(p.u.uEyeDir, eyeDir);
      gl.enable(gl.POLYGON_OFFSET_FILL);
      gl.polygonOffset(1.0, 1.0);
      list.forEach(function (o) {
        if (o.kind !== 'surface' || o.triCount === 0) return;
        self._flushHighlight(o);
        gl.uniform3fv(p.u.uColor, o.color);
        gl.uniform3fv(p.u.uHiColor, o.hiColor);
        gl.uniform1f(p.u.uOpacity, o.opacity);
        gl.bindBuffer(gl.ARRAY_BUFFER, o.posBuf);
        gl.enableVertexAttribArray(p.a.aPos);
        gl.vertexAttribPointer(p.a.aPos, 3, gl.FLOAT, false, 0, 0);
        gl.bindBuffer(gl.ARRAY_BUFFER, o.norBuf);
        gl.enableVertexAttribArray(p.a.aNormal);
        gl.vertexAttribPointer(p.a.aNormal, 3, gl.FLOAT, false, 0, 0);
        gl.bindBuffer(gl.ARRAY_BUFFER, o.hiBuf);
        gl.enableVertexAttribArray(p.a.aHi);
        gl.vertexAttribPointer(p.a.aHi, 1, gl.FLOAT, false, 0, 0);
        gl.drawArrays(gl.TRIANGLES, 0, o.triCount * 3);
      });
      gl.disable(gl.POLYGON_OFFSET_FILL);
    }

    function drawFlat(list) {
      var p = self.progFlat;
      gl.useProgram(p.program);
      gl.uniformMatrix4fv(p.u.uMVP, false, mvp);
      list.forEach(function (o) {
        if (o.kind === 'surface' || o.count === 0) return;
        gl.uniform4fv(p.u.uColor, o.color.length === 4 ? o.color : o.color.concat([1]));
        gl.uniform1f(p.u.uPointSize, o.size || 1);
        gl.uniform1f(p.u.uRound, (o.kind === 'points' && o.round) ? 1 : 0);
        gl.bindBuffer(gl.ARRAY_BUFFER, o.posBuf);
        gl.enableVertexAttribArray(p.a.aPos);
        gl.vertexAttribPointer(p.a.aPos, 3, gl.FLOAT, false, 0, 0);
        gl.drawArrays(o.kind === 'points' ? gl.POINTS : gl.LINES, 0, o.count);
      });
    }

    drawSurfaces(opaque);
    drawFlat(opaque);
    if (transparent.length) {
      gl.depthMask(false);
      drawSurfaces(transparent);
      gl.depthMask(true);
    }
  };

  /* Orientation triad drawn into the lower-left corner. */
  Renderer.prototype._drawTriad = function (size) {
    var gl = this.gl;
    if (!this._triadBuf) {
      var L = 1;
      this._triadBuf = this.buffer(new Float32Array([
        0, 0, 0, L, 0, 0,
        0, 0, 0, 0, L, 0,
        0, 0, 0, 0, 0, L
      ]));
    }
    var dim = Math.round(Math.min(size.w, size.h) * 0.16);
    gl.viewport(6, 6, dim, dim);
    gl.disable(gl.DEPTH_TEST);

    var c = this.camera;
    var saved = c.target, savedDist = c.distance;
    c.target = [0, 0, 0];
    c.distance = 3.1;
    var vp = this.viewProjection(1, 1);
    c.target = saved;
    c.distance = savedDist;

    var p = this.progFlat;
    gl.useProgram(p.program);
    gl.uniformMatrix4fv(p.u.uMVP, false, vp.mvp);
    gl.uniform1f(p.u.uPointSize, 1);
    gl.uniform1f(p.u.uRound, 0);
    gl.bindBuffer(gl.ARRAY_BUFFER, this._triadBuf);
    gl.enableVertexAttribArray(p.a.aPos);
    gl.vertexAttribPointer(p.a.aPos, 3, gl.FLOAT, false, 0, 0);
    var cols = [[0.95, 0.35, 0.35, 1], [0.45, 0.85, 0.45, 1], [0.45, 0.6, 0.98, 1]];
    for (var i = 0; i < 3; i++) {
      gl.uniform4fv(p.u.uColor, cols[i]);
      gl.drawArrays(gl.LINES, i * 2, 2);
    }
    gl.enable(gl.DEPTH_TEST);
  };

  /* --------------------------------------------------------------- picking */

  /* Per-triangle ids are only built when the user first clicks. */
  Renderer.prototype._ensurePickBuffer = function (obj) {
    if (obj.idBuf || obj.kind !== 'surface') return;
    var gl = this.gl;
    var base = this._nextPickBase;
    this._nextPickBase += obj.triCount;
    this._pickRegistry.push({ base: base, obj: obj });
    obj.pickBase = base;

    var ids = new Float32Array(obj.triCount * 9);
    for (var t = 0; t < obj.triCount; t++) {
      var id = base + t;
      var r = (id & 255) / 255;
      var g = ((id >> 8) & 255) / 255;
      var b = ((id >> 16) & 255) / 255;
      for (var v = 0; v < 3; v++) {
        var o = (t * 3 + v) * 3;
        ids[o] = r; ids[o + 1] = g; ids[o + 2] = b;
      }
    }
    obj.idBuf = this.buffer(ids);
  };

  Renderer.prototype._ensurePickTarget = function (w, h) {
    var gl = this.gl;
    if (this._pickFB && this._pickSize[0] === w && this._pickSize[1] === h) return;
    if (this._pickFB) {
      gl.deleteFramebuffer(this._pickFB);
      gl.deleteTexture(this._pickTex);
      gl.deleteRenderbuffer(this._pickDepth);
    }
    this._pickFB = gl.createFramebuffer();
    this._pickTex = gl.createTexture();
    gl.bindTexture(gl.TEXTURE_2D, this._pickTex);
    gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, w, h, 0, gl.RGBA, gl.UNSIGNED_BYTE, null);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.NEAREST);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.NEAREST);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
    this._pickDepth = gl.createRenderbuffer();
    gl.bindRenderbuffer(gl.RENDERBUFFER, this._pickDepth);
    gl.renderbufferStorage(gl.RENDERBUFFER, gl.DEPTH_COMPONENT16, w, h);
    gl.bindFramebuffer(gl.FRAMEBUFFER, this._pickFB);
    gl.framebufferTexture2D(gl.FRAMEBUFFER, gl.COLOR_ATTACHMENT0, gl.TEXTURE_2D, this._pickTex, 0);
    gl.framebufferRenderbuffer(gl.FRAMEBUFFER, gl.DEPTH_ATTACHMENT, gl.RENDERBUFFER, this._pickDepth);
    gl.bindFramebuffer(gl.FRAMEBUFFER, null);
    this._pickSize = [w, h];
  };

  /*
   * Pick at CSS pixel coordinates relative to the canvas. Returns
   * { object, triangle, elementIndex, elementId, point } or null.
   */
  Renderer.prototype.pick = function (cssX, cssY) {
    var gl = this.gl, self = this;
    var size = this.resize();
    var dpr = size.w / this.canvas.clientWidth;
    var px = Math.round(cssX * dpr);
    var py = Math.round(size.h - cssY * dpr);
    if (px < 0 || py < 0 || px >= size.w || py >= size.h) return null;

    this.objects.forEach(function (o) {
      if (o.kind === 'surface' && o.visible && o.pickable) self._ensurePickBuffer(o);
    });

    this._ensurePickTarget(size.w, size.h);
    gl.bindFramebuffer(gl.FRAMEBUFFER, this._pickFB);
    gl.viewport(0, 0, size.w, size.h);
    gl.clearColor(0, 0, 0, 0);
    gl.clear(gl.COLOR_BUFFER_BIT | gl.DEPTH_BUFFER_BIT);
    gl.disable(gl.BLEND);

    var vp = this.viewProjection(size.w, size.h);
    var p = this.progPick;
    gl.useProgram(p.program);
    gl.uniformMatrix4fv(p.u.uMVP, false, vp.mvp);
    this.objects.forEach(function (o) {
      if (o.kind !== 'surface' || !o.visible || !o.pickable || !o.idBuf) return;
      gl.bindBuffer(gl.ARRAY_BUFFER, o.posBuf);
      gl.enableVertexAttribArray(p.a.aPos);
      gl.vertexAttribPointer(p.a.aPos, 3, gl.FLOAT, false, 0, 0);
      gl.bindBuffer(gl.ARRAY_BUFFER, o.idBuf);
      gl.enableVertexAttribArray(p.a.aId);
      gl.vertexAttribPointer(p.a.aId, 3, gl.FLOAT, false, 0, 0);
      gl.drawArrays(gl.TRIANGLES, 0, o.triCount * 3);
    });

    var pix = new Uint8Array(4);
    gl.readPixels(px, py, 1, 1, gl.RGBA, gl.UNSIGNED_BYTE, pix);
    gl.bindFramebuffer(gl.FRAMEBUFFER, null);
    gl.enable(gl.BLEND);

    var id = pix[0] | (pix[1] << 8) | (pix[2] << 16);
    if (!id) return null;

    for (var i = this._pickRegistry.length - 1; i >= 0; i--) {
      var reg = this._pickRegistry[i];
      if (id >= reg.base && id < reg.base + reg.obj.triCount) {
        var tri = id - reg.base;
        var ei = reg.obj.triElem ? reg.obj.triElem[tri] : -1;
        return {
          object: reg.obj,
          triangle: tri,
          elementIndex: ei,
          elementId: (reg.obj.elementIds && ei >= 0) ? reg.obj.elementIds[ei] : null
        };
      }
    }
    return null;
  };

  global.InpGL = { Renderer: Renderer, M4: M4 };
})(typeof window !== 'undefined' ? window : globalThis);
