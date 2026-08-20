<div align="center" markdown="1">

<img src="assets/theurian-logo.svg" alt="Theurian" width="420">

# The engineering record your AI agents consult

### Stop your AI from re-proposing what your team rejected in March.

Decisions, rejected alternatives, constraints — and the evidence behind them —
under human governance, reachable by any MCP client.
**Agents read. Agents propose. Agents never approve.**

**[Understand the architecture](architecture/overview.md)** ·
**[Connect Claude Code](integrations/claude-code.md)** ·
**[Explore the MCP tools](protocol/mcp-tools.md)** ·
**[Read the roadmap](roadmap.md)**

</div>

---

## Your codebase remembers less than your team does

The answer to an engineering question often already exists.

It may be buried in an ADR from months ago, a specification nobody has opened
recently, or a review where the team explained why an apparently obvious
approach was rejected.

An AI agent looking only at source code or semantically similar text can miss
that context.

The result is familiar:

- decisions get rediscovered instead of reused
- rejected approaches get proposed again
- outdated guidance looks as authoritative as current guidance
- generated answers lose the evidence needed to verify them

**Theurian gives agents access to what your team decided — not just what text
looks similar.**

---

## From documents to governed engineering knowledge

Theurian provides a local knowledge layer between your engineering artifacts
and AI agents.

```text
ADRs · Specifications · Reviews · Engineering knowledge
                         │
                         ▼
                      Theurian
                         │
             ┌───────────┼───────────┐
             ▼           ▼           ▼
          Status      Provenance   Freshness
             │           │           │
             └───────────┼───────────┘
                         ▼
                    AI agents
```

A search result can carry more than an excerpt. It can tell the caller:

- whether the knowledge is approved
- what level of trust it has
- whether it is still within its validity window
- which source produced it
- which repository, commit, file, and line range support it when available
- which knowledge state answered the query

That makes an answer inspectable instead of merely plausible.

---

## AI proposes. Humans approve.

Theurian keeps an explicit boundary between AI output and approved engineering
knowledge.

**No MCP write tool can directly create approved knowledge.** The five tools a
client can call today are read-only, and `system.capabilities` reports
`writeTools: false`.

This is intentional.

AI agents can consume governed knowledge without becoming the authority that
governs it. Decisions remain reviewable engineering artifacts controlled by
the team.

> Memory is what your agent remembers.  
> Governance is what your team decided.

Theurian is built for the second one.

[Read the architectural decisions →](adr/README.md)

---

## The evidence plane, not a control plane

Theurian is the record that agents, CI, and people all *consult*. It is
deliberately not a place where things get decided or enforced.

> **Theurian does not orchestrate, does not approve, does not enforce.**

Approval is the act of merging a pull request; enforcement is what CI and branch
protection already do. Adding a third control point would put Theurian in
competition with both, and would contradict the safety rule the rest of the
design rests on: *Theurian labels; it does not enforce. Acting on the label is
the calling agent's responsibility.*

CI reading Theurian and blocking a pull request on what it finds is a welcome
arrangement — and the thing that blocked is CI.

The reasoning, and the alternative positioning that was rejected, are in
[the roadmap](roadmap.md).

---

## Why not just put your docs in a vector store?

Semantic similarity is useful, but similarity alone does not answer some of the
most important engineering questions.

| Question | Document / vector search | Theurian |
| --- | --- | --- |
| What text looks relevant? | ✓ | ✓ |
| What did the team decide? | Maybe | **Explicitly represented** |
| Was this reviewed? | Usually unknown | **Trust and status travel with it** |
| Is it still valid? | Usually unknown | **Freshness is queryable** |
| Where did this claim come from? | Often a document link | **Source provenance** |
| Can an AI silently promote its own output to approved knowledge? | Depends on the system | **No** |

The goal is not to replace search.

It is to make engineering knowledge **governable, traceable, and safe to use as
AI context**.

---

## Built for engineering systems, not a chatbot memory layer

### Governed knowledge

Knowledge can carry status, trust level, sensitivity, provenance, and a
validity window.

### Evidence-backed retrieval

Results can point back to the source material that justifies them, including
commit and line-level anchors when the source provides them.

### Reproducible state

Knowledge revisions are immutable, and searches identify the state that
answered them so engineering context can be compared over time.

### Local-first architecture

Theurian runs through a local daemon and exposes engineering knowledge to
agents through MCP.

---

## Start with the part you need

### I want to understand the system

Start with the [architecture overview](architecture/overview.md), then explore
the [Architecture Decision Records](adr/README.md).

### I want to connect an AI coding agent

See the [Claude Code integration](integrations/claude-code.md) and the
[MCP tool protocol](protocol/mcp-tools.md).

### I want to understand how knowledge changes

Read about [migrations](protocol/migrations.md) and the
[knowledge formats](architecture/knowledge-formats.md).

### I need to evaluate the security model

Start with the [threat model](security/threat-model.md).

### I want to contribute

See the [development guide](contributing/development.md).

---

## The direction

Theurian is building toward a traceable engineering knowledge graph connecting:

```text
requirement
    ↓
specification
    ↓
architecture decision
    ↓
pull request
    ↓
review
    ↓
code
    ↓
test
    ↓
evidence
```

The goal is for an AI agent to answer not only:

> “What should I do?”

but also:

> “Why did the team decide this, what evidence supports it, and is that
> decision still valid?”

**That graph is not built.** `system.capabilities` reports
`traceability: false`, there is no `knowledge.trace` or `knowledge.impact` tool,
and the `traceability_edges` table ships empty. Collecting the graph and
querying it is Phase C of the adopted roadmap; impact analysis and drift
detection are Phase E.

Nothing on this page describes a capability that does not exist today. Where
this documentation looks forward, it says so, and `system.capabilities` is the
authority a client should ask.

[Read the roadmap →](roadmap.md) ·
[Explore the traceability design →](architecture/traceability.md)

---

## Explore the documentation

| Area | What you'll find |
| --- | --- |
| [Roadmap](roadmap.md) | The adopted plan, phase by phase, and what each phase does not claim |
| [Architecture](architecture/overview.md) | System design, knowledge model, local daemon, retrieval, and the traceability design |
| [ADRs](adr/README.md) | The decisions that shape Theurian itself |
| [Protocol](protocol/mcp-tools.md) | MCP tools, migrations, and compatibility contracts |
| [Integrations](integrations/claude-code.md) | Connecting Theurian to AI development tools |
| [Security](security/threat-model.md) | Threat model and security boundaries |
| [Contributing](contributing/development.md) | Development and contribution guidance |

---

<div align="center" markdown="1">

### Give your AI the decisions behind the code.

**[Start with the architecture →](architecture/overview.md)**

</div>
