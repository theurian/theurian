"""Suite-wide: turn off git auto-maintenance for git commands that inherit the test env (#938).

After a commit, merge, fetch, ... git spawns a detached
``git maintenance run --auto`` that writes ``objects/maintenance.lock`` after the
command has returned. A test that byte-compares or hashes a repository it just
committed in raced it (#932, core.yml run 38494012447); one fixture was fixed
locally (``test_mutate_with_git.py``), this covers the class except the residual
below. The spawn was observed with GIT_TRACE on git 2.47.1 (Homebrew) and Apple git
2.54.0 (per #938's body).

The ``GIT_CONFIG_COUNT`` / ``GIT_CONFIG_KEY_<n>`` / ``GIT_CONFIG_VALUE_<n>`` trio is
used because the other placements lose: a ``GIT_CONFIG_GLOBAL`` session file is
overridden by the test-tree files that pin ``GIT_CONFIG_GLOBAL`` (to ``os.devnull`` or
an absent file) or drop it, and a per-fixture ``git config`` is enumerative over the
direct callers and the shared helpers. ``pytest_configure`` runs in the controller
and in every xdist worker, so collection and subprocesses inherit it. An inherited
``GIT_CONFIG_COUNT`` is appended to, never overwritten.

Residual, git commands that do not inherit the trio:

(a) The far side of a local transport: git unsets ``GIT_CONFIG_COUNT`` for it
(``unset GIT_CONFIG_COUNT GIT_PREFIX; 'git-receive-pack ...'``), so receive-pack in
a local push's bare remote still spawns maintenance. Four files push at ba2c80a9:
``test_findings_build_cli``, ``test_findings_builder``, ``test_git_trailer_source``
and e2e ``test_daemon_single_instance``; none hashes, walks or stats the remote.
Key:
``git grep -l -F -e '_git(' --and -e '"push"' ba2c80a9 -- packages/theurian-core/tests tests``

(b) An explicit ``env=`` built without ``os.environ``.

(c) An environment copied from ``os.environ`` that drops ``GIT_CONFIG_COUNT`` or every
``GIT_*``: ``trailer_source.py``, ``command_population.py``, ``tools/corpus_drift.py``,
``tools/mutate.py``, ``test_dogfood_corpus_governance.py``, ``adr_0038_support.py``
and ``adr_0039_support.py``; all seven run only read commands. Key:
``git grep -l -E 'startswith.*GIT_|GIT_CONFIG_COUNT' ba2c80a9 -- packages tests tools``

A test that compares the bytes of a repository a command in (a)-(c) touches must set
``maintenance.auto false`` in that repository itself.
"""

from __future__ import annotations

import os

import pytest

_COUNT = "GIT_CONFIG_COUNT"
_SAVED = pytest.StashKey[dict[str, str | None]]()


def pytest_configure(config: pytest.Config) -> None:
    previous_count = os.environ.get(_COUNT)
    index = int(previous_count) if previous_count else 0
    key, value = f"GIT_CONFIG_KEY_{index}", f"GIT_CONFIG_VALUE_{index}"
    config.stash[_SAVED] = {name: os.environ.get(name) for name in (_COUNT, key, value)}

    os.environ[key] = "maintenance.auto"
    os.environ[value] = "false"
    os.environ[_COUNT] = str(index + 1)


def pytest_unconfigure(config: pytest.Config) -> None:
    for name, previous in config.stash.get(_SAVED, {}).items():
        if previous is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = previous
