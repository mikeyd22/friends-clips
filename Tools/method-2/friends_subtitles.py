#!/usr/bin/env python3
"""Render Friends bilingual burned-subtitle alpha tracks.

The picture remains a single continuous approved source span.  This tool only
creates the subtitle overlay and final-word timing records: English is on top,
Simplified Chinese is static white below it, and one verified English word at a
time is yellow.
"""
from __future__ import annotations

import argparse
import difflib
import json
import math
import os
import re
import shutil
import subprocess
import unicodedata
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
from runtime import EN_FONT, ZH_FONT

BASE_W, BASE_H = 1920, 1080
W, H = BASE_W, BASE_H
FPS = "24000/1001"
MAX_W = int(W * 0.72)
ZH_SIZE = 61
EN_SIZE = 46
STROKE = 5
# The explicit source request for this render puts English above Chinese.
EN_Y = 792
ZH_Y = 878
YELLOW = (255, 214, 64, 255)
WHITE = (255, 255, 255, 255)
BLACK = (0, 0, 0, 255)
FONT_EN = EN_FONT
FONT_ZH = ZH_FONT


def configure_dimensions(width: int, height: int):
    """Keep the Friends layout proportional when native source is smaller."""
    global W, H, MAX_W, ZH_SIZE, EN_SIZE, STROKE, ZH_Y, EN_Y
    W, H = int(width), int(height)
    scale = H / BASE_H
    MAX_W = int(W * 0.72)
    ZH_SIZE = max(12, round(61 * scale))
    EN_SIZE = max(9, round(46 * scale))
    STROKE = max(1, round(5 * scale))
    EN_Y = round(792 * scale)
    ZH_Y = round(878 * scale)


def font(path: str, size: int, weight: int = 900):
    f = ImageFont.truetype(path, size)
    try:
        f.set_variation_by_axes([36, 100, weight, 0])
    except Exception:
        pass
    return f


def strip_visible_punctuation(value: str) -> str:
    out = []
    for ch in value.replace("\n", " "):
        cat = unicodedata.category(ch)
        if cat.startswith("P") or cat.startswith("S"):
            continue
        out.append(ch)
    return re.sub(r"\s+", " ", "".join(out)).strip()


def clean_en(value: str) -> str:
    # All visible punctuation is forbidden by the Friends guide.  Keeping
    # letters/numbers/spaces also removes transcript ellipses and dollar signs.
    return strip_visible_punctuation(value)


def clean_zh(value: str) -> str:
    return strip_visible_punctuation(value)


def sec(value: str) -> float:
    z = value.replace(",", ".").split(":")
    return sum(float(v) * 60 ** (len(z) - i - 1) for i, v in enumerate(z))


def parse_vtt(path: Path):
    rows = []
    for block in path.read_text(encoding="utf-8").strip().split("\n\n"):
        lines = block.splitlines()
        ti = next((i for i, line in enumerate(lines) if "-->" in line), None)
        if ti is None:
            continue
        left, right = [x.strip() for x in lines[ti].split("-->")[:2]]
        rows.append({
            "id": str(len(rows)),
            "start": sec(left),
            "end": sec(right.split()[0]),
            "en_source": " ".join(x.strip() for x in lines[ti + 1:] if x.strip()),
        })
    return rows


def load_source_words(path: Path):
    data = json.loads(path.read_text())
    words = []
    timing_id = 0
    for segment in data.get("segments", []):
        for word in segment.get("words", []):
            text = clean_en(str(word.get("word", "")))
            if not text:
                continue
            current_timing_id = timing_id
            timing_id += 1
            start = float(word.get("start", 0))
            end = float(word.get("end", 0))
            if end <= start + 1e-4:
                continue
            words.append({"text": text, "start": start, "end": end,
                          "source": str(path), "timing_id": current_timing_id})
    return sorted(words, key=lambda x: (x["start"], x["end"]))


def load_vtt_timed_words(vtt_path: Path, timing_path: Path):
    """Use source VTT wording while binding each word to selected timings.

    The selected timing pass is an alignment aid, not a replacement caption
    transcript.  Sequence alignment drops ASR-only fillers and carries the
    source VTT wording through to the rendered subtitle.  Unmatched source
    words receive a bounded interpolated interval and are recorded as
    synthetic timing in the final-word manifest.
    """
    cues = parse_vtt(vtt_path)
    vtt_words = []
    for cue in cues:
        for token in cue["en_source"].split():
            text = clean_en(token)
            if text:
                vtt_words.append({"text": text, "cue_id": cue["id"],
                                  "cue_start": cue["start"], "cue_end": cue["end"],
                                  "order": len(vtt_words)})
    timing_data = json.loads(timing_path.read_text())
    selected = []
    for segment in timing_data.get("segments", []):
        for word in segment.get("words", []):
            text = clean_en(str(word.get("word", "")))
            if text:
                selected.append({"text": text, "start": float(word.get("start", 0)),
                                 "end": float(word.get("end", 0)),
                                 "timing_id": len(selected)})
    a = [w["text"].lower() for w in vtt_words]
    b = [w["text"].lower() for w in selected]
    matches = [None] * len(vtt_words)
    alignment_tags = ["synthetic"] * len(vtt_words)
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(
            a=a, b=b, autojunk=False).get_opcodes():
        if tag == "equal":
            for vi, sj in zip(range(i1, i2), range(j1, j2)):
                matches[vi] = selected[sj]
                alignment_tags[vi] = "exact"
        elif tag == "replace" and i1 < i2 and j1 < j2:
            left = selected[j1:j2]
            span_start = min(x["start"] for x in left)
            span_end = max(x["end"] for x in left)
            count = i2 - i1
            for offset, vi in enumerate(range(i1, i2)):
                frac_a = offset / count
                frac_b = (offset + 1) / count
                matches[vi] = {
                    "start": span_start + (span_end - span_start) * frac_a,
                    "end": span_start + (span_end - span_start) * frac_b,
                    "timing_id": left[min(offset, len(left) - 1)]["timing_id"],
                }
                alignment_tags[vi] = "replacement_split"
    # Interpolate VTT-only words between the nearest mapped words in the same
    # caption cue.  This handles source-caption wording such as "I'm gonna
    # leave" when the selected timing model omitted those words.
    i = 0
    while i < len(vtt_words):
        if matches[i] is not None:
            i += 1
            continue
        item = vtt_words[i]
        j = i
        while j < len(vtt_words) and matches[j] is None and vtt_words[j]["cue_id"] == item["cue_id"]:
            j += 1
        prev = i - 1
        while prev >= 0 and (matches[prev] is None or
                             vtt_words[prev]["cue_id"] != item["cue_id"]):
            prev -= 1
        start = matches[prev]["end"] if prev >= 0 else item["cue_start"]
        end = matches[j]["start"] if j < len(vtt_words) and vtt_words[j]["cue_id"] == item["cue_id"] else item["cue_end"]
        if end <= start:
            end = start + 0.08 * (j - i)
        step = (end - start) / max(1, j - i)
        for offset, missing_index in enumerate(range(i, j)):
            matches[missing_index] = {
                "start": start + step * offset,
                "end": start + step * (offset + 1),
                "timing_id": None,
            }
            alignment_tags[missing_index] = "synthetic"
        i = j
    out = []
    for item, timing, tag in zip(vtt_words, matches, alignment_tags):
        # Selected word timings can trail a source-caption cue boundary by a
        # few hundred milliseconds.  Keep the measured timing rather than
        # collapsing it to the cue end; the explicit cue_id below still keeps
        # the source wording grouped with its own Chinese translation.
        start = max(0.0, float(timing["start"]))
        end = max(start + 0.04, float(timing["end"]))
        if end <= start:
            end = start + 0.04
        out.append({"text": item["text"], "start": start, "end": end,
                    "source": f"{vtt_path} wording + {timing_path} selected timing",
                    "timing_id": timing.get("timing_id"), "cue_id": item["cue_id"],
                    "alignment": tag, "order": item["order"]})
    return out


def wrap_english(text_words, size=None):
    if size is None:
        size = EN_SIZE
    draw = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    if size < 31:
        f = font(FONT_EN, size)
        lines, cur = [], []
        for word in text_words:
            trial = " ".join(cur + [word])
            if cur and draw.textlength(trial, font=f) > MAX_W:
                lines.append(cur)
                cur = [word]
            else:
                cur.append(word)
        if cur:
            lines.append(cur)
        return size, f, lines[:2]
    for cur_size in range(size, 31, -2):
        f = font(FONT_EN, cur_size)
        lines, cur = [], []
        for word in text_words:
            trial = " ".join(cur + [word])
            if cur and draw.textlength(trial, font=f) > MAX_W:
                lines.append(cur)
                cur = [word]
            else:
                cur.append(word)
        if cur:
            lines.append(cur)
        if len(lines) <= 2:
            return cur_size, f, lines
    return 30, font(FONT_EN, 30), [text_words]


def wrap_chinese(text: str):
    draw = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    chars = list(text)
    if ZH_SIZE < 37:
        f = font(FONT_ZH, ZH_SIZE)
        if draw.textlength(text, font=f) <= MAX_W:
            return ZH_SIZE, f, [text]
        splits = []
        for split in range(1, len(chars)):
            left, right = text[:split], text[split:]
            lw, rw = draw.textlength(left, font=f), draw.textlength(right, font=f)
            if lw <= MAX_W and rw <= MAX_W:
                splits.append((abs(lw - rw), split))
        if splits:
            split = min(splits)[1]
            return ZH_SIZE, f, [text[:split], text[split:]]
        lines, cur = [], ""
        for ch in chars:
            trial = cur + ch
            if cur and draw.textlength(trial, font=f) > MAX_W:
                lines.append(cur)
                cur = ch
            else:
                cur = trial
        if cur:
            lines.append(cur)
        return ZH_SIZE, f, lines[:2]
    for size in range(ZH_SIZE, 37, -2):
        f = font(FONT_ZH, size)
        if draw.textlength(text, font=f) <= MAX_W:
            return size, f, [text]
    f = font(FONT_ZH, 42)
    lines, cur = [], ""
    for ch in chars:
        trial = cur + ch
        if cur and draw.textlength(trial, font=f) > MAX_W:
            lines.append(cur)
            cur = ch
        else:
            cur = trial
    if cur:
        lines.append(cur)
    return 42, f, lines[:2]


def draw_card(en_words, zh_text, active_index=None, cefr_en_indices=None,
              cefr_zh_spans=None):
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    cefr_en_indices = set(cefr_en_indices or [])
    cefr_zh_spans = cefr_zh_spans or []
    zh = clean_zh(zh_text)
    if zh:
        _, zf, zlines = wrap_chinese(zh)
        zlh = int(zf.size * 1.12)
        ztop = ZH_Y - (len(zlines) - 1) * zlh / 2
        for i, line in enumerate(zlines):
            marked = [False] * len(line)
            line_offset = sum(len(x) for x in zlines[:i])
            for start, end in cefr_zh_spans:
                for j in range(max(0, start - line_offset),
                               min(len(line), end - line_offset)):
                    if 0 <= j < len(line):
                        marked[j] = True
            widths = [d.textlength(ch, font=zf) for ch in line]
            total = sum(widths)
            x = W / 2 - total / 2
            for ch, width, yellow in zip(line, widths, marked):
                d.text((x + width / 2, ztop + i * zlh), ch, font=zf,
                       fill=YELLOW if yellow else WHITE,
                       stroke_width=STROKE, stroke_fill=BLACK, anchor="mm")
                x += width

    en_text = [clean_en(x) for x in en_words if clean_en(x)]
    if not en_text:
        return img
    _, ef, lines = wrap_english(en_text)
    lh = int(ef.size * 1.16)
    etop = EN_Y - (len(lines) - 1) * lh / 2
    idx = 0
    for li, line in enumerate(lines):
        widths = [d.textlength(word, font=ef) for word in line]
        total = sum(widths) + max(0, len(line) - 1) * d.textlength(" ", font=ef)
        x = W / 2 - total / 2
        for word, width in zip(line, widths):
            fill = YELLOW if (idx in cefr_en_indices or
                              (active_index is not None and idx == active_index)) else WHITE
            d.text((x + width / 2, etop + li * lh), word, font=ef, fill=fill,
                   stroke_width=STROKE, stroke_fill=BLACK, anchor="mm")
            x += width + d.textlength(" ", font=ef)
            idx += 1
    return img


def cue_map(rows, translations):
    out = {}
    for row in rows:
        out[row["id"]] = {**row, "zh": clean_zh(translations.get(row["id"], ""))}
    return out


def assign_cue(word, cues):
    # Prefer the cue containing the word start.  This handles source cues that
    # touch candidate boundaries without changing the approved span.
    hits = [c for c in cues if c["start"] - 1e-4 <= word["start"] < c["end"] - 1e-4]
    if hits:
        return hits[-1]
    before = [c for c in cues if c["start"] <= word["start"]]
    after = [c for c in cues if c["start"] > word["start"]]
    if before and after and after[0]["start"] - word["start"] <= 0.6:
        return after[0]
    return before[-1] if before else None


def load_overrides(path: Path):
    if not path.exists():
        return {"windows": [], "cues": []}
    return json.loads(path.read_text())


def make_cards(cand, source_words, cues, overrides, cefr_items=None):
    cefr_items = cefr_items or []
    sin = cand["source_in_seconds"]
    sout = cand["source_out_seconds"]
    words = [dict(w) for w in source_words if w["start"] >= sin - 0.02 and w["start"] < sout - 1e-4]
    custom_cues = [dict(c, zh=clean_zh(c.get("zh", ""))) for c in overrides.get("cues", [])
                   if c["start"] < sout and c["end"] > sin]
    for window in overrides.get("windows", []):
        a, b = window["start"], window["end"]
        if b <= sin or a >= sout:
            continue
        words = [w for w in words if not (w["start"] >= a - 1e-4 and w["start"] < b + 1e-4)]
    words.extend({
        "text": clean_en(w["text"]), "start": float(w["start"]), "end": float(w["end"]),
        "source": "verified source-range VTT word transcription"
    } for c in custom_cues for w in c.get("words", []
    ) if float(w["start"]) >= sin - 0.02 and float(w["start"]) < sout - 1e-4 and float(w["end"]) > float(w["start"]))
    if any("order" in w for w in words):
        words.sort(key=lambda x: (0, x.get("order", 10**9))
                   if "order" in x else (1, x["start"], x["end"]))
    else:
        words.sort(key=lambda x: (x["start"], x["end"]))

    all_cues = list(cues) + custom_cues
    all_cues.sort(key=lambda x: (x["start"], x["end"]))
    groups = []
    group_map = {}
    for word in words:
        cue = next((c for c in all_cues if str(c["id"]) == str(word.get("cue_id"))), None)
        if cue is None:
            cue = assign_cue(word, all_cues)
        if cue is None:
            continue
        key = cue["id"]
        if key not in group_map:
            group_map[key] = {"cue_id": key, "cue": cue, "words": []}
            groups.append(group_map[key])
        group_map[key]["words"].append(word)

    cards = []
    final_words = []
    for group in groups:
        ws = group["words"]
        if not ws:
            continue
        en_words = [w["text"] for w in ws]
        zh = clean_zh(group["cue"].get("zh", ""))
        start = max(0.0, ws[0]["start"] - sin)
        end = min(sout - sin, ws[-1]["end"] - sin + 0.08)
        if end <= start:
            continue
        timing_positions = {w.get("timing_id"): i for i, w in enumerate(ws)
                            if w.get("timing_id") is not None}
        cefr_en_indices = set()
        cefr_zh_spans = []
        cefr_matches = []
        for item in cefr_items:
            if str(item.get("cue_id")) != str(group["cue_id"]):
                continue
            ids = [int(x) for x in item.get("timing_ids", [])]
            if not ids or not all(x in timing_positions for x in ids):
                continue
            cefr_en_indices.update(timing_positions[x] for x in ids)
            phrase = clean_zh(str(item.get("chinese", "")))
            zh_start = zh.find(phrase) if phrase else -1
            if phrase and zh_start >= 0:
                cefr_zh_spans.append((zh_start, zh_start + len(phrase)))
            cefr_matches.append({"id": item.get("id"), "level": item.get("level"),
                                 "english": item.get("english"),
                                 "chinese": phrase, "matched_zh": zh_start >= 0})
        card = {"cue_id": group["cue_id"], "s": start, "e": end,
                "en_words": en_words, "zh": zh, "words": [],
                "cefr_en_indices": sorted(cefr_en_indices),
                "cefr_zh_spans": cefr_zh_spans, "cefr_matches": cefr_matches}
        for w in ws:
            rel = {"text": w["text"], "start": round(w["start"] - sin, 3),
                   "end": round(w["end"] - sin, 3), "source_start": w["start"],
                   "source_end": w["end"], "cue_id": group["cue_id"],
                   "timing_id": w.get("timing_id"),
                   "alignment": w.get("alignment", "exact")}
            card["words"].append(rel)
            final_words.append({"word": w["text"], "start": round(w["start"], 3),
                                "end": round(w["end"], 3), "cue_id": group["cue_id"],
                                "timing_id": w.get("timing_id"),
                                "alignment": w.get("alignment", "exact")})
        cards.append(card)
    # Avoid overlapping cards at cue seams.  This only affects overlay timing,
    # never the underlying continuous picture or source audio.
    for i in range(len(cards) - 1):
        cards[i]["e"] = min(cards[i]["e"], cards[i + 1]["s"] - 0.001)
    cards = [c for c in cards if c["e"] > c["s"]]
    return cards, final_words


def render_track(cards, duration, out_path: Path, frames_dir: Path):
    if frames_dir.exists():
        shutil.rmtree(frames_dir)
    frames_dir.mkdir(parents=True, exist_ok=True)
    blank = frames_dir / "blank.png"
    Image.new("RGBA", (W, H), (0, 0, 0, 0)).save(blank)
    intervals = []

    def add_interval(start, end, image):
        if end > start + 1e-6:
            intervals.append((start, end, image))

    t = 0.0
    for ci, card in enumerate(cards):
        if card["s"] > t:
            add_interval(t, card["s"], blank)
        # A no-active card makes held pauses readable without falsely coloring
        # a word that is not spoken at that instant.
        images = {None: frames_dir / f"c{ci:04d}_none.png"}
        draw_card(card["en_words"], card["zh"], None,
                  card.get("cefr_en_indices"), card.get("cefr_zh_spans")).save(images[None])
        cursor = card["s"]
        for wi, w in enumerate(card["words"]):
            ws, we = w["start"], min(w["end"], card["e"])
            if ws > cursor:
                add_interval(cursor, ws, images[None])
            active_path = frames_dir / f"c{ci:04d}_w{wi:03d}.png"
            draw_card(card["en_words"], card["zh"], wi,
                      card.get("cefr_en_indices"), card.get("cefr_zh_spans")).save(active_path)
            if we > ws:
                add_interval(ws, we, active_path)
            cursor = max(cursor, we)
        if card["e"] > cursor:
            add_interval(cursor, card["e"], images[None])
        t = card["e"]
    if duration > t:
        add_interval(t, duration, blank)
    if not intervals:
        intervals = [(0.0, duration, blank)]

    # The previous concat-duration implementation accumulated sub-frame
    # rounding across hundreds of word images.  Emit one exact image per
    # output frame instead, so active-word transitions stay within one frame
    # of the selected timing rather than drifting over the full clip.
    fps = 24000 / 1001
    frame_count = max(1, int(round(duration * fps)))
    interval_index = 0
    for frame_index in range(frame_count):
        timestamp = frame_index / fps
        while interval_index < len(intervals) - 1 and timestamp >= intervals[interval_index][1] - 1e-9:
            interval_index += 1
        image = blank
        start, end, candidate = intervals[interval_index]
        if start - 1e-9 <= timestamp < end + 1e-9:
            image = candidate
        frame_path = frames_dir / f"frame_{frame_index:06d}.png"
        os.symlink(image.resolve(), frame_path)
    subprocess.run([
        "ffmpeg", "-hide_banner", "-v", "error", "-framerate", FPS,
        "-start_number", "0", "-i", str(frames_dir / "frame_%06d.png"),
        "-frames:v", str(frame_count), "-vf", "format=rgba", "-c:v", "qtrle", "-pix_fmt", "argb",
        "-t", f"{duration:.3f}", str(out_path), "-y"
    ], check=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", required=True)
    ap.add_argument("--project", required=True)
    ap.add_argument("--timing", default=None,
                    help="word-timing JSON; defaults to index/words.json")
    ap.add_argument("--cefr", default=None,
                    help="contextual CEFR highlight manifest")
    ap.add_argument("--vtt-authoritative", action="store_true",
                    help="display source VTT wording while using selected word timings")
    ap.add_argument("--width", type=int, default=1920)
    ap.add_argument("--height", type=int, default=1080)
    args = ap.parse_args()
    run = Path(args.run).resolve()
    project = Path(args.project).resolve()
    configure_dimensions(args.width, args.height)
    plan = json.loads((run / "plan.json").read_text())
    trans = json.loads((run / "qa" / "subtitle-translations.json").read_text())
    trans_map = {str(row["id"]): row.get("zh", "") for row in trans["rows"]}
    manual = run / "qa" / "translation-overrides.json"
    if manual.exists():
        manual_map = json.loads(manual.read_text()).get("translations", {})
        trans_map.update({str(k): clean_zh(str(v)) for k, v in manual_map.items()})
    vtt_rows = parse_vtt(project / "index" / "transcript.vtt")
    cues = cue_map(vtt_rows, trans_map)
    overrides = load_overrides(run / "qa" / "final-word-overrides.json")
    timing_path = Path(args.timing).resolve() if args.timing else project / "index" / "words.json"
    if args.vtt_authoritative:
        source_words = load_vtt_timed_words(project / "index" / "transcript.vtt", timing_path)
    else:
        source_words = load_source_words(timing_path)
    cefr_path = Path(args.cefr).resolve() if args.cefr else None
    cefr_data = json.loads(cefr_path.read_text()) if cefr_path and cefr_path.exists() else {}
    cefr_items = list(cefr_data.get("items", []))
    qa_dir = run / "qa" / "subtitles"
    words_dir = run / "qa" / "final-words"
    qa_dir.mkdir(parents=True, exist_ok=True)
    words_dir.mkdir(parents=True, exist_ok=True)
    for cand in plan["candidates"]:
        work = run / "clips" / cand["slug"]
        work.mkdir(parents=True, exist_ok=True)
        cards, final_words = make_cards(cand, source_words, list(cues.values()), overrides,
                                        cefr_items)
        dur = cand["source_out_seconds"] - cand["source_in_seconds"]
        track = work / "subtitles.mov"
        render_track(cards, dur, track, work / "subframes")
        # Per-word PNGs are only an intermediate for qtrle; retain the compact
        # alpha track and timing records, not thousands of 8-bit RGBA frames.
        shutil.rmtree(work / "subframes", ignore_errors=True)
        (words_dir / f"{cand['slug']}.json").write_text(json.dumps({
            "schema_version": "friends-final-word-timing-1",
            "candidate": cand["name"], "source_in": cand["source_in"],
            "source_out": cand["source_out"], "source_in_seconds": cand["source_in_seconds"],
            "source_out_seconds": cand["source_out_seconds"],
            "words": sorted(final_words, key=lambda x: (x["start"], x["end"])),
            "source": str(timing_path),
        }, ensure_ascii=False, indent=2) + "\n")
        punct = [c for c in cards if re.search(r"[^\w\s\u3400-\u9fff]", c["zh"] + " " + " ".join(c["en_words"]))]
        cefr_matches = [m for c in cards for m in c.get("cefr_matches", [])]
        cefr_unmatched = [m for m in cefr_matches if not m.get("matched_zh")]
        (qa_dir / f"{cand['slug']}.json").write_text(json.dumps({
            "candidate": cand["name"], "slug": cand["slug"],
            "source_in": cand["source_in"], "source_out": cand["source_out"],
            "duration_seconds": round(dur, 3), "cards": len(cards),
            "subtitle_track": str(track), "resolution": [W, H], "fps": FPS,
            "timing_source": str(timing_path),
            "wording_source": str(project / "index" / "transcript.vtt") if args.vtt_authoritative else str(timing_path),
            "cefr": {
                "manifest": str(cefr_path) if cefr_path else None,
                "items_declared": len(cefr_items),
                "items_rendered": len({m.get("id") for m in cefr_matches}),
                "highlighted_spans": len(cefr_matches),
                "unmatched_chinese_spans": cefr_unmatched,
                "levels": sorted({m.get("level") for m in cefr_matches}),
            },
            "style": {
                "font_weight": 900, "zh_size_reference_px": ZH_SIZE,
                "en_size_reference_px": EN_SIZE, "stroke_px": STROKE,
                "max_width_px": MAX_W, "zh_y": ZH_Y, "en_y": EN_Y,
                "en_above_zh": True, "zh_static_white": True,
                "en_default_white": True, "en_active_yellow": "#FFD640",
                "punctuation": False, "max_en_lines": 2,
            },
            "punctuation_cards": len(punct),
            "word_timing_tolerance_ms": 80,
            "timing_records": sum(len(c["words"]) for c in cards),
        }, ensure_ascii=False, indent=2) + "\n")
        print(f"{cand['slug']}: {len(cards)} cards {len(final_words)} words -> {track}")


if __name__ == "__main__":
    main()
