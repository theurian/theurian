"""Identifier value objects.

Every identifier in Theurian is a distinct type. Passing a ``ProjectId`` where an
``ItemId`` is expected is a type error, not a runtime mystery -- these values are
all strings at rest and are trivially confusable otherwise.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final, Self, override

from theurian.domain.errors import InvalidIdentifierError

# Crockford base32, excluding I, L, O, and U.
_ULID_PATTERN: Final = re.compile(r"^[0-7][0-9A-HJKMNP-TV-Z]{25}$")

# Dotted, lowercase, kebab-friendly segments: ``architecture.auth-policy``.
_DOTTED_PATTERN: Final = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*(?:\.[a-z0-9]+(?:-[a-z0-9]+)*)*$")

# A single kebab-case segment: ``backend-service``.
_SLUG_PATTERN: Final = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")

_MAX_IDENTIFIER_LENGTH: Final = 200


@dataclass(frozen=True, slots=True)
class _StringId:
    """Base for validated string identifiers."""

    value: str

    def __post_init__(self) -> None:
        self._validate(self.value)

    @classmethod
    def _validate(cls, value: str) -> None:
        """Reject a malformed value. Overridden by every concrete identifier."""
        raise NotImplementedError

    @override
    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True, slots=True)
class Ulid(_StringId):
    """A canonical 26-character Crockford-base32 ULID.

    Theurian relies on the property that lexical order equals creation order, so
    the textual form is the stored form and is validated on construction.
    """

    @override
    @classmethod
    def _validate(cls, value: str) -> None:
        if not _ULID_PATTERN.match(value):
            raise InvalidIdentifierError(
                f"{cls.__name__} must be a 26-character Crockford-base32 ULID, got {value!r}"
            )

    @classmethod
    def parse(cls, value: str) -> Self:
        """Parse a ULID, normalising case.

        Crockford base32 is case-insensitive; Theurian stores it uppercase so that
        byte-wise sorting matches ULID ordering.
        """
        return cls(value.strip().upper())


@dataclass(frozen=True, slots=True)
class MigrationId(Ulid):
    """Identifies a knowledge migration. Ordering defines application order."""


@dataclass(frozen=True, slots=True)
class RevisionId(Ulid):
    """Identifies an immutable knowledge revision."""


@dataclass(frozen=True, slots=True)
class IndexBuildId(Ulid):
    """Identifies one index build attempt, successful or not."""


@dataclass(frozen=True, slots=True)
class NodeId(Ulid):
    """Identifies a RAPTOR node."""


@dataclass(frozen=True, slots=True)
class TreeId(Ulid):
    """Identifies a RAPTOR tree within a forest."""


@dataclass(frozen=True, slots=True)
class EdgeId(Ulid):
    """Identifies a traceability edge."""


@dataclass(frozen=True, slots=True)
class ProposalId(Ulid):
    """Identifies an agent-generated change proposal."""


@dataclass(frozen=True, slots=True)
class _DottedId(_StringId):
    """Base for dotted-namespace identifiers such as ``architecture.auth-policy``."""

    @override
    @classmethod
    def _validate(cls, value: str) -> None:
        if not value:
            raise InvalidIdentifierError(f"{cls.__name__} must not be empty")
        if len(value) > _MAX_IDENTIFIER_LENGTH:
            raise InvalidIdentifierError(
                f"{cls.__name__} must be at most {_MAX_IDENTIFIER_LENGTH} characters, "
                f"got {len(value)}"
            )
        if not _DOTTED_PATTERN.match(value):
            raise InvalidIdentifierError(
                f"{cls.__name__} must be lowercase dot-separated kebab-case segments, got {value!r}"
            )


@dataclass(frozen=True, slots=True)
class ItemId(_DottedId):
    """Human-authored, stable identifier for a knowledge item.

    Unlike a revision id, this is chosen by an author and appears in migrations,
    citations, and relations. It must therefore be readable and stable.
    """

    @property
    def namespace(self) -> str:
        """The leading segments, or ``""`` when the id has a single segment."""
        head, _, _ = self.value.rpartition(".")
        return head


@dataclass(frozen=True, slots=True)
class SpecId(_DottedId):
    """Stable identifier for a specification, e.g. ``spec.order-cancellation``."""


@dataclass(frozen=True, slots=True)
class ProjectId(_StringId):
    """Identifies a registered project within one Theurian installation."""

    @override
    @classmethod
    def _validate(cls, value: str) -> None:
        if not value:
            raise InvalidIdentifierError("ProjectId must not be empty")
        if len(value) > _MAX_IDENTIFIER_LENGTH:
            raise InvalidIdentifierError(
                f"ProjectId must be at most {_MAX_IDENTIFIER_LENGTH} characters, got {len(value)}"
            )
        if not _SLUG_PATTERN.match(value):
            raise InvalidIdentifierError(f"ProjectId must be lowercase kebab-case, got {value!r}")


@dataclass(frozen=True, slots=True)
class AgentId(_StringId):
    """Opaque, caller-supplied identifier for an AI agent.

    Theurian does not authenticate agents; this is provenance, not identity, and
    is recorded on proposals so a change can be traced to the run that made it.
    """

    @override
    @classmethod
    def _validate(cls, value: str) -> None:
        if not value or len(value) > _MAX_IDENTIFIER_LENGTH:
            raise InvalidIdentifierError(
                f"AgentId must be 1..{_MAX_IDENTIFIER_LENGTH} characters, got {len(value)}"
            )


@dataclass(frozen=True, slots=True)
class TaskId(_StringId):
    """Opaque, caller-supplied identifier for a unit of agent work."""

    @override
    @classmethod
    def _validate(cls, value: str) -> None:
        if not value or len(value) > _MAX_IDENTIFIER_LENGTH:
            raise InvalidIdentifierError(
                f"TaskId must be 1..{_MAX_IDENTIFIER_LENGTH} characters, got {len(value)}"
            )
