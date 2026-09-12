#!/usr/bin/env python3
"""Shared Method 2 quality evidence and delivery gates.

This module contains the small, deterministic parts of the Method 2 quality
contract.  It deliberately keeps media extraction separate from review:
frames and short moving excerpts are evidence for a reviewer, never a review
verdict by themselves.

The public helpers are intentionally usable by the project-local adapters as
well as the shared renderer:

* :func:`parse_freezedetect` handles an EOF ``freeze_start`` correctly;
* :func:`audio_content_gate` checks rendered-output VTT content overlap and
  labels it as an audio-content screen, never semantic proof;
* :func:`alignment_gate` validates candidate-specific range evidence against
  the actual EDL assets; and
* :func:`validate_review_record` fail-closes a completed visual/editorial
  review unless its hashes, checks, notes, and rendered VTT evidence match.

The alignment report interface is deliberately small and backwards-tolerant.
The preferred report shape is::

    {
      "schema": "method-2.range-acquisition.v3",
      "status": "PASS",
      "source_identity": {"url": "...", "id": "..."},
      "candidates": [{
        "slug": "candidate-slug", "status": "PASS",
        "asset_fingerprints": {
          "video": {"path": "...", "sha256": "...", "size": 1,
                    "mtime_ns": 2},
          "audio": {"path": "...", "sha256": "...", "size": 1,
                    "mtime_ns": 2}
        }
      }]
    }

The range agent may add an ``alignment_report`` path to the EDL.  That path
is preferred over guessing a project-level report.

The current range writer calls its source identity ``source_fingerprint``.
The gate accepts that name as an alias at both report and candidate level.
EDLs may bind it with ``source_identity`` or ``source_fingerprint`` (or the
small ``source_id``/``source_url``/``source_title`` mapping); an EDL without
an identity mapping still requires a non-empty identity from the report.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
from fractions import Fraction
from pathlib import Path
import tempfile
import shutil
from typing import Any, Iterable
from runtime import whisper_command, whisper_model


STOP_WORDS = {
    "the", "a", "an", "and", "is", "was", "of", "to", "it", "in", "on",
    "that", "this", "for", "you", "i", "he", "she", "they", "we", "so",
    "at", "as",
}
RENDERED_VTT_NAME = "rendered.vtt"
TAIL_TOLERANCE_SECONDS = 0.15
CONTENT_OVERLAP_THRESHOLD = 0.25
WHISPER = whisper_command()
WHISPER_MODEL = whisper_model()
OUT_W, OUT_H = 1080, 1920


def _path(value: str | os.PathLike[str]) -> Path:
    return Path(value).expanduser().resolve()


def sha256_file(path: str | os.PathLike[str]) -> str:
    """Return the SHA-256 of a file without loading it all into memory."""

    digest = hashlib.sha256()
    with _path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def file_fingerprint(path: str | os.PathLike[str], *, include_sha256: bool = True) -> dict[str, Any]:
    """Capture the identity used by render caches and review records.

    ``size`` and ``mtime_ns`` are cheap enough for every cache lookup.  A
    SHA-256 is included by default for durable review and alignment evidence;
    callers doing high-frequency draft lookups can request the cheap pair.
    """

    resolved = _path(path)
    stat = resolved.stat()
    result: dict[str, Any] = {
        "path": str(resolved),
        "size": int(stat.st_size),
        "mtime_ns": int(stat.st_mtime_ns),
    }
    if include_sha256:
        result["sha256"] = sha256_file(resolved)
    return result


def fingerprint_matches(actual: dict[str, Any], expected: dict[str, Any]) -> bool:
    """Compare all identity fields present in ``expected``.

    This allows a range verifier to retain the cheap fields while still
    accepting a full SHA-256 when one was recorded.  A path, when supplied,
    is compared after resolving it so relative and absolute records agree.
    """

    for key in ("size", "mtime_ns", "sha256"):
        if key in expected and str(actual.get(key)) != str(expected.get(key)):
            return False
    if expected.get("path"):
        try:
            if _path(actual.get("path", "")) != _path(expected["path"]):
                return False
        except (OSError, TypeError, ValueError):
            return False
    return True


def content_tokens(text: str) -> set[str]:
    """Return content words for the audio-content overlap screen."""

    return {
        token for token in re.sub(r"[^a-z' ]", " ", text.lower()).split()
        if len(token) > 2 and token not in STOP_WORDS
    }


def vtt_text(path: str | os.PathLike[str]) -> str:
    """Read cue payloads from VTT while ignoring headers and timing lines."""

    payload: list[str] = []
    for raw in _path(path).read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith(("WEBVTT", "NOTE", "STYLE", "REGION")):
            continue
        if "-->" in line or re.fullmatch(r"\d+", line):
            continue
        payload.append(line)
    return " ".join(payload)


def rendered_vtt_path(
    clip: str | os.PathLike[str],
    project: str | os.PathLike[str] | None = None,
    candidate: str | None = None,
) -> Path:
    """Return the one deterministic rendered-output VTT location."""

    clip_path = _path(clip)
    if project:
        root = _path(project)
        slug = candidate or clip_path.parent.name
        return root / "_cache" / root.name / "qa" / slug / "rendered-transcript-vtt" / RENDERED_VTT_NAME
    return clip_path.parent / "gate_verify" / "rendered-transcript-vtt" / RENDERED_VTT_NAME


def transcribe_rendered_vtt(
    clip: str | os.PathLike[str],
    output: str | os.PathLike[str] | None = None,
    *,
    whisper: str = WHISPER,
    model: str = WHISPER_MODEL,
    language: str | None = "en",
) -> tuple[Path, dict[str, Any]]:
    """Transcribe actual rendered bytes; never relabel an old transcript.

    VTT is the sole transcript. A small JSON provenance record binds the render,
    VTT bytes and decoder settings. Fresh decoding happens in an empty directory
    so a successful process that wrote nothing cannot validate stale output.
    """
    target = _path(output) if output else rendered_vtt_path(clip)
    target.parent.mkdir(parents=True, exist_ok=True)
    provenance_path = Path(str(target) + ".provenance.json")
    clip_hash = sha256_file(clip)
    language = None if language == "auto" else language
    try:
        provenance = json.loads(provenance_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        provenance = {}
    reusable = (target.exists() and provenance.get("render_sha256") == clip_hash
                and provenance.get("vtt_sha256") == sha256_file(target)
                and provenance.get("model") == model and provenance.get("language") == language)
    if reusable:
        return target, {"status": "reused", "format": "vtt", "path": str(target),
                        "render_sha256": clip_hash, "provenance": str(provenance_path)}
    with tempfile.TemporaryDirectory(prefix="rendered-asr-", dir=target.parent) as scratch:
        fresh = Path(scratch) / "rendered.vtt"
        language_args = ["--language", language] if language else []
        command = [whisper, str(_path(clip)), "--model", model, *language_args,
                   "--output-format", "vtt", "--output-name", "rendered",
                   "--verbose", "False", "--output-dir", scratch]
        result = subprocess.run(command, capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError((result.stderr or result.stdout).strip()[-2000:])
        if not fresh.is_file() or not fresh.read_text(encoding="utf-8").lstrip("\ufeff").startswith("WEBVTT"):
            raise RuntimeError("rendered-output transcription did not produce the expected fresh VTT")
        shutil.copyfile(fresh, target)
    provenance_payload = {
        "schema": "method-2.rendered-vtt-provenance.v1",
        "render": str(_path(clip)), "render_sha256": clip_hash,
        "vtt": str(target), "vtt_sha256": sha256_file(target), "format": "vtt",
        "engine": "friends_transcribe_compat", "model": model, "language": language,
    }
    provenance_path.write_text(json.dumps(provenance_payload, indent=2) + "\n", encoding="utf-8")
    return target, {"status": "created", "format": "vtt", "path": str(target),
                    "render_sha256": clip_hash, "provenance": str(provenance_path)}


def write_nonverbal_vtt(
    clip: str | os.PathLike[str],
    output: str | os.PathLike[str] | None = None,
) -> tuple[Path, dict[str, Any]]:
    """Write a deterministic no-speech VTT placeholder without running ASR."""

    target = _path(output) if output else rendered_vtt_path(clip)
    target.parent.mkdir(parents=True, exist_ok=True)
    render_hash = sha256_file(clip)
    target.write_text(
        "WEBVTT\n\n"
        "NOTE Method 2 non-verbal render; no speech ASR requested.\n"
        f"NOTE render_sha256={render_hash}\n",
        encoding="utf-8",
    )
    provenance_path = Path(str(target) + ".provenance.json")
    provenance_path.write_text(json.dumps({
        "schema": "method-2.rendered-vtt-provenance.v1",
        "render": str(_path(clip)),
        "render_sha256": render_hash,
        "vtt": str(target),
        "vtt_sha256": sha256_file(target),
        "format": "vtt",
        "status": "not_applicable_nonverbal",
    }, indent=2) + "\n", encoding="utf-8")
    return target, {
        "status": "not_applicable_nonverbal",
        "format": "vtt",
        "path": str(target),
        "render_sha256": render_hash,
        "provenance": str(provenance_path),
    }


def _planned_text(edl: dict[str, Any]) -> str:
    return " ".join(
        str(beat.get("text", ""))
        for beat in edl.get("beats", [])
        if not beat.get("non_verbal")
    )


def audio_content_gate(
    edl: dict[str, Any],
    rendered_vtt: str | os.PathLike[str] | None,
    *,
    threshold: float = CONTENT_OVERLAP_THRESHOLD,
) -> dict[str, Any]:
    """Check rendered-output VTT content overlap.

    The result is deliberately named ``audio_content`` and includes an
    explicit ``semantic_proof: false`` field.  Word overlap can catch a wrong
    or displaced rendered audio track; it cannot prove preserved meaning,
    attribution, promise, or context.  An all-nonverbal EDL is ``N/A`` and
    therefore still requires the visual/editorial review gate.
    """

    planned = content_tokens(_planned_text(edl))
    nonverbal_beats = [
        beat.get("id", index + 1)
        for index, beat in enumerate(edl.get("beats", []))
        if beat.get("non_verbal")
    ]
    base: dict[str, Any] = {
        "name": "audio-content overlap",
        "check": "audio_content_overlap",
        "semantic_proof": False,
        "interpretation": (
            "Rendered-output content-word overlap screens audio content only; "
            "it is not semantic, promise, attribution, or visual proof."
        ),
        "threshold": threshold,
        "non_verbal_beats": nonverbal_beats,
    }
    if not planned:
        return {
            **base,
            "status": "not_applicable",
            "passed": None,
            "required_visual_review": True,
            "reason": "no planned speech content; visual/editorial review is mandatory",
            "planned_content_words": [],
            "rendered_content_words": [],
        }
    if rendered_vtt is None or not _path(rendered_vtt).exists():
        return {
            **base,
            "status": "missing_rendered_vtt",
            "passed": False,
            "required_visual_review": True,
            "reason": "actual rendered-output VTT is required",
            "planned_content_words": sorted(planned),
            "rendered_content_words": [],
        }
    rendered = content_tokens(vtt_text(rendered_vtt))
    overlap = len(planned & rendered) / max(len(planned), 1)
    return {
        **base,
        "status": "checked",
        "passed": overlap >= threshold,
        "required_visual_review": True,
        "content_word_overlap": overlap,
        "planned_content_words": sorted(planned),
        "rendered_content_words": sorted(rendered),
        "rendered_vtt": str(_path(rendered_vtt)),
        "rendered_vtt_sha256": sha256_file(rendered_vtt),
    }


def parse_freezedetect(
    log: str,
    duration: float,
    tail_tolerance: float = TAIL_TOLERANCE_SECONDS,
) -> dict[str, Any]:
    """Parse ordered ``freezedetect`` events, including an open EOF freeze.

    FFmpeg can emit ``freeze_duration`` without a matching ``freeze_end``
    when the input ends while a freeze is active.  Pairing all starts and ends
    by list length therefore produces a false pass.  This parser follows event
    order and retains an unmatched open span.
    """

    event_re = re.compile(
        r"freeze_(start|duration|end)\s*:\s*"
        r"([-+]?(?:\d+(?:\.\d*)?|\.\d+))"
    )
    spans: list[dict[str, Any]] = []
    open_span: dict[str, Any] | None = None
    for match in event_re.finditer(log):
        kind, raw = match.group(1), match.group(2)
        value = float(raw)
        if kind == "start":
            if open_span is not None:
                spans.append(open_span)
            open_span = {"start": value, "duration": None, "end": None, "open": True}
        elif kind == "duration":
            if open_span is not None:
                open_span["duration"] = value
        elif kind == "end":
            if open_span is None:
                spans.append({"start": None, "duration": None, "end": value, "open": False})
            else:
                open_span["end"] = value
                if open_span.get("duration") is None and open_span.get("start") is not None:
                    open_span["duration"] = max(0.0, value - float(open_span["start"]))
                open_span["open"] = False
                spans.append(open_span)
                open_span = None
    if open_span is not None:
        spans.append(open_span)

    tail_failures: list[dict[str, Any]] = []
    for span in spans:
        end = span.get("end")
        if span.get("open"):
            tail_failures.append({"reason": "unmatched_freeze_start", **span})
        elif end is not None and float(end) >= duration - tail_tolerance:
            tail_failures.append({"reason": "freeze_reaches_tail", **span})
    return {
        "spans": spans,
        "tail_failures": tail_failures,
        "tail_ok": not tail_failures,
        "tail_tolerance_seconds": tail_tolerance,
    }


def presentation_cadence(
    video: str | os.PathLike[str],
    expected_rate: str | float | None = None,
    *,
    max_frames: int | None = None,
) -> dict[str, Any]:
    """Inspect presentation timestamps and frame deltas for cadence defects.

    Container ``r_frame_rate`` alone can remain correct while frames are
    duplicated or a timestamp gap is introduced.  This check samples the
    actual presentation timestamps and reports the median, minimum, and
    maximum deltas.  A short bounded output can safely inspect all frames;
    callers may cap the list for long source assets.
    """

    command = [
        "ffprobe", "-v", "error", "-select_streams", "v:0", "-show_frames",
        "-show_entries", "frame=best_effort_timestamp_time,pkt_duration_time",
        "-of", "json", str(video),
    ]
    try:
        result = subprocess.run(command, capture_output=True, text=True, check=True)
        frames = json.loads(result.stdout).get("frames", [])
    except (OSError, subprocess.CalledProcessError, json.JSONDecodeError) as exc:
        return {"passed": False, "status": "error", "error": str(exc), "frame_count": 0}
    timestamps = []
    missing_timestamp_count = 0
    for frame in frames:
        raw = frame.get("best_effort_timestamp_time")
        if raw is None:
            missing_timestamp_count += 1
            continue
        try:
            timestamps.append(float(raw))
        except (TypeError, ValueError):
            missing_timestamp_count += 1
    if max_frames and len(timestamps) > max_frames:
        # Evenly sample the timeline while retaining endpoints.  The final
        # quality path leaves this uncapped for short clips.
        step = max(1, len(timestamps) // max_frames)
        last_timestamp = timestamps[-1]
        timestamps = timestamps[::step]
        if timestamps and timestamps[-1] != last_timestamp:
            timestamps.append(last_timestamp)
    deltas = [b - a for a, b in zip(timestamps, timestamps[1:])]
    if not deltas:
        return {
            "passed": False,
            "status": "insufficient_frames",
            "frame_count": len(timestamps),
            "deltas": [],
            "missing_timestamp_count": missing_timestamp_count,
        }
    positive_deltas = [delta for delta in deltas if delta > 0]
    if not positive_deltas:
        return {
            "passed": False,
            "status": "nonmonotonic_timestamps",
            "frame_count": len(timestamps),
            "deltas": deltas,
            "missing_timestamp_count": missing_timestamp_count,
            "nonpositive_delta_count": len(deltas),
        }
    deltas_sorted = sorted(positive_deltas)
    median = deltas_sorted[len(deltas_sorted) // 2]
    expected = None
    if expected_rate:
        try:
            expected = float(Fraction(str(expected_rate)))
        except (ValueError, ZeroDivisionError):
            expected = float(expected_rate)
    expected_delta = 1.0 / expected if expected and expected > 0 else median
    # A 2% cadence tolerance accounts for timestamp rounding while catching
    # frame duplication, dropped spans, and obvious VFR gaps.
    tolerance = max(expected_delta * 0.02, 0.0005)
    close = [abs(delta - expected_delta) <= tolerance for delta in deltas]
    max_gap = max(deltas)
    min_delta = min(deltas)
    gap_limit = expected_delta * 1.5
    nonpositive_delta_count = sum(1 for delta in deltas if delta <= 0)
    passed = (
        bool(timestamps)
        and missing_timestamp_count == 0
        and nonpositive_delta_count == 0
        and all(close)
        and max_gap <= gap_limit
    )
    return {
        "passed": passed,
        "status": "checked",
        "frame_count": len(timestamps),
        "missing_timestamp_count": missing_timestamp_count,
        "nonpositive_delta_count": nonpositive_delta_count,
        "expected_rate": expected_rate,
        "expected_delta_seconds": expected_delta,
        "median_delta_seconds": median,
        "minimum_delta_seconds": min_delta,
        "maximum_delta_seconds": max_gap,
        "delta_tolerance_seconds": tolerance,
        "large_gap_limit_seconds": gap_limit,
        "nonmatching_delta_count": sum(1 for item in close if not item),
    }


def _candidate_id(edl: dict[str, Any]) -> str | None:
    for key in ("candidate_id", "id", "slug", "name"):
        if edl.get(key) is not None:
            return str(edl[key])
    return None


def _candidate_ids(edl: dict[str, Any]) -> set[str]:
    return {
        str(edl[key])
        for key in ("candidate_id", "id", "slug", "name")
        if edl.get(key) is not None
    }


def _report_path(edl: dict[str, Any], edl_path: str | os.PathLike[str] | None) -> Path | None:
    values: list[Any] = [
        edl.get("alignment_report"),
        edl.get("range_alignment_report"),
        edl.get("source_alignment_report"),
    ]
    nested = edl.get("alignment")
    if isinstance(nested, dict):
        values.extend([nested.get("report"), nested.get("alignment_report")])
    base = _path(edl_path).parent if edl_path else Path.cwd()
    for value in values:
        if not value:
            continue
        candidate = Path(str(value)).expanduser()
        if not candidate.is_absolute():
            # EDLs in active projects commonly retain workspace-relative paths
            # such as ``Projects/Active/.../qa/range-acquisition.json``;
            # future EDLs may use candidate-relative paths.  Resolve the first
            # existing interpretation without guessing a different report.
            probes = [base / candidate]
            cursor = base
            for _ in range(6):
                cursor = cursor.parent
                probes.append(cursor / candidate)
            existing = next((item for item in probes if item.exists()), None)
            candidate = existing or probes[0]
        return candidate.resolve()
    return None


def resolve_declared_path(value: str | os.PathLike[str], reference: str | os.PathLike[str] | None = None) -> Path:
    """Resolve an EDL path as absolute, candidate-relative, or workspace-relative."""

    candidate = Path(str(value)).expanduser()
    if candidate.is_absolute():
        return candidate.resolve()
    base = _path(reference).parent if reference else Path.cwd()
    probes = [base / candidate]
    cursor = base
    for _ in range(6):
        cursor = cursor.parent
        probes.append(cursor / candidate)
    return next((item.resolve() for item in probes if item.exists()), probes[0].resolve())


def _candidate_report(report: dict[str, Any], edl: dict[str, Any]) -> dict[str, Any] | None:
    wanted = _candidate_ids(edl)
    records = report.get("candidates") or report.get("clips") or []
    if isinstance(records, dict):
        keyed = []
        for key, value in records.items():
            if isinstance(value, dict):
                value = dict(value)
                value.setdefault("slug", key)
                keyed.append(value)
        records = keyed
    for item in records:
        if not isinstance(item, dict):
            continue
        ids = {str(item.get(key)) for key in ("candidate_id", "id", "slug", "name") if item.get(key) is not None}
        if wanted and wanted & ids:
            return item
    return None


def _source_identity(owner: dict[str, Any] | None) -> dict[str, Any] | None:
    """Return the explicit source identity under either supported spelling."""

    if not isinstance(owner, dict):
        return None
    for key in ("source_identity", "source_fingerprint"):
        value = owner.get(key)
        if isinstance(value, dict) and value:
            return value
    return None


def _asset_records(record: dict[str, Any], report: dict[str, Any]) -> dict[str, Any]:
    for owner in (record, report):
        for key in ("asset_fingerprints", "assets", "source_assets"):
            value = owner.get(key)
            if isinstance(value, dict):
                return value
    # Compact candidate reports commonly store the two checks as scalar
    # ``video_sha256``/``audio_sha256`` fields.  Normalize those into the
    # preferred nested shape without requiring the range agent to duplicate
    # its record for this consumer.
    compact: dict[str, Any] = {}
    for kind, aliases in {
        "video": ("video_sha256", "actual_video_sha256", "video_file", "video_path"),
        "audio": ("audio_sha256", "actual_audio_sha256", "audio_file", "audio_path"),
    }.items():
        item: dict[str, Any] = {}
        for key in aliases:
            value = record.get(key) or report.get(key)
            if not value:
                continue
            if key.endswith("sha256"):
                item["sha256"] = value
            elif key.endswith("file") or key.endswith("path"):
                item["path"] = value
        if item:
            compact[kind] = item
    return compact
    return {}


def _asset_record(assets: dict[str, Any], kind: str) -> dict[str, Any] | None:
    value = assets.get(kind)
    if isinstance(value, dict):
        return value
    # Some reports use source_video/source_audio keys.
    aliases = {
        "video": ("source_video", "video_file", "video_path"),
        "audio": ("source_audio", "audio_file", "audio_path"),
    }
    for key in aliases.get(kind, ()):
        value = assets.get(key)
        if isinstance(value, dict):
            return value
    return None


def _seconds(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    try:
        if ":" not in text:
            return float(text)
        parts = text.split(":")
        if len(parts) != 3:
            return None
        return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
    except (TypeError, ValueError):
        return None


def _range_from_edl(edl: dict[str, Any]) -> dict[str, float | None]:
    starts = [
        _seconds(edl.get("source_in_seconds")),
        _seconds(edl.get("source_in")),
    ]
    ends = [
        _seconds(edl.get("source_out_seconds")),
        _seconds(edl.get("source_out")),
    ]
    for unit in edl.get("beats", []) or edl.get("segments", []):
        starts.extend([_seconds(unit.get("source_in")), _seconds(unit.get("planned_source_in"))])
        ends.extend([_seconds(unit.get("source_out")), _seconds(unit.get("planned_source_out"))])
    start = next((value for value in starts if value is not None), None)
    end = next((value for value in ends if value is not None), None)
    return {
        "source_in": start,
        "source_out": end,
        "handled_start": _seconds(edl.get("handled_start")),
        "handled_end": _seconds(edl.get("handled_end")),
    }


def _status_pass(value: Any) -> bool:
    return str(value).upper() in {"PASS", "PASSED", "OK", "VERIFIED", "TRUE"} or value is True


def alignment_gate(
    edl: dict[str, Any],
    edl_path: str | os.PathLike[str] | None = None,
) -> dict[str, Any]:
    """Require candidate-specific alignment evidence for actual EDL assets.

    A project-level ``PASS`` is insufficient: the matching candidate must be
    present, verified, and tied to the exact source video and audio files in
    the EDL.  The preferred range report records size, mtime, and SHA-256 for
    both assets.  Older reports lacking those fingerprints remain pending so a
    changed or substituted range cannot silently reach delivery.
    """

    path = _report_path(edl, edl_path)
    result: dict[str, Any] = {
        "name": "source alignment",
        "check": "candidate_source_alignment",
        "status": "pending",
        "passed": False,
        "report": str(path) if path else None,
        "candidate": _candidate_id(edl),
        "required_assets": {},
        "checks": [],
    }
    if path is None:
        result["reason"] = "EDL has no alignment_report path"
        return result
    if not path.exists():
        result["reason"] = "alignment report missing"
        return result
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        result["reason"] = f"alignment report unreadable: {exc}"
        return result
    # A batch report may be REVIEW_REQUIRED because another candidate is
    # held.  Candidate-specific PASS evidence is still valid for this EDL;
    # FAIL remains a hard stop.
    report_status = str(report.get("status", "")).upper() in {"PASS", "PASSED", "OK", "VERIFIED", "REVIEW_REQUIRED"}
    result["checks"].append({"name": "report status", "passed": report_status, "observed": report.get("status")})
    candidate = _candidate_report(report, edl)
    if candidate is None:
        result["reason"] = "candidate record missing from alignment report"
        return result
    candidate_status = _status_pass(candidate.get("status")) and candidate.get("verified", True) is not False
    result["checks"].append({"name": "candidate verified", "passed": candidate_status, "observed": candidate.get("status")})

    report_identity = _source_identity(candidate) or _source_identity(report)
    expected_identity = _source_identity(edl)
    if expected_identity is None:
        expected_identity = {
            key: edl[key]
            for key in ("source_id", "source_url", "source_title")
            if edl.get(key) is not None
        }
    identity_ok = isinstance(report_identity, dict) and bool(report_identity)
    if identity_ok and isinstance(expected_identity, dict):
        identity_ok = all(report_identity.get(key) == value for key, value in expected_identity.items())
    result["checks"].append({
        "name": "source identity",
        "passed": identity_ok,
        "expected": expected_identity,
        "observed": report_identity,
    })

    # The same range asset can be aligned correctly at the wrong source
    # position.  Bind the report to the EDL's original long-form offsets and
    # handles before accepting its media fingerprints.
    expected_range = _range_from_edl(edl)
    range_fields = {
        "source_in": ("source_in", "planned_source_in", "source_in_seconds"),
        "source_out": ("source_out", "planned_source_out", "source_out_seconds"),
        "handled_start": ("handled_start", "handled_in"),
        "handled_end": ("handled_end", "handled_out"),
    }
    for field, aliases in range_fields.items():
        expected_value = expected_range.get(field)
        observed_value = next((_seconds(candidate.get(alias)) for alias in aliases if candidate.get(alias) is not None), None)
        # Original source positions are mandatory for a verified range.  A
        # missing handled field is also a blocker when the EDL declares one.
        required = expected_value is not None
        passed = expected_value is not None and observed_value is not None and abs(float(expected_value) - float(observed_value)) <= 0.01
        if not required and field.startswith("handled_"):
            passed = True
        result["checks"].append({
            "name": f"{field} timecode binding",
            "passed": passed,
            "expected": expected_value,
            "observed": observed_value,
        })

    assets = _asset_records(candidate, report)
    for kind, edl_key in (("video", "source_video"), ("audio", "source_audio")):
        raw_declared = edl.get(edl_key)
        raw_path = resolve_declared_path(raw_declared, edl_path) if raw_declared else None
        raw = str(raw_path) if raw_path else None
        item: dict[str, Any] = {"path": raw, "exists": False, "fingerprint": None}
        result["required_assets"][kind] = item
        if not raw:
            result["checks"].append({"name": f"{kind} asset path", "passed": False, "observed": raw})
            continue
        try:
            actual = file_fingerprint(raw)
            item["exists"] = True
            item["fingerprint"] = actual
        except OSError as exc:
            result["checks"].append({"name": f"{kind} asset exists", "passed": False, "observed": str(exc)})
            continue
        expected = _asset_record(assets, kind)
        if not expected:
            result["checks"].append({"name": f"{kind} asset fingerprint", "passed": False, "observed": "missing"})
            continue
        expected_path = expected.get("path") or expected.get("file")
        path_ok = not expected_path or _path(expected_path) == _path(raw)
        fingerprint_ok = fingerprint_matches(actual, {**expected, "path": expected_path} if expected_path else expected)
        result["checks"].append({
            "name": f"{kind} asset fingerprint",
            "passed": path_ok and fingerprint_ok,
            "expected": expected,
            "observed": actual,
        })

    # A report may carry an explicit source identity.  Preserve it as evidence
    # while accepting any source identity object that the range agent records.
    report_identity = _source_identity(report)
    if report_identity is not None:
        result["source_identity"] = report_identity
    result["status"] = "pass" if report_status and candidate_status and all(c["passed"] for c in result["checks"]) else "pending"
    result["passed"] = result["status"] == "pass"
    if not result["passed"]:
        result["reason"] = "alignment evidence is missing, stale, or failed"
    return result


def _check_status(value: Any) -> bool:
    if isinstance(value, bool):
        return False
    if isinstance(value, dict):
        if value.get("completed") is not True or value.get("reviewed") is not True:
            return False
        return _status_pass(value.get("status", value.get("result", value.get("value"))))
    return False


def _checks_pass(checks: Any, required_names: Iterable[str]) -> tuple[bool, list[str]]:
    missing: list[str] = []
    if isinstance(checks, dict):
        mapping = checks
    elif isinstance(checks, list):
        mapping = {
            str(item.get("name")): item
            for item in checks if isinstance(item, dict) and item.get("name") is not None
        }
    else:
        mapping = {}
    for name in required_names:
        value = mapping.get(name)
        # Accept a small set of readable aliases in externally completed
        # records, but never accept a bare True: completed and reviewed must
        # both be explicit.
        if value is None:
            for alias in (name.replace("_", " "), name.replace(" ", "_")):
                if alias in mapping:
                    value = mapping[alias]
                    break
        if not _check_status(value):
            missing.append(name)
    return not missing, missing


def _hash_from(record: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = record.get(key)
        if isinstance(value, dict):
            value = value.get("sha256") or value.get("hash")
        if value:
            return str(value)
    return None


def _review_section(record: dict[str, Any], *keys: str) -> dict[str, Any]:
    for key in keys:
        value = record.get(key)
        if isinstance(value, dict):
            return value
    return {}


def semantic_gate(
    record: dict[str, Any],
    rendered_vtt: str | os.PathLike[str] | None,
    *,
    render_sha256: str | None = None,
) -> dict[str, Any]:
    """Validate the structured semantic review portion of a record.

    Semantic review is intentionally separate from the audio-content overlap
    screen.  Only ``clip_planner`` may declare the completed semantic checks;
    the record must include notes and the actual rendered VTT as evidence.
    """

    semantic = _review_section(record, "semantic_review", "semantic", "editorial_review")
    checks_pass, missing = _checks_pass(
        semantic.get("checks"),
        (
            "meaning_preserved", "promise_truthful", "speaker_attribution",
            "material_qualifiers", "payoff_completion", "reorder_context",
        ),
    )
    record_vtt = semantic.get("rendered_vtt") or record.get("rendered_vtt") or {}
    if isinstance(record_vtt, str):
        record_vtt = {"path": record_vtt}
    path = _path(rendered_vtt) if rendered_vtt else (_path(record_vtt["path"]) if record_vtt.get("path") else None)
    actual_hash = sha256_file(path) if path and path.exists() else None
    vtt_ok = bool(path and actual_hash and record_vtt.get("sha256") == actual_hash)
    provenance_path = Path(str(path) + ".provenance.json") if path else None
    provenance_ok = False
    provenance_data: dict[str, Any] = {}
    if provenance_path and provenance_path.exists():
        try:
            provenance_data = json.loads(provenance_path.read_text(encoding="utf-8"))
            provenance_ok = (
                provenance_data.get("vtt") == str(path)
                and provenance_data.get("vtt_sha256") == actual_hash
                and bool(provenance_data.get("render_sha256"))
                and (render_sha256 is None or provenance_data.get("render_sha256") == render_sha256)
            )
        except (OSError, json.JSONDecodeError):
            provenance_ok = False
    source_record = semantic.get("source_vtt_context") or record.get("source_vtt_context") or {}
    if isinstance(source_record, str):
        source_record = {"path": source_record}
    source_path = _path(source_record["path"]) if source_record.get("path") else None
    source_hash = sha256_file(source_path) if source_path and source_path.exists() else None
    context_excerpts = semantic.get("context_excerpts") or record.get("context_excerpts") or []
    source_context_ok = bool(
        source_path and source_hash and source_record.get("sha256") == source_hash
    ) or bool(
        isinstance(context_excerpts, list) and context_excerpts and all(
            isinstance(item, dict) and item.get("reviewed") is True and str(item.get("text", "")).strip()
            and item.get("source_in") is not None and item.get("source_out") is not None
            for item in context_excerpts
        )
    )
    notes = str(semantic.get("notes") or "").strip()
    checks = [
        {"name": "reviewer", "passed": semantic.get("reviewer") == "clip_planner", "observed": semantic.get("reviewer")},
        {"name": "status", "passed": _status_pass(semantic.get("status")) and semantic.get("reviewed") is True, "observed": semantic.get("status")},
        {"name": "completed checks", "passed": checks_pass, "missing": missing},
        {"name": "notes", "passed": len(notes) >= 10},
        {"name": "rendered VTT evidence", "passed": vtt_ok, "path": str(path) if path else None, "sha256": actual_hash},
        {"name": "rendered VTT provenance", "passed": provenance_ok, "path": str(provenance_path) if provenance_path else None, "render_sha256": provenance_data.get("render_sha256")},
        {"name": "source VTT context", "passed": source_context_ok, "path": str(source_path) if source_path else None, "sha256": source_hash},
    ]
    passed = all(item["passed"] for item in checks)
    return {
        "name": "semantic review",
        "status": "pass" if passed else "pending",
        "passed": passed,
        "semantic_proof_source": "reviewed structured record",
        "checks": checks,
        "rendered_vtt": {"path": str(path) if path else None, "sha256": actual_hash},
        "reason": None if passed else "semantic review requires clip_planner checks, notes, and rendered VTT evidence",
    }


def validate_review_record(
    record: dict[str, Any],
    clip: str | os.PathLike[str],
    edl_path: str | os.PathLike[str],
    rendered_vtt: str | os.PathLike[str] | None = None,
    *,
    edl: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Validate a completed visual/editorial record against current assets.

    This function never turns extracted images into an automatic pass.  The
    record must say that a reviewer completed each required check and provide
    notes plus still/moving evidence.  Semantic review may only be declared by
    ``clip_planner`` and must cite the actual rendered VTT.
    """

    clip_path, plan_path = _path(clip), _path(edl_path)
    result: dict[str, Any] = {
        "name": "visual/editorial review",
        "status": "pending",
        "passed": False,
        "checks": [],
        "evidence_is_not_review": True,
    }
    try:
        render_hash = sha256_file(clip_path)
        edl_hash = sha256_file(plan_path)
    except OSError as exc:
        result["reason"] = f"review asset missing: {exc}"
        return result
    visual = _review_section(record, "visual_review", "visual_qa", "visual")
    semantic = _review_section(record, "semantic_review", "editorial_review", "semantic")
    record_render_hash = _hash_from(record, "render_sha256", "render_hash") or _hash_from(visual, "render_sha256", "render_hash") or _hash_from(semantic, "render_sha256", "render_hash")
    record_edl_hash = _hash_from(record, "edl_sha256", "edl_hash") or _hash_from(visual, "edl_sha256", "edl_hash") or _hash_from(semantic, "edl_sha256", "edl_hash")
    result["checks"].append({"name": "render hash", "passed": record_render_hash == render_hash, "observed": record_render_hash, "current": render_hash})
    result["checks"].append({"name": "EDL hash", "passed": record_edl_hash == edl_hash, "observed": record_edl_hash, "current": edl_hash})

    visual_status = _status_pass(visual.get("status")) and visual.get("reviewed") is True
    result["checks"].append({"name": "visual reviewer completed", "passed": visual_status, "observed": visual.get("reviewer")})
    visual_names = ("payoff_visibility", "source_detail", "cadence", "crop_continuity", "subject_identity")
    visual_checks_pass, visual_missing = _checks_pass(visual.get("checks"), visual_names)
    result["checks"].append({"name": "visual checks", "passed": visual_checks_pass, "missing": visual_missing})
    source_metadata = visual.get("source_metadata") or record.get("source_metadata") or {}
    resolution = source_metadata.get("source_resolution") or source_metadata.get("resolution")
    crop_scale = source_metadata.get("crop_scale") or source_metadata.get("source_rect_scale")
    crop_magnification = source_metadata.get("crop_magnification")
    result["checks"].append({"name": "source resolution recorded", "passed": bool(resolution), "observed": resolution})
    result["checks"].append({"name": "crop scale recorded", "passed": bool(crop_scale), "observed": crop_scale})
    result["checks"].append({"name": "crop magnification recorded", "passed": bool(crop_magnification), "observed": crop_magnification})

    notes = str(visual.get("notes") or record.get("notes") or "").strip()
    result["checks"].append({"name": "visual notes", "passed": len(notes) >= 10, "observed": notes})
    evidence = visual.get("evidence") or record.get("evidence") or []
    if isinstance(evidence, dict):
        evidence = list(evidence.values())
    evidence_types = {str(item.get("type") or item.get("purpose")) for item in evidence if isinstance(item, dict)}
    required_evidence = {"opening_still", "end_still", "payoff_moving_excerpt"}
    units = (edl or {}).get("segments") or (edl or {}).get("beats") or []
    required_items: list[tuple[str, int | None]] = [
        ("opening_still", None), ("end_still", None), ("payoff_moving_excerpt", None),
    ]
    for index in range(1, max(1, len(units))):
        required_items.extend([
            ("splice_before_still", index),
            ("splice_after_still", index),
            ("splice_moving_excerpt", index),
        ])
    missing_evidence: list[str] = []
    evidence_by_key: dict[tuple[str, int | None], dict[str, Any]] = {}
    evidence_binding_failures: list[str] = []
    for item in evidence:
        if not isinstance(item, dict):
            evidence_binding_failures.append("non-object evidence entry")
            continue
        kind = str(item.get("type") or item.get("purpose") or "")
        index = item.get("splice_index")
        key = (kind, int(index) if index is not None else None)
        evidence_by_key[key] = item
        path = item.get("path")
        current_hash = None
        if path and Path(str(path)).expanduser().exists():
            try:
                current_hash = sha256_file(path)
            except OSError:
                current_hash = None
        if not path or current_hash is None or item.get("sha256") != current_hash or item.get("reviewed") is not True or item.get("inspected") is not True:
            evidence_binding_failures.append(kind or "unknown evidence")
    for kind, index in required_items:
        if (kind, index) not in evidence_by_key:
            missing_evidence.append(f"{kind}{f'[{index}]' if index is not None else ''}")
    result["checks"].append({
        "name": "review evidence",
        "passed": not missing_evidence and not evidence_binding_failures and bool(evidence),
        "missing": missing_evidence,
        "binding_failures": evidence_binding_failures,
        "frame_extraction_is_evidence_not_review": True,
    })

    semantic_result = semantic_gate(record, rendered_vtt, render_sha256=render_hash)
    result["checks"].extend(semantic_result.get("checks", []))

    result["render_sha256"] = render_hash
    result["edl_sha256"] = edl_hash
    result["rendered_vtt"] = semantic_result.get("rendered_vtt")
    result["semantic_review_passed"] = bool(semantic_result.get("passed"))
    result["visual_review_passed"] = bool(visual_status and visual_checks_pass)
    result["status"] = "pass" if all(check["passed"] for check in result["checks"]) else "pending"
    result["passed"] = result["status"] == "pass"
    if not result["passed"]:
        result["reason"] = "visual/editorial review is incomplete or stale"
    return result


def delivery_gate(
    *,
    technical: dict[str, Any],
    audio_content: dict[str, Any],
    alignment: dict[str, Any],
    review: dict[str, Any],
) -> dict[str, Any]:
    """Return the fail-closed final delivery decision."""

    technical_pass = technical.get("passed") is True
    audio_pass = audio_content.get("passed") is True or audio_content.get("status") == "not_applicable"
    alignment_pass = alignment.get("passed") is True
    review_pass = review.get("passed") is True
    semantic_review_pass = review.get("semantic_review_passed") is True
    checks = [
        {"name": "technical", "passed": technical_pass},
        {"name": "audio content", "passed": audio_pass, "status": audio_content.get("status")},
        {"name": "source alignment", "passed": alignment_pass},
        {"name": "visual/editorial review", "passed": review_pass},
        {"name": "semantic review", "passed": semantic_review_pass},
    ]
    passed = all(item["passed"] for item in checks)
    return {
        "name": "final delivery",
        "status": "pass" if passed else "pending",
        "passed": passed,
        "delivery_eligible": passed,
        "checks": checks,
        "reason": None if passed else "all technical, audio-content, alignment, and visual/editorial gates are required",
    }


def source_visual_metadata(
    edl: dict[str, Any],
    source_video: str | os.PathLike[str] | None = None,
    *,
    reference: str | os.PathLike[str] | None = None,
) -> dict[str, Any]:
    """Return source resolution and computed crop magnification.

    Planner-provided ``source_rect_scale`` is retained as evidence, but the
    review record also gets a computed magnification for every unit.  If a
    source or crop rectangle cannot be inspected, the value stays ``None`` so
    the visual gate remains pending instead of guessing.
    """

    result: dict[str, Any] = {
        "source_resolution": None,
        "crop_scale": edl.get("source_rect_scale"),
        "source_rect_basis": edl.get("source_rect_basis"),
        "crop_magnification": None,
    }
    path = source_video or edl.get("source_video")
    if path and not Path(str(path)).expanduser().is_absolute():
        path = resolve_declared_path(path, reference)
    if path:
        try:
            probe = subprocess.run(
                ["ffprobe", "-v", "error", "-select_streams", "v:0",
                 "-show_entries", "stream=width,height,r_frame_rate",
                 "-of", "json", str(path)],
                capture_output=True, text=True, check=True,
            )
            data = json.loads(probe.stdout)
            stream = (data.get("streams") or [{}])[0]
            result["source_resolution"] = {
                "width": stream.get("width"),
                "height": stream.get("height"),
                "frame_rate": stream.get("r_frame_rate"),
            }
        except (OSError, subprocess.CalledProcessError, json.JSONDecodeError):
            result["source_resolution"] = None
    resolution = result.get("source_resolution") or {}
    src_w, src_h = resolution.get("width"), resolution.get("height")
    if src_w and src_h:
        units = edl.get("segments") or edl.get("beats") or []
        magnifications: list[dict[str, Any]] = []
        crop_w = int(round(float(src_h) * 9 / 16))
        if not units:
            units = [{"mode": "crop"}]
        for index, unit in enumerate(units, 1):
            mode = unit.get("mode", "crop")
            rects: list[tuple[str, dict[str, Any]]] = []
            if mode == "compose":
                if isinstance(unit.get("top"), dict):
                    rects.append(("top", unit["top"]))
                if isinstance(unit.get("bottom"), dict):
                    rects.append(("bottom", unit["bottom"]))
            elif mode == "solo" and isinstance(unit.get("rect"), dict):
                rects.append(("solo", unit["rect"]))
            elif mode == "split":
                rects.extend((name, unit) for name in ("x1", "x2") if unit.get(name) is not None)
            else:
                rects.append(("crop", {"w": crop_w, "h": src_h}))
            unit_record: dict[str, Any] = {"unit": index, "mode": mode, "rectangles": []}
            for name, rect in rects:
                try:
                    rw = float(rect.get("w", crop_w))
                    rh = float(rect.get("h", src_h))
                    if rw <= 0 or rh <= 0:
                        raise ValueError("non-positive crop rectangle")
                    # Compose and solo panels are scaled to a 1080-wide output;
                    # split halves use the same width and half-height.  The
                    # ratio is evidence of detail loss, not a quality verdict.
                    panel_h = OUT_H
                    if mode == "compose":
                        panel_h = float(unit.get("split", 640) if name == "top" else OUT_H - int(unit.get("split", 640)))
                    elif mode == "split":
                        panel_h = OUT_H / 2
                    unit_record["rectangles"].append({
                        "name": name,
                        "source_size": [rw, rh],
                        "output_size": [OUT_W, panel_h],
                        "scale_x": round(OUT_W / rw, 5),
                        "scale_y": round(panel_h / rh, 5),
                    })
                except (TypeError, ValueError):
                    unit_record["rectangles"].append({"name": name, "unknown": True})
            magnifications.append(unit_record)
        result["crop_magnification"] = magnifications
        if result.get("crop_scale") is None:
            result["crop_scale"] = magnifications
    return result


def _cli() -> int:
    parser = argparse.ArgumentParser(description="Validate Method 2 quality evidence and final delivery gates")
    parser.add_argument("--review", required=True, help="completed external review record")
    parser.add_argument("--render", required=True)
    parser.add_argument("--edl", required=True)
    parser.add_argument("--vtt")
    args = parser.parse_args()
    edl_path = _path(args.edl)
    edl = json.loads(edl_path.read_text(encoding="utf-8"))
    record = json.loads(_path(args.review).read_text(encoding="utf-8"))
    result = validate_review_record(record, args.render, edl_path, args.vtt, edl=edl)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if result["passed"] else 2


if __name__ == "__main__":
    raise SystemExit(_cli())
