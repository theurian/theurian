# The repo-wide closed-owner sweep for #129, #39 and #198 (#428)

#427 swept one file for owner cites of every number. This is the other half of
that split: three numbers, the whole tree. #129, #39 and #198 are all closed, and
all three were still being cited as the owner of work that does not ship.

## KEY, INSTRUMENT, SCOPE

**KEY** — the issue's own pasted population key, unchanged:

```sh
git grep -nE "issues/(129|39|198)[^0-9]|(^|[^0-9A-Za-z/])#(129|39|198)([^0-9]|$)"
```

**INSTRUMENT** — `git grep`, never `rg`. This repository serves a corpus under
`.theurian/`, and `rg` honours `.git/info/exclude` and skips dot directories
without `--hidden`; both are ways to under-count here and both have cost this
arc a population before.

**SCOPE** — the full tracked tree at this branch's base, `e546c15`
(`origin/main` when the branch was cut). The shared instrument run
([#199 issuecomment-5480024637](https://github.com/theurian/theurian/issues/199#issuecomment-5480024637))
recorded **164 lines / 44 files** at `5a9a1e5`; main moved five commits in
between, so the key was re-run here rather than carried:

```console
$ git grep -nE "issues/(129|39|198)[^0-9]|(^|[^0-9A-Za-z/])#(129|39|198)([^0-9]|$)" e546c15 | wc -l
199
```

**The unit is a cite, not a line, and not a number.** `docs/roadmap.md:630`
carries one #129 cite and one #198 cite, classified separately and repointed at
different issues; a markdown link spells the same cite twice (`[#198]` and the
URL behind it) and is one cite. So the classified population is **214 rows over
199 lines over 47 files**, and the 199 above is the line count that key prints,
stated separately so the two can never be read as the same number.

The brief's dispatch note said `origin/main = 53d1655`; it was `e546c15` by the
time the worktree was cut, one commit later (#474, the corpus-population pin).
Everything here is anchored at `e546c15`.

### What the key does not reach

- **A number named without `#` or a URL** — "the review-ingestion issue", "issue
  129". Not measured; no key of this shape can reach it.
- **Untracked files.** `git grep` reads tracked content only. Measured here:
  `git status --short --untracked-files=all` printed nothing at `e546c15`, so
  the tracked tree *is* the tree.
- **The served corpus** is inside the key's reach and holds no member:
  `git grep -nE <key> e546c15 -- .theurian` returns nothing. Recorded because a
  corpus file would have been a fence violation to fix, and "there were none" is
  a measurement rather than an assumption.
- **One false positive is real and stays**, `docs/protocol/plugin-core-compatibility.md:131`:
  `&#39;` is the HTML entity for an apostrophe inside a Mermaid label. The
  character before `#` is `&` and the character after `39` is `;`, so the key
  matches. Left in the population and classified rather than special-cased —
  #470 did the same with Mermaid hex colours.

## The classification rule

From the issue body, and it is the whole reason the fix set is 28 rows and not
199. **A cite is stale only if it names the closed issue as owner or authority of
something that does not ship, judged by the closure *reason*, not the state:**

| Number | Closed | Why it matters here |
| :-- | :-- | :-- |
| #129 | 2026-08-22, on a **documentation** fix | The three T-7 controls it described stayed unbuilt. Every owner cite is stale; every "as #129 recorded" is correct |
| #39 | 2026-08-07, on a **documentation** fix | The artifact-integrity strings were rewritten; install-time verification stayed unbuilt |
| #198 | 2026-08-24, **by shipping** its control | `propose accept` scans bodies today. A cite is stale only where it points at the *unshipped siblings* — ingest-time and index-time scanning — and correct everywhere it points at the gate |

| Label | Rule | Count |
| :-- | :-- | --: |
| **(a) DEAD** | owner-shaped, cited issue closed, work unbuilt — fixed in this branch | **28** |
| (a) DEAD, routed | same class, but the fix belongs to another issue's coordinated change | 8 |
| (b) history | "since #N", "#N established", "#N closed on", a provenance cite for a shipped guard. A closed owner is correct | 177 |
| (fp) | the key matched something that is not a cite | 1 |

214 total. The tallies are the classifier's output below, not a count made by
reading — this arc has already lost three counts to hand-applied keys.

## The classifier, and its output

The classification is held as data and checked against the population, so a row
classified at a line the key does not return is an error rather than a silent
extra. Run from the repository root:

```python
"""Classify the #428 population and check the classification covers it exactly."""

from __future__ import annotations

import collections
import re
import subprocess
import sys

KEY = r"issues/(129|39|198)[^0-9]|(^|[^0-9A-Za-z/])#(129|39|198)([^0-9]|$)"
ROOT = "."

# -- per-row classification, only where it differs from the file default ------
# class: a-dead (fix here), a-routed (fix belongs to another issue),
#        b (history, correct), fp (key false positive)
ROW: dict[tuple[str, int, str], str] = {}


def rows(spec: str, cls: str) -> None:
    path, rest = spec.split(" ", 1)
    for token in rest.split():
        line, number = token.split("#")
        ROW[(path, int(line), number)] = cls


# (a) DEAD, fixed in this branch
rows("README.md 232#39 598#39", "a-dead")
rows("docs/architecture/requirements-analysis.md 419#39", "a-dead")
rows("packaging/README.md 33#39", "a-dead")
rows("docs/roadmap.md 259#129 260#198 630#129 630#198", "a-dead")
rows("docs/roadmap.md 273#39", "a-dead")
rows("docs/architecture/source-normalization.md 170#129 207#129", "a-dead")
rows("plugins/claude-code/commands/ingest.md 38#129", "a-dead")
rows("examples/sample-project/.theurian/config.yaml 62#198", "a-dead")
rows(
    "packages/theurian-core/src/theurian/infrastructure/filesystem/parsers/openapi.py"
    " 101#129 189#129 532#129",
    "a-dead",
)
rows("packages/theurian-core/src/theurian/review/__init__.py 8#129", "a-dead")
rows("packages/theurian-core/src/theurian/security/project_config.py 13#129", "a-dead")
rows(
    "packages/theurian-core/tests/unit/test_network_call_sites.py 647#129 756#129 898#129", "a-dead"
)
rows(
    "packages/theurian-core/tests/unit/test_ref_recording.py 10#129 158#129 192#129 971#129",
    "a-dead",
)
rows("packages/theurian-core/tests/integration/test_mcp_tools.py 1975#129", "a-dead")
rows("packages/theurian-core/tests/unit/test_examples.py 173#198", "a-dead")
rows("packages/theurian-core/tests/unit/test_findings_store_is_unreachable.py 25#129", "a-dead")

# (a) DEAD, routed to another issue's coordinated change
rows("schemas/config/project-config.schema.json 5#129 74#129 147#198", "a-routed")
rows("packages/theurian-core/tests/unit/test_config_key_call_sites.py 562#198 571#129", "a-routed")
rows("packages/theurian-core/src/theurian/application/setup_steps.py 339#39", "a-routed")
rows("docs/contributing/release.md 309#39", "a-routed")
rows("packages/theurian-core/tests/unit/test_artifact_integrity_claim.py 124#39", "a-routed")

# key false positive
rows("docs/protocol/plugin-core-compatibility.md 131#39", "fp")

DEFAULT = "b"

# -- derive the population ---------------------------------------------------
raw = subprocess.run(
    ["git", "grep", "-nE", KEY, "e546c15"], cwd=ROOT, capture_output=True, text=True, check=True
).stdout.splitlines()
pat = re.compile(KEY)
population: list[tuple[str, int, str]] = []
for line in raw:
    _, path, lineno, text = line.split(":", 3)
    numbers: list[str] = []
    for match in pat.finditer(text):
        number = match.group(1) or match.group(3)
        if number not in numbers:
            numbers.append(number)
    population.extend((path, int(lineno), number) for number in numbers)

extra = sorted(set(ROW) - set(population))
if extra:
    print("CLASSIFIED ROWS THAT ARE NOT IN THE POPULATION:")
    for row in extra:
        print(f"  {row}")
    sys.exit(1)

tally = collections.Counter(ROW.get(row, DEFAULT) for row in population)
print(f"lines={len(raw)} rows={len(population)} files={len({p for p, _, _ in population})}")
print("by class:", dict(sorted(tally.items())))
print("by number:", dict(sorted(collections.Counter(n for _, _, n in population).items())))
```

```console
lines=199 rows=214 files=47
by class: {'a-dead': 28, 'a-routed': 8, 'b': 177, 'fp': 1}
by number: {'129': 80, '198': 85, '39': 49}
```

**`b` is a default, not an entry**, and that is deliberate: a new member of the
population is (b) until someone classifies it, and the 177 is what the default
returns rather than 177 judgements typed out. The judgement is in the rule table
above; the rows that carry an individual judgement are the 37 named in `ROW`.

## The full table, one row per cite

Every non-(b) cite is named individually with its line and number; the (b) bulk
is given as a per-file count, which is what the classifier's default produces.
Line numbers are `e546c15`'s.

| File | Cites | Classification |
| :-- | --: | :-- |
| `README.md` | 2 | `:232` #39 **(a) DEAD**; `:598` #39 **(a) DEAD** |
| `SECURITY.md` | 1 | (b) ×1 |
| `docs/adr/0027-accept-validates-before-it-moves.md` | 3 | (b) ×3 |
| `docs/architecture/requirements-analysis.md` | 3 | (b) ×2 · `:419` #39 **(a) DEAD** |
| `docs/architecture/review-knowledge.md` | 1 | (b) ×1 |
| `docs/architecture/source-normalization.md` | 2 | `:170` #129 **(a) DEAD**; `:207` #129 **(a) DEAD** |
| `docs/contributing/release.md` | 1 | `:309` #39 (a) DEAD, routed |
| `docs/protocol/plugin-core-compatibility.md` | 1 | `:131` #39 (fp) |
| `docs/roadmap.md` | 5 | `:259` #129 **(a) DEAD**; `:260` #198 **(a) DEAD**; `:273` #39 **(a) DEAD**; `:630` #129 **(a) DEAD**; `:630` #198 **(a) DEAD** |
| `docs/security/threat-model.md` | 12 | (b) ×12 |
| `docs/work-logs/2026-08-18-milestone-7-dogfooding-first-slice.md` | 2 | (b) ×2 |
| `docs/work-logs/2026-08-19-milestone-7-dogfooding-dev7-corpus.md` | 1 | (b) ×1 |
| `docs/work-logs/2026-08-30-199-unit-a-audit.md` | 14 | (b) ×14 |
| `docs/work-logs/2026-08-31-427-owner-cite-sweep.md` | 30 | (b) ×30 |
| `examples/sample-project/.theurian/config.yaml` | 2 | (b) ×1 · `:62` #198 **(a) DEAD** |
| `packages/theurian-core/CHANGELOG.md` | 29 | (b) ×29 |
| `packages/theurian-core/src/theurian/application/proposal_service.py` | 1 | (b) ×1 |
| `packages/theurian-core/src/theurian/application/setup_steps.py` | 1 | `:339` #39 (a) DEAD, routed |
| `packages/theurian-core/src/theurian/infrastructure/filesystem/parsers/openapi.py` | 3 | `:101` #129 **(a) DEAD**; `:189` #129 **(a) DEAD**; `:532` #129 **(a) DEAD** |
| `packages/theurian-core/src/theurian/infrastructure/github/__init__.py` | 1 | (b) ×1 |
| `packages/theurian-core/src/theurian/review/__init__.py` | 1 | `:8` #129 **(a) DEAD** |
| `packages/theurian-core/src/theurian/security/project_config.py` | 2 | (b) ×1 · `:13` #129 **(a) DEAD** |
| `packages/theurian-core/tests/integration/test_mcp_tools.py` | 4 | (b) ×3 · `:1975` #129 **(a) DEAD** |
| `packages/theurian-core/tests/integration/test_proposal_secret_scan.py` | 3 | (b) ×3 |
| `packages/theurian-core/tests/integration/test_propose_cli.py` | 2 | (b) ×2 |
| `packages/theurian-core/tests/unit/test_adr_0013_claims.py` | 2 | (b) ×2 |
| `packages/theurian-core/tests/unit/test_adr_0018_claims.py` | 2 | (b) ×2 |
| `packages/theurian-core/tests/unit/test_artifact_integrity_claim.py` | 9 | (b) ×8 · `:124` #39 (a) DEAD, routed |
| `packages/theurian-core/tests/unit/test_config_key_call_sites.py` | 18 | (b) ×16 · `:562` #198 (a) DEAD, routed; `:571` #129 (a) DEAD, routed |
| `packages/theurian-core/tests/unit/test_content_secrets.py` | 3 | (b) ×3 |
| `packages/theurian-core/tests/unit/test_dogfood_corpus_governance.py` | 2 | (b) ×2 |
| `packages/theurian-core/tests/unit/test_examples.py` | 6 | (b) ×5 · `:173` #198 **(a) DEAD** |
| `packages/theurian-core/tests/unit/test_findings_store_is_unreachable.py` | 1 | `:25` #129 **(a) DEAD** |
| `packages/theurian-core/tests/unit/test_ingest_manifest_path.py` | 7 | (b) ×7 |
| `packages/theurian-core/tests/unit/test_network_call_sites.py` | 9 | (b) ×6 · `:647` #129 **(a) DEAD**; `:756` #129 **(a) DEAD**; `:898` #129 **(a) DEAD** |
| `packages/theurian-core/tests/unit/test_project_and_traceability.py` | 1 | (b) ×1 |
| `packages/theurian-core/tests/unit/test_project_config.py` | 1 | (b) ×1 |
| `packages/theurian-core/tests/unit/test_raptor_config_claims.py` | 3 | (b) ×3 |
| `packages/theurian-core/tests/unit/test_ref_recording.py` | 4 | `:10` #129 **(a) DEAD**; `:158` #129 **(a) DEAD**; `:192` #129 **(a) DEAD**; `:971` #129 **(a) DEAD** |
| `packages/theurian-core/tests/unit/test_schemas.py` | 5 | (b) ×5 |
| `packages/theurian-core/tests/unit/test_threat_model_t16_claims.py` | 1 | (b) ×1 |
| `packages/theurian-core/tests/unit/test_threat_model_twins.py` | 3 | (b) ×3 |
| `packaging/README.md` | 1 | `:33` #39 **(a) DEAD** |
| `plugins/claude-code/CHANGELOG.md` | 3 | (b) ×3 |
| `plugins/claude-code/commands/ingest.md` | 1 | `:38` #129 **(a) DEAD** |
| `schemas/config/project-config.schema.json` | 4 | (b) ×1 · `:5` #129 (a) DEAD, routed; `:74` #129 (a) DEAD, routed; `:147` #198 (a) DEAD, routed |
| `tools/audit/threat_model_owner_cites.py` | 1 | (b) ×1 |

## Target verification, before any repoint was written

Two of this arc's three ownership mistakes were repoints to an issue whose scope
did not cover the work — T-7 to #368, corrected by #429 — so each target was
read before it was named.

| Target | Verified against | Covers |
| :-- | :-- | :-- |
| **#429** (OPEN) | its own body: "The three controls ... a **scheme allowlist**, **private-network/link-local rejection**, and a **repository allowlist** (SEC-10) ... conditioned on **any** external fetch path landing ... Until a fetch path exists, this issue is the recorded owner the threat model cites" | every T-7 fetch-control cite: `openapi.py` ×3, `project_config.py`, `test_ref_recording.py` ×4, `test_network_call_sites.py` ×3, `test_mcp_tools.py`, `roadmap.md` ×2 |
| **#329** (OPEN) | title "Ingested content is never secret-scanned: theurian ingest indexes approved bodies with no SEC-11 pass"; its body quotes #198's own 2026-08-17 measurement of that path | the ingest/index-time siblings: `roadmap.md` ×2, the sample config's `secretScan` annotation and its pin |
| **#80** (OPEN) | its body diagnoses the split — "#39 is CLOSED (completed) ... It was closed correctly: its release-gate half was discharged ... **But the other half is not discharged.**" — and asks for "a successor issue for T-16's install-time residual" | the T-16 install-time cites: `README.md` ×2, `packaging/README.md`, `requirements-analysis.md` |

**#80's cover is partial and is stated as such wherever it is named.** #80 owns
the *pointer* problem and records that a dedicated successor for the control is
still owed; it does not own the control. Every repoint therefore carries "#39
closed on its documentation half while the install-time control stayed unbuilt",
which is the form the threat model's T-16 entry already uses at `:2685`.

### Where no repoint was available

**Five cites over four files** named #129 as the owner of unbuilt **GitHub review
ingestion**, and no open issue owned that. **This paragraph is the authority for
that count**, and the vocabulary is settled here rather than per-site: it is
*five cites*, and where *four* appears it is the file count. Derived by asking
which `a-dead` rows repoint at #479 — not by listing files:

| Row at `e546c15` | Its base text |
| :-- | :-- |
| `source-normalization.md:170` | `owed with review ingestion (Milestone 7, [#129])` |
| `source-normalization.md:207` | `when review ingestion lands (Milestone 7, [#129])` |
| `review/__init__.py:8` | `` (`#129 <…/issues/129>`_) `` |
| `ingest.md:38` | `(Milestone 7, [#129])` |
| `test_findings_store_is_unreachable.py:25` | `` (Milestone 7, ``#129``) `` |

All five are the same shape. The fifth is an **owner cite** at base, not a
paraphrase — it became one because **this branch re-shaped it**, in `8c48c32`,
and the count was taken afterwards.

That is the same construction error twice. The sentence first said "three",
counting sites a pre-written table named. Corrected, it said "four … which is the
classifier's own count for those files" — a true statement about those files, and
still the wrong number, because keying the count on *files* let a member drop
out: `test_findings_store_is_unreachable.py` was not on the list, and it was not
on the list because by the time the sentence was written its text was already a
paraphrase. The branch's own edit had erased the shape the count was looking for.
A count keyed on **what each fix targets** cannot do that, which is why the table
above is keyed that way. Both corrections are recorded on #479
([first](https://github.com/theurian/theurian/issues/479#issuecomment-5484550729),
[second](https://github.com/theurian/theurian/issues/479#issuecomment-5485164975)).

Measured 2026-09-01 over the full open
set (164 issues,
`gh issue list --state open --limit 500`), by title and body, for
`reviewIngestion`, `KnowledgeCandidate`, `ReviewProvider`, `ReviewThread`,
`PromotionGate`, `FR-V5`, `Phase B` and `review ingestion`:

| Candidate | Why it is not the owner |
| :-- | :-- |
| #368 | "Review Ingestion: ingest Review-Finding trailers" — its own body says "this is a git-history source", and #429 was opened precisely because #368's scope reaches no fetch path |
| #223 | external tools (Jira, Confluence, Notion, Linear) — "an adjacent but different source class", in #368's words |
| #429 | the three SEC-10 fetch controls only |
| #200 | owns the `Git commit` and `Git diff` rows of the same table, and its body says the `GitHub review` row "is the review-ingestion face" and is *not* its scope |

So `source-normalization.md` (both cites), `review/__init__.py` and
`ingest.md` first stated the absence instead of naming a successor. Naming an
issue that does not cover the work is the same defect one number over, and it is
the defect #429 exists to have corrected.

**Resolved inside this branch, and recorded rather than rewritten.** The
measurement above is what got filed: **[#479](https://github.com/theurian/theurian/issues/479)**
was opened for GitHub review ingestion, adopting this candidate table and its
scoping — the ingestion path is #479's, the fetch controls stay #429's — and its
body names these same four sites. This branch's follow-up commit — the last one,
`docs(review)` — repoints all four, so the statements
are *owned* rather than *unowned-with-candidates*. The four-candidate analysis is
compressed at each site to the sentence that carries the point ("filed from this
sweep's measurement after four nearer candidates were each read and verified not
to cover it"); the table above stays here, in full, as the evidence behind it.

The absence was real when it was measured, and the record of measuring it is what
made the issue fileable — so it is kept above rather than replaced by its answer.

## The one member that is not an owner cite

`docs/roadmap.md:273` read:

> Tracked by [#80] — the summary table still points at #39, which is closed while
> its install-time half is not.

That is a claim about what another file in this repository contains, and it
stopped being true at `efd30fe` (#425), which repointed the threat model's T-16
summary row:

```console
$ git show efd30fe -- docs/security/threat-model.md | grep '^[-+]| T-16'
-| T-16 | Compromised release artifact | T | Critical | OSS-11 — publication only; install-time verification unmet (#39) |
+| T-16 | Compromised release artifact | T | Critical | OSS-11 — publication only; install-time verification unmet ([#80](https://github.com/theurian/theurian/issues/80); #39 is closed, on its documentation half only) |
```

The sentence now quotes what that row reads today. This is a corrected claim
about what the codebase contains, so it owed a bidirectional pin, and unlike the
tracker claims below it *has* a fact side: the string the roadmap quotes must
match the threat model's T-16 summary row, which goes RED from either direction.

**That pin is authored, not merely requested** — `1433a3d`, by the tests
specialist, in
`packages/theurian-core/tests/unit/test_roadmap_claims.py`. Both directions are
held, each on a premise asserted first rather than assumed:
`test_the_roadmap_quotes_what_the_t16_summary_row_reads_today` is the fact side,
reading the live row out of `docs/security/threat-model.md` rather than a copy of
it, so the record has to move when the row does; and the prose side refuses a
drift back to "the summary table still points at #39" unmarked.
`test_the_roadmap_carries_exactly_one_t16_release_gate_block` and
`test_the_threat_model_carries_exactly_one_t16_summary_row` hold the uniqueness
premises both rules rest on — a second block or a second row would let either
rule select the wrong text and pass.

## The claims that cannot be pinned, and why that is recorded

Who owns a gap is a fact about the tracker, not about this repository, and
**filing #479 did not change that** — "owned by #479" is exactly as unpinnable as
"owned by nobody" was, and for the same reason. #80 records it: a liveness check
"would reach the network from the unit suite, which this project does not do",
which is why its own pin asserts that *an* issue is named rather than that the
issue is open — and why that pointer went stale within a day of shipping. An
owner cite is the shape that rots; this sweep exists because thirty-six of them
did.

`source-normalization.md` says so in the correction itself, names the two other
places carrying the same cite (`review/__init__.py`,
`plugins/claude-code/commands/ingest.md`), and gives the claim its date. The
corrected-claim rule asks for a pin or the recorded reason there is none; that
paragraph is the recorded reason, and it survives the repoint unchanged in
substance.

## The coordinated pin, and the second defect it exposed

`examples/sample-project/.theurian/config.yaml`'s `secretScan` annotation ended
"`theurian ingest` and index building run no scan (#198)", and
`test_examples.py::ANNOTATED_KEYS` required the literal `"#198"` in that
annotation. Same shape as #448's face 4, where the `repositories` row moved from
#129 to #429 with its annotation.

**The naive coordinated change does not go RED, and that is the finding.** The
required token is matched as a substring, so the corrected annotation — which
still names #198 as closed history — satisfies the old row untouched:

```console
$ # annotation repointed to #329, ANNOTATED_KEYS row left at "#198"
$ uv run --frozen python -m pytest -q packages/theurian-core/tests/unit/test_examples.py
16 passed in 0.22s
```

The row's stated job is that "the annotation stays a claim someone owns", and a
closed number appearing anywhere in the block satisfied it. So the token moved to
`"#329"`, which is the live owner of the gap that sentence states, and the pin
was then demonstrated in both directions:

```console
$ # ANNOTATED_KEYS at "#329", annotation reverted to "(#198)" alone
$ uv run --frozen python -m pytest -q packages/theurian-core/tests/unit/test_examples.py
E   AssertionError: the annotation above `secretScan` in
E   examples/sample-project/.theurian/config.yaml no longer says '#329'.
E   assert '#329' in 'In force: what `theurian propose accept` does ...'
FAILED test_examples.py::test_a_key_the_example_sets_still_states_how_far_it_reaches[secretScan]
1 failed, 15 passed in 0.23s

$ # annotation restored
$ uv run --frozen python -m pytest -q packages/theurian-core/tests/unit/test_examples.py
16 passed in 0.21s
```

## Discharge accounting

Members dispositioned elsewhere. This sweep records them; it does not re-fix
them, and it does not count them as its own.

| Population members | Owner | Evidence |
| :-- | :-- | :-- |
| `docs/security/threat-model.md`, **12 cites** | **#470** | Its work log's *Key B members in this file* section classifies all of them and fixes the two defects (`:1639` #198 → #329, `:2624` #39 → stated unowned). Re-measured here at `e546c15`: 12 cites, all (b), zero defects surviving. #470's log predicted exactly this — 11 at `5a9a1e5`, 12 after its own fix added a `#39` mention |
| `schemas/config/project-config.schema.json` `:5`, `:74`, `:147` and the `WATCHED_KEY_DESCRIPTIONS` pin rows `:562`, `:571` | **#455 / #199 unit B** | Wheel-shipped (hatch force-include) and pinned, so description and pin must move together. `test_config_key_call_sites.py`'s own docstring already records that both "stay outside this PR because they land on `project-config.schema.json`, which #199 unit B owns" |
| `schemas/config/project-config.schema.json` `:154` | **nobody — nothing to discharge** | Classified **(b)**, so it is listed here only because the issue body named it beside `:74`. A correct history cite needs no owner and no follow-up; it is in this table to stop the next reader re-opening it, not because it is owed |
| `examples/sample-project/.theurian/config.yaml` `:23` (the `repositories` row) | **#448**, landed | Reads the model form today: "(#429 owns it; #129 was closed on the wording rather than the control)". Classified (b) |
| `application/setup_steps.py:339`, `docs/contributing/release.md:309`, `test_artifact_integrity_claim.py:124` | **#80** | The shipped `probe_artifact_integrity` detail, its byte-identical transcript, and `ISSUE_URL`. `test_the_release_document_quotes_what_setup_actually_publishes` compares the transcript to the published step, so the three move in one change — and that change is #80's stated scope, "the shipped probe string and what that string's pin should assert" |

**The stated exclusion list named `:74` and `:154`; the population at `e546c15`
holds four schema rows, not two.** `:5` is #455's own subject (the root
description's unnarrowed "Nothing in src/ reads this file") and `:147` is a live
(a)-DEAD #198 cite — "`theurian ingest` and index building run no scan
(https://.../issues/198)" — in the same wheel-shipped file, named in no recorded
exclusion. Both are routed alongside `:74` rather than silently dropped — three
routed rows, not four, because `:154` is not one of them.

`:154` is the one schema row classified **(b)**: "Nothing reads this key, so
changing the value here has no effect on that limit (#129)" cites #129 as the
authority for a measured absence, not as the owner of a control. Its twins in
`test_schemas.py:972` and `:1010` are classified the same way for the same
reason. Recorded because the issue body lists `:154` beside `:74`, and the two
are not the same class.

## The fixes

Split by the files rather than by the number. The **Cites** column is derived,
not typed: each `a-dead` row in the classifier is mapped to the first commit that
touched its file, and the column sums to the classifier's own 28. The first
version of this table typed the numbers and summed to 27 — `packaging/README.md`
was missing from `16784ca`'s row — which is the defect the rest of this work log
exists to prevent, committed inside the work log itself.

| Commit | Cites | What changed |
| :-- | --: | :-- |
| `16784ca` `docs(readme)` | 3 | The quick-start install note, the posture table's artifact-verification row and `packaging/README.md`'s OSS-11 paragraph name #80; each keeps #39 as the filing that closed on its documentation half |
| `15e065f` `docs(architecture)` | 3 | `requirements-analysis.md` §6.2 → #80. `source-normalization.md`'s `GitHub review` row and FR-V5 paragraph → a stated absence, with the four candidate issues and why none covers it |
| `9ec5e45` `docs(roadmap)` | 5 | SEC-10 rows → #429; SEC-11 rows drop #198 from owner position beside #329/#330; the T-16 paragraph quotes what the summary row reads today |
| `8c48c32` `docs(core)` | 6 | `openapi.py` ×3 and `project_config.py` → #429; `review/__init__.py` → a stated absence; `test_findings_store_is_unreachable.py` moves with it because it paraphrases that docstring's cite |
| `7e12caf` `docs(tests)` | 8 | `test_network_call_sites.py` ×3, `test_ref_recording.py` ×4, `test_mcp_tools.py` → #429 |
| `118212b` `docs(integrations)` | 1 | `ingest.md`'s FR-V5 bullet → a stated absence. Its `:33`–`:35` neighbour is #461's face and was not touched |
| `fd3829c` `docs(examples)` | 2 | The sample config's `secretScan` annotation → #329, with `ANNOTATED_KEYS` in the same commit |
| `8aa488a` `docs(architecture)` | 0 | Records that the unowned-review-ingestion note has no pin, and why |
| `9aacded` `docs(work-logs)` | 0 | This file |
| `ea77745` `docs(review)` | 0 | The four stated-unowned sites → #479, once the gap this sweep measured was filed as an issue. Adds no cite to the population: #479 is not #129, #39 or #198 |
| `1433a3d` `test(claims)` | 0 | The pin this sweep asked for, authored by the tests specialist: the roadmap's T-16 quotation held against the live threat-model row |
| `da6231f` `docs(review)` | 0 | Round-1 fixes: the #80 successor clause carried to all four sites, this table re-derived, the fifth carrier named, `project_config.py`'s schedule clause dropped |
| `1f97a59` `test(claims)` | 0 | Round-2 tests half: the correction marker scoped, and the fifth carrier repointed at #479 in the file itself |
| `docs(review)`, last | 0 | Round-2 docs half: this row's own count settled at five, the table brought level with the branch again, the docstring-only claim narrowed to the commits it is true of |

**No total is asserted for this table, deliberately.** Two rounds running, a
figure here went stale the moment the next commit landed — "Eight commits" over
nine rows in round 1, twelve rows against thirteen commits in round 2. The row
count is what `git log origin/main..HEAD` returns and is not restated in prose;
the **Cites** column is the one number that is checked, because it is derived
from the classifier and must equal 28 whatever the commit count does.

**Which commits touch Python, and how deeply**, measured per file by parsing both
revisions rather than asserted over the branch. Three registers, because one
sentence covering all of them was false of the round:

| Register | Check that holds it | Commits |
| :-- | :-- | :-- |
| Docstring-only | `ast.dump` equal with module/class/function docstrings stripped | `8c48c32` (4 files), `ea77745`, `da6231f`'s `project_config.py`, `1f97a59`'s `test_examples.py` and `test_findings_store_is_unreachable.py` |
| Literal-text-only | not docstring-only, but `ast.dump` equal with **every string constant blanked** — an assert message or a required-sentence tuple moved, no logic | `7e12caf` (3 files), `fd3829c`, `da6231f`'s `test_network_call_sites.py` |
| Structure moved | neither holds; this is real code | `1433a3d` (`test_examples.py`, plus `test_roadmap_claims.py` as a new module) and `1f97a59`'s `test_roadmap_claims.py` |

The middle register exists because the earlier version of this sentence claimed
docstring-only for the whole branch, and `7e12caf`, `fd3829c` and `da6231f` each
move an assert message or a pin's required sentence — text a reader sees only on
failure, but not a docstring. Calling that "docstring-only" was false in the same
direction each time: a check that fits the edit, rather than the edit described
by the check that was to hand. **`1433a3d` and `1f97a59` are the round's real
code changes**, and no docstring-only claim covers them.

### The ratchet, applied to this table

The rule this work log records — *a follow-up's population is the diff's added
lines, not the table the first pass wrote* — **caught this table twice, having
been written to prevent exactly that.** Round 1 found it heading nine rows with
"Eight commits"; round 2 found twelve rows against thirteen commits, `1f97a59`
missing. Both times the table was re-derived from the base rather than from what
the branch had since committed, which is the failure the ratchet names, in the
document that names it. The response is structural rather than another promise:
the row count is no longer stated in prose, so there is no number to go stale.

**Five** of the `#429` repoints also dropped a schedule clause ("owed with
Milestone 7", "owed with review ingestion") — four counted from the removed
lines of `git diff origin/main..HEAD` over `src` and `tests`, plus
`security/project_config.py`, which kept its clause until the review caught the
inconsistency and this round dropped it. #429's body records itself as "open,
unscheduled, activated by the first fetch-path design", so carrying the old
clause across would have asserted something the new owner denies. Recorded
because it is a wording change beyond the cite itself.

Its `(b)`-classified twin — `test_network_call_sites.py`'s "#129 established that
all three are owed with review ingestion in Milestone 7" — keeps its schedule,
because it reports what #129 established rather than what is scheduled now. One
clause was added beside it saying exactly that, so a developer reading the
failure is not left to infer which of the two registers they are in.

The wider **"Milestone 7"** planning anchor was left alone everywhere it was not
inside a clause being rewritten. It is its own class, filed as
[#480](https://github.com/theurian/theurian/issues/480) — `git grep -n "Milestone 7"`
returns 76 lines at `e546c15`, against a README that says "Forward planning moved
from milestone numbers to phases on 2026-08-20" — and folding it in would have
been the box-split rule broken.

**The forward anchors this branch newly *authored* are its own to fix, and are
fixed.** Three of them, not two — the count was wrong here for the same reason
the review-ingestion count was, a list written from what was salient rather than
from what the branch's diff touched:

| Line | What the branch did to it | Now reads |
| :-- | :-- | :-- |
| `source-normalization.md:170` | `15e065f` rewrote the cell, pairing "Milestone 7" with a new claim | `(roadmap Phase B)` |
| `source-normalization.md:206` | `15e065f` rewrote the parenthetical, removing `[#129]` and **leaving the bare anchor** | `(roadmap Phase B)` |
| `ingest.md:38` | `118212b`/`ea77745` rewrote the bullet, pairing "Milestone 7" with #479 | `#479, which carries `phase-b`` (label verified, not assumed) |

An earlier version of this paragraph called `:206` a "bare neighbour" that
"predates this branch". It does not: `git show 15e065f -- docs/architecture/source-normalization.md`
shows the clause being rewritten here, and a clause this branch rewrote is this
branch's, whatever survived inside it. **`ingest.md:32` genuinely does predate
it** — untouched by `git diff origin/main..HEAD` — and stays #480's. The line is
*authored here*, not *nearby*: a sentence this sweep wrote is this sweep's
defect, and one it merely stood next to is not.

### The ratchet this round bought

**A follow-up's population is the diff's added lines, not the table the first
pass wrote.** `ea77745` repointed the four sites its own pre-written candidate
table named and missed a fifth,
`tests/unit/test_findings_store_is_unreachable.py`, which had been *given* its
"owned by no open issue" wording by `8c48c32` earlier on this same branch — so
the branch shipped a coverage claim its own later commit falsified. The key was
run once, at the base, and never re-run over what the branch had since written.
So: **after a follow-up that changes a claim, re-run the population key over
`git diff <base>...HEAD`'s added lines, not only over the base.** The offline
same-number check across carriers, recorded in `source-normalization.md`, is the
mechanical form of the same guard and is #199 unit B's to build.

## Out of scope, found and not fixed

Reported to the orchestrator for box-splitting rather than folded in. The tracker
was searched for each before it was written down. Two of the four left this list
inside the branch — recorded rather than deleted, because what a sweep declined
to fold in and then folded in anyway is part of its record.

1. **The shared instrument's model output is itself a defect.** The #199 Key B
   note says: "Note `docs/roadmap.md:630` shows the CORRECT modern form (owed
   under OPEN #198/#329/#330) — the sweep's model output." #198 is CLOSED, and
   that line carried two (a)-DEAD cites, both fixed in `9ec5e45`. A measurement
   comment that hands the next sweep a wrong exemplar is worse than one that
   hands it nothing.
2. ~~**`ANNOTATED_KEYS` pins that an issue is *named*, not that it is live.**~~
   **Fixed in `1433a3d`, not deferred.** The finding stands as recorded — any
   row's issue token was satisfied by the number appearing anywhere in the
   annotation, including inside a sentence saying the issue is closed — and the
   tests specialist closed it in this branch rather than filing it: a required
   cite may now not be described as closed *within its own clause*, keyed on the
   cite and windowed to a full stop or semicolon, so the annotations keep naming
   closed issues as the history that explains the live owner. Re-measured one
   number over (#500 as owner, #329 as closed history): 16 passed before, 1
   failed / 15 passed after. The same shape #80 records for
   `test_the_step_names_the_issue_that_owns_the_gap` is still open one file over.
3. **"Milestone 7" as a planning anchor, 76 lines** — filed as
   [#480](https://github.com/theurian/theurian/issues/480). The README records that
   forward planning moved to phases on 2026-08-20 and that "the numbers had
   stopped being trustworthy"; `docs/roadmap.md` places review ingestion in
   Phase B. Documents that still say "owed with Milestone 7" point at a frame
   that is no longer the plan of record.
4. **GitHub review ingestion had no owner at all — now filed as
   [#479](https://github.com/theurian/theurian/issues/479), and closed.** Not a
   cite defect but the gap behind three of them: the feature was in the
   roadmap's Phase B and in no issue. Filed from the candidate table above,
   adopting its scoping (ingestion path #479's, fetch controls #429's); all four
   sites repointed in this branch's `docs(review)` commit. The only item on this
   list that did not survive
   the branch, and it is kept here because the measurement is why it could be
   filed at all.
