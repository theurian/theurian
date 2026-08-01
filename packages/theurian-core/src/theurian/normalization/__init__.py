"""Normalization: Source Layer to Canonical Layer.

A mechanical transformation. It never summarises, never infers, and never calls
a model -- a normalized document must be reproducible from its source bytes, or
the content hash that every downstream guarantee rests on means nothing.

Structured sources stay structured. Extracting text only is what makes coverage
and drift detection impossible later, and impossible to add back without
reprocessing everything (ADR-0010).
"""
