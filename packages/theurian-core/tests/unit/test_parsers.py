"""Source parsers (FR-S1, FR-S2, ADR-0010, ADR-0019).

Parsers are where untrusted input enters the system, and where the promise that
"a structured source keeps its structure" is either kept or quietly broken.
"""

from __future__ import annotations

import json
import re
import time
from pathlib import PurePosixPath
from typing import Any, Final, cast

import pytest
from hypothesis import given, seed, settings
from hypothesis import strategies as st
from hypothesis.strategies import DrawFn

from theurian.domain.errors import InputTooLargeError
from theurian.domain.knowledge import SourceAnchor
from theurian.domain.ports import NormalizedDocument
from theurian.domain.values import JSON, MARKDOWN, YAML, MediaType
from theurian.infrastructure.filesystem.parsers.markdown import (
    GOVERNED_FIELDS,
    MAX_FENCES,
    MarkdownParser,
)
from theurian.infrastructure.filesystem.parsers.openapi import OpenApiParser, _external_refs
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


def test_unclosed_fence_openers_scan_linearly() -> None:
    """Issue #331: the old combined opener-through-closer regex had a lazy
    ``.*?`` body group that had to rescan every remaining line before it could
    conclude a closer was absent -- once per unclosed opener, so a document that
    is n unclosed openers cost Theta(n^2). Measured on 68e8a0b (pre-fix): 156 KB
    (32,000 unclosed openers) took 25.3 s, with each doubling of the input
    roughly quadrupling the cost.

    The 1 s bound sits nearly two orders of magnitude below that 25.3 s and well
    above the linear scan's own measured cost (0.006 s), so it fails on the
    defect rather than on a slow machine.
    """
    text = "```a\n" * 32000
    assert len(text.encode("utf-8")) == 160_000

    started = time.monotonic()
    document = _markdown(text)
    elapsed = time.monotonic() - started

    assert _structured(document)["codeFences"] == [], "no opener here ever closes"
    assert elapsed < 1.0, f"32,000 unclosed fence openers took {elapsed:.2f}s"


def test_max_fences_bounds_the_record_not_the_scan() -> None:
    """``MAX_FENCES`` bounds what ``_fences`` *records*; issue #331 was that
    nothing bounded what the scan *spent* getting there. A document of
    ``MAX_FENCES + 1`` closed (not merely opened) fences must still record no
    more than ``MAX_FENCES`` of them."""
    text = "".join(f"```lang{i}\nbody\n```\n" for i in range(MAX_FENCES + 1))
    document = _markdown(text)
    fences = _structured(document)["codeFences"]

    assert len(fences) == MAX_FENCES


def test_fence_scan_memory_stays_linear_in_document_size() -> None:
    """Round-one MEDIUM (code review): ``_fences`` materializes ``lines`` and
    ``line_starts`` at O(line-count) -- ~202 MB peak measured 2026-08-27 at the
    8 MiB ``MAX_SOURCE_FILE_BYTES`` cap on an all-newline document. That is a
    bounded, linear residual against the quadratic *CPU* blowup #331 removed,
    and irrelevant at ordinary document sizes (microseconds and kilobytes
    either way); only an adversarial document at the ingestion cap reaches it.
    Pinned here at a smaller scale, so a future change that reintroduces
    super-linear memory -- as happened once already in the sibling ``$ref``
    walk this same review round measured (issue #245) -- goes RED rather than
    only showing up as a slow CI run somewhere else.
    """
    small_peak, small_bytes = _fence_scan_peak_memory(line_count=100_000)
    large_peak, large_bytes = _fence_scan_peak_memory(line_count=800_000)

    assert large_bytes == pytest.approx(8 * small_bytes, rel=0.01)
    ratio = large_peak / small_peak
    # A linear cost stays near the 8x input ratio; a quadratic one would be
    # near 64x. 16x leaves comfortable room above measurement noise while
    # catching a real regression back toward quadratic.
    assert ratio < 16, f"peak memory scaled {ratio:.1f}x for an 8x larger document"
    # A generous absolute ceiling too, in case a future change keeps the
    # linear *shape* but inflates the per-line constant: ~2.5x the ~38.7 MB
    # measured 2026-08-27 for this document size.
    assert large_peak < 96 * 1024 * 1024, f"peaked at {large_peak / 1024 / 1024:.1f} MB"


def _fence_scan_peak_memory(*, line_count: int) -> tuple[int, int]:
    """Peak traced memory parsing an all-newline document of ``line_count``
    single-character lines, and the document's byte length.

    Every line is a non-candidate for either fence pattern (issue #331's
    prefix-skip guard filters it before the regex ever runs), which isolates
    what ``lines``/``line_starts`` themselves cost from what the regex costs.
    """
    import tracemalloc

    data = ("a\n" * line_count).encode("utf-8")
    tracemalloc.start()
    try:
        MarkdownParser().parse(data, media_type=MARKDOWN, anchor=ANCHOR)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    return peak, len(data)


# -- codeFences differential oracle (issue #331, round-one MEDIUM) --------
#
# #331's own review measured the line-scan rewrite byte-identical to the regex
# it replaced over an 800k-document fuzz corpus, but nothing pinned
# ``line``/``characters`` directly: a mutation that offsets either by any
# constant currently survives the full suite. This oracle is the pre-#331
# ``_fences`` body, verbatim, so the two extractions are compared against each
# other rather than against hand-computed numbers a copy-paste of the
# implementation would also get wrong.

_OLD_FENCE = re.compile(r"^```([A-Za-z0-9_+-]*)[ \t]*$(.*?)^```[ \t]*$", re.MULTILINE | re.DOTALL)


def _oracle_fences(body: str) -> list[dict[str, Any]]:
    """The pre-#331 ``_fences`` extraction, kept as an independent check."""
    fences: list[dict[str, Any]] = []
    for match in _OLD_FENCE.finditer(body):
        if len(fences) >= MAX_FENCES:
            break
        fences.append(
            {
                "language": match.group(1) or None,
                "line": body.count("\n", 0, match.start()) + 1,
                "characters": len(match.group(2)),
            }
        )
    return fences


def _closed_fences(count: int) -> str:
    """``count`` distinct, closed, language-tagged fences -- for the
    ``MAX_FENCES`` boundary cases, where what matters is whether the cap and
    the oracle agree on which ones survive it."""
    return "".join(f"```lang{i}\nbody{i}\n```\n" for i in range(count))


_FENCE_DIFFERENTIAL_CASES: dict[str, str] = {
    "tilde-blocks-are-not-fences": "~~~python\nx = 1\n~~~\n",
    "crlf-line-endings": "```python\r\nx = 1\r\n```\r\n",
    "four-space-indented-fence": "    ```python\n    x = 1\n    ```\n",
    "closer-with-trailing-content": "```python\nx = 1\n```extra\n",
    "two-backtick-run": "``\nx\n``\n",
    "four-backtick-run": "````\nx\n````\n",
    "nested-opener-looking-lines": "```outer\n```inner\nx\n```\n```\n",
    "unterminated-final-fence": "```python\nno closer\n",
    "empty-language": "```\nx\n```\n",
    "no-trailing-newline": "```python\nx = 1\n```",
    "multi-line-body": "```python\nline1\nline2\nline3\n```\n",
    "language-tagged-fence": "```json\n{}\n```\n",
    "max_fences_minus_one_closed": _closed_fences(MAX_FENCES - 1),
    "max_fences_plus_one_closed": _closed_fences(MAX_FENCES + 1),
}


@pytest.mark.parametrize(
    ("case", "body"),
    list(_FENCE_DIFFERENTIAL_CASES.items()),
    ids=list(_FENCE_DIFFERENTIAL_CASES),
)
def test_code_fences_match_the_pre_331_regex_oracle(case: str, body: str) -> None:
    """The line-scan rewrite (#331) must match what it replaced exactly, not
    only on language: round one (code and adversarial review, MEDIUM) found
    that a mutation offsetting ``line`` or ``characters`` by any constant
    currently survives the full suite. Each case here is a shape #331's own
    800k-document fuzz run does not specifically target: CRLF, indentation, a
    malformed closer, degenerate backtick runs, overlapping opener-looking
    lines, truncation at both sides of ``MAX_FENCES``, and the ordinary shapes
    besides -- compared field-for-field against an independent oracle rather
    than against hardcoded numbers.
    """
    document = _markdown(body)
    assert _structured(document)["codeFences"] == _oracle_fences(body), case


#: `_FENCE_LINE_ENDINGS` mixes LF and CRLF *per line*, and the trailing one is
#: sometimes dropped -- both shapes the 14 fixed cases above cover only once
#: each (`"crlf-line-endings"`, `"no-trailing-newline"`), never in combination
#: with the fence shapes below.
_FENCE_LINE_ENDINGS: Final = ("\n", "\r\n")


@st.composite
def _markdown_ish_line(draw: DrawFn) -> str:
    """One line of a fuzzed, fence-adjacent Markdown document.

    Six shapes, each independently likely across a document of up to 15
    lines: a blank line, ordinary prose, a fence opener (with a run of two,
    three or four backticks and a random language tag), a bare closer, a
    closer with trailing content after it (never a closer, per the
    ``"closer-with-trailing-content"`` fixed case), and a tilde run (never a
    fence at all, per ``"tilde-blocks-are-not-fences"``). Indentation is drawn
    independently of the shape, so an opener or closer can land indented --
    the ``"four-space-indented-fence"`` fixed case, generalised.
    """
    indent = draw(st.sampled_from(["", " ", "  ", "   ", "    "]))
    kind = draw(st.sampled_from(["blank", "prose", "opener", "closer", "closer-trailing", "tilde"]))
    if kind == "blank":
        return ""
    if kind == "prose":
        return indent + draw(st.text(alphabet="abcXYZ019 .,!_-", max_size=10))
    if kind == "opener":
        run = draw(st.sampled_from(["```", "````", "``"]))
        lang = draw(st.text(alphabet="abcXYZ019_+-", max_size=6))
        return f"{indent}{run}{lang}"
    if kind == "closer":
        run = draw(st.sampled_from(["```", "````", "``"]))
        return f"{indent}{run}"
    if kind == "closer-trailing":
        run = draw(st.sampled_from(["```", "````"]))
        return f"{indent}{run}extra"
    run = draw(st.sampled_from(["~~~", "~~~~"]))
    return f"{indent}{run}"


@st.composite
def _markdown_ish_documents(draw: DrawFn) -> str:
    """A document assembled from :func:`_markdown_ish_line`, up to 15 lines,
    each followed by an independently drawn line ending -- and sometimes no
    trailing one at all."""
    lines = draw(st.lists(_markdown_ish_line(), max_size=15))
    parts: list[str] = []
    for line in lines:
        parts.append(line)
        parts.append(draw(st.sampled_from(_FENCE_LINE_ENDINGS)))
    # `mypy` cannot re-infer `DrawFn.__call__`'s type variable across a loop
    # boundary followed by another call site in the same function (a
    # reproduced, minimal case is filed for reference) -- drawing into an
    # explicitly typed local first, rather than inline in the `if`, sidesteps
    # it without changing what gets drawn.
    drop_trailing_ending: bool = draw(st.booleans())
    if parts and drop_trailing_ending:
        parts.pop()
    return "".join(parts)


#: Deterministic in CI: `@seed` fixes which examples run (not `derandomize`
#: alone -- see the identical note on `_GENERATED` in `test_ref_recording.py`),
#: `database=None` writes nothing to disk, and 400 examples run in well under a
#: second (measured 2026-08-28: 0.52s), comfortably inside the 300-500 range
#: this fuzz needs to cover the six line shapes above in combination.
_FUZZ_SETTINGS = settings(deadline=None, derandomize=True, database=None, max_examples=400)


@seed(331)
@_FUZZ_SETTINGS
@given(body=_markdown_ish_documents())
def test_code_fences_match_the_pre_331_regex_oracle_over_random_documents(body: str) -> None:
    """The 14-case oracle above pins specific edges by hand; this pins the
    property those edges were sampled from, so a divergence the fixed cases
    do not happen to hit still goes red.

    #331 replaced ``_fences``'s single combined opener-through-closer regex
    with a from-scratch forward line scan for performance (see that
    function's docstring) -- a genuinely different algorithm from
    ``_oracle_fences``'s regex, not a copy of it, so agreement between the two
    across random shapes is exactly the claim `docs/security/threat-model.md`
    makes about this rewrite and the only thing that was, until now, checked
    against a one-time, uncommitted dev-time fuzz run rather than a committed
    one.

    Verified directly (2026-08-28): reverting the closer-detection filter from
    ``line.startswith("```") and _FENCE_CLOSER_LINE.fullmatch(line)`` to
    ``line.startswith("```")`` alone -- treating any backtick-opening line as a
    valid closer, including one carrying its own language tag or trailing
    text -- turned this test red on the very first generated example,
    ``"```\\n```0\\n"``, confirming it can fail rather than passing
    vacuously.
    """
    document = _markdown(body)
    assert _structured(document)["codeFences"] == _oracle_fences(body), body


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


def _external_refs_cost(n: int, *, repeats: int = 5) -> tuple[float, Any]:
    """Best (minimum) wall time of ``repeats`` calls to ``_external_refs`` on
    the long-key/wide-fan-out shape at size ``n``, and the walk result from the
    last call -- identical across repeats since the input document is
    unchanged.

    ``min`` rather than ``mean`` damps CI scheduling noise, which only ever
    adds delay: the fastest observed run sits closest to the walk's own cost,
    and a slower one reflects the runner, not the code.
    """
    document: dict[str, Any] = {"openapi": "3.1.0", "x" * n: {str(i): 0 for i in range(n)}}
    best = float("inf")
    walk = None
    for _ in range(repeats):
        started = time.monotonic()
        walk = _external_refs(document)
        best = min(best, time.monotonic() - started)
    assert walk is not None, "repeats must be at least 1"
    return best, walk


def test_external_refs_path_build_is_linear() -> None:
    """Issue #328: ``_external_refs`` built each child's path with
    ``f"{path}.{key}"``, which copies the parent's whole accumulated string on
    every edge. A document with one long mapping key (length n) fanning out to n
    children under it therefore cost Theta(n^2) -- both the key length and the
    fan-out are chosen by whoever wrote the document, and neither ``MAX_REFS``
    nor ``MAX_REF_DEPTH`` fires on this shape, since there is no ``$ref`` at all
    and the depth never exceeds 2.

    This test used to assert an absolute wall (``elapsed < 0.2``), which no
    single threshold can satisfy on every machine: it passed locally at
    ~0.036s, then failed on a loaded ubuntu CI runner at 0.29s -- a runner slow
    enough to miss a 0.2s wall on the *fixed* code is not implausible, and a
    machine fast enough to pass a reverted quadratic's absolute cost is not
    implausible either. The two ranges overlap, so a fixed wall is fragile in
    both directions at once.

    What is machine-independent is the *scaling*, not the absolute time: a
    linear walk costs ~2x per doubling of the input; the pre-#328 quadratic one
    costs ~4x, since it pays for a copy of the accumulated path on every edge.
    Measured directly (2026-08-27), best-of-5 to damp scheduling noise: the
    fixed code costs 0.0175s at n=120,000 and 0.0350s at n=240,000 -- ratio
    2.00. The pre-#328 eager ``f"{path}.{key}"`` build, reconstructed against
    the same shape and the same current helpers, costs 0.270s and 1.238s --
    ratio 4.59. A 3.0 threshold sits comfortably between the two and holds on
    any machine, fast or slow, loaded or idle, because it never compares
    against a wall clock -- only against itself at half the size.
    """
    small, small_walk = _external_refs_cost(120_000)
    large, large_walk = _external_refs_cost(240_000)

    assert small_walk.found == () and large_walk.found == (), "no $ref anywhere in this document"
    assert small_walk.truncations == () and large_walk.truncations == (), (
        "neither cap fires on this shape"
    )

    ratio = large / small
    # A linear walk scales ~2x per doubling; the pre-#328 quadratic build
    # measured ~4.6x on this same shape (see docstring). 3.0 sits comfortably
    # between the two.
    assert ratio < 3.0, (
        f"cost scaled {ratio:.2f}x for a 2x larger input ({small:.3f}s -> {large:.3f}s); "
        "linear scales ~2x per doubling, the pre-#328 quadratic build ~4.6x"
    )
    # A generous absolute backstop against a total blowup, not the primary
    # signal: comfortably above the ~1.2s a reintroduced quadratic measures at
    # n=240,000 here, and far above the ~0.035s the fixed code measures.
    assert large < 5.0, f"the n=240,000 shape alone took {large:.2f}s"


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
