---
description: Ingest local sources — docs and specs — into Theurian.
allowed-tools: Bash(theurian:*)
---

# /theurian:ingest

Read source material into the canonical store as evidence.

## What to do

```sh
theurian ingest --json
```

Report what was ingested by source type and how many documents changed.

## Rules

- Ingestion stores **evidence**, not approved knowledge. An ingested design note
  is a record of what a document says; it is not a team rule.
- Ingestion never creates approved knowledge. Promotion requires
  `/theurian:propose` followed by human review and a merged pull request.
- Review history from GitHub is **not ingested yet**: `system.capabilities`
  reports `reviewIngestion: false`, and `theurian ingest` reads only local data:
  files under `.theurian/`, plus three `git` reads — the repository root
  (`rev-parse --show-toplevel`), HEAD (`rev-parse HEAD`), and the `origin` URL
  (`remote get-url origin`). When it lands (Milestone 7) a repository will have
  to be on the allowlist in `.theurian/config.yaml` before Theurian contacts it;
  nothing reads that file today, so do not tell the user the allowlist is
  protecting them.
- `theurian ingest` generates no candidates and runs no summarization stage, so
  there is no partial result to report. When review ingestion lands
  (Milestone 7, [#129](https://github.com/theurian/theurian/issues/129)), a
  failure in candidate generation must not fail raw ingestion (FR-V5).
