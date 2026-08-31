# Packaging

Installers for the platforms Theurian supports at 1.0.

| Platform | Service mechanism | Status |
| :-- | :-- | :-- |
| macOS | LaunchAgent (`~/Library/LaunchAgents/`) | Milestone 4 |
| Linux | systemd user unit (`~/.config/systemd/user/`) | Milestone 4 |
| Windows | Task Scheduler | interface defined; not a 1.0 gate |

## Rules every packaging target follows

1. **Per user. Never root.** No installer requires administrator privileges.
   Software that needs `sudo` to read your own documentation is asking for more
   than it needs.
2. **Installation is an explicit user action**, reached only through
   `theurian setup` or `/theurian:setup` — never from a hook, never from a
   plugin install (FR-L3).
3. **Nothing is overwritten destructively.** An existing unit or plist with
   different contents is backed up and the difference reported (SEC-18).
4. **Every created path is enumerable** by `theurian uninstall --dry-run`
   before anything is deleted (NFR-12).
5. **Release artifacts carry SHA-256 checksums and a CycloneDX SBOM**, published
   with every release by
   [`release-core.yml`](../.github/workflows/release-core.yml) (OSS-7, OSS-11).
   That much is satisfied — OSS-11 asks that artifacts carry checksums, not that
   anything checks them. **Nothing verifies them at install time.** `theurian
   setup`'s artifact-integrity step returns `not-applicable` without checking
   anything, so no packaging target aborts on a mismatch today. The unmet half is
   setup step 3 in
   [§6.2 of the requirements](../docs/architecture/requirements-analysis.md);
   T-16 is open on it, tracked at
   [#80](https://github.com/theurian/theurian/issues/80) — which diagnoses the
   split and records that an issue for the control itself is
   [still owed](https://github.com/theurian/theurian/issues/80#issuecomment-5484550422),
   so no packaging target is waiting on scheduled work. It was filed as
   [#39](https://github.com/theurian/theurian/issues/39), which closed on its
   documentation half while the install-time control stayed unbuilt. See
   [the release process](../docs/contributing/release.md) for what is published
   and what is not consumed.

## Layout

```text
packaging/
├── macos/     LaunchAgent plist template, notarization notes
├── linux/     systemd user unit template, XDG paths
└── windows/   Task Scheduler definition (design only)
```

Templates are rendered by the `DaemonManager` adapters, which own the
platform-specific logic. Keeping the templates here rather than embedded in
Python makes them reviewable by people who know launchd and systemd but not this
codebase.
