"""ADR-0032 decision 2 is not stated by field name (#963, #965 item 8, #995).

ADR-0032 decision 2 is dereference: the daemon reads no write-intent input value as
a path. Stating it as the absence of path-, URI- or reference-named fields or
parameters is false, because the inputs declare such members (among them
`scopePaths`, `sourceAnchors[]`'s `sourceUri` and `filePath`, the migration
document's `sourceUri` and `anchor.filePath`) and record them without reading them.

Population (`_population`): every `description` string at any depth in
`schemas/mcp/*.schema.json`; every docstring (module, class, function, nested
handlers included) under `packages/theurian-core/src`, read by AST; and the text of
ADR-0032. Comments, other string literals, other docs and
`packages/theurian-core/CHANGELOG.md` are outside it (released blocks are immutable,
and the `[Unreleased]` errata must quote the false sentences; review checks that face
by diff).

Excluded (`_live`), as history: every double-quoted span after a
`**Corrected in place.**` marker, to the end of its paragraph; unquoted text there
stays live. Wider than the marker's own "said:" quotation on purpose: ADR-0032's
decision-2 *Compliance* paragraph also quotes the handler docstring's false "There
is no path parameter, by construction" after "docstring's", which a "said:"-only
exclusion reads as live (measured at 43e681d7: RED on that quotation). Cost: a live
claim written inside double quotes in such a paragraph is unseen.
"""

from __future__ import annotations

import ast
import json
import pathlib
import re
from collections.abc import Iterator
from typing import Final

import pytest

pytestmark = pytest.mark.unit

REPO_ROOT: Final = pathlib.Path(__file__).resolve().parents[4]
ADR_0032: Final = REPO_ROOT / "docs" / "adr" / "0032-the-write-intent-mcp-tool-surface.md"
SCHEMAS: Final = REPO_ROOT / "schemas" / "mcp"
SRC: Final = REPO_ROOT / "packages" / "theurian-core" / "src"
MARKER: Final = "**Corrected in place.**"

_SEP: Final = r"(?:\s*[-,/]\s*|\s+)(?:(?:and/or|or|and|nor)\s+)?(?:an?\s+)?"
_BY_NAME: Final = re.compile(
    rf"(?:\bpath{_SEP}UR[IL]s?(?:{_SEP}reference)?"
    r"|(?:\bno|\bwithout\s+an?|\bnot\s+\w+\s+an?|\bnever\s+\w+\s+an?)\s+path)"
    r"(?:-named|-shaped)?\s+(?:field|parameter)s?\b",
    re.IGNORECASE,
)


def _descriptions(node: object, where: str) -> Iterator[tuple[str, str]]:
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "description" and isinstance(value, str):
                yield f"{where}/description", value
            yield from _descriptions(value, f"{where}/{key}")
    elif isinstance(node, list):
        for index, value in enumerate(node):
            yield from _descriptions(value, f"{where}/{index}")


def _docstrings(src: pathlib.Path) -> Iterator[tuple[str, str]]:
    hosts = ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef
    for path in sorted(src.rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, hosts) and (doc := ast.get_docstring(node)):
                yield f"{path.relative_to(src).as_posix()}:{node.body[0].lineno}", doc


def _live(text: str) -> str:
    paragraphs = []
    for paragraph in re.split(r"\n\s*\n", text):
        head, marker, tail = paragraph.partition(MARKER)
        paragraphs.append(head + marker + re.sub(r'"[^"]*"', '""', tail))
    return "\n\n".join(paragraphs)


def _population(
    schemas: pathlib.Path = SCHEMAS, adr: pathlib.Path = ADR_0032, src: pathlib.Path = SRC
) -> dict[str, str]:
    texts = {
        f"{path.name}#{where}": text
        for path in sorted(schemas.glob("*.schema.json"))
        for where, text in _descriptions(json.loads(path.read_text(encoding="utf-8")), "")
    }
    texts.update(_docstrings(src))
    texts[adr.name] = adr.read_text(encoding="utf-8")
    return {where: _live(text) for where, text in texts.items()}


def _hits(key: re.Pattern[str], population: dict[str, str]) -> list[tuple[str, str]]:
    return [(where, m.group()) for where, text in population.items() for m in key.finditer(text)]


def test_the_population_reaches_every_schema_the_adr_and_the_corrected_docstrings() -> None:
    hosts = {where.partition("#")[0].partition(":")[0] for where in _population()}

    assert {path.name for path in SCHEMAS.glob("*.schema.json")} <= hosts
    assert {
        ADR_0032.name,
        "theurian/mcp/tools.py",
        "theurian/application/proposal_service.py",
    } <= hosts


def test_no_live_sentence_states_decision_2_by_field_name() -> None:
    """Key (`_BY_NAME`, case-insensitive, across line wraps): `path` then `URI` or
    `URL`, optionally then `reference`, each joined by a comma, hyphen, slash or space,
    an optional `or`/`and`/`nor`/`and/or` and an optional `a`/`an`; or a lone `path`
    right after `no`, `without a`, `not <word> a` or `never <word> a`; then optionally
    `-named`/`-shaped`, then `field` or `parameter`. The noun is what separates it from
    #963's seed key, whose other hits are true and name no field. A lone `path` needs
    the adjacent negation, so the true "no body-path parameter" does not hit.

    Measured: zero live hits at 43e681d7. RED drivers, each in a private worktree at
    43e681d7 plus this file: the two schema files checked out from 41484424; the
    `knowledge_propose_change` docstring's "There is no path parameter, by
    construction" restored in `mcp/tools.py`; the first four phrasings in
    `test_the_by_name_key_catches_each_phrasing`, each planted in the proposeChange
    description. Run at fd525381: the ADR's quoted false sentence moved out of its
    quotation, and again wrapped after `or`.

    Blind: a paraphrase without `field`/`parameter` ("takes no path", "argument",
    "member"); a list not running `path` then `URI` ("URI or path field"); a negation
    not adjacent to a lone `path` ("neither ... has a path parameter"); markup inside
    the phrase, a blockquote's `>` at a wrap included. The `path`-then-`URI` form is
    negation-blind, so a sentence affirming such fields in that phrase also hits.
    """
    hits = _hits(_BY_NAME, _population())

    assert not hits, (
        "ADR-0032 decision 2 is dereference, not the absence of path-named fields: "
        "name the members and say they are recorded, not read as paths (#963).\n"
        + "\n".join(map(repr, hits))
    )


@pytest.mark.parametrize(
    "sentence",
    [
        "declares neither a path, a URI, nor a reference field",
        "takes no path/URI field",
        "has no path, URI, and/or reference field",
        "has no path, URL, or reference field",
        "There is no path parameter, by construction",
        "It does not take a path-shaped parameter",
    ],
)
def test_the_by_name_key_catches_each_phrasing(sentence: str) -> None:
    assert _BY_NAME.search(sentence)


@pytest.mark.parametrize(
    "sentence",
    [
        "There is no body-path parameter, which would be a read primitive",
        "It does not take a path, a URI, or any other reference the daemon would have to "
        "dereference",
    ],
)
def test_the_by_name_key_passes_a_true_sentence(sentence: str) -> None:
    assert not _BY_NAME.search(sentence)


def test_the_walk_and_the_live_filter_reach_each_planted_sentence(
    tmp_path: pathlib.Path,
) -> None:
    """Planted through the real walk and `_live` (#995 adversarial M1). RED drivers, in
    a private worktree at 43e681d7 plus this file: `_live` returning ""; `_live`
    keeping only the text up to the marker; `_live` returning its input unchanged;
    `_descriptions` not recursing; `_docstrings` reading only top-level definitions;
    `IGNORECASE` dropped; `_SEP` without whitespace; the schema glob cut to two files.
    """
    schemas = tmp_path / "schemas"
    schemas.mkdir()
    for name in ("a", "b"):
        (schemas / f"{name}.schema.json").write_text('{"description": "d"}', encoding="utf-8")
    nested = {
        "description": "d",
        "properties": {"x": {"description": "Has No Path,\n  URI Or Reference\n  Field."}},
    }
    (schemas / "c.schema.json").write_text(json.dumps(nested), encoding="utf-8")
    adr = tmp_path / "adr.md"
    adr.write_text(
        "It declares no path, URI or reference field.\n\n"
        f'{MARKER} This said: "no path, URI or reference field". It has no path parameter.\n',
        encoding="utf-8",
    )
    src = tmp_path / "src"
    src.mkdir()
    (src / "m.py").write_text(
        '"""There is no body-path parameter."""\n\n\n'
        "def register():\n"
        "    def handler():\n"
        '        """There is no path parameter, by construction."""\n',
        encoding="utf-8",
    )

    hits = _hits(_BY_NAME, _population(schemas, adr, src))

    assert sorted(hits) == [
        ("adr.md", "no path parameter"),
        ("adr.md", "path, URI or reference field"),
        ("c.schema.json#/properties/x/description", "Path,\n  URI Or Reference\n  Field"),
        ("m.py:6", "no path parameter"),
    ]


def test_the_key_sees_the_false_sentences_the_adr_quotes_as_history() -> None:
    """Without this, the zero above could come from a key that matches nothing."""
    hits = _hits(_BY_NAME, {ADR_0032.name: ADR_0032.read_text(encoding="utf-8")})

    assert sorted(match for _, match in hits) == [
        "no path parameter",
        "path, URI, or reference field",
    ]
