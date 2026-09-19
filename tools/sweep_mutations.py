"""Mutations nobody wrote by hand, generated from one file's syntax tree.

Deliberately small. Three operator families -- comparison boundaries, boolean
literals, and ``and``/``or`` -- and no more. A generator that emits every mutation
it can think of produces a scheduled job nobody reads; these three are the ones
whose survival says something specific about the suite, because each of them
names a *branch* that some test was supposed to distinguish.

**The anchor is the hard part, not the operator.** ``tools/mutate.py`` takes an
exact ``--old`` string and requires it to occur once in the file. An anchor that
occurs twice makes the harness exit 2 and the night answers nothing; an anchor
that occurs *zero* times is worse, because (``mutate_edits._apply_edit``'s own
words) "a missing anchor produces a run that tests nothing while reporting
SURVIVED". So every
anchor here is checked for uniqueness by this module before a spec is written,
on a ladder: the expression as written, then the whole source line, then the
candidate is dropped -- and a drop is *reported*, never swallowed, because a
generator that silently dropped everything would leave the night reading clean.

**Positions come from tokens, not from unparsing.** The replacement text is the
original bytes with exactly one token swapped, so a mutation cannot reformat a
line, cannot re-quote a string, and cannot change a precedence the source spelled
out. ``ast`` alone cannot do this: operator nodes (:class:`ast.Lt`,
:class:`ast.And`) carry no position at all, so the token stream is what says
where the ``<`` actually is. The two coordinate systems differ -- ``ast``
column offsets are UTF-8 *byte* offsets into the line while ``tokenize``
columns are character offsets -- and this repository's sources do contain
non-ASCII text, so :func:`_offset` converts rather than assuming.
"""

from __future__ import annotations

import ast
import hashlib
import io
import tokenize
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Final

from sweep_census import Slot, SweepError

#: Mutations one run asks of one file.
#:
#: One, and the reason is the run's budget rather than the file's depth. The
#: harness spends one walk on its unmutated control and one per mutation, so the
#: seven walks a run can afford buy six mutations; spending them one each across
#: :data:`sweep_census.BLOCK_SIZE` files covers the census in ``ceil(len / 6)``
#: runs instead of ``len`` -- 24 instead of 142 at the census of 2026-09-19. Six
#: on one file bought depth in one module and left every other module in the
#: census untouched for that run.
#:
#: Depth is not lost, it is spread over time: :meth:`Generated.at_lap` indexes a
#: file's candidates by the visit number, so the second visit to a file asks its
#: second question.
MUTATIONS_PER_FILE: Final = 1

#: Comparison boundaries: the off-by-one a test either distinguishes or does not.
_COMPARISON_FLIP: Final = {"<": "<=", "<=": "<", ">": ">=", ">=": ">", "==": "!=", "!=": "=="}

#: ``and``/``or``: the guard that either needs both conditions or does not.
_BOOLEAN_OPERATOR_FLIP: Final = {"and": "or", "or": "and"}

#: Boolean literals: a default, a flag, a hard-coded answer.
_BOOLEAN_CONSTANT_FLIP: Final = {"True": "False", "False": "True"}

_COMPARISON_TOKEN: Final = {
    ast.Lt: "<",
    ast.LtE: "<=",
    ast.Gt: ">",
    ast.GtE: ">=",
    ast.Eq: "==",
    ast.NotEq: "!=",
}

_BOOLEAN_TOKEN: Final = {ast.And: "and", ast.Or: "or"}

#: Label fragments. A label reaches shell-free argv, a JSON spec and an issue
#: body, so it is restricted to ``[a-z0-9-]`` by construction rather than escaped
#: at each destination.
_SLUG: Final = {
    "<": "lt",
    "<=": "le",
    ">": "gt",
    ">=": "ge",
    "==": "eq",
    "!=": "ne",
    "and": "and",
    "or": "or",
    "True": "true",
    "False": "false",
}

_WANTED_TOKENS: Final = frozenset(
    {*_COMPARISON_FLIP, *_BOOLEAN_OPERATOR_FLIP, *_BOOLEAN_CONSTANT_FLIP}
)


@dataclass(frozen=True)
class _Token:
    """One token of interest, in absolute character offsets into the source."""

    text: str
    start: int
    end: int


#: One operator token, the expression that encloses it, and what it becomes.
_Hit = tuple[_Token, tuple[int, int], str]

#: Resolves an expression node to its half-open character span in the source.
_Span = Callable[[ast.expr], tuple[int, int]]


@dataclass(frozen=True)
class Candidate:
    """One mutation, ready to be written into a ``tools/mutate.py`` spec."""

    label: str
    path: str
    old: str
    new: str
    line: int
    swapped_from: str
    swapped_to: str
    #: Which rung of the widening ladder produced ``old`` -- reported so a reader
    #: of the filed issue knows whether the anchor is the expression itself or
    #: the line that carries it.
    anchor: str


@dataclass(frozen=True)
class Skip:
    """A mutation the generator could describe but could not anchor."""

    line: int
    swapped_from: str
    reason: str


@dataclass(frozen=True)
class Generated:
    """Everything one file yielded: what can run, and what was dropped."""

    path: str
    candidates: tuple[Candidate, ...]
    skipped: tuple[Skip, ...]

    def at_lap(self, lap: int) -> Candidate | None:
        """This visit's mutation, or ``None`` when the file is barren.

        Indexed by the visit and not by the date, for the reason
        :data:`sweep_census.Slot.lap` records: consecutive visits to one index
        differ by exactly one lap, so ``lap % len`` walks a file's candidates one
        per visit and returns to the first only after all of them have run. A
        date-indexed pick has no such property -- the gap between two visits to
        one file is the cover length, and any selector keyed on the run number
        strides by that gap and can close over a subgroup, which is the defect
        PR #759 removed from the rotation and this would have reintroduced one
        level down.

        ``items[0]`` was the previous behaviour at a budget of one, and it made
        every visit to a file ask the same question for ever. Measured
        2026-09-19 through :func:`sweep_census.block` against the census of
        that day, over 60 runs: of the 118 revisited files that offer a candidate, all 112
        that offer **more than one** drew a different one on the next visit, and
        the remaining 6 offer exactly one and cannot. That exception is the
        reason the claim is written this way rather than as "a later visit asks
        a different question".
        """
        if not self.candidates:
            return None
        return self.candidates[lap % len(self.candidates)]


@dataclass(frozen=True)
class Attempt:
    """What one block slot yielded: the file, what it offers, and this visit's pick."""

    slot: Slot
    generated: Generated
    #: ``None`` when the file is barren. A barren slot buys no walk, so it costs
    #: the run nothing but its place in the block.
    candidate: Candidate | None

    @property
    def path(self) -> str:
        return self.slot.path


def _line_index(source: str) -> tuple[tuple[str, ...], tuple[int, ...]]:
    """Each source line, and the absolute offset it starts at.

    Split on ``"\n"`` and not with :meth:`str.splitlines`, because the two
    disagree about what a line is and only one of them agrees with the
    tokenizer. ``splitlines`` also breaks on ``\x0b``, ``\x0c``, ``\x1c``
    -``\x1e``, ``\x85``, ``\u2028`` and ``\u2029``; Python's own grammar
    breaks on ``\n``, ``\r\n`` and ``\r``, and treats a form feed as ordinary
    whitespace *within* a line -- it is the conventional page separator in
    Python source.

    One form feed was therefore enough to put this module's line numbering one
    ahead of ``ast``'s for the rest of the file, and every offset computed from
    it lands somewhere else. The reviewer's reproduction anchored a mutation on a
    docstring while the label claimed a ``True``/``False`` flip in code: a
    SURVIVED verdict fabricated out of a mutation that never touched a branch,
    and its mirror, a KILLED that holds nothing. Correct today only because no
    file in the census carries such a character (0 of 139, measured 2026-09-16).

    ``\r\n`` needs no special case: the ``\r`` stays at the end of the line
    where every column offset is already past it. A lone ``\r`` would still
    diverge, and nothing here can reach one -- :func:`sweep_census.census` and
    the driver read sources through :meth:`Path.read_text`, whose universal
    newline translation turns both into ``\n`` before this sees them.
    """
    lines = source.split("\n")
    starts: list[int] = []
    running = 0
    for line in lines:
        starts.append(running)
        running += len(line) + 1  # the separator `split` removed
    return tuple(lines), tuple(starts)


def _offset(lines: Sequence[str], starts: Sequence[int], lineno: int, column: int) -> int:
    """An ``ast`` position -- 1-based line, UTF-8 byte column -- as a character offset.

    The byte/character distinction is not pedantry here: this repository's
    sources carry CJK text (ADR-0023's tokenization evidence, among others), and
    on such a line every ``ast`` column past the non-ASCII text is larger than
    the character index. Slicing with it would anchor the mutation at the wrong
    place, which the uniqueness check would then wave through whenever the wrong
    place happened to be unique.
    """
    line = lines[lineno - 1]
    return starts[lineno - 1] + len(line.encode("utf-8")[:column].decode("utf-8"))


def _significant_tokens(source: str, starts: Sequence[int]) -> tuple[_Token, ...]:
    """Every token one of the three operator families could mutate.

    Filtering on the token *type* is what keeps the search off text that only
    looks like an operator: a ``<`` inside a comment is a ``COMMENT`` token and a
    ``True`` inside a docstring is a ``STRING`` token, and neither can be
    mistaken for the real thing here.
    """
    collected: list[_Token] = []
    try:
        for token in tokenize.generate_tokens(io.StringIO(source).readline):
            if token.type not in (tokenize.OP, tokenize.NAME) or token.string not in _WANTED_TOKENS:
                continue
            collected.append(
                _Token(
                    text=token.string,
                    start=starts[token.start[0] - 1] + token.start[1],
                    end=starts[token.end[0] - 1] + token.end[1],
                )
            )
    except tokenize.TokenError as error:
        raise SweepError(f"the target tokenizes differently than it parses: {error}") from error
    return tuple(collected)


def _comparison_targets(node: ast.Compare) -> Iterator[tuple[ast.expr, ast.expr, str]]:
    """Each mutable comparison in a chain, with the operands that bracket it.

    A chain (``lower <= value < upper``) is one :class:`ast.Compare` carrying two
    operators, and each is its own candidate: flipping the first alone is a real
    and different question from flipping the second.
    """
    left = node.left
    for operator, right in zip(node.ops, node.comparators, strict=True):
        text = _COMPARISON_TOKEN.get(type(operator))
        if text is not None:
            yield left, right, text
        left = right


def _boolean_targets(node: ast.BoolOp) -> Iterator[tuple[ast.expr, ast.expr, str]]:
    """Each ``and``/``or`` keyword in a chain, with the operands that bracket it."""
    text = _BOOLEAN_TOKEN[type(node.op)]
    for earlier, later in zip(node.values, node.values[1:], strict=False):
        yield earlier, later, text


def _sole_token(tokens: Sequence[_Token], start: int, end: int, text: str) -> _Token:
    """The one token of the given text between two operands.

    Raising rather than skipping is deliberate. Every way this can fail is the
    generator's model of the file being wrong -- a position system misread, an
    operand span that does not bracket its own operator -- and a run that quietly
    emitted fewer mutations because of it would still report a clean night. The
    driver turns this into exit 1, which is the signal the workflow alarms on.
    """
    matches = [
        item for item in tokens if start <= item.start and item.end <= end and item.text == text
    ]
    if len(matches) != 1:
        raise SweepError(
            f"expected exactly one {text!r} token between offsets {start} and {end}, "
            f"found {len(matches)}: the generator's reading of this file is wrong"
        )
    return matches[0]


def _line_span(source: str, span: tuple[int, int]) -> tuple[int, int]:
    """The full line or lines a span sits on, trailing newline excluded."""
    start = source.rfind("\n", 0, span[0]) + 1
    end = source.find("\n", span[1])
    return start, len(source) if end == -1 else end


def _anchored(
    source: str, span: tuple[int, int], token: _Token, replacement: str
) -> tuple[str, str, str] | None:
    """The narrowest unique anchor for this token, or ``None`` if there is none.

    The ladder, in order: the expression as written, then the whole line or lines
    it sits on. Both are counted against the *file*, because that is what
    ``tools/mutate.py`` counts.
    """
    for rung, bounds in (("expression", span), ("line", _line_span(source, span))):
        start, end = bounds
        old = source[start:end]
        if source.count(old) != 1:
            continue
        new = old[: token.start - start] + replacement + old[token.end - start :]
        return rung, old, new
    return None


def _hits(tree: ast.Module, tokens: Sequence[_Token], span: _Span) -> list[_Hit]:
    """Every operator token this file offers, with the expression that encloses it."""
    found: list[_Hit] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Compare):
            for left, right, text in _comparison_targets(node):
                token = _sole_token(tokens, span(left)[1], span(right)[0], text)
                found.append((token, span(node), _COMPARISON_FLIP[text]))
        elif isinstance(node, ast.BoolOp):
            for earlier, later, text in _boolean_targets(node):
                token = _sole_token(tokens, span(earlier)[1], span(later)[0], text)
                found.append((token, span(node), _BOOLEAN_OPERATOR_FLIP[text]))
        elif isinstance(node, ast.Constant) and isinstance(node.value, bool):
            literal = "True" if node.value else "False"
            start, end = span(node)
            token = _sole_token(tokens, start, end, literal)
            found.append((token, (start, end), _BOOLEAN_CONSTANT_FLIP[literal]))
    return found


def _file_slug(path: str) -> str:
    """Six hex characters standing for one census path, inside a label.

    A block hands the harness one mutation from each of several files at once,
    and a label is the only key joining a harness verdict back to its mutation.
    Without a file discriminator two files collide on the first one that offers
    the same operator on the same line at the same candidate index -- which is
    ordinary, not exotic, because the index counts each file's own hits from
    zero. A digest rather than the path: a label reaches shell-free argv, a JSON
    spec and an issue body, and is restricted to ``[a-z0-9-]`` by construction
    rather than escaped at each destination.
    """
    return hashlib.sha256(path.encode("utf-8")).hexdigest()[:6]


def candidates(path: str, source: str, *, on: date) -> Generated:
    """Every mutation this file can carry, in source order, plus what was dropped.

    Source order rather than ``ast.walk``'s breadth-first order: both are
    deterministic, and only one of them makes a candidate index mean "position
    in the file" rather than "position in the tree walk" -- which is what
    :meth:`Generated.at_lap` indexes into.

    The index inside a label counts *every* operator token the file offers, not
    only the anchorable ones, so a label keeps naming the same source position
    even after an edit elsewhere in the file makes some neighbour anchorable.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError as error:
        raise SweepError(f"{path} does not parse, so the sweep cannot read it: {error}") from error

    lines, starts = _line_index(source)
    tokens = _significant_tokens(source, starts)

    def span(node: ast.expr) -> tuple[int, int]:
        end_lineno = node.end_lineno if node.end_lineno is not None else node.lineno
        end_column = node.end_col_offset if node.end_col_offset is not None else node.col_offset
        return (
            _offset(lines, starts, node.lineno, node.col_offset),
            _offset(lines, starts, end_lineno, end_column),
        )

    emitted: list[Candidate] = []
    skipped: list[Skip] = []
    ordered = sorted(_hits(tree, tokens, span), key=lambda hit: hit[0].start)
    for index, (token, node_span, replacement) in enumerate(ordered):
        line = source.count("\n", 0, token.start) + 1
        anchored = _anchored(source, node_span, token, replacement)
        if anchored is None:
            skipped.append(Skip(line=line, swapped_from=token.text, reason="anchor-not-unique"))
            continue
        rung, old, new = anchored
        emitted.append(
            Candidate(
                label=(
                    f"sweep-{on:%Y-%m-%d}-{_file_slug(path)}-{index:02d}-"
                    f"{_SLUG[token.text]}-to-{_SLUG[replacement]}-l{line}"
                ),
                path=path,
                old=old,
                new=new,
                line=line,
                swapped_from=token.text,
                swapped_to=replacement,
                anchor=rung,
            )
        )
    return Generated(path=path, candidates=tuple(emitted), skipped=tuple(skipped))


def for_block(
    slots: Sequence[Slot], source_of: Callable[[str], str], *, on: date
) -> tuple[Attempt, ...]:
    """What each slot in this run's block offers, and which mutation it hands over.

    Every slot is read, including the barren ones: a block member with nothing to
    mutate is a fact about the block worth filing, and the previous single-file
    form could only express it by advancing past it. A file of constants,
    dataclasses and SQL text is ordinary here -- ``review_search_sql.py`` is one,
    holding no comparison, no boolean literal and no ``and``.

    A barren slot buys no walk, so the run's cost falls with it rather than being
    spent elsewhere: the freed walk is **not** reassigned to another file's second
    candidate. One mutation per file per run is what makes a verdict's meaning
    independent of which neighbours the block happened to draw, and what keeps the
    reproduction instruction a function of the date alone.

    Raising when the *whole* block is barren, rather than reporting it clean: a
    run that asked nothing has measured nothing, and "0 survived" out of it reads
    exactly like a run that asked six questions and got six answers.
    """
    attempts: list[Attempt] = []
    for slot in slots:
        generated = candidates(slot.path, source_of(slot.path), on=on)
        attempts.append(
            Attempt(slot=slot, generated=generated, candidate=generated.at_lap(slot.lap))
        )
    if not any(attempt.candidate is not None for attempt in attempts):
        raise SweepError(
            f"no file in a block of {len(slots)} yielded a single anchorable mutation; "
            "the sweep has nothing to run and must not report a clean run"
        )
    return tuple(attempts)


def spec_entries(picked: Sequence[Candidate]) -> list[dict[str, str]]:
    """The exact document ``tools/mutate.py --spec`` reads."""
    return [
        {"label": item.label, "file": item.path, "old": item.old, "new": item.new}
        for item in picked
    ]
