"""Secret storage adapters.

macOS Keychain and Linux Secret Service where available; a 0600 file otherwise.
The file backend is a supported configuration, not a degraded one -- a headless
Linux box often has no Secret Service.

A secret value never appears in a log or an exception message. A store that
raises ``KeyError("token abc123...")`` puts the secret in every stack trace
(SEC-6, ADR-0011).
"""
