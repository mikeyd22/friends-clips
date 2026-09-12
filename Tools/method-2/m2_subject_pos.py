#!/usr/bin/env python3
"""Locate the subject in each camera angle, without face detection.

Face detection has now failed on two different sources. For a fixed-camera
studio there is a far more reliable signal: within one angle the set never
moves and the person always does. Take the per-pixel temporal standard
deviation across many frames of the same angle and the speaker lights up.

Two well-separated peaks mean a two-shot, which gets a split-screen treatment
instead of a crop.
"""
import argparse
import json
import os
import subprocess

import numpy as np

FPS = 2
FW, FH = 160, 90
SRC_W = 1280
MAX_FRAMES = 240


def decode_gray(path):
    out = subprocess.run(
        ["ffmpeg", "-hide_banner", "-v", "error", "-i", path,
         "-vf", f"fps={FPS},scale={FW}:{FH},format=gray", "-f", "rawvideo", "-"],
        capture_output=True, check=True).stdout
    n = len(out) // (FW * FH)
    return np.frombuffer(out[:n * FW * FH], np.uint8).reshape(n, FH, FW).astype(np.float32)


def peaks(profile, thresh=0.45, min_mass=0.22, bridge=6):
    """Contiguous regions of motion, not local maxima.

    Suppressing a fixed window around the highest point fails when a subject
    occupies a wide band: the second 'peak' lands inside the same person. Take
    runs above a threshold instead, bridge small gaps, and keep only runs
    carrying a real share of the total motion so a stray mic or hand does not
    register as a second speaker.
    """
    p = np.convolve(profile, np.ones(9) / 9, mode="same")
    p = p / (p.max() + 1e-9)
    on = p >= thresh

    runs, i = [], 0
    while i < len(on):
        if on[i]:
            j = i
            while j < len(on) and on[j]:
                j += 1
            runs.append([i, j])
            i = j
        else:
            i += 1
    merged = []
    for r in runs:                        # bridge short gaps within one person
        if merged and r[0] - merged[-1][1] <= bridge:
            merged[-1][1] = r[1]
        else:
            merged.append(r)

    total = p.sum() + 1e-9
    out = []
    for a, b in merged:
        seg = p[a:b]
        if seg.sum() / total < min_mass:
            continue
        out.append(a + float((seg * np.arange(len(seg))).sum() / (seg.sum() + 1e-9)))
    return sorted(out)


def main(project):
    state = json.load(open(os.path.join(project, "m2.json")))
    ang = json.load(open(os.path.join(project, "index", "angles.json")))
    labels = np.array(ang["labels"])
    shots = ang["shots"]

    frames = decode_gray(state["source"]["proxy"])
    out = {}
    print("subject position per angle (x in the 1280-wide source):")
    for j in range(ang["k"]):
        members = np.where(labels == j)[0]
        idxs = []
        for m in members:
            a, b = shots[m]
            lo, hi = int(a * FPS) + 1, int(b * FPS)
            idxs.extend(range(lo, min(hi, len(frames))))
        if len(idxs) > MAX_FRAMES:
            idxs = list(np.array(idxs)[np.linspace(0, len(idxs) - 1, MAX_FRAMES).astype(int)])
        if len(idxs) < 8:
            out[str(j)] = {"mode": "centre"}
            print(f"  angle {j}: too few frames -> centre")
            continue

        sub = frames[idxs]
        motion = sub.std(axis=0)          # the set is static; the speaker is not
        prof = motion.sum(axis=0)
        pk = peaks(prof)
        centres = [int(round(p * SRC_W / FW)) for p in pk]

        if len(centres) == 2 and abs(centres[1] - centres[0]) > 380:
            out[str(j)] = {"mode": "split", "centres": centres}
            print(f"  angle {j}: TWO-SHOT -> split at {centres}")
        elif centres:
            out[str(j)] = {"mode": "crop", "centre": centres[0]}
            print(f"  angle {j}: single -> centre {centres[0]}")
        else:
            out[str(j)] = {"mode": "centre"}
            print(f"  angle {j}: no clear subject -> centre")

    path = os.path.join(project, "index", "angle-subjects.json")
    json.dump(out, open(path, "w"), indent=2)
    print(f"\nwrote {path}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", required=True)
    main(ap.parse_args().project)
