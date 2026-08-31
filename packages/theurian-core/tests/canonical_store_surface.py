"""What the ``CanonicalStore`` port publishes, derived once for every pin that counts it.

**Two claim modules now read the same port surface, and a second copy of the
derivation is the failure mode this whole file set exists to prevent.**

- ``tests/unit/test_connection_claims.py`` reads it to hold the write path's
  docstrings: "exclusivity is held by convention at each call site" is only true
  while the port publishes its writes directly, so that module counts them and
  watches for the single interface #439 owes landing beside them.
- ``tests/unit/test_adr_0018_claims.py`` reads it to hold ADR-0018's Milestone 5
  amendment, whose correction note spells the count in prose. Its
  ``test_the_amendment_spells_the_write_method_count_the_port_publishes``
  asserts the spelled word equals :func:`write_methods`, so the record goes RED
  whether the sentence drifts or the port gains a write method.

This is ``write_lock_claims.py``'s reasoning applied to a second derivation. A
copy of the member walk in each module would go RED in whichever one its author
remembered to update and stay green in the other -- and here the two modules do
not even agree on what they are measuring: one wants "more than one", the other
wants an exact number. One walk, called from both, fails both records together.

**The walk is over the MRO rather than over ``vars(port)``, and that is the whole
difference between a net and a hole.** ``vars`` reports only what a class body
declares, so a member reached through a base Protocol -- the shape a port split
into reads and writes takes -- is invisible to every rule built on it. The
measurement and the review round that found it are recorded on
:func:`public_methods`.

**Nothing here proves a lock is taken, or that a write goes through one.** These
are introspections of a ``Protocol``; they would stay green against a build whose
``write_transaction`` computed the right lock path and never flocked it. Both
importing modules disclaim the same about their own halves.
"""

from __future__ import annotations

import inspect
from typing import Any, Final, get_type_hints

from theurian.domain.ports.canonical_store import CanonicalStore

#: The modules the MRO scaffolding comes from. ``typing`` holds ``Protocol`` and
#: ``Generic``; ``builtins`` holds ``object``. A port's own bases -- the shipped
#: ones under ``theurian.domain.ports``, the synthetic ones in
#: ``test_connection_claims.py`` -- come from neither, so :func:`public_methods`
#: reads them and skips these.
MRO_LIBRARY: Final = frozenset({"typing", "builtins"})


def public_methods(port: type) -> dict[str, Any]:
    """Every public method a port Protocol declares, its base Protocols included.

    Takes the port rather than reading ``CanonicalStore`` directly, so the rules
    built on it can be driven by a synthetic Protocol through the same walk the
    shipped one takes. That matters for
    ``test_connection_claims.py``'s ``_transaction_shaped_members``, whose whole
    point is that it finds nothing today: a rule that always returned nothing
    would be indistinguishable from it unless something can be shown to make it
    fire.

    **The walk is over the MRO, not over ``vars(port)``, and that is the whole
    difference between a net and a hole.** ``vars`` reports only what a class
    body declares, so a ``transaction()`` reached through a base Protocol --
    ADR-0018 point 1's own spelling, and the natural shape for the port #439
    splits into reads and writes -- was invisible to every rule built on this
    function. Measured 2026-08-31 on a two-line Protocol pair: ``"transaction" in
    vars(port)`` is ``False`` while ``"transaction" in dir(port)`` is ``True``,
    so ``_transaction_shaped_members`` reported a clean port that declared
    exactly the member it watches for.

    The scaffolding is skipped by the module it comes from, :data:`MRO_LIBRARY`.
    Every Protocol drags ``typing.Protocol``, ``typing.Generic`` and
    ``builtins.object`` into its MRO, and none of the three is anything a port
    declares. They are excluded by name rather than left to the public-function
    filter because "they happen to expose none" is the sort of unstated premise
    this file set exists to refuse -- measured 2026-08-31 they expose zero apiece,
    and the skip means a future one that did would still not be read as a port
    member. The MRO is walked in reverse so a subclass declaration overwrites the
    base's, which is the resolution order Python itself uses.

    The skip is driven by
    ``test_connection_claims.py::test_the_member_walk_skips_library_scaffolding_and_reads_a_ports_own_base``,
    which forges ``typing`` onto a base that declares a public function: no real
    library base drives it, so without that sample the skip is a guard no input
    reaches.
    """
    methods: dict[str, Any] = {}
    for klass in reversed(port.__mro__):
        if klass.__module__ in MRO_LIBRARY:
            continue
        for name, member in vars(klass).items():
            if not name.startswith("_") and inspect.isfunction(member):
                methods[name] = member
    return methods


def write_methods() -> dict[str, Any]:
    """The ``CanonicalStore`` public methods that declare no return value.

    The classification is derived from the live annotations rather than from a
    list of names, and its reach is exactly that: on this port every mutating
    method is annotated ``-> None`` and every read returns a value, so "declares
    no return value" and "writes" coincide today. A future write that returned
    the id it wrote would drop out of this population, and both counting pins
    would go RED rather than silently narrow -- which is the direction an
    imprecise rule should fail in.

    **Its result reaches a durable record**, so it is not only a count. ADR-0018's
    Milestone 5 amendment spells the number in prose, and
    ``test_adr_0018_claims.py::test_the_amendment_spells_the_write_method_count_the_port_publishes``
    holds that word equal to ``len(write_methods())``. Anything that changes what
    this returns changes what that ADR is allowed to say.
    """
    return {
        name: method
        for name, method in public_methods(CanonicalStore).items()
        if get_type_hints(method).get("return") is type(None)
    }
