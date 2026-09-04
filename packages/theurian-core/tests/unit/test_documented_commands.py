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

A second class, over the same surface (#525/#329)
-------------------------------------------------
**A user-facing string states an exit code the command does not select.** The
same shape as the first -- prose about the CLI, checked against the source
rather than against a list kept here -- and it lives beside it because it reads
the same shipped-file population through :data:`command_population.REPO_ROOT`.
It is the narrower of the two: the first ranges over every instructional surface
in the repository, this one over one document, and the last section of this file
says exactly what that leaves unread.

The population
--------------
Four readers over four file families, walked from the repository root:

=================================  ==========================================
``*.md`` anywhere                  fenced blocks, inline code spans, YAML
                                   frontmatter values, and any line whose
                                   own content is a command
``*.py`` under Core's ``src/``,    ``#`` comment runs, and string literals with
``examples/`` or ``tools/``        f-strings and implicit concatenation resolved
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
on disk, so it is git's answer and not a walk's: ``git ls-files --cached``, the
index and nothing else. ``command_population``'s own docstring holds why
``--others`` is out -- the product writes untracked files under ``.theurian/``
that a gate must not fail on. Two measured failures say why the walk went. A
suite run under the mutation harness leaves twelve thousand fixture files
inside the tree, and reading them turned the unmutated control RED. A
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

import ast
import importlib
import inspect
import pathlib
import re
from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from types import ModuleType
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
    population = _population(REPO_ROOT)

    # The guard's predicate is pinned by _GUARD_ORACLE; its *input* is pinned
    # here, and it was not: emptying this tuple passed the whole suite, because
    # a guard that is handed nothing reports nothing and reporting nothing is
    # what passing looks like. The repository tracked 398 files at bd4fb25.
    #
    # What this floor catches is an emptied or near-emptied population -- a
    # source that stopped answering, a manifest read as nothing. It does *not*
    # catch a degraded one: the name-based walk on the merged corpus branch
    # drops 78 of 321 scanned files and still hands over more than 400, so it
    # clears this line comfortably. That case is caught where it is visible --
    # by the RuntimeWarning `_git_output` raises under `filterwarnings = error`,
    # and by the manifest tests in test_command_population.

    assert len(population) > 200, (
        f"the guard below was handed {len(population)} files. It reports what no "
        "reader opens, so a population this small makes it pass by having "
        "nothing to look at."
    )
    assert REPO_ROOT / "README.md" in population

    unseen: list[str] = []
    for path in population:
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


# -- the exit codes a command document enumerates ---------------------------
#
# The second class this module holds (see its docstring): a user-facing string
# states an exit code the command does not select. Its recorded face is
# `plugins/claude-code/commands/index.md`, which told an agent `theurian index
# build` "has two non-zero exits" and that "Only exit 1 means nothing was
# published". Both were false, and had been since #233 gave the build a second
# route to `EXIT_STATE_ERROR` -- so an agent that met exit 4 was reading a
# document which said that could not happen. #525 widened the gap (the
# unwritable-pointer refusal), and the repair and this pin landed together, which
# is the condition #329 states for moving a code a plugin reads.
#
# **Nothing pinned any plugin document's exit-code prose before this.** The
# reach is one document and one command, stated here rather than implied:
# `reindex.md` and `propose.md` name exit codes too and are *not* pinned, because
# each speaks about several commands at once and the union of their codes is not
# a set any single walk derives. Widening the pin to them needs a per-command key
# they do not currently carry.


#: The document under this pin, repository-relative.
INDEX_COMMAND_DOCUMENT: Final = "plugins/claude-code/commands/index.md"

#: Where the walk starts. `index.md` is a document about `theurian index build`,
#: so the derivation is rooted at that command's function rather than at its
#: module: `index_commands.py` also registers `index gc` and `index status`, and
#: a module-wide union would demand this document enumerate a code that belongs
#: to a command it never runs.
INDEX_BUILD_MODULE: Final = "theurian.cli.index_commands"
INDEX_BUILD_FUNCTION: Final = "index_build"

#: The two call shapes that end a Theurian command with a chosen code. `_fail`
#: writes the `{error, remedy}` envelope and raises; `typer.Exit` is the bare
#: form the secret-scan verdict uses, because that path has a payload to publish
#: on stdout and must not replace it with an envelope.
#:
#: Anything else that could end the process is deliberately outside the key, and
#: :func:`test_the_index_build_walk_sees_every_way_that_module_ends_the_process`
#: is what stops that being a silent hole rather than a stated bound.
_EXIT_CALLS: Final = frozenset({"_fail", "Exit"})

#: Other ways a module could end the process, none of which `index_commands.py`
#: uses. Held as an absence so the key above cannot quietly go incomplete.
_UNREAD_EXIT_CONSTRUCTS: Final = ("sys.exit", "os._exit", "SystemExit")

#: `exit 4`, `Exit 6`, `exits 1` -- every place prose names a code by number.
_A_NAMED_EXIT: Final = re.compile(r"\bexits?\s+(\d+)\b", re.IGNORECASE)

#: The enumerating sentence: "selects three non-zero exits — 1, 4 and 6 —".
#:
#: The count and the list are captured separately on purpose. "has two non-zero
#: exits" beside a list of three is the exact shape that shipped, and a pattern
#: that read only the list would have called it correct.
#: The dashes are written as escapes rather than as themselves: an em dash and an
#: en dash are indistinguishable in a diff, and this pattern has to accept either
#: because the document's typography is not the claim being pinned.
_A_DASH: Final = r"[\u2014\u2013-]"

_THE_ENUMERATION: Final = re.compile(
    rf"selects\s+(?P<count>[a-z]+|\d+)\s+non-zero\s+exits?\s*{_A_DASH}\s*"
    rf"(?P<codes>[^\u2014\u2013]+?)\s*{_A_DASH}"
)

#: Number words an enumeration may spell out. A count outside this map is not
#: silently accepted -- :func:`enumerated_exits` returns ``None`` for it and the
#: test reports the sentence rather than passing over it.
_COUNT_WORDS: Final = {
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
}

#: The sentence #525 replaced, kept as a literal so its return is reported as
#: itself rather than only through the set equality above it. It was false from
#: #233 onward: exit 4 also means nothing was published.
_THE_SUPERSEDED_CLAIM: Final = "Only exit 1 means nothing was published"


@dataclass(frozen=True)
class SelectedExits:
    """The codes a command selects, and the names the walk could not resolve.

    ``unresolved`` is not an error the walk swallows. A constant that has been
    renamed resolves to nothing, the set silently shrinks, and the document then
    looks like it over-enumerates -- a failure pointing at the wrong file. Naming
    them separately makes the walk report its own blindness.
    """

    codes: frozenset[int]
    unresolved: tuple[str, ...]


def _module_tree(module_name: str) -> tuple[ast.Module, ModuleType]:
    module = importlib.import_module(module_name)
    source = inspect.getsourcefile(module)
    assert source is not None, f"{module_name} is not importable from source"
    return ast.parse(pathlib.Path(source).read_text(encoding="utf-8")), module


def _module_functions(tree: ast.Module) -> Mapping[str, ast.FunctionDef]:
    return {node.name: node for node in tree.body if isinstance(node, ast.FunctionDef)}


def _reached_from(functions: Mapping[str, ast.FunctionDef], root: str) -> frozenset[str]:
    """``root`` and every module-level function it can reach, transitively.

    A call graph over plain ``Name`` calls, which is what this module's helpers
    are: `index_build` calls `_run_build`, which calls `_refuse_if_empty`, and
    the exit code the reader meets is chosen three frames down. Rooting the walk
    at the command and closing over its own module is what makes the derived set
    "what `theurian index build` selects" rather than "what this file contains".
    """
    reached = {root}
    pending = [root]
    while pending:
        for node in ast.walk(functions[pending.pop()]):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
                continue
            name = node.func.id
            if name in functions and name not in reached:
                reached.add(name)
                pending.append(name)
    return frozenset(reached)


def _exit_code_expressions(node: ast.AST) -> frozenset[str]:
    """Every expression handed to one of :data:`_EXIT_CALLS` as its code."""
    found: set[str] = set()
    for call in ast.walk(node):
        if not isinstance(call, ast.Call):
            continue
        target = call.func
        name = target.attr if isinstance(target, ast.Attribute) else getattr(target, "id", None)
        if name not in _EXIT_CALLS:
            continue
        arguments = [keyword.value for keyword in call.keywords if keyword.arg == "code"]
        if name == "Exit":
            arguments.extend(call.args)
        found.update(ast.unparse(argument) for argument in arguments)
    return frozenset(found)


def _import_origins(tree: ast.Module) -> Mapping[str, str]:
    """Which module each imported name came from, function-local imports included.

    ``EXIT_STATE_ERROR`` reaches `index_build` through an import *inside* the
    function -- `index_commands.py` imports it there to break a cycle -- so it is
    not an attribute of the module object and ``getattr`` alone cannot resolve
    it. Walking every ``ImportFrom`` in the tree finds it where it actually is.
    """
    origins: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            for alias in node.names:
                origins[alias.asname or alias.name] = node.module
    return origins


def selected_non_zero_exits(module_name: str, root: str) -> SelectedExits:
    """The non-zero codes ``root`` and its module-local helpers select.

    Zero is dropped rather than never collected: nothing in this module passes
    it, and a rule that collected it would make every document enumerate
    success as an outcome.
    """
    tree, module = _module_tree(module_name)
    functions = _module_functions(tree)
    origins = _import_origins(tree)

    expressions: set[str] = set()
    for name in _reached_from(functions, root):
        expressions |= _exit_code_expressions(functions[name])

    codes: set[int] = set()
    unresolved: list[str] = []
    for expression in sorted(expressions):
        try:
            value = ast.literal_eval(expression)
        except (ValueError, SyntaxError):
            owner = origins.get(expression)
            source = importlib.import_module(owner) if owner else module
            value = getattr(source, expression, None)
        if isinstance(value, int):
            codes.add(value)
        else:
            unresolved.append(expression)
    return SelectedExits(frozenset(code for code in codes if code), tuple(unresolved))


def _document(relative: str) -> str:
    """One shipped document, with its line breaks flattened.

    Flattened because the enumerating sentence wraps, and a pattern that had to
    survive re-wrapping would be a pattern about the formatter rather than about
    the claim.
    """
    return " ".join((REPO_ROOT / relative).read_text(encoding="utf-8").split())


def named_exits(text: str) -> frozenset[int]:
    """Every non-zero code the prose names by number, anywhere in the document."""
    return frozenset(code for code in (int(m) for m in _A_NAMED_EXIT.findall(text)) if code)


def enumerated_exits(text: str) -> tuple[int | None, frozenset[int]]:
    """The count and the codes the enumerating sentence states, or ``(None, ...)``."""
    match = _THE_ENUMERATION.search(text)
    if match is None:
        return None, frozenset()
    spelled = match.group("count").lower()
    count = _COUNT_WORDS.get(spelled, int(spelled) if spelled.isdigit() else None)
    return count, frozenset(int(digits) for digits in re.findall(r"\d+", match.group("codes")))


def test_the_index_command_document_enumerates_exactly_the_exits_the_build_selects() -> None:
    """The fact side of the pin (#525, #329). RED when a fourth code joins.

    ``plugins/claude-code/commands/index.md`` is read by an agent deciding what
    to do with a non-zero exit, so a code it does not name is a branch the agent
    does not have. Set equality both ways, against a derivation rather than a
    list: a code the build gains and the document does not name fails here, and
    so does a code the document names that the build cannot produce.

    The derivation is demonstrably able to move -- the same walk returns
    ``{1, 6}`` on ``origin/main`` and ``{1, 4, 6}`` on this branch, the
    difference being the unwritable-pointer refusal this cluster added.

    **What the walk cannot see, stated rather than implied.** It closes over
    `index_commands.py` only, so a code selected *solely* inside a helper in
    `cli/commands.py` -- `_require_project`, which grades a containment refusal
    ``EXIT_STATE_ERROR`` -- is invisible to it. Exit 4 reaches this command by
    both routes, and the walk sees one of them: delete the in-module site and
    this test would go RED asking the document to drop a code the command still
    produces. The other route is pinned from the other side, by running it --
    ``test_contained_path_envelope.py`` measures ``index build`` exiting 4 over
    six planted artefacts -- so the pair of tests disagreeing is what a reader
    would see, not a quiet hole.
    """
    selected = selected_non_zero_exits(INDEX_BUILD_MODULE, INDEX_BUILD_FUNCTION)
    text = _document(INDEX_COMMAND_DOCUMENT)
    _count, enumerated = enumerated_exits(text)

    assert not selected.unresolved, (
        f"the walk could not resolve {list(selected.unresolved)} to a number, so the "
        f"set it derived is smaller than what {INDEX_BUILD_FUNCTION} really selects"
    )
    assert selected.codes, "the walk found no exit code at all, so this test asserts nothing"
    assert enumerated, (
        f"{INDEX_COMMAND_DOCUMENT} no longer states an enumerating sentence this "
        f"pattern can read, so nothing here is checking its exit-code prose"
    )
    assert enumerated == selected.codes, (
        f"{INDEX_COMMAND_DOCUMENT} enumerates {sorted(enumerated)} and "
        f"`theurian {INDEX_BUILD_FUNCTION.replace('_', ' ')}` selects "
        f"{sorted(selected.codes)}. Named and not selected: "
        f"{sorted(enumerated - selected.codes)}; selected and not named: "
        f"{sorted(selected.codes - enumerated)}"
    )
    assert named_exits(text) == selected.codes, (
        f"the codes {INDEX_COMMAND_DOCUMENT} discusses in prose "
        f"({sorted(named_exits(text))}) and the codes it enumerates in the rule "
        f"({sorted(enumerated)}) have come apart from what the command selects "
        f"({sorted(selected.codes)})"
    )


def test_the_index_command_document_counts_the_exits_it_lists() -> None:
    """The prose side. RED on a drift back to a two-code enumeration.

    The set equality above does not catch the sentence that actually shipped.
    "``theurian index build`` **has two non-zero exits**" sat beside a list, and
    a reader who trusted the count stopped reading after the second entry; an
    agent that branched on "the only other outcome" took the wrong branch on the
    third. So the number the sentence claims is checked against the number of
    codes the sentence itself lists.

    The superseded claim is asserted absent by literal, beside that. It is the
    weaker of the two -- a reworded version of the same falsehood passes it --
    which is why it is one line under a property rather than a test of its own.
    Its value is that the specific sentence #525 removed is reported as itself if
    it returns.
    """
    text = _document(INDEX_COMMAND_DOCUMENT)

    count, enumerated = enumerated_exits(text)

    assert enumerated, f"{INDEX_COMMAND_DOCUMENT} states no enumeration to check"
    assert count is not None, (
        f"{INDEX_COMMAND_DOCUMENT} enumerates {sorted(enumerated)} behind a count "
        f"this test cannot read, so the count and the list are unchecked"
    )
    assert count == len(enumerated), (
        f"{INDEX_COMMAND_DOCUMENT} says the build selects {count} non-zero exits "
        f"and then lists {len(enumerated)} of them: {sorted(enumerated)}"
    )
    assert _THE_SUPERSEDED_CLAIM not in text, (
        f"{INDEX_COMMAND_DOCUMENT} has gone back to {_THE_SUPERSEDED_CLAIM!r}, which "
        f"is false for exit 4 as well and has been since #233"
    )


def test_the_index_build_walk_stops_at_the_command_the_document_is_about() -> None:
    """RED means the derived set has quietly become "every exit in the module".

    The bound that makes the equality above sound. ``index_commands.py`` also
    registers ``index gc``, whose refusals are its own -- ``reindex.md``
    documents two of them -- and a walk that included them would demand
    ``index.md`` enumerate codes for a command it never runs. Asserted with a
    positive control on the exclusion: ``index_gc`` really does select a code, so
    its absence from the closure is a decision rather than an empty set.
    """
    tree, _module = _module_tree(INDEX_BUILD_MODULE)
    functions = _module_functions(tree)
    reached = _reached_from(functions, INDEX_BUILD_FUNCTION)

    assert "index_gc" in functions, "the module no longer registers `index gc` under that name"
    assert _exit_code_expressions(functions["index_gc"]), (
        "`index_gc` selects no exit code, so excluding it from the closure proves nothing"
    )
    assert "index_gc" not in reached, (
        "the walk now reaches `index gc`, so the derived set is no longer what "
        "`index build` selects and `index.md` is being held to another command's codes"
    )
    assert "_run_build" in reached, (
        "the walk no longer reaches `_run_build`, where exit 1 is chosen, so it is "
        "reading the command's own body and nothing below it"
    )


def test_the_index_build_walk_sees_every_way_that_module_ends_the_process() -> None:
    """RED means a code is chosen by a construct :data:`_EXIT_CALLS` does not read.

    The key is two call shapes, and a module that started calling ``sys.exit``
    would select codes the walk cannot see -- the set would shrink, and the
    document would look like it over-enumerates. Held as an absence over the
    source text, so the bound is checked rather than described.
    """
    source = pathlib.Path(inspect.getsourcefile(importlib.import_module(INDEX_BUILD_MODULE)) or "")
    text = source.read_text(encoding="utf-8")

    present = [construct for construct in _UNREAD_EXIT_CONSTRUCTS if construct in text]

    assert not present, (
        f"{INDEX_BUILD_MODULE} now ends the process through {present}, which the "
        f"exit-code walk does not read; the derived set is incomplete until "
        f"`_EXIT_CALLS` covers it"
    )


def test_the_exit_walk_resolves_a_literal_a_local_constant_and_an_imported_one() -> None:
    """RED means the resolver stopped resolving, and every derived set is short.

    Driven by synthetic source, because the shipped module cannot drive it: its
    three expressions all resolve today, so a resolver that had lost an arm would
    look identical to one that never needed it. All three arms are exercised --
    a bare literal, a module-level constant, and a name imported inside a
    function, which is the shape ``EXIT_STATE_ERROR`` really has -- plus the
    negative case, a name that resolves to nothing and must be reported rather
    than dropped.
    """
    synthetic = ast.parse(
        "LOCAL = 7\n"
        "def root() -> None:\n"
        "    from theurian.cli.commands import EXIT_STATE_ERROR\n"
        "    _fail('a', code=1)\n"
        "    _fail('b', code=LOCAL)\n"
        "    _fail('c', code=EXIT_STATE_ERROR)\n"
        "    _fail('d', code=NOT_A_THING)\n"
        "    raise typer.Exit(0)\n"
    )
    functions = _module_functions(synthetic)
    origins = _import_origins(synthetic)

    expressions = _exit_code_expressions(functions["root"])

    assert expressions == {"1", "LOCAL", "EXIT_STATE_ERROR", "NOT_A_THING", "0"}
    assert origins["EXIT_STATE_ERROR"] == "theurian.cli.commands", (
        "a function-local `ImportFrom` is no longer read, so the one constant the "
        "real walk cannot reach by `getattr` would resolve to nothing"
    )
    declaring = importlib.import_module(origins["EXIT_STATE_ERROR"])
    assert declaring.EXIT_STATE_ERROR == 4, (
        "`EXIT_STATE_ERROR` no longer names 4 in the module that declares it"
    )
