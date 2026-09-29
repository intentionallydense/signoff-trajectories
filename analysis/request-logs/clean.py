#!/usr/bin/env python3
"""Clean the DSEWiki operator request logs (external/betreiberlogs) into a pseudonymised table.

Drops client IPs, HOST (= IP, plus a KILLED marker we keep as a flag), cookie/cache-buster params and all `text=`
page text. The only trace of a client address left is a keyed hash, and the key exists only in memory for one run.
See README.md.
"""
import argparse, gzip, hashlib, hmac, ipaddress, json, os, re, secrets, sys
import urllib.parse as up
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
SRC = HERE.parent.parent / 'external' / 'betreiberlogs'
MONTHS = ['2604', '2605', '2606', '2607']

REC = re.compile(r'#DONE2\|1\|UT\|([^|]*)\|ST\|([^|]*)\|STAMP\|[^|]*\|IP\|([^|]*)\|HOST\|([^|]*)\|USER\|([^|]*)'
                 r'\|NAME\|([^|]*)\|ACTION\|(.*?)(?:\|TS\|(\d*))?$', re.S)
DROP = {'text', 'p_cookieid', 'p_cookie_id', 'p_expirecookie', 'expirecookie', 'p_username', 'p_password',
        'password', 'p_email', 'email'}
CACHEBUST = {'x', 'cb', 'uniq', '_', 'z', 'nocache', 'cachebust', 'rnd', 'rand'}   # values dropped, keys kept
IPV4 = re.compile(r'(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?![\d.])')
IPV6 = re.compile(r'(?<![0-9A-Fa-f:])(?:[0-9A-Fa-f]{1,4}:){2,7}[0-9A-Fa-f]{0,4}(?![0-9A-Fa-f:])')
EMAIL = re.compile(r'[\w.+-]+@[\w-]+\.[\w.-]+')
COLS = ['ts', 'time', 'ut', 'st', 'killed', 'site', 'script', 'client', 'net24', 'net16', 'name', 'cls', 'action',
        'page', 'oldid', 'diff', 'save', 'text', 'cachebust', 'query']


def scrub(s):
    s = IPV4.sub('<ip>', s)
    s = IPV6.sub(lambda m: '<ip>' if m.group(0).count(':') >= 3 else m.group(0), s)
    return EMAIL.sub('<email>', s)


def records(path, stats):
    """Yield whole records, rejoining the ones a newline inside a leaked text= value split across lines."""
    cur = None
    with open(path, 'rb') as f:
        for raw in f:
            line = raw.decode('utf-8', 'replace').rstrip('\r\n')
            if line.startswith('#DONE2|'):
                if cur is not None:
                    yield cur
                cur = line
            elif cur is not None:
                cur += '\n' + line
                stats['continuation_lines'] += 1
            else:
                stats['orphan_lines'] += 1
    if cur is not None:
        yield cur


def classify(action, q, page, has_text):
    a = action.rstrip("'")
    if a in ('edit', 'form_edit') and has_text:
        return 'save'
    if a in ('edit', 'form_edit'):
        return 'edit_form'
    if a in ('search', 'form_search') or 'search' in q or ('keywords' in q and not page):
        return 'search'
    if a in ('editprefs', 'form_editprefs', 'login'):
        return 'prefs'
    if a == 'delete':
        return 'delete'
    if a in ('rc', 'rss', 'history', 'archive', 'index', 'wordindex', 'showtop', 'top', 'random', 'links', 'info'):
        return 'meta'
    if 'diff' in q or a == 'diff':
        return 'diff'
    if page or a in ('browse', 'raw', 'print', 'source'):
        return 'browse'
    return 'other'


def clean(rec, key, stats, keep_net16):
    m = REC.match(rec)
    if not m:
        stats['unparsed'] += 1
        return None
    ut, st, ip, host, user, name, action, ts = m.groups()
    if user:
        stats['user_field_dropped'] += 1
    killed = host.endswith(' KILLED')
    if host not in (ip, ip + ' KILLED'):
        stats['host_unexpected'] += 1
    try:
        addr = ipaddress.ip_address(ip)
        client = hmac.new(key, addr.packed, hashlib.sha256).hexdigest()[:12]
        n24 = ipaddress.ip_network(f'{ip}/{24 if addr.version == 4 else 48}', strict=False)
        net24 = hmac.new(key, n24.network_address.packed + bytes([n24.prefixlen]), hashlib.sha256).hexdigest()[:10]
        net16 = str(ipaddress.ip_network(f'{ip}/16', strict=False)) if keep_net16 and addr.version == 4 else ''
    except ValueError:
        client = net24 = net16 = ''
        stats['bad_ip'] += 1
    u = up.urlsplit(action.replace('\n', ' '))
    pairs = [(k.removeprefix('amp;'), v) for k, v in up.parse_qsl(u.query, keep_blank_values=True)]  # &amp; in links
    q = {}
    for k, v in pairs:
        q.setdefault(k, v)
    text = ''
    if 'text' in q:
        text = 'redacted' if q['text'] == '(NN)' else 'leaked'
        stats['text_' + text] += 1
    bust = ','.join(sorted(k for k in q if k in CACHEBUST))
    act = q.get('action', '')
    page = q.get('id', '') or q.get('formpage', '')
    if not page and pairs and pairs[0][1] == '' and '=' not in pairs[0][0] and not act:
        page = pairs[0][0]                        # wiki.cgi?PageName= / wiki.cgi?PageName
    kept = [(k, v) for k, v in pairs if k not in DROP and k not in CACHEBUST]
    cls = classify(act, q, page, 'text' in q)
    stats['cls_' + cls] += 1
    t = int(ts) if ts and int(ts) > 0 else None
    if t is None:
        stats['no_ts'] += 1
    return {
        'ts': t or '', 'time': datetime.fromtimestamp(t, timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ') if t else '',
        'ut': ut, 'st': st, 'killed': int(killed), 'site': (u.hostname or '').removeprefix('www.'),
        'script': u.path.rsplit('/', 1)[-1], 'client': client, 'net24': net24, 'net16': net16, 'name': scrub(name),
        'cls': cls, 'action': scrub(act), 'page': scrub(page), 'oldid': scrub(q.get('oldid', '')),
        'diff': q.get('diff', ''), 'save': int('Save' in q or 'save' in q), 'text': text, 'cachebust': bust,
        'query': scrub(up.urlencode(kept)),
    }


def tsv(v):
    return str(v).replace('\\', '\\\\').replace('\t', '\\t').replace('\n', '\\n').replace('\r', '\\r')


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--months', nargs='*', default=MONTHS)
    ap.add_argument('--out', type=Path, default=HERE / 'clean')
    ap.add_argument('--net16', action='store_true', help='also keep the plaintext IPv4 /16 (off by default)')
    args = ap.parse_args()
    args.out.mkdir(exist_ok=True)
    key = secrets.token_bytes(32)                 # never written anywhere: hashes join within this run only
    summary = {'run': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'), 'net16': args.net16, 'months': {}}
    clients, names = set(), set()
    for mo in args.months:
        stats = Counter()
        n = 0
        with gzip.open(args.out / f'requests_{mo}.tsv.gz', 'wt', encoding='utf-8', newline='\n') as w:
            w.write('\t'.join(COLS) + '\n')
            for rec in records(SRC / f'log_{mo}', stats):
                r = clean(rec, key, stats, args.net16)
                if r is None:
                    continue
                n += 1
                clients.add(r['client'])
                if r['name']:
                    names.add(r['name'])
                w.write('\t'.join(tsv(r[c]) for c in COLS) + '\n')
        stats['rows'] = n
        rstats = Counter()
        with open(SRC / f'refer_{mo}', encoding='utf-8', errors='replace') as f, \
                open(args.out / f'referrers_{mo}.tsv', 'w', encoding='utf-8') as w:
            w.write('ts\ttime\tpath\treferrer_host\n')
            for line in f:
                p = line.rstrip('\n').split('|', 2)
                if len(p) != 3 or not p[0].isdigit():
                    rstats['unparsed'] += 1
                    continue
                t = int(p[0])
                host = up.urlsplit(p[2]).hostname or ''
                when = datetime.fromtimestamp(t, timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
                w.write('\t'.join(tsv(x) for x in (t, when, scrub(p[1]), scrub(host))) + '\n')
                rstats['rows'] += 1
        summary['months'][mo] = {'requests': dict(sorted(stats.items())), 'referrers': dict(rstats)}
        print(mo, n, 'rows', dict(stats.most_common(6)), file=sys.stderr)
    summary['distinct_clients'] = len(clients)
    summary['distinct_names'] = len(names)
    (args.out / 'summary.json').write_text(json.dumps(summary, indent=1) + '\n')


if __name__ == '__main__':
    main()
