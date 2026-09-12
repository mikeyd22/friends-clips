# Method 2 — approved automated clipping

Method 2 is fully approved and operational. The current contract is
`System/methods/full-automated-ai-clipping.yaml` (v4, hardening approved
2026-09-06). The planner authorizes the edit; completed agent quality checks
authorize delivery. There is no routine human approval step.

Existing project-specific scripts and historical deliveries are retained.
Use these shared tools for new work. An old file without current input or review
provenance is not silently relabeled as verified.

## Workflow and ownership

1. **Executor: acquire.** Obtain full audio and a small full-source proxy. Keep
   native source audio in the proxy so its timeline can be independently
   verified. Download full-quality video only for approved ranges.
2. **Executor: discovery index.** Generate full-source VTT, coverage warnings,
   energy, shots and vision sheets. Do not run full-source word timing.
3. **Scout: evidence.** Read VTT plus coverage and visual signals. Missing text
   is not silence. Preserve uncertain coverage for the planner.
4. **Planner: shortlist.** Return source spans labeled `shortlisted` or
   `approved_for_timing`; do not authorize rendering yet.
5. **Executor: selected timing.** Time only shortlisted spans plus handles.
   Overlapping spans merge; decoder windows overlap for context while each word
   belongs to one core. Explicitly nonverbal candidates need visual boundaries.
6. **Planner: lock.** Use selected timing and source context to approve the EDL.
   Final candidates use `status: approved_for_execution`.
7. **Executor: ranges and alignment.** Clamp handles to the source duration,
   acquire approved ranges, verify actual video independently, and retain the
   resulting SHA-256-bound range report. Missing or uncertain evidence is held.
8. **Executor and planner: draft review.** Inspect moving excerpts and stills,
   source detail, crop continuity and the actual cut's meaning. Return only
   affected candidates to the planner; never reopen discovery routinely.
9. **Executor and planner: final QA.** Generate the final encode, verify actual
   output and complete reviews bound to that final render, EDL and VTT. A draft
   approval does not approve different final bytes.
10. **Executor: deliver.** Only current passing outputs enter `deliverables/`.
    Failed or pending clips stay held. Existing different files are preserved.

Independent downloads or technical operations may run with bounded concurrency
after approval; the editorial stages stay sequential. Rendering uses per-beat
files to avoid the original reordered-filter-graph stall.

## Acquire, index and time selected spans

```bash
python3 Tools/method-2/m2_acquire.py --url "<source-url>" --project <project>
# Or use an existing local master:
python3 Tools/method-2/m2_acquire.py --file <source-file> --project <project>

python3 Tools/method-2/m2_index.py --project <project> --language en
# For an intentionally multilingual source use --language auto.

# After the planner returns a shortlist:
python3 Tools/method-2/m2_index.py --project <project> \
  --timing-plan <project>/shortlist.json --handles 30

# After the planner locks plan.json:
python3 Tools/method-2/m2_ranges.py --project <project> --handles 30
```

Selected timing writes `index/words-selected-<input-key>.json` and stores the
path in `m2.json`. It does not overwrite historical full-source `words.json`.
`index/transcript-coverage.json` retains the source/model/language/chunk record,
unexplained gaps and suspicious intervals; suspected ASR text stays in the VTT.
A legacy VTT with no matching input record requires explicit validation or
migration instead of automatic reuse.

The range report is `qa/range-acquisition.json`. Only candidate-specific PASS
records with matching video/audio hashes and original source offsets can gate
assembly. Native video audio or a verified proxy establishes video alignment;
a separately extracted replacement WAV does not. Low-confidence or drifting
alignment returns a nonzero status. `--validate-existing` permits independent
verification of legacy range media; it never permits overwriting it.

## Build, inspect and deliver

The EDL preserves `slug`, original `source_in` / `source_out`,
`handled_start` / `handled_end`, `source_video`, `source_audio`, `source_vtt`,
`source_identity`, and `alignment_report`. Copy `source_identity` exactly from
the range report (also carried into the acquired plan). Beat `in` / `out` remain local to the acquired range.
Copy these fields from the locked plan and verified range record; never infer
an offset from a filename. `audience_profile` selects duration preferences.

```bash
# Cheap draft; no delivery:
python3 Tools/method-2/m2_build.py --project <project> --edl <edl.json>

# After draft review, build final bytes and produce QA evidence:
python3 Tools/method-2/m2_build.py --project <project> --edl <edl.json> \
  --quality --no-route

# Extract a PENDING review record for those exact final bytes:
python3 Tools/method-2/m2_review.py --edl <edl.json> \
  --render <final.mp4> --vtt <rendered-vtt-from-qa-report> \
  --output <pending-review.json>

# After executor visual review and planner semantic review, verify without rebuilding:
python3 Tools/method-2/m2_build.py --project <project> --edl <edl.json> \
  --quality --skip-build --clip <final.mp4> --review <completed-review.json>
```

The initial final-quality run and review-template command return status 2 while
required reviews are pending. This is intentional: the MP4 may be ready for
review while still ineligible for delivery. The final verification must use
`--skip-build` so a new encode cannot invalidate the completed review.

Keep the reviewed evidence and report hashes intact. Complete every still,
moving excerpt, visual and semantic check only after actual inspection. Recheck
only affected candidates when an edit changes; do not copy a prior PASS onto a
new render. A passed final verification routes into `deliverables/`; use
`--no-route` to update QA without copying an export into delivery.

## Quality checks

- **Technical:** duration from the audience profile or explicit approved EDL
  override, requested codec and dimensions, audio loudness/peak, A/V duration,
  actual presentation cadence, black/frozen openings and open-ended frozen tails.
- **Audio content:** transcribe actual rendered speech to VTT and compare
  content-word overlap. The 0.25 threshold detects missing or unrelated speech;
  it does not establish semantic faithfulness. Explicit wholly nonverbal clips
  use visual evidence instead of invented words.
- **Visual:** inspect stills and moving excerpts at the opening, ending, action
  payoff and every splice. Record source resolution, crop magnification,
  subject identity, payoff visibility, crop continuity and visible stutter.
- **Semantic:** the planner checks material qualifiers, meaning, speakers,
  promise, payoff and reorder context against the rendered VTT and source
  context. The executor persists the planner's record without inventing a pass.
- **Delivery:** every required check and review must match the current source
  files, EDL and final render. Evidence extraction alone is never a review.

## Audience feedback

`System/audiences/legacy-short-form.json` retains the existing 33-second
preferred ceiling, 35-second hard ceiling and one-in-three published Send
preference. Their local evidence is n=3, low confidence. They are account
preferences, not universal platform rules. The coordinator copies the selected
project audience profile into the EDL's `audience_profile` field. Explicit
approved `duration_policy` overrides are supported; no duration floor is allowed.

Copy `Templates/published-clip-feedback.json`, enter the actual pre-publication
predictions and user-supplied post metrics, then:

```bash
python3 Tools/method-2/m2_feedback.py --project <project> \
  --record <observation.json> --render <exact-published.mp4>
```

Missing metrics stay `null`. Reports group comparable source types, platforms,
audiences and observation intervals. They calculate rates only from records
with available denominators and prompt review after the configured cohort
threshold. They never change editing policies or claim causality automatically.

## Shared files

| File | Responsibility |
|---|---|
| `m2_acquire.py` | Full audio and analysis proxy |
| `m2_index.py` | Discovery VTT/index, selected word timing |
| `m2_ranges.py`, `m2_alignment.py` | Approved range acquisition and independent alignment |
| `m2_build.py` | Cached per-beat drafts/finals, technical checks, safe delivery |
| `m2_quality.py`, `m2_review.py` | Review evidence, hash validation and delivery gates |
| `m2_policy.py` | Explicit audience duration defaults and approved overrides |
| `m2_feedback.py` | Validated local analytics and descriptive cohort reports |
| `m2_hook.py`, `m2_subtitles.py` | Requested packaging; subtitles remain off by default |
| `m2_angles.py`, `m2_subject_pos.py` | Source framing evidence for the approved crop plan |

## Regression checks

Use a Python environment with NumPy and Pillow, plus FFmpeg/FFprobe for media
fixtures. No platform downloads or real ASR are required by the unit fixtures.

```bash
python3 -m unittest discover -s Tools/method-2/tests -v
```
