"""Specification handling.

Specifications keep their native structure -- YAML, JSON, OpenAPI -- alongside a
text projection for lexical search. ``spec.getCoverage`` asks which declared
outcomes have verifying tests, which requires the outcomes to still exist as
data (ADR-0010, FR-T1).
"""
