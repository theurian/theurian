"""POP-2: the threat model's issue cites, one row per cite (#427).

Reads one Markdown file named on the command line -- the sweep ran it against
``docs/security/threat-model.md`` only. Not a repo-wide walker, not CI-wired; see
``tools/audit/README.md``.

**What the key is.** A cite is a bracketed issue or pull-request reference,
``[#N]``. Every occurrence is a row: a number cited on six lines is six rows,
because the classification is a property of the *context*, not of the number.
At the anchor commit every ``[#N]`` in this file is a Markdown link whose target
is a ``github.com/theurian/theurian/{issues,pull}/N`` URL, and no such URL is
reachable under any other label -- so the bracket form and the URL form select
the same population here. The script reports both counts; if they ever diverge,
the key has stopped covering the file.

**What the script cannot do, and why classification lives elsewhere.** Whether a
cite is (a) an owner-of-a-residual -- "[#N] removes this face", which is a defect
when #N is closed -- or (b) a historical record -- "fixed in [#N]", where closed
is correct -- is decided by the surrounding prose *and* by the cited issue's
state in the tracker. A script has neither: it cannot read GitHub, and no regex
over the sentence separates the two (the confirmed member at the anchor commit
says "removes", a verb the #199 unit-A sweep's list did not carry). So this
prints the population and its context; the per-cite classification is recorded by
hand in
``docs/work-logs/2026-08-31-427-owner-cite-sweep.md``.

**Escape space, measured rather than assumed.** Bare ``#N`` mentions -- not
bracketed, not linked -- are outside this key. The script counts them separately
so the gap is a number rather than a silence, and the #427 sweep found two
(a)-class defects there that the bracket key does not reach.

Known false positives in that escape count, recorded rather than special-cased:
a Mermaid hex colour (``fill:#1f6f4a``) and an in-document ordinal (``residual
#2``) both match ``#N`` and neither is a cite. They inflate the escape number,
never the population, which is why they are left in.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

#: A bracketed cite: the population key.
CITE = re.compile(r"\[#(?P<number>[0-9]+)\]")

#: A tracker URL, used only to check that the bracket key still covers the file.
URL = re.compile(r"github\.com/theurian/theurian/(?:issues|pull)/(?P<number>[0-9]+)")

#: A bare mention: the measured escape space, not a member.
BARE = re.compile(r"(?:^|[^0-9A-Za-z/\[])#(?P<number>[0-9]+)")

#: Characters of the preceding line kept, because prose wraps mid-sentence and
#: the verb that decides the classification is often on the line before.
_LEAD_WIDTH = 56
_CONTEXT_WIDTH = 150
#: ``argv`` is the program plus exactly one Markdown path.
_EXPECTED_ARGV = 2


class Cite:
    """One bracketed cite, with the context a classifier reads."""

    def __init__(self, number: int, line_no: int, context: str) -> None:
        self.number = number
        self.line_no = line_no
        self.context = context


def _collapse(line: str) -> str:
    """Drop link targets, which are noise once the label carries the number."""
    return re.sub(r"\]\((?:https?://)[^)]*\)", "]", line).strip()


def _context(lines: list[str], line_no: int, number: int) -> str:
    """The cite's line, led by the tail of the one before it."""
    previous = lines[max(0, line_no - 2) : line_no - 1]
    lead = _collapse(previous[0]) if previous else ""
    if len(lead) > _LEAD_WIDTH:
        lead = "..." + lead[-_LEAD_WIDTH:]
    body = _collapse(lines[line_no - 1])
    joined = f"{lead} | {body}" if lead else body
    marker = joined.find(f"[#{number}]", len(lead))
    if len(joined) <= _CONTEXT_WIDTH or marker < 0:
        return joined[:_CONTEXT_WIDTH]
    begin = max(0, marker - _LEAD_WIDTH)
    return ("..." if begin else "") + joined[begin : begin + _CONTEXT_WIDTH]


def _cites(lines: list[str]) -> list[Cite]:
    found: list[Cite] = []
    for line_no, line in enumerate(lines, start=1):
        for match in CITE.finditer(line):
            number = int(match.group("number"))
            found.append(Cite(number, line_no, _context(lines, line_no, number)))
    return found


def main() -> int:
    if len(sys.argv) != _EXPECTED_ARGV:
        print(f"usage: {Path(sys.argv[0]).name} <markdown-file>")
        return 2

    text = Path(sys.argv[1]).read_text(encoding="utf-8")
    lines = text.splitlines()

    cites = _cites(lines)
    numbers = sorted({cite.number for cite in cites})
    url_numbers = sorted({int(match.group("number")) for match in URL.finditer(text)})
    bare = [
        (line_no, int(match.group("number")))
        for line_no, line in enumerate(lines, start=1)
        for match in BARE.finditer(line)
    ]

    print(f"POP-2 CITES: {len(cites)} occurrences over {len(numbers)} distinct numbers")
    print(f"lines carrying at least one cite: {len({cite.line_no for cite in cites})}")
    coverage = "same population" if url_numbers == numbers else "KEY NO LONGER COVERS THE FILE"
    print(f"tracker URLs reach {len(url_numbers)} distinct numbers -- {coverage}")
    print(
        f"escape space (bare #N, outside the key): {len(bare)} mentions "
        f"over {len({number for _, number in bare})} distinct numbers"
    )
    print()
    for cite in cites:
        print(f"  #{cite.number:<5} :{cite.line_no:<5} {cite.context}")
    print()
    print("distinct cited numbers:")
    print("  " + " ".join(f"#{number}" for number in numbers))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
