"""Pins what the GHSA-wwq9 record (0.5.2) says about the replay order, against the code.

Reads the `reorders` rule, the accept refusals, the residual counts, `MoveKind` and the census
marker where the docs, help texts, plugin and comments state them.
"""

# ruff: noqa: E501 -- the tables hold the exact words each site uses
from __future__ import annotations

import importlib.util
import inspect
import re
from collections import Counter
from pathlib import Path
from typing import Final, get_args

import pytest
from record_sentences import sentences
from threat_model_claims import SPELLED_NUMBERS, entry, entry_in, prose
from write_lock_claims import REPO_ROOT

from theurian import __version__
from theurian.application import proposal_service
from theurian.application.permissive_moves import MoveKind
from theurian.cli import commands

pytestmark = pytest.mark.unit

ROOT: Final = REPO_ROOT
_CORE, _SRC = "packages/theurian-core", "packages/theurian-core/src/theurian"
_TM, _MIG = "docs/security/threat-model.md", "docs/protocol/migrations.md"
_ADR = "docs/adr/0032-the-write-intent-mcp-tool-surface.md"
_PM, _CLI = f"{_SRC}/application/permissive_moves.py", f"{_SRC}/cli/commands.py"
_PLUGIN, _CHANGELOG = "plugins/claude-code/commands/migrate.md", f"{_CORE}/CHANGELOG.md"


def _read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def _flat(text: str) -> str:
    return prose(re.sub(r"(?m)^\s*(?:#:?|>) ?", "", text))


def _match(pattern: str, text: str) -> re.Match[str]:
    found = re.search(pattern, text)
    assert found, f"{pattern!r} not found"
    return found


_DOC = "before the migration|the field's largest-id writer|has a larger id than the migration's own|found the field at or above the level that writer left|its end leaves it below"
_HELP = "before the migration|the largest id to have written the field|is larger than its own|from at or above that id's level|to below it"
#: name | file | start marker | end marker | the P3..P7 phrases
# fmt: off
_SITES: Final = [
    f"T-28 control 4|{_TM}|4. **The permissive-move report.**|5. **`accept` never|written the field before it|the largest id to have written the field|any smaller-id migration that replays after|found the field at or above that id's level|left it below",
    f"migrations.md|{_MIG}|**`reorders` (GHSA-wwq9-p8wq-5m68).**|**`propose accept` never|before each migration replays|largest-id migration that has written it, by any label write, at the level it left it|a smaller-id migration that found|found the field at or above that level|and ends it below",
    f"PermissiveMove|{_PM}|``reorders`` is the other source|``before`` and|{_DOC}",
    f"_reordered|{_PM}|R2': |\"\"\"|before the migration|the attributed id|is larger|found the field at or above its level|ended below it",
    f"module docstring|{_PM}|``reorders`` when the field's|whatever operation|{_DOC}",
    f"validate help|{_CLI}|`reorders`, for any write, when the largest id|`undoes` names that id|{_HELP}",
    f"apply help|{_CLI}|`kind` is `reorders`, for any write this run applied,|`undoes` names that id|{_HELP}",
    f"plugin|{_PLUGIN}|`kind: reorders` means|Say that nothing|written the field before it|the largest id to have written the field|a larger one than its own|from at or above that id's level|to below",
    f"engine comment|{_SRC}/application/migration_engine.py|those a migration ended|(GHSA-v2qg|before the migration|the field's largest-id writer|that id being larger than its own|after finding it at or above that level|ended below the level",
    f"reorders test|{_CORE}/tests/integration/test_reorders_report.py|R1: |\"\"\"|before each migration replays|attributed to the largest-id migration that has written it|has a smaller id than the attributed one|found the field at or above the attributed level|and left it below",
]
# fmt: on
_CELLS = [
    pytest.param(f, f"P{3 + i}", phrase, id=f"{f[0]}-P{3 + i}")
    for f in (s.split("|") for s in _SITES)
    for i, phrase in enumerate(f[4:])
]


@pytest.mark.parametrize(("site", "cond", "phrase"), _CELLS)
def test_each_statement_of_the_reorders_rule_carries_its_conditions(
    site: list[str], cond: str, phrase: str
) -> None:
    """One condition at one site: deleting its phrase turns exactly its cell RED."""
    name, rel, start, end = site[:4]
    text = _read(rel)
    assert text.count(start) == 1, f"{name}: {start!r} occurs {text.count(start)} times"
    region = _flat(start + text.split(start, 1)[1].split(end, 1)[0])

    assert _flat(phrase) in region, f"{name} lost {cond}: {phrase!r} not in {region!r}"


def _texts(roots: tuple[str, ...], suffixes: set[str]) -> dict[str, str]:
    """The Core CHANGELOG and every file under *roots* with one of *suffixes*, by repo-relative path."""
    paths = [p for r in roots for p in (ROOT / r).rglob("*") if p.suffix in suffixes]
    return {p.relative_to(ROOT).as_posix(): p.read_text(encoding="utf-8") for p in paths} | {
        _CHANGELOG: _read(_CHANGELOG)
    }


def _population(key: re.Pattern[str], *, flat: bool = False) -> dict[str, int]:
    texts = _texts(("docs", _SRC, "plugins"), {".md", ".py"})
    counts = {rel: len(key.findall(_flat(t) if flat else t)) for rel, t in texts.items()}
    return {rel: n for rel, n in sorted(counts.items()) if n}


def test_the_largest_id_rule_is_stated_only_where_the_drift_pin_reads_it() -> None:
    """Measured 2026-10-09 with `git grep -c -i -E 'largest[- ]id' -- docs packages/theurian-core/src/theurian plugins packages/theurian-core/CHANGELOG.md`.
    A new statement using `largest[- ]id` is a new site to pin; a reworded one outside the key is not caught."""
    expected = {
        _MIG: 1,
        _TM: 1,
        f"{_SRC}/application/migration_engine.py": 1,
        _PM: 3,
        _CLI: 2,
        _PLUGIN: 1,
    }

    assert _population(re.compile(r"largest[- ]id", re.I)) == expected


_UNDOES = "would make a row the history already holds name a different migration in undoes"
_MOVES = "changes which migration a row already there says it undoes"
#: file | the add half | the re-attribution half, in the words each site uses
# fmt: off
_REFUSALS: Final = [
    "docs/adr/0027-accept-validates-before-it-moves.md|neither adds a row|nor changes which migration a held row says it undoes",
    f"{_ADR}|would add a row to that report|or re-attribute one it already holds",
    f"{_MIG}|adds or re-attributes no report row|re-attributes",
    f"{_TM}|it refuses both a new row|a held row whose undoes the proposal would change",
    f"{_MIG}|would add a row the landed migrations alone do not report|{_UNDOES}",
    f"{_CHANGELOG}|would add a report row for that landed migration|{_UNDOES}",
    f"{_CHANGELOG}|would add a reorders row the landed migrations alone do not report|would make one the history already holds name a different migration in undoes",
    f"{_CHANGELOG}|would add a row to that report|{_UNDOES}",
    f"{_CHANGELOG}|would add a row to the permissivemoves report|make a held row name a different migration in undoes",
    f"{_SRC}/application/proposal_service.py|would add or re-attribute a row of the union's|re-attribute",
    f"{_SRC}/application/proposal_service.py|would add or re-attribute a permissive-move report row|re-attribute",
    f"{_SRC}/cli/propose_commands.py|neither adds a row to the replay's|nor changes which migration a row",
    f"{_SRC}/cli/propose_commands.py|would add a row to the report theurian migrate validate prints|or change which migration a row there says it undoes",
    f"plugins/claude-code/CHANGELOG.md|adds a row to the report|{_MOVES}",
    f"plugins/claude-code/commands/propose.md|adds a row to the report|{_MOVES}",
]
# fmt: on
#: Not statements of when accept refuses: a recounted finding, an ADR how-to, and one released 0.5.2 CHANGELOG line naming a single half.
_NOT_REFUSALS: Final = {_ADR: 1, "docs/adr/README.md": 1, _CHANGELOG: 1}
#: The key: an add-half wording, in the flattened text of docs/, src/theurian, plugins/ and the Core CHANGELOG.
_ADD_HALF = re.compile(
    r"\b(?:would add|neither adds|to add|adds or re-attributes|adds? (?:a|an))\b(?: or re-attribute)?[^.;]{0,25}?\brows?\b|\bwould add or re-attribute\b|\brows?\b[^.;]{0,25}\bwould add\b"
)  # fmt: skip


@pytest.mark.parametrize("site", _REFUSALS)
def test_each_report_row_refusal_carries_both_halves_of_the_published_invariant(site: str) -> None:
    """core-v0.5.1: accept "refuses a proposal whose replay would add a row to that report, or
    would make a row the history already holds name a different migration in `undoes`"."""
    rel, add, second = site.split("|")
    text = _flat(_read(rel))

    assert add in text, f"{rel}: {add!r}"
    assert second in text[text.index(add) : text.index(add) + len(add) + len(second) + 120]


def test_the_published_invariant_stands_verbatim_and_no_add_half_is_unclassified() -> None:
    """Every hit of `_ADD_HALF` is a pinned refusal or a recorded non-refusal; a refusal worded outside it is not caught."""
    keyed = (s.split("|") for s in _REFUSALS)
    expected = Counter(f[0] for f in keyed if _ADD_HALF.search(f[1])) + Counter(_NOT_REFUSALS)

    assert (
        f"it refuses a proposal whose replay would add a row to that report, or {_UNDOES}"
        in _flat(_read(_CHANGELOG))
    )
    assert _population(_ADD_HALF, flat=True) == dict(sorted(expected.items()))


def _row(threat_id: str) -> str:
    return _flat(_match(rf"(?m)^\| {threat_id} \|.*$", _read(_TM))[0])


#: A residual leaves the open count by being marked closed or fixed in a version.
_CLOSED: Final = re.compile(r"\*\*(?:Closed|Fixed) in")


def _residuals(threat_id: str, stop: str) -> list[str]:
    block = entry_in(_read(_TM), threat_id).split("**Residuals.**", 1)[1].split(stop, 1)[0]
    return re.split(r"(?m)^\d+\. ", block)[1:]


def test_the_residual_counts_agree_with_the_numbered_lists() -> None:
    t28, t29 = _residuals("T-28", "**A known cost"), _residuals("T-29", "**The same root cause")
    moved = [i for i, r in enumerate(t28, 1) if r.startswith("**Moved to T-29")]
    closed = [i for i, r in enumerate(t28, 1) if _CLOSED.match(r)]
    header = _match(
        r"all but the (\w+) are open in 0\.5\.3, the (\w+) new in it",
        _flat(entry_in(_read(_TM), "T-28")),
    )
    row = _match(
        r"(\w+) open residuals and (\w+) closed, recorded in the entry, (\w+) of the open ones by pointer to t-29",
        _row("T-28"),
    )
    ordinal = {"ninth": 9, "tenth": 10, "eleventh": 11, "twelfth": 12}
    n = SPELLED_NUMBERS

    assert (moved, closed) == ([6, 9], [10])
    assert [ordinal[w] for w in header.group(1, 2)] == [closed[0], len(t28)]
    assert [n[w] for w in row.groups()] == [len(t28) - len(closed), len(closed), len(moved)]
    open29 = len([r for r in t29 if not _CLOSED.match(r)])
    assert n[_match(r"all (\w+) are open", _flat(entry_in(_read(_TM), "T-29")))[1]] == open29
    assert n[_match(r"(\w+) open residuals", _row("T-29"))[1]] == open29


def test_the_moved_residuals_and_their_dependson_qualifier_survive_the_move() -> None:
    t28, t29 = _residuals("T-28", "**A known cost"), _residuals("T-29", "**The same root cause")
    for number, item in enumerate(t28, 1):
        if item.startswith("**Moved to T-29"):
            target = int(_match(r"its residual (\d+)", item)[1])
            assert f"t-28 residual {number} until this entry" in _flat(t29[target - 1])

    assert "absent dependson" in _flat(t29[1])
    assert "with a dependson, see t-28 residual 5 and reorders" in _flat(t29[1])


def test_every_move_kind_is_named_where_a_reader_looks_for_kinds() -> None:
    """Each value is keyed on its kind context: `undoes` is also a field name."""
    texts = {
        "plugin": _read(_PLUGIN),
        "validate help": commands.migrate_validate.__doc__ or "",
        "apply help": commands.migrate_apply.__doc__ or "",
    }
    for value in get_args(MoveKind):
        assert re.search(rf"(?m)^\| `kind` \|.*`{value}`", _read(_MIG)), f"kind row lacks {value}"
        for name, text in texts.items():
            assert re.search(
                rf"`kind(?:` is `|: ){value}`|`{value}` otherwise", " ".join(text.split())
            ), f"{name}: {value}"


def test_the_adr_marker_for_loosens_agrees_with_the_census() -> None:
    """ADR-0032's 2026-10-08 amendment says `_loosens` is registered as a writer, as the census has it."""
    spec = importlib.util.spec_from_file_location(
        "census", Path(__file__).parent / "test_gate_call_sites.py"
    )
    assert spec and spec.loader
    census = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(census)
    site = ("application/permissive_moves.py", "_loosens")
    start = "**Amended 2026-10-08:** since GHSA-wwq9-p8wq-5m68 `_loosens`"
    amended = _flat(start + _read(_ADR).split(start, 1)[1].split("An item absent", 1)[0])

    assert site in census.STATUS_GATE_WRITER_SITES
    assert site not in census.STATUS_GATE_READER_SITES
    assert "status_gate_writer_sites" in amended
    assert "status_gate_reader_sites" not in amended


def test_a_version_a_residual_closes_in_is_shipped() -> None:
    """T-28 residual 10 is "Closed in 0.5.2"; every closed residual of T-28 and T-29 must name a
    version with a CHANGELOG heading, at or below the package's."""
    closed = [
        item
        for threat_id, stop in (("T-28", "**A known cost"), ("T-29", "**The same root cause"))
        for item in _residuals(threat_id, stop)
        if _CLOSED.match(item)
    ]
    headings = re.findall(r"(?m)^## \[(\d+\.\d+\.\d+)\]", _read(_CHANGELOG))
    shipped = [int(x) for x in re.findall(r"\d+", __version__)]

    assert closed
    for item in closed:
        version = _match(r"(?:closed|fixed) in (\d+\.\d+\.\d+)", _flat(item))[1]
        assert version in headings, version
        assert [int(x) for x in version.split(".")] <= shipped, version


def test_the_changelogs_quoted_refusal_messages_match_the_templates_that_produce_them() -> None:
    """Pins released quotes against live templates: the fragments between each quote's
    `<placeholders>` are literals of its function. A template change retires this test in that
    change, because released notes are not amended."""
    quotes = re.findall(
        r'"(accepting this proposal would (?:make|let)[^"]*)"', _flat(_read(_CHANGELOG))
    )
    for fn, key in (
        ("_refuse_a_reported_upsert", "undoing what this proposal sets"),
        ("_refuse_a_landed_overwrite", "loosen what it sets"),
    ):
        source = inspect.getsource(getattr(proposal_service, fn))
        source = source.replace("{landed}", _match(r'landed = "([^"]*)" if', source)[1])
        source = re.sub(r'\{[^}]*\}|\bf"|"', "", source)
        template = " ".join(_flat(source).replace("'", "").split())
        found = [q for q in quotes if key in q]
        assert found, key
        for quote in found:
            for fragment in re.split(r"<[^>]*>|…", quote):
                assert " ".join(fragment.replace("'", "").split()) in template, (fn, fragment)


def _between(raw: str, start: str, end: str) -> str:
    assert raw.count(start) == 1, f"{start!r} occurs {raw.count(start)} times"
    return raw.split(start, 1)[1].split(end, 1)[0]


def _wwq9_amendment() -> str:
    return _between(
        _read(_ADR), "**Amended by GHSA-wwq9-p8wq-5m68 (2026-10-08).**", "**Amended by GHSA-fjqq"
    )


def _items(block: str) -> list[str]:
    return [_flat(item) for item in re.split(r"(?m)^\d+\. ", block)[1:]]


def _actions() -> list[str]:
    return _items(_between(entry("T-29"), "**Operator actions.**", "**What would reopen"))


def test_the_repair_rule_names_the_last_writer_in_applicationorder_and_says_to_read_it() -> None:
    """A repair depending on the row's migration alone left a chain loose; a reader told "the
    row's `migrationId` is enough" repeats that."""
    repair = _actions()[1]

    assert (
        "read the last migration in migrate validate's applicationorder that writes the row's field: often the row's migrationid, but read it, never assume it."
        in repair
    )
    assert (
        "otherwise merge a new migration that sets the row's before again and declares dependson on that migration."
        in repair
    )
    assert "dependson: [<the row's migrationid>]" not in repair
    assert "is enough" not in repair


def test_action_two_decides_leave_it_from_applicationorder_and_the_rows_before() -> None:
    """`knowledge.get` answers jointly over status and sensitivity against one ceiling, so it is
    no evidence of the row's own field
    (`test_reorders_report.py::test_a_withheld_item_is_no_evidence_of_the_rows_field_level`);
    the guard reads the writer the repair rule already reads, against the row's `before`."""
    repair = _actions()[1]

    assert (
        "if it sets that field at or above the row's before (for status, retires the item), leave it."
        in repair
    )
    assert (
        "do not decide this by knowledge.get: it withholds an item for either field, and at any level above the ceiling."
        in repair
    )
    assert repair.index("applicationorder") < repair.index("leave it") < repair.index("otherwise")


#: Words that make a sentence a decision to repair or to leave a row alone.
_DECISION = re.compile(r"leave it|repair|re-apply|still serves|already withholds")
#: The opening of T-29 action 2 as it stood at 990cdf12 (`git show 990cdf12:docs/security/threat-model.md`),
#: before 754675ec read the guard from applicationOrder; cut after the guard's two sentences.
_990C_GUARD: Final = """\
2. To repair a `reorders` row whose `undoes` set the level meant to hold: if
   `knowledge.get`, called at the default ceiling, with
   `includeUnapproved: true`, already withholds the
   item, leave it: a later migration holds a tighter label. If it still
   serves the item, merge a new migration that sets that level again
"""


def _knowledge_get_decisions(actions: list[str]) -> list[str]:
    return [
        sentence
        for action in actions
        for sentence in sentences(action)
        if "knowledge.get" in sentence and _DECISION.search(sentence)
    ]


def test_no_operator_action_pairs_knowledge_get_with_a_repair_decision() -> None:
    """Key: a sentence of T-29's operator actions naming `knowledge.get` and one of `_DECISION`.
    A decision worded without those terms, or split across two sentences, is not caught."""
    assert _knowledge_get_decisions(_actions()) == []


def test_the_knowledge_get_decision_key_hits_the_guard_that_stood_at_990cdf12() -> None:
    """Positive control: `_990C_GUARD` fed to the same key yields its guard sentence."""
    [hit] = _knowledge_get_decisions(_items(_990C_GUARD))

    assert "already withholds the item, leave it" in hit


def test_the_confirmation_says_what_knowledge_get_shows_and_what_it_does_not() -> None:
    """Without `includeUnapproved: true` an item an in-place withdrawal left `draft` reads as
    withheld whether or not the repair held (`test_reorders_report.py::test_a_repair_confirmation_needs_include_unapproved_after_an_in_place_withdrawal`),
    and the report keeps naming the row after a repair, so neither it nor the label confirms one."""
    repair = _actions()[1]

    assert (
        "confirm after migrate apply, never by the report: in a fresh migrate validate, no migration that writes the field follows the repair in applicationorder, which shows the replay order, not the level served."
        in repair
    )
    assert (
        "knowledge.get at the default ceiling, with includeunapproved: true, shows only whether the item is served there, not which field withholds it or at what level."
        in repair
    )
    for stale in ("for a status repair", "the item's label", "no longer reported"):
        assert stale not in repair


def test_action_three_names_every_landed_writer_in_replay_order() -> None:
    """0.5.3's remedy names every landed writer of a refused field that replays after the
    proposal's (GHSA-fjqq-grr7-53cc), not the one that last took the field."""
    actions = _actions()

    assert (
        "every landed migration that writes a refused field and replays after the proposal's"
        in actions[2]
    )
    assert "in replay order" in actions[2]
    assert not [a for a in actions if "expect a second refusal" in a or "last took the field" in a]


def _unlink(text: str) -> str:
    """*text* with Markdown links reduced to their label, so a linked `#929` reads as `#929`."""
    return re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)


def test_action_three_states_the_removal_cure_the_floor_now_prints() -> None:
    """Before #929 the floor's remedy said nothing of the removal and the record sent the reader
    to a tracker; #929 prints the cure, so the record states what it names, including the body
    files only the removed operations use, and where the measured shapes are."""
    action = _actions()[2]

    for sentence in (
        "remove every operation whose itemid is that item from the migration file, delete the body files only those operations use inside the proposal's directory, and accept again, or delete the proposal if those were all its operations.",
        "the floor's remedy names those operations, any such body file at a plain path inside that directory, and the directory, or the directory alone when a name would be redacted or cut or a body path is not a plain path inside it (the migration format defines plain).",
        "t-28 residual 11 records the shapes measured; on this route, the one measured is the proposal's own item behind a landed tightener (test_floor_remedy_reaccept.py::test_a_reaccept_the_floor_refuses_is_cured_by_the_printed_removal)",
    ):
        assert sentence in _unlink(action), action
    for stale in (
        "the floor's remedy does not say so",
        "tracks the fix",
        "remove that item's operation",
        "remove every operation on that item",
        "names those operations and the proposal's directory.",
        "the proposal's own item and a co-carried one",
        "delete the body files only those operations use, and",
        "on this route, the proposal's own item behind",
    ):
        assert stale not in action


def test_residual_eleven_states_the_kind_routed_floor_and_the_measured_cure() -> None:
    """The residual used to say the cure was "printed nowhere", that it "leaves the item withheld"
    and that the own item "is accepted without reaching the floor"; the cure leaves each item at
    its landed label, which one landed internal and approved serves, and two shapes of the own
    item do reach the floor. A reader acting on the old words would be told no cure exists, or
    that following it is safe for every label."""
    raw = _unlink(_flat(_residuals("T-28", "**A known cost")[10]))

    for sentence in (
        "since #929, after 0.5.3, the floor routes its remedy by what the proposal carries",
        "a proposal of createitem and upsertrevision alone keeps the content path's remedy.",
        "any other is told to remove each operation whose itemid is an item the refusal names from its own migration file, delete any body file the remedy lists, and accept again, or to delete the proposal directory if those are all its operations, if _one_name, the helper the refusal messages use, would redact or cut an id or body path the cure would name, or if a body path is outside _printable_body_path or resolves outside that directory",
        "followed literally, the cure leaves each item it names at its landed label, and that label decides whether it is served: one landed internal and approved is served at the default ceiling",
        "in each suite test cited next, the item the cure names ends confidential or deprecated, and the test asserts it withheld.",
        "refused by the lowering floor at a re-accept",
        "with the tightener's id newer than the draft the re-accept exits 0 without reaching a floor; older, it is refused by the lowering floor",
        "behind an older tightener declaring no dependson, the lowering floor refuses the first accept, run without a kept raise",
        "a refusal, not a disclosure.",
    ):
        assert sentence in raw, sentence
    for stale in (
        "printed nowhere",
        "to be fixed next",
        "no cure was measured",
        "tracked on its own issue",
        "remove every operation on each item",
        "that cure leaves the item the floor names withheld",
        "accepted without reaching the floor and stays withheld",
        "the refusal's message redacts or cuts",
        "is withheld in each shape measured",
        "declaring dependson on a landed revision",
    ):
        assert stale not in raw


def test_the_severity_premise_grounds_residual_one_on_the_adr_decision_not_on_a_merge() -> None:
    """Residual 1's status face is reached with both migrations accepted, so "residuals 1 to 3
    need a human to merge the migrations without `accept`" was false for it; its High is
    ADR-0032's decision to report rather than refuse. Residual 4 is reached through `accept` too."""
    severity = _GRADE_SITES["severity paragraph"]()

    assert (
        "records the decision to report it rather than refuse it; that decision holds residual 1 at high."
        in severity
    )
    assert (
        "its status face is reached with both migrations accepted, each on a base without the other:"
        in severity
    )
    assert (
        "its sensitivity face was not reached that way: two theurian propose updates conflict on expectedrevision."
        in severity
    )
    assert "residuals 2 and 3 need a hand-authored migration, which runs no accept." in severity
    assert (
        "residual 4 is reached through accept too, with a hand-edited dependson, and discloses nothing there"
        in severity
    )
    assert "need a human to merge" not in severity


_GRADE_SITES: Final = {
    "heading": lambda: _flat(_match(r"(?m)^#### T-29 .*$", _read(_TM))[0]),
    "summary row": lambda: _row("T-29"),
    "severity paragraph": lambda: _flat(
        _between(entry("T-29"), "**Severity:", "**Controls (0.5.2)")
    ),
}


@pytest.mark.parametrize("site", _GRADE_SITES)
def test_t_29_is_graded_critical_as_shipped_and_high_after_0_5_2_at_each_site(site: str) -> None:
    """The grade is read at the heading, the summary row and the Severity paragraph; one site
    drifting to another grade leaves a reader the wrong one depending on where they land."""
    text = _GRADE_SITES[site]()

    assert "critical as shipped (ghsa-wwq9-p8wq-5m68)" in text
    assert "high after 0.5.2" in text


def test_every_t_29_residual_is_stated_open_in_both_releases() -> None:
    residuals = _flat(_between(entry("T-29"), "**Residuals.**", "1. **"))

    assert residuals.startswith("all four are open in 0.5.2 and 0.5.3.")


def test_residual_three_says_the_larger_id_replay_can_end_with_no_row() -> None:
    """The no-row claim is held at its own site: it is an existential over the operations
    measured in P's place, not a statement about any loosening."""
    [_, _, third, _] = _residuals("T-29", "**The same root cause")

    assert _flat(third).startswith(
        "a changesensitivity lowering or restoreitem with a larger id than a migration merged after it can replay last, with no row."
    )


def test_residual_four_says_a_tightening_inversion_can_be_neither_refused_nor_reported() -> None:
    """Through `accept` with a hand-edited `dependsOn` it is accepted, so the claim is
    existential and carries its accept face."""
    [_, _, _, fourth] = _residuals("T-29", "**The same root cause")
    fourth = _flat(fourth)

    assert fourth.startswith("a tightening inversion can be neither refused nor reported:")
    assert "with dependson on the root hand-edited into the staged file it exits 0" in fourth


def test_the_t_29_summary_row_names_only_the_larger_id_lowering_it_measured() -> None:
    """The row states the operations measured in P's place; "any loosening" was not measured."""
    row = _row("T-29")

    assert "a changesensitivity lowering or restoreitem replaying last by its larger id" in row
    assert "and any loosening with a larger id" not in row


def test_control_two_points_at_where_the_larger_id_refusal_is_measured() -> None:
    """Control 2 once predicted which check meets a landed write first ("control 5 has the
    row"); the prediction was stated where it was not measured and was false for a restating
    history."""
    controls = _between(entry("T-29"), "**Controls (0.5.2).**", "**What holds it.**")
    second = _items(controls)[1]

    assert "is stated where it was measured: t-28 control 5 and residual 10" in second
    assert "has the row" not in second and "has no row" not in second


def test_the_adr_names_the_tightening_inversion_without_the_no_row_wording() -> None:
    """The amendment points at T-29 residual 4 for what each history ends at and reports."""
    amendment = _flat(_wwq9_amendment())

    assert (
        "a dependson migration that tightens a label can replay after a larger-id changesensitivity lowering or restoreitem, and reorders covers the loosening direction only. t-29 residual 4 records the histories measured"
        in amendment
    )
    assert "wins with no row" not in amendment


# Verbatim from the GHSA-wwq9 record as it stood at e8c7be23, the head of #901 before its wave
# reworded these sites (`git show e8c7be23:docs/security/threat-model.md`, lines 7023-7029 and
# 7075-7087; `e8c7be23:docs/adr/0032-the-write-intent-mcp-tool-surface.md`, lines 1265-1272).
# Embedded because a squash-merge leaves that commit reachable only through the PR ref.
_E8C7_CONTROL_2: Final = """\
2. **The end-state refusal, at `accept`, after control 5**
   (`_refuse_a_landed_overwrite`, with
   `application/permissive_moves.py :: loosened_after`): T-28 residual 10.
   It also refuses where control 5 has no row, as for a landed
   `changeSensitivity` lowering or `restoreItem` with a larger id than the
   proposal's (of `application/permissive_moves.py :: LabelWrite`, the two
   that can loosen); for an `upsertRevision` there, control 5 has the row.
"""
_E8C7_RESIDUAL_3: Final = """\
3. **A `changeSensitivity` lowering or `restoreItem` with a larger id than a
   migration merged after it replays last, with no row.** P (id 100), merged
   after L (id 300), raises an `internal` item to `confidential`, and L, a
   `changeSensitivity`, lowers it to `internal`, neither
   declaring `dependsOn`: they replay P, L, the item ends `internal`, and
   `permissiveMoves` is `[]`, since L is simply the later id and there is no
   inversion for `reorders` to see. With X (id 200, `dependsOn` the root)
   restating `confidential` and L declaring `dependsOn` on X, they replay P,
   X, L, with the same end and `[]`; if L declares none, X replays last and
   the item ends `confidential`. The status face, a deprecation undone by a
   `restoreItem`, ends `approved` with `[]` both ways, and `deprecated` when L
   declares none. Measured on main at 8d7e2f09 on 2026-10-08, through the CLI.
   `test_reorders_report.py::test_a_loosening_with_a_larger_id_replays_last_with_no_row` holds the three histories, both faces, at `[]` and those ends.
"""
_E8C7_ADR_PARAGRAPH: Final = """\
> **The tightening inversion is the recorded cost of keeping the order.**
> `reorders` is loosening-only, so a `dependsOn` migration that tightens a
> label after a larger-id `changeSensitivity` lowering or `restoreItem`
> (`application/permissive_moves.py :: LabelWrite`) wins with no row and no
> refusal. Nothing
> is disclosed; a reviewed declassification or readmission is lost silently.
> T-29 residual 4 records it, measured. It is the clearest argument for (C),
> whose order follows ids in both directions wherever `dependsOn` allows.
"""


#: The key: a sentence naming one check (`control 5`, `report check`, `end-state refusal`) and
#: a speaks-first verb. It reads the flattened sentences of docs/, plugins/ and the Core
#: CHANGELOG; a prediction worded with neither term (or in src/) is not caught.
_CHECK_NAME = re.compile(r"control 5|report check|end-state refusal")
_SPEAKS_FIRST = re.compile(r"has the row|speaks|refuses first|runs first|meets it first")
#: file | sentence start | a condition the sentence must keep stating | the ruling
_PREDICTIONS_RULED: Final = [
    f"{_TM}|accept now refuses it on the field's end state|when the union replay leaves a status or sensitivity the proposal wrote looser|every earlier check refuses a different cause: the union replay a revision conflict; the floors a lowering or readmission the proposal itself writes, which in residual 10's shape — the proposal raises or withdraws the field — cannot be this item's",
    f"{_TM}|the advisory's face meets it first|builds the advisory's history for both faces|T-29 control 1: the prediction is for the advisory's history, and the cited test builds it",
]


def _predictions(texts: dict[str, str]) -> list[tuple[str, str]]:
    return [
        (rel, sentence)
        for rel, text in texts.items()
        for sentence in sentences(_flat(text))
        if _CHECK_NAME.search(sentence) and _SPEAKS_FIRST.search(sentence)
    ]


def _unruled(hits: list[tuple[str, str]]) -> list[tuple[str, str]]:
    ruled = [r.split("|") for r in _PREDICTIONS_RULED]
    return [
        (rel, sentence)
        for rel, sentence in hits
        if not any(
            rel == f and sentence.startswith(start) and condition in sentence
            for f, start, condition, _ in ruled
        )
    ]


def test_every_sentence_predicting_which_check_speaks_first_is_ruled_with_its_conditions() -> None:
    """T-29 control 2 said "for an `upsertRevision` there, control 5 has the row" with no
    condition, and it was false for a history that restates a label. A prediction of which check
    meets a landed write first holds only on the histories it states, so each one in the
    docs is listed with the condition it keeps stating; a new or unconditioned one is RED."""
    hits = _predictions(_texts(("docs", "plugins"), {".md"}))

    assert _unruled(hits) == []
    assert len(hits) == len(_PREDICTIONS_RULED), "an allowlist entry matches no sentence"


def test_the_prediction_key_hits_the_clause_that_escaped_it_at_e8c7be23() -> None:
    """Positive control: `_predictions` fed T-29 control 2 as it stood hits the clause, unruled."""
    hits = _unruled(_predictions({_TM: _E8C7_CONTROL_2}))

    assert len(hits) == 1
    assert "for an upsertrevision there, control 5 has the row" in hits[0][1]


_ROW_OUTCOME = re.compile(
    r"no row|no report|reports nothing|\[\]|is not reported|neither refused nor reported"
)
_TEST_CITE = re.compile(r"::test_")
#: Entry-qualified only: a bare `control 5` or `residual 4` names a sibling in the same entry,
#: which is how the e8c7be23 control 2 sentence used it.
_POINTER = re.compile(r"\bt-2\d\b")
_EXISTENTIAL = re.compile(r"\bcan\b")
_HISTORY = re.compile(r"\bends?\b")
_MEASURED = re.compile(r"measured on .{1,60}? on 20\d\d-\d\d-\d\d")
#: sentence start | why it is not anchored by a rule above
_NO_ROW_HISTORY: Final = [
    "on an internal item, a landed changesensitivity to confidential|0.5.1 behaviour, measured on core-v0.5.1 (the paragraph)",
    "the landed lowering is a sanctioned operation|0.5.1 behaviour: 0.5.1 never reported it",
    "a merge approves only what its diff shows|0.5.1 behaviour: 'in 0.5.1' is in the sentence",
]


def _no_row_unanchored(raw: str) -> list[str]:
    stripped = re.sub(r"(?m)^\s*(?:#:?|>) ?", "", raw)
    unanchored = []
    for unit in re.split(r"\n\s*\n|\n(?=\d+\. )", stripped):
        found = sentences(_flat(re.sub(r"^\s*\d+\. ", "", unit)))
        cited = bool(_TEST_CITE.search(_flat(unit)))
        for sentence in found:
            if not _ROW_OUTCOME.search(sentence):
                continue
            anchored = (
                _TEST_CITE.search(sentence)
                or _POINTER.search(sentence)
                or (_EXISTENTIAL.search(sentence) and cited)
                or (cited and _HISTORY.search(sentence) and _MEASURED.search(_flat(unit)))
            )
            if not anchored:
                unanchored.append(sentence)
    return unanchored


def test_every_sentence_stating_a_row_outcome_in_t_29_and_the_adr_is_anchored() -> None:
    """The no-row wording sat unanchored at three sites and was false at two: a sentence
    `_ROW_OUTCOME` matches in T-29 or in ADR-0032's GHSA-wwq9 amendment must (a) cite a `::test_`, (b)
    point at a `T-2N` entry, (c) be existential ("can") in an item or paragraph that cites a
    `::test_`, or (d) walk a history ("ends"), in an
    item that cites a `::test_` and a dated "Measured on"; or be a recorded 0.5.1 sentence. A
    generalisation worded as a walk, or appended to a recorded sentence, is not caught."""
    unanchored = _no_row_unanchored(entry("T-29") + "\n\n" + _wwq9_amendment())

    recorded = tuple(h.split("|")[0] for h in _NO_ROW_HISTORY)

    assert [s for s in unanchored if not s.startswith(recorded)] == []
    assert len(unanchored) == len(recorded), "a recorded 0.5.1 sentence is no longer hit"


@pytest.mark.parametrize(
    ("raw", "sentence"),
    [
        (_E8C7_RESIDUAL_3, "replays last, with no row."),
        (_E8C7_CONTROL_2, "control 5 has no row"),
        (_E8C7_ADR_PARAGRAPH, "wins with no row and no refusal."),
    ],
    ids=["residual 3 heading", "control 2", "ADR paragraph"],
)
def test_the_no_row_key_hits_the_three_sites_the_wave_reworded(raw: str, sentence: str) -> None:
    """Positive control: each site as it stood at e8c7be23 is unanchored under the same classifier."""
    [hit, *_] = _no_row_unanchored(raw)

    assert sentence in hit
