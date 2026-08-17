"""Proposal values (ADR-0013).

The two rules that hold wherever a proposal is written from: what it must carry
before it may exist at all, and the name its migration file takes.
"""

from __future__ import annotations

import pytest

from theurian.domain.errors import InvariantViolationError
from theurian.domain.identifiers import AgentId, ItemId, MigrationId, RevisionId, TaskId
from theurian.domain.knowledge import SourceAnchor
from theurian.domain.proposal import (
    MAX_SLUG_LENGTH,
    Evidence,
    body_extension,
    body_relative_path,
    kebab_slug,
    migration_file_name,
)
from theurian.domain.values import JSON, MARKDOWN, YAML, MediaType

pytestmark = pytest.mark.unit

ANCHOR = SourceAnchor(
    provider="git",
    source_uri="https://github.com/acme/api/commit/0123456789abcdef",
    commit_sha="0123456789abcdef",
)

REVISION = RevisionId("01K9C7VN4TQZB2M8XR5HD3JFEW")


def _evidence(
    *,
    model: str = "claude-opus-5",
    reasoning: str = "The review thread on #41 settled the retry budget.",
    anchors: tuple[SourceAnchor, ...] = (ANCHOR,),
) -> Evidence:
    return Evidence(
        agent_id=AgentId("claude-code"),
        task_id=TaskId("t-1"),
        model=model,
        reasoning=reasoning,
        anchors=anchors,
    )


# -- evidence --------------------------------------------------------------


def test_evidence_records_the_origin_a_reviewer_reads() -> None:
    evidence = _evidence()

    assert evidence.agent_id.value == "claude-code"
    assert evidence.anchors == (ANCHOR,)


def test_evidence_without_an_anchor_is_valid_when_it_carries_reasoning() -> None:
    """ADR-0013 point 5 requires the reasoning, not an anchor.

    Knowledge that originates in Theurian has no external source to name -- it
    carries the ``authored-in-theurian`` label (INV-8, enforced on the request)
    and the reasoning that produced it. Requiring an anchor here made that case
    impossible, which is what left ``--authored-here`` unreachable from the CLI.
    """
    evidence = _evidence(anchors=())

    assert evidence.anchors == ()
    assert evidence.reasoning


def test_a_proposal_with_no_reasoning_is_rejected_at_construction() -> None:
    """An anchor without the reasoning that joins it to the claim is not evidence.

    The whole point of ``evidence.json`` is that a human reads *why* the anchor
    supports the change; a list of URLs answers a different question.
    """
    with pytest.raises(InvariantViolationError, match="no evidence"):
        _evidence(reasoning="   ")


def test_a_proposal_with_no_model_identity_is_rejected_at_construction() -> None:
    with pytest.raises(InvariantViolationError, match="model"):
        _evidence(model="")


# -- naming ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("Retry policy", "retry-policy"),
        ("  Retry   policy  ", "retry-policy"),
        ("Retry policy (v2)", "retry-policy-v2"),
        ("HTTP/2 upgrade", "http-2-upgrade"),
        ("--leading and trailing--", "leading-and-trailing"),
    ],
)
def test_a_slug_is_derived_from_the_title(title: str, expected: str) -> None:
    assert kebab_slug(title, fallback="fallback") == expected


def test_a_title_with_no_ascii_letters_falls_back() -> None:
    """A Japanese title slugifies to nothing, and a nameless file is not a name.

    The fallback is the item id's last segment, which ``ItemId`` has already
    validated as kebab-case -- so the fallback cannot itself need one.
    """
    assert kebab_slug("署名付きトークンを持つ", fallback="auth-policy") == "auth-policy"


def test_a_slug_is_bounded_and_never_ends_in_a_separator() -> None:
    slug = kebab_slug("word " * 40, fallback="fallback")

    assert len(slug) <= MAX_SLUG_LENGTH
    assert not slug.endswith("-")


def test_a_title_and_a_fallback_that_both_slugify_to_nothing_is_refused() -> None:
    with pytest.raises(InvariantViolationError, match="slug"):
        kebab_slug("署名", fallback="トークン")


def test_the_migration_file_carries_its_own_id() -> None:
    """The measured lesson of #89: a fixed name overwrites the previous acceptance.

    Two proposals accepted in turn both landed as ``migration.yaml``; after the
    second move ``migrate validate`` reported one migration and ``migrate apply``
    applied only it, with the first change gone from the set.
    """
    first = migration_file_name(MigrationId("01K9C7VN4TQZB2M8XR5HD3JFEW"), "retry-policy")
    second = migration_file_name(MigrationId("01K9D2G8YT6PXN0VKS4WBZ7RQM"), "retry-policy")

    assert first == "01K9C7VN4TQZB2M8XR5HD3JFEW-retry-policy.yaml"
    assert first != second


# -- body placement --------------------------------------------------------


@pytest.mark.parametrize(
    ("content_type", "extension"),
    [(MARKDOWN, ".md"), (JSON, ".json"), (YAML, ".yaml")],
)
def test_a_body_extension_follows_the_declared_content_type(
    content_type: MediaType, extension: str
) -> None:
    """The extension is what ``theurian ingest`` reads a file's format from.

    A body written as ``.md`` while the revision declares JSON would be indexed
    as prose, so the two are derived from one value rather than chosen twice.
    """
    assert body_extension(content_type) == extension


def test_an_unsupported_content_type_is_refused_rather_than_guessed() -> None:
    with pytest.raises(InvariantViolationError, match="content type"):
        body_extension(MediaType("application/x-tar"))


def test_a_body_path_comes_from_the_item_id_and_never_from_free_text() -> None:
    """``namespace`` is free text and would be a traversal primitive as a path.

    ``ItemId`` is dotted lowercase kebab-case, so every segment of the path below
    is already bounded by its pattern -- ``../`` cannot be spelled in one.
    """
    path = body_relative_path(ItemId("architecture.retry-policy"), REVISION, MARKDOWN)

    assert path.as_posix() == f"architecture/retry-policy.{REVISION.value}.md"


def test_a_single_segment_item_lands_at_the_knowledge_root() -> None:
    path = body_relative_path(ItemId("glossary"), REVISION, MARKDOWN)

    assert path.as_posix() == f"glossary.{REVISION.value}.md"


def test_two_revisions_of_one_item_never_share_a_body_file() -> None:
    """The measured reason the revision id is in the name.

    A body a migration references is immutable -- the loader re-reads it and
    compares it against the pinned digest on every load. Two generated
    proposals for one item, accepted in turn, shared ``retry-policy.md`` and
    took ``theurian migrate validate`` to exit 4 for the whole project:
    *"hashes to abc7cdb70713 but the migration pins 4f9c5503e198"*. No
    migration could be applied afterwards.
    """
    item = ItemId("architecture.retry-policy")
    first = body_relative_path(item, REVISION, MARKDOWN)
    second = body_relative_path(item, RevisionId("01K9D2G8YT6PXN0VKS4WBZ7RQM"), MARKDOWN)

    assert first != second
    assert first.parent == second.parent, "one item's revisions stay in one directory"
