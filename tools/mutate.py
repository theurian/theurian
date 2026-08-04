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

Exit status is ``0`` when every mutation was killed, ``1`` when at least one
survived, and ``2`` when the run itself cannot be trusted -- an anchor that did
not match, a restore that did not restore, a control run that was not green.

Why it is built this way
------------------------
Each of the following cost this repository a wrong answer at least once. Only
``-x`` is about speed; the rest are about not reporting a result that is false.

**The whole suite runs, always.** There is deliberately no option to narrow the
selection. A narrowed run assumes the answer to the question being asked. During
Milestone 5, dropping the outer ``lower()`` from ``_matched_characters`` came
back SURVIVED and no plausible narrowed selection would have found that; a test
was then written, and the same mutation is killed today. Which tests catch a
given mutation is exactly what is not known in advance.

**``-x`` by default.** A run that is going to be RED stops at the first
failure; a run that is going to be GREEN still walks every test, so the
question is preserved intact. Most mutations are killed, so most runs stop
early. ``--no-fail-fast`` restores the full walk when the complete failure list
matters.

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
"""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import os
import queue
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Final

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

_PYTEST_ARGS: Final = (
    "-q",
    "--no-header",
    "--tb=no",
    # Deterministic collection order, so a fail-fast stop is comparable across
    # mutations, and no cache written into the throwaway tree.
    "-p",
    "no:randomly",
    "-p",
    "no:cacheprovider",
)

_CONTROL_LABEL: Final = "__control__"
_DEFAULT_TIMEOUT_SECONDS: Final = 1800


class HarnessError(RuntimeError):
    """The run cannot be trusted -- as distinct from a mutation surviving."""


@dataclass(frozen=True)
class Mutation:
    """One exact-string replacement in one repository-relative source file."""

    label: str
    path: str | None
    old: str
    new: str

    @property
    def is_control(self) -> bool:
        return self.path is None


@dataclass(frozen=True)
class Outcome:
    label: str
    verdict: str
    suite_green: bool | None
    seconds: float
    summary: str
    failures: tuple[str, ...] = ()
    detail: str = ""
    digests: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class Options:
    workers: int
    fail_fast: bool
    control: bool
    timeout: int
    keep_trees: bool
    json_path: Path | None
    work_dir: Path | None


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _clear_pycache(tree: Path) -> None:
    """Remove every stale bytecode cache outside the virtualenv.

    Paired with ``PYTHONDONTWRITEBYTECODE=1``. See the module docstring: a
    same-length constant mutation written inside one second is otherwise served
    from a ``.pyc`` and never actually tested.
    """
    for cache in tree.rglob("__pycache__"):
        if ".venv" in cache.parts:
            continue
        shutil.rmtree(cache, ignore_errors=True)


def _uv() -> str:
    found = shutil.which("uv")
    if found is None:
        raise HarnessError("uv is not on PATH; the harness runs the suite through `uv run`")
    return found


def _child_env(tree: Path, cache_dir: Path) -> dict[str, str]:
    """Environment for a suite run: isolated HOME, no inherited virtualenv."""
    env = dict(os.environ)
    for leaked in ("VIRTUAL_ENV", "PYTHONHOME", "PYTHONPATH"):
        env.pop(leaked, None)
    home = tree / ".mutate-home"
    data = tree / ".mutate-data"
    home.mkdir(exist_ok=True)
    data.mkdir(exist_ok=True)
    env.update(
        {
            "PYTHONDONTWRITEBYTECODE": "1",
            "HOME": str(home),
            "THEURIAN_DATA_DIR": str(data),
            # Resolved before HOME moved, so the child still hits the warm cache.
            "UV_CACHE_DIR": str(cache_dir),
        }
    )
    return env


def _build_tree(destination: Path, cache_dir: Path) -> Path:
    """Copy the checkout and give the copy its own virtualenv."""
    shutil.rmtree(destination, ignore_errors=True)
    shutil.copytree(REPO_ROOT, destination, ignore=_COPY_IGNORE, symlinks=True)
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


def _run_suite(tree: Path, options: Options, cache_dir: Path) -> subprocess.CompletedProcess[str]:
    argv = [_uv(), "run", "--frozen", "--no-sync", "pytest", *_PYTEST_ARGS]
    if options.fail_fast:
        argv.append("-x")
    try:
        return subprocess.run(  # noqa: S603 - argv is harness-owned, never user input
            argv,
            cwd=tree,
            env=_child_env(tree, cache_dir),
            capture_output=True,
            text=True,
            check=False,
            timeout=options.timeout,
        )
    except subprocess.TimeoutExpired as expired:
        raise HarnessError(f"the suite exceeded {options.timeout}s in {tree}") from expired


@dataclass(frozen=True)
class Applied:
    """A landed mutation, and everything needed to prove it was undone."""

    target: Path
    original: str
    before: str
    mutated: str


def _apply(tree: Path, mutation: Mutation) -> Applied:
    """Write the mutation, or raise. A mutation that does not land is an error.

    Anchors must match exactly once. A missing anchor produces a run that tests
    nothing while reporting SURVIVED, and an anchor matching twice produces a
    change nobody aimed. Both have happened in this project.
    """
    if mutation.path is None:
        raise HarnessError(f"{mutation.label}: a control carries no file to mutate")
    target = tree / mutation.path
    if not target.is_file():
        raise HarnessError(f"{mutation.label}: no such file {mutation.path}")
    original = target.read_text(encoding="utf-8")
    occurrences = original.count(mutation.old)
    if occurrences != 1:
        raise HarnessError(
            f"{mutation.label}: anchor matched {occurrences} times in {mutation.path} "
            f"(exactly one required): {mutation.old[:80]!r}"
        )
    before = _sha256(target)
    target.write_text(original.replace(mutation.old, mutation.new, 1), encoding="utf-8")
    _clear_pycache(tree)
    mutated = _sha256(target)
    if mutated == before:
        raise HarnessError(f"{mutation.label}: the file is unchanged after writing the mutation")
    if mutation.new not in target.read_text(encoding="utf-8"):
        raise HarnessError(f"{mutation.label}: the replacement is absent from the file on disk")
    return Applied(target=target, original=original, before=before, mutated=mutated)


def _restore(applied: Applied, tree: Path) -> str:
    """Put the file back and prove it, byte for byte.

    The caveat in the module docstring applies: this compares against the hash
    this process took, so it cannot see a concurrent writer. The isolated tree
    is what removes that hazard.
    """
    applied.target.write_text(applied.original, encoding="utf-8")
    _clear_pycache(tree)
    after = _sha256(applied.target)
    if after != applied.before:
        raise HarnessError(f"restore failed for {applied.target}: {applied.before} != {after}")
    return after


def _summarise(stdout: str) -> tuple[str, tuple[str, ...]]:
    lines = [line for line in stdout.splitlines() if line.strip()]
    summary = lines[-1] if lines else "(no output)"
    failures = tuple(line for line in lines if line.startswith(("FAILED", "ERROR")))[:20]
    return summary, failures


def _run_one(tree: Path, mutation: Mutation, options: Options, cache_dir: Path) -> Outcome:
    started = time.monotonic()
    if mutation.is_control:
        completed = _run_suite(tree, options, cache_dir)
        summary, failures = _summarise(completed.stdout)
        green = completed.returncode == 0
        return Outcome(
            label=mutation.label,
            verdict="control-green" if green else "control-red",
            suite_green=green,
            seconds=time.monotonic() - started,
            summary=summary,
            failures=failures,
            detail="" if green else completed.stdout[-4000:],
        )

    applied = _apply(tree, mutation)
    try:
        completed = _run_suite(tree, options, cache_dir)
    finally:
        restored = _restore(applied, tree)
    summary, failures = _summarise(completed.stdout)
    green = completed.returncode == 0
    return Outcome(
        label=mutation.label,
        verdict="SURVIVED" if green else "KILLED",
        suite_green=green,
        seconds=time.monotonic() - started,
        summary=summary,
        failures=failures,
        digests={"before": applied.before, "mutated": applied.mutated, "restored": restored},
    )


def _load_spec(path: Path) -> tuple[Mutation, ...]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise HarnessError(f"{path}: a spec is a JSON list of mutation objects")
    mutations: list[Mutation] = []
    for index, entry in enumerate(raw):
        if not isinstance(entry, dict):
            raise HarnessError(f"{path}: entry {index} is not an object")
        try:
            mutations.append(
                Mutation(
                    label=str(entry["label"]),
                    path=str(entry["file"]),
                    old=str(entry["old"]),
                    new=str(entry["new"]),
                )
            )
        except KeyError as missing:
            raise HarnessError(f"{path}: entry {index} is missing {missing}") from missing
    if not mutations:
        raise HarnessError(f"{path}: no mutations")
    return tuple(mutations)


def _porcelain() -> str:
    completed = subprocess.run(
        ["git", "status", "--porcelain"],  # noqa: S607 - resolved via PATH
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    return completed.stdout


def _report(outcome: Outcome) -> None:
    """Print one result immediately.

    ``flush`` is not decoration: a batch runs for many minutes and its output is
    usually being read through a pipe, where Python block-buffers and nothing
    appears until the very end.
    """
    suffix = {
        "SURVIVED": " (suite GREEN -- nothing holds this)",
        "KILLED": " (suite RED)",
        "control-green": " (baseline GREEN)",
        "control-red": " (baseline RED -- every KILLED below is meaningless)",
        "ERROR": " (the run proves nothing)",
    }.get(outcome.verdict, "")
    print(f"{outcome.label:44s} {outcome.verdict}{suffix}  {outcome.seconds:6.1f}s", flush=True)
    print(f"{'':44s} {outcome.summary}", flush=True)
    for failure in outcome.failures[:6]:
        print(f"{'':44s}   {failure[:120]}", flush=True)


def _execute(mutations: tuple[Mutation, ...], options: Options) -> list[Outcome]:
    cache_dir = Path(os.environ.get("UV_CACHE_DIR") or Path.home() / ".cache/uv")
    root = options.work_dir or Path(tempfile.mkdtemp(prefix="theurian-mutate-"))
    root.mkdir(parents=True, exist_ok=True)
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


def _mutations_from(args: argparse.Namespace) -> tuple[Mutation, ...]:
    if args.spec:
        return _load_spec(Path(args.spec))
    if not args.file:
        raise HarnessError("give either --spec or --file with --old/--new")
    old = Path(args.old_file).read_text(encoding="utf-8") if args.old_file else args.old
    new = Path(args.new_file).read_text(encoding="utf-8") if args.new_file else args.new
    if old is None or new is None:
        raise HarnessError("--file needs --old/--new or --old-file/--new-file")
    relative = Path(args.file)
    if relative.is_absolute():
        relative = relative.resolve().relative_to(REPO_ROOT)
    label = args.label or relative.name
    return (Mutation(label=str(label), path=str(relative), old=str(old), new=str(new)),)


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
    return parser.parse_args(argv)


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
        mutations = _mutations_from(args)
    except HarnessError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2

    if options.control:
        mutations = (Mutation(_CONTROL_LABEL, None, "", ""), *mutations)

    before_tree = _porcelain()
    started = time.monotonic()
    try:
        outcomes = _execute(mutations, options)
    except HarnessError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    elapsed = time.monotonic() - started

    # r3's check, kept: the isolated copies mean this checkout is never written
    # to, and this is what proves it rather than assuming it.
    if _porcelain() != before_tree:
        print("error: the real checkout changed during the run", file=sys.stderr)
        return 2

    survivors = [item for item in outcomes if item.verdict == "SURVIVED"]
    errors = [item for item in outcomes if item.verdict == "ERROR"]
    control_red = [item for item in outcomes if item.verdict == "control-red"]
    print(
        f"\n{len(outcomes)} run(s) in {elapsed:.1f}s across {options.workers} worker(s): "
        f"{len(survivors)} SURVIVED, "
        f"{len([i for i in outcomes if i.verdict == 'KILLED'])} KILLED, "
        f"{len(errors)} ERROR"
    )
    for survivor in survivors:
        print(f"  SURVIVED  {survivor.label}")
    if control_red:
        print("the unmutated control was RED: no KILLED verdict here means anything")
        return 2
    if errors:
        return 2
    return 1 if survivors else 0


if __name__ == "__main__":
    raise SystemExit(main())
