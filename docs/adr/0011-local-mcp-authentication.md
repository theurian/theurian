# ADR-0011: Local MCP authentication and token handling

- Status: accepted
- Date: 2026-08-01
- Deciders: Theurian maintainers
- Requirements: SEC-1 – SEC-6, T-1, T-2, T-8, T-9, §25 of the brief

## Context

`127.0.0.1:7419` is reachable by **every process running as the user**, and — via
DNS rebinding — by a web page the user visits. Theurian serves an organization's
architecture decisions, security rules, incident write-ups, and unreleased
specifications. An unauthenticated loopback endpoint is a full disclosure of all
of it to any script the user runs.

Authentication introduces its own problem: the token has to reach Claude Code's
MCP client, and the obvious place to put it is the MCP configuration file. That
file gets copied into gists, pasted into issues, synced to dotfile repositories,
and read by every tool on the machine.

## Decision

Defence in depth on the wire; an environment-variable *reference* — never the
literal secret — in configuration.

### Wire

1. Bind `127.0.0.1` only. Binding a non-loopback interface is not a supported
   configuration of the OSS Core.
2. Validate `Origin` and `Host` on every request (DNS-rebinding protection). The
   MCP SDK enables this automatically for localhost hosts; Theurian asserts it is
   on rather than assuming.
3. Require `Authorization: Bearer <token>` on `/mcp` and on every management
   endpoint. Compare in constant time.
4. `GET /health` is unauthenticated and returns only
   `{status, version, protocolVersion, dataDir, startedAt}` — enough for a health
   check, nothing about projects or knowledge. This is what lets `SessionStart`
   stay cheap and unprivileged.

   > **Amended in Milestone 3.** As accepted, this point listed
   > `uptimeSeconds` and no `dataDir`. Implementing the ADR-0002 startup
   > handshake showed the two cannot both hold: the handshake exists to
   > distinguish *our* daemon from a different Theurian squatting on the port,
   > and only the data directory answers that. Without it a starter must either
   > reuse a daemon serving someone else's knowledge base, or treat every
   > occupied port as a conflict.
   >
   > The disclosure is a filesystem path, to a caller that is already running as
   > this user and can therefore read `~/.theurian` directly; it reveals nothing
   > about projects or knowledge, which is the property this point protects.
   > `startedAt` replaces `uptimeSeconds` because an absolute timestamp lets a
   > caller tell a restarted daemon from a long-running one.

   > **Amended in Milestone 5, review round 7. "A caller that is already running
   > as this user" is one of two caller classes, and the disclosure argument was
   > written for that one only.** `/health` is exempt from the bearer token *and*
   > from the `Origin` and `Host` allowlist, because the rebinding settings are
   > passed to the mounted MCP app and `/health` sits beside it. Measured against
   > the real ASGI app: `GET /health` with `Origin: https://evil.example` and a
   > rebound `Host` returns 200 and the full body, where the same headers get 401
   > on `/mcp` without a token and 421 `Invalid Host header` with one.
   >
   > So the second caller class is **a web page in the user's browser**, which is
   > not running as this user and cannot read `~/.theurian`. To it, `dataDir` is
   > `/Users/<username>/.theurian` and therefore the OS username, alongside the
   > version and the uptime. The point's protected property still holds — nothing
   > about projects or knowledge crosses — and the sentence that cleared the path
   > does not.
   >
   > Left as accepted for Milestone 5 rather than changed: the token still bars
   > `/mcp`, so this is a fingerprinting disclosure and not a knowledge one. T-2
   > in the threat model records what is readable and the one option for closing
   > it — publishing a truncated `sha256` of the resolved path, which both
   > consumers' equality test can still use.
5. Tokens are ≥ 32 bytes from `secrets.token_urlsafe`.

### Storage

6. Canonical location: `~/.theurian/auth/mcp-token`, mode 0600, inside a 0700
   directory. Mode is verified on read; a world-readable token is refused, not
   silently used.
2. When available, mirror into the OS secret store through the `SecretStore` port
   (macOS Keychain, Linux Secret Service). The file remains the fallback, because
   a headless Linux box may have no Secret Service.
8. Rotation: `theurian auth rotate` writes a new token and rewrites
   `~/.theurian/env`. Rotation is never automatic, and never happens in
   `SessionStart` (§8 of the brief).

### Reaching the client

9. The MCP configuration contains a reference, never the secret:

   ```json
   {
     "mcpServers": {
       "theurian": {
         "type": "http",
         "url": "http://127.0.0.1:7419/mcp",
         "headers": { "Authorization": "Bearer ${THEURIAN_MCP_TOKEN}" }
       }
     }
   }
   ```

   Claude Code expands `${VAR}` and `${VAR:-default}` in `url` and `headers` for
   HTTP servers. Verified against the current Claude Code documentation before
   adopting this design.

10. `/theurian:setup` generates `~/.theurian/env` (mode 0600):

    ```sh
    THEURIAN_MCP_TOKEN="$(cat "${HOME}/.theurian/auth/mcp-token")"
    export THEURIAN_MCP_TOKEN
    ```

    and offers to add a single guarded block to the user's shell profile:

    ```sh
    # >>> theurian >>>
    [ -f "$HOME/.theurian/env" ] && . "$HOME/.theurian/env"
    # <<< theurian <<<
    ```

    The block is shown as a diff and requires consent (SEC-18). Only the guarded
    block is ever rewritten; the rest of the profile is never touched. If consent
    is declined, setup completes in `Degraded` state and prints the export line.

11. If the variable is unset, Claude Code passes the literal `${THEURIAN_MCP_TOKEN}`
    through and the daemon rejects it with a 401 whose body names the fix. A
    confusing failure is worse than a slightly verbose error message.

### Logging

12. Redaction happens at the logging sink, not at call sites. A single formatter
    scrubs anything matching the token, `Authorization` header values, and
    configured secret patterns. Relying on every call site to remember is how
    tokens end up in logs.

    > **Amended in Milestone 5, review round 7. This was never implemented, and
    > has read as a shipped control ever since it was accepted.** There is no
    > formatter and no logging sink. `security/tokens.redact` is the function
    > this point describes; its only caller in the repository is
    > `packages/theurian-core/tests/unit/test_tokens.py`.
    >
    > What holds the property in its place is not what this repository says it
    > is. `daemon/runner.py` runs uvicorn with `access_log=False` and
    > `log_level="warning"`, and that is read everywhere as the reason. Switching
    > both back on against a real daemon puts the token nowhere in the output:
    > `uvicorn.logging.AccessFormatter` writes the client address, method, path,
    > HTTP version and status code, and no header. The property holds because
    > nothing in this stack logs request headers — wider than the stated reason,
    > and nobody's decision.
    >
    > `tests/e2e/test_daemon_single_instance.py::test_the_token_never_reaches_the_log`
    > asserts the outcome against a real daemon and is worth keeping as the only
    > end-to-end check over a real log. It does not evidence the mechanism, and no
    > flip of either uvicorn argument makes it red.
    >
    > The decision is **not withdrawn**, because its reasoning is still right —
    > the first component that logs a request or an exception with headers
    > attached needs a sink, not a habit. It is restated as unimplemented, and
    > filed under *Still owed* below rather than left reading as a control in
    > force. A control that does not exist is worse than a missing one: it is
    > what a reviewer stops looking at.
13. `theurian doctor --report` redacts by default, because its output is what
    people paste into public issues (O-3).

## Consequences

### Positive

- A local process cannot read the knowledge base without also being able to read
  a 0600 file — which raises the bar from "any script" to "already has the user's
  filesystem access".
- A committed or pasted MCP configuration leaks nothing.
- Health checking needs no credential, so `SessionStart` stays fast and unprivileged.
- The same bearer-token shape upgrades cleanly to OAuth 2.1 in the cloud port.

### Negative

- The token must be in the environment of the process running Claude Code, which
  means either a shell-profile edit or a manual export. This is the main
  ergonomic cost and it is a deliberate trade against SEC-5.
- A user who launches Claude Code from a GUI launcher may not inherit the shell
  environment. `doctor` detects the resulting 401 specifically and explains it.

### Neutral

- Nothing here prevents a future Unix-domain-socket transport, which would make
  filesystem permissions the primary control. The token stays either way.

## Alternatives considered

| Alternative | Why rejected |
| :-- | :-- |
| No authentication on loopback | Any local process reads all knowledge. T-1, and the reason this ADR exists. |
| Literal token in the MCP config file | Violates SEC-5 and §4.2. Configs are copied, synced, and pasted. |
| Token in the URL path | Leaks into logs, process listings, and error messages. |
| OS peer-credential check (SO_PEERCRED) | Not available over TCP, and every local process shares the same UID anyway. |
| Full OAuth 2.1 locally | Enormous complexity for a single-user loopback daemon; specified for the cloud port instead. |
| Unix domain socket only | Better ambient security, but Claude Code's MCP client configures HTTP URLs. Revisit if UDS support lands. |

## Compliance

Landed in Milestone 3, all in `tests/integration/test_daemon.py` unless noted:

- `test_binding_a_non_loopback_address_is_refused` — the daemon refuses to bind
  anything but loopback.
- `test_mcp_without_a_token_is_refused`, `test_mcp_with_a_wrong_token_is_refused`,
  and `test_malformed_authorization_headers_are_refused` — every shape of bad
  credential receives 401.
- `tests/unit/test_tokens.py::test_a_prefix_does_not_verify` — guards the
  constant-time comparison against byte-at-a-time recovery.
- `test_a_cross_origin_request_is_rejected` and
  `test_a_foreign_host_header_is_rejected` — Origin and Host validation.
- `test_health_does_not_leak_the_token`,
  `test_the_401_names_the_fix_without_revealing_the_token`, and
  `tests/e2e/test_daemon_single_instance.py::test_the_token_never_reaches_the_log`
  — the token reaches neither a response body nor the daemon log.
- `test_the_env_file_references_the_token_rather_than_embedding_it` — the secret
  lives in one file; everything else points at it.
- `test_a_world_readable_token_is_refused` — mode 0644 is refused rather than
  repaired.
- `test_ensure_token_never_regenerates` — rotation is explicit.

Landed in Milestone 4:

- `tests/integration/test_setup_service.py::test_the_env_file_references_the_token_rather_than_embedding_it`
  and `test_the_mcp_entry_is_installed_without_the_literal_token` read the
  generated token back and assert it appears in neither the env file nor the MCP
  entry.
- `tests/integration/test_claude_mcp_config.py::test_the_real_cli_stores_the_variable_reference_verbatim`
  runs the real `claude` binary and asserts `${THEURIAN_MCP_TOKEN}` is stored
  rather than expanded — the property SEC-5 actually depends on.
- `tests/integration/test_setup_cli.py::test_the_report_mode_redacts_the_home_directory`
  — `doctor --report` is what people paste into public issues, and it is
  redacted by default rather than on request (O-3). Note what this pins: the home
  directory, not the token. It went on passing while the payload carried a live
  bearer token, because substitution reaches only values the local process put
  there, and a `theurian` MCP entry someone configured with a literal
  `Authorization` header was never one of them.
- `tests/integration/test_setup_report_withholding.py` covers the other half:
  every value a setup step reads but did not write is withheld under `--report`,
  asserted by grepping the whole payload for the literal secret rather than by
  checking the anchors. The credential cases are a literal bearer token in
  Claude Code's entry and a token in a service unit's environment.
- `test_a_second_run_never_regenerates_the_token` — setup mints a token only
  when there is none.

Still owed, with the milestone that will satisfy it:

- **Decision 12, the logging sink, is unimplemented** and this section previously
  read as though `test_the_token_never_reaches_the_log` discharged it. That test
  discharges the *outcome* for one log file — see the amendment to point 12 for
  why the mechanism is uvicorn's `access_log=False` and not a formatter. The
  decision itself is owed by whichever milestone first adds a component that logs
  a request, an exception with headers attached, or an audit record; nothing
  before that has a sink to put it in. Found in Milestone 5, review round 7.
- **`/health` outside the `Origin` and `Host` allowlist** (point 4's second
  amendment). No test asserts what that endpoint discloses to a cross-origin
  caller — `test_a_cross_origin_request_is_rejected` and
  `test_a_foreign_host_header_is_rejected` both drive `/mcp`. Deferred past
  Milestone 5 with the disclosure recorded under T-2; the milestone that changes
  `daemon/server.py`'s route layout owns it.
