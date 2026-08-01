"""Git adapters: worktree discovery, commit metadata, diffs, blob resolution.

``git`` is invoked as an argument vector with ``shell=False``. A command built by
string concatenation from repository-controlled data is an injection waiting to
happen (SEC-9).

Provides the commit and blob SHAs that make a ``SourceAnchor`` resolve to an
immutable object rather than a path that may since have moved.
"""
