# Behavioural norms

Which conventions the agents followed, and whether any spread from agent to agent through the wiki. The method and
results are in the [top-level README](../../README.md#method-behavioural-analysis); each script's docstring has the
details.

```
python3 analysis/behavioral-norms/variant.py analysis/signoff-trajectories analysis/behavioral-norms/results
```

`variant.py` imports the scripts below and points their input and output at the given directories.

| script | output (in `results/`) | needs request logs |
|---|---|---|
| `norms.py` | `norms.json`, `examples.json` | no |
| `spread.py` | `spread.json`, `spread.txt` | no |
| `coined.py` | `coined.json`, `coined.txt` | no |
| `presignal_reads.py` | `presignal_reads.json` | yes |
| `presignal_timing.py` | `presignal_timing.json` | yes |
| `../fingerprints/shared_saves.py` | `saves.tsv.gz`, `shared_saves.json`, `shared_saves.txt` | yes |
| `fingerprint_null.py` | `fingerprint_null.json`, `fingerprint_null.txt` (reads `saves.tsv.gz`) | no |

`run.log` is every script's stdout from the committed run. Without request logs (`$REQUEST_LOGS`, see
`analysis/request-logs/README.md`), the log-joined steps are skipped and their committed outputs are kept.

The scripts' built-in default output location is this directory; always pass `results` as above.
