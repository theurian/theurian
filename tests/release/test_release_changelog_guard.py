"""The "Require a changelog section written for this version" guard, in
`release-core.yml`'s `build` job.

Why this file exists
--------------------
The guard extracted the `## [version]` section and refused a tag whose section
was missing or empty. It never looked at `## [Unreleased]`, so a CHANGELOG
holding a non-empty `[Unreleased]` *and* a non-empty version section passed
(exit 0): whatever sat under `[Unreleased]` shipped in the tag and the sdist
while the GitHub release body -- generated from the version section alone --
announced it nowhere. This happened on `core-v0.1.0.dev0` (#79).

How the step gets here
-----------------------
Extracted from the workflow YAML structurally (by the step's `name:`, not by
position), never retyped, and run under real `bash` and `python3` -- the same
interpreters the runner uses. The one thing a raw extraction cannot reproduce
is `${{ steps.resolve.outputs.version }}`: GitHub substitutes that expression
into the script *as text* before any shell sees it, so this file performs the
same textual substitution before running the script, rather than reimplementing
the guard's regex logic in Python. `_materialize` asserts the substitution
actually replaced something, so a future edit that stops passing the version
this way is reported here rather than silently testing stale text.

Not covered
-----------
* The version-section checks themselves (missing section, empty section).
  Untouched by #79's fix and not the guard family this file exists to pin.
* The release-notes `print(body)` output beyond "the guard let this through".
"""

from __future__ import annotations

import pathlib
import shlex
import shutil
import subprocess
import sys
from dataclasses import dataclass
from typing import Any, cast

import pytest
import yaml

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "release-core.yml"

#: Real `bash`: the guard's shipped text, run under the same interpreter the
#: runner uses. Resolved to an absolute path so `subprocess.run` never
#: searches `PATH` at call time (ruff S607).
BASH = shutil.which("bash")

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(BASH is None, reason="the guard's shipped text is a bash script"),
]

#: The literal GitHub Actions expression the guard's script embeds directly in
#: its `run:` text (not behind an `env:` block, unlike the publication-guard
#: steps this repository already tests). GitHub replaces it with the resolved
#: version as plain text before bash ever runs the script.
VERSION_EXPRESSION = "${{ steps.resolve.outputs.version }}"

GUARD_STEP_NAME = "Require a changelog section written for this version"


def _workflow() -> dict[str, Any]:
    return cast(dict[str, Any], yaml.safe_load(WORKFLOW.read_text(encoding="utf-8")))


def _guard_script() -> str:
    """The guard's `run:` text, located by its step name, not its position."""
    for step in _workflow()["jobs"]["build"]["steps"]:
        if step.get("name") == GUARD_STEP_NAME:
            return str(step["run"])
    raise AssertionError(
        f"build has no step named {GUARD_STEP_NAME!r}; "
        "this file locates the changelog guard structurally"
    )


GUARD_SCRIPT = _guard_script()


def _materialize(version: str) -> str:
    """The shipped script with GitHub's own substitution applied.

    A plain text replacement, matching what the Actions runner does before
    invoking the shell -- not a reimplementation of the guard's regex.
    """
    materialized = GUARD_SCRIPT.replace(VERSION_EXPRESSION, shlex.quote(version))
    assert materialized != GUARD_SCRIPT, (
        f"{VERSION_EXPRESSION!r} was not found in the guard's script; "
        "the substitution point moved and this file needs updating with it"
    )
    return materialized


@dataclass(frozen=True)
class GuardRun:
    exit_code: int
    stdout: str
    stderr: str

    @property
    def succeeded(self) -> bool:
        return self.exit_code == 0


def _run_guard(tmp_path: pathlib.Path, changelog: str, version: str) -> GuardRun:
    """Run the shipped step text against a real CHANGELOG.md on disk.

    The script reads `packages/theurian-core/CHANGELOG.md` relative to its
    working directory, so that path is built under `tmp_path` rather than
    pointed at the real file -- the guard's own behaviour must not depend on
    which CHANGELOG this repository happens to carry today.
    """
    changelog_path = tmp_path / "packages" / "theurian-core" / "CHANGELOG.md"
    changelog_path.parent.mkdir(parents=True, exist_ok=True)
    changelog_path.write_text(changelog, encoding="utf-8")

    script_path = tmp_path / "step.sh"
    script_path.write_text(_materialize(version), encoding="utf-8")

    assert BASH is not None
    completed = subprocess.run(  # noqa: S603 - fixed argv, no shell
        [BASH, "-e", str(script_path)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    return GuardRun(
        exit_code=completed.returncode, stdout=completed.stdout, stderr=completed.stderr
    )


#: A version section the guard's existing checks accept on their own, so every
#: case below isolates the `[Unreleased]` behaviour rather than tripping over
#: the checks #79 does not touch.
_VALID_VERSION_SECTION = """\
## [0.1.0.dev1] - 2026-08-28

### Fixed

- A fix that was moved out of [Unreleased] before tagging.
"""


def _changelog(unreleased_body: str) -> str:
    return f"""\
# Changelog

## [Unreleased]

{unreleased_body}
{_VALID_VERSION_SECTION}"""


@pytest.mark.skipif(
    sys.platform == "win32", reason="the step is bash, run only on the runner's OS family"
)
def test_the_guard_refuses_a_non_empty_unreleased_section_at_tag_time(
    tmp_path: pathlib.Path,
) -> None:
    """AC-4(a), and the reproduction of #79: a real Security fix left under
    [Unreleased] while a valid, non-empty version section sits right below it.

    Before the fix this guard checked only the version section and exited 0,
    which is exactly how the change under [Unreleased] shipped in
    `core-v0.1.0.dev0`'s tag and sdist while the GitHub release announced
    nothing about it.
    """
    changelog = _changelog("### Security\n\n- A fix nobody moved before tagging.\n")

    guard = _run_guard(tmp_path, changelog, "0.1.0.dev1")

    assert not guard.succeeded, (guard.stdout, guard.stderr)
    assert "[Unreleased] is not empty" in guard.stderr


@pytest.mark.parametrize(
    "unreleased_body",
    ["", "Nothing yet."],
    ids=["blank", "placeholder"],
)
def test_the_guard_passes_when_unreleased_holds_only_the_allowed_placeholder(
    tmp_path: pathlib.Path, unreleased_body: str
) -> None:
    """AC-4(b): an [Unreleased] section left blank, or holding exactly the
    "Nothing yet." placeholder #77 leaves behind once emptied, must not block
    a release whose version section is itself valid.
    """
    changelog = _changelog(unreleased_body)

    guard = _run_guard(tmp_path, changelog, "0.1.0.dev1")

    assert guard.succeeded, (guard.stdout, guard.stderr)
    assert "A fix that was moved out of [Unreleased]" in guard.stdout


def test_the_unreleased_check_does_not_override_the_existing_version_checks(
    tmp_path: pathlib.Path,
) -> None:
    """#79 adds a check; it must not replace the ones already there. A missing
    version section is refused exactly as before, even with [Unreleased] empty.
    """
    changelog = "# Changelog\n\n## [Unreleased]\n\nNothing yet.\n"

    guard = _run_guard(tmp_path, changelog, "0.1.0.dev1")

    assert not guard.succeeded
    assert "has no '## [0.1.0.dev1]' section" in guard.stderr
