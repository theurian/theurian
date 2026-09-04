"""The records that say T-17a's segment face is closed, and the fact under them (#499).

PR #545 closed the FTS5 tombstone channel: `index_purge._merge_full_text` merges
every full-text table the build declares, so a purged build no longer carries the
postings of the rows it was asked to remove, and query duration stops being
monotone in the withdrawn count. **Eleven recorded sites said the opposite in the
present tense**, three of them shipping in the wheel as production docstrings, and
the docs stage corrected all eleven. This module is that correction's ratchet.

Two pins, answering two different ways the correction can rot:

1. **The prose can be reverted or re-described.** A future edit that restores
   "nothing in the shipped purge merges them" -- or that simply drops the sentence
   recording the merge -- puts the repository back to asserting an open
   information channel that is in fact closed. That is the security finding this
   file's trailer names, and its severity comes from the direction: a record that
   says a closed channel is open is read by the next reviewer as work still owed,
   while a record that says an open channel is closed is a false assurance. The
   sweep found the second kind.
2. **The fact can move out from under the prose.** The records state a count --
   the schema "carried two of these at v3 and carries four at v4" -- and they
   state a mechanism, that the purge calls the merge. A fifth full-text table, or
   a lost call, makes every one of those records false without touching a word of
   them.

**THE REACH, AND WHY IT IS NOT A LINE-ORIENTED KEY.** The sweep that found these
sites was run with `git grep`, and `git grep` matches within a line. This
repository soft-wraps prose, so a sentence is routinely spread across two or three
source lines and a line-oriented key reads a fragment that matches nothing. That
is not hypothetical here and it is not a small correction: measured on this tree
2026-09-04, the merge sentence pinned below

    git grep -c "over every full-text table it discovers in the build's own schema"

returns **one** file, and the same key read wrap-aware returns **three** --
`visibility.py` and `index_store.py` both carry it across a soft wrap and are
invisible to the line-oriented form. The count sentence is the same story from the
other side: `git grep -c "carries four at v4"` returns four files and the
wrap-aware read returns five, the CHANGELOG's instance wrapping between "carries"
and "four". The docs stage's own population went from eight to eleven for exactly
this reason.

So every key here is matched against :func:`_prose` -- Markdown blockquote `>`,
Python comment `#` and comment-continuation `--` markers stripped from the head of
each line, inline backticks and emphasis asterisks removed so RST ``x`` and
Markdown `x` read alike, lowercased, whitespace collapsed across line breaks.
:func:`test_at_least_one_pinned_record_is_reachable_only_wrap_aware` is the
control on that instrument: it fails if the normalisation stops collapsing wraps,
so nobody can quietly replace it with a substring test that happens to pass.

**What this file does not claim.** It does not claim the population of eleven is
complete -- that is the docs stage's sweep, recorded in commit 6234e9a9's message
with both keys' before and after. It pins the four sites whose *reversion* would
be most costly: the threat model's T-17a row, which is the entry a reviewer reads
to decide whether the face is still open, and the three production docstrings,
which ship to users in the wheel. The seven other corrected sites -- ADR-0024's
amendment, the CHANGELOG, `test_purged_build_quantities.py`, and the threat
model's detail blocks -- are dated measurement records rather than live claims,
and ADR-0024 and the CHANGELOG deliberately *retain* the old wording inside
pointered historical entries. A blanket repository-wide negative key would go RED
on those retentions, which is why the negative key below is scoped to the four
live sites rather than swept over the tree.
"""

from __future__ import annotations

import ast
import re
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from write_lock_claims import REPO_ROOT, collapsed

from theurian.infrastructure.sqlite.index_purge import (
    _FTS5_DECLARATION,
    _FTS5_TABLE_CANDIDATES,
)
from theurian.infrastructure.sqlite.index_schema import INDEX_DDL

# -- the wrap-aware reading --------------------------------------------------

#: Leading structure markers, stripped so a claim inside one reads as the sentence
#: it is. `>` is the threat model's blockquote -- T-17a's detail block is nested
#: two deep, `> > `, so the marker repeats. `#` is a Python comment, which is how
#: `mcp/tools.py` carries its claim, and also a Markdown heading. `--` is the
#: comment continuation used in SQL DDL and in this project's docstring prose.
#: Repeated because `> > ` is two markers on one line.
_MARKERS: Final = re.compile(r"^[ \t]*(?:>[ \t]?|#+[ \t]?|--[ \t]?)+")

#: Inline markup, removed so one key reads three markup dialects. The same
#: sentence is spelled ``index_purge._merge_full_text`` in an RST docstring,
#: `index_purge._merge_full_text` in Markdown, and **bold** in the threat model.
#:
#: Underscore is deliberately *not* in this class. It is Markdown emphasis, but it
#: is also every Python identifier these records name, and stripping it turns
#: `_merge_full_text` into `mergefulltext` -- which silently stopped the
#: `mcp/tools.py` key matching while this module was being written.
_MARKUP: Final = re.compile(r"[`*]+")


def _prose(text: str) -> str:
    """*text* as one lowercased line, structure markers and inline markup gone.

    :func:`collapsed` alone is not enough, and the gap is invisible until a key
    spans a line inside a marked-up block. The threat model's T-17a detail block
    is a two-deep blockquote, so a sentence written across a soft wrap reads
    ``the segment-level > > face is closed`` once whitespace alone is flattened,
    and a key written the way the sentence reads matches nothing while the
    sentence sits there intact. That is the failure mode `write_lock_claims.py`
    records paying for twice and the one the #499 sweep paid for a third time.
    """
    stripped = "\n".join(_MARKERS.sub("", line) for line in text.splitlines())
    return collapsed(_MARKUP.sub("", stripped))


def _line_oriented(text: str, key: str) -> bool:
    """Whether *key* is reachable when the same normalisation stops at a line.

    The comparison :func:`test_at_least_one_pinned_record_is_reachable_only_wrap_aware`
    needs: identical marker and markup handling, so the *only* difference from
    :func:`_prose` is whether a soft wrap is crossed. A plain `git grep` would
    differ in three ways at once and could not tell which one mattered.
    """
    return any(_prose(line) and key in _prose(line) for line in text.splitlines())


# -- pin 1: the records say the channel is closed ----------------------------

_CORE: Final = REPO_ROOT / "packages/theurian-core"

#: The sentence three of the four sites use to record the mechanism, normalised.
#: Chosen over a byte-for-byte block because it is the load-bearing clause -- what
#: merges, and over which tables -- and because the three sites spell it in three
#: markup dialects and wrap it in three different places.
MERGE_SENTENCE: Final = (
    "issues an fts5 optimize over every full-text table it discovers in the build's own schema"
)


@dataclass(frozen=True, slots=True)
class ClosureRecord:
    """One live record, and the clause whose loss would re-open the claim."""

    label: str
    path: Path
    key: str


#: The four sites whose reversion costs the most: the entry a reviewer reads to
#: grade T-17a, and the three docstrings that ship in the wheel. `mcp/tools.py`
#: carries its own wording -- it names the closure rather than restating the
#: mechanism -- so it is pinned on the clause it actually has, not on a clause a
#: reader of this list might assume it shares.
CLOSURE_RECORDS: Final = (
    ClosureRecord(
        "threat model, T-17a risk-table row (the verdict a reviewer reads)",
        REPO_ROOT / "docs/security/threat-model.md",
        "the segment-level face is closed",
    ),
    ClosureRecord(
        "threat model, T-17a (the mechanism under the verdict)",
        REPO_ROOT / "docs/security/threat-model.md",
        MERGE_SENTENCE,
    ),
    ClosureRecord(
        "visibility.py, CanonicalVisibility (ships in the wheel)",
        _CORE / "src/theurian/application/visibility.py",
        MERGE_SENTENCE,
    ),
    ClosureRecord(
        "index_store.py, SqliteIndexStore (ships in the wheel)",
        _CORE / "src/theurian/infrastructure/sqlite/index_store.py",
        MERGE_SENTENCE,
    ),
    ClosureRecord(
        "mcp/tools.py, the search frame's scope note (ships in the wheel)",
        _CORE / "src/theurian/mcp/tools.py",
        "index_purge._merge_full_text closed that face",
    ),
)

#: Present-tense assertions that the channel is open. **Present tense only**, and
#: that is the whole difficulty of this key: the corrected sites keep the same
#: sentences in the past tense, because the measurement they carry is the record.
#: "nothing in the purge merged them" is history and must stay; "nothing in the
#: purge merges them" is a false claim about the shipped code.
OPEN_CHANNEL_KEYS: Final = (
    ("the purge does not merge", re.compile(r"nothing in the (?:shipped )?purge merges")),
    (
        "duration is monotone in the withdrawn count",
        re.compile(r"(?:is|are|stays|stay) monotone in the withdrawn count"),
    ),
)


def test_every_live_record_of_the_tombstone_channel_says_it_is_closed() -> None:
    """The four records a reader grades T-17a from must carry the closure (#499, SEC-13).

    Before PR #545 each of these asserted, in the present tense, that FTS5's
    `'delete'` leaves the postings and nothing merges them -- true when written and
    false the moment the merge landed. Three of them ship to users inside the
    wheel. A record that describes a closed information channel as open is not a
    harmless staleness: it is read by the next reviewer as work still owed, and by
    an operator as a residual risk they are carrying.

    Pinned on the load-bearing clause rather than on the block, so the prose around
    it stays editable -- which is the point of a ratchet, as against a snapshot
    nobody can touch without a diff war.
    """
    missing = [
        record.label
        for record in CLOSURE_RECORDS
        if record.key not in _prose(record.path.read_text(encoding="utf-8"))
    ]

    assert not missing, (
        f"a record of #499's closure lost the clause that records it: {missing}. "
        f"Restore the sentence, or -- if the merge really has been removed -- correct "
        f"every one of these records together, because they are what a reviewer grades "
        f"T-17a from and three of them ship in the wheel"
    )


def test_no_live_record_asserts_the_tombstone_channel_open_in_the_present_tense() -> None:
    """The reverted-wording arm: the sentences that were false must not come back.

    The two keys are the ones the docs stage swept with, and they are matched here
    in the **present tense only**. That distinction is the whole of this test's
    difficulty and it is deliberate: every corrected site keeps the same clause in
    the past tense, because the +27.4 ms and 16.8 ms figures those sentences carry
    are PR #498's measurement and the record of what was true before the merge.
    A key that could not tell "merged" from "merges" would either force the
    deletion of the measurements or fail on the day they were written down.

    Scoped to the four live sites, not swept over the tree, and the reason is in
    the module docstring: ADR-0024 and the CHANGELOG deliberately retain the old
    wording inside dated, pointered entries, so a repository-wide sweep with these
    keys reports those retentions as defects.
    """
    reopened = [
        (record.label, name)
        for record in CLOSURE_RECORDS
        for name, pattern in OPEN_CHANNEL_KEYS
        if pattern.search(_prose(record.path.read_text(encoding="utf-8")))
    ]

    assert not reopened, (
        f"a live record asserts the closed channel open, in the present tense: {reopened}. "
        f"`index_purge._merge_full_text` merges every full-text table the build declares, "
        f"so the postings are gone and the duration is flat. If this text is meant as "
        f"history, put it in the past tense the way the surrounding measurements are"
    )


def test_at_least_one_pinned_record_is_reachable_only_wrap_aware() -> None:
    """The control on the instrument: a line-oriented key would miss these sites.

    Without this, :func:`_prose` could be replaced by a plain substring test and
    the two pins above might still pass -- leaving a ratchet that silently stops
    reaching exactly the sites the #499 sweep first missed. The sweep's own
    population went from eight to eleven when it was re-run wrap-aware.

    Asserted as "at least one", not as a count, on purpose. Where a sentence wraps
    is a function of how the paragraph was last reflowed, so a pinned count would
    go RED on a reflow that changed nothing anyone cares about. What must hold is
    that the wrap-crossing case is *live* in this population, so the normalisation
    is load-bearing rather than decorative.
    """
    only_wrap_aware = [
        record.label
        for record in CLOSURE_RECORDS
        if (text := record.path.read_text(encoding="utf-8"))
        and record.key in _prose(text)
        and not _line_oriented(text, record.key)
    ]

    assert only_wrap_aware, (
        "every pinned clause now fits on one source line, so these pins no longer "
        "demonstrate that the wrap-aware reading is necessary and a future reader may "
        "reasonably simplify `_prose` away. Either a paragraph was reflowed -- in which "
        "case pin a clause that does wrap -- or the records were rewritten"
    )


# -- pin 2: the fact the records rest on -------------------------------------

#: What every record spells: the schema "carried two of these at v3 and carries
#: four at v4". Recomputed below from the live schema and the live discovery
#: pattern, so the number in the prose and the number in the code cannot drift.
RECORDED_FULL_TEXT_TABLES: Final = 4

#: The clause the count is spelled in, normalised. Wrap-aware for the usual
#: reason: the CHANGELOG breaks it between "carries" and "four", so `git grep`
#: finds four of these five files.
COUNT_SENTENCE: Final = "carries four at v4"

COUNT_RECORDS: Final = (
    REPO_ROOT / "docs/adr/0024-a-purge-is-a-build.md",
    REPO_ROOT / "docs/security/threat-model.md",
    _CORE / "CHANGELOG.md",
    _CORE / "src/theurian/infrastructure/sqlite/index_purge.py",
    _CORE / "tests/integration/test_purge_full_text_discovery.py",
)

PURGE_SOURCE: Final = _CORE / "src/theurian/infrastructure/sqlite/index_purge.py"


def _discovered_full_text_tables() -> tuple[list[str], int]:
    """The tables the shipped merge reaches over the shipped schema, and the field it filtered.

    Built the way `_merge_full_text` discovers them -- the live
    :data:`_FTS5_TABLE_CANDIDATES` query and the live :data:`_FTS5_DECLARATION`
    pattern over a database made from the live `INDEX_DDL` -- rather than by
    counting `USING fts5` in the schema file. Two reasons, and each is a way the
    file-count answer is wrong: `index_schema.py` also declares `FTS5_PROBE`, a
    temp-table probe that is not part of a build, so the file carries five
    occurrences where a build declares four; and the question this pin asks is
    what the *discovery* reaches, which is the pattern's answer and not the
    schema's.

    In memory, so nothing here touches a filesystem: measured 2026-09-04, an
    in-memory database made from `INDEX_DDL` and a real `SqliteIndexStore.create`
    build return the same 26 candidates and the same 4 reached.
    """
    with closing(sqlite3.connect(":memory:")) as connection:
        connection.row_factory = sqlite3.Row
        connection.executescript(INDEX_DDL)
        rows = connection.execute(_FTS5_TABLE_CANDIDATES).fetchall()
    reached = [str(row["name"]) for row in rows if _FTS5_DECLARATION.match(str(row["sql"]))]
    return reached, len(rows)


def test_the_merge_reaches_as_many_full_text_tables_as_the_records_claim() -> None:
    """The count in the prose is recomputed from the schema it describes (#499).

    Six records state the schema "carried two of these at v3 and carries four at
    v4", and the closure argument rests on it: the merge is safe to describe as
    covering the whole index precisely because the discovery reaches every table
    the schema declares. A fifth full-text table would make all six false at once,
    silently, without anyone editing them -- which is the shape
    `docs/.../pin-derivations-not-prose` exists to stop.

    The control is asserted first and is what stops this passing on a pattern that
    matched everything: the candidate query returns every `CREATE TABLE` in the
    build, shadow tables included, so a declaration pattern that had stopped
    discriminating would reach far more than four and be caught here rather than
    read as agreement.
    """
    reached, candidates = _discovered_full_text_tables()

    assert candidates > len(reached), (
        f"the declaration filter reached all {candidates} candidate tables, so it is no "
        f"longer discriminating and the count below agrees with the records by accident. "
        f"Each FTS5 table owns four or five shadow tables that also carry CREATE TABLE "
        f"text, and `_FTS5_DECLARATION` is what excludes them"
    )

    assert len(reached) == RECORDED_FULL_TEXT_TABLES, (
        f"the shipped merge reaches {len(reached)} full-text tables ({reached}), but the "
        f"records say four. Every one of these has to move together, because each states "
        f"the count as the reason the merge covers the whole index: "
        f"{[str(path.relative_to(REPO_ROOT)) for path in COUNT_RECORDS]}"
    )

    unspelled = [
        str(path.relative_to(REPO_ROOT))
        for path in COUNT_RECORDS
        if COUNT_SENTENCE not in _prose(path.read_text(encoding="utf-8"))
    ]
    assert not unspelled, (
        f"a record stopped spelling the table count the code was just measured at "
        f"{len(reached)}: {unspelled}. The count is what makes 'every full-text table' "
        f"a checkable claim rather than a hope"
    )


def test_the_purge_still_calls_the_merge_the_records_name_as_the_closure() -> None:
    """Every closure record names `_merge_full_text` as what closed T-17a's face.

    The merge helper can keep existing, keep its tests, and stop being called --
    `purge_into` is the only caller, so deleting one line re-opens the channel
    while `test_purge_full_text_discovery.py` stays green on the helper itself.
    The structural pins in `test_purged_build_structure.py` would go RED too, and
    that is the right outcome; what they would not say is that four records
    describing a closed channel had just become false. This test says it.

    Read off the AST rather than the file's text, so a mention inside a docstring
    or a comment -- and this module is full of them -- cannot stand in for a call.
    """
    tree = ast.parse(PURGE_SOURCE.read_text(encoding="utf-8"))
    purge_into = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "purge_into"
    )

    called = {
        node.func.id
        for node in ast.walk(purge_into)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }

    assert "_merge_full_text" in called, (
        "`purge_into` no longer calls `_merge_full_text`, so a purged build keeps the "
        "withdrawn rows' postings and query duration goes back to being monotone in the "
        "withdrawn count. Four records now describe a channel that is open again: the "
        "threat model's T-17a row, and the docstrings in visibility.py, index_store.py "
        "and mcp/tools.py -- all three of which ship in the wheel"
    )
