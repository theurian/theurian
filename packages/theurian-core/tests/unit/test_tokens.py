"""Local access tokens (ADR-0011, SEC-3 .. SEC-6)."""

from __future__ import annotations

import re

import pytest

from theurian.security.tokens import (
    MIN_TOKEN_LENGTH,
    TOKEN_ENV_VAR,
    describe,
    extract_bearer,
    generate_token,
    is_well_formed,
    redact,
    verify_token,
)


def test_generated_tokens_are_long_and_unique() -> None:
    tokens = {generate_token() for _ in range(100)}
    assert len(tokens) == 100, "a CSPRNG must not repeat across 100 draws"
    for token in tokens:
        assert len(token) >= MIN_TOKEN_LENGTH
        assert is_well_formed(token)


def test_generated_tokens_carry_enough_entropy() -> None:
    """SEC-3 asks for 256 bits. `token_urlsafe(32)` yields 256 bits encoded in
    roughly 43 URL-safe characters."""
    assert len(generate_token()) >= 43


@pytest.mark.parametrize(
    "value",
    ["", "short", "has spaces in it aaaaaaaaaaaaaaaaaaaaaaaaa", "has/slash" + "a" * 40],
)
def test_malformed_tokens_are_not_well_formed(value: str) -> None:
    assert not is_well_formed(value)


# -- Verification ----------------------------------------------------------


def test_matching_tokens_verify() -> None:
    token = generate_token()
    assert verify_token(token, token)


def test_differing_tokens_do_not_verify() -> None:
    assert not verify_token(generate_token(), generate_token())


@pytest.mark.parametrize(("presented", "expected"), [("", "x" * 40), ("x" * 40, ""), ("", "")])
def test_empty_tokens_never_verify(presented: str, expected: str) -> None:
    """An empty expected token would otherwise let an empty presentation in --
    the failure mode of a daemon started before its token was written."""
    assert not verify_token(presented, expected)


def test_a_prefix_does_not_verify() -> None:
    """Guards the constant-time comparison.

    `==` short-circuits at the first differing byte, letting an attacker who can
    measure response times recover a token one byte at a time.
    """
    token = generate_token()
    assert not verify_token(token[:-1], token)
    assert not verify_token(token + "x", token)


# -- Header parsing --------------------------------------------------------


def test_a_bearer_header_yields_its_token() -> None:
    assert extract_bearer("Bearer abc123") == "abc123"


@pytest.mark.parametrize(
    "header",
    [None, "", "abc123", "Basic abc123", "bearer abc123", "Bearer", "Bearer   "],
)
def test_anything_else_yields_none(header: str | None) -> None:
    """Absent and malformed mean the same thing at the boundary, so a caller
    never has to distinguish them."""
    assert extract_bearer(header) is None


def test_surrounding_whitespace_is_tolerated() -> None:
    assert extract_bearer("Bearer   abc123  ") == "abc123"


# -- Redaction and display -------------------------------------------------


def test_redaction_removes_the_token() -> None:
    """Applied at the logging sink, not at each call site: relying on every
    present and future call site to remember is how tokens reach logs."""
    token = generate_token()
    text = f"request failed with Authorization: Bearer {token} on /mcp"

    redacted = redact(text, token)

    assert token not in redacted
    assert "***REDACTED***" in redacted
    assert "/mcp" in redacted, "surrounding context must survive"


def test_redaction_ignores_a_short_needle() -> None:
    """A short string would match everywhere and destroy the log.

    `redact(text, "a")` must not turn every 'a' into a redaction marker.
    """
    assert redact("a normal log line", "a") == "a normal log line"


def test_describe_never_reveals_the_token() -> None:
    token = generate_token()
    described = describe(token)

    assert token not in described
    assert token[:8] not in described
    # The last characters are what an attacker most easily brute-forces once
    # they have the rest, so they are exactly what must not appear.
    assert token[-4:] not in described


def test_describe_distinguishes_two_tokens() -> None:
    """Useful for debugging requires telling them apart; safe requires not
    revealing either."""
    assert describe(generate_token()) != describe(generate_token())


def test_describe_handles_an_absent_token() -> None:
    assert describe("") == "<none>"


def test_the_env_var_name_is_the_published_one() -> None:
    """The MCP configuration template references this exact name; a change here
    silently breaks every configured client."""
    assert TOKEN_ENV_VAR == "THEURIAN_MCP_TOKEN"  # noqa: S105 - a variable name, not a secret
    assert re.fullmatch(r"[A-Z_]+", TOKEN_ENV_VAR)
