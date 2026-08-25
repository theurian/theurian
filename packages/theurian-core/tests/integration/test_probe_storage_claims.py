"""What `data-directory` and `token-storage` claim about what is on disk (#87).

Two probes, one defect: **the summary states a fact the check never
established.**

- ``data-directory`` asked ``exists()`` and then ``is_world_accessible()``, and
  a *regular file* at the data directory's path answers the first true and the
  second whatever its mode says. A 0600 file therefore satisfied a step whose
  summary is "exists with private permissions", and every step after it went on
  to write inside a directory that is not there -- while the report said the
  machine was converged.
- ``token-storage`` checked ``st_mode & 0o077 == 0`` and reported "The token is
  stored 0600 inside a 0700 directory". A 0400 token satisfies the check and
  falsifies the sentence. The sentence is what an operator quotes in a security
  review; the check is what is true.

Both are the class ``tests/integration/test_setup_probe_assertions.py`` opens:
a probe whose stated conclusion is wider than its own predicate. A ``satisfied``
verdict is the one nobody re-reads.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fakes.setup import FakeMcpConfig, FakeService
from setup_migrations import unchecked_migrations

from theurian.application.setup_context import SetupContext
from theurian.application.setup_steps import probe_data_directory, probe_token_storage
from theurian.domain.setup import StepStatus
from theurian.infrastructure.claude.mcp_config import ConnectionSpec
from theurian.infrastructure.secrets.file_store import FileSecretStore

pytestmark = pytest.mark.integration

#: The file `FileSecretStore` keeps the local access token in.
_TOKEN_FILENAME = "mcp-token"  # noqa: S105 - a filename, not a credential


def _context(tmp_path: Path, data_dir: Path) -> SetupContext:
    return SetupContext(
        home=tmp_path / "home",
        data_dir=data_dir,
        port=7419,
        project_root=None,
        connection=ConnectionSpec(port=7419),
        mcp_config=FakeMcpConfig(),
        secrets=FileSecretStore(data_dir),
        health=lambda: None,
        service=FakeService(),
        executable="",
        check_migrations=unchecked_migrations,
    )


# -- data-directory ----------------------------------------------------------


def test_a_regular_file_where_the_data_directory_belongs_is_a_conflict(tmp_path: Path) -> None:
    """The measured face: a 0600 file reported as a private *directory* (#87).

    ``exists()`` is true of a file, and 0600 has no group or other bits, so both
    of the old predicates passed. What follows a wrong ``satisfied`` here is not
    a cosmetic wrong sentence: ``token``, ``token-storage`` and ``env-file`` all
    write *inside* this path, and the run reports CONVERGED.

    CONFLICTING rather than MISSING, because setup replaces nothing it did not
    create (SEC-18): the remedy is a person moving their file aside, and
    ``missing`` is the status that would have setup act.
    """
    data_dir = tmp_path / "data"
    data_dir.write_text("not a directory\n", encoding="utf-8")
    data_dir.chmod(0o600)

    step = probe_data_directory(_context(tmp_path, data_dir))

    assert step.status is StepStatus.CONFLICTING
    assert step.summary == f"{data_dir} exists but is not a directory."
    assert step.detail == (
        "Setup never replaces a file it did not create. Move it aside; setup then "
        "creates the directory with mode 0700."
    )
    assert data_dir.read_text(encoding="utf-8") == "not a directory\n", (
        "a probe reports; it does not touch what it found"
    )


def test_a_world_readable_file_at_the_data_directory_is_reported_as_the_file_it_is(
    tmp_path: Path,
) -> None:
    """Order, and the reason the new arm goes first.

    A 0666 file answers the world-accessible question true, so the mode check
    reaches it first and reports "is mode 0666, readable by other users" beside
    "Tighten it to 0700" -- a remedy that would leave a *file* at the path with
    a tidier mode and nothing else fixed. What the reader has to be told is that
    the thing is not a directory at all.
    """
    data_dir = tmp_path / "data"
    data_dir.write_text("not a directory\n", encoding="utf-8")
    data_dir.chmod(0o666)

    step = probe_data_directory(_context(tmp_path, data_dir))

    assert step.status is StepStatus.CONFLICTING
    assert step.summary == f"{data_dir} exists but is not a directory."


def test_a_private_directory_is_what_satisfies_the_step(tmp_path: Path) -> None:
    """The other half, or every test above passes by always conflicting."""
    data_dir = tmp_path / "data"
    data_dir.mkdir(mode=0o700)

    step = probe_data_directory(_context(tmp_path, data_dir))

    assert step.status is StepStatus.SATISFIED
    assert step.summary == f"{data_dir} exists with private permissions."


def test_a_data_directory_that_does_not_exist_is_missing(tmp_path: Path) -> None:
    """Unchanged, and pinned: the new arm must not swallow the absent case.

    ``missing`` here is correct precisely because there is nothing of anybody
    else's to replace, which is what makes this the one arm setup may act on.
    """
    data_dir = tmp_path / "data"

    step = probe_data_directory(_context(tmp_path, data_dir))

    assert step.status is StepStatus.MISSING
    assert step.summary == f"{data_dir} does not exist."
    assert step.action == f"Create {data_dir} with mode 0700."


def test_a_directory_other_users_can_read_is_missing_with_its_mode_named(
    tmp_path: Path,
) -> None:
    """The check the new arm is inserted in front of, pinned so it survives.

    ``missing`` and not ``conflicting``: a mode is something setup created and
    may tighten, so this one it acts on.
    """
    data_dir = tmp_path / "data"
    data_dir.mkdir(mode=0o755)

    step = probe_data_directory(_context(tmp_path, data_dir))

    assert step.status is StepStatus.MISSING
    assert step.summary == f"{data_dir} is mode 0755, readable by other users."
    assert step.action == f"Tighten {data_dir} to 0700."


# -- token-storage -----------------------------------------------------------


def _stored_token(data_dir: Path, *, token_mode: int, directory_mode: int = 0o700) -> Path:
    auth = data_dir / "auth"
    auth.mkdir(parents=True, mode=0o700)
    token = auth / _TOKEN_FILENAME
    token.write_text("t" * 64, encoding="utf-8")
    token.chmod(token_mode)
    auth.chmod(directory_mode)
    return token


def test_a_read_only_token_is_reported_by_what_was_checked(tmp_path: Path) -> None:
    """0400 satisfies the check and falsifies the old sentence (#87).

    The predicate is ``st_mode & 0o077 == 0`` -- nothing may reach the file but
    its owner -- and 0400 passes it. "Stored 0600 inside a 0700 directory" is a
    *different* claim, and it was published for a file whose mode had not been
    compared to 0600 at all. An operator pasting that line into a review is
    quoting a mode Theurian never read.

    ``0600`` is asserted absent from the summary rather than the exact sentence
    being asserted alone, because a sentence that names a mode it did not check
    is the defect regardless of which mode it names.
    """
    data_dir = tmp_path / "data"
    _stored_token(data_dir, token_mode=0o400)

    step = probe_token_storage(_context(tmp_path, data_dir))

    assert step.status is StepStatus.SATISFIED
    assert step.summary == "The token is not accessible to other local users."
    assert "0600" not in step.summary


def test_a_token_stored_the_way_setup_writes_it_is_satisfied(tmp_path: Path) -> None:
    """0600 in a 0700 directory -- the state `apply_token` leaves behind.

    Same sentence as the 0400 case above, deliberately: the step reports what it
    checked, and it checked the same thing both times.
    """
    data_dir = tmp_path / "data"
    _stored_token(data_dir, token_mode=0o600)

    step = probe_token_storage(_context(tmp_path, data_dir))

    assert step.status is StepStatus.SATISFIED
    assert step.summary == "The token is not accessible to other local users."


def test_a_group_readable_token_is_a_conflict_that_names_rotation(tmp_path: Path) -> None:
    """SEC-4, pinned: 0640 is caught, and tightening the mode is not the remedy.

    Once a credential has been exposed, a `chmod` restores the permissions and
    not the secrecy -- so the detail names `theurian auth rotate` rather than
    offering to fix the mode.
    """
    data_dir = tmp_path / "data"
    token = _stored_token(data_dir, token_mode=0o640)

    step = probe_token_storage(_context(tmp_path, data_dir))

    assert step.status is StepStatus.CONFLICTING
    assert step.summary == "The token is readable by other local users."
    assert f"{token} is mode 0640." in step.detail
    assert "`theurian auth rotate`" in step.detail


def test_a_private_token_inside_a_readable_directory_is_a_conflict(tmp_path: Path) -> None:
    """The other half of the check: the directory is reachable too.

    A 0600 file inside a 0750 directory is readable by the group, and a probe
    that looked only at the file would call that private.
    """
    data_dir = tmp_path / "data"
    _stored_token(data_dir, token_mode=0o600, directory_mode=0o750)

    step = probe_token_storage(_context(tmp_path, data_dir))

    assert step.status is StepStatus.CONFLICTING


def test_no_token_file_yet_is_missing(tmp_path: Path) -> None:
    """The state a fresh machine is in, and the one arm setup acts on."""
    data_dir = tmp_path / "data"

    step = probe_token_storage(_context(tmp_path, data_dir))

    assert step.status is StepStatus.MISSING
    assert step.summary == "The token file does not exist yet."
