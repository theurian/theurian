"""`theurian init` and the marked block it owns inside ``.gitignore`` (#128, SEC-18).

The env file's markers and these are spelled identically, written by different
code, and were broken in the same way: the search was ``str.find`` with no count
of the start markers, so a file holding two of them -- what resolving a merge
conflict by keeping both sides leaves behind -- had every rule between them
swallowed by the rewrite and reported as ``changed: true`` with nothing else
said. A ``.gitignore`` is tracked by Git, so the loss shows in a diff and is
recoverable; that is a mitigation, not the fix, and it is worth nothing to
somebody running `theurian init` in a tree that already has changes in it.

Driven through the real command rather than through ``ensure_gitignore``,
because the refusal reached the user as a Typer traceback with the remedy buried
in it until this milestone: the only ``except`` in ``init_command`` wrapped
``resolve_context``. What the function raises and what the person is shown are
two different behaviours, and only one of them was ever wrong.

In-process, in a throwaway repository, with ``THEURIAN_DATA_DIR`` redirected --
`init` writes into the process's working directory and takes no argument that
says where.
"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from theurian.cli.main import app
from theurian.domain.project import GITIGNORE_BLOCK_END, GITIGNORE_BLOCK_START

pytestmark = pytest.mark.integration

runner = CliRunner()

#: A rule of the user's, distinctive enough that finding it in the file
#: afterwards -- or failing to -- cannot be a coincidence.
USER_RULE = "secrets/sentinel-gitignore-rule-zzzz/\n"


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """A Git repository nobody else is using, with the data directory redirected."""
    root = tmp_path / "demo"
    root.mkdir()
    for args in (
        ["git", "init", "-q", "-b", "main"],
        ["git", "config", "user.email", "test@example.com"],
        ["git", "config", "user.name", "Test"],
    ):
        subprocess.run(args, cwd=root, check=True, capture_output=True)  # noqa: S603

    monkeypatch.setenv("THEURIAN_DATA_DIR", str(tmp_path / "datadir"))
    monkeypatch.chdir(root)
    yield root


def _init_json() -> tuple[int, dict[str, Any]]:
    """Run `theurian init --json`, returning the exit code and the payload.

    Errors go to stderr so that stdout stays a machine channel; a helper that
    merged the two could not tell a refusal from a result.
    """
    result = runner.invoke(app, ["init", "--json"], catch_exceptions=False)
    stream = result.stdout if result.exit_code == 0 else (result.stderr or result.stdout)
    payload: dict[str, Any] = json.loads(stream) if stream.strip() else {}
    return result.exit_code, payload


#: The two states a hand edit or a kept-both-sides merge leaves behind. Rendered
#: with a rule of the user's *between* the markers, which is the position the
#: old search called Theurian's own.
UNRESOLVABLE = {
    "repeated-start": (
        f"*.log\n{GITIGNORE_BLOCK_START}\n{USER_RULE}{GITIGNORE_BLOCK_START}\n"
        f".theurian/state/\n{GITIGNORE_BLOCK_END}\n"
    ),
    "unterminated": f"*.log\n{GITIGNORE_BLOCK_START}\n{USER_RULE}",
}


@pytest.mark.parametrize("shape", sorted(UNRESOLVABLE))
def test_markers_that_do_not_delimit_one_block_leave_the_file_exactly_as_it_was(
    project: Path, shape: str
) -> None:
    """SEC-18. Once the delimiters disagree there is no safe guess, so nothing is written.

    Every way of guessing which rules are Theurian's ends in deleting one that
    is not, and the repeated-start shape is the one that hid: a start marker
    *above* the block is inside the span the search opened, so the rule between
    the two markers was rewritten away while the command reported success.

    Asserted on the bytes rather than on the rule alone -- "the rule is still
    there" is also true of a file that lost the four lines under it.
    """
    gitignore = project / ".gitignore"
    gitignore.write_text(UNRESOLVABLE[shape], encoding="utf-8")
    before = gitignore.read_bytes()

    code, payload = _init_json()

    assert code == 1, shape
    assert gitignore.read_bytes() == before
    assert payload["error"], "and the refusal is reported rather than swallowed"


@pytest.mark.parametrize("shape", sorted(UNRESOLVABLE))
def test_the_refusal_says_what_to_look_for_and_never_quotes_a_rule_back(
    project: Path, shape: str
) -> None:
    """The message is the whole remedy: nothing repairs these markers automatically.

    So it has to carry the file to open, a marker to search for, and the command
    to re-run -- and it must not carry a line out of the file, which a person
    may have written for reasons of their own and which lands in whatever they
    paste the failure into.
    """
    (project / ".gitignore").write_text(UNRESOLVABLE[shape], encoding="utf-8")

    _, payload = _init_json()

    said = payload["error"] + payload["remedy"]
    assert str(project / ".gitignore") in payload["error"], shape
    assert GITIGNORE_BLOCK_START in said or GITIGNORE_BLOCK_END in said
    assert "theurian init" in payload["remedy"], "the remedy is a command, not a description"
    assert "sentinel-gitignore-rule-zzzz" not in said, "and not a rule of theirs"


def test_the_refusal_reaches_a_person_as_an_error_line_and_not_a_traceback(
    project: Path,
) -> None:
    """Nobody runs this with ``--json``. The default output is the one that matters.

    ``ensure_gitignore`` raising was correct all along; the only ``except`` in
    ``init_command`` wrapped ``resolve_context``, so the refusal arrived as a
    Typer traceback with the remedy somewhere in the middle of it. Pinned on the
    rendered stderr, because that is the difference -- the exception is the same
    either way.
    """
    (project / ".gitignore").write_text(UNRESOLVABLE["repeated-start"], encoding="utf-8")

    result = runner.invoke(app, ["init"], catch_exceptions=False)

    assert result.exit_code == 1
    assert result.stderr.startswith("error: ")
    assert "Traceback" not in result.stderr
    assert "`theurian init`" in result.stderr, "with the command to run after the repair"


def test_a_refused_run_leaves_the_directories_it_had_already_created(project: Path) -> None:
    """Stated in the code as a decision, so it is pinned as one rather than assumed.

    ``initialize_project`` runs before the ``.gitignore`` is touched, and what it
    creates is ``.theurian/`` and nothing else -- no state, no credential, no
    registration. Undoing it would mean deleting directories on a failure path
    that has no record of what was there before, and a re-run after the repair
    adds the ignore block to them.
    """
    (project / ".gitignore").write_text(UNRESOLVABLE["repeated-start"], encoding="utf-8")

    code, _ = _init_json()

    assert code == 1
    assert (project / ".theurian/migrations").is_dir()
    assert (project / ".theurian/knowledge/architecture").is_dir()


def test_the_remedy_is_one_a_person_can_actually_carry_out(project: Path) -> None:
    """A remedy is a claim about what happens next, and this checks the claim.

    "Delete the block you do not want -- markers and all -- then re-run" is only
    true if the re-run then succeeds. Performed here the way the sentence
    describes it: the second block goes, the rule between the markers stays, and
    `theurian init` is run again.
    """
    gitignore = project / ".gitignore"
    gitignore.write_text(UNRESOLVABLE["repeated-start"], encoding="utf-8")
    assert _init_json()[0] == 1

    repaired = f"*.log\n{USER_RULE}"
    gitignore.write_text(repaired, encoding="utf-8")
    code, payload = _init_json()

    content = gitignore.read_text(encoding="utf-8")
    assert code == 0, payload
    assert content.count(GITIGNORE_BLOCK_START) == 1
    assert content.startswith(repaired), "and their rules are where they left them"


def test_a_marker_inside_a_line_somebody_wrote_is_not_a_marker(project: Path) -> None:
    """The .gitignore half of the whole-line rule, on the file it applies to.

    A comment that mentions the marker -- a note to a teammate about the block
    below, a line pasted out of this project's own README -- is a line somebody
    wrote. A substring search opens a span at it, finds no end marker after it,
    and refuses to initialise a repository over a comment.
    """
    noted = f"{GITIGNORE_BLOCK_START} (keep this, see the wiki)\n{USER_RULE}"
    (project / ".gitignore").write_text(noted, encoding="utf-8")

    code, payload = _init_json()

    content = (project / ".gitignore").read_text(encoding="utf-8")
    assert code == 0, payload
    assert content.startswith(noted), "their line is kept, and kept first"
    assert content.count(f"{GITIGNORE_BLOCK_START}\n") == 1, "one real block was appended"


def test_a_marker_line_with_a_space_after_it_is_not_a_marker(project: Path) -> None:
    """The env file's whole-line rule again, on the ``.gitignore`` scan beside it.

    ``_gitignore_marker_lines`` strips a trailing ``\\r`` so a CRLF file still
    delimits, and stops there. Widening that to ``rstrip()`` reads like
    tolerance and is the opposite: ``# >>> theurian >>>␠`` and
    ``# <<< theurian <<<␠`` would then delimit a block Theurian did not write --
    every marker it writes is exact -- so the rules between them are somebody
    else's, and the rewrite replaces them with Theurian's own. A trailing space
    is invisible in every editor that would have shown it to them.

    The honest answer is that this file holds no Theurian block: a real one is
    appended, both odd-looking lines stay where they are, and the rule between
    them is still there. Asserted on the rule by name as well as on the prefix,
    so a failure says what went.

    The two scans are deliberately not shared -- separate marker literals, in
    files edited by different code -- which is exactly why this has to be
    pinned on both sides. `tests/unit/test_env_file_merge.py` holds the other.
    """
    gitignore = project / ".gitignore"
    padded = f"{GITIGNORE_BLOCK_START} \n{USER_RULE}{GITIGNORE_BLOCK_END} \n"
    gitignore.write_text(padded, encoding="utf-8")

    code, payload = _init_json()

    content = gitignore.read_text(encoding="utf-8")
    assert code == 0, payload
    assert content.startswith(padded), "no line of theirs is inside a block Theurian owns"
    assert "sentinel-gitignore-rule-zzzz" in content, "the rule between them survived"
    assert content.count(f"{GITIGNORE_BLOCK_START}\n") == 1, "and one real block was appended"


def test_an_end_marker_above_the_block_does_not_become_the_blocks_own_end(
    project: Path,
) -> None:
    """The end is searched for after the start, and the order is the property.

    The same merge that leaves two start markers leaves a lone end marker above
    a real block. Taken as that block's end, the span runs backwards: the block
    stops matching, so it is "stale", and the rewrite that follows duplicates
    everything between the two markers on every run.

    Written as a converged repository plus one stray line, so what is asserted
    is that a second `theurian init` changes nothing (FR-L2).
    """
    gitignore = project / ".gitignore"
    assert _init_json()[0] == 0
    gitignore.write_text(
        f"{GITIGNORE_BLOCK_END}\n{USER_RULE}" + gitignore.read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    before = gitignore.read_bytes()

    code, payload = _init_json()

    assert code == 0, payload
    assert payload["gitignoreUpdated"] is False, "an already-converged file is not rewritten"
    assert gitignore.read_bytes() == before


def test_a_crlf_gitignore_keeps_its_line_endings_through_a_rewrite(project: Path) -> None:
    """A line ending is a byte the user chose, and this file is checked in.

    Read with newline translation on, every ``\\r\\n`` is already ``\\n`` before
    the block is located, and the rewrite hands back a file whose every line has
    changed -- a whole-file diff on the repository's own ``.gitignore``, from a
    command that says it rewrites its own marked block.

    The seeded block has CRLF markers, so it is genuinely not the current block
    and a rewrite really happens; what is asserted is what survives it.
    """
    gitignore = project / ".gitignore"
    keep = b"*.log\r\n"
    stale = f"{GITIGNORE_BLOCK_START}\n.theurian/state/\n{GITIGNORE_BLOCK_END}\n"
    gitignore.write_bytes(keep + stale.replace("\n", "\r\n").encode("utf-8"))

    code, payload = _init_json()

    written = gitignore.read_bytes()
    assert code == 0, payload
    assert written.startswith(keep), "their line ending is theirs"
    assert written.count(GITIGNORE_BLOCK_START.encode("utf-8")) == 1, "the block was replaced"
    assert b"*.log\n" not in written, "and nothing of theirs was translated on the way through"
