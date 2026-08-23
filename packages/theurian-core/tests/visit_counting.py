"""Containers that count how often a walk descends into them (issues #232, #245).

Both guards these tests drive are about *how many times* one shared sub-object
is materialised, and both walks read a container's children exactly once per
descent -- a mapping through ``items()``, a sequence through ``iter()``.
Counting that call is therefore counting descents, which is what makes the
assertions deterministic: the alternative is a stopwatch, and a stopwatch on a
loaded machine measures the machine.

``dict`` and ``list`` subclasses rather than ``Mapping``/``Sequence``
implementations because both walks dispatch on ``isinstance(node, dict)`` and
``isinstance(node, list | tuple)``, and a node that is neither would be treated
as a scalar and never descended into at all.

**Two classes rather than one, because a guard can be written for mappings
alone.** Both walks are recursive over containers of either kind, so a memo or a
budget that skips the sequence branch keeps every mapping-shaped assertion green
while a list-shaped document re-explodes -- measured as two surviving mutations,
``proj-sequence-no-propagate`` and ``refs-memo-only-for-mappings``, against a
suite whose shared-node fixtures were all mappings.
"""

from __future__ import annotations

from collections.abc import Iterator
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


class CountsIterations(list[object]):
    """A sequence that records how often a walk descended into it.

    ``__iter__`` rather than ``__getitem__``: both walks reach a sequence's
    children through ``enumerate(node)``, which calls ``iter()`` once per
    descent and then advances the iterator, so ``__getitem__`` is never called
    at all and an override of it would count zero however often the node was
    walked.
    """

    def __init__(self, items: list[object]) -> None:
        super().__init__(items)
        self.iterations = 0

    @override
    def __iter__(self) -> Iterator[object]:
        self.iterations += 1
        return super().__iter__()
