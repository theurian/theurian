"""Clock and IdGenerator ports.

These are ports because time and identifiers are *inputs* to the state hash
(ADR-0007) and to every ``created_at`` field. Without controlling them, the claim
"the same migrations produce the same canonical state" cannot be asserted in a
test -- which would make ADR-0007 unverifiable.
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol, runtime_checkable

from theurian.domain.identifiers import Ulid


@runtime_checkable
class Clock(Protocol):
    """Supplies the current time.

    Implementations must return timezone-aware values. A naive datetime silently
    compares wrong across a DST boundary, and validity windows depend on those
    comparisons.
    """

    def now(self) -> datetime:
        """The current instant, timezone-aware and UTC-based."""
        ...


@runtime_checkable
class IdGenerator(Protocol):
    """Supplies ULIDs.

    Monotonicity matters: Theurian relies on lexical order equalling creation
    order for migration application order and revision history. A generator that
    returns out-of-order values within the same millisecond breaks that.
    """

    def new_ulid(self) -> Ulid:
        """A fresh ULID, monotonically increasing within this process."""
        ...
