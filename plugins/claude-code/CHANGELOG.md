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
- Step 6 no longer tells the agent that a step reporting `missing` with an
  `action` is one setup skips. All seven steps setup performs report exactly
  that before they run, so an agent following the old rule would have asked the
  user to go and "Create ~/.theurian with mode 0700" themselves.

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
