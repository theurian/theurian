"""Readers ADR-0037's three pin modules share (#804).

``test_adr_0037_claims.py`` holds the constants the ADR measured;
``test_adr_0037_emission_walk.py`` holds the walk over everything the export
emits; ``test_adr_0037_knowledge_get_bound.py`` holds the second instrument of
the disclosure bound. They read the same document, build the same revision and
measure ``knowledge.get``'s additions the same way, and a copy of any of those
readers is a fix that lands in whichever file its author remembered -- which is
the failure ``ast_keys`` was extracted for.

Lives beside ``ast_keys`` and ``threat_model_claims`` at the tests root, which is
what makes a bare ``from adr_0037_support import ...`` resolve from a test
module.
"""

from __future__ import annotations

import ast
import importlib.util
import pathlib
from datetime import UTC, datetime
from typing import Final, NamedTuple

from theurian.domain.enums import KnowledgeKind, KnowledgeStatus, Sensitivity, TrustLevel
from theurian.domain.identifiers import ItemId, MigrationId, ProjectId, RevisionId
from theurian.domain.knowledge import KnowledgeRevision, RevisionMetadata, SourceAnchor
from theurian.domain.values import MARKDOWN, ValidityPeriod

#: ``parents[3]`` is ``.../tests/`` -> ``theurian-core`` -> ``packages`` -> root.
REPO_ROOT: Final = pathlib.Path(__file__).resolve().parents[3]

ADR_0037: Final = REPO_ROOT / "docs" / "adr" / "0037-okf-is-the-knowledge-layer-interchange.md"

NOW: Final = datetime(2026, 9, 24, 12, 0, tzinfo=UTC)

#: Every field published *per source anchor*. The ADR's `of exactly` list.
SERVED_ANCHOR_FIELDS: Final[frozenset[str]] = frozenset(
    {"provider", "sourceUri", "repository", "commitSha", "filePath", "lineStart", "lineEnd"}
)


def collapsed(text: str) -> str:
    """Markdown emphasis and code markers dropped, whitespace runs flattened.

    ADR-0037 is line-wrapped Markdown, so every sentence pinned against it breaks
    across source lines and a raw substring match would miss the real wording and
    pass vacuously.

    ``*`` and backticks go with the whitespace because they carry no claim.
    Bolding one more field name inside the served-payload enumeration would
    otherwise redden a claim that did not move -- the same failure the vocabulary
    pin avoids by splitting its citation into its own clause. Applied to the
    fragment as well as to the document, so a pin can still be written in the
    ADR's own markup.
    """
    return " ".join(text.replace("*", "").replace("`", "").split())


def flowed() -> str:
    """The ADR's line wrapping flattened, **backticks kept**.

    :func:`collapsed` is the wrong reader for an enumeration whose members are
    code spans: it drops the very markers that say where one member ends. This
    is the reader for parsing a list out of prose.
    """
    return " ".join(ADR_0037.read_text(encoding="utf-8").split())


def adr() -> str:
    return collapsed(ADR_0037.read_text(encoding="utf-8"))


def assert_the_adr_states(fragment: str, *, because: str) -> None:
    """The prose half, in one helper so every claim reports the same way."""
    assert collapsed(fragment) in adr(), (
        f"ADR-0037 no longer states:\n\n  {fragment}\n\n{because}\n\n"
        f"The fact half is in the same module as this one. If it is GREEN, nothing in "
        f"the tree moved and the document is what gets restored; if it is RED, the "
        f"constant moved and the sentence has to move with it."
    )


def markdown_table(*, after: str) -> tuple[tuple[str, ...], ...]:
    """The data rows of the first Markdown table at or after a line starting ``after``.

    Anchored on a line prefix and never on a line number: every paragraph added
    above a table moves its lines, and a pin that has to be renumbered is a pin
    that gets updated without being read. The prefix is matched against the
    *stripped* line, as every anchored read in these two modules does, so a table
    that becomes a list item's child stays readable.
    """
    lines = ADR_0037.read_text(encoding="utf-8").splitlines()
    anchored = [index for index, line in enumerate(lines) if line.strip().startswith(after)]

    assert anchored, f"ADR-0037 has no line starting {after!r}; this parse anchors on it."

    table: list[str] = []
    for line in lines[anchored[0] :]:
        stripped = line.strip()
        if stripped.startswith("|"):
            table.append(stripped)
        elif table:
            break

    assert len(table) >= 3 and set(table[1]) <= {"|", " ", ":", "-"}, (
        f"the first table after {after!r} is not a header, a `:--` separator and at "
        f"least one row. The parse below would read prose as rows, and a walk over "
        f"rows it invented asserts nothing."
    )
    return tuple(tuple(cell.strip() for cell in row.strip("|").split("|")) for row in table[2:])


def revision() -> KnowledgeRevision:
    """One approved revision whose anchor carries **every** field a store can hold.

    The anchor's ``blob_sha`` and ``external_id`` are populated deliberately. An
    anchor that left them ``None`` would make "the payload does not publish them"
    pass for the wrong reason -- the fixture could not produce the shape its own
    assertion is about -- so the values are set and asserted present before the
    payload's key set is read.
    """
    return KnowledgeRevision.create(
        revision_id=RevisionId("01K1REV00101234567890ABCDE"),
        item_id=ItemId("architecture.auth-policy"),
        project_id=ProjectId("demo"),
        migration_id=MigrationId("01K1MAG00101234567890ABCDE"),
        title="Authentication and authorization policy",
        body="Every call carries a signed token.",
        content_type=MARKDOWN,
        metadata=RevisionMetadata(
            kind=KnowledgeKind.ARCHITECTURE,
            namespace="backend",
            status=KnowledgeStatus.APPROVED,
            trust_level=TrustLevel.REVIEWED,
            sensitivity=Sensitivity.INTERNAL,
            owner="platform-team",
        ),
        validity=ValidityPeriod(valid_from=NOW),
        author="engineer@example.com",
        created_at=NOW,
        source_anchors=(
            SourceAnchor(
                provider="github",
                source_uri="https://github.com/demo/repo/blob/main/a.md",
                repository="demo/repo",
                commit_sha="0123abc",
                blob_sha="4567def",
                file_path="a.md",
                line_start=1,
                line_end=4,
                external_id="PR-42",
            ),
        ),
    )


# ---------------------------------------------------------------------------
# The second instrument of ADR-0037 decision 7's disclosure bound: what
# `knowledge.get` adds to `result_payload`'s shape.
# ---------------------------------------------------------------------------

_PAYLOAD: Final = "payload"


class PayloadMutation(NamedTuple):
    """What some appearance of ``payload`` does to it: keys added, shapes unread."""

    keys: tuple[str, ...]
    unrecognized: tuple[str, ...]


_NOTHING: Final = PayloadMutation((), ())


def _names(node: ast.expr, name: str) -> bool:
    return isinstance(node, ast.Name) and node.id == name


def _keys_of(node: ast.Dict) -> PayloadMutation:
    """A dict literal's constant string keys, and the entries this cannot name."""
    keys: list[str] = []
    unread: list[str] = []
    for key, value in zip(node.keys, node.values, strict=True):
        if key is None:
            if not _names(value, _PAYLOAD):
                unread.append(f"{{**{ast.unparse(value)}}}")
        elif isinstance(key, ast.Constant) and isinstance(key.value, str):
            keys.append(key.value)
        else:
            unread.append(f"{{{ast.unparse(key)}: ...}}")
    return PayloadMutation(tuple(keys), tuple(unread))


def _updated(call: ast.Call) -> PayloadMutation:
    keys: list[str] = []
    unread: list[str] = []
    for argument in call.args:
        if isinstance(argument, ast.Dict):
            named = _keys_of(argument)
            keys.extend(named.keys)
            unread.extend(named.unrecognized)
        else:
            unread.append(f"payload.update({ast.unparse(argument)})")
    for keyword in call.keywords:
        if keyword.arg is None:
            unread.append(f"payload.update(**{ast.unparse(keyword.value)})")
        else:
            keys.append(keyword.arg)
    return PayloadMutation(tuple(keys), tuple(unread))


def _rebound(value: ast.expr) -> PayloadMutation:
    """``payload = <value>``, recognized only where the measured base shape survives.

    Each recognized arm collects nothing, because the keys ride on a node the
    walk visits anyway -- the dict literal or the ``|`` operand carrying the
    name. A rebind to anything else replaces the payload with a shape nothing
    measured, so it is reported rather than assumed harmless.
    """
    if isinstance(value, ast.Call) and _names(value.func, "result_payload"):
        return _NOTHING
    if isinstance(value, ast.Dict) and any(
        key is None and _names(item, _PAYLOAD)
        for key, item in zip(value.keys, value.values, strict=True)
    ):
        return _NOTHING
    if (
        isinstance(value, ast.BinOp)
        and isinstance(value.op, ast.BitOr)
        and (_names(value.left, _PAYLOAD) or _names(value.right, _PAYLOAD))
    ):
        return _NOTHING
    return PayloadMutation((), (f"payload = {ast.unparse(value)}",))


def _subscripted(parent: ast.Subscript) -> PayloadMutation:
    index = parent.slice
    if isinstance(parent.ctx, ast.Load):
        return _NOTHING
    if (
        isinstance(parent.ctx, ast.Store)
        and isinstance(index, ast.Constant)
        and isinstance(index.value, str)
    ):
        return PayloadMutation((index.value,), ())
    return PayloadMutation((), (f"payload[{ast.unparse(index)}]",))


def _augmented(parent: ast.AugAssign) -> PayloadMutation:
    if isinstance(parent.op, ast.BitOr) and isinstance(parent.value, ast.Dict):
        return _keys_of(parent.value)
    return PayloadMutation(
        (), (f"payload {type(parent.op).__name__}= {ast.unparse(parent.value)}",)
    )


def _merged(parent: ast.BinOp, node: ast.Name) -> PayloadMutation:
    other = parent.right if parent.left is node else parent.left
    if isinstance(other, ast.Dict):
        return _keys_of(other)
    return PayloadMutation((), (f"payload | {ast.unparse(other)}",))


def _method_called(attribute: ast.Attribute, call: ast.AST | None) -> PayloadMutation:
    if not (isinstance(call, ast.Call) and call.func is attribute):
        return PayloadMutation((), (f"payload.{attribute.attr}, not called",))
    if attribute.attr == "update":
        return _updated(call)
    if attribute.attr == "setdefault" and call.args:
        first = call.args[0]
        if isinstance(first, ast.Constant) and isinstance(first.value, str):
            return PayloadMutation((first.value,), ())
    return PayloadMutation((), (f"payload.{attribute.attr}(...)",))


def _mutation_at(node: ast.Name, parents: dict[int, ast.AST]) -> PayloadMutation:
    """What the one appearance of ``payload`` at *node* contributes.

    The dispatch is **total**: every arm either names the keys it adds or reports
    a shape it cannot name, and the fallthrough reports rather than ignores. A
    reader that saw only ``payload[<str>] = ...`` would report the same four keys
    while a ``payload.update({...})`` beside them served a fifth -- an instrument
    scoped to the spelling somebody happened to use, which is the defect
    ADR-0037's own walk was widened to stop one layer up.

    Reads are recognized narrowly, a subscript read and a ``return`` only:
    nothing can tell a mutating callee from a formatting one by looking at the
    call, so ``payload`` handed to anything is reported.
    """
    parent = parents.get(id(node))
    if parent is None:
        return PayloadMutation((), (_PAYLOAD,))

    if isinstance(parent, ast.Subscript) and parent.value is node:
        measured = _subscripted(parent)
    elif isinstance(parent, ast.Attribute) and parent.value is node:
        measured = _method_called(parent, parents.get(id(parent)))
    elif isinstance(parent, ast.Assign) and node in parent.targets:
        measured = _rebound(parent.value)
    elif isinstance(parent, ast.AugAssign) and parent.target is node:
        measured = _augmented(parent)
    elif isinstance(parent, ast.BinOp) and isinstance(parent.op, ast.BitOr):
        measured = _merged(parent, node)
    elif isinstance(parent, ast.Dict):
        measured = _keys_of(parent)
    elif isinstance(parent, ast.Return):
        measured = _NOTHING
    else:
        measured = PayloadMutation((), (ast.unparse(parent),))
    return measured


def payload_mutations(tree: ast.AST) -> PayloadMutation:
    """Every key added to a local ``payload`` in *tree*, and every shape that adds unread."""
    parents = {id(child): node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
    keys: list[str] = []
    unrecognized: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id == _PAYLOAD:
            measured = _mutation_at(node, parents)
            keys.extend(measured.keys)
            unrecognized.extend(measured.unrecognized)
    return PayloadMutation(tuple(keys), tuple(unrecognized))


def knowledge_get() -> ast.FunctionDef | ast.AsyncFunctionDef:
    """``mcp/tools.py``'s ``knowledge_get``, read from the file the import resolves to.

    Measured off the source and not off a response because the tool resolves a
    project, opens a store and reads a live index -- none of which belongs in a
    unit test.
    """
    spec = importlib.util.find_spec("theurian.mcp.tools")

    assert spec is not None and spec.origin is not None, "`theurian.mcp.tools` has no source"

    tree = ast.parse(pathlib.Path(spec.origin).read_text(encoding="utf-8"))
    defined = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.name == "knowledge_get"
    ]

    assert len(defined) == 1, (
        f"`mcp/tools.py` defines {len(defined)} `knowledge_get`. This measurement reads "
        f"exactly one, and a walk over the wrong body bounds the wrong tool."
    )
    return defined[0]


def knowledge_get_additions() -> tuple[str, ...]:
    """The keys ``knowledge.get`` adds to ``result_payload``'s shape.

    Read out of the AST rather than recorded as a constant, because both
    directions move ADR-0037's bound and neither is visible to a constant: a
    fifth addition widens what decision 7 may draw from, and a removed one turns
    an exported key into metadata the serve path withholds.
    """
    measured = payload_mutations(knowledge_get())

    assert measured.keys, (
        "`knowledge_get` adds no key this reader can name. The tool builds its "
        "additions some other way now, so this measurement reads nothing and every "
        "claim resting on it would pass vacuously."
    )
    return measured.keys


__all__ = [
    "ADR_0037",
    "NOW",
    "REPO_ROOT",
    "SERVED_ANCHOR_FIELDS",
    "PayloadMutation",
    "adr",
    "assert_the_adr_states",
    "collapsed",
    "flowed",
    "knowledge_get",
    "knowledge_get_additions",
    "markdown_table",
    "payload_mutations",
    "revision",
]
