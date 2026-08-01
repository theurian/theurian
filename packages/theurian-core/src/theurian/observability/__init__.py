"""OpenTelemetry tracing and metrics.

Off by default and opt-in. Never emits knowledge bodies, tokens, or file
contents: a telemetry pipeline is a copy of whatever you put in it, usually
somewhere with a different retention policy than you assumed (NFR-11, SEC-6).
"""
