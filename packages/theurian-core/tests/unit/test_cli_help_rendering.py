"""What ``--help`` puts on the screen, against what the source says it will.

Nothing in this suite rendered a ``--help`` before this module, and every check
on help text read the docstring instead. Source and screen are not the same
text: Typer runs each help string through ``rich.markup``, which silently eats
anything shaped like a style tag. ``theurian setup --help`` said Core arrives
through ``uv tool install 'theurian[daemon]'`` and printed
``uv tool install 'theurian'`` -- with the next sentence still explaining that
the extra is what gives ``theurian daemon start`` a server to run. The fix that
put the extra in the docstring (#82) never reached the screen, and no test could
tell, because the only reader was the docstring itself.

So the assertions here are on rendered output, and the invocation goes through
Typer's own runner rather than a hand-rolled call to ``rich.markup`` -- what
matters is that the path from docstring to terminal preserves the text, not that
one function in the middle behaves as documented.

Two checks, deliberately different in kind:

- one names the ``[daemon]`` literal, because that is the claim a user acts on;
- one sweeps the whole command tree for *any* help string Rich would alter, so
  the next one is caught before it is written rather than after it ships.

The sweep's population is every string Typer 0.27 passes to
``Text.from_markup`` while rendering a ``--help``: for each command and group,
``help``, ``short_help``, ``epilog``, and every parameter's ``help``. That list
is read off ``typer/rich_utils.py`` -- ``_get_help_text`` takes ``obj.help``,
``_get_parameter_help`` takes ``param.help``, ``_print_commands_panel`` takes
``short_help or help``, and ``rich_format_help`` takes ``epilog``. It is pinned
by :data:`_POPULATION_FLOOR` below, because a walker that finds nothing sweeps
clean.
"""

from __future__ import annotations

import re
from typing import Any, Final

import pytest
from rich.errors import MarkupError
from rich.text import Text
from typer.main import get_command
from typer.testing import CliRunner

from theurian.cli.main import app

pytestmark = pytest.mark.unit

runner = CliRunner()

#: The console width every render here is measured at. Pinned so that a wrap
#: cannot decide whether an assertion passes: without it the runner reports an
#: 80-column terminal, and a literal that happens to straddle column 80 would
#: make this suite depend on the length of the sentence in front of it.
_RENDER_ENV: Final = {"COLUMNS": "200"}

#: The two commands that install a Theurian whose daemon can start, written out
#: rather than imported from ``theurian.domain.extras``. An imported constant
#: would make this test green for whatever that constant later says, which is
#: the drift it exists to catch; ``tests/unit/test_setup_claims.py`` holds the
#: same two literals against the step that reports them.
_INSTALLERS: Final = (
    "uv tool install 'theurian[daemon]'",
    "pipx install 'theurian[daemon]'",
)

#: A backslash-escaped ``[``, which Rich prints as a plain ``[``. The only
#: transformation a help string is allowed to undergo between source and screen.
_ESCAPED_BRACKET: Final = re.compile(r"\\\[")

#: A floor under the sweep's population, which was 67 strings across 27 commands
#: when this was written. Not an equality, so deleting one option's help does not
#: fail here; not absent, because the first draft of this walker used
#: ``isinstance(command, click.Group)``, descended into nothing, swept the root
#: group's three strings and reported the tree clean while the defect was live.
#: A sweep that finds nothing is the failure mode a sweep cannot report itself.
_POPULATION_FLOOR: Final = 60


def _rendered(*path: str) -> str:
    """A command's ``--help`` as it reaches a terminal, whitespace collapsed.

    Collapsing is what lets a multi-word literal be matched contiguously after
    Rich has wrapped the paragraph it lives in.
    """
    result = runner.invoke(app, [*path, "--help"], env=_RENDER_ENV, catch_exceptions=False)
    assert result.exit_code == 0, result.output
    return " ".join(result.output.split())


def _help_strings(command: Any, path: str) -> list[tuple[str, str, str]]:
    """Every ``(command path, field, value)`` Typer renders through Rich markup.

    Duck-typed on ``commands`` rather than ``isinstance(command, click.Group)``:
    Typer 0.27 vendors its own copy of Click, so a ``TyperGroup`` is not an
    instance of the installed ``click.Group`` and the isinstance form silently
    walks nothing.
    """
    found = [
        (path, field, value)
        for field in ("help", "short_help", "epilog")
        if isinstance(value := getattr(command, field, None), str) and value
    ]
    found.extend(
        (path, f"param:{param.name}.help", param.help)
        for param in command.params
        if isinstance(getattr(param, "help", None), str) and param.help
    )
    for name, sub in sorted(getattr(command, "commands", {}).items()):
        found.extend(_help_strings(sub, f"{path} {name}"))
    return found


def test_setup_help_names_the_daemon_extra_it_calls_essential() -> None:
    """The screen must not contradict its own next sentence.

    ``--help`` says the extra is not decoration and that without it
    ``theurian daemon start`` has no server to run. It printed the installer
    without the extra, so a reader following it got exactly the broken install
    the following sentence warns about.
    """
    rendered = _rendered("setup")

    for installer in _INSTALLERS:
        assert installer in rendered, f"`theurian setup --help` does not print {installer}"


def test_no_help_string_loses_text_to_rich_markup() -> None:
    """Rich may unescape ``\\[``, and may do nothing else to a help string.

    Every module that defines commands here states that help text is plain
    prose. That is a house rule with no enforcement, which is how a square
    bracket got in and survived review, a merge and a release branch -- so the
    rule is measured over the whole tree instead of trusted per file.

    ``short_help`` and ``epilog`` are swept while no command in this tree sets
    either. That is not padding: the population is *what Typer renders*, and the
    obvious pair -- a docstring and a ``typer.Option(help=...)``, which is the
    pair https://github.com/theurian/theurian/issues/48 proposes -- is a
    description of where the strings happen to live today. The first command to
    pass ``short_help`` is then swept by a walker nobody had to remember to
    widen.
    """
    strings = _help_strings(get_command(app), "theurian")
    assert len(strings) >= _POPULATION_FLOOR, f"the walker found only {len(strings)} help strings"

    for path, field, value in strings:
        try:
            plain = Text.from_markup(value).plain
        except MarkupError as error:  # pragma: no cover - a defect, not a path
            pytest.fail(f"`{path} --help` ({field}) is unbalanced Rich markup: {error}")
        assert plain == _ESCAPED_BRACKET.sub("[", value), (
            f"`{path} --help` ({field}) loses text to Rich markup; escape the bracket as `\\[`"
        )
