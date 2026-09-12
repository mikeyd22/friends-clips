# Viral Moment Analysis Rubric

Use this rubric to build an evidence profile, not a universal virality formula.

## 1. Eligibility gates

Mark each Pass, Uncertain, or Fail.

| Gate | Pass condition |
|---|---|
| Meaning integrity | The edit preserves the speaker's claim, intent, and material qualifiers. |
| Cold-viewer orientation | The viewer can identify the subject and stakes without the full episode. |
| Promise integrity | The opening accurately signals what the clip will deliver. |
| Payoff completion | The clip contains a reveal, answer, consequence, insight, or emotional resolution. |
| Boundary coherence | A clean entry and exit preserve a complete micro-arc. |
| Evidence safety | Material factual or sensitive claims can be presented responsibly. |

A Meaning Integrity or Promise Integrity failure disqualifies the candidate. Other failures require a boundary fix or explicit warning before selection.

## 2. Pathway profile

Score 0–4. Attach at least one reason code and transcript observation to every score above 2.

### Stop potential

- **0:** No clear subject, relevance, tension, or novelty.
- **1:** Understandable only after substantial setup.
- **2:** Clear but ordinary opening.
- **3:** Immediate relevance plus a specific unresolved question, contrast, or consequence.
- **4:** Exceptional clarity and a strong bounded gap, surprise, stakes, or audience-specific relevance.

Reason codes:

- `S1` Immediate subject and stakes
- `S2` Bounded information gap
- `S3` Surprise or expectation violation
- `S4` Concrete specificity
- `S5` Strong audience relevance

### Hold potential

- **0:** Flat, repetitive, or already resolved.
- **1:** Long runway with weak progression.
- **2:** Coherent but predictable.
- **3:** Each beat adds evidence, stakes, causality, or emotional movement toward a payoff.
- **4:** Tight escalating progression with no natural exit before a satisfying payoff.

Reason codes:

- `H1` Escalating stakes or causality
- `H2` Reveal chain
- `H3` Emotional movement
- `H4` Specific story progression
- `H5` Credible demonstration or proof
- `H6` Payoff arrives before interest decays

### Send potential

- **0:** No credible reason to transmit it.
- **1:** Generic interest with no clear recipient or social use.
- **2:** Some value to a defined audience.
- **3:** Strong practical, identity, relational, emotional, or conversational value.
- **4:** Multiple compatible send motives with a clear recipient and low social cost.

Reason codes:

- `X1` Practical utility: “This will help you.”
- `X2` Positive self-presentation or identity: “This expresses something about us/me.”
- `X3` Social connection: “This made me think of you.”
- `X4` High-impact emotion: awe, joy, humor, anger, anxiety, or another activating response
- `X5` Worthwhile conversation or moderate controversy
- `X6` Insider novelty or surprising information

Always complete:

> Send to **[recipient]** because **[reason]**.

A score of 3 or higher requires that sentence to name a **specific recipient**
and carry at least one `X` code. A demographic is not a recipient: "young men
who watch this creator" does not qualify, "my roommate who has been to
Shenzhen" does. Score the completed sentence rather than a general impression
of shareability. A number can be nudged to clear a bar; a named recipient
either exists or it does not.

Motive availability shifts with source format, not with the account. `X1`
practical utility is the one motive that leans genuinely toward interview and
podcast sources. The rest are reachable from any format: reaction and travel
footage carry `X2`, `X4`, `X5`, and `X6` readily, and reach `X3` whenever a
moment maps onto a specific relationship the viewer already has. Do not retire
any code as permanently unavailable.

On a share-button platform the clip travels intact, so transmission does not
depend on the viewer being able to summarise it. The send decision is an
impulse formed while watching, when a specific person comes to mind. Score the
recipient, not the describability.

Do not count outrage, anxiety, or moral language as automatically beneficial. Label accuracy, polarization, and reputation risk.

### Audience fit

- **0:** Audience is unknown or the topic conflicts with it.
- **1:** Weak inferred relevance.
- **2:** General relevance.
- **3:** Strong platform, niche, and audience match.
- **4:** Strong Local evidence from comparable posts or explicit user intent.

### Boundary confidence

- **0:** No reliable alignment or clean extraction.
- **1:** Approximate alignment with essential missing context.
- **2:** Usable with a boundary compromise.
- **3:** Clean coherent source span.
- **4:** Exact VTT alignment with a minimal entry and peak exit.

## 3. Evidence labels

Use these beside the reason codes:

- `D` Direct research or platform evidence
- `A` Adjacent-domain evidence
- `L` Local comparable analytics
- `H` Editorial heuristic

Examples: `S2-A`, `X1-D`, `H4-H`, `X3-D/L`.

Do not inflate confidence by citing the same observation under many dimensions. State when several scores depend on one feature.

## 4. Risks and penalties

Label None, Low, Medium, or High:

- context removal changes meaning;
- opening overpromises the payoff;
- factual claim requires verification;
- medical, legal, financial, personal, or reputational sensitivity;
- polarizing or moral-emotional language narrows appeal to an in-group;
- controversy creates discomfort or social cost;
- clip depends on unavailable visuals, delivery, captions, or comments;
- moment duplicates a stronger candidate;
- distribution or platform eligibility concern.

Do not subtract numerical points. Explain how each risk changes the recommendation.

## 5. Potential classification

- **High potential:** All gates pass; Stop and Hold are at least 3; Send is at least 3 for transmission objectives; risks are manageable.
- **Medium potential:** Gates pass, but one pathway is weak or dependent on packaging, audience, or execution.
- **Low potential:** A gate is uncertain, the payoff is weak, or the proposed mechanism is mainly heuristic.

For watch-time objectives, a high Stop/Hold profile can rank well even with modest Send. State that the objective changes the ranking.

## 6. Shortlist record

```markdown
### <rank>. <working label> — <high / medium / low> potential
- Timecode: <HH:MM:SS.mmm–HH:MM:SS.mmm> (<duration>)
- Opens: “<first retained line>”
- Pays off: “<payoff line>”
- Micro-arc: <premise → progression → payoff>
- Gates: meaning <P/U/F>; orientation <P/U/F>; promise <P/U/F>; payoff <P/U/F>; boundary <P/U/F>; safety <P/U/F>
- Profile: Stop <0–4>; Hold <0–4>; Send <0–4>; Fit <0–4>; Boundary <0–4>
- Evidence: <reason codes with D/A/L/H labels>
- Send reason: Send to <recipient> because <reason>
- Edit note: <boundary or packaging dependency>
- Risks: <context / accuracy / safety / reputation>
- Confidence: <high / medium / low>; largest unknown: <unknown>
```

Quote only enough transcript to identify the opening and payoff. Preserve exact VTT timecodes.
