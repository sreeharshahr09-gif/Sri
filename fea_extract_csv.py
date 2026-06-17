# Run with: abaqus python fea_extract_csv.py -- <fea_runs_dir>
# (also works with: abaqus cae noGUI=fea_extract_csv.py -- <fea_runs_dir>)
#
# Pairs with fea_write_inp.py. After you've solved the M_<i>_Kx.inp /
# M_<i>_Ky.inp / M_<i>_Kxy.inp / M_<i>_Kz.inp jobs yourself (locally or on a
# server), point this script at the fea_runs folder. It:
#   - reads manifest.json (written by fea_write_inp.py) for per-design name,
#     delta, and the tool's predicted Kx/Ky/Kxy/Kz,
#   - auto-discovers whichever M_<i>_<case>.odb files actually exist in the
#     folder (you don't have to have solved all four cases, or all designs,
#     for this to produce a partial CSV),
#   - for each found ODB, sums RF on the BASE node set, computes the
#     stiffness, and (if WRITE_LOAD_HISTORY was on when the .inp was written)
#     writes the per-increment RF-vs-U history CSV from every frame,
#   - writes fea_comparison.csv next to manifest.json.
#
# This script does NOT need a CAE license -- only an Abaqus/Viewer or
# Abaqus/Standard post-processing license for odbAccess, so it can run even
# if your CAE token is unavailable.

import sys, os, glob, json, csv, math
from odbAccess import openOdb


def read_args():
    argv = sys.argv
    if '--' in argv:
        argv = argv[argv.index('--') + 1:]
    run_dir = argv[0] if argv else '.'
    return os.path.abspath(run_dir)


def find_set(root_assembly, name):
    """BASE/TOP are assembly-level sets (a.Set(...) in fea_write_inp.py), so
    they live under rootAssembly.nodeSets. Fall back to the instance for
    Abaqus builds that promote them there instead."""
    if name in root_assembly.nodeSets.keys():
        return root_assembly.nodeSets[name]
    return root_assembly.instances['BLOCK-1'].nodeSets[name]


def sum_rf(frame, region):
    rf = frame.fieldOutputs['RF'].getSubset(region=region)
    s = [0.0, 0.0, 0.0]
    for v in rf.values:
        s[0] += v.data[0]; s[1] += v.data[1]; s[2] += v.data[2]
    return s


def avg_u(frame, region):
    u = frame.fieldOutputs['U'].getSubset(region=region)
    s = [0.0, 0.0, 0.0]; n = 0
    for v in u.values:
        s[0] += v.data[0]; s[1] += v.data[1]; s[2] += v.data[2]; n += 1
    return [c / n for c in s] if n else s


def extract_case(odb_path, label, delta, write_history):
    """Opens one solved M_<i>_<case>.odb and returns (fea_value, error_or_None).
    Writes the RF-vs-U history CSV alongside the odb if write_history."""
    odb = openOdb(odb_path)
    try:
        ra = odb.rootAssembly
        base_set = find_set(ra, 'BASE')
        top_set = find_set(ra, 'TOP')
        # Single-step .inp from fea_write_inp.py -> exactly one step.
        step = list(odb.steps.values())[0]
        frames = step.frames
        comp_idx = {'Kx': 0, 'Ky': 1, 'Kz': 2, 'Kxy': None}[label]

        if write_history:
            hist_path = odb_path[:-4] + '_history.csv'
            with open(hist_path, 'w') as hf:
                hf.write('frame_time,U_driven_mm,RF_reaction_N,'
                         'U1,U2,U3,RF1,RF2,RF3\n')
                for fr in frames:
                    rs = sum_rf(fr, base_set); ua = avg_u(fr, top_set)
                    if comp_idx is None:
                        u_drv = math.hypot(ua[0], ua[1])
                        rf_rct = math.hypot(rs[0], rs[1])
                    else:
                        u_drv = ua[comp_idx]; rf_rct = rs[comp_idx]
                    hf.write('%g,%g,%g,%g,%g,%g,%g,%g,%g\n' % (
                        fr.frameValue, u_drv, rf_rct,
                        ua[0], ua[1], ua[2], rs[0], rs[1], rs[2]))
            print('      wrote %s (%d frames)' % (os.path.basename(hist_path), len(frames)))

        comps = sum_rf(frames[-1], base_set)
        if label == 'Kx':
            value = abs(comps[0]) / delta
        elif label == 'Ky':
            value = abs(comps[1]) / delta
        elif label == 'Kxy':
            value = (abs(comps[0]) + abs(comps[1])) / 2.0 / (delta * 0.7071)
        elif label == 'Kz':
            value = abs(comps[2]) / delta
        else:
            value = None
        return value, None
    except Exception as e:
        return None, str(e)
    finally:
        odb.close()


def main():
    run_dir = read_args()
    manifest_path = os.path.join(run_dir, 'manifest.json')
    if not os.path.isfile(manifest_path):
        raise RuntimeError(
            'manifest.json not found in %s -- run fea_write_inp.py first.' % run_dir)
    with open(manifest_path) as f:
        manifest = json.load(f)

    rows = []
    write_history = True  # matches WRITE_LOAD_HISTORY default in fea_write_inp.py

    for d in manifest['designs']:
        i = d['index']
        name = d['name']
        delta = d['delta']
        pred = d.get('predicted') or {}
        row = {'name': name}
        any_found = False
        for label in ('Kx', 'Ky', 'Kxy', 'Kz'):
            job_name = d['job_names'].get(label, 'M_%d_%s' % (i, label))
            odb_path = os.path.join(run_dir, job_name + '.odb')
            tool_v = pred.get(label)
            row['tool_' + label] = tool_v
            if not os.path.isfile(odb_path):
                row['fea_' + label] = None
                continue
            any_found = True
            print('[%d] extracting %s from %s' % (i, label, job_name))
            fea_v, err = extract_case(odb_path, label, delta, write_history)
            row['fea_' + label] = fea_v
            if err:
                row['error'] = (row.get('error', '') + ('; ' if row.get('error') else '') +
                                 '%s: %s' % (label, err))
            elif tool_v is not None and fea_v not in (None, 0):
                row['pct_diff_' + label] = round(100.0 * (tool_v - fea_v) / fea_v, 1)
        row['mesh_seed_used'] = d.get('mesh_seed_used')
        row['contact_added'] = d.get('contact_added')
        row['min_gap'] = d.get('min_gap')
        row['delta_used'] = delta
        if not any_found:
            row['error'] = (row.get('error', '') +
                             ('; ' if row.get('error') else '') + 'no .odb files found yet')
        rows.append(row)

    out_csv = os.path.join(run_dir, 'fea_comparison.csv')
    fieldnames = ['name',
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


# Abaqus 'noGUI'/'python' may execute this script under a module name other
# than '__main__', so call main() unconditionally rather than gating on it.
main()
