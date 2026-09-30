"""A ratchet over the files that name the body-joining `get_item_exact` read (#832).

`get_item_exact` joins the current revision's body, so a gate placed on it
materialises a withheld row's body before refusing it (T-26). A file that starts
calling or describing it must be classified here before it lands; a traversal hop
(#841) is the expected next one.

**Key A.** A line of a tracked file that contains `get_item_exact` and does not
contain `get_item_exact_metadata`, outside `packages/theurian-core/tests/`. The
files holding such a line must equal :data:`_RECORDED`, each with one reason.

**Known-weak half, stated beside the key.** The key is line-keyed. It cannot see
a face whose symbol is off the line (the port docstring's "through this exact
read"), nor one that names both reads on one line (ADR-0003's row, ADR-0037's
quote, the threat model's Amended block), nor a caller that reaches the read
through another name. Those are covered by anchored pins instead:
`test_adr_0038_gate_read_records.py` holds the gate's read and the post-gate
body read in the records that name them, and holds the callers of
`get_item_exact` in `src` to `_served_item` by AST.

**Corpus membership (mandatory declaration).** The population is `git ls-files`
through :func:`adr_0038_support._tracked`, so an untracked file is outside it
and a staged one is inside. `.theurian/` is **IN**: the dogfood corpus is
tracked, and its twin of ADR-0003 holds a frozen body that says
`get_item_exact`, which is the point of counting it. When the twin is
re-seeded, its old body stays (revisions are immutable) and a new file carries
the new body: this pin then goes RED on the new path, or on a recorded path that
left the tree, and the re-seeding PR classifies the change here. That RED is the
ratchet working, not a defect. `packages/theurian-core/tests/` is OUT because
its files spell the read as data.
"""

from __future__ import annotations

from typing import Final

import pytest
from adr_0038_support import REPO_ROOT, SRC, _definitions, _references, _tracked

pytestmark = pytest.mark.unit

_KEY: Final = "get_item_exact"
_EXCLUDED_KEY: Final = "get_item_exact_metadata"
_OUT: Final = "packages/theurian-core/tests/"

_DEFINITION_OR_CALLER: Final = "definition or caller"
_CONTRAST: Final = "contrast with the metadata form"
_DATED_RECORD: Final = "dated record"
_SNAPSHOT: Final = "governed snapshot"

#: One reason per file. The `src` files marked as definition or caller are also
#: derived from the AST below, so that half cannot be recorded wrongly.
_RECORDED: Final = {
    ".theurian/knowledge/architecture/ports-and-adapters.01M1QMNB8R2YG7RMS2JVYARM4C.md": _SNAPSHOT,
    "docs/adr/0038-specification-folds-into-a-knowledge-kind.md": _CONTRAST,
    "docs/roadmap.md": _CONTRAST,
    "docs/security/threat-model.md": _DATED_RECORD,
    "docs/work-logs/2026-09-16-t26-timing.md": _DATED_RECORD,
    "packages/theurian-core/CHANGELOG.md": _DATED_RECORD,
    SRC + "application/visibility.py": _DEFINITION_OR_CALLER,
    SRC + "domain/ports/canonical_store.py": _DEFINITION_OR_CALLER,
    SRC + "infrastructure/sqlite/store.py": _DEFINITION_OR_CALLER,
    SRC + "mcp/tools.py": _CONTRAST,
}


def _population() -> set[str]:
    tracked = [path for path in _tracked() if not path.startswith(_OUT)]
    assert ".theurian/knowledge/architecture" in " ".join(tracked), (
        "positive control: the tracked listing no longer reaches the dogfood corpus"
    )
    hits: set[str] = set()
    for path in tracked:
        file = REPO_ROOT / path
        if not file.is_file():
            continue
        text = file.read_bytes().decode("utf-8", "replace")
        if any(_KEY in line and _EXCLUDED_KEY not in line for line in text.splitlines()):
            hits.add(path)
    return hits


def test_every_file_naming_the_joined_read_is_recorded_with_a_reason() -> None:
    """RED means a file names `get_item_exact` unclassified, or a recorded file stopped doing so."""
    found = _population()

    assert found == _RECORDED.keys(), (
        f"unrecorded: {sorted(found - _RECORDED.keys())}; "
        f"recorded but no longer hitting key A: {sorted(_RECORDED.keys() - found)}"
    )


def test_the_recorded_definition_and_caller_files_are_the_ones_the_ast_finds() -> None:
    """RED means the joined read gained a definition or caller the record does not classify."""
    derived = {path for path, _ in _references(frozenset({_KEY}), SRC)} | _definitions(_KEY, SRC)
    recorded = {path for path, why in _RECORDED.items() if why == _DEFINITION_OR_CALLER}

    assert derived == recorded
