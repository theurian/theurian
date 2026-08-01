# Governance

This document states how Theurian is governed, who decides what, and what the
project promises to stay. It exists before external contributions start, because
governance written after a disagreement is written to win it.

## Project status

Theurian is in early development. Governance is currently **maintainer-led**: a
small group makes decisions in the open, in this repository. As the contributor
base grows, this document will be revised — by pull request, like everything else.

## Roles

| Role | How you get it | What you can do |
| :-- | :-- | :-- |
| **Contributor** | Open a pull request | Propose changes, file issues, review |
| **Reviewer** | Sustained, high-quality review from a maintainer's invitation | Approve pull requests in a named area |
| **Maintainer** | Nominated by an existing maintainer, no objection from the others | Merge, release, own an area in CODEOWNERS |

Maintainers are listed in [`.github/CODEOWNERS`](.github/CODEOWNERS), split by
area: Core and the Claude Code plugin have distinct owner groups, and `schemas/`
requires both — the shared contract cannot be changed unilaterally by either
side.

A maintainer who has been inactive for six months moves to emeritus. This is
administrative, not a judgement, and is reversible on request.

## How decisions are made

**Lazy consensus.** Most changes merge after one maintainer approval from the
relevant CODEOWNERS group and green CI. Silence is agreement.

**Architecture decisions** — anything expensive to reverse — require an ADR in
`docs/adr/`. An ADR opens as `proposed` and needs approval from a majority of
Core maintainers to become `accepted`. Decisions are changed by superseding an
ADR, never by editing an accepted one: the history of a decision is what makes
the decision legible later.

**Blocking objections** must be technical and must include what would resolve
them. "I don't like it" is not a block; "this breaks the reproducibility
guarantee in ADR-0007, and here is the case" is.

**Deadlock** goes to a maintainer vote. Simple majority. Ties fail — the status
quo wins, because a tie means the case has not been made.

## Releases

Core and the Claude Code plugin release independently, with independent versions
and changelogs ([ADR-0001](docs/adr/0001-monorepo-with-independent-artifacts.md)).
Either group of maintainers can cut a release of their own artifact. Neither can
force a release of the other's.

Semantic Versioning. Pre-1.0, a MINOR bump may break the protocol; post-1.0 only a
MAJOR may. See [docs/contributing/release.md](docs/contributing/release.md).

## Licensing and the promise this project makes

Theurian Core is Apache-2.0, and contributions are made under the
[DCO](docs/contributing/dco.md). **There is no CLA and no copyright assignment.**

That is a deliberate constraint on the maintainers, not an oversight. Because
every contributor retains copyright, relicensing Core away from Apache-2.0 would
require every contributor's agreement. Adopters get a structural guarantee rather
than a stated intention.

## The OSS/commercial boundary

A hosted offering may exist in the future. The boundary is written down now, so
it is a commitment rather than a later assertion:

**Theurian Core will always:**

- work completely offline, with no account, no network call, and no API key;
- include the migration engine, canonical store, ingestion, retrieval, RAPTOR,
  review ingestion, and traceability;
- be Apache-2.0;
- have no dependency that requires a hosted service.

**A hosted service may add** multi-tenancy, team management, SSO, centralized
knowledge stores, managed indexing and embeddings, cross-organization search, and
enterprise policy — as components *around* Core, built on the same Apache-2.0
Core anyone else uses.

**No feature will be removed from Core in order to sell it back.** If a
capability works locally today, it keeps working locally.

## Changing this document

By pull request, approved by a majority of maintainers. The licensing and
OSS/commercial sections above are commitments to the community; changing them
requires an ADR that explains what changed and why, and it will be visible in the
history forever.

## Contact

- Technical discussion: GitHub issues and pull requests
- Security: the private path in [SECURITY.md](SECURITY.md)
- Conduct: the reporting path in [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md)
