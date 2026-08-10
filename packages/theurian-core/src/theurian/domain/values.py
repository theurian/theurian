"""Value objects shared across entities."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Final, Self, override

from theurian.domain.enums import KnowledgeStatus, Sensitivity
from theurian.domain.errors import DomainError, InvariantViolationError
from theurian.domain.identifiers import ProjectId

_SHA256_PATTERN: Final = re.compile(r"^[0-9a-f]{64}$")
_MEDIA_TYPE_PATTERN: Final = re.compile(r"^[a-z0-9][a-z0-9!#$&^_.+-]*/[a-z0-9][a-z0-9!#$&^_.+-]*$")

#: Upper bound for opaque scope component names. Generous for a human-chosen
#: label, tight enough that a component cannot become an exfiltration channel.
MAX_SCOPE_COMPONENT_LENGTH: Final = 128

#: Upper bound for a dotted namespace, matching the identifier limit.
MAX_NAMESPACE_LENGTH: Final = 200

#: C0 controls (0x00-0x1f) plus DEL (0x7f). ``Scope.key`` (below) joins its six
#: components with ``\x1f``; a component that can itself carry that byte lets
#: two DISTINCT scopes render the same key -- a scope with
#: ``acl_group="a\x1fb"``, ``namespace="c"`` collides with one with
#: ``acl_group="a"``, ``namespace="b\x1fc"`` (the collision reviewers
#: demonstrated). Rejecting the whole control range, not just ``\x1f``, keeps
#: the rule statable as one sentence rather than an allowlist that the next
#: delimiter change would have to remember to extend.
_CONTROL_CHAR_PATTERN: Final = re.compile(r"[\x00-\x1f\x7f]")


def _reject_control_characters(value: str, *, label: str) -> None:
    """Raise if ``value`` carries a C0 control character or DEL.

    Shared by every ``Scope`` component that is free-form text: ``AclGroup``,
    ``TenantId`` and ``Scope.namespace``. ``project_id`` needs no separate
    check here -- ``ProjectId`` already restricts to a kebab-case slug -- and
    ``sensitivity``/``status`` are enums, so neither can carry an arbitrary
    byte.
    """
    if _CONTROL_CHAR_PATTERN.search(value):
        raise DomainError(f"{label} must not contain control characters, got {value!r}")


@dataclass(frozen=True, slots=True)
class ContentHash:
    """A lowercase hex SHA-256 digest of content bytes.

    Content addressing is what makes revisions verifiable (INV-3), state hashes
    reproducible (ADR-0007), and derived-artifact staleness exact rather than
    heuristic.
    """

    value: str

    def __post_init__(self) -> None:
        if not _SHA256_PATTERN.match(self.value):
            raise DomainError(
                f"ContentHash must be 64 lowercase hex characters, got {self.value!r}"
            )

    @classmethod
    def of_bytes(cls, data: bytes) -> Self:
        return cls(hashlib.sha256(data).hexdigest())

    @classmethod
    def of_text(cls, text: str) -> Self:
        """Hash text as UTF-8.

        Bytes are hashed exactly as given: no newline or Unicode
        normalisation. Normalising here would make the same file hash
        differently depending on who checked it out.
        """
        return cls.of_bytes(text.encode("utf-8"))

    @property
    def short(self) -> str:
        """First 12 characters, for display and state-directory names."""
        return self.value[:12]

    @override
    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True, slots=True)
class MediaType:
    """An IANA-style media type, e.g. ``text/markdown``.

    Carried through normalisation so a structured source is never silently
    reinterpreted as prose (ADR-0010).
    """

    value: str

    def __post_init__(self) -> None:
        if not _MEDIA_TYPE_PATTERN.match(self.value):
            raise DomainError(f"MediaType must look like 'type/subtype', got {self.value!r}")

    @property
    def is_structured(self) -> bool:
        """Whether the payload has machine-queryable structure beyond prose."""
        return self.value in _STRUCTURED_MEDIA_TYPES or self.value.endswith(("+json", "+yaml"))

    @override
    def __str__(self) -> str:
        return self.value


_STRUCTURED_MEDIA_TYPES: Final = frozenset(
    {
        "application/json",
        "application/schema+json",
        "application/vnd.oai.openapi",
        "application/vnd.oai.openapi+json",
        "application/vnd.aai.asyncapi",
        "application/yaml",
        "text/x-yaml",
    }
)

MARKDOWN: Final = MediaType("text/markdown")
PLAIN_TEXT: Final = MediaType("text/plain")
JSON: Final = MediaType("application/json")
YAML: Final = MediaType("application/yaml")


@dataclass(frozen=True, slots=True)
class ValidityPeriod:
    """The window during which knowledge is considered current.

    Knowledge expires. A runbook for a decommissioned service is not merely
    unhelpful; retrieved without a validity window it is actively misleading.
    """

    valid_from: datetime
    valid_to: datetime | None = None

    def __post_init__(self) -> None:
        if self.valid_from.tzinfo is None:
            raise InvariantViolationError("valid_from must be timezone-aware")
        if self.valid_to is not None:
            if self.valid_to.tzinfo is None:
                raise InvariantViolationError("valid_to must be timezone-aware")
            if self.valid_to <= self.valid_from:
                raise InvariantViolationError(
                    f"valid_to ({self.valid_to.isoformat()}) must be after "
                    f"valid_from ({self.valid_from.isoformat()})"
                )

    def contains(self, moment: datetime) -> bool:
        if moment.tzinfo is None:
            raise DomainError("moment must be timezone-aware")
        if moment < self.valid_from:
            return False
        return self.valid_to is None or moment < self.valid_to

    @property
    def is_open_ended(self) -> bool:
        return self.valid_to is None


@dataclass(frozen=True, slots=True)
class AclGroup:
    """An opaque access-control group name.

    Locally this is almost always the default; it exists now because it is a
    RAPTOR scope component, and retrofitting a scope component after trees are
    built means rebuilding every tree.
    """

    value: str = "default"

    def __post_init__(self) -> None:
        if not self.value or len(self.value) > MAX_SCOPE_COMPONENT_LENGTH:
            raise DomainError(
                f"AclGroup must be 1..{MAX_SCOPE_COMPONENT_LENGTH} characters, "
                f"got {len(self.value)}"
            )
        _reject_control_characters(self.value, label="AclGroup")


@dataclass(frozen=True, slots=True)
class TenantId:
    """Tenant boundary. Always ``local`` in the OSS single-user deployment."""

    value: str = "local"

    def __post_init__(self) -> None:
        if not self.value or len(self.value) > MAX_SCOPE_COMPONENT_LENGTH:
            raise DomainError(
                f"TenantId must be 1..{MAX_SCOPE_COMPONENT_LENGTH} characters, "
                f"got {len(self.value)}"
            )
        _reject_control_characters(self.value, label="TenantId")


@dataclass(frozen=True, slots=True)
class Scope:
    """The isolation boundary that partitions the RAPTOR forest (ADR-0008).

    A summary node may only be built from children that share this tuple exactly.
    Because tree identity is derived from it, a node mixing two scopes has no
    tree to belong to -- the isolation is structural rather than a check someone
    could forget to write.

    ``status`` is the sixth component, added by the Milestone 6 amendment to
    decision 1: an ``index build --include-unapproved`` run can otherwise mix a
    ``draft`` and an ``approved`` child into one summary node, since ``_scope``
    filters on status but the five-component tuple never named it.
    """

    project_id: ProjectId
    tenant_id: TenantId
    sensitivity: Sensitivity
    acl_group: AclGroup
    namespace: str
    status: KnowledgeStatus

    def __post_init__(self) -> None:
        if len(self.namespace) > MAX_NAMESPACE_LENGTH:
            raise DomainError(
                f"namespace must be at most {MAX_NAMESPACE_LENGTH} characters, "
                f"got {len(self.namespace)}"
            )
        _reject_control_characters(self.namespace, label="namespace")

    @property
    def key(self) -> str:
        """A stable, collision-free textual key.

        Components are separated by ``\\x1f`` (unit separator). The separator
        cannot occur in any component because the components' own validation
        rejects control characters, so ``a|b`` and ``a`` + ``|b`` cannot
        collide.
        """
        return "\x1f".join(
            (
                self.project_id.value,
                self.tenant_id.value,
                self.sensitivity.value,
                self.acl_group.value,
                self.namespace,
                self.status.value,
            )
        )

    @property
    def digest(self) -> ContentHash:
        """Content hash of :attr:`key`, used as a stable tree discriminator."""
        return ContentHash.of_text(self.key)
