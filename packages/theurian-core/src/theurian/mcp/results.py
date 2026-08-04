"""Shaping one knowledge result: what a hit looks like on the wire.

One function, called from every tool, because the trust labels were attached by
the substring path and, for one milestone, not by the ranked one — which is a
knowledge body arriving at an agent with nothing saying it is a document rather
than an instruction (SEC-15). A shape constructed in two places drifts in one of
them.

The companion rule — *whether* a hit may be shown at all — used to live here too
and now lives in :func:`theurian.domain.enums.may_surface`. It was reached from
three layers, including the application-layer index builder, which cannot import
this module (ADR-0003) and so kept its own copy of the comparison.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Final

from theurian.domain.enums import KnowledgeStatus
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
