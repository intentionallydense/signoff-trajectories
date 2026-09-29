"""Calibrates spread.py's variant tests with a trait that cannot be copied from page text: each trajectory's dominant
save fingerprint (cache-buster keys | parameter order of its save requests; analysis/fingerprints/out/saves.tsv.gz).
The fingerprint is stable within an agent (median purity 1.0) and not associated with family or start day, and agents
never see each other's requests. Any conformity it shows beyond the family x 6-hour null is therefore not copying, but
page-level homophily the strata don't absorb (e.g. the same model/harness working the same pages). Word-choice leads
should be read against it.

Each test mirrors a spread.py variant test with the fingerprint in place of the word:
- `clock task` / `clock wall` / `self-reference`: the agent's first edit using a variant of that table, compared with
  the authors of other posts on the page that used a variant of the table (past or future 12 h; union and nearest
  post). Same anchors and same visible posts as the word test.
- `any post`: the agent's first edit, compared with the authors of all other trajectory posts on the page, 12 h.
- `name (first page)`: the agent's first page, compared with the names signed there by others, as the name tests.
Fingerprint versions: `bust|order` (full), `order` alone (never appears in any post), `bust` alone (some posts show
`?x=UNIQUE` recipes, so bust is not strictly invisible).
Calibration (`random bust|order #k`): the dominant fingerprints shuffled across all agents, a trait of the same
diversity that is independent of everything. The permutation null excludes the agent from its own visible set but
not from the shuffle, so a diverse, purely individual trait scores *below* its null. The real fingerprint is read
against these, not against a ratio of 1.

    python3 analysis/behavioral-norms/fingerprint_null.py    # ~3 min -> fingerprint_null.json, fingerprint_null.txt
"""
import csv, gzip, json, random, sys
from bisect import bisect_left, bisect_right
from collections import Counter, defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT / 'analysis' / 'human-verification'))
sys.path.insert(0, str(HERE))
import server  # noqa: E402
from norms import SIGNOFF, novel_texts  # noqa: E402
from spread import BIN, CLOCK_TASK, CLOCK_WALL, H, PERMS, SELFREF, T, TRAJ, name_parts, variants  # noqa: E402

SAVES = ROOT / 'analysis/fingerprints/out/saves.tsv.gz'
REPS = 3
CAL_REPS = 20
KEYS = {'bust|order': lambda b, o: f'{b}|{o}', 'order': lambda b, o: o, 'bust': lambda b, o: b}


def perm_test(items, key):
    """As spread.py: items (stratum, own set, visible set); match = own ∩ visible nonempty; shuffle own in strata."""
    items = [i for i in items if i[1] and i[2]]
    obs = sum(bool(i[1] & i[2]) for i in items)
    by = defaultdict(list)
    for k, i in enumerate(items):
        by[i[0]].append(k)
    rng = random.Random(key)
    null = []
    for _ in range(PERMS):
        own = [i[1] for i in items]
        for ks in by.values():
            vals = [own[k] for k in ks]
            rng.shuffle(vals)
            for k, v in zip(ks, vals):
                own[k] = v
        null.append(sum(bool(o & i[2]) for o, i in zip(own, items)))
    mean = sum(null) / PERMS
    return {'agents': len(items), 'matches': obs, 'null_mean': round(mean, 1), 'excess': round(obs - mean, 1),
            'ratio': round(obs / mean, 2) if mean else None,
            'p_one_sided': round((1 + sum(n >= obs for n in null)) / (PERMS + 1), 4)}


def main():
    A = server.Archive(ROOT / 'full-wiki-logs.zip', None)
    rows = [json.loads(l) for l in TRAJ.open()]
    # agents are keyed by trajectory id: a split leaves two trajectories with one name
    name_of = {r['id']: r['name'] for r in rows}
    ids_of = defaultdict(list)
    for r in rows:
        ids_of[r['name']].append(r['id'])
    fam_of, author = {}, {}
    for r in rows:
        for e in r['edits']:
            author[e['rev']] = r['id']
            fam_of[e['rev']] = e.get('family') or e.get('family_inferred') or r['primary_family'] or 'none'

    prof = defaultdict(lambda: defaultdict(Counter))  # key -> name -> Counter
    with gzip.open(SAVES, 'rt', newline='') as f:
        for s in csv.DictReader(f, delimiter='\t', quoting=csv.QUOTE_NONE, escapechar='\\'):
            for k, fn in KEYS.items():
                prof[k][s['traj_id']][fn(s['bust'], s['order'])] += 1
    dom = {k: {n: c.most_common(1)[0][0] for n, c in p.items()} for k, p in prof.items()}
    # calibration: the same values shuffled across all agents, i.e. a trait of identical spread that is independent of
    # page, family and time. Its ratio is the test's built-in bias for a trait this diverse.
    rng = random.Random(7)
    for rep in range(REPS):
        names = sorted(dom['bust|order'])
        vals = [dom['bust|order'][n] for n in names]
        rng.shuffle(vals)
        dom[f'random bust|order #{rep + 1}'] = dict(zip(names, vals))

    touched = {A.revs[e['rev']]['page_key'] for r in rows for e in r['edits'] if e['rev'] in A.revs}
    novel = novel_texts(A, touched)
    pages = defaultdict(list)
    for pk in touched:
        for rid in A.pages[pk]:
            raw = novel[rid]
            pages[pk].append((T(A.revs[rid]['time']), author.get(rid), SIGNOFF.sub('', raw), set(SIGNOFF.findall(raw))))
    for v in pages.values():
        v.sort(key=lambda x: x[0])
    ptimes = {k: [x[0] for x in v] for k, v in pages.items()}

    def window(pk, t, name, lo, hi):
        return [x for x in pages[pk][bisect_left(ptimes[pk], t + lo):bisect_right(ptimes[pk], t + hi)]
                if x[1] != name and x[0] != t]

    seqs = {}
    for r in rows:
        es = []
        for e in r['edits']:
            if e['rev'] not in A.revs or not novel[e['rev']].strip():
                continue
            t = T(e['time'])
            es.append({'t': t, 'pk': A.revs[e['rev']]['page_key'], 'txt': SIGNOFF.sub('', novel[e['rev']]),
                       'stratum': (fam_of[e['rev']], int(t // BIN))})
        seqs[r['id']] = sorted(es, key=lambda x: x['t'])

    sides = {'past': (-12 * H, 0), 'future': (0, 12 * H + 1)}
    out = {'trajectories_with_fingerprint': len(dom['bust|order']),
           'distinct_dominant': {k: len(set(d.values())) for k, d in dom.items()}, 'tests': {}}
    tables = {'clock task': CLOCK_TASK, 'clock wall': CLOCK_WALL, 'self-reference': SELFREF, 'any post': None}
    for key, d in dom.items():
        for label, table in tables.items():
            res = {}
            for side, (lo, hi) in sides.items():
                items, near = [], []
                for name, sq in seqs.items():
                    if name not in d:
                        continue
                    first = next((e for e in sq if table is None or variants(table, e['txt'])), None)
                    if not first:
                        continue
                    posts = [p for p in window(first['pk'], first['t'], name, lo, hi)
                             if p[1] in d and (table is None or variants(table, p[2]))]
                    own = frozenset([d[name]])
                    items.append((first['stratum'], own, frozenset(d[p[1]] for p in posts)))
                    if posts:
                        near.append((first['stratum'], own, frozenset([d[(posts[-1] if side == 'past' else posts[0])[1]]])))
                res[side] = perm_test(items, f'{key}{label}{side}')
                res[side + '_nearest'] = perm_test(near, f'{key}{label}{side}n')
            out['tests'][f'{key} :: {label}'] = res
        res = {}
        for side, (lo, hi) in (('past', (-10 ** 9, 0)), ('future', (0, 12 * H + 1))):
            items = []
            for name, sq in seqs.items():
                if not sq or name not in d:
                    continue
                f = sq[0]
                if pages[f['pk']] and pages[f['pk']][0][1] == name:
                    continue
                signed = {i for p in window(f['pk'], f['t'], name, lo, hi) for n in p[3] if n != name_of[name]
                          for i in ids_of.get(n, ()) if i in d}
                items.append((f['stratum'], frozenset([d[name]]), frozenset(d[i] for i in signed)))
            res[side] = perm_test(items, f'{key}name{side}')
        out['tests'][f'{key} :: name (first page)'] = res

    # the same calibration for the word tests themselves. Each agent's trait is its own first-used variant set (or
    # name part), and the visible set is the union of the visible authors' traits (agent-level, unlike spread.py, which
    # reads the visible posts' words). `observed` uses the real traits. Each calibration rep shuffles the traits within
    # family x 6-hour strata (each agent's anchor stratum), keeping who works when and where but breaking any page-level
    # link: a world of personal habits with no copying. p = share of reps with a ratio >= observed.
    def calibrate(names, anchor, trait, visible_authors, key):
        strata = defaultdict(list)
        for n in names:
            strata[anchor(n)['stratum']].append(n)
        def run(d, tag):
            items = [(anchor(n)['stratum'], d[n], frozenset().union(*(d[a] for a in visible_authors(n, d))))
                     for n in names]
            return perm_test(items, tag)['ratio']
        obs = run({n: trait(n) for n in names}, key + 'obs')
        reps = []
        for rep in range(CAL_REPS):
            d = {}
            for ns in strata.values():
                vals = [trait(n) for n in ns]
                rng.shuffle(vals)
                d.update(zip(ns, vals))
            reps.append(run(d, f'{key}{rep}'))
        reps.sort()
        return {'observed_ratio': obs, 'calibration_median': reps[len(reps) // 2],
                'calibration_range': [reps[0], reps[-1]],
                'p': round((1 + sum(r >= obs for r in reps)) / (CAL_REPS + 1), 3)}

    calib = {}
    for label, table in (('6 clock vocabulary: task side', CLOCK_TASK), ('6 clock vocabulary: wall side', CLOCK_WALL),
                         ('3 self-reference noun', SELFREF)):
        firsts = {n: next((e for e in sq if variants(table, e['txt'])), None) for n, sq in seqs.items()}
        firsts = {n: e for n, e in firsts.items() if e}
        calib[label] = {}
        for side, (lo, hi) in sides.items():
            vis = lambda n, d, lo=lo, hi=hi: {p[1] for p in window(firsts[n]['pk'], firsts[n]['t'], n, lo, hi)
                                              if p[1] in d and variants(table, p[2])}
            calib[label][side] = calibrate(sorted(firsts), firsts.get, lambda n: variants(table, firsts[n]['txt']),
                                           vis, f'cal{label}{side}')
    starters = sorted(n for n, sq in seqs.items() if sq and not (pages[sq[0]['pk']] and pages[sq[0]['pk']][0][1] == n))
    for part in ('role noun', 'date position', 'org marker', 'trailing X'):
        calib['4 name: ' + part] = {}
        for side, (lo, hi) in (('past', (-10 ** 9, 0)), ('future', (0, 12 * H + 1))):
            vis = lambda n, d, lo=lo, hi=hi: {i for p in window(seqs[n][0]['pk'], seqs[n][0]['t'], n, lo, hi)
                                              for s in p[3] if s != name_of[n] for i in ids_of.get(s, ()) if i in d}
            calib['4 name: ' + part][side] = calibrate(starters, lambda n: seqs[n][0],
                                                       lambda n, part=part: frozenset([name_parts(name_of[n])[part]]),
                                                       vis, f'cal{part}{side}')
    out['word_calibration_ratios'] = calib

    # the word tests being calibrated, from spread.json
    sp = json.loads((HERE / 'spread.json').read_text())['variants']
    out['word_tests'] = {k: {s: {x: v[s][x] for x in ('agents', 'matches', 'null_mean', 'excess', 'p_one_sided')}
                             for s in ('past', 'future', 'past_nearest_post', 'future_nearest_post') if s in v}
                         for k, v in sp.items()}
    (HERE / 'fingerprint_null.json').write_text(json.dumps(out, indent=1))
    lines = []
    g = lambda r: f"{r['matches']:3}/{r['agents']:<3} null {r['null_mean']:6} x{r['ratio']} p={r['p_one_sided']}"
    for k, v in out['tests'].items():
        lines.append(f"{k:32} past {g(v['past'])}   future {g(v['future'])}")
        if 'past_nearest' in v:
            lines.append(f"{'  nearest':32} past {g(v['past_nearest'])}   future {g(v['future_nearest'])}")
    lines.append('word tests (spread.json):')
    for k, v in out['word_tests'].items():
        r = {s: {**x, 'ratio': round(x['matches'] / x['null_mean'], 2) if x['null_mean'] else None} for s, x in v.items()}
        lines.append(f"{k:32} past {g(r['past'])}   future {g(r['future'])}")
        c = out['word_calibration_ratios'].get(k)
        if c:
            h = lambda x: f"obs x{x['observed_ratio']} vs no-copy x{x['calibration_median']} {x['calibration_range']} p={x['p']}"
            lines.append(f"{'  agent-level, calibrated':32} past {h(c['past'])}   future {h(c['future'])}")
    (HERE / 'fingerprint_null.txt').write_text('\n'.join(lines) + '\n')
    print('\n'.join(lines))


if __name__ == '__main__':
    main()
