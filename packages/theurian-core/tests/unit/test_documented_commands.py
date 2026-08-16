"""Every ``theurian`` subcommand an instruction names must be one the CLI registers.

The defect class, stated once: **a user-facing string names a ``theurian``
subcommand that is not registered.** A reader -- a person or an agent -- runs
what the string says and gets ``No such command`` and exit 2. Nothing in the
repository joined the prose to the command table, so each face was found by
someone running it.

Three faces are on record. ``/theurian:upgrade`` and Core's compatibility remedy
both told users to run ``theurian upgrade``, which has never existed (#42).
``/theurian:propose`` shelled out to ``theurian propose`` and offered
``theurian propose accept``; ``/theurian:reindex`` shelled out to
``theurian index rebuild``; ``docs/integrations/claude-code.md`` mapped both
slash commands to the same dead invocations (#89). #42's fix pinned one document
by literal, so the two in #89 shipped anyway. This file is the closure that
argument asked for: one mechanism over every instructional surface, resolved
against the app the CLI actually runs, so a command added or removed tomorrow is
covered without anyone remembering to add a test.

**The population key is the command word, not the flag.** These tests resolve
the first word after ``theurian`` -- and, for a registered group, the second --
against :func:`typer.main.get_command`. A flag is deliberately out of scope:
``--dry-run`` on a command that does not accept it is the same shape of defect
and is tracked as #193, and folding it in here would make one failure mean two
things. A reader who wants to attack this test should attack that key first.

The authority is the app object, never a list written here. A hardcoded set is
the thing that goes stale, and it is what made #42's fix incomplete.
"""

from __future__ import annotations

import io
import pathlib
import re
import tokenize
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
from typing import Final, cast

import pytest
import typer.main

from theurian.cli.main import app

REPO_ROOT: Final = pathlib.Path(__file__).resolve().parents[4]

#: Where an instruction lives. Markdown under ``plugins/`` and ``docs/`` is read
#: by users; Python under ``src/`` carries remedies that reach them through error
#: messages and ``--help``. Tests are excluded on purpose: a test that names a
#: dead command fails on its own.
SCANNED_ROOTS: Final = (
    (REPO_ROOT / "plugins", "*.md"),
    (REPO_ROOT / "docs", "*.md"),
    (REPO_ROOT / "packages" / "theurian-core" / "src", "*.py"),
)


# -- the authority ---------------------------------------------------------


def _subcommands(node: object) -> Mapping[str, object]:
    """The child commands of a Click/Typer node, or nothing if it is a leaf.

    Read with :func:`getattr` rather than ``isinstance(node, click.Group)``,
    which is the trap here: Typer 0.27 vendors Click as ``typer._click.core``,
    so a ``TyperGroup`` is not an instance of the ``click.Group`` a test would
    import. That check returns ``False`` for every group, which would leave each
    group with an empty verb set -- and an empty verb set is read below as "a
    leaf command, whose second word is an argument". ``theurian index rebuild``
    would then pass. :func:`test_the_authority_reports_groups_with_their_verbs`
    is what stops that silently.
    """
    commands = getattr(node, "commands", None)
    if isinstance(commands, dict):
        return cast("Mapping[str, object]", commands)
    return {}


def registered_commands() -> Mapping[str, frozenset[str]]:
    """Every registered command word, mapped to its verbs (empty for a leaf)."""
    root = typer.main.get_command(app)
    return {name: frozenset(_subcommands(node)) for name, node in _subcommands(root).items()}


REGISTERED: Final = registered_commands()


# -- extraction ------------------------------------------------------------

#: A fenced block, with its terminator. The opening run of backticks or tildes
#: is back-referenced so a nested fence does not close the outer one.
_FENCE: Final = re.compile(
    r"(?m)^[ \t]*(?P<fence>```+|~~~+)[^\n]*\n(?P<body>.*?)(?:^[ \t]*(?P=fence)[ \t]*$)",
    re.DOTALL,
)

#: An inline code span, of any backtick run length. ``propose.md``'s dead
#: ``theurian propose --json`` sat in a fence and the integrations table's dead
#: ``theurian index rebuild --json`` sat in an inline span, so an extractor that
#: reads only one of the two misses half the class by construction.
_CODE_SPAN: Final = re.compile(
    r"(?<!`)(?P<ticks>`+)(?!`)(?P<body>.+?)(?<!`)(?P=ticks)(?!`)", re.DOTALL
)

#: A Mermaid edge operator. A span holding one is a diagram fragment, not a
#: command line: ``docs/integrations/claude-code.md``'s SessionStart flowchart
#: has the node label ``A{"theurian on PATH?"}``, and
#: ``docs/security/threat-model.md`` quotes that edge inline as
#: ``theurian on PATH? --no--> warn: run /theurian:setup``. In the quoted form
#: the word ``theurian`` opens the span, so command position (below) does not
#: exclude it and the arrow is the only signal left. Nothing that runs in a
#: shell contains ``-->``.
_DIAGRAM_EDGE: Final = re.compile(r"--+>|-\.-+>|==+>|->>")

#: ``theurian`` followed by whitespace. The whitespace is what separates an
#: invocation from a name that merely starts with the same letters:
#: ``theurian.infrastructure`` (a module), ``theurian[daemon]`` (an extra),
#: ``theurian::compat_check`` (a shell function), ``theurian-hashed-char-ngram``
#: (a provider id). All four appear in the scanned files.
_INVOCATION: Final = re.compile(r"\btheurian\b[ \t]")

#: What may precede ``theurian`` and still leave it in command position. A
#: backtick is both shell command substitution and the opening of a code span,
#: which is what keeps a remedy readable when an outer pair of backticks in the
#: same docstring swallows the surrounding prose into the span.
_SEPARATORS: Final = frozenset("|;&(`")

#: A word that runs the word after it. ``$`` is a shell prompt in a transcript.
_RUNNERS: Final = frozenset({"$", "sudo", "exec", "env", "time", "run", "xargs"})

#: A subcommand word. Lowercase because every registered command is, so a
#: capitalised or punctuation-led token -- ``CLI``, ``>>>`` from the
#: ``# >>> theurian >>>`` marker, ``0.1.0.dev0`` -- is prose rather than a claim
#: about the CLI. ``-`` and ``<`` lead a flag and a placeholder respectively.
_SUBCOMMAND: Final = re.compile(r"^[a-z][a-z0-9-]*$")

#: Punctuation a command word picks up from the prose around it.
_MARKUP: Final = "`\"'.,;:!?)]}*"


def _strip_markup(token: str) -> str:
    return token.strip(_MARKUP)


def _alternatives(token: str) -> list[str]:
    """The branches of an alternation cell, escaped or not.

    ``docs/integrations/claude-code.md`` documents two commands as one row each:
    ``theurian daemon\\|project\\|index\\|migrate status --json`` and
    ``theurian migrate validate\\|apply --json``, the pipes escaped because a bare
    one would end the table cell. Every branch is a real invocation and every
    branch has to resolve -- reading the cell as a single opaque word would
    silently exempt six of them.
    """
    return [_strip_markup(part) for part in re.split(r"\\\||\|", token) if part]


def _unwrap(text: str, marker: str) -> str:
    """Join a span that a line wrap split, dropping ``marker`` from continuations.

    Prose reflows and code spans go with it: ``docs/architecture/raptor.md``
    carries ``theurian`` at the end of one line and ``index build --raptor`` at
    the start of the next, inside one span.

    The marker is per-language rather than one pattern covering both, because
    the two overlap on ``>``. ``env_file.py`` documents the shell marker
    ``# >>> theurian >>>`` in a comment wrapped mid-marker, and a blockquote rule
    applied there eats the closing ``>>>`` and reports the invocation as
    ``theurian and here``. Right verdict, unreadable reason.
    """
    return re.sub(rf"[ \t]*\n[ \t]*(?:{marker})?", " ", text).strip()


#: A markdown blockquote continuation, repeated: ADR-0008 nests them two deep.
_BLOCKQUOTE: Final = r"(?:>[ \t]*)+"

#: A Python comment continuation, in both the plain and the Sphinx ``#:`` form.
_COMMENT_MARKER: Final = r"#:?[ \t]?"


def _fence_lines(body: str, first_line: int) -> Iterator[tuple[int, str]]:
    """A fenced block's logical lines, joining shell backslash continuations.

    Line-oriented on purpose, unlike a prose span. Two adjacent lines in a
    transcript are two commands, and joining them invents invocations that are
    not there: ``docs/contributing/development.md`` ends one line with the clone
    URL ``.../theurian/theurian`` and starts the next with ``cd theurian``, and
    ``docs/security/threat-model.md`` follows ``Installed 1 executable: theurian``
    with ``$ echo $?``. A trailing backslash is the one join a shell really makes.
    """
    pending: list[str] = []
    start = first_line
    for offset, raw in enumerate(body.splitlines()):
        if not pending:
            start = first_line + offset
        if raw.rstrip().endswith("\\"):
            pending.append(raw.rstrip()[:-1])
            continue
        pending.append(raw)
        yield start, " ".join(part.strip() for part in pending)
        pending = []
    if pending:
        yield start, " ".join(part.strip() for part in pending)


def fenced_lines(text: str) -> Iterator[tuple[int, str]]:
    """Every logical line of every fenced code block."""
    for fence in _FENCE.finditer(text):
        yield from _fence_lines(fence.group("body"), text.count("\n", 0, fence.start("body")) + 1)


def inline_spans(text: str) -> Iterator[tuple[int, str]]:
    """Every inline code span that a fenced block does not already contain."""
    outside_fences = _FENCE.sub(lambda m: re.sub(r"[^\n]", " ", m.group(0)), text)
    for span in _CODE_SPAN.finditer(outside_fences):
        yield text.count("\n", 0, span.start("body")) + 1, _unwrap(span.group("body"), _BLOCKQUOTE)


def markdown_command_lines(text: str) -> Iterator[tuple[int, str]]:
    """Every fenced line and every inline code span in a markdown document.

    Kept as two generators the scan chains, rather than one function with two
    loops, so :func:`test_the_scan_reaches_every_source_by_both_kinds_of_code_quoting`
    can count them separately without restating how either one works.
    """
    yield from fenced_lines(text)
    yield from inline_spans(text)


def _comment_blocks(source: str) -> Iterator[tuple[int, str]]:
    """Docstrings and string literals whole, and runs of ``#`` comments joined.

    ``tokenize`` emits one token per comment *line*, so a code span wrapped over
    two of them -- ``mcp/search.py`` has ``theurian index`` on one line and
    ``gc`` on the next -- is not a span in either token. Joining the run first is
    what makes it one.
    """
    block: list[str] = []
    start = 0
    previous = -2
    for token in tokenize.generate_tokens(io.StringIO(source).readline):
        if token.type == tokenize.COMMENT:
            if token.start[0] != previous + 1:
                if block:
                    yield start, "\n".join(block)
                block, start = [], token.start[0]
            block.append(token.string)
            previous = token.start[0]
        elif token.type == tokenize.STRING:
            yield token.start[0], token.string
    if block:
        yield start, "\n".join(block)


def python_command_lines(source: str) -> Iterator[tuple[int, str]]:
    """Every code span written inside a Python string, docstring, or comment."""
    for line, chunk in _comment_blocks(source):
        for span in _CODE_SPAN.finditer(chunk):
            body = _unwrap(span.group("body"), _COMMENT_MARKER)
            yield line + chunk.count("\n", 0, span.start()), body


def _at_command_position(head: str) -> bool:
    """Whether what precedes ``theurian`` leaves it in the position of a command.

    This is what keeps prose out of the population without an exception list:
    a Mermaid node label opens with ``{"``, a sequence diagram writes
    ``participant CLI as theurian CLI``, a clone URL ends with ``/``, and
    ``uv tool install 'theurian[daemon]'`` puts it after ``install``. Only a
    line start, a shell separator, a path, or a word that runs its argument
    counts.
    """
    head = head.rstrip()
    if not head:
        return True
    if head[-1] in _SEPARATORS or head.endswith("$(") or head.endswith("/"):
        return True
    return head.split()[-1] in _RUNNERS


def unregistered_in(
    line: str, registry: Mapping[str, frozenset[str]] = REGISTERED
) -> Iterator[str]:
    """Each command word in one line that ``registry`` does not register."""
    if _DIAGRAM_EDGE.search(line):
        return
    for match in _INVOCATION.finditer(line):
        if not _at_command_position(line[: match.start()]):
            continue
        words = line[match.end() :].split()
        yield from _resolve(words[:2], registry)


def _resolve(words: list[str], registry: Mapping[str, frozenset[str]]) -> Iterator[str]:
    first = _strip_markup(words[0]) if words else ""
    second = _strip_markup(words[1]) if len(words) > 1 else ""
    for name in _alternatives(first):
        if not _SUBCOMMAND.match(name):
            continue
        verbs = registry.get(name)
        if verbs is None:
            yield name
            continue
        if not verbs:
            continue
        for verb in _alternatives(second):
            if _SUBCOMMAND.match(verb) and verb not in verbs:
                yield f"{name} {verb}"


# -- the scan --------------------------------------------------------------


@dataclass(frozen=True)
class Invocation:
    """One ``theurian <command>`` a repository file tells somebody to run."""

    path: str
    line: int
    command: str
    span: str

    @property
    def literal(self) -> str:
        """The text an exemption is anchored to, and a reader can grep for."""
        return f"theurian {self.command}"

    @property
    def anchor(self) -> tuple[str, str]:
        return self.path, self.literal


def _scan() -> list[Invocation]:
    found: list[Invocation] = []
    for root, pattern in SCANNED_ROOTS:
        for path in sorted(root.rglob(pattern)):
            text = path.read_text(encoding="utf-8")
            reader = python_command_lines if pattern == "*.py" else markdown_command_lines
            relative = path.relative_to(REPO_ROOT).as_posix()
            for line, span in reader(text):
                for command in unregistered_in(span):
                    found.append(Invocation(relative, line, command, span))
    return found


# -- what is knowingly left ------------------------------------------------


@dataclass(frozen=True)
class Exemption:
    """A mention of an unregistered command that is a record, not an instruction."""

    path: str
    literal: str
    reason: str
    reference: str

    @property
    def anchor(self) -> tuple[str, str]:
        return self.path, self.literal


#: Anchored by file and literal, never by line number: a line number is stale
#: the next time anyone edits above it, and a stale anchor exempts whatever has
#: moved into its place. Each entry is a text that *describes* a dead command
#: rather than telling a reader to run one, which is why it may stay -- and each
#: has to keep matching, or
#: :func:`test_no_recorded_exception_outlives_the_text_it_excuses` removes it.
KNOWN_UNREGISTERED: Final = (
    Exemption(
        path="plugins/claude-code/CHANGELOG.md",
        literal="theurian upgrade",
        reason="the 0.1.1 entry quoting the invocation it removed",
        reference="#42",
    ),
    Exemption(
        path="docs/adr/0013-ai-writes-produce-proposals.md",
        literal="theurian propose",
        reason="the accepted design for a flow Milestone 7 builds; not an instruction yet",
        reference="#89",
    ),
    Exemption(
        path="docs/security/threat-model.md",
        literal="theurian upgrade",
        reason="the corrected-entry history, and the note recording that implementing it "
        "was the rejected alternative",
        reference="#42",
    ),
    Exemption(
        path="docs/security/threat-model.md",
        literal="theurian propose",
        reason="the defect-class description naming the reachable member of the class",
        reference="#89",
    ),
    Exemption(
        path="packages/theurian-core/src/theurian/domain/compatibility.py",
        literal="theurian upgrade",
        reason="comment and docstring mentions recording that upgrade never existed, beside "
        "the remedy that replaced it",
        reference="#42",
    ),
)


def _describe(invocations: Iterable[Invocation]) -> str:
    return "\n".join(
        f"  {found.path}:{found.line}  `{found.literal}`  in: {found.span[:90]}"
        for found in sorted(invocations, key=lambda found: (found.path, found.line))
    )


# -- the tests -------------------------------------------------------------


def test_every_theurian_command_a_document_names_is_registered() -> None:
    """The closure #42 asked for and #89 proved was still open.

    #42 fixed ``theurian upgrade`` in two places and pinned one of them by
    literal. Two more faces shipped afterwards in ``/theurian:propose`` and
    ``/theurian:reindex``, because a per-literal pin cannot cover a word nobody
    has thought of. Resolving every literal against the registered set covers
    ``upgrade``, ``propose``, ``index rebuild``, and whatever the next one turns
    out to be.
    """
    permitted = {exemption.anchor for exemption in KNOWN_UNREGISTERED}

    offending = [found for found in _scan() if found.anchor not in permitted]

    assert not offending, (
        "These instructions name a `theurian` subcommand the CLI does not "
        "register, so a reader following them gets `No such command` and exit 2 "
        f"(#42, #89):\n{_describe(offending)}\n"
        "Fix the text. If it records a dead command rather than telling anyone to "
        "run one, add an Exemption with its reason."
    )


def test_no_recorded_exception_outlives_the_text_it_excuses() -> None:
    """An exemption that stops matching is a permission nobody revoked.

    It also fails when the extractor stops finding anything at all, which is the
    way this file goes quietly useless: a reworded fence, a Typer release that
    changes command introspection, a moved directory. Every exemption going
    unused at once says the scan reached nothing, not that the repository got
    five fixes in one commit.
    """
    found = {invocation.anchor for invocation in _scan()}

    unused = [exemption for exemption in KNOWN_UNREGISTERED if exemption.anchor not in found]

    assert not unused, (
        "These exemptions no longer match anything in the file they name. Either "
        "the text was fixed -- delete the entry -- or the scan has stopped "
        "reading that file, which would make this whole module pass by finding "
        "nothing:\n"
        + "\n".join(f"  {e.path}: `{e.literal}` ({e.reason}, {e.reference})" for e in unused)
    )


def test_the_authority_reports_groups_with_their_verbs() -> None:
    """Without this, the scan passes by mistaking every group for a leaf.

    :func:`_resolve` reads an empty verb set as "a leaf command, whose second
    word is an argument" -- which is true of ``theurian setup --json`` and would
    be catastrophic for ``theurian index rebuild``. Typer 0.27 vendors Click, so
    the obvious ``isinstance(node, click.Group)`` returns ``False`` for every
    group and produces exactly that empty set. Pinned against ``index`` because
    ``index rebuild`` is the face #89 fixed, and it is only a defect at all if
    the group's verbs are visible here.
    """
    assert REGISTERED, "the Typer app registered no commands at all"

    assert REGISTERED.get("index") == frozenset({"build", "gc", "status"}), (
        f"`theurian index` reports verbs {sorted(REGISTERED.get('index') or ())}. If that "
        "set is empty, group introspection has broken and `theurian index rebuild` "
        "now passes this module silently."
    )
    assert REGISTERED["setup"] == frozenset(), "`setup` is a leaf command, not a group"


def test_the_commands_the_defect_class_was_found_through_are_still_absent() -> None:
    """The premise the whole module rests on, kept honest against Milestone 7.

    ``propose`` is planned (ADR-0013), and the day it is registered the entries
    excusing ADR-0013 and the threat model become wrong rather than merely
    stale. This fails then, which is the signal to delete them.
    """
    assert "propose" not in REGISTERED, (
        "`theurian propose` is now registered. Remove the ADR-0013 and threat-model "
        "exemptions, and revisit `/theurian:propose`, which writes the proposal by "
        "hand because the subcommand did not exist (#89)."
    )
    assert "upgrade" not in REGISTERED, (
        "`theurian upgrade` is now registered. The remedy in `domain/compatibility.py` "
        "delegates to `uv tool upgrade` / `pipx upgrade` precisely because it was not (#42)."
    )


#: The shapes the extraction has to get right, each with the exact set of
#: unregistered commands it must report. Every entry is a real line from the
#: scanned files or the shape one of them was fixed from; the empty ones are the
#: prose that must never enter the population, and the non-empty ones are the
#: faces of #42 and #89. Written as data so a reader can see the oracle rather
#: than infer it from a regex.
_EXTRACTION_ORACLE: Final = (
    pytest.param("theurian propose --json", ("propose",), id="the-fenced-propose-face"),
    pytest.param(
        "theurian propose accept <proposal-id>", ("propose",), id="the-propose-accept-face"
    ),
    pytest.param("theurian index rebuild --json", ("index rebuild",), id="the-reindex-face"),
    pytest.param("theurian index build --json", (), id="a-live-command"),
    pytest.param("theurian migrate validate --json", (), id="a-live-group-verb"),
    pytest.param("uv run theurian version --json", (), id="behind-a-runner"),
    pytest.param("$ theurian doctor --json", (), id="behind-a-prompt"),
    pytest.param(
        "/usr/local/bin/theurian frobnicate", ("frobnicate",), id="invoked-by-absolute-path"
    ),
    pytest.param(
        "theurian daemon\\|project\\|index\\|migrate status --json", (), id="a-live-alternation"
    ),
    pytest.param("theurian migrate validate\\|apply --json", (), id="a-live-alternation-of-verbs"),
    pytest.param(
        "theurian daemon\\|frobnicate status --json",
        ("frobnicate",),
        id="one-dead-branch-of-an-alternation",
    ),
    pytest.param(
        "theurian migrate validate\\|reticulate --json",
        ("migrate reticulate",),
        id="one-dead-verb-of-an-alternation",
    ),
    pytest.param('S["Session starts"] --> A{"theurian on PATH?"}', (), id="a-mermaid-node-label"),
    pytest.param(
        "theurian on PATH? --no--> warn: run /theurian:setup", (), id="a-quoted-flowchart-edge"
    ),
    pytest.param("theurian.infrastructure", (), id="a-module-path"),
    pytest.param("theurian[daemon]", (), id="a-packaging-extra"),
    pytest.param("theurian::compat_check", (), id="a-shell-function"),
    pytest.param("# >>> theurian >>>", (), id="a-gitignore-marker"),
    pytest.param("participant CLI as theurian CLI", (), id="prose-naming-the-binary"),
    pytest.param(
        "an absolute path to the theurian executable", (), id="prose-whose-next-word-is-lowercase"
    ),
    pytest.param("theurian index", (), id="a-group-named-without-a-verb"),
    pytest.param("theurian --version", (), id="a-flag-rather-than-a-subcommand"),
    pytest.param("theurian <verb> --json", (), id="a-placeholder-rather-than-a-subcommand"),
    pytest.param(
        "Run `theurian frobnicate` to fix it", ("frobnicate",), id="inside-nested-quoting"
    ),
)


@pytest.mark.parametrize(("line", "expected"), _EXTRACTION_ORACLE)
def test_the_extractor_reports_exactly_the_dead_commands_in_a_line(
    line: str, expected: tuple[str, ...]
) -> None:
    """An extractor nobody has proved works is a test that always passes.

    The scan above reports nothing today, so on its own it cannot distinguish
    "the repository is clean" from "the regex matches nothing". Both directions
    are pinned here: the faces of #42 and #89 must be reported, and the prose
    shapes that motivated every filter must not be.
    """
    assert tuple(unregistered_in(line)) == expected


#: A markdown document holding one of every shape the reader has to survive,
#: with the dead commands at known lines. Deliberately not a real file: the real
#: ones are clean, so nothing in this repository exercises the *reporting* half
#: of the reader, and a change that stopped it reading fenced blocks altogether
#: kept the whole module green. That mutation is what this fixture exists for --
#: ``propose.md``'s dead ``theurian propose --json`` sat in a fence, so the fence
#: path is the one that carried the motivating face of #89.
_MARKDOWN_FIXTURE: Final = """\
# A command document

Prose naming the `theurian` binary, plus a live `theurian index build --json`.

```sh
theurian propose --json
theurian index \\
    rebuild --json
```

The integrations table's dead row: `theurian index rebuild --json`, and one
the line wrap split: `theurian
propose accept <proposal-id>`.

> > A nested blockquote wraps one too: `theurian index
> > rebuild --json`.

```mermaid
S["Session starts"] --> A{"theurian on PATH?"}
```
"""

#: ``(line, command)`` for every dead command in :data:`_MARKDOWN_FIXTURE`.
#: Line 6 is fenced. Line 7 is fenced across a shell backslash continuation, and
#: reads ``index`` with a trailing backslash if the join is dropped. Line 11 is
#: an inline span. Line 12 wraps *between* ``theurian`` and its command word, so
#: it disappears entirely unless the span is unwrapped first. Line 15 wraps
#: inside a doubly nested blockquote, the shape ADR-0008 has. Nothing comes from
#: the Mermaid block, and nothing from the live invocation on line 3.
_MARKDOWN_FIXTURE_FINDINGS: Final = {
    (6, "propose"),
    (7, "index rebuild"),
    (11, "index rebuild"),
    (12, "propose"),
    (15, "index rebuild"),
}

#: The same, for Python. The comment run is the shape ``mcp/search.py`` has: one
#: code span split over two ``#`` lines, which ``tokenize`` hands over as two
#: separate tokens and which is therefore a span in neither of them.
_PYTHON_FIXTURE: Final = '''\
"""A module docstring naming `theurian index build`."""

#: A comment run whose code span the line wrap split: `theurian index
#: rebuild` is the face #89 fixed.
REMEDY = "Run `theurian propose` to draft one."
'''

_PYTHON_FIXTURE_FINDINGS: Final = {(3, "index rebuild"), (5, "propose")}


def _read(document: str, reader: str) -> set[tuple[int, str]]:
    lines = python_command_lines if reader == "python" else markdown_command_lines
    return {(line, command) for line, span in lines(document) for command in unregistered_in(span)}


def test_a_markdown_document_yields_its_dead_commands_and_their_lines() -> None:
    """Both extraction paths, exercised on text that actually contains a defect.

    :func:`test_the_extractor_reports_exactly_the_dead_commands_in_a_line` hands
    single lines straight to the resolver, so it never runs the reader that
    finds them -- measured: deleting the fenced-block branch outright left all
    28 tests in this file green. The line numbers are pinned too, because a
    failure message that names the wrong line sends the reader to the wrong
    place.
    """
    assert _read(_MARKDOWN_FIXTURE, "markdown") == _MARKDOWN_FIXTURE_FINDINGS


def test_a_python_module_yields_dead_commands_from_docstrings_and_comment_runs() -> None:
    """The same for Core's own source, where a remedy reaches a user as an error."""
    assert _read(_PYTHON_FIXTURE, "python") == _PYTHON_FIXTURE_FINDINGS


def test_a_continuation_marker_is_stripped_by_language_and_not_by_shape() -> None:
    """``>`` is a blockquote in markdown and content in a Python comment.

    One combined pattern gets the same verdict either way -- neither span is in
    command position -- and reports it against text that was never written:
    ``env_file.py`` documents ``# >>> theurian >>>`` in a comment wrapped between
    the name and the closing marker, and blockquote stripping turns the span into
    ``theurian and here``. This pins the reported text, because a failure naming
    a line that does not say what the failure claims costs the reader the trip.
    """
    comment_run = 'echo "everything between # >>> theurian\n#: >>> and here"'
    nested_quote = "theurian index\n> > rebuild --json"

    assert _unwrap(comment_run, _COMMENT_MARKER) == (
        'echo "everything between # >>> theurian >>> and here"'
    )
    assert _unwrap(nested_quote, _BLOCKQUOTE) == "theurian index rebuild --json"


def test_the_scan_reaches_every_source_by_both_kinds_of_code_quoting() -> None:
    """The plumbing over the real tree, checked separately from the verdict.

    The fixtures above prove the reader reports what it is given; this proves it
    is given the repository. A glob that stops matching, a fence that stops
    closing, or a ``tokenize`` change would otherwise make every assertion here
    pass by reading nothing at all.
    """
    counts = dict.fromkeys(("plugins", "docs", "src", "fenced", "inline"), 0)
    for root, pattern in SCANNED_ROOTS:
        for path in sorted(root.rglob(pattern)):
            text = path.read_text(encoding="utf-8")
            if pattern == "*.py":
                counts["src"] += _count_invocations(python_command_lines(text))
                continue
            fenced = _count_invocations(fenced_lines(text))
            inline = _count_invocations(inline_spans(text))
            counts["fenced"] += fenced
            counts["inline"] += inline
            counts[root.name] += fenced + inline

    assert all(counts.values()), (
        f"a scanned root or a kind of code quoting yielded no `theurian <command>` "
        f"at all: {counts}. Every assertion in this module would pass on an empty "
        "population."
    )


def _count_invocations(spans: Iterable[tuple[int, str]]) -> int:
    """How many ``theurian <word>`` sites reached the resolver, dead or alive."""
    return sum(
        1
        for _, span in spans
        for match in _INVOCATION.finditer(span)
        if _at_command_position(span[: match.start()])
    )
