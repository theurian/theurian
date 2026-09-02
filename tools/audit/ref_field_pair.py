"""Class 5: does every `unresolvedRefCount` site state its narrowed contract? (#246).

``unresolvedRefCount`` and ``refWalkTruncated`` are published parser metadata,
and what makes them safe to read is a **narrowing**: the count is of *distinct
``$ref`` strings, and nothing else*. Not occurrences. Not distinct targets. Not
the other resolution keywords a specification can carry -- ``$dynamicRef``,
``operationRef`` and the rest are outside the walk entirely, so a document can
hold a remote reference this count does not see.

A reader who meets either field without that sentence in reach reads it as
"external references, counted", which is the false published claim #246 records.
So the population is every place the two names are written, and the discharge is
that the narrowing is stated **in the block, or in the section the block sits
in** -- a reader following a heading finds it either way, and requiring the
sentence to be repeated six times would be a rule nobody keeps.

The engineering half of #246 -- actually resolving the other keywords -- is
deliberately not this audit's business. This checks the *claim*, which is the
half that is false now.

**What section granularity reaches, and what it lets past -- stated, because it
was measured rather than reasoned about.** The discharge rule is "the narrowing
is in the block, or in the section the block sits in", so it catches a field
named in a section that does not state the narrowing and it does **not** catch a
false reading planted *inside* a section that does. Measured at ``ef345c9``:
inserting

    **``unresolvedRefCount`` is a total of the external references a document
    holds, and a caller may read it as one.**

into the threat model's T-7 -- verbatim the reading #246 records as false --
takes the block count from four to five and leaves this audit at **exit 0**,
because T-7's own narrowing sentence discharges the new block along with the
others. The same sentence under a new heading is exit 1, which is the control
that already runs.

That is the reach a reader gets, and it is a deliberate trade rather than an
oversight: requiring the narrowing per *block* would demand the sentence be
repeated at every mention, which is a rule nobody keeps and which this module's
third positive control exists to say. Round one recorded the gap as MEDIUM with
the mechanism change deferred; what is not deferred is a reader believing this
audit catches a contradiction in the section it was written for. It does not.

Run it::

    uv run --frozen python tools/audit/ref_field_pair.py
    uv run --frozen python tools/audit/ref_field_pair.py --positive-control
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from claim_surfaces import governed_paths, print_control_tally, repo_root

#: Where a published-field claim is governed. Source is excluded on purpose: the
#: parser's own comments are the implementation's reasoning, and #246's face is
#: the *published* claim.
GOVERNED_ROOTS: Final[tuple[str, ...]] = ("docs/", "schemas/", "plugins/")

#: The field pair, as the key that finds every site.
_FIELD: Final = re.compile(r"unresolvedRefCount|refWalkTruncated")

#: The narrowing, matched after whitespace collapsing because it is written across
#: a wrap in the one place it currently appears.
_NARROWED_CONTRACT: Final = re.compile(
    r"counts?\s+distinct\s+.?\$ref.?\s+strings,\s+and\s+nothing\s+else", re.IGNORECASE
)

#: A section boundary. Any ATX heading, because a reader following a heading of
#: any depth has left the region the narrowing was stated in.
_HEADING: Final = re.compile(r"^#{1,6}\s")

_MAX_EXCERPT: Final = 120


@dataclass(frozen=True, slots=True)
class Site:
    path: str
    line: int
    heading: str
    text: str
    contract_in_block: bool
    contract_in_section: bool

    @property
    def discharged(self) -> bool:
        return self.contract_in_block or self.contract_in_section


def _sections(text: str) -> list[tuple[str, int, list[str]]]:
    """``(heading, first line number, lines)`` for each ATX-delimited section."""
    found: list[tuple[str, int, list[str]]] = []
    heading = "(document preamble)"
    start = 1
    body: list[str] = []
    for number, line in enumerate(text.splitlines(), start=1):
        if _HEADING.match(line):
            found.append((heading, start, body))
            heading, start, body = line.strip(), number, []
            continue
        body.append(line)
    found.append((heading, start, body))
    return found


def _blocks(body: list[str], start: int) -> list[tuple[int, str]]:
    """``(first line number, collapsed text)`` for each blank-line-delimited block.

    Collapsing before matching is what makes the counts wrap-aware. A
    line-oriented pass over the same region returns **six** hits where this
    returns **four blocks**, because two sentences are typed across a wrap; the
    two numbers are different measurements of the same prose and are printed
    separately so neither can be read as the other.
    """
    found: list[tuple[int, str]] = []
    block: list[str] = []
    block_start = start
    for offset, line in enumerate(body):
        if line.strip():
            if not block:
                block_start = start + offset + 1
            block.append(line)
            continue
        if block:
            found.append((block_start, " ".join(" ".join(block).split())))
            block = []
    if block:
        found.append((block_start, " ".join(" ".join(block).split())))
    return found


def sites_in_text(path: str, text: str) -> list[Site]:
    """Every place either field name is written in one document."""
    if not _FIELD.search(text):
        return []
    found: list[Site] = []
    for heading, start, body in _sections(text):
        in_section = bool(_NARROWED_CONTRACT.search(" ".join(" ".join(body).split())))
        for line, block in _blocks(body, start):
            if not _FIELD.search(block):
                continue
            found.append(
                Site(
                    path=path,
                    line=line,
                    heading=heading,
                    text=block,
                    contract_in_block=bool(_NARROWED_CONTRACT.search(block)),
                    contract_in_section=in_section,
                )
            )
    return found


def sites(root: Path) -> list[Site]:
    """Every place either field name is written, in path order."""
    return [
        site
        for path in governed_paths(root)
        if any(path.startswith(prefix) for prefix in GOVERNED_ROOTS)
        for site in sites_in_text(
            path, (root / path).read_text(encoding="utf-8", errors="surrogateescape")
        )
    ]


#: The narrowing as a shipped document writes it, reused by the controls so a
#: change to :data:`_NARROWED_CONTRACT` that stops matching the real sentence is
#: caught here rather than by a silent zero.
_CONTRACT_SENTENCE: Final = (
    "**`unresolvedRefCount` counts distinct `$ref` strings, and nothing else.**"
)

#: Whole synthetic documents run instead of the tree under
#: ``--positive-control``, as ``(what it demonstrates, document, how many sites,
#: how many of them discharge)``.
#:
#: The third row is the one that matters: a field named in a *different* section
#: from the narrowing must NOT discharge, because that is the shape a new mention
#: takes and it is why this audit is section-scoped rather than file-scoped. The
#: fourth is the wrap: the same sentence typed across two source lines has to be
#: found, or every count here is a line-oriented undercount.
POSITIVE_CONTROLS: Final[tuple[tuple[str, str, int, int], ...]] = (
    (
        "the narrowing stated in the block itself",
        f"#### T-7\n\n{_CONTRACT_SENTENCE} Not occurrences.\n",
        1,
        1,
    ),
    (
        "a field mentioned elsewhere in a section that states the narrowing",
        f"#### T-7\n\n{_CONTRACT_SENTENCE}\n\nWith `refWalkTruncated` false it is exact.\n",
        2,
        2,
    ),
    (
        "a field mentioned in a section that does not state it",
        f"#### T-7\n\n{_CONTRACT_SENTENCE}\n\n#### T-9\n\nIngest publishes"
        " `unresolvedRefCount` per document.\n",
        2,
        1,
    ),
    (
        "the narrowing typed across a wrap",
        "#### T-7\n\n**`unresolvedRefCount` counts distinct `$ref`\nstrings, and nothing"
        " else.** Not occurrences.\n",
        1,
        1,
    ),
    (
        "a document naming neither field, which `main` reads as a dead key rather than "
        "as a clean tree",
        "#### T-7\n\nIngest records what it could not resolve, and fetches nothing.\n",
        0,
        0,
    ),
    (
        "the reach this audit does NOT have: a false reading planted inside a section "
        "that states the narrowing, which discharges",
        f"#### T-7\n\n{_CONTRACT_SENTENCE}\n\n**`unresolvedRefCount` is a total of the"
        " external references a document holds.**\n",
        2,
        2,
    ),
)


def _run_positive_controls() -> int:
    failures = 0
    ran = 0
    print("=== POSITIVE CONTROLS ===")
    for label, document, expected_sites, expected_discharged in POSITIVE_CONTROLS:
        ran += 1
        found = sites_in_text("control.md", document)
        discharged = sum(1 for site in found if site.discharged)
        ok = len(found) == expected_sites and discharged == expected_discharged
        status = "OK  " if ok else "FAIL"
        failures += status == "FAIL"
        print(
            f"  {status} {label}: sites={len(found)} (expected {expected_sites}), "
            f"discharged={discharged} (expected {expected_discharged})"
        )
    print_control_tally("POSITIVE_CONTROLS", ran, failures)
    return 1 if failures else 0


def _line_hits(root: Path) -> int:
    """The line-oriented count, printed beside the block count and never instead.

    A reader who has run `git grep` sees this number, and a report that showed
    only the block count would read as a disagreement with their own terminal.
    """
    total = 0
    for path in governed_paths(root):
        if not any(path.startswith(prefix) for prefix in GOVERNED_ROOTS):
            continue
        text = (root / path).read_text(encoding="utf-8", errors="surrogateescape")
        total += sum(1 for line in text.splitlines() if _FIELD.search(line))
    return total


def main(argv: list[str]) -> int:
    if "--positive-control" in argv:
        return _run_positive_controls()

    root = repo_root()
    found = sites(root)

    print("=== #246 FIELD-PAIR SITES ===")
    print(f"  blocks naming either field: {len(found)}")
    print(f"  files: {len({site.path for site in found})}")
    print(f"  source lines naming either field: {_line_hits(root)}")
    print("  (the two numbers differ because sentences wrap; neither is the other)")
    for site in found:
        how = (
            "block" if site.contract_in_block else "section" if site.contract_in_section else "NONE"
        )
        print(f"  {how:<8} {site.path}:{site.line}  under {site.heading}")
        print(f"           {site.text[:_MAX_EXCERPT]}")

    undischarged = [site for site in found if not site.discharged]
    if undischarged:
        print("\nUNDISCHARGED -- a published field named where its narrowing is not in reach:")
        for site in undischarged:
            print(f"  {site.path}:{site.line}  under {site.heading}")
    if not found:
        print("\nNO SITES -- the key matched nothing, which a clean tree and a dead key")
        print("  both look like. Run --positive-control before reading this as a zero.")
        return 1
    return 1 if undischarged else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
