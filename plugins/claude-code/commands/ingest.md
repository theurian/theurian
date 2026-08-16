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
- If candidate generation fails (for example, no summarization provider is
  configured), raw ingestion still succeeds. Report the partial result rather
  than treating the whole run as failed.
- Review history from GitHub is **not ingested yet**: `system.capabilities`
  reports `reviewIngestion: false`, and `theurian ingest` reads only local data:
  files under `.theurian/`, plus `git` for the repository root, HEAD, the branch
  name, and the `origin` URL out of Git config. When it lands (Milestone 7) a
  repository will have to be on
  the allowlist in `.theurian/config.yaml` before Theurian contacts it; nothing
  reads that file today, so do not tell the user the allowlist is protecting
  them.
