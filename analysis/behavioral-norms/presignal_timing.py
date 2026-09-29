"""Re-times PRE-SIGNAL exposure on edited pages. coined.py counts an adopter as exposed when the word was on a page
it edited, as the page stood at the edit. But agents write the post before loading the edit form and save about 1 s
later (analysis/fingerprints: median composition 1 s, 88% under 5 s), so the version under the save is not what the
post was written from. What the agent could have seen is the page at its earlier reads.

For each adopter counted exposed that way (`edited_page` or `same_page` in coined.py), this looks up its named reads
of those pages (browse/diff/edit_form, unique names only, as presignal_reads.py) at least LAG seconds before the
edit, and asks whether any showed the word:
  confirmed         a read of an exposing page showed the word before first use
  arrived_between   it read the page(s), but no read showed the word: it arrived after the last read
  no_named_read     no named read of the exposing page(s) before the edit: can't tell (most reads are unnamed)
LAG = 5 s (main) and 30 s (sensitivity). Reads in the last LAG seconds before a save can't have fed its text.

    python3 analysis/behavioral-norms/presignal_timing.py     # ~2 min -> presignal_timing.json
    PRESIGNAL_READS=browse,diff python3 ...                   # presignal_reads.py's read classes (overwrites the json)
"""
import csv, gzip, json, re, sys
from bisect import bisect_right
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from statistics import median

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT / 'analysis' / 'human-verification'))
sys.path.insert(0, str(HERE))
import server  # noqa: E402
from norms import SIGNOFF, TRAJ, novel_texts  # noqa: E402

CLEAN = ROOT / 'analysis/request-logs/clean'
RX = re.compile(r'\bpre-?signal', re.I)
T = lambda s: datetime.fromisoformat(s.replace('Z', '+00:00')).timestamp()
import os
READS = set(os.environ.get('PRESIGNAL_READS', 'browse,diff,edit_form').split(','))
LAGS = (5, 30)
csv.field_size_limit(10 ** 8)


def page_key(title):  # as exposure/build.py
    return 'dse~' + ''.join(c if c.isascii() and (c.isalnum() or c in '_.-') else ''.join(f'~{b:02x}' for b in c.encode())
                            for c in title)


def main():
    A = server.Archive(ROOT / 'full-wiki-logs.zip', None)
    rows = [json.loads(l) for l in TRAJ.open()]
    novel = novel_texts(A)

    # coined.py's exposure: first revision whose novel text carries the word, per page
    first_on_page = {}
    for pk, ids in A.pages.items():
        for rid in ids:
            if RX.search(novel.get(rid, '')):
                first_on_page[pk] = T(A.revs[rid]['time'])
                break
    # what a page body showed at a time
    shows, seq_shows = {}, {}
    for pk, ids in A.pages.items():
        if pk.startswith('dse~'):
            shows[pk] = ([T(A.revs[i]['time']) for i in ids], [RX.search(A.revs[i]['body']) is not None for i in ids])
            seq_shows[pk] = {str(A.revs[i]['seq']): RX.search(A.revs[i]['body']) is not None for i in ids}

    def shown_at(pk, t, oldid=''):
        if oldid and oldid in seq_shows.get(pk, {}):
            return seq_shows[pk][oldid]
        ts, v = shows.get(pk, ([], []))
        k = bisect_right(ts, t) - 1
        return k >= 0 and v[k]

    owners = defaultdict(set)
    for r in rows:
        owners[r['name']].add(r['id'])  # trajectories are keyed by id: split parts share a name
        for e in r['edits']:
            if e.get('label'):
                owners[e['label']].add(r['id'])
    name_to = {n: next(iter(o)) for n, o in owners.items() if len(o) == 1}

    edits, first_use = defaultdict(list), {}
    for r in rows:
        for e in sorted(r['edits'], key=lambda e: e['time']):
            if e['rev'] not in A.revs:
                continue
            t, pk = T(e['time']), A.revs[e['rev']]['page_key']
            edits[r['id']].append((t, pk))
            if r['id'] not in first_use and RX.search(SIGNOFF.sub('', novel.get(e['rev'], ''))):
                first_use[r['id']] = (t, pk)
    origin = min(first_use, key=lambda n: first_use[n][0])

    reads = defaultdict(list)  # trajectory -> [(ts, page_key, shown)]
    forms = defaultdict(list)  # (trajectory, page_key) -> edit_form times
    for month in ('2606', '2607'):
        with gzip.open(CLEAN / f'requests_{month}.tsv.gz', 'rt', newline='') as f:
            for row in csv.DictReader(f, delimiter='\t', quoting=csv.QUOTE_NONE):
                if row['cls'] not in READS or not row['name'] or row['name'] not in name_to or not row['ts']:
                    continue
                traj, pk, t = name_to[row['name']], page_key(row['page']), float(row['ts'])
                if pk not in shows:
                    continue
                reads[traj].append((t, pk, shown_at(pk, t, row['oldid'])))
                if row['cls'] == 'edit_form':
                    forms[(traj, pk)].append(t)

    out = {'coined': datetime.utcfromtimestamp(first_use[origin][0]).isoformat() + 'Z', 'origin': origin,
           'adopters': len(first_use) - 1}
    per = {}
    for n, (t, pk0) in first_use.items():
        if n == origin:
            continue
        # coined.py: pages the adopter edited up to first use where the word was already there at the edit
        expo = {(te, p) for te, p in edits[n] if te <= t and first_on_page.get(p, 1e18) < te}
        per[n] = {'expo_pages': sorted({p for _, p in expo}), 'same_page': first_on_page.get(pk0, 1e18) < t,
                  'named_reads': len(reads[n])}
        for lag in LAGS:
            pg = {p for _, p in expo}
            prior = [x for x in reads[n] if x[1] in pg and any(x[0] <= te - lag for te, p in expo if p == x[1])]
            per[n][f'lag{lag}'] = 'none' if not expo else 'confirmed' if any(x[2] for x in prior) else \
                'arrived_between' if prior else 'no_named_read'
            # any page, any read before first use - lag (presignal_reads.py's measure, with the lag)
            per[n][f'any_read_showed_lag{lag}'] = any(x[2] and x[0] <= t - lag for x in reads[n])
        fs = [t - x for x in forms[(n, pk0)] if 0 < t - x <= 1800]
        per[n]['first_use_composition_s'] = min(fs) if fs else None
    exposed = [n for n, v in per.items() if v['expo_pages']]
    out['coined_py_edited_or_same_page'] = len(exposed)
    for lag in LAGS:
        out[f'lag{lag}'] = dict(Counter(per[n][f'lag{lag}'] for n in exposed))
        out[f'any_named_read_showed_word_lag{lag}'] = sum(v[f'any_read_showed_lag{lag}'] for v in per.values())
        out[f'confirmed_by_any_read_lag{lag}'] = sum(per[n][f'any_read_showed_lag{lag}'] for n in exposed)
    comp = [v['first_use_composition_s'] for v in per.values() if v['first_use_composition_s'] is not None]
    out['first_use_saves_with_form'] = len(comp)
    out['first_use_composition_s_median'] = median(comp) if comp else None
    out['first_use_composition_under_5s'] = sum(c <= 5 for c in comp)
    out['per_adopter'] = per
    (HERE / 'presignal_timing.json').write_text(json.dumps(out, indent=1))
    print(json.dumps({k: v for k, v in out.items() if k != 'per_adopter'}, indent=1))


if __name__ == '__main__':
    main()
