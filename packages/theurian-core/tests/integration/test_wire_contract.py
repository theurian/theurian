"""Real MCP responses against their published schemas (FR-R5).

``tests/unit/test_schemas.py`` checks properties *of the schemas* -- that they
parse, that they close over unknown properties, that a published enum matches
the code's constants. None of that had ever run a tool, and for a whole
milestone ``retrieval-result.schema.json`` rejected every real
``knowledge.search`` result: it required four fields nothing emits and
declared neither of the two every ranked hit carries. Nothing noticed, because
every assertion in that file was about the schema and none had ever been
compared against a response. A hand-written fixture would have passed while
the wire shape stayed wrong, which is exactly how the drift survived a whole
milestone.

This module is the missing half. The input comes from ``build_server(registry)
.call_tool`` -- the same entry point the transport uses -- against a project
built by the real CLI: a Git working tree, ``theurian init``, two migrations
applied, and an index build. The whole response is validated, not the hits
alone: ``count``, ``results`` and the ``retrieval`` block are as much of the
contract as a hit is, and a schema that covers only part of a payload leaves
the rest free to drift.

``project.list`` is here for the same reason and was in a worse state: adding
two required fields to its response turned nothing red across 186 tests,
because no assertion anywhere in the repository pinned its shape at all. It
needs no corpus -- the tool reads the registry file and nothing else -- so its
captures are built from a registry written by hand rather than from the project
below.

``knowledge.status`` is the third, and it arrives with its schema (#19). What
that schema publishes is a security promise -- ``itemsByStatus`` may carry the
surfaceable statuses and nothing else -- so it is checked against a project
whose items are *all* retired, which is the corpus a hand-written fixture never
contains and the one where a leak would show.

It lives here rather than beside the schema tests because it touches
subprocess, SQLite and the filesystem -- ``tests/unit/`` is I/O-free by
convention in this repository, and the directory is how that split is actually
enforced. The ``pytest.mark.unit`` marker declared in ``pyproject.toml`` is
applied nowhere, so ``-m unit`` alone would not have caught this module
sitting in the wrong place; the ``@pytest.mark.integration`` markers below are
correct, but the directory is the real gate.
"""

from __future__ import annotations

import asyncio
import json
import pathlib
import subprocess
from datetime import datetime
from typing import Any, Final, NamedTuple

import pytest
from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError
from referencing import Registry, Resource

REPO_ROOT = pathlib.Path(__file__).resolve().parents[4]
SCHEMAS = REPO_ROOT / "schemas"

ALL_SCHEMA_PATHS = sorted(SCHEMAS.rglob("*.schema.json"))

SEARCH_RESPONSE = "mcp/knowledge-search-response.schema.json"
PROJECT_LIST_RESPONSE = "mcp/project-list-response.schema.json"
STATUS_RESPONSE = "mcp/knowledge-status-response.schema.json"


def _load(relative: str) -> dict[str, Any]:
    loaded: dict[str, Any] = json.loads((SCHEMAS / relative).read_text(encoding="utf-8"))
    return loaded


def _registry() -> Registry[Any]:
    """Every published schema, addressable by its ``$id``.

    The response schema ``$ref``s the result and metadata schemas across files.
    Without a registry those references are resolved over the network -- which
    the offline CI job blocks and a developer's machine does not -- so a schema
    that silently validated nothing would look green locally and fail in CI, or
    worse, the other way round.
    """
    resources = [
        (schema["$id"], Resource.from_contents(schema))
        for schema in (json.loads(path.read_text(encoding="utf-8")) for path in ALL_SCHEMA_PATHS)
    ]
    return Registry().with_resources(resources)


def _validator(relative: str) -> Draft202012Validator:
    return Draft202012Validator(_load(relative), registry=_registry())


CONFORMANCE_MIGRATION = """apiVersion: theurian.dev/v1
id: 01K1{letter}AAAAA01234567890ABCDE
createdAt: 2026-08-02T1{ordinal}:00:00+09:00
author: engineer@example.com
operations:
  - op: createItem
    itemId: architecture.{slug}
    kind: architecture
    namespace: backend
    owner: platform-team
  - op: upsertRevision
    itemId: architecture.{slug}
    revisionId: 01K1{letter}AAREV01234567890ABCDE
    contentFile: ../knowledge/architecture/{slug}.md
    metadata:
      title: {title}
      contentType: text/markdown
      kind: architecture
      namespace: backend
      status: {status}
      owner: platform-team
      trustLevel: reviewed
{provenance}"""

#: How a revision satisfies INV-8: an anchor back to a repository...
ANCHORED = """      sourceAnchors:
        - provider: git
          sourceUri: git://demo/{slug}.md
"""

#: ...or the explicit declaration that it originates in Theurian, which is the
#: other half of the disjunction and the only way a revision reaches the wire
#: with ``sourceAnchors: []``.
AUTHORED_HERE = """      labels:
        - authored-in-theurian
"""


class Document(NamedTuple):
    """One item in the conformance corpus.

    A record rather than a tuple because ``provenance`` is the seventh field and
    the one whose absence let a whole class of response go unvalidated: both
    documents carried an anchor, so nothing here had ever seen the empty
    ``sourceAnchors`` that INV-8 explicitly permits.
    """

    letter: str
    ordinal: int
    slug: str
    title: str
    status: str
    provenance: str
    body: str

    @property
    def item_id(self) -> str:
        return f"architecture.{self.slug}"


#: Disjoint vocabulary throughout, so one query selects exactly one document.
CONFORMANCE_DOCUMENTS = (
    Document(
        "A",
        0,
        "auth-policy",
        "Authentication policy",
        "approved",
        ANCHORED,
        "# Authentication policy\n\nEvery inbound call carries a signed token.\n",
    ),
    Document(
        "B",
        1,
        "caching-draft",
        "Caching draft",
        "draft",
        ANCHORED,
        "# Caching draft\n\nA caching proposal nobody has reviewed.\n",
    ),
    # INV-8's documented exception. `sourceAnchors` is empty on the wire for
    # this one, on both answer paths, and the published schema required
    # `minItems: 1` until this document was added -- so a legitimate result
    # violated the contract Theurian publishes, in a state no test could reach.
    Document(
        "C",
        2,
        "homegrown-routing",
        "Homegrown routing decision",
        "approved",
        AUTHORED_HERE,
        "# Homegrown routing decision\n\nWe keep the gateway routing table in code.\n",
    ),
)

#: The item ids that are *expected* to arrive without anchors. Derived from the
#: corpus rather than restated, so adding a document cannot leave this behind.
ANCHORLESS_ITEM_IDS = frozenset(
    document.item_id for document in CONFORMANCE_DOCUMENTS if document.provenance is AUTHORED_HERE
)

#: Named responses, captured once. Each reaches a different combination of the
#: fields a schema is most likely to be wrong about: the optional ones, the
#: null-carrying ones, and the empty-result case that would validate vacuously.
CAPTURES = (
    "unranked",
    "unranked-authored-here",
    "unranked-no-match",
    "unranked-overlong-query",
    "ranked",
    "ranked-authored-here",
    "ranked-dense",
    "ranked-no-match",
    "unapproved-not-indexed",
)


def _cli(*args: str) -> None:
    from typer.testing import CliRunner

    from theurian.cli.main import app

    result = CliRunner().invoke(app, [*args, "--json"], catch_exceptions=False)
    assert result.exit_code == 0, result.stdout + (result.stderr or "")


async def _call_search(registry: Any, arguments: dict[str, Any]) -> dict[str, Any]:
    """One search through the same entry point the transport uses."""
    from theurian.daemon.runner import build_server

    result = await build_server(registry).call_tool(
        "knowledge.search", {"projectId": "demo", **arguments}
    )
    structured = getattr(result, "structuredContent", None)
    if structured is not None:
        payload: dict[str, Any] = structured
        return payload
    content: Any = result.content  # type: ignore[union-attr]
    loaded: dict[str, Any] = json.loads(content[0].text)
    return loaded


def _search(registry: Any, **arguments: Any) -> dict[str, Any]:
    return asyncio.run(_call_search(registry, arguments))


def _build_conformance_project(root: pathlib.Path) -> None:
    """A Git working tree carrying :data:`CONFORMANCE_DOCUMENTS`, applied."""
    for args in (
        ["git", "init", "-q", "-b", "main"],
        ["git", "config", "user.email", "test@example.com"],
        ["git", "config", "user.name", "Test"],
    ):
        subprocess.run(args, cwd=root, check=True, capture_output=True)  # noqa: S603

    _cli("init")
    for document in CONFORMANCE_DOCUMENTS:
        slug = document.slug
        (root / f".theurian/knowledge/architecture/{slug}.md").write_text(
            document.body, encoding="utf-8"
        )
        migration = CONFORMANCE_MIGRATION.format(
            letter=document.letter,
            ordinal=document.ordinal,
            slug=slug,
            title=document.title,
            status=document.status,
            provenance=document.provenance.format(slug=slug),
        )
        (
            root / f".theurian/migrations/01K1{document.letter}AAAAA01234567890ABCDE-{slug}.yaml"
        ).write_text(migration, encoding="utf-8")
    _cli("project", "register")
    _cli("migrate", "apply")


def _capture(tmp: pathlib.Path) -> dict[str, dict[str, Any]]:
    """Every response in :data:`CAPTURES`, in the order their state requires.

    Four must be taken before an index exists and five after, so this is a
    sequence rather than a set of independent fixtures -- and it is why the whole
    thing is built once and shared.

    **Synchronous.** ``theurian index build`` embeds through ``asyncio.run``,
    which raises inside an already-running loop, so an async test cannot build an
    index in its own body.
    """
    from theurian.application.project_service import ProjectRegistry

    root = tmp / "demo"
    root.mkdir()
    data_dir = tmp / "datadir"

    monkey = pytest.MonkeyPatch()
    monkey.setenv("THEURIAN_DATA_DIR", str(data_dir))
    monkey.chdir(root)
    try:
        _build_conformance_project(root)
        registry = ProjectRegistry.default(data_dir)

        unranked = {
            "unranked": _search(registry, query="signed token"),
            # The INV-8 exception, on the path that reads the canonical store
            # directly. `sourceAnchors` arrives empty in both of these.
            "unranked-authored-here": _search(registry, query="gateway routing"),
            "unranked-no-match": _search(registry, query="kubernetes"),
            # Clamped at 2,000 characters before the search runs, so the echoed
            # `query` is the string that was actually searched for.
            "unranked-overlong-query": _search(registry, query="token " * 400),
        }
        # Approved-only, deliberately: it is what makes `includeUnapproved`
        # fall back rather than quietly answering from an index that holds no
        # drafts.
        _cli("index", "build")
        ranked = {
            "ranked": _search(registry, query="signed token"),
            "ranked-authored-here": _search(registry, query="gateway routing"),
            "ranked-dense": _search(registry, query="signed token", useDense=True),
            "ranked-no-match": _search(registry, query="kubernetes"),
            "unapproved-not-indexed": _search(registry, query="caching", includeUnapproved=True),
        }
    finally:
        monkey.undo()

    return {**unranked, **ranked}


@pytest.fixture(scope="module")
def real_responses(tmp_path_factory: pytest.TempPathFactory) -> dict[str, dict[str, Any]]:
    """Real MCP output, built once for the whole module.

    Module-scoped because a project, two migrations and an index build cost more
    than every assertion below put together.
    """
    return _capture(tmp_path_factory.mktemp("schema-conformance"))


@pytest.mark.integration
@pytest.mark.parametrize("name", CAPTURES)
def test_a_real_search_response_validates_against_its_published_schema(
    real_responses: dict[str, dict[str, Any]], name: str
) -> None:
    """The test whose absence let the contract drift through a whole milestone.

    Catches both directions at once: ``additionalProperties: false`` fails an
    emitted field nobody declared, and ``required`` fails a declared field
    nothing emits.
    """
    _validator(SEARCH_RESPONSE).validate(real_responses[name])


@pytest.mark.integration
def test_the_captured_corpus_reaches_both_answer_paths(
    real_responses: dict[str, dict[str, Any]],
) -> None:
    """Guards the test above, which is otherwise satisfiable by seven copies of
    one empty response.

    This is not a hypothetical failure mode in this repository: a breaking change
    to this response shape once passed the entire suite because all nineteen
    tests calling ``knowledge.search`` took the fallback, and nothing said so.
    """
    assert set(real_responses) == set(CAPTURES)

    answered = [r for r in real_responses.values() if r["results"]]
    paths = {r["retrieval"]["indexed"] for r in answered}
    statuses = {hit["status"] for r in answered for hit in r["results"]}
    hits = [hit for r in answered for hit in r["results"]]

    assert paths == {True, False}, "both answer paths must return something"
    assert statuses == {"approved", "draft"}
    # INV-8 is a disjunction, so a corpus that reaches only its first half
    # cannot tell whether the published schema describes the domain. It did not:
    # `sourceAnchors` required `minItems: 1`, and every anchorless result -- a
    # supported, documented state -- violated the contract on both paths.
    assert {bool(hit["sourceAnchors"]) for hit in hits} == {True, False}, (
        "the corpus must reach both halves of INV-8"
    )
    assert {hit["itemId"] for hit in hits if not hit["sourceAnchors"]} == set(ANCHORLESS_ITEM_IDS)
    assert any("foundBy" in hit for r in answered for hit in r["results"]), (
        "the ranked path's optional fields must be exercised"
    )
    assert {r["retrieval"]["mode"] for r in real_responses.values()} >= {
        "substring",
        "hybrid",
        "none",
    }
    assert {r["retrieval"]["fallbackReason"] for r in real_responses.values()} >= {
        None,
        "no-index",
        "unapproved-not-indexed",
    }


@pytest.mark.integration
def test_count_matches_the_results_actually_returned(
    real_responses: dict[str, dict[str, Any]],
) -> None:
    """Asserted here because JSON Schema cannot say it.

    ``count`` is the one field a client is likely to trust without reading
    ``results``, and a count that came from anywhere else -- what matched, what
    was ranked -- would be a statement about content the caller may not read.
    """
    for name, response in real_responses.items():
        assert response["count"] == len(response["results"]), name


@pytest.mark.integration
def test_the_trust_triple_is_on_real_output_not_only_in_the_schema(
    real_responses: dict[str, dict[str, Any]],
) -> None:
    """SEC-15, FR-R5. ``required`` in a schema is a claim about a document; this
    is the claim about the product.

    The labels are what stop an agent reading a knowledge body as an instruction
    addressed to it, and the ranked path went one milestone without attaching
    them while the substring path did.

    Provenance is asserted per document rather than as "every hit has an
    anchor", which is the assertion this test used to make and which is not what
    the domain guarantees. INV-8 lets a revision declare that it originates in
    Theurian instead, and such a result reaches the wire with an empty array.
    Blanket-asserting the truthy case passed only because the corpus contained
    no such document; relaxing it to "an anchor unless the document said
    otherwise" keeps the FR-R5 claim exact in both directions -- a hit that
    should carry an anchor and does not still fails here.
    """
    for name, response in real_responses.items():
        for hit in response["results"]:
            assert hit["contentClassification"] == "untrusted-knowledge", name
            assert hit["mayContainInstructions"] is True, name
            assert hit["executable"] is False, name
            expected_anchors = hit["itemId"] not in ANCHORLESS_ITEM_IDS
            assert bool(hit["sourceAnchors"]) is expected_anchors, name


@pytest.mark.integration
def test_published_timestamps_really_parse(real_responses: dict[str, dict[str, Any]]) -> None:
    """``format: date-time`` is annotation-only.

    JSON Schema treats ``format`` as documentation unless a format checker is
    installed, and this environment has none -- so the schema's own claim about
    ``revisionCreatedAt`` is checked by nothing. A client that parses it needs
    an offset-aware value, which is asserted here rather than assumed.
    """
    for name, response in real_responses.items():
        for hit in response["results"]:
            parsed = datetime.fromisoformat(hit["freshness"]["revisionCreatedAt"])
            assert parsed.tzinfo is not None, name


@pytest.mark.integration
def test_the_conformance_check_can_fail(real_responses: dict[str, dict[str, Any]]) -> None:
    """Guards every validation above.

    A schema that resolved its ``$ref``s to nothing, or a validator built without
    the registry, would accept all seven responses and prove nothing. Each
    mutation below is rejected by a different part of the contract, and two of
    them are only reachable *through* a cross-file reference.
    """
    validator = _validator(SEARCH_RESPONSE)
    response = real_responses["ranked"]
    validator.validate(response)

    rejected = (
        {**response, "surprise": 1},
        {key: value for key, value in response.items() if key != "retrieval"},
        # Through the `$ref` into the result schema: SEC-15's `const: false`.
        {**response, "results": [{**response["results"][0], "executable": True}]},
        # `sourceAnchors` dropped `minItems: 1` so that a revision authored in
        # Theurian can say so with an empty array. That relaxed how *many*
        # anchors a result may carry and nothing about what one is: an anchor
        # with no route back is still a contract violation, not a shorter one.
        {
            **response,
            "results": [{**response["results"][0], "sourceAnchors": [{"provider": "git"}]}],
        },
        # Through the `$ref` into the metadata schema. Named for the field this
        # round deleted: re-adding a per-query count of withheld matches must
        # fail the contract, not merely fail review.
        {**response, "retrieval": {**response["retrieval"], "withheldSuperseded": 3}},
    )
    for payload in rejected:
        with pytest.raises(ValidationError):
            validator.validate(payload)


# -- project.list ------------------------------------------------------------
#
# The tool an agent calls to find out what this daemon can answer for, and the
# one whose response shape nothing pinned: two required fields were added to it
# and the whole suite stayed green. `project.list` reads the registry file and
# opens no database, so these captures are built from a registry written
# directly rather than from the corpus above.


#: One readable registration, so `projects` is never empty and `count` has
#: something to be wrong about.
READABLE_REGISTRATION: Final[dict[str, str]] = {
    "rootPath": "/somewhere/team-one/api",
    "repositoryUrl": "",
    "defaultBranch": "main",
    "knowledgeDirectory": ".theurian",
    "registeredAt": "2026-08-02T10:00:00+00:00",
}

#: The two shapes a hand edit leaves behind, keyed so that sorting has to
#: reorder them: `load` skips both, `unreadable_ids` reports both.
BROKEN_REGISTRATIONS: Final[dict[str, Any]] = {
    "zeta-no-root-key": {"defaultBranch": "main"},
    "alpha-empty-root": {"rootPath": ""},
}


async def _call_project_list(registry: Any) -> dict[str, Any]:
    from theurian.daemon.runner import build_server

    result = await build_server(registry).call_tool("project.list", {})
    structured = getattr(result, "structuredContent", None)
    if structured is not None:
        payload: dict[str, Any] = structured
        return payload
    content: Any = result.content  # type: ignore[union-attr]
    loaded: dict[str, Any] = json.loads(content[0].text)
    return loaded


def _project_list_against(data_dir: pathlib.Path, entries: dict[str, Any]) -> dict[str, Any]:
    """One real ``project.list`` response over a registry holding ``entries``."""
    from theurian.application.project_service import ProjectRegistry

    registry = ProjectRegistry.default(data_dir)
    registry.path.parent.mkdir(parents=True, exist_ok=True)
    registry.path.write_text(json.dumps(entries), encoding="utf-8")
    return asyncio.run(_call_project_list(registry))


@pytest.fixture
def project_list_responses(tmp_path: pathlib.Path) -> dict[str, dict[str, Any]]:
    """``project.list`` with nothing unreadable, and with two entries that are.

    Both, because the field this schema exists to pin is ``unreadable``, and its
    contract is that it is *always* there. A capture of the clean case alone
    validates against a schema that requires the key and against one that does
    not, so "always present" would be tested by nothing.
    """
    return {
        "clean": _project_list_against(tmp_path / "clean", {"demo": READABLE_REGISTRATION}),
        "unreadable": _project_list_against(
            tmp_path / "unreadable", {"demo": READABLE_REGISTRATION, **BROKEN_REGISTRATIONS}
        ),
    }


@pytest.mark.integration
@pytest.mark.parametrize("name", ["clean", "unreadable"])
def test_a_real_project_list_response_validates_against_its_published_schema(
    project_list_responses: dict[str, dict[str, Any]], name: str
) -> None:
    """The assertion whose absence let two required fields land unnoticed.

    ``additionalProperties: false`` fails a field nobody declared and
    ``required`` fails a declared field nothing emits, so this catches drift in
    either direction -- on the tool an agent calls first, before it can call
    anything else.
    """
    _validator(PROJECT_LIST_RESPONSE).validate(project_list_responses[name])


@pytest.mark.integration
def test_the_project_list_captures_reach_both_states_of_the_unreadable_contract(
    project_list_responses: dict[str, dict[str, Any]],
) -> None:
    """Guards the validation above, which two identical responses would satisfy.

    The values are asserted, not merely their presence: ``unreadable`` is sorted
    rather than in file order, because it reaches a command the user retypes and
    JSON-file order reads differently on two machines holding the same registry.
    ``count`` sizes ``projects`` alone -- an unreadable entry can be queried by
    nothing -- so it stays 1 while the registry holds three ids.
    """
    clean = project_list_responses["clean"]
    unreadable = project_list_responses["unreadable"]

    assert clean["unreadable"] == []
    assert clean["remedy"] is None, "there is nothing to cure, and the key still has to be there"
    assert unreadable["unreadable"] == ["alpha-empty-root", "zeta-no-root-key"]
    assert "theurian project unregister" in unreadable["remedy"]
    assert unreadable["count"] == 1, "count sizes `projects`, not the registry file"
    assert [p["projectId"] for p in unreadable["projects"]] == ["demo"]


@pytest.mark.integration
def test_the_project_list_conformance_check_can_fail(
    project_list_responses: dict[str, dict[str, Any]],
) -> None:
    """Guards both validations above.

    A schema loaded but never applied would accept every response and prove
    nothing. Each rejection below is a different clause: an undeclared field, a
    required key dropped, and -- through ``projects``' item schema -- the empty
    ``rootPath`` that is precisely what makes an entry unreadable, so it must
    never appear as a registration.
    """
    validator = _validator(PROJECT_LIST_RESPONSE)
    response = project_list_responses["unreadable"]
    validator.validate(response)

    rejected = (
        {**response, "surprise": 1},
        {key: value for key, value in response.items() if key != "unreadable"},
        {key: value for key, value in response.items() if key != "remedy"},
        {**response, "projects": [{**response["projects"][0], "rootPath": ""}]},
    )
    for payload in rejected:
        with pytest.raises(ValidationError):
            validator.validate(payload)


# -- knowledge.status --------------------------------------------------------
#
# The tool that reports a project's knowledge state, and the one whose published
# promise is about what it does *not* report: `itemsByStatus` carries the
# surfaceable statuses and nothing else, so a retired item is absent from every
# count rather than present under a different label (SEC-13, T-17). That is
# `additionalProperties: false` in the schema, which is worth exactly as much as
# the corpus it has been run against -- so the captures below include a project
# whose items are *all* retired, where a leak has somewhere to appear.
#
# Its own corpus rather than the one above: this needs statuses the search
# corpus deliberately does not contain, and adding them there would change what
# every search capture is a capture of.


class StatusDocument(NamedTuple):
    """One item in a status corpus, and the status it must end up holding.

    ``deprecate`` is separate from ``status`` because ``deprecated`` is not a
    status a revision may declare: it is the state ``deprecateItem`` leaves an
    item in. Writing all three retired states as revision metadata would leave
    that operation's own path unrepresented, and it is the one a user actually
    runs.
    """

    letter: str
    slug: str
    title: str
    status: str
    deprecate: bool = False


STATUS_OPERATIONS = """  - op: createItem
    itemId: architecture.{slug}
    kind: architecture
    namespace: backend
    owner: platform-team
  - op: upsertRevision
    itemId: architecture.{slug}
    revisionId: 01K1{letter}AAREV01234567890ABCDE
    contentFile: ../knowledge/architecture/{slug}.md
    metadata:
      title: {title}
      contentType: text/markdown
      kind: architecture
      namespace: backend
      status: {status}
      owner: platform-team
      trustLevel: reviewed
      sourceAnchors:
        - provider: git
          sourceUri: git://status/{slug}.md
"""

STATUS_DEPRECATION = """  - op: deprecateItem
    itemId: architecture.{slug}
    reason: replaced by the edge proxy
"""

STATUS_MIGRATION_HEADER = """apiVersion: theurian.dev/v1
id: {migration_id}
createdAt: 2026-08-02T10:00:00+09:00
author: engineer@example.com
operations:
"""

#: Every status a caller may be told about, one item each. The schema declares
#: exactly these three keys, and a corpus reaching two of them would leave the
#: third's constraint validated by nothing -- the failure mode `sourceAnchors`
#: already had here once.
SURFACEABLE_CORPUS = (
    StatusDocument("D", "auth-policy", "Authentication policy", "approved"),
    StatusDocument("E", "caching-draft", "Caching draft", "draft"),
    StatusDocument("F", "proposed-queue", "Proposed queue", "proposed"),
)

#: Every status a caller may *not* be told about, one item each, and nothing
#: else -- so this project's response is the empty breakdown. All three, because
#: they are excluded for one reason and a corpus holding one of them could not
#: tell whether the other two were still excluded.
WITHHELD_CORPUS = (
    StatusDocument("G", "retired-gateway", "Retired gateway", "approved", deprecate=True),
    StatusDocument("H", "superseded-sessions", "Superseded sessions", "superseded"),
    StatusDocument("J", "rejected-store", "Rejected store", "rejected"),
)

#: What each corpus must really be holding, read from the canonical store. The
#: withheld project's empty breakdown is a statement about a project with three
#: items in it, and this is what makes it one rather than a statement about a
#: migration that stopped applying.
EXPECTED_STORED_STATUSES = {
    "surfaceable": {
        "architecture.auth-policy": "approved",
        "architecture.caching-draft": "draft",
        "architecture.proposed-queue": "proposed",
    },
    "withheld-only": {
        "architecture.retired-gateway": "deprecated",
        "architecture.superseded-sessions": "superseded",
        "architecture.rejected-store": "rejected",
    },
}

STATUS_PROJECTS: Final = {
    "surfaceable": ("status-surfaceable", SURFACEABLE_CORPUS, "01K1SSSSSS01234567890ABCDE"),
    "withheld-only": ("status-withheld", WITHHELD_CORPUS, "01K1WWWWWW01234567890ABCDE"),
}


async def _call_status(registry: Any, project_id: str) -> dict[str, Any]:
    """One status report through the same entry point the transport uses."""
    from theurian.daemon.runner import build_server

    result = await build_server(registry).call_tool("knowledge.status", {"projectId": project_id})
    structured = getattr(result, "structuredContent", None)
    if structured is not None:
        payload: dict[str, Any] = structured
        return payload
    content: Any = result.content  # type: ignore[union-attr]
    loaded: dict[str, Any] = json.loads(content[0].text)
    return loaded


def _build_status_project(
    root: pathlib.Path, documents: tuple[StatusDocument, ...], migration_id: str
) -> None:
    """A Git working tree holding ``documents``, applied, in one migration.

    ``theurian init`` and ``project register`` read the working directory and
    take no argument that says where, so the caller has already chdir'd into
    ``root`` -- passed here as well because the file writes below must not
    depend on that.
    """
    for args in (
        ["git", "init", "-q", "-b", "main"],
        ["git", "config", "user.email", "test@example.com"],
        ["git", "config", "user.name", "Test"],
    ):
        subprocess.run(args, cwd=root, check=True, capture_output=True)  # noqa: S603

    _cli("init")
    operations = ""
    for document in documents:
        (root / f".theurian/knowledge/architecture/{document.slug}.md").write_text(
            f"# {document.title}\n\nBody text for {document.slug}.\n", encoding="utf-8"
        )
        operations += STATUS_OPERATIONS.format(
            letter=document.letter,
            slug=document.slug,
            title=document.title,
            status=document.status,
        )
        if document.deprecate:
            operations += STATUS_DEPRECATION.format(slug=document.slug)

    (root / f".theurian/migrations/{migration_id}-corpus.yaml").write_text(
        STATUS_MIGRATION_HEADER.format(migration_id=migration_id) + operations, encoding="utf-8"
    )
    _cli("project", "register")
    _cli("migrate", "apply")


def _stored_statuses(root: pathlib.Path, project_id: str) -> dict[str, str]:
    """Every item in one canonical store, mapped to the status it really holds."""
    from theurian.application.project_service import ProjectPaths, read_active_state
    from theurian.domain.context import RequestContext
    from theurian.domain.identifiers import ProjectId
    from theurian.infrastructure.sqlite.store import SqliteCanonicalStore

    paths = ProjectPaths.of(root)
    active = read_active_state(paths)
    assert active is not None, f"{project_id} has no built canonical state"
    context = RequestContext(project_id=ProjectId(project_id))
    with SqliteCanonicalStore(paths.state / active.database_filename) as store:
        return {item.item_id.value: item.status.value for item in store.list_items(context)}


class StatusCaptures(NamedTuple):
    """Real ``knowledge.status`` responses, beside what their stores hold.

    The second half is not decoration. A response of ``{}`` is the correct
    answer for a project holding three retired items and also the correct answer
    for a project holding nothing, and only one of those is evidence.
    """

    responses: dict[str, dict[str, Any]]
    stored: dict[str, dict[str, str]]


def _capture_status(tmp: pathlib.Path) -> StatusCaptures:
    """Both status projects, built by the real CLI into one registry."""
    from theurian.application.project_service import ProjectRegistry

    data_dir = tmp / "datadir"
    responses: dict[str, dict[str, Any]] = {}
    stored: dict[str, dict[str, str]] = {}

    monkey = pytest.MonkeyPatch()
    monkey.setenv("THEURIAN_DATA_DIR", str(data_dir))
    try:
        for name, (project_id, documents, migration_id) in STATUS_PROJECTS.items():
            root = tmp / project_id
            root.mkdir()
            # `init` and `project register` resolve the project from the working
            # directory, so this is set before the build rather than passed to
            # it. Both projects register into the one `THEURIAN_DATA_DIR` above,
            # which is what lets one daemon answer for both.
            monkey.chdir(root)
            _build_status_project(root, documents, migration_id)
            stored[name] = _stored_statuses(root, project_id)
        registry = ProjectRegistry.default(data_dir)
        for name, (project_id, _, _) in STATUS_PROJECTS.items():
            responses[name] = asyncio.run(_call_status(registry, project_id))
    finally:
        monkey.undo()

    return StatusCaptures(responses=responses, stored=stored)


@pytest.fixture(scope="module")
def status_captures(tmp_path_factory: pytest.TempPathFactory) -> StatusCaptures:
    """Two projects, built once: one all-surfaceable, one all-withheld."""
    return _capture_status(tmp_path_factory.mktemp("status-conformance"))


@pytest.mark.integration
@pytest.mark.parametrize("name", list(STATUS_PROJECTS))
def test_a_real_status_response_validates_against_its_published_schema(
    status_captures: StatusCaptures, name: str
) -> None:
    """The schema #19 asked for, checked against the tool rather than read.

    Both directions at once: ``additionalProperties: false`` fails a field
    nobody declared, and ``required`` fails a declared field nothing emits.
    """
    _validator(STATUS_RESPONSE).validate(status_captures.responses[name])


@pytest.mark.integration
def test_the_status_captures_reach_every_declared_status_key_and_the_empty_one(
    status_captures: StatusCaptures,
) -> None:
    """Guards the validation above, which two copies of one response satisfy.

    The schema declares three keys under ``itemsByStatus`` and forbids a fourth.
    A corpus holding only ``approved`` items would validate against a schema
    that had lost ``proposed`` entirely, and against one that had gained
    ``rejected``; neither is what this file is for.

    The empty breakdown is the other half. It is asserted beside what the store
    actually holds, because ``{}`` from a project with three retired items and
    ``{}`` from an empty directory are the same response and only one of them
    says anything.
    """
    assert status_captures.stored == EXPECTED_STORED_STATUSES

    surfaceable = status_captures.responses["surfaceable"]
    withheld = status_captures.responses["withheld-only"]

    assert surfaceable["itemsByStatus"] == {"approved": 1, "draft": 1, "proposed": 1}
    assert surfaceable["itemCount"] == 3
    assert withheld["itemsByStatus"] == {}, "a retired item may not appear under any label"
    assert withheld["itemCount"] == 0, "the total may not restore what the breakdown withheld"


@pytest.mark.integration
def test_the_status_conformance_check_can_fail(status_captures: StatusCaptures) -> None:
    """Guards both validations above.

    A schema loaded but never applied accepts every response and proves nothing.
    Each rejection below is a different clause, and the middle three are the
    ones the schema exists for: a retired status appearing as a key, the same
    quantity relabelled into a bucket, and a count published for a status
    holding no items -- the shapes that would leak what the breakdown withholds.
    """
    validator = _validator(STATUS_RESPONSE)
    response = status_captures.responses["surfaceable"]
    validator.validate(response)

    rejected = (
        {**response, "surprise": 1},
        {key: value for key, value in response.items() if key != "stateHash"},
        {key: value for key, value in response.items() if key != "itemsByStatus"},
        {**response, "itemsByStatus": {**response["itemsByStatus"], "rejected": 1}},
        {**response, "itemsByStatus": {**response["itemsByStatus"], "other": 2}},
        {**response, "itemsByStatus": {**response["itemsByStatus"], "approved": 0}},
        {**response, "stateHash": response["stateHash"].upper()},
    )
    for payload in rejected:
        with pytest.raises(ValidationError):
            validator.validate(payload)
