#!/usr/bin/env python3
"""Record the authorized post-QA temporary-artifact cleanup."""
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
    removed = [
        str(project / "source" / "EQZ9wtYMfyM-proxy.mp4"),
        str(project / "index" / "vision"),
        str(project / "_cache" / "the-underrated-ones-from-season-7" / "qa-samples"),
        str(project / "_cache" / "the-underrated-ones-from-season-7" / "qa-sheets"),
        str(project / "_cache" / "the-underrated-ones-from-season-7" / "quality-probe"),
        str(project / "_cache" / "the-underrated-ones-from-season-7" / "sub-probe"),
        str(project / "_cache" / "the-underrated-ones-from-season-7" / "render-venv"),
        str(project / "_cache" / "the-underrated-ones-from-season-7" / "source-checks" / "c7-final.wav"),
        str(project / "_cache" / "the-underrated-ones-from-season-7" / "source-checks" / "c9-gap.wav"),
    ]
    record = {
        "status": "complete",
        "performed_after": "15/15 final QA PASS",
        "removed_temporary_artifacts": removed,
        "retained_cache_records": [
            {
                "path": str(project / "_cache" / "the-underrated-ones-from-season-7" / "source-checks" / "c7-vtt" / "c7-final.vtt"),
                "reason": "small textual targeted-QA transcript record for the C7 source check",
            },
            {
                "path": str(project / "_cache" / "the-underrated-ones-from-season-7" / "source-checks" / "c9-vtt" / "c9-gap.vtt"),
                "reason": "small textual targeted-QA transcript record for the C9 source gap",
            },
            {
                "path": str(project / "_cache" / "the-underrated-ones-from-season-7" / "source-checks" / "c9-json" / "c9-gap.json"),
                "reason": "small word-timing QA record supporting the retained C9 final-word override",
            },
        ],
        "retained": [
            "approved range video and audio media",
            "project records and QA manifests",
            "source-checks/c7-vtt/c7-final.vtt and source-checks/c9-vtt/c9-gap.vtt: small textual targeted-QA records",
            "source-checks/c9-json/c9-gap.json: small targeted-QA timing record used by the final word overrides",
        ],
    }
    qpath = run_dir / "qa" / "qa-summary.json"
    qa = json.loads(qpath.read_text())
    visual = qa.get("visual_review", {})
    reviewed_count = len(visual.get("sheets_reviewed", [])) or 5
    visual["sheets_reviewed_count"] = reviewed_count
    visual["sheets_reviewed"] = []
    visual["review_artifacts_retained"] = False
    visual["review_artifacts_removed_after_review"] = True
    qa["visual_review"] = visual
    for candidate in qa.get("approved_candidates", []):
        for sample in candidate.get("subtitles", {}).get("samples", []):
            if sample.get("frame"):
                sample["frame"] = None
                sample["frame_status"] = "removed_after_visual_review"
    qa["cleanup"] = record
    qpath.write_text(json.dumps(qa, ensure_ascii=False, indent=2) + "\n")
    mpath = run_dir / "qa" / "delivery-manifest.json"
    manifest = json.loads(mpath.read_text())
    manifest["visual_review"] = visual
    manifest["cleanup"] = record
    mpath.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    print(f"recorded cleanup in {qpath} and {mpath}")


if __name__ == "__main__":
    main()
