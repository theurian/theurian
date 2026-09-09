#!/usr/bin/env bash
# SessionStart hook: a bounded health check, and nothing else.
#
# What this script may do (§8 of the brief):
#   - probe the daemon's health endpoint, briefly
#   - check whether the current repository is a registered project
#   - check whether the knowledge state is stale
#   - check whether the MCP connection is configured
#   - check plugin/Core compatibility
#   - print a warning when something is wrong
#
# What this script must never do:
#   - install a package
#   - register an OS service
#   - regenerate an authentication token
#   - rebuild an index
#   - delete a database
#   - modify a Git-tracked file
#   - perform anything resembling /theurian:setup
#
# The reasoning is simple: a session-start hook runs on every session, often
# many times a day, frequently while the user is thinking about something else.
# Anything slow is a tax on every session, and anything mutating is a surprise
# the user did not ask for and cannot see. Every remedy here is a suggestion.
#
# Budget: p95 <= 300 ms, hard cap 2 s (NFR-2). Exits 0 unconditionally -- a
# degraded Theurian must never block a session from starting.

set -uo pipefail

# shellcheck source=./lib.sh
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

main() {
  if ! theurian::cli_present; then
    # Naming /theurian:setup here was advice nobody could follow: that command
    # shells out to the `theurian` binary whose absence produced this warning,
    # and so does every step in its document. The installer goes first, in the
    # same words `probe_core` and the CORE_MISSING remedy use, so a user landing
    # on any of the three reads one instruction (FR-L3).
    #
    # Printed, never run. `theurian::warn` writes its argument to stderr and
    # nothing else, which is the whole of this hook's remit under §8.
    theurian::warn "Core is not installed. Install it with: uv tool install --python 3.13 'theurian[daemon]'" \
      "or: pipx install --python 3.13 'theurian[daemon]', then run /theurian:setup to configure this machine."
    return 0
  fi

  local verdict exit_code
  verdict="$(theurian::compat_check 2>/dev/null)"
  exit_code=$?
  if [ "$exit_code" -eq "$THEURIAN_EXIT_INCOMPATIBLE" ]; then
    # Stop using Theurian for this session, but never upgrade anything
    # automatically (§30). Report and let the user decide.
    theurian::warn "plugin and Core versions are incompatible."
    printf '%s\n' "$verdict" >&2
    return 0
  fi

  if ! theurian::daemon_healthy; then
    # A stopped daemon may be safely startable *if* the OS service is already
    # registered -- that is a user-approved service resuming, not an install.
    # If it is not registered, do nothing and point at setup (§8).
    if theurian daemon status --json 2>/dev/null | grep -q '"state": *"installed-stopped"'; then
      theurian::warn "daemon is registered but stopped; starting it."
      theurian daemon start >/dev/null 2>&1 || \
        theurian::warn "could not start the daemon. Run /theurian:doctor."
    else
      theurian::warn "daemon is not running and no service is registered. Run /theurian:setup."
    fi
    return 0
  fi

  # Healthy. Report only what needs attention; a silent hook is a good hook.
  local status
  status="$(theurian project status --json 2>/dev/null)" || return 0
  if printf '%s' "$status" | grep -q '"registered": *false'; then
    theurian::warn "this repository is not registered. Run /theurian:register-project."
  elif printf '%s' "$status" | grep -q '"reason": *"'; then
    # `reason` is Core's field for "part of this status is degraded", and it
    # reaches `project status --json` from four places in `cli/commands.py`,
    # not one: `_unresolved_status` on the *unresolved* branch (`resolve_context`
    # raised -- a broken migration, an unregistered root), and, on the
    # *resolved* branch -- `resolve_context` having finished successfully --
    # `_pointer_failure_fields` (a corrupt `.theurian/state/active.json`),
    # `_RegistryRead.failure_fields` (the registry re-read failed mid-command)
    # and `_RegistryRead.unreadable_entry_fields` (the registry read fine and
    # holds a hand-edited entry, so `registered` is `null`; issue #384).
    # So `reason` is not evidence that resolution failed: on the resolved
    # branch it arrives with `registered: true` and can sit right beside
    # `indexStale` and a `statePointerCorrupt: true` this same payload
    # publishes -- a healthy-looking index next to a broken state pointer is
    # exactly the shape a resolved-but-degraded project takes. `registered:
    # false` already took the branch above and never reaches this one, which
    # is what keeps the "not registered" advice untouched (issue #381 owns
    # telling that case apart from a not-in-git repository; nothing here
    # assumes it is closed).
    #
    # This branch is entered by matching the literal `"reason": *"` fragment
    # above, never by parsing JSON, and `reason` itself is never printed:
    # Core's own `_unresolved_status` docstring records that a broken
    # migration's `reason` carries the YAML parser's own source snippet --
    # project file bytes, not something a session-start hook may echo.
    # `remedy` is Core's own advisory sentence and holds no such risk by
    # itself, but a SessionStart warning must stay a fixed literal (see
    # `test_a_session_start_warning_cannot_execute_anything` and
    # `test_a_session_start_warning_is_a_terminated_literal` in
    # test_plugin_boundary.py) -- this hook prints only strings it wrote
    # itself, never one built from a field that could carry a path with
    # attacker-influenced components. /theurian:doctor is where Core's own
    # remedy belongs; it reads `project status` directly and can print
    # `remedy` in full.
    theurian::warn "this repository's knowledge context is degraded. Run /theurian:doctor to diagnose."
  elif printf '%s' "$status" | grep -q '"indexStale": *true'; then
    theurian::warn "the knowledge index is stale. Run /theurian:index when convenient."
  fi

  return 0
}

main "$@"
exit 0
