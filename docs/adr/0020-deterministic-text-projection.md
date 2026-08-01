# ADR-0020: Text projection of structured sources is a deterministic pure function

- Status: accepted
- Date: 2026-08-02
- Deciders: Theurian maintainers
- Requirements: FR-S2, FR-R2, NFR-5, ADR-0010

## Context

A structured source keeps its structure in the Canonical Layer *and* gains a
text rendering, so lexical search can match it (ADR-0010). A specification that
declares `code: CANCELLATION_NOT_ALLOWED` must be findable by searching for that
string, and FTS5 indexes text, not trees.

That rendering is the **text projection**. It is stored, indexed, chunked, and
embedded. Which makes its stability a correctness property rather than a
formatting preference.

If the projection varies for identical input:

- the same document produces different chunks on different machines, so an index
  built on one is not the index another rebuilds;
- an embedding cache keyed on projected text misses constantly;
- `theurian doctor` cannot tell a genuine content change from a rendering
  change, so "is my index current?" becomes unanswerable.

The sources of instability are all ordinary Python behaviour, which is what
makes this worth writing down:

| Source | Failure |
| :-- | :-- |
| Iterating a `set` | Order varies with `PYTHONHASHSEED` across processes |
| `json.dumps(..., sort_keys=False)` on a re-parsed dict | Depends on parser insertion order |
| `str(float)` | `1.0` vs `1`, and repr changes have happened across Python versions |
| Locale-sensitive formatting | Thousands separators, decimal commas |
| Datetime rendering | Timezone offset formats differ per library |

## Decision

**The projection is a pure function of the parsed document, with no dependence
on process state, environment, locale, or Python version details.**

Rules:

1. **Preserve document order. Do not sort.** Mappings render in the order the
   parser produced, which is the order they appear in the source. Sorting would
   be stable but would destroy information — in an OpenAPI document, the order
   of parameters is meaningful, and in a specification the order of rules is how
   an author expressed precedence.
2. **Never iterate an unordered collection.** No `set`, and no `dict` that was
   not built from an ordered parse.
3. **Render scalars explicitly**, not with `str()`:
   - `bool` → `true` / `false` (not `True` / `False`)
   - `None` → `null`
   - `int` → decimal digits
   - `float` → `repr`, which round-trips exactly in Python 3.1+
   - `str` → as-is
4. **Emit key paths**, so a value keeps the context that gives it meaning:

   ```text
   outcomes.failure.code: CANCELLATION_NOT_ALLOWED
   ```

   Searching for `CANCELLATION_NOT_ALLOWED` finds it; so does searching for
   `outcomes failure`. A bare value dump would lose the second.
5. **Bound the output.** Depth and total size are capped, and truncation is
   marked in the text rather than silent. An unbounded projection of a deeply
   nested document is a memory-exhaustion vector (SEC-8).
6. **The projection is never the record of truth.** `structured` holds the real
   data. The projection exists so lexical search can reach it, and it can be
   regenerated at any time.

## Consequences

### Positive

- The same source produces the same projection on every machine and every run,
  so a rebuilt index equals the original.
- A change in projected text means the source changed. That makes staleness
  detection exact rather than heuristic.
- Key paths make structured content searchable both by value and by location,
  which is what a specification query actually needs.

### Negative

- The projection is more verbose than the source, so a large OpenAPI document
  projects to a lot of text. Bounded by rule 5, and lexical search over verbose
  text is cheap.
- Preserving document order means two semantically identical documents with
  different key order project differently, and therefore hash differently. This
  is correct: they are different files, and Theurian does not claim to normalise
  away authorial choices.

### Neutral

- The same rules will govern any future structured format. A parser that cannot
  produce a deterministic projection has not finished.

## Alternatives considered

| Alternative | Why rejected |
| :-- | :-- |
| Sort keys for determinism | Stable, but destroys meaningful order — parameter order in OpenAPI, rule precedence in a spec. Determinism is already achievable without it. |
| Project only leaf values, no key paths | Loses the context that makes a value findable. Searching "failure code" would match nothing. |
| Use `json.dumps` directly | Carries Python's float and boolean spellings into the index, and its behaviour has shifted across versions. |
| Skip the projection; index `structured` directly | FTS5 indexes text. Something has to produce it; leaving it implicit means every caller invents its own. |
| Let each parser render however it likes | Guarantees divergence between formats, and makes the determinism property untestable in general. |

## Compliance

- A test asserts the projection is byte-identical across separate interpreters
  under differing `PYTHONHASHSEED` values.
- A test asserts booleans, `None`, and floats render in the documented spelling
  rather than Python's.
- A test asserts key order in the source is preserved in the projection.
- A test asserts a document exceeding the depth or size cap is truncated with a
  visible marker rather than silently.
