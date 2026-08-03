"""Shaping one knowledge result, and deciding whether it may be shown.

Both rules live here rather than beside the tool that needed them first, because
both have already been broken by being written twice:

- the status gate reached ``knowledge.search`` through three separate code paths
  and ``knowledge.get`` through none, so a fix applied three times still left
  rejected content reachable in one more call (SEC-13);
- the trust labels were attached by the substring path and, for one milestone,
  not by the ranked one — which is a knowledge body arriving at an agent with
  nothing saying it is a document rather than an instruction (SEC-15).

One function each, called from every tool, is what makes those failures
structurally unavailable rather than merely fixed.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Final

from theurian.domain.enums import SURFACEABLE_STATUSES, KnowledgeStatus
from theurian.domain.knowledge import KnowledgeRevision

#: Attached to every knowledge-bearing result (SEC-15). Theurian labels; the
#: calling agent enforces. That split is stated in SECURITY.md rather than left
#: for a reader to infer.
SAFETY: Final[dict[str, object]] = {
    "contentClassification": "untrusted-knowledge",
    "mayContainInstructions": True,
    "executable": False,
}

#: Excerpt length. Long enough to judge relevance, short enough that ten hits do
#: not become the whole answer.
EXCERPT_CHARS: Final = 280


def may_surface(status: KnowledgeStatus, *, include_unapproved: bool) -> bool:
    """Whether a caller may see an item in this state.

    ``include_unapproved`` widens which statuses are allowed. It never disables
    the check: retired knowledge — deprecated, superseded, rejected — is reachable
    through no flag, because a rejected revision is where the secret that caused
    the rejection still lives.
    """
    if status not in SURFACEABLE_STATUSES:
        return False
    return include_unapproved or status is KnowledgeStatus.APPROVED


def excerpt(text: str) -> str:
    """One line of a passage, for a caller deciding whether to fetch the rest."""
    flattened = text.strip().replace("\n", " ")
    return flattened[:EXCERPT_CHARS] + ("..." if len(flattened) > EXCERPT_CHARS else "")


def result_payload(
    revision: KnowledgeRevision, status: KnowledgeStatus, now: datetime
) -> dict[str, Any]:
    """Shape one result, always with provenance and the trust triple.

    A result without an anchor is an unverifiable assertion, and one without the
    trust labels invites an agent to read a document as an instruction.
    """
    age = (now - revision.created_at).days

    return {
        "itemId": revision.item_id.value,
        "revisionId": revision.revision_id.value,
        "title": revision.title,
        "excerpt": excerpt(revision.body),
        "contentType": str(revision.content_type),
        "status": status.value,
        "trustLevel": revision.metadata.trust_level.value,
        "sensitivity": revision.metadata.sensitivity.value,
        "freshness": {
            "revisionCreatedAt": revision.created_at.isoformat(),
            "isWithinValidity": revision.validity.contains(now),
            "ageDays": max(0, age),
        },
        "sourceAnchors": [
            {
                "provider": a.provider,
                "sourceUri": a.source_uri,
                "repository": a.repository,
                "commitSha": a.commit_sha,
                "filePath": a.file_path,
                "lineStart": a.line_start,
                "lineEnd": a.line_end,
            }
            for a in revision.source_anchors
        ],
        **SAFETY,
    }
