"""Load and validate the Phase A fixture corpus (ADR-0036, docs/roadmap.md "Phase A").

Validates ``manifest.yaml``, ``queries.yaml`` and ``judgements.yaml`` against
the three frozen contract schemas under ``tools/eval/schemas/``, then enforces
the cross-file and within-document rules ADR-0036 assigns to this loader --
checks JSON Schema cannot express because they compare distinct documents, or
compare one array entry against another rather than against a fixed shape.

Every rule raises :class:`CorpusError` naming itself, so a refusal says which
rule fired rather than leaving a reader to infer it from a stack trace.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

import yaml
from jsonschema import Draft202012Validator

from theurian.application.authorization import DEFAULT_CEILING, ServingProfile
from theurian.domain.enums import Sensitivity
from theurian.domain.migration import DEFAULT_SENSITIVITY

SCHEMAS_DIR = Path(__file__).resolve().parent / "schemas"

#: The sensitivity ceiling every harness build serves under (build.py never
#: writes a serving-profile file, so `index build` always falls back to this
#: default) -- the axis ADR-0036's gate-vs-census derivation rule tests a
#: withheld item's sensitivity against.
BUILD_CEILING: Final = DEFAULT_CEILING
BUILD_CEILING_SENSITIVITIES: Final = ServingProfile(ceiling=BUILD_CEILING).visible_sensitivities

#: Statuses `index build --include-unapproved` admits to the index that a
#: default-flags query (`includeUnapproved=false`) still refuses to surface --
#: the query-time gate T-17a exists to test.
GATE_TESTED_STATUSES: Final = frozenset({"draft", "proposed"})


class CorpusError(Exception):
    """A corpus fails one loader rule.

    ``rule`` is a short, stable tag naming the rule that fired -- distinct from
    the JSON Schema validation errors, which are tagged ``schema:<file>``.
    """

    def __init__(self, rule: str, message: str) -> None:
        super().__init__(f"[{rule}] {message}")
        self.rule = rule


@dataclass(frozen=True, slots=True)
class MigrationEntry:
    file: str
    plane: str


@dataclass(frozen=True, slots=True)
class CorpusCensus:
    items: int
    by_status: dict[str, int]
    by_sensitivity: dict[str, int]
    chunks: int


@dataclass(frozen=True, slots=True)
class Manifest:
    contract_version: int
    corpus_id: str
    k_values: tuple[int, ...]
    migrations: tuple[MigrationEntry, ...]
    census: dict[str, CorpusCensus]
    description: str | None


@dataclass(frozen=True, slots=True)
class QueryEntry:
    id: str
    query_class: str
    query: str
    enabled: bool
    corpora: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class JudgedItem:
    item_id: str


@dataclass(frozen=True, slots=True)
class EvidenceRef:
    source_uri: str
    file_path: str | None


@dataclass(frozen=True, slots=True)
class JudgementEntry:
    query_id: str
    relevant: tuple[JudgedItem, ...]
    evidence: tuple[EvidenceRef, ...]
    forbidden: tuple[JudgedItem, ...]
    expect_abstention: bool


@dataclass(frozen=True, slots=True)
class WithheldItemCoverage:
    """One withheld-plane item's classification (ADR-0036, the gate-vs-census
    derivation rule -- amended there to the three-clause partition below; the
    amendment is landing on another PR and is cited here by ADR name only).

    ``is_gate_tested``: the item's final status is draft or proposed AND its
    final sensitivity sits within :data:`BUILD_CEILING_SENSITIVITIES` -- so it
    is admitted to the index by ``--include-unapproved`` and a default-flags
    query must not leak it through (the T-17a mechanism this harness measures).
    Otherwise the item is census-tested: its final status is superseded,
    rejected or deprecated, or its final sensitivity sits above the ceiling --
    either way it never reaches the index under either build flavor, so its
    absence from a response is a build-time property rather than evidence
    about the query-time gate. The third combination -- final status approved
    and within the ceiling -- is disclosable and never reaches this
    classification: :func:`_check_no_disclosable_withheld_item` refuses the
    corpus first.
    """

    item_id: str
    final_status: str
    final_sensitivity: str
    is_gate_tested: bool


@dataclass(frozen=True, slots=True)
class Corpus:
    root: Path
    manifest: Manifest
    queries: tuple[QueryEntry, ...]
    judgements: tuple[JudgementEntry, ...]
    withheld_coverage: tuple[WithheldItemCoverage, ...]

    def judgement_for(self, query_id: str) -> JudgementEntry | None:
        return next((j for j in self.judgements if j.query_id == query_id), None)

    def coverage_for(self, item_id: str) -> WithheldItemCoverage | None:
        return next((c for c in self.withheld_coverage if c.item_id == item_id), None)


def load_corpus(root: Path) -> Corpus:
    """Load, schema-validate and cross-check the fixture corpus rooted at ``root``.

    Raises :class:`CorpusError` on the first rule violated -- a schema failure,
    or one of the cross-file and within-document rules below.
    """
    manifest_raw = _load_yaml(root / "manifest.yaml")
    queries_raw = _load_yaml(root / "queries.yaml")
    judgements_raw = _load_yaml(root / "judgements.yaml")

    _validate("manifest.yaml", "manifest.schema.json", manifest_raw)
    _validate("queries.yaml", "queries.schema.json", queries_raw)
    _validate("judgements.yaml", "judgements.schema.json", judgements_raw)

    manifest = _parse_manifest(manifest_raw)
    queries = _parse_queries(queries_raw)
    judgements = _parse_judgements(judgements_raw)
    documents = {
        entry.file: _load_yaml(root / "migrations" / entry.file) for entry in manifest.migrations
    }

    _check_migration_order(manifest)
    _check_no_visible_depends_on_withheld(manifest, documents)
    _check_unique_query_ids(queries)
    _check_unique_judgement_query_ids(judgements)
    _check_judgements_name_declared_queries(queries, judgements)
    _check_every_enabled_query_is_judged(queries, judgements)
    _check_relevant_forbidden_disjoint(judgements)
    _check_judgement_not_empty(judgements)
    _check_no_evidence_subsumption(judgements)
    _check_no_disclosable_withheld_item(manifest, documents)

    withheld_coverage = _withheld_item_coverage(manifest, documents)
    return Corpus(
        root=root,
        manifest=manifest,
        queries=queries,
        judgements=judgements,
        withheld_coverage=withheld_coverage,
    )


def _schema(name: str) -> dict[str, Any]:
    loaded: dict[str, Any] = json.loads((SCHEMAS_DIR / name).read_text(encoding="utf-8"))
    return loaded


def _load_yaml(path: Path) -> Any:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise CorpusError("file-readable", f"{path} could not be read: {exc}") from exc
    return yaml.safe_load(text)


def _validate(name: str, schema_file: str, instance: Any) -> None:
    validator = Draft202012Validator(_schema(schema_file))
    errors = sorted(
        validator.iter_errors(instance), key=lambda e: [str(p) for p in e.absolute_path]
    )
    if errors:
        first = errors[0]
        location = "/".join(str(p) for p in first.absolute_path) or "<root>"
        raise CorpusError(f"schema:{name}", f"{location}: {first.message}")


def _parse_manifest(raw: dict[str, Any]) -> Manifest:
    migrations = tuple(MigrationEntry(file=m["file"], plane=m["plane"]) for m in raw["migrations"])
    census = {name: _parse_census(value) for name, value in raw["census"].items()}
    return Manifest(
        contract_version=raw["contractVersion"],
        corpus_id=raw["corpusId"],
        k_values=tuple(raw["kValues"]),
        migrations=migrations,
        census=census,
        description=raw.get("description"),
    )


def _parse_census(raw: dict[str, Any]) -> CorpusCensus:
    return CorpusCensus(
        items=raw["items"],
        by_status=dict(raw.get("byStatus", {})),
        by_sensitivity=dict(raw.get("bySensitivity", {})),
        chunks=raw["chunks"],
    )


def _parse_queries(raw: dict[str, Any]) -> tuple[QueryEntry, ...]:
    return tuple(
        QueryEntry(
            id=q["id"],
            query_class=q["class"],
            query=q["query"],
            enabled=q.get("enabled", True),
            corpora=tuple(q.get("corpora", ["full"])),
        )
        for q in raw["queries"]
    )


def _parse_judgements(raw: dict[str, Any]) -> tuple[JudgementEntry, ...]:
    return tuple(
        JudgementEntry(
            query_id=j["queryId"],
            relevant=tuple(JudgedItem(item_id=i["itemId"]) for i in j.get("relevant", [])),
            evidence=tuple(
                EvidenceRef(source_uri=e["sourceUri"], file_path=e.get("filePath"))
                for e in j.get("evidence", [])
            ),
            forbidden=tuple(JudgedItem(item_id=i["itemId"]) for i in j.get("forbidden", [])),
            expect_abstention=j.get("expectAbstention", False),
        )
        for j in raw["judgements"]
    )


def _check_migration_order(manifest: Manifest) -> None:
    """The manifest lists migrations in filename order (ADR-0036 decision 6).

    A migration filename opens with a ULID, which sorts lexicographically in
    creation order -- so the manifest's own declared order is a self-check
    against its filenames, with no directory read required.
    """
    files = [entry.file for entry in manifest.migrations]
    ordered = sorted(files)
    if files != ordered:
        raise CorpusError(
            "migration-order",
            f"manifest.yaml lists migrations out of filename order: {files} (expected {ordered})",
        )


def _check_no_visible_depends_on_withheld(manifest: Manifest, documents: dict[str, Any]) -> None:
    """No ``visible``-plane migration's ``dependsOn`` names a ``withheld`` one.

    A visible migration applied on its own -- the ``clean`` build never applies
    a withheld one -- would otherwise depend on a migration that is not there.
    """
    plane_by_id = {documents[entry.file]["id"]: entry.plane for entry in manifest.migrations}
    for entry in manifest.migrations:
        if entry.plane != "visible":
            continue
        for dependency in documents[entry.file].get("dependsOn", []):
            if plane_by_id.get(dependency) == "withheld":
                raise CorpusError(
                    "visible-depends-on-withheld",
                    f"{entry.file} (plane=visible) depends on {dependency!r}, "
                    f"which is plane=withheld",
                )


def _check_unique_query_ids(queries: tuple[QueryEntry, ...]) -> None:
    seen: set[str] = set()
    for query in queries:
        if query.id in seen:
            raise CorpusError(
                "duplicate-query-id", f"queries.yaml declares {query.id!r} more than once"
            )
        seen.add(query.id)


def _check_unique_judgement_query_ids(judgements: tuple[JudgementEntry, ...]) -> None:
    seen: set[str] = set()
    for judgement in judgements:
        if judgement.query_id in seen:
            raise CorpusError(
                "duplicate-judgement-query-id",
                f"judgements.yaml judges {judgement.query_id!r} more than once",
            )
        seen.add(judgement.query_id)


def _check_judgements_name_declared_queries(
    queries: tuple[QueryEntry, ...], judgements: tuple[JudgementEntry, ...]
) -> None:
    declared = {query.id for query in queries}
    for judgement in judgements:
        if judgement.query_id not in declared:
            raise CorpusError(
                "judgement-unknown-query",
                f"judgements.yaml judges {judgement.query_id!r}, which queries.yaml "
                f"does not declare",
            )


def _check_every_enabled_query_is_judged(
    queries: tuple[QueryEntry, ...], judgements: tuple[JudgementEntry, ...]
) -> None:
    judged = {judgement.query_id for judgement in judgements}
    for query in queries:
        if query.enabled and query.id not in judged:
            raise CorpusError(
                "query-missing-judgement",
                f"query {query.id!r} is enabled and has no entry in judgements.yaml",
            )


def _check_relevant_forbidden_disjoint(judgements: tuple[JudgementEntry, ...]) -> None:
    for judgement in judgements:
        relevant_ids = {item.item_id for item in judgement.relevant}
        forbidden_ids = {item.item_id for item in judgement.forbidden}
        overlap = relevant_ids & forbidden_ids
        if overlap:
            raise CorpusError(
                "relevant-forbidden-overlap",
                f"judgement for {judgement.query_id!r} lists {sorted(overlap)} in both "
                f"relevant and forbidden",
            )


def _check_judgement_not_empty(judgements: tuple[JudgementEntry, ...]) -> None:
    for judgement in judgements:
        if not (
            judgement.relevant
            or judgement.forbidden
            or judgement.evidence
            or judgement.expect_abstention
        ):
            raise CorpusError(
                "empty-judgement",
                f"judgement for {judgement.query_id!r} carries none of relevant, forbidden, "
                f"evidence or expectAbstention -- it judges nothing while validating",
            )


def _check_no_evidence_subsumption(judgements: tuple[JudgementEntry, ...]) -> None:
    """No evidence entry subsumes another within one judgement.

    JSON Schema's ``uniqueItems`` already refuses two byte-identical entries;
    what it cannot see is a plain ``sourceUri`` entry standing beside a
    ``(sourceUri, filePath)`` entry narrowing the same URI -- a hit citing that
    file would satisfy both, scoring one anchor twice (ADR-0036, "Still owed").
    """
    for judgement in judgements:
        plain = {ref.source_uri for ref in judgement.evidence if ref.file_path is None}
        narrowed = {ref.source_uri for ref in judgement.evidence if ref.file_path is not None}
        overlap = plain & narrowed
        if overlap:
            raise CorpusError(
                "evidence-subsumption",
                f"judgement for {judgement.query_id!r} lists {sorted(overlap)} both with and "
                f"without filePath, so a hit citing one file would satisfy both entries",
            )


def _withheld_item_ids(manifest: Manifest, documents: dict[str, Any]) -> set[str]:
    """Every item id named by an operation of a withheld-plane migration."""
    withheld_files = [entry.file for entry in manifest.migrations if entry.plane == "withheld"]
    ids: set[str] = set()
    for filename in withheld_files:
        for op in documents[filename].get("operations", []):
            item_id = op.get("itemId")
            if item_id is not None:
                ids.add(item_id)
    return ids


def _final_status_and_sensitivity(
    manifest: Manifest, documents: dict[str, Any], withheld_item_ids: set[str]
) -> tuple[dict[str, str], dict[str, str]]:
    """Each withheld item's final status and sensitivity, replayed in migration order.

    Final status is the last ``upsertRevision.metadata.status``, overridden by
    a later ``deprecateItem`` -> ``deprecated``. Final sensitivity is the last
    ``upsertRevision.metadata.sensitivity`` (default ``internal``, ADR-0027
    decision 1's own default when a revision omits it), overridden by a later
    ``changeSensitivity``.
    """
    status_by_item: dict[str, str] = {}
    sensitivity_by_item: dict[str, str] = {}
    for entry in manifest.migrations:
        for op in documents[entry.file].get("operations", []):
            item_id = op.get("itemId")
            if item_id not in withheld_item_ids:
                continue
            if op["op"] == "upsertRevision":
                metadata = op["metadata"]
                status_by_item[item_id] = metadata["status"]
                sensitivity_by_item[item_id] = metadata.get(
                    "sensitivity", DEFAULT_SENSITIVITY.value
                )
            elif op["op"] == "deprecateItem":
                status_by_item[item_id] = "deprecated"
            elif op["op"] == "changeSensitivity":
                sensitivity_by_item[item_id] = op["sensitivity"]
    return status_by_item, sensitivity_by_item


def _check_no_disclosable_withheld_item(manifest: Manifest, documents: dict[str, Any]) -> None:
    """No withheld-plane item may end up approved and within the build ceiling.

    ADR-0036's gate-vs-census derivation rule is a three-clause partition
    (the amendment landing on another PR, cited here by ADR name only):
    gate-tested iff final status in {draft, proposed} and within the ceiling;
    census-tested iff final status in {superseded, rejected, deprecated} or
    above the ceiling. The third combination -- approved and within the
    ceiling -- is excluded by no mechanism: the item is indexed and surfaced
    at default flags exactly like any other approved item, so calling it
    "census-tested" would be false. Refused here rather than given a third
    label, so the classification below only ever partitions cleanly.
    """
    withheld_item_ids = _withheld_item_ids(manifest, documents)
    status_by_item, sensitivity_by_item = _final_status_and_sensitivity(
        manifest, documents, withheld_item_ids
    )
    for item_id in sorted(withheld_item_ids):
        status = status_by_item.get(item_id, "draft")
        sensitivity = sensitivity_by_item.get(item_id, DEFAULT_SENSITIVITY.value)
        within_ceiling = Sensitivity(sensitivity) in BUILD_CEILING_SENSITIVITIES
        if status == "approved" and within_ceiling:
            raise CorpusError(
                "withheld-item-disclosable",
                f"withheld-plane item {item_id!r} has final status 'approved' and "
                f"final sensitivity {sensitivity!r}, within the build ceiling "
                f"({BUILD_CEILING.value!r}) -- nothing excludes it from either "
                f"build's index, so it is not a valid withheld-plane item. "
                f"Author it draft or proposed (gate-tested), or give it a "
                f"retired final status or a sensitivity above the ceiling "
                f"(census-tested).",
            )


def _withheld_item_coverage(
    manifest: Manifest, documents: dict[str, Any]
) -> tuple[WithheldItemCoverage, ...]:
    """Classify every withheld-plane item (ADR-0036, the gate-vs-census derivation rule).

    Called only after :func:`_check_no_disclosable_withheld_item` has refused
    the one combination this classification cannot honestly label, so every
    item here is either gate-tested or census-tested and never both.
    """
    withheld_item_ids = _withheld_item_ids(manifest, documents)
    status_by_item, sensitivity_by_item = _final_status_and_sensitivity(
        manifest, documents, withheld_item_ids
    )

    coverage = []
    for item_id in sorted(withheld_item_ids):
        status = status_by_item.get(item_id, "draft")
        sensitivity = sensitivity_by_item.get(item_id, DEFAULT_SENSITIVITY.value)
        within_ceiling = Sensitivity(sensitivity) in BUILD_CEILING_SENSITIVITIES
        coverage.append(
            WithheldItemCoverage(
                item_id=item_id,
                final_status=status,
                final_sensitivity=sensitivity,
                is_gate_tested=status in GATE_TESTED_STATUSES and within_ceiling,
            )
        )
    return tuple(coverage)
