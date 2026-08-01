"""The daemon: HTTP server, lifecycle, and single-instance enforcement.

A composition root. One process per user per machine, bound to ``127.0.0.1``
(ADR-0002).

Single-instance uses three independent mechanisms because each alone has a known
failure mode: an OS file lock, a port health probe, and a startup handshake
reporting version and data directory. A losing starter exits 0 after confirming
the winner is healthy -- it never kills the winner and never repairs data
automatically.
"""
