# ADR-0015: Developer Certificate of Origin, not a Contributor License Agreement

- Status: accepted
- Date: 2026-08-01
- Deciders: Theurian maintainers
- Requirements: OSS-5, O-6, O-7

## Context

§26 of the brief requires that the DCO-versus-CLA decision be recorded in an ADR.
The choice shapes who contributes and what the project may later become.

A **CLA** asks contributors to grant the project steward broad rights, typically
including the right to relicense. That is what makes a future proprietary
relicense possible — and it is also what makes many individual contributors, and
most corporate legal departments, decline to contribute at all. The signing step
alone loses drive-by fixes.

A **DCO** is a sign-off asserting the contributor has the right to submit the
work under the project's license. It adds `-s` to `git commit` and nothing else.
It does not permit relicensing: every contributor retains copyright, and a
license change would require asking all of them.

Theurian anticipates a hosted offering (§27). It is worth being precise about
what that requires: a hosted service built on Apache-2.0 code needs no CLA. A CLA
would only be needed to make the OSS Core itself proprietary — which §27 rules
out by requiring that Core remain fully functional standalone.

## Decision

**DCO. No CLA.**

1. Every commit carries `Signed-off-by: Name <email>` (`git commit -s`).
2. The DCO text (version 1.1, verbatim) lives at `docs/contributing/dco.md` and is
   referenced from `CONTRIBUTING.md`.
3. A CI job verifies the sign-off on every commit in a pull request. It is a
   required check.
4. Contributors retain copyright in their contributions. There is no copyright
   assignment.
5. `NOTICE` attributes the work collectively to "Theurian Contributors" rather
   than to any single entity.
6. **Relicensing the OSS Core away from Apache-2.0 requires the agreement of all
   copyright holders.** This is deliberately hard, and it is the guarantee this
   ADR exists to provide: adopters can depend on Core staying open (O-7).
7. A future hosted service is built *on* the Apache-2.0 Core, under the same
   license terms as any other user. It adds proprietary components around Core; it
   does not close Core.

## Consequences

### Positive

- The contribution barrier is one command-line flag.
- Corporate legal review is straightforward: Apache-2.0 in, Apache-2.0 out, no
  rights assignment.
- Adopters get a structural, not merely stated, guarantee against a rug-pull.
- Provenance for every contribution is recorded in Git history.

### Negative

- The project cannot relicense Core without unanimous consent. Intended.
- The DCO is a weaker patent and warranty instrument than a full CLA. Apache-2.0's
  own §3 patent grant covers most of the practical gap.
- Sign-off is easy to forget. Mitigated by a clear CI failure message with the
  exact `git commit --amend -s` fix, and a `.gitmessage` template.

### Neutral

- This matches the Linux kernel, Git, GitLab, and the CNCF default. It is what
  contributors expect.

## Alternatives considered

| Alternative | Why rejected |
| :-- | :-- |
| CLA with copyright assignment | Deters individual and corporate contributors; its main benefit is a future relicense that §27 rules out. |
| Neither DCO nor CLA | No provenance record; an unclear position if a contribution's origin is ever disputed. |
| DCO plus a separate patent grant | Apache-2.0 already grants patent rights; a second instrument adds friction without adding coverage. |

## Compliance

- `.github/workflows/dco.yml` verifies sign-off on every commit in a pull request
  and is a required check.
- `CONTRIBUTING.md` documents `git commit -s` and the amend fix.
- A `.gitmessage` template is provided and referenced in the development guide.
