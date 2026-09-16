"""The sweep's child processes, proved against real ones (#378).

``tests/unit/tools/test_sweep_gh_invocation.py`` asserts the *shape* of the call
-- one argv element per string, body on stdin -- against a recording stand-in.
That is the right check for the argv builders and the wrong one for the claim
that matters most: a stand-in cannot demonstrate that nothing interprets
``$(id)``. Only a real subprocess can, so this file spawns one.

The subprocess is a Python interpreter echoing its own argument and stdin. It
reaches no network, writes no file, and is not ``gh``: no test in this
repository invokes the real ``gh``.
"""

from __future__ import annotations

import os
import sys

import pytest
import sweep
import sweep_census
import sweep_filing

pytestmark = pytest.mark.integration

#: Command substitution, a backquote, a quote and a semicolon. Under a shell the
#: first two would run, the third would unbalance the line and the fourth would
#: start a second command.
_HOSTILE = 'packages/$(id)/`whoami`/"it\'s"; echo pwned.py'

_ECHO = "import sys; sys.stdout.write(sys.argv[1] + '|' + sys.stdin.read())"

#: Planted values, named so a scanner does not read them as credentials. Nothing
#: here authenticates anything; the point is that the child cannot see them.
_SENTINEL_GH = "planted-not-a-credential-gh"
_SENTINEL_GITHUB = "planted-not-a-credential-github"
_CACHE_MARKER = "a-cache-path-the-child-must-keep"


def test_an_argument_full_of_shell_syntax_arrives_as_the_bytes_that_were_sent() -> None:
    """The claim a stand-in cannot make: no shell ever sees this string.

    The child prints the argument it was handed. Byte equality is the assertion
    because every interesting failure changes the bytes: a shell would substitute
    ``$(id)`` and the backquotes for output, split the string at the space, and
    truncate it at the semicolon.
    """
    result = sweep_filing.run_command([sys.executable, "-c", _ECHO, _HOSTILE], "body text")

    assert result.returncode == 0
    assert result.stdout == f"{_HOSTILE}|body text"


def test_a_body_sent_on_stdin_arrives_whole_including_its_fences_and_newlines() -> None:
    """``--body-file -`` is only useful if what arrives is what was written.

    An issue body is markdown with embedded code fences and trailing newlines,
    and a runner that stripped or re-encoded any of it would file a body that
    renders differently from the one the tests checked.
    """
    body = "# heading\n\n```diff\n-    value < 3\n+    value <= 3\n```\n\nlast line\n"

    result = sweep_filing.run_command([sys.executable, "-c", _ECHO, "x"], body)

    assert result.stdout == f"x|{body}"


def test_a_command_that_cannot_be_run_at_all_is_a_failed_sweep() -> None:
    """``gh`` missing from ``PATH`` is the ordinary way this fails on a new runner.

    Left uncaught it would be an ``OSError`` traceback and a Python exit code of
    1 -- which happens to be the code the contract reserves for "the sweep failed
    to run", so the workflow would alarm correctly for the wrong reason and the
    log would show a crash rather than a diagnosis.
    """
    with pytest.raises(sweep_census.SweepError):
        sweep_filing.run_command(["theurian-no-such-binary-378", "--version"], "")


def test_the_harness_child_cannot_see_the_tracker_token(
    monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str]
) -> None:
    """Seven full suite walks a night must not run with an issues:write token.

    The harness executes this repository's entire dependency tree under mutation
    for roughly two hours, nightly, and ``mutate_run._child_env`` builds its own
    environment from ``dict(os.environ)`` -- so whatever the driver was given is
    what every one of those processes gets. In CI that is ``GH_TOKEN`` with
    ``issues:write``, and the red-team workflow is the only one pairing suite
    execution with a write token.

    The driver still needs the token itself: it files through ``gh`` afterwards,
    in a process this does not touch. Only the harness loses it.

    Asserted by reading the child's own view of its environment through a real
    subprocess. A stand-in that recorded an ``env=`` argument would pass equally
    well against a runner that built the dict and then forgot to pass it.
    """
    monkeypatch.setenv("GH_TOKEN", _SENTINEL_GH)
    monkeypatch.setenv("GITHUB_TOKEN", _SENTINEL_GITHUB)
    probe = (
        "import os;"
        "print('GH=' + os.environ.get('GH_TOKEN', '<absent>'));"
        "print('GITHUB=' + os.environ.get('GITHUB_TOKEN', '<absent>'));"
        "print('PATH=' + ('set' if os.environ.get('PATH') else '<absent>'))"
    )

    code = sweep._run_mutate([sys.executable, "-c", probe])

    printed = capfd.readouterr().out
    assert code == 0
    assert "GH=<absent>" in printed
    assert "GITHUB=<absent>" in printed
    assert "PATH=set" in printed
    assert "planted-not-a-credential" not in printed
    assert os.environ["GH_TOKEN"] == _SENTINEL_GH  # the driver keeps it, for `gh`


def test_the_harness_keeps_every_variable_that_is_not_a_tracker_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Subtracting two names, not building an allow-list.

    ``tools/mutate.py`` reads ``UV_CACHE_DIR`` to keep each isolated tree's
    virtualenv warm, ``HOME`` to resolve it when that is unset, and git's own
    configuration variables; a scrubbed environment would make every tree build
    from scratch or fail outright. The fix has to be narrow, and narrowness is
    the half that no security assertion above would notice going wrong.
    """
    monkeypatch.setenv("GH_TOKEN", _SENTINEL_GH)
    monkeypatch.setenv("UV_CACHE_DIR", _CACHE_MARKER)

    env = sweep._harness_env()

    assert "GH_TOKEN" not in env
    assert "GITHUB_TOKEN" not in env
    assert env["UV_CACHE_DIR"] == _CACHE_MARKER
    assert set(env) == set(os.environ) - {"GH_TOKEN", "GITHUB_TOKEN"}
