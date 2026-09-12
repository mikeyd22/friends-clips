#!/usr/bin/env python3
"""Prepare a mechanical execution copy of an approved Friends Method 2 plan.

This script does not discover or rank candidates.  It only carries the
approved candidate records into a run directory, adds stable slugs, and
records the source/audio acquisition choices used by the bounded executor.
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path


def slugify(name: str) -> str:
    value = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return value


def hms(value):
    if isinstance(value, (int, float)):
        return float(value)
    h, m, s = value.split(":")
    return int(h) * 3600 + int(m) * 60 + float(s)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--approved", required=True)
    ap.add_argument("--run", required=True)
    ap.add_argument("--project", required=True)
    ap.add_argument("--audio", required=True)
    ap.add_argument("--format", default="137")
    ap.add_argument("--client", default="web_embedded")
    args = ap.parse_args()

    approved_path = Path(args.approved).resolve()
    run = Path(args.run).resolve()
    project = Path(args.project).resolve()
    approved = json.loads(approved_path.read_text())
    candidates = []
    for source in approved["candidates"]:
        if source.get("status") != "approved_for_execution":
            raise SystemExit(f"unexpected non-approved candidate: {source}")
        name = source["name"]
        slug = slugify(name)
        if any(c["slug"] == slug for c in candidates):
            raise SystemExit(f"duplicate generated slug: {slug}")
        sin = hms(source["source_in"])
        sout = hms(source["source_out"])
        candidates.append({
            "scout_id": source.get("scout_id"),
            "slug": slug,
            "name": name,
            "status": source["status"],
            "source_in": source["source_in"],
            "source_out": source["source_out"],
            "source_in_seconds": round(sin, 3),
            "source_out_seconds": round(sout, 3),
            "duration_seconds": round(sout - sin, 3),
            "opening": source.get("opening"),
            "payoff": source.get("payoff"),
            "micro_arc": source.get("micro_arc"),
            "edit_boundary_note": source.get("edit_boundary_note"),
            "send_reason": source.get("send_reason"),
            "approved_source_record": str(approved_path),
        })

    if len(candidates) != approved["planner_gate"]["approved_candidate_count"]:
        raise SystemExit("approved candidate count mismatch")
    for left, right in zip(candidates, candidates[1:]):
        if right["source_in_seconds"] < left["source_out_seconds"]:
            raise SystemExit(f"approved overlap: {left['slug']} / {right['slug']}")

    run.mkdir(parents=True, exist_ok=True)
    (run / "source" / "ranges").mkdir(parents=True, exist_ok=True)
    (run / "clips").mkdir(parents=True, exist_ok=True)
    (run / "qa").mkdir(parents=True, exist_ok=True)

    plan = {
        "schema_version": "friends-method-2-execution-1",
        "project": approved["project"],
        "method": approved["method"],
        "plan_status": approved["plan_status"],
        "approved_source_plan": str(approved_path),
        "source_constraints": approved["source_constraints"],
        "packaging": approved["packaging"],
        "candidates": candidates,
        "rejected": approved.get("rejected", []),
        "acquisition": {
            "url": f"https://youtu.be/{approved['youtube_id']}",
            "youtube_id": approved["youtube_id"],
            "format": args.format,
            "player_client": args.client,
            "audio_path": str(Path(args.audio).resolve()),
            "handles_seconds": 30.0,
            "video_quality": "1920x1080 H.264 High yuv420p source stream; no upscale",
            "source_cadence": "24000/1001",
            "download_mode": "approved ranges only with 30 second handles",
        },
        "execution_contract": {
            "one_continuous_source_span": True,
            "preserve_horizontal_framing": True,
            "preserve_source_cadence": True,
            "internal_cuts": False,
            "reordering": False,
            "silence_or_filler_removal": False,
            "reframing": False,
            "subtitles": "burned zh-Hans above en; no visible punctuation; active English word",
            "hook_title": False,
            "music": False,
            "b_roll": False,
            "effects": False,
            "transitions": False,
            "metadata": False,
        },
    }
    (run / "plan.json").write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n")
    state = {
        "source": {
            "mode": "url",
            "url": f"https://youtu.be/{approved['youtube_id']}",
            "id": approved["youtube_id"],
            "title": approved["project"],
            "full_audio": str(Path(args.audio).resolve()),
            "ytdlp_format": args.format,
            "ytdlp_args": ["--extractor-args", f"youtube:player_client={args.client}"],
            "video_format": "137 (1920x1080 H.264 High, video-only DASH)",
            "audio_format": "140 (AAC stereo 128 kbps, 44100 Hz)",
            "full_master": False,
            "range_source": "highest actually playable embedded-client format",
        },
        "project": str(project),
        "stage": "planned_for_execution",
        "approved_plan": str(approved_path),
        "index": {
            "vtt": str((project / "index" / "transcript.vtt").resolve()),
            "words": str((project / "index" / "words.json").resolve()),
        },
    }
    (run / "m2.json").write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"run": str(run), "candidate_count": len(candidates), "slugs": [c["slug"] for c in candidates]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
