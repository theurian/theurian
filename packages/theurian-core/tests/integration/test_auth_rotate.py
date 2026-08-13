"""`theurian auth rotate` (ADR-0011, SEC-4, SEC-6).

Three error messages in Core tell a user to run this after their token has been
exposed. It has to exist, and it has to leave the system in a state where the
new token actually works.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from theurian.cli.main import app
from theurian.security.env_file import (
    ENV_BLOCK_END,
    ENV_BLOCK_START,
    TOKEN_KEY,
    env_block,
    legacy_env_file_contents,
)

pytestmark = pytest.mark.integration

runner = CliRunner()

#: A 0400 file is what makes the OS refuse the write below, and root is refused
#: nothing. Skipped rather than adapted: as root the rotation would simply
#: succeed and the test would assert its way through the arm it exists to check.
_CANNOT_BE_REFUSED_BY_A_MODE = sys.platform == "win32" or os.geteuid() == 0


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


def test_rotation_keeps_the_lines_the_user_added_to_the_env_file(sandbox: Path) -> None:
    """#128. Rotation is the second writer of this file, with the same root cause.

    It rendered the whole file and truncated the rest of it, exactly as setup
    did -- and this is the worse of the two places for it, because a rotation is
    usually run *because* a credential has been exposed. Taking something away
    silently at that moment is how a person ends up with two problems.

    Seeded as a machine a released version set up and its owner then appended
    to, and asserted as the exact whole file: the old rendering replaced where
    it stood, the appended line still after it, and one export of the variable
    rather than two.
    """
    sandbox.mkdir(parents=True, mode=0o700)
    mine = "export MY_OTHER_VAR=keepme\n"
    (sandbox / "env").write_text(legacy_env_file_contents(sandbox) + mine, encoding="utf-8")

    _rotate()

    written = (sandbox / "env").read_text(encoding="utf-8")
    assert written == env_block(sandbox) + "\n" + mine
    assert written.count("export THEURIAN_MCP_TOKEN\n") == 1
    assert (sandbox / "env").stat().st_mode & 0o777 == 0o600


def test_rotation_leaves_an_env_file_it_cannot_delimit_alone_and_says_so(sandbox: Path) -> None:
    """SEC-18 and SEC-4 pulling opposite ways, resolved in favour of the token.

    Markers that do not delimit one block leave setup unable to tell which lines
    are its own, so the file is not written. Refusing to *rotate* over that
    would be the wrong trade in the other direction: the exposed credential
    outranks a comment marker, and the token has already been replaced by the
    time this file is reached.

    So both halves are asserted together -- the rotation happened, the file did
    not move, and the person is told which file to repair. Any two of those
    without the third is a defect: a silent rewrite, a refused rotation, or a
    machine that now 401s with the remedy nowhere in the output.
    """
    sandbox.mkdir(parents=True, mode=0o700)
    seeded = f"export MINE=1\n{ENV_BLOCK_START}\nexport THEURIAN_MCP_TOKEN=by-hand\n"
    (sandbox / "env").write_text(seeded, encoding="utf-8")

    payload = _rotate()

    assert payload["rotated"] is True, "an exposed token is not left in place over a marker"
    assert (sandbox / "auth" / TOKEN_KEY).is_file()
    assert (sandbox / "env").read_text(encoding="utf-8") == seeded, "the file is left as it was"
    remedy = "\n".join(payload["nextSteps"])
    assert str(sandbox / "env") in remedy, "and the output names the file that was skipped"
    assert ENV_BLOCK_START in remedy and ENV_BLOCK_END in remedy, "with what to look for in it"


def test_rotation_keeps_a_crlf_env_files_own_line_endings(sandbox: Path) -> None:
    """Rotation is the second writer of this file, and the two must not differ.

    Setup reads and writes it with newline translation off, so the ``\\r\\n`` a
    Windows editor left on a line somebody added stays where it is. If rotation
    does not, the same file comes back with every line ending rewritten by the
    command a person runs *because a credential has been exposed* -- and the
    diff they are looking at that day is not the one they wanted.

    Seeded with a block whose markers are CRLF, which is not this block: the
    rewrite genuinely happens, so the assertion is about what survives it rather
    than about a write that never took place.
    """
    sandbox.mkdir(parents=True, mode=0o700)
    keep = b"export MY_OTHER_VAR=keepme\r\n"
    block = env_block(sandbox).replace("\n", "\r\n").encode("utf-8")
    (sandbox / "env").write_bytes(keep + block + b"\r\n")

    _rotate()

    written = (sandbox / "env").read_bytes()
    assert written == keep + env_block(sandbox).encode("utf-8") + b"\r\n"
    assert written.startswith(keep), "their line ending is theirs"


@pytest.mark.skipif(_CANNOT_BE_REFUSED_BY_A_MODE, reason="POSIX permission bits, and not as root")
def test_a_rotation_survives_an_env_file_the_os_will_not_let_it_write(sandbox: Path) -> None:
    """Nothing after the new token is minted is allowed to end the command.

    The ordering is already committed to by the time this file is reached: the
    token on disk has been replaced, the daemon has not been restarted yet, and
    the shells that exported the old value are still holding it. An exception
    here would end `auth rotate` with a traceback where the remedy should be --
    on the command every exposure message in Core tells people to run.

    A read-only file is the ordinary way to arrive: a dotfiles checkout mounted
    read-only, a file another account owns, a full disk. All three reach the
    same ``OSError``, and all three need the same three sentences.
    """
    sandbox.mkdir(parents=True, mode=0o700)
    seeded = env_block(sandbox) + "\nexport MY_OTHER_VAR=keepme\n"
    (sandbox / "env").write_text(seeded, encoding="utf-8")
    (sandbox / "env").chmod(0o400)

    payload = _rotate()

    assert payload["rotated"] is True, "the exposed token is not left in place over a file mode"
    assert (sandbox / "env").read_text(encoding="utf-8") == seeded, "and nothing was half-written"
    remedy = "\n".join(payload["nextSteps"])
    assert str(sandbox / "env") in remedy, "the output names the file that was skipped"
    assert "theurian setup" in remedy, "and what to run once it is repaired"


@pytest.mark.skipif(_CANNOT_BE_REFUSED_BY_A_MODE, reason="POSIX permission bits, and not as root")
def test_the_refusal_names_the_kind_of_failure_and_not_what_the_os_said(sandbox: Path) -> None:
    """SEC-6. This line is printed beside a rotation somebody pastes into a report.

    An ``OSError`` carries whatever the OS put in it -- ``strerror``, the errno,
    and on some platforms a second path that is nobody's business. The class
    name is the part that tells a person which kind of repair they are looking
    at, and it is the only part that is Theurian's own text.
    """
    sandbox.mkdir(parents=True, mode=0o700)
    (sandbox / "env").write_text(env_block(sandbox) + "\n", encoding="utf-8")
    (sandbox / "env").chmod(0o400)

    remedy = "\n".join(_rotate()["nextSteps"])

    assert "PermissionError" in remedy, "which kind of failure it was"
    assert "Permission denied" not in remedy, "but not the sentence the OS wrote"
    assert "Errno" not in remedy


def test_the_env_file_is_private_however_permissive_the_umask_is(sandbox: Path) -> None:
    """SEC-5, and a guarantee that must not depend on whoever is running it.

    The creation mode on the ``open`` is ANDed with the process umask, so
    ``0600`` there is not on its own a promise; the ``chmod`` that follows is
    what closes it. Every other permission assertion in this file runs under
    whatever umask the developer happens to have, which means their *verdict*
    depends on it -- a 0666 creation reads as 0600 under the common ``0o077``.
    This one fixes the umask at its most permissive so the answer is about the
    code.
    """
    previous = os.umask(0o000)
    try:
        _rotate()
    finally:
        os.umask(previous)

    assert (sandbox / "env").stat().st_mode & 0o777 == 0o600
    assert (sandbox / "auth" / TOKEN_KEY).stat().st_mode & 0o777 == 0o600


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
