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
