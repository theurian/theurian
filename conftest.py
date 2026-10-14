"""Suite-wide: turn off git auto-maintenance for every repository the suite builds (#938).

After a commit, merge, fetch, ... git (>= 2.29) spawns a detached
``git maintenance run --auto`` that writes ``objects/maintenance.lock`` after the
command has returned. A test that byte-compares or hashes a repository it just
committed in raced it (#932, core.yml run 38494012447); one fixture was fixed
locally (``test_mutate_with_git.py``), this covers the class.

The ``GIT_CONFIG_COUNT`` / ``GIT_CONFIG_KEY_<n>`` / ``GIT_CONFIG_VALUE_<n>`` trio is
used because the other placements lose: a ``GIT_CONFIG_GLOBAL`` session file is
overridden by the 17 test files that pin ``GIT_CONFIG_GLOBAL=os.devnull``, and a
per-fixture ``git config`` is enumerative (21 files spawn git directly, plus shared
helpers with 50, 25 and 11 importers). ``pytest_configure`` runs in the controller
and in every xdist worker, so collection and subprocesses inherit it. An inherited
``GIT_CONFIG_COUNT`` is appended to, never overwritten.

Residual, measured on git 2.47.1: git unsets ``GIT_CONFIG_COUNT`` for the far side
of a local transport (``unset GIT_CONFIG_COUNT GIT_PREFIX; 'git-receive-pack ...'``),
so the bare remote of a local push still spawns maintenance. Four files push
(``test_findings_build_cli``, ``test_findings_builder``, ``test_git_trailer_source``
and e2e ``test_daemon_single_instance``); none hashes, walks or stats the remote
(key: ``rglob(|os.walk(|iterdir(|filecmp|hashlib|.stat()|_hash_tree``, read per hit).
A test that compares a push remote's bytes must set the config in that remote. An
explicit ``env=`` built without ``os.environ`` is the other blind spot; every one
among the 21 direct files derives from ``os.environ``.
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
