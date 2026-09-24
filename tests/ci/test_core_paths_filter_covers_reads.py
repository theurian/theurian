"""`core.yml`'s paths filter covers every path `tests/ci` reads (#762).

Why this file exists
--------------------
On a push or a pull request, `tests/ci` is selected by exactly one job:
`core.yml`'s `-m "not e2e"` step. (`release-core.yml` runs the whole suite too,
but on a `core-v*` tag -- after the change has landed, and on the release path
rather than as a gate on the commit. `shared.yml` names individual files and
`-m contract`, and reaches nothing here.) So a commit that touches a file one of
these modules *reads* -- and nothing else -- runs none of the pins holding that
file until a release.

**The population, keyed before it is counted.** The key is a *distinct derived
read pattern* -- one entry per pattern, not per reading module and not per probe
-- and the count is over the four sibling modules as they stood at 3d8fe8ba,
before this one existed. Recompute either number by importing this module and
printing ``sorted({pattern for pattern, _, _ in REQUIREMENTS})``, dropping this
file from :func:`_corpus` for the sibling-only figure. Measured that way:
**eight** sibling patterns, of which **four** sat outside the filter --
`CLAUDE.md`, `.github/PULL_REQUEST_TEMPLATE.md`,
`.github/workflows/security.yml`, and the rest of the `.github/workflows/*.yml`
population `test_job_timeouts` globs. The other four were already covered:
`.github/workflows/red-team.yml`, `docs/contributing/orchestration.md`,
`docs/contributing/release.md`, and `tools/**` -- which is the one an earlier
draft of this docstring dropped, reporting seven where its own derivation said
eight. Including this module the figure is ten; its two additions
(`.github/workflows/core.yml`, `tests/ci/*.py`) were covered already.

Both demonstrations recorded in #762 land green on a repository whose pins say
they cannot: an edit to `security.yml` alone, and a new workflow file carrying
no `timeout-minutes` at all.

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
A concrete probe path -- every committable file matching the read today, plus
synthetic names matching it that no current file does -- has to be matched by
`push.paths` *and* `pull_request.paths`. The synthetic probes are what separate
"the filter lists today's workflows" from "the filter covers the directory": the
eighth workflow file is the case #762 was filed for. A `**` read gets two of
them, nested and zero-depth, because `tools/*/**` matches a nested probe while
leaving `tools/mutate.py` uncovered.

"Committable" is `git ls-files --cached --others --exclude-standard`: the files
a push can actually carry, which is the domain GitHub evaluates a paths filter
against. A plain filesystem walk of `tools/**` also returns
`tools/__pycache__/*.pyc` -- five of thirty entries here, none of them in a
fresh clone -- so the parametrized case list would differ between a developer
machine and the runner. A read that resolves to a directory with no glob on it
is an import root, and demands recursive coverage.

A second, unrelated check (PR #797's light pass, MEDIUM-1)
-----------------------------------------------------------
Everything above holds `core.yml`'s top-level `on.paths` to what `tests/ci`
reads. The `retrieval-baseline` job's own `dorny/paths-filter` -- a *second*,
narrower filter, gating one job rather than the whole workflow -- had no
machine check at all: nothing noticed if it stopped covering what
`tools/eval/compare_baseline.py` reads. The functions below reuse this file's
matcher (`_matches`, `_regex`) and probe machinery (`_probes`) against that
filter instead, with their own corpus and their own derivation --
:func:`_retrieval_read_corpus` and :func:`_retrieval_requirements` -- because
the population is different in kind, not just in target: `compare_baseline.py`
does not read `tools/eval/run.py` or `tools/eval/metrics.py` through any
`pathlib.Path` expression the scan above understands, it reaches them through
plain `import` statements, so what counts as "a read" here is *the module
graph*, not path-expression AST. Deliberately shallow: it stops at
`compare_baseline.py`'s own direct imports (`run`, `metrics`) rather than
following `run.py`'s further imports (`corpus.py` reads
`schemas/migrations/migration.schema.json`, a real read and well outside
"retrieval quality") -- see :func:`_retrieval_read_corpus`'s own docstring.
"""

from __future__ import annotations

import ast
import pathlib
import re
import shutil
import subprocess
from typing import Any, Final, cast

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
# GitHub path-filter matching over a restricted alphabet
# --------------------------------------------------------------------------
# Implemented: `*` matches any run of characters *within one segment*, `**`
# matches any run including `/`, and every other character is literal.
#
# NOT implemented, and foreclosed rather than approximated:
# `test_the_filter_uses_only_the_alphabet_the_matcher_implements` rejects any
# entry carrying `?`, `+`, `[` or `]`, and `test_the_filter_carries_no_negated_entry`
# rejects a leading `!`. GitHub's filter-pattern cheat sheet defines `?` as
# "zero or one of the *preceding* character" and `+` as "one or more of the
# preceding character" -- quantifiers, not wildcards:
# https://docs.github.com/en/actions/writing-workflows/workflow-syntax-for-github-actions#filter-pattern-cheat-sheet
# Reading `?` as "one character" (as this file did until the round-1 review)
# made `_matches("a?b", "axb")` true where GitHub says false, which is the
# false-green direction: an entry would have read as covering a path the real
# filter skips. Restricting the alphabet closes that mechanically, where
# widening the matcher would add semantics nothing in this repository uses and
# a fresh way to be wrong about each of them.
#
# Read patterns are a *different* dialect and keep their own `?`: they come from
# `pathlib.Path.glob` calls in the sibling modules, where `?` is one character.
# `_probes` is where that one is handled, and the two must not be conflated.

#: Filter-entry characters this matcher does not implement. `!` is the sibling
#: rule's, because negation changes what a whole list *means* rather than what
#: one entry matches.
UNIMPLEMENTED_METACHARACTERS = "?+[]"


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


def _committable() -> frozenset[str]:
    """Repo-relative paths a push can carry: tracked, plus untracked and not ignored.

    The domain GitHub evaluates a paths filter against, and the reason the probe
    set is the same here and on the runner: a filesystem walk of `tools/**` also
    yields `tools/__pycache__/*.pyc`, which no commit contains and a fresh clone
    does not have. `--others` rather than `--cached` alone so a sibling written
    but not yet `git add`ed is still in the population -- an index-keyed walk
    would drop exactly the new file whose reads this module exists to notice.
    """
    git = shutil.which("git")
    assert git is not None, "git is required to enumerate the committable file set"
    listed = subprocess.run(  # noqa: S603
        [git, "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return frozenset(entry for entry in listed.split("\0") if entry)


COMMITTABLE = _committable()


def _probes(pattern: str) -> list[str]:
    """Concrete paths the filter must match for `pattern` to be covered.

    Today's committable files, so the rule fails on something a reader can open,
    plus the synthetic names below, so a filter that enumerates today's
    population instead of covering it goes red -- which is #762 itself.

    A `**` read gets two synthetics. The nested one alone is satisfied by
    `tools/*/**`, which would leave every top-level `tools/*.py` -- the modules
    `test_red_team_sweep_prose` actually imports -- outside the filter.
    """
    if not any(wildcard in pattern for wildcard in "*?"):
        return [pattern]

    # `?` here is `pathlib`'s "one character", not GitHub's quantifier: these
    # patterns come from the siblings' own `.glob()` calls.
    def _fill(star_star: str) -> str:
        return pattern.replace("**", star_star).replace("*", PROBE).replace("?", "x")

    synthetic = {_fill(f"{PROBE}/{PROBE}")}
    if "**" in pattern:
        synthetic.add(_fill(PROBE))
    for name in synthetic:
        assert not (REPO_ROOT / name).exists(), (
            f"the synthetic probe {name!r} names a file that exists, so it says nothing about a "
            "filter covering paths that do not exist yet; rename PROBE"
        )

    concrete = {p.relative_to(REPO_ROOT).as_posix() for p in REPO_ROOT.glob(pattern)} & COMMITTABLE
    return sorted(synthetic | concrete)


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


@pytest.mark.parametrize("event", ["push", "pull_request"])
def test_the_filter_uses_only_the_alphabet_the_matcher_implements(event: str) -> None:
    """`_matches` reads `?`, `+`, `[` and `]` as literal characters, and GitHub does not.

    On GitHub's filter-pattern cheat sheet `?` is "zero or one of the *preceding*
    character" and `+` is "one or more of the preceding character" -- quantifiers
    over the character before them, not wildcards. Measured on this module's own
    matcher before this rule existed: `_matches("a?b", "axb")` returned True
    where GitHub matches nothing of the sort, and returned False for `"ab"`,
    which GitHub does match. The first of those is the false-green direction --
    an entry reading as covering a path the real filter skips -- and it is
    exactly what the comment above `_regex` used to deny was possible.

    Foreclosing the alphabet is the fix rather than implementing the quantifiers:
    no entry in this repository has ever needed one, and four more branches in
    `_regex` would be four more chances to be wrong about a character whose only
    job is to make this rule pass. Wanting one of them here is a signal to
    implement it in `_regex` *first*, with its own cases in
    :func:`test_the_matcher_agrees_with_github_over_that_alphabet`.
    """
    offenders = {
        entry: sorted(set(entry) & set(UNIMPLEMENTED_METACHARACTERS))
        for entry in _paths_filter(event)
        if set(entry) & set(UNIMPLEMENTED_METACHARACTERS)
    }

    assert offenders == {}, (
        f"{CORE_WORKFLOW.name}'s `{event}.paths` carries {offenders}, and `_matches` reads those "
        "characters literally while GitHub reads them as quantifiers or a character class. Every "
        "coverage answer in this file about such an entry is unreliable, in both directions."
    )


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
        ("tools/*/**", "tools/sweep.py", False),
        ("tests/**", "tests/ci/test_job_timeouts.py", True),
        ("CLAUDE.md", "CLAUDE.md", True),
        ("CLAUDE.md", "docs/CLAUDE.md", False),
    ],
)
def test_the_matcher_agrees_with_github_over_that_alphabet(
    pattern: str, path: str, expected: bool
) -> None:
    """Literals, `*` and `**` -- the whole alphabet, and nothing about `?` or `+`.

    `*` stops at a `/` and `**` does not, which is the reason
    `.github/workflows/*.yml` covers the workflow directory without also covering
    everything under `.github/`. The `tools/*/**` case is the one MEDIUM-2 turned
    on: it is a plausible rewrite of `tools/**` that covers no top-level file.
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


#: The four patterns #762 was filed over, held as a floor under the derivation.
#:
#: Not a restatement of the population -- it is deliberately a *subset*, so a
#: new sibling read still has to be discovered rather than listed, and a module
#: that reads nothing does not make this rule red. What it forecloses is the
#: derivation quietly shrinking: `REQUIREMENTS` is non-empty from this module's
#: own two reads alone, so a sibling whose anchor shape drifts out of
#: `_reads_of`'s reach -- a `parents` chain through an intermediate name, an
#: f-string, a binding this scan does not follow -- would drop every one of its
#: coverage requirements and leave the suite green. That is #762's failure shape
#: rebuilt one level up, in the instrument instead of the filter.
DERIVATION_FLOOR = frozenset(
    {
        "CLAUDE.md",
        ".github/PULL_REQUEST_TEMPLATE.md",
        ".github/workflows/security.yml",
        ".github/workflows/*.yml",
    }
)


def test_the_derivation_still_finds_the_reads_that_762_was_filed_over() -> None:
    """The rule below is parametrized from the scan; a shrunken scan collects less.

    pytest reports zero collected cases as a pass, and a *partial* collapse is
    worse than a total one because the file goes on reporting coverage for
    whatever it still finds.
    """
    derived = {pattern for pattern, _, _ in REQUIREMENTS}

    assert derived >= DERIVATION_FLOOR, (
        f"the derivation no longer reaches {sorted(DERIVATION_FLOOR - derived)}. These are the "
        "reads #762 was filed over, so either a sibling stopped reading one -- in which case drop "
        "it from DERIVATION_FLOOR deliberately -- or the scan stopped understanding how that "
        "sibling names it, and every other read in the same shape has silently gone with it."
    )


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


# --------------------------------------------------------------------------
# The `retrieval-baseline` job's own filter (PR #797, MEDIUM-1)
# --------------------------------------------------------------------------
# See the module docstring's own section for why this is a second, narrower
# check rather than an extension of the scan above.

COMPARE_BASELINE = REPO_ROOT / "tools" / "eval" / "compare_baseline.py"

#: `compare_baseline.py` runs *as a step inside* the job this defines -- no
#: Python expression names this path, so nothing above would ever derive it.
#: The same reasoning #769 already applies to the top-level filter reading its
#: own workflow file.
RETRIEVAL_WORKFLOW_REQUIREMENT: Final = ".github/workflows/core.yml"


def _direct_local_imports(module: pathlib.Path) -> list[pathlib.Path]:
    """Sibling `.py` files `module` imports directly, resolved to paths beside it.

    Not transitive -- see the module docstring's own section for why stopping
    at one level is the deliberate scope, not an oversight.
    """
    tree = ast.parse(module.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None and node.level == 0:
            names.add(node.module)
    return sorted(
        module.parent / f"{name}.py" for name in names if (module.parent / f"{name}.py").exists()
    )


def _retrieval_read_corpus() -> list[pathlib.Path]:
    """`compare_baseline.py` and the `tools/eval` siblings it imports directly.

    `run.py` and `metrics.py` today. Each contributes two kinds of requirement:
    its own file path (compare_baseline.py *imports* it, so an edit to it is an
    edit to what compare_baseline.py runs) and whatever `_patterns` derives
    from its source the same way the scan above does for `tests/ci` -- `run.py`
    builds `DEFAULT_CORPUS` from `REPO_ROOT`, which is exactly that shape.

    Stops at one level of import on purpose. `run.py`'s own further imports
    (`corpus.py`, `corpus_build.py`, `report.py`, `wire.py`) are a different,
    larger population: `corpus.py` alone reads
    `schemas/migrations/migration.schema.json`, a real read and one this rule
    does not claim to cover, because "everything the harness eventually
    touches" is not "a path that can move retrieval quality" -- the filter
    this rule holds to account is deliberately scoped to the latter, by prose,
    in `core.yml`'s own comment.
    """
    return sorted({COMPARE_BASELINE, *_direct_local_imports(COMPARE_BASELINE)})


def _retrieval_filter() -> list[str]:
    """The `retrieval` entry of the `changes` job's own `dorny/paths-filter`.

    A *second* YAML document: `dorny/paths-filter`'s `filters:` step input is a
    string that the action itself parses as YAML at run time, not something
    GitHub's own workflow parser expands -- so covering it means parsing that
    string a second time, deliberately, rather than treating it as opaque text.
    """
    document = cast(dict[str, Any], yaml.safe_load(CORE_WORKFLOW.read_text(encoding="utf-8")))
    jobs = document.get("jobs")
    assert isinstance(jobs, dict), f"{CORE_WORKFLOW.name} has no `jobs:` mapping"
    changes_job = jobs.get("changes")
    assert isinstance(changes_job, dict), f"{CORE_WORKFLOW.name} has no `changes:` job"
    steps = changes_job.get("steps")
    assert isinstance(steps, list), f"{CORE_WORKFLOW.name}'s `changes` job carries no `steps:` list"
    filter_steps = [
        step
        for step in steps
        if isinstance(step, dict) and str(step.get("uses", "")).startswith("dorny/paths-filter@")
    ]
    assert len(filter_steps) == 1, (
        f"{CORE_WORKFLOW.name}'s `changes` job carries {len(filter_steps)} `dorny/paths-filter` "
        "step(s), not the 1 this function assumes"
    )
    filters_text = filter_steps[0].get("with", {}).get("filters")
    assert isinstance(filters_text, str), (
        "the `dorny/paths-filter` step carries no `filters:` string"
    )
    embedded = cast(dict[str, Any], yaml.safe_load(filters_text))
    retrieval = embedded.get("retrieval")
    assert isinstance(retrieval, list) and retrieval, "the `retrieval` filter is empty or missing"
    return [str(entry) for entry in retrieval]


def _retrieval_requirements() -> list[tuple[str, str, str]]:
    """`(pattern, probe, readers)`, the same shape as :data:`REQUIREMENTS`, over the
    `retrieval-baseline` job's own filter instead of the top-level one.
    """
    citations: dict[str, set[str]] = {
        RETRIEVAL_WORKFLOW_REQUIREMENT: {"compare_baseline.py (runs inside this job)"}
    }
    for module in _retrieval_read_corpus():
        citations.setdefault(module.relative_to(REPO_ROOT).as_posix(), set()).add(
            "compare_baseline.py (direct import)"
        )
        for pattern in _patterns(module):
            citations.setdefault(pattern, set()).add(module.name)
    return [
        (pattern, probe, ", ".join(sorted(readers)))
        for pattern, readers in sorted(citations.items())
        for probe in _probes(pattern)
    ]


RETRIEVAL_REQUIREMENTS = _retrieval_requirements()


def test_the_retrieval_derivation_is_not_vacuous() -> None:
    """An anti-vacuity control for :data:`RETRIEVAL_REQUIREMENTS` -- the same shape as
    :func:`test_the_derivation_still_finds_the_reads_that_762_was_filed_over`, sized to what this
    corpus is known to contain today rather than carrying over the unrelated #762 floor.
    """
    derived = {pattern for pattern, _, _ in RETRIEVAL_REQUIREMENTS}

    assert derived >= {
        "tools/eval/compare_baseline.py",
        "tools/eval/run.py",
        "tools/eval/metrics.py",
        "tests/fixtures/eval/**",
        RETRIEVAL_WORKFLOW_REQUIREMENT,
    }, (
        f"the retrieval-baseline derivation only reaches {sorted(derived)}. Either "
        "`compare_baseline.py` stopped importing `run`/`metrics`, or the scan stopped "
        "understanding how it does."
    )


@pytest.mark.parametrize(
    ("pattern", "probe", "readers"),
    RETRIEVAL_REQUIREMENTS,
    ids=[f"{pattern}::{probe}" for pattern, probe, _ in RETRIEVAL_REQUIREMENTS],
)
def test_every_path_compare_baseline_reads_is_covered_by_the_retrieval_filter(
    pattern: str, probe: str, readers: str
) -> None:
    """MEDIUM-1 (PR #797's light pass): the `retrieval-baseline` job only runs when its own
    filter says so, and nothing previously checked that the filter kept covering what
    `compare_baseline.py` reads.
    """
    entries = _retrieval_filter()
    assert any(_matches(entry, probe) for entry in entries), (
        f"the `changes` job's `retrieval` filter matches nothing for {probe!r}. {readers} reads "
        f"{pattern!r}, and `retrieval-baseline` is gated on that filter alone, so a commit "
        f"touching that path never runs the comparison. Add an entry covering {pattern!r}."
    )
