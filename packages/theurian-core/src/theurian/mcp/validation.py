"""Check an MCP tool call's arguments against that tool's published input schema.

SEC-12 is "validate every MCP tool input against its published JSON Schema
before it reaches application code"; ADR-0031 is the decision that makes it
runnable. This module is the plain-Python half: it loads the published input
schemas and answers one question -- *may this tool call reach a handler?* The
seat that asks the question is an SDK ``ServerMiddleware`` wired where the
server is built (decision 2), deliberately not here, so that **nothing in this
module imports the MCP SDK** and the control stays drivable without the
``daemon`` extra installed.

Two properties shape everything below:

* **Fail-closed.** A tool name with no loaded schema is *refused*, never passed
  through (decision 5), and a set that cannot be loaded whole raises rather than
  serving the part of it that parsed. A loader that dropped the one file it
  could not read would leave a daemon that looks healthy while one tool's
  contract is silently gone.
* **Bounded refusals.** Every caller-written fragment a refusal names is escaped
  through ``repr`` and cut to :data:`MAX_ECHOED_FRAGMENT_CHARS`, and the
  assembled message is held under :data:`MAX_REFUSAL_CHARS` at construction, so
  a refusal cannot become an amplifier of the caller's own bytes (decision 4,
  and ``mcp/tools.py``'s ``_publishable``/``_bounded_message`` pair).

Reference resolution is offline: the registry is built from the local schemas
tree and carries no ``retrieve`` callable, so a ``$ref`` naming something the
tree does not hold fails closed instead of being fetched over the network --
the posture ``migration_loader`` takes for the installed migration schema
(issue #235). Unlike that module, every ``$ref`` is resolved *at load*, because
a reference that cannot be resolved must stop the daemon serving that tool at
all rather than surface once per request.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
from itertools import chain
from pathlib import Path
from types import MappingProxyType
from typing import Any, Final

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError, ValidationError
from referencing import Registry, Resource
from referencing.exceptions import CannotDetermineSpecification, Unresolvable

from theurian.domain.errors import TheurianError

#: The contents type a schema resource carries, spelled the way `jsonschema`'s
#: stubs spell it (JSON Schema admits a bare ``true``/``false`` as a whole
#: schema, hence the ``bool`` arm). The one edge here that forces the ``Any``
#: the house style otherwise rejects: ``referencing.Registry`` is invariant in
#: its contents type, so a registry built as anything narrower does not
#: typecheck against ``Draft202012Validator(..., registry=...)``. Every document
#: this module *reads* is narrowed to ``dict[str, object]`` by
#: :func:`_read_document` first; this alias is used only where a value crosses
#: into ``referencing``.
type SchemaContents = bool | Mapping[str, Any]

#: The filename suffix marking a published schema as *input* rather than
#: response side. ADR-0031 decision 1 fixes only that input schemas live beside
#: the response schemas under ``schemas/mcp/``; the loader needs to tell the two
#: apart by name, because a response schema has no ``x-theurian-tool`` key and
#: must not be mistaken for a tool contract that forgot one.
INPUT_SCHEMA_SUFFIX: Final = "-input.schema.json"

#: The key each input schema declares its *wire* tool name under. A filename
#: cannot carry it: ``knowledge.search`` is a legal MCP tool name and not a path
#: component to round-trip through, so the file-to-tool mapping is stated in the
#: document rather than inferred from where it sits.
TOOL_NAME_KEY: Final = "x-theurian-tool"

#: How much of one caller-written fragment -- a tool name, a path segment, a
#: refused key -- a refusal may quote back. Long enough to recognise a real
#: field name, short enough that a 10,000-character key is a bounded echo rather
#: than the payload. Tracks ``mcp/tools.py``'s ``_MAX_QUOTED_VALUE_CHARS``: the
#: two bound the same kind of thing at the same surface.
MAX_ECHOED_FRAGMENT_CHARS: Final = 120

#: The ceiling on a whole assembled refusal. Not a second truncation -- every
#: builder below bounds its own fragments, and this is the invariant that says
#: they all did, enforced by :meth:`InputRefusal.__post_init__` and recomputed
#: from the live templates and fragment cap by
#: ``test_input_validation.py::test_every_refusal_template_fits_the_ceiling``.
#: Cutting the assembled message instead would cut the remedy off the end, and
#: the remedy is the part that must survive.
MAX_REFUSAL_CHARS: Final = 800

#: What a bounded fragment ends with when it was cut. Never a prefix of what a
#: cut fragment can end with, so "the value ended here" and "the value was cut"
#: stay distinguishable in a transcript (``mcp/tools.py``'s ``_CUT_MARKER``).
_CUT_MARKER: Final = "… (cut)"

#: How deeply a request's arguments may nest, counting the arguments object as
#: level 1. ``jsonschema`` renders a failing instance with ``{instance!r}``
#: while building its own message, and that render recurses: measured
#: 2026-09-13 against ``jsonschema==4.26.0`` on CPython 3.13, arguments carrying
#: a 20,000-level nested array raised ``RecursionError`` -- *"maximum recursion
#: depth exceeded while getting the repr of an object"* -- out of
#: ``iter_errors``, while 5,000 levels still validated. That budget is CPython's
#: C recursion limit and is shared with the ambient call stack (the mechanism is
#: recorded on ``migration_loader._validate_document``), so the bound sits far
#: below the measured onset rather than just under it. A real call nests a
#: handful of levels: an arguments object holding a list of strings is 3.
MAX_PARAMS_NESTING: Final = 32

#: How many values a request's arguments may hold, counting keys, mapping values
#: and sequence elements alike. Bounds this module's own walk and the per-node
#: work ``jsonschema`` does after it, and is the limit this boundary *records*
#: -- a per-request transport bound is the T-6 family's, deferred with its
#: reasoning (ADR-0031, *What this does not close*, item 4).
MAX_PARAMS_NODES: Final = 100_000

#: How many characters of rendered scalar content a request's arguments may hold
#: in total, and **the ceiling a request actually meets over this transport**.
#: ``build_app`` passes ``max_request_body_size=MAX_REQUEST_BODY_BYTES``
#: (``daemon/server.py``, derived there from ``security/paths.py``'s
#: ``MAX_SOURCE_FILE_BYTES``), and that bound sits *above* this one, so a body
#: between the two caps arrives, is framed, and **can** meet this seam's bounded
#: refusal -- which names the tool and the limit it passed -- instead of the bare
#: ``413 Request body too large`` that the transport tier emits before any MCP
#: framing exists. "Can", not "does": an escape-heavy body is charged several
#: characters per wire byte and a plain one may satisfy a published
#: ``maxLength`` first, so which refusal a given body meets depends on what it
#: carries. This bound is reachable in the shipped default configuration either
#: way.
#:
#: **What holds this bound is the charge, not any property of JSON.** The two
#: caps count different things -- ``MAX_REQUEST_BODY_BYTES`` bounds the bytes a
#: caller may send, this one bounds the render work ``jsonschema`` may be asked
#: to do -- and no ordering between them makes one imply the other, because
#: ``{instance!r}`` escapes: a raw U+007F is one wire byte and four rendered
#: characters, a raw U+0600 is two bytes and six. A body at the transport cap
#: can therefore render to four times its own byte count. The bound holds
#: because :func:`_rendered_width` charges **every leaf at least the number of
#: characters that leaf contributes to the render** -- the escape classes and
#: their measured ratios are on that function -- so :func:`_unbounded` refuses
#: before the render is built. Do not re-derive this from the caps' ordering: "a
#: request's rendered width never exceeds the bytes the caller sent" reads true
#: and is false in both directions.
#:
#: **This constant is the ceiling on charged leaves, not on the whole render.**
#: What ``jsonschema`` renders is the instance, and an instance is its leaves
#: plus the punctuation holding them together -- braces, brackets, ``, ``,
#: ``: ``, and the quotes around each string, none of which any leaf is charged
#: for. That excess is bounded by :data:`MAX_PARAMS_NODES`, because it is a
#: fixed cost per node: measured 2026-09-15 over both the structured worst cases
#: and 300,000 random shapes, it never reaches **4 characters per node** (the
#: dict-of-string-keys and list-of-strings families converge on 4 from below;
#: the random search peaked at 3.64). So the render a request can actually reach
#: is ``MAX_PARAMS_RENDERED_CHARS + MAX_PARAMS_NODES * 4`` = **12,982,912
#: characters**, 1.032x this constant. Quote that composed figure wherever the
#: real ceiling matters; this constant alone under-states it.
#:
#: That ordering is the reconciliation **#669** asked for, and it is deliberate
#: rather than incidental. Until it landed, ``streamable_http_app`` was called
#: with no ``max_request_body_size``, so the SDK's own 4 MiB
#: ``DEFAULT_MAX_REQUEST_BODY_SIZE`` answered ``413`` at a third of this constant
#: and, *while the charge was a plain code-point count*, nothing could reach this
#: seam -- 4 MiB of one-byte characters is 4.19 M code points, a third of the
#: budget. That clause matters now that the charge counts escapes: 4 MiB of raw
#: U+007F is charged 16.78 M, so an escape-heavy body reaches this seam even
#: under the SDK's own default. The decision, its derivation from
#: ``MAX_SOURCE_FILE_BYTES``, and the encodings it still leaves meeting the
#: ``413`` are recorded on ``MAX_REQUEST_BODY_BYTES`` itself, which is the place
#: to read before moving either cap; ADR-0031's *Amendment 1* records the same
#: reconciliation from the decision's side, with what it left open.
#:
#: It is *not* the same guard ``migration_loader``'s ``MAX_DOCUMENT_RENDERED_CHARS``
#: is. There, a YAML anchor aliased N deep expands a 500-byte file into millions
#: of rendered characters, so width is unbounded by the input's own size. JSON
#: has no aliases, so a request's rendered width is bounded by the bytes the
#: caller sent *times a small constant* -- at most four, per the escape classes
#: on :func:`_rendered_width`, rather than the unbounded factor aliasing buys.
#: What this adds is a limit this seam records and refuses at rather than one
#: only the transport knows, and a factor of four is exactly the gap that makes
#: recording it necessary.
MAX_PARAMS_RENDERED_CHARS: Final = 12 * 1024 * 1024

#: How many code points :func:`_rendered_width`'s fallback reprs at a time once a
#: leaf is wider than this. The fallback's whole cost is the transient ``repr``
#: it builds, and that transient is denominated in *bytes*, not characters: PEP
#: 393 sizes a ``str`` by its widest member, so a repr whose output carries one
#: printable astral character is stored at 4 bytes per character rather than 1.
#: Reprring a 26 MB leaf whole therefore peaked far above what the
#: character-denominated records claimed. Slicing bounds it instead, and the
#: bound has **two** terms, because the same expression builds two strings: the
#: repr output, at most ten characters per code point, *and* the concatenated
#: slice ``_chunked_width`` reprs. Both can sit in the 4-byte kind at once, so
#: the ceiling is ``_CHUNK_CODE_POINTS * (10 + 1) * 4`` -- **~352 KiB** at this
#: size, whatever the leaf's width or kind -- and the loop stops as soon as the
#: running charge passes the remaining budget. That figure is the module's one
#: ceiling; :func:`_chunked_width` and :func:`_rendered_width` quote it and
#: nothing else.
#:
#: Wide enough that the exact single-repr path still covers every string a real
#: call carries, which matters because the chunked sum is a *bound* rather than
#: the exact render -- see :func:`_chunked_width`.
_CHUNK_CODE_POINTS: Final = 8192

#: The keywords whose own ``jsonschema`` message names the offending *keys* and
#: interpolates no instance value, so it can be quoted (bounded) instead of
#: re-derived. Read off ``jsonschema==4.26.0``'s ``_keywords.py``: both build
#: their message from ``extras_msg(sorted(...))`` over key names alone, and the
#: per-key sub-errors ``unevaluatedProperties`` builds are consumed and
#: discarded rather than yielded. Every other keyword's message carries
#: ``{instance!r}`` -- measured, a failing ``projectId`` pattern reports the
#: value verbatim -- which is why :func:`_detail` words those from the schema
#: side instead.
_KEYWORDS_THAT_NAME_KEYS: Final = frozenset({"additionalProperties", "unevaluatedProperties"})

_SCHEMA_REMEDY: Final = (
    "Call `tools/list` to read this tool's published inputSchema, then retry with a "
    "request that matches it."
)

_NO_SCHEMA_REMEDY: Final = (
    "Call `tools/list` for the tools this daemon serves; if this one belongs there, "
    "reinstall theurian to restore its published input schema."
)

#: Interpolated by :func:`_mismatch`. Held as a template so the ceiling test can
#: recompute the worst assembled length from the live prose and fragment cap.
MISMATCH_REFUSAL: Final = (
    "{tool}: the request does not match this tool's published input schema. "
    "At {location}: {detail}. " + _SCHEMA_REMEDY
)

#: Interpolated by :func:`_no_schema`. Says nothing a caller could not already
#: learn from ``tools/list``, so it does not become the "an error that fires for
#: one input and not another" channel SEC-13 closes elsewhere.
NO_SCHEMA_REFUSAL: Final = (
    "{tool}: this daemon publishes no input schema for that tool, so the request was "
    "refused rather than served. Nothing was read and nothing was changed. " + _NO_SCHEMA_REMEDY
)

#: Interpolated by :func:`_oversized`. ``{limit}`` and ``{unit}`` are this
#: module's own constants, never anything the caller sent.
OVERSIZED_REFUSAL: Final = (
    "{tool}: the request arguments hold more {unit} than this daemon will validate "
    "({limit}). Nothing was read and nothing was changed. " + _SCHEMA_REMEDY
)

#: Interpolated by :func:`_unusable_schema`. The schema loaded but could not be
#: applied to this request -- an installation fault, not the caller's -- so the
#: remedy names the operator's cure and the refusal says plainly that nothing
#: happened.
UNUSABLE_SCHEMA_REFUSAL: Final = (
    "{tool}: this build's published input schema for that tool could not be applied, so "
    "the request was refused. Nothing was read and nothing was changed. Reinstall "
    "theurian, or report it at https://github.com/theurian/theurian/issues."
)


class InputSchemaError(TheurianError):
    """The published input schemas could not be loaded into a usable set.

    Raised for the *set* rather than for one file, because ADR-0031 decision 5's
    fail-closed property is a property of the whole set. Defined here rather
    than in ``domain/errors.py`` for the reason ``mcp/tools.py``'s ``ToolError``
    is: this is the MCP composition root failing to read its own shipped
    artifacts, and no layer below names it.
    """

    def __init__(self, schema_path: Path | str, reason: str, remedy: str) -> None:
        self.schema_path = str(schema_path)
        self.remedy = remedy
        super().__init__(f"{self.schema_path!r} {reason}. {remedy}")


@dataclass(frozen=True, slots=True)
class InputRefusal:
    """One tool call refused before it reached a handler, and why.

    ``tool`` is the *bounded echo* of the name the caller asked for, not a
    validated identifier: a refusal can name a tool this daemon never registered
    -- that is decision 5's whole point -- and that name arrives from the wire.

    ``message`` is what a caller reads, and its length is checked here rather
    than trusted: every builder in this module bounds its own fragments, and
    this is where "they all did" is enforced, so a builder that grows a new
    unbounded fragment raises at construction instead of shipping an amplifier.
    Driven by ``test_input_validation.py::test_a_refusal_past_the_ceiling_cannot_be_built``.
    """

    tool: str
    message: str

    def __post_init__(self) -> None:
        if not self.message:
            raise ValueError("a refusal with no message names no cure; build one or raise")
        if len(self.message) > MAX_REFUSAL_CHARS:
            raise ValueError(
                f"a refusal of {len(self.message)} characters exceeds MAX_REFUSAL_CHARS "
                f"({MAX_REFUSAL_CHARS}); bound every caller-written fragment with _echo "
                f"before interpolating it"
            )


def _bounded(rendered: str, limit: int = MAX_ECHOED_FRAGMENT_CHARS) -> str:
    """``rendered`` cut to ``limit``, marked when it was cut."""
    if len(rendered) <= limit:
        return rendered
    return rendered[:limit] + _CUT_MARKER


def _echo(value: object) -> str:
    """One caller-written fragment, rendered safe to interpolate into a refusal.

    ``repr`` rather than a second escaping scheme, for the reason
    ``mcp/tools.py``'s ``_publishable`` gives: it escapes a lone surrogate, a NUL
    and the C0 controls in one operation a reader knows how to undo -- so a
    newline a caller sent cannot forge a line of this daemon's output, and a
    payload the wire encoder would refuse cannot reach it -- while leaving
    printable non-ASCII legible.
    """
    return _bounded(repr(value))


def _location(path: Iterable[object]) -> str:
    """Where in the arguments a rejection fired: every segment escaped, the join
    bounded once.

    A segment is a schema property name wherever the schema names its
    properties, and a *caller-written key* wherever a schema descends into one
    (``additionalProperties`` as a subschema, ``patternProperties``). Array
    indices are ``jsonschema``-derived integers. Both halves of that second case
    are the caller's to choose -- how long each key is *and* how many of them the
    path carries -- which is why the bound is on the assembled location rather
    than on a segment: a per-segment cut leaves
    :data:`MAX_PARAMS_NESTING` segments to concatenate, and the ceiling
    derivation is what showed that assembling several times the whole message
    ceiling.

    A per-segment cut *as well* is what an earlier draft carried, and it is not
    kept: with the join bounded it changes no output this module can produce, so
    nothing could drive it. The transient the join builds is bounded by the
    request's own budget, :data:`MAX_PARAMS_RENDERED_CHARS`: every
    caller-written segment is a key the walk already charged for, and
    :func:`_rendered_width` charges a string the width ``repr`` renders it as --
    which is exactly the ``repr(part)[1:-1]`` this line builds -- so a key's
    escapes fall inside that budget rather than outside it. A charge of
    ``len(part)`` would not bound this: a key of non-printable astral
    characters builds ten characters here for each one it was charged.
    """
    segments = [repr(part)[1:-1] if isinstance(part, str) else str(part) for part in path]
    return _bounded("/".join(segments)) or "<root>"


def _detail(error: ValidationError) -> str:
    """Why this rejection fired, worded so no caller-supplied *value* is echoed.

    ADR-0031 decision 4 is that a refusal names the offending key path and the
    constraint that rejected it and does not reproduce an arbitrary-length
    value, which splits the keywords two ways. For
    :data:`_KEYWORDS_THAT_NAME_KEYS` ``jsonschema``'s own message is already
    exactly that, so it is quoted through the bound rather than re-derived --
    re-deriving would mean recomputing which keys an ``allOf`` branch evaluated,
    a second description of the keyword's semantics that could disagree with the
    one that actually refused the request. Every other keyword's message
    interpolates ``{instance!r}``, so it is discarded and the refusal is worded
    from the schema side: the keyword and the value it demanded, both fixed by
    the published contract and bounded anyway.
    """
    if error.validator in _KEYWORDS_THAT_NAME_KEYS and error.validator_value is False:
        return _bounded(error.message)
    if isinstance(error.validator, str):
        return f"does not satisfy {error.validator!r} (expected {_echo(error.validator_value)})"
    # `Unset` when jsonschema raised with no keyword: no expectation to name, and
    # "the schema" rather than a sentinel's repr.
    return "does not satisfy the schema"


def _order(error: ValidationError) -> tuple[int, tuple[str, ...], str, str]:
    """A total ordering over one request's rejections, deepest path first.

    **Deepest first, because a closure keyword double-reports a failed branch.**
    ``unevaluatedProperties`` credits only the keys an ``allOf`` branch
    evaluated *successfully*, so a request whose ``projectId`` fails its pattern
    produces two errors -- the ``pattern`` failure at ``projectId`` and a
    root-level *"'projectId' was unexpected"* (measured 2026-09-13,
    ``jsonschema==4.26.0``, against ADR-0031 decision 1's composition).
    Preferring the shallower error would answer "unknown key" to a caller who
    sent the right key with the wrong value.

    **Total, because a tie broken by dict order is a bug.** Depth, then the path,
    then the keyword, then the message; two errors agreeing on all four are the
    same rejection said twice.
    """
    path = tuple(str(part) for part in error.absolute_path)
    return (-len(path), path, str(error.validator), error.message)


def _mismatch(tool: str, error: ValidationError) -> InputRefusal:
    return InputRefusal(
        tool=tool,
        message=MISMATCH_REFUSAL.format(
            tool=tool, location=_location(error.absolute_path), detail=_detail(error)
        ),
    )


def _oversized(tool: str, limit: int, unit: str) -> InputRefusal:
    return InputRefusal(
        tool=tool, message=OVERSIZED_REFUSAL.format(tool=tool, limit=limit, unit=unit)
    )


def _no_schema(tool: str) -> InputRefusal:
    return InputRefusal(tool=tool, message=NO_SCHEMA_REFUSAL.format(tool=tool))


def _unusable_schema(tool: str) -> InputRefusal:
    return InputRefusal(tool=tool, message=UNUSABLE_SCHEMA_REFUSAL.format(tool=tool))


def _iter_nodes(root: object) -> Iterator[tuple[object, int]]:
    """Every value inside ``root``, paired with its depth, without recursion.

    Iterative for the reason ``migration_loader``'s walk is: a recursive depth
    checker spends the very budget it exists to protect, so it would raise
    ``RecursionError`` on exactly the documents it is meant to refuse. Each
    child is yielded *before* it joins the frontier, so a consumer that stops at
    a budget stops the frontier growing too. ``str`` and ``bytes`` stay leaves --
    their elements are characters, not structure.
    """
    frontier: list[tuple[object, int]] = [(root, 1)]
    while frontier:
        value, depth = frontier.pop()
        if isinstance(value, Mapping):
            children: Iterable[object] = chain(value.keys(), value.values())
        elif isinstance(value, list | tuple | set | frozenset):
            children = value
        else:
            continue
        for child in children:
            yield child, depth + 1
            frontier.append((child, depth + 1))


def _chunked_width(value: str, remaining: int) -> int:
    """A bound on ``value``'s repr contribution, built without reprring it whole.

    Reprring a leaf whole costs a transient proportional to the leaf, and the
    proportion is in *bytes*: the output runs to ten characters per code point,
    each stored at up to four bytes once any printable astral character puts the
    result in PEP 393's widest kind. Reprring one slice at a time caps that
    transient at the slice, and stopping as soon as the running total passes
    ``remaining`` caps the *work* too: the caller refuses at that point, so the
    rest of the leaf is never read.

    **The cap has two terms, not one.** The line below builds two strings per
    slice -- the concatenation ``'"' + value[...]`` and the ``repr`` of it -- and
    both can be in the 4-byte kind at the same time, so the bound is
    ``_CHUNK_CODE_POINTS * (10 + 1) * 4`` = **360,448 bytes of payload**, ~352
    KiB, plus the two ``str`` object headers. Three readings of that one
    transient have been taken -- through the walk, directly on the arm, and on
    round 3's own instance -- and they are one range rather than three figures:
    **360,548 to 361,156 bytes**, every one of them the payload bound plus
    headers, the spread being where the widening character falls in its slice.
    The worst leaf is one of *non-printable* astral characters carrying a
    *printable* astral: the first makes the repr output ten characters per code
    point, the second forces both that output and the concatenated slice into
    the widest kind. An earlier record priced only the repr output
    (``* 10 * 4``, 320 KiB) and omitted the concatenation.

    **The slice is reprred in a forced quote context, and that is what makes the
    sum sound.** ``repr``'s choice of delimiter is a property of the whole
    string: one holding apostrophes and no ``"`` is rendered with ``"``
    delimiters and its apostrophes cost one character each, while any other
    string is rendered with ``'`` delimiters and they cost two. Summing naive
    per-slice reprs can therefore *under*-count -- a slice with no ``"`` scores
    its apostrophes at one while the whole string scores them at two.
    Prefixing each slice with a ``"`` removes the choice: every slice is then
    rendered with ``'`` delimiters, every apostrophe costs two, and no other
    character's width depends on context. The overhead that prefix adds is
    exactly three characters -- two delimiters and the ``"`` itself, which
    ``repr`` never escapes -- hence the ``- 3``; subtracting four would
    under-count by one per slice, which is the unsafe direction.

    So the result is exact except for a string carrying apostrophes and no
    ``"``, where it over-charges by one per apostrophe. Over-charging refuses a
    request the budget was sized to admit, so the threshold above which this runs
    is set past anything a real call carries, and every small string keeps the
    exact single-repr arm.
    """
    total = 0
    for start in range(0, len(value), _CHUNK_CODE_POINTS):
        total += len(repr('"' + value[start : start + _CHUNK_CODE_POINTS])) - 3
        if total > remaining:
            # Already past what the caller can accept; the refusal does not need
            # the exact number and the rest of the leaf is not worth reading.
            return total
    return total


def _rendered_width(value: object, remaining: int = MAX_PARAMS_RENDERED_CHARS) -> int:
    """How many characters ``value`` contributes to a ``{instance!r}`` render.

    **Every leaf is charged at least the number of characters it contributes to
    that render**, and that, not any property of JSON, is what holds
    :data:`MAX_PARAMS_RENDERED_CHARS`. Two things the invariant deliberately does
    *not* say. It is about a leaf's **contribution**, not about
    ``len(repr(leaf))``: the ``str`` arms exclude the two delimiting quotes
    ``repr`` puts around a string, because those are punctuation of the render
    rather than content of the leaf -- the ``bytes`` arm is the one asymmetry,
    charging its whole repr including the ``b''`` delimiters, which over-charges
    in the safe direction. And it is about *leaves*: a container is charged zero
    here because :func:`_iter_nodes` descends into it and charges its members, so
    the punctuation holding an instance together is counted by
    :data:`MAX_PARAMS_NODES` instead -- at most 4 characters per node, measured,
    giving the composed ceiling recorded on
    :data:`MAX_PARAMS_RENDERED_CHARS`. **Charging a leaf zero is how this
    invariant was broken once**: ``float`` and ``None`` fell through to a
    ``return 0`` justified as "bounded per node", and a request pairing a string
    at the budget with 99,995 full-precision floats rendered 15,182,796
    characters -- 1.207x the budget -- and was *admitted*. Bounded is not free;
    every leaf type is charged, and the final arm charges whatever a future
    parser hands this walk rather than enumerating what is expected.
    ``repr`` escapes: a leaf charged its own length is charged one character for
    something that renders as up to ten, and ``jsonschema`` then builds a
    message this seam never budgeted for. The escape classes, measured
    exhaustively over all 1,114,112 code points on CPython 3.13 -- each as
    *rendered characters per code point*, then per *raw wire byte*, which is the
    ratio the transport cap does not bound:

    * printable in any plane, the backslash aside -- **1** per code point, so
      1.00 per wire byte at worst (ASCII; printable non-ASCII renders narrower
      than it is sent).
    * a two-character escape -- exactly tab, newline, return and the backslash,
      plus an apostrophe when the string *also* holds a ``"``. ``repr`` switches
      to a ``"`` delimiter for a string carrying only apostrophes, and never
      escapes a ``"`` at all, so ``'`` is the one character whose width depends
      on the rest of the string. **2** per code point; 2.00 per wire byte for a
      raw ``'``, which JSON leaves alone.
    * ``\\xHH`` -- exactly the 64 code points U+0000-U+0008, U+000B-U+000C,
      U+000E-U+001F, U+007F-U+00A0 and U+00AD -- **4** per code point, and
      **4.00 per wire byte for a raw U+007F**, the worst ratio any character
      reaches. The C0 block below it cannot: JSON forbids those raw, so each
      costs at least two wire bytes.
    * ``\\uXXXX`` -- every other non-printable BMP code point, lone surrogates
      included -- **6** per code point; 3.00 per wire byte for a raw U+0600,
      2.00 for a three-byte one.
    * ``\\UXXXXXXXX`` -- non-printable astral -- **10** per code point, 2.50 per
      wire byte.

    That second column is why no ordering of the two caps substitutes for this
    charge: a body at :data:`~theurian.daemon.server.MAX_REQUEST_BODY_BYTES`
    renders to as much as four times its own byte count.

    ``str`` takes two arms, both *exact*. ``repr`` renders a printable character
    as itself and escapes every other one, so a string ``str.isprintable()``
    accepts that carries neither a backslash nor an apostrophe renders to
    exactly its own length -- verified exhaustively: printable and outside
    ``{'\\\\', "'"}`` implies a one-character render, with no exceptions, and a
    ``"`` is never escaped whichever delimiter ``repr`` picks. That arm builds
    nothing, and ``isprintable`` stops at the first non-printable character, so
    the clean text a real call carries costs three C-speed scans and no
    allocation. Measured 2026-09-15, median of five, on the whole
    :func:`_unbounded` walk: **0.00 ms** for a realistic ``knowledge.search``
    call, 8.8 ms for an ASCII leaf at :data:`MAX_PARAMS_RENDERED_CHARS`, and
    **17.9 ms for one at the transport cap** -- which is the number that
    matters, since the transport admits 2.08x this gate's budget in ASCII
    characters and a clean leaf is scanned whole before the budget refuses it.
    For scale, ``json.loads`` on the same at-budget body costs 11.4 ms.

    Everything else falls back to a ``repr``, exactly for a leaf at or under
    :data:`_CHUNK_CODE_POINTS` (``len(repr(value)) - 2``: ``repr`` always carries
    exactly two delimiting quotes, so that difference is the contribution rather
    than an estimate) and through :func:`_chunked_width` above it.

    **The fallback's cost is a transient denominated in bytes, and that is why it
    is chunked.** Reprring a leaf whole builds an output of up to ten characters
    per code point, and PEP 393 sizes a ``str`` by its *widest* member -- so the
    same escape-heavy leaf whose repr is pure ASCII at 1 byte per character
    becomes 4 bytes per character the moment one printable astral character is
    present. Character-denominated records missed that by 4x. Measured
    2026-09-15 on the fallback arm alone, for the widest leaf the transport
    admits: reprring it whole peaks at **100 MiB** (dense U+007F) to **400 MiB**
    (the same leaf with one emoji); chunked, the same two leaves peak at
    **0.04 MiB** and **0.15 MiB**.

    Those two are corroborations, not the ceiling, and it is worth saying what
    they were measured on: a dense-U+007F leaf, walked with the early exit
    disabled, whose single widening character sat in the final slice. U+007F
    renders four characters per code point rather than ten, so neither figure
    reaches the worst case -- the ceiling is
    ``_CHUNK_CODE_POINTS * (10 + 1) * 4``, **~352 KiB**, derived and measured on
    :func:`_chunked_width`, and it is the one figure to quote. Instrument:
    ``tracemalloc`` peak, which is the Python-heap question; ``ru_maxrss``
    answers a different one and is quoted beside its own figures on
    :data:`~theurian.daemon.server.MAX_REQUEST_BODY_BYTES`.

    Chunking pays in time as well, because :func:`_chunked_width` stops as soon
    as the running charge passes what the caller can still accept. On the same
    at-cap leaves the whole walk went from 82.5 ms to 9.9 ms (dense U+007F),
    119.1 ms to 10.8 ms (with an emoji) and 71.0 ms to 12.0 ms (a 2-byte
    non-printable); the fast path is unchanged at 17.9 ms. The worst shape left
    is a clean ASCII leaf with one non-printable at its end -- ``isprintable``
    scans it whole, then the chunked walk does too, because no prefix of it
    reaches the budget -- measured **29.3 ms**, down from 50.7 ms.

    O(1) for the other leaves, which keeps the budget walk's cost the walk's
    own. An ``int`` reports its decimal digit count estimated from
    ``bit_length`` -- never ``str(value)``, which is quadratic for a giant
    integer and, past CPython's int-to-str limit, raises the very cost this
    bound exists to refuse. ``30103/100000`` rounds ``log10(2)`` up, so the
    estimate never under-charges.
    """
    if isinstance(value, str):
        if value.isprintable() and "\\" not in value and "'" not in value:
            return len(value)
        if len(value) > _CHUNK_CODE_POINTS:
            return _chunked_width(value, remaining)
        return len(repr(value)) - 2
    if isinstance(value, int) and not isinstance(value, bool):
        # `bool` excluded rather than matched first: it is an `int` subclass whose
        # one-bit `bit_length` would under-report "True"/"False", and it is
        # charged by the final arm instead.
        digits = (value.bit_length() * 30103) // 100_000 + 1
        return digits + 1 if value < 0 else digits
    if isinstance(value, Mapping | list | tuple | set | frozenset):
        # Not a leaf: `_iter_nodes` descends into exactly these and charges every
        # member on its own, so a width here would double-count them. What a
        # container adds beyond its members is punctuation, and that is the
        # per-node constant the composed ceiling accounts for. This type list is
        # `_iter_nodes`'s own, and the two must not drift apart.
        return 0
    # Everything else, charged its whole repr rather than enumerated: `float` and
    # `None`, which a `return 0` once waved through (above); `bytes`, which no
    # `json.loads` output holds but whose escapes would otherwise go uncounted;
    # and whatever a future parser hands this walk. For `bytes` the charge
    # includes the `b''` delimiters, the one arm that over-charges rather than
    # matching the render exactly -- the safe direction, and the asymmetry the
    # invariant above names.
    return len(repr(value))


def _unbounded(tool: str, params: Mapping[str, object]) -> InputRefusal | None:
    """Refuse arguments past :data:`MAX_PARAMS_NESTING`, :data:`MAX_PARAMS_NODES`
    or :data:`MAX_PARAMS_RENDERED_CHARS`, before ``jsonschema`` is handed them.

    Before, not as a wider ``except`` around the validate call: past the
    interpreter's C recursion budget ``jsonschema`` cannot build even its own
    refusal message, and the ``RecursionError`` that follows is
    indistinguishable from a broken schema (mechanism on
    ``migration_loader._validate_document``; onset measured on
    :data:`MAX_PARAMS_NESTING`). A refusal rather than a raise, because
    oversized arguments are the caller's input and the caller gets the limit.
    """
    discovered = 1
    rendered = 0
    for value, depth in _iter_nodes(params):
        if depth > MAX_PARAMS_NESTING:
            return _oversized(tool, MAX_PARAMS_NESTING, "levels of nesting")
        discovered += 1
        if discovered > MAX_PARAMS_NODES:
            return _oversized(tool, MAX_PARAMS_NODES, "values")
        rendered += _rendered_width(value, MAX_PARAMS_RENDERED_CHARS - rendered)
        if rendered > MAX_PARAMS_RENDERED_CHARS:
            return _oversized(tool, MAX_PARAMS_RENDERED_CHARS, "characters of content")
    return None


@dataclass(frozen=True, slots=True)
class InputSchemaSet:
    """The published input schemas, keyed by the wire tool name each declares.

    Built by :func:`load_input_schemas`, which is where the fail-closed
    conditions are enforced; a set assembled some other way from a partial
    mapping would serve the tools it does hold and refuse the rest, which reads
    as "those tools are not published" rather than "this install is damaged".
    """

    validators: Mapping[str, Draft202012Validator]
    origins: Mapping[str, Path]

    @property
    def tool_names(self) -> tuple[str, ...]:
        """The wire tool names this set can check, in a deterministic order.

        Sorted, because this feeds the sweep ADR-0031 owes -- every registered
        tool resolves to a loaded schema -- and a set's iteration order reaching
        a failure message would make that sweep's output vary between runs.
        """
        return tuple(sorted(self.validators))

    def validate(self, tool_name: str, params: Mapping[str, object]) -> InputRefusal | None:
        """``None`` when this call may reach its handler, a refusal when it may not.

        The order of the gates is load-bearing: the tool lookup first, because
        refusing an unknown name costs nothing and needs no walk; the size bound
        next, because it is what makes handing ``params`` to ``jsonschema`` safe
        (:func:`_unbounded`); validation last.

        The four exceptions caught around the validate call are the *schema's*
        fault rather than this request's, and none of them is a
        ``ValidationError``, so each would otherwise escape as a raw traceback.
        ``Unresolvable`` is a reference that stopped resolving; ``RecursionError``
        a schema that recurses without terminating; ``ValueError`` and
        ``ArithmeticError`` are what ``jsonschema`` raises rendering or
        numeric-checking a value too large to process. All four answer with the
        operator's remedy, because a caller cannot fix this build's schemas.
        """
        echoed = _echo(tool_name)
        validator = self.validators.get(tool_name)
        if validator is None:
            return _no_schema(echoed)
        oversized = _unbounded(echoed, params)
        if oversized is not None:
            return oversized
        try:
            errors = sorted(validator.iter_errors(params), key=_order)
        except (Unresolvable, RecursionError, ValueError, ArithmeticError):
            return _unusable_schema(echoed)
        if not errors:
            return None
        return _mismatch(echoed, errors[0])


def _read_document(path: Path) -> dict[str, object]:
    """One schema file parsed into an object, or an error naming the cure.

    Every failure here is an installation fault rather than a user's, so each
    remedy points at the install. The arms mirror
    ``migration_loader._validator``'s, which closed the same class for the
    bundled migration schema (issue #205).
    """
    remedy = "Reinstall theurian to restore the published schemas."
    try:
        document: object = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise InputSchemaError(path, f"could not be read ({exc.strerror or exc})", remedy) from exc
    except UnicodeDecodeError as exc:
        raise InputSchemaError(path, f"is not valid UTF-8 ({exc})", remedy) from exc
    except json.JSONDecodeError as exc:
        raise InputSchemaError(path, f"is not valid JSON ({exc})", remedy) from exc
    except RecursionError as exc:
        raise InputSchemaError(path, "nests past the JSON parser's limit", remedy) from exc
    if not isinstance(document, dict):
        reason = f"parsed to a {type(document).__name__}, not a JSON object"
        raise InputSchemaError(path, reason, remedy)
    return document


def _registry(documents: Mapping[Path, dict[str, object]]) -> Registry[SchemaContents]:
    """Every schema in the tree, addressable by its ``$id`` and nothing else.

    No ``retrieve`` callable, so a ``$ref`` naming a resource this tree does not
    hold raises ``Unresolvable`` instead of taking ``jsonschema``'s default path
    of fetching it (``urllib.request.urlopen``). That default is an SSRF-shaped
    network read gated only on a shipped schema being replaced; issue #235
    closed it for the migration schema and the MCP boundary takes the same
    posture.
    """
    registry: Registry[SchemaContents] = Registry()
    resources: list[tuple[str, Resource[SchemaContents]]] = []
    for path in sorted(documents):
        document = documents[path]
        identifier = document.get("$id")
        if not isinstance(identifier, str) or not identifier:
            raise InputSchemaError(
                path,
                "declares no string '$id', so nothing can reference it offline",
                'Reinstall theurian, or add a "$id" to that schema.',
            )
        try:
            resource: Resource[SchemaContents] = Resource.from_contents(document)
        except CannotDetermineSpecification as exc:
            raise InputSchemaError(
                path,
                f"could not be read as a JSON Schema resource ({exc})",
                'Reinstall theurian, or give that schema a "$schema" this build knows.',
            ) from exc
        resources.append((identifier, resource))
    return registry.with_resources(resources)


def _resolve_every_ref(
    path: Path, document: dict[str, object], registry: Registry[SchemaContents]
) -> None:
    """Resolve every ``$ref`` in ``document`` now, so none can fail per-request.

    ``Draft202012Validator.check_schema`` does not follow references -- a
    ``$ref`` is a plain string to the metaschema -- so a schema naming a file the
    tree does not hold builds a validator that looks fine and fails on the first
    real request. Resolving here makes that a load failure instead, which is
    what decision 5 requires: the daemon must not come up serving a tool whose
    contract cannot be applied.

    References resolve against the document's own base URI. A schema introducing
    a nested ``$id`` would move that base for the subschemas beneath it, and this
    walk does not track it; the walk would have to grow a scope stack first.
    """
    root: Resource[SchemaContents] = Resource.from_contents(document)
    resolver = registry.resolver_with_root(root)
    for value, _ in chain([(document, 1)], _iter_nodes(document)):
        if not isinstance(value, Mapping):
            continue
        reference = value.get("$ref")
        if not isinstance(reference, str):
            continue
        try:
            resolver.lookup(reference)
        except Unresolvable as exc:
            raise InputSchemaError(
                path,
                f"names a $ref this build cannot resolve offline ({_bounded(str(exc))})",
                "Reinstall theurian; a published schema is missing or was replaced.",
            ) from exc


def _declared_tool_name(path: Path, document: Mapping[str, object]) -> str:
    declared = document.get(TOOL_NAME_KEY)
    remedy = f'Reinstall theurian, or add "{TOOL_NAME_KEY}": "<wire tool name>" to that schema.'
    if not isinstance(declared, str) or not declared.strip():
        reason = f"declares no non-empty {TOOL_NAME_KEY!r}, so it names no tool to serve"
        raise InputSchemaError(path, reason, remedy)
    return declared


def _checked_schema(path: Path, document: dict[str, object]) -> None:
    remedy = "Reinstall theurian to restore the published schemas."
    try:
        Draft202012Validator.check_schema(document)
    except SchemaError as exc:
        reason = f"is not a valid Draft 2020-12 schema ({_bounded(str(exc))})"
        raise InputSchemaError(path, reason, remedy) from exc
    except RecursionError as exc:
        reason = "nests past check_schema's safe recursion depth"
        raise InputSchemaError(path, reason, remedy) from exc


def load_input_schemas(schemas_dir: Path) -> InputSchemaSet:
    """Load every published input schema under ``schemas_dir`` into one set.

    An input schema is a file named ``*-input.schema.json``
    (:data:`INPUT_SCHEMA_SUFFIX`) declaring its wire tool name under
    :data:`TOOL_NAME_KEY`. Every other ``*.schema.json`` in the tree is read too,
    but only into the offline registry, so a per-tool schema can ``$ref``
    ``tool-context.schema.json`` the way ADR-0031 decision 1's composition
    requires.

    **Every failure is fatal to the whole set** -- a missing or empty tool name,
    a name two files both claim, an unreadable or unparseable file, a schema the
    metaschema rejects, a ``$ref`` that does not resolve offline. Dropping one
    entry instead would leave a daemon serving every other tool while the one
    with the broken contract is refused at dispatch, which reads as "that tool
    is not published" and is actually "this install is damaged".

    Args:
        schemas_dir: The directory holding the published MCP schemas. Searched
            recursively, so a referent may sit in a subdirectory.

    Returns:
        The set, keyed by wire tool name.

    Raises:
        InputSchemaError: On any of the conditions above; every instance carries
            a remedy naming the install.
    """
    documents = {path: _read_document(path) for path in sorted(schemas_dir.rglob("*.schema.json"))}
    registry = _registry(documents)
    validators: dict[str, Draft202012Validator] = {}
    origins: dict[str, Path] = {}
    for path in sorted(documents):
        if not path.name.endswith(INPUT_SCHEMA_SUFFIX):
            continue
        document = documents[path]
        tool_name = _declared_tool_name(path, document)
        if tool_name in origins:
            raise InputSchemaError(
                path,
                f"claims the tool name {tool_name!r}, which {origins[tool_name].name} "
                f"already claims",
                "Reinstall theurian; two published schemas cannot serve one tool.",
            )
        _checked_schema(path, document)
        _resolve_every_ref(path, document, registry)
        validators[tool_name] = Draft202012Validator(document, registry=registry)
        origins[tool_name] = path
    return InputSchemaSet(
        validators=MappingProxyType(validators), origins=MappingProxyType(origins)
    )
