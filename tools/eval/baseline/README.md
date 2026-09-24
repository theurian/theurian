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

## Measured

- **Date:** 2026-09-24
- **Commit:** `a58fdcb588c8189dc00a8935403c00b87b3c5d48`
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
