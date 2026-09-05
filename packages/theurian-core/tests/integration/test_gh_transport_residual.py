"""The residual the pre-spawn check leaves, demonstrated (ADR-0030 clause 4(ii-b)).

Clause 4(ii) is split in two because its halves have different requirements, and
this is the half that **is** skippable:

* **(ii-a) the refusal driver** -- with the fixture in place the adapter refuses
  *before spawning*, so it needs no ``gh`` and is never skipped. It lives in
  ``test_gh_review_provider.py::test_a_planted_transport_override_refuses_before_any_binary_probe``,
  and it is the control.
* **(ii-b) this file** -- with the refusal bypassed, the request leaves through
  the socket. That needs a real ``gh``, so it is skipped where the binary is
  absent or below the version floor, and **the skip is reported** rather than
  counted as a pass.

**The bypass is a test seam and nothing else.** It is a ``monkeypatch`` of the
name the adapter imported, supplied by this file at run time. It is deliberately
**not** a shipped flag or a configuration key: a runtime switch that disables a
security check is a second way to reach the exposure, which would make the
residual worse rather than demonstrate it.

**What this demonstrates, precisely.** With ``http_unix_socket`` in the
configuration directory ``gh`` resolves, a request this adapter believes it
pinned to ``github.com`` leaves through a socket of somebody else's choosing. The
pre-spawn check reduces the accidental single-well-formed-file case; it is not a
control against an adversary, and this is what "not a control" looks like when it
is run rather than argued.

No outbound network: the only destination reached is a unix socket this test
created, and the isolated configuration directory means the operator's real
``gh`` configuration is neither read nor written.
"""

from __future__ import annotations

import contextlib
import pathlib
import re
import shutil
import socket
import subprocess
import tempfile
import threading
from collections.abc import Iterator
from typing import Final

import pytest

from theurian.domain.identifiers import ProjectId
from theurian.domain.review_ingest import ReviewIngestRefusedError
from theurian.infrastructure.github import review_provider
from theurian.infrastructure.github.limits import GH_VERSION_FLOOR, rendered_version
from theurian.infrastructure.github.review_provider import GitHubReviewProvider
from theurian.infrastructure.github.transport_guard import GH_CONFIG_FILE

pytestmark = pytest.mark.integration

PROJECT: Final = ProjectId("demo")
REPOSITORY: Final = "acme/order-service"

#: A token this test hands the isolated configuration so ``gh`` considers itself
#: signed in and actually issues a request. It is not a credential and reaches
#: nothing but the local socket below, which is the whole point: the exposure
#: ADR-0030 records is that an **authenticated** request is captured, and a
#: ``gh`` that refuses to make one demonstrates nothing.
FAKE_TOKEN: Final = "not-a-real-token"  # noqa: S105 - a fixture value, not a credential

_VERSION = re.compile(r"(\d+)\.(\d+)\.(\d+)")


def _installed_version() -> tuple[int, int, int] | None:
    """The operator's ``gh`` version, or ``None`` when there is none to read."""
    binary = shutil.which("gh")
    if binary is None:
        return None
    done = subprocess.run(  # noqa: S603 - a fixed vector, resolved absolute, no shell
        [binary, "--version"], capture_output=True, text=True, timeout=30, check=False
    )
    match = _VERSION.search(done.stdout)
    return (int(match[1]), int(match[2]), int(match[3])) if match else None


_INSTALLED = _installed_version()

#: Why this half is skipped when it is, stated so a reader of a green run knows
#: which of the two halves of clause 4(ii) actually ran.
_SKIP_REASON: Final = (
    f"ADR-0030 clause 4(ii-b) needs a real `gh` at or above the recorded floor "
    f"{rendered_version(GH_VERSION_FLOOR)}; this machine has "
    f"{rendered_version(_INSTALLED) if _INSTALLED else 'none'}. The control half, "
    f"(ii-a), needs no binary and is never skipped: "
    f"test_gh_review_provider.py::test_a_planted_transport_override_refuses_before_"
    f"any_binary_probe."
)


class SocketWatch:
    """A unix socket that records whether anything connected to it."""

    def __init__(self, path: pathlib.Path) -> None:
        self.path = path
        self.connections = 0
        self._listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._listener.bind(str(path))
        self._listener.listen(8)
        self._listener.settimeout(0.5)
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def _serve(self) -> None:
        while not self._stop.is_set():
            try:
                connection, _ = self._listener.accept()
            except (TimeoutError, OSError):
                continue
            self.connections += 1
            with contextlib.suppress(OSError):
                connection.recv(4096)
                connection.close()

    def close(self) -> None:
        self._stop.set()
        self._thread.join(timeout=5)
        self._listener.close()


@pytest.fixture
def socket_watch() -> Iterator[SocketWatch]:
    """A short-pathed unix socket, because ``AF_UNIX`` bounds the path at ~104 bytes.

    ``tmp_path`` is far too long for that on macOS, where it lives under
    ``/private/var/folders/...``. This is the one fixture in the suite that
    cannot use it.
    """
    directory = pathlib.Path(tempfile.mkdtemp(prefix="thr-", dir="/tmp"))
    watch = SocketWatch(directory / "gh.sock")
    try:
        yield watch
    finally:
        watch.close()
        with contextlib.suppress(OSError):
            watch.path.unlink()
        with contextlib.suppress(OSError):
            directory.rmdir()


def _provider_over(tmp_path: pathlib.Path, socket_path: pathlib.Path) -> GitHubReviewProvider:
    """A provider pointed at an isolated ``gh`` configuration that plants the socket.

    The configuration directory is this test's, so the operator's real one is
    neither read nor written; ``hosts.yml`` carries the fixture token so ``gh``
    issues an authenticated request rather than declining to make one.
    """
    config_dir = tmp_path / "gh-config"
    config_dir.mkdir()
    (config_dir / GH_CONFIG_FILE).write_text(f"http_unix_socket: {socket_path}\n", encoding="utf-8")
    (config_dir / "hosts.yml").write_text(
        f"github.com:\n    oauth_token: {FAKE_TOKEN}\n    user: nobody\n", encoding="utf-8"
    )

    knowledge = tmp_path / "project" / ".theurian"
    knowledge.mkdir(parents=True)
    (knowledge / "config.yaml").write_text(
        f"apiVersion: theurian.dev/v1\nproviders:\n  review:\n    repositories:\n"
        f"      - {REPOSITORY}\n",
        encoding="utf-8",
    )
    return GitHubReviewProvider(
        project_root=tmp_path / "project",
        config_file=knowledge / "config.yaml",
        parent_environment={
            "GH_CONFIG_DIR": str(config_dir),
            "HOME": str(tmp_path / "home"),
        },
        binary=pathlib.Path(shutil.which("gh") or "gh"),
    )


@pytest.mark.skipif(_INSTALLED is None or _INSTALLED < GH_VERSION_FLOOR, reason=_SKIP_REASON)
@pytest.mark.asyncio
async def test_with_the_refusal_bypassed_the_request_leaves_through_the_socket(
    tmp_path: pathlib.Path, socket_watch: SocketWatch, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ADR-0030 runs D-F, re-run as a control: this is what the check does not close.

    Everything the design pins is in place -- ``--hostname github.com``, the
    constructed environment with no ``GH_HOST`` and no proxy variable, an
    argument vector with no URL in it -- and the request still goes to a
    destination the operator's own configuration file chose.
    """
    # The seam: the name the adapter imported, replaced for the duration of this
    # test. Nothing shipped can reach it.
    monkeypatch.setattr(review_provider, "refuse_transport_overrides", lambda _parent: None)
    provider = _provider_over(tmp_path, socket_watch.path)

    with pytest.raises(ReviewIngestRefusedError):
        await provider.list_pull_requests(PROJECT, REPOSITORY)

    assert socket_watch.connections > 0, (
        "the request did not reach the planted socket, so this test demonstrates "
        "nothing. Either `gh` stopped honouring `http_unix_socket` -- in which "
        "case ADR-0030's runs D-F need re-taking and the recorded residual is "
        "narrower than it says -- or the refusal seam did not open."
    )


@pytest.mark.skipif(_INSTALLED is None or _INSTALLED < GH_VERSION_FLOOR, reason=_SKIP_REASON)
@pytest.mark.asyncio
async def test_without_the_seam_the_same_fixture_reaches_the_socket_zero_times(
    tmp_path: pathlib.Path, socket_watch: SocketWatch
) -> None:
    """The negative control, so the zero above is not the number a broken watch returns.

    Same fixture, same binary, seam closed. A count of zero only means something
    beside a count that is not zero on the same instrument -- otherwise a socket
    watch that never records anything reports its own answer as a clean result,
    in both tests.
    """
    provider = _provider_over(tmp_path, socket_watch.path)

    with pytest.raises(ReviewIngestRefusedError) as raised:
        await provider.list_pull_requests(PROJECT, REPOSITORY)

    assert raised.value.grade.value == "transport-override-configured"
    assert socket_watch.connections == 0
