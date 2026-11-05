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

from collections.abc import Collection, Iterable, Mapping
from itertools import pairwise

from theurian.domain.enums import INVERSE_RELATIONS, RelationType
from theurian.domain.errors import RelationCycleError
from theurian.domain.identifiers import ItemId
from theurian.domain.knowledge import KnowledgeAlias, KnowledgeRelation


def refuse_relation_cycles(rows: Collection[KnowledgeRelation | KnowledgeAlias]) -> None:
    """Raise on the first cycle, graphs and items visited in sorted order.

    Ids the alias map connects, at any depth, are one node: a served read resolves aliases.
    Literal ids go first, so only a cycle an alias joins is named by its rows as stored,
    which is what ``removeRelation`` deletes.

    Raises:
        RelationCycleError: Naming the graph's relation type and the cycle's items.
    """
    aliases = dict(
        sorted((a.alias.value, a.item_id.value) for a in rows if isinstance(a, KnowledgeAlias))
    )
    parent: dict[str, str] = {}

    def find(item: str) -> str:
        while (up := parent.get(item, item)) != item:
            parent[item] = item = parent.get(up, up)
        return item

    for alias, target in aliases.items():
        parent[find(alias)] = find(target)
    for node in ({}, {item: find(item) for pair in aliases.items() for item in pair}):
        graphs: dict[RelationType, dict[str, dict[str, list[tuple[str, str]]]]] = {}
        for relation in rows:
            if isinstance(relation, KnowledgeAlias) or not relation.must_be_acyclic():
                continue
            graph, source, target = (
                relation.relation_type,
                relation.source_item_id.value,
                relation.target_item_id.value,
            )
            row = f"{source} {graph} {target}"
            if graph is RelationType.SUPERSEDED_BY:
                graph, source, target = INVERSE_RELATIONS[graph], target, source
            edges = graphs.setdefault(graph, {}).setdefault(node.get(source, source), {})
            edges.setdefault(node.get(target, target), []).append((row, source))
        for graph in sorted(graphs):
            cycle = _first_cycle(graphs[graph])
            if cycle is not None:
                steps = [min(graphs[graph][a][b]) for a, b in pairwise((*cycle, cycle[0]))]
                nodes = set(cycle)
                joined = [f"alias {a} -> {t}" for a, t in aliases.items() if node.get(a) in nodes]
                through = (*(text for text, _ in steps), *joined) if joined else ()
                raise RelationCycleError(graph, tuple(ItemId(s) for _, s in steps), through)


def _first_cycle(successors: Mapping[str, Iterable[str]]) -> tuple[str, ...] | None:
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
