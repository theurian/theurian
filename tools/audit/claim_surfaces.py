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
    if len(collapsed) == len(joined):
        return collapsed, origin
    # Whitespace runs inside a single source line collapse too; rebuild the map
    # by walking both strings, which is exact and costs nothing at this size.
    rebuilt: list[int] = []
    index = 0
    for character in collapsed:
        while index < len(joined) and joined[index] != character:
            index += 1
        rebuilt.append(origin[index] if index < len(origin) else origin[-1])
        index += 1
    return collapsed, rebuilt


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
