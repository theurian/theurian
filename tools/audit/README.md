# Audit keys (#199)

Two families live here, and they answer different questions. Mixing them up is
how a population key gets read as a gate.

| Family | Question | Exit status |
| :-- | :-- | :-- |
| **Population keys** (`threat_model_*.py`) | *What is the population?* | always `0`; they report |
| **Census audits** (unit B, below) | *Does every member of the population discharge?* | `1` on a violation |

## Population keys (#199 unit A, and the sweeps that followed it)

The population keys the #199 unit-A audit ran, committed so that every count in
[the work log](../../docs/work-logs/2026-08-30-199-unit-a-audit.md) is
re-runnable rather than merely reported. Later sweeps over the same file add
their key here on the same terms; each row names the work log its figure is in.

**Scope, and what these are not:**

- They read **one file**, given as an argument — `docs/security/threat-model.md`
  for every count in the work log. They are not repo-wide walkers and have no
  corpus membership; the frozen-corpus rule that governs
  [`tools/corpus_drift.py`](../corpus_drift.py) does not apply to them.
- They are **not CI-wired**, deliberately. They answer "what is the population",
  which is a question an auditor asks while choosing a key, not an invariant a
  gate can hold. The invariants this audit produced are pinned by tests in
  `packages/theurian-core/tests/`, not here.
- They report; they never rewrite. Each prints counts and members to stdout.

| Script | Key | Work-log figure |
| :-- | :-- | --: |
| `threat_model_1e.py` | retraction/discharge blocks, structurally: a marker is a member iff it *opens* a block not already inside one | 21 members, 7 continuations |
| `threat_model_census.py` | the container census (bold-labelled block openers) and the active-voice verb sweep | 276 openers, 88 keyed, 188 outside, 14 candidates; 134 verb hits split 33 / 101 |
| `threat_model_escape.py` | the **independent** third key used to measure the escape space — mermaid labels, every table, asserting headings, and src-symbol prose that is neither bold-opened nor verb-bearing | 43 tables/182 rows; 120 escape lines |
| `threat_model_owner_cites.py` | POP-2, the [#427](https://github.com/theurian/theurian/issues/427) owner-cite sweep: every bracketed `[#N]`, one row per occurrence, with the context a classifier reads. Whether a cite is an owner or a history is *not* decidable here — see the module docstring | **at `5a9a1e5`:** 114 cites / 56 numbers / 110 lines, and 97 bare-`#N` escapes ([work log](../../docs/work-logs/2026-08-31-427-owner-cite-sweep.md)) |

**A figure in that column is a measurement, not an invariant.** It is what the
key answered at the commit its row names, and the working tree moves it — the
#427 sweep's own fixes to the threat model took its row from 114/56/110/97 at
`5a9a1e5` to 119/57/114/109 at the tip of the branch that made them
([#470](https://github.com/theurian/theurian/pull/470)). Read a row against the
commit it names, or re-run it and record the new anchor beside the new number.

Run them against a specific commit by extracting the file first, so the counts
are anchored:

```sh
mkdir -p /tmp/tm && git show 06de58a:docs/security/threat-model.md > /tmp/tm/threat-model.md
uv run --frozen python tools/audit/threat_model_1e.py /tmp/tm/threat-model.md
```

## Census audits (#199 unit B)

The object-keyed census. Where the keys above answer *what is the population*,
these answer *does every member of it discharge* — so each exits `1` on a
violation and prints its classification, and four of the five carry a **ledger**:
an unclassified member is a finding, and a ledger row the sweep no longer
produces means somebody fixed a sentence and left the record behind.
(`ref_field_pair.py` has no ledger — each site discharges against its own text.)

**A ledger reconciles in every direction two records can disagree**, and the list
grew each time one of them was found not to. The first two are the ones every
ledger has; the rest are per-audit, and each was added because a real member
slipped through the ones above it.

| Direction | What it catches | Where |
| :-- | :-- | :-- |
| unrecorded / undischarged | a produced member nobody judged | every ledger |
| stale | a recorded row the sweep no longer produces | every ledger |
| **ambiguous** | one recorded row covering *two* produced members — a fragment is a substring test and counts nothing, so a second live member containing a recorded fragment reads as recorded | `config_object_claims.py`, `controls_discharge.py`, `owner_position_cites.py` |
| verdict drift | a row recorded as retracted that comes back a suspect, which means the amendment block moved or was deleted | `owner_position_cites.py` |
| occurrence count | a second anchor added to a `(token, path)` already judged | `sha_anchors.py` |

So `owner_position_cites.py` reconciles in four directions and the other three
in three each. `controls_discharge.py`'s first direction is spelled
**undischarged** rather than *unrecorded* — a member that names no `src/` symbol,
no test, no open owner and no `PROSE_ONLY` row — but it is the same direction with
the same exit status, and it is what the last `POSITIVE_CONTROLS` row drives.
This paragraph said "in two" until #501's round three, which is what a count
written beside a derivation rather than from it does.

**What each one reads is not the same, and neither is how it matches.** Only
`config_object_claims.py` reads the whole tracked tree; every other audit narrows
it, and `controls_discharge.py` reads two named files:

| Script | Reads | Matched on |
| :-- | :-- | :-- |
| `config_object_claims.py` | every tracked file | `claim_surfaces` sentences |
| `owner_position_cites.py` | tracked files under `docs/`, `schemas/`, `plugins/`, plus seven named files (`GOVERNED_FILES`) | `claim_surfaces` sentences |
| `ref_field_pair.py` | tracked files under `docs/`, `schemas/`, `plugins/` | its own section-and-block reader, which joins a block before matching |
| `sha_anchors.py` | tracked files under `docs/` | raw text — an anchor is a token, not a sentence |
| `controls_discharge.py` | `docs/security/threat-model.md` and `schemas/config/project-config.schema.json` | the threat model's bold-opened blocks, joined to the next blank line; the schema, parsed |

Wherever a population comes from `governed_paths`, two exclusions apply, and
[`claim_surfaces.py`](claim_surfaces.py) states them as a constant: `.theurian/`
(the served corpus, moved by a re-seed, never an edit) and `docs/work-logs/`
(dated records).

The `claim_surfaces` reader matches on **wrap-joined, whitespace-collapsed
blocks**, not on lines: every Markdown document here is hard-wrapped, and a
line-oriented pass undercounts by an amount nobody can state.

**Emphasis stripping is not something that reader does to every audit.**
`claim_surfaces` *offers* `without_emphasis` — `*`, `_`, and the
`<b>`/`<i>`/`<em>`/`<strong>` tags that render as them, in whatever attribute or
whitespace spelling they are written — and `config_object_claims.py` applies it
at one seam, `as_read`, so a wrapper a reader cannot see does not move a claim
out of reach of that census's keys. `owner_position_cites.py` deliberately does
not apply it: its supersession probe reads a block's **bold opener**, where the
emphasis is the signal rather than the noise. The markup the strip does not reach
is recorded and run rather than described — six HTML tags beside four
composition forms, each a row in `MEASURED_ESCAPES` in
`config_object_claims.py`.

| Script | Population | Discharge |
| :-- | :-- | :-- |
| `config_object_claims.py` | liveness claims about a watched object; the inventory is the schema key surface (json-parsed) ∪ the `ProjectPaths` file surface (introspected) ∪ the `.theurian/` paths governed prose names | a classified verdict per suspect in `SUSPECTS` |
| `controls_discharge.py` | the threat model's `**Controls` blocks ∪ the project-config schema's descriptions | names a `src/` symbol, or a pinning test, or is owed by an **open** issue, or has a row in `PROSE_ONLY` |
| `owner_position_cites.py` | every tracker cite in governed prose | the cite is historical, or its number is open, or the block after it retracts the sentence in place |
| `sha_anchors.py` | every sha-like token in governed prose | the commit is an ancestor of `main`, or the cite carries the pull-request qualifier — and a qualifier that names the commit its branch *landed as* is held to the same reachability test |
| `ref_field_pair.py` | every `unresolvedRefCount` / `refWalkTruncated` site | the site states the narrowed contract, in its block or in its section |

A `CHANGELOG.md` sentence is a record because a **dated** `## [x.y.z] - date`
section states it — asked positively, which is what makes both of its faces one
rule. `[Unreleased]` describes the tree a reader has checked out, and a changelog
with no dated sections at all (the repository-root `CHANGELOG.md`) records
nothing; both are classified like any other governed prose. Round one asked
"outside `[Unreleased]`?" and cleared the second whole, which #501's round two
found.

**Run the positive control before reading a zero.** A key that has stopped
matching reports exactly what a clean tree reports, and this repository has
shipped that failure before. Each `--positive-control` run also **drives the
ledger reconciliation itself**, from planted rows against a planted ledger, in
every direction that audit has — a ledger claiming exactness while no control
ever ran a direction is an assertion about code nobody has executed with a
mismatch in it. `config_object_claims.py` additionally runs its recorded escape
space (`MEASURED_ESCAPES`), so the bound on what its key cannot see fails
instead of rotting:

```sh
for audit in config_object_claims controls_discharge owner_position_cites \
             sha_anchors ref_field_pair; do
  uv run --frozen python "tools/audit/$audit.py" --positive-control
done
```

**The suite runs all of it.** `tests/integration/audit/test_census_audits_run.py`
subprocess-runs every audit and every control with `--offline` and asserts each
exits 0, and reads each module's control tables to fail when one is emptied — a
control loop over an empty table reports zero failures, which is how an
instrument stops checking without anything going red. Before it existed, six
mutations reverting round-one fixes survived the whole suite.

**How much ran is checked too, because "the controls passed" is not "the controls
ran".** Every control runner counts the rows its loop executes and prints one
`CONTROL-TALLY <TABLE> ran=<n> failed=<m>` line; the guard pins those counts per
audit and reads the call graph so a runner nobody reaches is RED. Five one-line
edits — a runner opening `return 0`, two loops rewritten to iterate `()`, a table
sliced to its first row, and the guard's own required-table set emptied — each
left the whole suite green before that, and each fails now.

Tracker states come from [`tracker_state.py`](tracker_state.py): a live `gh`
query by default, falling back to the committed `tracker-state.json` with its
measurement date printed. `--offline` forces the snapshot, which is what makes a
committed census run reproducible.
