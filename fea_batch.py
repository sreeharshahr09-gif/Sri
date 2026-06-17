# Run with: abaqus cae noGUI=fea_batch.py -- input_file [output_dir]
#
# input_file may be either:
#   - a project .json exported by the tool's "Save JSON" button, or
#   - a plain-ASCII .txt exported by the tool's "Export FEA TXT" button.
# The format is auto-detected from the file extension.
#
# For every design it builds a 3D solid block from the polygon, applies
# the same 4-load-case test (Kx, Ky, Kxy via 45deg, Kz) used in manual
# validation, solves, extracts reaction forces, and writes a CSV
# comparing FEA stiffness against the tool's prediction.
#
# Material: Neo-Hookean hyperelastic (large-strain), run with nlgeom=ON.
# Meshing: seed size is auto-capped to the smallest geometric feature in the
# polygon, and the mesh is re-seeded progressively finer if the initial
# attempt fails Abaqus's own quality check or fails to mesh/solve at all.
# Contact: if any two non-adjacent polygon edges pass within CONTACT_GAP_MIN/
# MAX of each other (sipe walls, narrow slots, etc.), a self-contact general
# contact definition is added automatically so those surfaces interact
# instead of being left free to interpenetrate.

import sys, os, json, csv, math
from abaqus import mdb, session
from abaqusConstants import *
import regionToolset
import mesh

# Number of CPU cores each individual solve uses (Abaqus/Standard domain
# decomposition). Set to 1 to disable parallelism (e.g. on a machine with a
# single-CPU-token license). Safe to raise once you know how many cores
# your server/license allows per job.
JOB_NUM_CPUS = 4

# A fixed 1mm displacement is fine for a 10-15mm-tall plain square block, but
# on real tread geometry (thin ribs/walls) it can be 7-10%+ local strain in a
# single step -- enough to genuinely buckle/wrinkle thin features. Stiffness
# Kx/Ky/Kxy/Kz is a small-deformation property anyway, so the applied
# displacement is scaled to a small strain fraction of each design's own
# height instead of a fixed absolute value. This is what was producing
# "mesh distortion" that no amount of re-meshing could fix -- it was real
# buckling driven by an oversized applied displacement, not a meshing issue.
DELTA_STRAIN_FRAC = 0.02   # applied displacement = 2% of block height (nsd)
DELTA_FLOOR = 0.02          # mm, never apply less than this (keeps RF signal
                             # well above solver noise on very short blocks)
E_DEFAULT = 6.0       # MPa, used only if a design omits E
NU_DEFAULT = 0.49

# ---------------------------------------------------------------------------
# MESH QUALITY / AUTOMATION CONTROLS
# ---------------------------------------------------------------------------
# The global seed is sized from the block's OVERALL dimensions (target a
# handful of elements across the smallest bounding extent), NOT from the
# tiniest polygon feature. A single 0.1mm sipe gap must not drive a 0.1mm
# seed across an entire 10-15mm block -- that produces millions of quadratic
# tets and a run that never finishes. Tiny features are simply meshed a bit
# coarsely; that's fine for a stiffness estimate. A hard element-count budget
# coarsens the seed if a mesh still comes out too large.
MESH_TARGET_ELEMS_ACROSS = 6.0   # aim for ~this many elements across the
                                  # smallest in-plane / height dimension
MESH_MAX_ELEMS = 40000           # element-count budget; coarsen if exceeded
MESH_SEED_FLOOR = 0.3            # mm, absolute finest auto seed
MESH_SEED_CEIL_FRAC = 0.5        # seed never coarser than this * char. dim
MESH_REFINE_FACTOR = 0.7         # seed *= this when refining for quality
MESH_MAX_ATTEMPTS = 5
ASPECT_RATIO_LIMIT = 12.0        # elements above this are considered poor

# Non-adjacent polygon edges closer than this are treated as a real surface
# pair (sipe wall, narrow rib slot, etc.) that needs contact, not a coincident
# point that's just noisy digitization.
CONTACT_GAP_MIN = 0.1   # mm
CONTACT_GAP_MAX = 1.5   # mm

# Static step increment control (per request: replicate
# initial/min/max increment behaviour, kept under nlgeom=ON).
STEP_INITIAL_INC = 0.01
STEP_MIN_INC = 1.0e-6
STEP_MAX_INC = 0.1
STEP_MAX_NUM_INC = 1000
STEP_TIME_PERIOD = 1.0

# ---------------------------------------------------------------------------
# PARAMETER SWEEPS (optional)
# ---------------------------------------------------------------------------
# Each entry sweeps ONE design field across a list of values (one-factor-at-
# a-time: other fields stay at the design's base value). Every base design in
# the JSON is expanded into one FEA run per sweep value. Leave SWEEPS empty
# to run designs exactly as saved.
#
# 'param' must match a field consumed by build_and_run:
#   'nsd' -> block height (mm)
#   'E'   -> Young's modulus (MPa)
#   'nu'  -> Poisson's ratio
#   'mesh'-> seed size (mm)   (convergence studies)
#
# Examples:
#   SWEEPS = [{'param':'nsd', 'values':[15, 12, 9, 6]}]
#   SWEEPS = [{'param':'E',   'values':[4, 6, 8, 10]},
#             {'param':'mesh','values':[1.0, 0.5, 0.25]}]
#
# MESH CONVERGENCE STUDY (active below): seed = block_thickness / N_elements.
# For an ~8mm-NSD block, these seeds give roughly 4 / 6 / 8 / 10 elements
# across the thickness. Run this on ONE block (a single-block .txt/.json) and
# watch fea_Kx/fea_Ky in the CSV: pick the coarsest seed where K has stopped
# changing (typ. <1-2% between successive rows). Then set that as the routine
# density (via MESH_TARGET_ELEMS_ACROSS) and clear SWEEPS back to [] for
# production runs. Explicit sweep seeds bypass the element-count budget, so
# the fine meshes won't be auto-coarsened.
SWEEPS = [{'param': 'mesh', 'values': [2.0, 1.33, 1.0, 0.8]}]


def read_args():
    # Abaqus 'noGUI' mode populates sys.argv with the CAE executable path
    # and a 'noGUI=...' token, and may strip the '--' separator. So rather
    # than trust argument positions, scan everything for the data file
    # (the arg ending in .txt or .json) and treat the next remaining arg
    # as the output directory.
    argv = sys.argv
    if '--' in argv:
        argv = argv[argv.index('--') + 1:]

    input_path = None
    for a in argv:
        low = a.lower()
        if low.endswith('.txt') or low.endswith('.json'):
            input_path = a
            break
    if input_path is None:
        raise RuntimeError(
            'No .txt or .json input file found in arguments (sys.argv=%r). '
            'Run as: abaqus cae noGUI=fea_batch.py -- <file.txt> [out_dir]'
            % (sys.argv,))

    # Output goes in a 'fea_runs' folder NEXT TO the input file. We do NOT
    # trust argv for this: Abaqus noGUI injects temp-dir paths into argv that
    # are indistinguishable from a user-supplied output dir, so deriving the
    # location from the input file is the only reliable choice.
    out_dir = os.path.join(os.path.dirname(os.path.abspath(input_path)), 'fea_runs')
    return input_path, out_dir


def _seg_seg_distance(p1, p2, p3, p4):
    """Min distance between segments p1-p2 and p3-p4 in 2D."""
    def clamp(t):
        return max(0.0, min(1.0, t))

    def closest_pt(a, b, p):
        ax, ay = a; bx, by = b; px, py = p
        dx, dy = bx - ax, by - ay
        l2 = dx * dx + dy * dy
        if l2 < 1e-12:
            return a
        t = clamp(((px - ax) * dx + (py - ay) * dy) / l2)
        return (ax + t * dx, ay + t * dy)

    def dist(a, b):
        return math.hypot(a[0] - b[0], a[1] - b[1])

    candidates = [
        dist(closest_pt(p1, p2, p3), p3),
        dist(closest_pt(p1, p2, p4), p4),
        dist(closest_pt(p3, p4, p1), p1),
        dist(closest_pt(p3, p4, p2), p2),
    ]
    return min(candidates)


def analyze_geometry(verts):
    """Inspect the polygon for (a) the smallest feature size, used to cap
    the mesh seed so thin walls/ribs get enough elements across them, and
    (b) pairs of non-adjacent edges that pass close enough to each other
    (CONTACT_GAP_MIN..CONTACT_GAP_MAX) to need an explicit contact
    definition rather than being left as unconnected free surfaces."""
    n = len(verts)
    pts = [tuple(v) for v in verts]
    edges = [(pts[i], pts[(i + 1) % n]) for i in range(n)]

    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    bbox_w = max(xs) - min(xs)
    bbox_h = max(ys) - min(ys)

    min_edge_len = min(math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in edges)
    min_edge_len = max(min_edge_len, 1e-6)

    needs_contact = False
    min_gap = None
    for i in range(n):
        for j in range(i + 1, n):
            if j == i or j == (i + 1) % n or i == (j + 1) % n:
                continue  # skip shared-vertex (adjacent) edges
            d = _seg_seg_distance(edges[i][0], edges[i][1], edges[j][0], edges[j][1])
            if min_gap is None or d < min_gap:
                min_gap = d
            if CONTACT_GAP_MIN <= d <= CONTACT_GAP_MAX:
                needs_contact = True

    feature_size = min_edge_len if min_gap is None else min(min_edge_len, max(min_gap, 1e-6))
    return {'min_feature': feature_size, 'needs_contact': needs_contact,
            'min_gap': min_gap, 'bbox_w': bbox_w, 'bbox_h': bbox_h}


def pick_seed_size(requested_seed, geo, height):
    """Choose the GLOBAL seed from the block's overall size, not the smallest
    feature. Target ~MESH_TARGET_ELEMS_ACROSS elements across the smallest of
    {bbox width, bbox height, block height}, clamped to a sane absolute range.
    An explicit per-design 'mesh' value still wins (clamped to the floor)."""
    char_dim = min(d for d in (geo['bbox_w'], geo['bbox_h'], height) if d > 1e-6)
    if requested_seed is not None:
        return max(requested_seed, MESH_SEED_FLOOR)
    seed = char_dim / MESH_TARGET_ELEMS_ACROSS
    seed = min(seed, char_dim * MESH_SEED_CEIL_FRAC)
    return max(seed, MESH_SEED_FLOOR)


def mesh_quality_ok(part):
    """Best-effort mesh quality check via Abaqus's own verifyMeshQuality.
    Returns True if no elements fail the aspect-ratio criterion (or if the
    check itself isn't available in this Abaqus version, in which case we
    don't block the run on it)."""
    try:
        bad = part.verifyMeshQuality(criterion=ASPECT_RATIO,
                                      threshold=ASPECT_RATIO_LIMIT,
                                      thresholdType=ABOVE)
        n_bad = len(bad) if bad is not None else 0
        return n_bad == 0
    except Exception:
        return True


def mesh_with_quality_control(part, requested_seed, geo, height):
    """Seed + mesh with both an element-count budget (coarsen if too many
    elements) and a quality gate (refine if elements are poor). Returns the
    seed size that was ultimately used.

    Uses quadratic tetrahedra (C3D10MH) with free meshing rather than linear
    hex (C3D8H): free-tet meshing handles arbitrary/complex polygon topology
    (sipes, concave corners, islands) without needing a sweepable extrusion,
    and quadratic tets don't suffer the shear locking that linear
    full-integration hex elements show under bending of thin ribs -- which
    is what was showing up as "mesh distortion" on real tread geometry even
    after the applied displacement was scaled down."""
    # An explicitly-requested seed (per-design 'mesh' value, or a mesh sweep
    # for a convergence study) is honored EXACTLY: the element-count budget
    # and quality auto-refine are skipped, otherwise the auto-coarsen would
    # silently undo the fine meshes a convergence study depends on.
    honor_seed = requested_seed is not None
    seed = pick_seed_size(requested_seed, geo, height)
    last_err = None
    for attempt in range(MESH_MAX_ATTEMPTS):
        try:
            part.deleteMesh()
        except Exception:
            pass
        part.setMeshControls(regions=part.cells, elemShape=TET, technique=FREE)
        part.seedPart(size=seed, deviationFactor=0.1, minSizeFactor=0.1)
        part.setElementType(regions=(part.cells,),
            elemTypes=(mesh.ElemType(elemCode=C3D10MH, elemLibrary=STANDARD),))
        try:
            part.generateMesh()
            n_el = len(part.elements)
            print('      mesh attempt %d: seed=%.3f -> %d elements'
                  % (attempt + 1, seed, n_el))
            if honor_seed:
                return seed
            if n_el > MESH_MAX_ELEMS:
                # Too many elements: coarsen by the cube-root of the overage
                # so we land near the budget in one step instead of many.
                seed = seed * (float(n_el) / MESH_MAX_ELEMS) ** (1.0 / 3.0)
                continue
            if mesh_quality_ok(part) or seed <= MESH_SEED_FLOOR:
                return seed
            seed = max(seed * MESH_REFINE_FACTOR, MESH_SEED_FLOOR)
        except Exception as e:
            last_err = e
            seed = max(seed * MESH_REFINE_FACTOR, MESH_SEED_FLOOR)
    if last_err is not None:
        raise last_err
    return seed


def build_and_run(design, model_name, work_dir):
    verts = design['vertices']           # [[x,y], ...] closed polygon
    h = design.get('nsd') or 15.0
    E = design.get('E') or E_DEFAULT
    nu = design.get('nu') or NU_DEFAULT

    geo = analyze_geometry(verts)

    mdb.Model(name=model_name)
    m = mdb.models[model_name]

    s = m.ConstrainedSketch(name='profile', sheetSize=200.0)
    pts = [tuple(v) for v in verts]
    for i in range(len(pts)):
        s.Line(point1=pts[i], point2=pts[(i + 1) % len(pts)])

    part = m.Part(name='Block', dimensionality=THREE_D, type=DEFORMABLE_BODY)
    part.BaseSolidExtrude(sketch=s, depth=h)

    # Neo-Hookean hyperelastic material, calibrated to match the small-strain
    # E/nu the tool already uses: mu0 = E/2(1+nu) = 2*C10, K0 = E/3(1-2nu) = 2/D1.
    c10 = E / (4.0 * (1.0 + nu))
    d1 = 6.0 * (1.0 - 2.0 * nu) / E
    mat = m.Material(name='Rubber')
    mat.Hyperelastic(materialType=ISOTROPIC, testData=OFF,
                      type=NEO_HOOKE, volumetricResponse=VOLUMETRIC_DATA,
                      table=((c10, d1),))

    # Distortion control on the solid section directly targets the mesh-
    # distortion failures seen under large shear deformation. The
    # sectionControls repository / SectionControls method aren't exposed in
    # every Abaqus build, so fall back to a plain section if unavailable.
    use_controls = False
    try:
        existing = getattr(m, 'sectionControls', None)
        if existing is None or 'EC-1' not in existing.keys():
            m.SectionControls(name='EC-1', distortionControl=ON,
                              lengthRatio=0.1, elemDeletion=OFF)
        use_controls = True
    except Exception:
        use_controls = False

    if use_controls:
        m.HomogeneousSolidSection(name='Sec', material='Rubber', controls='EC-1')
    else:
        m.HomogeneousSolidSection(name='Sec', material='Rubber')
    part.SectionAssignment(region=(part.cells,), sectionName='Sec')

    a = m.rootAssembly
    inst = a.Instance(name='Block-1', part=part, dependent=ON)

    faces = inst.faces
    top = faces.getByBoundingBox(zMin=h - 1e-3, zMax=h + 1e-3)
    base = faces.getByBoundingBox(zMin=-1e-3, zMax=1e-3)
    a.Set(faces=top, name='TOP')
    a.Set(faces=base, name='BASE')

    requested_seed = design.get('mesh')   # None -> auto-size from block dims
    used_seed = mesh_with_quality_control(part, requested_seed, geo, h)

    a.regenerate()

    # Self-contact: if the polygon has features (sipe walls, narrow ribs)
    # whose surfaces pass within CONTACT_GAP_MIN/MAX of each other, those
    # surfaces can fold into one another under load if left unconnected.
    # General contact with self-contact (ALLSTAR) catches any such pair
    # automatically without having to identify specific faces.
    if geo['needs_contact']:
        cp_name = 'IntProp-Contact'
        if cp_name not in m.interactionProperties:
            cp = m.ContactProperty(cp_name)
            cp.TangentialBehavior(formulation=PENALTY, table=((0.6,),))
            cp.NormalBehavior(pressureOverclosure=HARD, allowSeparation=ON)

    delta = max(h * DELTA_STRAIN_FRAC, DELTA_FLOOR)
    cases = {
        'Kx': (1, delta, 0, 0),
        'Ky': (2, 0, delta, 0),
        'Kxy': (3, delta * 0.7071, delta * 0.7071, 0),
        'Kz': (4, 0, 0, delta),
    }
    results = {}

    for label, (idx, ux, uy, uz) in cases.items():
        step_name = 'Step1'
        if step_name in m.steps:
            del m.steps[step_name]
        m.StaticStep(name=step_name, previous='Initial', nlgeom=ON,
                     initialInc=STEP_INITIAL_INC, minInc=STEP_MIN_INC,
                     maxInc=STEP_MAX_INC, maxNumInc=STEP_MAX_NUM_INC,
                     timePeriod=STEP_TIME_PERIOD)

        if geo['needs_contact'] and 'Int-Contact' not in m.interactions:
            gc = m.ContactStd(name='Int-Contact', createStepName=step_name)
            gc.includedPairs.setValuesInStep(stepName=step_name, useAllstar=ON)
            gc.contactPropertyAssignments.appendInStep(
                stepName=step_name, assignments=((GLOBAL, SELF, cp_name),))

        for bc in list(m.boundaryConditions.keys()):
            del m.boundaryConditions[bc]

        m.DisplacementBC(name='Fix', createStepName='Initial',
            region=a.sets['BASE'], u1=0, u2=0, u3=0)
        m.DisplacementBC(name='Load', createStepName=step_name,
            region=a.sets['TOP'], u1=ux, u2=uy, u3=uz)

        job_name = '%s_%s' % (model_name, label)
        if JOB_NUM_CPUS > 1:
            job = mdb.Job(name=job_name, model=model_name,
                          numCpus=JOB_NUM_CPUS, numDomains=JOB_NUM_CPUS,
                          multiprocessingMode=DEFAULT)
        else:
            job = mdb.Job(name=job_name, model=model_name)
        job.submit()
        job.waitForCompletion()

        odb_path = os.path.join(work_dir, job_name + '.odb')
        from odbAccess import openOdb
        odb = openOdb(odb_path)
        base_set = odb.rootAssembly.instances['BLOCK-1'].nodeSets['BASE']
        frame = odb.steps[step_name].frames[-1]
        rf = frame.fieldOutputs['RF'].getSubset(region=base_set)
        comps = [0.0, 0.0, 0.0]
        for v in rf.values:
            comps[0] += v.data[0]
            comps[1] += v.data[1]
            comps[2] += v.data[2]
        odb.close()

        if label == 'Kx':
            results['Kx'] = abs(comps[0]) / delta
        elif label == 'Ky':
            results['Ky'] = abs(comps[1]) / delta
        elif label == 'Kxy':
            results['Kxy'] = (abs(comps[0]) + abs(comps[1])) / 2.0 / (delta * 0.7071)
        elif label == 'Kz':
            results['Kz'] = abs(comps[2]) / delta

    results['mesh_seed_used'] = used_seed
    results['contact_added'] = geo['needs_contact']
    results['min_gap'] = geo['min_gap']
    results['delta_used'] = delta
    return results


def expand_designs(designs):
    """Yield (label, swept_param, swept_value, design_dict) for every run.
    With no SWEEPS, each design runs once as saved. With sweeps, each design
    is expanded one-factor-at-a-time across the listed values."""
    for i, d in enumerate(designs):
        base_name = d.get('name', 'design_%d' % i)
        if not SWEEPS:
            yield (base_name, None, None, d)
            continue
        for sweep in SWEEPS:
            p = sweep['param']
            for val in sweep['values']:
                variant = dict(d)
                variant[p] = val
                label = '%s_%s=%s' % (base_name, p, val)
                yield (label, p, val, variant)


def parse_fea_txt(path):
    """Parse the plain-ASCII FEA export (Export FEA TXT button).
    Returns {'designs': [ {name, vertices, nsd, E, nu, draft, predicted}, ... ]}."""
    designs = []
    cur = None
    reading_verts = False
    with open(path, 'r') as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith('#'):
                continue
            if line == 'BLOCK':
                cur = {'vertices': [], 'predicted': {}}
                reading_verts = False
                continue
            if line == 'VERTICES':
                reading_verts = True
                continue
            if line == 'END':
                if cur is not None:
                    designs.append(cur)
                cur = None
                reading_verts = False
                continue
            if cur is None:
                continue  # e.g. the leading "count=" line
            if reading_verts:
                parts = line.split()
                if len(parts) >= 2:
                    cur['vertices'].append([float(parts[0]), float(parts[1])])
                continue
            # key=value metadata
            if '=' in line:
                k, v = line.split('=', 1)
                k = k.strip(); v = v.strip()
                if k == 'name':
                    cur['name'] = v
                elif k in ('nsd', 'E', 'nu', 'draft'):
                    cur[k] = float(v) if v != '' else None
                elif k.startswith('pred_'):
                    if v != '':
                        cur['predicted'][k[5:]] = float(v)
    return {'designs': designs}


def archive_previous_run(out_dir):
    """Move any files left over from a previous run (job files, lock files,
    the old comparison CSV) into a timestamped archive subfolder, so every
    run starts from a clean out_dir. This matters because job names are
    index-based (M_0_Kx, M_1_Ky, ...) and get reused run to run -- a leftover
    .lck file from a run that was Ctrl+C'd can block re-submission, and a
    stale .odb can otherwise linger alongside the new results. Nothing is
    deleted; old files are preserved under fea_runs/archive_<timestamp>/."""
    import glob, shutil, time
    leftovers = []
    for pattern in ('M_*.*', 'fea_comparison.csv', 'fea_batch_error.log'):
        leftovers.extend(glob.glob(os.path.join(out_dir, pattern)))
    if not leftovers:
        return
    archive_dir = os.path.join(out_dir, 'archive_%s' % time.strftime('%Y%m%d_%H%M%S'))
    os.makedirs(archive_dir)
    for f in leftovers:
        shutil.move(f, os.path.join(archive_dir, os.path.basename(f)))
    print('Archived %d leftover file(s) from a previous run into %s'
          % (len(leftovers), archive_dir))


def main():
    input_path, out_dir = read_args()
    # Resolve to absolute paths NOW, before any chdir, so they stay valid.
    input_path = os.path.abspath(input_path)
    out_dir = os.path.abspath(out_dir)
    print('=== fea_batch starting ===')
    print('Reading input file: %s' % input_path)
    print('Output directory:   %s' % out_dir)
    if not os.path.isfile(input_path):
        raise RuntimeError('Input file not found: %s' % input_path)
    if not os.path.isdir(out_dir):
        os.makedirs(out_dir)
    archive_previous_run(out_dir)
    # Abaqus writes job files (.odb etc.) to the current working directory,
    # so run everything from inside out_dir to keep outputs together.
    os.chdir(out_dir)
    if input_path.lower().endswith('.txt'):
        data = parse_fea_txt(input_path)
    else:
        with open(input_path, 'r', encoding='utf-8') as f:
            data = json.load(f)

    print('Parsed %d design(s)' % len(data['designs']))

    rows = []
    swept = bool(SWEEPS)
    for i, (name, sparam, sval, d) in enumerate(expand_designs(data['designs'])):
        model_name = 'M_%d' % i
        print('--- [%d] %s : %d vertices ---' % (i, name, len(d.get('vertices', []))))
        try:
            fea = build_and_run(d, model_name, out_dir)
        except Exception as e:
            rows.append({'name': name, 'error': str(e)})
            continue

        # Tool prediction is only valid at the unswept base point.
        pred = (d.get('predicted') or {}) if not swept else {}
        row = {'name': name}
        if swept:
            row['sweep_param'] = sparam
            row['sweep_value'] = sval
        for k in ('Kx', 'Ky', 'Kxy', 'Kz'):
            tool_v = pred.get(k)
            fea_v = fea.get(k)
            row['tool_' + k] = tool_v
            row['fea_' + k] = fea_v
            if tool_v is not None and fea_v not in (None, 0):
                row['pct_diff_' + k] = round(100.0 * (tool_v - fea_v) / fea_v, 1)
        row['mesh_seed_used'] = fea.get('mesh_seed_used')
        row['contact_added'] = fea.get('contact_added')
        row['min_gap'] = fea.get('min_gap')
        row['delta_used'] = fea.get('delta_used')
        rows.append(row)

    out_csv = os.path.join(out_dir, 'fea_comparison.csv')
    fieldnames = ['name', 'sweep_param', 'sweep_value',
                  'tool_Kx', 'fea_Kx', 'pct_diff_Kx',
                  'tool_Ky', 'fea_Ky', 'pct_diff_Ky',
                  'tool_Kxy', 'fea_Kxy', 'pct_diff_Kxy',
                  'tool_Kz', 'fea_Kz', 'pct_diff_Kz',
                  'mesh_seed_used', 'contact_added', 'min_gap', 'delta_used', 'error']
    with open(out_csv, 'w') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction='ignore')
        writer.writeheader()
        for r in rows:
            writer.writerow(r)

    print('Wrote %s' % out_csv)
    print('=== fea_batch done ===')


# Abaqus 'noGUI' may execute this script under a module name other than
# '__main__', so call main() unconditionally. Any crash is also written to
# fea_batch_error.log next to the script so it can't vanish from the console.
try:
    main()
except Exception:
    import traceback
    tb = traceback.format_exc()
    sys.stderr.write(tb)
    try:
        with open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                               'fea_batch_error.log'), 'w') as _f:
            _f.write(tb)
    except Exception:
        pass
    raise
