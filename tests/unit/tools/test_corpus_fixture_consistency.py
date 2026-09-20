"""The frozen fixture corpus under ``tests/fixtures/eval/`` holds its own rules.

Nothing else checks it. The three contract schemas state the shape of one file
each and cannot reach across two (ADR-0036 decision 6), the S2 loader that would
is not written, and ``schemas/migrations/migration.schema.json`` is applied by
``theurian migrate`` against a project's own migrations, never against a fixture
directory. So until S2 lands, an edited body whose ``contentSha256`` was not
re-pinned, a judgement naming an item no migration creates, or a withheld item
promoted into a ``relevant`` list would each be committed green.

Every rule below is a function returning the violations it found, and every one
is asserted twice: once over the corpus as committed, and once over a deep copy
carrying exactly the defect the rule exists to catch. A rule asserted only over
a corpus that satisfies it is a rule that would also pass if it checked nothing.
The one rule whose subject is a *pair* -- an ADR snapshot and the ``docs/adr/``
file it was taken from -- is perturbed on the live half, because the frozen half
is what the pin exists to keep unedited.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
import subprocess
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import pytest
import yaml
from jsonschema import Draft202012Validator

from theurian.application.authorization import ServingProfile
from theurian.domain.enums import KnowledgeStatus, Sensitivity, may_disclose, may_surface

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[3]
CORPUS_ROOT = REPO_ROOT / "tests" / "fixtures" / "eval"
MIGRATIONS_DIR = CORPUS_ROOT / "migrations"
EVAL_SCHEMAS = REPO_ROOT / "tools" / "eval" / "schemas"
MIGRATION_SCHEMA = REPO_ROOT / "schemas" / "migrations" / "migration.schema.json"

#: Item counts, statuses and sensitivities follow from the migration operations;
#: ``chunks`` does not, and is measured by a real build instead.
DERIVABLE_CENSUS_KEYS = ("items", "byStatus", "bySensitivity")

#: Every key through which an operation names a knowledge item.
ITEM_REFERENCES = ("itemId", "sourceItemId", "targetItemId", "supersededBy", "alias", "specId")

#: The operations ``_replay`` folds, and the operations it may skip because
#: ``MigrationEngine._apply_operation`` leaves both status and sensitivity alone
#: for them. ``restoreItem`` is in neither set on purpose: the engine sets
#: ``approved`` for it (``migration_alias_guards._final_item_statuses`` says so
#: in as many words), so a fold that skipped it would report the item's
#: pre-restore status. Between them the two sets classify every operation the
#: migration schema admits except that one, which is pinned rather than said.
REPLAYED_OPERATIONS = frozenset(
    {"createItem", "upsertRevision", "deprecateItem", "changeSensitivity"}
)
STATE_PRESERVING_OPERATIONS = frozenset(
    {
        "addAlias",
        "addEvidence",
        "addRelation",
        "changeOwner",
        "registerSpecification",
        "removeAlias",
        "removeEvidence",
        "removeRelation",
        "supersedeSpecification",
    }
)

#: The tokens ``queries.yaml`` says it checked absent from every body and every
#: title before building its two absent-topic probes on them -- that comment's
#: own list, mirrored. ``kubernetes`` is deliberately not among them: ADR-0023
#: uses the word as a tokenizer example, which is why the Kubernetes probe names
#: the topic's other terms instead.
ABSENT_TOPIC_TOKENS = frozenset(
    {
        "autoscaling",
        "chargeback",
        "ingress",
        "kubelet",
        "merchant",
        "payment",
        "pod",
        "refund",
        "settlement",
    }
)

#: The probes whose correct answer is abstention *because the topic is absent*,
#: as opposed to the withheld-topic probes, whose subject the ``full`` corpus
#: holds and gates.
ABSENT_TOPIC_QUERIES = frozenset({"q-absent-kubernetes", "q-absent-payment-flow"})

#: The docs link walk's own link pattern (``.github/workflows/shared.yml``).
MARKDOWN_LINK = re.compile(r"\[[^\]]*\]\(([^)]+)\)")

#: How many verbatim ADR snapshots the corpus carries, which is also the count
#: the walk's corpus-exclusion comment states ("12 distinct paths, all tracked").
ADR_SNAPSHOT_COUNT = 12

#: What a default response may hold, and what a ``--include-unapproved`` build
#: writes. Folded out of the shipped gates rather than spelled out: a status
#: moving into or out of ``SURFACEABLE_STATUSES``, or a move of
#: ``DEFAULT_CEILING``, must move this file's derivation with it, or the split
#: below would go on describing the gate the corpus was authored against.
DEFAULT_STATUSES = frozenset(
    status.value for status in KnowledgeStatus if may_surface(status, include_unapproved=False)
)
INDEXED_STATUSES = frozenset(
    status.value for status in KnowledgeStatus if may_surface(status, include_unapproved=True)
)
SERVED_SENSITIVITIES = frozenset(
    level.value
    for level in Sensitivity
    if may_disclose(level, visible=ServingProfile().visible_sensitivities)
)

#: Which withheld members the response-equality battery tests, and which the
#: manifest census tests instead (ADR-0036 decision 6). Derived at the rule from
#: each member's final status and sensitivity; pinned here so a member changing
#: side cannot do so silently. The manifest declares no coverage key --
#: ``migrationEntry`` is closed at ``{file, plane}`` -- by the same decision.
GATE_TESTED_ITEMS = frozenset(
    {"security.draft-scan-hardening", "architecture.proposed-storage-redesign"}
)
CENSUS_TESTED_ITEMS = frozenset(
    {
        "domain.rejected-credential-cache",
        "domain.restricted-retention-exceptions",
        "operations.superseded-daemon-procedure",
        "security.confidential-token-rotation",
        "testing.deprecated-flaky-quarantine",
    }
)

#: Every withheld member's state once its migrations are folded, and the whole
#: reason each one lands where it does: two gated at query time, three retired
#: by status, two above the serving ceiling.
WITHHELD_FINAL_STATES = {
    "architecture.proposed-storage-redesign": ("proposed", "internal"),
    "domain.rejected-credential-cache": ("rejected", "internal"),
    "domain.restricted-retention-exceptions": ("approved", "restricted"),
    "operations.superseded-daemon-procedure": ("superseded", "internal"),
    "security.confidential-token-rotation": ("approved", "confidential"),
    "security.draft-scan-hardening": ("draft", "internal"),
    "testing.deprecated-flaky-quarantine": ("deprecated", "internal"),
}


@dataclass(frozen=True)
class Corpus:
    manifest: dict[str, Any]
    queries: dict[str, Any]
    judgements: dict[str, Any]
    #: ``(filename, document)`` in filename order.
    migrations: tuple[tuple[str, dict[str, Any]], ...]
    #: Body bytes keyed by the ``contentFile`` path a migration names.
    bodies: dict[str, bytes]


def _yaml(path: Path) -> dict[str, Any]:
    loaded: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8"))
    return loaded


def _schema(path: Path) -> Draft202012Validator:
    return Draft202012Validator(json.loads(path.read_text(encoding="utf-8")))


def _load() -> Corpus:
    migrations = tuple((path.name, _yaml(path)) for path in sorted(MIGRATIONS_DIR.glob("*.yaml")))
    bodies = {
        operation["contentFile"]: (MIGRATIONS_DIR / operation["contentFile"]).read_bytes()
        for _name, document in migrations
        for operation in document["operations"]
        if operation["op"] == "upsertRevision"
    }
    return Corpus(
        manifest=_yaml(CORPUS_ROOT / "manifest.yaml"),
        queries=_yaml(CORPUS_ROOT / "queries.yaml"),
        judgements=_yaml(CORPUS_ROOT / "judgements.yaml"),
        migrations=migrations,
        bodies=bodies,
    )


CORPUS = _load()

MANIFEST_VALIDATOR = _schema(EVAL_SCHEMAS / "manifest.schema.json")
QUERIES_VALIDATOR = _schema(EVAL_SCHEMAS / "queries.schema.json")
JUDGEMENTS_VALIDATOR = _schema(EVAL_SCHEMAS / "judgements.schema.json")
MIGRATION_VALIDATOR = _schema(MIGRATION_SCHEMA)

#: Every ``op`` the migration schema admits, read off its own discriminated
#: union rather than listed here.
SCHEMA_OPERATIONS = frozenset(
    definition["properties"]["op"]["const"]
    for definition in json.loads(MIGRATION_SCHEMA.read_text(encoding="utf-8"))["$defs"].values()
    if "const" in definition.get("properties", {}).get("op", {})
)


# -- derivation helpers -------------------------------------------------------


def _ids(entry: dict[str, Any], field: str) -> set[str]:
    return {item["itemId"] for item in entry.get(field, [])}


def _planes(corpus: Corpus) -> dict[str, str]:
    return {entry["file"]: entry["plane"] for entry in corpus.manifest["migrations"]}


def _creating_plane(corpus: Corpus) -> dict[str, str]:
    """Each created itemId, mapped to the plane of the migration that created it.

    A migration the manifest does not declare reads as ``withheld`` here. That
    default is never reached by a corpus this module passes --
    ``_manifest_order_violations`` reports declared-against-present as a
    violation of its own -- and it leans that way rather than raising so one
    undeclared file cannot stop every other rule from reporting.
    """
    planes = _planes(corpus)
    return {
        operation["itemId"]: planes.get(name, "withheld")
        for name, document in corpus.migrations
        for operation in document["operations"]
        if operation["op"] == "createItem"
    }


def _tally(values: list[str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for value in values:
        counts[value] = counts.get(value, 0) + 1
    return counts


def _replay(corpus: Corpus, wanted: set[str]) -> dict[str, dict[str, str]]:
    """Replay the wanted planes' migrations into each item's final state.

    Four operations are folded, the way the engine applies them: a revision's
    metadata adopts status and sensitivity onto the item
    (``KnowledgeItem.with_revision``), while ``deprecateItem`` and
    ``changeSensitivity`` move one of them afterwards. Every other operation is
    skipped, which is sound only for the nine the engine's own dispatch leaves
    item state alone for. ``_unmodelled_operation_violations`` is what holds the
    corpus inside that boundary; ``restoreItem`` is what it exists to catch.
    """
    planes = _planes(corpus)
    state: dict[str, dict[str, str]] = {}
    for name, document in corpus.migrations:
        if planes.get(name) not in wanted:
            continue
        for operation in document["operations"]:
            item = operation.get("itemId")
            match operation["op"]:
                case "createItem":
                    state[item] = {"status": "", "sensitivity": operation["sensitivity"]}
                case "upsertRevision":
                    state[item] = {
                        "status": operation["metadata"]["status"],
                        "sensitivity": operation["metadata"]["sensitivity"],
                    }
                case "deprecateItem" if item in state:
                    state[item]["status"] = "deprecated"
                case "changeSensitivity" if item in state:
                    state[item]["sensitivity"] = operation["sensitivity"]
                case _:
                    pass
    return state


def _derive_census(corpus: Corpus, wanted: set[str]) -> dict[str, Any]:
    state = _replay(corpus, wanted)
    return {
        "items": len(state),
        "byStatus": _tally([item["status"] for item in state.values()]),
        "bySensitivity": _tally([item["sensitivity"] for item in state.values()]),
    }


def _withheld_final_states(corpus: Corpus) -> dict[str, tuple[str, str]]:
    """Each withheld-plane item's ``(status, sensitivity)`` after every migration.

    Folded over both planes, not the withheld one alone: a later visible
    migration moving a withheld item's state would be missed by a withheld-only
    replay. ``_plane_dependency_violations`` forbids that direction today, so the
    two agree on this corpus -- this does not depend on that rule holding.
    """
    creators = _creating_plane(corpus)
    return {
        item: (fields["status"], fields["sensitivity"])
        for item, fields in _replay(corpus, {"visible", "withheld"}).items()
        if creators.get(item) == "withheld"
    }


def _anchored_revisions(corpus: Corpus) -> list[tuple[str, dict[str, Any]]]:
    """``(migration file, operation)`` for every revision carrying a source anchor."""
    return [
        (name, operation)
        for name, document in corpus.migrations
        for operation in document["operations"]
        if operation["op"] == "upsertRevision" and operation["metadata"].get("sourceAnchors")
    ]


def _anchor_paths(corpus: Corpus) -> list[str]:
    return [
        anchor["filePath"]
        for _name, operation in _anchored_revisions(corpus)
        for anchor in operation["metadata"]["sourceAnchors"]
    ]


def _relative_links(text: str) -> set[str]:
    """Every link target the docs walk would resolve against the file's own directory.

    The walk's three steps, in its order (``.github/workflows/shared.yml``): its
    link pattern, then ``split(" ")[0]`` to drop a title, then "relative" as the
    walk decides it -- no URL scheme and not a bare fragment. The ``#fragment``
    is stripped last, because the walk resolves ``target.split("#")[0]``.
    """
    targets = (target.split(" ")[0] for target in MARKDOWN_LINK.findall(text))
    return {
        target.split("#")[0]
        for target in targets
        if not urlparse(target).scheme and not target.startswith("#")
    }


def _tracked_sources(paths: Iterable[str]) -> dict[str, str]:
    """Each of ``paths`` this repository tracks, mapped to the text the walk reads.

    Two questions from two sources, deliberately. Membership is ``git ls-files
    --cached``, which is the population a fresh CI checkout materialises and the
    one the walk's exclusion comment counts. The text is the working tree's,
    because ``root.rglob("*.md")`` is what the walk actually reads: an ADR edited
    and not yet committed is already the file it would check.
    """
    wanted = sorted(set(paths))
    argv = ["git", "-c", f"safe.directory={REPO_ROOT}", "ls-files", "--cached", "-z", "--", *wanted]
    try:
        listing = subprocess.run(  # noqa: S603 - argv is written here, never caller input
            argv, cwd=REPO_ROOT, capture_output=True, check=False, timeout=30
        )
    except OSError as error:
        raise AssertionError(f"`git ls-files` could not run in {REPO_ROOT}: {error}") from error
    assert listing.returncode == 0, (
        f"`git ls-files` failed in {REPO_ROOT}, so the rule below would be asserting against "
        f"a population nobody built:\n{listing.stderr.decode('utf-8', 'replace')}"
    )
    tracked = {entry for entry in listing.stdout.decode("utf-8").split("\0") if entry}
    return {
        path: (REPO_ROOT / path).read_text(encoding="utf-8")
        for path in wanted
        if path in tracked and (REPO_ROOT / path).is_file()
    }


# -- the rules ----------------------------------------------------------------


def _schema_violations(corpus: Corpus) -> list[str]:
    found = [
        f"{name}: {error.json_path}"
        for name, document, validator in (
            ("manifest.yaml", corpus.manifest, MANIFEST_VALIDATOR),
            ("queries.yaml", corpus.queries, QUERIES_VALIDATOR),
            ("judgements.yaml", corpus.judgements, JUDGEMENTS_VALIDATOR),
        )
        for error in validator.iter_errors(document)
    ]
    found += [
        f"{name}: {error.json_path}"
        for name, document in corpus.migrations
        for error in MIGRATION_VALIDATOR.iter_errors(document)
    ]
    return found


def _content_hash_violations(corpus: Corpus) -> list[str]:
    return [
        f"{name}: {operation['contentFile']}"
        for name, document in corpus.migrations
        for operation in document["operations"]
        if operation["op"] == "upsertRevision"
        and hashlib.sha256(corpus.bodies[operation["contentFile"]]).hexdigest()
        != operation["contentSha256"]
    ]


def _plane_key_violations(corpus: Corpus) -> list[str]:
    return [name for name, document in corpus.migrations if "plane" in document]


def _unmodelled_operation_violations(corpus: Corpus) -> list[str]:
    return [
        f"{name}: {operation['op']} is neither replayed nor state-preserving"
        for name, document in corpus.migrations
        for operation in document["operations"]
        if operation["op"] not in REPLAYED_OPERATIONS | STATE_PRESERVING_OPERATIONS
    ]


def _body_file_violations(corpus: Corpus) -> list[str]:
    claimed: dict[Path, list[str]] = {}
    for name, document in corpus.migrations:
        for operation in document["operations"]:
            if operation["op"] != "upsertRevision":
                continue
            body = (MIGRATIONS_DIR / operation["contentFile"]).resolve()
            claimed.setdefault(body, []).append(name)
    present = {path.resolve() for path in (CORPUS_ROOT / "knowledge").rglob("*.md")}
    return (
        [
            f"{path}: named by {sorted(namers)}"
            for path, namers in sorted(claimed.items())
            if len(namers) > 1
        ]
        + [
            f"{path}: a contentFile names it, nothing committed it"
            for path in sorted(claimed.keys() - present)
        ]
        + [
            f"{path}: committed under knowledge/, no contentFile names it"
            for path in sorted(present - claimed.keys())
        ]
    )


def _absent_topic_violations(corpus: Corpus) -> list[str]:
    titled = [
        (f"{name} description", document.get("description", ""))
        for name, document in corpus.migrations
    ] + [
        (f"{name} title of {operation['itemId']}", operation["metadata"]["title"])
        for name, document in corpus.migrations
        for operation in document["operations"]
        if operation["op"] == "upsertRevision"
    ]
    return (
        [
            f"{path}: carries the absent-topic token {token!r}"
            for path, body in sorted(corpus.bodies.items())
            for token in sorted(ABSENT_TOPIC_TOKENS)
            if token in body.decode("utf-8").casefold()
        ]
        + [
            f"{where}: carries the absent-topic token {token!r}"
            for where, text in titled
            for token in sorted(ABSENT_TOPIC_TOKENS)
            if token in text.casefold()
        ]
        + [
            f"{query['id']}: asks for {token!r}, which no rule here pins absent"
            for query in corpus.queries["queries"]
            if query["id"] in ABSENT_TOPIC_QUERIES
            for token in sorted(set(query["query"].casefold().split()) - ABSENT_TOPIC_TOKENS)
        ]
    )


def _abstention_class_violations(corpus: Corpus) -> list[str]:
    classes = {query["id"]: query["class"] for query in corpus.queries["queries"]}
    enabled = {query["id"] for query in corpus.queries["queries"] if query.get("enabled", True)}
    found = []
    for entry in corpus.judgements["judgements"]:
        name = entry["queryId"]
        if name not in classes:
            continue
        expects = bool(entry.get("expectAbstention"))
        if classes[name] == "unknown" and name in enabled and not expects:
            found.append(f"{name}: class unknown, judged without expectAbstention")
        if expects and classes[name] != "unknown":
            found.append(f"{name}: expectAbstention on a {classes[name]} query")
    return found


def _snapshot_link_violations(corpus: Corpus, live: Mapping[str, str]) -> list[str]:
    found = []
    for name, operation in _anchored_revisions(corpus):
        body = corpus.bodies[operation["contentFile"]].decode("utf-8")
        for anchor in operation["metadata"]["sourceAnchors"]:
            path = anchor["filePath"]
            source = live.get(path)
            if source is None:
                found.append(f"{name}: {path} is not a file this repository tracks")
                continue
            found += [
                f"{name}: {path} no longer links to {target}"
                for target in sorted(_relative_links(body) - _relative_links(source))
            ]
    return found


def _manifest_order_violations(corpus: Corpus) -> list[str]:
    declared = [entry["file"] for entry in corpus.manifest["migrations"]]
    present = [name for name, _document in corpus.migrations]
    found = []
    if declared != sorted(declared):
        found.append("manifest order disagrees with ULID order")
    if declared != present:
        found.append("manifest names a different set of files than the directory holds")
    found += [
        f"{name}: filename prefix is not the migration id"
        for name, document in corpus.migrations
        if not name.startswith(f"{document['id']}-")
    ]
    return found


def _plane_dependency_violations(corpus: Corpus) -> list[str]:
    """A ``visible`` migration must not name an item only a ``withheld`` one creates.

    The reverse is allowed and used once: the withheld draft carries a
    ``related_to`` edge onto a visible ADR. That direction is the affordance the
    corpus exists to exercise; this one would make the ``clean`` build fail.
    """
    planes = _planes(corpus)
    creators = _creating_plane(corpus)
    return [
        f"{name}: references withheld item {value}"
        for name, document in corpus.migrations
        if planes.get(name) == "visible"
        for operation in document["operations"]
        for key in ITEM_REFERENCES
        if (value := operation.get(key)) is not None and creators.get(value) == "withheld"
    ]


def _query_id_violations(corpus: Corpus) -> list[str]:
    ids = [query["id"] for query in corpus.queries["queries"]]
    return sorted({name for name in ids if ids.count(name) > 1})


def _corpora_invariant_violations(corpus: Corpus) -> list[str]:
    return [
        query["id"]
        for query in corpus.queries["queries"]
        if query.get("enabled", True) and query.get("corpora") != ["full", "clean"]
    ]


def _judgement_coverage_violations(corpus: Corpus) -> list[str]:
    judged = [entry["queryId"] for entry in corpus.judgements["judgements"]]
    declared = {query["id"] for query in corpus.queries["queries"]}
    enabled = {query["id"] for query in corpus.queries["queries"] if query.get("enabled", True)}
    duplicated = {name for name in judged if judged.count(name) > 1}
    return (
        [f"unjudged enabled query: {name}" for name in sorted(enabled - set(judged))]
        + [f"judgement names no query: {name}" for name in sorted(set(judged) - declared)]
        + [f"query judged twice: {name}" for name in sorted(duplicated)]
    )


def _disjointness_violations(corpus: Corpus) -> list[str]:
    return [
        f"{entry['queryId']}: {sorted(overlap)}"
        for entry in corpus.judgements["judgements"]
        if (overlap := _ids(entry, "relevant") & _ids(entry, "forbidden"))
    ]


def _empty_judgement_violations(corpus: Corpus) -> list[str]:
    return [
        entry["queryId"]
        for entry in corpus.judgements["judgements"]
        if not (
            entry.get("relevant")
            or entry.get("forbidden")
            or entry.get("evidence")
            or entry.get("expectAbstention")
        )
    ]


def _evidence_subsumption_violations(corpus: Corpus) -> list[str]:
    found = []
    for entry in corpus.judgements["judgements"]:
        pairs = [(ref["sourceUri"], ref.get("filePath")) for ref in entry.get("evidence", [])]
        found += [
            f"{entry['queryId']}: duplicate {pair}"
            for pair in sorted({p for p in pairs if pairs.count(p) > 1})
        ]
        bare = {uri for uri, path in pairs if path is None}
        narrowed = {uri for uri, path in pairs if path is not None}
        found += [f"{entry['queryId']}: subsumed {uri}" for uri in sorted(bare & narrowed)]
    return found


def _judged_item_violations(corpus: Corpus) -> list[str]:
    creators = _creating_plane(corpus)
    found = []
    for entry in corpus.judgements["judgements"]:
        judged = _ids(entry, "relevant") | _ids(entry, "forbidden")
        found += [
            f"{entry['queryId']}: no migration creates {name}"
            for name in sorted(judged - set(creators))
        ]
        found += [
            f"{entry['queryId']}: relevant item {name} is not visible-plane"
            for name in sorted(_ids(entry, "relevant"))
            if creators.get(name, "withheld") != "visible"
        ]
    return found


def _census_violations(corpus: Corpus) -> list[str]:
    found = []
    for plane, wanted in (("full", {"visible", "withheld"}), ("clean", {"visible"})):
        derived = _derive_census(corpus, wanted)
        declared = corpus.manifest["census"][plane]
        found += [
            f"{plane}.{key}: declared {declared.get(key)!r}, derived {derived[key]!r}"
            for key in DERIVABLE_CENSUS_KEYS
            if declared.get(key) != derived[key]
        ]
    return found


def _coverage_split_violations(corpus: Corpus) -> list[str]:
    """Derive which instrument tests each withheld member, and compare the pins.

    ``gate`` is a member the ``--include-unapproved`` build indexes and a default
    query refuses; ``census`` is a member excluded before the index, by status or
    by the serving ceiling. Both are computed from the member's own final state,
    never read from a declared key.

    The two are complementary by construction, so disjointness is asserted
    nowhere: no input satisfies both predicates, which makes such an assertion
    one that cannot fail. What the union can and does catch is a member in
    *neither* class -- approved and within the ceiling, hence indexed and
    ungated, the state ``_surfacing_withheld_violations`` names outright.
    """
    states = _withheld_final_states(corpus)
    gate = {
        item
        for item, (status, sensitivity) in states.items()
        if status in INDEXED_STATUSES - DEFAULT_STATUSES and sensitivity in SERVED_SENSITIVITIES
    }
    census = {
        item
        for item, (status, sensitivity) in states.items()
        if status not in INDEXED_STATUSES or sensitivity not in SERVED_SENSITIVITIES
    }
    found = []
    if gate != GATE_TESTED_ITEMS:
        found.append(f"gate-tested: derived {sorted(gate)}, pinned {sorted(GATE_TESTED_ITEMS)}")
    if census != CENSUS_TESTED_ITEMS:
        found.append(
            f"census-tested: derived {sorted(census)}, pinned {sorted(CENSUS_TESTED_ITEMS)}"
        )
    if unclassified := set(states) - gate - census:
        found.append(f"withheld member in neither class: {sorted(unclassified)}")
    return found


def _surfacing_withheld_violations(corpus: Corpus) -> list[str]:
    return [
        f"{item}: ends {status}/{sensitivity}, which a default response may hold"
        for item, (status, sensitivity) in sorted(_withheld_final_states(corpus).items())
        if status in DEFAULT_STATUSES and sensitivity in SERVED_SENSITIVITIES
    ]


def _relevant_state_violations(corpus: Corpus) -> list[str]:
    enabled = {query["id"] for query in corpus.queries["queries"] if query.get("enabled", True)}
    states = _replay(corpus, {"visible", "withheld"})
    found = []
    for entry in corpus.judgements["judgements"]:
        if entry["queryId"] not in enabled:
            continue
        for item in sorted(_ids(entry, "relevant")):
            state = states.get(item, {})
            status = state.get("status", "uncreated")
            sensitivity = state.get("sensitivity", "uncreated")
            if status not in DEFAULT_STATUSES:
                found.append(f"{entry['queryId']}: relevant item {item} ends status {status}")
            if sensitivity not in SERVED_SENSITIVITIES:
                found.append(
                    f"{entry['queryId']}: relevant item {item} ends sensitivity {sensitivity}"
                )
    return found


# -- the corpus as committed satisfies every rule -----------------------------


def test_every_contract_document_and_migration_validates_against_its_schema() -> None:
    assert _schema_violations(CORPUS) == []


def test_every_content_sha256_matches_the_body_bytes_committed_beside_it() -> None:
    assert _content_hash_violations(CORPUS) == []


def test_no_migration_document_declares_a_plane() -> None:
    """ADR-0036 decision 6: the plane lives in the manifest and nowhere else.

    ``_schema_violations`` above runs ``MIGRATION_VALIDATOR`` over these very
    documents and its root carries ``additionalProperties: false``, so a
    ``plane`` key here is already refused -- as a bare ``json_path``, by a rule
    that would report the same way for a typo. This one names the key, says
    where the plane belongs instead, and is what still holds the decision if
    that root is ever reopened.
    """
    assert _plane_key_violations(CORPUS) == []


def test_the_manifest_lists_every_migration_once_in_ulid_order() -> None:
    assert _manifest_order_violations(CORPUS) == []


def test_every_operation_is_one_this_modules_replay_folds_or_may_safely_skip() -> None:
    """``_replay`` is a four-operation fold, and nine more are safe to skip.

    The schema admits fourteen. ``restoreItem`` is the one in neither set: it
    sets ``approved``, so a corpus carrying one would have a census, a withheld
    member's final state and a coverage split derived from a status the real
    apply contradicts -- and every rule here reads that fold, so all of them
    would agree with each other and with nothing else.
    """
    assert _unmodelled_operation_violations(CORPUS) == []


def test_restore_item_is_the_only_operation_this_module_declines_to_classify() -> None:
    """The rule above only bites while its two sets between them cover the schema.

    An operation added to ``migration.schema.json`` and classified nowhere here
    is admitted by ``_unmodelled_operation_violations`` the moment somebody uses
    it -- which is the right direction -- but nobody has decided whether
    ``_replay`` should fold it. This is where that decision is forced, and it is
    also what holds the claim that ``restoreItem`` is the single gap.
    """
    unclassified = SCHEMA_OPERATIONS - REPLAYED_OPERATIONS - STATE_PRESERVING_OPERATIONS

    assert unclassified == {"restoreItem"}


def test_every_committed_body_is_named_by_exactly_one_migration() -> None:
    """A ``.md`` under ``knowledge/`` that no ``contentFile`` names is checked by nothing.

    The docs link walk skips the whole prefix and ``_content_hash_violations``
    keys off ``contentFile``, so an orphan body would be committed, shipped and
    read by no rule at all. The other two directions matter for the same reason
    in reverse: a ``contentFile`` naming nothing breaks the apply, and two
    migrations naming one body make a single edit move two revisions.
    """
    assert _body_file_violations(CORPUS) == []


def test_no_absent_topic_token_appears_anywhere_the_retriever_can_reach() -> None:
    """The two absent-topic probes can only abstain while their terms are absent.

    ``queries.yaml`` says every token was checked absent from every body and
    every title; that sentence was the whole control. A body edited with its
    ``contentSha256`` re-pinned -- the way an author would edit it -- reaches the
    index, makes the probe's correct answer a hit rather than an abstention, and
    turns an abstention-rate regression into a fixture defect nobody can see.
    Substring and case-insensitive, because the word retriever tokenizes and
    folds case, so ``Refunds`` is a match for ``refund``.
    """
    assert _absent_topic_violations(CORPUS) == []


def test_every_unknown_class_query_is_judged_by_abstention_and_only_those() -> None:
    """The class and its judgement have to agree, or the class measures nothing.

    An ``unknown`` query judged with a ``relevant`` list asks the retriever to
    return a document for a question the corpus is built not to answer, so the
    abstention rate this class exists to measure is computed over one query
    fewer and nothing reports it. The converse is the same defect mirrored:
    ``expectAbstention`` on a query whose answer the corpus does hold scores an
    abstention as correct where it is a miss.
    """
    assert _abstention_class_violations(CORPUS) == []


def test_no_visible_migration_depends_on_a_withheld_one() -> None:
    """The ``clean`` build applies the visible plane alone; a visible migration
    naming a withheld item would make that build fail outright, which is a
    corpus defect and not a retrieval measurement.
    """
    assert _plane_dependency_violations(CORPUS) == []


def test_no_two_queries_share_an_id() -> None:
    assert _query_id_violations(CORPUS) == []


def test_every_enabled_query_runs_against_both_corpora() -> None:
    """A query that ran against ``full`` alone could not show that the withheld
    plane moved nothing, so the disclosure-equality form ADR-0036 decision 6
    fixes has no exceptions to enumerate.
    """
    assert _corpora_invariant_violations(CORPUS) == []


def test_every_enabled_query_has_exactly_one_judgement() -> None:
    assert _judgement_coverage_violations(CORPUS) == []


def test_no_judgement_both_requires_and_forbids_an_item() -> None:
    assert _disjointness_violations(CORPUS) == []


def test_no_judgement_entry_judges_nothing() -> None:
    """``judgementEntry`` requires ``queryId`` alone, so an entry carrying none
    of the four judging fields validates while asserting nothing (ADR-0036,
    *Still owed*).
    """
    assert _empty_judgement_violations(CORPUS) == []


def test_no_evidence_entry_subsumes_another() -> None:
    """``filePath`` narrows a ``sourceUri``, so ``(u, absent)`` standing beside
    ``(u, f)`` lets one cited anchor satisfy both entries and score twice,
    inflating evidence precision (ADR-0036, *Still owed*).
    """
    assert _evidence_subsumption_violations(CORPUS) == []


def test_every_judged_item_exists_and_every_relevant_one_is_visible_plane() -> None:
    """A withheld item may be named in ``forbidden`` and never in ``relevant``.

    A ``relevant`` withheld item would ask the ``clean`` corpus to return a
    document it never held, so every equality query would score a miss that is
    not a retrieval defect.
    """
    assert _judged_item_violations(CORPUS) == []


def test_the_manifest_census_agrees_with_the_migrations_it_declares() -> None:
    assert _census_violations(CORPUS) == []


def test_the_census_omits_every_zero_count_label() -> None:
    """The manifest schema's own words: "a status with zero expected items is
    simply absent rather than listed as `0`".
    """
    counts = [
        count
        for plane in ("full", "clean")
        for key in ("byStatus", "bySensitivity")
        for count in CORPUS.manifest["census"][plane][key].values()
    ]

    assert 0 not in counts


def test_each_withheld_member_ends_in_the_state_its_coverage_follows_from() -> None:
    """The seven states the split below is derived from, said rather than implied.

    Two are gated at query time, three are retired by status and two sit above
    the serving ceiling -- and the last two of those seven reach their state only
    through ``deprecateItem`` and ``changeSensitivity`` in a later migration, so
    a replay that folded revisions alone would report them approved and internal.
    """
    assert _withheld_final_states(CORPUS) == WITHHELD_FINAL_STATES


def test_the_withheld_plane_splits_into_gate_tested_and_census_tested_members() -> None:
    """ADR-0036 decision 6: which instrument covers a withheld member is derived.

    The response-equality battery is vacuous for a member no build indexes: both
    corpora answer identically because neither holds the row, and the comparison
    would pass with the gate deleted. Only the two members the
    ``--include-unapproved`` build does index test the gate; the other five are
    tested by the manifest census, which counts them applied; that they are not
    indexed is the S2 loader's chunk comparison (PR #780), not this file's.
    Nothing in the fixture declares that split, so without this pin a member
    could change side -- a draft approved, a sensitivity lowered -- and the
    battery would go on reporting coverage it no longer has.
    """
    assert _coverage_split_violations(CORPUS) == []


def test_no_withheld_member_ends_in_a_state_a_default_response_may_hold() -> None:
    """The invariant the whole two-corpus design rests on (ADR-0036 decision 6).

    A withheld member that ended approved and within the serving ceiling would be
    written into ``full`` and refused by nothing at query time, so ``full`` would
    return a row ``clean`` never held and every equality query reaching it would
    fail by construction -- a corpus defect read as a retrieval defect. It is
    also what makes the census class above exhaustive: a member outside the gate
    class is excluded before the index only while this holds.
    """
    assert _surfacing_withheld_violations(CORPUS) == []


def test_every_enabled_query_judges_only_items_a_default_response_may_return() -> None:
    """A ``relevant`` item the default gate withholds scores a miss on every run.

    The battery runs at default flags, so Recall@k for such a judgement is zero
    against both corpora however well retrieval works, and the metric measures
    the judgement rather than the retriever. Both axes of the default gate, not
    one: a visible item reclassified above the serving ceiling is withheld just
    as completely as an unapproved one, and reclassifying is the likelier edit.
    Strengthens the visible-plane rule above, which admits a visible item of any
    status and any sensitivity.
    """
    assert _relevant_state_violations(CORPUS) == []


def test_every_source_anchor_names_a_file_this_repository_still_tracks() -> None:
    """Half of what the docs walk's corpus exclusion rests on: the source is there.

    ``.github/workflows/shared.yml`` skips ``tests/fixtures/eval/knowledge/`` and
    argues nothing goes unchecked because every one of those links is still
    checked at the ``docs/adr/`` file it was snapshotted from. An anchor naming a
    file this repository no longer tracks silently removes that file from the
    walk, and with it every link the exclusion assumed was still being checked.
    """
    paths = _anchor_paths(CORPUS)

    assert len(set(paths)) == ADR_SNAPSHOT_COUNT
    assert sorted(_tracked_sources(paths)) == sorted(set(paths))


def test_every_adr_snapshot_links_only_where_its_live_source_still_links() -> None:
    """The other half, and the one tracked-and-exists does not establish.

    The exclusion's argument is that the excluded links are *the same links*
    still checked at ``docs/adr/``. That holds only while the live ADR still
    carries them; a link deleted there leaves the snapshot's copy checked
    nowhere. The snapshot is not the thing to edit when this reddens -- its body
    is ``contentSha256``-pinned and frozen -- the walk's closure argument is.

    A subset rather than an equality, deliberately: the live ADR gaining links
    is how documentation normally moves, and each new one is walked at
    ``docs/adr/`` already, so an equality here would redden on the first
    ordinary edit and teach people to ignore it.
    """
    live = _tracked_sources(_anchor_paths(CORPUS))

    assert _snapshot_link_violations(CORPUS, live) == []


def test_the_disabled_historical_query_may_judge_superseded_items_relevant() -> None:
    """Why the rule above is scoped to enabled queries rather than to all of them.

    ``q-hist-ttl-evolution`` asks how the TTL policy changed over time, so the
    superseded chain is its answer and not its error. It is disabled, the harness
    skips it (Phase D), and the scope is what lets the judgement stay committed
    instead of being deleted to satisfy a rule it was never in.
    """
    states = _replay(CORPUS, {"visible", "withheld"})
    historical = next(
        entry
        for entry in CORPUS.judgements["judgements"]
        if entry["queryId"] == "q-hist-ttl-evolution"
    )
    assert {
        item
        for item in _ids(historical, "relevant")
        if states[item]["status"] not in DEFAULT_STATUSES
    } == {"domain.session-token-ttl-v1", "domain.session-token-ttl-v2"}

    judged = {violation.split(":")[0] for violation in _relevant_state_violations(CORPUS)}

    assert "q-hist-ttl-evolution" not in judged


# -- each rule, over a copy carrying the defect it exists to catch ------------


def _revisions(migrations: list[tuple[str, dict[str, Any]]]) -> list[dict[str, Any]]:
    return [
        operation
        for _name, document in migrations
        for operation in document["operations"]
        if operation["op"] == "upsertRevision"
    ]


def test_the_schema_rule_catches_a_query_id_the_pattern_refuses() -> None:
    queries = copy.deepcopy(CORPUS.queries)
    queries["queries"][0]["id"] = "Q-Uppercase"

    assert _schema_violations(replace(CORPUS, queries=queries)) != []


def test_the_schema_rule_catches_a_migration_missing_its_api_version() -> None:
    name, document = CORPUS.migrations[0]
    broken = copy.deepcopy(document)
    del broken["apiVersion"]

    assert _schema_violations(replace(CORPUS, migrations=((name, broken),))) != []


def test_the_content_hash_rule_catches_a_body_edited_after_it_was_pinned() -> None:
    bodies = dict(CORPUS.bodies)
    edited = next(iter(bodies))
    bodies[edited] = bodies[edited] + b"one appended byte\n"

    assert _content_hash_violations(replace(CORPUS, bodies=bodies)) != []


def test_the_plane_rule_catches_a_plane_written_into_a_migration() -> None:
    name, document = CORPUS.migrations[0]
    perturbed = copy.deepcopy(document)
    perturbed["plane"] = "withheld"

    assert _plane_key_violations(replace(CORPUS, migrations=((name, perturbed),))) != []


def test_the_operation_rule_catches_a_restore_the_replay_folds_as_if_it_were_absent() -> None:
    """Three facts, and the rule exists because of the first two.

    The schema admits ``restoreItem`` with nothing but an ``itemId``, so no
    contract document refuses the perturbation below. ``_replay``'s ``case _``
    then drops it, and the fold goes on reporting ``deprecated`` for an item
    ``MigrationEngine`` leaves ``approved`` -- which would move this member out
    of the census class and into neither class, with no rule saying so.
    """
    migrations = copy.deepcopy(list(CORPUS.migrations))
    deprecating = next(
        document
        for name, document in migrations
        if name.endswith("deprecate-flaky-quarantine.yaml")
    )
    deprecating["operations"].append(
        {"op": "restoreItem", "itemId": "testing.deprecated-flaky-quarantine"}
    )
    perturbed = replace(CORPUS, migrations=tuple(migrations))

    assert _schema_violations(perturbed) == []
    assert _replay(perturbed, {"visible", "withheld"})["testing.deprecated-flaky-quarantine"] == {
        "status": "deprecated",
        "sensitivity": "internal",
    }

    assert _unmodelled_operation_violations(perturbed) != []


def test_the_body_rule_catches_a_committed_body_no_migration_names() -> None:
    migrations = copy.deepcopy(list(CORPUS.migrations))
    _name, document = migrations[0]
    document["operations"] = [
        operation for operation in document["operations"] if operation["op"] != "upsertRevision"
    ]

    assert _body_file_violations(replace(CORPUS, migrations=tuple(migrations))) != []


def test_the_body_rule_catches_a_content_file_naming_nothing_committed() -> None:
    migrations = copy.deepcopy(list(CORPUS.migrations))
    _revisions(migrations)[0]["contentFile"] = "../knowledge/architecture/never-written.md"

    assert _body_file_violations(replace(CORPUS, migrations=tuple(migrations))) != []


def test_the_body_rule_catches_two_migrations_claiming_one_body() -> None:
    migrations = copy.deepcopy(list(CORPUS.migrations))
    first, second = _revisions(migrations)[:2]
    second["contentFile"] = first["contentFile"]

    assert _body_file_violations(replace(CORPUS, migrations=tuple(migrations))) != []


def test_the_order_rule_catches_a_manifest_listing_two_migrations_out_of_order() -> None:
    manifest = copy.deepcopy(CORPUS.manifest)
    manifest["migrations"][0], manifest["migrations"][1] = (
        manifest["migrations"][1],
        manifest["migrations"][0],
    )

    assert _manifest_order_violations(replace(CORPUS, manifest=manifest)) != []


def test_the_order_rule_catches_a_migration_the_manifest_never_declares() -> None:
    manifest = copy.deepcopy(CORPUS.manifest)
    del manifest["migrations"][-1]

    assert _manifest_order_violations(replace(CORPUS, manifest=manifest)) != []


def test_the_plane_dependency_rule_catches_a_visible_edge_onto_a_withheld_item() -> None:
    manifest = copy.deepcopy(CORPUS.manifest)
    migrations = copy.deepcopy(list(CORPUS.migrations))
    withheld = next(
        entry["file"] for entry in manifest["migrations"] if entry["plane"] == "withheld"
    )
    visible = next(index for index, (name, _doc) in enumerate(migrations) if name != withheld)
    migrations[visible][1]["operations"].append(
        {
            "op": "addRelation",
            "sourceItemId": "architecture.a-purge-is-a-build",
            "relationType": "related_to",
            "targetItemId": "domain.rejected-credential-cache",
        }
    )

    perturbed = replace(CORPUS, manifest=manifest, migrations=tuple(migrations))

    assert _plane_dependency_violations(perturbed) != []


def test_the_absent_topic_rule_catches_a_pinned_token_reintroduced_into_a_body() -> None:
    """The edit the pin exists for, shaped as an author would make it.

    Appending to a body and re-pinning its ``contentSha256`` leaves every other
    rule here green -- the hash matches, the schema validates, the census is
    unmoved -- while ``q-absent-payment-flow`` now has an answer to find.
    """
    bodies = dict(CORPUS.bodies)
    edited = next(iter(bodies))
    bodies[edited] = bodies[edited] + b"\nRefund and chargeback for a merchant payment.\n"

    assert _absent_topic_violations(replace(CORPUS, bodies=bodies)) != []


def test_the_absent_topic_rule_catches_a_pinned_token_reintroduced_into_a_title() -> None:
    """Titles are indexed beside bodies, and no body changes, so no hash moves."""
    migrations = copy.deepcopy(list(CORPUS.migrations))
    _revisions(migrations)[0]["metadata"]["title"] = "Ingress autoscaling for the kubelet"

    assert _absent_topic_violations(replace(CORPUS, migrations=tuple(migrations))) != []


def test_the_absent_topic_rule_catches_a_probe_asking_for_a_token_nobody_checked() -> None:
    """Widening a probe is the other way the claim rots: the list stops covering it."""
    queries = copy.deepcopy(CORPUS.queries)
    probe = next(q for q in queries["queries"] if q["id"] == "q-absent-kubernetes")
    probe["query"] = f"{probe['query']} sidecar"

    assert _absent_topic_violations(replace(CORPUS, queries=queries)) != []


def test_the_abstention_rule_catches_an_unknown_query_judged_with_relevant_items() -> None:
    judgements = copy.deepcopy(CORPUS.judgements)
    entry = next(e for e in judgements["judgements"] if e["queryId"] == "q-absent-kubernetes")
    entry.pop("expectAbstention", None)
    entry["relevant"] = [{"itemId": "architecture.raptor-forest"}]

    assert _abstention_class_violations(replace(CORPUS, judgements=judgements)) != []


def test_the_abstention_rule_catches_an_answerable_query_judged_by_abstention() -> None:
    judgements = copy.deepcopy(CORPUS.judgements)
    entry = next(e for e in judgements["judgements"] if e["queryId"] == "q-sqlite-derived")
    entry["expectAbstention"] = True

    assert _abstention_class_violations(replace(CORPUS, judgements=judgements)) != []


def test_the_query_id_rule_catches_a_duplicated_id() -> None:
    queries = copy.deepcopy(CORPUS.queries)
    queries["queries"].append(copy.deepcopy(queries["queries"][0]))

    assert _query_id_violations(replace(CORPUS, queries=queries)) != []


def test_the_corpora_rule_catches_an_enabled_query_running_against_one_index() -> None:
    queries = copy.deepcopy(CORPUS.queries)
    queries["queries"][0]["corpora"] = ["full"]

    assert _corpora_invariant_violations(replace(CORPUS, queries=queries)) != []


def test_the_coverage_rule_catches_an_enabled_query_nobody_judged() -> None:
    judgements = copy.deepcopy(CORPUS.judgements)
    del judgements["judgements"][0]

    assert _judgement_coverage_violations(replace(CORPUS, judgements=judgements)) != []


def test_the_coverage_rule_catches_a_judgement_naming_no_declared_query() -> None:
    judgements = copy.deepcopy(CORPUS.judgements)
    judgements["judgements"][0]["queryId"] = "q-never-declared"

    assert _judgement_coverage_violations(replace(CORPUS, judgements=judgements)) != []


def test_the_coverage_rule_catches_one_query_judged_twice() -> None:
    judgements = copy.deepcopy(CORPUS.judgements)
    judgements["judgements"].append(copy.deepcopy(judgements["judgements"][0]))

    assert _judgement_coverage_violations(replace(CORPUS, judgements=judgements)) != []


def test_the_disjointness_rule_catches_an_item_both_required_and_forbidden() -> None:
    judgements = copy.deepcopy(CORPUS.judgements)
    entry = judgements["judgements"][0]
    entry["forbidden"] = [copy.deepcopy(entry["relevant"][0])]

    assert _disjointness_violations(replace(CORPUS, judgements=judgements)) != []


def test_the_empty_judgement_rule_catches_an_entry_carrying_only_a_query_id() -> None:
    judgements = copy.deepcopy(CORPUS.judgements)
    judgements["judgements"][0] = {"queryId": judgements["judgements"][0]["queryId"]}

    assert _empty_judgement_violations(replace(CORPUS, judgements=judgements)) != []


@pytest.mark.parametrize(
    "extra",
    [
        pytest.param({"sourceUri": "https://github.com/theurian/theurian.git"}, id="subsumes"),
        pytest.param(
            {
                "sourceUri": "https://github.com/theurian/theurian.git",
                "filePath": "docs/adr/0006-immutable-revisions-and-optimistic-concurrency.md",
            },
            id="duplicate-pair",
        ),
    ],
)
def test_the_evidence_rule_catches_an_entry_that_scores_one_anchor_twice(
    extra: dict[str, str],
) -> None:
    judgements = copy.deepcopy(CORPUS.judgements)
    entry = next(e for e in judgements["judgements"] if e["queryId"] == "q-optimistic-concurrency")
    entry["evidence"].append(extra)

    assert _evidence_subsumption_violations(replace(CORPUS, judgements=judgements)) != []


def test_the_judged_item_rule_catches_a_judgement_naming_an_item_no_migration_creates() -> None:
    judgements = copy.deepcopy(CORPUS.judgements)
    judgements["judgements"][0]["relevant"][0]["itemId"] = "architecture.never-created"

    assert _judged_item_violations(replace(CORPUS, judgements=judgements)) != []


def test_the_judged_item_rule_catches_a_withheld_item_promoted_into_relevant() -> None:
    judgements = copy.deepcopy(CORPUS.judgements)
    judgements["judgements"][0]["relevant"][0]["itemId"] = "domain.rejected-credential-cache"

    assert _judged_item_violations(replace(CORPUS, judgements=judgements)) != []


@pytest.mark.parametrize("plane", ["full", "clean"])
def test_the_census_rule_catches_a_declared_item_count_the_migrations_do_not_support(
    plane: str,
) -> None:
    manifest = copy.deepcopy(CORPUS.manifest)
    manifest["census"][plane]["items"] += 1

    assert _census_violations(replace(CORPUS, manifest=manifest)) != []


@pytest.mark.parametrize("key", ["byStatus", "bySensitivity"])
def test_the_census_rule_catches_a_label_moved_between_two_buckets(key: str) -> None:
    manifest = copy.deepcopy(CORPUS.manifest)
    labels = sorted(manifest["census"]["full"][key])
    manifest["census"]["full"][key][labels[0]] += 1
    manifest["census"]["full"][key][labels[-1]] -= 1

    assert _census_violations(replace(CORPUS, manifest=manifest)) != []


def test_the_census_rule_reports_a_missing_census_key_instead_of_raising() -> None:
    """A manifest census that lost ``byStatus`` is a corpus defect to name.

    Subscripting it raises ``KeyError`` out of the rule, which fails the one test
    that called it and leaves every later rule in this module unrun -- so the
    reader is told about a manifest key and not about whatever else is wrong.
    """
    manifest = copy.deepcopy(CORPUS.manifest)
    del manifest["census"]["full"]["byStatus"]

    assert _census_violations(replace(CORPUS, manifest=manifest)) != []


def _restated(item: str, **fields: str) -> Corpus:
    """A copy whose every revision of ``item`` carries different metadata."""
    migrations = copy.deepcopy(list(CORPUS.migrations))
    for _name, document in migrations:
        for operation in document["operations"]:
            if operation["op"] == "upsertRevision" and operation["itemId"] == item:
                operation["metadata"].update(fields)
    return replace(CORPUS, migrations=tuple(migrations))


def test_the_split_rule_catches_a_withheld_draft_promoted_to_approved() -> None:
    perturbed = _restated("security.draft-scan-hardening", status="approved")

    assert _coverage_split_violations(perturbed) != []


def test_the_split_rule_catches_a_member_the_serving_ceiling_stops_excluding() -> None:
    """The arm that has to be a third class rather than a complement: lowering
    this member to ``internal`` leaves it indexed *and* ungated, so it belongs to
    neither the gate class nor the census class.
    """
    perturbed = _restated("security.confidential-token-rotation", sensitivity="internal")

    assert any("neither class" in violation for violation in _coverage_split_violations(perturbed))


def test_the_surfacing_rule_catches_a_withheld_member_lowered_into_the_ceiling() -> None:
    perturbed = _restated("security.confidential-token-rotation", sensitivity="internal")

    assert _surfacing_withheld_violations(perturbed) != []


def test_the_relevant_state_rule_catches_an_enabled_query_judging_a_superseded_item() -> None:
    judgements = copy.deepcopy(CORPUS.judgements)
    entry = next(e for e in judgements["judgements"] if e["queryId"] == "q-sqlite-derived")
    entry["relevant"][0]["itemId"] = "domain.session-token-ttl-v1"

    assert _relevant_state_violations(replace(CORPUS, judgements=judgements)) != []


def test_the_relevant_state_rule_catches_a_relevant_item_raised_above_the_ceiling() -> None:
    """The axis the status conjunct alone does not reach.

    Reclassifying a visible, approved item to ``confidential`` leaves it
    ``approved`` and visible-plane, so the status rule, the visible-plane rule
    and the withheld-member rules all stay green -- and every enabled query
    naming it scores Recall@k of zero against both corpora for good.
    """
    perturbed = _restated("architecture.sqlite-is-a-derived-artifact", sensitivity="confidential")

    assert any(
        "ends sensitivity confidential" in violation
        for violation in _relevant_state_violations(perturbed)
    )


def test_the_relevant_state_rule_reaches_the_historical_query_once_it_is_enabled() -> None:
    """The scope is what exempts that query, not a rule too weak to reach it.

    In memory only: ``queries.schema.json`` refuses a ``historical`` query with
    ``enabled: true``, so the state cannot be written to the fixture at all.
    """
    queries = copy.deepcopy(CORPUS.queries)
    next(q for q in queries["queries"] if q["id"] == "q-hist-ttl-evolution")["enabled"] = True

    assert _relevant_state_violations(replace(CORPUS, queries=queries)) != []


def test_the_snapshot_link_rule_catches_a_live_adr_dropping_a_link_the_snapshot_keeps() -> None:
    """The frozen half cannot move, so only the live half can break the argument.

    Deleting one link from ``docs/adr/0008-raptor-forest.md`` leaves the
    snapshot's copy of it checked by nothing at all: the walk skips the corpus,
    and the file that used to carry it no longer does.
    """
    live = dict(_tracked_sources(_anchor_paths(CORPUS)))
    source = "docs/adr/0008-raptor-forest.md"
    dropped = sorted(_relative_links(live[source]))[0]
    live[source] = re.sub(rf"\]\({re.escape(dropped)}[^)]*\)", "](#gone)", live[source])

    assert dropped not in _relative_links(live[source])
    assert _snapshot_link_violations(CORPUS, live) != []


def test_the_snapshot_link_rule_reads_a_link_target_the_way_the_walk_reads_it() -> None:
    """Three ordinary edits to a live ADR that move no target, and must not redden.

    Renaming the heading a link points at, adding a hover title, and repointing
    an external URL all leave the walk checking the same repository paths -- it
    resolves ``target.split("#")[0]`` after ``split(" ")[0]`` and skips anything
    with a scheme. A rule that read any of the three as a deleted link would go
    RED on the pull request that improved the documentation, which is how a
    check gets silenced rather than fixed.
    """
    live = dict(_tracked_sources(_anchor_paths(CORPUS)))
    source = "docs/adr/0008-raptor-forest.md"
    before = _relative_links(live[source])
    for old, new in (
        ("../architecture/overview.md", "../architecture/overview.md#renamed-heading"),
        ("../architecture/raptor.md", '../architecture/raptor.md "The forest"'),
        (
            "https://github.com/theurian/theurian/issues/119",
            "https://github.com/theurian/theurian/issues/999",
        ),
    ):
        live[source] = live[source].replace(f"]({old})", f"]({new})")

    assert _relative_links(live[source]) == before
    assert _snapshot_link_violations(CORPUS, live) == []


def test_the_snapshot_link_rule_catches_an_anchor_naming_an_untracked_file() -> None:
    live = dict(_tracked_sources(_anchor_paths(CORPUS)))
    del live["docs/adr/0004-sqlite-is-a-derived-artifact.md"]

    assert _snapshot_link_violations(CORPUS, live) != []
