#!/usr/bin/env python3
"""Method 2 — stage 2: index.

Builds the four signals the planner needs. The transcript alone is the weakest
of them for IRL footage, which is why the other three exist.

  transcript   full-source portable Whisper-compatible VTT for discovery
  word timing  selected spans only, after the planner shortlists candidates
  energy       100 Hz RMS envelope -> quiet runs and reaction peaks
  shots        scene-change timestamps
  vision       contact sheets so a model can see what is actually on screen

Everything lands in <project>/index/.
"""
import argparse
import hashlib
import math
from pathlib import Path
import json
import os
import re
import subprocess

import numpy as np

from runtime import whisper_command, whisper_model

WHISPER = whisper_command()
MODEL = whisper_model()
SR = 8000
HZ = 100
SHEET_SECONDS = 300      # one contact sheet per 5 minutes of source
SHEET_STEP = 5           # a frame every 5s


def run(cmd, **kw):
    return subprocess.run(cmd, check=True, **kw)


def duration_seconds(path):
    return float(subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nw=1:nk=1", path],
        capture_output=True, text=True, check=True).stdout.strip())


def vtt_time(seconds):
    ms = max(0, round(float(seconds) * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    sec, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{sec:02d}.{ms:03d}"


def write_vtt(segments, path):
    """Write the sole discovery transcript from offset-preserving decoder segments."""
    with open(path, "w") as f:
        f.write("WEBVTT\n\n")
        for i, seg in enumerate(segments, 1):
            text = seg["text"].strip()
            if not text:
                continue
            f.write(f"{i}\n{vtt_time(seg['start'])} --> {vtt_time(seg['end'])}\n{text}\n\n")


def source_identity(audio):
    path = Path(audio).resolve()
    stat = path.stat()
    return {"path": str(path), "size": stat.st_size, "mtime_ns": stat.st_mtime_ns}


def cache_key(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()[:20]


def read_vtt(path):
    cue = re.compile(r"(\d+:\d+:\d+\.\d+)\s+-->\s+(\d+:\d+:\d+\.\d+)")
    segments = []
    for block in Path(path).read_text().replace("\r\n", "\n").split("\n\n"):
        lines = block.splitlines()
        for i, line in enumerate(lines):
            match = cue.search(line)
            if match:
                segments.append({"start": hms(match[1]), "end": hms(match[2]),
                                 "text": " ".join(lines[i + 1:]).strip()})
                break
    return segments


def hms(value):
    if isinstance(value, (float, int)):
        result = float(value)
    else:
        h, m, sec = str(value).split(":")
        result = int(h) * 3600 + int(m) * 60 + float(sec)
    if not math.isfinite(result):
        raise ValueError("Non-finite source time")
    return result


def coverage_report(segments, duration):
    gaps, suspects = [], []
    cursor = 0.0
    for i, seg in enumerate(segments):
        start, end = float(seg["start"]), float(seg["end"])
        if not all(math.isfinite(v) for v in (start, end)) or start < 0 or end <= start or end > duration + 0.1:
            raise ValueError(f"Invalid transcript timing at cue {i + 1}")
        if start < cursor - 0.05:
            raise ValueError(f"Overlapping transcript timing at cue {i + 1}")
        if start - cursor >= 10:
            gaps.append({"start": cursor, "end": start, "status": "unexplained"})
        cursor = end
        text = seg.get("text", "").strip()
        reasons = []
        if i >= 2 and text and text == segments[i - 1].get("text", "").strip() == segments[i - 2].get("text", "").strip():
            reasons.append("repeated_text")
        if float(seg.get("compression_ratio", 0) or 0) > 2.4:
            reasons.append("high_compression_ratio")
        if end - start >= 25 and len(re.findall(r"\b\w+\b", text)) <= 4:
            reasons.append("sparse_text_in_long_window")
        if reasons:
            suspects.append({"cue": i + 1, "start": start, "end": end, "reasons": reasons,
                             "action": "retained_in_vtt; inspect audio before excluding"})
    if duration - cursor >= 10:
        gaps.append({"start": cursor, "end": duration, "status": "unexplained"})
    return {"source_duration_seconds": duration, "cue_count": len(segments),
            "unexplained_gaps": gaps, "suspected_asr_artifacts": suspects,
            "status": "review_coverage" if gaps or suspects or not segments else "pass",
            "note": "Missing text does not establish silence. No suspected speech is silently removed."}


def transcribe(audio, out_dir, chunk_seconds, language="en"):
    """Segment-only discovery pass; timing JSON is temporary decoder output.

    VTT is the sole analysis transcript. Coverage flags retain uncertain speech
    rather than deleting potential candidates. Word timing is a separate stage.
    """
    if chunk_seconds <= 0:
        raise ValueError("chunk_seconds must be positive")
    os.makedirs(out_dir, exist_ok=True)
    vtt = Path(out_dir) / "transcript.vtt"
    coverage = Path(out_dir) / "transcript-coverage.json"
    duration = duration_seconds(audio)
    identity = {"audio": source_identity(audio), "model": MODEL,
                "language": language, "chunk_seconds": chunk_seconds}
    if vtt.exists():
        if not coverage.exists() or json.loads(coverage.read_text()).get("input") != identity:
            raise ValueError("Existing VTT has no matching input record. Validate/migrate that transcript explicitly; it will not be overwritten or silently reused.")
        segments = read_vtt(vtt)
        coverage_report(segments, duration)
        print("  matching discovery VTT exists; no word decoder started")
        return None, str(vtt)
    project_dir = Path(out_dir).resolve().parent
    cache_dir = project_dir / "_cache" / project_dir.name / "transcription-chunks" / cache_key(identity)
    cache_dir.mkdir(parents=True, exist_ok=True)
    segments = []
    start = 0.0
    chunk_no = 0
    while start < duration - 0.001:
        end = min(start + chunk_seconds, duration)
        chunk_audio = cache_dir / f"chunk_{chunk_no:03d}.wav"
        chunk_out = cache_dir / f"chunk_{chunk_no:03d}"
        chunk_out.mkdir(exist_ok=True)
        if not chunk_audio.exists():
            run(["ffmpeg", "-hide_banner", "-v", "error", "-ss", f"{start:.3f}",
                 "-i", audio, "-t", f"{end - start:.3f}", "-vn", "-c:a", "pcm_s16le",
                 "-ar", "16000", "-ac", "1", str(chunk_audio), "-y"])
        chunk_json = chunk_out / "result.json"
        if not chunk_json.exists():
            language_args = ["--language", language] if language else []
            run([WHISPER, str(chunk_audio), "--model", MODEL, *language_args,
                 "--output-format", "json", "--output-name", "result", "--word-timestamps", "False",
                 "--condition-on-previous-text", "False", "--verbose", "False",
                 "--output-dir", str(chunk_out)])
        data = json.loads(chunk_json.read_text())
        for raw in data.get("segments", []):
            if not raw.get("text", "").strip():
                continue
            seg = dict(raw)
            seg["start"] = round(float(raw["start"]) + start, 3)
            seg["end"] = round(min(float(raw["end"]) + start, end), 3)
            segments.append(seg)
        start = end
        chunk_no += 1
    report = coverage_report(segments, duration)
    report["input"] = identity
    write_vtt(segments, vtt)
    coverage.write_text(json.dumps(report, indent=2) + "\n")
    print(f"  discovery VTT: {len(segments)} cues; coverage {report['status']}; word timing deferred")
    return None, str(vtt)


def selected_intervals(plan, duration, handles=30.0):
    if not math.isfinite(handles) or handles < 0:
        raise ValueError("Timing handles must be finite and non-negative")
    spans = []
    for candidate in plan.get("candidates", []):
        if candidate.get("status") not in {"shortlisted", "approved_for_timing", "approved_for_execution"}:
            continue
        if candidate.get("non_verbal") is True:
            continue
        start, end = hms(candidate["source_in"]), hms(candidate["source_out"])
        if start < 0 or end <= start or end > duration + 0.1:
            raise ValueError(f"Invalid selected range for {candidate.get('slug')}")
        spans.append([max(0.0, start - handles), min(duration, end + handles)])
    if not spans:
        raise ValueError("No explicitly shortlisted or approved candidates for word timing")
    merged = []
    for start, end in sorted(spans):
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(end, merged[-1][1])
        else:
            merged.append([start, end])
    return merged


def timing_units(intervals, maximum=120.0, overlap=1.0):
    """Overlapping decoder context; each word belongs to one non-overlap core."""
    if maximum <= 0 or overlap < 0:
        raise ValueError("Invalid timing unit size")
    units = []
    for start, end in intervals:
        cursor = start
        while cursor < end - 0.001:
            core_end = min(cursor + maximum, end)
            units.append({"start": max(start, cursor - overlap),
                          "end": min(end, core_end + overlap),
                          "core_start": cursor, "core_end": core_end})
            cursor = core_end
    return units


def time_selected(audio, out_dir, plan_path, handles=30.0, language="en"):
    """Only the planner shortlist plus handles receives exact word timing."""
    plan = json.loads(Path(plan_path).read_text())
    duration = duration_seconds(audio)
    selected = [c for c in plan.get("candidates", []) if c.get("status") in
                {"shortlisted", "approved_for_timing", "approved_for_execution"}]
    for candidate in selected:
        start, end = hms(candidate["source_in"]), hms(candidate["source_out"])
        if start < 0 or end <= start or end > duration + 0.1:
            raise ValueError(f"Invalid selected range for {candidate.get('slug')}")
    all_nonverbal = bool(selected) and all(c.get("non_verbal") is True for c in selected)
    intervals = [] if all_nonverbal else selected_intervals(plan, duration, handles)
    identity = {"audio": source_identity(audio), "model": MODEL, "language": language,
                "intervals": intervals, "maximum_core_seconds": 120.0, "overlap_seconds": 1.0,
                "nonverbal_candidates": [{"slug": c.get("slug"), "source_in": c["source_in"], "source_out": c["source_out"]}
                                         for c in selected if c.get("non_verbal") is True]}
    key = cache_key(identity)
    project_dir = Path(out_dir).resolve().parent
    cache = project_dir / "_cache" / project_dir.name / "word-timing" / key
    cache.mkdir(parents=True, exist_ok=True)
    output = Path(out_dir) / f"words-selected-{key}.json"
    if output.exists():
        existing = json.loads(output.read_text())
        if existing.get("input") == identity:
            return str(output)
        raise ValueError("Existing word timing does not match requested inputs")
    if all_nonverbal:
        output.write_text(json.dumps({"input": identity, "segments": [], "word_count": 0,
                                      "status": "not_applicable_explicit_nonverbal",
                                      "scope": "shortlisted_spans_plus_handles",
                                      "source_duration_seconds": duration,
                                      "candidate_slugs": [c.get("slug") for c in selected]}, indent=2) + "\n")
        return str(output)
    segments = []
    for index, unit in enumerate(timing_units(intervals)):
        unit_audio = cache / f"unit_{index:04d}.wav"
        unit_out = cache / f"unit_{index:04d}"
        unit_out.mkdir(exist_ok=True)
        if not unit_audio.exists():
            run(["ffmpeg", "-hide_banner", "-v", "error", "-ss", f"{unit['start']:.3f}",
                 "-i", audio, "-t", f"{unit['end'] - unit['start']:.3f}", "-vn",
                 "-c:a", "pcm_s16le", "-ar", "16000", "-ac", "1", str(unit_audio), "-y"])
        unit_json = unit_out / "result.json"
        if not unit_json.exists():
            language_args = ["--language", language] if language else []
            run([WHISPER, str(unit_audio), "--model", MODEL, *language_args,
                 "--output-format", "json", "--output-name", "result", "--word-timestamps", "True",
                 "--condition-on-previous-text", "False", "--hallucination-silence-threshold", "2",
                 "--verbose", "False", "--output-dir", str(unit_out)])
        data = json.loads(unit_json.read_text())
        for raw in data.get("segments", []):
            words = []
            for item in raw.get("words", []):
                word = dict(item)
                word["start"] = round(unit["start"] + float(item["start"]), 3)
                word["end"] = round(unit["start"] + float(item["end"]), 3)
                midpoint = (word["start"] + word["end"]) / 2
                if not unit["core_start"] <= midpoint < unit["core_end"]:
                    continue
                if not all(math.isfinite(word[k]) for k in ("start", "end")) or word["start"] < 0 or word["end"] < word["start"] or word["end"] > duration + 0.1:
                    raise ValueError("Invalid ASR word timing")
                words.append(word)
            if words:
                segments.append({"id": len(segments), "start": words[0]["start"], "end": words[-1]["end"],
                                 "text": "".join(w.get("word", "") for w in words), "words": words})
    all_words = [word for segment in segments for word in segment["words"]]
    if not all_words:
        raise ValueError("Selected timing produced no words; inspect nonverbal/speech coverage before approval")
    if any(b["start"] < a["end"] - 0.05 for a, b in zip(all_words, all_words[1:])):
        raise ValueError("Word timing overlaps across decoder units; inspect affected boundaries")
    output.write_text(json.dumps({"input": identity, "source_duration_seconds": duration,
                                  "segments": segments, "word_count": len(all_words),
                                  "scope": "shortlisted_spans_plus_handles"}, indent=2) + "\n")
    return str(output)


def energy(audio, out_dir):
    out = os.path.join(out_dir, "energy.npy")
    if os.path.exists(out):
        print("  energy curve exists, skipping")
        return out
    print("  energy curve ...")
    raw = os.path.join(out_dir, "_energy.raw")
    run(["ffmpeg", "-hide_banner", "-v", "error", "-i", audio,
         "-ac", "1", "-ar", str(SR), "-f", "s16le", raw, "-y"])
    a = np.fromfile(raw, dtype=np.int16).astype(np.float32) / 32768.0
    win = SR // HZ
    n = len(a) // win
    rms = np.sqrt(np.maximum((a[:n * win].reshape(n, win) ** 2).mean(axis=1), 1e-12))
    np.save(out, 20 * np.log10(rms))
    os.remove(raw)
    print(f"    {n / HZ:.0f}s at {HZ} Hz")
    return out


def shots(proxy, out_dir, threshold=0.15):
    out = os.path.join(out_dir, "shots.json")
    if os.path.exists(out):
        print("  shot list exists, skipping")
        return out
    print("  shot detection ...")
    meta = os.path.join(out_dir, "_scenes.txt")
    run(["ffmpeg", "-hide_banner", "-v", "error", "-i", proxy, "-an",
         "-vf", f"scale=320:-2,select='gt(scene,{threshold})',metadata=print:file={meta}",
         "-f", "null", "-"])
    cuts = []
    if os.path.exists(meta):
        for line in open(meta):
            if "pts_time:" in line:
                cuts.append(round(float(line.split("pts_time:")[1].split()[0]), 2))
        os.remove(meta)
    json.dump({"threshold": threshold, "cuts": cuts}, open(out, "w"), indent=2)
    print(f"    {len(cuts)} cuts")
    return out


def vision(proxy, out_dir, duration):
    sheets_dir = os.path.join(out_dir, "vision")
    os.makedirs(sheets_dir, exist_ok=True)
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError("Vision index needs a positive source duration")
    n = math.ceil(duration / SHEET_SECONDS)
    print(f"  vision contact sheets ({n} covering {duration/3600:.1f}h) ...")
    cols = SHEET_SECONDS // SHEET_STEP // 6
    for i in range(n):
        start = i * SHEET_SECONDS
        out = os.path.join(sheets_dir, f"sheet_{start:06d}.jpg")
        if os.path.exists(out) and os.path.getsize(out) > 0:
            continue
        subprocess.run(
            ["ffmpeg", "-hide_banner", "-v", "error", "-ss", str(start),
             "-t", str(SHEET_SECONDS), "-i", proxy,
             "-vf", f"fps=1/{SHEET_STEP},scale=240:-2,tile=6x{cols}:margin=3:padding=3",
             "-frames:v", "1", "-q:v", "4", out, "-y"], check=True)
        if not os.path.exists(out) or os.path.getsize(out) == 0:
            raise RuntimeError(f"Missing vision sheet at {start}s; index is incomplete")
    return sheets_dir


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", required=True)
    ap.add_argument("--chunk-seconds", type=int, default=3600)
    ap.add_argument("--skip-vision", action="store_true", help="Leaves the required vision index incomplete")
    ap.add_argument("--timing-plan", help="Planner shortlist JSON; run selected word timing only")
    ap.add_argument("--handles", type=float, default=30.0)
    ap.add_argument("--language", default="en", help="Language code, or auto for multilingual sources")
    a = ap.parse_args()

    state_path = os.path.join(a.project, "m2.json")
    state = json.load(open(state_path))
    src = state["source"]

    out_dir = os.path.join(a.project, "index")
    os.makedirs(out_dir, exist_ok=True)

    language = None if a.language == "auto" else a.language
    if a.timing_plan:
        words = time_selected(src["full_audio"], out_dir, a.timing_plan, a.handles, language)
        state.setdefault("index", {})["words"] = words
        state["index"]["word_timing_scope"] = "shortlisted_spans_plus_handles"
        state["stage"] = "selected_timing_ready_for_planner"
        json.dump(state, open(state_path, "w"), indent=2)
        print(f"Selected word timing: {words}. Return to planner to lock boundaries.")
        return
    print("indexing:")
    idx = {}
    _, idx["transcript_vtt"] = transcribe(src["full_audio"], out_dir, a.chunk_seconds, language)
    idx["word_timing_scope"] = "deferred_until_shortlist"
    idx["coverage"] = os.path.join(out_dir, "transcript-coverage.json")
    idx["energy"] = energy(src["full_audio"], out_dir)
    idx["shots"] = shots(src["proxy"], out_dir)
    if not a.skip_vision:
        idx["vision"] = vision(src["proxy"], out_dir, src.get("duration_seconds") or duration_seconds(src["full_audio"]))

    state["index"] = idx
    state["stage"] = "index_incomplete" if a.skip_vision else "indexed"
    json.dump(state, open(state_path, "w"), indent=2)
    print("\nNext: scout discovery, planner shortlist, selected timing, then planner boundary lock.")


if __name__ == "__main__":
    main()
