/*
 * model.js -- turns the flat block list from the parser into the structured
 * model the UI navigates: parts, the assembly and its instances, materials,
 * interactions, steps and everything else.
 *
 * The blocks themselves stay authoritative. Model objects only ever hold
 * references to blocks, so an edit made through any view is visible to the
 * writer without a rebuild step.
 */
(function (global) {
  'use strict';

  var P = global.InpParser;

  /* Keywords that open a scope, and the keyword that closes each. */
  var SCOPE_OPEN = {
    PART: 'ENDPART',
    ASSEMBLY: 'ENDASSEMBLY',
    INSTANCE: 'ENDINSTANCE',
    STEP: 'ENDSTEP'
  };

  /* Grouping of loose model-level keywords into readable categories. */
  var CATEGORY = {
    MATERIAL: 'materials',
    SURFACEINTERACTION: 'interactionProperties',
    CONTACTPROPERTYASSIGNMENT: 'interactionProperties',
    AMPLITUDE: 'amplitudes',
    BOUNDARY: 'boundary',
    INITIALCONDITIONS: 'initial',
    CONTACTPAIR: 'interactions',
    CONTACT: 'interactions',
    TIE: 'interactions',
    CONTACTINCLUSIONS: 'interactions',
    CONTACTEXCLUSIONS: 'interactions',
    COUPLING: 'constraints',
    KINEMATICCOUPLING: 'constraints',
    DISTRIBUTINGCOUPLING: 'constraints',
    EQUATION: 'constraints',
    MPC: 'constraints',
    RIGIDBODY: 'constraints',
    CONNECTORSECTION: 'constraints',
    SYSTEM: 'misc',
    HEADING: 'header',
    PREPRINT: 'header',
    RESTART: 'output',
    OUTPUT: 'output',
    NODEPRINT: 'output',
    ELPRINT: 'output',
    NODEFILE: 'output',
    ELFILE: 'output'
  };

  /* Material behaviour cards that belong to the *MATERIAL above them. */
  var MATERIAL_SUB = {
    ELASTIC: 1, PLASTIC: 1, DENSITY: 1, HYPERELASTIC: 1, HYPERFOAM: 1,
    VISCOELASTIC: 1, EXPANSION: 1, CONDUCTIVITY: 1, SPECIFICHEAT: 1,
    DAMPING: 1, UNIAXIALTESTDATA: 1, BIAXIALTESTDATA: 1, PLANARTESTDATA: 1,
    VOLUMETRICTESTDATA: 1, SIMPLESHEARTESTDATA: 1, DEPVAR: 1, USERMATERIAL: 1,
    CREEP: 1, POTENTIAL: 1, MULLINSEFFECT: 1, DUCTILEDAMAGE: 1,
    DAMAGEINITIATION: 1, DAMAGEEVOLUTION: 1, CONCRETEDAMAGEDPLASTICITY: 1,
    DRUCKERPRAGER: 1, DRUCKERPRAGERHARDENING: 1, MOHRCOULOMB: 1,
    MOHRCOULOMBHARDENING: 1, LATENTHEAT: 1, PERMEABILITY: 1, POROUSELASTIC: 1
  };

  /* Interaction-property behaviour cards belonging to *SURFACE INTERACTION. */
  var INTERACTION_SUB = {
    FRICTION: 1, SURFACEBEHAVIOR: 1, COHESIVEBEHAVIOR: 1, DAMPING: 1,
    THERMALCONDUCTANCE: 1, GAPCONDUCTANCE: 1, GAPHEATGENERATION: 1,
    ELECTRICALCONDUCTANCE: 1, GAPELECTRICALCONDUCTANCE: 1
  };

  /* Output-request cards belonging to the *OUTPUT above them. */
  var OUTPUT_SUB = {
    NODEOUTPUT: 1, ELEMENTOUTPUT: 1, CONTACTOUTPUT: 1, ENERGYOUTPUT: 1,
    RADIATIONOUTPUT: 1, INTEGRATEDOUTPUT: 1, INCREMENTATION: 1
  };

  /* Step procedure cards. */
  var PROCEDURES = {
    STATIC: 1, DYNAMIC: 1, FREQUENCY: 1, BUCKLE: 1, MODALDYNAMIC: 1,
    STEADYSTATEDYNAMICS: 1, HEATTRANSFER: 1, COUPLEDTEMPERATUREDISPLACEMENT: 1,
    VISCO: 1, SOILS: 1, GEOSTATIC: 1, COMPLEXFREQUENCY: 1, RANDOMRESPONSE: 1,
    STATICRIKS: 1, ANNEAL: 1
  };

  var LOAD_KEYS = {
    CLOAD: 1, DLOAD: 1, DSLOAD: 1, PRESSURE: 1, GRAVITY: 1, CENTRIF: 1,
    BODYFORCE: 1, TEMPERATURE: 1, FILM: 1, RADIATE: 1, CFLUX: 1, DFLUX: 1,
    CONNECTORLOAD: 1, CONNECTORMOTION: 1
  };

  var SECTION_KEYS = {
    SOLIDSECTION: 1, SHELLSECTION: 1, BEAMSECTION: 1, MEMBRANESECTION: 1,
    TRUSSSECTION: 1, COHESIVESECTION: 1, SHELLGENERALSECTION: 1,
    GASKETSECTION: 1, ACOUSTICINFINITESECTION: 1, SURFACESECTION: 1,
    BEAMGENERALSECTION: 1, MASS: 1, ROTARYINERTIA: 1, SPRING: 1, DASHPOT: 1,
    CONNECTORSECTION: 1
  };

  function num(v) {
    var f = parseFloat(v);
    return isFinite(f) ? f : 0;
  }

  /* ---------------------------------------------------------------- nodes */

  /*
   * Node blocks become a parallel id array + flat xyz array. Missing z is
   * treated as 0 so 2-D models still draw.
   */
  function readNodes(blocks) {
    var ids = [], xyz = [], map = {};
    blocks.forEach(function (b) {
      var rows = P.dataRows(b);
      for (var i = 0; i < rows.length; i++) {
        var r = rows[i];
        if (r.comment || !r.fields || r.fields.length < 2) continue;
        var id = parseInt(r.fields[0], 10);
        if (!isFinite(id)) continue;
        if (map[id] === undefined) {
          map[id] = ids.length;
          ids.push(id);
          xyz.push(num(r.fields[1]), num(r.fields[2]), num(r.fields[3]));
        } else {
          /* A repeated node id redefines the coordinates. */
          var k = map[id] * 3;
          xyz[k] = num(r.fields[1]);
          xyz[k + 1] = num(r.fields[2]);
          xyz[k + 2] = num(r.fields[3]);
        }
      }
    });
    return { ids: ids, xyz: new Float64Array(xyz), map: map, count: ids.length };
  }

  /* ------------------------------------------------------------- elements */

  function readElements(blocks) {
    var ids = [], conn = [], types = [], typeOf = [], map = {};
    blocks.forEach(function (b) {
      var type = (P.getParam(b, 'type') || 'UNKNOWN').toUpperCase();
      var ti = types.indexOf(type);
      if (ti === -1) { ti = types.length; types.push(type); }
      var rows = P.dataRows(b);
      for (var i = 0; i < rows.length; i++) {
        var r = rows[i];
        if (r.comment || !r.fields || r.fields.length < 2) continue;
        var id = parseInt(r.fields[0], 10);
        if (!isFinite(id)) continue;
        var nodes = [];
        for (var j = 1; j < r.fields.length; j++) {
          var n = parseInt(r.fields[j], 10);
          if (isFinite(n)) nodes.push(n);
        }
        map[id] = ids.length;
        ids.push(id);
        conn.push(nodes);
        typeOf.push(ti);
      }
    });
    return {
      ids: ids, conn: conn, types: types, typeOf: typeOf,
      map: map, count: ids.length
    };
  }

  /* ----------------------------------------------------------------- sets */

  /*
   * Expand an *NSET/*ELSET block to an explicit id list. Handles GENERATE
   * (first, last, increment) and references to previously defined sets.
   */
  function expandSet(block, lookup) {
    var out = [];
    var generate = P.hasParam(block, 'generate');
    var rows = P.dataRows(block);
    for (var i = 0; i < rows.length; i++) {
      var r = rows[i];
      if (r.comment || !r.fields) continue;
      if (generate) {
        var a = parseInt(r.fields[0], 10);
        var b = parseInt(r.fields[1], 10);
        var inc = r.fields.length > 2 ? parseInt(r.fields[2], 10) : 1;
        if (!isFinite(a) || !isFinite(b)) continue;
        if (!isFinite(inc) || inc === 0) inc = 1;
        for (var v = a; inc > 0 ? v <= b : v >= b; v += inc) out.push(v);
      } else {
        for (var j = 0; j < r.fields.length; j++) {
          var f = r.fields[j];
          if (f === '') continue;
          var n = parseInt(f, 10);
          if (isFinite(n) && /^\s*-?\d+\s*$/.test(f)) out.push(n);
          else if (lookup) {
            /* Set built from other sets. */
            var ref = lookup(f);
            if (ref) out.push.apply(out, ref);
          }
        }
      }
    }
    return out;
  }

  /* ------------------------------------------------------- model assembly */

  function nameOf(block, keys) {
    for (var i = 0; i < keys.length; i++) {
      var v = P.getParam(block, keys[i]);
      if (v) return v;
    }
    return null;
  }

  function newContainer() {
    return {
      nodeBlocks: [], elementBlocks: [], nsets: [], elsets: [], surfaces: [],
      sections: [], orientations: [], other: []
    };
  }

  function build(parseResult) {
    var blocks = parseResult.blocks;
    var model = {
      blocks: blocks,
      trailing: parseResult.trailing,
      heading: null,
      headerBlocks: [],
      parts: [],
      partByName: {},
      assembly: null,
      materials: [],
      interactionProperties: [],
      interactions: [],
      constraints: [],
      amplitudes: [],
      initial: [],
      boundary: [],
      output: [],
      misc: [],
      steps: [],
      unrecognised: []
    };

    var part = null, assembly = null, instance = null, step = null;
    var material = null, interProp = null, outputReq = null;
    var i;

    function target() {
      if (instance) return instance.container;
      if (part) return part.container;
      if (assembly) return assembly.container;
      return null;
    }

    for (i = 0; i < blocks.length; i++) {
      var b = blocks[i];
      var k = b.key;
      b.scope = { part: part, assembly: assembly, instance: instance, step: step };

      /* -- scope openers / closers ------------------------------------- */
      if (k === 'PART') {
        part = {
          type: 'part',
          name: P.getParam(b, 'name') || ('Part-' + (model.parts.length + 1)),
          block: b, endBlock: null, container: newContainer(), blocks: [b]
        };
        model.parts.push(part);
        model.partByName[part.name.toUpperCase()] = part;
        continue;
      }
      if (k === 'ENDPART') { if (part) { part.endBlock = b; part.blocks.push(b); } part = null; material = null; continue; }

      if (k === 'ASSEMBLY') {
        assembly = {
          type: 'assembly',
          name: P.getParam(b, 'name') || 'Assembly',
          block: b, endBlock: null, container: newContainer(), instances: [], blocks: [b]
        };
        model.assembly = assembly;
        continue;
      }
      if (k === 'ENDASSEMBLY') { if (assembly) { assembly.endBlock = b; assembly.blocks.push(b); } assembly = null; continue; }

      if (k === 'INSTANCE') {
        instance = {
          type: 'instance',
          name: P.getParam(b, 'name') || ('Instance-' + ((assembly ? assembly.instances.length : 0) + 1)),
          partName: P.getParam(b, 'part') || '',
          block: b, endBlock: null, container: newContainer(), blocks: [b],
          translation: [0, 0, 0], rotation: null
        };
        /* Data lines on *INSTANCE give the position:
           line 1  x, y, z                          (translation)
           line 2  x1,y1,z1, x2,y2,z2, angle        (rotation about that axis) */
        var irows = P.dataRows(b).filter(function (r) { return !r.comment && r.fields; });
        if (irows.length > 0 && irows[0].fields.length >= 3) {
          instance.translation = [num(irows[0].fields[0]), num(irows[0].fields[1]), num(irows[0].fields[2])];
        }
        if (irows.length > 1 && irows[1].fields.length >= 7) {
          instance.rotation = irows[1].fields.slice(0, 7).map(num);
        }
        if (assembly) assembly.instances.push(instance);
        continue;
      }
      if (k === 'ENDINSTANCE') { if (instance) { instance.endBlock = b; instance.blocks.push(b); } instance = null; continue; }

      if (k === 'STEP') {
        step = {
          type: 'step',
          name: P.getParam(b, 'name') || ('Step-' + (model.steps.length + 1)),
          block: b, endBlock: null, blocks: [b],
          procedure: null, boundary: [], loads: [], interactions: [],
          output: [], controls: [], other: []
        };
        model.steps.push(step);
        outputReq = null;
        continue;
      }
      if (k === 'ENDSTEP') { if (step) { step.endBlock = b; step.blocks.push(b); } step = null; outputReq = null; continue; }

      /* -- inside a step ----------------------------------------------- */
      if (step) {
        step.blocks.push(b);
        if (PROCEDURES[k]) { step.procedure = b; continue; }
        if (k === 'BOUNDARY') { step.boundary.push(b); continue; }
        if (LOAD_KEYS[k]) { step.loads.push(b); continue; }
        if (k === 'OUTPUT' || k === 'RESTART') {
          outputReq = { block: b, subs: [] };
          step.output.push(outputReq);
          continue;
        }
        if (OUTPUT_SUB[k] && outputReq) { outputReq.subs.push(b); continue; }
        if (k === 'CONTROLS' || k === 'SOLUTIONTECHNIQUE') { step.controls.push(b); continue; }
        if (k === 'CONTACTPAIR' || k === 'MODELCHANGE' || k === 'CONTACTINTERFERENCE') {
          step.interactions.push(b); continue;
        }
        step.other.push(b);
        continue;
      }

      /* -- inside part / instance / assembly --------------------------- */
      var tgt = target();
      if (tgt) {
        if (part) part.blocks.push(b);
        else if (instance) instance.blocks.push(b);
        else if (assembly) assembly.blocks.push(b);

        if (k === 'NODE') { tgt.nodeBlocks.push(b); continue; }
        if (k === 'ELEMENT') { tgt.elementBlocks.push(b); continue; }
        if (k === 'NSET') { tgt.nsets.push(b); continue; }
        if (k === 'ELSET') { tgt.elsets.push(b); continue; }
        if (k === 'SURFACE') { tgt.surfaces.push(b); continue; }
        if (SECTION_KEYS[k]) { tgt.sections.push(b); continue; }
        if (k === 'ORIENTATION') { tgt.orientations.push(b); continue; }
        tgt.other.push(b);
        continue;
      }

      /* -- model level -------------------------------------------------- */
      if (k === 'MATERIAL') {
        material = { type: 'material', name: P.getParam(b, 'name') || 'Material', block: b, subs: [] };
        model.materials.push(material);
        continue;
      }
      if (MATERIAL_SUB[k] && material) { material.subs.push(b); continue; }

      if (k === 'SURFACEINTERACTION') {
        interProp = { type: 'interactionProperty', name: P.getParam(b, 'name') || 'IntProp', block: b, subs: [] };
        model.interactionProperties.push(interProp);
        material = null;
        continue;
      }
      if (INTERACTION_SUB[k] && interProp) { interProp.subs.push(b); continue; }

      if (k === 'OUTPUT' || k === 'RESTART') {
        outputReq = { block: b, subs: [] };
        model.output.push(outputReq);
        continue;
      }
      if (OUTPUT_SUB[k] && outputReq) { outputReq.subs.push(b); continue; }

      if (k === 'HEADING') { model.heading = b; model.headerBlocks.push(b); continue; }

      var cat = CATEGORY[k];
      if (cat === 'header') { model.headerBlocks.push(b); continue; }
      if (cat && model[cat]) { model[cat].push(b); continue; }
      if (k === 'AMPLITUDE') { model.amplitudes.push(b); continue; }
      if (SECTION_KEYS[k]) { model.misc.push(b); continue; }

      model.unrecognised.push(b);
    }

    resolveMesh(model);
    return model;
  }

  /*
   * Build node/element arrays for every part and for assembly-level meshes,
   * then index the sets so the viewer and the set editors can resolve them.
   */
  function resolveMesh(model) {
    function fill(owner, container) {
      owner.nodes = readNodes(container.nodeBlocks);
      owner.elements = readElements(container.elementBlocks);
      owner.nsetIndex = {};
      owner.elsetIndex = {};
      container.nsets.forEach(function (b) {
        var n = P.getParam(b, 'nset');
        if (!n) return;
        var key = n.toUpperCase();
        var ids = expandSet(b, function (ref) { return owner.nsetIndex[ref.toUpperCase()]; });
        owner.nsetIndex[key] = (owner.nsetIndex[key] || []).concat(ids);
        b._setName = n;
        b._setCount = owner.nsetIndex[key].length;
      });
      container.elsets.forEach(function (b) {
        var n = P.getParam(b, 'elset');
        if (!n) return;
        var key = n.toUpperCase();
        var ids = expandSet(b, function (ref) { return owner.elsetIndex[ref.toUpperCase()]; });
        owner.elsetIndex[key] = (owner.elsetIndex[key] || []).concat(ids);
        b._setName = n;
        b._setCount = owner.elsetIndex[key].length;
      });
    }

    model.parts.forEach(function (p) { fill(p, p.container); });

    if (model.assembly) {
      fill(model.assembly, model.assembly.container);
      model.assembly.instances.forEach(function (inst) {
        fill(inst, inst.container);
        inst.part = model.partByName[(inst.partName || '').toUpperCase()] || null;
        /* An instance may add its own nodes (rigid reference points) on top of
           the part mesh; both are drawn. */
        inst.hasOwnMesh = inst.nodes.count > 0 || inst.elements.count > 0;
      });
    }
  }

  /*
   * Resolve a possibly instance-qualified name such as `Road-1.Road_RP`
   * against the assembly. Returns { instance, name, ids } or null.
   */
  function resolveNset(model, ref) {
    return resolveSet(model, ref, 'nsetIndex');
  }
  function resolveElset(model, ref) {
    return resolveSet(model, ref, 'elsetIndex');
  }
  function resolveSet(model, ref, which) {
    if (!ref) return null;
    var name = String(ref).trim();
    var dot = name.indexOf('.');
    var asm = model.assembly;
    if (dot > 0 && asm) {
      var instName = name.substring(0, dot).toUpperCase();
      var local = name.substring(dot + 1).toUpperCase();
      for (var i = 0; i < asm.instances.length; i++) {
        var inst = asm.instances[i];
        if (inst.name.toUpperCase() === instName) {
          var ids = inst[which][local] ||
            (inst.part ? inst.part[which][local] : null);
          if (ids) return { instance: inst, name: name, ids: ids };
        }
      }
      return null;
    }
    if (asm) {
      var got = asm[which][name.toUpperCase()];
      if (got) {
        /* Assembly-level sets carry an INSTANCE parameter on their block. */
        var owner = null;
        var pool = which === 'nsetIndex' ? asm.container.nsets : asm.container.elsets;
        for (var j = 0; j < pool.length; j++) {
          var pn = P.getParam(pool[j], which === 'nsetIndex' ? 'nset' : 'elset');
          if (pn && pn.toUpperCase() === name.toUpperCase()) {
            var iname = P.getParam(pool[j], 'instance');
            if (iname) {
              for (var m = 0; m < asm.instances.length; m++) {
                if (asm.instances[m].name.toUpperCase() === iname.toUpperCase()) { owner = asm.instances[m]; break; }
              }
            }
            break;
          }
        }
        return { instance: owner, name: name, ids: got };
      }
    }
    for (var p = 0; p < model.parts.length; p++) {
      var pid = model.parts[p][which][name.toUpperCase()];
      if (pid) return { instance: null, part: model.parts[p], name: name, ids: pid };
    }
    return null;
  }

  /* Summary counts for the header strip. */
  function stats(model) {
    var nodes = 0, elems = 0;
    model.parts.forEach(function (p) { nodes += p.nodes.count; elems += p.elements.count; });
    if (model.assembly) {
      nodes += model.assembly.nodes.count;
      elems += model.assembly.elements.count;
      model.assembly.instances.forEach(function (i) {
        nodes += i.nodes.count;
        elems += i.elements.count;
      });
    }
    return {
      nodes: nodes,
      elements: elems,
      parts: model.parts.length,
      instances: model.assembly ? model.assembly.instances.length : 0,
      materials: model.materials.length,
      steps: model.steps.length,
      blocks: model.blocks.length
    };
  }

  global.InpModel = {
    build: build,
    expandSet: expandSet,
    readNodes: readNodes,
    readElements: readElements,
    resolveNset: resolveNset,
    resolveElset: resolveElset,
    stats: stats,
    PROCEDURES: PROCEDURES,
    LOAD_KEYS: LOAD_KEYS,
    SECTION_KEYS: SECTION_KEYS
  };
})(typeof window !== 'undefined' ? window : globalThis);
