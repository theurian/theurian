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

- **`theurian index build` scans the served corpus for secrets (SEC-11's second
  control)** ([#329](https://github.com/theurian/theurian/issues/329), ADR-0027
  decision 3). SEC-11 shipped at the approval gate, so a body that entered the
  corpus before that scanner existed — or through a migration written straight
  into `.theurian/migrations/`, which never passes `propose accept` — was indexed
  and served with no scan having seen it. The build now reads every text channel
  it serves: the body, keyed on the exact `title + body` string the index chunks
  so a credential in a title is covered like one in the prose; each source
  anchor's author-written fields, which ride verbatim on every search result and
  every fetch; and each relation `note` this deployment would publish. The anchor
  field set is one constant shared with the approval gate
  (`domain.knowledge.AUTHORED_ANCHOR_FIELDS`), so a field cannot join one control
  and not the other. It re-checks that population whole on every rebuild rather
  than a delta.

  **The population is the approved, in-ceiling corpus this deployment serves by
  default — not the whole canonical store**, and the difference is recorded
  rather than implied. A `draft` or `rejected` body reachable through
  `includeUnapproved` is not scanned by a default build: reading a withheld row
  into a published count is how the existence of withheld content leaves through
  a number (T-17), and an operator who serves drafts scans them by building with
  `--include-unapproved`. A superseded revision stays in the canonical store
  unscanned, which is why the remedy rotates the value before it supersedes the
  revision. `SECURITY.md` and the threat model carry both.

  **It reports and never refuses, and that is a boundary rather than a
  preference.** A landed secret is readable through `knowledge.search` and
  `knowledge.get` the moment `theurian migrate apply` writes it — search degrades
  to an unranked canonical substring scan and `get` reads the store by id — so a
  build that refused to publish would deny ranking without un-disclosing
  anything, and a project that had never built an index would be denied ranking
  for ever. The same `security.secretScan` knob therefore selects signal severity
  here rather than a gate: under **`block`** the index is published, `theurian
  index build` exits **6** — distinct from 1, so a pipeline can tell "published
  with a finding" from "nothing was published" — and `theurian doctor` reports
  `indexSecretScan: degraded` with the get-it-out-of-the-corpus remedy until a rebuild
  comes back clean; under **`warn`** the finding is reported and the exit stays
  0; under **`off`** nothing is read. Nothing is ever retired automatically: the
  detector is best effort, and acting on a false positive would be silent data
  loss.

  What the report may carry is bounded. A finding names the item id, a channel
  tag built from literals and an integer index, the position within the string
  that was scanned, the detector family, and at most four characters of the match
  — the ceiling `SecretFinding` already refuses to be constructed past — and the
  findings across every channel of every item share one `MAX_FINDINGS` budget, so
  a corpus cannot choose how long the list is. The scan sits below the
  `may_surface` and `may_disclose` filters, and a relation note is gated exactly
  as `knowledge.get` gates it — both endpoints visible — so the published count
  is a function of the rows the build wrote and cannot carry the existence of one
  it withheld. `theurian doctor` publishes only the status, the policy and the
  count: a `--report` is pasted into public issues, and the build's own output is
  where items are named. The channel index is a position in the list the serving
  surface publishes, not in the store's own — `knowledge.get` withholds an edge
  whose far end the caller may not see, so numbering the unfiltered list would
  send a reader to a different, benign row. If the record cannot be written after
  the index has published, the report still lists what was found and carries a
  named `recordWarning` saying the verdict will not reach `doctor`.

  The remedy names three routes rather than two, because two of the channels do
  not live in a revision: a new `upsertRevision` replaces a body, a title and a
  source anchor, a relation note needs `removeRelation` and survives superseding,
  and `deprecateItem` withholds all of them.

  `theurian ingest` still runs no scan of its own and needs none, for a reason
  about **storage**: it stores no content at all — a manifest and an in-memory
  read — so at that point nothing is persisted for a scan to have missed. (The
  reason this replaces, "everything its manifest names is read again by the
  build", was false in both directions: a specification and an orphaned body file
  are named by a manifest and never enter the canonical store, and a body reaches
  the store through routes no manifest names.) Draft-time advisory scanning
  remains owed ([#330](https://github.com/theurian/theurian/issues/330)). T-15
  was re-graded against the shipped control and stays **High**: the build
  detects, and `migrate apply` is still where the content becomes readable.

  A withdrawal-triggered index purge republishes the pointer at a copy of the
  published build, and it now carries the scan verdict forward under the new
  build id: an unrelated `theurian migrate apply` no longer clears a `degraded`
  `doctor` with no rebuild in between. `doctor`'s verdict is also anchored to
  `BuildProvenance`, so a repository that ships a `.theurian/state/` past its
  managed ignore cannot make a never-built machine report `clean` (ADR-0004,
  SEC-7).

### Fixed

- **One commit whose message is not valid UTF-8 no longer costs the whole
  review-finding corpus**
  ([#496](https://github.com/theurian/theurian/issues/496), ADR-0029 decision 3).
  `theurian findings build` decoded the whole `git log` output in a single call,
  so a public commit carrying bytes that are not UTF-8 — git validates nothing
  about a commit message's *encoding* — failed the build with *"Cannot read
  refs/remotes/origin/main history"* and a remedy naming a `git fetch` that
  cannot help a history that is already local and readable. Every well-formed
  trailer beside that commit was lost with it, and public history is signed and
  append-only, so there was no forward fix.

  The stream is framed before it is decoded now, and each record is decoded on
  its own. A message that will not decode costs its own record's trailers and
  nothing else: the commit is counted in the build report's `rejected` total and
  recorded with a bounded, replacement-decoded excerpt of its message, so the
  loss is visible to whoever ran the build and never silent. Nothing on the
  rejected side reaches a `review.findings` response, that excerpt included. The
  trailer grammar is untouched, so `parserStamp` does not move and no rebuild is
  forced — and no store written under the old behaviour can exist, because a
  history carrying such a commit produced no store at all.

- **`theurian doctor`'s initial-index step answers whether the *current*
  knowledge state is built, so a pulled-but-not-applied checkout is no longer
  reported as built** ([#451](https://github.com/theurian/theurian/issues/451),
  [#519](https://github.com/theurian/theurian/pull/519)). The step asked whether
  an active-state pointer existed at all, and that pointer is never removed once
  a first `theurian migrate apply` has written it — so from that moment the step
  printed "Knowledge state is built." for every later migration set, and the arm
  that names `theurian migrate apply` as the remedy could not be reached. That is
  exactly the state a `git pull` leaves a deployment in, which is when someone is
  most likely to run `doctor` to find out what to do, and `theurian project
  status` answered `stateBuilt: false` about the same tree in the same minute.
  The predicate is now `project status`' own — the state database for the hash
  the *loaded* migration set resolves to — so the two commands address one file
  and answer alike.

  **A migration set the loader refuses gets its own sentence** instead of being
  folded into "not built": "Cannot tell what state this project is at: its
  migration set could not be read. Run `theurian migrate validate`, which prints
  why." It names no culprit on purpose. An incomplete install — published JSON
  Schemas missing, or a schema that cannot be read — arrives on the same arm as a
  malformed migration file, and a sentence blaming the operator's YAML would send
  the reader to their own files for a broken installation. The refusal itself is
  `migrations-valid`'s to publish, in the same report.

  **The step's `detail` no longer announces retrieval indexes as future work.**
  It read "Retrieval indexes arrive in Milestone 5; there is nothing to build
  yet." while `theurian index build` was a shipped command; it now names which
  artefact the step is about and where the other one is reported: "This is the
  canonical state `theurian migrate apply` writes. The retrieval index over it is
  separate: `theurian index build` builds it and `theurian index status` reports
  it." **`doctor` still has no step for the retrieval index itself** — `theurian
  index status` is the surface that answers `built`, and §6.2 row 17 of
  [the requirements analysis](../../docs/architecture/requirements-analysis.md)
  now records that divergence and its owner,
  [#528](https://github.com/theurian/theurian/issues/528).

  Not a breaking change: every arm of the step is still `not-applicable`, and no
  JSON key, step id or exit code moves. What changed is which sentences a report
  carries.

- **A doctored `.theurian/` no longer truncates a tracked file, and a fault at
  the state-database path no longer escapes `--json` as a traceback**
  ([#481](https://github.com/theurian/theurian/issues/481),
  [#483](https://github.com/theurian/theurian/issues/483),
  [#484](https://github.com/theurian/theurian/issues/484), ADR-0004).
  Three failures sharing one delivery: `.theurian/state/` and
  `.theurian/runtime/` are derived and git-ignored, so a repository
  contributor can force-add a symbolic link past that ignore and every clone
  carries it.

  **Taking the write lock truncated whatever the lock path pointed at**
  (#481). `WriteLock` opened its lock file with `open("w")`, which follows a
  symbolic link and `O_TRUNC`s the target — and a lock file's bytes are never
  read by anything here, so clearing them was never a step this class needed.
  With a link at `.theurian/runtime/write.lock`, a writer's first act
  destroyed the file the link named: measured with a real CLI run, a tracked
  file in the working tree went to zero bytes while the command exited 0 and
  reported success. Containment does not cover this and could not — it refuses
  a path resolving *outside* the project root, and a link pointing at a file
  *inside* the tree resolves inside it and passes untouched, which is exactly
  the shape that damages a working tree. The open is now
  `os.open(O_WRONLY | O_CREAT | O_NOFOLLOW, 0o600)`: no truncation, and the
  link is refused inside the kernel's own resolution of that call rather than
  by an `is_symlink()` check with a window between the test and the open. The
  refusal arrives as `WriteLockUnusableError`, a `TheurianError` carrying a
  remedy that names the link, so it reaches a `--json` caller as an envelope
  rather than as a bare `OSError`. `O_NOFOLLOW` constrains the final component
  only, and that bound is recorded rather than closed: an attacker who
  rewrites a *prefix* component between the `mkdir` and the open defeats the
  ordering the refusal rests on, and the error then names the lock file while
  the culprit is a directory above it.

  **A containment refusal on the state-database path reached `--json` callers
  as a Rich traceback** (#483). `ProjectPaths.database_for` routes through the
  containment chokepoint, which refuses a state-database path resolving
  outside the project root — and that call was the last statement of
  `_require_project`, outside the `try` wrapping everything else, so the
  refusal escaped every caller of it at once. Seven of the nine commands in
  the CLI sweep answered with nothing on the machine channel and a traceback
  on stderr carrying absolute paths into the installed source tree;
  `project status` reached the same refusal through its own direct call and
  needed its own handler. All seven now publish the `{error, remedy}` envelope
  at the state exit code, and that population is *derived* — the two commands
  resolving no state path (`project list`, `version`) are subtracted from the
  sweep, so a command added later is classified by measurement rather than by
  a second list someone has to remember.

  **The remedy is keyed to the path that was refused.** One cure was published
  for every escape alike, and it named the knowledge directory — the
  operator's authored source — for a problem in derived state, then sent the
  reader to `theurian init`, which meets the identical refusal. Following it
  cost a round trip to learn nothing. A leaf under a derived subdirectory now
  names that *subdirectory*, because a link anywhere between the subdirectory
  and the leaf produces the same refusal: naming the refused leaf would name a
  file inside the link's target and cure nothing.

  **That instruction is rendered without a trailing slash, which is a safety
  property and not a formatting choice.** The first cut named the path with
  one — `.theurian/runtime/` — and BSD `rm -rf` on a trailing-slash symlink
  *follows the link*. Measured end to end: obeying that text literally
  destroyed the entire directory the link pointed at, outside the working
  tree, and left the link in place, so the retry met the identical refusal.
  The shipped text gives both forms — `rm` for a link, `rm -rf` for a real
  directory, since neither command honestly covers both shapes — calls the
  trailing slash out explicitly so a reader who tidies the path does not get
  the destructive form back, and is measured curative end to end. What it
  promises about the cost is narrower than the sentence it replaced: removing
  a link costs nothing, and removing a real directory here costs only
  artifacts Theurian rebuilds. "Nothing authored is lost" was never this
  remedy's to promise, because a link's target is arbitrary.

  **A filesystem fault at the state database escaped `--json` as well**
  (#484). `migrate status` had no `(OSError, sqlite3.Error)` arm at all, and
  `migrate apply` carried one on either side of its transaction but not across
  it — so which section met a fault depended on whether the project had ever
  been applied. With no provenance record it landed in a covered section; with
  one, which is every project after its first apply, it landed in the
  transaction and escaped. Both are backstopped now, at the same exit code and
  envelope, with a cure that names the precondition to check before the
  rebuild. Neither deletes anything: `migrate apply` deliberately does not
  clean up here, because the database is usually one this installation already
  built and provenanced, and removing a live state because an unrelated fault
  interrupted a write would turn a failed command into data loss.

  **A write conflict is answered by waiting, not by deleting the state.**
  Those backstops caught a transient conflict in the same net as a directory
  planted at the database path, and answered both with the second one's cure:
  measured with a second connection holding `BEGIN IMMEDIATE` — an operator's
  `sqlite3` shell, another tool, a process that exited with a transaction open
  — both commands published `database is locked` beside an instruction to
  delete `.theurian/state/`, and both exited 0 on the retry once the holder
  let go. An operator who follows that remedy destroys derived state for
  nothing; a scripted agent does it without reading. Contention is now
  converted at its source into `WriteTransactionBusyError`, a typed
  wait-and-retry answer stating that nothing is damaged and nothing needs
  rebuilding, across every statement the transaction helper issues on its own
  behalf. Since round two that includes the pragmas and schema read that make
  a connection usable, which run *before* `BEGIN IMMEDIATE`: a holder in
  `PRAGMA locking_mode = EXCLUSIVE`, and a database left in a rollback journal
  whose `journal_mode = WAL` pragma has to take the conflict itself, both
  reached the delete-your-state cure for a database in perfect condition. The
  predicate was already right and only its placement was wrong, so the
  classification moved ahead of the broad catch rather than being widened. The
  caller's own statements are untouched: past `BEGIN IMMEDIATE` a failure is
  the caller's statement against the caller's data, which this layer must not
  reinterpret.

  **What that conversion is scoped to.** `SQLITE_BUSY` is the only arrival
  measurement reached (2026-09-03, SQLite 3.47.1). `SQLITE_LOCKED` and the
  primary-code mask beside it are defense in depth against an extended
  spelling of a condition the set already names — kept because they over-cover
  in the safe direction, not because anything produced them. The `COMMIT` arm
  is believed unreachable under this product's own `journal_mode = WAL`, is
  driven by no test, and is kept for the rollback-journal edge; the
  post-`BEGIN` residue is scoped the same way, and the rollback-journal
  neighbour is stated unproven in both directions rather than folded into the
  WAL claim. `ROLLBACK` is deliberately left unconverted, and that exclusion
  is argued rather than measured: no input was found that makes it fail while
  a caller's exception is already travelling.

  **This closes the contention fault family, not fault routing in general.**
  The permissions face — a database file the process cannot open, or an
  unwritable state directory, which three places grade three different ways —
  is [#530](https://github.com/theurian/theurian/issues/530). Unifying the two
  escape remedies, and the exit codes that differ beside them, is
  [#525](https://github.com/theurian/theurian/issues/525). None of this is a
  breaking change: the new exception types are internal, and the envelopes
  replace tracebacks that were never a contract.

- **`theurian propose accept` no longer prints a credential that its own scan
  would have withheld, and it now reads the proposal's `evidence.json`**
  ([#360](https://github.com/theurian/theurian/issues/360),
  [#361](https://github.com/theurian/theurian/issues/361),
  [#339](https://github.com/theurian/theurian/issues/339), SEC-11, T-15,
  ADR-0027 decision 3). Two disclosure surfaces on one command, fixed together
  because they share one file.

  **Refusals echoed the author's own names, and several of them fire before the
  scan runs.** Measured on `63e3851` under the shipped `block` default, a
  credential placed in a migration filename, in a `contentFile` or in a
  migration's inner `id` was printed at full length into the terminal and into
  `accept --json` — by refusals about a name collision or a missing file, which
  beat the scan to the output. Every author-controlled string
  `propose accept` interpolates into a message is now cut to what may be printed
  (200 characters for a name, the bound this codebase already uses for an
  untrusted YAML scalar; 2,000 for another component's own report) and *then*
  scanned, so the guard is keyed on exactly the text that will be printed. A
  string the detector reports is replaced whole by a fixed literal — *a name that
  appears to carry a secret* — and never partially echoed, because the detector
  publishes no match length and a "clean" remainder around a redacted span is a
  partial copy besides. Two channels beyond the reported ones close with it:
  PyYAML's parse error, which quotes the offending source line before anything
  is scanned, and `jsonschema`'s message, which quotes the offending instance in
  full. **This changes what a refusal prints**, so a script that parsed a name
  back out of an error string will not find it there; the `{error, remedy}`
  shape and every exit code are unchanged.

  **`evidence.json` travelled into the pull request unscanned.** The scan covered
  everything the command *lands*, and the record is not landed — but `accept`
  deletes the migration and the bodies, leaves the record behind, and then tells
  the author to open a pull request with the proposal directory in it, and
  `.theurian/proposals/` is not git-ignored. So an agent's free-text `reasoning`
  reached Git history under the default `block` with `findings=0`. It is scanned
  now under the same `security.secretScan` policy, **whole-text rather than field
  by field**: what travels is the file byte for byte, and `reasoning` is under no
  schema constraint, so an enumeration would gain an unscanned channel the next
  time the record gained a field. Under `block` the refusal arrives **before** any
  next step is printed, which is the property that matters. A record that is
  present but unreadable — symlinked, or past the source-file cap — refuses under
  `block` and is skipped under `warn`: `block` promises that nothing it cannot
  clear gets past, and `warn` already proceeds past a finding. An absent record is
  still fine, because an interrupted `draft` legitimately has none.

  **The two SEC-11 controls sit at opposite postures on purpose.** At accept time
  a human operator is present to act on a refusal, so refusing is the correct
  action and `block` is the default. At build time nobody is there and the content
  is already readable, so refusing would deny ranking over knowledge the project
  has already merged — it would self-DoS retrieval
  ([#329](https://github.com/theurian/theurian/issues/329)). Same control class,
  opposite posture, each for a stated reason; the evidence channel belongs to the
  first.

  **What this does not reach, stated rather than implied.** The gate's reach is
  the *detector's* reach: it withholds a string the detector reports and does not
  promise no credential is printed — PyYAML's `Mark.get_snippet` cut the `sk-`
  prefix off a 43-character token before the gate saw it, leaving 32 lower-case
  hex characters no family matches, and they were printed (measured 2026-09-04
  through the real CLI). A credential spelled with JSON `\uNNNN` escapes is in the
  parsed evidence value and not in its bytes, so the whole-text scan misses it;
  that is unreachable through the record `propose draft` writes. The
  migration loader still prefixes a landed migration's filename onto every error
  it raises — a different producer, reached by every command that resolves a
  project context, so its message arrives before `accept` runs at all
  ([#537](https://github.com/theurian/theurian/issues/537)). And `accept --json`'s
  `migrationFile` and `bodyFiles` **success** fields still name landed paths at
  full length by decision: a payload whose job is to say what was written reports
  nothing if it is redacted, and reaching one needs `warn`, under which the same
  string is already published redacted beside it. Draft-time advisory scanning
  remains owed ([#330](https://github.com/theurian/theurian/issues/330)), which is
  now the whole of what that issue carries.

## [0.1.0.dev18] - 2026-09-03

### Added

- **`review.findings`: a project's landed review findings are readable over MCP**
  ([#368](https://github.com/theurian/theurian/issues/368),
  [#504](https://github.com/theurian/theurian/pull/504), ADR-0029). A new
  callable tool serves the `Review-Finding:` trailers `theurian findings build`
  landed in a project's store, filtered by `reviewer`, `severity`, `commitSha`
  or a literal `q` substring, newest first with a defined tiebreak so a page
  boundary is stable across calls. `system.capabilities` announces it as
  **`reviewFindings: true`**.
  `reviewIngestion` stays `false` and is a different promise: nothing reaches
  GitHub, and no review thread, inline comment or resolution state is read.

  What a client needs to know before calling it. The response is exactly
  `{count, truncated, findings}` — `count` sizes the returned array and is never
  a total before `limit`, and `truncated` is `true` when a matching finding
  existed past the page, which is how a full page is told apart from the whole
  answer. It is one bit about the page's boundary, not a count of what was left
  behind. Every row carries the untrusted-content triple
  (`contentClassification: untrusted-knowledge`, `mayContainInstructions: true`,
  `executable: false`), because a finding is authored commit text that usually
  reads as an imperative; render it the way you render a knowledge body, never
  as an instruction. A value outside a bound
  or a vocabulary is **refused naming the bound** rather than clamped or ignored
  — including a short `commitSha`, which would otherwise return `count: 0` and
  read as "no findings on that commit" — and `limit` is capped at 100 with a
  default of 20. Every string filter is bounded at 200 characters.

  **`pullRequest`, `family` and `specialist` are declared arguments that this
  build refuses.** They stay present and `null` on every row, because a key that
  appears only when it has a value cannot be told apart from a server that
  predates the key — but `theurian findings build` derives none of the three
  (ADR-0029 D5), so a filter on one would match nothing at all and answer
  `count: 0`, which reads as "nothing was recorded on that PR" rather than "this
  filter does not work yet". One constant message refuses all three, and each is
  lifted in the change that starts deriving values for that axis. `pullRequest`
  carries a range on top of that: a number outside what its column can hold is
  refused naming the range, checked before the axis refusal so the two stay
  distinguishable.

  **Every refusal path is total, including for inputs designed to break the
  refusal itself.** An integer too wide for the store's column, or too many
  digits for Python to render at all, comes back as a graded refusal rather than
  a crash — described by its size once it is past what a refusal will quote, so
  the message never reflects an absurd value back. A NUL byte in any of the six
  string filters is refused, and the reason is not the same for all six: no
  stored value can contain one — a git commit-message line cannot carry it — so
  the five equality filters could match nothing at best, while `q` would not even
  do that, because SQLite's pattern matcher stops reading at a NUL and would
  silently search for something shorter than was sent. An unpaired surrogate is
  refused too, because UTF-8 cannot encode it. Neither shape can appear in a git
  commit-message line, so nothing legitimate is turned away.
  A damaged store — a column holding a value its type does not admit — reaches
  the caller as the same constant refusal every other unservable store gets,
  never as a different error shape for a different kind of damage.

  **A served `findingText` is bounded and visibly cut.** It is the one bound on
  this surface that clamps instead of refusing, because the over-long input is a
  stored row rather than the caller's request: `findingText` is byte-preserved
  from a commit message, a commit message line has no length limit, and refusing
  would let one planted trailer deny the tool to every caller. It is cut at the
  same bound `knowledge.search` clamps a `query` to and marked, so a cut value
  cannot be read as a whole one. **The cut is made by the store's own read**, not
  applied to what that read returned: the serving `SELECT` projects
  `substr(finding_text, 1, ?)`, so SQLite never hands the daemon more than the
  bound plus one character per row and a planted line no longer sizes what one
  call costs. The published bytes are the same either way. **How many of these
  reads run at once is bounded too**, by an admission gate of this tool's own
  with its own refusal message — sharing `knowledge.search`'s would refuse a
  findings caller with a message about concurrent *searches*.

  **A findings store is served only if this installation built it** (ADR-0004,
  SEC-7). The store is derived and git-ignored like the canonical state and the
  retrieval index, so a repository contributor can force-add a fabricated one
  past that ignore and presence on disk is evidence of nothing;
  `theurian findings build` now records the build out of the repository tree and
  the tool refuses a store with no record, in the same words an absent store
  gets. A build that writes the store and cannot record it **fails** rather than
  reporting success: reporting `built: true` for a store the serve path refuses
  would be false.

  A project whose store has not been built, or whose store was built by a
  superseded schema or trailer grammar, is **refused with one constant message**
  naming `theurian findings build`, never answered empty: "never built" must not
  be readable as "no findings". The message carries nothing from the request or
  from any project's contents, so which of those causes fired is not published.
  Rejected trailers are unreachable rather than filtered — no argument selects
  them, and the store's serving read never touches that table. The full wire
  contract is
  [`schemas/mcp/review-findings-response.schema.json`](../../schemas/mcp/review-findings-response.schema.json)
  and [`docs/protocol/mcp-tools.md`](../../docs/protocol/mcp-tools.md).

### Changed

- **The wheel-shipped project-config schema no longer says nothing reads the
  file; its root description names the one reader**
  ([#455](https://github.com/theurian/theurian/issues/455),
  [#501](https://github.com/theurian/theurian/pull/501)). The schemas are
  force-included into the distribution by `hatch_build.py` and land at
  `theurian/schemas/`, so a user who opened the published
  `config/project-config.schema.json` read *"Nothing in src/ reads this file, so
  no value in it takes effect today"*. That has been false since ADR-0027
  decision 3 shipped `security/project_config.py`, which takes
  `security.secretScan` from the file. **This is a claim correction, not a
  contract change**: no key is added, removed, renamed or re-typed, and every
  config file that validated before validates now.

  Three faces, one sentence. It also cited
  [#129](https://github.com/theurian/theurian/issues/129) as the owner of the
  still-reserved review allowlist — closed on 2026-08-22 on the wording rather
  than on the control, leaving the allowlist with no open owner — and anchored
  the rest to "Milestone 7", a counter this project stopped planning against.
  The description now names the one reader and the one key in force, says every
  other published key is reserved, repoints the owner to
  [#429](https://github.com/theurian/theurian/issues/429), and drops the
  milestone anchor. `providers.review.repositories`' own description carries the
  same repoint.

  **A fourth face, found in review of the rewrite itself.** The replacement
  sentence said *"Each reserved key's own description says what it is owed
  with"*, and three of the ten reserved keys carry such a clause —
  `providers.review.repositories`, `raptor.enabled` and
  `security.maxSourceFileBytes`. It is a claim about the artifact a reader is
  holding, on the artifact's most-read line, and it sent that reader to seven
  descriptions to look for something not there. Deleted; the specific statement
  it introduced — that the review-ingestion allowlist is owed with the first
  external fetch path — stands on its own and keeps its
  [#429](https://github.com/theurian/theurian/issues/429) pointer. The
  whole-text pin moved in the same commit, and the built wheel was JSON-parsed
  to confirm the shipped copy carries the corrected sentence.

  **Pinned in both directions for the first time**, which is why the false
  sentence survived four sweeps unnoticed. What is pinned is stated as a
  derivation rather than as coverage, and the sentence below is *rebuilt* from
  the table and the schema by
  `test_the_changelog_states_the_pin_reach_this_module_actually_has` — so
  unpinning a key reddens it here rather than leaving this paragraph to rot.
  Re-derived after round three widened the table: the schema publishes **12**
  descriptions — the root and 11 key blocks —
  and **12 of the 12** carry a `WATCHED_KEY_DESCRIPTIONS` row in
  `tests/unit/test_config_key_call_sites.py`: the root, `providers`,
  `providers.embedding.apiKeyEnv`, `providers.embedding.endpointEnv`,
  `providers.review.repositories`, `raptor.enabled`,
  `raptor.minChildrenPerSummary`, `retrieval.includeStatuses`, `retrieval.rrfK`,
  `security.maxSourceFileBytes`, `security.secretScan` and `traceabilityPolicy`.
  Every published description is pinned. Reaching the root at all is what is new;
  every earlier key enumerated *key blocks*, and the root is not one. **Each row
  is the whole published text**, matched exactly, so a fabricated control claim
  *added* beside a description's real sentences is RED — the direction no
  fragment row has. Three rows carried fragments until round three, on the
  argument that a reserved key's description "asserts nothing a reader can act
  on". That is false of `retrieval.includeStatuses`, which describes the status
  gate, and of `security.maxSourceFileBytes`, which describes the limit
  `security/paths.py` enforces: a contradicting sentence added to either shipped
  in the built wheel with every check green. The schema separately joined
  `tests/unit/test_raptor_config_claims.py`'s scanned prose surfaces. A reader
  added for any of the five spellings in `WATCHED_SPELLINGS` — four of them
  published key blocks, plus `raptor.maxLevels`, which has no block — reddens
  the call-site scan; a sentence drifting back to the universal reddens the
  prose surface.


### Fixed

- **Two `theurian findings build` runs at once no longer tear each other's
  store, and a reader never observes a half-built one**
  ([#404](https://github.com/theurian/theurian/issues/404),
  [#492](https://github.com/theurian/theurian/pull/492)). The rebuild took no
  lock and wrote in place under the published name — it unlinked the live file
  and recreated it there — so a concurrent reader could observe a missing store
  (which `dump` answers *empty*, indistinguishable from a genuinely empty
  corpus) or one whose schema had committed and whose rows had not, and an
  interrupted rebuild destroyed the previously good store outright. Measured
  2026-09-02 on a 12×4 scratch twin (48 real CLI children): on the pre-fix shape
  12 of 48 workers failed with `disk I/O error` or
  `table findings_metadata already exists`; on the fixed shape 48 of 48 exited 0.
  The suite-runnable regression guard runs a smaller 3×3 = 9 children, enough to
  detect the tearing at high probability in ~3 s. PR #396 had already recorded
  the same class from the other side — workers reporting `FindingsStoreError` in
  17–21 of 25 rounds, one iteration leaving a file with **no tables at all**
  under the publish name.

  The rebuild now assembles at a `.building` sibling and publishes with
  `os.replace`, the discipline `index build` already used, and `findings build`
  holds the project's advisory write lock across the whole store write in **one
  continuous hold** — not the two sequential holds
  [#468](https://github.com/theurian/theurian/issues/468) measured leaving a
  window worse than the race they closed. The git read stays outside the hold:
  it touches nothing the lock protects, and holding a project's single writer
  across a 30-second subprocess would block `migrate apply` for no guarantee.
  Atomicity is a clause of the `ReviewFindingStore` **port** now rather than a
  property of one adapter, because an implementation that wrote in place would
  satisfy every other clause while handing the serving slice a window in which
  the corpus reads as empty. A failed rebuild leaves the previous store whole
  and strands no working file, and SQLite's `-wal`/`-shm` companions are reaped
  with the file they belong to — a rename cannot reconcile a stale write-ahead
  log the way an in-place `connect` could. One ordering the lock deliberately
  does not fix: two rebuilds can read git at different instants and the earlier
  reader may publish later, so the survivor can be one commit behind. It is a
  whole, self-consistent, correctly stamped store either way, and the next
  rebuild converges.

- **A finding's `committed_at` is stored as a UTC instant, so ordering by it is
  chronological** ([#405](https://github.com/theurian/theurian/issues/405),
  [#492](https://github.com/theurian/theurian/pull/492)). The column kept git's
  `%cI` with the committer's own offset. SQLite compares TEXT byte-wise, so the
  column was not a sort key at all: a `+14:00` commit earlier in real time
  sorted after a `-11:00` commit that was later, and one instant written
  through two offsets was two unequal strings that unrelated rows could fall
  between — the same bug class PR #112 recorded for the canonical store. The
  value is now normalised (`astimezone(UTC)`) and fixed width
  (`timespec="microseconds"`, always 32 characters), so equal instants compare
  equal and a sub-second value cannot sort against a whole-second one on the
  byte at offset 19. The git source normalises at parse and refuses an
  offsetless date as unrepresentable rather than letting `astimezone` read the
  *machine's* own timezone into a stored value — that would make the store a
  function of where it was built — accounting the record as one rejected entry
  instead of aborting the load.

  **`FINDINGS_SCHEMA_VERSION` moves 1 → 2**, and the constant's rule is widened
  to say why: a bump is owed for a change to the *encoding* of a column's value
  and not only to the DDL text, because a reader that mis-decodes a column is as
  wrong as one that misses a table. **No migration, and no client-visible
  break** — the store is a wholesale projection of git history (ADR-0004),
  `theurian findings build` rebuilds it unconditionally on every run, and it is
  still the only shipped consumer, so a version-1 file is replaced rather than
  upgraded. `StoredFinding.committed_at` round-trips the *instant* of
  `ReviewFinding.date`, not its spelling; a reader wanting the committer's own
  offset has to get it from git, because the store no longer keeps it.

- **`parserStamp` moves when the parser's mechanics or its matching behaviour
  change, not only when a vocabulary literal does**
  ([#406](https://github.com/theurian/theurian/issues/406),
  [#492](https://github.com/theurian/theurian/pull/492)). The stamp hashed the
  five closed-vocabulary literals ADR-0029 decision 2 names, so a change that
  widened what the parser *accepts* while leaving every literal byte-identical
  left it still — tolerating a TAB after the key, accepting an indented trailer
  line, or an `Enum._missing_` hook that made `CODE-REVIEW` or `Security`
  parse — and a store built under the old grammar then read as current under the
  new one. Three sections are hashed now: the vocabulary literals as before; a
  **matching surface** per governed vocabulary, computed as everything this
  codebase's source added to the class body once a plain `StrEnum` baseline is
  subtracted, which is where a `_missing_` or `__new__` widening lives; and the
  **behaviour** the grammar gives to a fixed probe matrix, run through the whole
  path — the column-0 extraction rule and then the parse mechanics. That
  extraction rule moved into the domain as `keyed_lines`, since it was grammar
  the git adapter owned privately and therefore out of the stamp's reach.

  **The residual is stated rather than closed:** the behaviour section separates
  only the mechanics its probes distinguish, so a widening no probe separates
  leaves the stamp still and owes a probe; the other two sections are total over
  their populations. The stamp is a function of Python *semantics* rather than
  of source text — verified byte-identical across two fresh interpreters — so a
  behaviour-preserving refactor does not mark every store stale, and no
  interpreter upgrade can drift it. The published `parserStamp` value changes
  with this release; it is opaque, nothing reads it back yet, and the one writer
  rebuilds unconditionally.

- **A `Review-Finding:` trailer folded into a commit's subject paragraph is no
  longer dropped unaccounted**
  ([#410](https://github.com/theurian/theurian/issues/410),
  [#492](https://github.com/theurian/theurian/pull/492)). The git source read
  `%b`, and git's `%b` excludes the first *paragraph* rather than the first
  line: in a message whose subject is not followed by a blank line every
  following line folds into the subject and `%b` is empty, so a column-0 trailer
  sitting there reached neither the accepted nor the rejected tuple — falsifying
  the loss-free mapping ADR-0029 decision 1 requires, and the live loss-free
  test's own baseline. The source reads `%B`, the whole message, and the
  candidate lines come from `keyed_lines` over it, so no paragraph rule sits
  between an author's bytes and the parse.

  **The live corpus was unaffected, measured rather than assumed:** `%b` and
  `%B` return the same trailer-line count under every key ADR-0029 uses — 28 at
  `e39572c`, 55 and 9 at `4c4a784`, 386 at `266e6b6` (all measured 2026-09-02).
  The lines `%B` has and `%b` lacks are exactly the first paragraph's, so equal
  counts are what says no commit in that range carries a keyed line inside its
  subject paragraph. Two bounds on the population are stated rather than hidden: a
  message whose separators are lone `CR` bytes is a single line, so at most its
  first line is a candidate — an unkeyed first line means no finding, a keyed
  first line makes the CR-joined remainder (further trailers, a sign-off) that one
  finding's opaque text, never further findings (#404 R1-4) — and a subject that
  is itself a keyed line is a finding like any other. This is a different
  mechanism from ADR-0029 Amendment 1's D2, which refuses a trailer *value*
  spanning two lines and is unchanged.

### Documentation

- **Claims about what reads a watched file are now swept by object rather than
  by phrasing, and the sweep is runnable**
  ([#199](https://github.com/theurian/theurian/issues/199),
  [#501](https://github.com/theurian/theurian/pull/501)). Five sweeps in a row
  keyed on how the claim was *worded* and each missed a live member: the
  wheel-shipped schema root above was missed even by the corrected key written
  to catch its siblings, and a plugin doc dropped `src/` from the sentence
  entirely and named the file by pronoun. `tools/audit/` — a repository tool, not
  part of the distribution — inverts the key: it enumerates the *objects* a claim
  can be made about (the schema's key surface by JSON parse, `ProjectPaths`'
  file surface by introspection, and the `.theurian/` paths governed prose
  names), then asks of each what negated-liveness sentences refer to it.

  **Object-keyed is not markup-free, and this round measured the difference.**
  The object still has to be *spelled*, and both keys for this class admitted a
  single optional delimiter character — so a false universal written in the RST
  double-backtick form, the house style in **323 of this tree's 352** governed
  `.py` files measured at `5d0b1d9`, a commit on the branch of
  [#501](https://github.com/theurian/theurian/pull/501) and not on `main`,
  passed the census, passed the prose pin, and shipped inside a
  wheel when it was planted in `application/forest_builder.py`. That is a dated
  figure, not a standing one, and the derivation is beside the key it justifies
  in `tools/audit/config_object_claims.py`. The escape was in the
  markup, which is the one thing an object-keyed sweep is supposed to be immune
  to. Both delimiter classes are runs of `{0,2}` now, the schema's bare-leaf
  reference keeping a required `{1,2}` so widening a key does not quietly widen
  it to zero, and every plant was re-demonstrated against the widened key: single
  backtick, bare path, double quote and the house style all reach exit 1.

  **Widening the run was not the answer, and the review round is how that was
  found out.** Any delimiter run that is spelled is a run some wrapper sits
  outside of: the same claim with its path wrapped in bold — in a wheel-shipped
  module, outside the prose pin's surfaces — passed the widened key, all five
  audits and the whole suite. So the census stopped matching on emphasis.
  `claim_surfaces.without_emphasis` is applied at one seam, `as_read`, which the
  pre-filter, the three claim keys, the reference keys, the record markers and
  the ledger fragments all run against, so a form that defeats one cannot defeat
  only one. Measured by setting that seam to the identity — which is the census
  as it stood before — five emphasis forms move `no match` → `SUSPECT`: the path
  in bold, in italic, in underscores, and the verb in bold or italic. A sixth,
  emphasis on the negation, was already caught, and ADR-0028's live house style
  — a bold path in a sentence whose negation is about something else — stays
  `no match`, so the five were not bought with a false positive on a true
  sentence.

  **"Emphasis" meant the CommonMark spellings only, and round three reopened the
  escape by typing four characters.** `<b>` renders as `**` and was invisible to
  the strip, so ``Nothing in `src/` reads <b>`.theurian/config.yaml`</b>``
  planted in a wheel-shipped module left the census, the five audits and the
  whole suite green — in the one markup this repository already writes by hand.
  The criterion the strip already rested on settles which tags join it: a form
  **invisible in the rendered sentence**, so that a reader cannot tell the two
  apart. `<b>`, `<i>`, `<em>` and `<strong>` are stripped beside the asterisk
  and underscore runs, case-insensitively; `<code>` and `<summary>` are not,
  because a reader sees them and a key can be widened to spell them.

  **Round four found that fix written as four literal strings, and a tag is
  carried by its spelling.** `<b class="x">`, `<b >` and `<strong id="a">`
  render exactly as the bare forms do, and each was measured escaping the
  literal pattern. The tag name is now followed by a word boundary and then
  whatever the tag carries up to its `>`, which admits the attribute and
  whitespace spellings and keeps `<br>`, `<img>` and `<blockquote>` outside on
  the boundary. Both directions are driven over a table of spellings in
  `tests/unit/audit/test_emphasis_strip_spellings.py` rather than asserted in a
  docstring.

  **What the strip reaches is a list, and the list has a run table beside it.**
  Six render-adjacent tags sit outside it — `<span>`, as invisible as `<b>` and
  simply not one of the four, and `<ins>`, `<mark>`, `<u>`, `<s>` and `<small>`,
  which a reader can see but which wrap a claim just as well. Each is now a row
  in `MEASURED_ESCAPES` beside the four composition forms already there: a path
  written as a Markdown link, a path in a three-backtick run, JSON's `\/` escape
  (which every parser undoes and this module's JSON reader does not), and an
  `e.g.` that puts a negation and its object in different sentences. Ten rows,
  and the table **runs** — a row whose reach changes fails the audit rather than
  rotting in a sentence. Adding six more tag names to the strip instead would
  have been the move that produced bold, then `<b>`, then these; all ten rows
  are [#512](https://github.com/theurian/theurian/issues/512)'s, whose north
  star — normalise to rendered text, then match — is the terminal form that ends
  the enumeration rather than extending it.

  **The A/B this entry reported as "moves nothing" moves one row, and round four
  measured it.** At `5074d08`, a commit on the branch of
  [#501](https://github.com/theurian/theurian/pull/501) and not on `main`,
  running the census twice with the emphasis class as its only difference gives
  **57 rows with the four tags stripped and 56 without**, on both sides 19
  suspects, (0, 0, 0) ledger drift and 124 watched objects. The
  symmetric difference over the produced rows is **one row, not zero**: this
  entry's own paragraph above, which quotes the round-three `<b>` plant and so
  made the changelog entry describing the fix a member of the census it
  describes. It classifies `record (past tense)`, which is why no suspect and no
  ledger direction moves with it. The plant still flips `no match` → `SUSPECT`
  and ADR-0028's house style stays `no match` either way.

  **Widening the delimiter class reaches two of the four composition rows, and
  that was measured rather than guessed** — as shipped against `{0,3}` plus `[`
  and `]`, at the same commit, both sides give **57** rows, 19 suspects and
  (0, 0, 0) ledger drift,
  with the link and the three-backtick run moving to `SUSPECT` while the JSON and
  `e.g.` rows do not move. The widening is not taken: a delimiter class widened
  to reach two more spellings is the enumeration this module exists to stop
  doing, it is a second mechanism in a round closed on the first, and it would
  leave the rest of the recorded escape space open regardless.

  Two further properties, both measured. The sweep reads **wrap-joined
  sentences**, not lines: at `75c4c7a`, a commit on the branch of
  [#501](https://github.com/theurian/theurian/pull/501) and not on `main`, the
  same key over the same scope returns 19 lines and 31
  sentences, and the 19 lines land inside 17 of those sentences — so **14** are
  ones a line pass never returns at all. And every run demonstrates its key
  against a **planted positive** before any zero is read, because a key that has
  stopped matching
  reports exactly what a clean tree reports.

  Each of the four ledgers is exact in both directions — a suspect no row covers
  and a row the sweep no longer produces are both exit 1 — and that is the
  **minimum** every ledger here meets rather than the whole of what any of them
  does. Three further directions were each added because a real member walked
  past the two above it: **ambiguous**, one row covering two produced members,
  which a substring fragment absorbs and only a cardinality check sees;
  **verdict drift**, a row recorded as retracted that comes back a suspect; and
  an **occurrence count**, a second anchor added to a place already judged. So
  `owner_position_cites` reconciles in four directions and the other three in
  three each — **4/3/3/3**. This paragraph said `controls_discharge` reconciled
  in *two* until round three: it counted the arity of `ledger_drift`, which
  returns the two ledger-keyed directions, and `controls_discharge`'s first
  direction is spelled **undischarged** — a member naming no `src/` symbol, no
  test, no open owner and no `PROSE_ONLY` row — and is computed in `_report`
  instead, where an arity key cannot see it. It is the same direction its
  siblings spell *unrecorded*, with the same exit status: `_report` returns 1 on
  `undischarged or dead or unknown or stale or ambiguous`. Each *direction* is
  driven by a control rather than asserted, which is what makes a correction and
  its record land in one commit instead of the record rotting behind the fix.

  **And the suite runs the audits now, which it did not.** Committing five
  instruments that nothing invokes leaves them free to rot: mutations reverting
  the round-one fixes survived the full suite, and emptying a control table was
  green. A wrapper in `tests/integration/audit/` subprocess-runs all five plus
  `--positive-control` and asserts each exits 0. Running the control mode is not
  sufficient on its own — a table emptied to `()` reports zero failures among
  the rows it has, so its own audit exits 0 — and the same module therefore
  reads the tables structurally out of the source: every module-level
  `*_CONTROLS` name must bind a non-empty tuple, `POSITIVE_CONTROLS` is required
  of every audit, and `LEDGER_CONTROLS` of every audit that defines a ledger
  runner, derived from the source rather than from a list of four names kept in
  step by hand.

  **A structural read is not a bound on how much runs, and round three priced
  the difference at five one-line edits.** Reading a table as *bound and
  non-empty* says nothing about the rows a loop reaches, so each audit now
  counts the rows its loops **execute** — never `len(TABLE)`, which none of
  those edits moves — and prints one `CONTROL-TALLY` line per table through a
  single seam in `claim_surfaces`. The guard pins those counts per audit in
  `CONTROL_TALLIES`, reads the control call graph transitively from
  `_run_positive_controls` so a runner nobody reaches is RED rather than silent,
  and holds its own keys to the rule it imposes. Measured against the guard as
  it stood before those changes and as it stands now, each edit applied on its
  own to `config_object_claims.py` in a throwaway checkout:

  | The one-line edit | Guard before | Guard now | What fails now |
  | :-- | :-- | :-- | :-- |
  | a control runner opens with `return 0` | green | **RED** | pinned tally |
  | the `LEDGER_CONTROLS` loop iterates `()` | green | **RED** | pinned tally |
  | the `MEASURED_ESCAPES` loop iterates `()` | green | **RED** | pinned tally |
  | `POSITIVE_CONTROLS` sliced to `[:1]` | green | **RED** | pinned tally |
  | the guard's own required-table set emptied to `frozenset()` | green | **RED** | the guard's own keys |
  | the call to a runner removed, table and runner kept | green | **RED** | reachability, and the tally |
  | the table, its runner *and* the call deleted together | green | **RED** | the tally line stops being printed |
  | *control:* `POSITIVE_CONTROLS` emptied to `()` | **RED** | **RED** | structural (the check that already held) |

  **So the reach this entry previously claimed is now wrong in the good
  direction.** It said deleting a table *and* its runner *and* the call to it
  was not caught, "because nothing is left to be inconsistent with"; a pinned
  figure fails on that too, since the tally line simply stops being printed —
  the last row above. The audit's own `--positive-control` still exits 0 under
  both deletion edits, so the guard is what catches them.

  **Round four then found the guard reading one of the three signals a failing
  control row produces.** A row that disagrees with its key is printed as
  `FAIL <label>`, counted into the `failed=` half of its table's `CONTROL-TALLY`
  line, and folded into the exit code — and the guard read the exit code. One
  token separates it from the truth, and the two edits launder different halves:

  | The edit, applied with a control row genuinely failing | Audit `--pc` | Guard before | Guard now |
  | :-- | :-- | :-- | :-- |
  | `return 1 if failures else 0` → `return 0` in the escape runner | exit 0, `failed=1`, one `FAIL` row printed | green | **RED** |
  | `failures += status == "FAIL"` → `failures += 0` in the escape runner | exit 0, `failed=0`, one `FAIL` row printed | green | **RED** |
  | `(1 if failures else 0) \|` → `0 \|` in `_run_positive_controls` | exit 0, `failed=21`, 21 `FAIL` rows printed | green | **RED** |

  The guard now asserts both printed signals, and neither is derived from the
  other: the first edit is caught by the `failed=` figure and the second is not,
  and the `FAIL` row line is what catches the second. Getting past both takes
  two edits rather than one.

  **What the guard still does not reach, named rather than left implicit.** Two
  edits pass every check here, and both were run against the guard as it now
  stands. A control row that keeps its place in the count while it stops
  asserting anything — a row duplicated, or its expected value edited to match
  what the code does — is what review is for. And a loop that sets
  `ran = len(TABLE)` and then iterates a slice of it prints `ran=21` from a loop
  that executed none: the point of the tally is that the number comes from the
  loop, this edit produces it elsewhere, and comparing it back to the table's own
  length cannot separate the two. Measured green under the guard before and
  after; catching it wants a line per executed row, which is
  [#512](https://github.com/theurian/theurian/issues/512)'s. Full populations,
  keys and mutation controls:
  [`docs/work-logs/2026-09-02-199-unit-b-census.md`](../../docs/work-logs/2026-09-02-199-unit-b-census.md).

- **ADR-0008's two dated measurement anchors say which pull requests they belong
  to** ([#463](https://github.com/theurian/theurian/issues/463),
  [#501](https://github.com/theurian/theurian/pull/501)). Two amendment blocks
  anchored a measured line count to `4bfec1d` and `1cc2fa8`. Both are real
  commits on branches that were squash-merged, so each anchor resolved for
  whoever wrote it and for nobody who cloned the repository. Repointing was
  tested before re-tensing and is not available: each names a mid-branch working
  tree, and no commit reachable from `main` carries either tree. So each sentence
  now names the pull request the work landed as — `56582b2`
  ([#142](https://github.com/theurian/theurian/pull/142)) and `d1e79b1`
  ([#145](https://github.com/theurian/theurian/pull/145)) — and says the measured
  tree is not preserved, which is the honest form and the one the anchor audit
  above accepts. **Ten further unreachable anchors remain inside the audit's own
  scope** — its `GOVERNED_ROOTS` is `("docs/",)` — in ADR-0007, ADR-0024,
  ADR-0027 and the threat model. Ten is what this instrument can see and not
  #463's whole population: the members outside `docs/` were measured and filed on
  the issue rather than absorbed here, and they put the cross-scope remainder at
  ≥14 distinct tokens beyond the ten — the
  [`packages/` extension](https://github.com/theurian/theurian/issues/463#issuecomment-5507928206)
  and the
  [two CHANGELOG members](https://github.com/theurian/theurian/issues/463#issuecomment-5509455089),
  each hand-classified there. It is a **lower** bound and stays one on purpose.
  Run the same key unclassified over `packages/` and it returns **77** unqualified
  unreachable tokens on this entry's own tree, against **71** at `141cf6f` — the
  branch point on `main` — so the figure moves with the tree and is derived here
  rather than quoted (measured 2026-09-03):

  ```sh
  uv run --frozen python - <<'PY'
  import sys; sys.path.insert(0, "tools/audit")
  import sha_anchors as s
  root = s.repo_root()
  reference = s.main_reference(root)
  s.GOVERNED_ROOTS = ("packages/",)   # the widening #463 owns
  unqualified = {a.token for a in s.anchors(root) if not s._qualified(a)}
  loose = sorted(t for t in unqualified if not s._is_ancestor(root, t, reference))
  print(len(loose), sum(1 for t in loose if s._resolves(root, t)))
  PY
  # 77 55
  ```

  **55 of the 77 resolve to a commit object in a full clone and 22 do not**, so
  the population is not mostly fixture literals — the sentence this replaces said
  it was, on a count that reproduced at neither tree. `deadbeef`, `abcdef1` and a
  22-character run of `b`s are three of the 22; the 55 are branch and
  squashed-away commits that a reader still cannot reach. Telling a cited anchor
  from a fixture literal is the hand work `GOVERNED_ROOTS` being `("docs/",)`
  defers. #463 owns both halves; widening `GOVERNED_ROOTS` is its ratchet.

- **ADR-0029 no longer records the four findings-pipeline residuals as open, and
  its trailer census is keyed on `%B`**
  ([#404](https://github.com/theurian/theurian/issues/404),
  [#405](https://github.com/theurian/theurian/issues/405),
  [#406](https://github.com/theurian/theurian/issues/406),
  [#410](https://github.com/theurian/theurian/issues/410),
  [#492](https://github.com/theurian/theurian/pull/492)). The slice-2 note's
  residuals paragraph said in the present tense that the write is not atomic,
  that `committed_at` is not chronological, that `PARSER_STAMP` reaches no
  mechanic, and that `%b` can drop a folded trailer. All four are false as of
  this release.

  The paragraph is **kept as written and marked**, not edited: it records what
  slice-2 shipped and what each fix had to answer, and a landing note beneath it
  states per residual what closed, the test that holds it, and what each
  measured — including that `FINDINGS_SCHEMA_VERSION` moved 1 → 2 for the
  `committed_at` encoding, and that #406's behaviour section carries a stated
  residual (a widening no probe distinguishes leaves the stamp still). No
  decision changes, so this is a landing note rather than an amendment: each
  residual was a gap between a decision and its implementation.

  **The seven `%b` census cites are re-anchored to `%B`** — the population is
  the whole message since #410 — **with no figure restated.** The two keys were
  compared at every commit the ADR names and agree everywhere (28 at `e39572c`,
  55 at `4c4a784`, 9 for the `code` alias at `4c4a784`, 386 at `266e6b6`;
  measured 2026-09-02), and that table is now in the ADR's *Re-anchored census*
  beside the figures it protects. What equal counts mean is stated rather than
  left as a coincidence: the lines `%B` has and `%b` lacks are exactly the first
  paragraph's, so agreement says no commit in the measured range carries a keyed
  line inside its subject paragraph.

  **A second stale claim in the same section is corrected while it was open.**
  The *Re-anchored census* said the `SEPARATOR` docstring in
  `domain/review_finding.py` "carries a third, intermediate figure — 38 lines"
  and that correcting it to a commit-anchored form was the parser lane's owed
  work. The parser lane had already taken it: the comment reads "55 lines across
  7 commits ... measured 2026-08-26 on `origin/main` @ `4c4a784`". Measured
  2026-09-02, `git grep -n '38 lines' -- packages/theurian-core/src tools tests`
  returns nothing, against a `55 lines` positive control that hits that same
  docstring. The key is source-only on purpose: a `packages/ tools/ tests/` sweep
  scans this changelog too, where "38 lines" is named to explain the correction,
  so that key reports its own explanatory prose as a live occurrence — the record
  falsifying itself, which is exactly why the operative key is scoped to source
  (#404 R1-7). No count of the broad key is stated here, because any number would
  count the sentence that states it. The owed item is now recorded as discharged
  rather than left open.

  **Both halves are pinned, in the same pull request that made the correction.**
  `test_adr_0029_claims.py` is the record — the ADR-0018 and ADR-0027 shape,
  which this ADR had no equivalent of until now. The prose half reads the two
  records this bullet and the landing note live in, and holds the closure
  structurally rather than by wording, because the residuals paragraph is kept as
  written: the marker must be the paragraph *directly* above it, and the landing
  note must sit below, each matching exactly one paragraph so a rewritten anchor
  fails as a count instead of silently scanning nothing. The fact half derives
  from the live contracts the correction leaned on
  (`FINDINGS_SCHEMA_VERSION`, `trailer_source._FORMAT`, `_STAMP_PROBES`,
  `SqliteReviewFindingStore.building_path`, `FindingsBuilder`'s `write_section`),
  so a mechanism that moves and leaves these records behind goes RED — a bump to
  schema 3 reddens both spellings of "1 → 2", and the two REDs are separate tests
  with separate messages, because the CHANGELOG's would be a false release note
  while the ADR's is a decision to append the next move.

  **What the pin does not reach, stated because a pin that catches less than the
  sentence claims is the same defect one level up.** Its scope is those two
  records and no third, so the *Re-anchored census* is outside it: neither the
  `%b`/`%B` equivalence table nor the superseded "38 lines" claim above has a
  fact-side pin. The recorded reason is that both are measurements against named
  commits and a named command, which is the one form of a written number this
  file set accepts without one — the ADR carries each with its command and, for
  "38 lines", its positive control. The module's own docstring states this reach.

- **The records now carry the purged-build ground truth: T-17 and T-17a are
  updated, ADR-0024 point 4 is corrected against measurement, the NFR-4 record is
  reconciled, and the flat purged columns are pinned**
  ([#472](https://github.com/theurian/theurian/issues/472),
  [#445](https://github.com/theurian/theurian/issues/445),
  [#140](https://github.com/theurian/theurian/issues/140)). The re-measurement
  below landed as a work log and edited nothing; this is the pass that moves the
  records to match it.

  **T-17's discharge note said no figure had been re-run against a purged build,
  and that is now discharged.** Each of the four figure records it covers carries
  a dated annotation naming its own measured pair, so the history is annotated
  rather than rewritten: the pass-count edge (stale +5,116.7 µs at 51 withheld,
  purged flat at one pass), the canonical-read table and its rate (24.3 µs per
  withheld row stale, none purged), the +90 ms residual that was never a
  measurement (+14% re-derives to +0%), and the peak-memory sweep (stale 80.6 →
  8,244.4 KB, purged 80.6 → 84.9 KB). F5's 29.17 ms is recorded as **not
  re-runnable** — its subject was deleted with
  [#16](https://github.com/theurian/theurian/issues/16) — with what the cache
  stood in front of measured in its place.

  **T-17a's [#344](https://github.com/theurian/theurian/issues/344) byte-residue
  record stated no quantity at all**, and this entry said it "called the residue
  a fixed overhang" until PR #498's round-one review corrected that claim about
  what the record said. The quantity is what is new: the purged file's size is a
  monotone function of the pre-purge corpus (282,624 B and 7 free pages at
  nothing withdrawn, 9,715,712 B and 587 at 5,950), so the *file's size* carries
  the withdrawn count to anyone who can `stat` it.

  **And the residue is not where that record placed it, nor is it only a disk
  surface.** Three quarters of the growth is live rather than free-list — 587
  free pages of 2,372 is 25%. FTS5's `'delete'` writes a **tombstone**: the
  postings stay in the segment structure until a merge, and nothing in the
  shipped purge merges, so a purged build carries 151× a never-held build's
  trigram postings for rows it no longer serves and `optimize` takes it from
  8,564,736 B to 241,664 B (the source table records `optimize`; a `VACUUM`
  applied in the reproduction lands at the same figure). **That reaches a caller as a
  duration** — 16.8 ms against 1.2 ms isolated at 5,950 withdrawn, and **+27.4
  ms** end to end there (the round-one measurement, +27.36 ms; six later re-runs
  give +27.59…+28.18 ms, so the delta is stable across runs while the ratio
  moves with its denominator at 5.08–5.67×, median 5.41×), crossing the 1.40 ms
  noise floor between 500 and 1,000 withdrawn rows, with a five-point
  calibration reading the withdrawn count off the clock at 3
  of 5. Content is not recovered: responses stay byte-identical, because
  `'delete'` *does* decrement the averages record even while tombstoning the
  postings. Recorded as a new **face of T-17a**, named by the root cause that
  entry already carries — *the index still holds the withdrawn rows*, surviving
  at the FTS5 segment level — and owned by
  [#499](https://github.com/theurian/theurian/issues/499), whose closure is the
  merge or a recorded acceptance carrying the measured bound.

  **So "the purge removes the term the quantities are functions of" is scoped**
  rather than left as a universal: it holds for the canonical-read count, the
  retriever pass count and the `tracemalloc` peak — the three quantities measured
  and pinned — and is false for query duration on the trigram path, where the
  purge only shrinks the term. The two results do not conflict; they are
  different instruments, this re-measurement's below the trigram floor and
  #499's above it. **Read the re-measurement entry in the `0.1.0.dev17` section
  below under that scope**: it states the same sentence unqualified, and it is
  left standing as what the re-measurement reported at the time — it shipped in
  that release — rather than edited after the fact.

  **The flat columns are pinned** in
  `packages/theurian-core/tests/integration/test_purged_build_quantities.py`,
  over withheld counts 0/50/200 for the read count and the peak and 49–52 for
  the pass count. Each asserts its stale control first — exactly derived values
  for the read and pass counts, strict increase for the peak, whose own evidence
  grade says no absolute `tracemalloc` figure is quotable — and each half was
  taken RED by mutation, so a change putting the withheld term back goes RED
  there rather than being rediscovered by a fourth review round.

  **Ten satellite sites cited a pre-purge figure as a current cost**, and each
  now says which build its figure belongs to. Two carried a claim rather than a
  number — `application/visibility.py` and
  `tests/unit/test_result_gate_session.py` both asserted the cost is linear in
  the withheld count with no threshold in it — and on a purged build there is no
  line left to be linear, so the linearity is scoped to a build that still holds
  the withdrawn rows and the purged behaviour is stated beside it. **Why the term
  is absent is branch-dependent**, and five sites stated it as one unconditional
  mechanism until the review: the purged `|ranking|` equals the visible count on
  the scan below the trigram floor, which carries no `LIMIT`, while on the
  truncating branches it is `depth` whatever was withheld, before and after the
  purge alike — which this PR's own pass-count pin measures at 200 visible rows
  and 100 canonical reads.

  **ADR-0024 decision 4 made three claims; one holds and two were false when they
  were written.** "Publishing takes the index write lock" has no referent — the
  pointer swap is a write-to-temp plus `os.replace`, the lock file is never
  created, and the purge says so in its own source. "There is exactly one such
  interface" is false: eleven writable opens of an index file across two modules,
  seven of them public, with no common gate. "Nothing outside that interface
  opens an index file for writing" is the clause that holds, with its
  measurement. The point is narrowed in place with the retracted text quoted, and
  the header line that said this ADR **discharges** the index half of ADR-0018's
  single-writer debt now says it **narrows** it — a published build is never
  written, which is a property of when writes happen and not of how many
  interfaces perform them. That never-written property belongs to decisions 1 and
  2 plus the naming discipline rather than to point 4, and is held by three
  refusal tests plus the byte-for-byte untouched-build test; attributing it to
  point 4 was this entry's own error, corrected in the same review. ADR-0018's
  own blue/green note, which predicted that point 4 "is what discharges this
  bullet when it lands", now carries a dated correction saying it landed and does
  not. Decision 7's "pinned over every read method" is likewise stated as what it
  is: **seven of the eleven** public read methods that raise on a missing file,
  with the four unpinned ones named and each verified to raise and create no
  file. The contract stays owed under
  [#439](https://github.com/theurian/theurian/issues/439).

  **Six records state NFR-4's discharge status and five of them disagreed with
  the sixth; they now agree.** Settled
  against decision 7's own acceptance module,
  `tests/integration/test_gc_during_a_search.py`, read rather than re-run:
  ADR-0024's "discharged by points 6 and 7 together" is the direction that
  survives, and ADR-0018's Compliance bullet, ADR-0022's Still-owed opener,
  ADR-0007's in-progress-build bullet, `indexing/__init__.py` and
  `infrastructure/sqlite/store.py` now carry dated corrections agreeing with it.
  Six and five is the pair to quote: earlier drafts of this reconciliation said
  "four different ways", "three other records" and "all four records", none of
  which agreed with each other or with the corrected set. The settlement is split by clause rather than
  granted whole: **zero read downtime is discharged** — it is the clause NFR-4
  was recorded unmet for, and points 6 and 7 close it with pins including a
  no-session counterexample — while **"while a new build runs" is true by
  construction with every element pinned and no test issuing a query during a
  build**. So the mechanism is discharged and one acceptance test is owed. That
  residue is ADR-0007's own Still-owed bullet, whose "(Milestone 6)" owner was
  dead; it is now owned by
  [#497](https://github.com/theurian/theurian/issues/497), whose definition of
  done requires the records that state the gap to move in the same pull request
  the test lands in, because each becomes false the moment it exists. **Scoped to
  this branch's files that population is seven** — ADR-0007, ADR-0018, ADR-0022,
  ADR-0024, `indexing/__init__.py`, `infrastructure/sqlite/store.py` and this
  entry — **and repo-wide the same key returns eight**, the eighth being
  ADR-0007's dogfood-corpus twin, which is served content re-seeded from its ADR
  rather than edited in place and belongs to the M7 lane
  ([#317](https://github.com/theurian/theurian/issues/317) is its drift checker).
  The absence behind the gap was re-swept with a widened key covering the
  `asyncio` forms the first one could not see, and the still-zero result was
  confirmed against a planted positive control. **This entry is in that
  seven-file population and not in the six-record one**, and the two keys are
  why: the gap population is every record *stating* that no test issues a query
  while a build runs, which this entry does; the discharge-status population is
  every record carrying the dated `#140 member 1` correction marker, which this
  entry does not — it states NFR-4's status in its own words as release prose
  rather than correcting an ADR. This also answers what the `store.py` NFR-4
  correction in the `0.1.0.dev17` section below left open.

## [0.1.0.dev17] - 2026-09-02

### Fixed

- **A refusal keeps reaching its MCP caller with its own message under mcp
  2.1 and later, exactly as it did under 2.0**
  ([#469](https://github.com/theurian/theurian/issues/469),
  [#491](https://github.com/theurian/theurian/issues/491)). mcp 2.1.0
  (upstream PR #3314, listed there as a behaviour change) split the tool
  dispatcher's `except Exception` arm: `str(exc)` is forwarded only for
  exceptions that *are* the SDK's own `ToolError` or `ResourceError`, and
  everything else is treated as a crash — logged with its traceback
  server-side, answered with a bare `Error executing tool <name>`. Every
  refusal this daemon raises fell into the crash arm at once, so an agent
  that should have been told to re-register a project or to rebuild an
  unreadable state database would have read the tool's name and nothing
  else. Measured on [#460](https://github.com/theurian/theurian/pull/460)'s
  `mcp` 2.0.0 → 2.1.1 bump: 44 assertions on message text went RED.

  **Two causes, not one.** `mcp/tools.py`'s `ToolError` carried the SDK's
  *name* without its identity — it shadowed
  `mcp.server.mcpserver.exceptions.ToolError` and never subclassed it. It
  now declares both bases, `TheurianError` first so `remedy` and every
  `except TheurianError` clause are untouched; that closes 41 of the 44. The
  other 3 were a different root cause (#491): `TheurianError` subclasses
  raised *below* that module — `SchemaVersionMismatchError` and
  `StateDatabaseUnreadableError` from `infrastructure/sqlite/connection.py`
  — travel up through a tool body that never converts them, so they were
  never the SDK's `ToolError` either. Those two are the measured reachable
  set: instrumenting the conversion seam and driving nine MCP integration
  files, exactly two types reach it — `StateDatabaseUnreadableError` 192
  times and `SchemaVersionMismatchError` 6. Conversion is now one seam
  rather than five tool bodies: `_tool` replaces the five `@server.tool`
  registrations and turns an escaping `TheurianError` into
  `ToolError(str(exc))`, so "a refusal raised below the surface still
  reaches its caller" is a property of the surface. Two tests hold that,
  and the division matters: a source scan catches a *decorator* that names
  something other than the seam, and a runtime check asks the built server
  whether each registered tool *is* the forwarding seam — identified by the
  code object every one of its wrappers shares, not by the presence of a
  `functools.wraps` marker, which a lookalike wrapper also carries. Only the
  second can fail when the seam is bypassed from *inside* the registration
  helper — the first reads spelling, and adversarial review demonstrated
  bypasses (a deleted application, then a `wraps` lookalike) that left every
  decorator identical and the whole suite green.

  **Nothing a caller reads moves.** The seam forwards the refusal's own text
  and adds nothing to it — not `remedy`, not a path, not the class name, not
  the traceback or the exception chain. `str(exc)` is exactly what mcp
  2.0.0's blanket arm folded in, and widening an error while restoring it is
  how a restoration becomes a disclosure (SEC-13); it is what keeps
  `StateDatabaseUnreadableError` naming the failing exception's *type* and
  never the corrupted cell. Measured with one probe against three trees: for
  all five refusal classes probed — the two reachable ones included — the
  wire text is byte-identical between this fix under 2.1.1, this fix under
  2.0.0, and the unfixed tree under 2.0.0. The 44 node ids that were RED
  under 2.1.1 pass.

  **The text of an exception that escapes a tool body stays withheld under
  mcp 2.1 and later unless it is a deliberate refusal, by design.** The seam
  catches `TheurianError` and nothing wider, so a `TypeError`, an `OSError`
  or a bare `sqlite3.Error` is left in upstream's crash arm — the one cell
  the same probe measured moving. In that probe each tool was *named* after
  the exception class it raises, so the tool name is what mcp interpolates
  and what the quotes below repeat: `Error executing tool TypeError: an
  internal call was made with the wrong arity` under 2.0.0 against `Error
  executing tool TypeError` under 2.1.1. Upstream's withholding is hardening
  this project agrees with, and catching `Exception` at the seam would
  defeat the change that surfaced the bug.

  The claim is scoped to exceptions that *escape a tool body*, because it is
  not true of the whole wire: the SDK refuses a malformed call before the
  body runs, with its own `ToolError` carrying pydantic's validation text —
  which echoes the caller's own argument back. Measured under both 2.0.0 and
  2.1.1, that text is byte-identical, so this change neither widens nor
  narrows it, and what it echoes is the caller's own input rather than
  anything read from a project.

  The seam only sees what is raised while it is on the stack, so a tool that
  returned before running its body would silently opt out. Registration now
  refuses a coroutine, async-generator or generator function, and a callable
  object whose `__call__` is one of those; a plain function that *returns* an
  awaitable cannot be recognised until it is called, so the wrapper refuses
  that at call time rather than letting the SDK serialise an un-awaited
  object as a successful result. No registered tool has any of these shapes.

  Under mcp 2.0.0 nothing user-visible changes either way: that dispatcher
  wraps the SDK's own `ToolError` exactly like anything else, so the added
  base is inert there, and the seam emits the text the blanket arm already
  folded in.

### Documentation

- **T-17's round-5/6/7 residual figures are re-run against a real purged build,
  and the ADR-0024 point-4 clauses #445 asks about are answered by measurement**
  ([#472](https://github.com/theurian/theurian/issues/472),
  [#445](https://github.com/theurian/theurian/issues/445),
  [#486](https://github.com/theurian/theurian/pull/486)). The threat model's
  T-17 discharge note records that every figure in those rounds was taken
  against a build that still held withdrawn rows, and that none had been re-run.
  [`docs/work-logs/2026-09-01-472-purged-build-re-measurement.md`](../../docs/work-logs/2026-09-01-472-purged-build-re-measurement.md)
  is that re-run, on a real canonical store and a real index purged through
  `SqliteIndexStore.derive_purged` — the call the withdrawal trigger makes —
  with the stale build reported beside the purged one in every table, so a
  harness that measures nothing cannot pass. #472 stays open for face A, and
  #445 stays open for the ADR-0024 reconciliation these answers feed.

  **The purge does not make these quantities smaller; it removes the term they
  are functions of.** At 5,990 withheld rows the canonical-read count goes
  6,000 → 10 and the gate 157.71 ms → 0.24 ms; at 5,950 the `tracemalloc` peak
  goes 8,244.4 KB → 84.9 KB; the pass-count edge at `FIRST_PASS_DEPTH` is
  crossed by the stale build from 51 withheld and never by the purged one; one
  scan over the corpus goes 85.10 ms → 1.21 ms, against 1.05 ms for a build that
  never held the rows; and T-17a's five BM25 collection statistics match the
  never-held build on all five where the stale build differs on all five. The
  4.3 KB step visible in the purged peak-memory column is isolated to the harness
  rather than explained away — retriever and gate measured separately are flat to
  0.1 KB across the whole sweep, nine repeats each. **F5's 29.17 ms has no
  purged-build counterpart and is recorded as not re-runnable**: its subject,
  `SqliteIndexStore._scan_cache`, was deleted in Milestone 6
  ([#16](https://github.com/theurian/theurian/issues/16)), so what the cache
  saved cannot be re-priced; what it stood in front of is measured instead.

  **What a purged build still carries** is the byte residue
  [#344](https://github.com/theurian/theurian/issues/344) already records, now
  quantified: a purge page-copies the published build and deletes from the copy,
  so the purged file's size and free-page count are a monotone function of the
  pre-purge corpus — 9.7 MB and 587 free pages to serve fifty rows. No query
  reads a free page and the retriever's peak is flat across a 34× change in file
  size, so the residue stays a disk-forensics surface rather than a query-side
  one; what is new is that it scales with what was withdrawn.

  **ADR-0024 point 4 is one true clause and two false ones.** No index write path
  takes a lock — the advisory lock guards the *state* databases, and the purge's
  publish half creates no lock file at all. There are eleven writable opens of an
  index file across `index_store.py` and `index_purge.py` with no common gate, so
  "exactly one such interface" holds only if *interface* means the port plus the
  module it delegates to, which is a layering statement and not the single-writer
  contract ADR-0018 point 1 defines. Nothing outside those two modules opens an
  index file for writing, and that is the clause that survives. The records
  themselves are not edited here; that is the follow-up pull request's scope.

  **The population of records to update is derived rather than guessed.** A key
  built from the work log's own F1–F9 reconstruction table — every figure those
  records publish in its Figure column, plus the roundings the records use —
  returns 48 lines at `ec0dbcd`, classified exhaustively into four buckets that
  sum to 48: 19 inside the T-17 entry, 15 lines over **ten** satellite sites that
  cite a round-5/6/7 figure as current, one shipped changelog line, and 13 hits
  on figures these records borrow rather than produce. Two of the ten —
  `application/visibility.py` and `tests/unit/test_result_gate_session.py` —
  carry a linearity claim as well as the figures, and the purged column's flat
  ten reads leave no line to be linear, so those two need the claim rewritten in
  addition to the numbers.

  Every figure is a measurement at one anchor (`ec0dbcd`, Apple M1 Max, CPython
  3.13.3, SQLite 3.47.1, 2026-09-01) and not an invariant. The work log grades
  itself the way it grades the records it re-measures: the harness was scratch
  and is not committed, so no table here is reproducible by a reader yet. The
  durable producer is the pins work in flight on
  `docs/472-purged-records-and-pins`, which turns the flat purged columns into
  tests under `packages/theurian-core/tests/integration/`.

- **Four more config-reader universals are narrowed, `store.py`'s retracted
  NFR-4 citation is corrected, and the dead `#15`/`#113` owner cites in the
  purge path and three ADRs are repointed or classified as history**
  ([#447](https://github.com/theurian/theurian/issues/447),
  [#454](https://github.com/theurian/theurian/issues/454),
  [#444](https://github.com/theurian/theurian/issues/444),
  [#464](https://github.com/theurian/theurian/issues/464),
  [#487](https://github.com/theurian/theurian/pull/487)). Four records that
  [#426](https://github.com/theurian/theurian/issues/426) and
  [#428](https://github.com/theurian/theurian/issues/428) had already corrected
  elsewhere still carried the retracted version, each in a file those sweeps did
  not reach.

  **Three "nothing in `src/` reads `.theurian/config.yaml`" universals** —
  `application/forest_builder.py`'s `SUMMARY_MAX_TOKENS` comment,
  `test_forest_derivation.py::test_the_option_defaults_are_the_config_schemas_own`
  and `test_schemas.py::test_the_raptor_forest_is_declared_off_by_default` — are
  narrowed to the `raptor` block, the population that still has no reader. The
  universal stopped being true when ADR-0027 decision 3 shipped
  `security/project_config.py::read_secret_scan_policy`, which reads
  `security.secretScan` and nothing else. No conclusion leaned on the file being
  unread — each leans on these keys being unread — so the premise is narrowed
  rather than the paragraph deleted.

  **Which half is pinned is stated rather than assumed.**
  `test_config_key_call_sites.py` holds the fact side, over `raptor` key
  spellings derived from `properties.raptor.properties` in the schema, so a
  loader that names one goes RED. The prose side of these three is **unpinned**,
  because `test_raptor_config_claims.py` is scoped to the three files #426
  corrected and these are not among them; both test docstrings now say so. So is
  the fact side's one measured gap: a read bound to `max_levels` or
  `min_children_per_summary` inside `application/forest_builder.py` adds no new
  `(module, spelling)` pair, since `ForestOptions` already owns both names there
  — mutation A1 SURVIVED for that reason where A2, the same read in another
  module, was KILLED.

  **`store.py`'s module docstring** said reads open their own WAL connection "so
  a search never blocks on a running rebuild (NFR-4, NFR-7)". NFR-4 is about the
  *retrieval index*, which has been a separate file since ADR-0022 and is
  republished by writing a new one and swapping a pointer — something no WAL
  connection spans. ADR-0018's Neutral consequence made the same mis-citation and
  its Milestone 5 amendment retracted it, but the retraction had not travelled
  here. The NFR-7 half is kept and stated as what this file settles: a
  `migrate apply` write does not block a reader of the state database. Whether
  ADR-0024 points 6 and 7 leave NFR-4 discharged is left open rather than
  answered — the ADR records still disagree, which is
  [#140](https://github.com/theurian/theurian/issues/140)'s first item.

  **The purge path's two owner cites** named things that cannot own work.
  `application/withdrawal_purge.py` handed the single index-writer interface
  ADR-0018 point 1 owes to "issue #15's follow-through", and no such tracker
  exists: #15 closed on 2026-08-10 (`66a43ae`) by shipping the withdrawal→purge
  trigger that function *is*, not the interface it writes without.
  `application/project_service.py` called a compare-and-swap pointer write
  "#113's scope", and #113 is the merged pull request that shipped the
  purge-is-a-build model the same day, so it holds no owed work. Both now name
  [#439](https://github.com/theurian/theurian/issues/439), which owns the derived
  index's single-writer contract. The other #113 cite at the head of that bullet
  names the same pull request as the *mechanism* the check rests on, which is
  history and is left alone.

  **The doc-side `#15` cites are classified per the closure-reason rule**, so
  only the one that pointed at unbuilt work moved. ADR-0022's Still-owed bullet
  headed "(Milestone 6, #15)" is repointed at #439 — Milestone 6 has passed, and
  #15 closed by wiring ADR-0024 decision 5, which is the *answer* recorded below
  that bullet and not the writer discipline the bullet asks for. Its measurement
  is pasted rather than summarised, and anchored: at `ec0dbcd`,
  `git grep -nE "flock|lockf|LOCK_EX|write_lock" -- packages/theurian-core/src`
  returned ten lines, every one the canonical `ProjectPaths.write_lock` or the
  daemon's single-instance lock and none in an index write path. That count is
  the anchor's, not today's — `266e6b6` (#478, merged during this release
  window) took the same lock across `migrate apply`'s critical section and the
  command returns 18 lines at this branch's head. The conclusion is unchanged,
  because every added line is that same state-database lock or prose about
  `flock` semantics, and none is an index write path. ADR-0008's
  "#15 removes those rows" is history: the actor is
  `application/withdrawal_purge.py`, and the sentence never named a live tracker
  — #15 closed at 19:45 +0900 and the sentence entered the file at 22:27 +0900
  the same day (`379e197`). ADR-0023's two future-tensed sentences are tensed
  against the shipped purge, each dated note naming its subject so the two are
  distinguishable when scanning: T-17a's duration face, and the canonical-read
  residual left by dropping the `LIMIT`.

  Prose, comments and docstrings only — `forest_builder.py`,
  `project_service.py`, `withdrawal_purge.py` and `store.py` are AST-identical to
  their parents once docstrings are stripped, checked with a positive control,
  and no behaviour changes. Carried: the three served corpus twins under
  `.theurian/knowledge/architecture/` — `raptor-forest`,
  `index-lives-in-its-own-database` and
  `trigram-index-beside-the-word-index` — hold the uncorrected sentences
  byte-identically and move only on a governed re-seed (#199 unit C mechanics),
  which follows in its own PR.

## [0.1.0.dev16] - 2026-09-02

### Fixed

- **Two concurrent `theurian migrate apply` invocations against a fresh
  project no longer race `create_database` and the pointer publish outside
  the advisory write lock**
  ([#468](https://github.com/theurian/theurian/issues/468)). Measured on
  eight real two-process runs (`theurian-adversarial-review`, PR #446 round
  2): the loser crashed in four, three distinct unhandled `sqlite3` errors,
  because `create_database` ran before the migration transaction opened and
  `write_active_state` ran after it committed, both unlocked, so `--json`
  requested a defined outcome and got a Rich traceback instead.

  `migrate apply` now holds one advisory lock across its whole critical
  section — the discard/create decision, `create_database`, the migration
  transaction, the provenance record and the pointer publish, in that order
  — rather than the migration content alone. A first version of this fix
  held the same lock in two separate acquire/release cycles instead of one,
  and review found a real gap in the window between them:
  `provenance.record_state` ran after the pointer publish and outside any
  lock, so a slower process racing a faster one could observe
  `has_state == False` for a database the faster process had already built
  and published, and the untrusted-state discard branch — meant for a
  doctored, committed `.theurian/state/` — deleted and rebuilt that live
  database out from under it (13/78 raced pairs measured both processes
  `databaseCreated: true`; one pair produced two winners). The shipped
  design holds one lock for the whole sequence instead, with the provenance
  record moved ahead of the pointer publish, so there is no window where
  `active.json` names a state hash the serve-side provenance gate has not
  yet been told about. Re-measured with the same two-process harness and a
  synthetic stagger sweep built to reproduce the two-winner shape directly:
  zero crashes, zero double-`databaseCreated`, zero two-winner pairs, across
  five runs of eight pairs each.

  ADR-0018 Decision point 2 and its Positive-consequence bullet — narrowed
  on 2026-08-31 to record the gap this closes — are updated to say so.

### Documentation

- **ADR-0027 and the SQLite write path no longer repeat ADR-0018's corrected
  lock clause or its retracted single-interface claim**
  ([#433](https://github.com/theurian/theurian/issues/433),
  [#434](https://github.com/theurian/theurian/issues/434),
  [#441](https://github.com/theurian/theurian/pull/441)). Two records carried
  copies of claims that [#432](https://github.com/theurian/theurian/pull/432)
  and ADR-0018's own Milestone 5 amendment had already retracted, and nothing
  tied a copy to its original.

  **ADR-0027's decision-2 residue** described the Milestone 1 mechanism as "an
  OS advisory file lock on the state database" while reasoning about the accept
  path's file moves. It is narrowed the way #432 narrowed ADR-0018 point 2 — the
  lock is taken on the separate `.theurian/runtime/write.lock` and guards the
  state databases under `.theurian/state/` — and the reasoning built on it is
  unchanged, because it never depended on which object was locked: the accept
  path's file moves are outside that lock either way.

  **`connection.py` and `store.py`** asserted that writes go through one
  interface. In `connection.py` the module docstring said so and
  `write_transaction`'s called itself "The only way to write", adding that
  "`CanonicalStore` exposes no connection"; `store.py` carried three more faces,
  the last of which — "there is no way to build one otherwise, so the
  single-writer guarantee cannot be sidestepped by reaching for this class" —
  was not merely overstated: `SqliteWriter(sqlite3.connect(":memory:"))` builds
  a writer from any connection, with no lock and no transaction. All five
  docstrings now state the mechanism that is real — `write_transaction` takes
  the advisory flock on `lock_path` and holds it for the transaction — and the
  guarantee as the amendment records it: held by convention at each call site,
  because the `CanonicalStore` port publishes its write methods directly. What
  the read/write split does buy is kept and made checkable rather than asserted:
  `SqliteCanonicalStore` publishes no write method and reads through
  `open_read_connection`, which passes `mode=ro`, so SQLite refuses a write
  issued on that connection. `write_transaction`'s `Raises:` now documents
  `WriteLockTimeoutError` beside `StateDatabaseUnreadableError`, and the
  ADR-0018 point 3 citation carries both of that point's mechanisms — the
  daemon-owned queue *and* the file lock a CLI invocation running alongside it
  still needs.

  **Both corrections are pinned in both directions**, so drifting back and the
  owed mechanism landing each turn a test RED: `test_adr_0027_claims.py` and
  `test_connection_claims.py` hold the prose, a shared
  `tests/write_lock_claims.py` derives the two paths once from the live
  `ProjectPaths` for every record that names them, and the port's shape is read
  live rather than restated — the day a single write interface lands, "held by
  convention" must move with it. One limit is stated rather than implied: the
  docstrings say two processes entering `write_transaction` serialise, which is
  inherited from `fcntl.flock` rather than measured here, since the one test
  that builds the lock directly runs two `WriteLock` objects in a single
  interpreter.

  Prose and docstrings only — `connection.py` and `store.py` are AST-identical
  to their parents once docstrings are stripped, and no behaviour changes.
  Residues owned: the single write interface ADR-0018 records as owed
  ([#439](https://github.com/theurian/theurian/issues/439)); ADR-0018's
  Milestone 5 amendment, whose count of the port's write methods the port never
  matched
  ([#446](https://github.com/theurian/theurian/pull/446)); and the served corpus
  twin under `.theurian/knowledge/architecture/`, which carries the retracted
  sentence byte-identically and moves only on a governed re-seed (#199 unit C) —
  the same carry #417 and #432 record.

- **The "nothing in `src/` reads `.theurian/config.yaml`" universals are narrowed
  to the key populations that are still unread, and pinned**
  ([#426](https://github.com/theurian/theurian/issues/426),
  [#448](https://github.com/theurian/theurian/pull/448)). ADR-0027 decision 3
  shipped `security/project_config.py::read_secret_scan_policy`, called from
  `application/proposal_service.py` at `theurian propose accept`. A sentence that
  had been true across several records stopped being true that day, and nothing
  went red. Each is narrowed to the population that still has no reader rather
  than deleted, and every conclusion that leaned on the retracted premise is
  re-derived rather than carried over.

  **The raptor family**: `docs/architecture/raptor.md`, and three sentences of
  ADR-0008 — decision 10's rationale, its "switch is the CLI flag" note, and the
  amendment to decision 3, the last found by the pin work after the first two
  were fixed. What is unread is the `raptor` block, not the file, and both
  conclusions hold on that narrower fact: the CLI flag is still the switch and
  not the config key, and an operator still cannot move
  `minChildrenPerSummary`. The rationale took a different repair from the other
  two — it is *tensed* rather than narrowed, since it describes the state as the
  decision was taken, and its own paragraph now says which of its clauses have
  moved since and which have not.

  **ADR-0024 decision 2** said nothing reads either `index_metadata` column back,
  so a copy inheriting its parent's identity was "latent rather than broken".
  That is true of `built_at` alone now: `SqliteIndexStore.add_nodes` selects
  `index_build_id` out of the file it is writing into, to stamp each summary node
  with the build it belongs to. The re-derivation is sharper than the retracted
  version rather than merely narrower — the first reader the decision predicted
  arrived *on the purge path itself*, because `purge_into` runs the forest
  recompute before `_restamp`, so a `--raptor` purge writes nodes carrying the
  parent's id and `_restamp`'s `UPDATE nodes` is what repairs them. The docstring
  of `test_index_purge.py::test_a_purged_build_names_itself_in_its_own_metadata`
  carries the same correction; its assertions are unchanged.

  **The sample project's allowlist annotation** carried two defects in one
  comment block: the file-level premise, and an owner that had closed.
  `providers.review.repositories` still has no reader, but the file it sits in
  does, so the annotation names the key rather than the file, and names the
  reader the file has beside it. SEC-10's allowlist is owed against the first
  external fetch path, which [#429](https://github.com/theurian/theurian/issues/429)
  owns — [#129](https://github.com/theurian/theurian/issues/129) closed on the
  wording rather than on the control, and this annotation had been left out of
  the repoint [#425](https://github.com/theurian/theurian/pull/425) made to the
  threat model, `docs/architecture/review-knowledge.md` and
  `infrastructure/github/`.

  **Every narrowed claim is held by something derived rather than restated, and
  in both directions.** The `raptor` keys join `test_config_key_call_sites.py`'s
  call-site watch with their spellings **derived from the schema** —
  `properties.raptor.properties` in
  `schemas/config/project-config.schema.json`, guarded by a population control
  that reddens if the block is renamed or emptied — so the day a loader names one
  of them, the enumeration reddens and these records must move with it.
  `test_index_metadata_claims.py` scans the imported package for a consumer of
  each `index_metadata` column, with the column population parsed out of
  `INDEX_DDL` rather than transcribed, so a column added to the table is watched
  from the moment it exists; the count the decision bounds its `built_at` claim
  with is held beside it, since `metadata()` fetches every column and the claim
  rests on neither of its two callers reading that one.
  `test_index_build_id_read_back.py` drives the read
  itself rather than asserting it: a file whose metadata row is missing is refused
  with the product's own error, and `add_nodes` is pinned to take no build-id
  argument, so the stamp cannot quietly become something a caller supplies.
  `test_raptor_config_claims.py` holds the prose — each narrowed sentence
  positively, plus a negative scan that reddens if the retracted wording returns —
  with the dated correction notes scanned rather than skipped: only what a
  retraction verb introduces is excised, inline code spans are neutralised first
  so the asterisks of a `SELECT *` cannot swallow a live sentence between them,
  and every scanned surface has a recorded note count so the exclusion cannot
  widen unnoticed. `test_examples.py`'s `ANNOTATED_KEYS` row pins the sample
  config's annotation in three parts: the reader the file has, the key that has
  none, and the live owner.

  Documentation and tests only — this change touches no file under
  `packages/theurian-core/src/`. Two limits are recorded in the modules rather
  than left implied. The column scan resolves only what sits between `SELECT` and
  `FROM`, so a `WHERE`, `ORDER BY`, `GROUP BY`, `HAVING` or `DELETE` keyed on a
  column is pinned as a deliberate miss: whether a filter or a delete key
  "consumes the value" is a question ADR-0024 decision 2 has not answered, and the
  shapes are recorded where the next reader sees the gap and the question
  together. The note exclusion's backtick pairing is an approximation, and an
  unbalanced backtick can leave a verb-led reassertion hidden — a false green,
  bounded on the shipped surfaces by
  `test_every_block_the_exclusion_runs_on_pairs_its_backticks`. Residues owned:
  the members of this class outside the surfaces these modules scan —
  [#447](https://github.com/theurian/theurian/issues/447) for the Python ones,
  [#455](https://github.com/theurian/theurian/issues/455) for the wheel-shipped
  root `description` in `schemas/config/project-config.schema.json`, and
  [#461](https://github.com/theurian/theurian/issues/461) for a Markdown one under
  `plugins/` — and the served corpus twins of ADR-0008 and ADR-0024 under
  `.theurian/knowledge/architecture/`, which carry the retracted sentences
  byte-identically and move only on a governed re-seed (#199 unit C), the same
  carry #417 and #432 record.

- **ADR-0018's owed single-writer work names a live owner, its write-method
  count matches the port, and the README states the NFS exclusion**
  ([#436](https://github.com/theurian/theurian/issues/436),
  [#417](https://github.com/theurian/theurian/issues/417),
  [#446](https://github.com/theurian/theurian/pull/446)). One document was
  pointing owed work at a closed issue, naming a count the port has never had,
  and keeping an operator-facing exclusion in records operators do not read.

  **The owner-cites.** ADR-0018 cited
  [#15](https://github.com/theurian/theurian/issues/15) three times as the
  Milestone-6 owner of owed work: the `CanonicalStore` single write interface,
  the Protocol-surface pin that waits on it, and the derived index's missing
  contract. #15 closed on 2026-08-10 (`66a43ae`) by wiring ADR-0024 decision
  5 — the withdrawal→purge trigger — which is none of those three, and Milestone
  6 is past. Each cite is classified by what that closure shipped and repointed
  at [#439](https://github.com/theurian/theurian/issues/439), filed for the work
  itself: the two Compliance cites are corrected in place, each keeping the
  retracted pointer inside the sentence that corrects it, while the Milestone-5
  amendment's sentence stays verbatim under a dated note, because that
  blockquote is a record of what was believed then. The index bullet now records
  what did land — `migrate apply` publishes a purged build through
  `application/withdrawal_purge.py`, so `theurian index build` is no longer the
  index's only writer — and what did not: no index write lock exists anywhere
  under `packages/theurian-core/src` (measured at `6b83be1`, unchanged at
  `c3886db`), which the purge states of itself in its own source.

  **The write-method count.** The Milestone-5 amendment said the port publishes
  "twelve write methods directly". It publishes thirteen, and the measurement
  refutes the obvious reading that the port grew one: counted by the key
  `test_connection_claims.py` uses — `CanonicalStore`'s public members that
  declare no return value — the port has published thirteen since `261eff3`
  (2026-08-01), the commit that introduced it, and still did at `f665ecf`
  (2026-08-07), the commit that wrote the amendment, with no revision of that
  file counting otherwise. So it is corrected in place under a dated note rather
  than left standing as a record that aged. This discharges the residue the
  entry above records, and corrects that entry's framing: the count never
  matched, rather than stopped matching.

  **The NFS exclusion, where an operator meets it.** ADR-0018 accepts the
  advisory lock's behaviour on network filesystems by putting a `.theurian/`
  directory on NFS outside the supported configuration, records that nothing
  detects that it is, and rejects rather than defers building a probe. README's
  quick start now carries one line of that, immediately after what `init`
  creates, citing ADR-0018 rather than re-asserting the absence on its own
  authority. It promises no detection in any tense.

  **The point-1 escape hatch, re-measured.** The Compliance bullet for point 1
  said that adding a `connection()` method to the port leaves the suite green
  and nothing notices. Two of the three spellings now fail:
  `-> sqlite3.Connection` under `test_connection_claims.py`'s port-shape pin,
  which reads it as a member returning a context manager, and the unannotated
  `def connection(self)` under
  `test_ports.py`'s annotation rule. `-> object` still slips through, and the
  bullet now records it as the residual. Every spelling was injected in a
  throwaway checkout with a control run first, and the two failures were re-run
  at `c3886db` — an ancestor on `main`, not a branch tip — so the anchor survives
  the squash. The Milestone-5 figures the bullet used to quote are dropped rather
  than refreshed, since nothing here re-measured that suite, and no new suite
  total is quoted in their place.

  **Point 2's serialisation claim is narrowed to the write transaction**
  ([#468](https://github.com/theurian/theurian/issues/468)). The Decision said
  two concurrent `theurian migrate apply` invocations serialise and the loser
  becomes a no-op, and the Positive consequence called two concurrent CLI
  invocations "already safe". Measured on eight real two-process runs, the loser
  crashed in four of them, because `create_database` runs before the write
  transaction opens and `write_active_state` publishes the pointer after it
  commits — both outside the lock, both completing while another process holds
  it. Point 2 now serialises the work inside the transaction and says so, the
  Positive bullet records what was false, and a narrowing blockquote carries the
  measurement and the control that the lock itself works. The record is narrowed
  rather than the design changed: #468 owns bringing both writes inside the lock,
  and stays open.

  Prose only, and no behaviour changes. What holds these claims, all in
  `test_adr_0018_claims.py`: the NFS sentence is pinned in both directions and
  README's copy of it is read in the same sweep, so neither record can drift
  alone; `test_both_corrected_compliance_bullets_name_the_live_owner_of_the_owed_work`
  and `test_neither_corrected_bullet_cites_the_closed_tracker_as_an_owner` hold
  the two repointed bullets — the first requires #439's link in each, the second
  refuses a #15 mention whose own sentence does not retract it;
  `test_no_index_write_path_module_takes_a_lock` sweeps the modules the published
  index is written through, so the index bullet's "no lock" half moves when a
  lock lands; and
  `test_the_amendment_spells_the_write_method_count_the_port_publishes` holds the
  count spelled in the amendment against the live port, RED whether the sentence
  drifts or the port gains a write method — so the number is not a copy anyone
  keeps in step by hand.
  The review rounds added four more, each closing a deletion or an edit measured
  green against the tree before it:
  `test_decision_point_2_says_what_its_serialisation_promise_does_not_cover`
  holds the boundary clause inside the point a reader who stops at the Decision
  actually reads, and
  `test_the_positive_consequence_records_that_already_safe_was_measured_false`
  holds the three fragments that keep that bullet a retraction rather than a
  statement of fact;
  `test_every_correction_note_still_carries_what_makes_it_a_correction` requires
  the content each correction blockquote turns on, so a note cannot be cut down
  to its anchor or rewritten around it to assert what it retracted; and
  `test_every_symbol_pointer_in_the_adr_resolves_to_something_live` reads every
  `module.py::symbol` the record hands a reader out of the live module, with the
  harvested set of those references held **equal** to the list — so a reference
  added to the ADR fails as loudly as one renamed out of the code.
  Residues owned: the engineering the repoints point
  at ([#439](https://github.com/theurian/theurian/issues/439), filed without a
  milestone), and the served corpus twin under
  `.theurian/knowledge/architecture/`, which still carries the dead cites and
  the old count and moves only on a governed re-seed (#199 unit C) — the same
  carry #417, #432 and #441 record.

- **Every issue cite in the threat model is classified, and the seven that named
  an owner unable to receive work are repointed or corrected**
  ([#427](https://github.com/theurian/theurian/issues/427),
  [#470](https://github.com/theurian/theurian/pull/470)). The #199 unit-A audit
  measured its escape space empty *of control-capability claims*. A different
  claim shape lived there and was never a stated verification axis: the
  **owner-cite** — "[#N] removes this face" — asserting that a tracked issue owns
  a residual. The container census misses one on a blockquote continuation line,
  and the verb sweep's list did not carry "removes".

  **The population, and one row per cite rather than per number.** Run at
  `5a9a1e5`: 114 cite occurrences over 56 distinct numbers on 110 lines, plus 97
  bare `#N` mentions outside the key. #15 alone is cited six times and each cite
  is classified on its own context, because the classification is a property of
  the surrounding prose and not of the number. The whole table is committed —
  `docs/work-logs/2026-08-31-427-owner-cite-sweep.md` — with the rule each label
  is earned by: 88 history, 20 owner-with-an-open-issue, 2 explicit acceptance
  records, 4 dead owners. Those four numbers are counted off the table by script
  and its `(number, line)` pairs asserted equal as a set to the key's own output,
  because counting by reading is what this arc had already lost three counts to.

  **Four defects in the bracket key.** `#198` was still named as tracking
  ingest-time secret scanning; it closed on the `propose accept` half it shipped,
  and [#329](https://github.com/theurian/theurian/issues/329) — which quotes
  #198's own measurement of that path — owns it. `#15` was named three times in
  T-17's residual chain as the future fix for the `|ranking|` term; it closed
  `COMPLETED` on 2026-08-10 having shipped exactly that, as `66a43ae`. The three
  records are left byte-for-byte, because each is the argument as it stood at the
  round that produced it and the fix location they name did not move — only the
  register did, from owed to shipped — so a dated discharge blockquote is
  appended at the end of the chain instead, locating each by a phrase chosen so
  `grep -F` finds it exactly twice: the pointer and its target.

  **It says what it does not claim.** None of the round-five, -six or -seven
  figures has been re-run against a purged build. A Critical entry therefore goes
  on publishing pre-purge numbers with a caveat and no scheduled correction — an
  accurate residue the discharge created rather than closed, and it is named as
  one rather than left to read as a closure.

  **Three more in the bare-mention escape space, which no bracket key reaches.**
  Two deferred a recorded MEDIUM to `#113`, a pull request that merged on
  2026-08-10 and so cannot receive work — the same PR-as-owner shape
  [#444](https://github.com/theurian/theurian/issues/444) records in the source
  twin of that sentence — repointed to
  [#439](https://github.com/theurian/theurian/issues/439), whose own body
  measures the same file set, with ADR-0022/ADR-0018 kept as the mechanism
  reference. The third attributed T-16's cross-surface gap to closed `#39`. That
  the escape count was itself wrong — three places said two — is a round-one
  finding on this PR, and is the whole reason the count now appears with its
  population named beside it everywhere it appears.

  **The key is committed and re-runnable**, as `tools/audit/threat_model_owner_cites.py`,
  on the same terms as the three unit-A keys beside it: one file argument,
  report-only, not CI-wired, classification explicitly out of scope because a
  script can read neither the tracker nor the sentence. Its coverage guard
  compares tracker links to cites **as occurrences and as distinct numbers**, and
  its verdict is now stated as the two comparisons it computes rather than as a
  property derived from them. The escape that forces that wording is recorded
  beneath it, measured four ways against the file at `5a9a1e5`: untouched 114
  cites / 114 links, green; one prose-labelled link, 114 / 115, red; one unlinked
  `[#N]`, 115 / 114, red; **both together, 115 / 115, green** — a compensating
  pair leaving two cites outside the correspondence. Closing it needs per-line
  pairing, which is a different check and is recorded rather than built.

  **One corrected claim about the codebase, so it ships with its pin.** T-16's
  evidence for "nothing holds the three release-claim surfaces to the step's own
  words" was that no test reads `README.md`, `packages/theurian-core/CHANGELOG.md`
  or the threat model. Measured false: seven files under
  `packages/theurian-core/tests` named the root README at `5a9a1e5` and eight do
  at this PR's tip (#470) — the eighth being the pin module itself — and
  `test_setup_claims.CORE_ARRIVAL_SURFACES` carries the root README, which the
  same entry recorded twenty lines further up. The conclusion survives on a
  narrower fact and is now stated on both keys **with the pathspec in each**,
  because the unscoped pair is not a population at all: it counts every prose
  mention of the step id anywhere in the repository, so it read 8 and 9 while
  this change was in flight and 9 and 10 once this entry named the token. Scoped,
  it holds: `probe_artifact_integrity` reaches one module and the step id reaches
  two, at `5a9a1e5` and at this PR's tip alike.
  `test_threat_model_t16_claims.py` holds both
  directions — the retracted wording refused as an assertion while legal as a
  quotation, every block carrying it required to mark it retracted, and the
  narrow fact held as an exact set so a third module naming the probe is the
  moment the record must move to *held* rather than a test to make green. Its
  self-exclusion is derived rather than intended: the token comes from
  `StepId.ARTIFACT_INTEGRITY` and is asserted absent from the module's own bytes,
  so the pin cannot make the entry's published key answer one more than the entry
  says.

  Documentation, one work log and one audit script — no file under
  `packages/theurian-core/src/` is touched, and no behaviour changes. Three
  review rounds, and every HIGH in all three was a count or a stated mechanism in
  this change's *own* new prose rather than in the material it audited: the class
  the sweep exists to close, recurring inside it. That is why each figure above
  names its key and its commit, and why no sentence here restates a check as
  something wider than the check computes. Residues owned: the two unowned faces
  the discharge created, T-16's cross-surface pin and T-17's pre-purge figures
  ([#472](https://github.com/theurian/theurian/issues/472), face B sequenced with
  #445's ADR-0024 reconciliation, which needs the same purge-path ground truth);
  the docstring-backslash ratchet a round-one fix surfaced
  ([#473](https://github.com/theurian/theurian/issues/473) — D301 is off, and
  enabling it flags 31 existing non-raw docstrings under
  `packages/theurian-core/tests/unit/`); and the owner-cite class beyond this one
  file, which #199 unit B's object-keyed census subsumes — the threat model's
  members of the `#129`/`#39`/`#198` population are discharged here and listed
  separately in the work log for #428's accounting.

- **Every repo-wide cite of closed `#129`, `#39` and `#198` is classified, and
  the 28 that named a closed issue as the owner of unbuilt work are repointed or
  stated**
  ([#428](https://github.com/theurian/theurian/issues/428),
  [#482](https://github.com/theurian/theurian/pull/482)). #427 swept one file for
  every number; this is the other half of that split — three numbers, the whole
  tree. All three are closed, and all three were still being cited as the owner
  of controls that do not ship.

  **The population, keyed and anchored.** The key is the one pasted on the issue,
  run with `git grep` rather than `rg` because this repository serves a tracked
  corpus under a dot directory: `214 cite-rows over 199 lines in 47 files` at
  `e546c15`. The unit is a **cite**, not a line and not a number — one roadmap
  line carries a `#129` cite and a `#198` cite repointed at different issues, and
  a Markdown link spells one cite twice — so both counts are stated where either
  appears. Classified per the closure-reason rule, which is what makes the fix
  set 28 and not 199: **#129** and **#39** closed on *documentation* fixes with
  their controls unbuilt, so every owner cite is stale; **#198** closed by
  *shipping* `propose accept`'s scan, so its cites are stale only where they
  point at the unshipped ingest-time and index-time siblings. 28 dead, 8 routed
  to the slices that own them, 177 history, 1 false positive — `&#39;`, the HTML
  entity for an apostrophe inside a Mermaid label, left in the population and
  classified rather than special-cased.

  **The tallies are a script's output, not a reading.** The classifier is pasted
  whole into `docs/work-logs/2026-09-01-428-closed-owner-aggregate.md`, holds the
  classification as data, fails loudly if a classified row is not in the
  population, and was re-extracted from the Markdown and re-run to reproduce its
  own three lines. `(b)` is its default rather than 177 typed judgements.

  **Where each dead owner went, each target read before it was named** — the
  repoint that is not verified is how #429 came to exist, after T-7 was pointed
  at an issue whose scope reaches no fetch path. 14 cites to
  [#429](https://github.com/theurian/theurian/issues/429), which owns the scheme
  allowlist, private-network rejection and repository allowlist against the first
  external fetch path; 4 to
  [#329](https://github.com/theurian/theurian/issues/329), whose body quotes
  #198's own measurement of the unscanned ingest path; 4 to
  [#80](https://github.com/theurian/theurian/issues/80) for T-16, each carrying
  the successor clause the threat model already used, because #80 owns the stale
  pointer and *records that an issue for the control itself is still owed* — a
  partial cover, said as one; and **five cites over four files** to
  [#479](https://github.com/theurian/theurian/issues/479).

  **That five is the entry's own instrument turned on itself.** The count was
  first written as three, corrected to four, and is five. Both wrong answers came
  from keying it on *files* rather than on what each fix targets, and the member
  that kept dropping out — `test_findings_store_is_unreachable.py:25` — reads
  `(Milestone 7, #129)` at the base commit, the same owner-cite shape as the other
  four. It looked like a paraphrase only because an earlier commit *on this
  branch* had already re-shaped it, so the count could no longer see what it was
  counting. The work log's row is now the single authority for the vocabulary,
  derived from the rows whose repoint target is #479 and tabulated with all five
  base texts.

  **What could not be repointed is stated, not invented.** No open issue owned
  GitHub review ingestion. Measured over the full open set (164 issues,
  2026-09-01) by title and body on eight keys, with the four nearest candidates
  each read and rejected — #368 calls itself a git-history source, #223 is
  external tools, #429 gates but does not ingest, and #200's body says the
  `GitHub review` row is outside its scope. The sites said so, with the table
  behind them, until the measurement was filed as #479 and they were repointed at
  it; the absence is kept in the work log rather than replaced by its answer,
  because the record of measuring it is what made the issue fileable.

  **Two pins, both bidirectional.** `docs/roadmap.md` asserted that the threat
  model's T-16 summary row "still points at #39", which stopped being true at
  `efd30fe`; the sentence now quotes what that row reads today and
  `test_roadmap_claims.py` holds it from both sides — the fact rule reads the live
  row out of `docs/security/threat-model.md` rather than a copy, selects the
  quotation by the row's own link labels so the selection moves when the row does,
  and enforces a minimum quotation length so a trivial substring cannot satisfy
  it; the prose rule refuses the retired assertion coming back, with its excusing
  window measured and then narrowed to the cited number's own span after four
  ordinary-English escapes were demonstrated green. And `test_examples.py`'s
  `ANNOTATED_KEYS` required an issue number that a **closed** number satisfied —
  measured one number over, 16 passed — so a required cite may no longer be
  described as closed within its own clause, which keeps the annotations naming
  closed issues as the history that explains the live owner. Every rule shipped
  in these two modules is now driven from both directions; the last one to lack a
  driver survived deleting its own iteration, and that is recorded rather than
  smoothed over.

  **Discharges accounted, not re-fixed.** The threat model's 12 cites are #470's
  and were re-measured here as all history-shaped with zero defects surviving —
  the figure #470's own log predicted. Three wheel-shipped schema descriptions and
  the two `WATCHED_KEY_DESCRIPTIONS` rows that pin them ride
  [#455](https://github.com/theurian/theurian/issues/455)/#199 unit B, and two of
  those five were named in no recorded exclusion until this sweep found them. The
  `ISSUE_URL` triple — the shipped probe string, its byte-identical transcript in
  `release.md`, and the pin comparing them — is #80's, and moving one without the
  others breaks the pin.

  **What the sweep found and did not fold in.**
  [#479](https://github.com/theurian/theurian/issues/479) and
  [#480](https://github.com/theurian/theurian/issues/480) were filed from it (the
  76 lines still anchoring plans to a milestone the project stopped trusting),
  and the shared instrument's own "model output" exemplar was corrected on #199 —
  it offered `docs/roadmap.md:630` as the correct modern form, and that line
  carried two dead cites.

  **The classification never moved; the prose about it did.** Every count this
  entry corrects — three→four→five, 27→28, a fixes table stale by one commit in
  each of two rounds — was prose *about* the branch, written from a list rather
  than derived from the branch's own diff. The 214/28/8/177/1 classification
  survived two review rounds unchanged. That is the distinction worth carrying
  out of this entry: the machinery held, and what failed each time was a sentence
  counting the machinery's work by hand.

## [0.1.0.dev15] - 2026-08-31

### Added

- **`knowledge.search` gains an admission cap on the retrieval path**
  (threat-model T-6, [#26](https://github.com/theurian/theurian/issues/26)). A
  `threading.BoundedSemaphore` in `mcp/tools.py::register`, shared by every
  `knowledge.search` call the daemon serves, admits at most
  `MAX_CONCURRENT_SEARCHES` (4) calls into the answer block at once. A caller
  past the cap is refused before it does any retrieval work
  (`test_the_cap_refuses_the_excess_caller`), capacity is restored on every
  exit path whether the answer block returns or raises
  (`test_capacity_is_restored_on_every_exit_path`), and `/health` keeps
  answering while the cap is saturated
  (`test_health_answers_promptly_while_the_cap_is_saturated`). This bounds
  concurrent occupancy only — not the cost of a single call — and
  `knowledge.get`/`knowledge.status` stay uncapped. `MAX_CONCURRENT_SEARCHES`
  (4) and `ADMISSION_WAIT_SECONDS` (1.0 s) are recorded defaults, not tuned;
  there is no operator config key for either in this slice. See what the cap
  does and does not bound, and the cross-project design decision, in
  [T-6](../../docs/security/threat-model.md).

- **Review-Finding trailer landing store, still no serving surface**
  ([#368](https://github.com/theurian/theurian/issues/368), ADR-0029). A
  `ReviewFindingStore` port, a `SqliteReviewFindingStore` adapter, and a
  standalone `FindingsBuilder` land slice-1's parsed `Review-Finding:` trailers
  in a `theurian-findings-<id>.sqlite` file under `.theurian/state/` — a
  Canonical-layer artifact (ADR-0029 decision 4), rebuilt from empty on every
  run rather than migrated in place. The new `theurian findings build` command
  runs the rebuild and reports counts and a parser-grammar stamp — `findings`,
  `rejected`, `parserStamp`, `storePath` — never a finding's own text. Two
  rebuilds over unchanged history leave a logically identical store; a rebuild
  after history grows converges to the new full set with nothing lost or
  duplicated; a malformed trailer lands in its own table, byte-preserved and
  never re-parsed into a finding; and a deleted store rebuilds identically from
  git, so losing the file is a cache miss, not data loss (ADR-0004). **The write
  is not yet atomic against a concurrent reader** — unlike `index build`'s
  working-name-then-`os.replace` discipline, this rebuild unlinks the live file
  and writes the replacement in place, so a reader racing a build can observe a
  missing file or a partially written one; tracked as
  [#404](https://github.com/theurian/theurian/issues/404), and not yet a defect
  because this slice ships no reader.

  **This is still not a served capability.** `system.capabilities` keeps
  reporting `reviewIngestion: false`, and nothing a caller reaches can serve a
  finding: no MCP tool, no CLI read command, and no module under `mcp/`,
  `daemon/`, or the retrieval/CLI-content path imports the store or names its
  tables — asserted structurally over the whole shipped package, not a
  hand-picked list. The derived `pullRequest`, `family` and `specialist` fields
  stay `None`; a served finding's SEC-15 safety triple, the recurrence query,
  the family-taxonomy corpus items, and the relation/view surfaces are the later
  lanes ADR-0029 still owes.

### Changed

- **Under sustained concurrent load, `knowledge.search` now refuses with a
  constant retryable error instead of queueing without bound** (threat-model
  T-6, [#26](https://github.com/theurian/theurian/issues/26)). This is a
  client-visible behaviour change, not additive: a `knowledge.search` call
  that succeeded at base can now be refused once `MAX_CONCURRENT_SEARCHES`
  (4) calls are already in the retrieval answer block and a further caller
  does not gain a permit within `ADMISSION_WAIT_SECONDS` (1.0 s). The
  refusal's `ToolError` message is a constant, interpolating nothing from the
  request or the store, verified byte-identical by
  `test_the_refusal_is_byte_identical_whatever_the_input`. Measured
  serialization cost at 40 concurrent callers against a light corpus (its own
  document count and size were not themselves recorded): median latency
  0.10 s → 0.36 s, max 0.11 s → 0.62 s (2026-08-30, in-process, branch vs
  05ab8f3) — zero calls were refused in that run, since even the max stayed
  under the 1.0 s admission wait. **On a heavier corpus the comparison
  inverts in wall clock, and the two medians measure different outcomes.**
  Measured 2026-08-30, in-process, 400 documents × 2,000 chars, 40
  concurrent callers: with the cap effectively off (emulated in-process by
  raising `MAX_CONCURRENT_SEARCHES` to 10,000 on the branch — not a
  `05ab8f3` build), all 40 callers were answered, median 3.72 s / max
  3.83 s; under the shipped cap, 19 of 40 were refused within ~1 s and the
  21 answered calls' median was 1.04 s / max 1.15 s. A refused caller
  spends less time per attempt; it does not get an answer. T-6 records
  the multi-second interference this causes for the other tools sharing the
  pool. The refusal has no machine-readable envelope — no error code, no
  retry-after, no capabilities flag — tracked as
  [#419](https://github.com/theurian/theurian/issues/419).

### Documentation

- **ADR-0018 Decision point 2 no longer places the write lock on the state
  database** ([#424](https://github.com/theurian/theurian/issues/424)). The
  point said Milestone 1 "enforces exclusivity with an **OS advisory file lock**
  on the state database". No lock is ever taken on a database file:
  `ProjectPaths.write_lock` (`application/project_service.py`) is
  `.theurian/runtime/write.lock`, `ProjectPaths.database_for` puts the state
  databases under `.theurian/state/`, and `write_transaction(database_path,
  lock_path)` (`infrastructure/sqlite/connection.py`) flocks the lock file
  before it opens a connection to the database and releases it after the commit.
  Mutual exclusion was never in doubt — the record named the wrong object — so
  the clause is corrected in place and a short blockquote records the drift,
  rather than the decision being superseded. The correction also names what the
  Milestone 5 amendment got wrong: it re-read point 2 as accurate after checking
  that a lock is taken and not what it is taken on, so the Decision disagreed
  with the Consequences > Negative bullet that
  [#420](https://github.com/theurian/theurian/pull/420) had already corrected to
  name both paths. Same class as the #417, #252, #198 and #129 corrections — a
  durable record asserting a mechanism the codebase does not contain — and found
  by the same #199 unit-A audit.
  `docs/adr/0027-accept-validates-before-it-moves.md` repeats the retracted
  phrasing in its decision-2 residue and is left for its own change
  ([#433](https://github.com/theurian/theurian/issues/433)); the served
  corpus twin under `.theurian/knowledge/architecture/` carries the retracted
  sentence byte-identically and moves only on a governed re-seed (#199 unit C),
  the same carry #417 records.

- **ADR-0018 no longer cites a `doctor` NFS warning as the mitigation for an
  accepted risk** ([#417](https://github.com/theurian/theurian/issues/417)). Its
  Consequences > Negative accepted the advisory lock's behaviour on network
  filesystems on the grounds that a `.theurian/state/` directory on NFS "is
  already outside the supported configuration, and `doctor` will warn about it".
  No such warning exists: measured 2026-08-30 at 06de58a, no step in
  `application/setup_steps.py::STEPS` reads a filesystem type,
  `cli/setup_commands.py::doctor_command` reports exactly that tuple, and
  `git grep -ni nfs packages/theurian-core/src/` matches nothing. Unlike
  [#252](https://github.com/theurian/theurian/issues/252), which was corrected
  to the owed form because
  [#414](https://github.com/theurian/theurian/issues/414) owns building what it
  described, this one is **rejected rather than deferred**: no owner exists or
  is planned anywhere, and no portable detection design does either —
  `statfs`'s `f_type` is platform-specific, macOS wants `f_fstypename`, and
  nothing in the tree wraps either. That verdict is the disposition recorded on
  #417, not an inference from the missing implementation, which is evidence that
  cannot tell the two cases apart. The acceptance itself is unchanged — NFS is
  still outside the supported configuration — but the bullet now says that
  nothing detects the configuration and that no probe is planned, so an operator
  on NFS reads it as unsignalled rather than as covered by a check they would
  otherwise wait for. It also names the paths the risk is about, since the
  advisory lock is `.theurian/runtime/write.lock` while the databases it guards
  are under `.theurian/state/`. Same class as the #252, #198 and #129
  corrections — a mechanism named in a durable record whose component does not
  exist — and a sibling of the still-open
  [#195](https://github.com/theurian/theurian/issues/195). What holds the
  correction is `tests/unit/test_adr_0018_claims.py`, in both directions: the
  prose half goes RED if the retracted wording returns or if the stated
  absence is dropped, and the fact half goes RED when a filesystem-type read
  lands in `domain/setup.py` or `application/setup_steps.py`. That fact half is
  a source-text sweep over a named API list, which is narrower than "no probe
  exists" and says so in its own docstring; the "no probe is planned" half has
  no fact side at all, because a plan is not a property of the source tree.
  #417 stays open: its
  served corpus twin under `.theurian/knowledge/architecture/` carries the same
  sentence and is corrected by a governed re-seed from a `main` that already
  holds this amendment.

- **ADR-0013 no longer describes the proposal-age report as shipped**
  ([#252](https://github.com/theurian/theurian/issues/252)). Its Consequences
  said in the present tense that `knowledge.status` reports proposal age and
  that `doctor` warns past a threshold. Neither half had an implementation.
  `knowledge_status` publishes six keys — `projectId`, `stateHash`,
  `itemCount`, `itemsByStatus`, `appliedMigrations`, `schemaVersion` — plus the
  conditional `integrity`, and the response schema names no proposal field;
  `doctor_command` (`cli/setup_commands.py`) and the rest of the setup layer
  read `.theurian/proposals/` nowhere. The bullet now reads as owed rather than
  shipped and names [#414](https://github.com/theurian/theurian/issues/414),
  which owns building both. Same class as the #198 and #129 corrections — a
  mechanism named in the present tense in a durable record whose component does
  not exist — and a sibling of the still-open
  [#195](https://github.com/theurian/theurian/issues/195), where
  `plugins/claude-code/README.md` goes on asserting that write-intent tools
  produce proposals. It aligns the ADR with `docs/protocol/mcp-tools.md`, which
  already records that proposal ages are not in that response.

- **The threat model's recorded claims are reconciled against `src/`**
  ([#199](https://github.com/theurian/theurian/issues/199), unit A). An audit of
  every control claim in `docs/security/threat-model.md` against
  `packages/theurian-core/src/` found four false, each now narrowed to the fact
  that is true rather than deleted. **C-1:** "No reader of `.theurian/config.yaml`
  exists in `src/`" was falsified by ADR-0027 decision 3 —
  `security/project_config.py::read_secret_scan_policy` reads it on the `propose
  accept` path — and the conclusion survives on the narrower fact that nothing
  reads `providers.review.repositories`. **C-2:** not-shipped controls named
  CLOSED issues as their owners — two of them (#129, #39) closed on a
  *documentation* fix while the control stayed unbuilt, and #198 on a different
  root cause, having **shipped** its control in `1a38afe` while entries went on
  citing it for the ingest-time and index-time siblings it never claimed. T-7's
  three SSRF controls now point at #429 (opened for them; #368 ingests git
  trailers and builds no fetch path, so it could not own a fetch control),
  T-16's install-time verification at #80, and T-15's unshipped scanning halves
  at #329 — all verified open. **C-3:** T-3's own correction blockquote claimed
  `theurian.domain.retrieval` has no importer in `src/`; it has five, one of them
  `mcp/results.py`, so the argument is restated on `SafetyMetadata` and
  `RetrievalResult`, which are named nowhere outside their module. **C-4:** the
  T-9 sweep row said "all nine sources" where an AST count over the test's own
  `seeds` dict measures ten — wider than claimed, so nothing rested on it.
  T-6 also gains the measurement #199 owed it, taken on the path ingestion
  actually runs: `application/ingestion_service.py:225` calls
  `projection.project`, which **truncates** at `MAX_PROJECTION_CHARS` and indexes
  the result — the refusing `project_checked`, and the `build_projection` that
  wraps it, have no production caller. The costliest document found within the
  4 MiB `MAX_YAML_BYTES` gate — a block sequence of `- 1` entries, four bytes per
  node — costs **13.06 s and ~666 MB RSS** end to end on CPython 3.13.3, of which
  **99% is the YAML parse**, not the projection walk; the walk's own absent-memo
  behaviour is real (48 parsed objects against 120,491 visits) but is under 3% of
  the cost. **Token density rather than alias structure is the lever**, which is
  why `MAX_YAML_BYTES` is the bound that governs this term. Recorded as the worst
  shape *found*, not an established maximum — the figure moved twice under
  search. The recorded decision that no ingestion-side timeout is filed is
  unchanged. Three `src` docstrings carrying the correction class are fixed with
  it — `index_purge.py::_restamp` on `index_metadata.index_build_id`, which
  `SqliteIndexStore.add_nodes` does read back; `verify_state_provenance`, which
  claimed two call sites and has one, the build path it also named being gated by
  `BuildProvenance.has_state` in `cli/index_commands.py` instead; and
  `infrastructure/github/__init__.py`'s config-reader sentence — all prose-only
  and AST-identical once docstrings are stripped. The audit record, its
  population keys and the escape space they leave are in
  [`docs/work-logs/2026-08-30-199-unit-a-audit.md`](../../docs/work-logs/2026-08-30-199-unit-a-audit.md).

- **The requirements analysis's T-7 and T-15 rows carry the threat model's owner
  cites** ([#428](https://github.com/theurian/theurian/issues/428)).
  `docs/architecture/requirements-analysis.md` keeps a second copy of the threat
  table, and [#425](https://github.com/theurian/theurian/pull/425) repointed only
  the originals: the copy still owed T-7's scheme allowlist, private-network
  rejection and repository allowlist to closed #129, and T-15's ingest-time and
  index-time scanning to closed #198. Both cites now read as the threat model's
  summary rows read — #429 for T-7's fetch controls, #329 for T-15's unshipped
  scanning halves, each closed issue named for what it actually closed on. The
  owner-cite fragments were extracted from both files and diffed rather than
  compared by eye; nothing else in either row moved, and the rest of #428's twin
  sweep stays open. That diff is now a test —
  `tests/unit/test_threat_model_twins.py` re-extracts both fragments and fails
  when either side moves alone — and it holds **these two rows**, not the two
  tables: the general row-by-row pin needs the copy's differently shaped Control
  column reconciled first, and that is a documents change.
- **Two flowcharts stop advising a command the shipped `SessionStart` hook
  dropped** ([#421](https://github.com/theurian/theurian/issues/421)). The hook
  names an installer before `/theurian:setup`, because `/theurian:setup` shells
  out to the `theurian` binary whose absence produced the warning. Two documents
  that *specify* the hook were left behind when it was corrected:
  `docs/integrations/claude-code.md`'s `SessionStart` flowchart still said
  `warn: run /theurian:setup`, and `docs/architecture/requirements-analysis.md`'s
  compatibility flowchart still said "Advise /theurian:setup. Do not install
  anything." Both now name the installer first, matched against the line the hook
  actually prints — captured by running it with `theurian` off `PATH`, not read —
  which the integration doc now quotes verbatim once. Requirements-analysis keeps
  "Do not install anything": that half was always true of a hook that prints its
  advice and runs none of it. The threat model's T-16 table, which carried the two
  as deferrals with an empty owner column, now records them as corrected in the
  same three columns its resolved rows use.

  **Both nodes are pinned, one at a time, because adding the file to the
  population would not have held either.** `tests/unit/test_setup_claims.py`
  gained a rule per flowchart, each keyed on that chart's single Core-absent edge
  and each asserting the edge is unique before reading it.
  `docs/integrations/claude-code.md` also joined `CORE_ARRIVAL_SURFACES` — but
  membership pins the verbatim quotation, not the picture above it: reverting the
  node while leaving the quote in place left all three tuple rules green,
  measured. `docs/architecture/requirements-analysis.md` stays outside the tuple,
  since its node advises "the installer" and points at the quotation rather than
  repeating the commands, and the tuple's rule is verbatim by design.

## [0.1.0.dev14] - 2026-08-28

### Fixed

- **The Markdown fence scan no longer rescans the rest of the document per
  unclosed fence opener** ([#331](https://github.com/theurian/theurian/issues/331)).
  `parsers/markdown.py::_FENCE` combined opener and closer into one
  `re.MULTILINE | re.DOTALL` pattern whose lazy `(.*?)` body group had to scan
  every remaining line before it could conclude a closer was absent, so a
  document of n unclosed openers cost Θ(n²). Measured: 156 KB of unclosed
  openers (32,000 of them) went from 25.3 s to 0.0065 s (~3900×), and the scan
  now costs roughly 2×, not 4×, per doubling of the input. `_fences` is now a
  single forward pass over lines, matched against two anchored,
  non-backtracking patterns instead of one pattern that rescans the remaining
  document per unclosed opener; `codeFences` stays byte-identical, pinned by
  a 14-case named-edge oracle
  (`test_code_fences_match_the_pre_331_regex_oracle`) plus a seeded,
  deterministic Hypothesis fuzz over 400 random documents
  (`test_code_fences_match_the_pre_331_regex_oracle_over_random_documents`,
  `@seed(331)`), both against the pre-fix regex embedded as an oracle.
  **A bounded residual replaces it:** the rewrite materializes
  `lines` and `line_starts` up front, so peak memory is now linear in document
  size rather than constant — ~202 MB on an 8 MiB document (the
  `MAX_SOURCE_FILE_BYTES` cap), irrelevant at ordinary sizes and pinned by
  `test_fence_scan_memory_stays_linear_in_document_size` so a future change
  cannot silently make it super-linear again. Discharges the T-6 residual
  `docs/security/threat-model.md` recorded as owed to this issue.

- **The OpenAPI `$ref` walk no longer copies its accumulated path string on
  every edge** ([#328](https://github.com/theurian/theurian/issues/328)).
  `_external_refs`'s `walk` built each child's path with `f"{path}.{key}"`,
  which copies the parent's whole path on every child; `descended` (#245)
  bounds how many *nodes* the walk enters but never charged this, so one long
  mapping key with a wide fan-out under it cost Θ(edges × path length) —
  quadratic in the document's own size, with neither `MAX_REFS` nor
  `MAX_REF_DEPTH` firing on this shape. Measured at n=240,000 (~3.25 MB, zero
  refs, zero truncations): 1.28 s → 0.037 s. `walk` now carries the path as a
  tuple of un-rendered segments — mirroring
  `normalization/projection.py::_walk`'s tuple-path split — and renders it to
  a string only where a ref or a truncation is actually recorded, not once per
  edge crossed; appending a segment now costs `O(depth)`, bounded by
  `MAX_REF_DEPTH`, never `O(len of the rendered string)`. Recorded ref paths
  stay byte-identical to the pre-fix eager-concat build, pinned by a single
  two-`$ref` fixture
  (`test_recorded_paths_match_the_pre_328_eager_concat_build`) plus a seeded,
  deterministic Hypothesis fuzz over 400 random nested structures
  (`test_ref_paths_match_the_pre_328_eager_concat_build_over_random_documents`,
  `@seed(328)`). Discharges the T-6 residual
  `docs/security/threat-model.md` recorded as owed to this issue.

### Changed

- **BREAKING — `SCHEMA_VERSION` 3 → 4: every existing state database is refused
  once, and one `theurian migrate apply` rebuilds it**
  ([#117](https://github.com/theurian/theurian/issues/117)).
  `knowledge_revisions`' `CHECK (valid_to IS NULL OR valid_to > valid_from)`
  compared `valid_from`/`valid_to` as stored — `datetime.isoformat()`
  verbatim, each keeping the author's own UTC offset rather than a
  normalised one — so SQLite ordered them as TEXT, not as instants. A window
  the domain accepts sorts the wrong way as a string: `validFrom`
  `2031-01-01T00:00:00+09:00` (the instant 2030-12-31T15:00Z) and `validTo`
  `2031-01-01T00:00:00+00:00` (the instant 2031-01-01T00:00Z, nine hours
  *later*) satisfy `valid_to > valid_from` as datetimes and fail it as text,
  so `theurian migrate apply` exited 1 with an unhandled
  `sqlite3.IntegrityError` for a legitimate window.

  The `CHECK` is dropped rather than rewritten; the ordering guarantee moves
  entirely to the domain. `ValidityPeriod.__post_init__` (INV-4,
  `domain/values.py`) already compares aware `datetime`s and orders by
  instant, and it is the only constructor that reaches these columns —
  `SqliteWriter.append_revision`, `.put_item` and `.register_specification`
  each bind `<entity>.validity.valid_from`/`.valid_to`, which
  `test_validity_write_path.py` pins structurally so removing the redundant
  SQL check does not open a bypass. A state database is derived and
  Git-ignored (ADR-0004) and is rebuilt rather than migrated (ADR-0017), so
  nothing authored is lost — but the rebuild is not automatic, and until it
  runs the three read tools refuse. BREAKING for a state database on disk,
  not for the wire: `protocolVersion` stays `theurian/v1`, the same rule the
  `SCHEMA_VERSION` 2 → 3 entry for `0.1.0.dev4` applies.

### Security

- **`propose accept`'s body-materialisation cost is now bounded on every
  channel it has, closing #306 and #400 together**
  ([#306](https://github.com/theurian/theurian/issues/306),
  [#400](https://github.com/theurian/theurian/issues/400); SEC-8, T-6 in
  [the threat model](../../docs/security/threat-model.md)). `_body_moves`
  read a fresh copy of a body for every operation that named it, with no cap
  on how many operations a migration could declare and no dedup for two
  operations naming the same file — a schema refusal fired only after that
  read had already happened. `_commit`'s rollback snapshot of a *replaced*
  destination read it with a raw `Path.read_bytes()`, uncapped whenever that
  destination had reached its size some way other than through this service
  — a body committed directly to `.theurian/knowledge/`, exactly what a
  `git clone` delivers.

  **#306, the incoming/count face.** Two independent bounds close it, both
  landing before a single body is read: `MAX_UPSERT_OPERATIONS` (250) refuses
  a migration whose `operations` list is longer, checked against the raw
  parsed document; and `_body_moves` now reads a given source at most once,
  keyed on `(st_dev, st_ino)` inode identity rather than the path string —
  which a case-insensitive or NFC/NFD-normalising filesystem can alias — so
  resident memory tracks distinct bodies, not operations naming them.
  Measured: 1,000 operations naming one shared 512 KB body held 583 MB
  resident before the fix — the same run also missed a 120-second budget and
  was SIGKILLed, a latency DoS on top of the memory one — against 65 MB
  after, flat as operation count grows past the cap, with an over-cap
  proposal now refusing in about 2 seconds instead of timing out.

  **The cap is sized for two channels, not one.** `_commit` holds a second
  set of bytes resident alongside the incoming bodies: for every operation
  that replaces an existing destination, the prior body is kept resident for
  rollback at the same time as the incoming one is held in `moves`. Both can
  be full together, so the worst-case peak is
  `2 × MAX_UPSERT_OPERATIONS × MAX_SOURCE_FILE_BYTES` ≈ 3.9 GiB, not the
  single-channel `250 × 8 MiB` a reader might otherwise infer from the cap
  alone — which is why `MAX_UPSERT_OPERATIONS` tightened from an
  initially-chosen 500 to 250. This repository's largest legitimate
  migration is 5 operations, so 250 leaves roughly 50× headroom over what a
  real migration here has ever declared.

  **#400, the replaced/per-entry face, closed the same way.**
  `MAX_UPSERT_OPERATIONS` bounds the *count* of operations, not the size of
  any *one* destination a replace operation overwrites. `_commit` now reads
  a replaced destination through `security/paths.py::read_source_file`, the
  same size-capped path every other accept-path read already takes: a
  destination over `MAX_SOURCE_FILE_BYTES` (8 MiB) is refused before a byte
  of it is read, and a new rollback clause restores whatever the same
  `accept` call had already written before re-raising. Measured against a
  256 MiB replaced destination: **+257.3 MiB** resident before this fix (the
  replacement succeeded, the whole file held in memory) against **+1.3 MiB**
  after (refused before reading). The boundary is exact — 8 MiB is accepted,
  8 MiB + 1 byte is refused.

  Two refusals reached through this same path also stopped naming no file at
  all: `_commit`'s restored-destination read and the ADR-0027 rehearsal's
  landed-set read (`cli/migration_pipeline.py::_materialize`) now both
  attach a project-relative `referrer` to `IrregularSourceFileError`, the
  same fix `ProposalService._read_within_project` already had for the
  incoming side — left bare, either read published a refusal naming no path
  at all.

## [0.1.0.dev13] - 2026-08-27

### Added

- **Review-Finding trailer ingestion, parse-only**
  ([#368](https://github.com/theurian/theurian/issues/368), ADR-0029). A
  `ReviewFinding` canonical record, a `ReviewFindingSource` port, and a
  `GitTrailerFindingSource` adapter that reads `Review-Finding:` commit trailers
  from the public default branch into structured finding records. `reviewer` and
  `severity` are validated against their closed vocabularies; the free-text
  finding is kept byte-for-byte; a keyed line that does not parse is captured as
  a rejected record with its reason, rather than aborting the whole load or being
  silently dropped — one quoted grammar example in a future commit body must not
  brick a signed, append-only corpus. The frozen `code` spelling on existing
  history normalises to `code-review`, and no new reviewer token is coined.

  **This is foundational, not a user-facing feature.** It produces records and
  nothing else: no store write, no serving path, and no MCP or CLI surface reads
  or emits a finding, so there is no command to run and `system.capabilities`
  still reports `reviewIngestion: false`. The derived `pullRequest`, `family` and
  `specialist` fields are `None` in this slice; deriving them, serving a finding's
  text under the untrusted-content safety triple, the recurrence query, and the
  family-taxonomy corpus items are the later lanes ADR-0029 names. The source
  reads the fully-qualified `refs/remotes/origin/main` — not the ambiguous short
  name a shadowing local branch, tag, or `git replace` can hijack — with object
  replacement disabled and inherited `GIT_*` stripped, and stays offline: it
  spawns `git log` over local objects and opens no network connection.

### Changed

- **BREAKING — `theurian project status` publishes `registered` about the
  registry rather than about whether the project resolved, and stops guessing
  `indexStale`** ([#226](https://github.com/theurian/theurian/issues/226)).

  **Old behaviour:** the field answered about *resolution*, on both of the
  command's branches, and each was wrong in its own direction.

  When resolution failed — an unreadable `.theurian/migrations`, a malformed
  migration, a state schema that will not parse — the payload asked the registry
  "is *anything* registered?", which a healthy registry answers *no*. So a
  registered project with `chmod 000 .theurian/migrations` reported
  `registered: false` while `theurian project list`, reading the same file in the
  same second, listed its `rootPath` at `count: 1`; and a root the registry held
  under *two* ids — named twice, both entries readable — reported
  `registered: false` as well.

  When resolution succeeded, the field answered about `projectId`, which for an
  unregistered repository is only the directory name. Two teams checking out
  `api` was enough: with `team-one/api` registered, `theurian project status` in
  an unregistered `team-two/api` reported `registered: true, projectId: api` off
  the neighbour's entry, while `project list`, `project register` and `setup`
  all answered by root and said unregistered.

  The unresolved payload also published a hardcoded `indexStale: false`,
  claiming a fresh index for a directory nothing had looked at.

  **New behaviour:** one rule on both branches, keyed on the working-tree root.
  **Inside a Git working tree**, `registered` is `true` when the registry holds
  this root, `null` when the registry cannot say, and `false` only when a
  readable registry genuinely lacks it. All three cases above change: the first
  two to `true`, the neighbour-collision to `false`.

  **Outside a working tree the rule does not apply at all**, and this is
  unchanged from before: `registered` is a plain `false`, whatever state the
  registry is in — a corrupt one included, where `unreadable` is `[]` and
  nothing in the payload says the file could not be read. A directory in no
  working tree is not a project whichever way the registry reads, so no entry
  could be about it; `theurian project list` is the surface that reports the
  file.

  `null` is unchanged in meaning; its reach is the file's *legibility*, which is
  broader than "does not parse". Inside a tree it is published whenever the
  registry cannot be read as a whole (it does not parse, or it cannot be opened)
  **or** it holds any entry that `ProjectRegistry.load` skips — one naming no
  root, and one keyed by an id no consumer accepts, *even when that entry names
  a different directory*. That second kind does not stop the project resolving,
  so `null` arrives on the **resolved** payload too, beside a correct
  `projectId` and `root`; it is not a symptom of the command having failed.
  A skipped entry cannot be ruled out as a second registration of this root; the
  `unreadable` list names the entries to remove with
  `theurian project unregister`.

  `indexStale` is now **absent** from the unresolved payload rather than `false`
  or `null`: nothing on that branch reads the active state pointer or computes a
  state hash, and this payload already spells that distinction — `null` is
  "asked, and the answer is unknowable", absence is "never asked", which is why
  `statePointerCorrupt` has always been absent there. The resolved payload is
  untouched and still publishes `indexStale` as a boolean.

  **What a consumer changes:** code indexing `payload["indexStale"]`
  unconditionally must use `.get()`. Do **not** discriminate the two payload
  shapes on `reason` — the resolved payload carries `reason` and `remedy` too,
  for an unreadable state pointer or registry (measured: a corrupt
  `.theurian/state/active.json` yields the resolved shape with `reason`,
  `statePointerCorrupt: true` and every other resolved field). `root` and
  `projectId` are
  the resolved-only keys, so their presence is what tells the shapes apart. Code
  reading `registered` as a boolean must handle `null`, which both branches now
  publish.

  Two bundled Claude Code plugin surfaces read this payload.
  `scripts/session-start.sh` needs no syntactic edit — it greps for
  `"registered": *false` and `"indexStale": *true`, and neither pattern breaks.
  Its advice is unchanged for a repository that is genuinely unregistered:
  that still resolves, still publishes `registered: false` and `indexStale:
  true`, and the hook still says both things. What changes is the three cases
  above, where it used to advise on a false premise and is now silent —
  [#380](https://github.com/theurian/theurian/issues/380) tracks giving it
  something true to say. Separately, its `indexStale` grep can no longer match
  an unresolved payload at all, because the key is absent there rather than
  `false`; that grep was already unreachable on that branch, so nothing it
  printed is lost. `commands/status.md` is reworded in this release: it
  instructed a two-valued "registered or not" project summary, which a
  tri-state field cannot be reduced to without inventing an answer.

- **BREAKING — `theurian project status` publishes the index's own staleness as
  `indexStale`, computed by the same function `theurian index status` publishes
  `stale` from** ([#100](https://github.com/theurian/theurian/issues/100)).

  **Old behaviour:** `indexStale` was `active is None or active.state_hash !=
  context.state_hash` — the *canonical* state pointer against the migrations,
  which asks whether `theurian migrate apply` is up to date. The command never
  opened `.theurian/state/active-index.json` at all, so every axis the index
  actually has was invisible to it. Measured through the real CLI: a registered
  project with its migrations applied and **no index ever built** published
  `indexStale: false` in the same second `theurian index status` published
  `built: false, stale: true` with the remedy ``Run `theurian index build`.``
  Applying a further migration over a published build was the inverted case —
  the canonical pointer is current again after `apply`, so the field answered
  `false` at exactly the moment the build fell a migration behind.

  **New behaviour:** `indexStale` is the verdict `index status` reports as
  `stale`, on every axis it recognises — no published build, a build whose state
  hash is behind, an index schema this build does not understand, a build
  stamped with another project's id, a recorded disclosure flavor that is not
  the one in force, and a withdrawal purge that did not complete. There is one
  computation (`cli.index_status_report.index_staleness`) and both commands
  consume it, so the two surfaces cannot drift.

  `theurian index status`'s `--json` payload is unchanged: the same twenty keys
  with the same values, re-derived from the shared function. Its *rendered*
  output lists the index-side fields together before the state-side ones now,
  because that block is one merge; `--json` sorts its keys and is unaffected.

  **What a consumer changes:** nothing syntactically — the key, its type and its
  location in the resolved payload are the same, and it stays **absent** from
  the unresolved payload as the entry above records. What changes is which
  projects it is `true` for. It becomes `true` for a project that has never
  built an index, and for the pointer-side axes the old expression could not
  see.

  It flips the other way for a **class**, not for a listed state: the canonical
  state pointer no longer participates in the verdict at all, so `indexStale`
  reads `false` wherever `.theurian/state/active.json` disagrees with the
  migrations while a published build still matches them. Measured, that class
  has three members — the pointer is **missing**, the pointer is **unreadable**
  (truncated JSON, raw text and arbitrary bytes each measured; `statePointerCorrupt`
  reports it), or the pointer parses and **names a different state hash**. In
  all three the index genuinely is current, and `stateBuilt`,
  `activeStateHash` and `statePointerCorrupt` beside it are what report the
  state. A pending `migrate apply` is not in the class and still reads `true`,
  because the published build's state hash is behind the migrations either way.

  For a missing pointer and for one naming a different hash, `theurian index
  status` answers the same `stale: false` beside `knowledgeNotApplied: true`,
  and agreeing with it is the change. For an **unreadable** pointer there is
  nothing to agree with: `index status` refuses that state outright (exit 1,
  `{error, remedy}`, no payload), so `project status` is the only surface
  answering, and it answers about the index while `statePointerCorrupt` answers
  about the file.

  The old question is still answerable from the same payload and always was —
  `activeStateHash` against `stateHash`, both published beside it — so no
  information is lost, which is why no new key was added for it.

  The bundled Claude Code plugin's `scripts/session-start.sh` needs no
  syntactic edit — it greps for `"indexStale": *true` — but what it says changes
  in three directions, and only one of them is an improvement.

  It **newly fires**, correctly, for a project that has never built an index and
  for the pointer-side axes: the advice, run `/theurian:index`, is exactly right
  there. It **newly goes silent** for the flip class named above — a missing,
  unreadable or hash-mismatched `active.json` under a build that still matches
  the migrations — where it used to warn about a stale index. The warning it
  loses was not about the index in the first place, and nothing in the hook yet
  reads `statePointerCorrupt` or `activeStateHash`, so those states now pass
  unremarked. And on the serving-profile axis the warning it prints names a
  command that cannot run: measured, an unreadable
  `<data-dir>/auth/serving-profile` makes `indexStale: true`, while `theurian
  index build` — what `/theurian:index` invokes — refuses on the same file at
  exit 1, and `project status` publishes no remedy at all for a caller to relay.
  `theurian index status` is the surface that names the `chmod`.

  All three are [#380](https://github.com/theurian/theurian/issues/380)'s scope:
  the hook needs to read more of this payload than one boolean before it can say
  something true in each state.

### Fixed

- **An over-long `indexBuildId` no longer ends `theurian index status` in a
  traceback** ([#100](https://github.com/theurian/theurian/issues/100)).
  `cli.index_status_report.index_schema_version` probed the published build with
  `Path.is_file()` outside the block that answers for the pointer's contents.
  `is_file()` swallows only the errnos `pathlib` lists as "this is not a file",
  and `ENAMETOOLONG` is not among them; `ProjectPaths.index_for` cannot convert
  it either, because `Path.resolve()` in non-strict mode never stats. Measured
  through the real CLI on macOS: an `indexBuildId` of 234 characters or more —
  `theurian-index-<id>.sqlite` past a 255-byte `NAME_MAX` — ended the command in
  a bare `OSError`, exit 1, empty stdout, none of the `{error, remedy}` shape
  CP-2 promises; 233 answered. The probe is inside the block now and the build's
  version reports 0, which is that function's "unknowable" and makes the index
  stale. The pointer is derived, git-ignored and unsigned (SEC-7), so this is an
  input any local process can leave behind.

  `theurian project status` reaches the same probe as of the `indexStale` change
  above, so this release is the first in which it could crash that way and the
  first in which it cannot. `theurian index gc` and `knowledge.search` reach
  `ProjectPaths.index_for` by their own routes and still probe outside a guard;
  [#388](https://github.com/theurian/theurian/issues/388) owns those.

- **A `rootPath` that resolves to the caller's own directory no longer registers
  whichever repository asks**
  ([#226](https://github.com/theurian/theurian/issues/226)). An entry was
  rejected as naming no root when its `rootPath` was `""`, because
  `Path("").resolve()` is the calling process's working directory. Two other
  spellings reach the same place and neither was refused: a relative path
  (`"."`, `"./"`, `"demo/../."`, `"demo/sub"`), and — on Linux — the absolute
  `/proc/self/cwd`, a symlink to exactly that directory. Measured, once per
  spelling: a registry hand-edited to a single such entry made *every*
  repository on the machine report `registered: true` under that one entry's id,
  each answering as a project it had nothing to do with.

  A `rootPath` that is not absolute, or whose lexically normalised first
  component is `proc`, is now unreadable like a missing one: named under
  `unreadable`, removable with `theurian project unregister`. The `/proc` test
  is on the spelling, not on the filesystem, so it holds on macOS too — where
  the namespace does not exist — rather than only on the platform where it
  bites. It normalises first, so `//proc/self/cwd` and `/tmp/../proc/self/cwd`
  are refused as well; it cannot see a symlink on disk that points into `/proc`,
  which would have to be resolved to be detected, and resolving is the operation
  that produces the wrong answer.

  No entry Theurian writes is affected — `project register` writes an absolute,
  resolved path, `Project` rejects anything else at construction, and a Git
  working tree does not live under `/proc` — so this can only reject a hand
  edit.

### Security

- **The serve gate now re-checks a served excerpt's content against canonical's
  current revision, closing a same-revision content-drift disclosure**
  (`GHSA-3f65-gr36-qqx8`; T-23 in
  [the threat model](../../docs/security/threat-model.md), a new face of the
  derived-state-trust class `GHSA-266v-fcj2-qggx` / T-19). `knowledge.search`
  cleared a retrieved row on **revision identity alone** —
  `CanonicalVisibility._may_surface` checked that the indexed `revision_id` still
  matched canonical's current pointer and stopped there. The state database is a
  derived, unsigned, git-ignored file (ADR-0004, SEC-7), so a revision's *served*
  content can be made to drift under an *unchanged* `revision_id`: an author edits
  an approved revision's title (migration metadata that no `contentSha256` pins)
  or body (re-pinning `contentSha256`), deletes `.theurian/state/active.json` so
  history verification early-returns (FR-K5), and re-applies. Canonical then holds
  the new content while a published index still holds the old chunk text under the
  same `revision_id`, so the revision check passed on both sides and the excerpt —
  cut from the index's `title\n\nbody` chunk text — served the retracted content.

  The gate now keys on the **exact served bytes**. A per-chunk
  `served_content_sha256`, recorded at build time as the hash of the very string
  the builder chunks (`served_content_hash(title, body)`), is compared at serve
  time against the same hash recomputed from canonical's *current* revision's
  title and body. Title drift and body drift both move it; a mismatch — or an
  unverifiable `None` — withholds the whole row, beside the status and sensitivity
  checks and before the candidate-depth cut. This is the same class
  `GHSA-266v-fcj2-qggx` closed for a wholesale doctored `.theurian/state/`; here
  the drift is a single live revision's served content.

  **Consumer-visible consequence: `INDEX_SCHEMA_VERSION` moves 6 → 7, so the
  first `knowledge.search` after upgrading rebuilds the index.** A pre-fix build
  carries no `served_content_sha256` column and cannot answer the content check,
  so it is refused whole as `index-schema-mismatch` and stands aside to the
  unranked canonical scan until `theurian index build` runs — the same one-command
  rebuild every `INDEX_SCHEMA_VERSION` bump has always required (ADR-0022
  point 3), never an in-place migration of the file.

  **Scope.** The leaf-chunk excerpt is closed; a summary build's
  `raptorPath[].title` can still quote a drifted leaf, which stays the T-17a
  residual (`GHSA-97q9-xxfg-33r6`), not this fix. The advisory carries the full
  mechanism and the affected range.

## [0.1.0.dev12] - 2026-08-26

### Fixed

- **`doctor` and `theurian migrate validate` no longer disagree about the same
  migrations directory**
  ([#91](https://github.com/theurian/theurian/issues/91)). The `migrations-valid`
  step counted `migrations/*.yaml` and reported `satisfied` for any directory at
  all, so a project whose migrations did not parse or validate read as converged
  while every `theurian migrate` against it refused. The step now runs the same
  static validation `theurian migrate validate` runs — parse, schema conformance,
  `contentFile` containment, the `contentSha256` pins, application order, and the
  whole-set guards — and a directory that fails any of them now reports `missing`
  (not `satisfied`), naming the file to fix. History verification — an
  already-applied migration edited since the state database recorded its checksum
  — is still out of scope for `doctor` and reported by `theurian migrate validate`
  alone ([#366](https://github.com/theurian/theurian/issues/366)).

- **The `gitignore` step judges the managed block `theurian init` writes, not a
  substring** ([#87](https://github.com/theurian/theurian/issues/87)). A
  `.gitignore` whose Theurian entries were negated (`!.theurian/state/`) or
  commented out (`# .theurian/state/`) satisfied the old check while Git ignored
  nothing, so `doctor` called a machine converged on which derived artifacts — and
  ADR-0028's machine-local `.theurian/proposals-local/` — were committable. The
  step now reports `satisfied` only when exactly one well-formed managed block is
  present and its span matches, byte for byte, what `theurian init` writes; an
  absent, silent, stale, edited, or marker-malformed block now reports `missing`.
  This does not yet catch a re-inclusion further down the file
  (`!.theurian/state/` below the block) or a nested `.theurian/.gitignore`; the
  `git ls-files` tracked-artifact check that would
  ([#64](https://github.com/theurian/theurian/issues/64)) is still owed.

- **A non-directory at the data directory is reported, not treated as private**
  ([#87](https://github.com/theurian/theurian/issues/87)). A regular file at the
  data-directory path satisfied the data-directory step whenever its mode was
  tight, and `token`, `token-storage` and `env-file` then wrote inside a path that
  is not a directory. It now reports `conflicting`: setup replaces nothing it did
  not create, so the remedy is to move the file aside.

- **`token-storage` claims only the permission bits it measured, and asks for
  rotation when the token directory is writable**
  ([#87](https://github.com/theurian/theurian/issues/87)). Its `satisfied` summary
  read "stored 0600 inside a 0700 directory", which was never what the check
  measured — a 0400 token passed it — and "not accessible to other local users"
  overclaimed past what mode bits decide, since a macOS ACL can widen a 0600 file.
  The summary now states only that no group or other permission bits are set on
  the token file or its directory. A group- or other-*writable* `auth/` directory
  is now `conflicting` and asks for `theurian auth rotate` as well as a `chmod`,
  because another user who can write the directory can replace the token file; the
  old single directory arm dropped rotation on the write bit.

- **The "No daemon is running" summaries are scoped to the port actually probed**
  ([#93](https://github.com/theurian/theurian/issues/93)). `doctor` probes one
  address, `127.0.0.1:<port>`, and reported "No daemon is running" for the whole
  machine from it — so a daemon serving the same data directory on another port
  was described as absent, and the reader was sent to start a second one that
  `theurian daemon start` then refuses as a duplicate. The three affected
  summaries (`daemon-running`, `single-instance`, `mcp-health`) now name the
  address observed and say what they did not look at.
### Security

- **The accept-path secret scan covers the artifacts an acceptance lands, not
  only the fields it parses**
  ([#349](https://github.com/theurian/theurian/issues/349), SEC-11, ADR-0027
  decision 3; T-15 in [the threat model](../../docs/security/threat-model.md)).
  dev11 widened the scan from the bodies alone to the migration document's
  author-written field *values*; a credential could still ride in on text those
  values do not contain. `theurian propose accept` now scans everything the
  acceptance would land, under the same `security.secretScan` policy — `block`
  by default, `warn` and `off` unchanged:

  - **the migration file's raw bytes**, which land in `.theurian/migrations/`
    verbatim — so a YAML **comment** (a rotation note naming the retired value)
    and every field *as written* are read, not only what a parse keeps;
  - **the migration filename**, whose slug after the ULID prefix is the
    contributor's on a hand-authored proposal and appears in no field;
  - **the parsed `contentFile` value**, now part of the author-written field
    walk — the one channel that catches a credential that is both `..`-collapsed
    *and* spelled with YAML `\xNN` escapes, which the byte channel and the
    landed-path channel each miss on their own;
  - **each landed body's path** relative to `.theurian/knowledge/`, directory
    components included, since `accept` makes every component a real directory.

  **A finding never reproduces the value it reports.** Every finding location is
  a fixed module literal plus an index — never the author's filename, path or
  body text — so a `block` refusal and an `accept --json` result quote at most
  the four-character redaction prefix. This closes the last finding-location
  channel that was still built from scanned text.

  **What this does not do.** The filename channel reaches only two credential
  families in practice: a migration slug is `[a-z0-9]+(-[a-z0-9]+)*`, so only
  `openai-api-key` (`sk-`) and `slack-token` (`xox`) can be spelled in one —
  `aws-access-key-id` and `google-api-key` need an upper-case letter, and
  `github-token` and `stripe-secret-key` need `_` (measured 2026-08-26 over
  400,000 legal filenames). The less-restricted body-path channel is not so
  limited — a path component admits upper-case letters, digits and `_`, so every
  family the slug excludes can be spelled and caught in one (measured 2026-08-26:
  seven families fire by name, and a `google-api-key` shape is caught as
  `high-entropy-token` rather than its own family). The detector is still best
  effort, and Theurian is still not a repository secret scanner. No false positives over the 82 documents of the
  dogfood migration corpus on any channel, the parsed `contentFile` included.

  **Two residuals, tracked rather than closed.** Refusal *messages* elsewhere on
  the accept path still echo an author's migration filename, id or `contentFile`
  verbatim (≈6 sites), and the `accept --json` `bodyFiles` success field prints
  landed paths at full length — a general name-hygiene channel, pre-existing and
  non-disclosing, tracked in
  [#360](https://github.com/theurian/theurian/issues/360). A proposal's
  `evidence.json` still travels into the pull request unscanned, because
  `accept` leaves the rest of the proposal directory where it is and instructs
  committing it ([#361](https://github.com/theurian/theurian/issues/361)).

### Documentation

- **`docs/contributing/release.md` no longer claims branch protection is
  unenforced on the advisory merge.** The `GHSA-97q9-xxfg-33r6` release measured
  the opposite: `main`'s seven required status checks *are* enforced on the
  advisory "Merge pull request(s)", and because none of them can run on the
  CI-less temporary private fork they never report, so they block the merge with
  *"N of N required status checks are expected"*. The maintainer lands it via an
  admin bypass — temporarily setting `enforce_admins` to `false`, then restoring
  it — with the manual gate on the fork before the merge and the push-triggered
  CI on `main` after it standing in for the checks that cannot run; the
  `git format-patch` route to an ordinary public pull request stays as the
  CI-satisfying alternative. The stale settings table, which asserted
  `required_status_checks: null` and an unenforced merge, is corrected to the
  values measured with `gh api .../branches/main/protection` on 2026-08-25.
- **`mkdocs build --strict` builds again, restoring the *Deploy documentation*
  job on `main`.** ADR-0013 and ADR-0027 linked
  `plugins/claude-code/commands/propose.md` with a relative path that resolved
  outside the `docs/` tree, which `--strict` rejects as an unresolved link; both
  now use the absolute GitHub URL this repository already uses for links to files
  outside `docs/`.

## [0.1.0.dev11] - 2026-08-25

### Added

- **A deployment declares one sensitivity ceiling, and it is enforced**
  ([#119](https://github.com/theurian/theurian/issues/119), ADR-0025). One word —
  `public`, `internal`, `confidential` or `restricted` — in
  `<data_dir>/auth/serving-profile`, beside the bearer token, mode 0600 **inside
  a 0700 directory** — both are checked, because a directory's write bit governs
  replacing an entry in it, so the mode on the file buys nothing the directory
  has already given away. It is deliberately **not** read from a project's
  Git-tracked `.theurian/config.yaml`: repository contributors are an untrusted
  actor class, and a committed ceiling would make *raising* it a
  contributor-authored access-control change. An absent file is the ordinary
  state and selects this build's default; a word the file does not recognise
  **refuses at startup** rather than falling back, because an access control that
  widens on a typo is not one, and the refusal names the four valid words without
  echoing more than the one it read.

  **Absence is decided by `lstat`, not by `Path.exists()`.** A profile that is a
  symlink is refused whether or not it resolves: `exists()` follows the link and
  answers `False` for a dangling one, so a deployment that had declared `public`
  silently widened back to the built-in ceiling the moment the target was
  deleted — the one malformed input on this path that defaulted instead of
  refusing. A profile that is present and cannot be opened — 0000, or owned by
  another account — is refused with a `chmod` remedy rather than escaping as a
  bare `PermissionError`, which is not a `TheurianError` and so left `theurian
  index build --json` and `theurian daemon start` with an empty stdout and a
  traceback (CP-2).

  Enforcement is three places deep, not a predicate: a build writes no chunk row
  and no summary node for an item above the ceiling, so the withheld text never
  reaches `chunks_fts`, `chunks_trigram`, `nodes_fts` or `nodes_trigram` where
  BM25 collection statistics would price the visible rows against it; `_scope` and
  `_node_scope` emit `sensitivity IN (…)` in the same statement as the match; and
  a `changeSensitivity` past the ceiling a published build ran under purges that
  item's rows in the same `migrate apply`. The canonical re-check on the item's
  *current* class stands behind all three.

  **Tenant and ACL group are a weaker claim and are recorded as one.** They are
  discharged degenerately — refused at write time, so nothing is stored to
  withhold — not enforced by any predicate.

  **Both operator surfaces can see the ceiling.** `theurian index status` gains
  `profileMismatch`, `profileUnrecorded`, `profileUnreadable`,
  `servedSensitivities` and `indexedSensitivities` — additive keys, so a client
  reading the existing ones is unaffected — and folds all three refusals into
  `stale`, with the profile's own remedy named ahead of every other when the file
  itself cannot be read. Without them a narrowed ceiling degraded every
  `knowledge.search` to an unranked scan with `indexed: false` while `index
  status` answered `stale: false` and an empty remedy, for the very build the
  search had refused. Both surfaces now read one comparison
  (`recorded_flavor_verdict`), because that pair was written twice and disagreed.
  `theurian doctor` gains a `serving-profile` step that names the level in force
  and reports a profile it cannot honour as a problem — `not-applicable` when
  none is declared, because declaring none is the ordinary state and no command
  clears it.

- **`system.capabilities` reports `sensitivityEnforcement: true`**
  ([#119](https://github.com/theurian/theurian/issues/119)). An eighth flag in the
  capability block, and a client should read it as changing what an *empty*
  answer means: no results can mean "withheld by this deployment's ceiling"
  rather than "nothing matched". It reports that the axis is enforced and
  **never which ceiling this deployment declares** — that word would tell a caller
  which levels it is not being shown, on a tool that resolves no project and
  passes no gate.

### Changed

- **BREAKING — the shipped default sensitivity ceiling is `internal`, so
  `confidential` and `restricted` knowledge is withheld until an operator raises
  it** ([#119](https://github.com/theurian/theurian/issues/119), ADR-0025).
  **Old behaviour:** every level was served, because nothing read the label.
  **New behaviour:** a deployment that declares no serving profile serves `public`
  and `internal` and withholds the rest — from `knowledge.search`, from
  `knowledge.get`, from `knowledge.status`'s counts, and from the index build
  itself. Every existing installation that upgrades loses results it used to get,
  with no configuration change on its part.

  **Remedy, if a deployment is entitled to the whole corpus:**

  ```sh
  echo restricted > ~/.theurian/auth/serving-profile
  chmod 600 ~/.theurian/auth/serving-profile
  theurian index build   # a build is specific to the ceiling it ran under
  ```

  **Why the default is restrictive rather than permissive.** Measured on a
  resident loopback daemon serving this repository's own mixed-sensitivity corpus
  (82 items, 6 of them `confidential`), a default-parameter `knowledge.search`
  returned four `confidential` items ranked and excerpted in its top six, and
  `knowledge.get` served a 5,058-character `confidential` body. A permissive
  default is what made that the shipped behaviour. An operator surprised by fewer
  results has been told something true; an operator surprised by a `confidential`
  excerpt has not.

- **BREAKING — `INDEX_SCHEMA_VERSION` goes 5 → 6, so every existing index takes
  the mismatch fallback until it is rebuilt**
  ([#119](https://github.com/theurian/theurian/issues/119)). A version-5 index
  predates the build-side exclusion and may hold above-ceiling text, and **no
  pointer field can establish what such a build excluded** — the flavor it would
  have to record was not written. The bump makes those files unusable by
  construction rather than filtered on read: `knowledge.search` reports
  `fallbackReason: index-schema-mismatch` and answers from the gated canonical
  scan until `theurian index build` runs. No canonical schema change and no new
  index columns; the columns already existed and were already written.

- **BREAKING — a new `fallbackReason`, `serving-profile-mismatch`, joins the
  published enum** ([#119](https://github.com/theurian/theurian/issues/119),
  `schemas/mcp/retrieval-metadata.schema.json`). A client that exhaustively
  switches on that enum meets a value it does not know. It fires when the
  published build's recorded disclosure flavor differs from the ceiling in force —
  the state an operator reaches by changing the ceiling without rebuilding — and
  the ranked path stands aside to the canonical scan, which carries the same grant
  as a SQL predicate. **Both directions refuse**: a wider build would price the
  rows it does return against text this deployment does not serve, which no
  read-time filter can undo, and a narrower one is missing rows the deployment
  serves and would answer with a silence a caller reads as "this team has made no
  such decision". Neither the reason nor its `note` names a level. Remedy:
  `theurian index build`.

- **BREAKING — `knowledge.status`'s `itemCount` and `itemsByStatus` are narrowed
  by the ceiling** ([#119](https://github.com/theurian/theurian/issues/119)). They
  are a statistic over rows the caller may not see — the disclosure-family member
  reached through a tool nobody checking `knowledge.search` would think to call —
  so they follow the grant. A caller under an `internal` ceiling is told how much
  `internal` knowledge a project holds and learns nothing about the rest, not even
  a total: `itemCount` is the sum of the narrowed breakdown rather than the
  store's size, so no count restores the withheld population by subtraction.

  **The `#30` integrity comparison deliberately does not follow it.**
  `expected_surfaceable_count` is written ceiling-blind by `migrate apply`, so
  comparing a narrowed live count against it makes a *healthy* restricted
  deployment report `damageDetected` from its own ceiling — measured, which is why
  that comparison now runs its own ungated count internally instead of reusing the
  published sum.

- **`theurian propose accept` refuses a secret in the migration document, not
  only in a body**
  ([#336](https://github.com/theurian/theurian/issues/336)). A tightening of the
  approval gate, not a wire-contract break — `secretFindings` keeps its shape and
  no read is affected — but a behaviour a caller can observe, so it is recorded
  here as well as in the security entry above. **Old
  shape:** an acceptance was refused under `security.secretScan: block` only
  when an incoming *body file* appeared to carry a secret, and every entry in
  `accept --json`'s `secretFindings` began with a body path relative to
  `.theurian/knowledge/`. **New shape:** the migration document's author-written
  fields are refused on the same terms, and a `secretFindings` entry may instead
  name a field of that document — `migration.operations[1].metadata.title` — in
  the same `<location>:<line>:<column>: <family> (<prefix>)` line. A project
  whose existing proposals carry a high-entropy title, label or source URI will
  see `accept` exit 1 where it exited 0, and the escape hatch is the policy key
  the refusal's own remedy names: there is still no per-finding suppression.

### Fixed

- **A credential glued behind a lower-case prefix is no longer invisible to the
  SEC-11 secret scan**
  ([#350](https://github.com/theurian/theurian/issues/350)). `staging-sk-<40 hex>`
  in a migration document's `title` — or the same shape in a body — was scanned at
  the default `block` policy, reported nothing, and was accepted, while the bare
  `sk-<40 hex>` beside it was refused. The field was in scope and the scan ran;
  what failed is the detector. The delimiter is a candidate-class character, so
  the whole thing is one 51-character run to the generic high-entropy family; that
  family is declared last, but the engine takes the leftmost match, so it consumes
  the run at its first position and is then refused by the class gate for want of
  an upper-case character. `finditer` resumes *after* what the refused branch
  consumed, so the word boundary the internal `-` provides — exactly where
  `openai-api-key` would have matched — was never tried.

  **A refused candidate is now searched again with the specific families alone,
  over the document and not over a copy of the run.** The second pass bounds
  `finditer` with `pos` and `endpos`. `pos` is what carries the boundary: it
  restricts where a match may begin while leaving `\b` and any lookbehind reading
  the character *before* it. A slice does not — it throws that character away and
  fabricates a word boundary at its own position 0, and `\b` is Unicode-aware
  where the candidate class is ASCII, so a run preceded by a non-ASCII word
  character has no boundary in the document and gains one in a slice. Measured
  against the slicing version this branch first carried, ordinary Japanese prose —
  `監視対象sk-ingest-pipeline-primary-2026q1` — reported an `openai-api-key` the
  text does not contain, refusing a clean proposal under the default `block`. It
  reports nothing now, while the same slug behind an ASCII boundary still does.
  `endpos` does a different job and is not the mirror of `pos`: it truncates the
  right, so a lookahead genuinely cannot see past it, and what it is there for is
  confinement. It buys two things. Without it the first refused run's rescan runs
  on to the end of the document and reports a *later* run's credential too, at
  that run's offset — a duplicate, and out of the document order `scan_text`
  publishes. And it is what actually holds member 2 below for
  `private-key-block`, the one family whose *body* leaves the candidate class
  rather than only its lookahead: a match there can begin inside a run and end
  past it, so no lookahead argument covers it. On
  `staging-deployment-secrets-----BEGIN RSA PRIVATE KEY-----`, whose run the gate
  refuses, the bounded rescan finds nothing while an unbounded one finds a
  `private-key-block` running from 26 to 57, well past the run that admitted it.
  The trailing lookaheads themselves succeed at a run's end because the run is
  maximal in the candidate class, not because `endpos` is there. Only *refused*
  candidates are re-examined: a run that clears the heuristic is already reported
  as `high-entropy-token`, and reporting it again under an inner family would make
  one value two findings at two positions.

  **A recovered match must also carry an ASCII digit.** Reaching inside a refused
  run reaches inside text that is mostly English, which is where a family's prefix
  turns up by accident: `i18n-sk-locale-and-translation-notes` and
  `task-sk-review-the-ranking-heuristics` were each reported as an
  `openai-api-key`, refusing a proposal that carries no credential and telling its
  author to rotate a secret that does not exist. The test is the family's *matched
  substring*, not the run around it: the first of those carries a digit in `i18n`
  and is refused anyway, because what matched is `sk-locale-and-translation-notes`.
  Six of the seven declared families' fixture credentials carry a digit — every
  family that can match inside a candidate run; the seventh, `private-key-block`,
  needs spaces and never matches within a run anyway. The gate applies to
  *recovered* matches only — the outer pass is left exactly as it was, so this is
  a false-positive fix and not a quiet change to what a top-level match reports.

  Findings recovered this way are charged against the same `max_findings` ceiling
  as the outer pass, because one refused run can hold many inner matches: forty
  `sk_live_` credentials behind a single `staging-` prefix answer twenty findings,
  not forty.

  **What is still missed, by root cause.** The module's docstrings are the
  reference; this is the summary. (1) *No word boundary before the family's prefix
  in the document* — ASCII glue (`stagingsk-<40 hex>`) and CJK glue
  (`証sk-<40 hex>`) are one residual and not two, and reaching either means
  matching a prefix at an arbitrary offset, which reports `risk-`, `task-` and
  `disk-` as credentials. (2) *A family whose pattern needs characters outside the
  candidate class can never match within a run* — today only `private-key-block`,
  whose body leaves the class and which `endpos` is what stops from ending past
  the run; it costs little, because a real PEM key's payload is long and
  mixed-case and the generic family reports it. (3) *A credential inside a run is
  reached only if its family can end where the run lets it, from a distance the
  family can span* — two independent properties of the patterns, and reading
  either one alone puts a family in the wrong bucket. **Where a match may end** is
  the trailing lookahead: one that admits exactly the candidate class leaves only
  the run's end, which measured over all 64 candidate characters is
  `openai-api-key` and `google-api-key`; the other four also end at a character
  their lookahead omits — `-` and `_` for `aws-access-key-id`, `github-token` and
  `stripe-secret-key`, `_` for `slack-token`. **How far it reaches** is the
  repetition: a *fixed* count has exactly one end offset — `aws-access-key-id`
  matches 20 characters and `google-api-key` 39, always — while an *open* one
  reaches `len(prefix) + 255`, which is 258 for `openai-api-key`, 259 for
  `github-token`, 260 for `slack-token` and 263 for `stripe-secret-key`. The four
  quadrants, each measured: *fixed and equal* (`google-api-key`) loses the family
  to **all 64** glue characters, so any glue at all; *fixed and strict-subset*
  (`aws-access-key-id`) to **62 of 64**, only `-` and `_` surviving — two
  characters, not a difference of kind, which is exactly what reading the lookahead
  column alone got wrong about this family here; *open and equal*
  (`openai-api-key`) reports at 258 from `sk-` and is silent at 259, the
  credential's own body spending that distance as readily as a trailing slug; and
  *open and strict-subset* (`github-token`, `slack-token`, `stripe-secret-key`)
  needs either an omitted delimiter — which reaches the family through a
  400-character tail — or the run's end within reach, measured at 259/260,
  260/261 and 263/264.
  **Losing the family is not always losing the finding:** where the generic gate
  passes, the run is still reported as `high-entropy-token` and `block` still
  refuses, so what is lost is precision — `AIza<35>-x` measures exactly that.
  Silence needs a run the credential's own characters do not carry past the gate: a
  *low-entropy* tail, or a run with no digit at all. **Lower case is not the
  property.** An AWS key followed by twenty-four *identical* lower-case characters
  scans to nothing — the module records that acceptance landing through the real
  CLI at the default `block` — while twenty-four *distinct* ones after the same key
  clear the entropy floor and are reported. For the *open* families the reach is
  the ReDoS budget this module spends elsewhere and is not moved to buy them back;
  for the two *fixed* families there is no such excuse, since a fixed count
  consumes no backtracking budget at all — narrowing `google-api-key`'s lookahead
  measured free, and would move it into `aws-access-key-id`'s quadrant, from 64
  killing characters to 62, which is
  [#356](https://github.com/theurian/theurian/issues/356). (4) *A match is
  leftmost-greedy and non-overlapping at either pass, so a credential inside a
  span another match consumed is not reported* — in the rescan,
  `backup-xoxb-<digits>-sk-<40 hex>` reports one `slack-token` and not the `sk-`
  credential inside it; at the outer pass, which had this face before the module
  grew a rescan, `sk-<40 hex>-ghp_<36>` reports one `openai-api-key` and never the
  GitHub token inside the span it consumed, though that token reports on its own.
  Under `block` the refusal still fires; under `warn` the published list
  under-reports. (5) *The digit gate itself, in both directions* — a real
  credential whose recovered match carries no ASCII digit is dropped, at a rate
  that is per family rather than one number, falling with the token's length and
  rising with the letter share of the issuer's alphabet: simulated against
  *assumed* alphabets, ≈0.02% for a 48-character alphanumeric key against a few
  percent for the short-token families. And a digit-*bearing* English-ish slug
  (`staging-sk-ingest-pipeline-primary-2026q1`) still reports. This bounds the
  gate [#336](https://github.com/theurian/theurian/issues/336) widened rather than
  adding one: the detector is still best effort, and SEC-11 still disclaims being
  a complete secret scanner.

  **The second pass is a constant factor, not a new complexity class — and every
  figure below is per body.** Measured 2026-08-25 on CPython 3.13, paired against
  the pre-fix module: the worst input found — `sk-` repeated to the 8 MiB
  `MAX_SOURCE_FILE_BYTES` ceiling, which satisfies that family's `\bsk-` anchor at
  every third character and yields **no findings at all** — costs ≈8.7 s against
  0.28 s, and the same input at 1, 2, 4 and 8 MiB costs 1.08–1.09 s per MiB at
  every size. Nothing shaped like a real body pays it: this repository's largest
  committed knowledge body, 132,811 characters, measures 0.018 s either way,
  because the cost lands only on runs the heuristic refuses and only where a
  family's anchor is repeatedly satisfied inside one. **An acceptance is that
  per-body figure times the number of bodies**, since `accept` scans once per body
  file plus once over the migration document's fields and neither the schema's
  `operations` array nor the service bounds how many bodies one proposal carries —
  eight scans of a 2 MiB worst-case body measured ≈17 s. `propose accept` is a
  local, interactive command, so this is a recorded cost rather than a denial of
  service, and the bound that does exist is `MAX_SOURCE_FILE_BYTES` on each body
  rather than on their number. The full table is in the module's docstring.

### Security

- **A `--raptor` retrieval index whose withdrawal purge failed is no longer
  served** (`GHSA-97q9-xxfg-33r6`; T-17a in
  [the threat model](../../docs/security/threat-model.md)). A build left stale by
  a failed withdrawal or reclassification purge could still answer queries,
  disclosing withheld content two ways: a visible hit's `raptorPath` title
  carried the withheld document's text verbatim, and the stale build's BM25
  collection statistics still priced the visible rows against the withheld ones.
  Fixed by refusing to serve a build whose purge did not complete — the
  active-index pointer is marked tainted and the serve path stands the build
  aside to the unranked canonical scan until `theurian index build` rebuilds it.
  **HIGH**, reachable only with a non-default `--raptor` build together with a
  purge failure; no release is yanked.

- **`sensitivity` stops being a published label and becomes an enforced read
  control** ([#119](https://github.com/theurian/theurian/issues/119), ADR-0025;
  T-17a in [the threat model](../../docs/security/threat-model.md), FR-R1,
  SEC-13, SEC-14). It was published on every result and filtered on by no query,
  which by this project's own grading is a claim that misleads a security
  decision. The four parts are listed under Added and Changed above; what this
  entry records is that T-17a's closure now covers the sensitivity axis as well
  as the status axis, and by a stronger mechanism — the status axis is closed
  after the fact by a purge, this one **by construction at build time**, so all
  four BM25 scoring surfaces are covered by rows that do not exist.

  **Two residuals, recorded rather than closed.** A canonical read's cost grows
  with the above-ceiling rows it withholds, because `idx_items_status` does not
  carry the `sensitivity` column: 0.20 µs per above-ceiling row on the unranked
  scan and 0.54 µs on `knowledge.status`'s counts, in-process, corpus-bounded, no
  recovery of content demonstrated — new threat-model entry **T-22**, flattening
  owned by [#338](https://github.com/theurian/theurian/issues/338). And a purged
  build can hold the withheld body's raw bytes in SQLite free pages, since
  `DELETE` frees without zeroing and `backup` page-copies the free list; no query
  reads a free page, a `deprecateItem` control shows it predates this trigger, and
  it is [#344](https://github.com/theurian/theurian/issues/344).

- **The accept-path secret scan reads the migration document, not only the
  bodies** ([#336](https://github.com/theurian/theurian/issues/336), SEC-11,
  ADR-0027 decision 3 as amended; T-15 in
  [the threat model](../../docs/security/threat-model.md)). Until now `theurian
  propose accept` scanned the body files a proposal would land and nothing else,
  so a credential in the revision's own metadata was accepted and committed
  unread. That is the wider channel of the two: a body is reviewed as a file in
  a pull request, while a title is skimmed as one line of YAML beside a ULID —
  and the title and the published source anchors (`provider`, `sourceUri`,
  `repository`, `commitSha`, `filePath`) appear verbatim on every
  `knowledge.search` and `knowledge.get` result, so a credential in one reaches
  an agent that never opens the body.

  **What is scanned now** — author-written string *values*, not a YAML comment
  and not the filename a `contentFile` points at (the artifact-level face,
  tracked in [#349](https://github.com/theurian/theurian/issues/349)). The
  migration's `author`, `createdAt` and `description`; on every operation the
  free text and the names an author chooses (`reason`, `note`, `alias`, `specId`,
  `sourceUri`, `format`, `description`, `sourceItemId`, `targetItemId`,
  `supersededBy`, `itemId`, `namespace`, `owner`); a revision's `title`,
  `namespace`, `owner`, `tenantId`, `aclGroup`, `contentType`, `validFrom`,
  `validTo`, `labels` and `scope.paths`; and every string of a source anchor —
  `provider`, `sourceUri`, `filePath`, `repository`, `externalId`, `commitSha`,
  `blobSha` — wherever an anchor appears, including `addEvidence`'s. All fourteen
  operation types the published schema declares, not only the two `propose`
  writes. **The allowlist is the schema's string fields minus the derived half,
  not a list of the fields somebody thought of**: each of the fourteen operation
  branches and each leaf object (anchors, metadata) declares
  `additionalProperties: false` — the `$defs/operation` `oneOf` wrapper does not
  itself, but every branch it selects does — so the strings an acceptable
  document may carry are exactly the ones those objects name.

  **What is not.** The derived half, each field excluded by a mechanism rather
  than by choice: the ULID- and `^[0-9a-f]{64}$`-shaped identifiers
  (`id`, `revisionId`, `expectedRevision`, `dependsOn`,
  `contentSha256`), which the detector's class gate cannot fire on; the fixed
  vocabularies (`op`, `kind`, `status`, `trustLevel`, `sensitivity` and the
  other enums); and `contentFile`, a path whose secret-in-filename face is the
  artifact-level one above. The date fields (`createdAt`, `validFrom`,
  `validTo`) are *not* excluded — they are scanned, because a committed secret
  in one was reproduced verbatim by the rehearsal's date parse and scanning
  pre-empts it with a redacted refusal under `block`.
  A proposal's `evidence.json` is not scanned either: `accept` never moves it
  into the canonical tree, so it rides with the draft-time advisory
  ([#330](https://github.com/theurian/theurian/issues/330)). Ingest-time and
  index-time scanning still do not exist
  ([#329](https://github.com/theurian/theurian/issues/329)), a migration written
  straight into `.theurian/migrations/` still never meets the scan, and the
  detector is still best effort. T-15 stays **High** for those reasons: this
  widens the one gate of three that was already covered.

  **Nothing about the policy changes.** `block`, `warn` and `off` mean what they
  meant, `block` is still what an absent key and an absent file select, and the
  policy is still read before either input is touched — so `off` skips the
  document as well as the bodies, which is what keeps the escape hatch a
  whole-control escape hatch. The scan still runs before the pre-check and before
  any write, so a refusal consumes nothing. Body findings and document findings
  are one list under one `_MAX_NAMES_LISTED` cap, not a cap each.

  **A finding names a location and never reproduces the value.** Measured
  2026-08-24 against the real CLI in a scratch project: a secret in `--title`
  refuses at the default `block` with exit 1, `.theurian/migrations/` and
  `.theurian/knowledge/` untouched and the proposal directory intact; under
  `warn` the same acceptance exits 0 and `secretFindings` carries
  `migration.operations[1].metadata.title:1:14: high-entropy-token (fMlA...)`.

  **No false positives on real documents**, which is what a `block` default has
  to earn: zero findings over the migration corpus this repository tracks — the
  26 documents under `.theurian/migrations/` and the 2 under
  `examples/sample-project/`, 510 author-written strings (measured against
  `67727eb`). The live dogfood machine's fuller corpus of 82 (those 26 plus 56
  machine-local operator notes) scans clean too, but is not reproducible from
  the repository. The detector's ULID subtraction is what makes that possible,
  and a title citing the migration that introduced an item is pinned as an
  accepted input rather than left to chance.


## [0.1.0.dev10] - 2026-08-24

### Added

- **SEC-11 ships as a real control: `theurian propose accept` scans every body
  it would land for secrets** ([#198](https://github.com/theurian/theurian/issues/198),
  ADR-0027 decision 3). The policy is `security.secretScan` in
  `.theurian/config.yaml`: `block` refuses the acceptance and consumes nothing,
  `warn` accepts and reports every finding on the result, `off` skips the scan.
  `block` is also what an absent key and an absent config file select, so a
  project that configures nothing is scanned. An unrecognised value refuses
  rather than coercing to `block` — a typo about a security control that
  silently selects the strictest setting is a typo nobody ever finds.

  **This is the first key of `.theurian/config.yaml` that anything in `src/`
  reads.** Nothing read that file at all before this change — the state
  [#129](https://github.com/theurian/theurian/issues/129) measured, and which it
  was closed by correcting the prose about rather than by adding a reader.
  `security.secretScan` now publishes `default: "block"`, because there is at
  last an applied behaviour for a published default to document.
  `security.maxSourceFileBytes` and `providers.review.repositories` are still
  read by nothing.

  **Write `off` quoted.** PyYAML implements YAML 1.1, whose implicit resolver
  reads a bare `off` — and `no`, and `false` — as the boolean `False`, so the
  value the enum publishes, written the obvious way, never arrives as the string
  it looks like. It is refused with the quoting cure rather than translated:
  `off`, `no` and `false` are indistinguishable once parsed, so reading `False`
  as "off" would disable a security control for an operator who wrote `no` and
  meant something else.

  **What it is not.** The detector is in-house and takes no new dependency
  (ADR-0014): pattern families for known credential shapes plus a Shannon-entropy
  heuristic over candidate tokens. It is best effort, it will miss things and
  fire on things that are not secrets, and there is no per-finding suppression —
  the escape hatch for a false positive is the policy key, which the refusal's
  own remedy names. Theurian is not a repository secret scanner and is not a
  replacement for one, which is the stance SECURITY.md published before this
  control existed and still publishes. It covers the **approval gate only**:
  `theurian ingest` and index building run no scan
  ([#329](https://github.com/theurian/theurian/issues/329)), a draft-time
  advisory does not exist
  ([#330](https://github.com/theurian/theurian/issues/330)), and a migration
  written straight into `.theurian/migrations/` never passes through `accept` at
  all. T-15 stays **High** for that reason, re-graded when #329 ships.
- **`theurian propose --local` drafts under `.theurian/proposals-local/`**
  ([#265](https://github.com/theurian/theurian/issues/265), ADR-0028), for an
  author whose draft must not leave the machine. `theurian init` writes that
  path into the managed `.gitignore` block, and **a committed ignore rule is
  inherited by every clone**, which is the one property the `.git/info/exclude`
  fence it replaces does not have. The directory is git-ignored and **not**
  derived: nothing rebuilds it, so `doctor` must never describe a force-added
  local proposal as a rebuildable artifact, and `init` creates it without a
  `.gitkeep`.

  `propose accept` reads both locations through one implementation — a second
  location must not become a second reader (SEC-7) — and **refuses a proposal id
  present in both**, naming both paths rather than picking one: two directories
  of that id can hold different bytes, and choosing silently would accept one
  while the author was reading the other.

  **What it is not.** `--local` covers the *proposal*, not the knowledge:
  accepting one still writes the migration and the body into their tracked
  locations, so it hides the pre-approval artifact and nothing after it. There
  is no overlay for locally-accepted knowledge
  ([#332](https://github.com/theurian/theurian/issues/332)), nothing grades a
  non-`public` proposal that a contributor commits from a public repository
  ([#333](https://github.com/theurian/theurian/issues/333)), and `git clean -xdf`
  deletes the directory — an accepted availability residual, recorded rather than
  fixed ([#334](https://github.com/theurian/theurian/issues/334)).

### Changed

- **BREAKING — `opUpsertRevision.contentSha256` is now required**
  ([#210](https://github.com/theurian/theurian/issues/210), ADR-0027 decision 1).
  **Old shape:** `$defs.opUpsertRevision.required` was
  `["op", "itemId", "revisionId", "contentFile", "metadata"]`; a revision that
  declared no pin loaded with the body's *current* hash adopted as though it had
  been pinned, and `migrate validate` warned at exit 0. **New shape:** the list
  is `["op", "itemId", "revisionId", "contentFile", "contentSha256", "metadata"]`,
  and an `upsertRevision` without a pin is a schema error at `migrate validate`
  and a load refusal — before anything is applied, and before it is merged. FR-K5's
  tamper evidence stops depending on the author having remembered.

  **`apiVersion` stays `theurian.dev/v1`.** ADR-0005 scopes the bump rule to
  adding an operation, and this adds none. The loader matches the version
  exactly, so publishing `theurian.dev/v2` would make every conforming document
  unreadable in exchange for separating an incompatible population that does not
  exist.

  **Nothing needed repair.** Every migration document tracked in this repository
  already pinned every `upsertRevision` — 28 of them, 26 in the dogfood corpus
  and 2 under `examples/sample-project/` — as did all 82 in the live dogfood
  project's working tree, measured 2026-08-23 against `main` @ `68e8a0b`.
  `ProposalService.draft` has never emitted an unpinned revision. What breaks is
  a **hand-written** migration that omits the field: it is now refused at load
  rather than silently adopted.
- **BREAKING — `migrate validate --json` no longer publishes
  `unpinnedRevisions`** ([#210](https://github.com/theurian/theurian/issues/210),
  ADR-0027 decision 1). A second, separate break riding the first: a
  published-field removal rather than a schema tightening. **Old shape:** the
  payload carried `unpinnedRevisions`, a list of warnings naming revisions that
  declared no pin. **New shape:** the key is absent. With the pin required that
  list is empty for every schema-valid input, and a permanently empty published
  field is a claim that its condition is still reachable. The domain flag
  `UpsertRevision.content_pinned` and the `unpinned_revisions()` helper go with
  it, having become a guard no real data reaches.
- **BREAKING (contract) — `theurian propose accept` validates before it moves,
  and a refusal consumes nothing** ([#307](https://github.com/theurian/theurian/issues/307),
  ADR-0027 decision 2). **Old shape:** `accept` moved the proposal's files into
  `.theurian/migrations/` and `.theurian/knowledge/` and deleted the proposal
  directory, without validating what it moved; a self-inconsistent proposal
  landed, broke `migrate validate` project-wide, and was no longer available to
  re-accept because its sources were gone. **New shape:** before anything moves,
  `accept` proves that the union of the landed migration set and the incoming
  proposal survives the same pipeline `migrate apply` runs — the published
  schema and the document limits, the proposal's self-consistency, the whole-set
  guards `migrate validate` runs, and a dry replay of landed ∪ incoming against
  a throwaway store. If any stage refuses, the proposal directory is left exactly
  as it was, so the change is corrected and accepted rather than re-drafted from
  nothing.

  **The replay invokes the same engine path `migrate apply` invokes**, differing
  only in the write target, rather than re-implementing the checks. Two detectors
  deriving one fact independently agree until they do not, and the disagreement
  would surface as "`accept` said yes and `apply` said no" with nothing to
  arbitrate. `test_propose_cli.py::test_the_accept_replay_and_migrate_apply_reach_one_apply_function`
  and `::test_the_accept_pre_check_reaches_the_loaders_own_entry_points_and_every_guard`
  walk the call graph rather than compare answers on a fixture, so a
  re-implementation that agrees today still fails them.

  That closes #307's three demonstrated faces — two operations naming one
  `contentFile`, a self-pin that mismatches its own body, and `contentFile: ""`
  — and a fourth that measurement turned up: two proposals drafted before either
  acceptance, each claiming an item's first revision, produce a set that
  `migrate validate` calls green and `migrate apply` refuses permanently, with
  both proposals already consumed. Stages short of the replay do not catch it.

  **A fault in the *landed* set is now a distinct failure.** It raises
  `ApprovedSetUnusableError` at **exit 4**, not proposal-at-fault **exit 1**:
  exit 1's contract is "nothing landed, and drafting again is the recovery", and
  the second half is false when the proposal is not the cause — a second draft
  mints a duplicate migration for a fault it does not have (#89). Its remedy
  names neither the proposal nor a re-draft, and points into
  `.theurian/migrations/` at what the message itself names.

  **Cost, measured rather than estimated**: a full `migrate apply` over the live
  dogfood corpus — 82 migrations, 164 operations — took 0.55 s wall against
  `migrate validate`'s 0.56 s load-and-guards pass over the same corpus (measured
  2026-08-23 on a development machine, against a scratch copy). The replay is
  indistinguishable from the work the guard stage already does; the cost is
  process startup.

  **What it is not.** Only `accept` gained the replay, so a validate-green,
  apply-red-forever set stays reachable for a migration placed into
  `.theurian/migrations/` by hand. Two `accept` invocations racing at the process
  level remain unaddressed, and this change *lengthens* the interval between
  examining and moving by the replay's own duration (ADR-0018). No CI job applies
  the committed corpus ([#325](https://github.com/theurian/theurian/issues/325)).
- **Every install command Theurian prints now names the interpreter as well as
  the extra**: `uv tool install --python 3.13 'theurian[daemon]'` and
  `pipx install --python 3.13 'theurian[daemon]'`
  ([#323](https://github.com/theurian/theurian/pull/323)). These are the remedies
  a user actually copies, and they previously left the interpreter to whatever
  the installer picked by default, while `requires-python` has been `>=3.13`
  throughout and the README's quick start had said so since it was written. Not
  breaking: the old commands are not rejected, they are unqualified, and the
  qualified ones are what the product now recommends.

  Three surfaces read `theurian.domain.extras.DAEMON_INSTALLERS` rather than
  spelling a command of their own, so their answers cannot drift apart:

  - `core-present`'s detail, when `theurian setup` finds no Core.
  - the daemon-extra remedy the CLI prints when a bare install reaches
    `ModuleNotFoundError: No module named 'uvicorn'`. Its pipx form is
    `pipx install --force --python 3.13 'theurian[daemon]'` — `--force` because
    a plain `pipx install` over an existing installation reports success and
    changes nothing, which is measured and recorded in `domain/extras.py`. The
    `--python 3.13` in that one command is **not** covered by that measurement;
    it is there so the repair command cannot choose a different interpreter from
    the install commands beside it.
  - `domain/compatibility.py`'s `core-missing` remedy, published as the string
    third-party plugins implement against in
    [`docs/protocol/plugin-core-compatibility.md`](../../docs/protocol/plugin-core-compatibility.md),
    whose outcome table and flowchart carry the same pair. No production caller
    reaches that branch — its one call site always passes a parsed version — so
    the copy a user actually meets is the plugin's `SessionStart` hook, which
    carries the same advice and moved with it.

  The fourth, `theurian setup --help`, spells the two commands in its docstring
  instead of reading the constant, and is held to `core-present`'s own words by
  `test_setup_claims.py::test_the_installers_pinned_here_are_the_ones_the_step_reports`.

  `3.13` is now held to `requires-python` by
  `test_daemon_extra.py::test_the_install_commands_pin_the_python_core_requires`,
  which reads the floor out of `pyproject.toml`. Raising the floor without
  touching `domain/extras.py` would otherwise ship, as the remedy for an install
  that did not work, a command pinning an interpreter the only wheel it may
  install rejects.

  The written surfaces follow: this package's README — whose install line also
  moves from `theurian[all]` to `theurian[daemon]`, matching what the root README
  recommends and what the extra is actually for — the plugin's README,
  `SessionStart` hook, `setup` and `upgrade` commands, ADR-0014, the macOS
  packaging note, `docs/contributing/release.md`, and the `Install:` line
  `release-core.yml` writes into every GitHub release body — which now reads
  `uv tool install --python 3.13 'theurian[daemon]==<version>'`. That release
  line and the release document were the last two surfaces still naming the bare
  command; T-16 in
  [the threat model](../../docs/security/threat-model.md) recorded them as a
  deferral discharged by
  [#71](https://github.com/theurian/theurian/pull/71) and records them as closed
  here.

### Fixed

- **`InputTooLargeError` now carries its own remedy** telling the user which
  input exceeded a size limit and to shrink or split it before retrying,
  instead of falling through to the generic "run this inside an initialised
  Theurian project" diagnosis `resolve_context` gives every error with no
  remedy of its own ([#287](https://github.com/theurian/theurian/issues/287)).
- **A source file whose size bounds nothing is refused unread**
  ([#215](https://github.com/theurian/theurian/issues/215)). `read_source_file`
  enforced SEC-8's byte cap from `st_size`, and `st_size` bounds what a read
  returns only for a regular file: a FIFO reports 0, passes the cap, and then
  blocks in `open()` until a writer appears. Measured against the real CLI in a
  sandboxed `HOME` and `THEURIAN_DATA_DIR`, a migration whose `contentFile`
  named a FIFO made `theurian migrate validate --json` produce no output and no
  exit within 15 seconds — worse than the raw traceback
  [#205](https://github.com/theurian/theurian/issues/205) closed on this same
  seam, because a hang cannot even be graded. A FIFO, a socket and a device now
  raise `IrregularSourceFileError`, which names the shape and carries its own
  remedy, so every caller already guarding `TheurianError` inherits the CP-2
  `{error, remedy}` payload without its own patch. The refusal is taken from
  `stat`, which answers from the directory entry and opens nothing, so it is
  reachable at the last point that is still free. A directory is deliberately
  not a member and a test pins the non-membership: `open()` refuses one outright
  with `EISDIR`, which can neither block nor stream, and that errno already
  selects a remedy naming the fault exactly.

  **The refusal names the referrer, never the `contentFile` value an author
  wrote** ([#233](https://github.com/theurian/theurian/issues/233)'s
  discipline). `read_source_file` publishes no path at all — its argument is
  attacker-influenceable and still in the caller's own spelling when this branch
  fires, because this is one of the two refusals that run *after* containment
  rather than before it — so a caller holding a name it has decided is safe to
  print re-raises with that attached: the `.theurian/migrations/` entry
  `iterdir()` returned, or, on the `theurian propose accept` path, the
  project-relative body path Theurian itself built from the proposal's ULID and
  the resolved `knowledge/` tail. **The same guard covers the accept path**: a
  proposal body that is a FIFO is refused there too, and `accept` now surfaces
  the refusing error's own remedy instead of overwriting every refusal that
  reaches it with one fixed sentence about the `contentFile` — which for this
  fault names the only object that is *not* wrong. A path escape and an
  oversized input on the same path gain their own cure by the same change.
- **The projection budget bounds what the walk spends, not only what it keeps**
  ([#232](https://github.com/theurian/theurian/issues/232)).
  `MAX_PROJECTION_CHARS` was checked after `_walk` had built and joined every
  line, so it capped the return value and nothing else. PyYAML resolves an alias
  by sharing the parsed object rather than copying it, so a document whose
  aliases nest parses cheaply and expands only when the walk materialises it
  into text. Measured against the truncating `project` the ingest path calls,
  with the returned string capped at 2 MiB throughout: 297 B of YAML cost 0.23 s
  and 71 MB of RSS, 351 B cost 2.05 s and 334 MB, and 405 B cost 19.76 s and
  2.8 GB. After the fix the same three cost 0.09 s and 43 MB. The budget is now
  threaded through the walk, which stops the moment it is passed, and characters
  are charged exactly as `"\n".join` will spend them — so stopping early and
  truncating afterwards produce the text the unbounded walk produced, verified
  byte-for-byte against the pre-change module over twelve shapes.

  **Behaviour change: a visited-node ceiling joins it**
  (`MAX_PROJECTION_NODES`, 1,000,000), because characters price only the walk's
  emitting end — a non-empty container emits nothing, so at `MAX_DEPTH` a
  document can spend two dozen visits per line. It carries a marker of its own,
  since a projection well under the size limit must not be labelled as one that
  did not fit. A document that exhausts the visits while its whole projection
  would have fitted is now refused by `project_checked` where it was accepted
  before: measured, a 422,040-byte YAML document raises at 1,000,001 visits in
  0.48 s while its full projection is 2,084,035 characters. Which of the two
  budgets binds first is a property of the prefix the walk has covered when one
  of them crosses, not of the document's overall ratio — measured, one
  document's key order alone decides which answers. `project_checked` raises on
  the same documents the character budget always did; what changes there is
  `observed`, now the spend at the stop rather than the size of a projection
  this no longer builds.
- **The `$ref` walk enters each parsed node once, however many aliases reach
  it** ([#245](https://github.com/theurian/theurian/issues/245)).
  `_external_refs` treated the parsed document as a tree, but a YAML alias is
  resolved by sharing the parsed object, so one sub-object can be reachable by
  exponentially many paths and every path was walked. Measured: 694 bytes of
  YAML at 22 alias levels cost 11.51 s while recording a single reference and
  reaching neither the depth nor the count cap — which is why neither cap could
  have stopped it, and `seen` deduplicates reference *strings* rather than
  bounding the traversal. A `descended` set of node ids makes the cost linear in
  the parsed graph instead of in the paths through it: 1.5 ms for that same
  document. Both existing caps are untouched, and where the memo sits is
  load-bearing in both directions, each with a test that fails when it moves —
  before the caps, so re-reaching a node the walk has already been inside
  records no truncation ("we did not look" would be false, and
  `unresolvedRefCount` would read 2 for a document holding one reference); and
  added only on an actual descent, never when a cap turned the node away, so a
  node cut past `MAX_REF_DEPTH` is still walked when a shallower path reaches
  it.

  **All three together, through the real CLI.** A sandboxed project holding a
  405-byte alias bomb under `.theurian/specifications/`, a 754-byte `$ref` bomb
  at 24 alias levels beside it, an ordinary Markdown note, and a FIFO under
  `.theurian/knowledge/`: `theurian ingest --json` took **114.60 s** against
  `main` (68e8a0b) and **0.37 s** on this branch, ingesting the same three
  documents with no failures either way (paired runs on one machine,
  2026-08-24). The FIFO costs neither run, because `IngestionService._discover`
  drops a non-regular file before anything opens it — silently, which is its own
  open issue ([#327](https://github.com/theurian/theurian/issues/327)) and not
  closed here.

### Security

- **A single field rendered with `repr` re-expands a YAML alias chain that parsed
  small** ([#210](https://github.com/theurian/theurian/issues/210),
  [#316](https://github.com/theurian/theurian/issues/316), ADR-0027; T-6 in
  [the threat model](../../docs/security/threat-model.md)). `theurian propose
  accept` rendered two fields materialised from parsed YAML — `security.secretScan`
  read from `.theurian/config.yaml`, and a proposal migration's `id` — with `repr`
  before any per-shared-reference bound applied. PyYAML collapses an alias to a
  shared object, so an alias chain parses in a few hundred bytes and re-expands
  exponentially only when a field is rendered: a hostile committed
  `.theurian/config.yaml` of 481 bytes drove 16.9 s and 4.8 GB of RSS before the
  fix, and a proposal migration whose `id` is an alias graph does the same
  (measured 2026-08-24 against the real CLI). The fix bounds each field with
  `is_bounded_scalar` before it is rendered — a container or an oversized scalar is
  refused first — so the same inputs now refuse in ~0.2 s with a bounded
  `{error, remedy}` document.

  **The migration-`id` face was a released defect in `0.1.0.dev9`**, where
  `_require_filename_matches_id` ran the render before schema validation. It is the
  same T-6 alias-re-expansion class the migration loader closed for whole documents
  ([#291](https://github.com/theurian/theurian/issues/291)) and the ingest
  projection closed in [#335](https://github.com/theurian/theurian/pull/335)
  ([#232](https://github.com/theurian/theurian/issues/232),
  [#245](https://github.com/theurian/theurian/issues/245)); it is now closed for
  these two single-field render sites too.
- **`theurian propose --local` brings the managed `.gitignore` block current
  before it writes a private body**
  ([#265](https://github.com/theurian/theurian/issues/265),
  [#316](https://github.com/theurian/theurian/issues/316), ADR-0028). `--local`
  drafts under `.theurian/proposals-local/`, which is git-ignored only if the
  managed `.gitignore` block carries that entry — true only for a project
  initialised with the ADR-0028 block. A project initialised before ADR-0028 —
  every project first set up under `0.1.0.dev9` or earlier — has a stale block, so
  `--local` wrote the private body into a Git-tracked directory while the command
  asserted it was ignored, and `doctor`'s gitignore step reported `satisfied`
  because it compared a substring rather than the managed entries.

  **Fixed** by refreshing the managed block before the write — in the service
  `draft(local=True)`, so a future MCP composition root inherits the same guard —
  or refusing with a remedy if `.gitignore` cannot be written; `doctor`'s gitignore
  probe now compares against the managed entries.

  **Behaviour change: `draft(local=True)` now writes `.gitignore`.** A local draft
  that previously touched only `.theurian/proposals-local/` now also updates the
  managed `.gitignore` block, which is what makes the confidentiality claim true
  before the body lands.

### Documentation

- **T-6's ingestion controls reconciled with what `src/` enforces**
  ([#199](https://github.com/theurian/theurian/issues/199)). Every remaining
  line of the threat model's Controls table names the symbol that implements it
  — `security/paths.py::MAX_SOURCE_FILE_BYTES` and `MAX_PATH_DEPTH`,
  `security/yaml_loading.py::MAX_YAML_BYTES` and `_StrictLoader`,
  `normalization/projection.py::MAX_DEPTH`, `MAX_PROJECTION_CHARS` and
  `MAX_PROJECTION_NODES`, `parsers/openapi.py::MAX_REF_DEPTH`, `MAX_REFS` and
  `MAX_OPERATIONS` — and **two controls it listed do not exist**, each dropped
  with the search that settled it rather than filed: there is no *archive
  expansion ratio*, because nothing in `packages/theurian-core/src` unpacks an
  archive (no `zipfile`, `tarfile`, `gzip`, `zlib` or `shutil.unpack_archive`
  import appears anywhere in it), and no *parse wall clock*, because nothing
  there bounds any parse by time. Both were written in the indicative beside
  bounds that do run, which is exactly the shape a reader takes for a control
  that runs. `docs/architecture/source-normalization.md`'s bounds table is
  reconciled the same way — a third column saying whose each bound is, since
  three are enforced before any parser sees the bytes and three are the OpenAPI
  parser's alone — and both tables' figures are now pinned by tests, so prose
  cannot outlive the constant it quotes.
- **Two residuals are recorded rather than closed, each measured under T-6 in
  `docs/security/threat-model.md` and out of scope for this change.** The `$ref`
  walk builds one path string per edge and nothing charges it, which is
  quadratic in the document's own size — ~4.39 MiB in 16.93 s, bounded only by
  the 8 MiB file-size cap
  ([#328](https://github.com/theurian/theurian/issues/328)).
  `parsers/markdown.py::_FENCE` spans the whole document under `re.DOTALL`, so
  every fence opener that never closes scans to the end — 156.2 KiB in 25.12 s,
  four times the cost per doubling, with `MAX_FENCES` never engaging because
  such input yields no match to cap
  ([#331](https://github.com/theurian/theurian/issues/331)).
- **`unresolvedRefCount`'s published contract corrected.** It was documented as
  "a total when `refWalkTruncated` is false, and a lower bound for `$ref` when
  it is true"; the second half is false, because the number adds one record per
  truncation reason to the distinct references recorded and can therefore
  *over*count them — measured, a document holding no `$ref` at all, nested 66
  levels deep, publishes `externalRefs` empty and `unresolvedRefCount` 1. What
  it never undercounts is the *uninspected surface*, which is the property
  [#203](https://github.com/theurian/theurian/issues/203) needed: a subtree the
  walk declined to enter always leaves a record. The arithmetic is unchanged and
  deliberate — counting truncations is what stops a capped document answering
  "no external references" at all.

## [0.1.0.dev9] - 2026-08-22

### Fixed

- **`propose accept` translates every accept-path filesystem or path fault into
  an `{error, remedy}` document instead of escaping a raw traceback**
  ([#227](https://github.com/theurian/theurian/issues/227)). A proposal directory
  this process cannot list, stat or read; a migration file it cannot open; a
  `contentFile` whose path `resolve()` refuses (a NUL byte or an unpaired
  surrogate); an unwritable `.theurian/migrations/` — each used to leave `accept`
  as a bare `OSError`/`ValueError`, so `--json` published nothing where it
  promises a document. (An unreadable `evidence.json` is not in this set: it was
  already translated to an indeterminate document in 0.1.0.dev8,
  [#253](https://github.com/theurian/theurian/issues/253).) Each is now a `ProposalError` naming the offending path
  relative to the project root — never the absolute path, which is the machine's
  home directory (SEC-7) — whose remedy sends the reader to
  `.theurian/migrations/` before re-drafting: a refused read establishes nothing
  about whether the migration landed, and re-drafting an accepted proposal mints a
  duplicate migration (#89). A move that landed but whose trailing cleanup of the
  proposal's own copied files could not run (a read-only `0o555` proposal
  directory) now degrades to success with a `remedy` naming the leftover, rather
  than reporting a non-landing that would send the caller to re-draft.
- **`propose accept`'s replacement guard reads the loaded migration set and keys
  on the body's filesystem identity, closing a disagreement with the loader**
  ([#234](https://github.com/theurian/theurian/issues/234)). The guard that
  refuses a body replacement which would leave the migration set unable to
  validate used to enumerate `.theurian/migrations/*.yaml` itself and skip the
  symlinked entries the loader follows, so a pin held by a relocated (symlinked)
  migration was invisible to it: the replacement was allowed, and the set then
  stopped loading at exit 4 with no undo. It now reads the same `MigrationSet` the
  loader produced, and compares on the body's `(st_dev, st_ino)` rather than a
  path string.

  **Behaviour change:** `accept` now refuses four inputs it previously let
  through, each of which then broke the loaded set at exit 4:

  - a *different* revision replacing an **unpinned** landed body — the old guard
    filtered on the pin and waved this through, yet the set then holds two
    revisions on one physical file (`refuse_duplicate_content_files`, exit 4);
  - a byte-identical replacement of a **pinned** landed body under a *different*
    revision id — the old guard treated "byte-identical changes nothing" as true
    of the *set*, but it is the same two-revisions-on-one-file break;
  - a **hardlink** to a landed body — the identity key `(st_dev, st_ino)` reaches
    the same inode by every alias, where the old path-string compare saw none;
  - a **case variant** of a landed body's path (`RETRY-POLICY.md` against a landed
    `retry-policy.md`) — likewise one inode by every spelling. A Unicode
    normalisation variant reaches it the same way, though no test exercises that
    face.

  The one in-place re-declare `accept` still allows — this proposal's own revision
  re-declared byte-for-byte **on the same item**, the in-place status change of
  ADR-0024 decision 5 — is an explicit conjunction of equal item id, revision id
  and bytes. A byte-*different* re-declare of a *pinned* landed revision was
  already refused before this branch and is unchanged. There are no users yet, but
  the refusals change what `accept` exits on, so they are named here.
- **`propose accept` refuses a cross-item re-declare of a landed revision id**
  (round-one review finding). The replacement-guard skip that admits the one
  legitimate in-place re-declare (ADR-0024 decision 5) fired on two conjuncts —
  equal revision id and equal bytes — so a byte-identical body re-declared under a
  *different* item's id passed it. `accept` returned 0, and `migrate validate`
  passed too (it does not check cross-item revision ownership); then `migrate
  apply` refused the whole set at exit 4 ("a revision id belongs to one item",
  INV-1/SEC-13) after the pull request had merged and the proposal was already
  consumed, with no undo. The skip now carries the item id as a third conjunct
  (item id, revision id, bytes), moving that refusal to the accept door. No
  rejected-item content is disclosed either way — `store.py` refuses a cross-item
  revision-id reuse before it reads content (SEC-13) — so this spares the operator
  a consumed proposal; it does not add a disclosure control.
- **`propose accept` refuses a migration whose id the loaded set already holds
  under another filename** (round-one review finding). The "already in place"
  refusal keyed only on the destination filename (`<id>-<slug>.yaml`), but the
  loader keys migrations by their *inner* `id`. A hand-authored proposal named
  `<landed-id>-other-slug.yaml` carrying `id: <landed-id>` collided on the inner
  id while its filename was free: the name check waved it through, the
  filename/id agreement check was satisfied (the prefix equals the id), and
  `accept` landed a *duplicate* migration id, on which `migrate
  validate`/`status`/`apply` then all exit 4. The refusal now also answers the id
  against the same loaded migration set the loader, `migrate validate` and
  `migrate apply` read (#234/#253/#254). This is the third and last accept-path
  procedure moved off a filesystem enumeration and onto the loaded set; no method
  on the accept path enumerates `.theurian/migrations/` any more.
- **The accept-path read-failure remedy is chosen by `errno`, not a blanket
  `chmod`** (round-one review finding, extending
  [#227](https://github.com/theurian/theurian/issues/227)). The remedy prescribed
  "Make _path_ readable — chmod u+rX on it" for every accept-path read failure —
  the same over-claim [#233](https://github.com/theurian/theurian/issues/233)
  corrected for `PathEscapeError`, reopened here. A `contentFile` naming a
  directory raises `EISDIR`, which no `chmod` cures: the fault is the authored
  input, so the remedy now names the `contentFile` to correct and says nothing
  about permissions. A `stat`/`open` refused for a *child* is the parent directory
  lacking its search bit, so `chmod u+rX` on the child cures the wrong file — the
  remedy now points `chmod u+x` at the unsearchable directory instead.
  `EACCES`/`EPERM` still earn a `chmod`; a `None` errno is treated as
  non-permission, since a `chmod` prescribed for an unknown cause is the very
  over-claim this avoids. Every branch still points at `.theurian/migrations/`
  before any re-draft (#89).
- **Scope note, not a behaviour change: `propose accept` does not self-validate
  the proposal it is accepting**
  ([#307](https://github.com/theurian/theurian/issues/307)). The replacement
  guard's docstring claimed it holds the invariant that `accept` "never leaves the
  set unable to validate." That is false as a global invariant: `accept` does not
  schema-validate the incoming migration and does not check it against itself, so
  a self-contained breakage in a single proposal — two operations naming one
  `contentFile`, a self-inconsistent pin, an empty `contentFile` — lands and is
  caught by `migrate validate` in CI, which is the check by design (ADR-0013 §4).
  The docstring is narrowed to what the guard actually holds: it refuses a
  replacement that would break a pin *already landed* in the approved set, the one
  fault it can judge from the landed set alone. No behaviour changed here;
  hardening `accept` itself against a self-contained breakage is deferred to #307.
- **A migration-schema rejection echoed `jsonschema`'s raw `{instance!r}` and
  had dropped the schema-side fact that names the defect**
  ([#289](https://github.com/theurian/theurian/issues/289)). When a migration
  failed validation, the message interpolated the whole failing instance
  verbatim — unbounded, and with control characters unescaped — while no longer
  saying *what the schema expected*: the `const` a value must equal, the
  `pattern` it must match, or the unexpected keys an `additionalProperties:
  false` rejected. The rejection is now built at Theurian's own seam
  (`migration_loader.py`'s `_schema_rejection`), keeping that schema-side fact
  and bounding every author-written fragment it echoes to `MAX_ECHOED_VALUE`
  (1,000 characters) through a `reprlib`-based renderer that escapes control
  characters. This restores the diagnostic parity the pre-change wording had
  while removing the unbounded echo — a 100 KB value no longer renders a
  100,198-character refusal into a terminal.

- **An oversized scalar nested in a rejected operation reported its
  post-truncation length, not its true length**
  ([#289](https://github.com/theurian/theurian/issues/289)). When a rejected
  operation carried an oversized string, the echo truncated the container's
  render first, so the "N characters in all" count reported the *container
  repr's* length (~1,100), not the offending value's. The renderer now records
  the longest scalar's pre-truncation length and the rejection names that true
  length — the one number that is the diagnosis for a value refused for being
  large.

- **A hostile migration document could exhaust CPU or memory at validate time,
  and a giant-integer scalar escaped `--json` as a raw traceback**
  ([#291](https://github.com/theurian/theurian/issues/291)). Several shapes of a
  parsed migration document defeated `jsonschema`'s own message building at
  validate time. The loader now applies three bounds ahead of `validate` in its
  own un-memoised walk — nesting depth, node count, and total rendered magnitude
  — and translates a single giant integer by type after `validate` raises:
  - **Nesting** is refused ahead of `validate` at `MAX_DOCUMENT_NESTING` (64).
    Past the interpreter's C recursion budget `jsonschema` cannot build its own
    refusal message, and the `RecursionError` that follows is indistinguishable
    from a corrupt schema — so a deep *document* was answered "reinstall
    theurian". A schema-valid migration nests at most 7 levels.
  - **Branching alias expansion** is refused ahead of `validate` at
    `MAX_DOCUMENT_NODES` (100,000). A YAML anchor aliased into a doubling chain
    is a ~500-byte file whose expansion is 2^N nodes; `jsonschema`'s
    `{instance!r}` re-expands it the same way, building a 46 MB message from a
    500-byte file at alias level 22 (measured 2026-08-21, `jsonschema` 4.26.0).
    The walk deliberately does not collapse shared references — a collapsed
    count would wave the bomb through. Same un-memoised-walk shape as the OpenAPI
    `$ref` ref-walk in
    [#245](https://github.com/theurian/theurian/issues/245), in another seam.
  - **Total rendered magnitude** is refused ahead of `validate` at
    `MAX_DOCUMENT_RENDERED_CHARS` (1,000,000) — a new bound in the same walk that
    the node count could not stand in for. One large scalar aliased into many
    slots is only a handful of nodes, well under `MAX_DOCUMENT_NODES`, but
    `{instance!r}` re-expands it to N times its rendered width. The walk charges
    every leaf's width via a new O(1) `_rendered_width` per un-memoised reference
    — `len` for a `str`/`bytes`, a `bit_length`-derived decimal-digit estimate
    for an `int` (never `str(int)`, which is quadratic and raises past CPython's
    int→str limit), and the `repr` length of a bounded `bool`/`float`/`None`.
    This closes two aliased-scalar faces. The first is a large *string* aliased
    into N slots: a hundreds-of-gigabytes transient that raises `MemoryError`,
    which is neither a `ValueError` nor an `ArithmeticError` and so escaped the
    scalar catch below as a raw traceback. The second was found by the round-two
    review and missed by the round-one bound, which charged only `str`/`bytes`: a
    *medium integer* — a few thousand digits, so its own `repr` does not raise —
    aliased into N slots is O(N) nodes under `MAX_DOCUMENT_NODES` and not a
    `ValueError`, so it defeated every round-one control while `{instance!r}`
    re-emitted its digits once per slot, a `digits × slots`-character message.
    Charging every leaf per reference refuses both faces before `validate`. The
    refusal message still says "characters of string content", but the bound now
    covers every leaf type, integers and bytes included.
  - **A single giant integer** — one whose own `{instance!r}` render raises,
    past CPython's int→str limit (4300 digits by default) — is one node whose
    lone width passes the aggregate budget above, so it reaches `validate` and is
    *translated by type* rather than refused ahead. `jsonschema` renders it past
    that limit and raises `ValueError` (reachable today), or a float `multipleOf`
    coerces it and raises `OverflowError` (latent — the bundled schema carries
    only `minimum`/`maximum`). `_validate_document` catches the whole
    `(ValueError, ArithmeticError)` class as a `MigrationError` so a future
    numeric keyword cannot reopen the escape. This catch and the rendered budget
    are complementary, not redundant: the budget refuses the *aliased* integer
    whose aggregate render is large, and this catch handles the *single* integer
    whose lone render raises. The file-load path closes the same single-value
    face: a YAML integer literal past CPython's limit is now translated to the
    same bounded "reduce it" wording instead of forwarding CPython's message,
    which named `sys.set_int_max_str_digits()` — an interpreter tuning knob no
    migration author should reach for. As defence in depth, the rejection
    builder's `_echo` refuses an integer wider than `_MAX_ECHOED_INT_BITS`
    (2,000 bits) as a placeholder.

## [0.1.0.dev8] - 2026-08-20

### Added

- **`evidence.json` now records the `migrationId` and `itemId` its proposal
  drafted** ([#253](https://github.com/theurian/theurian/issues/253)). These are
  the two fields of that file Core reads back: together they let `propose accept`
  ask whether a migration with that id, operating on that item, is in
  `.theurian/migrations/`. Both are a contributor's claim, not authority —
  `evidence.json` is committed and untrusted (ADR-0013 point 7) — so `itemId` is
  the cross-check that stops a forged `migrationId` (one pointing at another
  proposal's landed migration) from reading as accepted. Optional on read: the 26
  proposals committed in this repository predate both fields and are diagnosed by
  best-effort inference, which their message says.

### Fixed

- **The installed migration schema resolved `$ref`s over the network, and its
  resolution failures escaped as raw tracebacks**
  ([#235](https://github.com/theurian/theurian/issues/235)). The migration
  schema was validated with `jsonschema`'s default registry, whose retrieve
  callable fetches an external `$ref` over the network
  (`urllib.request.urlopen`) at validate time — and reads a `file://` ref off
  disk the same way. An SSRF-shaped read gated only on the installed schema
  being corrupted or replaced (an operator-side precondition; `schema_root()`
  never reads user-project paths), and covered by no existing claim:
  `parsers/openapi.py`'s "external `$ref` targets are recorded, never fetched"
  governs *ingested* documents, not the schema this build ships. Every
  validator is now built with `referencing`'s `EMPTY_REGISTRY` — no retrieve
  callable — so an external `$ref` fails closed; internal `#/$defs/…` refs
  still resolve from the schema's own root resource, so the bundled schema
  validates unchanged. Separately, the resulting resolution failures — an
  unresolvable `$ref`/`$dynamicRef` raises `referencing.exceptions.Unresolvable`
  and a self-recursive or empty `$ref` raises `RecursionError`, neither a
  `ValidationError` — used to slip past every `except ValidationError` seam and
  reach `resolve_context` as a raw traceback under `--json`. Both are now
  translated to `SchemaUnreadableError`, the type every other
  installed-schema-corruption failure already carries, at both validate seams
  (`migrate validate` / `propose`, and any command that loads migrations from
  disk).
- **A path-containment refusal told the user to go somewhere they already were,
  and could instruct them to delete their own work**
  ([#233](https://github.com/theurian/theurian/issues/233)). With
  `.theurian/migrations` — or one `*.yaml` file inside it — a symlink pointing
  outside the project, `theurian migrate validate --json` printed
  `{"error": "Path escapes the permitted root /<absolute>/<project>", "remedy":
  "Run this inside an initialised Theurian project."}` at exit 1. The refusal
  itself was correct and is unchanged; what it said about itself was not.
  `PathEscapeError` set no `remedy` of its own, so the CLI's generic fallback
  won — the "exception that does not describe itself" shape
  [#205](https://github.com/theurian/theurian/issues/205) exists to end. It now
  names, relative to the project root and with the `!r` quoting its siblings
  use, *where* the problem is — and **no remedy names a file to delete.** That
  is the substantive change: an escape happens through a symbolic link somewhere
  on the entry's ancestor chain or its resolution chain, and which link it is
  cannot be determined from the entry alone. Three successive attempts to
  determine it anyway were each refuted by a deeper construction — the last by
  `x.yaml → y.yaml → outside`, where following the remedy deleted two
  Git-tracked files and ended at `valid: true` while the minimal cure was
  repointing `y` alone. So the remedy now states only what `lstat` proves — that
  the named entry is or is not itself a link — and then hands over the finite
  checklist: this entry, the directories above it, the links it resolves
  through. "Repoint that link, or remove that link" refers to whichever the
  reader finds; Theurian never names it. Related: an outside-pointing
  `.theurian` is reachable from a plain `git clone`
  ([#237](https://github.com/theurian/theurian/issues/237)). The offending path
  is still never echoed (SEC-7), and neither is the absolute project root.
- **A `contentFile` refusal no longer denies the commonest cure.** The
  path-escape remedy for a `contentFile` used to say that removing the `..` was
  "not the cure" — wrong for a plain over-traversal typo, which involves no
  symlink at all and is fixed by exactly that edit, and in conflict with the
  sibling `MigrationContentUnreadableError`, which tells the author to fix the
  path. It now offers both candidates: correct the path, or find the link
  something it traverses goes through.
- **Exit code:** a `PathEscapeError` raised while resolving a project now exits
  `EXIT_STATE_ERROR` (4) rather than 1 for the commands routed through
  `_require_project` — measured, all nine: `migrate validate`, `migrate status`,
  `migrate apply`, `index build`, `index status`, `index gc`, `ingest`,
  `propose` and `propose accept` — matching every sibling refusal on the same
  load path (`MigrationsDirectoryUnreadableError`,
  `MigrationFileUnreadableError`, `MigrationError`). A script keying on exit 1
  for those must be updated. `init` and `project register` call
  `resolve_context` directly and still exit 1; `project status` reports at exit
  0 as it always has. All three now carry the new remedy text.
- **A path refused only for nesting too deep no longer claims to have escaped**
  ([#233](https://github.com/theurian/theurian/issues/233)). `resolve_within_root`
  refuses a path past `MAX_PATH_DEPTH` whether or not it ever leaves the root,
  and reported it as `Path escapes the permitted root` — false for a path
  sitting entirely inside. It now raises `PathDepthExceededError` with its own
  message and a remedy about flattening the nesting. The new type is a
  `PathEscapeError` subclass, so every existing `except` and every exit-code
  route catches it unchanged; only what the caller is told differs.
- **An interrupted `theurian propose` was diagnosed as an accepted one**
  ([#253](https://github.com/theurian/theurian/issues/253)). `propose accept`
  read the presence of `evidence.json` as "this proposal has already been
  accepted", but `propose` writes the body, then the evidence, then the
  migration — so a run killed before its last write left exactly that shape.
  `accept` reported that the migration had been moved into
  `.theurian/migrations/` and that no action was needed beyond opening a pull
  request, while `.theurian/migrations/` held nothing and the drafted knowledge
  existed nowhere. The remedy discarded the draft.

  Acceptance is a **best-effort diagnosis over untrusted input, not a
  tamper-proof fact**: the proposal directory is contributor-controlled, so the
  recorded `migrationId` is a claim (cross-checked by `itemId` against the
  migration it names), a `evidence.json` that is present but unreadable is
  answered as *indeterminate* rather than collapsed into "no record", and every
  fallible branch points the reader at `.theurian/migrations/` first — no branch
  emits an unconditional "no action is needed" or "draft it again", so none can
  tell the author to discard work that may exist or duplicate a change that
  already landed. Whether a migration is in place under the recorded id is read
  from the same approved `MigrationSet` `migrate validate`/`apply` read — keyed by
  the migration's inner id, not by a filename match — so `propose accept` cannot
  disagree with the loader about what has landed, whatever a migration file was
  renamed to or whether it is a symlink. The three states are kept distinct by
  their exit codes: a migration in place under the recorded id — whether or not
  the item cross-checks — exits 4 ("read before acting"), and only a recorded id
  the loaded set holds *no* migration for exits 1 ("nothing landed, re-draft"), so
  following exit 1 can never mint a duplicate of a change on disk. Absent
  `evidence.json` (a legacy proposal, or
  one interrupted before its evidence write) falls to inference over the
  directory, which reads only the body shape the generator produces, so a
  reviewer's notes or a `Thumbs.db` left beside an accepted proposal no longer
  flips the verdict.

- **A committed proposal could forge `theurian propose accept`'s own output**
  ([#253](https://github.com/theurian/theurian/issues/253)). A proposal directory
  arrives through a contributor's pull request, and a file name or a migration's
  `contentFile` carrying `ESC [ 2 K` and a carriage return erases the line the
  terminal has drawn and prints its own in place of it — reproduced printing this
  command's own output under its own name, on both the refusal path and the
  exit-0 **success** payload (`bodyFiles`, `migrationFile`). The CLI now escapes
  every terminal-control character — the whole C0 block, `DEL` and C1 — at one
  shared sink that every text-mode emitter routes each value and key through, so
  no value any command prints, from any source, reaches a terminal with a raw
  control byte. A value's own newline is escaped too (the output's structural
  newlines are the emitters' own), while printable Unicode (a Japanese title) is
  kept. The `--json` output was never affected.

### Changed

- **BREAKING — re-accepting an already-accepted proposal now exits 4, not 1**
  ([#254](https://github.com/theurian/theurian/issues/254)). The published exit
  code table documented 4 for "that migration is already in place", while the
  natural route to that state — running `theurian propose accept` twice — exited
  1 alongside "no such proposal", an interrupted draft, and a refused
  `contentFile`. Exit 4 keeps its meaning and gains this case: *the knowledge
  state refuses this move, so read it before acting*. It is not "already done" —
  an approved migration set that cannot be read also exits 4, with the proposal
  still waiting — and the published table now says so rather than promising that
  1 always means nothing landed. Scripts that treat any non-zero exit as failure
  are unaffected; one that special-cased 1 for "already accepted" must read 4.

  Breaking by the compatibility table in
  [`docs/protocol/plugin-core-compatibility.md`](../../docs/protocol/plugin-core-compatibility.md),
  and `protocolVersion` is **not** bumped for it — a recorded, narrowly scoped
  exemption on the same grounds as `system.capabilities.milestone`'s (#206),
  written up under "Changing this contract" in
  [`docs/protocol/mcp-tools.md`](../../docs/protocol/mcp-tools.md).

## [0.1.0.dev7] - 2026-08-19

### Added

- **`theurian propose` can now set `--scope-path` and `--label`**
  ([#249](https://github.com/theurian/theurian/issues/249)). Each is repeatable
  and keeps the order it was given: `--scope-path` writes `metadata.scope.paths`
  (the globs Milestone 8's drift detection will read), and `--label` writes
  `metadata.labels`. A `--label` given alongside `--authored-here` is
  de-duplicated against the implied `authored-in-theurian` label — the schema
  declares `labels` `uniqueItems`, so a duplicate would otherwise turn a legal
  invocation into a refusal. An enhancement rather than a fix: before this, these
  fields were simply unreachable from the propose flow, and their absence
  published nothing false.

### Fixed

- **A maintainer-reviewed, public ADR accepted through `propose` → `accept`
  published `trustLevel: unverified` and `sensitivity: internal` on every result**
  ([#249](https://github.com/theurian/theurian/issues/249)). `theurian propose`
  could express no `trustLevel` or `sensitivity`, so a corpus built by the shipped
  flow could only ever carry the schema defaults; the loader completed the omitted
  fields silently, and both reach every `knowledge.search` and `knowledge.get`
  result. Measured on the first dogfooding slice: three publicly readable ADRs a
  maintainer had merged were published as `unverified`/`internal` beside
  `status: approved`, and hand-editing the generated YAML was the only remedy.
  `propose` now takes `--trust-level {unverified|inferred|reviewed|authoritative}`
  and `--sensitivity {public|internal|confidential|restricted}`, on both the
  create and update flows, so a false value is correctable rather than permanent.
  When either is omitted the default is not stamped into the migration — that
  would assert a judgement the caller never made — but is named in the draft's
  `--json` next steps, so an omission is a surfaced choice rather than a silent
  false positive.

## [0.1.0.dev6] - 2026-08-19

### Fixed

- **A rejected item's relation note reached an approved item's `knowledge.get`
  through an alias-id collision**
  ([GHSA-vx8x-rjfj-9x54](https://github.com/theurian/theurian/security/advisories/GHSA-vx8x-rjfj-9x54)).
  Critical; affected 0.1.0.dev0–0.1.0.dev5. An `addAlias` whose key
  equalled the id of a live `rejected` item, while the alias pointed at an
  approved item, defeated the relation-visibility gate: `get_item` resolves an
  alias before it looks up a status, so a lookup for the retired id resolved to
  the approved target and cleared the gate as that item. An incoming edge the
  rejected item authored — for example `contradicts` — and its `note`, where the
  secret that caused the rejection lives, was published on the *approved* item's
  response. The withheld id itself never appeared; the note did. Requesting the
  withheld id directly was still correctly refused, and the ranked
  `knowledge.search` path was never affected — it is held by the revision-identity
  check that T-18 underwrites.

  The read side is fixed independently of the write-side refusal below, because a
  migration guard cannot reach a database an affected version already built:
  `_relation_is_visible` now reads each relation endpoint's status through a new
  non-resolving `get_item_exact`, the row the id literally names. Reachability
  still resolves an alias — `knowledge.get(old)` reaches `new` after a rename —
  but authority, a visibility decision on a referenced id, reads the
  literally-named row. See
  [T-21 in the threat model](../../docs/security/threat-model.md).

### Changed

- **BREAKING — an alias key may not collide with a non-deprecated item id across a
  migration set**
  ([GHSA-vx8x-rjfj-9x54](https://github.com/theurian/theurian/security/advisories/GHSA-vx8x-rjfj-9x54)).

  **Old shape:** a set whose `addAlias` key equalled a live item's id applied
  silently. With that item `rejected` and the alias pointing at an approved item,
  the approved item's `knowledge.get` then published the rejected item's edge and
  note (the Fixed entry above).

  **New shape:** `migrate validate` and `migrate apply` both refuse the whole set
  with `AliasItemCollisionError` at exit 4, naming the alias, the item it points
  at, and the item's final status, and quoting no note. `apply` refuses before it
  creates a database file, so a refused set costs no state — the property issue
  #63's refusal already has. Both collision directions are refused — an `addAlias`
  authored over an existing item, and a `createItem` that takes an id an alias
  already keys — and a collision that straddles an already-applied migration is
  caught too, because `apply` reloads every migration file into the set the guard
  sees. The one exempt shape is the rename `deprecateItem(old)` then
  `addAlias(old -> new)`, which leaves `old` `deprecated`; every other final
  status is refused, `superseded` included. `migrate status` does not refuse — its
  contract is observation — but names every colliding migration under
  `refusedIds`, matching how it treats the tenant/ACL and duplicate-body rules.

  Breaking because a migration set that applied on `0.1.0.dev5` and earlier now
  refuses. No stable release exists — the published versions are `0.1.0.devN` —
  and no compatibility promise covers it, but the break is named here rather than
  filed as a fix. Documented in
  [`docs/protocol/migrations.md`](../../docs/protocol/migrations.md).

## [0.1.0.dev5] - 2026-08-19

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
  `upsertRevision` that declares no pin, naming the migration, the revision
  inside it, the body's **project-relative** path (the one a reader can `shasum`
  from the repository root, not the authored `contentFile`, which is relative to
  the migration file), and the remedy — in `--json` and in the default human
  output alike. The remedy carries its applied-case escape: the warning fires on
  already-applied migrations too, and editing an applied migration to add the pin
  trips FR-K5's checksum guard, so pinning an already-applied body means editing
  it, deleting `.theurian/state/`, and rebuilding (FR-K4) — a warning that
  stopped at "add the pin" would loop a reader between two errors, the way issue
  #63's HIGH-1 did. Additive and **always present**, an empty list when
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
  ([#210](https://github.com/theurian/theurian/issues/210);
  [GHSA-w5cm-cqf9-vm7r](https://github.com/theurian/theurian/security/advisories/GHSA-w5cm-cqf9-vm7r)).

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
  second revision name the same body through a variant spelling; casefolding the
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

  Published as [GHSA-266v-fcj2-qggx](https://github.com/theurian/theurian/security/advisories/GHSA-266v-fcj2-qggx).

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
