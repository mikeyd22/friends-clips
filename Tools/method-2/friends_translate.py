#!/usr/bin/env python3
"""Translate the approved VTT cue set for the Friends subtitle renderer.

Translation is a packaging operation over the already approved VTT cues.  The
script never discovers or changes candidates.  A local Ollama model supplies
the draft conversational Simplified Chinese; punctuation is removed from the
stored subtitle text by the same deterministic cleaner used by rendering.
"""
from __future__ import annotations

import argparse
import json
import re
import unicodedata
import urllib.request
from pathlib import Path


def sec(s: str) -> float:
    z = s.replace(",", ".").split(":")
    return sum(float(v) * 60 ** (len(z) - i - 1) for i, v in enumerate(z))


def clean_text(value: str) -> str:
    out = []
    for ch in value.replace("\n", " "):
        cat = unicodedata.category(ch)
        if cat.startswith("P") or cat.startswith("S"):
            continue
        out.append(ch)
    return re.sub(r"\s+", " ", "".join(out)).strip()


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


def selected(row, spans):
    return any(row["start"] < end and row["end"] > start for start, end in spans)


def ollama_translate(items, model):
    payload = {str(item["id"]): item["en_source"] for item in items}
    prompt = (
        "Translate every English subtitle cue below into natural conversational "
        "Simplified Chinese for a Friends sitcom. Preserve joke intent, speaker "
        "meaning, names, titles, brands, and numbers. Keep proper names in the "
        "original English spelling where possible. Do not omit any IDs. Return "
        "ONLY a valid JSON object mapping each exact ID string to its Chinese "
        "translation. No commentary. Punctuation will be removed later. Empty "
        "strings are allowed only for explicit laughter music or silent reaction "
        "cues.\n\n"
        + json.dumps(payload, ensure_ascii=False)
    )
    body = json.dumps({
        "model": model,
        "prompt": prompt,
        "stream": False,
        "think": False,
        "format": "json",
        "options": {"temperature": 0.15},
    }).encode("utf-8")
    req = urllib.request.Request(
        "http://127.0.0.1:11434/api/generate",
        data=body,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=300) as response:
        data = json.load(response)
    parsed = json.loads(data["response"])
    return {str(k): clean_text(str(v)) for k, v in parsed.items()}


def translate_resilient(items, model):
    """Translate a batch, retrying only omitted IDs in smaller requests.

    The local model can occasionally return a valid but incomplete JSON object
    on a long batch.  Omitted cues are not editorially skipped: they are
    retried in bounded smaller batches, then individually as a last resort.
    """
    pending = list(items)
    got = {}
    try:
        got.update(ollama_translate(pending, model))
    except Exception:
        # Retry the whole request in smaller pieces when the response is not
        # valid JSON or the local server transiently fails.
        got = {}
    pending = [row for row in pending if row["id"] not in got]
    width = max(1, min(8, len(pending)))
    while pending:
        next_pending = []
        for i in range(0, len(pending), width):
            piece = pending[i:i + width]
            try:
                piece_got = ollama_translate(piece, model)
            except Exception:
                piece_got = {}
            got.update(piece_got)
            next_pending.extend(row for row in piece if row["id"] not in piece_got)
        if len(next_pending) == len(pending):
            if width == 1:
                break
            width = max(1, width // 2)
        pending = next_pending
    return got


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--vtt", required=True)
    ap.add_argument("--plan", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--model", default="gemma4:e4b")
    ap.add_argument("--batch", type=int, default=50)
    ap.add_argument("--force", action="store_true",
                    help="regenerate every cue instead of reusing checkpoints")
    args = ap.parse_args()
    vtt_rows = parse_vtt(Path(args.vtt))
    plan = json.loads(Path(args.plan).read_text())
    spans = [(c["source_in_seconds"], c["source_out_seconds"]) for c in plan["candidates"]]
    selected_rows = [row for row in vtt_rows if selected(row, spans)]
    # Checkpointing keeps a long subtitle pass resumable if the local model or
    # shell is interrupted.  The final file is still written in one stable
    # schema, but rows completed in an earlier invocation are reused.
    out_path = Path(args.out)
    translations = {}
    if out_path.exists() and not args.force:
        try:
            old = json.loads(out_path.read_text())
            translations = {str(row["id"]): row.get("zh", "") for row in old.get("rows", [])}
            print(f"resuming {len(translations)} previously translated cues")
        except Exception:
            translations = {}
    for i in range(0, len(selected_rows), args.batch):
        batch = selected_rows[i:i + args.batch]
        batch = [row for row in batch if row["id"] not in translations]
        if not batch:
            continue
        got = translate_resilient(batch, args.model)
        missing = [row["id"] for row in batch if row["id"] not in got]
        if missing:
            raise SystemExit(f"translation model omitted cue IDs: {missing}")
        translations.update(got)
        checkpoint_rows = [{**row, "zh": translations.get(row["id"], "")} for row in selected_rows if row["id"] in translations]
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps({
            "schema_version": "friends-bilingual-subtitles-1",
            "source_vtt": str(Path(args.vtt).resolve()),
            "approved_plan": str(Path(args.plan).resolve()),
            "model": args.model,
            "punctuation_policy": "all Unicode punctuation and symbols removed",
            "rows": checkpoint_rows,
        }, ensure_ascii=False, indent=2) + "\n")
        print(f"translated {len(batch)} cues; checkpoint {len(translations)} of {len(selected_rows)}")

    rows = []
    for row in selected_rows:
        rows.append({**row, "zh": translations.get(row["id"], "")})
    out = {
        "schema_version": "friends-bilingual-subtitles-1",
        "source_vtt": str(Path(args.vtt).resolve()),
        "approved_plan": str(Path(args.plan).resolve()),
        "model": args.model,
        "punctuation_policy": "all Unicode punctuation and symbols removed",
        "rows": rows,
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n")
    print(f"wrote {len(rows)} bilingual cue translations to {out_path}")


if __name__ == "__main__":
    main()
