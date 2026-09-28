---
name: agentic-sleep-jitai
description: Scheduled sleep JITAI (just-in-time adaptive intervention) review. Reviews about 30 days of sleep and behavioural data, looks for patterns under guards against noise, evaluates the interventions already deployed, and decides whether to keep, modify, remove or add one, within a cap of active interventions and with human oversight. Use when asked to run the daily sleep review or review sleep interventions.
---

# Agentic Sleep JITAI: scheduled review

You run unattended on a schedule. Nobody will answer a question. Be decisive, never
invent a number, and fail loudly. Settings come from `resources/config.yaml`
(fall back to `resources/config.example.yaml`); defaults are shown in brackets.

## 0. Operating rules (these override everything below)

1. **The default is NO CHANGE.** You ran because a timer fired, not because something
   is wrong. "Existing interventions are sufficient; no change" is a complete,
   successful run and should be the most common outcome.
2. **At most one structural change per run** (`interventions.max_changes_per_run` [1]):
   one add, modify, remove or repair. Editing a message or trigger time counts.
   Cap repair (§4, step 0) is exempt and runs first.
3. **Hard cap** (`interventions.max_active` [3]) on automations whose alias starts with
   `interventions.owned_alias_prefix`, counted whether enabled or disabled. Never exceed it,
   not even mid-edit.
4. **You own only those automations.** Everything else, especially
   `interventions.protected_automation_ids`, is read-only forever: never modify, disable,
   rename, reorder or reformat it.
5. **Never fabricate.** Every number you record traces to a data row you read. Missing data
   is reported as missing, never as 0.
6. Never print or log tokens. Never restart Home Assistant; reload automations only.
7. No write to the automations file without a verified backup taken first.
8. **Human oversight.** With `oversight.approval_mode: propose` [default] you never edit
   automations: you write the proposed change into the run record for a human to apply.
   With `apply` (how the paper's runs worked) you edit it yourself and a human reviews the records.
   Either way a human can veto any intervention by turning it off, or mute them all through
   `interventions.notification_channel`.

## 1. Preflight: abort (§7) if any check fails

- The data source answers (Home Assistant: `GET /api/` returns `API running.`).
- The configured `timezone` resolves and is not silently UTC. Otherwise every night
  boundary shifts and all findings are corrupt. Do date arithmetic in code with a tz
  library, never by hand.
- `interventions.notify_service` still exists. If it is gone, every intervention is a
  silent no-op. If the operator alert service is missing, only warn.
- The automations file parses **before** you touch it. If not, report it and change nothing.
- Read `runtime/history/last_status.json`. If the previous run failed, escalate that in today's record.

## 2. Review the data (`analysis.window_days` [30])

Build one row per `night_of`: the local date of the evening the night starts. Use only
pre-registered columns (`analysis.outcomes`, `predictors`, `context_only`); never invent
new features mid-run. Merge into the durable table `runtime/history/nights.csv`: fill
nulls, apply corrections, and log any overwrite.

Cleaning rules (Home Assistant specifics in `resources/config.example.yaml`):
- Drop `unknown`, `unavailable`, `none` and empty states. Drop 0 where 0 is impossible
  (minutes asleep), but keep it where 0 is legal.
- Wearable values usually reset after midnight and arrive later that morning. The last
  clean value on local date E (within `nightly_value_band`) describes night E-1. The wearable
  may not have synced yet, so check which night is actually the most recent measured one.
- Convert bedtime to minutes past 18:00 before any statistic. Never average clock strings:
  23:50 and 00:10 average to noon.
- For daily counters, take the max per local day (the min if the counter counts down),
  never the last value.
- Binary sensors: pair state changes into intervals. Unavailable time counts toward neither
  side; more than `max_gap_fraction` [0.2] missing, or zero rows, gives null, never 0.
- A signal stuck at one value for the whole window is a dead channel, not an observation.
  Only use signals that actually have recorded history. An empty history is not evidence.
- **Predictor windows are fixed clock times** (for example screen minutes 21:00-23:00),
  never "until sleep onset". An onset-relative window mechanically correlates with bedtime.
- A feature that an existing reminder manipulates (for example routine completion) is
  **context only**, never an independent predictor.
- Expect fewer usable nights than calendar days, and report the actual count. With fewer
  than `analysis.min_valid_nights` [10] nights of sleep data: status `degraded`, skip
  §3-§5, change nothing, record why.

## 3. Look for patterns, with guards against noise

Run `python resources/analyze.py --nights runtime/history/nights.csv --as-of <today>
--previous <latest findings from an earlier day> --out runtime/history/findings-<today>.json`,
or reproduce its rules yourself:
- Medians and IQRs, never means. State `n` for every number. Report weekday and weekend bedtime separately.
- For each configured predictor-outcome pair, split at the predictor median. If either arm
  has fewer than `min_nights_per_arm` [8] nights, write exactly "insufficient evidence";
  do not soften it into a trend.
- **Split-half guard:** a gap counts only if it holds in both the older and the newer half
  of the window, or the two arms' IQRs do not overlap.
- **Two-run confirmation:** an association can justify a *new* intervention only if the
  same pair, in the same direction, also cleared the guards on a previous run on a
  **different calendar day**. A finding seen once goes on the watchlist.
- Use association language only ("nights with more than X ... (n=12) had ... vs ... (n=14)"),
  never "causes". Name visible confounds, such as the weekend share of each arm.
- Relate findings to the user's `goal` (`primary_outcome`, `direction`).

## 4. Review active interventions

Automation entities usually have **no recorder history**, so zero history rows never means
"never fired". It means unobservable. On every run, record each owned automation's `state` and
`attributes.last_triggered` in `runtime/history/ledger.json`. You build the fire history
yourself, one observation per night. When you deploy an intervention, store its target
metric's baseline median and `n` in the ledger. If the ledger is lost, rebuild it from each
block's `description` metadata (take `created=` from there, not from today), flag it, assign
no verdicts this run, and continue.

Assign exactly one verdict per owned intervention:
- **BROKEN**: structural evidence only. The automation or an entity it references no longer
  exists. A low or zero fire count is never BROKEN: when behaviour improves, conditional
  triggers stop matching, so success and failure produce the same number.
- **VETOED**: the user turned it off. This is a rejection, not a fault. Never re-enable it.
  It still uses a cap slot and is first in line for removal.
- **TOO_EARLY**: fewer than `too_early_nights` [14] ledger nights since deployment or the
  last change. Protected: you may not judge, tune or replace it.
- **PROMISING**: the target moved in the intended direction by more than the night-to-night
  IQR. Protected.
- **MIXED**: right direction, but too small or inconsistent to call. Keep it.
- **INEFFECTIVE**: at least `ineffective_after_nights` [21] nights deployed and
  `ineffective_min_exposed_nights` [10] exposed nights, with no movement or movement the
  wrong way. Replaceable, and its original evidence no longer defends it.
- **STALE**: at least `stale_after_nights` [60] nights and still not evaluable. Replaceable
  after INEFFECTIVE.

Prefer a within-period contrast (nights exposed vs not exposed since deployment) over
pre/post. Record `n_exposed`, `n_control`, both medians and the difference. If the weekend
share of the two arms differs sharply, downgrade the verdict.

## 5. Decide: keep, modify, remove or add

Each candidate must pass all five gates, in order:
1. **Evidence:** it names a §3 finding with `n` that passed both guards.
2. **Upstream first:** (a) daytime inputs, then (b) evening environment, then (c) routine
   initiation, then (d) another bedtime push. A tier (d) candidate needs one sentence on why
   no (a)-(c) lever exists.
3. **Duplicate:** reject it if any automation, owned or not, fires within
   ±`duplicate_window_minutes` [45] and targets the same behaviour. If a bedtime reminder
   already exists, "remind them to go to bed" is a duplicate.
4. **Burden (`interventions.budget`):** across all owned interventions, at most
   `max_pushes_per_day` [2], at most `max_pushes_in_evening` [1] in `evening_window`,
   none in `quiet_hours`, and at least `min_spacing_minutes` [30] from any other reminder.
   `notify_service` is the only intervention channel; the operator channel never carries
   interventions. If the user mutes the channel, every later measurement is meaningless.
   Gate 3 outranks gate 4.
5. **Cooldown:** no intervention is structurally changed twice within `cooldown_days` [14].

Zero surviving candidates is normal and correct. Then run these steps in order and stop at
the first match (A = owned blocks, whatever their state):
0. `len(A) > max_active`: delete the newest owned blocks down to the cap (exempt, runs first).
1. Any BROKEN or VETOED: repair it if the fix is obvious and local, otherwise remove it.
   That is this run's one change.
2. No candidate survived: **no change**.
3. `len(A) < max_active`: **add** the strongest candidate.
4. At the cap: replace the oldest INEFFECTIVE, then STALE. If there is none, no change;
   record "queued, blocked by cap".
5. Any tie, ambiguity or doubt: **no change**. Record your reasoning.

## 6. Apply or propose the change

Write the block from `resources/intervention_template.yaml`. It needs a quoted epoch-ms
`id`, the owned prefix, the `description` metadata line (`created`, `review_after`, `target`,
`direction`, `hypothesis`, `evidence`), `mode: single`, the once-per-night `last_triggered`
guard (`mode: single` alone does not stop re-firing), and `channel`. Every threshold comes
from your findings. In `propose` mode, put the block in the record and stop. In `apply` mode:
1. Copy the automations file to `runtime/backups/automations.yaml.<timestamp>`, verify the
   copy matches and parses. No backup, no write.
2. Make a **surgical text edit**: append a block or replace the exact lines of an owned block.
   Never round-trip the file through a YAML dumper, which reformats automations you do not own.
   Read aliases with `.get("alias", "")`.
3. Validate: the file parses to a list; protected entries are identical to the backup; every
   changed line is inside an owned block; ids are unique; every entry has triggers and actions
   (plural or legacy singular keys); the owned count is within the cap.
4. Reload automations (never restart). A reload can succeed while rejecting one automation,
   so verify that **the one you touched** loaded. Do not check that "all owned are on",
   because a vetoed intervention is legitimately off. On a mismatch, restore the backup,
   reload, and set status `failed`.

## 7. Record every run, and handle failures

A run with no record did not happen. On every path, including no change, degraded and
failed, write `runtime/records/<date>.md` and `.json`. Include:
- the timestamp and timezone, the data window, usable nights and rows per signal;
- findings with `n`;
- automations reviewed with ownership and a verdict with its numbers;
- each candidate and the gate that rejected it;
- the decision step that fired, and the change (or "No change made: <reason>");
- the final active list, and errors and data gaps.

Then update `ledger.json` (per intervention: id, alias, created, last_modified, target,
direction, baseline, review_after, last_triggered samples, verdicts; plus the watchlist and
the last change). Write `last_status.json` (`status`, `step`, `error`, `timestamp`) **last**.

Hard-fail on any of these: the source is unreachable, the token is missing, the timezone
check fails, the automations file does not parse, the data is truncated (a chatty signal's
window spans 6 days or less), a protected automation was altered, or the cap cannot be
restored. Abort before any write, restore the backup if you applied one, and record the
exact error. Send **one** alert through `oversight.operator_notify_service` (never the
intervention channel). Skip it if the last run already reported the same cause, or if the
API itself is down. Never notify on success. A clean abort with a good record is correct;
do not attempt clever recovery.

## 8. Final check: answer yes to all, or make no change

- Does every number trace to a row I actually read?
- Did the finding pass split-half **and** a previous run on a different day?
- Am I treating missing fire history as unobservable, not as "never fired"?
- Is this exactly one structural change, to an intervention untouched for the cooldown period?
- Does it pass the duplicate and burden tests against every existing reminder?
- Are the protected automations untouched, and am I still within the cap afterwards?
- Is this the most upstream lever the evidence supports, and does it serve the user's goal?
- **Would I make this same change if the job had also run yesterday and the day before, or
  am I changing something because today's run wants an output?**

## Adapting to other users, sensors and behaviours

Edit the config, not this workflow: map `home_assistant.signals` to your entities, choose
the `goal`, pre-register `analysis` columns and pairs, and set limits. Any data source that
yields the nightly table works (`resources/synthetic_sleep_data.csv` shows the format).
Other behaviours (activity, medication routines, stress) need new columns, a new goal and
tested wording in this file.
