# Changelog — Theurian Core

All notable changes to the **`theurian` Python package** are documented here.
The Claude Code plugin has its own changelog at
[`plugins/claude-code/CHANGELOG.md`](../../plugins/claude-code/CHANGELOG.md);
the two version and release independently (ADR-0001).

Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versioning: [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Pre-1.0, a MINOR bump may change the protocol. Post-1.0, only a MAJOR may.

## [Unreleased]

### Added

- **`theurian propose` drafts a knowledge change, `theurian propose accept`
  moves it into place** ([#212](https://github.com/theurian/theurian/issues/212)).
  Packages ADR-0013 §4's previously manual flow into the CLI: `theurian propose`
  writes a proposal directory under `.theurian/proposals/<id>/` — a schema-valid,
  directly applicable migration named `<migration-ulid>-<slug>.yaml`, the body in
  its native format under a sub-path mirroring its knowledge namespace, and
  `evidence.json` — and writes nowhere else. `theurian propose accept <id>` moves
  the migration into `.theurian/migrations/` and the body to the path its
  `contentFile` names. Neither approves anything: `accept` moves files and stops
  short of the judgement, and approval is a human merging the pull request that
  carries the proposal (ADR-0013 point 4). There is no CLI or MCP surface that
  stands in for that merge.

- **`theurian migrate validate` names every revision whose body no digest pins**
  ([#210](https://github.com/theurian/theurian/issues/210)). `contentSha256` is
  optional in the schema and is the only thing that freezes a body: where it is
  declared, the loader hashes the file on every load and refuses a mismatch;
  where it is absent, the loader adopts whatever the file hashes to now as that
  revision's own content hash. Measured on an unpinned migration: apply it, edit
  the body out of band, and `migrate validate` still reports `valid: true` at
  exit 0, while the next `migrate apply` records the edited bytes under the same
  revision id and returns `changed: true`. Nothing recommended the field and
  nothing reported its absence.

  Validate's output now carries `unpinnedRevisions` — one line per
  `upsertRevision` that declares no pin, naming the migration to edit, the
  revision inside it, and the body whose digest to take — in `--json` and in the
  default human output alike. Additive and **always present**, an empty list when
  every revision pins. It is a **warning, not a refusal**: `valid` stays `true`
  and the exit code stays 0. Requiring the pin instead would be a breaking schema
  change with a measured cost — both shipped example migrations under
  `examples/sample-project/` are unpinned, and at this branch's base
  (`8b8abd7`) 21 of the 22 test files naming `upsertRevision` never mentioned the
  field — so it is recorded on #210 as a Milestone 7 decision rather than taken
  here. `theurian propose` already pins every revision it writes (ADR-0013).
  Reported per operation rather than per migration, because the fix is a digest
  taken from one named body file. Documented in
  [`docs/protocol/migrations.md`](../../docs/protocol/migrations.md).

### Changed

- **BREAKING — one body file may back only one revision across a migration set**
  ([#210](https://github.com/theurian/theurian/issues/210)).

  **Old shape:** a set in which two *different* revisions named one `contentFile`
  applied. Measured: two hand-written migrations sharing one path, with a correct
  `expectedRevision` chain and no `contentSha256`, both applied at exit 0 — and
  the earlier revision recorded the *later* body under its own title and author.
  Having adopted that body's hash where no pin was declared, the wrong record was
  self-consistent afterwards, so nothing could detect it later.

  **New shape:** `migrate validate` and `migrate apply` both refuse the whole set
  with `DuplicateContentFileError` at exit 4, naming both revisions, both authored
  paths, and the resolved body. `apply` refuses before it creates a database file,
  so a refused set costs no state — the property issue #63's refusal already has.
  The refusal is **unconditional of pinning**: even a pair that both pin the same
  `contentSha256` is refused, because one file cannot be independently frozen or
  attributed to two revisions — the hazard is the sharing, not the missing pin.
  The remedy says to give the later revision a body file of its own, and says what
  to do when the offending migration was already applied: editing it trips FR-K5's
  applied-migration checksum guard, so the fix there is the edit plus a
  `.theurian/state/` rebuild (FR-K4).

  The comparison key is the body's **filesystem identity** (`st_dev`/`st_ino`),
  not the path string, so two revisions that reach one physical file through
  *different* spellings still collide — a `./` segment, and the case-variant and
  NFC/NFD spellings a case-insensitive filesystem (APFS, NTFS) collapses onto one
  inode, and a second hardlinked name. A string key left those distinct and let a
  second revision name a withheld body through a variant spelling; casefolding the
  string would go wrong the other way and refuse two genuinely different files on
  a case-sensitive filesystem, so identity is the platform-correct key. Re-declaring
  one revision against its own body — how an in-place status change such as
  `reject` is written, where the revision id does not move, only `status` differs,
  and `append_revision` stays the no-op FR-K8 requires (ADR-0024 decision 5) —
  still passes, because the key that separates a re-declaration from a collision is
  the revision id. `migrate status` does not refuse — its contract is observation —
  but names every refused migration under `refusedIds`, matching how it treats the
  tenant/ACL rule.

  Breaking because a migration set that applied on `0.1.0.dev4` and earlier now
  refuses. No stable release exists — the published versions are `0.1.0.devN` —
  and no compatibility promise covers it, but the break is named here rather than
  filed as a fix. Documented in
  [`docs/protocol/migrations.md`](../../docs/protocol/migrations.md).

### Removed

- **BREAKING — `system.capabilities` no longer publishes `milestone`**
  ([#206](https://github.com/theurian/theurian/issues/206)). The field
  reported a build-progress integer that had drifted stale against the
  README's own milestone claim since the Milestone 6 close, and nothing —
  test, schema, or `docs/protocol/mcp-tools.md` — pinned its value or even
  its presence: a mutation setting it to `99` survived the whole suite. It
  had exactly one producer (`mcp/tools.py`) and no consumer anywhere in this
  repository, plugin included, and was never defined in that document or in
  any schema under `schemas/mcp/`.

  Breaking by the table in
  [`docs/protocol/plugin-core-compatibility.md`](../../docs/protocol/plugin-core-compatibility.md)
  ("Removing a field" is always breaking), and `protocolVersion` is not
  bumped for it anyway — a recorded, narrowly-scoped exemption specific to
  this one field, not a precedent for others. The reasoning is in *Changing
  this contract* in
  [`docs/protocol/mcp-tools.md`](../../docs/protocol/mcp-tools.md):
  `milestone` was protocol-undefined and had no defined purpose anywhere in
  this repository, which `version` and `protocolVersion` are not — both are
  named in that document's field-role paragraph, and each re-publishes a
  process constant a real consumer elsewhere (`theurian compat check`)
  reads directly, even though that consumer reads the constant and never
  this response.

  `test_the_system_capabilities_response_holds_exactly_the_keys_that_are_pinned`
  now pins the response's exact top-level key set, and
  `test_capabilities_report_what_is_and_is_not_built` newly pins `version`,
  `protocolVersion`, `schemaVersion` and the load-bearing substring of
  `note` — closing the four siblings review found unpinned beside
  `milestone`.

### Fixed

- **An external `$ref` destined for a host no longer records as a local file,
  and one past a walk cap no longer vanishes**
  ([#203](https://github.com/theurian/theurian/issues/203)). `_external_refs`
  recorded the scheme `urlsplit` found and defaulted to `relative-file` when it
  found none, which put four measured shapes on the wrong side of the one field
  Milestone 7's scheme allowlist (T-7, #129) will key on:

  - `//evil.test/x.json` (a protocol-relative URL) and
    `\\smb-host\share\x.json` (a UNC path) both name a host, and both recorded
    `relative-file` — the label such a gate is most likely to accept, so the
    error was in the fail-open direction;
  - `C:\Windows\system32\x.json` recorded the scheme `c`, a drive letter
    `urlsplit` reads as a scheme;
  - a `$ref` nested past `MAX_REF_DEPTH` (64) was dropped in silence, and the
    document reported `unresolvedRefCount` 0 — the same answer a document with
    no external references gives;
  - `http://[::1` made `urlsplit` raise `ValueError("Invalid IPv6 URL")` from
    inside the recording branch. That exception escaped `parse`, so one
    malformed reference discarded the *whole* document — every operation,
    schema and other reference in it — with a message naming no remedy.

  A reference carrying no scheme is now classified by its structure, following
  RFC 3986 §4.2, into `protocol-relative`, `unc`, `absolute-file` or
  `relative-file`; one that carries a scheme records that scheme, lowercased,
  matched against RFC 3986 §3.1 rather than through `urllib.parse`. The split is
  structural rather than a list of bad spellings, so the mixed `/\host\x` that
  Windows and browsers accept lands on the network side without being
  enumerated, and `x://host/y` keeps its one-letter *scheme* while
  `C:\Windows\x` does not. `NETWORK_PATH_SCHEMES` and `LOCAL_PATH_SCHEMES`
  publish the two groups for the gate that will read them.

  **Both walk caps stay where they were** — `MAX_REFS` (5000) and
  `MAX_REF_DEPTH` (64) — and each now records where it stopped, one record per
  reason and two reasons, so the marker list holds at most two entries however
  many nodes sit at a cap. That bound is on the marker list and not on the walk:
  the traversal revisits shared sub-objects rather than memoising them, so
  neither cap is a resource-exhaustion control
  ([#245](https://github.com/theurian/theurian/issues/245)).
  Neither marks a node that could not have held a reference: a scalar
  has no children and an empty container has none either, and emptiness is
  answerable without descending, which is what lets the check sit in front of a
  cap that forbids descending. A non-empty container stays marked even when it
  holds only scalars, because knowing better means reading the children the cap
  refused. The parser's metadata gains `refWalkTruncated`, which says whether
  `unresolvedRefCount` is a total or a lower bound, and the index gains
  `refWalkTruncations`. That count is over the document's distinct `$ref`
  strings only — not occurrences, not distinct targets, and not the other
  resolution keywords a specification can carry, which this walk does not visit
  ([#246](https://github.com/theurian/theurian/issues/246)). Both counts stop at the parser boundary — `IngestedDocument`
  has no metadata field, so what survives ingestion is `_index`'s
  `externalRefs` and `refWalkTruncations`, which is where a Milestone 7 gate
  should read. Nothing fetches, and the never-fetched pins in
  `tests/unit/test_network_call_sites.py` are untouched.

  **Not covered: a scheme that is faithful and still remote.**
  `file://evil.test/share/x.json` records `file`, which is what it is. A gate
  that allows `file` at all has to inspect the authority, exactly as it has to
  inspect the path of an equally local `file:///etc/shadow`; the residual is
  recorded in `docs/security/threat-model.md` (T-7) and pinned as a decision by
  `test_a_file_url_with_a_host_records_its_scheme_and_leaves_the_authority_alone`.

- **`--json` commands no longer crash with a raw traceback when a migration's
  content cannot be read**
  ([#205](https://github.com/theurian/theurian/issues/205)). Every CLI command
  that resolves a project loads and validates its migrations first, and a raw
  filesystem-call failure on that path escaped through Typer as a Rich traceback
  — exit 1, empty stdout, no `{error, remedy}` document — even under `--json`.
  Reproduced against `migrate validate --json` and `init`; it reached all eleven
  `resolve_context`/`_require_project` call sites, `project status` included,
  whose contract is to answer rather than crash. Each read failure now raises a
  `TheurianError` subtype that those call sites already guard, so it renders as a
  structured `{error, remedy}` failure at exit 1 instead. The class covered is
  filesystem-call failures that *raise*:

  - a `contentFile` that cannot be resolved or read — missing, a permission
    problem, a directory, or a path holding a NUL byte, which makes
    `Path.resolve()` raise `ValueError` before any syscall, one call site earlier
    than the read;
  - a migration file the loader cannot read;
  - a `.theurian/migrations/` directory whose probe (`Path.is_dir()`) re-raises
    `EACCES` — for example its execute bit removed;
  - a project registry — data directory or registry file — that cannot be read;
  - the installed package's JSON Schema, when a candidate is found but reading it
    fails.

  Each remedy is selected by *why* the read failed, not one guess per `OSError`:
  a missing or malformed path points at path resolution, `EACCES`/`EPERM` at
  permissions, `EISDIR` at "this names a directory, not a file." `project status
  --json` now surfaces the remedy in its unresolved-project payload too.

  **Not covered when this entry was written — two adjacent defects shared this
  symptom but not its root cause.** A `.theurian/migrations/` directory that
  `Path.glob` cannot list swallowed the `PermissionError` and reported
  `valid: true, migrationCount: 0` — a silent false-negative, because nothing
  raised ([#214](https://github.com/theurian/theurian/issues/214)). A malformed
  migration YAML raised `yaml.YAMLError` at parse time, which the loader did not
  catch, so it still escaped as a traceback under `--json`
  ([#217](https://github.com/theurian/theurian/issues/217)). Both are fixed
  below.

- **`migrate validate|status|apply --json` (and every other `resolve_context`
  consumer) refuse a malformed migration, an unreadable migrations directory,
  or a corrupted installed schema with the structured `{error, remedy}`
  shape, instead of a raw traceback or a silent false positive**
  ([#214](https://github.com/theurian/theurian/issues/214),
  [#217](https://github.com/theurian/theurian/issues/217)). Closes the two
  defects the `#205` fix above named as not covered, a third face of #214
  that reproducing it found and that neither issue had named, three more
  members of the same class that this branch's own review round two found on
  the same load path, three further members round three found on the
  round-two fixes themselves, and four further members round four found —
  two more ways `migrations_dir` itself can behave like a broken symlink, a
  dependency of a multi-failure refusal's named entry on filesystem
  enumeration order, and a `RecursionError` escape round three's own new
  `check_schema` call introduced — not new issues, escapes the round-one
  through round-three fixes each left open.

  - **#217 — a YAML syntax error, or a NUL byte in the migration source, no
    longer crashes.** `load_yaml_mapping` raises `yaml.YAMLError` on either —
    a scanner syntax error, and a reader NUL-byte error
    (`yaml.reader.ReaderError`, also a `YAMLError` subclass) — neither of
    which is the `UnicodeDecodeError` or `ValueError` that `_load_one`'s
    `except` clause around it caught. Both now convert to `MigrationError`
    naming the file and the parse problem. `proposal_service.py`'s own two
    `load_yaml_mapping` call sites already catch `yaml.YAMLError`, but not
    identically: `_parse_migration` (the `propose accept` path) already
    translated it into a `ProposalError`, while `_pinned_digest_at` catches it
    only to skip a malformed *existing* migration silently — documented there
    as deliberate, since that migration already fails `migrate validate` on
    its own and diagnosing it is not `propose accept`'s job.
  - **#214 — an unreadable `migrations_dir` no longer reports the never-read
    set as an empty one.** `chmod 000`/`0o111` on `.theurian/migrations/`
    itself, not its parent (`is_dir()` needs no permission on the target,
    only its ancestors), left `Path.glob("*.yaml")`'s own `scandir` catching
    the `PermissionError` internally and yielding nothing: `migrate validate
    --json` reported `valid: true` with `migrationCount: 0`, and `migrate
    apply --json` went on to create a state database for the set it wrongly
    believed was the whole story. Enumeration is now `iterdir()`-based, with
    the directory listing and every entry's `is_file()` stat inside one
    `try`, so any `OSError` there raises `MigrationsDirectoryUnreadableError`.
  - **A third face of #214, found while reproducing it and named in neither
    issue.** `chmod 0o444` leaves the directory listable but not traversable,
    so the same per-entry `is_file()` stat raised an uncaught
    `PermissionError` instead — a raw traceback, not the silent false
    positive above. Same root cause, same fix: caught by the same `try`.
  - **Round two — a YAML document nested past PyYAML's recursion limit no
    longer crashes.** `RecursionError` is not outside `Exception`'s hierarchy
    — it is a `RuntimeError` subclass, and a bare `except Exception` would
    have caught it fine — but no `except` clause on this path named
    `RuntimeError` or `RecursionError` at all: `load_yaml_mapping`'s three
    callers (`migration_loader.py`'s `_load_one`, and `proposal_service.py`'s
    `_parse_migration` and `_pinned_digest_at`) each caught only
    `UnicodeDecodeError`, `ValueError`, and `yaml.YAMLError` —
    `_pinned_digest_at` also catches `OSError`, which the other two do not,
    but none of the three named `RuntimeError` or `RecursionError` — so it
    escaped every one of them and reached `resolve_context` as a raw
    traceback under `--json`. About 1 KB of nested YAML is already enough to
    trigger it (measured: `"["*495 + "]"*495`, 990 bytes — 1,023 was the full
    migration document this was reproduced against, not this bracket string
    alone). `load_yaml` and
    `load_yaml_mapping` now catch `RecursionError` and raise `ValueError` in
    its place — the type every consumer on this path already handles.
    `load_yaml`'s other three callers, which do not go through
    `load_yaml_mapping` — the front-matter, structured-YAML, and OpenAPI
    parsers (`infrastructure/filesystem/parsers/`) — get the identical fix
    from the same one seam, rather than a catch clause added at each of the
    six.
  - **Round two — a symlink chain at `.theurian/migrations` longer than the
    platform's loop limit no longer reports real migrations as an empty
    set.** `Path.is_dir()` swallows every `OSError` it hits internally,
    `ELOOP` included, and reports `False` — the same convenience the #214 fix
    above relies on for the genuinely-absent case — so a directory that was
    actually a loop was misreported as "does not exist": `migrate validate
    --json` reported `valid: true` with `migrationCount: 0`, and `migrate
    apply --json` seeded a state database for the empty set it wrongly
    believed was the whole story. The probe is now an explicit `os.stat`:
    `ENOENT`/`ENOTDIR` still answer the legitimate empty case, but `ELOOP`
    and every other errno now raise `MigrationsDirectoryUnreadableError`,
    whose remedy is keyed on `errno` (the loop, a permission problem, or an
    unnamed residual case) rather than one permission-shaped message for all
    three. **This is a behaviour change, not only a fix**: a symlink loop at
    this path used to validate as an empty migration set by accident, and now
    refuses instead — any earlier description of a symlink loop as a
    legitimate empty shape no longer holds.
  - **Round two, never itself given a line in this entry until now — the
    enumeration's own `ENOENT`/`ENOTDIR` answer relaxed from a refusal (round
    one) back to an empty set.** Round one's enumeration `except OSError`
    (the #214 fix above) converted *every* `OSError` to
    `MigrationsDirectoryUnreadableError`, `ENOENT`/`ENOTDIR` included, so a
    `migrations_dir` that raced its own deletion between the directory probe
    and the `iterdir()` listing refused rather than answering
    `LoadedMigrations.empty()`. The `os.stat`-probe rewrite in the bullet
    above added the identical `if exc.errno in (ENOENT, ENOTDIR): return
    empty()` branch to the enumeration's own `except` too, quietly changing
    this one case back to the pre-#214 answer — a deliberate race policy, not
    an oversight: the probe's own answer went stale between the check and the
    listing, and a directory that has since vanished means nothing to load,
    not a corrupted install. Shipped in round two but not previously named
    here, unlike the `ELOOP` change beside it, which this entry already
    flagged as a behaviour change; pinned by
    `test_load_migrations_treats_a_stale_enumeration_failure_as_an_empty_set`.
  - **Round two — a corrupted installed schema no longer crashes
    `_validator`.** Truncated or empty JSON raised `json.JSONDecodeError`;
    non-UTF-8 bytes raised `UnicodeDecodeError` at the same
    `read_text(encoding="utf-8")` call; and a schema that parses but is a
    JSON list rather than an object raised `AttributeError` one line later,
    at `Draft202012Validator` construction (`jsonschema` calls
    `schema.get(...)` internally, and a list has no `.get`). All three, and
    the original `OSError`, now convert to `SchemaUnreadableError`. Round
    three (below) removes the `AttributeError` translation and replaces it
    with a check performed before construction, rather than after.
  - **Round three — a `*.yaml` entry that is a symlink loop, or a symlink
    whose target is missing, no longer vanishes silently from the loaded
    set.** `Path.is_file()` — used by both `glob` and round one/two's
    `iterdir()`-based enumeration — swallows `ELOOP` and `ENOENT` internally
    and reports `False` for both, the identical directory-level convenience
    the #214 and round-two fixes above exist to close, one level down:
    `migrate validate --json` reported a lower `migrationCount` with no error
    at all, and `migrate apply --json` seeded a state database for the
    migrations it did see. Per-entry classification now `lstat`s each entry
    first to tell a symlink from an ordinary file, and a symlink whose
    resolution fails is refused by name as `MigrationFileUnreadableError` —
    `ENOENT` gets a dangling-link-specific remedy, every other errno (`ELOOP`
    chief among them) the loop-specific one from the bullet above. A
    non-symlink entry that raises `ENOENT`/`ENOTDIR` is still skipped rather
    than refused: the identical race the enumeration-level bullet above
    already answers with "nothing to load," one entry down instead of one
    directory up. Handling the two identically was considered and rejected:
    an entry-level `try` that answered every stat error with "skip the
    entry," symlink or not, would turn a directory-wide permission refusal
    (`chmod 0o444` on `migrations_dir`, #214's own third face above) into a
    silently shrunken migration set the moment it reached one symlinked entry
    first — the same worse-regression trap the loop/dangling fix itself
    exists to avoid, one shape over. Any other non-symlink errno (`EACCES`
    included) still re-raises to the enumeration's own `except OSError` and
    surfaces as `MigrationsDirectoryUnreadableError`, unchanged. Reproduced
    against the real CLI:
    `test_validate_reports_a_symlink_loop_migration_entry_instead_of_silently_dropping_it`
    and
    `test_apply_refuses_a_symlink_loop_migration_entry_without_seeding_a_state_database`.
  - **Round three — the last path where a deeply nested document could still
    crash a parser now converts to `ValueError`, and the sibling leg that
    already converted gains the attribution every other failure branch
    carries.** `OpenApiParser`'s JSON leg
    (`infrastructure/filesystem/parsers/openapi.py`) called `json.loads(text)`
    with no guard around `RecursionError`, so a document nested past the
    decoder's own recursion limit escaped `_load` uncaught (measured: 20,000
    nested arrays) — `structured.py`'s `JsonParser` already carried the
    identical guard, so this closes the class's one remaining crash.
    `YamlParser.parse` (`structured.py`) never crashed the same way: round
    two's `load_yaml` fix (above) had already converted its nesting-depth
    failure to `ValueError`. What it lacked was `anchor.source_uri` on that
    one branch, unlike every other failure branch in the same method — this
    round adds it, so the message names the same document every sibling
    failure already names. Reproduced against the real parsers directly:
    `test_openapi_reports_the_source_uri_for_json_nested_past_the_recursion_limit`
    and
    `test_yaml_parser_names_the_source_uri_for_a_document_nested_past_the_recursion_limit`.
  - **Round three — the installed schema is now refused, not silently
    accepted, when it parses but is not usable as a schema.** Two checks in
    `_validator`, both performed before `Draft202012Validator` construction
    rather than after: `isinstance(schema, dict)` refuses a schema that is
    not a JSON object at all — a list (already an `AttributeError` refusal
    since round two, above) or a bare `true`/`false`, both otherwise-valid
    top-level JSON Schema documents. Accepting `true` had been silently
    fail-open: it builds a validator that matches every instance, so an
    installation corrupted to `true` made every migration in every project
    validate rather than refusing. `Draft202012Validator.check_schema(schema)`
    separately refuses a schema whose own keywords are structurally
    malformed — `required` must be an array of strings, and a bare string
    previously surfaced only when a schema-*valid* migration tripped over it,
    misattributed to whichever migration validated first as `'n' is a
    required property`. Both convert to `SchemaUnreadableError`, and the
    now-unreachable `except AttributeError` clause is removed rather than
    kept as a defensive clause nothing can drive. `{}` remains accepted: a
    valid, if vacuous, schema that matches every instance — deliberately not
    a third refusal. `test_validator_raises_schema_unreadable_error_for_a_non_object_schema`,
    `test_validator_raises_schema_unreadable_error_for_structurally_invalid_schema_keywords`,
    and `test_validator_accepts_the_vacuous_empty_object_schema` pin all
    three.
  - **Round four — `migrations_dir` itself being a dangling, looping, or
    outside-project symlink no longer reports "nothing to load" the way a
    genuinely absent directory does.** The entry-level symlink policy round
    three added (two bullets above) never covered the directory `_load_one`
    reads *from*: the top-of-function probe (`migrations_dir.stat()`, which
    follows symlinks) cannot tell a dangling symlink from a directory that
    never existed — both raise the identical `ENOENT` — and an
    outside-project target was never checked directly at all, only reached
    incidentally through `_load_one`'s own containment check once a `*.yaml`
    entry exists to trigger it, which an empty outside directory never
    reaches. Orchestrator-measured before this fix: a dangling
    `migrations_dir` symlink made `migrate apply --json` report
    `databaseCreated: true` and create `.theurian/state/active.json` and a
    `.sqlite` database for the empty set it wrongly believed was the whole
    story; a symlink resolving outside `project_root` to an empty directory
    made `migrate validate --json` report `valid: true, migrationCount: 0`
    at exit 0. A new check, `_refuse_unusable_migrations_directory_symlink`,
    now runs before the top-of-function probe: a dangling or looping target
    raises `MigrationsDirectoryUnreadableError` (the identical dangling-link
    and loop remedies the entry-level and round-two directory-level cases
    already use), and a target outside `project_root` raises
    `PathEscapeError` directly rather than depending on an entry existing to
    trigger it. **This is a behaviour change, not only a fix**: a dangling or
    outside-pointing `migrations_dir` symlink used to validate as an empty
    migration set, and now refuses instead. A `migrations_dir` symlinked to a
    real, in-project directory still loads normally — this policy narrows
    only the two broken shapes, not every symlinked directory.
    `test_load_migrations_refuses_a_dangling_migrations_directory_symlink`,
    `test_load_migrations_refuses_a_migrations_directory_symlink_to_an_empty_outside_directory`,
    and
    `test_load_migrations_follows_a_migrations_directory_symlink_to_a_valid_in_project_directory`
    pin all three.
  - **Round four — a multi-failure refusal now names the
    lexicographically-first failing entry on every filesystem, not whichever
    one the directory listing happened to yield first.** The enumeration's
    long-standing `sorted(...)` call sorts the *paths* it is given, but round
    three's per-entry classification (`_entry_is_migration_file`, which can
    itself raise `MigrationFileUnreadableError` for a dangling or looping
    entry) ran *inside* the same generator expression `sorted()` consumed, so
    it was still evaluated in whatever order `iterdir()` yielded, before
    sorting ever ran. Two failing entries therefore named
    whichever one the filesystem happened to enumerate first — APFS is
    measured here to walk in creation order; ext4's documented `dir_index`
    hashing walks in hash order (not measured on this machine, which is not
    Linux), so the identical fixture could name a different offender on
    each. Enumeration now
    collects and sorts the `*.yaml` names first, then runs classification
    over the already-sorted list.
    `test_load_migrations_names_the_lexicographically_first_entry_when_classification_fails`
    drives it directly, injecting a reversed-order fixture so the bug
    reproduces regardless of the developer's own filesystem's enumeration
    order.
  - **Round four — a deeply nested installed schema no longer crashes
    `_validator` through the `check_schema` call round three's own fix
    added.** `Draft202012Validator.check_schema`, added last round to catch a
    structurally malformed schema before any migration is checked against
    it, recurses into a schema's own nested keywords the same way
    `json.loads` recurses into its document structure — a schema deep enough
    exhausts the interpreter stack the identical way an attacker-controlled
    migration document already does, measured directly at 400 levels of
    nested `not` keywords. Neither the read's own three `except` clauses nor
    round three's new `except SchemaError` around `check_schema` named
    `RecursionError` — a `RuntimeError` subclass, not a
    `jsonschema.exceptions.SchemaError` — so it escaped `_validator` raw, the
    identical class every other member of this entry was closed for. A
    regression the branch caught and fixed within its own review loop rather
    than shipping: `check_schema` did not exist in this function until round
    three added it (the bullet above), so this gap did not exist before that
    round either. `_validator` now catches `RecursionError` around both
    `json.loads` and `check_schema`, converting each to
    `SchemaUnreadableError`.
    `test_validator_raises_schema_unreadable_error_for_a_schema_nested_past_the_recursion_limit`
    pins it. **Not covered by this fix**: a validate-time `$ref` resolution
    failure — including whatever network fetch `jsonschema`'s own reference
    resolution performs for a remote `$ref` — is a different failure surface
    than the schema document's own JSON and keyword nesting, and stays
    untranslated ([#235](https://github.com/theurian/theurian/issues/235)).

  **The legitimate empty shapes, current as of round four.**
  `load_migrations` still answers `LoadedMigrations.empty()` — never a
  refusal — for: a `migrations_dir` that does not exist at all; one that
  exists but is a regular file rather than a directory
  (`test_load_migrations_treats_a_migrations_path_that_is_a_regular_file_as_an_empty_set`,
  round three); an existing, ordinarily-readable, genuinely empty directory
  (`test_load_migrations_on_an_ordinarily_readable_empty_directory_returns_an_empty_set`);
  a `*.yaml`-named entry that is a FIFO, a directory, or a symlink resolving
  to a FIFO, a directory, or a character device, silently excluded from the
  loaded set rather than counted — the FIFO and directory cases are pinned by
  `test_load_migrations_skips_a_fifo_and_a_directory_both_named_dot_yaml`
  (round three); the symlink-to-either-of-those (and to a character device)
  case follows the identical `entry.stat()`-then-`S_ISREG` path — a symlink
  to a non-regular target stats successfully and simply fails the type
  check — and is reasoned from that code path, not pinned by a dedicated
  test: the shape the round-three adversarial review flagged as missing from
  this list, recorded here for the first time; and a non-symlink entry that
  is simply gone by the time it is stat-ed, having raced its own deletion
  between `iterdir()` listing it and the classification stat that follows
  (`test_load_migrations_skips_only_a_non_symlink_entry_that_vanishes_mid_enumeration`,
  round three) — the identical race policy the enumeration-level bullet above
  already applies one directory up. A directory-level symlink loop at
  `migrations_dir` itself is deliberately not a member of this list — refused
  since round two, and refused here too, though round four's new symlink
  check now catches it before the top-of-function directory probe ever runs
  (identical `MigrationsDirectoryUnreadableError` loop remedy either way). A
  dangling or outside-project `migrations_dir` symlink is not a member of
  this list either, as of round four — see below.

  **Not a legitimate empty shape any more, as of round three: a `*.yaml`
  entry that is a symlink loop, or a symlink whose target is missing.** Both
  used to vanish the identical silent way described above — `glob` and every
  round before this one relied on `Path.is_file()`, which swallows `ELOOP`
  and `ENOENT` internally and simply excludes the entry, no error, no count.
  Per-entry classification now raises `MigrationFileUnreadableError` naming
  the entry instead (round-three bullet above); any earlier description of
  either as a legitimate empty or silently-skipped shape no longer holds.

  **Not a legitimate empty shape any more, as of round four: a dangling or
  outside-project `migrations_dir` symlink.** Both used to validate as an
  empty migration set — a dangling target folded into the identical `ENOENT`
  a missing directory raises, and an outside-project target holding no
  `*.yaml` files never reached the containment check that would otherwise
  catch it. The round-four directory-level bullet above closes both; any
  earlier description of either as a legitimate empty shape no longer holds.

  **Scope of the directory-level symlink check: the final path component
  only.** Round four's check is an `lstat` on `migrations_dir` itself, so a
  symlinked *ancestor* — most visibly `.theurian` being a symlink — is not
  covered: a dangling `.theurian`, or one pointing at an empty outside
  directory, still validates as an empty set, and a `.theurian` symlink
  committed to a repository makes `migrate apply` write state through it,
  outside the working tree, on `git clone` alone. That is a distinct class
  from this one — its root cause is the writer/context stack trusting a
  resolved `.theurian` for every consumer, not the migration load path's
  error surfacing — and it is tracked separately at
  [#237](https://github.com/theurian/theurian/issues/237), not closed here.

  Every fault named above — across all four rounds, not counting the
  enumeration-race policy note, which documents a round-two decision rather
  than closing a new one — was reproduced against the real CLI, or the real
  parsers and `_validator` directly for the parser and schema checks; covered
  by `tests/unit/test_migration_loader_errors.py`,
  `tests/unit/test_yaml_loading.py`, `tests/unit/test_parsers.py`, and
  `tests/integration/test_cli_commands.py`.

### Documentation

- **Documents describing review ingestion as shipped, corrected together with
  the tests that hold the corrected claims**
  ([#129](https://github.com/theurian/theurian/issues/129)). The class is a
  security control named in the present tense whose component does not exist:
  T-7's SSRF entry, the `.theurian/config.yaml` repository allowlist (SEC-10),
  the `providers.review.repositories` schema key, the sample project's config,
  and the `review`/`infrastructure.github` package docstrings all read as if the
  allowlist were in force. No reader of that file exists in `src/`, and
  `infrastructure/github/` holds no adapter, so nothing consults it; each
  now says what is owed and names Milestone 7.

  **What the allowlist's absence rests on is now pinned.** T-7's stand-in
  control is not a filter but the absence of any way to make the request, and
  that absence was enforced by nothing: a mutation that left `_external_refs`
  recording exactly as before and added a real `urllib.request.urlopen` beside
  it survived the whole suite, because
  `test_external_refs_are_recorded_never_fetched` reads the recorded output and
  the recording did not change. `tests/unit/test_network_call_sites.py` adds the
  missing half in three arms —
  `test_no_module_outside_the_daemon_health_probe_reaches_a_network_client`
  scans the shipped package and pins the permitted network-client sites to
  `daemon/instance.py` alone, resolving attribute chains and constant-string
  dynamic imports; `test_no_module_outside_the_git_and_service_adapters_can_spawn_a_process`
  does the same over `subprocess`, the `os` spawn/exec family
  (`system`, `popen`, `spawn*`, `posix_spawn*`, `exec*`) and
  `asyncio.create_subprocess_*`, because `curl` and `gh api` reach the network
  with no client module in the diff; and
  `test_parsing_a_hostile_document_opens_no_socket` watches the socket layer
  while *every* parser `default_parsers()` returns handles a hostile document,
  with `test_every_parser_the_registry_ships_has_a_hostile_document` failing
  when the registry gains a format the table does not know. The threat model now
  cites the recording pin and the never-fetched pins separately, rather than
  crediting one test with both, and states the residual all three share: a
  fetch both spelled at runtime and issued from a child process.

- **`system.capabilities`' `reviewIngestion`, `traceability` and
  `knowledgeSearch` flags pinned** in
  `test_capabilities_report_what_is_and_is_not_built`, with
  `test_the_capability_block_holds_exactly_the_flags_that_are_pinned` holding
  the key set so a flag added later cannot ship unasserted. All three were
  unpinned: mutations flipping the first two to `true`, and rewriting
  `knowledgeSearch` to `"substring"` — indistinguishable to a client from what
  an un-indexed project reports — survived the suite. That is the same drift
  that once let the test claim `hybridRetrieval is False` after hybrid retrieval
  shipped. T-7 cites `reviewIngestion: false` as part of what stands in for the
  missing allowlist, so the flag is a security-relevant declaration and not a
  feature toggle.

- **`KnowledgeCandidate.trust_level` cannot be set at construction**, pinned by
  `test_a_candidate_cannot_be_constructed_with_a_trust_level`. The invariant is
  one `field(init=False)` keyword and no test named it, so removing it kept the
  default green while `KnowledgeCandidate(trust_level=REVIEWED)` became
  constructible — a candidate granting itself the trust a human reviewer exists
  to grant (ADR-0013, INV-7). `docs/architecture/review-knowledge.md` now names
  the mechanism behind each promotion invariant rather than attributing all of
  them to construction.

- **`$ref` recording fidelity stated rather than overclaimed.** T-7 said
  `_external_refs` "records the target's scheme"; it recorded the scheme only
  where the target's form carried one, so a protocol-relative (`//host/x.yaml`)
  or UNC target recorded as `relative-file`, and a ref past either walk cap —
  `MAX_REFS` (5000) or `MAX_REF_DEPTH` (64), both now named — was dropped from
  the count entirely. Stating it is what this entry did;
  [#203](https://github.com/theurian/theurian/issues/203) then fixed the
  recording itself, in the same release — see *Fixed* above, which is what T-7
  now describes.

- **How many `git` reads `theurian ingest` performs, measured rather than
  counted from the module.** `cli/context.py` defines four readers; the ingest
  path runs three — `rev-parse --show-toplevel`, `rev-parse HEAD` and
  `remote get-url origin` — because `default_branch`
  (`symbolic-ref --short HEAD`) is reached only from `project register` and
  `migrate apply`. Measured by running the command against a `git` shim that
  logs every invocation. Recorded here because the count is a fact about
  `cli/context.py`; the document it corrects is the plugin's `/theurian:ingest`,
  whose own change is in
  [the plugin changelog](../../plugins/claude-code/CHANGELOG.md).

- **T-7's owed controls listed wherever the entry is summarised.** The threat
  table in `docs/architecture/requirements-analysis.md` named only the
  repository allowlist as owed, while SEC-10 also requires the scheme allowlist
  and the rejection of private-network destinations; it now lists all three, as
  the threat model itself already did. In the same sweep,
  `schemas/config/project-config.schema.json` stops attributing every absent
  loader to Milestone 7 — #129 owes the review-ingestion allowlist reader
  specifically, and the rest of the file simply has no loader yet — and
  restores "Not in force." to the head of the `repositories` description, since
  an editor showing a field's hover text does not show the root note.
  `ReviewProvider`'s docstring now says the GitHub *adapter* is unbuilt; the
  port itself exists.

- **The documents described a secret scanner that does not exist**
  ([#198](https://github.com/theurian/theurian/issues/198)). SEC-11 — scan a
  candidate revision for secrets and block (default), warn, or do nothing per
  policy — is not implemented. No content scanner exists anywhere in `src/`, and
  nothing reads `.theurian/config.yaml` at all (#129), so the
  `security.secretScan` key selected no behaviour while the published schema
  declared `"default": "block"`. Six surfaces asserted the control was in force
  and each now names the absence: T-15's Controls block, the threat summary row,
  the same T-15 row in `docs/architecture/requirements-analysis.md`,
  `SECURITY.md`'s "Ingestion warns or blocks per policy", the schema key, and the
  sample project's config. Same class as the T-7 correction above — a security
  control written in the present tense whose component does not exist.

  **What stands at SEC-11's trigger point is now stated rather than implied, and
  neither control is automated:** human review of the authored migration
  (ADR-0013 — no registered MCP tool can reach a canonical write, and `theurian
  propose accept` moves files without approving), and supersede or retire with
  Milestone 6's withdrawal→purge trigger for removing a secret after the fact.
  Both entries record what neither control does: nothing enforces the merge —
  `migrate apply` applies whatever is in `.theurian/migrations/`, committed or
  not.

  **The residual names the boundary the exposure actually starts at.** It is the
  canonical write, not the index: a secret becomes readable through
  `knowledge.search` and `knowledge.get` the moment `theurian migrate apply`
  writes it, before any `index build`, because search degrades to a canonical
  substring scan when no index can answer (`mcp/search.py`). The repository-side
  `Secret scan` job (OSS-9, gitleaks) is a different control and is unchanged —
  it scans this repository's Git history in CI and was never in a user project's
  ingestion path.

  **Each correction is now pinned, because a corrected claim with no test is a
  claim that can quietly become false again.** `test_the_secret_scan_policy_publishes_no_default`
  holds the dropped schema default, with the enum asserted beside it so the
  no-default assertion cannot pass vacuously against a renamed or deleted key.
  `tests/unit/test_config_key_call_sites.py` is new and holds the claim the other
  five surfaces rest on — that no shipped code reads the key:
  `test_no_shipped_module_reads_a_config_key_the_schema_publishes_as_not_in_force`
  parses every `.py` under the imported package and matches whole identifiers and
  whole string constants, which is what separates a reader from the six places
  `repositories` appears in `src/` as English prose;
  `test_each_reserved_key_still_publishes_the_absence_the_scan_enforces` closes the
  reverse direction, where the description moves and no reader is added; and
  `test_the_config_key_scan_sees_each_naming_form_and_no_other` guards the scanner
  itself, since a scan that resolves nothing and a package with no reader produce
  the same green. `test_a_key_the_example_sets_but_nothing_reads_stays_marked_not_in_force`
  holds the sample project's annotation, which no schema validates. The same
  three pins cover `providers.review.repositories` (#129), whose schema
  description makes the identical not-in-force claim.

- **`theurian ingest`'s docstring said it "stores evidence"**, which overstates
  what the command persists: `IngestionService` has no write path, parsed bodies
  live in memory for the run, and the only file written is the content-hash
  manifest `.theurian/cache/ingestion.json`. The docstring and T-15's
  reference to it now say so. `schemas/config/project-config.schema.json`'s
  `security.maxSourceFileBytes` gains the same treatment its annotated siblings
  have: its default documents the shipped `MAX_SOURCE_FILE_BYTES` in
  `security/paths.py` rather than setting it, and nothing reads the key (#129).

## [0.1.0.dev4] - 2026-08-16

### Added

- **The `integrity` signal takes a second measurement: how many items a caller
  should be able to see, against the number `theurian migrate apply` recorded**
  ([#30](https://github.com/theurian/theurian/issues/30) PR2). This is what makes
  a response's *own* emptiness visible, where PR1 — shipped in `0.1.0.dev3` —
  could only report damage elsewhere in the state. Three of the four positions
  PR1 left now disclose, and `SILENTLY_EMPTIED` is deleted — #30's stated closure
  condition.

  The record is a new `project_integrity` table, one row per project, written by
  `migrate apply` inside its own write transaction and counted over the rows that
  transaction just wrote. Nothing on a query path computes it: the reader's half
  is one `COUNT` over `idx_items_status`, catching a change in the *number* of
  surfaceable rows — a row leaving or entering the surfaceable scope, in either
  direction — so an expectation cannot be satisfied by the state it is meant to
  check
  (`CanonicalStore.count_surfaceable_items`,
  `CanonicalStore.expected_surfaceable_count`, `SqliteWriter.record_expected_surfaceable_count`).

  **A missing record is damage, not "not recorded", and a schema bump is what
  makes that sound.** `SCHEMA_VERSION` went 2 → 3 for this table (below), and
  `is_supported` is exact-match, so every database this build can open was written
  by a build that records. `test_a_missing_integrity_record_is_damage_and_not_silence`
  holds the inference and `test_a_pre_integrity_database_is_refused_unread_by_every_tool`
  holds the premise on all three tools and over every version below the current
  one — including that an old database is not reported as a damaged one.

  **What it now catches, pinned by the corruption sweep against the real tools
  over a real damaged database.** A sentinel in `knowledge_items.project_id`
  takes every item out of the project scope, and one in `knowledge_items.status`
  takes a row out of the
  surfaceable scope: `knowledge.search` answers `count: 0, results: []` and
  `knowledge.status` answers a shrunken `itemCount` **with the key beside them**,
  where PR1 answered the same numbers alone; `knowledge.get` refuses those cells
  as damage rather than reporting them as absence
  (`test_a_lost_surfaceable_item_is_damage_on_every_read_tool`,
  `test_a_lost_surfaceable_item_makes_get_refuse_an_absent_id_as_damage`). So an
  agent can now tell "this project holds nothing" from "part of this project could
  not be read", and `knowledge.status` no longer publishes a positive
  `appliedMigrations` beside `itemCount: 0` without comment. The `status` cell is
  the member `0.1.0.dev2` recorded as "the fifth `SILENTLY_EMPTIED` member,
  carried to Milestone 6" when #19 stopped parsing every row: it still
  under-reports, because no read tool can repair a row it cannot parse, but it no
  longer does so in silence.

  **`knowledge.get`'s damage refusal is reworded, and deliberately says less.**
  `0.1.0.dev3` reported a project that "could not be fully read: its derived state
  holds a different number of migration-history rows than its own records expect".
  Either comparison now reaches that branch, so the text says only that the
  derived state "disagrees with its own records about what it holds" and names no
  record — a caller matching on the old substring stops matching. The SEC-13 rule
  that a withheld id and an absent id get the same message as each other is
  unchanged, and so is the pair that pins both directions
  (`test_an_absent_item_over_a_damaged_state_is_refused_as_damage_not_absence`,
  `test_an_absent_item_over_a_healthy_state_is_refused_as_absence`).

  **One position stays silent and is named rather than rounded away.**
  `(knowledge.search, knowledge_items, item_id)` moves neither count — the row
  keeps its `project_id` and its `status`, so both sides still count it — while
  the item → revision pointer the search walks is broken, and the tool answers one
  result short with no key. A count is not a checksum, and that is the shape a
  count cannot see. It is `UNDETECTED_UNDERREPORT` in
  `tests/integration/test_canonical_store_corruption.py`, an exact set of exactly
  one member: a second position appearing there is a failure, not an expectation
  to update.

  **`SILENTLY_EMPTIED` is replaced by three sets that partition the same
  question**, so no position can slide between outcomes unremarked.
  `DISCLOSED_AS_INTEGRITY` holds the nine positions that fire the key, keyed on
  the key's presence and nothing else — six of them fire while every integer in
  the response stays put, which is the detector's true shape;
  `DISCLOSED_BESIDE_A_SHRUNKEN_COUNT` holds the three that also shrink a published
  integer; `UNDETECTED_UNDERREPORT` holds the one that shrinks and says nothing.
  The sweep asserts the whole shrinking class equals the union of the last two
  (`test_exactly_these_positions_disclose_damage_as_integrity`,
  `test_exactly_one_position_answers_with_less_than_the_file_holds_and_says_nothing`
  — renamed and re-scoped from PR1's
  `test_exactly_these_positions_disclose_migration_history_damage_as_integrity`
  and `test_no_tool_answers_with_less_than_the_intact_database_holds`), which is
  what closes the seam a pair of independently-keyed sets would leave. That
  partition is over the swept single-cell positions, not a claim that the count
  catches every wrong answer: a status moved *within* `SURFACEABLE_STATUSES`
  changes the set's composition without its size and sets no key, and an item
  whose `current_revision_id` names another item's revision would disclose rather
  than under-report and is refused at read time by the read-back guard
  (`61747b3`), a mechanism distinct from this detector.

  **Neither side of the new comparison counts a row the caller may not read.**
  Both count `SURFACEABLE_STATUSES` — at build time in the `INSERT … SELECT`, at
  read time in the `COUNT` — so a `rejected`, `deprecated` or `superseded` row is
  on neither side.
  `test_the_integrity_signal_is_identical_across_a_withheld_only_difference` holds
  that whether the key appears is identical across two corpora differing only in
  twenty-five `rejected` items. Measured beyond what that pins: overwriting a
  `deprecated` row's `status` produces no key on any tool, where the same
  overwrite on an `approved` row fires it, and on a `draft` row it fires the key
  while the default `knowledge.search` answer stays unchanged — a draft is
  surfaceable even when the default answer omits it. The mirror of that is a
  recorded residual: overwriting an `approved` row's `status` to another
  surfaceable value moves neither count and sets no key, while the default answer
  loses a row it used to surface — the count measures the size of the surfaceable
  set, not its composition, and the moved row is caller-readable either way. The
  read cost keeps PR1's
  shape, `O(surfaceable)` over the covering index rather than `O(total)`, and
  `knowledge.status` spends no extra query at all: it sums the breakdown it had
  already read.

  **The remedy's first step deliberately does not clear this shape, and the
  residual is recorded.** `migrate apply` records only when it created the
  database or applied a migration; an apply with nothing pending must not
  re-record, because it is step one of the remedy this signal publishes and
  re-recording from the damaged state would manufacture its own all-clear
  (`test_a_pending_free_apply_does_not_re_record_over_a_damaged_state`,
  `test_an_apply_that_changes_the_store_records_the_new_count`). What stays open,
  recorded and not fixed: an apply that *does* have a migration to apply re-records
  over the state as it then is, so damage already present becomes the new
  expectation. It is the pointer's limit again — a count is not a checksum, and a
  writer can record only what it can read.

  The detector also found a defect in the test suite it does not own.
  `tests/integration/test_absence_proof.py` built its pair by hand with a pointer
  claiming one applied migration over a store holding none, so every response in
  that file had carried `integrity` since PR1 and each equality was comparing two
  damage reports. Both records are now written the way `migrate apply` writes
  them, and `_assert_the_pair_bites` fails an example that answers as a damaged
  project — the fifth way that file can be green while proving nothing.

### Changed

- **BREAKING — `SCHEMA_VERSION` 2 → 3: every existing state database is refused
  once, and one `theurian migrate apply` rebuilds it**
  ([#30](https://github.com/theurian/theurian/issues/30) PR2,
  [#24](https://github.com/theurian/theurian/issues/24)). The DDL gains the
  `project_integrity` table and makes the item → revision pointer a composite
  foreign key; `knowledge.status` therefore publishes `schemaVersion: 3` where it
  published `2`. A state database is derived and Git-ignored (ADR-0004) and is
  rebuilt rather than migrated (ADR-0017), so nothing authored is lost — but the
  rebuild is not automatic, and until it runs the three read tools refuse.
  BREAKING for a state database on disk, not for the wire: a published value
  moves and no field, type or tool name does, so `protocolVersion` stays
  `theurian/v1` (see *Changing this contract* in
  [`docs/protocol/mcp-tools.md`](../../docs/protocol/mcp-tools.md)).

  Measured end to end rather than argued, against a database `0.1.0.dev3` wrote
  with the real CLI and then read by this build: `knowledge.search`,
  `knowledge.get` and `knowledge.status` each refuse with

  ```
  theurian-state-f1711b98d302.sqlite was written at schema version 2, but this
  build uses 3. State databases are derived; rebuild with `theurian migrate
  apply` rather than migrating this file.
  ```

  and one `theurian migrate apply` answers `databaseCreated: true` with a new
  state hash — the schema version is an input to that hash, so the rebuilt file is
  `theurian-state-2e8880bf25be.sqlite` beside the old one — after which all three
  tools answer, `schemaVersion` reads `3`, and no `integrity` key appears. The old
  file is left on disk for `theurian index gc` (ADR-0017 decision 5), not deleted
  under a pinned `snapshotId`.

  A database written at *any* earlier version is refused *unread* rather than
  reinterpreted, which is what lets the new detector read a missing
  `project_integrity` row as damage rather than as "this file predates the table"
  (`test_a_pre_integrity_database_is_refused_unread_by_every_tool`, parametrised
  over every version below the current one).

- **The item → revision pointer is scoped to its project by the schema, not only
  by every read of it** ([#24](https://github.com/theurian/theurian/issues/24),
  closed). `knowledge_items.current_revision_id` referenced
  `knowledge_revisions(revision_id)` alone while `get_revision` and
  `list_revisions` both filter on `project_id` as well, so the two never met: a
  revision whose `project_id` moved — what a project id changing over an unchanged
  root does — left the item pointing at a row its own project-scoped read could no
  longer see, and `PRAGMA foreign_key_check` called the database satisfied.

  The key is now composite, `(project_id, current_revision_id)` referencing
  `knowledge_revisions(project_id, revision_id)` over a new unique index. Measured
  on SQLite 3.51.2, before and after, against the stranding `UPDATE`: before, the
  writer's own connection accepted it and `foreign_key_check` returned `[]`;
  after, the same statement is refused, and forced through with foreign keys off
  it is reported as `('knowledge_items', <rowid>, 'knowledge_revisions', 0)`.
  `test_a_revision_cannot_be_moved_out_from_under_the_item_pointing_at_it` holds
  both arms — the stranding move refused, a revision no item points at still
  movable — so a key that refused every write to that column would fail it too.

  A `NULL` `current_revision_id` still satisfies the key, because a composite
  child key with a NULL component imposes no constraint in SQLite; an item exists
  before its first revision is upserted. The four `# pragma: no cover` branches
  whose justification was "the pointer is a foreign key" now say what actually
  holds them, and the claim is true for the first time. INV-2's other half — that
  the revision belongs to the same *item* — is enforced above the database and in
  nothing the schema declares: by `KnowledgeItem.with_revision` in the domain, and
  by `append_revision` and `put_item` in both `MigrationWriter` adapters.

### Security

- **Derived state under `.theurian/state/` is served only if this installation
  built it (threat-model T-19, ADR-0004, SEC-7).** Everything there — the active
  pointers and the SQLite databases they name — is derived and git-ignored, but a
  repository contributor can force-add a doctored copy past that ignore (`git add
  -f`), and a victim who clones (or downloads the ZIP/tarball), `theurian project
  register`s, and serves over MCP **without ever running `theurian migrate
  apply`** was served the attacker's bytes: a `rejected` body relabelled
  `approved`, rows injected, titles and excerpts rewritten in the index.

  This is the self-consistent face the read-back guards cannot catch. The #30 PR2
  detector and the item → revision pointer guard above find a derived state that
  *disagrees with its own records*; this attacker authors both sides, at the
  current schema version, so there is no inconsistency to find. `active.json`'s
  `stateHash` binds the migration *set*, not the database bytes, and the database
  filename is derived from that hash, so the doctored pair is self-consistent by
  construction. The only property a repository author cannot forge is whether
  *this installation* built the artifact.

  **Control: an out-of-tree build-provenance anchor.** `theurian migrate apply`
  and `theurian index build` record the state hash and index build id this
  install produced for each project root in `THEURIAN_DATA_DIR/provenance.json` —
  beside the project registry, out of the repository tree where a contributor
  cannot write (`BuildProvenance`). Every MCP read path checks it before a byte of
  `.theurian/state/` reaches a caller: `_resolve` refuses a canonical state whose
  hash this install did not build (`verify_state_provenance`, covering
  `knowledge.get`, `knowledge.search`, `knowledge.status`); the ranked path stands
  aside from an index build id this install did not build. Both paths that
  generate an index are gated on source-index provenance, so neither launders a
  committed one: `index build` refuses to build *from* an unprovenanced canonical
  state, and — since commit `dc6aa79` — the withdrawal purge refuses to copy a
  committed index forward and record it when this install did not build the source
  index (`UNTRUSTED_SOURCE_INDEX`), the second laundering path review found. And
  `migrate apply` discards an unprovenanced database and rebuilds from the
  Git-tracked migrations; a committed `-wal` cannot replay because
  `create_database` refuses to write over an existing file, so the rebuild deletes
  the main database and creates a fresh one with no database for the sidecar to
  replay into — the sidecars are removed too, redundant defense-in-depth.
  Those migrations are vouched for by human PR review (T-1), not by FR-K5: on a
  fresh clone nothing has been applied, so FR-K5 has no recorded checksum to
  disagree with an attacker-authored migration, and re-derivation is safe because
  a reviewer read the migration diff.

  Delivery-independent by construction: the discriminator is "did this install
  build it", not "is it tracked by Git", so a clone, a ZIP download and a
  repackaged tarball are refused alike — which a `git ls-files` probe could not
  do. Pinned by `tests/integration/test_state_provenance.py`, whose closure
  invariant is one query against two checkouts: a checkout shipping derived state
  and one shipping none produce identical served knowledge, both refused until the
  state is built locally.

  **Effect on existing installs, and it is deliberate.** A project already built
  by a pre-`0.1.0.dev4` build has no provenance record, so the three read tools
  refuse it until one `theurian migrate apply` (then `theurian index build`)
  rebuilds it and records provenance — the same one-command rebuild the
  `SCHEMA_VERSION` bump above already requires, and nothing authored is lost
  (ADR-0004). The residual is recorded in T-19: provenance vouches for a hash, not
  for the database bytes, so replacing a database *after* this install built the
  matching hash is out of scope for this control and left to the schema gate, the
  #30 read-back guards, and the corruption checks.

  <!-- Advisory (GHSA) reference for T-19 to be filled at release, when the
  advisory ships; embargoed until then. -->

- **`knowledge.search` gains one `fallbackReason`, `index-unbuilt`**, emitted when
  the published index was not built by this installation and the ranked path
  therefore stands aside to the provenance-gated canonical scan. A new published
  *value*, not a new field, type or tool, so `protocolVersion` stays `theurian/v1`
  by the same rule the `SCHEMA_VERSION` entry above applies; the JSON Schema
  `schemas/mcp/retrieval-metadata.schema.json` adds the `const`.

## [0.1.0.dev3] - 2026-08-15

### Added

- **A present-only `integrity` object discloses derived-state damage on
  `knowledge.search`, `knowledge.get` and `knowledge.status`**
  ([#30](https://github.com/theurian/theurian/issues/30), PR1 of five positions —
  one closes here, four remain).

  ```json
  {
    "integrity": {
      "damageDetected": true,
      "remedy": "Run `theurian migrate apply` to rebuild the derived state from the Git-tracked migrations. If this signal persists, delete `.theurian/state/` and run `theurian migrate apply` again, then `theurian index build` to restore ranked retrieval; the state is derived, so nothing is lost."
    }
  }
  ```

  **The key is present only when a bounded check detected a discrepancy, and its
  absence asserts nothing.** No `integrity` key means the check did not fire —
  which is *not* "verified clean" and must not be read as one. There is
  deliberately no `damageDetected: false` form: the detector is incomplete by
  design, so a `false` token would publish "checked and clean" over a check that
  never made that claim, while absence cannot be misread without a caller
  inventing a claim of its own. `damageDetected` is therefore always `true` when
  present, kept explicit rather than reduced to a bare boolean so the object can
  gain a second field without a wire break. This is the same present-only shape
  `raptorPath` already uses (ADR-0008 decision 8), so the wire already branches on
  key presence; the schema declares the key as an optional property precisely so
  `additionalProperties: false` keeps holding when it appears
  (`knowledge-search-response.schema.json`,
  `knowledge-status-response.schema.json`; `knowledge.get` still publishes no
  response schema, [#20](https://github.com/theurian/theurian/issues/20)).

  **What PR1 detects is a migration-count mismatch, and nothing finer.**
  `expected` is the active pointer's own `migrationCount`, carried from the same
  resolution of `active.json` that chose the state database rather than re-read;
  `live` is `SELECT COUNT(*) FROM migration_history INDEXED BY
  idx_migration_history_sequence WHERE project_id = ?`. The state database is
  immutable once built, so a healthy project has `live == expected` and any
  difference is damage — `!=`, not `<`, so another project's rows reaching this
  one count too (`test_a_surplus_migration_row_is_damage_on_every_read_tool`,
  which is RED against `>=` in place of `!=`; the `WHERE project_id = ?` that
  keeps a *sibling* project's rows out of `live` is
  `test_a_sibling_projects_rows_in_the_same_file_forge_no_mismatch`). Both sides
  are pinned: a lost row surfaces the field from each of the three tools
  (`test_a_lost_migration_row_surfaces_integrity_from_knowledge_search`,
  `…_status`, `…_get`, each RED when that tool's emission is unplugged) and a
  healthy build emits it from none of them
  (`test_a_healthy_build_emits_no_integrity_field_from_any_tool`, and
  `test_a_re_apply_and_a_third_migration_leave_every_tool_silent` for the same
  silence after the pointer has moved). The wire form is validated against the
  schemas from a damaged project rather than a healthy one, where the optional
  key is never present to check
  (`test_the_damaged_captures_really_carry_the_optional_integrity_key`,
  `test_the_integrity_conformance_check_can_fail`).

  **The signal carries no bit about withheld content, and its cost carries none
  either.** It reads `migration_history`, a table no gate filters, so nothing it
  counts scales with the withheld set —
  `test_the_integrity_signal_is_identical_across_a_withheld_only_difference`
  measures whether the key appears across two corpora differing only in
  twenty-five `rejected` items and asserts it is identical for all three tools.
  The added per-request read on `knowledge.search` is answered from the covering
  index — SQLite plans `SEARCH migration_history USING COVERING INDEX
  idx_migration_history_sequence (project_id=?)`, so its cost is `O(migrations)`
  and independent of the corpus — which is what keeps it off the `O(withheld)`
  timing channels [#19](https://github.com/theurian/theurian/issues/19) and
  [#158](https://github.com/theurian/theurian/issues/158) closed
  (`test_the_search_integrity_count_is_answered_by_a_covering_index`, pinning both
  the `INDEXED BY` hint in the statement the store really runs and the plan
  SQLite produces for it). The plan assertion requires the *seek* — `SEARCH`, the
  index name, and the `(project_id=?` that opens the constraint list — because the
  index name alone appears on a `SCAN` line too: reversing the declared columns to
  `(sequence, project_id)` keeps the name and walks every project's migration
  entries at 172× the work, measured, and passed the earlier substring. The same
  strengthening was applied to `idx_items_status`'s assertion in
  `test_status_count_is_answered_by_a_covering_index`.

  **`knowledge.get` distinguishes damage from absence in its refusal message.**
  It refuses with a bare string and no field, so the distinction lives in the
  text: where the check reports damage, an item it could not read is now reported
  as a project that "could not be fully read: its derived state holds a different
  number of migration-history rows than its own records expect", not as an item
  that is not present. Both directions are pinned, because either alone is
  satisfied by a tool that says one thing always
  (`test_an_absent_item_over_a_damaged_state_is_refused_as_damage_not_absence`,
  `test_an_absent_item_over_a_healthy_state_is_refused_as_absence`). The SEC-13
  rule that a withheld id and an absent id get the same message is unchanged.

  **What this does not cover.** Four `SILENTLY_EMPTIED` positions remain and are
  carried to PR2 — `(knowledge.search, knowledge_items, item_id)`,
  `(knowledge.search, knowledge_items, project_id)`,
  `(knowledge.status, knowledge_items, project_id)`,
  `(knowledge.status, knowledge_items, status)`. A migration-count check cannot
  see any of them: they empty a result rather than the migration history, so
  `live` still equals `expected` and the key stays absent exactly as on a healthy
  project. That is what "absence asserts nothing" means in practice.

  **The `remedy` names a fallback, because one command does not cure both
  directions.**
  `theurian migrate apply` is the cheap cure and comes first — measured, it clears
  a lost row, a sentinel in `migration_history.project_id`, and an over-counting
  pointer. It clears nothing for a *surplus* row: every authored migration is
  already applied, so three consecutive runs exited 0 with `applied: [], changed:
  false` and left the key present. Deleting `.theurian/state/` makes the next apply
  rebuild the database (`databaseCreated: true`, key absent), and `theurian index
  build` is named third because that deletion takes the published retrieval index
  with it — measured, `retrieval.indexed` is `false` with `fallbackReason:
  "no-index"` until the rebuild runs, so without the third step "nothing is lost"
  would be false. The efficacy is measured, not yet pinned by a test.

  Two further limits, measured rather than assumed, both recorded in
  [the threat model](../../docs/security/threat-model.md) under T-17:

  - **A pointer whose `migrationCount` is wrong in the same direction as the rows
    is undetectable.** The check compares two derived numbers against each other,
    never against the Git-tracked migrations, so a state that lost its migration
    row *and* a pointer recording `0` agree — measured, all three tools answer,
    `knowledge.status` publishes `appliedMigrations: 0` against a project holding
    one applied migration, and `migrate status`, `migrate apply` and `index build`
    all exit 0. A pointer wrong on its own does fire the key (measured at `2` and
    at `0` against one live row), but the signal cannot say which side is wrong,
    and `appliedMigrations` publishes the pointer's number either way.
  - **Corrupt `migration_history.applied_at` or `.sequence` is seen by no shipped
    surface at all** — measured: all three tools answer cleanly, and `migrate
    status`, `migrate apply` (with and without a new migration to apply) and
    `index build` all exit 0.

### Changed

- **`knowledge.status` reports `appliedMigrations` from the active pointer's
  `migrationCount`, not from a live count of `migration_history` rows**
  ([#30](https://github.com/theurian/theurian/issues/30) PR1). On a healthy
  project the two are equal by construction and no response changes. They diverge
  only under damage, and there the pointer's count is the authoritative one:
  before this change a corrupt `migration_history.project_id` dropped every row
  out of the `WHERE`, so the tool answered `appliedMigrations: 0` against a
  project that had applied several — a successful, false statement, and the
  `SILENTLY_EMPTIED` position PR1 closes. The live count is now compared against
  the pointer and any difference — in either direction — disclosed through
  `integrity` instead of published as the answer
  (`test_a_corrupt_migration_project_id_is_disclosed_not_silently_emptied`;
  `test_no_tool_answers_with_less_than_the_intact_database_holds` holds the set at
  four members and goes RED if the field starts shrinking again). **A behaviour
  change for a caller that compares this field against a row count it obtained
  some other way**: over a damaged state the two now disagree by design, and the
  `integrity` key is what says so.

- **A negative `migrationCount` in `.theurian/state/active.json` is now refused
  at parse time, where it used to be published**
  ([#30](https://github.com/theurian/theurian/issues/30) PR1). `knowledge.status`
  reports that field as `appliedMigrations`, whose schema declares `minimum: 0`,
  and `ActiveState.from_json` accepted any integer. Measured before the fix:
  `migrationCount: -5` reached the wire as `appliedMigrations: -5`, so the
  response violated its own published contract — and a strict client rejects the
  whole response, including the `integrity` key riding along on it to say the
  state is damaged. The one field reporting the damage was thrown away by the
  damage. It is now a `DomainError` at parse time, converted by
  `read_active_state` into the `ProjectError` a corrupt pointer already produced,
  so all three read tools refuse with "Malformed active state pointer:
  migrationCount is negative (-5)" and the delete-the-pointer-and-re-apply cure
  (`test_a_negative_migration_count_is_refused_by_every_read_tool`). **A behaviour
  change for anyone who hand-edits the pointer**: a value that used to be answered
  with is now a refusal. Only negative values are refused — a non-negative integer
  that is simply wrong is still accepted, which is the one-way limit recorded
  above and in the threat model.

- **`knowledge.status` no longer refuses over a corrupt
  `migration_history.migration_id` or `checksum`; it answers cleanly**
  ([#30](https://github.com/theurian/theurian/issues/30) PR1). The refusal was a
  side effect of parsing rows the tool no longer reads: it used to call
  `applied_migrations`, which converts both cells and raises on a damaged one,
  and it now calls a bare `COUNT` that interprets neither. Measured on the
  corruption corpus: with a sentinel in either column the tool returns its six
  keys and no `integrity` — the count is unaffected, so the check does not fire —
  while `applied_migrations` over the same database still raises
  `StateDatabaseUnreadableError`. No published `knowledge.status` field is
  derived from either cell, and `migrate status` and `migrate apply` still exit 4
  over both, so migration tamper is still detected where it is acted on. Recorded
  rather than marked BREAKING because no published contract promised the refusal
  and no field, type or tool name changed (see *Changing this contract* in
  [`docs/protocol/mcp-tools.md`](../../docs/protocol/mcp-tools.md)).

  **It is a real reduction in what the read tools notice, and it is now pinned as
  an exact six-cell set** rather than left to a reader:
  `ANSWERED_CLEAN_OVER_A_DAMAGED_CELL` in
  `tests/integration/test_canonical_store_corruption.py` names all three tools
  over both cells, and
  `test_exactly_these_positions_answer_cleanly_over_a_cell_the_cli_calls_tampering`
  reads it against the CLI sweep — so the silence is only green while `migrate
  status` and `migrate apply` keep exiting non-zero on the same cells, and a read
  tool that starts refusing again fails it too. Its counterpart
  `test_exactly_these_positions_disclose_migration_history_damage_as_integrity`
  keeps that set from going vacuous by naming the three positions
  (`migration_history.project_id` on each tool) that must fire the key.

- **`theurian setup` and `theurian auth rotate` rewrite only the Theurian-owned
  block in `<data_dir>/env`, and leave every other byte of that file alone**
  ([#128](https://github.com/theurian/theurian/issues/128)). Both used to render
  the whole file and truncate whatever else was in it, and `probe_env_reference`
  reported `Missing` on any difference — so a line somebody had added to a file
  whose own header says "Sourced by your shell profile" was destroyed with no
  diff, no backup and no mention in `changedPaths`, on every run of a command whose
  contract is that running it twice changes nothing (FR-L2). §6.2 row 7 of the
  requirements analysis had required "rewrite the Theurian-owned block only"
  throughout. The block is delimited by `# >>> theurian >>>` and
  `# <<< theurian <<<`, spelled exactly as the pair `theurian init` writes into a
  `.gitignore` so that someone who has seen one managed block recognises the
  other, and the merge is computed *before* the file is opened — so a file that
  cannot be delimited is never opened at all.

  **What an operator upgrading from `0.1.0.dev0`–`dev2` sees.** Those versions
  wrote the whole file as a fixed rendering of the data directory, so the first
  `setup` or `auth rotate` after upgrading recognises that rendering and replaces
  it *in place* with the marked block: one `export THEURIAN_MCP_TOKEN` afterwards
  and not two, with lines added before it still before it and lines added after
  it still after it. Appending the block beside the old rendering would have left
  two assignments naming different paths once a data directory moves — the shell
  taking whichever came last, while setup reported the machine converged.

  **Recognition is exact, and deliberately not fuzzy**: those lines must be
  consecutive and whole, and must name *this* data directory's token path. A
  rendering somebody edited, and one written for another installation, are
  therefore left alone and the block is appended below them — two visible
  exports, the shell keeping the block because it reads it last. That is the
  honest answer for a line somebody changed on purpose; matching it loosely is
  what glued the block over half of one in the first cut.

  **A marker is a whole line, and the first cut of this fix did not do that.**
  Review found it substring-based: `str.find` opened the span at the first
  *occurrence* of the start marker, so `echo "everything between
  # >>> theurian >>> and here"` opened one and the rewrite cut that line in half,
  leaving an unclosed quote that poisons every line after it in a sourced file;
  the count that was supposed to catch a second start looked only at what
  followed the *end* marker, so `S`, a user's line, `S`, the block, `E` — what
  repairing an unterminated block by pasting a fresh one under it leaves — was
  swallowed whole; and the dev0–dev2 rendering was matched as a substring, so
  `export THEURIAN_MCP_TOKEN  # my note` had the block spliced over its first
  half and the leftovers glued onto the end marker. Measured over every file a
  start marker, an end marker and a user's line build up to five lines long, 363
  arrangements: **39 took the wrong refusal decision and 16 of those reported
  success while dropping 19 of the user's lines**, one of them an
  `export AWS_SECRET_ACCESS_KEY`, with the run reporting `converged` and the
  re-probe `satisfied`. What shipped matches whole lines — split on `\n` alone,
  a trailing `\r` dropped, so a CRLF file delimits — and counts the start lines
  over the whole file *before* choosing a span, which is what makes the second
  one's position irrelevant.

  **A behaviour change for a file whose markers do not delimit exactly one
  block** — two or more start lines anywhere in the file, or a start line with no
  end line after it. That used to be overwritten; it is now `Conflicting`,
  because once the delimiters disagree setup cannot tell which lines are its own.
  An *end* line with no start above it, and a second end line, are not that: they
  delimit nothing, and they are kept like any other line. `setup` writes nothing
  there, declares no path for it, and stops at consent; `--approve-conflicts`
  applies the rest of the plan and still leaves that file byte-identical,
  finishing `degraded` with the step named in `warnings`. `auth rotate` leaves the
  file untouched, **still rotates the token**, and prepends one line to
  `nextSteps` naming the file to repair — an exposed credential outranks a comment
  marker, and the token has already been replaced by the time that file is
  reached. The same holds when the OS refuses the write — a read-only checkout, a
  file another account owns, a full disk: rotation completes and `nextSteps`
  carries the exception's class name and never its message, which holds
  `strerror`, the errno and on some platforms a second path. The conflict detail
  carries the two marker strings, the path they are in and the command to re-run,
  and no other line out of that file, because `doctor --report` publishes it
  (O-3, SEC-6).

  **A run can be right about the block and wrong about the machine, and now says
  so for the direct assignment forms.** A shell keeps the last assignment it
  reads, so a line *below* the block assigning `THEURIAN_MCP_TOKEN` again is what
  gets exported while the probe — deliberately blind to lines it does not own —
  reports the block current. That line is not Theurian's to edit and it is not a
  conflict either; the step stays `satisfied` and carries a caveat, which
  `_reservations` turns into a warning, so the run ends **`degraded` where it
  used to end `converged`**. The warning names the path, the variable and the
  start marker to move the line above, and never the line itself. A bare
  `export THEURIAN_MCP_TOKEN` or a commented-out assignment is not an override
  and leaves the run converged. Currency is asked first, so a block that is both
  stale and shadowed is rewritten and *then* reported, rather than reported
  instead of fixed.

  **What finds that line is a heuristic, and the scope is published rather than
  implied.** `contains_shadowing_assignment` reads one line at a time and
  recognises a first word spelled `THEURIAN_MCP_TOKEN=…`, or that word after
  `export`, `declare`, `typeset` or `readonly`. It is wrong in both directions,
  measured with `/bin/bash` sourcing the block and then the line: an `&&` list,
  an `if`/`then`, a `{ }` group and an `eval` each assign the variable while the
  run stays **silent and `converged`**, and an assignment inside a quoted heredoc
  *body* draws the warning although the shell keeps the block's value. The four
  misses are pinned **as** the recorded boundary, each through a real shell
  rather than restated against the function, so a change that starts warning on
  one of them has to arrive with an argument and update the pin deliberately. Not
  extended, and that is the decision rather than a to-do: what a line does is
  settled by the shell at run time — `eval` takes a string that need not exist
  until then, and a heredoc body is not shell at all — and a probe that runs
  somebody's shell profile is not a probe. The residual is carried in the wording
  instead: both published sentences say the line *appears* to assign and the
  block *appears* to be overridden, which is what keeps the heredoc case honest,
  and both are pinned — dropping the hedge from the `summary` alone, the sentence
  a reader who stops at `satisfied` sees, survived all 2,442 tests while the
  `detail`'s was held. **What stays unqualified** is the other arm's summary,
  "`…/env` exports `THEURIAN_MCP_TOKEN` by reference" — and the `converged` the
  run reports beside it. On a machine using one of the four evading shapes both
  are true of the block and incomplete about the machine; measured through the
  real CLI, `theurian doctor --json` publishes that summary, zero warnings and
  exit 0 while `bash` exports the later line's value. Recorded here and in §6.2
  row 7 rather than fixed, because no line-level rule can tell that machine from
  a healthy one.

  **`theurian doctor` and `theurian setup --dry-run` now carry that warning
  too**, which is the caller-visible half of this. The sentence was built in the
  verification pass alone, so on one machine `theurian setup` said `degraded`
  with the caveat while `theurian doctor --json` said `"warnings": []` and exited
  0 — the caveat sitting in the payload the whole time as the `detail` of a step
  whose status reads `satisfied`, which is where a reader stops. Both surfaces
  that publish a plan now build their warnings with the same `_reservations`, so
  a shadowed machine gains one `env-reference: …` line in `doctor --json`,
  `doctor --report` and the `--dry-run` plan the plugin renders. Nothing else
  moved, deliberately: a reservation is a finding with no work attached, so
  `healthy`, `problemCount` and the exit status stay tied to what setup would
  change and what needs consent, and a machine whose only finding is a line
  Theurian will not touch still exits 0. The reports built on the way *past* a
  plan — `aborted`, `awaiting_consent` and `halted` — carry no reservations, and
  that is recorded rather than closed in `_reservations`' docstring: each hands
  the reader a larger question first, and the step's `detail` still travels with
  the report.

  **Line endings are bytes somebody chose.** Both writers read and write with
  newline translation off, so a file edited on Windows keeps its `\r` bytes
  outside the block — including a `\r` inside a quoted value, which translation
  would turn into a newline and split the assignment in two. The block itself is
  written with `\n`, so a block that arrived with CRLF markers is normalised on
  the first run and is a fixed point after that.

  **The same class in `theurian init`.** `ensure_gitignore` writes an
  identically-spelled block into a repository's `.gitignore` with the same
  `str.find` and no count of the start markers, so a file holding two of them —
  what resolving a merge conflict by keeping both sides leaves behind — had every
  rule between them swallowed by the rewrite and reported as `changed: true` with
  nothing else said. It now matches whole lines, counts the starts first, keeps a
  CRLF `.gitignore`'s line endings, and refuses both undelimited shapes. **The
  refusal also reaches a person differently**: it used to arrive as a Typer
  traceback with the remedy buried in it, because the only `except` in
  `init_command` wrapped context resolution, and it is now `error: …` plus a
  remedy on stderr with exit 1. A refused run leaves the `.theurian/`
  directories it had already created; nothing else is written.

  **Not marked BREAKING, and here is the one place it is arguable.** No MCP tool,
  field, type or name changed, and no published contract promised any of this
  (see *Changing this contract* in
  [`docs/protocol/mcp-tools.md`](../../docs/protocol/mcp-tools.md)). What did
  change for a script is exit status on two inputs. `theurian init` over a
  `.gitignore` holding two start markers used to exit 0 having silently eaten the
  rules between them, and now exits 1 (an *unterminated* block already failed,
  though as a traceback). `theurian setup` over an env file in either state used
  to rewrite it and count the step converged; it now stops the whole run at
  consent — exit 5, `EXIT_NEEDS_CONSENT` — before `_apply` is reached, so nothing
  at all is written. Both are the fix, not a side effect of it.

  Idempotence is now measured on the file rather than on the report: a converged
  second run does not reopen it at all, witnessed on the mtime, so a run that
  rewrote identical bytes fails the pin. The write goes *through* the inode
  rather than temp-and-rename, because this file is a symlink into a dotfiles
  repository on plenty of machines, and through an `io.BufferedWriter`, because a
  short write here would now destroy lines Theurian did not author. 74 tests, 97
  collected cases: `tests/unit/test_env_file_merge.py` (33 tests, including one
  that sweeps all 363 arrangements against a rule read off the symbols rather
  than off the code), `tests/integration/test_setup_env_file.py` (21, driving the
  real `SetupService` over real files because the defect lived in the seam — a
  probe asking one question while the apply performs a different write is exactly
  what shipped, two of them — five cases — asking a real `bash` what a file
  exports rather than asking the heuristic to agree with itself),
  `tests/integration/test_init_gitignore_block.py` (9, through the real
  `theurian init`), `tests/integration/test_auth_rotate.py` (6 added),
  `tests/integration/test_setup_cli.py` (3 added, for the `doctor` and
  `--dry-run` parity above), and `tests/integration/test_setup_service.py` (2
  added). The last two of those close pins that were simply absent: nothing
  asserted `CONVERGED` by value — replacing `_verify`'s state choice with an
  unconditional `DEGRADED` passed the whole suite, because `succeeded` is true of
  both — and nothing asserted the *status* half of the reservation test, so
  reporting every explained `NOT_APPLICABLE` step as a warning, which would turn
  the supply-chain note on row 3 into something wrong with this install, passed
  it too. Four parser decision points gained pins the same way: a marker line
  with a trailing space is not a marker, in the env file **and** in the
  `.gitignore` scan beside it; the block is searched for before the dev0–dev2
  rendering; markers that cannot be resolved are refused even where that
  rendering is also present; and the probe asks currency before shadowing.

  **Round two re-measured the parser rather than re-reading it.** A second sweep
  over an extended alphabet, 2,800 shapes, found no arrangement that loses a line
  outside the block or takes the wrong refusal decision; the two writers were
  compared over 84 file-state combinations and produce identical bytes; and the
  six numbers this entry publishes — 363, 229 refused, 134 merged, and the first
  cut's 39, 16 and 19 — were re-measured exact. Those are review measurements and
  not suite tests: what guards the rule from here on is the 363-arrangement
  sweep, which asserts its own population size so a shrunken alphabet fails
  rather than passing quietly.

  **Two remedies now describe the state they are reached from.** The `.gitignore`
  refusal said "Add `# <<< theurian <<<` where the block ends" to a person whose
  file appears to have that line already — a marker is matched as a whole line,
  so a trailing space is the likeliest way to reach an unterminated block, and
  the remedy sent them looking for what was in front of them; it now says what
  the line must be, trailing space and all. `auth rotate`'s `OSError` remedy
  offered "an older block, or readable by other accounts" for a file that can
  also be left *empty*, since the `open` truncates before the write that failed —
  reproduced under `RLIMIT_FSIZE`, 588 bytes in and 16 out — and now admits that
  state. A third correction is not user-visible: the comment above the rotation's
  env-file refresh enumerated the shapes it handles, absent, stale and
  pre-marker, without the residual — a line below the block that assigns the
  token again survives the rotation and produces the 401 anyway, and `doctor` is
  what reports it.

  This supersedes one sentence of `0.1.0.dev2`'s `changed_paths` entry below. The
  env file's truncation is still disclosed on the arm that motivated it, but
  "what it replaced is preserved nowhere" no longer holds: what a completed write
  puts back includes the lines the run did not author. What stays unpinned, as
  there, is the window between the truncation and the write's last byte.

### Security

- **A reused `revisionId` across two items no longer leaks a withheld item's body**
  ([GHSA-7997-g35f-q59h](https://github.com/theurian/theurian/security/advisories/GHSA-7997-g35f-q59h);
  fixed in [`67c0e81`](https://github.com/theurian/theurian/commit/67c0e81)).
  **BREAKING (state database schema).**
  In 0.1.0.dev0–0.1.0.dev2 a migration that reused an existing `revisionId` under
  a second `itemId` — the shape a copy-pasted `upsertRevision` block produces —
  pointed the second (approved) item's current revision at the first item's
  revision row. When that first item was withheld (for example `status:
  rejected`), its full body — title, source anchors, and any secret that caused
  the rejection — reached `knowledge.get` and `knowledge.search` for a caller who
  requested the *approved* item's id. Requesting the withheld id directly was
  still correctly refused; the reuse bypassed that gate, and `migrate validate` /
  `migrate apply` reported nothing.

  **Fixed** by making `append_revision` refuse to treat a reused `revisionId` as
  an idempotent no-op when the stored row belongs to a different item — a revision
  id names one item for the life of a project — with a symmetric store-level guard
  in `put_item` that refuses a `current_revision_id` naming another item's
  revision. The state database `SCHEMA_VERSION` is bumped from 1 to 2 (an input to
  the derived-state hash), so a state database written by an affected version — the
  old shape, opened and served regardless of provenance — is refused on open and
  rebuilt from the Git-tracked migrations on the next `theurian migrate apply`. The
  derived state carries no data that is not recoverable from those migrations, so
  the rebuild strands nothing. If the migration set itself encodes the reuse, that
  rebuild refuses it (exit 4, naming the reused `revisionId`) until the operation
  is given its own id. **Updating the build alone does not remediate a database an
  affected version already wrote; run `theurian migrate apply` after upgrading.**

  Affected: `theurian` 0.1.0.dev0, 0.1.0.dev1, 0.1.0.dev2. Fixed in 0.1.0.dev3.

- **The substring-search fallback's withheld-count timing channel is closed**
  ([#158](https://github.com/theurian/theurian/issues/158)), the twin of #19's
  `knowledge.status` fix. `knowledge.search`'s unranked fallback
  (`mcp/search.py::_scan`) used to read every item with `list_items` (`SELECT *`,
  no status predicate) and drop the withheld rows in Python, so its response time
  scaled with the withheld count and a caller with a stopwatch could recover by
  subtraction exactly what `count` withholds (T-17). It now resolves the
  surfaceable statuses and reads through `SqliteCanonicalStore.list_items_by_status`,
  whose `status IN (...)` is forced through the `idx_items_status` index, so a
  withheld row is never materialised and the read cost is independent of the
  withheld count: SQLite VM steps stay flat at 119–120 across 0/50/300/1,000
  withheld where the old scan went 63 → 913 → 5,163, and the result set is
  byte-identical. Pinned by
  `test_the_substring_scan_reads_items_through_idx_items_status`,
  `test_the_substring_scan_materializes_the_same_rows_however_many_are_withheld`,
  and `test_the_substring_scan_never_surfaces_a_retired_item_even_with_include_unapproved`
  in `tests/integration/test_mcp_tools.py`. Two trades are recorded, not fixed: a
  corrupt `status` cell on this path is now silently dropped by the SQL filter
  rather than crashing the Python `may_surface` parse it replaced — the same
  crash → silent-drop trade #19 made for `knowledge.status`, carried with that
  integrity class as [#30](https://github.com/theurian/theurian/issues/30) — and
  the fallback's rows-and-memory page bound stays a deferred DoS residual (T-6),
  since bounding it changes the search fallback's published surface.

- **The unresolved-project error now bounds the `projectId` it echoes**
  ([#17](https://github.com/theurian/theurian/issues/17)), the last member of the
  error-echo amplification class. `mcp/tools.py::_unresolvable` interpolated the
  caller's raw `projectId` into the "not registered" message with no length bound:
  `_resolve` runs before any `ProjectId` is constructed, so a 2,000,000-character
  id produced a 2,000,141-character message — an ~1× amplifier of the caller's own
  bytes. An unregistered id longer than `MAX_IDENTIFIER_LENGTH` (200, the ceiling a
  `ProjectId` cannot exceed, duplicated in the JSON schemas as `maxLength: 200`) is
  now reported by its length and never echoed; a well-formed unregistered id within
  the ceiling is still named so a typo stays visible. That matches the discipline
  `MAX_QUERY_CHARS` already holds for `query` and `ItemId` for `itemId`, so all
  three error-echo members are bounded. Not a disclosure — the caller only ever
  gets back bytes it sent (the `Registered:` list is the daemon's own registry
  contents, SEC-13). Pinned by
  `test_an_over_long_project_id_is_reported_by_length_not_echoed` and
  `test_the_project_id_echo_is_named_up_to_the_id_ceiling_then_by_length` in
  `tests/integration/test_mcp_tools.py`; see T-6 in
  [the threat model](../../docs/security/threat-model.md).

## [0.1.0.dev2] - 2026-08-12

### Added

- **`knowledge.search` takes an optional `asOf`** (RFC 3339, any explicit
  offset), pinning results to FR-R1's validity-window axis at that moment
  ([#63](https://github.com/theurian/theurian/issues/63) phase 2). #63 itself
  closes in phase 0 below; enforcing the three axes still deferred — tenant,
  ACL group and sensitivity — is its successor,
  [#119](https://github.com/theurian/theurian/issues/119).
  Additive: omitting `asOf` filters on nothing more than before this parameter
  existed, on both answer paths. A permanent default filter was considered and
  rejected, because it would make the published `freshness.isWithinValidity`
  field constant-`true` on a healthy index and give the ranked path a
  stale-index statistics residual with no way to turn off (see T-17a in
  [the threat model](../../docs/security/threat-model.md)). `asOf` is a
  refinement rather than a withholding: everything one call excludes is
  returned to the same caller by the identical query with the parameter
  omitted, so it opens none of the disclosure guarantees this project holds
  for a document a caller may not read. `knowledge.get` deliberately does not
  take it — see its docstring for why "not found" would be a worse answer than
  `isWithinValidity: false`. An unparseable `asOf` is a clean tool error.
  Both answer paths compare the pinned moment through the identical
  `ValidityPeriod.contains`, in Python, on timezone-aware `datetime` values —
  no timestamp is ever compared as SQLite text — and the ranked path applies
  it only after its retriever depth-doubling loop has already stopped asking
  for more, so a pinned moment cannot change how many times a request reads a
  retriever.

- **`knowledge.search` retrieves *through* the RAPTOR forest, and a hit carries
  its `raptorPath`** (ADR-0008 decision 8, FR-R3, FR-R5). A summary retriever
  matches the forest's summary nodes and descends to the leaf chunks beneath a
  matched node, so a query about a theme reaches a document that clusters under
  it without containing the words — fused with the leaf retrievers by reciprocal
  rank fusion. **Additive wire-contract change**, pre-1.0 and with no external
  consumers: `foundBy` gains the value `summary`, naming a leaf reached through
  the forest (a hit found both directly and through it carries `summary` beside
  the leaf retriever), and every hit over a `--raptor` index carries a
  `raptorPath` — the leaf's summary ancestry, catalog root to leaf, one
  `{nodeId, level, title}` per node, `title` the node text bounded by the same
  `excerpt` as every body on the wire. Absent, not empty, over a chunk-only
  index, so a client tells "no forest here" from "here is the path".
  `system.capabilities.raptor` flips to `true`: this build reads the forest, and
  a project's own forest is discovered per response through `raptorPath`'s
  presence, exactly as `hybridRetrieval` is. Naming `summary` as its own value
  rather than folding it under a leaf retriever is deliberate — hiding a distinct
  retrieval mode would be a false published claim about how a hit was found.

  **The disclosure gate is unchanged, and doubled at the forest.** Routing
  decides which leaves are *candidates*; it never decides whether a gated row may
  surface (SEC-13, T-15). The summary node match is filtered on the same scope
  the leaf retrievers apply — Project, and status unless the caller asked for
  drafts — so a draft-scope summary is not even traversed on a default query; the
  descended leaves are filtered again and then re-cleared against the canonical
  store, as every candidate is. A `raptorPath` is built only for a leaf that
  cleared that gate, and a summary node's children share its six-component scope
  by construction (ADR-0008 decision 1), so a title carries no content from a
  scope the caller's leaf is not in, and a withheld leaf contributes no result
  and no path — its ancestors' titles never reach the wire. A title's build-time
  staleness is the same residual every excerpt carries (T-17a), not a new
  channel.

- **`theurian index build --raptor` derives and stores the RAPTOR forest**
  (ADR-0008, `application/forest_builder.py`). The three tiers decision 2 names,
  deepest last: a Document node per item revision over that revision's chunks, a
  Domain node per `kind` within a scope over those (or several, once a kind is
  large enough to fan out — below), and a Catalog node per scope over those. **Without the flag a build writes zero node rows** — decision 10's
  opt-in as a hard guarantee rather than a filter someone has to remember, the
  shape `--include-unapproved` already has for drafts, and held by
  `test_a_build_without_the_raptor_flag_writes_no_summary_nodes`. A level with
  fewer than `minChildrenPerSummary` children is skipped, because a summary of
  one document is a paraphrase; `maxLevels` caps the tiers rather than refusing a
  larger value, so a valid config stays buildable.

  **`kind` is the Domain-tree discriminator, and `namespace` is written for the
  first time.** A tree's scope already fixes the namespace, so decision 2's "one
  namespace or kind" reduces to `kind` inside one — without it a scope holds
  exactly one Domain tree, the Catalog tier always has a single child, and three
  levels are structurally unreachable. `IndexableChunk` carries `kind`, and at v4
  `chunks` gained no column for it — nothing queries it, and the build that
  produced the chunk consumed it in memory. (The purge-recompute change below adds
  `chunks.kind` at v5, because a purge re-derives from the published index and has
  to read `kind` back.) `chunks.namespace` existed as `NOT NULL DEFAULT
  ''` and was never populated; a forest derived from those rows would have
  partitioned on five components while claiming six, so it is populated now and
  pinned on a *default* build by
  `test_a_chunks_namespace_carries_the_value_its_item_was_registered_with`.

  **Two new store writes.** `IndexStore.add_nodes` inserts the forest and its
  `node_derivation` edges in **one transaction, nodes first** — the foreign keys
  are immediate, so an edge before its node is refused rather than resolved at
  commit, and a commit between the two statements would expose the exact
  unprovenanced state `_verify` refuses to publish. `add_node_embeddings` is
  separate from `add_embeddings` because `embeddings.chunk_id REFERENCES chunks`
  and a node id is not a chunk id. Every node is embedded or none, for the reason
  chunks are: dense retrieval would rank the embedded half and silently never
  surface the rest. `--no-embeddings` reaches the forest too, or the flag would
  mean half of what it says.

  **`SUMMARY_MAX_TOKENS` is a constant and never a share of the corpus**
  (decision 6's amendment). A builder dividing a shared budget by document count
  would move a visible node's text when a withheld document was added or removed,
  while the summariser itself read nothing it should not — a property only the
  caller can hold, since a summariser is handed the number and never the recipe.
  It is one chunk's worth, the chunker's target passage priced at the estimator's
  characters-per-token, and it is the one `ForestOptions` field with no config
  key. `test_the_summary_budget_is_a_constant_and_not_a_share_of_the_corpus`
  holds it with a recorder that sees what each call was charged.

  **Node identity is content-addressed.** `node_identity(tree_id, level, the
  children's content hashes sorted)` in `domain/raptor.py`, pinned against a
  literal by `test_a_node_id_is_pinned_to_its_exact_join_order_sort_and_encoding`
  — a literal rather than a recomputation, because the forest tests recompute the
  recipe from the function they are checking and would pass together with a
  builder that agreed on a *different* one. `tree_id` adds the tier and the
  within-scope partition on top of the scope key, without which two items holding
  duplicate content mint one id for two nodes. `IndexableNode` refuses a node
  whose declared child scopes do not stand one per source, which is the half
  `SummaryNode` cannot see, and the builder derives each declaration from the
  chunk or node it summarises — together discharging ADR-0008's "each declared
  child scope is derived from the child it summarises".

  **Default off in both config surfaces** (decision 10):
  `schemas/config/project-config.schema.json` declares `raptor.enabled` as
  `default: false` with the reason on the property, and
  `examples/sample-project/.theurian/config.yaml` sets it `false`. Each is pinned
  by its own test, because validating the example against the schema cannot catch
  a disagreement — both values are valid booleans. Nothing in `src/` reads
  `.theurian/config.yaml`, so **the CLI flag is the switch and the config key is
  not**; `ForestOptions` carries the schema's own defaults for `maxLevels` and
  `minChildrenPerSummary`, pinned against that file so the two cannot drift
  before a loader exists. `system.capabilities` still reports `raptor: false`,
  which ADR-0008 decision 10 predicted would flip here and should not: that flag
  answers what a *caller* can get, and nothing node-derived reaches a response.

  **The withdrawal purge is exercised over rows the builder wrote**, for the
  first time — every node row the suite had purged until now was inserted with
  raw SQL by the test that purged it. Withdrawing one item of three takes its
  Document node and, by the upward closure, the Domain node standing on it, while
  the two unaffected Document nodes survive; `nodes_fts` and `nodes_trigram` are
  read back through `fts5vocab` and hold no term of the withdrawn document. This
  CL's purge was **delete-only**; ADR-0008 decision 9's re-derivation of each
  affected tree, and the two-corpus equality that is the only thing able to check
  it, land in the purge-recompute change below.
  `test_rebuilding_the_same_state_produces_a_byte_identical_forest` holds the
  precondition that equality rests on.

  **The Domain tier fans out above a per-node bound.** A Domain node summarises
  one node per document of its kind, so its input is the one tier's that grows
  with the corpus rather than with the number of kinds — a same-kind corpus past
  roughly a thousand documents drove one Domain node over the extractive default's
  `MAX_TOTAL_INPUT_CHARS` and refused the build. `MAX_CHILDREN_PER_DOMAIN` (500)
  caps it: above it a kind's Document nodes, sorted by `node_id`, split into
  contiguous batches of at most 500 — a full batch is 500 × 250 × 4 = 500k
  characters, half the limit — each its own Domain node whose discriminator is
  `kind` joined by `#` with the partition index, which a `KnowledgeKind` value
  cannot contain, so a partitioned discriminator never collides with a bare kind.
  Every Document node stays under exactly one Domain node and the Catalog
  summarises the batches;
  `test_a_domain_tier_over_many_documents_fans_out_into_bounded_batches` holds it.
  The Catalog tier is not itself fanned out, so a scope holding one kind at
  hundreds of thousands of documents would still meet the limit at the Catalog
  node — a ceiling raised about 500×, not removed (ADR-0008 decision 2's fan-out
  amendment; tracked by
  [#144](https://github.com/theurian/theurian/issues/144), which the ADR
  amendment above does not yet link — a docs follow-up owes it).

  **Sensitivity on a result and a chunk is the item's, not the revision's.** A
  revision is immutable, so `revision.metadata.sensitivity` is the label the
  content was authored under; a `changeSensitivity` moves the classification on
  the item without writing a new revision. `result_payload` now takes
  `sensitivity` as an item-authoritative parameter the way it already took
  `status`, and every call site threads the item's current value (`Surfaced` gains
  the field for the ranked path), so a search reports the new label the instant a
  reclassification commits. `index_builder` stamps a chunk with `item.sensitivity`,
  which flows to node scopes, so the built forest partitions on the item's current
  label rather than the revision's.
  `test_the_payload_reports_the_items_sensitivity_not_the_revisions` and
  `test_a_reclassified_item_is_reindexed_at_its_new_sensitivity` hold the two
  halves.

  **A reclassification forces no rebuild, and the engine does not fake one.**
  `migration_engine._withdrawal_affected_item` deliberately excludes
  `changeSensitivity`: a purge deletes rows and cannot rewrite a scope column, and
  a pure reclassification withholds nothing, so no purge fires. The response is
  already correct without one, and the built index's stale `sensitivity` column is
  read by no gate before #119 (SEC-7), matching canonical again on the next
  `index build`. The `docs/protocol/migrations.md`, `migration.schema.json` and
  `domain/migration.py` claim that a reclassification "forces every affected RAPTOR
  tree to rebuild" was false and is corrected in all three.
  `test_a_reclassification_is_not_a_withdrawal` and
  `test_a_reclassification_shows_in_the_response_before_any_rebuild` pin it.

  **Mutation-kill pins, and one softened docstring.** New tests hold the
  properties a mutation could flip silently: `SUMMARY_MAX_TOKENS`'s external
  definition, the `ForestOptions` floors and the `minChildrenPerSummary` minimum
  of exactly two, the document-tier skip, an upper node summarising its children
  in content-id order, `derive` returning low tiers before the tiers built on
  them, and the `node_type` join in `tree_identity` that keeps a Document tree and
  a Domain tree named alike apart. `IndexableNode`'s two refusals — more declared
  children than sources, and a source named twice — get their own tests. The
  declaration docstring in `domain/raptor.py` is softened to what it can hold:
  `IndexableNode`'s count check makes a declaration standing for no source
  unconstructible and a test pins that, but for a *valid* node a declaration
  copied from the parent and one derived from the child are equal by the type's
  own scope invariant, so no test separates the two forms — only the
  count-mismatch defect is pinned, and the earlier "derived from the child it
  summarises" claim is narrowed accordingly.

  **Reported, both fields.** `index build --json` gains `raptor` and `nodes`,
  because the count alone cannot tell a forest-free build apart from one whose
  corpus fell below every threshold — the confusion `indexesUnapproved` exists to
  prevent for drafts.

  **What this does not do, said plainly.** Nothing reads a node back: every
  retriever names `chunks`, no traversal exists, and `raptorPath` is emitted by
  nothing, so a forest is written, purged, and never returned to a caller. The
  build cost is unmeasured — one `summarize` call per node, on top of a build
  ADR-0024 measured at 2,614 ms over 400 documents for chunks alone — which is
  why the capability ships opt-in. Rebuilds are whole, not incremental. And the
  purge tests over builder-written rows reach only the unanchored arms a builder
  can produce: an unprovenanced node, an edge naming an absent node, and a
  provenance cycle stay covered by the raw-SQL fixtures in
  `test_index_purge_nodes.py`, because this builder writes every node before any
  edge in one transaction, gives each node at least one source, and builds each
  tier only from the one below.

  **The "no builder / nothing writes a node / no summary is generated" family is
  closed across the tree**, at 58 assertion sites in 16 files: ADR-0008 and
  ADR-0024, `docs/architecture/raptor.md`, `overview.md`,
  `requirements-analysis.md`'s R-3, R-4, R-7 and R-14, the threat model's T-3 and
  T-10, `SECURITY.md`, `README.md`, and six test files. ADR-0008's Compliance
  section records the key, the counts against both trees, and the two classes no
  keyword search can reach — including the four sites this CL planted in its own
  RED-phase test docstrings.

- **The withdrawal purge re-derives the forest from the surviving rows, landing
  ADR-0008 decision 9's two-corpus equality for the derived layer**
  (`application/withdrawal_purge.py`, `infrastructure/sqlite/index_purge.py`). The
  purge was delete-only for the forest: it removed the withdrawn chunks and every
  node the survivors could no longer ground, but never rebuilt the trees the
  withdrawal reshaped. A Domain tree of four documents that loses one to a
  withdrawal must end up with the three-child node a corpus that never held the
  fourth would build — content-addressing makes the survivor a *different* node
  than the old one minus a child — not with no node at all, which delete-only
  leaves. After the delete, the purge now re-derives each **scope that lost a
  row** whole — every tree in it, coarser than ADR-0008 decision 9's per-tree
  ancestor closure and subsuming it, since a scope's unaffected trees re-derive
  byte-for-byte — and leaves every scope that lost nothing untouched. It runs
  before `_verify`, so a re-derived node that is not grounded is refused by the
  same post-conditions a bad delete is.
  `test_a_purged_forest_equals_one_that_never_held_the_withdrawn_rows` asserts a
  purged build **identical** — node rows, derivation edges and node vectors — to a
  never-held build, with a stale pre-purge control asserted different.

  **`SqliteIndexStore.surviving_chunks` reads chunk rows back as full
  `IndexableChunk`s** for the builder to derive from — the purge is a function of
  the *published index*, not canonical state (ADR-0024) — and
  `delete_nodes_grounded_in_chunks` clears an affected scope's **entire** current
  node set before the fresh forest is written: seeded on the scope's surviving
  chunks, it walks `node_derivation` upward and deletes every node the scope still
  grounds, not only the trees the fresh derivation happens to reproduce — because
  clustering moves a node's id and re-inserting over a survivor would collide on
  the primary key.

  **A withdrawal that collapses a Domain fan-out re-batch made the purge fail
  closed instead of re-deriving (HIGH, reproduced by all three reviewers).**
  Above `MAX_CHILDREN_PER_DOMAIN` a kind splits into batches `kind#0 ..
  kind#(b-1)`; a withdrawal that drops the batch count to `b-1` re-derives only
  `kind#0 .. kind#(b-2)`, but a *surviving* top batch `kind#(b-1)` — none of whose
  members was withdrawn, so the universal-grounding delete never dooms it — keeps
  a `tree_id` the fresh set does not name. The earlier `delete_nodes_of_trees`
  deleted only that fresh set, so it missed the stale batch; the `ON DELETE
  CASCADE` then stripped the stale node's edges when the survivors' Document
  nodes were re-derived, leaving it unprovenanced, and `_verify` refused the whole
  purge over that remnant. A legitimate withdrawal therefore published no purge at
  all, leaving the stale build serving the withdrawn rows' statistics (T-17a).
  `delete_nodes_grounded_in_chunks`'s scope-wide deletion is the fix: it reaches
  the stale batch because the batch still grounds on the scope's surviving
  chunks, so the purge now re-derives instead of refusing. The earlier reliance on
  a primary-key collision to fail an incomplete delete *closed* was an accidental
  net, not the mechanism, and nothing depends on it now — the delete is exact over
  the scope by construction.
  `tests/integration/test_forest_purge_recompute.py` pins the fan-out boundary
  the equality test above does not reach: a re-batching withdrawal at the exact
  boundary, from the final batch, as a bulk withdrawal, and across two scopes
  withdrawn from in one command — each asserted identical to a never-held build.

  **The recompute is injected, not imported.** `index_purge` (infrastructure) may
  not name the application-layer `ForestBuilder`, so `purge_into` takes an optional
  `recompute_forest` callback; `make_forest_recompute` builds it at the composition
  root, closing over the extractive summariser and the hashing embedder
  (`cli/commands.py`). A passed-down callable keeps ADR-0003's layering — `test_layering`
  still passes. The re-derived nodes are embedded exactly when the build being
  purged already carried chunk embeddings, so a `--no-embeddings` forest stays
  vector-free.

  **A chunk-only build and a fully-withdrawn one keep today's delete-only path:**
  both leave zero surviving nodes, and there is nothing to re-derive.

  **The non-deterministic-provider fallback is recorded, not built.** ADR-0008
  decision 9's delete-and-mark-stale branch — for a provider that cannot reproduce
  a never-held build — is documented in `make_forest_recompute`'s docstring and
  exercised by nothing: the extractive default is deterministic, and a dead branch
  is a later change's. See the schema v5 breaking entry under Changed.

- **`ExtractiveSummarizer` lands as the first `SummarizationProvider` adapter,
  and the port's default** (ADR-0008 decision 6's Milestone 6 amendment and 7,
  `infrastructure/raptor/extractive.py`). It splits each child text on Latin
  `.!?` and the CJK ideographic full stop, exclamation and question mark
  terminators, scores each sentence by the summed frequency of its lower-cased
  character trigrams across the call's own sentences, and greedily adds
  sentences in descending score order — ties broken by document position,
  skipping a sentence that does not fit the *remaining* budget rather than
  stopping at the first one that does not, so a cheap well-scored sentence
  after a pricier one is never lost to it. Selected sentences are re-ordered to
  document position before being joined, so a caller reads a summary top to
  bottom regardless of the score order that chose it. [ADR-0009](../../docs/adr/0009-no-llm-vendor-lock-in.md)'s
  port table called this default "extractive (lead + salient sentence
  selection)"; there is no lead component, and that row is corrected with the
  reason recorded. Position breaks a score tie, orders the output, and chooses
  the sentence the truncation fallback cuts; it is never a scoring bonus, so a
  low-scoring opening line is dropped like any other.

  **Pure by construction, per decision 6's amended constraint**: "a summariser
  is a pure function of its own children's texts, its scope tuple, and a
  configuration-derived `max_tokens`. No corpus-wide statistic may enter ...
  and `max_tokens` must never be a corpus-derived quantity." `summarize`
  computes the split, the trigram frequencies and the selection fresh from the
  `texts` and `max_tokens` of the one call in progress; nothing is cached on
  `self` between calls, no corpus handle is acquired in `__init__`, and `scope`
  is accepted only because the port's shape requires it of every
  implementation, unread by this one. This discharges carriers (a) and (c) of
  the three-carrier class decision 6 names, in ADR-0008's Compliance section:
  `test_the_same_children_summarise_identically_across_contexts_that_differ_everywhere_else`
  is the owed two-budget equality test for carrier (a) — the summariser's text
  inputs — and `test_negative_control_a_corpus_reading_fake_is_detected_as_different`
  and `test_negative_control_corpus_derived_max_tokens_is_detected_as_different`
  are its negative controls for carriers (a) and (c) respectively, demonstrating
  the harness can tell a corpus-reading provider and a corpus-derived budget
  apart from this one. Carrier (b) — which children cluster into a node — is
  unreachable here because this test holds the child set fixed by construction; it
  is closed by decision 9's tree-level two-corpus test, which lands with the
  purge-recompute change above.

  **The budget is charged for the string that is returned, separators
  included.** Charging each sentence its own `estimate_tokens` cost and joining
  afterwards undercharges: `estimate_tokens` rounds up once per call, so the
  spaces between the selected sentences arrived unpriced and the returned text
  could cost more than the caller allowed — four four-character sentences at a
  budget of four came back costing five, and an exhaustive sweep found Japanese
  overshoots at budgets 1, 65, 69 and 98. Every sentence after the first is now
  charged the single space followed by the sentence, **priced as one string
  rather than as a separately-rounded separator** — which by
  `ceil(a) + ceil(b) >= ceil(a + b)` is never less than what appending it adds,
  and `k` sentences carry `k - 1` separators however they are ordered — so
  charging in score order and joining in document order price the same string.
  The charge is an upper bound on the joined cost rather than the cost itself,
  and deliberately: exact charging admits a second sentence at a budget its
  joined cost fills to the token, which
  `test_a_restrictive_budget_selects_the_mixed_childs_first_sentence_whole`
  requires left out. The under-fill it costs is under two tokens per selected
  sentence, one per ceiling. Held by every budget from 1 to the corpus total
  over the English and the Japanese fixture, and by a review fuzz of 12,369
  (random corpus, budget) pairs over Latin and CJK alphabets, none of which
  overshot.

  **One budget contract, two call sites.** `max_tokens < 1` raises
  `RankingError` — the error `domain.ranking.take_within_budget` raises for the
  same situation and for the same reason: `estimate_tokens` prices even the
  empty string at one token, so below one token there is nothing a summary could
  be that would not already break the budget it was handed. Before this it
  returned a single character regardless, and called that a summary.

  **The fallback floor changed.** When no whole sentence fits `max_tokens`, the
  output is the longest character prefix of the first sentence (by document
  position) whose cost still fits, with trailing whitespace removed — never
  anything but a verbatim prefix, and no longer a character costing more than
  the budget.
  `estimate_tokens` is non-decreasing in text length, so the longest fitting
  prefix is well defined and a binary search finds it, over a range bounded by
  the budget rather than by the input. That makes the output **empty** in
  exactly one case for content-bearing input: `max_tokens == 1` *and* the first
  character of the first sentence is dense script, which `estimate_tokens`
  prices at two, so not even a one-character prefix fits. The same budget over a
  sentence beginning with a Latin character returns that character. Emitting one
  costing more than the budget regardless — what it used to do — would make this
  the one place in the module that knowingly breaks FR-R4. Whitespace-only
  children summarise to the empty string at every budget, which is the other
  empty case and is unchanged.

  **The staleness key hashes a version, and is pinned by a literal.**
  `prompt_hash` is `sha256(SEMANTICS_VERSION)`, over the compact constant
  `extractive-sentence-selection/2`, rather than over `ALGORITHM_DESCRIPTION`'s
  prose. Rewording the review-facing description no longer invalidates every
  stored summary node; a change that would pick different sentences for the same
  children still must bump the version, and `MODEL_REVISION` is derived from
  that same constant rather than kept as a second literal to forget.
  `test_prompt_hash_is_pinned_to_the_literal_sha256_of_semantics_version` pins
  it to a hard-coded digest, following 3c5bd6d: a value compared against its own
  derivation can never fail. The port's contract moved with it:
  `SummarizationProvider.prompt_hash` said "hash of the summarization prompt",
  which is false for the only implementation that exists, and now splits by
  whether an implementation prompts at all — the prompt for one that does, the
  identifier of its selection semantics for one that does not.

  **The version ships at `/2`, and the mechanism has been run once already.**
  The truncation fallback cuts wherever the budget runs out, which is as often
  mid-space as mid-word, so it could hand back a prefix ending in a space: a
  character that renders as nothing, breaks equality against the same prefix
  produced any other way, and was paid for out of the caller's budget. It
  right-strips now, which changes what the same children summarise to and is
  therefore a semantics change, so it took the whole mechanism with it —
  `SEMANTICS_VERSION` to `/2`, `MODEL_REVISION` to `"2"` by derivation, and the
  pinned digest re-pinned by hand. Measured over the suite's own sweeps: the
  strip moves the output at **1 of 56 English budgets** (`"S1 sentence "` →
  `"S1 sentence"` at budget 3) and **none of the 116 Japanese ones**, since CJK
  sentences carry no spaces to strip. It is deliberately run now rather than
  deferred: nothing is persisted yet, so this bump costs a re-pin and no
  rebuild, and every later one invalidates a forest.

  **Retracted from this entry as first written**, because it claimed a guarantee
  no test held: "a change to the algorithm that forgot to bump
  `ALGORITHM_DESCRIPTION`'s trailing version would leave every stored node's
  staleness check unable to see it, and this test is what turns that omission
  into a failing assertion instead of a silent gap." The test it named compared
  `prompt_hash` against `ContentHash.of_text(ALGORITHM_DESCRIPTION)` — both
  sides move together, so it could not fail for any reason at all. What the
  literal pin holds is one direction: bumping `SEMANTICS_VERSION` cannot land
  without a human re-pinning the digest in the same diff. A semantics change
  that forgets to bump the constant is still invisible to the suite, and is
  recorded that way rather than papered over, because no test distinguishes a
  deliberate scoring change from an accidental one.

  **The blind spot reaches past this module, and two of its three carriers are
  closed here.** Selection is priced by `estimate_tokens`, so
  `domain.ranking`'s charging model decides which sentences survive a budget as
  directly as the selection code does — and none of it is hashed. Measured over
  the same sweeps with `prompt_hash` unmoved throughout: raising
  characters-per-token from 4 to 5 changes the output at **41 of 56 English
  budgets** and none of the 116 Japanese ones; raising the dense-script rate
  from 1.5 to 2.0 changes it at **101 of 116 Japanese budgets** and none of the
  56 English ones. A node summarised under either would be silently unrebuilt,
  because nothing in its `summary_prompt_hash` moved. Both rates are pinned now
  by `test_the_charging_model_selection_depends_on_is_pinned_too`, whose
  docstring says in as many words that changing them is a `SEMANTICS_VERSION`
  bump here; the constant's own note names the charging model as
  bump-triggering, and `domain.ranking` carries the cross-reference back.

  **The third carrier is deliberately unpinned, and named rather than left
  silent.** `_DENSE_SCRIPT_RANGES` decides *which* characters are charged at
  the dense rate, so adding a script to it moves selection exactly as changing
  the rates does. Pinning a tuple of seven ranges would go red on every
  legitimate script addition as loudly as on a semantics-changing one, so it is
  left to the note beside the constant and to that test's own docstring — both
  of which state that this carrier is uncovered.

  **`model_id` is `theurian-extractive-sentences`**, namespaced the way
  `HashingEmbedding`'s `theurian-hashed-char-ngram` is and for the same reason:
  it lands in every summary node's `nodes.summary_model`, where a bare
  "extractive" could not be told apart from a later, differently-behaved
  extractive implementation. Nothing writes a node row, so no stored value
  changes.

  **`MAX_TOTAL_INPUT_CHARS` records the bound that was missing**: 1,000,000
  characters of `texts` per call — a thousand times `domain.chunking`'s
  1000-character chunk target — above which `summarize` raises
  `InvariantViolationError`. Every stage is linear in that count, so without a
  recorded limit the only bound on one call's work was what the caller passed,
  and a cluster of a thousand chunks is a clustering defect rather than a large
  document. Measured at exactly the cap and recorded on the constant: 1.45 s of
  CPU and 5.6 MB of peak heap over Latin prose, 1.10 s and 16.3 MB over
  Japanese. Scoring makes two passes and re-derives each sentence's trigrams
  rather than holding every sentence's at once, which is what keeps those heap
  figures small — 53.9 MB and 78.0 MB respectively for the one-pass variant —
  for about 7% more CPU on the whole call. The whole-call figure hides where it
  lands: inside the scoring function itself the second pass costs 41% to 51%
  more, depending on the corpus.

  **Determinism** is pinned in-process against freshly built string objects at
  a restrictive budget, and **across processes by the suite itself**:
  `test_summarize_is_stable_across_processes` and
  `test_a_tied_selection_is_stable_across_processes` each run `summarize` in
  three fresh interpreters at `PYTHONHASHSEED` 0, 1 and 999 — the seeds
  `test_projection.py` cross-checks under ADR-0020 — and require one distinct
  output. `PYTHONHASHSEED` varies across interpreter invocations by default and
  cannot be varied within one, so an iteration order keyed by object hash is
  invisible in-process. Two tests rather than one because the English fixture
  has no genuine score ties: a tie-break that started reading a
  hash-seed-dependent key would have nothing to disagree about there, so the
  tied fixture is run across the same boundary. Round one checked this by hand
  in two `uv run python` processes; that is history now, and the property is in
  the suite.

  **Offline by construction, and now asserted rather than argued.**
  `test_the_default_summarizer_reaches_no_socket_capable_module` imports the
  module in a fresh interpreter and asserts its whole import closure holds none
  of sixteen socket-capable standard-library modules — `socket`, `ssl`,
  `asyncio`, `urllib.request` and twelve others. ADR-0009's no-network control
  was deferred with the reason that every adapter which could open a socket was
  unbuilt, so a test would have passed vacuously; this is the first adapter, and
  that reason expired with it. The item stays owed for `RerankingProvider` and
  `ReviewProvider`, and for the wider claim about a whole default configuration
  rather than one module's closure.

  **Nothing calls it yet.** `infrastructure/raptor/` still has no builder and
  no traversal, so this lands with no consumer; wiring it into a build is the
  next CL. This discharges the present-tense claim in
  `domain/ports/summarization.py`'s docstring — "The default is extractive" —
  which described no adapter until now and needs no wording change to read
  correctly as of this commit; flagged as exactly that gap in
  [#141](https://github.com/theurian/theurian/pull/141)'s review round.

  **The "`infrastructure/raptor/` is empty / `SummarizationProvider` has no
  adapter" family is closed across the tree**, at 27 assertion sites in 12
  files: that package's own module docstring, `index_schema.py`, the node-table
  comments in `test_index_purge.py` and `test_index_store.py`,
  `test_scope_isolation.py`, `SECURITY.md`, the threat model's T-3 and T-10,
  three risk rows in `requirements-analysis.md`, and ADR-0008, ADR-0009,
  ADR-0024 and `docs/architecture/raptor.md`. The key is the *proposition* in
  five vocabularies rather than the token `SummarizationProvider`; ADR-0008's
  Compliance section records the search, the count, and why two earlier counts
  (ten, then twelve) were short. The builder and traversal absences those files
  also name stay open, since neither exists yet.

- **`knowledge.status` publishes a response schema, and the two fields a withheld
  item may move are pinned as an exact set**
  ([#19](https://github.com/theurian/theurian/issues/19)).
  `schemas/mcp/knowledge-status-response.schema.json` declares the response's six
  fields — `projectId`, `stateHash`, `itemCount`, `itemsByStatus`,
  `appliedMigrations`, `schemaVersion` — under `additionalProperties: false`,
  with `itemsByStatus` declaring only `approved`, `draft` and `proposed` and
  forbidding a fourth key, so a retired status is rejected under its own name and
  under a relabelled bucket alike; either reports the quantity the breakdown
  exists to withhold. **Additive, and no wire change**: the tool emitted these
  six fields before the schema existed and emits the same six now. The only
  change under `src/` is a comment.

  It is also where #19's decision now lives, instead of in that comment.
  `stateHash` and `appliedMigrations` both stay, and the schema states why per
  field: neither carries a bit about *what* was withheld; `stateHash`
  content-addresses the whole working tree by design (ADR-0016) and is the
  query-independent value FR-R5 exists to let a caller compare against;
  `appliedMigrations` counts migration *files*, so it moves identically whether a
  migration created an approved item, a draft, a rejected one, or none at all.
  `knowledge.status` takes only `projectId`, so nothing about a request reaches
  either number — no probe to vary, and therefore no extraction oracle. The
  remedies considered and rejected are recorded there too: removing the field
  breaks the question it exists to answer, bucketing it answers a question nobody
  asked, and counting only migrations that produced surfaceable items publishes a
  number no user can reproduce from their own migration directory.

  **The exception set is a test, not a sentence.**
  `test_a_withheld_item_moves_exactly_the_two_fields_the_status_schema_exempts`
  builds two projects one migration apart, where that migration creates a
  `deprecated`, a `superseded` and a `rejected` item and nothing else, registers
  both under the same id in registries of their own so the request is
  byte-identical, and asserts that the set of fields whose values differ *equals*
  `{stateHash, appliedMigrations}`. An exact set rather than a subset: a response
  that stopped publishing `appliedMigrations`, or a `stateHash` gone insensitive
  to canonical state, goes red instead of passing quietly. This extends T-17's
  one-query-two-corpora equality to a third tool — `knowledge.search` and
  `knowledge.get` hold it without exception. The schema is checked against real
  CLI-built projects in `test_wire_contract.py`, including one whose items are all
  retired, whose breakdown is `{}` and whose `itemCount` is `0`, asserted beside
  what its canonical store really holds, because `{}` from a project that holds
  nothing is the same document. `knowledge.get` and `system.capabilities` still
  publish no response schema
  ([#20](https://github.com/theurian/theurian/issues/20)).

  **The read cost is now independent of the withheld count, not only the
  response.** `knowledge.status` used to run `list_items` and filter
  `SURFACEABLE_STATUSES` in Python, so its work — and its response time — scaled
  with the total row count, letting a caller recover the withheld count by
  subtracting `itemCount` from the time (measured at 97.5% single-call
  classification with fifty withheld rows, T-17). It now counts in SQL through
  `CanonicalStore.count_surfaceable_by_status` — `status IN (SURFACEABLE_STATUSES)
  GROUP BY status` over the `idx_items_status` covering index — so the query never
  reads a withheld row. SQLite VM steps stay flat at 103 as the withheld count
  grows 50 → 300, where the old scan went 1,130 → 5,380, and the response dict is
  byte-identical on both paths.
  `test_status_materializes_the_same_rows_however_many_are_withheld` pins it at the
  row, going RED when the `list_items` path returns. The sibling channel on the
  search fallback (`mcp/search.py::_scan`) is filed as
  [#158](https://github.com/theurian/theurian/issues/158). A corrupt `status` cell
  now makes `knowledge.status` under-report rather than raise, since the SQL count
  no longer parses every row — the fifth `SILENTLY_EMPTIED` member, carried to
  Milestone 6 ([#30](https://github.com/theurian/theurian/issues/30)).

  **`stateHash` and `appliedMigrations` move on different triggers.** `stateHash`
  moves for any change to canonical state; `appliedMigrations` moves only when a
  migration is *added*, not when an existing one is edited — an edit moves the hash
  alone. Prose saying both fields move with any canonical change was wrong and is
  corrected in the schema; `test_applied_migrations_counts_files_not_items` pins
  the field as a file count invariant to the item count.

  **`deprecated` is declarable through revision metadata, and the withheld corpus
  now proves it.** `migration.schema.json`'s `status` enum includes `deprecated`
  and `migrate apply` writes it straight onto the item, so `deprecateItem` is not
  the only path to a `deprecated` item. `WITHHELD_CORPUS` in
  `test_wire_contract.py` now carries a metadata-declared `deprecated` item beside
  the `deprecateItem` one, so "no retired status appears under any published label"
  is held for the metadata path too; a test docstring claiming otherwise was
  corrected.

### Changed

- **BREAKING — the terminal state a critical apply failure reaches is `halted`,
  was `rolled-back`** ([#47](https://github.com/theurian/theurian/issues/47)).
  `aborted` is terminal too and is unaffected; this is the failure *during*
  apply. A consumer keying on the string `"rolled-back"` must update to
  `"halted"`. The old value named a rollback setup never performed: the setup
  journal (`~/.theurian/setup-journal.jsonl`) is append-only with no inverse
  action, and `_apply` replays nothing. A critical step failing during apply now
  halts the run where it failed and undoes nothing. Any credential minted before
  the failure remains on disk — deleting a token another session may be holding
  is its own defect — so `changed_paths` discloses it. What that list holds: each
  applied step's declared artefacts, plus whichever of the failing step's
  declared artefacts this run *moved*, plus the setup journal when this run
  appended to it — de-duplicated in first-seen order, so the credential appears
  exactly once. Two remedies, and neither costs a client reconfiguration:
  `theurian auth rotate` replaces the value in place, rewrites the env file and
  restarts the daemon **where it can**; deleting the file by hand leaves a later
  `theurian setup` to mint a new token at the same path. `_restart_daemon`
  restarts only where `detect_manager` finds a service manager and that manager
  reports the service as something other than not-installed — otherwise the
  command answers `daemonRestarted: false` and names the restart in `nextSteps`,
  which is the arm a halted run reaches, since a halt has usually come before
  daemon-service registered anything. Client *configuration* holds a *reference*
  either way — `${THEURIAN_MCP_TOKEN}` in the MCP entry,
  `THEURIAN_MCP_TOKEN="$(cat …)"` in the env file — so nothing about it changes.
  A running *process* holds the expansion it took at its own startup: the daemon
  until it restarts, and equally a shell that has already sourced the env file
  and a client session already running. That is the third participant
  `auth_commands`' module docstring names, and why `_restart_daemon` returns the
  reload-shell instruction on every path it can take.

- **`changed_paths` names two things it used to omit**
  ([#47](https://github.com/theurian/theurian/issues/47)). It listed the planned
  paths of the steps that *finished*, so a halted run reported neither the setup
  journal it had just appended to nor what the failing step wrote before it
  raised — an apply can create its artefact before the write or `chmod` that
  fails, since `FileSecretStore.set` and `apply_env_reference` both `os.open`
  with `O_CREAT | O_TRUNC` ahead of the `os.write` and the `chmod`. Both now
  appear.

  **A failing step's path is published on provenance, not on existence.** The
  first fix in this milestone asked whether the declared path is on disk now, on
  the premise that a step reaches its apply only when `Missing`; `Missing` means
  "not as setup wants it", not "absent", so that check published paths the run
  had never touched — a pre-existing 0755 `~/.theurian` whose `chmod` was
  refused, a `~/.claude.json` left byte-identical by a failed `claude mcp add`
  (a file Theurian never writes at all), and a *directory* at `auth/mcp-token`,
  which had the plugin advising an operator to rotate a credential that did not
  exist. Each declared path is now reduced to
  `(st_ino, st_mode, st_size, st_mtime_ns)` by `os.stat` immediately before the
  apply and again after the raise, and named only if it appeared or its
  signature changed. `st_mode` is in the signature because the data-directory
  step's whole write *is* a mode change; `os.stat` follows symlinks because
  every apply here writes *through* a link rather than replacing one. A check
  that fails on either side — EACCES, ELOOP, a name too long — discloses the
  path anyway: when the run cannot tell, it says so. Two of the seven arms in
  that truth table cannot be reached by any shipped apply and are driven by a
  synthetic step through the real `SetupService`; the one for a path that stops
  being statable passes on the signature comparison (`None` against a tuple)
  rather than on the flag that separates "absent" from "could not look", so
  isolating pins for the unknown arms and for `st_ino`, `st_size` and
  `st_mtime_ns` individually are deferred to
  [#155](https://github.com/theurian/theurian/issues/155).

  The steps that finished are still trusted rather than re-measured. That is
  exact for an apply that writes or raises, which is every one here **but
  `apply_token_storage`**: it is a call to `apply_token`, which mints only when
  there is no token, so on a fresh install the token step ahead of it has
  written the file already and this apply returns having done neither. Its
  declared path is truthful because its predecessor wrote it, and the ordering
  is now pinned rather than incidental
  (`test_the_token_is_minted_before_the_step_that_stores_it`). Swapping the two
  moves no report field — both declare the artefact, so `state`, `changed_paths`
  and both outcomes are identical either way; what it corrupts is the journal,
  which would record "Generate a 256-bit token with the system CSPRNG." for a
  step that generated nothing. The class an apply that finishes without writing
  belongs to — this one, and an external tool exiting successfully without
  writing — is [#153](https://github.com/theurian/theurian/issues/153).
  Truncation is still disclosed, on the arm that motivated the first fix:
  `apply_env_reference`'s `O_TRUNC` moves size and mtime before the write that
  raises, and what it replaced is preserved nowhere
  ([#128](https://github.com/theurian/theurian/issues/128)). That arm is read
  off the open flags and not measured — no test drives a truncation followed by
  a write that raises.

  Implicitly created paths are still not listed — a step discloses its declared
  artefacts only — and that category is wider than `auth/` under the data
  directory: the service adapters create `~/Library/LaunchAgents` and
  `~/.config/systemd/user` the same way. An adapter's `.plist.tmp` surviving a
  failed install is absent from `changed_paths` for the same reason, but not
  from the report: the failed journal record's `detail` and the report's
  `warnings` carry the same `reason` string, so an exception naming the
  temporary path puts it in both
  ([#152](https://github.com/theurian/theurian/issues/152)). `~/.claude.json`
  cuts the other way — the row above says a failed `claude mcp add` may not
  claim it, and a *converged* run does name it, because the step declares it and
  `claude` wrote it. "A file Theurian never writes" is about Theurian's own
  process, which delegates that write.

- **The setup journal is created 0600, and `theurian setup --help` now names it**
  ([#47](https://github.com/theurian/theurian/issues/47)). Its lines hold local
  absolute paths and the verbatim text of the exception that stopped a step, and
  `changed_paths` points every reader of a halted report straight at the file;
  under a 0022 umask it was created 0644. The directory around it is not what
  protects it — the arm that fails to tighten `~/.theurian` is exactly the arm
  that leaves this file's parent 0755, and
  `test_the_journal_is_created_private_inside_a_directory_that_is_not` asserts
  both modes in that scenario. The mode comes from the `open` that creates the
  file, so there is no window at the wider one. **And it is re-asserted on every
  append**, by an `os.fchmod` on the open descriptor before the write, which
  supersedes this entry's earlier statement that a journal an earlier version
  created keeps its own mode: `0.1.0.dev0` and `0.1.0.dev1` both created it
  through `Path.open("a")` — 0644 under the usual umask — and the next append
  now repairs that rather than the installation carrying it for life. The same
  line closes the other direction, which the creation mode cannot reach either:
  `os.open`'s mode argument is ANDed with the umask, so a 0277 umask creates the
  journal 0400 and every later run's `O_WRONLY` open then fails EACCES, leaving
  the journal silently never written again. A refused `fchmod` — a journal owned
  by another account — skips the append and reports it, the same trade the 0600
  creation already makes. `--help` said the seven steps are every write setup
  performs; the journal is an eighth, appended by the runner and belonging to no
  step, and the sentence now says so
  (`test_the_cli_docstring_names_the_write_that_belongs_to_no_step`).

- **An append to the setup journal completes or reports that it did not**
  ([#47](https://github.com/theurian/theurian/issues/47)). It used to answer
  whether the file grew, and `changed_paths` turns that answer into a claim that
  the journal is a file this run wrote. `write(2)` may write fewer bytes than it
  was handed and return that count without raising, so under a file-size limit
  or a full disk an `os.write` whose return was discarded left a truncated
  record and reported success — measured at three half-lines run together into a
  single entry no reader can parse, announced in `changed_paths` as a file this
  run wrote. The record now goes through an `io.BufferedWriter`, which loops
  until the buffer is empty and raises whatever the flush or the close hit; the
  bytes that did reach the disk are left there, because the file is `O_APPEND`
  and truncating back to a remembered length would discard a concurrent writer's
  record rather than this one's. What was false was the answer, not the byte
  (`test_an_append_that_could_not_complete_leaves_the_journal_undisclosed`).
  This is per append: a line an earlier append landed stays on disk and stays
  disclosed when a later one fails, on the applied and the failed arm alike
  (`test_a_step_that_applied_and_could_not_be_journalled_keeps_the_earlier_line_disclosed`,
  `test_a_failure_that_could_not_be_journalled_keeps_the_earlier_line_disclosed`).

- **BREAKING — `INDEX_SCHEMA_VERSION` 4 → 5: `chunks` gains a `kind` column**
  (the purge-recompute change under Added; ADR-0008 decision 2's and ADR-0024
  decision 8's Milestone 6 amendments). The withdrawal purge re-derives each
  affected scope's Domain trees from the *published index's* surviving rows, and a
  Domain tree is keyed by `kind` within a scope — but v4 kept `kind` only on the
  in-memory `IndexableChunk`, and a summary node records its scope and not the
  leaf `kind` its tree clustered on, so `kind` lived nowhere a re-derivation
  reading the index could recover it. v5 persists it, `NOT NULL DEFAULT ''` so a
  v4 build mismatches and rebuilds and the purge suite's column-naming `INSERT`s
  need no edit. No *retrieval* reads the column.

  **Every existing index reports `index-schema-mismatch` and falls back to the
  substring scan until `theurian index build` runs.** That is the designed
  response to an index schema change and not a regression: the index is derived
  and disposable, so this costs an index rebuild and nothing else — no in-place
  migration of the file, no data migration, no canonical `SCHEMA_VERSION` bump, no
  state hash change (ADR-0022 point 3). `theurian index status` reports
  `indexSchemaVersion` beside `expectedIndexSchemaVersion`, counts the build
  `stale`, and its `remedy` names the command.

  **Affects `0.1.0.dev0` and `0.1.0.dev1`**, both of which ship index schema **2**
  (each pins `INDEX_SCHEMA_VERSION: Final = 2`), so a released Theurian meets this
  as 2 → 5 in one rebuild — schemas 3 and 4 exist only on `main`. Nothing
  canonical needs migrating; what is lost is the index build itself, and
  rebuilding it is the whole remedy.

- **`Scope` gains `status: KnowledgeStatus` as a required sixth component of
  RAPTOR tree identity** (ADR-0008 decision 1's Milestone 6 amendment, SEC-14,
  T-10, R-14): `(project, tenant, sensitivity, acl_group, namespace, status)`.
  Without it, an `index build --include-unapproved` run could mix a `draft`
  and an `approved` child into one summary node with no tree boundary to stop
  it, even though `_scope` already filters chunk reads on status — the
  five-component tuple never named the axis that filters.

  **BREAKING — every `Scope` construction site must now pass `status`.** There
  is no default: the other five fields have none, and a silently-defaulted
  status is the exact builder-filled-column failure the amendment exists to
  prevent. The two construction sites in this tree supply it without a
  signature change of their own — `RevisionMetadata.scope_for` from
  `self.status`, `KnowledgeItem.scope` from `self.status` — but any other
  caller constructing `Scope` directly now fails at the call site. `key` and
  `digest` join all six components, so this changes every `Scope.digest` this
  tree can compute; that costs nothing today, because nothing in `src/`
  persists a tree id yet — `Scope.digest`'s only reader is `SummaryNode.tree_id`
  below.

  **BREAKING — and the separator those six components are joined with is now
  reserved.** `Scope.key`'s docstring said the unit separator "cannot occur in
  any component" and nothing enforced it, so two *distinct* scopes could render
  one key and therefore share one `digest`: `acl_group="a\x1fb"` with
  `namespace="c"` produced the same key as `acl_group="a"` with
  `namespace="b\x1fc"`, demonstrated in review rather than reasoned about.
  `AclGroup`, `TenantId` and `Scope.namespace` now refuse C0 control characters
  and DEL at construction — the whole range rather than `\x1f` alone, so the
  rule survives a change of delimiter as one sentence instead of an allowlist.
  `ProjectId` was already a kebab-case slug and `sensitivity`/`status` are
  enums, so no component can carry the separator now. A value that carried a
  control character used to construct and now raises `DomainError`; nothing in
  this tree built one.

  **Affects `0.1.0.dev0` and `0.1.0.dev1`.** Code written against either release
  that constructs `Scope` directly, or that builds an `AclGroup`, a `TenantId`
  or a namespace containing a control character, fails at the call site after
  upgrading. Nothing persisted needs migrating: no `Scope.digest` is written to
  any database or state file in this tree.

- **`domain/raptor.py` adds `SummaryNode`**, the value-level node type holding
  the scope-match rule; decision 5's provenanced node type — the one carrying
  `text`, `summary_model` and `summary_prompt_hash` — is still owed. A frozen
  node refuses construction from an empty child tuple, and refuses construction
  from any child whose scope differs from the node's own in any of the six
  components — comparing whole `Scope` values, not an enumerated field list that
  could omit one. Its `tree_id` is the scope's `digest`, which is the tree-id
  function ADR-0008 decision 1 describes, total over all six components; the
  class is `@final`, so a subclass overriding `__post_init__` to mint a node
  whose children were never checked fails type-checking rather than being a
  supported extension. `children` is normalised to a tuple as the first step of
  `__post_init__`, because a list handed to a frozen dataclass is not its own
  storage and a caller that kept a reference could otherwise mutate a node it was
  told is immutable (measured). This discharges the scope-match and tree-id
  halves of ADR-0008's `tests/unit/test_raptor_scope.py` item; the item stays
  open, because the claim it also carries — that no node's *text* spans two
  sensitivities — needs the node type that has text.
  `test_scope_isolation.py`'s exhaustive product moves with the tuple, from 32
  combinations over five components to 64 over six.

- `VectorStore.search`'s `scopes` filter narrows with the tuple: one `Scope` now
  names exactly one status, so a caller wanting both drafts and approved rows
  passes two scopes rather than one. The port's docstring is unchanged and no
  behaviour changes — `infrastructure/vector/` is empty and nothing implements
  the protocol, so the narrowing lands on a contract with no adapter to break.

- **BREAKING — `INDEX_SCHEMA_VERSION` 3 → 4: RAPTOR summary nodes get their own
  tables, and `chunks.derived` and `chunk_derivation` are dropped** (the
  Milestone 6 amendments to ADR-0008 decision 5 and ADR-0024 decision 8).
  **Every existing index reports `index-schema-mismatch` and falls back to the
  substring scan until `theurian index build` runs.** That is the designed
  response to an index schema change and not a regression: the index is derived
  and disposable, and ADR-0022 point 3 exists so that a schema change costs an
  index rebuild and nothing else — never an in-place migration of the file, no
  canonical `SCHEMA_VERSION` bump, no state hash change, no canonical database
  invalidated. `theurian index status` reports `indexSchemaVersion` beside
  `expectedIndexSchemaVersion`, counts the build as `stale`, and its `remedy`
  names the command.

  **Affects `0.1.0.dev0` and `0.1.0.dev1`**, both of which ship index schema
  **2** — each tag pins `INDEX_SCHEMA_VERSION: Final = 2`. So a released
  Theurian meets this as 2 → 4 in one step: schema 3 exists only on `main` and
  was never in a release, even though the 2 → 3 entry below is filed under
  `[0.1.0.dev0]`, which
  [#138](https://github.com/theurian/theurian/issues/138) moves. Nothing
  canonical needs migrating and no state hash moves; what is lost is the index
  build itself, and rebuilding it is the whole remedy.

  `nodes` carries the fourteen provenance columns ADR-0008 decision 5 names —
  `node_id`, `tree_id`, `level`, `node_type`, `text`, `content_hash`, three
  summary-model columns, three embedding columns, `source_revision_id` and
  `index_build_id` — plus `project_id`, `sensitivity` and `status`. Those three
  are denormalised for the same reason `chunks` carries its own copies:
  filtering has to happen in the same statement as the match, before ranking
  (FR-R1). `tree_id` already encodes the whole six-component scope tuple, so
  they are read at query time rather than recovered from it. `node_derivation`
  is the provenance edge, naming exactly one of a source chunk or a source node
  — a `CHECK` per row, rather than two nullable columns every future writer is
  trusted to keep consistent.

  **`nodes_fts` is a separate external-content FTS5 table, and the separation is
  the point.** `bm25` scores every row against collection statistics computed
  over *every* row in the table it is asked about — `N`, `avgdl` and the
  per-term document frequencies — and a summary systematically repeats the terms
  of the children it was built from. A summary row sharing `chunks_fts` would
  move all three under every ordinary leaf query the caller never asked a node
  about, so a visible leaf's rank would become a function of the forest's shape.
  `test_a_node_row_does_not_move_a_leaf_chunks_bm25_score` pins it: a leaf's
  score is read through the real `search_lexical` path before and after
  inserting a node whose text is nothing but the query's own terms, and must be
  unchanged. It is a first, narrow instance of the whole-statistics test
  ADR-0008 still owes, not that test.

  **`node_embeddings` and `nodes_trigram` land at v4 as well, so node storage
  costs one schema bump rather than three.** `embeddings` is keyed on
  `chunk_id REFERENCES chunks`, so a summary's vector had nowhere to live;
  `nodes_trigram` exists because `unicode61` splits on whitespace and
  punctuation only, which makes a Japanese summary a single token, and this
  project's own knowledge is written in Japanese. Both mirror their chunk
  counterparts including `ON DELETE CASCADE`, and `_verify`'s orphan check for
  node vectors exists from birth rather than arriving with whichever CL first
  writes one.

  **`chunks.derived` and `chunk_derivation` are dropped rather than kept beside
  the new tables.** Nothing ever wrote either: v3 added them ahead of RAPTOR
  (ADR-0024 decision 8) on the assumption that RAPTOR would be their writer, and
  this is the feature they were waiting for deciding otherwise. Keeping a dead
  provenance mechanism beside a live one is how the wrong one gets read. Their
  **six** traversal tests migrate to node rows rather than being deleted — what
  they hold is decision 8's rule, and the rule is unchanged: withdrawal is
  transitive over derived content, and an unresolvable derivation edge means
  delete, not keep. ADR-0024's Compliance section counted five of the six until
  this change corrected it; the sixth had been in the suite from the start.

  **The purge moves with the storage, and its rule for a node is universal
  grounding: a node survives only if *every* derivation path below it terminates
  at a surviving chunk in finitely many steps.** `index_purge._DOOMED` computes
  the complement, because grounding is a least fixed point under a universal
  quantifier and SQLite's row-at-a-time recursion cannot express one. *Unanchored*
  is five arms — a `source_revision_id` naming a withdrawn revision, no
  `node_derivation` row at all, an edge naming a withdrawn or absent chunk, an
  edge naming an absent node, and a node standing on a provenance cycle — closed
  upward over "is built from", so a node built on an unanchored node goes too. A
  summary cannot be partially grounded any more than it can be partially
  withdrawn, so one good parent and one that leads nowhere is still removed.
  Every one of those shapes survived the reading this replaces, which seeded on
  unprovenanced rows and walked forward from the withdrawn chunks: measured, a
  two-cycle of summaries of a withdrawn incident survived a purge of the *entire*
  corpus with its text intact, and `_verify` accepted the build. Against a
  well-founded reference over 400 randomly generated graphs — self edges and
  cycles allowed, one fixed seed — the shipped reading now diverges on none; the
  reading it replaces still diverges on 11 of the same 400, and on 91 of them
  before the self-edge `CHECK` below started refusing those graphs' self edges
  outright.

  **`_verify` is six post-conditions, not v3's three**: rows of the withdrawn
  revisions (chunks by `revision_id` *and* nodes by the `source_revision_id`
  stamp, where v3 counted chunks only), an orphaned chunk embedding, an
  unprovenanced node, a `node_derivation` edge whose source chunk or source node
  is gone, a node standing on a cycle, and an orphaned node embedding. The
  dangling-edge check has no v3 analogue at all: two tables make a dangling edge
  and an unprovenanced row different states where v3's single table made them
  one, so a node can hold an edge that points at nothing while still having an
  edge — which the unprovenanced count, then and now, does not see. The cycle
  count is computed independently rather than by asking `_DOOMED` a second time,
  because a post-condition computed by the function it checks cannot catch that
  function being wrong. With it the six are jointly complete: no cycle makes the
  node graph finite and well ordered, no dangling edge and no unprovenanced node
  make every edge name a surviving row, and grounding follows by induction up
  that order.

  **`_restamp` reaches `nodes.index_build_id` too, not only `index_metadata`.**
  That column is one of decision 5's fourteen provenance columns; measured, a
  surviving node named the build it had been copied from while `index_metadata`
  named the new one — the disagreement `_restamp` exists to prevent at the file
  level, one level down inside it.

  **Four schema hardenings, each closing a check that had stopped checking.**
  `chunks.chunk_id`, `nodes.node_id`, `embeddings.chunk_id` and
  `node_embeddings.node_id` gain `NOT NULL`: only an INTEGER primary key is a
  rowid alias SQLite refuses NULL for, so a TEXT one admitted a single NULL row,
  and one NULL in a `NOT IN` subquery answers NULL — falsy — for *every* row.
  Measured against the `NOT IN` form those checks were first written in, one NULL
  `chunk_id` turned two of the purge's post-conditions inert and `_verify` then
  accepted a build holding both a dangling edge and an orphaned embedding — the
  checks are `NOT EXISTS` now as well, so neither guard depends on the other.
  `node_derivation` refuses a self edge,
  the smallest provenance cycle. Its three-column `UNIQUE` index never fired,
  because the exclusive-source `CHECK` leaves one of those columns NULL in every
  row and no NULL equals another — three byte-identical edges went in through it
  — so two partial unique indexes replace it, one per source column. A partial
  index cannot answer `WHERE node_id = ?`, which the three-column one had been
  serving by accident of being its leftmost column, so `node_derivation_by_node`
  is declared explicitly: dropping it takes the no-provenance check from 0.29 ms
  to 227.8 ms over 1,100 nodes and from 1.42 ms to 5.78 s over 5,500.

  **`IndexStore.holds_any_revision` moves with them, and it is the one that
  reaches past the purge.** Its second clause is an executed SQL predicate, not
  a docstring, and `application/withdrawal_purge.py` runs it as the pre-check on
  every `migrate apply` that withdraws anything. Left naming `chunk_derivation`,
  it raises `no such table: chunk_derivation` against a v4 index — reproduced,
  and it raises even where the revision clause alone would have answered,
  because SQLite resolves the whole statement before evaluating any of it. So
  the drop would have broken withdrawal and not only purging.

  **It stops being a second hand-written predicate.** It runs
  `index_purge.ANY_DOOMED_ROW`, composed from the same withdrawn-chunk and
  unanchored-node literals `_DOOMED` is built from, so the pre-check is `_DOOMED`
  minus an upward closure over an empty seed and the two agree by construction
  rather than by being kept in step. Kept in step by hand, they did not agree: a
  build whose only damage was a pre-existing dangling edge answered "nothing to
  purge" on the pre-check — so `migrate apply` skipped it as clean without
  copying the file — while a purge run directly on that same build refused to
  publish over the one bad row. Under universal grounding that node is exactly as
  ungrounded as one with no edges at all, so it is removed and the build
  publishes. Ten hand-enumerated graph shapes pin the equivalence
  (`test_holds_any_revision_agrees_with_whether_a_purge_removes_anything`), each
  carrying its own chunk corpus so that no case can agree for the wrong reason
  through the withdrawn-chunk arm.

  **Nothing writes a node row.** `infrastructure/raptor/` is still an empty
  package and `SummarizationProvider` still a port with no adapter, so every
  test named above builds its fixture with raw SQL, exactly as the v3 suite did
  for `chunks.derived = 1` rows. The tables and the traversal over them land
  first so that the day a summary node exists it inherits a purge that already
  carries it rather than one designed a second time under pressure.

### Fixed

- **A withdrawal now publishes a purged index in the same `migrate apply`**
  ([#15](https://github.com/theurian/theurian/issues/15)), closing the T-17a
  status-axis disclosure window at its root rather than at the next
  `theurian index build`. Retiring, superseding or rejecting a revision — or
  changing its status in place — left the withdrawn rows in the published build
  until a rebuild; while they stayed, the visible ranking was scored against BM25
  collection statistics that counted them, so a value the caller may read moved
  with content it may not (see T-17a in
  [the threat model](../../docs/security/threat-model.md)). After the write
  transaction commits, `migrate apply` now derives and publishes a build with
  those revisions removed, synchronously, in the same command
  (`publish_purge_for_withdrawal`, wiring ADR-0024 decision 5). The set removed is
  computed against the published index's own build flavor: a default index purges
  draft/proposed/deprecated/rejected/superseded and any non-current revision,
  while an `--include-unapproved` index keeps the drafts and proposals it was told
  to hold and purges only what is withheld under every flag plus non-current
  revisions. **Scoped to the status axis** — `may_surface` reads only status; the
  deferred sensitivity, tenant and ACL axes are
  [#119](https://github.com/theurian/theurian/issues/119), and this does not claim
  to enforce them. Two residuals remain, both content-independent and bounded: a
  single request in flight at the pointer swap finishes against the pre-purge
  build (the swap protects the next request, not one already served), and a purge
  that fails leaves the stale build serving until a manual `theurian index build`
  — reported, not silent, through the apply's `indexPurge` (`published: false`,
  `failed: true`, and a `remedy` naming the rebuild).

  Not a breaking change to the `migrate apply` contract: its JSON gains an
  `indexPurge` object and, on a withdrawal, it swaps the active index pointer to
  the purged build — both additive. No existing field or behaviour is removed, and
  a withdrawal-free apply skips the purge, reporting `indexPurge` with
  `published: false` and `reason: "no-withdrawal"`.

- **BREAKING — `migrate validate` and `migrate apply` refuse a revision
  naming a `tenantId` other than `local` or an `aclGroup` other than
  `default`** ([#63](https://github.com/theurian/theurian/issues/63)).
  Neither field was enforced — no `AuthorizationProvider` is implemented
  anywhere in this tree — so a migration using either read as a security
  boundary that nothing checked. The schema keeps both fields and their type
  (ADR-0003: they describe the hosted deployment's shape); only their
  `description` changed. `migrate status` keeps exit 0 and gains
  `refusedIds`, naming the same migrations without gating on them.

  **If a migration naming a foreign tenant or ACL group was already applied**
  — possible only on `0.1.0.dev0` or `0.1.0.dev1` — the next `migrate
  validate` or `migrate apply` against that project refuses it with a
  different remedy than an unapplied revision gets: editing the field in
  place changes the migration file's checksum and trips the existing
  tamper-evidence check instead, which loops back to the same refusal. The
  working procedure: edit every offending `tenantId`/`aclGroup` to the
  default, delete `.theurian/state/`, then run `theurian migrate apply` to
  rebuild canonical state from the edited migrations — state is fully
  reconstructible from the Git-tracked migrations (FR-K4). This discards the
  tamper-evidence guarantee (FR-K5) for every migration applied before that
  point, so do it once, deliberately, not as a routine fix. Existing rows
  already written with a non-default tenant or ACL group are not migrated by
  this fix — it closes the write side only; nothing here rewrites canonical
  state or changes what `knowledge.search`/`knowledge.get` return.

- **`ClaudeCodeMcpConfig.install` now backs up `~/.claude.json` before the
  race-only removal branch destroys the user's entry** (SEC-18, closes
  [#27](https://github.com/theurian/theurian/issues/27)), matching the two
  sibling installers, `LaunchAgentManager` and `SystemdUserManager`, which
  already back up before overwriting their own files. The backup file is
  created 0600 from birth, via `O_CREAT | O_EXCL` rather than write-then-
  `chmod`, and two backups landing in the same UTC second get distinct names
  instead of overwriting each other. A backup that cannot be written aborts
  the removal and is reported as `install`'s own failure string, not raised
  as an uncaught `OSError`.

### Documentation

- **FR-R1 per-axis disposition register**
  ([#63](https://github.com/theurian/theurian/issues/63) phase 0, which closes
  the issue). `docs/architecture/requirements-analysis.md` gains one row per
  axis — Project, status, tenant, ACL group, sensitivity, validity window —
  recording what the pre-1.0 product does about each: enforced through `_scope`
  (Project, status), refused at write time
  ([#110](https://github.com/theurian/theurian/pull/110)), a caller-chosen
  `asOf` refinement ([#112](https://github.com/theurian/theurian/pull/112)), or
  a published label whose enforcement as a control is deferred to
  [#119](https://github.com/theurian/theurian/issues/119) (sensitivity), with the
  landing PR per row. Enforcing the three deferred axes (tenant, ACL group,
  sensitivity) is #119, the successor to #63. Two tests keep the enforced set
  from drifting from the documents: `test_gate_call_sites.py` enumerates every
  `may_surface` call site — following the import, so a bare, `as`-aliased, or
  module-attribute call all count — and pins both SECURITY.md's and the
  register's published axis lists, tokens and spelled count, to the
  `chunks.<column>` predicates `_scope` actually emits.
- **`may_surface`'s caller count corrected from four to five** in its `enums.py`
  and `mcp/results.py` docstrings
  ([#63](https://github.com/theurian/theurian/issues/63)). An AST scan of the
  shipped tree finds five call sites, not "four callers in three layers"; the
  fifth (`mcp/tools.py::_relation_is_visible`, which gates each relation
  endpoint on `knowledge.get`) landed after the count was written. The count is
  now pinned by a test rather than restated in prose.
- **Security-document claims naming a control whose component does not exist**,
  corrected together so the class does not survive the sweep
  ([#115](https://github.com/theurian/theurian/issues/115)). The threat model's
  T-11 no longer asserts "an `AuthorizationProvider` check precedes every read"
  — that port is a `Protocol` with no implementation — and names the mechanisms
  that do isolate projects (`projectId` validation and `_scope`'s WHERE
  predicate). T-10, SECURITY.md's RAPTOR sensitivity-boundary bullet, and the
  requirements-analysis R-14 risk row switch the RAPTOR tree-identity guarantee
  to the subjunctive the raptor package's own docstring uses, name Milestone 6
  as when it takes effect, and state the interim residual: no RAPTOR summary is
  generated, so there is none to leak.

## [0.1.0.dev1] - 2026-08-09

**If you installed `theurian` before today, upgrade.** `0.1.0.dev0` was the only
published version, and everything below was fixed in the repository without
reaching anyone who had run the command the shipped surfaces name
([#83](https://github.com/theurian/theurian/issues/83)). On `0.1.0.dev0`, an
install without the `daemon` extra gives you `theurian daemon start` raising
`ModuleNotFoundError` as a rendered traceback; `theurian daemon status` — which
the Claude Code plugin's `SessionStart` hook runs on **every session** —
printing a Rich traceback into that session; and a `theurian setup` that runs to
`DEGRADED` and leaves an env file, an OS service unit and an MCP connection
entry behind, pointing at a service that fails on every start.

```sh
uv tool install --python 3.13 --force 'theurian[daemon]==0.1.0.dev1'
# or: pipx install --python 3.13 --force 'theurian[daemon]==0.1.0.dev1'
```

The extra is the part that is easy to lose. `uv tool upgrade` and `pipx upgrade`
both re-resolve the spec they recorded, so an installation that recorded no
extras stays bare across an upgrade — it will carry these fixes and still not be
able to serve. What changes is that it now says so, by name and with the command,
instead of crashing. `--force` is what makes the line above repair an existing
installation rather than report success and change nothing; measured for pipx
against 1.16.6, and harmless for uv, which re-resolves in place regardless.

Nothing here changes the wire contract: `protocolVersion` stays `theurian/v1`,
no MCP tool's request or response shape moves, and no canonical state or index
needs rebuilding.

**This is a Core release only.** The Claude Code plugin versions and releases
independently (ADR-0001) and is unchanged at `0.1.0`; fixes to its shell hooks
that landed alongside these are named below where they bear on a claim, but they
are not delivered by this wheel.

### Changed

- **`--help` is plain text now: no panels, no colour.** Fixing the entry below
  meant turning Typer's Rich formatting off at the root app
  (`rich_markup_mode=None`), and that setting is what draws the rounded boxes
  around `Options` and styles the option names. Every `theurian … --help` falls
  back to Click's formatter and prints the docstring as written. Measured on a
  real terminal, `theurian setup --help` before and after:

  | | ANSI escape sequences | box-drawing characters |
  | :-- | --: | --: |
  | before | 134 | 187 |
  | after | 0 | 0 |

  This is cosmetic and it is the whole cost of the fix. It is named here because
  it is the change a user notices without being told, and a release page is
  where they will come looking for the reason.

### Fixed

- **`theurian setup --help` printed an install command with the `daemon` extra
  deleted from it** ([#99](https://github.com/theurian/theurian/pull/99)). The
  docstring says `uv tool install 'theurian[daemon]'`; what reached the terminal
  was `uv tool install 'theurian'`, one line above the sentence explaining that
  the extra is what gives `theurian daemon start` a server to run. So the
  surface that exists to keep a user out of the bare install told them to make
  one, and contradicted itself in the same paragraph.

  Typer parses help strings as Rich markup, and Rich reads `[daemon]` as a style
  tag. Measured against `rich` 15.0.0:

  ```
  'uv tool install 'theurian[daemon]''  ->  "uv tool install 'theurian'"
  'run it from [/usr/bin]'              ->  MarkupError: closing tag '[/usr/bin]'
                                            doesn't match any open tag
  ```

  The second row is the same defect one step worse — a path in square brackets
  takes `--help` from wrong to absent — and it is why the fix is not a smarter
  docstring.

  **The fix leaves markup rather than escaping the bracket**, and that
  distinction is the finding. Escaping to `theurian\[daemon]` was tried and
  reverted: `TYPER_USE_RICH=0` is a documented setting that formats through
  Click instead, and there the escape survives to the user as a literal
  backslash — `uv tool install 'theurian\[daemon]'`, which is not an installable
  requirement. **No single docstring is correct in both modes while markup is
  on**, so the escape did not remove the defect, it moved it between modes and
  broke a path that had been correct. `rich_markup_mode=None` takes the same
  Click path under both settings, which makes the source text the printed text
  everywhere.

  This was introduced by the entry below, not inherited: before it, the
  docstring named the bare command and printed the bare command, wrongly but
  consistently.

- **`uv tool install theurian` installed a Theurian whose daemon could not
  start.** `uvicorn` and the MCP SDK live in the `daemon` extra, so the next step
  of the documented flow ended in
  `ModuleNotFoundError: No module named 'uvicorn'` and a rendered traceback
  ([#78](https://github.com/theurian/theurian/issues/78)). The packaging split is
  kept — a CI image running only `theurian migrate` should not carry a web server
  (ADR-0014) — and the three faces of the defect are fixed instead:

  - `theurian daemon start` reports which extra is missing and the command that
    installs it, on the `--json` channel as well. The guard reads
    `ModuleNotFoundError.name` and re-raises anything else, so a broken Theurian
    is never answered with "reinstall the package that holds the broken file".
  - `theurian daemon status` no longer needs the extra at all. It imported the
    lock file's *name* from the module that starts the web server, so a bare
    install printed a traceback into every Claude Code session — that command is
    what the `SessionStart` hook runs. `LOCK_FILENAME` now lives in
    `theurian/daemon/instance.py`, beside the lock it names.
  - `core-present` reported `satisfied` for an install that cannot run a daemon,
    and setup went on to write an env file, an OS service unit and an MCP
    connection entry, ending `degraded` with a registered service that fails on
    every start. The step now reports `conflicting`, which aborts the run before
    anything is created.

- **Every surface that tells a user how to install Core now names the extra**:
  `uv tool install 'theurian[daemon]'` / `pipx install 'theurian[daemon]'`. That
  includes the two that execute — the `core-missing` compatibility remedy and the
  `core-present` step detail — which read one constant,
  `theurian.domain.extras.DAEMON_INSTALLERS`, so the two answers cannot drift.
  The quoting is required, not stylistic: unquoted, the bracket is a glob under
  zsh.

  `[daemon]` rather than `[all]`, measured: `sqlite_vec` and `opentelemetry` are
  imported nowhere in `src/`, so `[all]` differs by 12 distributions and by no
  behaviour.

  **Two surfaces still name the bare command, and this release page is one of
  them.** The entry as written under `[Unreleased]` named four, deferred to four
  then-open pull requests, and said re-deriving the count belonged to whichever
  landed last. All four have landed, so it is re-derived here. `git grep -l` for
  `uv tool install theurian` / `pipx install theurian` returns 15 files at this
  tag; `README.md` and `docs/security/threat-model.md` are no longer among the
  surfaces that *instruct* it, and two are:

  - `.github/workflows/release-core.yml`, which writes
    `Install: uv tool install theurian==<version>` into every GitHub release
    body — including this one. **If you arrived from that line, add the extra**:
    `uv tool install 'theurian[daemon]'`.
  - `docs/contributing/release.md`, whose post-release verification step
    installs bare. That one is read by maintainers, not by users.

  Both are release tooling rather than install advice, and both are tracked with
  the rest of the release gate in
  [#39](https://github.com/theurian/theurian/issues/39). Naming them on the page
  that carries the defect is the mitigation available without changing the
  workflow inside the release it publishes.

- **The `core-too-old` remedy named a subcommand that does not exist.** It read
  "Upgrade Core with `theurian upgrade`, or run /theurian:upgrade"; `upgrade` has
  never been registered in `cli/main.py`, so `theurian upgrade` exits 2 with
  `No such command` and `/theurian:upgrade` failed the same way
  ([#42](https://github.com/theurian/theurian/issues/42)). It now reads
  `uv tool upgrade theurian` / `pipx upgrade theurian`, from a single
  `CORE_UPGRADERS` constant.

  `CORE_MISSING` is the only outcome `cli.main.compat_check` cannot produce,
  because it always passes a parsed version. `core-too-old`, `core-too-new` and
  `protocol-mismatch` all reach production and all exit 3, so this is a remedy a
  user can really be handed. The plugin's `SessionStart` hook prints the whole
  verdict to stderr on every session that hits it, so the remedy that could not
  be followed was the one most likely to be read.

  **That last sentence is true only from
  [#90](https://github.com/theurian/theurian/pull/90), which is a plugin fix and
  is therefore not in this wheel.** `lib.sh` opened `set -euo pipefail` and
  `session-start.sh` sources it, so a bare assignment whose command exits 3
  aborted the hook before it printed anything. Measured against both revisions:
  `set -euo pipefail` gives exit 3 and no output at all, `set -uo pipefail` gives
  the warning, the verdict and exit 0. The dependency is named because the
  remedy corrected here is only ever *seen* on a plugin that carries that fix;
  it reaches users through a plugin release, not through this one.

  **It is not reachable in the shipped configuration, and this release does not
  make it reachable.** The plugin's declared floor is `0.1.0-dev.0`, which is the
  lowest `0.1.0` any Core can report, so no released Core is below it:
  `0.1.0.dev0` renders as `0.1.0-dev.0` and this release renders as
  `0.1.0-dev.1`. Measured against the declaration the plugin ships:

  ```console
  $ theurian compat check --plugin-version 0.1.0 --core-minimum 0.1.0-dev.0 \
      --core-maximum-exclusive 0.2.0 --protocol-version theurian/v1 --json
  { "outcome": "compatible", "coreVersion": "0.1.0-dev.1", … }
  $ echo $?
  0
  ```

  What makes `core-too-old` reachable is raising `coreCompatibility.minimum` in
  the plugin's `compatibility.yaml` — a plugin release, not a Core one. The
  entry as written under `[Unreleased]` said "the first Core release that moves
  either number makes it reachable"; moving Core's version *up* moves it further
  above the floor, and cutting this release is what falsified the sentence. The
  remedy is corrected before anyone can be handed it, which is the order this
  wants.

  **Delegation is the decision, not an omission.** A real `theurian upgrade`
  would make Theurian the thing that fetches its own wheel, and with it T-16's
  install-time verification; that is a larger commitment than a remedy string and
  is deliberately not taken. The remedy names no extra because both installers
  re-resolve the spec they recorded, so an install carrying `[daemon]` keeps it
  across an upgrade and a bare one stays bare. Measured against the real
  distribution, which settles it without any upgrade:
  `uv tool install 'theurian==0.1.0.dev0'` records no extras and has no `mcp`,
  `uvicorn`, `watchfiles` or `starlette`; `'theurian[daemon]==0.1.0.dev0'`
  records `extras = ["daemon"]` and has all four. Naming the extra would imply
  that upgrading repairs a bare install; it does not, and that user needs
  `uv tool install 'theurian[daemon]'`.

  The upgrade path was measured with `black`, because when this was written
  `theurian` had exactly one release and so could not be upgraded at all. This
  release is the second, and it is the first that can be. **uv installs the
  newest version its spec allows**, so
  `uv tool install 'black[d]==24.1.0'` followed by `uv tool upgrade black`
  reports `Nothing to upgrade`; dropping the `==` pin from `uv-receipt.toml` is
  what stands in for time passing. An earlier version of this entry omitted that
  step, which made the procedure it recorded a no-op — the observation was real,
  the published recipe was not. With the step: both receipts go
  `24.1.0 -> 26.5.1`, `aiohttp` absent throughout for `black` and present
  throughout for `black[d]`, each receipt keeping what it recorded. pipx 1.16.6
  drops the pin itself (`upgrading black from spec 'black[d]'`) and ran with
  `--backend pip`, its default backend requiring uv>=0.9.17 against this
  machine's 0.7.2.

## [0.1.0.dev0] - 2026-08-07

A development release, published to claim the `theurian` name on PyPI. Until
this, the name was unregistered while `theurian setup` and the plugin's
SessionStart hook both told a user whose machine has no Core to run
`uv tool install theurian` — a command that could not work, and that would have
installed somebody else's package had the name been taken first.

Everything below happened before Theurian had released anything at all:
Milestones 0 through 5, then the two groups that follow, which landed after
Milestone 5 and before the tag. The breaking changes named in it broke nothing
that had shipped, and the `#### Known limitations` sections are where this says
what it does not do.

**The first two groups reached this section after the release.** They were
written under `[Unreleased]`, and the move that
[`release.md` §2](../../docs/contributing/release.md) asks for was not made
before `core-v0.1.0.dev0` was pushed — so the published sdist carries a changelog
filing its own contents as unreleased, and the release body generated from this
section does not mention them. The release workflow's changelog guard did not
catch it: it checks that a section for the version exists and is not empty, not
that `[Unreleased]` is.

**The wheel carries no changelog**, so this reached the sdist and the release
page rather than an install: `[tool.hatch.build.targets.sdist]` lists
`CHANGELOG.md` and the wheel target ships `src/theurian` only. `uv tool install`
and `pip install` take the wheel, which means the reader most exposed to the
error is the one reading the release notes to decide whether to upgrade.

### Milestone 6 — the index lifecycle

#### Added

- **An API to purge a build: `IndexStore.derive_purged`** (ADR-0024). Given a
  published build and a set of withdrawn revisions, it copies the build with
  `sqlite3.Connection.backup`, deletes those revisions from the copy, restamps
  `index_metadata` for the new build, verifies the result, and produces a new
  file fit to publish. The published file is never written to, so a search
  reading it is unaffected.

  **Not yet wired to withdrawal.** The automatic trigger — a purge fired whenever
  a revision is retired, superseded or rejected — is ADR-0024 decision 5, and it
  has no caller in this release: `derive_purged` is invoked by tests only. It is
  the next slice and closes [#15](https://github.com/theurian/theurian/issues/15);
  this release *advances* #15 by landing the mechanism and the schema the trigger
  will use.

  Measured on a real 400-document index with embeddings: 2,732 chunks to 1,229,
  1,503 rows removed in 847 ms, and the purged build answers **identically** to
  an index that never held the withdrawn documents — chunk ids and BM25 scores to
  ten decimals, on both the word index and the trigram index — while a stale
  control differs on every query.

  `shutil.copyfile` and `VACUUM INTO` are both rejected, and ADR-0024 records
  why: the first drops the `-wal` sidecar, and the second rests on rowid
  stability SQLite documents as *not* guaranteed for tables without an INTEGER
  PRIMARY KEY, which `chunks` is — while both FTS5 tables are external-content
  keyed on `chunks.rowid`.

- **Withdrawal is transitive over derived content.** A row built from a withdrawn
  chunk holds that chunk's content: a purge can delete a passage, and cannot
  delete a sentence out of a summary of it. `chunk_derivation` records the
  provenance and the purge walks it transitively, so a summary, a summary of that
  summary, and a node with mixed provenance all go with the withdrawal. A derived
  row whose provenance cannot be resolved is deleted rather than kept.

- **`theurian index gc` reclaims superseded index builds.** Named by ADR-0007,
  ADR-0016 and ADR-0017 since Milestone 1 and never implemented until now. It
  deletes builds the published pointer does not name, and refuses to touch four
  things: the published build, any build whose id sorts above it (a build that
  has not published yet), anything under a `.building` suffix (a writer still in
  progress, or a crash's leftovers, which it reports as `strandedBuilding`), and
  everything, when the pointer cannot be read. `--dry-run` reports the plan
  without deleting.

#### Changed

- **Publishing an index build no longer reclaims the build it replaced**
  (ADR-0024 point 6). The old file stays on disk until `theurian index gc` runs.
  Reaping at publish is what made ADR-0022's "the previous build is not deleted"
  false, and measured against a concurrent reader it cost 2,627 errors against 40
  answered searches in 1.5 seconds. Builds now accumulate — ten publishes leave
  ten files — which is the cost that makes `index gc` load-bearing rather than a
  tidy-up.

- **A search holds one read connection for the whole request** (ADR-0024 point
  7), and index files are opened `mode=ro`. Together these let a request survive
  a `theurian index gc` that unlinks its build mid-request: the held descriptor
  keeps the file readable on POSIX, and `mode=ro` stops a read of a reaped path
  from conjuring an empty database where the build was. Measured, one request of
  four index reads with the unlink after the first: 4 of 4 answered with the
  session held, against 1 of 4 without.

#### Changed — BREAKING

- **`INDEX_SCHEMA_VERSION` 2 → 3**, adding `chunks.derived` and
  `chunk_derivation`. **Every existing index reports `index-schema-mismatch` and
  falls back to the substring scan until `theurian index build` runs.** That is
  the designed response to an index schema change and not a regression: the index
  is derived and disposable, and ADR-0022 point 3 exists so that a schema change
  costs an index rebuild and nothing else — no canonical `SCHEMA_VERSION` bump,
  no state hash change, no canonical database invalidated. `theurian index
  status` reports the mismatch and names the command.

  The two new columns are for RAPTOR (ADR-0008), which does not exist yet. They
  land ahead of it because withdrawal has to be transitive from the first build
  that has anything to be transitive over — designing the purge after summary
  nodes ship means designing it twice, the second time under pressure from a
  feature already in use.


- **`IndexStore`'s three search methods return `RetrieverPage`, not
  `tuple[Ranked, ...]`** ([#16](https://github.com/theurian/theurian/issues/16)).
  The page carries the rows and an `exhausted` flag, which the depth loop reads
  instead of inferring exhaustion from a row count. No MCP schema impact:
  `src/theurian/mcp/` never calls a `search_*` method, so the outward breaking
  cost is zero — the break is to the port, its one adapter, and the six
  test-side implementations `rg "def search_lexical" packages/theurian-core`
  finds: `_ScriptedIndex`, `_CountingIndex`, `_NeverFinished`, `_TwoOpinions`,
  `_TwoRankings`, and `_CountedStore`, across five files. Four of them answer
  through the new `fakes.pages` helper; `_CountedStore` delegates to the real
  store and `_NeverFinished` builds a page the helper deliberately cannot.

  One expression used to read three different `limit` semantics off one number —
  a ceiling in `search_lexical`, a floor in `search_substring`, absent in
  `search_dense` — and the port's docstrings stood in for a type. An adapter that
  capped its output above `limit` without that cap being exhaustive satisfied
  every word of them and cost the caller rows it never learned it lost.
  `exhausted` may be `True` only when the implementation has verified there is
  nothing further; the SQLite adapter fetches `limit + 1` and reports whether the
  extra row arrived, then drops it, so `limit` stays a true ceiling.

- **`SqliteIndexStore._scan_cache` is deleted**, as its own docstring
  instructed. It was a security mitigation rather than an optimisation: it made
  the second call to the scan below the trigram floor cost no second pass over
  the corpus, where the second call itself was a step function of how many rows
  the canonical store had withheld. There is no second call now — that branch has
  read and scored everything by the time it returns, so it reports itself
  exhausted on its first. Measured against a real 400-document index with a
  two-character CJK query: one port call at 0, 49, 50, 51 and 99 withheld rows,
  where 51 and 99 cost two before.

  The cache also required a fresh `SqliteIndexStore` per search, because a
  pooled one would have leaked one caller's withheld-row count into another
  caller's latency. That requirement went with it, replaced by something
  smaller and checkable: the store holds no per-instance state at all.

- **`tests/integration/test_scan_cache.py` becomes `test_scan_exhaustion.py`.**
  Its cross-request test was deleted rather than moved — with no cache, two
  requests cost two scans whatever happens, so it would have sat in the suite
  green and guarding nothing.

#### Known limitations

- **The timing residual on the truncating retrievers is unchanged, and #16 does
  not close it.** A first pass in which too many rows were withheld still has to
  fetch deeper to keep fifty visible rows, which follows from the definition of
  the depth loop rather than from any defect in it: measured, `search_lexical`
  and the trigram lookup still make two calls at 51 withheld rows and one at 50.
  Only an index that no longer holds withdrawn rows removes it
  ([#15](https://github.com/theurian/theurian/issues/15)).

### Changed after Milestone 5

- `theurian setup` and `theurian doctor` now explain the `artifact-integrity`
  step's `not-applicable` as a property of Theurian rather than of the world.
  The old wording denied that any record existed to check against, and promised
  verification at the first tagged release. Both held only until a `core-v*` tag
  was cut, and `core-v0.1.0.dev0` cut it: from that moment the first of them
  would have told every user not to bother checking a file published on that
  very release page, which is the only mitigation available while the control is
  unimplemented. The step still reports `not-applicable` and still verifies
  nothing; it now says that Theurian does not verify the artifact it is running
  from, which holds on both sides of a tag. Checking a download against the
  checksums published with it remains a manual step
  ([#39](https://github.com/theurian/theurian/issues/39), T-16).

  *The superseded sentences are deliberately not reproduced here.* A version's
  section is published verbatim as the GitHub release body, a short distance
  above a line stating that every artifact below is covered by `SHA256SUMS` —
  which is the defect this entry records, and a changelog is no place to
  reintroduce it. That did not happen for this entry, because it was still under
  `[Unreleased]` when the tag was pushed; the published release body for
  `core-v0.1.0.dev0` does not contain it.

### Fixed after Milestone 5

- **A `minimum` did not bound anything, and neither did a `maximumExclusive`.**
  `theurian compat check` translates Core's PEP 440 version into SemVer before
  comparing it against a plugin's declared range, and the translation put
  versions in the wrong order. Declaring `minimum: 0.1.0-dev.0` — the floor this
  repository's own plugin ships, and the one the documentation recommends for an
  unreleased Core — accepted `0.1.0.dev1`, refused `0.1.0a1`, `0.1.0a2` and
  `0.1.0b1`, then accepted `0.1.0rc1` and `0.1.0` again. A floor with a hole in
  the middle is not a stricter floor; it is a floor that means nothing.

  Two rules disagree between the ecosystems, and both were live. PEP 440 sorts
  `.devN` below every pre-release phase, while SemVer §11.4.2 compares the phase
  words as ASCII and puts `dev` between `beta` and `rc`. PEP 440 sorts
  `0.2.0a1.dev1` below `0.2.0a1`, while SemVer §11.4.4 ranks the longer
  identifier list higher. Over the release train the tests now enumerate — 40
  versions of one release, so 780 ordered pairs — 99 came out backwards.

  `maximumExclusive` is the same comparison read from the other end and failed
  the same way: a ceiling of `0.1.0-alpha.1` refused `0.1.0.dev0`, which is
  *below* it, and accepted `0.1.0a0`, which is above.

  Both bounds are now ordered by Core's release train — `dev` < `alpha` <
  `beta` < `rc` < final, with a development build below the pre-release it
  precedes — applied to the declaration's bounds and to Core's own version
  alike. Declarations keep their existing spelling and verdicts keep printing
  it; neither `compatibility.yaml` nor the published schema changes.

  **Not breaking, measured rather than argued.** Every `minimum`/
  `maximumExclusive` pair this repository declares was resolved against 200
  versions spanning five releases, under the old comparison and the new one. No
  pair changes verdict against the Core that ships (`0.1.0.dev0`), and the only
  pair whose meaning changes at all is `0.1.0-dev.0`/`0.2.0`, where all 24
  changes run `core-too-old` → `compatible`: versions that were wrongly refused
  are now accepted, and nothing that was accepted is now refused.

  What *can* move the restrictive way is a bound that names a pre-release phase.
  A ceiling of `0.1.0-dev.0` stops accepting every `0.1.0` alpha — correctly,
  because those are newer than it. If you maintain a client whose `minimum` or
  `maximumExclusive` carries a `-dev`, `-alpha`, `-beta` or `-rc` segment,
  re-read it against the ordering above. A bound with no pre-release segment is
  unaffected.
- **A PEP 440 development segment carrying no number was dropped whole.** PEP
  440 makes that number optional and defaults it to 0, but the parser decided
  the segment was *present* by asking whether its number was — so `0.2.0.dev`
  parsed as `0.2.0`, a development build read as the finished release it
  precedes. That is the failure this translation exists to prevent, inverted:
  rather than being told Core was missing, a client would have been told it had
  shipped.

---

### Milestone 5 — hybrid retrieval

#### Added

- **Reciprocal Rank Fusion** over three retrievers (FR-R2): a word index, a
  trigram substring index, and — opt-in — a dense one. Fusion uses *ranks*,
  never scores. BM25 and cosine similarity are not comparable quantities, and
  neither are two BM25 scores computed over different token spaces; normalising
  any of them onto one scale needs assumptions about their distributions that do
  not survive a change of corpus, tokenizer, or embedding model (ADR-0021).
- **A trigram index beside the word index**, which is what makes languages
  without word spacing searchable at all. `unicode61` splits on whitespace and
  punctuation only, so `署名付きトークンを持つ` is one token and `トークン`
  matched nothing. Both indexes feed the fusion as separate retrievers; the
  trigram one is not a replacement, because trigrams are worse at the exact
  identifiers engineering queries are mostly made of — a trigram search for
  `cat` matches `concatenate`. (ADR-0023)
- **Document chunking** on structure first and length second — headings, then
  paragraphs, then sentences, then words, then a hard character cut as the
  backstop that always terminates.
- **A retrieval index in its own SQLite file**: FTS5 for terms, an exact vector
  scan for the rest. Separate from the canonical store on purpose — the
  canonical `SCHEMA_VERSION` is an input to the state hash (ADR-0017), so
  co-locating them would make every index change invalidate every canonical
  state.
- **A default embedding provider** that is deterministic, local, and needs no
  API key: hashed character trigrams. It is **not a semantic model, does not
  claim to be, and is no longer on by default** — see the breaking change below.
- **Diversification and token budgeting** (FR-R4). At most N chunks per item, so
  one long document cannot take every slot; packing strictly in rank order,
  never a knapsack fill that would trade relevance for a number the caller
  cannot see.
- **`theurian index build` and `theurian index status`.** Status reports three
  hashes — what the knowledge *is*, what the database *holds*, and what the
  index was *built from* — because all three can differ, and comparing only the
  last two calls an index fresh exactly when someone most needs to be told
  otherwise.
- **`theurian project register --project-id <id>`**, which is how a directory
  name collision is broken. See the breaking change below.
- **`StateDatabaseUnreadableError`**, the one error every read of the canonical
  state database answers with when the file cannot be interpreted. It carries
  the failing exception's **type** and never its message, because every
  converter the store reaches for quotes the value it would not accept:
  `datetime.fromisoformat` quotes the string, each enum quotes the member it
  could not find, and every domain value object renders its argument with `!r`.
  See Security, below, for what that used to cost.

  It lives in `theurian.infrastructure.sqlite.connection`, not beside the store
  that raises it most — opening a connection interprets the file too, and
  `write_transaction` opens one without going through the store at all. The real
  exception travels on `__cause__` for whoever debugs it; every CLI path that
  would have rendered that cause to a terminal is converted (see Changed).

#### Changed

- **BREAKING — `knowledge.search` response shape.** The flat `note` string is
  replaced by a structured `retrieval` object carrying `mode`, `indexed`,
  `stale`, `staleAgainst`, `indexesUnapproved`, `indexBuildId`,
  `embeddingModel`, `fallbackReason`, `snapshotId`, `usedTokens`,
  `droppedForBudget`, and `note`. Each hit gains `foundBy` (which retrievers
  surfaced it) and `fusedScore`. A ranking nobody can explain is a ranking
  nobody can debug.

  `snapshotId` is FR-R5's provenance realised once per response rather than
  once per hit: every hit in one answer is resolved through one canonical
  connection, so a per-hit copy would repeat one string. It is byte-identical
  to `knowledge.status.stateHash`, so a caller holding one can compare it
  against the other without a second call, and it is query-independent by
  construction — which is what makes it safe to publish at all (see Security).

  **The shape is now the same on both answer paths.** The ranked path and the
  unranked fallback used to publish different key sets — `stale`,
  `staleAgainst`, `indexBuildId`, and `embeddingModel` only on the ranked one,
  `fallbackReason` only on the fallback — which let a client branch on key
  *presence* rather than on a value. Every key now appears on both responses;
  one that does not apply to a given path carries `null` rather than being
  omitted.

  `retrieval.mode` takes five values: `substring` for the unranked fallback,
  then `lexical`, `dense`, `hybrid`, or `none` on the ranked path, according to
  which retrievers actually contributed a result that survived the canonical
  re-check. `none` is new: an empty result set used to report `lexical`,
  indistinguishable from "the word index answered and found nothing" — exactly
  what a v1 index missing its trigram table, or an embedder whose vectors do
  not match the corpus, produces.

  When the answer came from the unranked fallback, `retrieval.fallbackReason`
  says which of seven things happened — `no-index`, `index-pointer-invalid`,
  `index-file-missing`, `index-schema-mismatch`, `index-unreadable`,
  `index-project-mismatch`, or `unapproved-not-indexed`. All seven used to
  produce the same sentence, "no retrieval index has been built for this
  project", which is true of exactly one of them; the rest told a user to run a
  command they had already run and said nothing about the one that would have
  helped.

  **BREAKING — `withheldSuperseded` is removed** from the `retrieval` object.
  It was a per-query count of matches the caller was not allowed to see. See
  Security, below: it turned out to be a side channel, not a courtesy.

- **BREAKING — the index schema is version 2; existing indexes must be rebuilt.**
  The trigram table is new, and `INDEX_SCHEMA_VERSION` went 1 → 2 with it. Run
  `theurian index build`. Nothing canonical is affected: the index is derived and
  disposable, and this is the lifecycle separation ADR-0022 exists for, exercised
  for the first time.

  A version-1 index is detected rather than silently losing its trigram half.
  `SqliteIndexStore.is_searchable` compares the stored schema version against
  the one this build expects before any query runs; a mismatch is reported as
  `retrieval.fallbackReason: "index-schema-mismatch"` and `indexed: false`,
  with an unranked substring scan still answering the question underneath it.
  This landed in Milestone 5, not Milestone 6 as ADR-0022 and ADR-0023 said —
  the check shipped later in the same milestone, after those ADRs were
  written, and the ADRs were not updated to match until now.

- **BREAKING — `theurian index build` refuses to publish an index with zero
  chunks when the canonical state holds knowledge.** Publishing it used to put
  a correct-looking empty index in place — every later search answers
  `count: 0` with `indexed: true`, and `theurian index status` reports nothing
  to do, which is the exact shape a project-id mismatch takes. The build now
  exits 1 and names every project id the canonical store actually holds
  knowledge under.

- **BREAKING — `theurian index status` gains `projectId`, `indexProjectId`, and
  `orphaned`; `active-index.json` now records `projectId`.** Every chunk is
  stamped with the project id that built it, so an index built for a different
  id answers every query with nothing while still reporting `indexed: true`.
  A pointer written before this field existed cannot be checked, so it is
  treated as `orphaned` too — deliberately, because the command exists to avoid
  asserting a freshness it has not established, and a pointer that predates the
  check has none to assert. `knowledge.search` reports the same class of
  mismatch as `retrieval.fallbackReason: "index-project-mismatch"`.

- **BREAKING — dense retrieval is off by default.** `SearchRequest.use_dense` and
  the MCP parameter `useDense` both default to `false`, so a healthy default
  search now reports `retrieval.mode: "lexical"` rather than `"hybrid"`.

  This is measured, not cautious. Against a real corpus, **91% of unrelated
  natural-language questions cleared the bundled embedder's similarity floor**,
  while the lowest genuinely related query scored below the unrelated median. The
  distributions overlap; no threshold separates them, because what the embedder
  measures is English surface-form overlap and not topical relevance. The floor
  in the code was calibrated against random strings, which turned out to be the
  easy case and the wrong population to calibrate on.

  The retriever is kept and made opt-in rather than deleted, so the code path
  stays exercised and works the day a real model is configured through the same
  port (ADR-0009). `theurian index build` still writes embeddings unless
  `--no-embeddings` is passed, so opting in needs no rebuild.

- **BREAKING — a project id already registered to another root is refused.**
  `ProjectRegistry.register` used to overwrite. Ids default to the directory
  name, directory names repeat, and registering `team-two/api` therefore
  re-pointed the id `api` at the newer root — after which an agent working in
  `team-one` that asked for `api` was served `team-two`'s knowledge, with no
  error and nothing in the answer naming the repository (SEC-13). Registration
  that used to succeed now exits 1 and names the conflict. Break it with
  `theurian project register --project-id <id>`. Choosing a suffix automatically
  would have been worse: an already-configured agent keeps naming `api` and would
  silently follow the id to whichever project kept it.

  **BREAKING, and its mirror.** The same refusal now also applies the other way
  round: a root that already has an id being registered under a *second* one.
  An id is stamped into every canonical row and index chunk at the moment it is
  written, and `migrate apply` is idempotent, so using `--project-id` to rename
  an already-registered project produced a second, empty project rather than a
  renamed one — every search under the new id answered `count: 0` with
  `indexed: true`, and `theurian index status` reported nothing to do. To
  rename a project: `theurian project unregister <old-id>`, register the new
  id, delete `.theurian/state/`, then `theurian migrate apply` and `theurian
  index build`.

- **Project id resolution order changed** to: explicit `--project-id`, then the
  registry keyed by *root path*, then the directory name. Without the middle
  step, a project registered under a disambiguated id would still be addressed by
  the colliding default on its own command line — the CLI writing to one project
  while every agent reads the other.

- **BREAKING — the project registry validates every entry as it reads it, and a
  question keyed by root path refuses while any entry is unreadable.**
  `ProjectRegistry.load` used to return whatever `json.loads` produced, so an
  entry that was not a registration reached its caller unchecked. Three
  consequences, each reproduced against the previous behaviour before being
  written here:

  | Registry contents | Was | Is |
  | :-- | :-- | :-- |
  | an entry naming no `rootPath` | `project list` reported it as an ordinary project, `count: 2` | skipped by `load`; `count: 1`, and the id appears under `unreadable` |
  | an entry naming no `rootPath` | `id_for_root` matched it against **every** directory | every root-keyed question refuses |
  | a JSON array, or truncated JSON | `AttributeError: 'list' object has no attribute 'items'`, as a Rich traceback | reported as `{error, remedy}` with exit 1 |

  The middle row is the one that mattered. `Path("").resolve()` is the *calling
  process's* current working directory, so `entry.get("rootPath", "")` made an
  entry with no root claim whichever directory the command happened to run from:
  `id_for_root` returned that id for a completely unrelated repository, and
  `resolve_context` then addressed that repository's knowledge under it
  (SEC-13). An empty `rootPath` is now rejected as firmly as a missing key.

  A malformed entry invalidates only itself. It still holds its id, so `register`
  refuses to reclaim it — with its own remedy, rather than a collision message
  that assumes a readable `rootPath` to report — and `unregister` can remove it.
  Both read the raw file rather than `load`'s validated subset, or registering
  one id would erase a different id's broken entry as a side effect of an
  unrelated write.

  **`ProjectRegistry.ids_for_root` raises rather than answering "not
  registered", and that is the breaking half.** An entry is skipped exactly
  because it names no root, so "is that entry this directory's registration?"
  has no answer: the field that would settle it is the field that is missing.
  Answering `()` sends `resolve_context` to `derive_project_id`, which addresses
  the directory by its *name* — the id that may already belong to the project it
  collided with, which is the misrouting the collision refusal above exists to
  prevent.

  The blast radius is asymmetric on purpose. Questions keyed by an **id** keep
  working: `theurian project list`, `theurian project unregister` (the remedy),
  the setup registry scan, and every MCP tool — so one hand-edited line does not
  stop a daemon that serves every other project on the machine. Questions keyed
  by a **root path** refuse: resolving the project for the working directory,
  and `theurian project register`, which asks the same question to enforce "one
  root, one id" and therefore stops machine-wide until the entry is removed.
  That last part is accepted rather than special-cased — the plain `register`
  form resolves its context from the working directory and would refuse there
  regardless, so an exception would reach only the `--project-id` form, in
  exchange for a safety argument harder to check than the refusal it removes.

- **`theurian project list` gains an always-present `unreadable` field**, naming
  the ids whose entries `load` skipped, plus a `remedy` when it is non-empty. It
  is emitted even when empty, because a consumer that has to branch on whether a
  key is present will eventually forget to. Reported here rather than only where
  it breaks something: this is the command every other surface sends a user to,
  and the id it now prints is the argument `theurian project unregister` needs.
  A registry that cannot be partitioned into entries at all — truncated JSON, a
  JSON array, arbitrary bytes — is now reported by this command rather than
  escaping as a traceback, which mattered most here because this was the one
  place the "delete it and re-register" remedy never reached.

- **BREAKING — the `project.list` MCP tool gains two required response fields,
  `unreadable` and `remedy`, and its output is published as a schema for the
  first time.** Adding a required property is a breaking change under
  `schemas/README.md`'s compatibility rules, and it is named as one here even
  though a client that ignores unknown keys will not notice: the rule is about
  what the contract *permits*, and a response missing either key is now invalid.

  Both are always present — `remedy` carries `null` when `unreadable` is empty —
  because emitting a key only when it applies makes "nothing is unreadable"
  indistinguishable from "this daemon predates the field". `count` sizes
  `projects` alone and excludes the unreadable ids, since an entry naming no root
  path can be queried by nothing.

  `schemas/mcp/project-list-response.schema.json` is the contract, published
  after three milestones in which `project.list` was the tool an agent calls to
  find out what this daemon can answer for and the only one whose shape was not
  written down.

  **`projects` and `unreadable` are two reads of one file, not a partition of
  one snapshot.** `load()` and `unreadable_ids()` open the registry
  independently, so a registration landing between them can leave an id in both
  lists or in neither. Stated as it is rather than as the cleaner guarantee it
  resembles: a caller must not compute the size of the registry file by adding
  the two, and must not treat membership of one as proof of absence from the
  other. The fix is a single-snapshot read in `ProjectRegistry`, which is not
  written and not scheduled.

  **This wire change turned nothing red, and that is the finding.** Every test
  covering the MCP tools, the schemas and the wire contract — 186 of them when
  the fields were added, 189 now — was green before and after, because no
  assertion anywhere in the repository pins `project.list`'s response shape. The
  tool's own test reads `count` and one `projectId` and never looks at the key
  set. It is the same gap that let
  `knowledge.search`'s response shape change with the whole suite passing; that
  one was closed with a conformance test this milestone and this one was still
  open until the schema was written. The guarantee now rests on the schema —
  and therefore on the schema being *checked against a real response*, which is
  the rule stated in `schemas/README.md` and the work item it names.

  **The schema deliberately puts no pattern on `projects[].projectId`, and that
  was settled by measurement rather than by transcription.** Nothing validates
  registry *keys*: `load()` reads only `rootPath`, so a hand-edited
  `{"Not An Id": {"rootPath": "/valid"}}` loads and `project.list` publishes
  `projectId: "Not An Id"`. Ids that Theurian creates are lowercase kebab-case
  and `ProjectId` enforces that, but a registry key is not a `ProjectId`.
  Constraining the published field to the slug pattern would have produced a
  schema that rejects the product's own output — which is exactly the defect
  this milestone shipped in round one and corrected in
  `knowledge/retrieval-result.schema.json` (see Fixed, below).

- **`protocolVersion` stays `theurian/v1`, and that is a decision rather than an
  omission.** Milestone 5 makes several breaking wire changes — the
  `knowledge.search` response reshape, the removal of `withheldSuperseded`, and
  the two required fields above — and none of them bumps it. No published
  version of Core has ever lacked them, so no plugin can be pinned to a v1 that
  lacks these fields, and bumping would publish a `theurian/v2` whose v1 was
  never shipped. The version's unit
  is a released protocol; what protects an integrator is this changelog, which
  names each break. `protocolVersion` bumps on the first breaking change *after*
  the version that first carries `theurian/v1` — Milestone 5's set is the
  content of v1, not a departure from it. Recorded here because a reader who
  finds three breaking wire changes and an unchanged protocol version is
  entitled to know which of the two is the mistake.

- **Every project-scoped MCP tool now tells "not registered" from "registered
  and unreadable".** The two need opposite remedies and used to share one
  message, which sent half its readers into a loop: `theurian project register`,
  be told the id is already in use, read the same advice again. An id whose entry
  cannot be parsed is now named as such and pointed at `theurian project
  unregister <id>` first. The `Registered:` list is assembled from the entries
  that loaded, so the skipped ids are named beside it rather than merged into it
  — merged, they would inherit the `register` remedy that cannot work; omitted, a
  user comparing the answer against their own registry file finds a project
  missing from both the list and the explanation.

- **BREAKING — `theurian project status --json` reports `registered: null`, a
  third value for what was a boolean**, and gains an always-present `unreadable`
  list. `null` means "cannot be told": the registry holds an entry that names no
  root path, and this directory is inside a Git repository, so `false` would be
  the same guess `ids_for_root` refuses to make. A plain "not inside a Git
  repository" keeps its honest `false` rather than being dragged into an
  ambiguity it cannot be about. The command still exits 0 and carries the remedy
  in the payload, because a confused user reaches for `project status` first and
  a report with no way out is what it used to give them. A consumer treating
  `registered` as a boolean now sees `null` where it previously saw `false`.

- **`theurian setup` reports a registry it cannot read as `conflicting` rather
  than as a missing registration.** The project-registration step asked the
  registry for entries and scanned them by root path, which silently skipped an
  unreadable one and reported `MISSING` beside a remedy that cannot work —
  registering is refused while an entry that might hold this root's id is
  unreadable. It now asks the same question `ids_for_root` answers, and reports
  the impossibility on the first screen a person reads when something is broken.

- **BREAKING — `maxTokens` now pays for the whole response, not only the
  results, so the same budget returns fewer of them.** `projectId`, the echoed
  `query`, `count` and the entire `retrieval` block — the `note` above all,
  which is a paragraph of prose — travel with every answer and were charged to
  nobody. Measured at 138 to 171 tokens of fixed overhead on a ten-document
  project: a caller asking for 2,000 was sent 2,030 and told the answer had
  cost 1,860. The envelope is now reserved from the budget before any result is
  packed, so on that same project the default `maxTokens=2000` returns **9
  results with `droppedForBudget: 1`** where it used to return 10 and overshoot.
  `usedTokens` still means what it meant — what `results` cost — rather than
  quietly becoming a different number; charging honestly for the envelope did
  not have to wait for a wire-contract change. A budget smaller than the
  envelope still returns one result rather than none.

- **BREAKING — `SearchRequest` has no `limit`, and `substring_answer` and
  `hybrid_answer` require the caller's `ActiveState`.** Both are Milestone 5
  APIs changed within Milestone 5, so no released version is affected; they are
  named here because this changelog is what anyone integrating against Core is
  reading.

  `SearchRequest(query=..., limit=10)` no longer type-checks. `limit` and the
  token budget both moved to `ResultRequest`, which is applied on the far side
  of the canonical gate. Applying `limit` to *candidates* let a withheld
  document consume a result slot — see Security, below — and a field that
  cannot be set cannot be applied in the wrong order. What is left on
  `SearchRequest` bounds the work rather than the answer: `CANDIDATE_DEPTH` per
  retriever, and `per_item` per document.

  `substring_answer(database, *, project_id=...)` becomes
  `substring_answer(database, *, state=..., project_id=...)`, and `hybrid_answer`
  takes the same new parameter in place of reading the active state itself.
  Both publish `retrieval.snapshotId`, and resolving it here rather than
  receiving it is the read that can disagree with the one that chose the
  database: a pointer replaced by `migrate apply` mid-request makes the field
  name a state the results did not come from, and a pointer deleted mid-request
  makes it `null`. The caller passes down the state it already resolved to
  choose the database, so `snapshotId` names that state and is never empty.

- **BREAKING — retrieval takes a visibility, because the canonical gate moved
  inside the ranking.** Again Milestone 5 APIs changed within Milestone 5, so no
  released version is affected. No `knowledge.search` response field changes:
  this is the application layer and the `IndexStore` port only.

  | Was | Is |
  | :-- | :-- |
  | `RetrievalService.search(request)` | `search(request, visible)`, taking a `Visibility` — with **no default** |
  | `ResultGate.admit(request)`, with candidates and passages carried on `ResultRequest` | `admit(request, source)`, where `source` is `Callable[[Visibility], SearchOutcome]`; `ResultRequest` has neither field |
  | `SearchOutcome.embedding_model` | `RetrievalService.embedding_model(use_dense=...)` |
  | `IndexStore.search_dense(vector, *, project_id, limit, include_unapproved)` | the same without `limit`; it returns its whole ranking |
  | `IndexStore.token_sizes(chunk_ids, *, project_id)` | removed |
  | `theurian.mcp.results.may_surface` | `theurian.domain.enums.may_surface` |
  | — | `CanonicalReadSession.get_item` is now part of the protocol |

  A caller of `search` has to name whose view it is ranking for. "Everything is
  visible" is the assumption this milestone chased through five separate fields
  (see Security, below), and a default parameter is how it comes back — so there
  is none, and a test that wants an ungated ranking says so at the call site.
  `admit` takes a *source* of candidates rather than a finished list for the same
  reason: there is nowhere to put a list that was ranked without a visibility.

  The rest follow from the same move. `embeddingModel` left the search outcome
  because it was the same value for every query against one index, and a value
  answerable without a query cannot be made to vary with one. `search_dense` lost
  its `limit` because an exact vector scan scores every embedding whatever it is
  asked for — the parameter bounded the output while appearing to bound the work,
  which would have misled the caller that now re-asks at greater depth.
  `token_sizes` went with the budget that used it: pricing a retrieved chunk
  charges for text the canonical store may still withdraw. `get_item` is on the
  read protocol because clearing a ranked row needs the *item*'s status now, not
  the status the index recorded at build time.

- **BREAKING — a damaged canonical state database is reported as
  `{"error", "remedy"}`, and `theurian migrate status` and `theurian migrate
  apply` exit 4 rather than 1.** These paths used to leave the command through a
  Rich traceback: exit 1, an empty stdout where `--json` promises a document
  (CP-2), and — because a traceback renders `__cause__` one line below the
  exception — the corrupted cell printed to the operator anyway, undoing the
  withholding described under Security.

  | Command, over | Was | Is |
  | :-- | :-- | :-- |
  | `migrate status`, `migrate apply` — a damaged cell | exit 1, a traceback quoting the cell | exit 4, `{"error", "remedy"}` |
  | `migrate apply` — a real immutability violation, **healthy** database | exit 1, a traceback | exit 4, `{"error", "remedy"}` |
  | `index build` — zero chunks, canonical store unreadable | exit 1, a traceback quoting the cell | exit 1, `{"error", "remedy"}` |

  A caller branching on exit 1 has to branch on 4; a caller parsing `--json` is
  handed a payload where it used to be handed nothing. Failures print to stderr,
  as every other failure in this CLI does. Measured across `migrate status` and
  `migrate apply` over `migration_history.migration_id`,
  `migration_history.checksum` and `schema_metadata.schema_version` — six
  (command, column) positions, all six now exit 4 carrying both keys and none of
  the cell. The immutability row keeps its own remedy, "Fix the migration set,
  then retry", because it is the caller's migration set that is wrong and not
  the file.

  The `except` is over `TheurianError` rather than over the types known to
  arrive today, because a guard's promise reaches only as far as the exception is
  caught. It wraps `write_transaction` itself rather than the body of the `with`:
  opening a connection interprets the file, so `schema_metadata.schema_version`
  raises before the body runs. The remedy is chosen per family — a file this
  build cannot interpret, another process holding the write lock, a migration set
  the store refused — and the one that deletes something is the one that is never
  the default.

  `index build`'s row is a second read session, opened after the build to ask the
  canonical store whether indexing nothing was correct, over rows the build never
  reads. It is reached only when the build indexed zero chunks, which no fixture
  produced; measured on a project whose only knowledge is `draft`, with
  `projects.registered_at` or `projects.root_path` overwritten. The partially
  built index file goes with the refusal, matching every other branch that
  declines to publish — a file left behind is one a later `index status` finds
  and believes.

- `knowledge.search` gains a `maxTokens` parameter (FR-R4).
- Searching a project with no index falls back to the previous substring scan
  and says so, rather than returning nothing — which would read as "we have no
  such decision" rather than "ask me again in a moment".
- The substring fallback now honours `maxTokens` as well. FR-R4 is a promise
  about every answer, and this path ignored it: fifty results carrying their
  provenance and trust labels are several thousand tokens handed to a caller who
  asked for five hundred.
- The relevance floor on the lexical retriever was removed, because it was dead
  code. A review reported that BM25 returns "exactly 0.0000" when the only
  matching terms appear in every row, and proposed excluding those hits.
  Measured, SQLite returns `-1.375e-06` for that case — the `0.0000` was a
  printed rounding — so the threshold excluded nothing while claiming to be a
  floor. Separating "matched only common words" from "matched weakly" needs a
  per-term IDF test, which is recorded as an outstanding gap rather than papered
  over.

#### Fixed

- Japanese documents were indexed as a single chunk. Japanese puts no space
  after a full stop, so the sentence pattern matched nothing and the word
  fallback had no spaces to split on either. Found by running it, not by
  reading it.
- `theurian index build` reported "no built knowledge state" on a project that
  had one: `_require_project` returns the state *database* as its second value,
  and the new code treated it as the repository root.
- `search_dense` leaked a raw `sqlite3.OperationalError` — "no such table:
  embeddings" — when an index's `embeddings` table was missing, which defeated
  `hybrid_answer`'s guarantee to never answer from a broken index for
  `useDense=true`. Wrapped in `IndexUnreadableError`, like the other two
  retrievers, and reported as an ordinary fallback.
- A query containing a NUL byte or a lone unpaired surrogate reached the agent
  as a tool failure instead of a search result: SQLite rejects the first as an
  unterminated string, and the Python driver raises `UnicodeEncodeError` on the
  second *before* SQLite is even called, so no `except sqlite3.OperationalError`
  could catch either. Both are now dropped as untransportable terms — the
  treatment punctuation already got — so `auth token\x00` still searches for
  `auth`. Separately, a 20,000,000-character query was accepted and echoed back
  verbatim, a 20 MB response to one search; `knowledge.search` now bounds and
  normalises the query once, at the MCP boundary, before both searching and
  echoing it.
- A corrupt `active-index.json` — truncated, a JSON array, an object with no
  `indexBuildId` — was treated identically to no index ever having been built,
  sending a user who had already run `theurian index build` back through the
  same command a second time. The two are now distinguished
  (`fallbackReason: "index-pointer-invalid"` vs. `"no-index"`).
- A query with more than 64 distinct terms kept the first 64 in the order the
  caller typed them, which for a natural-language question discards the noun
  it was about — "how do we handle the ..." front-loads its least selective
  words. The limit now keeps the 64 *longest* terms, a tokenizer-free proxy for
  selectivity.
- **`theurian index build` made search strictly worse than having no index at
  all, for the most common noun length in Japanese.** A trigram index has no
  gram for a term shorter than three characters, so 認証, 決済, 監査 and 契約 —
  two characters each — returned results before a build and `count: 0,
  indexed: true` after one, with no `fallbackReason` to explain it. An agent
  reads that as "this team has made no such decision". A query whose terms are
  *all* below the floor is now answered by a scoped `LIKE` scan over the same
  rows, under the same project and status filters, and a single character is
  admitted when it is a letter of a script written without word boundaries
  (`鍵` is a noun; `e` is a letter the word index already answers as a word).
  The scan is ranked, by how many characters of the query each chunk accounts
  for — under a `LIMIT` the ordering key is the selection key, so ordering by
  `chunk_id` would have made the *oldest* matches the only reachable ones. A
  lone punctuation character is deliberately declined rather than answered:
  `。` is in every Japanese paragraph, and matching it means reading the whole
  corpus to return "the fifty the sort favoured". (ADR-0023)
- **The published schema disagreed with the product it describes, twice, in the
  same way — found by comparing the schema against the domain's own
  validators, not by testing output.** `knowledge/retrieval-result.schema.json`
  required at least one `sourceAnchors` entry. INV-8 permits a revision to
  carry no source anchor when it declares itself `authored-in-theurian`, so
  every result for knowledge written inside Theurian violated the schema
  Theurian publishes, on both answer paths. No `protocolVersion` bump: no
  response ever carried a different shape, and no schema-validating client
  could have been working against such a document. An integrator who wrote a
  non-empty check from the schema rather than from the product has work to do.
  Separately, `itemId` had no `maxLength` in the schema while the domain has
  rejected one over 200 characters since it was introduced; the schema now
  states the same bound.
- **The disclosure fix below silently disabled the FR-K5 check for six
  commands, and this branch is where that was found.** Recorded even though it
  never shipped, because it is the most instructive thing that happened here:
  `StateDatabaseUnreadableError` descends from `TheurianError`, and
  `_verify_history` swallowed every `TheurianError` raised while reading the
  *previously active* state database — on the correct grounds that a state
  written at another schema version is not evidence about this one (ADR-0017).
  The new error fell into the same `except`.

  So a **tampered applied migration**, which is what FR-K5 and ADR-0005 exist to
  catch, was reported as a clean history with exit 0 where a healthy database
  refuses it with exit 4. The check is reached from `_require_project`, so the
  silence covered `migrate status`, `migrate apply`, `migrate validate`,
  `index build`, `index status` and `ingest`. `SchemaVersionMismatchError` keeps
  the early return, because that is the case the comment describes; a database
  this build cannot read is neither evidence of tampering nor evidence of its
  absence, and now exits 4 naming the check that could not be performed and what
  rebuilding costs — the rebuilt history records the files as they are now, so an
  edit made before that point stops being detectable.
- **A damaged `content_sha256` cell was diagnosed as a rewritten revision.**
  `append_revision` compares the stored hash against the caller's, and two states
  produce the same mismatch: an author rewriting a revision, which is INV-1, and
  a cell that is not a digest at all. The second was answered with `Revisions are
  immutable; write a new revision instead` — a remedy that appends a duplicate
  into a database that is already damaged. The comparison stays a comparison of
  opaque strings; only the question of *why* the two differ is an interpretation,
  and it is now asked only on the branch that has already decided they do.

#### Security

- **`theurian doctor --report` published values Theurian had only read, inside a
  payload that said `redacted: true`** (SEC-6, O-3). Redaction was a substitution
  of the paths the local `SetupContext` holds, which by construction cannot
  reach a string that came from another file, another process, or an exception —
  and five setup steps put exactly such strings into the `detail` a report
  carries. The one that matters is the MCP entry: `mcp-connection` renders the
  installed entry so the user can decide whether the run may proceed around it,
  and an entry configured with a literal `Authorization: Bearer <token>` instead
  of `${THEURIAN_MCP_TOKEN}` is both the state that makes that step conflict and
  the state that gives someone a reason to publish the report. Measured in the
  shipped default configuration, with no flags: the token was in the output.

  Theurian never writes such an entry (SEC-5) — it is what it finds. The same
  route ran through a service unit's `EnvironmentVariables` / `Environment=`
  lines, another daemon's `dataDir` from `/health`, the ids of other
  repositories in the project registry, and the message of any exception a probe
  raised.

  Redaction now has a second half that runs before substitution rather than
  after: `SetupContext.for_publication`, set by the composition root when
  `--report` is passed, makes each of those steps withhold what it did not
  author. What is published is which fields differ, a count of unreadable
  registry entries, `<another data directory>`, and an exception's type. Plain
  `theurian doctor` is unchanged and still prints everything, because it is read
  by the person who has to act on it. Asserted on the values themselves in
  `tests/integration/test_setup_report_withholding.py` — a test that only checked
  the path anchors passed before this fix and after it.

  **A field *name* is a value too, and that took a second pass to see.** The
  first fix published the names of the differing fields on the reasoning that a
  name is schema. It is not, unless Theurian defined it: the names came from a
  union with the installed file, so what got published was whatever string sat in
  key position in somebody else's. A systemd continuation line is the *value* of
  the directive above it, and parsed alone its left-hand side became a directive
  name — a bearer token, published as a field name inside the sentence promising
  the values were withheld. `DifferingFields` now intersects with the names
  Theurian's own renderer produces and counts the rest, which holds without
  depending on a parser being right about a third party's file format.

  **And "the names Theurian's own renderer produces" had to stop being asked of
  the renderer.** The vocabulary was computed by re-parsing `render()`'s output,
  on the argument that a name Theurian writes cannot be a value it read. True of
  `plistlib` and of a dict literal; not true of an f-string over a line-oriented
  format. `SystemdUserManager.render` interpolates the data directory and the
  executable, so a line break in either added a directive of the caller's
  choosing to the "authored" set — and a name present only in the *installed*
  unit was then published. Two faces of one root cause: the write side rendered
  that injected directive into the user's unit file at all three interpolation
  points. The vocabulary is now a stated constant, and a line break in an
  interpolated value is refused rather than escaped, because systemd has no
  escape that makes one part of a value. Not reachable in the shipped default
  configuration — `THEURIAN_DATA_DIR` had to contain a newline.

  **The two halves cannot be used apart.** `_redacted` refuses a payload from a
  context that did not ask for publication, because stamping `redacted: true` on
  a run that did not withhold reproduces the original defect exactly — and
  `tests/integration/test_setup_report_withholding.py` sweeps *every* step in
  `STEPS` with a seeded sentinel rather than testing the routes that were known
  to be broken, after a one-line addition to an unrelated step reopened the class
  with the whole suite green.

  **`SECURITY.md` and `docs/security/local-mcp.md` said this could not happen**
  ("no credential value … enters that payload for it to remove"), which is what
  told a reader the output was safe to paste. Both now describe the two
  mechanisms and what review is still the reader's. `docs/adr/0011`,
  `CONTRIBUTING.md`, `docs/architecture/requirements-analysis.md` and the bug
  report template carried the same claim. The plugin's `/theurian:doctor` command
  said plain `theurian doctor --json` "redacts by default" — it never has, and
  now says so.

- **A corrupted cell in the canonical state database was published to MCP
  callers verbatim** (SEC-13). `SqliteCanonicalStore` handed the bytes it could
  not interpret straight to the tool result: overwriting `created_at`,
  `valid_from`, `content_type` or `status` came back as `Error executing tool
  knowledge.get: Invalid isoformat string: '<the cell>'`, eight of eight across
  `knowledge.get` and `knowledge.search`, against a control on an intact database
  that raised nothing. Swept one cell at a time across the whole schema, the
  damage reached an MCP client from **60 (column, tool) positions** on
  `67a792c`, and `theurian index build` published the same cell as an unhandled
  `ValueError`.

  That store holds *every* revision — `draft` and `rejected` alongside `approved`
  (ADR-0006) — so a cell it fails to interpret carries bytes the caller may not
  read, and the retrieval gate never sees them: an exception raised while a row
  is being interpreted goes around the gate entirely. This was on file as
  [#18](https://github.com/theurian/theurian/issues/18), *a corrupt state
  database reaches the caller with no remedy*, and stood under Known limitations
  below through this milestone. That reading was wrong. The missing remedy was
  the smaller half of it; the defect is an information disclosure.

  Every line that turns a stored cell into a value now runs inside a guard that
  answers with `StateDatabaseUnreadableError` (see Added), whose detail is the
  failing exception's type and never its message. The block is entered by the two
  functions that are the only way this class reads, so a read added later cannot
  forget the convention. **The type name is the whole detail for `sqlite3`'s own
  errors too**, which is a narrower rule than the index store's: damaging one
  `sqlite_master.sql` cell gives `malformed database schema
  (payroll_secret_band_l7) - incomplete input` on SQLite 3.51.2 — a name read
  straight out of the file — so passing `str(exc)` through keeps a case analysis
  over SQLite's error catalogue that a later release can invalidate.

  Two cells travelled as *data* rather than inside an exception message, and both
  are converted on the way out. `migration_history.checksum` was returned as a
  plain string and rendered into `MigrationChecksumMismatchError`, so
  `theurian migrate status --json` answered `Migration 01K1… was applied with
  checksum <the cell> but the file on disk hashes to …`. And INV-3's refusal on a
  tampered body named `content_sha256.short` together with the hash of the stored
  body — a 12-character confirmation oracle over a revision the caller may not be
  entitled to read. The invariant check is unchanged; what it publishes is not.

  **The remedy discards the retrieval index, and does not rebuild it.** It reads
  "delete `.theurian/state/` and run `theurian migrate apply`", and the index
  lives under `.theurian/state/` as well, so following it literally takes the
  index with the canonical state while `migrate apply` restores only the latter.
  Run for real: `migrate apply` reports `databaseCreated: true` and the knowledge
  is back, so nothing authored is lost; `theurian index status` then reports
  `built: false` with a remedy of its own — run `theurian index build` — and
  `knowledge.search` answers from the unranked substring scan with
  `retrieval.fallbackReason: "no-index"`. The degradation announces itself at
  both surfaces, but a project that was ranked stays unranked until `theurian
  index build` runs.

- **`knowledge.get` was not gated on status** (SEC-13). Closing every path
  through `knowledge.search` achieved nothing while this stood open: a caller
  read an approved item, took a `targetItemId` off one of its relations, and
  fetched the withheld body in one further call. No flag, no guessing. A
  rejected revision is where the secret that caused the rejection still lives.
  Both the item and its relations are now limited to surfaceable statuses by the
  same authority search uses.
- The refusal for a withheld item is byte-identical to the one for an item that
  does not exist, so the error cannot be used to confirm that a retired item
  exists at a given id.
- A stale index no longer resurrects retired knowledge or superseded revisions.
  Status and current-revision are both re-checked against the canonical store on
  *both* the default and the `includeUnapproved` path, so a stale index returns
  fewer results rather than wrong ones.
- **The token budget was priced on candidates before the canonical re-check
  withdrew them, which turned `usedTokens` into a truth oracle for content the
  caller had just been refused** (SEC-13, T-15). A retired or superseded
  revision that matched the query still spent its share of the budget before
  being dropped from `results`, so `count: 0, results: [], usedTokens: 46`
  said "something matched and may not be read" — and because the trigram
  retriever matches any substring of three characters or more, that statement
  supports sequential extraction, not just existence detection: guess a
  character, ask, keep it if the number moves. Measured on this code, 257
  ordinary `knowledge.search` calls — no `includeUnapproved`, no privileges —
  recovered a 20-character credential from a document whose superseding
  revision had redacted it. The only precondition was an index older than the
  redaction, which is the normal state between `migrate apply` and `index
  build`: the window opened by *performing* the redaction was the window the
  plaintext could be read back through.

  `retrieval.mode` had the same defect in a different field: it was derived
  from the rankings fusion produced, which still held candidates the canonical
  store went on to withhold, rather than from the results the caller actually
  received. Both are now computed from `results` after resolution — never from
  a candidate that did not survive it. `withheldSuperseded` is removed for the
  same reason (see Changed, above). Verified by comparing a query that matches
  only withheld content against a query that matches nothing at all, field by
  field: no key differs.

- **The bullet above is one face of five, and recording it as one defect with
  five faces is the finding** (SEC-13, T-15, T-17). Four more fields carried the
  same oracle. They are listed together because each fix closed the one in front
  of it and left a sibling: every one of them moved a *quantity* past the
  canonical gate while the gate itself stayed after the ranking.

  | Face | What was computed before the gate |
  | :-- | :-- |
  | `usedTokens` | the token budget, priced on candidates |
  | `count` | `limit`, truncating candidates |
  | `fusedScore` | the RRF ranks |
  | `CANDIDATE_DEPTH` | the rows *fetched* from each retriever |
  | the excerpt | `diversify` choosing which chunk of a document to publish |

  Two of them supported full extraction with no flags and no privileges: 203
  ordinary `knowledge.search` calls recovered a 16-character credential through
  `count`, and 442 recovered one through the candidate depth at the **default**
  token budget with no parameter set. 203 is the figure to plan against, because
  an attacker picks whichever implementation is cheaper. `fusedScore` moved every
  published score by a rank —
  `[0.032787, 0.032258, 0.031746, 0.031250]` became
  `[0.032258, 0.031746, 0.031250, 0.030769]` — and the excerpt moved *which
  paragraph* of a visible document was published, in 9.1% of 20,000 random rank
  arrangements.

  **What closed it was not a sixth patch.** The gate moved inside the ranking:
  `RetrievalService.search` takes a `Visibility` and ranks only rows it has
  cleared, so fusion, diversification, `limit` and the budget all see exactly the
  rows an index that never held the withheld documents would have offered. The
  fix this entry used to describe — re-fusing the survivors after filtering — is
  gone with the function that did it, because ranks are never computed over
  withheld rows now and there is nothing left to repair. Retrievers are read deeper
  instead: 100 rows, then twice as many, until 50 *visible* rows exist or the
  retriever returns fewer rows than it was asked for. On a Japanese corpus this
  mattered most, and needed no setup: `unicode61` cannot segment CJK, so the
  trigram retriever's fifty slots are the whole candidate list.

  Verified by comparing one query against two corpora — one whose index holds a
  document the caller may not read, one that never held it — rather than two
  queries against one corpus, which is what the three earlier rounds did and is
  only ever as wide as the fields those two queries happen to move. Every
  published value is equal, at every `limit` up to 50 and at the default budget,
  with and without `useDense`, **in English and in Japanese**.

  **Both corpora are load-bearing, in opposite directions.** The depth loop is
  read once for the word index and once for the trigram retriever, and removing
  it from one is a different mutation from removing it from the other. Taking it
  off the trigram retriever, English notices only at `maxTokens=32,000` while
  Japanese also notices at `limit=50` at the default budget, through
  `droppedForBudget` — the field the attack used. Taking it off the word index,
  English fails four cases and **Japanese fails none**: the Japanese word index
  returns one row against this crowd, so its loop has nothing to skip and the
  displacement is unobservable. Deleting either corpus leaves one of the two
  loops unguarded with a green suite.

  Timing remains a stated residual, and closing the content channel **widened**
  it before a mitigation narrowed it again: with a first pass of exactly 50, a
  single withheld row forced a second query, and a single call classified the two
  cases correctly 91.6% of the time. Reading 100 rows first moves that threshold
  from "one withheld row matched" to "fifty did" and brings it back to +3.0% /
  63.0%, against 62.1% for a pipeline with no depth loop at all. It is a
  mitigation, not a proof, and what guards it is a count of retriever reads per
  request rather than a clock — no wall-clock assertion runs in CI, so a change
  that made each read more expensive in proportion to what was withheld would go
  unnoticed.

  Those separations are the trigram-lookup branch, and the branch below the
  trigram floor was two orders worse before it was fixed in this same milestone:
  a `LIMIT` bounded nothing there, so every doubling re-scanned the corpus —
  +86% on a plain CJK noun, +101% on the worst legal query, and a six-pass worst
  case of 3.06 s against the 43 ms recorded for the lookup. **That branch now
  scans the corpus once whatever the canonical store withheld** — `scan_statement`
  dropped its `LIMIT` and the loop's exit test became `!=`, so a retriever that
  never truncates is not asked twice; 3.06 s → 0.64 s, and 0.65 s with the whole
  corpus retired.

  **Once *scanned*, not once *called*, and the two were reported as one until
  review round five.** A ranking that totals exactly `FIRST_PASS_DEPTH` rows is
  indistinguishable from a truncated one, so the loop asks again: the scan port
  is called once at 50 withheld rows and twice at 51. What holds that second call
  to no further pass over the corpus is `SqliteIndexStore._scan_cache`, a
  memoisation for this one gap — deleted when `IndexStore` states its own
  exhaustion, filed as [#16](https://github.com/theurian/theurian/issues/16).
  The earlier "one pass from 0 to 5,999 withheld rows" is not wrong, only
  narrower than the sentence it supported: a 6,000-row ranking never lands on the
  coincidence.

  The trigram lookup keeps the loop outright: 1 pass at 50 withheld rows and 2 at
  51, costing +12.8 ms (+15% of a request), down from +64.3 ms. **What is left on
  both branches is closed by an argument rather than a further mitigation**, and
  it is the duration face of the BM25 entry below rather than a finding of its
  own: a ranking the visibility has not yet judged still contains the withheld
  rows, so any work proportional to its length moves with how many there are.
  That is the extra fetch needed to secure `CANDIDATE_DEPTH` visible rows from a
  retriever that is not exhausted, and — with the pass count held at one — the
  canonical read `CanonicalVisibility.cleared` makes for every row of the
  ranking. Both follow from the definition of the loop and not from a defect in
  it. No exhaustion signal removes them and no cache removes them; only an index
  that no longer holds withdrawn rows does
  ([#15](https://github.com/theurian/theurian/issues/15), Milestone 6). See T-17
  in the threat model for the measurements, the five things that would falsify
  that argument, and their evidence grade.

  **The canonical-read half was found in review round six, and two claims are
  retracted with it.** T-17 said this branch's timing channel was "closed
  outright" and that walking the whole ranking is what keeps the canonical read
  count off the withheld count. Dropping the `LIMIT` closed the *pass count*; the
  read count is `len(ranked)`, so 10 visible rows cost 10 canonical reads with
  nothing withheld and 210 with 200 withheld, in one pass, at about 15 µs each and
  bounded by nothing on a branch whose statement has no `LIMIT`. Round four
  replaced a bounded 6× multiplier with an unbounded linear term rather than
  removing the channel, and the published residual of +0.35 ms / 63.0% is the
  lookup's pass-count edge rather than a bound over T-17 as a whole.

#### Known limitations

- The default embedder is lexical in vector form, and off by default for the
  reason given above. Semantic retrieval needs a real model, which plugs in
  through the `EmbeddingProvider` port without touching anything else
  (ADR-0003, ADR-0009). Because it stays opt-in, FR-R2 is only partly
  discharged: the fusion is real and both retrievers exist, but a healthy
  default search never runs the dense one.
- The relevance floor removed above (Changed) leaves a query whose terms all
  appear in every document still ranking. Separating "matched weakly" from
  "matched only common words" needs a per-term IDF test, not a score
  threshold (ADR-0021, Milestone 6).
- Two candidates ranked `(i, j)` and `(j, i)` by two retrievers score exactly
  equal under Reciprocal Rank Fusion, so the `chunk_id` tie-break — revision
  creation order — decides between them instead of relevance. Measured at
  9%–16% of adjacent top-10 pairs, depending on corpus, over a 30-document,
  15-query test corpus. A relevance-based tie-break needs a per-retriever
  weighting decision (ADR-0021, Milestone 6).
- A query mixing a short term (one or two characters) with a longer one (three
  or more) still drops the short term from the trigram retriever entirely,
  because the trigram expression is then non-empty and the floor that rescues
  an all-short query never fires. `認証 トークン` searches only for `トークン` on
  this retriever (ADR-0023, Milestone 6).
- The scan below the trigram floor orders by occurrences weighted by term
  length, which is a proxy for relevance and not IDF: a chunk that repeats one
  term many times can outrank a chunk that covers two. The same per-term IDF
  work closes this and the mixed-length residual above (ADR-0023, Milestone 6).
- **A query with more than eight terms below the trigram floor searches only its
  first eight**, and *first* means first typed. Both the match and the order come
  from that one slice, so a term past it is honestly absent rather than present
  but unrankable. Eight is a cost bound: each term is a `LIKE` and an occurrence
  count over every row, so the worst legal query runs 0.81s at four terms, 1.67s
  at eight and 4.25s unbounded, on 20,000 chunks — the multi-second, GIL-holding
  query SEC-8 exists to keep out of a daemon shared by every project. The
  longest-first ordering that decides *which* terms survive elsewhere is a no-op
  here, because every term on this path is one or two characters; picking the
  selective one out of `認証 決済 監査 契約` needs corpus statistics this
  retriever does not have (ADR-0023, Milestone 6).
- Case matching is asymmetric across the trigram floor: SQLite's `LIKE` folds
  ASCII only while the trigram tokenizer folds all of Unicode, so a two-letter
  Greek query is case-sensitive and the same word with one letter more is not.
  Japanese and Chinese are caseless, so the scripts the floor exists for are
  unaffected; a Greek or Cyrillic corpus would need the `icu` tokenizer
  (ADR-0023).
- `mode: "substring"` names two different things on the wire: the unranked
  canonical scan reported at the top level of `retrieval`, and the trigram
  retriever named inside a ranked hit's `foundBy`. Left as one published value
  rather than renamed, and documented at both call sites in `mcp/search.py`.
- A hit's `foundBy` names which retrievers ranked it, not at which position each
  did. The positions exist on the fused candidate and are what `fusedScore` is
  computed from; publishing them would be a schema change (ADR-0021).
- **On a stale index, BM25's collection statistics count documents the canonical
  store will withhold, and the visible order moves with them.** This entry twice
  called part of the effect harmless, and both bounds were false. It first said
  the statistics "do not vary with what a query matched"; FTS5's `bm25` weights
  each phrase by `idf = log((N - nHit + 0.5) / (nHit + 0.5))`, where `nHit`
  counts the rows matching *that phrase* — so a withheld row containing one of
  the query's terms reweights the **visible** rows against each other. It then
  said the remaining statistics were harmless because query-independent. They are
  query-independent, and that is not the same claim.

  Two channels, and they differ in what a caller can do with them:

  - **`idf`, via `nHit`** — query-dependent, so a probe steers it, and bounded by
    `tf`: a term that does not also occur in visible content leaves every visible
    row at `tf = 0` and reads back nothing. It is an oracle for whether a
    withheld document contains a term already in the visible vocabulary, not the
    character-at-a-time extraction T-17 describes — still disclosure on a corpus
    of incident notes or rejected-review rationales.
  - **`avgdl`, and more weakly `N`** — query-independent, so no probe can make
    them answer a question, but **unconditional**: no shared vocabulary is
    needed. BM25's length norm `k1 * (1 - b + b * D / avgdl)` is a function of
    each row's own `D`, so it is not a common factor across rows and moving
    `avgdl` does not preserve an order. Measured on withheld rows sharing no term
    with the query, with each phrase's `nHit` asserted identical in both indexes:
    1,218 configurations reorder two visible rows.

  So the published `fusedScore`, the hit order and — because `knowledge.search`
  always asks for one chunk per document — which paragraph is returned as
  `excerpt` can all move for *any* withheld content while the index is stale,
  whatever that content says. The gate removes rows from the result; it does not
  remove them from the statistics the survivors are scored against. What an
  attacker can read back out is the `idf` channel and nothing more.

  **Accepted for this milestone rather than deferred without a decision**, and
  re-taken in review round five against the corrected text rather than carried
  forward on the old one. The argument is in T-17a: purging withheld chunks from
  the derived index on read means a read path writing to a derived artifact, and
  Milestone 6 settles blue/green index builds, so building it now means building
  it twice. The window is the stale window and the root fix is eliminating it,
  not correcting statistics inside it. `theurian index build` closes it today.
  Tracked at HIGH against Milestone 6 as
  [#15](https://github.com/theurian/theurian/issues/15), disclosed in
  `SECURITY.md` and the README. Both channels are pinned by tests that assert the
  leak is *present*, so its scope cannot grow unnoticed and closing the window in
  Milestone 6 turns them red — `test_a_withheld_document_can_still_reorder_the_visible_ones`
  for the `idf` channel, and
  `test_a_withheld_document_sharing_no_vocabulary_still_reorders_the_visible_ones`
  for the `avgdl` one.
- A search running while `theurian index build` publishes is not protected. The
  new build reaps the old file immediately, and the retrieval store holds no
  open handle between queries, so such a search falls back to the substring scan.
  Blue/green index builds in Milestone 6 are what fix this properly; ADR-0022's
  original promise that the previous build survives has been withdrawn rather
  than delivered.
- **`knowledge.status` is not covered by the equality above.** The withheld half
  of a project cannot be read out of `itemCount` or `itemsByStatus`, which
  exclude `deprecated`, `superseded` and `rejected` and report a total that is
  the sum of what they do report. Two other values move: `stateHash`, which
  covers the whole working tree by design (ADR-0016) and is query-independent —
  the same property that makes `snapshotId` safe to publish — and
  `appliedMigrations`, which counts migration *files*, so it increments for a
  migration that creates only withheld items. Measured on two projects differing
  by exactly one rejected item: `appliedMigrations` 1 against 2. It names no
  status, id or body, no request parameter reaches it, and `stateHash` already
  distinguishes anything it does. Accepted for this milestone with the argument
  in T-17 and filed against Milestone 6 as
  [#19](https://github.com/theurian/theurian/issues/19), because every remedy is
  a change to a published tool that has no response schema to change with it.
- **An unregistered `projectId` is echoed back into the error unbounded.** Two
  million characters in produce a two-million-character message, against 2,000
  for an over-long `query` (clamped by `MAX_QUERY_CHARS`) and 185 for an
  over-long `itemId` (which reports its length instead of itself). Nothing is
  disclosed — the caller receives bytes it sent — but the reader of the error
  pays for them. Recorded under T-6 and filed as
  [#17](https://github.com/theurian/theurian/issues/17); the bound is trivial and
  where it goes decides which of three tools change their error text.
- **A damaged cell can make `knowledge.search` and `knowledge.status` answer
  *successfully* with less than the database holds.** The byte-interpretation
  guard under Security does not cover this and cannot: the corrupted column is
  part of the item → revision pointer chain, so the lookup misses and **no value
  is ever converted**, while that guard's whole key is whether a line interprets
  bytes that came out of the file. A caller is told `count: 0` with
  `stale: false`, or `itemCount: 0`, and has no way to tell either from a project
  that genuinely holds nothing — `knowledge.status` contradicts itself inside one
  body, reporting one applied migration beside zero items. Four positions, held
  as an exact set rather than as an allowance, so the reach cannot grow in
  silence and each entry disappears the moment its surface starts refusing
  instead:

  | Tool | Table | Column |
  | :-- | :-- | :-- |
  | `knowledge.search` | `knowledge_items` | `item_id` |
  | `knowledge.search` | `knowledge_items` | `project_id` |
  | `knowledge.status` | `knowledge_items` | `project_id` |
  | `knowledge.status` | `migration_history` | `project_id` |

  The same cause has a second face, and it is the same defect rather than a
  second one: `knowledge.get`'s two id-resolution refusals — `'<itemId>' is not
  present in project '<projectId>'.` and `'<itemId>' points at a missing
  revision.` — **report damage as absence**, at four (tool, table, column)
  positions of their own. A caller is told the item does not exist when its row
  is in fact unreadable. Both faces fire before any cell is interpreted, so
  neither message contains anything but the caller's own arguments: this is
  wrong knowledge, not disclosure. Milestone 6, tracked as
  [#30](https://github.com/theurian/theurian/issues/30); closing it is a change
  to the retrieval gate, `knowledge.status` and `knowledge.get`, not to the
  store the fix above changed.
- **`knowledge.get` and `knowledge.status` publish no response schema.**
  `schemas/mcp/` describes `knowledge.search`, `project.list` and the tool
  context; two of the five tools have nothing a client in another language can
  validate against, and nowhere for the decisions above to land.
  [#20](https://github.com/theurian/theurian/issues/20), which also collects the
  review rounds' remaining LOW findings — three docstrings that overstate what
  the code beside them does, a stale line-number citation, a mutable module-level
  `SAFETY` dict, and a relation gate that publishes an id `knowledge.get` refuses.
- Scope filtering is not implemented. `sensitivity`, `trust_level`, and
  `namespace` are carried on every chunk and read by no query; `namespace` is not
  even populated. Milestone 6.
- RAPTOR summary nodes (FR-R3) and reranking arrive in Milestone 6.

---

### Fixed after Milestone 4

- **`theurian doctor --report` did not redact the repository on any machine
  where the checkout lives inside the home directory** — which is most of them
  (O-3). `_redacted` substitutes plain substrings and replaced `$HOME` with `~`
  first, so by the time the `<repository>` substitution ran its needle was no
  longer in the string. Same command, two layouts:

  ```
  repository beside HOME   <repository>/.theurian is missing migrations, knowledge, state.
  repository under HOME    /private~/work/api/.theurian is missing migrations, knowledge, state.
  ```

  The second publishes the checkout's path relative to home into output meant
  for a public issue, and the `/private~` is a second fault arriving with the
  first: `context.home` is whatever `$HOME` says while the repository root is
  `Path.cwd().resolve()`, so on an account whose home is a symlink the
  unresolved anchor matched *inside* the resolved path.

  Anchors are now built in both spellings and applied longest first. The rest of
  the class goes with it, since the root cause is naive substitution over
  incomplete anchors rather than one ordering:

  - **Only the default data directory is still legible as `~/.theurian`.** The
    exemption used to cover every path under `$HOME`, on an argument about the
    default alone, so `THEURIAN_DATA_DIR=$HOME/clients/<name>/store` was
    published in full — `~` is anonymous, and the directory it sits in is what
    identifies someone. Anything the operator chose is now `<data directory>`
    wherever it points. This is also what stopped `~/work/api/.theurian-data`
    from disclosing the checkout's path relative to home when `$HOME` is a
    symlink and the data directory sits inside the repository.
  - **The executable is redacted to `<executable>`**, since it is routinely a
    virtualenv under a project directory. The install location is given up
    deliberately; `platform` and `version` are still published.
  - **The setup steps stop naming the repository by its bare directory name**,
    which no path anchor can catch without corrupting unrelated prose.

- **`theurian setup` reported files as changed that it never touched, and
  journalled them as applied.** Three steps — `project-registered`,
  `project-layout` and `gitignore` — report what `theurian project register` and
  `theurian init` would do, and setup performs neither. Their probes reported
  `missing`, the runner recorded them `changed`, and five paths landed in
  `changedPaths` with an `applied` line in the setup journal apiece. All five
  were absent from the disk when the run ended, and a second run named the same
  five having written nothing — so the report did not describe the idempotence
  setup actually has (FR-L2). `setup --dry-run --json` offered the same five
  under `steps[].paths`, which is what the user is shown before consenting.

  **Published JSON changes** for those three steps: `outcome` is now `unchanged`
  rather than `changed`, `paths` is now `[]`, and they no longer contribute to
  `changedPaths`. What they report does not shrink — the `missing` status stays,
  `action` still names the command that fixes it, and the run still ends
  `degraded` with a warning for each. The two locations that only `paths` had
  been carrying moved into `summary`: `project-registered` names the registry
  file, `gitignore` names the `.gitignore` it checked.

  The rule is now the runner's rather than each probe's — a step declared with no
  action has its `paths` dropped centrally, the same way criticality is already
  taken from the step definition instead of from the probe. Two mutations had
  restored the defect from a single probe arm while the whole suite stayed green.

- **`theurian auth rotate` did not exist**, while three user-facing messages told
  people to run it — including the one shown when a token is found readable by
  other users. A remedy that errors out is worse than no remedy, because it is
  shown at the moment a credential has already been exposed. Rotation now
  replaces the token, rewrites the env file that names its location, and
  restarts the daemon: the daemon reads its token once at startup, so writing a
  new file alone leaves every client getting a 401 with no visible cause.
- **`theurian daemon stop`** now exists. Milestone 3 omitted it deliberately —
  the service manager owns the lifecycle and a PID-based stop would contradict
  the reason this design uses an advisory lock. Milestone 4 made `daemon start`
  delegate to that manager, which left the absence of `stop` an arbitrary
  asymmetry rather than a principled one. It asks the service manager, and
  refuses rather than guessing when none is registered.

### Milestone 4 — setup, service adapters, and doctor

#### Added

- **`theurian setup`**, an idempotent plan-then-apply state machine over the
  eighteen steps of the specification. It probes everything, shows what it would
  do, applies only that, and then probes everything again — so the report states
  what *is*, not what the apply functions believe they did. Running it twice
  changes nothing (FR-L1, FR-L2).
- **`--dry-run`** is the same code path with the apply skipped, so what the user
  is shown cannot drift from what runs.
- **User-scoped service adapters** for macOS LaunchAgent and Linux systemd user
  units. Never a LaunchDaemon and never a system unit: those need administrator
  rights and would run Theurian as root or a service account to read one user's
  home directory.
- **`theurian doctor`**, read-only by design, and **`doctor --report`**, which
  redacts the home directory, the token path, and repository paths by default
  because its output is what people paste into public issues (O-3).
- **`theurian uninstall`**, which removes the OS service and the MCP entry
  independently and never touches approved knowledge (FR-L5).
- **MCP connection installation** into Claude Code at user scope, carrying
  `${THEURIAN_MCP_TOKEN}` rather than a literal token (SEC-5).

#### Changed

- Theurian **reads** `~/.claude.json` and delegates every **write** to
  `claude mcp add` / `claude mcp remove`. That file is Claude Code's live state,
  not a configuration file Theurian has any business reformatting, and Claude
  Code may be writing to it concurrently. See the amendment to ADR-0012.
- `theurian daemon start` without `--foreground` now asks the service manager
  to start the daemon rather than refusing. Theurian never daemonises itself:
  launchd and systemd already do supervision, restart-on-failure, and log
  redirection, and a hand-rolled double-fork would be a second, worse
  implementation of all three. Starting an *unregistered* service is refused
  rather than improvised — a hook may resume a service the user approved, but it
  must never be the thing that installs one (FR-L3).
- `theurian daemon status` now distinguishes `not-installed` from
  `installed-stopped` by asking the service manager. The SessionStart hook
  branches on exactly this: one means a user-approved service may be resumed,
  the other means send the user to `/theurian:setup` and install nothing.

#### Fixed

- Setup reported `degraded` on almost every successful install. The verification
  pass re-probed the daemon's health microseconds after the start command
  returned, long before it had bound its port. Starting a service and having it
  answer are separate events, and setup now waits for the second one.

#### Known limitations

- Artifact integrity verification is reported as *not applicable* rather than
  satisfied: setup never obtains Core, so it holds no artifact to hash, and a
  step claiming success without checking anything would be a false assurance
  about supply chain integrity (T-16).
- Rollback is a journal, not an undo. Every apply is a create-or-tighten, so a
  critical failure stops and reports where it stopped rather than deleting a
  token another session may already be using.

---

### Milestone 3 — the single MCP daemon

#### Added

- **One daemon per user per machine**, serving MCP over Streamable HTTP at
  `http://127.0.0.1:7419/mcp`. Ten subagents cost one process, one writer, and
  one warm index rather than ten of each (ADR-0002).
- **Single-instance enforcement** through three independent mechanisms, because
  each alone has a known failure mode: an advisory `flock`, a port probe, and a
  startup handshake that reports version and data directory. A losing starter
  exits 0 after confirming the winner is healthy; it never kills the winner and
  never repairs data. A daemon serving a *different* data directory is a
  conflict, not something to reuse — reusing it would answer every query from
  the wrong knowledge base.
- **Local authentication.** A 256-bit token in a 0600 file inside a 0700
  directory, compared in constant time. `/health` stays unauthenticated so the
  SessionStart hook and the instance probe need no credential (ADR-0011).
- **Five read-only MCP tools**: `knowledge.search`, `knowledge.get`,
  `knowledge.status`, `project.list`, and `system.capabilities`. No write-intent
  tool exists at all — not behind a flag, not behind a permission (ADR-0013).
- **Explicit project context.** Every project-scoped tool requires `projectId`.
  There is no "last used project", because with many agents sharing one daemon
  an implicit default resolves one agent's query against another's project.
- **Trust labelling on every result**: `contentClassification:
  untrusted-knowledge`, `mayContainInstructions: true`, `executable: false`,
  plus source anchors and freshness. Knowledge bodies contain imperative
  sentences because they describe rules; the labels say so explicitly (SEC-15).
- **`theurian daemon start --foreground` and `theurian daemon status`**, both
  with `--json`.

#### Security

- The daemon refuses to bind anything but loopback. A networked deployment needs
  TLS, OAuth 2.1, audience validation, and tenant isolation; shipping half of
  them would be worse than shipping none (SEC-1).
- Origin and Host validation, so a page the user visits cannot reach the MCP
  endpoint by resolving a hostname to 127.0.0.1 (SEC-2, T-2).
- Access logging is off. Every request carries an `Authorization` header, and an
  access log is the easiest place for one to escape its 0600 file (SEC-6).
- A token file readable by other users is refused rather than quietly repaired:
  a credential others could already read is not a credential (SEC-4).
- An unregistered `projectId` returns an error naming what *is* registered,
  never another project's knowledge (SEC-13).

#### Known limitations

- `theurian daemon start` supports `--foreground` only. Detaching belongs to the
  user's service manager — a LaunchAgent or a systemd unit — which arrives with
  `/theurian:setup` in Milestone 4. There is deliberately no `daemon stop`:
  the lifecycle owner is the service manager, and a PID-based stop would
  contradict the reason the design uses an advisory lock rather than a PID file.
- `knowledge.search` matches substrings. Ranked hybrid retrieval arrives in
  Milestone 5; the *result shape* is already the published one, so callers
  written now keep working.

---

### Milestone 2 — source ingestion

#### Added

- **Parsers** for Markdown, YAML, JSON, OpenAPI, AsyncAPI, and JSON Schema.
  Structured sources keep their structure: an OpenAPI document yields an index
  of operations, parameters, responses, and schemas, which is what specification
  coverage will read in Milestone 8.
- **Deterministic text projection** so lexical search can reach structured
  content. Renders `outcomes.failure.code: CANCELLATION_NOT_ALLOWED` rather than
  a bare value dump, and is byte-identical across processes and machines.
- **Front matter handling**: parsed, preserved as searchable data, and never
  permitted to govern. A `status: approved` in front matter is ignored *and
  reported*, because a silent ignore is exactly the case where an author
  believes something is approved and it is not.
- **Media-type detection** that prefers content over extension. An OpenAPI
  document is conventionally `openapi.yaml`, and treating it as plain YAML would
  discard the operation index — a loss that would only surface milestones later
  as a query returning nothing.
- **`theurian ingest`**, with per-document failure isolation and an incremental
  path: an unchanged file costs one hash, not a reparse.
- External `$ref` targets are recorded, never fetched (SEC-10, T-7).

#### Fixed

- Markdown files with front matter were reparsed on every run. The manifest
  stored the *body* hash while the early exit compared the *source* hash, and
  those differ for exactly such a file. The two are now distinct fields with
  distinct purposes.
- OpenAPI documents serialised as JSON were not detected, because the sniff
  assumed a YAML line start and `{"swagger": ...}` never matched. They fell
  through to the generic JSON parser and silently lost their operation index.
- The blank line after a front matter block stayed in the body, so identical
  prose hashed differently depending on whether the file carried front matter.

---

### Milestone 1 — local canonical store

#### Added

- **Knowledge migration engine.** Applies the fourteen YAML operations
  transactionally, with `expectedRevision` optimistic concurrency, deterministic
  topological ordering, cycle detection that names the actual cycle, and
  idempotent re-application.
- **SQLite canonical store.** WAL, `foreign_keys=ON`, immutable revisions with no
  update path, alias resolution, bidirectional relation traversal, and a
  migration history that records checksums.
- **State hashing.** Content-addresses a whole canonical state so a database
  file's name describes its contents. Covered by a committed golden vector and a
  cross-process test that would catch a `PYTHONHASHSEED` dependency.
- **Single-writer guarantee.** An OS advisory lock serialises concurrent writers,
  behind an interface a daemon-owned queue can replace without touching
  application code.
- **CLI**: `theurian init`, `project register|unregister|list|status`, and
  `migrate status|validate|apply`, all with `--json`.
- **Deterministic fakes** for `Clock`, `IdGenerator`, and the migration writer.

#### Fixed

- The published JSON Schemas were not packaged into the wheel, so an installed
  `theurian` could not validate a migration at all. A build hook now ships them
  and an e2e test asserts an installed build can read a migration.
- Editing an already-applied migration was not detected. Editing one changes the
  state hash, which routed the next command to a fresh empty database where
  nothing looked wrong; the check now runs against the previously active state
  as well. See the amendment to ADR-0016.
- Re-registering a project reported a change every time, because the
  registration timestamp was refreshed. It now records when the project was
  *first* registered, restoring the idempotence FR-L2 requires.
- A read connection leaked when verifying migration history.

#### Security

- `contentFile` paths are resolved with symlinks followed before the containment
  check, so both `../` traversal and symlink escape are refused (SEC-7, T-4, T-5).
- YAML loading no longer coerces timestamps to `datetime`, which had made valid
  migrations fail their own schema validation.

---

### Milestone 0 — architecture and OSS foundation

### Added

- **Domain model.** Immutable `KnowledgeRevision` under a mutable
  `KnowledgeItem` pointer; typed relations, aliases, source anchors, and
  evidence; specifications with their structured form preserved; traceability
  edges and per-change-type policy; review events, threads, comments,
  resolutions, promotion gates, and knowledge candidates.
- **Ten enforced invariants**, including content-hash verification, immutable
  revisions, half-open validity windows, and mandatory source attribution.
- **Fourteen ports** as `Protocol`s, including `Clock` and `IdGenerator` — both
  ports because time and identifiers are inputs to the state hash, and without
  controlling them the reproducibility guarantee is not assertable.
- **Scope isolation primitives.** A RAPTOR tree's identity is
  `(project, tenant, sensitivity, acl_group, namespace)`, so a summary spanning
  two sensitivity levels has no tree to belong to.
- **Path containment and input limits.** `realpath` resolution before
  containment checks, symlink-escape rejection on intermediate components as
  well as final targets, size and depth caps, and permission checks for
  credential files.
- **Compatibility resolution**, including SemVer §11 ordering and a PEP 440 →
  SemVer translation so Core's own development versions resolve correctly.
- **CLI**: `theurian version --json` and `theurian compat check`, with exit code
  3 reserved for a compatibility mismatch.
- **Public JSON Schemas** for the migration format, MCP tool context, retrieval
  results, project configuration, the CLI version contract, and plugin
  compatibility metadata.
- **275 tests** covering domain invariants, layering, scope isolation, path
  security, schemas, the plugin boundary, and the CLI contract. All run offline.

### Security

- Retrieval results are structurally prevented from being marked executable.
- Every result requires at least one source anchor.
- Migration `contentFile` paths are rejected at both schema and runtime level if
  they escape the project root.

[Unreleased]: https://github.com/theurian/theurian/compare/core-v0.1.0.dev2...main
[0.1.0.dev2]: https://github.com/theurian/theurian/compare/core-v0.1.0.dev1...core-v0.1.0.dev2
[0.1.0.dev1]: https://github.com/theurian/theurian/compare/core-v0.1.0.dev0...core-v0.1.0.dev1
[0.1.0.dev0]: https://github.com/theurian/theurian/releases/tag/core-v0.1.0.dev0
