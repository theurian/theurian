# ADR-0012: The plugin does not declare an MCP server; setup installs the connection

- Status: accepted
- Date: 2026-08-01
- Deciders: Theurian maintainers
- Requirements: FR-L3, CP-5, §7 of the brief

## Context

§7 of the brief is explicit: installing the plugin must not register an OS
service or start a daemon. Step 14 of setup is equally explicit: setup creates
the Claude Code MCP connection configuration.

Claude Code's documented behaviour for plugin-provided MCP servers is:

> When you enable a plugin, Claude Code starts its MCP servers automatically.

For an HTTP server that means an immediate connection attempt at plugin-enable
time. Nothing is spawned — an HTTP entry cannot start a process — but the user's
very first experience is a red, failed MCP server, before they have been told
that `/theurian:setup` exists. Worse, the failing entry stays in the UI as a
permanent artifact of enabling the plugin, which is exactly the "installation had
side effects" property FR-L3 rules out.

Placing the entry under the plugin also puts its lifetime under the plugin's
control, which conflicts with FR-I4: `theurian` is usable without Claude Code, and
a user may want the MCP connection to survive removing the plugin.

## Decision

**The plugin ships the connection as a template. `/theurian:setup` installs it.**

1. `plugins/claude-code/.claude-plugin/plugin.json` declares **no** `mcpServers`
   key, and the plugin root contains **no** `.mcp.json`. Both would be
   auto-loaded.
2. The connection definition lives at
   `plugins/claude-code/mcp/theurian.mcp.json` — a path Claude Code does not scan.
   The plugin still owns the definition, satisfying §6 of the brief.
3. `/theurian:setup` installs it at **user** scope (`~/.claude.json`), because the
   daemon is per-user and per-machine, not per-project.
4. Installation is a merge, never a replace:
   - if no `theurian` entry exists, add it;
   - if an identical entry exists, do nothing and report `Satisfied`;
   - if a different `theurian` entry exists, back up the file with a timestamp,
     show a diff, and ask;
   - every other server entry, including `serena`, is preserved byte-for-byte.
5. `/theurian:uninstall` can remove the entry independently of removing the
   plugin, the daemon, or the data — the granularity §9 requires.
6. The template carries `${THEURIAN_MCP_TOKEN}` and never a literal token
   (ADR-0011).

## Consequences

### Positive

- Installing the plugin genuinely does nothing observable. FR-L3 holds literally,
  not approximately.
- No failed-server entry appears before setup runs.
- The MCP connection outlives plugin removal, so `theurian` stays usable for
  other clients.
- Uninstall granularity is achievable, because the entry is not owned by the
  plugin's lifecycle.

### Negative

- Users must run `/theurian:setup` before any Theurian tool appears. This is
  intended and is stated in the plugin README, in `/theurian:status`, and in the
  `SessionStart` warning.
- Setup writes to `~/.claude.json`, a file Claude Code also owns. Mitigated by
  merge-not-replace, timestamped backup, diff display, and `--dry-run`.

### Neutral

- If Claude Code later supports a lazily-connected or opt-in plugin MCP server,
  this decision should be revisited by superseding this ADR.

## Alternatives considered

| Alternative | Why rejected |
| :-- | :-- |
| Declare `mcpServers` in `plugin.json` | Auto-connects at enable time; produces a failed server before setup; violates FR-L3. |
| Ship `.mcp.json` at the plugin root | Same auto-load behaviour as above. |
| Project-scope `.mcp.json` in the user's repository | Would commit a machine-local daemon URL to a shared repository, and prompt every teammate for approval of a server they have not installed. |
| Have `SessionStart` write the configuration | §8 forbids configuration mutation in `SessionStart`, and a hook that silently edits `~/.claude.json` is exactly the surprising behaviour this design avoids. |

## Compliance

- A test asserts `plugin.json` has no `mcpServers` key and that no `.mcp.json`
  exists at the plugin root.
- A test asserts the merge preserves an existing `serena` entry byte-for-byte.
- A test asserts a conflicting entry produces a backup plus a diff and does not
  write without consent.
- An E2E test asserts that after plugin install and before setup, no socket is
  listening on 7419 and no service is registered.
