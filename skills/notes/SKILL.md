---
name: notes
description: Take and manage persistent markdown notes organized by topic
version: 1.1.0
argument-hint: "<topic> [note instruction or list|view|search]"
user-invocable: true
allowed-tools: Bash, Read, Write, Grep
---

# Session Notes

```text
/notes <topic> <what to note>   # Save a note about the current discussion
/notes <topic> list             # List notes for a topic
/notes <topic> view [N]         # View a note (N from the list)
/notes <topic> search <query>   # Search within a topic
/notes [list]                   # List all topics
```

**Arguments:** $ARGUMENTS

Notes live in `~/notes-ai/<topic>/<YYYY-MM-DD-HHMMSS>.md`, one directory per
topic. A topic is one word of letters, digits, `.`, `_` or `-` that does not
start with `.` and is not `list`; check it before any action and refuse
anything else.

## Reading

Read with plain tools; never modify existing notes here.

- **Topics:** the subdirectories of `~/notes-ai` (skip names starting with `.`),
  each with its count of `*.md` files (use `find`; zsh errors on empty globs).
  If there are none, say there are no notes yet.
- **List:** the topic's `*.md` files sorted by name, numbered from 1, each with
  its first level-1 heading as the title.
- **View:** list as above, then show note N. Without N, show the list and ask.
- **Search:** use the Grep tool on `~/notes-ai/<topic>` with glob `*.md`,
  case-insensitive, line numbers on, and the query regex-escaped so it matches
  literally.

## Saving a note

Look back through the conversation and write a distilled summary of what was
learned or decided, not a transcript. Save it with the Write tool:

```markdown
---
date: <ISO 8601 timestamp>
topic: <topic>
tags: [<tags inferred from content>]
---

# <Descriptive Title>

<Concise, structured content>
```

Then commit it locally. Run `git init` first if `~/notes-ai/.git` is missing:

```bash
git -C "$HOME/notes-ai" add "<topic>/<filename>.md"
git -C "$HOME/notes-ai" commit -s -m "Add note: <descriptive title>"
```

Report the file path and commit hash. If the commit fails (for example,
signing needs a key the session cannot use), report the saved path and the git
error; do not retry with signing disabled. **Do not push**; the user pushes
when ready.
