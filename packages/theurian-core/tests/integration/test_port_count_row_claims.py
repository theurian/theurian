"""The port-count census row's own claims, recomputed from this tree (#315).

**Why this file exists.** ``docs/roadmap.md``'s known-defect table row 4 -- the
struck-through claim that the port set is exactly the count ADR-0003 point 5
used to spell out -- is an audit
measurement, and PR #557's docs step (``204f9036``) rewrote three of its
assertions at once: how many lines its recorded population key returns, which
revision of the served ``architecture.ports-and-adapters`` twin is current, and
whether that twin is still un-re-seeded. All three were fresh, all three were
derived, and at ``204f9036`` nothing recomputed any of them -- ``git grep -n
"test_port_count_row" -- packages tests`` returned nothing before this module.
That is the shape row 6 already paid for twice by hand (``f702736``'s 34,
``394c850``'s 35) and that ``test_documented_tool_set.py`` closed for row 6
alone; this module is the same instrument aimed at row 4.

**The three pins, and the direction each fails from.**

- The **prose pin** refuses a reassertion that the twin is un-re-seeded. #557
  re-seeded it on 2026-09-05, so the sentence that said otherwise is now
  history; what it may not do is come back. Applied to every block of the
  roadmap rather than to the located row, because the same claim written three
  sections away is the same defect somewhere the locator does not look -- the
  rule ``test_roadmap_claims.py`` states for the retired T-16 claim.
- The **population pin** re-runs the row's *own* recorded key -- the backticked
  ``git grep`` command the cell publishes -- and holds the row's four figures to
  what it returns. Measured 2026-09-05 at ``204f9036``: 11 lines across 7 files
  under the row's pathspec, 14 across 8 without it. Not a key written here: the
  cell publishes one, and what this checks is that *that* key over *this* tree
  yields *those* numbers. A key spelled here instead would hold the row to an
  arithmetic it never claimed.
- The **current-revision pin** derives the terminal revision of
  ``architecture.ports-and-adapters`` from the tracked migrations and holds the
  row's ``migration X supersedes revision Y with Z`` sentence to all three
  values. It goes RED the next time that twin is re-seeded, which is exactly
  when the row has to move.

**What is deliberately not held.** Not that the row's prose *reasons* correctly
about any of it -- that the superseded revision belongs in the population as
immutable history, that ``docs/architecture/overview.md`` left the population,
that #553 owns the ``McpClientConfig`` join. Those are dispositions, and a
disposition is an argument rather than a measurement. Not the other eleven rows
of that table, and not row 4's `f702736`-era figures (15 against 17, 17 against
20), which are dated snapshots against named commits rather than live claims.
Not the corpus's own integrity: whether the re-seeded body matches its pin is
``test_dogfood_corpus_governance.py``'s, and whether it still matches the
document it snapshots is ``tools/corpus_drift.py``'s.

**Corpus membership (mandatory declaration).** This module walks the repository
twice, so it states which side of the frozen corpus each walk reaches.

- The population walk is ``git grep`` under the row's own pathspec, and
  ``.theurian/knowledge/`` is **IN** -- that is the point of the measurement,
  since two of the eleven lines are the re-seeded twin's body and two more are
  the superseded revision's. The row's stated exclusion,
  ``:!packages/theurian-core/tests/``, is applied because the row applies it,
  not because this module has an opinion about it.
- The migrations walk is ``git ls-files --cached`` over ``*.yaml`` **directly**
  under ``.theurian/migrations/`` -- **tracked only**, the same population key
  ``tools/corpus_drift.py`` records and for the same measured reason: on the
  maintainer's dogfooding machine that directory holds local-only vault notes
  fenced in ``.git/info/exclude``, and a filesystem glob would read them. 48
  tracked migrations, measured 2026-09-05 at ``204f9036``.

**Both walks fail safe.** Git exports ``GIT_INDEX_FILE`` and friends to hooks,
and an inherited override makes git answer for a different index -- but here
that costs matches and migrations, never adds them, so every such run lands on a
count mismatch or an empty revision history and reddens. There is no
environment scrubbing because there is no silent-green direction to scrub.

**Running a command read out of a document is fenced, not trusted.** The row's
key is parsed with :func:`shlex.split` and never a shell, its first two words
must be ``git grep``, and every element that looks like a flag must be in
:data:`_ALLOWED_GREP_FLAGS`. ``git grep -O`` opens matches in a pager of the
caller's choosing; a row edited to carry it would otherwise run it. The fence
refuses rather than executes, and its failure names the element it refused.

**This module is spelled so that it is not itself a member of the population it
measures**, and that is a rule with a measurement behind it rather than a
preference. The row's key matches three phrases; before this constraint was
applied, three lines of this file carried one of them -- the locator constant
and two docstrings -- and the *unexcluded* figure the row publishes went from
14 to 17 the moment this file was tracked (measured 2026-09-05 at ``3e7011b4``,
in a clean clone). The pathspec'd figure never moved, because
``packages/theurian-core/tests/`` is what the row excludes; the wider one is
exactly the count of what the exclusion removes, so an instrument that joins it
falsifies the claim it was written to hold. :data:`_CLAIM_TAIL` is where the
constraint is discharged, and a RED on the wider figure is the first thing to
read as "something in this file rejoined the population".

**Integration, not unit**: two real subprocesses. The roadmap-only prose pin
sits with them because the three pins are one record over one table cell, which
is where ``test_documented_tool_set.py`` already puts row 6's roadmap read
alongside its server-driven siblings. No database, no socket, no temporary
directory, and nothing written anywhere.
"""

from __future__ import annotations

import re
import shlex
import subprocess
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Final, NamedTuple

import pytest
import yaml

pytestmark = pytest.mark.integration

#: ``parents[4]`` is ``.../tests/integration/`` -> ``tests`` -> ``theurian-core``
#: -> ``packages`` -> repo root, the same reckoning
#: ``test_documented_tool_set.py`` uses. Derived from this file's own location
#: and never from ``cwd``, so the module reads the checkout it lives in whether
#: that is a linked worktree under a dot directory or a plain clone (#558).
REPO_ROOT: Final = Path(__file__).resolve().parents[4]

ROADMAP: Final = REPO_ROOT / "docs" / "roadmap.md"

#: The tracked migrations population, as a repository-relative prefix. Matched
#: against ``git ls-files --cached`` output, never against a filesystem walk.
MIGRATIONS_PREFIX: Final = ".theurian/migrations/"

#: The served twin row 4 is about. Written here rather than derived: it is the
#: identity of the thing being measured, and the row is required to name it.
ITEM_ID: Final = "architecture.ports-and-adapters"

#: The last word of the retired claim, held apart from the sentence it ends.
#:
#: Assembled rather than written whole because the row's own population key
#: matches that two-word phrase: spelled out here, this module would be a member
#: of the population it measures and would falsify the row's unexcluded figure by
#: existing (see the module docstring for the measurement). Joining the two
#: halves back into one literal is what a RED on that figure means.
_CLAIM_TAIL: Final = "fourteen"

#: How row 4 is located: a table row carrying the struck-through claim it is the
#: known-defect record *for*. Not its number -- an inserted row moves that -- and
#: not either figure, since a key made of a figure stops matching precisely when
#: that figure drifts, which is the event these pins exist for. The leading pipe
#: is what separates it from ``docs/roadmap.md``'s own Phase-0 bullet, which
#: writes the same claim in lower case and outside any table.
ROW_KEY: Final = f"The port set is exactly these {_CLAIM_TAIL}"

#: A Crockford base32 ULID: 26 characters, no ``I``, ``L``, ``O`` or ``U``.
#: Spelled out rather than written as ranges so the excluded four are visible.
_ULID: Final = r"[0-9ABCDEFGHJKMNPQRSTVWXYZ]{26}"

#: The row's own population key, read back out of the row. Anchored on ``git
#: grep`` inside a code span so the cell's other backticked spans -- commit
#: shas, tool names, ``ALL_PORTS`` -- cannot be mistaken for it.
_ROW_POPULATION_KEY: Final = re.compile(r"`(git grep [^`]*)`")

#: Every ``git grep`` flag this module will execute. A row may re-measure under
#: a different matcher; it may not turn the pin into an arbitrary-command
#: runner. ``-O``/``--open-files-in-pager`` and ``-f <file>`` are the two that
#: make that concrete, and neither is here.
_ALLOWED_GREP_FLAGS: Final = frozenset({"-n", "-i", "-w", "-I", "-E", "-F", "--"})

#: How the row states what its key returns under its own pathspec, and without
#: it. Two patterns rather than one because the row states the second to justify
#: the first -- "the pathspec is part of the key, not a convenience" is an
#: argument that rests on the 14/8 figure, so a stale 14/8 makes the argument
#: wrong. Both are long enough that the row's other numbers cannot satisfy
#: them: the historical measurement beside the first is written "returned 9
#: across 6 at `7293ca9f`", which carries neither "lines" nor "files" and is
#: correctly framed as history against a named commit.
_STATED_UNDER_THE_PATHSPEC: Final = re.compile(r"returns \*\*(\d+) lines across (\d+) files\*\*")
_STATED_WITHOUT_THE_PATHSPEC: Final = re.compile(
    r"without the exclusion the same command returns (\d+) lines across (\d+) files"
)

#: How the row states the re-seed. All three ULIDs are derived below, so this
#: reads the sentence's shape and holds every slot in it rather than the one a
#: reader happens to look at.
_STATED_RESEED: Final = re.compile(
    rf"migration `({_ULID})` supersedes revision `({_ULID})` with `({_ULID})`"
)

#: A Markdown inline link, reduced to its label by :func:`_normalised`. The
#: target is deliberately narrow -- no whitespace, no parenthesis -- so a link
#: this does not recognise is left intact rather than being eaten by a
#: normalisation that reached further than it should. The same pattern
#: ``test_roadmap_claims.py`` uses, for the same reason.
_INLINE_LINK: Final = re.compile(r"\[([^\[\]]*)\]\([^()\s]*\)")

#: The retired assertion, as a shape rather than as its wording: a twin, and
#: ``un-re-seeded``, within one sentence. ``twin`` is the anchor because that is
#: the noun the row uses for the served snapshot, and the window stops at a full
#: stop so it cannot pair a subject with a state from the next sentence.
#:
#: The state is captured as ``state`` because :func:`_correcting_marker`
#: measures a marker's reach from **it**: taking that span out of the pattern is
#: what keeps the two from drifting apart if the anchor is ever reworded.
_UN_RE_SEEDED_CLAIM: Final = re.compile(r"\btwin\b[^.]{0,80}?(?P<state>\bun-re-seeded\b)")

#: What turns a claim naming a twin and ``un-re-seeded`` into a record that the
#: state ended. Any one of them is enough, and each has to sit **outside** the
#: claim's own ``un-re-seeded`` span -- see :func:`_correcting_marker`.
CORRECTION_MARKERS: Final = ("re-seeded", "no longer")

#: How far from the claim's ``un-re-seeded`` a marker may sit, on either side,
#: and still be read as correcting *that* claim. Measured between the two spans
#: rather than between their starts, so the reach does not shrink with the
#: length of the marker.
#:
#: Derived from the shipped sentence rather than chosen: in the
#: :func:`_normalised` roadmap at ``204f9036``, the correction reads ``twin was
#: un-re-seeded; [#557] re-seeded it on 2026-09-05`` and its marker sits **9**
#: characters past the state it corrects, so 20 admits it with margin and a
#: reach of 8 or less would report the shipped roadmap as the defect.
#:
#: Bounded from **above** as well, in
#: :func:`test_the_un_re_seeded_claim_is_caught_when_it_comes_back`: widening
#: this constant is the direction the shipped roadmap cannot detect, since a
#: wider window only ever excuses more.
MARKER_REACH_CHARS: Final = 20

_GIT_TIMEOUT_SECONDS: Final = 60


class _TerminalRevision(NamedTuple):
    """The last ``upsertRevision`` for :data:`ITEM_ID`, and where it came from.

    ``migration_id`` is the carrying migration's own inner ``id``, which is what
    the row names -- not the file name, which merely starts with it.
    """

    migration_id: str
    revision_id: str
    expected_revision: str


# -- Reading the row ---------------------------------------------------------


def _row() -> str:
    """The roadmap's port-count known-defect row, raw, asserted unique.

    A table row is one line, so no block splitting is needed -- but the file is
    scanned for the row's anchor rather than indexed by row number, because a
    row inserted above it would silently move the index while the anchor follows
    the row it names.
    """
    rows = [
        line
        for line in ROADMAP.read_text(encoding="utf-8").splitlines()
        if line.startswith("|") and ROW_KEY in line
    ]

    assert len(rows) == 1, (
        f"the roadmap's port-count row is not findable as exactly one table row keyed "
        f"on `{ROW_KEY}`: found {len(rows)}. Zero means the row was reworded past its "
        f"own anchor and every assertion below would pass over nothing; more than one "
        f"means the figures checked below are not necessarily this row's"
    )
    return rows[0]


def _one(pattern: re.Pattern[str], row: str, *, claim: str) -> tuple[str, ...]:
    """The single match of *pattern* in *row*, failing differently on 0 and many."""
    found = pattern.findall(row)

    assert len(found) == 1, (
        f"the roadmap's port-count row states {claim} {len(found)} times, expected "
        f"once, so this pin cannot say which is the claim: {found}. Zero means the "
        f"sentence was reworded past the shape this module reads, and the figure it "
        f"carried is unpinned again"
    )
    only = found[0]
    return only if isinstance(only, tuple) else (only,)


# -- Running the row's own population key -------------------------------------


def _population_argv(row: str) -> list[str]:
    """The row's recorded key as an argv, fenced to what this module will run.

    Parsed out of the **raw** Markdown, where the alternation is written ``\\|``
    -- both the GFM escape a pipe inside a table cell needs and the BRE
    alternation ``git grep`` needs. Split with :func:`shlex.split` and never a
    shell, so no expansion of any kind happens.
    """
    (command,) = _one(_ROW_POPULATION_KEY, row, claim="its population key")
    argv = shlex.split(command)

    assert argv[:2] == ["git", "grep"], (
        f"the roadmap's port-count row publishes `{command}` as its population key, "
        f"and this pin runs `git grep` and nothing else"
    )
    refused = [
        word for word in argv[2:] if word.startswith("-") and word not in _ALLOWED_GREP_FLAGS
    ]
    assert not refused, (
        f"the row's population key carries {refused}, which is not in this module's "
        f"allowed flag set {sorted(_ALLOWED_GREP_FLAGS)}. A key read out of a document "
        f"and executed is only safe while the flags are enumerated -- `git grep -O` "
        f"runs a pager of the row's choosing. Widen the set deliberately, or re-measure "
        f"the row under a matcher this pin already runs"
    )
    return argv


def _matched(argv: Sequence[str]) -> tuple[int, int]:
    """``(lines, distinct files)`` *argv* reports, run against this checkout.

    Exit 1 is ``git grep``'s "no match" and is a real answer -- zero lines, zero
    files -- while anything above 1 is the command failing, which must not be
    read as an empty population.

    The file count comes from splitting each ``path:lineno:text`` line at its
    first colon, and every path so derived is required to be a file in this
    tree: a path this split got wrong is not one, so a mis-parse reddens here
    instead of quietly deflating the count.
    """
    completed = subprocess.run(  # noqa: S603 - fenced argv, no shell; see _population_argv
        argv,
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        encoding="utf-8",
        timeout=_GIT_TIMEOUT_SECONDS,
    )

    assert completed.returncode in (0, 1), (
        f"`{shlex.join(argv)}` exited {completed.returncode} in {REPO_ROOT}, so it "
        f"reported nothing about the population rather than reporting an empty one: "
        f"{completed.stderr.strip()!r}"
    )
    lines = [line for line in completed.stdout.splitlines() if line]
    files = {line.split(":", 1)[0] for line in lines}

    unresolved = sorted(path for path in files if not (REPO_ROOT / path).is_file())
    assert not unresolved, (
        f"{unresolved} came out of `{shlex.join(argv)}`'s output as file paths and are "
        f"not files here, so the line-to-path split is wrong and the file count below "
        f"is measuring something else"
    )
    return len(lines), len(files)


# -- Deriving the twin's current revision -------------------------------------


def _git(*arguments: str) -> str:
    """``git`` in this checkout, its stdout, refusing anything but a clean exit."""
    completed = subprocess.run(  # noqa: S603 - fixed argv, no shell, no caller input
        ["git", *arguments],  # noqa: S607 - `git` from PATH, as tools/corpus_drift.py does
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        encoding="utf-8",
        timeout=_GIT_TIMEOUT_SECONDS,
    )

    assert completed.returncode == 0, (
        f"`git {' '.join(arguments)}` exited {completed.returncode} in {REPO_ROOT}, so "
        f"the migration population below is unknown rather than empty: "
        f"{completed.stderr.strip()!r}"
    )
    return completed.stdout


def _tracked_migrations() -> dict[str, Any]:
    """Every tracked ``*.yaml`` directly under ``.theurian/migrations/``, parsed.

    ``-z`` rather than newline-separated output: without it git quotes and
    escapes any path holding a non-ASCII byte, and a corpus seeded from
    documents with CJK titles is exactly where such a name appears.
    """
    listing = _git("ls-files", "--cached", "-z").split("\0")
    paths = sorted(
        path
        for path in listing
        if path.startswith(MIGRATIONS_PREFIX)
        and path.endswith(".yaml")
        and "/" not in path.removeprefix(MIGRATIONS_PREFIX)
    )

    assert paths, (
        f"nothing tracked under {MIGRATIONS_PREFIX}, so there is no revision history "
        f"to derive {ITEM_ID}'s current revision from and the row's re-seed sentence "
        f"would be held against nothing"
    )
    return {path: yaml.safe_load((REPO_ROOT / path).read_text(encoding="utf-8")) for path in paths}


def _inner_id(document: Any) -> str:
    """A migration's own ``id`` -- application order's sort key."""
    value = document.get("id") if isinstance(document, dict) else None
    return value if isinstance(value, str) else ""


def _upserts(document: Any) -> list[dict[str, Any]]:
    """The ``upsertRevision`` operations a migration declares, in file order."""
    operations = document.get("operations") if isinstance(document, dict) else None
    if not isinstance(operations, list):
        return []
    return [
        operation
        for operation in operations
        if isinstance(operation, dict) and operation.get("op") == "upsertRevision"
    ]


def _item_history(documents: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    """Every ``upsertRevision`` for :data:`ITEM_ID`, in application order.

    Application order is the inner ``id``-ascending sort, which is what a Kahn
    walk over ``dependsOn`` degenerates to when nothing declares an edge -- the
    rule ``tools/corpus_drift.py`` implements and the only case either it or
    this module handles. The caller checks that no edge is declared before
    trusting the order.
    """
    return [
        (_inner_id(documents[path]), operation)
        for path in sorted(documents, key=lambda path: _inner_id(documents[path]))
        for operation in _upserts(documents[path])
        if operation.get("itemId") == ITEM_ID
    ]


def _terminal_revision(documents: dict[str, Any]) -> _TerminalRevision:
    """The last ``upsertRevision`` for :data:`ITEM_ID` in application order.

    The same rule ``tools/corpus_drift.py`` applies to decide which revision of
    an item a drift check compares: the terminal one, and every earlier one for
    that item is history.
    """
    history = _item_history(documents)

    assert history, (
        f"no tracked migration carries an upsertRevision for {ITEM_ID}, so this tree "
        f"has no current revision for the twin the row describes. Either the item was "
        f"renamed -- in which case `ITEM_ID` moves with it -- or the corpus lost it"
    )
    migration_id, operation = history[-1]
    revision_id = operation.get("revisionId")
    expected = operation.get("expectedRevision")

    assert isinstance(revision_id, str) and revision_id, (
        f"{ITEM_ID}'s terminal upsertRevision in migration {migration_id!r} declares "
        f"revisionId {revision_id!r}, so there is no id for the row to name"
    )
    assert isinstance(expected, str) and expected, (
        f"{ITEM_ID}'s terminal upsertRevision in migration {migration_id!r} declares "
        f"expectedRevision {expected!r}. The row's sentence says which revision the "
        f"re-seed superseded, and a terminal revision that supersedes nothing is the "
        f"seed -- at which point the row is describing a re-seed that is not there"
    )
    return _TerminalRevision(migration_id, revision_id, expected)


# -- The prose rule ----------------------------------------------------------


def _normalised(text: str) -> str:
    """*text* with links reduced to labels, whitespace collapsed and case dropped.

    The roadmap writes the correction with an inline link to #557 between the
    claim and the marker that ends it, so reducing links is what keeps the two
    within reach of each other rather than 45 characters of URL apart.
    """
    return " ".join(_INLINE_LINK.sub(lambda match: f"[{match.group(1)}]", text).lower().split())


def _blocks(document: str) -> list[str]:
    """*document* split on blank lines, each block joined and :func:`_normalised`."""
    blocks: list[list[str]] = [[]]
    for line in document.splitlines():
        if line.strip():
            blocks[-1].append(line)
        else:
            blocks.append([])

    return [flattened for block in blocks if (flattened := _normalised(" ".join(block)))]


def _correcting_marker(text: str, claim: re.Match[str]) -> str | None:
    """The marker recording that *claim*'s state ended, or ``None`` if none reaches it.

    Two conditions, and the first is the one this rule cannot be written
    without: a marker **overlapping** the claim's own ``un-re-seeded`` span does
    not count. ``re-seeded`` is a substring of ``un-re-seeded``, so without that
    clause every sentence asserting the retired claim excuses itself at distance
    zero and the rule can never fail -- measured 2026-09-05, the retired sentence
    is reported clean with the overlap clause removed, and
    :func:`test_a_marker_inside_the_claims_own_word_does_not_excuse_it` drives it.

    The second is reach: what is left has to sit within
    :data:`MARKER_REACH_CHARS` of the state's span, on whichever side it falls.

    There is no sentence split under this. The shipped correction joins its
    claim and its marker with a semicolon, so a boundary there would cut the
    claim away from the record that ends it and report the corrected roadmap as
    the defect; the reach window is what bounds the search instead.
    """
    start, end = claim.span("state")
    for marker in CORRECTION_MARKERS:
        for occurrence in re.finditer(re.escape(marker), text):
            if occurrence.start() < end and occurrence.end() > start:
                continue
            if max(start - occurrence.end(), occurrence.start() - end) <= MARKER_REACH_CHARS:
                return marker
    return None


def _offending_claims(text: str) -> list[str]:
    """Claims in *text* saying a twin is un-re-seeded, with no marker in reach.

    Extracted rather than written inline so the synthetic drivers below run
    *this* predicate. A driver that restated the rule would go RED on its own
    restatement and stay green whatever the shipped rule did.

    Every claim is reported, not the first: one corrected mention does not
    license an uncorrected one beside it.
    """
    return [
        text[max(0, claim.start() - 60) : claim.end() + 60]
        for claim in _UN_RE_SEEDED_CLAIM.finditer(text)
        if _correcting_marker(text, claim) is None
    ]


# -- The pins ----------------------------------------------------------------


def test_no_roadmap_block_says_the_ports_twin_is_still_un_re_seeded() -> None:
    """RED means the roadmap has gone back to a claim #557 falsified.

    Row 4 said *"The served-corpus twin is un-re-seeded"* until ``204f9036``,
    and it was true when it was written: the twin mirrored ADR-0003 as it stood
    at ``2a98d4c`` while #534's branch moved the source. #557 re-seeded it on
    2026-09-05 through propose/accept, so the sentence is now a record of
    something that ended -- and the row keeps saying so, which is what this rule
    allows and what it requires.

    Applied to every block of the roadmap, not to the located row. The same
    claim written three sections away is the same defect, and a row-scoped rule
    would report the document clean while it sat there. Measured 2026-09-05 at
    ``204f9036``: ``un-re-seeded`` appears once in the whole file, and ``twin``
    five times, all of them in this row.
    """
    offenders = {
        index: found
        for index, block in enumerate(_blocks(ROADMAP.read_text(encoding="utf-8")))
        if (found := _offending_claims(block))
    }

    assert not offenders, (
        f"the roadmap says a twin is un-re-seeded with nothing in reach recording "
        f"that the state ended: {offenders}. It stopped being true on 2026-09-05, "
        f"when PR #557 re-seeded `{ITEM_ID}` through propose/accept. If the sentence "
        f"is meant as history, say what ended it -- one of "
        f"{list(CORRECTION_MARKERS)} -- and if a twin really has drifted back out of "
        f"governance, the re-seed sentence in the same cell is what has to move with it"
    )


def test_the_un_re_seeded_claim_is_caught_when_it_comes_back() -> None:
    """RED means the rule above cannot fail, whatever the roadmap says.

    Driven with synthetic text, because the shipped document is compliant and a
    rule measured only against a compliant document is indistinguishable from
    one that answers "nothing is wrong". The first input is the sentence as it
    stood before ``204f9036`` made it false, verbatim.

    The rest is what stops the assertion being satisfiable by a rule that
    refuses every mention of a twin and ``un-re-seeded`` together: both
    **corrected** forms name the pair and are required to come back clean. A
    rule that failed on them would report the shipped roadmap as the defect, and
    the discriminator this module claims to use -- a recorded ending, not a
    tense -- would be fiction.

    The last input pins :data:`MARKER_REACH_CHARS` from **above**, which the
    three before it do not: each of them passes at every reach from 8 to 100000,
    so widening the window is a change none of them can see. That direction is
    not hypothetical -- a wide enough window reaches a marker belonging to some
    other sentence and excuses the claim by proximity alone, which is the same
    failure ``test_roadmap_claims.py``'s
    :func:`test_a_correction_marker_in_another_clause_does_not_excuse_the_claim`
    exists for, and this is that pattern applied to this module's rule.
    """
    retired = _normalised(
        "**The served-corpus twin is un-re-seeded.** "
        "`.theurian/knowledge/architecture/ports-and-adapters` mirrors ADR-0003 and "
        'still reads "fourteen".'
    )

    assert _offending_claims(retired), (
        "the retired sentence was not caught, so the rule over the shipped roadmap "
        "passes for a reason that has nothing to do with the roadmap"
    )

    recording_the_end = (
        _normalised(
            "**The served-corpus twin was un-re-seeded; "
            "[#557](https://github.com/theurian/theurian/pull/557) re-seeded it on "
            "2026-09-05.**"
        ),
        _normalised("The served-corpus twin is no longer un-re-seeded."),
    )
    refused = [text for text in recording_the_end if _offending_claims(text)]

    assert not refused, (
        f"a marker in reach of the claim did not excuse it: {refused}. The scope is "
        f"{MARKER_REACH_CHARS} characters either side of `un-re-seeded`, taken from "
        f"the shipped sentence's own marker at 9 -- tightened below that, this rule "
        f"reports the roadmap's corrected cell as the defect it exists to protect"
    )

    # The ceiling. Measured 2026-09-05 at `e1a665f2`, by rebinding the constant
    # in-process: a full revert of row 4 -- the corrected sentence replaced by
    # the retired one outright -- is still caught at a reach of 512 and goes
    # unreported at 513, because the next `re-seeded` in that block sits exactly
    # 513 characters past the claim. The input below carries its marker 149
    # characters past the claim -- the collapsed ". ", twenty filler words and
    # "it was " -- so it is an offender up to 148 and clean from 149, holding
    # the window far below that vacuity threshold. The three inputs above pin
    # nothing here: each passes at every reach from 8 to 100000.
    too_far = _normalised(
        "The served-corpus twin is un-re-seeded. " + "filler " * 20 + "It was re-seeded later."
    )

    assert _offending_claims(too_far), (
        f"a marker 149 characters past the claim excused it, so the reach is wide "
        f"enough to be satisfied by a marker belonging to another sentence. This "
        f"input is an offender at every reach up to 148 and clean from 149, and "
        f"{MARKER_REACH_CHARS} is the shipped value; the roadmap's own next "
        f"`re-seeded` is 513 characters past row 4's claim, so a window grown that "
        f"far would report a full revert of that row as clean"
    )


def test_a_marker_inside_the_claims_own_word_does_not_excuse_it() -> None:
    """RED means the rule is excused by the retired claim's own spelling.

    ``re-seeded`` is a substring of ``un-re-seeded``. A reach test that did not
    exclude an overlapping occurrence would find the marker at distance zero
    inside every offending claim, and
    :func:`test_no_roadmap_block_says_the_ports_twin_is_still_un_re_seeded`
    would be green over any roadmap at all. That is the failure this file's
    whole prose side reduces to, so it is driven on its own rather than left to
    the case above -- which cannot tell the two apart, because it carries a real
    marker as well.

    The input carries **no** correction marker outside the claim: its only
    ``re-seeded`` is the one inside ``un-re-seeded``. Measured 2026-09-05, with
    the overlap clause removed from :func:`_correcting_marker` this input is
    reported clean.
    """
    only_the_claims_own_word = _normalised("The twin is un-re-seeded.")

    assert "re-seeded" in only_the_claims_own_word, (
        "this driver only tests the overlap clause while its input carries a marker "
        "as a substring of the claim; the input was reworded past that"
    )
    assert _offending_claims(only_the_claims_own_word), (
        "a claim whose only correction marker is the one inside its own "
        "`un-re-seeded` was reported clean, so `_correcting_marker` is matching the "
        "claim against itself and the prose rule cannot fail"
    )


def test_the_port_count_row_inventories_the_population_its_own_key_returns() -> None:
    """RED means the roadmap's inventory of the port-count claim has gone stale.

    Row 4 is what a reader consults to learn how many surfaces still carry the
    retired exact-count claim and where they are. The figure goes
    stale on any edit to any of them -- a changelog entry reworded, a re-seed
    adding a governed twin's body, a document leaving the population -- and #557
    moved it by two lines and one file on 2026-09-05, which is the event that
    left it unpinned.

    Recomputed under the row's **own** published key, so what fails is the
    number and not this module's opinion of how to count. Both figure pairs are
    held: the row states the second to justify the first, since "the pathspec is
    part of the key, not a convenience" is an argument that rests on the wider
    count being wider.

    Measured 2026-09-05 at ``204f9036``: 11 lines across 7 files under the
    pathspec, 14 across 8 without it.

    The first thing asserted is that **this file** is not in either population.
    It is the one member the pin can add by existing, and while the row's
    pathspec keeps it out of the narrower figure, nothing keeps it out of the
    wider one -- so the constraint the module docstring records is checked here
    rather than trusted, and its failure names the file instead of leaving a
    reader to work out why 14 became 17.
    """
    row = _row()
    argv = _population_argv(row)
    separator = argv.index("--")

    own_path = Path(__file__).resolve().relative_to(REPO_ROOT).as_posix()
    assert _matched([*argv[:separator], "--", own_path]) == (0, 0), (
        f"{own_path} matches the row's own population key, so this pin is a member "
        f"of the population it measures and the row's unexcluded figure moves when "
        f"this file does. Spell the claim through `_CLAIM_TAIL` rather than whole"
    )

    lines, files = _matched(argv)
    assert lines and files, (
        f"`{shlex.join(argv)}` matched nothing in this tree, so every comparison "
        f"below is between two zeroes and holds the row to nothing"
    )
    stated_lines, stated_files = _one(_STATED_UNDER_THE_PATHSPEC, row, claim="what its key returns")
    assert (int(stated_lines), int(stated_files)) == (lines, files), (
        f"the roadmap's port-count row says its key returns {stated_lines} lines "
        f"across {stated_files} files, and in this tree it returns {lines} across "
        f"{files}. Re-measure the row, and re-disposition whatever joined or left "
        f"the population -- the row's value is the inventory, not the number"
    )

    wide_lines, wide_files = _matched(argv[:separator])
    stated_wide_lines, stated_wide_files = _one(
        _STATED_WITHOUT_THE_PATHSPEC, row, claim="what its key returns unexcluded"
    )
    assert (int(stated_wide_lines), int(stated_wide_files)) == (wide_lines, wide_files), (
        f"the roadmap's port-count row says the same command without its exclusion "
        f"returns {stated_wide_lines} lines across {stated_wide_files} files, and here "
        f"it returns {wide_lines} across {wide_files}. That figure is what the row's "
        f"claim that the pathspec is part of the key rests on"
    )
    assert (wide_lines, wide_files) >= (lines, files), (
        f"dropping the row's exclusion returned {wide_lines} lines across {wide_files} "
        f"files, no more than the {lines} across {files} the exclusion left -- so the "
        f"pathspec is excluding nothing and the two figures are not measuring what the "
        f"row says they measure"
    )


@pytest.mark.parametrize(
    ("doctored_key", "offending_flag"),
    [
        pytest.param("`git grep -n -O/bin/sh anything`", "-O/bin/sh", id="a-pager-of-its-choosing"),
        pytest.param("`git grep -n -f evil anything`", "-f", id="a-pattern-file-of-its-choosing"),
    ],
)
def test_a_population_key_carrying_an_unlisted_flag_is_refused_before_it_runs(
    doctored_key: str, offending_flag: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """RED means a row can name a flag and this module will run it.

    :func:`_population_argv` executes a command read out of ``docs/roadmap.md``,
    so the flag fence is the whole of what stands between an edited row and an
    arbitrary process. It had no driving test until this one: deleting the
    refusal's body left the entire suite green, because the shipped row carries
    only allowed flags and no other input ever reached the branch. A guard no
    input reaches survives its own deletion.

    Both parameters are the two the fence's own comment names. ``git grep -O``
    hands each match to a pager of the row's choosing, and ``-f`` reads the
    pattern list from a file of the row's choosing; neither is in
    :data:`_ALLOWED_GREP_FLAGS`, and each must be named in the refusal rather
    than merely rejected, so that whoever widened the row learns which element
    was refused.

    ``subprocess.run`` is replaced with a tripwire that raises
    :class:`RuntimeError` -- deliberately not an :class:`AssertionError`, so
    :func:`pytest.raises` below cannot absorb it and report a fence that
    refused *after* running as a pass. The claim is that the fence refuses
    rather than executes, and that is only observable by watching for the
    execution.

    The patterns are spelled ``anything`` rather than the phrase the row greps
    for: a doctored key quoting that phrase would make this file a member of
    the population it measures, which is the constraint the module docstring
    records and :data:`_CLAIM_TAIL` discharges.
    """

    def _tripwire(*arguments: object, **keywords: object) -> object:
        raise RuntimeError(
            f"the flag fence let {arguments!r} reach subprocess.run, so a row naming "
            f"{offending_flag} is executed rather than refused"
        )

    monkeypatch.setattr(subprocess, "run", _tripwire)
    row = f"| 4 | a doctored known-defect row | Population: {doctored_key} |"

    with pytest.raises(AssertionError, match=re.escape(offending_flag)):
        _population_argv(row)


def test_the_port_count_row_names_the_twins_current_revision() -> None:
    """RED means the roadmap names a revision of the twin that is no longer current.

    The row records the #557 re-seed as ``migration X supersedes revision Y with
    Z``, and all three are derived here from the tracked migrations rather than
    trusted. The next re-seed of ``architecture.ports-and-adapters`` makes this
    RED, which is precisely when the row has to move: an audit row naming a
    superseded revision as the current one sends a reader to a body the default
    index does not serve.

    ``Z`` is the terminal ``upsertRevision`` in application order -- the rule
    ``tools/corpus_drift.py`` applies to decide which revision it compares --
    and ``Y`` is that revision's own ``expectedRevision``, so the row's sentence
    is held at both ends of the supersession rather than at the end a reader
    happens to check.

    Two premises come first, because both would otherwise let the derivation be
    wrong quietly. No tracked migration may declare a ``dependsOn`` edge: the
    ``id``-ascending walk is only the loader's real order while the dependency
    graph has none, and with an edge the terminal revision is a guess. And
    nothing may supersede ``Z``, which is the same terminality question asked
    through ``expectedRevision`` instead of through application order -- two
    keys that have to agree.
    """
    documents = _tracked_migrations()
    edges = sorted(
        path for path, document in documents.items() if (document or {}).get("dependsOn")
    )
    assert not edges, (
        f"{edges} declare dependsOn, so application order is a Kahn walk over that "
        f"graph rather than the id-ascending sort this pin walks, and which revision "
        f"of {ITEM_ID} is terminal is no longer something this module can answer. "
        f"Extend it to walk the graph, as tools/corpus_drift.py records the same limit"
    )

    terminal = _terminal_revision(documents)
    superseding = sorted(
        operation.get("revisionId", "")
        for _, operation in _item_history(documents)
        if operation.get("expectedRevision") == terminal.revision_id
    )
    assert not superseding, (
        f"{superseding} declare expectedRevision {terminal.revision_id}, so the "
        f"revision application order calls terminal is superseded by the "
        f"expectedRevision chain. The two keys disagree and neither can be quoted "
        f"as the twin's current revision until they do not"
    )

    row = _row()
    assert ITEM_ID.replace(".", "/", 1) in row, (
        f"the roadmap's port-count row no longer names {ITEM_ID}, so the re-seed "
        f"sentence below is being held against an item the row does not claim to "
        f"be about"
    )
    stated = _one(_STATED_RESEED, row, claim="which revision the re-seed superseded")

    assert stated == (terminal.migration_id, terminal.expected_revision, terminal.revision_id), (
        f"the roadmap's port-count row says migration {stated[0]} superseded revision "
        f"{stated[1]} with {stated[2]}. In this tree {ITEM_ID}'s terminal revision is "
        f"{terminal.revision_id}, carried by migration {terminal.migration_id}, and it "
        f"supersedes {terminal.expected_revision}. If the twin was re-seeded again, "
        f"this row is the audit record that follows it -- and so is the population "
        f"inventory above, which the new body's lines join"
    )
