# Milestone 7 dogfooding, first slice: Theurian on its own repository

> **Superseded on 2026-08-19.** The corpus this log describes — three
> migrations, state hash `d514af0c…`, 42 chunks, and the table of three ids per
> ADR — never merged. It is replaced by the dev7 corpus in
> [2026-08-19](2026-08-19-milestone-7-dogfooding-dev7-corpus.md): 26 committed
> items seeded through the released `0.1.0.dev7` wheel, deriving state hash
> `73cda6f9…` and 669 chunks. Nothing below is reachable from that branch, so
> every number here is the record of a run rather than something a reader can
> reproduce.
>
> **The decision this log records is discharged.** It says the remaining 21 ADRs
> are not seeded until [#249](https://github.com/theurian/theurian/issues/249)
> ships `trustLevel`, `sensitivity`, `scope` and `labels` on `theurian propose`.
> #249 shipped in `0.1.0.dev7`, released 2026-08-19, and the dev7 corpus sets all
> four explicitly — so no migration in it was hand-edited, where all three here
> had to be.
>
> **The body is kept verbatim**, corrections and all. Two things in it are live
> rather than historical: the checksum-guard lesson — a pre-merge edit to a
> proposal is normal and requires re-derivation, not a checksum adjustment — and
> the entrance decisions, which the dev7 run followed. The one clause that has
> moved on is the dev port: this log writes it as `<dev-port>`, which nothing
> bound. [`CLAUDE.md`](../../CLAUDE.md) now names 7420 and keeps 7419 in the
> check, because a run that forgets `--port` leaves its survivor on the default.

Theurian now holds three of its own ADRs as knowledge. This is the first slice
of Milestone 7 dogfooding: `theurian init` against this checkout, then ADR-0004,
ADR-0006 and ADR-0013 proposed, accepted, applied and indexed. The corpus is
committed as `1424c5e` and corrected by `ede9ea5`, which the first review round
made necessary (below). This log records what ran, the entrance decisions taken
*before* the daemon becomes resident, and the roughness the run exposed; it is
corrected here to what that round measured.

Everything below was measured on **2026-08-18** with the development checkout at
`main@8b8abd7`. Every command set `HOME`, `THEURIAN_DATA_DIR`, `UV_TOOL_DIR` and
`UV_CACHE_DIR` to scratch directories **in the same command** that ran the CLI.
The writes that landed in `1424c5e` are project-local by design: `theurian init`
writes the `.gitignore` block and creates the `.theurian/` layout in the
process's working directory, and `propose accept` adds files under
`.theurian/migrations/` and `.theurian/knowledge/` — it touches no `.gitignore`.
This time the working directory was the intended one.

## What ran

| Command | Result |
| :-- | :-- |
| `theurian init` | the `# >>> theurian >>>` block in `.gitignore` and the `.theurian/` layout |
| `theurian propose` ×3 | ADR-0004 → `architecture.sqlite-is-a-derived-artifact`, ADR-0006 → `architecture.immutable-revisions-and-optimistic-concurrency`, ADR-0013 → `architecture.ai-writes-produce-proposals`. Bodies verbatim, source anchors pinned to commit `8b8abd7`, every generated migration pins its `contentSha256` |
| `theurian propose accept` ×3 | migration and body moved out of the proposal directory |
| `theurian migrate validate` | `valid: true`, 3 migrations, 3 content files |
| `theurian migrate apply` | 6 operations |
| `theurian index build` | 42 chunks, 42 embeddings, `published: true`, `raptor: false`, `nodes: 0` — no summary forest was asked for, so none was built (ADR-0008 decision 10) |
| `theurian index status` | `stale: false` — `index build` does not report staleness, `index status` does |

Derived state hash, after the metadata correction in `ede9ea5`:
`d514af0c0d9637d27533750f820bd4374a49676c6e530041205ed26b8bb79c60`.

Each ADR travelled under three different ids, and nothing prints them side by
side:

| ADR | Proposal (`evidence.json`) | Migration | Revision |
| :-- | :-- | :-- | :-- |
| ADR-0004 | `01M0ADN110HTB605KR206NQA9G` | `01M0ADN110HTB605KR206NQA9H` | `01M0ADN110HTB605KR206NQA9J` |
| ADR-0006 | `01M0ADNFP1HMHXBKY5VEW8FRWE` | `01M0ADNFP1HMHXBKY5VEW8FRWF` | `01M0ADNFP1HMHXBKY5VEW8FRWG` |
| ADR-0013 | `01M0ADNFZJH5V7J5EWQRFVRQX6` | `01M0ADNFZKP19D141N7YMJ0QSW` | `01M0ADNFZKP19D141N7YMJ0QSX` |

The `model` field in all three `evidence.json` records is `claude-fable-5`. That
is the real identifier of the model that drafted these proposals (Claude Fable
5); it postdates the model names this repository's older examples use, which is
why grepping the repository for it finds only these three files.

The full quality gate was green on the branch after seeding: **2918 passed, 1
xfailed** — the strict xfail in `tests/unit/test_schemas.py` that holds
[#28](https://github.com/theurian/theurian/issues/28) open, and reports every
run by design. That figure has a precondition: `theurian` must be on `PATH`.
Without it, `test_a_dry_run_reports_a_plan_and_creates_nothing` fails and 38 e2e
tests skip — known, and filed as
[#204](https://github.com/theurian/theurian/issues/204).

No `setup`, no `uninstall` and no `daemon` command was run. Nothing was
registered with the login session's service manager; `launchctl list | grep
theurian` is silent.

## The corpus shipped false trust labels, and the review round caught it

The three propose-generated migrations carried the loader's defaults for every
label nobody passed, because `theurian propose` has no options for them:
`trustLevel: unverified` and `sensitivity: internal`
(`domain/migration.py`, `CreateItem` and `RevisionMetadataSpec`). Both are false
for maintainer-approved ADRs that are public on GitHub, and both are published:
`mcp/results.py` puts `trustLevel` on every search result from the revision's
own metadata, and `sensitivity` from the item's current classification. A corpus
seeded through `propose` therefore publishes false trust claims by default.

`ede9ea5` corrects the three migrations by hand — a proposal is a draft under
review until the pull request merges (ADR-0013), so editing one before merge is
the reviewer's own affordance — adding `trustLevel: reviewed` and
`sensitivity: public` to each `upsertRevision` metadata block, and switching the
source anchors from the SSH remote form to `https://`. The `createItem`
operations still carry the defaults, and that does not change what is served:
`put_item(item.with_revision(revision))` adopts the revision's labels onto the
item (`domain/knowledge.py`, `KnowledgeItem.with_revision`). Measured against the
re-derived database, the item rows and the revision rows agree:

```console
$ sqlite3 .theurian/state/theurian-state-d514af0c0d96.sqlite \
    "select item_id, status, trust_level, sensitivity from knowledge_items;"
architecture.sqlite-is-a-derived-artifact|approved|reviewed|public
architecture.immutable-revisions-and-optimistic-concurrency|approved|reviewed|public
architecture.ai-writes-produce-proposals|approved|reviewed|public
```

The root cause — `propose` cannot set `trustLevel`, `sensitivity`, `scope` or
`labels` — is [#249](https://github.com/theurian/theurian/issues/249).

**Recorded decision: the remaining 21 ADRs are not seeded until #249 ships those
options.** Hand-editing three migrations is a reviewer reading a draft;
hand-editing twenty-one is a workaround wearing a review's clothes, and it would
put the same false default one forgotten edit away every time. `scope.paths` is
the second reason to wait: `propose` cannot set it either, so seeding now would
mean a retrofit revision per item once Milestone 8 reads it.

### The edit proved the checksum guard, and cost a re-derivation

Editing an already-applied migration is refused, by design: `migrate validate`
and `migrate apply` hash each file and compare it against the checksum the local
derived state recorded when that migration was applied, and both stopped with
*"An applied migration must never be edited"*. Nothing about the guard needed
relaxing — the stale thing was the local derived state. Discarding it and
re-deriving from the edited migrations succeeded, and what came out is what a
fresh clone derives, since `.theurian/state/` is git-ignored (ADR-0004) and
nobody else holds the old state.

The operational consequence is worth stating plainly, because it is the opposite
of what "immutable migrations" sounds like: **a pre-merge edit to a proposal is
normal and requires re-derivation, not a checksum adjustment.** The immutability
that matters is against *published* history, and this corpus had not been
published to anyone.

## The released package derives the same state

A scratch copy of the seeded corpus — `git init`'d, environment-redirected — was
validated, applied and indexed with the **released** `0.1.0.dev4` package rather
than the dev checkout:

```sh
# In the scratch copy, with HOME, THEURIAN_DATA_DIR, UV_TOOL_DIR and UV_CACHE_DIR
# redirected in the same command:
uvx --python 3.13 --from 'theurian[daemon]==0.1.0.dev4' theurian migrate validate --json
uvx --python 3.13 --from 'theurian[daemon]==0.1.0.dev4' theurian migrate apply --json
uvx --python 3.13 --from 'theurian[daemon]==0.1.0.dev4' theurian index build --json
```

`valid: true`, 6 operations applied, 42 chunks, and the same state hash
`d514af0c…` the dev checkout derived.

**An equal state hash does not by itself prove equal derivation.**
`compute_state_hash` (`domain/state.py:89-135`) hashes the *inputs*: the set of
`(migration id, migration file checksum)` pairs, the checksums of the bodies
those migrations reference, `schema_version` and `engine_version`. Two equal
hashes therefore say the two runs were handed the same migrations and the same
bodies and declared the same schema and engine versions. They say nothing about
the rows that came out the other side.

What says something about the rows is comparing the rows. Dumping
`knowledge_items`, `knowledge_revisions` (including the full body text),
`source_anchors` and `migration_history` from both databases and diffing them
leaves only the columns that record *where* and *when* the apply ran:
`project_id`, derived from the registered directory's name, and the apply
wall-clock (`valid_from` on items and revisions, `applied_at` in
`migration_history`). Every other column of every row is identical, each
revision's body included.

**What that licenses, and what it does not.** The dev checkout at `8b8abd7` and
the released `0.1.0.dev4` artifact *apply and index* this corpus identically, row
for row. Serving was not exercised — no daemon was started in this slice — so
what is measured is that the release derives the state a serve path would read.
On that basis the resident daemon can run the PyPI release for serving. Drafting
it cannot: the release has no `propose` command at all.

```console
$ uvx --python 3.13 --from 'theurian[daemon]==0.1.0.dev4' theurian propose accept --help
Usage: theurian [OPTIONS] COMMAND [ARGS]...
Try 'theurian --help' for help.

Error: No such command 'propose'.
```

`propose` and `propose accept` are Unreleased — they are in the dev checkout and
in no published release. Two consequences are worth writing down before somebody
meets them at a prompt:

- **The version string cannot tell the two builds apart.** Both report
  `0.1.0.dev4`, because the checkout still carries the version the last release
  tagged. "Which Theurian am I talking to" is not answerable from
  `theurian version`, and the answer changes which commands exist.
- **The plugin's `/theurian:propose` command does not work against an installed
  release, and nothing warns.** `plugins/claude-code/commands/propose.md` is
  built entirely of `theurian propose` invocations, and
  `plugins/claude-code/compatibility.yaml` declares `minimum: 0.1.0-dev.0` and
  `maximumExclusive: 0.2.0` — a range dev4 satisfies. That declaration bounds
  Core's *version*; it cannot express which commands that version ships.

## Entrance decisions, recorded before the daemon becomes resident

**1. A deliberate real `theurian setup` on the development machine installs the
PyPI release, never the dev checkout.** The prerequisite is the cross-version run
above: dev4 derives this corpus row for row, so nothing about the corpus requires
unreleased code. The reason for the rule is the one
[`CLAUDE.md`](../../CLAUDE.md) records under *Running the CLI on a development
machine*: a real `setup` registers with the real login session whatever `HOME`
says, so whatever it points at is what crash-loops in launchd if it is wrong.

```sh
uv tool install --python 3.13 'theurian[daemon]'
```

No stable release exists, so this resolves to the latest pre-release —
`0.1.0.dev4` on 2026-08-18, confirmed by installing into a scratch `UV_TOOL_DIR`
and `UV_TOOL_BIN_DIR` and asking the result: `theurian version --json` reports
`"version": "0.1.0.dev4"`.

**Record what was installed, at install time, because nothing else does.**
Theurian does not verify the artifact it runs from — `probe_artifact_integrity`
reports `not-applicable` rather than assert a check it never made — and T-16 in
the [threat model](../security/threat-model.md) carries the residual:
release-time publication ships, install-time verification does not. So the record
to keep is the resolved version and the wheel's SHA-256, checked against the
`SHA256SUMS` asset on the matching `core-v*` GitHub release. For dev4, measured
2026-08-18, PyPI's published digest and the release's `SHA256SUMS` agree:

```text
6c774df9dce0b1947c650bd7cc71bd55f686cc2a027d07bdb8f7ae8b35e0548a  theurian-0.1.0.dev4-py3-none-any.whl
```

T-16's summary row in the threat model names
[#39](https://github.com/theurian/theurian/issues/39) for that residual, and #39
is closed. What came due there was the dated *promise* in setup's own text, and
rewriting the text closed it; the verification itself is still unimplemented, so
the row's residual stands and its tracker does not.

**Serving runs the release; drafting keeps running the checkout.** dev4 ships no
`propose` command (above), so until a release does, `theurian propose` and
`theurian propose accept` are invoked from the dev checkout while the resident
daemon serves from the installed release — and the plugin's `/theurian:propose`
is unusable against the installed release until then.

The uninstall path, recorded now rather than discovered later:

```sh
theurian uninstall --dry-run                          # see the plan
theurian uninstall                                    # apply it
launchctl list | grep theurian                        # silent is clean
launchctl bootout gui/$(id -u)/dev.theurian.daemon    # manual cleanup, if ever needed
uv tool uninstall theurian
```

Only the `--dry-run` line is safe to run casually. `theurian uninstall` applies by
default and deregisters from the real login session, which is exactly what makes
it the right teardown for a deliberate installation and the wrong thing to type
while verifying something else.

**That teardown does not end the token's life, and no command does.**
`uninstall` keeps the data directory by design — it removes the OS service and
the MCP entry, and reports the data directory under `kept`, because derived state
and a local token are not knowledge and deleting them is a separate, explicit
choice. Ending the credential is therefore manual, and the manual steps belong
here:

```sh
rm -rf ~/.theurian    # the token, ~/.theurian/auth/mcp-token, and ~/.theurian/env
```

Then delete the line that sources `~/.theurian/env` from whichever shell profile
has it. Setup never wrote that line — it is the user's to add
([local-mcp.md](../security/local-mcp.md)) — so no uninstall can remove it.

`theurian auth rotate` replaces a token, which is not the same as ending one;
there is no revoke command. The gap — and `--service-only`'s selecting no
behaviour — is [#250](https://github.com/theurian/theurian/issues/250). Where
`THEURIAN_DATA_DIR` is set, substitute it for `~/.theurian`.

**2. Port 7419 belongs to the resident dogfood daemon.** Dogfooding is only worth
anything if it exercises the shipped default, so development-time daemon runs
move to an explicit `--port`, and dev-side "is the daemon gone" checks
(`lsof -nP -iTCP:<dev-port> -sTCP:LISTEN`) target the chosen dev port, not 7419.

**This is not only `daemon start`.** `setup --dry-run` and `doctor` probe the
port too — both build their context from it and report on whatever answers on
`127.0.0.1:<port>` — so a dev-time `doctor` run without `--port` describes the
resident daemon while reading as if it described the thing under test. Both take
`--port <dev-port>` here. The complete `--port` surface, at `8b8abd7`:

| Command | Where the option is declared |
| :-- | :-- |
| `theurian daemon start` | `cli/commands.py` |
| `theurian daemon status` | `cli/commands.py` |
| `theurian setup` | `cli/setup_commands.py` (`PortOption`) |
| `theurian doctor` | `cli/setup_commands.py` (`PortOption`) |
| `theurian uninstall` | `cli/setup_commands.py` (`PortOption`) |
| `theurian auth rotate` | `cli/auth_commands.py` |

**3. The resident phase starts by re-deriving state under the real
`THEURIAN_DATA_DIR`.** Everything above ran against scratch data directories, and
provenance does not travel with the repository: `migrate apply` records *this
installation built this state* out of the tree, in `THEURIAN_DATA_DIR`
(`application/project_service.py`, `BuildProvenance`), and
`verify_state_provenance` refuses to index or serve derived state carrying no
such record (ADR-0004, SEC-7). So the first two commands of the resident phase
are `theurian migrate apply` and `theurian index build` under the real data
directory — before any search is expected to answer, not after it fails to.

## Ordering against SEC-11 (do not generalize)

Dogfooding proceeds before the SEC-11 secret scanner ships. The rationale and its
tripwires are recorded in
[#198](https://github.com/theurian/theurian/issues/198#issuecomment-5328174174)
and are repository-specific: gitleaks runs here on every pull request, and on
every push to `main`; the corpus is committed public documentation; and ingestion
is confined to `.theurian/knowledge/` and `.theurian/specifications/`.

**This ordering is not evidence that the missing scanner is harmless.** It is a
decision about one repository whose compensating controls user projects do not
have.

## What the slice found

**The run found nothing; the review round on it found three things.** The flow
held end to end on 2026-08-18, from `init` through a searchable index — step 2 of
the milestone definition of done produced no failure for the first time in this
project. Then round one read the same paths:

- **The corpus published false trust labels.** `unverified`/`internal` on
  approved public ADRs, fixed in `ede9ea5`; root cause
  [#249](https://github.com/theurian/theurian/issues/249). Recorded above.
- **[#253](https://github.com/theurian/theurian/issues/253) — an interrupted
  `propose` is diagnosed as accepted.** HIGH: with the proposal directory
  half-emptied and no migration anywhere, `propose accept` reports that no action
  is needed, and the remedy it prints discards the drafted work.
- **[#254](https://github.com/theurian/theurian/issues/254) — `propose accept`'s
  published exit-code table is wrong for the natural already-accepted case.** It
  documents 4; that case returns 1, and exit 1 folds four distinct states.

The premise correction under *The released package derives the same state* is
the same shape: "the resident phase needs no interim release" was true of serving
and false of drafting, and only running the released binary settled it.

So the dogfooding deliverable is working as intended. A clean run is not the
result; a clean run plus an adversarial reading of the paths it touched is, and
the reading is where the defects were.

The day's other defect-finding happened elsewhere, in the hand-written-flow
reproduction for
[#210](https://github.com/theurian/theurian/issues/210#issuecomment-5328173657),
where the same pipeline's schema-optional path lets two hand-written migrations
share one `contentFile` and silently cross-record bodies. The CLI flow this slice
used pins `contentSha256` on every generated migration, which is why the slice
never met it.

Six observations, none of them a defect:

- **LOW, wording.** `propose accept`'s `nextSteps` still says "open a pull
  request with the proposal directory in it" (`cli/propose_commands.py`,
  `_ACCEPT_STEPS`). By the time it prints, accept has moved the migration and the
  body out, and the proposal directory holds only `evidence.json`. The sentence
  predates the move.
- **LOW, ergonomics.** One item costs fourteen options on `theurian propose`: the
  eleven whose help says `(required)`, `--source-uri` (required unless
  `--authored-here`), plus `--source-commit` and `--source-path` to pin the
  anchor. Seeding the remaining corpus that way — 21 more ADRs, plus work-logs —
  is verbose. A manifest or bulk mode is a candidate for the Milestone 7
  write-tools scope. Recorded as an observation, not filed.
- **Neutral.** The `requires-python = ">=3.13"` floor surfaced naturally on a
  machine whose default is 3.12.2. uv's resolver message states it clearly; no
  action.
- **Neutral.** A local pre-merge `migrate apply` is possible and useful for
  verification, although `nextSteps` frames apply as strictly post-merge. State
  is derived and gitignored, so this is safe by design
  ([ADR-0004](../adr/0004-sqlite-is-a-derived-artifact.md)) — the merge is still
  the approval
  ([ADR-0013](../adr/0013-ai-writes-produce-proposals.md)), and applying locally
  approves nothing.
- **Known, accepted.** A body is copied verbatim, so its relative links keep
  pointing at the ADR's neighbourhood rather than the copy's. These three bodies
  hold exactly one relative link — `../protocol/migrations.md#naming-and-layout`
  in the ADR-0013 copy — which resolves from `docs/adr/` and, from
  `.theurian/knowledge/architecture/`, points at
  `.theurian/knowledge/protocol/migrations.md`, which does not exist. Rewriting
  it would take a new revision; verbatim is the property worth keeping, and the
  absolute links in the same bodies are unaffected.
- **Neutral.** `theurian init` creates fourteen directories and writes `.gitkeep`
  into three: `migrations`, `specifications`, `proposals`
  (`application/project_service.py`, `initialize_project`). Marking the
  git-ignored four — `state`, `cache`, `runtime`, `generated` — would commit a
  path ADR-0004 says must be absent, and the rest are left empty and untracked.
  So a clone has no `.theurian/evaluations/`, no `.theurian/schema/` and none of
  the four unused `knowledge/` subdirectories (`domain`, `operations`,
  `security`, `testing`). Measured rather than assumed: `migrate apply` and
  `index build` over a tree holding only the tracked files reach the same
  `d514af0c…` state hash and the same 42 chunks, so this flow does not need them.
