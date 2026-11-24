"""``Refs:`` trailers to traceability edges (ADR-0040 decisions 1-3), dormant until C2-4.

Expected ids and digests are recomputed here with ``hashlib`` and a local base32
encoder, never through the module's own helpers.
"""

from __future__ import annotations

import ast
import hashlib
import inspect
import itertools
import string
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

import pytest

from theurian.domain import refs_trailer
from theurian.domain.enums import RelationType, TraceNodeType
from theurian.domain.identifiers import ProjectId
from theurian.domain.knowledge import SourceAnchor
from theurian.domain.refs_trailer import (
    MAX_REFS_PER_COMMIT,
    CommitRefs,
    capped_digest,
    edges_from_commit,
)

pytestmark = pytest.mark.unit

_SHA: Final = "a" * 40
_OTHER_SHA: Final = "b" * 40
_WHEN: Final = datetime(2026, 11, 19, 12, 0, tzinfo=UTC)
_PROJECT: Final = ProjectId("demo-project")
_ALPHABET: Final = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_MARKER: Final = "echo-marker-7f3"
_LINE_SEPARATOR: Final = chr(0x2028)
_NBSP: Final = chr(0xA0)


def _expected_edge_id(sha: str, path: str, item: str) -> str:
    digest = hashlib.sha256(sha.encode() + b"\0" + path.encode() + b"\0" + item.encode()).digest()
    number = int.from_bytes(digest[:16], "big")
    chars = []
    for _ in range(26):
        chars.append(_ALPHABET[number & 31])
        number >>= 5
    return "".join(reversed(chars))


def _refs(
    message: bytes | str,
    *,
    sha: str = _SHA,
    paths: tuple[str, ...] | None = ("x.py",),
    committed_at: datetime | None = _WHEN,
) -> CommitRefs:
    raw = message.encode() if isinstance(message, str) else message
    return edges_from_commit(
        project_id=_PROJECT, sha=sha, committed_at=committed_at, paths=paths, message=raw
    )


def _ids(count: int) -> str:
    return "".join(f"Refs: item-{n}\n" for n in range(count))


def test_one_ref_and_one_path_give_one_fully_derived_edge() -> None:
    """C1: every field of ADR-0040 decision 1, with the id derived from the inputs only."""
    result = _refs("Refs: a.b\n")

    (edge,) = result.edges
    assert (result.sha, result.refused, result.capped) == (_SHA, 0, False)
    assert (result.undecodable, result.undated) == (False, False)
    assert edge.source.node_type is TraceNodeType.CODE
    assert edge.source.node_id == "x.py"
    assert edge.target.node_type is TraceNodeType.KNOWLEDGE
    assert edge.target.node_id == "a.b"
    assert edge.relation_type is RelationType.RELATED_TO
    assert edge.confidence == 1.0
    assert edge.evidence == (
        SourceAnchor(provider="git", source_uri=f"{_SHA}:x.py", commit_sha=_SHA, file_path="x.py"),
    )
    assert edge.created_at == _WHEN
    assert edge.source_commit == _SHA
    assert edge.project_id == _PROJECT
    assert edge.edge_id.value == _expected_edge_id(_SHA, "x.py", "a.b")
    assert len(edge.edge_id.value) == 26


def test_the_same_inputs_give_equal_results() -> None:
    assert _refs("Refs: a.b\n") == _refs("Refs: a.b\n")


@pytest.mark.parametrize(
    ("sha", "path", "item"),
    [(_OTHER_SHA, "x.py", "a.b"), (_SHA, "y.py", "a.b"), (_SHA, "x.py", "a.c")],
    ids=["sha", "path", "item"],
)
def test_changing_any_one_input_changes_the_edge_id(sha: str, path: str, item: str) -> None:
    base = _refs("Refs: a.b\n").edges[0].edge_id
    other = _refs(f"Refs: {item}\n", sha=sha, paths=(path,)).edges[0].edge_id

    assert other != base
    assert other.value == _expected_edge_id(sha, path, item)


def test_a_repeated_id_gives_one_edge_per_path() -> None:
    result = _refs("Refs: a.b\nRefs: a.b\n", paths=("x.py", "y.py"))

    assert [(e.source.node_id, e.target.node_id) for e in result.edges] == [
        ("x.py", "a.b"),
        ("y.py", "a.b"),
    ]


def test_two_ids_and_two_paths_give_four_distinct_edges_in_input_order() -> None:
    result = _refs("Refs: a.b\nRefs: c.d\n", paths=("x.py", "y.py"))

    assert [(e.source.node_id, e.target.node_id) for e in result.edges] == [
        ("x.py", "a.b"),
        ("x.py", "c.d"),
        ("y.py", "a.b"),
        ("y.py", "c.d"),
    ]
    assert len({e.edge_id for e in result.edges}) == 4


@pytest.mark.parametrize(
    ("message", "collected"),
    [
        ("subject\n\nRefs: a.b\n\nSigned-off-by: A <a@b.c>\n", ["a.b"]),
        ("subject\n\n  Refs: a.b\n", []),
        ("subject\n\n> Refs: a.b\n", []),
        ("subject\n\nRefs:a.b\n", []),
        ("subject\r\n\r\nRefs: a.b\r\n", ["a.b"]),
        ("Refs: a.b\n\nbody\n", ["a.b"]),
        (f"x{_LINE_SEPARATOR}Refs: a.b\n", []),
        ("subject\n\nRefs:  a.b\n", ["a.b"]),
        ("subject\n\nRefs: \ta.b\t \n", ["a.b"]),
    ],
    ids=[
        "above-signed-off-by",
        "indented",
        "quoted",
        "no-space",
        "crlf",
        "subject-line",
        "unicode-line-separator-is-not-a-break",
        "two-spaces",
        "tab-and-space",
    ],
)
def test_extraction_follows_decision_three(message: str, collected: list[str]) -> None:
    """C1c: column 0, ``\\n`` as the only line break, ASCII whitespace stripped."""
    result = _refs(message)

    assert [e.target.node_id for e in result.edges] == collected
    assert result.refused == 0


def test_a_mid_line_refs_is_not_a_trailer() -> None:
    assert _refs("x Refs: a.b\n").edges == ()


_HOSTILE: Final = [
    pytest.param(b"Refs: ADR-0040\n", "ADR-0040", id="adr-reference"),
    pytest.param(b"Refs: #1033\n", "#1033", id="issue-number"),
    pytest.param(f"Refs: {_MARKER}, c.d\n".encode(), _MARKER, id="comma-list"),
    pytest.param(f"Refs: {_MARKER}{_NBSP}\n".encode(), _MARKER, id="nbsp-not-stripped"),
    pytest.param(f"Refs: {_MARKER}x{'a' * 190}\n".encode(), _MARKER + "x", id="over-200-chars"),
    pytest.param(b"Refs: ECHO-MARKER-7f3\n", "ECHO-MARKER-7f3", id="uppercase"),
    pytest.param(b"Refs: \x80ECHO-MARKER-7f3\n", "ECHO-MARKER-7f3", id="non-utf8"),
]


@pytest.mark.parametrize(("message", "marker"), _HOSTILE)
def test_a_refused_value_is_counted_and_never_echoed(message: bytes, marker: str) -> None:
    """C2: a refused value is counted (an undecodable message: not at all), never echoed."""
    assert marker in repr(message) or marker.encode() in message
    assert marker in message.decode("utf-8", "replace")

    result = _refs(message)

    assert result.edges == ()
    assert result.refused == (0 if result.undecodable else 1)
    assert marker not in repr(result)
    assert marker.lower() not in repr(result).lower()


def test_a_201_character_valid_shape_id_is_refused_whole() -> None:
    value = "z" * 201

    result = _refs(f"Refs: {value}\n")

    assert (result.edges, result.refused) == ((), 1)
    assert "z" * 20 not in repr(result)


def test_the_cap_is_recorded_as_32() -> None:
    assert MAX_REFS_PER_COMMIT == 32


def test_32_distinct_ids_give_all_edges_for_each_path() -> None:
    result = _refs(_ids(32), paths=("x.py", "y.py"))

    assert len(result.edges) == 64
    assert result.capped is False


def test_33_distinct_ids_give_no_edges_and_are_capped() -> None:
    result = _refs(_ids(33))

    assert result.edges == ()
    assert result.capped is True


def test_a_repeated_id_does_not_count_twice_toward_the_cap() -> None:
    result = _refs(_ids(32) + "Refs: item-0\n")

    assert len(result.edges) == 32
    assert result.capped is False


def test_a_path_capped_commit_gives_no_edges_and_is_capped() -> None:
    result = _refs("Refs: a.b\n", paths=None)

    assert (result.edges, result.capped) == ((), True)


def test_the_capped_digest_of_nothing_is_none() -> None:
    assert capped_digest([]) is None


def test_the_capped_digest_is_the_sorted_newline_terminated_sha256_in_any_order() -> None:
    shas = [_OTHER_SHA, "0" * 40, _SHA]
    expected = hashlib.sha256(b"".join(s.encode() + b"\n" for s in sorted(shas))).hexdigest()

    assert capped_digest(shas) == expected
    assert capped_digest(reversed(shas)) == expected


def test_invalid_values_do_not_count_toward_the_cap() -> None:
    junk = "".join(f"Refs: BAD {n}\n" for n in range(5))

    result = _refs(_ids(32) + junk)

    assert len(result.edges) == 32
    assert result.refused == 5
    assert result.capped is False


def test_edges_from_commit_takes_exactly_the_documented_keyword_parameters() -> None:
    """C4: no store or port, so it cannot read canonical rows."""
    parameters = inspect.signature(edges_from_commit).parameters

    assert [p.kind for p in parameters.values()] == [inspect.Parameter.KEYWORD_ONLY] * 5
    assert set(parameters) == {"project_id", "sha", "committed_at", "paths", "message"}


def test_the_module_imports_nothing_from_application_or_infrastructure() -> None:
    tree = ast.parse(Path(refs_trailer.__file__).read_text(encoding="utf-8"))
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            imported.append(node.module)
        elif isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)

    assert "theurian.domain.review_finding" in imported
    assert [
        m for m in imported if m.startswith(("theurian.application", "theurian.infrastructure"))
    ] == []


def test_a_non_utf8_message_is_reported_by_sha_with_nothing_evaluated() -> None:
    result = _refs(b"Refs: a.b\n\x80\n")

    assert (result.edges, result.undecodable, result.refused) == ((), True, 0)
    assert result.sha == _SHA


def test_an_undated_commit_gives_no_edges_and_is_flagged_not_capped() -> None:
    result = _refs("Refs: a.b\n", committed_at=None)

    assert (result.edges, result.undated, result.capped) == ((), True, False)


def test_an_undated_path_capped_commit_is_both_undated_and_capped() -> None:
    result = _refs("Refs: a.b\n", committed_at=None, paths=None)

    assert (result.undated, result.capped) == (True, True)


def test_an_undecodable_undated_path_capped_commit_keeps_the_independent_flags() -> None:
    result = _refs(b"\x80", committed_at=None, paths=None)

    assert (result.undecodable, result.undated, result.capped) == (True, True, True)


_NON_LF_BREAKS: Final = sorted(
    {c for c in map(chr, range(0x110000)) if len(("a" + c + "b").splitlines()) == 2} - {"\n"}
)
_LISTED_BREAKS: Final = [
    "\r",
    chr(0x0B),
    chr(0x0C),
    chr(0x1C),
    chr(0x1D),
    chr(0x1E),
    chr(0x85),
    chr(0x2028),
    chr(0x2029),
]


def test_the_listed_line_breaks_are_every_one_str_splitlines_honours_besides_lf() -> None:
    assert sorted(_LISTED_BREAKS) == _NON_LF_BREAKS


@pytest.mark.parametrize(
    "separator", _LISTED_BREAKS, ids=[f"u{ord(c):04x}" for c in _LISTED_BREAKS]
)
def test_only_lf_ends_a_line_so_no_other_separator_exposes_a_trailer(separator: str) -> None:
    """A splitlines()-style split would collect a value ADR-0040 decision 3 keeps in the subject."""
    result = _refs(f"subject{separator}Refs: a.b")

    assert (result.edges, result.refused) == ((), 0)


_SPACE_LIKE: Final = [
    c for c in map(chr, range(0x110000)) if (c.isspace() or c in string.whitespace) and c != "\n"
]


@pytest.mark.parametrize("suffix", _SPACE_LIKE, ids=[f"u{ord(c):04x}" for c in _SPACE_LIKE])
def test_only_ascii_whitespace_is_stripped_from_a_value(suffix: str) -> None:
    """str.strip() or a narrower ASCII set would flip one of these (NBSP, U+0085, VT, FF)."""
    result = _refs(f"Refs: a.b{suffix}")

    if suffix in string.whitespace:
        assert [e.target.node_id for e in result.edges] == ["a.b"]
        assert result.refused == 0
    else:
        assert (result.edges, result.refused) == ((), 1)


@pytest.mark.parametrize(
    ("decodable", "dated", "has_paths", "ids"),
    list(itertools.product([True, False], [True, False], [True, False], [1, 33])),
)
def test_the_flags_are_independent_and_edges_need_all_three_clear(
    decodable: bool, dated: bool, has_paths: bool, ids: int
) -> None:
    """The undecodable branch must report capped and undated from the inputs, not a constant."""
    message = _ids(ids).encode() + (b"" if decodable else b"\x80")

    result = _refs(
        message,
        committed_at=_WHEN if dated else None,
        paths=("x.py",) if has_paths else None,
    )

    assert result.capped == (not has_paths or (decodable and ids == 33))
    assert result.undated == (not dated)
    assert result.undecodable == (not decodable)
    clear = not (result.capped or result.undated or result.undecodable)
    assert bool(result.edges) == clear


def test_the_capped_digest_keeps_duplicates() -> None:
    expected = hashlib.sha256(_SHA.encode() + b"\n" + _SHA.encode() + b"\n").hexdigest()

    assert capped_digest([_SHA, _SHA]) == expected
    assert capped_digest([_SHA, _SHA]) != capped_digest([_SHA])


def test_the_collector_answers_as_it_did_when_the_engine_version_was_last_set() -> None:
    """D11, ruling c2-4 section 2's ratchet, in ``PARSER_STAMP``'s shape: answers, not source."""
    spaces = sorted({*string.whitespace, *filter(str.isspace, map(chr, range(0x3001)))})
    paths = ("a.py", "署名付きトークンを持つ.md")
    answers = [
        refs_trailer.REFS_KEY,
        MAX_REFS_PER_COMMIT,
        [
            _refs(probe).edges != ()
            for probe in ("Refs: a.b", "Refs:a.b", "refs: a.b", "Refs:\ta.b")
        ],
        [
            c
            for c in spaces
            if [e.target.node_id for e in _refs(f"Refs: {c}a.b{c}").edges] == ["a.b"]
        ],
        [_refs(_ids(count)).capped for count in (32, 33)],
        [edge.edge_id.value for edge in _refs("Refs: a.b", paths=paths).edges],
        capped_digest([_OTHER_SHA, _SHA, _SHA]),
    ]

    assert hashlib.sha256(repr(answers).encode()).hexdigest() == (
        "5a04fdf3040cc620ad75b2a7be04be1b39462b0b1113eec60e156239f2b92c5c"
    ), "the collector's rules moved: bump MIGRATION_ENGINE_VERSION (ADR-0040 decision 2)"
