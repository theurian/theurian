"""Holds the records of #915's kind-routed floor remedy to the code that prints it.

Reads `docs/protocol/migrations.md`, `plugins/claude-code/commands/propose.md` and T-28
residual 11, against `_REFUSED_TO_CONTENT_PATH`, `item_labels` and `_floor_refusal`.
"""

from __future__ import annotations

import ast
import re
import string
from pathlib import Path
from typing import Final

import pytest
from floor_remedy_support import TRACKED_LOCATION
from threat_model_claims import SPELLED_NUMBERS, entry_in
from write_lock_claims import REPO_ROOT

from theurian.application.authorization import DEFAULT_CEILING
from theurian.application.item_labels import (
    ACCEPT_READMISSION_REMEDY,
    DECLASSIFY_REMEDY,
    DRAFT_AGAIN_CLAUSE,
    READMIT_REMEDY,
)
from theurian.application.proposal_service import (
    _MAX_NAME_CHARS,
    _PRINTABLE_BODY_PATH,
    _REFUSED_TO_CONTENT_PATH,
    _BodyMove,
    _floor_refusal,
    _removal_cure,
)
from theurian.domain.enums import KnowledgeStatus, Sensitivity
from theurian.domain.identifiers import ItemId

pytestmark = pytest.mark.unit

_MIG, _PROPOSE = "docs/protocol/migrations.md", "plugins/claude-code/commands/propose.md"
_TM = "docs/security/threat-model.md"
_ITEM = ItemId("architecture.a")


def _flat(rel: str) -> str:
    return re.sub(r"\s+", " ", (REPO_ROOT / rel).read_text(encoding="utf-8"))


def _residual_eleven() -> str:
    block = entry_in((REPO_ROOT / _TM).read_text(encoding="utf-8"), "T-28")
    residuals = block.split("**Residuals.**", 1)[1].split("**A known cost", 1)[0]
    return re.sub(r"\s+", " ", re.split(r"(?m)^\d+\. ", residuals)[11])


_STALE_PLACEHOLDER: Final = re.compile(r"dependsOn:\s*\[<its id>\]")


def test_no_record_edits_a_placeholder_into_a_drafts_dependson() -> None:
    """A tool extracting ids from a remedy read `<its id>` as one on main 4d42fe6f (#915); the
    records now say "that migration's id". ADR-0032 keeps the old spelling as history, so it
    is not read, and `dependsOn: [<id>]` (a migration declaring it) stays legitimate."""
    sample = "edit `dependsOn: [<its id>]` into"

    assert _STALE_PLACEHOLDER.search(sample)
    assert not [rel for rel in (_MIG, _PROPOSE) if _STALE_PLACEHOLDER.search(_flat(rel))]


_PAIR: Final = re.compile(r"(?:of|besides) `(\w+)` and `(\w+)`")


def test_the_kinds_the_records_call_content_only_are_the_ones_the_code_routes_so() -> None:
    """Widening `_REFUSED_TO_CONTENT_PATH` changes who is told to redraft; a record naming two
    kinds would then misdirect. Keyed on `(of|besides) `A` and `B``: four sites in
    migrations.md and one in residual 11 at this commit."""
    code = {k.value for k in _REFUSED_TO_CONTENT_PATH}

    pairs = {
        _MIG: _PAIR.findall(_flat(_MIG)),
        _TM: _PAIR.findall(_residual_eleven()),
    }

    assert {rel: len(found) for rel, found in pairs.items()} == {_MIG: 4, _TM: 1}
    assert [set(p) for found in pairs.values() for p in found] == [code] * 5


def test_migrations_md_quotes_the_dependson_sentence_the_content_remedies_end_with() -> None:
    """The clause is the sentence a reader follows to place a redraft after a landed migration;
    a quote that drifted from it sends them to a different file edit."""
    tail = DRAFT_AGAIN_CLAUSE.rsplit(", so ", 1)[1]
    text = _flat(_MIG)

    assert ACCEPT_READMISSION_REMEDY.endswith(tail)
    assert tail.startswith("edit that migration's id into the `dependsOn` of")
    assert f'"{tail}"' in text
    assert f'"{tail.rstrip(".")}"' in text
    assert text.count(tail.rstrip(".")) == 3


@pytest.mark.parametrize(
    ("lowered", "readmitted", "tail"),
    [
        (((_ITEM, Sensitivity.CONFIDENTIAL, Sensitivity.INTERNAL),), (), DECLASSIFY_REMEDY),
        ((), ((_ITEM, KnowledgeStatus.DEPRECATED, KnowledgeStatus.APPROVED),), READMIT_REMEDY),
    ],
    ids=["lowering", "readmission"],
)
def test_migrations_md_quotes_the_removal_remedy_the_floor_prints(
    lowered: tuple[tuple[ItemId, Sensitivity, Sensitivity], ...],
    readmitted: tuple[tuple[ItemId, KnowledgeStatus, KnowledgeStatus], ...],
    tail: str,
) -> None:
    """The quote is the cure a reader performs on a file and a directory. The doc code-formats
    its directory placeholder (the remedy prints the path bare) and the id sits in the remedy's own
    code span; what follows the cure is the hand-written step."""
    where = TRACKED_LOCATION
    text = _flat(_MIG)
    quoted = re.search(r'It reads "(.*?)", with an "every operation whose', text)
    assert quoted, "the removal remedy is no longer quoted"
    quote = (
        quoted[1].replace("<id>", _ITEM.value).replace("`<proposal dir>/`", where.relative + "/")
    )
    refusal = _floor_refusal(lowered, readmitted, frozenset({"changeSensitivity"}), where)
    assert refusal is not None

    assert refusal.remedy == f"{quote} {tail}"


def test_migrations_md_names_the_hand_written_operation_each_remedy_tail_sends_to() -> None:
    """The record says which operation follows the cure; the tails are what name it."""
    lowering, readmission = (
        re.search(r"`(\w+)` operation", tail) for tail in (DECLASSIFY_REMEDY, READMIT_REMEDY)
    )
    assert lowering and readmission

    assert (
        f"the hand-written `{lowering[1]}` for a lowering and `{readmission[1]}` for a readmission"
        in _flat(_MIG)
    )


def _routing_kind_sets() -> int:
    """The entries of the two dicts `test_floor_remedy_routing.py` builds; not importable
    (`tests/unit` is not on `sys.path`), so read from its source."""
    source = (
        REPO_ROOT / "packages/theurian-core/tests/unit/test_floor_remedy_routing.py"
    ).read_text(encoding="utf-8")
    sizes = {
        target.id: len(node.value.keys)
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.AnnAssign)
        and isinstance(target := node.target, ast.Name)
        and target.id in {"_CONTENT_KINDS", "_OTHER_KINDS"}
        and isinstance(node.value, ast.Dict)
    }
    assert sorted(sizes) == ["_CONTENT_KINDS", "_OTHER_KINDS"]
    return sum(sizes.values())


def test_migrations_md_counts_the_kind_sets_the_routing_test_builds() -> None:
    """ "ten kind sets" is the population `_every_remedy` drives; adding a set without
    recounting leaves the record claiming less coverage than the test has."""
    said = re.search(r"for (\w+) kind sets", _flat(_MIG))
    assert said, "the kind-set count is no longer stated"

    assert SPELLED_NUMBERS[said[1]] == _routing_kind_sets()


def test_residual_eleven_names_the_label_the_default_ceiling_serves() -> None:
    """The residual's "one landed `internal` and `approved` is served at the default ceiling" is
    the claim that following the cure does not always leave an item withheld; it holds only
    while the ceiling is `internal`."""
    said = re.search(
        r"one landed `(\w+)` and `approved` is served at the default ceiling", _residual_eleven()
    )
    assert said, "residual 11 no longer states which landed label is served"

    assert said[1] == DEFAULT_CEILING.value == Sensitivity.INTERNAL.value


def _body_move(name: str, item: ItemId) -> _BodyMove:
    source = Path(TRACKED_LOCATION.directory, name)
    return _BodyMove(source, source, b"", False, None, item.value)


def test_migrations_md_quotes_the_body_files_clause_the_removal_cure_prints() -> None:
    """A reader told to delete the body files "those operations drafted" is following the clause
    the cure prints; a doc quote that drifted would name a different file set or position."""
    where = TRACKED_LOCATION
    quoted = re.search(
        r'body files that no kept operation uses, "(.*?)" follows the first', _flat(_MIG)
    )
    assert quoted, "the body-files clause is no longer quoted"
    clause = quoted[1].replace("<proposal dir>/<file>", f"{where.relative}/body.md")

    cure = _removal_cure([_ITEM], where, [_body_move("body.md", _ITEM)])

    assert f"in {where.relative}/{clause}, then run" in cure


def test_a_body_file_another_items_operation_still_uses_is_not_named_for_deletion() -> None:
    """The doc's "no kept operation uses" bounds the clause: deleting a shared body would break the
    kept operation that reads it."""
    other = ItemId("architecture.b")
    shared = _body_move("shared.md", _ITEM)

    cure = _removal_cure([_ITEM], TRACKED_LOCATION, [shared, _body_move("shared.md", other)])

    assert "body files" not in cure


def test_migrations_md_quotes_the_directory_only_cure_a_redacted_id_leaves() -> None:
    """The fallback prints no id, so an operator is never handed a secret-shaped one; a doc quote
    that drifted from it would describe a cure the floor does not print."""
    where = TRACKED_LOCATION
    secret = ItemId("sk-" + "a1b2c3d4e5f6" * 4)
    quoted = re.search(r'the cure is "(.*?)" alone', _flat(_MIG))
    assert quoted, "the directory-only cure is no longer quoted"
    quote = quoted[1].replace("`<proposal dir>/`", where.relative + "/")

    refusal = _floor_refusal(
        ((secret, Sensitivity.CONFIDENTIAL, Sensitivity.INTERNAL),),
        (),
        frozenset({"changeSensitivity"}),
        where,
    )

    assert refusal is not None
    assert refusal.remedy.startswith(
        f"Nothing has moved. To keep those items at their landed labels, {quote} "
    )
    assert secret.value not in refusal.remedy


def test_migrations_md_states_the_cut_length_the_code_cuts_a_body_path_at() -> None:
    """The record says a body path "past 200 characters" is cut; the number is
    `_MAX_NAME_CHARS`, so the record moves with it or goes RED."""
    said = re.search(r"cut a body path past (\d+) characters", _flat(_MIG))
    assert said, "the cut length is no longer stated"

    assert int(said[1]) == _MAX_NAME_CHARS


def test_a_body_path_past_the_cut_length_gets_the_delete_only_cure() -> None:
    """The cut half of the directory-only fallback has its own branch: a path the refusal would
    cut is never printed, so the cure names nothing. A path exactly at the length is printed."""
    where = TRACKED_LOCATION
    room = _MAX_NAME_CHARS - len(where.relative) - 1
    at_the_limit = _removal_cure([_ITEM], where, [_body_move("b" * room, _ITEM)])
    past_it = _removal_cure([_ITEM], where, [_body_move("b" * (room + 1), _ITEM)])

    assert "delete the body files" in at_the_limit
    assert past_it == f"delete {where.relative}/."


_CHANGELOG = "packages/theurian-core/CHANGELOG.md"


def _unreleased_915_entry() -> str:
    """The `[Unreleased]` `### Fixed` bullet naming issue 915, whitespace-flattened."""
    text = (REPO_ROOT / _CHANGELOG).read_text(encoding="utf-8")
    fixed = text.split("## [Unreleased]", 1)[1].split("\n## [", 1)[0].split("### Fixed", 1)[1]
    bullets = [b for b in re.split(r"(?m)^- ", fixed) if "/issues/915)" in b]
    assert len(bullets) == 1, f"{len(bullets)} Unreleased Fixed bullets cite #915"
    return re.sub(r"\s+", " ", bullets[0])


def test_the_changelog_entry_states_which_body_files_the_cure_names_and_its_fallback() -> None:
    """A reader of the release notes decides what to delete from the entry; "the body files only
    those operations drafted" is what keeps a shared body out of the deletion, and the redact-or-cut
    fallback and the containment fallback (a body path that is not a plain path inside the
    proposal directory) are the two cases where the cure names no file."""
    entry = _unreleased_915_entry()

    assert "delete the body files only those operations drafted" in entry
    assert re.search(
        r"says to delete the directory instead where an id or body path it would print is one "
        r"an error message redacts or cuts",
        entry,
    )
    assert "or where a body path is not a plain path inside the proposal directory" in entry, entry


_ADR = "docs/adr/0032-the-write-intent-mcp-tool-surface.md"


def _lowered(text: str) -> str:
    """Whitespace-flattened, blockquote markers and code backticks dropped, lower-cased."""
    text = re.sub(r"(?m)^\s*> ?", "", text)
    return re.sub(r"\s+", " ", text.replace("`", "")).lower()


def _adr_915_note() -> str:
    """The ADR-0032 note headed `Amended 2026-10-14` citing #915, up to the next section."""
    text = (REPO_ROOT / _ADR).read_text(encoding="utf-8")
    head = "**Amended 2026-10-14 ([#915]"
    assert text.count(head) == 1
    return _lowered(text.split(head, 1)[1].split("\n## ", 1)[0])


def test_migrations_md_states_the_body_path_condition_and_not_the_message_redaction_wording() -> (
    None
):
    """The directory-only fallback fires on the path condition and on `_one_name`; the superseded
    wording blamed "the message" for a body path, but no refusal message prints one."""
    text = _lowered(_flat(_MIG))

    assert (
        "made of segments of ascii letters, digits, ., _ and -, none . or .., and resolving "
        "inside that directory" in text
    )
    assert "if a body path fails that" in text
    assert "if the message would redact" not in text


def test_the_adr_note_states_the_path_gate_and_not_the_message_redaction_wording() -> None:
    """The ADR note is the decision record of the fallback's triggers; the superseded wording
    named a gate (the message) that never saw a body path."""
    note = _adr_915_note()

    assert "outside _printable_body_path or resolves outside that directory" in note
    assert "the refusal's message redacts or cuts" not in note


def _doc_grammar() -> tuple[set[str], set[str]]:
    """The characters and the excluded segments migrations.md lists for a plain body path."""
    said = re.search(
        r"segments of ASCII letters, digits, `(.)`, `(.)` and `(.)`, none `(\.+)` or `(\.+)`",
        _flat(_MIG),
    )
    assert said, "migrations.md no longer lists the plain-path grammar in this shape"
    chars = set(string.ascii_letters + string.digits) | set(said.group(1, 2, 3))
    return chars, set(said.group(4, 5))


def test_migrations_md_lists_the_characters_and_segments_the_body_path_grammar_allows() -> None:
    """The doc is what an operator checks a path against; a widened grammar would print paths the
    doc calls unplain, and a narrowed one would fall back where the doc promises a file name.
    Both sets are probed from the compiled pattern, not read from its source."""
    probed = {
        chr(c)
        for c in range(128)
        if chr(c) != "/" and _PRINTABLE_BODY_PATH.fullmatch(f"a{chr(c)}a")
    }
    excluded = {seg for seg in (".", "..", "...") if not _PRINTABLE_BODY_PATH.fullmatch(seg)}
    nested = [f"a/{seg}/b" for seg in (".", "..")] + [f"{seg}/a" for seg in (".", "..")]

    chars, segments = _doc_grammar()

    assert probed == chars
    assert excluded == segments
    assert not [n for n in nested if _PRINTABLE_BODY_PATH.fullmatch(n)]
