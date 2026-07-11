# CLAUDE.md — gws-mcp

Scoped MCP server (local stdio) for Google Tasks/Calendar/Drive. The full
design + phased plan live in the owner's private vault
(`~/Documents/obsidian/docs/meta/gws-mcp-design.md`) — read it before
structural changes.

## Non-negotiable invariants

1. **Every mutating tool requires explicit human approval** before executing —
   MCP elicitation primary, two-step confirm-token fallback for clients
   without it. Never weaken either path, never let a write execute on the
   first call in fallback mode.
2. **Tool naming is the contract**: reads are `list_*/get_*/search_*/read_*`
   (+ `readOnlyHint`); writes are `create_*/update_*/complete_*/move_*/
   trash_*/delete_*` (+ `destructiveHint` where irreversible). Drive delete is
   trash, never hard delete.
3. **Scopes are a ceiling** (`tasks`, `calendar.events`, `drive` — in
   `config.py`): adding one needs a discussion issue, not a commit.
4. **No secret material on disk or in output**: tokens live in the OS keyring;
   `auth_state()` and every error message must stay leak-free
   (tests enforce this — keep `test_state_never_leaks_secret_material` honest).
5. **stdout is the MCP protocol channel** — server-side code never prints;
   diagnostics go to stderr.

## Dev

```bash
uv sync                 # env + lockfile
uv run pytest           # tests mock the keyring; must pass headless
uv run ruff check src tests
uv run gws-mcp auth     # interactive OAuth (browser) — CLI only, never in-session
uv run gws-mcp          # serve on stdio
```

Main is ruleset-protected: all changes via PR. Keep PRs single-purpose.
