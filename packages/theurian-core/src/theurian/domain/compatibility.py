"""Plugin/Core compatibility resolution (ADR-0001, §30 of the brief).

Three versions move independently: the Core version, the plugin version, and the
wire protocol version. The plugin declares which Core range and protocol it
supports; this module decides whether to proceed.

A mismatch is always terminal. Theurian never installs, upgrades, downgrades, or
deletes anything to resolve one -- it reports and stops.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from functools import total_ordering
from typing import Final, Self, override

from theurian.domain.errors import DomainError

#: Semantic version with an optional pre-release and build metadata.
_SEMVER_PATTERN: Final = re.compile(
    r"^(?P<major>0|[1-9]\d*)"
    r"\.(?P<minor>0|[1-9]\d*)"
    r"\.(?P<patch>0|[1-9]\d*)"
    r"(?:-(?P<prerelease>[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?"
    r"(?:\+(?P<build>[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?$"
)

#: PEP 440 release with an optional pre-release and/or development segment.
#: Core is a Python package and reports PEP 440; plugins declare SemVer ranges.
_PEP440_PATTERN: Final = re.compile(
    r"^(?P<major>0|[1-9]\d*)"
    r"\.(?P<minor>0|[1-9]\d*)"
    r"(?:\.(?P<patch>0|[1-9]\d*))?"
    r"(?:[._-]?(?P<pre_label>a|b|c|rc|alpha|beta|pre|preview)[._-]?(?P<pre_number>\d+)?)?"
    # ``dev`` is captured rather than matched literally because the segment's
    # presence and its number are separate facts. PEP 440 makes the number
    # optional and defaults it to 0, so a parser that keys off the number alone
    # drops ``0.2.0.dev`` whole and reads a development build as the finished
    # release it precedes.
    r"(?:[._-]?(?P<dev_label>dev)[._-]?(?P<dev_number>\d+)?)?$",
    re.IGNORECASE,
)

#: PEP 440 spells the same pre-release phase several ways. Normalise so that
#: ``0.2.0a1`` and ``0.2.0alpha1`` compare identically.
_PEP440_PRE_LABELS: Final = {
    "a": "alpha",
    "alpha": "alpha",
    "b": "beta",
    "beta": "beta",
    "c": "rc",
    "rc": "rc",
    "pre": "rc",
    "preview": "rc",
}

#: The wire protocol this build of Core speaks.
CURRENT_PROTOCOL_VERSION: Final = "theurian/v1"

#: ``^``/``$``, not ``\A``/``\Z``, and the difference is a known open finding
#: rather than an oversight. Python's ``$`` also matches immediately before a
#: trailing newline, so ``"theurian/v1\n"`` constructs here while the published
#: ``schemas/protocol/compatibility.schema.json`` pattern refuses it under
#: ECMA-262 and RE2, where ``$`` means end of input. ``domain/identifiers.py``
#: took the other answer for the four identifier types in ``80f94b6``; this
#: pattern, ``MediaType`` and ``ContentHash`` are its three remaining siblings.
#:
#: **Measured, because ``80f94b6`` recorded a claim about this one without
#: measuring it.** The claim was that it is fail-closed and costs only a confusing
#: error message. Both halves hold: ``resolve_compatibility`` returns
#: ``PROTOCOL_MISMATCH`` and the CLI exits 3, ``CompatibilityVerdict``'s published
#: ``protocolVersion`` carries Core's clean value rather than the declaration's,
#: and the message reads ``plugin speaks theurian/v1\n, Core speaks
#: theurian/v1.`` -- two strings a human reads as one.
#:
#: What the claim left out is the third face: the constructor and the published
#: schema disagree about a value, which is the same defect the agreement oracle in
#: ``tests/unit/test_schemas.py`` holds for ``itemId``, ``revisionId``,
#: ``contentType`` and ``projectId``. That schema is not in the oracle at all.
#: ``MediaType``'s disagreement is held by a strict xfail; this one and
#: ``ContentHash``'s are held by nothing. All three are filed for Milestone 6 as
#: https://github.com/theurian/theurian/issues/28, which also owes the two
#: missing fields to the agreement oracle.
_PROTOCOL_PATTERN: Final = re.compile(r"^theurian/v[1-9]\d*$")

#: SemVer §9's pre-release identifier alphabet, matching what ``_SEMVER_PATTERN``
#: accepts. :func:`_release_train_order` introduces two markers outside it, and
#: their being unspellable is what makes that rewrite injective.
_PRERELEASE_IDENTIFIER: Final = re.compile(r"^[0-9A-Za-z-]+$")

#: The pre-release phases of a Python release train, in PEP 440's order. ``dev``
#: is handled separately: it names a phase of its own when it leads, and a
#: development build of the phase beside it when it follows one.
_TRAIN_PHASES: Final = frozenset({"alpha", "beta", "rc"})

#: Stands in for a leading ``dev``. ``!`` is 0x21, below ``-``, the digits and
#: every letter, so SemVer's own ASCII comparison then places a development
#: release under every pre-release phase, which is where PEP 440 puts it.
_DEV_PHASE_MARKER: Final = "!dev"

#: Fills the slot a development segment would occupy on a pre-release that has
#: none. ``~`` is 0x7E, above every letter, so ``alpha.1`` outranks
#: ``alpha.1.dev.9`` -- the direction PEP 440 wants and SemVer §11.4.4, which
#: ranks the longer identifier list higher, gives the wrong way round.
_NO_DEV_MARKER: Final = "~final"


def _release_train_order(prerelease: tuple[str, ...]) -> tuple[str, ...]:
    """Rewrite a pre-release list so that SemVer §11.4 reproduces PEP 440.

    Core's version names a point on a Python release train; a plugin's
    ``minimum`` and ``maximumExclusive`` name two more, in SemVer spelling. They
    are compared against each other, so they have to be read on the same train
    -- and PEP 440's order is not SemVer's. Two rules disagree:

    * PEP 440 puts ``.devN`` below every pre-release phase. SemVer §11.4.2
      compares the words as ASCII, which puts ``dev`` between ``beta`` and
      ``rc``.
    * PEP 440 puts ``aN.devM`` below ``aN``. SemVer §11.4.4 ranks the longer
      identifier list higher, which puts ``alpha.N.dev.M`` above ``alpha.N``.

    Together they punched a hole in the middle of a floor: ``minimum:
    0.1.0-dev.0`` accepted ``0.1.0.dev1``, refused every alpha and beta, then
    accepted ``0.1.0rc1`` again. A minimum whose accepted set is not
    upward-closed is not a minimum.

    Rewriting rather than special-casing the comparison is what keeps the order
    total. Consulting the train ranks only when both sides are train forms and
    falling back to ASCII otherwise is the obvious implementation and it is
    cyclic: ``cat`` sits between ``alpha`` and ``dev`` in ASCII, so ``dev`` <
    ``alpha`` < ``cat`` < ``dev``. Here every list is rewritten first and
    compared once, by one rule.

    The rewrite is injective over every value :meth:`Version.parse` and
    :meth:`Version.parse_python` can produce, so ``<`` stays consistent with
    ``==``: both markers it introduces are outside SemVer's identifier alphabet,
    and ``__post_init__`` refuses a version that spells one.

    A list that is not a release-train form is returned unchanged, so SemVer's
    order is what holds between two of those. Between a train form and one of
    those the answer is defined but arbitrary -- PEP 440 has no such version,
    and nothing on either side of this comparison can produce one.
    """
    match prerelease:
        case ("dev", *rest):
            return (_DEV_PHASE_MARKER, *rest)
        case (phase, number) if phase in _TRAIN_PHASES and number.isdigit():
            return (phase, number, _NO_DEV_MARKER)
        case _:
            return prerelease


@total_ordering
@dataclass(frozen=True, slots=True)
class Version:
    """A semantic version.

    Ordering follows SemVer §11, including the rule that a pre-release sorts
    *before* its own release. Getting that backwards would let a plugin accept
    ``0.5.0-rc1`` under a ``maximumExclusive`` of ``0.5.0``.

    The one deliberate departure is the pre-release *phase*, which follows
    Core's release train instead of ASCII -- ``dev`` < ``alpha`` < ``beta`` <
    ``rc``, and a development build below the pre-release it precedes. Both
    sides of every comparison here name a point on that train, so ordering them
    by the alphabet is what would be wrong. :func:`_release_train_order` carries
    the argument.
    """

    major: int
    minor: int
    patch: int
    prerelease: tuple[str, ...] = ()
    build: str | None = None

    def __post_init__(self) -> None:
        for identifier in self.prerelease:
            if not _PRERELEASE_IDENTIFIER.match(identifier):
                raise DomainError(
                    f"Not a SemVer pre-release identifier: {identifier!r}. "
                    "Identifiers are ASCII alphanumerics and hyphens; build a version "
                    "with Version.parse('0.1.0-dev.0') rather than by hand."
                )

    @classmethod
    def parse(cls, value: str) -> Self:
        match = _SEMVER_PATTERN.match(value.strip())
        if match is None:
            raise DomainError(f"Not a semantic version: {value!r}")
        prerelease_raw = match.group("prerelease")
        return cls(
            major=int(match.group("major")),
            minor=int(match.group("minor")),
            patch=int(match.group("patch")),
            prerelease=tuple(prerelease_raw.split(".")) if prerelease_raw else (),
            build=match.group("build"),
        )

    @classmethod
    def parse_python(cls, value: str) -> Self:
        """Parse a PEP 440 version into SemVer semantics.

        Core is a Python package, so it reports PEP 440 strings such as
        ``0.1.0.dev0`` or ``0.2.0rc1``. Plugins declare SemVer ranges. Without a
        translation, ``0.1.0.dev0`` would fail to parse and every development
        build would look like "Core not installed".

        The mapping is strictly monotone: for any two PEP 440 versions, the
        translated pair sorts the same way the originals do. Everything the
        compatibility range promises rests on that, because a minimum's accepted
        set is upward-closed only if the translation preserves order.

        It is monotone by :func:`_release_train_order`, not by the spelling. The
        SemVer strings this returns are the readable ones -- ``0.1.0-dev.0``,
        ``0.2.0-alpha.3.dev.1`` -- and reading *them* under a literal SemVer
        §11.4 puts every development release above every alpha and beta of its
        own release, and every ``aN.devM`` above ``aN``. Over the release train
        ``tests/unit/test_compatibility.py`` enumerates -- 40 versions of one
        release, so 780 ordered pairs -- that is 99 pairs the wrong way round.
        """
        text = value.strip()
        match = _PEP440_PATTERN.match(text)
        if match is None:
            # A plain SemVer string is also valid input; try it before failing.
            return cls.parse(text)

        prerelease: list[str] = []
        if match.group("pre_label"):
            label = _PEP440_PRE_LABELS[match.group("pre_label").lower()]
            prerelease.extend((label, match.group("pre_number") or "0"))
        if match.group("dev_label"):
            prerelease.extend(("dev", match.group("dev_number") or "0"))

        return cls(
            major=int(match.group("major")),
            minor=int(match.group("minor")),
            patch=int(match.group("patch") or "0"),
            prerelease=tuple(prerelease),
        )

    @property
    def is_prerelease(self) -> bool:
        return bool(self.prerelease)

    @property
    def next_minor(self) -> Version:
        return Version(self.major, self.minor + 1, 0)

    @property
    def next_major(self) -> Version:
        return Version(self.major + 1, 0, 0)

    def _core(self) -> tuple[int, int, int]:
        return (self.major, self.minor, self.patch)

    @override
    def __eq__(self, other: object) -> bool:
        # Build metadata is explicitly excluded from precedence (SemVer §10).
        if not isinstance(other, Version):
            return NotImplemented
        return self._core() == other._core() and self.prerelease == other.prerelease

    @override
    def __hash__(self) -> int:
        return hash((self._core(), self.prerelease))

    def __lt__(self, other: object) -> bool:
        if not isinstance(other, Version):
            return NotImplemented
        if self._core() != other._core():
            return self._core() < other._core()
        if self.prerelease == other.prerelease:
            return False
        # A version with a pre-release precedes the associated release.
        if not self.prerelease:
            return False
        if not other.prerelease:
            return True
        return (
            _compare_prerelease(
                _release_train_order(self.prerelease), _release_train_order(other.prerelease)
            )
            < 0
        )

    @override
    def __str__(self) -> str:
        text = f"{self.major}.{self.minor}.{self.patch}"
        if self.prerelease:
            text += "-" + ".".join(self.prerelease)
        if self.build:
            text += f"+{self.build}"
        return text


def _compare_prerelease(left: tuple[str, ...], right: tuple[str, ...]) -> int:
    """Compare pre-release identifier lists per SemVer §11.4.

    This is §11.4 and nothing else. What makes :class:`Version` order by Core's
    release train instead is that its only caller hands both lists through
    :func:`_release_train_order` first, so these are rewritten identifiers
    rather than the ones a declaration wrote.
    """
    for left_id, right_id in zip(left, right, strict=False):
        if left_id == right_id:
            continue
        left_numeric = left_id.isdigit()
        right_numeric = right_id.isdigit()
        if left_numeric and right_numeric:
            return -1 if int(left_id) < int(right_id) else 1
        # Numeric identifiers always have lower precedence than alphanumeric ones.
        if left_numeric != right_numeric:
            return -1 if left_numeric else 1
        return -1 if left_id < right_id else 1
    # A shorter list of otherwise-equal identifiers has lower precedence.
    if len(left) == len(right):
        return 0
    return -1 if len(left) < len(right) else 1


class CompatibilityOutcome(StrEnum):
    """The decision reached by :func:`resolve_compatibility`."""

    COMPATIBLE = "compatible"
    CORE_MISSING = "core-missing"
    CORE_TOO_OLD = "core-too-old"
    CORE_TOO_NEW = "core-too-new"
    PROTOCOL_MISMATCH = "protocol-mismatch"


@dataclass(frozen=True, slots=True)
class CompatibilityDeclaration:
    """What ``plugins/claude-code/compatibility.yaml`` declares."""

    plugin_version: Version
    core_minimum: Version
    core_maximum_exclusive: Version
    protocol_version: str

    def __post_init__(self) -> None:
        if self.core_maximum_exclusive <= self.core_minimum:
            raise DomainError(
                f"coreCompatibility.maximumExclusive ({self.core_maximum_exclusive}) must be "
                f"greater than minimum ({self.core_minimum})"
            )
        if not _PROTOCOL_PATTERN.match(self.protocol_version):
            raise DomainError(
                f"protocolVersion must look like 'theurian/vN', got {self.protocol_version!r}"
            )


@dataclass(frozen=True, slots=True)
class CompatibilityVerdict:
    """The resolution result, including the remedy to show the user."""

    outcome: CompatibilityOutcome
    message: str
    remedy: str
    plugin_version: Version
    core_version: Version | None
    protocol_version: str | None

    @property
    def is_compatible(self) -> bool:
        return self.outcome is CompatibilityOutcome.COMPATIBLE


def resolve_compatibility(
    declaration: CompatibilityDeclaration,
    core_version: Version | None,
    core_protocol_version: str | None,
) -> CompatibilityVerdict:
    """Decide whether the plugin may operate against the installed Core.

    ``core_version`` is ``None`` when the ``theurian`` CLI is not on ``PATH``.
    That is not an error state to repair automatically -- it is the normal
    "plugin installed, Core not yet installed" case (FR-L3).

    **Nothing in production ever passes it.** The only production call site is
    ``cli.main.compat_check``, which passes ``Version.parse_python(__version__)``
    -- and it could not do otherwise, because reaching this function at all means
    the binary was found. A client that cannot find Core resolves ``CORE_MISSING``
    on its own side and never calls in; ``theurian::compat_check`` in the plugin's
    ``lib.sh`` returns 1 before running anything. The branch exists so that the
    outcome, its message and its remedy have one definition rather than one per
    client, and it is reachable only from tests. Correcting the wording here
    therefore changes nothing a user sees: the surfaces that do are enumerated in
    ``CORE_ARRIVAL_SURFACES`` in ``tests/unit/test_setup_claims.py``.

    The remedy names the installer *before* ``/theurian:setup``, in the same
    words the setup report's ``core-present`` step uses. Setup does not install
    Core; it runs from an installed Core. Sending this user straight to
    ``/theurian:setup`` is advice they cannot follow -- it shells out to the
    ``theurian`` binary whose absence is the thing being reported.
    """
    if core_version is None:
        return CompatibilityVerdict(
            outcome=CompatibilityOutcome.CORE_MISSING,
            message="Theurian Core is not installed or is not on PATH.",
            remedy=(
                "Install Theurian with `uv tool install theurian` or "
                "`pipx install theurian`, then run /theurian:setup to configure "
                "this machine."
            ),
            plugin_version=declaration.plugin_version,
            core_version=None,
            protocol_version=None,
        )

    if core_version < declaration.core_minimum:
        return CompatibilityVerdict(
            outcome=CompatibilityOutcome.CORE_TOO_OLD,
            message=(
                f"Theurian Core {core_version} is older than this plugin requires "
                f"(>= {declaration.core_minimum})."
            ),
            remedy=(
                f"Upgrade Core with `theurian upgrade`, or run /theurian:upgrade. "
                f"Required: >= {declaration.core_minimum}, "
                f"< {declaration.core_maximum_exclusive}."
            ),
            plugin_version=declaration.plugin_version,
            core_version=core_version,
            protocol_version=core_protocol_version,
        )

    if core_version >= declaration.core_maximum_exclusive:
        return CompatibilityVerdict(
            outcome=CompatibilityOutcome.CORE_TOO_NEW,
            message=(
                f"Theurian Core {core_version} is newer than plugin "
                f"{declaration.plugin_version} supports "
                f"(< {declaration.core_maximum_exclusive})."
            ),
            remedy=(
                "Update the Theurian plugin with `/plugin update theurian`. Core was not changed."
            ),
            plugin_version=declaration.plugin_version,
            core_version=core_version,
            protocol_version=core_protocol_version,
        )

    if core_protocol_version != declaration.protocol_version:
        return CompatibilityVerdict(
            outcome=CompatibilityOutcome.PROTOCOL_MISMATCH,
            message=(
                f"Protocol mismatch: plugin speaks {declaration.protocol_version}, "
                f"Core speaks {core_protocol_version or '<unknown>'}."
            ),
            remedy=(
                "Update both the plugin and Core to a matching pair. "
                "See docs/protocol/plugin-core-compatibility.md."
            ),
            plugin_version=declaration.plugin_version,
            core_version=core_version,
            protocol_version=core_protocol_version,
        )

    return CompatibilityVerdict(
        outcome=CompatibilityOutcome.COMPATIBLE,
        message=(
            f"Theurian plugin {declaration.plugin_version} is compatible with "
            f"Core {core_version} ({declaration.protocol_version})."
        ),
        remedy="",
        plugin_version=declaration.plugin_version,
        core_version=core_version,
        protocol_version=core_protocol_version,
    )
