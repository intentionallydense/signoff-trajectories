"""Clock clashes within signoff trajectories (split candidates) and shared schedules across names (merge candidates).

Reads claims.jsonl from extract.py. Writes clashes.tsv, task_clashes.tsv, merge_candidates.tsv and check.json.

    python3 analysis/clock-consistency/check.py

A claim is usable when it has a round, is the signer's own or unmarked, is on the task clock or an unnamed one, and is
not worked out in the post or a now-reading. Rounds are keyed by tag: C (clothing) and G (grocery) stay apart, and R, Q, #, 'round'
count as one generic tag.

OFF turns off fixes made during calibration, to rerun the earlier rulesets (ablate.py): 'now' (now-readings count),
'unconnected' (any two families with claims are a task clash), 'compatible' (merges need a shared family, so
relay-only trajectories never merge), 'same_date' (always MERGE_MIN_KEYS), 'contested' (contested pairs count).
"""
import csv, json
from collections import defaultdict
from itertools import combinations
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
TRAJECTORIES = ROOT / 'analysis/signoff-trajectories/trajectories.jsonl'

REPORT_TOL = 20 * 60      # reports of one round: arrival, answer and deadline sit within the longest timer (18m04)
FORECAST_TOL = 3 * 3600   # forecasts get revised (OECD R2 went 20:07 -> 20:35 -> 20:52) but not by hours
MERGE_MIN_KEYS = 2        # shared exact own times, to the second, for a merge candidate
MERGE_MIN_KEYS_SAME_DATE = 1  # ... when the two names carry the same date tag (Sep30 / OpenAIIvySep30Helper)
DAY = 86400
OFF = set()


def usable(c):
    return (c['round'] is not None and c['owner'] in ('self', 'unmarked') and c['clock'] != 'other'
            and c['status'] != 'derived' and (c['event'] != 'now' or 'now' in OFF))  # "no R2 yet as of 03:54" is a now-reading


def tag(c):
    return c['round_tag'] if c['round_tag'] in ('C', 'G') else 'R'


def gap(a, b):
    d = abs(a - b) % DAY
    return min(d, DAY - d)


def tolerance(x, y):
    firm = lambda c: c['status'] == 'reported' and not c['qualified']
    return REPORT_TOL if firm(x) and firm(y) else FORECAST_TOL


def keyed(edit):
    out = defaultdict(list)
    for c in edit['claims']:
        if usable(c):
            out[(tag(c), c['round'])].append(c)
    return out


def compare(a, b):
    """(agreeing keys, disagreeing keys with the closest pair of values) for two edits' shared rounds. A round agrees
    when any pair of its claims is within tolerance."""
    ka, kb = keyed(a), keyed(b)
    agree, disagree = [], []
    for k in ka.keys() & kb.keys():
        pairs = [(gap(x['value_s'], y['value_s']), x, y) for x in ka[k] for y in kb[k]]
        if any(d <= tolerance(x, y) for d, x, y in pairs):
            agree.append(k)
        else:
            d, x, y = min(pairs, key=lambda p: p[0])
            disagree.append((k, x['raw'], y['raw'], d))
    return agree, disagree


def compatible(ta, tb):
    """Two trajectories can be one run unless both have family-page edits and share no family. Relay-only
    trajectories (OpenAINov28CVD) fit any family."""
    fa, fb = set(ta['families']), set(tb['families'])
    if 'compatible' in OFF:
        return bool(fa & fb)
    return not fa or not fb or bool(fa & fb)


def run(edits, meta):
    """edits: trajectory id -> claims.jsonl rows. Returns clashes, task clashes and merge candidates (contested ones
    included and marked)."""
    # split candidates: edit pairs of one trajectory whose shared rounds mostly disagree
    clashes, per_traj = [], defaultdict(list)
    for tid, es in edits.items():
        for a, b in combinations(es, 2):
            agree, disagree = compare(a, b)
            if len(disagree) > len(agree):
                row = {'trajectory': tid, 'rev_a': a['rev'], 'time_a': a['time'], 'rev_b': b['rev'], 'time_b': b['time'],
                       'agree': len(agree), 'disagree': len(disagree),
                       'rounds': ' '.join(f'{k[0]}{k[1]}:{x}/{y}' for k, x, y, _ in disagree),
                       'max_gap_min': round(max(d for *_, d in disagree) / 60)}
                clashes.append(row)
                per_traj[tid].append(row)

    # task clashes: own-round claims on pages of two or more families, where one family's claims agree with none from
    # the others. Agents cross-post their own schedule to other families' pages (the times then agree), so the page
    # family alone doesn't show a second task. Relay and other pages don't count.
    task_clashes = []
    for tid, es in edits.items():
        by_fam = defaultdict(list)
        for e in es:
            if e['family'] and any(usable(c) for c in e['claims']):
                by_fam[e['family']].append(e)
        if len(by_fam) < 2:
            continue
        apart = [f for f, mine in by_fam.items()
                 if not any(compare(a, b)[0] for a in mine for g, theirs in by_fam.items() if g != f for b in theirs)]
        if apart and 'unconnected' in OFF:
            apart = list(by_fam)
        if apart:
            task_clashes.append({'trajectory': tid, 'families': ' '.join(f'{f}={len(v)}' for f, v in by_fam.items()),
                                 'unconnected': ' '.join(apart)})

    # merge candidates: exact own times shared by two names in one task
    index = defaultdict(set)
    for tid, es in edits.items():
        for e in es:
            if {'copied', 'reencoded'} & set(e['flags']):
                continue
            for c in e['claims']:
                if usable(c) and c['precision'] == 'second' and c['status'] in ('reported', 'predicted') and not c['qualified']:
                    index[(tag(c), c['round'], c['raw'])].add((tid, e['rev']))
    shared = defaultdict(lambda: {'keys': set(), 'revs_a': set(), 'revs_b': set()})
    for key, hits in index.items():
        for (ta, ra), (tb, rb) in combinations(sorted(hits), 2):
            if ta == tb or not compatible(meta[ta], meta[tb]):
                continue
            s = shared[(ta, tb)]
            s['keys'].add(key)
            s['revs_a'].add(ra)
            s['revs_b'].add(rb)
    merges = []
    for (ta, tb), s in shared.items():
        same_date = bool(set(edits[ta][0]['own_tags']) & set(edits[tb][0]['own_tags']))
        if len(s['keys']) < (MERGE_MIN_KEYS_SAME_DATE if same_date and 'same_date' not in OFF else MERGE_MIN_KEYS):
            continue
        # a disagreeing shared round between the two argues against one run
        against = sum(len(compare(a, b)[1]) for a in edits[ta] for b in edits[tb])
        # contested: some shared round disagrees between the two (Apr27CohortHelperJun16 maps a Jun16 cohort onto
        # Apr27's times); listed for review, but not a merge candidate
        merges.append({'a': ta, 'b': tb, 'contested': against > 0 and 'contested' not in OFF, 'same_date_tag': same_date, 'shared_keys': len(s['keys']),
                       'edits_a': len(s['revs_a']), 'edits_b': len(s['revs_b']), 'disagreeing_pairs': against,
                       'times': ' '.join(f'{k[0]}{k[1]}@{k[2]}' for k in sorted(s['keys'], key=lambda k: (k[1], k[2])))})
    merges.sort(key=lambda m: (-m['shared_keys'], m['disagreeing_pairs']))
    return clashes, per_traj, task_clashes, merges


def main():
    meta = {t['id']: t for t in map(json.loads, open(TRAJECTORIES))}
    edits = defaultdict(list)
    for row in map(json.loads, open(HERE / 'claims.jsonl')):
        edits[row['trajectory']].append(row)
    clashes, per_traj, task_clashes, merges = run(edits, meta)
    for name, rows in (('clashes', clashes), ('task_clashes', task_clashes), ('merge_candidates', merges)):
        with open(HERE / f'{name}.tsv', 'w', newline='') as f:
            w = csv.DictWriter(f, list(rows[0]) if rows else ['none'], delimiter='\t')
            w.writeheader()
            w.writerows(rows)
    summary = {'trajectories': len(edits), 'with_a_clash': len(per_traj), 'clashing_edit_pairs': len(clashes),
               'task_clashes': len(task_clashes), 'merge_candidates': sum(not m['contested'] for m in merges),
               'contested_merges': sum(m['contested'] for m in merges),
               'thresholds': {'report_tol_s': REPORT_TOL, 'forecast_tol_s': FORECAST_TOL, 'merge_min_keys': MERGE_MIN_KEYS,
                              'merge_min_keys_same_date': MERGE_MIN_KEYS_SAME_DATE}}
    (HERE / 'check.json').write_text(json.dumps(summary, indent=1) + '\n')
    print(json.dumps(summary, indent=1))


if __name__ == '__main__':
    main()
