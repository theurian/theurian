"""Traceability edges declared by ``Refs:`` commit trailers (ADR-0040 decisions 1-3).

Recorded in ADR-0040's amendments to decisions 2 and 3 (2026-11-24):

- **Edge id.** The first 128 bits of SHA-256(sha ‖ 0x00 ‖ path ‖ 0x00 ‖ item id)
  over UTF-8, read big-endian and Crockford-base32 encoded to 26 characters, most
  significant first. 26 characters carry 130 bits, so a 128-bit value always
  leads with 0-7 and is a valid ULID with no bit cleared. The separator is
  unambiguous: git forbids 0x00 in a path (tree entries are NUL-terminated), the
  sha is hex and an ``ItemId`` is ``[a-z0-9.-]``.
- **Evidence.** ``provider="git"`` and ``source_uri="<sha>:<path>"``, git's own
  ``<rev>:<path>`` syntax, which reads no remote (ADR-0040:347-350).
- **Undated.** A commit whose committer date ``datetime`` cannot hold
  (``committed_at is None``) yields no edges and is flagged ``undated``:
  containment, the ADR-0029 D3 class, not a cap.

:func:`keyed_lines` splits on ``\\n`` only, so a CRLF message leaves a ``\\r`` on
the value, which the ASCII-whitespace strip removes; a bare ``str.strip()`` would
also remove U+00A0 and U+0085 and collect a value ADR-0040 refuses. No byte of a
refused value reaches a return value or exception text: it costs a count, and
the ``InvalidIdentifierError`` that repeats it is dropped without a raise.
"""

from __future__ import annotations

import hashlib
import string
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import Final

from theurian.domain.enums import RelationType, TraceNodeType
from theurian.domain.errors import InvalidIdentifierError
from theurian.domain.identifiers import EdgeId, ItemId, ProjectId
from theurian.domain.knowledge import SourceAnchor
from theurian.domain.review_finding import keyed_lines
from theurian.domain.specification import TraceabilityEdge, TraceNode

#: The trailing space is part of the key (ADR-0040:424-431).
REFS_KEY: Final = "Refs: "

#: ADR-0040 decision 3(c).
MAX_REFS_PER_COMMIT: Final = 32

# Infrastructure's alphabet is out of the domain's reach (ADR-0003).
_CROCKFORD: Final = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


@dataclass(frozen=True, slots=True)
class CommitRefs:
    """One commit's edges and what it reports by sha: counts and flags, never values."""

    sha: str
    edges: tuple[TraceabilityEdge, ...]
    refused: int
    capped: bool
    undecodable: bool
    undated: bool


def edges_from_commit(
    *,
    project_id: ProjectId,
    sha: str,
    committed_at: datetime | None,
    paths: tuple[str, ...] | None,
    message: bytes,
) -> CommitRefs:
    """One edge per changed path per distinct valid id, or none.

    ``paths`` is ``None`` for a path-capped commit (ADR-0040:363-364).
    """
    try:
        text = message.decode("utf-8")
    except UnicodeDecodeError:
        return CommitRefs(
            sha=sha,
            edges=(),
            refused=0,
            capped=paths is None,
            undecodable=True,
            undated=committed_at is None,
        )
    item_ids, refused = _item_ids(text)
    capped = paths is None or len(item_ids) > MAX_REFS_PER_COMMIT
    edges: tuple[TraceabilityEdge, ...] = ()
    if paths is not None and committed_at is not None and not capped:
        edges = tuple(
            _edge(project_id, sha, committed_at, path, item_id)
            for path in paths
            for item_id in item_ids
        )
    return CommitRefs(
        sha=sha,
        edges=edges,
        refused=refused,
        capped=capped,
        undecodable=False,
        undated=committed_at is None,
    )


def edge_id_for(sha: str, path: str, item_id: ItemId) -> EdgeId:
    digest = hashlib.sha256(f"{sha}\x00{path}\x00{item_id.value}".encode()).digest()
    number = int.from_bytes(digest[:16], "big")
    return EdgeId("".join(_CROCKFORD[(number >> shift) & 31] for shift in range(125, -1, -5)))


def capped_digest(shas: Iterable[str]) -> str | None:
    """ADR-0040 3(c)'s capped digest; duplicates are kept."""
    ordered = sorted(sha.encode() for sha in shas)
    if not ordered:
        return None
    return hashlib.sha256(b"".join(sha + b"\n" for sha in ordered)).hexdigest()


def _item_ids(text: str) -> tuple[tuple[ItemId, ...], int]:
    valid: list[ItemId] = []
    refused = 0
    for _position, line in keyed_lines(text, key=REFS_KEY):
        try:
            valid.append(ItemId(line[len(REFS_KEY) :].strip(string.whitespace)))
        except InvalidIdentifierError:
            refused += 1
    return tuple(dict.fromkeys(valid)), refused


def _edge(
    project_id: ProjectId, sha: str, committed_at: datetime, path: str, item_id: ItemId
) -> TraceabilityEdge:
    return TraceabilityEdge(
        edge_id=edge_id_for(sha, path, item_id),
        project_id=project_id,
        source=TraceNode(TraceNodeType.CODE, path),
        relation_type=RelationType.RELATED_TO,
        target=TraceNode(TraceNodeType.KNOWLEDGE, item_id.value),
        evidence=(
            SourceAnchor(
                provider="git", source_uri=f"{sha}:{path}", commit_sha=sha, file_path=path
            ),
        ),
        confidence=1.0,
        created_at=committed_at,
        source_commit=sha,
    )
