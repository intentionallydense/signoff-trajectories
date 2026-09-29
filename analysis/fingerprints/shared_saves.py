"""Can tool fingerprints tell apart agents that shared one name cookie? Tested on saves, whose author is known.

Every logged save that joins an export revision (page + second, editor label == log name, as exposure/build.py) and
belongs to a trajectory has a known author. Its fingerprint is `bust | order`: the cache-buster keys and the full
parameter order of the form post (e.g. `Save summary` vs `summary Save`, `minoredit`, `preview`).

Test: for each save in a session whose saves come from two or more trajectories (the exposure build's `ambiguous` and
`weak` tiers, plus any session with saves by 2+ trajectories), score each of those trajectories by how often it used
this fingerprint in its *other* saves (leave-one-out, add-0.5 smoothing over the fingerprints seen) and predict the
best. Compare with chance (1 / candidates). The same is repeated with only the cache-buster, and only the order.

    python3 analysis/fingerprints/shared_saves.py     # ~2 min -> out/shared_saves.json, out/shared_saves.txt, out/saves.tsv.gz
"""
import csv, gzip, json, sys
from collections import Counter, defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT / 'analysis' / 'human-verification'))
sys.path.insert(0, str(HERE))
import server  # noqa: E402
from profile import CLEAN, MONTHS, OUT, T, fingerprint  # noqa: E402

GAP = 1800


def page_key(title):  # as exposure/build.py
    return 'dse~' + ''.join(c if c.isascii() and (c.isalnum() or c in '_.-') else ''.join(f'~{b:02x}' for b in c.encode())
                            for c in title)


def main():
    A = server.Archive(ROOT / 'full-wiki-logs.zip', None)
    rows = [json.loads(l) for l in (ROOT / 'analysis/signoff-trajectories/trajectories.jsonl').open()]
    author = {e['rev']: r['id'] for r in rows for e in r['edits']}
    by_second = defaultdict(list)
    for pk, ids in A.pages.items():
        if pk.startswith('dse~'):
            for i in ids:
                by_second[(pk, T(A.revs[i]['time']))].append(i)

    saves = []  # (name, ts, traj, fp dict)
    for mo in MONTHS:
        with open_gz(CLEAN / f'requests_{mo}.tsv.gz') as f:
            for r in csv.DictReader(f, delimiter='\t', quoting=csv.QUOTE_NONE):
                if r['cls'] != 'save' or not r['name'] or not r['ts']:
                    continue
                ts = int(r['ts'])
                hit = [i for i in by_second.get((page_key(r['page']), ts), ()) if A.revs[i].get('label') == r['name']]
                trajs = {author[i] for i in hit if i in author}
                if len(trajs) == 1:
                    saves.append((r['name'], ts, trajs.pop(), {**fingerprint(r), 'page': r['page']}))

    with gzip.open(OUT / 'saves.tsv.gz', 'wt', newline='') as f:
        w = csv.writer(f, delimiter='\t', lineterminator='\n', quoting=csv.QUOTE_NONE, quotechar=None, escapechar='\\')
        w.writerow(['traj_id', 'name', 'ts', 'page', 'bust', 'order'])
        for name, ts, traj, fp in sorted(saves, key=lambda s: (s[2], s[1])):
            w.writerow([traj, name, ts, fp['page'], fp['bust'], fp['order']])

    # sessions per name (30 min gap), as the exposure build
    by_name = defaultdict(list)
    for s in saves:
        by_name[s[0]].append(s)
    groups = []
    for v in by_name.values():
        v.sort(key=lambda s: s[1])
        cur = [v[0]]
        for s in v[1:]:
            if s[1] - cur[-1][1] > GAP:
                groups.append(cur)
                cur = []
            cur.append(s)
        groups.append(cur)

    keys = {'bust|order': lambda fp: f"{fp['bust']}|{fp['order']}", 'bust': lambda fp: fp['bust'],
            'order': lambda fp: fp['order']}
    out = {'joined_saves': len(saves), 'trajectories': len({s[2] for s in saves})}
    for kname, kf in keys.items():
        prof = defaultdict(Counter)
        for s in saves:
            prof[s[2]][kf(s[3])] += 1
        vocab = len({kf(s[3]) for s in saves})
        n = correct = chance = ties = 0
        per_k = Counter()
        for g in groups:
            cands = {s[2] for s in g}
            if len(cands) < 2:
                continue
            for s in g:
                fpk = kf(s[3])
                score = {}
                for c in cands:
                    cnt = prof[c][fpk] - (1 if c == s[2] else 0)
                    tot = sum(prof[c].values()) - (1 if c == s[2] else 0)
                    score[c] = (cnt + 0.5) / (tot + 0.5 * vocab)
                best = max(score.values())
                top = [c for c, v in score.items() if v == best]
                n += 1
                chance += 1 / len(cands)
                correct += (s[2] in top) / len(top)  # ties split evenly
                ties += len(top) > 1
                per_k[len(cands)] += 1
        out[kname] = {'fingerprints': vocab, 'test_saves': n, 'accuracy': round(correct / n, 3) if n else None,
                      'chance': round(chance / n, 3) if n else None, 'tied': ties,
                      'candidates_per_save': dict(sorted(per_k.items()))}
    # how distinct are concurrent agents' dominant save fingerprints?
    prof = defaultdict(Counter)
    for s in saves:
        prof[s[2]][keys['bust|order'](s[3])] += 1
    dom = {t: c.most_common(1)[0][0] for t, c in prof.items()}
    purity = sorted(c[dom[t]] / sum(c.values()) for t, c in prof.items() if sum(c.values()) >= 3)
    out['save_fingerprint_purity_median_3plus_saves'] = round(purity[len(purity) // 2], 3) if purity else None
    out['distinct_dominant_save_fingerprints'] = len(set(dom.values()))
    out['largest_dominant_group'] = Counter(dom.values()).most_common(1)[0][1]
    (OUT / 'shared_saves.json').write_text(json.dumps(out, indent=1))
    (OUT / 'shared_saves.txt').write_text(json.dumps(out, indent=1) + '\n')
    print(json.dumps(out, indent=1))


def open_gz(p):
    return gzip.open(p, 'rt', newline='')


if __name__ == '__main__':
    main()
