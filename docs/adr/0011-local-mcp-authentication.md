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
8. Rotation: `theurian auth rotate` writes a new token and refreshes the
   Theurian-owned block in `~/.theurian/env` through the same merge setup uses
   (point 10). **Nothing after the new token is on disk may end the command.**
   Markers that delimit no single block leave that file untouched; an OS-level
   refusal — a read-only checkout, a file another account owns, a full disk —
   leaves it wherever the write reached. Both name the file in `nextSteps` and
   the rotation still completes: the exposed credential outranks a comment marker
   or a permission bit, and by then the token has already been replaced. What the
   OS-level arm says is the exception's class name and never its message, which
   carries `strerror`, the errno and on some platforms a second path. Rotation is
   never automatic, and never happens in `SessionStart` (§8 of the brief).

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

10. `theurian setup` writes one guarded block into `~/.theurian/env` (mode 0600),
    and rewrites nothing else in that file:

    ```sh
    # >>> theurian >>>
    # Written by `theurian setup`. Sourced by your shell profile so that
    # Claude Code can expand ${THEURIAN_MCP_TOKEN} in its MCP configuration
    # without the literal token ever entering a config file (ADR-0011).
    #
    # Theurian rewrites only the lines between these two markers. Anything
    # you add outside them is left exactly as you wrote it.
    THEURIAN_MCP_TOKEN="$(cat "/Users/you/.theurian/auth/mcp-token")"
    export THEURIAN_MCP_TOKEN
    # <<< theurian <<<
    ```

    The token's path is written resolved, not as `${HOME}`. Everything outside
    the markers survives byte for byte, under `setup` and `auth rotate` alike
    (SEC-18) — with two exceptions, both of them additions or Theurian's own
    text: the unmarked whole-file rendering `0.1.0.dev0`–`dev2` wrote is
    recognised and replaced *in place* by this block, so an upgraded machine
    carries one `export THEURIAN_MCP_TOKEN` and not two; and a file that ended
    without a newline gains one, rather than having the start marker run onto the
    end of somebody's last line.

    **A marker is a whole line.** The file is split on `\n` and on nothing else —
    what a shell ends a line at — and a trailing carriage return is dropped from
    a line's text before it is compared, so a file with CRLF endings still
    delimits while its `\r` bytes stay outside every span. Marker text anywhere
    else on a line belongs to whoever wrote that line: `echo "between
    # >>> theurian >>> and here"` opens nothing.

    Two arrangements are reported and never repaired, because once the
    delimiters disagree setup cannot tell which lines are its own: **two or more
    start lines, anywhere in the file**, and **a start line with no end line
    after it**. The start lines are counted over the whole file before a span is
    chosen, so where a second one sits does not matter. An *end* line with no
    start above it, and a second end line, are neither: they delimit nothing, and
    a line Theurian cannot claim is a line Theurian keeps.

    The block is Theurian's own text and is written with `\n` endings, so a block
    that came back from a Windows editor with CRLF markers is not the current
    block. It is normalised once — the lines around it keeping their own endings —
    and the file is a fixed point from then on.

    > **Amended after Milestone 6, by the env-file managed-block CL
    > ([#128](https://github.com/theurian/theurian/issues/128)). As accepted,
    > this point showed an *unmarked* env file and put the guarded block in the
    > user's shell profile** — shown as a diff, requiring consent, "only the
    > guarded block is ever rewritten; the rest of the profile is never touched",
    > and a `Degraded` completion printing the export line if consent was
    > declined.
    >
    > **No step ever implemented any of that.** Setup does not edit a shell
    > profile, has no consent prompt for one and no `Degraded` arm for a declined
    > one; `STEPS` has no such member. The markers existed as `PROFILE_BEGIN` and
    > `PROFILE_END` in `application/setup_steps.py` with no reader at all, which
    > is how the sentence stood unchallenged from the day it was accepted.
    >
    > Meanwhile the promise attached to those unread constants — only the block
    > between them is rewritten — was **false of the one file setup does write**.
    > `apply_env_reference` opened `~/.theurian/env` with `O_TRUNC` and rendered
    > it whole, and the probe reported `Missing` on any difference, so a line
    > added to a file whose own header says "Sourced by your shell profile" was
    > destroyed with no diff, no backup and no mention in `changedPaths` — on
    > every setup and every rotation, by a command whose contract is that running
    > it twice changes nothing.
    >
    > So the guarded block moved into the file it had been describing all along,
    > and the ADR now states the property where it is enforced rather than where
    > it was imagined. Theurian owns a marked span inside `~/.theurian/env` and
    > nothing outside `~/.theurian`; the line that *sources* that file is still
    > the user's own edit to their own profile, which is the ergonomic cost the
    > first *Negative* below records and not an oversight.
    >
    > **The first cut of this amendment was substring-based, and the whole-line
    > rule above was false of it.** `str.find` opened the span at the first
    > *occurrence* of the start marker rather than at a line equal to it, counted
    > a second start only in what followed the end marker, and matched the
    > dev0–dev2 rendering as a substring. Measured over every file three symbols —
    > a start marker, an end marker, a line of the user's — build up to five
    > lines, 363 arrangements: 39 took the wrong refusal decision, and 16 of those
    > reported success while dropping 19 of the user's lines between them, the run
    > reporting `converged` and the re-probe `satisfied`. `S`, a user's line, `S`,
    > the block, `E` — what repairing an unterminated block by pasting a fresh one
    > under it leaves — was one of the 16. Matching lines, and counting the start
    > lines before choosing a span, is what makes those paragraphs true.
    >
    > Pinned by `packages/theurian-core/tests/unit/test_env_file_merge.py` for the
    > merge, `…/tests/integration/test_setup_env_file.py` for setup driven end to
    > end over real files, and
    > `…/tests/integration/test_auth_rotate.py::test_rotation_keeps_the_lines_the_user_added_to_the_env_file`
    > for the second writer. The whole-line rule is asserted over the population
    > rather than one shape at a time, in
    > `…/test_env_file_merge.py::test_no_arrangement_of_the_markers_loses_a_line_outside_the_block`
    > — the 363 arrangements above, of which 229 are refused and 134 merge with
    > every line outside the delimited block surviving in its original order.

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
  a value a setup step reads but did not write is withheld under `--report`,
  asserted by grepping the whole payload for the literal secret rather than by
  checking the anchors. The credential cases are a literal bearer token in
  Claude Code's entry, a token in a service unit's environment, and a token on a
  systemd continuation line — which parsed as a field *name*, and is why the rule
  is "only the names Theurian's own renderer produces", not "names, not values".

  What that file asserts is a sweep, not a list of routes: one sentinel per
  source a step reads and does not own, over every step in `STEPS`. The claim is
  therefore "no step in the current plan publishes a seeded value", which is
  checkable — not "every value, always", which is not. A step reading a source
  the sweep does not seed is outside it, and the sweep is where to add one.
- `test_a_second_run_never_regenerates_the_token` — setup mints a token only
  when there is none.

Landed after Milestone 6, discharging point 10 as amended
([#128](https://github.com/theurian/theurian/issues/128)):

- `packages/theurian-core/tests/unit/test_env_file_merge.py` pins the merge
  itself — a stale block replaced where it stands, a file with no Theurian
  material keeping all of it, a file that ends without a newline keeping its last
  line, and the dev0–dev2 whole-file rendering replaced rather than appended
  beside (which is what keeps a machine from carrying two assignments of
  `THEURIAN_MCP_TOKEN` naming different paths).
- `…/tests/integration/test_setup_env_file.py` drives the real `SetupService`
  over real files, because the defect lived in the seam and not in the decision:
  a probe asking one question while the apply performs a different write is
  exactly what shipped. `test_a_second_run_does_not_reopen_the_env_file` measures
  convergence on the file's mtime rather than on the report, so a run that
  rewrote identical bytes still fails it.
- The refusal arm is pinned on the bytes, not the state:
  `::test_an_undelimited_env_file_stops_the_run_before_anything_is_written` and
  `::test_approving_the_conflict_buys_progress_and_never_an_overwrite` —
  `--approve-conflicts` is consent to proceed *past* a conflict, and reads as
  "yes, do it" often enough to be worth pinning as the opposite.
- `::test_the_conflict_detail_carries_the_markers_and_the_remedy_and_no_other_line`
  holds the detail both ways round (O-3, SEC-6): it names the two markers, the
  path and the command to re-run, and carries no other line out of a file whose
  every other byte somebody else wrote. `doctor --report` publishes that detail.
  This arm is **outside** `test_setup_report_withholding.py`'s sweep: that
  sweep's env-file seed is now a current block with an assignment under it, which
  is what reaches the override warning, and a file in that shape is not
  conflicting. One seed per source is the sweep's shape, so the pin above is what
  covers this branch instead. Neither detail can carry a line by construction —
  the conflict one is built from `EnvBlockFault` and the marker constants, and
  `contains_shadowing_assignment` returns `bool` — which is recorded under T-9 in
  the [threat model](../security/threat-model.md).
- `…/test_env_file_merge.py::test_no_arrangement_of_the_markers_loses_a_line_outside_the_block`
  is the whole-line rule stated as a property rather than as a shape: every file
  a start marker, an end marker and a user's line build up to five lines long,
  363 of them, with the refusal rule read off the symbols instead of asked of the
  code. `::test_a_marker_that_is_not_the_whole_line_does_not_open_a_block` covers
  the four ways marker text appears inside a line somebody wrote, and
  `::test_an_end_marker_with_no_start_delimits_nothing_and_is_no_reason_to_refuse`
  and `::test_an_end_marker_above_the_block_does_not_become_the_blocks_own_end`
  hold the other half — the arrangements that are *not* a refusal.
- The CRLF pair, on both sides of the same claim:
  `…/test_setup_env_file.py::test_a_crlf_file_keeps_every_byte_outside_the_block`
  asserts the exact bytes through a real run and counts the `\r`s the run did not
  author, and `::test_a_block_that_arrived_with_crlf_endings_is_normalised_exactly_once`
  pins the sequence — missing once, applied once, satisfied afterwards, and the
  second run measured on the file's mtime because a rewrite to identical bytes is
  still a rewrite.
- `…/tests/integration/test_auth_rotate.py::test_rotation_keeps_the_lines_the_user_added_to_the_env_file`
  and `::test_rotation_leaves_an_env_file_it_cannot_delimit_alone_and_says_so` —
  the second writer, and the SEC-4/SEC-18 trade in point 8. Its other arm,
  `::test_a_rotation_survives_an_env_file_the_os_will_not_let_it_write`, asserts
  the three halves together: rotated, the file unmoved, and the repair in
  `nextSteps`; `::test_the_refusal_names_the_kind_of_failure_and_not_what_the_os_said`
  holds the class name in and the OS's own sentence out (SEC-6).
- The block being current is not the same claim as the shell exporting it.
  `…/test_setup_env_file.py::test_an_assignment_below_the_block_is_reported_rather_than_edited_away`
  pins a line below the block that assigns `THEURIAN_MCP_TOKEN` again as
  `Satisfied` with a caveat — never edited away, since it is not Theurian's line
  (SEC-18) — which ends the run `Degraded` rather than `Converged`. What that
  warning may say is pinned separately in
  `::test_the_override_warning_names_the_variable_and_never_the_line_it_found`:
  the path, the variable and the marker, exactly once, and never the line itself.

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
