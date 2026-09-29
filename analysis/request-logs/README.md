# Operator request logs

The log-joined steps of the behavioural analysis (`presignal_reads`, `presignal_timing`, `fingerprints/shared_saves`)
read a pseudonymised table of the DSEWiki operator's request logs. Neither the logs nor the table are in this
repository. The raw logs are public, so this is how to rebuild the table.

## Get the raw logs

The ProWiki operator publishes the `/dse` request logs at <https://www.wikiservice.at/dse/>: `log_2604` … `log_2607`
(requests) and `refer_2604` … `refer_2607` (referrers), April–July 2026. Put the eight files in
`external/betreiberlogs/` and check them:

```sh
cd external/betreiberlogs && sha256sum -c SHA256SUMS
```

`SHA256SUMS` is copied from Lütje's Zenodo record (10.5281/zenodo.22689980, MIT), so a passing check means the files
are byte-identical to that paper's evidence base and to the ones used here. The operator may rotate the directory
later, and a download that fails the check is a different edition.

## Clean them

```sh
python3 analysis/request-logs/clean.py      # ~6 min -> analysis/request-logs/clean/
REQUEST_LOGS=analysis/request-logs/clean python3 verify.py
```

`clean.py` writes `requests_26MM.tsv.gz`, `referrers_26MM.tsv` and `summary.json`. What it does to each raw field:
- **IP:** dropped. `client` and `net24` are truncated HMAC-SHA256 hashes. The key is random, is held in memory for one
  run only, and is never written.
- **HOST:** dropped. Its `KILLED` marker is kept as a flag.
- **USER:** dropped.
- **NAME:** kept. This is the wiki username cookie, which the analysis joins to signoffs and editor labels.
- **Timestamps:** `ts` and `time` (UTC) are derived from `TS`.
- **Request URL:** kept as a sanitised query. The script drops:
  - `text=` page text;
  - cookie and username preferences;
  - the values of cache-buster parameters, keeping only their key names, which feed the save fingerprint.

  It also replaces any IP-shaped or email-shaped string that is left.

`--net16` would also keep the plaintext IPv4 /16. It is off by default and is not used by anything here.

The analysis joins only on `name`. The client hashes differ from run to run, so a rebuilt table joins the same way as
the one used for the committed outputs.

## Why the logs are not redistributed

Even the cleaned table still carries:
- per-device hashes, which link one device's requests within a run;
- the reads and searches of human visitors, some of whom are long-standing regulars of the wiki.

The published outputs contain only aggregates and rows keyed to agent trajectories.
