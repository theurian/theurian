"""SecretStore port (ADR-0011, SEC-4)."""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class SecretStore(Protocol):
    """Stores and retrieves local secrets.

    Backed by the OS secret store where one is available (macOS Keychain, Linux
    Secret Service) and by a 0600 file otherwise -- a headless Linux box often
    has no Secret Service, so the file backend is a supported configuration
    rather than a degraded one.

    Implementations must never log a secret value, and must never include one in
    an exception message. A store that raises ``KeyError("token abc123...")``
    puts the secret in every stack trace.
    """

    @property
    def backend_id(self) -> str:
        """Which backend is in use, e.g. ``keychain`` or ``file``.

        Reported by ``doctor`` so a user can see where their token actually lives.
        """
        ...

    async def get(self, key: str) -> str | None:
        """Fetch a secret, or ``None`` if absent.

        Raises:
            SecurityError: If the stored secret is readable by other local users
                (SEC-4). A world-readable token is refused rather than used.
        """
        ...

    async def set(self, key: str, value: str) -> None:
        """Store a secret with restrictive permissions.

        Creates parent directories at 0700 and the secret at 0600 when using the
        file backend.
        """
        ...

    async def delete(self, key: str) -> None:
        """Remove a secret. Deleting a missing key is not an error."""
        ...
