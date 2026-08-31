"""The container census and the active-voice verb sweep (#199 unit A).

Reads one Markdown file named on the command line -- the audit ran it against
``docs/security/threat-model.md`` only. Not a repo-wide walker, not CI-wired; see
``tools/audit/README.md``.

Two keys, deliberately orthogonal, because each covers the other's blind spot:

* **The container census** keys on the *container* -- every bold-labelled block
  opener at any blockquote depth -- rather than on a label. This is what the
  audit's first key got wrong: ``line.startswith("**Controls")`` is a prefix
  match on a plural noun, and it misses the five blocks labelled ``**Control``
  in the singular, every one of which belongs to a Critical entry.
* **The verb sweep** keys on vocabulary and finds claims the census's container
  rule does not reach. It is the cross-check and not the primary key: the block
  at ``:4940`` ("Control, part A") carries zero verb hits, because its claim is
  spelled "reads each ... referenced id".

The census is load-bearing; the sweep is the cross-check. A claim in neither is
the escape space, which ``threat_model_escape.py`` measures with a third key that
shares no rule with these two.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

#: A bold run at the head of a line, after any blockquote nesting.
BOLD_RUN = re.compile(r"^(?P<quote>[ \t>]*)\*\*(?P<label>[^*].*?)(?:\*\*|$)")
CONTROLS_LABEL = re.compile(r"\*\*Controls")
SRC_SYMBOL = re.compile(r"`[^`]*(?:[A-Za-z_][\w/]*\.py|::[A-Za-z_]\w*|MAX_[A-Z_]+)[^`]*`")

#: The audit's control-verb alternation: 22 stems, active voice.
VERBS = re.compile(
    r"\b(refuses|rejects|escapes|redacts|withholds|clamps|caps|bounds|validates"
    r"|enforces|verifies|prevents|blocks|requires|forbids|denies|drops|filters"
    r"|scans|sanitis\w*|sanitiz\w*|limits)\b",
    re.IGNORECASE,
)

#: The header patterns that select a control-claim table (POP-1b). A table is a
#: member iff its header row matches one of these; every data row is then a
#: member. Stated here because a population is only re-runnable if its selection
#: rule is, and this one was prose until #199's review asked for it.
TABLE_HEADERS = (
    re.compile(r"^\|\s*Surface\s*\|\s*Assertion\s*\|"),
    re.compile(r"^\|\s*Bound\s*\|\s*Symbol\s*\|"),
    re.compile(r"^\|\s*Member\s*\|.*\|\s*Bounded by\s*\|"),
    re.compile(r"^\|\s*Quantity\s*\|\s*What would bound it\s*\|"),
    re.compile(r"^\|\s*Defense\s*\|\s*Why no test can fail without it\s*\|"),
    re.compile(r"^\|\s*ID\s*\|\s*Threat\s*\|\s*STRIDE\s*\|"),
    re.compile(r"^\|\s*Surface\s*\|\s*What it says\s*\|\s*Owner\s*\|"),
    re.compile(r"^\|\s*Path\s*\|.*ranking.*\|\s*Bounded by\s*\|"),
    re.compile(r"^\|\s*Result field\s*\|\s*Source\s*\|\s*Disposition\s*\|"),
)

FUTURE_CONTROLS = re.compile(r"^\*Future controls")
RETRACTION = re.compile(
    r"^[ \t>]*\*\*(Corrected|Correction|Amended|Amendment|Retracted|Resolved|Revised"
    r"|Superseded|Withdrawn|Discharged|Closed|This sentence|What (this|it) (entry )?said)\b",
    re.IGNORECASE,
)

_LABEL_WIDTH = 70
#: ``argv`` is the program plus exactly one Markdown path.
_EXPECTED_ARGV = 2
#: The first line has no predecessor to inspect.
_FIRST_LINE = 2


def _block_extent(lines: list[str], start: int) -> range:
    """A bold-labelled paragraph or blockquote runs to the next blank non-quote line."""
    quoted = lines[start - 1].lstrip().startswith(">")
    index = start
    while index < len(lines):
        nxt = lines[index]
        if quoted:
            if not nxt.lstrip().startswith(">"):
                break
        elif not nxt.strip():
            break
        index += 1
    return range(start, index + 1)


def _table_extent(lines: list[str], header: int) -> range:
    index = header
    while index < len(lines) and lines[index].lstrip().startswith("|"):
        index += 1
    return range(header, index + 1)


def _covered(lines: list[str]) -> set[int]:
    """Every line inside a keyed member, by the keys that are *derivable*.

    1a (``**Controls`` blocks), 1b (tables matching :data:`TABLE_HEADERS`),
    1d (``*Future controls`` paragraphs) and 1e (retraction blocks) all follow
    from a rule and are recomputed here. **POP-1c is not included**: those eleven
    floating prose assertions were selected by hand from the verb sweep, so no
    rule reproduces them, and a tool that pretended otherwise would be asserting
    a key it does not have.
    """
    covered: set[int] = set()
    for line_no, line in enumerate(lines, start=1):
        stripped = line.lstrip("> ").rstrip()
        opens_block = (
            CONTROLS_LABEL.search(line) or FUTURE_CONTROLS.match(stripped) or RETRACTION.match(line)
        )
        if opens_block:
            covered.update(_block_extent(lines, line_no))
        elif any(header.match(stripped) for header in TABLE_HEADERS):
            covered.update(_table_extent(lines, line_no))
    return covered


def _opens_a_block(lines: list[str], line_no: int) -> bool:
    """A bold run opens a block only if a blank line -- or a blank quote line --
    precedes it. Without this the census counts every bold run in running prose,
    which is a different and much larger population (322 rather than 276 at
    ``06de58a``), and not the container the key is about.
    """
    previous = lines[line_no - 2] if line_no >= _FIRST_LINE else ""
    return previous.strip() in ("", ">")


def main() -> int:
    if len(sys.argv) != _EXPECTED_ARGV:
        print(f"usage: {Path(sys.argv[0]).name} <markdown-file>")
        return 2

    lines = Path(sys.argv[1]).read_text(encoding="utf-8").splitlines()

    openers: list[tuple[int, str]] = []
    controls: list[int] = []
    for line_no, line in enumerate(lines, start=1):
        if CONTROLS_LABEL.search(line):
            controls.append(line_no)
        match = BOLD_RUN.match(line)
        if match is not None and _opens_a_block(lines, line_no):
            openers.append((line_no, match.group("label").strip()[:_LABEL_WIDTH]))

    verb_hits = [line_no for line_no, line in enumerate(lines, start=1) if VERBS.search(line)]

    print(f"document lines: {len(lines)}")
    print("\n=== CONTAINER CENSUS: bold-labelled block openers ===")
    print(f"  openers: {len(openers)}")
    print(f"  of which labelled `**Controls` (anchored or inline): {len(controls)}")
    anchored = [n for n in controls if BOLD_RUN.match(lines[n - 1])]
    print(f"    anchored at a line start: {len(anchored)}")
    print(f"    mid-paragraph (inline): {len(controls) - len(anchored)}")

    singular = [
        (line_no, label)
        for line_no, label in openers
        if label.startswith("Control") and not label.startswith("Controls")
    ]
    print(f"\n  labelled `**Control` -- SINGULAR, missed by a plural-noun key: {len(singular)}")
    for line_no, label in singular:
        print(f"    :{line_no:<5} {label}")

    covered = _covered(lines)
    inside = [(n, label) for n, label in openers if n in covered]
    outside = [(n, label) for n, label in openers if n not in covered]
    candidates = [
        (n, label)
        for n, label in outside
        if any(SRC_SYMBOL.search(lines[i - 1]) for i in _block_extent(lines, n) if i <= len(lines))
        and any(VERBS.search(lines[i - 1]) for i in _block_extent(lines, n) if i <= len(lines))
    ]
    print("\n  triage against the derivable keys (1a, 1b, 1d, 1e -- not 1c):")
    print(f"    inside a key   : {len(inside)}")
    print(f"    outside every key: {len(outside)}")
    print(f"    ...of which name a src symbol AND a control verb (candidates): {len(candidates)}")
    share = 100 * len(covered) / len(lines)
    print(f"    keyed lines: {len(covered)} of {len(lines)} ({share:.1f}%)")

    print(f"\n=== VERB SWEEP: {len(verb_hits)} hits over {VERBS.pattern.count('|') + 1} stems ===")
    with_symbol = [n for n in verb_hits if SRC_SYMBOL.search(lines[n - 1])]
    print(f"  hits whose line also names a src symbol: {len(with_symbol)}")
    print(f"  hits inside a keyed member: {len([n for n in verb_hits if n in covered])}")
    print(f"  hits outside every key: {len([n for n in verb_hits if n not in covered])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
