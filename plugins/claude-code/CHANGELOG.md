# Changelog — Theurian Claude Code plugin

All notable changes to the **plugin** are documented here. Core has its own
changelog at [`packages/theurian-core/CHANGELOG.md`](../../packages/theurian-core/CHANGELOG.md);
the two version independently (ADR-0001).

Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versioning: [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Plugin manifest, deliberately without an `mcpServers` entry so that installing
  the plugin has no observable effect (ADR-0012).
- Twelve commands: `setup`, `status`, `doctor`, `register-project`,
  `unregister-project`, `index`, `reindex`, `migrate`, `ingest`, `propose`,
  `upgrade`, `uninstall`.
- `SessionStart` hook performing a bounded health check only, with a hard
  timeout and unconditional exit 0.
- `compatibility.yaml` declaring the supported Core range and protocol version.
- `mcp/theurian.mcp.json` connection template, installed by `/theurian:setup`,
  carrying an environment-variable reference rather than a literal token.
- Shell helpers that shell out to the `theurian` CLI and contain no Theurian
  logic.
- `/theurian:propose` documents `theurian propose --local` and tells the agent
  not to reach for it: a proposal is review input, so the committable location
  is the default, and `--local` is for a user who asks for a draft that stays on
  their machine ([#265](https://github.com/theurian/theurian/issues/265),
  ADR-0028). Two things it says when the user does ask, because both are ways a private
  body reaches Git anyway: the scratch body file still has to be deleted, since
  the `Write` grant reaches only the *committable* `.theurian/proposals/`; and
  `--local` hides the proposal rather than the knowledge, so accepting it still
  writes the body into `.theurian/knowledge/`.

### Fixed

- **`/theurian:ingest` told the agent nothing reads `.theurian/config.yaml`.**
  The allowlist paragraph said a repository will have to be listed in that file
  before Theurian contacts it, then "nothing reads that file today, so do not
  tell the user the allowlist is protecting them". The file has been read since
  ADR-0027 decision 3 shipped `security/project_config.py`, which takes
  `security.secretScan` from it. The warning it supports is still correct, so it
  is re-derived on the fact that is true rather than dropped: the paragraph now
  names the one reader and the one key, then narrows the negation to
  `providers.review.repositories`, which nothing reads — so the allowlist
  protects nobody and the agent must still not say otherwise
  ([#461](https://github.com/theurian/theurian/issues/461),
  [#501](https://github.com/theurian/theurian/pull/501)).

  Naming `security.secretScan` as in force left a second gap in the same
  paragraph: a scanning control announced, in a document about `theurian
  ingest`, with nothing saying where it runs. The paragraph now carries the
  bound — the scan covers the approval gate only, and `theurian ingest` and
  index building run no scan — worded verbatim from the schema's own
  `security.secretScan` description (SEC-11,
  [#198](https://github.com/theurian/theurian/issues/198)).

  **What holds the two surfaces together is four pins, and the reach is the
  bullet rather than the document.** Both surfaces are pinned *whole* — the
  schema's `security.secretScan` description by an exact match, and this
  paragraph's list item by another — so a reword, a deletion and a sentence
  *added* beside the bound are each RED; a fragment pin catches the first two
  and not the third. The shared clause is then derived from the published schema
  and matched byte for byte in the document, so neither side can move alone. The
  fourth arrived in round three, when every published description was pinned
  whole instead of three by fragment, which puts `security.secretScan` in that
  table's parametrised row as well — so the entry says four where it used to say
  three. Re-measured on this entry's own tree (2026-09-03), each plant applied on
  its own to a throwaway checkout and reverted before the next. The suite the
  counts below are out of is one command, so the totals are derivable rather than
  quoted:

  ```console
  $ uv run --frozen pytest -q packages/theurian-core/tests/unit/test_config_key_call_sites.py
  57 passed
  ```

  | The plant | What goes RED |
  | :-- | :-- |
  | *"Ingested content is screened on the way in as well."* appended to the schema description | `test_the_secret_scan_description_is_exactly_what_this_file_records`, and `WATCHED_KEY_DESCRIPTIONS`' parametrised whole-description row for `security.secretScan` — 2 failed, 55 passed |
  | the same sentence appended to this bullet | `test_the_ingest_command_states_the_config_bound_and_nothing_beside_it` — 1 failed, 56 passed |
  | the shared clause reworded on the schema side alone | those two, and `test_the_scan_bound_is_byte_identical_where_two_surfaces_publish_it` with them — 3 failed, 54 passed |

  **The bullet pin ends the item where CommonMark ends one, and rounds three and
  four are why.** It had read a list item as "the `- ` line plus the lines
  indented by two spaces", and four ways of appending a sentence render inside
  the same bullet while sitting outside that rule — so the contradiction above
  went in four times over with every pin green. Round four rendered twelve line
  shapes with `markdown_it`'s CommonMark preset, asked per shape whether the
  added sentence lands in the same `<li>` as the bullet's own text, and asked the
  pin the same question. Each shape the renderer puts **inside** the item now
  reddens `test_the_ingest_command_states_the_config_bound_and_nothing_beside_it`
  alone:

  | The contradiction appended to the pinned bullet | Renders | Held |
  | :-- | :-- | :-- |
  | at column 0, as a lazy continuation | inside | yes |
  | indented by one space | inside | yes |
  | indented by a tab | inside | yes |
  | as a second paragraph after a blank line | inside | yes |
  | after a line holding one no-break space, at column 0 | inside | yes |
  | after a line holding one no-break space, indented | inside | yes |
  | after a line holding one em space, at column 0 | inside | yes |
  | as a `\| … \|` line | inside | yes |
  | as an ordered item, `2. ` | outside | n/a |
  | as an ordered item, `10. ` | outside | n/a |
  | as a paren-marked item, `1) ` | outside | n/a |
  | **as its own `- ` bullet of the same document** | **outside** | **no** |

  Three of those eight were round four's, and two of the three are the same
  defect: `str.strip()` calls a no-break space and an em space blank, CommonMark
  calls neither blank, and the sentence one line below such a line rendered
  inside the bullet while the pin had already ended the item. The third is the
  pipe row — CommonMark has no tables, so `|` opens nothing and the transcribed
  rule that said it did left a whole line shape unreachable. The `1) ` row failed
  in the other direction before this pass: the rule folded it into the bullet and
  reddened the pin for a document nobody had changed.

  **What is not held is the last row.** A sibling bullet opens a block, so the
  item ends there: the same contradiction written into a *different* bullet of
  this document ships with the pins green and the five audits at exit 0, measured
  the same way. The pins hold the paragraph that carries the claim, not the file
  around it, and a whole-document pin is
  [#512](https://github.com/theurian/theurian/issues/512)'s. The twelve shapes are
  a table in `test_config_key_call_sites.py` that fails in both directions, and
  the whitespace population it rests on is derived from `str.isspace` at run time
  rather than transcribed.
- **`/theurian:propose` told the agent the secret scan reads the body only.** It
  said to keep credentials out of `--title`, `--description`, `--label` and the
  `--source-*` anchors because "those are not scanned". Core now scans the
  migration document's author-written fields as well as the bodies, so a
  credential in any of them refuses the acceptance under the default `block`
  ([#336](https://github.com/theurian/theurian/issues/336)). The advice to keep
  them out stands and now says why — the detector is best effort, and the title
  and the published source anchors appear on every search result.
- **`/theurian:propose` described the accept contract Core no longer has.** It
  told the agent that `theurian propose accept` "moves files and nothing else:
  it does not validate, does not apply, and does not approve", that
  `migrate validate` is "the first point at which anything is checked", and that
  a revision's source anchor and a reused revision id "are checked in step 5,
  after the pull request has already merged". All three became false with
  [#307](https://github.com/theurian/theurian/issues/307): `accept` now scans
  every body for secrets and replays the landed set with the proposal in it
  before it moves anything, and a refusal consumes nothing (ADR-0027). The
  document says that, says `migrate validate` is now the *weaker* check of the
  two rather than the stronger one, and adds what to do with exit 1 against exit
  4 — the second means the proposal may not be at fault, so re-drafting mints a
  duplicate. Its digest bullet also stops implying the body pin is something an
  author may leave out; `contentSha256` is schema-required
  ([#210](https://github.com/theurian/theurian/issues/210)). Core's own commands
  moved in the same CL; this document was outside that sweep.
- `/theurian:setup` no longer presents itself as the way Theurian Core gets onto
  the machine. Its `description` — visible in Claude Code's command list — said
  "Install and configure Theurian"; the document opened by calling itself the
  only command that installs software; and it now begins by checking
  `command -v theurian` and naming
  `uv tool install --python 3.13 'theurian[daemon]'` or
  `pipx install --python 3.13 'theurian[daemon]'`. Setup runs *from* an
  installed Core and cannot be what creates it.
- The `SessionStart` hook told a user with no Core to run `/theurian:setup`,
  which shells out to the `theurian` binary whose absence produced the warning.
  It now names the installer first.
- The plugin README's install sequence began at the marketplace and ended at
  `/theurian:setup`, never mentioning Core. Core is now the first step.
- Step 6 no longer tells the agent that a step reporting `missing` with an
  `action` is one setup skips. All seven steps setup performs report exactly
  that before they run, so an agent following the old rule would have asked the
  user to go and "Create ~/.theurian with mode 0700" themselves.
- The `/theurian:setup` command doc now lists `halted` instead of `rolled-back`
  among the states where the verification pass never ran. Core renamed that
  terminal failure state — a critical step failing during apply halts the run
  rather than reporting a rollback that never existed
  ([#47](https://github.com/theurian/theurian/issues/47)).
- Step 6 says how to present a `halted` run's `changedPaths`, which it named
  without saying what to do with it. Three corrections
  ([#47](https://github.com/theurian/theurian/issues/47)): a token left on disk
  gets a named remedy rather than "keep or remove", since Core never deletes a
  credential a session may be holding; `changedPaths` is the field that says
  what was written, because in `halted` the `steps[].status` values are still
  the plan's; and `changedPaths` covers only the run that produced it, so on a
  repeated halted run a credential left by the first run appears as the token
  step reporting `satisfied` rather than as a path.
- That remedy is now the measured one, and it replaces advice that was wrong in
  two ways ([#47](https://github.com/theurian/theurian/issues/47)). The document
  said the way to be rid of the token was `theurian auth rotate`, and that
  deleting the file by hand meant reconfiguring every client that references it.
  `rotate` removes nothing — it replaces the value in place, rewrites the env
  file and restarts the daemon where it can, and its own docstring scopes it to
  "after a token has been exposed". And no client *configuration* ever holds the
  value: the MCP entry carries `${THEURIAN_MCP_TOKEN}` verbatim and the env file
  carries `THEURIAN_MCP_TOKEN="$(cat <token path>)"`. Measured: deleting the
  token file and re-running setup mints a new token at the same path, leaves
  `~/.claude.json` and the env file byte-identical, and performs no MCP write.
  Step 6 now splits the advice by what the user is doing next — carrying on
  (leave it; a later setup reuses the token it finds), suspected exposure
  (`rotate`), abandoning the install (delete it) — and keeps the one warning
  that held all along: a running daemon may hold the old value until it is
  restarted.
- The remedy is now stated as the conditional it is, and it names the third
  participant in a rotation
  ([#47](https://github.com/theurian/theurian/issues/47)). Two corrections to
  the advice above. `theurian auth rotate` restarts the daemon only when
  `detect_manager` finds a service manager *and* that manager reports the
  service as something other than not-installed; otherwise it reports
  `daemonRestarted: false` and puts the restart into `nextSteps`. A halted run
  typically stopped before the daemon-service step registered anything, which is
  precisely the case this paragraph is about — so the document now tells the
  agent to read `daemonRestarted` and relay `nextSteps`, rather than to tell the
  user a restart happened. And a configuration holding a reference is not a
  process holding one: the expansion happens once, at process start —
  `$(cat <token path>)` when a shell sources `~/.theurian/env`,
  `${THEURIAN_MCP_TOKEN}` when a client session starts — so existing shells and
  running sessions keep the old value until they are re-sourced or restarted.
  `_restart_daemon` returns that instruction on every path for this reason,
  including the one where it did restart the daemon. The delete-the-file branch
  carried the daemon half of that warning and nothing else; it now names every
  process that already read the value.
- `/theurian:ingest` no longer offers review history as something it ingests,
  and now says what the command does read
  ([#129](https://github.com/theurian/theurian/issues/129)). Its `description` —
  visible in Claude Code's command list — said "Ingest sources — docs, specs,
  and Git review history", as did the command's row in the plugin README, and
  the Rules illustrated evidence with an ingested review comment. No review
  history is ingested: `system.capabilities` reports `reviewIngestion: false`
  and `infrastructure/github/` holds no adapter, so an agent reading the old
  description would have offered a source Core cannot read. The Rules also told
  the user that "Theurian will not contact a repository that is not listed" in
  `.theurian/config.yaml`, which reads as a control in force. It is not one:
  **nothing reads the `providers.review.repositories` allowlist**, so it protects
  no one yet. That file itself *is* read, for one key — `security.secretScan`,
  by `security/project_config.py` and nothing else (ADR-0027 decision 3) — and
  this entry said the file was unread until this branch narrowed it to the key,
  the same correction #461 made to `ingest.md` itself
  ([#501](https://github.com/theurian/theurian/pull/501)).

  The document now says review ingestion is owed with Milestone 7, says the
  allowlist is not protecting the user, and enumerates what `theurian ingest`
  actually reads: files under `.theurian/`, plus three `git` reads —
  `rev-parse --show-toplevel`, `rev-parse HEAD` and `remote get-url origin`.
  Measured by running the command against a `git` shim that logs every
  invocation.
- `/theurian:ingest` no longer says it stores anything. It opened with "Read
  source material into the canonical store as evidence" and its first Rule said
  "Ingestion stores **evidence**", both of which describe a write that does not
  happen: `IngestionService` has no write path, parsed documents live in memory
  for the run, and the only file the command writes is the content-hash manifest
  `.theurian/cache/ingestion.json`. An agent reading the old text would
  have told a user that an ingested document is retrievable, which it is not —
  nothing reaches the canonical store until a migration is applied. The same
  overstatement in `ingest_command`'s docstring is corrected in
  [Core's changelog](../../packages/theurian-core/CHANGELOG.md)
  ([#198](https://github.com/theurian/theurian/issues/198) round one).
- `/theurian:ingest` no longer promises to report a partial result when
  candidate generation fails (FR-V5). `theurian ingest` generates no candidates
  and runs no summarization stage, and its JSON has no field for a partial
  result, so the instruction described a run that cannot happen. It now states
  FR-V5 as owed with review ingestion, which is owned by
  [#479](https://github.com/theurian/theurian/issues/479) — this entry and the
  document both named [#129](https://github.com/theurian/theurian/issues/129)
  until it closed on the wording rather than on the adapter, and the document
  was repointed in [#482](https://github.com/theurian/theurian/pull/482) while
  this changelog was outside that pass's file set
  ([#501](https://github.com/theurian/theurian/pull/501)).
- **`/theurian:propose` ran a command that does not exist.** Its step 2 shelled
  out to a `propose` subcommand to generate the proposal, and its step 4 offered
  a `propose accept` to approve it. The CLI registers neither, so a user running
  the slash command was sent into `No such command`
  ([#89](https://github.com/theurian/theurian/issues/89)). Same class as
  `/theurian:upgrade` in 0.1.1, and the reachable member of it: the compatibility
  remedy only fires once `coreCompatibility.minimum` is raised above the shipped
  Core, whereas this one fired every time the command was used.

  The command now runs
  [ADR-0013](../../docs/adr/0013-ai-writes-produce-proposals.md) §4's flow by
  hand. The agent writes `.theurian/proposals/<proposal-id>/` itself — the
  unscoped `Write` grant it already held, and the reason it holds it — and the
  approval steps are the user's: move the migration into `.theurian/migrations/`
  and the body to the path its `contentFile` names, check it with
  `theurian migrate validate --json`, open a pull request, and run
  `theurian migrate apply --json` after the merge. Both of those are registered.

  Two things the document now states because running the flow showed them.
  `contentFile` resolves from `.theurian/migrations/` rather than from the
  directory holding the migration file that names it, so a proposal whose path
  is relative to itself stops resolving the moment it is moved into place. And
  `theurian migrate validate` reads `.theurian/migrations/` only: while a
  proposal sits under `.theurian/proposals/` it reports zero migrations and says
  nothing about it, so nothing checks a proposal until a human has already
  decided to accept it.

  The `propose` subcommand is still the intended shape, and Milestone 7 builds
  it. Until then the migration format is knowledge the plugin carries, which is
  the boundary cost of the interim flow and why this plugin's README and
  `docs/integrations/claude-code.md` now say eleven of the twelve commands are
  thin adapters rather than all twelve.

  Running the interim flow a second time then found three more things, all of
  them in what the document tells the *user* to do. It named the proposal's
  migration `migration.yaml`, while `.theurian/migrations/` names files
  `<ulid>-<kebab-slug>.yaml`: accepting a second proposal moved that name over
  the first, and nothing reported it — measured, `migrate validate` answered
  `valid: true` with `migrationCount: 1` naming only the second migration, and
  `migrate apply` exited 0 having applied only it. The proposal now carries the
  final name, so accepting is a move that renames nothing, and the step says to
  stop on a name that already exists. The document also said
  `theurian migrate validate` was what enforced the schema, which read as though
  passing it meant the migration would apply; validate checks schema conformance
  only, and a revision missing `metadata.sourceAnchors` or reusing an applied
  `revisionId` gets `valid: true` and then exit 4 from `migrate apply` — after
  the pull request has merged
  ([#36](https://github.com/theurian/theurian/issues/36)). And the flow ended at
  `migrate apply`, which does not index what it applied: measured, `index status`
  reports `stale: true` immediately afterwards, so the approved knowledge was not
  searchable. There is now a step 4.6 that builds the index, and the pull request
  step says to include the proposal directory, since `evidence.json` is read by
  reviewers and never by Core.

  Two corrections to that step 4.6 and to what step 4.5 claims. The rebuild was
  unconditional, which walked into the RAPTOR trap the `/theurian:reindex` entry
  below describes — a plain build publishes `nodes: 0`, so on a forest-bearing
  project the summary retriever goes quiet — and it now asks about the forest
  first and uses `--raptor` only on a yes, never unasked (ADR-0008 decision 10).
  And step 4.5 said a failed apply "left no state database behind"; it does leave
  one, measured at 151,552 bytes with no pointer referencing it. The successful
  run's `databaseCreated: true` follows the changed state hash (ADR-0017), not
  anything the failed run cleaned up. The step now says both. The shape section
  also recommends `contentSha256` on every revision and `expectedRevision` on
  updates — optional to the schema, and what makes an out-of-band body edit or a
  concurrent change detectable rather than silent
  ([#210](https://github.com/theurian/theurian/issues/210)).
- **`/theurian:reindex` ran a command that does not exist**, the second live
  face of the same root cause as the entry above: a user-facing instruction
  naming a `theurian` subcommand that is not registered (#89). Its step 2
  shelled out to an `index rebuild`, while the `index` group registers `build`,
  `gc` and `status` — measured, `No such command 'rebuild'. Did you mean
  'build'?` and exit 2. `docs/integrations/claude-code.md` mapped the command to
  the same dead invocation.

  It now runs `theurian index build --json` and then `theurian index gc --json`,
  which is what the command was always describing. Measured on a scratch
  project: two consecutive builds produce two different `indexBuildId` values,
  the same chunk count, `published: true` both times, and **two files on disk** —
  there is no already-built short-circuit, so a plain `index build` is the full
  rebuild, and publishing does not reclaim what it replaced (ADR-0024 point 6).
  `index gc` is the explicit reclamation ADR-0007 requires; in the same run it
  removed exactly the superseded build, 159,744 bytes, and left the published one
  in place. The document now shows `index gc --dry-run --json` first, because
  step 3 is the only part of this command that deletes anything.

  That flow then turned out to destroy a RAPTOR summary forest. `index build`
  writes zero summary nodes unless it is given `--raptor`, and step 3 reclaims
  the build the new one superseded — which is the build holding the forest.
  Measured on a 128-chunk corpus: `--raptor` produced `nodes: 5`, a plain
  rebuild of the same state produced `nodes: 0`, and `index gc` then reclaimed
  the `--raptor` build, leaving the `nodes: 0` one alone on disk. The document
  recommended this flow "after a change of summarization provider", which is
  exactly when the forest matters. Step 1 now asks the user whether the project
  keeps a forest — `index status` reports nothing about summaries, so there is
  nothing to infer it from — and uses `--raptor` only on their yes, never
  unasked (ADR-0008 decision 10). Step 3 warns that a build made without it by
  mistake has to be redone *before* gc, not after. Three smaller corrections in
  the same pass: the confirmation now comes after the `gc --dry-run` output rather
  than before anything is shown, matching `/theurian:uninstall`; the step no
  longer asks for a duration `index build --json` does not report; and the rules
  name gc's two refusals — a pointer naming a missing build, and a pointer that
  cannot be read — which exit 1 and reclaim nothing, `--dry-run` included.

  Step 1 no longer says `/theurian:index` "handles the normal case
  incrementally", and the front-matter description no longer claims this command
  is "slower". No incremental path exists — every build re-derives from canonical
  state — so both were describing a cost difference the code does not have. What
  replaces them is the difference that is real: this command reclaims, and
  `/theurian:index` does not. The remaining incremental claims in
  `commands/index.md` are [#143](https://github.com/theurian/theurian/issues/143)
  and are not touched here.

### Security

- `/theurian:setup` narrows `allowed-tools` from `Bash(command:*)` to
  `Bash(command -v:*)` and drops `Edit`. `command` is a shell builtin that runs
  its argument, so the prefix pattern pre-approved arbitrary execution; the
  document's own Rules section already forbade editing configuration files.
- `/theurian:propose` narrows its `Write` grant to
  `Write(.theurian/proposals/**)`. The command's whole purpose is writing a
  proposal directory, and an unscoped `Write` auto-approved writes to
  `.theurian/migrations/` and `.theurian/knowledge/` — the two directories its
  own Rules section forbids it to touch.

  **That narrowing bounds what the command writes, not what it may invoke, and
  the documents now say so.** `/theurian:propose` claimed "There is no code path
  from this command to approved state" while the same front-matter's
  `Bash(theurian:*)` auto-approves `theurian migrate apply`, a canonical write;
  `/theurian:reindex` carries the same grant over the irreversible
  `theurian index gc`. `allowed-tools` grants and removes nothing — only
  `disallowed-tools` removes — which this changelog already recorded for
  `/theurian:upgrade` in 0.1.1 and which was reintroduced here from the other
  side. Both documents now carry the residual statement: during the manual flow
  containment is a documented rule plus a scoped grant, not a server-side check,
  and the "You cannot approve knowledge" rule is what keeps approval with the
  human. Narrowing the `Bash` grant itself is deliberately not done here and is
  tracked as its own class
  ([#209](https://github.com/theurian/theurian/issues/209)).

## [0.1.1] - 2026-08-09

### Fixed

- **An incompatible Core produced a silent blocked session.** `session-start.sh`
  sources `lib.sh`, whose `set -euo pipefail` imposed `errexit` on a hook that
  turns `errexit` off one line earlier on purpose — it must exit 0 no matter what
  Core reports. The incompatible-Core branch is a bare command substitution,
  `verdict="$(theurian::compat_check)"`; under `errexit`, its non-zero exit (3,
  `THEURIAN_EXIT_INCOMPATIBLE`) aborted the shell right there, so the warning,
  the verdict printed to stderr, and the intended `exit 0` were all unreachable.
  A session with an incompatible Core exited 3 with empty stdout and empty
  stderr — a blocked session with nothing telling the user why
  ([#90](https://github.com/theurian/theurian/pull/90)). `lib.sh` now runs
  `set -uo pipefail`; failure travels by return status, and the hook again
  prints the warning and the compatibility verdict to stderr on every session
  before exiting 0.
- **`/theurian:upgrade` ran a command that does not exist.** It called
  `theurian upgrade --check --json` and then `theurian upgrade --json`; `upgrade`
  has never been a registered Core subcommand and both exit 2 with `No such
  command` ([#42](https://github.com/theurian/theurian/issues/42)). The command
  now reports the compatibility verdict from `theurian version` and
  `theurian compat check`, then prints `uv tool upgrade theurian` /
  `pipx upgrade theurian` for the user to run. It never upgrades anything —
  Theurian does not obtain its own artifacts.

  **What enforces that is the document's "Never run the upgrade" rule, not the
  front-matter.** An earlier draft of this entry said `allowed-tools:
  Bash(theurian:*)` meant the command "cannot invoke an installer even by
  mistake". That is false: `allowed-tools` is a permission *grant* that
  auto-approves matching invocations, and only `disallowed-tools` removes
  anything — as `tests/unit/test_plugin_boundary.py` has recorded since
  `Bash(command:*)` was found pre-approving arbitrary execution. The document
  also read `compatibility.yaml` while granting no `Read`, which is the same
  mistake seen from the other side, so `Read` is now granted like every other
  command that reads a file.

  The command's `theurian compat check` invocation named
  `coreCompatibility.protocolVersion`; `protocolVersion` is top-level and
  `coreCompatibility` is `additionalProperties: false`, so an agent following it
  would have hit exit 2. Two tests now pin the document against `lib.sh`'s
  `theurian::compat_check` — the flag names, and the placeholder key.

  The command is kept rather than removed: `REQUIRED_COMMANDS` in
  `tests/unit/test_plugin_boundary.py` pins `upgrade` as one of the twelve §9
  commands, and both this README's command table and
  `docs/integrations/claude-code.md` list it. What changed is what it does.
- **The installer the plugin named produced a Core whose daemon cannot start.**
  All three surfaces — the `SessionStart` hook's Core-absent warning, the README's
  install block, and `/theurian:setup`'s prerequisite — said
  `uv tool install theurian`, which resolves, installs, and leaves out `uvicorn`;
  `/theurian:setup` then had nothing to configure
  ([#78](https://github.com/theurian/theurian/issues/78)). They now name
  `uv tool install 'theurian[daemon]'` and `pipx install 'theurian[daemon]'`, in
  the same words Core's own `core-missing` remedy and `core-present` step use.
  The quotes are part of the command: unquoted, `theurian[daemon]` is a glob
  under zsh and fails with `no matches found`.
- **`compatibility.yaml`'s declared floor had a hole in it.** `theurian compat
  check` translates Core's PEP 440 version into SemVer before comparing it
  against a plugin's declared range, and the translation did not preserve
  ordering — PEP 440 sorts `.devN` below every pre-release phase, while SemVer's
  ASCII comparison put `dev` between `beta` and `rc`. Over one release train's
  780 ordered version pairs, 99 came out backwards. This plugin's own
  `minimum: 0.1.0-dev.0` accepted `0.1.0.dev1`, refused `0.1.0a1`, `0.1.0a2` and
  `0.1.0b1`, then accepted `0.1.0rc1` again — versions strictly newer than ones
  it had just refused
  ([#70](https://github.com/theurian/theurian/pull/70)). Core now orders both
  its own version and a declaration's bounds by the release train (`dev` <
  `alpha` < `beta` < `rc` < final) rather than by SemVer's ASCII comparison.
  `compatibility.yaml`'s values are unchanged; only what `0.1.0-dev.0` accepts
  is.

### Compatibility

| | Version |
| :-- | :-- |
| Plugin | 0.1.1 |
| Core | >= 0.1.0-dev.0, < 0.2.0 |
| Protocol | theurian/v1 |
