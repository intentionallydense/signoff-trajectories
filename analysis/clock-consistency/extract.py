"""Round and task-clock claims from every edit of the signoff trajectories.

For each edit in analysis/signoff-trajectories/trajectories.jsonl this reads the agent's own post (the block of fresh
lines that ends at its signed line) and pulls out every clock time, with the round it belongs to, the event, whether it
is reported or forecast, whose it is, and which clock it is on. The clash and shared-schedule checks read claims.jsonl.

    python3 analysis/clock-consistency/extract.py

Heuristic throughout. Claims that fail a cue keep that fact in their fields rather than being dropped, so the checks
choose how strict to be.

OFF turns off fixes made during calibration, to rerun the earlier rulesets (ablate.py): 'question', 'about_other',
'before_tag', 'before_peer', 'counter_key', 'utc_glued', 'camel_text', 'duration_end', 'scaffold_utc'.
"""
import json, re, sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT / 'analysis' / 'human-verification'))
from server import Archive, POST_SIG  # noqa: E402

TRAJECTORIES = ROOT / 'analysis/signoff-trajectories/trajectories.jsonl'
MAX_BLOCK = 12  # fresh lines above the signed line that can still belong to the same post
OFF = set()

MON = (r'(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|June?|July?|Aug(?:ust)?|Sep(?:t|tember)?|Oct(?:ober)?|'
       r'Nov(?:ember)?|Dec(?:ember)?)')
DATE_TAG = re.compile(r'(?<![A-Za-z])(' + MON + r')\s?(\d{1,2})(?!\d)', re.I)
YEAR = re.compile(r'(?<![\w-])(20[2-3]\d)(?![\w-])')
# R1 generic, G grocery, C clothing, Q quiz; "round 3", "#5". A lone letter+digit inside a word never matches, nor
# a counter key (R4-Slovak; G4-KY stays a round).
ROUND = re.compile(r'(?<![\w/])(?:[RGCQ]|round\s?)([1-9]\d?)(?![\w%]|(?-i:-[A-Z][a-z]))|(?<![\w&])#([1-9]\d?)(?!\w)', re.I)
ROUND_BEFORE_COUNTER_KEY = re.compile(r'(?<![\w/])(?:[RGCQ]|round\s?)([1-9]\d?)(?![\w%])|(?<![\w&])#([1-9]\d?)(?!\w)', re.I)
CLOCK = re.compile(r'(?<![\d:.])([01]?\d|2[0-3]):([0-5]\d)(?::([0-5]\d))?(?![\d:])(Z)?')

SELF = re.compile(r'\b(?:I|my|our|ours|we|us|own)\b', re.I)
PEER = re.compile(r'\b(?:you|your|their|they|them|according to|reported by|please|anyone|lead(?:ing)?|ahead)\b|@\w', re.I)
PREDICT = re.compile(r'\b(?:due|nominal\w*|expect\w*|predict\w*|forecast\w*|project\w*|likely|target\w*|ETA|next|would|should|'
                     r'estimat\w*|scheduled|schedule|candidate|if|plan\w*|window\w*|early|alt|alternate|hypothe\w*|'
                     r'anticipat\w*|pending|will)\b|->|→', re.I)
ARRIVE = re.compile(r'\b(?:arriv\w*|prompt\w*|received|came|appeared|start\w*|began|begin|activat\w*|opened|landed|'
                    r'hit|showed|observed|saw)\b', re.I)
ANSWER = re.compile(r'\b(?:answer\w*|submit\w*|receipt|responded|replied)\b', re.I)
DEADLINE = re.compile(r'\b(?:deadline|window to|closes|closed|expires?)\b', re.I)
CONFIRM = re.compile(r'\b(?:confirm\w*|actual|exact(?:ly)?)\b', re.I)
NOW = re.compile(r'\b(?:now|current(?:ly)?|at posting|at this post|as of)\b', re.I)
# counter keys are stamped on the server clock
_OTHER = r'|\bserver\b|\bwiki[- ]?local\b|\bexternal\b|\bshared\b|\bGMT\b|\bZ\b|\bcounter\w*|\bincrement\w*'
# "arrived 04:46:20 scaffold UTC" (A3Feb21Cashier) names the task clock and says it runs on UTC: not another clock
_TASK_UTC = r'(?<!task )(?<!scaffold )(?<!harness )(?<!wall )(?<!task clock )(?<!task-clock )'
OTHER_CLOCKS = {(glued, task_utc): re.compile((_TASK_UTC if task_utc else '') + (r'\bUTC' if glued else r'\bUTC\b') + _OTHER, re.I)
                for glued in (True, False) for task_utc in (True, False)}


def other_clock():
    return OTHER_CLOCKS[('utc_glued' not in OFF, 'scaffold_utc' not in OFF)]
TASK_CLOCK = re.compile(r'\b(?:task|scaffold|harness|wall)\b', re.I)
# a clock right after an offset: "Q1+2h15 (13:45:39)"
DURATION_END = re.compile(r'[+-]\s*\d+\s*[hm][\dms]*\s*[(=:]?\s*$', re.I)
PAIR = re.compile(r'\b(arriv\w*|prompt|deadline|answer\w*)/(arriv\w*|prompt|deadline|answer\w*)\s*\S*$', re.I)
# a forecast word earlier in the sentence covers later clocks ("Projected G3 05:13:32, G4 05:20:08") until a report
SCOPE_PREDICT = re.compile(r'\b(?:project\w*|forecast\w*|expect\w*|predict\w*|estimat\w*|if|would|plan\w*|'
                           r'scheduled?|nominal\w*|hypothe\w*|ETA)\b', re.I)
PAST_REPORT = re.compile(r'\b(?:arrived|received|confirmed|answered|submitted|came|began|started|appeared|observed|'
                         r'hit|was|were)\b', re.I)
QUALIFY = re.compile(r'[~≈?]|\b(?:approx\w*|about|around|roughly|estimat\w*|alt\w*|or|candidate|if|maybe|perhaps|'
                     r'guess\w*|speculat\w*|unconfirmed|possibl\w*)\b', re.I)


CAMEL = re.compile(r'(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])')  # OpenAIDec23Police -> Open AI Dec23 Police


def date_tags(text, name=False):
    """Date tags (mar13) in text, including inside names such as GroceryAgentMar13X."""
    split = name or 'camel_text' not in OFF
    return {f'{m.group(1)[:3].lower()}{int(m.group(2)):02d}' for m in DATE_TAG.finditer(CAMEL.sub(' ', text) if split else text)
            if 1 <= int(m.group(2)) <= 31}


def own_tags(t):
    names = {t['name']} | set(t.get('merged_names', [])) | {e.get('signed_as') for e in t['edits'] if e.get('signed_as')}
    return set().union(*(date_tags(n.split('#')[0], name=True) for n in names if n))


def post_block(A, e):
    """The edit's own post: fresh lines contiguous with the signed line, up to the previous signed line. For an
    attributed unsigned edit (its line carries no signoff), the fresh block from its line down to the next signoff."""
    r = A.revs[e['rev']]
    body, fresh = r['body'].split('\n'), A.fresh_lines(r)
    i = e['line'] - 1
    if i >= len(body):
        return ''
    if POST_SIG.search(body[i].rstrip()):
        j = i
        while j - 1 in fresh and i - (j - 1) <= MAX_BLOCK and not POST_SIG.search(body[j - 1].rstrip()) and body[j - 1].strip():
            j -= 1
        return '\n'.join(body[j:i + 1])
    k = i
    while k + 1 in fresh and k + 1 < len(body) and k + 1 - i <= MAX_BLOCK and body[k + 1].strip():
        k += 1
        if POST_SIG.search(body[k].rstrip()):
            k -= 1
            break
    return '\n'.join(body[i:k + 1])


def seconds(m):
    return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + int(m.group(3) or 0)




def classify(cue, prefix=''):
    """Event and status from the words between the round token (or the previous clock) and this clock; prefix is the
    sentence before that. A time worked out in the post ("+105m = 13:19:16") is status 'derived'."""
    if cue.rstrip().endswith(('=', '≈')) or 'duration_end' not in OFF and DURATION_END.search(cue):
        return 'derived', 'derived'
    predicted = bool(PREDICT.search(cue)) or bool(SCOPE_PREDICT.search(prefix)) and not PAST_REPORT.search(cue)
    # the event word nearest the clock wins ("arrived 20:58:37, timer 20s, deadline 20:58:57")
    hits = [(m.end(), event) for pattern, event in ((DEADLINE, 'deadline'), (ANSWER, 'answer'), (ARRIVE, 'arrival'))
            for m in pattern.finditer(cue)]
    if hits:
        event = max(hits)[1]
    else:
        event = 'confirmed' if CONFIRM.search(cue) else 'now' if NOW.search(cue) else 'unspecified'
    if predicted and event in ('arrival', 'unspecified', 'confirmed'):
        return 'due', 'predicted'
    status = 'predicted' if predicted else 'reported' if event != 'unspecified' else 'unspecified'
    return event, status


def owner_of(text, own, before='', question=False, about_other=False):
    """before: the sentence ahead of this clause. A clause with no first-person or own-tag cue is a peer's when the
    sentence is a question, names another cohort earlier ("Saw JUL31 fast R5; R6 due 18:38:00"), or opens as a request
    to a peer ("Please relay R4 at your 19:43:54; if possible post heartbeat around 19:35"). about_other: the post
    opens on another cohort ("May28: are you still active? R5 was due task 14:18:54"), so unmarked clauses are theirs."""
    question = question and 'question' not in OFF
    about_other = about_other and 'about_other' not in OFF
    self_ = bool(SELF.search(text)) or bool(own & date_tags(text)) and not question
    peer = bool(PEER.search(text)) or bool(date_tags(text) - own - {'jan01'} and not self_)
    if not self_ and (question or about_other or 'before_tag' not in OFF and date_tags(before) - own - {'jan01'}
                      or 'before_peer' not in OFF and PEER.search(before) and not SELF.search(before)):
        peer = True
    return 'mixed' if self_ and peer else 'self' if self_ else 'peer' if peer else 'unmarked'


def claims(text, own=frozenset()):
    """Every clock in the post, as a claim. The round is the nearest round token before it in the same sentence
    (a round carried across a ';' clause is marked round_inherited), else None."""
    text = POST_SIG.sub('', text)
    out = []
    opening = re.match(r'(?:[^\n!?.]|\.(?!\s))+', text.lstrip(" *#>-'=")) if text.strip() else None
    head = opening.group() if opening else ''
    about_other = bool(date_tags(head) - own - {'jan01'}) and not own & date_tags(head) and not SELF.search(head)
    for sent in re.finditer(r'(?:[^\n!?.]|\.(?!\s))+', text):
        s, base = sent.group(), sent.start()
        question = text[sent.end():sent.end() + 1] == '?'
        tokens = list((ROUND_BEFORE_COUNTER_KEY if 'counter_key' in OFF else ROUND).finditer(s))
        # clauses end at ';' so owner and clock cues stay local
        bounds = [0] + [m.end() for m in re.finditer(';', s)] + [len(s)]
        prev_end = 0
        for c in CLOCK.finditer(s):
            last = max((m for m in tokens if m.start() < c.start()), key=lambda m: m.start(), default=None)
            rnd = int(last.group(1) or last.group(2)) if last else None
            # R generic, G grocery, C clothing, Q quiz, 'round', '#': rounds compare only within a tag and a task
            tag = None if not last else '#' if last.group(2) else last.group(0)[0].upper() if len(last.group(0).rstrip('0123456789 ')) == 1 else 'round'
            cue_start = max(prev_end, last.start()) if last else prev_end
            clause_start = max(b for b in bounds if b <= c.start())
            clause = s[clause_start:min(b for b in bounds if b > c.start())]
            inherited = last is not None and last.start() < clause_start
            cue = s[cue_start:c.start()]
            event, status = classify(cue, s[:cue_start])
            pair = PAIR.search(s[max(0, cue_start - 40):cue_start]) if cue.strip() == '/' and out else None
            if pair:
                event, out[-1]['event'] = classify(pair.group(2), s[:cue_start])[0], classify(pair.group(1), s[:cue_start])[0]
            date = DATE_TAG.search(s[max(cue_start, c.start() - 8):c.start()])
            out.append({
                'round': rnd, 'round_tag': tag, 'round_inherited': inherited, 'raw': c.group(0), 'value_s': seconds(c),
                'precision': 'second' if c.group(3) else 'minute', 'event': event, 'status': status,
                'owner': owner_of(clause, own, s[:clause_start], question, about_other),
                'clock': 'other' if c.group(4) or other_clock().search(clause) else 'task' if TASK_CLOCK.search(clause) else 'unspecified',
                'post_names_task_clock': bool(TASK_CLOCK.search(text)),
                'qualified': bool(QUALIFY.search(s[cue_start:min(c.end() + 12, len(s))])),
                'task_date': f'{date.group(1)[:3].lower()}{int(date.group(2)):02d}' if date else None,
                'start': base + c.start(), 'cue': cue.strip()[-80:],
            })
            prev_end = c.end()
    return out


def main():
    A = Archive(ROOT / 'full-wiki-logs.zip', None)
    rows, stats = [], Counter()
    for line in open(TRAJECTORIES):
        t = json.loads(line)
        own = own_tags(t)
        for e in t['edits']:
            text = post_block(A, e)
            cs = claims(text, own)
            rows.append({'trajectory': t['id'], 'name': t['name'], 'own_tags': sorted(own), 'rev': e['rev'],
                         'time': e['time'], 'page': e['page'], 'family': e.get('family'), 'scope': e['scope'],
                         'label': e['label'], 'flags': e['flags'], 'years': sorted(set(YEAR.findall(text))),
                         'text': text, 'claims': cs})
            stats['edits'] += 1
            stats['edits_with_a_clock'] += bool(cs)
            stats['claims'] += len(cs)
            for c in cs:
                stats[f"owner:{c['owner']}"] += 1
                stats[f"status:{c['status']}"] += 1
                stats[f"event:{c['event']}"] += 1
                stats[f"clock:{c['clock']}"] += 1
                stats['with_round'] += c['round'] is not None
                stats['second_precision'] += c['precision'] == 'second'
    with open(HERE / 'claims.jsonl', 'w') as f:
        f.writelines(json.dumps(r, ensure_ascii=False) + '\n' for r in rows)
    print(json.dumps(dict(sorted(stats.items())), indent=1))


if __name__ == '__main__':
    main()
