"""The frozen fixture corpus under ``tests/fixtures/eval/`` holds its own rules.

Most of them belong to the S2 loader (``tools/eval/corpus.py``), and this file
consumes that loader rather than reimplementing it -- one implementation, two
consumers (#794). Until that issue it carried its own copy of the loader's
schema validation and eleven of its thirteen named refusals, with no equivalence
pin, and the two had already drifted: the loader exempts a *disabled* query's
judgement from the default-flag retrievability clauses (ADR-0036, "What the rule
asks of a corpus editor", PR #793), and the copy here did not.

What remains is what the loader does not carry -- the body bytes and their
``contentSha256``, the absent-topic tokens, the abstention classes, the
``corpora`` invariant, the ADR-snapshot link walk, and the manifest census
derived without a build -- plus three rules that overlap the loader's semantics
without consuming it, each carrying the equivalence pin the last section holds.

Every rule is asserted twice: once over the corpus as committed, and once over a
copy carrying exactly the defect the rule exists to catch. A converged rule's
copy is written to a temporary directory and handed to ``load_corpus``, so its
twin drives the shared implementation; a rule that stays here is perturbed in
memory as before. The one rule whose subject is a *pair* -- an ADR snapshot and
the ``docs/adr/`` file it was taken from -- is perturbed on the live half,
because the frozen half is what the pin exists to keep unedited.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
import shutil
import subprocess
import sys
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import pytest
import yaml

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[3]

#: ``tools/eval`` is a flat script directory, not a package: this file puts it
#: on ``sys.path`` itself, the route ``test_harness_pins.py`` and
#: ``test_adr36_ratchet.py`` in this directory already take.
_HARNESS_DIR = REPO_ROOT / "tools" / "eval"
if str(_HARNESS_DIR) not in sys.path:
    sys.path.insert(0, str(_HARNESS_DIR))

import corpus as harness_corpus  # noqa: E402

CORPUS_ROOT = REPO_ROOT / "tests" / "fixtures" / "eval"
MIGRATIONS_DIR = CORPUS_ROOT / "migrations"
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

#: Which withheld members the response-equality battery tests, and which the
#: manifest census tests instead (ADR-0036 decision 6). Derived by the loader
#: from each member's final status and sensitivity; pinned here so a member
#: changing side cannot do so silently. The manifest declares no coverage key --
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

#: Each way a replay can drift, the committed member that separates it from the
#: real fold, and which of that member's revisions the drifted fold would report
#: instead (0 the first, -1 the last). Read by the fold-equivalence pin's
#: positive control: the member's final state differs from that revision's, so a
#: pair drifted there disagrees rather than agreeing vacuously.
FOLD_BRANCH_WITNESSES = {
    "first-revision-kept": ("domain.session-token-ttl-v1", 0),
    "deprecate-item-lost": ("testing.deprecated-flaky-quarantine", -1),
    "change-sensitivity-lost": ("domain.restricted-retention-exceptions", -1),
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

_SCHEMA_DEFS = json.loads(MIGRATION_SCHEMA.read_text(encoding="utf-8"))["$defs"]

#: Every ``op`` the migration schema admits, read off its own discriminated
#: union rather than listed here.
SCHEMA_OPERATIONS = frozenset(
    definition["properties"]["op"]["const"]
    for definition in _SCHEMA_DEFS.values()
    if "const" in definition.get("properties", {}).get("op", {})
)

#: The sensitivity a migration that omits the optional field publishes, read off
#: the contract that declares it the way ``SCHEMA_OPERATIONS`` is. Declared on
#: ``revisionMetadata`` alone; ``createItem`` takes the same value because both
#: operations' domain dataclasses default to ``DEFAULT_SENSITIVITY``
#: (``theurian.domain.migration``), which is the constant the loader's fold
#: fills. Two sources for one value, deliberately: a pin whose halves read the
#: same source cannot report them disagreeing.
SCHEMA_DEFAULT_SENSITIVITY: str = _SCHEMA_DEFS["revisionMetadata"]["properties"]["sensitivity"][
    "default"
]


@pytest.fixture(scope="module")
def loaded() -> harness_corpus.Corpus:
    """The committed corpus as ``load_corpus`` returns it.

    Every rule this file converged onto the loader has its positive assertion
    here: the corpus loading at all is that assertion, since ``load_corpus``
    raises on the first rule violated.
    """
    return harness_corpus.load_corpus(CORPUS_ROOT)


# -- handing a perturbed copy to the loader -----------------------------------


def _written(corpus: Corpus, root: Path) -> Path:
    """``corpus`` serialised where ``load_corpus`` reads it.

    The bodies are not written: the loader never opens a ``contentFile``
    (``git grep -c "contentFile" tools/eval/corpus.py`` finds none), so a copy
    that carries only the three contract files and the migrations is the whole
    input to every rule it holds.

    ``migrations/`` is cleared rather than written over, so the tree is a
    function of ``corpus`` and not of whatever a previous call left in ``root``
    -- a twin that *removes* a migration would otherwise be handed the file it
    removed. The three contract files need no counterpart: every call writes
    all three.
    """
    shutil.rmtree(root / "migrations", ignore_errors=True)
    (root / "migrations").mkdir(parents=True)
    for name, document in (
        ("manifest.yaml", corpus.manifest),
        ("queries.yaml", corpus.queries),
        ("judgements.yaml", corpus.judgements),
    ):
        (root / name).write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")
    for name, document in corpus.migrations:
        (root / "migrations" / name).write_text(
            yaml.safe_dump(document, sort_keys=False), encoding="utf-8"
        )
    return root


def _refusal(corpus: Corpus, root: Path) -> harness_corpus.CorpusError:
    with pytest.raises(harness_corpus.CorpusError) as excinfo:
        harness_corpus.load_corpus(_written(corpus, root))
    return excinfo.value


def _accepted(corpus: Corpus, root: Path) -> harness_corpus.Corpus:
    return harness_corpus.load_corpus(_written(corpus, root))


# -- derivation helpers -------------------------------------------------------


def _ids(entry: dict[str, Any], field: str) -> set[str]:
    return {item["itemId"] for item in entry.get(field, [])}


def _planes(corpus: Corpus) -> dict[str, str]:
    return {entry["file"]: entry["plane"] for entry in corpus.manifest["migrations"]}


def _creating_plane(corpus: Corpus) -> dict[str, str]:
    """Each created itemId, mapped to the plane of the migration that created it.

    A migration the manifest does not declare reads as ``withheld`` here. That
    default is never reached by a corpus this module passes --
    ``_manifest_listing_violations`` reports declared-against-present as a
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

    The loader folds the same four in ``_final_status_and_sensitivity`` and
    cannot be consumed here, because it has no plane filter and the ``clean``
    census needs one. Kept as a second implementation under the equivalence pin
    the last section holds, not as an unpinned copy (#794).

    ``sensitivity`` is optional on ``createItem`` and on a revision's metadata.
    Both branches fill ``SCHEMA_DEFAULT_SENSITIVITY``, the default the migration
    contract declares, where the loader's fold fills the domain constant --
    which is what makes the pin below a comparison. ``status`` needs no
    counterpart: the schema requires it in every metadata block, which is why
    the loader subscripts it too.
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
                    state[item] = {
                        "status": "",
                        "sensitivity": operation.get("sensitivity", SCHEMA_DEFAULT_SENSITIVITY),
                    }
                case "upsertRevision":
                    state[item] = {
                        "status": operation["metadata"]["status"],
                        "sensitivity": operation["metadata"].get(
                            "sensitivity", SCHEMA_DEFAULT_SENSITIVITY
                        ),
                    }
                case "deprecateItem" if item in state:
                    state[item]["status"] = "deprecated"
                case "changeSensitivity" if item in state:
                    state[item]["sensitivity"] = operation["sensitivity"]
                case _:
                    pass
    return state


def _loader_fold(
    migrations: tuple[tuple[str, dict[str, Any]], ...],
) -> tuple[dict[str, str], dict[str, str]]:
    """``_final_status_and_sensitivity`` over ``migrations``, every item it names."""
    manifest = harness_corpus.Manifest(
        contract_version=1,
        corpus_id="fold-equivalence-pin",
        k_values=(),
        migrations=tuple(
            harness_corpus.MigrationEntry(file=name, plane="visible") for name, _doc in migrations
        ),
        census={},
        description=None,
    )
    documents = dict(migrations)
    return harness_corpus._final_status_and_sensitivity(
        manifest, documents, harness_corpus._all_item_ids(manifest, documents)
    )


def _derive_census(corpus: Corpus, wanted: set[str]) -> dict[str, Any]:
    state = _replay(corpus, wanted)
    return {
        "items": len(state),
        "byStatus": _tally([item["status"] for item in state.values()]),
        "bySensitivity": _tally([item["sensitivity"] for item in state.values()]),
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


# -- the rules the loader does not carry --------------------------------------


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


def _manifest_listing_violations(corpus: Corpus) -> list[str]:
    """What the manifest declares against what the directory holds.

    Filename order is the loader's ``migration-order``; both clauses here are
    outside its reach. It resolves each declared file under ``migrations/`` and
    never lists the directory, so a committed migration the manifest omits is
    invisible to it -- and no loader rule reads a filename against the
    document's own ``id``.
    """
    declared = [entry["file"] for entry in corpus.manifest["migrations"]]
    present = [name for name, _document in corpus.migrations]
    found = []
    if declared != present:
        found.append("manifest names a different set of files than the directory holds")
    found += [
        f"{name}: filename prefix is not the migration id"
        for name, document in corpus.migrations
        if not name.startswith(f"{document['id']}-")
    ]
    return found


def _visible_reference_violations(corpus: Corpus) -> list[str]:
    """A ``visible`` migration must not name an item only a ``withheld`` one creates.

    Not the loader's ``visible-depends-on-withheld``, which reads a migration's
    ``dependsOn`` against another migration's plane. This reads every key
    through which an *operation* names an item, and the two populations do not
    overlap: no fixture migration declares ``dependsOn`` at all.

    The reverse direction is allowed and used once: the withheld draft carries a
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


def _corpora_invariant_violations(corpus: Corpus) -> list[str]:
    return [
        query["id"]
        for query in corpus.queries["queries"]
        if query.get("enabled", True) and query.get("corpora") != ["full", "clean"]
    ]


def _uncreated_judged_item_violations(corpus: Corpus) -> list[str]:
    """Every judged itemId -- ``relevant`` or ``forbidden`` -- is one a
    ``createItem`` names.

    Stronger than the loader's ``relevant-item-unknown`` on two axes, both
    measured and pinned in the last section: that rule reads ``relevant`` alone,
    and it keys on any operation's ``itemId`` rather than on ``createItem``.
    """
    creators = _creating_plane(corpus)
    return [
        f"{entry['queryId']}: no migration creates {name}"
        for entry in corpus.judgements["judgements"]
        for name in sorted((_ids(entry, "relevant") | _ids(entry, "forbidden")) - set(creators))
    ]


def _census_violations(corpus: Corpus) -> list[str]:
    """The manifest census against the migrations, with no build.

    ``corpus_build.py``'s ``census-mismatch`` asks the same question of a real
    ``index build``, which unit weight cannot reach; ``load_corpus`` asks it of
    nothing at all -- it parses ``census`` into the manifest and never compares
    it.
    """
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


def _coverage_split_violations(
    coverage: tuple[harness_corpus.WithheldItemCoverage, ...],
) -> list[str]:
    """Compare the loader's own coverage classification against the pinned split.

    ``gate`` is a member the ``--include-unapproved`` build indexes and a default
    query refuses; ``census`` is a member excluded before the index, by status or
    by the serving ceiling. Which is which is ``load_corpus``'s to decide
    (``_withheld_item_coverage``); what is pinned here is *who lands where*,
    which nothing in the fixture declares.

    The two are complementary by construction -- ``is_gate_tested`` is one
    boolean -- so disjointness is asserted nowhere. A member in neither class --
    final status in ``DEFAULT_SURFACEABLE_STATUSES`` and within the ceiling, the
    loader's own predicate rather than a status spelled here -- is the state the
    loader refuses outright with ``withheld-item-disclosable``, and the twin for
    that refusal is in the converged section above.
    """
    gate = {item.item_id for item in coverage if item.is_gate_tested}
    census = {item.item_id for item in coverage if not item.is_gate_tested}
    found = []
    if gate != GATE_TESTED_ITEMS:
        found.append(f"gate-tested: derived {sorted(gate)}, pinned {sorted(GATE_TESTED_ITEMS)}")
    if census != CENSUS_TESTED_ITEMS:
        found.append(
            f"census-tested: derived {sorted(census)}, pinned {sorted(CENSUS_TESTED_ITEMS)}"
        )
    return found


# -- the corpus as committed satisfies every rule the loader holds ------------


def test_the_committed_corpus_loads_through_the_loader_that_owns_its_rules(
    loaded: harness_corpus.Corpus,
) -> None:
    """The positive half of every rule #794 converged onto ``load_corpus``.

    Schema validation, migration order and its topology, query and judgement
    identity and coverage, disjointness, non-emptiness, evidence subsumption,
    relevant-item existence and retrievability, and the withheld-plane
    disclosability rule are all one refusal away from this call: it raises on
    the first rule violated. Asserting each separately here would assert the
    same call fourteen times.
    """
    assert len(loaded.queries) == len(CORPUS.queries["queries"])
    assert len(loaded.judgements) == len(CORPUS.judgements["judgements"])


def test_an_unperturbed_copy_of_the_corpus_still_loads(tmp_path: Path) -> None:
    """The control every twin below rests on: a refusal must come from the
    perturbation, not from how ``_written`` serialises the corpus. Without this,
    a serialiser that produced nonsense would make every twin pass.
    """
    committed = harness_corpus.load_corpus(CORPUS_ROOT)

    written = _accepted(CORPUS, tmp_path)

    assert written.manifest == committed.manifest
    assert written.queries == committed.queries
    assert written.withheld_coverage == committed.withheld_coverage


def test_a_copy_written_where_another_stood_holds_only_its_own_migrations(
    tmp_path: Path,
) -> None:
    """The other half of that control: the tree is the Corpus, not the union.

    ``_written`` used to write over whatever ``root`` already held. No twin
    removes a migration today, so nothing was misreporting -- but the first one
    to try would have been handed back the file it removed, and would have run
    its rule against a corpus nobody constructed.
    """
    reduced = replace(CORPUS, migrations=CORPUS.migrations[:-1])

    _written(CORPUS, tmp_path)
    root = _written(reduced, tmp_path)

    assert sorted(path.name for path in (root / "migrations").iterdir()) == [
        name for name, _document in reduced.migrations
    ]


def test_each_withheld_member_ends_in_the_state_its_coverage_follows_from(
    loaded: harness_corpus.Corpus,
) -> None:
    """The seven states the split below is derived from, said rather than implied.

    Two are gated at query time, three are retired by status and two sit above
    the serving ceiling -- and the last two of those seven reach their state only
    through ``deprecateItem`` and ``changeSensitivity`` in a later migration, so
    a loader fold that folded revisions alone would report them approved and
    internal.
    """
    states = {
        item.item_id: (item.final_status, item.final_sensitivity)
        for item in loaded.withheld_coverage
    }

    assert states == WITHHELD_FINAL_STATES


def test_the_withheld_plane_splits_into_gate_tested_and_census_tested_members(
    loaded: harness_corpus.Corpus,
) -> None:
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
    assert _coverage_split_violations(loaded.withheld_coverage) == []


def test_the_disabled_historical_query_judges_superseded_items_and_the_corpus_loads(
    loaded: harness_corpus.Corpus,
) -> None:
    """The rider-b exemption, exercised rather than asserted (#793, #794).

    ``q-hist-ttl-evolution`` asks how the TTL policy changed over time, so the
    superseded chain is its answer and not its error. It is disabled, the
    harness skips it (Phase D), and the loader's enabled scoping is what lets
    the judgement stay committed instead of being deleted to satisfy a rule it
    was never in. This file used to apply its own copy of that rule to every
    judgement, unscoped, and the two disagreed until #794.

    The first assertion is the exemption's positive control: if no relevant item
    of that query were unretrievable, the corpus loading would say nothing about
    the scoping.
    """
    documents = dict(CORPUS.migrations)
    status, _sensitivity = harness_corpus._final_status_and_sensitivity(
        loaded.manifest, documents, harness_corpus._all_item_ids(loaded.manifest, documents)
    )
    judgement = loaded.judgement_for("q-hist-ttl-evolution")
    assert judgement is not None

    unretrievable = {
        item.item_id
        for item in judgement.relevant
        if status[item.item_id] not in harness_corpus.DEFAULT_SURFACEABLE_STATUSES
    }

    assert unretrievable == {"domain.session-token-ttl-v1", "domain.session-token-ttl-v2"}


def test_the_loader_admits_a_disabled_querys_judgement_of_a_withheld_item(
    tmp_path: Path,
) -> None:
    """The #794 divergence itself, resolved by consumption.

    A disabled query judging a withheld-plane item passes ``load_corpus`` and
    failed this file's own copy of the plane rule, which applied it to every
    judgement. The copy is gone; the loader's scoping is what this corpus is now
    held to, and this is the shape that used to separate them.
    """
    judgements = copy.deepcopy(CORPUS.judgements)
    historical = next(
        entry for entry in judgements["judgements"] if entry["queryId"] == "q-hist-ttl-evolution"
    )
    historical["relevant"].append({"itemId": "domain.rejected-credential-cache"})

    written = _accepted(replace(CORPUS, judgements=judgements), tmp_path)

    judgement = written.judgement_for("q-hist-ttl-evolution")
    assert judgement is not None
    assert harness_corpus.JudgedItem(item_id="domain.rejected-credential-cache") in (
        judgement.relevant
    )


# -- the corpus as committed satisfies every rule that stays here -------------


def test_every_content_sha256_matches_the_body_bytes_committed_beside_it() -> None:
    assert _content_hash_violations(CORPUS) == []


def test_no_migration_document_declares_a_plane() -> None:
    """ADR-0036 decision 6: the plane lives in the manifest and nowhere else.

    The migration schema's root carries ``additionalProperties: false``, so the
    loader already refuses a ``plane`` key here -- as a bare ``json_path``, by a
    rule that would report the same way for a typo. This one names the key, says
    where the plane belongs instead, and is what still holds the decision if
    that root is ever reopened. The two are pinned as agreeing in the last
    section.
    """
    assert _plane_key_violations(CORPUS) == []


def test_the_manifest_declares_exactly_the_migrations_the_directory_holds() -> None:
    assert _manifest_listing_violations(CORPUS) == []


def test_every_operation_is_one_this_modules_replay_folds_or_may_safely_skip() -> None:
    """``_replay`` is a four-operation fold, and nine more are safe to skip.

    The schema admits fourteen. ``restoreItem`` is the one in neither set: it
    sets ``approved``, so a corpus carrying one would have a census and a
    coverage split derived from a status the real apply contradicts.
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

    The docs link walk skips the whole prefix, ``_content_hash_violations`` keys
    off ``contentFile`` and the loader never opens one at all, so an orphan body
    would be committed, shipped and read by no rule. The other two directions
    matter for the same reason in reverse: a ``contentFile`` naming nothing
    breaks the apply, and two migrations naming one body make a single edit move
    two revisions.
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


def test_no_visible_migration_references_an_item_only_a_withheld_one_creates() -> None:
    """The ``clean`` build applies the visible plane alone; a visible migration
    naming a withheld item would make that build fail outright, which is a
    corpus defect and not a retrieval measurement.
    """
    assert _visible_reference_violations(CORPUS) == []


def test_every_enabled_query_runs_against_both_corpora() -> None:
    """A query that ran against ``full`` alone could not show that the withheld
    plane moved nothing, so the disclosure-equality form ADR-0036 decision 6
    fixes has no exceptions to enumerate.
    """
    assert _corpora_invariant_violations(CORPUS) == []


def test_every_judged_item_is_one_a_create_item_operation_names() -> None:
    assert _uncreated_judged_item_violations(CORPUS) == []


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


# -- each converged rule, driven RED through the loader -----------------------


def _restated(item: str, **fields: str) -> Corpus:
    """A copy whose every revision of ``item`` carries different metadata."""
    migrations = copy.deepcopy(list(CORPUS.migrations))
    for _name, document in migrations:
        for operation in document["operations"]:
            if operation["op"] == "upsertRevision" and operation["itemId"] == item:
                operation["metadata"].update(fields)
    return replace(CORPUS, migrations=tuple(migrations))


def _perturbed_migrations() -> list[tuple[str, dict[str, Any]]]:
    return copy.deepcopy(list(CORPUS.migrations))


def _revisions(migrations: list[tuple[str, dict[str, Any]]]) -> list[dict[str, Any]]:
    return [
        operation
        for _name, document in migrations
        for operation in document["operations"]
        if operation["op"] == "upsertRevision"
    ]


def test_the_loader_refuses_a_query_id_the_schema_pattern_forbids(tmp_path: Path) -> None:
    queries = copy.deepcopy(CORPUS.queries)
    queries["queries"][0]["id"] = "Q-Uppercase"

    assert _refusal(replace(CORPUS, queries=queries), tmp_path).rule == "schema:queries.yaml"


def test_the_loader_refuses_a_migration_missing_its_api_version(tmp_path: Path) -> None:
    migrations = _perturbed_migrations()
    name, document = migrations[0]
    del document["apiVersion"]

    refusal = _refusal(replace(CORPUS, migrations=tuple(migrations)), tmp_path)

    assert refusal.rule == f"schema:migrations/{name}"


def test_the_loader_refuses_a_manifest_declaring_a_migration_the_directory_lacks(
    tmp_path: Path,
) -> None:
    """``file-readable`` had no driving test anywhere in the repository (#800):
    the tag's only other hit is a tag-enumeration pin in
    ``test_adr36_ratchet.py`` that proves the tag exists, not that the rule
    fires. ``_written`` writes only the migrations ``corpus.migrations``
    names, so a manifest entry with no matching tuple member never lands on
    disk.
    """
    manifest = copy.deepcopy(CORPUS.manifest)
    manifest["migrations"].append(
        {"file": "01M9EV0000000000000000M999-ghost.yaml", "plane": "visible"}
    )

    refusal = _refusal(replace(CORPUS, manifest=manifest), tmp_path)

    assert refusal.rule == "file-readable"


def test_the_loader_refuses_a_missing_contract_file(tmp_path: Path) -> None:
    """``file-readable`` has a second raise site, ``_load_yaml`` -- the one
    that reads the corpus's own three contract files -- distinct from
    ``_load_migration_document`` above. Written whole with ``_written``, then
    one file removed from disk, so this drives that site rather than the one
    the twin above already does.
    """
    _written(CORPUS, tmp_path)
    (tmp_path / "queries.yaml").unlink()

    with pytest.raises(harness_corpus.CorpusError) as excinfo:
        harness_corpus.load_corpus(tmp_path)

    assert excinfo.value.rule == "file-readable"


def test_the_loader_refuses_a_manifest_listing_two_migrations_out_of_order(
    tmp_path: Path,
) -> None:
    manifest = copy.deepcopy(CORPUS.manifest)
    manifest["migrations"][0], manifest["migrations"][1] = (
        manifest["migrations"][1],
        manifest["migrations"][0],
    )

    assert _refusal(replace(CORPUS, manifest=manifest), tmp_path).rule == "migration-order"


def test_the_loader_refuses_a_duplicated_query_id(tmp_path: Path) -> None:
    queries = copy.deepcopy(CORPUS.queries)
    queries["queries"].append(copy.deepcopy(queries["queries"][0]))

    assert _refusal(replace(CORPUS, queries=queries), tmp_path).rule == "duplicate-query-id"


def test_the_loader_refuses_an_enabled_query_nobody_judged(tmp_path: Path) -> None:
    judgements = copy.deepcopy(CORPUS.judgements)
    del judgements["judgements"][0]

    refusal = _refusal(replace(CORPUS, judgements=judgements), tmp_path)

    assert refusal.rule == "query-missing-judgement"


def test_the_loader_refuses_a_judgement_naming_no_declared_query(tmp_path: Path) -> None:
    judgements = copy.deepcopy(CORPUS.judgements)
    judgements["judgements"][0]["queryId"] = "q-never-declared"

    refusal = _refusal(replace(CORPUS, judgements=judgements), tmp_path)

    assert refusal.rule == "judgement-unknown-query"


def test_the_loader_refuses_one_query_judged_twice(tmp_path: Path) -> None:
    judgements = copy.deepcopy(CORPUS.judgements)
    judgements["judgements"].append(copy.deepcopy(judgements["judgements"][0]))

    refusal = _refusal(replace(CORPUS, judgements=judgements), tmp_path)

    assert refusal.rule == "duplicate-judgement-query-id"


def test_the_loader_refuses_an_item_both_required_and_forbidden(tmp_path: Path) -> None:
    judgements = copy.deepcopy(CORPUS.judgements)
    entry = judgements["judgements"][0]
    entry["forbidden"] = [copy.deepcopy(entry["relevant"][0])]

    refusal = _refusal(replace(CORPUS, judgements=judgements), tmp_path)

    assert refusal.rule == "relevant-forbidden-overlap"


def test_the_loader_refuses_an_entry_carrying_only_a_query_id(tmp_path: Path) -> None:
    """``judgementEntry`` requires ``queryId`` alone, so an entry carrying none
    of the four judging fields validates while asserting nothing (ADR-0036,
    Compliance).
    """
    judgements = copy.deepcopy(CORPUS.judgements)
    judgements["judgements"][0] = {"queryId": judgements["judgements"][0]["queryId"]}

    assert _refusal(replace(CORPUS, judgements=judgements), tmp_path).rule == "empty-judgement"


@pytest.mark.parametrize(
    ("extra", "expected"),
    [
        pytest.param(
            {"sourceUri": "https://github.com/theurian/theurian.git"},
            "evidence-subsumption",
            id="subsumes",
        ),
        pytest.param(
            {
                "sourceUri": "https://github.com/theurian/theurian.git",
                "filePath": "docs/adr/0006-immutable-revisions-and-optimistic-concurrency.md",
            },
            "schema:judgements.yaml",
            id="duplicate-pair",
        ),
    ],
)
def test_the_loader_refuses_an_entry_that_scores_one_anchor_twice(
    tmp_path: Path, extra: dict[str, str], expected: str
) -> None:
    """``filePath`` narrows a ``sourceUri``, so ``(u, absent)`` standing beside
    ``(u, f)`` lets one cited anchor satisfy both entries and score twice,
    inflating evidence precision (ADR-0036, Compliance). The byte-identical
    second entry is refused a layer earlier, by ``uniqueItems`` on the
    judgements schema's own ``evidence`` array -- which is why the two cases
    expect different rule tags.
    """
    judgements = copy.deepcopy(CORPUS.judgements)
    entry = next(e for e in judgements["judgements"] if e["queryId"] == "q-optimistic-concurrency")
    entry["evidence"].append(extra)

    assert _refusal(replace(CORPUS, judgements=judgements), tmp_path).rule == expected


def test_the_loader_refuses_a_relevant_item_no_migration_names(tmp_path: Path) -> None:
    judgements = copy.deepcopy(CORPUS.judgements)
    judgements["judgements"][0]["relevant"][0]["itemId"] = "architecture.never-created"

    refusal = _refusal(replace(CORPUS, judgements=judgements), tmp_path)

    assert refusal.rule == "relevant-item-unknown"


def test_the_loader_refuses_a_withheld_item_promoted_into_relevant(tmp_path: Path) -> None:
    """A ``relevant`` withheld item would ask the ``clean`` corpus to return a
    document it never held, so every equality query would score a miss that is
    not a retrieval defect. A withheld item may still be named in ``forbidden``.

    The message, not the tag: all three retrievability clauses raise
    ``relevant-item-unretrievable``, and every withheld member of a *loadable*
    corpus is either unapproved or above the ceiling -- that is what
    ``withheld-item-disclosable`` guarantees -- so the status clause would catch
    this perturbation too. Measured: with the plane clause deleted this test
    still passed on the tag alone.
    """
    judgements = copy.deepcopy(CORPUS.judgements)
    judgements["judgements"][0]["relevant"][0]["itemId"] = "domain.rejected-credential-cache"

    refusal = _refusal(replace(CORPUS, judgements=judgements), tmp_path)

    assert refusal.rule == "relevant-item-unretrievable"
    assert "is withheld-plane" in str(refusal)


def test_the_loader_refuses_an_enabled_query_judging_a_superseded_item(tmp_path: Path) -> None:
    judgements = copy.deepcopy(CORPUS.judgements)
    entry = next(e for e in judgements["judgements"] if e["queryId"] == "q-sqlite-derived")
    entry["relevant"][0]["itemId"] = "domain.session-token-ttl-v1"

    refusal = _refusal(replace(CORPUS, judgements=judgements), tmp_path)

    assert refusal.rule == "relevant-item-unretrievable"
    assert "superseded" in str(refusal)


def test_the_loader_refuses_a_relevant_item_raised_above_the_ceiling(tmp_path: Path) -> None:
    """The axis the status conjunct alone does not reach.

    Reclassifying a visible, approved item to ``confidential`` leaves it
    ``approved`` and visible-plane, so the plane clause, the status clause and
    the withheld-member rules all stay green -- and every enabled query naming
    it scores Recall@k of zero against both corpora for good.
    """
    refusal = _refusal(
        _restated("architecture.sqlite-is-a-derived-artifact", sensitivity="confidential"),
        tmp_path,
    )

    assert refusal.rule == "relevant-item-unretrievable"
    assert "confidential" in str(refusal)


def test_the_loader_refuses_the_historical_judgement_once_its_query_is_enabled(
    tmp_path: Path,
) -> None:
    """The rider-b exemption is scoped to ``enabled: false``, not a blanket skip.

    The committed judgement that loads while ``q-hist-ttl-evolution`` is
    disabled must be refused the moment it is enabled, or the scoping widened
    into no check at all. ``queries.schema.json`` refuses a ``historical`` query
    with ``enabled: true``, so the control moves the class to ``unknown``, which
    carries no such constraint; the judgement and its superseded items are the
    committed ones.
    """
    queries = copy.deepcopy(CORPUS.queries)
    historical = next(q for q in queries["queries"] if q["id"] == "q-hist-ttl-evolution")
    historical["class"] = "unknown"
    historical["enabled"] = True

    refusal = _refusal(replace(CORPUS, queries=queries), tmp_path)

    assert refusal.rule == "relevant-item-unretrievable"
    assert "superseded" in str(refusal)


@pytest.mark.parametrize(
    ("item", "fields"),
    [
        pytest.param("security.draft-scan-hardening", {"status": "approved"}, id="draft-approved"),
        pytest.param(
            "security.confidential-token-rotation",
            {"sensitivity": "internal"},
            id="lowered-into-the-ceiling",
        ),
    ],
)
def test_the_loader_refuses_a_withheld_member_a_default_response_may_hold(
    tmp_path: Path, item: str, fields: dict[str, str]
) -> None:
    """The invariant the whole two-corpus design rests on (ADR-0036 decision 6).

    A withheld member that ended approved and within the serving ceiling would
    be written into ``full`` and refused by nothing at query time, so ``full``
    would return a row ``clean`` never held and every equality query reaching it
    would fail by construction -- a corpus defect read as a retrieval defect. It
    is also what keeps the coverage split exhaustive: such a member is in
    neither class, and the loader refuses it rather than labelling it.
    """
    refusal = _refusal(_restated(item, **fields), tmp_path)

    assert refusal.rule == "withheld-item-disclosable"


def test_the_split_pin_catches_a_member_changing_side_without_becoming_disclosable(
    tmp_path: Path,
) -> None:
    """What the loader accepts and only the pinned split refuses.

    A withheld draft restated ``rejected`` is still excluded from every index,
    so no loader rule fires -- but it has moved from the gate class to the
    census class, and the response-equality battery silently loses one of the
    two members that give it teeth.
    """
    written = _accepted(_restated("security.draft-scan-hardening", status="rejected"), tmp_path)

    gate = {item.item_id for item in written.withheld_coverage if item.is_gate_tested}

    assert gate == GATE_TESTED_ITEMS - {"security.draft-scan-hardening"}
    assert _coverage_split_violations(written.withheld_coverage) != []


# -- each rule that stays here, over a copy carrying its own defect -----------


def test_the_content_hash_rule_catches_a_body_edited_after_it_was_pinned() -> None:
    bodies = dict(CORPUS.bodies)
    edited = next(iter(bodies))
    bodies[edited] = bodies[edited] + b"one appended byte\n"

    assert _content_hash_violations(replace(CORPUS, bodies=bodies)) != []


def test_the_operation_rule_catches_a_restore_the_replay_folds_as_if_it_were_absent(
    tmp_path: Path,
) -> None:
    """Three facts, and the rule exists because of the first two.

    The schema admits ``restoreItem`` with nothing but an ``itemId``, so the
    loader accepts the perturbation below outright. ``_replay``'s ``case _``
    then drops it, and the fold goes on reporting ``deprecated`` for an item
    ``MigrationEngine`` leaves ``approved`` -- which would move this member out
    of the census class and into neither class, with no rule saying so.
    """
    migrations = _perturbed_migrations()
    deprecating = next(
        document
        for name, document in migrations
        if name.endswith("deprecate-flaky-quarantine.yaml")
    )
    deprecating["operations"].append(
        {"op": "restoreItem", "itemId": "testing.deprecated-flaky-quarantine"}
    )
    perturbed = replace(CORPUS, migrations=tuple(migrations))

    _accepted(perturbed, tmp_path)
    assert _replay(perturbed, {"visible", "withheld"})["testing.deprecated-flaky-quarantine"] == {
        "status": "deprecated",
        "sensitivity": "internal",
    }

    assert _unmodelled_operation_violations(perturbed) != []


def test_the_body_rule_catches_a_committed_body_no_migration_names() -> None:
    migrations = _perturbed_migrations()
    _name, document = migrations[0]
    document["operations"] = [
        operation for operation in document["operations"] if operation["op"] != "upsertRevision"
    ]

    assert _body_file_violations(replace(CORPUS, migrations=tuple(migrations))) != []


def test_the_body_rule_catches_a_content_file_naming_nothing_committed() -> None:
    migrations = _perturbed_migrations()
    _revisions(migrations)[0]["contentFile"] = "../knowledge/architecture/never-written.md"

    assert _body_file_violations(replace(CORPUS, migrations=tuple(migrations))) != []


def test_the_body_rule_catches_two_migrations_claiming_one_body() -> None:
    migrations = _perturbed_migrations()
    first, second = _revisions(migrations)[:2]
    second["contentFile"] = first["contentFile"]

    assert _body_file_violations(replace(CORPUS, migrations=tuple(migrations))) != []


def test_the_listing_rule_catches_a_migration_the_manifest_never_declares() -> None:
    """The loader resolves the declared list and never lists the directory, so
    this file is the only thing that reads the committed set against it.
    """
    manifest = copy.deepcopy(CORPUS.manifest)
    del manifest["migrations"][-1]

    assert _manifest_listing_violations(replace(CORPUS, manifest=manifest)) != []


def test_the_listing_rule_catches_a_filename_whose_prefix_is_not_the_migration_id() -> None:
    migrations = _perturbed_migrations()
    migrations[0][1]["id"] = "01M9EV000000000000000ZZZZZ"

    assert _manifest_listing_violations(replace(CORPUS, migrations=tuple(migrations))) != []


def test_the_visible_reference_rule_catches_a_visible_edge_onto_a_withheld_item() -> None:
    migrations = _perturbed_migrations()
    withheld = next(
        entry["file"] for entry in CORPUS.manifest["migrations"] if entry["plane"] == "withheld"
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

    perturbed = replace(CORPUS, migrations=tuple(migrations))

    assert _visible_reference_violations(perturbed) != []


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
    migrations = _perturbed_migrations()
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


def test_the_corpora_rule_catches_an_enabled_query_running_against_one_index() -> None:
    queries = copy.deepcopy(CORPUS.queries)
    queries["queries"][0]["corpora"] = ["full"]

    assert _corpora_invariant_violations(replace(CORPUS, queries=queries)) != []


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


# -- where this file is stronger than the loader, and the pins that say so ----
#
# Three rules above overlap the loader's semantics without consuming it, because
# each reaches something the loader does not. Every one carries a pin over an
# input on which a drifted pair would disagree -- the check #794 found missing,
# and the reason the drift it records went unnoticed.


def test_the_plane_key_rule_and_the_migration_schema_refuse_the_same_document(
    tmp_path: Path,
) -> None:
    """The equivalence the ``plane`` rule's authority statement rests on.

    Its value is that it survives ``additionalProperties`` being reopened on the
    migration schema's root; while that root stays closed, the loader must
    refuse the same document. Reopening it -- the drift this pin exists for --
    makes the second assertion fail while the first still holds.
    """
    migrations = _perturbed_migrations()
    name, document = migrations[0]
    document["plane"] = "withheld"
    perturbed = replace(CORPUS, migrations=tuple(migrations))

    assert _plane_key_violations(perturbed) == [name]
    assert _refusal(perturbed, tmp_path).rule == f"schema:migrations/{name}"


def test_the_existence_clause_and_the_loader_agree_on_a_relevant_item_named_nowhere(
    tmp_path: Path,
) -> None:
    """The axis ``_uncreated_judged_item_violations`` and ``relevant-item-unknown``
    share: a ``relevant`` itemId no operation anywhere names. A pair drifted on
    that axis -- either side stopping at the judgement's own file -- disagrees
    here.
    """
    judgements = copy.deepcopy(CORPUS.judgements)
    judgements["judgements"][0]["relevant"][0]["itemId"] = "architecture.never-created"
    perturbed = replace(CORPUS, judgements=judgements)

    assert _uncreated_judged_item_violations(perturbed) != []
    assert _refusal(perturbed, tmp_path).rule == "relevant-item-unknown"


@pytest.mark.parametrize("field", ["relevant", "forbidden"])
def test_the_existence_clause_keys_on_create_item_where_the_loader_keys_on_any_operation(
    tmp_path: Path, field: str
) -> None:
    """The first of two axes on which this file is deliberately stronger.

    ``_all_item_ids`` collects every operation's ``itemId``, so an item an
    ``upsertRevision`` names and no ``createItem`` creates satisfies the loader
    -- a shape the real ``migrate apply`` refuses, and one this corpus must not
    carry into a build. The ``forbidden`` parameter carries the second axis in
    the same act: ``relevant-item-unknown`` reads ``relevant`` alone.
    """
    judgements = copy.deepcopy(CORPUS.judgements)
    entry = next(e for e in judgements["judgements"] if e.get(field))
    orphan = entry[field][0]["itemId"]
    migrations = _perturbed_migrations()
    for _name, document in migrations:
        document["operations"] = [
            operation
            for operation in document["operations"]
            if not (operation["op"] == "createItem" and operation.get("itemId") == orphan)
        ]
    perturbed = replace(CORPUS, judgements=judgements, migrations=tuple(migrations))

    _accepted(perturbed, tmp_path)

    assert _uncreated_judged_item_violations(perturbed) != []


def _revision_metadata(corpus: Corpus, item: str) -> list[dict[str, Any]]:
    """Every ``upsertRevision`` metadata block naming ``item``, in migration order."""
    return [
        operation["metadata"]
        for _name, document in corpus.migrations
        for operation in document["operations"]
        if operation["op"] == "upsertRevision" and operation["itemId"] == item
    ]


@pytest.mark.parametrize(
    ("losing_fold", "witness"),
    sorted(FOLD_BRANCH_WITNESSES.items()),
    ids=sorted(FOLD_BRANCH_WITNESSES),
)
def test_the_committed_corpus_separates_every_fold_the_pin_below_would_otherwise_admit(
    losing_fold: str, witness: tuple[str, int]
) -> None:
    """The positive control for the fold-equivalence pin (#794, WatchDog ruling).

    Two folds compared only on inputs that cannot separate them agree for free.
    Each witness ends in a state the revision its drifted fold would report does
    not name, so that fold disagrees here rather than passing vacuously. A
    parameter going RED names which drift has stopped being detectable.
    """
    item, index = witness

    final = _replay(CORPUS, {"visible", "withheld"})[item]
    reported = _revision_metadata(CORPUS, item)[index]

    assert (final["status"], final["sensitivity"]) != (
        reported["status"],
        reported["sensitivity"],
    ), losing_fold


def test_the_suites_replay_and_the_loaders_fold_agree_on_every_item_the_corpus_creates() -> None:
    """The second axis on which this file keeps its own implementation (#794).

    ``_replay`` takes a plane set and ``_final_status_and_sensitivity`` does not,
    and the ``clean`` census needs one -- so the census cannot consume the
    loader's fold. What it can do is agree with it over the whole population,
    which is what the test above makes a non-trivial claim. Dict equality
    compares key sets too: an item a ``createItem`` names and no revision ever
    reaches is in ``_replay``'s answer and in neither of the loader's, and would
    redden here rather than being silently folded two different ways.
    """
    replayed = _replay(CORPUS, {"visible", "withheld"})

    status, sensitivity = _loader_fold(CORPUS.migrations)

    assert {item: state["status"] for item, state in replayed.items()} == status
    assert {item: state["sensitivity"] for item, state in replayed.items()} == sensitivity


def test_the_two_folds_agree_on_a_constructed_history_the_fixture_does_not_hold() -> None:
    """The same pin over an input built here rather than read off the fixture.

    Three items, one per input family the fold has to get right, because a
    frozen fixture can stop exercising a branch and this input cannot.

    ``domain.drifting`` takes two revisions, a ``deprecateItem`` and a
    ``changeSensitivity``, so every moving branch moves and the naive answers
    all differ from the agreed one. The other two carry the family this pin was
    missing until #799: ``sensitivity`` is optional on both operations that
    declare one, a document omitting it is accepted by
    ``migration.schema.json`` with zero errors, and on that input the loader
    folded to its default while ``_replay`` raised ``KeyError`` -- RED before
    the fix in the same commit, green after. The revision-side item is created
    ``public`` so the agreed answer is the default and not a value the document
    names: a ``_replay`` that carried the ``createItem`` sensitivity forward
    instead of defaulting would disagree here.

    The two halves read that default from two places -- the schema's declared
    ``default`` here, ``theurian.domain.migration.DEFAULT_SENSITIVITY`` in the
    loader -- so this also reddens if the contract and the implementation ever
    name different values.
    """
    migrations = (
        (
            "1M23BW8DJNKT2GJB31BMEYQP08-first.yaml",
            {
                "id": "1M23BW8DJNKT2GJB31BMEYQP08",
                "operations": [
                    {"op": "createItem", "itemId": "domain.drifting", "sensitivity": "public"},
                    {
                        "op": "upsertRevision",
                        "itemId": "domain.drifting",
                        "metadata": {"status": "approved", "sensitivity": "public"},
                    },
                    {
                        "op": "upsertRevision",
                        "itemId": "domain.drifting",
                        "metadata": {"status": "proposed", "sensitivity": "internal"},
                    },
                    {
                        "op": "createItem",
                        "itemId": "domain.revision-omits-sensitivity",
                        "sensitivity": "public",
                    },
                    {
                        "op": "upsertRevision",
                        "itemId": "domain.revision-omits-sensitivity",
                        "metadata": {"status": "approved"},
                    },
                    {"op": "createItem", "itemId": "domain.create-omits-sensitivity"},
                    {
                        "op": "upsertRevision",
                        "itemId": "domain.create-omits-sensitivity",
                        "metadata": {"status": "approved", "sensitivity": "restricted"},
                    },
                ],
            },
        ),
        (
            "2R8B5ZVNYVVS83VJW4JTPDGDA2-second.yaml",
            {
                "id": "2R8B5ZVNYVVS83VJW4JTPDGDA2",
                "operations": [
                    {"op": "deprecateItem", "itemId": "domain.drifting"},
                    {
                        "op": "changeSensitivity",
                        "itemId": "domain.drifting",
                        "sensitivity": "confidential",
                    },
                ],
            },
        ),
    )
    entries = [{"file": name, "plane": "visible"} for name, _document in migrations]
    suite_corpus = Corpus(
        manifest={"migrations": entries},
        queries={},
        judgements={},
        migrations=migrations,
        bodies={},
    )

    replayed = _replay(suite_corpus, {"visible"})
    status, sensitivity = _loader_fold(migrations)

    assert SCHEMA_DEFAULT_SENSITIVITY != "public", (
        "the revision-side item is created 'public' so the default is a different value; "
        "with the two equal, a fold carrying the createItem sensitivity forward passes below"
    )
    assert replayed == {
        "domain.drifting": {"status": "deprecated", "sensitivity": "confidential"},
        "domain.revision-omits-sensitivity": {
            "status": "approved",
            "sensitivity": SCHEMA_DEFAULT_SENSITIVITY,
        },
        "domain.create-omits-sensitivity": {"status": "approved", "sensitivity": "restricted"},
    }
    assert {item: state["status"] for item, state in replayed.items()} == status
    assert {item: state["sensitivity"] for item, state in replayed.items()} == sensitivity


def test_the_fold_credits_create_items_sensitivity_when_no_revision_overrides_it() -> None:
    """The gap #801 named: a createItem-only item's sensitivity was invisible to
    the fold, which then fell back to the schema default at every call site
    instead of what createItem declared -- masked in the committed corpus
    because every fixture item also carries a revision. Matches the real
    engine: ``KnowledgeItem.sensitivity`` is set at create
    (``MigrationEngine._create_item``) and stays there until something else
    changes it; nothing else touches these two items.
    """
    migrations = (
        (
            "1M23BW8DJNKT2GJB31BMEYQP08-create-only.yaml",
            {
                "id": "1M23BW8DJNKT2GJB31BMEYQP08",
                "operations": [
                    {
                        "op": "createItem",
                        "itemId": "domain.create-only-confidential",
                        "sensitivity": "confidential",
                    },
                    {"op": "createItem", "itemId": "domain.create-only-default"},
                ],
            },
        ),
    )

    _status, sensitivity = _loader_fold(migrations)

    assert sensitivity["domain.create-only-confidential"] == "confidential"
    assert sensitivity["domain.create-only-default"] == SCHEMA_DEFAULT_SENSITIVITY
