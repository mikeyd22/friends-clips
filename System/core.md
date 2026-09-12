# Core Clipping Rules

These rules apply to every project unless its brief says otherwise.

## Method selection

- Choose exactly one editing method: `straight-cut`, `full-automated-ai-clipping`, or `method-2.5`.
- Run `System/viral-moments/SKILL.md` and its single shared `System/viral-moments/analysis-rubric.md` for every method.
- Use the rubric as the common analysis record: eligibility gates, Stop, Hold, Send, Audience Fit, Boundary Confidence, evidence labels, risks, and confidence.
- Do not duplicate the rubric into separate method-specific scoring systems.
- When the user supplies an exact range, use the analysis as a non-mutating validation pass. Do not silently change the user's boundaries.
- When the user asks for discovery, use Viral Moments Find mode and return candidates with exact timecodes, opening line, payoff, micro-arc, analysis profile, risks, and confidence.
- For hook discovery, opening selection, or hook diagnosis, also load
  `System/hooks/mentor-hook-mastery.md`. Treat the mentor framework as
  editorial heuristic evidence unless a claim has independent Direct or Local
  support.

## Straight Cut — Method 1

- Cut the requested span from the supplied long-form source into a short-form export.
- Preserve the source format, framing, resolution, frame rate, audio, and natural continuity unless the request says otherwise.
- Do not add captions, music, B-roll, effects, transitions, hook text, metadata, or silence removal by default.
- Return the export after technical QA.

## Full Automated AI Clipping — Method 2

- This method is fully approved and operational. Use `System/methods/full-automated-ai-clipping.yaml` plus explicit project overrides.
- The planner's completed method-compliant plan authorizes execution; there is no routine human approval gate.
- Use full-source VTT, energy, shots, and vision for discovery. Generate word timing for the planner's shortlisted spans plus handles, then return to the planner to lock boundaries before range execution.
- Produce drafts first. Delivery requires current alignment, technical, audio-content, visual-continuity, and semantic review evidence for the exact final render and edit plan.
- Uncertain or failed checks hold the affected clip for agent review. Only the planner resolves semantic or boundary ambiguity; no missing check may be treated as a pass.
- Hook titles follow the approved method and project settings. Subtitles, music, B-roll, and other optional packaging remain off unless explicitly enabled.

## Method 2.5 — Premiere Rough Cut

- Use Viral Moments and the rubric to identify or validate approved moments before handoff.
- Preserve the approved candidate name in the XML clip name, sequence marker, and future caption/title source.
- Default to storage-efficient acquisition: download the complete audio, transcribe and select first, then download only approved high-quality video ranges.
- Give every selected range 30 seconds of editing handles on both sides by default. Increase the handles when continuity or likely human editing requires it.
- Use a temporary low-resolution full-video proxy only when needed for visual discovery or verification, then remove it after QA.
- Download the complete high-quality video master only when the user explicitly requests it or unrestricted source access is required by the brief.
- Assemble the approved ranges into a Premiere-importable XML sequence with linked, separate, editable cuts. Preserve original long-form timecodes in the plan and marker comments.
- Approved ranges may be losslessly consolidated into one selected-source video reel and matching audio reel when that makes Premiere relinking simpler; do not re-encode merely to consolidate them.
- Never delete a previously downloaded full master merely because the default acquisition policy changed.
- Do not add captions, effects, reframing, silence removal, or rendered media unless requested.
- The user owns the final human edit in Premiere.

## Shared guardrails

- Preserve the speaker's meaning and enough context to understand the clip.
- Keep hooks truthful. Never invent a promise the selected clip does not deliver.
- Do not manufacture outrage, certainty, conflict, or false novelty through editing.
- Do not treat transcript analysis, a preset, or an automated score as a substitute for human judgment.
- Follow `System/transcription.md` for engine, model, chunking, VTT, and word-timing decisions.
- Follow `System/qa/clipping-qa.md` for method-appropriate technical and consistency checks.
- Optional elements remain off unless the user requests them.
- Automate reliable technical work such as transcript search, timecode extraction, encoding, word timing, XML generation, and delivery checks when requested.
- Run technical QA on rendered deliverables. Run visual QA only for elements present in the requested edit.
- Consider permission, copyright, privacy, claims, and disclosure before publishing.

## Codex speed policy

- Standard/default speed is the default for every clipping project, workflow, and editing method.
- Fast mode is disabled by default. Do not set `service_tier` to `fast` or enable `[features].fast_mode` for clipping work unless the user explicitly requests Fast mode for that task.
- Project and method speed settings should state `mode: standard` and `fast_mode: false` when they declare runtime defaults.

## Audience calibration

- Separate universal integrity checks from account preferences. The active project may name `workflow.audience_profile`; Method 2 otherwise uses `System/audiences/legacy-short-form.json` to preserve the current house preferences.
- The profile retains the current <=33s preferred / 35s maximum duration and one-in-three published Send target as operating preferences supported by low-confidence local evidence, not universal performance laws.
- Keep Stop, Hold, and Send separate. A strong Stop/Hold clip is not disqualified merely for weak Send; never pad a batch to meet a target.
- Record predicted profiles before publication. Link supplied analytics to the exact published render, source type, platform, and reporting interval using `Tools/method-2/m2_feedback.py`.
- Compare retention, shares per view, and other metrics within comparable cohorts and fixed intervals. Do not infer causality from views, update a preference automatically, or invent missing metrics.
- Revisit preferences with the user when the profile's review threshold is reached. The threshold is a review prompt, not proof of statistical significance.

## Vertical reframing quality

- Never use stepwise face tracking that visibly snaps, jitters, or switches subjects inside a continuous source shot.
- Lock the crop within B-roll and multi-person shots. Change crop position at genuine source edits, or use smooth continuous motion when following one clearly identified subject.
- Reject vertical exports with stutter or glitched cuts: validate crop continuity separately from frame cadence, and visually review multiple frames from every reframed clip before delivery.

- Visual QA must inspect moving excerpts at action payoffs and splices as well as stills. Report actual source resolution and crop magnification; 1080x1920 export dimensions alone do not establish image detail.
