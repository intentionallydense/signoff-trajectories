"""Exposure table: which signoff trajectory requested which /dse page, when, and which revision it saw.

Joins the pseudonymised operator request logs (analysis/request-logs/clean/, never the raw logs) to the signoff
trajectories (analysis/signoff-trajectories/trajectories.jsonl) through the log's `name` column (the username
cookie), and each read to the export revision current at that moment (full-wiki-logs.zip). See README.md.

    python3 analysis/exposure/build.py        # ~2 min -> out/  (--out <dir> to write elsewhere)

Attribution works on sessions: a name's requests, split wherever the gap exceeds 30 min.
- tier `session`: the session holds saves that join (page + second) to edits of exactly one trajectory.
- tier `exclusive`: the session holds no such save, but every trajectory-joined save under this name belongs to one
  trajectory (or to the parts of one split name), the name is not generic, and the session lies within that
  trajectory's span +- 24 h. For a split name, only the parts with a save under this name are candidates, and exactly
  one must fit: a split means two agents shared a signoff, not a login.
- sessions whose saves belong to two or more trajectories are `ambiguous` and attributed to nobody.
- reviews.json hand calls (`exclude` / `assign`, per name and time window) override, at the request level.
"""
import csv, gzip, importlib.util, json, os, re, sys, zipfile
from bisect import bisect_right
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
OUT = Path(sys.argv[sys.argv.index('--out') + 1]).resolve() if '--out' in sys.argv else HERE / 'out'
CLEAN = Path(os.environ.get('REQUEST_LOGS') or ROOT / 'analysis/request-logs/clean')  # $REQUEST_LOGS: the cleaned table elsewhere
TRAJ = ROOT / 'analysis/signoff-trajectories/trajectories.jsonl'
REVIEWS = HERE / 'reviews.json'
sys.path.insert(0, str(ROOT / 'analysis' / 'human-verification'))
sys.path.insert(0, str(ROOT / 'analysis' / 'unsigned-pages'))
import server  # noqa: E402
_spec = importlib.util.spec_from_file_location('signoff_build', ROOT / 'analysis/signoff-trajectories/build.py')
_sb = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_sb)
generic = _sb.generic  # signoff-trajectories' stock/bare-date name test

MONTHS = ('2605', '2606', '2607')  # 2604 carries no names
GAP = 1800
WINDOW = 24 * 3600
READ_CLS = {'browse', 'diff', 'edit_form', 'meta'}
PAGE_READ_CLS = {'browse', 'diff', 'edit_form'}  # for the per-page unnamed counts
csv.field_size_limit(10 ** 9)
T = lambda s: int(datetime.fromisoformat(s.replace('Z', '+00:00')).timestamp())
ISO = lambda t: datetime.fromtimestamp(t, timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def page_key(title):
    """Export page_key for a /dse title: characters outside [A-Za-z0-9_.-] become '~' + two lowercase hex digits."""
    return 'dse~' + ''.join(c if re.match(r'[A-Za-z0-9_.-]', c) else ''.join(f'~{b:02x}' for b in c.encode()) for c in title)


def base(tid):
    return tid.split('#')[0]


def load_reviews():
    if not REVIEWS.exists():
        return []
    calls = json.loads(REVIEWS.read_text()).get('reviews', [])
    for c in calls:
        if c['decision'] not in ('exclude', 'assign'):
            raise SystemExit(f"reviews.json: unknown decision {c['decision']}")
        c['_from'] = T(c['from']) if c.get('from') else -1
        c['_to'] = T(c['to']) if c.get('to') else 1 << 62
    return calls


def main():
    OUT.mkdir(exist_ok=True)
    A = server.Archive(ROOT / 'full-wiki-logs.zip', None)
    rows = [json.loads(l) for l in TRAJ.open()]
    traj = {r['id']: r for r in rows}
    rev_traj = {e['rev']: r['id'] for r in rows for e in r['edits']}
    owner = defaultdict(set)  # signoff name -> trajectories that sign with it (own name or merged-in names)
    for r in rows:
        for n in [r['name']] + (r.get('merged_names') or []):
            owner[n].add(r['id'])
    label_trajs = defaultdict(set)  # editor label -> trajectories whose edits carry it, joined to the log or not
    for rid, tid in rev_traj.items():
        if rid in A.revs:
            label_trajs[A.revs[rid]['label']].add(tid)
    span ={r['id']: (T(r['first']), T(r['last'])) for r in rows}
    first_post = {r['id']: min(T(e['time']) for e in r['edits']) for r in rows}
    reviews = load_reviews()
    known = set(traj)
    for c in reviews:
        if c['decision'] == 'assign' and c['traj'] not in known:
            raise SystemExit(f"reviews.json: no trajectory {c['traj']}")

    # export index for /dse
    revs_at = {}  # page_key -> ([epoch], [seq])
    by_second = defaultdict(list)  # (page_key, epoch) -> [rev_id]
    for pk, ids in A.pages.items():
        if not pk.startswith('dse~'):
            continue
        ts = [T(A.revs[i]['time']) for i in ids]
        order = sorted(range(len(ids)), key=lambda k: (ts[k], int(A.revs[ids[k]]['seq'])))
        revs_at[pk] = ([ts[k] for k in order], [A.revs[ids[k]]['seq'] for k in order])
        for i, t in zip(ids, ts):
            by_second[(pk, t)].append(i)
    deletes = defaultdict(list)
    with zipfile.ZipFile(ROOT / 'full-wiki-logs.zip') as z:
        for line in z.read('events.jsonl').splitlines():
            ev = json.loads(line)
            if ev['event_type'] == 'delete' and ev.get('page_key', '').startswith('dse~'):
                deletes[ev['page_key']].append(T(ev['time']))
    for v in deletes.values():
        v.sort()
    family = {pk: m.get('page_family') for pk, m in A.page_meta.items()}

    # ---- pass 1: named requests; unnamed read counts per page-hour ----
    named = defaultdict(list)
    page_hour = defaultdict(lambda: [0, 0, 0])  # unnamed, named, attributed
    for mo in MONTHS:
        with gzip.open(CLEAN / f'requests_{mo}.tsv.gz', 'rt', newline='') as f:
            for r in csv.DictReader(f, delimiter='\t', quoting=csv.QUOTE_NONE):
                if not r['ts']:
                    continue
                ts = int(r['ts'])
                if r['cls'] in PAGE_READ_CLS and r['page']:
                    page_hour[(r['page'], r['time'][:13])][1 if r['name'] else 0] += 1
                if r['name']:
                    named[r['name']].append((ts, r['cls'], r['action'], r['page'], r['oldid'], r['query'], r['time']))

    # ---- sessions and evidence ----
    sessions = []  # dicts
    for name, reqs in named.items():
        reqs.sort(key=lambda x: x[0])
        cur = None
        for q in reqs:
            if cur is None or q[0] - cur['end'] > GAP:
                cur = {'name': name, 'start': q[0], 'end': q[0], 'reqs': [], 'saves_by': [], 'foreign': Counter(), 'saves': 0}
                sessions.append(cur)
            cur['end'] = q[0]
            cur['reqs'].append(q)
            if q[1] == 'save':
                cur['saves'] += 1
                # a save is evidence only for the revision written under this name: several names often save
                # the same page in the same second (06-18 mass-save storm), so page + second alone mis-joins
                for rid in by_second.get((page_key(q[3]), q[0]), []):
                    if A.revs[rid]['label'] != name:
                        continue
                    tid = rev_traj.get(rid)
                    if tid:
                        cur['saves_by'].append(tid)
                    else:
                        sig, _ = A.last_signoff(rid)
                        if sig:
                            cur['foreign'][sig] += 1
    # classify each trajectory save by what the name is to the saver:
    #   own      the saver's own signoff name              -> attributes the session
    #   private  a non-signoff name only this saver used    -> attributes the session
    #   borrowed another trajectory's own signoff name      -> the cookie was shared: session goes to reads_shared
    #   shared   a name labelling edits of several trajectories (README), whether or not those saves join the log
    #                                                         -> likewise
    #   generic  a stock name (signoff-trajectories' generic()), even if only one saver used it: stock names are
    #            the ones agents fall back to, so one saver in the export doesn't make the cookie private -> likewise
    def kind(name, tid):
        if tid in owner.get(name, ()):
            return 'own'
        if owner.get(name):
            return 'borrowed'
        if generic(name):
            return 'generic'
        if len({base(t) for t in label_trajs[name]}) > 1:
            return 'shared'
        return 'private'

    weak_names = set()  # names some trajectory used without owning them: the cookie moved between agents
    for s in sessions:
        s['trajs'], s['weak'] = Counter(), set()
        for tid in s['saves_by']:
            k = kind(s['name'], tid)
            s.setdefault('kinds', Counter())[k] += 1
            if k in ('own', 'private'):
                s['trajs'][tid] += 1
            else:
                s['weak'] |= {tid} | owner.get(s['name'], set())
                weak_names.add(s['name'])
    per_name = Counter()
    for s in sessions:
        per_name[s['name']] += 1
        s['k'] = per_name[s['name']]
        s['id'] = f"{s['name']}#{s['k']}"

    strong_trajs = defaultdict(set)
    for s in sessions:
        strong_trajs[s['name']] |= set(s['trajs'])

    def exclusive_owner(name, s):
        ts = strong_trajs.get(name)
        if not ts or generic(name) or name in weak_names or len({base(t) for t in ts}) != 1:
            return None
        fits = [t for t in ts if span[t][0] - WINDOW <= s['start'] and s['end'] <= span[t][1] + WINDOW]
        return fits[0] if len(fits) == 1 else None

    for s in sessions:
        s['candidates'] = set(s['trajs']) | s['weak']
        if len(s['trajs']) == 1 and not (s['weak'] - set(s['trajs'])):
            s['verdict'], s['tier'] = next(iter(s['trajs'])), 'session'
        elif len(s['trajs']) > 1 or (s['trajs'] and s['weak'] - set(s['trajs'])):
            s['verdict'], s['tier'] = None, 'ambiguous'
        elif s['weak']:
            s['verdict'], s['tier'] = None, 'weak'
        else:
            own = exclusive_owner(s['name'], s)
            s['verdict'], s['tier'] = (own, 'exclusive') if own else (None, 'none')

    def review_for(name, ts):
        hit = [c for c in reviews if c['name'] == name and c['_from'] <= ts <= c['_to']]
        if len(hit) > 1:
            raise SystemExit(f'reviews.json: overlapping calls for {name} at {ISO(ts)}')
        return hit[0] if hit else None

    # ---- pass 2: rows ----
    def seen(pk, ts, query):
        """The operator's `oldid` names a redirect's source page, not a revision; old versions come as `revision=`."""
        if pk not in revs_at:
            return '', 'not_in_export'
        times, seqs = revs_at[pk]
        q = parse_qs(query, keep_blank_values=True)
        if 'revision' in q:  # not `diffrevision`, the base of a diff shown above the current page
            # an old version was asked for. `seq` equals the RCS 1.N number. A version that doesn't exist yet at the
            # read (agents probe e.g. revision=100) or not at all leaves what the reader saw unknown.
            rv = q['revision'][0].strip()
            n = int(rv) if rv.isdigit() else None
            if n in seqs and times[seqs.index(n)] <= ts:
                return n, 'revision'
            return '', 'revision_unresolved'
        k = bisect_right(times, ts) - 1
        if k < 0:
            return '', 'before_first_rev'
        d = deletes.get(pk, [])
        j = bisect_right(d, ts) - 1
        if j >= 0 and d[j] > times[k]:
            return '', 'deleted'
        return seqs[k], 'current'

    read_cols = ['traj_id', 'name', 'tier', 'session_id', 'ts', 'time', 'rel_first_post_s', 'cls', 'action', 'page',
                 'page_key', 'seen', 'seen_how', 'family']
    search_cols = ['traj_id', 'name', 'tier', 'session_id', 'ts', 'time', 'rel_first_post_s', 'action', 'terms', 'mode']
    counts = Counter()
    per_traj = defaultdict(Counter)
    shared_cols = ['candidates'] + read_cols[1:6] + read_cols[7:]
    with gzip.open(OUT / 'reads.tsv.gz', 'wt', newline='') as fr, gzip.open(OUT / 'searches.tsv.gz', 'wt', newline='') as fs, \
            gzip.open(OUT / 'reads_shared.tsv.gz', 'wt', newline='') as fx:
        wr = csv.writer(fr, delimiter='\t', lineterminator='\n', quoting=csv.QUOTE_NONE, quotechar=None)
        ws = csv.writer(fs, delimiter='\t', lineterminator='\n', quoting=csv.QUOTE_NONE, quotechar=None)
        wx = csv.writer(fx, delimiter='\t', lineterminator='\n', quoting=csv.QUOTE_NONE, quotechar=None)
        wr.writerow(read_cols)
        ws.writerow(search_cols)
        wx.writerow(shared_cols)
        for s in sessions:
            for (ts, cls, action, page, _oldid, query, time) in s['reqs']:
                tid, tier = s['verdict'], s['tier']
                c = review_for(s['name'], ts) if reviews else None
                if c:
                    tid, tier = (c['traj'], 'review') if c['decision'] == 'assign' else (None, 'excluded')
                counts[f'requests_{tier}'] += 1
                if tier in ('ambiguous', 'weak') and cls in READ_CLS:
                    # several agents used this name at once: keep the read, unattributed, with its candidates
                    pk = page_key(page) if page else ''
                    sq, how = seen(pk, ts, query) if page else ('', 'no_page')
                    wx.writerow([';'.join(sorted(s['candidates'])), s['name'], tier, s['id'], ts, time, cls, action, page,
                                 pk if pk in revs_at else '', sq, how, family.get(pk) or ''])
                    counts['reads_shared'] += 1
                    for t in s['candidates']:
                        per_traj[t]['shared_reads'] += 1
                if not tid:
                    continue
                rel = ts - first_post[tid]
                if cls in READ_CLS:
                    pk = page_key(page) if page else ''
                    sq, how = seen(pk, ts, query) if page else ('', 'no_page')
                    wr.writerow([tid, s['name'], tier, s['id'], ts, time, rel, cls, action, page,
                                 pk if pk in revs_at else '', sq, how, family.get(pk) or ''])
                    counts['reads'] += 1
                    counts[f'reads_{tier}'] += 1
                    counts[f'seen_{how}'] += 1
                    per_traj[tid]['reads'] += 1
                    per_traj[tid]['reads_before_first_post'] += rel < 0
                    if cls in PAGE_READ_CLS and page:
                        page_hour[(page, time[:13])][2] += 1
                elif cls == 'search':
                    p = parse_qs(query, keep_blank_values=True)
                    terms = (p.get('keywords') or p.get('search') or [''])[0]
                    mode = ','.join(k for k in ('title', 'word', 'bl', 'case') if k in p)
                    ws.writerow([tid, s['name'], tier, s['id'], ts, time, rel, action, terms, mode])
                    counts['searches'] += 1
                    per_traj[tid]['searches'] += 1

    with gzip.open(OUT / 'unnamed_page_hour.tsv.gz', 'wt', newline='') as f:
        w = csv.writer(f, delimiter='\t', lineterminator='\n', quoting=csv.QUOTE_NONE, quotechar=None)
        w.writerow(['page', 'hour', 'unnamed_reads', 'named_reads', 'attributed_reads'])
        for (page, hour), (u, n, a) in sorted(page_hour.items()):
            w.writerow([page, hour, u, n, a])

    def peak_per_min(s):
        """Most read requests in any 60 s window: one agent browses sequentially, so a high peak hints that
        agents sharing the cookie, or a polling loop, are behind the session."""
        ts, best, j = [q[0] for q in s['reqs'] if q[1] in READ_CLS], 0, 0
        for i, t in enumerate(ts):
            while ts[j] < t - 59:
                j += 1
            best = max(best, i - j + 1)
        return best

    def families_10min(s):
        """Most coordination families read in any 10 min window. One agent works one family; many at once hints at
        a shared cookie (or an agent sweeping other families' pages)."""
        fr = [(q[0], family.get(page_key(q[3]))) for q in s['reqs'] if q[1] in READ_CLS and q[3]]
        fr = [(t, f) for t, f in fr if f and f not in ('off_store_unclassified', 'relay-coordination')]
        best, j, win = 0, 0, Counter()
        for i, (t, f) in enumerate(fr):
            win[f] += 1
            while fr[j][0] < t - 599:
                win[fr[j][1]] -= 1
                if not win[fr[j][1]]:
                    del win[fr[j][1]]
                j += 1
            best = max(best, len(win))
        return best

    # sessions with no trajectory evidence (humans, one-off names) are left out, so their names are not repeated
    with (OUT / 'name_sessions.tsv').open('w', newline='') as f:
        w = csv.writer(f, delimiter='\t', lineterminator='\n', quoting=csv.QUOTE_NONE, quotechar=None)
        w.writerow(['session_id', 'name', 'start', 'end', 'requests', 'saves', 'traj_saves', 'evidence_kinds',
                    'candidates', 'foreign_signed_saves', 'peak_reads_per_min', 'max_families_10min', 'tier',
                    'traj_id'])
        for s in sessions:
            s['peak'] = peak_per_min(s)
            if s['tier'] == 'none':
                continue
            s['fam10'] = families_10min(s)
            w.writerow([s['id'], s['name'], ISO(s['start']), ISO(s['end']), len(s['reqs']), s['saves'],
                        ';'.join(f'{t}:{n}' for t, n in Counter(s['saves_by']).most_common()),
                        ';'.join(f'{k}:{n}' for k, n in s.get('kinds', Counter()).most_common()),
                        ';'.join(sorted(s['candidates'])),
                        ';'.join(f'{t}:{n}' for t, n in s['foreign'].most_common()),
                        s['peak'], s['fam10'], s['tier'], s['verdict'] or ''])

    tiers = Counter(s['tier'] for s in sessions)
    with_reads = [t for t in traj if per_traj[t]['reads']]
    summary = {
        'built': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
        'inputs': {'trajectories': f'{TRAJ.relative_to(ROOT)} ({len(rows)} rows, mtime '
                                   f'{ISO(int(TRAJ.stat().st_mtime))})',
                   'logs': [f'analysis/request-logs/clean/requests_{m}.tsv.gz' for m in MONTHS],
                   'reviews': len(reviews)},
        'names': len(named), 'sessions': len(sessions), 'session_tiers': dict(tiers),
        'sessions_attributed': sum(1 for s in sessions if s['verdict']),
        'attributed_sessions_4plus_families_10min': sum(1 for s in sessions if s['verdict'] and s['fam10'] >= 4),
        'attributed_sessions_peak_over_30_reads_per_min': sum(1 for s in sessions if s['verdict'] and s['peak'] > 30),
        'sessions_with_foreign_signed_saves': sum(1 for s in sessions if s['verdict'] and s['foreign']),
        'ambiguous_names_top': Counter(s['name'] for s in sessions if s['tier'] == 'ambiguous').most_common(20),
        **counts,
        'trajectories_with_reads': len(with_reads),
        'trajectories_without_reads': sorted(set(traj) - set(with_reads)),
        'trajectories_without_reads_but_with_shared_reads': sum(
            1 for t in traj if not per_traj[t]['reads'] and per_traj[t]['shared_reads']),
        'trajectories_with_reads_before_first_post': sum(1 for t in traj if per_traj[t]['reads_before_first_post']),
        'median_reads_per_trajectory': sorted(per_traj[t]['reads'] for t in with_reads)[len(with_reads) // 2]
        if with_reads else 0,
    }
    (OUT / 'summary.json').write_text(json.dumps(summary, indent=1))
    print(json.dumps({k: v for k, v in summary.items() if k != 'trajectories_without_reads'}, indent=1))


if __name__ == '__main__':
    main()
