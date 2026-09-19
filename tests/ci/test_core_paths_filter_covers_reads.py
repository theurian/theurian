"""`core.yml`'s paths filter covers every path `tests/ci` reads (#762).

Why this file exists
--------------------
On a push or a pull request, `tests/ci` is selected by exactly one job:
`core.yml`'s `-m "not e2e"` step. (`release-core.yml` runs the whole suite too,
but on a `core-v*` tag -- after the change has landed, and on the release path
rather than as a gate on the commit. `shared.yml` names individual files and
`-m contract`, and reaches nothing here.) So a commit that touches a file one of
these modules *reads* -- and nothing else -- runs none of the pins holding that
file until a release. Measured at 3d8fe8ba, four of the seven
read paths sat outside the filter: `CLAUDE.md`,
`.github/PULL_REQUEST_TEMPLATE.md`, `.github/workflows/security.yml`, and the
rest of the `.github/workflows/*.yml` population `test_job_timeouts` globs. Both
demonstrations recorded in #762 land green on a repository whose pins say they
cannot: an edit to `security.yml` alone, and a new workflow file carrying no
`timeout-minutes` at all.

The corpus this rule walks
--------------------------
Every `tests/ci/*.py`, this module included -- the whole population of modules
that step runs out of this directory, joined by a new sibling *existing* rather
than by being listed here. `tests/release/` and the rest of `tests/` are out of
scope on purpose: `tests/**` already reaches them as subjects, and what they
read is a separate question from the one #762 asked.

How the read set is derived
---------------------------
By parsing each module, never by restating its constants: a list written here
would agree with itself while a sibling grew a sixth read. The scan understands
one shape -- a `/` chain of string literals rooted at a name bound to
`pathlib.Path(__file__).resolve().parents[N]` that resolves to the repository
root, or at a module-level alias of such a chain -- plus `.glob()`/`.rglob()`
called on one. A function body is in reach (`REPO_ROOT / "tools"` is found); a
path assembled by an f-string, by `os.path.join`, or by a loop over names is
**not**, and that is a hole in this rule rather than in the filter.
:func:`test_the_resolver_finds_every_shape_it_claims_to` is the instrument's own
positive control, run against a synthetic source rather than against the live
corpus so that proving the scan works does not freeze what it finds.

What each read demands of the filter
------------------------------------
A concrete probe path -- every file matching the read today, plus one synthetic
name matching it that no current file does -- has to be matched by `push.paths`
*and* `pull_request.paths`. The synthetic probe is what separates "the filter
lists today's workflows" from "the filter covers the directory": the eighth
workflow file is the case #762 was filed for. A read that resolves to a
directory with no glob on it is an import root, and demands recursive coverage.
"""

from __future__ import annotations

import ast
import pathlib
import re
from typing import Any, cast

import pytest
import yaml

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
TESTS_CI = REPO_ROOT / "tests" / "ci"
CORE_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "core.yml"

#: Stands in for a file that does not exist yet. Substituted into a derived
#: pattern's wildcards, it is the future workflow, the future test module and
#: the future `tools/` script all at once.
PROBE = "zz-probe-that-does-not-exist"

#: A path expression, resolved to repo-relative segments.
Segments = tuple[str, ...]


# --------------------------------------------------------------------------
# GitHub's own path-filter matching, minimally
# --------------------------------------------------------------------------
# `*` matches any run of characters *within one segment*; `**` matches any run
# including `/`; `?` matches one character other than `/`. Everything else is
# literal -- a `[` is escaped rather than opened as a character class, which can
# only under-match and so can only produce a false red, never a false green.
# Negation (`!`) is not implemented, and `test_the_filter_carries_no_negated_entry`
# is what keeps that honest.


def _regex(pattern: str) -> str:
    out: list[str] = []
    index = 0
    while index < len(pattern):
        if pattern.startswith("**", index):
            out.append(".*")
            index += 2
        elif pattern[index] == "*":
            out.append("[^/]*")
            index += 1
        elif pattern[index] == "?":
            out.append("[^/]")
            index += 1
        else:
            out.append(re.escape(pattern[index]))
            index += 1
    return "".join(out)


def _matches(pattern: str, path: str) -> bool:
    return re.fullmatch(_regex(pattern), path) is not None


# --------------------------------------------------------------------------
# The filter
# --------------------------------------------------------------------------


def _triggers() -> dict[str, Any]:
    """`core.yml`'s `on:` block.

    YAML 1.1 reads a bare `on` as the boolean true, which is what
    `yaml.safe_load` implements; both keys are accepted so a loader on YAML 1.2
    rules does not turn this into a failure about nothing.
    """
    document = cast(dict[str, Any], yaml.safe_load(CORE_WORKFLOW.read_text(encoding="utf-8")))
    for key in (True, "on"):
        if key in document:
            return cast(dict[str, Any], document[key])
    raise AssertionError(f"{CORE_WORKFLOW} has no `on:` block at all")


def _paths_filter(event: str) -> list[str]:
    block = _triggers().get(event)
    assert isinstance(block, dict), f"{CORE_WORKFLOW.name} has no `{event}:` trigger"
    paths = block.get("paths")
    assert isinstance(paths, list) and paths, (
        f"{CORE_WORKFLOW.name}'s `{event}:` carries no `paths:` list. An absent filter runs the "
        "job on everything, which passes this file for the wrong reason -- say so deliberately "
        "and delete this rule rather than letting it read as a pin"
    )
    return [str(entry) for entry in paths]


# --------------------------------------------------------------------------
# The read set
# --------------------------------------------------------------------------


def _corpus() -> list[pathlib.Path]:
    modules = sorted(TESTS_CI.glob("*.py"))
    assert modules, f"no modules found under {TESTS_CI}; is the glob still correct?"
    return modules


def _binding(statement: ast.stmt) -> tuple[str, ast.expr] | None:
    """A module-level `NAME = <expr>`, annotated or not."""
    if isinstance(statement, ast.Assign) and len(statement.targets) == 1:
        target = statement.targets[0]
        if isinstance(target, ast.Name):
            return target.id, statement.value
    elif isinstance(statement, ast.AnnAssign) and statement.value is not None:
        if isinstance(statement.target, ast.Name):
            return statement.target.id, statement.value
    return None


def _is_repo_root(node: ast.expr, module: pathlib.Path) -> bool:
    """`pathlib.Path(__file__).resolve().parents[N]`, where N reaches this root.

    The index is evaluated against the module's own location rather than
    assumed, so a file that moves deeper and re-counts stays understood, and a
    `.parents[1]` pointing at `tests/` is correctly not a repository root.
    """
    if not (isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Constant)):
        return False
    depth = node.slice.value
    value = node.value
    if not isinstance(depth, int) or not isinstance(value, ast.Attribute):
        return False
    if value.attr != "parents" or not any(
        isinstance(inner, ast.Name) and inner.id == "__file__" for inner in ast.walk(value)
    ):
        return False
    parents = module.resolve().parents
    return depth < len(parents) and parents[depth] == REPO_ROOT


def _segments(node: ast.expr, names: dict[str, Segments]) -> Segments | None:
    """Repo-relative segments of a path expression, or `None` if it is not one."""
    if isinstance(node, ast.Name):
        return names.get(node.id)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
        left = _segments(node.left, names)
        right = node.right
        if left is None or not isinstance(right, ast.Constant) or not isinstance(right.value, str):
            return None
        return (*left, right.value)
    return None


def _anchored_names(tree: ast.Module, module: pathlib.Path) -> dict[str, Segments]:
    names: dict[str, Segments] = {}
    for statement in tree.body:
        binding = _binding(statement)
        if binding is None:
            continue
        name, value = binding
        if _is_repo_root(value, module):
            names[name] = ()
            continue
        segments = _segments(value, names)
        if segments is not None:
            names[name] = segments
    return names


def _glob(node: ast.AST, names: dict[str, Segments]) -> tuple[Segments, str] | None:
    """A `.glob("...")` / `.rglob("...")` call on a repo-anchored path."""
    if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.args):
        return None
    if node.func.attr not in ("glob", "rglob"):
        return None
    argument = node.args[0]
    base = _segments(node.func.value, names)
    if base is None or not isinstance(argument, ast.Constant):
        return None
    if not isinstance(argument.value, str):
        return None
    pattern = argument.value
    return base, f"**/{pattern}" if node.func.attr == "rglob" else pattern


def _reads(module: pathlib.Path) -> tuple[set[Segments], set[tuple[Segments, str]]]:
    return _reads_of(module.read_text(encoding="utf-8"), module)


def _reads_of(source: str, module: pathlib.Path) -> tuple[set[Segments], set[tuple[Segments, str]]]:
    """Every repo-anchored path the source names, and every glob it runs.

    `module` is where the source *sits*, not where it is read from: the anchor
    depth is evaluated against it. Passing the two separately is what lets the
    positive control parse a synthetic source as if it were a sibling without
    writing a file into `tests/ci`.

    Only the outermost expression of a `/` chain counts: `REPO_ROOT / "docs"`
    inside `REPO_ROOT / "docs" / "x.md"` is the prefix of a read, not a read.
    """
    tree = ast.parse(source)
    names = _anchored_names(tree, module)
    nested = {
        id(node.left)
        for node in ast.walk(tree)
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div)
    }

    paths: set[Segments] = set()
    globs: set[tuple[Segments, str]] = set()
    for node in ast.walk(tree):
        found = _glob(node, names)
        if found is not None:
            globs.add(found)
        if id(node) in nested or not isinstance(node, ast.BinOp | ast.Name):
            continue
        segments = _segments(node, names)
        if segments:
            paths.add(segments)
    return paths, globs


def _patterns(module: pathlib.Path) -> set[str]:
    """What the filter has to cover for this module, as repo-relative patterns."""
    paths, globs = _reads(module)
    patterns = {"/".join((*base, pattern)) for base, pattern in globs}
    glob_bases = {base for base, _ in globs}
    for segments in paths - glob_bases:
        joined = "/".join(segments)
        # A directory nothing globs is an import root: the module reaches every
        # file under it, so `dir/*` is not enough.
        patterns.add(f"{joined}/**" if (REPO_ROOT / joined).is_dir() else joined)
    return patterns


def _probes(pattern: str) -> list[str]:
    """Concrete paths the filter must match for `pattern` to be covered.

    Today's files, so the rule fails on something a reader can open, plus one
    synthetic name matching the same pattern and no current file, so a filter
    that enumerates today's population instead of covering it goes red -- which
    is #762 itself.
    """
    if not any(wildcard in pattern for wildcard in "*?"):
        return [pattern]

    synthetic = pattern.replace("**", f"{PROBE}/{PROBE}").replace("*", PROBE).replace("?", "x")
    assert not (REPO_ROOT / synthetic).exists(), (
        f"the synthetic probe {synthetic!r} names a file that exists, so it says nothing about a "
        "filter covering paths that do not exist yet; rename PROBE"
    )
    concrete = (
        []
        if "**" in pattern
        else [p.relative_to(REPO_ROOT).as_posix() for p in REPO_ROOT.glob(pattern)]
    )
    return sorted({synthetic, *concrete})


def _requirements() -> list[tuple[str, str, str]]:
    """`(pattern, probe, readers)`, one row per concrete path the filter must match."""
    citations: dict[str, set[str]] = {}
    for module in _corpus():
        for pattern in _patterns(module):
            citations.setdefault(pattern, set()).add(module.name)
    return [
        (pattern, probe, ", ".join(sorted(readers)))
        for pattern, readers in sorted(citations.items())
        for probe in _probes(pattern)
    ]


REQUIREMENTS = _requirements()


# --------------------------------------------------------------------------
# Rules
# --------------------------------------------------------------------------

#: The resolver's positive control. Every shape the module docstring claims to
#: understand, in a source the live corpus cannot change: the root binding, an
#: alias of it, a file read, a top-level file, a glob on the alias, and a chain
#: built inside a function body. `SHALLOW` is the negative half -- `parents[1]`
#: is `tests/`, so `NOT_A_READ` must not be reported.
SYNTHETIC_SOURCE = """
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[2]
DIR = ROOT / ".github" / "workflows"
FILE = ROOT / "docs" / "contributing" / "release.md"
TOP = ROOT / "CLAUDE.md"
SHALLOW = pathlib.Path(__file__).resolve().parents[1]
NOT_A_READ = SHALLOW / "not-the-repository-root"


def _read() -> object:
    return str(ROOT / "tools"), sorted(DIR.glob("*.yml")), FILE.read_text(), TOP.read_text()
"""


def test_the_resolver_finds_every_shape_it_claims_to() -> None:
    """A scan that silently found nothing would leave every rule below vacuous.

    Run against a synthetic source rather than the live corpus, so the control
    does not double as a frozen copy of the population it is meant to discover.
    The depth check is the half that is easy to lose: a resolver treating any
    `.parents[...]` as the repository root would report `NOT_A_READ`, and the
    rule below would then demand filter coverage for a path that does not exist.

    Parsed as if it sat where this module sits, which it does: naming a
    fictional sibling instead would be a repo-anchored path expression in this
    file, and :func:`test_every_derived_read_resolves_in_the_tree` would read it
    as a read of a module that was never written.
    """
    paths, globs = _reads_of(SYNTHETIC_SOURCE, pathlib.Path(__file__))

    assert paths == {
        (".github", "workflows"),
        ("docs", "contributing", "release.md"),
        ("CLAUDE.md",),
        ("tools",),
    }
    assert globs == {((".github", "workflows"), "*.yml")}


def test_push_and_pull_request_filter_on_the_same_paths() -> None:
    """Two lists that can drift are two filters, and only one of them is read.

    A path added to `pull_request` alone leaves every push to `main` skipping
    the job that holds it -- and the tag cut reads `main`.
    """
    assert _paths_filter("push") == _paths_filter("pull_request")


def test_the_filter_carries_no_negated_entry() -> None:
    """`_matches` implements no negation, and would read `!x` as a literal.

    An excluded path would then count as covered here while GitHub skipped the
    job for it -- the one direction this file must not fail in. If negation is
    ever wanted, it belongs in `_regex` before it belongs in the workflow.
    """
    negated = [entry for entry in _paths_filter("push") if entry.startswith("!")]

    assert negated == []


@pytest.mark.parametrize(
    ("pattern", "path", "expected"),
    [
        (".github/workflows/*.yml", ".github/workflows/core.yml", True),
        (".github/workflows/*.yml", ".github/workflows/nested/core.yml", False),
        (".github/workflows/*.yml", ".github/workflows/core.yaml", False),
        (".github/workflows/core.yml", ".github/workflows/core.yml", True),
        (".github/workflows/core.yml", ".github/workflows/security.yml", False),
        ("tools/**", "tools/sweep.py", True),
        ("tools/**", "tools/nested/deep/script.py", True),
        ("tools/*", "tools/nested/deep/script.py", False),
        ("tests/**", "tests/ci/test_job_timeouts.py", True),
        ("CLAUDE.md", "CLAUDE.md", True),
        ("CLAUDE.md", "docs/CLAUDE.md", False),
    ],
)
def test_the_matcher_implements_githubs_wildcard_semantics(
    pattern: str, path: str, expected: bool
) -> None:
    """`*` stops at a `/` and `**` does not -- the whole reason `.github/workflows/*.yml`
    covers the workflow directory without also covering everything under `.github/`.
    """
    assert _matches(pattern, path) is expected


def test_every_derived_read_resolves_in_the_tree() -> None:
    """A read pointing at nothing is either a stale sibling or a broken scan.

    Both leave the rule below asserting coverage of a path that cannot be
    edited, which is coverage of nothing.
    """
    missing = sorted(
        f"{module.name}: {'/'.join(segments)}"
        for module in _corpus()
        for paths, globs in [_reads(module)]
        for segments in paths | {base for base, _ in globs}
        if not (REPO_ROOT / "/".join(segments)).exists()
    )

    assert missing == []


def test_the_derivation_produced_requirements_at_all() -> None:
    """The rule below is parametrized from the scan; an empty scan collects nothing.

    pytest reports zero collected cases as a pass, so without this the whole
    file could go quiet in exactly the way #762 describes.
    """
    assert REQUIREMENTS


@pytest.mark.parametrize(
    ("pattern", "probe", "readers"),
    REQUIREMENTS,
    ids=[f"{pattern}::{probe}" for pattern, probe, _ in REQUIREMENTS],
)
def test_every_path_tests_ci_reads_is_covered_by_the_filter(
    pattern: str, probe: str, readers: str
) -> None:
    """The rule #762 asked for, one read at a time.

    A path `tests/ci` reads but the filter misses is a file whose only
    pre-release check is skipped by the very commit that changes it, and the
    skip is reported as a green tick.
    """
    for event in ("push", "pull_request"):
        entries = _paths_filter(event)
        assert any(_matches(entry, probe) for entry in entries), (
            f"{CORE_WORKFLOW.name}'s `{event}.paths` matches nothing for {probe!r}. "
            f"{readers} reads {pattern!r}, and this is the only workflow running `tests/ci` on a "
            f"push or a pull request, so a commit touching that path alone runs none of its own "
            f"pins. Add an entry covering {pattern!r} to both `push.paths` and "
            "`pull_request.paths`."
        )
