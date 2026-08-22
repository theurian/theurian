"""The real invocation: `main(argv)` over a real git index, with the real flags.

Everything under ``scan(root, tracked=...)`` bypasses two things the CI job
depends on, and this file is where they are exercised for real:

- **the population comes from `git ls-files --cached`.** The repositories built
  here are real ones, with a real index, so ``tracked_paths`` runs unmodified.
  That is the difference between an untracked migration being excluded because a
  test said so and being excluded because git did.
- **the flags decide the exit status.** ``.github/workflows/shared.yml`` runs
  this with ``--advisory --format github --summary`` and carries neither
  ``continue-on-error: true`` nor ``|| true``, deliberately: either would
  swallow exit 2 alongside exit 1 and leave a green tick over a check that had
  gone silent. Nothing else in the repository stops that, so the CLI's own
  handling of exit 2 is the last guard. The compared-count floor reaches the
  exit status through the same path and is mostly driven here with
  ``--minimum-compared``, because the tool's own default binds only on this
  repository's tree and none of these fixtures is that tree. One test drives the
  *default* instead, by repointing ``corpus_drift.REPO_ROOT`` at a fixture so
  that ``main`` with no flags at all takes the branch the CI job takes:
  ``test_the_default_floor_binds_the_one_tree_its_number_was_measured_against``.
  Without it, ``minimum_compared_for(...)`` can be cut out of ``main`` and this
  whole suite stays green.

``HOME`` is redirected for every test here, and the developer's real
``~/.gitconfig`` is read before and compared after: these tests shell out to
git, and a fixture that writes into the maintainer's own configuration has
already failed whatever it went on to assert. ``GITHUB_STEP_SUMMARY`` is removed
from the environment for the same class of reason -- it is *set* when this suite
runs on GitHub Actions, so a test that did not control it would append into the
real job summary and would pass locally for a reason that does not hold in CI.
"""

from __future__ import annotations

import hashlib
import shutil
import subprocess
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import corpus_drift
import pytest
import yaml

pytestmark = pytest.mark.integration

_MIGRATION = ".theurian/migrations/01MB4V3XKQ7ZPYE8R2NGT5HW6A-adr-0001-example.yaml"
_DOCUMENT = "docs/adr/0001-example.md"
_ITEM = "architecture.example"

_SNAPSHOT = "# ADR-0001: Example\n\nThe text as it stood when the snapshot was taken.\n"
_EDITED = _SNAPSHOT + "\n## Consequences\n\nA section added after the snapshot.\n"


@pytest.fixture(autouse=True)
def _off_the_developers_machine(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Redirect `HOME`, refuse an inherited step summary, and prove the real config is intact.

    `tracked_paths` builds its own environment and drops every `GIT_CONFIG_*`
    override from it, so `HOME` is what decides which global configuration the
    git it runs will read -- redirecting the overrides alone would not isolate
    it. The `GIT_CONFIG_*` settings below govern this fixture's own `git init`
    and `git add`.

    The before/after comparison is not ceremony: a test that shells out is the
    one that can quietly rewrite `~/.gitconfig`, and nothing downstream would
    report it.
    """
    real = Path.home() / ".gitconfig"
    before = real.read_bytes() if real.exists() else None

    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(home / "absent.gitconfig"))
    monkeypatch.setenv("GIT_CONFIG_SYSTEM", str(home / "absent.gitconfig"))
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", f"{tmp_path}:{tmp_path.resolve()}")
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)

    yield

    after = real.read_bytes() if real.exists() else None
    assert after == before, f"{real} was modified by a test that only meant to read it"


def _git(*arguments: str) -> None:
    """One git command, by absolute path, failing loudly and with git's own reason."""
    git = shutil.which("git")
    if git is None:
        pytest.skip("the population is `git ls-files` output, and this machine has no git")
    completed = subprocess.run(  # noqa: S603 - argv is written here, never user input
        [git, *arguments], check=False, capture_output=True, text=True
    )
    assert completed.returncode == 0, (
        f"the fixture's own `git {' '.join(arguments)}` failed, so the assertions below "
        f"would be made against a tree nobody built:\n{completed.stderr}"
    )


def _repository(root: Path, *, document: str) -> Path:
    """A real checkout whose index holds one migration, one body, and one document."""
    revision: dict[str, Any] = {
        "op": "upsertRevision",
        "itemId": _ITEM,
        "revisionId": "01MB4V3XKQ7ZPYE8R2NGT5HW6B",
        "contentSha256": hashlib.sha256(_SNAPSHOT.encode("utf-8")).hexdigest(),
        "metadata": {
            "title": "ADR-0001: Example",
            "status": "approved",
            "sourceAnchors": [
                {
                    "provider": "git",
                    "sourceUri": "https://github.com/theurian/theurian.git",
                    "commitSha": "2a98d4c8963cdf46cc6169e43ac7add039745342",
                    "filePath": _DOCUMENT,
                }
            ],
        },
    }
    for relative, text in (
        (
            _MIGRATION,
            yaml.safe_dump(
                {
                    "apiVersion": "theurian.dev/v1",
                    "id": "01MB4V3XKQ7ZPYE8R2NGT5HW6A",
                    "operations": [
                        {"op": "createItem", "itemId": _ITEM, "kind": "architecture"},
                        revision,
                    ],
                },
                sort_keys=False,
            ),
        ),
        (_DOCUMENT, document),
    ):
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")

    _git("init", "-q", str(root))
    _git("-C", str(root), "add", _MIGRATION, _DOCUMENT)
    return root


def test_a_clean_tree_exits_zero(tmp_path: Path) -> None:
    """The whole path -- git index, YAML, digest, exit status -- over a healthy corpus.

    Without it, every assertion below is satisfied by a CLI that never returns 0.
    """
    root = _repository(tmp_path / "clean", document=_SNAPSHOT)

    assert corpus_drift.main(["--repo-root", str(root)]) == 0


def test_a_bare_run_over_a_drifted_corpus_exits_one(tmp_path: Path) -> None:
    """What a maintainer gets locally: a non-zero status they can chain on."""
    root = _repository(tmp_path / "drifted", document=_EDITED)

    assert corpus_drift.main(["--repo-root", str(root)]) == 1


def test_advisory_reports_the_same_drift_and_still_exits_zero(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The advisory job must stay visible: exit 0, and the finding printed anyway.

    A downgrade that also silenced the report would turn the CI job into a
    no-op, which is the failure mode `--advisory` is one character away from.
    """
    root = _repository(tmp_path / "drifted", document=_EDITED)

    status = corpus_drift.main(["--repo-root", str(root), "--advisory"])

    assert status == 0
    printed = capsys.readouterr().out
    assert _DOCUMENT in printed
    assert corpus_drift.REMEDY in printed


def test_advisory_does_not_rescue_a_tree_the_checker_could_not_read(tmp_path: Path) -> None:
    """The load-bearing case, through the flag path the CI job actually passes.

    `--advisory` downgrades drift and nothing else. A directory git cannot
    answer for -- no repository, a container without git, a broken checkout --
    means this run compared nothing, and the workflow has no other guard: it
    carries neither `continue-on-error` nor `|| true`, precisely so that exit 2
    reaches the build.
    """
    root = tmp_path / "not-a-repository"
    root.mkdir()

    assert corpus_drift.main(["--repo-root", str(root), "--advisory"]) == 2


def test_an_empty_corpus_is_exit_two_under_advisory_too(tmp_path: Path) -> None:
    """A real repository, a real index, and nothing under `.theurian/migrations/`.

    The other route to "compared nothing", and the one a bad merge produces.
    """
    root = tmp_path / "empty"
    root.mkdir()
    (root / "README.md").write_text("# Empty\n", encoding="utf-8")
    _git("init", "-q", str(root))
    _git("-C", str(root), "add", "README.md")

    assert corpus_drift.main(["--repo-root", str(root), "--advisory"]) == 2


def test_the_github_format_adds_the_workflow_commands_the_text_format_omits(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """`::warning` annotations are how an advisory finding reaches the Files view.

    The default `text` format must not emit them -- a local run would print
    workflow syntax at a human -- so both directions are asserted from one
    corpus.
    """
    root = _repository(tmp_path / "drifted", document=_EDITED)

    corpus_drift.main(["--repo-root", str(root), "--advisory"])
    plain = capsys.readouterr().out
    corpus_drift.main(["--repo-root", str(root), "--advisory", "--format", "github"])
    annotated = capsys.readouterr().out

    assert "::warning" not in plain
    assert f"::warning file={_DOCUMENT},title=Corpus drift::" in annotated


def test_the_step_summary_is_written_only_when_the_run_asks_for_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`--summary` appends to `$GITHUB_STEP_SUMMARY`; without it the file is untouched.

    Appending unasked would put a corpus report into the summary of every job
    that happened to have the variable set.
    """
    root = _repository(tmp_path / "drifted", document=_EDITED)
    summary = tmp_path / "step-summary.md"
    summary.write_text("", encoding="utf-8")
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))

    corpus_drift.main(["--repo-root", str(root), "--advisory"])
    assert summary.read_text(encoding="utf-8") == ""

    corpus_drift.main(["--repo-root", str(root), "--advisory", "--summary"])
    assert "## Dogfood corpus drift" in summary.read_text(encoding="utf-8")
    assert f"| `{_ITEM}` | `{_DOCUMENT}` | drifted |" in summary.read_text(encoding="utf-8")


def test_a_migration_that_is_only_on_disk_is_not_in_the_population_git_reports(
    tmp_path: Path,
) -> None:
    """The `git ls-files --cached` key, exercised against a real index (#262).

    The untracked copy here is drifted. A filesystem glob would find it and take
    the run to exit 1 -- noisy on the one machine that dogfoods Theurian, quiet
    in CI, and this is the assertion that keeps the population honest end to end.
    """
    root = _repository(tmp_path / "partly-tracked", document=_SNAPSHOT)
    drifted = root / ".theurian" / "migrations" / "01MB4V3XKQ7ZPYE8R2NGT5HW6C-vault.yaml"
    drifted.write_text(
        (root / _MIGRATION).read_text(encoding="utf-8").replace(_DOCUMENT, "docs/gone.md"),
        encoding="utf-8",
    )

    assert corpus_drift.main(["--repo-root", str(root)]) == 0


# -- the floor, through the flags ---------------------------------------------


def test_a_run_that_compared_less_than_its_floor_exits_two_even_though_it_found_no_drift(
    tmp_path: Path,
) -> None:
    """A healthy-looking clean run is exactly what a shrinking corpus produces.

    One anchor, matching its document, and a stated floor of two: the compared
    count is the only thing wrong, and it is the thing `Status.CLEAN` cannot
    express. Run without the floor this same tree is exit 0 -- which is what
    every run between 26 anchors and 1 used to report.
    """
    root = _repository(tmp_path / "clean", document=_SNAPSHOT)

    assert corpus_drift.main(["--repo-root", str(root)]) == 0
    assert corpus_drift.main(["--repo-root", str(root), "--minimum-compared", "2"]) == 2


def test_the_floor_outranks_drift_so_advisory_cannot_downgrade_a_breach(tmp_path: Path) -> None:
    """The load-bearing case: a finding *and* a green tick is the worst outcome available.

    `--advisory` turns drift into exit 0, deliberately, so that editing an ADR
    does not redden its own pull request. A run that also compared too little has
    two things to say, and the exit status can only carry one of them -- so it
    carries the one no flag downgrades. Were the order reversed, a corpus that
    had lost twenty-five of twenty-six anchors would report its one surviving
    drift as an advisory warning and pass.

    The CI step in `.github/workflows/shared.yml` passes `--advisory` and carries
    neither `continue-on-error` nor `|| true`, so this exit status is what
    reaches the build.
    """
    root = _repository(tmp_path / "drifted", document=_EDITED)

    assert corpus_drift.main(["--repo-root", str(root), "--advisory"]) == 0
    assert (
        corpus_drift.main(["--repo-root", str(root), "--advisory", "--minimum-compared", "2"]) == 2
    )


def test_a_breach_still_prints_the_drift_and_the_remedy_it_found(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Exit 2 says the run cannot be trusted; the report still has to say what it saw.

    A maintainer meeting this in CI needs both halves: which anchors stopped
    being comparable (so they can be restored) and which document drifted (so it
    can be re-seeded). Replacing the report with a bare "compared too little"
    would make the exit status actionable and the output not.
    """
    root = _repository(tmp_path / "drifted", document=_EDITED)

    corpus_drift.main(["--repo-root", str(root), "--advisory", "--minimum-compared", "2"])

    printed = capsys.readouterr().out
    assert "compared 1 anchor(s), fewer than the 2 this corpus is held to" in printed
    assert f"DRIFT  {_ITEM}: {_DOCUMENT} now hashes to" in printed
    assert corpus_drift.REMEDY in printed


def test_a_floor_leaves_a_tree_git_would_not_answer_for_saying_so(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Exit 2 twice over, and the two reasons are not interchangeable in the job log.

    A run that could not establish its population compared zero anchors, so it is
    below every floor above zero and the floor's arithmetic fires on it too --
    with text that offers "restore them, or lower the floor" to a maintainer
    whose actual problem is that git was never asked or never answered. Lowering
    the floor would not make this tree checkable, and there are no anchors to
    restore.

    The status cannot carry the difference (both are 2, and ``--advisory``
    downgrades neither), so the printed diagnosis is the whole of it.
    """
    root = tmp_path / "not-a-repository"
    root.mkdir()

    status = corpus_drift.main(["--repo-root", str(root), "--minimum-compared", "26", "--advisory"])

    printed = capsys.readouterr().out
    assert status == 2
    assert "did not answer" in printed
    assert "no filesystem fallback on purpose" in printed
    assert "fewer than the 26 this corpus is held to" not in printed


def test_a_floor_leaves_a_repository_whose_corpus_is_gone_saying_so(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The other zero-compared route, and the one a bad merge actually produces.

    Here git answers and the index is real; what is missing is
    ``.theurian/migrations/``. "The committed corpus is gone" is a finding a
    maintainer acts on immediately, and the floor's "most of it went unchecked"
    describes a corpus that is still mostly there -- the opposite diagnosis, at
    the same exit status.
    """
    root = tmp_path / "empty"
    root.mkdir()
    (root / "README.md").write_text("# Empty\n", encoding="utf-8")
    _git("init", "-q", str(root))
    _git("-C", str(root), "add", "README.md")

    status = corpus_drift.main(["--repo-root", str(root), "--minimum-compared", "26", "--advisory"])

    printed = capsys.readouterr().out
    assert status == 2
    assert "The committed corpus is gone" in printed
    assert "fewer than the 26 this corpus is held to" not in printed


def test_the_default_floor_binds_the_one_tree_its_number_was_measured_against(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Nothing else drives `main`'s default floor: every other call states one.

    ``main`` defaults ``--repo-root`` to ``corpus_drift.REPO_ROOT`` and then asks
    :func:`corpus_drift.minimum_compared_for` which floor that tree is held to,
    so the default is reachable only from a run that passes *neither* flag.
    Every other call site in this suite passes ``--repo-root <tmp>``, and every
    floor-exercising one passes ``--minimum-compared`` as well -- which left
    ``minimum_compared_for(repo_root, arguments.minimum_compared)`` replaceable
    by ``arguments.minimum_compared or 0`` with the whole suite still green.

    Repointing ``REPO_ROOT`` at a one-anchor repository is what makes the default
    branch fire against a tree small enough to breach it: the constant is 26, the
    corpus here compares 1. The floor's own sentence is asserted rather than the
    status alone, because exit 2 has three other producers and a fixture that
    quietly failed to build would reach one of them.

    The second run pins the same tree as otherwise healthy, so the 2 above is the
    floor and not a corpus this test broke on its way in.
    """
    root = _repository(tmp_path / "the-measured-tree", document=_SNAPSHOT).resolve()
    monkeypatch.setattr(corpus_drift, "REPO_ROOT", root)

    status = corpus_drift.main([])

    printed = capsys.readouterr().out
    assert status == 2
    assert "compared 1 anchor(s), fewer than the 26 this corpus is held to" in printed
    assert corpus_drift.main(["--minimum-compared", "0"]) == 0


@pytest.mark.parametrize("floor", ["0", "1"], ids=["disabled", "met"])
def test_a_floor_the_run_clears_leaves_the_ordinary_exit_codes_alone(
    tmp_path: Path, floor: str
) -> None:
    """Drift is still exit 1: the floor decides whether a verdict is trustworthy, not what it is.

    Without this, "always 2 once `--minimum-compared` is passed" satisfies the
    three tests above, and the flag becomes a way to fail the build rather than a
    way to state what a tree can meet.

    What the `0` case does *not* pin, stated so nobody reads it as covered: that
    an explicit zero beats a non-zero default. It cannot be pinned from here,
    because the default for any tree that is not this repository's own is already
    zero, so the two answers agree whatever `minimum_compared_for` does with a
    falsy request. `tests/unit/tools/test_corpus_drift_floor.py` holds that one
    against `REPO_ROOT`, the only tree where the two can disagree, as
    `test_a_requested_floor_of_zero_turns_the_floor_off_rather_than_reading_as_absent`.
    """
    root = _repository(tmp_path / "drifted", document=_EDITED)

    assert corpus_drift.main(["--repo-root", str(root), "--minimum-compared", floor]) == 1
