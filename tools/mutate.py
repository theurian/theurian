"""Break the source on purpose and find out whether the suite notices.

A mutation harness answers exactly one question: *does anything, anywhere,
catch this change?* A mutation the suite still passes -- SURVIVED -- names a
property nothing in the repository holds. That is the outcome worth acting on,
so it is the outcome this tool is built to get right.

Usage
-----
One mutation, given inline::

    uv run python tools/mutate.py \\
        --file packages/theurian-core/src/theurian/infrastructure/sqlite/index_scan.py \\
        --old 'SCAN_TERMS: Final = 8' --new 'SCAN_TERMS: Final = 4' \\
        --label scan-terms-four

Multi-line anchors are easier to pass as files::

    uv run python tools/mutate.py --file <path> --old-file before.txt --new-file after.txt

A batch, across four isolated trees, writing results as they land::

    uv run python tools/mutate.py --spec mutations.json --workers 4 --json results.json

where ``mutations.json`` is a list of ``{"label", "file", "old", "new"}``
objects and ``file`` is repository-relative.

A mutation may carry several edits that land together, as ``{"label", "edits":
[{"file", "old", "new"}, ...]}``. Use it when the hypothesis needs more than one
change to be *stated at all* -- "does this guard still catch the defect once the
walker is weakened" is only asked by weakening the walker and reintroducing the
defect in the same tree, and split across two labels each is killed by the
other's absence and the question goes unanswered. Every edit is anchored,
digested and restored exactly like a single one, and a composite that fails
halfway unwinds what it landed rather than leaving a tree the next job borrows.

A prepared tree, which applies the mutation and then runs *nothing*, so you can::

    tree=$(uv run python tools/mutate.py --prepare-tree \\
        --file packages/theurian-core/src/theurian/domain/ranking.py \\
        --old 'reverse=True' --new 'reverse=False')
    sh "$tree/.mutate-run" packages/theurian-core/tests/unit/test_ranking.py -x
    rm -rf "$(dirname "$tree")"

``--prepare-tree`` writes the tree's absolute path to stdout and every other
line to stderr, so ``$(...)`` captures the path and nothing else.

Exit status is ``0`` when every mutation was killed, ``1`` when at least one
survived or the suite hung under it (a hung suite cannot go RED for that
mutation, so it is reported as ``HUNG`` rather than folded into either
verdict), and ``2`` when the run itself cannot be trusted -- an anchor that did
not match, a restore that did not restore, an unreadable summary line, a
control run that was not green, or a mutation that reached the real checkout.

Which mode do you want
----------------------
**While writing a test: ``--prepare-tree``.** The question then is "does *this*
test catch *this* mutation", you will ask it ten times, and on the verdict path
each answer costs a full suite -- about 150 s. The tree is copied, synced and
mutated once; every iteration after that is one selected pytest run inside it,
at a few seconds each.

**Before believing a result: the verdict path** -- ``--file`` or ``--spec``
*without* ``--prepare-tree``. It runs the whole suite, and only the whole suite
answers the question a mutation harness exists to ask.

The two are not interchangeable, which is why the prepared tree reports no
verdict at all: it prints a path, never a KILLED or a SURVIVED. What you see
inside a prepared tree is a lead. Turn it into a verdict on the verdict path.

Why it is built this way
------------------------
Each of the following cost this repository a wrong answer at least once. Only
``-x`` is about speed; the rest are about not reporting a result that is false.

**The whole suite runs, always -- on the verdict path.** There is deliberately
no ``--tests`` option, no way to narrow the selection of a run that produces a
KILLED or a SURVIVED. A narrowed run assumes the answer to the question being
asked. During Milestone 5, dropping the outer ``lower()`` from
``_matched_characters`` came back SURVIVED and no plausible narrowed selection
would have found that; a test was then written, and the same mutation is killed
today. Which tests catch a given mutation is exactly what is not known in
advance.

``--prepare-tree`` is not an exception to this. It hands back a mutated tree
and stops, so anything you run inside it is yours, unlabelled, and never
reported as a verdict by this tool.

**``-x`` by default -- and drop it more often than that sounds.**
``--no-fail-fast`` reads as being for when you want the complete failure list.
In practice it is what you want *whenever the question is which test catches a
mutation*, because fail-fast stops at whichever catcher comes first in
collection order and that is rarely the test you just wrote. Keep ``-x`` when
the question is *whether* anything catches it; drop it when the question is
*what* does.

*Position is not time.* Catchers sit at 15.5-32.1% of collection order, but the
early band is integration-heavy and slow, so the saving is not that fraction.
On the eight-mutation replay below, killed runs took 32.8 s to 93.1 s against a
130 s full walk -- a wide spread that depends entirely on where the catcher
lands. The useful consequence is that **a batch's wall clock has a floor of one
full-suite run**, because every survivor and the control walk everything. Adding
mutations that will be killed is nearly free; adding survivors is not.

**An isolated copy per worker, made with ``shutil.copytree``.** Do *not* reach
for ``git worktree add HEAD``. This checkout routinely carries untracked new
source files -- during Milestone 5 that was ``index_scan.py``, ``index_query.py``,
``visibility.py`` and ``index_builder.py`` -- and a worktree cut from ``HEAD``
lacks them, so every run inside it is garbage that looks like a result. The copy
also means the real checkout is never written to, which is what makes running
batches concurrently safe.

**``PYTHONDONTWRITEBYTECODE=1``, and ``__pycache__`` is cleared.** CPython
validates a cached ``.pyc`` against ``(source mtime in whole seconds, source
size)``. A constant mutation that keeps the source the same length and is
written back inside the same second is therefore *silently ignored*, and the
run reports a false SURVIVED. This happened during Milestone 5. Constant
mutations are the most exposed case, because a replacement digit usually has
the same width as the original.

**sha256 before and after.** Confirms the mutation reached the file and that
the restore put it back byte for byte. The limitation is worth stating: a
before/after hash *cannot* detect another process writing to the file during
the window, because the restored hash matches the hash this process took. Only
the isolated copy removes that hazard -- which is the second reason for it.

**The integrity check watches the mutation set, not the whole tree.** Before
and after, the harness digests exactly the paths this run intends to mutate *as
they exist in the real checkout*. Exit 2 means one of them moved, which is
either a mis-specified path that wrote to the live tree or a source file that
changed underneath a batch whose trees were copied at different moments. Both
make every verdict in the batch worthless, and nothing else does.

It used to diff the whole ``git status --porcelain`` output, and with several
agents working one checkout that fired on runs that were entirely sound: a
nine-mutation batch came back all KILLED with a GREEN control and still exited
2, because unrelated files had been edited while it ran. A signal that is wrong
in the common case is not read in the rare one. Everything that moved *outside*
the mutation set is still reported -- by name, so the line is worth reading --
but as a note, and it does not touch the exit code.

The narrowing is only sound because a mutation cannot reach a file the check is
not watching, so anchor paths that are absolute-outside-the-repository or
contain ``..`` are rejected before anything is copied: ``tree / "/etc/passwd"``
is ``/etc/passwd``.

**A control run.** One job in every batch applies no mutation at all and must
come back GREEN. If the tree is already RED, every KILLED verdict in the batch
is meaningless, and without the control that is invisible. It runs as an
ordinary job, so with two or more workers it costs almost no wall clock.

**No ``pytest-xdist``.** Evaluated and rejected; do not re-evaluate it from
scratch. It is not a dependency, the e2e tests take real ports via
``_free_port()``, spawn subprocesses and shell out to ``lsof``, and
``test_mcp_tools.py``'s ``three_indexes`` fixture builds three real projects at
module scope at roughly 2.9 s per corpus. If it is ever wanted it needs
``--dist=loadscope``, and it needs these isolated trees first.

The port hazard is the reason to keep ``--workers`` modest. ``_free_port()``
binds port 0, reads the number and closes the socket, so concurrent suites can
in principle be handed the same ephemeral port. Four workers has been exercised;
a much larger number has not.

**``uv run``, not ``.venv/bin/pytest``.** Measured at 1.088 s against 1.071 s
for the direct interpreter -- no effect worth the loss of ``--frozen``.

Measured
--------
Eight mutations replayed from Milestone 5's eighth review round -- seven that
the suite catches, one it does not -- on an Apple silicon laptop, 1407 tests:

===================================================  ==========
``--workers 1 --no-fail-fast --no-control``            1043.7 s
``--workers 4`` (the defaults, plus the control run)    253.4 s
===================================================  ==========

Same eight verdicts either way, and the faster run does strictly more work: it
adds the unmutated control. Building the four trees cost under a second each,
because the copy is 3.8 MB without ``.git`` and ``.venv``, and ``uv sync
--frozen`` restores the virtualenv from cache in about 0.8 s.

The two modes on one mutation -- ``3-scan-tiebreak-gone``, dropping the
``chunks.chunk_id`` tiebreak from the below-floor scan -- at 1431 tests:

=======================================================  =========
``--prepare-tree``, then three selected runs inside it       2.6 s
the verdict path, ``--workers 2`` (control plus mutation)  139.7 s
=======================================================  =========

The prepared tree cost 0.7 s and each selected run 0.6-1.1 s. They are not the
same measurement and the table is not an argument for the cheaper one: the
139.7 s bought the fact that nothing *else* in 1431 tests depends on that
tiebreak, and the 2.6 s bought none of it. It is an argument for not paying
139.7 s ten times over while still deciding what to assert.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import queue
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import traceback
from dataclasses import asdict
from pathlib import Path
from typing import Final

from mutate_checkout import _porcelain_entries, _report_checkout
from mutate_edits import (
    Applied,
    HarnessError,
    Mutation,
    _apply,
    _changed_targets,
    _digest_targets,
)
from mutate_run import Options, Outcome, _child_env, _run_one, _uv
from mutate_spec import _mutations_from

REPO_ROOT: Final = Path(__file__).resolve().parents[1]

# `.git` is excluded deliberately: the copy is not a repository, and the suite
# has been run without one. `.venv` is excluded because `uv sync --frozen`
# rebuilds it from uv's cache in under a second, against several seconds to
# copy 149 MB -- and a copied venv keeps an editable `.pth` pointing back at
# the real checkout, which would make every mutation a silent no-op.
_COPY_IGNORE: Final = shutil.ignore_patterns(
    "__pycache__",
    ".git",
    ".venv",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".coverage",
    "dist",
    "htmlcov",
    "node_modules",
)

_CONTROL_LABEL: Final = "__control__"
_DEFAULT_TIMEOUT_SECONDS: Final = 1800

# Dropped into a prepared tree. Dot-prefixed to stay clear of anything in the
# suite that walks the repository root.
_RUNNER_NAME: Final = ".mutate-run"

# The source checkout's tracked file list, recorded in the copy because the copy
# has no `.git` to be asked. A contract with
# `packages/theurian-core/tests/command_population.py`, which reads this file by
# this name and takes it over its own name-based guess -- the format is exactly
# `git ls-files --cached -z` output, so both ends parse it the same way.
# `packages/theurian-core/tests/unit/test_dogfood_corpus_governance.py` is the
# second reader, and it needs the manifest for a different reason: without one
# its `git` calls exit 128 in a copy, 11 of its rules fail, and the batch's own
# unmutated control goes RED.
#
# Without it that guess drops the whole repository-root `.theurian/`, which on
# the dogfood corpus branch is 81 tracked files -- 26 knowledge documents, 26
# migrations, 26 proposal evidence files and 3 `.gitkeep` placeholders (measured
# 2026-08-20) -- 78 of them with a suffix the scan reads, against a scanned
# population of 321. The harness would then run every verdict against 24% less
# than the gate it stands in for, and say nothing about the difference.
_POPULATION_NAME: Final = ".mutate-population"


# The same class of variable `command_population._INHERITED_GIT_OVERRIDES`
# drops, and for the same reason: `GIT_INDEX_FILE` binds the index while the
# `-C` argument binds the working tree, so an inherited one answers for another
# tree and `ls-files` comes back empty. Git exports these to hooks, and a harness
# started from one would record a manifest belonging to somebody else's index.
_INHERITED_GIT_OVERRIDES: Final = frozenset(
    {
        "GIT_ALTERNATE_OBJECT_DIRECTORIES",
        "GIT_COMMON_DIR",
        "GIT_CONFIG",
        "GIT_CONFIG_COUNT",
        "GIT_CONFIG_GLOBAL",
        "GIT_CONFIG_SYSTEM",
        "GIT_DIR",
        "GIT_INDEX_FILE",
        "GIT_OBJECT_DIRECTORY",
        "GIT_WORK_TREE",
    }
)


def _is_complete(listing: bytes) -> bool:
    """Whether a manifest is a whole one.

    ``ls-files -z`` terminates *every* entry including the last, so a listing
    that does not end in a NUL was cut short -- a partial write, a full disk, a
    harness killed between the two. Empty fails the same test, which is the
    point: an empty manifest is a population of nothing, and the reader would
    otherwise adopt it as the answer.
    """
    return listing.endswith(b"\0")


def _git_in_source(*arguments: str) -> subprocess.CompletedProcess[bytes] | None:
    """One read-only git command in the source checkout, or ``None`` if it cannot run.

    ``None`` rather than an exception for the two ways running it is impossible:
    no git on ``PATH``, and a git that cannot be executed. The second is not
    theoretical -- a ``PATH`` entry that has gone away raises ``OSError`` from
    ``subprocess``, and an uncaught one here would walk straight past
    ``_prepare_mode``'s ``except HarnessError`` and leave the work root behind.
    """
    git = shutil.which("git")
    if git is None:
        return None
    try:
        return subprocess.run(  # noqa: S603 - argv is harness-owned, never user input
            [git, "-C", str(REPO_ROOT), *arguments],
            capture_output=True,
            check=False,
            env={
                name: value
                for name, value in os.environ.items()
                if name not in _INHERITED_GIT_OVERRIDES
            },
        )
    except OSError:
        return None


def _tracked_paths() -> bytes | None:
    """``git ls-files --cached -z`` for the source checkout, or ``None``.

    **Three guards, and the reader holds the same three** -- see
    ``_git_listing`` and ``_manifest_listing`` in
    ``packages/theurian-core/tests/command_population.py``. They are duplicated
    rather than shared because ``tools/`` and the core's test tree reach
    ``sys.path`` by different mechanisms and neither imports the other; the two
    sites name each other so a change to one is a search away from the other.

    1. **The environment is the harness's, not the caller's.** ``GIT_INDEX_FILE``
       binds the index while ``-C`` binds the working tree, so an inherited one
       answers from another index. Git exports it to hooks.
    2. **The toplevel must be this checkout.** A source that is not a repository
       but sits *inside* one is answered for by the outer repository, and the
       answer is not empty -- measured on a scratch tree, ``ls-files`` exits 0
       and lists the outer repository's paths *relative to the source*,
       including a file the source itself does not track. Neither the exit code
       nor the emptiness check sees it; only comparing the toplevel does.
    3. **The listing must be whole.** A zero exit with nothing on stdout is what
       an empty or foreign index looks like, and a 0-byte manifest tells the
       copy that this repository ships nothing.
    """
    toplevel = _git_in_source("rev-parse", "--show-toplevel")
    if toplevel is None or toplevel.returncode != 0:
        return None
    named = toplevel.stdout.decode("utf-8", "surrogateescape").strip()
    if not named or Path(named).resolve() != REPO_ROOT.resolve():
        return None
    listing = _git_in_source("ls-files", "--cached", "-z")
    if listing is None or listing.returncode != 0 or not _is_complete(listing.stdout):
        return None
    return listing.stdout


def _record_population(destination: Path) -> None:
    """Write the source checkout's tracked paths into the copy, or refuse to build.

    **A tree whose population cannot be recorded raises rather than degrading**,
    and that is a decision worth stating because the alternative looks kinder.
    Printing a warning and letting the copy fall back to its name-based guess
    means every verdict in the batch is computed against a population the real
    gate does not have -- 78 scanned files fewer on the corpus branch, about a
    third of it -- while each verdict still reads as an ordinary KILLED or
    SURVIVED. Nothing downstream can tell. The harness already refuses to build
    a tree whose virtualenv it cannot make; a tree whose *suite* it cannot make
    honest is the same kind of refusal, and ``_prepare_mode`` already unwinds
    the work root on ``HarnessError``.

    The one case that is not a degrade: a copy of a tree that was itself
    prepared carries the manifest its source carried, which lists the same
    files. That is kept, and said out loud on stderr -- ``--prepare-tree`` puts
    the tree's path on stdout and nothing else, so commentary that lands there
    would be captured by ``$(...)`` and break the caller's ``cd``.

    Written through a temporary name and renamed into place, because the reader
    cannot distinguish a manifest that is short from one that is truncated
    except by its terminator, and the cheapest way to never write a short one is
    to never write into the name at all.
    """
    listing = _tracked_paths()
    if listing is not None:
        staging = destination / f"{_POPULATION_NAME}.partial"
        staging.write_bytes(listing)
        staging.replace(destination / _POPULATION_NAME)
        return

    carried = destination / _POPULATION_NAME
    if carried.is_file() and _is_complete(carried.read_bytes()):
        _note(f"{destination}: kept the population its source recorded; git could not be asked")
        return

    raise HarnessError(
        f"could not record the population for {destination}: `git ls-files` did not answer "
        f"for {REPO_ROOT}, and no manifest came with the copy. The suite inside it would "
        "silently scan a different set of files than the gate this batch stands in for."
    )


def _build_tree(destination: Path, cache_dir: Path) -> Path:
    """Copy the checkout and give the copy its own virtualenv."""
    shutil.rmtree(destination, ignore_errors=True)
    shutil.copytree(REPO_ROOT, destination, ignore=_COPY_IGNORE, symlinks=True)
    _record_population(destination)
    completed = subprocess.run(  # noqa: S603 - argv is harness-owned, never user input
        [_uv(), "sync", "--frozen"],
        cwd=destination,
        env=_child_env(destination, cache_dir),
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        raise HarnessError(f"uv sync failed in {destination}:\n{completed.stderr[-2000:]}")
    marker = destination / ".venv/lib"
    if not marker.exists():
        raise HarnessError(f"no virtualenv materialised in {destination}")
    return destination


def _report(outcome: Outcome) -> None:
    """Print one result immediately.

    ``flush`` is not decoration: a batch runs for many minutes and its output is
    usually being read through a pipe, where Python block-buffers and nothing
    appears until the very end.
    """
    suffix = {
        "SURVIVED": " (suite GREEN -- nothing holds this)",
        "KILLED": " (suite RED)",
        "HUNG": " (suite NEVER FINISHED -- it cannot go RED for this)",
        "control-green": " (baseline GREEN)",
        "control-red": " (baseline RED -- every KILLED below is meaningless)",
        "ERROR": " (the run proves nothing)",
    }.get(outcome.verdict, "")
    print(f"{outcome.label:44s} {outcome.verdict}{suffix}  {outcome.seconds:6.1f}s", flush=True)
    print(f"{'':44s} {outcome.summary}", flush=True)
    for failure in outcome.failures[:6]:
        print(f"{'':44s}   {failure[:120]}", flush=True)


def _cache_dir() -> Path:
    """uv's cache, resolved before ``HOME`` is moved, so children stay warm."""
    return Path(os.environ.get("UV_CACHE_DIR") or Path.home() / ".cache/uv")


def _work_root(options: Options) -> Path:
    """Where the copies live -- anywhere but inside the thing being copied.

    ``copytree`` snapshots the source before it creates the destination, so a
    ``--work-dir`` under the checkout survives the first tree and then copies
    ``tree-0`` into ``tree-1``. ``--prepare-tree`` makes ``--work-dir`` worth
    reaching for, which is what puts this within reach.
    """
    if options.work_dir is None:
        return Path(tempfile.mkdtemp(prefix="theurian-mutate-"))
    root = options.work_dir.resolve()
    if root == REPO_ROOT or REPO_ROOT in root.parents:
        raise HarnessError(f"--work-dir must be outside {REPO_ROOT}; the trees are copies of it")
    root.mkdir(parents=True, exist_ok=True)
    return root


def _execute(mutations: tuple[Mutation, ...], options: Options) -> list[Outcome]:
    cache_dir = _cache_dir()
    root = _work_root(options)
    workers = max(1, min(options.workers, len(mutations)))
    print(f"building {workers} isolated tree(s) under {root}", flush=True)

    trees: queue.Queue[Path] = queue.Queue()
    try:
        for index in range(workers):
            trees.put(_build_tree(root / f"tree-{index}", cache_dir))

        outcomes: list[Outcome] = []

        def task(mutation: Mutation) -> Outcome:
            tree = trees.get()
            try:
                return _run_one(tree, mutation, options, cache_dir)
            finally:
                trees.put(tree)

        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(task, item): item for item in mutations}
            for future in concurrent.futures.as_completed(futures):
                mutation = futures[future]
                try:
                    outcome = future.result()
                except HarnessError as error:
                    outcome = Outcome(
                        label=mutation.label,
                        verdict="ERROR",
                        suite_green=None,
                        seconds=0.0,
                        summary=str(error),
                    )
                _report(outcome)
                outcomes.append(outcome)
                _persist(outcomes, options)
        return outcomes
    finally:
        if options.keep_trees:
            print(f"trees kept at {root}")
        else:
            shutil.rmtree(root, ignore_errors=True)


def _persist(outcomes: list[Outcome], options: Options) -> None:
    """Write after every result, so a long batch is inspectable while it runs."""
    if options.json_path is None:
        return
    payload = json.dumps([asdict(outcome) for outcome in outcomes], indent=2, ensure_ascii=False)
    options.json_path.write_text(payload + "\n", encoding="utf-8")


def _note(line: str = "") -> None:
    """Prepared-tree commentary goes to stderr; only the path goes to stdout."""
    print(line, file=sys.stderr, flush=True)


def _write_runner(tree: Path, cache_dir: Path) -> Path:
    """Drop a launcher into the prepared tree.

    The environment is the point. Without ``PYTHONDONTWRITEBYTECODE=1`` an
    edit-and-rerun loop inside the tree can be served a stale ``.pyc`` and
    report a pass for code that never ran -- the same false-SURVIVED hazard the
    verdict path handles for you, and the reason to hand over a launcher rather
    than a bare directory.
    """
    quoted = {
        "tree": shlex.quote(str(tree)),
        "home": shlex.quote(str(tree / ".mutate-home")),
        "data": shlex.quote(str(tree / ".mutate-data")),
        "cache": shlex.quote(str(cache_dir)),
    }
    script = tree / _RUNNER_NAME
    script.write_text(
        "#!/bin/sh\n"
        "# Generated by tools/mutate.py --prepare-tree.\n"
        "# Runs pytest inside this prepared copy, with the isolation the\n"
        "# verdict path uses. Any pytest arguments are passed straight through.\n"
        f"cd {quoted['tree']} || exit 1\n"
        "exec env -u VIRTUAL_ENV -u PYTHONHOME -u PYTHONPATH \\\n"
        "    PYTHONDONTWRITEBYTECODE=1 \\\n"
        f"    HOME={quoted['home']} \\\n"
        f"    THEURIAN_DATA_DIR={quoted['data']} \\\n"
        f"    UV_CACHE_DIR={quoted['cache']} \\\n"
        '    uv run --frozen --no-sync pytest -p no:randomly -p no:cacheprovider "$@"\n',
        encoding="utf-8",
    )
    return script


def _single_mutation(args: argparse.Namespace) -> Mutation | None:
    """The one mutation a prepared tree will hold, or ``None`` for a clean tree."""
    if not args.spec and not args.file:
        return None
    mutations = _mutations_from(args)
    if len(mutations) != 1:
        raise HarnessError(
            f"--prepare-tree holds one mutation; this spec has {len(mutations)}. "
            "Prepare them one at a time, or drop --prepare-tree to run the batch."
        )
    return mutations[0]


def _apply_to_prepared(tree: Path, mutation: Mutation | None) -> tuple[Applied, ...] | None:
    """Apply the mutation and prove it did not land in the real checkout."""
    if mutation is None:
        return None
    before = _digest_targets((mutation,))
    landed = _apply(tree, mutation)
    strayed = _changed_targets(before, _digest_targets((mutation,)))
    if strayed:
        raise HarnessError(f"the mutation wrote to {REPO_ROOT}, not to the copy: {strayed[0]}")
    return landed


def _describe_prepared(
    tree: Path, root: Path, mutation: Mutation | None, land: tuple[Applied, ...] | None
) -> None:
    _note("prepared tree ready -- nothing has been run in it")
    _note(f"  path       {tree}")
    if mutation is not None and land:
        _note(f"  mutation   {mutation.label}")
        for edit, applied in zip(mutation.edits, land, strict=True):
            _note(f"  applied    {edit.path}  {applied.before[:12]} -> {applied.mutated[:12]}")
    else:
        _note("  mutation   none -- this is an unmutated baseline copy")
    _note("run any selection inside it, as many times as you like:")
    _note(f"  sh {shlex.quote(str(tree / _RUNNER_NAME))} <pytest args>")
    _note("delete it when you are done:")
    _note(f"  rm -rf {shlex.quote(str(root))}")
    _note(
        "a selection tells you whether *that* test catches it. Only the verdict "
        "path -- this tool without --prepare-tree -- tells you whether anything does."
    )


def _prepare_mode(args: argparse.Namespace, options: Options) -> int:
    """Build one mutated tree, hand back its path, and run nothing."""
    mutation = _single_mutation(args)
    cache_dir = _cache_dir()
    root = _work_root(options)
    try:
        tree = _build_tree(root / "tree-0", cache_dir)
        land = _apply_to_prepared(tree, mutation)
    except HarnessError:
        shutil.rmtree(root, ignore_errors=True)
        raise
    _write_runner(tree, cache_dir)
    _describe_prepared(tree, root, mutation, land)
    print(tree)
    return 0


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="tools/mutate.py",
        description="Apply source mutations and report which ones the suite fails to catch.",
    )
    parser.add_argument("--spec", help="JSON list of {label, file, old, new}")
    parser.add_argument("--file", help="repository-relative source file to mutate")
    parser.add_argument("--old", help="exact text to replace; must occur exactly once")
    parser.add_argument("--new", help="replacement text")
    parser.add_argument("--old-file", help="read --old from this file (multi-line anchors)")
    parser.add_argument("--new-file", help="read --new from this file")
    parser.add_argument("--label", help="name for the single inline mutation")
    parser.add_argument("--workers", type=int, default=4, help="isolated trees to run across")
    parser.add_argument(
        "--no-fail-fast",
        dest="fail_fast",
        action="store_false",
        help="walk the whole suite even once a test has failed",
    )
    parser.add_argument(
        "--no-control",
        dest="control",
        action="store_false",
        help="skip the unmutated baseline run (not recommended)",
    )
    parser.add_argument("--timeout", type=int, default=_DEFAULT_TIMEOUT_SECONDS)
    parser.add_argument("--json", dest="json_path", help="write results here after every mutation")
    parser.add_argument("--work-dir", help="where to build the isolated trees")
    parser.add_argument("--keep-trees", action="store_true", help="do not delete the copies")
    parser.add_argument(
        "--prepare-tree",
        action="store_true",
        help=(
            "build and mutate one tree, print its path to stdout, run nothing "
            "and delete nothing -- for iterating on a single test"
        ),
    )
    return parser.parse_args(argv)


def _report_summary(outcomes: list[Outcome], elapsed: float, workers: int) -> None:
    survivors = [item for item in outcomes if item.verdict == "SURVIVED"]
    hung = [item for item in outcomes if item.verdict == "HUNG"]
    print(
        f"\n{len(outcomes)} run(s) in {elapsed:.1f}s across {workers} worker(s): "
        f"{len(survivors)} SURVIVED, "
        f"{len(hung)} HUNG, "
        f"{len([i for i in outcomes if i.verdict == 'KILLED'])} KILLED, "
        f"{len([i for i in outcomes if i.verdict == 'ERROR'])} ERROR"
    )
    for survivor in survivors:
        print(f"  SURVIVED  {survivor.label}")
    for item in hung:
        print(f"  HUNG      {item.label}  -- the suite cannot report this as a failure")


def _verdict_mode(args: argparse.Namespace, options: Options) -> int:
    """Run the whole suite per mutation and report KILLED / SURVIVED."""
    mutations = _mutations_from(args)
    if options.control:
        mutations = (Mutation(_CONTROL_LABEL, None, "", ""), *mutations)

    before_targets = _digest_targets(mutations)
    before_status = _porcelain_entries()
    started = time.monotonic()
    outcomes = _execute(mutations, options)
    elapsed = time.monotonic() - started

    # Printed before the integrity verdict, not after: the old order threw the
    # whole summary away whenever the checkout check tripped, which is exactly
    # when someone most needs to see that the verdicts themselves were fine.
    _report_summary(outcomes, elapsed, options.workers)
    if _report_checkout(mutations, before_targets, before_status):
        return 2
    if any(item.verdict == "control-red" for item in outcomes):
        print("the unmutated control was RED: no KILLED verdict here means anything")
        return 2
    if any(item.verdict == "ERROR" for item in outcomes):
        return 2
    return 1 if any(item.verdict in {"SURVIVED", "HUNG"} for item in outcomes) else 0


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    options = Options(
        workers=int(args.workers),
        fail_fast=bool(args.fail_fast),
        control=bool(args.control),
        timeout=int(args.timeout),
        keep_trees=bool(args.keep_trees),
        json_path=Path(args.json_path) if args.json_path else None,
        work_dir=Path(args.work_dir) if args.work_dir else None,
    )
    try:
        if args.prepare_tree:
            return _prepare_mode(args, options)
        return _verdict_mode(args, options)
    except HarnessError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    except Exception:
        # Last resort: anything that reaches here is a bug in the harness, not
        # a verdict. Uncaught, Python's default exit code for this is 1 --
        # indistinguishable from the documented "at least one survived",
        # which is the exact false signal this tool exists to prevent.
        traceback.print_exc(file=sys.stderr)
        print("error: the harness raised an exception it did not anticipate", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
