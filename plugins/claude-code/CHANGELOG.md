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
- The `/theurian:setup` command doc now lists `halted` instead of `rolled-back`
  among the states where the verification pass never ran. Core renamed that
  terminal failure state — a critical step failing during apply halts the run
  rather than reporting a rollback that never existed
  ([#47](https://github.com/theurian/theurian/issues/47)).
- Step 6 says how to present a `halted` run's `changedPaths`, which it named
  without saying what to do with it. Three corrections
  ([#47](https://github.com/theurian/theurian/issues/47)): the remedy for a
  token left on disk is `theurian auth rotate`, not "keep or remove" — Core
  never deletes a credential a session may be holding, and removing the file by
  hand means reconfiguring every client that references it; `changedPaths` is
  the field that says what was written, because in `halted` the `steps[].status`
  values are still the plan's; and `changedPaths` covers only the run that
  produced it, so on a repeated halted run a credential left by the first run
  appears as the token step reporting `satisfied` rather than as a path.

### Security

- `/theurian:setup` narrows `allowed-tools` from `Bash(command:*)` to
  `Bash(command -v:*)` and drops `Edit`. `command` is a shell builtin that runs
  its argument, so the prefix pattern pre-approved arbitrary execution; the
  document's own Rules section already forbade editing configuration files.

## [0.1.1] - 2026-08-09

### Fixed

- **An incompatible Core produced a silent blocked session.** `session-start.sh`
  sources `lib.sh`, whose `set -euo pipefail` imposed `errexit` on a hook that
  turns `errexit` off one line earlier on purpose — it must exit 0 no matter what
  Core reports. The incompatible-Core branch is a bare command substitution,
  `verdict="$(theurian::compat_check)"`; under `errexit`, its non-zero exit (3,
  `THEURIAN_EXIT_INCOMPATIBLE`) aborted the shell right there, so the warning,
  the verdict printed to stderr, and the intended `exit 0` were all unreachable.
  A session with an incompatible Core exited 3 with empty stdout and empty
  stderr — a blocked session with nothing telling the user why
  ([#90](https://github.com/theurian/theurian/pull/90)). `lib.sh` now runs
  `set -uo pipefail`; failure travels by return status, and the hook again
  prints the warning and the compatibility verdict to stderr on every session
  before exiting 0.
- **`/theurian:upgrade` ran a command that does not exist.** It called
  `theurian upgrade --check --json` and then `theurian upgrade --json`; `upgrade`
  has never been a registered Core subcommand and both exit 2 with `No such
  command` ([#42](https://github.com/theurian/theurian/issues/42)). The command
  now reports the compatibility verdict from `theurian version` and
  `theurian compat check`, then prints `uv tool upgrade theurian` /
  `pipx upgrade theurian` for the user to run. It never upgrades anything —
  Theurian does not obtain its own artifacts.

  **What enforces that is the document's "Never run the upgrade" rule, not the
  front-matter.** An earlier draft of this entry said `allowed-tools:
  Bash(theurian:*)` meant the command "cannot invoke an installer even by
  mistake". That is false: `allowed-tools` is a permission *grant* that
  auto-approves matching invocations, and only `disallowed-tools` removes
  anything — as `tests/unit/test_plugin_boundary.py` has recorded since
  `Bash(command:*)` was found pre-approving arbitrary execution. The document
  also read `compatibility.yaml` while granting no `Read`, which is the same
  mistake seen from the other side, so `Read` is now granted like every other
  command that reads a file.

  The command's `theurian compat check` invocation named
  `coreCompatibility.protocolVersion`; `protocolVersion` is top-level and
  `coreCompatibility` is `additionalProperties: false`, so an agent following it
  would have hit exit 2. Two tests now pin the document against `lib.sh`'s
  `theurian::compat_check` — the flag names, and the placeholder key.

  The command is kept rather than removed: `REQUIRED_COMMANDS` in
  `tests/unit/test_plugin_boundary.py` pins `upgrade` as one of the twelve §9
  commands, and both this README's command table and
  `docs/integrations/claude-code.md` list it. What changed is what it does.
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
- **`compatibility.yaml`'s declared floor had a hole in it.** `theurian compat
  check` translates Core's PEP 440 version into SemVer before comparing it
  against a plugin's declared range, and the translation did not preserve
  ordering — PEP 440 sorts `.devN` below every pre-release phase, while SemVer's
  ASCII comparison put `dev` between `beta` and `rc`. Over one release train's
  780 ordered version pairs, 99 came out backwards. This plugin's own
  `minimum: 0.1.0-dev.0` accepted `0.1.0.dev1`, refused `0.1.0a1`, `0.1.0a2` and
  `0.1.0b1`, then accepted `0.1.0rc1` again — versions strictly newer than ones
  it had just refused
  ([#70](https://github.com/theurian/theurian/pull/70)). Core now orders both
  its own version and a declaration's bounds by the release train (`dev` <
  `alpha` < `beta` < `rc` < final) rather than by SemVer's ASCII comparison.
  `compatibility.yaml`'s values are unchanged; only what `0.1.0-dev.0` accepts
  is.

### Compatibility

| | Version |
| :-- | :-- |
| Plugin | 0.1.1 |
| Core | >= 0.1.0-dev.0, < 0.2.0 |
| Protocol | theurian/v1 |
