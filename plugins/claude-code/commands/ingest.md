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
  (Milestone 7 — no open issue owns it, and
  [#129](https://github.com/theurian/theurian/issues/129), which this line used
  to name, closed on the wording rather than on the adapter), a failure in
  candidate generation must not fail raw ingestion (FR-V5).
