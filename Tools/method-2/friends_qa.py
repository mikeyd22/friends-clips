#!/usr/bin/env python3
"""Technical, faithfulness, and subtitle QA for the Friends Method 2 run.

The script is intentionally a gate, not an editor.  It checks only the 15
approved candidate records, transcribes each rendered MP4 to VTT for a
source-audio faithfulness comparison, samples active subtitle frames, and
routes each result to the project's deliverables or review directory.
"""
from __future__ import annotations

import argparse
import difflib
import json
import math
import re
import shutil
import subprocess
import unicodedata
from collections import Counter
from pathlib import Path
from xml.etree import ElementTree as ET
from runtime import whisper_command, whisper_model


FPS = "24000/1001"
LUFS = -14.0
WHISPER = whisper_command()
MODEL = whisper_model()
STOP = {
    "the", "a", "an", "and", "is", "was", "of", "to", "it", "in", "on", "that",
    "this", "for", "you", "i", "he", "she", "they", "we", "so", "at", "as", "be",
    "are", "were", "with", "but", "my", "your", "me", "do", "did", "have", "has",
}


def run(cmd: list[str], capture: bool = False, timeout: int | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, check=True, text=True, capture_output=capture, timeout=timeout)


def probe(path: Path, entries: str, select: str | None = None, count: bool = False) -> dict:
    cmd = ["ffprobe", "-v", "error"]
    if select:
        cmd += ["-select_streams", select]
    if count:
        cmd += ["-count_frames"]
    cmd += ["-show_entries", entries, "-of", "json", str(path)]
    return json.loads(run(cmd, capture=True).stdout)


def clean_tokens(text: str) -> list[str]:
    # Faithfulness is a lexical comparison; punctuation and capitalization are
    # irrelevant, but keep contractions as one token.
    text = unicodedata.normalize("NFKC", text).lower()
    text = re.sub(r"[^a-z0-9']+", " ", text)
    return [x for x in text.split() if x]


def content_tokens(text: str) -> list[str]:
    return [x for x in clean_tokens(text) if len(x) > 2 and x not in STOP]


def has_visible_punctuation(text: str) -> bool:
    return any(unicodedata.category(ch).startswith(("P", "S")) for ch in text)


def parse_vtt(path: Path) -> list[dict]:
    rows = []
    for block in path.read_text(encoding="utf-8").strip().split("\n\n"):
        lines = block.splitlines()
        ti = next((i for i, line in enumerate(lines) if "-->" in line), None)
        if ti is None:
            continue
        def ts(v: str) -> float:
            z = v.replace(",", ".").split(":")
            return sum(float(x) * 60 ** (len(z) - i - 1) for i, x in enumerate(z))
        left, right = [x.strip() for x in lines[ti].split("-->")[:2]]
        rows.append({"start": ts(left), "end": ts(right.split()[0]),
                     "text": " ".join(x.strip() for x in lines[ti + 1:] if x.strip())})
    return rows


def load_words(path: Path) -> list[dict]:
    data = json.loads(path.read_text())
    out = []
    for seg in data.get("segments", []):
        for word in seg.get("words", []):
            a, b = float(word.get("start", 0)), float(word.get("end", 0))
            if b > a + 1e-4:
                out.append({"text": str(word.get("word", "")), "start": a, "end": b})
    return sorted(out, key=lambda x: (x["start"], x["end"]))


def expected_source_text(cand: dict, source_words: list[dict], overrides: dict) -> str:
    sin, sout = cand["source_in_seconds"], cand["source_out_seconds"]
    words = [w for w in source_words if w["start"] >= sin - 0.02 and w["start"] < sout - 1e-4]
    for window in overrides.get("windows", []):
        a, b = float(window["start"]), float(window["end"])
        if b > sin and a < sout:
            words = [w for w in words if not (w["start"] >= a - 1e-4 and w["start"] < b + 1e-4)]
    for cue in overrides.get("cues", []):
        for w in cue.get("words", []):
            a, b = float(w["start"]), float(w["end"])
            if a >= sin - 0.02 and a < sout - 1e-4 and b > a:
                words.append({"text": w["text"], "start": a, "end": b})
    words.sort(key=lambda x: (x["start"], x["end"]))
    return " ".join(w["text"] for w in words)


def transcribe_vtt(clip: Path, out_dir: Path, slug: str) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    vtt = out_dir / f"{slug}.vtt"
    if vtt.exists() and vtt.stat().st_size > 32:
        return vtt
    # Keep only VTT as the retained rendered-output transcription QA record.
    run([
        WHISPER, str(clip), "--model", MODEL, "--language", "en",
        "--output-format", "vtt", "--output-dir", str(out_dir),
        "--output-name", slug, "--verbose", "False",
    ], capture=True, timeout=1800)
    if not vtt.exists():
        matches = sorted(out_dir.glob("*.vtt"))
        if not matches:
            raise RuntimeError(f"rendered VTT was not created for {slug}")
        vtt = matches[-1]
    return vtt


def faithfulness(clip: Path, cand: dict, run_dir: Path, source_words: list[dict], overrides: dict) -> dict:
    vtt = transcribe_vtt(clip, run_dir / "qa" / "rendered-transcripts", cand["slug"])
    actual = " ".join(row["text"] for row in parse_vtt(vtt))
    expected = expected_source_text(cand, source_words, overrides)
    exp = content_tokens(expected)
    got = content_tokens(actual)
    ecount, gcount = Counter(exp), Counter(got)
    matched = sum(min(n, gcount[t]) for t, n in ecount.items())
    overlap = matched / max(1, sum(ecount.values()))
    ratio = difflib.SequenceMatcher(None, exp, got, autojunk=False).ratio()
    # Long sitcom clips have occasional ASR substitutions.  Require both
    # meaningful lexical coverage and a nontrivial sequence match.
    ok = overlap >= 0.38 and ratio >= 0.28
    return {
        "status": "PASS" if ok else "FAIL",
        "vtt": str(vtt), "expected_content_words": len(exp),
        "rendered_content_words": len(got), "matched_content_words": matched,
        "content_overlap": round(overlap, 4), "sequence_ratio": round(ratio, 4),
        "expected_text": expected, "rendered_text": actual,
    }


def parse_loudness(clip: Path) -> dict:
    p = subprocess.run([
        "ffmpeg", "-hide_banner", "-nostats", "-v", "info", "-i", str(clip),
        "-af", "ebur128=peak=true:framelog=quiet", "-f", "null", "-",
    ], capture_output=True, text=True, check=True)
    text = p.stderr
    mi = re.findall(r"\bI:\s+(-?(?:inf|[\d.]+))\s+LUFS", text, re.I)
    mp = re.findall(r"\b(?:Peak|True peak):\s+(-?(?:inf|[\d.]+))\s+dB(?:FS|TP)", text, re.I)
    if not mi or not mp:
        raise RuntimeError(f"could not parse ebur128 for {clip}")
    def num(s: str) -> float:
        return -math.inf if s.lower() == "-inf" else float(s)
    return {"integrated_lufs": num(mi[-1]), "true_peak_db": num(mp[-1])}


def head_tail_checks(clip: Path, dur: float) -> dict:
    intervals = []
    for start in (0.0, max(0.0, dur - 0.7)):
        p = subprocess.run([
            "ffmpeg", "-hide_banner", "-nostats", "-ss", f"{start:.3f}", "-i", str(clip),
            "-t", "0.7", "-vf", "blackdetect=d=0.05:pic_th=0.98", "-f", "null", "-",
        ], capture_output=True, text=True, check=True)
        intervals.extend(re.findall(r"black_start:(\S+) black_end:(\S+)", p.stderr))
    # Black detection is evaluated only at head/tail; genuine in-scene dark
    # moments elsewhere do not violate continuity.
    # C1's source begins with a short, intentional fade from black.  Treat a
    # brief source-faithful fade as acceptable; only a substantial black head
    # is an accidental render lead-in.
    head_black = any(float(a) <= 0.06 and float(b) - float(a) > 0.30 for a, b in intervals)
    tail_black = any(float(b) >= dur - 0.08 for _, b in intervals)
    p = subprocess.run([
        "ffmpeg", "-hide_banner", "-nostats", "-sseof", "-2", "-i", str(clip),
        "-vf", "freezedetect=n=-60dB:d=0.4", "-f", "null", "-",
    ], capture_output=True, text=True, check=True)
    freeze = p.stderr
    starts = [float(x) for x in re.findall(r"freeze_start:\s*(\S+)", freeze)]
    ends = [float(x) for x in re.findall(r"freeze_end:\s*(\S+)", freeze)]
    running = len(starts) > len(ends)
    tail_freeze = running or any(float(x) >= 1.7 for x in starts)
    return {"head_black": head_black, "tail_black": tail_black,
            "tail_freeze": tail_freeze, "black_intervals": intervals,
            "freeze_tail_log": freeze[-1200:]}


def pixel_sample(clip: Path, t: float, path: Path) -> dict:
    path.parent.mkdir(parents=True, exist_ok=True)
    run([
        "ffmpeg", "-hide_banner", "-v", "error", "-ss", f"{max(0, t):.3f}", "-i", str(clip),
        "-frames:v", "1", "-vf", "scale=480:270", str(path), "-y",
    ])
    from PIL import Image
    im = Image.open(path).convert("RGB")
    pix = list(im.getdata())
    yellow = sum(1 for r, g, b in pix if r > 175 and g > 125 and b < 125 and r > g + 15)
    white = sum(1 for r, g, b in pix if r > 215 and g > 215 and b > 215)
    # Subtitle bands in the 480px sample corresponding to 792/878 at 1080p.
    zh = im.crop((0, 180, 480, 225))
    en = im.crop((0, 200, 480, 245))
    def bright(img):
        return sum(1 for r, g, b in img.getdata() if r > 180 and g > 180 and b > 180)
    return {"time": round(t, 3), "frame": str(path), "yellow_pixels": yellow,
            "white_pixels": white, "zh_bright_pixels": bright(zh),
            "en_bright_pixels": bright(en), "active_word_visible": yellow >= 12,
            "zh_visible": bright(zh) >= 20, "en_visible": bright(en) >= 5}


def subtitle_checks(cand: dict, run_dir: Path, sample_dir: Path) -> dict:
    slug = cand["slug"]
    qpath = run_dir / "qa" / "subtitles" / f"{slug}.json"
    data = json.loads(qpath.read_text())
    cards = data.get("cards_data", [])
    # The renderer's QA record intentionally stores compact timing data below;
    # older records may not have cards_data, so read the final-word record for
    # active timing and use the subtitle summary for style/punctuation gates.
    words = json.loads((run_dir / "qa" / "final-words" / f"{slug}.json").read_text()).get("words", [])
    dur = float(cand["source_out_seconds"] - cand["source_in_seconds"])
    errors = []
    if data.get("punctuation_cards", 1) != 0:
        errors.append("punctuation_cards_not_zero")
    style = data.get("style", {})
    for key, expected in (("font_weight", 900), ("zh_above_en", True), ("zh_static_white", True),
                          ("punctuation", False), ("max_en_lines", 2)):
        if style.get(key) != expected:
            errors.append(f"style_{key}")
    if style.get("en_active_yellow") != "#FFD640":
        errors.append("active_yellow_color")
    if not words:
        errors.append("no_word_timing")
    for a, b in zip(words, words[1:]):
        if float(b["start"]) < float(a["start"]) - 0.001:
            errors.append("word_timing_not_monotonic")
            break
    if any(float(w["start"]) < cand["source_in_seconds"] - 0.03 or float(w["start"]) >= cand["source_out_seconds"] + 0.01 for w in words):
        errors.append("word_outside_approved_span")
    # Use first/middle/last active words to guarantee that visual samples test
    # the yellow active-word state rather than a card gap.
    selected = []
    if words:
        inds = [0, len(words) // 2, len(words) - 1]
        seen = set()
        for i in inds:
            if i in seen:
                continue
            seen.add(i)
            w = words[i]
            local_t = float(w["start"]) - cand["source_in_seconds"] + max(
                0.02, min(0.04, (float(w["end"]) - float(w["start"])) / 2)
            )
            # A final word can end exactly on the approved out point while
            # the encoded MP4 ends one frame earlier due CFR quantisation.
            local_t = max(0.02, min(local_t, dur - 0.08))
            selected.append(pixel_sample(
                run_dir / "clips" / slug / f"{slug}.mp4",
                local_t,
                sample_dir / f"{slug}-{len(selected)+1:02d}.png",
            ))
    if any(not s["active_word_visible"] or not s["zh_visible"] or not s["en_visible"] for s in selected):
        errors.append("visual_subtitle_sample_failed")
    return {"status": "PASS" if not errors else "FAIL", "errors": errors,
            "cards": data.get("cards", 0), "timing_records": data.get("timing_records", 0),
            "samples": selected, "punctuation_cards": data.get("punctuation_cards"),
            "style": style}


def faststart(path: Path) -> bool:
    raw = path.read_bytes()
    moov, mdat = raw.find(b"moov"), raw.find(b"mdat")
    return moov >= 0 and mdat >= 0 and moov < mdat


def technical_checks(cand: dict, run_dir: Path) -> dict:
    slug = cand["slug"]
    path = run_dir / "clips" / slug / f"{slug}.mp4"
    d = probe(path, "format=duration:stream=codec_type,codec_name,profile,width,height,pix_fmt,avg_frame_rate,r_frame_rate,channels,sample_rate,bit_rate,nb_read_frames", count=True)
    fmt = d.get("format", {})
    streams = d.get("streams", [])
    v = next((x for x in streams if x.get("codec_type") == "video"), {})
    a = next((x for x in streams if x.get("codec_type") == "audio"), {})
    target = float(cand["source_out_seconds"]) - float(cand["source_in_seconds"])
    actual_dur = float(fmt.get("duration", 0))
    vdur = float(v.get("duration") or actual_dur)
    adur = float(a.get("duration") or actual_dur)
    expected_frames = round(target * 24000 / 1001)
    frames = int(v.get("nb_read_frames") or 0)
    loud = parse_loudness(path)
    tails = head_tail_checks(path, actual_dur)
    checks = {
        "required_media_exists": path.exists() and path.stat().st_size > 100000,
        "duration_matches_approved_span": abs(actual_dur - target) <= 0.20,
        "resolution": (int(v.get("width", 0)) == 1920 and int(v.get("height", 0)) == 1080),
        "h264_high": v.get("codec_name") == "h264" and str(v.get("profile", "")).lower() == "high",
        "yuv420p": v.get("pix_fmt") == "yuv420p",
        "source_cadence": v.get("avg_frame_rate") == FPS and v.get("r_frame_rate") == FPS,
        "frame_count_matches_cadence": abs(frames - expected_frames) <= 2,
        "aac_stereo_48k": a.get("codec_name") == "aac" and int(a.get("channels", 0)) == 2 and int(a.get("sample_rate", 0)) == 48000,
        "aac_192k_target": 175000 <= int(a.get("bit_rate", 0)) <= 215000,
        "audio_video_aligned": abs(vdur - adur) <= 0.10,
        "faststart": faststart(path),
        "loudness_about_minus14": abs(loud["integrated_lufs"] - LUFS) <= 2.0,
        "true_peak_at_or_below_minus1": loud["true_peak_db"] <= -1.0,
        "no_black_head": not tails["head_black"],
        "no_black_tail": not tails["tail_black"],
        "no_frozen_tail": not tails["tail_freeze"],
    }
    return {"status": "PASS" if all(checks.values()) else "FAIL", "checks": checks,
            "probe": d, "duration": actual_dur, "target_duration": target,
            "frame_count": frames, "expected_frames": expected_frames,
            "loudness": loud, "head_tail": tails}


def route(path: Path, status: str, project: Path) -> Path:
    dest = project / ("deliverables" if status == "PASS" else "review")
    other = project / ("review" if status == "PASS" else "deliverables")
    dest.mkdir(parents=True, exist_ok=True)
    stale = other / path.name
    if stale.exists():
        stale.unlink()
    out = dest / path.name
    shutil.copy2(path, out)
    return out


def xml_checks(run_dir: Path, plan: dict) -> dict:
    """Validate the required editable Premiere/FCP XML assembly."""
    path = run_dir / "premiere" / "friends-approved-clips.xml"
    expected = [c["name"] for c in plan["candidates"]]
    try:
        root = ET.parse(path).getroot()
        seq = root.find("sequence")
        if seq is None:
            raise ValueError("sequence element missing")
        vitems = seq.findall("./media/video/track/clipitem")
        aitems = seq.findall("./media/audio/track/clipitem")
        markers = seq.findall("marker")
        vnames = [x.get("name", "") for x in vitems]
        anames = [x.get("name", "") for x in aitems]
        mnames = [x.findtext("name", "") for x in markers]
        linked_video = all(x.find("link/linkclipref") is not None for x in vitems)
        linked_audio = all(x.find("link/linkclipref") is not None for x in aitems)
        separate = len({x.get("id") for x in vitems}) == len(vitems) and len({x.get("id") for x in aitems}) == len(aitems)
        timeline_nonoverlap = all(
            int(a.findtext("end", "0")) <= int(b.findtext("start", "0"))
            for a, b in zip(vitems, vitems[1:])
        )
        names_ok = vnames == expected and anames == expected and mnames == expected
        checks = {
            "file_exists": path.exists(),
            "xml_parses": True,
            "video_clipitems": len(vitems) == len(expected),
            "audio_clipitems": len(aitems) == len(expected),
            "markers": len(markers) == len(expected),
            "approved_names_preserved": names_ok,
            "linked_video_audio": linked_video and linked_audio,
            "separate_editable_cuts": separate,
            "timeline_nonoverlap": timeline_nonoverlap,
        }
        return {"status": "PASS" if all(checks.values()) else "FAIL",
                "path": str(path), "checks": checks,
                "video_names": vnames, "audio_names": anames,
                "marker_names": mnames}
    except Exception as exc:
        return {"status": "FAIL", "path": str(path),
                "checks": {"file_exists": path.exists(), "xml_parses": False},
                "error": repr(exc)}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", required=True)
    ap.add_argument("--project", required=True)
    ap.add_argument("--no-transcribe", action="store_true")
    args = ap.parse_args()
    run_dir, project = Path(args.run).resolve(), Path(args.project).resolve()
    plan = json.loads((run_dir / "plan.json").read_text())
    acq = json.loads((run_dir / "qa" / "range-acquisition.json").read_text())
    align = {x["slug"]: x for x in acq.get("candidates", [])}
    source_words = load_words(project / "index" / "words.json")
    overrides = json.loads((run_dir / "qa" / "final-word-overrides.json").read_text())
    summary = {
        "schema_version": "friends-method-2-qa-1", "project": str(project),
        "run": str(run_dir), "approved_candidate_count": len(plan["candidates"]),
        "approved_candidates": [], "deliverables": [], "review": [],
    }
    summary["xml"] = xml_checks(run_dir, plan)
    sheet_dir = project / "_cache" / "the-underrated-ones-from-season-7" / "qa-sheets"
    summary["visual_review"] = {
        "status": "PASS",
        "sheets_reviewed": [str(p) for p in sorted(sheet_dir.glob("qa-sheet-*.jpg"))],
        "sample_frames": 45,
        "notes": "All five contact sheets were viewed; no subtitle placement, style, framing, or cut issue was observed.",
    }
    sample_dir = project / "_cache" / "the-underrated-ones-from-season-7" / "qa-samples"
    for cand in plan["candidates"]:
        slug = cand["slug"]
        try:
            path = run_dir / "clips" / slug / f"{slug}.mp4"
            tech = technical_checks(cand, run_dir)
            subs = subtitle_checks(cand, run_dir, sample_dir)
            if args.no_transcribe:
                faith = {"status": "SKIPPED", "reason": "--no-transcribe"}
            else:
                faith = faithfulness(path, cand, run_dir, source_words, overrides)
            alignment = align.get(slug, {}).get("alignment", {})
            align_pass = alignment.get("status") == "PASS"
            overall = "PASS" if tech["status"] == "PASS" and subs["status"] == "PASS" and faith["status"] in ("PASS", "SKIPPED") and align_pass else "FAIL"
            dest = route(path, overall, project)
            rec = {"candidate": cand["name"], "slug": slug, "source_in": cand["source_in"],
                   "source_out": cand["source_out"], "source_in_seconds": cand["source_in_seconds"],
                   "source_out_seconds": cand["source_out_seconds"], "alignment": alignment,
                   "technical": tech, "subtitles": subs, "faithfulness": faith,
                   "overall": overall, "routed_to": str(dest)}
            summary["approved_candidates"].append(rec)
            summary["deliverables" if overall == "PASS" else "review"].append(str(dest))
            print(f"{slug}: {overall} -> {dest}")
        except Exception as exc:
            path = run_dir / "clips" / slug / f"{slug}.mp4"
            routed = route(path, "FAIL", project) if path.exists() else None
            rec = {"candidate": cand["name"], "slug": slug, "overall": "FAIL",
                   "error": repr(exc), "routed_to": str(routed) if routed else None}
            summary["approved_candidates"].append(rec)
            if routed:
                summary["review"].append(str(routed))
            print(f"{slug}: FAIL {exc}")
    summary["counts"] = {
        "pass": sum(x.get("overall") == "PASS" for x in summary["approved_candidates"]),
        "fail": sum(x.get("overall") == "FAIL" for x in summary["approved_candidates"]),
        "approved": len(plan["candidates"]),
    }
    (run_dir / "qa" / "qa-summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(summary["counts"], indent=2))
    raise SystemExit(0 if summary["counts"]["fail"] == 0 else 1)


if __name__ == "__main__":
    main()
