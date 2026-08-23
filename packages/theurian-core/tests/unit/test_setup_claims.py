"""What ``setup`` tells users it does, against what it does (FR-L1, FR-L3).

Nine surfaces tell a user with no Theurian Core on the machine that
``/theurian:setup`` is how Core arrives. None can be true -- setup runs *from* an
installed Core, and every one of those surfaces is reached by shelling out to the
binary whose absence is the thing being reported.

The class is **a surface that offers setup as the way Core gets onto the
machine**, not "three documents that used the word *installs*". The first attempt
closed three faces and left three more open, including the only one a user meets
without asking for it: the ``SessionStart`` hook, which prints its advice on every
session.

**Seven of the nine are corrected, and this module does not check all seven.** Two
are held where they are produced rather than as text -- ``setup_command.__doc__``
by :func:`test_the_cli_docstring_denies_installing_core_and_names_the_installer`
below, and ``domain/compatibility.py``'s ``CORE_MISSING`` verdict by
``test_compatibility.py::test_missing_core_is_reported_as_install_then_setup``.
The remaining five are read from disk and listed in
:data:`CORE_ARRIVAL_SURFACES`. **Two are not corrected at all**, and are
recorded with line numbers in T-16 of ``docs/security/threat-model.md``.

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

**A docstring is not a screen.** Every ``--help`` check here read
``setup_command.__doc__``, and Typer's default parses that text as Rich markup
before printing it, so the two were different strings. The installer literal was
the one place it mattered: ``'theurian[daemon]'`` in the source, ``'theurian'``
on the terminal, and this module green throughout. Only the assertion that
turned on it reads the render -- see :func:`_rendered_help` -- because the ones
about prose are about prose. What keeps *those* honest is that ``cli/main.py``
now passes ``rich_markup_mode=None``, pinned by
``tests/unit/test_cli_help_rendering.py``, which renders every ``--help`` in the
tree and fails on any string that does not arrive intact.
"""

from __future__ import annotations

import pathlib
import re
from typing import Final

import pytest
from fakes.setup import FakeMcpConfig, FakeService
from typer.testing import CliRunner

from theurian.application import setup_steps
from theurian.application.setup_context import SetupContext
from theurian.application.setup_service import SetupRequest, SetupService
from theurian.application.setup_steps import STEPS
from theurian.cli.main import app
from theurian.cli.setup_commands import setup_command
from theurian.domain.setup import SetupReport, SetupState, StepId, StepStatus
from theurian.infrastructure.claude.mcp_config import ConnectionSpec
from theurian.infrastructure.secrets.file_store import FileSecretStore

REPO_ROOT = pathlib.Path(__file__).resolve().parents[4]

#: The five corrected surfaces this module reads from disk, as repository-relative
#: paths. **This is not the class and it is not a closure argument.** Two more
#: corrected surfaces are checked where they are produced -- see the module
#: docstring -- and two are not corrected at all:
#: ``docs/integrations/claude-code.md`` and
#: ``docs/architecture/requirements-analysis.md``, each carrying the "Core is
#: absent" premise beside a ``/theurian:setup`` remedy, enumerated with line
#: numbers in T-16 of ``docs/security/threat-model.md``.
#:
#: ``README.md`` is the fifth, and it joins after three successive exclusions
#: that each expired in turn. It used to install Core from the checkout, because
#: the distribution did not exist on PyPI and the literal :data:`INSTALLERS` pin
#: would have failed on the one surface whose instruction ran; publishing
#: ``0.1.0.dev0`` ended that. Then :data:`INSTALLERS` was the bare
#: ``uv tool install theurian`` -- an installer the README's own next command
#: does not survive, because it omits the ``daemon`` extra the quick start goes
#: on to use -- and https://github.com/theurian/theurian/issues/78 ended that.
#: What was left was the interpreter: the README recommends
#: ``uv tool install --python 3.13 'theurian[daemon]'``, the flag sits between
#: the tool and the package spec, and
#: :func:`test_every_surface_that_says_how_core_arrives_names_the_installer`
#: requires each literal *contiguously*, so the pinned string was not there to
#: find.
#:
#: **That last one is closed by this commit, and the match did not move.**
#: Loosening it to skip flags was rejected rather than deferred: verbatim is the
#: whole of what this check is, and a rule that accepts arbitrary text between
#: ``uv tool install`` and the package would accept
#: ``uv tool install --from somewhere-else 'theurian[daemon]'``, which is the
#: supply-chain sentence T-16 exists over. What moved instead is
#: :data:`INSTALLERS`, which now names the interpreter the README always named --
#: so the README's command is a verbatim match, the file is in the tuple, and
#: there is no exclusion left to justify or to expire.
#:
#: Appended rather than inserted: :data:`PLUGIN_SETUP_DOC` and
#: :data:`SESSION_START_HOOK` index into this tuple by position.
#:
#: An earlier version of this comment claimed no other file in the tree paired
#: that premise with a remedy. Three do, and ``domain/compatibility.py``'s own
#: ``CORE_MISSING`` message and remedy are the plainest instance of the pair --
#: which is what a list of what *is* checked reads as when it is written in the
#: completed tense. A deferral list is not a closure argument, however carefully
#: the search behind it was run.
CORE_ARRIVAL_SURFACES: Final = (
    "plugins/claude-code/commands/setup.md",
    "plugins/claude-code/README.md",
    "plugins/claude-code/scripts/session-start.sh",
    "docs/protocol/plugin-core-compatibility.md",
    "README.md",
)

PLUGIN_SETUP_DOC: Final = REPO_ROOT / CORE_ARRIVAL_SURFACES[0]
SESSION_START_HOOK: Final = REPO_ROOT / CORE_ARRIVAL_SURFACES[2]

#: The two installers :func:`theurian.application.setup_steps.probe_core` names.
#: Every surface in :data:`CORE_ARRIVAL_SURFACES` must name these rather than
#: ``theurian setup``, and
#: :func:`test_the_installers_pinned_here_are_the_ones_the_step_reports` holds
#: this tuple to the step's own words.
#:
#: **The ``[daemon]`` extra is part of the literal, and that is the correction
#: #78 made.** Without it, ``uv tool install theurian`` resolves, installs, and
#: leaves a Theurian whose ``daemon start`` fails on ``uvicorn`` -- so every
#: surface below was true in the only sense this tuple could measure (the
#: command runs) and false in the sense a reader uses it (the install works).
#:
#: **Still two literals rather than an import of
#: :data:`~theurian.domain.extras.DAEMON_INSTALLERS`**, which production now
#: has. Extracting it would make this module green for whatever that constant
#: says, including ``brew install theurian`` -- the exact drift the docstring of
#: :func:`test_the_installers_pinned_here_are_the_ones_the_step_reports`
#: describes, arriving one indirection further away. What makes the check mean
#: anything is that the two are written independently and asserted equal.
INSTALLERS: Final = (
    "uv tool install --python 3.13 'theurian[daemon]'",
    "pipx install --python 3.13 'theurian[daemon]'",
)

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
#:
#: **Its reach is one grammar, and that is recorded rather than chased.** Seven
#: rephrasings were measured surviving it: a gerund, an inverted step 1, "download
#: and set it up", the installer demoted to a footnote or offered as the
#: alternative, passive "where Core gets installed", and "is the installer for".
#: Two are worth naming because they are not near-misses:
#:
#: - :data:`_DENIAL` accepts *nothing* as a denial word, and step 1's own "If this
#:   prints nothing" supplies one within six words -- so the rule is weakest
#:   exactly where the claim is most likely to return.
#: - :data:`_INSTALL_COMMANDS` masks ``/plugin install ...`` to ``<installer>``,
#:   so a three-line block ending at ``/theurian:setup`` reads as compliant on the
#:   ``<installer>``-in-tail branch. The plugin README is held by
#:   :func:`test_every_surface_that_says_how_core_arrives_names_the_installer`
#:   instead, which wants a Core installer by name.
#:
#: Tightening this until no rephrasing survives is the same defect one level up:
#: a rule that pins grammar will always have a next grammar. What it is, and all
#: it is, is a regression test over the wordings this class has actually taken.
#: It is not a closure argument and neither is the tuple it runs over; see
#: :data:`CORE_ARRIVAL_SURFACES`.
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

    **Spelling-independent on the sentinel side, not on the writer side.** A step
    wired to a lambda or a ``functools.partial`` rather than to a module-level
    ``apply_*`` is classified report-only here, and the enumeration test below
    then *requires* ``--help`` to list it as a step that only reports. Recorded
    rather than closed: identifying a writer by anything weaker than the module's
    own names -- an arity check, a name prefix on the callable -- trades a shape
    this repository does not use for one it might.
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


def _degraded_setup(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> SetupService:
    """The service behind :func:`_degraded_report`, before it has run.

    Handed out separately because one test below needs the *plan* as well as the
    report, and the two disagree by design: a step that applied successfully
    probes ``SATISFIED`` afterwards and declares no paths at all, so the finished
    report cannot be asked what setup said it would write.

    The start timeout is patched to zero because :func:`apply_daemon_running`
    waits out ``DAEMON_START_TIMEOUT_SECONDS`` for a daemon that will never
    arrive, and fifteen seconds of real sleeping is not something a unit test
    should buy.
    """
    monkeypatch.setattr(setup_steps, "DAEMON_START_TIMEOUT_SECONDS", 0.0)
    executable = tmp_path / "theurian"
    # 0755, not `touch()`: `probe_core` requires a path a service manager could
    # exec, and a 0644 file makes `core-present` abort the run, so every claim
    # below would be asserted against a plan that never ran (#49).
    executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    executable.chmod(0o755)
    return SetupService(_context(tmp_path, executable=str(executable)))


def _degraded_report(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> SetupReport:
    """A real run that ends ``DEGRADED`` -- probed *after* the verification pass.

    The rule in step 6 of the plugin document is about the report a user is
    shown at the end, and the test that used to hold it read the plan built at
    the start. Those disagree in exactly the case the rule turns on: before the
    run every writer is ``MISSING`` with an action; after it, the ones still
    ``MISSING`` are the ones setup could not finish.

    The scenario is the ordinary one -- the service is registered and started,
    and nothing ever answers on the port. No apply raises.
    """
    return _degraded_setup(tmp_path, monkeypatch).run(SetupRequest())


def _collapsed(text: str) -> str:
    """Lowercased with runs of whitespace flattened to single spaces.

    The false claim spanned a line break in two of the original three surfaces,
    so a naive substring search for it passed while the claim was still there.
    """
    return " ".join(text.lower().split())


def _rendered_help() -> str:
    """``theurian setup --help`` as a terminal receives it, collapsed.

    Not ``setup_command.__doc__``. The two agree again only because
    ``cli/main.py`` turns Rich markup off; under Typer's default the docstring
    said ``'theurian[daemon]'`` and the terminal said ``'theurian'``. Reading
    the render keeps this assertion true of the thing it names rather than true
    of the file that feeds it.

    ``COLUMNS`` is pinned because the runner otherwise reports 80 columns, and a
    literal spanning spaces is only contiguous after collapsing if the wrap fell
    on one of them.
    """
    result = CliRunner().invoke(
        app, ["setup", "--help"], env={"COLUMNS": "200"}, catch_exceptions=False
    )
    assert result.exit_code == 0, result.output
    return _collapsed(result.output)


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

    "Install Core with `uv tool install --python 3.13 'theurian[daemon]'`" is
    the sentence these surfaces are supposed to contain, so a claim whose own
    words name an installer is exactly right. What is left over is a claim that
    leaves the reader believing something else puts Core on the machine.

    The masking in :func:`_paragraphs` is what makes "names an installer" mean
    *the current* installer: :data:`_INSTALL_COMMANDS` no longer contains the
    bare command, so a surface that reintroduces it is a claim naming no
    installer and fails here. Measured -- two drafts of #78's own prose in
    ``setup.md`` were rejected by exactly that, one of them for quoting the very
    command the change existed to replace.
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


def test_the_token_is_minted_before_the_step_that_stores_it() -> None:
    """§6.4, #153. Whichever of the pair runs second applies nothing.

    ``apply_token_storage`` is one statement: a call to ``apply_token``, which
    mints only when there is no token (ADR-0011). And the two probes both key on
    ``(auth/mcp-token).is_file()``, so they report ``MISSING`` together or not at
    all -- a run that reaches one of these applies reaches both, and the second
    one finds the file already there and returns without writing.

    That is the exception to the rule §6.4 states about a halted run's
    ``changed_paths``: *the declared paths of every step whose apply finished --
    declared, not re-measured. A step that returned is taken at its word, which
    is exact for what ships today: every apply here writes or raises.*
    ``apply_token_storage`` returns without doing either, and its declared path
    is truthful only because the step before it wrote the file.
    [#153](https://github.com/theurian/theurian/issues/153) records the class an
    apply that finishes without writing belongs to.

    **What swapping the two entries actually moves, measured rather than
    argued.** A cold run and a run halted on a *directory* at ``auth/mcp-token``
    were each executed under both orders: ``state``, ``changed_paths`` and both
    steps' outcomes came back identical, because the credential is declared by
    both steps and written by whichever runs first, so the run-level claim
    survives either way. What moves is the journal. Its applied record carries
    the step's own ``action``, and under the swap that is
    ``token: "Generate a 256-bit token with the system CSPRNG."`` written for a
    step that generated nothing -- an event claim, in the file an operator reads
    to repair a machine, about work that did not happen. In the shipped order
    the second of the pair is ``token-storage``, whose action describes a state,
    ``"Store the token as a 0600 file inside a 0700 directory."``, and that state
    is true at the moment the record is written.

    So this is a pin on the journal and on §6.4's trust rule, not on
    ``changed_paths``: an assertion that the report moves under the swap would
    not fail, and one written that way would be reporting a safety that is not
    there.

    Both steps are asserted still to carry an apply, because the whole reason
    above evaporates if one of them stops applying -- and a reader arriving after
    that change needs the failure to land here rather than on the ordering.
    """
    acting = _steps_that_act()
    order = [step.step_id for step in STEPS]

    assert {StepId.TOKEN, StepId.TOKEN_STORAGE} <= acting, (
        "both halves of the pair still apply; without that this order holds nothing"
    )
    assert order.index(StepId.TOKEN) < order.index(StepId.TOKEN_STORAGE), (
        "the step that mints the token runs before the one whose apply is a no-op "
        "once it exists, or the journal records a mint that never happened"
    )


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
    """Two claims, read from the two places they are true of.

    The denial is a property of the prose, so it is read from the docstring.
    Naming the installer is a property of what a user sees, so it is read from
    the render -- and those disagreed: the docstring carried
    ``'theurian[daemon]'`` while ``--help`` printed ``'theurian'``, because
    Typer parses the docstring as Rich markup and a bracket opening on a
    lowercase letter is a style tag. Reading the source, this loop went green
    on the commit that introduced the discrepancy and stayed green through the
    review and the release branch, with the extra missing from every screen it
    describes.

    ``tests/integration/test_cli_help_without_rich.py`` asserts the same two
    literals against the same command's help, and it is not a duplicate of this
    loop: it renders under ``TYPER_USE_RICH=0``, which formats through a
    different code path in Typer and cannot be reached from this interpreter.
    An earlier version of this paragraph justified the pair by claiming
    :data:`INSTALLERS` is derived from ``probe_core`` while the other tuple is
    independent. It is the other way round -- see :data:`INSTALLERS`, which is
    itself the independently written literal, asserted *equal* to the step's
    words rather than taken from them. The two tuples differ by mode, and by
    case: this one is compared after :func:`_collapsed` lowercases.
    """
    doc = setup_command.__doc__ or ""

    assert not _install_claims_naming_no_installer(doc)
    assert "setup cannot tell you core is missing, because setup is core" in _collapsed(doc)
    rendered = _rendered_help()
    for installer in INSTALLERS:
        assert installer in rendered, f"`theurian setup --help` does not print {installer}"


def test_the_cli_docstring_enumerates_every_step_that_only_reports() -> None:
    """All eleven, in ``STEPS`` order, and the count of the seven that write.

    The enumeration was "project registration and the initial index" -- two of
    eleven, omitting ``project-layout``, ``gitignore`` and ``migrations-valid``,
    which are exactly the ones a user is most likely to believe setup performs.
    Parsed out of the sentence and compared to the table, so the next rewrite
    either lists all of them or fails here.

    **The count of the writers is asserted here; that they are every write is
    not**, and the difference is what two earlier versions of this note got
    wrong. ``those N steps are every write setup performs`` quantifies over
    *writes*, not over steps, so :meth:`SetupService._journal` -- which appends
    ``setup-journal.jsonl`` on every applied step, attributed to nothing --
    falsified it from the moment that method existed. The note recorded the
    exception and then argued the sentence could stay, first because the journal
    lands inside the ``~/.theurian`` the first clause already announces, and
    then because the sentence "is true of steps". It is not a sentence about
    steps: it is the one place ``--help`` tells a reader that the list they have
    just read is complete.

    The correction landed in the docstring rather than in this note. ``--help``
    now names the journal as the write outside the seven and says
    ``changedPaths`` lists it whenever the append reached the disk, which is
    what the return value of `_journal` means. Held to the code by
    :func:`test_the_cli_docstring_names_the_write_that_belongs_to_no_step`
    below, which measures the undeclared path off a real run rather than
    remembering it -- because a note is where this claim survived being written
    down as false twice.
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


def test_the_cli_docstring_names_the_write_that_belongs_to_no_step(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#47. The completeness claim is measured against a run, not read off the table.

    ``--help`` says the seven writing steps are every write setup performs. That
    is checkable: a run publishes ``changedPaths``, the plan publishes what each
    step said it would write, and anything in the first that is not in the
    second is a write belonging to no step. There is exactly one, and it is the
    journal -- which is why the sentence carries an exception, and why the
    exception has to name it rather than gesture at the data directory.

    The plan is read *before* the run and the report after: a step that applied
    successfully probes ``SATISFIED`` afterwards and declares no paths at all, so
    comparing against the finished report would report every path as undeclared
    and pass this test for a reason that has nothing to do with the journal.

    The prose half asserts the journal is named **in the same sentence as the
    claim**, not merely somewhere in the docstring. A rewrite that dropped the
    exception while leaving the word elsewhere is exactly the drift this exists
    to catch, and one that says "except for" or "besides" instead is not.
    """
    service = _degraded_setup(tmp_path, monkeypatch)
    declared = service.plan().paths

    report = service.run(SetupRequest())

    assert report.state is SetupState.DEGRADED, f"the fixture no longer degrades: {report.state}"
    undeclared = [path for path in report.changed_paths if path not in declared]
    assert undeclared == [str(service.journal_path)], (
        "one write belongs to no step, and it is the journal; if this list grew, "
        "`--help`'s exception clause is now incomplete rather than merely unnamed"
    )

    doc = _collapsed(setup_command.__doc__ or "")
    claim = f"those {len(_steps_that_act())} steps are every write setup performs"
    assert claim in doc, "`theurian setup --help` no longer makes the completeness claim"
    rest_of_sentence = doc[doc.index(claim) + len(claim) :].split(".")[0]
    assert "journal" in rest_of_sentence, (
        f"the claim is stated without its one exception: {claim}{rest_of_sentence}"
    )


def test_the_cli_docstring_names_the_commands_those_steps_defer_to(
    tmp_path: pathlib.Path,
) -> None:
    """Which commands, measured off the steps rather than remembered.

    The docstring says several report-only steps "name the command that does the
    work instead". That is a claim about the step table, which is how this file
    got its two previous false sentences, so it is probed: every ``theurian``
    subcommand a report-only step offers has to appear in ``--help``.

    **Only ``action`` is read, and two steps name their command in ``summary``
    instead** -- ``initial-index`` offers ``theurian migrate apply`` there, and
    ``migrations-valid`` offers ``theurian migrate validate`` once a migrations
    directory exists. Neither is in ``--help``. Widening this to ``summary``
    means deciding what a summary that merely mentions a command is promising,
    and that is a question about the step table rather than about the docstring;
    recorded here so the omission is a known one.
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
    """One rule over :data:`CORE_ARRIVAL_SURFACES`, rather than one member at a time.

    What moved this along was naming the root cause -- a surface that offers
    setup as the way Core gets onto the machine -- and applying the same rule to
    every member instead of reading each document on its own terms. What it does
    *not* do is decide the population: this is a regression test over the four
    files listed there, and the class is larger than the list.
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


def test_the_plugin_document_relays_only_the_actions_that_name_a_command(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The rule is about the finished report, so it is checked against one.

    Its predecessor read the plan built *before* the run, where every writer is
    ``MISSING`` with an action, and concluded that anything still ``MISSING``
    afterwards carries a command to hand over. Measured on an ordinary
    ``DEGRADED`` run -- no apply failure, the daemon simply never answers -- the
    unresolved steps split two ways, and the writer's action is setup describing
    its own unfinished work:

        daemon-running    "Start the service that was just registered."
        project-layout    "Create the missing directories. Run `theurian init`."

    Both halves of the split are asserted to exist, so the document cannot be
    checked against a report that happens to contain only one of them.
    """
    report = _degraded_report(tmp_path, monkeypatch)
    assert report.state is SetupState.DEGRADED, f"the fixture no longer degrades: {report.state}"

    unresolved = [step for step in report.steps if step.status is StepStatus.MISSING]
    commands = {command for step in unresolved for command in _SUBCOMMAND.findall(step.action)}
    describing = [step.action for step in unresolved if not _SUBCOMMAND.search(step.action)]

    assert commands, "no unresolved step names a command; the document's first case is dead"
    assert describing, "every unresolved action names a command; the second case is dead"

    text = _collapsed(PLUGIN_SETUP_DOC.read_text(encoding="utf-8"))
    for command in sorted(commands):
        assert f"`{command}`" in text, f"step 6 does not tell the agent to relay `{command}`"

    quoted = [action for action in describing if _collapsed(action).rstrip(".") in text]
    assert quoted, (
        f"step 6 quotes none of the actions that name no command, so nothing stops it "
        f"from calling them instructions again: {describing}"
    )
