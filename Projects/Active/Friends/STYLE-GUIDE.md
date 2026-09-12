# Friends clipping style guide

This guide applies only to source projects inside `Projects/Active/Friends/` It is the default unless the user explicitly overrides a rule for one source

## Project organization

- Create one project for every new source video directly under the Friends folder
- Name it `YYYY-MM-DD - Source Video Title` using the date the user requested the clip
- Never put a new source inside another source project's `runs` folder
- Put every finished video in that source project's `deliverables` folder

## Selection and clip count

- Find and render every distinct scene that genuinely clears the quality gates Quality over quantity
- Deliver at least three clips when the source contains three qualifying scenes
- Never pad the batch with weak moments filler or multiple boundary versions of the same scene
- If fewer than three scenes clear the gates stop and ask the user before weakening a gate
- Use these as rough planning expectations not quotas or hard limits
  - About 10 minutes of source usually suggests around 5 clips
  - About 20 minutes of source usually suggests at least 8 clips
  - About 30 minutes of source usually suggests around 12 clips
- Longer sources scale by actual scene density and quality Do not extrapolate a mandatory count

## Editorial rules

- Select funny moments or otherwise strong satisfying Friends scenes using Viral Moments and the shared rubric
- Every clip must be understandable and complete with setup progression and payoff
- Minimum duration is 60 seconds
- One to three minutes is normal Longer is allowed when the natural scene needs it There is no hard maximum
- Use one continuous source span per clip
- Do not add internal editorial hard cuts jump cuts reordering speed changes silence removal or filler removal
- Preserve the original pace pauses reactions source camera edits and horizontal framing
- Do not cut off a sentence reaction punchline or scene resolution
- Suppress overlapping or duplicate candidate versions
- If a genuine editorial ambiguity could materially change meaning boundaries or clip count ask the user for approval

## Video and audio

- Keep the original horizontal aspect ratio
- Use the highest resolution and audio quality actually available from the source Do not upscale merely to claim a higher resolution
- Follow the source frame cadence
- Deliver MP4 with H264 High Profile `yuv420p` and faststart
- Deliver AAC stereo at 48 kHz and 192 kbps
- Target about `-14 LUFS` with true peak at or below `-1 dBTP`
- Do not add music B roll hook titles effects transitions or metadata unless the user asks

## Subtitle content

- Burn in Simplified Chinese above English
- Chinese must be visibly larger than English
- Translate into natural conversational Simplified Chinese while preserving the joke intent names and speaker meaning
- Remove every visible punctuation character from both languages including commas periods apostrophes quotation marks hyphens and CJK punctuation
- Do not caption laughter music or silent reactions as dialogue

## Subtitle appearance

- Center aligned in the lower safe area at about `78 percent` of frame height
- Maximum text width about `72 percent` of frame width
- Chinese default fill white
- English default fill white
- Current spoken English word changes to yellow `#FFD640`
- Chinese remains static white
- Black stroke at full opacity with the approved lighter visual weight
- Weight 900
- Chinese maximum one line when possible English maximum two lines

Use these 4K reference measurements and scale them proportionally to the actual output height

- At `3840x2160` Chinese `121px` English `93px` stroke `9px`
- Chinese size ratio `0.056 x output height`
- English size ratio `0.043 x output height`
- Stroke ratio `0.00417 x output height` rounded to a practical whole pixel
- Example at `1280x720` Chinese `40px` English `31px` stroke `3px`

The active English highlight must follow final word timing within `80ms` Default text is white with a black stroke and only the word currently being spoken turns yellow

## Quality gates

- Validate meaning cold viewer orientation promise payoff boundaries and safety
- Verify the rendered output against the source transcript
- Verify continuous frame cadence correct source span audio video alignment loudness true peak and clean head and tail
- Visually inspect subtitle safe zone readability lighter stroke active word timing and absence of punctuation
- Route only passing files to `deliverables`
