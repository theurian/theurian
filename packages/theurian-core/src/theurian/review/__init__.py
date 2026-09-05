"""Review knowledge: ingestion, classification, candidate generation.

**Not implemented, and that is now a claim about this package rather than about
the capability.** This package holds no code. The review domain model --
``ReviewThread``, ``PromotionGate`` and ``KnowledgeCandidate`` -- is built and
lives in :mod:`theurian.domain.review`; the **fetch half of ingestion** shipped
with `ADR-0030 <../../../docs/adr/0030-github-review-ingestion-spawns-gh.md>`_
and lives in :mod:`theurian.infrastructure.github`, not here. What is owed is
everything after the fetch -- landing evidence on disk, classification, and
candidate generation -- and ``system.capabilities`` reports
``reviewIngestion: false`` while no tool exposes any of it, which is a narrower
statement than it used to be: it says no ingestion call surface is callable, not
that nothing reaches GitHub.

**The shipped ``review.findings`` tool is not this package, and does not make
the sentence above stale.** It serves ``Review-Finding:`` commit trailers read
out of *local* git history (ADR-0029) and is announced separately, as
``reviewFindings: true``; none of its code is here -- it lives in the domain
type, the git source, the SQLite findings store and the MCP tool. It reaches no
network, reads no thread, and generates no candidate, which is exactly why it
moved a different flag: the stages above are still owed, and ``reviewIngestion``
is still ``false``.

**Owned by `#479 <https://github.com/theurian/theurian/issues/479>`_**, filed
from #428's measurement after four nearer candidates were each read and verified
not to cover the work. `#429 <https://github.com/theurian/theurian/issues/429>`_
used to keep all three SEC-10 fetch controls; ADR-0030's adapter discharged the
**repository allowlist** and reduced private-network rejection on the ``gh``
path, so what stays #429's is the raw-URL context -- the scheme allowlist and
private-network rejection against the OpenAPI ``$ref`` fetcher. This docstring named
`#129 <https://github.com/theurian/theurian/issues/129>`_ until it closed
``COMPLETED`` on the wording of the documents that describe the absence rather
than on the code. Whether #479 is still open is a tracker fact no offline test
can hold, which is why it was read rather than assumed
(``docs/architecture/source-normalization.md`` carries that reasoning in full).

Review history is evidence; approved knowledge is a generalisation of it. The
step between them is a human judgement, and there is no code path that skips it
(ADR-0013).

The promotion gate decides whether a thread deserves a human's attention, using
seven observed facts. It never decides whether the generalisation is true.
"""
