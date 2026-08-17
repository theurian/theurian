"""YAML and JSON parsers (FR-S1, FR-S2, ADR-0010, ADR-0020).

A structured source keeps its structure. The text projection exists so lexical
search can reach it, not to replace it -- flattening to prose is what makes
specification coverage and contradiction detection impossible later.
"""

from __future__ import annotations

import json
from typing import Any, Final, final

import yaml

from theurian.domain.errors import InputTooLargeError
from theurian.domain.knowledge import SourceAnchor
from theurian.domain.ports import NormalizedDocument
from theurian.domain.values import ContentHash, MediaType
from theurian.normalization.projection import project_checked
from theurian.security.yaml_loading import MAX_YAML_BYTES, load_yaml

#: Keys checked, in order, for a human-facing title.
_TITLE_KEYS: Final = ("title", "name", "summary", "id")

#: Guards against a JSON document nested deeply enough to exhaust the C parser's
#: stack. Python's json module raises RecursionError rather than segfaulting, but
#: a clear limit gives a better message than a stack trace (SEC-8).
MAX_JSON_BYTES: Final = MAX_YAML_BYTES


@final
class YamlParser:
    """Parses YAML into its full document tree plus a projection."""

    parser_id = "yaml"

    _SUPPORTED: Final = frozenset({"application/yaml", "text/yaml", "text/x-yaml"})

    def supports(self, media_type: MediaType) -> bool:
        return media_type.value in self._SUPPORTED

    def parse(
        self, data: bytes, *, media_type: MediaType, anchor: SourceAnchor
    ) -> NormalizedDocument:
        """Parse YAML.

        Uses the safe, timestamp-preserving loader: ``yaml.safe_load`` would
        turn an RFC 3339 string into a ``datetime``, and the projection would
        then render Python's datetime spelling into the search index rather than
        the text the document contains.

        Raises:
            ValueError: If the bytes are not UTF-8 or the YAML is malformed.
            InputTooLargeError: If the document or its projection exceeds a limit.
        """
        text = _decode(data, anchor)
        try:
            loaded = load_yaml(text)
        except yaml.YAMLError as exc:
            msg = f"{anchor.source_uri} is not valid YAML: {exc}"
            raise ValueError(msg) from exc
        except ValueError as exc:
            # `load_yaml`'s own `RecursionError` -> `ValueError` translation
            # (`security/yaml_loading.py`) has no `anchor` to name, so its
            # message is the bare "YAML document exceeds the parser's safe
            # nesting depth" -- re-wrapped here with `anchor.source_uri` for
            # the identical reason the `yaml.YAMLError` clause immediately
            # above it, and `_decode`'s `UnicodeDecodeError` clause, both
            # already carry it: this is the only *ValueError* `load_yaml` can
            # raise into this method that did not, until now. `load_yaml` can
            # also raise `InputTooLargeError` (`SecurityError`, not
            # `ValueError`) for an oversized document; that one is not caught
            # by this clause and passes through this method unwrapped, still
            # without `anchor.source_uri` -- a deliberate contract this
            # method's own `Raises:` section already documents separately,
            # not a gap this clause is meant to close.
            msg = f"{anchor.source_uri}: {exc}"
            raise ValueError(msg) from exc

        return _document(
            loaded, text=text, media_type=media_type, anchor=anchor, parser_id=self.parser_id
        )


@final
class JsonParser:
    """Parses JSON into its full document tree plus a projection."""

    parser_id = "json"

    _SUPPORTED: Final = frozenset({"application/json", "text/json"})

    def supports(self, media_type: MediaType) -> bool:
        return media_type.value in self._SUPPORTED or media_type.value.endswith("+json")

    def parse(
        self, data: bytes, *, media_type: MediaType, anchor: SourceAnchor
    ) -> NormalizedDocument:
        """Parse JSON.

        Raises:
            ValueError: If the bytes are not UTF-8 or the JSON is malformed.
            InputTooLargeError: If the document or its projection exceeds a limit.
        """
        if len(data) > MAX_JSON_BYTES:
            raise InputTooLargeError("JSON document size", MAX_JSON_BYTES, len(data))

        text = _decode(data, anchor)
        try:
            loaded = json.loads(text)
        except json.JSONDecodeError as exc:
            msg = f"{anchor.source_uri} is not valid JSON: {exc}"
            raise ValueError(msg) from exc
        except RecursionError as exc:
            msg = f"{anchor.source_uri} is nested too deeply to parse"
            raise ValueError(msg) from exc

        return _document(
            loaded, text=text, media_type=media_type, anchor=anchor, parser_id=self.parser_id
        )


def _decode(data: bytes, anchor: SourceAnchor) -> str:
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as exc:
        msg = f"{anchor.source_uri} is not valid UTF-8"
        raise ValueError(msg) from exc


def _document(
    loaded: object,
    *,
    text: str,
    media_type: MediaType,
    anchor: SourceAnchor,
    parser_id: str,
) -> NormalizedDocument:
    """Build the normalized document from parsed structured data."""
    structured: dict[str, Any] = loaded if isinstance(loaded, dict) else {"value": loaded}

    return NormalizedDocument(
        # The body is the source text as written. Re-serialising the parse would
        # discard comments, quoting style, and formatting -- all of which a
        # reader looking at a retrieval result expects to see.
        title=_title(structured, anchor),
        body=text,
        content_type=media_type,
        content_hash=ContentHash.of_text(text),
        anchors=(anchor,),
        structured=structured,
        metadata={
            "parser": parser_id,
            "topLevelKeys": str(len(structured)),
        },
    )


def _title(structured: dict[str, Any], anchor: SourceAnchor) -> str:
    """Find a human-facing title, falling back to the file name."""
    for key in _TITLE_KEYS:
        candidate = structured.get(key)
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()

    if anchor.file_path:
        return anchor.file_path.rsplit("/", 1)[-1]
    return "Untitled"


def build_projection(structured: dict[str, Any]) -> str:
    """Project structured data to searchable text (ADR-0020).

    Separate from parsing so a caller that only needs the structure does not pay
    for the projection, and so the determinism rules live in one place.
    """
    return project_checked(structured)
