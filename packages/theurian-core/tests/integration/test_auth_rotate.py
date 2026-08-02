"""`theurian auth rotate` (ADR-0011, SEC-4, SEC-6).

Three error messages in Core tell a user to run this after their token has been
exposed. It has to exist, and it has to leave the system in a state where the
new token actually works.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from theurian.cli.main import app
from theurian.security.env_file import TOKEN_KEY

pytestmark = pytest.mark.integration

runner = CliRunner()


@pytest.fixture
def sandbox(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("THEURIAN_DATA_DIR", str(home / ".theurian"))
    monkeypatch.chdir(tmp_path)
    return home / ".theurian"


def _rotate() -> dict[str, Any]:
    result = runner.invoke(app, ["auth", "rotate", "--json"], catch_exceptions=False)
    assert result.exit_code == 0, result.stdout + (result.stderr or "")
    payload: dict[str, Any] = json.loads(result.stdout)
    return payload


def test_the_command_every_exposure_message_names_actually_exists(sandbox: Path) -> None:
    """`InsecureSecretPermissionsError` and two setup steps tell the user to run
    this. A remedy that errors out is worse than no remedy, because it is shown
    at the moment a credential has already been exposed."""
    payload = _rotate()

    assert payload["rotated"] is True
    assert (sandbox / "auth" / TOKEN_KEY).is_file()


def test_rotation_replaces_the_token(sandbox: Path) -> None:
    _rotate()
    first = (sandbox / "auth" / TOKEN_KEY).read_text()

    _rotate()

    assert (sandbox / "auth" / TOKEN_KEY).read_text() != first


def test_the_new_token_is_stored_privately(sandbox: Path) -> None:
    """A rotation that produced a world-readable token would be a rotation
    straight back into the state that prompted it."""
    _rotate()

    assert (sandbox / "auth" / TOKEN_KEY).stat().st_mode & 0o777 == 0o600
    assert (sandbox / "auth").stat().st_mode & 0o777 == 0o700


def test_the_env_file_is_rewritten_alongside_it(sandbox: Path) -> None:
    """It names the token's location. A stale one is a 401 with no visible
    cause."""
    _rotate()

    contents = (sandbox / "env").read_text()
    assert "THEURIAN_MCP_TOKEN" in contents
    assert str(sandbox / "auth" / TOKEN_KEY) in contents
    assert (sandbox / "env").stat().st_mode & 0o777 == 0o600


def test_the_new_token_never_appears_in_the_output(sandbox: Path) -> None:
    """SEC-6. Terminal scrollback is not a place to put a fresh credential."""
    payload = _rotate()
    token = (sandbox / "auth" / TOKEN_KEY).read_text().strip()

    assert token not in json.dumps(payload)
    assert token[:8] not in json.dumps(payload)
    assert payload["token"], "it must still confirm that something changed"


def test_the_output_says_what_the_user_must_do_next(sandbox: Path) -> None:
    """Rotation has three participants -- the file, the daemon, and every shell
    that exported the old value. Fixing one silently is how a rotation ends in
    an unexplained 401."""
    payload = _rotate()

    assert payload["nextSteps"]
    assert any("shell" in step.lower() for step in payload["nextSteps"])
