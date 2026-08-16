"""Turning a repository file into the ``theurian`` invocations it names.

Split out of ``tests/unit/test_documented_commands.py``, which owns the
population, the recorded exemptions and the assertions. This half is the part
with no policy in it: given text, which command words does it name, and where.

The two are separated because the test module had grown past this repository's
size limit for a file, not because the halves are independent -- the split is a
pure move, so the reasoning for every regex and every filter travels with the
code it explains rather than being summarised here.

Lives under ``tests/`` and therefore inside the population's ``UNREAD`` prefix,
which matters: the docstrings below quote dead commands (``theurian upgrade``,
``theurian index rebuild``) as examples, and a reader that opened this file
would report every one of them.
"""

from __future__ import annotations

import io
import json
import re
import tokenize
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, field
from typing import Final, NamedTuple, cast

import typer.main

from theurian.cli.main import app

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
#: and ``{}`` is what :func:`_literal_blocks` writes where an interpolation was.
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
    """Runs of ``#`` comment lines, joined.

    ``tokenize`` emits one token per comment *line*, so a code span wrapped over
    two of them -- ``mcp/search.py`` has ``theurian index`` on one line and
    ``gc`` on the next -- is not a span in either token. Joining the run first is
    what makes it one.

    String literals are :func:`_literal_blocks`'s, because they need joining of
    a different kind.
    """
    block: list[str] = []
    start = 0
    previous = -2
    for token in tokenize.generate_tokens(io.StringIO(source).readline):
        if token.type != tokenize.COMMENT:
            continue
        if token.start[0] != previous + 1:
            if block:
                yield start, "\n".join(block)
            block, start = [], token.start[0]
        block.append(token.string)
        previous = token.start[0]
    if block:
        yield start, "\n".join(block)


#: A string literal's prefix letters and its opening quote run.
_LITERAL_OPENER: Final = re.compile(r"[A-Za-z]*(?P<quote>'''|\"\"\"|'|\")")

#: What may sit between two implicitly concatenated literals without ending the
#: concatenation. A logical ``NEWLINE`` ends the statement and therefore does.
_LITERAL_GLUE: Final = frozenset({tokenize.NL, tokenize.COMMENT})


def _literal_body(raw: str) -> str:
    """The source between a literal's delimiters, with its escapes left alone.

    Deliberately not :func:`ast.literal_eval`. Decoding turns ``\\n`` into a real
    newline, and every span after it in the same literal would then be reported
    one line late for each escape it followed.
    """
    opener = _LITERAL_OPENER.match(raw)
    if opener is None:  # pragma: no cover - tokenize emits no such STRING token
        return raw
    return raw[opener.end() : len(raw) - len(opener.group("quote"))]


@dataclass
class _Builder:
    """Literal text being reassembled from the tokens that carry it."""

    line: int
    row: int
    parts: list[str] = field(default_factory=list)
    pending: bool = False

    def take(self, start_row: int, text: str, end_row: int) -> None:
        """Append the next fragment, standing in for whatever preceded it.

        The rows between two fragments are kept as newlines whether what sat
        there was an interpolation or the quotes, whitespace and ``f`` prefix
        joining two adjacent literals. Without them every span after the gap is
        reported at the line the literal *started* on.
        """
        if self.pending:
            self.parts.append("{}")
            self.pending = False
        self.parts.append("\n" * (start_row - self.row))
        self.parts.append(text)
        self.row = end_row

    @property
    def text(self) -> str:
        return "".join(self.parts)


def _literal_blocks(source: str) -> Iterator[tuple[int, str]]:
    """Every string literal, with implicit concatenation and f-strings resolved.

    Two joins, both of which were holes.

    **f-strings.** PEP 701 changed the tokenizer in Python 3.12: an f-string is
    no longer one ``STRING`` token but a ``FSTRING_START`` / ``FSTRING_MIDDLE`` /
    ``FSTRING_END`` run. A reader of ``STRING`` alone saw none of Core's 403
    f-strings, 37 of which carry a backticked remedy to a user through an error
    message. An interpolation becomes ``{}`` rather than vanishing, because
    vanishing invents commands: ``f"theurian {verb} build"`` would read as
    ``theurian build``. ``{}`` is not a :data:`_SUBCOMMAND`, so the invocation is
    seen and refused, while ``f"theurian index build --project {pid}"`` still
    resolves.

    **Implicit concatenation.** Adjacent literals are one string to Python and
    were two chunks here, so a code span that opened in one and closed in the
    next was a span in neither. That is not hypothetical:
    ``infrastructure/sqlite/index_purge.py`` ends a fragment with
    ``Run `theurian index `` and opens the next with ``build` to rebuild it``,
    and the remedy was invisible. Merging the run is what makes it one span.

    A literal inside an f-string's interpolation is yielded on its own: it is not
    part of the surrounding concatenation, and dropping it would lose a remedy
    written as ``f"{'Run `theurian gc`' if stale else ''}"``.
    """
    group: _Builder | None = None
    stack: list[_Builder] = []
    for token in tokenize.generate_tokens(io.StringIO(source).readline):
        if stack:
            if token.type == tokenize.STRING:
                yield token.start[0], _literal_body(token.string)
                continue
            closed = _fstring_step(stack, token)
            if closed is None:
                continue
            if stack:
                yield closed.line, closed.text
            else:
                group = _joined(group, closed.line, closed.text, token.end[0])
        elif token.type == tokenize.STRING:
            group = _joined(group, token.start[0], _literal_body(token.string), token.end[0])
        elif token.type == tokenize.FSTRING_START:
            stack.append(_Builder(line=token.start[0], row=token.end[0]))
        elif token.type not in _LITERAL_GLUE and group is not None:
            yield group.line, group.text
            group = None
    if group is not None:
        yield group.line, group.text


def _fstring_step(stack: list[_Builder], token: tokenize.TokenInfo) -> _Builder | None:
    """Advance the innermost f-string, returning it on the token that closes it.

    Anything that is not a literal fragment marks an interpolation as pending,
    so the next fragment is preceded by ``{}`` and by the rows it spanned.
    """
    if token.type == tokenize.FSTRING_START:
        stack[-1].pending = True
        stack.append(_Builder(line=token.start[0], row=token.end[0]))
    elif token.type == tokenize.FSTRING_MIDDLE:
        stack[-1].take(token.start[0], token.string, token.end[0])
    elif token.type == tokenize.FSTRING_END:
        done = stack.pop()
        done.take(token.start[0], "", token.end[0])
        if stack:
            stack[-1].pending = True
        return done
    else:
        stack[-1].pending = True
    return None


def _joined(group: _Builder | None, line: int, text: str, end_row: int) -> _Builder:
    """Start a concatenation run, or add the next literal to the one open."""
    if group is None:
        group = _Builder(line=line, row=line)
    group.take(line, text, end_row)
    return group


def python_command_lines(source: str) -> Iterator[Span]:
    """Every code span written inside a Python comment, string, or f-string."""
    for line, chunk in sorted([*_comment_blocks(source), *_literal_blocks(source)]):
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
