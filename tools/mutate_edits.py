"""The mutation model: what a mutation is, how it lands, and how it is undone.

Split out of ``tools/mutate.py`` (see its module docstring for the harness this
supports) because the file grew past a comfortable size once a mutation could
carry several edits. Everything here is pure file manipulation plus the safety
checks around it -- no subprocess, no CLI, no reporting. ``mutate.py`` imports
what it needs from this module and stays the orchestration layer.
"""

from __future__ import annotations

import hashlib
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Final

REPO_ROOT: Final = Path(__file__).resolve().parents[1]

# Stands in for a digest when a mutation target does not exist in the real
# checkout, so "created during the run" reads as a change rather than as equal.
_ABSENT: Final = "<absent>"


class HarnessError(RuntimeError):
    """The run cannot be trusted -- as distinct from a mutation surviving."""


@dataclass(frozen=True)
class Edit:
    """One exact-string replacement in one repository-relative source file."""

    path: str
    old: str
    new: str


@dataclass(frozen=True)
class Mutation:
    """One labelled hypothesis: the edits that land together for a single verdict.

    Most mutations are one edit and are written as ``file``/``old``/``new``.
    Some questions cannot be asked with one. "Does this guard still catch the
    defect once the walker is weakened?" needs the guard weakened *and* the
    defect reintroduced in the same tree, and running them as two labels answers
    a different question -- each alone is killed by the other's absence. ``also``
    carries the further edits; every one of them is anchored, digested and
    restored exactly like the first.
    """

    label: str
    path: str | None
    old: str
    new: str
    also: tuple[Edit, ...] = ()

    @property
    def is_control(self) -> bool:
        return self.path is None

    @property
    def edits(self) -> tuple[Edit, ...]:
        if self.path is None:
            return ()
        return (Edit(self.path, self.old, self.new), *self.also)

    @property
    def paths(self) -> tuple[str, ...]:
        return tuple(edit.path for edit in self.edits)


@dataclass(frozen=True)
class Applied:
    """A landed mutation, and everything needed to prove it was undone."""

    target: Path
    original: str
    before: str
    mutated: str


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

    Paired with ``PYTHONDONTWRITEBYTECODE=1``. See the harness module docstring:
    a same-length constant mutation written inside one second is otherwise
    served from a ``.pyc`` and never actually tested.
    """
    for cache in tree.rglob("__pycache__"):
        if ".venv" in cache.parts:
            continue
        shutil.rmtree(cache, ignore_errors=True)


def _apply_edit(tree: Path, label: str, edit: Edit) -> Applied:
    """Write one edit, or raise. An edit that does not land is an error.

    Anchors must match exactly once. A missing anchor produces a run that tests
    nothing while reporting SURVIVED, and an anchor matching twice produces a
    change nobody aimed. Both have happened in this project.
    """
    target = tree / edit.path
    if not target.is_file():
        raise HarnessError(f"{label}: no such file {edit.path}")
    original = target.read_text(encoding="utf-8")
    occurrences = original.count(edit.old)
    if occurrences != 1:
        raise HarnessError(
            f"{label}: anchor matched {occurrences} times in {edit.path} "
            f"(exactly one required): {edit.old[:80]!r}"
        )
    before = _sha256(target)
    target.write_text(original.replace(edit.old, edit.new, 1), encoding="utf-8")
    _clear_pycache(tree)
    mutated = _sha256(target)
    if mutated == before:
        raise HarnessError(f"{label}: {edit.path} is unchanged after writing the mutation")
    if edit.new not in target.read_text(encoding="utf-8"):
        raise HarnessError(f"{label}: the replacement is absent from {edit.path} on disk")
    return Applied(target=target, original=original, before=before, mutated=mutated)


def _apply(tree: Path, mutation: Mutation) -> tuple[Applied, ...]:
    """Land every edit this mutation carries, unwinding the ones that landed.

    A composite that fails halfway would otherwise leave a partially mutated
    tree for the next job that borrows it, and the next job's verdict would be
    about a source nobody described.
    """
    if mutation.path is None:
        raise HarnessError(f"{mutation.label}: a control carries no file to mutate")
    landed: list[Applied] = []
    try:
        for edit in mutation.edits:
            landed.append(_apply_edit(tree, mutation.label, edit))
    except HarnessError:
        for done in reversed(landed):
            _restore(done, tree)
        raise
    return tuple(landed)


def _restore(applied: Applied, tree: Path) -> str:
    """Put the file back and prove it, byte for byte.

    The caveat in the harness module docstring applies: this compares against
    the hash this process took, so it cannot see a concurrent writer. The
    isolated tree is what removes that hazard.
    """
    applied.target.write_text(applied.original, encoding="utf-8")
    _clear_pycache(tree)
    after = _sha256(applied.target)
    if after != applied.before:
        raise HarnessError(f"restore failed for {applied.target}: {applied.before} != {after}")
    return after


def _restore_all(landed: tuple[Applied, ...], tree: Path) -> dict[str, str]:
    """Undo every edit and report the digests, so a caller can check applied-ness.

    Reversed, because two edits to one file would otherwise restore the earlier
    snapshot last and silently keep the later edit.
    """
    digests: dict[str, str] = {}
    for applied in reversed(landed):
        restored = _restore(applied, tree)
        name = str(applied.target.name)
        digests[f"{name}:before"] = applied.before
        digests[f"{name}:mutated"] = applied.mutated
        digests[f"{name}:restored"] = restored
    return digests


def _digest_targets(mutations: tuple[Mutation, ...]) -> dict[str, str]:
    """Digest every path this run intends to mutate, as it exists *here*.

    "Here" is the real checkout, never a copy. These are the only files whose
    movement makes a verdict false, and therefore the only ones the exit code
    is allowed to answer for.
    """
    digests: dict[str, str] = {}
    for mutation in mutations:
        for relative in mutation.paths:
            target = REPO_ROOT / relative
            digests[relative] = _sha256(target) if target.is_file() else _ABSENT
    return digests


def _changed_targets(before: dict[str, str], after: dict[str, str]) -> tuple[str, ...]:
    return tuple(
        f"{path}  {before[path][:12]} -> {after.get(path, _ABSENT)[:12]}"
        for path in sorted(before)
        if before[path] != after.get(path, _ABSENT)
    )
