---
description: Ingest sources — docs, specs, and Git review history — into Theurian.
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
- If the user wants review history from GitHub, the repository must be on the
  allowlist in `.theurian/config.yaml`. Theurian will not contact a repository
  that is not listed.
