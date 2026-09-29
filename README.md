# Signoff trajectories

728 per-agent trajectories reconstructed from the public export of the DSEWiki agent swarm (June 2026), and an analysis
of the conventions those agents followed and whether any of them spread from agent to agent through the wiki.

A trajectory is one agent run's sequence of wiki edits. It is assembled from the agents' own signoffs (`-- Name`) by a
fixed, deterministic rule set, plus four hand calls. No per-edit LLM judgement enters the assignment: every edit is
placed by a stated rule or by one of the four calls. Everything here can be rebuilt from the files in this repository,
and the rebuild reproduces the committed outputs exactly (see [Reproduce](#reproduce)).

- **Data:** [`analysis/signoff-trajectories/trajectories.jsonl`](analysis/signoff-trajectories/trajectories.jsonl),
  with [`summary.json`](analysis/signoff-trajectories/summary.json) and
  [`review_queue.tsv`](analysis/signoff-trajectories/review_queue.tsv).
- **Behavioural analysis:** [`analysis/behavioral-norms/results/`](analysis/behavioral-norms/results/).
- **Method:** this README. The rule set is also documented in the docstring of
  [`build.py`](analysis/signoff-trajectories/build.py).

## At a glance

| | |
|---|---:|
| Trajectories | 728 |
| Edits in trajectories (in scope) | 3,358 (2,937 on task-family pages, 421 on relay pages) |
| Task families with at least one trajectory | 34 of 41 |
| Signoff names in the archive | 1,107 |
| Names dropped for having a single in-scope edit | 358 |
| Trajectories with no flag | 458 |
| Hand calls applied | 4 |
| Edits per trajectory | median 4, maximum 23 |

All in-scope edits fall between 16 and 22 June 2026, most of them on 16–17 and 20–21 June.

## Source data

- **Wiki export.** [`full-wiki-logs.zip`](full-wiki-logs.zip) is the privacy-sanitized derivative of collusion.wiki's
  public export (<https://collusion.wiki/explorer/download/full-wiki-logs.zip>, `explorer-schema-2`, generated
  2026-09-03). It is the same file published in
  [fast-follow-question-trajectories](https://github.com/intentionallydense/fast-follow-question-trajectories)
  (SHA-256 `7531b0bf…cbb25ae`). Relative to upstream, it removes `ip16` metadata, two full IP addresses,
  credential-like URL query values and local usernames in paths. All replacements preserve length, so revision ids
  and character offsets are unchanged. Upstream had already anonymised human usernames.
- **Task families.** [`analysis/family-inventory/inventory.json`](analysis/family-inventory/inventory.json) lists the
  41 task families. These are the export's own `page_family` labels, copied from the repository above.
- **Operator request logs** (used only by the log-joined parts of the behavioural analysis). These are the wiki
  operator's public request logs for April–July 2026, from <https://www.wikiservice.at/dse/>. They are the same bytes
  as the evidence base of Lütje (2026); [`external/betreiberlogs/SHA256SUMS`](external/betreiberlogs/SHA256SUMS)
  pins them. **They are not redistributed here.** See [`analysis/request-logs/README.md`](analysis/request-logs/README.md)
  for fetching and cleaning them.

## Method: trajectories

### The attribution standard

The aim is to recover as many of each agent's posts as possible without mixing two agents.

- **A signoff is a strong prior of one agent.** Agents in this swarm signed their posts with a self-chosen name, which
  is usually dated (`OECDEquityJun06Agent`, `TransportHelperAug27OAI`). Two posts under the same signoff are taken to
  come from the same run unless there is solid contrary evidence:
  - a task-clock or schedule clash;
  - a gap of more than about a day (99% of multi-post signatures span less than 24 h);
  - the post being swept up in someone else's save rather than written fresh.
- **Any second post counts.** A second post does not need to add new information. Restatements, cross-posts,
  retries, test saves and page stubs all count. A trajectory needs at least **two in-scope edits**.
- **Editor labels are not identities.** The wiki records an editor label for each save. Labels rotate between agents,
  and on these edits a label equals the signoff only 69.9% of the time. So no rule here attributes or merges on a label
  match alone. Labels appear only as a guard in two narrow merge rules (below).

### What an edit wrote

Each revision in the export has a diff against its base. An edit's **fresh lines** are the lines that its insert and
replace hunks produce. Carried text is never read as the author's own. That covers lines already on the page and
lines that a mis-decoding client re-saved as mojibake (flagged `reencoded`).

### Assignment

1. **Signoff.** Each revision goes to the **last signoff in its fresh lines**, which is the last line ending in
   `-- Name`. Names must start with a capital letter (the only lowercase match in the archive is a `--help` flag). A
   trailing `?` or a bracketed tag is allowed.
2. **Other signoff forms.** Below that line, or across all fresh lines if there is none, five further forms are read.
   The first that matches wins, and it beats the `-- Name` line above it. For example, a carried `-- LFRelayNov14`
   above the author's own `-- [[OpenAIResearchMay07]]` loses (the position rule).
   - R1 `-- [[Name]]`.
   - R2 `-- Name: …` at or near the start of a line.
   - R3 `-- Name` followed by junk (`\n`, `$(date +%s)`, a few words).
   - R4 a `Name:` prefix in the first two lines, marked LIVE/UPDATE, followed by self-report words (our, new,
     confirmed, arrived, …), or equal to the editor label or its tail.
   - R5 a dated phrase (`-- Sep21 watcher`).

   Vocative prefixes (`Name: are you …`, `please`, `?`), bare dates and dateless team phrases stay unsigned. I read
   all 68 `Name:` readings (R2, R4) by hand; each is the poster's own status, not an address to someone else. R4 and R5
   edits are flagged `rule_signoff`. The forms read 115 in-scope edits under 82 names.
3. **Unsigned edits are left out.** 936 unsigned edits on task-family pages and 4,983 on relay pages belong to no
   trajectory.

### Scope

An edit is **in scope** if it is on a page that the export assigns to one of the 41 families, or on a
`relay-coordination` page. A trajectory's relay edits get its majority family as `family_inferred`. Edits elsewhere are
kept in the timeline with `scope: other` (44 edits) but do not count toward the two-edit floor.

### Merges between names

Two mechanical rules merge a **one-off name** (one in-scope edit) into a trajectory. Both need exactly one qualifying
target. The moved edit keeps its original signoff in `signed_as`.

- **Alias merge.** The one-off's edit joins its alias's trajectory. The trajectory must have posted on the same page,
  under the same editor label, within 10 minutes. It would fire once, `OAI7C97` → `OAI7C97Dec15`, but a hand call
  blocks that merge (below), so it fires zero times.
- **Containment merge.** The names contain one another as a run of camel-case tokens (`Nov16` → `OpenAINov16CVD`,
  `ChatGPTJul19` → `ChatGPTJul19Agent`). The rule also requires all of the following:
  - a shared date tag and compatible families;
  - an in-scope edit of the trajectory within 24 h;
  - no clash on any round that both report. The check compares the task-clock claims in each post
    (`analysis/clock-consistency/`).

  A generic one-off such as `Nov16` also needs an editor label that the trajectory uses. This rule takes 10
  one-offs.

### Hand calls

[`reviews.json`](analysis/signoff-trajectories/reviews.json) holds four calls. I made the first three by hand on
27 September 2026, before any LLM reviewer was involved. An LLM reviewer found the fourth, which I confirmed the same
day. I re-checked it against the task clock myself on 29 September, and it rests on that clock check alone:

| name | call | reason (abridged) |
|---|---|---|
| `OpenAIResearchOct14X` | split | two bursts 60 h apart in different families |
| `OpenAIResearchApr07X` | split | three clothing posts, then one construction post 41 h later; the second part falls below two edits and drops out |
| `OECDEquityMar31Team` | keep whole | 25 h of continuous OECD-equity activity; a label switch after a 9 h gap does not outweigh the signoff |
| `OAI7C97` | not an alias of `OAI7C97Dec15` | the one post signed `-- OAI7C97` sits on the Nov20 cohort's task clock (01:45, 01:46:07, 01:53:20, 01:57 across four posts in 80 minutes), not Dec15's (R4 due 11:18:47, posted 8 s earlier). Two runs shared the editor label, which is what triggered the alias merge |

The build checks that every call still matches the data, and stops if not.

### Where the build stops, and why

Before this rebuild I worked through a **human verification phase** (26–27 September 2026). An earlier, LLM-assembled
set of per-agent dossiers was reviewed edit by edit, and each claim that "this edit comes from this agent" was confirmed
or rejected by hand. The signoff rules above were developed after that phase, as a transparent replacement for the LLM
assembly. I drafted the rules and the scripts, and this README, with an LLM coding assistant (Claude Code). No LLM
places any individual edit: that is done by the rules and the hand calls.

After the verification phase, the dataset kept growing through layers I no longer consider comparable:

1. calls that LLM reviewers found, which I confirmed one by one;
2. LLM-proposed excludes that I approved in bulk;
3. LLM-proposed attributions of unsigned posts, some approved and some still pending.

This release **stops just after the human verification phase**. It keeps the mechanical rules and my three hand calls
from that phase, and nothing from the later layers. The one exception is the `OAI7C97` call from layer 1, which I
re-checked against the task clock myself. The verification verdicts themselves are not applied as reviews either. They
serve as a check (below), not as input. The code is a frozen copy of the full build with the proposals file pointed at
nothing.

The trade-off is deliberate. This build is smaller and misses edits that the later layers recovered, but every
assignment in it follows from a stated rule or a named hand call.

### Flags

Flags mark doubt; they do not remove anything. [`review_queue.tsv`](analysis/signoff-trajectories/review_queue.tsv)
lists every flagged trajectory (except `keep`-reviewed ones) and every flagged in-scope edit.

| level | flag | count | meaning |
|---|---|---:|---|
| edit | `multipost` | 286 | two or more signed lines in the fresh text, so the last signoff is a weaker guide |
| edit | `reencoded` | 108 | a mojibake re-save of carried lines, so the signoff may be someone else's older text |
| edit | `copied` | 83 | every line signed with the name was already on the page: a re-save, a retry, or someone else's save that carried the post along |
| edit | `rule_signoff` | 39 | the name comes from R4 or R5, not a signoff proper |
| edit | `containment_merge` | 10 | moved in by the containment merge |
| edit | `alias_merge` | 0 | moved in by the alias merge |
| trajectory | `several_families` | 20 | family-page edits span more than one family |
| trajectory | `generic` | 12 | a bare date (`Sep05`), stock name (`ResearchHelper`) or phrase, so only a weak prior of one agent |
| trajectory | `alias` | 4 | matches another name once a date or case is dropped (`AgentOpenResearch` / `AgentOpenResearchApr10`) |
| trajectory | `multi_day` | 1 | in-scope edits span more than 24 h |

### Output format

`trajectories.jsonl` has one JSON object per trajectory:
- id (`S:Name`, or `S:Name#k` for split parts), name, primary family, per-family counts;
- first and last edit time, span in hours, flags and alias candidates;
- `edits`, the timeline. Each edit has its revision id, UTC time, page, family, editor label, the signed line and its
  text, other signers in the same edit, flags, and `scope`. It also records `signoff_rule`, `signed_as`, `review` or
  `family_inferred` where these apply.

`matches_existing_profile` lists the dossiers from the human verification phase that carry the same name or alias.
342 trajectories have at least one. The table behind it is
[`verified_profile_names.json`](analysis/signoff-trajectories/verified_profile_names.json), with 376 names and 369
dossier ids.
- 322 of the ids are dossiers published in
  [fast-follow-question-trajectories](https://github.com/intentionallydense/fast-follow-question-trajectories): the 298
  supported and the 24 provisional.
- The other 47 were never published.

The field is informational only; no rule reads it.

## Validation

- **Reproducibility.** The build is deterministic. [`verify.py`](verify.py) rebuilds everything in a scratch copy and
  compares it with the committed files.
- **Against the human verification phase.** The verdicts were given on the earlier dossiers, and I check them at edit
  level here ([`analysis/verification-check/`](analysis/verification-check/)):
  - 481 of 485 confirmations fall in the matching trajectory.
  - 2 of 50 rejections are still in the rejected trajectory, and both carry an edit flag.
  - The 4 missed confirmations are:
    - a lost save, which credits whoever signed last;
    - two `OpenAIFPResearchSep05` edits that sit under the generic name `Sep05`;
    - `IHMEFamilyPlanningR4Signal@1`.
  - Of the other 48 rejections, 45 now sit in a different trajectory and 3 in none.
  - The 2 edits marked unsure are both in the matching trajectory.

  About 95% of those verdicts were on dossiers assembled by an LLM in early September. The agreement is therefore
  measured against LLM-chosen claims that I confirmed. It is not a random, whole-dataset human sample. The verdicts
  are published as a verdict-only export (`verdicts.jsonl`: dossier, revision, verdict, day), without my notes or
  tags.
- **A random validity sample, on an earlier build.** 40 trajectories were drawn at random from 282 of the 750 in an
  earlier, larger build that also included the later layers. The 282 excluded anything an earlier check had already covered: hand reviews,
  verification verdicts, attribution proposals and clock reviews. Each sampled trajectory was checked against the
  standard above by LLM subagents, not by me.
  - Result: 36 valid, 4 with minor issues, 0 invalid.
  - 1 of 130 edits was wrong: an identical re-save counted twice.
  - 5 of the agents' own posts were missed.

  The failure mode of signoff attribution is **recall, not precision**.

## Known issues

These are left in the data on purpose, so that the dataset stays a pure product of the rules and the four calls.

- **Re-saves.** 72 trajectory edits wrote nothing new to the page.
  - 41 of them carry the trajectory's own editor label, which is consistent with a retry.
  - 31 carry a label that the trajectory never uses on a fresh post: another trajectory's label (20) or no
    trajectory's label (11). By the standard above these are probably other agents' saves that carried the post
    along.
  - They are concentrated in `TransportHelperMar28OAI` (12), `OpenAIJanSixWatcher` (5) and
    `OAIHouseholdNov02Scout` (4).
  - They stay in because the only mechanical way to drop them is a label rule. In the `Mar28` cluster, one agent
    re-posted the same line in 5–13 s bursts under rotating labels, so such a rule would cut real posts.
  - The text analyses skip edits with no novel text, so this affects the edit counts, not the behavioural results.
- **The two remaining rejections:** `FinanceSequenceMar26OAI@31` in `ResearchHelperAug12X`, and
  `DataUSALanguageLiveRound4@19` in `LanguageWatcherNov12`.
- **Signoff changes split agents.** An agent that changes signoff partway through appears as two trajectories unless
  a merge rule applies. For example, `OpenAIFeb28Watcher`, `OpenAIFeb28A3` and `Feb28A3` stay separate here.
- **Recall.** Unsigned posts are invisible. 358 single-edit names are dropped. For example, the `OAI7C97` Nov20 run
  above has one signed post and three unsigned ones, so it has no trajectory. 7 small families have no trajectory:
  aihw-pbs, dataafrica-health-stunting, datausa-elpaso-foreign-born, gapminder-age80, ihme-mcv2, unaids-bosnia-hiv and
  world-poverty-clock.
- **Text encoding.** Archive text keeps its Latin-1 representation and the sanitizer's length-preserving redactions.
  Some quotations therefore look garbled. Do not re-encode them before matching against the archive.
- **Reach.** A trajectory is a distinguishable, self-reported run. It does not authenticate a backend process or
  verify any claim an agent made.

## Method: behavioural analysis

The question is which conventions these agents followed, and whether any of them passed from agent to agent through
the wiki beyond what shared tasks and time trends explain. Authorship comes from the trajectories above. All scripts
are in [`analysis/behavioral-norms/`](analysis/behavioral-norms/), and their outputs are in `results/`.

### Text definition

Only text **new to the page** is read (`novel_texts` in `norms.py`). These are fresh lines whose ASCII skeleton never
appeared earlier on the page. Without this filter, mojibake re-saves and whole-page rewrites pass for adoption. In an
early pass, one "correction" line re-saved by five agents in a row looked like a spreading norm. `-- Name` signoffs are
removed before matching. 3,286 edits are read, and the 72 that wrote nothing new are skipped.

### Norms (`norms.py`)

These are descriptive counts from regexes (the `FEATURES` table in `norms.py`), per edit and per trajectory.
`examples.json` has up to four sample posts per feature for spot-checking. They show a common status-post dialect, for
example:
- an hh:mm:ss timestamp: 98.6% of trajectories;
- a deadline, cooldown or window: 98.2%;
- "please": 90.9%;
- a round marker: 77.9%;
- a full status template of round, time and due time: 72.3%.

Names are highly regular:
- dated: 94.8%;
- role noun (Agent, Research, Scout, Helper, …): 77.5%;
- OpenAI/OAI marker: 57.3%;
- trailing X: 14.8%.

### Spread tests (`spread.py`, `coined.py`)

I ranked the leads **before** testing, by how likely each was to give a clean signal. Three designs:

- **Presence leads** ask whether an agent starts doing X after seeing it. The at-risk edits are each trajectory's
  edits up to and including its first use of X. Exposure means another author's post with X appeared on the same page
  within a window W (1, 3 or 12 h) before the edit.
  - The Mantel–Haenszel odds ratio is stratified by family × 6-hour bin, so family-wide prompts and time trends
    cancel out.
  - The **placebo** is the same count for posts in the window *after* the edit, made by authors who were already
    using X before the edit, so the edit cannot have influenced them. It measures page-topic clustering.
  - Spread toward the agent shows up as a past OR clearly above the placebo. A page-stratified version is reported
    too. It is biased low, because a page's opening posts are unexposed by construction. The family design is biased
    high by page clustering.
- **Variant leads** ask which of several interchangeable words an agent picks. The candidates are clock vocabulary,
  self-reference noun, and name parts (role noun, date position, org marker, trailing X).
  - The agent's first variant is matched against the variants that others used on the same page before it (its first
    page, for names).
  - The null shuffles variants among agents in the same family × 6 h stratum 2,000 times.
  - Arbitrary word choices are not dictated by the task, so conformity beyond the stratum baseline is the cleanest
    evidence of copying. Switches between variants are also checked for whether the new variant was visible first.
- **Coined tokens.** An arbitrary string that no task prompt supplies (`PRE-SIGNAL`, `STATE5-XX`) must have been
  picked up from someone. For each one the output gives:
  - the originator, the adopters and the families reached;
  - whether each adopter was exposed on the same page or on any page it had edited;
  - whether that exposure included an explicit request to use the token.

### Log-joined checks (`presignal_reads.py`, `presignal_timing.py`)

These join trajectories to the operator request logs **by acting name**. A trajectory's names are its signoff plus
the editor labels of its edits, minus any label another trajectory also uses. A named read counts as exposure when the
page revision current at that moment showed the word. Most reads carry no name, so named reads are a lower bound.
`presignal_timing` re-times edited-page exposure against reads at least 5 s (and 30 s) before the save. Agents write a
post before loading the edit form and save about 1 s later, so the page under the save is not what they wrote from.

### Calibration (`fingerprint_null.py`)

Could the variant tests be fooled by *who* works on a page, rather than by copying? Each trajectory's dominant **save
fingerprint** is the cache-buster keys and parameter order of its save requests, taken from the logs.
- It is stable within an agent and invisible to other agents.
- It identifies a save's author with 0.881 accuracy on 666 test saves, against 0.429 chance.
- Running it through the same tests in place of the word measures what the tests report when there is no copying.

The joined save table ships as `results/saves.tsv.gz`, so this step reruns without the logs.

### Results

**Every conclusion rests on the 3 h family-stratified design, unless noted.**

| lead | result |
|---|---|
| `PRE-SIGNAL` (coined) | **Clear spread.** It was coined 06-16 21:58 by `TransportHelperAug27OAI`, with a rationale ("R3 may … terminate the episode"). By 06-21 it had 105 users in 15 families. 37 were exposed on the same page and 53 on some page they had edited |
| Task signal tokens (`STATE5-`, `C<n>-STATE`, `G<n>-`, counter keys) | Spread widely within their family (54, 32, 12 and 19 adopters) but **never outside it**. Most adopters saw an explicit request first (STATE5: 50 of 54) |
| Signal before final answer (presence) | Past 2.52 [1.75, 3.62] vs placebo 1.87 [1.08, 3.22]: overlapping; page strata 0.86. **Page clustering, no directional signal** |
| Clock vocabulary, wall side (variant) | Strong local conformity (nearest post 135 vs null 107, p 0.0005), but just as strong looking forward (140 vs 111). Against the fingerprint calibration it is real in both directions (past ×1.12 vs 0.99, future ×1.19 vs 0.97). **Pages converge on a word; the direction is unresolved** |
| Clock vocabulary, task side | Leans backward on the nearest post (past p 0.033, future p 0.14), but not against the calibration (p 0.71) |
| Self-reference noun (switches) | Of 120 switches, the new variant was visible beforehand in 47, against 39 where anyone used it afterwards. Leans directional, weak |
| Name parts | **Null.** No excess over the stratum baseline (e.g. role noun, nearest name 172 of 429 vs null 182.5), and none reaches the calibration floor |
| Status template, "please", date-as-address, "cohort", thanks, corrections (presence) | **Null.** Past ≤ placebo, or indistinguishable from it (e.g. template 2.74 vs 2.77, please 1.05 vs 1.31) |
| Save fingerprint homophily | None. The real fingerprint matches page neighbours no more than a shuffled one (×0.58–0.77 of null) |

**PRE-SIGNAL against the logs:**
- 39 of 104 adopters had a named read of a page showing the word before first use.
  - The median gap from that read to first use was 94 min, and the shortest 1.2 min.
  - For 14 of them, the first such page belonged to another family. `OECDEquityLiveJul10` was that page for 7.
- Reading does not predict adoption on its own: 169 of 289 non-adopters with named reads also read such a page. That
  comparison is unmatched, so treat it as a caution, not a null.
- Of the 53 edited-page exposures, 18 are confirmed by an earlier read, in 8 the word arrived after the last read, and
  27 have no named read to check.
- In the 9 minutes before coining the word, the originator read `Sector61State5FastSignal` about ten times. That
  included one post warning that repeated cohorts "go silent after R5… may terminate the episode" and another agent's
  "TERMINATION-SAFE" token. Its PRE-SIGNAL post combines the two.

**Bottom line:**
- One coined coordination token spread across families.
- Task-specific signal tokens spread widely but stayed inside their family.
- Wall-clock wording converges locally with no clear direction.
- Name conventions and the generic "presence" habits show no spread beyond shared task and timing.

### Caveats

- Everything is regex-based. `signal token` also matches round labels such as `R3-Social`, and
  `deletion / backup` matches "backup hypothesis".
- Page topic can prompt a word, for example "container" on clock-wait pages. That remains the main confound for the
  variant tests, and it would produce the symmetric past/future pattern seen for the wall side.
- The log joins map names through editor labels. The re-saves listed above make 11 labels point at the wrong
  trajectory, for example `RelayReader` → `S:TransportHelperMar28OAI`. None of those trajectories adopted PRE-SIGNAL,
  but the non-adopter comparison and the save table can carry a few misattributed rows.
- The calibration uses 20 shuffles, so its smallest possible p is 0.048. Which name part reaches that floor changes from
  run to run, as noise would.

## Reproduce

Python 3.10+, standard library only.

```sh
python3 verify.py                  # rebuild trajectories + behavioural analysis in a scratch copy, compare with the committed files
python3 verify.py --build-only     # trajectories and verification cross-check only (a few seconds)
python3 privacy_check.py           # pattern scan for credentials and IP addresses
```

To regenerate in place:

```sh
python3 analysis/signoff-trajectories/build.py
python3 analysis/verification-check/xcheck.py
python3 analysis/behavioral-norms/variant.py analysis/signoff-trajectories analysis/behavioral-norms/results
```

The log-joined steps (`presignal_reads`, `presignal_timing`, `shared_saves`) need the cleaned request logs. Without
them they are skipped and their committed outputs stay; see
[`analysis/request-logs/README.md`](analysis/request-logs/README.md). With the logs, `verify.py` checks them too
(`REQUEST_LOGS=<dir>`).

## Repository layout

| path | contents |
|---|---|
| `full-wiki-logs.zip` | sanitized wiki export (input) |
| `analysis/signoff-trajectories/` | `build.py`, `reviews.json` (the four hand calls), outputs |
| `analysis/behavioral-norms/` | norm, spread, coined-token, log-join and calibration scripts; `variant.py` runs them all |
| `analysis/behavioral-norms/results/` | outputs: `norms.json`, `examples.json`, `spread.json`/`.txt`, `coined.json`/`.txt`, `presignal_*.json`, `fingerprint_null.json`/`.txt`, `shared_saves.json`, `saves.tsv.gz`, `run.log` |
| `analysis/verification-check/` | the verdicts of the human verification phase (verdict-only export) and the cross-check against them |
| `analysis/human-verification/server.py` | archive loader: fresh lines, signoff regex and review flags. It began as the tool for the human verification phase; only its loader is used here |
| `analysis/unsigned-pages/rule_candidates.py` | the R1–R5 signoff forms |
| `analysis/clock-consistency/` | task-clock claim extraction and clash check, used by the containment merge |
| `analysis/fingerprints/` | save-fingerprint extraction (needs the request logs) |
| `analysis/request-logs/` | the log cleaner (logs not included) |
| `analysis/family-inventory/` | the 41 task families |
| `external/betreiberlogs/SHA256SUMS` | checksums pinning the operator logs |

### Changes from the working copies

The scripts are the ones I ran, copied unchanged, with these exceptions:
- `analysis/verification-check/` is new. `verdicts.jsonl` is exported from the verification log, and `xcheck.py` is a
  standalone rewrite of my working cross-check that reads that export and the frozen name table; it gives the same
  counts.
- `build.py`:
  - reads the vendored family inventory;
  - loads the archive directly instead of through the verification tool's dossier store, and reads the dossier names
    for `matches_existing_profile` from a frozen copy (`verified_profile_names.json`);
  - renames one summary key, to `matching_an_existing_verified_profile_name`;
  - rewords one comment.
- `variant.py` reads the request logs from `$REQUEST_LOGS` and skips the log-joined steps without them. It writes a
  relative path into `run.log`, and its docstring is reworded.
- `norms.py` and `server.py` each have docstring lines reworded.
- `reviews.json`:
  - the reviewer field reads `author`;
  - it adds the fourth hand call (`OAI7C97`, not an alias).

Only the fourth call alters an assignment. It moves one edit, `HealthdataCVDSequenceCollab@12`, out of
`S:OAI7C97Dec15`. Otherwise the rebuilt trajectories match the working build exactly, except for 10 edit texts, where the
sanitized archive has redacted an IP address or a local path.

## Privacy

This release is derived from the sanitized archive. `privacy_check.py`, taken from the earlier repository, passes on
every file; its only change is to allow the loopback and bind-all addresses in the verification tool's command-line
defaults. The verification verdicts are published without the notes, tags, post-level calls and text hashes of the raw log, and
without reviewer names or times of day. The operator request logs and their cleaned table are not included. The cleaned table still carries
per-device hashes and some human visitors' reads and searches. The published log-derived outputs contain only
aggregate counts and rows keyed to agent trajectories: names, save times, pages, and cache-buster key names without
values.

## Related work

- Lütje, *The Mechanics of a Swarm* (arXiv 2609.12748; code: PhilflowIO/agent-swarm-forensics). It analyses the same
  export and request logs at the level of cohorts (task family × date, assigned by regex), not per-agent trajectories.
- [fast-follow-question-trajectories](https://github.com/intentionallydense/fast-follow-question-trajectories) is my
  earlier release. It holds 298 LLM-assembled, audited dossiers, and its dossiers were what the human verification
  phase reviewed. This repository is a different, rule-based reconstruction and does not reuse those assignments.
