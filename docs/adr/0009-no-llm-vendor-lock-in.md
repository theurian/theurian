# ADR-0009: No vendor lock-in for LLM, embedding, or cloud providers

- Status: accepted
- Date: 2026-08-01
- Deciders: Theurian maintainers
- Requirements: OSS-13, OSS-15, NFR-10, R-13, §26 of the brief

## Context

Theurian would benefit from embeddings, abstractive summarization, and cross-encoder
reranking. The path of least resistance is to depend on one hosted API. For an
open-source project that is disqualifying:

- A contributor cannot run the test suite without a paid key, so nobody outside
  the funded core runs the tests (OSS-13).
- CI cannot run deterministically — model outputs drift, and test failures become
  unattributable.
- Enterprises with data-residency constraints cannot adopt the tool at all.
- The project's fate becomes tied to one vendor's pricing and deprecation schedule.

The opposite extreme — refusing to use models — gives up most of the retrieval
quality that motivates RAPTOR.

## Decision

**Every model-dependent capability sits behind a port, and every port ships an
in-tree deterministic default that requires no network.**

| Port | In-tree default | Quality without configuration |
| :-- | :-- | :-- |
| `EmbeddingProvider` | deterministic hashed bag-of-tokens vector | weak semantically, but usable and reproducible; lexical FTS carries retrieval |
| `SummarizationProvider` | extractive (lead + salient sentence selection) | grounded by construction; never hallucinates because it never generates |
| `RerankingProvider` | identity (preserves fusion order) | no gain, no loss |

> **Amended in Milestone 5.** "Usable" was too generous, and measuring it said
> so. The shipped `EmbeddingProvider` default is `theurian-hashed-char-ngram`, a
> hashed character trigram vectoriser, and against a real corpus **91% of
> unrelated natural-language questions cleared its similarity floor** while the
> lowest genuinely related query fell below the unrelated median. As a retriever
> it is not weak, it is uninformative — the distributions overlap, so no
> threshold separates them.
>
> The decision is unchanged: the port stays, the in-tree default stays, and no
> API key is required. What changed is that the dense retriever is now **opt-in**
> (`useDense`, default `false` — see the amendment to
> [ADR-0021](0021-rank-fusion-over-score-normalisation.md)). The row above should
> be read as: *usable as a deterministic, offline stand-in that keeps the code
> path exercised*, not as *usable as retrieval*.
>
> This is the "Negative" consequence below arriving with a number attached, and
> it strengthens rather than weakens rule 1: what makes Theurian functional
> offline is lexical retrieval — now two tokenizers of it (ADR-0023) — not the
> default embedder.

Rules:

1. Core's default configuration calls no external service and requires no API key.
   `theurian` installed from PyPI on an air-gapped machine is fully functional for
   ingestion, migration, traceability, and hybrid FTS retrieval.
2. Real providers are opt-in adapters configured in `.theurian/config.yaml`.
   Adding one never requires changing application or domain code.
3. **No test in this repository calls an external model API.** Adapters for hosted
   providers are tested against recorded fixtures and a Protocol-conformance suite
   that the fakes also pass, so fakes cannot silently diverge from real adapters
   (R-13).
4. Model identity is data, not configuration-only: `summary_model`,
   `summary_model_revision`, `summary_prompt_hash`, `embedding_model`,
   `embedding_model_revision`, and `embedding_dimension` are persisted per node
   (ADR-0008). Changing a provider invalidates the affected derived artifacts
   deterministically instead of producing a silently mixed index.
5. Vendor names never appear in domain or application code. Adapter modules may
   be named for their vendor; nothing else may.
6. The same rule applies to infrastructure: the OSS Core must never require a
   hosted database, queue, object store, or auth provider.

## Consequences

### Positive

- `git clone && uv sync && pytest` passes on any machine, offline, for free.
- CI is deterministic, so a red build means a real regression.
- Adopters choose their own provider — including a self-hosted one — and their
  data-residency posture is theirs to set.
- Degradation is graceful and legible: without an embedding provider, retrieval is
  lexical-plus-structural, not broken.

### Negative

- Out-of-the-box semantic search quality is materially below what a real embedding
  model gives. Documented plainly in the README so nobody benchmarks the fake and
  concludes the design is bad.
- Maintaining the Protocol-conformance suite across fakes and real adapters is
  ongoing work.

### Neutral

- A future Theurian Cloud can ship managed providers as adapters, with no change
  to Core.

## Alternatives considered

| Alternative | Why rejected |
| :-- | :-- |
| Depend on one hosted embedding API | Breaks OSS-13 and NFR-10; makes CI non-deterministic. |
| Bundle a local ONNX embedding model | Adds hundreds of megabytes and a heavyweight runtime to a knowledge CLI. Available as an opt-in adapter, not a default. |
| Require a local model server (Ollama) by default | Another mandatory process for a tool whose core value does not need one. |
| Skip embeddings entirely | Discards semantic retrieval and most of RAPTOR's value. |

## Compliance

- A CI job runs the full suite with network access blocked.
- A test asserts the default configuration instantiates no adapter that opens a socket.
- A shared Protocol-conformance suite runs against both the fake and (in an
  opt-in credentialed job) each real adapter.
- A grep-based CI check fails if a vendor name appears under `domain/` or
  `application/`.

Landed in Milestone 5, for the `EmbeddingProvider` default specifically:

- `tests/unit/test_hashing_embedding.py::test_it_does_not_claim_to_be_semantic`
  — the test that keeps the amendment above honest in code rather than only in
  prose.
- `test_it_is_deterministic_across_processes` — rule 4 in practice. `hash()`
  randomisation would give a rebuilt index vectors that no longer match the ones
  a pinned result was ranked against, and the failure would look like a
  relevance drift rather than a bug.
- `test_it_satisfies_the_port` — the default and any future adapter answer the
  same `EmbeddingProvider` Protocol, which is what makes swapping one a
  configuration change rather than a code change.
