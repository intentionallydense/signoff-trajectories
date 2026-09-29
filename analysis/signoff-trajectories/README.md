# Signoff trajectories (post-verification build)

The dataset. The method, flags, validation and known issues are in the [top-level README](../../README.md); the rule
set is also in the docstring of `build.py`.

```
python3 analysis/signoff-trajectories/build.py    # a few seconds -> trajectories.jsonl, summary.json, review_queue.tsv
```

| file | contents |
|---|---|
| `build.py` | the rule set (input: `full-wiki-logs.zip`, `analysis/family-inventory/inventory.json`, `reviews.json`, `verified_profile_names.json`) |
| `reviews.json` | the four hand calls; the `note` field defines every review type the build supports, most unused here |
| `verified_profile_names.json` | frozen name → dossier-id table from the human verification phase; fills `matches_existing_profile` only |
| `trajectories.jsonl` | one trajectory per line, with its edit timeline |
| `summary.json` | counts, merges, per-family totals, signed/unsigned edits per family |
| `review_queue.tsv` | flagged trajectories and flagged in-scope edits |

`PROPOSALS` points at a file that does not exist on purpose: LLM-proposed attributions are never placed in this build.
