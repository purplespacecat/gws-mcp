# gws-mcp

A small, self-contained **MCP server for Google Tasks, Calendar, and Drive** —
read-write, with one hard rule: **every write requires explicit human
approval** before it executes.

> **Status: pre-alpha.** Design is settled; implementation is in progress.
> Nothing here is usable yet.

## Why another Google MCP?

There is no official Google MCP server for consumer Workspace data, and the
community options are broad (10+ services) and mostly unmaintained. This one
is deliberately narrow:

- **Three services only** — Tasks, Calendar, Drive. Smaller code, smaller
  audit surface.
- **Local stdio server** — spawned by your MCP client (Claude Code, Gemini
  CLI, …). Nothing hosted, nothing listening on a network. The server only
  ever sees tool calls, never your prompts.
- **BYO OAuth** — you create your own Google Cloud OAuth client and grant
  scopes to *yourself*. No shared credentials anywhere.
- **Tokens in the OS keyring** (Secret Service / KDE Wallet / macOS Keychain)
  — never plaintext on disk.

## The write-approval invariant

Read tools (`list_*`, `get_*`, `search_*`, `read_*`) run freely. Mutating
tools (`create_*`, `update_*`, `complete_*`, `move_*`, `trash_*`, `delete_*`)
**cannot execute without a human saying yes**, enforced in layers:

1. **MCP elicitation** (primary): before executing, the server sends a
   human-readable preview through the client UI — the confirmation goes
   directly to the human, so the model cannot approve its own writes.
2. **Two-step fallback** (clients without elicitation): the first call returns
   a preview + one-time confirm token and mutates nothing; only a second call
   with the token executes. All executed writes are logged to stderr.
3. **Tool annotations** (`readOnlyHint` / `destructiveHint`) + the strict
   naming convention, so client permission systems can auto-allow reads and
   always-prompt writes.

Destructive operations are softened where the API allows it: Drive "delete"
is **trash**, never a hard delete.

## Planned tool surface (~22 tools)

| Service | Read | Write (approval-gated) |
|---|---|---|
| Tasks | `list_task_lists` `list_tasks` `get_task` | `create_task` `update_task` `complete_task` `move_task` `delete_task` |
| Calendar | `list_calendars` `list_events` `search_events` `get_event` | `create_event` `update_event` `respond_to_event` `delete_event` |
| Drive | `search_files` `get_file_metadata` `read_file_content` | `create_file` `update_file_content` `move_file` `trash_file` |

OAuth scopes: `tasks`, `calendar.events` (events only — no ACL/settings
access), `drive`.

## Stack

Python 3.12+ · [`uv`](https://docs.astral.sh/uv/) · official
[`mcp`](https://github.com/modelcontextprotocol/python-sdk) SDK (FastMCP,
stdio) · `google-api-python-client` + `google-auth-oauthlib` · `keyring` ·
pytest + ruff.

## Contributing

Issues and PRs are welcome — see [CONTRIBUTING.md](CONTRIBUTING.md). All
contributions are reviewed before merge; PRs that touch auth, scopes, or the
write-approval layer get extra scrutiny.

## License

[MIT](LICENSE)
