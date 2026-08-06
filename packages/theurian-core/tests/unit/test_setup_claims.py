"""What ``setup`` tells users it does, against what it does (FR-L1, FR-L3).

Six surfaces told a user with no Theurian Core on the machine that
``/theurian:setup`` is how Core arrives. None could be true -- setup runs *from*
an installed Core, and every one of those surfaces is reached by shelling out to
the binary whose absence is the thing being reported.

The class is **a surface that offers setup as the way Core gets onto the
machine**, not "three documents that used the word *installs*". The first
attempt closed three faces and left three, including the only one a user meets
without asking for it: the ``SessionStart`` hook, which prints its advice on
every session. :data:`CORE_ARRIVAL_SURFACES` is the enumeration, and every entry
is checked here rather than trusted.

These pin fact and prose to each other in both directions. The fact, so that a
setup which one day really did install Core fails here rather than quietly
making the prose true again. The prose, so it cannot drift back -- and pinned by
*what* is claimed rather than by the words the last author happened to use.
Measured: a first version that asserted ``"installs software" not in doc`` let
"installs Theurian Core", "installs Theurian" and "installs anything" straight
back in, and eight mutations survived it.

The claim generalises past installation. The wording that replaced it was itself
drafted as "registers the project, and can build the initial index", and then as
a two-item list of the eleven steps that only report -- false the same way, and
for the same reason: prose about a step's behaviour, written without running it.
So the enumeration below is derived from ``STEPS``, and the commands the docstring
names are derived from what those steps actually offer.
"""

from __future__ import annotations

import pathlib
import re
from typing import Final

from fakes.setup import FakeMcpConfig, FakeService

from theurian.application import setup_steps
from theurian.application.setup_context import SetupContext
from theurian.application.setup_steps import STEPS
from theurian.cli.setup_commands import setup_command
from theurian.domain.setup import StepId
from theurian.infrastructure.claude.mcp_config import ConnectionSpec
from theurian.infrastructure.secrets.file_store import FileSecretStore

REPO_ROOT = pathlib.Path(__file__).resolve().parents[4]

#: Every surface that tells a reader how Theurian Core reaches the machine. The
#: closure argument for this class is that no other file both states the
#: "Core is absent" premise and offers a remedy for it: found with
#: ``grep -rn`` over the premise *and* over ``/theurian:setup``, because two of
#: these carry the premise with no verb at all -- the hook's warning string and
#: the plugin README's bare three-line install block.
CORE_ARRIVAL_SURFACES: Final = (
    "plugins/claude-code/commands/setup.md",
    "plugins/claude-code/README.md",
    "plugins/claude-code/scripts/session-start.sh",
    "docs/protocol/plugin-core-compatibility.md",
)

PLUGIN_SETUP_DOC: Final = REPO_ROOT / CORE_ARRIVAL_SURFACES[0]
SESSION_START_HOOK: Final = REPO_ROOT / CORE_ARRIVAL_SURFACES[2]

#: The two installers :func:`theurian.application.setup_steps.probe_core` names.
#: Every surface in :data:`CORE_ARRIVAL_SURFACES` must name these rather than
#: ``theurian setup``, and
#: :func:`test_the_installers_pinned_here_are_the_ones_the_step_reports` holds
#: this tuple to the step's own words.
INSTALLERS: Final = ("uv tool install theurian", "pipx install theurian")

#: Commands that legitimately contain "install theurian": the two Core
#: installers, and Claude Code's own plugin installer. Masked out before the
#: scan below, so that it reads prose about *who installs Theurian* rather than
#: the commands the prose is recommending.
_INSTALL_COMMANDS: Final = (
    *INSTALLERS,
    "/plugin install theurian@theurian-plugins",
    "/plugin marketplace add theurian/theurian-plugins",
)

#: An ``install`` verb whose object is Theurian itself, with up to three words
#: in between -- "Install and configure Theurian" was one of the measured
#: survivors. Setup installs the MCP entry and the OS service; it does not
#: install Theurian, Core, "software" or "anything", so every one of these has
#: to be either a denial or a sentence that names the installer doing it.
_INSTALLS_THEURIAN: Final = re.compile(
    r"(?P<lead>(?:\S+\s+){0,6})\binstalls?\b(?:\s+\w+){0,3}?\s+"
    r"(?:theurian core|theurian|core|software|anything|it)\b(?P<tail>(?:\s+\S+){0,6})"
)

#: Words that turn one of the above into the sentence this module wants.
_DENIAL: Final = re.compile(r"\b(?:not|never|cannot|no|nothing|neither|nor)\b")

#: The sentence in ``theurian setup --help`` that enumerates the steps which
#: only report. Parsed rather than spot-checked: the enumeration was two items
#: of eleven when a reviewer counted it, and a spot check is what let that pass.
_REPORT_ONLY_SENTENCE: Final = re.compile(
    r"the other (?P<count>\d+) steps only report what they found: (?P<ids>[a-z0-9 ,-]+?)\."
)

#: A ``theurian`` subcommand named inside backticks.
_SUBCOMMAND: Final = re.compile(r"`(theurian [a-z][a-z ]*[a-z])`")

#: A line that begins a new block rather than continuing the one above it:
#: a heading, a list item, a table row, a fence, a front-matter rule.
_BLOCK_START: Final = re.compile(r"\s*(?:#{1,6}\s|[-*+]\s|\d+\.\s|\||```|---\s*$)")


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


def _report_only_steps() -> tuple[StepId, ...]:
    """The rest, in the order ``STEPS`` lists them."""
    acting = _steps_that_act()
    return tuple(step.step_id for step in STEPS if step.step_id not in acting)


def _context(tmp_path: pathlib.Path, *, executable: str = "") -> SetupContext:
    """A context over an empty directory, which is what makes the probes speak.

    A repository with nothing in it is the environment where the project steps
    have a remedy to offer; a converged one would report ``SATISFIED`` and name
    no command at all. A service manager is present for the same reason: with
    ``service=None`` the two daemon steps report ``NOT_APPLICABLE`` and drop out
    of the seven, which is a fixture that quietly weakens the count below.
    """
    return SetupContext(
        home=tmp_path,
        data_dir=tmp_path / "data",
        port=7419,
        project_root=tmp_path / "repo",
        connection=ConnectionSpec(port=7419),
        mcp_config=FakeMcpConfig(),
        secrets=FileSecretStore(tmp_path / "data"),
        health=lambda: None,
        service=FakeService(),
        executable=executable,
    )


def _collapsed(text: str) -> str:
    """Lowercased with runs of whitespace flattened to single spaces.

    The false claim spanned a line break in two of the original three surfaces,
    so a naive substring search for it passed while the claim was still there.
    """
    return " ".join(text.lower().split())


def _paragraphs(text: str) -> list[str]:
    """The document's paragraphs, soft wraps joined and block boundaries kept.

    A scan that stops at every newline misses the original claim, which wrapped
    across a line break in two of the three surfaces. A scan that ignores
    newlines entirely reads a heading into the sentence beneath it -- "##
    Install" followed by "Theurian Core is a prerequisite" is a claim nobody
    made, and it was reported as one before this function existed. A blank line
    or a block marker ends a paragraph; a soft wrap does not.

    The legitimate install commands are masked here, so what is scanned is prose
    about who installs Theurian rather than the commands it recommends.
    """
    blocks: list[list[str]] = [[]]
    for line in text.splitlines():
        if not line.strip() or _BLOCK_START.match(line):
            blocks.append([])
        blocks[-1].append(line)

    paragraphs: list[str] = []
    for block in blocks:
        collapsed = _collapsed(" ".join(block))
        for command in _INSTALL_COMMANDS:
            collapsed = collapsed.replace(command.lower(), "<installer>")
        if collapsed:
            paragraphs.append(collapsed)
    return paragraphs


def _install_claims_naming_no_installer(text: str) -> list[str]:
    """Every "X installs Theurian" that neither denies it nor says who does.

    "Install Core with `uv tool install theurian`" is the sentence these
    surfaces are supposed to contain, so a claim whose own words name an
    installer is exactly right. What is left over is a claim that leaves the
    reader believing something else puts Core on the machine.
    """
    return [
        match.group(0)
        for paragraph in _paragraphs(text)
        for match in _INSTALLS_THEURIAN.finditer(paragraph)
        if not _DENIAL.search(match.group("lead")) and "<installer>" not in match.group("tail")
    ]


# -- The step table ---------------------------------------------------------


def test_no_setup_step_installs_core_registers_a_project_or_builds_an_index() -> None:
    """The three steps whose prose overstated them apply nothing at all.

    ``CORE_PRESENT`` is the one this module is named for, but a report-only step
    is exactly what invites a docstring to claim setup performs it, so the two
    that were misdescribed alongside it are pinned here too.
    """
    report_only = set(_report_only_steps())

    assert StepId.CORE_PRESENT in report_only
    assert StepId.PROJECT_REGISTERED in report_only
    assert StepId.INITIAL_INDEX in report_only


def test_the_installers_pinned_here_are_the_ones_the_step_reports(
    tmp_path: pathlib.Path,
) -> None:
    """:data:`INSTALLERS` and ``probe_core``'s detail are two literals.

    Nothing in production ties them together, and ``setup_steps.py`` belongs to
    a change in flight, so the link is asserted rather than extracted. Without
    it, rewriting the step's detail to ``brew install theurian`` left every
    other test in this module green while the surfaces above went on naming a
    command the product no longer suggests.
    """
    detail = _collapsed(setup_steps.probe_core(_context(tmp_path)).detail)

    for installer in INSTALLERS:
        assert installer in detail, f"probe_core no longer names {installer}"


# -- `theurian setup --help` ------------------------------------------------


def test_the_cli_docstring_denies_installing_core_and_names_the_installer() -> None:
    doc = setup_command.__doc__ or ""

    assert not _install_claims_naming_no_installer(doc)
    assert "setup cannot tell you core is missing, because setup is core" in _collapsed(doc)
    for installer in INSTALLERS:
        assert installer in _collapsed(doc), f"`theurian setup --help` does not name {installer}"


def test_the_cli_docstring_enumerates_every_step_that_only_reports() -> None:
    """All eleven, in ``STEPS`` order, and the count of the seven that write.

    The enumeration was "project registration and the initial index" -- two of
    eleven, omitting ``project-layout``, ``gitignore`` and ``migrations-valid``,
    which are exactly the ones a user is most likely to believe setup performs.
    Parsed out of the sentence and compared to the table, so the next rewrite
    either lists all of them or fails here.
    """
    doc = _collapsed(setup_command.__doc__ or "")

    match = _REPORT_ONLY_SENTENCE.search(doc)
    assert match is not None, "`theurian setup --help` no longer enumerates the report-only steps"

    listed = tuple(part.strip() for part in match.group("ids").split(","))
    expected = tuple(step_id.value for step_id in _report_only_steps())
    assert listed == expected
    assert int(match.group("count")) == len(expected)

    writes = len(_steps_that_act())
    assert f"those {writes} steps are every write setup performs" in doc


def test_the_cli_docstring_names_the_commands_those_steps_defer_to(
    tmp_path: pathlib.Path,
) -> None:
    """Which commands, measured off the steps rather than remembered.

    The docstring says several report-only steps "name the command that does the
    work instead". That is a claim about the step table, which is how this file
    got its two previous false sentences, so it is probed: every ``theurian``
    subcommand a report-only step offers has to appear in ``--help``.
    """
    context = _context(tmp_path)
    report_only = set(_report_only_steps())
    offered = {
        command
        for step in STEPS
        if step.step_id in report_only
        for command in _SUBCOMMAND.findall(step.probe(context).action)
    }

    assert offered, "no report-only step names a command; the docstring's claim is now false"
    doc = _collapsed(setup_command.__doc__ or "")
    for command in sorted(offered):
        assert f"`{command}`" in doc, f"`theurian setup --help` does not name `{command}`"


# -- Every surface that says how Core arrives -------------------------------


def test_no_surface_that_says_how_core_arrives_claims_setup_installs_it() -> None:
    """The class, over its whole enumeration rather than one member at a time.

    Three rounds closed one face each. What ended it was naming the root cause
    -- a surface that offers setup as the way Core gets onto the machine -- and
    checking every member against the same rule.
    """
    for name in CORE_ARRIVAL_SURFACES:
        claims = _install_claims_naming_no_installer((REPO_ROOT / name).read_text(encoding="utf-8"))
        assert not claims, f"{name} claims setup installs Theurian: {claims}"


def test_every_surface_that_says_how_core_arrives_names_the_installer() -> None:
    """Naming no installer is the same defect as naming the wrong one.

    A surface that only denies setup installs Core leaves the user knowing what
    does not work and not what does -- which is what the plugin README did with
    a three-line block that ended at ``/theurian:setup``.
    """
    for name in CORE_ARRIVAL_SURFACES:
        text = _collapsed((REPO_ROOT / name).read_text(encoding="utf-8"))
        for installer in INSTALLERS:
            assert installer in text, f"{name} does not name `{installer}`"


def test_the_session_start_hook_sends_a_core_less_user_to_the_installer() -> None:
    """The one surface a user meets without asking, on every session.

    It used to say "Core is not installed. Run /theurian:setup once to get
    started." -- the same advice ``CORE_MISSING`` carried, on the only path that
    reaches a real user, and left in place by the change that fixed
    ``CORE_MISSING``. That branch of ``resolve_compatibility`` has no production
    caller at all: its one call site passes a parsed version and never ``None``,
    so the fix landed on the unreachable face and not on this one.
    """
    remedies = [
        paragraph
        for paragraph in _paragraphs(SESSION_START_HOOK.read_text(encoding="utf-8"))
        if "core is not installed" in paragraph
    ]

    assert len(remedies) == 1, f"the hook's Core-absent warning is not findable: {remedies}"
    assert "<installer>" in remedies[0], "the hook does not say how Core reaches the machine"


def test_no_surface_offers_setup_before_the_installer() -> None:
    """Order is the whole finding, not a nicety.

    ``/theurian:setup`` shells out to the ``theurian`` binary, so a reader who
    has just been told Core is absent and meets that command first has met an
    instruction they cannot carry out. ``CORE_MISSING``'s remedy states the rule
    -- "names the installer *before* ``/theurian:setup``" -- and this is what
    holds the other surfaces to it.
    """
    for name in CORE_ARRIVAL_SURFACES:
        for paragraph in _paragraphs((REPO_ROOT / name).read_text(encoding="utf-8")):
            if "/theurian:setup" not in paragraph or "<installer>" not in paragraph:
                continue
            assert paragraph.index("<installer>") < paragraph.index("/theurian:setup"), (
                f"{name} offers /theurian:setup before the installer: {paragraph}"
            )


# -- `/theurian:setup` ------------------------------------------------------


def test_the_plugin_command_document_denies_installing_core() -> None:
    """The first thing a plugin user reads, and the one that has to work first.

    A user who reaches ``/theurian:setup`` without Core on ``PATH`` gets a bare
    "command not found" from every step in the document, so it opens by checking
    for the binary rather than assuming it.
    """
    text = _collapsed(PLUGIN_SETUP_DOC.read_text(encoding="utf-8"))

    assert "does **not** install theurian core" in text
    assert "command -v theurian" in text


def test_the_plugin_document_does_not_call_a_writing_step_one_setup_skips(
    tmp_path: pathlib.Path,
) -> None:
    """``missing`` plus an ``action`` is not the signal it was said to be.

    Every one of the steps setup performs reports exactly that while there is
    work to do, so an agent following the old rule would have told the user to
    go and run "Create ~/.theurian with mode 0700" themselves. Probed against
    the real step table rather than argued -- and the count is asserted, because
    the rule was false for *every* writer, not for an unlucky one.
    """
    context = _context(tmp_path)
    acting = _steps_that_act()
    writers_that_look_skippable = {
        step.step_id.value
        for step in STEPS
        if step.step_id in acting and (probed := step.probe(context)).would_change and probed.action
    }

    assert len(writers_that_look_skippable) == len(acting), (
        f"only {sorted(writers_that_look_skippable)} of {len(acting)} writers report "
        f"missing with an action; the rule this test refutes may have become true"
    )

    text = _collapsed(PLUGIN_SETUP_DOC.read_text(encoding="utf-8"))
    assert "a step that reports `missing` alongside an `action` is one setup does not" not in text
    for field in ("`status`", "`outcome`"):
        assert field in text, f"step 6 no longer tells the agent to read {field}"
