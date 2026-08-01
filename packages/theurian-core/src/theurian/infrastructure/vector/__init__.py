"""Vector store adapters.

``sqlite-vec`` by default, with an in-tree brute-force fallback that is correct
but slower. ``sqlite-vec`` is pre-1.0, so it is reachable only from this package
and its replacement is a configuration change rather than a project (ADR-0014).

Both adapters run the same conformance suite, so the fallback cannot rot.
"""
