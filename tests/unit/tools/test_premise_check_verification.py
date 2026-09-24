"""Per-kind verification recipes: each fails closed, and each family's own
"not found" exit code is what the module measured, not assumed (AC2).

`git cat-file -e` reports a missing object with exit 128; `git grep` and
`git merge-base --is-ancestor` report "no match" with exit 1. Treating either
family's code as the other's rule maps a genuinely missing citation onto
`ERROR` -- the module docstring's own warning -- so every family below is
driven at its measured not-found code and at one it has no rule for.

AC7 lives here too: `TEST_ROOTS` is what makes an `async def test_...`
citation findable at all (the #718 lesson), and it is pinned against
`pyproject.toml`'s own `testpaths` rather than restated as a literal.

The recipes and their dispatcher live in :mod:`premise_verify` (round 2
LOW-2/out-of-perspective split); this file imports it directly rather than
reaching through `premise_check`'s re-export, which a
`monkeypatch.setattr` on a recipe function would silently miss (the
dispatcher resolves the name in its own module's globals, not the caller's).
"""

from __future__ import annotations

import tomllib
from collections.abc import Sequence

import premise_check
import premise_verify
import pytest

pytestmark = pytest.mark.unit


class _ScriptedRunner:
    """Maps an exact argv tuple to a canned `CommandResult`; anything unscripted fails loudly."""

    def __init__(self, script: dict[tuple[str, ...], premise_verify.CommandResult]) -> None:
        self._script = script
        self.calls: list[tuple[str, ...]] = []

    def __call__(self, argv: Sequence[str]) -> premise_verify.CommandResult:
        key = tuple(argv)
        self.calls.append(key)
        if key not in self._script:
            raise AssertionError(f"unscripted git invocation: {' '.join(key)}")
        return self._script[key]


def _result(returncode: int, stdout: str = "", stderr: str = "") -> premise_verify.CommandResult:
    return premise_verify.CommandResult(returncode=returncode, stdout=stdout, stderr=stderr)


# --------------------------------------------------------------------------
# path -- the cat-file family, not-found is 128
# --------------------------------------------------------------------------


def test_a_tracked_path_is_intact() -> None:
    argv = ("git", "cat-file", "-e", "HEAD:tools/premise_check.py")
    runner = _ScriptedRunner({argv: _result(0)})

    result = premise_verify._verify_path("tools/premise_check.py", runner)

    assert result.status == premise_verify.INTACT


def test_cat_files_measured_not_found_code_128_is_dangling() -> None:
    argv = ("git", "cat-file", "-e", "HEAD:tools/does-not-exist.py")
    runner = _ScriptedRunner(
        {argv: _result(128, stderr="fatal: Path 'does-not-exist.py' does not exist")}
    )

    result = premise_verify._verify_path("tools/does-not-exist.py", runner)

    assert result.status == premise_verify.DANGLING


def test_an_unexpected_exit_code_from_cat_file_is_error_never_intact() -> None:
    argv = ("git", "cat-file", "-e", "HEAD:tools/premise_check.py")
    runner = _ScriptedRunner({argv: _result(129, stderr="fatal: ambiguous argument")})

    result = premise_verify._verify_path("tools/premise_check.py", runner)

    assert result.status == premise_verify.ERROR


# --------------------------------------------------------------------------
# path_line -- short-circuits on its own path, then bounds-checks the line
# --------------------------------------------------------------------------


def test_a_path_line_within_the_files_range_is_intact() -> None:
    cat_argv = ("git", "cat-file", "-e", "HEAD:tools/premise_check.py")
    show_argv = ("git", "show", "HEAD:tools/premise_check.py")
    runner = _ScriptedRunner(
        {cat_argv: _result(0), show_argv: _result(0, stdout="line one\nline two\n")}
    )

    result = premise_verify._verify_path_line("tools/premise_check.py:2", runner)

    assert result.status == premise_verify.INTACT


def test_a_path_line_past_the_files_own_length_is_dangling() -> None:
    cat_argv = ("git", "cat-file", "-e", "HEAD:tools/premise_check.py")
    show_argv = ("git", "show", "HEAD:tools/premise_check.py")
    runner = _ScriptedRunner(
        {cat_argv: _result(0), show_argv: _result(0, stdout="only one line\n")}
    )

    result = premise_verify._verify_path_line("tools/premise_check.py:99", runner)

    assert result.status == premise_verify.DANGLING


def test_a_path_line_whose_own_path_is_missing_never_reaches_git_show() -> None:
    """A missing file has no lines to bounds-check; a second git call here would
    be wasted at best and, against a scripted runner with no second entry,
    reveals itself immediately.
    """
    cat_argv = ("git", "cat-file", "-e", "HEAD:tools/gone.py")
    runner = _ScriptedRunner({cat_argv: _result(128)})

    result = premise_verify._verify_path_line("tools/gone.py:5", runner)

    assert result.status == premise_verify.DANGLING
    assert runner.calls == [cat_argv]


# --------------------------------------------------------------------------
# constant / test_name -- the grep family, not-found is 1, not 128
# --------------------------------------------------------------------------


def test_greps_measured_not_found_code_1_reads_a_constant_miss_as_unknown() -> None:
    """The split AC2 names by number: cat-file's 128 and grep's 1 disagree about
    which exit code means "not found", so a verification recipe built by
    copying the other family's constant would report a real `ERROR` here
    instead. A `constant` miss itself grades UNKNOWN, not DANGLING (round 1
    HIGH-1c decision 2): unlike a `test_name` definition site, a constant can
    live in text this checkout's own grep never reaches, so absence is weak
    evidence rather than a broken citation.
    """
    argv = ("git", "grep", "-wnF", "FETCH_LIMIT", "HEAD")
    runner = _ScriptedRunner({argv: _result(1)})

    result = premise_verify._verify_constant("FETCH_LIMIT", runner)

    assert result.status == premise_verify.UNKNOWN


def test_greps_unexpected_exit_code_is_error_never_intact() -> None:
    argv = ("git", "grep", "-wnF", "FETCH_LIMIT", "HEAD")
    runner = _ScriptedRunner({argv: _result(2, stderr="fatal: bad object HEAD")})

    result = premise_verify._verify_constant("FETCH_LIMIT", runner)

    assert result.status == premise_verify.ERROR


def test_a_defined_test_name_is_intact() -> None:
    pattern = r"(?:async )?def test_thing\("
    argv = ("git", "grep", "-nP", pattern, "HEAD", "--", *premise_verify.TEST_ROOTS)
    runner = _ScriptedRunner({argv: _result(0, stdout="tests/x.py:1:def test_thing():")})

    result = premise_verify._verify_test_name("test_thing", runner)

    assert result.status == premise_verify.INTACT


def test_a_missing_test_name_is_dangling_via_greps_own_exit_code() -> None:
    pattern = r"(?:async )?def test_missing\("
    argv = ("git", "grep", "-nP", pattern, "HEAD", "--", *premise_verify.TEST_ROOTS)
    runner = _ScriptedRunner({argv: _result(1)})

    result = premise_verify._verify_test_name("test_missing", runner)

    assert result.status == premise_verify.DANGLING


# --------------------------------------------------------------------------
# sha -- two git calls, each family keeping its own not-found code
# --------------------------------------------------------------------------

_SHA = "9f2c1ab4d5e6f70819a2b3c4d5e6f7089a1b2c3d"
_COMMIT_ARGV = ("git", "cat-file", "-e", f"{_SHA}^{{commit}}")
_ANCESTOR_ARGV = ("git", "merge-base", "--is-ancestor", _SHA, "HEAD")


def test_a_sha_whose_commit_does_not_exist_is_unknown_not_dangling() -> None:
    """Under this repository's squash-merge-plus-GC workflow, a cited
    feature-branch sha absent from the local object store is a machine-local
    artifact, not a broken citation (round 1 HIGH-1c decision 2) -- the same
    rationale the resolvable-non-ancestor case below already applies one step
    later in the same recipe. `UNKNOWN`, not `DANGLING`.
    """
    runner = _ScriptedRunner({_COMMIT_ARGV: _result(128)})

    result = premise_verify._verify_sha(_SHA, runner)

    assert result.status == premise_verify.UNKNOWN
    assert runner.calls == [_COMMIT_ARGV]  # a commit that isn't there is never checked for ancestry


def test_a_shas_cat_file_step_reports_an_unexpected_exit_code_as_error() -> None:
    runner = _ScriptedRunner({_COMMIT_ARGV: _result(129, stderr="fatal: bad object")})

    result = premise_verify._verify_sha(_SHA, runner)

    assert result.status == premise_verify.ERROR


def test_a_sha_that_is_an_ancestor_of_head_is_intact() -> None:
    runner = _ScriptedRunner({_COMMIT_ARGV: _result(0), _ANCESTOR_ARGV: _result(0)})

    result = premise_verify._verify_sha(_SHA, runner)

    assert result.status == premise_verify.INTACT


def test_a_resolvable_non_ancestor_sha_is_intact_not_dangling() -> None:
    """`merge-base --is-ancestor`'s own "no" answer, exit 1, means the commit
    exists but was not merged into `HEAD` by a fast-forward -- exactly what
    this repository's squash-merge workflow does to every feature-branch SHA
    an issue ever cited. That is expected and weak-as-evidence, not a broken
    citation: only a SHA `cat-file` cannot find at all (above) reads as
    anything other than INTACT, and even that now reads UNKNOWN, not
    DANGLING.
    """
    runner = _ScriptedRunner({_COMMIT_ARGV: _result(0), _ANCESTOR_ARGV: _result(1)})

    result = premise_verify._verify_sha(_SHA, runner)

    assert result.status == premise_verify.INTACT


def test_merge_bases_unexpected_exit_code_is_error() -> None:
    runner = _ScriptedRunner({_COMMIT_ARGV: _result(0), _ANCESTOR_ARGV: _result(2, stderr="fatal")})

    result = premise_verify._verify_sha(_SHA, runner)

    assert result.status == premise_verify.ERROR


# --------------------------------------------------------------------------
# adr -- content-based, not exit-code-based: ls-tree succeeds either way
# --------------------------------------------------------------------------

_ADR_LS_TREE_ARGV = ("git", "ls-tree", "-r", "--name-only", "HEAD", "--", "docs/adr")


def test_an_adr_with_a_matching_file_is_intact() -> None:
    runner = _ScriptedRunner(
        {_ADR_LS_TREE_ARGV: _result(0, stdout="docs/adr/0033-candidates.md\n")}
    )

    result = premise_verify._verify_adr("ADR-0033", runner)

    assert result.status == premise_verify.INTACT


def test_an_adr_with_no_matching_file_is_dangling() -> None:
    runner = _ScriptedRunner({_ADR_LS_TREE_ARGV: _result(0, stdout="docs/adr/0032-other.md\n")})

    result = premise_verify._verify_adr("ADR-0033", runner)

    assert result.status == premise_verify.DANGLING


def test_an_adr_lookup_that_fails_outright_is_error() -> None:
    runner = _ScriptedRunner(
        {_ADR_LS_TREE_ARGV: _result(128, stderr="fatal: not a valid object name HEAD")}
    )

    result = premise_verify._verify_adr("ADR-0033", runner)

    assert result.status == premise_verify.ERROR


# --------------------------------------------------------------------------
# issue_ref -- a snapshot/PR-map lookup, no git call, no DANGLING branch
# (round 2 HIGH-3's PR-provenance grading is pinned separately, in
# test_premise_check_evidence.py and test_premise_check_verdict.py)
# --------------------------------------------------------------------------


def test_an_issue_ref_present_in_the_snapshot_is_intact() -> None:
    result = premise_verify._verify_issue_ref("#2", frozenset({1, 2, 3}), {})

    assert result.status == premise_verify.INTACT


def test_an_issue_ref_absent_from_the_snapshot_and_pr_map_is_unknown_not_dangling() -> None:
    """A closed issue and a typo look identical from inside a snapshot of only
    open issues and known PRs. `UNKNOWN` says "cannot tell"; `DANGLING` would
    assert the reference is broken when it may simply have closed cleanly.
    """
    result = premise_verify._verify_issue_ref("#42", frozenset({1, 2, 3}), {})

    assert result.status == premise_verify.UNKNOWN


# --------------------------------------------------------------------------
# symbol -- a best-effort guess, so a miss is UNKNOWN, never DANGLING
# --------------------------------------------------------------------------


def test_an_unresolvable_symbol_is_unknown_never_dangling() -> None:
    """`_verify_symbol`'s own comment: the pattern is a heuristic guess at a
    definition site, and a miss says as much about the guess as about the
    symbol. A false `DANGLING` here would poison the agent pass's triage with
    a citation that never had a reliable check to begin with.
    """
    argv = ("git", "grep", "-n", "def _verify_something_else", "HEAD")
    runner = _ScriptedRunner({argv: _result(1)})

    result = premise_verify._verify_symbol("_verify_something_else", runner)

    assert result.status == premise_verify.UNKNOWN


def test_a_resolvable_symbol_is_intact() -> None:
    argv = ("git", "grep", "-n", "def _verify_path", "HEAD")
    runner = _ScriptedRunner(
        {argv: _result(0, stdout="tools/premise_verify.py:123:def _verify_path(")}
    )

    result = premise_verify._verify_symbol("_verify_path", runner)

    assert result.status == premise_verify.INTACT


def test_a_symbols_unexpected_exit_code_is_error() -> None:
    argv = ("git", "grep", "-n", "def _verify_path", "HEAD")
    runner = _ScriptedRunner({argv: _result(129, stderr="fatal: bad object HEAD")})

    result = premise_verify._verify_symbol("_verify_path", runner)

    assert result.status == premise_verify.ERROR


def test_a_symbol_ending_in_a_dot_is_unknown_without_a_git_call() -> None:
    """`"tools.".rsplit(".", 1)[-1]` is `""`, and an empty pattern's `def `/
    `class ` grep matches nearly any line -- round 3 LOW-3's false `INTACT`.
    Not a resolvable symbol, so no git call is even made.
    """

    def unreachable_runner(argv: Sequence[str]) -> premise_verify.CommandResult:
        raise AssertionError("an empty last segment names no symbol to grep for")

    result = premise_verify._verify_symbol("tools.", unreachable_runner)

    assert result.status == premise_verify.UNKNOWN


def test_a_dotted_symbols_keyword_is_chosen_from_its_last_segment() -> None:
    """`Citation.token` is an attribute on a class, not a class named `token`.
    Choosing the keyword from the first segment instead would search for
    `class token`, which nothing defines, and turn every dotted attribute
    reference into a false negative.
    """
    argv = ("git", "grep", "-n", "def token", "HEAD")
    runner = _ScriptedRunner(
        {argv: _result(0, stdout="tools/premise_citations.py:78:    token: str")}
    )

    result = premise_verify._verify_symbol("Citation.token", runner)

    assert result.status == premise_verify.INTACT


def test_a_capitalised_last_segment_is_searched_as_a_class() -> None:
    argv = ("git", "grep", "-n", "class Citation", "HEAD")
    runner = _ScriptedRunner(
        {argv: _result(0, stdout="tools/premise_citations.py:73:class Citation:")}
    )

    result = premise_verify._verify_symbol("Citation", runner)

    assert result.status == premise_verify.INTACT


def test_a_trailing_call_parens_is_stripped_before_the_symbol_is_searched() -> None:
    argv = ("git", "grep", "-n", "def extract_citations", "HEAD")
    runner = _ScriptedRunner({argv: _result(0, stdout="match")})

    result = premise_verify._verify_symbol("extract_citations()", runner)

    assert result.status == premise_verify.INTACT
    assert runner.calls == [argv]


# --------------------------------------------------------------------------
# AC7: TEST_ROOTS pinned against pyproject.toml, and the #718 async recall
# --------------------------------------------------------------------------


def test_test_roots_matches_pytests_own_testpaths_in_pyproject() -> None:
    """Pinned against the config pytest itself reads, not restated as a literal.

    `TEST_ROOTS`'s own docstring measured that a bare `tests` pathspec matches
    52 of 317 test files and misses every one under
    `packages/theurian-core/tests` -- a constant copied by hand from that
    measurement could drift the moment `pyproject.toml`'s `testpaths` changes
    and nothing here would notice.
    """
    config = tomllib.loads((premise_check.REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))

    assert set(premise_verify.TEST_ROOTS) == set(
        config["tool"]["pytest"]["ini_options"]["testpaths"]
    )


def _a_committed_async_test_function_name() -> str:
    """One real `async def test_...`, found live rather than hard-coded.

    Hard-coding a name would go stale the day that function is renamed; asking
    `git` for one keeps the fixture anchored to whatever the checkout
    currently holds.
    """
    result = premise_check.run_command(
        (
            "git",
            "grep",
            "-hoP",
            r"(?<=^async def )test_[a-z0-9_]+",
            "HEAD",
            "--",
            "packages/theurian-core/tests",
        )
    )
    names = [line for line in result.stdout.splitlines() if line]
    assert names, "no `async def test_...` is committed under packages/theurian-core/tests"
    return names[0]


def _skip_unless_git_repository() -> None:
    """#788: `mutate.py --prepare-tree` without `--with-git` copies this file
    into a tree with no `.git` at all, where every `git grep`/`git cat-file`
    below fails closed with exit 128 rather than the exit these two tests are
    driving -- a real checkout is what the claim below is about, and there is
    no real checkout to ask.
    """
    if not premise_verify.git_repository_present(premise_check.REPO_ROOT):
        pytest.skip("no git repository in this tree (mutate.py --prepare-tree without --with-git)")


def test_an_async_def_test_under_the_core_package_tree_is_found() -> None:
    """The #718 lesson: every `async def test_...` in this repository lives
    only under `packages/theurian-core/tests` (measured 2026-09-19: none
    under the bare `tests/` root), so a `TEST_ROOTS` missing that entry, or a
    pattern requiring a bare `def`, would report every async test citation
    DANGLING while the function it names is right there. Run against the
    real checkout's own `git`, not a script, because the claim is about what
    `git grep -P` actually matches.
    """
    _skip_unless_git_repository()
    name = _a_committed_async_test_function_name()

    result = premise_verify._verify_test_name(name, premise_check.run_command)

    assert result.status == premise_verify.INTACT


def test_a_test_name_committed_nowhere_is_dangling_via_the_real_seam() -> None:
    """The negative control for the test above: proof that `INTACT` there comes
    from the pattern actually matching, not from `_verify_test_name` returning
    `INTACT` unconditionally.
    """
    _skip_unless_git_repository()
    result = premise_verify._verify_test_name(
        "test_this_name_is_not_defined_anywhere_in_this_repository_zzqx",
        premise_check.run_command,
    )

    assert result.status == premise_verify.DANGLING


# --------------------------------------------------------------------------
# `verify`'s own dispatch and downgrade (round 1 HIGH-1c decision 2): every
# test above calls a `_verify_<kind>` recipe directly, so none of them would
# notice a swapped `case` in `verify`'s `match` statement -- the only entry
# `premise_check._citation_result` actually calls in production.
# --------------------------------------------------------------------------


def test_sha_dispatches_through_verify_to_its_own_recipe_not_a_neighbours_argv() -> None:
    runner = _ScriptedRunner({_COMMIT_ARGV: _result(128)})

    result = premise_verify.verify("sha", _SHA, runner, frozenset(), {})

    assert result.status == premise_verify.UNKNOWN
    assert runner.calls == [_COMMIT_ARGV]


def test_constant_dispatches_through_verify_to_its_own_recipe_not_a_neighbours_argv() -> None:
    argv = ("git", "grep", "-wnF", "FETCH_LIMIT", "HEAD")
    runner = _ScriptedRunner({argv: _result(1)})

    result = premise_verify.verify("constant", "FETCH_LIMIT", runner, frozenset(), {})

    assert result.status == premise_verify.UNKNOWN
    assert runner.calls == [argv]


def test_a_kind_outside_the_dangling_allowed_set_is_downgraded_from_dangling_to_unknown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`_DANGLING_ALLOWED_KINDS` is `{path, path_line, adr, test_name}`; no
    shipped recipe for a kind outside that set actually returns `DANGLING`
    today (decision 2 moved `sha`/`constant`/`symbol` off it), so this drives
    `verify`'s own downgrade line directly by forcing `_verify_symbol` -- a
    kind outside the set -- to return one anyway. A future citation kind
    added without its own not-found recipe fails closed the same way.
    """
    assert "symbol" not in premise_verify._DANGLING_ALLOWED_KINDS

    def fake_verify_symbol(
        token: str, runner: premise_verify.Runner
    ) -> premise_verify.VerifyResult:
        return premise_verify.VerifyResult(
            "git grep -n whatever HEAD", "exit 1: no match", "", premise_verify.DANGLING
        )

    monkeypatch.setattr(premise_verify, "_verify_symbol", fake_verify_symbol)

    def unreachable_runner(argv: Sequence[str]) -> premise_verify.CommandResult:
        raise AssertionError("the patched recipe should short-circuit before any git call")

    result = premise_verify.verify("symbol", "whatever", unreachable_runner, frozenset(), {})

    assert result.status == premise_verify.UNKNOWN
