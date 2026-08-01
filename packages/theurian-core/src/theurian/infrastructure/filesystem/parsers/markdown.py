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
_FENCE = re.compile(r"^```([A-Za-z0-9_+-]*)[ \t]*$(.*?)^```[ \t]*$", re.MULTILINE | re.DOTALL)

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
    """
    fences: list[dict[str, Any]] = []
    for match in _FENCE.finditer(body):
        if len(fences) >= MAX_FENCES:
            break
        fences.append(
            {
                "language": match.group(1) or None,
                "line": body.count("\n", 0, match.start()) + 1,
                "characters": len(match.group(2)),
            }
        )
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
