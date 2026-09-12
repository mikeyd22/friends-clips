#!/usr/bin/env python3
"""Write a minimal Premiere/FCP XML assembly of the approved source spans.

The XML deliberately references the editable handled range media and its
matching stereo WAV, not a flattened delivery.  Every candidate remains a
separate linked video/audio pair on a continuous master timeline; sequence
markers preserve the original long-form source timecodes and approved names.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from xml.etree import ElementTree as ET


FPS_NUM, FPS_DEN = 24, 1


def frames(seconds: float) -> int:
    return round(seconds * FPS_NUM / (FPS_DEN * 1.001))


def add(parent: ET.Element, tag: str, text: str | int | float | None = None, **attrs) -> ET.Element:
    e = ET.SubElement(parent, tag, attrs)
    if text is not None:
        e.text = str(text)
    return e


def rate(parent: ET.Element) -> None:
    r = add(parent, "rate")
    add(r, "timebase", FPS_NUM)
    add(r, "ntsc", "TRUE")


def characteristics(parent: ET.Element) -> None:
    sc = add(parent, "samplecharacteristics")
    rate(sc)
    add(sc, "width", 1920)
    add(sc, "height", 1080)
    add(sc, "anamorphic", "FALSE")
    add(sc, "pixelaspectratio", "square")
    add(sc, "fielddominance", "none")


def media_file(parent: ET.Element, file_id: str, path: Path, duration_frames: int,
               kind: str) -> None:
    f = add(parent, "file", id=file_id)
    add(f, "name", path.name)
    add(f, "pathurl", path.resolve().as_uri())
    add(f, "duration", duration_frames)
    rate(f)
    if kind == "video":
        m = add(f, "media")
        v = add(m, "video")
        add(v, "duration", duration_frames)
        characteristics(v)
    else:
        add(f, "timecode")
        m = add(f, "media")
        a = add(m, "audio")
        sc = add(a, "samplecharacteristics")
        add(sc, "samplerate", 48000)
        add(sc, "depth", 24)
        add(a, "channelcount", 2)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    run_dir = Path(args.run).resolve()
    out = Path(args.out).resolve()
    plan = json.loads((run_dir / "plan.json").read_text())
    root = ET.Element("xmeml", version="5")
    seq = add(root, "sequence", id="friends-method-2-approved")
    add(seq, "name", "Friends Method 2 Approved Clips")
    total = sum(frames(c["source_out_seconds"] - c["source_in_seconds"]) for c in plan["candidates"])
    add(seq, "duration", total)
    rate(seq)
    tc = add(seq, "timecode")
    rate(tc)
    add(tc, "string", "00:00:00:00")
    add(tc, "displayformat", "NDF")
    media = add(seq, "media")
    video = add(media, "video")
    fmt = add(video, "format")
    characteristics(fmt)
    vtrack = add(video, "track")
    audio = add(media, "audio")
    add(audio, "numOutputChannels", 2)
    atrack = add(audio, "track")

    timeline = 0
    markers = []
    for i, c in enumerate(plan["candidates"], 1):
        slug = c["slug"]
        dur = frames(c["source_out_seconds"] - c["source_in_seconds"])
        media_in = frames(float(c.get("media_in_seconds", 30.0)))
        video_path = run_dir / "source" / "ranges" / f"{slug}.mp4"
        audio_path = run_dir / "source" / "ranges" / f"{slug}-audio.wav"
        vid_id, aud_id = f"file-video-{i:02d}", f"file-audio-{i:02d}"
        v_id, a_id = f"clip-video-{i:02d}", f"clip-audio-{i:02d}"
        vc = add(vtrack, "clipitem", id=v_id, name=c["name"])
        add(vc, "masterclipid", f"master-{i:02d}")
        add(vc, "duration", dur)
        rate(vc)
        add(vc, "start", timeline)
        add(vc, "end", timeline + dur)
        add(vc, "in", media_in)
        add(vc, "out", media_in + dur)
        media_file(vc, vid_id, video_path, round((float(c["handled_end"]) - float(c["handled_start"])) * 24000 / 1001), "video")
        add(vc, "enabled", "TRUE")
        add(vc, "anamorphic", "FALSE")
        add(vc, "alphatype", "none")
        link = add(vc, "link")
        add(link, "linkclipref", a_id)
        add(link, "mediatype", "audio")
        add(link, "trackindex", 1)
        add(link, "clipindex", i)

        ac = add(atrack, "clipitem", id=a_id, name=c["name"])
        add(ac, "masterclipid", f"master-{i:02d}")
        add(ac, "duration", dur)
        rate(ac)
        add(ac, "start", timeline)
        add(ac, "end", timeline + dur)
        add(ac, "in", media_in)
        add(ac, "out", media_in + dur)
        media_file(ac, aud_id, audio_path, round((float(c["handled_end"]) - float(c["handled_start"])) * 48000), "audio")
        st = add(ac, "sourcetrack")
        add(st, "mediatype", "audio")
        add(st, "trackindex", 1)
        add(ac, "enabled", "TRUE")
        link = add(ac, "link")
        add(link, "linkclipref", v_id)
        add(link, "mediatype", "video")
        add(link, "trackindex", 1)
        add(link, "clipindex", i)

        markers.append((timeline, timeline + dur, c))
        timeline += dur

    for start, end, c in markers:
        marker = add(seq, "marker")
        add(marker, "in", start)
        add(marker, "out", end)
        add(marker, "name", c["name"])
        add(marker, "comment", (
            f"Approved candidate {c['scout_id']} | original source timecode "
            f"{c['source_in']}–{c['source_out']} | source seconds "
            f"{c['source_in_seconds']:.3f}–{c['source_out_seconds']:.3f} | "
            f"continuous single span no overlap"
        ))

    out.parent.mkdir(parents=True, exist_ok=True)
    ET.ElementTree(root).write(out, encoding="UTF-8", xml_declaration=True)
    print(f"wrote {len(plan['candidates'])} linked approved clips to {out}")


if __name__ == "__main__":
    main()
