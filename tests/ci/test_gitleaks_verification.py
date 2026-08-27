"""The `secret-scan` job aborts before installing an unverified gitleaks (#108).

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
the `tar`/`install`.

What this file proves, and what it deliberately does not assume
-----------------------------------------------------------------
Proving that `sha256sum -c` merely *exits* non-zero on a mismatch is not
enough: whether that non-zero exit actually stops `tar`/`sudo install` from
running depends on the shell aborting on error, and that is a property of
*how the step is invoked*, not of the `sha256sum -c` line itself. GitHub's
default shell for a Linux `run:` step already invokes
`bash --noprofile --norc -eo pipefail`, but a script that relies on that
default -- rather than declaring it -- keeps the fail-closed guarantee
outside the file that is supposed to hold it: a future `shell:` override or a
stray `set +e` would let a failed check fall through to install while every
test that only inspects the `sha256sum -c` line's own exit code, or its
position in the script text, stays green. The fix pins `set -euo pipefail`
as the first line of the step's own `run:` block for exactly this reason, and
this file's driving test runs the step's *whole* script -- not just its
verification line -- and asserts the install command is never reached on a
mismatch. That is what "aborts before install" means, and it is the only
thing that actually forecloses a tampered download from being installed.

- `test_digest_is_pinned_alongside_the_version` -- the digest is present, 64
  lowercase hex characters, and lives in the same `env:` block as the
  version, so the two are visibly paired for whoever bumps one.
- `test_run_block_pins_fail_closed_shell_options` -- `set -euo pipefail` is
  the first executable line of the step, ahead of the download.
- `test_verification_runs_after_download_and_before_install` -- the
  verification line sits after `curl` and before `tar`/`install` in the
  script text.
- `test_workflow_verification_line_rejects_a_tampered_artefact` -- the
  workflow's own `sha256sum -c` line, run in isolation, exits non-zero on a
  digest mismatch and zero on a match.
- `test_whole_step_aborts_before_install_on_a_mismatched_digest` -- the
  driving check. It runs the step's entire `run:` script (curl, tar, sudo,
  gitleaks all stubbed so nothing touches the network or a real system path)
  and asserts that a mismatched digest leaves an "install was reached"
  marker file **never created**, while a matching digest reaches it. This is
  what a version bump that forgets to update the digest is caught by: not a
  green check line sitting next to bytes that got installed anyway.

This file does not assert anything about what gitleaks itself scans, or about
the pinning-checker in the `pinning` job (deliberately out of scope, #58's
MEDIUM half) -- both are unrelated to the supply-chain gap this closes.
"""

from __future__ import annotations

import hashlib
import os
import pathlib
import re
import shutil
import subprocess
from typing import Any, cast

import pytest
import yaml

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "security.yml"

_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")

# The install step's curl/tar/sudo/gitleaks invocations are replaced by these
# shims so the whole-step test below never touches the network or a real
# system path; each shim is a plain shell script written to a directory
# prepended to PATH ahead of the ambient one (FIX 2: inheriting the ambient
# PATH, rather than a hardcoded one, is what makes this test pass on a
# developer machine whose sha256sum/coreutils live somewhere this file does
# not otherwise know about, e.g. Homebrew's /opt/homebrew/bin on Apple
# Silicon).
_CURL_STUB = """#!/bin/bash
set -euo pipefail
outfile=""
prev=""
for arg in "$@"; do
  if [ "$prev" = "-o" ]; then
    outfile="$arg"
  fi
  prev="$arg"
done
printf '%s' "$CURL_STUB_BYTES" > "$outfile"
"""

_TAR_STUB = """#!/bin/bash
set -euo pipefail
# Stands in for extracting a "gitleaks" member from the (stubbed) tarball.
touch gitleaks
chmod +x gitleaks
"""

_SUDO_STUB = """#!/bin/bash
set -euo pipefail
# Stands in for `sudo install ...`: reaching this line at all is the thing
# under test, so its only job is to leave evidence that it ran.
touch "$INSTALL_MARKER"
"""

_GITLEAKS_STUB = """#!/bin/bash
echo "gitleaks version 8.30.0-stub"
"""


def _install_gitleaks_step() -> dict[str, Any]:
    document = cast(dict[str, Any], yaml.safe_load(WORKFLOW.read_text(encoding="utf-8")))
    steps = document["jobs"]["secret-scan"]["steps"]
    for step in steps:
        if step.get("name") == "Install gitleaks":
            return cast(dict[str, Any], step)
    raise AssertionError(f"no 'Install gitleaks' step found in {WORKFLOW}")


def _drop_full_line_comments(script: str) -> str:
    """Strip lines that are pure `#` comments before any substring or
    position search below.

    The step's own comments describe the mechanism in prose -- including the
    literal text "sha256sum -c" -- so a plain `in`/`.index()` search over the
    whole script can match the comment explaining the check instead of the
    check itself. None of the lines this file locates carry a trailing
    inline comment, so dropping only whole-comment lines does not risk
    cutting real content.
    """
    return "\n".join(line for line in script.splitlines() if not line.strip().startswith("#"))


def _verification_line(script: str) -> str:
    """Pull the `sha256sum -c` line out of the step's own run script.

    Matching the real line -- rather than writing an equivalent one here --
    is what makes the pass/fail assertions below a test of the workflow
    itself, not of a copy that could silently drift from it.
    """
    for line in _drop_full_line_comments(script).splitlines():
        if "sha256sum -c" in line:
            return line.strip()
    raise AssertionError(f"no 'sha256sum -c' line found in the install script:\n{script}")


def _write_executable(path: pathlib.Path, script: str) -> None:
    path.write_text(script)
    path.chmod(0o755)


def _sha256sum_available() -> bool:
    return shutil.which("sha256sum") is not None


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


def test_run_block_pins_fail_closed_shell_options() -> None:
    """`set -euo pipefail` must be declared in the step itself, not left to
    GitHub's default shell invocation -- a `shell:` override or a stray
    `set +e` added later would otherwise silently defeat the abort that
    `test_whole_step_aborts_before_install_on_a_mismatched_digest` proves.
    """
    script = _drop_full_line_comments(_install_gitleaks_step()["run"])
    lines = [line for line in script.splitlines() if line.strip()]
    assert lines, "the install step's run: block is empty"
    assert lines[0].strip() == "set -euo pipefail", (
        "the first executable line of the Install gitleaks step must be "
        f"'set -euo pipefail', got {lines[0]!r}"
    )


def test_verification_runs_after_download_and_before_install() -> None:
    script = _drop_full_line_comments(_install_gitleaks_step()["run"])
    curl_pos = script.index("curl ")
    verify_pos = script.index("sha256sum -c")
    tar_pos = script.index("tar -xzf")
    install_pos = script.index("sudo install")
    assert curl_pos < verify_pos < tar_pos < install_pos, (
        "the sha256sum check must run after the curl download and before "
        "tar/install -- verifying after installation guards nothing"
    )


@pytest.mark.skipif(not _sha256sum_available(), reason="sha256sum not found on PATH")
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
    case), and asserts the workflow's own check tells them apart. This is
    necessary but not sufficient for the fix's claim -- it shows the check
    line itself fails closed, not that the step around it does; see
    `test_whole_step_aborts_before_install_on_a_mismatched_digest` for that.
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
        # Inherit the ambient PATH rather than a hardcoded one: sha256sum's
        # location varies by machine (e.g. Homebrew's /opt/homebrew/bin on
        # Apple Silicon vs. /usr/bin on the Ubuntu runners this workflow
        # actually targets), and a hardcoded PATH missing it turns into a
        # spurious "command not found" rather than a real pass/fail signal.
        env={**os.environ, "GITLEAKS_SHA256": pinned_digest},
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == expected_returncode, (
        f"sha256sum -c exited {result.returncode}, expected {expected_returncode}\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )


@pytest.mark.skipif(not _sha256sum_available(), reason="sha256sum not found on PATH")
@pytest.mark.parametrize(
    ("tampered", "install_reached"),
    [
        pytest.param(False, True, id="matching-digest-reaches-install"),
        pytest.param(True, False, id="mismatched-digest-aborts-before-install"),
    ],
)
def test_whole_step_aborts_before_install_on_a_mismatched_digest(
    tmp_path: pathlib.Path, tampered: bool, install_reached: bool
) -> None:
    """This is AC-1's driving check. It runs the "Install gitleaks" step's
    entire `run:` script -- not just its `sha256sum -c` line -- under bash,
    with `curl`, `tar`, `sudo`, and `gitleaks` all replaced by shims that
    touch no network and no real system path.

    `sudo`'s shim is the observable: it exists only to leave a marker file
    behind, because reaching it at all is exactly what a fail-closed
    `sha256sum -c` failure must prevent. A mismatched digest must leave that
    marker never created; a matching one must reach it. Asserting only that
    `sha256sum -c` exits non-zero (the previous test) does not prove this --
    a script invoked without `-e`, or with a later `set +e`, would still let
    a non-zero exit fall through to `tar`/`sudo install` while that test
    stayed green. This test would have failed before `set -euo pipefail` was
    added to the step, because bash does not abort on a failed pipeline by
    default when a script is simply handed to it.
    """
    step = _install_gitleaks_step()
    script_path = tmp_path / "install_gitleaks.sh"
    script_path.write_text(step["run"])

    stub_dir = tmp_path / "stub-bin"
    stub_dir.mkdir()
    _write_executable(stub_dir / "curl", _CURL_STUB)
    _write_executable(stub_dir / "tar", _TAR_STUB)
    _write_executable(stub_dir / "sudo", _SUDO_STUB)
    _write_executable(stub_dir / "gitleaks", _GITLEAKS_STUB)

    workdir = tmp_path / "work"
    workdir.mkdir()
    marker = tmp_path / "install-reached"

    stub_tarball_bytes = b"stub gitleaks tarball bytes for the whole-step abort test\n"
    real_digest = hashlib.sha256(stub_tarball_bytes).hexdigest()
    if tampered:
        digest = "0" * 64
        assert digest != real_digest
    else:
        digest = real_digest

    result = subprocess.run(  # noqa: S603
        ["bash", str(script_path)],  # noqa: S607
        cwd=workdir,
        env={
            **os.environ,
            # The stub dir goes first so curl/tar/sudo/gitleaks resolve to
            # the shims above rather than whatever the ambient PATH (kept
            # for sha256sum's sake, per FIX 2) would otherwise find.
            "PATH": f"{stub_dir}{os.pathsep}{os.environ.get('PATH', '')}",
            "GITLEAKS_VERSION": "8.30.0",
            "GITLEAKS_SHA256": digest,
            "CURL_STUB_BYTES": stub_tarball_bytes.decode(),
            "INSTALL_MARKER": str(marker),
        },
        capture_output=True,
        text=True,
        check=False,
    )

    assert marker.exists() == install_reached, (
        f"install marker exists={marker.exists()}, expected {install_reached}\n"
        f"returncode={result.returncode}\nstdout={result.stdout}\nstderr={result.stderr}"
    )
    if tampered:
        assert result.returncode != 0, (
            "a mismatched digest must abort the whole step non-zero, not "
            f"just the check line\nstdout={result.stdout}\nstderr={result.stderr}"
        )
    else:
        assert result.returncode == 0, (
            f"a matching digest must let the step complete\n"
            f"stdout={result.stdout}\nstderr={result.stderr}"
        )
