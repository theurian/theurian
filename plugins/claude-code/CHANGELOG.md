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

### Compatibility

| | Version |
| :-- | :-- |
| Plugin | 0.1.0 |
| Core | >= 0.1.0-dev.0, < 0.2.0 |
| Protocol | theurian/v1 |
