"""T-24's evidence plane, held to the four facts its grade rests on.

``docs/security/threat-model.md``'s T-24 records an **accepted residual**: a
repository can ship its own ``.theurian/review/``, a victim runs the documented
``theurian review build``, and ``review.search`` serves fabricated review history
as the repository's own. Nothing refuses it, and the entry says so.

An accepted residual is the shape that most needs recomputing, because nobody
ever revisits it. Its grade is not a label -- the entry argues **Medium** where
its sibling T-3 is High, from properties of this surface -- and each of those
properties is a fact about today's code that could stop being true without a word
of the entry moving.

**The four the entry's reasoning stands on, and the failure each one hides.**

1. **``.theurian/review/`` is source, not derived.** That is what makes T-19's
   control inapplicable here: no ignore has to be bypassed, so a clone's copy is
   not evidence of tampering. If the directory ever joined the managed
   ``.gitignore`` block or ``DERIVED_SUBDIRECTORIES``, the entry would be
   describing a plane that no longer exists -- and ADR-0030 decision 3's data-loss
   argument would have been reversed silently.
2. **The build really does record provenance.** The entry's whole point is that
   every gate downstream is satisfied *honestly*, and the mechanism is
   ``rebuild_search_store`` calling ``BuildProvenance.record_review``. Take that
   call away and T-24 describes a victim path that no longer runs, while T-19's
   sibling face gets wider.
3. **Provenance has no per-record member.** ``BuildProvenance`` vouches for a
   *store*; the entry states plainly that it "does not answer *did this
   installation ingest the evidence*", and records verifying that as **unowned**.
   The day a ``record_evidence``/``has_evidence`` family lands, the residual is
   no longer accepted -- it is closed, or partly so -- and the entry has to say
   which.
4. **The grade and its falsification trigger are still written down.** A grade
   with no stated way to be wrong is a label. The entry names three things that
   would raise it, and that paragraph is what makes the Medium checkable by
   someone who was not in the round.

**A fifth thing is held, and it is a population rather than a fact.** T-24's
control table carries the rule the round that closed this class wrote: a sentence
asserting provenance or an ingestion guarantee for a field that can arrive clone-
delivered either names the route it holds for, or says what the read actually
checks -- shape and the derived path, never authorship. That closure was reached
by deriving the sentences rather than fixing the ones a review quoted, and a
closure of that shape decays through the *next* sentence somebody writes. So the
population is re-derived here every run from the surfaces and the key the census
used, and compared against the census that judged it: a new matching phrase has
no judgement behind it and fails. It cannot decide whether a sentence is honest
-- that is a reading -- but an unjudged member can no longer be added silently.

**The key is applied to unwrapped text, and every branch of it carries its own
floor.** Both are corrections to how that population was derived, and both came
from a round attacking the derivation rather than the sentences. A key applied
line by line cannot see a phrase a soft wrap split -- ``as the provider`` ending
one comment line and ``resolved it`` opening the next is invisible to it, and
two of this tree's sentences are written exactly that way -- so the surfaces are
folded first: line breaks, the ``#`` comment markers a continuation line carries,
and the escaped newline a JSON string has to wrap with. And a total that only has
to stay large is satisfied by a key most of whose alternation has stopped
matching anything, so each branch is recorded with the hits it contributes and
held to them: a branch deleted from the key, or one whose sentences were reworded
away, reddens on its own rather than disappearing into a total.

**Both sides are pinned, and they fail differently.** The fact arms read the
live symbols and one syntax tree; the prose arms read the entry and the
document's summary table. Neither side is parsed from the other. A prose arm RED
while the fact arms are green means the record drifted; the reverse means the
product did, and the entry moves in that same commit.

**The absence claims carry their positive control inline.** "``review`` is not in
``DERIVED_SUBDIRECTORIES``" is a claim that a collection lacks a member, and a
collection read from the wrong place lacks every member. So each absence arm
asserts the *sibling's presence* in the same breath: ``state`` is derived and
``.theurian/state/`` is ignored, which is the contrast T-24's opening paragraph
is built on -- and a lookup that found neither would fail on the presence half
rather than pass on the absence half.

Pure in the sense the other claim pins are: one document read as text, one module
read as a syntax tree, two modules read for their symbols, and the thirteen
authority surfaces read as unwrapped text -- no database, socket or temporary
directory.
"""

from __future__ import annotations

import ast
import pathlib
import re
from collections.abc import Mapping
from typing import Final

import pytest
from threat_model_claims import CHECKER, RECORDER, THREAT_MODEL, artifact_families, entry, prose
from write_lock_claims import REPO_ROOT

from theurian.application.project_service import BuildProvenance
from theurian.domain.project import DERIVED_SUBDIRECTORIES, GITIGNORE_ENTRIES

pytestmark = pytest.mark.unit

#: The entry this module reads. Sliced by ``threat_model_claims.entry``, which is
#: where the anchoring rules and the reason for them live.
_THREAT_ID: Final = "T-24"

#: The plane T-24 is about, spelled as ``theurian init`` would have written it
#: into the managed block, and as its own subdirectory name.
_EVIDENCE_ENTRY: Final = ".theurian/review/"
_EVIDENCE_SUBDIRECTORY: Final = "review"

#: T-19's plane, used as the positive control for both absence arms: it is the
#: sibling the entry contrasts itself against, and it is a member of both
#: collections.
_DERIVED_ENTRY: Final = ".theurian/state/"
_DERIVED_SUBDIRECTORY: Final = "state"

#: The artifact family this branch added, by the name ``BuildProvenance`` gives
#: it. Spelled out rather than derived -- nothing in the class says which family
#: the review search store belongs to -- and guarded by a membership premise, so
#: a rename fails naming itself.
_REVIEW_FAMILY: Final = "review"

#: Every artifact family ``BuildProvenance`` carries today. An equality rather
#: than a membership test, because T-24's residual is an **absence**: the set may
#: not grow a per-record member without the entry moving. Recorded here so the
#: failure message can name what arrived.
_RECORDED_FAMILIES: Final = ("findings", "index", "review", "state")

#: The composition root the entry names as the victim's own honest build, and the
#: recorder it must call.
_BUILD_FUNCTION: Final = "rebuild_search_store"
_REVIEW_COMMANDS: Final = REPO_ROOT / "packages/theurian-core/src/theurian/cli/review_commands.py"

#: The grade, as the heading and the narrative both spell it. Held in two places
#: because a heading edited alone is the drift that leaves a reader's index
#: disagreeing with the entry they open.
_GRADE: Final = "medium"

#: What identifies the paragraph that makes the grade falsifiable. Keyed on the
#: phrase that says what the paragraph *is*, not on any of the three triggers it
#: lists: a key naming a trigger would be satisfied by its own subject, so
#: deleting that trigger would drop the paragraph out of the population rather
#: than fail.
_UPGRADE_ANCHOR: Final = "what would raise it, so the grade is falsifiable rather than a label"

#: The summary row's own key. The table lives outside every entry -- it is the
#: index a reader scans before opening anything -- so it is looked for in the
#: whole document and identified by the leading cell.
_SUMMARY_CELL: Final = "| T-24 |"


def _calls_inside(function: str, source: pathlib.Path) -> frozenset[str]:
    """Every ``<something>.method(...)`` attribute call inside *function*.

    The receiver is deliberately unconstrained, for the reason
    ``test_threat_model_t19_claims.py`` gives at its own call-site arm: the claim
    is that the build asks for a provenance record, and a check that recognised
    only one spelling of the receiver would stop seeing the call the moment it
    was reached through another name.
    """
    tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
    wanted = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.name == function
    ]

    assert len(wanted) == 1, (
        f"{source.name} defines `{function}` {len(wanted)} times, expected once. T-24 "
        f"names it as the command that projects a repository's own evidence files and "
        f"then records provenance for the result, so with none of them the arm below "
        f"would pass over an empty call set -- which is what a build that records "
        f"nothing also looks like"
    )
    return frozenset(
        node.func.attr
        for node in ast.walk(wanted[0])
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    )


def test_the_review_evidence_plane_is_neither_git_ignored_nor_derived() -> None:
    """RED means T-24's opening distinction from T-19 stopped being true.

    Everything else in the entry follows from this. T-19's control works because
    ``.theurian/state/`` is git-ignored and derived: a copy that arrived with a
    clone had to be force-added past the ignore, which is itself the signal, and
    ``BuildProvenance`` refuses it because this installation did not build it.
    T-24 exists precisely because ``.theurian/review/`` is neither -- ADR-0030
    decision 3 makes it **source**, since an upstream comment can be deleted and a
    discarded local copy is data loss no refetch recovers.

    So a change that added the directory to the managed ``.gitignore`` block, or
    to ``DERIVED_SUBDIRECTORIES``, would not merely make a sentence stale. It
    would reverse a recorded design decision by editing a tuple, and the reversal
    would look like tidying.

    The sibling's membership is asserted first and in the same arm, because a
    collection read from the wrong module lacks every member and would report
    this absence as a safety.
    """
    ignored = tuple(GITIGNORE_ENTRIES)
    derived = tuple(DERIVED_SUBDIRECTORIES)

    assert _DERIVED_ENTRY in ignored and _DERIVED_SUBDIRECTORY in derived, (
        f"`{_DERIVED_ENTRY}` is not among the managed block's entries ({list(ignored)}) "
        f"or `{_DERIVED_SUBDIRECTORY}` is not derived ({list(derived)}). Those are T-19's "
        f"plane and the contrast T-24 is built on: with them missing, the absence "
        f"checked below is evidence of nothing -- this module is reading the wrong "
        f"collections"
    )

    assert _EVIDENCE_ENTRY not in ignored, (
        f"`{_EVIDENCE_ENTRY}` is now written into the managed `.gitignore` block. T-24 "
        f"records that `theurian init` deliberately does not, because ADR-0030 decision "
        f"3 makes the evidence **source**: an upstream comment can be edited or deleted, "
        f"and a discarded local copy of a deleted comment is data loss no refetch "
        f"recovers. Whether a project commits its review evidence is the project's "
        f"decision, and this line takes it away from them"
    )
    assert _EVIDENCE_SUBDIRECTORY not in derived, (
        f"`{_EVIDENCE_SUBDIRECTORY}` joined `DERIVED_SUBDIRECTORIES`, so the evidence "
        f"plane is now declared rebuildable. It is not: the files under "
        f"`{_EVIDENCE_ENTRY}` are the record, and `theurian review build` projects a "
        f"store *from* them. `ProjectPaths.review`'s own docstring says it must never "
        f"join this tuple, and every path that treats a derived directory as disposable "
        f"now reaches it"
    )


def test_the_build_t24_describes_records_provenance_for_the_store_it_projects() -> None:
    """RED means the honest-victim path T-24 describes no longer runs.

    The entry's mechanism is uncomfortable and exact: the victim does nothing
    wrong. They clone, they run the documented ``theurian review build``, and that
    command projects whatever is under ``.theurian/review/`` and then records
    provenance for the store -- so every gate downstream is satisfied honestly,
    because this installation really did build it.

    That sentence needs the call to exist. Without it the store is unservable and
    T-24's reach is a different, smaller thing -- while T-19's face gets wider,
    since a store that arrived with the clone and one this build made would both
    be refused. Either way the entry is describing a product that changed.

    Read from source rather than run: what is asserted is that this function asks
    for the record, and running it would need a project tree, a store and a real
    build. The behaviour half is
    ``tests/integration/test_review_search_rebuild.py`` and
    ``tests/integration/test_findings_store_reads_are_governed.py``.
    """
    families = artifact_families(dir(BuildProvenance))
    called = _calls_inside(_BUILD_FUNCTION, _REVIEW_COMMANDS)

    assert _REVIEW_FAMILY in families, (
        f"`{_REVIEW_FAMILY}` is no longer one of `BuildProvenance`'s families "
        f"({list(families)}), so the recorder named below is not the one this branch "
        f"added; rename it in the entry and here together"
    )

    assert f"{RECORDER}{_REVIEW_FAMILY}" in called, (
        f"`{_BUILD_FUNCTION}` no longer calls `{RECORDER}{_REVIEW_FAMILY}`; it calls "
        f"{sorted(called)}. T-24's mechanism is a victim who runs the documented build "
        f"and satisfies every gate honestly -- without this call there is no provenance "
        f"record, the serve path refuses the store, and the entry describes a reach the "
        f"product no longer has"
    )


def test_provenance_gained_no_per_record_member_while_t24_records_one_as_unowned() -> None:
    """RED means the residual T-24 accepts was implemented, and the entry has to move.

    This is the arm that has never fired and is the reason the module exists.
    T-24's grade rests on what ``BuildProvenance`` **cannot** answer: it vouches
    for a store, never for the evidence the store was projected from, and the
    entry records verifying evidence provenance as *unowned* -- deliberately, so
    that an owed item does not get a milestone nothing has scheduled.

    A per-record anchor would arrive as another family on this class: a
    ``record_evidence``/``has_evidence`` pair, or whatever it is called. On that
    day the residual is no longer accepted -- it is closed, or narrowed -- and the
    entry, the grade and the *what would raise it* paragraph all have to say which.

    An equality over the derived family set, so it fails in both directions: a
    new family reddens, and a family disappearing means one of the four artifacts
    stopped being vouched for at all, which is a different product. The set is
    derived from the class rather than transcribed, so this is not a list somebody
    remembered.
    """
    families = artifact_families(dir(BuildProvenance))

    assert families == _RECORDED_FAMILIES, (
        f"`BuildProvenance` carries {list(families)}, and this module recorded "
        f"{list(_RECORDED_FAMILIES)}.\n\n"
        f"A NEW family: if it vouches for review *evidence* rather than for a store, "
        f"T-24's residual is no longer accepted-and-unowned. Rewrite the entry -- the "
        f"'provenance answers a narrower question than a reader expects' paragraph, the "
        f"Medium grade and its upgrade triggers -- in the change that lands it. If it "
        f"vouches for something else, record it here with what it covers.\n\n"
        f"A MISSING family: an artifact this installation used to vouch for is now "
        f"ungated. T-19 is the entry that moves for that, and it enumerates the "
        f"families by name.\n\n"
        f"Either way `{CHECKER}*` and `{RECORDER}*` are what this set is read from, so "
        f"the change that renames one half is the change that reddens here."
    )


def test_t24_keeps_its_medium_grade_and_the_paragraph_that_makes_it_falsifiable() -> None:
    """RED means an accepted residual lost the reasoning that lets anyone check it.

    T-24 is graded Medium against a scale on which its sibling T-3 is High, and
    the entry does not assert that -- it argues it, from two properties of this
    surface, and then names three things that would raise it. That last paragraph
    is what separates a grade from a label: a reader who was not in the round can
    take a change, hold it against those three triggers, and decide.

    Both places the grade appears are held. The heading is what a reader sees in
    the document's outline and what the summary table has to agree with; the
    narrative is where the argument lives. A heading edited alone leaves the index
    and the entry disagreeing about the severity of an accepted residual, which is
    exactly the state nobody notices.

    Spelling only, and deliberately: this arm is blind to whether Medium is the
    right grade. The fact arms above are what say the properties it rests on still
    hold.
    """
    text = entry(_THREAT_ID)
    heading, _, body = text.partition("\n")

    assert _GRADE in prose(heading), (
        f"T-24's heading no longer grades it `{_GRADE}`: {heading[:200]}\n\n"
        f"The heading is the grade a reader meets first and the one the summary table "
        f"has to match. If the grade really moved, it moves in the heading, in the "
        f"narrative, in the summary row and in this pin together."
    )
    assert f"graded **{_GRADE.capitalize()}**" in body, (
        f"T-24's narrative no longer says `graded **{_GRADE.capitalize()}**`, so the "
        f"entry states a severity in its heading and argues for none. The two reasons "
        f"under that phrase -- the content never leaves the untrusted plane, and no "
        f"published value here is priced over records the caller did not receive -- are "
        f"what make the grade something other than a preference"
    )
    assert _UPGRADE_ANCHOR in prose(body), (
        f"T-24 no longer carries its *{_UPGRADE_ANCHOR}* paragraph. Without it the "
        f"Medium is a label: nothing tells the next reader which change would raise it, "
        f"and an accepted residual whose falsification condition is unwritten is one "
        f"nobody will ever revisit"
    )


def test_the_threat_summary_table_carries_t24_at_the_grade_the_entry_argues() -> None:
    """RED means the index a reader scans disagrees with the entry it points at.

    The summary table at the end of the threat model is how a reviewer takes in
    the whole surface before opening anything, and it is the one place an entry
    can go missing without any entry looking wrong. T-24 was added to it in the
    same commit as the entry; a later edit that dropped the row, or graded it
    differently there, leaves the document self-contradictory in the direction
    that under-reports.

    The row is looked for in the whole document rather than in the entry, because
    that is where it lives. Its grade cell is compared against the grade the entry
    argues -- read from the entry, not written here -- so the two move together or
    this fails.
    """
    document = THREAT_MODEL.read_text(encoding="utf-8")
    rows = [line for line in document.splitlines() if line.startswith(_SUMMARY_CELL)]

    assert len(rows) == 1, (
        f"the threat summary table carries {len(rows)} `{_THREAT_ID}` rows, expected 1. "
        f"Zero means the entry exists and the index does not mention it, which is the "
        f"omission a reviewer scanning the table would never detect"
    )
    cells = [cell.strip() for cell in rows[0].strip().strip("|").split("|")]

    assert len(cells) == 5, (
        f"the `{_THREAT_ID}` summary row splits into {len(cells)} cells, expected 5 "
        f"(id, title, STRIDE, severity, status). The grade below is read by position, "
        f"so a row of another width means it is read off the wrong column: {rows[0][:300]}"
    )
    assert prose(cells[3]) == _GRADE, (
        f"the threat summary table grades `{_THREAT_ID}` `{cells[3]}` while its entry "
        f"argues `{_GRADE}`. A reader triaging from the table would carry the table's "
        f"figure into a decision the entry does not support -- and if the grade really "
        f"moved, it moves in both places and in the entry's argument for it"
    )


# -- the honest-wording ratchet, derived ---------------------------------------

#: The surfaces T-24's control-table rule ranges over: everything that *describes*
#: a served review field, or the ingestion guarantee around it, with the authority
#: of the product rather than of a commit message. Taken verbatim from the census
#: that closed the class, so this pin and that closure read one population.
#:
#: A directory contributes its ``.py`` files. ``tests/`` is deliberately absent:
#: a test narrating the flag is held by ``test_review_ingestion_flag_claims.py``'s
#: era rows, which compare it against the published value rather than against this
#: rule.
_AUTHORITY_SURFACES: Final = (
    "schemas/mcp/review-search-response.schema.json",
    "schemas/mcp/system-capabilities-response.schema.json",
    "schemas/config/project-config.schema.json",
    "packages/theurian-core/src/theurian/mcp/review_search.py",
    "packages/theurian-core/src/theurian/mcp/tools.py",
    "packages/theurian-core/src/theurian/domain/review_search.py",
    "packages/theurian-core/src/theurian/application/review_search_builder.py",
    "packages/theurian-core/src/theurian/infrastructure/review_evidence/",
    "packages/theurian-core/src/theurian/infrastructure/sqlite/review_search_store.py",
    "docs/security/threat-model.md",
    "docs/protocol/mcp-tools.md",
    "docs/architecture/review-knowledge.md",
    "plugins/claude-code/commands/ingest.md",
)

#: The spellings a provenance or ingestion-guarantee sentence reaches for. Case-
#: insensitive, applied to each surface's unwrapped text, and the same alternation
#: the closing census ran -- so a member this pin reports is a member that census
#: judged.
#:
#: **Deliberately an over-approximation.** ``allowlist`` and ``secret scan`` catch
#: knowledge-side sentences that are out of the class entirely. That is the right
#: direction for a ratchet: it costs a judgement on a sentence that turns out to be
#: fine, and the alternative -- a key narrow enough to be quiet -- is a key that
#: misses the sentence nobody thought to spell out.
#:
#: Its top-level branches are split back out by :func:`_key_branches` and each is
#: held to its own floor, so the over-approximation cannot quietly become the only
#: part of this key that still matches anything.
_OVER_CLAIM_TERMS: Final = re.compile(
    r"at ingestion|Theurian (itself )?(wrote|writes)|written by Theurian|never received"
    r"|not received|allowlist|secret scan|was visible to|public[- ]allowlisted"
    r"|every record it holds|every record held|an author chose|provider's own"
    r"|as the provider resolved|the adapter record|the ingestion (adapter|run|path|scan)"
    r"|landed by|already ingested|pseudonymised at landing",
    re.IGNORECASE,
)

#: The line starts a wrapped sentence carries in this population. A continuation
#: line keeps its comment marker, so folding whitespace alone leaves
#: ``as the provider #: resolved it`` -- a phrase the key cannot see and a reader
#: reads as one sentence. Both the Sphinx attribute form and a plain comment are
#: stripped; a Markdown heading's ``#`` is stripped too, which is harmless because
#: a heading is never the middle of a wrapped phrase.
_CONTINUATION_MARKER: Final = re.compile(r"(?m)^[ \t]*#:?[ \t]?")

#: How a JSON string wraps, since it cannot carry a raw newline. **No shipped
#: schema in the population carries one today**, so this fold is driven by
#: :func:`test_the_key_reads_a_term_split_by_each_break_this_population_uses`'s
#: synthetic row rather than by the tree -- recorded rather than left to look
#: like a measured effect, because a fold nothing reaches would survive its own
#: deletion. It is kept because the three schemas are in the population and an
#: escaped newline is the only way a description of theirs can wrap.
_JSON_BREAK: Final = "\\n"


def _unwrapped(text: str) -> str:
    """*text* with every break this population wraps at folded to one space.

    Named apart from ``test_review_search_store_claims.py``'s ``_flattened``
    deliberately, though the whitespace half is the same technique. That one
    folds soft wraps and stops there, because it resolves ``file.py::test_name``
    citations and a comment marker never sits inside one. This one also has to
    drop the marker a continuation line carries and the escaped newline a JSON
    string wraps with, because the key below ranges over Python comment blocks
    and schema descriptions. Two names rather than one function with a flag, so
    neither module can be loosened by an edit made for the other.

    Case and markup are preserved for the sibling's reason: the key is applied
    case-insensitively by its own flag, and folding case here would hide which
    spelling a surface actually carries from anyone reading a failure.
    """
    return " ".join(_CONTINUATION_MARKER.sub(" ", text.replace(_JSON_BREAK, " ")).split())


#: Matching phrases per surface, measured 2026-09-11 at the commit that moves this
#: arm onto unwrapped text.
#:
#: **Phrases, not lines**, because a surface folded to one string has one line.
#: The unit change is most of the difference from the line-keyed figures this
#: replaced, and the two effects are worth separating rather than reporting as one
#: jump: the same key over the same tree found 78 matching *lines*, 98 matches
#: once a line carrying two of them counted two, and 100 once the surfaces were
#: unwrapped. So the fold itself is worth two matches -- both the phrase
#: ``as the provider resolved``, split across a ``#:`` wrap in
#: ``domain/review_search.py`` and in ``review_evidence/records.py``, and both
#: judged **honest**: each names the route it holds for (*on the ingest route*,
#: *on the run that fetched it*) and each sits under a class docstring that says
#: in as many words that these attributions describe the run and not a record read
#: back. Under the line key that whole branch counted zero, which is the blindness
#: this derivation change exists to end.
#:
#: ``mcp/tools.py`` reads 9 rather than the 8 the line-keyed census recorded: the
#: commit that aligned the ``reviewIngestion`` comment with the provenance the
#: read makes added a line quoting ``an operator already ingested`` **in order to
#: reject it**, and did not move the census. Judged honest -- the sentence around
#: it names what the read inspects, a file's shape and its derived path -- and
#: recorded here, which is also what takes this arm back to green.
_JUDGED_CENSUS: Final[dict[str, int]] = {
    "docs/security/threat-model.md": 34,
    "schemas/config/project-config.schema.json": 10,
    "packages/theurian-core/src/theurian/mcp/tools.py": 9,
    "schemas/mcp/review-search-response.schema.json": 9,
    "docs/architecture/review-knowledge.md": 8,
    "schemas/mcp/system-capabilities-response.schema.json": 7,
    "docs/protocol/mcp-tools.md": 5,
    "packages/theurian-core/src/theurian/domain/review_search.py": 5,
    "packages/theurian-core/src/theurian/application/review_search_builder.py": 4,
    "packages/theurian-core/src/theurian/infrastructure/review_evidence/records.py": 4,
    "plugins/claude-code/commands/ingest.md": 3,
    "packages/theurian-core/src/theurian/infrastructure/review_evidence/layout.py": 1,
    "packages/theurian-core/src/theurian/infrastructure/sqlite/review_search_store.py": 1,
}


def _surface_files() -> list[pathlib.Path]:
    """Every file the population key names, a directory expanded to its modules."""
    found: list[pathlib.Path] = []
    for surface in _AUTHORITY_SURFACES:
        path = REPO_ROOT / surface

        # Named rather than left to raise: a surface that moved is the way this
        # key goes blind, and the total below stays comfortably above its floor
        # when one file drops out. `FileNotFoundError` is a failure too, but it
        # says nothing about what to do with it.
        assert path.exists(), (
            f"`{surface}` is in the T-24 authority population and is not in the tree. "
            f"If it moved, move it here in the same commit -- a population key that "
            f"names a path nothing resolves stops reading a surface it claims to cover"
        )

        found.extend(sorted(path.rglob("*.py")) if surface.endswith("/") else [path])
    return found


def _authority_census() -> dict[str, int]:
    """Matching phrases per surface, keyed by repository-relative path.

    Each surface is unwrapped before the key is applied, so a phrase a soft wrap
    split is counted where a reader reads it. ``finditer`` rather than
    ``findall`` because the key carries groups and ``findall`` would then return
    them instead of the matches.
    """
    counted: dict[str, int] = {}
    for path in _surface_files():
        text = _unwrapped(path.read_text(encoding="utf-8"))
        hits = sum(1 for _ in _OVER_CLAIM_TERMS.finditer(text))
        if hits:
            counted[path.relative_to(REPO_ROOT).as_posix()] = hits
    return counted


def test_the_authority_surfaces_still_carry_the_sentences_the_key_was_built_for() -> None:
    """The premise: a key that matched nothing would report a clean surface.

    The arm below asserts that a population has not *grown*. An assertion of that
    shape passes perfectly against a key that stopped matching -- a renamed file, a
    moved schema, a regex broken by an edit -- and it would pass most convincingly
    at the moment it had stopped working. So the population is asserted to be
    substantial first, and asserted to reach the two surfaces the class was found
    on rather than merely to be non-empty somewhere.
    """
    census = _authority_census()

    assert sum(census.values()) > 65, (
        f"the population key matched {sum(census.values())} phrases across "
        f"{len(census)} surfaces. It matched 100 when it was moved onto unwrapped "
        f"text, so a figure this small means the key has stopped reading the surfaces "
        f"it names rather than that the sentences went away"
    )
    for surface in (
        "docs/security/threat-model.md",
        "schemas/mcp/review-search-response.schema.json",
    ):
        assert census.get(surface), (
            f"`{surface}` matched nothing. That is where the over-claiming sentences were "
            f"found, so a zero here is the key going blind, not the surface going quiet"
        )


#: One real key term per break this population wraps at, split the way that break
#: splits it, with the term the unwrapped text has to yield.
#:
#: Real terms and real break forms, not a synthetic regex against synthetic text:
#: the failure being controlled for is ``_unwrapped`` quietly folding one break and
#: not another, and a row written against a made-up key would be green while the
#: shipped key stayed blind.
#:
#: The JSON row is the one no surface drives. No schema in the population carries
#: an escaped newline today, so without this row that fold could be deleted and
#: every other arm here would stay green -- which is the shape of guard this
#: project has been burned by. The three schemas are in the population and a JSON
#: string cannot wrap any other way, so the fold is kept and this is what holds it.
_WRAPPED_TERM_CASES: Final[tuple[tuple[str, str, str], ...]] = (
    (
        "a bare soft wrap, the way a docstring or a Markdown paragraph wraps",
        "The repository the record names -- as the provider\nresolved it on the ingest route.",
        "as the provider resolved",
    ),
    (
        "a `#:` attribute-doc block, the way two of this tree's sentences wrap",
        "    #: The repository the record names -- as the provider\n"
        "    #: resolved it on the ingest route.",
        "as the provider resolved",
    ),
    (
        "a `#` comment block",
        "    # call `review.search` and read what an operator already\n"
        "    # ingested -- which is the wording this comment goes on to reject.",
        "already ingested",
    ),
    (
        "a JSON description's escaped newline",
        '  "description": "Every record the store serves was landed\\nby '
        '`theurian review ingest`."',
        "landed by",
    ),
)


@pytest.mark.parametrize(
    ("label", "wrapped", "term"),
    _WRAPPED_TERM_CASES,
    ids=[case[0] for case in _WRAPPED_TERM_CASES],
)
def test_the_key_reads_a_term_split_by_each_break_this_population_uses(
    label: str, wrapped: str, term: str
) -> None:
    """The premise under the census: the key has to survive a soft wrap.

    A census applied line by line is blind to every term an editor's wrap
    happened to split, and blind silently -- the surface's count simply does not
    include it, and a count that is too low reads exactly like a surface with
    fewer sentences on it. That was not hypothetical: ``as the provider
    resolved`` matched **nothing** in the whole population under the line key,
    while two authority surfaces carried it across a ``#:`` wrap.

    So each break this population actually wraps at is planted through the
    middle of a real key term and the key is asked twice. The negative half is
    what makes the row mean anything: the unfolded text must **not** match, so a
    row cannot pass by planting a term that was never split in the first place.

    This holds the derivation, not the tree. Whether the sentences on those
    surfaces are honest is a reading, recorded in ``_JUDGED_CENSUS``'s note.
    """
    assert not _OVER_CLAIM_TERMS.search(wrapped), (
        f"{label}: the key already matches the text with the break still in it, so "
        f"this row exercises nothing. The term has to be split by the break, or the "
        f"green below says only that the key matches unsplit text"
    )

    match = _OVER_CLAIM_TERMS.search(_unwrapped(wrapped))

    assert match is not None, (
        f"{label}: unwrapping recovered nothing, and `{term}` was planted across the "
        f"break.\n\n`_unwrapped` has stopped folding this break, so every sentence on "
        f"an authority surface that wraps this way is invisible to `_authority_census` "
        f"and to `_branch_census` -- and both report the absence as a clean surface."
    )
    assert match.group(0).lower() == term, (
        f"{label}: unwrapping recovered {match.group(0)!r} where `{term}` was planted. "
        f"Another branch of the key reached this text first, so the row no longer "
        f"demonstrates that the break is folded"
    )


def test_no_unjudged_sentence_joins_the_t24_authority_population() -> None:
    """RED means a provenance sentence entered an authority surface unjudged.

    T-24's control table states the rule this arm ratchets: *a sentence asserting
    provenance or an ingestion guarantee for a field that can arrive clone-
    delivered either names the route it holds for, or says what the read actually
    checks -- which is shape and the derived path, never authorship.* The class was
    closed by deriving that population and judging all of it, rather than by fixing
    the sentences a review round happened to quote.

    A closure like that decays in one way: the next sentence. Somebody documents a
    served field, reaches for "landed by `theurian review ingest`" because that is
    the route they have in mind, and the surface over-claims again -- for a corpus
    a clone can carry, which is the whole residual T-24 accepts. Nothing would say
    so, because the sentence reads exactly like the ones already there.

    So the **population** is derived here every run and compared against the census
    that judged it. A new matching phrase in any authority surface has no judgement
    behind it and fails, naming the file and the rule to judge it by. This does not
    and cannot decide whether the wording is honest -- that is a reading -- but it
    makes an unjudged member impossible to add silently, which is the part that was
    missing.

    **What it costs, recorded rather than claimed away.** The key is an over-
    approximation, so an out-of-class edit -- a knowledge-side ``allowlist``
    sentence -- reddens too, and the answer is to judge it and move the number. And
    a reword that removes one matching phrase while adding another leaves the count
    where it was: this arm holds the population's *size* per surface, not its text.
    Pinning a hundred sentences verbatim is the alternative, and it is not a
    stricter version of this -- it is a different pin, RED on every rewrap, which is
    why the size is what is held.

    **Counting phrases rather than lines is what makes a soft wrap invisible to an
    editor and visible here.** The surface is unwrapped first, so a term split
    across two comment lines counts, and a sentence rewrapped without being changed
    does not move the number.

    Paired with the fact side, which is held elsewhere and not restated here: the
    served field classification (``AUTHOR_CONTROLLED_FIELDS`` and
    ``PROVIDER_CONTROLLED_FIELDS``, pinned against the schema's own property set by
    ``test_schemas.py`` and against the served response by
    ``test_review_search_tool.py``), and the day per-record provenance lands, which
    is :func:`test_provenance_gained_no_per_record_member_while_t24_records_one_as_unowned`'s
    equality over ``BuildProvenance``'s families.
    """
    census = _authority_census()
    grown = {
        surface: (count, _JUDGED_CENSUS.get(surface, 0))
        for surface, count in census.items()
        if count > _JUDGED_CENSUS.get(surface, 0)
    }

    assert not grown, (
        "an authority surface gained a phrase matching the T-24 provenance key, and "
        "nothing has judged it:\n"
        + "".join(
            f"\n  {surface}: {now} matches, {then} judged"
            for surface, (now, then) in sorted(grown.items())
        )
        + "\n\nJudge the new sentence against T-24's control-table rule: a sentence "
        "asserting provenance or an ingestion guarantee for a field that can arrive "
        "clone-delivered either **names the route it holds for** (`theurian review "
        "ingest` landed it, or a clone delivered it), or **says what the read "
        "actually checks** -- shape and the derived path, never authorship. Then "
        "record the new count in `_JUDGED_CENSUS` in the same commit, so the next "
        "reader can see it was judged rather than absorbed."
    )


# -- and the key's own branches, held one at a time ----------------------------


def _key_branches(pattern: str) -> tuple[str, ...]:
    """*pattern* split on its top-level ``|``, each branch as it is written.

    Derived rather than transcribed, so the roster below is the shipped key's
    roster and not a second list somebody kept in step. A hand-written copy would
    agree with the key on the day it was written and then stop, which is exactly
    the drift the arms underneath exist to report.

    Depth-aware on three counts, and each of them is a silent mis-split rather
    than a loud one. A ``|`` inside a group belongs to that group
    (``(wrote|writes)``), a ``|`` inside a character class is a literal, and an
    escaped ``\\|`` is a literal too. Split naively and the roster fills with
    fragments like ``Theurian (itself )?(wrote`` -- which does not even compile,
    but a fragment that *does* compile would just count the wrong thing.

    Nothing is stripped from a branch: leading and trailing spaces are part of a
    regex, so trimming them would change what a branch matches while leaving the
    roster looking unchanged.
    """
    branches: list[str] = []
    current: list[str] = []
    depth = 0
    in_class = False
    escaped = False

    for char in pattern:
        if escaped:
            escaped = False
        elif char == "\\":
            escaped = True
        elif in_class:
            in_class = char != "]"
        elif char == "[":
            in_class = True
        elif char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
        elif char == "|" and depth == 0:
            branches.append("".join(current))
            current = []
            continue
        current.append(char)

    branches.append("".join(current))
    return tuple(branches)


#: What each branch of the key contributes on its own, measured 2026-09-11 over
#: the same unwrapped surfaces :func:`_authority_census` reads.
#:
#: **Why a per-branch floor and not the total.** The total says the key still
#: matches a lot of things. It does not say the key still matches *the things it
#: was built to match*. Measured by deleting each branch from the key in turn and
#: running this module as the commit before this one left it, **fifteen of the
#: nineteen go unnoticed**. Four are caught, and the total's own reasoning
#: accounts for exactly one of them: ``allowlist``, which alone carries 60 of the
#: 100 matches. The other three -- ``as the provider resolved``, ``landed by`` and
#: ``already ingested`` -- are caught incidentally, because
#: :data:`_WRAPPED_TERM_CASES` happens to plant those real terms, which is
#: coverage nobody chose and nobody would notice losing. A branch is what encodes
#: one spelling an over-claiming sentence reaches for, and a branch that has
#: stopped matching is a spelling nothing is watching any more.
#:
#: **Five of these stand at zero, and that is recorded rather than hidden**:
#: ``never received``, ``not received``, ``every record held``,
#: ``the adapter record`` and ``pseudonymised at landing``. The census that closed
#: this class rewrote the sentences they were written for, so their floors can
#: only be vacuous. They are kept because the key aims at the *next* sentence, not
#: only at today's, and the roster arm is what holds them: deleting one is RED
#: there even though it cannot be RED here.
#:
#: **These sum to 115 against the surface census's 100**, and the 15 is not a
#: discrepancy: ``allowlist`` is a substring of ``public-allowlisted``. The whole
#: key returns one match per position and prefers the longer branch where both
#: could start; measured branch by branch, those fifteen occurrences are counted
#: by each. Recorded so the next reader does not take the two totals for two
#: measurements of the same thing.
_JUDGED_BRANCH_FLOOR: Final[dict[str, int]] = {
    "at ingestion": 7,
    "Theurian (itself )?(wrote|writes)": 3,
    "written by Theurian": 1,
    "never received": 0,
    "not received": 0,
    "allowlist": 60,
    "secret scan": 6,
    "was visible to": 3,
    "public[- ]allowlisted": 15,
    "every record it holds": 2,
    "every record held": 0,
    "an author chose": 1,
    "provider's own": 7,
    "as the provider resolved": 2,
    "the adapter record": 0,
    "the ingestion (adapter|run|path|scan)": 4,
    "landed by": 3,
    "already ingested": 1,
    "pseudonymised at landing": 0,
}


def _branch_census() -> dict[str, int]:
    """Matches per top-level branch of the live key, over the same unwrapped text.

    Keyed by the branch as the key spells it, and derived from
    :data:`_OVER_CLAIM_TERMS` rather than from :data:`_JUDGED_BRANCH_FLOOR` --
    which is what makes deleting a branch visible. Ask each recorded branch
    directly instead and a branch removed from the shipped key would still be
    counted, by a regex only this module still holds.
    """
    texts = [_unwrapped(path.read_text(encoding="utf-8")) for path in _surface_files()]

    counted: dict[str, int] = {}
    for branch in _key_branches(_OVER_CLAIM_TERMS.pattern):
        compiled = re.compile(branch, re.IGNORECASE)
        counted[branch] = sum(1 for text in texts for _ in compiled.finditer(text))
    return counted


def test_the_census_key_carries_exactly_the_branches_that_were_judged() -> None:
    """RED means a spelling entered or left the key without anyone judging it.

    This is the arm that holds the five branches matching nothing today. Their
    floor below is zero and therefore vacuous, so without a roster they could be
    dropped from the key in a tidy-up and nothing in this module would notice --
    and the key would quietly stop watching for five of the spellings the closing
    census wrote it to watch for.

    It fails in both directions on purpose. A branch **gone** is a spelling no
    longer watched; a branch **arrived** is a spelling whose contribution nobody
    has measured, which is how a floor gets recorded at whatever the tree happened
    to hold rather than at what was judged.
    """
    live = set(_key_branches(_OVER_CLAIM_TERMS.pattern))
    recorded = set(_JUDGED_BRANCH_FLOOR)

    assert live == recorded, (
        "the T-24 census key's branches no longer match the roster that was judged:\n"
        + "".join(f"\n  GONE     {branch!r}" for branch in sorted(recorded - live))
        + "".join(f"\n  ARRIVED  {branch!r}" for branch in sorted(live - recorded))
        + "\n\nA GONE branch is a spelling the key has stopped watching for. Five of "
        "the recorded branches match nothing in the tree today and are kept for the "
        "next sentence rather than for this one, so this is the only arm that can "
        "say they went -- restore the branch, or record here why the spelling is no "
        "longer worth watching.\n\nAn ARRIVED branch has no measured contribution "
        "behind it. Run the branch over the authority surfaces, judge what it "
        "matches against T-24's control-table rule, and record the count in "
        "`_JUDGED_BRANCH_FLOOR` in the same commit."
    )


def _fallen(census: Mapping[str, int], floors: Mapping[str, int]) -> dict[str, tuple[int, int]]:
    """Every recorded branch contributing fewer matches than it was recorded with.

    ``census.get(branch, 0)`` rather than ``census[branch]``, and the default is
    the whole of what this helper is separately held to. The **loudest** way for a
    branch to fall below its floor is to be deleted from the key -- and a deleted
    branch is absent from the census, so indexing it raises a ``KeyError`` where
    this is supposed to report, and any default at or above the floor would make
    that loudest failure the one arm that says nothing.

    Its own case is :func:`test_a_branch_missing_from_the_census_is_reported_at_zero`,
    because no corpus this repository holds can make a recorded branch absent
    while the roster arm is green.
    """
    return {
        branch: (census.get(branch, 0), floor)
        for branch, floor in floors.items()
        if census.get(branch, 0) < floor
    }


def test_a_branch_missing_from_the_census_is_reported_at_zero() -> None:
    """RED means deleting a branch from the key goes past the floor arm in silence.

    The floor arm's default is unreachable over the live key: the roster arm holds
    the two rosters equal, so every recorded branch is measured and
    ``census.get``'s default never fires. That makes it exactly the kind of line
    that can be changed without any case noticing -- and changing it to anything
    at or above the floor turns a deleted branch from the arm's loudest finding
    into its quietest.

    Two directions, because a default that always reported would be the other
    defect: a branch that is present and at its floor must not be reported.
    """
    assert _fallen({}, {"at ingestion": 7}) == {"at ingestion": (0, 7)}, (
        "a branch the census never measured -- which is what a branch deleted from the "
        "key looks like from here -- was not reported as having fallen to zero"
    )
    assert _fallen({"at ingestion": 7}, {"at ingestion": 7}) == {}, (
        "a branch sitting exactly at its recorded floor was reported as fallen"
    )


def test_no_branch_of_the_census_key_stops_contributing_the_hits_it_was_recorded_with() -> None:
    """RED means one spelling of the over-claim went unwatched while the total held.

    The total is a poor guard on a key whose branches are this unevenly weighted.
    ``allowlist`` carries 60 of the 100 matches, so deleting any other branch
    moves the total by a few per cent at most -- inside the slack
    :func:`test_the_authority_surfaces_still_carry_the_sentences_the_key_was_built_for`
    deliberately leaves for prose to move in. Eighteen of the nineteen branches
    can be deleted with that premise still green; the sweep recorded beside
    :data:`_JUDGED_BRANCH_FLOOR` puts the number that went unnoticed by the whole
    module at fifteen.

    So each branch is held to what it was recorded contributing. Two different
    things redden it, and both are the same defect seen from either end: the
    branch was removed from the key, or the sentences it matched were reworded
    until it matched nothing. Either way a spelling the closing census decided was
    worth watching has stopped being watched.

    Direction matters and only one direction is held. Hits **above** the recorded
    floor are the surface census's business, which reports them per file as
    unjudged growth; hits below are this one's.
    """
    fallen = _fallen(_branch_census(), _JUDGED_BRANCH_FLOOR)

    assert not fallen, (
        "a branch of the T-24 census key contributes fewer matches than it was "
        "recorded contributing:\n"
        + "".join(
            f"\n  {branch!r}: {now} matches, {then} recorded"
            for branch, (now, then) in sorted(fallen.items())
        )
        + "\n\nEither the branch was narrowed or removed -- in which case the key has "
        "stopped watching for that spelling of the over-claim -- or the sentences it "
        "matched were reworded away. The second is usually good news and still has to "
        "be recorded: check that the rewording did not simply move the claim into a "
        "spelling no branch covers, then lower the floor in `_JUDGED_BRANCH_FLOOR` in "
        "the same commit, so the next reader sees a judged number rather than a "
        "shrinking one."
    )


#: A pattern with every shape :func:`_key_branches` has to split around, and the
#: branches it has to produce. Synthetic, because the shipped key happens to
#: carry no ``|`` inside a character class and no escaped one at all -- so a
#: splitter that mishandled either would agree with the roster today and split a
#: future branch wrong, silently.
_SPLITTER_CASE: Final = (
    r"plain|grouped (one|two)|nested ((a|b)|c)|class [a|b]|escaped \||last",
    (
        "plain",
        "grouped (one|two)",
        "nested ((a|b)|c)",
        "class [a|b]",
        r"escaped \|",
        "last",
    ),
)


def test_the_branch_splitter_keeps_alternations_inside_groups_and_classes_intact() -> None:
    """The premise under both branch arms: the roster has to be the real roster.

    :func:`_key_branches` is where a wrong answer is quietest. Split one branch
    too many and the roster arm reddens loudly on the next run, which is fine.
    Split one too *few* -- fold two spellings into a single branch -- and the
    roster still matches, the floors still add up, and one of the two spellings is
    no longer separately held by anything.

    Driven against a synthetic pattern rather than the shipped key, so the
    splitter is exercised on shapes the key does not carry today: a ``|`` inside a
    character class and an escaped ``|`` are both literals, and both are what a
    naive split gets wrong first.
    """
    pattern, expected = _SPLITTER_CASE

    assert _key_branches(pattern) == expected, (
        f"`_key_branches` split the control pattern into "
        f"{list(_key_branches(pattern))}, expected {list(expected)}.\n\n"
        f"Both branch arms read their roster from this function, so a wrong split "
        f"makes them agree about the wrong thing: `_JUDGED_BRANCH_FLOOR` would be "
        f"re-recorded against whatever it produced, and the spellings it merged or "
        f"tore apart would stop being held one at a time."
    )
