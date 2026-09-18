"""FR-V5 over the **built** ingest pipeline, not over a naming convention.

*"Raw review ingestion must succeed even when candidate generation fails."* The
way ADR-0030 discharges that is structural rather than by a fallback: no model
is in the path at all, so there is nothing for a model failure to break. A
sentence saying so proves nothing, and neither does a module list somebody keeps
in step -- this walks the object graph
``cli/review_commands.build_ingest_service`` actually returns, in the shape
``tests/integration/test_mcp_tools.py::test_no_registered_tool_can_reach_a_canonical_write``
established for the canonical-write guarantee.

The traversal, its two halves and the blind spot they share are
``tests/model_free_walk.py``'s, shared with ADR-0033 decision 1's walk over the
**candidate** pipeline (``test_candidate_generation_is_model_free.py``). What
stays here is this pipeline's own seed, its own landmarks and its own controls.

**The blind spot, restated because it bounds *this* claim.** The walk reads the
names a function spells, so a provider obtained through ``getattr``, a factory
looked up in a table, or an import one frame deeper is invisible to it. What
bounds that here is the name half plus the fact that no module in the ingest
path imports a provider module at all; neither is a proof that a dynamic route
could not be written.

**The three positive controls are what make a clean result mean anything.** A
walk that reached nothing would report the same green. One plant per arm --
a class method, an injected closure, and a collaborator reached only through a
closure cell -- each asserted to turn this test RED.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Final

import pytest
from model_free_walk import MODEL_NAMES, import_closure, offenders, walk

from theurian.application.project_service import ProjectPaths
from theurian.application.review_ingest_service import ReviewIngestService
from theurian.cli.review_commands import build_ingest_service
from theurian.infrastructure.review_evidence import IngestionRun, ReviewEvidenceStore

pytestmark = pytest.mark.integration

REPOSITORY: Final = "acme/order-service"

RUN: Final = IngestionRun("01K1AAAAAA01234567890ABCDE", datetime(2026, 9, 7, 9, 0, tzinfo=UTC))


# -- the pipeline under test --------------------------------------------------


@pytest.fixture
def service(tmp_path: Path) -> ReviewIngestService:
    """The ingest pipeline exactly as ``theurian review ingest`` composes it.

    Built through the shipped factory rather than by hand: a walk over a graph
    the command does not build would prove nothing about the command. Nothing is
    run -- the constructors store paths -- so no process is spawned and nothing
    reaches the network.
    """
    root = tmp_path / "demo"
    (root / ".theurian").mkdir(parents=True)
    return build_ingest_service(
        ProjectPaths(root=root, knowledge_dir=root / ".theurian"),
        repository=REPOSITORY,
        run=RUN,
    )


def test_the_walk_reaches_the_pipeline_it_claims_to_inspect(
    service: ReviewIngestService,
) -> None:
    """The vacuity guard: a walk that reached nothing would report the same green.

    Named landmarks per arm, so a traversal that stopped at the service's own
    methods reddens here instead of reporting a clean graph it never entered.
    **The two arms are asserted separately because they cover different things**,
    which is the honest statement of this instrument's reach: the object walk
    enters a module only when the graph *holds* something defined there, so the
    gate -- reached through a module-global function the service calls -- is in
    the import closure and not in the walk's own module set.
    """
    seen = walk(service)

    # The walk answered 38 callables when this floor was chosen (2026-09-07, on
    # this branch); `seen.callables` below recomputes it on every run, so the
    # figure is context and the assertion is the claim. Deliberately not anchored
    # to a branch commit: a squash merge orphans one, and a citation that answers
    # `fatal: bad object` is worse than none. The floor is well below the
    # measurement rather than equal to it: this guards a walk that collapsed to
    # the service's own handful of methods, and a floor pinned to the exact
    # number would redden on every ordinary addition without saying anything.
    assert seen.callables >= 20, f"the walk reached only {seen.callables} callables"
    for landmark in ("screen_landing_candidates", "list_pull_requests", "_write_one"):
        assert landmark in seen.names, (
            f"the walk never reached {landmark!r}, so it is not inspecting the "
            f"pipeline this test is about"
        )
    for held in (
        "theurian.application.review_ingest_service",
        "theurian.infrastructure.github.review_provider",
        "theurian.infrastructure.review_evidence.store",
    ):
        assert held in seen.modules, f"the object walk never entered {held}"
    closure = import_closure(seen.modules)
    for named in (
        "theurian.application.review_landing_gate",
        "theurian.infrastructure.review_evidence.layout",
        "theurian.security.content_secrets",
    ):
        assert named in closure, f"the import closure never reached {named}"


def test_no_callable_in_the_built_pipeline_reaches_a_model(
    service: ReviewIngestService,
) -> None:
    """FR-V5, bytecode half: no embedding, summarization or reranking provider."""
    reached = walk(service).names & MODEL_NAMES

    assert not reached, (
        f"the built ingest pipeline reaches model vocabulary {sorted(reached)}. "
        f"FR-V5 holds because no model is in this path at all; a reference here "
        f"means raw ingestion can now be broken by candidate generation failing."
    )


def test_no_module_in_the_ingest_path_names_a_model(service: ReviewIngestService) -> None:
    """FR-V5, name half: derived from the walk, so the population cannot be curated."""
    seen = walk(service)
    assert seen.modules, "no module was derived from the walk, so this scans nothing"
    population = import_closure(seen.modules)
    assert "theurian.infrastructure.review_evidence.layout" in population, (
        "the import closure did not reach a module the walk only names, so this "
        "half is scanning the graph's objects rather than the ingest path"
    )

    named = {dotted: sorted(spelled) for dotted, spelled in offenders(population).items()}

    assert not named, (
        f"modules in the ingest path name model vocabulary: {named}. Either a "
        f"provider arrived on this path, or the vocabulary above has collided with "
        f"an ordinary name and needs narrowing -- say which in the same change."
    )


# -- the positive controls ----------------------------------------------------


def _reaches_a_model() -> object:
    """A plant: a function that names an embedding adapter and nothing else."""
    # Imported inside the body deliberately: the plant has to be a name this
    # function's own code object spells, which is what the walk reads.
    from theurian.infrastructure.embedding.hashing import HashingEmbedding

    return HashingEmbedding


def test_a_model_planted_on_a_service_method_reddens_the_walk(
    service: ReviewIngestService, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Control one: the class-method arm."""
    monkeypatch.setattr(type(service), "_probe", _reaches_a_model, raising=False)

    assert walk(service).names & MODEL_NAMES, (
        "a model planted on the service's own class was invisible to the walk, so "
        "the clean result above is a walk that inspects nothing"
    )


def _planted_lander(_records: object) -> tuple[str, ...]:
    """A lander that *spells* an embedding adapter. Never called; only walked.

    The name has to be in this function's own code object, not one call away: the
    walk reads names, never values, so a lander that delegated to
    :func:`_reaches_a_model` would plant nothing here -- measured, and it is why
    this second plant exists rather than reusing the first.
    """
    from theurian.infrastructure.embedding.hashing import HashingEmbedding

    return (HashingEmbedding.__name__,)


def test_a_model_planted_in_an_injected_closure_reddens_the_walk(
    service: ReviewIngestService,
) -> None:
    """Control two: the injected-callable arm, which is where the store arrives."""
    service._land = _planted_lander

    assert walk(service).names & MODEL_NAMES, (
        "a model planted in the injected lander was invisible to the walk, so the "
        "clean result above says nothing about what the composition root wires"
    )


def test_a_model_planted_on_a_collaborator_behind_a_cell_reddens_the_walk(
    service: ReviewIngestService, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Control three: the closure-cell arm.

    The evidence store is reachable only as the contents of a cell inside the
    lander. A walk that stopped at the closure's own code would miss a model on
    the store entirely, and would still report the two controls above as green.
    """
    monkeypatch.setattr(ReviewEvidenceStore, "_probe", _reaches_a_model, raising=False)

    assert walk(service).names & MODEL_NAMES, (
        "a model planted on the evidence store was invisible to the walk, so the "
        "traversal is not descending into closure cells"
    )
