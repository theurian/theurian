"""Deterministic text projection of structured data (ADR-0020).

The projection is what lexical search indexes. It is stored, chunked, and
embedded, so its stability is a correctness property: an unstable projection
means a rebuilt index does not equal the original one, and staleness detection
stops working.

Everything here is a pure function of the parsed document. No process state, no
environment, no locale, no reliance on Python's own repr conventions.
"""

from __future__ import annotations

from typing import Final

from theurian.domain.errors import InputTooLargeError

#: Maximum nesting the projection descends. Deeper levels are marked truncated
#: rather than dropped silently -- a projection that quietly omits content would
#: make search miss text the document actually contains.
MAX_DEPTH: Final = 24

#: Ceiling on projected characters (SEC-8). An unbounded projection of a deeply
#: nested document is a memory-exhaustion vector.
MAX_PROJECTION_CHARS: Final = 2 * 1024 * 1024

#: Marks where the projection stopped, so truncation is visible in the indexed
#: text rather than inferred from its absence.
DEPTH_MARKER: Final = "[truncated: nesting limit]"
SIZE_MARKER: Final = "[truncated: size limit]"

_PATH_SEPARATOR: Final = "."


def project(value: object, *, max_chars: int = MAX_PROJECTION_CHARS) -> str:
    """Render parsed structured data as deterministic, searchable text.

    Produces one ``key.path: value`` line per scalar, so a value keeps the
    context that makes it findable::

        outcomes.failure.code: CANCELLATION_NOT_ALLOWED

    Args:
        value: Parsed data -- mappings, sequences, and scalars.
        max_chars: Size ceiling. Exceeding it truncates with a visible marker.

    Returns:
        The projection. Identical input always yields identical output, on any
        machine, in any process.
    """
    lines: list[str] = []
    _walk(value, path=(), depth=0, lines=lines)

    text = "\n".join(lines)
    if len(text) > max_chars:
        # Cut on a line boundary so the last line is never a half-rendered
        # value that a reader would mistake for the real content.
        cut = text.rfind("\n", 0, max_chars)
        text = text[: cut if cut > 0 else max_chars] + f"\n{SIZE_MARKER}"
    return text


def project_checked(value: object, *, max_chars: int = MAX_PROJECTION_CHARS) -> str:
    """Project, raising rather than truncating when the limit is exceeded.

    Used where silent truncation would be wrong -- ingesting a document whose
    projection does not fit is a document Theurian cannot faithfully index, and
    saying so beats indexing a fraction of it.
    """
    lines: list[str] = []
    _walk(value, path=(), depth=0, lines=lines)
    text = "\n".join(lines)
    if len(text) > max_chars:
        raise InputTooLargeError("projected text size", max_chars, len(text))
    return text


def _walk(value: object, *, path: tuple[str, ...], depth: int, lines: list[str]) -> None:
    if depth > MAX_DEPTH:
        lines.append(f"{_render_path(path)}: {DEPTH_MARKER}")
        return

    if isinstance(value, dict):
        if not value:
            lines.append(f"{_render_path(path)}: {{}}")
            return
        for key, child in value.items():
            # Mapping keys are rendered through the same scalar function as
            # values, so a non-string key (legal in YAML and JSON-adjacent
            # formats) cannot produce Python's repr in the index.
            _walk(child, path=(*path, _render_scalar(key)), depth=depth + 1, lines=lines)
        return

    if isinstance(value, list | tuple):
        if not value:
            lines.append(f"{_render_path(path)}: []")
            return
        for index, child in enumerate(value):
            _walk(child, path=(*path, str(index)), depth=depth + 1, lines=lines)
        return

    lines.append(f"{_render_path(path)}: {_render_scalar(value)}")


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
