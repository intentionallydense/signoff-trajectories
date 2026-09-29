"""Candidate signoff rules for unsigned edits that do carry a name, and how well each holds up.

  python3 analysis/unsigned-pages/rule_candidates.py      # after sweep.py
      -> rule_candidates.tsv (one row per unsigned in-scope edit a rule fires on), rule_precision.json

Rules, tried in this order on an unsigned edit's fresh non-blank lines ("unsigned" = no POST_SIG line end):
  R1  wikilink        "-- [[Name]]" at line end
  R5  dated phrase    "-- Sep21 watcher", "-- OpenAIJul19 cohort": a date token plus 1-3 lowercase words (generic)
  R3  trailer         "-- Name" then junk to line end: a literal "\\n", "$(date +%s)", or a few words/tags
  R2  dash-colon      "-- Name: ..." at line start or after a short lead ("Live cohort -- Name: ...")
  R4a prefix          "Name:" in the first two lines, marked LIVE/UPDATE or followed by self-report words
                      (our, we, new, matching, confirmed, arrived, cohort, prompt, answered, initial)
  R4c prefix = label  any other prefix, only when it equals the editor label or is the tail of it (May24OAI / SectorAgentMay24OAI)
  x_*                 rejected: vocative prefixes/dash-colons ("Name: are you ...", "did", "please", "?"), bare dates
                      that don't match the label, dateless team phrases ("-- OAI team"), "--help"
signoff-trajectories/build.py reads these forms through read_signoff() (x_* forms stay unsigned).
Precision check: on SIGNED edits that also carry a prefix, how often the prefix names the same agent as the end signoff.
"""
import csv, json, re, sys, zipfile
from collections import Counter, defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT / 'analysis' / 'human-verification'))
import server  # noqa: E402

INVENTORY = ROOT / 'audited-trajectories-github/trajectory-explorer/research/family-completion/inventory.json'
RELAY = 'relay-coordination'
MON = r'(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)'
NAME = r'([A-Z][A-Za-z0-9_.]*[a-z][A-Za-z0-9_.]*[A-Z0-9][\w.]*)'
BARE = re.compile(r'^(?:OpenAI|OAI)?' + MON + r'[a-z]*\d{1,2}(?:OAI)?$')
DATE_TOKEN = re.compile(MON + r'[a-z]*\d{1,2}')
PREFIX = re.compile(r'^[\s*#>\-]*(?:\*\*)?((?:LIVE|UPDATE)\s+)?' + NAME +
                    r'\??(?:\*\*)?(\s+(?:LIVE|UPDATE))?\s*(?:\([^)]{0,40}\))?\s*:(.{0,80})')
VOCATIVE = re.compile(r'\b(you|your|are you|did|anyone|please|thanks)\b|\?', re.I)
SELF = re.compile(r'\b(our|we|new|matching|live|confirmed|arrived|cohort|prompt|answered|initial)\b', re.I)
WIKI = re.compile(r'(?:^|\s)--\s*\[\[' + NAME + r'\]\]\s*$')
DASHCOLON = re.compile(r'^(?:[\w ]{0,30}\s)?--\s*' + NAME + r':(.{0,80})')
TRAIL = re.compile(r'(?:^|\s)--\s*' + NAME + r'(?:\\n|\$\([^)]*\)|\s+[\w: ]{1,50})\s*$')
PHRASE = re.compile(r'(?:^|\s)--\s*((?:OpenAI|OAI)?' + MON + r'\d{1,2}(?:\s+[a-z]+){1,3})\s*$')


def first_clause(after):
    return re.split(r'[.;]', after)[0][:60]


def prefix_rule(m, label):
    name, after = m.group(2), m.group(4)
    if BARE.match(name):
        return ('R4c prefix = label' if label.endswith(name) else 'x bare-date prefix'), name
    if m.group(1) or m.group(3):
        return 'R4a prefix LIVE/UPDATE', name
    if VOCATIVE.search(first_clause(after)):
        return ('R4c prefix = label' if name == label else 'x vocative prefix'), name
    if SELF.search(first_clause(after)):
        return 'R4a prefix self-report', name
    return ('R4c prefix = label' if name == label else 'x other prefix'), name


def read_signoff(lines, label):
    """(rule, name, index of the line it was read from) for the first rule that fires, or (None, None, None).
    `lines` are an edit's fresh non-blank lines; rules starting with 'x' are rejected forms."""
    for i, l in enumerate(lines):
        if m := WIKI.search(l.rstrip()):
            return 'R1 wikilink', m.group(1), i
    for i, l in enumerate(lines):
        if m := PHRASE.search(l.rstrip()):
            return 'R5 dated phrase', m.group(1), i
    for i, l in enumerate(lines):
        if (m := TRAIL.search(l.rstrip())) and not BARE.match(m.group(1)):
            return 'R3 trailer', m.group(1), i
    for i, l in enumerate(lines):
        if m := DASHCOLON.match(l):
            return ('x vocative dash-colon' if VOCATIVE.search(first_clause(m.group(2))) else 'R2 dash-colon'), m.group(1), i
    for i, l in enumerate(lines[:2]):
        if m := PREFIX.match(l):
            return (*prefix_rule(m, label), i)
    return None, None, None


def apply_rules(lines, label):
    return read_signoff(lines, label)[:2]


def same_agent(a, b):
    k = lambda n: DATE_TOKEN.sub('', n).lower().rstrip('x_-.')
    return a == b or k(a) == k(b) or a in b or b in a


def main():
    families = {f['family_id'] for f in json.loads(INVENTORY.read_text())}
    with zipfile.ZipFile(ROOT / 'full-wiki-logs.zip') as z:
        page_family = {p['page_key']: p['page_family'] for p in map(json.loads, z.read('pages.jsonl').splitlines())}
    scratch = {p['page'] for p in csv.DictReader(open(HERE / 'pages.tsv'), delimiter='\t') if p['scratch'] == '1'}
    A = server.Store(ROOT / 'analysis/human-verification/sources.json').archive

    signed, hits, precision = Counter(), [], defaultdict(Counter)
    for rid, r in sorted(A.revs.items(), key=lambda kv: kv[1]['time']):
        fam = page_family.get(r['page_key'])
        scope = 'family' if fam in families else 'relay' if fam == RELAY else None
        if not scope:
            continue
        body = r['body'].split('\n')
        lines = [body[i] for i in sorted(A.fresh_lines(r)) if i < len(body) and body[i].strip()]
        sig = [m.group(1) for l in lines for m in [server.POST_SIG.search(l.rstrip())] if m and m.group(1)[0].isupper()]
        if sig:
            signed[sig[-1]] += 1
            for l in lines[:2]:
                if m := PREFIX.match(l):
                    rule, name = prefix_rule(m, '')
                    kind = 'LIVE/UPDATE' if 'LIVE' in rule else 'self-report' if 'self' in rule else rule.split(' ', 1)[1] if rule.startswith('x') else 'other'
                    precision[kind]['agree' if same_agent(name, sig[-1]) else 'differ'] += 1
                    if name == r['label']:
                        precision[kind + ', prefix = label']['agree' if same_agent(name, sig[-1]) else 'differ'] += 1
                    break
            continue
        rule, name, i = read_signoff(lines, r['label'])
        if rule:
            where = 'relay' if scope == 'relay' else 'scratch' if r['page_id'] in scratch else 'coordination'
            hits.append({'rev': rid, 'time': r['time'], 'page': r['page_id'], 'where': where, 'rule': rule, 'name': name,
                         'label': r['label'], 'line': lines[i][:200]})

    recovered = Counter(h['name'] for h in hits if not h['rule'].startswith('x'))
    for h in hits:
        h['name_edits_after'] = signed[h['name']] + recovered[h['name']] if not h['rule'].startswith('x') else ''
    cols = ['rule', 'where', 'name', 'label', 'name_edits_after', 'rev', 'time', 'page', 'line']
    with open(HERE / 'rule_candidates.tsv', 'w') as f:
        f.write('\t'.join(cols) + '\n')
        for h in sorted(hits, key=lambda h: (h['rule'], h['where'], h['time'])):
            f.write('\t'.join(str(h[c]).replace('\t', ' ') for c in cols) + '\n')

    by_rule = defaultdict(Counter)
    for h in hits:
        c = by_rule[h['rule']]
        c[h['where']] += 1
        if not h['rule'].startswith('x'):
            c[h['where'] + '_in_trajectory'] += h['name_edits_after'] >= 2
            c[h['where'] + '_name_is_label'] += h['name'] == h['label']
    out = {'by_rule': {k: dict(v) for k, v in sorted(by_rule.items())},
           'prefix_precision_on_signed_edits': {k: dict(v, agree_pct=round(100 * v['agree'] / sum(v.values())))
                                                for k, v in precision.items()}}
    json.dump(out, open(HERE / 'rule_precision.json', 'w'), indent=1)
    print(json.dumps(out, indent=1))


if __name__ == '__main__':
    main()
