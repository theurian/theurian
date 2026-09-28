"""Which reads still carry a symlink *route* to check, and which no longer can.

``read_source_file`` runs two different containment checks, and only one of them
survives its caller's choice of argument. ``resolve_within_root`` proves where
the path points and works on any spelling. ``assert_no_symlink_escape`` proves
the *route* -- that nothing on the way out of the root, and back, went
unnoticed -- and it walks the components it is handed. Hand it a path
``Path.resolve()`` has already flattened and it still runs, still passes, and
proves nothing: every link is already gone.

That failure is silent by construction. It removes a refusal, so no test that
reads a legitimate file can notice, and it is exactly how the guard spent its
whole first life dead at both of its call sites (issue #288). The obligation on
a caller that can only hold a flattened form is to make its own
``assert_no_symlink_escape`` call upstream, on the string its author wrote --
which ``migration_loader._parse_upsert`` and ``ProposalService._destination_of``
both do.

Until now that obligation was **prose**, in ``read_source_file``'s docstring and
in ``_materialize``'s. Round 1 asked for it as an instrument, in the shape this
repository already uses for questions of the form *which places may do X*
(``test_network_call_sites.py``, ``test_gate_call_sites.py``).

**The population key**, so a reader can attack the key rather than the count:
every ``read_source_file(...)`` call, found by walking the AST of every ``*.py``
under the *imported* ``theurian`` package -- the tree the suite actually ran
against, not a hand-built relative path -- and keyed by
``(module path, enclosing function)``. The scan reads names, exactly as its
siblings do: a call reached through an alias or a re-export would not be seen,
so this is a floor on the review a new read gets and not a proof that one cannot
slip past.

What it cannot check is that a ``FLATTENED`` row's named upstream guard is the
one actually protecting that read; that is an argument, and it is recorded in
each row's ``why``. What it does check is that no site joins, leaves or changes
category without someone writing down which of the two it is.

**A second population, added for MEDIUM-2**: every bare ``resolve_within_root``
call, the same walk over the same tree. ``resolve_within_root`` proves only the
*destination*; a call with no ``assert_no_symlink_escape`` beside it over the
same reference is on its own for the route, and its own site has to say why
that is fine. This walker used to key on ``read_source_file`` alone, so
``okf_import.py::_resolve_body`` grew a second, unpaired ``resolve_within_root``
call invisible to it -- the anchor derivation HIGH-2 found and fixed.
"""

from __future__ import annotations

import ast
import pathlib
from collections import Counter
from typing import Final, NamedTuple

import theurian

#: The shipped package, resolved through the import so the files scanned are the
#: files the suite ran.
_PACKAGE: Final = pathlib.Path(theurian.__file__).parent

#: The route check is live here: the caller passes components as they appear in
#: the tree or as their author wrote them.
REQUESTED = "requested-form"

#: The route check is vacuous here: the caller holds a path ``resolve()`` has
#: already flattened, and the read is bounded by a guard made further upstream.
FLATTENED = "flattened-guarded-upstream"


class _CallSite(NamedTuple):
    """One ``read_source_file`` call, and what the route check is worth there."""

    module: str
    function: str
    form: str
    #: For a ``FLATTENED`` row, the symbol whose ``assert_no_symlink_escape``
    #: call bounds this read. Empty for a ``REQUESTED`` row, which needs none.
    guarded_at: str
    why: str


#: Every ``read_source_file`` call in the shipped package, with the category its
#: argument puts it in. Ten as of 2026-09-25 (ADR-0037 slice S3 added two); the
#: test below re-derives the population rather than trusting that number.
_CALL_SITES: Final = (
    _CallSite(
        module="application/ingestion_service.py",
        function="IngestionService._ingest_one",
        form=REQUESTED,
        guarded_at="",
        why=(
            "the relative path is `path.relative_to(project_root)` over an `rglob` of "
            "the knowledge tree, so its components are the tree's own and a link among "
            "them is still there to be seen"
        ),
    ),
    _CallSite(
        module="application/okf_import.py",
        function="_decode_concept_file",
        form=REQUESTED,
        guarded_at="",
        why=(
            "the relative path is `path.relative_to(root).as_posix()` over an `rglob` of "
            "the bundle, `_ingest_one`'s shape: its components are the tree's own and a "
            "link among them is still there to be seen"
        ),
    ),
    _CallSite(
        module="application/okf_import.py",
        function="_resolve_body",
        form=REQUESTED,
        guarded_at="",
        why=(
            "the relative path is `(relative.parent / concept.theurian_body_file).as_posix()`, "
            "joining the concept document's own bundle-relative directory with the literal "
            "string its front matter wrote, never resolved before this call -- the route walk "
            "is exactly what proves the joined path stays inside the bundle root"
        ),
    ),
    _CallSite(
        module="application/proposal_service.py",
        function="ProposalService._read_within_project",
        form=REQUESTED,
        guarded_at="",
        why=(
            "the path is built under the project root from a ULID directory and a name "
            "`iterdir()` returned; `_reject_symlink_in_chain` also runs first, and "
            "refuses every link in the chain rather than only those that leave"
        ),
    ),
    _CallSite(
        module="application/proposal_service.py",
        function="ProposalService._reads_identical_bytes",
        form=FLATTENED,
        guarded_at="ProposalService._destination_of",
        why=(
            "the relative path is taken from a `_destination_of` result, which is "
            "resolved; the branch is also unreachable in production, because the "
            "loader always sets `content_sha256`"
        ),
    ),
    _CallSite(
        module="application/proposal_service.py",
        function="ProposalService._commit",
        form=FLATTENED,
        guarded_at="ProposalService._destination_of",
        why=(
            "the relative path is `move.destination.relative_to(root)`, and "
            "`move.destination` came from `_destination_of`, which walked the author's "
            "own `contentFile` before returning it"
        ),
    ),
    _CallSite(
        module="cli/migration_pipeline.py",
        function="_materialize",
        form=FLATTENED,
        guarded_at="theurian.infrastructure.filesystem.migration_loader._parse_upsert",
        why=(
            "`candidate.landed` is mixed: its body entries are the loader's "
            "`resolved_content_path`, which is flattened, while its migration-file "
            "entries are `migration.source_path` from a directory listing and are not. "
            "The category is the weaker of the two, because a row that claimed "
            "REQUESTED would be false for the half that matters most"
        ),
    ),
    _CallSite(
        module="infrastructure/filesystem/migration_loader.py",
        function="_load_one",
        form=REQUESTED,
        guarded_at="",
        why=(
            "the relative path is `path.relative_to(project_root)` for an entry "
            "`iterdir()` returned from `.theurian/migrations/`, unresolved"
        ),
    ),
    _CallSite(
        module="infrastructure/filesystem/migration_loader.py",
        function="_parse_upsert",
        form=FLATTENED,
        guarded_at="theurian.infrastructure.filesystem.migration_loader._parse_upsert",
        why=(
            "`relative_posix` is derived from `(migrations_dir / content_file).resolve()`, "
            "so the link is gone by the time it is passed. This function therefore makes "
            "the route check itself, on the author's `contentFile`, a few lines above -- "
            "it is its own upstream, and fixing `read_source_file` alone left `migrate "
            "validate` at exit 0"
        ),
    ),
    _CallSite(
        module="infrastructure/review_evidence/reader.py",
        function="EvidenceReader._read_one",
        form=REQUESTED,
        guarded_at="",
        why=(
            "the relative path is three `iterdir()` entry names joined by `/`, "
            "unresolved -- `migration_loader._load_one`'s shape, over "
            "`.theurian/review/` rather than `.theurian/migrations/`. Nothing "
            "`resolve()`s on the way, so the route walk inside `read_source_file` "
            "is live here and needs no upstream of its own"
        ),
    ),
    _CallSite(
        module="security/project_config.py",
        function="_read_document",
        form=REQUESTED,
        guarded_at="",
        why=(
            "the relative path is a constant filename under the knowledge directory, "
            "carrying no author-chosen component at all"
        ),
    ),
)


def _scoped(node: ast.AST, scope: tuple[str, ...]) -> list[tuple[ast.AST, tuple[str, ...]]]:
    """Every node under ``node``, paired with the ``def``\\ s enclosing it."""
    found: list[tuple[ast.AST, tuple[str, ...]]] = []
    for child in ast.iter_child_nodes(node):
        if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            found.extend(_scoped(child, (*scope, child.name)))
        else:
            found.append((child, scope))
            found.extend(_scoped(child, scope))
    return found


def _read_source_file_calls() -> Counter[tuple[str, str]]:
    """``(module path, enclosing function)`` for every ``read_source_file`` call.

    Counted, not collected into a set. A set collapses two calls in one function
    into one member, so a *second* read added beside an existing one -- the
    cheapest way for an unreviewed flattened read to enter -- would leave the
    population unchanged and this file still reading as complete. Measured: with
    a set, planting exactly that passed.
    """
    calls: Counter[tuple[str, str]] = Counter()
    for path in sorted(_PACKAGE.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node, scope in _scoped(tree, ()):
            if not isinstance(node, ast.Call):
                continue
            function = node.func
            name = function.id if isinstance(function, ast.Name) else None
            if name == "read_source_file":
                calls[(path.relative_to(_PACKAGE).as_posix(), ".".join(scope) or "<module>")] += 1
    return calls


def test_every_read_source_file_call_is_one_this_table_categorises() -> None:
    """A new read must declare whether the route check is live where it sits.

    Equality against the whole population rather than a count, so it fails in
    both directions and names what it found. A call added with a flattened path
    and no upstream guard is the #288 defect re-entering, and it looks exactly
    like a correct read until someone plants a link.
    """
    found = _read_source_file_calls()
    declared = Counter((site.module, site.function) for site in _CALL_SITES)

    assert found == declared, (
        "the `read_source_file` call sites in the shipped package no longer match the "
        f"table in this file.\n  found:    {sorted(found.items())}\n"
        f"  declared: {sorted(declared.items())}\n\n"
        "A new call needs a `_CallSite` row saying whether it passes the components "
        "its author wrote (REQUESTED) or a path `resolve()` already flattened "
        "(FLATTENED) -- and, if flattened, which symbol's "
        "`assert_no_symlink_escape` bounds it."
    )


def test_every_flattened_site_names_an_upstream_guard_that_exists() -> None:
    """A ``FLATTENED`` row's whole content is the symbol it defers to.

    Without this the category is a label: a row could name a guard that was
    deleted, or was never there, and read as though the site were covered. The
    named symbol is resolved in the shipped package and its body checked for a
    real ``assert_no_symlink_escape`` call, which is the same resolvability step
    ``controls_discharge`` applies to a threat-model citation.
    """
    for site in _CALL_SITES:
        if site.form == REQUESTED:
            assert not site.guarded_at, f"{site.function} is REQUESTED and needs no upstream"
            continue
        assert site.form == FLATTENED, f"{site.function} has an unknown form {site.form!r}"
        assert site.guarded_at, f"{site.function} is FLATTENED and must name its upstream"
        assert _calls_the_route_check(site.guarded_at), (
            f"{site.function} defers to {site.guarded_at}, which does not call "
            "assert_no_symlink_escape in the shipped source"
        )

    assert any(site.form == FLATTENED for site in _CALL_SITES), (
        "the positive control: with no FLATTENED row this test asserts nothing"
    )


def _calls_the_route_check(symbol: str) -> bool:
    """Whether ``symbol``'s body contains an ``assert_no_symlink_escape`` call.

    ``symbol`` is either a dotted path into the package or a bare
    ``Class.method`` inside ``application/proposal_service.py``; both forms
    appear in the table because both read naturally at their own row.
    """
    tail = symbol.rsplit(".", 1)[-1]
    for path in sorted(_PACKAGE.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node, scope in _scoped(tree, ()):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)):
                continue
            if node.func.id == "assert_no_symlink_escape" and scope and scope[-1] == tail:
                return True
    return False


#: A `resolve_within_root` call immediately paired with `assert_no_symlink_escape`
#: over the same reference gets `read_source_file`'s own two-part guarantee.
PAIRED = "paired-with-route-check"

#: No such pairing at this call; its own site names why the destination alone
#: is enough there.
BARE = "destination-only-with-its-own-reason"


class _ResolveWithinRootCallSite(NamedTuple):
    """One ``resolve_within_root`` call, and what covers its route."""

    module: str
    function: str
    form: str
    why: str


#: Every `resolve_within_root` call in the shipped package, `read_source_file`'s
#: own internal one included.
_RESOLVE_WITHIN_ROOT_CALL_SITES: Final = (
    _ResolveWithinRootCallSite(
        module="security/paths.py",
        function="read_source_file",
        form=PAIRED,
        why=(
            "assert_no_symlink_escape(root, base=root, requested=relative) runs "
            "immediately after, over the same `relative` -- the pairing every "
            "REQUESTED row in the table above relies on"
        ),
    ),
    _ResolveWithinRootCallSite(
        module="cli/migration_pipeline.py",
        function="_write",
        form=BARE,
        why=(
            "the write lands at this call's own answer, so a symlink planted "
            "inside the scratch copy by an earlier write in the same rehearsal "
            "is refused if it points outside the copy and irrelevant if it "
            "points inside -- the copy is a fresh tempfile directory this "
            "process owns, not a tree anything else reads"
        ),
    ),
    _ResolveWithinRootCallSite(
        module="application/proposal_service.py",
        function="ProposalService._destination_of",
        form=PAIRED,
        why=(
            "assert_no_symlink_escape(self._paths.knowledge, "
            "base=self._paths.migrations, requested=content_file) runs "
            "immediately after, over the author's own unflattened contentFile "
            "-- the guard the FLATTENED rows above (_reads_identical_bytes, "
            "_commit) both defer to"
        ),
    ),
    _ResolveWithinRootCallSite(
        module="infrastructure/review_evidence/store.py",
        function="ReviewEvidenceStore._write_one",
        form=PAIRED,
        why=(
            "assert_no_symlink_escape(self._root, base=self._root, "
            "requested=PurePosixPath(relative)) runs on the very next line, "
            "over the same `relative`"
        ),
    ),
    _ResolveWithinRootCallSite(
        module="infrastructure/review_evidence/store.py",
        function="ReviewEvidenceStore._refuse_a_relocated_directory",
        form=BARE,
        why=(
            "not a containment check -- containment and the route are already "
            "proved by `_write_one`'s own paired call, earlier in the same "
            "write. This one compares the resolved parent against a plain "
            "join to detect *any* link on the way, escaping or not (issue "
            "#577's cheap half)"
        ),
    ),
    _ResolveWithinRootCallSite(
        module="infrastructure/filesystem/migration_loader.py",
        function="_parse_upsert",
        form=PAIRED,
        why=(
            "assert_no_symlink_escape(project_root, base=migrations_dir, "
            "requested=content_file) runs on the very next line, over the "
            "author's own unflattened contentFile -- the same function the "
            "read_source_file table above already names FLATTENED for the "
            "same reason"
        ),
    ),
)


def _resolve_within_root_calls() -> Counter[tuple[str, str]]:
    """``(module path, enclosing function)`` for every ``resolve_within_root`` call."""
    calls: Counter[tuple[str, str]] = Counter()
    for path in sorted(_PACKAGE.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node, scope in _scoped(tree, ()):
            if not isinstance(node, ast.Call):
                continue
            function = node.func
            name = function.id if isinstance(function, ast.Name) else None
            if name == "resolve_within_root":
                calls[(path.relative_to(_PACKAGE).as_posix(), ".".join(scope) or "<module>")] += 1
    return calls


def test_every_resolve_within_root_call_is_one_this_table_categorises() -> None:
    """MEDIUM-2: this walker used to key on `read_source_file` alone, so a bare
    `resolve_within_root` call could grow anywhere and never appear here --
    exactly how `okf_import.py::_resolve_body`'s anchor derivation did (HIGH-2).
    """
    found = _resolve_within_root_calls()
    declared = Counter((site.module, site.function) for site in _RESOLVE_WITHIN_ROOT_CALL_SITES)

    assert found == declared, (
        "the `resolve_within_root` call sites in the shipped package no longer "
        f"match the table in this file.\n  found:    {sorted(found.items())}\n"
        f"  declared: {sorted(declared.items())}\n\n"
        "A new call needs a `_ResolveWithinRootCallSite` row saying whether an "
        "`assert_no_symlink_escape` call over the same reference covers its "
        "route (PAIRED) or not (BARE, with its own reason)."
    )


def test_every_resolve_within_root_site_is_paired_or_names_its_own_reason() -> None:
    """A PAIRED row's claim is checked the same way a FLATTENED `read_source_file`
    row's upstream guard is: resolved in the shipped source, not trusted from the
    label. A BARE row's whole content is its `why`.
    """
    for site in _RESOLVE_WITHIN_ROOT_CALL_SITES:
        if site.form == PAIRED:
            assert _calls_the_route_check(site.function), (
                f"{site.function} is PAIRED but calls no assert_no_symlink_escape"
            )
            continue
        assert site.form == BARE, f"{site.function} has an unknown form {site.form!r}"
        assert site.why, f"{site.function} is BARE and must name its own reason"

    assert any(site.form == BARE for site in _RESOLVE_WITHIN_ROOT_CALL_SITES), (
        "the positive control: with no BARE row this test asserts nothing"
    )
