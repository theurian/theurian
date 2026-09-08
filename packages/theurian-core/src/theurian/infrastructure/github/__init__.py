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
* a request timeout, a page cap, a pull-request cap and a per-response byte cap,
  each a named constant in :mod:`~theurian.infrastructure.github.limits` -- and
  **no bound lives as a number inside a query string**: every ``first:`` literal a
  document spells is pinned to a constant there, which
  ``test_every_first_literal_in_a_document_is_pinned_to_a_constant`` reddens when
  one is not. The list above is deliberately not the list of caps, because the
  per-connection ones grow with the documents;
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

**What reaches this code, and what does not.** ``theurian review ingest`` does,
as of ADR-0030 slice 2: it composes this adapter, screens every record through
the ingestion secret gate, and lands what the gate cleared as evidence files
under ``.theurian/review/``. **No MCP tool exposes it**, so
``system.capabilities`` still reports ``reviewIngestion: false`` -- read that
narrowly, as the flag's own pin in ``tests/integration/test_mcp_tools.py`` says:
it means *no ingestion call surface is callable by a client*, never *this build
cannot reach GitHub*. Slice 3 adds the tool and flips it.
"""

from __future__ import annotations

from theurian.infrastructure.github.review_provider import GitHubReviewProvider

__all__ = ["GitHubReviewProvider"]
