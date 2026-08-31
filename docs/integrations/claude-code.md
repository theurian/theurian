# Claude Code integration

The [plugin README](https://github.com/theurian/theurian/blob/main/plugins/claude-code/README.md) is the user-facing
guide. This page covers how the integration works and why it is shaped this way.

## Two artifacts, one contract

```mermaid
flowchart TB
    subgraph P["Claude Code plugin"]
        CMD["12 commands"]
        HK["SessionStart hook"]
        TPL["mcp/theurian.mcp.json (template)"]
        COMPAT["compatibility.yaml"]
    end

    subgraph PUB["Published contract — the only permitted surface"]
        A1["theurian CLI, --json"]
        A2["MCP over Streamable HTTP"]
        A3["GET /health, /capabilities"]
        A4["schemas/**.json"]
    end

    subgraph CORE["Theurian Core"]
        INT["theurian.* internals"]
    end

    P --> PUB --> CORE
    P -. "forbidden: import theurian" .-x CORE

    style CORE fill:#5a3a7a,color:#fff
    style PUB fill:#1f6f4a,color:#fff
```

The plugin contains no Python at all. Its scripts shell out to `theurian
<verb> --json`. A CI job greps for `import theurian` under `plugins/` and fails
the build, and a test asserts no `.py` file exists there.

That constraint is what keeps the plugin movable to its own repository, and it is
also why the plugin can never quietly acquire logic that belongs in Core.

## Installation flow

```mermaid
sequenceDiagram
    participant U as User
    participant CC as Claude Code
    participant PL as Plugin
    participant CLI as theurian CLI
    participant D as Daemon

    U->>CC: /plugin marketplace add
    U->>CC: /plugin install theurian
    CC->>PL: enable
    Note over PL: Nothing happens.<br/>No daemon, no service,<br/>no MCP server declared.

    U->>CC: /theurian:setup
    CC->>PL: run the setup command
    PL->>CLI: theurian setup --dry-run --json
    CLI-->>PL: plan
    PL->>U: show the plan, ask
    U->>PL: approve
    PL->>CLI: theurian setup --json
    CLI->>CLI: data dir, token, service, project, .theurian/
    CLI->>D: start
    D-->>CLI: healthy
    CLI->>CC: merge the MCP entry into ~/.claude.json
    CLI-->>PL: report
    PL->>U: what changed, what was already satisfied

    Note over CC,D: Next session: every agent connects to<br/>http://127.0.0.1:7419/mcp
```

## Why the plugin declares no MCP server

Claude Code starts a plugin's MCP servers as soon as the plugin is enabled. For
an HTTP entry that means an immediate connection attempt — nothing is spawned,
but the user's first experience is a red, failed server, before they have been
told `/theurian:setup` exists. That failed entry then persists as an artifact of
merely enabling the plugin, which is exactly the "installation had side effects"
property FR-L3 rules out.

So the plugin ships the definition at `plugins/claude-code/mcp/theurian.mcp.json`
— a path Claude Code does not scan — and `/theurian:setup` installs it at user
scope, merging rather than replacing.

A side benefit: the connection outlives plugin removal, so `theurian` stays
usable by other MCP clients, and `/theurian:uninstall` can remove the entry
independently of the plugin, the daemon, or the data.

Detail: [ADR-0012](../adr/0012-plugin-does-not-autoregister-mcp-server.md).

## What SessionStart does

Runs on every session, often several times a day, usually while the user is
thinking about something else. It must therefore be cheap and boring.

```mermaid
flowchart TD
    S["Session starts"] --> A{"theurian on PATH?"}
    A -->|no| W1["warn: Core is not installed.<br/>Install it with uv tool or pipx,<br/>then run /theurian:setup"] --> Z["exit 0"]
    A -->|yes| B{"compatible?"}
    B -->|no| W2["warn: version mismatch, show the remedy"] --> Z
    B -->|yes| C{"GET /health ok?"}
    C -->|no| D{"service registered?"}
    D -->|yes| E["start it — a user-approved<br/>service resuming"] --> Z
    D -->|no| W3["warn: run /theurian:setup.<br/>Install nothing."] --> Z
    C -->|yes| F{"repo registered?<br/>index stale?"}
    F -->|needs attention| W4["one short warning"] --> Z
    F -->|all good| Z2["silent"] --> Z

    style Z fill:#1f6f4a,color:#fff
```

The `theurian`-absent branch names the installer before the command that needs
one. What it prints, verbatim:

```text
Theurian: Core is not installed. Install it with: uv tool install --python 3.13 'theurian[daemon]' or: pipx install --python 3.13 'theurian[daemon]', then run /theurian:setup to configure this machine.
```

Printed, never run. Naming `/theurian:setup` on its own was advice nobody could
follow: that command shells out to the `theurian` binary whose absence produced
the warning.

Budget: p95 ≤ 300 ms, hard timeout 5 s, and it exits 0 unconditionally — a
degraded Theurian must never stop a session from starting.

**Never:** install a package, register an OS service, regenerate a token, rebuild
an index, delete a database, modify a Git-tracked file, or do anything
resembling `/theurian:setup`.

Starting an *already-registered* service is allowed: that is a service the user
approved, resuming. Registering one is not.

These constraints are tested by
`tests/unit/test_plugin_boundary.py::test_session_start_hook_performs_no_heavy_or_mutating_work`,
which greps the script for forbidden operations — a rule enforced by prose is a
rule that erodes.

## Every subagent shares one daemon

Because the connection is a URL rather than a spawned process, every agent that
inherits the MCP configuration reaches the same daemon. Ten subagents means ten
connections to one process, sharing a warm index.

If Theurian were stdio, ten subagents would mean ten processes writing to one
SQLite database and ten index builders racing. That is corruption, not slowness.
([ADR-0002](../adr/0002-single-local-daemon-over-streamable-http.md))

## Commands

All twelve are thin adapters over the CLI and contain no Theurian logic.
`/theurian:propose` was the exception until Milestone 7 registered the `propose`
subcommand [ADR-0013](../adr/0013-ai-writes-produce-proposals.md) describes
([#212](https://github.com/theurian/theurian/issues/212), closing
[#89](https://github.com/theurian/theurian/issues/89)); the command now shells out
to `theurian propose` / `theurian propose accept` like the rest, so the migration
format lives in Core rather than in the command document.

| Command | Underlying CLI |
| :-- | :-- |
| `/theurian:setup` | `theurian setup [--dry-run] --json` |
| `/theurian:status` | `theurian daemon\|project\|index\|migrate status --json` |
| `/theurian:doctor` | `theurian doctor --json` |
| `/theurian:register-project` | `theurian project register --json` |
| `/theurian:unregister-project` | `theurian project unregister --json` |
| `/theurian:index` | `theurian index build --json` |
| `/theurian:reindex` | `theurian index build [--raptor] --json`, `theurian index gc [--dry-run] --json` |
| `/theurian:migrate` | `theurian migrate validate\|apply --json` |
| `/theurian:ingest` | `theurian ingest --json` |
| `/theurian:propose` | `theurian propose --json`, `theurian propose accept --json` |
| `/theurian:upgrade` | `theurian version --json`, `theurian compat check --json` |
| `/theurian:uninstall` | `theurian uninstall [--dry-run] --json` |

`/theurian:propose` shells out to `theurian propose` to draft the proposal and
`theurian propose accept` to move it into place. Core writes
`.theurian/proposals/<proposal-id>/`; the command's own `Write` grant is only for
the body file it hands to `--body-file`. Accepting and the commands after it are
the user's: `theurian migrate validate --json` on the accepted migration, then
`theurian migrate apply --json` and `theurian index build --json` after the pull
request merges — `--raptor` on that last one where the project keeps a summary
forest, since a plain build writes no summary nodes.

The `Write` grant does not bound what the command may invoke. `allowed-tools`
grants and never removes — the semantics, with the vendor citation, are stated
once in
[`upgrade.md`](https://github.com/theurian/theurian/blob/main/plugins/claude-code/commands/upgrade.md) — so
`Bash(theurian:*)` auto-approves `migrate apply`, `index gc`, and now `propose
accept` (which moves a migration into `.theurian/migrations/`) even in the very
commands that reserve them for a human. That boundary is the command documents'
rules rather than a check Core performs
([#209](https://github.com/theurian/theurian/issues/209)).

Setup logic exists once, in `SetupService`. `/theurian:setup` and `theurian
setup` are the same code path with different presentation — duplicating it in the
plugin would guarantee the two drift.

## Compatibility

The plugin declares its supported range in `compatibility.yaml`; **Core performs
the comparison**, so nothing reimplements SemVer ordering or the PEP 440
translation. On a mismatch the plugin stops with an actionable message and
changes nothing.

Detail: [plugin-core-compatibility.md](../protocol/plugin-core-compatibility.md).

## Uninstalling

Eight independent scopes: plugin configuration, MCP entry, daemon service,
binary, cache and derived indexes, user settings, and repository `.theurian/`.
Destructive scopes default to off, `--dry-run` enumerates everything first, and
`.theurian/` is never removed without a confirmation that names what is being
deleted.

Removing the plugin never deletes approved knowledge. It is in Git.

## Related

- [Plugin README](https://github.com/theurian/theurian/blob/main/plugins/claude-code/README.md)
- [Using Theurian with Serena](serena.md)
- [ADR-0001 — monorepo with independent artifacts](../adr/0001-monorepo-with-independent-artifacts.md)
- [ADR-0012 — the plugin does not auto-register the MCP server](../adr/0012-plugin-does-not-autoregister-mcp-server.md)
