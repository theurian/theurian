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
argument asked for: one mechanism over the instructional surfaces named below,
resolved against the app the CLI actually runs, so a command added or removed
tomorrow is covered without anyone remembering to add a test.

The population
--------------
Four readers over four file families, walked from the repository root:

=================================  ==========================================
``*.md`` anywhere                  fenced blocks, inline code spans, YAML
                                   frontmatter values, and any line whose
                                   own content is a command
``*.py`` under Core's ``src/``     ``#`` comment runs, string literals, and
                                   f-strings
``*.json`` anywhere                code spans inside string literals
``*.sh`` / ``*.yml`` / ``*.yaml``  every logical line
=================================  ==========================================

The first version of this file read three roots -- ``plugins/**/*.md``,
``docs/**/*.md`` and Core's ``src/**/*.py`` -- while its docstring claimed
"every instructional surface". Measured against that claim, 193 command-position
sites lived outside it: the README quickstart, ``SECURITY.md``, ``CLAUDE.md``,
Core's ``CHANGELOG.md``, the packaging and schema READMEs, the JSON schemas'
remedy strings, the issue templates, the release workflows, and the plugin's two
shell scripts -- which do not instruct anybody, they *execute*.

What is deliberately unread is listed in :data:`UNREAD`, and
:func:`test_no_file_that_names_a_command_escapes_the_scan` walks every file in
the repository to prove the list is complete: a surface added tomorrow in a file
type nothing here reads fails that test rather than escaping quietly.

The walk is over what the repository *ships*, which is not the same as what is
on disk -- see :func:`_walked`. A suite run under the mutation harness leaves
twelve thousand fixture files inside the tree, and reading them turned the
unmutated control RED.

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

import functools
import io
import json
import os
import pathlib
import re
import tokenize
from collections import Counter
from collections.abc import Callable, Iterable, Iterator, Mapping
from dataclasses import dataclass, field
from typing import Final, NamedTuple, cast

import pytest
import typer.main

from theurian.cli.main import app

REPO_ROOT: Final = pathlib.Path(__file__).resolve().parents[4]


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


# -- what a reader hands to the resolver -----------------------------------


class Span(NamedTuple):
    """One quoted run of text, and the line it starts on.

    ``prose`` marks the one reader whose text has no shell line for a command to
    stand at the start of -- see :func:`frontmatter_values`.
    """

    line: int
    text: str
    prose: bool = False


Reader = Callable[[str], Iterator[Span]]


# -- extraction: shared vocabulary -----------------------------------------

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
#: (a provider id), ``Bash(theurian:*)`` (a plugin permission grant), and
#: ``.theurian/proposals/`` (a directory). All six appear in the scanned files.
_INVOCATION: Final = re.compile(r"\btheurian\b[ \t]")

#: What may precede ``theurian`` and still leave it in command position. A
#: backtick is both shell command substitution and the opening of a code span,
#: which is what keeps a remedy readable when an outer pair of backticks in the
#: same docstring swallows the surrounding prose into the span.
_SEPARATORS: Final = frozenset("|;&(`")

#: A word that runs the word after it. ``$`` is a shell prompt in a transcript.
_RUNNERS: Final = frozenset({"$", "sudo", "exec", "env", "time", "run", "xargs"})

#: A shell variable assignment written as a prefix, which leaves the next word
#: in command position: ``THEURIAN_DATA_DIR="$dir" theurian index build``. The
#: leading character class refuses ``--flag=value``, which is an argument to
#: whatever came before it and not a prefix at all.
_ENV_ASSIGNMENT: Final = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")

#: A subcommand word. Lowercase because every registered command is, so a
#: capitalised or punctuation-led token -- ``CLI``, ``>>>`` from the
#: ``# >>> theurian >>>`` marker, ``0.1.0.dev0`` -- is prose rather than a claim
#: about the CLI. ``-`` and ``<`` lead a flag and a placeholder respectively,
#: and ``{}`` is what :func:`_fstring_blocks` writes where an interpolation was.
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


#: A Python comment continuation, in both the plain and the Sphinx ``#:`` form.
_COMMENT_MARKER: Final = r"#:?[ \t]?"


def _unwrap(text: str, marker: str = "") -> str:
    """Join a span that a line wrap split, dropping ``marker`` from continuations.

    Prose reflows and code spans go with it: ``docs/architecture/raptor.md``
    carries ``theurian`` at the end of one line and ``index build --raptor`` at
    the start of the next, inside one span.

    Only Python needs a marker. Markdown's blockquote ``>`` is gone before any
    span is read -- :func:`_flatten_blockquotes` removes it from the document,
    not from the span -- and the two must not share one pattern, because they
    overlap on ``>``: ``env_file.py`` documents the shell marker
    ``# >>> theurian >>>`` in a comment wrapped mid-marker, and a blockquote rule
    applied there eats the closing ``>>>`` and reports the invocation as
    ``theurian and here``. Right verdict, unreadable reason.
    """
    return re.sub(rf"[ \t]*\n[ \t]*(?:{marker})?", " ", text).strip()


# -- extraction: markdown --------------------------------------------------

#: One markdown blockquote marker, with the space that conventionally follows.
_QUOTE_MARKER: Final = re.compile(r"[ \t]*>[ \t]?")


def _unquote_line(line: str) -> str:
    """Strip every leading blockquote marker; ADR-0008 nests them two deep."""
    position = 0
    while match := _QUOTE_MARKER.match(line, position):
        position = match.end()
    return line[position:]


def _flatten_blockquotes(text: str) -> str:
    """Remove blockquote markers from a document, keeping every line and number.

    Done to the whole document *before* fences are found, which is the only
    order that works. :data:`_FENCE` anchors the opening run at the start of a
    line, so ``> ```sh `` was never a fence: the block fell through to the inline
    span reader, which joined its lines into one string where only the first
    token stands in command position. ``docs/security/threat-model.md`` and
    ``docs/adr/0008-raptor-forest.md`` hold 14 such lines between them, and two
    mutations planting a dead command inside them survived the whole suite.

    Stripping the marker from a fenced block's *content* would be wrong -- a
    fence at quote depth zero may legitimately contain a line beginning with
    ``>``. :func:`test_flattening_blockquotes_does_not_touch_fenced_content`
    is what makes the shortcut safe rather than merely convenient.
    """
    return "\n".join(_unquote_line(line) for line in text.split("\n"))


def _fence_lines(body: str, first_line: int) -> Iterator[Span]:
    """Logical lines, joining shell backslash continuations.

    Line-oriented on purpose, unlike a prose span. Two adjacent lines in a
    transcript are two commands, and joining them invents invocations that are
    not there: ``docs/contributing/development.md`` ends one line with the clone
    URL ``.../theurian/theurian`` and starts the next with ``cd theurian``, and
    ``docs/security/threat-model.md`` follows ``Installed 1 executable: theurian``
    with ``$ echo $?``. A trailing backslash is the one join a shell really makes.

    Shared by :func:`fenced_lines` and :func:`plain_command_lines`, because a
    fenced block and a shell script are the same thing read the same way.
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
        yield Span(start, " ".join(part.strip() for part in pending))
        pending = []
    if pending:
        yield Span(start, " ".join(part.strip() for part in pending))


#: The YAML frontmatter block a document opens with, if it has one.
_FRONTMATTER: Final = re.compile(r"\A---[ \t]*\n(?P<body>.*?)\n---[ \t]*(?:\n|\Z)", re.DOTALL)


def _blanked(text: str, pattern: re.Pattern[str]) -> str:
    """``text`` with every match blanked out, keeping every offset and line."""
    return pattern.sub(lambda match: re.sub(r"[^\n]", " ", match.group(0)), text)


def _prose_of(text: str) -> str:
    """``text`` with fenced blocks and frontmatter removed from view.

    The four markdown arms partition the document rather than overlapping, so
    that the occurrence count an :class:`Exemption` carries is the number of
    times the text was written and not the number of readers that can see it.
    Frontmatter is blanked here because :func:`frontmatter_values` reads it as
    prose, which finds strictly more than an inline span in the same line would.
    """
    return _blanked(_blanked(text, _FENCE), _FRONTMATTER)


def fenced_lines(text: str) -> Iterator[Span]:
    """Every logical line of every fenced code block."""
    for fence in _FENCE.finditer(text):
        yield from _fence_lines(fence.group("body"), text.count("\n", 0, fence.start("body")) + 1)


def inline_spans(text: str) -> Iterator[Span]:
    """Every inline code span outside a fenced block and outside frontmatter."""
    for span in _CODE_SPAN.finditer(_prose_of(text)):
        yield Span(text.count("\n", 0, span.start("body")) + 1, _unwrap(span.group("body")))


#: A line whose own content is an invocation -- no fence, no backticks. This is
#: the four-space indented code block, and equally a command written into a
#: paragraph or a list item on its own line. Measured: the repository has none
#: today, so :data:`_MARKDOWN_FIXTURE` is the only thing that exercises it, and
#: reporting it costs nothing until someone writes one.
_BARE_COMMAND: Final = re.compile(r"(?m)^[ \t]*(?P<body>theurian[ \t][^\n]*?)[ \t]*$")


def bare_command_lines(text: str) -> Iterator[Span]:
    """Every line outside a fence whose content is itself a command."""
    for match in _BARE_COMMAND.finditer(_prose_of(text)):
        yield Span(text.count("\n", 0, match.start("body")) + 1, match.group("body"))


def frontmatter_values(text: str) -> Iterator[Span]:
    """Every line of a document's YAML frontmatter, read as prose.

    Claude Code *displays* a command document's ``description`` to the user, so a
    description naming a dead command is an instruction like any other -- and
    frontmatter is neither fenced nor backticked, so nothing else here reads it.
    A mutation rewording ``reindex.md``'s description to "Runs theurian index
    rebuild" survived the whole suite.

    Yielded with ``prose=True``: a frontmatter value is one field of prose, not a
    shell line, so there is no line start or pipe for a command to stand after
    and command position cannot be computed. Every ``theurian <word>`` in one is
    therefore read as naming a command. The cost is that a value writing
    ``theurian`` immediately before an ordinary lowercase English word is
    reported; the benefit is that the field users actually see is covered.
    Measured: no frontmatter value in the repository does that today -- the
    plugin's 12 command documents and the 8 agent definitions write ``Theurian``,
    ``theurian-core`` or ``Bash(theurian:*)``, none of which this can match.
    """
    block = _FRONTMATTER.match(text)
    if block is None:
        return
    first = text.count("\n", 0, block.start("body")) + 1
    for offset, line in enumerate(block.group("body").splitlines()):
        yield Span(first + offset, line, prose=True)


def markdown_command_lines(text: str) -> Iterator[Span]:
    """Every command line a markdown document holds, by all four quotings.

    Kept as four generators the scan chains, rather than one function with four
    loops, so :func:`test_the_scan_reaches_every_arm_of_every_reader` can count
    them separately without restating how any of them works.
    """
    flattened = _flatten_blockquotes(text)
    yield from fenced_lines(flattened)
    yield from inline_spans(flattened)
    yield from bare_command_lines(flattened)
    yield from frontmatter_values(flattened)


# -- extraction: python ----------------------------------------------------


def _comment_blocks(source: str) -> Iterator[tuple[int, str]]:
    """String literals whole, and runs of ``#`` comments joined.

    ``tokenize`` emits one token per comment *line*, so a code span wrapped over
    two of them -- ``mcp/search.py`` has ``theurian index`` on one line and
    ``gc`` on the next -- is not a span in either token. Joining the run first is
    what makes it one.

    f-strings are not ``STRING`` tokens and are read by :func:`_fstring_blocks`.
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


@dataclass
class _FStringBuilder:
    """One f-string being reassembled from its tokens."""

    line: int
    row: int
    parts: list[str] = field(default_factory=list)
    pending: bool = False

    def take(self, start_row: int, text: str, end_row: int) -> None:
        """Append the next literal fragment, standing in for what preceded it."""
        if self.pending:
            self.parts.append("{}" + "\n" * (start_row - self.row))
            self.pending = False
        self.parts.append(text)
        self.row = end_row


def _fstring_blocks(source: str) -> Iterator[tuple[int, str]]:
    """Each f-string's literal text, with ``{}`` where an interpolation was.

    PEP 701 changed the tokenizer in Python 3.12: an f-string is no longer one
    ``STRING`` token but a ``FSTRING_START`` / ``FSTRING_MIDDLE`` /
    ``FSTRING_END`` run. :func:`_comment_blocks` reads ``STRING``, so every one
    of Core's 403 f-strings was invisible to it -- including 37 that carry a
    backticked ``theurian <command>`` remedy to a user through an error message.
    A mutation turning ``index_purge.py``'s ``theurian index build`` remedy into
    ``theurian index rebuild`` survived the whole suite.

    An interpolation becomes ``{}`` rather than being dropped, because dropping
    it invents commands: ``f"theurian {verb} build"`` would read as
    ``theurian build``. ``{}`` is not a ``_SUBCOMMAND``, so the invocation is
    seen and refused, while ``f"theurian index build --project {pid}"`` still
    resolves. The newlines an interpolation spanned are kept with it, so a span
    after a multi-line interpolation is still reported at its own line.
    """
    stack: list[_FStringBuilder] = []
    for token in tokenize.generate_tokens(io.StringIO(source).readline):
        if token.type == tokenize.FSTRING_START:
            if stack:
                stack[-1].pending = True
            stack.append(_FStringBuilder(line=token.start[0], row=token.end[0]))
        elif not stack:
            continue
        elif token.type == tokenize.FSTRING_MIDDLE:
            stack[-1].take(token.start[0], token.string, token.end[0])
        elif token.type == tokenize.FSTRING_END:
            done = stack.pop()
            done.take(token.start[0], "", token.end[0])
            yield done.line, "".join(done.parts)
            if stack:
                stack[-1].pending = True
        else:
            stack[-1].pending = True


def python_command_lines(source: str) -> Iterator[Span]:
    """Every code span written inside a Python comment, string, or f-string."""
    for line, chunk in sorted([*_comment_blocks(source), *_fstring_blocks(source)]):
        for span in _CODE_SPAN.finditer(chunk):
            body = _unwrap(span.group("body"), _COMMENT_MARKER)
            yield Span(line + chunk.count("\n", 0, span.start()), body)


# -- extraction: json ------------------------------------------------------

#: A JSON string literal, escapes included. Matched against the raw text rather
#: than walking a parsed document, because the line number is what sends a
#: reader to the right place and :func:`json.loads` does not keep it.
_JSON_STRING: Final = re.compile(r'"(?:[^"\\]|\\.)*"')


def json_command_lines(source: str) -> Iterator[Span]:
    """Every code span written inside a JSON string.

    The schemas' ``description`` fields carry remedies to users through MCP tool
    errors and ``knowledge.status`` -- ``schemas/`` holds 19 such invocations,
    and none of them was read before. A JSON string cannot contain a raw
    newline, so the line a literal starts on is the line the span is on.

    Code spans, not whole strings, for the same reason as a Python string
    literal: a description is prose, and reading it whole reports the word after
    ``theurian`` in a sentence like "a CI image running `theurian migrate`
    should not carry a web server". Measured: every one of the 19 is backticked.
    """
    for literal in _JSON_STRING.finditer(source):
        value = cast("str", json.loads(literal.group()))
        line = source.count("\n", 0, literal.start()) + 1
        for span in _CODE_SPAN.finditer(value):
            yield Span(line, _unwrap(span.group("body")))


# -- extraction: scripts and configuration ---------------------------------


def plain_command_lines(source: str) -> Iterator[Span]:
    """Every logical line of a shell script or a YAML file.

    ``plugins/claude-code/scripts/lib.sh`` and ``session-start.sh`` do not
    instruct anybody: they *run* ``theurian compat check``,
    ``theurian daemon start`` and ``theurian project status`` in the user's
    session. ``.github/workflows/`` runs three more, and
    ``.github/ISSUE_TEMPLATE/bug_report.yml`` asks a reporter to run
    ``theurian doctor --report --json`` before they file anything.

    Read as lines rather than as YAML, so that a workflow's ``run:`` block, a
    block scalar's markdown and a comment are all one thing. Command position
    does the filtering: measured over every ``*.sh``, ``*.yml`` and ``*.yaml``
    in the repository, this yields 11 sites and no false one.
    """
    yield from _fence_lines(source, 1)


# -- resolution ------------------------------------------------------------


def _at_command_position(head: str) -> bool:
    """Whether what precedes ``theurian`` leaves it in the position of a command.

    This is what keeps prose out of the population without an exception list:
    a Mermaid node label opens with ``{"``, a sequence diagram writes
    ``participant CLI as theurian CLI``, a clone URL ends with ``/``, and
    ``uv tool install 'theurian[daemon]'`` puts it after ``install``. Only a
    line start, a shell separator, a path, a variable assignment prefix, or a
    word that runs its argument counts.
    """
    head = head.rstrip()
    if not head:
        return True
    if head[-1] in _SEPARATORS or head.endswith("$(") or head.endswith("/"):
        return True
    last = head.split()[-1]
    return last in _RUNNERS or _ENV_ASSIGNMENT.match(last) is not None


def unregistered_in(
    line: str, registry: Mapping[str, frozenset[str]] = REGISTERED, *, prose: bool = False
) -> Iterator[str]:
    """Each command word in one line that ``registry`` does not register.

    ``prose`` drops the command-position filter, for text that has no command
    position to be in. Only :func:`frontmatter_values` sets it.
    """
    if _DIAGRAM_EDGE.search(line):
        return
    for match in _INVOCATION.finditer(line):
        if not prose and not _at_command_position(line[: match.start()]):
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
class Surface:
    """One family of files, and the reader that turns it into command lines."""

    label: str
    root: pathlib.Path
    suffixes: frozenset[str]
    reader: Reader


SCANNED_SURFACES: Final = (
    Surface("markdown", REPO_ROOT, frozenset({".md"}), markdown_command_lines),
    Surface(
        "python",
        REPO_ROOT / "packages" / "theurian-core" / "src",
        frozenset({".py"}),
        python_command_lines,
    ),
    Surface("json", REPO_ROOT, frozenset({".json"}), json_command_lines),
    Surface("plain", REPO_ROOT, frozenset({".sh", ".yml", ".yaml"}), plain_command_lines),
)

#: The dot directories this repository *ships*. Every other one is somebody's
#: tooling state and is not walked, which is a rule rather than a list because a
#: list of the ones seen so far is a list that keeps being wrong: the mutation
#: harness runs the suite with ``TMPDIR`` inside the copied tree, so a run there
#: put 12,734 fixture files -- whole ``.theurian`` project directories, some of
#: them not even UTF-8 -- under ``.mutate-tmp/``, and the scan read them. The
#: control run went RED and every verdict in the batch with it.
#:
#: The residual is stated rather than hidden: a *fourth* dot directory that
#: ships instructions would escape the scan, and nothing here would say so. The
#: list is three entries long and sits beside the rule for that reason.
SHIPPED_DOT_DIRECTORIES: Final = frozenset({".github", ".claude", ".theurian"})

#: Directory names the walk never enters even though they do not start with a
#: dot. Build and coverage output, vendored packages, and -- the one that is not
#: obvious -- ``worktrees``, because ``.claude/worktrees/`` is where this machine
#: keeps agent checkouts of the repository itself. Walking one would scan a
#: second copy of every file below, which both doubles the population and makes
#: the result depend on who else is working today.
PRUNED_DIRECTORIES: Final = frozenset(
    {"__pycache__", "node_modules", "htmlcov", "dist", "site", "worktrees"}
)


def _walked(names: Iterable[str]) -> list[str]:
    """The subdirectories of one directory that are part of the repository."""
    return sorted(
        name
        for name in names
        if name not in PRUNED_DIRECTORIES
        and (not name.startswith(".") or name in SHIPPED_DOT_DIRECTORIES)
    )


@dataclass(frozen=True)
class Unread:
    """A path prefix no reader looks at, and the reason that is safe."""

    prefix: str
    reason: str


#: The whole of the exclusion. Everything else in the repository is either read
#: by a :data:`SCANNED_SURFACES` entry or holds no ``theurian <command>`` at all,
#: and :func:`test_no_file_that_names_a_command_escapes_the_scan` is what turns
#: that second half from a claim into a check.
UNREAD: Final = (
    Unread(
        prefix="packages/theurian-core/tests/",
        reason="a test that names a dead command and runs it fails on its own, and the "
        "fixtures in this very file name dead commands on purpose",
    ),
    Unread(
        prefix="tests/",
        reason="the same, for the end-to-end tree",
    ),
)


def _is_unread(relative: str) -> bool:
    return any(relative.startswith(entry.prefix) for entry in UNREAD)


def _files(root: pathlib.Path, suffixes: frozenset[str]) -> Iterator[pathlib.Path]:
    """Every file of those suffixes under ``root``, in a fixed order.

    ``os.walk`` rather than :meth:`~pathlib.Path.rglob` because the directories
    have to be pruned *during* the walk. Filtering afterwards still descends
    into a 149 MB virtualenv, into every sibling worktree, and into the twelve
    thousand fixture files a suite run leaves under a redirected ``TMPDIR``.
    """
    for base, directories, names in os.walk(root):
        directories[:] = _walked(directories)
        for name in sorted(names):
            path = pathlib.Path(base) / name
            if path.suffix in suffixes and not _is_unread(path.relative_to(REPO_ROOT).as_posix()):
                yield path


def _text(path: pathlib.Path) -> str:
    """Read a file the scan is responsible for, naming it if it is not UTF-8.

    Bare :meth:`~pathlib.Path.read_text` raises a ``UnicodeDecodeError`` that
    names the byte and not the file, which is a long way from the file when the
    walk covers the whole repository.
    """
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError as error:
        message = f"{path.relative_to(REPO_ROOT).as_posix()} is not UTF-8 ({error})"
        raise AssertionError(message) from error


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

    @property
    def excerpt(self) -> str:
        """The quoted text, whitespace-normalised, as an exemption records it."""
        return " ".join(self.span.split())


@functools.cache
def _scan() -> tuple[Invocation, ...]:
    """Every unregistered invocation in the repository, one entry per occurrence.

    Not deduplicated, and that is what makes an :class:`Exemption`'s occurrence
    count mean something: ``plugins/claude-code/CHANGELOG.md`` names two dead
    invocations on line 218 and the threat model names the same one twice on line
    1106, so collapsing by ``(path, line, command)`` would license a third. The
    readers are made not to overlap instead -- see :func:`_prose_of`.

    Cached because four tests want the same answer and the walk reads every file
    in the repository. Deterministic for the same reason it is cacheable: the
    surfaces are ordered, :func:`_files` sorts, and each reader is a generator
    over one text.
    """
    return tuple(
        Invocation(relative, span.line, command, span.text)
        for surface in SCANNED_SURFACES
        for path in _files(surface.root, surface.suffixes)
        for relative in (path.relative_to(REPO_ROOT).as_posix(),)
        for span in surface.reader(_text(path))
        for command in unregistered_in(span.text, prose=span.prose)
    )


# -- what is knowingly left ------------------------------------------------


@dataclass(frozen=True)
class Exemption:
    """A mention of an unregistered command that is a record, not an instruction.

    ``excused`` is the whole bound, and the reason this is not just a pair of
    strings. Anchored by file and literal alone, an exemption covered every
    occurrence of that literal in that file *including ones written after it was
    granted*: five injections -- among them re-adding #42's own remedy to
    ``domain/compatibility.py``, and a new CHANGELOG entry telling users to run
    ``theurian upgrade`` -- were each swallowed with the whole suite green.

    So the permission names the texts it excuses, once per occurrence. A sixth
    occurrence of a literal excused five times is a new instruction and fails,
    whatever it says; a reworded excusal fails too, which is the point -- the
    question "is this still a record rather than an instruction" is exactly what
    a reviewer should be made to answer again.

    Line numbers stay out of it. A line number is stale the next time anyone
    edits above it, and a stale anchor exempts whatever has moved into its place.
    """

    path: str
    literal: str
    excused: tuple[str, ...]
    reason: str
    reference: str

    @property
    def anchor(self) -> tuple[str, str]:
        return self.path, self.literal


#: Each entry is a text that *describes* a dead command rather than telling a
#: reader to run one, which is why it may stay -- and each has to keep matching,
#: or :func:`test_no_recorded_exception_outlives_the_text_it_excuses` removes it.
KNOWN_UNREGISTERED: Final = (
    Exemption(
        path="docs/adr/0013-ai-writes-produce-proposals.md",
        literal="theurian propose",
        excused=("theurian propose accept",),
        reason="the accepted design for a flow Milestone 7 builds; not an instruction yet",
        reference="#89",
    ),
    Exemption(
        path="docs/security/threat-model.md",
        literal="theurian upgrade",
        excused=(
            "theurian upgrade",
            "theurian upgrade",
            "theurian upgrade --check --json",
            "theurian upgrade",
        ),
        reason="the corrected-entry history quoting the remedy it replaced, and the note "
        "recording that implementing it was the rejected alternative",
        reference="#42",
    ),
    Exemption(
        path="docs/security/threat-model.md",
        literal="theurian propose",
        excused=("theurian propose",),
        reason="the defect-class description naming the reachable member of the class",
        reference="#89",
    ),
    Exemption(
        path="packages/theurian-core/CHANGELOG.md",
        literal="theurian upgrade",
        excused=("theurian upgrade", "theurian upgrade", "theurian upgrade"),
        reason="the 0.1.0.dev1 entry quoting the invocation it removed, and the note "
        "recording that a real implementation was the rejected alternative",
        reference="#42",
    ),
    Exemption(
        path="packages/theurian-core/src/theurian/domain/compatibility.py",
        literal="theurian upgrade",
        excused=(
            "theurian upgrade",
            "theurian upgrade",
            "theurian upgrade",
            "theurian upgrade",
        ),
        reason="two in the `CORE_UPGRADERS` comment and two in `resolve_compatibility`'s "
        "docstring, all recording that upgrade never existed beside the remedy that "
        "replaced it",
        reference="#42",
    ),
    Exemption(
        path="plugins/claude-code/CHANGELOG.md",
        literal="theurian upgrade",
        excused=("theurian upgrade --check --json", "theurian upgrade --json"),
        reason="the 0.1.1 entry quoting the two invocations it removed",
        reference="#42",
    ),
)


def _describe(invocations: Iterable[Invocation]) -> str:
    return "\n".join(
        f"  {found.path}:{found.line}  `{found.literal}`  in: {found.span[:90]}"
        for found in sorted(invocations, key=lambda found: (found.path, found.line))
    )


def _by_anchor(invocations: Iterable[Invocation]) -> Mapping[tuple[str, str], list[Invocation]]:
    grouped: dict[tuple[str, str], list[Invocation]] = {}
    for found in invocations:
        grouped.setdefault(found.anchor, []).append(found)
    return grouped


def unexcused(
    invocations: Iterable[Invocation], exemptions: Iterable[Exemption]
) -> list[Invocation]:
    """The invocations no exemption covers, counted rather than merely matched.

    A :class:`collections.Counter` difference and not a set difference, which is
    the whole of the fix: a set says "this file excuses this literal" and a
    multiset says "this file excuses this literal three times". The repository
    has nothing that violates the bound, so nothing in the real scan exercises
    it -- :func:`test_an_exemption_covers_the_texts_it_lists_and_no_others` is
    what makes it a rule rather than a comment.

    When a text is over-represented, *every* occurrence of it is returned rather
    than the arithmetic difference. An exemption records texts, not positions, so
    nothing here knows which of four identical mentions is the new one -- and a
    failure that picks one anyway sends the reader to a line that was fine.
    """
    permitted = {exemption.anchor: exemption for exemption in exemptions}
    offending: list[Invocation] = []
    for anchor, group in _by_anchor(invocations).items():
        exemption = permitted.get(anchor)
        if exemption is None:
            offending.extend(group)
            continue
        surplus = Counter(found.excerpt for found in group) - Counter(exemption.excused)
        offending.extend(found for found in group if surplus[found.excerpt])
    return offending


def unmatched(
    invocations: Iterable[Invocation], exemptions: Iterable[Exemption]
) -> list[tuple[Exemption, str]]:
    """Every excused text the scan no longer finds, once per missing occurrence."""
    found = _by_anchor(invocations)
    return [
        (exemption, text)
        for exemption in exemptions
        for text, missing in (
            Counter(exemption.excused)
            - Counter(item.excerpt for item in found.get(exemption.anchor, []))
        ).items()
        for _ in range(missing)
    ]


# -- the tests -------------------------------------------------------------


def test_every_theurian_command_a_document_names_is_registered() -> None:
    """The closure #42 asked for and #89 proved was still open.

    #42 fixed ``theurian upgrade`` in two places and pinned one of them by
    literal. Two more faces shipped afterwards in ``/theurian:propose`` and
    ``/theurian:reindex``, because a per-literal pin cannot cover a word nobody
    has thought of. Resolving every literal against the registered set covers
    ``upgrade``, ``propose``, ``index rebuild``, and whatever the next one turns
    out to be.

    An exemption does not silence a file: it excuses the exact texts it lists,
    once each, so a *new* instruction written under one still fails here.
    """
    offending = unexcused(_scan(), KNOWN_UNREGISTERED)

    assert not offending, (
        "These instructions name a `theurian` subcommand the CLI does not "
        "register, so a reader following them gets `No such command` and exit 2 "
        f"(#42, #89):\n{_describe(offending)}\n"
        "Fix the text. If it records a dead command rather than telling anyone to "
        "run one, add an Exemption -- or, if one already covers that file and "
        "literal, add this occurrence's exact quoted text to its `excused` tuple "
        "and say in `reason` why this one is a record too. Where an exemption "
        "already lists that text, every occurrence of it is above and one of them "
        "is new: nothing here records which, so read them all."
    )


#: The exemption the bound is demonstrated against: Core's CHANGELOG names the
#: remedy it removed three times, all with the same text, so a set-based
#: permission cannot tell three from four.
_DEMONSTRATION: Final = Exemption(
    path="packages/theurian-core/CHANGELOG.md",
    literal="theurian upgrade",
    excused=("theurian upgrade", "theurian upgrade", "theurian upgrade"),
    reason="the entry quoting the invocation it removed",
    reference="#42",
)


def _occurrence(line: int, span: str) -> Invocation:
    return Invocation(_DEMONSTRATION.path, line, "upgrade", span)


def test_an_exemption_covers_the_texts_it_lists_and_no_others() -> None:
    """The bound itself, which nothing in the repository exercises.

    The repository is clean, so every occurrence the real scan finds is one an
    exemption already lists -- which means the difference between "this file and
    literal are excused" and "these three texts are excused" is invisible to
    :func:`test_every_theurian_command_a_document_names_is_registered`. It was
    invisible for real: with the old set-based permission, five injected
    instructions were swallowed with the whole suite green, among them a new
    CHANGELOG entry telling users to run ``theurian upgrade`` and #42's own
    removed remedy put back into ``domain/compatibility.py``.

    A CHANGELOG is the worst case and therefore the case pinned here: it is
    append-only, so the next entry lands in the same file the exemption names.
    """
    three = [
        _occurrence(2039, "theurian upgrade"),
        _occurrence(2040, "theurian upgrade"),
        _occurrence(2085, "theurian upgrade"),
    ]

    assert unexcused(three, [_DEMONSTRATION]) == []

    a_fourth = [*three, _occurrence(2500, "theurian upgrade")]
    assert [found.line for found in unexcused(a_fourth, [_DEMONSTRATION])] == [
        2039,
        2040,
        2085,
        2500,
    ], (
        "a fourth occurrence of a text excused three times is a new instruction, "
        "and it has to fail even though the file and the literal are both excused. "
        "All four are named because an exemption records texts and not positions"
    )

    reworded = [*three[:2], _occurrence(2085, "theurian upgrade --check")]
    assert [found.line for found in unexcused(reworded, [_DEMONSTRATION])] == [2085], (
        "an excused text that was rewritten is a text nobody has re-read; it "
        "fails so that somebody does -- and here the rewrite is the only "
        "occurrence of its own text, so it is named alone"
    )

    assert [found.line for found in unexcused(three, [])] == [2039, 2040, 2085]


def test_an_exemption_that_loses_one_of_its_texts_is_reported() -> None:
    """The other direction: a permission for three that now covers two.

    Checked per text rather than per exemption because the anchor still matches
    -- two occurrences remain -- so an exemption-level check sees nothing wrong
    and leaves a standing permission for an occurrence that no longer exists.
    """
    two = [_occurrence(2039, "theurian upgrade"), _occurrence(2040, "theurian upgrade")]

    assert unmatched(two, [_DEMONSTRATION]) == [(_DEMONSTRATION, "theurian upgrade")]
    assert unmatched([*two, _occurrence(2085, "theurian upgrade")], [_DEMONSTRATION]) == []


def test_no_recorded_exception_outlives_the_text_it_excuses() -> None:
    """An exemption that stops matching is a permission nobody revoked.

    It also fails when the extractor stops finding anything at all, which is the
    way this file goes quietly useless: a reworded fence, a Typer release that
    changes command introspection, a moved directory. Every exemption going
    unused at once says the scan reached nothing, not that the repository got
    six fixes in one commit.

    Checked per excused text rather than per exemption, so that deleting one of
    the three ``theurian upgrade`` mentions in Core's CHANGELOG is reported here
    instead of leaving a permission for two that covers three.
    """
    stale = unmatched(_scan(), KNOWN_UNREGISTERED)

    assert not stale, (
        "These excused texts no longer appear in the file that names them. Either "
        "the text was fixed -- delete that entry from `excused`, and the whole "
        "Exemption if it is now empty -- or the scan has stopped reading that "
        "file, which would make this whole module pass by finding nothing:\n"
        + "\n".join(
            f"  {exemption.path}: `{text}` ({exemption.reason}, {exemption.reference})"
            for exemption, text in stale
        )
    )


def test_the_walk_enters_only_what_the_repository_ships() -> None:
    """A walk that reads whatever is on disk answers a question nobody asked.

    Both the scan and the guard below walk from the repository root, so what
    they see depends on what a tool has left lying about. The mutation harness
    is the case that proved it: it runs the suite with ``TMPDIR`` pointed inside
    its copy of the tree, and a run left 12,734 fixture files under
    ``.mutate-tmp/`` -- entire ``.theurian`` project directories with their own
    markdown, JSON and YAML, some of it not UTF-8. The scan read them, the
    unmutated control went RED, and with it every verdict in that batch.

    Pinned as a rule and not as the list of names seen so far, because the names
    keep changing and the rule does not.
    """
    assert _walked([".claude", ".github", ".theurian", "docs", "schemas"]) == [
        ".claude",
        ".github",
        ".theurian",
        "docs",
        "schemas",
    ]

    assert _walked([".mutate-tmp", ".mutate-home", ".venv", ".git", ".pytest_cache"]) == []
    assert _walked(["worktrees", "node_modules", "site", "htmlcov", "__pycache__"]) == []


def test_no_file_that_names_a_command_escapes_the_scan() -> None:
    """The population claim in the docstring, checked instead of asserted.

    The first version of this module read three roots and called itself "one
    mechanism over every instructional surface". 193 command-position sites were
    outside it, in ten markdown files, seven JSON schemas, four workflows and the
    plugin's two shell scripts -- and the two scripts *execute* theirs. Widening
    the roots fixes that once; this is what keeps it fixed, because the next
    surface will be a file type nobody thought of rather than a directory
    somebody forgot.

    Deliberately coarser than the readers: it asks only whether some reader opens
    the file, using a raw line scan with no notion of fences or quoting. A file
    it flags is not necessarily a defect -- it is a file whose contents nothing
    here has ever looked at.

    Walks the same pruned tree the readers do, and for the same reason: the
    question is which *repository* files escape, and everything :func:`_walked`
    refuses belongs to a tool rather than to the repository.
    """
    scanned = {
        path.relative_to(REPO_ROOT).as_posix()
        for surface in SCANNED_SURFACES
        for path in _files(surface.root, surface.suffixes)
    }

    unseen: list[str] = []
    for base, directories, names in os.walk(REPO_ROOT):
        directories[:] = _walked(directories)
        for name in sorted(names):
            path = pathlib.Path(base) / name
            relative = path.relative_to(REPO_ROOT).as_posix()
            if relative in scanned or _is_unread(relative):
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            if any(_names_a_command(line) for line in text.splitlines()):
                unseen.append(relative)

    assert not unseen, (
        "These files name a `theurian <command>` and no reader in this module "
        f"opens them:\n  {'\n  '.join(unseen)}\n"
        "Either add the file type to SCANNED_SURFACES, or add its prefix to "
        "UNREAD with the reason a dead command there is harmless."
    )


def _names_a_command(line: str) -> bool:
    """Whether one raw line puts a plausible subcommand after ``theurian``."""
    for match in _INVOCATION.finditer(line):
        if not _at_command_position(line[: match.start()]):
            continue
        words = line[match.end() :].split()
        if words and _SUBCOMMAND.match(_strip_markup(words[0])):
            return True
    return False


#: Real lines from files the scan did not use to read, and prose from files it
#: did. A guard that answers ``False`` to everything reports an empty list and
#: passes, which is the one way this population check goes quietly useless.
_GUARD_ORACLE: Final = (
    pytest.param(
        "          /tmp/verify/bin/theurian version --json", True, id="a-release-workflow"
    ),
    pytest.param("uv run theurian compat check \\", True, id="a-shared-workflow-step"),
    pytest.param(
        "        **Before you paste diagnostics:** `theurian doctor --report --json`",
        True,
        id="an-issue-template",
    ),
    pytest.param("theurian daemon start >/dev/null 2>&1 || \\", True, id="the-sessionstart-hook"),
    pytest.param(
        "  # Off, so a first build is the cheap one. `theurian index build --raptor`",
        True,
        id="the-sample-project-config",
    ),
    pytest.param(
        'jq -e \'.mcpServers.theurian | has("command") | not\' "$template"',
        False,
        id="a-workflow-naming-the-mcp-server-key",
    ),
    pytest.param("participant CLI as theurian CLI", False, id="prose-naming-the-binary"),
    pytest.param("Read theurian-core's CHANGELOG first.", False, id="prose-naming-the-package"),
)


@pytest.mark.parametrize(("line", "expected"), _GUARD_ORACLE)
def test_the_population_guard_recognises_a_command_when_it_sees_one(
    line: str, expected: bool
) -> None:
    """The guard above reports files, so a guard that sees nothing reports nothing.

    Every positive here is a real line from a file the first version of this
    module never opened -- two workflows, an issue template, the SessionStart
    hook, and the sample project's config. They are what the widened population
    was for, so they are what proves the check that keeps it wide can fail.
    """
    assert _names_a_command(line) is expected


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
        'THEURIAN_DATA_DIR="$dir" theurian index rebuild --json',
        ("index rebuild",),
        id="behind-an-environment-assignment",
    ),
    pytest.param(
        "HOME=/tmp/h THEURIAN_DATA_DIR=/tmp/d theurian version --json",
        (),
        id="behind-two-environment-assignments",
    ),
    pytest.param(
        "run --project=theurian frobnicate", (), id="after-a-flag-that-merely-contains-the-name"
    ),
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
    pytest.param("allowed-tools: Bash(theurian:*), Read", (), id="a-plugin-permission-grant"),
    pytest.param("Write(.theurian/proposals/**)", (), id="a-scoped-write-grant"),
    pytest.param("# >>> theurian >>>", (), id="a-gitignore-marker"),
    pytest.param("participant CLI as theurian CLI", (), id="prose-naming-the-binary"),
    pytest.param(
        "an absolute path to the theurian executable", (), id="prose-whose-next-word-is-lowercase"
    ),
    pytest.param("theurian index", (), id="a-group-named-without-a-verb"),
    pytest.param("theurian --version", (), id="a-flag-rather-than-a-subcommand"),
    pytest.param("theurian <verb> --json", (), id="a-placeholder-rather-than-a-subcommand"),
    pytest.param("theurian {} build --json", (), id="an-fstring-interpolation-in-command-position"),
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


#: The same, for the one reader that has no command position to filter on.
#: The negatives are the real descriptions ``reindex.md`` and ``setup.md`` ship.
_PROSE_ORACLE: Final = (
    pytest.param(
        "description: Runs theurian index rebuild, then reclaims the builds it replaces.",
        ("index rebuild",),
        id="a-description-naming-a-dead-command-mid-sentence",
    ),
    pytest.param(
        "description: Rebuild the index from current state, then reclaim the builds it replaces.",
        (),
        id="the-description-reindex-actually-ships",
    ),
    pytest.param(
        "description: Configure this machine to run Theurian, and connect Claude Code to it.",
        (),
        id="prose-capitalising-the-product-name",
    ),
    pytest.param("name: theurian-adversarial-review", (), id="an-agent-definition-naming-itself"),
    pytest.param(
        "description: Runs theurian index build, then reports how long it took.",
        (),
        id="a-description-naming-a-live-command",
    ),
)


@pytest.mark.parametrize(("line", "expected"), _PROSE_ORACLE)
def test_the_extractor_reads_a_frontmatter_value_as_prose(
    line: str, expected: tuple[str, ...]
) -> None:
    """Command position cannot be computed in a field, so it is not required.

    That is a widening, and a widening is where false positives come from -- so
    the negatives here are the exact descriptions the plugin and the agent
    definitions ship, not invented ones.
    """
    assert tuple(unregistered_in(line, prose=True)) == expected


#: A markdown document holding one of every shape the reader has to survive,
#: with the dead commands at known lines. Deliberately not a real file: the real
#: ones are clean, so nothing in this repository exercises the *reporting* half
#: of the reader, and a change that stopped it reading fenced blocks altogether
#: kept the whole module green. That mutation is what this fixture exists for --
#: ``propose.md``'s dead ``theurian propose --json`` sat in a fence, so the fence
#: path is the one that carried the motivating face of #89.
_MARKDOWN_FIXTURE: Final = """\
---
description: Runs theurian propose, then waits for review.
allowed-tools: Bash(theurian:*), Read, Write(.theurian/proposals/**)
---

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

A fence inside a blockquote is a fence, not one long line:

> ```sh
> theurian index build --json
> theurian propose accept
> ```

An indented block, which no fence encloses:

    theurian index rebuild --json

```mermaid
S["Session starts"] --> A{"theurian on PATH?"}
```
"""

#: ``(line, command)`` for every dead command in :data:`_MARKDOWN_FIXTURE`.
#: Line 2 is a frontmatter description, which is neither fenced nor backticked.
#: Line 11 is fenced. Line 12 is fenced across a shell backslash continuation,
#: and reads ``index`` with a trailing backslash if the join is dropped. Line 16
#: is an inline span. Line 17 wraps *between* ``theurian`` and its command word,
#: so it disappears entirely unless the span is unwrapped first. Line 20 wraps
#: inside a doubly nested blockquote, the shape ADR-0008 has. Line 27 is the
#: *second* line of a fence inside a blockquote, which is only reachable once the
#: markers are gone before the fence is looked for -- line 26 of that block is
#: live, and reporting only the first line is how the old reader passed. Line 32
#: is indented four spaces and quoted by nothing at all. Nothing comes from the
#: Mermaid block, from ``allowed-tools``, or from the live invocation on line 8.
_MARKDOWN_FIXTURE_FINDINGS: Final = {
    (2, "propose"),
    (11, "propose"),
    (12, "index rebuild"),
    (16, "index rebuild"),
    (17, "propose"),
    (20, "index rebuild"),
    (27, "propose"),
    (32, "index rebuild"),
}

#: The same, for Python. The comment run is the shape ``mcp/search.py`` has: one
#: code span split over two ``#`` lines, which ``tokenize`` hands over as two
#: separate tokens and which is therefore a span in neither of them. The last
#: three are f-strings, which are not ``STRING`` tokens at all since PEP 701 and
#: were read by nothing until they were: one plain, one whose command word is
#: interpolated and must therefore *not* resolve, and one whose span follows an
#: interpolation spread over three lines and must still report its own line.
_PYTHON_FIXTURE: Final = '''\
"""A module docstring naming `theurian index build`."""

#: A comment run whose code span the line wrap split: `theurian index
#: rebuild` is the face #89 fixed.
REMEDY = "Run `theurian propose` to draft one."
DETAILED = f"Run `theurian index rebuild --project {name}` to fix it."
GUESSED = f"Run `theurian {verb} --json`, whatever it turns out to be."
WRAPPED = f"""Run {
    "this"
    or "that"
} and then `theurian propose accept`."""
'''

_PYTHON_FIXTURE_FINDINGS: Final = {
    (3, "index rebuild"),
    (5, "propose"),
    (6, "index rebuild"),
    (11, "propose"),
}

#: A JSON schema's remedy strings, which reach a user through an MCP tool error.
_JSON_FIXTURE: Final = """\
{
  "description": "Run `theurian index build` to refresh it.",
  "properties": {
    "stale": {"description": "Rebuild with `theurian index rebuild`."},
    "prose": {"description": "A CI image running `theurian migrate` needs no server."}
  }
}
"""

_JSON_FIXTURE_FINDINGS: Final = {(4, "index rebuild")}

#: The plugin's SessionStart hook, which runs what it names.
_SHELL_FIXTURE: Final = """\
#!/usr/bin/env bash
theurian daemon start >/dev/null 2>&1 || true
status="$(theurian project status --json)" || return 0
theurian index rebuild \\
    --json || true
# A comment naming `theurian propose accept` as the flow to come.
"""

_SHELL_FIXTURE_FINDINGS: Final = {(4, "index rebuild"), (6, "propose")}


def _read(document: str, reader: Reader) -> set[tuple[int, str]]:
    return {
        (span.line, command)
        for span in reader(document)
        for command in unregistered_in(span.text, prose=span.prose)
    }


def test_a_markdown_document_yields_its_dead_commands_and_their_lines() -> None:
    """All four markdown paths, exercised on text that actually contains a defect.

    :func:`test_the_extractor_reports_exactly_the_dead_commands_in_a_line` hands
    single lines straight to the resolver, so it never runs the reader that
    finds them. Measured before this fixture existed: deleting the fenced-block
    branch outright left every test in this file green, and so did planting a
    dead command inside a blockquoted fence. Both go RED here now. The line
    numbers are pinned too, because a failure message that names the wrong line
    sends the reader to the wrong place.
    """
    assert _read(_MARKDOWN_FIXTURE, markdown_command_lines) == _MARKDOWN_FIXTURE_FINDINGS


def test_one_occurrence_is_one_finding_even_when_two_arms_could_see_it() -> None:
    """The arms partition the document; a set of findings would not show it.

    A backticked command inside frontmatter is the only text two markdown arms
    can both reach, and :func:`_prose_of` blanks the frontmatter for the other
    three so that they do not. Removing that blanking survived the whole suite,
    because every other assertion here compares *sets* of findings and a
    duplicate collapses into one.

    It matters because an :class:`Exemption` bounds a file and literal by the
    number of occurrences. Counted twice, the text would need listing twice to
    stay green -- which is a standing permission for a second occurrence nobody
    has written, and the exemption bound is precisely what this round added.
    """
    document = "---\ndescription: Run `theurian propose` first.\n---\n\nBody.\n"

    found = [
        (span.line, command)
        for span in markdown_command_lines(document)
        for command in unregistered_in(span.text, prose=span.prose)
    ]

    assert found == [(2, "propose")]


def test_a_python_module_yields_dead_commands_from_comments_strings_and_fstrings() -> None:
    """The same for Core's own source, where a remedy reaches a user as an error."""
    assert _read(_PYTHON_FIXTURE, python_command_lines) == _PYTHON_FIXTURE_FINDINGS


def test_a_json_schema_yields_the_dead_commands_in_its_descriptions() -> None:
    """A schema description is a remedy the MCP surface hands to an agent."""
    assert _read(_JSON_FIXTURE, json_command_lines) == _JSON_FIXTURE_FINDINGS


def test_a_shell_script_yields_the_dead_commands_it_would_run() -> None:
    """These lines are not instructions to a reader; they execute in a session."""
    assert _read(_SHELL_FIXTURE, plain_command_lines) == _SHELL_FIXTURE_FINDINGS


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
    nested_quote = "> > theurian index\n> > rebuild --json"

    assert _unwrap(comment_run, _COMMENT_MARKER) == (
        'echo "everything between # >>> theurian >>> and here"'
    )
    assert _unwrap(_flatten_blockquotes(nested_quote)) == "theurian index rebuild --json"


def test_flattening_blockquotes_does_not_touch_fenced_content() -> None:
    """The assumption that makes a document-wide strip safe instead of merely cheap.

    A fenced block at quote depth zero may contain a line beginning with ``>`` --
    a shell redirect, a doctest prompt, a quoted diff. Stripping the document
    would silently rewrite it, and the span this module reports would then name
    text nobody wrote. Measured over the repository: no such line exists, so the
    cheap strip is exact. This is what says so the day one is written.
    """
    rewritten: list[str] = []
    for path in _files(REPO_ROOT, frozenset({".md"})):
        text = _text(path)
        for fence in _FENCE.finditer(text):
            first = text.count("\n", 0, fence.start("body")) + 1
            rewritten.extend(
                f"{path.relative_to(REPO_ROOT).as_posix()}:{first + offset}  {line}"
                for offset, line in enumerate(fence.group("body").splitlines())
                if _unquote_line(line) != line
            )

    assert not rewritten, (
        "These lines sit inside a fenced code block and begin with a blockquote "
        "marker, so flattening the document rewrites content that was never a "
        f"blockquote:\n  {'\n  '.join(rewritten)}\n"
        "_flatten_blockquotes now has to become fence-aware."
    )


def test_the_scan_reaches_every_arm_of_every_reader() -> None:
    """The plumbing over the real tree, checked separately from the verdict.

    The fixtures above prove each reader reports what it is given; this proves it
    is given the repository. A glob that stops matching, a fence that stops
    closing, or a ``tokenize`` change would otherwise make every assertion here
    pass by reading nothing at all.

    Two arms are absent from the counts and are held by :data:`_MARKDOWN_FIXTURE`
    instead: the repository has no bare command line and no frontmatter value
    naming a command, so requiring either here would be an assertion that can
    only ever be satisfied by a defect.
    """
    counts = dict.fromkeys(("fenced", "inline", "comment", "fstring", "json", "plain"), 0)
    for path in _files(REPO_ROOT, frozenset({".md"})):
        flattened = _flatten_blockquotes(_text(path))
        counts["fenced"] += _count_invocations(fenced_lines(flattened))
        counts["inline"] += _count_invocations(inline_spans(flattened))
    for path in _files(REPO_ROOT / "packages" / "theurian-core" / "src", frozenset({".py"})):
        source = _text(path)
        counts["comment"] += _count_spans(_comment_blocks(source))
        counts["fstring"] += _count_spans(_fstring_blocks(source))
    for path in _files(REPO_ROOT, frozenset({".json"})):
        counts["json"] += _count_invocations(json_command_lines(_text(path)))
    for path in _files(REPO_ROOT, frozenset({".sh", ".yml", ".yaml"})):
        counts["plain"] += _count_invocations(plain_command_lines(_text(path)))

    assert all(counts.values()), (
        f"a reader arm yielded no `theurian <command>` at all: {counts}. Every "
        "assertion in this module would pass on an empty population."
    )


def _count_invocations(spans: Iterable[Span]) -> int:
    """How many ``theurian <word>`` sites reached the resolver, dead or alive."""
    return sum(
        1
        for span in spans
        for match in _INVOCATION.finditer(span.text)
        if span.prose or _at_command_position(span.text[: match.start()])
    )


def _count_spans(chunks: Iterable[tuple[int, str]]) -> int:
    """The same, for the two Python token readers, before spans are extracted."""
    return _count_invocations(
        Span(line, _unwrap(span.group("body"), _COMMENT_MARKER))
        for line, chunk in chunks
        for span in _CODE_SPAN.finditer(chunk)
    )
