"""Agent-generated change proposals (ADR-0013).

AI proposes, Git reviews, humans approve. What lives here is the part of that
rule that holds wherever a proposal is written from -- the CLI today, an MCP
write-intent tool in Milestone 7 -- and that therefore cannot sit in either
composition root: what a proposal must carry before it may exist at all, and the
name its migration file takes.

The naming rule is not cosmetic. ``.theurian/migrations/`` names its files
``<ulid>-<kebab-slug>.yaml`` (``docs/protocol/migrations.md``), and a proposal
that writes anything else forces ``propose accept`` to rename on the way in.
A *fixed* name is worse than that: two proposals both called ``migration.yaml``
land on one path, and the second acceptance overwrites the first with nothing
reported. Measured on #89 -- after the second move, validation reported one
migration and applying it applied only that one, with the first change gone from
the set and its body file left behind with nothing pointing at it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Final

from theurian.domain.errors import InvariantViolationError
from theurian.domain.identifiers import AgentId, ItemId, MigrationId, RevisionId, TaskId
from theurian.domain.knowledge import SourceAnchor
from theurian.domain.values import JSON, MARKDOWN, YAML, MediaType

#: Longest slug a generated migration file carries. The ULID in front of it is
#: the identity; the slug is for a human scanning a directory listing, and a
#: sentence-length one stops being scannable at about this width.
MAX_SLUG_LENGTH: Final = 48

#: Runs of anything a filename should not carry, collapsed to one separator.
_NOT_SLUG: Final = re.compile(r"[^a-z0-9]+")

#: Body extension by declared media type. Derived from one value rather than
#: chosen twice: ``theurian ingest`` reads a file's format from its extension
#: (``detect_media_type``), so a body written as ``.md`` while its revision
#: declares JSON would be re-read as prose.
_EXTENSIONS: Final = {MARKDOWN.value: ".md", JSON.value: ".json", YAML.value: ".yaml"}


@dataclass(frozen=True, slots=True)
class Evidence:
    """Where a proposed change came from, and why its author believes it.

    Written to ``evidence.json``, which is **read by the humans reviewing the
    pull request and never by Core**. It is not a substitute for
    ``metadata.sourceAnchors``, which is what ``theurian migrate apply``
    enforces (INV-8): a revision carrying rich evidence and no metadata anchor
    validates and then fails to apply. The two are separate fields with separate
    readers, and this one is the one a person acts on.

    ADR-0013 point 5: *a proposal with no evidence is rejected at generation*.
    Enforced here, at construction, so that no code path can assemble a proposal
    directory and discover the problem while writing the third file.
    """

    agent_id: AgentId
    task_id: TaskId
    model: str
    reasoning: str
    anchors: tuple[SourceAnchor, ...]

    def __post_init__(self) -> None:
        require_evidence(self)


def require_evidence(evidence: Evidence) -> None:
    """Raise unless ``evidence`` evidences anything (ADR-0013 point 5).

    Stated once and called from two places -- here, and again where a proposal
    request is assembled. The second call is not redundant: it is what makes
    "rejected at generation" a property of the generation *path* rather than of
    one constructor, so that a caller holding an :class:`Evidence` built by any
    other route still cannot package a proposal out of it.
    """
    if not evidence.model.strip():
        raise InvariantViolationError(
            "Evidence must name the model that produced the proposal. "
            "Pass the model identity, e.g. --model claude-opus-5."
        )
    if not evidence.anchors or not evidence.reasoning.strip():
        raise InvariantViolationError(
            "A proposal with no evidence is rejected at generation (ADR-0013). "
            "Give at least one source anchor and the reasoning that joins it to "
            "the claim: --source-uri and --reasoning."
        )


def kebab_slug(text: str, *, fallback: str) -> str:
    """A bounded, filename-safe slug for ``text``, or for ``fallback``.

    ``fallback`` exists because slugification is lossy in a way that is total for
    some inputs: a title written entirely in Japanese has no ASCII alphanumerics
    at all and reduces to the empty string. A caller passes the item id's last
    segment, which ``ItemId`` has already validated as kebab-case.

    Raises:
        InvariantViolationError: If neither survives slugification. A file named
            only by its ULID would still be unique, but the failure is worth
            reporting: it means the caller passed two values that carry no
            ASCII, and silently dropping the human-readable half of a filename
            is how a directory listing stops being scannable.
    """
    slug = _slugify(text) or _slugify(fallback)
    if not slug:
        raise InvariantViolationError(
            f"Neither {text!r} nor {fallback!r} yields a slug. A migration file is "
            "named <ulid>-<kebab-slug>.yaml, so one of them must contain ASCII "
            "letters or digits."
        )
    return slug


def _slugify(text: str) -> str:
    return _NOT_SLUG.sub("-", text.lower())[:MAX_SLUG_LENGTH].strip("-")


def migration_file_name(migration_id: MigrationId, slug: str) -> str:
    """The name a generated migration keeps from generation through acceptance.

    Written at generation rather than at acceptance so that ``propose accept``
    is a move that renames nothing, and so that the id -- which is what makes
    two proposals two files -- is present from the first moment the file exists.
    """
    return f"{migration_id.value}-{slug}.yaml"


#: The exact shape :func:`migration_file_name` produces: a ULID, a hyphen, a
#: kebab slug, ``.yaml``. Anchored so a body file that also ends ``.yaml`` --
#: ``glossary.<revision-ulid>.yaml`` for a YAML-bodied namespace-less item --
#: does not match, since it opens with a lowercase word rather than a ULID.
_MIGRATION_FILE_NAME: Final = re.compile(
    r"\A[0-7][0-9A-HJKMNP-TV-Z]{25}-[a-z0-9]+(?:-[a-z0-9]+)*\.yaml\Z"
)


def is_migration_file_name(name: str) -> bool:
    """Whether ``name`` is a generated migration file name (``<ulid>-<slug>.yaml``).

    ``accept`` uses this to pick the migration out of a proposal directory
    rather than globbing ``*.yaml``: a YAML or YML *body* is a ``*.yaml`` too,
    and globbing counted it as a second migration, so a YAML-bodied proposal
    could never be accepted (it "held two or more migration files").
    """
    return _MIGRATION_FILE_NAME.match(name) is not None


def body_extension(content_type: MediaType) -> str:
    """The filename extension a body of this media type is written with.

    Raises:
        InvariantViolationError: For a media type with no mapping. Guessing
            ``.txt`` would produce a body the ingestion walk skips entirely,
            which is a silent loss rather than a refusal.
    """
    extension = _EXTENSIONS.get(content_type.value)
    if extension is None:
        raise InvariantViolationError(
            f"No body extension for content type {content_type.value!r}. A proposal "
            f"body is Markdown, JSON, or YAML: {', '.join(sorted(_EXTENSIONS))}."
        )
    return extension


def body_relative_path(
    item_id: ItemId, revision_id: RevisionId, content_type: MediaType
) -> PurePosixPath:
    """Where one revision's body file lives under the knowledge directory.

    Derived from the **item id**, never from ``namespace``. The two would
    usually agree, but ``namespace`` is free text bounded only by a control-
    character exclusion, so using it as a path component would make ``../`` a
    spellable value in a field nothing treats as a path. ``ItemId`` is dotted
    lowercase kebab-case, which cannot express a traversal at all.

    **The revision id is in the name, and that is not decoration.** A body file
    a migration references is immutable: the loader re-reads it on every load
    and compares it against the digest the migration pinned, and applying the
    whole set to an empty database has to reproduce the same canonical state
    (FR-K4). One path per *item* satisfies neither. Measured on this branch,
    with two generated proposals for one item accepted in turn:
    ``theurian migrate validate`` exited 4 for the whole project --
    *"../knowledge/architecture/retry-policy.md hashes to abc7cdb70713 but the
    migration pins 4f9c5503e198"* -- and no migration could be applied
    afterwards, because the second acceptance had replaced the body the first
    migration still names.

    Unpinning the digest would have hidden it rather than fixed it: replaying
    the first migration would then read the *second* revision's body and record
    it under the first revision's id. A fresh ULID per revision is what makes
    the file the migration named stay the file it named.
    """
    leaf = item_id.value.rpartition(".")[2]
    name = f"{leaf}.{revision_id.value}{body_extension(content_type)}"
    namespace = item_id.namespace
    if not namespace:
        return PurePosixPath(name)
    return PurePosixPath(*namespace.split(".")) / name
