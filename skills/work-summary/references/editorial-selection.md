# Select links for the reader

Discovery and presentation answer different questions. Keep the accessible
native evidence broad enough to find missed work. In the short update, explain
progress and give the reader the few artifacts that identify that progress.
URL validity, relation provenance and weekly activity are necessary evidence
checks; they do not establish editorial relevance.

## Choose the work before its links

Use a supplied update or destination document to learn its audience, level of
detail and grouping. A manager 1:1 often needs outcomes, current work, blockers,
decisions and concrete review/unblocking contributions. Avoid making the reader
sort an inventory. Do not copy dated status, personal context or TODOs into a
new weekly update without supporting evidence or an explicit user request.

Choose a top-level workstream when its children make sense together. Use a short
child description for independently describable progress, such as planning and
CI implementation. Keep an update flat when splitting it would add ceremony.
Place each URL under the narrowest description it supports. A child does not
inherit unrelated links from its parent or siblings. Reuse a shared artifact
across topics only when it serves a distinct purpose in each.

## Decide which artifacts earn a place

- **Core Jira:** directly tracks the described fix, deliverable, investigation or
  acceptance criteria. Prefer the specific task over its epic, release umbrella,
  neighboring tasks, duplicate tracker or ticket quoted in historical context.
- **Implementation PR/MR:** contains the change the bullet describes. Retain
  separate repositories or changes when they are necessary pieces of that work.
  A prerequisite belongs only when its unresolved status explains the update's
  current blocker, not simply because another artifact mentions it.
- **Review/investigation artifact:** contains substantive personal analysis or
  the decision being reported. A review request or incidental mention is not
  enough to claim that work. A native actor/time on a mention, subscription or
  cross-reference establishes the event, not implementation or review progress.
  Older open work can be useful ongoing context without being new weekly work.
- **Backports and superseded changes:** normally select the primary current PR
  and describe maintained-branch coverage in prose. Include a distinct branch
  PR if its behavior, status or blocker matters. Prefer the replacement over a
  closed proposal that contributes no additional current information.
- **No useful link:** keep a supported update without URLs. Local-only work and
  concise status notes do not need a guessed counterpart or filler link.

There is no universal number of links per topic. One topic may need a Jira and
one PR; another may need several independent implementation changes and no
Jira. An explicit reference to a real ticket is still peripheral if that ticket
is not the work being described. Avoid choosing by link count, search order or
keyword overlap alone.

For each proposed URL, explain privately why it is central to this description.
If removing it leaves the reader equally able to identify the work and its
status, omit it. Keep the full candidate/provenance graph in the private audit.

## Express selection separately from evidence

Analysis citations establish what supports the prose. `links` establishes what
the reader sees. It is legitimate to cite several backports privately and show
only the primary PR. Use an explicit `links: []` to omit all candidate links.
URLs must come from the same node's captured evidence and validated reference
candidates; selecting a display URL never changes the source or verifies an
unread remote state. Source gaps remain explicit even when their links are hidden.

```json
{
  "name": "Verification",
  "description": "Improved the service's verification workflow.",
  "items": [],
  "links": [],
  "subtopics": [
    {
      "name": "CI",
      "description": "Added checks for the helper scripts.",
      "items": [
        {"repo": "org/repo", "number": 457, "url": "https://github.com/org/repo/pull/457"}
      ],
      "links": ["https://github.com/org/repo/pull/457"]
    }
  ]
}
```

Without explicit `links`, the conservative renderer shows direct cited Jira or
GitHub artifacts and leaves their surrounding references out. Agent analysis
should make the actual editorial choices. Native body/commit URLs and contextual
MRs remain available for deliberate selection, including in reduced-auth modes.

Read the finished list as a meeting update: the descriptions should stand on
their own, related pieces should have a clear hierarchy, and each selected link
should earn the reader's attention. Do not equate exhaustive source reconciliation
with a requirement to display every ticket or PR.
