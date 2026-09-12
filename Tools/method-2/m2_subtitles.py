#!/usr/bin/env python3
"""Method 2 — subtitles (spoken words on screen).

Distinct from the hook title. Off by default in the method; enabled per clip.

Builds one transparent alpha track rather than chaining many overlay inputs —
29 looped PNG inputs ran at roughly 13 KB/s in an earlier pilot.

Styles
  spec      Michael's spec: TikTok Sans, white, ONE line, max 30 chars,
            no punctuation, sat middle-to-lower.
  tiktok-native-one-line
            Saved T1 preset: TikTok Sans 500, lowercase, one physical line,
            punctuation/symbols stripped, numbers preserved, max 40 chars.
  active    Word-by-word highlight: the word being spoken is picked out in an
            accent colour while its phrase stays visible.
  kinetic   One or two words at a time, heavy caps, centred, for punchy delivery.
"""
import argparse
import json
import os
import re
import subprocess
import unicodedata

from PIL import Image, ImageDraw, ImageFont
from runtime import EN_FONT

FONT = EN_FONT
W, H = 1080, 1920
FPS = 30

STYLES = {
    # Michael's spec: one line, max 30 chars, no punctuation, white, mid-lower.
    # Size is set so 30 characters actually fit inside the safe width rather
    # than being silently truncated. A thin stroke is kept for legibility —
    # plain white is unreadable over the bright parts of this footage.
    "spec": dict(size=54, weight=800, y=1180, max_chars=30, max_lines=1,
                 strip_punct=True, fill=(255, 255, 255), stroke=5,
                 accent=None, max_words=99, wrap_w=780),
    # System/captions/saved/tiktok-native-one-line.yaml (T1). The minimum
    # size is a fit fallback only; group() splits at word boundaries first.
    "tiktok-native-one-line": dict(size=86, min_size=66, max_size=96,
                                    weight=500, y=1171, max_chars=40,
                                    max_lines=1, one_line=True, lower=True,
                                    strip_punct=True, strip_symbols=True,
                                    fill=(255, 255, 255), stroke=0,
                                    accent=None, max_words=99, wrap_w=950,
                                    mask_profanity=False),
    "active": dict(size=74, weight=900, y=1230, max_chars=40, max_lines=2,
                   strip_punct=True, fill=(255, 255, 255), stroke=7,
                   accent=(255, 214, 64), max_words=4, wrap_w=780),
    "kinetic": dict(size=96, weight=900, y=1130, max_chars=26, max_lines=2,
                    strip_punct=True, fill=(255, 255, 255), stroke=9,
                    accent=None, max_words=4, upper=True, wrap_w=760),
}
# No punctuation in subtitles. Ever, in every style — not a per-preset option.
# Apostrophes stay: stripping them breaks "don't" and "y'all".
PUNCT = str.maketrans("", "", ".,!?;:\"\u2014\u2026")

# Masked on screen only \u2014 the audio is never altered. Deliberately narrow:
# "damn" and "hell" are left alone, they carry real reactions ("damn bro, 62").
MASK = {"fuck", "fucks", "fucking", "fucked", "fucker", "motherfucker",
        "motherfuckers", "motherfucking", "shit", "shits", "bitch",
        "nigga", "nigger", "cunt", "pussy", "dick"}


def mask(word):
    """First letter then asterisks. Asterisks are not punctuation and survive PUNCT."""
    if word.lower().strip("'") not in MASK:
        return word
    return word[0] + "*" * (len(word) - 1)


def font_at(size, weight):
    f = ImageFont.truetype(FONT, size)
    try:
        f.set_variation_by_axes([36, 100, weight, 0])
    except Exception:
        pass
    return f


def clean_word(word, st):
    """Apply the selected preset's text rules without changing the audio.

    T1 strips punctuation and non-word symbols while preserving numbers. The
    Unicode-category filter handles apostrophes, dashes, emoji, and other
    symbols consistently instead of leaving legacy mask asterisks on screen.
    """
    token = word.translate(PUNCT)
    if st.get("strip_symbols"):
        # Drop symbols/punctuation without merging neighbouring words or
        # number ranges: 6–7 becomes "6 7", while don't becomes "dont".
        kept = []
        pending_sep = False
        for i, ch in enumerate(token):
            is_word = ch.isspace() or unicodedata.category(ch)[0] in ("L", "N")
            if is_word:
                if pending_sep and kept and not kept[-1].isspace():
                    kept.append(" ")
                kept.append(ch)
                pending_sep = False
            elif ch not in ("'", "’"):
                future_word = any(
                    nxt.isspace() or unicodedata.category(nxt)[0] in ("L", "N")
                    for nxt in token[i + 1:]
                )
                pending_sep = bool(kept and future_word)
        token = "".join(kept)
    if st.get("lower"):
        token = token.lower()
    if not token.strip():
        return ""
    if st.get("mask_profanity", True):
        token = mask(token)
    return token.strip()


def load_words(path, t0, t1):
    d = json.load(open(path))
    out = []
    for s in d["segments"]:
        for w in s.get("words", []):
            if t0 <= w["start"] <= t1:
                out.append({"t": w["word"].strip(), "s": w["start"], "e": w["end"]})
    return out


def join_words(words):
    """Recogniser output splits 'push-ups' into 'push' + '-ups' and 'y'all' into
    'y' + ''all'. Joined with spaces those render as 'push -ups' and 'y 'all'."""
    out = []
    for w in words:
        if out and w["t"][:1] in "-'" and len(w["t"]) > 1:
            out[-1] = dict(out[-1], t=out[-1]["t"] + w["t"], e=w["e"])
        else:
            out.append(w)
    return out


def group(words, st):
    """Chunk words into cards under the style's line/character budget."""
    cards, cur = [], []
    probe = ImageDraw.Draw(Image.new("RGBA", (1, 1)))

    def too_wide(text):
        if not st.get("one_line"):
            return False
        f = font_at(st["size"], st["weight"])
        return probe.textlength(text, font=f) > st.get("wrap_w", 780)

    for w in join_words(words):
        txt = clean_word(w["t"], st)
        if not txt:
            continue
        w = dict(w, t=txt)
        if cur:
            joined = " ".join(x["t"] for x in cur + [w])
            gap = w["s"] - cur[-1]["e"]
            if (gap > 0.5 or len(cur) >= st["max_words"] or
                    len(joined) > st["max_chars"] or too_wide(joined)):
                cards.append(cur)
                cur = []
        cur.append(w)
    if cur:
        cards.append(cur)

    out = []
    for c in cards:
        out.append({"words": c, "text": " ".join(x["t"] for x in c),
                    "s": c[0]["s"], "e": c[-1]["e"] + 0.10})
    for i in range(len(out) - 1):
        out[i]["e"] = min(out[i]["e"], out[i + 1]["s"] - 0.01)
    return [c for c in out if c["e"] > c["s"]]


def draw_card(card, st, active_word=None):
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    text = card["text"].upper() if st.get("upper") else card["text"]

    if st.get("one_line"):
        # T1 never wraps to a second line. group() normally keeps text at the
        # target size; reduce only within the saved preset's [66, 96] range for
        # an unusually wide single word.
        size = st["size"]
        f = font_at(size, st["weight"])
        max_w = st.get("wrap_w", 950)
        while d.textlength(text, font=f) > max_w and size > st.get("min_size", size):
            size -= 1
            f = font_at(size, st["weight"])
        total = d.textlength(text, font=f)
        x = W / 2 - total / 2
        d.text((x, st["y"]), text, font=f, fill=st["fill"] + (255,),
               stroke_width=0, anchor="lm")
        return img

    words = text.split()
    wrap_w = st.get("wrap_w", 780)

    def wrap_at(size):
        f = font_at(size, st["weight"])
        lines, cur = [], []
        for wd in words:
            trial = " ".join(cur + [wd])
            if cur and d.textlength(trial, font=f) > wrap_w:
                lines.append(cur)
                cur = [wd]
            else:
                cur.append(wd)
        if cur:
            lines.append(cur)
        return f, lines

    size = st["size"]
    f, lines = wrap_at(size)
    # shrink to fit the line budget — never truncate, that silently drops speech
    while st["max_lines"] and len(lines) > st["max_lines"] and size > 30:
        size -= 3
        f, lines = wrap_at(size)

    lh = int(size * 1.2)
    y0 = st["y"] - (len(lines) - 1) * lh / 2
    idx = 0
    for li, ln in enumerate(lines):
        total = d.textlength(" ".join(ln), font=f)
        x = W / 2 - total / 2
        for wd in ln:
            colour = st["fill"]
            if st["accent"] and active_word is not None and idx == active_word:
                colour = st["accent"]
            d.text((x, y0 + li * lh), wd, font=f, fill=colour + (255,),
                   stroke_width=st["stroke"], stroke_fill=(0, 0, 0, 255), anchor="lm")
            x += d.textlength(wd + " ", font=f)
            idx += 1
    return img


def build_track(cards, st, duration, out_path, work, fps=FPS):
    """One alpha .mov instead of many overlay inputs."""
    frames_dir = os.path.join(work, "subframes")
    os.makedirs(frames_dir, exist_ok=True)
    blank = os.path.join(frames_dir, "blank.png")
    Image.new("RGBA", (W, H), (0, 0, 0, 0)).save(blank)

    seq, t = [], 0.0
    for i, c in enumerate(cards):
        if c["s"] > t:
            seq.append((blank, c["s"] - t))
        if st["accent"]:
            for j, w in enumerate(c["words"]):
                p = os.path.join(frames_dir, f"c{i:03d}_{j}.png")
                draw_card(c, st, active_word=j).save(p)
                end = min(w["e"], c["e"]) if j < len(c["words"]) - 1 else c["e"]
                if end > w["s"]:
                    seq.append((p, end - max(w["s"], c["s"])))
        else:
            p = os.path.join(frames_dir, f"c{i:03d}.png")
            draw_card(c, st).save(p)
            seq.append((p, c["e"] - c["s"]))
        t = c["e"]
    if duration > t:
        seq.append((blank, duration - t))

    if not seq:
        seq.append((blank, duration))

    lst = os.path.join(frames_dir, "list.txt")
    with open(lst, "w") as f:
        for p, dur in seq:
            f.write(f"file '{os.path.abspath(p)}'\nduration {max(dur, 0.033):.3f}\n")
        f.write(f"file '{os.path.abspath(seq[-1][0])}'\n")

    subprocess.run(["ffmpeg", "-hide_banner", "-v", "error", "-f", "concat", "-safe", "0",
                    "-i", lst, "-vf", f"fps={fps},format=rgba", "-c:v", "qtrle",
                    "-t", f"{duration:.3f}", out_path, "-y"], check=True)
    return out_path


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--edl", required=True)
    ap.add_argument("--words", required=True)
    ap.add_argument("--style", default="spec", choices=list(STYLES))
    ap.add_argument("--out", default="subs.mov")
    ap.add_argument("--fps", type=float, default=FPS,
                    help="subtitle track cadence; match the locked picture")
    ap.add_argument("--start-after", type=float, default=0.0,
                    help="suppress subtitles before this time (the hook owns the opening)")
    a = ap.parse_args()

    edl = json.load(open(a.edl))
    st = STYLES[a.style]
    work = os.path.dirname(os.path.abspath(a.edl))

    # map source-time words onto the condensed output timeline
    mapped, t = [], 0.0
    for b in edl["beats"]:
        # A beat declared non-verbal has no speech to caption. Whatever the
        # recogniser produced over that music or crowd noise is hallucination —
        # on one pilot clip it invented a slur over a song sung in Chinese.
        if not b.get("non_verbal"):
            for w in load_words(a.words, b["in"], b["out"]):
                mapped.append({"t": w["t"], "s": t + (w["s"] - b["in"]),
                               "e": t + (w["e"] - b["in"])})
        t += b["out"] - b["in"]

    cards = group(mapped, st)
    if a.start_after > 0:
        # the opening belongs to the hook title alone; subtitles begin after it
        cards = [c for c in cards if c["s"] >= a.start_after]
    # Accept either the conventional filename (relative to the EDL folder)
    # or an explicit absolute/nested output path without duplicating the EDL
    # directory prefix.
    out_path = a.out if os.path.isabs(a.out) else os.path.join(work, a.out)
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    build_track(cards, st, t, out_path, work, a.fps)
    print(f"{a.style}: {len(cards)} cards over {t:.2f}s -> {a.out}")
    for c in cards[:6]:
        print(f"  {c['s']:6.2f}-{c['e']:6.2f}  {c['text']}")
