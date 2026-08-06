"""What ``setup`` tells users it does, against what it does (FR-L1, FR-L3).

Three surfaces claimed ``theurian setup`` installs Theurian Core: the docstring
``theurian setup --help`` prints, the plugin's ``/theurian:setup`` document, and
the ``CORE_MISSING`` compatibility remedy. None could be true -- setup runs
*from* an installed Core -- and the remedy was the worst of them, sending a user
with no ``theurian`` on ``PATH`` to a command that shells out to that binary.

These pin the fact and the prose to each other, in both directions. The fact, so
that a setup which one day really did install Core fails here rather than
quietly making the prose true again. The prose, so it cannot drift back.

The claim generalises past installation: the wording that replaced it was itself
drafted as "registers the project, and can build the initial index", and both
are false for the same reason -- those steps report and name another command.
"""

from __future__ import annotations

import pathlib

from theurian.application import setup_steps
from theurian.application.setup_steps import STEPS
from theurian.cli.setup_commands import setup_command
from theurian.domain.setup import StepId

REPO_ROOT = pathlib.Path(__file__).resolve().parents[4]
PLUGIN_SETUP_DOC = REPO_ROOT / "plugins" / "claude-code" / "commands" / "setup.md"

#: The two installers :func:`probe_core` names. Every surface telling a user how
#: Core arrives must name these rather than ``theurian setup``.
INSTALLERS = ("uv tool install theurian", "pipx install theurian")


def _steps_that_act() -> frozenset[StepId]:
    """The steps that write something, derived from the step table itself.

    Classified by whether a step's ``apply`` is one of the module's ``apply_*``
    functions -- never by how "this step does nothing" happens to be spelled.
    That spelling is a shared no-op function here and ``None`` on the branch of
    https://github.com/theurian/theurian/pull/45, the two touch different files,
    and Git merges them cleanly: a module that named the sentinel would import a
    symbol the merged tree does not define, and a collection error aborts the
    whole run rather than one test. Neither spelling is an ``apply_*``, so both
    classify identically through this function.
    """
    actions: frozenset[object] = frozenset(
        getattr(setup_steps, name) for name in dir(setup_steps) if name.startswith("apply_")
    )
    return frozenset(step.step_id for step in STEPS if step.apply in actions)


def _collapsed(text: str) -> str:
    """Lowercased with runs of whitespace flattened to single spaces.

    The false claim spanned a line break in two of the three surfaces, so a
    naive substring search for it passed while the claim was still there.
    """
    return " ".join(text.lower().split())


def test_no_setup_step_installs_core_registers_a_project_or_builds_an_index() -> None:
    """The three steps whose prose overstated them apply nothing at all.

    ``CORE_PRESENT`` is the one this module is named for, but a report-only step
    is exactly what invites a docstring to claim setup performs it, so the two
    that were misdescribed alongside it are pinned here too.
    """
    report_only = {step.step_id for step in STEPS} - _steps_that_act()

    assert StepId.CORE_PRESENT in report_only
    assert StepId.PROJECT_REGISTERED in report_only
    assert StepId.INITIAL_INDEX in report_only


def test_the_cli_docstring_denies_installing_core_and_names_the_installer() -> None:
    doc = _collapsed(setup_command.__doc__ or "")

    assert "installs software" not in doc
    assert "it does not install core" in doc
    for installer in INSTALLERS:
        assert installer in doc, f"`theurian setup --help` does not name {installer}"


def test_the_plugin_command_document_denies_installing_core() -> None:
    """The first thing a plugin user reads, and the one that has to work first.

    A user who reaches ``/theurian:setup`` without Core on ``PATH`` gets a bare
    "command not found" from every step in the document, so it opens by checking
    for the binary rather than assuming it.
    """
    text = _collapsed(PLUGIN_SETUP_DOC.read_text(encoding="utf-8"))

    assert "installs software" not in text
    assert "does **not** install theurian core" in text
    assert "command -v theurian" in text
    for installer in INSTALLERS:
        assert installer in text, f"/theurian:setup does not name {installer}"
