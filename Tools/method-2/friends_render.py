#!/usr/bin/env python3
"""Render the approved Friends spans with the prepared bilingual alpha track.

This is deliberately a bounded renderer: each output is one continuous source
range, with only the approved subtitle alpha track composited over the picture.
No reframing, silence removal, cuts, graphics, music, or B-roll are introduced.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
from pathlib import Path


FPS = "24000/1001"


def probe(path: Path, entries: str) -> dict:
    p = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", entries, "-of", "json", str(path)],
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(p.stdout)


def run(cmd: list[str], capture: bool = False) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, check=True, text=True, capture_output=capture)


def duration(path: Path) -> float:
    d = probe(path, "format=duration").get("format", {}).get("duration", 0)
    return float(d or 0)


def loudnorm_stats(audio: Path) -> dict[str, float]:
    """Obtain first-pass EBU R128 measurements for deterministic loudnorm."""
    p = subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-nostats", "-v", "info", "-i", str(audio),
            "-af", "loudnorm=I=-14:TP=-2:LRA=11:print_format=json",
            "-f", "null", "-",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    text = p.stderr
    blobs = re.findall(r"\{\s*\"input_i\"[\s\S]*?\}", text)
    if not blobs:
        raise RuntimeError(f"loudnorm did not return JSON for {audio}")
    data = json.loads(blobs[-1])
    out: dict[str, float] = {}
    for key in ("input_i", "input_tp", "input_lra", "input_thresh", "target_offset"):
        out[key] = float(data[key])
    return out


def valid_existing(path: Path, target: float, width: int, height: int) -> bool:
    try:
        d = probe(path, "format=duration:stream=codec_name,width,height,pix_fmt,avg_frame_rate,profile,codec_type")
        s = next(x for x in d.get("streams", []) if x.get("codec_type") == "video")
        return (
            abs(float(d["format"]["duration"]) - target) < 0.2
            and s.get("codec_name") == "h264"
            and int(s.get("width", 0)) == width
            and int(s.get("height", 0)) == height
        )
    except Exception:
        return False


def render_candidate(cand: dict, run_dir: Path, force: bool = False,
                     width: int | None = None, height: int | None = None) -> dict:
    slug = cand["slug"]
    target = float(cand["source_out_seconds"]) - float(cand["source_in_seconds"])
    range_video = run_dir / "source" / "ranges" / f"{slug}.mp4"
    range_audio = run_dir / "source" / "ranges" / f"{slug}-audio.wav"
    subtitles = run_dir / "clips" / slug / "subtitles.mov"
    work = run_dir / "clips" / slug
    work.mkdir(parents=True, exist_ok=True)
    out = work / f"{slug}.mp4"
    media_in = float(cand["media_in_seconds"])
    if not range_video.exists() or not range_audio.exists() or not subtitles.exists():
        raise FileNotFoundError(f"missing approved inputs for {slug}")
    dimensions = probe(range_video, "stream=width,height,codec_type").get("streams", [])
    video_stream = next(x for x in dimensions if x.get("codec_type") == "video")
    width = int(width or video_stream.get("width", 0))
    height = int(height or video_stream.get("height", 0))
    if width <= 0 or height <= 0:
        raise RuntimeError(f"could not determine source resolution for {slug}")
    if not force and valid_existing(out, target, width, height):
        return {"slug": slug, "output": str(out), "status": "reused", "duration": duration(out),
                "width": width, "height": height}

    # Cut the already acquired handled audio at the exact approved source
    # offset.  The handled WAV is 48 kHz stereo, so this seek is sample-safe.
    audio_cut = work / "clip-audio.wav"
    run([
        "ffmpeg", "-hide_banner", "-v", "error", "-ss", f"{media_in:.3f}",
        "-i", str(range_audio), "-t", f"{target:.3f}", "-c:a", "pcm_s24le",
        "-ar", "48000", "-ac", "2", str(audio_cut), "-y",
    ])
    stats = loudnorm_stats(audio_cut)
    filter_a = (
        "loudnorm=I=-14:TP=-2:LRA=11:"
        f"measured_I={stats['input_i']}:measured_TP={stats['input_tp']}:"
        f"measured_LRA={stats['input_lra']}:measured_thresh={stats['input_thresh']}:"
        f"offset={stats['target_offset']}:linear=true:print_format=none,"
        "alimiter=limit=0.72:level=disabled"
    )
    # The source video is one continuous stream.  -ss/-t are applied to that
    # stream only; the final encode retains the source's horizontal framing and
    # 24000/1001 cadence while overlaying the pre-rendered alpha track.
    run([
        "ffmpeg", "-hide_banner", "-v", "error", "-ss", f"{media_in:.3f}",
        "-i", str(range_video), "-i", str(subtitles), "-i", str(audio_cut),
        "-filter_complex", "[0:v][1:v]overlay=0:0:shortest=0[v];"
        f"[2:a]{filter_a}[a]",
        "-map", "[v]", "-map", "[a]", "-t", f"{target:.3f}",
        "-r", FPS, "-fps_mode", "cfr", "-c:v", "libx264", "-preset", "medium",
        "-crf", "18", "-profile:v", "high", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
        "-map_metadata", "-1", "-movflags", "+faststart", str(out), "-y",
    ])
    if not out.exists() or duration(out) < target - 0.2:
        raise RuntimeError(f"rendered output is short for {slug}")
    return {
        "slug": slug,
        "output": str(out),
        "status": "rendered",
        "duration": duration(out),
        "loudnorm_first_pass": stats,
        "target_duration": target,
        "media_in_seconds": media_in,
        "width": width,
        "height": height,
        "native_source_resolution": True,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", required=True)
    ap.add_argument("--slugs", nargs="*", default=None,
                    help="render only these approved slugs")
    ap.add_argument("--force", action="store_true",
                    help="replace generated outputs for the selected slugs")
    ap.add_argument("--width", type=int, default=None,
                    help="override output width; defaults to approved source width")
    ap.add_argument("--height", type=int, default=None,
                    help="override output height; defaults to approved source height")
    args = ap.parse_args()
    run_dir = Path(args.run).resolve()
    plan = json.loads((run_dir / "plan.json").read_text())
    rows = []
    wanted = set(args.slugs) if args.slugs else None
    for cand in plan["candidates"]:
        if wanted is not None and cand["slug"] not in wanted:
            continue
        rec = render_candidate(cand, run_dir, force=args.force,
                                width=args.width, height=args.height)
        rows.append(rec)
        print(f"{cand['slug']}: {rec['status']} -> {rec['output']}")
    manifest_path = run_dir / "qa" / "render-manifest.json"
    existing_rows = {}
    if manifest_path.exists():
        try:
            existing_rows = {r["slug"]: r for r in json.loads(manifest_path.read_text()).get("candidates", [])}
        except Exception:
            existing_rows = {}
    existing_rows.update({r["slug"]: r for r in rows})
    all_rows = [existing_rows[c["slug"]] for c in plan["candidates"] if c["slug"] in existing_rows]
    manifest_path.write_text(json.dumps({
        "schema_version": "friends-render-manifest-1",
        "video": {
            "codec": "H.264 High",
            "pix_fmt": "yuv420p",
            "resolution": [
                all_rows[0].get("width", 1920) if all_rows else (args.width or 1920),
                all_rows[0].get("height", 1080) if all_rows else (args.height or 1080),
            ],
            "fps": FPS,
            "framing": "original horizontal source",
        },
        "audio": {"codec": "AAC", "channels": 2, "sample_rate": 48000, "bitrate": 192000,
                  "target_lufs": -14, "true_peak_max_db": -1},
        "subtitles": "approved bilingual alpha tracks only",
        "candidates": all_rows,
    }, ensure_ascii=False, indent=2) + "\n")


if __name__ == "__main__":
    main()
