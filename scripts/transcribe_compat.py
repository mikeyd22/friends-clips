#!/usr/bin/env python3
"""Portable VTT/JSON transcription adapter for the Friends workflow.

It accepts the small MLX-Whisper-compatible argument subset used by the
Method 2 tools, but runs with faster-whisper so the same repository can work
in Codex Cloud as well as on a local computer.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path


def as_bool(value: str | bool | None, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def vtt_time(seconds: float) -> str:
    seconds = max(0.0, float(seconds))
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = seconds % 60
    return f"{hours:02d}:{minutes:02d}:{secs:06.3f}".replace(".", ",")


def model_name(raw: str | None) -> str:
    configured = os.environ.get("FRIENDS_WHISPER_MODEL")
    value = configured or raw or "large-v3"
    value = value.rsplit("/", 1)[-1]
    if value in {"whisper-large-v3-turbo", "large-v3-turbo"}:
        return "large-v3-turbo"
    if value in {"whisper-large-v3", "large-v3"}:
        return "large-v3"
    return value


def main() -> int:
    parser = argparse.ArgumentParser(add_help=True)
    parser.add_argument("audio")
    parser.add_argument("--model")
    parser.add_argument("--language")
    parser.add_argument("--output-format", default="vtt")
    parser.add_argument("--output-dir", default=".")
    parser.add_argument("--output-name", default=None)
    parser.add_argument("--word-timestamps", default="False")
    parser.add_argument("--condition-on-previous-text", default="True")
    parser.add_argument("--verbose", default="False")
    args, _unknown = parser.parse_known_args()

    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:
        raise SystemExit(
            "faster-whisper is not installed; run scripts/setup.sh first"
        ) from exc

    device = os.environ.get("FRIENDS_WHISPER_DEVICE", "cpu")
    compute_type = os.environ.get(
        "FRIENDS_WHISPER_COMPUTE_TYPE",
        "float16" if device == "cuda" else "int8",
    )
    language = None if not args.language or args.language == "auto" else args.language
    model = WhisperModel(model_name(args.model), device=device, compute_type=compute_type)
    segments, _info = model.transcribe(
        args.audio,
        language=language,
        word_timestamps=as_bool(args.word_timestamps),
        condition_on_previous_text=as_bool(args.condition_on_previous_text, True),
        vad_filter=False,
    )

    rows = []
    for segment in segments:
        words = []
        for word in (segment.words or []):
            words.append({
                "word": word.word,
                "start": round(float(word.start), 3),
                "end": round(float(word.end), 3),
            })
        rows.append({
            "id": len(rows),
            "start": round(float(segment.start), 3),
            "end": round(float(segment.end), 3),
            "text": segment.text.strip(),
            "words": words,
        })

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    stem = args.output_name or Path(args.audio).stem
    fmt = args.output_format.lower()
    if fmt == "json":
        (output_dir / f"{stem}.json").write_text(
            json.dumps({"segments": rows}, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    elif fmt == "vtt":
        lines = ["WEBVTT", ""]
        for row in rows:
            lines.extend([
                f"{vtt_time(row['start'])} --> {vtt_time(row['end'])}",
                row["text"],
                "",
            ])
        (output_dir / f"{stem}.vtt").write_text("\n".join(lines), encoding="utf-8")
    else:
        raise SystemExit(f"unsupported output format: {fmt}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
