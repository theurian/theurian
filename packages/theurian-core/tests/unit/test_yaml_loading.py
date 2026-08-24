"""Predictable YAML loading (SEC-8).

The timestamp behaviour here is not a nicety. It was found by validating a real
example migration against the published schema: `yaml.safe_load` had already
replaced the RFC 3339 string with a `datetime`, so the document failed its own
schema.
"""

from __future__ import annotations

import datetime
from collections.abc import Callable

import pytest
import yaml

from theurian.domain.errors import InputTooLargeError
from theurian.security.yaml_loading import (
    MAX_RENDERED_SCALAR_CHARS,
    MAX_YAML_BYTES,
    is_bounded_scalar,
    load_yaml,
    load_yaml_mapping,
)


def _alias_bomb(levels: int, fan: int) -> str:
    """A YAML document whose ``bomb`` key aliases a DAG of ``fan**levels`` leaves.

    The reviewers' own shape (``repro_config_bomb.sh``, ``e19_proposal_bomb.sh``):
    each anchor references the one below it ``fan`` times, so parsing stays
    O(levels) while ``repr`` of the expanded value is exponential. A few hundred
    bytes here renders to gigabytes if anything ever walks it as a tree.
    """
    lines = ["a0: &a0 'x'"]
    for level in range(1, levels + 1):
        refs = ", ".join(f"*a{level - 1}" for _ in range(fan))
        lines.append(f"a{level}: &a{level} [{refs}]")
    lines.append(f"bomb: *a{levels}")
    return "\n".join(lines)


MIGRATION = """
apiVersion: theurian.dev/v1
id: 01K1ABCXYZ01234567890ABCDE
createdAt: 2026-07-15T10:00:00+09:00
author: platform-team@example.com
"""


def test_timestamps_stay_strings() -> None:
    """The document that validates must be the document that was written."""
    loaded = load_yaml_mapping(MIGRATION)
    assert loaded["createdAt"] == "2026-07-15T10:00:00+09:00"
    assert isinstance(loaded["createdAt"], str)


def test_this_differs_from_yaml_safe_load() -> None:
    """Documents the exact coercion this module exists to prevent."""
    coerced = yaml.safe_load(MIGRATION)["createdAt"]
    assert isinstance(coerced, datetime.datetime)

    preserved = load_yaml_mapping(MIGRATION)["createdAt"]
    assert isinstance(preserved, str)


@pytest.mark.parametrize(
    "value",
    [
        "2026-07-15",
        "2026-07-15T10:00:00Z",
        "2026-07-15T10:00:00+09:00",
        "2026-07-15 10:00:00",
        "2026-07-15t10:00:00.123456Z",
    ],
)
def test_every_timestamp_spelling_stays_a_string(value: str) -> None:
    """PyYAML's timestamp resolver accepts several formats; all must be inert."""
    assert isinstance(load_yaml_mapping(f"when: {value}")["when"], str)


def test_yaml_safe_load_elsewhere_is_unaffected() -> None:
    """The resolver is removed from a subclass, not from PyYAML globally.

    Mutating `SafeLoader` in place would change behaviour for every library in
    the process, which is exactly the kind of action-at-a-distance that produces
    a bug nobody can locate.
    """
    assert isinstance(yaml.safe_load("when: 2026-07-15")["when"], datetime.date)


def test_other_scalar_types_still_resolve() -> None:
    """Only timestamps are affected; the rest of YAML behaves normally."""
    loaded = load_yaml_mapping(
        """
        count: 42
        ratio: 0.5
        enabled: true
        absent: null
        text: hello
        items: [a, b]
        """
    )
    assert loaded["count"] == 42
    assert loaded["ratio"] == 0.5
    assert loaded["enabled"] is True
    assert loaded["absent"] is None
    assert loaded["text"] == "hello"
    assert loaded["items"] == ["a", "b"]


def test_arbitrary_object_construction_is_refused() -> None:
    """The loader derives from SafeLoader, so `!!python/object` has no resolver."""
    with pytest.raises(yaml.YAMLError):
        load_yaml("value: !!python/object/apply:os.system ['echo pwned']")


def test_oversized_document_is_refused() -> None:
    with pytest.raises(InputTooLargeError) as exc:
        load_yaml("a: b", max_bytes=2)
    assert exc.value.limit == 2


def test_default_size_limit_is_bounded() -> None:
    assert 64 * 1024 <= MAX_YAML_BYTES <= 16 * 1024 * 1024


def test_size_is_measured_in_bytes_not_characters() -> None:
    """A multi-byte document must not slip past a byte limit."""
    document = "key: " + "あ" * 10  # 3 bytes each in UTF-8
    with pytest.raises(InputTooLargeError):
        load_yaml(document, max_bytes=20)


@pytest.mark.parametrize("document", ["- a\n- b", "just a scalar", "42"])
def test_non_mapping_root_is_refused(document: str) -> None:
    """Migrations and configuration are always mappings.

    Saying so here beats a KeyError three layers up.
    """
    with pytest.raises(ValueError, match="mapping at the document root"):
        load_yaml_mapping(document)


def test_malformed_yaml_raises() -> None:
    with pytest.raises(yaml.YAMLError):
        load_yaml("key: [unclosed")


@pytest.mark.parametrize("loader", [load_yaml, load_yaml_mapping])
def test_excessive_nesting_raises_value_error_not_recursion_error(
    loader: Callable[[str], object],
) -> None:
    """Adversarial HIGH (round two, orchestrator-reproduced): a document
    nested past PyYAML's own recursion limit -- 495 bracket pairs is already
    enough, measured directly -- makes ``yaml.load`` raise ``RecursionError``.

    Corrected (round three): ``RecursionError`` is a ``RuntimeError``
    subclass, in turn an ``Exception`` subclass -- not, as an earlier
    revision of this docstring claimed, something outside ``Exception``'s
    hierarchy that only a bare ``except BaseException`` could reach.
    ``except Exception`` would have caught it perfectly well. What actually
    let it through is narrower and more mundane: no ``except`` clause on the
    migration-load path ever named ``RuntimeError`` or ``RecursionError`` at
    all -- ``_load_one``'s clauses around ``load_yaml_mapping``
    (``migration_loader.py``) name only ``UnicodeDecodeError``,
    ``ValueError``, and ``yaml.YAMLError``, and ``RecursionError`` is none of
    those. Reproduced against the real CLI: it sailed past every one of them
    and reached ``resolve_context`` as a raw traceback under ``--json``, on a
    1023-byte document. Depth 1000 here, roughly double the measured leak
    threshold, so this stays red even if PyYAML's own recursion cost per
    nesting level shifts between versions.

    ``ValueError`` is the target, not merely "not ``RecursionError``",
    because it is the one type every existing consumer on this path already
    catches -- ``load_yaml_mapping``'s own non-mapping-root check
    (:func:`test_non_mapping_root_is_refused` above) and ``_load_one``'s
    ``except ValueError`` (``migration_loader.py``) both already handle it,
    so translating here needs no new catch clause anywhere downstream.
    """
    document = "apiVersion: theurian.dev/v1\nid: " + "[" * 1000 + "]" * 1000 + "\n"

    with pytest.raises(ValueError, match="nesting depth"):
        loader(document)


@pytest.mark.parametrize(
    ("label", "value"),
    [
        ("a short string", "block"),
        ("an empty string", ""),
        ("a string at the ceiling", "x" * MAX_RENDERED_SCALAR_CHARS),
        ("short bytes", b"payload"),
        ("true", True),
        ("false", False),
        ("none", None),
        ("a small integer", 42),
        ("a negative integer", -7),
        ("a float", 1.5),
        ("a date", datetime.date(2026, 1, 1)),
        ("a datetime", datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC)),
    ],
)
def test_a_bounded_scalar_is_safe_to_render(label: str, value: object) -> None:
    """A value a caller may interpolate with ``repr`` into a bounded message.

    Each of these renders to a small, fixed width, so a field guard admits it and
    lets the caller's own validation (and its own ``{value!r}``) proceed.
    """
    assert is_bounded_scalar(value), label


@pytest.mark.parametrize(
    ("label", "value"),
    [
        ("an empty list", []),
        ("an empty dict", {}),
        ("an empty set", set()),
        ("a tuple", ("a", "b")),
        ("a populated mapping", {"a": 1}),
        ("an oversized string", "x" * (MAX_RENDERED_SCALAR_CHARS + 1)),
        ("oversized bytes", b"x" * (MAX_RENDERED_SCALAR_CHARS + 1)),
        # bit_length 2002, one past the 2000-bit ceiling: rendering it in decimal
        # is the quadratic-and-then-raising cost the bound refuses.
        ("a giant integer", 2**2001),
    ],
)
def test_a_container_or_giant_scalar_is_refused(label: str, value: object) -> None:
    """The values whose ``repr`` a field guard must refuse before rendering.

    A container is the T-6 alias-expansion carrier -- ``repr`` re-expands the DAG
    a YAML alias graph collapsed -- and a giant scalar renders unboundedly. A
    field that must be a short identifier or selector is neither.
    """
    assert not is_bounded_scalar(value), label


def test_the_predicate_refuses_the_object_an_alias_bomb_parses_to() -> None:
    """The exact object the reviewers' bomb produces, refused in O(1).

    ``load_yaml`` collapses the aliases to one shared list, so parsing an
    18-level, fan-8 bomb is cheap -- and :func:`is_bounded_scalar` never walks it,
    it reads its type. Nothing here expands the ``8**18`` leaves; that is the
    whole point of refusing *before* a render.
    """
    parsed = load_yaml(_alias_bomb(levels=18, fan=8))

    assert not is_bounded_scalar(parsed["bomb"])
