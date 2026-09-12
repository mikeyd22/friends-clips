#!/usr/bin/env python3
"""Method 2 — stage 5: build one clip from its EDL.

Renders per beat and concatenates. A single filter graph stalls on reordered
edits, because concat blocks waiting for a beat sourced from late in the file
while every earlier decoded frame buffers.

Then: 9:16 crop, hook title overlay, two-pass loudness normalisation, and the
three gates.
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from m2_hook import render as render_hook, choose_y as choose_hook_y  # noqa: E402
from m2_policy import duration_policy  # noqa: E402
from m2_quality import (  # noqa: E402
    alignment_gate,
    audio_content_gate,
    delivery_gate,
    parse_freezedetect,
    presentation_cadence,
    rendered_vtt_path,
    resolve_declared_path,
    sha256_file,
    source_visual_metadata,
    transcribe_rendered_vtt,
    validate_review_record,
    write_nonverbal_vtt,
)
from runtime import whisper_command, whisper_model

FADE = 0.015
OUT_W, OUT_H = 1080, 1920
BAND = (0, 35)    # no floor as of 2026-08-17: a clip may be as short as its
                  # content. 33 is the preferred ceiling; 33-35 only when the
                  # cut needs the extra context. Never extend a clip at the
                  # front to reach a length. See the spec's duration_band.
LUFS, LUFS_TOL, TP_CEIL = -14.0, 2.0, -1.0
WHISPER = whisper_command()
MODEL = whisper_model()
BUILD_CACHE_VERSION = "method-2-per-beat-v2"


def run(cmd, **kw):
    return subprocess.run(cmd, check=True, **kw)


def probe(path, entries, stream=None):
    cmd = ["ffprobe", "-v", "error"]
    if stream:
        cmd += ["-select_streams", stream]
    cmd += ["-show_entries", entries, "-of", "default=nw=1:nk=1", path]
    return subprocess.run(cmd, capture_output=True, text=True, check=True).stdout.strip()


def source_fps(video):
    """The spec says follow source cadence. Forcing 60000/1001 resampled every
    60/1 source, dropping about one frame every 16.7s. Read it, don't assume it.
    """
    for entry in ("stream=r_frame_rate", "stream=avg_frame_rate"):
        v = probe(video, entry, "v:0").split("\n")[0].strip()
        if "/" in v:
            num, den = v.split("/")
            if float(den) > 0 and float(num) > 0:
                return v
    raise SystemExit(f"cannot read a usable frame rate from {video} — "
                     "ffprobe reports no valid r_frame_rate or avg_frame_rate")


def crop_plan(edl, src_w, src_h):
    """Locked centre unless the EDL supplies explicit per-beat crops.

    Centre is the reliable default, not a fallback: face, appearance and motion
    tracking were each tried across two pilot clips and none beat it. IRL
    operators keep the subject centred. Genuine tracking needs a person
    detection model that is not installed here.
    """
    crop_w = int(round(src_h * 9 / 16))
    centre = (src_w - crop_w) // 2
    beats = edl.get("crop") or [{"mode": "static", "x": centre} for _ in edl["beats"]]
    return crop_w, src_h, beats


def source_identity(path):
    """Cheap source identity for resumable draft beat renders.

    Size and nanosecond mtime are enough to invalidate a local draft cache
    without hashing a multi-gigabyte range for every beat.  Durable review and
    alignment records use :func:`m2_quality.sha256_file` separately.
    """
    resolved = os.path.abspath(os.path.expanduser(str(path)))
    stat = os.stat(resolved)
    return {"path": resolved, "size": int(stat.st_size), "mtime_ns": int(stat.st_mtime_ns)}


def _cache_json(path):
    try:
        with open(path) as handle:
            return json.load(handle)
    except (OSError, json.JSONDecodeError):
        return None


def beat_cache_key(video, audio, unit, fps, cw, ch, audio_offset, *, quality=True):
    """Return the exact identity used to reuse one per-beat render."""
    payload = {
        "cache_version": BUILD_CACHE_VERSION,
        "source_video": source_identity(video),
        "source_audio": source_identity(audio),
        "unit": unit,
        "fps": fps,
        "crop_width": cw,
        "crop_height": ch,
        "output": [OUT_W, OUT_H],
        "fade_seconds": FADE,
        "audio_offset_seconds": audio_offset,
        "quality": "final" if quality else "draft",
        "codec": {"video": "libx264", "crf": 16 if quality else 28,
                  "preset": "veryfast" if quality else "ultrafast", "pix_fmt": "yuv420p",
                  "audio": "aac", "bitrate": "192k", "rate": 48000, "channels": 2},
    }
    return payload


def cached_beat(out_path, meta_path, expected):
    """Reuse a beat only when its exact source/settings identity matches."""
    if not os.path.exists(out_path):
        return False
    observed = _cache_json(meta_path)
    if not observed or observed.get("cache_key") != expected:
        return False
    try:
        return os.path.getsize(out_path) > 0
    except OSError:
        return False


def write_beat_cache(meta_path, expected):
    temp = meta_path + ".tmp"
    with open(temp, "w") as handle:
        json.dump({"schema": "method-2.beat-cache.v1", "cache_key": expected}, handle, indent=2)
    os.replace(temp, meta_path)


def build(project, edl_path, *, quality=False):
    edl = json.loads(open(edl_path).read())
    work = os.path.dirname(os.path.abspath(edl_path))
    render_kind = "final" if quality else "draft"
    seg_dir = os.path.join(work, "segments", render_kind)
    os.makedirs(seg_dir, exist_ok=True)

    video = str(resolve_declared_path(edl["source_video"], edl_path))
    audio = str(resolve_declared_path(edl["source_audio"], edl_path))
    a_off = edl.get("audio_offset_seconds", 0.0)
    src_w = int(probe(video, "stream=width", "v:0").split("\n")[0])
    src_h = int(probe(video, "stream=height", "v:0").split("\n")[0])
    fps = source_fps(video)
    cw, ch, crops = crop_plan(edl, src_w, src_h)
    print(f"  source {src_w}x{src_h} @ {fps}")

    # per-shot segments when the angle pass has run, otherwise one crop per beat
    units = edl.get("segments") or [
        dict(b, **(p if isinstance(p, dict) else {})) for b, p in zip(edl["beats"], crops)]

    paths = []
    for i, u in enumerate(units):
        dur = u["out"] - u["in"]
        if u.get("mode") == "solo":
            # One source rect, letterboxed onto a blurred copy of itself. For a
            # two-panel source where one panel carries nothing to look at -- a
            # caller whose camera is black for the whole call -- stacking it
            # would spend half the frame on a dead rectangle.
            r = u["rect"]
            # Keep the approved full-frame solo layout even when the highest
            # accessible source is smaller than the planner's nominal 1920x1080
            # source rectangle (for example, a progressive fallback after a
            # DASH range request is refused).  Never upscale the crop itself or
            # silently crop a different region: clamp to the actual source.
            rx = max(0, min(int(r.get("x", 0)), src_w - 1))
            ry = max(0, min(int(r.get("y", 0)), src_h - 1))
            rw = max(1, min(int(r.get("w", src_w)), src_w - rx))
            rh = max(1, min(int(r.get("h", src_h)), src_h - ry))
            vf = (f"[0:v]crop={rw}:{rh}:{rx}:{ry},split=2[bg][fg];"
                  f"[bg]scale={OUT_W}:{OUT_H}:force_original_aspect_ratio=increase,"
                  f"crop={OUT_W}:{OUT_H},boxblur=28:2,eq=brightness=-0.16[bgb];"
                  f"[fg]scale={OUT_W}:{OUT_H}:force_original_aspect_ratio=decrease[fgs];"
                  f"[bgb][fgs]overlay=(W-w)/2:(H-h)/2,setsar=1[v]")
        elif u.get("mode") == "compose":
            # Reaction over content: two explicit source rectangles stacked into
            # one vertical frame. Needed for sources whose face and screen are
            # separate regions of the same 16:9 capture -- a single crop can
            # hold one or the other, never both.
            #
            # "cover" fills its panel and may lose the rect's edges. "contain"
            # letterboxes the whole rect onto a blurred copy of itself, for
            # clips whose payoff is on-screen UI that a crop would cut off.
            top, bot = u["top"], u["bottom"]
            sp = int(u.get("split", 640))
            bh = OUT_H - sp

            def rect(r):
                return f"crop={r['w']}:{r['h']}:{r['x']}:{r['y']}"

            def fill(h):
                return (f"scale={OUT_W}:{h}:force_original_aspect_ratio=increase,"
                        f"crop={OUT_W}:{h}")

            parts_v = [f"[0:v]split=2[t][b]", f"[t]{rect(top)},{fill(sp)}[u]"]
            if u.get("bottom_fit") == "contain":
                parts_v += [
                    f"[b]{rect(bot)},split=2[bg][fg]",
                    f"[bg]{fill(bh)},boxblur=24:2,eq=brightness=-0.14[bgb]",
                    f"[fg]scale={OUT_W}:{bh}:force_original_aspect_ratio=decrease[fgs]",
                    f"[bgb][fgs]overlay=(W-w)/2:(H-h)/2[w]"]
            else:
                parts_v += [f"[b]{rect(bot)},{fill(bh)}[w]"]
            parts_v.append("[u][w]vstack,setsar=1[v]")
            vf = ";".join(parts_v)
        elif u.get("mode") == "split":
            half_h = OUT_H // 2
            vf = (f"[0:v]split=2[a][b];"
                  f"[a]crop=720:640:{u['x1']}:30,scale={OUT_W}:{half_h}[u];"
                  f"[b]crop=720:640:{u['x2']}:30,scale={OUT_W}:{half_h}[w];"
                  f"[u][w]vstack,setsar=1[v]")
        else:
            x = (f"{u['x0']}+({u['x1']}-{u['x0']})*min(t/{dur:.3f}\\,1)"
                 if u.get("mode") == "ramp" else str(u.get("x", (src_w - cw) // 2)))
            vf = f"[0:v]crop={cw}:{ch}:'{x}':0,scale={OUT_W}:{OUT_H},setsar=1[v]"

        out = os.path.join(seg_dir, f"seg{i:03d}.mp4")
        meta_path = out + ".cache.json"
        cache_key = beat_cache_key(video, audio, u, fps, cw, ch, a_off, quality=quality)
        if cached_beat(out, meta_path, cache_key):
            print(f"  reuse beat {i + 1}/{len(units)}")
        else:
            run(["ffmpeg", "-hide_banner", "-v", "error",
                 "-ss", f"{u['in']:.3f}", "-i", video,
                 "-ss", f"{u['in'] + a_off:.3f}", "-i", audio,
                 "-t", f"{dur:.3f}", "-filter_complex",
                 vf + f";[1:a]afade=t=in:st=0:d={FADE},"
                      f"afade=t=out:st={max(0.0, dur - FADE):.3f}:d={FADE}[a]",
                 "-map", "[v]", "-map", "[a]", "-r", fps,
                 "-c:v", "libx264", "-preset", "veryfast" if quality else "ultrafast",
                 "-crf", "16" if quality else "28", "-pix_fmt", "yuv420p",
                 "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2", out, "-y"])
            write_beat_cache(meta_path, cache_key)
        paths.append(out)
    kinds = {}
    for u in units:
        kinds[u.get("mode", "crop")] = kinds.get(u.get("mode", "crop"), 0) + 1
    print(f"  {len(units)} segments  " + ", ".join(f"{k}:{v}" for k, v in kinds.items()))

    lst = os.path.join(seg_dir, "list.txt")
    with open(lst, "w") as f:
        for p_ in paths:
            f.write(f"file '{os.path.basename(p_)}'\n")
    base = os.path.join(seg_dir, "base.mp4")
    run(["ffmpeg", "-hide_banner", "-v", "error", "-f", "concat", "-safe", "0",
         "-i", lst, "-c", "copy", base, "-y"])
    base_dur = float(probe(base, "format=duration"))

    hook_png = None
    if edl.get("hook_title"):
        hook_png = os.path.join(work, "hook.png")
        info = render_hook(edl["hook_title"], hook_png)
        # re-place it clear of the subject, measured against the actual footage
        block_h = info["lines"] * int(info["size"] * 1.16) + 40
        y = choose_hook_y(base, edl.get("hook_seconds", [0, 4]), block_h)
        info = render_hook(edl["hook_title"], hook_png, y=y)
        print(f"  hook placed at y={y} (clear of the subject)")

    # two-pass loudness: single pass misses the target and leaves true peak hot
    meas = subprocess.run(["ffmpeg", "-hide_banner", "-i", base, "-af",
                           f"loudnorm=I={LUFS}:TP=-2.0:LRA=11:print_format=json",
                           "-f", "null", "-"], capture_output=True, text=True).stderr
    m = json.loads(re.search(r'\{[^{}]*"input_i"[^{}]*\}', meas, re.S).group(0))
    # alimiter caps SAMPLE peak; the technical gate measures TRUE peak (ebur128
    # peak=true), which runs 1.0-1.3 dB higher on percussive material. A 0.83
    # ceiling (-1.6 dBFS sample) let four episode-03 clips out at -0.3 to -0.7
    # dBTP against a -1.0 limit. 0.72 is -2.85 dBFS sample, leaving headroom for
    # intersample overshoot. Integrated loudness is unaffected -- this only tames
    # transients.
    af = (f"loudnorm=I={LUFS}:TP=-2.0:LRA=11:measured_I={m['input_i']}:"
          f"measured_TP={m['input_tp']}:measured_LRA={m['input_lra']}:"
          f"measured_thresh={m['input_thresh']}:offset={m['target_offset']}:linear=true,"
          "alimiter=limit=0.72:level=disabled")

    render_dir = os.path.join(work, "final-renders" if quality else "drafts")
    os.makedirs(render_dir, exist_ok=True)
    out_path = os.path.join(render_dir, edl.get("output_name", "clip.mp4"))
    subs = os.path.join(work, "subs.mov")
    has_subs = edl.get("subtitle_style") and os.path.exists(subs)

    cmd = ["ffmpeg", "-hide_banner", "-v", "error", "-i", base]
    chain, idx = "[0:v]", 1
    parts = []
    if has_subs:                       # subtitles under the hook
        cmd += ["-i", subs]
        parts.append(f"{chain}[{idx}:v]overlay=0:0[vs]")
        chain, idx = "[vs]", idx + 1
    if hook_png:
        hs, he = edl.get("hook_seconds", [0, 4])
        cmd += ["-loop", "1", "-i", hook_png]
        parts.append(f"{chain}[{idx}:v]overlay=0:0:enable='between(t,{hs},{he})'[v]")
    else:
        parts.append(f"{chain}null[v]")
    parts.append(f"[0:a]{af}[a]")
    cmd += ["-filter_complex", ";".join(parts)]
    cmd += ["-map", "[v]", "-map", "[a]", "-t", f"{base_dur:.3f}", "-r", fps,
            "-c:v", "libx264", "-preset", "medium" if quality else "ultrafast",
            "-crf", "18" if quality else "28", "-pix_fmt", "yuv420p",
            "-profile:v", "high", "-level", "4.2",
            "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
            "-movflags", "+faststart", out_path, "-y"]
    run(cmd)
    provenance_path = out_path + ".build.json"
    provenance = {
        "schema": "method-2.render-provenance.v1",
        "quality": "final" if quality else "draft",
        "render": os.path.abspath(out_path),
        "render_sha256": sha256_file(out_path),
        "edl": os.path.abspath(edl_path),
        "edl_sha256": sha256_file(edl_path),
        "source_video": source_identity(video),
        "source_audio": source_identity(audio),
        "source_resolution": [src_w, src_h],
        "source_frame_rate": fps,
        "beat_count": len(units),
        "cache_version": BUILD_CACHE_VERSION,
    }
    temp_provenance = provenance_path + ".tmp"
    with open(temp_provenance, "w") as handle:
        json.dump(provenance, handle, indent=2)
    os.replace(temp_provenance, provenance_path)
    return out_path, edl, hook_png, fps


def gates(clip, edl, hook_png, src_fps, *, project=None, edl_path=None,
          require_rendered_vtt=True, return_report=False):
    print("\n=== TECHNICAL GATE ===")
    policy = duration_policy(edl, edl_path)
    min_duration = float(policy.get("minimum_seconds") or 0.0)
    preferred_ceiling = float(policy["preferred_ceiling_seconds"])
    hard_ceiling = float(policy["hard_ceiling_seconds"])
    dur = float(probe(clip, "format=duration"))
    w = probe(clip, "stream=width", "v:0").split("\n")[0]
    h = probe(clip, "stream=height", "v:0").split("\n")[0]
    fps = probe(clip, "stream=r_frame_rate", "v:0").split("\n")[0]
    vcodec = probe(clip, "stream=codec_name", "v:0").split("\n")[0]
    vprofile = probe(clip, "stream=profile", "v:0").split("\n")[0]
    pix_fmt = probe(clip, "stream=pix_fmt", "v:0").split("\n")[0]
    acodec = probe(clip, "stream=codec_name", "a:0").split("\n")[0]
    sample_rate = probe(clip, "stream=sample_rate", "a:0").split("\n")[0]
    channels = probe(clip, "stream=channels", "a:0").split("\n")[0]
    vdur = float(probe(clip, "stream=duration", "v:0").split("\n")[0])
    adur = float(probe(clip, "stream=duration", "a:0").split("\n")[0])
    loud_result = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-i", clip, "-af",
                                  "ebur128=peak=true:framelog=quiet", "-f", "null", "-"],
                                 capture_output=True, text=True)
    loud = loud_result.stderr
    loud_match = re.search(r"I:\s+(-?[\d.]+) LUFS", loud)
    peak_match = re.search(r"Peak:\s+(-?[\d.]+) dBFS", loud)
    I = float(loud_match.group(1)) if loud_match else None
    TP = float(peak_match.group(1)) if peak_match else None
    black_result = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-i", clip, "-vf",
                                   "blackdetect=d=0.1:pic_th=0.98", "-f", "null", "-"],
                                  capture_output=True, text=True)
    black = black_result.stderr
    freeze_result_ffmpeg = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-i", clip, "-vf",
                                           "freezedetect=n=-60dB:d=0.4", "-f", "null", "-"],
                                          capture_output=True, text=True)
    freeze = freeze_result_ffmpeg.stderr
    # A frozen TAIL is the documented failure: an overlay track longer than the
    # picture silently freezes the last frame. Any-freeze-anywhere is a
    # different thing and fires on real footage. Follow event order so an EOF
    # ``freeze_start`` with no ``freeze_end`` cannot pass by accident.
    freeze_result = parse_freezedetect(freeze, dur)
    freeze_result["detector_ok"] = freeze_result_ffmpeg.returncode == 0
    black_detector_ok = black_result.returncode == 0
    loud_detector_ok = loud_result.returncode == 0
    spans = freeze_result["spans"]
    tail_ok = freeze_result["tail_ok"]
    if not spans:
        tail_note = "none"
    elif not tail_ok:
        tail_note = "frozen at the tail: " + ", ".join(
            str(item.get("reason")) for item in freeze_result["tail_failures"]
        )
    else:
        tail_note = f"none at the tail ({len(spans)} transient freeze(s))"

    cadence = presentation_cadence(clip, src_fps)
    checks = [
        ("duration in band", min_duration <= dur <= hard_ceiling,
         f"{dur:.2f}s (preferred <= {preferred_ceiling:.2f}s, hard <= {hard_ceiling:.2f}s)"),
        ("resolution", (w, h) == ("1080", "1920"), f"{w}x{h}"),
        ("H.264 high profile", vcodec == "h264" and vprofile.lower().startswith("high"),
         f"{vcodec}/{vprofile}"),
        ("pixel format", pix_fmt == "yuv420p", pix_fmt),
        ("audio format", acodec == "aac" and sample_rate == "48000" and channels == "2",
         f"{acodec}/{sample_rate}Hz/{channels}ch"),
        # follow source cadence: assert the render kept the rate it came in at
        ("cadence matches source", fps == src_fps, f"{fps} (source {src_fps})"),
        ("presentation cadence", cadence.get("passed", False),
         f"{cadence.get('frame_count', 0)} frames, deltas {cadence.get('minimum_delta_seconds')}–{cadence.get('maximum_delta_seconds')}s"),
        ("black detector", black_detector_ok, f"returncode {black_result.returncode}"),
        ("no black frames", black_detector_ok and "black_start" not in black,
         "none" if "black_start" not in black else
         "at " + re.search(r"black_start:(\S+)", black).group(1) + "s"),
        ("loudness detector", loud_detector_ok and I is not None and TP is not None,
         f"returncode {loud_result.returncode}"),
        ("integrated loudness", I is not None and abs(I - LUFS) <= LUFS_TOL,
         f"{I:.1f} LUFS" if I is not None else "unavailable"),
        ("true peak", TP is not None and TP <= TP_CEIL,
         f"{TP:.1f} dBFS" if TP is not None else "unavailable"),
        ("a/v durations aligned", abs(vdur - adur) < 0.10, f"{vdur:.2f}/{adur:.2f}s"),
        # report where, not a hardcoded "none" that reads as a pass on a failure
        ("freeze detector", freeze_result_ffmpeg.returncode == 0,
         f"returncode {freeze_result_ffmpeg.returncode}"),
        ("no frozen opening", freeze_result_ffmpeg.returncode == 0 and not any(
            span.get("start") is not None and float(span["start"]) <= 0.05
            for span in spans
        ), "none at opening"),
        ("no frozen tail", freeze_result_ffmpeg.returncode == 0 and tail_ok, tail_note),
    ]
    tech = all(c[1] for c in checks)
    for n, ok, v in checks:
        print(f"  {'PASS' if ok else 'FAIL'}  {n:24} {v}")

    safe = not bool(edl.get("hook_title"))
    if hook_png:
        from PIL import Image
        bb = Image.open(hook_png).getbbox()
        safe = bb[0] >= 90 and bb[2] <= 875 and bb[1] >= 200 and bb[3] <= 1430
        print("\n=== HOOK SAFE-ZONE GATE ===")
        print(f"  {'PASS' if safe else 'FAIL'}  bounds x {bb[0]}-{bb[2]}, y {bb[1]}-{bb[3]}")
    elif edl.get("hook_title"):
        print("\n=== HOOK SAFE-ZONE GATE ===")
        print("  FAIL  hook.png missing")

    print("\n=== AUDIO-CONTENT GATE ===")
    candidate = str(edl.get("slug") or edl.get("candidate_id") or "candidate")
    # Rendered transcripts always use the deterministic project cache path;
    # arbitrary EDL paths cannot accidentally make stale VTT evidence look
    # current.  The review record binds this path to the rendered hash.
    vtt_path = str(rendered_vtt_path(clip, project, candidate))
    transcript_info = {"status": "not_requested", "format": "vtt", "path": vtt_path}
    if require_rendered_vtt:
        # Fully marked non-verbal candidates receive a hash-bound placeholder;
        # no ASR process is launched for them.  Speech candidates use the
        # actual rendered-output VTT and its provenance sidecar.
        nonverbal_probe = audio_content_gate(edl, None)
        try:
            if nonverbal_probe.get("status") == "not_applicable":
                _, transcript_info = write_nonverbal_vtt(clip, vtt_path)
            else:
                _, transcript_info = transcribe_rendered_vtt(clip, vtt_path, language=edl.get("language", "en"))
        except Exception as exc:
            transcript_info = {"status": "error", "format": "vtt", "path": vtt_path, "error": str(exc)}
    audio_content = audio_content_gate(edl, vtt_path if os.path.exists(vtt_path) else None)
    if transcript_info.get("status") == "error":
        audio_content.update(status="pending", passed=False,
                             reason="Actual rendered-output transcription failed; old text cannot pass this gate")
    if audio_content.get("status") == "not_applicable":
        print("  N/A   audio-content overlap (all planned beats are non-verbal; visual review required)")
    else:
        overlap = audio_content.get("content_word_overlap", 0.0)
        print(f"  {'PASS' if audio_content.get('passed') else 'FAIL'}  audio-content overlap ({overlap:.0%})")

    report = {
        "technical": {"passed": tech, "checks": [
            {"name": n, "passed": ok, "observed": v} for n, ok, v in checks
        ]},
        "hook_safe_zone": {"passed": safe},
        "audio_content": audio_content,
        "rendered_vtt": transcript_info,
        "frozen_tail": freeze_result,
        "presentation_cadence": cadence,
        "duration_policy": policy,
    }
    # A draft can be technically inspectable while final delivery remains
    # pending review, alignment, and semantic checks. The caller decides the
    # final delivery gate; this function only reports repeatable checks.
    ok = tech and safe and (audio_content.get("passed") is True or audio_content.get("status") == "not_applicable")
    print(f"\nOVERALL: {'PASS -> quality review' if ok else 'FAIL -> review/'}")
    return report if return_report else ok


def project_root(edl_path, project):
    """Where delivered/ and review/ live. Explicit --project wins; otherwise the
    clip sits at <root>/clips/<slug>/edl.json, so the root is two levels up."""
    if project:
        return os.path.abspath(project)
    work = os.path.dirname(os.path.abspath(edl_path))
    parent = os.path.dirname(work)
    return os.path.dirname(parent) if os.path.basename(parent) == "clips" else work


def route(clip, ok, root):
    """Route one final verdict without deleting or overwriting other exports.

    A passing quality record goes only to ``deliverables/``.  A pending or
    failed record goes to ``review/``.  If the exact destination already holds
    the same bytes it is reused; a different existing file is preserved and
    causes a clear blocker instead of being overwritten.
    """
    dest = os.path.join(root, "deliverables" if ok else "review")
    os.makedirs(dest, exist_ok=True)
    name = os.path.basename(clip)
    out = os.path.join(dest, name)
    if os.path.exists(out):
        if sha256_file(out) != sha256_file(clip):
            raise FileExistsError(
                f"refusing to overwrite existing different export: {out}"
            )
        print(f"  reused identical export in {os.path.basename(dest)}/")
        return out
    shutil.copy2(clip, out)
    return out


def quality_report_path(root, edl):
    slug = str(edl.get("slug") or edl.get("candidate_id") or "candidate")
    return os.path.join(root, "_cache", os.path.basename(os.path.abspath(root)), "qa", slug, "quality.json")


def write_quality_report(root, edl_path, edl, clip, gate_report, alignment, review, final):
    """Persist the exact final QA evidence under the project cache."""
    output = quality_report_path(root, edl)
    os.makedirs(os.path.dirname(output), exist_ok=True)
    report = {
        "schema": "method-2.quality-report.v2",
        "project": os.path.abspath(root),
        "candidate": edl.get("candidate_id") or edl.get("slug"),
        "edl": os.path.abspath(edl_path),
        "edl_sha256": sha256_file(edl_path),
        "render": os.path.abspath(clip),
        "render_sha256": sha256_file(clip),
        "render_provenance": os.path.abspath(clip) + ".build.json",
        "source_visual_metadata": source_visual_metadata(edl, reference=edl_path),
        "duration_policy": gate_report.get("duration_policy"),
        "technical": gate_report.get("technical"),
        "hook_safe_zone": gate_report.get("hook_safe_zone"),
        "presentation_cadence": gate_report.get("presentation_cadence"),
        "audio_content": gate_report.get("audio_content"),
        "rendered_vtt": gate_report.get("rendered_vtt"),
        "alignment": alignment,
        "review": review,
        "delivery": final,
    }
    temp = output + ".tmp"
    with open(temp, "w") as handle:
        json.dump(report, handle, indent=2, ensure_ascii=False)
        handle.write("\n")
    os.replace(temp, output)
    return output


def main():
    ap = argparse.ArgumentParser(description="Build a Method 2 draft or run the explicit final quality gate")
    ap.add_argument("--edl", required=True)
    ap.add_argument("--project", default=None,
                    help="project root holding deliverables/ and review/; "
                         "inferred from the EDL path when omitted")
    ap.add_argument("--quality", action="store_true",
                    help="run final technical, audio-content, alignment, and review gates")
    ap.add_argument("--skip-build", action="store_true",
                    help="verify an existing render; use with --quality and --clip to preserve review hashes")
    ap.add_argument("--clip", default=None,
                    help="existing render path for --skip-build")
    ap.add_argument("--review", default=None,
                    help="completed m2_review JSON required by --quality")
    ap.add_argument("--no-route", action="store_true",
                    help="leave the built clip where it was built")
    a = ap.parse_args()
    edl = json.loads(Path(a.edl).expanduser().resolve().read_text(encoding="utf-8"))
    root = project_root(a.edl, a.project)
    if a.skip_build:
        if not a.clip:
            ap.error("--clip is required with --skip-build")
        clip = os.path.abspath(os.path.expanduser(a.clip))
        if not os.path.exists(clip):
            print(json.dumps({"status": "pending", "reason": f"existing render missing: {clip}"}, indent=2))
            sys.exit(2)
        if a.quality:
            provenance_path = clip + ".build.json"
            try:
                provenance = json.loads(Path(provenance_path).read_text(encoding="utf-8"))
                provenance_ok = (
                    provenance.get("quality") == "final"
                    and provenance.get("render_sha256") == sha256_file(clip)
                    and provenance.get("edl_sha256") == sha256_file(a.edl)
                )
            except (OSError, json.JSONDecodeError):
                provenance = None
                provenance_ok = False
            if not provenance_ok:
                print(json.dumps({
                    "status": "pending",
                    "reason": "--skip-build requires a hash-matched final render provenance sidecar",
                    "provenance": provenance_path,
                }, indent=2))
                sys.exit(2)
        hook = os.path.join(os.path.dirname(os.path.abspath(a.edl)), "hook.png")
        if not os.path.exists(hook):
            hook = None
        src_fps = source_fps(str(resolve_declared_path(edl["source_video"], a.edl)))
    else:
        pre_alignment = alignment_gate(edl, a.edl)
        if not pre_alignment.get("passed"):
            print(json.dumps({
                "status": "pending",
                "reason": "pre-build source alignment gate failed",
                "alignment": pre_alignment,
            }, indent=2, ensure_ascii=False))
            sys.exit(2)
        clip, edl, hook, src_fps = build(root, a.edl, quality=a.quality)
    print(f"\n{clip}")
    if not a.quality:
        print("DRAFT: built per-beat render; final quality gates were not run")
        sys.exit(0)

    gate_report = gates(
        clip, edl, hook, src_fps, project=root, edl_path=a.edl,
        require_rendered_vtt=True, return_report=True,
    )
    alignment = alignment_gate(edl, a.edl)
    if a.review:
        try:
            review_record = json.loads(Path(a.review).expanduser().resolve().read_text(encoding="utf-8"))
            review = validate_review_record(
                review_record, clip, a.edl,
                gate_report.get("rendered_vtt", {}).get("path"), edl=edl,
            )
        except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
            review = {"status": "pending", "passed": False, "reason": f"review record unreadable: {exc}"}
    else:
        review = {"status": "pending", "passed": False, "reason": "--review completed record is required for final delivery"}
    technical = {
        "passed": bool(gate_report.get("technical", {}).get("passed"))
        and bool(gate_report.get("hook_safe_zone", {}).get("passed")),
        "checks": gate_report.get("technical", {}).get("checks", []),
    }
    final = delivery_gate(
        technical=technical,
        audio_content=gate_report.get("audio_content", {}),
        alignment=alignment,
        review=review,
    )
    final["review"] = review
    final["alignment"] = alignment
    final["rendered_vtt"] = gate_report.get("rendered_vtt")
    report_path = write_quality_report(root, a.edl, edl, clip, gate_report, alignment, review, final)
    final["quality_report"] = report_path
    print(json.dumps(final, indent=2, ensure_ascii=False))
    ok = bool(final.get("passed"))
    if not a.no_route:
        print(f"routed -> {route(clip, ok, root)}")
    sys.exit(0 if ok else 2)


if __name__ == "__main__":
    main()
