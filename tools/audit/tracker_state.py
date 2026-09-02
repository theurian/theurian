"""Whether a cited number is open, and how that answer is obtained (#199 unit B).

Two of the audits beside this file classify a cite by the *state* of what it
cites: a residual whose owner is closed has no owner, and a control whose issue
merged is not owed by anybody. Neither question can be answered by reading the
repository, so this module answers it in the one order that does not rot.

**Live first, snapshot second, and the direction of the failure decides.** A
snapshot that has gone stale says ``open`` about an issue closed since, and a
stale ``open`` is a **false green** -- the audit reports a discharged owner that
is not one. So the live ``gh`` query is the default, and the committed snapshot
in ``tracker-state.json`` is the fallback, used with its measurement date printed
so a run without network says which day its verdicts are from. ``--offline``
forces the snapshot, which is what makes the committed census output
reproducible at the commit it was measured against.

The snapshot is refreshed by printing a new one and redirecting it, never by the
audit rewriting a tracked file mid-run::

    uv run --frozen python tools/audit/tracker_state.py --refresh \\
        > tools/audit/tracker-state.json
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

SNAPSHOT: Final = Path(__file__).with_name("tracker-state.json")

REPOSITORY: Final = "theurian/theurian"

_GH_TIMEOUT_SECONDS: Final = 60

#: The states that mean "somebody still owes this". A merged pull request is
#: *not* one of them: #444 recorded PR-as-owner as its own defect shape, because
#: a merged PR closes and can own nothing afterwards.
OPEN_STATES: Final[frozenset[str]] = frozenset({"issue:open", "pr:open"})


def _gh(*arguments: str) -> str | None:
    try:
        completed = subprocess.run(  # noqa: S603 - fixed argv, no shell, no caller input
            ["gh", *arguments],  # noqa: S607 - resolved via PATH, as every other tool here
            check=False,
            capture_output=True,
            text=True,
            timeout=_GH_TIMEOUT_SECONDS,
            env=dict(os.environ),
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return completed.stdout if completed.returncode == 0 else None


#: How many issues and how many pull requests one ``gh list`` call asks for.
#:
#: **The page size is a silent-truncation risk, and that is the false-green
#: direction.** ``gh`` returns the newest ``--limit`` entries and says nothing
#: when there are more, so a repository that outgrew this number would hand the
#: audits a table missing its *oldest* numbers -- and a number absent from the
#: table is not open, which is the verdict a cite of it then gets. Every audit
#: here would quietly stop reporting the dead owners it exists to find.
_PAGE: Final = 2000


def _live() -> dict[str, str] | None:
    """Every issue and pull-request state the tracker holds, or ``None``.

    Raises when a page comes back full, because a full page cannot be
    distinguished from a truncated one and the failure is silent in the
    direction that matters: see :data:`_PAGE`.
    """
    found: dict[str, str] = {}
    for kind, command in (
        ("issue", ("issue", "list")),
        ("pr", ("pr", "list")),
    ):
        payload = _gh(
            *command,
            "--repo",
            REPOSITORY,
            "--state",
            "all",
            "--limit",
            str(_PAGE),
            "--json",
            "number,state",
        )
        if payload is None:
            return None
        entries = json.loads(payload)
        if len(entries) >= _PAGE:
            message = (
                f"`gh {' '.join(command)}` returned {len(entries)} entries at "
                f"--limit {_PAGE}: the page is full, so the tracker table may be "
                f"missing its oldest numbers. A number the table does not carry "
                f"reads as `not open`, which silently clears every dead-owner "
                f"verdict below it. Raise `_PAGE` and re-run."
            )
            raise RuntimeError(message)
        for entry in entries:
            found[str(entry["number"])] = f"{kind}:{entry['state'].lower()}"
    return found or None


def _snapshot() -> tuple[dict[str, str], str]:
    document = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    return document["state"], document["measured"]


def states(*, offline: bool = False) -> tuple[dict[str, str], str]:
    """``({number: "issue:open"}, provenance)``.

    The provenance string is printed by every caller, so a verdict never appears
    without the day its evidence is from.
    """
    if not offline:
        live = _live()
        if live is not None:
            return live, f"live `gh` query, {datetime.now(tz=UTC).date().isoformat()}"
    table, measured = _snapshot()
    return table, f"committed snapshot, measured {measured} (no live query)"


def is_open(table: dict[str, str], number: str) -> bool:
    """Whether the tracker still owes this number.

    A number the table does not carry is **not** open: it is a cite of something
    that does not exist in this repository, which is a defect of its own and one
    the caller reports rather than silently clears.
    """
    return table.get(number, "") in OPEN_STATES


def main(argv: list[str]) -> int:
    if "--refresh" not in argv:
        table, provenance = states(offline="--offline" in argv)
        print(f"{len(table)} numbers, {provenance}")
        return 0
    live = _live()
    if live is None:
        print("gh is unavailable; the snapshot is unchanged", file=sys.stderr)
        return 2
    document = {
        "measured": datetime.now(tz=UTC).date().isoformat(),
        "commit": (
            subprocess.run(
                ["git", "rev-parse", "HEAD"],  # noqa: S607 - fixed argv, no shell, via PATH
                check=True,
                capture_output=True,
                text=True,
                timeout=_GH_TIMEOUT_SECONDS,
            ).stdout.strip()
        ),
        "repository": REPOSITORY,
        "how": (
            "gh issue list --state all --limit 2000 --json number,state; the same for gh pr list"
        ),
        "state": {number: live[number] for number in sorted(live, key=int)},
    }
    print(json.dumps(document, indent=1, sort_keys=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
