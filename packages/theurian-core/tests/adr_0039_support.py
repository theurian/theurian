"""Readers ADR-0039's pin modules share.

``test_adr_0039_claims.py`` is the module ADR-0039's Compliance section names, and
its docstring states the reach of every scan here and in its siblings. There is no
import guard: a sibling imports this module like any other.

Lives at the tests root beside ``adr_0037_support``: under
``--import-mode=importlib`` a bare import resolves only from the directory
``conftest.py`` puts on ``sys.path``, and ``tests/unit`` is not it.
"""

from __future__ import annotations

import ast
import functools
import json
import os
import re
import shutil
import subprocess
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import Any, Final

from adr_0037_support import collapsed

from theurian.domain.enums import (
    KnowledgeKind,
    KnowledgeStatus,
    RelationType,
    Sensitivity,
    SpecificationStatus,
    TrustLevel,
)
from theurian.domain.migration import OperationKind

#: ``parents[3]``: ``tests`` -> ``theurian-core`` -> ``packages`` -> root.
REPO_ROOT: Final = Path(__file__).resolve().parents[3]
SRC: Final = REPO_ROOT / "packages" / "theurian-core" / "src" / "theurian"
SCHEMAS: Final = REPO_ROOT / "schemas"
MIGRATION_SCHEMA: Final = SCHEMAS / "migrations" / "migration.schema.json"
ADR_DIR: Final = REPO_ROOT / "docs" / "adr"
ADR: Final = ADR_DIR / "0039-closed-set-extension-compatibility.md"
ADR_0005: Final = ADR_DIR / "0005-yaml-knowledge-migrations.md"
ADR_0038: Final = ADR_DIR / "0038-specification-folds-into-a-knowledge-kind.md"
ROADMAP: Final = REPO_ROOT / "docs" / "roadmap.md"
SAMPLE: Final = REPO_ROOT / "examples" / "sample-project"
SECOND: Final = "01K1DEFABC01234567890ABCDE-add-order-cancellation.yaml"
ADR_0039_LINK: Final = "[ADR-0039](0039-closed-set-extension-compatibility.md)"

GOVERNED: Final = (
    KnowledgeKind,
    RelationType,
    OperationKind,
    KnowledgeStatus,
    Sensitivity,
    TrustLevel,
    SpecificationStatus,
)
GOVERNED_NAMES: Final = frozenset(cls.__name__ for cls in GOVERNED)
ADDITIVE: Final = frozenset({"KnowledgeKind", "RelationType", "OperationKind"})


def _adr() -> str:
    return collapsed(ADR.read_text(encoding="utf-8"))


def _section(heading: str, path: Path = ADR) -> list[str]:
    """The lines under ``heading``, up to the next heading of its level or above.

    Fence-aware: a ``# `` comment inside a fenced code block is not a heading.
    """
    lines = path.read_text(encoding="utf-8").splitlines()
    assert heading in lines, f"{path.name} has no heading {heading!r}; this read anchors on it"
    start = lines.index(heading)
    level = len(heading) - len(heading.lstrip("#"))
    fenced = False
    for at in range(start + 1, len(lines)):
        fenced ^= lines[at].startswith("```")
        if not fenced and re.match(rf"#{{1,{level}}} ", lines[at]):
            return lines[start + 1 : at]
    return lines[start + 1 :]


def _table(lines: Sequence[str]) -> list[tuple[str, ...]]:
    """Header and data rows of the first Markdown table in ``lines``."""
    rows: list[tuple[str, ...]] = []
    for line in lines:
        if line.strip().startswith("|"):
            rows.append(tuple(cell.strip() for cell in line.strip().strip("|").split("|")))
        elif rows:
            break
    return [row for row in rows if not set("".join(row)) <= set(":- ")]


def _numbered(lines: Sequence[str]) -> dict[int, str]:
    """The first numbered list in ``lines``, each item's wrapped lines rejoined."""
    items: dict[int, list[str]] = {}
    current: int | None = None
    for line in lines:
        start = re.match(r"(\d+)\. (.*)", line)
        if start:
            current = int(start.group(1))
            items[current] = [start.group(2)]
        elif current is not None and line.strip():
            items[current].append(line)
        elif current is not None:
            break
    return {number: collapsed(" ".join(parts)) for number, parts in items.items()}


def _blockquotes(path: Path) -> list[str]:
    """Each run of ``>`` lines, the markers stripped and the run collapsed."""
    runs: list[list[str]] = [[]]
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith(">"):
            runs[-1].append(line.removeprefix(">"))
        elif runs[-1]:
            runs.append([])
    return [collapsed(" ".join(run)) for run in runs if run]


def _git(*arguments: str) -> str:
    git = shutil.which("git")
    assert git is not None, "git is not on PATH, so the committed tree cannot be read"
    # A hook exports GIT_DIR / GIT_INDEX_FILE, which would answer for another tree.
    environment = {name: value for name, value in os.environ.items() if not name.startswith("GIT_")}
    completed = subprocess.run(  # noqa: S603 - fixed argv, no caller input
        [git, "-c", f"safe.directory={REPO_ROOT}", *arguments],
        cwd=REPO_ROOT,
        capture_output=True,
        env=environment,
        check=False,
        timeout=30,
    )
    assert completed.returncode in {0, 1}, completed.stderr.decode("utf-8", "replace")
    return completed.stdout.decode("utf-8")


@functools.cache
def _trees() -> dict[str, ast.Module]:
    return {
        path.relative_to(SRC).as_posix(): ast.parse(path.read_bytes(), filename=str(path))
        for path in sorted(SRC.rglob("*.py"))
    }


@functools.cache
def _source_lines(relative: str) -> tuple[str, ...]:
    return tuple((SRC / relative).read_text(encoding="utf-8").splitlines())


def _line(relative: str, node: ast.AST) -> str:
    return _source_lines(relative)[getattr(node, "lineno", 0) - 1].strip()


def _scoped(tree: ast.AST) -> Iterator[tuple[ast.AST, str]]:
    """Every node with the name of the innermost function holding it."""

    def walk(node: ast.AST, scope: str) -> Iterator[tuple[ast.AST, str]]:
        for child in ast.iter_child_nodes(node):
            inner = (
                child.name if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef) else scope
            )
            yield child, inner
            yield from walk(child, inner)

    return walk(tree, "<module>")


def _named(node: ast.AST) -> str | None:
    match node:
        case ast.Name(id=name) | ast.Attribute(attr=name):
            return name
    return None


def _function(relative: str, name: str) -> ast.FunctionDef:
    found = [
        node
        for node in ast.walk(_trees()[relative])
        if isinstance(node, ast.FunctionDef) and node.name == name
    ]
    assert len(found) == 1, f"{relative} defines {name} {len(found)} times; this pin reads one"
    return found[0]


def _spelled(node: ast.AST) -> set[str]:
    return {name for inner in ast.walk(node) if (name := _named(inner)) is not None}


def _schema(path: Path) -> dict[str, Any]:
    loaded: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return loaded


def _nodes(node: object, pointer: str = "#") -> Iterator[tuple[str, dict[str, Any]]]:
    """Every JSON object in a schema, with its JSON pointer."""
    if isinstance(node, dict):
        yield pointer, node
        for key, value in node.items():
            yield from _nodes(value, f"{pointer}/{key}")
    elif isinstance(node, list):
        for index, value in enumerate(node):
            yield from _nodes(value, f"{pointer}/{index}")
