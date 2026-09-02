"""The records that describe the object-keyed census, held to what it is (#199 unit B).

``tools/audit/`` is a repository tool rather than shipped code, and three
governed documents describe it to a reader who will not run it: the core
changelog's entry on the sweep, the unit's work log, and the plugin changelog
entries the sweep's corrections landed in. Each of them states something the
tree can be asked about, and a claim about a tool nobody runs from prose is the
shape that rots first — which is the whole finding this unit exists to record.
Five prose sweeps in a row keyed on how a claim was *worded* and each missed a
live member; a sixth sweep keyed on a number nobody rechecks is the same failure
with a different key.

**What each pin here holds, and what it deliberately does not.**

- The **ledger count** is derived from the audit modules themselves, so a fifth
  ledger reddens the sentence that says there are four. It does not check that a
  ledger is *correct* — each audit's own ``--positive-control`` run drives that,
  in every direction the ledger has, and that is the check the changelog sentence
  beside this one is about.
- **``CONFIG_HOMES`` membership** is pinned as an exact set, because the work
  log's account of which instrument holds ``ingest.md``'s regression direction
  rests on that file *not* being a config home. Adding it would make the census
  reach the paragraph by the home rule and make the work log's stated mechanism
  false, in the direction that reads as a *strengthening* — which is why it
  needs a pin rather than a reviewer.
- The **plugin changelog corrections** are pinned as whole sentences. Their fact
  sides live elsewhere and are not duplicated here: that nothing reads
  ``providers.review.repositories`` is held by
  ``test_config_key_call_sites.py``'s reader scan, and which tracker issue owns
  FR-V5 is not a property of this tree at all. What these hold is the wording,
  which is what a reader gets, and the census holds only the reversion direction
  — a reword that never returns to the retracted universal moves nothing there.

The audit modules are read as **text and parsed**, never imported: they are not
part of the distribution, they sit outside ``mypy``'s package graph for the
tests, and importing one to read a constant would run its module body. Pure in
the sense the other structural tests here are — no database, no socket, no
temporary directory, and no subprocess.
"""

from __future__ import annotations

import ast
import pathlib
from typing import Final

import pytest

pytestmark = pytest.mark.unit

#: ``parents[4]`` is ``.../tests/unit/`` → ``tests`` → ``theurian-core`` →
#: ``packages`` → repo root, the same reckoning ``test_config_key_call_sites.py``
#: uses for the published schemas.
REPO_ROOT: Final = pathlib.Path(__file__).resolve().parents[4]

AUDIT_DIR: Final = REPO_ROOT / "tools" / "audit"
CORE_CHANGELOG: Final = REPO_ROOT / "packages" / "theurian-core" / "CHANGELOG.md"
CENSUS_WORK_LOG: Final = REPO_ROOT / "docs" / "work-logs" / "2026-09-02-199-unit-b-census.md"

#: The module-level names a census audit holds its ledger under.
#:
#: One name per audit rather than one shared name, because each ledger records a
#: different kind of row: a classified suspect, a prose-only discharge, a
#: verdict with an occurrence count. The set is the structural key for "how many
#: ledgers are there", and :data:`_LEDGER_PROSE_KEY` is the second key that
#: catches a fifth ledger arriving under a sixth name.
_LEDGER_NAMES: Final[frozenset[str]] = frozenset({"SUSPECTS", "PROSE_ONLY", "CLASSIFIED"})

#: The word every module carrying a ledger uses for it, in any case.
#:
#: A second key over the same population, and the reason it is worth its cost:
#: the structural key above can only see a ledger named after one of the three it
#: already knows. A sixth audit whose ledger is ``RECORDED`` would be invisible
#: to it and visible here, because this repository's own convention is to call
#: the thing a ledger in the module that carries one. The cost is the opposite
#: error -- a module that discusses ledgers without holding one -- and the
#: failure message says so rather than announcing a new ledger.
_LEDGER_PROSE_KEY: Final = "ledger"

#: Number words as the changelog spells them, index = value.
_NUMBER_WORDS: Final[tuple[str, ...]] = (
    "zero",
    "one",
    "two",
    "three",
    "four",
    "five",
    "six",
    "seven",
    "eight",
)

#: ``config_object_claims.CONFIG_HOMES``, as this record says it is.
#:
#: Files whose *subject* is the project config, so a bare pronoun inside them
#: resolves to it. The audit states this is a judgement rather than a
#: derivation, which is precisely why it needs an external pin: a judgement can
#: be widened by one line with nothing to notice.
CONFIG_HOMES: Final[tuple[str, ...]] = (
    "schemas/config/project-config.schema.json",
    "examples/sample-project/.theurian/config.yaml",
)


def _audit_modules() -> tuple[pathlib.Path, ...]:
    """Every module in ``tools/audit/``, sorted."""
    return tuple(sorted(AUDIT_DIR.glob("*.py")))


def _module_level_names(source: str, filename: str) -> frozenset[str]:
    """Every name bound at module level in ``source``, annotated or not."""
    bound: set[str] = set()
    for node in ast.parse(source, filename=filename).body:
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            bound.add(node.target.id)
        elif isinstance(node, ast.Assign):
            bound.update(target.id for target in node.targets if isinstance(target, ast.Name))
    return frozenset(bound)


def _string_tuple_constant(source: str, filename: str, name: str) -> tuple[str, ...]:
    """The value of module-level ``name``, a tuple of strings and string constants.

    One level of indirection is resolved, because ``CONFIG_HOMES``' first member
    is the module constant holding the schema path rather than a literal. Any
    other shape is an explicit failure: a pin that silently skipped a member it
    could not evaluate would report a shorter tuple as a match.
    """
    tree = ast.parse(source, filename=filename)
    literals: dict[str, str] = {}
    value: ast.expr | None = None
    for node in tree.body:
        target: str | None = None
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            target, assigned = node.target.id, node.value
        elif isinstance(node, ast.Assign) and len(node.targets) == 1:
            only = node.targets[0]
            if isinstance(only, ast.Name):
                target, assigned = only.id, node.value
        if target is None or assigned is None:
            continue
        if isinstance(assigned, ast.Constant) and isinstance(assigned.value, str):
            literals[target] = assigned.value
        if target == name:
            value = assigned

    assert isinstance(value, ast.Tuple), (
        f"`{name}` is not a module-level tuple in {filename}; this pin reads it "
        f"structurally and cannot check a shape it does not recognise."
    )

    resolved: list[str] = []
    for element in value.elts:
        if isinstance(element, ast.Constant) and isinstance(element.value, str):
            resolved.append(element.value)
        elif isinstance(element, ast.Name) and element.id in literals:
            resolved.append(literals[element.id])
        else:
            raise AssertionError(
                f"`{name}` in {filename} has a member this pin cannot evaluate "
                f"({ast.dump(element)}). Read the member by hand and widen this "
                f"reader; do not drop it, because a skipped member is a member "
                f"the pin stops holding."
            )
    return tuple(resolved)


def _number_word(value: int) -> str:
    """``value`` as the changelog spells it, or an explicit failure."""
    assert 0 <= value < len(_NUMBER_WORDS), (
        f"{value} ledgers is outside the range this pin can render as a word, so "
        f"the changelog sentence it builds cannot be checked. Widen "
        f"`_NUMBER_WORDS` and read the sentence again."
    )
    return _NUMBER_WORDS[value]


def test_the_changelog_counts_the_ledgers_the_audit_tools_actually_carry() -> None:
    """#199: "each of the four ledgers" is a count of code, so it is derived from code.

    The census's exactness claim is per ledger — an unclassified member and a row
    the sweep no longer produces are both exit 1 — and the changelog states it
    over a *number* of them. A fifth audit growing a ledger makes that sentence
    describe four fifths of the instrument while reading as though it described
    all of it, and nothing about the new audit's own tests would notice: they
    would test the new ledger, not the sentence counting them.

    Two keys, and both have to agree. The structural key reads each module's
    module-level bindings for a ledger name; the prose key reads which modules
    call the thing a ledger at all. A ledger arriving under a fourth constant
    name is invisible to the first and visible to the second, and the assertion
    that they match is what keeps the count honest against a rename.

    The count is rendered into the changelog's own sentence rather than compared
    as an integer, so the pin also fails if the sentence is reworded to claim
    something the ledgers do not do — "exact in both directions" is the property
    the number is attached to, and a number with the property edited off it would
    otherwise still match.
    """
    structural = {
        path.name
        for path in _audit_modules()
        if _module_level_names(path.read_text(encoding="utf-8"), path.name) & _LEDGER_NAMES
    }
    prose = {
        path.name
        for path in _audit_modules()
        if _LEDGER_PROSE_KEY in path.read_text(encoding="utf-8").lower()
    }
    changelog = " ".join(CORE_CHANGELOG.read_text(encoding="utf-8").split())

    assert structural == prose, (
        f"the two keys for 'which audits carry a ledger' disagree.\n\n"
        f"  named a ledger constant: {sorted(structural)}\n"
        f"  say 'ledger' in prose  : {sorted(prose)}\n\n"
        f"Only in the second: either a new ledger landed under a name "
        f"`_LEDGER_NAMES` does not know -- add it, and read the changelog "
        f"sentence below -- or a module discusses ledgers without holding one, "
        f"which is this key's known cost and is fixed by narrowing the wording. "
        f"Only in the first: a ledger with no prose saying what it is."
    )
    counted = (
        f"Each of the {_number_word(len(structural))} ledgers is exact in both "
        f"directions — a suspect no row covers and a row the sweep no longer "
        f"produces are both exit 1"
    )

    assert counted in changelog, (
        f"packages/theurian-core/CHANGELOG.md no longer states:\n\n  {counted}\n\n"
        f"Measured here: {len(structural)} audits carry a ledger "
        f"({sorted(structural)}). If an audit gained or lost one, the sentence "
        f"moves in the same commit -- and if the new ledger is not exact in both "
        f"directions, the sentence is the thing that has to change, not this pin."
    )


def test_the_work_log_names_the_config_homes_the_census_actually_has() -> None:
    """#461: the work log's mechanism paragraph is only true while ``ingest.md`` is not a home.

    ``config_object_claims`` reaches a claim about ``.theurian/config.yaml`` two
    ways: the block spells the path, or the document is a *config home* and a
    bare pronoun in it resolves to the file. The work log records — because the
    paragraph's first draft got this wrong — that ``ingest.md`` is reached by the
    named-path rule **alone**, so a drift dropping the path drops out of census
    reach and only the prose pin holds it.

    Adding ``plugins/claude-code/commands/ingest.md`` to ``CONFIG_HOMES`` is the
    change that falsifies that paragraph, and it is the shape nobody would flag:
    it reads as a strengthening, it makes the census catch *more*, and it leaves
    every audit exiting 0. What it actually does is make a recorded measurement
    of which instrument covers which direction wrong, and a later reader trusts
    that measurement when deciding whether the prose pin may be relaxed.

    The membership is asserted as an exact tuple, in order, so a home removed
    reddens as loudly as a home added. Removing the sample project would leave
    the pronoun rule reaching one surface while the audit's own docstring says it
    reaches the file's two homes.
    """
    audit = AUDIT_DIR / "config_object_claims.py"
    homes = _string_tuple_constant(audit.read_text(encoding="utf-8"), audit.name, "CONFIG_HOMES")
    work_log = " ".join(CENSUS_WORK_LOG.read_text(encoding="utf-8").split())
    mechanism = (
        "`CONFIG_HOMES` is the schema and `examples/sample-project/.theurian/config.yaml`, "
        "and nothing else; `ingest.md` is not in it, so the home rule never fires on this "
        "document. The census reaches this paragraph by the **named-path rule alone**"
    )

    assert homes == CONFIG_HOMES, (
        f"`config_object_claims.CONFIG_HOMES` is {list(homes)}, and this record "
        f"says {list(CONFIG_HOMES)}.\n\n"
        f"A home ADDED: every bare pronoun in that document now resolves to the "
        f"project config, so the census reaches claims it did not reach before. "
        f"That is a real widening and may be right -- but "
        f"docs/work-logs/2026-09-02-199-unit-b-census.md records which instrument "
        f"holds `ingest.md`'s regression direction on the basis that the home rule "
        f"does *not* fire there, so that paragraph is corrected in the same "
        f"change.\n\n"
        f"A home REMOVED: a surface whose subject is the config file no longer "
        f"resolves its pronouns, and `_PRONOUN_FILE`'s reason to exist moves with "
        f"it."
    )
    assert mechanism in work_log, (
        f"docs/work-logs/2026-09-02-199-unit-b-census.md no longer states:\n\n"
        f"  {mechanism}\n\n"
        f"That sentence is the measured answer to which of two instruments covers "
        f"`ingest.md`'s regression direction, and the paragraph above it records "
        f"that the first draft got it backwards. If `CONFIG_HOMES` moved, this "
        f"assertion and the one above it fail together and the work log is what "
        f"gets corrected."
    )


#: The two claims round one's M-j uncovered in the plugin changelog, as
#: ``(label, repo-relative path, the sentences that carry the correction)``.
#:
#: Both sat inside ``[Unreleased]``, which the census's release-note clear had
#: been treating as a dated record — so both were live prose that nothing read.
#: Scoping that clear to dated sections is what surfaced them, and this table is
#: what keeps them from drifting back once the next reader assumes the census has
#: them.
#:
#: **Whole sentences, not fragments**, which is round one's adv-L1 applied where
#: it is cheap: a changelog entry is written once and edited rarely, and the
#: failure these guard is a *reword* — a sentence that keeps the object spelled
#: and loses what it says about it. A fragment key would pass a rewording that
#: kept the fragment, which is the exact escape that put the retracted universal
#: here in the first place.
#:
#: The pin is not scoped to ``[Unreleased]``. A release cut moves these entries
#: into a dated section without changing a word, and a RED there would be noise
#: at the worst moment; what a release does change is their *status*, from a live
#: claim to a record, at which point a row may be retired with that reason on the
#: line.
PLUGIN_CHANGELOG_CORRECTIONS: Final[tuple[tuple[str, str, tuple[str, ...]], ...]] = (
    (
        "the retracted universal (#461, #426)",
        "plugins/claude-code/CHANGELOG.md",
        (
            # The entry ended "nothing reads that file, so the allowlist protects
            # no one yet" -- the file-wide universal ADR-0027 decision 3
            # falsified. The conclusion is still right, so it is re-derived on the
            # fact that holds: the negation names the key, and the sentence after
            # it says what the file *is* read for. Both are needed. The first
            # alone leaves a reader with no reason the file is not simply unread;
            # the second alone drops the warning the paragraph exists to deliver.
            (
                "It is not one: **nothing reads the `providers.review.repositories` "
                "allowlist**, so it protects no one yet."
            ),
            (
                "That file itself *is* read, for one key — `security.secretScan`, by "
                "`security/project_config.py` and nothing else (ADR-0027 decision 3) — "
                "and this entry said the file was unread until this branch narrowed it "
                "to the key, the same correction #461 made to `ingest.md` itself "
                "([#501](https://github.com/theurian/theurian/pull/501))."
            ),
        ),
    ),
    (
        "the FR-V5 owner repoint (#482, #479)",
        "plugins/claude-code/CHANGELOG.md",
        (
            # #129 closed COMPLETED on 2026-08-22 on the wording rather than on the
            # adapter, so it owned nothing afterwards. One sentence, because the
            # correction is one clause -- who owns FR-V5 now -- and the historical
            # position of the closed number is inside it: deleting #129 would lose
            # why the owner moved, which is the same treatment the schema's
            # `providers.review.repositories` description takes.
            (
                "It now states FR-V5 as owed with review ingestion, which is owned by "
                "[#479](https://github.com/theurian/theurian/issues/479) — this entry and "
                "the document both named "
                "[#129](https://github.com/theurian/theurian/issues/129) until it closed "
                "on the wording rather than on the adapter, and the document was "
                "repointed in [#482](https://github.com/theurian/theurian/pull/482) while "
                "this changelog was outside that pass's file set "
                "([#501](https://github.com/theurian/theurian/pull/501))."
            ),
        ),
    ),
)


@pytest.mark.parametrize(
    ("label", "relative_path", "sentences"),
    PLUGIN_CHANGELOG_CORRECTIONS,
    ids=[case[0] for case in PLUGIN_CHANGELOG_CORRECTIONS],
)
def test_each_plugin_changelog_correction_still_says_what_it_was_corrected_to_say(
    label: str, relative_path: str, sentences: tuple[str, ...]
) -> None:
    """#461, #482: the two corrections the census's release-note fix uncovered.

    Both entries were false in the same way the documents they describe were:
    one asserted a file-wide universal ADR-0027 decision 3 had falsified, the
    other named an issue that closed on the wording rather than on the control
    and so owned nothing. Both survived five sweeps because the census cleared a
    ``[Unreleased]`` section as though it were a dated record.

    That hole is closed, and the census now reads these entries — but only in one
    direction. It reddens on a **reversion to the retracted shape**, and a reword
    that never returns to that shape moves nothing there, exactly as it did for
    ``ingest.md`` itself. This is the other direction: the corrected wording,
    asserted whole, so a sentence that keeps the object spelled and loses what it
    says about the object is RED here.

    Spelling, and only spelling. That nothing reads
    ``providers.review.repositories`` is held by ``test_config_key_call_sites``'s
    reader scan; which issue owns FR-V5 is not a property of this tree. If either
    of those changes, the entry is what gets corrected, and this pin is what makes
    the correction land in the same commit.
    """
    normalized = " ".join((REPO_ROOT / relative_path).read_text(encoding="utf-8").split())

    for sentence in sentences:
        assert sentence in normalized, (
            f"{label}: {relative_path} no longer states:\n\n  {sentence}\n\n"
            f"This is a corrected claim, not a stylistic sentence. Before editing "
            f"this pin, settle which direction moved: if the tree changed, the "
            f"entry is corrected in that same change and the new wording goes "
            f"here; if it did not, the entry drifted and the entry is what gets "
            f"restored. The object-keyed census in `tools/audit/` holds only the "
            f"reversion direction, so a reword that never returns to the retracted "
            f"universal is green there and red here."
        )
