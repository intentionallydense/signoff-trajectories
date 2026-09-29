"""Re-times PRE-SIGNAL exposure on edited pages. coined.py counts an adopter as exposed when the word was on a page
it edited, as the page stood at the edit. But agents write the post before loading the edit form and save about 1 s
later (analysis/fingerprints: median composition 1 s, 88% under 5 s), so the version under the save is not what the
post was written from. What the agent could have seen is the page at its earlier reads.

For each adopter counted exposed that way (`edited_page` or `same_page` in coined.py), this looks up its reads of
those pages in the exposure table (analysis/exposure/out/, browse/diff/edit_form, with the revision each read saw) at
least LAG seconds before the edit, and asks whether any showed the word:
  confirmed         a read of an exposing page showed the word before first use
  arrived_between   it read the page(s), but no read showed the word: it arrived after the last read
  no_named_read     no named read of the exposing page(s) before the edit: can't tell (most reads are unnamed)
LAG = 5 s (main) and 30 s (sensitivity). Reads in the last LAG seconds before a save can't have fed its text.
As in presignal_reads.py, results come twice: `validated` (own/private2 sessions, main) and `all_attributed`.

    python3 analysis/behavioral-norms/presignal_timing.py     # ~2 min -> presignal_timing.json
    PRESIGNAL_READS=browse,diff python3 ...                   # presignal_reads.py's read classes (overwrites the json)
"""
import json, os, re, sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from statistics import median

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
READS = set(os.environ.get('PRESIGNAL_READS', 'browse,diff,edit_form').split(','))
LAGS = (5, 30)
VIEWS = {'validated': table.VALIDATED, 'all_attributed': table.BASES}


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
    # (page key, seq) -> the revision body shows the word
    seq_shows = {(pk, str(A.revs[i]['seq'])): RX.search(A.revs[i]['body']) is not None
                 for pk, ids in A.pages.items() if pk.startswith('dse~') for i in ids}

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

    out = {'coined': datetime.utcfromtimestamp(first_use[origin][0]).isoformat() + 'Z', 'origin': origin,
           'adopters': len(first_use) - 1, 'exposure_table': str(EXPO.relative_to(ROOT) if EXPO.is_relative_to(ROOT) else EXPO.name)}
    for view, bases in VIEWS.items():
        reads = defaultdict(list)  # trajectory -> [(ts, page_key, shown)]
        forms = defaultdict(list)  # (trajectory, page_key) -> edit_form times
        for r in table.reads(EXPO, bases, READS):
            pk = r['page_key']
            if not pk.startswith('dse~'):
                continue
            reads[r['traj_id']].append((r['ts'], pk, bool(r['seen']) and seq_shows.get((pk, r['seen']), False)))
            if r['cls'] == 'edit_form':
                forms[(r['traj_id'], pk)].append(r['ts'])
        out[view] = summarise(first_use, origin, first_on_page, edits, reads, forms)
    (HERE / 'presignal_timing.json').write_text(json.dumps(out, indent=1))
    print(json.dumps({k: ({a: b for a, b in v.items() if a != 'per_adopter'} if isinstance(v, dict) else v)
                      for k, v in out.items()}, indent=1))


def summarise(first_use, origin, first_on_page, edits, reads, forms):
    out = {}
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
    return out


if __name__ == '__main__':
    main()
