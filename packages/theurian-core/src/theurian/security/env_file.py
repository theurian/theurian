"""The shell snippet that exports the token by reference (SEC-5, ADR-0011).

Pure text. It lives here rather than beside the file-backed secret store because
the setup steps in the application layer render it, and the application layer
depends on ports rather than adapters (ADR-0003).
"""

from __future__ import annotations

from pathlib import Path

from theurian.security.tokens import TOKEN_ENV_VAR

#: The token file's name inside the auth directory.
TOKEN_KEY = "mcp-token"  # noqa: S105 - a file name, not a secret


def env_file_contents(data_dir: Path) -> str:
    """The snippet that exports the token from its 0600 file.

    The secret lives in exactly one place. Everything else — the MCP
    configuration, the shell profile — points at it (SEC-5).
    """
    token_path = data_dir / "auth" / TOKEN_KEY
    return (
        "# Written by `theurian setup`. Sourced by your shell profile so that\n"
        "# Claude Code can expand ${THEURIAN_MCP_TOKEN} in its MCP configuration\n"
        "# without the literal token ever entering a config file (ADR-0011).\n"
        f'{TOKEN_ENV_VAR}="$(cat "{token_path}")"\n'
        f"export {TOKEN_ENV_VAR}\n"
    )
