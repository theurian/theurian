"""ADR-0032 decision 2 is not stated by field name (#963, #965 item 8).

The property is dereference: the daemon reads no write-intent input value as a
path. Stating it as the absence of path-, URI- or reference-named fields is
false, because the inputs declare such fields (among them `scopePaths`,
`sourceAnchors[]`'s `sourceUri` and `filePath`, the migration document's
`sourceUri` and `anchor.filePath`) and record them without reading them.

Population: every `description` string at any depth in
`schemas/mcp/*.schema.json`, and the text of ADR-0032.

Key (`_BY_NAME`, case-insensitive, across line wraps): `path`, then `URI`,
then optionally `reference`, joined by commas, hyphens, `or`/`and` and an
optional `a`, followed directly by `field` or `parameter`. Requiring that noun
is what separates it from #963's seed key, whose other three hits are true and
name no field: the two `body` descriptions (about the body value) and ADR-0032's
decision-2 sentence qualified by dereference.

Excluded, as history rather than live claims:

- double-quoted text after a `**Corrected in place.**` marker in its paragraph,
  where ADR-0032 quotes the false sentence as governed history;
- `packages/theurian-core/CHANGELOG.md`, wholesale: released blocks are
  immutable, and the `[Unreleased]` errata must quote the false `[0.3.0]`
  sentence. Review checks that face by diff.

Measured: at 41484424 the key hits exactly the top-level `description` of
`knowledge-propose-change-input` and `knowledge-generate-migration-draft-input`;
at fd525381 it hits nothing live. RED drivers, each in a private worktree at
fd525381 plus this file: those two schema files checked out from 41484424
(RED, naming both); the quoted false sentence moved out of its quotation in
the *Corrected in place* paragraph (RED), and again with the phrase wrapped
after `or` (RED).

Blind: a paraphrase that never says `field` or `parameter`, a list that does
not run `path` then `URI` (`URI or path field`, a lone `path parameter`), and
markup inside the phrase, a blockquote's `>` at a wrap included. The key is
negation-blind, so a sentence affirming such fields in that phrase also hits;
name the members instead.
"""

from __future__ import annotations

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
MARKER: Final = "**Corrected in place.**"

_SEP: Final = r"[-,]*\s+(?:(?:or|and)\s+)?(?:a\s+)?"
_BY_NAME: Final = re.compile(
    rf"\bpath{_SEP}URI(?:{_SEP}reference)?(?:-named|-shaped)?\s+(?:field|parameter)s?\b",
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


def _live(text: str) -> str:
    paragraphs = []
    for paragraph in re.split(r"\n\s*\n", text):
        head, marker, tail = paragraph.partition(MARKER)
        paragraphs.append(head + marker + re.sub(r'"[^"]*"', '""', tail))
    return "\n\n".join(paragraphs)


def _population() -> dict[str, str]:
    texts = {
        f"{path.name}#{where}": text
        for path in sorted(SCHEMAS.glob("*.schema.json"))
        for where, text in _descriptions(json.loads(path.read_text(encoding="utf-8")), "")
    }
    texts[ADR_0032.name] = _live(ADR_0032.read_text(encoding="utf-8"))
    return texts


def test_no_live_sentence_states_decision_2_by_field_name() -> None:
    population = _population()
    assert {
        "knowledge-propose-change-input.schema.json#/description",
        "knowledge-generate-migration-draft-input.schema.json#/description",
    } <= population.keys()

    hits = [
        f"{where}: {m.group()!r}"
        for where, text in population.items()
        for m in _BY_NAME.finditer(text)
    ]

    assert not hits, (
        "ADR-0032 decision 2 is dereference, not the absence of path-named fields: "
        "name the members and say they are recorded, not read as paths (#963).\n" + "\n".join(hits)
    )


def test_the_key_sees_the_false_sentence_the_adr_quotes_as_history() -> None:
    """Without this, the zero above could come from a key that matches nothing."""
    assert _BY_NAME.search(ADR_0032.read_text(encoding="utf-8"))
