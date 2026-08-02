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
   `{status, version, protocolVersion, uptimeSeconds}` — enough for a health
   check, nothing about projects or knowledge. This is what lets `SessionStart`
   stay cheap and unprivileged.
5. Tokens are ≥ 32 bytes from `secrets.token_urlsafe`.

### Storage

6. Canonical location: `~/.theurian/auth/token`, mode 0600, inside a 0700
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
    THEURIAN_MCP_TOKEN="$(cat "${HOME}/.theurian/auth/token")"
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

Still owed, with the milestone that brings the feature under test:

- The token never appears in a `SetupReport` or in `doctor` output, using a
  poisoned-token fixture (M4 — neither exists yet).
- The generated MCP configuration contains `${THEURIAN_MCP_TOKEN}` and never a
  literal secret (M4 — the configuration writer arrives with
  `/theurian:setup`).
