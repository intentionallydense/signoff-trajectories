# Verification cross-check

The verdicts of the human verification phase (26–27 September 2026), and a script that checks the published
trajectories against them. The results are summarised under Validation in the [top-level README](../../README.md).

```
python3 analysis/verification-check/xcheck.py    # -> xcheck.json, prints the counts
```

| file | contents |
|---|---|
| `verdicts.jsonl` | one verdict per reviewed edit (537) |
| `xcheck.py` | for each verdict, which trajectories now hold the revision and whether one matches the reviewed dossier |
| `xcheck.json` | counts, and every verdict with the trajectories that hold it |

## `verdicts.jsonl`

Each line is one edit of one dossier as I last left it:

| field | meaning |
|---|---|
| `dossier` | id of the reviewed dossier from the earlier, LLM-assembled set (`P…` ids and `C…/1` ids); its names are in `../signoff-trajectories/verified_profile_names.json` |
| `rev` | the revision, `dse~Page@n` |
| `page` | the page, `dse/Page` |
| `verdict` | `confirm` (485), `reject` (50) or `unsure` (2) |
| `called_on` | `edit` if I called the whole edit, `post` if I called its posts one by one and the edit verdict follows from them |
| `date` | day of the last call |

When the posts of one edit were called separately, the edit takes the strongest call among them, in the order
confirm > reassign > unsure > reject. A reassignment to the edit's own dossier counts as a confirmation. This is the
verification tool's own rule (`server.fold`). No reassignment to another dossier survives in the final state.

It is an export, not the raw log. The raw log is an append-only event stream: 846 events, including calls that were
later changed or cleared. The export keeps only the final state of each edit. It leaves out:
- free-text notes and tags;
- the post-level calls and the hashes of the texts that were shown;
- the reviewer name and the time of day.

Six dossier-level calls were all cleared, so none appears here.
