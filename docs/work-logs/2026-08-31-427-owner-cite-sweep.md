# The threat model's owner-cite sweep (#427)

Anchored at **`5a9a1e5`** (`origin/main`, the #446 squash). Every line number
below is a line number in `git show 5a9a1e5:docs/security/threat-model.md`; the
fixes in this branch move them.

The population is **Key A** of the shared instrument run recorded on #199
([issuecomment-5480024637](https://github.com/theurian/theurian/issues/199#issuecomment-5480024637)):
56 unique cited numbers, with the OPEN/CLOSED-issue/CLOSED-PR split measured
there on 2026-08-31. That split is cited, not re-derived — re-deriving it is a
second detector, and this arc has already lost three counts to hand-applied keys.

## The key, and what it does not reach

```sh
mkdir -p /tmp/tm && git show 5a9a1e5:docs/security/threat-model.md > /tmp/tm/threat-model.md
uv run --frozen python tools/audit/threat_model_owner_cites.py /tmp/tm/threat-model.md
```

```
POP-2 CITES: 114 occurrences over 56 distinct numbers
lines carrying at least one cite: 110
tracker URLs reach 56 distinct numbers -- same population
escape space (bare #N, outside the key): 97 mentions over 37 distinct numbers
```

**A cite, not a number, is the unit.** #15 is cited six times and each cite is
classified on its own context; #40 is cited eight times in one table. 114 rows,
not 56.

**The bracket key and the URL key select the same population here**, and the
script says so on every run rather than leaving it assumed: every `[#N]` in this
file is a Markdown link to a `github.com/theurian/theurian/{issues,pull}/N`
target, and no such target is reachable under any other label. If those two
numbers ever diverge, the key has stopped covering the file.

**The escape space is bare `#N`** — 97 mentions, outside the key. It is measured
rather than assumed because #427 exists over a cite the previous two keys did not
reach, and stopping at the stated key would be the same failure one level over.
Two of the three defects this sweep fixed live in that escape space, and no
bracket key would have found them. Sweeping it is recorded in
[its own section](#the-escape-space-97-bare-mentions) below.

## Classification, and how each label is earned

| Label | Rule | Count |
| :-- | :-- | --: |
| **(b) history** | "fixed in #N", "found by #N's round", "since #N", a provenance cite for a shipped guard. A CLOSED owner is correct and expected | 87 |
| **(a) open** | owner-of-a-residual or future-control, and the cited issue is OPEN | 20 |
| **(a) accepted** | owner-shaped, cited issue CLOSED, and the text is an *explicit acceptance record* whose closure is recorded at the entry's own head — the stated exemption | 3 |
| **(a) DEAD** | owner-shaped, and the cited thing cannot receive work: a CLOSED issue, or a merged PR (the #444-recorded PR-as-owner shape) | **4** |

114 total. The four defects are `#198:1639` and `#15` at `:3970`, `:4108`,
`:4184`.

These four numbers are counted off [the full table below](#the-full-key-a-table),
not asserted: the table's `(number, line)` pairs were checked against the
script's own output and are equal as a set — 114 rows, nothing missing, nothing
extra. Counting by reading is what this arc has already lost three counts to.

**(b)-by-frame is still (b).** Several owner-shaped, future-tense cites sit
inside a block the document *itself* marks as a preserved record — "Amended in
Milestone 6 … Everything from … to here is the Milestone 5 record and is left
standing; none of it describes code that still exists" (`:4279`). A cite inside
such a frame is history by the document's own declaration, and rewriting it would
destroy the record rule 2 exists to keep. `#16:4206` and `#16:4274` are the
clearest cases: both read as owed, both are inside that frame, neither is a
defect. The four defects are exactly the owner-shaped cites with **no** such
frame reaching them.

## The four defects, and what each became

### 1. `#198:1639` — T-15, ingest-time scanning (a Key B member)

> `theurian ingest` runs no scan … out of #316's scope
> (**[#198] tracks the family**).

#198 is CLOSED COMPLETED. It closed by shipping the `propose accept` half; the
ingest/index-time half was never in it. **Repointed to #329**, verified rather
than assumed: #329's body is titled "Ingested content is never secret-scanned"
and quotes #198's own 2026-08-17 measurement of this exact path
("the shipped `theurian ingest` already ingests … with no scan on that path —
this is live today"), naming ADR-0027's *Still owed* as its provenance. The
summary row at `:5506` already carried the corrected form
("[#329]; #198 is closed, having shipped the `propose accept` half"), and the
T-15 body at `:1477` already reads "re-graded when #329 ships an ingest-time and
index-time control" — so this cite was the one surface in the entry still
pointing at the closed issue.

### 2–4. `#15` at `:3970`, `:4108`, `:4184` — the T-17 residual chain

Three cites, one claim, three review rounds:

| Line | The sentence | Round |
| :-- | :-- | :-- |
| `:3970` | "this residual and T-17a's collection statistics are removed by the same change and by nothing smaller — the Milestone 6 purge and blue/green build, [#15]" | 5 |
| `:4108` | "the fix location is unchanged: the index purge in [#15] **removes** this face and T-17a's collection statistics together" | 6 |
| `:4184` | "The Milestone 6 index purge and blue/green build, [#15], **removes** the withheld term from `\|ranking\|`" | 7 |

`:4108` is the positive control the shared measurement pasted and #427's body
named. All three are the same owner cite at three depths of the chain. #15 closed
COMPLETED on 2026-08-10; the purge shipped as `66a43ae`.

**Treated as a discharge, not a rewrite.** One dated blockquote is appended at the
end of the chain (after the *Evidence grade* paragraph that closes it), naming all
three positions by their round rather than by line number, recording what #15
shipped and where the class now stands, and stating what the note does **not**
claim. The three records are left byte-for-byte: each is the argument as it stood
at the round that produced it, and the fix location they name did not move —
only the register did, from *owed* to *shipped*.

Verified before writing it, each against source rather than against the issue's
own words:

| Claim in the note | Verified by |
| :-- | :-- |
| `derive_purged` has production callers | `application/withdrawal_purge.py:316` (`git grep -n derive_purged -- packages/theurian-core/src`) |
| the trigger is wired into a shipped command | `publish_purge_for_withdrawal` called from `cli/commands.py:1483` |
| the closure is pinned | `tests/integration/test_absence_proof.py:2729::test_a_withdrawal_purges_the_published_index_without_a_separate_build` |
| the note asserts no re-measurement | stated in the note; the round-5/6/7 figures were taken against a build that still held withdrawn rows, and no open issue owns re-running them (searched `gh issue list --state open` for `T-17`, `ranking`, `residual`, `purge`, `timing`, `withheld`, and `gh search issues --state open 're-measure'`, 2026-08-31 — #344, #334, #216, #199, #154, #131, #25 returned; none owns it) |

The last row is the discipline that matters most here: the easy version of this
fix says "the purge shipped, so the residual is closed", and nothing in the
repository measured that.

## The three not-defects that look like defects

Recorded because a later reader will re-open each of them otherwise.

| Cite | Why it is not a defect |
| :-- | :-- |
| `#15:4794`, `#15:4808` | the three conditions of a **Milestone 5 acceptance**, inside T-17a, whose heading and opening blockquote both record the closure. The stated exemption: "the text an explicit acceptance record". Rewriting the conditions would delete the acceptance |
| `#119:4928` | "**Accepted, with the acceptance recorded.** The decision is on [#119]" — a cite to *where a decision is recorded*, which is history, not an owner |
| `#119:4398` | "deferred to [#119]" inside a paragraph the very next line marks as preserved: "**Amended in #119 (2026-08-24)** … The paragraph above is the Milestone 6 record and stays" |

## Key B members in this file (#428's accounting)

Key B is the repo-wide `#129`/`#39`/`#198` population. Its members inside the
threat model are #427's to fix; listed here so #428 can discharge them without
re-deriving.

The rev is in the command, not only in the prose — the fixes below add a `#39`
mention of their own, so the same key run against the working tree returns 12:

```console
$ git grep -nE "issues/(129|39|198)[^0-9]|(^|[^0-9A-Za-z/])#(129|39|198)([^0-9]|$)" \
    5a9a1e5 -- docs/security/threat-model.md | wc -l
11
```

Of those 11, **2 were defective**:

| Line | Number | Context | Class | Action |
| :-- | :-- | :-- | :-- | :-- |
| 1388 | 129 | "> [#129] was closed `COMPLETED` on 2026-08-22 having corrected this entry's wording" | (b) history | none — inside the C-2 correction record |
| 1521 | 198 | "against #198's round-two security review" | (b) history | none |
| **1639** | **198** | "([#198] tracks the family)" | **(a) DEAD** | **repointed to #329** |
| 2489 | 39 | "tracked at [#80] since [#39] closed on its documentation half" | (b) history | none — the model form |
| 2526 | 39 | "That is the half of [#39]'s release gate that was met" | (b) history | none |
| 2563 | 39 | "half of what [#39] recorded as a condition on the release" | (b) history | none |
| **2624** | **39** | "and that is the gap #39 inherits" | **(a) DEAD** (bare) | **corrected in place** — see below |
| 2629 | 39 | "Filed as [#39], which is now **closed** … The live owner is [#80]" | (b) history | none — the model form |
| 5498 | 129 | "owned by [#429] (#129 closed on the wording, not the controls)" | (b) history | none |
| 5506 | 198 | "([#329]; #198 is closed, having shipped the `propose accept` half)" | (b) history | none |
| 5507 | 39 | "([#80]; #39 is closed, on its documentation half only)" | (b) history | none |

### `:2624`, and the false evidence it carried

The sentence was:

> Nothing in this repository holds any of the three to the step's own words — no
> test reads `README.md`, `packages/theurian-core/CHANGELOG.md` or this file, and
> `test_setup_claims.py` reads the *plugin's* README, not the root one — and that
> is the gap #39 inherits.

Two defects in one sentence, and only the first is an owner cite.

**The owner.** #39 is closed and inherits nothing. Its live successor #80 is
named nine lines below — but #80's scope is *the shipped probe string and what
that string's pin should assert*, not a test tying the three surfaces to the
probe's words. So the correction does not repoint: it states the gap and records
that it has no owner, which is the file's own idiom at `:3652` ("It is not
carried by an open issue any more, and that is a deliberate statement rather than
an omission").

**The evidence.** The parenthetical is false, measured at `5a9a1e5`:

```console
$ git grep -ln 'README\.md' -- packages/theurian-core/tests | wc -l
7
$ git grep -n 'README\.md' -- packages/theurian-core/tests/unit/test_setup_claims.py
129:#: ``README.md`` is the fifth, and it joins after three successive exclusions
166:    "plugins/claude-code/README.md",
169:    "README.md",
```

Line 169 is the **root** README, inside `CORE_ARRIVAL_SURFACES` — which this very
entry already records twenty lines earlier, in the #323 paragraph: "the file
joined the tuple in the same change". The document contradicted itself.

The conclusion survives on a narrower fact, which is what the corrected sentence
now carries:

```console
$ git grep -ln 'probe_artifact_integrity\|artifact_integrity' -- packages/theurian-core/tests
packages/theurian-core/tests/unit/test_artifact_integrity_claim.py
packages/theurian-core/tests/unit/test_dogfood_corpus_governance.py
$ git grep -n 'REPO_ROOT\|RELEASE_DOC' -- packages/theurian-core/tests/unit/test_artifact_integrity_claim.py
121:REPO_ROOT: Final = pathlib.Path(__file__).resolve().parents[4]
122:RELEASE_DOC: Final = REPO_ROOT / "docs" / "contributing" / "release.md"
514:    document = RELEASE_DOC.read_text(encoding="utf-8")
536:    document = RELEASE_DOC.read_text(encoding="utf-8")
```

`RELEASE_DOC` is the only path the module builds off `REPO_ROOT`, and both reads
are of it.

Two modules name the probe; the one that holds it reads `release.md` and none of
the three. **This is a corrected claim about what the codebase contains, so it
owes a bidirectional pin under the corrected-claim rule — requested in the report,
not authored here.**

## The escape space: 97 bare mentions

Bare `#N` is outside the stated key. It was swept anyway, because #427's own
finding is a cite that a stated key missed, and #420's note on this issue asks
for exactly this widening. Known false positives, left in rather than
special-cased: Mermaid hex colours (`fill:#1f6f4a`) and in-document ordinals
(`residual #2`, `residual #1`).

**Two (a)-DEAD members, both the same claim, both PR-as-owner:**

| Line | Context | Action |
| :-- | :-- | :-- |
| 4492 | "a recorded MEDIUM deferred to **#113** (ADR-0022), whose compare-and-swap pointer write is its scope, not this fix's" | repointed to **#439**, mechanism reference (ADR-0022/ADR-0018) kept |
| 5509 | T-17a summary row: "all SAFE-direction, the last deferred to **#113**/ADR-0022" | same |

#113 is a **merged pull request** (2026-08-10, "feat(index)!: a purge is a
build"). A merged PR cannot receive work — the shape the shared measurement calls
the #444-recorded PR-as-owner class.

**The repoint target was verified twice before it was written**, which is the
discipline that caught #429 covering the allowlist:

- #439's own body, item 3: "**The derived index's single-writer contract.**
  ADR-0018:~207 … Re-measured 2026-08-31 at `6b83be1`:
  `git grep -n "write_lock|flock|lockf|Lock("` over `index_purge.py`,
  `index_store.py`, `application/withdrawal_purge.py`, `cli/index_commands.py`
  → zero hits". The residual at `:4492` is named "same lock-free-pointer-write
  class as the success purge path (`withdrawal_purge` publishing `new_id` under
  no index-write lock)" — the same symbol, in the same file, in #439's own
  measured file set.
- #444, filed independently against the **source** twin of this sentence
  (`application/project_service.py:1066-1068`, "a compare-and-swap pointer write
  is #113's scope"), states the answer directly: "The owed CAS/pointer-discipline
  work belongs to the derived-index contract #439 records." Its premises are
  recorded as re-verified by the orchestrator against `origin/main`.

The threat-model twins are #427's (this file); the source twin stays #444's and
was not touched.

**Everything else in the escape space classified (b) or (a)-open.** The dense
clusters are `#30` (14), `#158` (11), `#119` (8) — all historical, all "since
#N" / "#N closed it" / "#N PR2" forms — and `#338`/`#329` in owner position with
both issues OPEN.

## The full Key A table

114 rows, in file order. `(b)` = history, CLOSED is correct. `(a) open` = owner
cite whose issue is OPEN. `(a) acc` = explicit acceptance record. `(a) DEAD` =
the defect.

| # | Line | Context | Class |
| :-- | --: | :-- | :-- |
| 199 | 487 | "each named by the symbol in `src/` that implements it ([#199])" | (b) |
| 215 | 494 | "refused unread … `read_source_file`, through `_unbounded_shape` ([#215])" | (b) |
| 232 | 496 | "`MAX_PROJECTION_NODES` … threaded through `_walk` ([#232])" | (b) |
| 245 | 497 | "enters each parsed node once … `descended` ([#245])" | (b) |
| 328 | 583 | "**Discharged: the `$ref` walk's path strings ([#328]).**" | (b) |
| 331 | 611 | "**Also discharged, in another parser … ([#331]).**" | (b) |
| 26 | 667 | "What [#26] (a8c1ce3) added is … a lock wait for an admission permit" | (b) |
| 328 | 684 | "~4.39 MiB in 16.93 s, [#328]" | (b) |
| 331 | 686 | "156.2 KiB in 25.12 s, [#331]). **Both are discharged now**" | (b) |
| 26 | 698 | "What [#26] filed for those members is discharged instead" | (b) |
| 291 | 704 | "it carries its own ingestion bounds ([#291], [#289])" | (b) |
| 289 | 705 | same line | (b) |
| 245 | 733 | "the OpenAPI `$ref` walk, which [#245] *memoised*" | (b) |
| 158 | 821 | "since [#158] it materialises every *surfaceable* `KnowledgeItem`" | (b) |
| 19 | 839 | "shed that shape under Milestone 6's T-17 timing fix ([#19])" | (b) |
| 26 | 913 | "as of [#26] (a8c1ce3, 2026-08-30) — it is no longer true that…" | (b) |
| 26 | 1008 | "**Shipped** ([#26], a8c1ce3, 2026-08-30)" | (b) |
| 17 | 1140 | "[#17] has now bounded it" | (b) |
| 17 | 1160 | "**Closed by [#17] (db36089)**" | (b) |
| 306 | 1190 | "Controls on `propose accept`'s body-materialisation cost ([#306], [#400])" | (b) |
| 400 | 1191 | same sentence | (b) |
| 203 | 1279 | "with the scheme a fetcher would use ([#203])" | (b) |
| 245 | 1312 | "now costs 1.5 ms ([#245], measured 2026-08-24 and pinned by …)" | (b) |
| 328 | 1317 | "unpriced and quadratic … ([#328]) — that residual is discharged now too" | (b) |
| 246 | 1325 | "`$dynamicRef`, `operationRef` … outside this walk entirely ([#246])" | (a) open |
| 203 | 1332 | "which is the property [#203] needed" | (b) |
| 429 | 1366 | "*Future controls, not shipped* … owned by [#429]" | (a) open |
| 129 | 1388 | "[#129] was closed `COMPLETED` … having corrected this entry's wording" | (b) |
| 368 | 1391 | "this audit repointed the three at [#368] … Review then found" | (b) |
| 429 | 1395 | "[#429] was opened to hold them against whatever first performs a fetch" | (a) open |
| 329 | 1456 | "runs no scan — shipped behaviour today, not a future gap ([#329])" | (a) open |
| 330 | 1459 | "Draft time is … advisory territory ([#330])" | (a) open |
| 336 | 1472 | "the accept-path scan has read it since [#336]" | (b) |
| 336 | 1487 | "(SEC-11, ADR-0027 decision 3, [#336])" | (b) |
| 349 | 1504 | "and the artifacts it lands them as ([#349])" | (b) |
| 63 | 1562 | "the engine refuses any value but `local` and `default` ([#63])" | (b) |
| 361 | 1611 | "That it rides into the PR this way is tracked as [#361]" | (a) open |
| 330 | 1613 | "tracked with the draft-time advisory ([#330])" | (a) open |
| 349 | 1615 | "The artifact level [#349] opened … is now scanned" | (b) |
| 360 | 1623 | "tracked in [#360]" | (a) open |
| 336 | 1633 | "those are scanned ([#336], [#349])" | (b) |
| 349 | 1634 | same sentence | (b) |
| **198** | **1639** | **"([#198] tracks the family)"** | **(a) DEAD** |
| 41 | 1913 | "**Amended after [#41], which replaced the check rather than tightening it**" | (b) |
| 212 | 2106 | "[#212] registered `theurian propose` and `theurian propose accept`" | (b) |
| 89 | 2108 | "(closing [#89])" | (b) |
| 90 | 2111 | "That printing is true only from [#90]" | (b) |
| 42 | 2128 | "**Resolved by delegation in [#42].**" | (b) |
| 40 | 2177–2182 | six rows of the *Corrected in* table | (b) ×6 |
| 34 | 2183 | seventh row of the same table | (b) |
| 40 | 2185 | "[#40] took the first six in one change" | (b) |
| 40 | 2204 | "documents [#40] did not reach" | (b) |
| 421 | 2209 | "[#421], fixed by [#435]" | (b) |
| 435 | 2209 | same cell | (b) |
| 421 | 2210 | second row, same pair | (b) |
| 435 | 2210 | same cell | (b) |
| 34 | 2243 | "**`README.md`'s two places were corrected in [#34].**" | (b) |
| 82 | 2296 | "Adding the file to the tuple was tried in [#82] and reverted" | (b) |
| 323 | 2302 | "[#323] qualified every install remedy with `--python 3.13`" | (b) |
| 78 | 2418 | "[#78] found that `uv tool install theurian` resolves and installs…" | (b) |
| 82 | 2423 | "The instructing surfaces moved to `theurian[daemon]` in [#82]" | (b) |
| 323 | 2440 | "both qualified in [#323]" | (b) |
| 54 | 2456 | "measured at `eb17a2e` after [#54] opened the `[0.1.0.dev0]` section" | (b) |
| 71 | 2472 | "a pull request that was open at the time ([#71])" | (b) |
| 323 | 2479 | "[#323] closed both" | (b) |
| 80 | 2488 | "The rest of the release gate stays open, tracked at [#80]" | (a) open |
| 39 | 2489 | "since [#39] closed on its documentation half" | (b) |
| 39 | 2526 | "the half of [#39]'s release gate that was met" | (b) |
| 60 | 2538 | "`3280bc9` ([#60]) retired both" | (b) |
| 59 | 2556 | "[#59], recorded here as in flight, landed as `c2a5406`" | (b) |
| 39 | 2563 | "half of what [#39] recorded as a condition on the release" | (b) |
| 56 | 2610 | "before [#56], where the section ran 1326 lines" | (b) |
| 39 | 2629 | "Filed as [#39], which is now **closed** — on its documentation half" | (b) |
| 80 | 2633 | "The live owner is [#80], which diagnoses exactly that split" | (a) open |
| 20 | 2695 | "is filed as LOW at [#20]" | (a) open |
| 119 | 2768 | "used to be described here as holding 'until [#119]'; #119 closed…" | (b) |
| 117 | 3118 | "now 4 since [#117] dropped `knowledge_revisions`' … CHECK" | (b) |
| 19 | 3137 | "**`appliedMigrations` was accepted for Milestone 5 and filed at [#19].**" | (b) |
| 19 | 3158 | "**Discharged by [#19]**" | (b) |
| 20 | 3170 | "[#20] named two tools and stays open for the other one" | (a) open |
| 19 | 3226 | "The fix ([#19], commit `2793d7b`) counts the surfaceable statuses in SQL" | (b) |
| 158 | 3245 | "is now closed the same way by [#158] this milestone" | (b) |
| 117 | 3415 | "a derived `range(1, SCHEMA_VERSION)` until [#117]" | (b) |
| 30 | 3654 | "[#30]'s closure condition was the deletion of `SILENTLY_EMPTIED`" | (b) |
| 16 | 3907 | "**Amended in Milestone 6, when [#16] landed.**" | (b) |
| 16 | 3961 | "[#16] states this about itself" — inside the block :3907 amends | (b) |
| **15** | **3970** | **"removed by the same change and by nothing smaller — … [#15]"** | **(a) DEAD** |
| **15** | **4108** | **"the index purge in [#15] removes this face"** | **(a) DEAD** |
| **15** | **4184** | **"the Milestone 6 index purge and blue/green build, [#15], removes…"** | **(a) DEAD** |
| 16 | 4206 | "Its real fix is the explicit exhaustion signal in [#16]" | (b) by frame (:4279) |
| 16 | 4274 | "Both go with the cache when [#16] lands" | (b) by frame (:4279) |
| 16 | 4283 | "both tests were deleted when [#16] gave `IndexStore` an … signal" | (b) |
| 15 | 4369 | "**Closed in Milestone 6 by the withdrawal→purge trigger ([#15])**" | (b) |
| 119 | 4398 | "deferred to [#119]" — the M6 record the next line amends | (b) by frame (:4401) |
| 344 | 4454 | "a disk-forensics surface, tracked as [#344]" | (a) open |
| 15 | 4794 | "filed at HIGH against Milestone 6 as [#15]" | (a) acc |
| 15 | 4808 | "**Issue [#15] carries both channels.**" | (a) acc |
| 338 | 4922 | "invalidates every existing state database — owned by [#338]" | (a) open |
| 119 | 4928 | "**Accepted, with the acceptance recorded.** The decision is on [#119]" | (a) acc |
| 119 | 5153 | "sensitivity was still deferred to [#119] … #119 has since added" | (b) |
| 210 | 5160 | "fixed in 0.1.0.dev5 (GHSA-w5cm-cqf9-vm7r, [#210])" | (b) |
| 128 | 5405 | "overwritten whole … until [#128]" | (b) |
| 429 | 5498 | T-7 summary row: "owned by [#429]" | (a) open |
| 336 | 5506 | T-15 summary row: "author-written fields ([#336])" | (b) |
| 330 | 5506 | same row: "are not read ([#330])" | (a) open |
| 329 | 5506 | same row: "do not ship ([#329]…)" | (a) open |
| 80 | 5507 | T-16 summary row: "install-time verification unmet ([#80]…)" | (a) open |
| 344 | 5509 | T-17a summary row: "the free-page byte residue ([#344]) is recorded" | (a) open |
| 338 | 5514 | T-22 summary row: "flattening owned by [#338]" | (a) open |

## Out of scope, found and not fixed

Reported to the orchestrator for box-splitting rather than folded in.

1. **A correction with a forward pointer and no back pointer.** T-17a's
   Milestone 5 condition 3, at `:4823`, predicts of
   `test_a_withheld_document_can_still_reorder_the_visible_ones` and its sibling:
   "Both go red when Milestone 6 closes the stale window, which is the intended
   alarm". They do not. The entry records that at `:4506` — "**A prediction this
   entry made was wrong** … Condition 3 below expected … They do not: they build
   a stale index directly, outside the `migrate apply` path" — so the correction
   *names* the condition it corrects, 317 lines above it. Condition 3 carries no
   pointer back, so a reader arriving there by search meets the falsified
   prediction alone. LOW, not an owner cite, out of this sweep.
2. **The cross-surface artifact-integrity pin has no owner** (see `:2624`
   above). #80 covers the shipped string and its pin; nothing covers a test
   tying `README.md`, `packages/theurian-core/CHANGELOG.md` and this file to
   `probe_artifact_integrity`'s words. Recorded in the entry; no issue filed
   from here.
3. **#154 is open against this same file** ("threat-model cites tests that no
   longer exist: `test_scan_cache.py` module and two renamed/deleted scan-cache
   controls"). A cite-shaped population one key over from POP-2 — a *test* cite
   rather than an *issue* cite. Not swept here; named so the next sweep does not
   rediscover it as new.
