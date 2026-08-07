"""Plugin/Core compatibility resolution (ADR-0001, §30)."""

from __future__ import annotations

import itertools
from typing import Final

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
        ("0.2.0a1.dev3", "0.2.0-alpha.1.dev.3"),
        # PEP 440 makes both segment numbers optional and defaults each to 0.
        # A numberless dev segment used to be dropped whole, which parsed a
        # development build as the finished release it precedes.
        ("0.2.0a", "0.2.0-alpha.0"),
        ("0.2.0.dev", "0.2.0-dev.0"),
        ("0.2.0rc1.dev", "0.2.0-rc.1.dev.0"),
    ],
)
def test_pep440_translates_to_semver(pep440: str, expected: str) -> None:
    """Core is a Python package and reports PEP 440; plugins declare SemVer.

    Without this translation, every development build of Core would fail to
    parse and be reported as "Core not installed".
    """
    assert Version.parse_python(pep440) == Version.parse(expected)


@pytest.mark.parametrize(
    ("lower", "higher"),
    [
        # A pre-release of any kind sorts below its own release.
        ("0.1.0.dev0", "0.1.0"),
        ("0.2.0rc1", "0.2.0"),
        ("0.2.0a1", "0.2.0"),
        # Within one release the phases run in PEP 440's order. `dev` below
        # `alpha` and `beta` is the pair a literal SemVer §11.4.2 gets
        # backwards: it compares the words as ASCII, and "alpha" < "beta" <
        # "dev". A floor of `0.1.0-dev.0` therefore used to accept `0.1.0.dev1`
        # and `0.1.0rc1` while refusing every alpha and beta between them.
        ("0.2.0.dev9", "0.2.0a1"),
        ("0.2.0.dev9", "0.2.0b1"),
        ("0.2.0.dev9", "0.2.0rc1"),
        ("0.2.0a1", "0.2.0b1"),
        ("0.2.0b1", "0.2.0rc1"),
        ("0.2.0a1", "0.2.0rc1"),
        # A development build of a pre-release sorts below that pre-release.
        # SemVer §11.4.4 gets this one backwards the other way: the longer
        # identifier list ranks higher, so `alpha.1.dev.1` outranked `alpha.1`.
        ("0.2.0a1.dev1", "0.2.0a1"),
        ("0.2.0b1.dev0", "0.2.0b1"),
        ("0.2.0rc1.dev0", "0.2.0rc1"),
        # ...but only below *its own* pre-release. A later phase number still
        # wins, so the dev marker must not lower the whole comparison.
        ("0.2.0a1", "0.2.0a2.dev0"),
        ("0.2.0a9.dev0", "0.2.0b0.dev0"),
    ],
)
def test_pep440_ordering_survives_the_translation(lower: str, higher: str) -> None:
    """Every pair here crosses a kind: dev against a phase, or a phase against
    its own development build. Same-kind pairs -- `a1` against `a2` -- are the
    ones the translation never got wrong, so asserting only those said nothing.
    """
    assert Version.parse_python(lower) < Version.parse_python(higher)
    assert Version.parse_python(higher) > Version.parse_python(lower)


@pytest.mark.parametrize(
    ("lower", "higher"),
    [
        ("0.2.0-dev.0", "0.2.0-alpha.0"),
        ("0.2.0-dev.9", "0.2.0-beta.1"),
        ("0.2.0-alpha.1.dev.1", "0.2.0-alpha.1"),
        ("0.2.0-rc.1", "0.2.0"),
    ],
)
def test_the_declaration_side_takes_the_same_order(lower: str, higher: str) -> None:
    """A floor is written in SemVer and compared against a translated PEP 440
    version, so the release-train order has to reach both sides. Applying it to
    Core's version alone would move the version and leave the floor behind.
    """
    assert Version.parse(lower) < Version.parse(higher)
    assert Version.parse(higher) > Version.parse(lower)


@pytest.mark.parametrize("value", ["1.0.0+build.7", "1.0.0-alpha.beta"])
def test_pep440_parser_accepts_plain_semver(value: str) -> None:
    """Both inputs need the fallback: the PEP 440 pattern spells neither build
    metadata nor a dotted alphanumeric pre-release, so it declines and the
    SemVer parser takes over.

    ``1.4.2`` used to stand here and proved nothing -- the PEP 440 pattern
    matches it on its own, so deleting the fallback left this test green.
    """
    assert Version.parse_python(value) == Version.parse(value)


#: Every version Core's release process can spell inside one release. Ordered
#: by construction from PEP 440 §"Version scheme" rather than by sorting with
#: the code under test: ``.devN`` precedes every pre-release phase, the phases
#: run ``a`` < ``b`` < ``rc``, a development build of a pre-release precedes
#: that pre-release, and the final release is last.
_PHASE_NUMBERS: Final = (0, 1, 2)
_DEV_NUMBERS: Final = (0, 1, 2)


def _release_train(release: str) -> list[str]:
    ordered = [f"{release}.dev{dev}" for dev in _DEV_NUMBERS]
    for phase in ("a", "b", "rc"):
        for number in _PHASE_NUMBERS:
            ordered.extend(f"{release}{phase}{number}.dev{dev}" for dev in _DEV_NUMBERS)
            ordered.append(f"{release}{phase}{number}")
    ordered.append(release)
    return ordered


#: Two adjacent releases, so the train crosses a release boundary too.
#: Exhaustive over a bounded grammar rather than sampled: the grammar has four
#: phases and two numeric positions, and enumerating it costs less than a
#: generator that would still leave the reader asking which cases it drew.
_RELEASE_TRAIN: Final = tuple(_release_train("0.1.0") + _release_train("0.2.0"))


def test_the_pep440_translation_is_strictly_monotone() -> None:
    """Upward closure rests on exactly this: order in, order out.

    Every pair, not only adjacent ones. A comparison that is not transitive
    would satisfy all 79 adjacent pairs and still leave a floor with a hole.
    """
    translated = [Version.parse_python(value) for value in _RELEASE_TRAIN]
    inverted = [
        (_RELEASE_TRAIN[i], _RELEASE_TRAIN[j])
        for i, j in itertools.combinations(range(len(_RELEASE_TRAIN)), 2)
        if not translated[i] < translated[j]
    ]
    assert not inverted, (
        f"{len(inverted)} of "
        f"{len(_RELEASE_TRAIN) * (len(_RELEASE_TRAIN) - 1) // 2} pairs inverted, "
        f"first {inverted[0][0]} !< {inverted[0][1]}"
    )


#: Floors a declaration could plausibly write, including the one
#: ``plugins/claude-code/compatibility.yaml`` ships.
_FLOORS: Final = (
    "0.1.0-dev.0",
    "0.1.0-alpha.0",
    "0.1.0-alpha.1.dev.1",
    "0.1.0-beta.2",
    "0.1.0-rc.0",
    "0.1.0",
    "0.2.0-dev.1",
)


@pytest.mark.parametrize("minimum", _FLOORS)
def test_a_minimum_accepts_every_core_above_it(minimum: str) -> None:
    """A floor whose accepted set is not upward-closed is not a floor.

    ``minimum: 0.1.0-dev.0`` -- the floor this repository's own declaration
    carries -- accepted ``0.1.0.dev1``, refused ``0.1.0a1``, ``0.1.0a2`` and
    ``0.1.0b1``, then accepted ``0.1.0rc1`` again. A hole in the middle of a
    floor is not a stricter floor; it is a floor that means nothing.
    """
    declaration = CompatibilityDeclaration(
        plugin_version=Version.parse("0.1.0"),
        core_minimum=Version.parse(minimum),
        # High enough that nothing on the train is refused as too new, so the
        # only reason left for a refusal is the floor.
        core_maximum_exclusive=Version.parse("9.0.0"),
        protocol_version="theurian/v1",
    )
    accepted = [
        resolve_compatibility(declaration, Version.parse_python(core), "theurian/v1").is_compatible
        for core in _RELEASE_TRAIN
    ]
    assert any(accepted), f"minimum {minimum} accepted nothing on the release train"
    first = accepted.index(True)
    holes = [_RELEASE_TRAIN[i] for i in range(first, len(accepted)) if not accepted[i]]
    assert not holes, f"minimum {minimum} accepted {_RELEASE_TRAIN[first]} and then refused {holes}"


#: Ceilings a declaration could plausibly write. `maximumExclusive` has to be
#: above the minimum, and `0.0.1` is below the whole train.
_CEILINGS: Final = (
    "0.1.0-alpha.1",
    "0.1.0-rc.0",
    "0.1.0",
    "0.2.0-dev.1",
    "0.2.0-beta.2",
    "0.2.0",
)


@pytest.mark.parametrize("maximum_exclusive", _CEILINGS)
def test_a_maximum_refuses_every_core_at_or_above_it(maximum_exclusive: str) -> None:
    """The mirror of upward closure, and the same root cause.

    A ceiling is the other end of the same comparison, so a translation that
    inverts order punches the same hole downwards: once a Core is refused as too
    new, every Core above it has to be refused too. Testing only the floor would
    have closed one face of the defect and left the other.
    """
    declaration = CompatibilityDeclaration(
        plugin_version=Version.parse("0.1.0"),
        # Below the whole train, so the only reason left for a refusal is the
        # ceiling.
        core_minimum=Version.parse("0.0.1"),
        core_maximum_exclusive=Version.parse(maximum_exclusive),
        protocol_version="theurian/v1",
    )
    refused = [
        resolve_compatibility(declaration, Version.parse_python(core), "theurian/v1").outcome
        is CompatibilityOutcome.CORE_TOO_NEW
        for core in _RELEASE_TRAIN
    ]
    assert any(refused), f"maximumExclusive {maximum_exclusive} refused nothing on the train"
    first = refused.index(True)
    holes = [_RELEASE_TRAIN[i] for i in range(first, len(refused)) if not refused[i]]
    assert not holes, (
        f"maximumExclusive {maximum_exclusive} refused {_RELEASE_TRAIN[first]} "
        f"and then accepted {holes}"
    )


#: Release-train forms mixed with pre-release shapes that are not on any release
#: train -- a bare numeric, a phase with no number, words on either side of
#: ``dev`` in ASCII, and the two identifiers a forgeable ordering marker would
#: have collided with.
_ORDER_CORPUS: Final = (
    "0.1.0",
    "0.2.0",
    "0.2.1",
    "0.2.0-dev.0",
    "0.2.0-dev.1",
    "0.2.0-alpha.0",
    "0.2.0-alpha.1",
    "0.2.0-alpha.1.dev.0",
    "0.2.0-beta.1",
    "0.2.0-rc.1",
    "0.2.0-0",
    "0.2.0-1",
    "0.2.0-alpha",
    "0.2.0-dev",
    "0.2.0-cat",
    "0.2.0-zeta",
    "0.2.0-alpha.beta",
    "0.2.0-alpha.1.final",
    "0.2.0-0dev.1",
)


def test_version_ordering_is_a_total_order() -> None:
    """The release-train rule must not cost transitivity.

    Ranking ``dev`` below ``alpha`` only when both sides are release-train
    forms, and by ASCII otherwise, is the obvious implementation and it is
    cyclic: ``cat`` sits between the two words in ASCII, so ``dev < alpha``
    by the train rule, ``alpha < cat`` and ``cat < dev`` by ASCII. ``Version``
    therefore rewrites first and compares once, and this holds that.

    Measured, not assumed: substituting that implementation makes this test
    report ``0.2.0-dev.0 < 0.2.0-alpha.0 < 0.2.0-cat but not 0.2.0-dev.0 <
    0.2.0-cat``. Deleting the rewrite altogether leaves it green, because plain
    SemVer §11.4 is already total -- so this one guards the fix rather than
    reproducing the defect, and the two closure tests above are the ones that
    go red on unmodified source.
    """
    # The premise, measured rather than asserted -- those three words really do
    # fall in that ASCII order, which is what closes the cycle. `0.2.0-cat` is
    # in the corpus below for the same reason.
    assert sorted(("dev", "cat", "alpha")) == ["alpha", "cat", "dev"]

    versions = [Version.parse(value) for value in _ORDER_CORPUS]
    for left, right in itertools.product(versions, repeat=2):
        trichotomy = (left < right) + (right < left) + (left == right)
        assert trichotomy == 1, f"{left} vs {right} satisfies {trichotomy} of <, >, =="
    for left, middle, right in itertools.product(versions, repeat=3):
        if left < middle < right:
            assert left < right, f"{left} < {middle} < {right} but not {left} < {right}"


def test_ordering_markers_cannot_be_spelled_by_a_declaration() -> None:
    """The rewrite distinguishes versions with two markers outside SemVer §9's
    identifier alphabet. That they are unspellable is what makes it injective,
    and injectivity is what keeps ``<`` consistent with ``==``.
    """
    for identifier in ("!dev", "~final"):
        with pytest.raises(DomainError, match="pre-release identifier"):
            Version(0, 2, 0, prerelease=(identifier,))


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


def test_missing_core_is_reported_as_install_then_setup() -> None:
    """The normal "plugin installed, Core not yet installed" case (FR-L3).

    It is not an error to repair automatically -- installing software from a
    compatibility check is exactly the surprising behaviour the design forbids.

    The remedy has to name an installer, not only ``/theurian:setup``. Setup
    shells out to the ``theurian`` binary whose absence produced this verdict,
    so a remedy that named setup alone would be advice the user cannot follow.
    """
    verdict = resolve_compatibility(DECLARATION, None, None)
    assert verdict.outcome is CompatibilityOutcome.CORE_MISSING
    assert "uv tool install theurian" in verdict.remedy
    assert "pipx install theurian" in verdict.remedy
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
