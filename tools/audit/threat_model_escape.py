"""The independent third key that measures the escape space (#199 unit A).

Reads one Markdown file named on the command line -- the audit ran it against
``docs/security/threat-model.md`` only. Not a repo-wide walker, not CI-wired; see
``tools/audit/README.md``.

**Why this exists.** The audit's completeness argument said its keys left an
escape space and that the space was empty. Measuring that with the keys which
*define* it is circular: asking the census whether the census missed anything
returns "no" by construction. This key therefore shares no rule with the other
two -- different containers, and a verb list of 58 stems against the sweep's 22,
including ordinary words like ``read``, ``name`` and ``return``, so that a line
missed here is verb-free under any plausible key rather than merely under the
audit's.

Four sub-keys:

``K-A``  mermaid node labels -- a container with no line-opening bold run at all.
``K-B``  every Markdown table, so the audit's 9 + 2 can be checked against the
         total rather than trusted.
``K-C``  headings that themselves assert.
``K-D``  the escape space proper: a line naming a ``src`` symbol that neither
         opens with a bold run nor carries any verb.

**A stated limitation.** The sweep is line-oriented and this document wraps at
about 80 columns, so a claim routinely splits across a keyed head line and an
unkeyed tail. ``K-D``'s population is therefore dominated by continuations and by
test-path citations; what it establishes is that no claim *head* escaped, not
that no line did.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

SRC_SYMBOL = re.compile(
    r"`[^`]*(?:"
    r"[A-Za-z_][\w/]*\.py"  # a module path
    r"|::[A-Za-z_]\w*"  # ::symbol
    r"|MAX_[A-Z_]+|MIN_[A-Z_]+"  # a bound constant
    r"|\b[A-Z][A-Za-z]+\.[a-z_]\w*"  # Class.method
    r")[^`]*`"
)

#: Deliberately generous -- wider than any plausible audit sweep.
VERBS = re.compile(
    r"\b(refus\w*|reject\w*|block\w*|enforc\w*|validat\w*|check\w*|bound\w*|gat\w*|"
    r"scan\w*|withh\w*|prevent\w*|requir\w*|read\w*|write\w*|open\w*|record\w*|"
    r"emit\w*|publish\w*|serv\w*|deni\w*|deny\w*|allow\w*|permit\w*|limit\w*|cap\w*|"
    r"strip\w*|sanitis\w*|sanitiz\w*|redact\w*|hash\w*|sign\w*|verif\w*|purg\w*|"
    r"exclud\w*|filter\w*|refresh\w*|guard\w*|hold\w*|stop\w*|catch\w*|raise\w*|"
    r"return\w*|produc\w*|comput\w*|charg\w*|track\w*|pin\w*|resolv\w*|follow\w*|"
    r"copi\w*|copy\w*|delet\w*|remov\w*|clos\w*|refer\w*|name\w*|call\w*|run\w*)\b",
    re.IGNORECASE,
)
BOLD_OPENER = re.compile(r"^\s*(?:>\s*)*(?:[-*]\s+)?\*\*")
TEST_CITATION = re.compile(r"tests?/|::test_|_test\b")

_MIN_TABLE_LINES = 3
_EXCERPT = 120
#: ``argv`` is the program plus exactly one Markdown path.
_EXPECTED_ARGV = 2


class Census:
    """Every line classified by the container it sits in."""

    def __init__(self, lines: list[str]) -> None:
        self.lines = lines
        self.kind: dict[int, str] = {}
        self.mermaid: list[tuple[int, str]] = []
        self.tables: dict[int, list[int]] = {}
        self._classify()

    def _classify(self) -> None:
        in_fence: int | None = None
        fence_lang = ""
        current_table: int | None = None
        for line_no, line in enumerate(self.lines, start=1):
            fence = re.match(r"^\s*```(\w*)", line)
            if fence is not None:
                if in_fence is None:
                    in_fence, fence_lang = line_no, fence.group(1)
                else:
                    in_fence = None
                self.kind[line_no] = "fence"
                continue
            if in_fence is not None:
                self.kind[line_no] = f"fence:{fence_lang}"
                if fence_lang == "mermaid":
                    self.mermaid.append((line_no, line))
                continue
            stripped = re.sub(r"^\s*(?:>\s*)*", "", line)
            if stripped.startswith("|"):
                if current_table is None:
                    current_table = line_no
                    self.tables[current_table] = []
                self.tables[current_table].append(line_no)
                self.kind[line_no] = "table"
                continue
            current_table = None
            if stripped.startswith("#"):
                self.kind[line_no] = "heading"
            elif re.match(r"^\s*(?:>\s*)*(?:[-*]|\d+\.)\s", line):
                self.kind[line_no] = "listitem"
            elif not stripped.strip():
                self.kind[line_no] = "blank"
            else:
                self.kind[line_no] = "para"


def main() -> int:
    if len(sys.argv) != _EXPECTED_ARGV:
        print(f"usage: {Path(sys.argv[0]).name} <markdown-file>")
        return 2

    lines = Path(sys.argv[1]).read_text(encoding="utf-8").splitlines()
    census = Census(lines)
    print(f"document lines: {len(lines)}")

    print("\n=== K-B: every Markdown table ===")
    sized = [(start, len(body)) for start, body in census.tables.items()]
    real = [(start, n) for start, n in sized if n >= _MIN_TABLE_LINES]
    print(f"  tables: {len(census.tables)}  (>= header+rule+1 row: {len(real)})")
    print(f"  data rows across them: {sum(n - 2 for _, n in real)}")
    print("  the audit keyed 9 tables / 62 rows, plus K-2's 2 tables / 7 rows")

    print("\n=== K-A: mermaid labels naming a src symbol or asserting ===")
    for line_no, line in census.mermaid:
        if SRC_SYMBOL.search(line) or re.search(r"refus|reject|block|gate|withh|deni", line, re.I):
            print(f"  :{line_no:<5} {line.strip()[:_EXCERPT]}")

    print("\n=== K-C: headings that assert ===")
    headings = [
        line_no
        for line_no, line in enumerate(lines, start=1)
        if census.kind.get(line_no) == "heading" and (SRC_SYMBOL.search(line) or VERBS.search(line))
    ]
    print(f"  ({len(headings)} headings)")

    print("\n=== K-D: src-symbol lines, NOT bold-opened, carrying NO verb ===")
    escaped: list[int] = []
    for line_no, line in enumerate(lines, start=1):
        kind = census.kind.get(line_no, "")
        if kind in ("blank", "fence") or kind.startswith("fence"):
            continue
        if not SRC_SYMBOL.search(line) or BOLD_OPENER.match(line) or VERBS.search(line):
            continue
        escaped.append(line_no)
        print(f"  :{line_no:<5} [{kind:<8}] {line.strip()[:_EXCERPT]}")

    citations = [n for n in escaped if TEST_CITATION.search(lines[n - 1])]
    print(f"\n  ({len(escaped)} lines; {len(citations)} cite a test path)")
    print("  The remainder is dominated by line-wrapped continuations of keyed")
    print("  sentences -- see this module's docstring on the line-oriented limit.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
