"""The deployment serving profile, and the authorization it grants (#119, ADR-0025).

:class:`~theurian.domain.ports.authorization.AuthorizationProvider` has had no
implementation since it was written, and that is what has blocked ``sensitivity``
from becoming a read control: enforcement needs a *principal*, and a loopback
daemon authenticates a bearer token rather than a person.

The entitlement model chosen for OSS Core is a **deployment serving profile**
(maintainer decision, 2026-08-23, recorded on
`#119 <https://github.com/theurian/theurian/issues/119>`_): the operator declares
one sensitivity *ceiling* for the whole deployment, and it lives with the token in
the operator-owned data directory. It is deliberately **not** read from a project's
Git-tracked ``.theurian/config.yaml``. Repository contributors are an untrusted
actor class, so a committed ceiling would make *raising* the ceiling a
contributor-authored access-control change -- reviewable in principle, and
indistinguishable from an ordinary configuration edit in practice.

**Nothing here withholds anything yet.** :data:`DEFAULT_CEILING` is
``restricted``, so the default profile serves every sensitivity and a deployment
with no profile file behaves exactly as it did before this module existed.
Flipping that default to ``internal`` -- the line that actually closes the leak
#119 measured on a real corpus -- is a later phase's change to make and to review,
and it is deliberately one line so that review has something small to look at.
"""

from __future__ import annotations

import stat
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType
from typing import TYPE_CHECKING, Final, final

from theurian.domain.enums import Sensitivity
from theurian.domain.errors import DomainError, SecurityError
from theurian.domain.values import AclGroup, TenantId
from theurian.security.paths import is_world_accessible

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

    from theurian.domain.identifiers import ProjectId

#: Sensitivity levels ordered by how much disclosure each one permits, least
#: first. **This tuple is the only definition of that order in the codebase**, and
#: everything that needs it -- the ceiling expansion below, the list of valid
#: ceilings in an error message -- reads it rather than restating it.
#:
#: It has to be written down because :class:`~theurian.domain.enums.Sensitivity`
#: is a ``StrEnum``, whose members therefore compare *as strings*: measured,
#: ``sorted(Sensitivity)`` is ``[confidential, internal, public, restricted]`` and
#: ``Sensitivity.CONFIDENTIAL < Sensitivity.INTERNAL`` is ``True``. An
#: implementation that reached for ``<`` would not raise; it would silently place
#: ``confidential`` below ``internal`` and serve confidential content under an
#: ``internal`` ceiling.
#:
#: Declaration order in the enum happens to agree today, and is not relied on for
#: the same reason: a member inserted in the middle would move the boundary with
#: nothing to notice it. ``test_authorization_provider`` pins this tuple's members
#: against ``set(Sensitivity)``, so a new level added to the enum turns RED here
#: rather than becoming quietly invisible.
DISCLOSURE_ORDER: Final[tuple[Sensitivity, ...]] = (
    Sensitivity.PUBLIC,
    Sensitivity.INTERNAL,
    Sensitivity.CONFIDENTIAL,
    Sensitivity.RESTRICTED,
)

#: The ceiling a deployment gets when it declares none.
#:
#: ``restricted`` -- every level -- which is exactly today's behaviour and is why
#: this phase changes nothing observable. The shipped default becomes ``internal``
#: in a later phase of #119, once the build-side exclusion and the read-side
#: predicate that make a withholding *safe* are in place; withholding rows while
#: the index still holds their text is the T-17a mechanism, not a fix.
DEFAULT_CEILING: Final = Sensitivity.RESTRICTED

#: The tenant this deployment serves. One process, one operator, one tenant
#: (ADR-0002) -- and the same ``TenantId()`` default that ``migration_engine``
#: refuses a revision for departing from at write time (#110). Both read the
#: domain default rather than a literal, so there is one value and not two.
DEPLOYMENT_TENANT: Final = TenantId()

#: The ACL groups this deployment serves, for the same reason and from the same
#: default as :data:`DEPLOYMENT_TENANT`.
DEPLOYMENT_ACL_GROUPS: Final = frozenset({AclGroup()})

#: The profile file's name, inside the ``auth`` directory beside the token.
SERVING_PROFILE_FILENAME: Final = "serving-profile"

#: Cap on the profile file's size. The longest valid content is ``confidential``
#: and a line terminator, so 64 bytes is roomy; the point is that the read is
#: bounded at all, and that an over-long file is refused before its bytes can
#: reach an error message.
MAX_SERVING_PROFILE_BYTES: Final = 64

_VISIBLE_BY_CEILING: Final[Mapping[Sensitivity, frozenset[Sensitivity]]] = MappingProxyType(
    {
        ceiling: frozenset(DISCLOSURE_ORDER[: index + 1])
        for index, ceiling in enumerate(DISCLOSURE_ORDER)
    }
)


class ServingProfileFault(StrEnum):
    """Why a profile file could not be read as a ceiling, as a closed set.

    A ``StrEnum`` rather than a message parameter, for the reason
    :class:`~theurian.security.env_file.EnvBlockFault` is one: these sentences are
    *published* -- the daemon prints them when it refuses to start -- and an
    exception carries whatever raised it. Constructing the message from a member
    of this enum is what makes "the message contains no byte of the file" a
    property of the type rather than a habit of each call site.
    """

    NOT_A_REGULAR_FILE = "is not a regular file"
    TOO_LARGE = f"is larger than {MAX_SERVING_PROFILE_BYTES} bytes"
    NOT_UTF8 = "is not valid UTF-8"
    EMPTY = "declares no ceiling"


class ServingProfileError(SecurityError):
    """The deployment serving profile exists and could not be honoured.

    Refused rather than defaulted. Falling back to the built-in ceiling would
    turn an operator's typo into a *wider* grant than they asked for, silently,
    and an access control that widens on malformed input is not one.
    """


class MalformedServingProfileError(ServingProfileError):
    """The profile file is not one readable ceiling word."""

    def __init__(self, path: Path, fault: ServingProfileFault) -> None:
        self.path = path
        self.fault = fault
        self.remedy = (
            f"Write one of {_valid_ceilings()} into {path}, or delete the file to "
            f"fall back to this build's default ceiling."
        )
        super().__init__(f"The deployment serving profile at {path} {fault.value}.")


class UnknownSensitivityCeilingError(ServingProfileError):
    """The profile file holds a word that is not a sensitivity level."""

    def __init__(self, path: Path, word: str) -> None:
        self.path = path
        self.word = word
        self.remedy = (
            f"Write one of {_valid_ceilings()} into {path}, or delete the file to "
            f"fall back to this build's default ceiling."
        )
        # The one place a byte of the file enters a message, because an operator
        # cannot fix a typo they cannot see. Bounded by construction: the read
        # refuses anything past MAX_SERVING_PROFILE_BYTES before this is reached,
        # and `!r` escapes whatever those bytes turned out to be.
        super().__init__(
            f"The deployment serving profile at {path} declares a ceiling of {word!r}, "
            f"which is not a sensitivity level."
        )


class InsecureServingProfilePermissionsError(ServingProfileError):
    """The profile file is readable or writable by other local users.

    The same refusal ``FileSecretStore.get`` makes about the token beside it, and
    for a stronger reason: a token another account can read is a leaked
    credential, while a *ceiling* another account can write is that account
    choosing what this daemon serves.
    """

    def __init__(self, path: Path, mode: int) -> None:
        self.path = path
        self.mode = mode
        self.remedy = f"Run `chmod 600 {path}`, then re-check who has been able to edit it."
        super().__init__(
            f"The deployment serving profile at {path} has mode {mode:04o} and is "
            f"accessible to other users."
        )


def _valid_ceilings() -> str:
    return ", ".join(level.value for level in DISCLOSURE_ORDER)


@dataclass(frozen=True, slots=True)
class ServingProfile:
    """One deployment's declared sensitivity ceiling, expanded once.

    :attr:`visible_sensitivities` is computed at construction rather than on
    demand, so the ceiling-to-set expansion happens in one place at one moment and
    every reader afterwards is looking at the same frozen answer.
    """

    ceiling: Sensitivity = DEFAULT_CEILING

    #: Every level at or below :attr:`ceiling`. Derived, never passed in.
    visible_sensitivities: frozenset[Sensitivity] = field(init=False)

    def __post_init__(self) -> None:
        # Normalised rather than trusted: `Sensitivity` is a `StrEnum`, so a bare
        # "internal" satisfies the annotation at runtime and would leave
        # `profile.ceiling` a `str` that only looks like a member. The lookup
        # below would still succeed -- `StrEnum` hashes as `str` -- and the
        # mismatch would surface somewhere else entirely.
        ceiling = Sensitivity(self.ceiling)
        object.__setattr__(self, "ceiling", ceiling)
        object.__setattr__(self, "visible_sensitivities", _VISIBLE_BY_CEILING[ceiling])


@dataclass(frozen=True, slots=True)
class AuthorizationGrant:
    """What the daemon's one principal may see, resolved once at startup.

    The composition root resolves this before the server exists and threads it in.
    That is deliberate: the port's methods are ``async`` and the MCP tool
    functions are not, so a per-call ``await`` would need an event loop the tool
    layer does not own. Resolving once is also the honest shape for a *deployment*
    profile -- there is one answer for the whole process, and a value re-derived
    per request is a second place for it to be wrong.
    """

    tenant: TenantId
    sensitivities: frozenset[Sensitivity]
    acl_groups: frozenset[AclGroup]

    def __post_init__(self) -> None:
        # A grant that permits nothing is not a policy, it is a deployment that
        # answers every query with an empty result and no explanation. Refused at
        # construction so it cannot reach a request.
        if not self.sensitivities:
            msg = "An AuthorizationGrant must permit at least one sensitivity level."
            raise DomainError(msg)
        if not self.acl_groups:
            msg = "An AuthorizationGrant must permit at least one ACL group."
            raise DomainError(msg)


@final
class StaticAuthorizationProvider:
    """The OSS Core's :class:`~theurian.domain.ports.authorization.AuthorizationProvider`.

    One process, one operator, one profile (ADR-0002). Every answer is precomputed
    at construction and every method hands back a slice of the *same*
    :class:`AuthorizationGrant` that :meth:`deployment_grant` returns -- so the
    port's answers and the composition root's grant cannot disagree, because there
    is one object and not two derivations of one idea.

    ``tenant_for`` and ``visible_acl_groups`` return the values ``migration_engine``
    already refuses a revision for departing from (#110), which is what makes
    those two axes degenerate here rather than unenforced: nothing can be stored
    outside them, so nothing can be withheld along them.
    """

    def __init__(self, profile: ServingProfile | None = None) -> None:
        self._profile = profile if profile is not None else ServingProfile()
        self._grant = AuthorizationGrant(
            tenant=DEPLOYMENT_TENANT,
            sensitivities=self._profile.visible_sensitivities,
            acl_groups=DEPLOYMENT_ACL_GROUPS,
        )

    @property
    def profile(self) -> ServingProfile:
        return self._profile

    def deployment_grant(self) -> AuthorizationGrant:
        """The whole deployment's grant, without an event loop.

        The port is ``async`` because a hosted provider will make a network call;
        this one has nothing to await, and the composition root that needs the
        answer (``daemon/runner.build_server``) is called from inside a running
        loop by every integration test, where ``asyncio.run`` raises. So the
        precomputed value is offered synchronously here, and the ``async``
        methods below return pieces of it.
        """
        return self._grant

    async def may_access_project(
        self,
        principal: str,  # noqa: ARG002 -- port shape; one bearer token, one principal
        project_id: ProjectId,  # noqa: ARG002 -- every registered project, per the port
    ) -> bool:
        return True

    async def visible_sensitivities(
        self,
        principal: str,  # noqa: ARG002 -- port shape; the profile is deployment-wide
        project_id: ProjectId,  # noqa: ARG002 -- port shape; the profile is deployment-wide
    ) -> frozenset[Sensitivity]:
        return self._grant.sensitivities

    async def visible_acl_groups(
        self,
        principal: str,  # noqa: ARG002 -- port shape; the profile is deployment-wide
        project_id: ProjectId,  # noqa: ARG002 -- port shape; the profile is deployment-wide
    ) -> frozenset[AclGroup]:
        return self._grant.acl_groups

    async def tenant_for(
        self,
        principal: str,  # noqa: ARG002 -- port shape; one process serves one tenant
    ) -> TenantId:
        return self._grant.tenant


def serving_profile_path(data_dir: Path) -> Path:
    """Where the operator declares this deployment's ceiling.

    Beside the token, inside ``auth/``, and not at the data directory's root.
    ``Path.mkdir(parents=True, mode=...)`` applies its mode to the leaf only, so a
    data directory that was created as somebody's parent keeps the umask's mode
    while ``auth/`` is 0700 by construction (``FileSecretStore.set``). A ceiling
    another local account can rewrite is not a ceiling.

    ``"auth"`` is spelled here as a literal, the way every other site already
    spells it. Counted rather than estimated --
    ``git grep -n '/ "auth"' -- packages/theurian-core/src`` on ``e58a8aa`` --
    there are six including this one, across ``application`` (twice), ``cli``,
    ``infrastructure`` and ``security`` (twice). Naming the directory once would
    be an improvement and is a change to four packages, none of which this phase
    touches.
    """
    return data_dir / "auth" / SERVING_PROFILE_FILENAME


def load_serving_profile(data_dir: Path) -> ServingProfile:
    """Read the declared ceiling, or the built-in default when none is declared.

    **An absent file is not an error and not a warning**: it is the ordinary state
    of a deployment that has not declared a ceiling, and it is what makes this
    phase behaviour-neutral on every existing installation.

    A file that is *present* is honoured or refused, never partially believed.

    Raises:
        MalformedServingProfileError: The file is not one readable word --
            irregular, oversized, not UTF-8, or empty.
        UnknownSensitivityCeilingError: The word is not a sensitivity level.
        InsecureServingProfilePermissionsError: Other local users can reach it.
    """
    path = serving_profile_path(data_dir)
    if not path.exists():
        return ServingProfile()

    info = path.stat()
    # Checked from the directory entry, before anything opens the path: a FIFO
    # reports size 0, passes every bound below it, and then blocks in `open()`
    # until a writer appears -- which for a file read during startup is a daemon
    # that never finishes starting (the shape issue #215 measured for source
    # files).
    if not stat.S_ISREG(info.st_mode):
        raise MalformedServingProfileError(path, ServingProfileFault.NOT_A_REGULAR_FILE)
    if is_world_accessible(path):
        raise InsecureServingProfilePermissionsError(path, info.st_mode & 0o777)
    if info.st_size > MAX_SERVING_PROFILE_BYTES:
        raise MalformedServingProfileError(path, ServingProfileFault.TOO_LARGE)

    with path.open("rb") as handle:
        raw = handle.read(MAX_SERVING_PROFILE_BYTES + 1)
    # Bounded at the read and re-checked after it, rather than trusted from the
    # `stat` above: the file can grow between the two, and `read_bytes` would
    # then pull in whatever it grew to before any of this could refuse it.
    if len(raw) > MAX_SERVING_PROFILE_BYTES:
        raise MalformedServingProfileError(path, ServingProfileFault.TOO_LARGE)

    try:
        word = raw.decode("utf-8").strip()
    except UnicodeDecodeError as exc:
        raise MalformedServingProfileError(path, ServingProfileFault.NOT_UTF8) from exc

    if not word:
        raise MalformedServingProfileError(path, ServingProfileFault.EMPTY)

    ceiling = _ceiling_from(word)
    if ceiling is None:
        raise UnknownSensitivityCeilingError(path, word)
    return ServingProfile(ceiling=ceiling)


def _ceiling_from(word: str) -> Sensitivity | None:
    """The level this word names, or ``None``.

    Case-insensitive: ``INTERNAL`` names the same level as ``internal`` and
    accepting it widens nothing. Everything else is refused rather than guessed --
    a near-miss silently resolved is how a ceiling ends up somewhere its operator
    did not put it.
    """
    folded = word.casefold()
    return next((level for level in DISCLOSURE_ORDER if level.value == folded), None)
