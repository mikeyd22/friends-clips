#!/usr/bin/env python3
"""Expand a clip's beats into per-shot segments with a framing treatment each.

A beat usually spans several camera cuts. Holding one crop across all of them
is what left the subject out of frame on the wide two-shots. Cutting the beat
at real shot boundaries lets each shot carry the treatment its angle needs —
a crop centred on the speaker, or a split screen when both are in view.

Crop changes only ever happen at genuine source edits, so nothing snaps or
drifts inside a shot.
"""
import argparse
import json
import os

CROP_H = 720
CROP_W = int(round(CROP_H * 9 / 16))       # 405 from a 1280x720 source
SRC_W = 1280
SPLIT_W, SPLIT_H = 720, 640                # each half, 1.125 aspect -> 1080x960
MIN_SEG = 0.35                             # shorter than this and it flickers


def clamp(v, lo, hi):
    return max(lo, min(hi, v))


def treatment_for(subj):
    if subj["mode"] == "split":
        c1, c2 = subj["centres"]
        return {"mode": "split",
                "x1": int(clamp(c1 - SPLIT_W / 2, 0, SRC_W - SPLIT_W)),
                "x2": int(clamp(c2 - SPLIT_W / 2, 0, SRC_W - SPLIT_W))}
    centre = subj.get("centre", SRC_W / 2)
    return {"mode": "crop", "x": int(clamp(centre - CROP_W / 2, 0, SRC_W - CROP_W))}


def expand(edl, shots, labels, subjects):
    segments = []
    for b in edl["beats"]:
        cuts = [b["in"]]
        for (a, z) in shots:
            if b["in"] < a < b["out"]:
                cuts.append(a)
        cuts.append(b["out"])

        for s, e in zip(cuts, cuts[1:]):
            if e - s <= 0:
                continue
            mid = (s + e) / 2
            lab = None
            for i, (a, z) in enumerate(shots):
                if a <= mid < z:
                    lab = labels[i]
                    break
            subj = subjects.get(str(lab), {"mode": "centre"})
            seg = {"beat": b["id"], "in": round(s, 3), "out": round(e, 3),
                   "angle": lab, **treatment_for(subj)}
            # fold a flash-length segment into its neighbour rather than cutting to it
            if segments and segments[-1]["beat"] == b["id"] and e - s < MIN_SEG:
                segments[-1]["out"] = round(e, 3)
                continue
            segments.append(seg)
    return segments


def main(edl_path, project):
    edl = json.load(open(edl_path))
    ang = json.load(open(os.path.join(project, "index", "angles.json")))
    subjects = json.load(open(os.path.join(project, "index", "angle-subjects.json")))

    segs = expand(edl, ang["shots"], ang["labels"], subjects)
    edl["segments"] = segs
    json.dump(edl, open(edl_path, "w"), indent=2)

    splits = sum(1 for s in segs if s["mode"] == "split")
    print(f"{os.path.basename(os.path.dirname(edl_path))}: "
          f"{len(edl['beats'])} beats -> {len(segs)} segments ({splits} split-screen)")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--edl", required=True)
    ap.add_argument("--project", required=True)
    a = ap.parse_args()
    main(a.edl, a.project)
