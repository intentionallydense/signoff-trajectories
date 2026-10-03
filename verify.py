"""Rebuilds the trajectories, the flat export, the verification cross-check and the behavioural analysis in a scratch copy of this repository and compares every output
with the committed file.

    python3 verify.py                 # trajectories + behavioural analysis (~4 min)
    python3 verify.py --build-only    # trajectories, flat export and verification cross-check only (a few seconds)
    REQUEST_LOGS=<dir> python3 verify.py   # also rebuild the exposure table and rerun the log-joined steps
                                           # (see analysis/request-logs/README.md)

Outputs must match byte for byte, with two exceptions: spread.json's "build" field (the input file's mtime) is ignored,
and saves.tsv.gz is compared decompressed (gzip headers carry a timestamp). Without request logs the log-joined outputs
(the exposure validation, coined_reads.json/.txt, presignal_reads.json, presignal_timing.json, shared_saves.json/.txt,
saves.tsv.gz) are not rerun, and are reported so. With them, the exposure table is rebuilt from the logs in the scratch
copy first; a local analysis/exposure/out is never used. It also checks that README.md quotes the full SHA-256 of
full-wiki-logs.zip.
"""
import gzip, hashlib, json, os, shutil, subprocess, sys, tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BUILD = 'analysis/signoff-trajectories'
RESULTS = 'analysis/behavioral-norms/results'
XCHECK = 'analysis/verification-check'
FLAT = 'analysis/flat-export'
FLAT_OUT = ['edits.csv', 'profiles.csv', 'checks.json']
BUILD_OUT = ['trajectories.jsonl', 'summary.json', 'review_queue.tsv']
TEXT_OUT = ['norms.json', 'examples.json', 'spread.json', 'spread.txt', 'coined.json', 'coined.txt',
            'fingerprint_null.json', 'fingerprint_null.txt']
LOG_OUT = ['coined_reads.json', 'coined_reads.txt', 'presignal_reads.json', 'presignal_timing.json', 'shared_saves.json',
           'shared_saves.txt', 'saves.tsv.gz']
EXPO_OUT = [('analysis/exposure/validate', 'validate.json')]


def same(a, b):
    if a.name == 'spread.json':
        strip = lambda p: {k: v for k, v in json.loads(p.read_text()).items() if k != 'build'}
        return strip(a) == strip(b)
    if a.suffix == '.gz':
        return gzip.decompress(a.read_bytes()) == gzip.decompress(b.read_bytes())
    return a.read_bytes() == b.read_bytes()


def main():
    build_only = '--build-only' in sys.argv
    logs = os.environ.get('REQUEST_LOGS')
    if logs and not Path(logs).is_dir():
        raise SystemExit(f'REQUEST_LOGS is not a directory: {logs}')
    failed = []
    zip_sha = hashlib.sha256((ROOT / 'full-wiki-logs.zip').read_bytes()).hexdigest()
    ok = zip_sha in (ROOT / 'README.md').read_text()
    print(f'{"ok  " if ok else "DIFF"}  README.md quotes sha256 {zip_sha} of full-wiki-logs.zip')
    if not ok:
        failed.append('README.md: full-wiki-logs.zip sha256')
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp) / 'repo'
        shutil.copytree(ROOT, work, ignore=shutil.ignore_patterns('.git', '__pycache__', '_root'))
        shutil.rmtree(work / 'analysis/exposure/out', ignore_errors=True)  # rebuilt from the logs below, or absent
        run = lambda *args: subprocess.run([sys.executable, *args], cwd=work, check=True, stdout=subprocess.DEVNULL,
                                           env={**os.environ, 'REQUEST_LOGS': str(Path(logs).resolve()) if logs else
                                                str(work / 'no-request-logs')})
        print('building trajectories ...', file=sys.stderr)
        run(f'{BUILD}/build.py')
        run(f'{XCHECK}/xcheck.py')
        run(f'{FLAT}/export.py')
        checks = [(BUILD, f) for f in BUILD_OUT] + [(XCHECK, 'xcheck.json')] + [(FLAT, f) for f in FLAT_OUT]
        if not build_only and logs:
            print('building the exposure table from the request logs ...', file=sys.stderr)
            run('analysis/exposure/build.py')
            run('analysis/exposure/validate/validate.py')
            checks += EXPO_OUT
        if not build_only:
            print('running the behavioural suite (a few minutes) ...', file=sys.stderr)
            run('analysis/behavioral-norms/variant.py', BUILD, RESULTS)
            checks += [(RESULTS, f) for f in TEXT_OUT + (LOG_OUT if logs else [])]
        for d, f in checks:
            ok = same(ROOT / d / f, work / d / f)
            print(f'{"ok  " if ok else "DIFF"}  {d}/{f}')
            if not ok:
                failed.append(f'{d}/{f}')
    if not build_only and not logs:
        print(f'not rerun (no REQUEST_LOGS): exposure table, validate.json, {", ".join(LOG_OUT)}')
    if failed:
        raise SystemExit(f'{len(failed)} output(s) differ')
    print('all compared outputs match')


if __name__ == '__main__':
    main()
