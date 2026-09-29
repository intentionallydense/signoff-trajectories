"""Checks the published trajectories against the verdicts of the human verification phase.

    python3 analysis/verification-check/xcheck.py      # -> xcheck.json

For each verdict: which trajectories now hold that revision, and does one of them match the dossier the verdict was
made on? A trajectory matches a dossier if the build tagged it with that dossier id (`matches_existing_profile`) or if
its name or a merged name is one of the dossier's names in `verified_profile_names.json`.
"""
import collections, json
from pathlib import Path

HERE = Path(__file__).resolve().parent
TRAJ = HERE.parent / 'signoff-trajectories'
verdicts = [json.loads(l) for l in open(HERE / 'verdicts.jsonl')]
rows = [json.loads(l) for l in open(TRAJ / 'trajectories.jsonl')]
names_of = collections.defaultdict(set)
for n, ids in json.loads((TRAJ / 'verified_profile_names.json').read_text()).items():
    for i in ids:
        names_of[i].add(n)
holders = collections.defaultdict(list)
for r in rows:
    for e in r['edits']:
        holders[e['rev']].append(r)


def matches(r, dossier):
    return dossier in r['matches_existing_profile'] or bool(({r['name']} | set(r.get('merged_names') or [])) & names_of[dossier])


out, detail = collections.Counter(), collections.defaultdict(list)
for v in verdicts:
    hs = holders[v['rev']]
    where = 'not_in_any_trajectory' if not hs else 'in_matching_trajectory' if any(matches(r, v['dossier']) for r in hs) \
        else 'in_other_trajectory'
    key = f"{v['verdict']}:{where}"
    out[key] += 1
    detail[key].append({**v, 'held_by': [r['id'] for r in hs]})
for k, n in sorted(out.items()):
    print(n, k)
(HERE / 'xcheck.json').write_text(json.dumps({'counts': dict(sorted(out.items())), 'detail': detail}, indent=1,
                                             ensure_ascii=False) + '\n')
