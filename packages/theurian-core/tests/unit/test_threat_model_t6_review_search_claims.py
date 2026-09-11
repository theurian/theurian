"""T-6's fifth query-side member, held to the bounds `review.search` actually applies.

``docs/security/threat-model.md`` gained *The fifth query-side member:
``review.search``* on this branch, and the block is a **conversion**: an
adversarial HIGH -- the tool takes no per-call wall-clock bound -- was converted
into a recorded design decision rather than fixed. This project's own rule is
that a HIGH converts only against "the reasoning recorded in the code or an
ADR", so the block is now the only thing standing between that HIGH and shipped
behaviour. A conversion nothing recomputes is a HIGH with a paragraph in front
of it.

**What the block promises a reader, and therefore what is pinned.** It hands
over a table naming every bound that exists and the symbol that applies each
one, and one row saying that the wall-clock bound is *not taken*. Someone sizing
this surface -- an operator deciding whether to expose the daemon, a reviewer
deciding whether the conversion still holds -- reads the figures out of that
table and the deferral out of that row. Both are facts about today's code.

**Both sides are derived, and they are written independently.** The fact side is
read off the live module: the published bound constants of
``mcp/review_search.py``, the response budget recomputed from its own factors,
the projection ``review_search_sql.excerpt_columns()`` really carries, and the
parameters ``mcp/tools.py`` declares for ``review.search``. The prose side is
read out of the entry: which symbols the table cites, which figure it puts
beside each, and what the wall-clock row says. Neither side is parsed from the
other, because a pin that read its expectation out of the sentence it checks
would agree with that sentence by construction and measure nothing.

**The arm that matters most is the one that has never fired.** The deferral's
premise is that *no* per-call wall-clock bound exists. The day one lands it will
land as a parameter on this tool -- a ``timeout``, a ``deadline``, a
``maxSeconds`` -- and the "not taken" row becomes false in that same commit.
:func:`test_review_search_takes_no_per_call_time_bound_while_t6_records_none`
is what makes that commit meet the record. Read from ``mcp/tools.py``'s **syntax
tree** rather than through ``inspect.signature``: the tool is registered through
a decorator, so a runtime signature is whatever the seam last chose to expose,
and what the deferral is about is the parameter list a contributor writes.

**Why this is a second module for one entry.** ``threat_model_claims`` says one
module per entry, and ``test_threat_model_t6_claims.py`` is T-6's -- but that
module is entirely about one row of the *fourth* member's table, the served
``findingText`` bound, down to its module docstring. This block is a different
member, a different set of symbols and a different failure. Folding it in would
have meant rewriting that module's stated subject; the slicing that the
convention exists to share is shared, since both read the entry through
``threat_model_claims.entry``.

**What it does not hold.** That the bounds are *enforced* -- that a caller
sending ``limit=51`` is refused, that the budget really stops a page -- which is
behaviour and is pinned as behaviour by
``tests/integration/test_review_search_bound_input.py`` and
``tests/integration/test_review_search_tool.py``. Nor the block's two
measurement sets, which are dated readings rather than properties of the tree
and are cited as such.

Pure in the sense the other claim pins are: one document and one source module
read as text, and two modules read for their symbols -- no database, socket or
temporary directory.
"""

from __future__ import annotations

import ast
import pathlib
import re
from typing import Final

import pytest
from threat_model_claims import entry, prose
from write_lock_claims import REPO_ROOT

from theurian.domain.retrieval import EXCERPT_CHARS
from theurian.infrastructure.sqlite.review_search_sql import excerpt_columns
from theurian.mcp import review_search

pytestmark = pytest.mark.unit

#: The entry this module reads. Sliced by ``threat_model_claims.entry``, which is
#: where the anchoring rules and the reason for them live.
_THREAT_ID: Final = "T-6"

#: How the entry opens the block, and how it opens every sibling member block.
#: The ordinal is a group so :func:`_member_block` can report which anchors the
#: entry carries when the one asked for is missing.
_MEMBER_ANCHOR: Final = re.compile(r"\*\*The (\w+) query-side member: `([a-z.]+)`\.\*\*")

#: This member's ordinal and tool, as the anchor spells them.
_ORDINAL: Final = "fifth"
_TOOL: Final = "review.search"

#: What follows the last member block in the shipped document. A member block has
#: no closing delimiter, and the generic rule -- run to the next member anchor --
#: has nothing to find for the last one. So the slice also stops here, and
#: :func:`_member_block` asserts that the text it kept carries neither the next
#: topic's heading nor the fourth member's citations. A paragraph inserted
#: between this block and that heading therefore fails loudly and gets a decision
#: rather than being silently scanned as part of the block.
_BLOCK_END: Final = "**Controls on `propose accept`"

#: The fourth member's citation prefix, used as a contamination guard: a slice
#: that ran into the neighbouring table would pick these up, and every figure
#: checked below would then be read off the wrong member.
_SIBLING_PREFIX: Final = "mcp/findings.py::"

#: The module whose published bounds the block enumerates, as the entry writes
#: the path. Every citation is asserted to carry it, so a symbol that happens to
#: be named somewhere else in T-6 cannot stand in for a table row.
_MODULE_PATH: Final = "mcp/review_search.py"


def _citation(symbol: str) -> re.Pattern[str]:
    """How the block cites *symbol*: a code span, and the figure if it gives one.

    The module path is optional because the concurrency row's symbols live in
    another module and the entry cites them bare. The unit suffix is matched so
    ``ADMISSION_WAIT_SECONDS`` (1.0 s) yields ``1.0`` rather than nothing --
    without it that row's figure would drop silently out of the comparison, which
    is a pin that stops checking rather than one that fails.
    """
    return re.compile(rf"`(?:[\w/.]+::)?{re.escape(symbol)}`(?:\s*\((\d+(?:\.\d+)?)\s*s?\))?")


#: The response budget as the block writes its derivation, with the two derived
#: terms captured. Sized by the *expression* rather than by the figure, which is
#: what the block tells a reader to do -- so what is checked is that the
#: expression still evaluates to the live constant.
#:
#: The multiplication sign is written as the escape ``\u00d7`` rather than
#: pasted. The document uses U+00D7, and a pasted one here is indistinguishable
#: at a glance from an ASCII ``x`` -- which is the drift that leaves this pattern
#: matching nothing while the arm reads as green over an entry it never found.
_BUDGET_EXPRESSION: Final = re.compile(
    r"`MAX_REVIEW_SEARCH_LIMIT \u00d7 \(MAX_EXCERPT_CHARS \+ (\d+) \+ (\d+) "
    r"\u00d7 MAX_FILTER_CHARS\)`"
)

#: The excerpt projection the block quotes, read from the raw block so the
#: backticks survive: an unquoted mention in running prose is not the citation
#: this checks.
_QUOTED_PROJECTION: Final = re.compile(r"`(substr\(t\.content[^`]*)`")

#: The row that carries the deferral. Keyed on the Dimension cell, which the
#: fourth member's table spells identically -- which is exactly why this arm reads
#: the sliced block rather than the whole entry.
_WALL_CLOCK_DIMENSION: Final = "wall clock per call"

#: What the deferral row has to keep saying. ``not taken`` is the recorded
#: decision itself; ``nothing`` is the Bound cell, and a row that named a bound
#: while still saying "not taken" would be two claims at once.
_DEFERRAL_WORDS: Final = ("nothing", "not taken")

#: ``mcp/review_search.py``, read as source. Imported as well, for the values;
#: read as text for the one claim that is about an *expression* rather than a
#: value -- see :func:`_module_assignment`.
_REVIEW_SEARCH_MODULE: Final = (
    REPO_ROOT / "packages/theurian-core/src/theurian/mcp/review_search.py"
)

#: ``mcp/tools.py``, read as source. The reason is on
#: :func:`test_review_search_takes_no_per_call_time_bound_while_t6_records_none`.
_TOOLS_MODULE: Final = REPO_ROOT / "packages/theurian-core/src/theurian/mcp/tools.py"

#: The names a per-call time bound would arrive under. Matched as a substring of
#: a lowercased parameter name, so ``timeoutSeconds``, ``max_seconds`` and
#: ``deadline`` all land. Over-broad in the RED direction on purpose: a false RED
#: costs a read of one parameter, a false green costs the deferral.
_TIME_BOUND_NAMES: Final = frozenset({"timeout", "deadline", "seconds", "budget", "expires"})

#: The three symbols the concurrency row cites that live in ``mcp/tools.py``
#: rather than in ``mcp/review_search.py``. Named here because nothing about the
#: gate says which module holds it, and asserted to be assigned at that module's
#: top level so a rename reddens instead of leaving the row pointing nowhere.
_GATE_SYMBOLS: Final = ("MAX_CONCURRENT_SEARCHES", "ADMISSION_WAIT_SECONDS")

#: The refusal constant, which is this member's alone -- the fourth member's row
#: cites ``FINDINGS_CAPACITY_REFUSAL`` in the same position. It is not a literal,
#: so only its assignment is asserted.
_REFUSAL_SYMBOL: Final = "REVIEW_SEARCH_CAPACITY_REFUSAL"


def _published_bounds() -> tuple[str, ...]:
    """Every bound constant ``mcp/review_search.py`` publishes, sorted.

    Derived from ``__all__`` rather than transcribed, so a seventh bound joins
    the population by the change that adds it and T-6's table has to gain a row
    for it. The prefixes are the module's own naming: ``MAX_`` for a ceiling,
    ``DEFAULT_`` for what a caller who sends nothing gets, and both are figures a
    reader sizes this surface by.

    A future bound named some other way takes this population GREEN while T-6
    says nothing about it. That is the honest bound of the derivation and is the
    same one ``test_threat_model_t19_claims.py`` records for its family key: the
    answer then is to name the new bound in the entry, not to widen this.
    """
    return tuple(
        sorted(name for name in review_search.__all__ if name.startswith(("MAX_", "DEFAULT_")))
    )


def _member_block(ordinal: str) -> str:
    """The one block of T-6 introduced by *ordinal*'s member anchor, raw.

    Raw rather than normalised, because the arms below split it into table rows
    and read its backticked citations, and :func:`prose` destroys both.

    Sliced rather than paragraph-scoped, which the sibling entries do not need to
    do. The reason is specific: the fourth member's table carries a
    ``wall clock per call`` row of its own, spelled identically and saying
    something different, so a key on the Dimension cell reads two rows across the
    entry and a pin over "the deferral" would be about whichever came first.

    Three assertions guard the slice, because a slice that ran long is silent.
    The anchor must be unique. **A terminator must be found at all** -- with none,
    the block would run to the end of the entry and quietly scan every paragraph
    after it, which is the failure mode that reports a green over the wrong text
    rather than reporting anything. And the kept text must not carry a sibling
    member's citation prefix, which is what a slice that swallowed a neighbouring
    table would pick up.
    """
    text = entry(_THREAT_ID)
    anchors = [
        match for match in _MEMBER_ANCHOR.finditer(text) if match.group(1) == ordinal.lower()
    ]

    assert len(anchors) == 1, (
        f"T-6 carries {len(anchors)} `{ordinal}` query-side member anchors, expected "
        f"1; it names {[match.group(1) for match in _MEMBER_ANCHOR.finditer(text)]}. With "
        f"none of them every arm below scans nothing, and with two it scans whichever "
        f"came first"
    )
    rest = text[anchors[0].start() :]
    following = [
        found
        for found in (
            *(
                match.start()
                for match in _MEMBER_ANCHOR.finditer(rest)
                if match.group(1) != ordinal.lower()
            ),
            rest.find(_BLOCK_END),
        )
        if found > 0
    ]
    assert following, (
        f"nothing ends the `{ordinal}` member block: T-6 carries neither another member "
        f"anchor after it nor `{_BLOCK_END}`. Without a terminator the slice runs to the "
        f"end of the entry and every arm below scans paragraphs that are not this "
        f"member's -- silently, and in the direction that passes. Give this module the "
        f"heading that now follows the block"
    )
    block = rest[: min(following)]

    assert _SIBLING_PREFIX not in block, (
        f"the `{ordinal}` member block carries `{_SIBLING_PREFIX}` citations, which "
        f"belong to the neighbouring member's table. The slice is reading two members' "
        f"figures as one, so every bound checked below could be the wrong one"
    )
    return block


def _table_rows(block: str) -> tuple[str, ...]:
    """*block*'s Markdown table rows, raw and in order."""
    return tuple(line for line in block.splitlines() if line.lstrip().startswith("|"))


def _module_assignment(source: pathlib.Path, name: str) -> ast.expr:
    """The expression assigned to *name* at *source*'s top level.

    The **expression**, not its value, because one of the claims below is about
    the expression: T-6 says ``MAX_EXCERPT_CHARS`` is *derived from*
    ``EXCERPT_CHARS`` "rather than respelled", and a respelling that happens to
    carry today's figure is indistinguishable from a derivation once both sides
    are evaluated. Reading the assignment is the only way to tell them apart, and
    it is the difference the entry is asserting.

    Both ``name: Final = ...`` and a bare ``name = ...`` are accepted, so the pin
    fails on the claim rather than on an annotation style somebody changed.
    """
    assigned: list[ast.expr] = []
    for node in ast.parse(source.read_text(encoding="utf-8"), filename=str(source)).body:
        if isinstance(node, ast.AnnAssign):
            if isinstance(node.target, ast.Name) and node.target.id == name and node.value:
                assigned.append(node.value)
        elif isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == name for target in node.targets
        ):
            assigned.append(node.value)

    assert len(assigned) == 1, (
        f"`{name}` is assigned {len(assigned)} times at the top level of "
        f"{source.name}, expected once. T-6's fifth-member table cites it, and a "
        f"citation naming a symbol the module does not define at module level sends a "
        f"reader auditing the bound nowhere"
    )
    return assigned[0]


def _module_literal(source: pathlib.Path, name: str) -> object | None:
    """The literal assigned to *name* at *source*'s top level, or ``None``.

    ``None`` for a name assigned something other than a written constant -- an
    f-string, a call -- which is a real answer here rather than a failure:
    :data:`_REFUSAL_SYMBOL` is built by interpolation and only its *existence* is
    the claim.
    """
    value = _module_assignment(source, name)
    return value.value if isinstance(value, ast.Constant) else None


def _as_number(symbol: str, value: object) -> float:
    """*value* as a figure, or a failure naming the symbol that stopped being one.

    Every bound the table puts a figure beside is a number a reader compares. A
    constant that became a callable, a tuple or a computed expression stops being
    comparable, and the honest failure is here -- naming which symbol -- rather
    than a ``TypeError`` raised inside a dict comprehension with no symbol in the
    message. ``bool`` is excluded because it is an ``int`` in Python and a flag
    read as a figure is exactly the confusion this exists to report.
    """
    assert isinstance(value, int | float) and not isinstance(value, bool), (
        f"`{symbol}` is `{value!r}`, a {type(value).__name__}, so T-6's figure for it "
        f"cannot be compared against anything. Either the constant became something "
        f"other than a number -- in which case the table's row has to say what it now is "
        f"-- or this module is reading the wrong symbol"
    )
    return float(value)


def _review_search_parameters() -> tuple[str, ...]:
    """Every parameter ``mcp/tools.py`` declares for the ``review.search`` tool.

    Located by the **published tool name** in the ``_tool`` decorator rather than
    by the Python function's name, so renaming the handler moves this pin with it
    while renaming the wire surface fails -- which is the right way round, since
    T-6's row is about the tool a caller calls.
    """
    tree = ast.parse(_TOOLS_MODULE.read_text(encoding="utf-8"), filename=str(_TOOLS_MODULE))
    handlers = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
        for decorator in node.decorator_list
        if isinstance(decorator, ast.Call)
        and any(
            keyword.arg == "name"
            and isinstance(keyword.value, ast.Constant)
            and keyword.value.value == _TOOL
            for keyword in decorator.keywords
        )
    ]

    assert len(handlers) == 1, (
        f"{_TOOLS_MODULE.name} registers {len(handlers)} tools named `{_TOOL}`, expected "
        f"1. Zero means the registration moved or was renamed and the arm below would "
        f"pass over an empty parameter list, which is what a tool with no time bound "
        f"also looks like"
    )
    arguments = handlers[0].args
    return tuple(
        parameter.arg
        for parameter in (
            *arguments.posonlyargs,
            *arguments.args,
            *arguments.kwonlyargs,
            *([arguments.vararg] if arguments.vararg else []),
            *([arguments.kwarg] if arguments.kwarg else []),
        )
    )


def test_t6_gives_review_search_its_own_bounds_block_and_cites_every_published_bound() -> None:
    """RED means T-6's table stopped naming a bound a caller is subject to.

    The block is the only place a reader learns what one ``review.search`` call
    can cost them, and it works by naming the symbol that applies each bound so
    the reader can go and check. A bound that exists in the code and nowhere in
    the table is a control the record does not claim -- harmless -- but a *new*
    bound landing without a row is the state this catches, because the next
    reader sizes the surface from a table that is short by one.

    An equality of population rather than a spot check: every ``MAX_*`` and
    ``DEFAULT_*`` name in ``mcp/review_search.__all__`` must be cited, so a
    seventh bound reddens on the day it lands. Each citation must carry the
    module path the entry writes, so a symbol mentioned elsewhere in T-6 -- the
    fourth member's table names ``MAX_PULL_REQUEST`` and ``MAX_FILTER_CHARS``
    too, under a different module -- cannot stand in for a row of this one.

    The premise comes first: a module publishing no bound at all would make the
    loop below iterate over nothing and report perfect coverage.
    """
    block = _member_block(_ORDINAL)
    rows = "\n".join(_table_rows(block))
    bounds = _published_bounds()

    assert bounds, (
        "`mcp/review_search.py` publishes no `MAX_*` or `DEFAULT_*` name at all, so "
        "the citation check below would pass over an empty population and say nothing "
        "about T-6's table"
    )
    assert rows, (
        f"the `{_ORDINAL}` member block carries no Markdown table row, so the bounds "
        f"table a reader sizes this surface from is gone: {block[:400]}"
    )

    uncited = [symbol for symbol in bounds if not _citation(symbol).search(rows)]
    unpathed = [
        symbol for symbol in bounds if f"{_MODULE_PATH}::{symbol}" not in rows and symbol in rows
    ]

    assert not uncited, (
        f"T-6's `{_ORDINAL}` member table does not cite {uncited}, which "
        f"`mcp/review_search.py` publishes as a bound on this tool. A reader sizes one "
        f"call from that table, so an uncited bound is one they will not know about -- "
        f"add a row naming the symbol and the figure, in the change that added it"
    )
    assert len(unpathed) < len(bounds), (
        f"no citation in the `{_ORDINAL}` member table carries `{_MODULE_PATH}::`, so "
        f"nothing distinguishes these rows from the fourth member's table, which names "
        f"`MAX_PULL_REQUEST` and `MAX_FILTER_CHARS` of its own: {rows[:400]}"
    )


#: The symbols T-6's fifth-member table puts a **figure** beside, measured
#: 2026-09-11. Six of the eight bounds the table ranges over.
#:
#: The two absentees are absent for their own reasons, and both are deliberate:
#: ``MAX_PULL_REQUEST`` is written as the expression a reader sizes it by rather
#: than as a decimal, and ``MAX_REVIEW_SEARCH_RESPONSE_CHARS`` is described by what
#: it bounds rather than by its number. Neither is covered by the drift comparison,
#: which is the honest bound and is why they are named here instead of left as a
#: gap in a set nobody wrote down.
_CITED_SYMBOLS: Final = frozenset(
    {
        "ADMISSION_WAIT_SECONDS",
        "DEFAULT_REVIEW_SEARCH_LIMIT",
        "MAX_CONCURRENT_SEARCHES",
        "MAX_EXCERPT_CHARS",
        "MAX_FILTER_CHARS",
        "MAX_REVIEW_SEARCH_LIMIT",
    }
)


def test_the_figures_t6_puts_beside_each_bound_are_the_live_ones() -> None:
    """RED means the record states a bound the code does not apply.

    Every figure in the table is a number somebody will act on: 50 rows, a 400
    character filter, a 280 character excerpt, four concurrent readers. A figure
    that drifted from the constant beside it is worse than an absent one, because
    a reader who checks it once stops checking.

    The figures are parsed out of the entry and compared against the live
    constants, so this fails on whichever side moved rather than against a third
    copy written here. ``MAX_PULL_REQUEST`` is checked through the expression the
    entry quotes rather than a decimal, because that is how the entry writes it
    and how a reader is meant to size it.

    ``MAX_CONCURRENT_SEARCHES`` and ``ADMISSION_WAIT_SECONDS`` live in
    ``mcp/tools.py`` and are read from its source rather than imported, for the
    reason ``test_threat_model_t19_claims.py`` records: a pin over the daemon's
    tool surface should not depend on that surface importing cleanly.

    **Which symbols carry a figure is pinned, not merely that some symbol does.**
    The drift check only ranges over the citations it finds, so a figure deleted
    from the table takes its own coverage with it: the entry stops stating that
    bound, the comparison stops making it, and both look like success. A floor of
    "at least one citation" is satisfied by a table that quotes one of eight. So
    the cited set is compared against :data:`_CITED_SYMBOLS` by equality --
    six of the eight today, and the two that carry no figure are named there with
    why. A citation lost reddens; a citation gained reddens too, and is answered by
    recording it, at which point the drift check covers it.
    """
    block = _member_block(_ORDINAL)
    rows = "\n".join(_table_rows(block))
    live: dict[str, float] = {
        name: _as_number(name, getattr(review_search, name)) for name in _published_bounds()
    } | {name: _as_number(name, _module_literal(_TOOLS_MODULE, name)) for name in _GATE_SYMBOLS}

    quoted = {
        symbol: found[0]
        for symbol in live
        if (found := _citation(symbol).findall(rows)) and found[0]
    }
    assert set(quoted) == set(_CITED_SYMBOLS), (
        f"T-6's `{_ORDINAL}` member table cites {sorted(quoted)}; this pin records "
        f"{sorted(_CITED_SYMBOLS)}.\n\n"
        f"LOST a citation {sorted(set(_CITED_SYMBOLS) - set(quoted))}: the entry no "
        f"longer states that bound, and the drift comparison below silently stops "
        f"covering it -- which reads as a pass. Restore the figure, or record here "
        f"that the entry deliberately stopped stating it.\n\n"
        f"GAINED a citation {sorted(set(quoted) - set(_CITED_SYMBOLS))}: welcome, and "
        f"add it to `_CITED_SYMBOLS` in the same commit so the next reader can see the "
        f"coverage grew rather than wondering.\n\n"
        f"Rows read: {rows[:400]}"
    )

    drifted = {
        symbol: (figure, live[symbol])
        for symbol, figure in quoted.items()
        if float(figure) != float(live[symbol])
    }

    assert not drifted, (
        "T-6's fifth-member table states figures the code does not apply:\n"
        + "\n".join(
            f"  {symbol}: the entry says {stated}, the constant is {actual}"
            for symbol, (stated, actual) in sorted(drifted.items())
        )
        + "\n\nWhichever side moved, the record and the code have to be brought back "
        "into step in one change: a reader sizing this surface from the table would be "
        "sizing it from a number nothing applies."
    )
    assert review_search.MAX_PULL_REQUEST == 2**63 - 1 and "`2**63 - 1`" in rows, (
        f"T-6 says `MAX_PULL_REQUEST` is the widest value the store's signed 64-bit "
        f"column holds and writes it as `2**63 - 1`; the constant is "
        f"{review_search.MAX_PULL_REQUEST}. The expression is what tells a reader the "
        f"bound is the column's width rather than a chosen ceiling, so both the "
        f"expression and the value have to agree with the column"
    )
    excerpt = _module_assignment(_REVIEW_SEARCH_MODULE, "MAX_EXCERPT_CHARS")

    assert review_search.MAX_EXCERPT_CHARS == EXCERPT_CHARS and "EXCERPT_CHARS" in rows, (
        f"T-6 says `MAX_EXCERPT_CHARS` is derived from `domain/retrieval.py::"
        f"EXCERPT_CHARS` rather than respelled; the two are "
        f"{review_search.MAX_EXCERPT_CHARS} and {EXCERPT_CHARS}"
    )
    assert isinstance(excerpt, ast.Name) and excerpt.id == "EXCERPT_CHARS", (
        f"`MAX_EXCERPT_CHARS` is assigned `{ast.unparse(excerpt)}`, not the name "
        f"`EXCERPT_CHARS`. T-6 says the bound is derived *rather than respelled*, and "
        f"the equality above cannot tell the two apart while the respelling carries "
        f"today's figure -- which is precisely the state that drifts the first time "
        f"either place is reworded, silently and in only one of them"
    )


def test_the_response_budget_t6_derives_still_evaluates_to_the_live_constant() -> None:
    """RED means the derivation the entry tells a reader to size by has gone stale.

    The block does something unusual and deliberate with this row: it refuses to
    let the figure stand alone. It writes
    ``MAX_REVIEW_SEARCH_LIMIT x (MAX_EXCERPT_CHARS + 3 + 17 x MAX_FILTER_CHARS)``
    and tells the reader to size the budget by the expression, because the 17 is
    itself derived -- it is the count of published keys beside ``excerpt``, so a
    field added to the shaper widens the budget by the change that adds it.

    That instruction is only worth following while the expression reproduces the
    constant. Both of its written terms are the ones that move: the ``3`` is the
    cut marker's length, and the ``17`` is
    ``_KEYS_BESIDE_THE_EXCERPT`` -- a field added to ``review_record`` changes it
    and nothing in the document notices. So the two terms are read out of the
    entry, checked against the live values they name, and then used to recompute
    the budget, which is asserted equal to the shipped constant.

    Recomputing as well as comparing the terms is not redundant: the terms could
    each be right while the entry's *shape* -- which factor multiplies which --
    had drifted, and a reader sizing the budget from a wrong shape gets a wrong
    number out of right inputs.
    """
    block = _member_block(_ORDINAL)

    written = _BUDGET_EXPRESSION.findall(block)
    assert len(written) == 1, (
        f"T-6's `{_ORDINAL}` member block writes the response budget's derivation "
        f"{len(written)} times in the form this pin reads, expected once. Zero means the "
        f"expression was replaced by a bare figure -- which is exactly what the row says "
        f"not to size by -- or reworded past this key: {block[:400]}"
    )
    marker, keys = (int(term) for term in written[0])

    assert marker == len(review_search._CUT_MARKER), (
        f"T-6 writes the cut marker as {marker} characters; `_CUT_MARKER` is "
        f"{review_search._CUT_MARKER!r}, {len(review_search._CUT_MARKER)} characters. A "
        f"cut excerpt is exactly `MAX_EXCERPT_CHARS + len(_CUT_MARKER)`, so the budget "
        f"the entry derives is understated by `MAX_REVIEW_SEARCH_LIMIT` times the "
        f"difference"
    )
    assert keys == review_search._KEYS_BESIDE_THE_EXCERPT, (
        f"T-6 writes {keys} published keys beside `excerpt`; "
        f"`_KEYS_BESIDE_THE_EXCERPT` is {review_search._KEYS_BESIDE_THE_EXCERPT}. That "
        f"count is derived from the two field-classification sets and the SEC-15 triple, "
        f"so a field added to `review_record` moves it -- and the entry has to say so, "
        f"because the row tells a reader to size the budget from this expression"
    )
    derived = review_search.MAX_REVIEW_SEARCH_LIMIT * (
        review_search.MAX_EXCERPT_CHARS + marker + keys * review_search.MAX_FILTER_CHARS
    )

    assert derived == review_search.MAX_REVIEW_SEARCH_RESPONSE_CHARS, (
        f"T-6's derivation evaluates to {derived} "
        f"and `MAX_REVIEW_SEARCH_RESPONSE_CHARS` is "
        f"{review_search.MAX_REVIEW_SEARCH_RESPONSE_CHARS}. The terms above each agree "
        f"with the code, so what drifted is the shape of the expression -- which factor "
        f"multiplies which -- and a reader following the entry's own instruction to size "
        f"by the expression gets the wrong number out of the right inputs"
    )


def test_the_excerpt_projection_t6_quotes_is_the_one_the_serving_select_carries() -> None:
    """RED means the record shows a reader SQL the store does not run.

    The excerpt row's whole argument is that the cut happens *inside SQLite*:
    ``substr`` in the ``SELECT`` means the daemon is never handed the whole
    fragment, whatever a repository author committed. The evidence for that
    argument is the projection the row quotes, and the two sides move
    independently -- a reviewer rewriting the cell can drift the quote, and a
    change to ``excerpt_columns`` can strand it.

    The quote is extracted from the entry and looked for in the live statement,
    rather than both being compared to a string written here, so this fails on
    whichever side moved instead of on a third copy nobody reads.
    """
    quoted = _QUOTED_PROJECTION.findall(_member_block(_ORDINAL))
    projection = excerpt_columns()

    assert len(quoted) == 1, (
        f"T-6's `{_ORDINAL}` member block carries {len(quoted)} backticked "
        f"`substr(t.content...)` quotes, expected 1, so this pin cannot say which "
        f"fragment is the claim: {quoted}"
    )
    assert projection, (
        "`excerpt_columns()` returns nothing, so the containment check below would hold "
        "for nothing and the entry's quote would be measured against no statement"
    )

    assert quoted[0] in projection, (
        f"T-6 quotes `{quoted[0]}` as the excerpt projection; `excerpt_columns()` is "
        f"`{projection}`. Whichever side moved, the row's argument -- that SQLite never "
        f"hands this process more than the bound per row -- is no longer evidenced, and "
        f"the response budget derived from that bound rests on it"
    )


def test_t6_keeps_recording_review_searchs_wall_clock_bound_as_not_taken() -> None:
    """RED means the conversion of an adversarial HIGH lost its recorded reasoning.

    ``review.search`` takes no per-call wall-clock bound. That was found as a
    HIGH and converted rather than fixed, and this project converts a HIGH only
    against reasoning recorded in the code or an ADR -- which is this row and the
    three grounds under it. Delete the row, or soften it into a bound that sounds
    like one, and the HIGH is not converted any more; it is merely unrecorded,
    and the next reader has no way to tell a deferral from an oversight.

    Two words are held because the row makes two claims at once: the Bound cell
    says ``nothing``, and the decision is spelled ``not taken``. A row that named
    a bound while still saying "not taken" would be self-contradictory, and one
    that said "nothing" without recording the deferral would read as an omission.

    The row is read out of the **sliced block**, because the fourth member's
    table carries a ``wall clock per call`` row of its own with different
    wording; a key on the Dimension cell alone reads whichever came first.
    """
    block = _member_block(_ORDINAL)
    rows = [row for row in _table_rows(block) if _WALL_CLOCK_DIMENSION in prose(row)]

    assert len(rows) == 1, (
        f"the `{_ORDINAL}` member table carries {len(rows)} `{_WALL_CLOCK_DIMENSION}` "
        f"rows, expected 1. Zero means the deferral row is gone, and with it the "
        f"recorded reasoning that converts the adversarial HIGH this member carries"
    )
    row = prose(rows[0])

    missing = [word for word in _DEFERRAL_WORDS if word not in row]

    assert not missing, (
        f"T-6's `{_ORDINAL}` member no longer says {missing} in its "
        f"`{_WALL_CLOCK_DIMENSION}` row: {rows[0][:300]}\n\n"
        f"If a bound landed, this row is not softened -- it is replaced, together with "
        f"the three grounds under it and the conversion they support. If it did not, the "
        f"HIGH is back to being unrecorded, which is the state this row exists to keep "
        f"the product out of."
    )


def test_review_search_takes_no_per_call_time_bound_while_t6_records_none() -> None:
    """RED means a wall-clock bound landed and T-6's deferral just went false.

    This is the arm that has never fired and is the reason the module exists. The
    row above is a claim about today's code: *nothing* bounds how long one
    ``review.search`` call may run. The day that stops being true, it stops being
    true here -- a ``timeout``, a ``deadline``, a ``maxSeconds`` on the tool's own
    parameter list -- and the entry's three grounds, which argue that the
    transport cannot carry such a bound, become an argument against something the
    product now does.

    So this is not a defect when it fires. It is the change meeting the record:
    whoever lands the bound rewrites the row, the grounds, and the two
    measurement sets that size what the deferral costs.

    Read from ``mcp/tools.py``'s syntax tree, and the parameter list is taken
    whole -- positional-only, positional, keyword-only, ``*args`` and ``**kwargs``
    alike -- because a bound smuggled in as a keyword argument bounds the call
    exactly as much as a declared one.
    """
    parameters = _review_search_parameters()

    assert parameters, (
        f"`{_TOOL}` declares no parameter at all, which is not a tool this record "
        f"describes -- the block's table is about `limit`, the filters and what they "
        f"cost. The extractor is what to fix before reading anything into the arm below"
    )

    bounding = sorted(
        parameter
        for parameter in parameters
        if any(word in parameter.lower() for word in _TIME_BOUND_NAMES)
    )

    assert not bounding, (
        f"`{_TOOL}` now declares {bounding}, which reads as a per-call time bound. "
        f"T-6's `{_ORDINAL}` member records the wall-clock bound as **not taken** and "
        f"argues in three grounds that the transport cannot carry one -- a sync tool's "
        f"worker thread outlives the cancelled await, the cost is inherent to the "
        f"non-ranked design, and the mitigations that apply are operator-side.\n\n"
        f"This is not a defect. It is the day the record moves: rewrite the row, the "
        f"grounds and the measurement sets beside them in the same change that lands the "
        f"bound. If the parameter is not a time bound, say so by naming it here."
    )


def test_the_gate_symbols_t6_cites_are_assigned_where_the_row_says() -> None:
    """RED means the concurrency row cites a gate nobody can find.

    The concurrency row is the only bound in the table that is not this tool's
    own module: the gate is sized in ``mcp/tools.py``, and a reader auditing
    whether ``review.search`` really has a semaphore of its own follows those
    three names there. A citation naming a symbol that was renamed or removed is
    evidence of nothing, and the row would then be describing a control by a name
    the code dropped.

    ``REVIEW_SEARCH_CAPACITY_REFUSAL`` is asserted to exist rather than to hold a
    value, because it is built by interpolation from the cap -- which is the
    property the refusal's own pin in
    ``tests/integration/test_mcp_tools.py`` holds. What matters here is that this
    member's refusal is a **third** constant rather than the fourth member's:
    a row citing ``FINDINGS_CAPACITY_REFUSAL`` would be describing another
    tool's gate.
    """
    block = _member_block(_ORDINAL)
    rows = "\n".join(_table_rows(block))

    for symbol in (*_GATE_SYMBOLS, _REFUSAL_SYMBOL):
        assert f"`{symbol}`" in rows, (
            f"T-6's `{_ORDINAL}` member table no longer cites `{symbol}`, so a reader "
            f"auditing this tool's admission gate is sent nowhere. The gate is the one "
            f"bound in the table that lives outside `mcp/review_search.py`, which is why "
            f"the row names it at all: {rows[:400]}"
        )
        _module_literal(_TOOLS_MODULE, symbol)
