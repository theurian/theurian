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
5. **Artifacts carry SHA-256 checksums**, verified before installation. Setup
   aborts rather than installing something it could not verify (T-16).

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
