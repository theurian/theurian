"""A refusal about an out-of-view item is indistinguishable from one about an
absent item, on both write-intent tools (ADR-0032 decision 6, the M5 error-oracle).

This surface's refusals answer questions about items, and some of those items the
caller may not read -- a ``rejected`` item, an item above the deployment's
sensitivity ceiling (#119, ADR-0025). A refusal that distinguishes *withheld*
from *absent* is a disclosure channel. Decision 6 binds it over the whole
surface: for an item outside the caller's view, every write-intent tool refuses
indistinguishably from an item that does not exist, and such a refusal carries no
current-revision id and no status.

The corpus is synthetic, which is the only way to hold a withheld row in a corpus
whose serve scope excludes it (ADR-0030 decision 6's reasoning, one surface
over): ``rejected-store`` is withheld by ``may_surface`` and
``confidential-item`` is above the default serving ceiling
(``may_disclose``), while ``auth-policy`` is in view. All three really hold a
current revision -- the *withheld-reach control* below asserts it -- so a ``None``
from the caller-scoped lookup is suppression, not genuine absence, and the
oracle it feeds is not passing vacuously over a two-item store.

**The two tools carry the bind by different mechanisms.**
``knowledge.proposeChange``'s ``expectedRevision`` refusal reads the caller-scoped
``CurrentRevisionLookup`` (decision 6's owed seat): ``None`` for an out-of-view
item makes its refusal identical to an absent item's, and the real revision for
an in-view item -- the *in-view control*, without which a lookup returning
``None`` for everything would satisfy the equality while breaking the
optimistic-concurrency remedy. ``knowledge.generateMigrationDraft`` consults no
item at generation (the alias guard is not run there -- decision 3's open
question, answered "no" by the composition), so its response about an
out-of-view item is a successful draft indistinguishable from one about an absent
item, at each of the six item-id-bearing positions -- and would go RED if a
future change ran the alias guard (or any item lookup) at generation, since the
alias-collision refusal is the one that publishes a status.

Refusals are asserted to carry no *status*, not only no revision id: the alias
guard's message is the one that publishes a status today (decision 3), so a bind
written against the revision id alone would pass a build that answered
``(status rejected)``.
"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

import pytest
from git_harness import commit_migrations
from migration_fixtures import body_pin
from typer.testing import CliRunner

from theurian.application.project_service import ProjectPaths, ProjectRegistry, read_active_state
from theurian.cli.main import app
from theurian.daemon.runner import build_server
from theurian.domain.context import RequestContext
from theurian.domain.identifiers import ProjectId
from theurian.infrastructure.sqlite.store import SqliteCanonicalStore

from mcp_wire_session import ToolCall, mcp_session  # isort: skip

pytestmark = pytest.mark.integration

runner = CliRunner()

MIGRATION_ID: Final = "01K1EEEEEE01234567890ABCDE"
BODY: Final = "# Body\n\nContent nobody outside the team may be told exists.\n"

#: The in-view item and its real current revision -- the id the in-view control
#: asserts a refusal *does* carry.
IN_VIEW: Final = "architecture.auth-policy"
IN_VIEW_REVISION: Final = "01K1AAAREV01234567890ABCDE"

#: A well-formed revision id the caller supplies as ``expectedRevision``. Never
#: any item's real one, so a refusal echoing it back proves nothing about the
#: store -- only the item's *own* revision, read through the lookup, is a leak.
CALLER_EXPECTED_REVISION: Final = "01K1BBBREV01234567890ABCDE"

#: An id no operation ever stored: the "absent" arm of every equality.
ABSENT_ITEM: Final = "architecture.never-existed"

#: A second well-formed id used to fill the *other* item-id positions of a
#: two-position operation, held fixed across the out-of-view and absent calls so
#: only the tested position varies.
ANCHOR_ITEM: Final = "architecture.anchor"


@dataclass(frozen=True, slots=True)
class _Withheld:
    """An out-of-view item, its real revision, and the word its state would leak."""

    item_id: str
    revision: str
    #: The attribute a leak would spell -- the status ``rejected`` or the
    #: sensitivity ``confidential`` -- asserted absent from every refusal about it.
    secret_word: str


# The ids deliberately do not embed their own withheld word: the item id is
# caller-supplied and echoing it is not a leak, so ``secret_word`` must be a word
# the *tool* could only produce by reporting the item's state.
REJECTED: Final = _Withheld("architecture.gamma-record", "01K1DDDRST01234567890ABCDE", "rejected")
CONFIDENTIAL: Final = _Withheld(
    "architecture.delta-record", "01K1CCCFDN01234567890ABCDE", "confidential"
)
WITHHELD: Final = (REJECTED, CONFIDENTIAL)


def _revision(item_id: str, revision_id: str, *, status: str, sensitivity: str) -> str:
    return f"""  - op: createItem
    itemId: {item_id}
    kind: architecture
    namespace: backend
    owner: platform-team
  - op: upsertRevision
    itemId: {item_id}
    revisionId: {revision_id}
    contentFile: ../knowledge/architecture/{item_id.split(".", 1)[1]}.md
    contentSha256: {body_pin(BODY)}
    metadata:
      title: {item_id}
      contentType: text/markdown
      kind: architecture
      namespace: backend
      status: {status}
      sensitivity: {sensitivity}
      owner: platform-team
      trustLevel: reviewed
      sourceAnchors:
        - provider: git
          sourceUri: git://demo/{item_id}.md
"""


MIGRATION: Final = (
    f"apiVersion: theurian.dev/v1\nid: {MIGRATION_ID}\n"
    "createdAt: 2026-08-02T16:00:00+09:00\nauthor: engineer@example.com\noperations:\n"
    + _revision(IN_VIEW, IN_VIEW_REVISION, status="approved", sensitivity="internal")
    + _revision(REJECTED.item_id, REJECTED.revision, status="rejected", sensitivity="internal")
    + _revision(
        CONFIDENTIAL.item_id, CONFIDENTIAL.revision, status="approved", sensitivity="confidential"
    )
)

EVIDENCE: Final = {
    "agentId": "claude-code",
    "taskId": "task-7",
    "model": "claude-opus-5",
    "reasoning": "Recorded for the write-intent disclosure oracle.",
}


def _run(*args: str) -> None:
    if args[:2] == ("migrate", "apply"):
        commit_migrations()
    result = runner.invoke(app, [*args, "--json"], catch_exceptions=False)
    assert result.exit_code == 0, result.stdout + (result.stderr or "")


@pytest.fixture
def corpus(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[ProjectRegistry]:
    """A demo project holding one in-view item and two withheld ones.

    Built by the real CLI so the canonical store, the serving grant and the
    resolution the write-intent tools inherit are exactly the shipped ones.
    """
    root = tmp_path / "demo"
    root.mkdir()
    for args in (
        ["git", "init", "-q", "-b", "main"],
        ["git", "config", "user.email", "test@example.com"],
        ["git", "config", "user.name", "Test"],
    ):
        subprocess.run(args, cwd=root, check=True, capture_output=True)  # noqa: S603

    data_dir = tmp_path / "datadir"
    monkeypatch.setenv("THEURIAN_DATA_DIR", str(data_dir))
    monkeypatch.chdir(root)

    _run("init")
    knowledge = root / ".theurian/knowledge/architecture"
    for item in (IN_VIEW, REJECTED.item_id, CONFIDENTIAL.item_id):
        (knowledge / f"{item.split('.', 1)[1]}.md").write_text(BODY)
    (root / f".theurian/migrations/{MIGRATION_ID}-corpus.yaml").write_text(MIGRATION)
    _run("project", "register")
    _run("migrate", "apply")

    yield ProjectRegistry.default(data_dir)


def _stored(registry: ProjectRegistry) -> dict[str, tuple[str, str, str | None]]:
    """Every item mapped to ``(status, sensitivity, current_revision_id)``."""
    paths = ProjectPaths.of(Path(registry.load()["demo"]["rootPath"]))
    active = read_active_state(paths)
    assert active is not None, "the fixture must have built a canonical state"
    context = RequestContext(project_id=ProjectId("demo"))
    with SqliteCanonicalStore(paths.state / active.database_filename) as store:
        return {
            item.item_id.value: (
                item.status.value,
                item.sensitivity.value,
                item.current_revision_id.value if item.current_revision_id else None,
            )
            for item in store.list_items(context)
        }


def _propose_change_args(item_id: str) -> dict[str, Any]:
    """A well-formed ``proposeChange`` input, carrying the label INV-8 needs so
    construction reaches ``_check_expected_revision`` rather than refusing first.
    """
    return {
        "projectId": "demo",
        "itemId": item_id,
        "title": "Retry policy",
        "kind": "architecture",
        "owner": "platform-team",
        "author": "platform-team@example.com",
        "description": "Record the retry budget.",
        "body": "# Retry policy\n\nThree attempts.\n",
        "contentType": "text/markdown",
        "evidence": EVIDENCE,
        "labels": ["authored-in-theurian"],
        "expectedRevision": CALLER_EXPECTED_REVISION,
    }


# -- The withheld-reach control: the oracle is not vacuous -------------------


def test_the_out_of_view_items_really_hold_a_revision_the_lookup_suppresses(
    corpus: ProjectRegistry,
) -> None:
    """Guards the oracle. Without this, a corpus whose withheld items never built
    would make every equality below pass over a store that has nothing to hide
    (ADR-0032 decision 6's *control that the battery reaches the withheld items*).

    Each withheld item is present in the canonical store with a real current
    revision and the status/sensitivity that puts it out of view -- so the
    caller-scoped lookup's ``None`` for it is suppression, not absence.
    """
    stored = _stored(corpus)

    assert stored[IN_VIEW] == ("approved", "internal", IN_VIEW_REVISION)
    assert stored[REJECTED.item_id] == ("rejected", "internal", REJECTED.revision)
    assert stored[CONFIDENTIAL.item_id] == ("approved", "confidential", CONFIDENTIAL.revision)


# -- proposeChange: the expectedRevision oracle (decisions 6, and 3's control) --


@pytest.mark.parametrize("withheld", WITHHELD, ids=[w.item_id for w in WITHHELD])
def test_proposechange_refuses_an_out_of_view_item_like_an_absent_one(
    corpus: ProjectRegistry, tmp_path: Path, withheld: _Withheld
) -> None:
    """The proposeChange arm: an ``expectedRevision`` about an out-of-view item is
    refused identically to one about an absent item (ADR-0032 decision 6).

    The caller-scoped lookup answers ``None`` for a withheld item exactly as for a
    missing one, so ``_check_expected_revision`` raises the same "does not exist
    yet" refusal. Compared after normalising the caller-supplied item id -- the one
    substring a caller already knows -- so what is asserted equal is everything
    else. And the refusal carries neither the item's real revision id nor its
    status/sensitivity word: those are what a lookup that distinguished withheld
    from absent would leak.
    """
    with mcp_session(build_server(corpus), tmp_path / "data") as call:
        withheld_answer = call("knowledge.proposeChange", _propose_change_args(withheld.item_id))
        absent_answer = call("knowledge.proposeChange", _propose_change_args(ABSENT_ITEM))

    withheld_text = withheld_answer["result"]["content"][0]["text"]
    absent_text = absent_answer["result"]["content"][0]["text"]

    assert withheld_answer["result"]["isError"] is True, withheld_answer
    assert absent_answer["result"]["isError"] is True, absent_answer
    assert withheld_text.replace(withheld.item_id, "<ITEM>") == absent_text.replace(
        ABSENT_ITEM, "<ITEM>"
    ), (withheld_text, absent_text)

    whole = json.dumps(withheld_answer)
    assert withheld.revision not in whole, f"the refusal leaked the item's revision: {whole}"
    assert withheld.secret_word not in whole, f"the refusal leaked the item's state: {whole}"


def test_proposechange_carries_the_revision_for_an_in_view_item(
    corpus: ProjectRegistry, tmp_path: Path
) -> None:
    """The in-view control (ADR-0032 decision 6): the caller-scoped lookup returns
    the *real* revision for an item the caller may see.

    Without it, a lookup that returned ``None`` for everything would satisfy the
    out-of-view equality above while silently breaking the optimistic-concurrency
    remedy in view -- a caller told only "that is stale" cannot find the value to
    retry with. So a stale ``expectedRevision`` about the in-view item must be
    refused *with* the item's current revision named.
    """
    with mcp_session(build_server(corpus), tmp_path / "data") as call:
        answer = call("knowledge.proposeChange", _propose_change_args(IN_VIEW))

    result = answer["result"]
    assert result["isError"] is True, answer
    text = result["content"][0]["text"]
    assert IN_VIEW_REVISION in text, (
        f"the in-view refusal did not name the item's current revision, so the lookup "
        f"may be returning None for everything: {text}"
    )
    assert CALLER_EXPECTED_REVISION in text, text


# -- generateMigrationDraft: no item lookup, so no oracle, at each position --


#: One admitted operation per item-id-bearing schema position (the six ADR-0032
#: decision 6 derives), placing the tested id at exactly that position and holding
#: the other positions fixed. The ``alias`` case is the T-21 shape: an alias key
#: equal to a live item id is what the alias guard's status oracle would fire on,
#: so it is the position a guard-at-generation regression would leak through.
def _op_at(position: str, item_id: str) -> dict[str, Any]:
    ops: dict[str, dict[str, Any]] = {
        "itemId": {"op": "deprecateItem", "itemId": item_id},
        "alias": {"op": "addAlias", "alias": item_id, "itemId": ANCHOR_ITEM},
        "sourceItemId": {
            "op": "addRelation",
            "sourceItemId": item_id,
            "targetItemId": ANCHOR_ITEM,
            "relationType": "related_to",
        },
        "targetItemId": {
            "op": "addRelation",
            "sourceItemId": ANCHOR_ITEM,
            "targetItemId": item_id,
            "relationType": "related_to",
        },
        "specId": {
            "op": "registerSpecification",
            "itemId": ANCHOR_ITEM,
            "specId": item_id,
            "sourceUri": "git://demo/spec.md",
            "format": "text/markdown",
        },
        "supersededBy": {"op": "deprecateItem", "itemId": ANCHOR_ITEM, "supersededBy": item_id},
    }
    return ops[position]


ITEM_ID_POSITIONS: Final = (
    "itemId",
    "alias",
    "sourceItemId",
    "targetItemId",
    "specId",
    "supersededBy",
)


def _draft(call: ToolCall, operation: dict[str, Any]) -> dict[str, Any]:
    return call(
        "knowledge.generateMigrationDraft",
        {
            "projectId": "demo",
            "document": {"author": "platform-team@example.com", "operations": [operation]},
            "evidence": EVIDENCE,
        },
    )


def test_the_six_positions_are_the_schema_derived_item_id_positions() -> None:
    """The battery's positions are exactly the six the ADR derives from the
    migration schema -- every property of an admitted operation whose ``$ref``
    resolves to ``itemId`` -- not a subset picked by hand (ADR-0032 decision 6).

    Derived here from the schema, so a fifteenth operation with a seventh position
    reddens this rather than silently escaping the battery.
    """
    schema = json.loads(
        (
            Path(__file__).resolve().parents[4] / "schemas/migrations/migration.schema.json"
        ).read_text()
    )
    pulled = {
        "operation",
        "opCreateItem",
        "opUpsertRevision",
        "opChangeSensitivity",
        "opRestoreItem",
    }
    positions: set[str] = set()
    for name, body in schema["$defs"].items():
        if not name.startswith("op") or name in pulled:
            continue
        positions |= {
            prop
            for prop, spec in body.get("properties", {}).items()
            if spec.get("$ref", "").endswith("itemId")
        }

    assert positions == set(ITEM_ID_POSITIONS), positions


@pytest.mark.parametrize("position", ITEM_ID_POSITIONS)
def test_generate_migration_draft_answers_an_out_of_view_item_like_an_absent_one(
    corpus: ProjectRegistry, tmp_path: Path, position: str
) -> None:
    """The generateMigrationDraft arm, at each item-id position (ADR-0032 decision 6).

    The tool consults no item at generation, so a document naming an out-of-view
    item at any position drafts a proposal indistinguishable in outcome from one
    naming an absent item -- both succeed. This asserts both, and asserts the
    out-of-view draft's response leaks neither the item's real revision id nor its
    status word.

    The teeth are against a regression that adds an item lookup at generation --
    running the alias guard, most of all: at the ``alias`` position that is the
    T-21 collision, and a guard-at-generation would answer ``(status rejected)``
    while the absent id drafted cleanly, so this would go RED with a divergent
    outcome and a leaked status.
    """
    with mcp_session(build_server(corpus), tmp_path / "data") as call:
        withheld_answer = _draft(call, _op_at(position, REJECTED.item_id))
        absent_answer = _draft(call, _op_at(position, ABSENT_ITEM))

    assert withheld_answer["result"]["isError"] is False, withheld_answer
    assert absent_answer["result"]["isError"] is False, absent_answer

    whole = json.dumps(withheld_answer)
    assert REJECTED.revision not in whole, f"the draft leaked the item's revision: {whole}"
    assert REJECTED.secret_word not in whole, f"the draft leaked the item's status: {whole}"
