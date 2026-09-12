# Transcription Contract

This is the shared transcription contract for new projects.

## Engine and model

- The repository tools call `Tools/method-2/runtime.py` for a portable
  transcription command and model choice.
- The default adapter is `scripts/transcribe_compat.py`, backed by
  `faster-whisper`. A local MLX-Whisper binary may be selected explicitly with
  `FRIENDS_WHISPER_BIN` when the computer supports it.
- The default model is `large-v3`, configurable with `FRIENDS_WHISPER_MODEL`.
  Use `large-v3-turbo` when the selected backend supports it and the run records
  that choice.
- Never rely on a binary's defaults. Every invocation must pass the model and
  output format explicitly. Use VTT for analysis and rendered-output review.
  The indexer may request temporary segment JSON to preserve source offsets and
  ASR confidence metadata, then write one canonical VTT; this is not word timing
  or a second analysis transcript. Selected-span word timing explicitly uses JSON.
- Run a short test before a multi-hour transcription and verify language,
  timestamps, and output schema.

Canonical analysis invocation:

```bash
python scripts/transcribe_compat.py <audio> \
  --model large-v3 \
  --output-format vtt \
  --output-dir <transcript-dir>
```

Set `--language` explicitly when the project language is known. Omit it for a
deliberately multilingual source when auto-detection is preferred.

## Outputs and timing

- Use VTT as the analysis and review transcript.
- Use word-level JSON for shortlisted Method 2 spans plus handles before the
  planner locks exact boundaries, or for requested final captions/timing. Do not
  run full-source word timing during discovery by default.
- Method 2 selected timing runs with `m2_index.py --project <project> --timing-plan <shortlist.json>`; eligible candidate statuses are `shortlisted`,
  `approved_for_timing`, and `approved_for_execution`. Return the resulting
  word file to the planner before executing clips.
- Overlapping timing windows supply decoder context; assign each word to one
  window core and verify source offsets and ordering. Never silently overwrite
  existing full-source timing with selected timing.
- Do not create duplicate TXT, SRT, or TSV transcripts by default.
- If long audio must be chunked, use 3600-second chunks by default and preserve
  each chunk's original long-form offset when merging.
- `Tools/merge-parakeet-chunks.mjs` is only for inputs matching its Parakeet
  `sentences[].tokens[]` schema. Do not use it blindly on MLX Whisper JSON.

## Validation

Before analysis, verify that the merged VTT starts at the source beginning,
ends near the source duration, has monotonic timestamps, and contains no large
unexplained gaps caused by chunk offsets. Record the engine, model, chunk size,
and output paths in `project.yaml`.

## Coverage and reuse

- Retain suspected repeated or low-content speech in the discovery VTT. Record
  suspicious intervals and unexplained gaps in `index/transcript-coverage.json`;
  missing text never establishes silence. Inspect source audio for candidates
  touching uncertain coverage before the planner approves their boundaries.
- Reuse discovery VTT only when its source/model/language/chunk record matches.
  An older VTT without that record requires explicit validation/migration; do
  not silently relabel it or overwrite it. Selected timing caches include source,
  model, language, intervals, and decoder-window settings.
- A project language is explicit. Pass `--language auto` for deliberately
  multilingual sources instead of forcing English.
