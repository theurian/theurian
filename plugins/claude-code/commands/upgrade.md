---
description: Report whether Core needs upgrading, and print the command that does it.
allowed-tools: Bash(theurian:*), Read
---

# /theurian:upgrade

Report whether the installed Core is one this plugin supports, and print the
command that upgrades it.

**This command does not upgrade anything.** Core is installed by `uv` or `pipx`
and is replaced the same way; Theurian does not obtain its own artifacts.

The control that enforces that is the **"Never run the upgrade"** rule below,
not the front-matter. `allowed-tools` is a permission *grant*: it auto-approves
`theurian` invocations and `Read` so this command does not prompt for them, and
it removes nothing — only `disallowed-tools` does that. The vendor documentation
says so in as many words: `allowed-tools` "grants permission for the listed tools
during the turn that invokes the skill … It does not restrict which tools are
available: every tool remains callable, and your permission settings still govern
tools that are not listed", while `disallowed-tools` names "Tools removed from
Claude's available pool while this skill is active"
([Extend Claude with skills](https://code.claude.com/docs/en/skills), accessed
2026-08-17). The grant is also per-turn: it clears when the user sends their next
message. Reading it as a sandbox is how a document ends up trusting a boundary
that is not there.

**This paragraph is the canonical statement of those semantics.** The other
command documents that depend on it point here rather than restating it, so
there is one place to correct if the vendor's behaviour changes.

## What to do

1. Show what is installed:

   ```sh
   theurian version --json
   ```

2. Read the plugin's `compatibility.yaml` and ask Core whether that version
   satisfies this plugin. **`pluginVersion` and `protocolVersion` are top-level;
   only `minimum` and `maximumExclusive` sit under `coreCompatibility`** — the
   schema declares `coreCompatibility` with `additionalProperties: false`, so
   reading `protocolVersion` from inside it yields nothing and the call fails
   with `Missing option` or a `must look like 'theurian/vN'` error.

   ```sh
   theurian compat check \
     --plugin-version <pluginVersion> \
     --core-minimum <coreCompatibility.minimum> \
     --core-maximum-exclusive <coreCompatibility.maximumExclusive> \
     --protocol-version <protocolVersion> \
     --json
   ```

   Exit 0 is compatible, 3 incompatible, 2 malformed input. Core performs the
   comparison so that no client reimplements SemVer ordering or the PEP 440
   translation (§34). These are the same five flags `theurian::compat_check` in
   `scripts/lib.sh` sends, and both the flags and the four placeholder keys are
   pinned to it and to the schema by
   `test_upgrade_command_names_the_same_flags_as_lib_sh` and
   `test_upgrade_command_placeholders_name_keys_the_schema_declares`.

3. Report `outcome` and `message`, then print the verdict's `remedy` **verbatim**
   and stop. For `core-too-old` the remedy already names both installers, and it
   is text to show the user rather than a command to run:

   > Upgrade Core with `uv tool upgrade theurian` or `pipx upgrade theurian`,
   > then start a new session.

4. Do not run it, and do not offer to. If the user runs it themselves, two
   different things happen at two different times:

   - **The binary changes immediately.** Both installers replace what a stable
     shim path points at, so the next `theurian version --json` — in this shell,
     in this session — reports the new version.
   - **This session's compatibility verdict does not.** It was resolved at
     `SessionStart` and is not re-evaluated. Re-running this command re-reads the
     version but the session's own decision about compatibility stands until a
     new session starts.

## Rules

- **Never run the upgrade.** Show the command and let the user run it. This is
  the same rule `/theurian:doctor` follows, and the `SessionStart` hook is bound
  by it too: §30 forbids upgrading anything automatically. Every remedy Theurian
  prints is a suggestion.
- **Do not add or drop an extra when repeating the remedy.** Both installers
  record the spec they were given and re-resolve it, so an install that carried
  `theurian[daemon]` keeps it across an upgrade. Naming the extra here would
  imply that upgrading repairs a bare install, and it does not — measured, a bare
  install re-resolves to a bare install. That user needs
  `uv tool install --python 3.13 'theurian[daemon]'` instead, which is what
  `/theurian:setup` and the `SessionStart` hook already tell them.
- If the outcome is `core-too-new`, the answer is to update the plugin, not to
  downgrade Core. Downgrading Core to satisfy one plugin breaks every other
  client on the machine.
- **Never downgrade to resolve a mismatch**, and never upgrade past this
  plugin's `maximumExclusive`. If the newest Core is outside the supported
  range, say so and stop; the correct order is to update the plugin first.
- If an upgrade changes the index format, mention that `/theurian:reindex` will
  be needed and roughly what that costs.
