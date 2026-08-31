# Threat-model audit keys (#199 unit A)

The population keys the #199 unit-A audit ran, committed so that every count in
[the work log](../../docs/work-logs/2026-08-30-199-unit-a-audit.md) is
re-runnable rather than merely reported.

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

Run them against a specific commit by extracting the file first, so the counts
are anchored:

```sh
mkdir -p /tmp/tm && git show 06de58a:docs/security/threat-model.md > /tmp/tm/threat-model.md
uv run --frozen python tools/audit/threat_model_1e.py /tmp/tm/threat-model.md
```
