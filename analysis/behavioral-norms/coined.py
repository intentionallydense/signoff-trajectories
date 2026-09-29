"""Spread of coined signal tokens. A token like STATE5-XX or C3-STATE is an arbitrary string that no task prompt
supplies, so another agent using it has almost certainly picked it up from someone. The question is by what route
and how far it travels. For each scheme:

- the originator (first trajectory to write it on a page where it was new);
- adopters: other trajectories, each at its first use;
- `exposed_same_page`: the scheme was already on the page (any earlier revision, any author) when the adopter
  first used it there;
- `exposed_edited_page`: it was on some page the adopter had edited before, as it stood at that edit;
- `requested`: the adopter's exposure included a request to use it ("please post STATE5-XX");
- `read`: the adopter has a read in the exposure table (analysis/exposure/out/, validated own/private2 sessions,
  browse/diff/edit_form) whose revision showed the token, at least LAG seconds before first use. Unlike the two
  edited-page routes, this also sees pages the adopter never edited;
- the families reached, and how many adopters work in a family other than the originator's.

coined.json / coined.txt hold the text routes only, so they need nothing but the export. The `read` route needs the
exposure table (a log-derived input) and goes to coined_reads.json / coined_reads.txt, written only when EXPO holds
the table. Adopters with no route of either kind must have seen the token through a read the table misses (most reads
are unnamed) or a channel invisible here.

    python3 analysis/behavioral-norms/coined.py   # -> coined.json, coined.txt (stdout); coined_reads.json, .txt

`uses()` is also what the signoff visualiser's spread view draws (signoff-trajectories/visualiser/export.py).
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
LAG = 5  # a read in the last seconds before a save can't have fed its text (presignal_timing.py)
T = lambda s: datetime.fromisoformat(s.replace('Z', '+00:00')).timestamp()
SCHEMES = {
    'STATE5-<xx> (sector #5 state)': r'\bSTATE5-[A-Z]{2}\b',
    'C<n>-STATE (clothing round state)': r'\bC\d-STATE\b',
    'G<n>-<xx> (grocery round state)': r'\bG\d-[A-Z]{2}\b',
    '<DATE>-R<n>-<KIND> counter key': r'\b[A-Z]{3}\d{1,2}-R\d-[A-Z]+',
    'R<n>OBSERVED-<x> counter key': r'\bR\dOBSERVED-',
    'PRE-SIGNAL / pre-signal': r'\bpre-?signal',
    'SLOW-TIER': r'\bSLOW-TIER\b',
    'TERMINATION-SAFE': r'\bTERMINATION-SAFE\b',
    'NO-SHOW': r'\bNO-SHOW\b',
    'ZZZ backup page': r'\bZZZ[A-Z]\w+',
}
CASELESS = {'PRE-SIGNAL / pre-signal'}
REQUEST = re.compile(r'please|use |post |signal |overwrite|append', re.I)


def load_reads(expo=None):
    """trajectory -> [(ts, page_key, seen seq)] from the exposure table's validated sessions."""
    out = defaultdict(list)
    for r in table.reads(expo or EXPO, table.VALIDATED, {'browse', 'diff', 'edit_form'}):
        if r['seen'] and r['page_key'].startswith('dse~'):
            out[r['traj_id']].append((r['ts'], r['page_key'], r['seen']))
    return out


def uses(A, rows, novel, reads=None):
    """Per scheme: users {trajectory id: (first-use time, page key, rev)}, the origin, the first time the scheme was on
    each page (any author), and one row per adopter with its exposure routes. Shared with the visualiser export.
    `reads` (load_reads()) adds the `read` route; without it `read` is None."""
    # agents are keyed by trajectory id: a split leaves two trajectories with one name
    fam_of_traj = {r['id']: r['primary_family'] for r in rows}
    edits = sorted(((T(e['time']), r['id'], e['rev']) for r in rows for e in r['edits'] if e['rev'] in A.revs))
    out = {}
    body_at = {(r['page_key'], str(r['seq'])): r['body'] for r in A.revs.values()} if reads is not None else {}
    for label, pat in SCHEMES.items():
        rx = re.compile(pat, 0 if label not in CASELESS else re.I)
        shows = {}  # (page key, seq) -> that revision's body carries the scheme

        def shown(pk, seq):
            k = (pk, seq)
            if k not in shows:
                b = body_at.get((pk, seq))
                shows[k] = b is not None and rx.search(b) is not None
            return shows[k]

        # first revision (any author) where the scheme is on each page, and its line if a request
        first_on_page, req_on_page = {}, defaultdict(list)
        for pk, ids in A.pages.items():
            for rid in ids:
                txt = novel.get(rid, '')
                for line in txt.split('\n'):
                    if rx.search(line):
                        first_on_page.setdefault(pk, T(A.revs[rid]['time']))
                        if REQUEST.search(line):
                            req_on_page[pk].append(T(A.revs[rid]['time']))
        users, seen_pages = {}, defaultdict(list)  # name -> first use (t, page, rev)
        for t, name, rid in edits:
            pk = A.revs[rid]['page_key']
            seen_pages[name].append((t, pk))
            if name not in users and rx.search(SIGNOFF.sub('', novel.get(rid, ''))):
                users[name] = (t, pk, rid)
        if not users:
            continue
        origin = min(users, key=lambda n: users[n][0])
        rows_out = []
        for name, (t, pk, _) in sorted(users.items(), key=lambda x: x[1][0]):
            if name == origin:
                continue
            same = first_on_page.get(pk, 1e18) < t
            edited = any(first_on_page.get(p, 1e18) < te <= t for te, p in seen_pages[name])
            requested = any(any(rt < t for rt in req_on_page.get(p, [])) for te, p in seen_pages[name] if te <= t)
            read = None if reads is None else any(ts <= t - LAG and shown(pk_, seq) for ts, pk_, seq in reads.get(name, ()))
            rows_out.append({'name': name, 'family': fam_of_traj[name], 'same_page': same, 'edited_page': edited,
                             'requested': requested, 'read': read})
        out[label] = {'users': users, 'origin': origin, 'first_on_page': first_on_page, 'adopters': rows_out}
    return out


def main():
    A = server.Archive(ROOT / 'full-wiki-logs.zip', None)
    rows = [json.loads(l) for l in TRAJ.open()]
    fam_of_traj = {r['id']: r['primary_family'] for r in rows}
    have_table = (EXPO / 'reads.tsv.gz').exists()
    out, out_reads = {}, {}
    for label, u in uses(A, rows, novel_texts(A), load_reads() if have_table else None).items():
        users, origin, rows_out = u['users'], u['origin'], u['adopters']
        n = len(rows_out)
        fams = Counter(fam_of_traj[x] for x in users)
        out[label] = {
            'users': len(users), 'origin': origin, 'origin_family': fam_of_traj[origin],
            'first_use': datetime.utcfromtimestamp(users[origin][0]).isoformat() + 'Z',
            'last_first_use': datetime.utcfromtimestamp(max(v[0] for v in users.values())).isoformat() + 'Z',
            'families': dict(fams.most_common()),
            'adopters': n,
            'adopters_outside_origin_family': sum(x['family'] != fam_of_traj[origin] for x in rows_out),
            'exposed_same_page': sum(x['same_page'] for x in rows_out),
            'exposed_edited_page': sum(x['edited_page'] or x['same_page'] for x in rows_out),
            'exposure_included_request': sum(x['requested'] for x in rows_out),
            'unexplained': [x['name'] for x in rows_out if not (x['same_page'] or x['edited_page'])],
        }
        if have_table:
            out_reads[label] = {
                'adopters': n,
                'read_before_use': sum(x['read'] for x in rows_out),
                'read_only': sum(x['read'] and not (x['same_page'] or x['edited_page']) for x in rows_out),
                'no_route': [x['name'] for x in rows_out if not (x['same_page'] or x['edited_page'] or x['read'])],
            }
    (HERE / 'coined.json').write_text(json.dumps(out, indent=1))
    for k, v in out.items():
        print(f"{k:36} users {v['users']:3} fams {len(v['families']):2} out-of-family {v['adopters_outside_origin_family']:2}/"
              f"{v['adopters']:<3} same-page {v['exposed_same_page']:3} any-edited {v['exposed_edited_page']:3} "
              f"request {v['exposure_included_request']:3}  origin {v['origin']} {v['first_use'][5:16]}"
              f"→{v['last_first_use'][5:16]}")
    if have_table:
        (HERE / 'coined_reads.json').write_text(json.dumps(
            {'exposure_table': 'validated sessions (own/private2), browse/diff/edit_form, read >= LAG s before first use',
             'lag_s': LAG, 'tokens': out_reads}, indent=1))
        (HERE / 'coined_reads.txt').write_text(''.join(
            f"{k:36} adopters {v['adopters']:3}  read {v['read_before_use']:3}  read-only {v['read_only']:3}  "
            f"no route {len(v['no_route']):3} (text routes alone: {len(out[k]['unexplained'])})\n"
            for k, v in out_reads.items()))


if __name__ == '__main__':
    main()
