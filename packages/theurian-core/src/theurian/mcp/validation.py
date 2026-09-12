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
#: in total. Sized *above* ``security/paths.py``'s 8 MiB
#: ``MAX_SOURCE_FILE_BYTES`` so a write-intent ``body`` at the cap ADR-0032
#: decision 3 assigns it is not refused here for being the size its own schema
#: permits: a bound that rejects what the published contract admits is the
#: failure ADR-0031 decision 1's table exists to prevent.
#:
#: It is *not* the same guard ``migration_loader``'s ``MAX_DOCUMENT_RENDERED_CHARS``
#: is. There, a YAML anchor aliased N deep expands a 500-byte file into millions
#: of rendered characters, so width is unbounded by the input's own size. JSON
#: has no aliases, so a request's rendered width is bounded by the bytes the
#: caller sent; what this adds is a limit this seam records and refuses at
#: rather than one only the transport knows.
MAX_PARAMS_RENDERED_CHARS: Final = 12 * 1024 * 1024

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
    request's own budget, :data:`MAX_PARAMS_RENDERED_CHARS`, since every
    caller-written segment is a key the walk already charged for.
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


def _rendered_width(value: object) -> int:
    """How many characters ``value`` contributes to a ``{instance!r}`` render.

    O(1) for every leaf a parsed JSON request can hold, which keeps the budget
    walk's cost the walk's own. An ``int`` reports its decimal digit count
    estimated from ``bit_length`` -- never ``str(value)``, which is quadratic for
    a giant integer and, past CPython's int-to-str limit, raises the very cost
    this bound exists to refuse. ``30103/100000`` rounds ``log10(2)`` up, so the
    estimate never under-charges.
    """
    if isinstance(value, str | bytes):
        return len(value)
    if isinstance(value, bool):
        # Matched before `int`, of which it is a subclass, so its one-bit
        # `bit_length` does not under-report "True"/"False".
        return len(repr(value))
    if isinstance(value, int):
        digits = (value.bit_length() * 30103) // 100_000 + 1
        return digits + 1 if value < 0 else digits
    # Every other leaf a parsed JSON request can hold -- a `float`, a `null` --
    # renders to a handful of characters, so its width is already bounded by
    # :data:`MAX_PARAMS_NODES` counting the node itself. Only a string and an
    # integer are unbounded per node, and those are the two charged above.
    return 0


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
        rendered += _rendered_width(value)
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
