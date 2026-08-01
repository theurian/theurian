"""Infrastructure layer: adapters implementing the domain ports.

An adapter may import :mod:`theurian.domain`. Nothing imports an adapter except
a composition root (``cli``, ``daemon``, ``mcp``), enforced by a lint rule and by
``tests/unit/test_layering.py``.

Adapters use their technology fully -- the SQLite adapter writes SQLite SQL. The
rule is containment, not abstraction of SQL itself (ADR-0003).
"""
