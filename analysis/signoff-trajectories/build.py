"""Signoff trajectories: agent activity reconstructed from "-- Name" signoffs alone, with review flags.

  python3 analysis/signoff-trajectories/build.py
      -> trajectories.jsonl (one per signoff name with 2+ edits in scope), summary.json, review_queue.tsv

Assignment: every revision in the collusion.wiki export (full-wiki-logs.zip) goes to the last signoff in its fresh
text (what the edit wrote): the last line that ends in "-- Name", unless, in scope, the fresh lines below that line
(all of them, when there is none) hold one of the other signoff forms in unsigned-pages/rule_candidates.py. Names must
start with a capital: the only lowercase match in the archive is a "--help" CLI flag. The forms are tried in this
order, first hit wins; the edit keeps the rule in `signoff_rule` and a "-- Name" above it in other_signers:
  R1  "-- [[Name]]"                       R3  "-- Name" then junk ("\n", "$(date +%s)", a few words)
  R5  "-- Sep21 watcher" (dated phrase)    R2  "-- Name: ..." at or near line start
  R4  "Name:" opening one of the first two lines, when marked LIVE/UPDATE, followed by self-report words, or equal to
      the editor label or its tail (May24OAI under the label SectorAgentMay24OAI). The name is kept as written.
Vocative prefixes ("Name: are you ..."), bare dates and dateless team phrases stay unsigned. Edits with no signoff in
any form are left out.
Scope: pages the export classifies into the 41 inventory task families, plus relay-coordination pages, whose
family is inferred from the trajectory's family-page edits. A trajectory needs two or more edits (any second
post counts); a name's edits on other pages are kept as out-of-scope activity.

Nothing is merged or dropped on suspicion. Doubts become flags for human review:
  edit        copied      every line the edit signed with its name was already on the page (matched ignoring
                          non-ASCII bytes): a re-save, by the agent or by someone sweeping its post along
              reencoded   carried lines rewritten with only non-ASCII bytes changed (mojibake), so the signoff may
                          be someone else's older text
              multipost   two or more "-- Name" lines in the fresh text; the last signoff is a weaker guide
              rule_signoff  the name was read from a "Name:" prefix (R4, 89-96% agreement with end signoffs) or a
                          dated phrase (R5), not a signoff; R1-R3 are plain signoffs in another spelling and are not flagged
  trajectory  multi_day   edits span more than 24 h (most agents did not persist beyond a day)
              generic     bare date token (Sep05), a stock name (OpenAIResearcher) or a phrase (Sep21 watcher): weak
                          prior of one agent
              alias       another name that is the same once a date token is dropped from one side, or once case is
                          ignored (AgentOpenResearch / AgentOpenResearchApr10): possibly one agent under two signoffs.
                          Two names with different dates (OECDEquityJun06Agent / OECDEquityJul14Scout) are not aliases,
                          and generic names are never aliases: a dated OpenAIResearcherJul13 is not the stock OpenAIResearcher.
  edit        proposed_attribution  a hand attribution proposed in unsigned-attribution-sample/proposals.json and not
                          yet reviewed: the edit (unsigned, or signed under another name, kept in signed_as) is placed
                          with its proposed author; `proposed` holds the proposal id and confidence
              alias_merge a one-off name's only in-scope edit, moved into the trajectory of its alias because that
                          trajectory has an edit on the same page, under the same editor label, within MERGE_WINDOW_S
                          (OAI7C97 -> OAI7C97Dec15, 51 s apart). The edit keeps its own signoff in signed_as. Exactly
                          one candidate trajectory must qualify, or nothing moves.
              containment_merge  a one-off name's only in-scope edit, moved into the trajectory whose name contains it
                          as a run of camel tokens or is contained in it (Nov16 -> OpenAINov16CVD, ChatGPTJul19 ->
                          ChatGPTJul19Agent, OpenAIResearchDec30CVD -> OpenAIResearchDec30): shared date tag, compatible
                          families, an edit within CONTAIN_WINDOW_S, no clashing round (clock-consistency's compare), and
                          for a generic one-off the same editor label. Exactly one trajectory must qualify. Keeps signed_as.

Hand-review calls in reviews.json override the grouping for named trajectories:
  split      cut a name's edits before the listed revisions (ids S:Name#1, S:Name#2, ...; each part must still reach
             two in-scope edits)
  partition  like split, for runs whose edits interleave: each of "groups" ({"run": label, "revs": [...]}) becomes a
             part, numbered by its first edit (S:Name#1, #2, ...; the two-edit rule applies to each). Every edit of the
             name must be in exactly one group or in "unplaced"; unplaced edits go to no trajectory, as in exclude
  keep       reviewed and left whole (its trajectory-level flags drop out of review_queue.tsv)
  not_alias  the name and each name in "of" are different agents: no alias merge, no alias candidate between them
  reassign   the listed revisions were written by "to", not by the name their last signoff shows (a carried line);
             they move to "to" with the author's own line as `line`/`text` and the carried signoff kept in
             `reassigned_from`. The own line is the first fresh line opening with "to", or with `own_line[rev]` when
             the author's line does not start with its name (e.g. a save test "F20")
  exclude    the listed revisions were not made by the name's agent (someone else's save swept its post along, or a
             digest rewrote it); they go to no trajectory, and "by" maps each to its likely author
  merge      the name and "into" are one agent under two signoffs: all the name's edits move to "into", each keeping
             its own signoff in `signed_as` and the review in `merged_from`
  attribute  the listed revisions carry no signoff, but the review found "name" wrote them (hand attribution from the
             page, clock or claims). They join the name's edits with the review in `attributed`, and `line`/`text` is
             the edit's first fresh line. The name need not sign anything, so a reviewed agent with no signoff at all
             can reach two edits. Several attribute reviews may share a name, and may sit beside that name's other review.
Reviewed edits carry a `review` field and leave the edit rows of review_queue.tsv. A reassign or exclude revision that
the signoff reading already gives to the review's author ("to", or the exclude's "by") needs no move and is listed in
summary.json under reviews_applied.already_by_rule.

Every trajectory has `name_from`: 'signoff' when some edit anywhere signs that name, else where the name we assigned
comes from ('label', 'page title', 'post text', 'coined'; see name_origin). Only attribute reviews and proposals
introduce names, so anything but 'signoff' marks a name we made for an agent that never signed it.
"""
import json, re, sys, zipfile
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT / 'analysis' / 'human-verification'))
sys.path.insert(0, str(ROOT / 'analysis' / 'unsigned-pages'))
sys.path.insert(0, str(ROOT / 'analysis' / 'clock-consistency'))
import server  # noqa: E402
from rule_candidates import read_signoff  # noqa: E402
import extract, check  # noqa: E402  (clock claims, for merge_contained's clash test)

REVIEWS = HERE / 'reviews.json'
PROPOSALS = HERE / 'no-proposals.json'  # post-verification build: pending LLM proposals are never placed (file does not exist)
INVENTORY = ROOT / 'analysis/family-inventory/inventory.json'  # public release: vendored copy of the 41-family inventory
RELAY = 'relay-coordination'
MON = r'(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*'
DATE_TOKEN = re.compile(MON + r'\d{1,2}')
BARE_DATE = re.compile(r'^' + MON + r'\d{1,2}$')
STOCK = re.compile(r'^(?:OpenAI|OAI|Agent)?(?:Research(?:er|Agent|Helper)?|Helper|Assistant|Agent|Bot)(?:OpenAI|OAI)?X?$', re.I)
MERGE_WINDOW_S = 600
CONTAIN_WINDOW_S = 24 * 3600
CAMEL = extract.CAMEL  # OpenAINov16CVD -> Open AI Nov16 CVD
T = lambda s: datetime.fromisoformat(s.replace('Z', '+00:00')).timestamp()


def alias_key(name):
    return DATE_TOKEN.sub('', name).lower().rstrip('x_-.') or name.lower()


def generic(name):
    return bool(BARE_DATE.match(name) or STOCK.match(name) or ' ' in name)


class Origins:
    """The earlier revision of the same page where a line first appeared, matched on its ASCII skeleton, so a
    re-save that sweeps up older posts (whole-page rewrites, mojibake round trips) shows them as copies."""

    def __init__(self, A):
        self.A, self.skeletons = A, {}

    def lines(self, rid):
        if rid not in self.skeletons:
            self.skeletons[rid] = {server.ascii_skeleton(l) for l in self.A.revs[rid]['body'].split('\n')}
        return self.skeletons[rid]

    def first_seen(self, rev_id, text):
        r = self.A.revs[rev_id]
        sk = server.ascii_skeleton(text)
        for rid in self.A.pages[r['page_key']]:
            if int(self.A.revs[rid]['seq']) >= int(r['seq']):
                return None
            if sk in self.lines(rid):
                return rid
        return None


def aliases_of(name, same_key, not_alias=frozenset()):
    """not_alias: pairs (frozensets) a review ruled to be different agents."""
    if generic(name):
        return []
    dated = bool(DATE_TOKEN.search(name))
    return sorted(n for n in same_key if n != name and not generic(n) and frozenset((name, n)) not in not_alias
                  and (n.lower() == name.lower() or dated != bool(DATE_TOKEN.search(n))))


def load_reviews(by_name):
    """Hand-review calls from reviews.json, checked against the build so a stale call fails loudly instead of doing nothing.
    Attribute reviews are loaded separately (load_attributions) and applied first, so a name they create can be reviewed."""
    listed = [r for r in json.loads(REVIEWS.read_text())['reviews'] if r['decision'] != 'attribute']
    reviews = {r['name']: r for r in listed}
    if len(reviews) != len(listed):
        raise SystemExit('reviews.json: one review per name')
    holder = {e['rev']: n for n, es in by_name.items() for e in es}
    for name, r in reviews.items():
        if name not in by_name:
            raise SystemExit(f'reviews.json: no signoff name {name}')
        revs = {e['rev'] for e in by_name[name]}
        # a reassigned or excluded revision that the signoff reading already gives to the review's author ("to", or the
        # exclude's likely author in "by"), because a signoff form below the carried one now wins, needs no move
        author = lambda x: r.get('to') if r['decision'] == 'reassign' else r.get('by', {}).get(x) if r['decision'] == 'exclude' else None
        agreed = {x for x in r.get('revs', []) if author(x) and holder.get(x) == author(x)}
        if agreed:
            r['already_by_rule'] = sorted(agreed)
        missing = [x for x in r.get('split_before', []) + r.get('revs', []) if x not in revs | agreed] + [n for n in r.get('of', []) if n not in by_name]
        if r['decision'] == 'merge' and (r.get('into') not in by_name or r['into'] == name
                                         or reviews.get(r['into'], {}).get('decision') == 'merge'):
            missing.append(f"into={r.get('into')}")
        if r['decision'] == 'partition':
            placed = [x for g in r.get('groups', []) for x in g['revs']] + r.get('unplaced', [])
            missing += [x for x in placed if x not in revs] + [f'unplaced {x}' for x in sorted(revs - set(placed))]
            missing += [f'placed twice {x}' for x, n in Counter(placed).items() if n > 1]
            if len(r.get('groups', [])) < 2 or not all(g['revs'] for g in r['groups']):
                missing.append('groups: two or more, none empty')
        if r['decision'] not in ('split', 'partition', 'keep', 'not_alias', 'reassign', 'exclude', 'merge') or missing or (r['decision'] == 'reassign' and not r.get('to')):
            raise SystemExit(f'reviews.json: bad review for {name}: decision={r["decision"]} missing={missing}')
    return reviews


def load_attributions():
    listed = [r for r in json.loads(REVIEWS.read_text())['reviews'] if r['decision'] == 'attribute']
    revs = Counter(x for r in listed for x in r.get('revs', []))
    bad = [r['name'] for r in listed if not r.get('revs')] + [x for x, n in revs.items() if n > 1]
    if bad:
        raise SystemExit(f'reviews.json: bad attribute reviews (no revs, or a rev attributed twice): {bad}')
    return listed


def attribute(by_name, attributions, make_edit, overridable=lambda e: False):
    """Add reviewed unsigned edits to the name a review found as their author. make_edit(rev) -> the caller's edit
    record for that revision, or None if the caller does not track it (raises KeyError for an unknown rev). An edit
    already held by a name fails loudly (a signed edit needs reassign), unless overridable(edit): oneoffs.py lets a
    hand call override a candidate rule's guess. Returns [(name, rev)]."""
    holder = {e['rev']: (n, e) for n, es in by_name.items() for e in es}
    done = []
    for r in attributions:
        for rev in r['revs']:
            if rev in holder:
                n, e = holder[rev]
                if not overridable(e):
                    raise SystemExit(f"reviews.json: attribute {rev} to {r['name']}, but it is already {n}'s (use reassign)")
                by_name[n].remove(e)
                if not by_name[n]:
                    del by_name[n]
            try:
                e = make_edit(rev)
            except KeyError:
                raise SystemExit(f"reviews.json: attribute {r['name']}: no revision {rev}")
            if e is not None:
                by_name[r['name']].append({**e, 'attributed': review_note(r), 'review': review_note(r)})
                done.append((r['name'], rev))
    return done


def merge(by_name, reviews):
    """Fold a reviewed name's edits into "into" (one agent under two signoffs). Each edit keeps its own signoff in
    signed_as. Returns [(from, into, rev)]."""
    moved = []
    for name, r in reviews.items():
        if r['decision'] == 'merge':
            for e in by_name.pop(name, []):
                by_name[r['into']].append({**e, 'signed_as': e.get('signed_as', name), 'merged_from': review_note(r)})
                moved.append((name, r['into'], e['rev']))
    return moved


def load_proposals():
    """Hand attributions proposed in PROPOSALS but not yet reviewed into reviews.json (no review.applied) or withdrawn
    (review.decision reject). Returns [(proposal id, confidence, name, rev)] for every target and companion edit.
    `unattributable` calls place nothing. A revision two pending calls give to different names is an error.
    A pending trajectory merge (PROPOSALS 'merges') comes back as one row whose rev is the 'from' trajectory, "S:Name";
    apply_proposals expands it to that trajectory's edits."""
    if not PROPOSALS.exists():
        return []
    out, claimed = [], {}
    P = json.loads(PROPOSALS.read_text())
    for m in P.get('merges', []):
        r = m.get('review', {})
        if not (r.get('applied') or r.get('decision') == 'reject'):
            out.append((m['id'], m['confidence'], m['into'][2:], m['from']))
    for c in P['calls']:
        r = c.get('review', {})
        if r.get('applied') or r.get('decision') == 'reject' or c['action'] == 'unattributable':
            continue
        name = (c.get('trajectory') or '')[2:] or c.get('name')
        if not name:
            raise SystemExit(f"proposals.json: {c['id']} has no trajectory or name")
        rows = [(c['id'], c['confidence'], name, c['rev'])]
        rows += [(c['id'], k.get('confidence', c['confidence']), name, k['rev']) for k in c.get('linked', [])]
        for pid, _, n, rev in rows:
            if rev in claimed and claimed[rev][1] != n:
                raise SystemExit(f'proposals.json: {rev} is given to {claimed[rev][1]} by {claimed[rev][0]} '
                                 f'and to {n} by {pid}')
            claimed.setdefault(rev, (pid, n))
        out += rows
    return out


def apply_proposals(by_name, proposals, make_edit):
    """Place each proposed edit with its proposed author, flagged proposed_attribution for review (nothing moves on
    suspicion without a flag). An unsigned edit gets make_edit(rev) (None: the caller does not track it); an edit held
    by another name moves, keeping that signoff in signed_as; an edit its author already holds is left alone. Edits a
    review already placed (attributed, reassigned, merged) or excluded are skipped. Returns [(id, name, rev, from)].
    A pending merge row ("S:Name" as rev) moves every edit Name holds at the start, before any call moves one; a revision
    a call and a merge would give to different names is an error."""
    holder = {e['rev']: (n, e) for n, es in by_name.items() for e in es}
    rows, given = [], {}
    for pid, conf, name, rev in proposals:
        revs = [e['rev'] for e in sorted(by_name.get(rev[2:], []), key=lambda e: e['time'])] if rev.startswith('S:') else [rev]
        for x in revs:
            if given.get(x, (pid, name))[1] != name:
                raise SystemExit(f'proposals.json: {x} is given to {given[x][1]} by {given[x][0]} and to {name} by {pid}')
            given.setdefault(x, (pid, name))
            rows.append((pid, conf, name, x))
    done = []
    for pid, conf, name, rev in rows:
        note = {'id': pid, 'confidence': conf, 'source': str(PROPOSALS.relative_to(ROOT))}
        if rev in holder:
            n, e = holder[rev]
            if n == name or 'review' in e or 'merged_from' in e:
                continue
            by_name[n].remove(e)
            if not by_name[n]:
                del by_name[n]
            e = {**e, 'signed_as': e.get('signed_as', n)}
        else:
            try:
                e = make_edit(rev)
            except KeyError:
                raise SystemExit(f'proposals.json: {pid}: no revision {rev}')
            if e is None:
                continue
            n = None
        by_name[name].append({**e, 'proposed': note, 'flags': e['flags'] + ['proposed_attribution']})
        holder[rev] = (name, by_name[name][-1])
        done.append((pid, name, rev, n))
    return done


def edit_record(A, rid, scope, fam, name=None, line=None, other_signers=(), copied=False, rule=None):
    """One edit row of a trajectory. Unsigned (attributed) edits have no signoff line, so `line`/`text` is the first
    fresh line. rule: the rule_candidates rule that read the name, when the edit has no "-- Name" line end."""
    r = A.revs[rid]
    body = r['body'].split('\n')
    if line is None:
        line = next((i for i in sorted(A.fresh_lines(r)) if i < len(body) and body[i].strip()), 0)
    f = A.review_flags(rid)
    return {'rev': rid, 'time': r['time'], 'page': r['page_id'], 'family': fam if scope == 'family' else None, 'scope': scope,
            'label': r['label'], 'line': line + 1, 'text': body[line] if line < len(body) else '',
            'other_signers': sorted(set(other_signers)),
            **({'signoff_rule': rule} if rule else {}),
            'flags': [k for k, on in (('reencoded', f['reencoded']), ('multipost', f['posts'] > 1), ('copied', copied),
                                      ('rule_signoff', bool(rule) and rule[:2] in ('R4', 'R5'))) if on]}


def read_edit(A, origins, rid, scope, fam):
    """(name, edit record) for a revision's signoff, or (None, None) when it has none: the last signoff in its fresh
    text. That is the last "-- Name" line end, unless, in scope, a rule_candidates form reads a name from the fresh lines
    below it (all of them when there is no "-- Name" line); the form then wins and is kept in `signoff_rule`, and the
    "-- Name" above it goes to other_signers (LFSequenceCollabMay03@16: a carried "-- LFRelayNov14" above the author's
    "-- [[OpenAIResearchMay07]]")."""
    r = A.revs[rid]
    body = r['body'].split('\n')
    signed = [(m.group(1), i) for i in sorted(A.fresh_lines(r)) if i < len(body) for m in [server.POST_SIG.search(body[i].rstrip())] if m and m.group(1)[0].isupper()]
    rule = None
    if scope != 'other':
        below = signed[-1][1] if signed else -1
        idx = [i for i in sorted(A.fresh_lines(r)) if below < i < len(body) and body[i].strip()]
        form, name, k = read_signoff([body[i] for i in idx], r['label']) if idx else (None, None, None)
        if form and form.startswith('R'):
            rule = form
            signed = signed + [(name, idx[k])]
    if not signed:
        return None, None
    name, line = signed[-1]
    copied = all(origins.first_seen(rid, body[i]) for n, i in signed if n == name)
    return name, edit_record(A, rid, scope, fam, name, line, [n for n, _ in signed if n != name], copied, rule)


NAME_FROM = ('signoff', 'label', 'page title', 'post text', 'coined')


def name_origin(A, rule_signed=frozenset()):
    """Where a trajectory or one-off name comes from, for names a review or proposal introduced rather than a signoff.
    Returns origin(name) -> the first that holds: 'signoff' (the name is signed on some fresh line by the signoff rule,
    anywhere in the archive, or is in rule_signed: names read by the other signoff forms), 'label' (an editor label), 'page title', 'post text' (it appears as a word in some
    edit's fresh text, e.g. a 'Name:' prefix or a mid-text signoff), else 'coined'. Anything but 'signoff' is a name
    we assigned."""
    signed, labels, pages, fresh = set(rule_signed), set(), set(), []
    for r in A.revs.values():
        labels.add(r['label'])
        pages.add(r['page_id'].split('/', 1)[-1])
        body = r['body'].split('\n')
        lines = [body[i] for i in sorted(A.fresh_lines(r)) if i < len(body)]
        fresh.append('\n'.join(lines))
        for l in lines:
            m = server.POST_SIG.search(l.rstrip())
            if m and m.group(1)[0].isupper():
                signed.add(m.group(1))
    text = '\n'.join(fresh)

    def origin(name):
        base = name.split('#')[0]
        if base in signed:
            return 'signoff'
        if base in labels:
            return 'label'
        if base in pages:
            return 'page title'
        if re.search(rf'(?<![\w-]){re.escape(base)}(?![\w-])', text):
            return 'post text'
        return 'coined'
    return origin


def review_note(r):
    return {k: r[k] for k in ('decision', 'reason', 'reviewer', 'date')}


def not_alias_pairs(reviews):
    return {frozenset((n, o)) for n, r in reviews.items() if r['decision'] == 'not_alias' for o in r['of']}


def exclude(by_name, reviews):
    """Drop reviewed edits that the name's agent did not make (a sweep or digest by someone else). The edit goes to no
    trajectory; the review's `by` records the likely author. Returns [(name, rev, by)]."""
    dropped = []
    for name, r in reviews.items():
        if r['decision'] in ('exclude', 'partition'):  # a partition's unplaced edits leave like excluded ones
            for e in [e for e in by_name[name] if e['rev'] in r.get('revs' if r['decision'] == 'exclude' else 'unplaced', [])]:
                by_name[name].remove(e)
                dropped.append((name, e['rev'], r.get('by', {}).get(e['rev'], '')))
    return dropped


def reassign(by_name, reviews, line_of=None):
    """Move reviewed edits to the name a review found as their author. line_of(edit, name) -> (line, text) of the
    author's own post in the edit, when the caller can read bodies. Returns [(from, to, rev)]."""
    moved = []
    for name, r in reviews.items():
        if r['decision'] != 'reassign':
            continue
        for e in [e for e in by_name[name] if e['rev'] in r['revs']]:
            by_name[name].remove(e)
            new = {**e, 'reassigned_from': {'name': name, **({'line': e['line'], 'text': e['text']} if 'line' in e else {})},
                   'review': review_note(r)}
            if 'flags' in e:  # copied described the carried signoff, not the author's own post
                new['flags'] = [f for f in e['flags'] if f != 'copied']
            if line_of:
                new['line'], new['text'] = line_of(e, r.get('own_line', {}).get(e['rev'], r['to']))
            by_name[r['to']].append(new)
            moved.append((name, r['to'], e['rev']))
    return moved


def split_edits(edits, split_before):
    """Cut a time-sorted edit list before each listed revision."""
    parts, cur = [], []
    for e in edits:
        if e['rev'] in split_before and cur:
            parts.append(cur)
            cur = []
        cur.append(e)
    return parts + [cur]


def partition_edits(edits, groups):
    """One part per group of revisions, in order of each part's first edit. Returns [(label, edits)]."""
    parts = [(g['run'], [e for e in edits if e['rev'] in set(g['revs'])]) for g in groups]
    return sorted(parts, key=lambda p: p[1][0]['time'])


def merge_one_offs(by_name, alias_groups, not_alias=frozenset()):
    """Move a one-off's edit into its alias's trajectory when that trajectory posted on the same page under the same
    label within MERGE_WINDOW_S. Returns [(from_name, to_name, rev)]."""
    inscope = lambda n: [e for e in by_name[n] if e['scope'] != 'other']
    merges = []
    for name in list(by_name):
        own = inscope(name)
        if len(own) != 1:
            continue
        e = own[0]
        hits = [a for a in aliases_of(name, alias_groups[alias_key(name)], not_alias) if len(inscope(a)) >= 2
                and any(x['page'] == e['page'] and x['label'] == e['label'] and abs(T(x['time']) - T(e['time'])) <= MERGE_WINDOW_S
                        for x in inscope(a))]
        if len(hits) == 1:
            by_name[name].remove(e)
            by_name[hits[0]].append({**e, 'signed_as': name, 'flags': e['flags'] + ['alias_merge']})
            merges.append((name, hits[0], e['rev']))
    return merges


def name_tokens(name):
    return [t.lower() for t in CAMEL.sub(' ', name.split('#')[0]).split()]


def token_run(a, b):
    """a's camel tokens are a contiguous run of b's, and b has more (Nov16 in OpenAINov16CVD)."""
    return 0 < len(a) < len(b) and any(b[i:i + len(a)] == a for i in range(len(b) - len(a) + 1))


def name_date_tags(name):
    return extract.date_tags(name.split('#')[0], name=True)


def clock_clash(A):
    """clash(edit, name, edits, into) -> True when a shared round of the edit's post and one of the edits' posts
    disagrees (clock-consistency's extract.claims and check.compare, each post read with its own name's date tags)."""
    def clash(e, name, xs, into):
        o = {'claims': extract.claims(extract.post_block(A, e), name_date_tags(name))}
        own = set().union(name_date_tags(into), *(name_date_tags(x['signed_as']) for x in xs if x.get('signed_as')))
        return any(check.compare(o, {'claims': extract.claims(extract.post_block(A, x), own)})[1] for x in xs)
    return clash


def merge_contained(by_name, not_alias, date_tags, clash):
    """Move a one-off's only in-scope edit into the trajectory whose name contains its name as a run of camel tokens,
    or is contained in it (Nov16 -> OpenAINov16CVD, ChatGPTJul19 -> ChatGPTJul19Agent, OpenAIResearchDec30CVD ->
    OpenAIResearchDec30), when the two names share a date tag, the families are compatible, the trajectory has an
    in-scope edit within CONTAIN_WINDOW_S, and no shared round clashes (clash(edit, name, edits, name) -> bool). A
    generic one-off (Nov16) also needs its editor label among the trajectory's. Exactly one trajectory must qualify.
    Hand-placed edits and names a review covers are left alone. Returns [(from_name, to_name, rev)]."""
    inscope = lambda n: [e for e in by_name[n] if e['scope'] != 'other']
    targets = [n for n in by_name if len(inscope(n)) >= 2 and not generic(n)]
    moves = []
    for name in list(by_name):
        own = inscope(name)
        if len(own) != 1 or not date_tags(name):
            continue
        e = own[0]
        if {'review', 'merged_from', 'proposed', 'attributed', 'signed_as'} & set(e):
            continue
        nt, tags = name_tokens(name), date_tags(name)
        hits = []
        for a in targets:
            at, xs = name_tokens(a), inscope(a)
            if not (token_run(nt, at) or token_run(at, nt)) or not tags & date_tags(a) or frozenset((name, a)) in not_alias:
                continue
            fams = {x['family'] for x in xs if x['scope'] == 'family'}
            if e['scope'] == 'family' and fams and e['family'] not in fams:
                continue
            if min(abs(T(x['time']) - T(e['time'])) for x in xs) > CONTAIN_WINDOW_S:
                continue
            if generic(name) and e['label'] not in {x['label'] for x in xs}:
                continue
            if clash(e, name, xs, a):
                continue
            hits.append(a)
        if len(hits) == 1:
            by_name[name].remove(e)
            by_name[hits[0]].append({**e, 'signed_as': name, 'flags': e['flags'] + ['containment_merge']})
            moves.append((name, hits[0], e['rev']))
    return moves


def main():
    families = {f['family_id'] for f in json.loads(INVENTORY.read_text())}
    with zipfile.ZipFile(ROOT / 'full-wiki-logs.zip') as z:
        page_family = {p['page_key']: p['page_family'] for p in map(json.loads, z.read('pages.jsonl').splitlines())}
    A = server.Archive(ROOT / 'full-wiki-logs.zip', None)  # public release: the verification tool's Store without its dossier inputs

    by_name, unsigned, per_family, rule_read = defaultdict(list), Counter(), defaultdict(Counter), []
    origins = Origins(A)
    for rid, r in A.revs.items():
        fam = page_family.get(r['page_key'])
        scope = 'family' if fam in families else 'relay' if fam == RELAY else 'other'
        name, e = read_edit(A, origins, rid, scope, fam)
        if scope == 'family':
            per_family[fam]['signed' if e else 'unsigned'] += 1
        if not e:
            unsigned[scope] += 1
            continue
        by_name[name].append(e)
        if 'signoff_rule' in e:
            rule_read.append((e['signoff_rule'], name, rid))

    def line_of(e, author):  # author: the name, or the review's own_line text for this revision
        body = A.revs[e['rev']]['body'].split('\n')
        hits = [i for i in sorted(A.fresh_lines(A.revs[e['rev']])) if i < len(body) and body[i].lstrip(' *#>-').startswith(author)]
        if not hits:
            raise SystemExit(f"reviews.json: {e['rev']} has no fresh line opening with {author}")
        return hits[0] + 1, body[hits[0]]

    def scope_of(rid):
        fam = page_family.get(A.revs[rid]['page_key'])
        return 'family' if fam in families else 'relay' if fam == RELAY else 'other', fam

    attributed = attribute(by_name, load_attributions(), lambda rid: edit_record(A, rid, *scope_of(rid)),
                           overridable=lambda e: 'signoff_rule' in e)  # a hand call beats a rule's reading, never a signoff
    overridden = {rev for _, rev in attributed} & {rev for _, _, rev in rule_read}
    for scope, _ in map(scope_of, (rev for _, rev in attributed if rev not in overridden)):
        unsigned[scope] -= 1
    reviews = load_reviews(by_name)
    not_alias = not_alias_pairs(reviews)
    reassigned = reassign(by_name, reviews, line_of)
    excluded = exclude(by_name, reviews)
    merged = merge(by_name, reviews)
    excluded_revs = {rev for _, rev, _ in excluded}
    proposed = apply_proposals(by_name, [p for p in load_proposals() if p[3] not in excluded_revs],
                               lambda rid: edit_record(A, rid, *scope_of(rid)))
    for scope, _ in map(scope_of, (rev for _, _, rev, n in proposed if n is None)):
        unsigned[scope] -= 1
    alias_groups = defaultdict(set)
    for name in by_name:
        alias_groups[alias_key(name)].add(name)
    merges = merge_one_offs(by_name, alias_groups, not_alias)
    contained = merge_contained(by_name, not_alias, name_date_tags, clock_clash(A))
    profile_names = json.loads((HERE / 'verified_profile_names.json').read_text())  # public release: frozen from the verification tool's profiles

    trajectories, split_parts, proposed_below_two = [], [], []
    name_from = name_origin(A, {n for _, n, _ in rule_read})
    for name, all_edits in by_name.items():
        all_edits.sort(key=lambda e: e['time'])
        review = reviews.get(name)
        decision = review['decision'] if review else None
        runs = (partition_edits(all_edits, review['groups']) if decision == 'partition' else
                [(None, p) for p in split_edits(all_edits, review['split_before'])] if decision == 'split' else [(None, all_edits)])
        parts = [p for _, p in runs]
        for part, (run, edits) in enumerate(runs, 1):
            tid = 'S:' + name + (f'#{part}' if len(parts) > 1 else '')
            inscope = [e for e in edits if e['scope'] != 'other']
            if len(parts) > 1:
                split_parts.append({'id': tid, 'edits_in_scope': len(inscope), 'first_rev': edits[0]['rev'] if edits else None,
                                    'kept': len(inscope) >= 2, **({'run': run} if run else {})})
            if len(inscope) < 2:
                proposed_below_two += [('O:' + tid[2:], e) for e in inscope if 'proposed' in e]
                continue
            fams = Counter(e['family'] for e in edits if e['family'])
            primary = fams.most_common(1)[0][0] if fams else None
            for e in edits:
                if e['scope'] == 'relay':
                    e['family_inferred'] = primary
            span_h = (T(inscope[-1]['time']) - T(inscope[0]['time'])) / 3600
            aliases = aliases_of(name, alias_groups[alias_key(name)], not_alias)
            flags = [k for k, on in (('multi_day', span_h > 24), ('generic', generic(name)),
                                     ('alias', bool(aliases)), ('several_families', len(fams) > 1)) if on]
            trajectories.append({
                'id': tid, 'name': name, **({'part': part} if len(parts) > 1 else {}), 'primary_family': primary, 'families': dict(fams),
                'edits_in_scope': len(inscope), 'family_page_edits': sum(e['scope'] == 'family' for e in inscope),
                'relay_page_edits': sum(e['scope'] == 'relay' for e in inscope), 'edits_elsewhere': len(edits) - len(inscope),
                'first': inscope[0]['time'], 'last': inscope[-1]['time'], 'span_hours': round(span_h, 2),
                'flags': flags, 'alias_candidates': aliases, 'flagged_edits': sum(bool(e['flags']) for e in inscope),
                'matches_existing_profile': profile_names.get(name, []),
                **({'review': review_note(review)} if review else {}),
                **({'merged_names': sorted({e['signed_as'] for e in edits if 'merged_from' in e})}
                   if any('merged_from' in e for e in edits) else {}),
                **({'attributed_edits': sum('attributed' in e for e in inscope)} if any('attributed' in e for e in edits) else {}),
                **({'proposed_edits': sum('proposed' in e for e in inscope)} if any('proposed' in e for e in edits) else {}),
                'name_from': name_from(name),
                'edits': edits})
    trajectories.sort(key=lambda t: (t['primary_family'] or '~', t['first']))

    (HERE / 'trajectories.jsonl').write_text(''.join(json.dumps(t, ensure_ascii=False) + '\n' for t in trajectories))
    queue = [(t['id'], 'trajectory', ','.join(t['flags']), '', t['first'], ', '.join(t['alias_candidates']))
             for t in trajectories if t['flags'] and t.get('review', {}).get('decision') != 'keep']
    queue += [(t['id'], 'edit', ','.join(e['flags']), e['rev'], e['time'], ', '.join(e['other_signers'] + ([e['signed_as']] if 'signed_as' in e else [])
                                                                                  + ([f"proposal {e['proposed']['id']} ({e['proposed']['confidence']})"] if 'proposed' in e else [])))
              for t in trajectories for e in t['edits'] if e['flags'] and e['scope'] != 'other' and 'review' not in e]
    queue += [(oid, 'edit', ','.join(e['flags']), e['rev'], e['time'], f"proposal {e['proposed']['id']} ({e['proposed']['confidence']}); one edit, no trajectory")
              for oid, e in proposed_below_two]
    (HERE / 'review_queue.tsv').write_text('trajectory\tlevel\tflags\trev\ttime\trelated_names\n' + ''.join('\t'.join(q) + '\n' for q in queue))

    fam_only = [t for t in trajectories if t['family_page_edits'] >= 2]
    summary = {
        'generated_from': 'full-wiki-logs.zip (collusion.wiki export) + 41-family inventory',
        'signoff_names_archive_wide': len(by_name),
        'trajectories': len(trajectories),
        'trajectories_with_2plus_edits_on_family_pages_alone': len(fam_only),
        'trajectories_without_any_flag': sum(not t['flags'] and not t['flagged_edits'] for t in trajectories),
        'trajectory_flags': dict(Counter(f for t in trajectories for f in t['flags'])),
        'edits_in_trajectories': sum(t['edits_in_scope'] for t in trajectories),
        'flagged_edits': sum(t['flagged_edits'] for t in trajectories),
        'families_covered': len({t['primary_family'] for t in trajectories if t['primary_family']}),
        'families_with_no_trajectory': sorted(families - {t['primary_family'] for t in trajectories}),
        'reviews_applied': {'kept_whole': sorted(n for n, r in reviews.items() if r['decision'] == 'keep'),
                            'split_parts': split_parts,
                            'not_alias': sorted(sorted(p) for p in not_alias),
                            'reassigned': [{'from': f, 'to': t, 'rev': r} for f, t, r in reassigned],
                            'already_by_rule': [{'name': n, 'decision': r['decision'], 'rev': x} for n, r in reviews.items()
                                                for x in r.get('already_by_rule', [])],
                            'excluded': [{'name': n, 'rev': r, 'likely_author': b} for n, r, b in excluded],
                            'merged': [{'from': f, 'into': t, 'rev': r} for f, t, r in merged],
                            'attributed': [{'name': n, 'rev': r} for n, r in attributed]},
        'alias_merges': [{'from': f, 'into': t, 'rev': r} for f, t, r in merges],
        'containment_merges': [{'from': f, 'into': t, 'rev': r} for f, t, r in contained],
        'rule_signoffs_below_a_signoff': sum('signoff_rule' in e and bool(e['other_signers']) for es in by_name.values() for e in es),
        'proposed_attributions': [{'proposal': i, 'name': n, 'rev': r, **({'from': f} if f else {})} for i, n, r, f in proposed],
        'single_edit_names_dropped': sum(sum(e['scope'] != 'other' for e in es) == 1 for es in by_name.values()),
        'relay_only_trajectories': sum(t['family_page_edits'] == 0 for t in trajectories),
        'assigned_names': dict(Counter(t['name_from'] for t in trajectories if t['name_from'] != 'signoff')),
        'per_family': dict(Counter(t['primary_family'] or '(relay only)' for t in trajectories).most_common()),
        'matching_an_existing_verified_profile_name': sum(bool(t['matches_existing_profile']) for t in trajectories),
        'unsigned_edits_left_out': dict(unsigned),
        'rule_signoffs': {'edits': len(rule_read), 'by_rule': dict(sorted(Counter(r for r, _, _ in rule_read).items())),
                          'names': len({n for _, n, _ in rule_read}),
                          'names_only_rule_read': sorted({n for _, n, _ in rule_read} - {n for n, es in by_name.items()
                                                         for e in es if 'signoff_rule' not in e and 'attributed' not in e}),
                          'overridden_by_attribute_review': sorted(overridden)},
        'family_page_edits_signed_vs_unsigned': {f: dict(per_family[f]) for f in sorted(families)},
    }
    (HERE / 'summary.json').write_text(json.dumps(summary, indent=1))
    print(json.dumps(summary, indent=1))


if __name__ == '__main__':
    main()
