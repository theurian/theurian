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
from threat_model_claims import SPELLED_NUMBERS, entry_in, prose
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


def _population(key: re.Pattern[str], *, flat: bool = False) -> dict[str, int]:
    files = [
        p
        for r in ("docs", _SRC, "plugins")
        for p in (ROOT / r).rglob("*")
        if p.suffix in {".md", ".py"}
    ]
    texts = {
        p.relative_to(ROOT).as_posix(): p.read_text(encoding="utf-8")
        for p in [*files, ROOT / _CHANGELOG]
    }
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


def _residuals(threat_id: str, stop: str) -> list[str]:
    block = entry_in(_read(_TM), threat_id).split("**Residuals.**", 1)[1].split(stop, 1)[0]
    return re.split(r"(?m)^\d+\. ", block)[1:]


def test_the_residual_counts_agree_with_the_numbered_lists() -> None:
    t28, t29 = _residuals("T-28", "**A known cost"), _residuals("T-29", "**The same root cause")
    moved = [i for i, r in enumerate(t28, 1) if r.startswith("**Moved to T-29")]
    closed = [i for i, r in enumerate(t28, 1) if r.startswith("**Closed in")]
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
    open29 = len([r for r in t29 if not r.startswith("**Closed in")])
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


def test_a_version_t_28_closes_in_has_a_changelog_heading_at_or_below_the_package() -> None:
    """T-28 residual 10 is "Closed in 0.5.2"; that must be a shipped version."""
    [item] = [r for r in _residuals("T-28", "**A known cost") if r.startswith("**Closed in")]
    closed = _match(r"closed in (\d+\.\d+\.\d+)", _flat(item))[1]
    headings = re.findall(r"(?m)^## \[(\d+\.\d+\.\d+)\]", _read(_CHANGELOG))

    assert closed in headings
    assert [int(x) for x in closed.split(".")] <= [int(x) for x in re.findall(r"\d+", __version__)]


def test_the_changelogs_quoted_refusal_messages_match_the_templates_that_produce_them() -> None:
    """The fragments between each quote's `<placeholders>` are literals of its function."""
    quotes = re.findall(
        r'"(accepting this proposal would (?:make|let)[^"]*)"', _flat(_read(_CHANGELOG))
    )
    for fn, key in (
        ("_refuse_a_reported_upsert", "undoing what this proposal sets"),
        ("_refuse_a_landed_overwrite", "loosen what it sets"),
    ):
        source = re.sub(r'\{[^}]*\}|\bf"|"', "", inspect.getsource(getattr(proposal_service, fn)))
        template = " ".join(_flat(source).replace("'", "").split())
        found = [q for q in quotes if key in q]
        assert found, key
        for quote in found:
            for fragment in re.split(r"<[^>]*>|…|the landed migration", quote):
                assert " ".join(fragment.replace("'", "").split()) in template, (fn, fragment)


def _t29() -> str:
    return entry_in(_read(_TM), "T-29")


def _between(raw: str, start: str, end: str) -> str:
    assert raw.count(start) == 1, f"{start!r} occurs {raw.count(start)} times"
    return raw.split(start, 1)[1].split(end, 1)[0]


def _items(block: str) -> list[str]:
    return [_flat(item) for item in re.split(r"(?m)^\d+\. ", block)[1:]]


def _actions() -> list[str]:
    return _items(_between(_t29(), "**Operator actions.**", "**What would reopen"))


def test_the_repair_rule_names_the_last_writer_in_applicationorder_and_says_to_read_it() -> None:
    """A repair depending on the row's migration alone left a chain loose; a reader told "the
    row's `migrationId` is enough" repeats that."""
    repair = _actions()[1]

    assert (
        "declares dependson on the last migration in migrate validate's applicationorder that writes the field: often the row's migrationid, but read it, never assume it."
        in repair
    )
    assert "dependson: [<the row's migrationid>]" not in repair
    assert "is enough" not in repair


def test_the_repair_guard_and_confirmation_each_serve_the_item_through_includeunapproved() -> None:
    """Without `includeUnapproved: true` an item an in-place withdrawal left `draft` reads as
    withheld whether or not the repair held (`test_reorders_report.py::test_a_repair_guard_needs_include_unapproved_after_an_in_place_withdrawal`),
    and the report keeps naming the row after a repair, so neither it nor the label confirms one."""
    repair = _actions()[1]

    assert (
        "if knowledge.get, called at the default ceiling, with includeunapproved: true, already withholds the item, leave it"
        in repair
    )
    assert (
        "confirm after migrate apply, never by the report: in a fresh migrate validate, no migration that writes the field follows the repair in applicationorder; and knowledge.get at the default ceiling, with includeunapproved: true, withholds the item."
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


def test_the_severity_premise_names_the_residuals_that_need_a_human_merge() -> None:
    """Residual 4 is reached through `accept` with a hand-edited `dependsOn`, so "every residual
    needs a human to merge" overstates the premise the grade rests on."""
    severity = _flat(_between(_t29(), "**Severity:", "**Controls (0.5.2)"))

    assert "residuals 1 to 3 need a human to merge the migrations without accept" in severity
    assert (
        "residual 4 is reached through accept too, with a hand-edited dependson, and discloses nothing there"
        in severity
    )
    assert "every residual open after 0.5.2 needs a human to merge" not in severity


_GRADE_SITES: Final = {
    "heading": lambda: _flat(_match(r"(?m)^#### T-29 .*$", _read(_TM))[0]),
    "summary row": lambda: _row("T-29"),
    "severity paragraph": lambda: _flat(_between(_t29(), "**Severity:", "**Controls (0.5.2)")),
}


@pytest.mark.parametrize("site", _GRADE_SITES)
def test_t_29_is_graded_critical_as_shipped_and_high_after_0_5_2_at_each_site(site: str) -> None:
    """The grade is read at the heading, the summary row and the Severity paragraph; one site
    drifting to another grade leaves a reader the wrong one depending on where they land."""
    text = _GRADE_SITES[site]()

    assert "critical as shipped (ghsa-wwq9-p8wq-5m68)" in text
    assert "high after 0.5.2" in text


def test_every_t_29_residual_is_stated_open_in_both_releases() -> None:
    residuals = _flat(_between(_t29(), "**Residuals.**", "1. **"))

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
    controls = _between(_t29(), "**Controls (0.5.2).**", "**What holds it.**")
    second = _items(controls[controls.index("1. ") :])[1]

    assert "is stated where it was measured: t-28 control 5 and residual 10" in second
    assert "has the row" not in second and "has no row" not in second


def test_the_adr_names_the_tightening_inversion_without_the_no_row_wording() -> None:
    """The amendment points at T-29 residual 4 for what each history ends at and reports."""
    amendment = _flat(
        _between(
            _read(_ADR),
            "**Amended by GHSA-wwq9-p8wq-5m68 (2026-10-08).**",
            "**Amended by GHSA-fjqq",
        )
    )

    assert (
        "a dependson migration that tightens a label can replay after a larger-id changesensitivity lowering or restoreitem, and reorders covers the loosening direction only. t-29 residual 4 records the histories measured"
        in amendment
    )
    assert "wins with no row" not in amendment
