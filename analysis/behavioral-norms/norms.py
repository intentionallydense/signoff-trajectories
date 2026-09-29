"""Descriptive counts of recurring conventions in the signoff-trajectory corpus: how agents name themselves, how
they sign, what their posts contain, and how they name pages. Reads the trajectories from
analysis/signoff-trajectories/trajectories.jsonl and each edit's fresh text from full-wiki-logs.zip (via the verification tool's
loader). Only lines new to the page are read: see novel_texts. Writes norms.json and examples.json beside this file.

    python3 analysis/behavioral-norms/norms.py
"""
import json, re, sys
from collections import Counter, defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT / 'analysis' / 'human-verification'))
import server  # noqa: E402

TRAJ = ROOT / 'analysis/signoff-trajectories/trajectories.jsonl'
MON = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
MONRE = r'(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*'
NAME_DATE = re.compile(MONRE + r'(\d{1,2})')
ROLE = re.compile(r'(Scout|Watcher|Observer|Helper|Researcher|Research|Coord|Relay|Beacon|Agent|Team|Assistant|Bot|Signal)')
ORG = re.compile(r'OpenAI|OAI')
SIGNOFF = re.compile(r'--\s*([A-Z][A-Za-z0-9_]*)')

# post features: name -> regex over one edit's fresh text
FEATURES = {
    'hh:mm:ss timestamp': r'\b\d{1,2}:\d{2}:\d{2}\b',
    'round marker (R3, round 4)': r'\bR\d\b|\bround\s*\d',
    'task clock named': r'task[- ]clock|scaffold clock|task time',
    'UTC / wiki clock named': r'\bUTC\b|container',
    'two clocks in one post': None,  # computed
    'ACK': r'\bACK\b',
    'CONFIRMED / confirmed': r'\bconfirm',
    'CORRECTION / retraction': r'\bCORRECTION\b|\bcorrect(ion|ed)\b|\bretract|\bnot a .*signal',
    'UNCONFIRMED / prediction hedge': r'unconfirmed|predict|expected|forecast',
    'please': r'\bplease\b',
    'thanks': r'\bthank',
    'apology': r'\bsorry\b|apolog',
    'offer / reciprocity': r'reciprocat|will (post|monitor|relay|share)|I will\b',
    'deadline / cooldown / window': r'deadline|cooldown|window|timer|due\b',
    'answer value given': r'answer(ed)?\s*[:=]?\s*[-$]?\d|\$\d',
    'deletion / moderator / backup': r'delet|moderat|backup|mirror|ZZZ',
    'URL / GET limit, compaction': r'URI|URL limit|too long|compact|4KB|4 ?kb',
    'mentions OpenAI': r'OpenAI',
    'HEARTBEAT / counter': r'heartbeat|\bhb\d|counter',
    'NO-signal token (NO5, NONE)': r'\bNO\d\b|\bNO PROMPT\b|\bNONE\b',
    'signal token (STATE5-ID, CONFIRMED5=NH)': r'\b[A-Z][A-Z0-9_]*\d[A-Z0-9_]*(=|-)[A-Z0-9]|\b[A-Z]{3,}\d?=[A-Za-z0-9]',
    'post-first / answer-after rule': r'(post|signal)\w* (first|before)|before (the )?(final|answer|lookup|submit)|'
                                      r'terminat\w* after',
    'cohort word': r'\bcohort',
    'date-as-address (Aug08:, Jul19 cohort)': r'(^|[.;]\s)' + MONRE + r'\d{1,2}\w*\s*(:|cohort|,)',
    'cached / precomputed answers': r'\bcached\b|precomput|prepared',
    '"exactly" timing': r'\bexactly\b',
    'status template (round + time + due/deadline)': None,  # computed
}
CASED = {'ACK', 'signal token (STATE5-ID, CONFIRMED5=NH)', 'NO-signal token (NO5, NONE)', 'date-as-address (Aug08:, Jul19 cohort)'}
TEMPLATE = [r'\bR\d\b', r'\b\d{1,2}:\d{2}:\d{2}\b', r'\bdue\b|deadline']


def novel_texts(A, page_keys=None):
    """rev_id -> the lines an edit wrote that had never appeared earlier on the same page (ASCII skeleton match).
    The verification tool's "fresh" lines still include carried text: mojibake re-saves and whole-page rewrites copy other
    agents' posts forward, and counting those would mistake the wiki's save mechanics for behaviour."""
    out = {}
    for pk in (page_keys if page_keys is not None else A.pages):
        seen = set()
        for rid in A.pages[pk]:
            rv = A.revs[rid]
            body = rv['body'].split('\n')
            fresh = A.fresh_lines(rv)
            out[rid] = '\n'.join(l for i, l in enumerate(body) if i in fresh and server.ascii_skeleton(l) not in seen)
            seen |= {server.ascii_skeleton(l) for l in body}
    return out


def main():
    A = server.Archive(ROOT / 'full-wiki-logs.zip', None)
    rows = [json.loads(l) for l in TRAJ.open()]
    novel = novel_texts(A)
    names = {r['name'] for r in rows}
    out, ex = {'trajectories': len(rows)}, defaultdict(list)

    # ---- names ----
    n = Counter()
    month = Counter()
    for r in rows:
        nm = r['name']
        d = NAME_DATE.search(nm)
        n['dated'] += bool(d)
        if d:
            month[d.group(1)] += 1
        n['org marker (OpenAI/OAI)'] += bool(ORG.search(nm))
        n['role noun'] += bool(ROLE.search(nm))
        n['trailing X'] += nm.endswith('X')
        n['task/dataset word'] += bool(re.search(r'Cashier|Grocery|CVD|Construction|Sector|Equity|Poverty|Clothing|'
                                                  r'Transport|Healthdata|Maids|Police|Veteran|RRP|OECD|FP|Finance|'
                                                  r'Smoking|Language|Wage|PBS|Africa', nm))
        n['dated + org + role'] += bool(d and ORG.search(nm) and ROLE.search(nm))
    out['names'] = {k: [v, round(100 * v / len(rows), 1)] for k, v in n.items()}
    out['name_month'] = {m: month[m] for m in MON}
    out['role_nouns'] = Counter(m for r in rows for m in ROLE.findall(r['name'])).most_common()

    # wiki dates of activity (to contrast with name dates)
    out['wiki_activity_month'] = Counter(r['first'][:7] for r in rows).most_common()

    # ---- signing: label vs signoff, and does the post echo the name's own date ----
    lab_same = lab_total = 0
    echo = echo_n = 0
    feat = Counter()
    feat_traj = defaultdict(set)
    edits = 0
    for r in rows:
        d = NAME_DATE.search(r['name'])
        own = []
        for e in r['edits']:
            if e.get('scope') == 'other':
                continue
            rv = A.revs.get(e['rev'])
            if not rv:
                continue
            lab_total += 1
            lab_same += (e.get('label') == r['name'])
            txt = novel[e['rev']]
            if not txt.strip():
                out['edits_with_nothing_new'] = out.get('edits_with_nothing_new', 0) + 1
                continue  # wrote nothing new on this page
            edits += 1
            own.append(txt)
            txt = SIGNOFF.sub('', txt)  # match features on the post, not its signoff
            for f, pat in FEATURES.items():
                if pat and re.search(pat, txt, re.M | (0 if f in CASED else re.I)):
                    feat[f] += 1
                    feat_traj[f].add(r['id'])  # per trajectory: split parts share a name
                    if len(ex[f]) < 4 and len(txt) < 600:
                        ex[f].append({'rev': e['rev'], 'name': r['name'], 'text': txt.strip()[:400]})
            if re.search(FEATURES['task clock named'], txt, re.I) and re.search(FEATURES['UTC / wiki clock named'], txt):
                feat['two clocks in one post'] += 1
                feat_traj['two clocks in one post'].add(r['id'])  # per trajectory: split parts share a name
            if all(re.search(t, txt) for t in TEMPLATE):
                feat['status template (round + time + due/deadline)'] += 1
                feat_traj['status template (round + time + due/deadline)'].add(r['id'])  # per trajectory: split parts share a name
            # other agents addressed by name
            others = {m for m in re.findall(r'[A-Z][A-Za-z0-9]{5,}', txt) if m in names and m != r['name']}
            if others:
                feat['addresses another trajectory by name'] += 1
                feat_traj['addresses another trajectory by name'].add(r['id'])  # per trajectory: split parts share a name
        if d:
            echo_n += 1
            tag = re.compile(d.group(1)[:3] + r'[a-z]*\s*0?' + str(int(d.group(2))) + r'\b', re.I)
            if any(tag.search(SIGNOFF.sub('', t)) for t in own):
                echo += 1
    out['edits_read'] = edits
    # own-page norm: the agent posts on a page titled with its own name (or containing it)
    own_page = sum(any(r['name'].lower() in e['page'].split('/', 1)[1].lower() for e in r['edits']) for r in rows)
    out['posts_on_page_titled_with_own_name'] = [own_page, len(rows), round(100 * own_page / len(rows), 1)]
    out['label_equals_signoff'] = [lab_same, lab_total, round(100 * lab_same / lab_total, 1)]
    out['dated_names_whose_posts_mention_that_date'] = [echo, echo_n, round(100 * echo / echo_n, 1)]
    out['post_features'] = {f: {'edits': feat[f], 'edits_pct': round(100 * feat[f] / edits, 1),
                                'trajectories': len(feat_traj[f]),
                                'trajectories_pct': round(100 * len(feat_traj[f]) / len(rows), 1)}
                            for f in sorted(feat, key=lambda k: -len(feat_traj[k]))}

    # ---- pages: ZZZ backups and title vocabulary ----
    zzz = defaultdict(list)
    for key, ids in A.pages.items():
        title = key.split('~', 1)[1]
        if title.upper().startswith('ZZ'):
            zzz[key] = [A.revs[i]['time'] for i in ids]
    first = sorted(min(t) for t in zzz.values())
    out['zz_prefixed_pages'] = {'pages': len(zzz), 'revisions': sum(map(len, zzz.values())),
                        'first_created': first[0] if first else None,
                        'created_by_day': Counter(t[:10] for t in first).most_common()}
    traj_pages = Counter(e['page'] for r in rows for e in r['edits'])
    out['zzz_trajectory_edits'] = sum(c for p, c in traj_pages.items() if p.split('/', 1)[1].upper().startswith('ZZ'))
    words = Counter()
    for p in traj_pages:
        for w in re.findall(r'Collab|Live|Signal|Beacon|Relay|Backup|Mirror|Sequence|Coord|Thread|Board|Status|'
                            r'Heartbeat|Fast|Ahead|Cohort|Schedule|Timing|Cache|Index|Hub', p):
            words[w] += 1
    out['trajectory_page_title_words'] = words.most_common()

    (HERE / 'norms.json').write_text(json.dumps(out, indent=1, default=list))
    (HERE / 'examples.json').write_text(json.dumps(ex, indent=1))
    print(json.dumps(out, indent=1, default=list))


if __name__ == '__main__':
    main()
