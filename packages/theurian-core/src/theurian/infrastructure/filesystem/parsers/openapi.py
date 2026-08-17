"""OpenAPI, AsyncAPI, and JSON Schema parsers (FR-S1, FR-T1, SEC-10).

These formats are the reason ADR-0010 refuses to flatten structured sources to
prose. An OpenAPI document's operations, parameters, and response schemas are
what ``spec.getCoverage`` reads; extracting only its descriptions would make
coverage impossible to compute and impossible to add later without reprocessing
everything.

External ``$ref`` targets are recorded, never fetched. Resolving one would turn
every ingested document into a potential SSRF request (SEC-10, T-7).
"""

from __future__ import annotations

import json
from typing import Any, Final, final
from urllib.parse import urlparse

import yaml

from theurian.domain.knowledge import SourceAnchor
from theurian.domain.ports import NormalizedDocument
from theurian.domain.values import ContentHash, MediaType
from theurian.security.yaml_loading import load_yaml

#: HTTP methods OpenAPI defines as operations. A path item also carries
#: non-operation keys (`parameters`, `summary`), which must not be counted.
_HTTP_METHODS: Final = frozenset(
    {"get", "put", "post", "delete", "options", "head", "patch", "trace"}
)

#: Caps the extracted index. A generated OpenAPI document can be enormous, and
#: an unbounded walk is a resource-exhaustion vector (SEC-8).
MAX_OPERATIONS: Final = 5000
MAX_REFS: Final = 5000

#: Depth cap for the $ref walk. Matches the projection's cap so the two agree
#: about how deep a document can be before it is treated as pathological.
MAX_REF_DEPTH: Final = 64


@final
class OpenApiParser:
    """Extracts paths, operations, parameters, and responses as structure."""

    parser_id = "openapi"

    _SUPPORTED: Final = frozenset(
        {
            "application/vnd.oai.openapi",
            "application/vnd.oai.openapi+json",
            "application/vnd.aai.asyncapi",
            "application/schema+json",
        }
    )

    def supports(self, media_type: MediaType) -> bool:
        return media_type.value in self._SUPPORTED

    def parse(
        self, data: bytes, *, media_type: MediaType, anchor: SourceAnchor
    ) -> NormalizedDocument:
        """Parse an API description document.

        Raises:
            ValueError: If the bytes are not UTF-8 or the document is malformed.
        """
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError as exc:
            msg = f"{anchor.source_uri} is not valid UTF-8"
            raise ValueError(msg) from exc

        document = _load(text, anchor)

        structured: dict[str, Any] = dict(document)
        structured["_index"] = _build_index(document)

        return NormalizedDocument(
            title=_title(document, anchor),
            body=text,
            content_type=media_type,
            content_hash=ContentHash.of_text(text),
            anchors=(anchor,),
            structured=structured,
            metadata={
                "parser": self.parser_id,
                "operationCount": str(len(structured["_index"]["operations"])),
                "unresolvedRefCount": str(len(structured["_index"]["externalRefs"])),
            },
        )


def _load(text: str, anchor: SourceAnchor) -> dict[str, Any]:
    """Load JSON or YAML, whichever the document is.

    OpenAPI is defined for both, and a file's extension does not reliably say
    which -- `.yaml` files containing JSON are common, because JSON is valid
    YAML. Trying JSON first is cheap and unambiguous.
    """
    try:
        loaded: Any = json.loads(text)
    except json.JSONDecodeError:
        try:
            loaded = load_yaml(text)
        except yaml.YAMLError as exc:
            msg = f"{anchor.source_uri} is neither valid JSON nor valid YAML: {exc}"
            raise ValueError(msg) from exc
    except RecursionError as exc:
        # Mirrors `structured.py::JsonParser.parse`'s identical guard around
        # the identical call: a JSON document nested deep enough blows the
        # decoder's own recursion limit, and `RecursionError` is not a
        # `json.JSONDecodeError`, so it sailed past the `except` above and out
        # of this function uncaught (measured: 20,000 nested arrays).
        msg = f"{anchor.source_uri} is nested too deeply to parse"
        raise ValueError(msg) from exc

    if not isinstance(loaded, dict):
        msg = (
            f"{anchor.source_uri} parsed to {type(loaded).__name__}; an API description "
            f"must be a mapping at its root"
        )
        raise ValueError(msg)
    return loaded


def _build_index(document: dict[str, Any]) -> dict[str, Any]:
    """Extract the queryable surface: operations, schemas, and references.

    This is what makes `spec.getImplementationStatus` and `spec.getCoverage`
    answerable. Without it, an OpenAPI document is just a long string.
    """
    operations = _operations(document)
    return {
        "specVersion": _version(document),
        "operations": operations,
        "operationIds": [op["operationId"] for op in operations if op.get("operationId")],
        "schemaNames": _schema_names(document),
        "channels": _channels(document),
        # Recorded, never fetched (SEC-10, T-7).
        "externalRefs": _external_refs(document),
    }


def _version(document: dict[str, Any]) -> str | None:
    for key in ("openapi", "swagger", "asyncapi"):
        value = document.get(key)
        if isinstance(value, str):
            return value
    return None


def _operations(document: dict[str, Any]) -> list[dict[str, Any]]:
    paths = document.get("paths")
    if not isinstance(paths, dict):
        return []

    operations: list[dict[str, Any]] = []
    for path, item in paths.items():
        if not isinstance(item, dict) or len(operations) >= MAX_OPERATIONS:
            continue
        for method, operation in item.items():
            if not isinstance(method, str) or method.lower() not in _HTTP_METHODS:
                continue
            if not isinstance(operation, dict) or len(operations) >= MAX_OPERATIONS:
                continue
            operations.append(
                {
                    "path": str(path),
                    "method": method.lower(),
                    "operationId": operation.get("operationId"),
                    "summary": operation.get("summary"),
                    "tags": [t for t in operation.get("tags", []) if isinstance(t, str)],
                    "parameters": _parameter_names(operation.get("parameters")),
                    "responses": sorted(
                        str(code)
                        for code in (operation.get("responses") or {})
                        if isinstance(operation.get("responses"), dict)
                    ),
                    "deprecated": bool(operation.get("deprecated", False)),
                }
            )
    return operations


def _parameter_names(parameters: object) -> list[str]:
    if not isinstance(parameters, list):
        return []
    names: list[str] = []
    for parameter in parameters:
        if isinstance(parameter, dict) and isinstance(parameter.get("name"), str):
            names.append(parameter["name"])
    return names


def _schema_names(document: dict[str, Any]) -> list[str]:
    components = document.get("components")
    if isinstance(components, dict):
        schemas = components.get("schemas")
        if isinstance(schemas, dict):
            return [str(k) for k in schemas]

    # Swagger 2.0 put them at the root.
    definitions = document.get("definitions")
    if isinstance(definitions, dict):
        return [str(k) for k in definitions]
    return []


def _channels(document: dict[str, Any]) -> list[str]:
    """AsyncAPI channels, the rough analogue of OpenAPI paths."""
    channels = document.get("channels")
    return [str(k) for k in channels] if isinstance(channels, dict) else []


def _external_refs(document: dict[str, Any]) -> list[dict[str, str]]:
    """Collect ``$ref`` targets that point outside this document.

    Recorded rather than resolved. Fetching one would let any ingested document
    make Theurian issue an arbitrary request -- the SSRF path in T-7. A local
    ``#/...`` reference is internal and needs no note.
    """
    found: list[dict[str, str]] = []
    seen: set[str] = set()

    def walk(node: object, path: str, depth: int) -> None:
        if len(found) >= MAX_REFS or depth > MAX_REF_DEPTH:
            return
        if isinstance(node, dict):
            ref = node.get("$ref")
            if isinstance(ref, str) and not ref.startswith("#") and ref not in seen:
                seen.add(ref)
                found.append(
                    {
                        "ref": ref,
                        "at": path,
                        "scheme": urlparse(ref).scheme or "relative-file",
                        "resolved": "false",
                    }
                )
            for key, child in node.items():
                walk(child, f"{path}.{key}" if path else str(key), depth + 1)
        elif isinstance(node, list):
            for index, child in enumerate(node):
                walk(child, f"{path}[{index}]", depth + 1)

    walk(document, "", 0)
    return found


def _title(document: dict[str, Any], anchor: SourceAnchor) -> str:
    info = document.get("info")
    if isinstance(info, dict):
        title = info.get("title")
        if isinstance(title, str) and title.strip():
            version = info.get("version")
            if isinstance(version, str) and version.strip():
                return f"{title.strip()} {version.strip()}"
            return title.strip()

    if anchor.file_path:
        return anchor.file_path.rsplit("/", 1)[-1]
    return "Untitled API description"
