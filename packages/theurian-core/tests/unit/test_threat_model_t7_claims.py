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

**And (3), for one site, what the records say its vector *is*.** Naming a path
is not saying anything true about it, and the one site T-7 describes as handed
an argument a document supplies is the one where that gap cost something: the
entry described the retired two-call ``rev-parse``-then-``diff-tree`` shape,
credited ``rev-parse --verify`` with refusing a fabricated sha, and told the
reader that ``diff-tree`` received the id ``rev-parse`` printed rather than the
caller's string -- three claims about a module that had spawned one
``diff-tree`` since C4c.

**Two records carry that description, and both drifted, so both are held by the
same arms** -- the entry, and ``PROCESS_SPAWN_SITES``' own note, which is where
a reader of the *suite* meets the same claim. They are one claim written twice
and they were written when the adapter asked two questions; a class closed at
one of its two surfaces is a class that comes back through the other. Each is
held against the adapter's own argument vector, read off its syntax tree, in
both directions: every constant token the adapter spawns is named, and no token
the adapter does not spawn is.

**What it still does not hold.** Whether the *sentences* around those tokens are
true -- a record can name the right flags and describe them wrongly, and that is
a reading. Nor anything about T-7's other two arms, which make their own claims
and have no pin here. And the set is keyed on ``(module path, the watched name
it reaches)``, so its size counts *entries*, not distinct modules. The two
coincide today, and the failure message reports both: a module that reached two
watched names would take this RED with the prose innocent, and the answer then
is to say in the entry which of the two figures it states, not to delete the pin.

Pure in the sense the other claim pins are: three files read as text, no
database, socket or temporary directory.
"""

from __future__ import annotations

import ast
import pathlib
import re
from collections.abc import Callable
from typing import Final

import pytest
from threat_model_claims import SPELLED_NUMBERS, WORD_FOR_COUNT, entry, prose
from write_lock_claims import REPO_ROOT

pytestmark = pytest.mark.unit

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

#: The entry this module reads, sliced by ``threat_model_claims.entry`` -- which
#: is where the anchoring rules and the reason for them live, because every
#: entry's pin needs them.
_THREAT_ID: Final = "T-7"

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


def _spawn_bullet_raw() -> str:
    """T-7's process-spawn bullet, **as written**, located by its anchor phrase.

    Scoped to the entry before it is split into bullets, for the reason the other
    threat-model pins are scoped: other entries carry bullet lists of their own,
    and a document-wide split would put whichever paragraph fell between two
    lists inside the preceding bullet.

    Raw rather than normalised, because two readers want different things from
    it: the count and module-path arms want :func:`prose`'s flattened lower case,
    and the argv arms below want the blank lines that separate one site's
    paragraph from the next and the backticks that mark a token as a token.
    """
    bullets = _BULLET_START.split(entry(_THREAT_ID))[1:]
    carrying = [bullet for bullet in bullets if SPAWN_BULLET_ANCHOR in prose(bullet)]

    assert len(carrying) == 1, (
        f"`{SPAWN_BULLET_ANCHOR}` identifies {len(carrying)} of T-7's "
        f"{len(bullets)} bullets, expected 1. Zero means the bullet was reworded "
        f"past its own anchor and everything below would pass over nothing; more "
        f"than one means what is read below is text this module never chose"
    )
    return carrying[0]


def _spawn_bullet() -> str:
    """T-7's process-spawn bullet, normalised for a prose scan."""
    return prose(_spawn_bullet_raw())


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
    assert spelled[0] in SPELLED_NUMBERS, (
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
    should_carry = WORD_FOR_COUNT.get(len(sites), "no word this pin can spell")
    assert SPELLED_NUMBERS[spelled[0]] == len(sites), (
        f"T-7's spawn bullet permits `{spelled[0]}` sites; `{SPAWN_SITES_CONSTANT}` "
        f"holds {len(sites)} entries over {len(modules)} modules ({modules}). "
        f"Whichever side moved, the record and the pin have to be brought back "
        f"into step, and the word the bullet should carry is `{should_carry}`. If "
        f"the two figures above differ, the set has gained a second watched name "
        f"on a module it already listed, and the entry has to say which of the two "
        f"it counts"
    )


# -- the sixth site's argv, held against the adapter that spawns it ------------

#: The one recorded spawn site T-7 describes as *handed an argument a document
#: supplies*, and the adapter that owns it. Named here and asserted to be a
#: member of the pinned set, so a module that moves reddens with a message
#: rather than by slicing an empty paragraph.
FIX_COMMIT_SITE: Final = "infrastructure/git/fix_commit_check.py"
FIX_COMMIT_ADAPTER: Final = REPO_ROOT / "packages/theurian-core/src/theurian" / FIX_COMMIT_SITE

#: A code span, in either markup. The unit the arms below read, because a token
#: in these records is written as one and a word in the surrounding prose is
#: not: an unquoted "options" must not count as ``--end-of-options``. The
#: threat model writes single backticks and the Python comment writes RST's
#: double, and the inner pair of a double matches this as written.
#:
#: Applied to *folded* text (:func:`_folded`), so a span an editor's wrap split
#: is still one span -- the T-24 census measured a whole branch of its key
#: reading zero for exactly that reason.
_CODE_SPAN: Final = re.compile(r"`([^`\n]+)`")

#: What a git *token* looks like inside a code span: an option, or a hyphenated
#: bare word, which is the shape of every git subcommand this tree spawns
#: (``rev-parse``, ``diff-tree``, ``cat-file``, ``hash-object``).
#:
#: Deliberately narrow on both sides. It must not match ordinary prose words
#: (``git``, ``theurian``) or the paragraph's other code spans -- ``fixCommit``
#: and ``GIT_TIMEOUT_SECONDS`` carry capitals, ``file_path`` an underscore,
#: ``^{commit}`` and ``:(exclude)…`` punctuation no token has, and a test path
#: a ``/``. Each of those is in the live paragraph today, so the narrowness is
#: measured rather than assumed.
_GIT_TOKEN: Final = re.compile(
    r"--?[a-z][a-z0-9]*(?:-[a-z0-9]+)*(?:=[a-z0-9-]+)?|[a-z][a-z0-9]*(?:-[a-z0-9]+)+"
)


#: The comment marker a line of the pinned set's own note carries.
_NOTE_MARKER: Final = re.compile(r"(?m)^#:?[ \t]?")


def _folded(text: str) -> str:
    """*text* with its comment markers dropped and its soft wraps folded to one space."""
    return " ".join(_NOTE_MARKER.sub(" ", text).split())


def _blocks_naming_the_site(text: str, separator: str) -> list[str]:
    return [block for block in text.split(separator) if FIX_COMMIT_SITE in block]


def _threat_model_paragraph() -> str:
    """The paragraph of T-7's spawn bullet that describes the fix-commit adapter.

    Keyed on the **module path**, which is the one thing in the paragraph that
    is also a member of :data:`PROCESS_SPAWN_SITES <SPAWN_SITES_CONSTANT>`: an
    ordinal ("the sixth") stops being true when a seventh site lands, and a
    phrase key stops matching the moment somebody rewrites the sentence it was
    taken from -- and either failure drops the paragraph out of the population
    rather than failing.
    """
    return _one_block(_blocks_naming_the_site(_spawn_bullet_raw(), "\n\n"), "T-7's spawn bullet")


def _pinned_set_note() -> str:
    """The item of ``PROCESS_SPAWN_SITES``' own note that describes the same adapter.

    The second record of this vector, and the one a reader of the *test* suite
    meets. It drifted with the threat model and for the same reason -- both were
    written when the adapter asked two questions -- so it is held by the same
    arms rather than left to be noticed: a class closed at one of its two
    surfaces is a class that comes back through the other.

    The note is the contiguous ``#:`` block above the constant; its items start
    at a ``- `` after the marker, which is how the block separates one site from
    the next.
    """
    source = NETWORK_CALL_SITES.read_text(encoding="utf-8")
    declaration = source.index(f"{SPAWN_SITES_CONSTANT} = ")
    lines = source[:declaration].splitlines()
    note: list[str] = []
    for line in reversed(lines):
        if not line.startswith("#"):
            break
        note.append(line)

    assert note, (
        f"`{SPAWN_SITES_CONSTANT}` in {NETWORK_CALL_SITES.name} carries no comment block "
        f"above it, so the arms below read nothing and report a clean record. The note is "
        f"where each permitted site's vector is described"
    )
    return _one_block(
        _blocks_naming_the_site("\n".join(reversed(note)), "\n#: - "),
        f"{NETWORK_CALL_SITES.name}'s `{SPAWN_SITES_CONSTANT}` note",
    )


def _one_block(blocks: list[str], where: str) -> str:
    assert len(blocks) == 1, (
        f"`{FIX_COMMIT_SITE}` identifies {len(blocks)} blocks of {where}, expected 1. "
        f"Zero means that record stopped naming the adapter that receives an MCP "
        f"caller's bytes -- which the arms below would then report as a clean record -- "
        f"and more than one means they read text this module never chose"
    )
    return _folded(blocks[0])


#: The two records that describe this adapter's ``git`` vector, each keyed by the
#: module path it names. Both are held by the same two arms, because they are one
#: claim written twice and they drifted together.
_RECORD_SURFACES: Final[dict[str, Callable[[], str]]] = {
    "threat-model T-7": _threat_model_paragraph,
    f"{SPAWN_SITES_CONSTANT}'s note": _pinned_set_note,
}


def _module_level_value(tree: ast.Module, name: str) -> ast.expr:
    """The expression assigned to *name* at *tree*'s top level."""
    assigned = [
        node.value
        for node in tree.body
        if (isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name))
        and node.target.id == name
        and node.value is not None
    ] + [
        node.value
        for node in tree.body
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == name for target in node.targets)
    ]

    assert len(assigned) == 1, (
        f"`{name}` is assigned {len(assigned)} times at the top level of "
        f"{FIX_COMMIT_ADAPTER.name}, expected once"
    )
    return assigned[0]


def _spawned_vector() -> tuple[str, ...]:
    """Every constant token the adapter hands ``git``, read off its own source.

    The fact side of the two arms below, and it is read from the **adapter**
    rather than from the entry it checks -- a pin that took its expected vector
    out of the sentence it holds would agree with that sentence by construction.

    Located by the spawn itself rather than by any token in it: the single call
    to the module's ``_run`` helper, whose argument list is the vector. A
    factoring that hoists that list to a module constant is followed by name, so
    the derivation survives the refactor rather than silently reading nothing.
    The two non-constant tokens -- the revision and the stored path -- are not
    here by construction, which is the point: a record may not name a caller's
    value as though the adapter fixed it.
    """
    tree = ast.parse(
        FIX_COMMIT_ADAPTER.read_text(encoding="utf-8"), filename=str(FIX_COMMIT_ADAPTER)
    )
    spawns = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "_run"
        and node.args
    ]

    assert len(spawns) == 1, (
        f"{FIX_COMMIT_ADAPTER.name} reaches its spawn helper from {len(spawns)} place(s) "
        f"with arguments, expected 1. The adapter asks git one question "
        f"(ADR-0033 decision 5); until that is true again there is no single vector for "
        f"the entry to be held against, and `test_fix_commit_check_adapter.py`'s own "
        f"one-spawn arm is where that is pinned"
    )
    argument = spawns[0].args[0]
    if isinstance(argument, ast.Name):
        argument = _module_level_value(tree, argument.id)

    assert isinstance(argument, ast.List), (
        f"the vector handed to `_run` is a {type(argument).__name__} rather than a list "
        f"literal, so this module can no longer read the tokens it claims to check"
    )
    return tuple(
        element.value
        for element in argument.elts
        if isinstance(element, ast.Constant) and isinstance(element.value, str)
    )


def _named_tokens(paragraph: str) -> frozenset[str]:
    """Every git token the paragraph names inside a code span."""
    return frozenset(
        word
        for span in _CODE_SPAN.findall(paragraph)
        for word in span.split()
        if _GIT_TOKEN.fullmatch(word)
    )


@pytest.mark.parametrize("record", sorted(_RECORD_SURFACES), ids=sorted(_RECORD_SURFACES))
def test_a_record_of_the_fix_commit_site_names_every_token_the_adapter_spawns(
    record: str,
) -> None:
    """RED means a record under-describes the vector it is vouching for.

    These are where a reader deciding whether to trust the *one* spawn site
    handed a caller's bytes finds out what that site runs. A token a record
    omits is a token nobody reviewed: ``--diff-merges=first-parent`` decides
    whether a merge commit can satisfy the promotion gate at all and ``--root``
    whether a repository's first commit can, and each arrived in a change whose
    whole argument was about something else.

    The expected set is read off the adapter's own syntax tree every run, so a
    token added to the vector is RED here at the moment it lands -- which is the
    moment both records have to move -- rather than at the next audit.

    The premise comes first: a vector that read empty would make the comparison
    pass over nothing, which is what a derivation that stopped finding the spawn
    also looks like.
    """
    vector = _spawned_vector()
    named = {
        word for span in _CODE_SPAN.findall(_RECORD_SURFACES[record]()) for word in span.split()
    }

    assert len(vector) >= 5, (
        f"the adapter's spawn vector read as {list(vector)}; it carried eight constant "
        f"tokens at 410d4437 and nine once `--diff-merges=first-parent` joined it, so a "
        f"set this small means the derivation stopped reading the call rather than that "
        f"the vector shrank"
    )
    missing = [token for token in vector if token not in named]

    assert not missing, (
        f"{record} does not name {missing}, which {FIX_COMMIT_SITE} hands to `git` on "
        f"every verification.\n\n"
        f"The whole vector is {list(vector)}. A reader takes that record as the "
        f"description of what this site runs, so an unnamed token is one nobody "
        f"reviewed -- and each of them decides a verdict: `--root` whether a root "
        f"commit can be a fix, `--diff-merges=first-parent` whether a merge can, "
        f"`--literal-pathspecs` whether a stored pathspec can verify a foreign commit."
    )


@pytest.mark.parametrize("record", sorted(_RECORD_SURFACES), ids=sorted(_RECORD_SURFACES))
def test_a_record_of_the_fix_commit_site_names_no_token_the_adapter_does_not_spawn(
    record: str,
) -> None:
    """RED means a record describes a control the adapter does not have.

    The direction that actually hurt, and it hurt on both surfaces at once. Until
    the two calls collapsed into one, each said the adapter *runs* ``git
    rev-parse`` and then ``git diff-tree``; the threat model went further and
    credited ``rev-parse --verify`` with refusing a fabricated forty hex digits,
    and told the reader that what ``diff-tree`` received was the id ``rev-parse``
    printed rather than the caller's string. None of that was true of the shipped
    module any more, and a security record describing a control that is gone is
    worse than one describing none: it is read as a closure.

    The completeness arm above cannot catch it -- fewer tokens in the vector is
    simply fewer things to find -- so the honesty direction is its own assertion.
    A word inside a token the vector really carries is allowed, because
    ``first-parent`` is how a sentence refers to ``--diff-merges=first-parent``
    and refusing that would be pinning the prose rather than the claim.
    """
    vector = _spawned_vector()
    named = _named_tokens(_RECORD_SURFACES[record]())

    assert named, (
        f"{record} names no git token at all in a code span, so this arm passes over "
        f"nothing -- either it stopped describing the vector or `_GIT_TOKEN` has stopped "
        f"reading the spellings it uses"
    )
    stray = sorted(
        token
        for token in named
        if token not in vector and not any(token in carried for carried in vector)
    )

    assert not stray, (
        f"{record} names {stray}, and {FIX_COMMIT_SITE} hands `git` {list(vector)}.\n\n"
        f"A token in that record reads as something the adapter runs or forecloses. "
        f"`rev-parse` and `--verify` are the retired two-call shape: the questions "
        f"collapsed into one `diff-tree` (ADR-0033 decision 5), so any control the "
        f"record credits to `rev-parse` -- refusing a fabricated sha, re-matching the "
        f"printed id as hex before spending it -- is a control this product does not "
        f"have. Rewrite the sentence around the vector above, and record what refuses a "
        f"value now: the entry funnel (`fix_commit_grammar`), then `diff-tree`'s own "
        f"non-zero exit."
    )


def test_the_fix_commit_site_this_module_slices_on_is_a_pinned_spawn_site() -> None:
    """The premise both argv arms rest on: the module path is the one the set records.

    :data:`FIX_COMMIT_SITE` is a string, and a string that no longer names a
    member of ``PROCESS_SPAWN_SITES`` slices a paragraph out of a record that
    has moved on -- or slices nothing, which the paragraph arm reports, but with
    a message about the *entry* when the cause is a module rename.
    """
    modules = {
        path for path, _watched in _literal_pair_set(NETWORK_CALL_SITES, SPAWN_SITES_CONSTANT)
    }

    assert FIX_COMMIT_SITE in modules, (
        f"`{FIX_COMMIT_SITE}` is not one of the pinned spawn sites ({sorted(modules)}). "
        f"The adapter moved; move this constant, the entry's own citation and "
        f"`CALLER_REACHABLE_SPAWN_SITES` in the same change"
    )
    assert FIX_COMMIT_ADAPTER.is_file(), f"{FIX_COMMIT_ADAPTER} is not in the tree"
