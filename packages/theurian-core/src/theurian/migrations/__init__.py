"""Schema migrations for the derived SQLite store.

Distinct from *knowledge* migrations, which are YAML domain operations living in
the user's repository under ``.theurian/migrations/`` (ADR-0005).

These change table structure in a rebuildable cache; those change canonical
knowledge state and are reviewed by the user's team. Different concerns,
different reviewers, different rollback semantics -- hence two systems and two
distinctly named CLI commands.
"""
