"""``--help`` under ``TYPER_USE_RICH=0``, which is the mode nobody ran.

Typer documents an environment variable that disables Rich, and reads it once
at import (``typer/core.py:26``), so the second mode needs a second
interpreter. That is why this module is here rather than beside the unit sweep:
no in-process test can reach it.

**What it holds is the premise the fix rests on**, not a defect of its own. The
app passes ``rich_markup_mode=None``, and the argument for that is "then both
settings format through Click, so there is one text and it is the source's".
Read off ``typer/core.py:982`` that is an inference about a third party's
control flow; run here it is a measurement. ``hasRich`` comes back from the
child for the same reason -- a child that quietly kept Rich would render, agree
with the source, and prove nothing.

**It is not what rejects the escape this PR reverted.** That was
``theurian\\[daemon]`` in the docstring with markup left on, and it is the unit
sweep that goes red on it: the escaped source no longer matches the rich-mode
render. Measured, because the first version of this paragraph claimed the
credit for this module. What this module would catch is the mirror image -- a
help string that survives the default mode and does not survive this one -- and
the escape is exactly that shape with the modes swapped, which is the reason to
keep both halves rather than the more interesting one.

The paths and the expected strings come from the unit module's own walk, sent
to the child over stdin. The child renders and nothing else, so there is one
walker in the suite rather than two that can disagree.
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Final

import pytest
from typer.main import get_command

from theurian.cli.main import app

pytestmark = pytest.mark.integration

#: The two commands a user copies out of ``setup --help`` to get a Theurian
#: whose daemon can start. Written out rather than imported, so that a change
#: to ``theurian.domain.extras`` has to be made here too; the same two literals
#: are held against the default mode by ``tests/unit/test_setup_claims.py``.
#:
#: A backslash anywhere in these is the harm the reverted escape caused:
#: ``uv tool install 'theurian\[daemon]'`` is not an installable requirement,
#: and single quotes carry the backslash through the shell to the installer.
INSTALLERS: Final = (
    "uv tool install 'theurian[daemon]'",
    "pipx install 'theurian[daemon]'",
)

#: Renders what it is told to and reports whether Rich was actually off.
_CHILD: Final = """
import json, sys
import typer.core
from typer.testing import CliRunner
from theurian.cli.main import app

paths = json.load(sys.stdin)
runner = CliRunner()
renders = {}
for key in paths:
    args = key.split()[1:] + ["--help"]
    renders[key] = runner.invoke(app, args, env={"COLUMNS": "200"}).output
json.dump({"hasRich": typer.core.HAS_RICH, "renders": renders}, sys.stdout)
"""


def _sweep() -> Any:
    """The unit sweep's walker, loaded by path.

    ``--import-mode=importlib`` puts ``tests/`` on ``sys.path`` but not
    ``tests/unit``, and a test module is not an installed package. Importing it
    by location is the honest form of "these two share one walker" -- the
    alternative is a second walker that can disagree with the first, which is
    the failure this whole PR is about.
    """
    path = Path(__file__).resolve().parents[1] / "unit" / "test_cli_help_rendering.py"
    spec = importlib.util.spec_from_file_location("theurian_help_sweep", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def without_rich() -> dict[str, Any]:
    """Every ``--help`` in the tree, rendered by an interpreter with Rich off."""
    sweep = _sweep()
    paths = sorted(
        {
            " ".join(("theurian", *path))
            for path, _label, _value in sweep.help_strings(get_command(app))
        }
    )
    completed = subprocess.run(  # noqa: S603 - fixed argv, no shell
        [sys.executable, "-c", _CHILD],
        input=json.dumps(paths),
        capture_output=True,
        text=True,
        timeout=60,
        check=True,
        env={**os.environ, "TYPER_USE_RICH": "0"},
    )
    payload: dict[str, Any] = json.loads(completed.stdout)

    assert payload["hasRich"] is False, "TYPER_USE_RICH=0 did not take; this proved nothing"
    return payload


def test_every_help_string_reaches_the_screen_with_rich_disabled(
    without_rich: dict[str, Any],
) -> None:
    sweep = _sweep()
    strings = sweep.help_strings(get_command(app))
    renders = {
        tuple(key.split()[1:]): sweep.collapsed(value)
        for key, value in without_rich["renders"].items()
    }

    lost = sweep.lost_from(renders, strings)

    assert not lost, (
        f"{len(lost)} help string(s) reach the screen differently with Rich disabled: "
        f"{lost}. The two settings format through different code paths, so any markup "
        "escape that is right in one is wrong in the other; the app avoids that by "
        "passing `rich_markup_mode=None`, which sends both down Click's formatter."
    )


def test_the_installer_a_user_copies_is_intact_with_rich_disabled(
    without_rich: dict[str, Any],
) -> None:
    """The harm, stated as itself rather than as a diff against the source.

    A sweep asking "does the source reach the screen" is green for an escaped
    docstring in this mode, because the escape reaches the screen faithfully.
    What is wrong there is not fidelity, it is that the faithful text is a
    command that does not run.
    """
    rendered = _sweep().collapsed(without_rich["renders"]["theurian setup"])

    for installer in INSTALLERS:
        assert installer in rendered, (
            f"`theurian setup --help` does not print {installer} with Rich disabled"
        )
