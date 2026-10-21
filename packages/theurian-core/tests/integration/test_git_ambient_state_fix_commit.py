"""``FixCommitCheck`` reads only this repository's own, whole, local history (#981).

Each case builds a real repository under ``tmp_path`` in which something git
consults besides the named commit -- an inherited variable, a replace ref, a graft,
a shallow boundary, a promisor remote, a rename's blob, a mailmap -- would change
the verdict or fetch an object, and asserts the refusal the isolated child produces.

``lazy_fetch_mode`` runs the partial-clone cases twice: once as git 2.45+ behaves
(``GIT_NO_LAZY_FETCH`` is honoured, so no fetch is attempted) and once as an older
git does (the variable is ignored, a fetch child may start, and only the
``GIT_ALLOW_PROTOCOL`` refusal keeps an object from arriving).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from git_isolation_fixtures import (
    clone,
    commit_file,
    git,
    has_object,
    missing_objects,
    new_repo,
    serving_origin,
    traced_fetches,
    without_lazy_fetch_guard,
)

from theurian.domain.review import FixCommitVerdict
from theurian.infrastructure.git.fix_commit_check import FixCommitCheck

pytestmark = pytest.mark.integration

FILE = "src/retrying.py"
OTHER = "docs/notes.md"
HONOURS = "git_honours_no_lazy_fetch"
OLDER = "git_below_2_45"


@pytest.fixture(params=[HONOURS, OLDER])
def lazy_fetch_mode(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> str:
    if request.param == OLDER:
        without_lazy_fetch_guard(monkeypatch)
    return str(request.param)


def _fetches_of(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, repo: Path, sha: str, path: str
) -> tuple[FixCommitVerdict, list[str]]:
    return traced_fetches(
        monkeypatch, tmp_path / "trace", lambda: FixCommitCheck(repo).verify(sha, path)
    )


def _history(tmp_path: Path, touched: list[str]) -> tuple[Path, list[str]]:
    origin = new_repo(tmp_path / "origin")
    shas = [commit_file(origin, path, f"v{index}\n") for index, path in enumerate(touched)]
    return origin, shas


_V = FixCommitVerdict


@pytest.mark.parametrize(
    ("touched", "depth", "verify_index", "expected"),
    [
        pytest.param([FILE, OTHER], 1, 1, _V.NO_SUCH_COMMIT, id="untouched-depth-1-boundary"),
        pytest.param([OTHER, FILE, OTHER], 2, 1, _V.NO_SUCH_COMMIT, id="touching-depth-2-boundary"),
        pytest.param([OTHER, OTHER, FILE, OTHER], 3, 2, _V.VERIFIED, id="touching-non-boundary"),
        pytest.param([FILE], 1, 0, _V.VERIFIED, id="true-root-at-depth-1"),
        pytest.param([FILE, OTHER], None, 1, _V.TOUCHES_NOTHING_HERE, id="full-clone-control"),
    ],
)
def test_a_shallow_boundary_commit_is_never_verified(
    tmp_path: Path, touched: list[str], depth: int | None, verify_index: int, expected: _V
) -> None:
    """A boundary commit reads as a root that added every file, so it would verify any path.

    A true root commit, and any commit with its parent present, still verifies.
    """
    origin, shas = _history(tmp_path, touched)
    flags = ("--depth", str(depth)) if depth else ()
    repo = clone(origin, tmp_path / "clone", *flags)
    assert (
        git(repo, "rev-parse", "--is-shallow-repository").strip() == str(depth is not None).lower()
    )

    assert FixCommitCheck(repo).verify(shas[verify_index], FILE) is expected


@pytest.mark.parametrize("redirect", ["replace-ref", "git-dir", "info-grafts", "graft-file-env"])
def test_a_redirected_history_never_verifies_a_commit_that_did_not_touch_the_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, redirect: str
) -> None:
    """Replace refs, grafts and an inherited ``GIT_DIR`` each rewrite what the sha reads as."""
    repo = new_repo(tmp_path / "work")
    touching = commit_file(repo, FILE, "a\n")
    untouched = commit_file(repo, OTHER, "b\n")
    root, sha, expected = repo, untouched, _V.TOUCHES_NOTHING_HERE
    if redirect == "replace-ref":
        git(repo, "replace", untouched, touching)
    elif redirect == "info-grafts":
        (repo / ".git" / "info" / "grafts").write_text(f"{untouched}\n", encoding="utf-8")
    elif redirect == "graft-file-env":
        grafts = tmp_path / "grafts"
        grafts.write_text(f"{untouched}\n", encoding="utf-8")
        monkeypatch.setenv("GIT_GRAFT_FILE", str(grafts))
    else:
        root, sha, expected = new_repo(tmp_path / "elsewhere"), touching, _V.NO_SUCH_COMMIT
        commit_file(root, OTHER, "c\n")
        monkeypatch.setenv("GIT_DIR", str(repo / ".git"))
    naive = git(root, "log", "--no-walk", "--name-only", "--format=", "--root", sha, "--", FILE)
    assert FILE in naive, (
        "the redirect does not reach an unisolated git, so this case proves nothing"
    )

    assert FixCommitCheck(root).verify(sha, FILE) is expected


def test_a_commit_in_a_treeless_partial_clone_is_refused_without_a_fetch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, lazy_fetch_mode: str
) -> None:
    """A lazy fetch makes the verdict depend on the promisor remote, not on this repository."""
    origin = serving_origin(tmp_path / "origin")
    commit_file(origin, FILE, "v1\n")
    fix = commit_file(origin, FILE, "v2\n")
    repo = clone(origin, tmp_path / "clone", "--no-checkout", "--filter=tree:0")
    before = missing_objects(repo)
    assert before > 0

    verdict, fetches = _fetches_of(monkeypatch, tmp_path, repo, fix, FILE)

    assert verdict is _V.NO_SUCH_COMMIT
    assert missing_objects(repo) == before
    assert lazy_fetch_mode == OLDER or fetches == []


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        pytest.param("docs/b.md", _V.VERIFIED, id="renamed-to"),
        pytest.param("docs/a.md", _V.VERIFIED, id="renamed-from"),
        pytest.param("docs", _V.TOUCHES_NOTHING_HERE, id="directory"),
    ],
)
def test_an_inexact_rename_is_judged_without_reading_a_blob(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, path: str, expected: _V
) -> None:
    """Rename detection compares blobs; in a blob-less clone that is a fetch or a refusal."""
    origin = serving_origin(tmp_path / "origin")
    commit_file(origin, "docs/a.md", "".join(f"line {n}\n" for n in range(200)))
    git(origin, "mv", "docs/a.md", "docs/b.md")
    (origin / "docs" / "b.md").write_text("".join(f"line {n}\n" for n in range(201)))
    git(origin, "add", "-A")
    git(origin, "commit", "-q", "-m", "rename with an edit")
    renamed = git(origin, "rev-parse", "HEAD").strip()
    repo = clone(origin, tmp_path / "clone", "--filter=blob:none")
    before = missing_objects(repo)
    assert before > 0

    verdict, fetches = _fetches_of(monkeypatch, tmp_path, repo, renamed, path)

    assert (verdict, fetches, missing_objects(repo)) == (expected, [], before)


def test_a_commit_the_clone_has_never_fetched_is_not_pulled_in_by_naming_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, lazy_fetch_mode: str
) -> None:
    """Naming a sha only the promisor remote holds must not make it, or its parents, local."""
    origin = serving_origin(tmp_path / "origin")
    commit_file(origin, FILE, "v1\n")
    repo = clone(origin, tmp_path / "clone", "--filter=blob:none")
    git(origin, "checkout", "-q", "-b", "side")
    chain = [commit_file(origin, f"docs/n{n}.md", f"n{n}\n") for n in range(3)]

    verdict, fetches = _fetches_of(monkeypatch, tmp_path, repo, chain[2], "docs/n2.md")

    assert verdict is _V.NO_SUCH_COMMIT
    assert not any(has_object(repo, sha) for sha in chain)
    assert lazy_fetch_mode == OLDER or fetches == []


def test_a_bare_partial_clone_does_not_ask_for_its_mailmap_blob(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, lazy_fetch_mode: str
) -> None:
    """A bare repository reads ``HEAD:.mailmap`` for ``log``; absent, that is a fetch attempt.

    Asserted on the trace in both modes: ``-c log.mailmap=false`` means git never asks,
    which is stronger than a protocol refusal stopping the ask.
    """
    origin = serving_origin(tmp_path / "origin")
    commit_file(origin, ".mailmap", "Test <test@example.com> <old@example.com>\n")
    fix = commit_file(origin, FILE, "v1\n")
    repo = clone(origin, tmp_path / "clone.git", "--bare", "--filter=blob:none")
    before = missing_objects(repo)
    assert before > 0

    verdict, fetches = _fetches_of(monkeypatch, tmp_path, repo, fix, FILE)

    assert (verdict, fetches, missing_objects(repo)) == (_V.VERIFIED, [], before)
