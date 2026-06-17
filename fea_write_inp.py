# Run with: abaqus cae noGUI=fea_write_inp.py -- input_file
#
# Generates standalone Kx/Ky/Kxy/Kz .inp files per design -- it does NOT
# submit or solve anything. Use this when you want to inspect, hand-edit, or
# submit the jobs yourself (locally or on a server), then run
# fea_extract_csv.py afterwards to build the comparison CSV from the
# resulting .odb files.
#
# input_file may be either:
#   - a project .json exported by the tool's "Save JSON" button, or
#   - a plain-ASCII .txt exported by the tool's "Export FEA TXT" button.
# The format is auto-detected from the file extension.
#
# Output (in a 'fea_runs' folder next to input_file):
#   M_<i>_Kx.inp, M_<i>_Ky.inp, M_<i>_Kxy.inp, M_<i>_Kz.inp   -- per design
#   manifest.json -- per-design metadata (name, delta, predicted Kx/Ky/Kxy/Kz,
#                     mesh_seed_used, contact_added, min_gap) that
#                     fea_extract_csv.py needs to turn solved .odb files back
#                     into a comparison CSV.
#
# To solve the jobs yourself, from inside fea_runs/:
#   abaqus job=M_0_Kx interactive cpus=4
#   abaqus job=M_0_Ky interactive cpus=4
#   abaqus job=M_0_Kxy interactive cpus=4
#   abaqus job=M_0_Kz interactive cpus=4
#   (repeat for every design index)
#
# Material: Neo-Hookean hyperelastic (large-strain), run with nlgeom=ON.
# Meshing/contact logic is identical to fea_batch.py.

import sys, os, json, math
from abaqus import mdb, session
from abaqusConstants import *
import regionToolset
import mesh

E_DEFAULT = 6.0       # MPa, used only if a design omits E
NU_DEFAULT = 0.49

DELTA_STRAIN_FRAC = 0.02   # applied displacement = 2% of block height (nsd)
DELTA_FLOOR = 0.02         # mm, floor on applied displacement

MESH_TARGET_ELEMS_ACROSS = 6.0
MESH_MAX_ELEMS = 40000
MESH_SEED_FLOOR = 0.3
MESH_SEED_CEIL_FRAC = 0.5
MESH_REFINE_FACTOR = 0.7
MESH_MAX_ATTEMPTS = 5
ASPECT_RATIO_LIMIT = 12.0

CONTACT_GAP_MIN = 0.1
CONTACT_GAP_MAX = 1.5

STEP_INITIAL_INC = 0.05
STEP_MIN_INC = 1.0e-6
STEP_MAX_INC = 0.25
STEP_MAX_NUM_INC = 1000
STEP_TIME_PERIOD = 1.0

# Field output written at every increment so the post-solve extraction can
# build the same RF-vs-U history CSVs as fea_batch.py.
WRITE_LOAD_HISTORY = True


def read_args():
    argv = sys.argv
    if '--' in argv:
        argv = argv[argv.index('--') + 1:]
    input_path = None
    for a in argv:
        if a.lower().endswith('.txt') or a.lower().endswith('.json'):
            input_path = a
            break
    if input_path is None:
        raise RuntimeError(
            'No .txt or .json input file found in arguments (sys.argv=%r). '
            'Run as: abaqus cae noGUI=fea_write_inp.py -- <file.txt>'
            % (sys.argv,))
    out_dir = os.path.join(os.path.dirname(os.path.abspath(input_path)), 'fea_runs')
    return input_path, out_dir


def _seg_seg_distance(p1, p2, p3, p4):
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

    return min(
        dist(closest_pt(p1, p2, p3), p3),
        dist(closest_pt(p1, p2, p4), p4),
        dist(closest_pt(p3, p4, p1), p1),
        dist(closest_pt(p3, p4, p2), p2),
    )


def analyze_geometry(verts):
    n = len(verts)
    pts = [tuple(v) for v in verts]
    edges = [(pts[i], pts[(i + 1) % n]) for i in range(n)]
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    bbox_w = max(xs) - min(xs)
    bbox_h = max(ys) - min(ys)
    min_edge_len = max(min(math.hypot(b[0]-a[0], b[1]-a[1]) for a, b in edges), 1e-6)

    needs_contact = False
    min_gap = None
    for i in range(n):
        for j in range(i + 1, n):
            if j == i or j == (i + 1) % n or i == (j + 1) % n:
                continue
            d = _seg_seg_distance(edges[i][0], edges[i][1], edges[j][0], edges[j][1])
            if min_gap is None or d < min_gap:
                min_gap = d
            if CONTACT_GAP_MIN <= d <= CONTACT_GAP_MAX:
                needs_contact = True

    feature_size = min_edge_len if min_gap is None else min(min_edge_len, max(min_gap, 1e-6))
    return {'min_feature': feature_size, 'needs_contact': needs_contact,
            'min_gap': min_gap, 'bbox_w': bbox_w, 'bbox_h': bbox_h}


def pick_seed_size(requested_seed, geo, height):
    char_dim = min(d for d in (geo['bbox_w'], geo['bbox_h'], height) if d > 1e-6)
    if requested_seed is not None:
        return max(requested_seed, MESH_SEED_FLOOR)
    seed = char_dim / MESH_TARGET_ELEMS_ACROSS
    seed = min(seed, char_dim * MESH_SEED_CEIL_FRAC)
    return max(seed, MESH_SEED_FLOOR)


def mesh_quality_ok(part):
    try:
        bad = part.verifyMeshQuality(criterion=ASPECT_RATIO,
                                      threshold=ASPECT_RATIO_LIMIT,
                                      thresholdType=ABOVE)
        return (len(bad) if bad is not None else 0) == 0
    except Exception:
        return True


def mesh_with_quality_control(part, requested_seed, geo, height):
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
            print('      mesh attempt %d: seed=%.3f -> %d elements' % (attempt + 1, seed, n_el))
            if honor_seed:
                return seed
            if n_el > MESH_MAX_ELEMS:
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


def build_model(design, model_name):
    """Builds the CAE model (geometry, material, mesh, contact) for one
    design. Returns (model, assembly, delta, geo) -- the per-case .inp
    writing is done separately in write_case_inp()."""
    verts = design['vertices']
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

    c10 = E / (4.0 * (1.0 + nu))
    d1 = 6.0 * (1.0 - 2.0 * nu) / E
    mat = m.Material(name='Rubber')
    mat.Hyperelastic(materialType=ISOTROPIC, testData=OFF,
                      type=NEO_HOOKE, volumetricResponse=VOLUMETRIC_DATA,
                      table=((c10, d1),))

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

    requested_seed = design.get('mesh')
    used_seed = mesh_with_quality_control(part, requested_seed, geo, h)

    a.regenerate()

    # Self-contact removed: 'needs_contact'/'min_gap' are still computed for
    # the manifest/CSV as diagnostic-only fields, but no ContactProperty/
    # ContactStd is created in the model.

    delta = max(h * DELTA_STRAIN_FRAC, DELTA_FLOOR)
    return m, a, delta, geo, used_seed


def write_case_inp(m, a, geo, delta, model_name, label, ux, uy, uz, out_dir):
    """Adds a single-step load case to model m and writes it out as its own
    standalone .inp (M_<model_name>_<label>.inp), without submitting it."""
    step_name = 'Step1'
    if step_name in m.steps:
        del m.steps[step_name]
    m.StaticStep(name=step_name, previous='Initial', nlgeom=ON,
                 initialInc=STEP_INITIAL_INC, minInc=STEP_MIN_INC,
                 maxInc=STEP_MAX_INC, maxNumInc=STEP_MAX_NUM_INC,
                 timePeriod=STEP_TIME_PERIOD)

    try:
        for fo in list(m.fieldOutputRequests.keys()):
            del m.fieldOutputRequests[fo]
        out_freq = 1 if WRITE_LOAD_HISTORY else LAST_INCREMENT
        m.FieldOutputRequest(name='F-Min', createStepName=step_name,
                              variables=('U', 'RF'), frequency=out_freq)
    except AttributeError:
        pass

    for bc in list(m.boundaryConditions.keys()):
        del m.boundaryConditions[bc]
    m.DisplacementBC(name='Fix', createStepName='Initial',
        region=a.sets['BASE'], u1=0, u2=0, u3=0)
    m.DisplacementBC(name='Load', createStepName=step_name,
        region=a.sets['TOP'], u1=ux, u2=uy, u3=uz)

    job_name = '%s_%s' % (model_name, label)
    job = mdb.Job(name=job_name, model=m.name)
    job.writeInput(consistencyChecking=OFF)
    print('      wrote %s.inp' % job_name)
    return job_name


def expand_designs(designs):
    for i, d in enumerate(designs):
        yield (d.get('name', 'design_%d' % i), d)


def parse_fea_txt(path):
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
                continue
            if reading_verts:
                parts = line.split()
                if len(parts) >= 2:
                    cur['vertices'].append([float(parts[0]), float(parts[1])])
                continue
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
    import glob, shutil, time
    leftovers = []
    for pattern in ('M_*.*', 'fea_comparison.csv', 'manifest.json', 'fea_write_inp_error.log'):
        leftovers.extend(glob.glob(os.path.join(out_dir, pattern)))
    if not leftovers:
        return
    archive_dir = os.path.join(out_dir, 'archive_%s' % time.strftime('%Y%m%d_%H%M%S'))
    os.makedirs(archive_dir)
    for f in leftovers:
        shutil.move(f, os.path.join(archive_dir, os.path.basename(f)))
    print('Archived %d leftover file(s) into %s' % (len(leftovers), archive_dir))


def main():
    input_path, out_dir = read_args()
    input_path = os.path.abspath(input_path)
    out_dir = os.path.abspath(out_dir)
    print('=== fea_write_inp starting ===')
    print('Reading input file: %s' % input_path)
    print('Output directory:   %s' % out_dir)
    if not os.path.isfile(input_path):
        raise RuntimeError('Input file not found: %s' % input_path)
    if not os.path.isdir(out_dir):
        os.makedirs(out_dir)
    archive_previous_run(out_dir)
    os.chdir(out_dir)

    if input_path.lower().endswith('.txt'):
        data = parse_fea_txt(input_path)
    else:
        with open(input_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
    print('Parsed %d design(s)' % len(data['designs']))

    manifest = {'designs': []}
    for i, (name, d) in enumerate(expand_designs(data['designs'])):
        model_name = 'M_%d' % i
        print('--- [%d] %s : %d vertices ---' % (i, name, len(d.get('vertices', []))))
        m, a, delta, geo, used_seed = build_model(d, model_name)
        cases = [
            ('Kx',  (delta, 0, 0)),
            ('Ky',  (0, delta, 0)),
            ('Kxy', (delta * 0.7071, delta * 0.7071, 0)),
            ('Kz',  (0, 0, delta)),
        ]
        job_names = {}
        for label, (ux, uy, uz) in cases:
            job_names[label] = write_case_inp(m, a, geo, delta, model_name,
                                                label, ux, uy, uz, out_dir)
        manifest['designs'].append({
            'index': i,
            'name': name,
            'delta': delta,
            'predicted': d.get('predicted') or {},
            'mesh_seed_used': used_seed,
            'contact_added': geo['needs_contact'],
            'min_gap': geo['min_gap'],
            'job_names': job_names,
        })

    manifest_path = os.path.join(out_dir, 'manifest.json')
    with open(manifest_path, 'w') as f:
        json.dump(manifest, f, indent=2)
    print('Wrote %s' % manifest_path)
    print('=== fea_write_inp done -- no jobs submitted ===')
    print('Submit jobs yourself, e.g.:')
    print('  abaqus job=M_0_Kx interactive cpus=4')
    print('then run: abaqus python fea_extract_csv.py -- %s' % out_dir)


try:
    main()
except Exception:
    import traceback
    tb = traceback.format_exc()
    sys.stderr.write(tb)
    try:
        with open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                               'fea_write_inp_error.log'), 'w') as _f:
            _f.write(tb)
    except Exception:
        pass
    raise
