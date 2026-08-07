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

A prepared tree, which applies the mutation and then runs *nothing*, so you can::

    tree=$(uv run python tools/mutate.py --prepare-tree \\
        --file packages/theurian-core/src/theurian/domain/ranking.py \\
        --old 'reverse=True' --new 'reverse=False')
    sh "$tree/.mutate-run" packages/theurian-core/tests/unit/test_ranking.py -x
    rm -rf "$(dirname "$tree")"

``--prepare-tree`` writes the tree's absolute path to stdout and every other
line to stderr, so ``$(...)`` captures the path and nothing else.

Exit status is ``0`` when every mutation was killed, ``1`` when at least one
survived, and ``2`` when the run itself cannot be trusted -- an anchor that did
not match, a restore that did not restore, a control run that was not green, or
a mutation that reached the real checkout.

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
import hashlib
import json
import os
import queue
import shlex
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
    # Deterministic collection order, so a fail-fast stop is comparable across
    # mutations, and no cache written into the throwaway tree.
    "-p",
    "no:randomly",
    "-p",
    "no:cacheprovider",
    # These come *after* the plugin flags on purpose, and the order is load
    # bearing. A run started here lives for minutes inside a checkout where
    # other work is also running `pytest -q`, and the usual way to stop that
    # work is `pkill -f "pytest -q"` -- which matches on the whole argv, so
    # leading with `-q` volunteers this harness for everyone else's cleanup.
    # Killed mid-run it reports `control-red` or a false SURVIVED, and neither
    # says "someone shot me". The prepared-tree runner has always ordered them
    # this way; the verdict path had not, which is why only the verdict path
    # kept dying.
    "-q",
    "--no-header",
    "--tb=no",
)

_CONTROL_LABEL: Final = "__control__"
_DEFAULT_TIMEOUT_SECONDS: Final = 1800

# Stands in for a digest when a mutation target does not exist in the real
# checkout, so "created during the run" reads as a change rather than as equal.
_ABSENT: Final = "<absent>"

# A note listing every unrelated edit in a busy checkout would bury its own
# point. The count is always printed, so a truncated list is still honest.
_MAX_LISTED: Final = 20

# Dropped into a prepared tree. Dot-prefixed to stay clear of anything in the
# suite that walks the repository root.
_RUNNER_NAME: Final = ".mutate-run"

# `git status --porcelain` v1: two status letters, a space, then the path.
_PORCELAIN_PREFIX: Final = 3


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


def _repository_relative(raw: str, label: str) -> str:
    """Normalise an anchor path, or refuse one that could escape the copy.

    ``Path(tree) / "/etc/passwd"`` is ``/etc/passwd`` and ``tree / "../x"``
    climbs out of it, so either form turns a mutation into a write against the
    machine. Refusing both here is what lets the integrity check watch only the
    mutation set: a path that cannot leave the copy cannot touch a file the
    check is not looking at.
    """
    candidate = Path(raw)
    if candidate.is_absolute():
        try:
            candidate = candidate.resolve().relative_to(REPO_ROOT)
        except ValueError:
            raise HarnessError(f"{label}: {raw} is outside {REPO_ROOT}") from None
    if ".." in candidate.parts:
        raise HarnessError(f"{label}: {raw} climbs out of the repository")
    if not candidate.parts:
        raise HarnessError(f"{label}: an empty path cannot be mutated")
    return str(candidate)


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
            label = str(entry["label"])
            mutations.append(
                Mutation(
                    label=label,
                    path=_repository_relative(str(entry["file"]), label),
                    old=str(entry["old"]),
                    new=str(entry["new"]),
                )
            )
        except KeyError as missing:
            raise HarnessError(f"{path}: entry {index} is missing {missing}") from missing
    if not mutations:
        raise HarnessError(f"{path}: no mutations")
    return tuple(mutations)


def _digest_targets(mutations: tuple[Mutation, ...]) -> dict[str, str]:
    """Digest every path this run intends to mutate, as it exists *here*.

    "Here" is the real checkout, never a copy. These are the only files whose
    movement makes a verdict false, and therefore the only ones the exit code
    is allowed to answer for.
    """
    digests: dict[str, str] = {}
    for mutation in mutations:
        if mutation.path is None:
            continue
        target = REPO_ROOT / mutation.path
        digests[mutation.path] = _sha256(target) if target.is_file() else _ABSENT
    return digests


def _changed_targets(before: dict[str, str], after: dict[str, str]) -> tuple[str, ...]:
    return tuple(
        f"{path}  {before[path][:12]} -> {after.get(path, _ABSENT)[:12]}"
        for path in sorted(before)
        if before[path] != after.get(path, _ABSENT)
    )


def _porcelain_entries() -> dict[str, tuple[str, str]]:
    """``git status --porcelain`` as ``path -> (two-letter status, digest)``.

    The digest is what makes the note honest in the case it exists for. In a
    checkout several agents share, the interesting files are already dirty when
    a run starts, so a status letter alone never moves when one of them is
    edited again. Only the paths git already reports are hashed, which is a
    handful.

    Informational only. Rename entries key on the whole ``old -> new`` phrase
    and untracked directories are not files; both fall back to the sentinel,
    which is fine for something that is printed and never gates an exit code.
    """
    completed = subprocess.run(
        ["git", "status", "--porcelain"],  # noqa: S607 - resolved via PATH
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    entries: dict[str, tuple[str, str]] = {}
    for line in completed.stdout.splitlines():
        if len(line) <= _PORCELAIN_PREFIX:
            continue
        path = line[_PORCELAIN_PREFIX:].strip()
        target = REPO_ROOT / path
        entries[path] = (line[:2], _sha256(target) if target.is_file() else _ABSENT)
    return entries


def _out_of_scope(
    before: dict[str, tuple[str, str]],
    after: dict[str, tuple[str, str]],
    in_scope: frozenset[str],
) -> tuple[str, ...]:
    """Name everything that moved in the checkout that this run did not aim at.

    Concurrent agents make this the ordinary case, not the alarming one, so it
    is reported rather than judged -- but reported by name. "Other files
    changed" with no list is a line people learn to skip.
    """
    changes: list[str] = []
    for path in sorted(set(before) | set(after)):
        if path in in_scope:
            continue
        was, now = before.get(path), after.get(path)
        if was == now:
            continue
        if was is None and now is not None:
            changes.append(f"{now[0]} {path}  (appeared while this run was working)")
        elif now is None and was is not None:
            changes.append(f"{was[0]} {path}  (no longer reported by git status)")
        elif was is not None and now is not None and was[0] != now[0]:
            changes.append(f"{was[0]} -> {now[0]} {path}")
        elif now is not None:
            changes.append(f"{now[0]} {path}  (edited again while this run was working)")
    return tuple(changes)


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


def _mutations_from(args: argparse.Namespace) -> tuple[Mutation, ...]:
    if args.spec:
        return _load_spec(Path(args.spec))
    if not args.file:
        raise HarnessError("give either --spec or --file with --old/--new")
    old = Path(args.old_file).read_text(encoding="utf-8") if args.old_file else args.old
    new = Path(args.new_file).read_text(encoding="utf-8") if args.new_file else args.new
    if old is None or new is None:
        raise HarnessError("--file needs --old/--new or --old-file/--new-file")
    label = str(args.label or Path(args.file).name)
    relative = _repository_relative(str(args.file), label)
    return (Mutation(label=label, path=relative, old=str(old), new=str(new)),)


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


def _apply_to_prepared(tree: Path, mutation: Mutation | None) -> Applied | None:
    """Apply the mutation and prove it did not land in the real checkout."""
    if mutation is None:
        return None
    before = _digest_targets((mutation,))
    applied = _apply(tree, mutation)
    strayed = _changed_targets(before, _digest_targets((mutation,)))
    if strayed:
        raise HarnessError(f"the mutation wrote to {REPO_ROOT}, not to the copy: {strayed[0]}")
    return applied


def _describe_prepared(
    tree: Path, root: Path, mutation: Mutation | None, land: Applied | None
) -> None:
    _note("prepared tree ready -- nothing has been run in it")
    _note(f"  path       {tree}")
    if mutation is not None and land is not None:
        _note(f"  mutation   {mutation.label}")
        _note(f"  applied    {mutation.path}  {land.before[:12]} -> {land.mutated[:12]}")
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
    print(
        f"\n{len(outcomes)} run(s) in {elapsed:.1f}s across {workers} worker(s): "
        f"{len(survivors)} SURVIVED, "
        f"{len([i for i in outcomes if i.verdict == 'KILLED'])} KILLED, "
        f"{len([i for i in outcomes if i.verdict == 'ERROR'])} ERROR"
    )
    for survivor in survivors:
        print(f"  SURVIVED  {survivor.label}")


def _report_listing(header: str, lines: tuple[str, ...], *, to_stderr: bool = False) -> None:
    where = sys.stderr if to_stderr else sys.stdout
    print(header, file=where)
    for line in lines[:_MAX_LISTED]:
        print(f"    {line}", file=where)
    if len(lines) > _MAX_LISTED:
        print(f"    ... and {len(lines) - _MAX_LISTED} more", file=where)


def _report_checkout(
    mutations: tuple[Mutation, ...],
    before_targets: dict[str, str],
    before_status: dict[str, tuple[str, str]],
) -> bool:
    """Report what moved in the real checkout. True iff a verdict is at stake.

    Two questions, deliberately separated. *Did this run write here?* is the
    only one the exit code answers. *Did anything else move?* is ordinary in a
    checkout several agents share, so it is named and moved on from.
    """
    in_scope = frozenset(item.path for item in mutations if item.path is not None)
    strayed = _out_of_scope(before_status, _porcelain_entries(), in_scope)
    if strayed:
        _report_listing(
            f"\nnote: {len(strayed)} path(s) outside this run's mutation set changed in "
            f"{REPO_ROOT} while it ran.\n      No verdict above depends on them -- every "
            "mutation was applied inside an isolated copy.",
            strayed,
        )
    touched = _changed_targets(before_targets, _digest_targets(mutations))
    if touched:
        _report_listing(
            "\nerror: this run's own mutation targets changed in the real checkout at "
            f"{REPO_ROOT}.\n       Either a mutation escaped its copy, or the source moved "
            "under trees copied at\n       different moments. Either way no verdict above "
            "is trustworthy:",
            touched,
            to_stderr=True,
        )
    return bool(touched)


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
    return 1 if any(item.verdict == "SURVIVED" for item in outcomes) else 0


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


if __name__ == "__main__":
    raise SystemExit(main())
