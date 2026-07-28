# Career Advocate Analysis Guide

## Setup

Create `private/` directory under `skills/career-advocate/`:

```bash
mkdir -p skills/career-advocate/private/history
```

### Curate Expectations from CSV

Download the job progression spreadsheet as CSV. Read the CSV and
create two markdown files:

- `private/current-level.md` — Copy the responsibilities and skills
  column for your current level. Include the Job Description Summary.
- `private/target-level.md` — Same for the level you're targeting.

Structure each file with clear headings:

```markdown
# Senior Software Engineer (Level 4)

## Job Description Summary
The Senior Software Engineer is a technical leader who...

## Responsibilities
1. Technical Impact: Leads the design and development...
2. Quality & Reliability: Owns the quality of their code...
[all 8 responsibilities]

## Skills
1. Technical Acumen: A go-to person for technical...
2. Quality Management: Drives quality within the team...
[all 10 skills]
```

### Career Context

Create `private/career-context.md` with these sections:

```markdown
# Career Context

## Goals
### Short-term (this quarter)
### Medium-term (this year)
### Long-term (2+ years)

## Feedback Quotes
- [Date] [Source]: "Quote"

## Slack Evidence
- [Date] [#channel] Summary of notable message/thread

## Supplemental Evidence
- Conference talks, blog posts, training, design docs

## Mentoring Notes
- Pair programming, onboarding help, teaching moments
```

### 1:1 Notes

Create `private/one-on-ones.md`:

```markdown
# 1:1 Notes with Manager

## YYYY-MM-DD
- Topics discussed
- Manager feedback quotes
- Action items
```

### Data Transmission Notice

Running this skill sends contribution data and private files to
Anthropic's API for analysis. Do not include salary figures or
other highly sensitive data in files the skill reads. Keep
compensation data in a separate file not referenced by the skill.

## Evidence Framework

### 2D Evidence Matrix

Rate evidence on two dimensions — strength and level alignment:

| | Strong | Moderate | Weak | Gap |
| --- | --- | --- | --- | --- |
| **Exceeds target** | Advocate | Advocate | Better artifacts | — |
| **Meets target** | Ready | Strengthen | Needs work | — |
| **Meets current** | Insufficient | Insufficient | Insufficient | Risk |
| **Below current** | — | — | Concern | Blocker |

**Key insight**: "Strong evidence at current level" is worse than
"Moderate evidence at target level" for a promotion case. Volume of
current-level work does not demonstrate readiness for the next level.

### Evidence Types

- **Strong**: direct artifact you produced (merged PR, shipped
  feature, authored design doc, resolved CVE, created automation)
- **Moderate**: indirect contribution (code review, design input,
  process improvement, mentoring through reviews)
- **Weak**: participation (attended meeting, joined discussion,
  was on a team that delivered)

### Situation-Action-Result Format

For top contributions, write narratives as:

- **Situation**: What was the problem or opportunity?
- **Action**: What did you specifically do? (cite PRs, commits)
- **Result**: What changed? What was the measurable impact?

## Output Templates

### Promote Mode

Start with **Manager Briefing** (2-minute read):

```markdown
## Manager Briefing

**Candidate:** [Name]
**Current Level:** [X] | **Target Level:** [Y]
**Period:** [since] to [until]

### Recommendation
[One sentence: why promote this person now.]

### Top 3 Evidence Points
1. [Strongest example] — maps to [Category]
2. [Second strongest] — maps to [Category]
3. [Third strongest] — maps to [Category]

### Calibration Talking Points
- "Already operating at [Target Level] in N/18 categories"
- "[Specific accomplishment] demonstrates [target expectation]"
- "[Peer/manager quote]"

### Known Gaps and Mitigations
- [Gap category]: [Why it's acceptable or plan to address]

### Quick Stats
- [X] PRs merged across [Y] repositories
- [X] code reviews provided
- [X] Jira issues resolved
- [X] user branch sequences (showing development depth)
- [+X / -Y] lines of code across [N] repos
- File types: [Go, YAML, shell, markdown, ...]
- Jira components: [list]
- [N] PRs commented on (beyond formal reviews)
```

Then: full evidence organized by lens (technical delivery,
collaboration, leadership, innovation). Each section includes the
lens rating, narrative, and gap analysis.

End with an honest overall assessment: "Strong candidate with
N/18 categories at or above target level" or "Developing in N
categories — recommend with development plan."

### Assess Mode

```markdown
## Self-Assessment: [Period]

### Summary of Accomplishments
[Themed overview: what was delivered, what impact]

### Contributions by Category
[Group by lens, cite evidence per category]

### Growth Areas
[Where evidence is thin, what to develop]

### Goals for Next Period
[Based on gaps identified]
```

### Meeting Mode

```markdown
## 1:1 Prep: [Date]

### Recent Wins (since last meeting)
[Top 3-5 accomplishments with links]

### Current Work
[Open PRs, active Jira issues, in-progress features]

### Blockers / Asks
[What you need from your manager]

### Career Discussion
[Based on career-context.md goals and recent progress]
```

## Synthesis Methodology

When merging lens reports into one document:

1. Read all lens reports — note ratings and evidence counts
2. Count categories at each level (exceeds/meets/developing/gap)
3. Identify the top 3 strongest evidence points across all lenses
4. Identify the most significant gaps
5. Write the Manager Briefing using these synthesized findings
6. Include each lens's full narrative in the body
7. Weight open source/upstream contributions heavily (Red Hat
   values community engagement)
8. Be honest — state gaps clearly, don't oversell. A document that
   acknowledges gaps is more credible than one that ignores them
9. Read private/contribution-log.md for what changed since the
   last collection run
