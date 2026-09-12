#!/usr/bin/env python3
"""Method 2 range acquisition and independent source alignment verification.

Only candidates already present in the approved ``plan.json`` are acquired.
The range audio cut from ``full_audio`` is the assembly source, but it is not
accepted as proof that the downloaded video is at the planned timecode.  The
actual video is verified through native video audio when that stream exists,
or through early/middle/late visual anchors against a trusted full proxy when
the downloaded video is video-only.

The command writes ``qa/range-acquisition.json`` and exits nonzero for an
empty plan, failed verification, low-confidence pending review, or stale
existing media.  It never overwrites existing range media.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Mapping

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from m2_alignment import (  # noqa: E402
    CONF_FLOOR,  # noqa: F401 - compatibility export for Friends adapter
    HZ,  # noqa: F401 - compatibility export for Friends adapter
    TOL,  # noqa: F401 - compatibility export for Friends adapter
    env_from_raw,  # noqa: F401 - compatibility export for Friends adapter
    env_of,
    find_offset,  # noqa: F401 - compatibility export for Friends adapter
    media_duration,
    resolve_low_confidence_audio,
    sha256_file,
    verify_audio_arrays,
    verify_candidate_video,
    verify_proxy_alignment,
)


REPORT_SCHEMA = "method-2.range-acquisition.v2"
META_SCHEMA = "method-2.range-media.v1"


def run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, check=True, **kwargs)


def hms(value: Any) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value)
    parts = text.split(":")
    if len(parts) != 3:
        raise ValueError(f"invalid timecode: {value!r}")
    hours, minutes, seconds = parts
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)


def _resolve(path: str | os.PathLike[str], project: str | os.PathLike[str] | None = None) -> Path:
    candidate = Path(path).expanduser()
    if candidate.is_absolute():
        return candidate.resolve()
    if candidate.exists():
        return candidate.resolve()
    if project:
        under_project = Path(project) / candidate
        if under_project.exists():
            return under_project.resolve()
    return candidate.resolve()


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _source_duration(state: Mapping[str, Any], project: Path) -> float:
    source = state.get("source", {})
    value = source.get("duration_seconds")
    if value is not None:
        try:
            parsed = float(value)
            if parsed > 0:
                return parsed
        except (TypeError, ValueError):
            pass
    audio = source.get("full_audio")
    if audio:
        duration = media_duration(str(_resolve(audio, project)))
        if duration and duration > 0:
            return duration
    proxy = source.get("proxy")
    if proxy:
        duration = media_duration(str(_resolve(proxy, project)))
        if duration and duration > 0:
            return duration
    return 0.0


def _source_fingerprint(state: Mapping[str, Any], project: Path) -> dict[str, Any]:
    source = state.get("source", {})
    result: dict[str, Any] = {
        "id": source.get("id") or source.get("youtube_id") or source.get("video_id"),
        "url": source.get("url"),
        "master": source.get("master"),
    }
    for key in ("full_audio", "proxy", "master"):
        raw = source.get(key)
        if not raw:
            continue
        path = _resolve(str(raw), project)
        result[f"{key}_path"] = str(path)
        if path.exists() and path.is_file():
            result[f"{key}_sha256"] = sha256_file(path)
            result[f"{key}_bytes"] = path.stat().st_size
    return result


def _candidate_bounds(
    candidate: Mapping[str, Any], handles: float, source_duration: float
) -> tuple[dict[str, Any] | None, str | None]:
    try:
        source_in = hms(candidate["source_in"])
        source_out = hms(candidate["source_out"])
    except (KeyError, TypeError, ValueError) as exc:
        return None, f"invalid approved source range: {exc}"
    if source_in < 0 or source_out <= source_in:
        return None, "invalid approved source range ordering"
    if source_duration <= 0:
        return None, "source duration is unavailable"
    if source_in >= source_duration or source_out > source_duration + 1e-3:
        return None, (
            f"approved source range {source_in:.3f}-{source_out:.3f}s exceeds "
            f"source duration {source_duration:.3f}s"
        )
    handled_start = max(0.0, source_in - handles)
    handled_end = min(source_duration, source_out + handles)
    if handled_end <= handled_start:
        return None, "clamped handled range is empty"
    return {
        "source_in_seconds": round(source_in, 3),
        "source_out_seconds": round(source_out, 3),
        "handled_start": round(handled_start, 3),
        "handled_end": round(handled_end, 3),
        "requested_handles_seconds": round(float(handles), 3),
        "actual_handle_before_seconds": round(source_in - handled_start, 3),
        "actual_handle_after_seconds": round(handled_end - source_out, 3),
        "media_in_seconds": round(source_in - handled_start, 3),
        "range_duration_seconds": round(handled_end - handled_start, 3),
    }, None


def _range_metadata_path(out_dir: Path, slug: str) -> Path:
    return out_dir / f"{slug}.range.json"


def _read_metadata(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _float_equal(left: Any, right: Any, tolerance: float = 1e-3) -> bool:
    try:
        return abs(float(left) - float(right)) <= tolerance
    except (TypeError, ValueError):
        return left == right


def _metadata_matches(
    metadata: Mapping[str, Any] | None,
    expected: Mapping[str, Any],
    *,
    include_media_hashes: bool = False,
) -> bool:
    if not isinstance(metadata, Mapping) or metadata.get("schema") != META_SCHEMA:
        return False
    for key in (
        "candidate_id",
        "candidate_slug",
        "source_in_seconds",
        "source_out_seconds",
        "handled_start",
        "handled_end",
        "requested_handles_seconds",
    ):
        if key not in metadata or key not in expected:
            return False
        if key.endswith("seconds") or key in {"handled_start", "handled_end"}:
            if not _float_equal(metadata[key], expected[key]):
                return False
        elif metadata[key] != expected[key]:
            return False
    if metadata.get("source_fingerprint") != expected.get("source_fingerprint"):
        return False
    if include_media_hashes:
        for key in ("video_sha256", "audio_sha256"):
            if not metadata.get(key) or metadata.get(key) != expected.get(key):
                return False
    return True


def _expected_metadata(
    candidate: Mapping[str, Any], bounds: Mapping[str, Any], source_fingerprint: Mapping[str, Any]
) -> dict[str, Any]:
    return {
        "schema": META_SCHEMA,
        "candidate_id": candidate.get("id"),
        "candidate_slug": candidate.get("slug"),
        "source_in_seconds": bounds["source_in_seconds"],
        "source_out_seconds": bounds["source_out_seconds"],
        "handled_start": bounds["handled_start"],
        "handled_end": bounds["handled_end"],
        "requested_handles_seconds": bounds["requested_handles_seconds"],
        "source_fingerprint": dict(source_fingerprint),
    }


def _mark_paths(candidate: dict[str, Any], video: Path, audio: Path, bounds: Mapping[str, Any]) -> None:
    candidate.update(
        {
            "video_file": str(video.resolve()),
            "audio_file": str(audio.resolve()),
            "source_in_seconds": bounds["source_in_seconds"],
            "source_out_seconds": bounds["source_out_seconds"],
            "handled_start": bounds["handled_start"],
            "handled_end": bounds["handled_end"],
            "media_in_seconds": bounds["media_in_seconds"],
            "handles_seconds": bounds["requested_handles_seconds"],
            "actual_handle_before_seconds": bounds["actual_handle_before_seconds"],
            "actual_handle_after_seconds": bounds["actual_handle_after_seconds"],
        }
    )


def fetch(
    state: Mapping[str, Any],
    candidate: dict[str, Any],
    out_dir: str | os.PathLike[str],
    handles: float,
    *,
    source_duration: float | None = None,
    source_fingerprint: Mapping[str, Any] | None = None,
    project: str | os.PathLike[str] | None = None,
    validate_existing: bool = False,
) -> dict[str, Any]:
    """Acquire one approved range without replacing existing media.

    Existing media without matching range/source metadata is deliberately
    returned as ``_range_issue``.  ``validate_existing`` is an explicit caller
    choice to run the new independent verification against that media.
    """

    project_path = Path(project).resolve() if project else Path.cwd()
    source = state.get("source", {})
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    slug = str(candidate.get("slug", ""))
    if not slug:
        candidate["_range_issue"] = "approved candidate is missing slug"
        return candidate
    if source_duration is None:
        source_duration = _source_duration(state, project_path)
    bounds, issue = _candidate_bounds(candidate, handles, source_duration)
    if issue or bounds is None:
        candidate["_range_issue"] = issue or "invalid range"
        return candidate
    if source_fingerprint is None:
        source_fingerprint = _source_fingerprint(state, project_path)

    video = out / f"{slug}.mp4"
    audio = out / f"{slug}-audio.wav"
    metadata_path = _range_metadata_path(out, slug)
    expected = _expected_metadata(candidate, bounds, source_fingerprint)
    metadata = _read_metadata(metadata_path)
    any_existing = video.exists() or audio.exists()
    metadata_matches = _metadata_matches(metadata, expected)
    if any_existing and not metadata_matches and not validate_existing:
        _mark_paths(candidate, video, audio, bounds)
        candidate["_range_issue"] = (
            "existing range media lacks matching source/range metadata; "
            "rerun with --validate-existing for explicit independent validation"
        )
        candidate["_range_issue_code"] = "STALE_OR_UNVERIFIED_EXISTING_RANGE"
        return candidate

    start = float(bounds["handled_start"])
    end = float(bounds["handled_end"])
    duration = end - start
    if not video.exists():
        mode = source.get("mode")
        if mode == "file":
            master = source.get("master")
            if not master:
                candidate["_range_issue"] = "file source has no master path"
                return candidate
            run(
                [
                    "ffmpeg",
                    "-hide_banner",
                    "-v",
                    "error",
                    "-ss",
                    f"{start:.3f}",
                    "-i",
                    str(_resolve(master, project_path)),
                    "-t",
                    f"{duration:.3f}",
                    "-an",
                    "-c:v",
                    "copy",
                    str(video),
                    "-y",
                ]
            )
        else:
            url = source.get("url")
            if not url:
                candidate["_range_issue"] = "URL source has no URL"
                return candidate
            # Do not cap the default at 1080p.  A configured source-specific
            # format still wins because it records the playable format already
            # selected for this source.
            fmt = source.get("ytdlp_format") or "bestvideo+bestaudio/best"
            extra = source.get("ytdlp_args") or []
            run(
                [
                    "yt-dlp",
                    "-f",
                    str(fmt),
                    *[str(value) for value in extra],
                    "--download-sections",
                    f"*{start:.2f}-{end:.2f}",
                    "--force-keyframes-at-cuts",
                    "--merge-output-format",
                    "mp4",
                    "-o",
                    str(video),
                    "--no-warnings",
                    str(url),
                ]
            )
    # This replacement audio is for assembly only.  It is never used as proof
    # that the video file is at the planned source time.
    if not audio.exists():
        full_audio = source.get("full_audio")
        if not full_audio:
            candidate["_range_issue"] = "source has no full audio for range assembly"
            return candidate
        run(
            [
                "ffmpeg",
                "-hide_banner",
                "-v",
                "error",
                "-ss",
                f"{start:.3f}",
                "-i",
                str(_resolve(full_audio, project_path)),
                "-t",
                f"{duration:.3f}",
                "-c:a",
                "pcm_s24le",
                "-ar",
                "48000",
                "-ac",
                "2",
                str(audio),
                "-y",
            ]
        )

    _mark_paths(candidate, video, audio, bounds)
    if not video.exists() or not audio.exists():
        candidate["_range_issue"] = "range acquisition did not produce both media files"
        return candidate
    candidate["_range_expected_metadata"] = expected
    candidate["_range_metadata_path"] = str(metadata_path.resolve())
    return candidate


def _media_hashes(candidate: Mapping[str, Any]) -> tuple[str | None, str | None]:
    video = candidate.get("video_file")
    audio = candidate.get("audio_file")
    return (
        sha256_file(str(video)) if video and os.path.isfile(str(video)) else None,
        sha256_file(str(audio)) if audio and os.path.isfile(str(audio)) else None,
    )


def _write_range_metadata(candidate: Mapping[str, Any], verification: Mapping[str, Any]) -> None:
    metadata_path = candidate.get("_range_metadata_path")
    expected = candidate.get("_range_expected_metadata")
    if not metadata_path or not isinstance(expected, Mapping):
        return
    video_hash, audio_hash = _media_hashes(candidate)
    if not video_hash or not audio_hash:
        return
    value = dict(expected)
    value.update(
        {
            "video_file": str(candidate.get("video_file")),
            "audio_file": str(candidate.get("audio_file")),
            "video_sha256": video_hash,
            "audio_sha256": audio_hash,
            "verification_status": verification.get("status"),
            "verification_method": verification.get("method"),
        }
    )
    _write_json(Path(str(metadata_path)), value)


def _candidate_failure(candidate: Mapping[str, Any], reason: str, code: str | None = None) -> dict[str, Any]:
    result: dict[str, Any] = {
        "id": candidate.get("id"),
        "slug": candidate.get("slug"),
        "name": candidate.get("name"),
        "source_in": candidate.get("source_in"),
        "source_out": candidate.get("source_out"),
        "status": "FAIL",
        "verification": {"status": "FAIL", "reason": reason, "independent": False},
    }
    if code:
        result["code"] = code
    for key in ("video_file", "audio_file", "handled_start", "handled_end"):
        if key in candidate:
            result[key] = candidate[key]
    video_hash, audio_hash = _media_hashes(candidate)
    if video_hash:
        result["video_sha256"] = video_hash
    if audio_hash:
        result["audio_sha256"] = audio_hash
    return result


def _is_approved_candidate(candidate: Mapping[str, Any]) -> bool:
    """Return whether the planner explicitly authorized this candidate.

    Older plans used the presence of a candidate in ``plan.json`` as the
    approval marker, so a missing status remains compatible.  Any explicit
    status outside the planner's approved values is skipped and never fetched.
    """

    status = candidate.get("status")
    if status is None:
        return True
    return str(status).lower() in {
        "approved",
        "approved_for_execution",
        "approved_for_delivery",
    }


def process_project(
    project: str | os.PathLike[str],
    *,
    handles: float = 30.0,
    validate_existing: bool = False,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Run acquisition and verification, returning report, plan, and state."""

    project_path = Path(project).resolve()
    state_path = project_path / "m2.json"
    plan_path = project_path / "plan.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    candidates = plan.get("candidates")
    report: dict[str, Any] = {
        "schema": REPORT_SCHEMA,
        "status": "EMPTY",
        "project": str(project_path),
        "plan": str(plan_path),
        "handles_seconds": round(float(handles), 3),
        "source_duration_seconds": None,
        "source_fingerprint": {},
        "proxy_alignment": None,
        "candidates": [],
        "skipped_unapproved": [],
        "approved_candidate_count": 0,
    }
    if not isinstance(candidates, list) or not candidates:
        report["reason"] = "approved plan has no candidates"
        state["stage"] = "ranges_failed"
        state["range_acquisition"] = {"status": report["status"], "report": str((project_path / "qa" / "range-acquisition.json").resolve())}
        _write_json(project_path / "qa" / "range-acquisition.json", report)
        _write_json(state_path, state)
        return report, plan, state

    approved_candidates = []
    for item in candidates:
        if isinstance(item, Mapping) and _is_approved_candidate(item):
            approved_candidates.append(item)
        elif isinstance(item, Mapping):
            report["skipped_unapproved"].append(
                {"id": item.get("id"), "slug": item.get("slug"), "status": item.get("status")}
            )
    if not approved_candidates:
        report["status"] = "EMPTY"
        report["reason"] = "approved plan contains no approved candidates"
        state["stage"] = "ranges_failed"
        report_path = project_path / "qa" / "range-acquisition.json"
        report["report"] = str(report_path.resolve())
        state["range_acquisition"] = {"status": report["status"], "report": str(report_path.resolve())}
        _write_json(report_path, report)
        _write_json(state_path, state)
        return report, plan, state

    source_duration = _source_duration(state, project_path)
    source_fingerprint = _source_fingerprint(state, project_path)
    report["source_duration_seconds"] = round(source_duration, 3)
    report["source_fingerprint"] = source_fingerprint
    report["source_identity"] = source_fingerprint
    report["approved_candidate_count"] = len(approved_candidates)
    verification_state = dict(state)
    verification_source = dict(state.get("source", {}))
    for key in ("full_audio", "proxy", "master"):
        if verification_source.get(key):
            verification_source[key] = str(_resolve(verification_source[key], project_path))
    verification_state["source"] = verification_source
    verification_state["_project"] = str(project_path)
    # This check is used only for video-only ranges. Native range audio has its
    # own independent evidence path and does not depend on proxy audio.
    proxy_alignment = verify_proxy_alignment(verification_state)
    report["proxy_alignment"] = proxy_alignment
    full_env: np.ndarray | None = None
    out_dir = project_path / "source" / "ranges"
    out_dir.mkdir(parents=True, exist_ok=True)

    for original in approved_candidates:
        if not isinstance(original, dict):
            report["candidates"].append(_candidate_failure({}, "candidate record is not an object", "INVALID_CANDIDATE"))
            continue
        candidate = original
        candidate["source_identity"] = source_fingerprint
        slug = candidate.get("slug")
        if not slug or "source_in" not in candidate or "source_out" not in candidate:
            result = _candidate_failure(candidate, "approved candidate is missing slug or source boundaries", "INVALID_CANDIDATE")
            report["candidates"].append(result)
            continue
        try:
            fetch(
                state,
                candidate,
                out_dir,
                handles,
                source_duration=source_duration,
                source_fingerprint=source_fingerprint,
                project=project_path,
                validate_existing=validate_existing,
            )
        except (OSError, RuntimeError, subprocess.CalledProcessError) as exc:
            candidate["_range_issue"] = f"range acquisition failed: {exc}"
        if candidate.get("_range_issue"):
            result = _candidate_failure(candidate, str(candidate["_range_issue"]), candidate.get("_range_issue_code"))
            report["candidates"].append(result)
            continue
        video = candidate.get("video_file")
        audio = candidate.get("audio_file")
        if not video or not audio or not os.path.isfile(str(video)) or not os.path.isfile(str(audio)):
            result = _candidate_failure(candidate, "approved range media is missing", "MISSING_MEDIA")
            report["candidates"].append(result)
            continue
        try:
            # Validate the replacement WAV independently for assembly.  It is
            # deliberately kept separate from the video proof below: a WAV
            # cut from full_audio cannot prove that downloaded picture is at
            # the same source time.
            full_audio_path = verification_source.get("full_audio")
            if full_env is None and full_audio_path:
                full_env = env_of(str(full_audio_path))
            if full_env is None:
                assembly_audio = {
                    "status": "PENDING_REVIEW",
                    "reason": "full source audio unavailable for replacement-audio verification",
                    "method": "replacement_audio_against_full_source",
                    "independent": True,
                    "usable_for_video_alignment": False,
                }
            else:
                range_audio_env = env_of(str(audio))
                assembly_audio = verify_audio_arrays(
                    full_env,
                    range_audio_env,
                    float(candidate["handled_start"]),
                    duration_seconds=float(candidate["handled_end"]) - float(candidate["handled_start"]),
                )
                assembly_audio["method"] = "replacement_audio_against_full_source"
                assembly_audio["usable_for_video_alignment"] = False
                assembly_audio = resolve_low_confidence_audio(
                    assembly_audio,
                    candidate,
                    current_video_sha256=sha256_file(str(video)),
                    current_source_audio_sha256=(
                        sha256_file(str(full_audio_path)) if full_audio_path and os.path.isfile(str(full_audio_path)) else None
                    ),
                    current_assembly_audio_sha256=sha256_file(str(audio)),
                    base_dir=project_path,
                )
            video_verification = verify_candidate_video(
                candidate,
                verification_state,
                full_audio_env=full_env,
                proxy_alignment=proxy_alignment,
            )
        except Exception as exc:
            assembly_audio = {
                "status": "PENDING_REVIEW",
                "reason": f"replacement-audio verification failed to run: {exc}",
                "method": "replacement_audio_against_full_source",
                "independent": True,
                "usable_for_video_alignment": False,
            }
            video_verification = {
                "status": "PENDING_REVIEW",
                "reason": f"video verification failed to run: {exc}",
                "method": "independent_video_alignment",
                "independent": False,
            }
        video_hash, audio_hash = _media_hashes(candidate)
        video_status = str(video_verification.get("status", "PENDING_REVIEW")).upper()
        audio_status = str(assembly_audio.get("status", "PENDING_REVIEW")).upper()
        if "FAIL" in {video_status, audio_status}:
            status = "FAIL"
        elif "PENDING_REVIEW" in {video_status, audio_status}:
            status = "PENDING_REVIEW"
        else:
            status = "PASS"
        verification = dict(video_verification)
        verification["status"] = status
        verification["video_verification"] = video_verification
        verification["assembly_audio_verification"] = assembly_audio
        verification["reason"] = (
            video_verification.get("reason")
            if video_status != "PASS"
            else assembly_audio.get("reason")
            if audio_status != "PASS"
            else "video and replacement audio independently verified"
        )
        result = {
            "id": candidate.get("id"),
            "slug": slug,
            "name": candidate.get("name"),
            "source_in": candidate.get("source_in"),
            "source_out": candidate.get("source_out"),
            "source_in_seconds": candidate.get("source_in_seconds"),
            "source_out_seconds": candidate.get("source_out_seconds"),
            "handled_start": candidate.get("handled_start"),
            "handled_end": candidate.get("handled_end"),
            "handles_seconds": candidate.get("handles_seconds"),
            "actual_handle_before_seconds": candidate.get("actual_handle_before_seconds"),
            "actual_handle_after_seconds": candidate.get("actual_handle_after_seconds"),
            "video_file": str(Path(str(video)).resolve()),
            "audio_file": str(Path(str(audio)).resolve()),
            "video_sha256": video_hash,
            "audio_sha256": audio_hash,
            "status": status,
            "verification": verification,
            "alignment_report": None,
        }
        candidate["range_video_alignment"] = {
            "candidate_slug": slug,
            "status": status,
            "method": verification.get("method"),
            "video_file": result["video_file"],
            "audio_file": result["audio_file"],
            "video_sha256": video_hash,
            "audio_sha256": audio_hash,
            "handled_start": candidate.get("handled_start"),
            "handled_end": candidate.get("handled_end"),
        }
        candidate["alignment"] = verification
        candidate["range_verification_status"] = status
        report["candidates"].append(result)
        if status == "PASS":
            _write_range_metadata(candidate, verification)

    statuses = [str(item.get("status", "FAIL")).upper() for item in report["candidates"]]
    if not statuses:
        report["status"] = "EMPTY"
    elif any(status == "FAIL" for status in statuses):
        report["status"] = "FAIL"
    elif any(status == "PENDING_REVIEW" for status in statuses):
        report["status"] = "REVIEW_REQUIRED"
    elif all(status == "PASS" for status in statuses):
        report["status"] = "PASS"
    else:
        report["status"] = "FAIL"
    report_path = project_path / "qa" / "range-acquisition.json"
    report["report"] = str(report_path.resolve())
    for item in report["candidates"]:
        item["alignment_report"] = str(report_path.resolve())
    # Carry the report path and candidate result into the approved plan without
    # introducing, renaming, or selecting candidates.
    by_slug = {str(item.get("slug")): item for item in report["candidates"]}
    for candidate in approved_candidates:
        if isinstance(candidate, dict) and str(candidate.get("slug")) in by_slug:
            item = by_slug[str(candidate.get("slug"))]
            candidate["alignment_report"] = str(report_path.resolve())
            candidate["alignment_report_candidate_slug"] = candidate.get("slug")
            candidate["alignment_report_status"] = item.get("status")
            candidate["video_sha256"] = item.get("video_sha256")
            candidate["audio_sha256"] = item.get("audio_sha256")

    if report["status"] == "PASS":
        state["stage"] = "ranges_verified"
    elif report["status"] == "REVIEW_REQUIRED":
        state["stage"] = "ranges_pending_review"
    else:
        state["stage"] = "ranges_failed"
    state["range_acquisition"] = {
        "status": report["status"],
        "handles_seconds": round(float(handles), 3),
        "report": str(report_path.resolve()),
        "source_fingerprint": source_fingerprint,
    }
    _write_json(report_path, report)
    _write_json(plan_path, plan)
    _write_json(state_path, state)
    return report, plan, state


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", required=True)
    parser.add_argument("--handles", type=float, default=30.0)
    parser.add_argument(
        "--validate-existing",
        action="store_true",
        help="explicitly validate existing range files whose metadata is absent or stale; never overwrite them",
    )
    args = parser.parse_args()
    try:
        report, _, _ = process_project(
            args.project,
            handles=args.handles,
            validate_existing=args.validate_existing,
        )
    except (OSError, json.JSONDecodeError, KeyError, ValueError) as exc:
        print(f"range acquisition failed: {exc}", file=sys.stderr)
        return 1
    for item in report.get("candidates", []):
        verification = item.get("verification", {})
        print(
            f"  {item.get('slug', '<missing>')}: {item.get('status')} "
            f"{verification.get('method', '')} {verification.get('reason', '')}"
        )
    print(f"\nrange report: {report.get('status')} -> {report.get('report')}")
    return 0 if report.get("status") == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
