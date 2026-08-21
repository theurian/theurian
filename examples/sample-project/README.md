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

In another terminal, call the shipped `knowledge.search` tool:

```sh
tmp="$(cat "${TMPDIR:-/tmp}/theurian-sample-project.path")"
cd "$tmp"
export THEURIAN_DATA_DIR="$tmp/.theurian-data"
export THEURIAN_PORT=17419
python3 - <<'PY'
import http.client
import json
import os
import re
from pathlib import Path

token = Path(os.environ["THEURIAN_DATA_DIR"], "auth", "mcp-token").read_text().strip()
port = int(os.environ["THEURIAN_PORT"])
session = {}
connection = http.client.HTTPConnection("127.0.0.1", port, timeout=30)

def post(payload):
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        **session,
    }
    connection.request("POST", "/mcp", body=json.dumps(payload), headers=headers)
    response = connection.getresponse()
    if session_id := response.getheader("mcp-session-id"):
        session["mcp-session-id"] = session_id
    body = response.read().decode()
    if not body.strip():
        return {}
    match = re.search(r"^data: (.*)$", body, re.MULTILINE)
    return json.loads(match.group(1) if match else body)

post({
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-06-18",
        "capabilities": {},
        "clientInfo": {"name": "sample-query", "version": "1"},
    },
})
post({"jsonrpc": "2.0", "method": "notifications/initialized"})
response = post({
    "jsonrpc": "2.0",
    "id": 2,
    "method": "tools/call",
    "params": {
        "name": "knowledge.search",
        "arguments": {
            "projectId": "sample-project",
            "query": "order cancellation deadline before mutation",
            "limit": 1,
        },
    },
})
print(json.dumps(response["result"]["structuredContent"], indent=2))
connection.close()
PY
```

The top hit should be `domain.order-cancellation`. The useful part is not just
that text matched the query; the result also carries the sample's approved
status, review trust level, validity metadata, and declared GitHub review-thread
anchor.
