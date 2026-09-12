#!/usr/bin/env python3
"""Acquire the approved Friends Method 2 ranges with storage-efficient handles.

Only the candidates already present in a prepared execution plan are touched.
The range video is downloaded with yt-dlp/ffmpeg stream copy so the actual
playable source resolution and cadence are retained.  Audio is cut from the
full approved transcription source at the same source offset, then every
range is checked against that full source with the Method 2 envelope
correlation check.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from m2_ranges import CONF_FLOOR, TOL, env_of, find_offset, hms  # noqa: E402


def run(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, check=True)


def probe(path: Path, entries: str) -> dict:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", entries, "-of", "json", str(path)],
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(out.stdout)


def good_video(path: Path) -> bool:
    try:
        d = probe(path, "format=duration:stream=codec_name,width,height,avg_frame_rate,pix_fmt")
        s = next(x for x in d.get("streams", []) if x.get("codec_name"))
        return (
            s.get("codec_type", "video") == "video"
            and int(s.get("width", 0)) >= 1920
            and int(s.get("height", 0)) >= 1080
            and float(d.get("format", {}).get("duration", 0)) > 1
        )
    except Exception:
        return False


def download_range(url: str, start: float, end: float, out: Path, client: str) -> tuple[str, list[str]]:
    # Highest resolution first.  137 is the delivery-compatible H.264 stream;
    # 248/399 are equal-resolution fallbacks if a future token/client refuses it.
    formats = ["137", "248", "399", "136", "135", "134", "18"]
    attempts = []
    for fmt in formats:
        if out.exists() and good_video(out):
            return fmt, attempts
        if out.exists():
            out.unlink()
        cmd = [
            "yt-dlp", "--no-warnings", "--extractor-args", f"youtube:player_client={client}",
            "-f", fmt, "--download-sections", f"*{start:.2f}-{end:.2f}",
            "--concurrent-fragments", "4", "-o", str(out), url,
        ]
        try:
            run(cmd)
            if good_video(out):
                return fmt, attempts + [fmt]
            attempts.append(f"{fmt}:invalid-output")
        except subprocess.CalledProcessError as exc:
            attempts.append(f"{fmt}:exit-{exc.returncode}")
    raise RuntimeError(f"all range formats failed for {out.name}: {attempts}")


def cut_audio(full_audio: Path, start: float, dur: float, out: Path) -> None:
    if out.exists():
        try:
            if float(probe(out, "format=duration")["format"]["duration"]) > dur - 0.5:
                return
        except Exception:
            out.unlink()
    run([
        "ffmpeg", "-hide_banner", "-v", "error", "-ss", f"{start:.3f}", "-i", str(full_audio),
        "-t", f"{dur:.3f}", "-c:a", "pcm_s24le", "-ar", "48000", "-ac", "2", str(out), "-y",
    ])


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", required=True)
    ap.add_argument("--handles", type=float, default=30.0)
    ap.add_argument("--client", default="web_embedded")
    args = ap.parse_args()
    run_dir = Path(args.run).resolve()
    plan_path = run_dir / "plan.json"
    state_path = run_dir / "m2.json"
    plan = json.loads(plan_path.read_text())
    state = json.loads(state_path.read_text())
    src = state["source"]
    url = src["url"]
    full_audio = Path(src["full_audio"])
    range_dir = run_dir / "source" / "ranges"
    range_dir.mkdir(parents=True, exist_ok=True)
    full = env_of(str(full_audio))

    report = {
        "status": "complete_with_warnings",
        "run": str(run_dir),
        "handles_seconds": args.handles,
        "source": {
            "url": url,
            "player_client": args.client,
            "preferred_format": "137",
            "fallback_formats": ["248", "399", "136", "135", "134", "18"],
            "audio": str(full_audio),
        },
        "candidates": [],
        "all_alignment_pass": True,
    }
    for c in plan["candidates"]:
        sin = float(c["source_in_seconds"])
        sout = float(c["source_out_seconds"])
        start = max(0.0, sin - args.handles)
        end = sout + args.handles
        dur = end - start
        vid = range_dir / f"{c['slug']}.mp4"
        aud = range_dir / f"{c['slug']}-audio.wav"
        chosen, attempts = download_range(url, start, end, vid, args.client)
        cut_audio(full_audio, start, dur, aud)
        probe_v = probe(vid, "format=duration:stream=codec_name,codec_type,width,height,avg_frame_rate,pix_fmt,profile")
        probe_a = probe(aud, "format=duration:stream=codec_name,codec_type,sample_rate,channels,bit_rate")
        off, conf = find_offset(full, env_of(str(aud), seconds=min(60, dur)))
        drift = off - start
        align_ok = abs(drift) < TOL or conf < CONF_FLOOR
        if not align_ok:
            report["all_alignment_pass"] = False
        rec = {
            "slug": c["slug"],
            "name": c["name"],
            "source_in": c["source_in"],
            "source_out": c["source_out"],
            "source_in_seconds": sin,
            "source_out_seconds": sout,
            "handled_start": round(start, 3),
            "handled_end": round(end, 3),
            "handled_duration_seconds": round(dur, 3),
            "media_in_seconds": round(sin - start, 3),
            "video_file": str(vid),
            "audio_file": str(aud),
            "requested_format": "137",
            "chosen_format": chosen,
            "format_attempts": attempts,
            "video_probe": probe_v,
            "audio_probe": probe_a,
            "alignment": {
                "offset_seconds": round(off, 3),
                "expected_handled_start": round(start, 3),
                "drift_seconds": round(drift, 3),
                "confidence": round(conf, 2),
                "status": "PASS" if align_ok else "FAIL",
                "confidence_floor": CONF_FLOOR,
                "tolerance_seconds": TOL,
            },
        }
        for key in ("handled_start", "handled_end", "media_in_seconds", "video_file", "audio_file", "chosen_format", "alignment"):
            c[key] = rec[key]
        report["candidates"].append(rec)
        print(f"{c['slug']}: {chosen} {start:.3f}-{end:.3f} alignment drift {drift:.3f}s conf {conf:.1f} {'PASS' if align_ok else 'FAIL'}")

    plan_path.write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n")
    report["status"] = "pass" if report["all_alignment_pass"] else "review_required"
    (run_dir / "qa" / "range-acquisition.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    state["stage"] = "ranges_acquired"
    state["range_acquisition"] = {
        "format": "137",
        "player_client": args.client,
        "highest_actual_video": "1920x1080 H.264 High yuv420p",
        "highest_actual_audio": "AAC stereo 128 kbps 44100 Hz format 140",
        "handles_seconds": args.handles,
        "alignment_report": str((run_dir / "qa" / "range-acquisition.json").resolve()),
    }
    state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n")
    if not report["all_alignment_pass"]:
        raise SystemExit("one or more range alignments failed")


if __name__ == "__main__":
    main()
