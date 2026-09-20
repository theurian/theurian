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
from typing import Any

import yaml
from jsonschema import Draft202012Validator

SCHEMAS_DIR = Path(__file__).resolve().parent / "schemas"


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
class Corpus:
    root: Path
    manifest: Manifest
    queries: tuple[QueryEntry, ...]
    judgements: tuple[JudgementEntry, ...]

    def judgement_for(self, query_id: str) -> JudgementEntry | None:
        return next((j for j in self.judgements if j.query_id == query_id), None)


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

    _check_migration_order(manifest)
    _check_no_visible_depends_on_withheld(root, manifest)
    _check_unique_query_ids(queries)
    _check_unique_judgement_query_ids(judgements)
    _check_judgements_name_declared_queries(queries, judgements)
    _check_every_enabled_query_is_judged(queries, judgements)
    _check_relevant_forbidden_disjoint(judgements)
    _check_judgement_not_empty(judgements)
    _check_no_evidence_subsumption(judgements)

    return Corpus(root=root, manifest=manifest, queries=queries, judgements=judgements)


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


def _check_no_visible_depends_on_withheld(root: Path, manifest: Manifest) -> None:
    """No ``visible``-plane migration's ``dependsOn`` names a ``withheld`` one.

    A visible migration applied on its own -- the ``clean`` build never applies
    a withheld one -- would otherwise depend on a migration that is not there.
    """
    documents = {
        entry.file: _load_yaml(root / "migrations" / entry.file) for entry in manifest.migrations
    }
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
