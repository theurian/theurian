"""A mapping that counts how often a walk iterates it (issues #232, #245).

Both guards these tests drive are about *how many times* one shared sub-object
is materialised, and both walks read a mapping's children exactly once per
descent, through ``items()``. Counting that call is therefore counting descents,
which is what makes the assertions deterministic: the alternative is a
stopwatch, and a stopwatch on a loaded machine measures the machine.

A ``dict`` subclass rather than a ``Mapping`` implementation because both walks
dispatch on ``isinstance(node, dict)``, and a node that is not a ``dict`` would
be treated as a scalar and never descended into at all.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, override

if TYPE_CHECKING:
    # What `dict.items()` returns, and so what an override of it must return.
    # A type-checking name only: it is not bound at runtime.
    from _collections_abc import dict_items


class CountsVisits(dict[str, object]):
    """A mapping that records how often a walk descended into it."""

    def __init__(self, mapping: dict[str, object]) -> None:
        super().__init__(mapping)
        self.visits = 0

    @override
    def items(self) -> dict_items[str, object]:
        self.visits += 1
        return super().items()
