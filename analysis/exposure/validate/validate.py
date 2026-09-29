"""Validation of the exposure table's attributions: do a trajectory's attributed reads land on the pages it goes on to
use, more than other readers' reads (or its own reads of other pages) do?

If a basis of attribution is sound, reads credited to trajectory T in the window before T acts on page Q should hit Q
far more often than the reads of other trajectories active in the same window. If the reads really belong to someone
else, T's hit rate falls to the rate of those other readers. There is no ground truth, so this measures agreement
with later behaviour, not accuracy.

Targets (T, Q, t), first per (T, Q):
  mention  T's novel text at time t names an export page Q (a title of [A-Za-z0-9_], >= 10 chars, not a trajectory
           name or label). Q is not the page being edited, and T never edited Q before t.
  quote    T's novel text at t shares an 8-word shingle with novel text that appeared on page Q before t, written by
           someone else. The shingle appears (in novel text) on at most 3 pages in the whole export, so scaffold and
           task wording drop out. Q is not the page being edited, and T never edited Q before t.
  join     T's first edit of an existing page Q (Q's first revision predates t by >= 60 s). Reads from any session of T
           spanning a T edit of Q are dropped, so the save that attributes a session can't also supply its hit.
Bases (from out/name_sessions.tsv): `own` (session tier with own-name saves), `private2` (only private saves, 2+),
`private1` (a single private save), `exclusive`.

For each target and basis, with window [t - 24 h, t), and only when T has a page read of that basis in the window:
  hit          T has a read of Q (for `join`, T must also have a read outside the dropped sessions)
  decoy_reader mean hit of the other trajectories with a read of that basis in the window, excluding any that ever
               edit or mention Q (all families, and the same primary family)
  decoy_page   T's own hit rate on the same outcome's targets of other trajectories within +-6 h, same family,
               pages T never edits or mentions. It holds T's reading volume fixed

    python3 analysis/exposure/validate/validate.py    # ~10 s -> validate.json (stdout in validate.txt)
    python3 analysis/exposure/validate/validate.py --window 72 --out analysis/exposure/validate/validate_72h.json
"""
import csv, gzip, json, random, re, sys
from bisect import bisect_left
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
OUT = HERE.parent / 'out'
sys.path.insert(0, str(ROOT / 'analysis' / 'human-verification'))
sys.path.insert(0, str(ROOT / 'analysis' / 'behavioral-norms'))
import server  # noqa: E402
from norms import TRAJ, novel_texts  # noqa: E402

W = 24 * 3600  # --window HOURS overrides
PAGE_WINDOW = 6 * 3600
BASES = ['own', 'private2', 'private1', 'exclusive']
PAGE_READS = {'browse', 'diff', 'edit_form'}
TOKEN = re.compile(r'[A-Za-z0-9_]{10,}')
SHINGLE, SHINGLE_PAGES = 8, 3
WORD = re.compile(r'[a-z0-9]+')
T = lambda s: int(datetime.fromisoformat(s.replace('Z', '+00:00')).timestamp())
csv.field_size_limit(10 ** 8)


def page_key(title):  # as exposure/build.py
    return 'dse~' + ''.join(c if re.match(r'[A-Za-z0-9_.-]', c) else ''.join(f'~{b:02x}' for b in c.encode())
                            for c in title)


def edit_pk(p):
    return page_key(p.split('/', 1)[1]) if p.startswith('dse/') else None


def shingles(text):
    out = set()
    for line in text.split('\n'):
        w = WORD.findall(line.lower())
        out |= {' '.join(w[i:i + SHINGLE]) for i in range(len(w) - SHINGLE + 1)}
    return out


def any_in(ts, lo, hi):
    i = bisect_left(ts, lo)
    return i < len(ts) and ts[i] < hi


def main():
    global W
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument('--window', type=int, default=24, help='hours before the target (default 24)')
    ap.add_argument('--out', default=str(HERE / 'validate.json'))
    ap.add_argument('--extra', nargs='*', default=[], metavar='LABEL=FILE',
                    help='more attributed reads (net16/net16.py format); each `via` key becomes basis LABEL:<key>')
    a = ap.parse_args()
    W = a.window * 3600
    extra = [x.split('=', 1) for x in a.extra]
    A = server.Archive(ROOT / 'full-wiki-logs.zip', None)
    rows = [json.loads(l) for l in TRAJ.open()]
    fam = {r['id']: r['primary_family'] for r in rows}

    # ---- sessions and reads ----
    basis, spans = {}, defaultdict(list)
    with (OUT / 'name_sessions.tsv').open() as f:
        for s in csv.DictReader(f, delimiter='\t', quoting=csv.QUOTE_NONE):
            if s['tier'] == 'exclusive':
                b = 'exclusive'
            elif s['tier'] == 'session':
                k = dict(x.split(':') for x in s['evidence_kinds'].split(';') if x)
                b = 'own' if 'own' in k else 'private2' if int(k.get('private', 0)) >= 2 else 'private1'
            else:
                continue
            basis[s['session_id']] = b
            spans[s['traj_id']].append((T(s['start']), T(s['end']), s['session_id']))
    any_read = {b: defaultdict(list) for b in BASES}      # basis -> traj -> [ts]
    own_read = {b: defaultdict(list) for b in BASES}      # basis -> traj -> [(ts, session)]
    page_read = {b: defaultdict(list) for b in BASES}     # basis -> (traj, pk) -> [(ts, session)]
    with gzip.open(OUT / 'reads.tsv.gz', 'rt', newline='') as f:
        for r in csv.DictReader(f, delimiter='\t', quoting=csv.QUOTE_NONE):
            if r['cls'] not in PAGE_READS or not r['page_key']:
                continue
            b = basis[r['session_id']]
            ts = int(r['ts'])
            any_read[b][r['traj_id']].append(ts)
            own_read[b][r['traj_id']].append((ts, r['session_id']))
            page_read[b][(r['traj_id'], r['page_key'])].append((ts, r['session_id']))
    for label, path in extra:
        with gzip.open(path, 'rt', newline='') as f:
            for r in csv.DictReader(f, delimiter='\t', quoting=csv.QUOTE_NONE):
                if r['cls'] not in PAGE_READS or not r['page_key']:
                    continue
                b = f"{label}:{r['via'].split('/')[0]}"
                if b not in any_read:
                    BASES.append(b)
                    for d in (any_read, own_read, page_read):
                        d[b] = defaultdict(list)
                ts = int(r['ts'])
                any_read[b][r['traj_id']].append(ts)
                own_read[b][r['traj_id']].append((ts, ''))
                page_read[b][(r['traj_id'], r['page_key'])].append((ts, ''))
    for d in any_read.values():
        for v in d.values():
            v.sort()
    for d in list(page_read.values()) + list(own_read.values()):
        for v in d.values():
            v.sort()
    readers = {b: sorted(d) for b, d in any_read.items()}

    # ---- targets ----
    titles = {pk[4:]: pk for pk in A.pages if pk.startswith('dse~') and re.fullmatch(r'[A-Za-z0-9_]+', pk[4:])}
    first_rev = {pk: T(A.revs[ids[0]]['time']) for pk, ids in A.pages.items() if pk.startswith('dse~') and ids}
    agent_names = {r['name'] for r in rows} | {n for r in rows for n in r.get('merged_names') or []} \
        | {e['label'] for r in rows for e in r['edits'] if e.get('label')}
    edit_pks = {edit_pk(e['page']) for r in rows for e in r['edits']} - {None}
    dse = [pk for pk in A.pages if pk.startswith('dse~')]
    novel = novel_texts(A, dse)

    # quote index: 8-word shingle -> [(time, page, rev)] of every novel appearance, for shingles on <= 3 pages
    rev_pk = {rid: pk for pk in dse for rid in A.pages[pk]}
    appear = defaultdict(list)
    for rid, pk in rev_pk.items():
        for sh in shingles(novel.get(rid, '')):
            appear[sh].append((T(A.revs[rid]['time']), pk, rid))
    appear = {sh: v for sh, v in appear.items() if len({x[1] for x in v}) <= SHINGLE_PAGES}

    targets = {'mention': [], 'join': [], 'quote': []}
    touches = defaultdict(set)         # pk -> trajectories that ever edit or mention it
    edit_times = defaultdict(list)     # (traj, pk) -> [t]
    for r in rows:
        tid = r['id']
        mine_revs = {e['rev'] for e in r['edits']}
        mine_names = {r['name']} | set(r.get('merged_names') or []) | {e['label'] for e in r['edits'] if e.get('label')}
        seen_m, seen_q, edited = set(), set(), set()
        for e in sorted(r['edits'], key=lambda e: e['time']):
            pk, t = edit_pk(e['page']), T(e['time'])
            if pk is None:
                continue
            edit_times[(tid, pk)].append(t)
            touches[pk].add(tid)
            if pk not in edited and pk in first_rev and first_rev[pk] < t - 60:
                targets['join'].append((tid, pk, t))
            for m in sorted(set(TOKEN.findall(novel.get(e['rev'], '')))):
                q = titles.get(m)
                if q and m not in agent_names and q != pk and q not in edited and q not in seen_m:
                    seen_m.add(q)
                    touches[q].add(tid)
                    targets['mention'].append((tid, q, t))
            for sh in shingles(novel.get(e['rev'], '')):
                earlier = [x for x in appear.get(sh, ()) if x[0] < t - 60]
                if not earlier or any(x[2] in mine_revs or A.revs[x[2]].get('label') in mine_names for x in earlier):
                    continue  # nothing earlier, or T's own phrase
                for q in {x[1] for x in earlier} - {pk} - edited - seen_q:
                    seen_q.add(q)
                    touches[q].add(tid)
                    targets['quote'].append((tid, q, t))
            edited.add(pk)

    def dropped(tid, pk):
        """T's sessions spanning one of its edits of pk."""
        ts = edit_times.get((tid, pk), [])
        return {sid for a, z, sid in spans.get(tid, ()) if any(a <= t <= z for t in ts)}

    def hit(b, tid, pk, lo, hi, drop=()):
        v = page_read[b].get((tid, pk))
        if not v:
            return False
        i = bisect_left(v, (lo, ''))
        while i < len(v) and v[i][0] < hi:
            if v[i][1] not in drop:
                return True
            i += 1
        return False

    # ---- measure ----
    results, items = {}, {}
    for outcome, tg in targets.items():
        by_time = sorted(tg, key=lambda x: x[2])
        tts = [x[2] for x in by_time]
        for b in BASES:
            rec = []
            for tid, q, t in tg:
                lo = t - W
                drop = dropped(tid, q) if outcome == 'join' else set()
                v = own_read[b].get(tid, [])
                i = bisect_left(v, (lo, ''))
                while i < len(v) and v[i][0] < t and v[i][1] in drop:
                    i += 1
                if not (i < len(v) and v[i][0] < t):
                    continue  # T has no read of this basis in the window outside the dropped sessions
                h = hit(b, tid, q, lo, t, drop)
                dec_all, dec_fam = [], []
                for o in readers[b]:
                    if o == tid or o in touches[q] or not any_in(any_read[b][o], lo, t):
                        continue
                    x = hit(b, o, q, lo, t)
                    dec_all.append(x)
                    if fam.get(o) == fam.get(tid):
                        dec_fam.append(x)
                i, j = bisect_left(tts, t - PAGE_WINDOW), bisect_left(tts, t + PAGE_WINDOW)
                pages = {p for o, p, _ in by_time[i:j]
                         if o != tid and tid not in touches[p] and p != q and fam.get(o) == fam.get(tid)}
                dec_pg = [hit(b, tid, p, lo, t, drop) for p in pages]
                rec.append({'traj': tid, 'hit': h,
                            'dr': sum(dec_all) / len(dec_all) if dec_all else None,
                            'drf': sum(dec_fam) / len(dec_fam) if dec_fam else None,
                            'dp': sum(dec_pg) / len(dec_pg) if dec_pg else None})
            items[(outcome, b)] = rec
            results[f'{outcome}/{b}'] = summarise(rec)

    # pooled over outcomes
    for b in BASES:  # the two outcomes that discriminate (join doesn't: see README)
        results[f'mention+quote/{b}'] = summarise(items[('mention', b)] + items[('quote', b)])
    out = {'window_h': W // 3600, 'targets': {k: len(v) for k, v in targets.items()},
           'targets_trajectories': {k: len({x[0] for x in v}) for k, v in targets.items()}, 'results': results}
    Path(a.out).write_text(json.dumps(out, indent=1))
    print(f"targets: {out['targets']} over {out['targets_trajectories']} trajectories; window {W // 3600} h\n")
    print(f"{'outcome/basis':24} {'n':>5} {'traj':>5} {'hit':>6} | {'reader':>6} {'excess [95% CI]':>22} | "
          f"{'fam hit':>7} {'fam rd':>6} {'excess [95% CI]':>22} | {'pg hit':>6} {'page':>6} {'excess [95% CI]':>22}")
    for k, v in results.items():
        f = lambda c: f"{v[c]['excess']:+.3f} [{v[c]['ci'][0]:+.3f}, {v[c]['ci'][1]:+.3f}]" if v.get(c) else '-'
        g = lambda c, x: f"{v[c][x]:.3f}" if v.get(c) else '-'
        print(f"{k:24} {v['n']:>5} {v['trajectories']:>5} {v['hit']:>6.3f} | {g('reader', 'decoy'):>6} "
              f"{f('reader'):>22} | {g('reader_family', 'hit'):>7} {g('reader_family', 'decoy'):>6} "
              f"{f('reader_family'):>22} | {g('page', 'hit'):>6} {g('page', 'decoy'):>6} {f('page'):>22}")


def summarise(rec, n_boot=1000, seed=1):
    out = {'n': len(rec), 'trajectories': len({r['traj'] for r in rec}),
           'hit': sum(r['hit'] for r in rec) / len(rec) if rec else 0.0}
    for name, key in (('reader', 'dr'), ('reader_family', 'drf'), ('page', 'dp')):
        sub = [r for r in rec if r[key] is not None]
        if len(sub) < 10:
            continue
        by = defaultdict(list)
        for r in sub:
            by[r['traj']].append((r['hit'], r[key]))
        groups = list(by.values())

        def stat(gs):
            xs = [p for g in gs for p in g]
            return sum(h for h, _ in xs) / len(xs), sum(d for _, d in xs) / len(xs)
        h, d = stat(groups)
        rng = random.Random(seed)
        boots = sorted(a - b for a, b in (stat([rng.choice(groups) for _ in groups]) for _ in range(n_boot)))
        out[name] = {'n': len(sub), 'hit': h, 'decoy': d, 'excess': h - d, 'ratio': h / d if d else None,
                     'ci': [boots[int(0.025 * n_boot)], boots[int(0.975 * n_boot) - 1]]}
    return out


if __name__ == '__main__':
    main()
