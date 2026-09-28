"""Describe the last N nights and test pre-registered predictor -> outcome pairs.

Reference implementation of the evidence rules in SKILL.md. In the original
deployment the agent wrote this analysis itself on each run; it is factored out
here so runs are reproducible and the example works offline.

Input is a nightly table (CSV, one row per night_of, blank = missing):
    night_of,bedtime,wake_time,minutes_asleep,steps,evening_screen_min,routine_completed
Lines starting with '#' are ignored.

Usage:
    python resources/analyze.py --nights resources/synthetic_sleep_data.csv --as-of 2026-04-30
    python resources/analyze.py --nights ... --as-of 2026-05-01 --previous findings-2026-04-30.json
"""
import argparse
import csv
import datetime as dt
import json
import statistics
import sys
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent


def load_config(path):
    if path is None:
        path = HERE / "config.yaml"
        if not path.exists():
            path = HERE / "config.example.yaml"
    return yaml.safe_load(Path(path).read_text())


def bed_offset(clock):
    """Minutes past 18:00. Never average raw clock strings: 23:50 and 00:10 average to noon."""
    hh, mm = map(int, clock.split(":"))
    if hh >= 18:
        return (hh - 18) * 60 + mm
    if hh < 12:
        return (hh + 6) * 60 + mm
    return None  # 12:00-17:59 is not a plausible bedtime: drop it


def offset_to_clock(offset):
    total = (int(round(offset)) + 18 * 60) % (24 * 60)
    return f"{total // 60:02d}:{total % 60:02d}"


def load_nights(path):
    lines = [l for l in Path(path).read_text().splitlines() if l.strip() and not l.startswith("#")]
    nights = []
    for row in csv.DictReader(lines):
        night = {"night_of": dt.date.fromisoformat(row["night_of"])}
        for key, value in row.items():
            if key == "night_of":
                continue
            value = (value or "").strip()
            if value == "":
                night[key] = None  # missing stays missing, never 0
            elif ":" in value:
                night[key] = value
            else:
                night[key] = float(value)
        bedtime = night.get("bedtime")
        night["bed_offset_min"] = bed_offset(bedtime) if bedtime else None
        nights.append(night)
    return nights


def describe(values):
    values = sorted(v for v in values if v is not None)
    if not values:
        return {"n": 0, "median": None, "q1": None, "q3": None}
    q1 = q3 = None
    if len(values) >= 2:
        q1, _, q3 = statistics.quantiles(values, n=4, method="inclusive")
    return {"n": len(values), "median": statistics.median(values), "q1": q1, "q3": q3}


def arms(rows, predictor, outcome, threshold):
    high = [r[outcome] for r in rows if r[predictor] > threshold]
    low = [r[outcome] for r in rows if r[predictor] <= threshold]
    return describe(high), describe(low)


def test_pair(nights, predictor, outcome, cfg):
    rows = sorted((r for r in nights if r.get(predictor) is not None and r.get(outcome) is not None),
                  key=lambda r: r["night_of"])
    result = {"pair": [predictor, outcome], "n": len(rows)}
    if not rows:
        return {**result, "status": "insufficient_evidence"}
    threshold = statistics.median(r[predictor] for r in rows)
    high, low = arms(rows, predictor, outcome, threshold)
    result.update(threshold=threshold, high=high, low=low)
    if min(high["n"], low["n"]) < cfg["min_nights_per_arm"]:
        return {**result, "status": "insufficient_evidence"}
    diff = high["median"] - low["median"]
    result["difference"] = diff
    if diff == 0:
        return {**result, "status": "no_gap"}
    result["direction"] = "higher" if diff > 0 else "lower"

    # Split-half guard: with ~25 nights and several pairs, the largest gap is often noise.
    # A gap counts only if it has the same sign in both halves, or the arms' IQRs do not overlap.
    half = len(rows) // 2
    halves = {}
    for name, part in (("older", rows[:half]), ("newer", rows[half:])):
        h, l = arms(part, predictor, outcome, threshold)
        holds = (min(h["n"], l["n"]) >= cfg["min_nights_per_half_arm"]
                 and (h["median"] - l["median"]) * diff > 0)
        halves[name] = {"high_n": h["n"], "high_median": h["median"],
                        "low_n": l["n"], "low_median": l["median"], "holds": holds}
    iqr_overlap = not (high["q1"] > low["q3"] or high["q3"] < low["q1"])
    passed = (halves["older"]["holds"] and halves["newer"]["holds"]) or not iqr_overlap
    result.update(split_half=halves, iqr_overlap=iqr_overlap)
    if cfg["require_split_half"] and not passed:
        return {**result, "status": "failed_split_half"}
    result["status"] = "candidate"
    result["summary"] = (f"Nights with {predictor} above {threshold:g} (n={high['n']}) had a median "
                         f"{outcome} of {high['median']:g} vs {low['median']:g} on other nights "
                         f"(n={low['n']}). Association only.")
    return result


def confirm(pair, previous, as_of):
    """Two-run confirmation: the same finding on a previous run from a different calendar day."""
    if not previous:
        return False, "no previous run"
    if previous["as_of"] == as_of:
        return False, "previous run is the same calendar day"
    for old in previous.get("pairs", []):
        if old["pair"] == pair["pair"]:
            if old.get("status") == "candidate" and old.get("direction") == pair.get("direction"):
                return True, f"also a candidate on {previous['as_of']}"
            return False, f"not a candidate on {previous['as_of']}"
    return False, "pair not tested previously"


def analyze(nights, config, as_of, previous=None):
    cfg = config["analysis"]
    start = as_of - dt.timedelta(days=cfg["window_days"])
    # A run on day D can only see sleep up to the night of D-1.
    window = [r for r in nights if start <= r["night_of"] < as_of]
    primary = config["goal"]["primary_outcome"]
    with_sleep = [r for r in window if r.get(primary) is not None]
    columns = cfg["outcomes"] + cfg["predictors"] + cfg.get("context_only", [])
    weekend_days = {"Mon": 0, "Tue": 1, "Wed": 2, "Thu": 3, "Fri": 4, "Sat": 5, "Sun": 6}
    weekend = {weekend_days[d] for d in cfg["weekend_nights"]}

    def bedtime_summary(rows):
        d = describe([r["bed_offset_min"] for r in rows])
        return {"n": d["n"], "median": offset_to_clock(d["median"]) if d["n"] else None}

    out = {
        "schema": "findings/1",
        "synthetic_input": None,
        "as_of": as_of.isoformat(),
        "window": {"start": start.isoformat(), "end": (as_of - dt.timedelta(days=1)).isoformat(),
                   "nights_in_window": len(window), "nights_with_sleep": len(with_sleep)},
        "descriptives": {c: describe([r.get(c) for r in window]) for c in columns},
        "bedtime": {"all": bedtime_summary(window),
                    "weekday": bedtime_summary([r for r in window if r["night_of"].weekday() not in weekend]),
                    "weekend": bedtime_summary([r for r in window if r["night_of"].weekday() in weekend])},
        "context_only": cfg.get("context_only", []),
        "pairs": [],
    }
    if len(with_sleep) < cfg["min_valid_nights"]:
        out["status"] = "degraded"
        out["reason"] = f"only {len(with_sleep)} nights with {primary}; need {cfg['min_valid_nights']}"
        return out
    out["status"] = "ok"
    for predictor, outcome in cfg["pairs"]:
        pair = test_pair(window, predictor, outcome, cfg)
        if pair["status"] == "candidate":
            pair["confirmed"], pair["confirmation_note"] = confirm(pair, previous, out["as_of"])
        else:
            pair["confirmed"] = False
        out["pairs"].append(pair)
    return out


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--nights", required=True, help="nightly table CSV")
    parser.add_argument("--config", help="config YAML (default resources/config.yaml, else the example)")
    parser.add_argument("--as-of", help="run date YYYY-MM-DD (default today)")
    parser.add_argument("--previous", help="findings JSON from an earlier run, for two-run confirmation")
    parser.add_argument("--out", help="write JSON here instead of stdout")
    args = parser.parse_args()

    config = load_config(args.config)
    as_of = dt.date.fromisoformat(args.as_of) if args.as_of else dt.date.today()
    previous = json.loads(Path(args.previous).read_text()) if args.previous else None
    result = analyze(load_nights(args.nights), config, as_of, previous)
    result["synthetic_input"] = Path(args.nights).read_text().startswith("# SYNTHETIC DATA")
    text = json.dumps(result, indent=2)
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(text + "\n")
    else:
        print(text)


if __name__ == "__main__":
    sys.exit(main())
