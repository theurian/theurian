"""Readers ADR-0038's pin modules share.

``test_adr_0038_claims.py`` is the module ADR-0038's Compliance section names;
it and its ``test_adr_0038_*.py`` siblings read the tree and the documents
through this one, so a fix to a reader lands once.

**Reach.** The scans parse every ``.py`` file ``git ls-files`` lists and see
attribute references, bare names and exact-string spellings. Each key marked
(#845) below is also stated beside its scan, and #845
(https://github.com/theurian/theurian/issues/845) owns widening it. A
``structured`` read is an attribute load, ``getattr(x, "structured")`` or the
bare string ``"structured"`` (a row or payload key). A whole-object read is a
*call* of ``asdict``, ``astuple`` or ``vars``, a ``__dict__`` access or a
``getattr`` with a computed name, held as an exact allow-set; a reader passed by
reference (``default=dataclasses.asdict``) is not one (#845). A read of the
``apiVersion`` key is a subscript or ``.get(...)`` of the literal, a ``match``
subject included, held to the two functions that check it today; a
mapping-pattern key or a key held in a named constant is not one (#845). A
compiled ``apiVersion`` check is any comparison or ``match`` that reads the key
or names the constant, a container holding it, or a name bound to either, and
each of the two is held as a top-level ``if`` of exactly its comparison whose
body is one ``raise``. SQL names the ``specifications`` table bare, quoted,
schema-qualified or after a comma join. A construction is ``Specification(...)``
called by bare name or as an attribute. The ``KnowledgeRevision.create`` scan
counts that expression spelled exactly so, called or referenced: an aliased
import's ``.create(...)`` is outside its reach, though a ``structured=`` keyword
on such a call is still caught by the keyword scan. Three more pins hold less
than the sentence they face (#845): the gate's project scope holds
``_ITEM_METADATA_SQL``'s ``project_id`` scoping and the scoped argument tuple,
not that ``get_item_exact_metadata`` makes no other read; the engine half of
``kind`` riding on the revision holds only that ``_upsert_revision`` calls
``with_revision``, not that the result reaches ``put_item`` unmodified; and the
field table's not-carried rule is a denylist of negation words. The scans do not
see a class imported under another name, a copy made by ``dataclasses.replace``,
a keyword smuggled through ``**kwargs``, a dictionary key computed at run time,
SQL assembled from fragments, or a second whole-object read inside a function
already on the allow-set.

**What is not parsed, and what it costs.** Every module of the pin set -- each
``test_adr_0038_*.py`` module and this one -- spells the names its scans search
for as data, so all of them are excluded (tracked, each would find itself).
:data:`_UNREAD` takes them from that glob, and :func:`_parsed` refuses to run
unless the glob's tracked members are exactly the modules importing this one: a
module named into the glob without importing this one would be hidden from the
scans, and one importing it from outside the glob would be parsed. The import,
at column 0, is the key; one inside a function is outside it (#845).
``domain/enums.py``, read by the pin set only by importing its enums,
``mcp/results.py`` and ``tests/unit/test_gate_call_sites.py`` are fenced because
PR #835 (https://github.com/theurian/theurian/pull/835) edits all three. The
fence is not free: ``mcp/results.py`` is a real ``KnowledgeRevision`` serialiser
-- it builds the search-result payload field by field -- so a
specification-reader call, a ``structured`` publication or a traceability-edge
method placed there is not seen by these scans while the fence stands. Deleting
those three entries from :data:`_UNREAD` once #835 has merged lifts it.

Every scan whose expected answer is *nothing* runs beside a positive control on
the same key, and every widened shape is driven through its classifier from an
in-memory snippet, because a broken walk and a clean tree look alike from
outside.

Lives at the tests root beside ``adr_0037_support``: under
``--import-mode=importlib`` a bare import resolves only from the directory
``conftest.py`` puts on ``sys.path``, and ``tests/unit`` is not it.
"""

from __future__ import annotations

import ast
import collections
import functools
import json
import os
import re
import shutil
import subprocess
from collections.abc import Callable, Iterator, Mapping, Sequence
from pathlib import Path
from typing import Any, Final

import pytest

from theurian.infrastructure.sqlite import schema as sqlite_schema

#: ``parents[3]`` is ``.../tests/`` -> ``theurian-core`` -> ``packages`` -> root.
REPO_ROOT: Final = Path(__file__).resolve().parents[3]
ROADMAP: Final = REPO_ROOT / "docs" / "roadmap.md"
README: Final = REPO_ROOT / "README.md"
THREAT_MODEL: Final = REPO_ROOT / "docs" / "security" / "threat-model.md"
TRACEABILITY: Final = REPO_ROOT / "docs" / "architecture" / "traceability.md"
ADR_0005: Final = REPO_ROOT / "docs" / "adr" / "0005-yaml-knowledge-migrations.md"
MIGRATION_SCHEMA: Final = REPO_ROOT / "schemas" / "migrations" / "migration.schema.json"

SRC: Final = "packages/theurian-core/src/theurian/"
ALIAS_GUARDS: Final = SRC + "application/migration_alias_guards.py"
ENGINE: Final = SRC + "application/migration_engine.py"
INGESTION: Final = SRC + "application/ingestion_service.py"
PROPOSALS: Final = SRC + "application/proposal_service.py"
COMMANDS: Final = SRC + "cli/commands.py"
KNOWLEDGE: Final = SRC + "domain/knowledge.py"
MIGRATION: Final = SRC + "domain/migration.py"
PORT: Final = SRC + "domain/ports/canonical_store.py"
PROVIDER: Final = SRC + "domain/ports/specification_provider.py"
LOADER: Final = SRC + "infrastructure/filesystem/migration_loader.py"
PARSERS: Final = SRC + "infrastructure/filesystem/parsers/"
STORE: Final = SRC + "infrastructure/sqlite/store.py"
TOOLS: Final = SRC + "mcp/tools.py"
SNIPPET: Final = "<snippet>"
CORRUPTION_TEST: Final = (
    "packages/theurian-core/tests/integration/test_canonical_store_corruption.py"
)
SAMPLE_MIGRATION: Final = (
    "examples/sample-project/.theurian/migrations/"
    "01K1DEFABC01234567890ABCDE-add-order-cancellation.yaml"
)

_PIN_GLOB: Final = "packages/theurian-core/tests/unit/test_adr_0038_*.py"
_UNREAD: Final = frozenset(
    {
        SRC + "domain/enums.py",
        SRC + "mcp/results.py",
        "packages/theurian-core/tests/unit/test_gate_call_sites.py",
        Path(__file__).resolve().relative_to(REPO_ROOT).as_posix(),
        *(path.relative_to(REPO_ROOT).as_posix() for path in REPO_ROOT.glob(_PIN_GLOB)),
    }
)

_SPECIFICATIONS_TABLE: Final = (
    r"""(?:(?:\w+|"\w+"|`\w+`|\[\w+\])\s*\.\s*)?"""
    r"""(?:specifications|"specifications"|`specifications`|\[specifications\])(?![\w"`\]])"""
)
_READS_SPECIFICATIONS: Final = re.compile(
    rf"""\b(?:FROM|JOIN)\s+(?:[\w."`\[\]]+(?:\s+(?:AS\s+)?\w+)?\s*,\s*)*{_SPECIFICATIONS_TABLE}""",
    re.IGNORECASE,
)
_WRITES_SPECIFICATIONS: Final = re.compile(
    rf"\b(?:(?:INSERT|REPLACE)(?:\s+OR\s+\w+)?\s+INTO|UPDATE(?:\s+OR\s+\w+)?|DELETE\s+FROM)\s+"
    rf"{_SPECIFICATIONS_TABLE}",
    re.IGNORECASE,
)
_WHOLE_OBJECT_READERS: Final = frozenset({"asdict", "astuple", "vars"})


# -- Reading the tree ---------------------------------------------------------


def _git(*arguments: str) -> subprocess.CompletedProcess[bytes]:
    git = shutil.which("git")
    if git is None:
        pytest.skip("git is not on PATH, so the committed tree cannot be listed")
    # A hook exports GIT_DIR / GIT_INDEX_FILE, which would answer for another tree.
    environment = {name: value for name, value in os.environ.items() if not name.startswith("GIT_")}
    return subprocess.run(  # noqa: S603 - fixed argv, no caller input
        [git, "-c", f"safe.directory={REPO_ROOT}", *arguments],
        cwd=REPO_ROOT,
        capture_output=True,
        env=environment,
        check=False,
        timeout=30,
    )


def _tracked(*pathspecs: str) -> tuple[str, ...]:
    listed = _git("ls-files", "-z", "--", *pathspecs)
    assert listed.returncode == 0, listed.stderr.decode("utf-8", "replace")
    return tuple(sorted(filter(None, listed.stdout.decode("utf-8", "surrogateescape").split("\0"))))


@functools.cache
def _parsed() -> Mapping[str, ast.Module]:
    """Every tracked ``.py`` file outside :data:`_UNREAD`, once the glob matches the pin set."""
    # Key: an import of this module at column 0; one inside a function is not seen (#845).
    grep = _git("grep", "-l", "-z", "-E", "^(from|import) adr_0038_support", "--", "*.py")
    pin_set = set(filter(None, grep.stdout.decode("utf-8", "surrogateescape").split("\0")))
    named = set(_tracked(_PIN_GLOB))

    assert grep.returncode in {0, 1}, grep.stderr.decode("utf-8", "replace")
    assert "packages/theurian-core/tests/unit/test_adr_0038_claims.py" in pin_set, (
        "positive control: the import key no longer finds the claims module"
    )
    assert named == pin_set, (
        f"{sorted(named - pin_set)} match {_PIN_GLOB} without importing adr_0038_support, so "
        f"_UNREAD hides them from the scans; {sorted(pin_set - named)} import it from outside "
        f"the glob, so the scans parse the names they spell as data"
    )
    return {
        path: ast.parse((REPO_ROOT / path).read_bytes(), filename=path)
        for path in _tracked("*.py")
        if path not in _UNREAD and (REPO_ROOT / path).is_file()
    }


def _trees(prefix: str = "") -> Mapping[str, ast.Module]:
    trees = {path: tree for path, tree in _parsed().items() if path.startswith(prefix)}
    assert {STORE, ENGINE} <= trees.keys(), (
        f"the scanned population under {prefix!r} no longer holds store.py and "
        f"migration_engine.py, so every scan over it would report nothing and read as a pass"
    )
    return trees


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


@functools.cache
def _index() -> tuple[Mapping[str, set[str]], Mapping[str, set[str]]]:
    """The files spelling each name, and the files defining a function by it: one walk."""
    spelled: collections.defaultdict[str, set[str]] = collections.defaultdict(set)
    defined: collections.defaultdict[str, set[str]] = collections.defaultdict(set)
    for path, tree in _parsed().items():
        for node in ast.walk(tree):
            match node:
                case ast.Attribute(attr=name) | ast.Name(id=name) | ast.Constant(value=str(name)):
                    spelled[name].add(path)
                case ast.FunctionDef(name=name) | ast.AsyncFunctionDef(name=name):
                    defined[name].add(path)
    return spelled, defined


def _references(names: frozenset[str], prefix: str = "") -> set[tuple[str, str]]:
    """Every attribute, bare name or exact string spelling one of ``names``."""
    within = set(_trees(prefix))
    spelled, _ = _index()
    return {(path, name) for name in names for path in spelled.get(name, set()) & within}


def _definitions(name: str, prefix: str = "") -> set[str]:
    within = set(_trees(prefix))
    _, defined = _index()
    return defined.get(name, set()) & within


def _function(path: str, name: str) -> ast.FunctionDef | ast.AsyncFunctionDef:
    found = [
        node
        for node in ast.walk(_trees()[path])
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.name == name
    ]
    assert len(found) == 1, f"{path} defines {name} {len(found)} times; this pin reads exactly one"
    return found[0]


def _callees(node: ast.AST) -> set[str]:
    called: set[str] = set()
    for call in ast.walk(node):
        if isinstance(call, ast.Call):
            match call.func:
                case ast.Attribute(attr=name) | ast.Name(id=name):
                    called.add(name)
    return called


def _constructions(
    class_name: str, trees: Mapping[str, ast.Module] | None = None
) -> list[tuple[str, str, ast.Call]]:
    found: list[tuple[str, str, ast.Call]] = []
    for path, tree in (_trees(SRC) if trees is None else trees).items():
        for node, scope in _scoped(tree):
            match node:
                case ast.Call(func=ast.Name(id=name) | ast.Attribute(attr=name)) if (
                    name == class_name
                ):
                    found.append((path, scope, node))
    return found


def _sites(
    trees: Mapping[str, ast.Module], classify: Callable[[ast.AST], str | None]
) -> set[tuple[str, str, str]]:
    """``(path, function, what)`` for every node ``classify`` names."""
    return {
        (path, scope, found)
        for path, tree in trees.items()
        for node, scope in _scoped(tree)
        if (found := classify(node)) is not None
    }


def _structured_read(node: ast.AST) -> str | None:
    match node:
        case (
            ast.Attribute(attr="structured", ctx=ast.Load())
            | ast.Call(func=ast.Name(id="getattr"), args=[_, ast.Constant(value="structured"), *_])
            | ast.Constant(value="structured")
        ):
            return ast.unparse(node)
    return None


def _whole_object_read(node: ast.AST) -> str | None:
    """A read of every field of whatever object it is handed."""
    # Key: a CALL of asdict/astuple/vars, a __dict__ access, a getattr with a computed name.
    # Not a reader passed by reference, e.g. `default=dataclasses.asdict` (#845 widens it).
    match node:
        case ast.Call(func=ast.Name(id=name) | ast.Attribute(attr=name)) if (
            name in _WHOLE_OBJECT_READERS
        ):
            return name
        case ast.Call(func=ast.Name(id="getattr"), args=[_, attribute, *_]) if not isinstance(
            attribute, ast.Constant
        ):
            return "getattr"
        case ast.Attribute(attr="__dict__"):
            return "__dict__"
    return None


def _sql(pattern: re.Pattern[str]) -> Callable[[ast.AST], str | None]:
    def classify(node: ast.AST) -> str | None:
        match node:
            case ast.Constant(value=str(text)) if pattern.search(text):
                return "sql"
        return None

    return classify


def _revision_creates(trees: Mapping[str, ast.Module]) -> set[str]:
    return {
        path
        for path, tree in trees.items()
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute) and ast.unparse(node) == "KnowledgeRevision.create"
    }


def _keywords(call: ast.Call) -> dict[str | None, str]:
    return {keyword.arg: ast.unparse(keyword.value) for keyword in call.keywords}


def _arms(node: ast.AST) -> dict[str, ast.match_case]:
    """Each ``case`` under ``node``, keyed by the class or the string it matches."""
    arms: dict[str, ast.match_case] = {}
    for arm in ast.walk(node):
        if isinstance(arm, ast.match_case):
            match arm.pattern:
                case (
                    ast.MatchClass(cls=ast.Name(id=key))
                    | ast.MatchValue(value=ast.Constant(value=str(key)))
                ):
                    arms[key] = arm
    return arms


def _raises(node: ast.AST) -> bool:
    return any(isinstance(inner, ast.Raise) for inner in ast.walk(node))


def _executed_sql(function: ast.AST) -> list[str]:
    return [
        ast.literal_eval(call.args[0])
        for call in ast.walk(function)
        if isinstance(call, ast.Call)
        and isinstance(call.func, ast.Attribute)
        and call.func.attr == "execute"
    ]


def _ddl_tables() -> dict[str, dict[str, str]]:
    """Column name to the rest of its definition line, per ``CREATE TABLE``."""
    constraints = {"PRIMARY", "UNIQUE", "CHECK", "FOREIGN", "CONSTRAINT"}
    tables: dict[str, dict[str, str]] = {}
    for table, body in re.findall(r"CREATE TABLE (\w+) \((.*?)\n\);", sqlite_schema.DDL, re.DOTALL):
        definitions = (line.split(maxsplit=1) for line in body.splitlines())
        tables[table] = {
            words[0]: words[-1]
            for words in definitions
            if words and not words[0].startswith("--") and words[0] not in constraints
        }
    return tables


def _migration_defs() -> dict[str, Any]:
    return dict(json.loads(MIGRATION_SCHEMA.read_text(encoding="utf-8"))["$defs"])


def _reads_the_version(node: ast.AST) -> bool:
    # Key: a Subscript or .get(...) of the literal "apiVersion", a match subject included.
    # Not a mapping-pattern key, nor a key held in a named constant (#845 widens it).
    match node:
        case (
            ast.Subscript(slice=ast.Constant(value="apiVersion"))
            | ast.Call(func=ast.Attribute(attr="get"), args=[ast.Constant(value="apiVersion"), *_])
        ):
            return True
    return False


def _names_the_version(node: ast.AST | None, bound: frozenset[str]) -> bool:
    """The constant, a container holding it, or a name bound to either."""
    match node:
        case ast.Constant(value="theurian.dev/v1"):
            return True
        case ast.Name(id=name) | ast.Attribute(attr=name):
            return name in bound
        case ast.Set(elts=items) | ast.List(elts=items) | ast.Tuple(elts=items):
            return any(_names_the_version(item, bound) for item in items)
        case ast.Dict(keys=keys):
            return any(_names_the_version(key, bound) for key in keys)
        case ast.Call(args=[inner]):
            return _names_the_version(inner, bound)
    return False


def _api_version_checks(trees: Mapping[str, ast.Module]) -> set[tuple[str, str]]:
    """Every comparison or ``match`` that reads the key or names the version."""
    assignments = [
        (target.id, node.value)
        for tree in trees.values()
        for node in ast.walk(tree)
        if isinstance(node, ast.Assign | ast.AnnAssign)
        for target in (node.targets if isinstance(node, ast.Assign) else [node.target])
        if isinstance(target, ast.Name)
    ]
    bound = frozenset({"MIGRATION_API_VERSION"})
    while (
        grown := {name for name, value in assignments if _names_the_version(value, bound)} - bound
    ):
        bound |= grown

    def checks_the_version(node: ast.AST) -> bool:
        return _reads_the_version(node) or _names_the_version(node, bound)

    checks: set[tuple[str, str]] = set()
    for path, tree in trees.items():
        for node in ast.walk(tree):
            match node:
                case ast.Compare(left=left, comparators=rest) if any(
                    map(checks_the_version, (left, *rest))
                ):
                    checks.add((path, ast.unparse(node)))
                case ast.Match(subject=subject, cases=cases) if _reads_the_version(subject) or any(
                    isinstance(pattern, ast.MatchValue) and checks_the_version(pattern.value)
                    for case in cases
                    for pattern in ast.walk(case.pattern)
                ):
                    checks.add((path, f"match {ast.unparse(subject)}"))
    return checks


# -- Reading the documents ----------------------------------------------------


def _collapsed(text: str) -> str:
    """Line wrapping, emphasis and code markers flattened away; they carry no claim."""
    return " ".join(text.replace("*", "").replace("`", "").split())


def _adr() -> Path:
    found = sorted((REPO_ROOT / "docs" / "adr").glob("0038-*.md"))
    assert len(found) == 1, f"expected one ADR-0038 file, found {found}"
    return found[0]


def _section(path: Path, heading: str) -> list[str]:
    lines = path.read_text(encoding="utf-8").splitlines()
    assert heading in lines, f"{path.name} has no heading {heading!r}; this read anchors on it"
    start = lines.index(heading)
    level = len(heading) - len(heading.lstrip("#"))
    ends = (at for at in range(start + 1, len(lines)) if re.match(rf"#{{1,{level}}} ", lines[at]))
    return lines[start + 1 : next(ends, len(lines))]


def _table(lines: Sequence[str]) -> list[tuple[str, ...]]:
    """Header and data rows of the first Markdown table in ``lines``."""
    rows: list[tuple[str, ...]] = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("|"):
            rows.append(tuple(cell.strip() for cell in stripped.strip("|").split("|")))
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
    return {number: _collapsed(" ".join(parts)) for number, parts in items.items()}
