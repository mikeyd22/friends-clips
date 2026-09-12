#!/usr/bin/env python3
"""Method 2 — classify the camera angles of a multi-camera source.

A studio podcast cuts between a small fixed set of framings. Detecting faces
frame by frame is unreliable (it failed on this footage, and on crowd footage
before it), but the angles themselves repeat, so cluster the shots by
appearance and let a human or vision model assign a crop treatment to each
cluster once.

Stage 1 (this script, --cluster): decode the proxy once, take a representative
frame per shot, cluster them, write a labelled contact sheet.
Stage 2 (--apply): expand a clip's beats into per-shot segments and attach the
crop treatment its cluster was assigned.
"""
import argparse
import json
import os
import subprocess

import numpy as np
from PIL import Image

FPS = 2
FW, FH = 160, 90


def decode_gray(path):
    out = subprocess.run(
        ["ffmpeg", "-hide_banner", "-v", "error", "-i", path,
         "-vf", f"fps={FPS},scale={FW}:{FH},format=gray", "-f", "rawvideo", "-"],
        capture_output=True, check=True).stdout
    n = len(out) // (FW * FH)
    return np.frombuffer(out[:n * FW * FH], np.uint8).reshape(n, FH, FW).astype(np.float32)


def shots_from_cuts(cuts, duration, min_len=0.6):
    edges = [0.0] + [c for c in cuts if 0 < c < duration] + [duration]
    shots = []
    for a, b in zip(edges, edges[1:]):
        if b - a >= min_len:
            shots.append((a, b))
        elif shots:                       # fold a flash-cut into the previous shot
            shots[-1] = (shots[-1][0], b)
    return shots


def kmeans(X, k, iters=40, seed=0):
    rng = np.random.default_rng(seed)
    C = X[rng.choice(len(X), k, replace=False)]
    for _ in range(iters):
        d = ((X[:, None, :] - C[None, :, :]) ** 2).sum(-1)
        lab = d.argmin(1)
        for j in range(k):
            if (lab == j).any():
                C[j] = X[lab == j].mean(0)
    return lab, C


def cluster(project, k):
    state = json.load(open(os.path.join(project, "m2.json")))
    idx = state["index"]
    cuts = json.load(open(idx["shots"]))["cuts"]
    dur = state["source"]["duration_seconds"]

    print("decoding proxy once ...")
    frames = decode_gray(state["source"]["proxy"])
    shots = shots_from_cuts(cuts, dur)
    print(f"  {len(frames)} sample frames, {len(shots)} shots")

    feats, reps = [], []
    for a, b in shots:
        i = min(len(frames) - 1, int(((a + b) / 2) * FPS))
        f = frames[i]
        feats.append((f / 255.0).ravel())
        reps.append(i)
    X = np.array(feats)
    X = (X - X.mean(1, keepdims=True)) / (X.std(1, keepdims=True) + 1e-6)

    lab, C = kmeans(X, k)
    print(f"  clustered into {k} angles:")
    order = []
    for j in range(k):
        n = int((lab == j).sum())
        # medoid: the shot closest to its cluster centre
        members = np.where(lab == j)[0]
        d = ((X[members] - C[j]) ** 2).sum(1)
        medoid = members[d.argmin()]
        order.append((j, n, shots[medoid][0] + 0.5))
        print(f"    angle {j}: {n:4d} shots  ({100*n/len(shots):4.1f}%)  example at {shots[medoid][0]:.1f}s")

    # contact sheet from the full-resolution master so the crop can be judged
    master = state["source"].get("master") or state["source"]["proxy"]
    tiles = []
    for j, n, t in order:
        p = os.path.join(project, "index", f"_angle{j}.png")
        subprocess.run(["ffmpeg", "-hide_banner", "-v", "error", "-ss", f"{t:.2f}",
                        "-i", master, "-frames:v", "1", "-vf", "scale=420:-2", p, "-y"],
                       check=True)
        tiles.append(p)
    ims = [Image.open(p) for p in tiles]
    wsum = sum(i.width for i in ims)
    sheet = Image.new("RGB", (wsum + 8 * len(ims), ims[0].height + 8), (14, 14, 14))
    x = 4
    for im in ims:
        sheet.paste(im, (x, 4))
        x += im.width + 8
    out = os.path.join(project, "index", "angles.png")
    sheet.save(out)
    for p in tiles:
        os.remove(p)

    json.dump({"k": k, "shots": shots, "labels": lab.tolist(),
               "counts": {str(j): int((lab == j).sum()) for j in range(k)}},
              open(os.path.join(project, "index", "angles.json"), "w"))
    print(f"\nwrote {out} — angles left to right are 0..{k-1}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", required=True)
    ap.add_argument("--k", type=int, default=6)
    a = ap.parse_args()
    cluster(a.project, a.k)
