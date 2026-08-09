"""What moved in the real checkout while a run was working.

Split out of ``tools/mutate.py`` (see its module docstring, "The integrity
check watches the mutation set, not the whole tree") because the file grew
past a comfortable size once a mutation could carry several edits. Every
mutation runs inside an isolated copy, so nothing here ever gates *whether* a
verdict is trustworthy except the narrow case where this run's own targets
moved in the real checkout underneath it -- everything else is reported by
name and moved on from, because several agents sharing one checkout is the
ordinary case, not the alarming one.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Final

from mutate_edits import _ABSENT, Mutation, _changed_targets, _digest_targets, _sha256

REPO_ROOT: Final = Path(__file__).resolve().parents[1]

# A note listing every unrelated edit in a busy checkout would bury its own
# point. The count is always printed, so a truncated list is still honest.
_MAX_LISTED: Final = 20

# `git status --porcelain` v1: two status letters, a space, then the path.
_PORCELAIN_PREFIX: Final = 3


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
    in_scope = frozenset(path for item in mutations for path in item.paths)
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
