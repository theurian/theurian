"""Shaping one knowledge result: what a hit looks like on the wire.

One function, called from every tool, because the trust labels were attached by
the substring path and, for one milestone, not by the ranked one — which is a
knowledge body arriving at an agent with nothing saying it is a document rather
than an instruction (SEC-15). A shape constructed in two places drifts in one of
them.

The companion rule — *whether* a hit may be shown at all — used to live here too
and now lives in :func:`theurian.domain.enums.may_surface`. It is reached from
six call sites, one being the application-layer index builder, which cannot
import this module (ADR-0003) and so kept its own copy of the comparison until
the rule moved to the domain, where every caller can reach it.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from types import MappingProxyType
from typing import Any, Final

from theurian.domain.enums import KnowledgeStatus, Sensitivity
from theurian.domain.knowledge import KnowledgeRevision

# `excerpt` and `EXCERPT_CHARS` now live in the domain, because the RAPTOR forest
# port bounds each `raptorPath` title with the same function and infrastructure
# may not import this wire layer (ADR-0003). Re-exported here so the callers that
# think of it as this module's -- `theurian.mcp.search`, and the tests -- keep
# reaching it at the same name.
from theurian.domain.retrieval import EXCERPT_CHARS, RaptorPathSegment, excerpt

#: Attached to every knowledge-bearing result (SEC-15). Theurian labels; the
#: calling agent enforces. That split is stated in SECURITY.md rather than left
#: for a reader to infer.
#:
#: **Read-only at runtime, not only by convention.** `Final` stops the *name*
#: being rebound and says nothing about the object; a plain dict shared by every
#: serving surface is one `SAFETY["executable"] = True` away from unlabelling
#: every result this daemon returns, from anywhere in the process, with no test
#: naming the line that did it. `MappingProxyType` makes that a `TypeError` at
#: the mutation rather than a disclosure at the wire. Spreading it (`**SAFETY`)
#: is unchanged, which is how all three call sites use it.
SAFETY: Final[Mapping[str, object]] = MappingProxyType(
    {
        "contentClassification": "untrusted-knowledge",
        "mayContainInstructions": True,
        "executable": False,
    }
)

__all__ = ["EXCERPT_CHARS", "SAFETY", "excerpt", "result_payload"]


def result_payload(
    revision: KnowledgeRevision,
    status: KnowledgeStatus,
    sensitivity: Sensitivity,
    now: datetime,
    *,
    raptor_path: tuple[RaptorPathSegment, ...] = (),
) -> dict[str, Any]:
    """Shape one result, always with provenance and the trust triple.

    A result without an anchor is an unverifiable assertion, and one without the
    trust labels invites an agent to read a document as an instruction.

    ``status`` and ``sensitivity`` are parameters, not read off
    ``revision.metadata``, because both are the *item's* authority and a revision
    is immutable: a deprecation moves the item's status and a ``changeSensitivity``
    moves its classification, each without writing a new revision. A payload
    echoing ``revision.metadata.sensitivity`` would report the label the content
    was authored under rather than the one that now decides who may read it
    (SEC-14). The caller threads the item's current values in, the way it already
    did for ``status``.

    ``raptor_path`` is the forest ancestry of a surfaced leaf, catalog root to
    leaf, and is emitted as ``raptorPath`` **only when non-empty** (ADR-0008
    decision 8). An empty tuple -- a chunk-only index, or the unranked canonical
    scan, which has no forest to walk -- omits the key rather than publishing an
    empty array: a field a client learns to ignore would say a forest was
    consulted when none was. Each segment's ``title`` is already the node text
    bounded by :func:`~theurian.domain.retrieval.excerpt`, applied in the index
    adapter so the full summary text never travels; this only serialises it.
    """
    age = (now - revision.created_at).days

    payload: dict[str, Any] = {
        "itemId": revision.item_id.value,
        "revisionId": revision.revision_id.value,
        "title": revision.title,
        "excerpt": excerpt(revision.body),
        "contentType": str(revision.content_type),
        "status": status.value,
        "trustLevel": revision.metadata.trust_level.value,
        "sensitivity": sensitivity.value,
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
    if raptor_path:
        payload["raptorPath"] = [
            {"nodeId": segment.node_id, "level": segment.level, "title": segment.title}
            for segment in raptor_path
        ]
    return payload
