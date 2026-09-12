# Friends Clips repository instructions

This repository is the portable Friends-only clipping workflow. It is designed
to run in Codex Cloud from the ChatGPT website or locally through Codex on a
trusted computer.

## Non-negotiable workflow

- Use exactly one method: `full-automated-ai-clipping`.
- Read `Projects/Active/Friends/project.yaml`, then
  `Projects/Active/Friends/STYLE-GUIDE.md`, `System/core.md`, the enabled
  method, the Viral Moments skill, and the shared analysis rubric before work.
- Run the stages sequentially: `clip_scout` -> `clip_planner` ->
  `clip_executor`.
- The scout reads the VTT and evidence without selecting final boundaries.
- The planner makes the editorial decision and locks the approved plan.
- The executor performs bounded acquisition, rendering, and technical QA only
  from the approved plan.
- Missing, stale, uncertain, or failed evidence holds the affected clip. Never
  call a held clip delivered.
- Run rendered-output transcription, semantic review, visual continuity review,
  and technical QA on the exact final bytes before delivery.
- Only passing final MP4s belong in a source project's `deliverables/` folder.

## User customization

The user's latest explicit request overrides repository defaults for that run.
The user may specify the source URL, date, number or type of clips, horizontal
or vertical output, subtitle language and order, typography, highlight color,
translation style, caption punctuation, hook/title behavior, and whether music,
B-roll, effects, or transitions are allowed. Record resolved overrides in the
project brief or plan before rendering.

Do not add packaging that the user did not request. Do not add an opening hook
or topic title inside the video by default. Keep proposed title or hook copy in
the sidecar when requested.

## Friends defaults

- One dated project per source video directly under `Projects/Active/Friends/`.
- Preserve complete continuous scenes, natural pacing, source framing, and
  source cadence unless the user explicitly asks for a different treatment.
- Quality over quantity: never pad a batch with weak, duplicate, or incomplete
  moments.
- Friends subtitles are enabled by default according to the style guide:
  natural Simplified Chinese plus English, with the style guide's ordering,
  punctuation, sizing, and active-word rules. The user may explicitly turn
  subtitles off or change the language, order, or typography for a run.
- Preserve the highest actually playable native source quality. Never upscale
  to claim a higher quality.
- Respect source permission, copyright, platform rules, and safety context.

## Agent routing

Preferred routing is the same as the original workflow: scout and executor use
`gpt-5.6-luna` at max reasoning, and planner uses `gpt-5.6-sol` at high
reasoning. If a friend's account does not expose one of those exact models,
use the closest available Codex model with the same role and reasoning level,
record the actual model in the run notes, and preserve the three-stage workflow.
Fast mode is off by default.

## Source and output handling

- Use only the source URL supplied by the user unless they authorize another
  source.
- Do not commit downloaded source media, rendered MP4s, cookies, credentials,
  local model caches, or private account data to GitHub.
- If a source is age-gated, unavailable, or blocked by the execution
  environment, stop and report the exact prerequisite instead of substituting
  unverified media.
- Keep all paths relative to the repository or the active project. Never add a
  machine-specific absolute path.
