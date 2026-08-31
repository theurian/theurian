"""``_child_env``'s ``TMPDIR`` sits beside the tree, never inside it (#452).

Landing ``--with-git`` (see ``test_mutate_with_git.py``) gives a copy a real
``.git`` at its root, and ``git rev-parse --show-toplevel`` -- the exact
mechanism ``theurian.cli.context.find_git_root`` uses -- walks *up* from any
nested directory to find one. ``_child_env`` used to root ``TMPDIR``/``TMP``/
``TEMP`` at ``tree / ".mutate-tmp"``, so every ``tmp_path``-rooted test run on
the verdict path was, once ``--with-git`` was on, running from *inside* the
copy's own lent repository -- which is precisely the boundary
``test_init_outside_a_git_repository_fails_clearly`` exists to check.

Measured while landing #452: with ``TMPDIR`` left under ``tree``, a
``--with-git`` verdict-path run of the corpus byte-identity mutation reported
``control-red`` on exactly that test, before the mutation itself was ever
compared -- and per this harness's own rule (``tools/mutate.py``'s
``_verdict_mode``), a red control means no KILLED verdict in the batch means
anything. Moving ``TMPDIR`` to a sibling of ``tree`` (``tree.parent``, which
is ``root`` and never carries a ``.git`` of its own) fixed it and is pinned
here two ways: structurally, that ``TMPDIR`` is never ``tree`` or a descendant
of it; and behaviourally, that a real ``git`` process asked to find its
toplevel from ``_child_env``'s own ``TMPDIR`` does not land on ``tree``.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import mutate_run
import pytest

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def _isolated_git(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Cut ``git`` off from the developer's own configuration.

    The same isolation ``test_mutate_with_git.py``'s ``_isolated_git`` fixture
    documents: a global ``core.excludesFile`` or ``init.templateDir`` must not
    decide what a fixture in this module tracks or discovers.
    """
    home = tmp_path / "git-home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(home / "absent.gitconfig"))
    monkeypatch.setenv("GIT_CONFIG_SYSTEM", str(home / "absent.gitconfig"))
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path))
    monkeypatch.delenv("GIT_DIR", raising=False)
    monkeypatch.delenv("GIT_INDEX_FILE", raising=False)
    monkeypatch.delenv("GIT_WORK_TREE", raising=False)


def _git(*arguments: str) -> subprocess.CompletedProcess[str]:
    """One git command, by absolute path, failing loudly and with git's reason."""
    git = shutil.which("git")
    if git is None:
        pytest.skip("this module needs a real git repository to probe the boundary")
    completed = subprocess.run(  # noqa: S603 - argv is written here, never user input
        [git, *arguments], check=False, capture_output=True, text=True
    )
    assert completed.returncode == 0, (
        f"the fixture's own `git {' '.join(arguments)}` failed, so the test below would be "
        f"asserting against a tree nobody built:\n{completed.stderr}"
    )
    return completed


def test_child_envs_tmpdir_is_never_the_tree_or_a_descendant_of_it(tmp_path: Path) -> None:
    """The structural half of the pin: no path-prefix relationship at all.

    Doesn't need a real ``.git`` to fail if this regresses -- a plain
    ``tree / ".mutate-tmp"`` would fail this on the path comparison alone,
    which is cheaper to check than spinning up git for every regression of
    this specific line.
    """
    tree = tmp_path / "tree-0"
    tree.mkdir()

    env = mutate_run._child_env(tree, tmp_path / "uv-cache")

    tmp_dir = Path(env["TMPDIR"])
    assert tmp_dir != tree
    assert tree not in tmp_dir.parents, (
        f"TMPDIR {tmp_dir} is inside {tree}; a --with-git tree's own .git would then cover "
        f"it, and any tmp_path-rooted test asserting 'not inside a Git repository' would see "
        f"one anyway"
    )


def test_a_real_git_repository_rooted_at_the_tree_does_not_reach_child_envs_tmpdir(
    tmp_path: Path,
) -> None:
    """The behavioural half: the exact mechanism the regression broke, run for real.

    Builds a real ``.git`` at ``tree`` -- standing in for what ``--with-git``
    lends a copy -- and asks real ``git`` to find its toplevel starting from
    ``_child_env``'s own ``TMPDIR``, the same call
    ``theurian.cli.context.find_git_root`` makes. A regression back to
    ``tree / ".mutate-tmp"`` would make this resolve to ``tree`` instead of
    failing.
    """
    tree = tmp_path / "tree-0"
    tree.mkdir()
    _git("init", "-q", str(tree))

    env = mutate_run._child_env(tree, tmp_path / "uv-cache")
    tmp_dir = Path(env["TMPDIR"])
    tmp_dir.mkdir(parents=True, exist_ok=True)

    result = _git_from(tmp_dir, "rev-parse", "--show-toplevel")

    if result.returncode == 0:
        found = Path(result.stdout.strip()).resolve()
        assert found != tree.resolve(), (
            f"`git rev-parse --show-toplevel` from _child_env's TMPDIR resolved to {found}, "
            f"the tree's own root -- a --with-git copy's lent repository has leaked into the "
            f"boundary a 'not inside a Git repository' test relies on"
        )


def _git_from(cwd: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    """A git command that is allowed to fail -- the assertion is on its answer, not its exit."""
    git = shutil.which("git")
    if git is None:
        pytest.skip("this module needs a real git repository to probe the boundary")
    return subprocess.run(  # noqa: S603 - argv is written here, never user input
        [git, *arguments], cwd=cwd, check=False, capture_output=True, text=True
    )
