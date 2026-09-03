"""What a refusal on the accept path may print of the author's own strings (#360).

``test_proposal_secret_scan.py`` proves the *scan* refuses what an acceptance
would land. This is the other half, and neither of the two can say it: a refusal
that fires for some entirely unrelated reason -- a name already taken, a body
that is not where the migration says it is, a document the parser cannot read --
prints the author's filename, ``contentFile`` or ``id`` on its way out, and
several of those refusals run *before* the scan does. Measured on ``63e3851``
under the shipped ``block`` default, a credential placed in any of the three was
echoed at full length into the terminal and into ``accept --json``.

Two kinds of test live here, and the first is the one that closes the class:

* :func:`test_every_interpolation_in_a_message_is_gated_or_recorded` reflects
  over ``proposal_service.py``'s own syntax tree, so the population is *every*
  interpolation in that module rather than the ones a reader thought to
  enumerate. A new raw ``{name}`` in a new refusal reddens it without anyone
  remembering this file exists.
* The end-to-end tests drive representative members of that population through
  the real :class:`ProposalService`, because a gate the refusal path does not
  actually call is a gate that passes its own unit test.

**The population is one module's, and the boundary is deliberate.** The accept
path also reaches ``migration_loader.py``, which prefixes a landed migration's
filename onto every error it raises and is read by four other commands besides;
``_names``' own docstring records that as an out-of-module population, measured
and not closed. A reader who takes the reflection below for a whole-codebase
claim will be wrong about that one.

Its own module rather than an addition to ``test_proposal_secret_scan.py``,
which is already 3,500 lines: the fixtures below are that module's, copied for
the reason it records -- a fixture shared across files would have to move to a
``conftest`` and take every other test in those modules with it.
"""

from __future__ import annotations

import ast
import base64
import hashlib
import inspect
import os
import re
from collections.abc import Collection, Iterator, Mapping
from pathlib import Path
from typing import Final

import pytest
import yaml
from fakes.clock import FrozenClock
from fakes.ids import SeededIdGenerator

from theurian.application import proposal_service
from theurian.application.project_service import ProjectPaths, initialize_project
from theurian.application.proposal_service import (
    _MAX_NAME_CHARS,
    _MAX_REPORT_CHARS,
    _REDACTED_NAME,
    _REDACTED_REPORT,
    _TRUNCATED,
    ProposalError,
    ProposalRequest,
    ProposalService,
    _bounded,
    _names,
    _their_words,
)
from theurian.cli.migration_pipeline import rehearse_migration_set
from theurian.domain.enums import KnowledgeKind
from theurian.domain.errors import IrregularSourceFileError
from theurian.domain.identifiers import AgentId, ItemId, MigrationId, ProjectId, RevisionId, TaskId
from theurian.domain.knowledge import SourceAnchor
from theurian.domain.migration import Migration, current_revision_in
from theurian.domain.project import DEFAULT_KNOWLEDGE_DIRECTORY
from theurian.domain.proposal import Evidence, is_migration_file_name
from theurian.domain.values import MARKDOWN
from theurian.infrastructure.filesystem.migration_loader import (
    load_migrations,
    validate_migration_document,
)
from theurian.security.content_secrets import scan_text

pytestmark = pytest.mark.integration

SCHEMAS = Path(__file__).resolve().parents[4] / "schemas"

ANCHOR = SourceAnchor(
    provider="git",
    source_uri="https://github.com/acme/api/commit/0123456789abcdef",
    commit_sha="0123456789abcdef",
)

EVIDENCE = Evidence(
    agent_id=AgentId("claude-code"),
    task_id=TaskId("task-7"),
    model="claude-opus-5",
    reasoning="The review thread on #41 settled the retry budget at three attempts.",
    anchors=(ANCHOR,),
)

CLEAN_BODY: Final = "# Retry policy\n\nThree attempts, then fail loudly.\n"

#: A credential spelled the way a *migration filename* can carry one. The name
#: pattern is ``<ulid>-[a-z0-9]+(-[a-z0-9]+)*.yaml``, so only a lower-case family
#: fits in one; ``openai-api-key`` is ``sk-`` plus 20 or more candidate
#: characters. Derived rather than drawn, for the reason
#: ``test_proposal_secret_scan.py``'s ``PLANTED_TOKEN`` records: a drawn fixture
#: reddens the suite for its own luck, and no credential-shaped literal then
#: exists in the file.
NAME_SECRET: Final = (
    "sk-" + hashlib.sha256(b"theurian refusal-name fixture (#360)").hexdigest()[:40]
)

#: The same, for a second channel, so re-seeding one fixture cannot silently
#: change what another one tests.
ID_SECRET: Final = "sk-" + hashlib.sha256(b"theurian refusal-id fixture (#360)").hexdigest()[:40]

#: A credential at the ``openai-api-key`` family's 20-character floor. Short
#: enough to survive PyYAML's own snippet window whole, which is what makes the
#: parser-error channel a full-length echo rather than a truncated one.
REPORT_SECRET: Final = "sk-" + "a" * 20

#: The *generic* family, for the straddle sweep, because the two families fail
#: the boundary differently: a prefix family loses its anchor as soon as the cut
#: passes ``sk-``, while ``high-entropy-token`` keeps matching every shortened
#: run that still clears the 32-character floor -- which is why it published 31
#: of its 43 characters where ``sk-`` published 22. Base64url of a digest, so it
#: carries the mixed case and the digit the class gate wants without being drawn.
HIGH_ENTROPY_SECRET: Final = (
    base64.urlsafe_b64encode(hashlib.sha256(b"theurian straddle fixture (#360 R1-B)").digest())
    .decode()
    .rstrip("=")
)


@pytest.fixture
def paths(tmp_path: Path) -> Iterator[ProjectPaths]:
    root = tmp_path / "demo"
    root.mkdir()
    project = ProjectPaths(root=root, knowledge_dir=root / DEFAULT_KNOWLEDGE_DIRECTORY)
    initialize_project(project)
    yield project


@pytest.fixture
def service(paths: ProjectPaths) -> ProposalService:
    def current_revision(item_id: ItemId) -> RevisionId | None:
        loaded = load_migrations(paths.root, paths.migrations, SCHEMAS)
        return current_revision_in(loaded.migration_set, item_id)

    def landed_migration(migration_id: MigrationId) -> Migration | None:
        loaded = load_migrations(paths.root, paths.migrations, SCHEMAS)
        return loaded.migration_set.get(migration_id)

    def landed_migrations() -> Collection[Migration]:
        loaded = load_migrations(paths.root, paths.migrations, SCHEMAS)
        return loaded.migration_set

    return ProposalService(
        paths=paths,
        project_id=ProjectId("demo"),
        clock=FrozenClock(),
        ids=SeededIdGenerator(),
        validate=lambda document: validate_migration_document(document, SCHEMAS),
        current_revision=current_revision,
        landed_migration=landed_migration,
        landed_migrations=landed_migrations,
        rehearse=lambda candidate: rehearse_migration_set(candidate, clock=FrozenClock()),
    )


def _request(body: str = CLEAN_BODY) -> ProposalRequest:
    return ProposalRequest(
        item_id=ItemId("architecture.retry-policy"),
        title="Retry policy",
        kind=KnowledgeKind.ARCHITECTURE,
        owner="platform-team",
        author="platform-team@example.com",
        description="Record the retry budget the API review settled on.",
        body=body,
        content_type=MARKDOWN,
        evidence=EVIDENCE,
        source_anchors=(ANCHOR,),
    )


def _published(error: ProposalError) -> str:
    """Everything one refusal puts in front of a caller.

    Both halves, because ``--json`` publishes both: ``cli.commands._fail`` writes
    ``{"error": str(exc), "remedy": exc.remedy}``, so a value withheld from the
    message and repeated in the remedy is published all the same.
    """
    return f"{error}\n{error.remedy}"


# -- the class: every interpolation that can reach a caller ------------------

#: The gates a string reaching a message must pass through. Matched on the
#: *expression text* rather than by resolving the call, so the reflection below
#: needs no import graph: a new gate has to be named here to be recognised, which
#: is the point -- a helper that quietly stops scanning would not be a gate.
_GATES: Final = (
    "_names(",
    "_one_name(",
    "_plain(",
    "_rendered_scalar(",
    "_their_words(",
)

#: Every string reaching a message in ``proposal_service.py`` that is *not*
#: routed through a gate, with why that particular one needs none. This is an
#: allowlist and not a description: an entry absent from it is a failure, so a
#: new raw ``{name}`` reddens
#: :func:`test_every_interpolation_in_a_message_is_gated_or_recorded` whether or
#: not anyone remembers this file.
#:
#: **Keyed on (enclosing function, expression), and the pair is load-bearing.**
#: Keyed on the expression alone, eleven of its rows were bare identifiers --
#: ``name``, ``named``, ``head``, ``at``, ``index`` -- and a bare identifier is a
#: module-wide wildcard: round one reintroduced #360's exact defect, spelled
#: ``name = path.name`` at a real refusal site, and it SURVIVED the full suite
#: with this reflection green, because some *other* function's ``name`` had
#: earned the row. The pair confines each reason to the site it was written
#: about. ``ast.unparse`` normalises whitespace and quoting, so a reformat does
#: not redden it and a change of expression does.
#:
#: **A reason that is true by a different argument is a different row.** Two of
#: these were false as written before round one attacked them, and both were
#: false in the same way: a true sentence about one site, copied to another where
#: something else is what makes it safe.
_UNGATED_BY_CONSTRUCTION: Final[Mapping[tuple[str, str], str]] = {
    # -- validated identifiers, and the messages built out of them -----------
    # A `ProposalId` is a ULID, so nothing a caller typed survives into
    # `location.relative` or a searched-directory list.
    ("_ambiguous_locations", "proposal_id.value"): "ProposalId, an anchored ULID",
    ("_evidence_indeterminate", "proposal_id.value"): "ProposalId, an anchored ULID",
    ("_inferred_answer", "proposal_id.value"): "ProposalId, an anchored ULID",
    ("_no_migration_error", "proposal_id.value"): "ProposalId, an anchored ULID",
    ("_require_directory", "proposal_id.value"): "ProposalId, an anchored ULID",
    ("_require_migration", "proposal_id.value"): "ProposalId, an anchored ULID",
    ("_unreadable", "proposal_id.value"): "ProposalId, an anchored ULID",
    ("refuse", "proposal_id.value"): "ProposalId, an anchored ULID",
    ("_refuse_if_migration_present", "migration_id.value"): "MigrationId, an anchored ULID",
    ("_no_migration_error", "recorded.value"): "MigrationId, an anchored ULID",
    ("_check_expected_revision", "current.value"): "RevisionId, an anchored ULID",
    ("_check_expected_revision", "expected.value"): "RevisionId, an anchored ULID",
    # NOT "an anchored pattern", which was the reason here and is false:
    # `_DOTTED_PATTERN` is lower-case kebab with dots, and `sk-<40 hex>` matches
    # it exactly (measured). What makes this one safe is provenance, not shape --
    # `_check_expected_revision` runs only from `draft`, so the value is the
    # caller's own `--item-id` on this very invocation, not content arriving
    # through a committed proposal directory. It is the caller's to read back.
    ("_check_expected_revision", "request.item_id.value"): (
        "the caller's own --item-id on this invocation, not third-party content"
    ),
    ("_ambiguous_locations", "location.relative"): "built from a validated ULID",
    ("_evidence_indeterminate", "location.relative"): "built from a validated ULID",
    ("_evidence_unscannable", "location.relative"): "built from a validated ULID",
    ("_require_directory", "location.relative"): "built from a validated ULID",
    ("_secret_refusal", "location.relative"): "built from a validated ULID",
    ("_union_refusal", "location.relative"): "built from a validated ULID",
    ("_require_directory", "self._within_project(parent)"): (
        "a project-relative path of this module's own parent directories"
    ),
    ("_require_directory", "' or '.join(searched)"): "the two proposal parents, ULID-free",
    ("_require_directory", "' and '.join(searched)"): "the two proposal parents, ULID-free",
    # -- the OS's own words, never `str(exc)` --------------------------------
    # `str(OSError)` carries the absolute filename and with it the machine's
    # home directory; `strerror` is the OS's category for the failure.
    ("_unreadable", "error.strerror or 'it could not be read'"): "OSError.strerror, or a literal",
    ("refuse", "error.strerror or 'it could not be read'"): "OSError.strerror, or a literal",
    ("_commit", "exc.strerror or 'the write failed'"): "OSError.strerror, or a literal",
    ("_ensure_local_is_ignored", "reason"): "OSError.strerror, or a literal",
    ("_evidence_indeterminate", "_evidence_failure_reason(error)"): (
        "a fixed table keyed on the exception's type, never its text"
    ),
    ("_evidence_unscannable", "_evidence_failure_reason(error)"): (
        "a fixed table keyed on the exception's type, never its text"
    ),
    # -- gated where they were built, interpolated again here ----------------
    # One hop, and the hop is named. Splitting these is what round one asked
    # for: as a single `named` row, the reason below was *false* at the
    # `_ambiguous_locations` site, which never calls `_names` at all.
    ("_unreadable", "named"): "assigned from _names two lines above",
    ("_read_failure_remedy", "named"): "the gated name _unreadable passed in",
    ("_permission_remedy", "named"): "the gated name _unreadable passed in",
    ("_permission_remedy", "named_parent"): "assigned from _names two lines above",
    ("_remove_proposal_sources", "leftover"): "assigned from _names two lines above",
    ("_read_failure_remedy", "self._permission_remedy(error, named)"): (
        "a remedy built from the gated name above"
    ),
    # A different `named` entirely, and safe for a different reason: it is
    # `" and ".join(location.relative)`, so it is ULID-derived like every other
    # `location.relative` row and reaches `_names` nowhere.
    ("_ambiguous_locations", "named"): "the two locations joined, each built from a ULID",
    ("_read_failure_remedy", "self._MIGRATIONS_TAIL"): "this module's own literal",
    # -- this module's own literals, and integers ----------------------------
    ("__post_init__", "AUTHORED_IN_THEURIAN"): "a domain literal",
    ("_refuse_a_document_the_schema_rejects", "MIGRATION_API_VERSION"): "a domain literal",
    ("_refuse_past_the_operation_cap", "MAX_UPSERT_OPERATIONS"): "this module's own constant",
    ("_evidence_indeterminate", "EVIDENCE_FILE"): "this module's own constant",
    ("_evidence_unscannable", "EVIDENCE_FILE"): "this module's own constant",
    ("_inferred_answer", "EVIDENCE_FILE"): "this module's own constant",
    ("_landed_text", "_AT_BODY_CONTENT"): "a finding-location literal (#349)",
    ("_landed_text", "_AT_BODY_PATH"): "a finding-location literal (#349)",
    ("__post_init__", "name"): "a field name from a fixed tuple in the loop above",
    ("_fields", "name"): "a field name from an allowlist tuple, never a document key",
    ("_authored_strings", "at"): "a finding location built from this module's literals (#349)",
    ("_fields", "at"): "a finding location built from this module's literals (#349)",
    ("_list_strings", "at"): "a finding location built from this module's literals (#349)",
    ("_metadata_strings", "at"): "a finding location built from this module's literals (#349)",
    ("_authored_strings", "index"): "an integer position",
    ("_landed_text", "index"): "an integer position",
    ("_list_strings", "index"): "an integer position",
    ("_metadata_strings", "index"): "an integer position",
    ("_refuse_past_the_operation_cap", "count"): "an integer the caller is deliberately told",
    # -- the gate's own internals --------------------------------------------
    ("_one_name", "head"): "what _bounded returned, inside the gate itself",
    ("_one_name", "_TRUNCATED"): "this module's own constant",
    ("_plain", "head"): "what _bounded returned, inside the gate itself",
    ("_plain", "_TRUNCATED"): "this module's own constant",
    ("_names", "shown"): "names already through _one_name, inside _names",
    ("_names", "remaining"): "an integer count, inside _names",
    # -- arguments to an error type that renders them itself -----------------
    # `IrregularSourceFileError` interpolates `shape` and `referrer`; the
    # `referrer` at both sites is gated, and `shape` is a literal from the fixed
    # vocabulary `read_source_file` chooses from (never a path).
    ("_read_within_project", "exc.shape"): "a fixed shape literal from the security layer",
    ("_commit", "exc.shape"): "a fixed shape literal from the security layer",
    # `PathEscapeError` renders NEITHER positional argument: with `entry=None`
    # -- which is every construction in this module -- its message is the fixed
    # "Path escapes the permitted root" and its remedy names no path.
    # `tests/unit/test_path_security.py::
    # test_no_reachable_refusal_branch_echoes_the_attacker_supplied_path` is what
    # holds that. These rows exist so that passing `entry=` from here, which IS
    # rendered, reddens the reflection.
    ("_within_project", "str(path)"): "PathEscapeError renders no positional argument",
    ("_within_project", "str(self._paths.root)"): "PathEscapeError renders no positional argument",
    ("_reject_symlink_in_chain", "str(path)"): "PathEscapeError renders no positional argument",
    ("_reject_symlink_in_chain", "str(root)"): "PathEscapeError renders no positional argument",
    ("_destination_of", "content_file"): "PathEscapeError renders no positional argument",
    ("_destination_of", "str(knowledge)"): "PathEscapeError renders no positional argument",
}


#: Error types this module constructs whose *arguments* become message text
#: somewhere else. An f-string walk cannot see these: the value is handed over as
#: an argument and the interpolation happens in the other module's
#: ``__init__`` -- which is exactly how ``IrregularSourceFileError``'s
#: ``referrer`` carried an author's ``contentFile`` character-for-character into
#: both halves of a CP-2 payload while the reflection stayed green (round 1,
#: R1-A face ii).
#:
#: Only the types whose constructor *renders* what it is given belong here.
#: ``ProposalError`` and its subclasses build their own text from f-strings in
#: this module, so the walk already sees their arguments as interpolations;
#: listing them again would double-count without adding a check.
_RENDERING_ERROR_TYPES: Final = frozenset({"IrregularSourceFileError", "PathEscapeError"})


def _callee(node: ast.expr) -> str:
    """The bare name a call's callee ends in, for ``Name`` and ``Attribute`` alike."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return ""


def _interpolations() -> list[tuple[int, str, str]]:
    """Every string this module hands to a message, with its line.

    Two shapes, because one of them is invisible to the other's walk:

    * **f-string interpolations**, over the whole module and not only the error
      constructions -- a remedy is assembled by
      :meth:`ProposalService._read_failure_remedy` *outside* the
      ``ProposalError(...)`` call that publishes it, so a walk scoped to error
      constructions would call that remedy unreachable and miss it.
    * **arguments to an error type that renders them itself**
      (:data:`_RENDERING_ERROR_TYPES`). A value handed to another module's
      constructor never becomes an ``ast.FormattedValue`` here, so the f-string
      walk is structurally blind to it. That blindness was not theoretical: it
      is where round one's second R1-A face lived.

    An argument that needs no gate is not special-cased here: it goes in the same
    allowlist every f-string uses, so the two shapes are judged by one rule and
    not by two.

    Each is returned with the function that encloses it, because the allowlist is
    keyed on the pair -- see :data:`_UNGATED_BY_CONSTRUCTION` for what a
    bare-identifier key let through.
    """
    tree = ast.parse(inspect.getsource(proposal_service))
    scopes = [
        node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
    ]

    def enclosing(lineno: int) -> str:
        # Innermost wins: a nested `def` inside a method -- `refuse` inside
        # `_unmoved_generated_bodies` is the live one -- owns its own rows, and
        # taking the outermost would merge them with its parent's.
        names = [
            node.name
            for node in scopes
            if node.lineno <= lineno <= (node.end_lineno or node.lineno)
        ]
        return names[-1] if names else "<module>"

    found = [
        (node.lineno, enclosing(node.lineno), ast.unparse(node.value))
        for node in ast.walk(tree)
        if isinstance(node, ast.FormattedValue)
    ]
    found.extend(
        (argument.lineno, enclosing(argument.lineno), ast.unparse(argument))
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and _callee(node.func) in _RENDERING_ERROR_TYPES
        for argument in [*node.args, *(keyword.value for keyword in node.keywords)]
    )
    return found


def test_the_reflection_finds_the_interpolations_it_claims_to_range_over() -> None:
    """The positive control on the test below, which is vacuous without it.

    A walk that found nothing -- a renamed module, a changed AST node type --
    would report every interpolation gated and pass. Two floors: there are
    interpolations at all, and the two shapes the allowlist and the gate set are
    each meant to cover are both present.
    """
    found = _interpolations()

    assert len(found) > 50, f"the walk found only {len(found)} interpolations; it is not reaching"
    assert any(expression.startswith(_GATES) for _, _, expression in found), (
        "the walk found no gated interpolation, so the gate set matches nothing"
    )
    keys = {(function, expression) for _, function, expression in found}
    assert keys & set(_UNGATED_BY_CONSTRUCTION), (
        "the walk found none of the allowlisted rows, so the allowlist matches nothing"
    )
    # The blind spot R1-A face (ii) lived in, held open by name: an f-string-only
    # walk returns nothing for this line, so a green reflection would say nothing
    # about it.
    assert any(
        function == "_read_within_project" and expression.startswith("_plain(")
        for _, function, expression in found
    ), "the walk no longer reaches an error constructor's arguments"


def test_every_interpolation_in_a_message_is_gated_or_recorded() -> None:
    """The class #360 names, closed by population rather than by site.

    The issue's own table listed seven sites. Reading for them found seven; this
    walk finds every interpolation in the module, which is what a *class* closure
    needs: a refusal added next year with a raw ``{path.name}`` in it is caught
    here, and no reviewer has to have read this file.

    An addition is either routed through one of :data:`_GATES` or added to
    :data:`_UNGATED_BY_CONSTRUCTION` with the reason it needs no gate -- and
    writing that reason down is the review, because it is the sentence that is
    false when the value is in fact the author's.
    """
    raw = sorted(
        (lineno, function, expression)
        for lineno, function, expression in _interpolations()
        if not expression.startswith(_GATES)
        and (function, expression) not in _UNGATED_BY_CONSTRUCTION
    )

    assert raw == [], (
        "these interpolations reach a message ungated and unrecorded; route each through "
        f"_names/_their_words/_rendered_scalar or record why it needs no gate: {raw}"
    )


# -- the gate itself ---------------------------------------------------------


def test_the_planted_names_are_reported_by_the_detector() -> None:
    """The positive control every test below rests on.

    Each asserts that a refusal withholds a planted credential. A plant the
    detector does not report would be withheld by nothing at all and every one of
    them would pass against the unfixed build.
    """
    for label, planted in (
        ("NAME_SECRET", NAME_SECRET),
        ("ID_SECRET", ID_SECRET),
        ("REPORT_SECRET", REPORT_SECRET),
    ):
        families = [finding.family for finding in scan_text(planted)]
        assert families == ["openai-api-key"], (
            f"the detector reports {families or 'nothing'} for {label}, so a redaction test "
            f"built on it would be testing nothing"
        )


def test_a_name_the_detector_reports_is_withheld_whole_and_not_in_part() -> None:
    """No prefix, no suffix, no elision of the middle -- a literal instead.

    A partial echo is the walk-around ``SecretFinding``'s four-character bound
    exists to prevent, and it is the shape ``_landed_text`` already refuses for a
    finding's *location*. The assertion is on the longest run rather than on the
    whole name, because "the whole name is absent" is satisfied by an
    implementation that drops one character.
    """
    rendered = _names([f"{NAME_SECRET}.yaml"])

    assert rendered == _REDACTED_NAME, f"a dirty name was rendered as {rendered!r}"
    longest = max(NAME_SECRET.split("-"), key=len)
    assert longest not in rendered, f"{longest!r} survived into {rendered!r}"


def test_a_clean_name_is_still_quoted_and_whole() -> None:
    """AC-2: the bound must not mangle the names this product mints itself.

    ``_looks_like_a_secret`` subtracts Theurian's own ULIDs before it judges,
    which is what makes this possible at all: all 26 of this repository's
    committed migration filenames scored above the entropy floor before that
    subtraction. A generated migration filename, a generated body path and a bare
    ULID all have to come out readable, or the fix for #360 has broken every
    ordinary refusal on the path.
    """
    generated = "01K3Z8Q9V4MRB7T2XNFCD5HGJW-retry-policy.yaml"
    assert is_migration_file_name(generated), "the fixture is not a name accept would ever see"

    for name in (
        generated,
        "architecture/retry-policy.01K3Z8Q9V4MRB7T2XNFCD5HGJW.md",
        "../knowledge/architecture/retry-policy.01K3Z8Q9V4MRB7T2XNFCD5HGJW.md",
        "01K3Z8Q9V4MRB7T2XNFCD5HGJW",
    ):
        assert _names([name]) == repr(name), f"{name!r} was mangled by the bound"


def test_a_name_past_the_bound_is_cut_and_says_so_without_publishing_its_length() -> None:
    """A ``contentFile`` is a raw YAML scalar, so its length is the author's.

    ``_MAX_NAMES_LISTED`` bounded how many names a refusal lists and nothing about
    how long one is: the ``_body_moves`` refusal echoed the whole scalar. The
    marker sits outside the quotes so a name that genuinely ends in an ellipsis is
    still tellable from one that was cut, and it carries no count -- how much was
    dropped is the contributor's number, the same reason ``_secret_refusal``
    suppresses ``_names``' own "and N more" tail.
    """
    long_name = "a" * (_MAX_NAME_CHARS + 500)

    rendered = _names([long_name])

    assert rendered.endswith(_TRUNCATED), f"the cut is not marked: {rendered!r}"
    assert len(rendered) < _MAX_NAME_CHARS + 50, f"the cut did not happen: {len(rendered)}"
    assert str(len(long_name)) not in rendered, "the refusal published the name's own length"


def test_the_scan_reads_the_whole_string_and_the_cut_happens_after_it() -> None:
    """Three directions, and the third is the one that was wrong (round 1, R1-B).

    A credential wholly inside the cut has to redact, and a credential wholly
    past it has to redact too: the scanned set is the *whole* string, so the
    printed cut is always a substring of something that scanned clean. Cutting
    first inverted the second case -- the head scanned clean and printed -- and
    the straddle below is what made that a leak rather than a preference.

    GHSA-3f65's lesson survives the inversion. That advisory is about keying a
    gate on a *subset* of what it protects; here the scanned set is a superset of
    the printed set, so nothing can drift in between. What the conservative
    direction costs is a false redaction -- a name whose far tail is dirty is
    withheld whole -- which is the right way to be wrong.
    """
    # A path separator either side of the plant: `openai-api-key` anchors on
    # `\bsk-`, and a run of `b` immediately before it is a word character that
    # denies the boundary -- the plant would then be reported by nothing and the
    # assertions below would be the only thing that noticed.
    past_the_cut = "b" * _MAX_NAME_CHARS + f"/{NAME_SECRET}"
    inside_the_cut = f"{NAME_SECRET}/" + "b" * _MAX_NAME_CHARS

    assert scan_text(past_the_cut), "the fixture plants nothing the detector reports"
    assert scan_text(inside_the_cut), "the fixture plants nothing the detector reports"
    assert _bounded(inside_the_cut, _MAX_NAME_CHARS) is None, (
        "a credential inside the cut was not caught"
    )
    assert _bounded(past_the_cut, _MAX_NAME_CHARS) is None, (
        "a credential past the cut was not caught, so the scan ran on the cut text and a "
        "credential straddling the boundary would publish its head"
    )


@pytest.mark.parametrize("limit_name", ["name", "report"])
def test_no_start_offset_lets_a_credential_publish_a_fragment_of_itself(limit_name: str) -> None:
    """The straddle sweep, which is what R1-B actually measured (adversarial e14).

    A single boundary case is a point; the defect was a curve. The token is slid
    across the bound one character at a time and *every* offset has to withhold
    it whole. Against the cut-first order this went red across the whole sweep --
    13 to 30 characters published at the name bound, 31 of 43 at the report bound
    -- and the single-offset assertion above passed throughout, which is why the
    sweep is here and not one more literal.

    Padded with ``/`` and not with a candidate character. A run of ``z`` before
    the token confounds two mechanisms -- entropy dilution and the cut -- so a
    green sweep would not say which one held. A separator is outside
    ``_CANDIDATE_CLASS``, so each segment is its own candidate run and the cut is
    the only thing under test.
    """
    limit = _MAX_NAME_CHARS if limit_name == "name" else _MAX_REPORT_CHARS
    token = NAME_SECRET if limit_name == "name" else HIGH_ENTROPY_SECRET
    assert scan_text(token), "the fixture plants nothing the detector reports"

    published = {}
    for start in range(limit - len(token), limit + 1):
        rendered = _bounded("/" * start + token, limit)
        if rendered is None:
            continue
        longest = max((n for n in range(len(token) + 1) if token[:n] in rendered), default=0)
        if longest:
            published[start] = longest

    assert published == {}, (
        f"a credential straddling the {limit_name} bound published a fragment of itself at these "
        f"start offsets (offset: characters published): {published}"
    )


def test_another_components_words_are_bounded_and_redacted_but_not_quoted() -> None:
    """A parser's message is a sentence a reader finishes, not a name to copy.

    ``repr`` on a multi-line ``yaml.YAMLError`` is unreadable, so ``_their_words``
    renders unquoted -- which is the whole of the difference from ``_one_name``.
    The bound and the redaction are the same gate.
    """
    assert _their_words(ValueError("plainly wrong")) == "plainly wrong"
    assert _their_words(ValueError(f"refused {REPORT_SECRET}")) == _REDACTED_REPORT
    assert _their_words(ValueError("z" * (_MAX_REPORT_CHARS + 5))).endswith(_TRUNCATED)


# -- the population, driven through the real service -------------------------


def _renamed_to(directory: Path, slug: str) -> Path:
    """The proposal's migration file, renamed ``<its own ULID>-<slug>.yaml``.

    The ULID prefix is kept, so ``_require_filename_matches_id`` still agrees with
    the document's ``id`` and cannot be what refuses. A committed proposal
    directory is a contributor's (ADR-0013 point 7), so a hand-renamed file is
    the ordinary shape here and not a contrived one.
    """
    original = next(path for path in directory.iterdir() if is_migration_file_name(path.name))
    renamed = directory / f"{original.name.split('-', 1)[0]}-{slug}.yaml"
    assert is_migration_file_name(renamed.name), (
        f"{renamed.name!r} is not a name accept recognises as a migration, so the refusal under "
        f"test would be 'this proposal holds no migration' instead"
    )
    original.rename(renamed)
    return renamed


def test_a_name_already_in_place_is_refused_without_printing_the_name(
    service: ProposalService, paths: ProjectPaths
) -> None:
    """``_refuse_if_migration_present``, and it ran *before* the scan.

    The sharpest member of the class: the refusal fires on a name collision, has
    nothing to do with secrets, and reached the terminal ahead of the control
    that would have redacted the very same string. Reproduced on ``63e3851``:
    the whole ``sk-`` token, verbatim, under the shipped ``block`` default.
    """
    drafted = service.draft(_request())
    renamed = _renamed_to(drafted.directory, NAME_SECRET)
    paths.migrations.mkdir(parents=True, exist_ok=True)
    (paths.migrations / renamed.name).write_text("placeholder\n", encoding="utf-8")

    with pytest.raises(ProposalError) as caught:
        service.accept(drafted.proposal_id)

    published = _published(caught.value)
    assert "already in .theurian/migrations/" in published, "a different refusal fired"
    assert NAME_SECRET not in published, f"the filename was echoed whole: {published}"
    assert _REDACTED_NAME in published, "the refusal does not say a name was withheld"


def test_a_body_the_migration_cannot_find_is_refused_without_printing_its_path(
    service: ProposalService,
) -> None:
    """``_body_moves``, which is #339's face of this class, closed with it.

    #339 filed the missing-body refusal on its own; it is the same root cause as
    every other site here -- an author-written name interpolated into a refusal
    that runs before the scan -- so it closes with the class rather than beside
    it. Both names in the message are the author's: the ``contentFile`` as
    written and the tail it resolves to.
    """
    drafted = service.draft(_request())
    document = yaml.safe_load(drafted.migration_file.read_text(encoding="utf-8"))
    for operation in document["operations"]:
        if operation["op"] == "upsertRevision":
            operation["contentFile"] = f"../knowledge/architecture/{NAME_SECRET}.md"
    drafted.migration_file.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")

    with pytest.raises(ProposalError) as caught:
        service.accept(drafted.proposal_id)

    published = _published(caught.value)
    assert "is not in the proposal directory" in published, "a different refusal fired"
    assert NAME_SECRET not in published, f"the contentFile was echoed whole: {published}"


def test_a_mismatched_id_is_refused_without_printing_the_id(service: ProposalService) -> None:
    """``_require_filename_matches_id``, on a value the schema has not seen yet.

    The ``id`` here is raw YAML: this check runs before stage-1 validation, so
    "it is a ULID" is exactly what has not been established. ``is_bounded_scalar``
    already refused an alias bomb before rendering it (#291); what it did not do
    is stop a 43-character credential, which is a perfectly bounded scalar.
    """
    drafted = service.draft(_request())
    document = yaml.safe_load(drafted.migration_file.read_text(encoding="utf-8"))
    document["id"] = ID_SECRET
    drafted.migration_file.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")

    with pytest.raises(ProposalError) as caught:
        service.accept(drafted.proposal_id)

    published = _published(caught.value)
    assert "filename ULID must equal" in published, "a different refusal fired"
    assert ID_SECRET not in published, f"the authored id was echoed whole: {published}"


def test_a_binary_id_is_refused_without_printing_what_it_decodes_to(
    service: ProposalService,
) -> None:
    """The same refusal, reached with a value that is not a ``str`` (round 1, R1-A).

    ``_rendered_scalar`` used to select its gate by *type*: a ``str`` went through
    the gate and everything else took a bare ``repr``, on the recorded ground that
    ``is_bounded_scalar`` admits only "a bool, a number, ``None`` or a timestamp".
    The enumeration was wrong. ``_StrictLoader`` is a ``SafeLoader``, so
    ``!!binary`` constructs ``bytes``; ``is_bounded_scalar`` admits ``bytes``
    under the render cap explicitly; and ``repr(bytes)`` spells the decoded
    credential verbatim. Both reviewers reproduced it through the real CLI under
    the shipped ``block`` default, at a refusal that fires before the scan.

    No real corpus produces a ``!!binary`` id -- ``draft`` writes a ULID string --
    which is exactly why this test has to plant one: the arm it drives is
    unreachable from every fixture the suite otherwise builds, so it survived its
    own deletion until now.
    """
    drafted = service.draft(_request())
    document = drafted.migration_file.read_text(encoding="utf-8")
    encoded = base64.b64encode(ID_SECRET.encode()).decode()
    document = re.sub(r"^id: .*$", f"id: !!binary {encoded}", document, count=1, flags=re.MULTILINE)
    drafted.migration_file.write_text(document, encoding="utf-8")

    parsed = yaml.safe_load(document)["id"]
    assert isinstance(parsed, bytes), (
        f"the fixture did not produce bytes but {type(parsed).__name__}; the non-str arm this "
        f"test exists for would not be reached"
    )
    assert ID_SECRET in repr(parsed), "the plant does not survive repr, so nothing could leak it"

    with pytest.raises(ProposalError) as caught:
        service.accept(drafted.proposal_id)

    published = _published(caught.value)
    assert "filename ULID must equal" in published, "a different refusal fired"
    assert ID_SECRET not in published, f"the decoded bytes were echoed whole: {published}"


#: A FIFO is the shape whose ``st_size`` bounds nothing, and it is what makes the
#: irregular-file refusal fire at all. POSIX-only, so the one test that needs it
#: skips where it cannot be made -- the same guard ``test_propose_cli.py`` carries.
_CAN_MAKE_A_BLOCKING_FILE: Final = hasattr(os, "mkfifo")


@pytest.mark.skipif(not _CAN_MAKE_A_BLOCKING_FILE, reason="os.mkfifo is POSIX-only")
def test_an_irregular_body_is_refused_without_printing_the_path_the_author_chose(
    service: ProposalService,
) -> None:
    """The referrer, which no f-string in this module interpolates (round 1, R1-A).

    ``IrregularSourceFileError`` builds its own message and remedy from the
    ``referrer`` it is handed, so the value never becomes an ``ast.FormattedValue``
    here and the reflection was structurally blind to it. What was handed over was
    ``relative``, described in the source as "Theurian's own construction, never
    an authored string" -- true of the prefix and false of the leaf, because
    ``_destination_of`` *resolves* the author's ``contentFile`` and resolution
    normalizes a path without renaming its components. Containment is not
    provenance.

    A FIFO is what makes the refusal fire, so this needs local write access at
    accept time; the echo is what it publishes once it does.
    """
    drafted = service.draft(_request())
    body = drafted.body_file
    tail = body.relative_to(drafted.directory).with_name(f"{NAME_SECRET}.md")
    document = yaml.safe_load(drafted.migration_file.read_text(encoding="utf-8"))
    for operation in document["operations"]:
        if operation["op"] == "upsertRevision":
            operation["contentFile"] = f"../knowledge/{tail.as_posix()}"
    drafted.migration_file.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")
    body.unlink()
    os.mkfifo(drafted.directory / tail)

    with pytest.raises(IrregularSourceFileError) as caught:
        service.accept(drafted.proposal_id)

    published = f"{caught.value}\n{caught.value.remedy}"
    assert "not a regular file" in published, "a different refusal fired"
    assert NAME_SECRET not in published, f"the authored path was echoed whole: {published}"


def test_a_migration_the_parser_refuses_withholds_the_name_and_the_quoted_token(
    service: ProposalService,
) -> None:
    """``_parse_migration``: two channels in one message, and both pre-scan.

    The filename is one. The other is PyYAML's own text, which quotes the
    offending source line through ``Mark.get_snippet``. The positive control
    below is the raw parser error, so this test knows the snippet really did
    carry the whole token before the gate saw it.

    **What it holds is that a token the detector reports is withheld, not that
    the snippet carries nothing.** ``get_snippet`` is bounded to a window around
    the mark, so a *longer* token arrives already cut -- and a 43-character
    ``sk-`` token cut past its prefix is lower-case hex, which no family matches.
    That fragment is printed, it is the detector's recorded best-effort residual
    rather than this gate's, and ``_bounded`` names it with the measurement.
    :data:`REPORT_SECRET` sits at the family's 20-character floor precisely so it
    survives the window whole and the gate is what withholds it.
    """
    drafted = service.draft(_request())
    renamed = _renamed_to(drafted.directory, NAME_SECRET)
    source = f"apiVersion: theurian.dev/v1\nauthor: {REPORT_SECRET}: x\n"
    renamed.write_text(source, encoding="utf-8")

    with pytest.raises(yaml.YAMLError) as raw:
        yaml.safe_load(source)
    assert REPORT_SECRET in str(raw.value), (
        "PyYAML's snippet does not carry the planted token, so this test would pass with the "
        "parser channel ungated"
    )

    with pytest.raises(ProposalError) as caught:
        service.accept(drafted.proposal_id)

    published = _published(caught.value)
    assert "could not be read as a migration" in published, "a different refusal fired"
    assert NAME_SECRET not in published, f"the filename was echoed whole: {published}"
    assert REPORT_SECRET not in published, f"the parser's snippet was echoed whole: {published}"


def test_two_migration_files_are_refused_without_printing_either_name(
    service: ProposalService,
) -> None:
    """``_require_migration``, whose list is built from a directory listing.

    The names come from ``iterdir``, so they are whatever the contributor's
    directory holds -- and the refusal lists them all, up to the cap. One dirty
    name among several must not take the others down with it: the clean sibling
    stays readable, which is what makes the gate per name rather than per message.
    """
    drafted = service.draft(_request())
    dirty = _renamed_to(drafted.directory, NAME_SECRET)
    clean = drafted.directory / f"{dirty.name.split('-', 1)[0]}-retry-policy.yaml"
    clean.write_text(dirty.read_text(encoding="utf-8"), encoding="utf-8")

    with pytest.raises(ProposalError) as caught:
        service.accept(drafted.proposal_id)

    published = _published(caught.value)
    assert "two or more migration files" in published, "a different refusal fired"
    assert NAME_SECRET not in published, f"the dirty name was echoed whole: {published}"
    assert clean.name in published, "the clean sibling was redacted with the dirty one"


def test_a_document_the_schema_rejects_prints_neither_the_value_nor_the_name(
    service: ProposalService, paths: ProjectPaths
) -> None:
    """``_refuse_a_document_the_schema_rejects``, reached under ``warn``.

    ``jsonschema`` quotes the instance it refused, in full and bounded by
    nothing. Under the shipped ``block`` this site is unreachable with a plainly
    spelled credential -- the migration's own bytes are scanned first and the
    acceptance is already refused -- so ``warn`` is what exposes it, and ``warn``
    is a posture a project chooses for false positives rather than one that opts
    out of hygiene in a refusal.
    """
    paths.config.write_text('security:\n  secretScan: "warn"\n', encoding="utf-8")
    drafted = service.draft(_request())
    document = yaml.safe_load(drafted.migration_file.read_text(encoding="utf-8"))
    document["operations"][0]["kind"] = NAME_SECRET
    drafted.migration_file.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")

    with pytest.raises(ProposalError) as caught:
        service.accept(drafted.proposal_id)

    published = _published(caught.value)
    assert "is not a valid migration" in published, "a different refusal fired"
    assert NAME_SECRET not in published, f"the schema error quoted the value: {published}"
