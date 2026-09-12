#!/usr/bin/env python3
"""Method 2 — on-screen hook title.

The hook title is the text that stops the scroll. It is NOT subtitles (spoken
word transcription), NOT the post caption (upload description), and NOT the
clip's internal name. Written per Tools/method-2/hook-title-guide.md.

Typography settings are measured, not guessed — see the guide's comparison
table. Type size is what stops the scroll and length costs type size, so the
renderer picks the largest size that still fits two lines.
"""
import argparse
import json
import re

from PIL import Image, ImageDraw, ImageFont
from runtime import EMOJI_FONT, EN_FONT

FONT = EN_FONT
EMOJI_STRIKE = 160
W, H = 1080, 1920
MAX_W = 640              # centred -> right edge 860, clear of the action rail at 875
HOOK_Y = 470
WEIGHT = 900
STROKE = 6
MIN_TWO_LINE = 80        # below this, three larger lines read better than two small ones
SIZES = [104, 100, 96, 92, 88, 84, 78, 72, 66, 60, 56, 52]
EMOJI_RE = re.compile("[\U0001F300-\U0001FAFF\U00002600-\U000027BF]")


def font_at(size, weight=WEIGHT):
    f = ImageFont.truetype(FONT, size)
    try:
        f.set_variation_by_axes([36, 100, weight, 0])
    except Exception:
        pass
    return f


def emoji_img(ch, target_h):
    f = ImageFont.truetype(EMOJI_FONT, EMOJI_STRIKE)
    tmp = Image.new("RGBA", (EMOJI_STRIKE * 2, EMOJI_STRIKE * 2), (0, 0, 0, 0))
    ImageDraw.Draw(tmp).text((10, 10), ch, font=f, embedded_color=True)
    tmp = tmp.crop(tmp.getbbox())
    return tmp.resize((max(1, int(tmp.width * target_h / tmp.height)), target_h), Image.LANCZOS)


def wrap(words, font, draw, max_w):
    lines, cur = [], ""
    for w in words:
        t = (cur + " " + w).strip()
        if cur and draw.textlength(t, font=font) > max_w:
            lines.append(cur)
            cur = w
        else:
            cur = t
    if cur:
        lines.append(cur)
    # never strand one or two short words on the last line
    changed = True
    while changed and len(lines) > 1:
        changed = False
        if draw.textlength(lines[-1], font=font) < draw.textlength(lines[-2], font=font) * 0.6:
            prev = lines[-2].split()
            if len(prev) > 1:
                cand = prev[-1] + " " + lines[-1]
                if draw.textlength(cand, font=font) <= max_w:
                    lines[-1] = cand
                    lines[-2] = " ".join(prev[:-1])
                    changed = True
    return lines


def render(text, out="hook.png", max_lines=2, y=None):
    emojis = EMOJI_RE.findall(text)
    words = EMOJI_RE.sub("", text).split()
    probe = ImageDraw.Draw(Image.new("RGBA", (10, 10)))

    def fits(size, budget):
        """Wrap at this size and report whether it fits the line and width budget.

        The width budget has to account for the stroke on both sides and for the
        emoji sitting inline on the last line, or the block silently overruns
        the safe zone.
        """
        f = font_at(size)
        gap = size * 0.22
        em_w = 0
        if emojis:
            em_w = emoji_img(emojis[0], int(size * 0.9)).width + gap
        # always reserve the emoji's width — it rides on the last line, and not
        # reserving it lets the block overrun the safe zone silently
        ls = wrap(words, f, probe, MAX_W - 2 * STROKE - em_w)
        if len(ls) > budget:
            return None
        widest = 0
        for i, ln in enumerate(ls):
            w_ = probe.textlength(ln, font=f) + 2 * STROKE
            if i == len(ls) - 1:
                w_ += em_w
            widest = max(widest, w_)
        return (size, ls) if widest <= MAX_W else None

    # Two lines is the nicer shape, but only while the type stays big — size is
    # what stops the scroll. So take the best two-line fit unless it forces the
    # type below MIN_TWO_LINE, in which case three larger lines read better.
    best2 = next((fits(s, 2) for s in SIZES if fits(s, 2)), None)
    best3 = next((fits(s, 3) for s in SIZES if fits(s, 3)), None)

    if best2 and best2[0] >= MIN_TWO_LINE:
        chosen, lines = best2
    elif best3:
        chosen, lines = best3
    elif best2:
        chosen, lines = best2
    else:
        chosen = lines = None
        print(f"  WARNING: {len(words)} words is too long for the 3-line budget; "
              f"the guide asks for 4-7. Falling back to the smallest size.")
    if chosen is None:                     # last resort: smallest size, width still respected
        chosen = SIZES[-1]
        f = font_at(chosen)
        em_w = (emoji_img(emojis[0], int(chosen * 0.9)).width + chosen * 0.22) if emojis else 0
        lines = wrap(words, f, probe, MAX_W - 2 * STROKE - em_w)

    font = font_at(chosen)
    line_h = int(chosen * 1.16)
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    base_y = HOOK_Y if y is None else y
    y0 = base_y - (len(lines) - 1) * line_h / 2

    for i, ln in enumerate(lines):
        last = i == len(lines) - 1
        w_px = d.textlength(ln, font=font)
        em = emoji_img(emojis[0], int(chosen * 0.9)) if (emojis and last) else None
        gap = chosen * 0.22
        total = w_px + (em.width + gap if em else 0)
        x = W / 2 - total / 2
        d.text((x, y0 + i * line_h), ln, font=font, fill=(255, 255, 255, 255),
               stroke_width=STROKE, stroke_fill=(0, 0, 0, 255), anchor="lm")
        if em:
            img.alpha_composite(em, (int(x + w_px + gap), int(y0 + i * line_h - em.height / 2)))

    img.save(out)
    bb = img.getbbox()
    safe = bb[0] >= 90 and bb[2] <= 875 and bb[1] >= 200 and bb[3] <= 1430
    print(f"hook: {text!r}")
    print(f"  {len(lines)} line(s) at {chosen}px{' + emoji' if emojis else ''}")
    print(f"  bounds x {bb[0]}-{bb[2]}, y {bb[1]}-{bb[3]}  safe zone: {'PASS' if safe else 'FAIL'}")
    return {"png": out, "size": chosen, "lines": len(lines), "safe": safe}


def choose_y(clip_path, window, block_h):
    """Place the hook in the clearest horizontal band, never across the subject.

    Samples the rendered opening and scores each candidate band by how much
    detail sits in it. The wall behind these subjects is flat, so the band with
    the least structure is the one that is not covering a face.
    """
    import subprocess
    import numpy as np

    hs, he = window
    picks = np.linspace(hs + 0.3, max(hs + 0.4, he - 0.3), 3)
    rows = []
    for t in picks:
        out = subprocess.run(
            ["ffmpeg", "-hide_banner", "-v", "error", "-ss", f"{t:.2f}", "-i", clip_path,
             "-frames:v", "1", "-vf", "scale=135:240,format=gray", "-f", "rawvideo", "-"],
            capture_output=True, check=True).stdout
        if len(out) >= 135 * 240:
            rows.append(np.frombuffer(out[:135 * 240], np.uint8).reshape(240, 135).astype(np.float32))
    if not rows:
        return HOOK_Y
    img = np.mean(rows, axis=0)
    detail = np.abs(np.diff(img, axis=0)).sum(axis=1)          # structure per row
    detail = np.convolve(detail, np.ones(5) / 5, mode="same")

    scale = 1920 / 240
    bh = max(2, int(block_h / scale))
    best, best_score = None, None
    for top in range(int(200 / scale), int((1430 - block_h) / scale)):
        score = detail[top:top + bh].sum()
        # nudge toward the upper third, where hooks normally sit
        score *= 1.0 + 0.35 * (top * scale / 1920)
        if best_score is None or score < best_score:
            best_score, best = score, top
    return int((best + bh / 2) * scale)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--text")
    ap.add_argument("--edl")
    ap.add_argument("--out", default="hook.png")
    a = ap.parse_args()
    text = a.text or json.load(open(a.edl))["hook_title"]
    render(text, a.out)
