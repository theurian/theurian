"""ADR-0033 decision 1 over the **built** candidate pipeline: Theurian runs no model.

*"Theurian runs no model; the calling agent authors the generalization."* That is
a universal, and ADR-0033's Compliance records that its authority was a test
which did not exist: owed is *a walk of the built candidate pipeline's object
graph in the shape of ``test_review_ingest_is_model_free.py``, with its
planted-model controls -- and with the same recorded bound*.

This is that walk. The instrument is ``tests/model_free_walk.py``, shared with
the ingest one rather than copied, so the five edge kinds a traversal needs
cannot be dropped from one and not the other.

The graph is **captured, not reconstructed**
--------------------------------------------
``build_ingest_service`` is a factory a test can call. The candidate pipeline has
none: ``mcp/tools.py``'s ``review_generate_knowledge_candidate`` constructs its
``CandidateGenerator`` inline, per call, out of four collaborators it builds on
the spot. Rebuilding that by hand here would walk a graph the tool does not
build, which is the thing the ingest module's own fixture docstring refuses to
do.

So :func:`generator` drives the **real tool over the real transport** against the
shipped corpus and keeps the instance the composition root wired, by wrapping
``CandidateGenerator.generate`` for the length of that one call. What is walked
afterwards is the object the tool used: the review-search store's bound
``relative_path_for``, the evidence reader behind the record-read lambda, the
``FixCommitCheck`` built on the project root, the draft-only proposal facade and
the clock.

The two seams this does not reach, and what covers each
-------------------------------------------------------
* **``theurian.mcp.tools`` is dropped from the name half's seed**, and it is the
  only exclusion. The record-read lambda is defined there, so the module enters
  the walk's module set; closing that module's imports pulls in
  ``retrieval_service``, ``mcp/search`` and the two index stores, which name an
  embedder **on purpose** -- they compose ``knowledge.search``, not this tool.
  Measured on this branch: the seeded closure is 69 modules with five naming
  model vocabulary, and 41 modules with none once the shared composition root is
  dropped. This is the same seam the ingest module records for
  ``cli/review_commands.py``: the scope asserted is the composed pipeline and
  the modules it is built from, not the surface it is registered in. What covers
  the lambda itself is the bytecode half, which reads its own code object and
  every name in it.
* **The dynamic route.** ``co_names`` sees the names a function spells, so a
  provider reached through ``getattr``, a factory looked up in a table, or an
  import one frame deeper than the walk descends is invisible. That bound is the
  ingest walk's too, and it is recorded rather than closed.

Marked ``integration``: a real git project, a real CLI-registered state, a real
evidence store, a real SQLite review-search store and one real ``tools/call``.
Writes only under pytest's temporary directories.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any, Final

import pytest
import review_candidate_fixtures as corpus
from model_free_walk import MODEL_NAMES, import_closure, offenders, walk
from review_candidate_project import served_project

from theurian.application.candidate_generation import CandidateGenerator, CandidateSubmission
from theurian.daemon.runner import build_server
from theurian.domain.review import FixCommitVerdict
from theurian.infrastructure.git.fix_commit_check import FixCommitCheck
from theurian.infrastructure.sqlite.review_search_store import SqliteReviewSearchStore

from mcp_wire_session import mcp_session  # isort: skip

pytestmark = pytest.mark.integration

TOOL: Final = "review.generateKnowledgeCandidate"

#: The shared MCP composition root, dropped from the name half's seed. The module
#: docstring carries the measurement and the reason; it is named once here so the
#: exclusion is a constant a reader can grep rather than a literal in a set
#: expression.
SHARED_COMPOSITION_ROOT: Final = "theurian.mcp.tools"


def _arguments() -> dict[str, Any]:
    return {
        "projectId": "demo",
        "repository": corpus.REPOSITORY,
        "recordKey": corpus.THREAD_SATISFYING,
        "itemId": "reliability.retry-lock-order",
        "title": "Acquire locks after reads in retry-eligible paths",
        "body": "Acquire locks after reads, never before, in retry-eligible paths.\n",
        "kind": "convention",
        "category": "reliability-rule",
        "owner": "platform-team",
        "author": "dana@example.com",
        "description": "Generalise the deadlock thread into a locking rule",
        "evidence": {
            "agentId": "claude-code",
            "taskId": "task-431",
            "model": "claude-opus-5",
            "reasoning": "The thread settled the lock ordering; this generalises it.",
        },
        "sourceAnchors": [
            {
                "provider": "github",
                "sourceUri": (
                    f"https://github.com/{corpus.REPOSITORY}/pull/"
                    f"{corpus.PULL_REQUEST_CI_PASSED}#discussion_r1"
                ),
                "repository": corpus.REPOSITORY,
                "filePath": corpus.FILE_PATH,
            }
        ],
    }


@pytest.fixture
def generator(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[CandidateGenerator]:
    """The ``CandidateGenerator`` the shipped tool built for one real call.

    Captured rather than composed: this pipeline has no factory a test can call,
    so the only way to walk what the tool wires is to let the tool wire it. The
    wrapper records ``self`` and delegates, so the call it is captured from is an
    ordinary successful generation -- the corpus and the withholding posture are
    stated at the ``served_project`` call below, as the wire module's are.

    The tool is driven once. A second drive would hand back a second, equal
    graph and cost another proposal on disk.

    The capture patch gets its **own** ``MonkeyPatch`` context rather than the
    fixture's, and that is not tidiness: ``undo()`` on the shared one would
    revert ``served_project``'s ``THEURIAN_DATA_DIR``, its pinned commit dates
    and its ``chdir`` along with the wrapper.
    """
    captured: list[CandidateGenerator] = []
    original = CandidateGenerator.generate

    def capturing(self: CandidateGenerator, submission: CandidateSubmission) -> Any:
        captured.append(self)
        return original(self, submission)

    for project in served_project(
        tmp_path, monkeypatch, records=corpus.evidence_records(), withheld=frozenset()
    ):
        with pytest.MonkeyPatch.context() as capture:
            capture.setattr(CandidateGenerator, "generate", capturing)
            with mcp_session(build_server(project.registry), tmp_path / "daemon") as call:
                answer = call(TOOL, {**_arguments(), "fixCommit": project.verifying})

        assert answer["result"]["isError"] is False, (
            f"the capture call did not generate a candidate, so the graph below is "
            f"whatever a refusal built rather than the pipeline: {answer}"
        )
        assert len(captured) == 1, f"expected one generator, captured {len(captured)}"
        yield captured[0]


def test_the_walk_reaches_the_candidate_pipeline_it_claims_to_inspect(
    generator: CandidateGenerator,
) -> None:
    """The vacuity guard: a walk that reached nothing would report the same green.

    Three arms, because each covers a different way the traversal could collapse.
    The **name** landmarks are attribute names reachable code objects really
    spell -- deliberately not ``relative_path_for`` or ``verify``, which arrive as
    *bound methods* stored on the generator and are therefore walked as objects
    without any reachable code object spelling them; asserting on those would be
    asserting the walk works the way a reader assumes rather than the way it
    does. The **module** landmarks are the four collaborators the composition
    root wires, so a walk that stopped at ``CandidateGenerator``'s own methods
    reddens. The **closure** landmark is a module the graph only names.
    """
    seen = walk(generator)

    # 156 callables when this floor was chosen (2026-09-19, on this branch);
    # `seen.callables` recomputes it every run, so the figure is context and the
    # assertion is the claim. The floor sits well below the measurement for the
    # ingest walk's reason: it guards a collapse to the generator's own handful
    # of methods, and a floor pinned to the exact number reddens on every
    # ordinary addition without saying anything.
    assert seen.callables >= 40, f"the walk reached only {seen.callables} callables"
    for landmark in ("_resolve_evidence_path", "_verify_fix_commit", "record_at", "draft"):
        assert landmark in seen.names, (
            f"the walk never reached {landmark!r}, so it is not inspecting the "
            f"pipeline this test is about"
        )
    for held in (
        "theurian.application.candidate_generation",
        "theurian.infrastructure.sqlite.review_search_store",
        "theurian.infrastructure.review_evidence.reader",
        "theurian.infrastructure.git.fix_commit_check",
        "theurian.application.proposal_service",
    ):
        assert held in seen.modules, f"the object walk never entered {held}"
    closure = import_closure(_seed(seen.modules))
    for named in ("theurian.domain.review", "theurian.infrastructure.review_evidence.layout"):
        assert named in closure, f"the import closure never reached {named}"


def _seed(modules: frozenset[str]) -> frozenset[str]:
    """The walk's modules, less the shared composition root.

    The module docstring records the measurement and the reason. Asserted rather
    than quietly subtracted: if the lambda ever stops being defined in
    ``mcp/tools.py``, the exclusion has become a subtraction of nothing and the
    reader should be told, not left with a set operation that looks deliberate.
    """
    assert SHARED_COMPOSITION_ROOT in modules, (
        f"{SHARED_COMPOSITION_ROOT} is no longer in the walk's module set, so this "
        f"exclusion removes nothing. Either the record-read lambda moved out of the "
        f"shared composition root -- in which case delete the exclusion and let the "
        f"name half cover the module -- or the walk stopped reaching it."
    )
    return frozenset(modules - {SHARED_COMPOSITION_ROOT})


def test_no_callable_in_the_built_candidate_pipeline_reaches_a_model(
    generator: CandidateGenerator,
) -> None:
    """ADR-0033 decision 1, bytecode half: no embedding, summarization or reranking.

    Theurian verifies the gate and packages the result; the generalization is the
    caller's. A provider reachable from this graph would make that sentence false
    on the surface that publishes it -- and ADR-0026's defect is a capability
    claim the product does not hold, in either direction.
    """
    reached = walk(generator).names & MODEL_NAMES

    assert not reached, (
        f"the built candidate pipeline reaches model vocabulary {sorted(reached)}. "
        f"ADR-0033 decision 1 says Theurian runs no model on this path: the caller "
        f"authors the generalization and this tool verifies and packages it."
    )


def test_no_module_the_candidate_pipeline_is_built_from_names_a_model(
    generator: CandidateGenerator,
) -> None:
    """ADR-0033 decision 1, name half: derived from the walk, not curated.

    The population is the walk's own modules closed under module-level imports,
    less the shared composition root the module docstring accounts for. Its floor
    is asserted first: a closure that had stopped reaching a module the graph only
    *names* would be scanning the objects rather than the path.
    """
    seen = walk(generator)
    assert seen.modules, "no module was derived from the walk, so this scans nothing"
    population = import_closure(_seed(seen.modules))
    assert "theurian.infrastructure.review_evidence.layout" in population, (
        "the import closure did not reach a module the walk only names, so this "
        "half is scanning the graph's objects rather than the candidate path"
    )

    named = {dotted: sorted(spelled) for dotted, spelled in offenders(population).items()}

    assert not named, (
        f"modules the candidate pipeline is built from name model vocabulary: "
        f"{named}. Either a provider arrived on this path, or the vocabulary has "
        f"collided with an ordinary name and needs narrowing -- say which in the "
        f"same change."
    )


def test_the_excluded_composition_root_is_what_the_exclusion_says_it_is(
    generator: CandidateGenerator,
) -> None:
    """The exclusion is a measurement, so it is measured rather than asserted in prose.

    Dropping ``theurian.mcp.tools`` from the seed is the one subtraction this
    module makes, and a subtraction is exactly how a scan comes to pass by not
    looking. Two directions: the unpruned closure really does name a model --
    otherwise the exclusion is unnecessary and should go -- and every name it
    brings in arrives through modules that compose ``knowledge.search`` rather
    than this tool, which is the reason the exclusion is admissible.

    If ``knowledge.search``'s composition ever stops naming an embedder, this
    reddens and the exclusion should be deleted rather than re-justified.
    """
    seen = walk(generator)
    unpruned = offenders(import_closure(seen.modules))
    pruned = offenders(import_closure(_seed(seen.modules)))

    assert unpruned, (
        "the seeded closure names no model vocabulary at all, so dropping the "
        "shared composition root subtracts nothing and the exclusion is dead "
        "weight -- delete it and let the name half range over the whole closure"
    )
    assert not pruned, f"the pruned closure still names model vocabulary: {pruned}"
    assert set(unpruned) <= {
        "theurian.application.retrieval_service",
        "theurian.domain.ports.index_store",
        "theurian.infrastructure.sqlite.index_forest",
        "theurian.infrastructure.sqlite.index_store",
        "theurian.mcp.search",
    }, (
        f"the exclusion now hides model vocabulary in a module that is not part of "
        f"`knowledge.search`'s retrieval composition: {sorted(unpruned)}. The "
        f"exclusion is admissible only while everything behind it is retrieval's; "
        f"a new module here is a provider arriving somewhere this module stopped "
        f"looking."
    )


# -- the positive controls ----------------------------------------------------


def _reaches_a_model() -> object:
    """A plant: a function that names an embedding adapter and nothing else."""
    # Imported inside the body deliberately: the plant has to be a name this
    # function's own code object spells, which is what the walk reads.
    from theurian.infrastructure.embedding.hashing import HashingEmbedding

    return HashingEmbedding


def test_a_model_planted_on_the_generator_class_reddens_the_walk(
    generator: CandidateGenerator, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Control one: the class-method arm."""
    monkeypatch.setattr(type(generator), "_probe", _reaches_a_model, raising=False)

    assert walk(generator).names & MODEL_NAMES, (
        "a model planted on the generator's own class was invisible to the walk, "
        "so the clean result above is a walk that inspects nothing"
    )


def _planted_verifier(_commit: str, _file_path: str) -> FixCommitVerdict:
    """A verification seam that *spells* an embedding adapter. Never called; walked.

    The name has to be in this function's own code object rather than one call
    away: the walk reads names, never values, so a seam that delegated to
    :func:`_reaches_a_model` would plant nothing.

    It keeps the seam's real return type so the assignment below is the one the
    composition root could make, rather than a shape ``VerifyFixCommit`` would
    not accept.
    """
    from theurian.infrastructure.embedding.hashing import HashingEmbedding

    assert HashingEmbedding.__name__
    return FixCommitVerdict.NO_SUCH_COMMIT


def test_a_model_planted_in_an_injected_collaborator_reddens_the_walk(
    generator: CandidateGenerator,
) -> None:
    """Control two: the injected-callable arm, which is where every seam arrives.

    All four of this pipeline's collaborators are injected callables, so this is
    the arm that matters most here -- unlike the ingest service, the generator
    holds almost nothing else.
    """
    generator._verify_fix_commit = _planted_verifier

    assert walk(generator).names & MODEL_NAMES, (
        "a model planted in the injected commit verification was invisible to the "
        "walk, so the clean result above says nothing about what the tool wires"
    )


def test_a_model_planted_on_a_collaborator_behind_a_bound_method_reddens_the_walk(
    generator: CandidateGenerator, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Control three: the arm that reaches an object held only as ``__self__``.

    The review-search store arrives as the bound method ``relative_path_for``, and
    the reader as the contents of a closure cell inside the record-read lambda.
    A walk that stopped at each callable's own code would miss a model on either
    object entirely and would still report the two controls above as green, so
    both are planted here -- one per holding shape.
    """
    monkeypatch.setattr(SqliteReviewSearchStore, "_probe", _reaches_a_model, raising=False)

    assert walk(generator).names & MODEL_NAMES, (
        "a model planted on the review-search store was invisible to the walk, so "
        "the traversal is not following bound methods to `__self__`"
    )


def test_a_model_planted_on_the_commit_verifier_reddens_the_walk(
    generator: CandidateGenerator, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Control four: the git adapter, the one collaborator that spawns a process.

    It is the newest thing on this path (ADR-0033 decision 3) and the only one
    that reaches outside the evidence store, so a plant on it is the arm that
    would still be green if the walk followed every seam but that one.
    """
    monkeypatch.setattr(FixCommitCheck, "_probe", _reaches_a_model, raising=False)

    assert walk(generator).names & MODEL_NAMES, (
        "a model planted on the fix-commit verifier was invisible to the walk, so "
        "the git adapter is outside the graph this module claims to inspect"
    )
