"""Which production file tonight's sweep attacks, decided by the date alone.

CLAUDE.md promises "a standing red-team sweep over ``main``, nightly single-file
mutation runs included". One night can afford one file: a verdict costs a full
suite walk, and the nightly budget is about two hours (issue #378). So the sweep
needs a rule for *which* file, and the rule has to hold two properties at once.

**Reproducible.** The issue the sweep files names a date and a mutation, and
whoever picks it up has to be able to regenerate that exact mutation from those
two facts. Nothing here may depend on the machine, the clock beyond the date, or
the order a directory walk returned.

**Fair.** Every production file has to come up, and no file may come up twice
before all the others have. ``ordinal % len`` gives both; a hash of the date
gives neither, and the difference is invisible on any single night.

The census deliberately drops ``__init__.py``: in this codebase those are
re-export surfaces, and the three operators in :mod:`sweep_mutations` have
nothing to reach in one. A barren target costs a whole night, so the rotation
also lets the driver advance -- see :func:`rotation`, which hands back the full
census in walk order rather than a single name.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from pathlib import Path
from typing import Final

REPO_ROOT: Final = Path(__file__).resolve().parents[1]

#: The production tree, repository-relative. Tests, fixtures and tooling are
#: deliberately outside it: a surviving mutation in a test file says nothing
#: about what the product holds.
CENSUS_ROOT: Final = "packages/theurian-core/src/theurian"


class SweepError(RuntimeError):
    """The sweep cannot run, as distinct from the sweep finding something.

    The driver turns this into exit 1. The distinction is the whole point of the
    exit codes: findings are a *successful* sweep (exit 0, an issue filed), and
    only a sweep that could not do its job is a failure the workflow alarms on.
    """


def census(repo_root: Path = REPO_ROOT) -> tuple[str, ...]:
    """Every production file, repository-relative and sorted.

    Sorted on the POSIX string rather than on ``Path``, so the index the rotation
    computes means the same thing on every machine. ``rglob`` alone does not:
    it returns entries in whatever order the filesystem hands back.
    """
    root = repo_root / CENSUS_ROOT
    if not root.is_dir():
        raise SweepError(f"no production tree at {root}; the sweep has nothing to attack")
    files = tuple(
        sorted(
            found.relative_to(repo_root).as_posix()
            for found in root.rglob("*.py")
            if found.is_file() and found.name != "__init__.py"
        )
    )
    if not files:
        raise SweepError(f"{root} holds no production module outside __init__.py")
    return files


def rotation(files: Sequence[str], on: date) -> tuple[str, ...]:
    """The census re-ordered to start at tonight's target.

    The whole census rather than a single name, because a target can be barren --
    a module of constants and dataclasses holds no comparison, no boolean literal
    and no ``and``. The driver walks this sequence until one file yields
    candidates, and because the sequence is a rotation rather than a slice, that
    walk both terminates and can reach every file.
    """
    if not files:
        raise SweepError("an empty census cannot be rotated; there is no file to attack")
    start = on.toordinal() % len(files)
    return (*files[start:], *files[:start])
