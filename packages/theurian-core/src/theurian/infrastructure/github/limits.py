"""What one review-ingestion run may spend, as named constants (ADR-0030 clauses 7, 8, 10).

Every bound here is enforced by a **reported, graded stop**, or derived from
ones that are -- never an unbounded loop -- with **two exceptions, both named
here rather than left for a reader to find**. :data:`REAP_SECONDS` bounds the
cleanup that follows a stop rather than any work a caller asked for, so there is
nothing for it to report. :data:`MAX_CHILD_STDERR_BYTES` is the one bound that
*does* truncate silently: a child that writes 200,000 bytes of ASCII to stderr
has 2,000 characters of it published and the rest dropped with nothing saying so.
The 2,000 is
:data:`~theurian.domain.review_ingest.MAX_REFUSAL_DETAIL_CHARS` deciding, not
this constant -- 4,096 bytes are kept and the envelope's character bound cuts
them again -- and it is **fewer for multi-byte output**, because this bound
counts bytes and that one counts characters: 1,366 for Japanese text and 1,024
for emoji (all three measured 2026-09-07). That is a deliberate trade rather than an oversight --
what it cuts is a child's own diagnostic, not a record this adapter keeps, and
the alternative costs a marker inside a published field that already refuses an
oversized value at construction. It is recorded because "never a silent
truncation" was written here as a universal and this is its counterexample.

The severity table grades "a caller can make the system spend work no recorded
limit bounds" as HIGH, so a cap that exists only as a page size somewhere in a
query string is not a bound: it has to be a constant a test can read and prose
can name, which is the shape
[#26](https://github.com/theurian/theurian/issues/26)'s T-6 concurrency cap set.

**The version floor is the one version this design measured, and that is
deliberate.** ADR-0030's runs A-F were taken against ``gh`` 2.86.0, and the flag
and environment behaviours clauses 2-6 rest on are that binary's. A lower floor
would be a claim about versions nobody ran, so the floor is 2.86.0 and the
refusal says so. It is not an assertion that 2.85 misbehaves; it is a refusal to
reach GitHub through a binary this design has no measurement of. Raising the
floor is the moment the transport-override key set is re-taken, because member
(b) of ADR-0030's divergence class -- a setting a newer ``gh`` understands and
this check has never heard of -- is bounded only by what that version reads.
"""

from __future__ import annotations

from typing import Final

#: SEC-19. How long one spawned ``gh`` may take, wall clock, from spawn to the
#: last byte this adapter reads. Generous for a GraphQL page over a public
#: repository and short enough that a hung child is an error rather than a hang.
REQUEST_TIMEOUT_SECONDS: Final = 30.0

#: How long a killed child is given to die before it is left to the runtime.
#:
#: The module docstring's **first** exception -- ``MAX_CHILD_STDERR_BYTES`` is
#: the second -- and here is what this one costs. It is spent
#: *after* a graded stop rather than on work a caller asked for -- reaping the
#: child so no refusal leaves a process behind. It is also what an external
#: cancellation pays: ``gh_cli._end`` runs from a ``finally``, so a caller that
#: cancels a ``run_bounded`` waits for the unwind instead of returning at once.
#: That replaced a worse trade, a cancellation that returned immediately and
#: left a live child and a pending drain task behind.
REAP_SECONDS: Final = 5.0

#: How many nodes one page asks for. Not a *bound* on anything by itself -- it is
#: the page size the caps below are counted in -- but it is a constant rather
#: than a literal in a query because the two caps are stated in terms of it.
PAGE_SIZE: Final = 50

#: The most pages any one paginated read will request before it stops and
#: reports. It bounds a read whose element count has no cap of its own -- the
#: review threads of a single pull request -- so no response's ``hasNextPage``
#: can keep this adapter asking.
MAX_PAGES: Final = 20

#: The most pull requests one ``list_pull_requests`` call will return, and the
#: ceiling on the ``limit`` a caller may ask for. Ten pages at
#: :data:`PAGE_SIZE`, so it bites before :data:`MAX_PAGES` does on that read and
#: both caps stay reachable rather than one shadowing the other.
#:
#: **On full pages.** What this counts is pull requests collected, not pages
#: read, and GitHub may answer a page with fewer nodes than were asked for. A
#: read of short pages therefore reaches :data:`MAX_PAGES` first and stops
#: there: measured against a stand-in ``gh`` answering one node and another page
#: every time (2026-09-07), the page cap fires after 22 spawns and this cap
#: never does.
MAX_PULL_REQUESTS: Final = 500

#: The most comments one thread may carry before the read stops and reports.
#: A thread past this is truncated by the provider's own pagination otherwise,
#: which would be a silent loss inside a record that looks complete.
MAX_COMMENTS_PER_THREAD: Final = 100

#: The most labels one pull request may carry before the read stops and reports.
#: A label is **author-controlled text** the ingestion scan reads (ADR-0030
#: decision 3's table), and the ``labels`` connection paginates like every other,
#: so without a check a pull request carrying sixty of them arrives with fifty
#: recorded and nothing saying the other ten -- or what they say -- exist.
#:
#: **A chosen ceiling rather than a measured distribution**, said plainly because
#: every other number in this file that looks like a measurement is one: this
#: adapter has ingested no repository, so there is nothing here to cite. It is set
#: above :data:`MAX_LINKED_ISSUES` because a label set is not a hand-written list
#: the way a closing-issue list is -- automation applies labels too -- and the
#: overflow is meant to read as an anomaly worth reporting rather than as an
#: ordinary busy pull request.
MAX_LABELS_PER_PULL_REQUEST: Final = 50

#: The most issues one pull request may close before the read stops and reports.
#: The ``closingIssuesReferences`` connection paginates like every other, and the
#: adapter asks for one page of it and follows no cursor -- so without a check a
#: pull request closing forty issues arrives looking exactly like one closing
#: twenty, and the record would name half the issues while looking whole.
MAX_LINKED_ISSUES: Final = 20

#: The most bytes one child response may produce. Set beside a recorded number
#: rather than invented: ``MAX_SOURCE_FILE_BYTES`` (``security/paths.py``) is
#: 8 MiB and is what ingestion already enforces on a file it reads, and a
#: repository's comment bodies are content Theurian does not control in exactly
#: the same way.
#:
#: **The cap is only half of it; the read shape is the other half.** An
#: unbounded ``capture_output`` measured after the fact has already paid for
#: whatever the child produced. ``gh_cli.run_bounded`` reads incrementally and
#: stops at this number, which is what
#: ``test_a_child_that_overruns_the_cap_is_refused_without_waiting_for_it_to_finish``
#: drives under a bounded wait.
MAX_RESPONSE_BYTES: Final = 8 * 1024 * 1024

#: **The derived byte ceiling on one paginated read**, recorded because neither
#: constant states it and a reader pricing a call needs the product rather than
#: the factors: :data:`MAX_PAGES` pages at :data:`MAX_RESPONSE_BYTES` each is
#: 160 MiB of child output a single ``list_pull_requests`` or ``get_threads``
#: may make this process read -- both reach the page cap, and the earlier
#: version of this sentence named only the second. What bounds *memory* is the
#: per-page cap -- a page is released once it is parsed -- so the two numbers
#: answer different questions and both are needed.
MAX_READ_BYTES_PER_CALL: Final = MAX_PAGES * MAX_RESPONSE_BYTES

#: The two probes -- ``gh --version`` and ``gh auth status`` -- counted as what
#: they are, spawns, because the two ceilings below are counted in spawns. They
#: run once per adapter instance rather than once per call, so including them
#: prices the first call rather than every call.
_PROBE_SPAWNS: Final = 2

#: **The derived spawn ceiling on one documented call.** The byte ceiling above
#: prices what a call may *read*; this prices what it may *start*, and no
#: constant states it on its own. Measured rather than reasoned: 22 children for
#: ``list_pull_requests`` and 22 for ``get_threads``, against a stand-in ``gh``
#: that answers a short page and another cursor every time (2026-09-07).
MAX_SPAWNS_PER_CALL: Final = _PROBE_SPAWNS + MAX_PAGES

#: **The derived wall-clock ceiling on one documented call**, at the recorded
#: worst case per spawn: :data:`REQUEST_TIMEOUT_SECONDS` before a child is
#: stopped and :data:`REAP_SECONDS` more for it to die. 770 seconds -- thirteen
#: minutes of bounded, graded refusing -- which is not a number a reader derives
#: from a 30-second timeout, and is what a caller with a deadline of its own has
#: to price against.
MAX_SECONDS_PER_CALL: Final = MAX_SPAWNS_PER_CALL * (REQUEST_TIMEOUT_SECONDS + REAP_SECONDS)

# -- what a whole run may spend -----------------------------------------------
#
# The three ceilings above price **one port call**. A run makes many, and the
# severity table grades "a caller can make the system spend work no recorded
# limit bounds" as HIGH -- so the run-level products are derived here rather
# than left for a reader to multiply. Every factor is imported; no number below
# is restated, which is what keeps these from drifting when a cap moves.

#: **How many port calls one ``review ingest`` run may make**: one
#: :meth:`~theurian.domain.ports.review_provider.ReviewProvider.list_pull_requests`,
#: then ``get_threads`` and ``get_reviews`` once each per pull request the
#: listing answered with. The listing is capped at :data:`MAX_PULL_REQUESTS`, so
#: ``1 + 2 * MAX_PULL_REQUESTS`` is the ceiling.
#:
#: **A skipped pull request costs no fetch.** A record the listing could not
#: build is answered as a skip and never fetched, so the ``2 *`` term counts
#: *built* records and this bound is reached only by a window in which every
#: pull request built. ``test_a_run_makes_one_listing_call_and_two_per_pull_request``
#: in ``tests/unit/test_review_ingest_service.py`` drives the shape against the
#: real service and is what fails if a third per-record read appears.
MAX_PORT_CALLS_PER_RUN: Final = 1 + 2 * MAX_PULL_REQUESTS

#: **The derived spawn ceiling on one run.** The probes are counted once, not
#: once per call: ``GitHubReviewProvider._ready`` memoises the probed ``gh`` for
#: the adapter's lifetime and one run uses one adapter. Every call after that may
#: reach :data:`MAX_PAGES` children.
MAX_SPAWNS_PER_RUN: Final = _PROBE_SPAWNS + MAX_PORT_CALLS_PER_RUN * MAX_PAGES

#: **The derived wall-clock ceiling on one run**, at :data:`MAX_SECONDS_PER_CALL`'s
#: own worst case per spawn. It is a large number -- days, not minutes -- and it
#: is the honest one: a run that timed every child out spends this long refusing
#: in a graded, reported way. What makes it payable is that the operator started
#: it: ``review ingest`` is a CLI verb with no MCP surface in ADR-0030 slice 2,
#: so no agent reaches this. ``--limit`` is how a caller buys a smaller ceiling
#: -- every product here is linear in it -- and the shipped default is one page
#: rather than :data:`MAX_PULL_REQUESTS`.
MAX_SECONDS_PER_RUN: Final = MAX_SPAWNS_PER_RUN * (REQUEST_TIMEOUT_SECONDS + REAP_SECONDS)

#: **The derived byte ceiling on one run**: what this process may be made to
#: *read* from children, as opposed to hold. Memory stays bounded by
#: :data:`MAX_RESPONSE_BYTES`, because a page is released once it is parsed, and
#: a landed record is bounded again by ``security/paths.py``'s
#: ``MAX_SOURCE_FILE_BYTES`` before it is written.
MAX_READ_BYTES_PER_RUN: Final = MAX_PORT_CALLS_PER_RUN * MAX_READ_BYTES_PER_CALL

#: The most stdout a **probe** may produce. ``gh --version`` prints one line and
#: ``gh auth status`` a short report, so this is generous by orders of magnitude
#: against either and still nothing a binary can spend memory with.
#:
#: Its own constant rather than a borrowed one: the probes used to pass
#: :data:`MAX_CHILD_STDERR_BYTES` as their stdout cap, which meant an oversized
#: ``gh --version`` was refused as "a GitHub response larger than the recorded
#: 4096-byte cap" -- a *stderr* bound, named as a *response* bound, about a
#: vector that makes no request.
#:
#: It is the same number as ``gh_cli._CHUNK_BYTES`` and that is a coincidence,
#: recorded so a reader does not take it for a relation: one is how much a probe
#: may print in total, the other how much stdout is taken per read. Neither is
#: derived from the other and moving one is no reason to move the other.
MAX_PROBE_STDOUT_BYTES: Final = 64 * 1024

#: The most bytes of a child's stderr this adapter will hold, before it is
#: decoded with replacement and sliced into a refusal envelope. Small: the point
#: is to locate a failure, not to relay a log.
#:
#: **The module docstring's second exception, and this is the cost.** What is
#: dropped is dropped without a marker: a reader of a refusal's ``detail`` cannot
#: tell a child that wrote two thousand characters from one that wrote two
#: hundred thousand. Every other bound in this file reports; this one is a
#: prefix of somebody else's error output, where a report would have to fit
#: inside the same published field the prefix is already competing for.
MAX_CHILD_STDERR_BYTES: Final = 4_096

#: The lowest ``gh`` this adapter will spawn a request through, as
#: ``(major, minor, patch)``. See the module docstring: it is the version
#: ADR-0030's runs A-F measured, not a guess at the earliest that would work.
GH_VERSION_FLOOR: Final[tuple[int, int, int]] = (2, 86, 0)


def rendered_version(version: tuple[int, int, int]) -> str:
    """``(2, 86, 0)`` as ``2.86.0``, so the floor is spelled one way everywhere."""
    return ".".join(str(part) for part in version)
