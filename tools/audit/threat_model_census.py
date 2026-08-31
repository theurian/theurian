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

_LABEL_WIDTH = 70
#: ``argv`` is the program plus exactly one Markdown path.
_EXPECTED_ARGV = 2
#: The first line has no predecessor to inspect.
_FIRST_LINE = 2


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

    print(f"\n=== VERB SWEEP: {len(verb_hits)} hits over {VERBS.pattern.count('|') + 1} stems ===")
    with_symbol = [n for n in verb_hits if SRC_SYMBOL.search(lines[n - 1])]
    print(f"  hits whose line also names a src symbol: {len(with_symbol)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
