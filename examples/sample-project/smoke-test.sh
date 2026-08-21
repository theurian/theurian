#!/usr/bin/env bash
set -euo pipefail

SOURCE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TMP="$(mktemp -d "${TMPDIR:-/tmp}/theurian-sample-project.XXXXXX")"
PORT="${THEURIAN_PORT:-17419}"
DAEMON_PID=""

cleanup() {
  if [ -n "$DAEMON_PID" ] && kill -0 "$DAEMON_PID" >/dev/null 2>&1; then
    kill "$DAEMON_PID" >/dev/null 2>&1 || true
    wait "$DAEMON_PID" >/dev/null 2>&1 || true
  fi
  rm -rf "$TMP"
}
trap cleanup EXIT

cp -R "$SOURCE_DIR/." "$TMP"
cd "$TMP"
git init -q -b main

export THEURIAN_DATA_DIR="$TMP/.theurian-data"
export THEURIAN_PORT="$PORT"

theurian project register --project-id sample-project --json >/tmp/theurian-sample-register.json
theurian migrate validate --json >/tmp/theurian-sample-validate.json
theurian migrate apply --json >/tmp/theurian-sample-apply.json
theurian index build --json >/tmp/theurian-sample-index.json

theurian daemon start --foreground --port "$THEURIAN_PORT" >"$TMP/daemon.log" 2>&1 &
DAEMON_PID="$!"

python3 - <<'PY'
import json
import os
import sys
import time
import urllib.request

port = os.environ["THEURIAN_PORT"]
deadline = time.monotonic() + 30
while time.monotonic() < deadline:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=1) as response:
            json.load(response)
            raise SystemExit(0)
    except Exception:
        time.sleep(0.2)

print("Theurian daemon did not become healthy.", file=sys.stderr)
raise SystemExit(1)
PY

uv run --python 3.13 --with 'mcp==2.0.0' python query.py
