# Friends Clips

Portable Friends-only clipping workflow for Codex Cloud or local Codex.

The repository keeps instructions, tools, style rules, and QA policy. It does
not keep downloaded episodes or rendered videos.

## Use from the ChatGPT website

1. Open Codex Cloud and connect the GitHub repository.
2. Create an environment for this repository.
3. Use `scripts/setup.sh` as the setup script, or install the dependencies from
   `requirements.txt`.
4. Enable the limited or unrestricted internet access needed to retrieve the
   user-supplied source URL.
5. Ask Codex to use the Friends workflow and include the source link plus any
   style overrides.

Codex reads `AGENTS.md` before each task. The required scout, planner, and
executor roles are configured under `.codex/agents/`.

## Request template

Copy `Templates/friends-request.md` into the prompt or use it as a per-run
brief. The user's latest explicit style request wins for that run.

## Local use

Run `scripts/setup.sh`, open this repository in Codex, and give Codex the same
source-link prompt. Local runs may be more reliable for long media jobs because
they have persistent disk and local media tools.

## Important limitations

The source URL must be accessible from the execution environment. Age gates,
HTTP 403 responses, login requirements, or unavailable source audio can hold a
run. The workflow keeps the highest playable native source and does not upscale
or pretend that missing QA passed.
