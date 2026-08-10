"""A malformed ``--spec`` entry must raise ``HarnessError``, never crash raw.

HIGH-2 face 1: the old ``{file, old, new}`` shape was guarded by
``isinstance(entry, dict)``, but a composite mutation's ``edits`` list was
not -- a non-dict element indexed straight into ``item["file"]``, raising a
bare ``TypeError`` that reaches ``main``'s ``except HarnessError`` unhandled.
Reading the JSON is real filesystem I/O, so these are integration tests.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from mutate_edits import HarnessError
from mutate_spec import _load_spec

pytestmark = pytest.mark.integration


def test_a_non_dict_edit_element_is_rejected_not_a_raw_typeerror(tmp_path: Path) -> None:
    """A string in an ``edits`` list must not reach ``item["file"]`` indexing."""
    spec = tmp_path / "spec.json"
    spec.write_text(
        json.dumps([{"label": "bad", "edits": ["not-a-dict"]}]),
        encoding="utf-8",
    )

    with pytest.raises(HarnessError):
        _load_spec(spec)


def test_a_non_dict_edit_among_otherwise_valid_edits_is_still_rejected(tmp_path: Path) -> None:
    """The check must cover every element, not just the first."""
    spec = tmp_path / "spec.json"
    spec.write_text(
        json.dumps(
            [
                {
                    "label": "bad",
                    "edits": [
                        {"file": "a.py", "old": "X", "new": "Y"},
                        None,
                    ],
                }
            ]
        ),
        encoding="utf-8",
    )

    with pytest.raises(HarnessError):
        _load_spec(spec)


def test_edits_and_file_old_new_together_is_rejected_not_silently_resolved(
    tmp_path: Path,
) -> None:
    """LOW-4: naming both shapes at once used to silently drop `file`/`old`/`new`."""
    spec = tmp_path / "spec.json"
    spec.write_text(
        json.dumps(
            [
                {
                    "label": "ambiguous",
                    "file": "ignored.py",
                    "old": "X",
                    "new": "Y",
                    "edits": [{"file": "a.py", "old": "X", "new": "Y"}],
                }
            ]
        ),
        encoding="utf-8",
    )

    with pytest.raises(HarnessError):
        _load_spec(spec)


def test_a_spec_whose_top_level_is_not_a_list_is_rejected(tmp_path: Path) -> None:
    """MEDIUM-4: a spec is documented as a JSON list; a bare object is not one.

    ``_load_spec``'s very first check (``isinstance(raw, list)``) had no
    test of its own -- every other spec fixture in this file is already a
    list, so a regression here (e.g. accepting a dict and iterating its
    keys) would have gone unnoticed.
    """
    spec = tmp_path / "spec.json"
    spec.write_text(json.dumps({"label": "not-a-list"}), encoding="utf-8")

    with pytest.raises(HarnessError, match="a JSON list"):
        _load_spec(spec)


def test_a_non_dict_top_level_entry_is_rejected(tmp_path: Path) -> None:
    """MEDIUM-4: a top-level list element that is not an object must be refused.

    Distinct from the ``edits``-list check above: this is the *original*
    ``isinstance(entry, dict)`` guard for each mutation entry itself, which
    also had no test naming it directly.
    """
    spec = tmp_path / "spec.json"
    spec.write_text(json.dumps(["not-an-object"]), encoding="utf-8")

    with pytest.raises(HarnessError, match="is not an object"):
        _load_spec(spec)
