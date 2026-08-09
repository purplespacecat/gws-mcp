# gws-mcp

A small, self-contained **MCP server for Google Tasks, Calendar, and Drive** —
read-write, with one hard rule: **every write requires explicit human
approval** before it executes.

> **Status: alpha (v0.1.0).** The full designed surface — 25 tools across
> Tasks, Calendar, and Drive — is implemented and tested (46 tests, both
> approval modes). Not yet battle-hardened against real-world API quirks.

## Why another Google MCP?

Google now ships [official remote MCP servers](https://developers.google.com/workspace/guides/configure-mcp-servers)
(Developer Preview) for **Gmail, Drive, Calendar, Chat and People** — hosted
HTTP endpoints you wire into a client as connectors. They do not cover
**Google Tasks**, they execute writes as soon as the model calls the tool
(no built-in approval step), and they are remote by design. Community
alternatives remain broad (10+ services) and mostly unmaintained.

This server is deliberately narrow and local — it pairs well with the
official connectors, filling the Tasks gap and covering Calendar/Drive when
you want approval-gated writes or a fully local path:

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

## Quick start

1. **Bring your own OAuth client** (one-time, ~30 min): in the Google Cloud
   console create a project, enable the **Tasks**, **Calendar**, and **Drive**
   APIs, create an OAuth 2.0 **Desktop app** client, and download its JSON to
   `~/.config/gws-mcp/credentials.json` (`chmod 600`). No credentials ever
   ship with, or are stored by, this project — the OAuth grant lives in your
   OS keyring.
2. **Authenticate** (interactive, opens a browser):
   ```bash
   git clone https://github.com/purplespacecat/gws-mcp && cd gws-mcp
   uv sync
   uv run gws-mcp auth          # then: uv run gws-mcp auth --status
   ```
3. **Wire it into your MCP client** (stdio):
   ```bash
   # Claude Code
   claude mcp add google-workspace -- uv --directory /path/to/gws-mcp run gws-mcp
   # Gemini CLI
   gemini mcp add google-workspace uv -- --directory /path/to/gws-mcp run gws-mcp
   ```
   In clients that support MCP elicitation, write approvals appear as UI
   prompts; in clients that don't, writes return a preview + one-time confirm
   token and nothing mutates until `confirm_write` is called.

### Serving a subset of services

Set `GWS_MCP_SERVICES` (comma-separated: `tasks`, `calendar`, `drive`) to
expose only some services — e.g. only Tasks, if you use Google's official
remote MCP servers for Calendar and Drive:

```bash
claude mcp add google-tasks --env GWS_MCP_SERVICES=tasks -- \
  uv --directory /path/to/gws-mcp run gws-mcp
```

This narrows both the *tools* the server exposes and the *scopes*
`gws-mcp auth` requests — a Tasks-only install never asks for the restricted
`drive` scope. Run the auth flow with the same value set
(`GWS_MCP_SERVICES=tasks uv run gws-mcp auth`); widening services later means
re-running it to consent to the added scopes.

## Contributing

Issues and PRs are welcome — see [CONTRIBUTING.md](CONTRIBUTING.md). All
contributions are reviewed before merge; PRs that touch auth, scopes, or the
write-approval layer get extra scrutiny.

## License

[MIT](LICENSE)
