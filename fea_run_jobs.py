# Run with: python fea_run_jobs.py <fea_runs_dir>
# (plain Python -- NOT "abaqus python"/"abaqus cae". This just shells out to
# the 'abaqus job=...' command line, the same way you'd type it by hand.)
#
# Pairs with fea_write_inp.py. Submits every M_<i>_<case>.inp found via
# manifest.json one at a time (sequential queue, same as chaining
# 'abaqus job=... && abaqus job=... && ...' by hand), so you can start it
# once and walk away instead of typing each job individually.
#
# Already-solved cases (an .odb next to the .inp) are skipped, so it's safe
# to re-run after adding new designs or after a partial run was interrupted.
#
# After it finishes (or partway through, for whatever completed), run:
#   abaqus python fea_extract_csv.py -- <fea_runs_dir>

import sys, os, json, subprocess

CPUS = 4


def main():
    if len(sys.argv) < 2:
        raise SystemExit('Usage: python fea_run_jobs.py <fea_runs_dir>')
    run_dir = os.path.abspath(sys.argv[1])
    manifest_path = os.path.join(run_dir, 'manifest.json')
    if not os.path.isfile(manifest_path):
        raise SystemExit(
            'manifest.json not found in %s -- run fea_write_inp.py first.' % run_dir)
    with open(manifest_path) as f:
        manifest = json.load(f)

    jobs = []
    for d in manifest['designs']:
        for label in ('Kx', 'Ky', 'Kxy', 'Kz'):
            job_name = d['job_names'].get(label)
            if job_name and os.path.isfile(os.path.join(run_dir, job_name + '.inp')):
                jobs.append(job_name)

    print('Found %d job(s) to run in %s' % (len(jobs), run_dir))

    failed = []
    for i, job_name in enumerate(jobs):
        odb_path = os.path.join(run_dir, job_name + '.odb')
        if os.path.isfile(odb_path):
            print('[%d/%d] %s: already solved (.odb exists), skipping'
                  % (i + 1, len(jobs), job_name))
            continue
        print('[%d/%d] %s: submitting...' % (i + 1, len(jobs), job_name))
        result = subprocess.run(
            ['abaqus', 'job=%s' % job_name, 'interactive', 'cpus=%d' % CPUS],
            cwd=run_dir)
        if result.returncode != 0:
            print('[%d/%d] %s: FAILED (exit code %d)'
                  % (i + 1, len(jobs), job_name, result.returncode))
            failed.append(job_name)
        else:
            print('[%d/%d] %s: done' % (i + 1, len(jobs), job_name))

    print('=== fea_run_jobs done: %d/%d solved, %d failed ===' %
          (len(jobs) - len(failed), len(jobs), len(failed)))
    if failed:
        print('Failed jobs: %s' % ', '.join(failed))


if __name__ == '__main__':
    main()
