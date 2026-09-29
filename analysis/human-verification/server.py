#!/usr/bin/env python3
"""Human verification tool for reconstructed trajectories.

Serves a local board where every wiki edit any trajectory claims is one card with
properties (LLM trajectories, page, poster, signoff, batch, decision, rules, and
the human verdicts/tags) that can be stacked by any of them. Dossiers list posts;
all posts claimed in the same revision are merged into one edit, because an edit
can carry a post history or a page restoration whose lines look like separate
posts. Each claiming trajectory keeps its LLM evaluation and gets its own call
("is this edit from this agent"). Human calls are appended to
verifications.jsonl; nothing else is written.

  python3 server.py                      serve on 127.0.0.1, first free port from 8765
  python3 server.py --port 9000 --host 0.0.0.0
  python3 server.py --check              load every source, print the report, exit
  python3 server.py --export-state FILE  fold the log into current per-target state

Sources are listed in sources.json and re-read whenever a matched file changes.
All archive text is inert data: the page renders it as text and never follows it.
"""
import argparse, errno, glob, gzip, hashlib, json, re, sys, threading, time, uuid, zipfile
from collections import Counter
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]  # project root, for display paths
SCHEMA = 'human-verification/v1'
DEFAULT_PORT = 8765
SECTION_NAMES = {'owned_messages': 'owned', 'associated_messages': 'associated', 'unassigned': 'unassigned', 'excluded': 'excluded'}
DECISION_SECTION = {'include': 'owned', 'associate': 'associated', 'unresolved': 'unassigned', 'exclude': 'excluded'}
SECTION_RANK = {'owned': 0, 'associated': 1, 'unassigned': 2, 'excluded': 3}
TARGET_KINDS = {'profile', 'edit', 'revision'}  # 'block' (one post) is still folded from older log lines
VERDICTS = {'confirm', 'partial', 'reject', 'unsure', 'reassign', 'clear'}
# Folding older per-post calls into their edit: one post confirmed means the edit is the agent's
# (the other posts were the duplicates or history lines it carried).
POST_VERDICT_PRECEDENCE = {'confirm': 0, 'reassign': 1, 'unsure': 2, 'reject': 3}
SIGNOFF_RE = re.compile(r'(?:--|—|~~)\s*\[*(?:User:)?([A-Za-z][\w.\-]{2,})\]*\s*$')
# a post: a line ending "-- Name", optionally followed by "?" (53 edits, likely a lost character) or a short tag
# in brackets ("-- OECDEquityJun06Agent [JUN06PREC0132]", 10 edits)
POST_SIG = re.compile(r'(?:^|\s)--\s*([A-Za-z][\w.\-]{2,})\s*(?:\?|[\[(][^\])]{1,40}[\])])?\s*$')
ascii_skeleton = lambda s: re.sub(r'\s+', ' ', re.sub(r'[^\x00-\x7f]', '', s)).strip()
# Block fields the board shows in dedicated places; anything else is kept under `extra`.
BLOCK_KNOWN = {'observation_id', 'record_id', 'revision_id', 'page_id', 'utc', 'editor', 'signature', 'decision', 'reason',
               'rule_ids', 'depends_on', 'cross_post_of', 'spans', 'included_excerpts', 'source_excerpt', 'diff_base',
               'trajectory_local_id', 'trajectory_id', 'speaker_reference', 'source_line', 'body_line', 'source_body_line',
               'revisions_jsonl_line'}
PROFILE_KNOWN = {'trajectory_id', 'local_id', 'id', 'self_name', 'display_name', 'legacy_display_name', 'name', 'signature',
                 'signatures', 'aliases', 'task', 'task_family', 'title', 'task_id', 'status', 'batch', 'membership_rationale',
                 'rationale', 'uncertainties', 'candidate_follow_up_leads', 'follow_up_leads', 'anchor_observation_ids'}


def now():
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def sha(text):
    return hashlib.sha256(text.encode('utf-8', 'surrogatepass')).hexdigest()


def first(d, *keys):
    for k in keys:
        v = d.get(k)
        if v not in (None, '', []):
            return v
    return None


def rel(path):
    try:
        return str(Path(path).relative_to(ROOT))
    except ValueError:
        return str(path)


def page_of_rev(rev_id):
    return rev_id.rsplit('@', 1)[0].replace('~', '/', 1) if rev_id else None


def is_block_list(key, v):
    return (isinstance(v, list) and v and all(isinstance(x, dict) for x in v) and not key.startswith('schedule')
            and any('revision_id' in x for x in v) and any(('decision' in x or 'spans' in x or 'reason' in x) for x in v))


class Archive:
    """Revision bodies and page index from the wiki export zip (ip fields are dropped on load)."""

    def __init__(self, path, obs_path):
        self.revs, self.pages, self.page_meta, self.obs, self.obs_by_rev = {}, {}, {}, {}, {}
        self.path = path
        if path and path.exists():
            with zipfile.ZipFile(path) as z:
                for line in z.read('revisions.jsonl').splitlines():
                    r = json.loads(line)
                    keep = {k: r.get(k) for k in ('rev_id', 'page_id', 'page_key', 'seq', 'time', 'label', 'body',
                                                  'diff_base', 'diff_base_reason', 'hunks', 'change_summary')}
                    self.revs[r['rev_id']] = keep
                    self.pages.setdefault(r['page_key'], []).append(r['rev_id'])
                for line in z.read('pages.jsonl').splitlines():
                    p = json.loads(line)
                    self.page_meta[p['page_key']] = {k: p.get(k) for k in ('page_id', 'page_family', 'n_revs', 'first_write', 'last_write')}
            for ids in self.pages.values():
                ids.sort(key=lambda i: int(self.revs[i]['seq']))
        if obs_path and obs_path.exists():
            for line in obs_path.read_text().splitlines():
                o = json.loads(line)
                self.obs[o['observation_id']] = o
                self.obs_by_rev.setdefault(o['revision_id'], []).append(o)

    def fresh_lines(self, r):
        if not r.get('diff_base'):
            return set(range(len(r['body'].split('\n'))))
        return {i for h in (r.get('hunks') or []) if h['op'] in ('insert', 'replace') for i in range(h['b0'], h['b1'])}

    def line_of_char(self, rev_id, char):
        body = (self.revs.get(rev_id) or {}).get('body')
        return body.count('\n', 0, char) + 1 if body is not None and char is not None else None

    def reencoded_lines(self, r):
        """Lines a replace hunk rewrote with only non-ASCII bytes changed (mojibake from a client mis-decoding the
        page and saving it back): carried text that the diff shows as fresh."""
        base = self.revs.get(r.get('diff_base') or '')
        if not base:
            return set()
        old, new, out = base['body'].split('\n'), r['body'].split('\n'), set()
        for h in r.get('hunks') or []:
            if h['op'] == 'replace':
                was = {ascii_skeleton(l) for l in old[h['a0']:h['a1']]}
                out |= {i for i in range(h['b0'], h['b1']) if i < len(new) and new[i].strip() and ascii_skeleton(new[i]) in was}
        return out

    def review_flags(self, rev_id):
        """Signals that the edit's signoff is less trustworthy: re-encoded carried lines, and several "-- Name" posts."""
        r = self.revs.get(rev_id)
        if not r or r.get('body') is None:
            return {'reencoded': 0, 'posts': 0}
        body = r['body'].split('\n')
        fresh = [i for i in sorted(self.fresh_lines(r)) if i < len(body)]
        return {'reencoded': len(self.reencoded_lines(r)), 'posts': sum(1 for i in fresh if POST_SIG.search(body[i].rstrip()))}

    def edit_lines(self, rev_id, posts):
        """What the edit wrote (its fresh lines) plus any carried line a post was claimed on, in body order.
        Each line lists the trajectories whose claimed posts cover it."""
        r = self.revs.get(rev_id)
        if not r or r.get('body') is None:
            return []
        body = r['body'].split('\n')
        starts = [0]
        for l in body:
            starts.append(starts[-1] + len(l) + 1)
        by = {}
        for b in posts:
            hit = {i for s0, s1 in b['spans'] for i in range(len(body)) if starts[i] <= s0 < starts[i + 1] or s0 < starts[i] < s1}
            if not hit and b['pos']:
                hit = {b['pos'] - 1}
            for i in hit:
                by.setdefault(i, []).append(b['t'])
        fresh, reenc = self.fresh_lines(r), self.reencoded_lines(r)
        keep = sorted(i for i in fresh | set(by) if i < len(body))
        while keep and not body[keep[0]].strip() and keep[0] not in by:
            keep.pop(0)
        while keep and not body[keep[-1]].strip() and keep[-1] not in by:
            keep.pop()
        return [{'n': i + 1, 'text': body[i], 'fresh': i in fresh, 'reenc': i in reenc, 'by': list(dict.fromkeys(by.get(i, [])))} for i in keep]

    def last_signoff(self, rev_id):
        """The signoff closest to the end of what the edit wrote, and where it came from."""
        obs = [o for o in self.obs_by_rev.get(rev_id, []) if o.get('signature') and o.get('body_line') is not None]
        if obs:
            return max(obs, key=lambda o: o['body_line'])['signature'], 'observation index'
        r = self.revs.get(rev_id)
        if r:
            body = r['body'].split('\n')
            for i in sorted(self.fresh_lines(r), reverse=True):
                m = SIGNOFF_RE.search(body[i]) if i < len(body) else None
                if m:
                    return m.group(1), 'fresh lines'
        return None, None


class Store:
    def __init__(self, config_path):
        self.config_path = config_path
        self.lock = threading.Lock()
        self.stamp, self.checked = None, 0
        cfg = self.cfg = json.loads(config_path.read_text())
        base = config_path.parent
        self.base = base
        self.archive = Archive(self.p(cfg.get('archive')), self.p(cfg.get('observation_index')))
        self.log_path = self.p(cfg.get('log', 'verifications.jsonl'))
        self.rules = self.load_rules()
        self.reload(force=True)

    def p(self, rel):
        return (self.base / rel).resolve() if rel else None

    def matched(self, globs):
        out = []
        for g in ([globs] if isinstance(globs, str) else globs or []):
            out += sorted(glob.glob(str(self.base / g)))
        return [Path(x) for x in out]

    def all_inputs(self):
        files = []
        for s in self.cfg.get('dossiers', []):
            files += self.matched(s['glob'])
        for key in ('audits', 'dispositions'):
            files += self.matched(self.cfg.get(key, []))
        return files

    def load_rules(self):
        rules = {}
        for f in self.matched(self.cfg.get('rules', [])):
            for m in re.finditer(r'^\*\*(R\d+)\s*[—-]\s*(.+?)\*\*\s*(.*)$', f.read_text(), re.M):
                rules.setdefault(m.group(1), f'{m.group(2)} {m.group(3)}'.strip())
        return rules

    # ---- loading -------------------------------------------------------------------------------------------------
    def reload(self, force=False):
        if not force and time.time() - self.checked < 2:
            return
        self.checked = time.time()
        stamp = tuple((str(f), f.stat().st_mtime) for f in self.all_inputs() if f.exists())
        if stamp == self.stamp and not force:
            return
        with self.lock:
            self.build()
            self.stamp = stamp

    def build(self):
        self.dispositions = {}
        for f in self.matched(self.cfg.get('dispositions', [])):
            for c in json.loads(f.read_text()):
                if isinstance(c, dict) and 'candidate_id' in c:
                    self.dispositions[c['candidate_id']] = {k: c.get(k) for k in ('batch', 'disposition', 'rationale', 'canonical_trajectory_ids')}
        self.audits = {}
        for f in self.matched(self.cfg.get('audits', [])):
            try:
                data = json.loads(f.read_text())
            except ValueError:
                continue
            for rec in data if isinstance(data, list) else []:
                if isinstance(rec, dict) and rec.get('candidate_id'):
                    self.audits.setdefault(rec['candidate_id'], []).append({'file': rel(f), 'record': rec})
        self.profiles, self.blocks, self.report = {}, [], []
        for src in self.cfg.get('dossiers', []):
            rep = {'label': src['label'], 'glob': src['glob'], 'files': 0, 'profiles': 0, 'replaced': 0, 'skipped': 0,
                   'errors': [], 'generic_profile_fields': set(), 'generic_block_fields': set()}
            for f in self.matched(src['glob']):
                rep['files'] += 1
                try:
                    data = json.loads(f.read_text())
                    for raw, kind in self.shape(data, src, rep):
                        self.add(raw, kind, src, f, rep)
                except Exception as e:  # keep serving other sources; show the error on the Sources tab
                    rep['errors'].append(f'{f.name}: {type(e).__name__}: {e}')
            rep['generic_profile_fields'] = sorted(rep['generic_profile_fields'])
            rep['generic_block_fields'] = sorted(rep['generic_block_fields'])
            self.report.append(rep)
        self.blocks = [b for p in self.profiles.values() for b in p['blocks']]
        self.claims = {}
        for b in self.blocks:
            if b['rev']:
                self.claims.setdefault(b['rev'], []).append(b)
        groups = {}
        for b in self.blocks:
            groups.setdefault(b['rev'] or 'k:' + b['k'], []).append(b)
        self.edits = [self.edit(posts) for posts in groups.values()]

    def edit(self, posts):
        """One revision with every post any dossier claims in it, ordered by line. Each claiming trajectory
        keeps its own LLM evaluation (a claim), led by the strongest section among its posts there."""
        posts = sorted(posts, key=lambda b: (b['pos'] is None, b['pos'] or 0))
        by_t = {}
        for b in posts:
            by_t.setdefault(b['t'], []).append(b)
        claims = []
        for t, ps in by_t.items():
            lead = min(ps, key=lambda b: SECTION_RANK.get(b['sec'], 9))
            claims.append({'t': t, 'sec': lead['sec'], 'dec': lead['dec'], 'why': lead['why'], 'xp': lead['xp'],
                           'rules': list(dict.fromkeys(r for b in ps for r in b['rules'])),
                           'dep': list(dict.fromkeys(d for b in ps for d in b['dep'])), 'posts': [b['k'] for b in ps]})
        claims.sort(key=lambda c: (SECTION_RANK.get(c['sec'], 9), c['t']))
        first_post, rev = posts[0], posts[0]['rev']
        sig, sig_src = self.archive.last_signoff(rev)
        if not sig:
            signed = [b for b in posts if b['sig']]
            sig, sig_src = (signed[-1]['sig'], 'dossier') if signed else (None, None)
        texts = list(dict.fromkeys(b['txt'] for b in posts if b['txt']))
        lines = self.archive.edit_lines(rev, posts)
        return {
            'k': rev or 'k:' + first_post['k'], 'rev': rev, 'page': first_post['page'], 'utc': first_post['utc'],
            'ed': first_post['ed'], 'sig': sig, 'sig_src': sig_src, 'ndistinct': len(texts),
            'txt': '\n'.join(x['text'] for x in lines) if lines else '\n\n'.join(texts),
            'lines': lines, 'unclaimed': sum(1 for x in lines if x['fresh'] and not x['by'] and x['text'].strip()),
            'flags': self.archive.review_flags(rev),
            'rules': list(dict.fromkeys(r for c in claims for r in c['rules'])),
            'obs': list(dict.fromkeys(b['obs'] or b['rec'] or b['k'] for b in posts)), 'hx': any(b['extra'] for b in posts),
            'posts': [{k: b[k] for k in ('k', 't', 'sec', 'dec', 'sig', 'pos', 'txt', 'why', 'rules', 'xp', 'obs', 'rec')} for b in posts],
            'claims': claims,
        }

    def shape(self, data, src, rep):
        """Yield (dossier-like dict, kind) from any known file shape."""
        items = data if isinstance(data, list) else [data]
        for item in items:
            if not isinstance(item, dict):
                continue
            if any(k in item for k in SECTION_NAMES):
                yield item, 'trajectory'
            elif 'candidate_id' in item and 'observations' in item:
                yield from self.from_assembly(item, src, rep)
            elif isinstance(item.get('trajectories'), list):
                rep['skipped'] += 1  # a catalog of pointers; point a source at the dossier files instead
            else:
                rep['skipped'] += 1

    def from_assembly(self, cand, src, rep):
        cid = cand['candidate_id']
        if src.get('skip_dispositioned') and cid in self.dispositions:
            rep['skipped'] += 1
            return
        obs = cand.get('observations', [])
        trajs = cand.get('trajectories', [])
        if src.get('groups_only') and trajs:
            return
        for t in trajs:
            lid = t['local_id']
            d = dict(t, trajectory_id=lid, signature=cand.get('signature'), candidate_id=cid, disposition=cand.get('disposition'),
                     candidate_rationale=cand.get('rationale'), status=src.get('status', 'proposal'))
            for name, sec in SECTION_NAMES.items():
                d[name] = [o for o in obs if o.get('trajectory_local_id') == lid and DECISION_SECTION.get(o.get('decision')) == sec]
            d['unassigned'] += [o for o in obs if not o.get('trajectory_local_id') and o.get('decision') == 'unresolved']
            yield d, 'trajectory'
        if not trajs:
            d = {'trajectory_id': f'group:{cid}', 'self_name': cand.get('signature'), 'signature': cand.get('signature'),
                 'candidate_id': cid, 'disposition': cand.get('disposition'), 'rationale': cand.get('rationale'),
                 'status': src.get('status', cand.get('disposition') or 'group'), 'follow_up_leads': cand.get('follow_up_leads')}
            for name, sec in SECTION_NAMES.items():
                d[name] = [o for o in obs if DECISION_SECTION.get(o.get('decision')) == sec]
            yield d, 'group'

    def add(self, raw, kind, src, path, rep):
        tid = first(raw, 'trajectory_id', 'local_id', 'id')
        if not tid:
            rep['skipped'] += 1
            return
        prev = self.profiles.get(tid)
        if prev and src.get('only_new'):
            prev['also_in'].append(src['label'])
            return
        rep['profiles'] += 1
        omit = set(self.cfg.get('omit_fields', []))
        rep['generic_profile_fields'] |= {k for k in raw if k not in PROFILE_KNOWN and k not in SECTION_NAMES and k not in omit
                                          and k not in ('candidate_id', 'disposition', 'candidate_rationale')}
        cid = raw.get('candidate_id') or (tid.split('/')[0] if '/' in tid else None)
        aliases = [a for a in (raw.get('signatures') or []) + (raw.get('aliases') or []) + [raw.get('legacy_display_name'), raw.get('signature')] if a]
        name = first(raw, 'self_name', 'display_name', 'name', 'signature') or tid
        prof = {
            'id': tid, 'kind': kind, 'name': name, 'aliases': sorted(set(a for a in aliases if isinstance(a, str) and a != name)),
            'task': first(raw, 'task', 'title', 'task_family'), 'family': first(raw, 'task_family', 'task_id'),
            'status': raw.get('status'), 'batch': raw.get('batch'), 'candidate_id': cid, 'source': src['label'],
            'source_file': rel(path), 'also_in': [],
            'rationale': first(raw, 'membership_rationale', 'rationale'), 'candidate_rationale': raw.get('candidate_rationale'),
            'uncertainties': raw.get('uncertainties') or [], 'leads': first(raw, 'candidate_follow_up_leads', 'follow_up_leads') or [],
            'anchors': raw.get('anchor_observation_ids') or [], 'disposition': raw.get('disposition') or (self.dispositions.get(cid) or {}).get('disposition'),
            'tables': {}, 'extra': {}, 'blocks': [],
        }
        for k, v in raw.items():
            if k in PROFILE_KNOWN or k in SECTION_NAMES or k in omit or k in ('candidate_id', 'disposition', 'candidate_rationale'):
                continue
            if is_block_list(k, v):
                continue
            if isinstance(v, list) and v and all(isinstance(x, dict) for x in v):
                prof['tables'][k] = v
            else:
                prof['extra'][k] = v
        for k, v in raw.items():
            if k in SECTION_NAMES or is_block_list(k, v):
                for m in v or []:
                    prof['blocks'].append(self.block(m, SECTION_NAMES.get(k, k), tid, rep))
        prof['fingerprint'] = sha('\n'.join(sorted(b['k'] for b in prof['blocks'] if b['sec'] == 'owned')))[:16]
        if prev:
            rep['replaced'] += 1
            prof['also_in'] = prev['also_in'] + [prev['source']]
        self.profiles[tid] = prof

    def block(self, m, section, tid, rep):
        rep['generic_block_fields'] |= {k for k in m if k not in BLOCK_KNOWN}
        rev = m.get('revision_id')
        a = self.archive.revs.get(rev) or {}
        o = self.archive.obs.get(m.get('observation_id')) or {}
        spans = [s for s in (m.get('spans') or []) if isinstance(s, dict)]
        texts = [s['text'] for s in spans if s.get('text')] or list(m.get('included_excerpts') or []) \
            or [x for x in (m.get('source_excerpt'), o.get('excerpt')) if x][:1]
        text = '\n…\n'.join(texts)
        hsh = (spans[0].get('text_sha256') if spans else None) or (sha(text) if text else '')
        key = m.get('observation_id') or m.get('record_id') or f'{rev}#{hsh[:16]}'
        return {
            'k': key, 't': tid, 'sec': section, 'dec': m.get('decision'), 'rev': rev,
            'page': m.get('page_id') or a.get('page_id') or o.get('page_id') or page_of_rev(rev),
            'utc': m.get('utc') or a.get('time') or o.get('time'), 'ed': m.get('editor') or a.get('label') or o.get('editor_label'),
            'sig': m.get('signature') or o.get('signature'), 'txt': text, 'why': m.get('reason'), 'rules': m.get('rule_ids') or [],
            'dep': m.get('depends_on') or [], 'xp': m.get('cross_post_of'), 'obs': m.get('observation_id'), 'rec': m.get('record_id'),
            'sha': hsh, 'base': m.get('diff_base') or a.get('diff_base'),
            'pos': first(m, 'body_line', 'source_body_line') or o.get('body_line')
                   or self.archive.line_of_char(rev, spans[0].get('start_char') if spans else None),
            'spans': [[s.get('start_char'), s.get('end_char')] for s in spans if s.get('start_char') is not None],
            'extra': {k: v for k, v in m.items() if k not in BLOCK_KNOWN},
        }

    # ---- views ---------------------------------------------------------------------------------------------------
    def index(self):
        cols = ('id', 'kind', 'name', 'aliases', 'task', 'family', 'status', 'batch', 'candidate_id', 'source', 'also_in', 'fingerprint', 'disposition')
        profiles = [{k: p[k] for k in cols} | {'n': len(p['blocks'])} for p in self.profiles.values()]
        return {'profiles': profiles, 'edits': self.edits, 'rules': self.rules, 'generated': now()}

    def profile(self, tid):
        p = self.profiles.get(tid)
        if not p:
            return None
        out = {k: v for k, v in p.items() if k != 'blocks'}
        out['block_extra'] = {b['k']: b['extra'] for b in p['blocks'] if b['extra']}
        out['audits'] = self.audits.get(p['candidate_id'], []) if p['candidate_id'] else []
        out['candidate'] = self.dispositions.get(p['candidate_id'])
        return out

    def revision(self, rev_id):
        r = self.archive.revs.get(rev_id)
        if not r:
            return None
        fresh, lines, off = self.archive.fresh_lines(r), [], 0
        for i, ln in enumerate(r['body'].split('\n')):
            lines.append({'n': i + 1, 'text': ln, 'fresh': i in fresh, 'start': off, 'end': off + len(ln)})
            off += len(ln) + 1
        claims = [{'k': b['k'], 't': b['t'], 'sec': b['sec'], 'spans': b['spans'], 'txt': b['txt']} for b in self.claims.get(rev_id, [])]
        meta = {k: r[k] for k in ('rev_id', 'page_id', 'page_key', 'seq', 'time', 'label', 'diff_base', 'diff_base_reason', 'change_summary')}
        return meta | {'lines': lines, 'claims': claims}

    def page(self, page_id):
        key = page_id.replace('/', '~', 1)
        out = []
        for rid in self.archive.pages.get(key, []):
            r = self.archive.revs[rid]
            body = r['body'].split('\n')
            fresh = sorted(self.archive.fresh_lines(r))
            out.append({'rev': rid, 'seq': r['seq'], 'utc': r['time'], 'ed': r['label'], 'summary': r.get('change_summary'),
                        'fresh': [{'n': i + 1, 'text': body[i]} for i in fresh if i < len(body) and body[i].strip()][:80],
                        'nfresh': len(fresh), 'claimed': sorted({b['k'] for b in self.claims.get(rid, [])})})
        return {'page': page_id, 'meta': self.archive.page_meta.get(key), 'revisions': out}

    def sources(self):
        return {'config': str(self.config_path), 'archive': str(self.archive.path), 'archive_revisions': len(self.archive.revs),
                'observation_index': len(self.archive.obs), 'log': str(self.log_path), 'rules': len(self.rules),
                'audited_candidates': len(self.audits), 'dispositions': len(self.dispositions), 'sources': self.report,
                'profiles': len(self.profiles), 'posts': len(self.blocks), 'edits': len(self.edits),
                'edits_with_several_posts': sum(1 for e in self.edits if len(e['posts']) > 1),
                'edits_claimed_by_several_trajectories': sum(1 for e in self.edits if len(e['claims']) > 1),
                'edit_signoff_from': dict(Counter(e['sig_src'] or 'none' for e in self.edits))}

    # ---- verification log ----------------------------------------------------------------------------------------
    def events(self):
        if not self.log_path.exists():
            return []
        return [json.loads(line) for line in self.log_path.read_text().splitlines() if line.strip()]

    def append(self, ev):
        target = ev.get('target') or {}
        if not str(ev.get('reviewer') or '').strip():
            raise ValueError('reviewer name is required')
        if target.get('kind') not in TARGET_KINDS:
            raise ValueError(f'target.kind must be one of {sorted(TARGET_KINDS)}')
        if 'verdict' in ev and ev['verdict'] not in VERDICTS:
            raise ValueError(f'verdict must be one of {sorted(VERDICTS)}')
        if not any(k in ev for k in ('verdict', 'assign_to', 'tags', 'note')):
            raise ValueError('event changes nothing')
        if 'tags' in ev and not (isinstance(ev['tags'], list) and all(isinstance(t, str) for t in ev['tags'])):
            raise ValueError('tags must be a list of strings')
        a = ev.get('assign_to')
        if a is not None:
            if not isinstance(a, str) or not (a in self.profiles or (a.startswith('new:') and a[4:].strip())):
                raise ValueError(f'assign_to must be a known trajectory id or new:<label>, not {a!r}')
            if a == target.get('trajectory_id'):
                raise ValueError('that is already this edit\'s trajectory; mark it "from this agent" instead')
        rec = {'schema': SCHEMA, 'event_id': uuid.uuid4().hex, 'at': now(), 'reviewer': ev['reviewer'].strip(), 'target': target}
        for k in ('verdict', 'assign_to', 'tags', 'note', 'context'):
            if k in ev:
                rec[k] = ev[k]
        with self.lock, self.log_path.open('a') as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + '\n')
        return rec


def state_key(t):
    """Edits are keyed by (trajectory, revision); older per-post 'block' events land on their edit."""
    if t['kind'] == 'profile':
        return f"p|{t.get('trajectory_id')}"
    if t['kind'] in ('edit', 'block'):
        return f"e|{t.get('trajectory_id')}|{t.get('revision_id') or t.get('edit_key') or 'k:' + str(t.get('block_key'))}"
    return f"r|{t.get('revision_id')}"


def apply(s, ev):
    if 'verdict' in ev:
        if ev['verdict'] == 'clear':
            for k in ('verdict', 'verdict_by', 'verdict_at', 'context'):
                s.pop(k, None)
        else:
            s.update(verdict=ev['verdict'], verdict_by=ev['reviewer'], verdict_at=ev['at'], context=ev.get('context'))
    if 'assign_to' in ev:
        s['assign_to'] = ev['assign_to']
    if 'tags' in ev:
        s['tags'] = ev['tags']


def derive_from_posts(s):
    """Edit verdict from its posts' calls: confirm > reassign > unsure > reject; tags are their union.
    A reassign to the edit's own trajectory (the only way to say 'associated, but it is theirs') counts as confirm."""
    tid = s['target'].get('trajectory_id')
    norm = lambda p: 'confirm' if p.get('verdict') == 'reassign' and p.get('assign_to') in (None, tid) else p.get('verdict')
    for k in ('verdict', 'verdict_by', 'verdict_at', 'context', 'context_post', 'assign_to', 'verdict_from'):
        s.pop(k, None)
    called = {k: p for k, p in s['posts'].items() if norm(p)}
    if called:
        v = min((norm(p) for p in called.values()), key=lambda x: POST_VERDICT_PRECEDENCE.get(x, 9))
        k, p = max(((k, p) for k, p in called.items() if norm(p) == v), key=lambda kp: kp[1]['verdict_at'])
        s.update(verdict=v, verdict_by=p['verdict_by'], verdict_at=p['verdict_at'], context=p.get('context'),
                 context_post=k, verdict_from='posts')
        if v == 'reassign':
            s['assign_to'] = p['assign_to']
    if any('tags' in p for p in s['posts'].values()):
        s['tags'] = list(dict.fromkeys(t for p in s['posts'].values() for t in p.get('tags', [])))


def fold(events, history=False):
    """Current state per target: fields present in an event overwrite; notes accumulate."""
    state = {}
    for ev in events:
        t = ev['target']
        s = state.get(state_key(t))
        if s is None:
            target = {'kind': 'edit', **{k: t.get(k) for k in ('trajectory_id', 'revision_id', 'page_id')}} if t['kind'] == 'block' else t
            s = state[state_key(t)] = {'target': target, 'notes': [], 'events': 0}
        s['events'] += 1
        s['last_at'], s['last_reviewer'] = ev['at'], ev['reviewer']
        if history:
            s.setdefault('history', []).append(ev)
        if ev.get('note'):
            s['notes'].append({'at': ev['at'], 'reviewer': ev['reviewer'], 'note': ev['note'],
                               **({'post': t.get('block_key')} if t['kind'] == 'block' else {})})
        if t['kind'] == 'block':
            apply(s.setdefault('posts', {}).setdefault(t.get('block_key'), {}), ev)
            derive_from_posts(s)
        else:
            apply(s, ev)
            if 'verdict' in ev:
                s['verdict_from'] = 'edit'
                s.pop('context_post', None)
    return state


def conflicts(state):
    """Revisions that more than one trajectory holds after the reviewer's calls (confirmed, reassigned or tagged in)."""
    holders = {}
    for s in state.values():
        t, v = s['target'], s.get('verdict')
        if t['kind'] == 'edit':
            owner = t.get('trajectory_id') if v == 'confirm' else s.get('assign_to') if v == 'reassign' else None
        elif t['kind'] == 'revision':
            owner = s.get('assign_to')
        else:
            continue
        if owner and t.get('revision_id'):
            holders.setdefault(t['revision_id'], set()).add(owner)
    return {r: sorted(o) for r, o in sorted(holders.items()) if len(o) > 1}


def make_handler(store):
    page = (HERE / 'index.html')

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def send(self, code, body, ctype='application/json'):
            data = body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False).encode()
            zipped = len(data) > 4096 and 'gzip' in self.headers.get('Accept-Encoding', '')
            if zipped:
                data = gzip.compress(data, compresslevel=5)  # the index and state are megabytes of JSON; matters off-host
            self.send_response(code)
            if zipped:
                self.send_header('Content-Encoding', 'gzip')
            self.send_header('Content-Type', ctype + '; charset=utf-8')
            self.send_header('Content-Length', str(len(data)))
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            u = urlparse(self.path)
            q = {k: v[0] for k, v in parse_qs(u.query).items()}
            store.reload()
            routes = {
                '/api/index': lambda: store.index(),
                '/api/profile': lambda: store.profile(q.get('id', '')),
                '/api/revision': lambda: store.revision(q.get('id', '')),
                '/api/page': lambda: store.page(q.get('id', '')),
                '/api/events': lambda: store.events(),
                '/api/state': lambda: (lambda st: {'targets': st, 'conflicts': conflicts(st)})(fold(store.events(), history=True)),
                '/api/sources': lambda: store.sources(),
            }
            if u.path in ('/', '/index.html'):
                return self.send(200, page.read_bytes(), 'text/html')
            if u.path not in routes:
                return self.send(404, {'error': 'not found'})
            out = routes[u.path]()
            self.send(200 if out is not None else 404, out if out is not None else {'error': 'not found'})

        def do_POST(self):
            if urlparse(self.path).path != '/api/events':
                return self.send(404, {'error': 'not found'})
            if self.headers.get('Content-Type', '').split(';')[0] != 'application/json':
                return self.send(415, {'error': 'expected application/json'})
            try:
                ev = json.loads(self.rfile.read(int(self.headers.get('Content-Length', 0))))
                rec = store.append(ev)
                # Answer with the one target's new folded state, so the page need not refetch the whole state.
                st, key = fold(store.events(), history=True), state_key(rec['target'])
                self.send(200, {'event': rec, 'key': key, 'state': st.get(key), 'conflicts': conflicts(st)})
            except (ValueError, TypeError, AttributeError) as e:
                self.send(400, {'error': str(e)})

    return H


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--config', default=str(HERE / 'sources.json'))
    ap.add_argument('--host', default='127.0.0.1')
    ap.add_argument('--port', type=int, default=None, help=f'default: first free port from {DEFAULT_PORT}')
    ap.add_argument('--check', action='store_true')
    ap.add_argument('--export-state', metavar='FILE')
    args = ap.parse_args()
    store = Store(Path(args.config).resolve())
    if args.check:
        print(json.dumps(store.sources(), indent=1, default=sorted))
        return
    if args.export_state:
        st = fold(store.events())
        out = {'schema': SCHEMA, 'generated': now(), 'targets': st, 'conflicting_edits': conflicts(st)}
        Path(args.export_state).write_text(json.dumps(out, indent=1, ensure_ascii=False))
        print(f'wrote {args.export_state}')
        return
    # Without an explicit --port, step past ports other local services already hold.
    ports = [args.port] if args.port else range(DEFAULT_PORT, DEFAULT_PORT + 20)
    for port in ports:
        try:
            srv = ThreadingHTTPServer((args.host, port), make_handler(store))
            break
        except OSError as e:
            if e.errno != errno.EADDRINUSE:
                raise
    else:
        sys.exit(f'port {args.port} is in use; pick another with --port' if args.port
                 else f'ports {DEFAULT_PORT}-{DEFAULT_PORT + 19} are all in use; pick one with --port')
    print(f'{len(store.profiles)} profiles, {len(store.edits)} edits ({len(store.blocks)} posts); http://{args.host}:{port}/  (log: {store.log_path})', flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == '__main__':
    sys.exit(main())
