"""Per-module kill rates, read back out of what the sweep filed (#378).

A population key in the sense ``tools/audit/README.md`` gives the word: it
reports and always exits ``0``. It is not a gate, because "this module's kill
rate fell" is a question a human asks while choosing where to look, not an
invariant a job can hold.

**The tracker is the record, and there is no second copy.** Every number here is
recomputed from the issues ``tools/sweep.py`` filed, so nothing can go stale
against them and there is no store to migrate when the body shape changes. The
cost is that a closed issue whose body was hand-edited is read as filed; that is
the same trade ``tracker_state.py`` makes, and the query date is printed for the
same reason.

**Read-only, and deliberately narrow.** One ``gh issue list`` carrying
``--json body``, and nothing else -- not even ``gh issue view``, because the
listing already returns every field this reads. This module never writes to the
tracker, never edits a body, and passes no caller string to ``gh`` except the
two ``argparse`` values, one of which is ``choices``-constrained and the other
an ``int``.

Two body shapes, because the sweep's unit changed
-------------------------------------------------
``sweep_filing`` filed one issue per *file* until the block form landed and one
issue per *run* after it. Both are on the tracker and both are evidence, so this
reads both and says which shape each issue was.

=================  ======================================================
Shape              How a verdict finds its module
=================  ======================================================
**per-run**        ``## Modules`` section, then one
                   :data:`sweep_filing.MODULE_HEADING_PATTERN` heading per
                   module, then that module's
                   :func:`sweep_filing.parse_verdict_line` lines.
**per-file**       one ``- **Target:** `path`\\ `` line in the header, and
                   the ``## Verdicts`` lines belong to it.
=================  ======================================================

In both, the region **opens** at the heading named above and **closes** at the
next ``##``; a verdict line outside one, or inside a code fence, attributes to
nothing. Saying that of the per-run shape alone is what let a legacy orphan be
counted against the header's target.

The per-run keys are imported from :mod:`sweep_filing` rather than written out
here, so the filer and this reader cannot drift apart; the suite's round-trip
test builds a payload with the filer and parses it with :func:`parse_body`.
:data:`_LEGACY_TARGET_PATTERN` is the exception and is local on purpose --
nothing writes that line any more, so it pins nothing and belongs with the
reader that still has to understand history.

Usage
-----
::

    uv run --frozen python tools/audit/sweep_kill_rates.py
    uv run --frozen python tools/audit/sweep_kill_rates.py --state open --limit 50
    uv run --frozen python tools/audit/sweep_kill_rates.py --json
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from collections import defaultdict
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sweep_filing import (  # the path insert above is what makes this importable
    LABEL,
    MODULE_HEADING_PATTERN,
    MODULES_HEADING,
    parse_verdict_line,
)
from sweep_verdict import UNHELD_VERDICTS

REPOSITORY: Final = "theurian/theurian"

_GH_TIMEOUT_SECONDS: Final = 60

#: The pre-block header line naming the single file an issue was about.
#:
#: Local, not imported: no current code path writes it, so importing it from the
#: filer would assert a contract that no longer exists. It is a historical key
#: for the five issues filed before the block form.
_LEGACY_TARGET_PATTERN: Final = re.compile(
    r"^- \*\*Target:\*\* (?P<fence>`+) ?(?P<path>.+?) ?(?P=fence)$"
)

#: The pre-block heading a legacy body's verdict list sat under.
#:
#: Local for the same reason as :data:`_LEGACY_TARGET_PATTERN`: nothing writes it
#: now. It is what gives the legacy shape an *attributable region* of its own, so
#: the section-break rule below can be one rule rather than two.
_LEGACY_VERDICTS_HEADING: Final = "## Verdicts"

#: A verdict that is a statement about the run rather than about a module.
_CONTROL_VERDICTS: Final = frozenset({"control-green", "control-red"})

#: Where an attributable region stops. Any other ``##`` closes it -- in practice
#: ``## Reproduce``, and ``## Unattributed verdicts``, whose lines are exactly
#: the ones that must not be credited to the module or target last seen.
#:
#: **This holds in both shapes, and briefly did not.** The rule was written
#: against the per-run shape, where leaving ``## Modules`` clears the owner. In a
#: legacy body nothing ever entered that region, so the owner fell back to the
#: header's target for the whole document and an orphan under
#: ``## Unattributed verdicts`` was counted against it -- deflating that module's
#: rate, which is the direction that reads as a finding. Both shapes now name the
#: heading that *opens* their attributable region and nothing else attributes.
_SECTION_BREAK: Final = "## "

#: An opening or closing code fence, as :func:`sweep_filing._block` writes them:
#: three or more backticks alone on a line, the opener optionally carrying a
#: language.
#:
#: Fenced lines are skipped because a filed body quotes **repository source** in
#: a ``diff`` block, and a removed line that began with a single space renders as
#: ``- **KILLED** `label` (1.0s)`` -- a verdict line by every rule this module
#: has. Measured 2026-09-19 over the 142-module census: 0 lines would render that
#: way today, so this is a guard against the corpus rather than a fix for it --
#: and the corpus is the whole rotating census, so "no file does that yet" is a
#: property of this week's tree and not of the parser.
_FENCE_PATTERN: Final = re.compile(r"^(?P<ticks>`{3,})(?P<language>[^`]*)$")


@dataclass(frozen=True)
class ModuleVerdict:
    """One mutation verdict, and the module it belongs to."""

    path: str
    label: str
    verdict: str


@dataclass
class Tally:
    """What one module's mutations came back as, across every issue seen."""

    path: str
    issues: set[int] = field(default_factory=set)
    killed: int = 0
    unheld: int = 0
    other: int = 0

    @property
    def mutations(self) -> int:
        return self.killed + self.unheld + self.other

    @property
    def rate(self) -> float | None:
        """Killed over mutations, or ``None`` when nothing decisive came back.

        ``None`` rather than ``0.0``: a module with no readable verdict has not
        scored zero, it has not been measured, and printing 0% for it would
        point a reader at the module with the best-looking evidence of all.
        """
        decisive = self.killed + self.unheld
        return self.killed / decisive if decisive else None


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


def _unfenced(lines: Iterable[str]) -> Iterator[str]:
    """Every line outside a code fence, stripped.

    Split out so :func:`parse_body` reads as attribution alone. A filed body
    quotes repository source in a ``diff`` block, and source is not evidence
    about itself: a removed line that began with a single space renders as
    ``- **KILLED** `label` (1.0s)``, which every other rule here would read as a
    verdict.

    Closing follows CommonMark rather than "any fence toggles": a closer is at
    least as long as its opener and carries no language, so a shorter run of
    backticks *inside* a longer block does not end it early and hand the rest of
    a diff back to the parser.
    """
    fence = 0
    for raw in lines:
        line = raw.strip()
        found = _FENCE_PATTERN.match(line)
        if found is not None:
            ticks = len(found.group("ticks"))
            if fence == 0:
                fence = ticks
            elif ticks >= fence and not found.group("language").strip():
                fence = 0
            continue
        if not fence:
            yield line


def parse_body(body: str) -> tuple[ModuleVerdict, ...]:
    """Every mutation verdict in one filed body, attributed to its module.

    Handles both shapes (see the module docstring). Control verdicts are dropped
    -- they are the run's, not a module's -- and so is anything outside an
    attributable region, which is what stops an unattributable outcome being
    credited to whichever module happened to be last.

    **One owner, set by whichever heading opens its shape's region and cleared by
    every other ``##``.** Writing it as two rules is what let the legacy branch
    attribute a whole document to its header target, orphan sections included;
    the per-run branch was correct and the shared docstring described only it.

    Fenced lines never attribute at all: a filed body quotes repository source in
    a ``diff`` block, and source is not evidence about itself.

    Unknown shape returns empty rather than raising: a body somebody hand-edited
    is not a reason for a reporting tool to stop reporting the other forty.
    """
    lines = body.splitlines()
    legacy_target: str | None = None
    for line in lines:
        found = _LEGACY_TARGET_PATTERN.match(line.strip())
        if found is not None:
            legacy_target = found.group("path")
            break

    collected: list[ModuleVerdict] = []
    in_modules = False
    owner: str | None = None
    for line in _unfenced(lines):
        if line == MODULES_HEADING:
            in_modules, owner = True, None
            continue
        if line == _LEGACY_VERDICTS_HEADING:
            in_modules, owner = False, legacy_target
            continue
        if line.startswith(_SECTION_BREAK):
            in_modules, owner = False, None
            continue
        heading = MODULE_HEADING_PATTERN.match(line)
        if heading is not None:
            owner = heading.group("path") if in_modules else None
            continue
        verdict = parse_verdict_line(line)
        if verdict is None:
            continue
        name, label = verdict
        if name in _CONTROL_VERDICTS or owner is None:
            continue
        collected.append(ModuleVerdict(path=owner, label=label, verdict=name))
    return tuple(collected)


def _issues(state: str, limit: int) -> tuple[dict[str, object], ...]:
    listing = _gh(
        "issue",
        "list",
        "--repo",
        REPOSITORY,
        "--label",
        LABEL,
        "--state",
        state,
        "--limit",
        str(limit),
        "--json",
        "number,title,body,state,createdAt",
    )
    if listing is None:
        return ()
    try:
        loaded = json.loads(listing)
    except json.JSONDecodeError:
        return ()
    return tuple(item for item in loaded if isinstance(item, dict))


def tally(issues: Sequence[dict[str, object]]) -> dict[str, Tally]:
    """Fold every issue's verdicts into one row per module."""
    rows: dict[str, Tally] = defaultdict(lambda: Tally(path=""))
    for issue in issues:
        raw = issue.get("number")
        # `runs` counts distinct issues, so an issue whose number did not come
        # back as an integer lands in one shared bucket rather than inventing a
        # number that could collide with a real one.
        number = raw if isinstance(raw, int) else -1
        for item in parse_body(str(issue.get("body", "") or "")):
            row = rows[item.path]
            row.path = item.path
            row.issues.add(number)
            if item.verdict == "KILLED":
                row.killed += 1
            elif item.verdict in UNHELD_VERDICTS:
                row.unheld += 1
            else:
                row.other += 1
    return dict(rows)


def _render(rows: dict[str, Tally], issues: int, asof: str) -> str:
    lines = [
        f"async-sweep kill rates, read from {issues} issue(s) under `{LABEL}` in {REPOSITORY}",
        f"measured {asof}",
        "",
    ]
    header = f"{'module':<64} {'runs':>5} {'muts':>5} {'kill':>5} {'live':>5} {'rate':>7}"
    # Derived, not counted: a hand-written width drifts the first time a column
    # does, and a rule that no longer spans its table reads as a broken render.
    lines += [header, "-" * len(header)]

    # Worst measured rate first, unmeasured modules last: the table is read to
    # choose where to look next, and a module with no decisive verdict is not a
    # candidate for that.
    def order(item: Tally) -> tuple[int, float, str]:
        return (1, 0.0, item.path) if item.rate is None else (0, item.rate, item.path)

    for row in sorted(rows.values(), key=order):
        rate = "n/a" if row.rate is None else f"{row.rate:6.1%}"
        lines.append(
            f"{row.path:<64} {len(row.issues):>5} {row.mutations:>5} "
            f"{row.killed:>5} {row.unheld:>5} {rate:>7}"
        )
    if not rows:
        lines.append("(no module verdict parsed from any issue)")
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="tools/audit/sweep_kill_rates.py",
        description="Per-module mutation kill rates, recomputed from filed async-sweep issues.",
    )
    parser.add_argument("--state", default="all", choices=("all", "open", "closed"))
    parser.add_argument("--limit", type=int, default=200)
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)

    asof = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
    issues = _issues(args.state, args.limit)
    rows = tally(issues)
    if args.as_json:
        print(
            json.dumps(
                {
                    "measuredAt": asof,
                    "repository": REPOSITORY,
                    "issues": len(issues),
                    "modules": [
                        {
                            "module": row.path,
                            "runs": len(row.issues),
                            "mutations": row.mutations,
                            "killed": row.killed,
                            "unheld": row.unheld,
                            "rate": row.rate,
                        }
                        for row in sorted(rows.values(), key=lambda item: item.path)
                    ],
                },
                indent=2,
            )
        )
    else:
        print(_render(rows, len(issues), asof))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
