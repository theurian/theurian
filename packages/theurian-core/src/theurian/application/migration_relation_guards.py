"""INV-6 over the relations an apply leaves (ADR-0040 decision 5).

``MigrationEngine.apply`` calls :func:`refuse_relation_cycles` inside the write
transaction, after the pending loop, on every apply -- one with nothing pending
included -- over every relation row of the project, whichever operation wrote it
(``addRelation``, or ``deprecateItem``'s ``supersededBy``). Over the stored rows and
not the migration set, so a cyclic set a non-enforcing Core applied is refused at its
next apply, and a later ``removeRelation`` in the same set cures an earlier cycle.

``ACYCLIC_RELATIONS`` is read as two graphs. A stored ``superseded_by`` row from A to
B is the ``supersedes`` row from B to A, so checked apart a cycle alternating the two
would pass; ``depends_on`` is a graph of its own. A loop mixing the two graphs breaks
neither reason ``ACYCLIC_RELATIONS`` records, and is not refused.
"""

from __future__ import annotations

from collections.abc import Iterable

from theurian.domain.enums import INVERSE_RELATIONS, RelationType
from theurian.domain.errors import RelationCycleError
from theurian.domain.identifiers import ItemId
from theurian.domain.knowledge import KnowledgeRelation


def refuse_relation_cycles(relations: Iterable[KnowledgeRelation]) -> None:
    """Raise on the first cycle, graphs and items visited in sorted order.

    Raises:
        RelationCycleError: Naming the graph's relation type and the cycle's items.
    """
    graphs: dict[RelationType, dict[str, list[str]]] = {}
    for relation in relations:
        if not relation.must_be_acyclic():
            continue
        graph, source, target = (
            relation.relation_type,
            relation.source_item_id.value,
            relation.target_item_id.value,
        )
        if graph is RelationType.SUPERSEDED_BY:
            graph, source, target = INVERSE_RELATIONS[graph], target, source
        graphs.setdefault(graph, {}).setdefault(source, []).append(target)
    for graph in sorted(graphs):
        cycle = _first_cycle(graphs[graph])
        if cycle is not None:
            raise RelationCycleError(graph, tuple(ItemId(item) for item in cycle))


def _first_cycle(successors: dict[str, list[str]]) -> tuple[str, ...] | None:
    """The first cycle an iterative depth-first search meets, or ``None``.

    Iterative because a 10,000-relation chain is deeper than Python's recursion limit.
    """
    finished: set[str] = set()
    for start in sorted(successors):
        if start in finished:
            continue
        path = [start]
        on_path = {start}
        pending = [iter(sorted(successors[start]))]
        while pending:
            following = next(pending[-1], None)
            if following is None:
                pending.pop()
                done = path.pop()
                on_path.discard(done)
                finished.add(done)
            elif following in on_path:
                return tuple(path[path.index(following) :])
            elif following not in finished:
                path.append(following)
                on_path.add(following)
                pending.append(iter(sorted(successors.get(following, ()))))
    return None
