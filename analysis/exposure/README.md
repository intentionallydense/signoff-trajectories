# Exposure table

Which trajectory requested which wiki page, when, and which revision of the page it saw. The log-joined parts of the
behavioural analysis (`presignal_reads`, `presignal_timing`, and `coined`'s read route) take agents' reads from here.
The table is built from the cleaned operator request logs, which are not redistributed, so neither is the table. See
[`../request-logs/README.md`](../request-logs/README.md) to get and clean the logs.

```sh
REQUEST_LOGS=analysis/request-logs/clean python3 analysis/exposure/build.py   # ~1 min -> analysis/exposure/out/
python3 analysis/exposure/validate/validate.py > analysis/exposure/validate/validate.txt   # ~10 s -> validate.json
```

`REQUEST_LOGS=<dir> python3 verify.py` does both in a scratch copy and checks `validate.json` and the log-joined
behavioural outputs against the committed files.

## How requests are attributed

The log's `name` column is the wiki's username cookie; an agent sets it with `form_editprefs` before saving. A name's
requests are split into sessions wherever the gap exceeds 30 min. A logged save joins an export revision on page plus
second, and only when that revision's editor label equals the log name. Each joined save of a trajectory T is one of
five evidence kinds:
- `own`: the name is one of T's signoff names;
- `private`: not an owner name, but across the whole export it labels only T's edits;
- `borrowed`: another trajectory's owner name (an agent typed someone else's signoff, or reused its cookie);
- `shared`: it labels edits of two or more trajectories and is nobody's owner name;
- `generic`: a stock name such as `AgentResearcher`, even if only one trajectory's edits carry it.

Only `own` and `private` saves attribute reads; the rest show that a trajectory was in the session, not that the
session's reads were its reads. Session tiers:
- `session`: `own`/`private` saves from exactly one trajectory, and no other candidate;
- `exclusive`: no joined save in the session, but every `own`/`private` save under this name belongs to one
  trajectory; the name is not generic and was never used borrowed or shared; the session lies within that trajectory's
  span ± 24 h;
- `ambiguous`: `own`/`private` saves from two or more trajectories, or plus other candidates; `weak`: borrowed, shared
  or generic evidence only. Both go to `reads_shared.tsv.gz`, attributed to nobody;
- `none`: no trajectory evidence (human visitors, one-off names, unsigned agents).

Each attributed session then gets a **basis**, read by [`table.py`](table.py):

| basis | session |
|---|---|
| `own` | `session` tier, with a save under one of the trajectory's own names |
| `private2` | `session` tier, only `private` saves, 2 or more |
| `private1` | the same with a single save |
| `exclusive` | `exclusive` tier (no save in the session) |

Each read gets the revision it saw (`seen`, `seen_how`): the last export revision at or before the request second, or
the one a `revision=` parameter named, when that existed at the time.

## Validation

There is no ground truth, so [`validate/validate.py`](validate/validate.py) measures agreement with what the agents did
next. Before a trajectory T names a page Q, or quotes an 8-word passage first written on Q by someone else, T's reads
should hit Q more often than other readers' reads do (all readers, and same-family readers), and more often than T's
own reads hit comparable pages. If a basis is wrong, the reads belong to someone else and the excess disappears. Mention
and quote targets together, 24 h window, bootstrap 95% CIs over trajectories (`validate/validate.txt`):

| basis | targets (traj) | T hit | other readers | same family | T on decoy pages |
|---|---:|---:|---:|---:|---:|
| own | 339 (179) | 0.24 | 0.10 | 0.18 | 0.11 |
| private2 | 24 (11) | 0.38 | 0.06 | 0.12 | 0.27 |
| private1 | 103 (70) | 0.23 | 0.16 | 0.20 | 0.18 |
| exclusive | 112 (56) | 0.08 | 0.08 | 0.11 | 0.07 |

- `own` is supported against all three decoys, most clearly on mentions (0.43 against 0.09, 0.18 and 0.08).
- `private2` is small but clearly above other readers and same-family readers; its decoy-page CI crosses zero.
- `private1` is marginal (CIs touch zero) and `exclusive` shows no signal.
- The first edit of an existing page (`join`) shows nothing on any basis: joining a page needs no earlier read of it.

So the analysis makes claims only from `own` + `private2` sessions (`table.VALIDATED`), and repeats the headline counts
over all four bases as a sensitivity.

## Outputs (`out/`, not committed)

Tab-separated, no quoting (read with `quoting=csv.QUOTE_NONE`).

| file | contents |
|---|---|
| `reads.tsv.gz` | one row per attributed request of class `browse`, `diff`, `edit_form` or `meta` (RecentChanges, index, history): `traj_id`, `name`, `tier`, `session_id`, `ts`, `time`, `rel_first_post_s`, `cls`, `action`, `page`, `page_key`, `seen`, `seen_how`, `family` |
| `reads_shared.tsv.gz` | the same for `ambiguous` and `weak` sessions, with `candidates` in place of `traj_id` |
| `searches.tsv.gz` | attributed searches |
| `unnamed_page_hour.tsv.gz` | per page and hour: unnamed, named and attributed page reads |
| `name_sessions.tsv` | every session except the `none` tier |
| `summary.json` | counts |

On this build: 36,732 sessions, 4,218 attributed; 197,554 attributed reads, of which 26,451 are in `exclusive`
sessions; 543 of the 728 trajectories have reads, 494 before their first post, median 109.

**Limits.**
- Named reads are a lower bound. About two-thirds of page reads carry no name, and an agent's browsing before its first
  `form_editprefs` is mostly unnamed. A read in the table is evidence of exposure; a missing read is not evidence of
  anything.
- Only `/dse` is logged. Reads on the operator's other wikis are invisible.
- `name_sessions.tsv` and `reads_shared.tsv.gz` can hold the names of human regulars, which is one reason the table is
  not published. The committed outputs derived from it carry only aggregate counts and rows keyed to agent trajectories.
