"""Predictable YAML loading (SEC-8).

The timestamp behaviour here is not a nicety. It was found by validating a real
example migration against the published schema: `yaml.safe_load` had already
replaced the RFC 3339 string with a `datetime`, so the document failed its own
schema.
"""

from __future__ import annotations

import datetime

import pytest
import yaml

from theurian.domain.errors import InputTooLargeError
from theurian.security.yaml_loading import (
    MAX_YAML_BYTES,
    load_yaml,
    load_yaml_mapping,
)

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
