"""Pure retrieval metrics over one ``(response, judgement)`` pair.

Field names match ``schemas/mcp/knowledge-search-response.schema.json`` and
its ``retrieval`` and hit sub-schemas. Every per-query function returns
``None`` when the judgement does not carry the axis it measures, so a caller
aggregates a metric over only the queries that speak to it, rather than
diluting it with entries it cannot judge.
"""

from __future__ import annotations

from typing import Any

from corpus import EvidenceRef, JudgementEntry


def _result_item_ids(response: dict[str, Any]) -> list[str]:
    return [hit["itemId"] for hit in response["results"]]


def recall_at_k(response: dict[str, Any], judgement: JudgementEntry, k: int) -> float | None:
    """Share of ``judgement.relevant`` present in the first ``k`` results.

    ``None`` when the judgement names no relevant item: Recall is undefined
    over an empty denominator, not zero.
    """
    relevant = {item.item_id for item in judgement.relevant}
    if not relevant:
        return None
    retrieved = set(_result_item_ids(response)[:k])
    return len(retrieved & relevant) / len(relevant)


def mrr(response: dict[str, Any], judgement: JudgementEntry) -> float | None:
    """Reciprocal rank of the first relevant hit, or ``0.0`` when none is present.

    ``None`` when the judgement names no relevant item.
    """
    relevant = {item.item_id for item in judgement.relevant}
    if not relevant:
        return None
    for rank, item_id in enumerate(_result_item_ids(response), start=1):
        if item_id in relevant:
            return 1.0 / rank
    return 0.0


def _matches(anchor: dict[str, Any], plain: set[str], narrowed: set[tuple[str, str]]) -> bool:
    source_uri = anchor["sourceUri"]
    file_path = anchor.get("filePath")
    return source_uri in plain or (file_path is not None and (source_uri, file_path) in narrowed)


def evidence_precision(response: dict[str, Any], judgement: JudgementEntry) -> float | None:
    """Share of returned source anchors that match an expected evidence key.

    A judged entry with no ``filePath`` matches any anchor sharing its
    ``sourceUri``; one with a ``filePath`` matches only that exact pair (the
    loader's evidence-subsumption rule keeps these two forms from ever judging
    the same anchor twice). ``None`` when the judgement carries no evidence,
    or when the response returned no anchors to score -- precision is
    undefined over an empty denominator, not zero, the same convention
    :func:`recall_at_k` records for its own empty-relevant case.
    """
    if not judgement.evidence:
        return None
    plain = {ref.source_uri for ref in judgement.evidence if ref.file_path is None}
    narrowed = _narrowed_keys(judgement.evidence)
    anchors = [anchor for hit in response["results"] for anchor in hit["sourceAnchors"]]
    if not anchors:
        return None
    matched = sum(1 for anchor in anchors if _matches(anchor, plain, narrowed))
    return matched / len(anchors)


def _narrowed_keys(evidence: tuple[EvidenceRef, ...]) -> set[tuple[str, str]]:
    return {(ref.source_uri, ref.file_path) for ref in evidence if ref.file_path is not None}


def forbidden_present(response: dict[str, Any], judgement: JudgementEntry) -> bool | None:
    """``None`` when the judgement names nothing forbidden. The
    superseded-knowledge error rate is the share of non-``None`` values across
    a query population, computed by the caller.
    """
    if not judgement.forbidden:
        return None
    forbidden = {item.item_id for item in judgement.forbidden}
    return bool(forbidden & set(_result_item_ids(response)))


def abstention_correct(response: dict[str, Any], judgement: JudgementEntry) -> bool | None:
    """Whether an abstention-expecting query correctly returned nothing.

    ``None`` for a query that does not expect abstention.
    """
    if not judgement.expect_abstention:
        return None
    return bool(response["count"] == 0 and response["results"] == [])


def differing_paths(left: Any, right: Any, prefix: str = "") -> frozenset[str]:
    """Dotted-path set of every leaf where two JSON-like structures disagree.

    Compares the *whole* structure rather than an enumerated field list
    (ADR-0036 decision 6): a subset or field-list check would pass a response
    that had silently stopped publishing a field the fixed exception set does
    not name. A structural mismatch (a missing key, a differing array length)
    is reported at the shallowest path where it is detected.
    """
    root_label = prefix or "<root>"
    if isinstance(left, dict) and isinstance(right, dict):
        diffs: set[str] = set()
        for key in sorted(set(left) | set(right)):
            child = f"{prefix}.{key}" if prefix else key
            if key not in left or key not in right:
                diffs.add(child)
                continue
            diffs |= differing_paths(left[key], right[key], child)
        return frozenset(diffs)
    if isinstance(left, list) and isinstance(right, list):
        if len(left) != len(right):
            return frozenset({root_label})
        diffs = set()
        for index, (left_item, right_item) in enumerate(zip(left, right, strict=True)):
            diffs |= differing_paths(left_item, right_item, f"{prefix}[{index}]")
        return frozenset(diffs)
    return frozenset() if left == right else frozenset({root_label})
