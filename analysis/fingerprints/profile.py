"""Tool and timing fingerprints of agents, from the pseudonymised request logs (analysis/request-logs/clean/, never the raw
logs). Client and /24 hashes are not read.

A request's *tool fingerprint* is the way its URL was built, not what it asked for:
  host     site + script (wikiservice.at/wiki.cgi, prowiki.org/wiki2.cgi, ...)
  bust     the cache-buster keys: the cleaner's `cachebust` column plus nonce-like params (_cb, nonce, zz, r, t, ts)
  order    the sequence of parameter names with page-specific ones (id values, search terms) kept only as names:
           e.g. `action id lang`, `id action`, bare `browse`. Cache-buster keys are dropped from it
The *tool* of a request is host | bust | order-class, where order-class is the order of the always-present params
(action/id/lang/raw/username), so the same tool gives the same value whatever page it reads.

Profiles are built from validated sessions only (exposure build, bases `own` and `private2`: see
analysis/exposure/validate/). For each trajectory: its tool mix, the share of its requests with its dominant tool,
and how many other trajectories share that tool.

    python3 analysis/fingerprints/profile.py      # ~3 min -> out/profile.json, out/profile.txt, out/requests.tsv.gz
"""
import csv, gzip, json, statistics as st, sys
from bisect import bisect_right
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
CLEAN = ROOT / 'analysis/request-logs/clean'
EXPO = ROOT / 'analysis/exposure/out'
OUT = HERE / 'out'
MONTHS = ('2605', '2606', '2607')
NONCE = {'_cb', 'nonce', 'zz', 'r', 't', 'ts', 'rnd', 'rand', 'nocache', 'cachebust', 'x', 'cb', 'uniq', '_', 'z'}
CORE = {'action', 'id', 'lang', 'raw', 'username', 'browse'}
csv.field_size_limit(10 ** 9)
T = lambda s: int(datetime.fromisoformat(s.replace('Z', '+00:00')).timestamp())


def load_sessions(keep=('own', 'private2')):
    """name -> sorted [(start, end, session_id, traj, basis)] for sessions of the given bases."""
    by_name = defaultdict(list)
    with (EXPO / 'name_sessions.tsv').open() as f:
        for s in csv.DictReader(f, delimiter='\t', quoting=csv.QUOTE_NONE):
            if s['tier'] == 'session':
                k = dict(x.split(':') for x in s['evidence_kinds'].split(';') if x)
                b = 'own' if 'own' in k else 'private2' if int(k.get('private', 0)) >= 2 else 'private1'
            elif s['tier'] == 'exclusive':
                b = 'exclusive'
            else:
                b = s['tier']  # ambiguous / weak
            if b in keep:
                by_name[s['name']].append((T(s['start']), T(s['end']), s['session_id'], s['traj_id'] or s['candidates'],
                                           b))
    for v in by_name.values():
        v.sort()
    return by_name


def fingerprint(row):
    keys = [kv.split('=', 1)[0] for kv in row['query'].split('&') if kv]
    bust = sorted(set(row['cachebust'].split(',') if row['cachebust'] else []) | {k for k in keys if k in NONCE})
    order = [k for k in keys if k not in NONCE]
    core = [k for k in order if k in CORE]
    return {'host': f"{row['site']}/{row['script']}", 'bust': ','.join(bust) or '-',
            'order': ' '.join(order) or '-', 'core': ' '.join(core) or '-'}


def tool(fp):
    return f"{fp['host']}|{fp['bust']}|{fp['core']}"


def main():
    OUT.mkdir(exist_ok=True)
    sessions = load_sessions()
    starts = {n: [x[0] for x in v] for n, v in sessions.items()}

    def session_of(name, ts):
        v = sessions.get(name)
        if not v:
            return None
        i = bisect_right(starts[name], ts) - 1
        return v[i] if i >= 0 and v[i][0] <= ts <= v[i][1] else None

    prof = defaultdict(Counter)            # traj -> tool -> requests
    feat = {k: defaultdict(Counter) for k in ('host', 'bust', 'core')}
    by_session = defaultdict(Counter)      # session -> tool -> requests
    sess_traj = {}
    cols = ['traj_id', 'session_id', 'basis', 'ts', 'cls', 'action', 'page', 'host', 'bust', 'core', 'order']
    with gzip.open(OUT / 'requests.tsv.gz', 'wt', newline='') as fo:
        w = csv.writer(fo, delimiter='\t', lineterminator='\n', quoting=csv.QUOTE_NONE, quotechar=None, escapechar='\\')
        w.writerow(cols)
        for mo in MONTHS:
            with gzip.open(CLEAN / f'requests_{mo}.tsv.gz', 'rt', newline='') as f:
                for r in csv.DictReader(f, delimiter='\t', quoting=csv.QUOTE_NONE):
                    if not r['name'] or not r['ts']:
                        continue
                    s = session_of(r['name'], int(r['ts']))
                    if not s:
                        continue
                    fp = fingerprint(r)
                    tl = tool(fp)
                    prof[s[3]][tl] += 1
                    for k in feat:
                        feat[k][s[3]][fp[k]] += 1
                    by_session[s[2]][tl] += 1
                    sess_traj[s[2]] = s[3]
                    w.writerow([s[3], s[2], s[4], r['ts'], r['cls'], r['action'], r['page'], fp['host'], fp['bust'],
                                fp['core'], fp['order']])

    # ---- stability and distinctiveness ----
    dom = {t: c.most_common(1)[0][0] for t, c in prof.items()}
    purity = {t: c[dom[t]] / sum(c.values()) for t, c in prof.items()}
    sharing = Counter(dom.values())
    res = {'trajectories': len(prof), 'requests': sum(sum(c.values()) for c in prof.values()),
           'distinct_tools': len({tl for c in prof.values() for tl in c}),
           'distinct_dominant_tools': len(sharing),
           'dominant_purity_median': round(st.median(purity.values()), 3),
           'dominant_purity_p25': round(sorted(purity.values())[len(purity) // 4], 3),
           'trajectories_whose_dominant_tool_is_unique': sum(1 for t in dom if sharing[dom[t]] == 1),
           'top_dominant_tools': sharing.most_common(15)}
    for k, d in feat.items():
        dk = {t: c.most_common(1)[0][0] for t, c in d.items()}
        pk = [c[dk[t]] / sum(c.values()) for t, c in d.items()]
        res[f'feature_{k}'] = {'values': len({v for c in d.values() for v in c}),
                               'dominant_values': dict(Counter(dk.values()).most_common(12)),
                               'purity_median': round(st.median(pk), 3)}
    # session consistency: do an agent's sessions share its dominant tool?
    per_traj_sessions = defaultdict(list)
    for sid, c in by_session.items():
        if sum(c.values()) >= 5:
            per_traj_sessions[sess_traj[sid]].append(c.most_common(1)[0][0])
    same = [sum(x == dom[t] for x in v) / len(v) for t, v in per_traj_sessions.items() if len(v) >= 2]
    res['sessions_matching_traj_dominant_tool_median'] = round(st.median(same), 3) if same else None
    res['trajectories_with_2plus_sessions'] = len(same)
    (OUT / 'profile.json').write_text(json.dumps(
        {**res, 'per_trajectory': {t: {'dominant': dom[t], 'purity': round(purity[t], 3),
                                       'shared_with': sharing[dom[t]] - 1, 'tools': dict(c.most_common(5))}
                                   for t, c in prof.items()}}, indent=1))
    txt = json.dumps(res, indent=1)
    (OUT / 'profile.txt').write_text(txt + '\n')
    print(txt)


if __name__ == '__main__':
    main()
