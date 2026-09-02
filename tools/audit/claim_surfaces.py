"""Governed text surfaces, read the way a wrap-aware sweep has to read them (#199 unit B).

Shared by the five object-keyed audits beside this file. It answers one question
-- *what text does this repository govern, and where does each sentence of it
live* -- so that five keys can be applied to one population rather than five
line-oriented approximations of it.

**Why not ``git grep``.** The keys these audits run are sentence-shaped, and a
sentence in this repository routinely spans two source lines: every Markdown
document here is hard-wrapped, and a ``#:`` comment block in ``src/`` is wrapped
at the same column. A line-oriented pass therefore undercounts, silently and by
an amount nobody can state -- three such undercounts were measured in the #498
arc alone. So the population comes from ``git ls-files`` (never ``rg``, which
honours ``.git/info/exclude`` and skips dot directories: both undercount the
served corpus), and the matching happens on **blocks that have been joined and
whitespace-collapsed first**, with each match attributed back to the source line
its first character came from.

**What is governed.** Every tracked file, minus two exclusions that are recorded
rather than assumed:

* ``.theurian/`` -- the served corpus. It carries governed snapshots held
  byte-identical to their source anchor commits, so a claim found there is a
  property of the snapshot, not of the tree, and it is fixed by a re-seed rather
  than by an edit (#199 unit C).
* ``docs/work-logs/`` -- dated records. A work log states what was true on its
  date; correcting one would falsify it.

Both exclusions are load-bearing for the counts these audits print, so
:data:`EXCLUDED_PREFIXES` is a constant a reader can attack rather than a
pathspec buried in a call.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Final, override

#: A reader turns one file's text into ``(collapsed block, per-character source
#: line)`` pairs. One per suffix, because a Markdown paragraph, a Python comment
#: run and a JSON string are three different containers for the same sentence.
type BlockReader = Callable[[str], list[tuple[str, list[int]]]]

#: Tracked paths whose text is outside every audit here, and why. Ordered pairs
#: rather than a bare set so the reason travels with the rule.
EXCLUDED_PREFIXES: Final[tuple[tuple[str, str], ...]] = (
    (".theurian/", "the served corpus: governed snapshots, moved by a re-seed (#199 unit C)"),
    ("docs/work-logs/", "dated records: a work log states what was true on its date"),
)

#: Suffixes that carry prose this repository governs. A suffix outside this set
#: is skipped, and :func:`skipped_suffixes` reports what that leaves out, so the
#: gap is a number rather than a silence.
READ_SUFFIXES: Final[frozenset[str]] = frozenset({".md", ".py", ".json", ".yaml", ".yml", ".toml"})

_GIT_TIMEOUT_SECONDS: Final = 30

#: Environment that must not reach the ``git`` child, for the reason
#: ``tools/corpus_drift.py`` records: an inherited ``GIT_DIR`` points the
#: enumeration at another repository.
_INHERITED_GIT_OVERRIDES: Final[frozenset[str]] = frozenset(
    {"GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR"}
)

#: Leading Markdown blockquote markers, however deeply nested. Stripped before
#: anything else looks at a line: ADR-0008 decision 10 lives inside a ``>``
#: amendment block, and treating ``>`` as a block boundary would make every
#: wrapped line its own paragraph and hide every sentence that spans a wrap.
_BLOCKQUOTE_MARKERS: Final = re.compile(r"^(?:[ \t]*>)+[ \t]?")

#: A line that begins a new block rather than continuing the one above it,
#: applied *after* the blockquote markers are stripped. The same rule
#: ``test_raptor_config_claims.py`` uses, and ``>`` is deliberately absent for
#: the same reason.
_BLOCK_START: Final = re.compile(r"[ \t]*(?:#{1,6}\s|[-*+]\s|\d+\.\s|\||```|---\s*$)")

#: A ``#:`` or ``#`` comment marker at the head of a Python line.
_PY_COMMENT: Final = re.compile(r"^[ \t]*#:?[ \t]?")

#: A YAML comment: a whole-line one, or one that follows whitespace on a line
#: that also carries a key. The first ``#`` wins, which is YAML's own rule for
#: the unquoted case, so the rest of the line is the comment's own text.
_YAML_WHOLE_LINE_COMMENT: Final = re.compile(r"^[ \t]*#[ \t]?(?P<comment>.*)$")
_YAML_INLINE_COMMENT: Final = re.compile(r"\s#[ \t]?(?P<comment>.*)$")

#: The end of a sentence, which is not every period: ``.theurian/config.yaml``
#: carries one that ends nothing, and so does every Markdown link. The lookahead
#: demands whitespace, the trap both the ADR-0013 and ADR-0018 modules record.
_SENTENCE_END: Final = re.compile(r"(?<=[.!?])\s+")

#: CommonMark's two emphasis delimiters, wherever a reader sees none.
#:
#: An asterisk run is unconditional: CommonMark gives ``*`` no other job in
#: running text. An underscore run is admitted only where it is *not* inside a
#: word, which is CommonMark's own rule and this repository's need at once --
#: ``providers.review.repositories`` carries none, but ``project_config.py``,
#: ``SUMMARY_MAX_TOKENS`` and every dunder in the tree do, and a rule that ate
#: them would rewrite the symbol names these audits key on.
_EMPHASIS_DELIMITERS: Final = re.compile(r"\*+|(?<![0-9A-Za-z_])_+(?![0-9A-Za-z_])")


def without_emphasis(text: str) -> str:
    """``text`` with its emphasis delimiters removed, leaving the words.

    **What this closes, and it is a matching rule rather than a cosmetic one.**
    A key that spells a path with its markup -- a backtick run, a quote -- reads
    ``**`` as neither, so wrapping the same span in bold moves the path out of
    reach of every such key while a reader sees the identical sentence. Measured
    in round two: ``Nothing in ``src/`` reads **`.theurian/config.yaml`**`` in a
    wheel-shipped module left the census, all five audits and the whole suite
    green, because the delimiter run in front of the path cannot step over the
    two asterisks. The escape is composition -- any key over spelled markup has a
    wrapper it does not spell -- and the answer here is to stop matching on
    *emphasis*, which is what ``test_raptor_config_claims.py`` had already done
    on the pin side and the census had not.

    **This closes the emphasis family, not markup composition generally**, and
    the difference is measured rather than hedged: a path written as a Markdown
    link or in a three-backtick run is still out of reach, and both are live
    rows in ``config_object_claims.MEASURED_ESCAPES``. Emphasis earns the
    normalisation because it is invisible in the rendered sentence -- a reader
    sees the same claim -- while a link and a code fence are visible choices a
    key can be widened to spell.

    **Not applied to every audit here, and the exception is load-bearing.**
    ``owner_position_cites.py``'s supersession probe reads a block's *bold
    opener* -- ``**Closed on 2026-09-01 ([#468]).**`` -- so for that audit the
    emphasis is the signal rather than noise. This is a function a caller applies
    where its keys are about words, not a normalisation
    :func:`sentences` performs for everybody.
    """
    return _EMPHASIS_DELIMITERS.sub("", text)


@dataclass(frozen=True, slots=True)
class Sentence:
    """One sentence of governed text, with the source line it opens on.

    ``text`` is whitespace-collapsed and its wrapping is gone, which is the whole
    point: a key applied to it sees the sentence a reader sees rather than the
    two source lines the sentence was typed across. ``line`` is where its first
    character lives, so a row this audit prints is a place a person can open.

    ``block`` is the whole paragraph the sentence came from, carried because a
    pronoun resolves in its paragraph and not in its sentence: the shipped claim
    *"nothing reads that file today"* names its object one clause earlier, and a
    sentence-scoped key either misses it or has to be loosened until it matches
    unrelated prose.
    """

    path: str
    line: int
    text: str
    block: str

    @override
    def __str__(self) -> str:
        return f"{self.path}:{self.line}"


#: A **dated** release heading, and any ``##`` heading that ends its section.
#:
#: Keep-a-Changelog spells a released section ``## [0.1.0.dev17] - 2026-09-02``:
#: the version, then a date. The date is the whole signal -- it is what says this
#: text is a record of what a release did rather than a statement about the tree.
#: ``## [Unreleased]`` has no date because it has not happened.
#:
#: The terminator is any ``##`` heading and not only a ``## [`` one, so a
#: document that ends its release sections with an ordinary ``## Notes`` does not
#: leave every line after it inside the last release.
_DATED_HEADING: Final = re.compile(
    "^##" + r"\s+\[[^\]]+\]\s+[-\u2013\u2014]\s+" + r"\d{4}-\d{2}-\d{2}"
)
_SECTION_HEADING: Final = re.compile(r"^##\s")


def dated_lines(text: str) -> frozenset[int]:
    """Line numbers inside one document's dated release sections, headings included.

    **The rule both audits clear a CHANGELOG sentence by, and it is stated
    positively for a reason.** Round one asked "is this line outside
    ``[Unreleased]``?", which is the right question only in a document that has an
    ``[Unreleased]`` section: ``CHANGELOG.md`` at the repository root carries no
    Keep-a-Changelog headings at all, so every line in it was outside, and the
    whole file cleared as a release record unread. Round two's R2-j. Asking "is
    this line inside a dated section?" collapses both faces into one rule -- a
    line is history because a dated release states it, never because a heading
    that would have contradicted that is missing.

    Returns an empty set for a document with no dated section, which is the
    root ``CHANGELOG.md`` and every governed file that is not a changelog: in
    both, nothing is a release record and every sentence is classified like any
    other governed prose.
    """
    inside = False
    found: set[int] = set()
    for number, line in enumerate(text.splitlines(), start=1):
        if _DATED_HEADING.match(line):
            inside = True
        elif _SECTION_HEADING.match(line):
            inside = False
        if inside:
            found.add(number)
    return frozenset(found)


#: Where a positive control's planted sentence sits in its synthetic changelog.
#:
#: Three, because the rule has three cases and round one only had two of them:
#: inside a dated release section (a record), inside ``[Unreleased]`` (live prose
#: about the tree), and in a document with no dated sections at all -- which is
#: the root ``CHANGELOG.md``, and which round two's R2-j found cleared whole.
PLANT_SECTIONS: Final[tuple[str, ...]] = ("dated", "unreleased", "none")


def planted_changelog(planted: str, *, section: str) -> tuple[frozenset[int], int]:
    """A synthetic CHANGELOG carrying ``planted``, read by :func:`dated_lines`.

    Returns ``(the dated line set, the line the plant sits on)``, so a positive
    control can classify a planted sentence with the section membership the real
    classifier would compute for it.

    **The point is that the control does not know the answer**, which is round
    two's R2-g. Both audits that clear a release note used to hand ``_classify``
    a hardcoded ``frozenset({0})`` for their ``[Unreleased]`` row and
    ``frozenset()`` for their dated row -- the two verdicts the rule has to keep
    apart, asserted against a premise the control supplied itself. Gutting the
    line-set function to ``return frozenset()`` left every such control green.
    Now the set comes from the function under test, and that mutation turns the
    ``[Unreleased]`` row red.

    The ``dated`` and ``unreleased`` documents carry **both** kinds of section,
    with the plant moved between them, so the control exercises the scoping and
    not merely the presence of a heading: a rule that answered "everything" or
    "nothing" fails one of the two whichever way it is broken. The ``none``
    document has no headings at all, which is the shape that made "outside
    ``[Unreleased]``" clear a whole file.
    """
    if section not in PLANT_SECTIONS:
        message = f"{section!r} is not one of {PLANT_SECTIONS}"
        raise ValueError(message)
    filler = "- An unrelated entry."
    entry = f"- {planted}"
    lines = (
        ("# Changelog", "", entry)
        if section == "none"
        else (
            "# Changelog",
            "",
            "## [Unreleased]",
            "",
            entry if section == "unreleased" else filler,
            "",
            "## [0.1.0.dev17] - 2026-09-02",
            "",
            filler if section == "unreleased" else entry,
        )
    )
    return dated_lines("\n".join(lines)), lines.index(entry) + 1


def repo_root(start: Path | None = None) -> Path:
    """The checkout that owns ``start``, found by walking up to a ``.git`` entry.

    ``.git`` is a *file* in a linked worktree and a directory in the main
    checkout, so the test is existence and never ``is_dir`` -- these audits are
    meant to be run inside a mutation tree.
    """
    here = (start or Path(__file__)).resolve()
    for candidate in (here, *here.parents):
        if (candidate / ".git").exists():
            return candidate
    message = f"no .git found at or above {here}"
    raise SystemExit(message)


def tracked_paths(root: Path) -> tuple[str, ...]:
    """Repository-relative paths git says are tracked, sorted.

    ``-z`` rather than newline-separated output: without it git quotes any path
    holding a non-ASCII byte, and a corpus seeded from documents with CJK titles
    is exactly where such a name appears.
    """
    environment = {
        name: value for name, value in os.environ.items() if name not in _INHERITED_GIT_OVERRIDES
    }
    completed = subprocess.run(  # noqa: S603 - fixed argv, no shell, no caller input
        ["git", "-c", f"safe.directory={root}", "ls-files", "--cached", "-z"],  # noqa: S607
        cwd=root,
        check=True,
        capture_output=True,
        env=environment,
        timeout=_GIT_TIMEOUT_SECONDS,
    )
    listing = completed.stdout.decode("utf-8", "surrogateescape")
    return tuple(sorted(entry for entry in listing.split("\0") if entry))


def is_governed(path: str) -> bool:
    """Whether a tracked path's text is inside every audit's population."""
    return not any(path.startswith(prefix) for prefix, _ in EXCLUDED_PREFIXES)


def governed_paths(root: Path) -> tuple[str, ...]:
    """Tracked, not excluded, and of a suffix these audits know how to read."""
    return tuple(
        path
        for path in tracked_paths(root)
        if is_governed(path) and Path(path).suffix in READ_SUFFIXES
    )


def skipped_suffixes(root: Path) -> dict[str, int]:
    """Governed tracked files these audits do not read, counted by suffix.

    The escape space, printed rather than assumed: a claim written into a file
    type nobody here parses is outside every count below, and this is the number
    that says how much room that is.
    """
    tally: dict[str, int] = {}
    for path in tracked_paths(root):
        if not is_governed(path):
            continue
        suffix = Path(path).suffix
        if suffix in READ_SUFFIXES:
            continue
        tally[suffix or "(none)"] = tally.get(suffix or "(none)", 0) + 1
    return dict(sorted(tally.items()))


def _collapse(lines: list[tuple[int, str]]) -> tuple[str, list[int]]:
    """Join ``(line number, text)`` pairs into one string, keeping the line map.

    The returned list is parallel to the string: element *i* is the source line
    the *i*-th character came from. That is what lets a sentence found in the
    joined text be reported at the line it opens on, which a naive join loses.
    """
    text: list[str] = []
    origin: list[int] = []
    for number, raw in lines:
        piece = raw.strip()
        if not piece:
            continue
        if text:
            text.append(" ")
            origin.append(number)
        text.append(piece)
        origin.extend([number] * len(piece))
    joined = "".join(text)
    collapsed = re.sub(r"\s+", " ", joined)
    return collapsed, _rebuild_origin(joined, origin)


def _rebuild_origin(joined: str, origin: list[int]) -> list[int]:
    """The line map for ``re.sub(r"\\s+", " ", joined)``, walked run by run.

    One entry per character of the collapsed string: a whitespace *run* becomes
    one space and contributes one entry, and every other character contributes
    itself.

    **This walks the runs rather than matching characters, which is round one's
    M-e.** The previous rebuild scanned ``joined`` for each character of the
    collapsed string, so when a run collapsed to a space it went looking for a
    literal ``" "`` -- and a run made of tabs has none. It skipped past the tabs
    to the next real space and every attribution after that point was off by a
    line. Measured: ``alpha\\t\\tbeta gamma`` on line 10 joined with a second
    source line reported ``beta`` at line 11.

    The old fast path had the same flaw one step earlier. It returned the
    unrebuilt map whenever the two strings were the same length, which is true of
    a *single* tab -- correct there by luck, since one character still maps to
    one character, but a rule that reads length rather than structure. There is
    one path now.
    """
    rebuilt: list[int] = []
    index = 0
    while index < len(joined):
        rebuilt.append(origin[index])
        if joined[index].isspace():
            while index < len(joined) and joined[index].isspace():
                index += 1
        else:
            index += 1
    return rebuilt


def _markdown_blocks(text: str) -> list[tuple[str, list[int]]]:
    blocks: list[tuple[str, list[int]]] = []
    current: list[tuple[int, str]] = []
    for number, raw in enumerate(text.splitlines(), start=1):
        line = _BLOCKQUOTE_MARKERS.sub("", raw)
        if not line.strip() or _BLOCK_START.match(line):
            if current:
                blocks.append(_collapse(current))
                current = []
            if not line.strip():
                continue
        current.append((number, line))
    if current:
        blocks.append(_collapse(current))
    return blocks


def _python_blocks(text: str) -> list[tuple[str, list[int]]]:
    """Every paragraph of a Python file, comment markers stripped.

    Code lines are in the population deliberately: a claim transcribed into a
    test fixture is a claim this repository ships, and the audits classify it as
    a probe rather than pretending not to see it.
    """
    blocks: list[tuple[str, list[int]]] = []
    current: list[tuple[int, str]] = []
    for number, raw in enumerate(text.splitlines(), start=1):
        if not raw.strip():
            if current:
                blocks.append(_collapse(current))
                current = []
            continue
        current.append((number, _PY_COMMENT.sub("", raw)))
    if current:
        blocks.append(_collapse(current))
    return blocks


def _yaml_blocks(text: str) -> list[tuple[str, list[int]]]:
    """Contiguous runs of YAML comment text, whole-line and inline alike.

    The inline form is not an afterthought: the sample project's annotation once
    moved to ``repositories:  # Nothing in `src/` reads this file`` and a
    whole-line rule could not see it (``test_raptor_config_claims.py`` records
    the measurement).
    """
    blocks: list[tuple[str, list[int]]] = []
    current: list[tuple[int, str]] = []
    for number, raw in enumerate(text.splitlines(), start=1):
        whole = _YAML_WHOLE_LINE_COMMENT.match(raw)
        inline = None if whole else _YAML_INLINE_COMMENT.search(raw)
        match = whole or inline
        if match is None:
            if current:
                blocks.append(_collapse(current))
                current = []
            continue
        current.append((number, match.group("comment")))
    if current:
        blocks.append(_collapse(current))
    return blocks


def _json_blocks(text: str) -> list[tuple[str, list[int]]]:
    """One block per source line, with JSON's string escapes undone.

    JSON strings cannot carry a raw newline, so a description is always on one
    line and there is no wrapping to join. The escapes are undone because the
    sentence a reader sees in a published schema is the decoded one --
    ``\\"block\\"`` is ``"block"`` to everybody downstream.
    """
    blocks: list[tuple[str, list[int]]] = []
    for number, raw in enumerate(text.splitlines(), start=1):
        decoded = raw.replace('\\"', '"').replace("\\\\", "\\")
        if decoded.strip():
            blocks.append(_collapse([(number, decoded)]))
    return blocks


_READERS: Final[dict[str, BlockReader]] = {
    ".md": _markdown_blocks,
    ".py": _python_blocks,
    ".json": _json_blocks,
    ".yaml": _yaml_blocks,
    ".yml": _yaml_blocks,
    ".toml": _python_blocks,
}


def sentences(root: Path, path: str) -> list[Sentence]:
    """Every sentence of one governed file, wrap-joined and line-attributed."""
    reader = _READERS.get(Path(path).suffix)
    if reader is None:
        return []
    text = (root / path).read_text(encoding="utf-8", errors="surrogateescape")
    found: list[Sentence] = []
    for block, origin in reader(text):
        offset = 0
        for piece in _SENTENCE_END.split(block):
            start = block.find(piece, offset)
            if start < 0:  # pragma: no cover - split pieces are always present
                start = offset
            if piece.strip():
                found.append(
                    Sentence(
                        path=path,
                        line=origin[start] if origin else 1,
                        text=piece.strip(),
                        block=block,
                    )
                )
            offset = start + len(piece)
    return found


def governed_sentences(root: Path) -> list[Sentence]:
    """Every sentence of every governed file, in path order."""
    return [sentence for path in governed_paths(root) for sentence in sentences(root, path)]


def load_json(root: Path, path: str) -> object:
    """Parse one tracked JSON file, so a key surface is derived and not typed."""
    return json.loads((root / path).read_text(encoding="utf-8"))
