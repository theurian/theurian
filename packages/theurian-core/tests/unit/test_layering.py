"""Layering enforcement (ADR-0003).

An architecture rule that only exists in a document is a rule that will be
violated within a quarter. These tests walk the real import graph.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

SRC = pathlib.Path(__file__).resolve().parents[2] / "src" / "theurian"

#: Only these packages may name a concrete adapter. They are the composition
#: roots where object graphs are wired.
COMPOSITION_ROOTS = frozenset({"cli", "daemon", "mcp"})


def _module_imports(path: pathlib.Path) -> set[str]:
    """Every module name imported by ``path``, absolute and relative alike."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            imports.add(node.module)
    return imports


def _python_files(package: str) -> list[pathlib.Path]:
    return sorted((SRC / package).rglob("*.py"))


def test_domain_does_not_import_application_or_infrastructure() -> None:
    """The dependency rule, checked against the code rather than the docs.

    If this fails, the ports abstraction has stopped meaning anything: the domain
    now knows about a concrete storage or transport technology, and swapping it
    is no longer a matter of writing an adapter.
    """
    violations: list[str] = []
    for path in _python_files("domain"):
        for imported in _module_imports(path):
            if imported.startswith(("theurian.application", "theurian.infrastructure")):
                violations.append(f"{path.relative_to(SRC)} imports {imported}")

    assert not violations, "domain/ must depend on nothing but itself:\n" + "\n".join(violations)


def test_application_does_not_import_infrastructure() -> None:
    """Use cases receive adapters; they never construct or name them."""
    violations: list[str] = []
    for path in _python_files("application"):
        for imported in _module_imports(path):
            if imported.startswith("theurian.infrastructure"):
                violations.append(f"{path.relative_to(SRC)} imports {imported}")

    assert not violations, "application/ must depend on ports, not adapters:\n" + "\n".join(
        violations
    )


def test_only_composition_roots_import_infrastructure() -> None:
    """Adapters are wired in one readable place per entry point."""
    violations: list[str] = []
    for path in SRC.rglob("*.py"):
        top_level = path.relative_to(SRC).parts[0]
        if top_level in COMPOSITION_ROOTS or top_level == "infrastructure":
            continue
        for imported in _module_imports(path):
            if imported.startswith("theurian.infrastructure"):
                violations.append(f"{path.relative_to(SRC)} imports {imported}")

    assert not violations, "Only cli/, daemon/, and mcp/ may name concrete adapters:\n" + "\n".join(
        violations
    )


@pytest.mark.parametrize(
    ("dependency", "allowed_packages"),
    [
        # Pre-1.0 and young-major dependencies stay inside one adapter each, so
        # a breaking upstream change breaks one module rather than the codebase
        # (ADR-0014).
        ("sqlite_vec", {"infrastructure"}),
        ("mcp", {"mcp", "daemon"}),
    ],
)
def test_volatile_dependencies_are_confined(dependency: str, allowed_packages: set[str]) -> None:
    violations: list[str] = []
    for path in SRC.rglob("*.py"):
        top_level = path.relative_to(SRC).parts[0]
        if top_level in allowed_packages:
            continue
        for imported in _module_imports(path):
            if imported == dependency or imported.startswith(f"{dependency}."):
                violations.append(f"{path.relative_to(SRC)} imports {imported}")

    assert not violations, (
        f"{dependency!r} must stay inside {sorted(allowed_packages)}:\n" + "\n".join(violations)
    )


def test_no_vendor_names_in_domain_or_application() -> None:
    """No LLM or cloud vendor is nameable from the inner layers (ADR-0009).

    Adapter *modules* may be named for a vendor. Domain and application code may
    not, because a vendor name there is a dependency the ports were supposed to
    remove.
    """
    vendors = ("openai", "anthropic", "cohere", "voyageai", "pinecone", "weaviate", "qdrant")
    violations: list[str] = []
    for package in ("domain", "application"):
        for path in _python_files(package):
            lowered = path.read_text(encoding="utf-8").lower()
            for vendor in vendors:
                if vendor in lowered:
                    violations.append(f"{path.relative_to(SRC)} mentions {vendor!r}")

    assert not violations, "\n".join(violations)
