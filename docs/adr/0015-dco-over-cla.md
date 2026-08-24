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
3. A CI job verifies the sign-off on every commit in a pull request, and **it is
   a required status check on `main`** — see the Compliance section and
   [#67](https://github.com/theurian/theurian/issues/67), which was open from
   this ADR's acceptance until 2026-08-23 for exactly this. The enforcement is
   what point 3 is worth: an unenforced sign-off records intent and proves
   nothing about the commits that actually landed.
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

- `.github/workflows/shared.yml`, job `commits`, step "Every commit is signed off
  (DCO)" verifies the trailer on every non-merge commit in a pull request and
  names both remedies (`--amend -s` and `rebase --signoff`) in the failure. This
  section said the check lived in `.github/workflows/dco.yml`, which has never
  existed; the control does, under a different name.
- That job is a **required status check** on `main`, under the check name
  `Conventional Commits and DCO`, so a missing sign-off blocks the merge instead
  of reporting beside it. It is requirable because it reports on every pull
  request whatever the change touches: `shared.yml` carries no path filter and
  the job's only condition is `github.event_name == 'pull_request'`. Measured
  2026-08-23: present with a conclusion on the head commit of all 30 most
  recently merged pull requests, 30/30 `success`.
- `CONTRIBUTING.md` documents `git commit -s` and the amend fix, and lists the
  required set under
  [What `main` requires](https://github.com/theurian/theurian/blob/main/CONTRIBUTING.md#what-main-requires).
- A `.gitmessage` template is provided and referenced in the development guide.

Resolved, with what it used to say:

- **The DCO check is now a required status check.** This section first claimed
  it was one, then corrected itself to say it was not: until 2026-08-23 `main`
  had no required status checks at all
  (`branches/main/protection/required_status_checks` returned 404), so the
  `commits` job reported a missing sign-off without blocking the merge. That was
  a repository setting rather than a file, which is why no amount of reading the
  tree found it; it was filed as
  [#67](https://github.com/theurian/theurian/issues/67) and closed by applying
  the required set.

  What this note used to conclude from that — "a pull request with an unsigned
  commit can be merged by anyone able to merge" — is false, and #197 proved it.
  `main`'s `required_signatures` setting (a branch-protection mechanism, not a
  status check) blocks any commit without a verified cryptographic signature,
  regardless of its sign-off state. #197, the first contribution from outside
  the team, carried a correct sign-off and was still refused with every check
  green — landable only by an admin override, not by "anyone able to merge." The
  distinction outlives the gap and is the part worth keeping: sign-off is
  gate-enforced by a status check that reports, while cryptographic signing is
  gate-enforced by a branch rule that reports through no check at all
  ([Signing your commits](https://github.com/theurian/theurian/blob/main/CONTRIBUTING.md#signing-your-commits)).
