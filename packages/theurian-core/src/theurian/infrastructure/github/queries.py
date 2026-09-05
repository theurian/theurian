"""The two GraphQL documents, as literals (ADR-0030 clause 2).

**Repository identity never reaches a document.** Both documents below are
module constants with no interpolation of any kind: the owner and the name
travel as typed GraphQL *variables*, so there is no path segment a repository
name can escape into and no string formatting to get wrong. That is what makes
clause 2 a checkable property rather than a promise about quoting -- and it is
why the endpoint is the literal ``graphql`` rather than ``repos/{owner}/{repo}``,
whose REST shape would interpolate caller data into a path.

Both were validated against the live schema on 2026-09-05 with ``gh`` 2.86.0
(``gh api graphql --hostname github.com``), which is also where
:data:`STATUS_ROLLUP_STATES` was introspected.
"""

from __future__ import annotations

from typing import Final

#: The GraphQL variable names this adapter binds, and the only names it binds.
#: A test holds every ``-f`` element of a spawned vector to ``name=value`` with
#: ``name`` in this set, so a repository name cannot arrive as anything else.
VARIABLE_NAMES: Final[frozenset[str]] = frozenset(
    {"query", "owner", "name", "first", "after", "number"}
)

#: Pull requests, newest first, with the fields FR-V1 names.
#:
#: ``author { ... on Node { id } }`` is how a stable provider id is read off an
#: ``Actor``: every implementation of that interface -- ``User``, ``Bot``,
#: ``Organization``, ``Mannequin``, ``EnterpriseUserAccount`` -- is also a
#: ``Node``, so one inline fragment covers them all rather than five. Verified
#: against the live schema rather than assumed.
PULL_REQUESTS: Final = """\
query($owner: String!, $name: String!, $first: Int!, $after: String) {
  repository(owner: $owner, name: $name) {
    nameWithOwner
    isPrivate
    pullRequests(first: $first, after: $after, orderBy: {field: CREATED_AT, direction: DESC}) {
      pageInfo { hasNextPage endCursor }
      nodes {
        number
        title
        url
        createdAt
        merged
        mergedAt
        headRefOid
        baseRefOid
        author { login ... on Node { id } }
        mergeCommit { oid }
        closingIssuesReferences(first: 20) { nodes { number } }
        commits(last: 1) { nodes { commit { statusCheckRollup { state } } } }
      }
    }
  }
}
"""

#: One pull request's review threads, with their comments and resolution state.
#:
#: ``resolvedBy`` is here and no resolution timestamp is: ``PullRequestReviewThread``
#: carries none (ADR-0030 decision 5), which is why ``ReviewResolution`` records
#: an unknown ``resolved_at`` rather than a fabricated one.
REVIEW_THREADS: Final = """\
query($owner: String!, $name: String!, $number: Int!, $first: Int!, $after: String) {
  repository(owner: $owner, name: $name) {
    nameWithOwner
    isPrivate
    pullRequest(number: $number) {
      number
      reviewThreads(first: $first, after: $after) {
        pageInfo { hasNextPage endCursor }
        nodes {
          id
          isResolved
          isOutdated
          path
          line
          startLine
          resolvedBy { login ... on Node { id } }
          comments(first: 100) {
            pageInfo { hasNextPage }
            nodes {
              id
              body
              createdAt
              originalCommit { oid }
              author { login ... on Node { id } }
            }
          }
        }
      }
    }
  }
}
"""

#: ``StatusState``'s members, introspected 2026-09-05 against ``gh`` 2.86.0:
#: ``{"EXPECTED", "ERROR", "FAILURE", "PENDING", "SUCCESS"}``.
#:
#: Recorded as a dated measurement, and the mapping below is stated by
#: **semantics** rather than by this list, because the list is what a future
#: schema may extend. The load-bearing half is the default: an unrecognised
#: value becomes *unknown*, never *failed*.
STATUS_ROLLUP_STATES: Final[frozenset[str]] = frozenset(
    {"EXPECTED", "ERROR", "FAILURE", "PENDING", "SUCCESS"}
)

#: The rollup states that mean the checks definitely passed, and the ones that
#: mean they definitely did not. Everything else -- pending, expected, absent, or
#: a member this version of the adapter does not recognise -- maps to ``None``.
ROLLUP_SUCCEEDED: Final[frozenset[str]] = frozenset({"SUCCESS"})
ROLLUP_FAILED: Final[frozenset[str]] = frozenset({"ERROR", "FAILURE"})


def ci_outcome(state: str | None) -> bool | None:
    """The status rollup as ``ReviewEvent.ci_successful``'s tri-state.

    A definite success is ``True``, a definite failure or error is ``False``, and
    **anything else is** ``None``: pending, expected, absent, or a value this
    adapter has never heard of. The default is the part that matters -- on the
    ingested record an unrecognised value becomes *unknown*, never *failed*, so
    no downstream reader sees a verdict the provider did not give.
    """
    if state in ROLLUP_SUCCEEDED:
        return True
    if state in ROLLUP_FAILED:
        return False
    return None
