"""A frozen clock (ADR-0003).

Time is an input to the state hash and to every ``created_at``. Controlling it is
what makes "the same migrations produce the same canonical state" assertable
rather than merely believed.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import final


@final
class FrozenClock:
    """Returns a fixed instant, advancing only when told to."""

    def __init__(self, start: datetime | None = None) -> None:
        self._now = start or datetime(2026, 8, 2, 12, 0, tzinfo=UTC)
        if self._now.tzinfo is None:
            raise ValueError("FrozenClock must start from a timezone-aware datetime")

    def now(self) -> datetime:
        return self._now

    def advance(self, seconds: float = 1.0) -> datetime:
        self._now += timedelta(seconds=seconds)
        return self._now
