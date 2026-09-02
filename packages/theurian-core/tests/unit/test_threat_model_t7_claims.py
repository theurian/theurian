"""T-7's spawn bullet, held to the pinned spawn-site set it describes.

``docs/security/threat-model.md``'s T-7 entry describes the structural spawn arm
of ``tests/unit/test_network_call_sites.py`` and states how many sites that arm
permits. Until 2026-09-02 it said *two* and named two modules, while the pinned
set ``PROCESS_SPAWN_SITES`` had held three since ADR-0029's trailer source
landed -- so the entry understated the set for as long as the third member
existed. https://github.com/theurian/theurian/pull/504 corrected the prose: it
now names all three module paths and spells *three*. This module is what stops
the sentence drifting again. A corrected claim about what the codebase contains
is worth exactly what the sentence it replaced was worth, until something
recomputes it.

**Both sides are derived, and they are written independently.** The fact side is
the pinned set as the arm's own module defines it, so a fourth spawn site takes
this RED at the moment it lands -- which is the moment the record has to move.
The prose side is read out of the entry: the module paths the bullet names, and
the number word it spells. Neither side is parsed from the other, because a pin
that read its expected count out of the sentence it checks would agree with that
sentence by construction and measure nothing.

**What it holds.** (1) The T-7 spawn bullet names every module path in
``PROCESS_SPAWN_SITES``; (2) the number word it spells equals the size of that
set. It goes RED both ways round: a spawn site added to the product while the
entry still says *three*, and prose reworded back to understating the set.

**What it does not hold.** That the bullet *describes* any site correctly --
naming a path is not saying anything true about it -- nor anything about T-7's
other two arms, which make their own claims and have no pin here. And the set is
keyed on ``(module path, the watched name it reaches)``, so its size counts
*entries*, not distinct modules. The two coincide today, at three and three, and
the failure message reports both: a module that reached two watched names would
take this RED with the prose innocent, and the answer then is to say in the entry
which of the two figures it states, not to delete the pin.

Pure in the sense the other claim pins are: two files read as text, no database,
socket or temporary directory.
"""

from __future__ import annotations

import ast
import pathlib
import re
from typing import Final

import pytest
from write_lock_claims import REPO_ROOT, collapsed

pytestmark = pytest.mark.unit

#: The document this module reads.
THREAT_MODEL: Final = REPO_ROOT / "docs/security/threat-model.md"

#: The module that owns the spawn arm and its pinned set, and the constant's
#: name. This is the path T-7's own bullet cites, so a move breaks the entry's
#: citation and this pin together.
#:
#: **Read as source rather than imported, and both spellings of the import were
#: measured 2026-09-02 before this was written.** ``from
#: test_network_call_sites import PROCESS_SPAWN_SITES`` fails at runtime with
#: ``ModuleNotFoundError``: the ``conftest`` puts ``packages/theurian-core/tests``
#: on ``sys.path`` and not ``tests/unit``, and the suite runs under
#: ``--import-mode=importlib``, which adds no test directory of its own. The
#: namespace-package spelling ``from unit.test_network_call_sites import ...``
#: imports and runs, and takes ``uv run mypy`` down for the whole tree with
#: *Source file found twice under different module names:
#: "test_network_call_sites" and "unit.test_network_call_sites"* -- an error that
#: prevents any further checking, so it is not one a pin may leave behind.
#:
#: The cost of reading is that the constant has to stay a literal. A
#: ``PROCESS_SPAWN_SITES`` computed at import time takes this RED, which is the
#: safe direction: the pin says it can no longer read what it claims to check.
NETWORK_CALL_SITES: Final = (
    REPO_ROOT / "packages/theurian-core/tests/unit/test_network_call_sites.py"
)
SPAWN_SITES_CONSTANT: Final = "PROCESS_SPAWN_SITES"

#: T-7's heading, and every heading level that ends the entry. The marker carries
#: its ``\n`` and its trailing space so the slice anchors on a line start and on a
#: whole threat id: a bare ``#### T-7`` would also open on a ``T-7a`` heading, and
#: an unanchored ``T-7`` matches the id inside another entry's prose.
_ENTRY_HEADING: Final = "\n#### T-7 "
_HEADING_MARKERS: Final = ("\n## ", "\n### ", "\n#### ")

#: A top-level Markdown bullet. Column-anchored, so a bullet's own soft-wrapped
#: continuation lines -- indented two spaces -- stay with the bullet they belong
#: to rather than starting a new one.
_BULLET_START: Final = re.compile(r"(?m)^- ")

#: The bullet this module reads, keyed on the phrase that says what the bullet
#: *is*. Not keyed on a module path or on the arm's test name: a key naming one
#: member would stop matching exactly when someone rewrote the list, and the
#: bullet would drop out of the population rather than fail.
SPAWN_BULLET_ANCHOR: Final = "process spawns, structurally"

#: How the bullet states the count, as the phrase rather than as a bare number
#: word. Every other spelled number the bullet carries, measured at this commit:
#: "that third **one**"; the retracted "**two** sites" it quotes and the "first
#: **two**" beside it; the "**four** constants" of the trailer source's argument
#: vector; and a second **three**, in the sentence recording what the pinned set
#: has held. So a key matching any spelled number would read a retraction or an
#: aside as the claim -- and a key matching any *three* would be satisfied by the
#: record sentence while the sentence that permits the sites said something else.
#:
#: The caller asserts exactly one match, so a rewrite that made the key ambiguous
#: fails naming both rather than silently reading the first.
_SPELLED_COUNT: Final = re.compile(r"\bpermits ([a-z]+) sites\b")

#: The spelled numbers the bullet could carry, mapped to what they mean. Spelled
#: rather than digits because that is how the entry writes it, and a pin reading
#: digits would pass over the sentence it exists to hold. The range brackets the
#: live figure with room to move on both sides; a word outside it fails loudly,
#: since a bullet that started spelling the count some other way has stopped
#: being the sentence this pin reads.
_SPELLED_NUMBERS: Final = {
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
    "thirteen": 13,
    "fourteen": 14,
    "fifteen": 15,
    "sixteen": 16,
    "seventeen": 17,
    "eighteen": 18,
    "nineteen": 19,
    "twenty": 20,
}

#: The same mapping the other way round, so a RED can name the word the bullet
#: should now carry rather than leaving an editor to work it out from a count.
_WORD_FOR_COUNT: Final = {count: word for word, count in _SPELLED_NUMBERS.items()}


def _prose(text: str) -> str:
    """*text* normalised for a prose scan: no markup, no wraps, lower case.

    ``collapsed`` (from ``write_lock_claims``, the shared primitive every claim
    pin uses) lowercases and flattens the soft wraps. Backticks and asterisks go
    first, because the bullet writes its module paths in code spans and bolds the
    number word, and a key written the way the sentence reads would miss both.
    """
    return collapsed(text.replace("`", "").replace("*", ""))


def _spawn_bullet() -> str:
    """T-7's process-spawn bullet, normalised, located by its anchor phrase.

    Scoped to the entry before it is split into bullets, for the reason the other
    threat-model pins are scoped: other entries carry bullet lists of their own,
    and a document-wide split would put whichever paragraph fell between two
    lists inside the preceding bullet.
    """
    text = THREAT_MODEL.read_text(encoding="utf-8")
    assert text.count(_ENTRY_HEADING) == 1, (
        f"the threat model has {text.count(_ENTRY_HEADING)} lines starting "
        f"`{_ENTRY_HEADING.strip()}`, expected 1; with none of them this module "
        f"scans nothing, and with two it scans whichever came first"
    )

    rest = text.split(_ENTRY_HEADING, 1)[1]
    ends = [found for marker in _HEADING_MARKERS if (found := rest.find(marker)) >= 0]
    entry = rest[: min(ends)] if ends else rest

    bullets = [_prose(bullet) for bullet in _BULLET_START.split(entry)[1:]]
    carrying = [bullet for bullet in bullets if SPAWN_BULLET_ANCHOR in bullet]

    assert len(carrying) == 1, (
        f"`{SPAWN_BULLET_ANCHOR}` identifies {len(carrying)} of T-7's "
        f"{len(bullets)} bullets, expected 1. Zero means the bullet was reworded "
        f"past its own anchor and everything below would pass over nothing; more "
        f"than one means what is read below is text this module never chose"
    )
    return carrying[0]


def _literal_pair_set(source: pathlib.Path, name: str) -> tuple[tuple[str, str], ...]:
    """The literal set of string pairs assigned to *name* at *source*'s top level.

    Sorted, so the failure messages built from it do not depend on set iteration
    order. Both ``name: Final = {...}`` and a bare ``name = {...}`` are accepted,
    so the pin fails on the claim rather than on an annotation style someone
    changed.
    """
    tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
    assigned: list[ast.expr] = []
    for node in tree.body:
        if isinstance(node, ast.AnnAssign):
            if isinstance(node.target, ast.Name) and node.target.id == name and node.value:
                assigned.append(node.value)
        elif isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == name for target in node.targets
        ):
            assigned.append(node.value)

    assert len(assigned) == 1, (
        f"`{name}` is assigned {len(assigned)} times at the top level of "
        f"{source.name}, expected once. Zero means it was renamed, moved or is "
        f"now computed rather than written, and this module can no longer read "
        f"the constant it says it checks"
    )

    literal = ast.literal_eval(assigned[0])
    assert isinstance(literal, set | frozenset), (
        f"{source.name}'s `{name}` is no longer a literal set: {type(literal).__name__}"
    )

    pairs: list[tuple[str, str]] = []
    for member in literal:
        assert isinstance(member, tuple) and len(member) == 2, (
            f"{source.name}'s `{name}` holds `{member!r}`, which is not a "
            f"(module path, watched name) pair, so the paths compared below are "
            f"about something else"
        )
        path, watched = member
        assert isinstance(path, str) and isinstance(watched, str), (
            f"{source.name}'s `{name}` holds a pair that is not two strings: {member!r}"
        )
        pairs.append((path, watched))

    return tuple(sorted(pairs))


def test_the_t7_spawn_bullet_names_every_pinned_spawn_site_and_spells_how_many() -> None:
    """RED means T-7 and the pinned spawn-site set disagree -- either side moved.

    T-7 tells a reader deciding whether to trust the SSRF absence argument which
    places in the shipped package may start another program, and how many there
    are. That is a fact about today's code written into a durable security
    record, and it was wrong once already: from ADR-0029's trailer source landing
    until #504 the entry said *two* and named two modules, while
    ``PROCESS_SPAWN_SITES`` held three throughout. A reader who took the entry at
    its word would have read the third as a spawn site that arrived unreviewed.

    So the assertion is an equality between two independently written things, the
    ``test_setup_claims.py`` shape: what the bullet names and spells, and the set
    the arm enforces. It fails whichever one moves -- a fourth site landing is not
    a defect in the product, it is the moment this record has to be rewritten, and
    the failure message says which word it should then carry.

    The premises come first. A constant that had gone empty would make every
    membership check vacuous, and a bullet that no longer spells a number at all
    would leave the count arm with nothing to compare, so each fails naming itself
    rather than arriving at the comparison as a bare mismatch.
    """
    bullet = _spawn_bullet()
    sites = _literal_pair_set(NETWORK_CALL_SITES, SPAWN_SITES_CONSTANT)

    spelled = _SPELLED_COUNT.findall(bullet)
    assert len(spelled) == 1, (
        f"T-7's spawn bullet no longer states how many sites it permits as one "
        f"spelled number matching `{_SPELLED_COUNT.pattern}`, so this pin has "
        f"nothing to hold against the pinned set: {spelled}"
    )
    assert spelled[0] in _SPELLED_NUMBERS, (
        f"T-7's spawn bullet spells its site count as `{spelled[0]}`, which is not "
        f"a number this pin can read; the bullet has to say how many, or the count "
        f"is back to being a claim nobody can check"
    )
    assert sites, (
        f"`{SPAWN_SITES_CONSTANT}` is empty, so every membership check below would "
        f"pass over nothing and the count would be about a set this test never read"
    )

    modules = sorted({path for path, _watched in sites})
    unnamed = [path for path in modules if path.lower() not in bullet]

    assert not unnamed, (
        f"T-7's spawn bullet does not name {unnamed}, which `{SPAWN_SITES_CONSTANT}` "
        f"pins as a place in the shipped package that may start another program. "
        f"The entry enumerates the permitted sites and a reader takes that list as "
        f"complete, so an unnamed member reads as a spawn site nobody reviewed: "
        f"{bullet[:400]}"
    )
    should_carry = _WORD_FOR_COUNT.get(len(sites), "no word this pin can spell")
    assert _SPELLED_NUMBERS[spelled[0]] == len(sites), (
        f"T-7's spawn bullet permits `{spelled[0]}` sites; `{SPAWN_SITES_CONSTANT}` "
        f"holds {len(sites)} entries over {len(modules)} modules ({modules}). "
        f"Whichever side moved, the record and the pin have to be brought back "
        f"into step, and the word the bullet should carry is `{should_carry}`. If "
        f"the two figures above differ, the set has gained a second watched name "
        f"on a module it already listed, and the entry has to say which of the two "
        f"it counts"
    )
