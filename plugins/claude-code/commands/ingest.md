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

- Ingestion stores **evidence**, not approved knowledge. A review comment that
  has been ingested is a record of what someone said; it is not a team rule.
- Ingestion never creates approved knowledge. Promotion requires
  `/theurian:propose` followed by human review and a merged pull request.
- If candidate generation fails (for example, no summarization provider is
  configured), raw ingestion still succeeds. Report the partial result rather
  than treating the whole run as failed.
- Review history from GitHub is **not ingested yet**: `system.capabilities`
  reports `reviewIngestion: false`, and `theurian ingest` reads local files under
  `.theurian/` only. When it lands (Milestone 7) a repository will have to be on
  the allowlist in `.theurian/config.yaml` before Theurian contacts it; nothing
  reads that file today, so do not tell the user the allowlist is protecting
  them.
