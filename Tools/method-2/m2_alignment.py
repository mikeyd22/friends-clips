#!/usr/bin/env python3
"""Independent Method 2 range and source alignment checks.

The range audio cut from ``full_audio`` is the audio used for assembly.  It is
not evidence that the downloaded video contains the planned picture.  This
module therefore has two independent verification paths:

* a native audio stream in the acquired video is correlated against the full
  source at early, middle, and late positions; or
* a video-only range is compared with the already trusted full analysis proxy
  at the same three positions.

The replacement audio cut is deliberately not accepted as proof of video
alignment.  Low-confidence correlation is ``PENDING_REVIEW`` unless the plan
contains an explicit, documented transcript verification record.

The functions are intentionally small and injectable so synthetic tests can
exercise the policy without downloading or editing project media.
"""

from __future__ import annotations

import hashlib
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

import numpy as np


SR = 8000
HZ = 100
WIN = SR // HZ
TOL = 0.5
CONF_FLOOR = 12.0
AUDIO_MIN_CORR = 0.55
VISUAL_MIN_CORR = 0.70
SEARCH_SECONDS = 2.0
ANCHOR_WINDOW_SECONDS = 10.0


def run(cmd: Sequence[str], **kwargs: Any) -> subprocess.CompletedProcess:
    """Run a media command with the same strict default as the range runner."""

    return subprocess.run(cmd, check=True, **kwargs)


def sha256_file(path: str | os.PathLike[str], chunk_size: int = 1024 * 1024) -> str:
    """Return the SHA-256 digest of an actual media file."""

    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        while True:
            block = handle.read(chunk_size)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def env_from_raw(path: str | os.PathLike[str]) -> np.ndarray:
    """Read signed 16-bit mono PCM and return a 100 Hz RMS envelope."""

    audio = np.fromfile(path, dtype=np.int16).astype(np.float32)
    count = len(audio) // WIN
    if count <= 0:
        return np.empty(0, dtype=np.float32)
    return np.sqrt(
        (audio[: count * WIN].reshape(count, WIN) ** 2).mean(axis=1) + 1e-9
    )


def env_of(
    path: str | os.PathLike[str],
    seconds: float | None = None,
    offset: float = 0.0,
    stream: str | None = None,
) -> np.ndarray:
    """Extract a mono RMS envelope from a media file.

    ``stream`` is normally ``a:0`` for native video audio.  Leaving it unset
    keeps compatibility with the old helper used by the Friends adapter.
    """

    raw_fd, raw_name = tempfile.mkstemp(suffix=".raw")
    os.close(raw_fd)
    try:
        command = ["ffmpeg", "-hide_banner", "-v", "error"]
        if offset:
            command += ["-ss", f"{float(offset):.3f}"]
        command += ["-i", str(path)]
        if stream:
            command += ["-map", f"0:{stream}"]
        if seconds is not None:
            command += ["-t", f"{float(seconds):.3f}"]
        command += ["-ac", "1", "-ar", str(SR), "-f", "s16le", raw_name, "-y"]
        run(command)
        return env_from_raw(raw_name)
    finally:
        try:
            os.remove(raw_name)
        except FileNotFoundError:
            pass


def has_stream(path: str | os.PathLike[str], selector: str) -> bool:
    """Return whether ffprobe reports a stream matching ``selector``."""

    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            selector,
            "-show_entries",
            "stream=index",
            "-of",
            "csv=p=0",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    return result.returncode == 0 and bool(result.stdout.strip())


def media_duration(path: str | os.PathLike[str]) -> float | None:
    """Read a media duration without making duration failure fatal to tests."""

    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=nw=1:nk=1",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return None
    try:
        return float(result.stdout.strip())
    except (TypeError, ValueError):
        return None


def find_offset(full: np.ndarray, probe: np.ndarray) -> tuple[float, float]:
    """Compatibility helper for the original full-source FFT check.

    New verification uses local, multi-position checks below.  This remains
    available to existing adapters that import it.
    """

    if len(full) == 0 or len(probe) == 0 or len(full) < len(probe):
        return 0.0, 0.0
    f = full - full.mean()
    p = probe - probe.mean()
    n = 1 << int(np.ceil(np.log2(len(f) + len(p))))
    corr = np.fft.irfft(np.fft.rfft(f, n) * np.fft.rfft(p[::-1], n), n)[: len(f)]
    lag = int(np.argmax(corr)) - (len(p) - 1)
    confidence = float((corr.max() - corr.mean()) / (corr.std() + 1e-9))
    return lag / HZ, confidence


def _normalized_correlation(left: np.ndarray, right: np.ndarray) -> float:
    """Pearson-like correlation with silence treated as uninformative."""

    if len(left) != len(right) or len(left) < 2:
        return 0.0
    a = np.asarray(left, dtype=np.float64).reshape(-1)
    b = np.asarray(right, dtype=np.float64).reshape(-1)
    a -= a.mean()
    b -= b.mean()
    den = float(np.linalg.norm(a) * np.linalg.norm(b))
    if den <= 1e-9:
        return 0.0
    return float(np.dot(a, b) / den)


def _search_audio_window(
    full: np.ndarray,
    probe: np.ndarray,
    expected_lag_seconds: float,
    search_seconds: float = SEARCH_SECONDS,
) -> dict[str, float]:
    """Find a local audio match near the expected source position."""

    if len(probe) < max(2 * HZ, 1) or len(full) < len(probe):
        return {"observed_seconds": 0.0, "correlation": 0.0, "confidence": 0.0}

    centre = int(round(expected_lag_seconds * HZ))
    radius = max(1, int(round(search_seconds * HZ)))
    lo = max(0, centre - radius)
    hi = min(len(full) - len(probe), centre + radius)
    if hi < lo:
        return {"observed_seconds": 0.0, "correlation": 0.0, "confidence": 0.0}

    values: list[float] = []
    lags: list[int] = []
    for lag in range(lo, hi + 1):
        values.append(_normalized_correlation(full[lag : lag + len(probe)], probe))
        lags.append(lag)
    scores = np.asarray(values, dtype=np.float64)
    best_index = int(np.argmax(scores))
    best = float(scores[best_index])
    baseline = float(np.median(scores))
    spread = float(np.std(scores))
    confidence = max(0.0, (best - baseline) / (spread + 1e-9))
    return {
        "observed_seconds": lags[best_index] / HZ,
        "correlation": best,
        "confidence": confidence,
    }


def anchor_positions(duration_seconds: float, window_seconds: float = ANCHOR_WINDOW_SECONDS) -> list[float]:
    """Return deduplicated early, middle, and late local positions."""

    duration = max(0.0, float(duration_seconds))
    window = min(max(2.0, float(window_seconds)), duration) if duration else 0.0
    if duration <= 0.0 or window <= 0.0:
        return []
    if duration <= window:
        return [0.0]
    values = [0.0, (duration - window) / 2.0, duration - window]
    result: list[float] = []
    for value in values:
        rounded = round(max(0.0, value), 3)
        if not result or abs(result[-1] - rounded) > 1e-6:
            result.append(rounded)
    return result


def _alignment_status(
    anchors: list[dict[str, Any]],
    *,
    tolerance: float,
    confidence_floor: float,
    minimum_correlation: float,
) -> tuple[str, str, float, float, float]:
    """Classify anchors while preserving drift and low-confidence distinctions."""

    if not anchors:
        return "PENDING_REVIEW", "no alignment anchors", 0.0, 0.0, 0.0

    errors = np.asarray([float(item["offset_error_seconds"]) for item in anchors])
    correlations = np.asarray([float(item["correlation"]) for item in anchors])
    confidences = np.asarray([float(item["confidence"]) for item in anchors])
    max_abs = float(np.max(np.abs(errors)))
    drift = float(errors[-1] - errors[0]) if len(errors) > 1 else 0.0
    span = float(np.max(errors) - np.min(errors))
    quality = (correlations >= minimum_correlation) & (confidences >= confidence_floor)
    # A damaged or quiet anchor may be pending while two other anchors still
    # establish a real time-varying offset.  Preserve that evidence as a drift
    # failure instead of hiding it behind the low-confidence fallback.
    if int(np.sum(quality)) >= 2:
        quality_errors = errors[quality]
        if float(np.max(quality_errors) - np.min(quality_errors)) > tolerance:
            return "FAIL", "alignment drift exceeds tolerance", max_abs, drift, span
    if float(np.min(correlations)) < minimum_correlation or float(np.min(confidences)) < confidence_floor:
        return "PENDING_REVIEW", "low-confidence audio correlation", max_abs, drift, span
    if span > tolerance:
        return "FAIL", "alignment drift exceeds tolerance", max_abs, drift, span
    if max_abs > tolerance:
        return "FAIL", "alignment offset exceeds tolerance", max_abs, drift, span
    return "PASS", "multi-position alignment verified", max_abs, drift, span


def verify_audio_arrays(
    full_env: np.ndarray,
    range_env: np.ndarray,
    handled_start: float,
    *,
    duration_seconds: float | None = None,
    tolerance: float = TOL,
    confidence_floor: float = CONF_FLOOR,
    minimum_correlation: float = AUDIO_MIN_CORR,
    window_seconds: float = ANCHOR_WINDOW_SECONDS,
) -> dict[str, Any]:
    """Verify a range audio signal against the full source at three positions."""

    full = np.asarray(full_env, dtype=np.float32)
    probe = np.asarray(range_env, dtype=np.float32)
    if duration_seconds is None:
        duration_seconds = len(probe) / HZ
    positions = anchor_positions(float(duration_seconds), window_seconds)
    anchors: list[dict[str, Any]] = []
    window_frames = max(2 * HZ, int(round(window_seconds * HZ)))
    for local in positions:
        begin = int(round(local * HZ))
        end = min(len(probe), begin + window_frames)
        segment = probe[begin:end]
        expected = float(handled_start) + local
        result = _search_audio_window(full, segment, expected)
        anchors.append(
            {
                "local_seconds": round(local, 3),
                "expected_source_seconds": round(expected, 3),
                "observed_source_seconds": round(result["observed_seconds"], 3),
                "offset_error_seconds": round(result["observed_seconds"] - expected, 3),
                "correlation": round(result["correlation"], 4),
                "confidence": round(result["confidence"], 2),
            }
        )
    status, reason, max_abs, drift, span = _alignment_status(
        anchors,
        tolerance=tolerance,
        confidence_floor=confidence_floor,
        minimum_correlation=minimum_correlation,
    )
    return {
        "status": status,
        "reason": reason,
        "anchors": anchors,
        "max_absolute_offset_seconds": round(max_abs, 3),
        "drift_seconds": round(drift, 3),
        "offset_span_seconds": round(span, 3),
        "minimum_correlation": round(
            min((float(item["correlation"]) for item in anchors), default=0.0), 4
        ),
        "minimum_confidence": round(
            min((float(item["confidence"]) for item in anchors), default=0.0), 2
        ),
        "expected_handled_start": round(float(handled_start), 3),
        "independent": True,
    }


def _same_seconds(left: Any, right: Any, tolerance: float = 1e-3) -> bool:
    try:
        return abs(float(left) - float(right)) <= tolerance
    except (TypeError, ValueError):
        return left == right


def _time_seconds(value: Any) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value)
    hours, minutes, seconds = text.split(":")
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)


def _bound_evidence_file(
    evidence: Mapping[str, Any], base_dir: str | os.PathLike[str] | None = None
) -> bool:
    """Require a current, hash-bound evidence file rather than free text."""

    evidence_path = evidence.get("evidence_path") or evidence.get("record_path")
    evidence_hash = evidence.get("evidence_sha256") or evidence.get("record_sha256")
    if not evidence_path or not evidence_hash:
        return False
    path = Path(str(evidence_path)).expanduser()
    if not path.is_file() and base_dir:
        path = Path(base_dir) / path
    if not path.is_file():
        return False
    try:
        return sha256_file(path) == str(evidence_hash)
    except OSError:
        return False


def explicit_transcript_verification(
    candidate: Mapping[str, Any],
    *,
    current_video_sha256: str | None = None,
    current_source_audio_sha256: str | None = None,
    current_assembly_audio_sha256: str | None = None,
    base_dir: str | os.PathLike[str] | None = None,
) -> dict[str, Any] | None:
    """Return only a current, explicitly documented transcript review.

    A status field or generic note is not enough.  The record must bind the
    exact current video and full-audio assets, the original source/handled
    offsets, an evidence file digest, a reviewer, and observed transcript
    anchors.  This prevents a previous run's low-confidence sign-off from
    silently authorizing a changed range.
    """

    possible = (
        candidate.get("alignment_transcript_verification"),
        candidate.get("transcript_alignment_verification"),
        candidate.get("audio_transcript_verification"),
    )
    for value in possible:
        if not isinstance(value, Mapping):
            continue
        status = str(value.get("status", "")).upper()
        method = str(value.get("method", "")).lower()
        if status not in {"PASS", "VERIFIED"} or "transcript" not in method:
            continue
        if not _bound_evidence_file(value, base_dir):
            continue
        if not value.get("reviewer") or not value.get("observed_anchor_notes"):
            continue
        anchors = value.get("observed_anchors")
        if not isinstance(anchors, list) or not anchors:
            continue
        if current_video_sha256 and value.get("video_sha256") != current_video_sha256:
            continue
        if current_source_audio_sha256 and value.get("source_audio_sha256") != current_source_audio_sha256:
            continue
        if current_assembly_audio_sha256:
            bound_audio_hash = value.get("assembly_audio_sha256") or value.get("audio_sha256")
            if bound_audio_hash != current_assembly_audio_sha256:
                continue
        if not _same_seconds(value.get("handled_start"), candidate.get("handled_start")):
            continue
        if not _same_seconds(value.get("handled_end"), candidate.get("handled_end")):
            continue
        try:
            source_in = _time_seconds(value.get("source_in"))
            source_out = _time_seconds(value.get("source_out"))
            candidate_in = _time_seconds(candidate.get("source_in"))
            candidate_out = _time_seconds(candidate.get("source_out"))
        except (TypeError, ValueError):
            continue
        if not _same_seconds(source_in, candidate_in) or not _same_seconds(source_out, candidate_out):
            continue
        return dict(value)
    return None


def resolve_low_confidence_audio(
    report: Mapping[str, Any],
    candidate: Mapping[str, Any],
    *,
    current_video_sha256: str | None = None,
    current_source_audio_sha256: str | None = None,
    current_assembly_audio_sha256: str | None = None,
    base_dir: str | os.PathLike[str] | None = None,
) -> dict[str, Any]:
    """Apply the documented transcript fallback to a pending audio result."""

    updated = dict(report)
    evidence = explicit_transcript_verification(
        candidate,
        current_video_sha256=current_video_sha256,
        current_source_audio_sha256=current_source_audio_sha256,
        current_assembly_audio_sha256=current_assembly_audio_sha256,
        base_dir=base_dir,
    )
    if str(report.get("status", "")).upper() == "PENDING_REVIEW" and evidence:
        updated["status"] = "PASS"
        updated["resolution"] = "explicit_transcript_verification"
        updated["transcript_verification"] = evidence
        updated["confidence"] = "low"
        updated["reason"] = "low-confidence correlation resolved by explicit transcript verification"
    return updated


def _resolve_low_confidence_proxy_audio(
    report: Mapping[str, Any],
    evidence: Mapping[str, Any] | None,
    proxy: str,
    full_audio: str,
    *,
    base_dir: str | os.PathLike[str] | None = None,
) -> dict[str, Any]:
    """Resolve a native-proxy low-confidence result only with bound evidence."""

    updated = dict(report)
    if not isinstance(evidence, Mapping):
        return updated
    method = str(evidence.get("method", "")).lower()
    if (
        str(report.get("status", "")).upper() != "PENDING_REVIEW"
        or "transcript" not in method
        or str(evidence.get("status", "")).upper() not in {"PASS", "VERIFIED"}
        or not _bound_evidence_file(evidence, base_dir)
        or not evidence.get("reviewer")
        or not evidence.get("observed_anchor_notes")
        or not isinstance(evidence.get("observed_anchors"), list)
        or len(evidence.get("observed_anchors", [])) < 3
        or not _same_seconds(evidence.get("source_start_seconds"), 0.0)
    ):
        return updated
    proxy_hash = sha256_file(proxy) if os.path.isfile(proxy) else None
    source_hash = sha256_file(full_audio) if os.path.isfile(full_audio) else None
    if (
        not proxy_hash
        or not source_hash
        or evidence.get("proxy_sha256") != proxy_hash
        or evidence.get("source_audio_sha256") != source_hash
    ):
        return updated
    updated["status"] = "PASS"
    updated["resolution"] = "explicit_transcript_verification"
    updated["transcript_verification"] = dict(evidence)
    updated["confidence"] = "low"
    updated["reason"] = "low-confidence proxy audio resolved by explicit transcript verification"
    return updated


def _capture_frame(path: str | os.PathLike[str], seconds: float) -> np.ndarray:
    """Capture a small grayscale frame for visual anchor comparison."""

    command = [
        "ffmpeg",
        "-hide_banner",
        "-v",
        "error",
        "-ss",
        f"{max(0.0, float(seconds)):.3f}",
        "-i",
        str(path),
        "-frames:v",
        "1",
        "-vf",
        "scale=64:36:force_original_aspect_ratio=decrease,pad=64:36:(ow-iw)/2:(oh-ih)/2",
        "-pix_fmt",
        "gray",
        "-f",
        "rawvideo",
        "-",
    ]
    result = subprocess.run(command, capture_output=True, check=True)
    frame = np.frombuffer(result.stdout, dtype=np.uint8)
    if len(frame) != 64 * 36:
        raise RuntimeError(f"unexpected frame size from {path}: {len(frame)}")
    return frame.astype(np.float32)


def _frame_match(left: np.ndarray, right: np.ndarray) -> tuple[float, float]:
    """Return normalized correlation and mean absolute error for two frames."""

    a = np.asarray(left, dtype=np.float32)
    b = np.asarray(right, dtype=np.float32)
    if a.shape != b.shape or not a.size:
        return 0.0, 1.0
    scale = max(float(np.max(np.abs(a))), float(np.max(np.abs(b))), 1.0)
    mae = float(np.mean(np.abs(a - b)) / scale)
    return _normalized_correlation(a, b), mae


def verify_visual_frames(
    proxy_reader: Callable[[float], np.ndarray],
    range_reader: Callable[[float], np.ndarray],
    handled_start: float,
    duration_seconds: float,
    *,
    tolerance: float = TOL,
    minimum_correlation: float = VISUAL_MIN_CORR,
    search_seconds: float = SEARCH_SECONDS,
    window_seconds: float = ANCHOR_WINDOW_SECONDS,
) -> dict[str, Any]:
    """Compare early/middle/late range frames against a trusted full proxy."""

    anchors: list[dict[str, Any]] = []
    deltas = np.arange(-float(search_seconds), float(search_seconds) + 0.001, 0.25)
    for local in anchor_positions(duration_seconds, window_seconds):
        try:
            reference = np.asarray(range_reader(local), dtype=np.float32)
            matches: list[tuple[float, float, float]] = []
            for delta in deltas:
                try:
                    candidate = np.asarray(proxy_reader(float(handled_start) + local + float(delta)), dtype=np.float32)
                except Exception:
                    continue
                corr, mae = _frame_match(reference, candidate)
                matches.append((corr, mae, float(delta)))
        except Exception as exc:
            anchors.append(
                {
                    "local_seconds": round(local, 3),
                    "status": "PENDING_REVIEW",
                    "reason": f"frame capture failed: {exc}",
                }
            )
            continue
        if not matches:
            anchors.append(
                {
                    "local_seconds": round(local, 3),
                    "status": "PENDING_REVIEW",
                    "reason": "no comparable proxy frame",
                }
            )
            continue
        matches.sort(key=lambda item: (item[0], -item[1]), reverse=True)
        best = matches[0]
        # A decoded frame can remain identical over adjacent search samples.
        # Average the tied deltas so a plateau centred on the expected time is
        # not reported as a false half-second offset.
        tied = [item for item in matches if abs(item[0] - best[0]) <= 1e-4]
        best_delta = float(np.mean([item[2] for item in tied]))
        best_mae = min(item[1] for item in tied)
        lower = [item[0] for item in matches if item[0] < best[0] - 1e-4]
        second = max(lower) if lower else best[0]
        margin = float(best[0] - second)
        # A flat match curve means the visual is repeated/static and cannot
        # establish a time position, even when the best frame looks similar.
        confidence = max(0.0, margin / 0.01)
        status = "PASS"
        reason = "visual anchor matched"
        if best[0] < minimum_correlation or confidence < 0.5:
            status = "PENDING_REVIEW"
            reason = "low-confidence visual anchor"
        anchors.append(
            {
                "local_seconds": round(local, 3),
                "expected_source_seconds": round(float(handled_start) + local, 3),
                "best_delta_seconds": round(best_delta, 3),
                "correlation": round(best[0], 4),
                "mae": round(best_mae, 4),
                "margin": round(margin, 4),
                "confidence": round(confidence, 2),
                "status": status,
                "reason": reason,
            }
        )

    usable = [item for item in anchors if "best_delta_seconds" in item]
    if not anchors or not usable:
        status = "PENDING_REVIEW"
        reason = "no usable visual anchors"
        drift = 0.0
        max_delta = 0.0
    else:
        offsets = np.asarray([float(item["best_delta_seconds"]) for item in usable])
        drift = float(offsets[-1] - offsets[0]) if len(offsets) > 1 else 0.0
        max_delta = float(np.max(np.abs(offsets)))
        if any(item.get("status") != "PASS" for item in anchors):
            status = "PENDING_REVIEW"
            reason = "low-confidence visual anchor"
        elif float(np.max(offsets) - np.min(offsets)) > tolerance:
            status = "FAIL"
            reason = "visual alignment drift exceeds tolerance"
        elif max_delta > tolerance:
            status = "FAIL"
            reason = "visual alignment offset exceeds tolerance"
        else:
            status = "PASS"
            reason = "multi-position visual alignment verified"
    return {
        "status": status,
        "reason": reason,
        "anchors": anchors,
        "max_absolute_delta_seconds": round(max_delta, 3),
        "drift_seconds": round(drift, 3),
        "minimum_correlation": round(
            min((float(item.get("correlation", 0.0)) for item in usable), default=0.0), 4
        ),
        "independent": True,
    }


def verify_visual_alignment(
    proxy_path: str | os.PathLike[str],
    range_path: str | os.PathLike[str],
    handled_start: float,
    duration_seconds: float,
    **kwargs: Any,
) -> dict[str, Any]:
    """File-backed wrapper around :func:`verify_visual_frames`."""

    return verify_visual_frames(
        lambda seconds: _capture_frame(proxy_path, seconds),
        lambda seconds: _capture_frame(range_path, seconds),
        handled_start,
        duration_seconds,
        **kwargs,
    )


def verify_proxy_alignment(
    state: Mapping[str, Any],
    *,
    full_audio_env: np.ndarray | None = None,
    proxy_audio_env: np.ndarray | None = None,
) -> dict[str, Any]:
    """Establish that the full analysis proxy is a trusted source timeline.

    A proxy with native audio gets an independent multi-position check.  A
    video-only proxy requires an explicit source record with ``independent``
    evidence.  Merely cutting replacement audio from ``full_audio`` is
    rejected as circular evidence.
    """

    source = state.get("source", {}) if isinstance(state, Mapping) else {}
    proxy = source.get("proxy") or state.get("proxy")
    full_audio = source.get("full_audio") or state.get("full_audio")
    if not proxy or not full_audio:
        return {
            "status": "PENDING_REVIEW",
            "reason": "full proxy and full audio are required",
            "method": "none",
            "independent": False,
        }

    if has_stream(str(proxy), "a:0"):
        try:
            full = full_audio_env if full_audio_env is not None else env_of(str(full_audio))
            proxy_env = proxy_audio_env if proxy_audio_env is not None else env_of(str(proxy), stream="a:0")
            duration = len(proxy_env) / HZ
            report = verify_audio_arrays(full, proxy_env, 0.0, duration_seconds=duration)
            report["method"] = "native_proxy_audio"
            report["proxy"] = os.path.abspath(str(proxy))
            report["full_audio"] = os.path.abspath(str(full_audio))
            report = _resolve_low_confidence_proxy_audio(
                report,
                source.get("proxy_alignment") or state.get("proxy_alignment"),
                str(proxy),
                str(full_audio),
                base_dir=state.get("_project"),
            )
            return report
        except Exception as exc:
            return {
                "status": "PENDING_REVIEW",
                "reason": f"native proxy audio verification failed: {exc}",
                "method": "native_proxy_audio",
                "independent": False,
            }

    evidence = source.get("proxy_alignment") or state.get("proxy_alignment")
    if isinstance(evidence, Mapping):
        status = str(evidence.get("status", "")).upper()
        method = str(evidence.get("method", "")).lower()
        independent = bool(evidence.get("independent"))
        forbidden = ("replacement", "range audio", "cut audio", "planned audio")
        proxy_hash = sha256_file(str(proxy)) if os.path.isfile(str(proxy)) else None
        source_hash = sha256_file(str(full_audio)) if os.path.isfile(str(full_audio)) else None
        anchors = evidence.get("observed_anchors")
        offsets_bound = _same_seconds(evidence.get("source_start_seconds", 0.0), 0.0)
        hashes_bound = (
            bool(proxy_hash)
            and bool(source_hash)
            and evidence.get("proxy_sha256") == proxy_hash
            and evidence.get("source_audio_sha256") == source_hash
        )
        evidence_file_bound = _bound_evidence_file(evidence)
        if (
            status in {"PASS", "VERIFIED"}
            and independent
            and not any(word in method for word in forbidden)
            and hashes_bound
            and evidence_file_bound
            and evidence.get("reviewer")
            and isinstance(anchors, list)
            and len(anchors) >= 3
            and offsets_bound
        ):
            return {
                "status": "PASS",
                "reason": "explicit independent proxy alignment evidence",
                "method": evidence.get("method"),
                "evidence": dict(evidence),
                "proxy": os.path.abspath(str(proxy)),
                "full_audio": os.path.abspath(str(full_audio)),
                "independent": True,
            }
    return {
        "status": "PENDING_REVIEW",
        "reason": "video-only proxy lacks independent alignment evidence",
        "method": "video_only_proxy_without_independent_evidence",
        "proxy": os.path.abspath(str(proxy)),
        "full_audio": os.path.abspath(str(full_audio)),
        "independent": False,
    }


def verify_candidate_video(
    candidate: Mapping[str, Any],
    state: Mapping[str, Any],
    *,
    full_audio_env: np.ndarray | None = None,
    proxy_alignment: Mapping[str, Any] | None = None,
    native_audio_present: bool | None = None,
    native_audio_env: np.ndarray | None = None,
    visual_report: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Verify the actual acquired video against the source timeline."""

    video = str(candidate.get("video_file", ""))
    source = state.get("source", {})
    full_audio = source.get("full_audio")
    proxy = source.get("proxy")
    handled_start = float(candidate.get("handled_start", 0.0))
    handled_end = float(candidate.get("handled_end", handled_start))
    duration = max(0.0, handled_end - handled_start)

    if native_audio_present is None:
        native_audio_present = has_stream(video, "a:0") if video else False
    if native_audio_present:
        try:
            full = full_audio_env if full_audio_env is not None else env_of(str(full_audio))
            native = native_audio_env if native_audio_env is not None else env_of(video, stream="a:0")
            report = verify_audio_arrays(full, native, handled_start, duration_seconds=duration)
            report["method"] = "native_video_audio"
            report["video"] = os.path.abspath(video)
            report["full_audio"] = os.path.abspath(str(full_audio))
            report = resolve_low_confidence_audio(
                report,
                candidate,
                current_video_sha256=sha256_file(video) if os.path.isfile(video) else None,
                current_source_audio_sha256=(
                    sha256_file(str(full_audio)) if full_audio and os.path.isfile(str(full_audio)) else None
                ),
                current_assembly_audio_sha256=(
                    sha256_file(str(candidate.get("audio_file")))
                    if candidate.get("audio_file") and os.path.isfile(str(candidate.get("audio_file")))
                    else None
                ),
                base_dir=state.get("_project"),
            )
            return report
        except Exception as exc:
            return {
                "status": "PENDING_REVIEW",
                "reason": f"native video audio verification failed: {exc}",
                "method": "native_video_audio",
                "video": os.path.abspath(video),
                "independent": False,
            }

    trusted_proxy = dict(proxy_alignment or {})
    if str(trusted_proxy.get("status", "")).upper() != "PASS":
        return {
            "status": "PENDING_REVIEW",
            "reason": "video-only range requires a trusted aligned full proxy",
            "method": "visual_anchors_against_proxy",
            "proxy_alignment": trusted_proxy,
            "video": os.path.abspath(video),
            "independent": False,
        }
    try:
        visual = dict(visual_report or verify_visual_alignment(str(proxy), video, handled_start, duration))
        visual["method"] = "visual_anchors_against_trusted_proxy"
        visual["video"] = os.path.abspath(video)
        visual["proxy"] = os.path.abspath(str(proxy))
        visual["proxy_alignment"] = trusted_proxy
        return visual
    except Exception as exc:
        return {
            "status": "PENDING_REVIEW",
            "reason": f"visual anchor verification failed: {exc}",
            "method": "visual_anchors_against_trusted_proxy",
            "video": os.path.abspath(video),
            "proxy": os.path.abspath(str(proxy)) if proxy else None,
            "independent": False,
        }


__all__ = [
    "AUDIO_MIN_CORR",
    "CONF_FLOOR",
    "HZ",
    "TOL",
    "anchor_positions",
    "env_from_raw",
    "env_of",
    "explicit_transcript_verification",
    "find_offset",
    "has_stream",
    "media_duration",
    "resolve_low_confidence_audio",
    "sha256_file",
    "verify_audio_arrays",
    "verify_candidate_video",
    "verify_proxy_alignment",
    "verify_visual_alignment",
    "verify_visual_frames",
]
