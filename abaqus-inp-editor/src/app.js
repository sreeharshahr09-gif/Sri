/*
 * app.js -- application shell: file loading, the navigation tree, the panel
 * host, the 3-D viewport and export.
 */
(function (global) {
  'use strict';

  var U = global.InpUI;
  var el = U.el;
  var P = global.InpParser;
  var M = global.InpModel;
  var W = global.InpWriter;
  var Panels = global.InpPanels;
  var doc = global.document;

  var App = {
    model: null,
    fileName: null,
    originalText: null,
    viewer: null,
    currentPath: 'overview',
    dirtyCount: 0
  };

  /* ------------------------------------------------------------ bootstrap */

  function init() {
    App.dom = {
      tree: doc.getElementById('tree'),
      content: doc.getElementById('content'),
      canvas: doc.getElementById('gl'),
      fileName: doc.getElementById('file-name'),
      fileMeta: doc.getElementById('file-meta'),
      dirtyBadge: doc.getElementById('dirty-badge'),
      pickInfo: doc.getElementById('pick-info'),
      dropZone: doc.getElementById('drop-zone'),
      viewerPane: doc.getElementById('viewer-pane'),
      busy: doc.getElementById('busy'),
      treeSearch: doc.getElementById('tree-search')
    };

    try {
      App.viewer = new global.InpViewer.Viewer(App.dom.canvas);
      App.viewer.onPick = onPick;
    } catch (err) {
      App.dom.viewerPane.appendChild(el('.gl-error', {
        text: 'The 3-D viewport could not start: ' + err.message +
          ' Everything else in the editor still works.'
      }));
    }

    wireToolbar();
    wireDropTarget();
    wireViewerControls();
    wireSplitters();

    doc.getElementById('load-sample').addEventListener('click', function () {
      loadText(global.DEMO_INP, 'demo-plate.inp');
    });
  }

  function wireToolbar() {
    var input = doc.getElementById('file-input');
    doc.getElementById('open-btn').addEventListener('click', function () { input.click(); });
    input.addEventListener('change', function () {
      if (input.files && input.files[0]) readFile(input.files[0]);
      input.value = '';
    });
    doc.getElementById('export-btn').addEventListener('click', exportInp);

    doc.addEventListener('keydown', function (e) {
      if (!(e.ctrlKey || e.metaKey)) return;
      if (e.key === 'o') { e.preventDefault(); input.click(); }
      if (e.key === 's' || e.key === 'e') { e.preventDefault(); exportInp(); }
    });

    App.dom.treeSearch.addEventListener('input', function () {
      filterTree(App.dom.treeSearch.value.trim().toLowerCase());
    });
  }

  function wireDropTarget() {
    var zone = doc.body;
    ['dragenter', 'dragover'].forEach(function (evt) {
      zone.addEventListener(evt, function (e) {
        e.preventDefault();
        doc.body.classList.add('dragging');
      });
    });
    ['dragleave', 'drop'].forEach(function (evt) {
      zone.addEventListener(evt, function (e) {
        e.preventDefault();
        if (evt === 'dragleave' && e.relatedTarget) return;
        doc.body.classList.remove('dragging');
      });
    });
    zone.addEventListener('drop', function (e) {
      var f = e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files[0];
      if (f) readFile(f);
    });
  }

  function readFile(file) {
    busy('Reading ' + file.name + '…');
    var reader = new global.FileReader();
    reader.onload = function () { loadText(String(reader.result), file.name); };
    reader.onerror = function () {
      busy(false);
      U.toast('Could not read that file.', 'error');
    };
    reader.readAsText(file);
  }

  function busy(message) {
    var b = App.dom.busy;
    if (message === false) { b.classList.remove('on'); return; }
    b.textContent = message;
    b.classList.add('on');
  }

  /*
   * Parsing a large file blocks the main thread, so the busy overlay is given
   * two frames to paint before the work starts.
   */
  function loadText(text, name) {
    busy('Parsing ' + name + '…');
    global.requestAnimationFrame(function () {
      global.requestAnimationFrame(function () {
        try {
          var t0 = global.performance.now();
          App.originalText = text;
          App.fileName = name;
          App.model = M.build(P.parse(text));
          App.dirtyCount = 0;
          var t1 = global.performance.now();

          doc.body.classList.add('has-model');
          App.dom.dropZone.style.display = 'none';

          busy('Building the display mesh…');
          global.requestAnimationFrame(function () {
            if (App.viewer) App.viewer.setModel(App.model);
            var t2 = global.performance.now();
            buildTree();
            selectPath('overview');
            refreshHeader();
            populateVisibilityList();
            busy(false);
            U.toast('Loaded ' + name + ' — parsed in ' + Math.round(t1 - t0) +
              ' ms, drawn in ' + Math.round(t2 - t1) + ' ms');
          });
        } catch (err) {
          busy(false);
          U.toast('Failed to read that file: ' + err.message, 'error');
          if (global.console) global.console.error(err);
        }
      });
    });
  }

  /* ----------------------------------------------------------------- tree */

  function buildTree() {
    var model = App.model;
    var tree = U.clear(App.dom.tree);

    function node(label, path, opts) {
      opts = opts || {};
      var b = el('button.tree-item', {
        'data-path': path,
        'data-label': label.toLowerCase(),
        onclick: function () { selectPath(path); }
      }, [
        el('span.tree-label', { text: label }),
        opts.count !== undefined ? el('span.tree-count', { text: U.fmt(opts.count) }) : null
      ]);
      if (opts.depth) b.classList.add('depth-' + opts.depth);
      return b;
    }

    function group(label, children, defaultOpen) {
      if (!children.length) return null;
      var body = el('.tree-group-body', null, children);
      var head = el('button.tree-group', { text: label });
      var g = el('.tree-group-wrap', null, [head, body]);
      if (!defaultOpen) g.classList.add('collapsed');
      head.addEventListener('click', function () { g.classList.toggle('collapsed'); });
      return g;
    }

    tree.appendChild(node('Overview', 'overview'));

    var partNodes = model.parts.map(function (p) {
      return node(p.name, 'part:' + p.name, { count: p.elements.count, depth: 1 });
    });
    var g1 = group('Parts (' + model.parts.length + ')', partNodes, true);
    if (g1) tree.appendChild(g1);

    if (model.assembly) {
      var asmChildren = [node('Assembly root', 'assembly', { depth: 1 })];
      model.assembly.instances.forEach(function (inst) {
        asmChildren.push(node(inst.name, 'instance:' + inst.name, { depth: 1 }));
      });
      var g2 = group('Assembly (' + model.assembly.instances.length + ' instances)', asmChildren, true);
      if (g2) tree.appendChild(g2);
    }

    var modelChildren = [];
    function maybe(label, path, count) {
      if (count) modelChildren.push(node(label, path, { count: count, depth: 1 }));
    }
    maybe('Materials', 'materials', model.materials.length);
    maybe('Interaction properties', 'interactionProperties', model.interactionProperties.length);
    maybe('Interactions', 'interactions', model.interactions.length);
    maybe('Constraints', 'constraints', model.constraints.length);
    maybe('Amplitudes', 'amplitudes', model.amplitudes.length);
    maybe('Initial conditions', 'initial', model.initial.length);
    maybe('Boundary conditions', 'boundary', model.boundary.length);
    maybe('Model output', 'output', model.output.length);
    maybe('Other cards', 'other', model.unrecognised.length + model.misc.length);
    var g3 = group('Model definition', modelChildren, true);
    if (g3) tree.appendChild(g3);

    var stepNodes = model.steps.map(function (s, i) {
      return node(s.name, 'step:' + i, { depth: 1 });
    });
    var g4 = group('Steps (' + model.steps.length + ')', stepNodes, true);
    if (g4) tree.appendChild(g4);

    tree.appendChild(node('Full file text', 'rawfile'));
  }

  function filterTree(q) {
    var items = App.dom.tree.querySelectorAll('.tree-item');
    Array.prototype.forEach.call(items, function (it) {
      var hit = !q || it.getAttribute('data-label').indexOf(q) !== -1;
      it.style.display = hit ? '' : 'none';
    });
    if (q) {
      Array.prototype.forEach.call(App.dom.tree.querySelectorAll('.tree-group-wrap'), function (g) {
        g.classList.remove('collapsed');
      });
    }
  }

  /* --------------------------------------------------------------- panels */

  var ctx = {
    get model() { return App.model; },
    get viewer() { return App.viewer; },
    get fileName() { return App.fileName; },

    markDirty: function () {
      App.dirtyCount = W.changeSummary(App.model).length;
      refreshHeader();
    },
    refreshHeader: refreshHeader,
    refresh: function () { selectPath(App.currentPath); },
    rebuild: function () {
      App.model = M.build({ blocks: App.model.blocks, trailing: App.model.trailing });
      buildTree();
      selectPath(App.currentPath);
      refreshHeader();
    },
    rebuildGeometry: function () {
      if (!App.viewer) return;
      busy('Rebuilding the view…');
      global.requestAnimationFrame(function () {
        App.model = M.build({ blocks: App.model.blocks, trailing: App.model.trailing });
        App.viewer.setModel(App.model);
        populateVisibilityList();
        busy(false);
      });
    },
    onDataEdited: function (block) {
      /* Coordinate and connectivity edits change the picture. */
      if (block.key === 'NODE' || block.key === 'ELEMENT') markGeometryStale();
    },
    selectPath: selectPath,
    isolateSource: isolateSource,
    fitInstance: fitInstance,
    highlightSetBlock: highlightSetBlock,
    deleteBlocks: deleteBlocks,
    addMaterial: addMaterial,
    addBehaviour: addBehaviour,
    addStep: addStep,
    addCard: addCard
  };

  function selectPath(path) {
    if (!App.model) return;
    App.currentPath = path;
    Array.prototype.forEach.call(App.dom.tree.querySelectorAll('.tree-item'), function (it) {
      it.classList.toggle('active', it.getAttribute('data-path') === path);
    });

    var host = U.clear(App.dom.content);
    var model = App.model;
    var panel;

    if (path === 'overview') panel = Panels.overviewPanel(ctx);
    else if (path === 'assembly') panel = Panels.assemblyPanel(ctx);
    else if (path === 'materials') panel = Panels.materialsPanel(ctx);
    else if (path === 'interactionProperties') panel = Panels.interactionPropsPanel(ctx);
    else if (path === 'interactions') panel = Panels.blockListPanel(ctx, 'Interactions', 'Contact pairs, ties and general contact', model.interactions, 'Contact Pair');
    else if (path === 'constraints') panel = Panels.blockListPanel(ctx, 'Constraints', 'Couplings, equations and rigid bodies', model.constraints);
    else if (path === 'amplitudes') panel = Panels.blockListPanel(ctx, 'Amplitudes', 'Time-varying load multipliers', model.amplitudes, 'Amplitude');
    else if (path === 'initial') panel = Panels.blockListPanel(ctx, 'Initial conditions', 'Model state before the first step', model.initial);
    else if (path === 'boundary') panel = Panels.blockListPanel(ctx, 'Boundary conditions', 'Applied before the first step', model.boundary, 'Boundary');
    else if (path === 'output') panel = Panels.blockListPanel(ctx, 'Model output', 'Requests declared outside any step', model.output.map(function (o) { return o.block; }));
    else if (path === 'other') panel = Panels.blockListPanel(ctx, 'Other cards', 'Keywords without a dedicated view. They stay fully editable and are exported unchanged.', model.unrecognised.concat(model.misc));
    else if (path === 'steps') panel = Panels.stepsPanel(ctx);
    else if (path === 'rawfile') panel = rawFilePanel();
    else if (path.indexOf('part:') === 0) {
      var pn = path.substring(5);
      var part = model.parts.filter(function (p) { return p.name === pn; })[0];
      panel = part ? Panels.partPanel(ctx, part) : missing(pn);
    } else if (path.indexOf('instance:') === 0) {
      var iname = path.substring(9);
      var inst = model.assembly && model.assembly.instances.filter(function (x) { return x.name === iname; })[0];
      panel = inst ? Panels.instancePanel(ctx, inst) : missing(iname);
    } else if (path.indexOf('step:') === 0) {
      var si = parseInt(path.substring(5), 10);
      panel = model.steps[si] ? Panels.stepPanel(ctx, model.steps[si], si) : missing(path);
    } else {
      panel = Panels.overviewPanel(ctx);
    }

    host.appendChild(panel);
    host.scrollTop = 0;
  }

  function missing(name) {
    return el('.panel', null, el('.empty-note', { text: 'Nothing found for "' + name + '".' }));
  }

  function rawFilePanel() {
    var wrap = el('.panel');
    var text = W.write(App.model);
    wrap.appendChild(U.sectionHeader('Full file text',
      U.fmt(text.split('\n').length) + ' lines as they would be exported right now', [
        {
          label: 'Copy', onClick: function () { copyText(text); }
        },
        { label: 'Export', primary: true, onClick: exportInp }
      ]));
    wrap.appendChild(el('p.card-hint', {
      text: 'This is generated from the current state of the model. Cards you have not edited are reproduced byte for byte from the file you opened.'
    }));
    /* Only the head of a large file is shown; the whole thing still exports. */
    var lines = text.split('\n');
    var cap = 4000;
    var shown = lines.length > cap ? lines.slice(0, cap).join('\n') +
      '\n\n… ' + U.fmt(lines.length - cap) + ' more lines not shown …' : text;
    wrap.appendChild(el('pre.raw-file', { text: shown }));
    return wrap;
  }

  /* ----------------------------------------------------- structural edits */

  function insertBlockAfter(reference, block) {
    var blocks = App.model.blocks;
    var i = reference ? blocks.indexOf(reference) : blocks.length - 1;
    blocks.splice(i + 1, 0, block);
  }

  function deleteBlocks(list) {
    var names = list.map(function (b) { return '*' + b.keyword; }).join(', ');
    U.modal('Delete ' + list.length + ' card(s)?',
      el('p', { text: 'This removes ' + names + ' from the model. The change only reaches disk when you export.' }),
      [
        { label: 'Cancel', onClick: function (close) { close(); } },
        {
          label: 'Delete', primary: true, onClick: function (close) {
            var blocks = App.model.blocks;
            list.forEach(function (b) {
              var i = blocks.indexOf(b);
              if (i >= 0) blocks.splice(i, 1);
            });
            close();
            App.dirtyCount++;
            ctx.rebuild();
            markGeometryStale();
            U.toast('Deleted ' + list.length + ' card(s)');
          }
        }
      ]);
  }

  function addMaterial() {
    promptName('New material', 'Material name', 'Material-' + (App.model.materials.length + 1),
      function (name) {
        var mat = P.makeBlock('Material', [{ key: 'name', value: name, flag: false }], []);
        mat.comments = ['** '];
        var elastic = P.makeBlock('Elastic', [], ['210000., 0.3']);
        var last = App.model.materials.length
          ? lastBlockOf(App.model.materials[App.model.materials.length - 1])
          : null;
        insertBlockAfter(last, mat);
        insertBlockAfter(mat, elastic);
        App.dirtyCount++;
        ctx.rebuild();
        selectPath('materials');
        U.toast('Added material "' + name + '"');
      });
  }

  function lastBlockOf(mat) {
    return mat.subs.length ? mat.subs[mat.subs.length - 1] : mat.block;
  }

  function addBehaviour(mat) {
    promptChoice('Add material behaviour', [
      'Density', 'Elastic', 'Plastic', 'Hyperelastic', 'Viscoelastic',
      'Expansion', 'Conductivity', 'Specific Heat', 'Damping', 'Depvar'
    ], function (kw) {
      var b = P.makeBlock(kw, [], []);
      insertBlockAfter(lastBlockOf(mat), b);
      App.dirtyCount++;
      ctx.rebuild();
      selectPath('materials');
      U.toast('Added *' + kw);
    });
  }

  function addStep() {
    promptName('New step', 'Step name', 'Step-' + (App.model.steps.length + 1), function (name) {
      var step = P.makeBlock('Step', [
        { key: 'name', value: name, flag: false },
        { key: 'nlgeom', value: 'YES', flag: false }
      ], []);
      step.comments = ['** ', '** STEP: ' + name, '** '];
      var stat = P.makeBlock('Static', [], ['0.1, 1., 1e-05, 1.']);
      var end = P.makeBlock('End Step', [], []);
      var ref = App.model.steps.length
        ? App.model.steps[App.model.steps.length - 1].endBlock
        : null;
      if (!ref) ref = App.model.blocks[App.model.blocks.length - 1];
      insertBlockAfter(ref, step);
      insertBlockAfter(step, stat);
      insertBlockAfter(stat, end);
      App.dirtyCount++;
      ctx.rebuild();
      selectPath('step:' + (App.model.steps.length - 1));
      U.toast('Added step "' + name + '"');
    });
  }

  /* Insert a new card, either at the end of a step or at model level. */
  function addCard(keyword, step) {
    var b = P.makeBlock(keyword, [], []);
    if (step) {
      var ref = step.endBlock || step.block;
      var blocks = App.model.blocks;
      var i = blocks.indexOf(ref);
      blocks.splice(Math.max(0, i), 0, b);
    } else {
      var anchor = App.model.assembly ? App.model.assembly.endBlock : null;
      insertBlockAfter(anchor || App.model.blocks[App.model.blocks.length - 1], b);
    }
    App.dirtyCount++;
    ctx.rebuild();
    U.toast('Added *' + keyword + ' — fill in its parameters and data');
  }

  function promptName(title, label, initial, done) {
    var input = el('input.text-input', { value: initial, spellcheck: 'false' });
    var m = U.modal(title, el('label.field', null, [el('span', { text: label }), input]), [
      { label: 'Cancel', onClick: function (close) { close(); } },
      {
        label: 'Add', primary: true, onClick: function (close) {
          var v = input.value.trim();
          if (!v) return;
          close();
          done(v);
        }
      }
    ]);
    input.focus();
    input.select();
    input.addEventListener('keydown', function (e) {
      if (e.key === 'Enter') {
        var v = input.value.trim();
        if (v) { m.close(); done(v); }
      }
    });
  }

  function promptChoice(title, options, done) {
    var list = el('.choice-list', null, options.map(function (o) {
      return el('button.choice', {
        text: '*' + o,
        onclick: function () { m.close(); done(o); }
      });
    }));
    var m = U.modal(title, list, [{ label: 'Cancel', onClick: function (close) { close(); } }]);
  }

  /* --------------------------------------------------------------- viewer */

  var geometryStale = false;

  function markGeometryStale() {
    if (geometryStale) return;
    geometryStale = true;
    var bar = doc.getElementById('stale-bar');
    bar.classList.add('on');
  }

  function clearGeometryStale() {
    geometryStale = false;
    doc.getElementById('stale-bar').classList.remove('on');
  }

  function wireViewerControls() {
    if (!App.viewer) return;
    var v = App.viewer;

    function bindToggle(id, apply) {
      var elm = doc.getElementById(id);
      if (!elm) return;
      elm.addEventListener('change', function () {
        apply(elm.type === 'checkbox' ? elm.checked : elm.value);
        v.applyDisplay();
      });
    }

    bindToggle('opt-shaded', function (on) { v.display.shaded = on; });
    bindToggle('opt-nodes', function (on) { v.display.nodes = on; });
    bindToggle('opt-analytical', function (on) { v.display.analytical = on; });
    bindToggle('opt-perspective', function (on) { v.display.perspective = on; });
    bindToggle('opt-edges', function (val) { v.display.edges = val; });
    bindToggle('opt-up', function (val) { v.display.upAxis = parseInt(val, 10); });
    bindToggle('opt-opacity', function (val) { v.display.opacity = parseFloat(val); });

    Array.prototype.forEach.call(doc.querySelectorAll('[data-view]'), function (b) {
      b.addEventListener('click', function () {
        v.setView(b.getAttribute('data-view'));
      });
    });
    doc.getElementById('fit-btn').addEventListener('click', function () { v.fitAll(); });
    doc.getElementById('clear-sel-btn').addEventListener('click', function () {
      v.highlight(null);
      setPickInfo(null);
    });
    doc.getElementById('rebuild-btn').addEventListener('click', function () {
      clearGeometryStale();
      ctx.rebuildGeometry();
    });
  }

  function populateVisibilityList() {
    var host = doc.getElementById('vis-list');
    if (!host || !App.viewer) return;
    U.clear(host);
    App.viewer.sourceList().forEach(function (s) {
      var cb = el('input', { type: 'checkbox', checked: !s.hidden });
      cb.checked = !s.hidden;
      cb.addEventListener('change', function () {
        App.viewer.setSourceVisible(s.key, cb.checked);
      });
      var swatch = el('span.swatch');
      swatch.style.background = 'rgb(' + s.color.map(function (c) {
        return Math.round(c * 255);
      }).join(',') + ')';
      host.appendChild(el('label.vis-row', null, [cb, swatch, el('span', { text: s.label })]));
    });
  }

  function isolateSource(kind, obj) {
    if (!App.viewer) return;
    var wantKey = kind === 'part' ? 'part:' + obj.name : 'inst:' + obj.name;
    App.viewer.sourceList().forEach(function (s) {
      var keep = s.key === wantKey || s.key.indexOf(wantKey) === 0;
      App.viewer.setSourceVisible(s.key, keep);
    });
    populateVisibilityList();
    U.toast('Isolated ' + obj.name + ' in the viewport');
  }

  function fitInstance(inst) {
    if (!App.viewer) return;
    var bounds = null;
    App.viewer.meshes.forEach(function (m) {
      if (m.meta && m.meta.instance === inst && m.bounds) {
        bounds = global.InpGeometry.mergeBounds(bounds, m.bounds);
      }
    });
    if (bounds) App.viewer.fitTo(bounds);
    else U.toast('That instance has no drawable geometry.');
  }

  function highlightSetBlock(block, which, owner) {
    if (!App.viewer) return;
    var name = P.getParam(block, which);
    if (!name) { U.toast('That set has no name.'); return; }

    var ids = (which === 'nset' ? owner.nsetIndex : owner.elsetIndex)[name.toUpperCase()] || [];
    var selection = {
      kind: which === 'nset' ? 'nset' : 'elset',
      ids: ids,
      instance: owner.type === 'instance' ? owner : null,
      part: owner.type === 'part' ? owner : null
    };

    /* An assembly-level set names its instance as a parameter. */
    if (owner.type === 'assembly') {
      var iname = P.getParam(block, 'instance');
      if (iname) {
        selection.instance = owner.instances.filter(function (i) {
          return i.name.toUpperCase() === iname.toUpperCase();
        })[0] || null;
      }
    }

    var found = App.viewer.highlight(selection);
    if (found) {
      U.toast('Highlighted ' + U.fmt(found) + ' ' +
        (which === 'nset' ? 'nodes' : 'elements') + ' of "' + name + '"');
    } else {
      U.toast('Set "' + name + '" has ' + U.fmt(ids.length) +
        ' members but none map onto drawn geometry.', 'error');
    }
  }

  function onPick(info) {
    setPickInfo(info);
  }

  function setPickInfo(info) {
    var host = U.clear(App.dom.pickInfo);
    if (!info) {
      host.appendChild(el('span.hint', { text: 'Click geometry to inspect an element.' }));
      return;
    }
    var src = info.source || {};
    host.appendChild(el('span.pick-item', null, [
      el('b', { text: 'Element ' }), String(info.elementId)
    ]));
    if (info.elementType) {
      host.appendChild(el('span.pick-item', null, [el('b', { text: 'Type ' }), info.elementType]));
    }
    host.appendChild(el('span.pick-item', null, [el('b', { text: 'In ' }), src.label || '—']));
    if (info.connectivity) {
      host.appendChild(el('span.pick-item', null, [
        el('b', { text: 'Nodes ' }),
        info.connectivity.slice(0, 10).join(', ') + (info.connectivity.length > 10 ? '…' : '')
      ]));
    }
    host.appendChild(el('button.link-btn', {
      text: 'Open in editor',
      onclick: function () { revealElement(info); }
    }));
  }

  /* Jump from a picked element to its row in the connectivity grid. */
  function revealElement(info) {
    var src = info.source;
    if (!src) return;
    var owner = src.instance && src.kind === 'instance-own' ? src.instance : (src.part || src.instance);
    if (src.part && src.kind === 'instance-part') owner = src.part;

    var path;
    if (owner && owner.type === 'part') path = 'part:' + owner.name;
    else if (owner && owner.type === 'instance') path = 'instance:' + owner.name;
    else path = 'assembly';
    selectPath(path);

    global.requestAnimationFrame(function () {
      var container = owner && owner.container;
      if (!container) return;
      for (var i = 0; i < container.elementBlocks.length; i++) {
        var b = container.elementBlocks[i];
        var rows = P.dataRows(b);
        for (var r = 0; r < rows.length; r++) {
          if (rows[r].fields && parseInt(rows[r].fields[0], 10) === info.elementId) {
            var card = doc.getElementById('card-' + b.id);
            if (card) {
              var tab = card.querySelector('[data-tab="data"]');
              if (tab) tab.click();
              card.scrollIntoView({ block: 'center' });
            }
            if (b._table) {
              b._table.setFilter(null);
              global.requestAnimationFrame(function () {
                b._table.scrollToRow(r);
                var cell = b._table.body.querySelector('[data-row="' + r + '"]');
                if (cell) cell.classList.add('flash');
              });
            }
            return;
          }
        }
      }
    });
  }

  /* --------------------------------------------------------------- export */

  function exportInp() {
    if (!App.model) { U.toast('Open an .inp file first.'); return; }
    var text = W.write(App.model);
    var changes = W.changeSummary(App.model);
    var name = (App.fileName || 'model.inp').replace(/\.inp$/i, '') + '-edited.inp';

    var summary = el('.export-summary');
    summary.appendChild(el('p', {
      text: changes.length
        ? changes.length + ' card(s) changed. Everything else is reproduced exactly as it was read.'
        : 'No cards were edited — this export reproduces the file you opened byte for byte.'
    }));
    if (changes.length) {
      summary.appendChild(el('ul.change-list', null, changes.slice(0, 40).map(function (c) {
        return el('li', { text: c.label + (c.added ? '  (new)' : '') });
      })));
      if (changes.length > 40) {
        summary.appendChild(el('p.card-hint', { text: '…and ' + (changes.length - 40) + ' more.' }));
      }
    }

    var nameInput = el('input.text-input', { value: name, spellcheck: 'false' });
    summary.appendChild(el('label.field', null, [
      el('span', { text: 'File name' }), nameInput
    ]));
    summary.appendChild(el('p.card-hint', {
      text: U.fmt(text.split('\n').length) + ' lines, ' + U.fmt(Math.round(text.length / 1024)) + ' KB.'
    }));

    U.modal('Export INP', summary, [
      { label: 'Close', onClick: function (close) { close(); } },
      {
        label: 'Copy to clipboard', onClick: function () { copyText(text); }
      },
      {
        label: 'Download .inp', primary: true, onClick: function (close) {
          download(text, nameInput.value.trim() || name);
          close();
        }
      }
    ]);
  }

  function download(text, name) {
    try {
      var blob = new global.Blob([text], { type: 'text/plain;charset=utf-8' });
      var url = global.URL.createObjectURL(blob);
      var a = doc.createElement('a');
      a.href = url;
      a.download = name;
      doc.body.appendChild(a);
      a.click();
      doc.body.removeChild(a);
      global.setTimeout(function () { global.URL.revokeObjectURL(url); }, 4000);
      U.toast('Exported ' + name);
    } catch (err) {
      U.toast('Download was blocked here — use "Copy to clipboard" instead.', 'error');
    }
  }

  function copyText(text) {
    if (global.navigator.clipboard && global.navigator.clipboard.writeText) {
      global.navigator.clipboard.writeText(text).then(function () {
        U.toast('Copied ' + U.fmt(text.length) + ' characters to the clipboard');
      }, function () { fallbackCopy(text); });
    } else fallbackCopy(text);
  }

  function fallbackCopy(text) {
    var ta = doc.createElement('textarea');
    ta.value = text;
    ta.style.position = 'fixed';
    ta.style.opacity = '0';
    doc.body.appendChild(ta);
    ta.select();
    try {
      doc.execCommand('copy');
      U.toast('Copied to the clipboard');
    } catch (e) {
      U.toast('Could not copy automatically.', 'error');
    }
    doc.body.removeChild(ta);
  }

  function refreshHeader() {
    if (!App.model) return;
    var s = M.stats(App.model);
    App.dom.fileName.textContent = App.fileName || 'untitled.inp';
    App.dom.fileMeta.textContent = U.fmt(s.nodes) + ' nodes · ' + U.fmt(s.elements) +
      ' elements · ' + U.fmt(s.blocks) + ' cards';
    var n = W.changeSummary(App.model).length;
    App.dirtyCount = n;
    App.dom.dirtyBadge.textContent = n ? n + ' edited' : 'unchanged';
    App.dom.dirtyBadge.classList.toggle('on', n > 0);
  }

  /* ------------------------------------------------------------ splitters */

  function wireSplitters() {
    Array.prototype.forEach.call(doc.querySelectorAll('.splitter'), function (sp) {
      var targetId = sp.getAttribute('data-target');
      var target = doc.getElementById(targetId);
      var dir = sp.getAttribute('data-dir') || 'left';
      var dragging = false, startX = 0, startW = 0;

      sp.addEventListener('pointerdown', function (e) {
        dragging = true;
        startX = e.clientX;
        startW = target.getBoundingClientRect().width;
        sp.setPointerCapture(e.pointerId);
        doc.body.classList.add('resizing');
      });
      sp.addEventListener('pointermove', function (e) {
        if (!dragging) return;
        var dx = e.clientX - startX;
        var w = dir === 'left' ? startW + dx : startW - dx;
        target.style.width = Math.max(180, Math.min(920, w)) + 'px';
        if (App.viewer) App.viewer.invalidate();
      });
      sp.addEventListener('pointerup', function () {
        dragging = false;
        doc.body.classList.remove('resizing');
      });
    });

    global.addEventListener('resize', function () {
      if (App.viewer) App.viewer.invalidate();
    });
  }

  if (doc.readyState === 'loading') {
    doc.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }

  global.InpApp = App;
})(typeof window !== 'undefined' ? window : globalThis);
