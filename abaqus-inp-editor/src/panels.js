/*
 * panels.js -- the editable views for each part of the model.
 *
 * Every panel edits the underlying blocks directly, so a change made here is
 * immediately part of what the exporter writes. Panels fall back to a generic
 * keyword-card editor for anything the file contains that is not specially
 * handled, which is what keeps unusual or unsupported cards editable and
 * exportable rather than silently dropped.
 */
(function (global) {
  'use strict';

  var U = global.InpUI;
  var el = U.el;
  var P = global.InpParser;
  var M = global.InpModel;
  var W = global.InpWriter;

  /* Short explanations shown under a card so the file reads as a model. */
  var KEYWORD_DOC = {
    HEADING: 'Free-text title for the analysis, echoed in the output database.',
    PREPRINT: 'Controls which tables Abaqus prints to the .dat file.',
    PART: 'A reusable meshed body. Parts are positioned by instances in the assembly.',
    NODE: 'Nodal coordinates: node id, x, y, z.',
    ELEMENT: 'Element connectivity: element id followed by its node ids.',
    NSET: 'A named group of nodes, referenced by boundary conditions, loads and output requests.',
    ELSET: 'A named group of elements, referenced by sections, surfaces and output requests.',
    SOLIDSECTION: 'Assigns a material to a set of continuum elements.',
    SHELLSECTION: 'Assigns a material and thickness to a set of shell elements.',
    BEAMSECTION: 'Assigns a profile and material to a set of beam elements.',
    SURFACE: 'A named surface built from element faces, nodes, or an analytical profile.',
    ASSEMBLY: 'Container positioning part instances into the analysis model.',
    INSTANCE: 'One placement of a part: an optional translation, then a rotation.',
    RIGIDBODY: 'Ties a node set or analytical surface to a reference node.',
    MATERIAL: 'A named material definition; the cards below it are its behaviours.',
    HYPERELASTIC: 'Nonlinear elastic model for rubber-like materials.',
    UNIAXIALTESTDATA: 'Nominal stress / nominal strain pairs used to calibrate the model.',
    ELASTIC: 'Linear elasticity: Young’s modulus and Poisson’s ratio.',
    PLASTIC: 'Yield stress against plastic strain.',
    DENSITY: 'Mass density, required for dynamics and gravity loads.',
    SURFACEINTERACTION: 'A named contact property; friction and pressure-overclosure go below it.',
    FRICTION: 'Tangential behaviour. The data line holds the friction coefficient.',
    SURFACEBEHAVIOR: 'Normal behaviour, typically HARD pressure-overclosure.',
    CONTACTPAIR: 'Pairs a slave and a master surface using an interaction property.',
    TIE: 'Bonds two surfaces so they move together.',
    BOUNDARY: 'Prescribed displacements or rotations. Data: set, first dof, last dof, value.',
    CLOAD: 'Concentrated force or moment applied at a node set.',
    DLOAD: 'Distributed load applied to elements.',
    DSLOAD: 'Distributed load applied to a surface.',
    STEP: 'An analysis step. Loads and boundary conditions inside apply from here on.',
    STATIC: 'General static procedure. Data: initial increment, period, min, max.',
    RESTART: 'Controls restart data written during the step.',
    OUTPUT: 'Opens a field or history output request.',
    NODEOUTPUT: 'Nodal variables written for this request.',
    ELEMENTOUTPUT: 'Element variables written for this request.',
    CONTACTOUTPUT: 'Contact variables written for this request.',
    AMPLITUDE: 'Time-varying multiplier that loads and boundary conditions can reference.',
    INITIALCONDITIONS: 'Initial state such as stress, temperature or velocity.',
    EQUATION: 'Linear multi-point constraint between degrees of freedom.',
    COUPLING: 'Couples a surface to a reference node.',
    ORIENTATION: 'Local coordinate system for material or section definitions.'
  };

  function docFor(block) {
    return KEYWORD_DOC[block.key] || null;
  }

  /* ---------------------------------------------------------- data editor */

  function columnsFor(block, rows) {
    var maxFields = 1;
    for (var i = 0; i < rows.length; i++) {
      if (rows[i].fields && rows[i].fields.length > maxFields) maxFields = rows[i].fields.length;
    }
    if (block.key === 'NODE') {
      return [
        { label: 'Node', width: 90 }, { label: 'X', width: 130 },
        { label: 'Y', width: 130 }, { label: 'Z', width: 130 }
      ];
    }
    if (block.key === 'ELEMENT') {
      var cols = [{ label: 'Element', width: 90 }];
      for (var n = 1; n < maxFields; n++) cols.push({ label: 'n' + n, width: 82 });
      return cols;
    }
    if (block.key === 'NSET' || block.key === 'ELSET') {
      if (P.hasParam(block, 'generate')) {
        return [
          { label: 'First', width: 110 }, { label: 'Last', width: 110 },
          { label: 'Increment', width: 110 }
        ];
      }
      var scols = [];
      for (var s = 0; s < maxFields; s++) scols.push({ label: String(s + 1), width: 82 });
      return scols;
    }
    var g = [];
    for (var c = 0; c < maxFields; c++) g.push({ label: String(c + 1), width: 130, align: 'left' });
    return g;
  }

  /*
   * Editable grid over a block's data lines. Comment lines inside the data are
   * shown in place and left alone.
   */
  function dataGrid(ctx, block) {
    var rows = P.dataRows(block);
    if (!rows.length) {
      return el('.empty-note', { text: 'This card has no data lines.' });
    }
    var columns = columnsFor(block, rows);

    var table = new U.VirtualTable({
      columns: columns,
      rowCount: rows.length,
      rowHeight: 26,
      getRow: function (i) {
        var r = rows[i];
        if (!r) return null;
        if (r.comment) return [r.raw];
        return r.fields;
      },
      rowClass: function (i) {
        var r = rows[i];
        if (!r) return null;
        if (r.comment) return 'is-comment';
        return r.dirty ? 'is-dirty' : null;
      },
      editable: function (i) { return rows[i] && !rows[i].comment; },
      onEdit: function (i, col, value) {
        var r = rows[i];
        while (r.fields.length <= col) r.fields.push('');
        r.fields[col] = value;
        P.markRow(block, r);
        ctx.markDirty(block);
        ctx.onDataEdited(block);
      }
    });

    var height = Math.min(460, Math.max(120, rows.length * 26 + 4));
    table.viewport.style.height = height + 'px';

    var wrap = el('.data-editor');

    /* Row search, worth having once a block runs to thousands of lines. */
    if (rows.length > 30) {
      var search = el('input.search-inline', {
        type: 'search', placeholder: 'Filter rows (id or text)…', spellcheck: 'false'
      });
      var count = el('span.row-count', { text: U.fmt(rows.length) + ' rows' });
      search.addEventListener('input', function () {
        var q = search.value.trim().toLowerCase();
        if (!q) {
          table.setFilter(null);
          count.textContent = U.fmt(rows.length) + ' rows';
          return;
        }
        var keep = [];
        for (var i = 0; i < rows.length; i++) {
          var r = rows[i];
          var hay = r.comment ? r.raw : r.fields.join(',');
          if (hay.toLowerCase().indexOf(q) !== -1) keep.push(i);
        }
        table.setFilter(keep);
        count.textContent = U.fmt(keep.length) + ' of ' + U.fmt(rows.length) + ' rows';
      });
      wrap.appendChild(el('.grid-toolbar', null, [search, count]));
    }

    wrap.appendChild(table.root);
    /* The viewport needs a layout pass before it knows its height. */
    global.requestAnimationFrame(function () { table.render(); });
    block._table = table;
    return wrap;
  }

  /* Raw text view of a whole card, for anything the grid cannot express. */
  function rawEditor(ctx, block) {
    var text = W.blockLines(block).join('\n');
    var ta = el('textarea.raw-edit', { spellcheck: 'false', value: text });
    ta.value = text;
    var status = el('span.raw-status');
    var apply = el('button.btn.primary', {
      text: 'Apply raw text',
      onclick: function () {
        try {
          var parsed = P.parse(ta.value);
          if (!parsed.blocks.length) {
            status.textContent = 'Nothing to apply.';
            return;
          }
          if (parsed.blocks.length > 1) {
            status.textContent = 'Raw text must contain exactly one keyword card (found ' +
              parsed.blocks.length + ').';
            status.className = 'raw-status error';
            return;
          }
          var nb = parsed.blocks[0];
          block.keyword = nb.keyword;
          block.key = nb.key;
          block.params = nb.params;
          block.comments = nb.comments;
          block.data = nb.data;
          block._rows = null;
          block._rowsDirty = false;
          block.dirty = true;
          ctx.markDirty(block);
          ctx.rebuild();
          U.toast('Card updated from raw text');
        } catch (err) {
          status.textContent = String(err.message || err);
          status.className = 'raw-status error';
        }
      }
    });
    return el('.raw-wrap', null, [ta, el('.raw-actions', null, [apply, status])]);
  }

  /*
   * One keyword card: parameters, data and raw text on tabs.
   */
  function blockCard(ctx, block, opts) {
    opts = opts || {};
    var title = '*' + (block.keyword || '(data)');
    var name = P.getParam(block, 'name') || block._setName || '';

    var body = el('.card-body');
    var tabs = el('.card-tabs');
    var panes = {};

    function makeTab(key, label, node) {
      panes[key] = node;
      var b = el('button.card-tab', {
        text: label,
        'data-tab': key,
        onclick: function () { select(key); }
      });
      tabs.appendChild(b);
    }

    function select(key) {
      U.clear(body);
      body.appendChild(panes[key]);
      Array.prototype.forEach.call(tabs.children, function (b) {
        b.classList.toggle('active', b.getAttribute('data-tab') === key);
      });
      if (key === 'data' && block._table) {
        global.requestAnimationFrame(function () { block._table.render(); });
      }
    }

    var paramPane = el('.pane', null, [
      U.paramEditor(block, function (b) { ctx.markDirty(b); ctx.refreshHeader(); }),
      el('.card-hint', { text: 'Parameters are written back exactly as typed. Leave a value empty for a flag such as GENERATE or NLGEOM.' })
    ]);

    makeTab('params', 'Parameters (' + block.params.length + ')', paramPane);
    makeTab('data', 'Data (' + P.dataRows(block).length + ')', el('.pane', null, dataGrid(ctx, block)));
    makeTab('raw', 'Raw', el('.pane', null, rawEditor(ctx, block)));

    if (block.comments && block.comments.length) {
      var cta = el('textarea.raw-edit.comments', {
        spellcheck: 'false', value: block.comments.join('\n')
      });
      cta.value = block.comments.join('\n');
      cta.addEventListener('change', function () {
        block.comments = cta.value === '' ? [] : cta.value.split('\n');
        block.dirty = true;
        ctx.markDirty(block);
      });
      makeTab('comments', 'Comments', el('.pane', null, [
        el('.card-hint', { text: 'Lines preceding this card in the file. Abaqus ignores them; they are kept so the exported file stays readable.' }),
        cta
      ]));
    }

    var actions = el('.card-actions');
    if (opts.onDelete) {
      actions.appendChild(el('button.icon-btn.danger', {
        text: 'Delete', title: 'Remove this card from the model',
        onclick: function () { opts.onDelete(block); }
      }));
    }
    if (opts.extraActions) {
      opts.extraActions.forEach(function (a) {
        actions.appendChild(el('button.icon-btn', { text: a.label, title: a.title || a.label, onclick: a.onClick }));
      });
    }

    var doc = docFor(block);
    var card = el('.card' + (block.dirty ? '.dirty' : ''), { id: 'card-' + block.id }, [
      el('.card-head', null, [
        el('.card-title', null, [
          el('code.kw', { text: title }),
          name ? el('span.card-name', { text: name }) : null,
          block.dirty ? U.chip('edited', 'warn') : null,
          el('span.card-line', { text: block.lineNo ? 'line ' + U.fmt(block.lineNo) : 'new' })
        ]),
        actions
      ]),
      doc ? el('p.card-doc', { text: doc }) : null,
      tabs,
      body
    ]);

    select(opts.initialTab || (P.dataRows(block).length && !block.params.length ? 'data' : 'params'));
    return card;
  }

  /* --------------------------------------------------------------- panels */

  function overviewPanel(ctx) {
    var model = ctx.model;
    var s = M.stats(model);
    var wrap = el('.panel');

    wrap.appendChild(U.sectionHeader(
      ctx.fileName || 'Model overview',
      model.heading && model.heading.comments.length
        ? model.heading.comments.join(' ').replace(/^\*+\s*/, '')
        : 'Abaqus input file',
      [{
        label: 'Fit view', onClick: function () { ctx.viewer.fitAll(); }
      }]
    ));

    wrap.appendChild(U.statGrid([
      { label: 'Nodes', value: s.nodes },
      { label: 'Elements', value: s.elements },
      { label: 'Parts', value: s.parts },
      { label: 'Instances', value: s.instances },
      { label: 'Materials', value: s.materials },
      { label: 'Steps', value: s.steps },
      { label: 'Keyword cards', value: s.blocks }
    ]));

    /* Element type census, the fastest read on what kind of model this is. */
    var census = {};
    function addCensus(elements) {
      for (var i = 0; i < elements.count; i++) {
        var t = elements.types[elements.typeOf[i]];
        census[t] = (census[t] || 0) + 1;
      }
    }
    model.parts.forEach(function (p) { addCensus(p.elements); });
    if (model.assembly) {
      addCensus(model.assembly.elements);
      model.assembly.instances.forEach(function (i) { addCensus(i.elements); });
    }
    var types = Object.keys(census);
    if (types.length) {
      wrap.appendChild(el('h3.sub', { text: 'Element types' }));
      wrap.appendChild(el('.chip-row', null, types.map(function (t) {
        var G = global.InpGeometry;
        return el('.type-chip', null, [
          el('code', { text: t }),
          el('span.type-count', { text: U.fmt(census[t]) }),
          el('span.type-shape', { text: G.classify(t, 0) })
        ]);
      })));
    }

    wrap.appendChild(el('h3.sub', { text: 'Contents' }));
    var toc = [];
    function tocRow(label, count, target) {
      if (!count) return;
      toc.push(el('button.toc-row', {
        onclick: function () { ctx.selectPath(target); }
      }, [
        el('span.toc-label', { text: label }),
        el('span.toc-count', { text: U.fmt(count) })
      ]));
    }
    tocRow('Parts', model.parts.length, 'parts');
    tocRow('Assembly instances', s.instances, 'assembly');
    tocRow('Materials', model.materials.length, 'materials');
    tocRow('Interaction properties', model.interactionProperties.length, 'interactionProperties');
    tocRow('Interactions', model.interactions.length, 'interactions');
    tocRow('Constraints', model.constraints.length, 'constraints');
    tocRow('Amplitudes', model.amplitudes.length, 'amplitudes');
    tocRow('Initial conditions', model.initial.length, 'initial');
    tocRow('Boundary conditions', model.boundary.length, 'boundary');
    tocRow('Steps', model.steps.length, 'steps');
    tocRow('Other cards', model.unrecognised.length + model.misc.length, 'other');
    wrap.appendChild(el('.toc', null, toc));

    if (model.headerBlocks.length) {
      wrap.appendChild(el('h3.sub', { text: 'Header cards' }));
      model.headerBlocks.forEach(function (b) { wrap.appendChild(blockCard(ctx, b)); });
    }
    return wrap;
  }

  function partPanel(ctx, part) {
    var wrap = el('.panel');
    var c = part.container;
    wrap.appendChild(U.sectionHeader('Part: ' + part.name, 'Meshed body definition', [
      {
        label: 'Show only this part',
        onClick: function () { ctx.isolateSource('part', part); }
      }
    ]));
    wrap.appendChild(U.statGrid([
      { label: 'Nodes', value: part.nodes.count },
      { label: 'Elements', value: part.elements.count },
      { label: 'Node sets', value: c.nsets.length },
      { label: 'Element sets', value: c.elsets.length },
      { label: 'Sections', value: c.sections.length },
      { label: 'Surfaces', value: c.surfaces.length }
    ]));

    wrap.appendChild(blockCard(ctx, part.block));

    appendGroup(ctx, wrap, 'Mesh', c.nodeBlocks.concat(c.elementBlocks));
    appendGroup(ctx, wrap, 'Sections', c.sections);
    appendSetGroup(ctx, wrap, 'Node sets', c.nsets, 'nset', part);
    appendSetGroup(ctx, wrap, 'Element sets', c.elsets, 'elset', part);
    appendGroup(ctx, wrap, 'Surfaces', c.surfaces);
    appendGroup(ctx, wrap, 'Orientations', c.orientations);
    appendGroup(ctx, wrap, 'Other cards in this part', c.other);
    return wrap;
  }

  function assemblyPanel(ctx) {
    var asm = ctx.model.assembly;
    var wrap = el('.panel');
    if (!asm) {
      wrap.appendChild(U.sectionHeader('Assembly', 'This file has no *ASSEMBLY block.'));
      return wrap;
    }
    var c = asm.container;
    wrap.appendChild(U.sectionHeader('Assembly: ' + asm.name,
      'Positions part instances and holds model-level sets and surfaces'));
    wrap.appendChild(U.statGrid([
      { label: 'Instances', value: asm.instances.length },
      { label: 'Node sets', value: c.nsets.length },
      { label: 'Element sets', value: c.elsets.length },
      { label: 'Surfaces', value: c.surfaces.length }
    ]));

    wrap.appendChild(blockCard(ctx, asm.block));

    if (asm.instances.length) {
      wrap.appendChild(el('h3.sub', { text: 'Instances' }));
      wrap.appendChild(el('.inst-list', null, asm.instances.map(function (inst) {
        return el('button.inst-row', {
          onclick: function () { ctx.selectPath('instance:' + inst.name); }
        }, [
          el('span.inst-name', { text: inst.name }),
          el('span.inst-part', { text: inst.partName ? 'part ' + inst.partName : 'no part' }),
          el('span.inst-xf', {
            text: describeTransform(inst)
          })
        ]);
      })));
    }

    appendSetGroup(ctx, wrap, 'Node sets', c.nsets, 'nset', asm);
    appendSetGroup(ctx, wrap, 'Element sets', c.elsets, 'elset', asm);
    appendGroup(ctx, wrap, 'Surfaces', c.surfaces);
    appendGroup(ctx, wrap, 'Other cards in the assembly', c.other);
    return wrap;
  }

  function describeTransform(inst) {
    var t = inst.translation;
    var bits = [];
    if (t[0] || t[1] || t[2]) {
      bits.push('T(' + t.map(shortNum).join(', ') + ')');
    }
    if (inst.rotation) bits.push('R ' + shortNum(inst.rotation[6]) + '°');
    return bits.length ? bits.join('  ') : 'at origin';
  }

  function shortNum(v) {
    if (v === undefined || v === null) return '0';
    var n = Number(v);
    if (!isFinite(n)) return String(v);
    return String(Math.round(n * 1000) / 1000);
  }

  /*
   * Instance panel. Position is edited through real fields rather than raw
   * data lines, since that is the edit people actually want to make here.
   */
  function instancePanel(ctx, inst) {
    var wrap = el('.panel');
    wrap.appendChild(U.sectionHeader('Instance: ' + inst.name,
      inst.partName ? 'Placement of part "' + inst.partName + '"' : 'Instance without a part', [
        { label: 'Isolate', onClick: function () { ctx.isolateSource('instance', inst); } },
        { label: 'Fit', onClick: function () { ctx.fitInstance(inst); } }
      ]));

    var counts = [];
    if (inst.part) {
      counts.push({ label: 'Part nodes', value: inst.part.nodes.count });
      counts.push({ label: 'Part elements', value: inst.part.elements.count });
    }
    if (inst.nodes.count) counts.push({ label: 'Local nodes', value: inst.nodes.count });
    if (inst.elements.count) counts.push({ label: 'Local elements', value: inst.elements.count });
    counts.push({ label: 'Node sets', value: inst.container.nsets.length });
    counts.push({ label: 'Surfaces', value: inst.container.surfaces.length });
    wrap.appendChild(U.statGrid(counts));

    /* Position editor writes straight into the *INSTANCE data lines. */
    var pos = el('.position-editor');
    var fields = [];

    function posInput(label, get, set) {
      var input = el('input.num', { value: shortNum(get()), spellcheck: 'false' });
      input.addEventListener('change', function () {
        set(input.value.trim());
        writeInstanceData(inst);
        ctx.markDirty(inst.block);
        ctx.rebuildGeometry();
      });
      fields.push(input);
      return el('label.num-field', null, [el('span', { text: label }), input]);
    }

    pos.appendChild(el('.pos-group', null, [
      el('h4', { text: 'Translation' }),
      el('.num-row', null, [
        posInput('x', function () { return inst.translation[0]; }, function (v) { inst.translation[0] = parseFloat(v) || 0; }),
        posInput('y', function () { return inst.translation[1]; }, function (v) { inst.translation[1] = parseFloat(v) || 0; }),
        posInput('z', function () { return inst.translation[2]; }, function (v) { inst.translation[2] = parseFloat(v) || 0; })
      ])
    ]));

    var rotWrap = el('.pos-group');
    rotWrap.appendChild(el('h4', { text: 'Rotation' }));
    if (!inst.rotation) {
      rotWrap.appendChild(el('button.btn', {
        text: '+ Add rotation',
        onclick: function () {
          inst.rotation = [0, 0, 0, 0, 0, 1, 0];
          writeInstanceData(inst);
          ctx.markDirty(inst.block);
          ctx.refresh();
        }
      }));
    } else {
      var labels = ['ax', 'ay', 'az', 'bx', 'by', 'bz', 'angle'];
      var row = el('.num-row');
      labels.forEach(function (lb, i) {
        row.appendChild(posInput(lb,
          function () { return inst.rotation[i]; },
          function (v) { inst.rotation[i] = parseFloat(v) || 0; }));
      });
      rotWrap.appendChild(row);
      rotWrap.appendChild(el('p.card-hint', {
        text: 'Rotation of the instance by "angle" degrees about the axis running from point a to point b, applied after the translation.'
      }));
      rotWrap.appendChild(el('button.btn', {
        text: 'Remove rotation',
        onclick: function () {
          inst.rotation = null;
          writeInstanceData(inst);
          ctx.markDirty(inst.block);
          ctx.refresh();
        }
      }));
    }
    pos.appendChild(rotWrap);
    wrap.appendChild(pos);

    wrap.appendChild(blockCard(ctx, inst.block));

    var c = inst.container;
    appendGroup(ctx, wrap, 'Local mesh', c.nodeBlocks.concat(c.elementBlocks));
    appendSetGroup(ctx, wrap, 'Node sets', c.nsets, 'nset', inst);
    appendSetGroup(ctx, wrap, 'Element sets', c.elsets, 'elset', inst);
    appendGroup(ctx, wrap, 'Surfaces', c.surfaces);
    appendGroup(ctx, wrap, 'Sections', c.sections);
    appendGroup(ctx, wrap, 'Other cards in this instance', c.other);
    return wrap;
  }

  /* Rebuild the *INSTANCE data lines from the edited position. */
  function writeInstanceData(inst) {
    var rows = P.dataRows(inst.block);
    var kept = rows.filter(function (r) { return r.comment; });
    var newRows = kept.slice();
    var t = inst.translation;
    if (t[0] || t[1] || t[2] || inst.rotation) {
      newRows.push({ fields: t.map(shortNum), comment: false, dirty: true, src: null });
    }
    if (inst.rotation) {
      newRows.push({ fields: inst.rotation.map(shortNum), comment: false, dirty: true, src: null });
    }
    inst.block._rows = newRows;
    inst.block._rowsDirty = true;
    inst.block.dirty = true;
    inst.block.data = P.rowsToData(newRows, 16);
  }

  function materialsPanel(ctx) {
    var wrap = el('.panel');
    wrap.appendChild(U.sectionHeader('Materials', ctx.model.materials.length + ' material definition(s)', [
      {
        label: '+ Material', primary: true,
        onClick: function () { ctx.addMaterial(); }
      }
    ]));
    if (!ctx.model.materials.length) {
      wrap.appendChild(el('.empty-note', { text: 'No *MATERIAL cards in this file.' }));
    }
    ctx.model.materials.forEach(function (mat) {
      var group = el('.group.material-group');
      group.appendChild(el('.group-head', null, [
        el('h3', { text: mat.name }),
        el('span.group-sub', { text: mat.subs.map(function (b) { return '*' + b.keyword; }).join(' · ') || 'no behaviours' })
      ]));
      group.appendChild(blockCard(ctx, mat.block, {
        onDelete: function () { ctx.deleteBlocks([mat.block].concat(mat.subs)); }
      }));
      mat.subs.forEach(function (b) {
        group.appendChild(blockCard(ctx, b, {
          onDelete: function () { ctx.deleteBlocks([b]); }
        }));
      });
      group.appendChild(el('.group-foot', null, [
        el('button.add-btn', {
          text: '+ behaviour card',
          onclick: function () { ctx.addBehaviour(mat); }
        })
      ]));
      wrap.appendChild(group);
    });
    return wrap;
  }

  function stepsPanel(ctx) {
    var wrap = el('.panel');
    wrap.appendChild(U.sectionHeader('Steps', ctx.model.steps.length + ' analysis step(s)', [
      { label: '+ Step', primary: true, onClick: function () { ctx.addStep(); } }
    ]));
    ctx.model.steps.forEach(function (step, i) {
      wrap.appendChild(el('button.toc-row', {
        onclick: function () { ctx.selectPath('step:' + i); }
      }, [
        el('span.toc-label', { text: (i + 1) + '. ' + step.name }),
        el('span.toc-count', {
          text: (step.procedure ? '*' + step.procedure.keyword : 'no procedure')
        })
      ]));
    });
    if (!ctx.model.steps.length) {
      wrap.appendChild(el('.empty-note', { text: 'No *STEP blocks in this file.' }));
    }
    return wrap;
  }

  function stepPanel(ctx, step, index) {
    var wrap = el('.panel');
    wrap.appendChild(U.sectionHeader('Step ' + (index + 1) + ': ' + step.name,
      step.procedure ? 'Procedure *' + step.procedure.keyword : 'No procedure card', [
        { label: 'Delete step', onClick: function () { ctx.deleteBlocks(step.blocks); } }
      ]));

    wrap.appendChild(U.statGrid([
      { label: 'Boundary cards', value: step.boundary.length },
      { label: 'Loads', value: step.loads.length },
      { label: 'Interactions', value: step.interactions.length },
      { label: 'Output requests', value: step.output.length }
    ]));

    wrap.appendChild(blockCard(ctx, step.block));
    if (step.procedure) wrap.appendChild(blockCard(ctx, step.procedure));

    appendGroup(ctx, wrap, 'Boundary conditions', step.boundary, function () { ctx.addCard('Boundary', step); });
    appendGroup(ctx, wrap, 'Loads', step.loads, function () { ctx.addCard('Cload', step); });
    appendGroup(ctx, wrap, 'Interactions', step.interactions);
    appendGroup(ctx, wrap, 'Controls', step.controls);

    if (step.output.length) {
      wrap.appendChild(el('h3.sub', { text: 'Output requests' }));
      step.output.forEach(function (o) {
        var g = el('.group');
        g.appendChild(blockCard(ctx, o.block, {
          onDelete: function () { ctx.deleteBlocks([o.block].concat(o.subs)); }
        }));
        o.subs.forEach(function (s) {
          g.appendChild(blockCard(ctx, s, { onDelete: function () { ctx.deleteBlocks([s]); } }));
        });
        wrap.appendChild(g);
      });
    }
    appendGroup(ctx, wrap, 'Other cards in this step', step.other);
    return wrap;
  }

  function blockListPanel(ctx, title, subtitle, blocks, addKeyword) {
    var wrap = el('.panel');
    wrap.appendChild(U.sectionHeader(title, subtitle, addKeyword ? [{
      label: '+ ' + addKeyword, primary: true,
      onClick: function () { ctx.addCard(addKeyword, null); }
    }] : []));
    if (!blocks.length) {
      wrap.appendChild(el('.empty-note', { text: 'Nothing of this kind in the file.' }));
    }
    blocks.forEach(function (b) {
      wrap.appendChild(blockCard(ctx, b, {
        onDelete: function () { ctx.deleteBlocks([b]); }
      }));
    });
    return wrap;
  }

  function interactionPropsPanel(ctx) {
    var wrap = el('.panel');
    wrap.appendChild(U.sectionHeader('Interaction properties',
      ctx.model.interactionProperties.length + ' contact property definition(s)'));
    ctx.model.interactionProperties.forEach(function (ip) {
      var group = el('.group');
      group.appendChild(el('.group-head', null, [
        el('h3', { text: ip.name }),
        el('span.group-sub', { text: ip.subs.map(function (b) { return '*' + b.keyword; }).join(' · ') })
      ]));
      group.appendChild(blockCard(ctx, ip.block, {
        onDelete: function () { ctx.deleteBlocks([ip.block].concat(ip.subs)); }
      }));
      ip.subs.forEach(function (b) {
        group.appendChild(blockCard(ctx, b, { onDelete: function () { ctx.deleteBlocks([b]); } }));
      });
      wrap.appendChild(group);
    });
    if (!ctx.model.interactionProperties.length) {
      wrap.appendChild(el('.empty-note', { text: 'No *SURFACE INTERACTION cards in this file.' }));
    }
    return wrap;
  }

  /* Sets get a dedicated list with counts and a jump-to-3D action. */
  function setsPanel(ctx, owner, which, label) {
    var wrap = el('.panel');
    var blocks = which === 'nset' ? owner.container.nsets : owner.container.elsets;
    wrap.appendChild(U.sectionHeader(label, blocks.length + ' set(s)'));
    appendSetGroup(ctx, wrap, null, blocks, which, owner);
    return wrap;
  }

  function appendSetGroup(ctx, wrap, title, blocks, which, owner) {
    if (!blocks.length) return;
    if (title) wrap.appendChild(el('h3.sub', { text: title + ' (' + blocks.length + ')' }));
    blocks.forEach(function (b) {
      var count = b._setCount || 0;
      var card = blockCard(ctx, b, {
        onDelete: function () { ctx.deleteBlocks([b]); },
        extraActions: [{
          label: 'Show in 3D',
          title: 'Highlight this set in the viewport',
          onClick: function () { ctx.highlightSetBlock(b, which, owner); }
        }]
      });
      var head = card.querySelector('.card-title');
      if (head) head.appendChild(U.chip(U.fmt(count) + (which === 'nset' ? ' nodes' : ' elements')));
      wrap.appendChild(card);
    });
  }

  function appendGroup(ctx, wrap, title, blocks, onAdd) {
    if (!blocks.length && !onAdd) return;
    wrap.appendChild(el('h3.sub', null, [
      el('span', { text: title + (blocks.length ? ' (' + blocks.length + ')' : '') }),
      onAdd ? el('button.add-btn.inline', { text: '+ add', onclick: onAdd }) : null
    ]));
    if (!blocks.length) {
      wrap.appendChild(el('.empty-note', { text: 'None.' }));
      return;
    }
    blocks.forEach(function (b) {
      wrap.appendChild(blockCard(ctx, b, {
        onDelete: function () { ctx.deleteBlocks([b]); }
      }));
    });
  }

  global.InpPanels = {
    overviewPanel: overviewPanel,
    partPanel: partPanel,
    assemblyPanel: assemblyPanel,
    instancePanel: instancePanel,
    materialsPanel: materialsPanel,
    interactionPropsPanel: interactionPropsPanel,
    stepsPanel: stepsPanel,
    stepPanel: stepPanel,
    blockListPanel: blockListPanel,
    setsPanel: setsPanel,
    blockCard: blockCard,
    KEYWORD_DOC: KEYWORD_DOC
  };
})(typeof window !== 'undefined' ? window : globalThis);
