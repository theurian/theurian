"""Every ``theurian`` subcommand an instruction names must be one the CLI registers.

The defect class, stated once: **a user-facing string names a ``theurian``
subcommand that is not registered.** A reader -- a person or an agent -- runs
what the string says and gets ``No such command`` and exit 2. Nothing in the
repository joined the prose to the command table, so each face was found by
someone running it.

Three faces are on record. ``/theurian:upgrade`` and Core's compatibility remedy
both told users to run ``theurian upgrade``, which has never existed (#42).
``/theurian:propose`` shelled out to ``theurian propose`` and offered
``theurian propose accept``; ``/theurian:reindex`` shelled out to
``theurian index rebuild``; ``docs/integrations/claude-code.md`` mapped both
slash commands to the same dead invocations (#89). #42's fix pinned one document
by literal, so the two in #89 shipped anyway. This file is the closure that
argument asked for: one mechanism over the instructional surfaces named below,
resolved against the app the CLI actually runs, so a command added or removed
tomorrow is covered without anyone remembering to add a test.

The population
--------------
Four readers over four file families, walked from the repository root:

=================================  ==========================================
``*.md`` anywhere                  fenced blocks, inline code spans, YAML
                                   frontmatter values, and any line whose
                                   own content is a command
``*.py`` under Core's ``src/``     ``#`` comment runs, and string literals with
                                   f-strings and implicit concatenation resolved
``*.json`` anywhere                code spans inside string literals
``*.sh`` / ``*.yml`` / ``*.yaml``  every logical line
=================================  ==========================================

The first version of this file read three roots -- ``plugins/**/*.md``,
``docs/**/*.md`` and Core's ``src/**/*.py`` -- while its docstring claimed
"every instructional surface".

**Measured at 27687e0, the commit that shipped those three roots: 193 sites
lived outside them.** Everything in this passage is that one dated measurement
and is deliberately not maintained -- the live claim is the class test below,
which fails on a real defect rather than on a number drifting. Numbers here rot
for reasons that are not defects: the merge that brought this branch up to date
added one ``theurian ingest`` line to Core's CHANGELOG, and this file's own new
test modules added sites of their own.

Reproduce it with ``git archive 27687e0 | tar -x`` into an empty directory and
this module's predicate over the result. The key, because a count means nothing
without one: occurrences of ``theurian`` followed by a lowercase word, in
command position, by a raw line scan, in files those three roots did not open,
``examples/`` included and both test trees excluded. The same archive gives 368
with the test trees, 187 without ``examples/``, and 177 counting lines rather
than occurrences -- so the number is only checkable beside the sentence above.

Where they were, in that tree, in full: Core's ``CHANGELOG.md`` (117), the
README quickstart (19), ``SECURITY.md`` (9), ``CLAUDE.md`` (5), the sample
project's README (5) and its ``config.yaml`` (1), the seven JSON schemas'
remedy strings (19), ``CONTRIBUTING.md`` (3), the bug-report issue template
(3), the packaging READMEs (4), ``schemas/README.md`` (1), three release and CI
workflows (``core.yml``, ``release-core.yml``, ``shared.yml``, 4), and the
plugin's two shell scripts (3) -- which do not instruct anybody, they
*execute*. Twenty-four files, six file types.

What is deliberately unread is listed in :data:`UNREAD`, and
:func:`test_no_file_that_names_a_command_escapes_the_scan` walks every file in
the repository to prove the list is complete: a surface added tomorrow in a file
type nothing here reads fails that test rather than escaping quietly.

The population is what the repository *ships*, which is not the same as what is
on disk, so it is git's answer and not a walk's: ``git ls-files --cached
--others --exclude-standard``, see :func:`_population`. Two measured failures
say why. A suite run under the mutation harness leaves twelve thousand fixture
files inside the tree, and reading them turned the unmutated control RED. A
machine that dogfoods Theurian keeps knowledge under ``.theurian/`` that
``.git/info/exclude`` hides from every clone, and reading *that* failed the
class test below on a note quoting ``theurian upgrade`` (#262) -- on a working
tree ``git status`` reports as clean.

**The population key is the command word, not the flag.** These tests resolve
the first word after ``theurian`` -- and, for a registered group, the second --
against :func:`typer.main.get_command`. A flag is deliberately out of scope:
``--dry-run`` on a command that does not accept it is the same shape of defect
and is tracked as #193, and folding it in here would make one failure mean two
things. A reader who wants to attack this test should attack that key first.

The authority is the app object, never a list written here. A hardcoded set is
the thing that goes stale, and it is what made #42's fix incomplete.
"""

from __future__ import annotations

import pathlib
import shutil
import subprocess
from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Final

import pytest
from command_extraction import (
    _CODE_SPAN,
    _COMMENT_MARKER,
    _FENCE,
    _INVOCATION,
    _SUBCOMMAND,
    REGISTERED,
    Span,
    _at_command_position,
    _comment_blocks,
    _flatten_blockquotes,
    _literal_blocks,
    _strip_markup,
    _unquote_line,
    _unwrap,
    fenced_lines,
    inline_spans,
    json_command_lines,
    plain_command_lines,
)
from command_population import (
    REPO_ROOT,
    SCANNED_SURFACES,
    Invocation,
    _files,
    _is_unread,
    _population,
    _scan,
    _text,
    _walked,
)

# -- what is knowingly left ------------------------------------------------


@dataclass(frozen=True)
class Exemption:
    """A mention of an unregistered command that is a record, not an instruction.

    ``excused`` is the whole bound, and the reason this is not just a pair of
    strings. Anchored by file and literal alone, an exemption covered every
    occurrence of that literal in that file *including ones written after it was
    granted*: five injections -- among them re-adding #42's own remedy to
    ``domain/compatibility.py``, and a new CHANGELOG entry telling users to run
    ``theurian upgrade`` -- were each swallowed with the whole suite green.

    So the permission names the texts it excuses, once per occurrence. A sixth
    occurrence of a literal excused five times is a new instruction and fails,
    whatever it says; a reworded excusal fails too, which is the point -- the
    question "is this still a record rather than an instruction" is exactly what
    a reviewer should be made to answer again.

    Line numbers stay out of it. A line number is stale the next time anyone
    edits above it, and a stale anchor exempts whatever has moved into its place.
    """

    path: str
    literal: str
    excused: tuple[str, ...]
    reason: str
    reference: str

    @property
    def anchor(self) -> tuple[str, str]:
        return self.path, self.literal


#: Each entry is a text that *describes* a dead command rather than telling a
#: reader to run one, which is why it may stay -- and each has to keep matching,
#: or :func:`test_no_recorded_exception_outlives_the_text_it_excuses` removes it.
KNOWN_UNREGISTERED: Final = (
    Exemption(
        path="docs/security/threat-model.md",
        literal="theurian upgrade",
        excused=(
            "theurian upgrade",
            "theurian upgrade",
            "theurian upgrade --check --json",
            "theurian upgrade",
        ),
        reason="the corrected-entry history quoting the remedy it replaced, and the note "
        "recording that implementing it was the rejected alternative",
        reference="#42",
    ),
    Exemption(
        path="packages/theurian-core/CHANGELOG.md",
        literal="theurian upgrade",
        excused=("theurian upgrade", "theurian upgrade", "theurian upgrade"),
        reason="the 0.1.0.dev1 entry quoting the invocation it removed, and the note "
        "recording that a real implementation was the rejected alternative",
        reference="#42",
    ),
    Exemption(
        path="packages/theurian-core/src/theurian/domain/compatibility.py",
        literal="theurian upgrade",
        excused=(
            "theurian upgrade",
            "theurian upgrade",
            "theurian upgrade",
            "theurian upgrade",
        ),
        reason="two in the `CORE_UPGRADERS` comment and two in `resolve_compatibility`'s "
        "docstring, all recording that upgrade never existed beside the remedy that "
        "replaced it",
        reference="#42",
    ),
    Exemption(
        path="plugins/claude-code/CHANGELOG.md",
        literal="theurian upgrade",
        excused=("theurian upgrade --check --json", "theurian upgrade --json"),
        reason="the 0.1.1 entry quoting the two invocations it removed",
        reference="#42",
    ),
)


def _describe(invocations: Iterable[Invocation]) -> str:
    return "\n".join(
        f"  {found.path}:{found.line}  `{found.literal}`  in: {found.span[:90]}"
        for found in sorted(invocations, key=lambda found: (found.path, found.line))
    )


def _by_anchor(invocations: Iterable[Invocation]) -> Mapping[tuple[str, str], list[Invocation]]:
    grouped: dict[tuple[str, str], list[Invocation]] = {}
    for found in invocations:
        grouped.setdefault(found.anchor, []).append(found)
    return grouped


def unexcused(
    invocations: Iterable[Invocation], exemptions: Iterable[Exemption]
) -> list[Invocation]:
    """The invocations no exemption covers, counted rather than merely matched.

    A :class:`collections.Counter` difference and not a set difference, which is
    the whole of the fix: a set says "this file excuses this literal" and a
    multiset says "this file excuses this literal three times". The repository
    has nothing that violates the bound, so nothing in the real scan exercises
    it -- :func:`test_an_exemption_covers_the_texts_it_lists_and_no_others` is
    what makes it a rule rather than a comment.

    When a text is over-represented, *every* occurrence of it is returned rather
    than the arithmetic difference. An exemption records texts, not positions, so
    nothing here knows which of four identical mentions is the new one -- and a
    failure that picks one anyway sends the reader to a line that was fine.
    """
    entries = list(exemptions)
    permitted = {exemption.anchor: exemption for exemption in entries}
    assert len(permitted) == len(entries), (
        "two Exemptions share a (path, literal) anchor, so one silently replaces "
        "the other and its `excused` texts stop being required: "
        f"{sorted(a for a in permitted if sum(e.anchor == a for e in entries) > 1)}"
    )

    offending: list[Invocation] = []
    for anchor, group in _by_anchor(invocations).items():
        exemption = permitted.get(anchor)
        if exemption is None:
            offending.extend(group)
            continue
        surplus = Counter(found.excerpt for found in group) - Counter(exemption.excused)
        offending.extend(found for found in group if surplus[found.excerpt])
    return offending


def unmatched(
    invocations: Iterable[Invocation], exemptions: Iterable[Exemption]
) -> list[tuple[Exemption, str]]:
    """Every excused text the scan no longer finds, once per missing occurrence."""
    found = _by_anchor(invocations)
    return [
        (exemption, text)
        for exemption in exemptions
        for text, missing in (
            Counter(exemption.excused)
            - Counter(item.excerpt for item in found.get(exemption.anchor, []))
        ).items()
        for _ in range(missing)
    ]


# -- the tests -------------------------------------------------------------


def test_every_theurian_command_a_document_names_is_registered() -> None:
    """The closure #42 asked for and #89 proved was still open.

    #42 fixed ``theurian upgrade`` in two places and pinned one of them by
    literal. Two more faces shipped afterwards in ``/theurian:propose`` and
    ``/theurian:reindex``, because a per-literal pin cannot cover a word nobody
    has thought of. Resolving every literal against the registered set covers
    ``upgrade``, ``propose``, ``index rebuild``, and whatever the next one turns
    out to be.

    An exemption does not silence a file: it excuses the exact texts it lists,
    once each, so a *new* instruction written under one still fails here.
    """
    offending = unexcused(_scan(), KNOWN_UNREGISTERED)

    assert not offending, (
        "These instructions name a `theurian` subcommand the CLI does not "
        "register, so a reader following them gets `No such command` and exit 2 "
        f"(#42, #89):\n{_describe(offending)}\n"
        "Fix the text. If it records a dead command rather than telling anyone to "
        "run one, add an Exemption -- or, if one already covers that file and "
        "literal, add this occurrence's exact quoted text to its `excused` tuple "
        "and say in `reason` why this one is a record too. Where an exemption "
        "already lists that text, every occurrence of it is above and one of them "
        "is new: nothing here records which, so read them all."
    )


#: The exemption the bound is demonstrated against: Core's CHANGELOG names the
#: remedy it removed three times, all with the same text, so a set-based
#: permission cannot tell three from four.
_DEMONSTRATION: Final = Exemption(
    path="packages/theurian-core/CHANGELOG.md",
    literal="theurian upgrade",
    excused=("theurian upgrade", "theurian upgrade", "theurian upgrade"),
    reason="the entry quoting the invocation it removed",
    reference="#42",
)


def _occurrence(line: int, span: str) -> Invocation:
    return Invocation(_DEMONSTRATION.path, line, "upgrade", span)


def test_an_exemption_covers_the_texts_it_lists_and_no_others() -> None:
    """The bound itself, which nothing in the repository exercises.

    The repository is clean, so every occurrence the real scan finds is one an
    exemption already lists -- which means the difference between "this file and
    literal are excused" and "these three texts are excused" is invisible to
    :func:`test_every_theurian_command_a_document_names_is_registered`. It was
    invisible for real: with the old set-based permission, five injected
    instructions were swallowed with the whole suite green, among them a new
    CHANGELOG entry telling users to run ``theurian upgrade`` and #42's own
    removed remedy put back into ``domain/compatibility.py``.

    A CHANGELOG is the worst case and therefore the case pinned here: it is
    append-only, so the next entry lands in the same file the exemption names.
    """
    three = [
        _occurrence(2039, "theurian upgrade"),
        _occurrence(2040, "theurian upgrade"),
        _occurrence(2085, "theurian upgrade"),
    ]

    assert unexcused(three, [_DEMONSTRATION]) == []

    a_fourth = [*three, _occurrence(2500, "theurian upgrade")]
    assert [found.line for found in unexcused(a_fourth, [_DEMONSTRATION])] == [
        2039,
        2040,
        2085,
        2500,
    ], (
        "a fourth occurrence of a text excused three times is a new instruction, "
        "and it has to fail even though the file and the literal are both excused. "
        "All four are named because an exemption records texts and not positions"
    )

    reworded = [*three[:2], _occurrence(2085, "theurian upgrade --check")]
    assert [found.line for found in unexcused(reworded, [_DEMONSTRATION])] == [2085], (
        "an excused text that was rewritten is a text nobody has re-read; it "
        "fails so that somebody does -- and here the rewrite is the only "
        "occurrence of its own text, so it is named alone"
    )

    assert [found.line for found in unexcused(three, [])] == [2039, 2040, 2085]


def test_two_exemptions_cannot_share_one_anchor() -> None:
    """The guard on the permission table, which the table itself never trips.

    :func:`unexcused` keys exemptions by ``(path, literal)``, so a second entry
    for a pair already listed replaces the first and takes its ``excused`` texts
    out of the requirement with it -- silently, and in the direction that grants
    rather than refuses. ``KNOWN_UNREGISTERED`` has no duplicate today, which is
    exactly why deleting the guard left the whole suite green: a check whose
    condition the data never meets is indistinguishable from no check at all.
    """
    twin = Exemption(
        path=_DEMONSTRATION.path,
        literal=_DEMONSTRATION.literal,
        excused=("theurian upgrade",),
        reason="a second entry for a file and literal already listed",
        reference="#42",
    )

    with pytest.raises(AssertionError, match="share a"):
        unexcused([_occurrence(2039, "theurian upgrade")], [_DEMONSTRATION, twin])

    assert unexcused([], [_DEMONSTRATION]) == [], "one entry per anchor stays legal"


def test_the_text_an_exemption_is_matched_on_is_whitespace_normalised() -> None:
    """The matching key must not inherit whatever shape a reader left behind.

    Every reader arm returns single-spaced text today, so this normalisation is
    invisible in the real scan and deleting it survives the whole suite. That is
    exactly why it is pinned here rather than left to be noticed: the day an arm
    stops collapsing a wrap -- or a new arm is added that never did -- every
    recorded permission stops matching at once, and the failure would read as
    "the repository grew six new dead commands" instead of "the key moved".
    """
    wrapped = Invocation(
        path="docs/example.md", line=1, command="upgrade", span="theurian\n  upgrade  --check"
    )

    assert wrapped.excerpt == "theurian upgrade --check"
    assert Invocation("p", 1, "upgrade", "  theurian upgrade  ").excerpt == "theurian upgrade"


def test_an_exemption_that_loses_one_of_its_texts_is_reported() -> None:
    """The other direction: a permission for three that now covers two.

    Checked per text rather than per exemption because the anchor still matches
    -- two occurrences remain -- so an exemption-level check sees nothing wrong
    and leaves a standing permission for an occurrence that no longer exists.
    """
    two = [_occurrence(2039, "theurian upgrade"), _occurrence(2040, "theurian upgrade")]

    assert unmatched(two, [_DEMONSTRATION]) == [(_DEMONSTRATION, "theurian upgrade")]
    assert unmatched([*two, _occurrence(2085, "theurian upgrade")], [_DEMONSTRATION]) == []

    one = [_occurrence(2039, "theurian upgrade")]
    assert unmatched(one, [_DEMONSTRATION]) == [
        (_DEMONSTRATION, "theurian upgrade"),
        (_DEMONSTRATION, "theurian upgrade"),
    ], (
        "two of the three excused mentions are gone, so the report has to say so "
        "twice. One entry per *text* would read as a single stale permission and "
        "leave a standing excuse for an occurrence that no longer exists -- and "
        "the one-missing case above cannot tell the two apart, because one is "
        "all it has to report"
    )

    assert unmatched([], [_DEMONSTRATION]) == [(_DEMONSTRATION, "theurian upgrade")] * 3


def test_no_recorded_exception_outlives_the_text_it_excuses() -> None:
    """An exemption that stops matching is a permission nobody revoked.

    It also fails when the extractor stops finding anything at all, which is the
    way this file goes quietly useless: a reworded fence, a Typer release that
    changes command introspection, a moved directory. Every exemption going
    unused at once says the scan reached nothing, not that the repository got
    six fixes in one commit.

    Checked per excused text rather than per exemption, so that deleting one of
    the three ``theurian upgrade`` mentions in Core's CHANGELOG is reported here
    instead of leaving a permission for two that covers three.
    """
    stale = unmatched(_scan(), KNOWN_UNREGISTERED)

    assert not stale, (
        "These excused texts no longer appear in the file that names them. Either "
        "the text was fixed -- delete that entry from `excused`, and the whole "
        "Exemption if it is now empty -- or the scan has stopped reading that "
        "file, which would make this whole module pass by finding nothing:\n"
        + "\n".join(
            f"  {exemption.path}: `{text}` ({exemption.reason}, {exemption.reference})"
            for exemption, text in stale
        )
    )


def test_the_fallback_walk_enters_only_what_the_repository_could_ship() -> None:
    """The rule that stands in for git where there is none, pinned as a rule.

    A tree with no ``.git`` in it is not hypothetical: the mutation harness
    copies the checkout without one, and a run there left 12,734 fixture files
    under ``.mutate-tmp/`` -- entire ``.theurian`` project directories with
    their own markdown, JSON and YAML, some of it not UTF-8. The scan read them,
    the unmutated control went RED, and every verdict in that batch with it.

    In a checkout none of this decides anything, because :func:`_population`
    asks git. What the fallback still has to get right is the direction of its
    error: it reads less than the repository ships, never more than the
    repository tracks.

    Pinned as a rule and not as the list of names seen so far, because the names
    keep changing and the rule does not.
    """
    assert _walked(
        [".claude", ".claude-plugin", ".github", ".theurian", "docs"], at_repository_root=False
    ) == [".claude", ".claude-plugin", ".github", ".theurian", "docs"]

    assert _walked([".theurian", "docs"], at_repository_root=True) == ["docs"]

    tool_state = [".mutate-tmp", ".mutate-home", ".venv", ".git", ".pytest_cache"]
    build_output = ["worktrees", "node_modules", "site", "htmlcov", "__pycache__"]
    for at_root in (True, False):
        assert _walked(tool_state, at_repository_root=at_root) == []
        assert _walked(build_output, at_repository_root=at_root) == []


def _require_git() -> str:
    """The git the population is defined by, or a skip that says why."""
    git = shutil.which("git")
    if git is None:
        pytest.skip("the population is defined by `git ls-files`, and this machine has no git")
    return git


def _git(git: str, *arguments: str) -> None:
    """Run one git command in a sandbox and fail loudly if it did not work."""
    completed = subprocess.run(  # noqa: S603 - argv is written here, never user input
        [git, *arguments], capture_output=True, text=True, check=False
    )
    assert completed.returncode == 0, (
        f"the fixture's own `git {' '.join(arguments)}` failed, so the test below would "
        f"be asserting against a tree nobody built:\n{completed.stderr}"
    )


@pytest.fixture
def sandbox(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> pathlib.Path:
    """A repository-shaped tree, cut off from the developer's own git configuration.

    ``GIT_CONFIG_GLOBAL`` and ``GIT_CONFIG_SYSTEM`` name files that do not
    exist, and ``HOME`` moves with them because git reads ``$HOME/.gitconfig``
    when the first is unset: a developer whose global ``core.excludesFile``
    happens to mention ``.theurian`` would otherwise get a different verdict
    here than CI does. ``GIT_CEILING_DIRECTORIES`` stops the *fallback* test
    from finding a repository above ``TMPDIR`` and taking the git path by
    accident, which would make it pass without exercising the fallback at all.

    The environment reaches the code under test because it runs git in a
    subprocess, which inherits it -- and it is also what keeps this test off the
    real ``~/.gitconfig``.
    """
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(home / "absent.gitconfig"))
    monkeypatch.setenv("GIT_CONFIG_SYSTEM", str(home / "absent.gitconfig"))
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path))
    monkeypatch.delenv("GIT_DIR", raising=False)
    monkeypatch.delenv("GIT_WORK_TREE", raising=False)

    root = tmp_path / "checkout"
    root.mkdir()
    return root


def _scanned_in(sandbox: pathlib.Path) -> list[str]:
    """The markdown the population hands the readers, relative to the sandbox."""
    return [
        path.relative_to(sandbox).as_posix()
        for path in _files(sandbox, frozenset({".md"}), repository=sandbox)
    ]


def test_a_git_ignored_document_is_no_part_of_the_population(sandbox: pathlib.Path) -> None:
    """A working tree ``git status`` calls clean must not fail this suite (#262).

    ``.theurian/`` is where a project keeps its own knowledge, so a machine that
    dogfoods Theurian keeps knowledge there that is deliberately never committed
    -- 56 bodies on the checkout that reported #262, excluded through
    ``.git/info/exclude``. One was a historical handoff note quoting
    ``theurian upgrade``, and because the population was defined by directory
    name, ``test_every_theurian_command_a_document_names_is_registered`` failed
    on a file no clone will ever hold. No exemption could have covered it: the
    path carries a ULID that exists on one machine.

    Ignored through ``.git/info/exclude`` rather than ``.gitignore`` deliberately
    -- that is the file #262's corpus used, it is never committed, and an
    implementation that parsed ignore rules itself would have to reproduce the
    whole chain to pass this.

    Asserted as the whole list rather than as an absence, because an enumeration
    that returned nothing at all would satisfy ``ignored not in scanned`` while
    making every other assertion in this module pass by reading no files.
    """
    git = _require_git()
    _git(git, "init", "-q", str(sandbox))
    knowledge = sandbox / ".theurian" / "knowledge"
    knowledge.mkdir(parents=True)
    (knowledge / "committed.md").write_text("run `theurian upgrade`\n", encoding="utf-8")
    (knowledge / "local-only.md").write_text("quoting `theurian upgrade`\n", encoding="utf-8")
    (sandbox / ".git" / "info" / "exclude").write_text(
        ".theurian/knowledge/local-only.md\n", encoding="utf-8"
    )
    _git(git, "-C", str(sandbox), "add", ".theurian/knowledge/committed.md")

    scanned = _scanned_in(sandbox)

    assert scanned == [".theurian/knowledge/committed.md"]


def test_a_document_written_but_not_yet_committed_is_scanned(sandbox: pathlib.Path) -> None:
    """A dead command is cheapest to fix before the commit that ships it.

    The population asks for ``--others --exclude-standard`` as well as
    ``--cached``, and this is the half that costs something to justify: an
    untracked file is not what the repository ships. ``--cached`` alone would
    let a new document name a dead command with the suite green until it was
    committed -- and the point of this module is that nobody has to remember to
    re-run it after ``git add``. The file is still git's answer and not the
    filesystem's: ``--exclude-standard`` is what keeps #262 fixed either way.
    """
    git = _require_git()
    _git(git, "init", "-q", str(sandbox))
    (sandbox / "docs").mkdir()
    (sandbox / "docs" / "draft.md").write_text("run `theurian upgrade`\n", encoding="utf-8")

    scanned = _scanned_in(sandbox)

    assert scanned == ["docs/draft.md"]


def test_a_tree_that_is_not_a_checkout_reads_less_rather_than_more(
    sandbox: pathlib.Path,
) -> None:
    """The population has to answer where there is no git to ask, and under-read there.

    ``tools/mutate.py`` copies the checkout with ``shutil.copytree`` and its
    ``_COPY_IGNORE`` drops ``.git`` on purpose ("the copy is not a repository,
    and the suite has been run without one"), while copying everything else the
    developer's tree carried -- local-only knowledge included. So the fallback
    runs in exactly the environment #262 is about, with no way to tell a shipped
    file from a private one.

    It resolves that by refusing the one directory where a project keeps its own
    state: ``.theurian/`` at the top of the tree, which this repository tracks
    nothing under (``git ls-files .theurian`` is empty, measured 2026-08-19 at
    bd4fb25). A nested one is sample content and is read -- that is
    ``examples/sample-project/.theurian/config.yaml``, which the scan has always
    covered.

    The cost is stated rather than hidden: without git the fallback under-reads,
    and every file it skips is one the real gate still reads, because the gate
    runs in a checkout.
    """
    (sandbox / ".theurian" / "knowledge").mkdir(parents=True)
    (sandbox / ".theurian" / "knowledge" / "local-only.md").write_text(
        "quoting `theurian upgrade`\n", encoding="utf-8"
    )
    (sandbox / "docs").mkdir()
    (sandbox / "docs" / "shipped.md").write_text("run `theurian init`\n", encoding="utf-8")
    nested = sandbox / "examples" / "sample-project" / ".theurian"
    nested.mkdir(parents=True)
    (nested / "notes.md").write_text("run `theurian init`\n", encoding="utf-8")

    scanned = _scanned_in(sandbox)

    assert scanned == ["docs/shipped.md", "examples/sample-project/.theurian/notes.md"]


def test_no_file_that_names_a_command_escapes_the_scan() -> None:
    """The population claim in the docstring, checked instead of asserted.

    The first version of this module read three roots and called itself "one
    mechanism over every instructional surface". The module docstring records
    what was outside them, measured at 27687e0: 24 files of six types --
    markdown, JSON, YAML, a sample project's config, CI workflows, and the
    plugin's two shell scripts, which *execute* theirs. Widening the roots
    fixed that once; this is what keeps it fixed, because the next surface will
    be a file type nobody thought of rather than a directory somebody forgot.

    Deliberately coarser than the readers: it asks only whether some reader opens
    the file, using a raw line scan with no notion of fences or quoting. A file
    it flags is not necessarily a defect -- it is a file whose contents nothing
    here has ever looked at.

    Reads the same population the readers do, and it has to: the question is
    which *repository* files escape, so a file git does not list is not an
    escape but somebody's private note. Asking the filesystem here instead is
    the other half of #262 -- the ignored corpus that failed the scan would have
    been reported by this guard as a file no reader opens, which is just as RED
    and just as wrong.
    """
    scanned = {
        path.relative_to(REPO_ROOT).as_posix()
        for surface in SCANNED_SURFACES
        for path in _files(surface.root, surface.suffixes)
    }

    unseen: list[str] = []
    for path in _population(REPO_ROOT):
        relative = path.relative_to(REPO_ROOT).as_posix()
        if relative in scanned or _is_unread(relative):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        if any(_names_a_command(line) for line in text.splitlines()):
            unseen.append(relative)

    assert not unseen, (
        "These files name a `theurian <command>` and no reader in this module "
        f"opens them:\n  {'\n  '.join(unseen)}\n"
        "Either add the file type to SCANNED_SURFACES, or add its prefix to "
        "UNREAD with the reason a dead command there is harmless."
    )


def _names_a_command(line: str) -> bool:
    """Whether one raw line puts a plausible subcommand after ``theurian``."""
    for match in _INVOCATION.finditer(line):
        if not _at_command_position(line[: match.start()]):
            continue
        words = line[match.end() :].split()
        if words and _SUBCOMMAND.match(_strip_markup(words[0])):
            return True
    return False


#: Real lines from files the scan did not use to read, and prose from files it
#: did. A guard that answers ``False`` to everything reports an empty list and
#: passes, which is the one way this population check goes quietly useless.
_GUARD_ORACLE: Final = (
    pytest.param(
        "          /tmp/verify/bin/theurian version --json", True, id="a-release-workflow"
    ),
    pytest.param("uv run theurian compat check \\", True, id="a-shared-workflow-step"),
    pytest.param(
        "        **Before you paste diagnostics:** `theurian doctor --report --json`",
        True,
        id="an-issue-template",
    ),
    pytest.param("theurian daemon start >/dev/null 2>&1 || \\", True, id="the-sessionstart-hook"),
    pytest.param(
        "  # Off, so a first build is the cheap one. `theurian index build --raptor`",
        True,
        id="the-sample-project-config",
    ),
    pytest.param(
        'jq -e \'.mcpServers.theurian | has("command") | not\' "$template"',
        False,
        id="a-workflow-naming-the-mcp-server-key",
    ),
    pytest.param("participant CLI as theurian CLI", False, id="prose-naming-the-binary"),
    pytest.param("Read theurian-core's CHANGELOG first.", False, id="prose-naming-the-package"),
)


@pytest.mark.parametrize(("line", "expected"), _GUARD_ORACLE)
def test_the_population_guard_recognises_a_command_when_it_sees_one(
    line: str, expected: bool
) -> None:
    """The guard above reports files, so a guard that sees nothing reports nothing.

    Every positive here is a real line from a file the first version of this
    module never opened -- two workflows, an issue template, the SessionStart
    hook, and the sample project's config. They are what the widened population
    was for, so they are what proves the check that keeps it wide can fail.
    """
    assert _names_a_command(line) is expected


def test_the_authority_reports_groups_with_their_verbs() -> None:
    """Without this, the scan passes by mistaking every group for a leaf.

    :func:`_resolve` reads an empty verb set as "a leaf command, whose second
    word is an argument" -- which is true of ``theurian setup --json`` and would
    be catastrophic for ``theurian index rebuild``. Typer 0.27 vendors Click, so
    the obvious ``isinstance(node, click.Group)`` returns ``False`` for every
    group and produces exactly that empty set. Pinned against ``index`` because
    ``index rebuild`` is the face #89 fixed, and it is only a defect at all if
    the group's verbs are visible here.
    """
    assert REGISTERED, "the Typer app registered no commands at all"

    assert REGISTERED.get("index") == frozenset({"build", "gc", "status"}), (
        f"`theurian index` reports verbs {sorted(REGISTERED.get('index') or ())}. If that "
        "set is empty, group introspection has broken and `theurian index rebuild` "
        "now passes this module silently."
    )
    assert REGISTERED["setup"] == frozenset(), "`setup` is a leaf command, not a group"


def test_the_commands_the_defect_class_was_found_through_are_still_absent() -> None:
    """The premise the whole module rests on, kept honest against Milestone 7.

    ``propose`` used to be here. #212 registered it, which is exactly what this
    test was watching for: the ADR-0013 and threat-model exemptions became wrong
    rather than merely stale, and both are gone from
    :data:`KNOWN_UNREGISTERED` above. ``propose`` is now a live group, so
    ``theurian propose accept`` resolves like any other verb and needs no
    permission -- while ``/theurian:propose`` still writes the proposal by hand
    and is a separate correction (#89, #212).

    ``upgrade`` is what is left, and it is the one that has never existed.
    """
    assert "upgrade" not in REGISTERED, (
        "`theurian upgrade` is now registered. The remedy in `domain/compatibility.py` "
        "delegates to `uv tool upgrade` / `pipx upgrade` precisely because it was not (#42)."
    )


def test_flattening_blockquotes_does_not_touch_fenced_content() -> None:
    """The assumption that makes a document-wide strip safe instead of merely cheap.

    A fenced block at quote depth zero may contain a line beginning with ``>`` --
    a shell redirect, a doctest prompt, a quoted diff. Stripping the document
    would silently rewrite it, and the span this module reports would then name
    text nobody wrote. Measured over the repository: no such line exists, so the
    cheap strip is exact. This is what says so the day one is written.
    """
    rewritten: list[str] = []
    for path in _files(REPO_ROOT, frozenset({".md"})):
        text = _text(path)
        for fence in _FENCE.finditer(text):
            first = text.count("\n", 0, fence.start("body")) + 1
            rewritten.extend(
                f"{path.relative_to(REPO_ROOT).as_posix()}:{first + offset}  {line}"
                for offset, line in enumerate(fence.group("body").splitlines())
                if _unquote_line(line) != line
            )

    assert not rewritten, (
        "These lines sit inside a fenced code block and begin with a blockquote "
        "marker, so flattening the document rewrites content that was never a "
        f"blockquote:\n  {'\n  '.join(rewritten)}\n"
        "_flatten_blockquotes now has to become fence-aware."
    )


def test_the_scan_reaches_every_arm_of_every_reader() -> None:
    """The plumbing over the real tree, checked separately from the verdict.

    The fixtures above prove each reader reports what it is given; this proves it
    is given the repository. A glob that stops matching, a fence that stops
    closing, or a ``tokenize`` change would otherwise make every assertion here
    pass by reading nothing at all.

    Two arms are absent from the counts and are held by :data:`_MARKDOWN_FIXTURE`
    instead: the repository has no bare command line and no frontmatter value
    naming a command, so requiring either here would be an assertion that can
    only ever be satisfied by a defect.
    """
    counts = dict.fromkeys(("fenced", "inline", "comment", "literal", "json", "plain"), 0)
    for path in _files(REPO_ROOT, frozenset({".md"})):
        flattened = _flatten_blockquotes(_text(path))
        counts["fenced"] += _count_invocations(fenced_lines(flattened))
        counts["inline"] += _count_invocations(inline_spans(flattened))
    for path in _files(REPO_ROOT / "packages" / "theurian-core" / "src", frozenset({".py"})):
        source = _text(path)
        counts["comment"] += _count_spans(_comment_blocks(source))
        counts["literal"] += _count_spans(_literal_blocks(source))
    for path in _files(REPO_ROOT, frozenset({".json"})):
        counts["json"] += _count_invocations(json_command_lines(_text(path)))
    for path in _files(REPO_ROOT, frozenset({".sh", ".yml", ".yaml"})):
        counts["plain"] += _count_invocations(plain_command_lines(_text(path)))

    assert all(counts.values()), (
        f"a reader arm yielded no `theurian <command>` at all: {counts}. Every "
        "assertion in this module would pass on an empty population."
    )


def _count_invocations(spans: Iterable[Span]) -> int:
    """How many ``theurian <word>`` sites reached the resolver, dead or alive."""
    return sum(
        1
        for span in spans
        for match in _INVOCATION.finditer(span.text)
        if span.prose or _at_command_position(span.text[: match.start()])
    )


def _count_spans(chunks: Iterable[tuple[int, str]]) -> int:
    """The same, for the two Python token readers, before spans are extracted."""
    return _count_invocations(
        Span(line, _unwrap(span.group("body"), _COMMENT_MARKER))
        for line, chunk in chunks
        for span in _CODE_SPAN.finditer(chunk)
    )
