"""Ingestion: discovering and reading sources.

Watches the project, resolves what changed, and hands bytes to the right parser.
Content-hash comparison is the early exit -- touching a file without changing it
costs one hash, not a reparse and a reindex.

A parser failure fails one document, not the run. A malformed YAML file among
two hundred must not make the other 199 unavailable.
"""
