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
  handling of exit 2 is the last guard.

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
