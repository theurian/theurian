"""What the last published index build's secret scan found (SEC-11, #329).

``theurian propose accept`` scans **before** anything is written, so a finding
there can refuse the acceptance and nothing lands. This module belongs to the
other control: the one that runs at ``theurian index build``, over content that is
**already in the canonical store**.

**Which is why it records rather than refuses, and the disclosure boundary is
where that decision comes from.** A landed secret is readable through
``knowledge.search`` and ``knowledge.get`` the moment ``theurian migrate apply``
writes it, before any index exists at all -- search degrades to an unranked
canonical substring scan and ``get`` reads the store by id. So a build that
refused to publish would deny *ranking* without un-disclosing anything, and on a
project that has never built one it would deny ranking for ever. The scan is
therefore a detection-and-signal control: ``block`` publishes the index and makes
the build exit non-zero, and this record is what carries that signal past the
terminal that saw it, to the next ``theurian doctor``.

**Never an auto-retire.** The detector is best-effort entropy heuristics
(:mod:`theurian.security.content_secrets` says so in its own first paragraph), and
retiring an item on a false positive is silent data loss plus a governance act a
build has no authority to take.

One record per project, overwritten by every publish, naming the build it
describes -- so a reader can tell a verdict about the *published* index from one
left behind by a build the pointer no longer names. It carries a count and a
policy and nothing else: ``theurian doctor --report`` is pasted into public
issues, and which item carries a credential is content the reader of a pasted
report has no business learning. The build's own terminal output names the items.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, Final

from theurian.application.project_service import ProjectPaths, read_active_index_pointer
from theurian.security.project_config import SecretScanPolicy

#: What to do about a secret that has already landed in the canonical store.
#:
#: Rotation comes first for the reason ``proposal_service._secret_refusal`` gives
#: about a proposal directory, only more so: this content is not merely in a
#: working tree, it is in the applied state and is being served. Telling the
#: operator to delete the line and carry on would be advice that leaves the
#: credential live.
#:
#: The escape hatch is named second and names the key, because ``block`` is the
#: default policy and a false positive is otherwise a dead end -- the same
#: reasoning, and deliberately the same three values, as the accept path's remedy.
LANDED_SECRET_REMEDY: Final = (
    "Treat the value as exposed and rotate it: it is in this project's canonical state and "  # noqa: S105 - prose about a secret, not one
    "in Git history, and `knowledge.search` and `knowledge.get` already serve it whatever "
    "this index holds. Then get it out of the corpus -- supersede the revision with a new "
    "`upsertRevision`, or retire the item with `deprecateItem` -- and run `theurian migrate "
    "apply` followed by `theurian index build`. Run `theurian index build` to see which "
    "items were reported. If it is not a secret, set security.secretScan to warn or off in "
    ".theurian/config.yaml (block, warn, off; block is what an absent key selects)."
)


class IndexSecretScanStatus(StrEnum):
    """What can be said about the published index's secret scan.

    Six answers rather than a boolean, because "no finding" is four different
    facts and an operator acts differently on each: the scan ran and found
    nothing, the scan was turned off, no scan has ever been recorded for the build
    that is published, and there is no published build to say anything about. A
    verdict that collapsed them would report a project nobody has scanned as
    clean.
    """

    #: No project here, or no published index build to describe.
    NOT_APPLICABLE = "not-applicable"
    #: An index is published and no scan record names it. Either it predates this
    #: control, or a withdrawal purge republished the pointer at a build this
    #: never saw. Honest ignorance, not a clean bill.
    UNRECORDED = "unrecorded"
    #: ``security.secretScan`` is ``off``, so nothing was read.
    UNSCANNED = "unscanned"
    #: Scanned, nothing found.
    CLEAN = "clean"
    #: Findings, under ``warn``. The operator asked to be told, not stopped.
    WARNED = "warned"
    #: Findings, under ``block``. The health verdict this control exists to raise.
    DEGRADED = "degraded"


@dataclass(frozen=True, slots=True)
class IndexSecretScanVerdict:
    """The published build's scan, in the form ``theurian doctor`` publishes it."""

    status: IndexSecretScanStatus
    #: The policy the recorded scan ran under, or ``None`` when there is no record.
    policy: SecretScanPolicy | None = None
    #: How many findings that scan reported, bounded by the detector's own
    #: ``MAX_FINDINGS``. Zero whenever :attr:`policy` is ``None``.
    findings: int = 0

    def __post_init__(self) -> None:
        if self.policy is None and self.findings:
            # A count with no policy beside it cannot say whether it means
            # "scanned and found" or "not scanned at all", which is the exact
            # confusion `SecretScanResult` carries its policy to prevent.
            msg = "a scan verdict with no policy cannot carry findings"
            raise ValueError(msg)

    @property
    def degraded(self) -> bool:
        """Whether ``theurian doctor`` must count this as a problem."""
        return self.status is IndexSecretScanStatus.DEGRADED

    @property
    def payload(self) -> dict[str, Any]:
        """The published block, whose shape does not vary with what was found.

        ``policy`` and ``findings`` are always present, for the reason
        ``propose accept``'s ``secretFindings`` is: a field that only appears when
        something is wrong is a field a caller learns not to read. ``remedy``
        appears only when there is something to do, which is the shape
        ``AcceptedProposal.cleanup_remedy`` already has.
        """
        published: dict[str, Any] = {
            "status": self.status.value,
            "policy": self.policy.value if self.policy is not None else None,
            "findings": self.findings,
        }
        if self.findings:
            published["remedy"] = LANDED_SECRET_REMEDY
        return published


def write_index_secret_scan(
    paths: ProjectPaths, *, index_build_id: str, policy: SecretScanPolicy, findings: int
) -> None:
    """Record what this build's scan did, atomically.

    Written on **every** publish, including a clean one, and that is the whole of
    what clears a previous ``degraded``. A record written only on trouble would
    leave the last bad verdict standing over a corpus somebody had already fixed.

    Write-to-temp then ``os.replace``, the discipline
    :func:`~theurian.application.project_service.write_active_index_pointer` holds
    for the pointer beside it: a reader must never see half a record and conclude
    the wrong thing about a security control.
    """
    record = paths.index_secret_scan
    record.parent.mkdir(parents=True, exist_ok=True)
    temporary = record.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps(
            {"indexBuildId": index_build_id, "policy": policy.value, "findings": findings},
            indent=2,
        ),
        encoding="utf-8",
    )
    os.replace(temporary, record)  # noqa: PTH105 - os.replace is the atomic primitive


def published_index_secret_scan(paths: ProjectPaths) -> IndexSecretScanVerdict:
    """What the *published* build's scan found, or why nothing can be said.

    Never raises. Every failure here means the same thing -- nothing is known
    about the published build -- and reporting ignorance is the honest answer for
    a record that is derived, git-ignored, and rewritten by the next build.

    **A record for another build is not this build's verdict.** The id is compared
    rather than trusted, because a withdrawal-triggered purge republishes the
    pointer at a build this never wrote a record for; reading the stale one would
    report a verdict about a build that is no longer served.
    """
    published = read_active_index_pointer(paths).payload
    if published is None:
        return IndexSecretScanVerdict(status=IndexSecretScanStatus.NOT_APPLICABLE)

    recorded = _read_record(paths.index_secret_scan)
    if recorded is None or recorded[0] != str(published.get("indexBuildId", "")):
        return IndexSecretScanVerdict(status=IndexSecretScanStatus.UNRECORDED)

    _, policy, findings = recorded
    return IndexSecretScanVerdict(
        status=_status_of(policy, findings), policy=policy, findings=findings
    )


def _status_of(policy: SecretScanPolicy, findings: int) -> IndexSecretScanStatus:
    """The verdict a recorded scan carries.

    ``off`` is reported as unscanned whatever the count says, because under ``off``
    there is no count to report -- and a record claiming otherwise has been edited.
    """
    if policy is SecretScanPolicy.OFF:
        return IndexSecretScanStatus.UNSCANNED
    if not findings:
        return IndexSecretScanStatus.CLEAN
    return (
        IndexSecretScanStatus.DEGRADED
        if policy is SecretScanPolicy.BLOCK
        else IndexSecretScanStatus.WARNED
    )


def _read_record(record: Path) -> tuple[str, SecretScanPolicy, int] | None:
    """The record's three fields, or ``None`` when it cannot be trusted whole.

    Every field is checked rather than coerced. A record whose ``policy`` is not
    one of the three, or whose ``findings`` is not a non-negative integer, is a
    file something else wrote -- and reading a policy out of it would be inventing
    one, the rule ``read_secret_scan_policy`` holds for the configuration file.
    ``bool`` is excluded explicitly: ``isinstance(True, int)`` is ``True``, and a
    ``findings: true`` would otherwise count as one finding.
    """
    loaded = _loaded_mapping(record)
    if loaded is None:
        return None

    build_id = loaded.get("indexBuildId")
    findings = loaded.get("findings")
    policy = _policy_named(loaded.get("policy"))
    if policy is None or not isinstance(build_id, str) or not build_id.strip():
        return None
    if isinstance(findings, bool) or not isinstance(findings, int) or findings < 0:
        return None
    return build_id, policy, findings


def _loaded_mapping(record: Path) -> dict[str, Any] | None:
    """The record parsed as a JSON object, or ``None`` on any way of failing.

    ``UnicodeDecodeError`` is a ``ValueError`` and not a ``JSONDecodeError``, so a
    record holding arbitrary bytes -- a partially overwritten file, a restored
    binary -- escapes a handler that lists only the latter. The same three-way
    catch ``read_active_index_pointer`` carries, for the same file shape.
    """
    if not record.is_file():
        return None
    try:
        loaded = json.loads(record.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, UnicodeDecodeError):
        return None
    return loaded if isinstance(loaded, dict) else None


def _policy_named(stated: object) -> SecretScanPolicy | None:
    """The policy this record states, or ``None`` when it states none of the three.

    The ``isinstance`` is not only for the type checker. ``SecretScanPolicy`` is a
    ``StrEnum``, so a JSON ``true`` reaches its constructor as a value it does not
    hold and raises -- but a *bare* ``off`` in a hand-edited record would be a
    boolean too, and refusing here keeps this file's reading of the policy exactly
    as strict as ``read_secret_scan_policy``'s reading of the configuration.
    """
    if not isinstance(stated, str):
        return None
    try:
        return SecretScanPolicy(stated)
    except ValueError:
        return None


__all__ = [
    "LANDED_SECRET_REMEDY",
    "IndexSecretScanStatus",
    "IndexSecretScanVerdict",
    "published_index_secret_scan",
    "write_index_secret_scan",
]
