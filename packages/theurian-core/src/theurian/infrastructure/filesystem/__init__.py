"""Filesystem adapters: source parsers, object storage, file watching.

Every path goes through :mod:`theurian.security.paths`. Every parser enforces
the size, depth, and expansion limits there, uses a safe loader, and never
fetches an external reference (SEC-7, SEC-8, SEC-10).

Parsers do not trust their input. Adding a format is a new adapter and no domain
or application change (FR-S4).
"""
