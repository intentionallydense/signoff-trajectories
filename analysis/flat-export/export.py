"""Flat export of the signoff trajectories: one row per revision in the wiki export, assigned or not, and one row per
profile, including the one-off signoffs that the two-edit floor keeps out of trajectories.jsonl.

  python3 analysis/flat-export/export.py    # about a minute -> edits.csv, profiles.csv, checks.json, signoff_trajectories.sqlite

Inputs (read-only): full-wiki-logs.zip, analysis/signoff-trajectories/{trajectories.jsonl, summary.json, reviews.json,
verified_profile_names.json} and build.py's own reading functions (read_edit, edit_record, Origins, aliases_of),
imported without running build.main. The signoff reading, flags and line text of edits outside trajectories are
therefore the build's, not a re-implementation. The build's outputs are not changed.

Profiles:
  S:<name>[#part]  a trajectory from trajectories.jsonl (two or more in-scope edits), kind = trajectory
  O:<name>         a one-off: a signoff name whose only in-scope edit is unplaced, kind = one_off. Its edits on other
                   pages join it as they would a trajectory (scope = other).

edits.status: assigned (in a trajectory) | one_off (in a one-off profile) | split_dropped (the part of a hand split that
fell below two edits) | unsigned (in scope, no signoff read) | out_of_scope (not on a family or relay page, no profile).

checks.json recomputes each count from the rows and compares it with summary.json and trajectories.jsonl; the script
stops on any miss. The sqlite file holds both tables; it is not committed (rebuild it with this script).
"""
import csv, json, re, sqlite3, sys
from collections import Counter, defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
BUILD = ROOT / 'analysis/signoff-trajectories'
sys.path.insert(0, str(BUILD))
import build  # noqa: E402  (module import only; main() is not run)

URL = re.compile(r'https?://|(?:^|[\s\[(])//[a-z0-9.-]+\.[a-z]{2,}/|\bwww\.[a-z0-9-]+\.', re.I)
EDIT_COLS = ['rev', 'time', 'page', 'seq', 'scope', 'family', 'family_inferred', 'label', 'status', 'profile_id',
             'profile_name', 'signoff_read', 'signed_as', 'signoff_rule', 'merge', 'split_part', 'other_signers',
             'copied', 'copied_from', 'multipost', 'reencoded', 'rule_signoff', 'line', 'text', 'n_fresh_lines',
             'n_urls', 'url_lines', 'has_url']
PROFILE_COLS = ['id', 'kind', 'name', 'name_from', 'primary_family', 'families', 'edits_in_scope', 'family_page_edits',
                'relay_page_edits', 'edits_elsewhere', 'first', 'last', 'span_hours', 'flags', 'flagged_edits',
                'alias_candidates', 'matches_existing_profile', 'split_part', 'review_decision', 'review_reason',
                'n_copied', 'n_with_url']


def main():
    fams = {f['family_id'] for f in json.loads(build.INVENTORY.read_text())}
    A = build.server.Archive(ROOT / 'full-wiki-logs.zip', None)
    origins = build.Origins(A)
    pf = {k: m['page_family'] for k, m in A.page_meta.items()}
    trajs = [json.loads(l) for l in (BUILD / 'trajectories.jsonl').read_text().splitlines()]
    summary = json.loads((BUILD / 'summary.json').read_text())
    reviews = json.loads((BUILD / 'reviews.json').read_text())['reviews']
    profile_names = json.loads((BUILD / 'verified_profile_names.json').read_text())
    split_names = {r['name'] for r in reviews if r['decision'] == 'split'}
    not_alias = {frozenset((r['name'], o)) for r in reviews if r['decision'] == 'not_alias' for o in r['of']}
    merges = {m['rev']: 'containment' for m in summary['containment_merges']}
    merges.update({m['rev']: 'alias' for m in summary['alias_merges']})

    placed = {}
    for t in trajs:
        for e in t['edits']:
            assert e['rev'] not in placed, e['rev']
            placed[e['rev']] = (t, e)

    # the build's own reading of every revision, before any merge, split or floor
    read = {}
    for rid, r in A.revs.items():
        fam = pf.get(r['page_key'])
        scope = 'family' if fam in fams else 'relay' if fam == build.RELAY else 'other'
        name, e = build.read_edit(A, origins, rid, scope, fam)
        read[rid] = (scope, fam, name, e)
    inscope_by_name = Counter(name for scope, _, name, _ in read.values() if name and scope != 'other')
    unplaced_inscope = Counter(name for rid, (scope, _, name, _) in read.items()
                               if name and scope != 'other' and rid not in placed)
    one_offs = {n for n, k in unplaced_inscope.items() if k == 1 and inscope_by_name[n] == 1 and n not in split_names}

    rows, odd = [], []
    for rid, (scope, fam, name, rec) in read.items():
        r = A.revs[rid]
        body = r['body'].split('\n')
        fresh = [body[i] for i in sorted(A.fresh_lines(r)) if i < len(body)]
        t, e = placed.get(rid, (None, None))
        pid = pname = None
        if t:
            status, pid, pname = 'assigned', t['id'], t['name']
        elif name in one_offs:
            status, pid, pname = 'one_off', 'O:' + name, name
        elif scope == 'other':
            status = 'out_of_scope'
        elif not name:
            status = 'unsigned'
        elif name in split_names:
            status = 'split_dropped'
        else:
            status = 'signed_other'
            odd.append(rid)
        if e is None:
            e = rec or build.edit_record(A, rid, scope, fam)
        signed_name = e.get('signed_as') or pname or name
        first = origins.first_seen(rid, e['text']) if signed_name and 'copied' in e['flags'] else None
        rows.append({
            'rev': rid, 'time': r['time'], 'page': r['page_id'], 'seq': int(r['seq']), 'scope': scope,
            'family': fam if scope == 'family' else None, 'family_inferred': e.get('family_inferred'),
            'label': r['label'], 'status': status, 'profile_id': pid, 'profile_name': pname, 'signoff_read': name,
            'signed_as': e.get('signed_as'), 'signoff_rule': e.get('signoff_rule'), 'merge': merges.get(rid),
            'split_part': t.get('part') if t else None, 'other_signers': ';'.join(e.get('other_signers', [])) or None,
            **{f: int(f in e['flags']) for f in ('copied', 'multipost', 'reencoded', 'rule_signoff')},
            'copied_from': first, 'line': e['line'], 'text': e['text'], 'flags': e['flags'],
            'n_fresh_lines': sum(bool(l.strip()) for l in fresh),
            'n_urls': sum(len(URL.findall(l)) for l in fresh), 'url_lines': sum(bool(URL.search(l)) for l in fresh),
        })
        rows[-1]['has_url'] = int(rows[-1]['n_urls'] > 0)
    rows.sort(key=lambda x: (x['time'], x['page'], x['seq']))

    by_profile = defaultdict(list)
    for x in rows:
        if x['profile_id']:
            by_profile[x['profile_id']].append(x)
    prows = []
    for t in trajs:
        es = [x for x in by_profile[t['id']] if x['scope'] != 'other']
        prows.append({
            **{k: t[k] for k in PROFILE_COLS if k in t and k not in ('families', 'flags', 'alias_candidates', 'matches_existing_profile')},
            'kind': 'trajectory', 'families': ';'.join(f'{k}:{v}' for k, v in t['families'].items()),
            'flags': ';'.join(t['flags']) or None, 'alias_candidates': ';'.join(t['alias_candidates']) or None,
            'matches_existing_profile': ';'.join(t['matches_existing_profile']) or None,
            'split_part': t.get('part'), 'review_decision': (t.get('review') or {}).get('decision'),
            'review_reason': (t.get('review') or {}).get('reason'),
            'n_copied': sum(x['copied'] for x in es), 'n_with_url': sum(x['has_url'] for x in es)})
    # one-offs, with the trajectory fields computed as build.main computes them
    alias_groups = defaultdict(set)
    for _, _, name, _ in read.values():
        if name:
            alias_groups[build.alias_key(name)].add(name)
    oprows = []
    for name in one_offs:
        xs = by_profile['O:' + name]
        (e,) = [x for x in xs if x['scope'] != 'other']
        aliases = build.aliases_of(name, alias_groups[build.alias_key(name)], not_alias)
        flags = [k for k, on in (('generic', build.generic(name)), ('alias', bool(aliases))) if on]
        oprows.append({
            'id': 'O:' + name, 'kind': 'one_off', 'name': name, 'name_from': 'signoff', 'primary_family': e['family'],
            'families': f"{e['family']}:1" if e['family'] else None, 'edits_in_scope': 1,
            'family_page_edits': int(e['scope'] == 'family'), 'relay_page_edits': int(e['scope'] == 'relay'),
            'edits_elsewhere': len(xs) - 1, 'first': e['time'], 'last': e['time'], 'span_hours': 0.0,
            'flags': ';'.join(flags) or None, 'flagged_edits': int(bool(e['flags'])),
            'alias_candidates': ';'.join(aliases) or None,
            'matches_existing_profile': ';'.join(profile_names.get(name, [])) or None,
            'n_copied': e['copied'], 'n_with_url': e['has_url']})
    oprows.sort(key=lambda p: (p['primary_family'] or '~', p['first'], p['id']))
    prows += oprows

    # checks against the build's own outputs; stop on any miss
    st = Counter((x['scope'], x['status']) for x in rows)
    unsigned = summary['unsigned_edits_left_out']
    checks = {
        'revisions': (len(rows), len(A.revs)),
        'trajectories': (sum(p['kind'] == 'trajectory' for p in prows), summary['trajectories']),
        'assigned_in_scope': (st['family', 'assigned'] + st['relay', 'assigned'], summary['edits_in_trajectories']),
        'assigned_out_of_scope': (st['other', 'assigned'], sum(t['edits_elsewhere'] for t in trajs)),
        'unsigned_family': (st['family', 'unsigned'], unsigned['family']),
        'unsigned_relay': (st['relay', 'unsigned'], unsigned['relay']),
        'out_of_scope_no_profile_unsigned': (sum(x['status'] == 'out_of_scope' and not x['signoff_read'] for x in rows), unsigned['other']),
        'one_off_profiles': (len(oprows), summary['single_edit_names_dropped']),  # counted after merges, as the build does
        'one_off_in_scope_edits': (st['family', 'one_off'] + st['relay', 'one_off'], len(oprows)),
        'merged_one_offs': (sum(bool(x['merge']) for x in rows), len(summary['containment_merges']) + len(summary['alias_merges'])),
        'split_dropped': (st['family', 'split_dropped'] + st['relay', 'split_dropped'],
                          sum(p['edits_in_scope'] for p in summary['reviews_applied']['split_parts'] if not p['kept'])),
        'signed_other': (len(odd), 0),
        'per_trajectory_edit_counts': (sum(len([x for x in by_profile[t['id']] if x['scope'] != 'other']) == t['edits_in_scope'] for t in trajs), len(trajs)),
        'family_page_signed': (sum(x['scope'] == 'family' and x['status'] != 'unsigned' for x in rows),
                               sum(v.get('signed', 0) for v in summary['family_page_edits_signed_vs_unsigned'].values())),
        'profile_ids_unique': (len({p['id'] for p in prows}), len(prows)),
    }
    bad = {k: v for k, v in checks.items() if v[0] != v[1]}
    status_by_scope = {f'{a}/{b}': n for (a, b), n in sorted(st.items())}
    (HERE / 'checks.json').write_text(json.dumps({'checks': checks, 'failed': bad, 'signed_other': odd,
                                                  'status_by_scope': status_by_scope}, indent=1) + '\n')
    if bad:
        raise SystemExit(f'checks failed: {bad}')

    for name, cols, data in (('edits', EDIT_COLS, rows), ('profiles', PROFILE_COLS, prows)):
        with open(HERE / f'{name}.csv', 'w', newline='', encoding='utf-8') as f:
            w = csv.DictWriter(f, fieldnames=cols, extrasaction='ignore', lineterminator='\n')
            w.writeheader()
            w.writerows(data)
    db = HERE / 'signoff_trajectories.sqlite'
    db.unlink(missing_ok=True)
    con = sqlite3.connect(db)
    for name, cols, data in (('edits', EDIT_COLS, rows), ('profiles', PROFILE_COLS, prows)):
        con.execute(f'CREATE TABLE {name} ({", ".join(cols)})')
        con.executemany(f'INSERT INTO {name} VALUES ({", ".join("?" * len(cols))})', [[d.get(c) for c in cols] for d in data])
    con.execute('CREATE UNIQUE INDEX edits_rev ON edits(rev)')
    con.execute('CREATE INDEX edits_profile ON edits(profile_id)')
    con.execute('CREATE UNIQUE INDEX profiles_id ON profiles(id)')
    con.commit()
    con.close()
    print(json.dumps({'profiles': dict(Counter(p['kind'] for p in prows)), 'status_by_scope': status_by_scope,
                      'checks': 'all pass'}, indent=1))


if __name__ == '__main__':
    main()
