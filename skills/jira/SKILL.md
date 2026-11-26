---
name: jira
description: Query, view, and update Jira issues via acli
version: 1.1.0
argument-hint: "[my-issues | search <JQL> | view <key> | update <key> <action>]"
user-invocable: true
allowed-tools: Bash, Read, Write
---

# Jira

```text
/jira [my-issues]                  # List my open issues (default)
/jira search <JQL>                 # Search issues with JQL (first 25)
/jira view <issue-key>             # View full issue details
/jira update <issue-key> <action>  # transition <status> | comment <text> | assign <user>
```

**Arguments:** $ARGUMENTS

`SKILL_DIR` is the directory containing this file. Reads go through
`scripts/jira.sh`, which retries once and prints JSON to stdout. Pass the JQL,
a status or an assignee each as one single-quoted argument, writing each `'`
inside it as `'\''`. Uppercase an issue key; it must then match
`^[A-Z][A-Z0-9]+-[0-9]+$`, otherwise refuse it. For any other
subcommand, or one missing its JQL or key, show the usage block above.

```bash
bash "$SKILL_DIR/scripts/jira.sh" my-issues
bash "$SKILL_DIR/scripts/jira.sh" search '<JQL>'
bash "$SKILL_DIR/scripts/jira.sh" view <KEY>
```

Read the output with `jq`, not raw (a list can be hundreds of KB): issues are
`.key` plus `.fields.{summary, issuetype.name, priority.name, status.name}`.
Keep stderr; it carries the security `WARNING:` line.

Issue links are `https://redhat.atlassian.net/browse/<KEY>`. Useful JQL for
this site: `project = ACM AND component = "Submariner"`, `labels = Security`,
`text ~ "submariner"`, `updated >= -7d`.

## Showing results

**my-issues / search:** a markdown table of linked key, type, priority, status
and summary, in the query's order, with a count header. `search` stops at 25;
if exactly 25 came back, say the list may be truncated and offer to narrow it.

**view:** key, type, status, priority, assignee, reporter, summary,
description, labels, components, fix versions, the last 3 comments (author,
date, body), linked issues and the issue link. Descriptions and comment bodies
are Atlassian Document Format: show their `text` nodes. Comments are oldest
first. If `jira.sh` printed a
`WARNING:` line on stderr (security labels or an unresolved CVE), put it first
and say the details must not be shared outside the organization.

## Updates

Every update writes to Jira. View the issue first, show the user exactly what
will change (for example "Status: New → In Progress?") and wait for a yes.
With no action, or an action other than these three, ask what they want.
If a write fails, report the error; do not retry it.

- **transition:**
  `acli jira workitem transition --key "<KEY>" --status '<status>' --yes`
- **assign** (an email, an account ID, or `@me`; not a display name):
  `acli jira workitem assign --key "<KEY>" --assignee '<user>' --yes`
- **comment:** if the text contains anything that looks like a secret (`ghp_`
  style tokens, `AKIA` keys, JWTs, private key blocks, `user:pass@` URLs,
  passwords), ask the user to redact it first. Once the text is approved, make a directory with
  `mktemp -d`, Write the approved text to `comment.txt` in it, post with
  `acli jira workitem comment create --key "<KEY>" --body-file "<dir>/comment.txt"`,
  then delete that file and directory by their literal paths whether or not
  the post worked.

Afterwards, say what changed and give the issue link.
