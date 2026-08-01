"""Plugin/Core compatibility resolution (ADR-0001, §30)."""

from __future__ import annotations

import pytest

from theurian.domain.compatibility import (
    CompatibilityDeclaration,
    CompatibilityOutcome,
    Version,
    resolve_compatibility,
)
from theurian.domain.errors import DomainError

DECLARATION = CompatibilityDeclaration(
    plugin_version=Version.parse("0.2.1"),
    core_minimum=Version.parse("0.4.0"),
    core_maximum_exclusive=Version.parse("0.5.0"),
    protocol_version="theurian/v1",
)


# -- SemVer ordering -------------------------------------------------------


@pytest.mark.parametrize(
    ("lower", "higher"),
    [
        ("0.1.0", "0.2.0"),
        ("0.4.9", "0.5.0"),
        ("1.0.0", "2.0.0"),
        ("0.4.0", "0.4.1"),
        # SemVer §11: a pre-release precedes its own release. If this were
        # backwards, `0.5.0-rc1` would slip past a `maximumExclusive` of 0.5.0.
        ("0.5.0-rc.1", "0.5.0"),
        ("0.5.0-alpha", "0.5.0-beta"),
        ("0.5.0-alpha.1", "0.5.0-alpha.2"),
        # Numeric identifiers rank below alphanumeric ones (SemVer §11.4.3).
        ("0.5.0-1", "0.5.0-alpha"),
        # A shorter identifier list ranks lower when the prefix is equal.
        ("0.5.0-alpha", "0.5.0-alpha.1"),
    ],
)
def test_version_ordering(lower: str, higher: str) -> None:
    assert Version.parse(lower) < Version.parse(higher)
    assert Version.parse(higher) > Version.parse(lower)


def test_build_metadata_is_excluded_from_precedence() -> None:
    """SemVer §10: build metadata does not affect ordering or equality."""
    assert Version.parse("1.0.0+build.1") == Version.parse("1.0.0+build.2")


def test_versions_are_hashable_consistently_with_equality() -> None:
    assert len({Version.parse("1.0.0+a"), Version.parse("1.0.0+b")}) == 1


@pytest.mark.parametrize("value", ["1.0", "v1.0.0", "1.0.0.0", "01.0.0", "", "latest"])
def test_malformed_versions_are_rejected(value: str) -> None:
    with pytest.raises(DomainError, match="Not a semantic version"):
        Version.parse(value)


# -- PEP 440 translation ---------------------------------------------------


@pytest.mark.parametrize(
    ("pep440", "expected"),
    [
        ("0.1.0", "0.1.0"),
        ("0.1.0.dev0", "0.1.0-dev.0"),
        ("0.2.0rc1", "0.2.0-rc.1"),
        ("0.2.0a3", "0.2.0-alpha.3"),
        ("0.2.0b1", "0.2.0-beta.1"),
        ("1.2", "1.2.0"),
        ("0.3.0.rc2", "0.3.0-rc.2"),
    ],
)
def test_pep440_translates_to_semver(pep440: str, expected: str) -> None:
    """Core is a Python package and reports PEP 440; plugins declare SemVer.

    Without this translation, every development build of Core would fail to
    parse and be reported as "Core not installed".
    """
    assert Version.parse_python(pep440) == Version.parse(expected)


def test_pep440_prereleases_sort_before_the_release() -> None:
    """Ordering survives the translation, in both directions."""
    assert Version.parse_python("0.1.0.dev0") < Version.parse("0.1.0")
    assert Version.parse_python("0.2.0rc1") < Version.parse_python("0.2.0")
    assert Version.parse_python("0.2.0a1") < Version.parse_python("0.2.0rc1")


def test_pep440_parser_accepts_plain_semver() -> None:
    assert Version.parse_python("1.4.2") == Version.parse("1.4.2")


# -- Declaration validation ------------------------------------------------


def test_inverted_range_is_rejected() -> None:
    with pytest.raises(DomainError, match="greater than minimum"):
        CompatibilityDeclaration(
            plugin_version=Version.parse("0.1.0"),
            core_minimum=Version.parse("0.5.0"),
            core_maximum_exclusive=Version.parse("0.4.0"),
            protocol_version="theurian/v1",
        )


@pytest.mark.parametrize("protocol", ["v1", "theurian/1", "theurian/v0", "theurian/vX", ""])
def test_malformed_protocol_version_is_rejected(protocol: str) -> None:
    with pytest.raises(DomainError, match="protocolVersion"):
        CompatibilityDeclaration(
            plugin_version=Version.parse("0.1.0"),
            core_minimum=Version.parse("0.1.0"),
            core_maximum_exclusive=Version.parse("0.2.0"),
            protocol_version=protocol,
        )


# -- Resolution ------------------------------------------------------------


def test_core_within_range_is_compatible() -> None:
    verdict = resolve_compatibility(DECLARATION, Version.parse("0.4.3"), "theurian/v1")
    assert verdict.outcome is CompatibilityOutcome.COMPATIBLE
    assert verdict.is_compatible
    assert verdict.remedy == ""


def test_minimum_is_inclusive() -> None:
    verdict = resolve_compatibility(DECLARATION, Version.parse("0.4.0"), "theurian/v1")
    assert verdict.is_compatible


def test_maximum_is_exclusive() -> None:
    verdict = resolve_compatibility(DECLARATION, Version.parse("0.5.0"), "theurian/v1")
    assert verdict.outcome is CompatibilityOutcome.CORE_TOO_NEW


def test_missing_core_is_reported_as_run_setup() -> None:
    """The normal "plugin installed, setup not yet run" case (FR-L3).

    It is not an error to repair automatically -- installing software from a
    compatibility check is exactly the surprising behaviour the design forbids.
    """
    verdict = resolve_compatibility(DECLARATION, None, None)
    assert verdict.outcome is CompatibilityOutcome.CORE_MISSING
    assert "/theurian:setup" in verdict.remedy


def test_old_core_advises_upgrading_core() -> None:
    verdict = resolve_compatibility(DECLARATION, Version.parse("0.3.9"), "theurian/v1")
    assert verdict.outcome is CompatibilityOutcome.CORE_TOO_OLD
    assert "upgrade" in verdict.remedy.lower()
    assert "0.4.0" in verdict.remedy


def test_new_core_advises_updating_the_plugin_not_core() -> None:
    """Downgrading Core to satisfy a plugin would break every other client."""
    verdict = resolve_compatibility(DECLARATION, Version.parse("0.6.0"), "theurian/v1")
    assert verdict.outcome is CompatibilityOutcome.CORE_TOO_NEW
    assert "plugin" in verdict.remedy.lower()
    assert "Core was not changed" in verdict.remedy


def test_protocol_mismatch_is_terminal_even_when_versions_fit() -> None:
    verdict = resolve_compatibility(DECLARATION, Version.parse("0.4.3"), "theurian/v2")
    assert verdict.outcome is CompatibilityOutcome.PROTOCOL_MISMATCH
    assert not verdict.is_compatible


def test_unknown_protocol_is_a_mismatch_not_an_assumption() -> None:
    """A daemon that reports no protocol is not assumed to speak ours."""
    verdict = resolve_compatibility(DECLARATION, Version.parse("0.4.3"), None)
    assert verdict.outcome is CompatibilityOutcome.PROTOCOL_MISMATCH


@pytest.mark.parametrize(
    ("core", "expected"),
    [
        ("0.3.9", CompatibilityOutcome.CORE_TOO_OLD),
        ("0.4.0", CompatibilityOutcome.COMPATIBLE),
        ("0.4.99", CompatibilityOutcome.COMPATIBLE),
        # A pre-release of the excluded ceiling is still below it, so it is
        # inside the supported range.
        ("0.5.0-rc.1", CompatibilityOutcome.COMPATIBLE),
        ("0.5.0", CompatibilityOutcome.CORE_TOO_NEW),
        ("1.0.0", CompatibilityOutcome.CORE_TOO_NEW),
    ],
)
def test_range_boundaries(core: str, expected: CompatibilityOutcome) -> None:
    verdict = resolve_compatibility(DECLARATION, Version.parse(core), "theurian/v1")
    assert verdict.outcome is expected


def test_every_incompatible_outcome_carries_a_remedy() -> None:
    """A verdict a user cannot act on is a verdict that wastes their time."""
    for core, protocol in [
        (None, None),
        (Version.parse("0.1.0"), "theurian/v1"),
        (Version.parse("9.0.0"), "theurian/v1"),
        (Version.parse("0.4.0"), "theurian/v9"),
    ]:
        verdict = resolve_compatibility(DECLARATION, core, protocol)
        assert not verdict.is_compatible
        assert verdict.remedy.strip()
        assert verdict.message.strip()
