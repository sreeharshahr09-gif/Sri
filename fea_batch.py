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

import sys, os, json, csv
from abaqus import mdb, session
from abaqusConstants import *
import regionToolset

DELTA = 1.0          # mm, applied displacement for shear/normal cases
E_DEFAULT = 6.0       # MPa, used only if a design omits E
NU_DEFAULT = 0.49

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
SWEEPS = []


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

    out_dir = None
    for a in argv:
        if a == input_path:
            continue
        low = a.lower()
        if a.startswith('-') or 'nogui' in low or 'cae' in low \
                or low.endswith('.py') or low.endswith('.txt') or low.endswith('.json'):
            continue  # skip flags/executable/script/the input file itself
        out_dir = a
        break
    if not out_dir:
        out_dir = os.path.dirname(input_path) or '.'
    return input_path, out_dir


def build_and_run(design, model_name, work_dir):
    verts = design['vertices']           # [[x,y], ...] closed polygon
    h = design.get('nsd') or 15.0
    E = design.get('E') or E_DEFAULT
    nu = design.get('nu') or NU_DEFAULT

    mdb.Model(name=model_name)
    m = mdb.models[model_name]

    s = m.ConstrainedSketch(name='profile', sheetSize=200.0)
    pts = [tuple(v) for v in verts]
    for i in range(len(pts)):
        s.Line(point1=pts[i], point2=pts[(i + 1) % len(pts)])

    part = m.Part(name='Block', dimensionality=THREE_D, type=DEFORMABLE_BODY)
    part.BaseSolidExtrude(sketch=s, depth=h)

    m.Material(name='Rubber').Elastic(table=((E, nu),))
    m.HomogeneousSolidSection(name='Sec', material='Rubber')
    part.SectionAssignment(region=(part.cells,), sectionName='Sec')

    a = m.rootAssembly
    inst = a.Instance(name='Block-1', part=part, dependent=ON)

    faces = inst.faces
    top = faces.getByBoundingBox(zMin=h - 1e-3, zMax=h + 1e-3)
    base = faces.getByBoundingBox(zMin=-1e-3, zMax=1e-3)
    a.Set(faces=top, name='TOP')
    a.Set(faces=base, name='BASE')

    part.seedPart(size=design.get('mesh') or 1.0)
    part.setElementType(regions=(part.cells,),
        elemTypes=(mesh.ElemType(elemCode=C3D8H, elemLibrary=STANDARD),))
    part.generateMesh()

    a.regenerate()

    cases = {
        'Kx': (1, DELTA, 0, 0),
        'Ky': (2, 0, DELTA, 0),
        'Kxy': (3, DELTA * 0.7071, DELTA * 0.7071, 0),
        'Kz': (4, 0, 0, DELTA),
    }
    results = {}

    for label, (idx, ux, uy, uz) in cases.items():
        step_name = 'Step1'
        if step_name in m.steps:
            del m.steps[step_name]
        m.StaticStep(name=step_name, previous='Initial', nlgeom=OFF)

        for bc in list(m.boundaryConditions.keys()):
            del m.boundaryConditions[bc]

        m.DisplacementBC(name='Fix', createStepName='Initial',
            region=a.sets['BASE'], u1=0, u2=0, u3=0)
        m.DisplacementBC(name='Load', createStepName=step_name,
            region=a.sets['TOP'], u1=ux, u2=uy, u3=uz)

        job_name = '%s_%s' % (model_name, label)
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
            results['Kx'] = abs(comps[0]) / DELTA
        elif label == 'Ky':
            results['Ky'] = abs(comps[1]) / DELTA
        elif label == 'Kxy':
            results['Kxy'] = (abs(comps[0]) + abs(comps[1])) / 2.0 / (DELTA * 0.7071)
        elif label == 'Kz':
            results['Kz'] = abs(comps[2]) / DELTA

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
        rows.append(row)

    out_csv = os.path.join(out_dir, 'fea_comparison.csv')
    fieldnames = ['name', 'sweep_param', 'sweep_value',
                  'tool_Kx', 'fea_Kx', 'pct_diff_Kx',
                  'tool_Ky', 'fea_Ky', 'pct_diff_Ky',
                  'tool_Kxy', 'fea_Kxy', 'pct_diff_Kxy',
                  'tool_Kz', 'fea_Kz', 'pct_diff_Kz', 'error']
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
