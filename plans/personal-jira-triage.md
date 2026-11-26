# Plan: portable personal Jira triage

Implemented as `skills/personal-jira-triage`. The design, qualification criteria and dated run notes that used to follow are in git
history (`git log -- plans/personal-jira-triage.md`).

## Outcome and boundaries

One invocation should identify all accessible assigned Jira issues, relevant PRs and related tracking without making the user
maintain a repository list. It must distinguish inventory coverage from deep triage coverage and show failed or excluded sources.
An unchanged issue is a successful result; the skill must never manufacture a batch to appear productive.

Default operation stops at user review. Retain authorization already given for a specific later action, but neither discovery nor
approval of a draft authorizes additional issues, assignments, comments, closures, PR changes or release execution.
Existing generic `/jira`, career reporting and specialized CVE triage keep their contracts. No automatic reassignment, new-story
creation, backlog-wide closure, CVE scanning, release replay, subscription/watch changes or engineering implementation belongs in v1.
