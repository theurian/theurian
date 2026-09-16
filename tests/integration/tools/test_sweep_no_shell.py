"""The sweep's command runner, proved against a real process (#378).

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

import sys

import pytest
import sweep_census
import sweep_filing

pytestmark = pytest.mark.integration

#: Command substitution, a backquote, a quote and a semicolon. Under a shell the
#: first two would run, the third would unbalance the line and the fourth would
#: start a second command.
_HOSTILE = 'packages/$(id)/`whoami`/"it\'s"; echo pwned.py'

_ECHO = "import sys; sys.stdout.write(sys.argv[1] + '|' + sys.stdin.read())"


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
