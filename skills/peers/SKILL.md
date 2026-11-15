---
name: peers
description: Find and message live Claude Code sessions in any profile (CLAUDE_CONFIG_DIR), including ones ListAgents does not show, such as ~/.claude-per2 sessions. Use when asked to message a session by name and ListAgents does not list it.
version: 0.1.0
argument-hint: "[session name]"
user-invocable: true
allowed-tools: Bash(*/skills/peers/scripts/peers.py), SendMessage
---

# Message sessions in other profiles

`ListAgents` lists only sessions that share this session's
`CLAUDE_CONFIG_DIR`. Sessions in another profile, such as `~/.claude-per2`,
are missing from it, but `SendMessage` still reaches them: every session
listens on a messaging socket, and `to` accepts `uds:<socket path>`.

**Arguments:** $ARGUMENTS

`SKILL_DIR` is the directory containing this file.

1. Run `<SKILL_DIR>/scripts/peers.py` with the absolute path written out
   and no arguments. It lists live sessions in `~/.claude`, `~/.claude-*`
   and `$CLAUDE_CONFIG_DIR`, one per line: name, profile, status, PID,
   address, cwd.
   Your own session is listed too. Pick the row by name, using cwd to tell
   apart sessions with the same name.
2. Send with `SendMessage`, setting `to` to that row's `uds:...` address,
   exactly as printed. The recipient sees your address in the message's
   `from=` attribute and can reply to it the same way.

A successful send means the message is in the recipient's inbox, not that it
was read. A session in a different permission mode holds it for its user's
approval. To prove it was read, ask for a reply.
