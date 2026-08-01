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
    r"(?:[._-]?dev[._-]?(?P<dev_number>\d+)?)?$",
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

_PROTOCOL_PATTERN: Final = re.compile(r"^theurian/v[1-9]\d*$")


@total_ordering
@dataclass(frozen=True, slots=True)
class Version:
    """A semantic version.

    Ordering follows SemVer §11, including the rule that a pre-release sorts
    *before* its own release. Getting that backwards would let a plugin accept
    ``0.5.0-rc1`` under a ``maximumExclusive`` of ``0.5.0``.
    """

    major: int
    minor: int
    patch: int
    prerelease: tuple[str, ...] = ()
    build: str | None = None

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

        The mapping preserves ordering in both ecosystems: a PEP 440 pre-release
        or development release becomes a SemVer pre-release, so it sorts *before*
        the corresponding final release under both rule sets.
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
        if match.group("dev_number") is not None:
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
        return _compare_prerelease(self.prerelease, other.prerelease) < 0

    @override
    def __str__(self) -> str:
        text = f"{self.major}.{self.minor}.{self.patch}"
        if self.prerelease:
            text += "-" + ".".join(self.prerelease)
        if self.build:
            text += f"+{self.build}"
        return text


def _compare_prerelease(left: tuple[str, ...], right: tuple[str, ...]) -> int:
    """Compare pre-release identifier lists per SemVer §11.4."""
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
    "plugin installed, setup not yet run" case, and the remedy is to tell the
    user to run ``/theurian:setup`` (FR-L3).
    """
    if core_version is None:
        return CompatibilityVerdict(
            outcome=CompatibilityOutcome.CORE_MISSING,
            message="Theurian Core is not installed or is not on PATH.",
            remedy="Run /theurian:setup once to install and configure Theurian.",
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
