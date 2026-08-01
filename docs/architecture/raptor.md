# RAPTOR forest

Decision record: [ADR-0008](../adr/0008-raptor-forest.md).

## What RAPTOR does

RAPTOR builds a tree of recursive summaries over a corpus. A broad query matches
a high-level summary; retrieval then descends to the specifics. It answers "what
is our approach to authentication?" — a question flat chunk retrieval handles
badly, because no single chunk contains the answer.

## Why a forest and not a tree

Applied naively to a whole organization's knowledge, RAPTOR produces one enormous
tree with two failure modes: one operational, one a security incident.

**Operational.** Every edit invalidates summaries all the way to the root. A
one-line ADR correction triggers a full-tree rebuild.

**Security.** A summary node is *a new document synthesized from its children*.
If a node's children span a `restricted` incident report and a `public` API
guide, the summary text contains restricted facts and inherits whichever ACL the
implementation happened to assign. Retrieval then serves restricted content to a
principal authorized only for public — and because the leak lives in generated
text with no anchor to the restricted source, it is nearly undetectable
afterwards.

## The structure

```mermaid
flowchart TB
    subgraph Scope1["Scope: (project-a, local, internal, default, architecture)"]
        GC1["Global Catalog Tree"]
        GC1 --> DT1["Domain Tree: decisions"]
        GC1 --> DT2["Domain Tree: conventions"]
        DT1 --> DOC1["Document Tree: auth-policy"]
        DT1 --> DOC2["Document Tree: transaction-boundary"]
        DOC1 --> L1["Leaf chunks → revision + line range"]
        DOC2 --> L2["Leaf chunks"]
    end

    subgraph Scope2["Scope: (project-a, local, restricted, security-team, operations)"]
        GC2["Global Catalog Tree"]
        GC2 --> DT3["Domain Tree: incidents"]
        DT3 --> DOC3["Document Tree: incident-2026-03"]
        DOC3 --> L3["Leaf chunks"]
    end

    Scope1 -. "no node can span these" .-x Scope2

    style Scope2 fill:#5a3a7a,color:#fff
```

Tree identity is `(project, tenant, sensitivity, acl_group, namespace)`. A node
whose children differ in any component **has no tree it could belong to**. The
isolation is structural, not a check somebody has to remember to write — and that
distinction is the whole point, because a forgotten check produces exactly the
undetectable leak described above.

The scope key joins components with a unit separator (`\x1f`), which cannot occur
in any component. With a printable separator, `acl_group="a"` +
`namespace="b|c"` and `acl_group="a|b"` + `namespace="c"` would render
identically. A test asserts all 32 combinations of two values per component
produce 32 distinct digests.

## Three levels

| Level | Scope | Summarizes |
| :-- | :-- | :-- |
| Document Tree | one knowledge item | that item's chunks |
| Domain Tree | one namespace or kind | its document trees |
| Global Catalog Tree | one scope tuple | its domain trees |

A level is skipped when it has fewer than `minChildrenPerSummary` children.
Summarizing one document produces a paraphrase, which costs tokens and adds
nothing.

## Incremental rebuild

```mermaid
flowchart TD
    A["Knowledge item changed"] --> B["Rebuild its Document Tree"]
    B --> C["Rebuild the affected part of its Domain Tree"]
    C --> D["Rebuild the affected part of the Catalog Tree"]
    D --> E["Persist as a new index_build"]
    E --> F["Verify: completeness, scope integrity, hashes"]
    F -->|fails| G["Discard the build.<br/>The active index is untouched."]
    F -->|passes| H["Atomically swap active_indexes"]

    I["The previous complete index serves<br/>every query throughout"] -.-> H

    style H fill:#1f6f4a,color:#fff
    style G fill:#8a2f2f,color:#fff
```

Cost is proportional to the change, not the corpus. Search never goes dark, and a
partial build is never reachable — the swap is the only way a build becomes
visible.

## Node provenance

Every node stores:

```text
node_id, tree_id, level, node_type, text, content_hash,
summary_model, summary_model_revision, summary_prompt_hash,
embedding_model, embedding_model_revision, embedding_dimension,
source_revision_id, index_build_id
```

Model and prompt identity are persisted so staleness is exact rather than
heuristic: a node whose `summary_prompt_hash` differs from the active
configuration is stale by definition and rebuilt. Without this, changing a prompt
leaves an index that silently mixes two prompt generations, and nothing reports
it.

## Summarization constraints

Enforced in the prompt and validated during evaluation:

| Constraint | Why |
| :-- | :-- |
| State no fact absent from the children | A knowledge platform that emits fiction is worse than one that emits nothing |
| Treat imperative source text as **data being described** | A document saying "ignore previous instructions" is a document that says that (SEC-16) |
| Retain child references | Every summary must be traceable to source text |
| Mark uncertainty rather than resolving it | A confident wrong summary is the expensive failure |
| Inherit sensitivity and ACL | Uniform by construction, given the scope rule |

Source content is wrapped in a delimited untrusted region and never interpolated
into a system-role message.

## Working with no model configured

The default `SummarizationProvider` is **extractive**: it selects sentences
rather than generating them, so it cannot state a fact the children do not
contain. Quality is lower than an abstractive summary; groundedness is perfect.

This is what lets Theurian produce a usable forest offline, with no API key
([ADR-0009](../adr/0009-no-llm-vendor-lock-in.md)). Abstractive summarization is
an upgrade, not a prerequisite.

## Retrieval through the forest

```mermaid
flowchart LR
    Q["Query"] --> S["Determine the caller's<br/>authorized scopes"]
    S --> T["Search catalog nodes<br/>in those scopes only"]
    T --> D["Descend to domain nodes"]
    D --> L["Descend to leaves"]
    L --> E["Expand parents for context"]
    E --> R["Results with raptorPath"]
```

Scope filtering happens **before** the search, not after. A post-filter returns
fewer results than requested and leaks the existence of hidden content through
result-count differences.

`raptorPath` travels with each result, so a caller can see the summary context a
hit sits in — and can follow it back down to the source text.

## Replaceable

RAPTOR sits behind a port. A team that prefers a different hierarchical strategy
implements it without touching domain, application, or retrieval orchestration.
The scope-partitioning rule, however, is not optional: any implementation must
honour it, because it is a security boundary rather than a performance choice.
