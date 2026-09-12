#!/usr/bin/env python3
"""Method 2 — stage 1: acquire.

Two input modes:
  --url   platform link; downloads best audio, then a 360p analysis proxy
  --file  a long-form file already on disk; extracts audio and builds the proxy

Writes into <project>/source/ and records what it did in <project>/m2.json.

The proxy is mandatory for this method: vision-assisted discovery and shot
detection have to run before any ranges are known, so range-only acquisition
cannot serve it.
"""
import argparse
import json
import os
import subprocess
import sys

PROXY_H = 360


def is_hls(url):
    return ".m3u8" in url.lower()


def run(cmd, **kw):
    return subprocess.run(cmd, check=True, **kw)


def probe(path, entries, stream=None):
    cmd = ["ffprobe", "-v", "error"]
    if stream:
        cmd += ["-select_streams", stream]
    cmd += ["-show_entries", entries, "-of", "default=nw=1:nk=1", path]
    return subprocess.run(cmd, capture_output=True, text=True, check=True).stdout.strip()


def from_url(url, src):
    print(f"probing {url}")
    meta = json.loads(subprocess.run(
        ["yt-dlp", "-J", "--no-warnings", url], capture_output=True, text=True, check=True).stdout)
    vid = meta.get("id", "source")
    info = {"mode": "url", "url": url, "id": vid, "title": meta.get("title"),
            "channel": meta.get("channel") or meta.get("uploader"),
            "duration_seconds": meta.get("duration"),
            "upload_date": meta.get("upload_date")}
    print(f"  {info['title']}  ({info['duration_seconds']}s)")

    audio = f"{src}/{vid}-audio.m4a"
    if not os.path.exists(audio):
        print("downloading audio ...")
        run(["yt-dlp", "-f", "bestaudio", "-o", audio, "--no-warnings", url])
    info["full_audio"] = audio

    proxy = f"{src}/{vid}-proxy.mp4"
    if not os.path.exists(proxy):
        print(f"downloading {PROXY_H}p analysis proxy ...")
        run(["yt-dlp", "-f", f"bestvideo[height<={PROXY_H}]+bestaudio/best[height<={PROXY_H}]",
             "--recode-video", "mp4", "-o", proxy, "--no-warnings", url])
    info["proxy"] = proxy
    return info


def from_hls(url, src):
    """Acquire a direct HLS source (Kick exposes muxed AV variants)."""
    print(f"probing direct HLS {url}")
    meta = json.loads(subprocess.run(
        ["yt-dlp", "-J", "--no-warnings", url], capture_output=True, text=True, check=True).stdout)
    formats = [f for f in meta.get("formats", []) if f.get("protocol") == "m3u8_native"
               and f.get("url")]
    if not formats:
        raise RuntimeError("direct HLS manifest has no playable variants")

    # Kick's variants are muxed and carry the same AAC stereo track. Use the
    # 360p variant for both full-audio extraction and the mandatory analysis
    # proxy; this avoids downloading a multi-hour high-resolution video master.
    proxy_formats = [f for f in formats if f.get("height") == PROXY_H]
    if not proxy_formats:
        proxy_formats = [f for f in formats if (f.get("height") or 0) <= PROXY_H]
    if not proxy_formats:
        raise RuntimeError("direct HLS manifest has no <=360p analysis variant")
    proxy_fmt = max(proxy_formats, key=lambda f: (f.get("height") or 0, f.get("tbr") or 0))
    media_url = proxy_fmt["url"]
    vid = meta.get("id", "source")
    info = {"mode": "url", "url": url, "id": vid, "title": meta.get("title"),
            "channel": meta.get("channel") or meta.get("uploader"),
            "duration_seconds": meta.get("duration"),
            "upload_date": meta.get("upload_date"),
            "analysis_variant": proxy_fmt.get("format_id"),
            "analysis_variant_url": media_url,
            "audio_variant": proxy_fmt.get("format_id"),
            "audio_variant_url": media_url}
    print(f"  {info['title']}  ({info['duration_seconds']}s)")

    audio = f"{src}/{vid}-audio.m4a"
    if not os.path.exists(audio):
        print("extracting full audio from direct HLS ...")
        run(["ffmpeg", "-hide_banner", "-v", "error", "-user_agent", "Mozilla/5.0",
             "-i", media_url, "-map", "0:a:0", "-vn", "-c:a", "aac", "-b:a", "192k",
             "-ar", "48000", "-ac", "2", audio, "-y"])
    info["full_audio"] = audio

    proxy = f"{src}/{vid}-proxy.mp4"
    if not os.path.exists(proxy):
        print(f"building {PROXY_H}p analysis proxy ...")
        run(["ffmpeg", "-hide_banner", "-v", "error", "-user_agent", "Mozilla/5.0",
             "-i", media_url, "-map", "0:v:0", "-map", "0:a:0?",
             "-vf", f"scale=-2:{PROXY_H}", "-c:v", "libx264", "-preset", "veryfast",
             "-crf", "30", "-c:a", "aac", "-b:a", "96k", "-ar", "48000", "-ac", "2",
             proxy, "-y"])
    info["proxy"] = proxy
    return info


def from_file(path, src):
    if not os.path.exists(path):
        sys.exit(f"no such file: {path}")
    base = os.path.splitext(os.path.basename(path))[0]
    dur = float(probe(path, "format=duration"))
    info = {"mode": "file", "master": os.path.abspath(path), "id": base,
            "title": base, "duration_seconds": dur}
    print(f"  {base}  ({dur:.0f}s)")

    audio = f"{src}/{base}-audio.m4a"
    if not os.path.exists(audio):
        print("extracting audio ...")
        run(["ffmpeg", "-hide_banner", "-v", "error", "-i", path,
             "-vn", "-c:a", "aac", "-b:a", "192k", audio, "-y"])
    info["full_audio"] = audio

    proxy = f"{src}/{base}-proxy.mp4"
    if not os.path.exists(proxy):
        print(f"building {PROXY_H}p analysis proxy ...")
        run(["ffmpeg", "-hide_banner", "-v", "error", "-i", path,
             "-map", "0:v:0", "-map", "0:a:0?", "-vf", f"scale=-2:{PROXY_H}",
             "-c:v", "libx264", "-preset", "veryfast", "-crf", "30",
             "-c:a", "aac", "-b:a", "96k", "-ar", "48000", "-ac", "2", proxy, "-y"])
    info["proxy"] = proxy
    return info


def main():
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--url")
    g.add_argument("--file")
    ap.add_argument("--project", required=True, help="project directory")
    a = ap.parse_args()

    src = os.path.join(a.project, "source")
    os.makedirs(src, exist_ok=True)

    info = (from_hls(a.url, src) if is_hls(a.url) else from_url(a.url, src)) if a.url else from_file(a.file, src)

    state_path = os.path.join(a.project, "m2.json")
    state = json.load(open(state_path)) if os.path.exists(state_path) else {}
    state["source"] = info
    state["stage"] = "acquired"
    json.dump(state, open(state_path, "w"), indent=2)

    print(f"\naudio: {info['full_audio']}")
    print(f"proxy: {info['proxy']}")
    print(f"state: {state_path}")
    print("\nnext: m2_index.py --project " + a.project)


if __name__ == "__main__":
    main()
