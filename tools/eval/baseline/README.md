# The Phase A retrieval-evaluation baseline

## What this is

The first committed baseline for the Phase A retrieval-evaluation harness
(ADR-0036 decision 4, slice S4b). `report.json` and `timings.json` here are
what `tools/eval/run.py` produced the first time it was run against the
committed corpus and pinned to a commit. **This run DEFINES the baseline; it
does not assert one.** No number in this file, `report.json`, or the harness
itself is a target — there is no minimum Recall@k, no MRR floor, no maximum
latency anywhere in the contract. A future regression is read against these
figures by a human decision, not by a threshold this harness carries.

Slice S4c (below, "The RAPTOR comparison arm") extends the same run with a
second, raptor-ON pair and a `comparison` block against this one — the base
figures on this page are untouched by that extension (confirmed the same way
the byte-identity pin confirms everything else: the regenerated
`report.json` minus its new `raptor`/`comparison` keys equals what this page
already documented).

## Measured

- **Date:** 2026-09-24
- **Measured on:** the slice-S4c review round's re-measurement — *fix(eval):
  widen the comparison block to every family and fix the raptor arm's own
  labels*, on [PR #798](https://github.com/theurian/theurian/pull/798). That is
  where the widened `comparison` families, the renamed `comparison.nodes` keys
  and the raptor arm's own scope and reason strings actually live. Named by
  subject and pull request rather than by sha, for the reason the next bullet
  gives.
- **What `timings.json`'s `commitSha` says, and what it does not.** The annex
  stamps `b1922f0e08478102ad078e7c0f54c213216ebd6c`. That is honest about the
  tree that ran — it was `HEAD` at the time — and **it is not the commit that
  produced these figures**: the `report.py` change they come from was still
  uncommitted, so the stamp names an in-flight tree's parent and *predates the
  code it measures*. A `commitSha` on this page is the nearest commit, never an
  attribution of the change. The earlier stamps read the same way: S4c's first
  measurement stamped `673b12cfd12fbb652c40cfd481be098e2e1ff20b`, which carries
  no raptor arm at all; S4b's original stamped
  `a58fdcb588c8189dc00a8935403c00b87b3c5d48`, which does contain the harness it
  measured.
- **What makes the figures checkable is same-tree reproduction, not the stamp.**
  `tests/integration/tools/test_baseline_current.py` regenerates `report.json`
  at whatever commit is checked out and byte-compares it against the committed
  one, so at every committed state the pair is self-consistent or it reddens —
  which is the property a stamp cannot give and this one did not. The durable
  anchor for this page is the merge commit this pull request squashes into,
  which its own body will name.
- **Corpus:** `tests/fixtures/eval` (`corpusId: adr-corpus-v1`)
- **Census** (echoed from `report.json`'s own `census` member):

  | | `full` | `clean` |
  | :-- | --: | --: |
  | items | 33 | 26 |
  | chunks | 615 | 609 |
  | byStatus | approved 26, superseded 3, draft 1, proposed 1, rejected 1, deprecated 1 | approved 24, superseded 2 |
  | bySensitivity | public 12, internal 19, confidential 1, restricted 1 | public 12, internal 14 |

## Instrument

Canonical reproduction (what a later run types):

```console
$ uv run python tools/eval/run.py --corpus tests/fixtures/eval --out <dir>
```

What actually produced this baseline: an in-session driver script that
inserted `tools/eval` on `sys.path` and called `run.main(["--corpus",
"tests/fixtures/eval", "--out", <dir>])` — the same in-process call path the
committed integration pin
`test_two_consecutive_harness_runs_over_the_smoke_corpus_produce_a_byte_identical_report`
(`tests/integration/tools/test_harness_pins.py`) drives, and the one
`tests/integration/tools/test_baseline_current.py` drives against this exact
corpus. **The equivalence of the documented command and that in-process call is
held directly, not inferred from determinism:**
`test_the_documented_cli_command_reproduces_the_committed_baseline_report` (the
same file) runs `tools/eval/run.py` as a real subprocess from the repository
root, over this corpus, and byte-compares its `report.json` against the
committed one. Its reach is the subprocess boundary — it invokes the current
interpreter directly, so the `uv run` wrapper in the `$` line above is not
itself exercised. The determinism pin (ADR-0036 decisions 5 and 7) is what the
two *in-process* runs below lean on. The driver was run **twice**;
`report.json` came back byte-identical both times:

```console
$ cmp baseline-out-1/report.json baseline-out-2/report.json; echo "exit: $?"
exit: 0
$ cmp baseline-out-1/timings.json baseline-out-2/timings.json; echo "exit: $?"
baseline-out-1/timings.json baseline-out-2/timings.json differ: char 121, line 4
exit: 1
```

`timings.json` here is the **second** run's (decision 7's dated annex is
expected to differ run to run; only `report.json` is the determinism pin).

Since slice S4c, the same command builds and queries the raptor-ON pair too
(`corpus_build.build_both(..., raptor=True)`, symmetric to the base pair) —
the two-run comparison above is over the whole `report.json`, so it already
covers the `raptor` and `comparison` keys, not only the base arm's.

## Environment (from `timings.json`, outside the byte-identity property)

- **Platform:** `macOS-26.6.2-arm64-arm-64bit-Mach-O`
- **CPython:** `3.13.3 (main, Apr 9 2025, 03:47:57) [Clang 20.1.0 ]`
- **SQLite:** `3.47.1`

`timings.json` is the dated annex (ADR-0036 decision 7): it carries the commit
sha, the environment, per-query latency and index-build cost, and it is *not*
expected to reproduce byte-for-byte across runs or machines — that is exactly
the property `report.json` claims and `timings.json` does not. `report.json`
by design embeds no sha and no timestamp, so a re-run at this same commit
diffs empty against it; a re-run at a different commit or on different
hardware is a new, separate measurement, not a correction of this one.

## The headline numbers

All figures below are copied from the committed `report.json`, at the commit
above — re-run the corresponding command to reproduce them.

**`equality.channel`** (ADR-0036 Amendment 1 rider 1, #787's joint-build
record): `18 of 26` enabled queries differ beyond `{retrieval.indexBuildId,
retrieval.snapshotId}` at the harness limit of 10, and `21 of 26` at the
equality limit of 50 — this run agrees exactly with the dated record on
[#787](https://github.com/theurian/theurian/issues/787#issuecomment-5804290254)
(measured there at `4ce0868f`); no divergence to report.

```json
"equality": {
  "channel": {
    "atLimit": {"queriesDiffering": 18, "of": 26},
    "atEqualityLimit": {"queriesDiffering": 21, "of": 26}
  }
}
```

**Abstention.** `aggregated.overall.abstentionAccuracy` is `0.75` (3 of the 4
`expectAbstention` judgements correct on `full`). None of the three correct
outcomes carries `abstentionCause` — every one is absence-earned in this
corpus's baseline, not gate-earned (`report["abstentionProbe"]` shows the
`#787` flag-probe ran, at limits `[10, 50]`, and found no hit at either limit
for any of these three). The one miss, `q-withheld-credential-cache`, is
recorded on #787 as benign vocabulary overlap by visible rows, not a leak.

**`supersededKnowledgeErrorRate`** is `0.0` overall and in every `byClass`
bucket that carries it (`exact-decision`, `superseded`, `unknown`) — zero by
build-time exclusion, annotated as such: every sample carrying
`forbiddenPresentCause` reads `"zero-by-construction: every forbidden item is
census-tested -- excluded from the index under either build flavor, so its
absence is a build-time property, not evidence about ranking"` (two queries,
`q-withheld-credential-cache` and `q-withheld-token-rotation`, carry that
annotation; the other forbidden-trap queries have no gate-tested member to
annotate and so report the same zero unannotated).

**Recall / MRR, `aggregated.byClass`** (echoed, no judgement about whether a
figure is good or bad — that reading is a future decision against a future
run, never made here):

| class | n | Recall@1 | Recall@5 | Recall@10 | MRR | evidencePrecision | abstentionAccuracy | supersededKnowledgeErrorRate |
| :-- | --: | --: | --: | --: | --: | --: | --: | --: |
| broad-architectural | 3 | 0.111111 | 0.75 | 1.0 | 0.583333 | — | — | — |
| conflicting | 2 | 0.5 | 1.0 | 1.0 | 1.0 | — | — | — |
| cross-adr | 3 | 0.666667 | 1.0 | 1.0 | 1.0 | 0.267857 | — | — |
| exact-decision | 8 | 0.625 | 0.75 | 0.875 | 0.671875 | 0.125992 | — | 0.0 |
| rejected-alternative | 3 | 0.833333 | 1.0 | 1.0 | 1.0 | 0.115741 | — | — |
| superseded | 3 | 1.0 | 1.0 | 1.0 | 1.0 | — | — | 0.0 |
| unknown | 4 | — | — | — | — | — | 0.75 | 0.0 |
| **overall** | 26 | 0.628788 | 0.875 | 0.954545 | 0.823864 | 0.154101 | 0.75 | 0.0 |

`aggregated.population` states the denominator: full-corpus runs only, one
sample per enabled query — a query's `clean` run (where one exists) is not a
second sample of the same judgement.

## The RAPTOR comparison arm (slice S4c)

The Phase A exit criteria name the RAPTOR default-on decision as one this
harness must measure (`docs/roadmap.md`, Phase A exit criteria row). This
section is that measurement: a second, raptor-ON build of the same two
projects (`full-raptor`, `clean-raptor`), queried with every enabled query
under the identical default flags, limits and #787 abstention-probe
machinery the base arm uses (`run.py`'s own docstring states the symmetry is
deliberate) — the RAPTOR forest's presence is the only variable the
`comparison` block below isolates. `report.json`'s `raptor` section carries
the same shapes as the base arm's own `census`/`queries`/`equality`/
`aggregated` members; only `corpusId`/`kValues`/`harnessConstants` are
dropped there, since both arms share one corpus and one set of constants.

**The forest's size, `comparison.nodes`** — a corpus-derived, deterministic
quantity (ADR-0008 decision 7: the default `SummarizationProvider` is extractive
and deterministic, so the forest a build derives is a function of the chunks that
build just wrote), so it belongs beside the other figures on
this page rather than only in `timings.json`'s dated annex, which carries the
same build's wall-clock cost instead:

```json
"comparison": {
  "nodes": {"clean-raptor": 26, "full-raptor": 28}
}
```

Keyed `-raptor`, matching `timings.json`'s own `indexBuild` keys for these
same builds: a bare `full`/`clean` key here would answer two different
questions under one name (`indexBuild.full.nodes` is the BASE build's
forest, always `0`; this is the RAPTOR build's).

**`raptor.equality.channel`** — the raptor pair's own equality entries,
reported rather than asserted the same way #787's channel already is, but for
a different reason than the base arm's set-equality claim: the two builds
derive their forests over different chunk populations, so node routing
(ADR-0008 decision 8) surfaces a different selection and ordering of
**approved** leaves on each side, and a wider differing set here is expected
rather than a regression. **It is not unapproved text reaching a default-flag
response**, and that is verified rather than argued:
`test_no_raptor_path_title_in_the_full_arms_default_response_leaks_an_unapproved_body`
drives a real `--raptor` build over this corpus, walks every
`results[*].raptorPath[*].title` the default-flag responses actually carry, and
checks each against the unapproved fixture bodies — `IndexStore._node_scope`
applies to a summary node's own scope the same status and sensitivity predicates
a leaf match clears. What stays open in GHSA-97q9's `raptorPath` territory is
the staleness face the threat model records — a summary build can still quote a
drifted leaf — which is a different mechanism from this count. This run's `atLimit` count widens from the base arm's `18
of 26` to `20 of 26`; `atEqualityLimit` holds at `21 of 26` — the base arm's
own set-equality claim (decision 6) is unchanged and untouched by this
number, which is why it is reported here rather than folded into that claim:

```json
"raptor": {
  "equality": {
    "channel": {
      "reason": "recorded channel, T-17a family and RAPTOR summary routing (ADR-0008 decision 8, GHSA-97q9's raptorPath territory); not a disclosure finding because includeUnapproved is a request parameter (not a grant) and the Core is one-principal (#119); reachable only under the operator's --include-unapproved AND --raptor build, absent from the shipped default.",
      "atLimit": {"queriesDiffering": 20, "of": 26},
      "atEqualityLimit": {"queriesDiffering": 21, "of": 26}
    }
  }
}
```

`reason` is its own string, not the base arm's `_CHANNEL_REASON` reused: that
one names `--include-unapproved` alone as the reachable condition, which
under-describes this channel -- reaching it also needs `--raptor`.

**`comparison.byClass`/`comparison.overall`** — raptor-on minus raptor-off,
over the `full`-corpus default-flag runs (deltas only, no judgement about
whether a move is good or bad, matching decision 4's own convention above):

| class | n | ΔRecall@1 | ΔRecall@5 | ΔRecall@10 | ΔMRR | ΔevidencePrecision | ΔabstentionAccuracy | ΔsupersededKnowledgeErrorRate |
| :-- | --: | --: | --: | --: | --: | --: | --: | --: |
| broad-architectural | 3 | 0.25 | -0.083333 | 0.0 | 0.416667 | — | — | — |
| conflicting | 2 | 0.0 | 0.0 | 0.0 | 0.0 | — | — | — |
| cross-adr | 3 | -0.666667 | -0.333333 | 0.0 | -0.683333 | 0.0 | — | — |
| exact-decision | 8 | 0.0 | 0.0 | 0.0 | -0.001736 | 0.0 | — | 0.0 |
| rejected-alternative | 3 | -0.666667 | 0.0 | 0.0 | -0.444444 | 0.018519 | — | — |
| superseded | 3 | 0.0 | 0.0 | 0.0 | 0.0 | — | — | 0.0 |
| unknown | 4 | — | — | — | — | — | 0.0 | 0.0 |
| **overall** | 26 | -0.147727 | -0.056818 | 0.0 | -0.097601 | 0.006173 | 0.0 | 0.0 |

Every `_aggregate_entries` family gets a delta, not only recall/MRR: a class
or k either arm has no sample for (`—` above) follows the same
`None`-for-empty-denominator convention the base arm's own table follows,
and stays `—` rather than a false zero. `rejected-alternative`'s
`evidencePrecision` moves +0.018519 (+16% relative to its base figure of
0.115741 above), the only nonzero movement among the three families beyond
recall/MRR — omitted from the block before this round, which is why every
family is published now rather than only two of five. `unknown` carries no
recall/MRR/evidencePrecision delta (every query in that class is an
abstention probe with no judged `relevant` item, so both arms read `None`
there), but its `abstentionAccuracy` and `supersededKnowledgeErrorRate`
deltas are real measurements (`0.0`, flat), not absent ones.

**Raptor build cost** (`timings.json`, outside the byte-identity property,
same as the Environment section above):

| | nodes | wallClockMs | indexBytes |
| :-- | --: | --: | --: |
| `full-raptor` | 28 | 1463.681 | 4636672 |
| `clean-raptor` | 26 | 1386.26 | 4554752 |

## Re-check commands

Regenerate the baseline:

```console
$ uv run python tools/eval/run.py --corpus tests/fixtures/eval --out <dir>
```

Run the pin that this baseline stays current against the tree (LOCAL half —
see `tests/integration/tools/test_baseline_current.py`'s own docstring for
what it does and does not cover) together with every other harness pin:

```console
$ uv run pytest tests/unit/tools tests/integration/tools -q
```
