# Windows packaging

**Status: interface defined, not implemented. Not a 1.0 release gate.**

`DaemonManager` has a Windows implementation slot, and `is_supported()` returns
`False` there today — so Windows fails clearly rather than mysteriously.

## Planned approach

Task Scheduler, registered per user:

- Trigger: at logon
- Action: `theurian.exe daemon start --foreground`
- Run whether or not the user is logged on: **no** (per-user, matching macOS and Linux)
- Highest privileges: **no** (never require administrator)

A Windows Service is the obvious alternative and is the wrong one: services
require administrator rights to install and run outside the user's session, which
contradicts the per-user model everywhere else.

## What needs resolving first

| Question | Note |
| :-- | :-- |
| Secret storage | DPAPI or Windows Credential Manager, behind `SecretStore` |
| Path containment | Case-insensitive filesystem, `\\?\` prefixes, 8.3 short names, and reparse points all need explicit handling — the current tests would not catch a Windows-specific escape |
| Line endings | Content hashing must stay byte-exact; `core.autocrlf` must not change a file's hash |
| Symlinks | Require either developer mode or elevation |
| Path length | 260-character limit unless long paths are enabled |

The path-containment work is the substantial one. `resolve_within_root` is
correct on POSIX; Windows has several distinct ways for two different strings to
name the same file, and each is a potential bypass.

## Interim

Windows Subsystem for Linux works today: install Theurian inside WSL and follow
the [Linux instructions](../linux/README.md).

Contributions welcome — start by opening an
[ADR proposal](../../.github/ISSUE_TEMPLATE/adr_proposal.yml) covering the path
containment questions above.
