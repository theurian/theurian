---
description: Ingest local sources — docs and specs — into Theurian.
allowed-tools: Bash(theurian:*)
---

# /theurian:ingest

Parse and normalize local source material. Nothing is written to the canonical
store.

## What to do

```sh
theurian ingest --json
```

Report what was ingested by source type and how many documents changed.

## Rules

- Ingestion **stores no content**. It parses, reports what it read, and writes
  one file: the content-hash manifest `.theurian/cache/ingestion.json`,
  which exists so an unchanged file is not reparsed. Parsed documents live in
  memory for the run. Nothing reaches the canonical store, so an ingested design
  note is neither a team rule nor knowledge an agent can retrieve afterwards.
- Ingestion never creates approved knowledge. Promotion requires
  `/theurian:propose` followed by human review and a merged pull request.
- Review history from GitHub is **not ingested by this command**:
  `theurian ingest`
  reads only local data: files under `.theurian/`, plus three `git` reads — the
  repository root (`rev-parse --show-toplevel`), HEAD (`rev-parse HEAD`), and
  the `origin` URL (`remote get-url origin`). The allowlist in
  `.theurian/config.yaml` is read and enforced (SEC-10, ADR-0030 decision 2):
  `security/review_allowlist.py` refuses a repository
  `providers.review.repositories` does not name, before any process is spawned.
  It protects a different command — `theurian review ingest`, which does fetch
  review history and lands it as durable files under `.theurian/review/` — so do
  not tell the user that listing a repository has turned anything on here.
  `system.capabilities` reports `reviewIngestion: false`, and that flag is a
  statement about **MCP tools**: no tool exposes review ingestion, which is why
  it is a separate CLI verb the operator runs, and ADR-0030's serve slice is what
  moves the flag. That file is
  read for three keys:
  `security/project_config.py` takes `security.secretScan`,
  `providers.review.repositories` and `providers.review.redactParticipantNames`
  from it and nothing else (ADR-0027 decision 3, ADR-0030 decisions 2 and 3).
  The third is R-12's ingestion-time redaction switch and belongs to that same
  other command: setting it redacts nothing `theurian ingest` writes. The first
  selects a control this command never reaches:
  it covers the approval gate, the index build and review ingestion —
  `theurian ingest` runs no scan of its own, and
  `theurian index build` scans every body it indexes, with the source anchors
  and relation notes served beside them, and reports rather than refusing
  (SEC-11,
  [#198](https://github.com/theurian/theurian/issues/198) shipped the
  approval-gate half and
  [#329](https://github.com/theurian/theurian/issues/329) the index-build half),
  the schema's own wording.
- `theurian ingest` generates no candidates and runs no summarization stage, so
  there is no partial result to report. When review ingestion lands
  (owned by [#479](https://github.com/theurian/theurian/issues/479), which
  carries `phase-b` — this line named
  [#129](https://github.com/theurian/theurian/issues/129) until it closed on the
  wording rather than on the adapter), a failure in candidate generation must not
  fail raw ingestion (FR-V5).
