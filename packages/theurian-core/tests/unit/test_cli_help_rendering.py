"""What every ``--help`` prints, measured by printing it.

Nothing in this suite rendered a ``--help`` before this module, and every check
on help text read the docstring instead. Source and screen were not the same
text: Typer's default parses each help string as Rich markup, which deletes
anything shaped like a style tag. ``theurian setup --help`` said Core arrives
through ``uv tool install 'theurian[daemon]'`` and printed
``uv tool install 'theurian'`` -- with the next sentence still explaining that
the extra is what gives ``theurian daemon start`` a server to run. The
correction that put the extra in the docstring (#82) landed in the file and
never in the output, and no test could tell, because the only reader was the
docstring itself.

**The fix is that ``cli/main.py`` turns markup off, not that one bracket is
escaped.** The escape was written first and reverted: ``TYPER_USE_RICH=0`` is a
documented Typer setting that formats through Click instead, where the escape
survives to the screen and ``uv tool install 'theurian\\[daemon]'`` is not an
installable requirement. Escaping moves the defect between modes rather than
removing it. With ``rich_markup_mode=None`` both settings take the same Click
path, so there is one text and it is the source's.

That makes this module's job two things:

- the source text of every help string reaches its own ``--help`` intact --
  measured here for all 27 commands, and in the other mode by
  ``tests/integration/test_cli_help_without_rich.py``;
- markup being off is *load-bearing*, so it is pinned on the app and
  demonstrated on a throwaway app that turns it back on and loses the strings.

Four shapes this can take, and which check rejects each. Every row was run:

===============================  ====================================
Shape                            Rejected by
===============================  ====================================
markup on, bracket unescaped     the sweep below: source is not on the
                                 rich-mode screen
markup on, bracket escaped       the same sweep -- the *escaped* source
                                 is not on the screen either -- and the
                                 markup-off pin
markup off, bracket escaped      the sweep is green here, because the
                                 escape does reach the screen; the
                                 integration module's installer check
                                 is what fails, on the ground that the
                                 faithful text is not a runnable command
markup turned back on later      the markup-off pin, on all 27
===============================  ====================================

Row three is the reason two modules exist. A sweep asking "does the source
reach the screen" cannot tell a correct docstring from a wrong one that is
printed correctly, and that is exactly the escape this PR reverted.

What Rich would eat, measured against 15.0.0 rather than remembered: ``RE_TAGS``
is ``((\\\\*)\\[([a-z#/@][^[]*?)])``, so ``[`` followed by a **lowercase
letter, ``#``, ``@`` or ``/``** opens a tag. ``'theurian[daemon]'`` and
``'theurian[#fff]'`` both render as ``'theurian'``, while ``[Daemon]``,
``[OPTIONS]`` and ``[0]`` survive. ``/`` is the one that does not lose text: it
opens a *closing* tag, so a bracketed POSIX path makes ``--help`` raise
``MarkupError`` and print nothing at all. Both are demonstrated below, and
neither can reach a user while markup is off.

**Not covered: the packaged console script.** ``CONTRIBUTING.md`` puts
process-boundary checks in ``tests/contract/``, and this renders in process.
Recorded as a decision rather than an omission -- what a contract test would add
is packaging and entry-point coverage, which ``tests/contract/`` already holds
for the JSON surface, and the integration module beside this one already pays
for a subprocess to get the second mode.
"""

from __future__ import annotations

import re
from typing import Annotated, Any, Final, Literal

import pytest
import typer
from typer.main import get_command
from typer.testing import CliRunner

from theurian.cli.main import app

pytestmark = pytest.mark.unit

runner = CliRunner()

#: The console width every render is measured at. Pinned so that no assertion
#: depends on the terminal the suite happens to run under.
RENDER_ENV: Final = {"COLUMNS": "200"}

#: Box-drawing characters, dropped before collapsing. Nothing in the shipped
#: app draws a panel any more; a throwaway app below turns markup back on and
#: does, and it must be compared on the same terms.
_BOX: Final = re.compile(r"[─-╿]")

#: Every command path the walk must reach, counted rather than listed: the
#: adversarial round measured that a floor of 60 over a population of 67 left
#: every one of the 27 individually droppable, because the largest contributes
#: 6 strings. A count of paths has no slack.
#:
#: Remedy when this fails: a command was added or removed, so change the number
#: -- and check that the new one is in the sweep's output, not merely counted.
COMMAND_COUNT: Final = 32

#: Labels the walk must produce, one per branch it has. ``short_help`` and
#: ``epilog`` are set by no command in the tree, so their branches are pinned by
#: :func:`test_rich_markup_would_delete_every_kind_of_help_string` instead.
#:
#: A named label rather than a total: the first draft of the walker used
#: ``isinstance(command, click.Group)``, which is False for a ``TyperGroup``
#: because Typer vendors its own Click. It descended into nothing, swept the
#: root group's three strings and reported the tree clean while the defect was
#: live. Remedy when one fails: the command was renamed, so rename it here.
MUST_FIND: Final = (
    "theurian setup :: help",  # a command's docstring
    "theurian setup :: --dry-run help",  # a typer.Option(help=...)
    "theurian index build :: help",  # reached only by descending into a group
    "theurian project unregister :: help",
)

#: One corpus, four kinds of help string, every one of them bracketed. Module
#: level because ``from __future__ import annotations`` makes Typer evaluate an
#: ``Annotated[...]`` against module globals -- a local would not resolve.
_DOC: Final = "Runs `uv tool install 'theurian[daemon]'` and nothing else."
_FLAG_HELP: Final = "Takes a [daemon] argument."
_SHORT_HELP: Final = "Listed by the parent as short[daemon] help."
_EPILOG: Final = "See also: theurian[daemon] elsewhere."

#: A docstring naming an absolute path in brackets. ``[/usr/bin]`` is a closing
#: tag with no opener, which Rich refuses rather than deletes.
_PATH_DOC: Final = "Writes to [/usr/bin] when asked."


def collapsed(text: str) -> str:
    return " ".join(_BOX.sub(" ", text).split())


def rendered(target: typer.Typer, path: tuple[str, ...]) -> str:
    """One command's ``--help`` as a terminal receives it.

    An empty string when the markup is refused: ``--help`` raises and prints
    nothing, so reporting every string of that command as missing from the
    screen is not a workaround -- it is what happened.
    """
    try:
        result = runner.invoke(target, [*path, "--help"], env=RENDER_ENV, catch_exceptions=False)
    # Deliberately broad: the question is whether the text reached the screen,
    # and every way of not reaching it answers that the same way. Naming
    # `MarkupError` here would couple this to the library it exists to stop
    # modelling, and would let the next refusal escape as a suite error with no
    # command named.
    except Exception:
        return ""
    assert result.exit_code == 0, result.output
    return collapsed(result.output)


def help_strings(
    command: Any,
    root: str = "theurian",
    path: tuple[str, ...] = (),
    parent: tuple[str, ...] | None = None,
) -> list[tuple[tuple[str, ...], str, str]]:
    """Every ``(render path, label, value)`` the app object carries.

    The render path is the command's own for everything it prints about itself,
    and the *parent's* for ``short_help``, which appears only in the group's
    list of subcommands.

    Duck-typed on ``commands`` rather than ``isinstance(command, click.Group)``
    -- see :data:`MUST_FIND` for what that cost.
    """
    name = " ".join((root, *path))
    found = [
        (path, f"{name} :: {field}", value)
        for field in ("help", "epilog")
        if isinstance(value := getattr(command, field, None), str) and value
    ]
    found.extend(
        # `opts[0]`, not `param.name`: the label goes into a failure message,
        # and what the reader has to find in the file is `--dry-run`.
        (path, f"{name} :: {(param.opts or [param.name])[0]} help", param.help)
        for param in command.params
        if isinstance(getattr(param, "help", None), str) and param.help
    )
    short_help = getattr(command, "short_help", None)
    if isinstance(short_help, str) and short_help and parent is not None:
        found.append((parent, f"{name} :: short_help", short_help))
    for sub_name, sub in sorted(getattr(command, "commands", {}).items()):
        found.extend(help_strings(sub, root, (*path, sub_name), path))
    return found


def command_paths(command: Any, path: tuple[str, ...] = ()) -> list[tuple[str, ...]]:
    """Every command in the tree, whether or not it carries a help string.

    Separate from :func:`help_strings` on purpose. That returns *render* paths,
    and ``short_help``'s render path is its parent -- so a command counted from
    those is a command that happened to print something about itself, which is
    not the population the count below is meant to pin.
    """
    found = [path]
    for name, sub in sorted(getattr(command, "commands", {}).items()):
        found.extend(command_paths(sub, (*path, name)))
    return found


def lost_from(
    renders: dict[tuple[str, ...], str], strings: list[tuple[tuple[str, ...], str, str]]
) -> list[str]:
    """Labels of every string that does not appear in the render that shows it."""
    return sorted(label for path, label, value in strings if collapsed(value) not in renders[path])


def _lost(target: typer.Typer, root: str = "theurian") -> list[str]:
    strings = help_strings(get_command(target), root)
    renders = {path: rendered(target, path) for path in {p for p, _label, _value in strings}}
    return lost_from(renders, strings)


def _victim(*, markup: Literal["rich"] | None, doc: str = _DOC) -> typer.Typer:
    """A throwaway app carrying one bracketed string of each kind.

    Two commands rather than one: Typer collapses a single-command app into a
    bare command with no group above it, and ``short_help`` has to be printed
    by a parent. ``add_completion=False`` mirrors ``cli/main.py``; with the
    default, Typer's own two completion options join the population and every
    expectation below has to carry them. ``sibling`` is the control -- walked
    and rendered every time, never expected in a result.

    The docstring is assigned rather than written because these tests need the
    same command under two markup modes and a docstring is not a parameter.
    Typer reads ``__doc__`` when the Click command is built, not when the
    decorator runs -- measured, not assumed.
    """
    victim = typer.Typer(
        help="A group whose own help is fine.",
        no_args_is_help=True,
        add_completion=False,
        rich_markup_mode=markup,
    )

    @victim.command("child", short_help=_SHORT_HELP, epilog=_EPILOG)
    def _child(
        flag: Annotated[bool, typer.Option("--flag", help=_FLAG_HELP)] = False,
    ) -> None: ...

    _child.__doc__ = doc

    @victim.command("sibling")
    def _sibling() -> None:
        """Nothing here is bracketed."""

    return victim


def test_the_walk_reaches_every_command_and_every_kind_of_help_string() -> None:
    """What the sweep is worth, asked before it is asked what it found."""
    strings = help_strings(get_command(app))
    paths = command_paths(get_command(app))
    found = {label for _path, label, _value in strings}

    assert len(paths) == COMMAND_COUNT, (
        f"the walk reaches {len(paths)} commands, not {COMMAND_COUNT}; "
        "if a command was added or removed, update COMMAND_COUNT"
    )
    for path in paths:
        # The `` :: `` is load-bearing: without it `theurian index` is satisfied
        # by `theurian index build`'s labels, and a group could contribute
        # nothing of its own while this passed.
        prefix = " ".join(("theurian", *path)) + " :: "
        assert any(label.startswith(prefix) for label in found), (
            f"`theurian {' '.join(path)}` contributes no help string of its own"
        )
    for label in MUST_FIND:
        assert label in found, f"the walk no longer finds `{label}`; see MUST_FIND"


def test_every_help_string_reaches_the_screen_intact() -> None:
    """The house rule that help text is printed, not interpreted.

    Every module defining commands here states it. That was a convention with
    no check, which is how a square bracket got in and survived review, a merge
    and a release branch.
    """
    lost = _lost(app)

    assert not lost, (
        f"{len(lost)} help string(s) never reach the screen: {lost}. Help text is "
        "printed verbatim only because `cli/main.py` passes `rich_markup_mode=None`; "
        "if that is gone, `[` followed by a lowercase letter, `#` or `@` is deleted "
        "as a Rich style tag and `[/...]` makes --help raise instead of printing."
    )


def test_the_app_leaves_rich_markup_off_on_every_command() -> None:
    """The one line the module above depends on, pinned where it is observable.

    ``rich_markup_mode`` propagates from the root to every group and command,
    so this is one setting rather than seven -- measured on the built tree
    rather than read off the constructor call, because propagation is the part
    that could change.
    """

    def modes(command: Any, path: tuple[str, ...] = ()) -> list[tuple[str, Any]]:
        found = [(" ".join(("theurian", *path)), getattr(command, "rich_markup_mode", "absent"))]
        for name, sub in sorted(getattr(command, "commands", {}).items()):
            found.extend(modes(sub, (*path, name)))
        return found

    on = [name for name, mode in modes(get_command(app)) if mode is not None]

    assert not on, f"Rich markup is back on for {on}; help text is no longer printed verbatim"


def test_rich_markup_would_delete_every_kind_of_help_string() -> None:
    """What turning it back on costs, on an app that is not the real one.

    ``short_help`` and ``epilog`` are set by no command in the tree, so against
    ``app`` alone the walk carries those two branches without ever running them
    against a defect. Here all four run.
    """
    assert _lost(_victim(markup="rich"), root="victim") == [
        "victim child :: --flag help",
        "victim child :: epilog",
        "victim child :: help",
        "victim child :: short_help",
    ]


def test_the_same_corpus_survives_with_markup_off() -> None:
    """The other half of the pair, and the reason the sweep can still fail.

    Once the shipped app has markup off, no help string in it carries a bracket
    that anything would touch -- so a sweep that reported *everything* lost
    would pass the test above and fail nothing else. This is what tells a
    working check from a check that always fires.
    """
    assert _lost(_victim(markup=None), root="victim") == []


def test_a_bracketed_path_would_stop_help_printing_under_markup() -> None:
    """``/`` is the one opener that raises rather than deleting.

    A help string naming an absolute path takes ``--help`` from "prints
    slightly wrong" to "prints nothing", so the sweep has to report it rather
    than let the error escape as a suite failure with no command named.

    All four of the child's strings come back, and the group's own does not:
    the parent lists each subcommand by ``short_help or help``, so an explicit
    ``short_help`` is what keeps it from parsing the broken docstring while
    rendering itself. Remove that and the parent goes down too -- measured, and
    the reason this test names the child's four rather than "everything".
    """
    assert _lost(_victim(markup="rich", doc=_PATH_DOC), root="victim") == [
        "victim child :: --flag help",
        "victim child :: epilog",
        "victim child :: help",
        "victim child :: short_help",
    ]
    assert _lost(_victim(markup=None, doc=_PATH_DOC), root="victim") == []
