# Agentic Sleep JITAI

A reusable skill that lets an AI agent run a just-in-time adaptive intervention
(JITAI) for sleep. On a schedule, the agent reviews recent sleep and behavioural
data, looks for patterns, evaluates the reminders already deployed, and decides
whether to keep, modify, remove or add one. Home Assistant delivers the reminders.

This repository accompanies the paper **"An Agentic Just-in-Time Adaptive
Intervention System for Personalized Sleep Support: Proof-of-Concept Study with
N of 1 Data"** ([arXiv:2609.21805](https://arxiv.org/abs/2609.21805)).

> **Research proof-of-concept, not a medical device.** The paper evaluated
> technical feasibility only, not whether the reminders improve sleep.

## Architecture

```text
Behavioral and sleep data
        ↓
Agent reviews recent history
        ↓
Agent identifies patterns
        ↓
Agent evaluates active interventions
        ↓
Keep / modify / remove / add intervention
        ↓
Home Assistant delivers intervention
        ↓
Human oversight
```

Key design choices, all taken from the deployment used in the paper:

- **No change is the default.** At most one structural change per run.
- **Cap on active interventions** (3 by default) and a notification budget.
- **Evidence guards.** Findings must survive a split-half check and be confirmed on two
  different days before they justify a new intervention.
- **Conservative evaluation.** A deployed reminder is protected for 14 nights. A low fire
  count is never taken as failure.
- **A record on every run**, including no-change and failed runs, for human review.
- **Human oversight.** `approval_mode: propose` (the default) writes changes for a person
  to apply. `apply` lets the agent edit automations itself, which is how the paper's runs
  worked, with daily review of the records. Users can veto any reminder by turning it off.

## Contents

| file | purpose |
|---|---|
| `SKILL.md` | the agent's workflow; the file that controls its reasoning |
| `resources/config.example.yaml` | goal, entities, analysis settings, limits, oversight |
| `resources/analyze.py` | reference implementation of the pattern-finding rules (stdlib + PyYAML) |
| `resources/intervention_template.yaml` | template for an agent-owned Home Assistant automation |
| `resources/automations.example.yaml` | SYNTHETIC existing automations file with one protected reminder |
| `resources/synthetic_sleep_data.csv` | SYNTHETIC 30-night nightly table |
| `resources/example_decision.json` | SYNTHETIC decision record for the worked example |

## Requirements

- An AI agent that can read files and run shell commands. The paper used Claude Code;
  any LLM agent that can follow the skill and reach Home Assistant works in principle.
- Python 3.9+ with PyYAML.
- For live use, Home Assistant with the recorder enabled for your sleep and behaviour sensors.

## Configuration

1. Copy `resources/config.example.yaml` to `resources/config.yaml` (gitignored).
2. Set `goal`, map `home_assistant.signals` to your own entities (for example
   `sensor.sleep_duration`), and set `notify_service`, the `protected_automation_ids` of
   automations the agent must never touch, and `automations_file` (usually
   `/config/automations.yaml`).
3. Give the agent Home Assistant API access through its environment (`HA_URL`, `HA_TOKEN`).
   Never commit tokens.

## Running

Worked example on SYNTHETIC data, with no Home Assistant needed:

```bash
python resources/analyze.py --nights resources/synthetic_sleep_data.csv --as-of 2026-04-30 --out /tmp/run1.json
python resources/analyze.py --nights resources/synthetic_sleep_data.csv --as-of 2026-05-01 --previous /tmp/run1.json
```

In the first run, `steps -> minutes_asleep` is a candidate that is seen once, so it goes on
the watchlist. In the second run, on a different day, it is confirmed.
`evening_screen_min -> bed_offset_min` fails the split-half guard.
`resources/example_decision.json` shows the record an agent would write from this: it
proposes one upstream "afternoon activity nudge" for human approval.

For real use, install the skill (for Claude Code, copy this folder to `.claude/skills/agentic-sleep-jitai/`)
and schedule the agent daily. The original deployment ran at 14:00 local from cron inside
a Home Assistant add-on:

```bash
0 14 * * * cd /path/to/workdir && claude -p "Run the agentic-sleep-jitai skill." --allowedTools "Bash Read Write Edit Glob Grep" --permission-mode acceptEdits
```

## Using other data sources and behaviours

Anything that produces the nightly table works: a wearable export, a phone app, or a study
database. Use one row per `night_of` and leave missing values blank, never 0. Add columns,
then pre-register them in `analysis` in the config. For other behaviours (physical activity,
medication routines, stress), change the goal, the columns and the wording of `SKILL.md`,
then test before deploying.

## How SKILL.md controls the agent

The agent's behaviour lives in plain Markdown. Change the data it reviews, the evidence
thresholds, the gates or what it records by editing `SKILL.md` and the config. No code
changes are needed. Editing the instructions does not guarantee good decisions, so test
any revision and keep reviewing the records.

## Provenance and privacy

This is derived from the authors' personalized Home Assistant deployment. Personal entity
ids, devices, notification targets, logs and historical records were removed or replaced
with generic values and synthetic examples. In the original, the agent wrote its own
analysis code on each run; `resources/analyze.py` factors those rules out for
reproducibility. Runtime state (`runtime/`) contains personal data and is gitignored.

MIT licensed; see `LICENSE`.
