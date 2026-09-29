"""Pilot: did PRE-SIGNAL adopters *read* a page showing the word before first using it?

Joins the trajectories to the pseudonymised operator request logs (analysis/request-logs/clean/, never the raw logs)
by acting name. A trajectory's names are its own signoff plus the editor labels of its edits, dropping any label
shared with another trajectory. For every named browse/diff read of a /dse page, the page body current at the
read (last export revision at or before it, or `oldid` when the read names one) is checked for the word.

- adopters: trajectories whose novel text uses pre-signal (see coined.py); exposure is counted before first use.
- comparison: non-adopters active after the coinage, with exposure counted before their last edit.
Named reads are a lower bound on exposure (ROADMAP.md: most reads are unnamed), so "no read" is not proof of anything.

    python3 analysis/behavioral-norms/presignal_reads.py      # ~1-2 min -> presignal_reads.json
"""
import csv, gzip, json, re, sys
from bisect import bisect_right
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT / 'analysis' / 'human-verification'))
sys.path.insert(0, str(HERE))
import server  # noqa: E402
from norms import SIGNOFF, TRAJ, novel_texts  # noqa: E402

CLEAN = ROOT / 'analysis/request-logs/clean'
RX = re.compile(r'\bpre-?signal', re.I)
T = lambda s: datetime.fromisoformat(s.replace('Z', '+00:00')).timestamp()
READS = {'browse', 'diff'}
csv.field_size_limit(10 ** 8)


def main():
    A = server.Archive(ROOT / 'full-wiki-logs.zip', None)
    rows = [json.loads(l) for l in TRAJ.open()]
    novel = novel_texts(A)

    # page -> sorted [(time, rev seq, body shows the word)] for dse pages
    shows, seq_shows = {}, {}
    for pk, ids in A.pages.items():
        if not pk.startswith('dse~'):
            continue
        shows[pk] = [(T(A.revs[i]['time']), RX.search(A.revs[i]['body']) is not None) for i in ids]
        seq_shows[pk] = {A.revs[i]['seq']: RX.search(A.revs[i]['body']) is not None for i in ids}
    coined = min(t for v in shows.values() for t, s in v if s)

    # names -> trajectory (labels unique to one trajectory only)
    owners = defaultdict(set)
    for r in rows:
        owners[r['name']].add(r['id'])  # trajectories are keyed by id: split parts share a name
        for e in r['edits']:
            if e.get('label'):
                owners[e['label']].add(r['id'])
    name_to = {n: next(iter(o)) for n, o in owners.items() if len(o) == 1}

    first_use, last_edit, family = {}, {}, {}
    for r in rows:
        family[r['id']] = r['primary_family']
        for e in sorted(r['edits'], key=lambda e: e['time']):
            t = T(e['time'])
            last_edit[r['id']] = t
            if r['id'] not in first_use and RX.search(SIGNOFF.sub('', novel.get(e['rev'], ''))):
                first_use[r['id']] = t

    exposures = defaultdict(list)  # trajectory -> [(read time, page)] of reads showing the word
    reads = Counter()
    for month in ('2606', '2607'):
        with gzip.open(CLEAN / f'requests_{month}.tsv.gz', 'rt', newline='') as f:
            for row in csv.DictReader(f, delimiter='\t'):
                if row['cls'] not in READS or not row['name'] or row['name'] not in name_to:
                    continue
                traj = name_to[row['name']]
                pk = 'dse~' + row['page']
                if pk not in shows:
                    continue
                reads[traj] += 1
                t = float(row['ts'])
                if row['oldid'] and row['oldid'] in seq_shows[pk]:
                    shown = seq_shows[pk][row['oldid']]
                else:
                    v = shows[pk]
                    k = bisect_right([x[0] for x in v], t) - 1
                    shown = k >= 0 and v[k][1]
                if shown:
                    exposures[traj].append((t, row['page']))

    def exposed_before(name, t):
        return [x for x in exposures[name] if x[0] < t]

    adopters = sorted(first_use, key=first_use.get)
    origin = adopters[0]
    ad = {n: exposed_before(n, first_use[n]) for n in adopters[1:]}
    comp = [n for n in last_edit if n not in first_use and last_edit[n] > coined]
    cp = {n: exposed_before(n, last_edit[n]) for n in comp}
    out = {
        'coined': datetime.utcfromtimestamp(coined).isoformat() + 'Z', 'origin': origin,
        'adopters': len(ad), 'adopters_with_named_reads': sum(reads[n] > 0 for n in ad),
        'adopters_read_word_before_use': sum(bool(v) for v in ad.values()),
        'comparison_nonadopters': len(cp), 'comparison_with_named_reads': sum(reads[n] > 0 for n in cp),
        'comparison_read_word_before_last_edit': sum(bool(v) for v in cp.values()),
        'minutes_from_first_exposure_read_to_first_use': sorted(
            round((first_use[n] - v[0][0]) / 60, 1) for n, v in ad.items() if v),
        'carrier_pages_read_by_adopters': Counter(p for v in ad.values() for _, p in v[:1]).most_common(15),
        'cross_family_first_exposure': sum(
            1 for n, v in ad.items() if v and A.page_meta.get('dse~' + v[0][1], {}).get('page_family') != family[n]),
        'per_adopter': {n: {'family': family[n], 'first_use': datetime.utcfromtimestamp(first_use[n]).isoformat() + 'Z',
                            'named_reads': reads[n], 'first_exposure_page': v[0][1] if v else None}
                        for n, v in ad.items()},
    }
    (HERE / 'presignal_reads.json').write_text(json.dumps(out, indent=1))
    print(json.dumps({k: v for k, v in out.items() if k != 'per_adopter'}, indent=1))


if __name__ == '__main__':
    main()
