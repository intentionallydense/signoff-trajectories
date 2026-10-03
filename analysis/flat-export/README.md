# Flat export

A flat, tabular form of the dataset. `trajectories.jsonl` lists only the edits that are in a trajectory. This export has
a row for **every revision in the wiki export**, assigned or not, and a row for **every profile**. Profiles include
the one-off signoffs: names whose only in-scope edit falls below the two-edit floor and so has no trajectory.

```
python3 analysis/flat-export/export.py    # a few seconds -> edits.csv, profiles.csv, checks.json, signoff_trajectories.sqlite
```

| file | contents |
|---|---|
| `edits.csv` | one row per revision (14,591) |
| `profiles.csv` | one row per profile: 728 trajectories and 358 one-offs; list fields joined with `;`, families as `family:n` |
| `checks.json` | each count recomputed from the rows and compared with `summary.json` and `trajectories.jsonl` |
| `signoff_trajectories.sqlite` | both tables, indexed on `edits.rev`, `edits.profile_id` and `profiles.id`; not committed, written by `export.py` |

The export changes nothing in the trajectories build. `export.py` imports `build.py` as a module without running it,
and calls the build's own `read_edit` on every revision. The signoff reading, the flags and the line text of edits
outside trajectories are therefore exactly what the build read, before any merge, split or two-edit floor. Rows for
trajectory edits take their fields from `trajectories.jsonl`. The script stops if any check in `checks.json` fails,
and `verify.py` rebuilds all three committed files and compares them byte for byte.

## Coverage

| | revisions | in a trajectory | one-off | split part dropped | unsigned |
|---|---:|---:|---:|---:|---:|
| task-family pages | 4,195 | 2,937 | 321 | 1 | 936 |
| relay pages | 5,441 | 421 | 37 | 0 | 4,983 |
| **in scope** | **9,636** | **3,358** | **358** | **1** | **5,919** |

3,716 of the 9,636 in-scope edits (38.6%) belong to a profile: 3,358 to trajectories and 358 to one-offs. These
counts take the export's page labels as given. Three hub pages (`WillkommenImWiki`, `StartSeite`, `TestSeite`) carry
3,021 in-scope edits, almost all unsigned. Without them, 3,714 of 6,615 (56.1%) belong to a profile; see the
[top-level README](../../README.md#disclaimer-three-hub-pages). Off scope
(4,955 revisions on other pages), 44 edits sit in trajectories and 8 in one-off profiles.

## profiles

- `id`: `S:Name` (or `S:Name#k` for a split part) for a trajectory, `O:Name` for a one-off.
- `kind`: `trajectory` or `one_off`.
- The other fields are those of `trajectories.jsonl` without `edits`, plus `split_part`, `review_decision` and
  `review_reason` (the hand calls), `n_copied` and `n_with_url` (in-scope edits).
- For one-offs, `build.py`'s own definitions fill the trajectory fields: `primary_family` is the edit's family (empty
  on a relay page), `span_hours` is 0, and `flags` can be `generic` or `alias`. `alias_candidates` lists the names
  that match once a date or case is dropped (`build.aliases_of`), and `matches_existing_profile` uses the same table as
  the trajectories.
- A one-off is not the same kind of claim as a trajectory. It says only that one in-scope post carried this signoff.
  It may be one post of an agent whose other posts are unsigned or signed differently; see `alias_candidates`. 22
  one-offs carry a name from the human verification phase (`matches_existing_profile`).

## edits

- `scope`: `family` (a page in one of the 41 families), `relay` (`relay-coordination`) or `other`.
- `status`:
  - `assigned`: in a trajectory.
  - `one_off`: in a one-off profile. The name's edits on other pages join it too, as they would a trajectory.
  - `split_dropped`: the part of a hand split that fell below two edits.
  - `unsigned`: in scope, with no signoff read.
  - `out_of_scope`: on another page and in no profile.
- `profile_id`, `profile_name`: the profile the edit belongs to.
- `signoff_read` is the build's reading of this revision. `profile_name` can differ from it after a merge (`merge` is
  `containment` or `alias`, and the original name is in `signed_as`) or a split (`split_part`).
- `copied` (a build flag) means every line signed with the name was already on the page. `copied_from` is the earliest
  revision of the page that has the signed line (the build's `Origins.first_seen`).
- `multipost`, `reencoded`, `rule_signoff`, `signoff_rule` and `other_signers` are as in `trajectories.jsonl`.
- `line`, `text`: the signed line, or for an unsigned edit its first non-blank fresh line.
- `n_fresh_lines` (non-blank), `n_urls`, `url_lines`, `has_url`: what the edit wrote. URLs are matched by
  `https?://`, a bare `//host.tld/` or `www.`.
