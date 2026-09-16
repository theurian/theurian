# Theurian for Codex CLI

> Invoke your engineering knowledge.

Registers a Theurian daemon already running on your machine as a streamable HTTP
MCP server for the Codex CLI, so a Codex session can search the specifications,
decisions, and reviewed records your team keeps in Theurian.

There is nothing to install here: registration is one command, removal is one
more. Every command below was run against `codex-cli 0.154.0`.

---

## Before you start

`theurian setup` must have completed on this machine. It is what puts a daemon
on `127.0.0.1:7419` and writes one guarded block into `~/.theurian/env`
(mode 0600) exporting `THEURIAN_MCP_TOKEN` from `~/.theurian/auth/mcp-token`
([ADR-0011](../../docs/adr/0011-local-mcp-authentication.md)).

Codex reads that variable out of its own environment, so the shell you launch
Codex from has to have sourced it:

```sh
. ~/.theurian/env
```

Setup does not edit your shell profile. Putting that line in it is yours to do.

## Register

```sh
codex mcp add theurian --url http://127.0.0.1:7419/mcp --bearer-token-env-var THEURIAN_MCP_TOKEN
```

That adds a table to `$CODEX_HOME/config.toml` — `~/.codex/config.toml` unless
`CODEX_HOME` says otherwise:

```toml
[mcp_servers.theurian]
url = "http://127.0.0.1:7419/mcp"
bearer_token_env_var = "THEURIAN_MCP_TOKEN"
```

`--bearer-token-env-var` stores the variable's *name*. The token itself never
enters the file, which is the point: config files get copied into gists, synced
to dotfile repositories, and pasted into issues (ADR-0011).

**It rewrites the whole `mcp_servers` table to do it, and what you wrote there
is not what comes back.** Measured at 0.154.0, all of it on servers the command
was never asked about: comments above a table, inside it, or trailing a value
are gone; lines carrying a default — `type = "stdio"`, `enabled = true` — are
dropped; and `startup_timeout_sec = 10` came back as `10.0`. The meaning is
preserved; the text is not. `codex mcp remove` re-serialises the same way and
restores nothing. Comments elsewhere in the file are untouched. Nothing warns
you, and there is no backup.

So back up `config.toml` first if you maintain it by hand — or skip the command
and add those three lines yourself. A hand-written table is read identically:
`codex mcp get theurian` reports the same entry from one, and reading never
rewrites the file.

**Do not use the `-- <command>` form** that `codex mcp add --help` offers for
stdio servers. A stdio server is spawned once per client, so several Codex
sessions would mean several processes writing one SQLite database. The result is
not slowness, it is corruption. One daemon serves them all over HTTP
([ADR-0002](../../docs/adr/0002-single-local-daemon-over-streamable-http.md)).

## Verify

```sh
codex mcp list
```

```text
Name      Url                        Bearer Token Env Var  Status   Auth
theurian  http://127.0.0.1:7419/mcp  THEURIAN_MCP_TOKEN    enabled  Bearer token
```

`codex mcp get theurian` prints the same entry in full, including the
`streamable_http` transport.

Then make a real call:

```sh
codex exec --approve-for-me -C "$(mktemp -d)" --skip-git-repo-check "Use the theurian MCP server's knowledge.search tool to search project theurian for 'single local daemon'. Report the number of results and the item ids. Do not run any shell commands."
```

```text
mcp: theurian/knowledge.search started
mcp: theurian/knowledge.search (completed)
```

Three flags, one reason each. `--approve-for-me` routes *every* approval request
in the session through automatic review, and it is needed because a plain
`codex exec` runs with approval policy `never` and refuses the tool call before
it runs: `MCP tool call requires approval, but approval policy is never`. That
review runs under the workspace-write sandbox, and `-s read-only` will not
compose with it — `the argument '--approve-for-me' cannot be used with
'--sandbox <SANDBOX_MODE>'` — so `-C` and `--skip-git-repo-check` pin the
writable workspace to an empty throwaway directory instead of wherever you
happen to be standing. Telling the model not to run shell commands is an
instruction, not a boundary; the working root is the boundary.

Run this to check the wiring once, not as a way to work day to day.

If the search refuses with `no built knowledge state`, that project has no index
yet and the refusal names `theurian migrate apply` in the project as the remedy.
`project.list` answers from the registry and works before that.

## Remove

```sh
codex mcp remove theurian
```

That deletes the table and re-serialises the section around it, with the comment
loss described above. Nothing else moves: no daemon is stopped, no knowledge is
deleted. Your team's knowledge lives in Git.

## What this is not

**A port of the Claude Code plugin.** That plugin runs a bounded health check at
SessionStart and ships twelve `/theurian:*` commands. Neither is here; run
`theurian doctor` and the `theurian` CLI directly.

**A packaged plugin.** Codex 0.154.0 has a plugin and marketplace system —
`codex plugin marketplace`, and `codex features list` reports `hooks` and
`plugins` stable and enabled — and Theurian publishes nothing to it. Both
absences are this integration's scope rather than a limit of Codex: registering
a running daemon is one command, so this integration is this file, and it lives
in the monorepo
([ADR-0001](../../docs/adr/0001-monorepo-with-independent-artifacts.md)).
