# ADR-0019: Front matter is data, not governance metadata

- Status: accepted
- Date: 2026-08-02
- Deciders: Theurian maintainers
- Requirements: FR-S2, ADR-0005, ADR-0010, §13.3 of the brief

## Context

§13.3 and [ADR-0010](0010-three-layer-knowledge-model.md) establish a split:
Markdown holds the knowledge body, and the migration YAML holds status,
revision, ownership, sensitivity, and validity. The same metadata is never
duplicated in both, because one copy would go stale and there would be no rule
for which wins.

Milestone 2 makes that abstract rule concrete, because Markdown files routinely
arrive with YAML front matter:

```markdown
---
title: Authentication policy
status: approved
owner: platform-team
---

# Authentication policy
```

Every static site generator, every docs tool, and most existing ADR templates
produce it. A team adopting Theurian will point it at a directory full of files
shaped exactly like the one above. So the parser must have an answer, and
"undefined" is not one.

Three answers are possible, and the choice matters:

1. **Read it as governance metadata.** Front matter now sets `status: approved`.
   But so does the migration. When they disagree, which is true? And an author
   can now approve their own knowledge by editing a file, with no migration and
   no review — which defeats [ADR-0013](0013-ai-writes-produce-proposals.md).
2. **Reject files that have it.** Correct in principle, hostile in practice.
   It makes adoption mean rewriting every existing document first.
3. **Keep it as data.** Preserve it, index it, never let it govern anything.

## Decision

**Front matter is parsed, preserved as structured data, and never interpreted
as governance metadata.**

1. YAML front matter delimited by `---` at the start of a Markdown file is
   parsed with the safe loader and stored in `NormalizedDocument.structured`
   under a `frontMatter` key.
2. It is searchable. A team that records `reviewers:` or `jira:` in front matter
   can retrieve on it.
3. **It never sets** `status`, `trustLevel`, `sensitivity`, `owner`,
   `validFrom`, `validTo`, or any other governed field. Those come from the
   migration and only from the migration.
4. The body used for content hashing and for the text projection is the content
   **after** the front matter block. Front matter is metadata about the
   document, not part of it.
5. When front matter contains a key that Theurian governs, the parser records a
   **warning** naming the key. It does not fail, and it does not apply the
   value. The warning exists because a silently ignored `status: approved` is
   exactly the situation where an author believes something is approved and it
   is not.
6. `title` is the one field read from front matter, and only as a *fallback*
   when the migration does not supply one and the document has no `# heading`.
   A title is a display concern, not a governance one.

## Consequences

### Positive

- A team can point Theurian at an existing docs tree without rewriting it.
- Approval remains impossible outside a migration, so ADR-0013 holds through
  the ingestion path as well as the MCP path.
- Front matter a team already maintains becomes searchable rather than discarded.
- The warning turns a silent no-op into a visible one, at the moment the author
  is looking at the output.

### Negative

- A newcomer may reasonably expect `status: approved` in front matter to work.
  The warning is the mitigation, and it names the specific key rather than
  saying something generic.
- Two places now describe a document — front matter and the migration — even
  though only one governs. Accepted: the alternative is rejecting files that
  every neighbouring tool produces.

### Neutral

- The same rule will apply to any future format with an embedded metadata block
  (DOCX properties, PDF XMP). Interpreted as data, never as governance.

## Alternatives considered

| Alternative | Why rejected |
| :-- | :-- |
| Read front matter as governance metadata | Two sources of truth for `status`, with no rule for which wins, and an author could approve knowledge by editing a file. Defeats ADR-0013 through the back door. |
| Reject files containing front matter | Makes adoption require rewriting an existing docs tree first. Correct in principle, hostile in practice. |
| Strip and discard it silently | Throws away data a team maintains deliberately, and gives no signal when a governed key was ignored. |
| Merge front matter and migration, migration wins | Sounds reasonable and is the worst option: the file says one thing, the store says another, and nothing reports the divergence. |

## Compliance

- A test asserts a front-matter `status: approved` does **not** reach the stored
  revision's status.
- A test asserts a governed key in front matter produces a warning naming that
  key.
- A test asserts the content hash covers the body only, so adding front matter
  to a file does not change the body's hash.
- A test asserts non-governed front matter keys survive into `structured`.
