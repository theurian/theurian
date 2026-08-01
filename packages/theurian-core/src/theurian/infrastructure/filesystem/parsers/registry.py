"""Parser registry and media-type detection (FR-S4).

Adding a format is a new adapter registered here. No domain or application
change, which is the property the ``SourceParser`` port exists to provide.
"""

from __future__ import annotations

import re
from pathlib import PurePosixPath
from typing import Final, final

from theurian.domain.ports import SourceParser
from theurian.domain.values import JSON, MARKDOWN, YAML, MediaType
from theurian.infrastructure.filesystem.parsers.markdown import MarkdownParser
from theurian.infrastructure.filesystem.parsers.openapi import OpenApiParser
from theurian.infrastructure.filesystem.parsers.structured import JsonParser, YamlParser

OPENAPI: Final = MediaType("application/vnd.oai.openapi")
ASYNCAPI: Final = MediaType("application/vnd.aai.asyncapi")
JSON_SCHEMA: Final = MediaType("application/schema+json")

#: Extension to media type. The starting guess, refined by content sniffing --
#: an extension is a hint an author chose, not a fact about the bytes.
_BY_EXTENSION: Final = {
    ".md": MARKDOWN,
    ".markdown": MARKDOWN,
    ".yaml": YAML,
    ".yml": YAML,
    ".json": JSON,
}

#: Bytes examined when sniffing. Enough to see a root-level key without reading
#: a large file twice.
_SNIFF_BYTES: Final = 8192

#: Matches a root-level version key in either serialisation. The `[{,]` branch
#: is what catches JSON, where the key follows a brace or comma rather than a
#: line start -- without it, `swagger.json` fell through to the generic JSON
#: parser and silently lost its operation index.
_OPENAPI_KEY = re.compile(rb'(?:^|[{,])\s*["\']?(openapi|swagger)["\']?\s*:', re.MULTILINE)
_ASYNCAPI_KEY = re.compile(rb'(?:^|[{,])\s*["\']?asyncapi["\']?\s*:', re.MULTILINE)
_SCHEMA_KEY = re.compile(rb'["\']\$schema["\']\s*:')


def detect_media_type(path: PurePosixPath, data: bytes) -> MediaType | None:
    """Determine a document's media type from its name and its content.

    Content wins over extension. An OpenAPI document is conventionally
    `openapi.yaml`, and treating it as plain YAML would discard the operation
    index that specification coverage depends on -- a silent loss that only
    shows up milestones later, as a query returning nothing.

    Returns:
        The media type, or ``None`` when nothing claims the file.
    """
    from_extension = _BY_EXTENSION.get(path.suffix.lower())
    if from_extension is None:
        return None

    if from_extension is MARKDOWN:
        return MARKDOWN

    head = data[:_SNIFF_BYTES]
    if _OPENAPI_KEY.search(head):
        return OPENAPI
    if _ASYNCAPI_KEY.search(head):
        return ASYNCAPI
    if _SCHEMA_KEY.search(head) and _looks_like_json_schema(head):
        return JSON_SCHEMA

    return from_extension


def _looks_like_json_schema(head: bytes) -> bool:
    """Distinguish a JSON Schema from a document that merely cites one.

    A `$schema` key appears in plenty of configuration files that are not
    schemas. Requiring a schema-defining keyword alongside it avoids sending
    every `.json` config through the schema parser.
    """
    try:
        text = head.decode("utf-8", errors="ignore")
    except UnicodeDecodeError:  # pragma: no cover - errors="ignore" cannot raise
        return False
    return any(keyword in text for keyword in ('"$defs"', '"definitions"', '"properties"', '"$id"'))


@final
class ParserRegistry:
    """Dispatches a document to the parser that handles its media type."""

    def __init__(self, parsers: tuple[SourceParser, ...] | None = None) -> None:
        self._parsers: tuple[SourceParser, ...] = parsers or default_parsers()

    def for_media_type(self, media_type: MediaType) -> SourceParser | None:
        """The first registered parser claiming ``media_type``.

        Order matters: the OpenAPI parser is registered before the generic YAML
        and JSON parsers, so a document that is both valid YAML and a valid
        OpenAPI description goes to the one that extracts more.
        """
        for parser in self._parsers:
            if parser.supports(media_type):
                return parser
        return None

    @property
    def parser_ids(self) -> tuple[str, ...]:
        return tuple(p.parser_id for p in self._parsers)

    @property
    def supported_extensions(self) -> tuple[str, ...]:
        return tuple(sorted(_BY_EXTENSION))


def default_parsers() -> tuple[SourceParser, ...]:
    """The parser set Milestone 2 ships.

    OpenAPI precedes YAML and JSON deliberately -- see ``for_media_type``.
    """
    return (MarkdownParser(), OpenApiParser(), YamlParser(), JsonParser())
