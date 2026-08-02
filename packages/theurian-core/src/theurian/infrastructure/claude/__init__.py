"""Adapters for Claude Code's own configuration.

Read-only towards Claude Code's state file. Writes are delegated to the `claude`
CLI, which owns that file's format and its concurrency (ADR-0012).
"""
