# Select links for the reader

SKILL.md's [Select and rank](../SKILL.md#select-and-rank) covers the basics: one
topic per bullet, the core ticket rather than its epic, the primary PR rather
than its backports, and no link quota. This file covers the harder calls.

## Which ticket is the core ticket

Read later comments before choosing. A newer request to disassociate a PR, an
explicit scope mismatch or a ticket split can make an early link wrong for this
update even though it was once valid. If no current core ticket is established,
keep the implementation link alone rather than substituting an umbrella, a
neighbouring task or a ticket that was only quoted in passing. An explicit
reference to a real ticket is still peripheral if that ticket is not the work
being described.

## Which change links belong

- Keep separate repositories or changes when each is a necessary piece of the
  work; one anchor link should not hide distinct contributions.
- Include a prerequisite only when its unresolved state explains the update's
  current blocker, not because another artifact mentions it.
- Prefer a replacement over a closed proposal that adds no current information.
- Credit review or investigation only where the linked artifact shows the user's
  substantive analysis or decision. A review request, mention or subscription is
  not review work, and older open work is context unless there was real activity
  this period.

## The test for each URL

Ask what the reader learns by opening it that the description and the other
links do not already show. If removing it leaves the reader equally able to
identify the work and its status, omit it. The digest's `related` lines show
what each citation lets you select; the capture keeps the full relation graph.

Citations in `analysis.json` establish what supports the prose, and `links`
establishes what the reader sees: it is fine to cite several backports and show
only the primary PR. Read the finished list as a meeting update: descriptions
stand on their own, related pieces have a clear hierarchy, and every link earns
the reader's attention.
