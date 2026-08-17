"""Source parsers (FR-S1, FR-S2, ADR-0010, ADR-0019).

Parsers are where untrusted input enters the system, and where the promise that
"a structured source keeps its structure" is either kept or quietly broken.
"""

from __future__ import annotations

import json
from pathlib import PurePosixPath
from typing import Any, cast

import pytest

from theurian.domain.errors import InputTooLargeError
from theurian.domain.knowledge import SourceAnchor
from theurian.domain.ports import NormalizedDocument
from theurian.domain.values import JSON, MARKDOWN, YAML, MediaType
from theurian.infrastructure.filesystem.parsers.markdown import (
    GOVERNED_FIELDS,
    MarkdownParser,
)
from theurian.infrastructure.filesystem.parsers.openapi import OpenApiParser
from theurian.infrastructure.filesystem.parsers.registry import (
    ASYNCAPI,
    JSON_SCHEMA,
    OPENAPI,
    ParserRegistry,
    detect_media_type,
)
from theurian.infrastructure.filesystem.parsers.structured import JsonParser, YamlParser

ANCHOR = SourceAnchor(provider="git", source_uri="git://demo/a", file_path="doc.md")


# ==========================================================================
# Markdown
# ==========================================================================


def _markdown(text: str) -> NormalizedDocument:
    return MarkdownParser().parse(text.encode("utf-8"), media_type=MARKDOWN, anchor=ANCHOR)


def _structured(document: NormalizedDocument) -> dict[str, Any]:
    """Narrow ``structured`` for assertions.

    The field is ``dict[str, object] | None`` on the port, which is correct --
    a parser may legitimately produce no structure. Tests that assert on nested
    values need it narrowed once rather than ignored at every access.
    """
    assert document.structured is not None, "this parser must produce structure"
    return cast("dict[str, Any]", document.structured)


def test_markdown_extracts_a_heading_tree() -> None:
    document = _markdown("# Title\n\ntext\n\n## Section\n\n### Deep\n")
    headings = _structured(document)["headings"]

    assert [h["level"] for h in headings] == [1, 2, 3]
    assert [h["text"] for h in headings] == ["Title", "Section", "Deep"]
    assert headings[0]["line"] == 1


def test_markdown_extracts_code_fences_with_language() -> None:
    """A fence is usually the concrete example a reader wants, and its language
    is a useful filter."""
    document = _markdown("# T\n\n```python\nx = 1\n```\n\n```\nplain\n```\n")
    fences = _structured(document)["codeFences"]

    assert [f["language"] for f in fences] == ["python", None]


def test_markdown_title_comes_from_the_first_heading() -> None:
    assert _markdown("## Second level first\n\n# Later h1\n").title == "Later h1"
    assert _markdown("## Only h2\n").title == "Only h2"
    assert _markdown("no headings at all\n").title == "Untitled"


# -- ADR-0019: front matter ------------------------------------------------


FRONT_MATTER_DOC = """---
title: From front matter
status: approved
owner: someone
reviewers: [alice, bob]
jira: ENG-42
---

# Real title

Body text.
"""


def test_front_matter_is_preserved_as_data() -> None:
    document = _markdown(FRONT_MATTER_DOC)
    front = _structured(document)["frontMatter"]

    assert front["reviewers"] == ["alice", "bob"]
    assert front["jira"] == "ENG-42"


def test_front_matter_never_governs() -> None:
    """The whole point of ADR-0019.

    If `status: approved` reached the store, an author could approve their own
    knowledge by editing a file, with no migration and no review.
    """
    document = _markdown(FRONT_MATTER_DOC)

    assert "approved" not in document.body
    # `structured` holds it as data, but nothing maps it onto a governed field.
    # The migration is the only path to a status, and this document has none.
    assert not hasattr(document, "status")


def test_a_governed_key_in_front_matter_warns() -> None:
    """A silently ignored `status: approved` is exactly the case where an author
    believes something is approved and it is not."""
    warnings = MarkdownParser().warnings_for(FRONT_MATTER_DOC)
    codes = {w.code for w in warnings}
    locations = {w.location for w in warnings}

    assert codes == {"front-matter-governed-field"}
    assert "frontMatter.status" in locations
    assert "frontMatter.owner" in locations
    assert "frontMatter.reviewers" not in locations, "non-governed keys must not warn"


def test_the_body_hash_excludes_front_matter() -> None:
    """Adding metadata to a file must not change the identity of its content."""
    without = _markdown("# Real title\n\nBody text.\n")
    with_front = _markdown("---\nowner: x\n---\n\n# Real title\n\nBody text.\n")

    assert with_front.content_hash == without.content_hash


def test_malformed_front_matter_does_not_fail_the_document() -> None:
    """The body is still perfectly good knowledge; refusing it is a poor trade."""
    document = _markdown("---\nkey: [unclosed\n---\n\n# Title\n\nBody.\n")
    warnings = MarkdownParser().warnings_for("---\nkey: [unclosed\n---\n\n# Title\n")

    assert document.title == "Title"
    assert {w.code for w in warnings} == {"front-matter-unparseable"}


def test_front_matter_that_is_not_a_mapping_warns() -> None:
    warnings = MarkdownParser().warnings_for("---\n- a\n- b\n---\n\n# T\n")
    assert {w.code for w in warnings} == {"front-matter-not-a-mapping"}


def test_front_matter_is_only_recognised_at_the_start() -> None:
    """A horizontal rule mid-document is not front matter."""
    document = _markdown("# Title\n\n---\nnot: front matter\n---\n")
    assert "frontMatter" not in _structured(document)


def test_title_falls_back_to_front_matter() -> None:
    """The one front-matter field ADR-0019 permits to be read: a display concern."""
    document = _markdown("---\ntitle: From front matter\n---\n\nNo headings.\n")
    assert document.title == "From front matter"


def test_governed_field_list_covers_every_governed_concern() -> None:
    for field in ("status", "owner", "sensitivity", "namespace", "kind"):
        assert field in GOVERNED_FIELDS


def test_markdown_rejects_invalid_utf8() -> None:
    with pytest.raises(ValueError, match="not valid UTF-8"):
        MarkdownParser().parse(b"\xff\xfe bad", media_type=MARKDOWN, anchor=ANCHOR)


# ==========================================================================
# YAML and JSON
# ==========================================================================


def test_yaml_preserves_the_full_document_tree() -> None:
    """ADR-0010: flattening to prose is what makes coverage impossible later."""
    source = b"id: spec.x\noutcomes:\n  failure:\n    code: NOT_ALLOWED\n"
    document = YamlParser().parse(source, media_type=YAML, anchor=ANCHOR)

    assert _structured(document)["outcomes"]["failure"]["code"] == "NOT_ALLOWED"


def test_yaml_keeps_timestamps_as_strings() -> None:
    """`yaml.safe_load` would produce a datetime, and the projection would then
    render Python's spelling into the index rather than the document's text."""
    document = YamlParser().parse(
        b"when: 2026-08-02T10:00:00+09:00\n", media_type=YAML, anchor=ANCHOR
    )
    assert _structured(document)["when"] == "2026-08-02T10:00:00+09:00"


def test_yaml_body_is_the_source_text_not_a_reserialisation() -> None:
    """Re-serialising would discard comments and quoting -- which a reader
    looking at a retrieval result expects to see."""
    source = b"# a comment\nkey: 'quoted'\n"
    document = YamlParser().parse(source, media_type=YAML, anchor=ANCHOR)

    assert "# a comment" in document.body
    assert "'quoted'" in document.body


def test_json_preserves_the_full_document_tree() -> None:
    document = JsonParser().parse(b'{"a": {"b": [1, 2]}}', media_type=JSON, anchor=ANCHOR)
    assert _structured(document)["a"]["b"] == [1, 2]


def test_structured_title_falls_back_through_candidate_keys() -> None:
    assert YamlParser().parse(b"title: T\n", media_type=YAML, anchor=ANCHOR).title == "T"
    assert YamlParser().parse(b"name: N\n", media_type=YAML, anchor=ANCHOR).title == "N"
    assert YamlParser().parse(b"other: x\n", media_type=YAML, anchor=ANCHOR).title == "doc.md"


def test_a_top_level_scalar_is_wrapped() -> None:
    """A YAML document may legally be a bare scalar; the canonical shape is a
    mapping, so it is wrapped rather than rejected."""
    document = YamlParser().parse(b"just a string\n", media_type=YAML, anchor=ANCHOR)
    assert _structured(document) == {"value": "just a string"}


@pytest.mark.parametrize(
    ("parser", "media_type", "source"),
    [
        (YamlParser(), YAML, b"key: [unclosed\n"),
        (JsonParser(), JSON, b'{"unterminated": '),
    ],
)
def test_malformed_structured_documents_raise(
    parser: YamlParser | JsonParser, media_type: MediaType, source: bytes
) -> None:
    with pytest.raises(ValueError, match="not valid"):
        parser.parse(source, media_type=media_type, anchor=ANCHOR)


def test_oversized_json_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("theurian.infrastructure.filesystem.parsers.structured.MAX_JSON_BYTES", 16)
    with pytest.raises(InputTooLargeError):
        JsonParser().parse(b'{"k": "' + b"x" * 100 + b'"}', media_type=JSON, anchor=ANCHOR)


def test_yaml_parser_names_the_source_uri_for_a_document_nested_past_the_recursion_limit() -> None:
    """Round-three RED (orchestrator-reproduced): ``security/yaml_loading.py``'s
    own ``RecursionError`` -> ``ValueError`` translation is a module-level
    function with no ``anchor`` to name, so its message is the bare "YAML
    document exceeds the parser's safe nesting depth" -- and
    ``YamlParser.parse`` (``structured.py``) catches only ``yaml.YAMLError``
    around ``load_yaml``, not ``ValueError``, so that URI-less message reaches
    the caller unchanged instead of being re-wrapped the way this same method
    already wraps every *other* failure with ``anchor.source_uri``: ``_decode``'s
    ``UnicodeDecodeError`` clause, and this method's own ``yaml.YAMLError``
    clause immediately above the ``load_yaml`` call this drives. Measured
    directly: today this assertion fails, not the ``pytest.raises`` itself --
    a ``ValueError`` *is* raised, it just never learns which document it came
    from.
    """
    deep = ("a: " + "[" * 20000 + "]" * 20000 + "\n").encode("utf-8")

    with pytest.raises(ValueError, match="nesting depth") as excinfo:
        YamlParser().parse(deep, media_type=YAML, anchor=ANCHOR)

    assert ANCHOR.source_uri in str(excinfo.value)


def test_json_parser_names_the_source_uri_for_a_document_nested_past_the_recursion_limit() -> None:
    """Round-four mutation-adversarial (mutation n17 SURVIVED): ``JsonParser.
    parse``'s own ``except RecursionError as exc: raise ValueError(f"{anchor.
    source_uri} is nested too deeply to parse") from exc`` (``structured.py``)
    is the *reference implementation* two siblings already copy and cite by
    name -- ``OpenApiParser._load``'s identical guard says "Mirrors
    ``structured.py::JsonParser.parse``'s identical guard around the
    identical call" (``openapi.py``), and this file already pins that mirror
    at
    :func:`test_openapi_reports_the_source_uri_for_json_nested_past_the_recursion_limit`
    below -- but nothing in this file ever drove the original guard it
    mirrors. A mutation deleting the ``except RecursionError`` clause here
    entirely, or one that dropped ``anchor.source_uri`` from its message,
    would have passed every existing test in this file: the malformed-JSON
    parametrization above drives only ``json.JSONDecodeError``, a different
    branch of the same ``try``.
    """
    deep = ("[" * 20000 + "]" * 20000).encode("utf-8")

    with pytest.raises(ValueError, match="nested") as excinfo:
        JsonParser().parse(deep, media_type=JSON, anchor=ANCHOR)

    assert ANCHOR.source_uri in str(excinfo.value)


# ==========================================================================
# OpenAPI
# ==========================================================================


OPENAPI_DOC = b"""openapi: 3.1.0
info:
  title: Orders API
  version: "2.1"
paths:
  /orders/{id}:
    parameters:
      - name: shared
        in: path
    get:
      operationId: getOrder
      tags: [orders]
      parameters:
        - name: id
          in: path
      responses:
        "200": {description: OK}
        "404": {description: Missing}
    delete:
      operationId: deleteOrder
      deprecated: true
      responses:
        "204": {description: Gone}
components:
  schemas:
    Order: {type: object}
    Error: {type: object}
"""


def test_openapi_extracts_the_operation_index() -> None:
    """This is what `spec.getCoverage` reads. Without it an OpenAPI document is
    just a long string."""
    document = OpenApiParser().parse(OPENAPI_DOC, media_type=OPENAPI, anchor=ANCHOR)
    index = _structured(document)["_index"]

    assert index["specVersion"] == "3.1.0"
    assert sorted(index["operationIds"]) == ["deleteOrder", "getOrder"]
    assert index["schemaNames"] == ["Order", "Error"]

    get = next(o for o in index["operations"] if o["method"] == "get")
    assert get["path"] == "/orders/{id}"
    assert get["responses"] == ["200", "404"]
    assert get["parameters"] == ["id"]
    assert get["tags"] == ["orders"]


def test_openapi_ignores_non_operation_path_keys() -> None:
    """A path item carries `parameters` and `summary` alongside its methods;
    counting those as operations would inflate coverage."""
    document = OpenApiParser().parse(OPENAPI_DOC, media_type=OPENAPI, anchor=ANCHOR)
    methods = {o["method"] for o in _structured(document)["_index"]["operations"]}

    assert methods == {"get", "delete"}


def test_openapi_records_deprecation() -> None:
    document = OpenApiParser().parse(OPENAPI_DOC, media_type=OPENAPI, anchor=ANCHOR)
    delete = next(
        o for o in _structured(document)["_index"]["operations"] if o["method"] == "delete"
    )
    assert delete["deprecated"] is True


def test_openapi_title_includes_the_version() -> None:
    document = OpenApiParser().parse(OPENAPI_DOC, media_type=OPENAPI, anchor=ANCHOR)
    assert document.title == "Orders API 2.1"


def test_external_refs_are_recorded_never_fetched() -> None:
    """SEC-10, T-7. Resolving a ref would let any ingested document make
    Theurian issue an arbitrary request."""
    source = json.dumps(
        {
            "openapi": "3.1.0",
            "paths": {"/a": {"get": {"responses": {"200": {"$ref": "https://evil.test/x.json"}}}}},
            "components": {"schemas": {"S": {"$ref": "./local.yaml#/S"}}},
        }
    ).encode()

    document = OpenApiParser().parse(source, media_type=OPENAPI, anchor=ANCHOR)
    refs = _structured(document)["_index"]["externalRefs"]

    by_ref = {r["ref"]: r for r in refs}
    assert by_ref["https://evil.test/x.json"]["scheme"] == "https"
    assert by_ref["https://evil.test/x.json"]["resolved"] == "false"
    assert by_ref["./local.yaml#/S"]["scheme"] == "relative-file"


def test_internal_refs_are_not_recorded_as_external() -> None:
    source = b'{"openapi": "3.1.0", "paths": {"/a": {"get": {"x": {"$ref": "#/components/x"}}}}}'
    document = OpenApiParser().parse(source, media_type=OPENAPI, anchor=ANCHOR)
    assert _structured(document)["_index"]["externalRefs"] == []


def test_openapi_accepts_json_as_well_as_yaml() -> None:
    """`.yaml` files containing JSON are common, because JSON is valid YAML."""
    source = b'{"openapi": "3.1.0", "info": {"title": "T"}, "paths": {}}'
    document = OpenApiParser().parse(source, media_type=OPENAPI, anchor=ANCHOR)
    assert _structured(document)["_index"]["specVersion"] == "3.1.0"


def test_swagger_2_definitions_are_found() -> None:
    source = b'{"swagger": "2.0", "definitions": {"Old": {}}, "paths": {}}'
    document = OpenApiParser().parse(source, media_type=OPENAPI, anchor=ANCHOR)
    assert _structured(document)["_index"]["schemaNames"] == ["Old"]


def test_asyncapi_channels_are_extracted() -> None:
    source = b'{"asyncapi": "3.0.0", "channels": {"orders/created": {}}}'
    document = OpenApiParser().parse(source, media_type=ASYNCAPI, anchor=ANCHOR)
    assert _structured(document)["_index"]["channels"] == ["orders/created"]


def test_a_non_mapping_api_document_is_refused() -> None:
    with pytest.raises(ValueError, match="must be a mapping"):
        OpenApiParser().parse(b"[1, 2, 3]", media_type=OPENAPI, anchor=ANCHOR)


def test_openapi_reports_the_source_uri_for_json_nested_past_the_recursion_limit() -> None:
    """Round-three RED (orchestrator-reproduced): ``_load``'s JSON leg
    (``openapi.py``) tries ``json.loads(text)`` and catches only
    ``json.JSONDecodeError`` before falling back to YAML -- a document nested
    deep enough to blow the decoder's recursion limit raises
    ``RecursionError`` instead, which is not a ``JSONDecodeError``, so it
    sails past that ``except`` uncaught and out of ``OpenApiParser.parse``.
    ``structured.py``'s ``JsonParser.parse`` already guards the identical
    failure with ``except RecursionError as exc: raise ValueError(f"{anchor.
    source_uri} is nested too deeply to parse") from exc`` -- this pins that
    ``OpenApiParser`` gets the same translation, carrying the same
    ``anchor.source_uri``, not a raw ``RecursionError`` escaping to the
    caller under ``--json``.
    """
    deep = ("[" * 20000 + "]" * 20000).encode("utf-8")

    with pytest.raises(ValueError, match="nested") as excinfo:
        OpenApiParser().parse(deep, media_type=OPENAPI, anchor=ANCHOR)

    assert ANCHOR.source_uri in str(excinfo.value)


# ==========================================================================
# Registry and detection
# ==========================================================================


@pytest.mark.parametrize(
    ("name", "content", "expected"),
    [
        ("a.md", b"# T", MARKDOWN),
        ("a.markdown", b"# T", MARKDOWN),
        ("a.yaml", b"key: value", YAML),
        ("a.yml", b"key: value", YAML),
        ("a.json", b"{}", JSON),
        # Content wins over extension: an OpenAPI document is conventionally
        # `openapi.yaml`, and treating it as plain YAML would discard the
        # operation index that coverage depends on.
        ("openapi.yaml", b"openapi: 3.1.0\npaths: {}", OPENAPI),
        ("swagger.json", b'{"swagger": "2.0"}', OPENAPI),
        ("events.yaml", b"asyncapi: 3.0.0", ASYNCAPI),
        ("s.json", b'{"$schema": "x", "properties": {}}', JSON_SCHEMA),
    ],
)
def test_media_type_detection(name: str, content: bytes, expected: MediaType) -> None:
    assert detect_media_type(PurePosixPath(name), content) == expected


def test_unknown_extensions_are_not_claimed() -> None:
    assert detect_media_type(PurePosixPath("notes.txt"), b"text") is None
    assert detect_media_type(PurePosixPath("image.png"), b"\x89PNG") is None


def test_a_config_citing_a_schema_is_not_a_schema() -> None:
    """`$schema` appears in plenty of configuration files that are not schemas."""
    assert detect_media_type(PurePosixPath("c.json"), b'{"$schema": "x", "name": "y"}') == JSON


def test_registry_prefers_the_more_specific_parser() -> None:
    """OpenAPI is registered before YAML and JSON, so a document that is both
    goes to the parser that extracts more."""
    registry = ParserRegistry()
    assert registry.for_media_type(OPENAPI).parser_id == "openapi"  # type: ignore[union-attr]
    assert registry.for_media_type(YAML).parser_id == "yaml"  # type: ignore[union-attr]
    assert registry.for_media_type(MARKDOWN).parser_id == "markdown"  # type: ignore[union-attr]


def test_registry_returns_none_for_an_unhandled_type() -> None:
    assert ParserRegistry().for_media_type(MediaType("application/pdf")) is None


def test_json_parser_claims_structured_suffixes() -> None:
    """`+json` types (e.g. `application/vnd.api+json`) are JSON."""
    assert JsonParser().supports(MediaType("application/vnd.custom+json"))
