"""POP-1e: the threat model's retraction and discharge blocks (#199 unit A).

Reads one Markdown file named on the command line -- the audit ran it against
``docs/security/threat-model.md`` only. Not a repo-wide walker, not CI-wired; see
``tools/audit/README.md``.

**Why the key is structural rather than a regex.** The marker text does not
determine the role. ``**What...said`` opens a member at one site and continues an
enclosing one at two others, so no pattern over the text splits them: position
decides. A marker is a MEMBER iff it opens a retraction block that is not already
inside one. A Markdown blockquote ends at the first line not beginning with
``>``; a depth-0 marker opens no quote block, so nothing can continue it.

Two disjoint kinds, both retracting or restating a claim the document made:
``1e-i`` CORRECTION (the document was wrong) and ``1e-ii`` DISCHARGE (the
document was right and the owed control has since landed).

Known false positive, recorded rather than special-cased: a bold run that begins
a line by accident of *wrapping* is indistinguishable from a block opener.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

#: A bold run opening a line, after any blockquote nesting and indentation.
MARKER = re.compile(r"^(?P<quote>[ \t>]*)\*\*(?P<text>[^*].*?)(?:\*\*|$)")

CORRECTION = re.compile(
    r"^(Corrected|Correction|Amended|Amendment|Retracted|Resolved|Revised"
    r"|Superseded|Withdrawn|This sentence|What (this|it) (entry )?said)\b",
    re.IGNORECASE,
)
DISCHARGE = re.compile(r"^(Discharged|Closed)\b", re.IGNORECASE)

_SUMMARY_WIDTH = 62
#: ``argv`` is the program plus exactly one Markdown path.
_EXPECTED_ARGV = 2


class Hit:
    """One marker, with the position that decides whether it opens a block."""

    def __init__(self, line_no: int, depth: int, kind: str, text: str) -> None:
        self.line_no = line_no
        self.depth = depth
        self.kind = kind
        self.text = text


def _hits(lines: list[str]) -> list[Hit]:
    found: list[Hit] = []
    for line_no, line in enumerate(lines, start=1):
        match = MARKER.match(line)
        if match is None:
            continue
        text = match.group("text")
        if CORRECTION.match(text):
            kind = "1e-i"
        elif DISCHARGE.match(text):
            kind = "1e-ii"
        else:
            continue
        found.append(Hit(line_no, match.group("quote").count(">"), kind, text[:_SUMMARY_WIDTH]))
    return found


def _continues(lines: list[str], previous: Hit, line_no: int) -> bool:
    """Whether ``line_no`` sits inside the block ``previous`` opened."""
    if previous.depth == 0:
        return False
    return all(line.lstrip().startswith(">") for line in lines[previous.line_no : line_no - 1])


def main() -> int:
    if len(sys.argv) != _EXPECTED_ARGV:
        print(f"usage: {Path(sys.argv[0]).name} <markdown-file>")
        return 2

    lines = Path(sys.argv[1]).read_text(encoding="utf-8").splitlines()

    members: list[Hit] = []
    continuations: list[Hit] = []
    for hit in _hits(lines):
        if any(_continues(lines, member, hit.line_no) for member in members):
            continuations.append(hit)
        else:
            members.append(hit)

    corrections = sum(1 for member in members if member.kind == "1e-i")
    discharges = sum(1 for member in members if member.kind == "1e-ii")
    print(
        f"POP-1e MEMBERS: {len(members)}  "
        f"( 1e-i correction {corrections} + 1e-ii discharge {discharges} )"
    )
    print(f"continuations (belong to an enclosing member, NOT counted): {len(continuations)}")
    print()
    for member in members:
        print(f"  {member.kind:<6} :{member.line_no:<5} depth={member.depth}  {member.text}")
    print()
    for continuation in continuations:
        print(
            f"  CONT   :{continuation.line_no:<5} depth={continuation.depth}  {continuation.text}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
