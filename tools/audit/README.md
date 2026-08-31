# Threat-model audit keys (#199 unit A, and the sweeps that followed it)

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
`5a9a1e5` to 119/57/114/109 at `5c5bad7`, in the same branch. Read a row against
the commit it names, or re-run it and record the new anchor beside the new
number.

Run them against a specific commit by extracting the file first, so the counts
are anchored:

```sh
mkdir -p /tmp/tm && git show 06de58a:docs/security/threat-model.md > /tmp/tm/threat-model.md
uv run --frozen python tools/audit/threat_model_1e.py /tmp/tm/threat-model.md
```
