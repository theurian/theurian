# Sample project

A minimal but complete `.theurian/` directory, so you can see the shape of a real
knowledge base without writing one first (OSS-13).

## What is here

```text
.theurian/
├── config.yaml                          project configuration, no secrets
├── knowledge/
│   ├── architecture/auth-policy.md      an approved architecture decision
│   └── domain/order-cancellation.md     a rule generalized from a review
├── specifications/
│   └── order-cancellation.yaml          a spec that keeps its structure
└── migrations/
    ├── 01K1ABCXYZ...-add-auth-policy.yaml
    └── 01K1DEFABC...-add-order-cancellation.yaml
```

## What to notice

**Content and state are separate.** The Markdown files hold the knowledge. The
migrations hold status, ownership, sensitivity, and validity. Neither duplicates
the other, so neither can go stale relative to the other.

**The specification stays structured.** `order-cancellation.yaml` has
`preconditions`, `rules`, and `outcomes` as data. The current MCP surface returns
knowledge, status, projects, and capabilities; graph-shaped specification
coverage is roadmap work, not a shipped tool.

**Review evidence is attached, not quoted.** The second migration records an
illustrative review-thread anchor as evidence for the rule. The sample shows how
Theurian preserves declared evidence; it does not prove the placeholder
`acme/order-service` review exists.

**`dependsOn` orders the migrations.** The second depends on the first, so
applying them to an empty database always produces the same state.

**Nothing here is derived.** No SQLite, no index, no embeddings. Those are
rebuilt from exactly these files (ADR-0004).

## First useful query

After installing `theurian[daemon]`, run the sample as its own Git repository so
its `.theurian/` directory is the project root:

```sh
tmp="$(mktemp -d "${TMPDIR:-/tmp}/theurian-sample-project.XXXXXX")"
printf '%s\n' "$tmp" > "${TMPDIR:-/tmp}/theurian-sample-project.path"
cp -R examples/sample-project/. "$tmp"
cd "$tmp"
git init -q -b main
export THEURIAN_DATA_DIR="$tmp/.theurian-data"
export THEURIAN_PORT=17419

theurian project register --project-id sample-project --json
theurian migrate validate --json
theurian migrate apply --json
theurian index build --json
```

Start the local MCP daemon:

```sh
theurian daemon start --foreground --port "$THEURIAN_PORT"
```

If that port is already in use, set `THEURIAN_PORT` to another local port in
both terminals and rerun the daemon command.

In another terminal, run the sample query helper. It uses the shipped MCP
`knowledge.search` tool and prints the fields a first-time user should inspect:

```sh
tmp="$(cat "${TMPDIR:-/tmp}/theurian-sample-project.path")"
cd "$tmp"
export THEURIAN_DATA_DIR="$tmp/.theurian-data"
export THEURIAN_PORT=17419
uv run --python 3.13 --with 'mcp==2.0.0' python query.py
```

The top hit should be `domain.order-cancellation`. The useful part is not just
that text matched the query; the result also carries the sample's approved
status, review trust level, validity metadata, and declared GitHub review-thread
anchor.

Use `uv run --python 3.13 --with 'mcp==2.0.0' python query.py --json` to see
the full structured MCP response.

To check the whole sample path in one command from a source checkout:

```sh
examples/sample-project/smoke-test.sh
```
