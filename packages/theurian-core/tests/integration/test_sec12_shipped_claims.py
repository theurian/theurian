"""SEC-12's shipped-ness, held from both ends: the records and the build.

PR #663's records commit (``7aa615cb`` on that branch) rewrote the records that
said SEC-12 does not run, in the branch that made them false. A correction of
that shape owes a pin in **both** directions, and this module is it. The prose
arms hold that three records still name the shipped mechanism; the fact arm
holds that the mechanism is still in the build. They fail differently, which is
the point: a prose arm RED while the fact arm is green means a record drifted
back to the pre-SEC-12 world, and the reverse means the product moved and the
records now describe a control this build no longer carries.

**Why both ends and not one.** A record frozen against a product free to move is
the same defect one file over -- ``write_lock_claims``' reasoning, applied to a
claim about a control rather than about a path. So every symbol the records
quote is read off the live object here: the class name, the function name, and
the source path each spelled from ``__module__`` by :func:`_record_path`, never
transcribed. Rename ``InputValidationMiddleware``, move ``mcp/validation.py``,
or take the seat out of ``build_server``, and the record quoting it goes RED in
that same commit rather than at whatever later moment somebody reads it. Driven,
not argued: renaming the class across ``packages/`` in a throwaway tree takes
both *names the seat* arms RED with the fact arm green, and unwiring the seat
from ``build_server`` takes the fact arm RED with every prose arm green.

**The three records, and what each is held to.**

* ``docs/security/threat-model.md``'s **T-11**. Two falsified passages were
  corrected there, and both are keyed by one rule: no SEC-12 subject may sit in
  reach of a not-shipped phrasing. That catches the retired *"Future controls,
  not shipped: SEC-12 ... is not implemented"* paragraph and the retired
  *Controls* clause -- ``projectId`` "is *not* validated by a JSON schema at the
  MCP boundary -- there is no such validation" -- together, since they are one
  claim written twice. Beside it, the entry must name the seat.
* ``docs/security/threat-model.md``'s **T-11 residual paragraph** is held by
  ``test_sec12_record_figures.py`` beside it, not here. That paragraph carries
  #669's figures rather than SEC-12's shipped-ness, and the two failures read
  differently: a record naming the wrong seat is a record about a control that
  moved, while a record printing the wrong number is a record about a constant
  that did.
* ``docs/roadmap.md``'s **Phase 0 SEC-12 row**. Its two cells may not read
  ``nothing`` and ``the whole control`` again, and the *What ships* cell must
  name the live class and the live builder.
* ``schemas/README.md``'s **tool-context row**. It may not read "nothing, and
  nothing should" again, and it must keep naming a reader -- held as the live
  loader's own source path, so the record and the module move together.

**The second fact leg is cited, not re-implemented.** That the published schema
set and the registered tool set are equal in *both* directions -- what makes
"every MCP tool input is validated" a property of the set rather than of the
tools somebody remembered -- is
``test_input_validation_dispatch.py::test_every_registered_tool_resolves_to_a_published_input_schema``,
which reads the loaded set off the live middleware instance through
``mcp_server_probe.loaded_input_schemas``. A copy here would be a second thing to
keep in step and would answer the same question no better.

**What this does not hold, said plainly.** It holds that the records name the
shipped mechanism and that the mechanism exists in the build. It does **not**
hold that the rest of that commit's rewrite is *faithful* -- whether the
shipped-control statement describes what the middleware actually does, whether
the two residuals are the right two, whether the roadmap's owed column is
complete. Faithfulness is a reading, and no mechanical check reaches it:
ADR-0031's *Compliance* says the same of the same rewrite, that whether any of
them is faithful "is a reading and no mechanical check reaches it". A pin whose
docstring claimed more would be this module's own subject matter one level up --
a record asserting a guarantee nothing computes. It also does not reach the two
records that commit moved and this module does not name:
``docs/protocol/mcp-tools.md`` and ADR-0031 itself.

**The same boundary, restated for #669's records**, because that correction
touched five surfaces and only one of them is held here. What these arms pin is
**T-11's residual paragraph and the constants it quotes** -- its claim that the
render bound is reachable, its count and factors, and every byte figure it
prints. They do **not** pin ADR-0031's *Amendment 1* prose, the CHANGELOG entry,
or the roadmap SEC-12 cell's ``#691`` clause. Those three carry the same
correction in their own words, and no arm here reads them: a reader who reverts
one of them meets nothing in this module. What the roadmap row *is* held to is
unchanged -- its two retired cells and the seat it must name -- and the ``#691``
wording inside its *owed* cell is prose, checked by reading.

**Integration, not unit.** The fact arm builds a real ``MCPServer`` through
``build_server``, which reads every published schema off disk. The prose arms sit
beside it because this is one claim held at two ends, which is where
``test_port_count_row_claims.py`` already puts a roadmap read next to its
server-driven siblings. No database, no socket, nothing written anywhere.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path
from typing import Final

import pytest
from threat_model_claims import entry, pairings, prose
from write_lock_claims import REPO_ROOT

from theurian.application.project_service import ProjectRegistry
from theurian.daemon.runner import build_server
from theurian.mcp.middleware import InputValidationMiddleware
from theurian.mcp.validation import load_input_schemas

pytestmark = pytest.mark.integration

#: The threat-model entry that carries SEC-12's shipped-control statement.
#: Sliced by ``threat_model_claims.entry``, which is where the anchoring rules
#: and the reason for them live -- an entry runs to whichever heading comes next,
#: and a pin that searched the whole document would read a neighbour's prose as
#: its own.
_THREAT_ID: Final = "T-11"

ROADMAP: Final = REPO_ROOT / "docs/roadmap.md"
SCHEMAS_README: Final = REPO_ROOT / "schemas/README.md"

#: How the Phase 0 requirement row is located: its own first cell. Not a figure
#: and not either of the cells under test, because a key made of the text a pin
#: watches stops matching exactly when that text drifts.
_ROADMAP_ROW_KEY: Final = "| SEC-12 (MCP input schema validation) |"

#: How the what-verifies-each-schema row is located. A line *start*, so the
#: schema's many other mentions in that file cannot be mistaken for the row.
_TOOL_CONTEXT_ROW_KEY: Final = "| `mcp/tool-context.schema.json` |"

#: The two retired roadmap cells, as :func:`~threat_model_claims.prose` renders
#: them. Held by **equality** over the whole cell rather than as substrings: both
#: are ordinary English that a cell describing a real residue could reach for
#: without asserting anything false, and what *was* false is a cell that
#: consisted of one of them. (Neither appears in the shipped row today; the
#: equality is what keeps a legitimate future use of the words from reddening
#: this arm.)
_RETIRED_SHIPS_CELL: Final = "nothing"
_RETIRED_OWED_CELL: Final = "the whole control"

#: The retired ``schemas/README.md`` claim. Distinctive enough to key on
#: directly: it asserted both that nothing read the file and that nothing ought
#: to, and the middleware falsified both halves at once.
_RETIRED_TOOL_CONTEXT_CLAIM: Final = "nothing, and nothing should"

#: What names SEC-12 as the subject of a sentence. Three spellings because the
#: two retired passages named it three ways -- by requirement id, by what it
#: validates, and by where it does not happen.
_SEC12_SUBJECT: Final = re.compile(
    r"sec-12|every mcp tool input|json schema at the mcp boundary",
)

#: What a sentence reaches for to say a control does not run. Applied to
#: normalised prose, so ``*not*`` and a soft wrap are already gone.
#:
#: **Deliberately an over-approximation**, the direction a ratchet should err in:
#: ``is not validated`` and ``does not run`` are ordinary English, so a future
#: T-11 sentence saying some *other* control does not run within
#: :data:`_REACH_CHARS` of a SEC-12 mention costs a judgement. The judgement is
#: cheap and the alternative -- a key narrow enough to be quiet -- misses the
#: reworded reassertion, which is the one nobody would notice. The shipped
#: records match none of it today, and
#: :func:`test_the_reassertion_scan_reports_the_passages_it_was_written_for`
#: holds that it still matches the passages it was built for.
_UNSHIPPED: Final = re.compile(
    r"not implemented|not shipped|unimplemented|no such validation"
    r"|is not validated|not in force|does not run",
)

#: How far apart a subject and a not-shipped phrasing may sit before they stop
#: being one claim. Measured from the ends of the two matches, in either order,
#: because the retired paragraph put the phrasing *before* its subject
#: ("Future controls, not shipped: SEC-12 ...") and the retired clause put it
#: after.
_REACH_CHARS: Final = 240

#: The import path every source module here shares, stripped before a module
#: name is written the way a record writes a file.
_PACKAGE_PREFIX: Final = "theurian."


def _record_path(module: str) -> str:
    """``theurian.mcp.middleware`` as the records spell a source file.

    The records quote ``mcp/middleware.py``, ``daemon/runner.py`` and
    ``mcp/validation.py`` -- paths relative to the package root, which is how
    every prose surface in this repository names a module. Derived from the live
    ``__module__`` rather than written here, because a transcribed path is
    correct on the day it is typed and silent afterwards: that is the whole
    failure this module exists to close, and repeating it inside the pin would
    be the closure arguing against itself.
    """
    assert module.startswith(_PACKAGE_PREFIX), (
        f"`{module}` is not under `{_PACKAGE_PREFIX}`, so the path this pin holds the "
        f"records to cannot be derived from it. The package moved; move the records "
        f"and this derivation together"
    )
    return module.removeprefix(_PACKAGE_PREFIX).replace(".", "/") + ".py"


#: The seat, the builder that wires it in and the loader that fills it, each
#: read off the live object. These six strings are what the records are required
#: to carry, and none of them is typed twice.
_SEAT: Final = InputValidationMiddleware.__name__
_SEAT_FILE: Final = _record_path(InputValidationMiddleware.__module__)
_BUILDER: Final = build_server.__name__
_BUILDER_FILE: Final = _record_path(build_server.__module__)
_LOADER: Final = load_input_schemas.__name__
_LOADER_FILE: Final = _record_path(load_input_schemas.__module__)


def _roadmap_sec12_row() -> str:
    """The one Phase 0 row for SEC-12, raw."""
    rows = [
        line
        for line in ROADMAP.read_text(encoding="utf-8").splitlines()
        if _ROADMAP_ROW_KEY in line
    ]

    assert len(rows) == 1, (
        f"`docs/roadmap.md` carries {len(rows)} rows keyed on "
        f"`{_ROADMAP_ROW_KEY}`, expected 1. With none of them every arm below "
        f"reads nothing and reports it as safety; with two, whichever came first"
    )
    return rows[0]


def _roadmap_sec12_cells() -> tuple[str, ...]:
    """That row's three cells -- requirement, what ships, what is owed."""
    row = _roadmap_sec12_row()
    cells = tuple(prose(cell) for cell in row.strip().strip("|").split("|"))

    assert len(cells) == 3, (
        f"the SEC-12 requirement row splits into {len(cells)} cells, expected 3 "
        f"(requirement, what ships, what is owed). The cells below are read by "
        f"position, so a row of another width means they are read off the wrong "
        f"column -- an escaped `\\|` inside a cell does exactly that: {row[:200]}"
    )
    return cells


def _tool_context_row() -> str:
    """The one what-verifies-each-schema row for ``tool-context.schema.json``, raw."""
    rows = [
        line
        for line in SCHEMAS_README.read_text(encoding="utf-8").splitlines()
        if line.startswith(_TOOL_CONTEXT_ROW_KEY)
    ]

    assert len(rows) == 1, (
        f"`schemas/README.md` carries {len(rows)} rows starting "
        f"`{_TOOL_CONTEXT_ROW_KEY}`, expected 1. This row is the one that said "
        f"nothing read the file, so a locator that finds none of it passes the arms "
        f"below over an empty string"
    )
    return rows[0]


#: The records the reassertion rule ranges over, each read at call time.
#:
#: ``schemas/README.md``'s row is deliberately absent: it names SEC-12 in neither
#: its retired nor its current wording -- it spoke about the schema's readers, not
#: about the requirement -- so no subject of :data:`_SEC12_SUBJECT` appears in it
#: and the rule could only ever report zero there. A parametrization that included
#: it would look like three records held and be two. Its retired wording is held by
#: :func:`test_the_tool_context_row_no_longer_says_nothing_reads_the_file` instead,
#: which is the key that can actually fire on it.
_REASSERTION_RECORDS: Final[dict[str, Callable[[], str]]] = {
    "roadmap SEC-12 row": _roadmap_sec12_row,
    "threat-model T-11": lambda: entry(_THREAT_ID),
}


def _unshipped_reassertions(text: str) -> list[str]:
    """Every place *text* pairs a SEC-12 subject with a not-shipped phrasing."""
    return pairings(text, _SEC12_SUBJECT, _UNSHIPPED, _REACH_CHARS)


@pytest.mark.parametrize("record", sorted(_REASSERTION_RECORDS))
def test_no_record_says_sec_12_is_unimplemented_again(record: str) -> None:
    """RED means a record went back to calling a shipped control unimplemented.

    SEC-12 has run since PR #663's middleware commit (``084de2fb`` on that
    branch): every ``tools/call`` is validated against that tool's published
    schema in a ``ServerMiddleware`` before dispatch. The records that said
    otherwise were corrected in the branch that made them false, and the failure
    this arm exists for is the correction being undone --
    by a revert, by a merge that resolved the wrong way, or by somebody
    re-deriving the old sentence from an older record.

    That failure is not cosmetic. An on-main claim that a security control is not
    implemented is read as an admission by anyone doing a security review, and it
    is read as an open work item by anyone planning one: the first over-reports
    the risk, the second spends a slice re-shipping a control that already ships.

    **The subject premise is what stops this passing on an empty read.** An arm
    that asserts a key matches nothing passes most convincingly when it has
    stopped reading anything at all -- a moved heading, a reworded row key, a
    locator that returns a blank line. So the record is asserted to name SEC-12
    *first*, in the same normalisation the scan uses, which is the positive
    control the absence claim is worthless without.
    """
    text = _REASSERTION_RECORDS[record]()

    assert _SEC12_SUBJECT.search(prose(text)), (
        f"the {record} carries no SEC-12 subject at all, so the scan below has "
        f"nothing to pair a not-shipped phrasing with and would report a clean "
        f"record whatever it said. The locator is reading the wrong bytes, or the "
        f"record stopped naming the control it is about:\n\n{text[:400]}"
    )

    reassertions = _unshipped_reassertions(text)

    assert not reassertions, (
        f"the {record} says SEC-12 does not run, within {_REACH_CHARS} characters "
        f"of naming it:\n"
        + "".join(f"\n  ...{window}..." for window in reassertions)
        + f"\n\nSEC-12 ships: `{_SEAT_FILE}`'s `{_SEAT}`, wired into the "
        f"`MCPServer` that `{_BUILDER_FILE}`'s `{_BUILDER}` constructs, validates "
        f"every `tools/call` against that tool's published schema before dispatch "
        f"(ADR-0031). If the control really was removed, this module's fact arm is "
        f"RED too and the records are right; if it was not, the record is asserting "
        f"a gap that does not exist. If the sentence is about a *different* control "
        f"that genuinely does not ship, key it to that control so it no longer "
        f"reads as a claim about this one."
    )


def test_the_t11_entry_names_the_seat_the_build_carries() -> None:
    """RED means T-11 stopped naming the mechanism that makes its claim checkable.

    An entry that says a control ships without saying *where* it sits is a claim
    a reader cannot attack: there is nothing to open, nothing to run, and nothing
    that goes stale when the mechanism moves. T-11 names the seat, the builder
    that wires it in and the loader that fills it, and this arm holds all three
    -- read off the live objects, so the entry and the code move together.

    It is the other half of :func:`test_no_record_says_sec_12_is_unimplemented_again`
    and fails from the other side. That arm refuses the old sentence; this one
    refuses an entry that quietly stopped carrying the new one -- a paragraph
    deleted in a tidy-up leaves nothing for the absence key to fire on.

    **It does not hold that the entry describes the seat correctly.** Whether the
    shipped-control statement is faithful to what the middleware does is a
    reading; what is mechanical is that the names still resolve.
    """
    text = prose(entry(_THREAT_ID))

    for name in (_SEAT, _SEAT_FILE, _BUILDER, _BUILDER_FILE, _LOADER, _LOADER_FILE):
        assert prose(name) in text, (
            f"T-11 no longer names `{name}`.\n\n"
            f"Either the entry stopped naming the seat SEC-12 runs from -- in which "
            f"case its 'SEC-12 ships' statement points at nothing a reader can open "
            f"-- or that symbol was renamed or moved in the product, and the entry "
            f"quotes a name this build no longer has. These six strings are read off "
            f"the live objects, so the second case is what this arm is for: move the "
            f"record in the commit that moves the code."
        )


def test_the_roadmap_sec_12_row_no_longer_reads_nothing_and_the_whole_control() -> None:
    """RED means the Phase 0 requirement table went back to owing all of SEC-12.

    The row is the project's own answer to *what of this requirement ships*, and
    it read ``nothing`` against *what ships* and ``the whole control`` against
    *what is owed* until the control landed. Restored, those two cells would put
    a shipped security control back on the owed list -- which is how a slice gets
    planned twice and how a reviewer reading the roadmap concludes the boundary
    is unvalidated.

    **Equality over the whole cell, not a substring.** "nothing" is an ordinary
    word in a cell describing what is owed, and the corrected row uses it. What
    was false is a cell that *consisted* of it, so that is what is refused.
    :func:`test_the_retired_rows_are_what_these_arms_reject` drives both retired
    cells through this same splitting and normalisation, so the comparison is
    known to reach the cells rather than known to be typed.
    """
    _, ships, owed = _roadmap_sec12_cells()

    assert ships != _RETIRED_SHIPS_CELL, (
        f"the roadmap's SEC-12 row says `{_RETIRED_SHIPS_CELL}` ships. "
        f"`{_SEAT_FILE}`'s `{_SEAT}` does, wired into `{_BUILDER}` -- one published "
        f"input schema per registered tool, validated before dispatch (ADR-0031)"
    )
    assert owed != _RETIRED_OWED_CELL, (
        f"the roadmap's SEC-12 row owes `{_RETIRED_OWED_CELL}` again. What is owed "
        f"is the residue the control left -- the unit a published `maxLength` on a "
        f"write-intent `body` counts, since JSON Schema counts code points while the "
        f"byte cap it transcribes is in landed bytes (#691); the three unread context "
        f"keys (#665); and ADR-0032 decision 3's value-domain constraints -- not the "
        f"control, which ships. The transport body-cap reconciliation was on that list "
        f"until #669 closed it, and this message is not the record of what is owed: "
        f"`docs/roadmap.md`'s own cell is, and it is what the cell says that decides"
    )


def test_the_roadmap_sec_12_row_names_the_seat_the_build_carries() -> None:
    """RED means the row says something ships without naming what.

    The absence arm above refuses the two retired cells verbatim; a reworded
    retreat -- "partial", "in progress", "some validation" -- walks past it. What
    cannot be reworded away is the requirement that the *What ships* cell names
    the live class and the live builder, because those two strings are read off
    the build rather than written here.

    Which also makes this the arm that fires when the product moves: rename the
    middleware or the function that seats it, and the roadmap is quoting a symbol
    this build does not have.
    """
    _, ships, _owed = _roadmap_sec12_cells()

    for name in (_SEAT, _BUILDER):
        assert prose(name) in ships, (
            f"the roadmap's SEC-12 *What ships* cell does not name `{name}`:\n\n"
            f"{ships[:400]}\n\n"
            f"The cell is the project's record of what of this requirement is in "
            f"force. Either it stopped describing the shipped control, or `{name}` "
            f"was renamed and the row now quotes a symbol this build does not carry."
        )


def test_the_tool_context_row_no_longer_says_nothing_reads_the_file() -> None:
    """RED means ``schemas/README.md`` went back to saying the schema has no reader.

    That row read *"nothing, and nothing should: it describes tool input, so
    there is no response to compare"* -- and it was true, for as long as the
    published input contract was enforced against no traffic. It stopped being
    true when the loader began reading the file through every per-tool schema's
    ``$ref``, on every project-scoped ``tools/call``.

    The sentence is worth a pin of its own because of its second clause. "Nothing
    *should*" is a design statement: a reader who takes it at face value
    concludes that checking input schemas against real calls is out of scope
    here, which is the opposite of what SEC-12 requires -- and unlike a stale
    fact, a stale norm gets applied to the next schema somebody adds.
    """
    row = prose(_tool_context_row())

    assert _RETIRED_TOOL_CONTEXT_CLAIM not in row, (
        f"`schemas/README.md`'s tool-context row says "
        f"`{_RETIRED_TOOL_CONTEXT_CLAIM}` reads the file. `{_LOADER_FILE}`'s "
        f"`{_LOADER}` does, reached through every per-tool input schema's `$ref` and "
        f"applied by `{_SEAT}` on every project-scoped `tools/call` -- and the second "
        f"clause of that sentence tells the next reader not to check an input schema "
        f"against real traffic, which is what SEC-12 asks for"
    )


def test_the_tool_context_row_still_names_the_module_that_reads_the_file() -> None:
    """RED means the row stopped naming a reader, or names one that moved.

    The retired claim was replaced by a *named* reader, and a name is what makes
    the replacement checkable: a row that said "something reads it now" would be
    unfalsifiable prose in a table whose whole purpose is to say where each rule
    has been applied.

    Held as the loader's own source path, derived from its ``__module__``, so the
    arm fires in both directions -- the row dropping the name, and the module
    moving out from under it.

    It does not pin the grammar around the name. What the row calls the reader --
    "loader", "reader", the function's own name -- is wording, and a pin on
    wording has a next wording.
    """
    row = prose(_tool_context_row())

    assert prose(_LOADER_FILE) in row, (
        f"`schemas/README.md`'s tool-context row does not name `{_LOADER_FILE}`:\n\n"
        f"{row[:400]}\n\n"
        f"That row is the table's answer to *what checks this schema*, and it names "
        f"the module whose loader reads the file through every per-tool `$ref`. "
        f"Either the row dropped the reader it gained, or the loader moved and the "
        f"row is pointing at a file that no longer holds it."
    )


def test_the_built_server_carries_the_input_validation_middleware(tmp_path: Path) -> None:
    """The fact the three records rest on: the seat is in the build.

    Every prose arm above holds a *record*. This one holds the thing recorded --
    that ``build_server`` still wires the input-validation middleware into the
    ``MCPServer`` it constructs -- and it is what makes the prose pins something
    other than three files agreeing with each other. Unwire the seat and the
    records become false the same day, with this arm RED and the prose arms
    green, which is the direction that says *the product moved*.

    **Exactly one**, not "at least one". Two seats would validate every call
    twice against two schema sets loaded at different moments, which is a
    disagreement nothing else here would report; zero is the control silently
    off while every published schema still sits in the tree.

    Asked of a real built server rather than of ``runner.py``'s source, for
    ``mcp_server_probe``'s reason: a second read of the tree agrees with the tree
    while the server runs on something else. The population the seat validates -- one
    schema per registered tool, in both directions -- is
    ``test_input_validation_dispatch.py::test_every_registered_tool_resolves_to_a_published_input_schema``
    and is deliberately not recomputed here.
    """
    server = build_server(ProjectRegistry(path=tmp_path / "projects.json"))

    seated = [seat for seat in server.middleware if isinstance(seat, InputValidationMiddleware)]

    assert len(seated) == 1, (
        f"the server `{_BUILDER}` built carries {len(seated)} `{_SEAT}` entries, "
        f"expected 1. Its chain is "
        f"{[type(seat).__name__ for seat in server.middleware]}.\n\n"
        f"Zero means SEC-12 is off: every `tools/call` reaches the SDK's argument "
        f"coercion unvalidated, an unknown key is dropped rather than refused, and "
        f"the three records this module pins have become false -- the threat model's "
        f"T-11, the roadmap's SEC-12 row and `schemas/README.md`'s tool-context row "
        f"all say this seat exists. Two means one call is validated twice against "
        f"two separately loaded schema sets, which nothing holds equal."
    )


# -- the keys' own positive controls -------------------------------------------

#: The two retired T-11 passages, verbatim at ``7aa615cb^`` -- the state
#: immediately before PR #663's records commit, so a squash leaves them findable
#: through that PR rather than through the sha. Line breaks included, because
#: both wrap mid-claim and folding the wrap is what makes the rule able to see
#: them at all.
_RETIRED_T11_PASSAGES: Final[tuple[tuple[str, str], ...]] = (
    (
        "the Controls clause",
        "**Controls:** `projectId` is required on every project-scoped call. It is *not*\n"
        "validated by a JSON schema at the MCP boundary — there is no such validation,\n"
        "`jsonschema` is imported only by the migration loader — but it is validated by\n"
        "construction:",
    ),
    (
        "the future-controls paragraph",
        "*Future controls, not shipped:* SEC-12 — validating every MCP tool input against\n"
        "its published JSON Schema at the boundary — is not implemented; input is checked\n"
        "by domain construction as above, not against the schemas.",
    ),
)


@pytest.mark.parametrize(
    ("label", "passage"),
    _RETIRED_T11_PASSAGES,
    ids=[case[0] for case in _RETIRED_T11_PASSAGES],
)
def test_the_reassertion_scan_reports_the_passages_it_was_written_for(
    label: str, passage: str
) -> None:
    """The premise under the absence arm: the rule can still fire.

    An absence arm is green against a rule that has stopped matching -- a regex
    narrowed in a tidy-up, a normalisation that stopped folding wraps, a subject
    spelling that drifted -- and it is green most convincingly at exactly that
    moment. So both retired passages are held here, verbatim as the records
    carried them, and the rule is required to report each one.

    Verbatim including the line breaks, which is the half a synthetic string
    would miss: each passage wraps through the middle of its own claim, and a
    rule that stopped normalising would report nothing while the shipped records
    stayed green.
    """
    reported = _unshipped_reassertions(passage)

    assert reported, (
        f"{label}, the retired text itself, is no longer reported.\n\n"
        f"The rule that guards the three records has stopped matching the passages "
        f"it was written for -- a narrowed key, a subject spelling that drifted, or a "
        f"normalisation that stopped folding the wrap this passage carries. Every "
        f"absence arm above is green whatever the records now say.\n\n{passage}"
    )


#: The two retired rows, verbatim at ``7aa615cb^`` -- same frame as the passages
#: above, and the roadmap row keeps the two-space indent its list item carries,
#: because the splitting below has to survive it.
_RETIRED_ROADMAP_ROW: Final = (
    "  | SEC-12 (MCP input schema validation) | nothing | the whole control |"
)
_RETIRED_TOOL_CONTEXT_ROW: Final = (
    "| `mcp/tool-context.schema.json` | nothing, and nothing should: it describes tool "
    "*input*, so there is no response to compare. "
    "`test_project_id_is_required_on_every_tool_call` holds what it is for |"
)


def test_the_retired_rows_are_what_these_arms_reject() -> None:
    """The premise under the two row arms: the comparison reaches the retired text.

    Both row arms compare normalised text against a literal written in this file,
    and a literal is a thing that can be typed wrong or drift from the rendering
    the arm actually performs -- backticks stripped, case folded, whitespace
    collapsed. Either way the arm would pass over the retired row it exists to
    refuse, and nothing would say so.

    So the retired rows are driven through the same splitting and the same
    normalisation the arms use, and the retired cells and phrase are required to
    come back out. This holds the instrument, not the tree: what the shipped rows
    read is the arms' business.
    """
    cells = tuple(prose(cell) for cell in _RETIRED_ROADMAP_ROW.strip().strip("|").split("|"))

    assert cells[1:] == (_RETIRED_SHIPS_CELL, _RETIRED_OWED_CELL), (
        f"the retired roadmap row renders as {list(cells)}, so the arm that refuses "
        f"`{_RETIRED_SHIPS_CELL}` and `{_RETIRED_OWED_CELL}` would not have refused "
        f"the row those cells came from -- it is comparing the wrong columns, or "
        f"against literals the normalisation never produces"
    )
    assert _RETIRED_TOOL_CONTEXT_CLAIM in prose(_RETIRED_TOOL_CONTEXT_ROW), (
        f"the retired tool-context row does not render to carry "
        f"`{_RETIRED_TOOL_CONTEXT_CLAIM}`, so the arm keyed on that phrase would pass "
        f"over the row it exists to refuse"
    )
