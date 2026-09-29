"""Rebuilds the trajectories, the verification cross-check and the behavioural analysis in a scratch copy of this repository and compares every output
with the committed file.

    python3 verify.py                 # trajectories + behavioural analysis (~4 min)
    python3 verify.py --build-only    # trajectories and the verification cross-check only (a few seconds)
    REQUEST_LOGS=<dir> python3 verify.py   # also rerun the log-joined steps (see analysis/request-logs/README.md)

Outputs must match byte for byte, with two exceptions: spread.json's "build" field (the input file's mtime) is ignored,
and saves.tsv.gz is compared decompressed (gzip headers carry a timestamp). Without request logs the log-joined outputs
(presignal_reads.json, presignal_timing.json, shared_saves.json/.txt, saves.tsv.gz) are not rerun, and are reported so.
"""
import gzip, json, os, shutil, subprocess, sys, tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BUILD = 'analysis/signoff-trajectories'
RESULTS = 'analysis/behavioral-norms/results'
XCHECK = 'analysis/verification-check'
BUILD_OUT = ['trajectories.jsonl', 'summary.json', 'review_queue.tsv']
TEXT_OUT = ['norms.json', 'examples.json', 'spread.json', 'spread.txt', 'coined.json', 'coined.txt',
            'fingerprint_null.json', 'fingerprint_null.txt']
LOG_OUT = ['presignal_reads.json', 'presignal_timing.json', 'shared_saves.json', 'shared_saves.txt', 'saves.tsv.gz']


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
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp) / 'repo'
        shutil.copytree(ROOT, work, ignore=shutil.ignore_patterns('.git', '__pycache__', '_root'))
        run = lambda *args: subprocess.run([sys.executable, *args], cwd=work, check=True, stdout=subprocess.DEVNULL,
                                           env={**os.environ, 'REQUEST_LOGS': str(Path(logs).resolve()) if logs else
                                                str(work / 'no-request-logs')})
        print('building trajectories ...', file=sys.stderr)
        run(f'{BUILD}/build.py')
        run(f'{XCHECK}/xcheck.py')
        checks = [(BUILD, f) for f in BUILD_OUT] + [(XCHECK, 'xcheck.json')]
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
        print(f'not rerun (no REQUEST_LOGS): {", ".join(LOG_OUT)}')
    if failed:
        raise SystemExit(f'{len(failed)} output(s) differ')
    print('all compared outputs match')


if __name__ == '__main__':
    main()
