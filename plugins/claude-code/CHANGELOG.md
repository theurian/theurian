# Changelog — Theurian Claude Code plugin

All notable changes to the **plugin** are documented here. Core has its own
changelog at [`packages/theurian-core/CHANGELOG.md`](../../packages/theurian-core/CHANGELOG.md);
the two version independently (ADR-0001).

Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versioning: [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Plugin manifest, deliberately without an `mcpServers` entry so that installing
  the plugin has no observable effect (ADR-0012).
- Twelve commands: `setup`, `status`, `doctor`, `register-project`,
  `unregister-project`, `index`, `reindex`, `migrate`, `ingest`, `propose`,
  `upgrade`, `uninstall`.
- `SessionStart` hook performing a bounded health check only, with a hard
  timeout and unconditional exit 0.
- `compatibility.yaml` declaring the supported Core range and protocol version.
- `mcp/theurian.mcp.json` connection template, installed by `/theurian:setup`,
  carrying an environment-variable reference rather than a literal token.
- Shell helpers that shell out to the `theurian` CLI and contain no Theurian
  logic.

### Fixed

- `/theurian:setup` no longer presents itself as the way Theurian Core gets onto
  the machine. Its `description` — visible in Claude Code's command list — said
  "Install and configure Theurian"; the document opened by calling itself the
  only command that installs software; and it now begins by checking
  `command -v theurian` and naming `uv tool install theurian` or
  `pipx install theurian`. Setup runs *from* an installed Core and cannot be
  what creates it.
- The `SessionStart` hook told a user with no Core to run `/theurian:setup`,
  which shells out to the `theurian` binary whose absence produced the warning.
  It now names the installer first.
- The plugin README's install sequence began at the marketplace and ended at
  `/theurian:setup`, never mentioning Core. Core is now the first step.
- **The installer the plugin named produced a Core whose daemon cannot start.**
  All three surfaces — the `SessionStart` hook's Core-absent warning, the README's
  install block, and `/theurian:setup`'s prerequisite — said
  `uv tool install theurian`, which resolves, installs, and leaves out `uvicorn`;
  `/theurian:setup` then had nothing to configure
  ([#78](https://github.com/theurian/theurian/issues/78)). They now name
  `uv tool install 'theurian[daemon]'` and `pipx install 'theurian[daemon]'`, in
  the same words Core's own `core-missing` remedy and `core-present` step use.
  The quotes are part of the command: unquoted, `theurian[daemon]` is a glob
  under zsh and fails with `no matches found`.
- Step 6 no longer tells the agent that a step reporting `missing` with an
  `action` is one setup skips. All seven steps setup performs report exactly
  that before they run, so an agent following the old rule would have asked the
  user to go and "Create ~/.theurian with mode 0700" themselves.
- **`/theurian:upgrade` ran a command that does not exist.** It called
  `theurian upgrade --check --json` and then `theurian upgrade --json`; `upgrade`
  has never been a registered Core subcommand and both exit 2 with `No such
  command` ([#42](https://github.com/theurian/theurian/issues/42)). The command
  now reports the compatibility verdict from `theurian version` and
  `theurian compat check`, then prints `uv tool upgrade theurian` /
  `pipx upgrade theurian` for the user to run. It never upgrades anything, and
  its `allowed-tools` is `Bash(theurian:*)`, so it cannot invoke an installer
  even by mistake — Theurian does not obtain its own artifacts.

  The command is kept rather than removed: `REQUIRED_COMMANDS` in
  `tests/unit/test_plugin_boundary.py` pins `upgrade` as one of the twelve §9
  commands, and both this README's command table and
  `docs/integrations/claude-code.md` list it. What changed is what it does.

### Security

- `/theurian:setup` narrows `allowed-tools` from `Bash(command:*)` to
  `Bash(command -v:*)` and drops `Edit`. `command` is a shell builtin that runs
  its argument, so the prefix pattern pre-approved arbitrary execution; the
  document's own Rules section already forbade editing configuration files.

### Compatibility

| | Version |
| :-- | :-- |
| Plugin | 0.1.0 |
| Core | >= 0.1.0-dev.0, < 0.2.0 |
| Protocol | theurian/v1 |
