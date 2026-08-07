---
description: Report whether Core needs upgrading, and print the command that does it.
allowed-tools: Bash(theurian:*)
---

# /theurian:upgrade

Report whether the installed Core is one this plugin supports, and print the
command that upgrades it.

**This command does not upgrade anything.** Core is installed by `uv` or `pipx`
and is replaced the same way; Theurian does not obtain its own artifacts. The
`allowed-tools` line above is the structural half of that — this command can run
`theurian` and nothing else, so it cannot invoke an installer even by mistake.

## What to do

1. Show what is installed:

   ```sh
   theurian version --json
   ```

2. Ask Core whether that version satisfies this plugin, passing the four values
   from the plugin's `compatibility.yaml`:

   ```sh
   theurian compat check \
     --plugin-version <pluginVersion> \
     --core-minimum <coreCompatibility.minimum> \
     --core-maximum-exclusive <coreCompatibility.maximumExclusive> \
     --protocol-version <coreCompatibility.protocolVersion> \
     --json
   ```

   Exit 0 is compatible, 3 incompatible, 2 malformed input. Core performs the
   comparison so that no client reimplements SemVer ordering or the PEP 440
   translation (§34).

3. Report `outcome` and `message`, then print the verdict's `remedy` verbatim
   and stop. For `core-too-old` the remedy already names both installers:

   ```sh
   uv tool upgrade theurian     # or: pipx upgrade theurian
   ```

4. If the user runs it, the new version is picked up by the next session — the
   compatibility check runs at `SessionStart`. Re-running this command inside
   the current session reports the Core that was already resolved.

## Rules

- **Never run the upgrade.** Show the command and let the user run it. This is
  the same rule `/theurian:doctor` follows and the same one the `SessionStart`
  hook follows: every remedy Theurian prints is a suggestion.
- **Do not add or drop an extra when repeating the remedy.** Both installers
  record the spec they were given and re-resolve it, so an install that carried
  `theurian[daemon]` keeps it across an upgrade. Naming the extra here would
  imply that upgrading repairs a bare install, and it does not — a bare install
  re-resolves to a bare install. That user needs
  `uv tool install 'theurian[daemon]'` instead, which is what `/theurian:setup`
  and the `SessionStart` hook already tell them.
- If the outcome is `core-too-new`, the answer is to update the plugin, not to
  downgrade Core. Downgrading Core to satisfy one plugin breaks every other
  client on the machine.
- **Never downgrade to resolve a mismatch**, and never upgrade past this
  plugin's `maximumExclusive`. If the newest Core is outside the supported
  range, say so and stop; the correct order is to update the plugin first.
- If an upgrade changes the index format, mention that `/theurian:reindex` will
  be needed and roughly what that costs.
