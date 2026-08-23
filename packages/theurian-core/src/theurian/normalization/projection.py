"""Deterministic text projection of structured data (ADR-0020).

The projection is what lexical search indexes. It is stored, chunked, and
embedded, so its stability is a correctness property: an unstable projection
means a rebuilt index does not equal the original one, and staleness detection
stops working.

Everything here is a pure function of the parsed document. No process state, no
environment, no locale, no reliance on Python's own repr conventions.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Final

from theurian.domain.errors import InputTooLargeError

#: Maximum nesting the projection descends. Deeper levels are marked truncated
#: rather than dropped silently -- a projection that quietly omits content would
#: make search miss text the document actually contains.
MAX_DEPTH: Final = 24

#: Ceiling on projected characters (SEC-8). An unbounded projection of a deeply
#: nested document is a memory-exhaustion vector.
#:
#: It bounds what the walk **spends**, not only what it keeps. Until issue #232
#: it was checked after ``_walk`` had built and joined every line, which bounded
#: only the return value: PyYAML resolves an alias by sharing the object rather
#: than copying it, so a document whose aliases nest stays cheap to parse and
#: expands only when the walk materialises it into text. Measured on this branch
#: before the fix, against the truncating :func:`project` the ingest path calls,
#: with the return value capped at 2 MiB throughout: 297 B of YAML cost 0.23 s
#: and 71 MB of RSS, 351 B cost 2.05 s and 334 MB, 405 B cost 19.76 s and 2.8 GB.
MAX_PROJECTION_CHARS: Final = 2 * 1024 * 1024

#: Ceiling on nodes the walk visits (SEC-8, issue #232), stated as its own bound
#: rather than left to follow from the one above.
#:
#: Characters bound the walk's *emitting* end: every leaf and every empty
#: container produces a line. What they do not price is the traversal between
#: them -- a non-empty container emits nothing at all, so at :data:`MAX_DEPTH`
#: a document can spend two dozen visits per line of output. At these constants
#: the two budgets land close together, and a walk of a million nodes usually
#: emits past 2 MiB and is refused by characters first, so this is a backstop
#: rather than the binding constraint. It is written down anyway because the
#: alternative is an *unwritten* bound resting on ``MAX_DEPTH`` and on the
#: shortest line the renderer can produce, and moving either would silently
#: unbound the traversal.
#:
#: Measured on this branch with the character budget raised out of the way, so
#: that this ceiling is what fires: reaching it costs 0.6 s.
MAX_PROJECTION_NODES: Final = 1_000_000

#: Marks where the projection stopped, so truncation is visible in the indexed
#: text rather than inferred from its absence.
DEPTH_MARKER: Final = "[truncated: nesting limit]"
SIZE_MARKER: Final = "[truncated: size limit]"

#: The node ceiling's own marker. Separate from :data:`SIZE_MARKER` because the
#: two say different things to a reader who finds one in the index: the size
#: marker means the document's text did not fit, this one means its *structure*
#: was too large to walk, and a document can hit it while producing very little
#: text at all.
EXPANSION_MARKER: Final = "[truncated: expansion limit]"

_PATH_SEPARATOR: Final = "."


@dataclass(frozen=True, slots=True)
class _Exhausted:
    """Which budget ran out, in the terms each caller needs to say so.

    ``observed`` is what the walk had spent when it stopped, which is a *lower
    bound* on what finishing would have cost. Measuring the true figure means
    building the whole expansion, and that cost is the one the budget exists to
    refuse (issue #232).
    """

    limit_name: str
    limit: int
    observed: int
    marker: str


@dataclass(slots=True)
class _Spend:
    """The budget, and what the walk has consumed of it.

    Mutable, alone in a module whose contract is purity, and for the same reason
    ``lines`` is: one accumulator threaded through a recursion, created per call
    and never shared. Nothing here reaches process state -- two calls with equal
    input still produce equal output, which is what ADR-0020 requires.
    """

    max_chars: int
    max_nodes: int
    chars: int = 0
    nodes: int = 0
    exhausted: _Exhausted | None = None

    def visit(self) -> bool:
        """Charge one visited node; ``False`` once the ceiling is passed.

        Charged on entry rather than on emission, because the visits this bounds
        are exactly the ones that emit nothing: a shared sub-object reached
        through many aliases costs a full traversal every time it is reached.
        """
        self.nodes += 1
        if self.nodes > self.max_nodes:
            self.exhausted = _Exhausted(
                "projected node count", self.max_nodes, self.nodes, EXPANSION_MARKER
            )
            return False
        return True

    def emit(self, lines: list[str], line: str) -> bool:
        """Append one line and charge it; ``False`` once the budget is passed.

        ``chars`` is kept exactly equal to ``len("\\n".join(lines))`` -- the
        separator is charged for every line but the first -- so that stopping
        here and truncating afterwards produce the same text the unbounded walk
        produced. What changes is the work, not the projection.
        """
        self.chars += len(line) + (1 if lines else 0)
        lines.append(line)
        if self.chars > self.max_chars:
            self.exhausted = _Exhausted(
                "projected text size", self.max_chars, self.chars, SIZE_MARKER
            )
            return False
        return True


def _projected(value: object, *, max_chars: int, max_nodes: int) -> tuple[list[str], _Spend]:
    """Walk ``value`` under a budget, returning the lines and what they cost."""
    lines: list[str] = []
    spend = _Spend(max_chars=max_chars, max_nodes=max_nodes)
    _walk(value, path=(), depth=0, lines=lines, spend=spend)
    return lines, spend


def project(
    value: object,
    *,
    max_chars: int = MAX_PROJECTION_CHARS,
    max_nodes: int = MAX_PROJECTION_NODES,
) -> str:
    """Render parsed structured data as deterministic, searchable text.

    Produces one ``key.path: value`` line per scalar, so a value keeps the
    context that makes it findable::

        outcomes.failure.code: CANCELLATION_NOT_ALLOWED

    Args:
        value: Parsed data -- mappings, sequences, and scalars.
        max_chars: Size ceiling. Exceeding it truncates with a visible marker.
        max_nodes: Ceiling on visited nodes. Exceeding it truncates too, with a
            marker of its own -- a document can exhaust it while producing
            almost no text (issue #232).

    Returns:
        The projection. Identical input always yields identical output, on any
        machine, in any process.
    """
    lines, spend = _projected(value, max_chars=max_chars, max_nodes=max_nodes)

    text = "\n".join(lines)
    if len(text) > max_chars:
        # Cut on a line boundary so the last line is never a half-rendered
        # value that a reader would mistake for the real content.
        cut = text.rfind("\n", 0, max_chars)
        return text[: cut if cut > 0 else max_chars] + f"\n{SIZE_MARKER}"
    if spend.exhausted is not None:
        # Only the node ceiling reaches here: a character exhaustion leaves
        # `chars` -- and so `len(text)` -- past `max_chars`, which the branch
        # above has already answered. The marker still has to be appended for
        # this one, because the text it stopped short of is under the size
        # limit and nothing else would say it is incomplete.
        return f"{text}\n{spend.exhausted.marker}" if text else spend.exhausted.marker
    return text


def project_checked(
    value: object,
    *,
    max_chars: int = MAX_PROJECTION_CHARS,
    max_nodes: int = MAX_PROJECTION_NODES,
) -> str:
    """Project, raising rather than truncating when a limit is exceeded.

    Used where silent truncation would be wrong -- ingesting a document whose
    projection does not fit is a document Theurian cannot faithfully index, and
    saying so beats indexing a fraction of it.

    The raise fires on the same documents it always did -- the walk stops the
    moment the budget is passed, and that is exactly when the finished text
    would have exceeded ``max_chars``. What differs is
    :attr:`~theurian.domain.errors.InputTooLargeError.observed`, which is now
    the spend at the stop rather than the size of a projection this no longer
    builds.
    """
    lines, spend = _projected(value, max_chars=max_chars, max_nodes=max_nodes)
    if spend.exhausted is not None:
        exhausted = spend.exhausted
        raise InputTooLargeError(exhausted.limit_name, exhausted.limit, exhausted.observed)
    return "\n".join(lines)


def _walk(
    value: object, *, path: tuple[str, ...], depth: int, lines: list[str], spend: _Spend
) -> bool:
    """Render one node, returning ``False`` once a budget is spent.

    Every caller propagates that ``False`` immediately instead of finishing its
    own loop, which is what makes the budget bound the traversal rather than
    only its result.
    """
    if not spend.visit():
        return False
    if depth > MAX_DEPTH:
        return spend.emit(lines, f"{_render_path(path)}: {DEPTH_MARKER}")
    if isinstance(value, dict):
        return _walk_mapping(value, path=path, depth=depth, lines=lines, spend=spend)
    if isinstance(value, list | tuple):
        return _walk_sequence(value, path=path, depth=depth, lines=lines, spend=spend)
    return spend.emit(lines, f"{_render_path(path)}: {_render_scalar(value)}")


def _walk_mapping(
    value: Mapping[object, object],
    *,
    path: tuple[str, ...],
    depth: int,
    lines: list[str],
    spend: _Spend,
) -> bool:
    if not value:
        return spend.emit(lines, f"{_render_path(path)}: {{}}")
    for key, child in value.items():
        # Mapping keys are rendered through the same scalar function as values,
        # so a non-string key (legal in YAML and JSON-adjacent formats) cannot
        # produce Python's repr in the index.
        child_path = (*path, _render_scalar(key))
        if not _walk(child, path=child_path, depth=depth + 1, lines=lines, spend=spend):
            return False
    return True


def _walk_sequence(
    value: Sequence[object],
    *,
    path: tuple[str, ...],
    depth: int,
    lines: list[str],
    spend: _Spend,
) -> bool:
    if not value:
        return spend.emit(lines, f"{_render_path(path)}: []")
    for index, child in enumerate(value):
        child_path = (*path, str(index))
        if not _walk(child, path=child_path, depth=depth + 1, lines=lines, spend=spend):
            return False
    return True


def _render_path(path: tuple[str, ...]) -> str:
    """Render a key path, or a placeholder for a bare top-level scalar."""
    return _PATH_SEPARATOR.join(path) if path else "value"


def _render_scalar(value: object) -> str:
    """Render one scalar in a spelling that does not depend on Python.

    ``str()`` would emit ``True`` and ``None``, which are Python's spellings
    rather than the source format's, and would make the index disagree with the
    document a user is reading.
    """
    if value is None:
        return "null"
    if isinstance(value, bool):
        # Checked before int: bool is a subclass of int, so the order matters.
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        # repr round-trips exactly for float in Python 3.1+, and is stable
        # across versions in a way that str() historically was not.
        return repr(value)
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def summarize_structure(value: object, *, max_entries: int = 40) -> tuple[str, ...]:
    """List the top-level key paths present, for a compact structural overview.

    Used by ``theurian ingest`` to report what a document contains without
    printing the whole projection.
    """
    if not isinstance(value, dict):
        return ()
    paths: list[str] = []
    for key, child in value.items():
        rendered = _render_scalar(key)
        if isinstance(child, dict):
            paths.append(f"{rendered}/ ({len(child)} keys)")
        elif isinstance(child, list | tuple):
            paths.append(f"{rendered}[] ({len(child)} items)")
        else:
            paths.append(rendered)
        if len(paths) >= max_entries:
            paths.append("...")
            break
    return tuple(paths)
