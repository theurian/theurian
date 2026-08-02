<div align="center">
  <img src="assets/theurian-logo.png" alt="Theurian" width="auto">

  <h1>Theurian</h1>

  <strong>Invoke your engineering knowledge.</strong>

  <p>Traceable engineering knowledge for AI agents.</p>

  <p>
    <a href="LICENSE"><img alt="License: Apache-2.0" src="https://img.shields.io/badge/license-Apache--2.0-blue.svg"></a>
    <img alt="Python 3.13+" src="https://img.shields.io/badge/python-3.13%2B-blue.svg">
    <img alt="Status: alpha" src="https://img.shields.io/badge/status-alpha-orange.svg">
  </p>
</div>

---

Theurian is a Git-native engineering knowledge platform that turns
specifications, architecture decisions, pull-request reviews, tests, and
operational knowledge into versioned, traceable context for AI agents.

The name comes from *theurgy* — the practice of invoking what is already there.
Your team has already decided most of this. Theurian makes those decisions
callable.

> **Status: Milestone 4.** The canonical store works, sources are ingested, a
> single local MCP daemon serves that knowledge over authenticated loopback
> HTTP, and `theurian setup` installs the whole thing idempotently. Search is
> still substring matching; ranked hybrid retrieval lands in Milestone 5. See
> the [roadmap](#roadmap).

---

## The problem

An agent asks: *"Should this endpoint use optimistic locking?"*

The answer exists. It is in an ADR from March, a review thread on PR #431 where
a staff engineer explained why the naive approach breaks under retry, and a
specification nobody has opened in six months. None of it is reachable, so the
agent guesses — and often guesses something the team explicitly rejected.

Grep does not help: the answer is a decision, not a string. A vector search over
Markdown does not help either: it returns text with no indication of whether the
decision is current, who approved it, or what it superseded.

## What Theurian does differently

It is not a Markdown search tool and not a general-purpose long-term memory.
Five properties define it:

| | |
| :-- | :-- |
| **Engineering knowledge governance** | Knowledge has an owner, a trust level, a validity window, and an approver. An unreviewed draft can never be mistaken for a team decision. |
| **Review-to-knowledge promotion** | A resolved review thread becomes a *candidate*. A human turns candidates into rules. Never the reverse. |
| **Specification traceability** | Requirement → spec → ADR → PR → review → code → test → operational evidence, as a queryable graph. |
| **Evidence-backed retrieval** | Every result resolves to a commit, a file, and a line range. No anchor, no result. |
| **Reproducible knowledge state** | State is content-addressed. Pin a `snapshotId` and an agent's run is reproducible months later. |

## How it works

```mermaid
flowchart TB
    subgraph Sources["Sources — read, never rewritten"]
        MD["Markdown / ADRs"]
        SPEC["YAML / JSON / OpenAPI specs"]
        GIT["Git commits and diffs"]
        GH["GitHub PRs, reviews, issues"]
    end

    subgraph Core["Theurian Core"]
        NORM["Normalize → Canonical model"]
        STORE["Canonical store<br/>immutable revisions"]
        INDEX["Index: FTS5 + vectors + RAPTOR forest"]
        TRACE["Traceability graph"]
    end

    subgraph Agents["AI agents — one shared daemon"]
        CC["Claude Code<br/>main + subagents"]
        OTHER["Any MCP client"]
    end

    Sources --> NORM --> STORE
    STORE --> INDEX
    STORE --> TRACE
    INDEX --> MCP["MCP over Streamable HTTP<br/>127.0.0.1:7419"]
    TRACE --> MCP
    MCP --> CC
    MCP --> OTHER

    GITREPO[("Git: migrations + approved content<br/>the record of truth")] --> STORE
    STORE -.->|proposals for human review| GITREPO
```

Git holds the record of truth. SQLite, embeddings, and RAPTOR trees are derived
artifacts, rebuilt on demand and never committed.

## Design decisions worth knowing up front

**One daemon per machine, over HTTP — never stdio.** A stdio MCP server is
spawned once per client. Ten subagents would mean ten processes writing to one
SQLite database and ten index builders racing each other. The failure mode is not
slowness, it is corruption. Every agent connects to `http://127.0.0.1:7419/mcp`.
([ADR-0002](docs/adr/0002-single-local-daemon-over-streamable-http.md))

**AI proposes; humans approve.** No MCP tool can write approved knowledge — not
behind a flag, not behind a permission. Write-intent tools emit a proposal that a
person reviews and merges. Approved knowledge is what an agent will cite tomorrow
as a team decision, so a human has to have said yes.
([ADR-0013](docs/adr/0013-ai-writes-produce-proposals.md))

**No knowledge is ever overwritten.** Revisions are immutable; items point at the
current one. A citation to a revision id means the same thing forever.
([ADR-0006](docs/adr/0006-immutable-revisions-and-optimistic-concurrency.md))

**Summaries never mix sensitivity levels.** A RAPTOR node's tree identity
includes project, tenant, sensitivity, ACL group, and namespace, so a summary
spanning a restricted incident report and a public API guide cannot be
constructed. This is structural, not a check someone could forget.
([ADR-0008](docs/adr/0008-raptor-forest.md))

**No vendor lock-in, and no API key required.** Embedding, summarization, and
reranking sit behind ports with deterministic in-tree defaults. `git clone && uv
sync && pytest` passes offline, for free, on any machine.
([ADR-0009](docs/adr/0009-no-llm-vendor-lock-in.md))

## Quick start

```sh
git clone https://github.com/theurian/theurian
cd theurian
uv sync
uv run pytest
```

Build a knowledge base from a repository:

```sh
cd /path/to/your/repo
theurian init                  # create .theurian/ and the .gitignore entries
theurian project register      # register this working tree
# author a migration under .theurian/migrations/, then:
theurian migrate validate
theurian migrate apply
theurian ingest                # normalize knowledge and specification sources
theurian project status
```

Or let setup do all of it:

```sh
theurian setup --dry-run   # show the plan; change nothing
theurian setup             # apply it; running twice changes nothing
theurian doctor            # what is wrong, read-only
```

`setup` registers a **user-scoped** service — a LaunchAgent on macOS, a systemd
user unit on Linux — and never asks for root. It adds Theurian's MCP entry to
Claude Code at user scope carrying `${THEURIAN_MCP_TOKEN}`, never a literal
token. Anything it finds already configured differently is shown as a diff and
left alone until you approve it.

To run the daemon by hand instead:

```sh
theurian daemon start --foreground   # one daemon, 127.0.0.1:7419, for every client
theurian daemon status               # what is running, and where its data lives
```

The daemon exposes five read-only MCP tools at `http://127.0.0.1:7419/mcp`:
`knowledge.search`, `knowledge.get`, `knowledge.status`, `project.list`, and
`system.capabilities`. Requests need a bearer token, which `daemon start` mints
into `~/.theurian/auth/mcp-token` (mode 0600) on first run:

```sh
curl http://127.0.0.1:7419/health     # no credential needed; this is what the hook calls

curl -H "Authorization: Bearer $(cat ~/.theurian/auth/mcp-token)" \
     -H 'Content-Type: application/json' \
     -H 'Accept: application/json, text/event-stream' \
     -d '{"jsonrpc":"2.0","id":1,"method":"initialize",
          "params":{"protocolVersion":"2025-06-18","capabilities":{},
                    "clientInfo":{"name":"curl","version":"1"}}}' \
     http://127.0.0.1:7419/mcp
```

Streamable HTTP is session-based: `initialize` returns an `mcp-session-id`
header that every later request must carry, so `tools/list` on its own answers
`400 Missing session ID`. In practice your MCP client handles this — the curl
above is a check that the endpoint is up and your token works, not a way to
drive the daemon by hand.

Starting a second daemon is safe: it detects the first, reports `reuse`, and
exits 0. One process serves every project you register and every agent that
connects.

Search is substring matching until Milestone 5 — the result *shape* is already
the published one, so callers written today keep working. What works now is the
part everything else depends on: a canonical store reproducible from Git that
refuses to let an applied migration change and reports conflicting edits instead
of merging them, served to agents with provenance and trust labels attached to
every result.

See [`examples/sample-project/`](examples/sample-project/) for a complete
`.theurian/` to copy from.

With Claude Code:

```text
/plugin marketplace add theurian/theurian-plugins
/plugin install theurian@theurian-plugins
/theurian:setup
```

Installing the plugin does nothing on its own — no daemon starts, no OS service
is registered. `/theurian:setup` is the only command that installs anything, it
shows a plan first, and running it twice changes nothing.

## Theurian and Serena

They answer different questions and are designed to be used together. Theurian
never calls Serena, and Serena never calls Theurian.

| Question | Tool |
| :-- | :-- |
| What did we decide about auth, and why? | **Theurian** |
| Was this approach rejected before? | **Theurian** |
| Which tests verify this specification? | **Theurian** |
| Where is `validateOrder` defined? | **Serena** |
| Who calls this function? | **Serena** |

A workflow using both: `spec.get` → `knowledge.search` → `review.findSimilar` →
Serena symbol search → Serena reference search → implement → `trace.findTests` →
`spec.getCoverage`.

Details: [docs/integrations/serena.md](docs/integrations/serena.md).

## Repository layout

```text
packages/theurian-core/   Python package: CLI, daemon, MCP server, domain, adapters
plugins/claude-code/      Claude Code plugin — separately versioned and released
schemas/                  Public JSON Schemas: the contract between the two
tests/                    Cross-artifact contract and E2E tests
docs/                     Architecture, ADRs, protocol, security, integrations
examples/                 A runnable sample project
packaging/                macOS, Linux, and Windows packaging
```

Core and the plugin are independent artifacts with their own versions,
changelogs, and release pipelines. The plugin never imports Core's Python — a CI
job fails the build if it does — which is what keeps it movable to its own
repository. ([ADR-0001](docs/adr/0001-monorepo-with-independent-artifacts.md))

## Roadmap

| Milestone | Scope | Status |
| :-- | :-- | :-- |
| 0 | Architecture, ADRs, domain model, ports, schemas, plugin skeleton, CI | **done** |
| 1 | Local canonical store, YAML migrations, state hashing, project CLI | **done** |
| 2 | Source ingestion: Markdown, YAML, JSON, OpenAPI | **done** |
| 3 | Single MCP daemon: Streamable HTTP, auth, multi-project | **done** |
| 4 | Claude Code plugin: setup, doctor, service adapters | **done** |
| 5 | Hybrid retrieval: FTS5, vectors, RRF, token budgets | next |
| 6 | RAPTOR forest, incremental rebuild, blue/green index | planned |
| 7 | GitHub review ingestion and knowledge candidates | planned |
| 8 | Specification and traceability tooling, drift detection | planned |

## Documentation

- [Requirements and architecture analysis](docs/architecture/requirements-analysis.md) — the reasoning behind everything here
- [Architecture decision records](docs/adr/README.md) — fifteen decisions and the alternatives rejected
- [Threat model](docs/security/threat-model.md)
- [Local MCP security](docs/security/local-mcp.md)
- [Migration format](docs/protocol/migrations.md)
- [Plugin/Core compatibility](docs/protocol/plugin-core-compatibility.md)
- [Claude Code integration](docs/integrations/claude-code.md) · [Serena](docs/integrations/serena.md)
- [Development](docs/contributing/development.md) · [Release](docs/contributing/release.md)

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). Commits are signed off under the
[DCO](docs/contributing/dco.md) — `git commit -s`. There is no CLA, which means
Core cannot be relicensed away from Apache-2.0 without every contributor's
agreement. ([ADR-0015](docs/adr/0015-dco-over-cla.md))

## Security

Please report vulnerabilities privately. See [SECURITY.md](SECURITY.md).

## License

Apache-2.0. See [LICENSE](LICENSE) and [NOTICE](NOTICE).
