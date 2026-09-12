#!/usr/bin/env python3
"""Validate supplied analytics and summarize comparable cohorts; never change policy."""
import argparse
from datetime import datetime
import hashlib
import json
import math
from pathlib import Path
import re
import statistics

DEFAULT_PROFILE = Path(__file__).resolve().parents[2] / "System/audiences/legacy-short-form.json"


def timestamp(value):
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        raise ValueError("Timestamps must include a timezone")
    return dt


def validate(record, profile):
    for key in ("post_id", "platform", "source_type", "audience_profile", "candidate_slug", "revision_reason"):
        if not isinstance(record.get(key), str) or not record[key].strip() or record[key].startswith("REPLACE_"):
            raise ValueError(f"Missing real {key}")
    if record["audience_profile"] != profile["id"]:
        raise ValueError("Record audience_profile differs from selected profile")
    if not re.fullmatch(r"[0-9a-f]{64}", record.get("published_render_sha256", "")):
        raise ValueError("Record needs the exact published render SHA-256")
    published, observed = timestamp(record["published_at"]), timestamp(record["observed_at"])
    pred = record["predictions"]
    if timestamp(pred["recorded_at"]) > published:
        raise ValueError("Predictions must have been recorded before publication")
    interval = record["reporting_interval_hours"]
    if interval not in profile["review"]["reporting_intervals_hours"]:
        raise ValueError("Reporting interval is not declared in the audience profile")
    elapsed = (observed - published).total_seconds() / 3600
    if abs(elapsed - interval) > max(1.0, interval * 0.05):
        raise ValueError("Observation is outside the declared reporting interval tolerance")
    for key in ("stop", "hold", "send", "audience_fit"):
        if type(pred.get(key)) is not int or not 0 <= pred[key] <= 4:
            raise ValueError(f"Invalid predicted {key}")
    if pred["send"] >= 3:
        if not pred.get("send_recipient", "").strip() or not pred.get("send_reason", "").strip() or not any(re.fullmatch(r"X[1-6]", str(code)) for code in pred.get("send_codes", [])):
            raise ValueError("Send >=3 requires recipient, reason and an X code")
    duration = record["duration_seconds"]
    if type(duration) not in (int, float) or not math.isfinite(duration) or duration <= 0:
        raise ValueError("Invalid clip duration")
    metrics = record["metrics"]
    for key in ("views", "shares", "saves", "comments"):
        value = metrics.get(key)
        if value is not None and (type(value) is not int or value < 0):
            raise ValueError(f"Invalid {key}; use null when unavailable")
    for key in ("average_watch_seconds", "completion_rate"):
        value = metrics.get(key)
        if value is not None and (type(value) not in (int, float) or not math.isfinite(value) or value < 0):
            raise ValueError(f"Invalid {key}")
    if metrics.get("completion_rate") is not None and metrics["completion_rate"] > 1:
        raise ValueError("completion_rate is a fraction between 0 and 1")
    return record


def observation_key(record):
    return (record["platform"], record["post_id"], record["reporting_interval_hours"])


def append_observation(path, record):
    existing = [json.loads(line) for line in path.read_text().splitlines() if line.strip()] if path.exists() else []
    matches = [r for r in existing if observation_key(r) == observation_key(record)]
    if matches:
        if matches[0] == record:
            return "already_recorded"
        raise ValueError("Observation already exists with different values; preserve history and inspect the conflict")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as f:
        f.write(json.dumps(record, sort_keys=True, allow_nan=False) + "\n")
    return "recorded"


def summarize(records, profile):
    groups = {}
    seen = set()
    for record in records:
        validate(record, profile)
        key = observation_key(record)
        if key in seen:
            raise ValueError("Duplicate post/interval observation")
        seen.add(key)
        cohort = tuple(record[k] for k in profile["review"]["comparison_keys"])
        groups.setdefault(cohort, []).append(record)
    results = []
    for key, rows in sorted(groups.items()):
        count = len(rows)
        result = dict(zip(profile["review"]["comparison_keys"], key))
        result["published_posts"] = count
        result["preference_review_due"] = count >= profile["review"]["published_posts_per_comparable_cohort"]
        result["send_qualifying_published_fraction"] = sum(r["predictions"]["send"] >= 3 for r in rows) / count
        result["send_target_met"] = result["send_qualifying_published_fraction"] >= profile["transmission"]["published_send_qualifying_fraction"]
        for metric in ("shares", "saves", "comments"):
            paired = [r for r in rows if r["metrics"].get(metric) is not None and r["metrics"].get("views", 0) is not None and r["metrics"].get("views", 0) > 0]
            denominator = sum(r["metrics"]["views"] for r in paired)
            result[metric + "_per_view"] = sum(r["metrics"][metric] for r in paired) / denominator if denominator else None
            result[metric + "_sample_posts"] = len(paired)
        retention = [r["metrics"]["average_watch_seconds"] / r["duration_seconds"] for r in rows if r["metrics"].get("average_watch_seconds") is not None]
        completion = [r["metrics"]["completion_rate"] for r in rows if r["metrics"].get("completion_rate") is not None]
        result["median_watch_fraction"] = statistics.median(retention) if retention else None
        result["watch_fraction_sample_posts"] = len(retention)
        result["median_completion_rate"] = statistics.median(completion) if completion else None
        result["completion_sample_posts"] = len(completion)
        results.append(result)
    return {"schema": "clipping.feedback-summary.v1", "cohorts": results,
            "preferences_changed": False,
            "interpretation": "Descriptive local evidence only. Cohort thresholds prompt review, not significance or causality."}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--project", required=True)
    ap.add_argument("--profile", type=Path, default=DEFAULT_PROFILE)
    ap.add_argument("--record", type=Path, help="User-supplied JSON observation to validate and append")
    ap.add_argument("--render", type=Path, help="Verify the supplied observation against this actual published MP4")
    args = ap.parse_args()
    profile = json.loads(args.profile.read_text())
    ledger = Path(args.project) / "analytics" / "posts.jsonl"
    if args.record:
        record = validate(json.loads(args.record.read_text()), profile)
        if args.render:
            with args.render.open("rb") as handle:
                digest = hashlib.file_digest(handle, "sha256").hexdigest()
            if digest != record["published_render_sha256"]:
                raise ValueError("Supplied file is not the recorded published render")
        print(append_observation(ledger, record))
    rows = [json.loads(line) for line in ledger.read_text().splitlines() if line.strip()] if ledger.exists() else []
    report = summarize(rows, profile)
    out = ledger.parent / "summary.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
