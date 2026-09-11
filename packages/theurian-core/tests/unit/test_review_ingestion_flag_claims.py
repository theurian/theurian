"""``reviewIngestion``'s published meaning, held across the records that state it.

The flag has meant three different things and only the last is published, which
is why every record that mentions it spends more words on what it does *not* say
than on what it does. Two claims follow from that, and they fail in different
ways.

**One sentence, four sites.** ``mcp/tools.py``'s comment states, in as many
words, that the same two "it never meant" sentences are in
``schemas/mcp/system-capabilities-response.schema.json``,
``docs/protocol/mcp-tools.md`` and the flag's own pin in
``tests/integration/test_mcp_tools.py``, "spelled one way across all four
deliberately, because two of them carried the ``false``-reading form and a reader
meeting both would have to work out whether the difference meant anything". That
is a claim about four files, made in one of them, that nothing was checking --
and the failure it guards against is the one it was written to fix, so it
recurs by exactly the same route: somebody rewords one site.
:func:`test_the_four_sites_spell_the_never_meant_sentences_one_way` compares the
four extracted sets, so a site moving alone reddens whichever way it moved.

**Seven records narrate the value, both ways round.** ``review/__init__.py`` and
``infrastructure/github/__init__.py`` were re-tensed when slice 3 flipped the
flag -- unpinned, which the flipping commit recorded as owed rather than leaving
silent. The third is the flag's own pin: ``test_mcp_tools.py`` asserted
``reviewIngestion is True`` while one of its docstrings still explained what "the
``false``" said and a sibling assertion message had the flag "stays false" --
both left behind by the flip, in the file whose job is to hold it.

The fourth and fifth are documents, and they are here because the enumeration
above missed them for a whole round. ADR-0026's **Compliance** section listed
``reviewIngestion: false`` among the flags reading false, and ``docs/roadmap.md``
opened by saying no review ingestion adapter exists -- both false since slice 1,
both outside the source-tree population the earlier sweep keyed on. ADR-0026's
Compliance section is maintained rather than frozen: the #504 flip of
``reviewFindings`` edited this same paragraph, which is the precedent this row
follows (PR #504). An ADR's *Context* and *Decision* sections are the opposite -- they
record what was true when the decision was taken, and ADR-0030's ``false``
sentences are correct there and are deliberately not in this population.

The sixth and seventh are the two records a person meets *before* any of the
above, and they were outside every sweep this module has run: ``README.md``'s
"AI proposes, humans approve" row and ``packages/theurian-core/CHANGELOG.md``'s
`gh`-adapter entry. Both were re-tensed by the flipping commit -- the README row
from "reports ``reviewIngestion: false`` while no *MCP* tool exposes either half"
and the adapter entry from "so ``system.capabilities`` still reports
``reviewIngestion: false``" -- and both then stood unpinned, in the two documents
with the widest readership in the repository. The changelog row is **not** scoped
to ``[Unreleased]``, for ``test_review_ingest_changelog_claims.py``'s reason: a
release cut moves that entry into a dated section without changing a word.

A prose pin alone would keep the ``true``-era wording against a build that had
flipped back; a value pin alone would keep the value against records still
describing the ``false`` era. So the fact side is the value read out of
``mcp/tools.py``'s own capability dict, and it *selects* which wording each
record must carry and which it must not. Flip the flag and all seven are RED
until they are re-tensed; re-tense one backwards and it is RED until the flag
moves.

**Narrates, not mentions.** Many records name this flag; these seven tell a
reader what its value *is* and reason from it, which is what makes them false
rather than merely dated when it moves. The population is enumerated rather than
derived -- an eighth narration would not redden this -- and that is the honest
bound, with the answer being to add it here in the change that writes it. Three
rounds have now added a row after the fact, which is the cost of an enumeration
and is recorded rather than argued away: a derived population, keyed on the
narrating spellings across the whole corpus, is the shape that would not need
the fourth.

**Each site is read the way its own reader reads it.** A Python comment through
``tokenize``, because comments are not in a syntax tree; a test's assertion
message through the AST, because implicit string concatenation is one string to
a reader and four literals to a text search; a JSON schema through ``json``,
because a description is an escaped string in the file and plain prose in a
client's tooling; Markdown as prose with its block quoting removed, because
ADR-0026's sentence lives inside a ``>`` quote and flattening the wraps alone
leaves a ``>`` in the middle of it. A single grep over four files would have
matched three of them and silently missed the fourth.

**Why this is not a row in ``test_adr_0030_claims.py``'s ``CLAIM_SURFACES``.**
That module's three claims each pair a Markdown sentence with an **AST scan over
``src/``** -- what the shipped package constructs, which modules reach a
directory -- and its docstring enumerates exactly those three. Neither claim here
has that shape: one compares four *documents* against each other with no scan at
all, and the other's fact side is a value in a dict literal. Folding them in
would have meant rewriting that module's stated subject and teaching its
surface reader about JSON and about tokenized comments.

**What this does not hold.** That the flag's value is *correct* -- that
``review.search`` really is registered and callable. That is behaviour, and it is
held by ``tests/integration/test_mcp_tools.py`` and
``tests/integration/test_wire_contract.py``. Every arm here would pass against a
daemon that registered nothing and declared ``true``.

Pure: it reads ten repository files -- two of them twice, for two different
claims -- and opens no database, no socket and no temporary directory.
"""

from __future__ import annotations

import ast
import json
import pathlib
import re
import tokenize
from collections.abc import Callable
from typing import Final

import pytest
from write_lock_claims import REPO_ROOT

pytestmark = pytest.mark.unit

_TOOLS: Final = REPO_ROOT / "packages/theurian-core/src/theurian/mcp/tools.py"
_SCHEMA: Final = REPO_ROOT / "schemas/mcp/system-capabilities-response.schema.json"
_PROTOCOL_DOC: Final = REPO_ROOT / "docs/protocol/mcp-tools.md"
_FLAG_PIN: Final = REPO_ROOT / "packages/theurian-core/tests/integration/test_mcp_tools.py"
_REVIEW_PACKAGE: Final = REPO_ROOT / "packages/theurian-core/src/theurian/review/__init__.py"
_GITHUB_PACKAGE: Final = (
    REPO_ROOT / "packages/theurian-core/src/theurian/infrastructure/github/__init__.py"
)
_BOUNDARY_ADR: Final = REPO_ROOT / "docs/adr/0026-evidence-plane-not-control-plane.md"
_ROADMAP: Final = REPO_ROOT / "docs/roadmap.md"
_README: Final = REPO_ROOT / "README.md"
_CORE_CHANGELOG: Final = REPO_ROOT / "packages/theurian-core/CHANGELOG.md"

#: The flag whose meaning all eight records state.
_FLAG: Final = "reviewIngestion"

#: A "it never meant X" clause, with the quoted subject captured.
#:
#: Both quoting styles are accepted because both are correct where they are used:
#: prose and JSON quote with ``"``, and a Python comment or a test message quotes
#: an identifier-shaped phrase in backticks. What the identity claim is about is
#: the **subject** -- the reading a client must not take from the flag -- and the
#: elaboration after each clause is deliberately different per site, because each
#: site has different room.
_NEVER_MEANT: Final = re.compile(r"never meant [\"`]([^\"`]+)[\"`]")

#: How many such clauses the comment says there are. Read out of the code rather
#: than counted here would be circular; two is the claim, and a third arriving
#: at three sites and not the fourth is precisely what the identity arm catches.
_EXPECTED_CLAUSES: Final = 2


def _flattened(text: str) -> str:
    """*text* with its soft wraps flattened, markup and case preserved.

    Every one of these records wraps, and ``docs/protocol/mcp-tools.md`` wraps
    *between* "It never" and "meant" -- so an unflattened search finds three of
    the four sites and reports the fourth as having dropped the sentence.
    """
    return " ".join(text.split())


def _comment_text(source: pathlib.Path) -> str:
    """Every comment in *source*, joined, with the markers removed.

    Tokenized rather than pattern-matched off the lines, because a ``#`` inside a
    string literal is not a comment and a comment block is what a reader sees as
    one paragraph. A syntax tree cannot answer this at all: comments are
    discarded before the tree exists.
    """
    with source.open("rb") as handle:
        tokens = tokenize.tokenize(handle.readline)
        return _flattened(
            " ".join(
                token.string.lstrip("#").strip()
                for token in tokens
                if token.type == tokenize.COMMENT
            )
        )


def _string_constants(source: pathlib.Path) -> str:
    """Every string constant in *source*, joined.

    Through the syntax tree, because the assertion message this reads is written
    as implicitly concatenated literals across five source lines -- one string to
    the reader and to the interpreter, five to a text search, with a clause
    broken across two of them. The tree does the joining the language does.
    """
    tree = ast.parse(source.read_text(encoding="utf-8"), filename=source.name)
    return _flattened(
        " ".join(
            node.value
            for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
        )
    )


def _schema_description(source: pathlib.Path, flag: str) -> str:
    """The published description of *flag* in the capabilities schema.

    Loaded rather than read as text, because in the file the description is one
    escaped JSON string and what a client's tooling shows a person is the decoded
    prose. A text search would be matching backslashes nobody reads.
    """
    document = json.loads(source.read_text(encoding="utf-8"))
    capabilities = document["properties"]["capabilities"]["properties"]

    assert flag in capabilities, (
        f"the capabilities schema declares no `{flag}` property; it declares "
        f"{sorted(capabilities)}. The description this arm reads is the wire contract's "
        f"own statement of what the flag means, and without it a client has only the "
        f"boolean"
    )
    return _flattened(capabilities[flag]["description"])


def _markdown_prose(source: pathlib.Path) -> str:
    """*source* as prose, with block quoting removed before the wraps are flattened.

    A Markdown document is read as text, but not as raw text: the sentence this
    module quotes out of ADR-0026 lives in a ``>`` block quote, so flattening the
    wraps alone leaves a ``>`` in the middle of it and every quotation that spans
    a line break reports itself missing. What a reader sees is the quoted prose,
    and that is what the quotation has to be checked against.
    """
    return _flattened(
        " ".join(
            line.lstrip().removeprefix(">").lstrip() if line.lstrip().startswith(">") else line
            for line in source.read_text(encoding="utf-8").splitlines()
        )
    )


def _package_docstring(source: pathlib.Path) -> str:
    """*source*'s module docstring, flattened.

    Read out of the syntax tree rather than by importing the package, so this pin
    does not depend on either package importing cleanly -- ``infrastructure/github``
    pulls in the whole adapter -- and so a docstring stripped by ``-O`` at some
    future runtime is not what is being checked.
    """
    docstring = ast.get_docstring(ast.parse(source.read_text(encoding="utf-8")))

    assert docstring, (
        f"{source.parent.name}/__init__.py has no module docstring. That docstring is "
        f"the package's only statement of what it holds and what it owes, and both "
        f"packages reach a claim about `{_FLAG}` through it"
    )
    return _flattened(docstring)


def _published_capability(flag: str) -> object:
    """The value ``mcp/tools.py`` publishes for *flag*, read from its syntax tree.

    From the source rather than by calling the tool, because calling it needs a
    server, a registry and an event loop -- and what is wanted here is the value a
    contributor wrote, which is the thing that moves. The behaviour half, that the
    daemon really answers this, is
    ``tests/integration/test_mcp_tools.py::test_capabilities_report_what_is_and_is_not_built``.
    """
    tree = ast.parse(_TOOLS.read_text(encoding="utf-8"), filename=_TOOLS.name)
    published = [
        value
        for node in ast.walk(tree)
        if isinstance(node, ast.Dict)
        for key, value in zip(node.keys, node.values, strict=True)
        if isinstance(key, ast.Constant) and key.value == flag
    ]

    assert len(published) == 1, (
        f"{_TOOLS.name} assigns `{flag}` in {len(published)} dict literals, expected 1. "
        f"Zero means the capability block was rewritten into a shape this reader does "
        f"not see, and the era selected below would then be about nothing"
    )
    value = published[0]
    assert isinstance(value, ast.Constant), (
        f"`{flag}` is published as `{ast.unparse(value)}`, which is computed rather "
        f"than written. A capability whose value depends on deployment state is a "
        f"different contract from the one these records describe -- they narrate a "
        f"constant, and a reader takes the narration as true of every build alike"
    )
    return value.value


#: Where the "it never meant" sentences are stated, and how each is read.
#:
#: All four, named here rather than discovered, because the claim is about
#: exactly these four: ``mcp/tools.py``'s comment says so. A fifth site that
#: started carrying the sentences would not redden this -- which is the honest
#: bound, and the answer then is to add it here with the change that adds it.
_NEVER_MEANT_SITES: Final[tuple[tuple[str, str], ...]] = (
    ("mcp/tools.py (the capability comment)", "comments"),
    ("schemas/mcp/system-capabilities-response.schema.json", "schema"),
    ("docs/protocol/mcp-tools.md", "markdown"),
    ("tests/integration/test_mcp_tools.py (the flag's own pin)", "constants"),
)


def _site_text(reader: str) -> str:
    """The text of one site, extracted the way that site's reader reads it."""
    if reader == "comments":
        return _comment_text(_TOOLS)
    if reader == "schema":
        return _schema_description(_SCHEMA, _FLAG)
    if reader == "markdown":
        return _flattened(_PROTOCOL_DOC.read_text(encoding="utf-8"))
    return _string_constants(_FLAG_PIN)


def test_the_four_sites_spell_the_never_meant_sentences_one_way() -> None:
    """RED means one of the four records started saying something the others do not.

    ``mcp/tools.py``'s comment makes this claim about itself and three other
    files: the same two sentences, "spelled one way across all four
    deliberately, because two of them carried the ``false``-reading form and a
    reader meeting both would have to work out whether the difference meant
    anything". So the identity is the product's own stated invariant, and it was
    stated *because* the four had already drifted once.

    It is compared as an equality between four independently extracted sets, not
    against a copy written here: a copy would be a fifth spelling, and the first
    reword would leave five records where the claim is about four agreeing.

    What is compared is the **quoted subject** of each clause -- the reading a
    client must not take from the flag -- and not the explanation after it. Those
    differ per site on purpose, because a schema description, a comment, a
    reference document and an assertion message have different room, and holding
    them byte-identical would force the shortest one's wording on all four.

    Two premises, both of which would otherwise pass silently. Every site must
    yield at least one clause, or a site whose extractor stopped working would
    make the sets equal by being empty in the same way; and the shared set must
    have the two members the comment says it has, so a third sentence that
    reached all four is noticed rather than absorbed.
    """
    found = {
        label: frozenset(_NEVER_MEANT.findall(_site_text(reader)))
        for label, reader in _NEVER_MEANT_SITES
    }

    silent = sorted(label for label, clauses in found.items() if not clauses)
    assert not silent, (
        f'{silent} carry no `never meant "..."` clause at all. Either the site dropped '
        f"the sentences -- which is the drift this arm exists to report, and the "
        f"equality below cannot report it because an empty set matches another empty "
        f"set -- or its extractor stopped seeing them. Settle which before touching "
        f"anything: the extractors are one per site because each reader reads that site "
        f"differently."
    )

    spellings = set(found.values())

    assert len(spellings) == 1, (
        "the four records no longer spell the `it never meant` sentences one way:\n"
        + "\n".join(f"  {label}: {sorted(clauses)}" for label, clauses in sorted(found.items()))
        + "\n\n`mcp/tools.py` states that all four carry the same two sentences, and "
        "gives the reason: two of them once carried the `false`-reading form, and a "
        "reader meeting both had to work out whether the difference meant anything. "
        "Whichever site moved, the other three move with it -- or the comment stops "
        "claiming an identity that is not there."
    )
    assert len(next(iter(spellings))) == _EXPECTED_CLAUSES, (
        f"the four records agree on {sorted(next(iter(spellings)))}, which is "
        f"{len(next(iter(spellings)))} sentences where `{_TOOLS.name}` says two. A third "
        f"reading a client must not take from the flag is worth stating -- state it in "
        f"all four, and say so here, in the change that adds it"
    )


#: Each record that **narrates** the flag's value, what it must say, and what it
#: must not, per published value.
#:
#: Narrates rather than mentions: these seven tell a reader what the value *is*
#: and reason from it, so they go false the moment it moves, while the many
#: records that merely name the flag do not.
#:
#: **The last two are read whole rather than scoped to their own section**, and
#: the fragments are long enough to be unambiguous in a file that is. The core
#: changelog in particular carries several historical ``reviewIngestion: false``
#: sentences that are correct as records of the releases they describe, so what
#: the ``false`` column quotes is the adapter entry's *own* sentence and not the
#: bare value.
#:
#: **The third row is the same defect twice over.** ``test_mcp_tools.py``
#: asserted ``reviewIngestion is True`` while one docstring narrated "the `false`
#: now says no ingestion call surface is callable" and a sibling assertion
#: message said the flag "stays false" -- both left behind by the flip, in the
#: file that is the flag's own pin. Neither was reachable from the two package
#: docstrings' rows, which is why the row exists rather than the two sentences
#: being quietly corrected.
#:
#: The ``false`` column is not hypothetical wording anywhere: it is what each
#: record really said, quoted rather than invented. A branch sha is orphaned by
#: the squash that merges it and a quotation survives, which is the citation rule
#: ``test_review_ingest_changelog_claims.py`` records.
_ERA_NARRATIONS: Final[tuple[tuple[str, str, tuple[str, ...], tuple[str, ...]], ...]] = (
    (
        "review/__init__.py",
        "review-package",
        (
            "``system.capabilities`` reports ``reviewIngestion: true``",
            "because that serving half is callable",
            "``reviewIngestion`` moved for the serving half, not for them",
        ),
        (
            "``system.capabilities`` reports ``reviewIngestion: false`` while no tool "
            "exposes any of it",
            "``reviewIngestion`` is still ``false``",
        ),
    ),
    (
        "infrastructure/github/__init__.py",
        "github-package",
        (
            "That tool is what moved ``system.capabilities`` to ``reviewIngestion: true``",
            "A fetch is still an operator's act through the CLI verb.",
        ),
        (
            "``system.capabilities`` still reports ``reviewIngestion: false``",
            "Slice 3 adds the tool and flips it.",
        ),
    ),
    (
        "tests/integration/test_mcp_tools.py (the flag's own pin)",
        "flag-pin",
        (
            "and then, with slice 3's serve tool, went `true` under that narrowed "
            "reading: it says an ingestion call surface exists that a client may call",
            "`reviewIngestion` below is a different `true` about a different corpus",
        ),
        (
            "the `false` now says no ingestion call surface is callable, not that nothing "
            "reaches GitHub",
            "which is what `reviewIngestion` below stays false for",
        ),
    ),
    (
        "docs/adr/0026-evidence-plane-not-control-plane.md (Compliance)",
        "boundary-adr",
        (
            "`reviewIngestion: true` is that case a second time",
            "The boundary is where it was: ingested review content is evidence a "
            "caller reads, and it gates nothing.",
        ),
        ("The other capability flags — `traceability: false`, `reviewIngestion: false` —",),
    ),
    (
        "docs/roadmap.md (the not-shipped preamble)",
        "roadmap",
        ("The **review ingestion adapter was on that list and no longer belongs on it**",),
        (
            "Neither does any write-intent MCP tool, review ingestion adapter, or "
            "evaluation harness.",
        ),
    ),
    (
        "README.md (the AI-proposes-humans-approve row)",
        "readme",
        (
            "`system.capabilities` reports `reviewIngestion: true` beside "
            '`reviewIngestionScope: "public-allowlisted"`, which says an ingestion call '
            "surface exists that a client may call",
            "none spawns `gh`, none lands a file",
        ),
        (
            "`system.capabilities` reports `reviewIngestion: false` while no *MCP* tool "
            "exposes either half.",
        ),
    ),
    (
        "packages/theurian-core/CHANGELOG.md (the `gh`-adapter entry)",
        "core-changelog",
        (
            "which is a statement about the callable surface (`review.search`, a read "
            "over what `theurian review build` projected out of `.theurian/review/`) "
            "and not about this adapter.",
            '**Not "a read over what an operator ingested"**',
        ),
        (
            "`theurian review ingest` is what reaches this code; **no MCP tool does**, "
            "so `system.capabilities` still reports `reviewIngestion: false`.",
        ),
    ),
)


#: One extractor per narrating record, keyed by the row's own reader name.
#:
#: A mapping rather than a chain of returns: the population has grown twice since
#: this was two branches, and each growth added a branch that looked like the one
#: above it. A missing key raises here, naming the reader, where a trailing
#: ``return`` silently handed the new row the *last* extractor's text -- which
#: would be a row asserting sentences against a file it does not describe, and
#: green for the wrong reason.
_ERA_READERS: Final[dict[str, Callable[[], str]]] = {
    "review-package": lambda: _package_docstring(_REVIEW_PACKAGE),
    "github-package": lambda: _package_docstring(_GITHUB_PACKAGE),
    "boundary-adr": lambda: _markdown_prose(_BOUNDARY_ADR),
    "roadmap": lambda: _markdown_prose(_ROADMAP),
    "readme": lambda: _markdown_prose(_README),
    "core-changelog": lambda: _markdown_prose(_CORE_CHANGELOG),
    "flag-pin": lambda: _string_constants(_FLAG_PIN),
}


def _era_text(reader: str) -> str:
    """The text of one narrating record, read the way its own reader reads it.

    The flag's own pin is read through its string constants rather than as a file,
    because both of its narrations are a docstring and an assertion message -- one
    of them written as implicitly concatenated literals with the claim broken
    across two of them.
    """
    assert reader in _ERA_READERS, (
        f"`{reader}` names no extractor, so the row that asked for it would be "
        f"asserting its sentences against another record's text. The readers are "
        f"{sorted(_ERA_READERS)}"
    )
    return _ERA_READERS[reader]()


def _assert_states_the_era(label: str, text: str, published: object) -> None:
    """The docstring check, in one place so its control drives the same path.

    Takes the published value and *selects* the wording from it, which is what
    makes the pin bidirectional: the same helper demands the ``false``-era
    sentences of a build that publishes ``false``, so a flip back is RED until
    the docstrings are re-tensed with it.
    """
    row = next(case for case in _ERA_NARRATIONS if case[0] == label)
    _name, _reader, on_true, on_false = row
    required, forbidden = (on_true, on_false) if published is True else (on_false, on_true)

    missing = [sentence for sentence in required if sentence not in text]
    stale = [sentence for sentence in forbidden if sentence in text]

    assert not missing and not stale, (
        f"{label} does not narrate `{_FLAG}: {str(published).lower()}`, which is what "
        f"`{_TOOLS.name}` publishes.\n"
        + "".join(f"\n  missing: {sentence}" for sentence in missing)
        + "".join(f"\n  stale:   {sentence}" for sentence in stale)
        + "\n\nIf the flag moved, this record is re-tensed in the same commit: it is "
        "one of the five that narrate the value rather than merely mention it, and a "
        "record describing an era the build has left is read as current by everyone "
        "who opens it. If the flag did not move, the record drifted and the wording is "
        "what gets restored."
    )


@pytest.mark.parametrize(
    ("label", "reader"),
    [(case[0], case[1]) for case in _ERA_NARRATIONS],
    ids=[case[0] for case in _ERA_NARRATIONS],
)
def test_each_record_narrates_the_flag_the_capability_dict_publishes(
    label: str, reader: str
) -> None:
    """RED means a package docstring describes an era this build has left.

    Both of these were corrected when slice 3 flipped the flag, and both shipped
    with nothing holding the correction -- recorded as owed at the time rather
    than left silent. That is the state a re-tensing lands in: the wording is
    freshly right, nobody rereads what was just written, and the next rewrite has
    no reason to keep it.

    The pin is bidirectional because either side can move. A docstring reworded
    back to the ``false`` era is RED against a build that publishes ``true``; a
    build that flips the flag back is RED against docstrings still saying
    ``true``. Neither half alone would catch the other's failure, and the pair is
    what makes "the flag moving reddens the docstrings' claim" mean something.

    What decides which wording is demanded is the value read out of
    ``mcp/tools.py``'s own capability dict, so the expectation is never written
    twice.
    """
    _assert_states_the_era(label, _era_text(reader), _published_capability(_FLAG))


def test_the_docstring_checker_demands_the_other_era_when_the_flag_moves() -> None:
    """The positive control for a pin whose two directions cannot both be observed.

    The arm above runs against one published value at a time, so on any given day
    only one of its two directions is exercised and the other is a claim about
    what would happen. That is exactly the shape that quietly stops working: the
    unexercised branch could select the wrong column, compare against nothing, or
    have been written the wrong way round, and nothing would say so until the
    flag moved -- which is once.

    So both directions are driven here against synthetic text. The live wording is
    asked about under the *opposite* published value and must be reported stale,
    and the ``false``-era wording is asked about under ``True`` and must be
    reported too. Nothing in ``src/`` is read for the second half and nothing is
    written at all.
    """
    label, reader, _on_true, on_false = _ERA_NARRATIONS[0]
    live = _era_text(reader)

    with pytest.raises(AssertionError, match=re.escape("missing")):
        _assert_states_the_era(label, live, published=False)
    with pytest.raises(AssertionError, match=re.escape("stale")):
        _assert_states_the_era(label, " ".join(on_false), published=True)
