"""A seeded ULID generator (ADR-0003).

Produces valid, strictly increasing ULIDs from a counter, so a test can assert
on exact identifiers instead of matching a pattern.
"""

from __future__ import annotations

from typing import final

from theurian.domain.identifiers import Ulid

#: Crockford base32, the alphabet ULIDs use.
_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"

#: A ULID's first character encodes the high bits of a 48-bit timestamp and must
#: be 0-7, or the value overflows the 128-bit space.
_ULID_LENGTH = 26


@final
class SeededIdGenerator:
    """Deterministic, monotonic ULIDs of the form ``0...<counter>``."""

    def __init__(self, start: int = 1) -> None:
        self._counter = start

    def new_ulid(self) -> Ulid:
        value = self._encode(self._counter)
        self._counter += 1
        return Ulid(value)

    @staticmethod
    def _encode(number: int) -> str:
        digits: list[str] = []
        remaining = number
        while remaining:
            remaining, index = divmod(remaining, len(_ALPHABET))
            digits.append(_ALPHABET[index])
        encoded = "".join(reversed(digits)) or "0"
        # Left-pad with '0' so the counter occupies the low-order positions and
        # lexical order therefore matches numeric order.
        return encoded.rjust(_ULID_LENGTH, "0")
