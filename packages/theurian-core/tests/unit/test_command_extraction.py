"""What the readers in ``command_extraction`` report, and what they must not.

Split from ``test_documented_commands.py``, which owns the population and the
recorded exemptions. These are the oracles: single lines handed straight to the
resolver, and whole documents handed to each reader with the dead commands at
known lines.

Both halves are needed and neither substitutes for the other. The line oracle
never runs a reader, so a reader that stopped reading fenced blocks kept it
green; the document fixtures never see the repository, so a glob that stopped
matching keeps *them* green. The second is
``test_documented_commands.test_the_scan_reaches_every_arm_of_every_reader``'s
job, and it stays there with the walk it needs.
"""

from __future__ import annotations

from typing import Final

import pytest
from command_extraction import (
    _COMMENT_MARKER,
    Reader,
    _flatten_blockquotes,
    _unwrap,
    json_command_lines,
    markdown_command_lines,
    plain_command_lines,
    python_command_lines,
    unregistered_in,
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
#: separate tokens and which is therefore a span in neither of them. Then three
#: f-strings, which are not ``STRING`` tokens at all since PEP 701 and were read
#: by nothing until they were: one plain, one whose command word is interpolated
#: and must therefore *not* resolve, and one whose span follows an interpolation
#: spread over three lines and must still report its own line. Last, the shape
#: ``index_purge.py`` really has: a span that opens in one implicitly
#: concatenated literal and closes in the next, so it is a span in neither
#: unless the run is merged first.
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
CONCATENATED = (
    f"The index build being purged could not be read ({name}). Nothing "
    f"was published, so retrieval still uses the current index. Run `theurian index "
    f"rebuild` to rebuild it; the index is derived, so nothing authored is lost."
)
SEPARATE = [
    "`theurian propose`",
    "`theurian index rebuild`",
]
'''

#: Line 14 is the one to read twice. The concatenation starts on line 13, and
#: the span opens on 14 and closes on 15 -- so reporting it at 14 is what says
#: the rows between two literals were kept as newlines when the run was merged.
#: Drop that padding and every span in a concatenation collapses onto the line
#: the *first* fragment started on. Lines 18 and 19 are the negative: a comma
#: ends a concatenation, so those two are separate literals reported at their
#: own lines rather than one merged blob.
_PYTHON_FIXTURE_FINDINGS: Final = {
    (3, "index rebuild"),
    (5, "propose"),
    (6, "index rebuild"),
    (11, "propose"),
    (14, "index rebuild"),
    (18, "propose"),
    (19, "index rebuild"),
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
