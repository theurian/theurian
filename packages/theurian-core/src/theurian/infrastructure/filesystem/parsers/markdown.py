"""Markdown parser (FR-S1, ADR-0019).

Markdown is the recommended format for human prose knowledge, so this is the
parser most documents go through. It extracts a heading tree and code fences as
structure, and handles front matter per ADR-0019: parsed, preserved as data,
never allowed to govern anything.
"""

from __future__ import annotations

import re
from typing import Any, Final, final

import yaml

from theurian.domain.ingestion import ParseWarning
from theurian.domain.knowledge import SourceAnchor
from theurian.domain.ports import NormalizedDocument
from theurian.domain.values import MARKDOWN, ContentHash, MediaType
from theurian.security.yaml_loading import load_yaml

#: Fields the migration governs. Front matter that sets one of these is ignored
#: *and reported*, because a silently ignored `status: approved` is exactly the
#: case where an author believes something is approved and it is not.
GOVERNED_FIELDS: Final = frozenset(
    {
        "status",
        "trustlevel",
        "trust_level",
        "sensitivity",
        "owner",
        "validfrom",
        "valid_from",
        "validto",
        "valid_to",
        "kind",
        "namespace",
        "acl",
        "aclgroup",
        "acl_group",
        "tenant",
        "tenantid",
        "tenant_id",
    }
)

#: Front matter delimited by `---`, only at the very start of the file. The
#: trailing newlines are consumed as part of the block: the blank line after the
#: closing `---` is the block's own formatting, and leaving it in the body would
#: make identical prose hash differently depending on whether the file has front
#: matter at all.
_FRONT_MATTER = re.compile(r"\A---[ \t]*\r?\n(.*?)\r?\n---[ \t]*(?:\r?\n)*", re.DOTALL)

_ATX_HEADING = re.compile(r"^(#{1,6})[ \t]+(.+?)[ \t]*#*[ \t]*$", re.MULTILINE)

#: A fence *opener* line -- an optional language tag, no other content. A
#: *closer* line permits no language tag at all: ``` ```lang``` `` never closes
#: anything, only bare ``` `` ` `` does. Matched with :meth:`re.Pattern.fullmatch`
#: against one already-split line rather than combined into the single
#: ``^```...$(.*?)^```...$`` pattern issue #331 replaced -- that pattern's lazy
#: ``.*?`` had to rescan every remaining line to confirm a closer was truly
#: absent, once per unclosed opener, which is Theta(n^2) on a document that is
#: n unclosed openers: measured on 68e8a0b, 156 KB (32,000 openers) took 25.3 s
#: and doubling the input roughly quadrupled it. ``MAX_FENCES`` bounds what is
#: *recorded*; it never bounded what the scan *spent* to get there.
_FENCE_OPENER_LINE = re.compile(r"```([A-Za-z0-9_+-]*)[ \t]*")
_FENCE_CLOSER_LINE = re.compile(r"```[ \t]*")

#: Guards a pathological document from producing an unbounded structure tree.
MAX_HEADINGS: Final = 2000
MAX_FENCES: Final = 1000


@final
class MarkdownParser:
    """Parses Markdown into a body, a heading tree, and code fences."""

    parser_id = "markdown"

    _SUPPORTED: Final = frozenset({"text/markdown", "text/x-markdown"})

    def supports(self, media_type: MediaType) -> bool:
        return media_type.value in self._SUPPORTED

    def parse(
        self, data: bytes, *, media_type: MediaType, anchor: SourceAnchor
    ) -> NormalizedDocument:
        """Parse Markdown.

        Raises:
            ValueError: If the bytes are not valid UTF-8.
        """
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError as exc:
            msg = f"{anchor.source_uri} is not valid UTF-8"
            raise ValueError(msg) from exc

        front_matter, body, warnings = _split_front_matter(text)
        headings = _headings(body)
        fences = _fences(body)

        structured: dict[str, Any] = {
            "headings": headings,
            "codeFences": fences,
        }
        if front_matter is not None:
            structured["frontMatter"] = front_matter

        return NormalizedDocument(
            title=_title(headings, front_matter),
            # Hashed over the body only, so adding front matter to a file does
            # not change the hash of what the document actually says.
            body=body,
            content_type=MARKDOWN if media_type.value == "text/markdown" else media_type,
            content_hash=ContentHash.of_text(body),
            anchors=(anchor,),
            structured=structured,
            metadata={
                "parser": self.parser_id,
                "headingCount": str(len(headings)),
                "warnings": ";".join(w.code for w in warnings),
            },
        )

    def warnings_for(self, text: str) -> tuple[ParseWarning, ...]:
        """Warnings this parser emits for ``text`` (the ``WarningSource`` protocol).

        Separate from ``parse`` because ``NormalizedDocument`` is the
        ``SourceParser`` port's return type and carries no warning field.
        Widening that port for one parser's benefit would push a Markdown
        concern into every future adapter (ADR-0019).
        """
        _, _, warnings = _split_front_matter(text)
        return warnings


def _split_front_matter(
    text: str,
) -> tuple[dict[str, Any] | None, str, tuple[ParseWarning, ...]]:
    """Separate front matter from the body (ADR-0019)."""
    match = _FRONT_MATTER.match(text)
    if match is None:
        return None, text, ()

    body = text[match.end() :]
    raw = match.group(1)

    try:
        loaded = load_yaml(raw)
    except (yaml.YAMLError, ValueError) as exc:
        # Malformed front matter does not fail the document. The body is still
        # perfectly good knowledge, and refusing it would be a poor trade.
        return (
            None,
            body,
            (
                ParseWarning(
                    code="front-matter-unparseable",
                    message=f"Front matter is not valid YAML and was ignored: {exc}",
                    location="frontMatter",
                ),
            ),
        )

    if not isinstance(loaded, dict):
        return (
            None,
            body,
            (
                ParseWarning(
                    code="front-matter-not-a-mapping",
                    message=(
                        f"Front matter parsed to {type(loaded).__name__}, not a mapping, "
                        f"and was ignored."
                    ),
                    location="frontMatter",
                ),
            ),
        )

    warnings = tuple(
        ParseWarning(
            code="front-matter-governed-field",
            message=(
                f"Front matter sets {key!r}, which is governed by the migration and was "
                f"ignored. Set it in the migration's metadata block instead (ADR-0019)."
            ),
            location=f"frontMatter.{key}",
        )
        for key in loaded
        if isinstance(key, str) and key.lower() in GOVERNED_FIELDS
    )

    return loaded, body, warnings


def _headings(body: str) -> list[dict[str, Any]]:
    """Extract ATX headings with their level and character offset.

    Offsets let a retrieval result point at the section a match came from
    rather than at the whole document.
    """
    headings: list[dict[str, Any]] = []
    for match in _ATX_HEADING.finditer(body):
        if len(headings) >= MAX_HEADINGS:
            break
        headings.append(
            {
                "level": len(match.group(1)),
                "text": match.group(2).strip(),
                "offset": match.start(),
                "line": body.count("\n", 0, match.start()) + 1,
            }
        )
    return headings


def _fences(body: str) -> list[dict[str, Any]]:
    """Extract fenced code blocks with their language.

    Kept as structure because a code fence is usually the concrete example a
    reader is looking for, and its language is a useful filter.

    A single forward pass over lines (issue #331), matching the semantics
    ``re.MULTILINE`` gives ``^``/``$`` exactly: splitting on ``"\\n"`` produces
    the same segments those anchors bound, without ever letting ``.`` cross a
    line it does not need to. Two anchored, non-backtracking line patterns
    replace the old combined opener-through-closer regex, whose lazy ``.*?``
    had to rescan every remaining line before it could conclude a closer was
    absent -- once per unclosed opener: measured on 68e8a0b (pre-#331), 156 KB
    (32,000 unclosed openers) cost 25.3 s and doubled roughly quadratically.
    The same shape costs 0.0065 s here, a ~3900x improvement on the input the
    rewrite exists for. The constant recorded below is a trade against *that*
    win, not a regression against it -- it is irrelevant at the sizes almost
    every real document hits, and only visible on an adversarial document at
    the ingestion cap.

    Materializes ``lines`` and ``line_starts`` up front, so peak memory is
    O(line-count): measured 2026-08-27, ~202 MB at an 8 MiB document (the
    ``MAX_SOURCE_FILE_BYTES`` cap), linear with input size and not
    super-linear. A typical few-KB Markdown file costs microseconds and
    kilobytes either way; this is a bounded residual against the quadratic
    CPU blowup #331 removed, recorded here so a future change does not
    reintroduce super-linear memory without anyone noticing.
    """
    lines = body.split("\n")
    line_starts: list[int] = []
    offset = 0
    for line in lines:
        line_starts.append(offset)
        offset += len(line) + 1

    # Every closer-matching line also matches the opener pattern (a bare ` ``` `
    # is a valid, empty-language opener too), so this list is computed once and
    # consulted by a pointer that only moves forward -- never rebuilt per
    # opener, which is exactly the rescan issue #331 removed.
    #
    # `startswith` filters before either pattern gets a chance to run: both
    # patterns require the line to open with the same three characters they
    # test for, so a line that fails this check cannot fullmatch either one --
    # this is a pure short-circuit, not a fidelity-affecting change. It matters
    # because `fullmatch` costs far more per call than `startswith`, and this
    # loop was paying that cost on every line of the document, fenced or not:
    # measured 2026-08-27, an 8 MiB all-newline document (no line ever a
    # candidate) cost 11.7 s / 202 MB running the regex unconditionally versus
    # a fraction of that once non-candidate lines are filtered first.
    closer_lines = [
        i
        for i, line in enumerate(lines)
        if line.startswith("```") and _FENCE_CLOSER_LINE.fullmatch(line)
    ]

    fences: list[dict[str, Any]] = []
    closer_index = 0
    line_index = 0
    while line_index < len(lines):
        if len(fences) >= MAX_FENCES:
            break
        if not lines[line_index].startswith("```"):
            line_index += 1
            continue
        opener = _FENCE_OPENER_LINE.fullmatch(lines[line_index])
        if opener is None:
            line_index += 1
            continue
        while closer_index < len(closer_lines) and closer_lines[closer_index] <= line_index:
            closer_index += 1
        if closer_index >= len(closer_lines):
            # No closer remains anywhere after this opener, so none remains
            # after any later opener either (closer indices only increase):
            # the whole rest of the scan is unclosed and the loop can stop
            # rather than repeat this same negative search per opener.
            break
        closer_line = closer_lines[closer_index]
        content_start = line_starts[line_index] + len(lines[line_index])
        fences.append(
            {
                "language": opener.group(1) or None,
                "line": line_index + 1,
                "characters": line_starts[closer_line] - content_start,
            }
        )
        line_index = closer_line + 1
    return fences


def _title(headings: list[dict[str, Any]], front_matter: dict[str, Any] | None) -> str:
    """Derive a display title.

    The first heading wins, because it is what a reader sees. Front matter
    ``title`` is a fallback -- a display concern, and the one front-matter field
    ADR-0019 permits to be read.
    """
    for heading in headings:
        if heading["level"] == 1:
            return str(heading["text"])
    if headings:
        return str(headings[0]["text"])
    if front_matter is not None:
        candidate = front_matter.get("title")
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()
    return "Untitled"
