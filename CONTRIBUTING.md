# Contributing

Thanks for your interest! This is a small, security-conscious project — the
rules are few but firm.

## Ground rules

- **All changes land via pull request.** The default branch is protected;
  nothing merges without maintainer review and approval.
- **The write-approval invariant is non-negotiable.** Any change that lets a
  mutating tool execute without explicit human confirmation — in either the
  elicitation path or the two-step fallback — will be rejected, no matter how
  convenient.
- **No new OAuth scopes** without a discussion issue first. The scope set
  (`tasks`, `calendar.events`, `drive`) is a deliberate ceiling.
- **No secrets, ever** — not in code, tests, fixtures, or CI. Tokens belong in
  the OS keyring; `credentials.json` stays outside the repo.
- **Dependencies are a curated set** (official Google + MCP SDKs, `keyring`).
  Adding a dependency needs a strong reason.

## Practicalities

- Python 3.12+, managed with `uv`. Run `uv sync`, then `uv run pytest` and
  `uv run ruff check` before pushing.
- Keep PRs small and single-purpose. Reference an issue where one exists.
- Changes to auth, scopes, or the approval layer get extra review scrutiny —
  expect questions.

## Reporting security issues

Please do **not** open a public issue for suspected vulnerabilities — use
GitHub's private vulnerability reporting ("Report a vulnerability" under the
Security tab) instead.
