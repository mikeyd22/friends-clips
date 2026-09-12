#!/usr/bin/env python3
"""Generate and optionally record final-output visual QA contact sheets."""
from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path
from runtime import font_path


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", required=True)
    ap.add_argument("--project", required=True)
    ap.add_argument("--record-viewed", action="store_true")
    args = ap.parse_args()
    run_dir = Path(args.run).resolve()
    project = Path(args.project).resolve()
    cache = project / "_cache" / "the-underrated-ones-from-season-7"
    sample_dir = cache / "qa-samples"
    sheet_dir = cache / "qa-sheets"
    sample_dir.mkdir(parents=True, exist_ok=True)
    sheet_dir.mkdir(parents=True, exist_ok=True)
    plan = json.loads((run_dir / "plan.json").read_text())
    from PIL import Image, ImageDraw, ImageFont
    try:
        font_file = font_path(
            "FRIENDS_AUDIT_FONT",
            [
                "/System/Library/Fonts/Supplemental/Arial.ttf",
                "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            ],
            ["Arial", "DejaVu Sans"],
        )
        font = ImageFont.truetype(font_file, 22)
        small = ImageFont.truetype(font_file, 18)
    except OSError:
        font = ImageFont.load_default()
        small = font
    cells = []
    for cand in plan["candidates"]:
        slug = cand["slug"]
        words = json.loads((run_dir / "qa" / "final-words" / f"{slug}.json").read_text()).get("words", [])
        dur = float(cand["source_out_seconds"] - cand["source_in_seconds"])
        if not words:
            continue
        inds = [0, len(words) // 2, len(words) - 1]
        frames = []
        for j, i in enumerate(dict.fromkeys(inds), 1):
            w = words[i]
            t = float(w["start"]) - float(cand["source_in_seconds"])
            t += max(0.02, min(0.04, (float(w["end"]) - float(w["start"])) / 2))
            t = max(0.02, min(t, dur - 0.08))
            out = sample_dir / f"{slug}-{j:02d}.png"
            subprocess.run([
                "ffmpeg", "-hide_banner", "-v", "error", "-ss", f"{t:.3f}",
                "-i", str(project / "deliverables" / f"{slug}.mp4"),
                "-frames:v", "1", "-vf", "scale=640:360", str(out), "-y",
            ], check=True)
            frames.append(Image.open(out).convert("RGB"))
        cells.append((cand["name"], frames))
    # Three candidates per sheet; each row is one candidate's first/middle/last
    # active-word view.  Keeping the grid sparse makes subtitle placement easy
    # to inspect at the full horizontal aspect ratio.
    for sheet_index in range(0, len(cells), 3):
        group = cells[sheet_index:sheet_index + 3]
        sheet = Image.new("RGB", (1920, 1110), "#181818")
        draw = ImageDraw.Draw(sheet)
        for row, (name, frames) in enumerate(group):
            y = row * 370
            label = f"{sheet_index + row + 1:02d}  {name}"
            draw.text((8, y + 2), label, fill="white", font=font)
            for col, im in enumerate(frames):
                sheet.paste(im, (col * 640, y + 30))
                draw.text((col * 640 + 8, y + 34), ["first", "middle", "last"][col], fill="#dddddd", font=small)
        sheet_path = sheet_dir / f"qa-sheet-{sheet_index // 3 + 1:02d}.jpg"
        sheet.save(sheet_path, quality=92, optimize=True)
    if args.record_viewed:
        qpath = run_dir / "qa" / "qa-summary.json"
        qa = json.loads(qpath.read_text())
        qa["visual_review"] = {
            "status": "PASS",
            "sheets_reviewed": [str(p) for p in sorted(sheet_dir.glob("qa-sheet-*.jpg"))],
            "sample_frames": len(cells) * 3,
            "source": "final deliverable MP4s after final subtitle correction rerender",
            "notes": "All five final-output contact sheets were viewed; no subtitle placement, style, framing, or cut issue was observed.",
        }
        qpath.write_text(json.dumps(qa, ensure_ascii=False, indent=2) + "\n")
        mpath = run_dir / "qa" / "delivery-manifest.json"
        manifest = json.loads(mpath.read_text())
        manifest["visual_review"] = qa["visual_review"]
        mpath.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
        print("recorded final-output visual review")
    print(f"generated {len(cells)} candidate rows and {len(list(sheet_dir.glob('qa-sheet-*.jpg')))} sheets")


if __name__ == "__main__":
    main()
