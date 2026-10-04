"""``tools/pr_size.py`` counts a branch against a throwaway repository."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from pr_size import main

pytestmark = pytest.mark.unit


def _git(repo: Path, *args: str) -> None:
    subprocess.run(  # noqa: S603 - fixed argv in a throwaway repository
        [  # noqa: S607 - git resolved via PATH
            "git",
            "-C",
            str(repo),
            "-c",
            "user.name=t",
            "-c",
            "user.email=t@t",
            "-c",
            "commit.gpgsign=false",
            *args,
        ],
        check=True,
        capture_output=True,
    )


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-q", "-b", "main")
    (root / "gone.txt").write_text("x\n" * 500)
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "base")
    _git(root, "checkout", "-qb", "topic")
    monkeypatch.chdir(root)
    return root


def _commit(repo: Path, files: dict[str, str]) -> None:
    for name, text in files.items():
        target = repo / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "work")


def _lines(n: int) -> str:
    return "y\n" * n


def _body(repo: Path, text: str) -> str:
    path = repo.parent / "body.md"
    path.write_text(text)
    return str(path)


def test_small_branch_exits_zero_with_summary(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _commit(repo, {"a": _lines(40), "b": _lines(40), "c": _lines(40)})
    assert main(["--base", "main"]) == 0
    assert capsys.readouterr().out.splitlines()[0] == (
        "pr-size: commits=1 files=3 lines=120 (warn >400 lines, limit 1000 lines or 30 files)"
    )


def test_warns_above_400_lines(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _commit(repo, {"a": _lines(401)})
    assert main(["--base", "main"]) == 0
    assert "warning:" in capsys.readouterr().out


def test_over_line_limit_fails(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _commit(repo, {"a": _lines(1001)})
    assert main(["--base", "main"]) == 1
    assert "lines 1001 exceeds" in capsys.readouterr().out


def test_at_line_limit_passes(repo: Path) -> None:
    _commit(repo, {"a": _lines(1000)})
    assert main(["--base", "main"]) == 0


def test_over_file_limit_fails(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _commit(repo, {f"f{i}": "z\n" for i in range(31)})
    assert main(["--base", "main"]) == 1
    assert "files 31 exceeds" in capsys.readouterr().out


def test_at_file_limit_passes(repo: Path) -> None:
    _commit(repo, {f"f{i}": "z\n" for i in range(30)})
    assert main(["--base", "main"]) == 0


def test_waiver_with_reason_passes(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _commit(repo, {"a": _lines(1001)})
    body = _body(repo, "## Summary\nx\n\n## Size waiver\n\nGenerated fixtures.\n")
    assert main(["--base", "main", "--body-file", body]) == 0
    assert "waived: Generated fixtures." in capsys.readouterr().out


@pytest.mark.parametrize("text", ["## Size waiver\n", "## Size waiver\n\n## Next\ntext\n"])
def test_empty_waiver_fails(repo: Path, text: str) -> None:
    _commit(repo, {"a": _lines(1001)})
    assert main(["--base", "main", "--body-file", _body(repo, text)]) == 1


@pytest.mark.parametrize(
    "text",
    [
        "## Size waiver\n<!-- why -->\n",
        "```\n## Size waiver\nreason\n```\n",
        "~~~\n## Size waiver\nreason\n~~~\n",
        "    ## Size waiver\n    reason\n",
        "<!--\n## Size waiver\nreason\n-->\n",
        "## Size waiver\n\u200b\n",
        "## Size waiver\n&nbsp;\n",
    ],
    ids=[
        "comment",
        "backtick-fence",
        "tilde-fence",
        "indented",
        "hidden-heading",
        "zwsp",
        "entity",
    ],
)
def test_waiver_that_renders_empty_fails(repo: Path, text: str) -> None:
    _commit(repo, {"a": _lines(1001)})
    assert main(["--base", "main", "--body-file", _body(repo, text)]) == 1


def test_excluded_paths_do_not_count(repo: Path) -> None:
    _commit(
        repo,
        {
            "uv.lock": _lines(1001),
            ".theurian/m.yaml": _lines(1001),
            "tools/eval/baseline/b.json": _lines(1001),
        },
    )
    assert main(["--base", "main"]) == 0


def test_unexcluded_path_of_same_size_counts(repo: Path) -> None:
    _commit(repo, {"tools/eval/other/b.json": _lines(1001)})
    assert main(["--base", "main"]) == 1


def test_deleted_file_counts_as_one_line(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (repo / "gone.txt").unlink()
    _commit(repo, {})
    assert main(["--base", "main"]) == 0
    assert "files=1 lines=1 " in capsys.readouterr().out


def test_binary_file_counts_as_one_line(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (repo / "bin.dat").write_bytes(b"\0" * 5000)
    _commit(repo, {})
    assert main(["--base", "main"]) == 0
    assert "files=1 lines=1 " in capsys.readouterr().out
