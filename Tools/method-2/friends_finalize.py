#!/usr/bin/env python3
"""Create the concise final delivery manifest from the completed QA gate."""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", required=True)
    ap.add_argument("--project", required=True)
    args = ap.parse_args()
    run_dir = Path(args.run).resolve()
    project = Path(args.project).resolve()
    plan = json.loads((run_dir / "plan.json").read_text())
    qa = json.loads((run_dir / "qa" / "qa-summary.json").read_text())
    acq = json.loads((run_dir / "qa" / "range-acquisition.json").read_text())
    render = json.loads((run_dir / "qa" / "render-manifest.json").read_text())
    render_by_slug = {x["slug"]: x for x in render.get("candidates", [])}
    qa_by_slug = {x["slug"]: x for x in qa.get("approved_candidates", [])}
    outputs = []
    for cand in plan["candidates"]:
        slug = cand["slug"]
        q = qa_by_slug.get(slug, {})
        a = next((x for x in acq.get("candidates", []) if x.get("slug") == slug), {})
        outputs.append({
            "scout_id": cand["scout_id"],
            "name": cand["name"],
            "slug": slug,
            "source_in": cand["source_in"],
            "source_out": cand["source_out"],
            "duration_seconds": round(float(cand["source_out_seconds"] - cand["source_in_seconds"]), 3),
            "handled_start": cand.get("handled_start"),
            "handled_end": cand.get("handled_end"),
            "alignment": a.get("alignment", {}).get("status"),
            "render": render_by_slug.get(slug, {}).get("status"),
            "qa": q.get("overall"),
            "file": str(project / "deliverables" / f"{slug}.mp4") if q.get("overall") == "PASS" else None,
        })
    counts = qa.get("counts", {})
    manifest = {
        "schema_version": "friends-delivery-manifest-1",
        "project": str(project),
        "run": str(run_dir),
        "status": "PASS" if counts.get("fail", 1) == 0 and qa.get("xml", {}).get("status") == "PASS" else "FAIL",
        "source": {
            "youtube_id": "EQZ9wtYMfyM",
            "video_format": "137 | 1920x1080 H.264 High yuv420p video-only DASH",
            "audio_format": "140 | AAC stereo 128 kbps 44100 Hz",
            "highest_actual_quality_used": True,
            "framing": "original horizontal 16:9",
            "cadence": "24000/1001",
            "handles_seconds": 30.0,
        },
        "edit": {
            "method": "full-automated-ai-clipping",
            "continuous_single_source_span": True,
            "no_overlap": True,
            "approved_candidates_only": True,
            "subtitles": "burned Simplified Chinese above English",
            "visible_punctuation": False,
            "music": False,
            "b_roll": False,
            "effects_transitions_reframing": False,
        },
        "counts": {
            "approved": counts.get("approved", len(outputs)),
            "pass": counts.get("pass", 0),
            "fail": counts.get("fail", 0),
            "deliverables": len(qa.get("deliverables", [])),
            "review": len(qa.get("review", [])),
        },
        "outputs": outputs,
        "qa_summary": str(run_dir / "qa" / "qa-summary.json"),
        "xml": qa.get("xml", {}),
        "visual_review": qa.get("visual_review", {}),
    }
    out = run_dir / "qa" / "delivery-manifest.json"
    out.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    print(f"wrote {out}")
    print(json.dumps(manifest["counts"], indent=2))


if __name__ == "__main__":
    main()
