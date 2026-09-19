"""The extraction grammar: turn free-text issue prose into citation candidates.

An open issue's body and comments cite files, line numbers, test names,
constants, commit SHAs, ADRs, and cross-referenced issues -- the premises a
later merge silently invalidates. This module finds those citations; it does
not judge them. :mod:`premise_check` runs each candidate against the current
checkout and grades the result.

**Pure by construction: no subprocess, no filesystem read, no network.**
:data:`premise_check`'s cousins -- #615 (sha anchors in governed prose) and
#692 (ADR-cited test names), both out of this scope -- read a narrower slice
of the same repository text and need the same eight-shape grammar. A module
that only pattern-matches strings is the one both can import without taking
on this instrument's git plumbing.

Eight kinds, in the order :func:`extract_citations` checks them:

``path``
    A slash-bearing token starting with a known top-level directory
    (``packages/``, ``tests/``, ``tools/``, ``docs/``, ``.github/``,
    ``scripts/``), or a bare filename with a recognised extension that
    resolves to exactly one tracked path via :func:`unique_basenames` --
    see that function's docstring for why an ambiguous basename is dropped
    rather than guessed at.
``path_line``
    ``<path>:<N>``, folded into one token so :mod:`premise_check` can verify
    the line as well as the file in one step.
``test_name``
    ``test_[a-z0-9_]+``, bare or backticked.
``constant``
    A backticked ``[A-Z][A-Z0-9_]{2,}`` token. Backticks are required: an
    unbacked all-caps run is as likely to be an acronym in prose as a named
    constant, and there is no regex that tells them apart.
``sha``
    ``[0-9a-f]{7,40}`` containing at least one digit *and* one ``a``-``f``
    letter. The two-class requirement is what keeps a plain lowercase word
    that happens to fall inside ``[0-9a-f]`` -- ``deadbeef``, ``decade`` --
    from reading as a commit reference; it does not cut everything, and it
    is not meant to. UNKNOWN and a wasted verification command are the cost
    of a false positive here, never a false DANGLING.
``adr``
    ``ADR-\\d{4}``.
``issue_ref``
    ``#\\d+``.
``symbol``
    A backticked identifier, optionally dotted (``Class.method``) or
    trailed with ``()``, that matched none of the kinds above. The
    catch-all is deliberate: a reader who backticks a name expects it
    checked, and :mod:`premise_check` grades an unresolved symbol UNKNOWN
    rather than DANGLING for exactly this reason (its match is
    best-effort).

Recall, not precision, is the goal: a citation this grammar misses is
silently never checked, while one it over-extracts costs one wasted
verification command, visible in the report, and never a false DANGLING --
a path-shaped token (one with a slash or a known extension) consumes its
span the moment :func:`extract_citations` recognises it as such, whether or
not it goes on to resolve into an emitted ``path`` citation, so a
``test_name`` run embedded in a path the extractor rejected (an unrecognised
prefix, an ambiguous basename) or truncated (a glob, a brace expansion) is
dropped along with it before either reaches :mod:`premise_check`, which in
turn downgrades any DANGLING outside its own allowed kinds (``path``,
``path_line``, ``adr``, ``test_name``) to UNKNOWN. Exotic forms -- a path
inside a URL, a SHA split across a line wrap -- are the documented gap
:mod:`premise_check`'s UNKNOWN status exists to escape into.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Final, Literal

CitationKind = Literal[
    "path", "path_line", "test_name", "constant", "sha", "adr", "issue_ref", "symbol"
]


@dataclass(frozen=True, slots=True)
class Citation:
    kind: CitationKind
    token: str


#: Top-level directories that make a slash-bearing token a path on sight, with
#: no need to consult the tree. Matches this repository's own layout; a
#: prefix outside this list still resolves through the bare-filename path
#: below when its basename is unique.
_PATH_PREFIXES: Final = ("packages/", "tests/", "tools/", "docs/", ".github/", "scripts/")

#: Extensions that make a *bare* filename (no slash) worth resolving against
#: :func:`unique_basenames`. Kept narrow: an issue mentioning "the README" in
#: prose is not a citation, but "core.yml" or "sweep_verdict.py" usually is.
_PATH_EXTENSIONS: Final = (".py", ".md", ".yml", ".yaml", ".toml", ".sh", ".json")

#: Trailing characters a sentence puts next to a path that the path's own
#: character class ([A-Za-z0-9_./-]) cannot otherwise distinguish from real
#: content -- a period closing a sentence, a slash closing a directory
#: mention. Every other punctuation mark a token could end with (comma,
#: colon, closing paren, quote) is already outside that class, so the token
#: regex never includes it in the first place.
_TRAILING_PUNCTUATION: Final = "./"

#: A character immediately following a matched path token that means the
#: token is a truncated glob (``tools/premise_*.py`` -> ``tools/premise_``),
#: not a real path -- none of these are in the path token's own character
#: class, so the match already stops right before one.
_GLOB_METACHARACTERS: Final = frozenset("*?[]{}")

_PATH_TOKEN_RE: Final = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_./-]*")
_LINE_SUFFIX_RE: Final = re.compile(r":(\d+)\b")
_TEST_NAME_RE: Final = re.compile(r"\btest_[a-z0-9_]+")
_CONSTANT_RE: Final = re.compile(r"^[A-Z][A-Z0-9_]{2,}$")
_SHA_RE: Final = re.compile(r"\b[0-9a-f]{7,40}\b")
_ADR_RE: Final = re.compile(r"ADR-\d{4}")
_ISSUE_REF_RE: Final = re.compile(r"#\d+")
_SYMBOL_RE: Final = re.compile(r"^[A-Za-z_][\w.]*(?:\(\))?$")

#: A single-backtick span on one line. Deliberately not multiline: every
#: value this module classifies from inside one (a constant, a symbol) is a
#: token, and a code span spanning a blank line is CommonMark's own signal
#: that the author meant a fenced block, not an inline value.
_BACKTICK_SPAN_RE: Final = re.compile(r"`([^`\n]+)`")


def unique_basenames(tracked_paths: Iterable[str]) -> dict[str, str]:
    """Repo-relative paths indexed by basename, keeping only unambiguous ones.

    A bare filename mentioned in an issue -- ``core.yml`` for
    ``.github/workflows/core.yml`` -- only becomes a ``path`` citation when
    exactly one tracked file carries that basename. Two or more, and the
    basename is dropped from the map entirely: :func:`extract_citations`
    then leaves the mention uncited rather than guessing which file was
    meant, because a wrong guess reports DANGLING or INTACT about a file the
    issue never named.
    """
    by_basename: dict[str, list[str]] = {}
    for path in tracked_paths:
        by_basename.setdefault(PurePosixPath(path).name, []).append(path)
    return {name: paths[0] for name, paths in by_basename.items() if len(paths) == 1}


def _strip_trailing_punctuation(token: str) -> str:
    return token.rstrip(_TRAILING_PUNCTUATION)


def _looks_like_path(token: str) -> bool:
    return any(token.startswith(prefix) for prefix in _PATH_PREFIXES)


def _has_known_extension(token: str) -> bool:
    return token.endswith(_PATH_EXTENSIONS)


def _is_path_shaped(raw: str) -> bool:
    """A slash or a known extension is what makes ``raw`` a path *candidate*
    -- distinct from whether it goes on to resolve. A bare word like
    ``test_zzz_case`` has neither, so it is never path-shaped: only a token
    the extractor actually considered as a path consumes a span (round 2
    HIGH-1) -- a blanket rule would swallow every bare ``test_`` mention in
    ordinary prose along with it.
    """
    return "/" in raw or _has_known_extension(raw)


#: `re.Match.end()` after a `{...}` group and any path-token tail following
#: it (the `.py` closing `tools/{a,b}.py`) always matches, since `*` allows
#: zero length -- there is no `None` case to handle.
_PATH_CONTINUATION_RE: Final = re.compile(r"[A-Za-z0-9_./-]*")


def _extend_past_brace_group(text: str, open_brace_pos: int) -> int:
    """The index just past a ``{...}`` group and any path-token tail that
    follows it, or the string's end if the brace never closes -- a malformed
    glob is consumed whole, never partially, so its comma-separated interior
    (``{test_a,test_b}``) cannot surface as a standalone ``test_name``.
    """
    close = text.find("}", open_brace_pos)
    end = close + 1 if close != -1 else len(text)
    tail = _PATH_CONTINUATION_RE.match(text, end)
    return tail.end() if tail is not None else end


def _iter_path_citations(
    text: str, tracked_basenames: Mapping[str, str]
) -> Iterator[tuple[Citation | None, tuple[int, int]]]:
    """Path and path_line citations, each paired with the span of text it
    consumed. A rejected or truncated path-shaped match yields ``None`` for
    its citation but still yields its span: :func:`extract_citations` uses
    every yielded span to keep a `test_name` match embedded in a path -- one
    actually emitted (round 1 HIGH-1, face A) or one the extractor merely
    considered (round 2 HIGH-1) -- from also surfacing on its own.
    """
    for match in _PATH_TOKEN_RE.finditer(text):
        raw = match.group(0)
        path_shaped = _is_path_shaped(raw)
        end = match.end()
        if end < len(text) and text[end] in _GLOB_METACHARACTERS:
            # A truncated glob ("tools/premise_*.py" -> "tools/premise_") or
            # a brace expansion ("tools/{a,b}.py"), whose comma-separated
            # interior needs its own span extended past the closing brace.
            span_end = _extend_past_brace_group(text, end) if text[end] == "{" else end
            if path_shaped:
                yield None, (match.start(), span_end)
            continue
        token = _strip_trailing_punctuation(raw)
        if not token:
            continue
        resolved: str | None = None
        if "/" in token:
            if _looks_like_path(token):
                resolved = token
        elif _has_known_extension(token):
            resolved = tracked_basenames.get(token)
        if resolved is None:
            if path_shaped:
                yield None, match.span()
            continue
        suffix = _LINE_SUFFIX_RE.match(text, match.end())
        if suffix is not None:
            citation = Citation("path_line", f"{resolved}:{suffix.group(1)}")
            yield citation, (match.start(), suffix.end())
        else:
            yield Citation("path", resolved), (match.start(), match.end())


def _span_overlaps(span: tuple[int, int], consumed: Iterable[tuple[int, int]]) -> bool:
    start, end = span
    return any(start < other_end and other_start < end for other_start, other_end in consumed)


def _looks_like_sha(token: str) -> bool:
    has_digit = any(char.isdigit() for char in token)
    has_hex_letter = any(char in "abcdef" for char in token)
    return has_digit and has_hex_letter


def _iter_sha_citations(text: str) -> Iterator[Citation]:
    for match in _SHA_RE.finditer(text):
        token = match.group(0)
        if _looks_like_sha(token):
            yield Citation("sha", token)


def _iter_backtick_spans(text: str) -> Iterator[str]:
    for match in _BACKTICK_SPAN_RE.finditer(text):
        yield match.group(1)


def _iter_constant_citations(text: str) -> Iterator[Citation]:
    for content in _iter_backtick_spans(text):
        if _CONSTANT_RE.fullmatch(content):
            yield Citation("constant", content)


def _iter_symbol_citations(text: str) -> Iterator[Citation]:
    for content in _iter_backtick_spans(text):
        if _CONSTANT_RE.fullmatch(content):
            continue
        if _ADR_RE.fullmatch(content) or _ISSUE_REF_RE.fullmatch(content):
            continue
        if _TEST_NAME_RE.fullmatch(content):
            continue
        if _looks_like_path(content) or _has_known_extension(content):
            continue
        if _SYMBOL_RE.fullmatch(content):
            yield Citation("symbol", content)


def extract_citations(
    body: str, comments: Sequence[str], *, tracked_basenames: Mapping[str, str]
) -> tuple[Citation, ...]:
    """Every citation candidate in one issue's body and comments.

    Comments are part of the premise, not an addendum: a correction posted
    after the fact ("actually this moved to ...") cites what the issue is
    really about, and dropping comments would grade against a premise the
    thread itself already retracted.

    ``tracked_basenames`` is the caller's :func:`unique_basenames` map, built
    once per run from the checkout's own tracked files -- passing an empty
    mapping is how a caller with no such map opts out of bare-filename
    resolution rather than seeing it silently degrade.

    Joined on a blank line rather than concatenated directly: none of the
    eight patterns' character classes include ``\\n``, so a token cannot
    span the body/comment boundary, but two texts glued edge to edge could
    still let one's trailing word-character run bleed into the next's
    leading one.

    The result is deduplicated by ``(kind, token)`` and sorted the same way,
    which is what makes two extractions of the same input byte-identical --
    :mod:`premise_check`'s determinism guarantee starts here.
    """
    joined = "\n\n".join((body, *comments))
    found: set[Citation] = set()
    path_hits = list(_iter_path_citations(joined, tracked_basenames))
    consumed = [span for _citation, span in path_hits]
    found.update(citation for citation, _span in path_hits if citation is not None)
    found.update(
        Citation("test_name", match.group(0))
        for match in _TEST_NAME_RE.finditer(joined)
        # A trailing "_" is only ever a truncation artefact (round 2 HIGH-1
        # belt-and-braces): a real test function name never ends there.
        if not match.group(0).endswith("_") and not _span_overlaps(match.span(), consumed)
    )
    found.update(_iter_constant_citations(joined))
    found.update(_iter_sha_citations(joined))
    found.update(Citation("adr", token) for token in _ADR_RE.findall(joined))
    found.update(Citation("issue_ref", token) for token in _ISSUE_REF_RE.findall(joined))
    found.update(_iter_symbol_citations(joined))
    return tuple(sorted(found, key=lambda citation: (citation.kind, citation.token)))
