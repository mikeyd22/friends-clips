---
name: viral-moments
description: Find, rank, and explain high-potential short-form clip moments in long-form transcripts while separating attention, retention, transmission, and distribution.
---

# Viral Moments

This is the shared, agent-neutral operating contract for clip analysis. Treat virality as an outcome produced by content, audience, packaging, distribution, and social feedback—not as an intrinsic property that can be read from a transcript.

## Required reading

Before selecting or evaluating clips, read:

- `System/viral-moments/references/research-foundations.md`
- `System/viral-moments/analysis-rubric.md`

Read local pattern examples or analytics only when the project explicitly supplies them for calibration.

## Analysis modes

- **Find:** Generate and rank moments from a new long-form transcript.
- **Explain:** Diagnose how a selected or published clip may earn attention, retention, or sharing.
- **Compare:** Evaluate proposed moments or boundaries.
- **Calibrate:** Use real post analytics or controlled variants to update local audience priors.
- **Research:** Update research foundations only when the user explicitly requests new research.

## Four separate pathways

1. **Stop:** Immediate relevance, clarity, tension, or surprise that prevents an early skip.
2. **Hold:** A bounded information gap, progression, emotional movement, or causal chain that leads to a payoff.
3. **Send:** Practical, identity, relational, emotional, or conversational value that gives someone a reason to transmit the clip.
4. **Distribution:** Account history, audience fit, timing, eligibility, recommendation exposure, and social feedback. Usually unknown from the transcript.

Never collapse these into one universal “virality” score.

## Find workflow

1. Read the active `project.yaml`, `System/core.md`, the selected editing method, and the relevant VTT fully enough to understand speakers, topics, dependencies, and nearby context.
2. Identify candidate moments from complete thoughts, not keyword hits alone. Look for surprise, utility, demonstrations, emotional turns, concrete stories, bounded questions, identity statements, and constructive conversation.
3. Run a separate Send sweep over the same source. Ask only: what here would someone forward to a specific person, and who is that person? Send-shaped moments are statement units — a claim, comparison, number, or verdict — not the narrative arcs the first pass looks for, so a search tuned for Stop and Hold will miss them. Add what this sweep finds to the candidate pool before applying gates.
4. Apply the eligibility gates in `analysis-rubric.md`. A Meaning Integrity or Promise Integrity failure disqualifies a candidate.
5. Score Stop, Hold, Send, Audience Fit, and Boundary Confidence separately from 0–4. For every score above 2, attach a reason code and transcript observation.
6. Complete `Send to [recipient] because [reason]` for every candidate. If no credible reason exists, keep Send weak.
7. Enter at the earliest truthful line that creates relevance or a bounded gap. Exit after the strongest fulfilled payoff, consequence, or emotional release. For Send-qualifying candidates, prefer an exit that leaves a conversational residue — a question the clip genuinely raises and does not settle. The residue follows the payoff; it never replaces it. Payoff completion remains a gate, and an exit that withholds the answer to manufacture discussion fails Promise integrity. Subject to method boundaries: straight-cut exits stay where the user set them.
8. Return a shortlist with exact source timecodes, opening and payoff lines, micro-arc, profile, evidence labels, risks, confidence, and largest unknown.

## Evidence labels

- `D` Direct research or platform evidence.
- `A` Adjacent-domain evidence; plausible transfer, not proof for organic short-form.
- `L` Local comparable analytics or controlled tests.
- `H` Editorial heuristic.

Do not present adjacent or heuristic claims as universal platform rules. Do not promise reach.

## Guardrails

- Preserve meaning and material qualifiers.
- Keep hooks truthful and include the payoff.
- Do not manufacture outrage, certainty, conflict, or false novelty.
- Flag accuracy, privacy, safety, copyright, reputational, and missing-visual risks.
- Do not add captions, music, B-roll, effects, metadata, or renders unless requested.
- Treat the analysis as evidence and decision support, not a guarantee of performance.
