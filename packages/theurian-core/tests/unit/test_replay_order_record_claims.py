"""Pins what the GHSA-wwq9 record (0.5.2) says about the replay order, against the code.

Reads the `reorders` rule, the accept refusals, the residual counts, `MoveKind` and the census
marker where the docs, help texts, plugin and comments state them.
"""

# ruff: noqa: E501 -- the tables hold the exact words each site uses
from __future__ import annotations

import importlib.util
import re
from collections import Counter
from pathlib import Path
from typing import Final, get_args

import pytest
from threat_model_claims import SPELLED_NUMBERS, entry_in, prose
from write_lock_claims import REPO_ROOT

from theurian import __version__
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
    A new statement of the rule is a new site to pin, or an exemption to record."""
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
#: Not statements of when accept refuses: a recounted finding, and an ADR how-to.
_NOT_REFUSALS: Final = {_ADR: 1, "docs/adr/README.md": 1}
#: The key: an add-half wording, in the flattened text of docs/, src/theurian, plugins/ and the Core CHANGELOG.
_ADD_HALF = re.compile(
    r"\b(?:would add|neither adds|to add|adds or re-attributes|adds? (?:a|an))\b(?: or re-attribute)?[^.;]{0,25}?\brows?\b|\bwould add or re-attribute\b"
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
    """The population is every add-half hit: a pinned refusal or a recorded non-refusal."""
    expected = Counter(s.split("|")[0] for s in _REFUSALS) + Counter(_NOT_REFUSALS)

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
    sentence = _match(
        r"(\w+) are open in 0\.5\.2: the (\w+) and the (\w+) moved to t-29, and the (\w+) is closed",
        _flat(entry_in(_read(_TM), "T-28")),
    )
    ordinal = {"sixth": 6, "ninth": 9, "tenth": 10}
    n = SPELLED_NUMBERS

    assert (moved, closed) == ([6, 9], [10])
    assert (n[sentence[1]], [ordinal[w] for w in sentence.group(2, 3, 4)]) == (
        len(t28) - 3,
        [6, 9, 10],
    )
    assert [
        n[w]
        for w in _match(
            r"(\w+) open residuals, (\w+) closed and (\w+) moved to t-29", _row("T-28")
        ).groups()
    ] == [len(t28) - 3, 1, 2]
    assert n[_match(r"all (\w+) are open", _flat(entry_in(_read(_TM), "T-29")))[1]] == len(t29)
    assert n[_match(r"(\w+) open residuals", _row("T-29"))[1]] == len(t29)


def test_the_moved_residuals_and_their_dependson_qualifier_survive_the_move() -> None:
    t28, t29 = _residuals("T-28", "**A known cost"), _residuals("T-29", "**The same root cause")
    for number, item in enumerate(t28, 1):
        if item.startswith("**Moved to T-29"):
            target = int(_match(r"its residual (\d+)", item)[1])
            assert f"t-28 residual {number} until this entry" in _flat(t29[target - 1])

    assert "absent dependson" in _flat(t29[1])
    assert "with a dependson, see t-28 residual 5 and reorders" in _flat(t29[1])


def test_every_move_kind_is_named_where_a_reader_looks_for_kinds() -> None:
    texts = {
        "kind row": _match(r"(?m)^\| `kind` \|.*$", _read(_MIG))[0],
        "plugin": _read(_PLUGIN),
        "validate help": commands.migrate_validate.__doc__ or "",
        "apply help": commands.migrate_apply.__doc__ or "",
    }
    for value in get_args(MoveKind):
        for name, text in texts.items():
            assert re.search(rf"`(?:kind: )?{value}`|`kind: {value}", text), f"{name} lacks {value}"


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
    closed = _match(r"closed in (\d+\.\d+\.\d+)", _flat(_residuals("T-28", "**A known cost")[-1]))[
        1
    ]
    headings = re.findall(r"(?m)^## \[(\d+\.\d+\.\d+)\]", _read(_CHANGELOG))

    assert closed in headings
    assert [int(x) for x in closed.split(".")] <= [int(x) for x in re.findall(r"\d+", __version__)]
