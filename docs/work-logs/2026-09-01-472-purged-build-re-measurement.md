# T-17's round-5/6/7 figures, re-run against a purged build (#472 face B)

The threat model's T-17 discharge note records honestly that

> Every figure in the round-five, round-six and round-seven records was taken
> against a build that still held withdrawn rows, and none has been re-run
> against a purged build.

This is that re-run. It is the measurement half of the light-sweep cluster: the
same ground truth [#445](https://github.com/theurian/theurian/issues/445)'s
ADR-0024 reconciliation needs, so that arc's fact questions are answered here
too.

**This work log does not edit the records.** Updating T-17, T-17a and ADR-0024
against these numbers is the next assignment. What is here is the spec, the
tables, and the reconstruction failures — stated per figure, because a spec that
cannot be re-derived is itself a finding.

## Anchor

| | |
| :-- | :-- |
| Source records | `docs/security/threat-model.md` @ `ec0dbcd`, T-17 entry: round five :3922-4043, round six :4045-4183, round seven :4185-4242, the M5 scan-cache body :4285-4300, the discharge note :4251-4283 |
| Code measured | `ec0dbcd`, clean worktree |
| Machine | Apple M1 Max, macOS 26.6.2, arm64 |
| Runtime | CPython 3.13.3, SQLite 3.47.1 |
| Date | 2026-09-01 |

Every figure below is a measurement at that anchor and not an invariant. Read a
row against the commit and machine it names, or re-run it and record the new
anchor beside the new number.

## Reconstruction: what the records say about how they were taken

The F1–F9 table below was written before measuring, so its spec is derived from
the records rather than back-formed from a result. That does not describe the
whole section: the last subsection carries a measurement of its own, taken at
this anchor and marked as such where it appears.

**The Milestone 5 review-rounds work log carries no provenance for any of
them.** `docs/work-logs/2026-08-03-milestone-5-review-rounds.md` is 340 lines,
and

```sh
grep -cnE '14\.7|640\.3|160\.3|29\.17|tracemalloc|peak' \
  docs/work-logs/2026-08-03-milestone-5-review-rounds.md   # -> 0
```

So the threat-model prose is the only spec source *in that work log*. It is not
the only source in the repository, and saying so would be false. The repo-wide
key, reproduced 2026-09-02:

```sh
git grep -nE '14\.7|640\.3|160\.3|29\.17' ec0dbcd   # -> 14 lines
```

Fourteen lines: seven inside the T-17 entry itself, one coincidental match on a
`uv.lock` upload timestamp, and **six live sites elsewhere that cite these
figures as current**.

| Site | What it cites |
| :-- | :-- |
| `docs/security/threat-model.md:1047` | 14.7 µs per withheld row, in accepted-cost prose outside the T-17 entry |
| `packages/theurian-core/src/theurian/domain/ports/canonical_store.py:183` | 14.7 µs, as the cost the ranked path accepts |
| `packages/theurian-core/src/theurian/infrastructure/sqlite/index_store.py:1264` | 29.17 ms, **and F5's corpus size** |
| `packages/theurian-core/src/theurian/infrastructure/sqlite/store.py:500` | 14.7 µs, as a comparison base ("about 70 times smaller per row") |
| `packages/theurian-core/src/theurian/mcp/tools.py:861` | 14.7 µs, as T-17's ranked-reads face |
| `packages/theurian-core/tests/integration/test_absence_proof.py:203` | 14.7 µs, as the accepted per-row cost a test compares against |

One parameter the threat model omits is therefore recoverable after all. The
T-17 M5 body (:4288-4290) publishes 29.17 / 14.00 / 14.04 ms with no corpus
size; `index_store.py:1264` records the same measurement "on a 6,000-row corpus
shaped to sit on that edge". What no source states is F1's ranking size, which
is recovered by arithmetic below.

All six cite the pre-purge figures as live comparison bases, so all six move
when the records do. They belong to the record-update follow-up's scope — the
next PR on this stack — not to this work log.

| ID | Figure | Record | What it measured | Mechanism as stated | Re-runnable |
| :-- | :-- | :-- | :-- | :-- | :-- |
| F1 | 14.7 µs per withheld row; 0.163 ms → 6.047 ms | round 6, :4079-4082 | marginal canonical-read cost per withheld row | `_visible_ranking` + a retriever that never truncates + a real `SqliteCanonicalStore`; 200 approved documents, 400 retired after the build, median of 40 runs | yes, with **one inferred parameter** — see below |
| F2 | 10 / 11 / 60 / 210 / 6,000 | round 6, :4071-4077 | `canonical reads = \|ranking\| = visible + withheld`, pass count held at one | `_visible_ranking` + non-truncating fake retriever | yes, exactly |
| F3 | 3.0 / 10.5 / 10.3 / 160.3 / 640.3 KB | round 7, :4197-4203 | `tracemalloc` peak; visible held at 50, withheld 0/50/200/2,000/5,950 | fake retriever, `tracemalloc` around the call | shape yes; the record already declares the absolutes non-quotable (:4223) |
| F4 | 8.8× and 213× over a 120× increase in `\|ranking\|` | round 7, :4224-4226 | peak-memory growth factor, two harnesses disagreeing | two `tracemalloc` harnesses | shape yes; the record already declares the factor non-quotable |
| F5 | 29.17 ms for two independent scans against 14.00 / 14.04 ms | M5 body, :4288-4290 | what `SqliteIndexStore._scan_cache` saved | two `SqliteIndexStore` instances against one | **no — the mechanism no longer exists** |
| F6 | +90 ms, roughly +14% against 0.64 s | round 6, :4148-4151 | 3,000 visible with 5,999 withheld, reads 3,000 → 8,999 | **not measured** — the record says so itself at :4181 ("that rate multiplied out") | re-derive only |
| F7 | 419 µs at 50 withheld against 454 µs at 51; +35 µs, +8.3% | round 5, :4028-4033 | the pass-count edge: a second, database-free `cleared` pass | fake retriever + real `CanonicalVisibility`, 2,000 iterations per side, four runs | yes |
| F8 | −0.07 ms median delta against a 1.40 ms noise floor, N=300 | round 5, :4034-4036 | the same edge end to end; sign not stable | end-to-end repeated calls | shape yes |
| F9 | the order flip, and the `N`/`avgdl` control table | T-17a, :4798-4812 | a withheld document reordering two *visible* rows | two indexes identical but for the withheld document | yes — the purged build is the third corpus |

### Reconstruction failures, per figure

**F5 cannot be re-run: its subject was deleted.** `git grep -n "_scan_cache" --
packages/` at `ec0dbcd` returns **9 lines over 4 files, every one of them
prose** — three CHANGELOG entries recording the deletion, two comments in
`index_store.py` (`:448`, `:1263`), one in `mcp/search.py:580`, and three lines
of `test_scan_exhaustion.py` explaining what it replaced. There is no field, no
branch that reads one, and `tests/integration/test_scan_cache.py` does not
exist. "Two calls through one store costing one pass" is not a state the
shipped product can be in, so 29.17 / 14.00 / 14.04 ms has no purged-build
counterpart to take. What *is* re-runnable is the quantity the cache stood in
front of — what one pass over the corpus costs — and that is measured below as
F5′.

**F6 was never a measurement and its record says so.** +90 ms is 5,999 rows
multiplied by a 15 µs rate; +14% is that against a 0.64 s scan taken elsewhere.
Re-deriving it from F1′ is the only honest re-run, and that is what is done.

**F1's harness is under-specified by one parameter, and the arithmetic
recovers it.** The record names "200 approved documents, 400 retired after the
build" but not how many rows the *ranking* held. Two hundred visible rows at the
record's own 14.7 µs would put the nothing-withheld baseline at 2.94 ms, not the
0.163 ms it publishes. At ten visible rows the record is internally consistent:
0.163 ms ÷ 10 = 16.3 µs per read, and (6.047 − 0.163) ÷ 400 = 14.71 µs. "The
same sweep" at :4080 refers to the round-six table immediately above, whose
visible count *is* ten. So the reconstruction is **visible = 10, corpus = 200
approved documents** — recovered by arithmetic, not stated by the record.

### The trap in re-running F2, F3, F4 and F7 as written — measured, not reconstructed

Everything above this heading derives a spec from the records. This subsection
does not: it rebuilds one of those harnesses and runs it at the anchor, and the
figures in it are measurements like every table further down.

All four were taken with a **fake** retriever whose withheld tail is a
constructor argument. Under such a harness "purged" is not a state — it is
`withheld = 0`, which is **already the first row of each published table**. A
faithful re-run of the original harness therefore answers nothing: the record has
contained its own purged-build value all along.

Demonstrated rather than asserted. Round seven's harness, rebuilt and re-run at
`ec0dbcd` (fake non-truncating retriever, fake visibility, `tracemalloc` around
`_visible_ranking`, visible held at 50):

```
 visible  withheld  |rank|  passes    peakKB
      50         0      50       1       1.4
      50        50     100       1       1.8
      50       200     250       1       3.0
      50      2000    2050       1      17.1
      50      5950    6000       1      47.9
growth factor over a 120x increase in |ranking|: 33.5x
```

A **third** harness, and a third magnitude: 1.4–47.9 KB against the entry's
3.0–640.3 KB and the security review's 34.6–305.4 KB, with growth factors of
33.5×, 213× and 8.8×. That is the round-seven evidence-grade paragraph confirmed
independently — "no absolute figure here is quotable, and neither is the growth
factor. What reproduces is the sign and the direction."

So the measurement that is not already in the record is **a real index that held
the withdrawn rows and had them removed**. That is what everything below uses.

## The ground truth

A real canonical store and a real index, purged through the same library call
the withdrawal trigger uses — `SqliteIndexStore.derive_purged` →
`index_purge.purge_into`, which is what
`withdrawal_purge.publish_purge_for_withdrawal` calls at `:316`.

1. Write `visible + withheld` revisions to a real state database through
   `write_transaction` + `SqliteWriter`, all `APPROVED`.
2. Build the index over **all** of them. This is the only way a published build
   ever comes to hold a withdrawn row: `index build` filters on `may_surface`,
   so the row can only have arrived before the status moved.
3. Move the `withheld` items to `DEPRECATED`, which `may_surface` refuses under
   every flag. The index is untouched — **this is the state every round-5/6/7
   figure was taken on**.
4. `derive_purged` into a new build, asserting it removed exactly `withheld`
   rows.
5. Measure both builds against the same canonical store.

The retriever is `search_substring`'s scan below the trigram floor (query `"re"`,
two characters, so `to_trigram_expression` yields nothing): the one shipped
retriever carrying no `LIMIT`, and therefore the real analogue of round six and
seven's "retriever that never truncates". Where a *pass count* is the subject the
retriever is `search_lexical`, which is the branch that truncates.

**The stale build is reported beside the purged one in every table.** Without
that control "the purged column is flat" is satisfied by a harness that measures
nothing — the defect `tests/integration/test_index_purge.py` guards against by
requiring its own stale control to *differ*.

### F2 and F1′ — canonical reads, at round six's own shape

visible = 10, one pass throughout, 40 iterations per timing cell.

| withheld | build | `\|ranking\|` | passes | `get_item` | returned | gate ms |
| --: | :-- | --: | --: | --: | --: | --: |
| 0 | stale | 10 | 1 | 10 | 10 | 0.2349 |
| 0 | **purged** | **10** | **1** | **10** | 10 | **0.2339** |
| 1 | stale | 11 | 1 | 11 | 10 | 0.2572 |
| 1 | **purged** | **10** | **1** | **10** | 10 | **0.2368** |
| 50 | stale | 60 | 1 | 60 | 10 | 1.4088 |
| 50 | **purged** | **10** | **1** | **10** | 10 | **0.2361** |
| 200 | stale | 210 | 1 | 210 | 10 | 5.0601 |
| 200 | **purged** | **10** | **1** | **10** | 10 | **0.2405** |
| 400 | stale | 410 | 1 | 410 | 10 | 9.9425 |
| 400 | **purged** | **10** | **1** | **10** | 10 | **0.2427** |
| 5,990 | stale | 6,000 | 1 | 6,000 | 10 | 157.7126 |
| 5,990 | **purged** | **10** | **1** | **10** | 10 | **0.2433** |

The stale column **is** round six's published table — 10 / 11 / 60 / 210 / 6,000,
one pass at every count — reproduced against a real index and a real store rather
than a fake retriever. The purged column is 10 at every withheld count.

**F1′, the per-withheld-row rate.** On the record's own shape (visible 10,
withheld 0 → 400):

| | reads at 0 | reads at 400 | gate ms at 0 | gate ms at 400 | µs per withheld row |
| :-- | --: | --: | --: | --: | --: |
| round 6 record | — | — | 0.163 | 6.047 | **14.7** |
| stale, here | 10 | 410 | 0.2349 | 9.9425 | **24.3** |
| **purged, here** | **10** | **10** | **0.2339** | **0.2427** | **0.02** |

The pre-purge rate reproduces in shape and to within 1.7× in magnitude — a
different machine three weeks later, so *comparable in shape, not in magnitude*.
The purged rate is zero: 0.0088 ms over 400 rows, against a within-condition
run-to-run spread of ~0.01 ms measured over three independent repeats of the same
pair. Across the whole 0 → 5,990 sweep the purged gate spans 0.2339–0.2433 ms —
0.0016 µs per withheld row, which is not a rate, it is drift.

**Why the term goes to zero rather than getting smaller** is structural and the
table shows it: `|ranking|` is 10 in every purged row. There is nothing left for
a per-row rate to multiply. That is round five's own falsification condition —
*"the purge did not remove it"* — measured on the shipped purge rather than
argued.

### F3 and F4 — peak memory, at round seven's own shape

visible = 50, pass count held at one.

| withheld | build | `\|ranking\|` | `get_item` | peak KB | gate ms |
| --: | :-- | --: | --: | --: | --: |
| 0 | stale | 50 | 50 | 80.6 | 1.1814 |
| 0 | **purged** | **50** | **50** | **80.6** | **1.1700** |
| 50 | stale | 100 | 100 | 155.9 | 2.3385 |
| 50 | **purged** | **50** | **50** | **80.6** | **1.1713** |
| 200 | stale | 250 | 250 | 358.6 | 5.9951 |
| 200 | **purged** | **50** | **50** | **80.6** | **1.1906** |
| 2,000 | stale | 2,050 | 2,050 | 2,891.1 | 50.9439 |
| 2,000 | **purged** | **50** | **50** | **84.9** | **1.2113** |
| 5,950 | stale | 6,000 | 6,000 | 8,244.4 | 156.2400 |
| 5,950 | **purged** | **50** | **50** | **84.9** | **1.2234** |

**Provenance of the two right-hand columns, stated because it is thin.**
Whether `peak KB` and `gate ms` were taken in the same measurement window is
**not recorded**, and the harness is not committed (see *Where the harness
should live*), so it cannot be established now — asserting either way would be
fabricating provenance. `tracemalloc` inflates wall clock several-fold, so a
clock running inside the trace is not comparable with a clean one. **The
`gate ms` column is therefore graded shape-only**: what it carries is the
direction and the flatness of the purged side, not its absolute milliseconds.
Neither a repeat count nor the statistic behind either column is recorded for
this table, unlike F2 and F1′ above, which name 40 iterations per timing cell.

**F4′, the growth factor.** Stale: 80.6 → 8,244.4 KB over a 120× increase in
`|ranking|` = **102×**, a fourth magnitude beside the record's 213× and 8.8× and
the fake-harness re-run's 33.5×. Purged: `|ranking|` does not increase at all, so
there is no 120× to take a factor over; the peak moves 80.6 → 84.9 KB, 1.05×.

**The 4.3 KB step in the purged column is a harness artefact, not a residual,
and it was isolated rather than explained away.** The column read alone settles
nothing: 80.6 / 80.6 / 80.6 / 84.9 / 84.9 KB is weakly increasing in the
withheld count, which is also what a residual would look like. What settles it
is that **the step follows the warm-up, not the withheld count** — warming the
harness on the stale build gives 84.8 KB at 5,950 withheld where warming it on
the purged build gives 80.5 KB: same condition, same process, same returned
result. Two isolations account for the rest, each holding the returned result
identical and varying only the pre-purge corpus:

- the **retriever** alone, `search_substring` on the purged build:
  **25.4 KB at every withheld count** from 0 to 5,950, nine repeats, min and
  median identical;
- the **gate** alone, `CanonicalVisibility.cleared` over the same fifty rows
  against state databases from 200 KB to 7.6 MB:
  **58.9 KB at every withheld count**, nine repeats.

Both flat to 0.1 KB across the entire sweep, and their sum (84.3 KB) is *above*
some composite readings — which is itself proof the composite figure is
allocator-pool-dependent rather than additive. With both stages flat and the
warm-up probe accounting for the step, **there is no peak-memory residual on a
purged build.**

Recorded at this length because a 5% step that correlates with the withheld count
in a first table is exactly what an unexamined re-measurement would publish as a
new face of T-17.

### F7 and F8 — the pass-count edge, on the retriever that truncates

visible = 500, `search_lexical`, `FIRST_PASS_DEPTH = 100`, 200 iterations,
clock around the whole `_visible_ranking` including its SQL. The withheld rows
occupy the top of the ranking deterministically: every body is the same length
and carries the term the same number of times, so `bm25` ties and
`ORDER BY rank_score, chunks.chunk_id` breaks the tie on the id.

| withheld | build | passes | `get_item` | returned | median µs |
| --: | :-- | --: | --: | --: | --: |
| 49 | stale | 1 | 100 | 50 | 4,730.6 |
| 49 | **purged** | **1** | 100 | 50 | 4,698.2 |
| 50 | stale | 1 | 100 | 50 | 4,761.6 |
| 50 | **purged** | **1** | 100 | 50 | 4,697.4 |
| 51 | stale | **2** | **200** | 50 | **9,878.3** |
| 51 | **purged** | **1** | 100 | 50 | 4,726.6 |
| 52 | stale | **2** | **200** | 50 | 9,863.7 |
| 52 | **purged** | **1** | 100 | 50 | 4,646.6 |

The edge is exactly where `FIRST_PASS_DEPTH = CANDIDATE_DEPTH * 2` puts it —
one pass through 50 withheld, two from 51 — reproduced on the real retriever
rather than on a fake. Crossing it costs **+5,116.7 µs, +107%** here, against
round four's +12.8 ms / +15% on a 6,000-chunk corpus and round five's +35 µs /
+8.3% for the database-free gate pass alone; the three price different things and
are not comparable in magnitude.

**On the purged build there is no edge to cross.** 4,698 / 4,697 / 4,727 /
4,647 µs across 49 → 52 withheld: an 80 µs spread with no monotone direction,
against a 5,117 µs step in the control. One pass and 100 reads at every count.

**F8′.** Round five reported the edge as *below* what an end-to-end stopwatch
could resolve (−0.07 ms against a 1.40 ms floor, sign unstable). Here the same
edge resolves loudly, because the second pass is a real SQL round-trip and not a
memoised Python walk. Both figures are in-process and neither crossed the
loopback hop a real client adds (TB-1), so both remain floors on the effort
extraction takes, not ceilings. What the purged column adds is that on a purged
build the question does not arise: the quantity is constant because the pass
count is pinned at one, not because it is too small to measure.

### F5′ — the scan the deleted cache stood in front of

visible = 50, withheld = 5,950, withdrawn bodies ten times the corpus mean.
`fresh` is a build that never held them.

| build | rows returned | scan ms |
| :-- | --: | --: |
| stale | 6,000 | 85.10 |
| purged | 50 | 1.21 |
| fresh | 50 | 1.05 |

The repeat count and the statistic behind `scan ms` are not recorded for this
table either.

F5 as published priced a *saving*; this prices the thing that was being saved.
The purge takes one pass over the corpus from 85.10 ms to 1.21 ms — the withheld
rows were being scanned, ranked and returned to Python on every request, and
after the purge they are not there to scan.

### F9 — T-17a's BM25 collection statistics

Same three corpora, read off the files through `fts5vocab`.

| build | chunks | `N` | `avgdl` | distinct terms | total tokens |
| :-- | --: | --: | --: | --: | --: |
| stale | 6,000 | 6,000 | 187.65 | 6,974 | 1,125,900 |
| **purged** | **50** | **50** | **27.00** | **123** | **1,350** |
| fresh | 50 | 50 | 27.00 | 123 | 1,350 |

**Purged equals fresh on all five, and stale differs on all five.** Both channels
round five separated are closed together: `N` (the weaker one, moving each
phrase's `idf` by a different amount) and `avgdl` (the demonstrated one, the
length normalisation that reorders visible rows even when the withheld document
shares no term with the query). The withdrawn bodies are deliberately long — a
fixture of same-length documents exercises `nHit` and quietly stops exercising
`avgdl`, which is `test_index_purge.py`'s own stated fixture discipline.

The *observable* this closes — the visible order flip itself — is already pinned
end to end and does not need re-deriving here:
`test_index_purge.py::test_a_purged_build_answers_as_if_the_rows_were_never_indexed`
(three queries, with a stale control asserted to differ),
`test_the_substring_retriever_holds_the_same_equality`, and
`test_forest_purge_equality.py::test_a_purged_forest_equals_one_that_never_held_the_withdrawn_rows`
for the two node surfaces. All green at `ec0dbcd` (84 tests across the five purge
and depth files).

### F6′ — the derived residual

Round six published +90 ms and +14% as a rate multiplied out, not a measurement.
Re-derived on this ground at the largest scale measured: 5,950 withheld rows at
24.3 µs = **+144.6 ms**, against a purged request's 1.21 ms scan and 1.22 ms gate
walk. Measured directly, the stale gate costs 156.24 ms against the purged
1.22 ms.

**This paragraph reads across three tables, and inherits the weakest grade
among them.** 24.3 µs is F1′'s, 1.21 ms is F5′'s `scan ms`, and 1.22 / 156.24 ms
are the F3/F4 table's `gate ms` — so the two gate figures carry that table's
**shape-only** grade, and the three tables are not recorded to share a
measurement window. Read the direction here, not the arithmetic.

On a purged build the derivation has no input: the multiplier is the withheld
term in `|ranking|`, and it is zero. **+14% becomes +0%.**

## What a purged build still carries that tracks the withdrawn count

Every row below serves the same fifty chunks and returns the same result; only
the size of the build the purge was derived *from* varies.

| withdrawn | rows returned | file bytes | pages | free pages | retriever peak KB |
| --: | --: | --: | --: | --: | --: |
| 0 | 50 | 282,624 | 69 | 7 | 25.4 |
| 50 | 50 | 376,832 | 92 | 12 | 25.4 |
| 200 | 50 | 765,952 | 187 | 57 | 25.4 |
| 2,000 | 50 | 4,022,272 | 982 | 263 | 25.4 |
| 5,950 | 50 | 9,715,712 | 2,372 | 587 | 25.4 |

A purge page-copies the published build and `DELETE`s from the copy, so the
purged file's **size and free-page count are a monotone function of the pre-purge
corpus**: 9.7 MB and 587 free pages to serve fifty rows. Nothing in the retriever
moves with it — the peak is flat to 0.1 KB across a 34× change in file size, and
no query reads a free page.

This is not a new finding. It is
[#344](https://github.com/theurian/theurian/issues/344)'s byte residue, which
T-17a already records — "No query reads a free page, so it reaches no caller
through any tool; it is a disk-forensics surface" — measured 2026-08-24 against
`6087be4` through the real CLI.
What is new is the quantity: the residue is not a fixed overhang but scales with
what was withdrawn, so the *file's size* carries the withdrawn count to anyone
who can `stat` it — the same trust boundary the marker-string residue sits behind
and no smaller.

## Summary: the six named figures, before and after

| Figure | Record (pre-purge) | Here, stale | Here, **purged** |
| :-- | --: | --: | --: |
| 14.7 µs per withheld row (F1) | 14.7 µs | 24.3 µs | **0.02 µs — no rate** |
| 160.3 KB peak at 2,000 withheld (F3) | 160.3 KB | 2,891.1 KB | **84.9 KB, flat** |
| 640.3 KB peak at 5,950 withheld (F3) | 640.3 KB | 8,244.4 KB | **84.9 KB, flat** |
| 8.8× growth (F4, review harness) | 8.8× | 102× | **no growth — `\|ranking\|` is constant** |
| 213× growth (F4, entry harness) | 213× | 102× | **no growth — `\|ranking\|` is constant** |
| 29.17 ms, two independent scans (F5) | 29.17 ms | **not re-runnable** — `_scan_cache` deleted in M6 (#16) | — |

The three magnitudes in the middle column are not corrections to the record. They
are a different machine, three weeks later, on a synthetic corpus — *comparable
in shape, not in magnitude*, which is what the round-seven evidence grade already
says about its own numbers.

**The right-hand column is the finding, and it has one shape everywhere:** the
purge does not make these quantities smaller, it removes the term they are
functions of. Round five's argument — *"any such quantity is therefore a function
of how many rows were withheld... they go away only when the index stops holding
withdrawn rows"* — is now measured on the shipped purge rather than reasoned, and
it holds for both the time-shaped members round six enumerated and the
memory-shaped member round seven added.

## #445's fact questions, on the same ground

ADR-0024 point 4, verbatim (`docs/adr/0024-a-purge-is-a-build.md:259-263`):

> **A purge goes through the same single-writer interface as a build, and there
> is exactly one such interface.** This is ADR-0018 point 1 applied to the index
> for the first time. `IndexStore.create` and the purge are both *productions of
> a new build*; publishing is a separate step that takes the index write lock.
> Nothing outside that interface opens an index file for writing.

### Q1 — does any index write path take a lock?

**No.** Re-confirmed at `ec0dbcd` by two independent keys.

*Static.* `git grep -nE "flock|lockf|LOCK_EX|LOCK_SH|LOCK_NB|write_lock|WriteLock"
-- packages/theurian-core/src` returns **19 lines over 5 files**. Every one is
either `ProjectPaths.write_lock` — the advisory lock on
`.theurian/runtime/write.lock` guarding the *state* databases (ADR-0018 point 2,
as corrected by #432/#433) — or `daemon/instance.py`'s single-instance lock.
`write_transaction`, the only function that takes it, has **two** call sites
(`cli/commands.py:1218`, `cli/migration_pipeline.py:94`), both on the state
database. **Zero index write paths.**

**That count is anchored to `ec0dbcd`, and it has already moved.** At `266e6b6`
— one commit later, #478's `migrate apply` serialisation — the same key returns
**33 lines**, because that commit added lock-token lines to the files this key
counts. Every added line is in `cli/commands.py`, `cli/migration_pipeline.py`
or `infrastructure/sqlite/connection.py`; none is in `index_store.py` or
`index_purge.py`, so the *zero* survives. The *19* does not: re-take the key at
the landing base before this is quoted into ADR-0024's correction.

*Runtime.* The publish half of the purge, called exactly as
`withdrawal_purge.py:334` calls it:

```
ProjectPaths.write_lock -> <root>/.theurian/runtime/write.lock
exists before publish:   False
exists after publish:    False
pointer written:         True
lock held during/after publish: False
```

`write_active_index_pointer` (`project_service.py:1014-1031`) is a
write-to-temp plus `os.replace` and nothing else. The lock file is never created,
so there is nothing to hold.

**The source already says so, in the file that does the work.**
`application/withdrawal_purge.py:305-313`:

> No new index-write lock is taken. […] The single index-writer interface
> ADR-0018 point 1 still owes the index is entangled with this purge […] and is
> tracked in issue #15's follow-through rather than opened here.

and `project_service.py:1053`: "The purge holds no index-write lock". So
ADR-0024's point 4 contradicts both the code and ADR-0018's own record (which
#436 corrected to *owed*, owner [#439](https://github.com/theurian/theurian/issues/439)),
and the code's side is the measured one.

### Q2 — is there "exactly one such interface"?

**No. There are eleven writable opens of an index file, across two modules, with
no common gate.** Key: an AST walk over `index_store.py` and `index_purge.py` for
call sites of the two module-private factories that hand out a *writable*
connection (`_connect`, `_writing`), plus the one raw
`sqlite3.connect(target)` neither factory covers.

| Site | Enclosing function | Visibility |
| :-- | :-- | :-- |
| `index_store.py:545` | `create` | public |
| `index_store.py:559` | `add_chunks` | public |
| `index_store.py:626` | `add_nodes` | public |
| `index_store.py:685` | `add_node_embeddings` | public |
| `index_store.py:835` | `delete_nodes_grounded_in_chunks` | public |
| `index_store.py:902` | `add_embeddings` | public |
| `index_store.py:917` | `record_embedding_model` | public |
| `index_purge.py:526` | `_copy` | private |
| `index_purge.py:545` | `_delete` | private |
| `index_purge.py:663` | `_restamp` | private |
| `index_purge.py:690` | `_verify` | private |

Seven public methods on `SqliteIndexStore` open an index file for writing, plus
`derive_purged`, which delegates to `index_purge.purge_into`. Nothing serialises
them against each other, and nothing serialises either module against the other.
"Exactly one interface" is true only if *interface* means "the `IndexStore` port
plus the purge module it delegates to" — which is a layering statement, not the
single-writer contract ADR-0018 point 1 defines and which the header claims to
discharge.

### Q3 — does anything outside it open an index file for writing?

**Not on the shipped paths — and this is the one clause of point 4 that holds.**
Key: `git grep -n "sqlite3.connect(" -- packages/theurian-core/src`, eleven code
lines, classified by target:

| Target | Lines |
| :-- | :-- |
| index file, writable | `index_store.py:265` (`_connect`), `index_purge.py:394` (`_writing`), `index_purge.py:526` (`_copy`'s writer) |
| index file, `mode=ro` | `index_store.py:303`, `index_purge.py:525` |
| state database | `connection.py:216` (`mode=ro`), `:237`, `:324` |
| findings store | `findings_store.py:203`, `:335` |
| `:memory:` | `index_store.py:246` |

Every writable index open is inside `index_store.py` or `index_purge.py`. The
one call that looked like a counterexample — `recompute_forest` reaching
`delete_nodes_grounded_in_chunks` and `add_nodes` from the *application* layer
(`withdrawal_purge.py:490-491`) — writes to the **building** file that
`purge_into` hands it, not to the published build, and it reaches them through
`SqliteIndexStore` either way.

Confirmed at runtime beside Q1: after a real `derive_purged`, the stale build's
bytes are unchanged (`test_index_purge.py::test_a_purge_leaves_the_published_build_untouched`
holds it byte-for-byte, green at `ec0dbcd`).

**So point 4 is one true clause and two false ones**, and the header's
"Discharges the index half of ADR-0018's…" rests on the false pair.

## Where the harness should live, and why not in `tools/`

The measurement scripts are not committed. A consolidated, documented version was
written to `tools/measure/purge_ground_truth.py` and then withdrawn, on a
measured reason rather than a preference: `pyproject.toml`'s
`[tool.ruff.lint.per-file-ignores]` exempts `tools/**` for `T20` alone
(`:94`), while `TID251` — *"Domain and application layers must depend on ports,
not infrastructure"* — and `SLF` are both enforced there and both exempted under
`packages/theurian-core/tests/**` (`:87`) and `tests/**` (`:91`).

A harness that drives `RetrievalService._visible_ranking` (`SLF001`) against a
real `SqliteIndexStore` and a real `SqliteCanonicalStore` (`TID251`) is, by the
repository's own lint configuration, **a test and not a tool**. Its durable home
is `packages/theurian-core/tests/integration/`, where the prints become
assertions and the flat purged columns above become pins that go RED if a future
change puts the withheld term back. Making that happen is a design decision for
the tests specialist, not a scratch commit — recorded here as the recommendation
that falls out of this work rather than executed inside it.

**Which grades this document the way it grades the records it re-measures.**
The defect it opens with is that the round-5/6/7 figures ship with no committed
producer. These figures ship with none either: the scripts were scratch, they
are withdrawn, and until a producer lands every table above is a dated claim
about one machine that no reader can re-run. The difference is that the
producer is in flight rather than absent — the pins are being written on this
branch's stack (`docs/472-purged-records-and-pins`), into the
`packages/theurian-core/tests/integration/` home decided just above. Read the
tables against that: measured, anchored, and not yet reproducible by anyone but
their author.

Until then the invariants underneath these tables are held by the suite already
named: `test_index_purge.py`, `test_absence_proof.py`,
`test_forest_purge_equality.py`, `test_retrieval_depth.py` and
`test_scan_exhaustion.py` — 84 tests, green at `ec0dbcd`. **What none of them
holds is a *quantity*.** They pin that a purged build answers identically to one
that never held the rows; nothing pins that its canonical-read count, its peak
memory or its pass count stop moving with the withheld count. That is the gap
this work log found and did not close.

## Containment

No CLI was invoked. Every number above comes from library calls into a scratch
directory under `/private/tmp`: `create_database`, `write_transaction`,
`SqliteWriter`, `SqliteIndexStore`, `derive_purged`,
`write_active_index_pointer`, `CanonicalVisibility` and
`RetrievalService._visible_ranking`. Nothing read or wrote `HOME`,
`THEURIAN_DATA_DIR`, `~/.claude.json`, a launch agent or a systemd unit; no
daemon was started and no port was bound. `git status --short` is clean in this
worktree and in the shared checkout.
