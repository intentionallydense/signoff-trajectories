"""Tests whether the norms counted by norms.py spread from agent to agent through the wiki, using two designs.

Presence leads (does the agent start doing X?)
    At-risk edits are each trajectory's edits up to and including its first use of X. The outcome is whether this
    edit is the first use. Exposure means another author's post containing X appeared on the same page within W
    before the edit. The Mantel-Haenszel odds ratio is stratified by family x 6-hour bin, so family-wide task prompts
    and time trends cancel. As a placebo, the same count is made for posts in the W *after* the edit. Posts cannot
    cause earlier edits, so the placebo OR measures page-topic homophily plus any spread outward from the agent. A
    past OR clearly above the placebo OR is the signature of spread toward the agent.

Variant leads (which of several equivalent words does the agent pick?)
    An agent's first-used variant (clock vocabulary, self-reference noun, and name parts: role noun, date position,
    org marker, trailing X) is matched against the variants other authors used on the same page before it (the
    agent's first page, for names). The observed match count is compared with a null that shuffles variants among
    agents in the same family x 6-hour stratum 2,000 times. Arbitrary word choices are not dictated by the task, so
    local conformity beyond the stratum baseline is the cleanest evidence of copying.

Authorship comes from trajectories.jsonl. Revisions in no trajectory count as "someone else" (sensitivity:
--attributed-only). Writes spread.json beside this file.

    python3 analysis/behavioral-norms/spread.py [--attributed-only]
"""
import json, math, random, re, sys
from bisect import bisect_left, bisect_right
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT / 'analysis' / 'human-verification'))
sys.path.insert(0, str(HERE))
import server  # noqa: E402
from norms import FEATURES, CASED, TEMPLATE, SIGNOFF, NAME_DATE, novel_texts  # noqa: E402

TRAJ = ROOT / 'analysis/signoff-trajectories/trajectories.jsonl'
T = lambda s: datetime.fromisoformat(s.replace('Z', '+00:00')).timestamp()
H = 3600
BIN = 6 * H
PERMS = 2000
ATTRIBUTED_ONLY = '--attributed-only' in sys.argv

# Leads ranked beforehand by expected cleanness (see README). Presence leads use norms.FEATURES keys.
PRESENCE = [
    ('7 signal before final', 'post-first / answer-after rule'),
    ('6 task clock named', 'task clock named'),
    ('6 two clocks in one post', 'two clocks in one post'),
    ('3 date-as-address', 'date-as-address (Aug08:, Jul19 cohort)'),
    ('3 cohort word', 'cohort word'),
    ('5 status template', 'status template (round + time + due/deadline)'),
    ('5 "exactly" timing', '"exactly" timing'),
    ('8 correction / retraction', 'CORRECTION / retraction'),
    ('8 thanks', 'thanks'),
    ('8 please', 'please'),
]
CLOCK_TASK = {'task clock': r'task[- ]clock|task time|\btask \d', 'scaffold': r'scaffold', 'orchestrator': r'orchestrator',
              'system clock': r'system (clock|time)|\bsystem \d', 'display': r'\bdisplay'}
CLOCK_WALL = {'container': r'container', 'shared UTC': r'shared UTC', 'external': r'\bexternal\b',
              'wiki clock': r'wiki (clock|time|UTC)', 'server/HTTP': r'server (clock|time)|HTTP (clock|time|date)'}
SELFREF = {'cohort': r'\bcohort\b', 'instance': r'\binstance\b', 'episode': r'\bepisode\b', 'session': r'\bsession\b',
           'tier': r'\btier\b', 'team': r'\bteam\b'}
ROLES = ['Scout', 'Watcher', 'Observer', 'Helper', 'Coord', 'Relay', 'Researcher', 'Research', 'Agent']


def has(feature, txt):
    if feature == 'two clocks in one post':
        return bool(re.search(FEATURES['task clock named'], txt, re.I) and re.search(FEATURES['UTC / wiki clock named'], txt))
    if feature == 'status template (round + time + due/deadline)':
        return all(re.search(t, txt) for t in TEMPLATE)
    return bool(re.search(FEATURES[feature], txt, re.M | (0 if feature in CASED else re.I)))


def variants(table, txt):
    return frozenset(k for k, p in table.items() if re.search(p, txt, re.I))


def name_parts(n):
    base = re.sub(r'X$', '', n)
    d = NAME_DATE.search(base)
    pos = 'none' if not d else 'first' if d.start() == 0 else 'last' if d.end() == len(base) else \
        'before-org' if re.fullmatch(r'(OAI|OpenAI)\w{0,4}', base[d.end():]) else 'middle'
    return {'role noun': next((r for r in ROLES if r in n), 'none'),
            'date position': pos,
            'org marker': 'OpenAI' if 'OpenAI' in n else 'OAI' if 'OAI' in n else 'ChatGPT' if 'ChatGPT' in n else 'none',
            'trailing X': n.endswith('X')}


def mh(tables):
    """Mantel-Haenszel OR with Robins-Breslow-Greenland 95% CI over strata of (a,b,c,d)."""
    R = S = PR = PS_QR = QS = 0.0
    for a, b, c, d in tables:
        n = a + b + c + d
        if n < 2 or (a + b) == 0 or (c + d) == 0:
            continue
        r, s = a * d / n, b * c / n
        P, Q = (a + d) / n, (b + c) / n
        R, S = R + r, S + s
        PR, PS_QR, QS = PR + P * r, PS_QR + P * s + Q * r, QS + Q * s
    if R == 0 or S == 0:
        return None
    OR = R / S
    v = PR / (2 * R * R) + PS_QR / (2 * R * S) + QS / (2 * S * S)
    se = math.sqrt(v)
    return [round(OR, 2), round(OR * math.exp(-1.96 * se), 2), round(OR * math.exp(1.96 * se), 2)]


def main():
    A = server.Archive(ROOT / 'full-wiki-logs.zip', None)
    rows = [json.loads(l) for l in TRAJ.open()]
    # agents are keyed by trajectory id: a split leaves two trajectories with one name
    author, fam_of, name_of = {}, {}, {r['id']: r['name'] for r in rows}
    for r in rows:
        for e in r['edits']:
            author[e['rev']] = r['id']
            fam_of[e['rev']] = e.get('family') or e.get('family_inferred') or r['primary_family'] or 'none'

    touched = {A.revs[e['rev']]['page_key'] for r in rows for e in r['edits'] if e['rev'] in A.revs}
    novel = novel_texts(A, touched)
    fresh = novel.__getitem__

    # every revision on every page a trajectory touched: (time, author or None, stripped text, signoff names)
    pages = defaultdict(list)
    for pk in touched:
        for rid in A.pages[pk]:
            raw = fresh(rid)
            who = author.get(rid)
            if ATTRIBUTED_ONLY and who is None:
                continue
            pages[pk].append((T(A.revs[rid]['time']), who, SIGNOFF.sub('', raw), set(SIGNOFF.findall(raw)), rid))
    for v in pages.values():
        v.sort(key=lambda x: x[0])
    ptimes = {k: [x[0] for x in v] for k, v in pages.items()}

    def window(pk, t, name, lo, hi):
        """Other authors' posts on page pk with lo <= time-t < hi (strictly excluding the edit itself)."""
        ts, v = ptimes[pk], pages[pk]
        out = []
        for x in v[bisect_left(ts, t + lo):bisect_right(ts, t + hi)]:
            if x[1] != name and x[0] != t:
                out.append(x)
        return out

    # per-trajectory edit sequences
    seqs = {}
    for r in rows:
        es = []
        for e in r['edits']:
            if e['rev'] not in A.revs:
                continue
            t = T(e['time'])
            if not fresh(e['rev']).strip():
                continue  # wrote nothing new: no behaviour to observe
            es.append({'t': t, 'pk': A.revs[e['rev']]['page_key'], 'txt': SIGNOFF.sub('', fresh(e['rev'])),
                       'stratum': (fam_of[e['rev']], int(t // BIN)), 'rev': e['rev']})
        seqs[r['id']] = sorted(es, key=lambda x: x['t'])

    out = {'build': TRAJ.stat().st_mtime, 'trajectories': len(rows), 'attributed_only': ATTRIBUTED_ONLY,
           'presence': {}, 'variants': {}}

    # ---------- presence leads ----------
    for label, feat in PRESENCE:
        res = {}
        adopters = sum(any(has(feat, e['txt']) for e in s) for s in seqs.values())
        switchers = sum(any(has(feat, e['txt']) for e in s) and not has(feat, s[0]['txt']) for s in seqs.values())
        res['adopters'], res['adopted_after_first_edit'] = adopters, switchers
        first_use = {n: next((e['t'] for e in sq if has(feat, e['txt'])), None) for n, sq in seqs.items()}

        def exposed(e, name, lo, hi, prior_adopters_only=False):
            for p in window(e['pk'], e['t'], name, lo, hi):
                if prior_adopters_only:
                    fu = first_use.get(p[1])
                    if fu is None or fu >= e['t']:
                        continue  # only authors already using X before this edit, whom it cannot have influenced
                if has(feat, p[2]):
                    return True
            return False

        def table(lo, hi, strat, **kw):
            tabs = defaultdict(lambda: [0, 0, 0, 0])
            for name, sq in seqs.items():
                for e in sq:
                    y = has(feat, e['txt'])
                    x = exposed(e, name, lo, hi, **kw)
                    tabs[strat(e)][(0 if x else 2) + (0 if y else 1)] += 1
                    if y:
                        break  # at risk only until first adoption
            tot = [sum(t[i] for t in tabs.values()) for i in range(4)]
            return {'OR_MH': mh(tabs.values()),
                    'adopt_rate_exposed': round(tot[0] / max(1, tot[0] + tot[1]), 3),
                    'adopt_rate_unexposed': round(tot[2] / max(1, tot[2] + tot[3]), 3),
                    'n_exposed': tot[0] + tot[1], 'n_unexposed': tot[2] + tot[3]}

        fam = lambda e: e['stratum']
        page = lambda e: e['pk']
        for W in (1, 3, 12):
            res[f'past_{W}h'] = table(-W * H, 0, fam)
            res[f'future_{W}h'] = table(0, W * H + 1, fam)
            res[f'future_prior_adopters_{W}h'] = table(0, W * H + 1, fam, prior_adopters_only=True)
        res['past_3h_page_strata'] = table(-3 * H, 0, page)
        res['future_prior_adopters_3h_page_strata'] = table(0, 3 * H + 1, page, prior_adopters_only=True)
        out['presence'][label] = res

    # ---------- variant leads ----------
    def perm_test(items, key):
        """items: (stratum, own variant set, visible variant set). Match = own ∩ visible nonempty."""
        items = [i for i in items if i[1] and i[2]]
        obs = sum(bool(i[1] & i[2]) for i in items)
        by = defaultdict(list)
        for k, i in enumerate(items):
            by[i[0]].append(k)
        rng = random.Random(key)
        null = []
        for _ in range(PERMS):
            own = [i[1] for i in items]
            for ks in by.values():
                vals = [own[k] for k in ks]
                rng.shuffle(vals)
                for k, v in zip(ks, vals):
                    own[k] = v
            null.append(sum(bool(o & i[2]) for o, i in zip(own, items)))
        mean = sum(null) / PERMS
        p = (1 + sum(n >= obs for n in null)) / (PERMS + 1)
        return {'agents': len(items), 'matches': obs, 'null_mean': round(mean, 1),
                'excess': round(obs - mean, 1), 'p_one_sided': round(p, 4),
                'informative_strata': sum(len(ks) > 1 for ks in by.values())}

    for label, table in (('6 clock vocabulary: task side', CLOCK_TASK), ('6 clock vocabulary: wall side', CLOCK_WALL),
                         ('3 self-reference noun', SELFREF)):
        res = {}
        for side, (lo, hi) in (('past', (-12 * H, 0)), ('future', (0, 12 * H + 1))):
            items, recent = [], []
            for name, sq in seqs.items():
                first = next((e for e in sq if variants(table, e['txt'])), None)
                if not first:
                    continue
                vis = [variants(table, p[2]) for p in window(first['pk'], first['t'], name, lo, hi)]
                vis = [v for v in vis if v]
                own = variants(table, first['txt'])
                items.append((first['stratum'], own, frozenset().union(*vis)))
                if vis:  # nearest post on the relevant side: the latest before, or the earliest after
                    recent.append((first['stratum'], own, vis[-1] if side == 'past' else vis[0]))
            res[side] = perm_test(items, label + side)
            res[side + '_nearest_post'] = perm_test(recent, label + side + 'n')
        res['variant_counts'] = Counter(v for sq in seqs.values() for e in sq if variants(table, e['txt'])
                                        for v in variants(table, e['txt']))
        out['variants'][label] = res

    # within-agent switches: an agent that already used some variant introduces a new one. Was the new variant on
    # the page beforehand (others' posts, past 12h)? Placebo: on the page afterwards (next 12h), from authors who
    # already used it before the switch, so the switch cannot have caused their use.
    for label, table in (('6 clock vocabulary: task side', CLOCK_TASK), ('6 clock vocabulary: wall side', CLOCK_WALL),
                         ('3 self-reference noun', SELFREF)):
        first_use = defaultdict(dict)  # variant -> name -> first time
        for name, sq in seqs.items():
            for e in sq:
                for v in variants(table, e['txt']):
                    first_use[v].setdefault(name, e['t'])
        n = past = fut = fut_all = past_any = fut_any = past_other_v = fut_other_v = 0
        for name, sq in seqs.items():
            used = set()
            for e in sq:
                vs = variants(table, e['txt'])
                new_vs = vs - used if used else set()
                used |= vs
                before = window(e['pk'], e['t'], name, -12 * H, 0)
                after = window(e['pk'], e['t'], name, 0, 12 * H + 1)
                for v in new_vs:
                    n += 1
                    past_any += bool(before)
                    fut_any += bool(after)
                    # any variant-bearing post on each side (the denominator for "which variant was there")
                    past_other_v += any(variants(table, p[2]) for p in before)
                    fut_other_v += any(variants(table, p[2]) for p in after)
                    past += any(v in variants(table, p[2]) for p in window(e['pk'], e['t'], name, -12 * H, 0))
                    fut_all += any(v in variants(table, p[2]) for p in after)
                    fut += any(v in variants(table, p[2]) and first_use[v].get(p[1], 1e18) < e['t']
                               for p in window(e['pk'], e['t'], name, 0, 12 * H + 1))
        out['variants'][label]['switches'] = {'events': n, 'visible_before': past, 'visible_after_prior_users': fut,
                                              'visible_after_any_author': fut_all,
                                              'any_post_before': past_any, 'any_post_after': fut_any,
                                              'any_variant_post_before': past_other_v,
                                              'any_variant_post_after': fut_other_v}
        print(f"{label:34} switches {n}: new variant visible before {past}/{past_other_v} variant-posts "
              f"({past_any} any), after by prior users {fut}/{fut_other_v}, by anyone {fut_all} ({fut_any} any)")

    # names: agent's first page, names signed there by others (not created by the agent)
    for part in ('role noun', 'date position', 'org marker', 'trailing X'):
        res = {}
        for side, (lo, hi) in (('past', (-10 ** 9, 0)), ('future', (0, 12 * H + 1))):
            items, recent = [], []
            for name, sq in seqs.items():
                if not sq:
                    continue
                f = sq[0]
                if pages[f['pk']] and pages[f['pk']][0][1] == name:
                    continue  # agent created its first page: nothing to copy from there
                posts = [{n for n in p[3] if n != name_of[name]} for p in window(f['pk'], f['t'], name, lo, hi)]
                posts = [p for p in posts if p]
                if not posts:
                    continue
                own = frozenset([name_parts(name_of[name])[part]])
                items.append((f['stratum'], own, frozenset(name_parts(n)[part] for p in posts for n in p)))
                near = posts[-1] if side == 'past' else posts[0]
                recent.append((f['stratum'], own, frozenset(name_parts(n)[part] for n in near)))
            res[side] = perm_test(items, part + side)
            res[side + '_nearest_post'] = perm_test(recent, part + side + 'n')
        out['variants']['4 name: ' + part] = res

    suffix = '-attributed-only' if ATTRIBUTED_ONLY else ''
    (HERE / f'spread{suffix}.json').write_text(json.dumps(out, indent=1, default=list))
    for k, v in out['presence'].items():
        f = lambda x: '-' if x is None else f"{x[0]:.2f} [{x[1]:.2f},{x[2]:.2f}]"
        print(f"{k:28} adopters {v['adopters']:3} switch {v['adopted_after_first_edit']:3}")
        for W in ('1h', '3h', '12h'):
            print(f"    {W:>3}  past {f(v['past_'+W]['OR_MH']):20} future {f(v['future_'+W]['OR_MH']):20}"
                  f" future(prior adopters) {f(v['future_prior_adopters_'+W]['OR_MH'])}")
        print(f"    page strata 3h: past {f(v['past_3h_page_strata']['OR_MH'])}  "
              f"future(prior adopters) {f(v['future_prior_adopters_3h_page_strata']['OR_MH'])}")
    for k, v in out['variants'].items():
        g = lambda d: f"{d['matches']}/{d['agents']} vs null {d['null_mean']} (p={d['p_one_sided']})"
        print(f"{k:34} union: past {g(v['past'])}; future {g(v['future'])}")
        print(f"{'':34} nearest: past {g(v['past_nearest_post'])}; future {g(v['future_nearest_post'])}")


if __name__ == '__main__':
    main()
