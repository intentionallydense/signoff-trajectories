"""Did PRE-SIGNAL adopters *read* a page showing the word before first using it?

Reads come from the exposure table (analysis/exposure/out/, see analysis/exposure/table.py): browse/diff reads in
attributed sessions, each with the revision it saw (`seen`). A read counts as exposure when that revision's body shows
the word. Results are given twice:
- `validated`: sessions of basis own/private2, the ones analysis/exposure/validate/ supports for claims (main result);
- `all_attributed`: every attributed session, private1 and exclusive included (sensitivity).

- adopters: trajectories whose novel text uses pre-signal (see coined.py); exposure is counted before first use.
- comparison: non-adopters active after the coinage, with exposure counted before their last edit.
Named reads are a lower bound on exposure (most reads are unnamed), so "no read" is not proof of anything.

    python3 analysis/behavioral-norms/presignal_reads.py      # ~1 min -> presignal_reads.json
"""
import json, re, sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT / 'analysis' / 'human-verification'))
sys.path.insert(0, str(ROOT / 'analysis' / 'exposure'))
sys.path.insert(0, str(HERE))
import server  # noqa: E402
import table  # noqa: E402
from norms import SIGNOFF, TRAJ, novel_texts  # noqa: E402

EXPO = ROOT / 'analysis/exposure/out'
RX = re.compile(r'\bpre-?signal', re.I)
T = lambda s: datetime.fromisoformat(s.replace('Z', '+00:00')).timestamp()
READS = {'browse', 'diff'}
VIEWS = {'validated': table.VALIDATED, 'all_attributed': table.BASES}


def main():
    A = server.Archive(ROOT / 'full-wiki-logs.zip', None)
    rows = [json.loads(l) for l in TRAJ.open()]
    novel = novel_texts(A)

    # (page key, seq) -> the revision body shows the word
    seq_shows = {(pk, str(A.revs[i]['seq'])): RX.search(A.revs[i]['body']) is not None
                 for pk, ids in A.pages.items() if pk.startswith('dse~') for i in ids}
    coined = min(T(A.revs[i]['time']) for pk, ids in A.pages.items() for i in ids if seq_shows.get((pk, str(A.revs[i]['seq']))))

    first_use, last_edit, family = {}, {}, {}
    for r in rows:
        family[r['id']] = r['primary_family']
        for e in sorted(r['edits'], key=lambda e: e['time']):
            t = T(e['time'])
            last_edit[r['id']] = t
            if r['id'] not in first_use and RX.search(SIGNOFF.sub('', novel.get(e['rev'], ''))):
                first_use[r['id']] = t

    out = {'coined': datetime.utcfromtimestamp(coined).isoformat() + 'Z', 'exposure_table': str(EXPO.relative_to(ROOT) if EXPO.is_relative_to(ROOT) else EXPO.name)}
    for view, bases in VIEWS.items():
        exposures = defaultdict(list)  # trajectory -> [(read time, page, page family)] of reads showing the word
        reads = Counter()
        for r in table.reads(EXPO, bases, READS):
            if not r['page_key'].startswith('dse~'):
                continue
            reads[r['traj_id']] += 1
            if r['seen'] and seq_shows.get((r['page_key'], r['seen'])):
                exposures[r['traj_id']].append((r['ts'], r['page'], r['family']))
        for v in exposures.values():
            v.sort()
        out[view] = summarise(first_use, last_edit, family, coined, exposures, reads)
    (HERE / 'presignal_reads.json').write_text(json.dumps(out, indent=1))
    print(json.dumps({k: ({a: b for a, b in v.items() if a != 'per_adopter'} if isinstance(v, dict) else v)
                      for k, v in out.items()}, indent=1))


def summarise(first_use, last_edit, family, coined, exposures, reads):
    def exposed_before(name, t):
        return [x for x in exposures[name] if x[0] < t]

    adopters = sorted(first_use, key=first_use.get)
    origin = adopters[0]
    ad = {n: exposed_before(n, first_use[n]) for n in adopters[1:]}
    comp = [n for n in last_edit if n not in first_use and last_edit[n] > coined]
    cp = {n: exposed_before(n, last_edit[n]) for n in comp}
    return {
        'origin': origin,
        'adopters': len(ad), 'adopters_with_named_reads': sum(reads[n] > 0 for n in ad),
        'adopters_read_word_before_use': sum(bool(v) for v in ad.values()),
        'comparison_nonadopters': len(cp), 'comparison_with_named_reads': sum(reads[n] > 0 for n in cp),
        'comparison_read_word_before_last_edit': sum(bool(v) for v in cp.values()),
        'minutes_from_first_exposure_read_to_first_use': sorted(
            round((first_use[n] - v[0][0]) / 60, 1) for n, v in ad.items() if v),
        'carrier_pages_read_by_adopters': Counter(x[1] for v in ad.values() for x in v[:1]).most_common(15),
        'cross_family_first_exposure': sum(
            1 for n, v in ad.items() if v and v[0][2] != family[n]),
        'per_adopter': {n: {'family': family[n], 'first_use': datetime.utcfromtimestamp(first_use[n]).isoformat() + 'Z',
                            'named_reads': reads[n], 'first_exposure_page': v[0][1] if v else None}
                        for n, v in ad.items()},
    }


if __name__ == '__main__':
    main()
