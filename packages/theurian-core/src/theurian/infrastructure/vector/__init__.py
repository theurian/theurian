"""Vector store adapters. **Empty: there are none.**

This package said "``sqlite-vec`` by default, with an in-tree brute-force
fallback that is correct but slower" and "both adapters run the same conformance
suite, so the fallback cannot rot". There is no adapter here, ``sqlite_vec`` is
imported nowhere in ``src/``, and no conformance suite exists. ADR-0014's
Compliance section repeated the claim from here, which is how a plan in a
docstring became a control in an ADR.

What ships instead is an exact scan in ``SqliteIndexStore.search_dense``:
thousands of chunks is small enough that a full scan is fast enough and exactly
reproducible, and an ANN index would trade FR-R7's reproducibility for a
speed-up nobody here can measure (ADR-0021). So the plan above is not merely
unbuilt -- the default it names was decided against.

If an ANN adapter does land, ADR-0014's isolation rule applies: ``sqlite-vec``
is pre-1.0, so it stays reachable only from this package, and
``tests/unit/test_layering.py::test_volatile_dependencies_are_confined``
already holds that. A conformance suite becomes worth writing at the same
moment, because it needs two implementations to compare.
"""
