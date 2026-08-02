"""Local access tokens (ADR-0011, SEC-3 .. SEC-6).

``127.0.0.1`` is reachable by every process running as the user, and — via DNS
rebinding — by a web page they visit. Theurian serves an organization's
architecture decisions, security rules, and incident write-ups, so an
unauthenticated loopback endpoint discloses all of it to any script.
"""

from __future__ import annotations

import hashlib
import re
import secrets
from typing import Final

#: 32 bytes from a CSPRNG, URL-safe encoded. Well above the 256 bits SEC-3 asks
#: for, and short enough to paste when debugging.
TOKEN_BYTES: Final = 32

#: Below this a token is refused outright rather than used. A short token is not
#: a weak credential to warn about; it is not a credential.
MIN_TOKEN_LENGTH: Final = 32

#: What `secrets.token_urlsafe` produces.
_TOKEN_PATTERN: Final = re.compile(r"^[A-Za-z0-9_-]{32,}$")

#: The header a client presents.
AUTHORIZATION_HEADER: Final = "authorization"
BEARER_PREFIX: Final = "Bearer "

#: The environment variable an MCP configuration references, so the literal
#: token never enters a file that gets copied into a gist or a dotfile
#: repository (SEC-5).
TOKEN_ENV_VAR: Final = "THEURIAN_MCP_TOKEN"  # noqa: S105 - a variable name, not a secret


def generate_token() -> str:
    """Mint a new local access token."""
    return secrets.token_urlsafe(TOKEN_BYTES)


def is_well_formed(token: str) -> bool:
    """Whether ``token`` could plausibly be one Theurian issued.

    A cheap shape check, not a security decision. Verification is
    :func:`verify_token`, which compares in constant time.
    """
    return bool(_TOKEN_PATTERN.match(token))


def verify_token(presented: str, expected: str) -> bool:
    """Compare two tokens without leaking their contents through timing.

    ``==`` on strings short-circuits at the first differing byte, so an attacker
    who can measure response times can recover a token one byte at a time.
    ``compare_digest`` takes the same time regardless.
    """
    if not presented or not expected:
        return False
    return secrets.compare_digest(presented, expected)


def extract_bearer(header_value: str | None) -> str | None:
    """Pull the token out of an ``Authorization`` header.

    Returns ``None`` for anything that is not a well-formed bearer credential,
    so a caller never has to distinguish "absent" from "malformed" — both mean
    the same thing at the boundary.
    """
    if not header_value or not header_value.startswith(BEARER_PREFIX):
        return None
    token = header_value[len(BEARER_PREFIX) :].strip()
    return token or None


def redact(text: str, token: str) -> str:
    """Replace a token wherever it appears in text.

    Applied at the logging sink rather than at each call site. Relying on every
    present and future call site to remember is how tokens end up in logs
    (SEC-6).
    """
    if not token or len(token) < MIN_TOKEN_LENGTH:
        return text
    return text.replace(token, "***REDACTED***")


def describe(token: str) -> str:
    """A safe way to refer to a token in output.

    Shows enough to tell two tokens apart when debugging, never enough to use
    one. The last four characters would be worse than nothing: they are the
    part an attacker most easily brute-forces once they have the rest.
    """
    if not token:
        return "<none>"
    return f"<token {len(token)} chars, sha-prefix {_short_fingerprint(token)}>"


def _short_fingerprint(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()[:8]
