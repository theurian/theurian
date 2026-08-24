"""The deployment serving profile and its provider (#119, ADR-0025).

Two properties carry this module, and they pull in opposite directions.

**It must withhold nothing yet.** Every deployment that has no profile file --
which is every deployment that exists -- has to see exactly what it saw before
this code was written. The tests that hold that are the ones about the default.

**It must be wrong loudly rather than widely.** A ceiling is an access control, so
every way the file can be malformed has to end in a refusal that names the four
valid words, never in a silent fallback to the built-in ceiling: falling back
serves *more* than the operator asked for, which is the failure this whole issue
exists to close.
"""

from __future__ import annotations

import inspect
import os
import stat
import sys
from pathlib import Path

import pytest

from theurian.application.authorization import (
    DEFAULT_CEILING,
    DEPLOYMENT_ACL_GROUPS,
    DEPLOYMENT_TENANT,
    DISCLOSURE_ORDER,
    MAX_SERVING_PROFILE_BYTES,
    AuthorizationGrant,
    InsecureServingProfilePermissionsError,
    MalformedServingProfileError,
    ServingProfile,
    ServingProfileFault,
    StaticAuthorizationProvider,
    UnknownSensitivityCeilingError,
    decode_sensitivities,
    encode_sensitivities,
    load_serving_profile,
    serving_profile_path,
)
from theurian.domain.enums import Sensitivity, may_disclose
from theurian.domain.errors import DomainError
from theurian.domain.identifiers import ProjectId
from theurian.domain.ports.authorization import AuthorizationProvider
from theurian.domain.values import AclGroup, TenantId

pytestmark = pytest.mark.unit

PROJECT = ProjectId("demo")


def _write_profile(data_dir: Path, contents: bytes, *, mode: int = 0o600) -> Path:
    path = serving_profile_path(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.write_bytes(contents)
    os.chmod(path, mode)
    return path


# -- The port ---------------------------------------------------------------


def test_the_static_provider_satisfies_the_port() -> None:
    """The port has had no implementation since it was written (#119).

    Both halves are asserted because they fail differently: the annotation is
    what mypy checks, and ``isinstance`` is what a composition root can check at
    wiring time on a provider that arrived from a plugin.
    """
    provider: AuthorizationProvider = StaticAuthorizationProvider()

    assert isinstance(provider, AuthorizationProvider)


@pytest.mark.asyncio
async def test_the_ports_answers_are_slices_of_the_grant_it_hands_out() -> None:
    """One object, not two derivations of one idea.

    A provider that recomputed its async answers would be a second place for the
    deployment's policy to live, and the composition root reads the *other* one.
    The two agreeing today would say nothing about them agreeing after an edit --
    so the async methods are asserted to return the very objects the grant holds,
    identity and not equality.
    """
    provider = StaticAuthorizationProvider(ServingProfile(ceiling=Sensitivity.INTERNAL))
    grant = provider.deployment_grant()

    assert await provider.visible_sensitivities("token", PROJECT) is grant.sensitivities
    assert await provider.visible_acl_groups("token", PROJECT) is grant.acl_groups
    assert await provider.tenant_for("token") is grant.tenant
    assert await provider.may_access_project("token", PROJECT) is True


@pytest.mark.asyncio
async def test_tenant_and_acl_group_are_the_values_write_time_already_refuses() -> None:
    """The degenerate discharge of two of FR-R1's axes (#119 decision 4).

    ``migration_engine`` refuses at write time any revision naming a tenant other
    than ``local`` or an ACL group other than ``default`` (#110), so no stored row
    can carry anything else and there is nothing along those axes to withhold.
    That argument only holds while the provider grants *exactly* what the writer
    refuses to depart from, which is what this reads out of both modules rather
    than restating as a literal.
    """
    from theurian.application.migration_engine import _ENFORCED_ACL_GROUP, _ENFORCED_TENANT_ID

    provider = StaticAuthorizationProvider()

    assert (await provider.tenant_for("token")).value == _ENFORCED_TENANT_ID
    assert {group.value for group in await provider.visible_acl_groups("token", PROJECT)} == {
        _ENFORCED_ACL_GROUP
    }
    assert TenantId() == DEPLOYMENT_TENANT
    assert frozenset({AclGroup()}) == DEPLOYMENT_ACL_GROUPS


# -- The ceiling and its expansion ------------------------------------------


@pytest.mark.parametrize(
    ("ceiling", "expected"),
    [
        (Sensitivity.PUBLIC, {Sensitivity.PUBLIC}),
        (Sensitivity.INTERNAL, {Sensitivity.PUBLIC, Sensitivity.INTERNAL}),
        (
            Sensitivity.CONFIDENTIAL,
            {Sensitivity.PUBLIC, Sensitivity.INTERNAL, Sensitivity.CONFIDENTIAL},
        ),
        (
            Sensitivity.RESTRICTED,
            {
                Sensitivity.PUBLIC,
                Sensitivity.INTERNAL,
                Sensitivity.CONFIDENTIAL,
                Sensitivity.RESTRICTED,
            },
        ),
    ],
)
def test_a_ceiling_expands_to_every_level_at_or_below_it(
    ceiling: Sensitivity, expected: set[Sensitivity]
) -> None:
    """All four, written out, because the boundary is the whole product here.

    Parametrised over the levels rather than derived from ``DISCLOSURE_ORDER``:
    a test that computed the expected set from the same tuple the implementation
    reads would stay green through any reordering of it, which is precisely the
    mistake that would serve confidential content under an ``internal`` ceiling.
    """
    profile = ServingProfile(ceiling=ceiling)

    assert profile.visible_sensitivities == frozenset(expected)
    assert StaticAuthorizationProvider(profile).deployment_grant().sensitivities == frozenset(
        expected
    )


def test_the_disclosure_order_covers_every_sensitivity_level() -> None:
    """A level absent from the order is a level no ceiling can ever admit.

    ``DISCLOSURE_ORDER`` is the only place the order lives, so a member added to
    the enum and forgotten here would be invisible to every ceiling -- withheld
    from everyone, including from the operator who declared ``restricted``. This
    is the check that turns that omission RED.
    """
    assert set(DISCLOSURE_ORDER) == set(Sensitivity)
    assert len(DISCLOSURE_ORDER) == len(set(DISCLOSURE_ORDER)), "a level listed twice"


def test_string_comparison_is_not_the_disclosure_order() -> None:
    """Why the order is written down at all.

    ``Sensitivity`` is a ``StrEnum``, so ``<`` between members is a string
    comparison that answers without complaint -- and answers wrongly. An
    implementation reaching for it would place ``confidential`` below
    ``internal``. Measured here so the reason the tuple exists cannot be
    optimised away by someone who checks that ``<`` "works".
    """
    assert Sensitivity.CONFIDENTIAL < Sensitivity.INTERNAL, "alphabetical, not disclosure order"
    assert sorted(Sensitivity) != list(DISCLOSURE_ORDER)


@pytest.mark.parametrize("ceiling", list(Sensitivity))
@pytest.mark.parametrize("level", list(Sensitivity))
def test_the_gate_admits_exactly_what_the_ceiling_expanded_to(
    ceiling: Sensitivity, level: Sensitivity
) -> None:
    """All sixteen pairs, because the gate and the expansion must not drift apart.

    :func:`~theurian.domain.enums.may_disclose` is the predicate every read path
    consults; ``ServingProfile.visible_sensitivities`` is the set the operator's
    declared ceiling expanded to. Two derivations of one idea is how a gate ends
    up admitting a level its own profile excluded, so this asserts they are the
    same answer for every ``(ceiling, level)`` pair rather than for the handful a
    fixture happens to use.

    The expected side is read off the profile deliberately.
    ``test_a_ceiling_expands_to_every_level_at_or_below_it`` above is what pins
    *that* set against a written-out expectation; this one has a different job --
    it pins the gate to whatever that set turns out to be.
    """
    visible = ServingProfile(ceiling=ceiling).visible_sensitivities

    assert may_disclose(level, visible=visible) is (level in visible)


def test_the_gate_is_membership_and_not_a_string_comparison() -> None:
    """The one pair that separates a correct gate from the plausible wrong one.

    ``Sensitivity`` is a ``StrEnum``, so ``confidential <= internal`` is ``True``
    (see ``test_string_comparison_is_not_the_disclosure_order``). A gate written
    as a comparison against the ceiling would therefore serve a ``confidential``
    item to an ``internal`` deployment, silently and without raising. This asserts
    the outcome that discriminates the two implementations, so the reason
    :func:`~theurian.domain.enums.may_disclose` takes an expanded *set* rather
    than a ceiling cannot be refactored away by someone who checks that ``<=``
    "works".
    """
    internal_deployment = ServingProfile(ceiling=Sensitivity.INTERNAL).visible_sensitivities

    assert Sensitivity.CONFIDENTIAL <= Sensitivity.INTERNAL, "the trap this test exists for"
    assert not may_disclose(Sensitivity.CONFIDENTIAL, visible=internal_deployment)
    assert may_disclose(Sensitivity.INTERNAL, visible=internal_deployment)


def test_a_ceiling_given_as_a_bare_string_is_normalised() -> None:
    """``StrEnum`` makes ``"internal"`` satisfy the annotation at runtime.

    Left alone, ``profile.ceiling`` would be a ``str`` that only looks like a
    member, and the mismatch would surface somewhere with no context.
    """
    profile = ServingProfile(ceiling="internal")  # type: ignore[arg-type]

    assert profile.ceiling is Sensitivity.INTERNAL


# -- The default: restrictive, and every construction site agrees ------------


def test_the_shipped_default_withholds_confidential_and_restricted(tmp_path: Path) -> None:
    """The default is restrictive (#119, maintainer decision 2026-08-23, ADR-0025).

    Four statements of one property, because each is a different way for the
    default to drift wider than the decision: the constant, the profile it
    produces, the profile an *absent* file produces, and the grant the composition
    root builds from that. A deployment that declares nothing serves ``public``
    and ``internal`` and withholds the other two.

    **This test asserted the opposite until the flip**, under the name
    ``test_the_shipped_default_serves_every_level``, and its docstring said the
    flip should turn it RED so that the behaviour change could not be made
    quietly. It did: ``assert DEFAULT_CEILING is Sensitivity.RESTRICTED`` failed
    with ``<Sensitivity.INTERNAL: 'internal'>``. Rewritten rather than deleted,
    because the pin is the same pin -- what moved is which side of it is true.

    The withheld set is written as a literal rather than derived from
    ``DEFAULT_CEILING``, deliberately: a derivation would agree with the constant
    however the constant moved, which is the one thing this test exists to
    notice.
    """
    served = frozenset({Sensitivity.PUBLIC, Sensitivity.INTERNAL})
    withheld = frozenset({Sensitivity.CONFIDENTIAL, Sensitivity.RESTRICTED})
    assert served | withheld == frozenset(Sensitivity), (
        "the two sets must partition Sensitivity, or a level added to the enum "
        "would be neither asserted served nor asserted withheld here"
    )

    assert DEFAULT_CEILING is Sensitivity.INTERNAL
    assert ServingProfile().visible_sensitivities == served
    assert load_serving_profile(tmp_path) == ServingProfile()
    assert (
        StaticAuthorizationProvider(load_serving_profile(tmp_path)).deployment_grant().sensitivities
        == served
    )


def test_an_absent_profile_is_not_an_error(tmp_path: Path) -> None:
    """Absent is the ordinary state, not a misconfiguration.

    Neither the data directory nor the ``auth`` directory need exist: a daemon
    starting for the first time reads the profile before anything has created
    either.
    """
    assert not serving_profile_path(tmp_path).exists()

    assert load_serving_profile(tmp_path).ceiling is DEFAULT_CEILING


# -- Where the profile lives ------------------------------------------------


def test_the_profile_lives_beside_the_token_under_the_data_directory() -> None:
    """The operator-owned location, and the 0700 directory (#119 decision 1)."""
    data_dir = Path("/somewhere/.theurian")

    path = serving_profile_path(data_dir)

    assert path.is_relative_to(data_dir)
    assert path.parent.name == "auth", "beside the token, in the directory created 0700"


def test_the_profile_is_never_read_from_a_project(tmp_path: Path) -> None:
    """Not from ``.theurian/config.yaml``, and by construction rather than by rule.

    Recorded on #119 on 2026-08-23: repository contributors are an untrusted actor
    class, so a ceiling committed to a project would make *raising* it a
    contributor-authored access-control change -- reviewable in principle, and
    indistinguishable from an ordinary configuration edit in practice.

    Held two ways. A project declaring a ceiling has no effect, and the loader
    accepts no project to read one from: the second is what keeps the first true
    after someone adds a parameter.
    """
    data_dir = tmp_path / "datadir"
    project = tmp_path / "repo"
    (project / ".theurian").mkdir(parents=True)
    (project / ".theurian" / "config.yaml").write_text("sensitivityCeiling: restricted\n")
    _write_profile(data_dir, b"public\n")

    assert load_serving_profile(data_dir).ceiling is Sensitivity.PUBLIC

    for function in (serving_profile_path, load_serving_profile):
        parameters = list(inspect.signature(function).parameters)
        assert parameters == ["data_dir"], f"{function.__name__} can reach a project"


# -- Refusals ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("contents", "ceiling"),
    [
        (b"public", Sensitivity.PUBLIC),
        (b"internal\n", Sensitivity.INTERNAL),
        (b"  confidential  \n", Sensitivity.CONFIDENTIAL),
        (b"RESTRICTED\n", Sensitivity.RESTRICTED),
        (b"Internal\r\n", Sensitivity.INTERNAL),
    ],
)
def test_a_declared_ceiling_is_honoured(
    tmp_path: Path, contents: bytes, ceiling: Sensitivity
) -> None:
    """Whitespace and case are tolerated; nothing else is.

    Case-folding widens nothing -- ``INTERNAL`` names one level and it is the same
    one -- while a near-miss resolved by guessing is how a ceiling ends up
    somewhere its operator did not put it.
    """
    _write_profile(tmp_path, contents)

    assert load_serving_profile(tmp_path).ceiling is ceiling


def test_an_unknown_word_names_the_four_valid_ceilings(tmp_path: Path) -> None:
    """The error is the operator's only view of what the file accepts.

    It carries the typo back -- bounded, because the read refuses anything past
    ``MAX_SERVING_PROFILE_BYTES`` first -- and every valid word, because a
    refusal that does not say what would have worked leaves the reader guessing
    at exactly the moment guessing is expensive.
    """
    path = _write_profile(tmp_path, b"secret\n")

    with pytest.raises(UnknownSensitivityCeilingError) as raised:
        load_serving_profile(tmp_path)

    message = f"{raised.value} {raised.value.remedy}"
    assert "'secret'" in message
    assert str(path) in message
    for level in Sensitivity:
        assert level.value in message


@pytest.mark.parametrize(
    ("contents", "fault"),
    [
        (b"", ServingProfileFault.EMPTY),
        (b"   \n\n", ServingProfileFault.EMPTY),
        (b"internal\xff\n", ServingProfileFault.NOT_UTF8),
        (b"x" * (MAX_SERVING_PROFILE_BYTES + 1), ServingProfileFault.TOO_LARGE),
    ],
)
def test_a_malformed_profile_is_refused_rather_than_defaulted(
    tmp_path: Path, contents: bytes, fault: ServingProfileFault
) -> None:
    """Never a silent fallback.

    Defaulting on malformed input would serve *more* than the operator asked for,
    which is the direction an access control must not fail in. The remedy names
    the file, so the reader is not left to find it.
    """
    path = _write_profile(tmp_path, contents)

    with pytest.raises(MalformedServingProfileError) as raised:
        load_serving_profile(tmp_path)

    assert raised.value.fault is fault
    assert str(path) in str(raised.value)
    assert str(path) in raised.value.remedy


def test_an_oversized_profile_does_not_reach_a_message(tmp_path: Path) -> None:
    """The bound is what makes echoing the word safe.

    ``UnknownSensitivityCeilingError`` puts the file's contents in a published
    message. That is only defensible while nothing large can get that far, so the
    size refusal has to come *before* the word is parsed rather than after.
    """
    # A sentinel rather than a filler byte: `tmp_path` holds this test's own name,
    # so "no letter from the file appears in the message" is a claim about the
    # path as much as about the file, and the first spelling of it failed on the
    # `z` in "oversized".
    spillage = b"SENTINEL-" * 8
    assert len(spillage) > MAX_SERVING_PROFILE_BYTES
    _write_profile(tmp_path, spillage)

    with pytest.raises(MalformedServingProfileError) as raised:
        load_serving_profile(tmp_path)

    assert "SENTINEL" not in f"{raised.value} {raised.value.remedy}"


def test_a_group_readable_profile_is_refused_like_the_token_beside_it(tmp_path: Path) -> None:
    """The same check ``FileSecretStore.get`` makes, for a stronger reason.

    A token another account can read is a leaked credential; a *ceiling* another
    account can write is that account choosing what this daemon serves. The mode
    is read rather than exercised, so this holds when the suite runs as root --
    where ``chmod`` denies nothing -- as well as when it does not.
    """
    path = _write_profile(tmp_path, b"internal\n", mode=0o644)

    with pytest.raises(InsecureServingProfilePermissionsError) as raised:
        load_serving_profile(tmp_path)

    assert raised.value.mode == 0o644
    assert "chmod 600" in raised.value.remedy
    assert path.stat().st_mode & 0o777 == 0o644, "refused, never repaired in place"


def test_a_profile_written_at_0600_is_accepted(tmp_path: Path) -> None:
    """The counterpart, so the refusal above is not vacuously green.

    Without it, a loader that refused every file whatever its mode would pass the
    permission test and fail nothing.
    """
    path = _write_profile(tmp_path, b"internal\n")

    assert path.stat().st_mode & 0o777 == 0o600
    assert load_serving_profile(tmp_path).ceiling is Sensitivity.INTERNAL


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="no mkfifo on this platform")
@pytest.mark.skipif(sys.platform == "win32", reason="POSIX file types")
def test_a_fifo_in_the_profiles_place_is_refused_before_anything_opens_it(tmp_path: Path) -> None:
    """A daemon that never finishes starting is the failure this prevents.

    A FIFO reports size 0, clears every bound, and then blocks in ``open()`` until
    a writer appears -- the shape issue #215 measured for source files, arriving
    here on a path that is read during startup. The type is checked from the
    directory entry, which cannot block.

    The test asserts the refusal rather than a timeout for the same reason: a
    hanging test hangs the suite.
    """
    path = serving_profile_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.mkfifo(path, 0o600)
    assert stat.S_ISFIFO(path.stat().st_mode)

    with pytest.raises(MalformedServingProfileError) as raised:
        load_serving_profile(tmp_path)

    assert raised.value.fault is ServingProfileFault.NOT_A_REGULAR_FILE


# -- Grant invariants -------------------------------------------------------


@pytest.mark.parametrize(
    ("sensitivities", "acl_groups"),
    [
        (frozenset(), frozenset({AclGroup()})),
        (frozenset({Sensitivity.PUBLIC}), frozenset()),
    ],
)
def test_a_grant_that_permits_nothing_cannot_be_constructed(
    sensitivities: frozenset[Sensitivity], acl_groups: frozenset[AclGroup]
) -> None:
    """An empty axis is not a policy.

    It is a deployment that answers every query with an empty result and no
    explanation -- indistinguishable, from the caller's side, from a corpus that
    holds nothing. Refused at construction so it cannot reach a request.

    Driven by synthetic input because nothing today produces it: every ceiling
    expands to at least ``public``, so without this the guard would survive its
    own deletion.
    """
    with pytest.raises(DomainError):
        AuthorizationGrant(
            tenant=DEPLOYMENT_TENANT, sensitivities=sensitivities, acl_groups=acl_groups
        )


# -- What the index pointer records, and what it refuses to read -------------
#
# `encode_sensitivities` / `decode_sensitivities` are how a build's disclosure
# flavor survives into `active-index.json` and back out at serve time (#119
# phase 3). The pointer is derived, git-ignored and unsigned, so the pair is
# read by a path that must treat its input as hostile and its own output as
# authoritative -- the two halves of one wire field.


@pytest.mark.parametrize("ceiling", list(Sensitivity))
def test_a_recorded_flavor_round_trips_through_the_pointer(ceiling: Sensitivity) -> None:
    """Every ceiling, because the pair's whole job is that the reader gets the
    writer's own answer rather than a second expansion of the same ceiling."""
    levels = ServingProfile(ceiling=ceiling).visible_sensitivities

    assert decode_sensitivities(encode_sensitivities(levels)) == levels


def test_the_recorded_order_is_the_disclosure_order_and_not_the_string_order() -> None:
    """A pointer file a person opens should read least-disclosing first.

    ``sorted()`` would not: ``Sensitivity`` is a ``StrEnum``, so it sorts
    alphabetically -- ``confidential`` ahead of ``internal`` -- which is the very
    ordering that is not the disclosure order (see
    ``test_string_comparison_is_not_the_disclosure_order``). Asserted against the
    written-out list rather than against ``DISCLOSURE_ORDER``, so that reordering
    that tuple has to be a deliberate edit here too.
    """
    every_level = frozenset(Sensitivity)

    assert encode_sensitivities(every_level) == [
        "public",
        "internal",
        "confidential",
        "restricted",
    ]
    assert encode_sensitivities(frozenset({Sensitivity.INTERNAL, Sensitivity.PUBLIC})) == [
        "public",
        "internal",
    ]


@pytest.mark.parametrize(
    "raw",
    [
        pytest.param(None, id="absent"),
        pytest.param([], id="empty-list"),
        pytest.param("internal", id="a-bare-string"),
        pytest.param({"ceiling": "internal"}, id="an-object"),
        pytest.param(["internal", "nonsense"], id="one-word-that-is-not-a-level"),
        pytest.param(["INTERNAL"], id="the-wrong-case"),
        pytest.param(["internal", None], id="a-null-among-the-levels"),
        pytest.param([["internal"]], id="a-nested-list"),
        pytest.param([1, 2], id="numbers"),
    ],
)
def test_an_unreadable_flavor_is_unknown_rather_than_a_default(raw: object) -> None:
    """``None``, never a guess and never a raise.

    The serve path turns ``None`` into a fallback and a rebuild. Any default here
    would be an assumption about which rows a file holds, made in the one place
    that cannot check -- and "assume it holds what we serve" is exactly the
    assumption that puts above-ceiling text into the collection statistics the
    visible rows are scored against.

    ``INTERNAL`` in the wrong case is refused even though
    ``load_serving_profile`` accepts ``INTERNAL`` from an operator's own file:
    that is a human writing a word, this is a machine reading back a value only
    Theurian writes, and a reader that repairs its input cannot tell a typo from
    a rewrite.
    """
    assert decode_sensitivities(raw) is None


def test_a_flavor_recorded_twice_over_is_still_the_set_it_names() -> None:
    """Duplicates are not corruption: a set is what the value means.

    No build writes this -- ``encode_sensitivities`` emits each level at most
    once -- so it is here to say that the decision was made rather than left to
    whichever branch happened to run. Refusing it would turn a harmless
    restatement into a fallback and a rebuild.
    """
    assert decode_sensitivities(["public", "public", "internal"]) == frozenset(
        {Sensitivity.PUBLIC, Sensitivity.INTERNAL}
    )
