"""The `secret-scan` job verifies the gitleaks download before running it (#108).

Why this file exists
---------------------
Before this fix, `.github/workflows/security.yml`'s "Install gitleaks" step
pinned `GITLEAKS_VERSION` but not the bytes it downloaded: it `curl`ed a
tarball straight into `tar` and then `sudo install`ed the result onto PATH,
all inside a job holding the workflow's token. A compromised release asset or
a MITM on the download would run arbitrary code with that token, and the
version pin would not have caught it -- the version string in the URL says
nothing about what bytes came back.

The fix adds `GITLEAKS_SHA256`, a digest pinned alongside `GITLEAKS_VERSION`
in the same `env:` block, and a `sha256sum -c` check between the `curl` and
the `tar`/`install`. This file is the driving check for that: it proves the
guard actually rejects a tampered artefact, not just that it accepts a
correct one -- a check that only ever sees matching bytes would pass even if
someone deleted the verification line's practical effect (e.g. redirecting
stderr, or using `-c` against the wrong file).

What is asserted, and what is not
-----------------------------------
- The digest is present, 64 lowercase hex characters (a sha256 digest), and
  lives in the same `env:` block as the version, so the two are visibly
  paired for whoever bumps one.
- The verification line runs after the `curl` and before the `tar`/`install`
  steps -- verifying after installation is not a guard.
- The workflow's *own* `sha256sum -c` line -- extracted from the parsed YAML,
  not reimplemented here -- rejects a file whose digest does not match
  `GITLEAKS_SHA256` and accepts one whose digest does. This is what proves a
  version bump that forgets to update the digest fails closed rather than
  installing unverified bytes: the same line runs either way, and only the
  digest changes.

This file does not assert anything about what gitleaks itself scans, or about
the pinning-checker in the `pinning` job (deliberately out of scope, #58's
MEDIUM half) -- both are unrelated to the supply-chain gap this closes.
"""

from __future__ import annotations

import hashlib
import pathlib
import re
import subprocess
from typing import Any, cast

import pytest
import yaml

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "security.yml"

_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")


def _install_gitleaks_step() -> dict[str, Any]:
    document = cast(dict[str, Any], yaml.safe_load(WORKFLOW.read_text(encoding="utf-8")))
    steps = document["jobs"]["secret-scan"]["steps"]
    for step in steps:
        if step.get("name") == "Install gitleaks":
            return cast(dict[str, Any], step)
    raise AssertionError(f"no 'Install gitleaks' step found in {WORKFLOW}")


def _verification_line(script: str) -> str:
    """Pull the `sha256sum -c` line out of the step's own run script.

    Matching the real line -- rather than writing an equivalent one here --
    is what makes the pass/fail assertions below a test of the workflow
    itself, not of a copy that could silently drift from it.
    """
    for line in script.splitlines():
        if "sha256sum -c" in line:
            return line.strip()
    raise AssertionError(f"no 'sha256sum -c' line found in the install script:\n{script}")


def test_digest_is_pinned_alongside_the_version() -> None:
    env = _install_gitleaks_step().get("env", {})
    assert env.get("GITLEAKS_VERSION"), "GITLEAKS_VERSION must be set"
    digest = env.get("GITLEAKS_SHA256")
    assert digest is not None, (
        "GITLEAKS_SHA256 must be set in the same env: block as GITLEAKS_VERSION, "
        "so a version bump that forgets to update it is visibly incomplete."
    )
    assert _DIGEST_RE.fullmatch(digest), (
        f"GITLEAKS_SHA256 must be a 64-character lowercase sha256 hex digest, got {digest!r}"
    )


def test_verification_runs_after_download_and_before_install() -> None:
    script = _install_gitleaks_step()["run"]
    curl_pos = script.index("curl ")
    verify_pos = script.index("sha256sum -c")
    tar_pos = script.index("tar -xzf")
    install_pos = script.index("sudo install")
    assert curl_pos < verify_pos < tar_pos < install_pos, (
        "the sha256sum check must run after the curl download and before "
        "tar/install -- verifying after installation guards nothing"
    )


@pytest.mark.parametrize(
    ("tampered", "expected_returncode"),
    [
        pytest.param(False, 0, id="matching-digest-passes"),
        pytest.param(True, 1, id="tampered-artefact-fails"),
    ],
)
def test_workflow_verification_line_rejects_a_tampered_artefact(
    tmp_path: pathlib.Path, tampered: bool, expected_returncode: int
) -> None:
    """Run the workflow's own `sha256sum -c` line -- the exact text parsed out
    of security.yml, not a reimplementation of it -- against a downloaded
    artefact whose digest either does or does not match `GITLEAKS_SHA256`.

    Sha256 is preimage-resistant, so this cannot construct bytes that hash to
    the *real* pinned digest without downloading the real release; instead it
    pins the digest to the bytes on disk (the "legitimate download" case) or
    to a digest those bytes do not hash to (the "substituted artefact"
    case), and asserts the workflow's own check tells them apart. That is
    AC-1's driving check: the guard must fail a wrong digest, not merely
    succeed on a right one -- a check that only ever sees a match would stay
    green even if the verification line were silently defanged.
    """
    step = _install_gitleaks_step()
    verify_command = _verification_line(step["run"])

    downloaded_bytes = b"a stand-in for a downloaded gitleaks release tarball\n"
    archive = tmp_path / "gitleaks.tar.gz"
    archive.write_bytes(downloaded_bytes)

    actual_digest = hashlib.sha256(downloaded_bytes).hexdigest()
    if tampered:
        # Stands in for a substituted or MITM'd artefact landing under the
        # pinned digest: the bytes on disk do not hash to it.
        pinned_digest = "0" * 64
        assert pinned_digest != actual_digest
    else:
        pinned_digest = actual_digest

    result = subprocess.run(  # noqa: S603
        ["bash", "-c", verify_command],  # noqa: S607
        cwd=tmp_path,
        # `sha256sum` is under /usr/bin on the Ubuntu runners this workflow
        # actually targets; /sbin covers the macOS coreutils-compat shim used
        # when this test runs on a developer machine.
        env={"PATH": "/usr/bin:/bin:/usr/local/bin:/sbin", "GITLEAKS_SHA256": pinned_digest},
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == expected_returncode, (
        f"sha256sum -c exited {result.returncode}, expected {expected_returncode}\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )
