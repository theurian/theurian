"""Clock and IdGenerator adapters (ADR-0003).

Production implementations of the two determinism ports. Their fakes live in
``tests/fakes`` and are what make "the same migrations produce the same state
hash" assertable.
"""

from __future__ import annotations

import threading
from datetime import UTC, datetime
from typing import final

from ulid import ULID

from theurian.domain.identifiers import Ulid


@final
class SystemClock:
    """The wall clock, always timezone-aware and UTC-based.

    Naive datetimes are never returned. A naive value compares wrong across a
    DST boundary, and knowledge validity windows depend on those comparisons.
    """

    def now(self) -> datetime:
        return datetime.now(UTC)


@final
class UlidGenerator:
    """Monotonic ULID generation.

    ``python-ulid`` derives the random component independently per call, so two
    ULIDs created in the same millisecond can come out in either lexical order.
    Theurian relies on lexical order equalling creation order -- for migration
    application order, for revision history, and for the deterministic tie-break
    in topological sorting. This wrapper therefore enforces strict monotonicity
    itself rather than trusting the library's timestamp resolution.

    Thread-safe: the daemon serialises writes, but id generation happens outside
    the write path and a lock here costs nothing measurable.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._previous: str | None = None

    def new_ulid(self) -> Ulid:
        with self._lock:
            candidate = str(ULID())
            if self._previous is not None and candidate <= self._previous:
                # Same millisecond, unlucky randomness. Increment the previous
                # value in Crockford base32 rather than spinning on the clock:
                # a busy loop would make id generation take a millisecond.
                candidate = _increment_base32(self._previous)
            self._previous = candidate
            return Ulid(candidate)


#: Crockford base32, excluding I, L, O, and U -- the alphabet ULIDs use.
_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_CROCKFORD_INDEX = {c: i for i, c in enumerate(_CROCKFORD)}


def _increment_base32(value: str) -> str:
    """Return the next ULID in lexical order after ``value``.

    Raises:
        OverflowError: If ``value`` is the maximum ULID, where no successor
            exists. Unreachable in practice -- it requires a timestamp far past
            the year 10889 -- but silently wrapping to a smaller id would break
            the ordering guarantee this class exists to provide.
    """
    characters = list(value)
    position = len(characters) - 1

    while position >= 0:
        index = _CROCKFORD_INDEX[characters[position]] + 1
        if index < len(_CROCKFORD):
            characters[position] = _CROCKFORD[index]
            return "".join(characters)
        characters[position] = _CROCKFORD[0]
        position -= 1

    raise OverflowError(f"No ULID sorts after {value}")
