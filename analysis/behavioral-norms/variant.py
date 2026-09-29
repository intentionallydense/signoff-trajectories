"""Runs the behavioral-norms suite on a trajectories build and writes the outputs to <out dir>, without touching the
scripts' default output location beside this file.

    python3 analysis/behavioral-norms/variant.py analysis/signoff-trajectories analysis/behavioral-norms/results
      -> <out>/norms.json, examples.json, spread.json, spread.txt, coined.json, coined.txt, presignal_reads.json,
         presignal_timing.json, saves.tsv.gz, shared_saves.json, fingerprint_null.json, fingerprint_null.txt, run.log

The scripts are imported, not copied. Their module constants TRAJ (input) and HERE (output directory) are pointed at the
variant before main() runs, so later edits to the scripts flow in. fingerprint_null reads a save table keyed by
trajectory id, so fingerprints/shared_saves.py is rerun first, against a stand-in root (<out>/_root, symlinks only) whose
analysis/signoff-trajectories/trajectories.jsonl is the variant's. Running the scripts directly on the same build gives
the same outputs, which is the check that the runner itself changes nothing.

Public release: the log-joined steps (presignal_reads, presignal_timing, shared_saves) read the cleaned operator request
logs from $REQUEST_LOGS (default analysis/request-logs/clean, which is not published; see analysis/request-logs/README.md).
Without them those steps are skipped, their shipped outputs stay as they are, and fingerprint_null reads the shipped
<out>/saves.tsv.gz.
"""
import contextlib, io, os, sys, time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT / 'analysis' / 'human-verification'))
sys.path.insert(0, str(ROOT / 'analysis' / 'fingerprints'))
sys.path.insert(0, str(HERE))


class Tee(io.TextIOBase):
    def __init__(self, *streams):
        self.streams = streams

    def write(self, s):
        for st in self.streams:
            st.write(s)
        return len(s)


def main():
    src, out = Path(sys.argv[1]).resolve(), Path(sys.argv[2]).resolve()
    traj = src / 'trajectories.jsonl' if src.is_dir() else src
    if out == HERE or out == ROOT / 'analysis/fingerprints/out':
        raise SystemExit('refusing to write over the live outputs')
    out.mkdir(parents=True, exist_ok=True)
    root = out / '_root'
    (root / 'analysis/signoff-trajectories').mkdir(parents=True, exist_ok=True)
    for link, target in ((root / 'full-wiki-logs.zip', ROOT / 'full-wiki-logs.zip'),
                         (root / 'analysis/signoff-trajectories/trajectories.jsonl', traj)):
        link.unlink(missing_ok=True)
        link.symlink_to(target)

    import norms
    norms.TRAJ, norms.HERE = traj, out
    import spread
    spread.TRAJ, spread.HERE = traj, out
    import coined, presignal_reads, presignal_timing  # take TRAJ from norms at import
    import profile as fp_profile  # analysis/fingerprints/profile.py, first on sys.path
    fp_profile.OUT = out
    import shared_saves
    shared_saves.ROOT, shared_saves.OUT = root, out
    import fingerprint_null
    fingerprint_null.SAVES, fingerprint_null.HERE = out / 'saves.tsv.gz', out
    for m in (coined, presignal_reads, presignal_timing):
        m.HERE = out
    assert all(m.TRAJ == traj for m in (norms, spread, coined, presignal_reads, presignal_timing, fingerprint_null))
    clean = Path(os.environ.get('REQUEST_LOGS') or ROOT / 'analysis/request-logs/clean').resolve()
    for m in (presignal_reads, presignal_timing, fp_profile, shared_saves):
        m.CLEAN = clean
    logged = {'presignal_reads', 'presignal_timing', 'shared_saves'} if not clean.is_dir() else set()

    steps = [('norms', norms, None), ('spread', spread, 'spread.txt'), ('coined', coined, 'coined.txt'),
             ('presignal_reads', presignal_reads, None), ('presignal_timing', presignal_timing, None),
             ('shared_saves', shared_saves, None), ('fingerprint_null', fingerprint_null, None)]
    with open(out / 'run.log', 'w') as log:
        print(f'trajectories: {traj.relative_to(ROOT)}\n', file=log)
        for name, mod, keep in steps:
            if name in logged:
                print(f'== {name} skipped: no request logs\n', file=log, flush=True)
                print(f'{name}: skipped (no request logs)', file=sys.stderr)
                continue
            t0 = time.time()
            buf = io.StringIO()
            print(f'== {name}', file=log, flush=True)
            with contextlib.redirect_stdout(Tee(buf, log)):
                mod.main()
            if keep:
                (out / keep).write_text(buf.getvalue())
            print(f'== {name} done in {time.time() - t0:.0f} s\n', file=log, flush=True)
            print(f'{name}: {time.time() - t0:.0f} s', file=sys.stderr)


if __name__ == '__main__':
    main()
