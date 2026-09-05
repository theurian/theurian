"""What one review-ingestion run may spend, as named constants (ADR-0030 clauses 7, 8, 10).

Every bound here is reached by a **reported, graded stop** -- never a silent
truncation and never an unbounded loop. The severity table grades "a caller can
make the system spend work no recorded limit bounds" as HIGH, so a cap that
exists only as a page size somewhere in a query string is not a bound: it has to
be a constant a test can read and prose can name, which is the shape
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
MAX_PULL_REQUESTS: Final = 500

#: The most comments one thread may carry before the read stops and reports.
#: A thread past this is truncated by the provider's own pagination otherwise,
#: which would be a silent loss inside a record that looks complete.
MAX_COMMENTS_PER_THREAD: Final = 100

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

#: The most bytes of a child's stderr this adapter will hold, before it is
#: decoded with replacement and sliced into a refusal envelope. Small: the point
#: is to locate a failure, not to relay a log.
MAX_CHILD_STDERR_BYTES: Final = 4_096

#: The lowest ``gh`` this adapter will spawn a request through, as
#: ``(major, minor, patch)``. See the module docstring: it is the version
#: ADR-0030's runs A-F measured, not a guess at the earliest that would work.
GH_VERSION_FLOOR: Final[tuple[int, int, int]] = (2, 86, 0)


def rendered_version(version: tuple[int, int, int]) -> str:
    """``(2, 86, 0)`` as ``2.86.0``, so the floor is spelled one way everywhere."""
    return ".".join(str(part) for part in version)
