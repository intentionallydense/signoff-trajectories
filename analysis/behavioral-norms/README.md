# Behavioural norms

Which conventions the agents followed, and whether any spread from agent to agent through the wiki. The method and
results are in the [top-level README](../../README.md#method-behavioural-analysis); each script's docstring has the
details.

```
python3 analysis/behavioral-norms/variant.py analysis/signoff-trajectories analysis/behavioral-norms/results
```

`variant.py` imports the scripts below and points their input and output at the given directories.

| script | output (in `results/`) | needs |
|---|---|---|
| `norms.py` | `norms.json`, `examples.json` | export only |
| `spread.py` | `spread.json`, `spread.txt` | export only |
| `coined.py` | `coined.json`, `coined.txt` | export only |
| | `coined_reads.json`, `coined_reads.txt` (the read route) | exposure table |
| `presignal_reads.py` | `presignal_reads.json` | exposure table |
| `presignal_timing.py` | `presignal_timing.json` | exposure table |
| `../fingerprints/shared_saves.py` | `saves.tsv.gz`, `shared_saves.json`, `shared_saves.txt` | request logs |
| `fingerprint_null.py` | `fingerprint_null.json`, `fingerprint_null.txt` (reads `saves.tsv.gz`) | export only |

The exposure table (`$EXPOSURE`, default `analysis/exposure/out`) is built from the request logs (`$REQUEST_LOGS`) by
`analysis/exposure/build.py`; see `analysis/exposure/README.md` and `analysis/request-logs/README.md`. `run.log` is
every script's stdout from the committed run. Without the logs or the table, the steps that need them are skipped and
their committed outputs are kept.

The scripts' built-in default output location is this directory; always pass `results` as above.
