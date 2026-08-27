"""OpenAPI, AsyncAPI, and JSON Schema parsers (FR-S1, FR-T1, SEC-10).

These formats are the reason ADR-0010 refuses to flatten structured sources to
prose. An OpenAPI document's operations, parameters, and response schemas are
what ``spec.getCoverage`` reads; extracting only its descriptions would make
coverage impossible to compute and impossible to add later without reprocessing
everything.

External ``$ref`` targets are recorded, never fetched. Resolving one would turn
every ingested document into a potential SSRF request (SEC-10, T-7). What gets
recorded has to be faithful for the same reason: the scheme allowlist Milestone 7
owes will read that record, so a target destined for a host must never arrive
under a label that reads like a file on this machine (#203).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Final, Literal, final

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

#: Cap the size of the extracted index -- how many operations and how many
#: references this module will *record* from one document, which a generated
#: OpenAPI document can otherwise make enormous.
#:
#: They bound the record, not the traversal, and they never did. What bounds the
#: *node entries* of the traversal is the node-identity memo in
#: :func:`_external_refs`, added for
#: https://github.com/theurian/theurian/issues/245: before it, a document whose
#: aliases share one sub-object was walked once per *path* to that object rather
#: than once per object, so 694 bytes of YAML at 22 alias levels cost 11.51 s
#: (measured 2026-08-24) while recording a single reference and reaching neither
#: cap. Both bounds are needed and neither implies the other -- these caps do not
#: fire on the shape that made the walk expensive, and the memo says nothing
#: about how large a record a legitimate document may produce. A third quantity,
#: the per-child path the walk builds, is bounded by construction rather than by
#: a cap here: :func:`_external_refs` carries it as a tuple of segments and
#: renders it to a string only where a ref or a truncation is actually recorded
#: (https://github.com/theurian/theurian/issues/328) -- see that function's
#: docstring for what made the eager string build quadratic.
MAX_OPERATIONS: Final = 5000
MAX_REFS: Final = 5000

#: Depth cap for the $ref walk: below this a document is treated as pathological
#: and the walk stops descending, recording where it stopped rather than dropping
#: the subtree in silence (#203).
#:
#: Not the projection's cap, which an earlier version of this comment claimed it
#: matched: ``normalization/projection.py::MAX_DEPTH`` is 24. The two have never
#: been equal, and nothing here requires them to be.
MAX_REF_DEPTH: Final = 64

#: RFC 3986 §3.1: a scheme is a letter followed by letters, digits, ``+``, ``-``
#: and ``.``, terminated by a colon. Matched here rather than delegated to
#: ``urllib.parse``, whose two answers to "what scheme is this" both reached the
#: record and both were wrong (#203, each measured):
#:
#: - ``urlsplit`` reads the drive letter of ``C:\Windows\system32\x.json`` as the
#:   scheme ``c``;
#: - it *raises* ``ValueError("Invalid IPv6 URL")`` on ``http://[::1``, and that
#:   exception travelled out of ``parse`` and discarded the whole document --
#:   every operation, schema and other ``$ref`` in it included.
_SCHEME: Final = re.compile(r"[A-Za-z][A-Za-z0-9+.\-]*:")

#: What separates path segments in the forms a ``$ref`` arrives in. Backslash
#: belongs here because Windows and every browser accept it where a URL wants
#: ``/``, so ``\\host\share`` reaches the same host ``//host/x`` does.
_SEPARATORS: Final = frozenset("/\\")

#: Dropped from a reference before it is classified -- tab, newline and carriage
#: return anywhere, C0 controls and space at the ends. Classifying the raw string
#: instead would read ``"\t//evil.test/x.json"`` as a relative path while
#: everything that could ever fetch it sees the host ``evil.test`` (measured).
#:
#: The removal *set* is the one ``urlsplit`` uses; the ends are not. ``urlsplit``
#: strips leading C0-and-space only, while this strips both ends, which is the
#: WHATWG side of the mapping. Deliberate, and safe in the direction that
#: matters: trailing characters cannot move a classification from network to
#: local, because every label here is decided by the *front* of the reference --
#: the two separators, or the scheme that ends at the first colon.
_REMOVED_ANYWHERE: Final = str.maketrans({"\t": None, "\n": None, "\r": None})
_STRIPPED_AT_THE_ENDS: Final = "".join(chr(code) for code in range(0x21))

#: The labels ``scheme`` carries for a reference that names no scheme of its own
#: -- RFC 3986 §4.2's relative-reference forms, plus Windows's spelling of the
#: first. Two groups, disjoint, and the split is the whole point of #203: the
#: scheme allowlist T-7 owes (#129) will key on this field, and a target destined
#: for a *host* must not arrive under a name that reads like a local file.
#:
#: Nothing in ``src/`` consumes either set yet -- the gate that will is Milestone
#: 7's. They are published so that gate and the fidelity table in
#: ``tests/unit/test_ref_recording.py`` share one statement of the label space
#: rather than each carrying its own copy.
NETWORK_PATH_SCHEMES: Final = frozenset({"protocol-relative", "unc"})
LOCAL_PATH_SCHEMES: Final = frozenset({"relative-file", "absolute-file"})


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

        index = _build_index(document)
        structured: dict[str, Any] = dict(document)
        structured["_index"] = index

        return NormalizedDocument(
            title=_title(document, anchor),
            body=text,
            content_type=media_type,
            content_hash=ContentHash.of_text(text),
            anchors=(anchor,),
            structured=structured,
            metadata={
                "parser": self.parser_id,
                "operationCount": str(len(index["operations"])),
                # A count of this document's *distinct `$ref` strings*, and only
                # those: not occurrences, not distinct targets (two spellings of
                # one URL count twice), and not the other resolution keywords a
                # specification can carry -- `$dynamicRef`, `operationRef` and
                # the rest are outside this walk entirely (#246). A truncation
                # record counts too, because it stands for a subtree nobody
                # looked at; without it a document whose refs all sit past a walk
                # cap answered "no external references" (#203).
                #
                # So the published number is two populations added together, and
                # what it bounds depends on which of them is empty. With
                # `refWalkTruncated` false there are no truncation records and it
                # is exactly the distinct `$ref` strings. With it true the sum is
                # no longer a count of references *in either direction*, and in
                # particular it is not a floor under them: it can **over**count.
                # Measured 2026-08-24 -- a document holding no `$ref` at all,
                # nested 66 levels deep, publishes `externalRefs` empty and
                # `unresolvedRefCount` 1, the depth cut standing alone.
                #
                # What it never undercounts is the *uninspected surface*: a
                # subtree the walk declined to enter always leaves a record, so 0
                # means both "no reference found" and "nothing left unlooked-at".
                # That is the property #203 needed and the one a consumer may
                # lean on; "a lower bound for `$ref`" is the one it may not.
                #
                # Both keys stop at this object. `IngestionService._to_document`
                # carries `structured` into `IngestedDocument` and has no
                # metadata field to carry these into, so the record that survives
                # ingestion -- and the one a scheme allowlist will read (T-7,
                # #129) -- is `structured["_index"]`: `externalRefs`, and
                # `refWalkTruncations` non-empty for exactly the documents this
                # flag calls truncated.
                "unresolvedRefCount": str(
                    len(index["externalRefs"]) + len(index["refWalkTruncations"])
                ),
                "refWalkTruncated": "true" if index["refWalkTruncations"] else "false",
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
    refs = _external_refs(document)
    return {
        "specVersion": _version(document),
        "operations": operations,
        "operationIds": [op["operationId"] for op in operations if op.get("operationId")],
        "schemaNames": _schema_names(document),
        "channels": _channels(document),
        # Recorded, never fetched (SEC-10, T-7).
        "externalRefs": list(refs.found),
        # Where the walk stopped looking. Without it, a document whose refs all
        # sit past a walk cap is indistinguishable from one that has none (#203).
        "refWalkTruncations": list(refs.truncations),
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


@dataclass(frozen=True, slots=True)
class _RefWalk:
    """What the ``$ref`` walk found, and where it stopped looking.

    Two fields rather than one list because a cut subtree is not a reference: it
    is the admission that this document holds an unknown number of them.
    """

    found: tuple[dict[str, str], ...]
    truncations: tuple[dict[str, str], ...]


#: One step of a ``$ref`` path, tagged by how it renders rather than inspecting
#: the value at render time -- a mapping key that happens to read ``"3"`` must
#: still render with a leading dot, not as ``[3]``.
_RefPathKind = Literal["key", "index"]
_RefPathSegment = tuple[_RefPathKind, str]
_RefPath = tuple[_RefPathSegment, ...]


def _render_ref_path(path: _RefPath) -> str:
    """Render a ``$ref`` path exactly as the pre-#328 eager build did.

    ``f"{path}.{key}"`` for a mapping key, ``f"{path}[{index}]"`` for a sequence
    index, with no leading dot before the first segment. Called only where a ref
    or a truncation is actually recorded -- at most ``MAX_REFS`` plus two times
    per document -- rather than once per edge the walk crosses, which is the
    change #328 made: see :func:`_external_refs`. Each call still costs
    ``O(depth)`` on its own (the loop below rebuilds ``rendered`` once per
    segment), bounded by ``MAX_REF_DEPTH``, so total render cost across one
    document is bounded by ``MAX_REF_DEPTH * MAX_REFS`` -- a residual against
    the per-edge cost #328 removed, not a reintroduction of it: the pre-#328
    build paid this same per-call cost on *every* edge the walk crossed,
    unconditionally, not only where a ref ended up recorded.
    """
    rendered = ""
    for kind, value in path:
        rendered = (
            f"{rendered}[{value}]"
            if kind == "index"
            else (f"{rendered}.{value}" if rendered else value)
        )
    return rendered


def _external_refs(document: dict[str, Any]) -> _RefWalk:
    """Collect ``$ref`` targets that point outside this document.

    Recorded rather than resolved. Fetching one would let any ingested document
    make Theurian issue an arbitrary request -- the SSRF path in T-7. A
    same-document reference needs no note: ``#/components/x``, and the empty
    reference RFC 3986 §4.4 defines as "this document", both resolve inside the
    bytes already in hand.

    Both caps stop the walk where they are checked, and each records where it
    stopped. A cut that leaves no trace is worse than a low cap, because the
    document then reports *no* external references at all (#203).

    **The marker list is bounded separately from the traversal.** One record per
    reason and two reasons, so ``truncations`` holds at most two entries however
    many nodes sit at a cap -- a document can hold thousands, and one marker each
    would be a list the caller never asked for.

    **The traversal is bounded by node identity, not by the caps** (#245). A YAML
    alias is resolved by sharing the parsed object, so one sub-object can be
    reachable by exponentially many paths; walking it per path rather than per
    object made 694 bytes at 22 alias levels cost 11.51 s while recording a
    single reference and reaching neither cap. ``descended`` holds the id of
    every node this walk has already gone *into*, so each is entered once: the
    same document now costs 1.5 ms and 1,234 bytes at 40 levels 2.6 ms, where the
    unmemoised walk would not have finished. All measured 2026-08-24, and through
    ``OpenApiParser.parse``, so each figure is a whole parse rather than the walk
    alone.

    **What the memo bounds is node *entries*, not what an eager path string
    would have spent.** Before #328, every edge built its own path with
    ``f"{path}.{key}"``, which copies the parent's whole accumulated string on
    every child; nothing charged that copy, so a document with one long mapping
    key and a wide fan-out under it cost Theta(edges x path length), quadratic in
    the document's own size: measured 2026-08-24, ~0.53 MiB cost 0.21 s, ~1.07
    MiB 0.98 s, ~2.16 MiB 4.21 s and ~4.39 MiB 16.93 s -- four times the cost per
    doubling, with no reference recorded and neither cap reached. ``walk`` now
    carries the path as a :data:`_RefPath` tuple of un-rendered segments instead:
    appending one costs ``O(depth)`` -- bounded by ``MAX_REF_DEPTH`` -- never
    ``O(len of the rendered string)``, and :func:`_render_ref_path` is called
    only where a ref or a truncation is actually recorded. The same #328 document
    shapes now cost within measurement noise of an unmodified walk over the same
    structure with no long key at all.

    Three properties of *where* the check sits, each load-bearing:

    * It precedes both caps, so re-reaching a node that was already walked
      records no truncation. That subtree was inspected; "we did not look" would
      be false.
    * A node is added only when it is actually descended into, never when a cap
      turned it away. A node cut at depth 65 and later reached at depth 3 is
      therefore still walked, exactly as before.
    * What it can elide is bounded: a node already descended, whose ``$ref`` is
      already in ``seen`` and whose children were already walked. The single
      exception is a node first descended deep enough for a cap to fire *inside*
      it and later re-reached shallower, where the deeper look is skipped -- and
      that is precisely the case where a cut was recorded, so the caller is
      already told the walk stopped looking. The truncation is what stands in for
      the elided reference; the *count* it feeds is not a floor under the
      references and was never safe to read as one (see ``parse``'s own note on
      ``unresolvedRefCount``).
    """
    found: list[dict[str, str]] = []
    truncations: dict[str, dict[str, str]] = {}
    seen: set[str] = set()
    descended: set[int] = set()
    # `id()` is unique only among *live* objects, so a collected node's id could
    # be reused by a later one and skip a subtree that was never walked. Every
    # node here is reachable from `document`, which outlives the walk -- this
    # list makes that argument local to this function rather than a property of
    # whoever called it.
    alive: list[object] = []

    def cut(reason: str, path: _RefPath, limit: int) -> None:
        truncations.setdefault(
            reason, {"reason": reason, "at": _render_ref_path(path), "limit": str(limit)}
        )

    def record(ref: object, path: _RefPath) -> None:
        if not isinstance(ref, str) or ref in seen:
            return
        target = _as_a_fetcher_reads_it(ref)
        if not target or target.startswith("#"):
            return
        seen.add(ref)
        found.append(
            {
                "ref": ref,
                "at": _render_ref_path(path),
                "scheme": _ref_scheme(target),
                "resolved": "false",
            }
        )

    def walk(node: object, path: _RefPath, depth: int) -> None:
        if not isinstance(node, dict | list) or not node:
            # Neither cap may claim it cut something the node could not have
            # held. A scalar has no children at all, and an empty container has
            # none either -- emptiness is answerable without descending, which
            # is the whole reason this check can sit in front of a cap that
            # forbids descending. Both were measured claiming otherwise: a
            # `$ref` at exactly `MAX_REF_DEPTH` had its own string value marked
            # as an uninspected subtree, and an empty `{}` or `[]` one past the
            # cap made a document with *no* external references publish
            # `unresolvedRefCount` 1 and `refWalkTruncated` true.
            #
            # A non-empty container stays marked even when it happens to hold
            # only scalars: knowing that requires reading its children, which is
            # exactly the descent the cap refused, so "we did not look" remains
            # the honest answer there.
            return
        if id(node) in descended:
            return
        if len(found) >= MAX_REFS:
            cut("refCount", path, MAX_REFS)
            return
        if depth > MAX_REF_DEPTH:
            cut("depth", path, MAX_REF_DEPTH)
            return
        descended.add(id(node))
        alive.append(node)
        if isinstance(node, dict):
            record(node.get("$ref"), path)
            for key, child in node.items():
                walk(child, (*path, ("key", str(key))), depth + 1)
        else:
            for index, child in enumerate(node):
                walk(child, (*path, ("index", str(index))), depth + 1)

    walk(document, (), 0)
    return _RefWalk(found=tuple(found), truncations=tuple(truncations.values()))


def _as_a_fetcher_reads_it(ref: str) -> str:
    r"""Strip what every URL parser strips, before anything classifies ``ref``.

    The record keeps the reference verbatim; only the classification reads this
    form. Otherwise ``"\t//evil.test/x.json"`` records as a relative file while
    ``urlsplit`` -- and so anything that would ever fetch it -- sees the host
    ``evil.test``.
    """
    return ref.translate(_REMOVED_ANYWHERE).strip(_STRIPPED_AT_THE_ENDS)


def _names_an_authority(target: str) -> bool:
    r"""Whether ``target`` opens with the two separators that introduce a host.

    RFC 3986 §3.2 introduces an authority with ``//``; Windows spells the same
    thing ``\\``, and Windows and browsers alike accept the mixed ``/\`` and
    ``\/``. Reading the pair structurally covers all four without enumerating
    them, so a spelling nobody has met yet lands on the network side rather than
    the local one.
    """
    # Sliced rather than indexed so a reference shorter than two characters
    # yields "" and simply fails the membership test.
    return target[:1] in _SEPARATORS and target[1:2] in _SEPARATORS


def _ref_scheme(target: str) -> str:
    r"""Name what ``target`` points at, the way a fetcher would read it.

    This value is what a scheme allowlist will key on (T-7, #129), so the failure
    that matters is a target destined for a host arriving under a label that
    reads like a file on this machine. Before #203 that was the *default*:
    anything ``urlsplit`` found no scheme in recorded ``relative-file``, which
    made ``//evil.test/x.json`` and ``\\smb-host\share\x.json`` -- both of which
    name a host -- read as local files.

    A scheme-less reference is therefore classified by its structure, following
    RFC 3986 §4.2's three relative-reference forms:

    - ``//host/x.json`` -- a network-path reference -> ``protocol-relative``
    - ``\\host\share\x`` -- the same thing, spelled for Windows -> ``unc``
    - ``/etc/passwd`` -- an absolute-path reference -> ``absolute-file``
    - ``./local.yaml`` -- a relative-path reference -> ``relative-file``

    ``///x`` has an *empty* authority and is really a local absolute path; it
    records as network-destined anyway, because between two readings of a form
    that cannot be resolved without its base, the one that assumes a host is the
    one that fails closed.

    A reference that does carry a scheme records that scheme, lowercased. Two
    consequences are worth stating outright:

    - ``C:\Windows\system32\x.json`` is not the scheme ``c``. IANA registers no
      one-letter scheme, so the drive reading is the only one with instances in
      the wild. It joins ``absolute-file`` rather than getting a label of its own
      because what it shares with ``/etc/passwd`` is the property a gate cares
      about -- it resolves from a root, not from the referring document. But
      ``x://host/y`` keeps the scheme ``x``: a one-letter scheme that names an
      authority is network-destined whatever else it is. That rule is about the
      authority, not about the letter, so ``C://foo`` and ``C:\\host\x`` record
      the scheme ``c`` as well -- a drive letter *is* what they open with, and
      the sentence above does not hold for those two spellings. Neither is in
      :data:`LOCAL_PATH_SCHEMES` nor :data:`NETWORK_PATH_SCHEMES`, so an
      allowlist of either group refuses them: wrong-looking, and fail-closed.
    - ``file://evil.test/share/x.json`` records ``file``, which is faithful and
      still network-destined. Nothing here decides that for the gate: allowing
      ``file`` at all obliges it to inspect the authority, exactly as it obliges
      it to inspect the path of a ``file:///etc/shadow``. That residual is
      recorded in ``docs/security/threat-model.md`` (T-7).
    """
    if _names_an_authority(target):
        return "unc" if target.startswith("\\") else "protocol-relative"

    match = _SCHEME.match(target)
    if match is None:
        return "absolute-file" if target[:1] in _SEPARATORS else "relative-file"

    # `end()` sits one past the colon, which is not part of the scheme itself.
    scheme = target[: match.end() - 1].lower()
    if len(scheme) == 1 and not _names_an_authority(target[match.end() :]):
        return "absolute-file"
    return scheme


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
