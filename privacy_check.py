"""Check this public snapshot for known secret/address patterns, without printing values."""
from pathlib import Path
import csv,io,json,re,sqlite3,zipfile,urllib.parse,hashlib

ROOT=Path(__file__).resolve().parent
QUERY=re.compile(r'(?i)[?&](?:amp;)?(?:api[_-]?key|access[_-]?token|subscription[_-]?key|token|password|secret|sig|signature|auth|key|nonce|hash)=([^&\s\"\x27<>\\]+)')
PROVIDER=re.compile(r'\b(?:gh[pousr]_[A-Za-z0-9_]{20,}|github_pat_[A-Za-z0-9_]{20,}|sk-(?:proj-|svcacct-)?[A-Za-z0-9_-]{24,}|AKIA[A-Z0-9]{16}|xox[baprs]-[A-Za-z0-9-]{12,})')
PRIVATE_KEY=re.compile(r'-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----')
IP=re.compile(r'(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?![\d.])')
VIEWER=re.compile(r'ACFrOg[A-Za-z0-9_-]{24,}')
LOCAL={'127.0.0.1','0.0.0.0'}  # loopback/bind defaults in the verification tool's CLI, not addresses of anyone

csv.field_size_limit(1<<30)

def main():
    issues=[];files=0
    def flag(name,kind):issues.append({'file':name,'kind':kind})
    def scan(name,s):
        for _ in range(4):s=urllib.parse.unquote(s)
        for match in QUERY.finditer(s):
            if set(match[1])!={'X'}:flag(name,'unredacted credential query value')
        if PROVIDER.search(s) or PRIVATE_KEY.search(s):flag(name,'credential pattern')
        if VIEWER.search(s):flag(name,'signed viewer identifier')
        if any(m[0] not in LOCAL for m in IP.finditer(s)):flag(name,'full IP address')
    def visit(name,d):
        if isinstance(d,str):scan(name,d)
        elif isinstance(d,list):
            for v in d:visit(name,v)
        elif isinstance(d,dict):
            if d.get('ip16') is not None:flag(name,'IP prefix metadata')
            for k,v in d.items():scan(name,k);visit(name,v)
    def content(name,s):
        if name.endswith('.jsonl'):
            for line in s.splitlines():
                if line:visit(name,json.loads(line))
        elif name.endswith('.json'):visit(name,json.loads(s))
        elif name.endswith('.csv'):visit(name,list(csv.reader(io.StringIO(s))))  # cell by cell: a redacted value ends at its cell
        else:scan(name,s)
    for p in ROOT.rglob('*'):
        if not p.is_file() or '.git' in p.parts or '__pycache__' in p.parts:continue
        files+=1;name=p.relative_to(ROOT).as_posix()
        if p.suffix=='.sqlite':
            con=sqlite3.connect(f'file:{p}?mode=ro',uri=True)
            for (t,) in con.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall():
                visit(name+'!'+t,[[c for c in row if isinstance(c,str)] for row in con.execute(f'SELECT * FROM "{t}"')])
            con.close()
        elif p.suffix=='.zip':
            with zipfile.ZipFile(p) as z:
                for member in z.namelist():content(name+'!'+member,z.read(member).decode())
        else:content(name,p.read_text(errors='replace'))
    if issues:
        print(json.dumps(issues,indent=2));raise SystemExit(1)
    print(f'PASS: {files} files and ZIP contents; no unredacted credential-query, provider-key, private-key, signed-viewer, full-IP or IP-prefix patterns detected.')
    print('Pattern checks complement the recorded human-style review; they do not prove absence of every possible sensitive detail.')

if __name__=='__main__':main()
