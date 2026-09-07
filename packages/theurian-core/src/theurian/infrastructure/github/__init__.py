"""GitHub adapter for the ``ReviewProvider`` port (ADR-0030).

**This package is the one place in the shipped wheel that reaches GitHub**, and
:mod:`~theurian.infrastructure.github.gh_cli` is the one module in it that does.
There is no HTTP client here and no production dependency was added for it: the
adapter spawns the **operator's own** ``gh`` binary as an argument vector, so
GitHub credentials stay in the operator's credential store and Theurian holds no
token.

Until this package held an adapter, what stood in for SEC-10's controls was an
absence -- nothing in the shipped package could reach out at all, pinned by
equality in ``tests/unit/test_network_call_sites.py``. That sentence had no
successor: the first time it is false, whatever it was protecting is
unprotected. ADR-0030 decision 1 is the replacement, stated in the positive, and
each of its clauses owes a test that goes RED when it stops holding. The module
docstrings carry them one by one; the shape is:

* exactly one module may reach GitHub, and the pinned spawn-site set grows by
  exactly that one entry;
* the endpoint is the literal ``graphql`` and repository identity travels as
  typed variables -- there is no URL for T-7's scheme allowlist to be needed on;
* the destination is pinned by ``--hostname github.com``;
* the child environment is **constructed** from a closed enumerated constant
  (:mod:`~theurian.infrastructure.github.environment`), never inherited and
  never merely scrubbed;
* the binary is resolved to an absolute path and no shell is used anywhere;
* no ``--paginate``: every page is a cursor this adapter hands back;
* a request timeout, a page cap, a pull-request cap, a per-thread comment cap
  and a per-response byte cap, each a named constant in
  :mod:`~theurian.infrastructure.github.limits`;
* a version floor, expressed as a constant with a refusal rather than as prose;
* ``gh`` absent or unauthenticated is a graded refusal envelope with a remedy,
  and the child's stderr surfaces only inside it.

**Repositories are allowlisted, and the check happens before any process
exists** (SEC-10). ``providers.review.repositories`` in ``.theurian/config.yaml``
is read by ``security/project_config.py`` and enforced by
``security/review_allowlist.py``; a repository the list does not name produces no
spawn, not a filtered result. An empty or absent list allows nothing. Only
**public** repositories are ingested in this version: an allowlisted repository
that resolves as private is refused at ingestion, and so is one GitHub redirects
to a different name.

The adapter fetches pull requests, reviews, threads, inline comments, resolution
state and CI outcomes as structured evidence. It must never classify, generalise
or call a model -- that separation is what lets raw ingestion succeed when
candidate generation fails (FR-V5), and here it holds structurally: no model
exists anywhere in this path.

**What is not here yet**, so no reader infers a capability from an adapter:
nothing lands on disk (ADR-0030 slice 2 owns the evidence files and the
ingestion-time secret scan), no CLI command reaches this code, and no MCP tool
exposes it -- ``system.capabilities`` reports ``reviewIngestion: false``, which
from slice 3 will mean *an ingestion call surface exists that a client may call*
rather than *this build cannot reach GitHub*.
"""

from __future__ import annotations

from theurian.infrastructure.github.review_provider import GitHubReviewProvider

__all__ = ["GitHubReviewProvider"]
