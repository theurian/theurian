"""FR-V5 over the **built** ingest pipeline, not over a naming convention.

*"Raw review ingestion must succeed even when candidate generation fails."* The
way ADR-0030 discharges that is structural rather than by a fallback: no model
is in the path at all, so there is nothing for a model failure to break. A
sentence saying so proves nothing, and neither does a module list somebody keeps
in step -- this walks the object graph
``cli/review_commands.build_ingest_service`` actually returns, in the shape
``tests/integration/test_mcp_tools.py::test_no_registered_tool_can_reach_a_canonical_write``
established for the canonical-write guarantee.

Two halves, because each is blind where the other sees.

* **The bytecode half** starts at the composed service and follows attributes,
  ``__wrapped__`` chains, closure cells and nested code objects, collecting every
  global and attribute name each reachable callable references. A model reached
  from *inside* any of them shows up here whatever the module list says.
* **The name half** takes the modules those callables came from, closes that set
  under **module-level** ``theurian`` imports, and scans each module's imports
  and identifiers. Both steps are derived from the walk rather than listed, and
  the closure is what makes the half reach a helper module the graph names but
  never holds -- ``review_evidence/layout.py`` is exactly that: the walk sees the
  name ``EvidenceRecord`` in a closure and never the class, because the class is
  constructed at call time. Why *module-level* and what that leaves out is
  recorded on :func:`_module_level_imports`.

**The blind spot, stated rather than discovered.** ``co_names`` sees the names a
function *spells*. A provider obtained through ``getattr(module, name)``, a
factory looked up in a table, or an import performed one frame deeper than the
walk reaches is invisible to it -- the walk descends into objects the graph
already holds, and an object nothing holds yet is not in the graph. What bounds
that is the name half above and the fact that no module in the ingest path
imports a provider module at all; neither is a proof that a dynamic route could
not be written.

**The three positive controls are what make a clean result mean anything.** A
walk that reached nothing would report the same green. One plant per arm --
a class method, an injected closure, and a collaborator reached only through a
closure cell -- each asserted to turn this test RED.
"""

from __future__ import annotations

import ast
import dataclasses
import functools
import importlib
import inspect
import pathlib
import sys
import types
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

import pytest

from theurian.application.project_service import ProjectPaths
from theurian.application.review_ingest_service import ReviewIngestService
from theurian.cli.review_commands import build_ingest_service
from theurian.infrastructure.review_evidence import IngestionRun, ReviewEvidenceStore

pytestmark = pytest.mark.integration

REPOSITORY: Final = "acme/order-service"

RUN: Final = IngestionRun("01K1AAAAAA01234567890ABCDE", datetime(2026, 9, 7, 9, 0, tzinfo=UTC))

#: The modules a model would arrive from: the three provider **ports**, and the
#: two adapters this build ships for them. Named as modules rather than as names,
#: so the forbidden vocabulary below is read out of them instead of transcribed.
MODEL_MODULES: Final = (
    "theurian.domain.ports.embedding",
    "theurian.domain.ports.summarization",
    "theurian.domain.ports.reranking",
    "theurian.infrastructure.embedding.hashing",
    "theurian.infrastructure.raptor.extractive",
)

#: Package components of :data:`MODEL_MODULES` that name a layer rather than a
#: model. Forbidding these would forbid every module in the package.
_LAYERS: Final = frozenset({"theurian", "domain", "ports", "infrastructure"})


def _model_vocabulary() -> frozenset[str]:
    """Every name a reference to a model would be spelled with.

    Three sources, all read off the imported modules:

    * the modules' own leaf components -- ``embedding``, ``summarization``,
      ``reranking``, ``hashing``, ``extractive``, ``raptor`` -- minus the
      layer names, which say nothing about models;
    * every public class those modules define;
    * every public member of those classes, **except** where the class is a
      dataclass. A value type's field names are not a provider surface, and
      ``ScoredCandidate`` carries ``text`` -- a name the GitHub response reader
      uses for something else entirely, which would make this guard report a
      model where there is a string.
    """
    names: set[str] = set()
    for dotted in MODEL_MODULES:
        parts = [part for part in dotted.split(".") if part not in _LAYERS]
        names.update(parts)
        module = importlib.import_module(dotted)
        for attribute, value in vars(module).items():
            if attribute.startswith("_") or not isinstance(value, type):
                continue
            if value.__module__ != dotted:
                continue
            names.add(attribute)
            if dataclasses.is_dataclass(value):
                continue
            names.update(member for member in vars(value) if not member.startswith("_"))
    return frozenset(names)


MODEL_NAMES: Final = _model_vocabulary()


# -- the graph walk -----------------------------------------------------------


def _is_ours(value: object) -> bool:
    """Whether an object belongs to this package rather than to the runtime.

    The traversal's own bound: it descends into Theurian's objects and stops at
    ``Path``, ``dict`` and every other thing the standard library owns. Stated
    because it is a *population* decision -- a model held inside a stdlib
    container the pipeline carries would not be seen -- and no such container is
    in the graph.
    """
    module = getattr(value, "__module__", None) or type(value).__module__
    return isinstance(module, str) and module.startswith("theurian")


def _code_objects(function: Any) -> Iterator[types.CodeType]:
    """Every code object a callable carries, following ``__wrapped__``.

    The whole chain is walked, wrapper and wrapped, rather than jumping to
    ``inspect.unwrap``'s innermost function: a walk that stepped over an
    intermediate wrapper would stop seeing a reference introduced *in* one.
    """
    pending: list[types.CodeType] = []
    current: Any = function
    while current is not None and hasattr(current, "__code__"):
        pending.append(current.__code__)
        current = getattr(current, "__wrapped__", None)
    while pending:
        code = pending.pop()
        yield code
        pending.extend(const for const in code.co_consts if isinstance(const, types.CodeType))


@dataclasses.dataclass(frozen=True, slots=True)
class Walk:
    """What one traversal of a built pipeline saw."""

    #: Every global and attribute name any reachable callable spells.
    names: frozenset[str]
    #: Every ``theurian`` module a reachable callable was defined in.
    modules: frozenset[str]
    #: How many callables were reached. The vacuity guard's number.
    callables: int


def walk(root: object) -> Walk:
    """Every name and module reachable from ``root`` by attribute, closure and class.

    A breadth-first traversal over five kinds of edge:

    * an **instance** to its class and to each of its attribute values, read
      through ``__dict__`` or the slots its class declares;
    * a **class** to the values in its own ``vars()`` -- which is how a method
      the graph never calls is still inspected;
    * a **descriptor** to the function underneath it. Its own member shape, not
      the routine check's: a ``property``, a ``staticmethod``, a
      ``classmethod``, a ``cached_property`` and a ``partial`` are none of them
      routines, and a walk that only tested ``isroutine`` skipped every one.
      Measured while writing this -- ``EvidenceRecord.relative_path`` is a
      ``property``, and the walk reported a clean graph it had never entered
      (#237's reflection-sweep lesson, arriving on the object side);
    * a **callable** to its ``__wrapped__`` chain, to the contents of its
      ``__closure__`` cells, and to ``__self__`` when it is bound;
    * a **callable** to every name in every code object it carries, nested ones
      included, which is the leaf of the walk.
    """
    seen: set[int] = set()
    queue: list[Any] = [root]
    names: set[str] = set()
    modules: set[str] = set()
    reached = 0

    while queue:
        current = queue.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))

        if inspect.isroutine(current):
            reached += 1
            module = getattr(current, "__module__", None)
            if isinstance(module, str) and module.startswith("theurian"):
                modules.add(module)
            for code in _code_objects(current):
                names.update(code.co_names)
            for cell in getattr(current, "__closure__", None) or ():
                queue.append(cell.cell_contents)
            bound = getattr(current, "__self__", None)
            if bound is not None:
                queue.append(bound)
            continue

        underneath = _behind_a_descriptor(current)
        if underneath is not None:
            queue.extend(underneath)
            continue

        if isinstance(current, type):
            if not _is_ours(current):
                continue
            queue.extend(vars(current).values())
            continue

        if not _is_ours(current):
            continue
        queue.append(type(current))
        queue.extend(_attribute_values(current))

    return Walk(names=frozenset(names), modules=frozenset(modules), callables=reached)


def _behind_a_descriptor(value: object) -> list[Any] | None:
    """What a descriptor holds, or ``None`` when ``value`` is not one.

    Every member shape this package's classes actually use, enumerated rather
    than assumed: an object that is not a routine can still hold one, and each of
    these is a way a method reaches a class body without ``isroutine`` seeing it.
    ``functools.partial`` is here for the same reason on the *instance* side --
    a collaborator bound with arguments is a partial, not a function.
    """
    match value:
        case property():
            return [each for each in (value.fget, value.fset, value.fdel) if each is not None]
        case staticmethod() | classmethod():
            return [value.__func__]
        case functools.cached_property():
            return [value.func]
        case functools.partial():
            return [value.func, *value.args, *value.keywords.values()]
        case _:
            return None


def _attribute_values(instance: object) -> list[Any]:
    """An instance's attribute values, whether it uses ``__dict__`` or slots.

    Both, because this package's own idiom is ``slots=True``: a walk that read
    ``__dict__`` alone would see nothing at all on a frozen dataclass and report
    a clean graph over an object it never opened.
    """
    values: list[Any] = list(vars(instance).values()) if hasattr(instance, "__dict__") else []
    for klass in type(instance).__mro__:
        for slot in getattr(klass, "__slots__", ()):
            if isinstance(slot, str) and hasattr(instance, slot):
                values.append(getattr(instance, slot))
    return values


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
    closure = _import_closure(seen.modules)
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
    """FR-V5, name half: derived from the walk, so the population cannot be curated.

    Imports and identifiers only. Docstrings are ``ast.Constant`` nodes and are
    deliberately not read -- this module's own prose names every forbidden term,
    and so may a module that explains why it holds none of them.
    """
    seen = walk(service)
    assert seen.modules, "no module was derived from the walk, so this scans nothing"
    population = _import_closure(seen.modules)
    assert "theurian.infrastructure.review_evidence.layout" in population, (
        "the import closure did not reach a module the walk only names, so this "
        "half is scanning the graph's objects rather than the ingest path"
    )

    offenders: dict[str, frozenset[str]] = {}
    for dotted in sorted(population):
        named = _names_in_source(dotted) & MODEL_NAMES
        if named:
            offenders[dotted] = named

    assert not offenders, (
        f"modules in the ingest path name model vocabulary: {offenders}. Either a "
        f"provider arrived on this path, or the vocabulary above has collided with "
        f"an ordinary name and needs narrowing -- say which in the same change."
    )


def _import_closure(seed: frozenset[str]) -> frozenset[str]:
    """``seed`` plus every ``theurian`` module those modules import, transitively.

    The population the name half ranges over, derived rather than listed. A module
    the graph merely *names* -- because the object it names is constructed at call
    time -- is unreachable by the object walk and reachable here, which is the
    whole reason this exists: ``review_evidence/layout.py`` and the landing gate
    are both in this set and in neither's object graph.

    **Each member is imported rather than looked up in ``sys.modules``**, so the
    population is the same whether this file runs alone or after a test that
    happened to import half the CLI. Measured while writing it: reading
    ``sys.modules`` made the result depend on collection order, and the run that
    followed the CLI tests scanned eight modules the run alone did not.
    """
    found: set[str] = set()
    pending = list(seed)
    while pending:
        dotted = pending.pop()
        if dotted in found or not _is_a_module(dotted) or _is_a_package(dotted):
            continue
        found.add(dotted)
        pending.extend(_module_level_imports(dotted))
    return frozenset(found)


def _is_a_module(dotted: str) -> bool:
    """Whether ``dotted`` names an importable module rather than a symbol in one.

    ``from theurian.domain.review import ReviewEvent`` yields both spellings and
    only one of them is a module; importing is how the two are told apart, and it
    is cheap because everything here is already loaded by the fixture.
    """
    try:
        importlib.import_module(dotted)
    except ImportError:
        return False
    return dotted in sys.modules


def _is_a_package(dotted: str) -> bool:
    """Whether ``dotted`` is a package ``__init__``, which this population excludes.

    **A recorded bound, not an oversight.** Importing any submodule executes its
    package's ``__init__``, and ``theurian/domain/ports/__init__.py`` re-exports
    *every* port -- so an unfiltered closure reports the ingest path as reaching
    ``EmbeddingProvider`` because it imports ``review_provider`` from the same
    package. That is a property of the package's membership, not of this path.
    What covers an aggregator that genuinely holds a model reference is the
    bytecode half: a callable that *used* it would spell it.
    """
    return pathlib.Path(str(sys.modules[dotted].__file__)).name == "__init__.py"


def _module_level_imports(dotted: str) -> Iterator[str]:
    """Every ``theurian`` module one module imports **at module level**.

    Function-local imports are deliberately not followed, and that is the other
    half of this population's key. ``cli/review_commands.py`` imports four output
    helpers from ``cli/commands.py`` inside the command body to break an import
    cycle; following it would pull in every other command's composition root --
    including ``index build``'s, which composes an embedder on purpose -- and
    report the ingest path as reaching a model because the CLI it is registered
    in has one.

    So the scope this file asserts over is **the composed pipeline and the modules
    it is built from**, not the command's shared CLI plumbing. What ``review
    ingest`` takes across that seam is ``_emit``, ``_fail``,
    ``_fail_a_path_escape`` and ``_require_project``: reporting and project
    resolution, none of it ingestion, and each covered by the package-wide sweeps
    in ``tests/unit/test_findings_store_is_unreachable.py`` rather than here.
    """
    for node in _module_level_nodes(ast.parse(_source_of(dotted))):
        match node:
            case ast.Import(names=aliases):
                for alias in aliases:
                    if alias.name.startswith("theurian"):
                        yield alias.name
            case ast.ImportFrom(module=where, names=aliases) if (where or "").startswith(
                "theurian"
            ):
                yield str(where)
                for alias in aliases:
                    yield f"{where}.{alias.name}"


def _module_level_nodes(tree: ast.Module) -> Iterator[ast.stmt]:
    """Every statement that runs at import time, descending through ``if``/``try``.

    ``if TYPE_CHECKING:`` and a ``try``/``except ImportError`` around an optional
    dependency are module level, so a walk that read ``tree.body`` alone would
    miss an import placed in either.
    """
    pending: list[ast.stmt] = list(tree.body)
    while pending:
        node = pending.pop()
        yield node
        match node:
            case ast.If(body=body, orelse=orelse):
                pending.extend([*body, *orelse])
            case ast.Try(body=body, orelse=orelse, finalbody=final, handlers=handlers):
                pending.extend([*body, *orelse, *final])
                for handler in handlers:
                    pending.extend(handler.body)
            case _:
                pass


def _source_of(dotted: str) -> str:
    return pathlib.Path(str(sys.modules[dotted].__file__)).read_text(encoding="utf-8")


def _names_in_source(dotted: str) -> frozenset[str]:
    """Every imported name and identifier one shipped module spells."""
    found: set[str] = set()
    for node in ast.walk(ast.parse(_source_of(dotted))):
        match node:
            case ast.Import(names=aliases):
                for alias in aliases:
                    found.update(alias.name.split("."))
                    if alias.asname:
                        found.add(alias.asname)
            case ast.ImportFrom(module=where, names=aliases):
                found.update((where or "").split("."))
                for alias in aliases:
                    found.add(alias.name)
                    if alias.asname:
                        found.add(alias.asname)
            case ast.Name(id=name):
                found.add(name)
            case ast.Attribute(attr=attribute):
                found.add(attribute)
    return frozenset(found)


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
