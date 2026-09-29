"""Shared reader for the exposure table (out/reads.tsv.gz, out/name_sessions.tsv), for scripts that use an agent's reads.

Each attributed session gets the validation basis of validate/README.md:
  own        session tier, with a save under one of the trajectory's own names
  private2   session tier, only saves under a name no other trajectory uses, 2 or more of them
  private1   the same with a single save
  exclusive  no save in the session (unvalidated: no measurable signal in validate/)
VALIDATED = own + private2, the bases the validation supports for claims.

    import table
    for r in table.reads(EXPO, bases=table.VALIDATED, cls={'browse', 'diff'}): ...
"""
import csv, gzip
from datetime import datetime

BASES = ('own', 'private2', 'private1', 'exclusive')
VALIDATED = ('own', 'private2')
csv.field_size_limit(10 ** 9)


def basis_of(s):
    """Validation basis of one name_sessions.tsv row, or None for sessions that attribute no reads."""
    if s['tier'] == 'exclusive':
        return 'exclusive'
    if s['tier'] != 'session':
        return None
    k = dict(x.split(':') for x in s['evidence_kinds'].split(';') if x)
    return 'own' if 'own' in k else 'private2' if int(k.get('private', 0)) >= 2 else 'private1'


def sessions(expo):
    """session_id -> name_sessions.tsv row plus 'basis', for attributed sessions."""
    out = {}
    with (expo / 'name_sessions.tsv').open() as f:
        for s in csv.DictReader(f, delimiter='\t', quoting=csv.QUOTE_NONE):
            b = basis_of(s)
            if b:
                s['basis'] = b
                out[s['session_id']] = s
    return out


def reads(expo, bases=VALIDATED, cls=None):
    """Rows of reads.tsv.gz whose session basis is in `bases` (and whose cls is in `cls`, if given), each with 'basis'
    and an integer 'ts'."""
    ses = sessions(expo)
    with gzip.open(expo / 'reads.tsv.gz', 'rt', newline='') as f:
        for r in csv.DictReader(f, delimiter='\t', quoting=csv.QUOTE_NONE):
            if cls is not None and r['cls'] not in cls:
                continue
            b = ses[r['session_id']]['basis']
            if b in bases:
                r['basis'], r['ts'] = b, int(r['ts'])
                yield r


def searches(expo, bases=VALIDATED):
    """Rows of searches.tsv.gz in sessions of the given bases, each with 'basis' and an integer 'ts'."""
    ses = sessions(expo)
    with gzip.open(expo / 'searches.tsv.gz', 'rt', newline='') as f:
        for r in csv.DictReader(f, delimiter='\t', quoting=csv.QUOTE_NONE):
            s = ses.get(r['session_id'])
            if s and s['basis'] in bases:
                r['basis'], r['ts'] = s['basis'], int(r['ts'])
                yield r


T = lambda s: datetime.fromisoformat(s.replace('Z', '+00:00')).timestamp()
