"""Load migration files from disk into the domain model (ADR-0005).

Loading is where untrusted input enters the system. A migration file is written
by whoever can commit to the repository, and it names arbitrary paths. Every
check that keeps that safe lives here.
"""

from __future__ import annotations

import errno
import json
import reprlib
import stat
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from functools import lru_cache
from itertools import chain
from pathlib import Path, PurePosixPath
from typing import Any, Final

import yaml
from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError, ValidationError
from referencing.exceptions import Unresolvable
from referencing.jsonschema import EMPTY_REGISTRY

from theurian.domain.enums import (
    KnowledgeKind,
    KnowledgeStatus,
    RelationType,
    Sensitivity,
    SpecificationStatus,
    TrustLevel,
)
from theurian.domain.errors import (
    EscapeRole,
    EscapeSite,
    MigrationContentUnreadableError,
    MigrationError,
    MigrationFileUnreadableError,
    MigrationsDirectoryUnreadableError,
    PathDepthExceededError,
    PathEscapeError,
    SchemaUnreadableError,
)
from theurian.domain.identifiers import ItemId, MigrationId, RevisionId, SpecId
from theurian.domain.knowledge import SourceAnchor
from theurian.domain.migration import (
    DEFAULT_SENSITIVITY,
    DEFAULT_TRUST_LEVEL,
    MIGRATION_API_VERSION,
    AddAlias,
    AddEvidence,
    AddRelation,
    ChangeOwner,
    ChangeSensitivity,
    CreateItem,
    DeprecateItem,
    LoadedMigrations,
    Migration,
    MigrationSet,
    Operation,
    RegisterSpecification,
    RemoveAlias,
    RemoveEvidence,
    RemoveRelation,
    RestoreItem,
    RevisionMetadataSpec,
    SupersedeSpecification,
    UpsertRevision,
)
from theurian.domain.values import ContentHash, MediaType
from theurian.security.paths import read_source_file, resolve_within_root
from theurian.security.yaml_loading import load_yaml_mapping

#: Ceiling on migration files *loaded* per project. Not a design limit -- it
#: bounds how many `Migration` objects a single load can produce, not the
#: directory walk that finds them: the check below runs *after* enumeration
#: -- the `iterdir()`/per-entry classification loop below, costlier than the
#: `glob("*.yaml")` it replaced (see that loop's own comment for why `glob`
#: is not used here) -- has already completed. Re-measured 2026-08-17 at
#: HEAD (round four), 5-run minimum, APFS/CPython 3.13.11: 10,000 `.yaml`
#: files, `glob` 13.8-14.5 ms vs classify 114.0-125.9 ms (7.9-9.1x); 1,000
#: `.yaml` mixed with 9,000 non-matching entries, 6.6-7.3 ms vs 35.6-37.2 ms
#: (4.9-5.6x). Both `glob` figures are `sorted(glob(...))`, paying the same
#: sort this loop's own `sorted(...)` pays, so the two sides are compared
#: apples to apples. The earlier bcdec22 measurement this comment carried
#: (1.27x, 2.71x) understated both: that commit's loop classified with a bare
#: `is_file()`, before `_entry_is_migration_file`'s own unguarded
#: `is_symlink()` lstat -- one extra syscall per entry -- existed to widen
#: the gap. Ratios vary by machine and directory shape; the direction --
#: costlier, and the multiple is largest when most entries match, since the
#: `endswith(".yaml")` name filter runs before classification and thins the
#: classified set first the larger the non-matching fraction is -- is what
#: this comment is pinning, not the exact multiples. A pathological or
#: generated directory still pays for the full walk before this refuses to
#: load what it found; it does not bound the walk's own cost.
MAX_MIGRATIONS: Final = 10_000

#: Ceiling on how deeply a migration *document* may nest, counting the root
#: mapping as level 1. Enforced by `_refuse_a_document_that_nests_too_deep`
#: before a document is validated, because past the interpreter's C recursion
#: budget `jsonschema` cannot even build its own refusal message and the
#: `RecursionError` that follows is indistinguishable from a broken schema
#: (issue #291; the measurements are on :func:`_validate_document`).
#:
#: A schema-valid document nests at most **7** levels including the root --
#: measured 2026-08-21 by walking the bundled schema's structural keywords
#: (`properties`, `items`, `oneOf`, `$ref` into `$defs`) for the deepest
#: instance path it permits -- so 64 refuses nothing an author can legitimately
#: write. It is not, however, "two orders of magnitude below the rendering
#: budget": that budget moves with the ambient call stack. From a shallow stack
#: `repr` renders ~9,997 levels, but with ~2,400 C-frames already spent the
#: mistranslation onset was measured near depth 6,000 -- and 64x100 is 6,400,
#: *above* that. 64 is chosen to sit far below even the ambient-reduced budget,
#: which is what the guard needs; the earlier "two orders below ~8,000" framing
#: compared against the shallow-stack budget only and did not hold once the
#: ambient stack was charged against the same ceiling.
MAX_DOCUMENT_NESTING: Final = 64

#: Ceiling on how many nodes a migration *document* may hold, counting every
#: value the walk reaches -- keys, mapping values and sequence elements alike --
#: *without* collapsing shared references. Enforced by
#: `_refuse_a_document_that_nests_too_deep` alongside `MAX_DOCUMENT_NESTING`,
#: and it is the node count, not the nesting depth, that closes the
#: alias-expansion denial of service (issue #291's guard was itself one; same
#: shape as #245's un-memoised `$ref` walk).
#:
#: A YAML anchor referenced from two aliases, each referenced from two more, is
#: a ~500-byte file whose *expanded* node count is 2**N -- 24 levels reach 100
#: million nodes. The walk is deliberately *not* memoised on object identity: a
#: collapsed walk would count that file as its ~24 distinct nodes and wave it
#: through to `validate`, and `jsonschema` does not collapse it -- `validate`
#: interpolates the failing instance with `{instance!r}`, and that repr
#: re-expands every shared reference, building a 46 MB message for a 500-byte
#: file (measured 2026-08-21, jsonschema 4.26.0, alias level 22) *before*
#: :func:`_schema_rejection` is ever reached. Counting the expanded nodes and
#: refusing here, ahead of `validate`, is what keeps that repr bounded;
#: memoising the walk would only move the cost into a dependency this module
#: cannot bound.
#:
#: Generous by three orders of magnitude, on purpose. This repository's own 26
#: committed migrations walk to 67-73 nodes each (measured 2026-08-21 over
#: `.theurian/migrations`). The bound refuses nothing an author legitimately
#: writes while capping the guard's own walk -- and every repr `validate`
#: performs on a document that passes it -- at this many steps.
MAX_DOCUMENT_NODES: Final = 100_000

#: Ceiling on how much of an author-written value a schema rejection may echo,
#: in characters. Applied by `_bounded` to every variable-length fragment
#: `_schema_rejection` assembles (issue #289).
#:
#: Sized to show a *real* rejected operation whole and to bite only on a value
#: written to be large: the 52 operations in this repository's own committed
#: migrations render at 145-937 characters, median 501 (measured 2026-08-21 over
#: `.theurian/migrations`). What the bound removes is the unbounded case behind
#: the issue -- a 100 KB value rendering a 100,198-character refusal into
#: someone's terminal.
MAX_ECHOED_VALUE: Final = 1_000

#: Ceiling on the *schema-side* expectation a rejection names (the ``const`` a
#: value must equal, the ``pattern`` it must match, the ``type`` it must be),
#: in characters. Applied by `_schema_rejection` through :func:`_bounded`
#: (issue #289).
#:
#: A real constraint is tiny -- measured 2026-08-21 against the bundled schema:
#: ``const`` 17 characters, ``pattern`` 31, ``type`` 7, ``minItems`` 1. The one
#: keyword whose expectation is large is ``oneOf``, whose ``validator_value`` is
#: the list of subschemas an operation must match one of (524 characters). Kept
#: well under :data:`MAX_ECHOED_VALUE` so that expectation and echoed value
#: together stay bounded however the schema grows: for ``oneOf`` the echoed
#: value and the location already localize the fault, so the schema dump is a
#: hint, not the diagnosis, and is truncated to that role.
MAX_ECHOED_EXPECTATION: Final = 120

_SCHEMA_RELATIVE: Final = "migrations/migration.schema.json"


@lru_cache(maxsize=1)
def _validator(schema_root: Path) -> Draft202012Validator:
    """Build the migration-schema validator, translating a corrupted install.

    Reads the *installed package's* schema, never a path under a user's
    project -- `schema_root()` (`cli/context.py`) already raises
    `ProjectError` when neither candidate location exists at all, checked with
    `.exists()` before either is returned here. What that leaves is "a
    location was found, but touching it failed", and that covers four
    different kinds of failure, all translated here to `SchemaUnreadableError`
    rather than a `MigrationError` -- keeping install-integrity failures in
    the *type* instead of in whether the failure is caught at all:

    1. **The read itself fails.** A permission problem on site-packages or a
       symlink loop raises `OSError`; non-UTF-8 bytes raise `UnicodeDecodeError`
       at the same `read_text(encoding="utf-8")` call. Originally only the
       first was guarded, on the reasoning that install-integrity is not
       user-project state -- true, but beside the CP-2 point: an unguarded
       read still crashes every `--json` command that reaches
       `resolve_context` (issue #205's Class 1).
    2. **The read succeeds, the content is corrupt (round two).** Truncated or
       empty JSON raises `json.JSONDecodeError` -- itself a `ValueError`
       subclass, measured with both an unterminated string and a zero-byte
       file, two distinct messages from the same type.
    3. **The read succeeds, the JSON is well-formed but unusable as a schema
       (round three).** A document that is not a JSON *object* -- a list, or a
       bare `true`/`false`, both permitted by JSON Schema itself as a
       top-level schema -- is refused explicitly with `isinstance(schema,
       dict)` before `Draft202012Validator` ever sees it. This is not because
       either would let every migration validate: `{}` is an equally
       accept-everything schema and this build keeps it (see the residual
       paragraph below). It is because this build treats only a JSON *object*
       as usable schema material at all -- a list or a bare boolean is a
       different, install-shaped kind of corruption than a permissive but
       well-typed schema, and the two are refused for that reason, not for
       being permissive. A document that *is* an object but whose own
       keywords are structurally malformed -- `required` must be an array of
       strings, and a bare string passed `check_schema`'s check silently
       until this round -- is caught by an explicit
       `Draft202012Validator.check_schema(schema)` call, translating
       `jsonschema.exceptions.SchemaError` before any migration is ever
       checked against it; before this, that failure surfaced only when a
       schema-valid *migration* tripped over the schema's own defect, blaming
       the wrong document entirely (`Draft202012Validator({"required":
       "not-a-list"}).validate(...)` raises `'n' is a required property`,
       misattributed to whichever migration validated first).
    4. **The schema, or the JSON encoding it, nests past Python's recursion
       limit (round four).** `json.loads` and `Draft202012Validator.
       check_schema` both recurse into nested structure -- a document, or a
       schema's own nested keywords, deep enough exhausts the interpreter
       stack the identical way an attacker-controlled *migration* document
       already does (`security/yaml_loading.py`'s `RecursionError` ->
       `ValueError` translation, and `parsers/structured.py`'s `JsonParser`).
       `check_schema`'s own recursion is a regression this round's own call
       introduced: it did not exist before item 3 above added that call, and
       is measured directly at 400 levels of nested `not` keywords. Neither
       call's `RecursionError` is caught by any `except` clause above it, so
       it used to escape `_validator` raw -- crashing every `--json` command
       that resolves a project, the identical CP-2 escape every other member
       of this class was closed for.

    `{}` remains accepted -- a valid, if vacuous, JSON Schema that matches
    every instance, and deliberately not a third refusal alongside item 3's
    two (`test_validator_accepts_the_vacuous_empty_object_schema`,
    `CHANGELOG.md`'s round-three entry). It is the residual this build lives
    with rather than the reason `true`/`false` are refused: the type check
    above is about what shape of document this build treats as a schema, and
    `{}` already satisfies that shape.

    **The registry pins reference resolution offline (issue #235).** The
    validator is built with `referencing`'s `EMPTY_REGISTRY` -- a registry
    with no `retrieve` callable -- so an external `$ref` (`http(s)://`,
    `file://`, or any other URI) *fails closed* instead of taking
    `jsonschema`'s default path of fetching it at validate time
    (`_warn_for_remote_retrieve`, `urllib.request.urlopen`). That default is
    an SSRF-shaped network read (and a local-file read for `file://`) gated
    only on this installed schema being corrupted or replaced -- a seam no
    existing claim covered: `parsers/openapi.py`'s "external `$ref` targets
    are recorded, never fetched" governs *ingested* documents, not the schema
    this build ships. Internal `#/$defs/...` refs are unaffected: they resolve
    against the schema's own root resource, which the registry always holds,
    so the bundled schema still validates exactly as before.

    **The resolution failure surfaces at validate time, not here.** A `$ref`
    is a plain string to `check_schema`, which validates the schema document
    against the metaschema without ever following a reference -- so an
    unresolvable or self-recursive `$ref` cannot be caught at build time by
    this function. The fail-closed lookup, a dangling `#/$defs/...` fragment,
    and a `$dynamicRef` to nowhere all raise `referencing.exceptions.
    Unresolvable` when a document is validated; an empty or self-recursive
    `$ref` raises `RecursionError` there. Neither is a `ValidationError`, so
    both used to slip past every `except ValidationError` seam as a raw
    traceback -- the same CP-2 escape item 4 closed one layer up. They are
    translated to `SchemaUnreadableError` at the validate call itself, by
    :func:`_validate_document`, which both validate seams route through: it is
    the installed schema that is broken, not the migration being checked.

    A JSON list used to reach `Draft202012Validator` construction and raise
    `AttributeError` there instead -- `jsonschema` calls `schema.get(...)`
    internally, and a `list` has no `.get` -- which this file's own `except
    AttributeError` used to translate. The `isinstance` check above now
    refuses every non-dict schema before that call runs at all, and every
    dict that reaches construction has already passed `check_schema`, so no
    path here can raise `AttributeError` any more: removed rather than kept
    as a defensive clause nothing can drive (measured against `jsonschema`
    4.26.0's own `Draft202012Validator.__init__`, which does not itself call
    anything that could raise it for a schema-conformant dict).
    """
    schema_path = schema_root / _SCHEMA_RELATIVE
    try:
        text = schema_path.read_text(encoding="utf-8")
        schema = json.loads(text)
    except OSError as exc:
        raise SchemaUnreadableError(str(schema_path), exc.strerror or str(exc)) from exc
    except UnicodeDecodeError as exc:
        raise SchemaUnreadableError(str(schema_path), str(exc)) from exc
    except json.JSONDecodeError as exc:
        raise SchemaUnreadableError(str(schema_path), str(exc)) from exc
    except RecursionError as exc:
        reason = "the JSON document nests past the parser's safe recursion depth"
        raise SchemaUnreadableError(str(schema_path), reason) from exc

    if not isinstance(schema, dict):
        reason = f"parsed to a {type(schema).__name__}, not an object"
        raise SchemaUnreadableError(str(schema_path), reason)
    try:
        Draft202012Validator.check_schema(schema)
    except SchemaError as exc:
        raise SchemaUnreadableError(str(schema_path), str(exc)) from exc
    except RecursionError as exc:
        reason = "the schema nests past check_schema's safe recursion depth"
        raise SchemaUnreadableError(str(schema_path), reason) from exc
    # `EMPTY_REGISTRY` has no `retrieve`, so an external `$ref` fails closed
    # rather than being fetched over the network at validate time (issue #235).
    return Draft202012Validator(schema, registry=EMPTY_REGISTRY)


@dataclass(frozen=True, slots=True)
class _SchemaValidator:
    """A validator paired with the path of the installed schema it was built
    from, so a validate-time `$ref` failure can name that schema (issue #235).

    Pairing them also keeps :func:`_load_one` to one validation argument rather
    than a validator and a path travelling separately down the load path.
    """

    validator: Draft202012Validator
    schema_path: Path


def _refuse_a_document_that_nests_too_deep(
    document: Mapping[str, object], document_name: str | None
) -> None:
    """Refuse a document that nests past :data:`MAX_DOCUMENT_NESTING` or holds
    more than :data:`MAX_DOCUMENT_NODES` nodes (issues #291, #245).

    **Iterative on purpose.** A recursive depth checker spends the very budget
    it exists to protect, so it would raise `RecursionError` on exactly the
    documents it is meant to refuse -- moving the fault rather than closing it.
    The frontier holds ``(value, depth)`` pairs, so the only stack this uses is
    the heap.

    **Un-collapsed on purpose.** The walk does not memoise the nodes it has
    seen. A YAML anchor referenced from two aliases, each referenced from two
    more, expands to 2**N nodes from an N-line file -- and `jsonschema`'s own
    ``{instance!r}`` message interpolation re-expands it the identical way,
    building a multi-megabyte message from a sub-kilobyte file *before*
    :func:`_schema_rejection` is ever reached. Collapsing the walk on object
    identity would count that file as its handful of distinct nodes and wave it
    through to ``validate``, moving the cost into a dependency this module
    cannot bound. So the walk counts every reference and refuses at
    :data:`MAX_DOCUMENT_NODES` -- which bounds both this walk's own cost and
    every repr ``validate`` performs on a document that passes it. The node
    count is checked as each child is *discovered*, so the frontier itself never
    grows past the cap even for a single very wide node.

    Both checks run before ``validate`` rather than as a wider ``except`` around
    it, because the two sources of a validate-time ``RecursionError`` cannot be
    told apart once ``validate`` is running -- see :func:`_validate_document`,
    whose schema attribution this function is what makes sound.

    Mappings and sequences are the containers a *parsed* document holds:
    ``load_yaml``'s ``_StrictLoader`` produces ``dict``/``list``/scalars (its
    timestamp resolver is dropped, so even dates arrive as ``str``), and the
    ``propose`` seam builds ``dict[str, object]`` with string keys and
    list/scalar values (`_migration_document`, ``application/proposal_service.py``).
    Neither seam produces a ``set``, a ``frozenset``, a ``tuple`` or a
    non-string key. Those are walked anyway -- ``set``/``frozenset``/``tuple``
    as containers, and mapping *keys* as well as values -- purely as defense for
    an in-memory caller of :func:`validate_migration_document` that hands in
    something those seams never build: a container sitting in a key, or inside a
    set, would otherwise slip past this bound and reach ``validate``, where a
    deep one is mistranslated as a corrupt schema. ``str`` and ``bytes`` stay
    leaves: their elements are characters and integers, not nesting, and
    descending into them would make this pass cost the length of the text.
    """
    frontier: list[tuple[object, int]] = [(document, 1)]
    discovered = 1
    while frontier:
        value, depth = frontier.pop()
        if depth > MAX_DOCUMENT_NESTING:
            # No part of the document is echoed: at this depth its rendering is
            # exactly what cannot be produced safely, and the cure is the shape
            # of the document rather than any one value in it.
            subject = f"{document_name} nests" if document_name else "This document nests"
            raise MigrationError(
                f"{subject} more than {MAX_DOCUMENT_NESTING} levels deep. Flatten it: a "
                f"migration is a fixed, shallow shape, and nothing the schema accepts "
                f"nests past 7 levels."
            )
        if isinstance(value, Mapping):
            children: Iterable[object] = chain(value.keys(), value.values())
        elif isinstance(value, list | tuple | set | frozenset):
            children = value
        else:
            continue
        for child in children:
            discovered += 1
            if discovered > MAX_DOCUMENT_NODES:
                # Bounded before the frontier is: a single node with more
                # children than the whole budget is refused as it is read, not
                # after it has been fully expanded into memory.
                subject = f"{document_name} holds" if document_name else "This document holds"
                raise MigrationError(
                    f"{subject} more than {MAX_DOCUMENT_NODES} values. A migration is a "
                    f"fixed, shallow shape; a document this large is a mistake or an attempt "
                    f"to exhaust memory. Reduce it, or split it into separate migration files."
                )
            frontier.append((child, depth + 1))


def _validate_document(
    schema_validator: _SchemaValidator,
    document: Mapping[str, object],
    *,
    document_name: str | None = None,
) -> None:
    """Run one document through the validator, translating a failed offline
    `$ref` resolution -- and a validate-time recursion, which by then can only
    be the schema's (see below) -- to `SchemaUnreadableError` (issue #235).

    `_validator` builds every validator with a `referencing` registry that has
    no network or file retrieval (see its docstring), so an external `$ref`
    fails closed rather than being fetched. That fail-closed lookup -- and a
    dangling `#/$defs/...` fragment or a `$dynamicRef` to nowhere -- raises
    `referencing.exceptions.Unresolvable` (`jsonschema` wraps it as its
    internal `_WrappedReferencingError`, which is itself an `Unresolvable`). A
    self-recursive or empty schema `$ref` (`"#"`, `""`) raises `RecursionError`
    at the same call. Neither is a `ValidationError`, so both escaped every
    `except ValidationError` seam and reached `resolve_context` as a raw
    traceback under `--json`.

    An `Unresolvable` is unambiguously a schema defect. A `RecursionError` was
    only ever *attributed* to one, and until issue #291 that attribution was
    unsound: a deeply nested **document** raised the identical `RecursionError`
    from `validator.validate(document)`, and a user-input fault was answered
    "reinstall theurian". Measured on 2026-08-21, CPython 3.13.3, `jsonschema`
    4.26.0, against a document carrying a ``{"a": {"a": ...}}`` chain inside
    its single operation:

    * The recursion is raised while `jsonschema` builds a *message*, not while
      it walks the schema -- `_keywords.py`'s `oneOf` interpolating
      `f"{instance!r} is not valid under any of the given schemas"`, and the
      exception says so verbatim: "maximum recursion depth exceeded while
      getting the repr of an object". It is instance-driven, so no amount of
      schema inspection at build time detects it.
    * The budget is CPython's C recursion limit, not `sys.getrecursionlimit()`:
      `repr` of a 9,000-level chain renders identically at Python limits of
      100, 1,000, 10,000 and 100,000, and the deepest chain that renders at all
      from a shallow stack is 9,997.
    * That budget is shared with the ambient call stack, so **the same document
      got different answers depending on who called**: depth 8,000 answered
      `MigrationError` from a direct call and `SchemaUnreadableError` from a
      stack 1,200 C-frames deep. Depths 2,000 through 7,000 were answered
      correctly at every ambient depth tried; 10,000 and 20,000 were
      mistranslated at all of them.

    :func:`_refuse_a_document_that_nests_too_deep` closes that gap by bounding
    the document at :data:`MAX_DOCUMENT_NESTING` before `validate` is reached,
    and at :data:`MAX_DOCUMENT_NODES` so that a shallow but alias-expanded
    document cannot make `validate` itself do unbounded work. The attribution
    below is a deduction only under the premise those two bounds establish: the
    document is shallow (past `MAX_DOCUMENT_NESTING` it was refused, not
    validated) *and* the ambient call stack the guard ran on is not itself near
    the C-recursion budget. `MAX_DOCUMENT_NESTING` is 64 and the shallow-stack
    budget is ~9,997, so an ordinary caller leaves ample headroom; a caller that
    had already spent most of the budget before reaching here could in principle
    make even a 64-level document recurse, which is why the premise is stated
    rather than claimed unconditionally. Within it, a `RecursionError` from a
    proven-shallow instance is the schema's, and the `SchemaUnreadableError`
    this raises is the honest answer. Both failures this function attributes to
    the schema carry that type, as every other install-corruption failure
    `_validator` translates does.

    A `ValidationError` is a real fault in the *document* and is deliberately
    left to propagate: the two callers word it differently (with or without a
    file name), so each wraps it into its own `MigrationError`. Only the
    schema-integrity failures, identical from both seams, are handled here.
    `document_name` exists for the same split -- the depth refusal above is
    raised here rather than by a caller, so it is told the file name the
    loading seam would otherwise have added itself.
    """
    _refuse_a_document_that_nests_too_deep(document, document_name)
    try:
        schema_validator.validator.validate(document)
    except Unresolvable as exc:
        # `str(exc)` echoes the offending ref -- safe here, it is the installed
        # schema's own content, not attacker-supplied user-project data (SEC-7).
        raise SchemaUnreadableError(
            str(schema_validator.schema_path),
            f"a schema $ref could not be resolved offline: {exc}",
        ) from exc
    except RecursionError as exc:
        reason = "a schema $ref resolves recursively without terminating"
        raise SchemaUnreadableError(str(schema_validator.schema_path), reason) from exc


def _bounded(rendered: str, limit: int = MAX_ECHOED_VALUE) -> str:
    """``rendered``, cut to ``limit`` (:data:`MAX_ECHOED_VALUE` by default),
    saying what was cut.

    The full length is named because for the case this bound exists for -- a
    value that is refused *for being large* -- the size is the diagnosis, and a
    reader who is only shown a prefix cannot tell 300 characters from 300,000.

    ``limit`` is a parameter, not a second constant, because the schema-side
    expectation :func:`_schema_rejection` names is bounded tighter than the
    author-written value it echoes (:data:`MAX_ECHOED_EXPECTATION`): a real
    constraint is a handful of characters, and the one structural keyword whose
    expectation is large is a hint rather than the diagnosis there.
    """
    if len(rendered) <= limit:
        return rendered
    return f"{rendered[:limit]}... ({len(rendered)} characters in all)"


#: A bounded renderer for the one author-written value a schema rejection
#: echoes. `reprlib.Repr` truncates *while* rendering -- it stops at ``maxlevel``
#: and at each container's element budget -- so a shared-reference (alias) graph
#: that plain ``repr`` would re-expand into millions of nodes, or a chain too
#: deep for ``repr`` to render without a `RecursionError`, is rendered in
#: bounded time and depth. The guard above refuses either shape before a
#: document reaches `validate`, so this is defense in depth for a value that
#: reaches :func:`_schema_rejection` some other way (issues #289, #291).
_ECHO: Final = reprlib.Repr(
    maxlevel=8,
    maxtuple=12,
    maxlist=12,
    maxdict=12,
    maxset=12,
    maxfrozenset=12,
    maxstring=MAX_ECHOED_VALUE,
    maxother=MAX_ECHOED_VALUE,
)


def _echo(instance: object) -> str:
    """``instance`` rendered for a schema-rejection message, bounded twice over.

    `reprlib` bounds the *work* -- it never recurses past ``maxlevel`` or renders
    past each container's element budget, so an alias graph or a chain deep
    enough to defeat plain ``repr`` is rendered in bounded time and depth, and
    the control characters `repr` escapes stay escaped. `_bounded` then bounds
    the *length* of what is left, so the total is capped at
    :data:`MAX_ECHOED_VALUE` however the per-element budgets happen to sum.
    """
    return _bounded(_ECHO.repr(instance))


def _missing_required_properties(exc: ValidationError) -> list[str]:
    """The ``required`` names the failing instance does not carry.

    Every name returned comes from the *schema's* ``required`` array, never
    from the document -- which is why :func:`_schema_rejection` may print them
    in full while it bounds everything the author wrote. The two ``isinstance``
    guards are what keep that true: without them this would be reading whatever
    a replaced schema put there, and the list is a plain ``Any`` in the stubs.

    Empty when the keyword is not ``required``, when the instance is not a
    mapping, or when nothing is missing -- each of which sends the caller back
    to its generic wording rather than to a sentence that would be false.

    The keyword check is not redundant with the type checks below it. Other
    keywords carry a list of strings too: an array-valued ``type``, or an
    ``enum``, against a mapping instance would otherwise have every name it
    lists reported as a *missing property*, which is not what either keyword
    means.
    """
    required = exc.validator_value
    instance = exc.instance
    if exc.validator != "required":
        return []
    if not isinstance(required, list) or not isinstance(instance, Mapping):
        return []
    return [name for name in required if isinstance(name, str) and name not in instance]


def _unexpected_properties(exc: ValidationError) -> list[object]:
    """The instance keys an ``additionalProperties: false`` rejection refused.

    These are the offending names `jsonschema` itself reported -- the ones the
    generic echo pushed off the end of the instance and truncated away on every
    real migration. Which keys are *unexpected* is fixed by the schema (the ones
    not in its ``properties``), so naming them is as author-safe as naming the
    missing ``required`` names; the names themselves are author-written, so
    :func:`_schema_rejection` still bounds them.

    Empty unless the keyword is ``additionalProperties`` refusing outright
    (``validator_value`` is ``False``), the instance is a mapping, and the
    failing schema is an object listing its allowed ``properties``. The bundled
    schema has no ``patternProperties`` anywhere
    (``test_the_bundled_schema_never_descends_into_an_author_written_key``), so
    "not in ``properties``" is exactly `jsonschema`'s own set; a schema that grew
    one would need this to consult it too.
    """
    if exc.validator != "additionalProperties" or exc.validator_value is not False:
        return []
    instance = exc.instance
    schema = exc.schema
    if not isinstance(instance, Mapping) or not isinstance(schema, Mapping):
        return []
    allowed = schema.get("properties", {})
    allowed_names = set(allowed) if isinstance(allowed, Mapping) else set()
    return [name for name in instance if name not in allowed_names]


def _escape_control(text: str) -> str:
    """``text`` with control characters (and non-ASCII) escaped, ASCII kept.

    A schema property name -- all a location segment is today -- is unchanged, so
    the common location reads as itself; a segment a future schema let descend
    into an author-written key would arrive escaped, never raw in a terminal.
    """
    return text.encode("unicode_escape").decode("ascii")


def _location(path: Iterable[object]) -> str:
    """Where in the document a rejection fired, one bounded, escaped segment each.

    Each string segment goes through the length bound and control-character
    escaping the echoed value does. The bundled schema never descends into an
    author-written key -- it is ``additionalProperties: false`` throughout with
    no ``patternProperties`` (``test_the_bundled_schema_never_descends_into_an_
    author_written_key``) -- so a segment is a schema property name today; the
    bound is what keeps this fragment from reopening into an unbounded, unescaped
    echo if that ever changes. Array indices are schema-derived integers,
    rendered as-is.
    """
    segments = [
        _bounded(_escape_control(part)) if isinstance(part, str) else str(part) for part in path
    ]
    return "/".join(segments) or "<root>"


def _schema_rejection(exc: ValidationError) -> str:
    """Where a document failed the schema and why, worded here (issue #289).

    **One function for both seams**, because they are one message with two
    prefixes: `validate_migration_document` says "invalid migration at ..." and
    `_load_one` says "<file> is invalid at ...". A second hand-rolled builder
    would drift, and the property most likely to be lost in the drift is the
    one that leaves no trace when it goes -- the escaping below.

    `jsonschema` builds `ValidationError.message` by interpolating the failing
    instance with `{instance!r}`, and two properties of the refusal this seam
    hands a reader used to rest on that:

    * **Bounded.** It was not: a 100 KB author-written value rendered whole
      into a message a reader receives, measured at 100,198 characters. A
      migration file is written by whoever can commit to the repository, so
      that echo is a terminal's worth of output the author chose. Every
      variable-length fragment below goes through :func:`_bounded`.
    * **Escaped.** It was, and by accident: an ESC or a newline in an authored
      value arrived escaped only because a third-party internal happened to use
      `!r` everywhere, which no test of ours held. Every author-written
      fragment below is rendered with `repr` *here* -- the language's own
      escaping, called by this seam rather than inherited from a dependency.

    The **schema-side fact** the refusal carries is the third property, and the
    one an earlier version of this seam dropped. `jsonschema`'s own message named
    it -- the expected `const`, the `pattern`, the unexpected key -- and replacing
    that message with "keyword name + echo of the instance" lost it: on all 26 of
    this repository's committed migrations a single top-level typo produced
    "does not satisfy 'additionalProperties'; the value there is {...}" with the
    offending key truncated off the end of the instance, strictly worse diagnosis
    than the `Additional properties are not allowed ('dependsOnn' was unexpected)`
    it replaced. So the schema-side fact is put back:

    * `required` and `additionalProperties` are worded from the *schema*: the
      missing names (`_missing_required_properties`) and the unexpected ones
      (`_unexpected_properties`). Both are fixed by the schema, not chosen by the
      author, so both may be named in full while the names themselves are
      bounded. The earlier docstring's claim that `required` is "the one
      rejection whose cause appears nowhere in the instance" was false -- a
      `const`'s expected value and a `pattern` are nowhere in the instance
      either -- and its claim that `additionalProperties` "needs no branch, the
      echo shows the keys" was the exact defect above: the echo truncates them.
    * Every other keyword names its `validator_value` -- the `const`, the
      `pattern`, the `type`, the `minItems` the value had to satisfy -- through
      `_bounded` at :data:`MAX_ECHOED_EXPECTATION`. That value is schema-derived,
      so it is as safe to print as the `required` names; it is bounded tighter
      than the echoed value because a real constraint is a handful of characters
      and the one large one (`oneOf`'s subschema list) is a hint there, not the
      diagnosis.

    The **location** (`_location`) and the **echoed value** (`_echo`) carry the
    other two properties. `absolute_path` holds the schema property names
    descended through and array indices; the bundled schema is
    `additionalProperties: false` throughout with no `patternProperties`, so no
    author-invented key is ever descended into, and `_location` bounds and
    escapes each segment so a future schema that did could not reopen an
    unbounded, unescaped echo. `_echo` renders the failing instance with
    `reprlib` -- bounded in depth and length, control characters escaped -- so an
    ESC or newline an author wrote cannot forge a line of this seam's output, and
    an alias graph or a chain too deep for plain `repr` cannot cost unbounded
    work here.
    """
    location = _location(exc.absolute_path)
    missing = _missing_required_properties(exc)
    if missing:
        noun = "property" if len(missing) == 1 else "properties"
        names = _bounded(", ".join(repr(name) for name in missing))
        return f"{location}: missing the required {noun} {names}"
    unexpected = _unexpected_properties(exc)
    if unexpected:
        noun = "property" if len(unexpected) == 1 else "properties"
        names = _bounded(", ".join(repr(name) for name in unexpected))
        return f"{location}: has the unexpected {noun} {names}"
    echoed = _echo(exc.instance)
    if isinstance(exc.validator, str):
        expected = _bounded(repr(exc.validator_value), MAX_ECHOED_EXPECTATION)
        return (
            f"{location}: does not satisfy {exc.validator!r} (expected {expected}); "
            f"the value there is {echoed}"
        )
    # `Unset` when `jsonschema` raised without a keyword: no expectation to name,
    # and "the schema" rather than a sentinel's repr.
    return f"{location}: does not satisfy the schema; the value there is {echoed}"


def validate_migration_document(document: Mapping[str, object], schema_root: Path) -> None:
    """Check a migration *document* against the published schema, without a file.

    The loader's own check reads a path; this one takes the parsed mapping, so
    a generator can refuse to write a migration it has just built wrong rather
    than leaving one on disk for a reviewer to discover. ADR-0013 point 3 is the
    reason it belongs at generation: the gap between a proposal and approved
    knowledge is human review, not format conversion.

    Raises:
        MigrationError: If the document does not satisfy the schema, nests past
            :data:`MAX_DOCUMENT_NESTING`, or holds more than
            :data:`MAX_DOCUMENT_NODES` nodes once its shared references are
            expanded -- document faults that used to be reported as a corrupt
            installation, or to cost unbounded work in `jsonschema`'s own
            message building (issues #291, #245; the mechanism is recorded on
            :func:`_validate_document` and :data:`MAX_DOCUMENT_NODES`).
        SchemaUnreadableError: If the installed schema cannot be read, parses
            to something this build cannot use as a schema (both from
            :func:`_validator`), or names a `$ref` that cannot be resolved
            offline or resolves without terminating (from
            :func:`_validate_document`).
    """
    schema_validator = _SchemaValidator(_validator(schema_root), schema_root / _SCHEMA_RELATIVE)
    try:
        _validate_document(schema_validator, document)
    except ValidationError as exc:
        raise MigrationError(f"invalid migration at {_schema_rejection(exc)}") from exc


def load_migrations(
    project_root: Path, migrations_dir: Path, schema_root: Path
) -> LoadedMigrations:
    """Load, validate, and order every migration under ``migrations_dir``.

    Args:
        project_root: The containment boundary. No file outside it is read.
        migrations_dir: Directory holding ``*.yaml`` migration files.
        schema_root: The repository's ``schemas/`` directory.

    Raises:
        MigrationError: On a malformed, duplicate, cyclic, or unresolvable
            file, or one whose document nests past :data:`MAX_DOCUMENT_NESTING`
            or expands past :data:`MAX_DOCUMENT_NODES` nodes (issues #291, #245).
        PathEscapeError: If a ``contentFile`` points outside ``project_root``;
            if ``migrations_dir`` itself is a symlink that resolves outside
            ``project_root`` (round four; checked directly, at the probe --
            see :func:`_refuse_unusable_migrations_directory_symlink`); or if
            a migration file inside it is such a symlink, which still
            surfaces through ``_load_one``'s call to ``read_source_file``
            (``security/paths.py``): a migration *entry*'s path starts with
            `project_root` as a string regardless of where it resolves, so
            `read_source_file`'s own resolve-and-compare is what actually
            catches that one, one call site later -- the same mechanism the
            directory-level check no longer has to rely on. All three name a
            project-relative entry and carry their own remedy (issue #233).
            The :class:`~theurian.domain.errors.EscapeRole` each carries picks
            only the opening sentence: :func:`_escape_role_of` for the two
            path-shaped cases, and ``"referrer"`` for a ``contentFile``, where
            the migration file is named as the place to look and the
            author-written value stays unechoed. None of the three tells the
            reader which file to delete -- see
            :class:`~theurian.domain.errors.EscapeSite` for why that claim
            cannot be made here.
        InputTooLargeError: If a file exceeds its size limit.
        MigrationsDirectoryUnreadableError: If ``migrations_dir`` cannot be
            probed or listed for a reason other than genuinely not existing --
            a parent that denies traversal, the directory itself denying
            listing or per-entry stat, a symlink loop or dangling symlink at
            ``migrations_dir`` itself (round four), or any other raw
            ``OSError``.
        MigrationFileUnreadableError: If a migration file cannot be read once
            found -- or, for a ``*.yaml`` entry found during enumeration, if
            it is a symlink loop or resolves to nothing (round three; see
            :func:`_entry_is_migration_file`).
        MigrationContentUnreadableError: If an ``upsertRevision`` operation's
            ``contentFile`` cannot be resolved or read.
        SchemaUnreadableError: If the installed schema cannot be read, parses
            to something this build cannot use as a schema, or names a `$ref`
            that cannot be resolved offline or resolves without terminating
            (issue #235; translated at the validate seam by
            :func:`_validate_document`, which :func:`_load_one` routes through).
    """
    _refuse_unusable_migrations_directory_symlink(migrations_dir, project_root)

    try:
        # `os.stat` (`Path.stat()`, following symlinks like `os.stat` does) is
        # probed explicitly rather than `Path.is_dir()`, which internally
        # ignores only `ENOENT`/`ENOTDIR`/`EBADF`/`ELOOP` and reports `False`
        # for those four alike -- it re-raises everything else, `EACCES`
        # included (that re-raise is exactly why a *parent* of
        # `migrations_dir` denying traversal already escaped as a raw
        # `PermissionError` before issue #205's fix; see
        # `MigrationsDirectoryUnreadableError`'s docstring, `domain/errors.py`,
        # for that history). What `is_dir()`'s swallowing *did* conflate
        # (round two) is a directory that never existed with one hidden
        # behind a symlink chain longer than the platform's loop limit: both
        # used to answer "nothing to load" here, and only the first one
        # should. `ENOENT`/`ENOTDIR` are the well-formed "not a directory"
        # case the `if not stat.S_ISDIR(...)` below already answers by
        # returning an empty migration set; every other errno -- `EACCES`
        # when a *parent* of `migrations_dir` denies traversal, `ELOOP` for
        # the loop, and the residual case round two's adversarial test drives
        # with `ENAMETOOLONG` -- is a refusal, keyed by
        # `_directory_unreadable_remedy` (`domain/errors.py`) so the remedy
        # matches the failure instead of guessing at the single most common
        # one. A dangling or outside-pointing symlink at `migrations_dir`
        # never reaches this probe at all -- `_refuse_unusable_migrations_
        # directory_symlink` above already refused it -- so this probe's own
        # `ENOENT` branch stays keyed to the genuinely-absent case only.
        probe = migrations_dir.stat()
    except OSError as exc:
        if exc.errno in (errno.ENOENT, errno.ENOTDIR):
            return LoadedMigrations.empty()
        raise MigrationsDirectoryUnreadableError(
            str(migrations_dir.relative_to(project_root)), exc.strerror or str(exc), exc.errno
        ) from exc
    if not stat.S_ISDIR(probe.st_mode):
        return LoadedMigrations.empty()

    # Enumeration is `iterdir()`-based rather than `glob("*.yaml")`, and both
    # the directory listing and the per-entry classification (`_entry_is_
    # migration_file` below) happen inside this one `try`, so that every
    # raw-IO failure the enumeration can hit surfaces as
    # `MigrationsDirectoryUnreadableError` rather than one of two divergent
    # failure modes (issue #214): `chmod 000`/`0o111` on `migrations_dir`
    # *itself* (rather than its parent, above -- the probe needs no
    # permission on the target, only on its ancestors) makes `os.scandir`
    # raise `PermissionError` when the listing starts, and `chmod 0o444`
    # leaves the directory *listable* but not *traversable*, so stat-ing each
    # entry raises `PermissionError` instead. `pathlib.Path.glob` caught the
    # first of those internally and yielded nothing -- a silent
    # `migrationCount: 0` false positive -- while the second escaped as a raw
    # traceback; both are one class now. An `OSError` raised for a *non*-
    # symlink entry here goes through the identical `ENOENT`/`ENOTDIR`-is-a-
    # race vs. everything-else-is-a-refusal split the probe above uses: the
    # directory can vanish or be replaced between the probe and this listing,
    # and that race gets the same "nothing to load" answer a directory that
    # was simply never created gets. A *symlink* entry's own resolution
    # failure is different in kind -- a real fault on a real entry, not a
    # race against the directory -- and is refused by
    # `_entry_is_migration_file` itself, as `MigrationFileUnreadableError`,
    # before the exception ever reaches this `except`.
    #
    # Sorted so a failure reports the first file in a stable order rather than
    # whichever the filesystem happened to yield first -- and sorted *before*
    # `_entry_is_migration_file` runs (round four), not after: the names are
    # collected and sorted in one pass, then classification runs over that
    # already-sorted list, so a classification failure (a dangling or looping
    # entry) also reports the lexicographically-first offender rather than
    # whichever entry `iterdir()` happened to yield first -- APFS and ext4
    # disagree on that order, and the two candidates used to disagree on which
    # of two simultaneous failures got named. `iterdir()` does not filter
    # dotfiles (unlike `glob.glob()`), matching `Path.glob("*.yaml")`'s own
    # measured behaviour that this enumeration replaces.
    try:
        candidates = sorted(p for p in migrations_dir.iterdir() if p.name.endswith(".yaml"))
        paths = [p for p in candidates if _entry_is_migration_file(p, project_root)]
    except OSError as exc:
        if exc.errno in (errno.ENOENT, errno.ENOTDIR):
            return LoadedMigrations.empty()
        raise MigrationsDirectoryUnreadableError(
            str(migrations_dir.relative_to(project_root)), exc.strerror or str(exc), exc.errno
        ) from exc
    if len(paths) > MAX_MIGRATIONS:
        raise MigrationError(f"{len(paths)} migration files exceeds the limit of {MAX_MIGRATIONS}")

    schema_validator = _SchemaValidator(_validator(schema_root), schema_root / _SCHEMA_RELATIVE)
    migrations: list[Migration] = []
    content_by_hash: dict[str, str] = {}

    for path in paths:
        migration = _load_one(path, project_root, migrations_dir, schema_validator, content_by_hash)
        migrations.append(migration)

    return LoadedMigrations(
        migration_set=MigrationSet.ordered(tuple(migrations)),
        content_checksums=tuple(ContentHash(h) for h in sorted(content_by_hash)),
        content_by_hash=content_by_hash,
    )


def _refuse_unusable_migrations_directory_symlink(migrations_dir: Path, project_root: Path) -> None:
    """Refuse ``migrations_dir`` itself being a dangling, looping, or
    outside-project symlink (round four), before ``load_migrations``'s own
    target-following probe runs.

    That probe (``migrations_dir.stat()``) cannot tell a dangling symlink
    apart from a directory that never existed: both raise the identical
    ``ENOENT``, and both used to fold into ``LoadedMigrations.empty()``. An
    outside-pointing target is not something that probe checks at all -- it
    only asks whether the resolved path is a readable directory, never where
    it resolves to -- and an outside directory holding no ``*.yaml`` files
    never reached `_load_one`'s own containment check either, since an empty
    directory gives enumeration nothing to call it on. Both are wrong in the
    same direction as the already-fixed loop case (round two): a directory
    that is not safely usable reports "nothing to load" instead of refusing.

    ``migrations_dir.is_symlink()`` (an ``lstat``, which never follows the
    final component) is checked first, the identical shape
    :func:`_entry_is_migration_file` already uses for the per-entry case: a
    non-symlink ``migrations_dir`` returns immediately, leaving the probe's
    existing, unwidened policy as the only check that runs for it. Wrapped in
    its own ``try``/``except OSError`` here, translating whatever it raises
    (``EACCES`` from a parent that denies traversal) directly -- unlike that
    function's own ``is_symlink()`` call, which is genuinely unguarded: this
    function is invoked from ``load_migrations`` before that function's own
    ``try`` block begins, so nothing upstream catches an ``OSError`` raised
    here, while ``_entry_is_migration_file``'s ``is_symlink()`` call runs
    inside the enumeration loop's own ``try``, whose surrounding ``except
    OSError`` (``load_migrations``) is what catches a failure there instead.

    Raises:
        MigrationsDirectoryUnreadableError: If the symlink is dangling
            (``ENOENT``) or loops (``ELOOP``, reusing the probe's existing
            remedy), or for any other resolution failure -- including one at
            ``is_symlink()``'s own ``lstat``.
        PathEscapeError: If the symlink resolves to a location outside
            ``project_root``.
    """
    relative = str(migrations_dir.relative_to(project_root))
    try:
        is_dir_symlink = migrations_dir.is_symlink()
    except OSError as exc:
        raise MigrationsDirectoryUnreadableError(
            relative, exc.strerror or str(exc), exc.errno
        ) from exc
    if not is_dir_symlink:
        return

    try:
        resolved = migrations_dir.resolve(strict=True)
    except OSError as exc:
        if exc.errno == errno.ENOENT:
            raise MigrationsDirectoryUnreadableError(
                relative,
                "symbolic link target is missing",
                missing_or_wrong_text=(
                    f"{relative!r} is a symbolic link whose target is missing. "
                    f"Restore the target or remove the link, then retry."
                ),
            ) from exc
        raise MigrationsDirectoryUnreadableError(
            relative, exc.strerror or str(exc), exc.errno
        ) from exc

    try:
        resolved.relative_to(project_root.resolve())
    except ValueError as exc:
        # `.theurian/migrations` is a constant of Theurian's own layout rather
        # than anything an author wrote, so it is safe to name (issue #233).
        # The role only decides whether the refusal opens by saying this entry
        # is itself a link; it never decides what the reader is told to do.
        #
        # `resolved` is deliberately not passed as the name: it is where the
        # link points, outside the project, and naming it would hand back a
        # fact about the filesystem the refusal exists to withhold.
        raise PathEscapeError(
            relative,
            str(project_root),
            entry=EscapeSite(relative, _escape_role_of(migrations_dir)),
        ) from exc


def _entry_is_migration_file(entry: Path, project_root: Path) -> bool:
    """Classify one ``*.yaml`` entry found during enumeration (round three).

    Replaces a bare ``entry.is_file()`` in the enumeration comprehension.
    ``is_file()`` performs the identical following ``stat()`` internally, but
    it swallows every errno in CPython's own ``_IGNORED_ERRNOS`` -- including
    ``ELOOP`` and ``ENOENT`` -- and reports ``False`` for all of them alike,
    silently dropping a symlink loop or a dangling symlink from the
    enumerated set with no error at all: measured directly, two ``*.yaml``
    entries on disk, one real and one a symlink loop, reported
    ``migrationCount: 1``.

    ``entry.is_symlink()`` is checked first (an ``lstat``, which never
    follows the final component and so never itself raises for a loop or a
    missing target) to tell those two faults apart from an ordinary
    enumeration race:

    * A symlink whose resolution fails is a real fault on a real entry --
      refused by name as :class:`MigrationFileUnreadableError`, whatever the
      errno. ``ENOENT`` means the target is missing (a dangling link, given
      an explicit ``missing_or_wrong_text`` naming that); every other errno
      (``ELOOP`` for a loop chief among them) goes through
      :func:`_read_failure_remedy`.
    * A non-symlink entry that raises ``ENOENT``/``ENOTDIR`` was there when
      ``iterdir()`` listed it and is simply gone now -- a plain file removed
      mid-enumeration, not a fault on this entry. Skipped, not refused,
      matching how ``migrations_dir``'s own ``ENOENT``/``ENOTDIR`` race is
      treated one level up.
    * Any other non-symlink errno is re-raised bare, to reach the
      enumeration's own ``except OSError`` in :func:`load_migrations` and
      surface as ``MigrationsDirectoryUnreadableError`` -- a naive per-entry
      `try` that answered *this* case with "skip the entry" too would turn a
      directory-wide permission refusal into a silently shrunken migration
      set, the identical worse-regression trap the dangling/loop fix itself
      exists to avoid one shape over. Reaching this branch needs a non-symlink
      entry whose ``is_symlink()`` lstat *succeeds* but whose separate
      follow-``stat()`` then fails with something other than
      ``ENOENT``/``ENOTDIR``: a permission bit changing, or the entry being
      replaced by something unstattable, in the gap between the two calls
      (measured with ``sys.settrace``, and driven directly by
      ``test_load_migrations_refuses_a_non_symlink_entry_racing_its_own_follow_stat``,
      ``tests/unit/test_migration_loader_errors.py``). A `chmod 0o444`
      `migrations_dir` does *not* reach this branch: its own ``is_symlink()``
      lstat fails first, at the unguarded call below.

    ``entry.is_symlink()`` is called unguarded: any ``OSError`` it can raise
    (``EACCES`` from the same denies-traversal ``migrations_dir``, the
    ``chmod 0o444`` case) propagates unchanged to that same enumeration
    ``except``, since there is nothing more specific to say about it than the
    bare stat failure already says.
    """
    is_symlink = entry.is_symlink()
    try:
        entry_stat = entry.stat()
    except OSError as exc:
        if is_symlink:
            relative = str(entry.relative_to(project_root))
            if exc.errno == errno.ENOENT:
                raise MigrationFileUnreadableError(
                    relative,
                    "symbolic link target is missing",
                    missing_or_wrong_text=(
                        f"{relative!r} is a symbolic link whose target is missing. "
                        f"Restore the target or remove the link, then retry."
                    ),
                ) from exc
            raise MigrationFileUnreadableError(
                relative, exc.strerror or str(exc), exc.errno
            ) from exc
        if exc.errno in (errno.ENOENT, errno.ENOTDIR):
            return False
        raise
    return stat.S_ISREG(entry_stat.st_mode)


def _referrer(migration_path: Path, project_root: Path) -> EscapeSite:
    """The migration file, named as the place to look for a bad ``contentFile``.

    One helper for the three sibling branches in :func:`_parse_upsert` that can
    refuse the same ``contentFile`` on containment grounds, so which branch
    fired stops deciding whether the user is told where to look.
    """
    return EscapeSite(str(migration_path.relative_to(project_root)), "referrer")


def _escape_role_of(path: Path) -> EscapeRole:
    """Whether the refusal may open with "this entry is itself a symbolic link".

    One probe, because that sentence is the only claim the role now carries and
    ``lstat`` is exactly what settles it. Earlier versions took two more probes
    -- the parent chain resolving inside the root, and the path still resolving
    outside -- to justify naming this entry as the culprit and telling the
    reader to delete it. That instruction is gone
    (:class:`~theurian.domain.errors.EscapeSite` records the three refutations),
    and with it the reason those probes existed: they narrowed nothing about
    whether the entry *is* a link, which is all that remains to be said.

    An ``OSError`` degrades to ``"resolved"``, which claims nothing at all. A
    probe that could not run is not evidence of a symbolic link.
    """
    try:
        return "symlink" if path.is_symlink() else "resolved"
    except OSError:
        return "resolved"


def _load_one(
    path: Path,
    project_root: Path,
    migrations_dir: Path,
    schema_validator: _SchemaValidator,
    content_by_hash: dict[str, str],
) -> Migration:
    try:
        raw = read_source_file(project_root, PurePosixPath(path.relative_to(project_root)))
    except PathEscapeError as exc:
        # Re-raised only to attach the entry's name (issue #233).
        # `read_source_file` cannot attach it itself: its `relative` argument is
        # attacker-influenceable at other call sites -- a `contentFile` an
        # author wrote -- and `tests/unit/test_path_security.py::
        # test_no_reachable_refusal_branch_echoes_the_attacker_supplied_path`
        # pins that it is never echoed. Here it is a `.theurian/migrations/`
        # name `iterdir()` returned, the identical string the
        # `MigrationFileUnreadableError` below already prints for this same
        # entry, so this is the call site that knows it is safe to name.
        #
        # A `PathDepthExceededError` needs no passthrough here, unlike its twin
        # in `_parse_upsert`: an entry's relative path is
        # `<knowledge dir>/migrations/<name>`, three segments, well under the
        # 32-segment limit. That depends on the one call site: `load_migrations`
        # is reached only from `cli/context.py`, which builds `migrations` from
        # `ProjectPaths.of(root)` -- no `knowledge_directory` argument, so the
        # `DEFAULT_KNOWLEDGE_DIRECTORY` constant. If a caller ever passed a
        # registry-supplied `knowledge_directory` deep enough to blow the limit,
        # the depth error would be mislabelled as an escape here, and this
        # passthrough would need restoring alongside `_parse_upsert`'s.
        raise PathEscapeError(
            exc.requested,
            exc.root,
            entry=EscapeSite(str(path.relative_to(project_root)), _escape_role_of(path)),
        ) from exc
    except OSError as exc:
        # The sibling of `_parse_upsert`'s conversion below, for the *other*
        # raw read on this load path: the migration file itself, not a
        # `contentFile` it names. The measurement behind this conversion is on
        # `MigrationFileUnreadableError`'s own docstring, not repeated here.
        raise MigrationFileUnreadableError(
            str(path.relative_to(project_root)), exc.strerror or str(exc), exc.errno
        ) from exc
    checksum = ContentHash.of_bytes(raw)

    try:
        document = load_yaml_mapping(raw.decode("utf-8"))
    except UnicodeDecodeError as exc:
        raise MigrationError(f"{path.name} is not valid UTF-8") from exc
    except ValueError as exc:
        raise MigrationError(f"{path.name}: {exc}") from exc
    except yaml.YAMLError as exc:
        # `load_yaml_mapping`'s own docstring names this as the type a parse
        # failure raises -- a syntax error via the scanner, or an embedded NUL
        # byte via the reader (`yaml.reader.ReaderError`, also a `YAMLError`
        # subclass). Neither is a `UnicodeDecodeError` nor a `ValueError`, so
        # both escaped the two clauses above uncaught until now, propagating
        # as a raw Rich traceback through `resolve_context` (issue #217).
        raise MigrationError(f"{path.name}: {exc}") from exc

    try:
        # `document_name` so the depth refusal (issue #291) names the file the
        # same way this seam's own wrap below does. It is raised inside
        # `_validate_document` rather than here because both seams need it and
        # the check has to run *before* `validate`, which is the one call the
        # two seams share.
        _validate_document(schema_validator, document, document_name=path.name)
    except ValidationError as exc:
        # `_schema_rejection` and not this seam's own builder: the file name is
        # the whole difference between the two wordings, and the rest -- the
        # location, the bound, the escaping -- is one message (issue #289).
        raise MigrationError(f"{path.name} is invalid at {_schema_rejection(exc)}") from exc

    if document["apiVersion"] != MIGRATION_API_VERSION:
        raise MigrationError(
            f"{path.name} declares apiVersion {document['apiVersion']!r}; "
            f"this build understands {MIGRATION_API_VERSION!r}"
        )

    operations = tuple(
        _parse_operation(op, path, project_root, migrations_dir, content_by_hash)
        for op in document["operations"]
    )

    return Migration(
        migration_id=MigrationId(document["id"]),
        created_at=_parse_datetime(document["createdAt"], path),
        author=document["author"],
        operations=operations,
        checksum=checksum,
        depends_on=tuple(MigrationId(d) for d in document.get("dependsOn", [])),
        description=document.get("description"),
        source_path=str(path.relative_to(project_root)),
    )


def _parse_datetime(value: str, path: Path) -> datetime:
    """Parse an RFC 3339 timestamp, requiring an explicit offset.

    A naive timestamp compares wrong across a DST boundary, and knowledge
    validity windows depend on those comparisons.
    """
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise MigrationError(f"{path.name}: {value!r} is not an RFC 3339 timestamp") from exc
    if parsed.tzinfo is None:
        raise MigrationError(
            f"{path.name}: {value!r} has no UTC offset. Timestamps must be unambiguous."
        )
    return parsed


def _parse_operation(  # noqa: PLR0911, PLR0912 -- a flat dispatch over 14 operations
    payload: dict[str, Any],
    path: Path,
    project_root: Path,
    migrations_dir: Path,
    content_by_hash: dict[str, str],
) -> Operation:
    op = payload["op"]

    match op:
        case "createItem":
            return CreateItem(
                item_id=ItemId(payload["itemId"]),
                kind_=KnowledgeKind(payload["kind"]),
                namespace=payload["namespace"],
                owner=payload["owner"],
                sensitivity=Sensitivity(payload.get("sensitivity", DEFAULT_SENSITIVITY.value)),
                trust_level=TrustLevel(payload.get("trustLevel", DEFAULT_TRUST_LEVEL.value)),
            )
        case "upsertRevision":
            return _parse_upsert(payload, path, project_root, migrations_dir, content_by_hash)
        case "deprecateItem":
            superseded = payload.get("supersededBy")
            return DeprecateItem(
                item_id=ItemId(payload["itemId"]),
                reason=payload.get("reason"),
                superseded_by=None if superseded is None else ItemId(superseded),
            )
        case "restoreItem":
            return RestoreItem(item_id=ItemId(payload["itemId"]), reason=payload.get("reason"))
        case "addRelation":
            return AddRelation(
                source_item_id=ItemId(payload["sourceItemId"]),
                relation_type=RelationType(payload["relationType"]),
                target_item_id=ItemId(payload["targetItemId"]),
                note=payload.get("note"),
            )
        case "removeRelation":
            return RemoveRelation(
                source_item_id=ItemId(payload["sourceItemId"]),
                relation_type=RelationType(payload["relationType"]),
                target_item_id=ItemId(payload["targetItemId"]),
            )
        case "addAlias":
            return AddAlias(alias=ItemId(payload["alias"]), item_id=ItemId(payload["itemId"]))
        case "removeAlias":
            return RemoveAlias(alias=ItemId(payload["alias"]))
        case "changeSensitivity":
            return ChangeSensitivity(
                item_id=ItemId(payload["itemId"]),
                sensitivity=Sensitivity(payload["sensitivity"]),
                reason=payload["reason"],
            )
        case "changeOwner":
            return ChangeOwner(item_id=ItemId(payload["itemId"]), owner=payload["owner"])
        case "registerSpecification":
            return RegisterSpecification(
                spec_id=SpecId(payload["specId"]),
                item_id=ItemId(payload["itemId"]),
                source_uri=payload["sourceUri"],
                content_format=MediaType(payload["format"]),
                status=SpecificationStatus(payload.get("status", "active")),
            )
        case "supersedeSpecification":
            return SupersedeSpecification(
                spec_id=SpecId(payload["specId"]),
                superseded_by=SpecId(payload["supersededBy"]),
            )
        case "addEvidence":
            return AddEvidence(
                item_id=ItemId(payload["itemId"]),
                anchor=_parse_anchor(payload["anchor"]),
                description=payload["description"],
                confidence=float(payload.get("confidence", 1.0)),
            )
        case "removeEvidence":
            return RemoveEvidence(
                item_id=ItemId(payload["itemId"]), source_uri=payload["sourceUri"]
            )
        case _:  # pragma: no cover - the schema rejects unknown ops first
            raise MigrationError(f"{path.name}: unknown operation {op!r}")


def _parse_upsert(
    payload: dict[str, Any],
    path: Path,
    project_root: Path,
    migrations_dir: Path,
    content_by_hash: dict[str, str],
) -> UpsertRevision:
    content_file = payload["contentFile"]

    # `contentFile` is relative to the migration file, and it is attacker-
    # influenceable. Resolution happens against the project root with symlinks
    # followed first, so `../../../.ssh/id_ed25519` and a symlink that leaves
    # the tree are both refused (SEC-7, T-4, T-5).
    try:
        relative_to_root = (migrations_dir / content_file).resolve()
    except (ValueError, OSError) as exc:
        # An embedded NUL byte makes `Path.resolve()` -- `os.path.realpath`,
        # then an `lstat` the OS refuses to even attempt -- raise `ValueError`,
        # not `OSError`, before any of the checks below run. The JSON Schema's
        # `contentFile` definition checks type, length and a `..`/absolute-path
        # prefix; none of those exclude a NUL byte, so this reached the
        # resolve call unfiltered (issue #205's Class 1a, reproduced against
        # the real CLI as `ValueError: lstat: embedded null character in
        # path`). `OSError` is caught too on the same reasoning as the read
        # below: neither is a `TheurianError`, and this call sits ahead of
        # every guard in this file.
        raise MigrationContentUnreadableError(
            str(path.relative_to(project_root)),
            content_file,
            str(exc),
            getattr(exc, "errno", None),
        ) from exc
    try:
        relative = relative_to_root.relative_to(project_root.resolve())
    except ValueError as exc:
        # The migration file names the offending path; it is not itself outside,
        # so it is attached as `"referrer"` -- the location to open, never the
        # culprit to delete. Naming it costs nothing new:
        # `MigrationContentUnreadableError` four lines up already prints this
        # exact project-relative string for this exact file. `content_file`
        # stays unechoed: it is the author-written value, and it is the one
        # string on this path that SEC-7 forbids reflecting (issue #233 -- an
        # earlier commit on this branch named nothing at all here, on the false
        # reasoning that the author's value was the only name available).
        raise PathEscapeError(
            content_file, str(project_root), entry=_referrer(path, project_root)
        ) from exc

    relative_posix = PurePosixPath(relative)
    # Both calls are inside the same guard: the branch above is not the only one
    # that can refuse this `contentFile` on containment grounds. `resolve_within_root`
    # re-checks depth and containment on the now-relative form, and
    # `read_source_file` runs the symlink-escape check after it -- and both used
    # to raise anonymously, so which of three sibling branches fired decided
    # whether the user was told where to look. The reason recorded for naming
    # the migration file above applies verbatim to them.
    try:
        resolved_path = resolve_within_root(project_root, relative_posix)
        body_bytes = read_source_file(project_root, relative_posix)
    except PathDepthExceededError as exc:
        # Caught *before* the escape clause below -- it is a `PathEscapeError`
        # subclass, and that clause would re-label it as an escape, false for a
        # path that never left the root. Re-raised with the same `_referrer` its
        # siblings use: the reason recorded there applies unchanged, and which
        # of these branches fires must not decide whether the user is told
        # where to look.
        raise PathDepthExceededError(
            exc.requested, exc.root, limit=exc.limit, entry=_referrer(path, project_root)
        ) from exc
    except PathEscapeError as exc:
        raise PathEscapeError(exc.requested, exc.root, entry=_referrer(path, project_root)) from exc
    except OSError as exc:
        # `read_source_file`'s own docstring names `FileNotFoundError` as one of
        # the things it raises, and a bare `OSError` is none of the types every
        # `resolve_context` caller already guards (issue #205). Converting here,
        # at the one call site the read happens, is what makes every one of
        # those callers -- `migrate validate`, `init`, and the rest of
        # `_require_project`'s call sites (nine as of 2026-08-20; re-count
        # with `grep -rn '_require_project(as_json)$'
        # packages/theurian-core/src/theurian/cli/`) -- report the CP-2 `{error,
        # remedy}` shape instead of a Rich traceback with an empty stdout.
        raise MigrationContentUnreadableError(
            str(path.relative_to(project_root)), content_file, exc.strerror or str(exc), exc.errno
        ) from exc

    try:
        body = body_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise MigrationError(f"{path.name}: {content_file} is not valid UTF-8") from exc

    # The identity the application layer compares two revisions on (issue #210).
    # Taken from the resolved body -- the same file `read_source_file` just read --
    # so a case-variant spelling of one file (`NOTE.md` vs `note.md` on APFS/NTFS)
    # cannot read as a second reference the way its path *string* does. Guarded
    # like the read above: a stat failing here is the same CP-2 escape, converted
    # to a `TheurianError` rather than surfaced as a raw traceback.
    try:
        body_stat = resolved_path.stat()
    except OSError as exc:
        raise MigrationContentUnreadableError(
            str(path.relative_to(project_root)), content_file, exc.strerror or str(exc), exc.errno
        ) from exc
    content_identity = (body_stat.st_dev, body_stat.st_ino)

    actual = ContentHash.of_bytes(body_bytes)
    declared = payload.get("contentSha256")
    if declared is not None and declared != actual.value:
        raise MigrationError(
            f"{path.name}: {content_file} hashes to {actual.short} but the migration "
            f"pins {declared[:12]}. The body file changed after the migration was written."
        )
    content_by_hash[actual.value] = body

    metadata = payload["metadata"]
    expected = payload.get("expectedRevision")

    return UpsertRevision(
        item_id=ItemId(payload["itemId"]),
        revision_id=RevisionId(payload["revisionId"]),
        content_file_path=content_file,
        expected_revision=None if expected is None else RevisionId(expected),
        content_sha256=actual,
        # The resolution this function already performed in order to read the
        # body at all -- kept for display (a body a reader can `shasum`, and the
        # path named in a refusal), not as the comparison key. Two spellings of
        # one file resolve to two *different* strings on a case-insensitive
        # filesystem, so `content_identity` below, not this, is what the
        # application layer compares (issue #210).
        resolved_content_path=relative_posix.as_posix(),
        content_identity=content_identity,
        # `content_sha256` above is the hash this loader just computed, whether
        # or not the migration declared one, so it cannot answer "is this body
        # frozen?". Only a declared pin is checked against the file, and only a
        # declared pin therefore makes an out-of-band edit detectable (#210).
        content_pinned=declared is not None,
        metadata=RevisionMetadataSpec(
            title=metadata["title"],
            content_type=MediaType(metadata["contentType"]),
            kind=KnowledgeKind(metadata["kind"]),
            namespace=metadata["namespace"],
            status=KnowledgeStatus(metadata["status"]),
            owner=metadata["owner"],
            trust_level=TrustLevel(metadata.get("trustLevel", DEFAULT_TRUST_LEVEL.value)),
            sensitivity=Sensitivity(metadata.get("sensitivity", DEFAULT_SENSITIVITY.value)),
            tenant_id=metadata.get("tenantId", "local"),
            acl_group=metadata.get("aclGroup", "default"),
            valid_from=_optional_datetime(metadata.get("validFrom"), path),
            valid_to=_optional_datetime(metadata.get("validTo"), path),
            labels=tuple(metadata.get("labels", [])),
            scope_paths=tuple(metadata.get("scope", {}).get("paths", [])),
            source_anchors=tuple(_parse_anchor(a) for a in metadata.get("sourceAnchors", [])),
        ),
    )


def _optional_datetime(value: str | None, path: Path) -> datetime | None:
    return None if value is None else _parse_datetime(value, path)


def _parse_anchor(payload: dict[str, Any]) -> SourceAnchor:
    return SourceAnchor(
        provider=payload["provider"],
        source_uri=payload["sourceUri"],
        repository=payload.get("repository"),
        commit_sha=payload.get("commitSha"),
        blob_sha=payload.get("blobSha"),
        file_path=payload.get("filePath"),
        line_start=payload.get("lineStart"),
        line_end=payload.get("lineEnd"),
        external_id=payload.get("externalId"),
    )
