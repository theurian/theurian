"""What the managed ``.gitignore`` block covers, asked of Git (#675).

Three prose sites rest on one negative -- no managed ignore entry covers
``.theurian/config.yaml``: :func:`config_escape_remedy`'s docstring, the cure's
pin in ``test_contained_path_envelope.py``, and the #652 CHANGELOG entry. Each
uses it to justify the *shape* of the cure: what the reader removes at that path
is authored, Git-tracked policy that nothing recreates, so the remedy carries
``GITIGNORE_LINK_REMEDY``'s copy-back clause instead of the derived-artefact
"remove it, Theurian writes it again". An entry added to
:data:`GITIGNORE_SECTIONS` that happens to match ``config.yaml`` falsifies all
three sentences at once, and until this module nothing went red for it --
``test_project_and_traceability.py`` asserts which entries are *present*, and
presence is the other direction.

The question goes to ``git check-ignore`` in a real repository rather than to
``fnmatch`` over the tuple, for two reasons. A reimplementation of gitignore
semantics agrees with whatever it implements, and Git's answer covers the whole
block at once: it evaluates every rule in the file, so one call is the negative
over all of :data:`GITIGNORE_ENTRIES` rather than over the entries somebody
remembered to enumerate.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from theurian.application.project_service import ensure_gitignore
from theurian.domain.project import GITIGNORE_ENTRIES

pytestmark = pytest.mark.integration

#: The authored file the negative is about, and a derived artifact beside it.
#: The second is the positive control: the globs in the managed block *do* match
#: files under ``.theurian``, which is what makes the first answer mean
#: "no rule covers it" rather than "no rule was in force".
_AUTHORED = ".theurian/config.yaml"
_DERIVED = ".theurian/probe.sqlite"


def _repository(tmp_path: Path) -> Path:
    """A throwaway repository carrying the block `theurian init` writes.

    Written by :func:`ensure_gitignore` rather than pasted, so a new entry or a
    respelled one is measured here the day it lands.
    """
    root = tmp_path / "repo"
    (root / ".theurian").mkdir(parents=True)
    subprocess.run(
        ["git", "init", "-q", "-b", "main"],  # noqa: S607
        cwd=root,
        check=True,
        capture_output=True,
    )
    ensure_gitignore(root)
    (root / _AUTHORED).write_text('security:\n  secretScan: "block"\n', encoding="utf-8")
    (root / _DERIVED).write_bytes(b"")
    return root


def _check_ignore(root: Path, path: str) -> subprocess.CompletedProcess[str]:
    """``git check-ignore -v``, with the developer's own configuration out of reach.

    ``GIT_CONFIG_GLOBAL`` and ``GIT_CONFIG_SYSTEM`` point at the null device so
    the decision comes from this repository's ``.gitignore`` and from nothing
    else -- the reason ``test_propose_cli.py::_git`` records. A global
    ``core.excludesFile`` covering ``.theurian/`` would ignore both paths below,
    turning the negative red on one machine and the control green on every
    machine for a rule the managed block never wrote.

    Exit 0 means ignored, 1 means not ignored, and 128 means Git failed -- the
    three are distinguished at the call sites, because "not ignored" and "the
    measurement did not happen" look the same to any check phrased as ``!= 0``.
    """
    return subprocess.run(  # noqa: S603
        ["git", "check-ignore", "-v", path],  # noqa: S607
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull},
    )


def test_no_managed_ignore_entry_covers_the_projects_config_file(tmp_path: Path) -> None:
    """The negative three prose sites spend, measured where it is spent (#675).

    Both answers come from one repository holding one block, so a run in which
    Git never evaluated the managed rules fails on the control rather than
    passing on the subject.
    """
    root = _repository(tmp_path)

    authored = _check_ignore(root, _AUTHORED)
    derived = _check_ignore(root, _DERIVED)

    assert derived.returncode == 0, (
        f"the managed block did not ignore {_DERIVED}, so the negative below "
        f"measures nothing: {derived.stdout or derived.stderr}"
    )
    source, _, pattern = derived.stdout.split("\t")[0].split(":")
    assert source == ".gitignore", f"the control was ignored by {source}, not by the block"
    assert pattern in GITIGNORE_ENTRIES, f"{pattern!r} is not a managed entry: {derived.stdout}"
    assert authored.returncode == 1, (
        f"a managed entry now covers {_AUTHORED} -- the cure's copy-back clause and the "
        f"two docstrings and CHANGELOG entry that justify it are stale: "
        f"{authored.stdout or authored.stderr}"
    )
