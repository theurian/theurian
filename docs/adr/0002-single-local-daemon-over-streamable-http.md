# ADR-0002: A single local daemon, reached over Streamable HTTP

- Status: accepted
- Date: 2026-08-01
- Deciders: Theurian maintainers
- Requirements: FR-P4, FR-L6, NFR-1, NFR-4, NFR-7, SEC-1, SEC-13

## Context

The default way to ship an MCP server is stdio: the client spawns the server as
a child process. For Theurian that default is actively harmful.

Theurian owns a SQLite canonical store, an index publisher, and RAPTOR build
jobs. Those require a single writer and a single publisher. A stdio server is
spawned **per MCP client**: one per Claude Code session, and in practice one per
subagent. Ten subagents means ten processes, ten write connections to the same
database file, ten independent index builders, and ten copies of every in-memory
cache. The failure mode is not slowness — it is data corruption.

The second problem is state. A conventional MCP server keeps a "current project"
or a per-connection working directory. With one shared daemon serving many
repositories, many Git worktrees, and many agents concurrently, any implicit
context is a cross-project data-leak waiting to happen: subagent B's query
resolves against subagent A's project because a mutable field changed between
the two calls.

## Decision

**One daemon per user per machine. Explicit context on every call. No stdio.**

1. The daemon binds `127.0.0.1:7419` and serves MCP over Streamable HTTP at
   `/mcp`.
2. Every MCP client — main agent, every subagent, every other tool — connects to
   that one endpoint:

   ```json
   { "mcpServers": { "theurian": { "type": "http", "url": "http://127.0.0.1:7419/mcp" } } }
   ```

3. Generating a stdio configuration for Theurian is forbidden. A `command`-based
   Theurian entry is a bug, not a supported deployment.
4. There is **no** process-global `currentProject`, and **no** connection-scoped
   project state. Every tool call carries its own context:

   ```json
   { "projectId": "backend-service", "snapshotId": null, "agentId": null, "taskId": null }
   ```

   `projectId` is required by every project-scoped tool. Omitting it is a
   validation error, never a fallback to "the last one".
5. Single-instance enforcement uses three independent mechanisms, because each
   alone is known to fail:
   - an OS advisory file lock (`flock`) on `~/.theurian/daemon.lock` — survives a
     stale PID file, fails on some network filesystems;
   - a health probe against the port — catches a live daemon whose lock file was
     deleted;
   - a startup handshake reporting version and data directory — catches a
     *different* daemon squatting on the port.

   A losing starter exits 0 after confirming the winner is healthy. It never
   kills the winner and never repairs data automatically.
6. The daemon serves many Projects. Adding a repository is a registration, not a
   new process.

## Consequences

### Positive

- One writer, one publisher, one cache. The concurrency model in NFR-7 becomes
  expressible at all.
- Ten subagents cost one process (NFR-1).
- Index builds and RAPTOR trees are shared across every client, so the second
  agent to ask a question gets a warm index.
- Explicit context makes cross-project isolation testable: a test asserts that a
  call for Project A cannot observe Project B (SEC-13).
- HTTP makes the daemon observable with ordinary tools (`curl /health`).

### Negative

- Something has to start the daemon. That is a user-scoped OS service and the
  reason `/theurian:setup` exists at all.
- A port can be occupied. Requires the handshake in point 5 and a clear error.
- Callers must pass `projectId` explicitly, which is more verbose than an
  implicit default. This is the intended trade: verbosity for isolation.
- Loopback HTTP is reachable by any local process, so authentication is
  mandatory rather than optional (ADR-0011).

### Neutral

- The same design is what makes a future hosted deployment a configuration
  change rather than a rewrite: the transport and the explicit-context model are
  already the ones a multi-tenant server needs.

## Alternatives considered

| Alternative | Why rejected |
| :-- | :-- |
| stdio, one server per client | N writers on one SQLite file. Corruption, duplicated index builds, N× memory. The failure this ADR exists to prevent. |
| stdio with an external lock, delegating to a shared store | All the cost of a daemon plus a process per client, and the lock protocol becomes the hard part anyway. |
| One daemon per repository | Ten repositories means ten daemons and ten ports; cross-repository search — a core feature — becomes an inter-process problem. |
| Unix domain socket instead of TCP | Better ambient security, but Claude Code's MCP client configures HTTP URLs. Revisit if UDS transport support lands. |
| Connection-scoped project context | Saves one field per call and reintroduces exactly the cross-agent leakage this design exists to prevent. |

## Compliance

Landed in Milestone 3:

- `tests/e2e/test_daemon_single_instance.py::test_many_concurrent_clients_share_one_daemon`
  runs 12 concurrent MCP clients against a real daemon and asserts exactly one
  listening PID.
- `tests/e2e/test_daemon_single_instance.py::test_concurrent_starts_produce_one_winner`
  races five `daemon start` invocations and asserts one winner, `reuse` from the
  losers, and exit code 0 throughout.
- `tests/integration/test_daemon.py` covers the handshake directly: a daemon on
  a *different* data directory is a conflict rather than something to reuse or
  kill.
- `tests/integration/test_mcp_tools.py::test_a_query_for_one_project_cannot_observe_the_other`
  registers two projects against one server and asserts a query for one cannot
  reach the other's knowledge, including when both use the same `itemId`
  (SEC-13). `test_an_unregistered_project_names_what_is_registered` covers the
  adjacent case: an unknown id gets an error, never someone else's content.
- Every project-scoped MCP tool schema declares `projectId` in `required`.

Landed in Milestone 4:

- `tests/integration/test_claude_mcp_config.py::test_the_entry_declares_no_command`
  asserts the installed entry carries no `command` key, and
  `test_a_stdio_entry_someone_hand_wrote_is_a_difference` asserts a hand-written
  stdio entry is caught as a conflict rather than left in place. Point 3 above
  is the rule both enforce.
- `tests/integration/test_daemon.py::test_stopping_without_a_registered_service_is_refused`
  — `daemon stop` asks the service manager rather than signalling a PID, because
  point 5's advisory lock exists precisely because PIDs are recycled.

Landed in Milestone 5 — two ways SEC-13 was reachable despite the above:

- `tests/integration/test_mcp_tools.py::test_knowledge_get_will_not_hand_over_what_search_withheld`
  — every path through `knowledge.search` was gated on status and
  `knowledge.get` was not, so a caller read an approved item, took a
  `targetItemId` off one of its relations, and fetched the withheld body in one
  further call. No flag and no guessing were required.
- `test_relations_to_withheld_items_are_not_published` — the id itself is the
  disclosure. Withholding the body while publishing the pointer to it withholds
  nothing that matters.
- `test_the_withheld_message_does_not_confirm_the_item_exists` — the refusal for
  a withheld item is byte-identical to the one for an absent item, so the error
  cannot be used as an existence oracle.
- `test_asking_for_unapproved_reaches_a_draft_through_get` — the gate narrows
  what is surfaceable; it does not remove the caller's ability to opt in to
  drafts.

Point 4 said a call for Project A cannot observe Project B. It could, because ids
were not unique. Directory names repeat, so `team-one/api` and `team-two/api`
both proposed `api`, and `ProjectRegistry.register` overwrote — silently
re-pointing the id at the newer root, after which an agent working in `team-one`
that asked for `api` was served `team-two`'s knowledge with nothing in the answer
naming the repository. Pinned by
`tests/integration/test_project_registry.py`:

- `test_a_second_repository_with_the_same_directory_name_is_refused` and
  `test_the_registry_itself_refuses_the_clash_not_only_the_cli` — the refusal
  lives in the registry, so it cannot be bypassed by a caller that is not the
  CLI.
- `test_the_first_registration_survives_a_refused_collision` — a refusal that
  had already corrupted the registry would be worse than the overwrite.
- `test_a_distinct_id_registers_the_colliding_repository` — `--project-id` is a
  real remedy and not just better wording on an error.
- `test_a_registered_repository_resolves_to_its_registered_id_without_the_flag`
  and `test_a_root_is_looked_up_by_path_not_by_directory_name` — resolution asks
  the registry by root path before falling back to the directory name.
  Otherwise a project registered under a disambiguated id would still be
  addressed by the colliding default on its own command line.
- `test_the_id_that_lost_the_clash_still_points_at_the_first_repository` and
  `test_the_other_repositorys_knowledge_is_never_returned` — the same property
  end to end, through the MCP tools, which is where the disclosure happened.
- `test_re_registering_the_same_root_stays_idempotent` and
  `test_re_registering_a_disambiguated_project_stays_idempotent` — the refusal
  does not cost FR-L2.
