# Codex CLI integration

The [Codex integration guide](https://github.com/theurian/theurian/blob/main/plugins/codex/README.md)
is the whole of this integration — what has to be true before you start,
registration, verification, removal, and what the registration command does to a
file you may maintain by hand. Every Codex command in it was run against
`codex-cli 0.154.0`. This page points at that guide rather than restating it, so
there is one place to correct when Codex changes.

There is nothing to install for Codex, but `theurian setup` must already have
completed on this machine — it is what puts the daemon on `127.0.0.1:7419` — and
the shell Codex is launched from has to have sourced `~/.theurian/env`. Setup
does not edit your shell profile, so that last part is yours to do. After it,
`codex mcp add` registers the running daemon as a streamable HTTP MCP server and
`codex mcp remove` unregisters it. Codex is handed the *name* of the environment
variable holding the token, never the token itself
([ADR-0011](../adr/0011-local-mcp-authentication.md)).

Read the guide before running either command. `codex mcp add` rewrites the whole
`mcp_servers` table, and `codex mcp remove` re-serialises it the same way — that
table's comments gone, lines carrying a default dropped, values reformatted, no
warning and no backup — so back up a hand-maintained `config.toml` first. And do
not take the stdio form `codex mcp add` also offers: a stdio server is spawned
once per client, so several Codex sessions would mean several processes writing
one SQLite database
([ADR-0002](../adr/0002-single-local-daemon-over-streamable-http.md)).

`plugins/codex/` is a document, not a packaged plugin. The Claude Code plugin's
SessionStart health check and its twelve `/theurian:*` commands have no Codex
counterpart — run `theurian doctor` and the `theurian` CLI directly.

## Related

- [Codex integration guide](https://github.com/theurian/theurian/blob/main/plugins/codex/README.md)
- [Claude Code integration](claude-code.md)
- [ADR-0002 — single daemon, no stdio](../adr/0002-single-local-daemon-over-streamable-http.md)
- [ADR-0011 — local MCP authentication](../adr/0011-local-mcp-authentication.md)
