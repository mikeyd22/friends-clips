#!/usr/bin/env python3
"""Create and validate the human/external Method 2 review record.

The default command creates a ``PENDING_REVIEW`` template and extracts a
small evidence set: opening and ending stills, stills on both sides of every
splice, and short moving excerpts around splices and the payoff.  The
presence of those files is recorded as evidence only.  A reviewer must fill
the structured visual and semantic checks before ``--validate`` can pass.

Example::

    python3 Tools/method-2/m2_review.py \
      --edl clips/example/edl.json \
      --render clips/example/clip.mp4 \
      --vtt _cache/project/qa/example/rendered-transcript-vtt/rendered.vtt \
      --output _cache/project/review/example.json

After a reviewer completes the JSON record, validate it with::

    python3 Tools/method-2/m2_review.py --validate \
      --record _cache/project/review/example.json \
      --edl clips/example/edl.json --render clips/example/clip.mp4 \
      --vtt _cache/project/qa/example/rendered-transcript-vtt/rendered.vtt
"""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path
from typing import Any

import m2_quality


def run(command: list[str], *, check: bool = False) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, capture_output=True, text=True, check=check)


def media_duration(path: Path) -> float:
    result = run([
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "default=nw=1:nk=1", str(path),
    ], check=True)
    return float(result.stdout.strip())


def extract_frame(clip: Path, at_seconds: float, output: Path) -> dict[str, Any]:
    output.parent.mkdir(parents=True, exist_ok=True)
    result = run([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-ss", f"{max(0.0, at_seconds):.3f}",
        "-i", str(clip), "-frames:v", "1", "-an", "-c:v", "png", str(output), "-y",
    ])
    if result.returncode != 0 or not output.exists():
        return {
            "type": "still",
            "path": str(output),
            "at_seconds": round(at_seconds, 3),
            "status": "error",
            "error": (result.stderr or result.stdout).strip()[-1000:],
        }
    return {
        "type": "still",
        "path": str(output),
        "at_seconds": round(at_seconds, 3),
        "status": "created",
    }


def extract_excerpt(clip: Path, start: float, duration: float, output: Path, kind: str) -> dict[str, Any]:
    output.parent.mkdir(parents=True, exist_ok=True)
    safe_duration = max(0.1, duration)
    result = run([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-ss", f"{max(0.0, start):.3f}",
        "-i", str(clip), "-t", f"{safe_duration:.3f}", "-an", "-c:v", "libx264",
        "-preset", "ultrafast", "-pix_fmt", "yuv420p", str(output), "-y",
    ])
    if result.returncode != 0 or not output.exists():
        return {
            "type": kind,
            "path": str(output),
            "start_seconds": round(start, 3),
            "duration_seconds": round(safe_duration, 3),
            "status": "error",
            "error": (result.stderr or result.stdout).strip()[-1000:],
        }
    return {
        "type": kind,
        "path": str(output),
        "start_seconds": round(start, 3),
        "duration_seconds": round(safe_duration, 3),
        "status": "created",
    }


def output_timeline(edl: dict[str, Any], duration: float) -> list[dict[str, Any]]:
    """Map source units to output boundaries for review evidence."""

    units = edl.get("segments") or edl.get("beats") or []
    timeline: list[dict[str, Any]] = []
    cursor = 0.0
    for index, unit in enumerate(units):
        unit_duration = max(0.0, float(unit.get("out", 0)) - float(unit.get("in", 0)))
        end = min(duration, cursor + unit_duration)
        timeline.append({
            "index": index + 1,
            "start_seconds": round(cursor, 3),
            "end_seconds": round(end, 3),
            "source": {key: unit.get(key) for key in ("id", "in", "out", "source_in", "source_out", "angle") if key in unit},
        })
        cursor = end
    return timeline


def prepare_evidence(clip: Path, edl: dict[str, Any], evidence_dir: Path) -> list[dict[str, Any]]:
    """Extract review evidence without declaring any visual result."""

    duration = media_duration(clip)
    evidence_dir.mkdir(parents=True, exist_ok=True)
    evidence: list[dict[str, Any]] = []
    evidence.append({
        **extract_frame(clip, min(0.10, max(0.0, duration - 0.01)), evidence_dir / "opening.png"),
        "type": "opening_still",
        "purpose": "opening hook and source setup",
    })
    evidence.append({
        **extract_frame(clip, max(0.0, duration - 0.10), evidence_dir / "end.png"),
        "type": "end_still",
        "purpose": "payoff and live tail",
    })

    timeline = output_timeline(edl, duration)
    # A moving excerpt around the final unit is the payoff evidence.  The
    # final review record must still say that a reviewer watched it.
    payoff_start = max(0.0, duration - min(3.0, duration))
    evidence.append({
        **extract_excerpt(clip, payoff_start, min(3.0, duration - payoff_start), evidence_dir / "payoff.mp4", "payoff_moving_excerpt"),
        "purpose": "moving payoff and tail evidence",
    })

    for item in timeline[:-1]:
        index = int(item["index"])
        boundary = float(item["end_seconds"])
        before_at = max(0.0, boundary - 0.12)
        after_at = min(max(0.0, duration - 0.01), boundary + 0.12)
        evidence.append({
            **extract_frame(clip, before_at, evidence_dir / f"splice_{index:03d}_before.png"),
            "type": "splice_before_still",
            "splice_index": index,
            "purpose": "frame before the splice",
        })
        evidence.append({
            **extract_frame(clip, after_at, evidence_dir / f"splice_{index:03d}_after.png"),
            "type": "splice_after_still",
            "splice_index": index,
            "purpose": "frame after the splice",
        })
        moving_start = max(0.0, boundary - 0.75)
        moving_duration = min(1.5, duration - moving_start)
        evidence.append({
            **extract_excerpt(clip, moving_start, moving_duration, evidence_dir / f"splice_{index:03d}.mp4", "splice_moving_excerpt"),
            "splice_index": index,
            "purpose": "moving evidence around the splice",
        })
    return evidence


def default_evidence_dir(edl_path: Path, candidate: str) -> Path:
    """Place temporary review evidence under the active project's cache."""

    # .../<project>/clips/<candidate>/edl.json is the standard layout.
    if edl_path.parent.parent.name == "clips":
        project = edl_path.parent.parent.parent
        return project / "_cache" / project.name / "review-evidence" / candidate
    return edl_path.parent / "_cache" / "review-evidence"


def _check_template(name: str) -> dict[str, Any]:
    return {"name": name, "completed": False, "reviewed": False, "status": "PENDING", "notes": ""}


def _vtt_record(rendered_vtt: Path | None) -> dict[str, Any]:
    if not rendered_vtt:
        return {"path": None, "sha256": None, "provenance": None, "render_sha256": None}
    record: dict[str, Any] = {"path": str(rendered_vtt), "sha256": None, "provenance": None, "render_sha256": None}
    if rendered_vtt.exists():
        record["sha256"] = m2_quality.sha256_file(rendered_vtt)
    provenance = Path(str(rendered_vtt) + ".provenance.json")
    if provenance.exists():
        try:
            data = json.loads(provenance.read_text(encoding="utf-8"))
            record["provenance"] = str(provenance)
            record["render_sha256"] = data.get("render_sha256")
        except (OSError, json.JSONDecodeError):
            pass
    return record


def make_template(
    edl_path: Path,
    render_path: Path,
    rendered_vtt: Path | None,
    evidence: list[dict[str, Any]],
    edl: dict[str, Any],
) -> dict[str, Any]:
    candidate = str(edl.get("candidate_id") or edl.get("slug") or edl_path.parent.name)
    bound_evidence: list[dict[str, Any]] = []
    for item in evidence:
        item = dict(item)
        item.setdefault("reviewed", False)
        item.setdefault("inspected", False)
        path = item.get("path")
        if path and Path(str(path)).expanduser().exists():
            try:
                item["sha256"] = m2_quality.sha256_file(path)
            except OSError:
                item["sha256"] = None
        else:
            item.setdefault("sha256", None)
        bound_evidence.append(item)
    source_vtt = edl.get("source_vtt") or edl.get("source_transcript_vtt")
    source_vtt_path = None
    if source_vtt:
        source_vtt_path = Path(str(source_vtt)).expanduser()
        if not source_vtt_path.is_absolute():
            probes = [edl_path.parent / source_vtt_path]
            cursor = edl_path.parent
            for _ in range(6):
                cursor = cursor.parent
                probes.append(cursor / source_vtt_path)
            source_vtt_path = next((item for item in probes if item.exists()), probes[0])
    source_vtt_record: dict[str, Any] = {"path": str(source_vtt) if source_vtt else None, "sha256": None}
    if source_vtt_path and source_vtt_path.exists():
        source_vtt_record["path"] = str(source_vtt_path.resolve())
        source_vtt_record["sha256"] = m2_quality.sha256_file(source_vtt_path)
    return {
        "schema": "method-2.review.v1",
        "status": "PENDING_REVIEW",
        "reviewed": False,
        "candidate": candidate,
        "render_path": str(render_path),
        "render_sha256": m2_quality.sha256_file(render_path),
        "edl_path": str(edl_path),
        "edl_sha256": m2_quality.sha256_file(edl_path),
        "rendered_vtt": _vtt_record(rendered_vtt),
        "source_metadata": m2_quality.source_visual_metadata(edl, reference=edl_path),
        "visual_review": {
            "status": "PENDING_REVIEW",
            "reviewed": False,
            "reviewer": None,
            "checks": {
                "payoff_visibility": _check_template("payoff_visibility"),
                "source_detail": _check_template("source_detail"),
                "cadence": _check_template("cadence"),
                "crop_continuity": _check_template("crop_continuity"),
                "subject_identity": _check_template("subject_identity"),
            },
            "source_metadata": m2_quality.source_visual_metadata(edl, reference=edl_path),
            "evidence": bound_evidence,
            "notes": "",
        },
        "semantic_review": {
            "status": "PENDING_REVIEW",
            "reviewed": False,
            "reviewer": None,
            "checks": {
                "meaning_preserved": _check_template("meaning_preserved"),
                "promise_truthful": _check_template("promise_truthful"),
                "speaker_attribution": _check_template("speaker_attribution"),
                "material_qualifiers": _check_template("material_qualifiers"),
                "payoff_completion": _check_template("payoff_completion"),
                "reorder_context": _check_template("reorder_context"),
            },
            "rendered_vtt": _vtt_record(rendered_vtt),
            "source_vtt_context": source_vtt_record,
            "context_excerpts": [],
            "notes": "",
        },
        "review_basis": {
            "frame_extraction_is_evidence_not_review": True,
            "required_visual_checks": ["payoff_visibility", "source_detail", "cadence", "crop_continuity", "subject_identity"],
            "required_semantic_checks": ["meaning_preserved", "promise_truthful", "speaker_attribution", "material_qualifiers", "payoff_completion", "reorder_context"],
            "required_semantic_reviewer": "clip_planner",
            "required_moving_evidence": True,
            "instructions": "A reviewer must watch the moving excerpts and complete every check; do not mark this record PASS from file presence alone.",
        },
    }


def write_template(args: argparse.Namespace) -> int:
    edl_path = Path(args.edl).expanduser().resolve()
    render_path = Path(args.render).expanduser().resolve()
    edl = json.loads(edl_path.read_text(encoding="utf-8"))
    candidate = str(edl.get("slug") or edl.get("candidate_id") or edl_path.parent.name)
    evidence_dir = Path(args.evidence_dir).expanduser().resolve() if args.evidence_dir else default_evidence_dir(edl_path, candidate)
    evidence = prepare_evidence(render_path, edl, evidence_dir)
    vtt = Path(args.vtt).expanduser().resolve() if args.vtt else None
    record = make_template(edl_path, render_path, vtt, evidence, edl)
    output = Path(args.output).expanduser().resolve() if args.output else edl_path.parent / "review.json"
    # A completed review is a retained record.  Never replace it merely
    # because a reviewer asks for a fresh template after a rerender; write a
    # hash-qualified pending record instead.
    if output.exists():
        try:
            existing = json.loads(output.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            existing = {}
        if existing.get("status") not in (None, "PENDING_REVIEW") or existing.get("render_sha256") != record.get("render_sha256"):
            output = output.with_name(f"{output.stem}.pending-{record['render_sha256'][:12]}{output.suffix}")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(record, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({"status": record["status"], "record": str(output), "evidence": len(evidence)}, indent=2))
    return 2


def validate(args: argparse.Namespace) -> int:
    record_path = Path(args.record).expanduser().resolve()
    edl_path = Path(args.edl).expanduser().resolve()
    edl = json.loads(edl_path.read_text(encoding="utf-8"))
    record = json.loads(record_path.read_text(encoding="utf-8"))
    result = m2_quality.validate_review_record(
        record, args.render, edl_path, args.vtt, edl=edl,
    )
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if result["passed"] else 2


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Create or validate a Method 2 visual/editorial review record")
    parser.add_argument("--validate", action="store_true", help="validate a completed external record")
    parser.add_argument("--record", help="completed review JSON for --validate")
    parser.add_argument("--edl", required=True)
    parser.add_argument("--render", required=True)
    parser.add_argument("--vtt", help="actual rendered-output VTT")
    parser.add_argument("--output", help="pending template path")
    parser.add_argument("--evidence-dir", help="temporary evidence directory")
    args = parser.parse_args(argv)
    if args.validate:
        if not args.record:
            parser.error("--record is required with --validate")
        return validate(args)
    return write_template(args)


if __name__ == "__main__":
    raise SystemExit(main())
