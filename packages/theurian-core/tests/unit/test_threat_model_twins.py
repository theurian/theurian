"""Two threat-table rows that exist twice, held to the same owner cite.

``docs/security/threat-model.md`` carries the threat table, and
``docs/architecture/requirements-analysis.md`` carries a second copy of it. A
correction applied to one and not the other leaves a reader on the copy being
told a *closed* issue owns a control that does not exist -- which is what
happened: https://github.com/theurian/theurian/pull/425 repointed T-7 at #429
and T-15 at #329 in the threat model, and the copy went on citing closed #129
and closed #198. An owner cite is not decoration; it is the answer to "who is
going to build this", and a closed issue answers "nobody, it is done".

**What this pins, and it is two rows rather than a property.** The population is
exactly the keys in :data:`TWIN_CITE_ANCHORS` -- ``T-7`` and ``T-15`` -- and for
each of them exactly one *fragment* of one cell: the tail of the Control column
beginning at that row's anchor phrase. Every other row of the table, every other
cell of these two rows, and the head of the Control cell before the anchor are
all unchecked. This is a regression test over the two rows that drifted, and
**calling it "the twin tables agree" would be the false closure argument** --
the class is a duplicated table, and a class is not closed by pinning the two
members that were caught.

The general form -- every row of both tables, keyed on the threat id -- is
worth having and is not this. It needs the two tables' *columns* reconciled
first (the copy's Control column is a different shape: T-15's is longer there
than in the summary row, deliberately), and that reconciliation is a documents
change, not a test change.

**The row is located by an anchored key, and the key is asserted unique.** A
threat id is a whole cell, so ``^| T-7 |`` matches the summary row and nothing
else -- not the ``T-7`` inside prose, not a ``T-15`` mentioned in another row's
cell. If either file stops containing exactly one such row the test fails on
that, rather than quietly comparing two empty strings and passing: a comparison
between two absent things is the assertion that cannot fail, and this whole
module exists because a copy went unread.
"""

from __future__ import annotations

import pathlib
import re
from typing import Final

import pytest

REPO_ROOT: Final = pathlib.Path(__file__).resolve().parents[4]

#: The document that owns the threat table, and the one that copies it.
THREAT_MODEL: Final = REPO_ROOT / "docs/security/threat-model.md"
REQUIREMENTS_ANALYSIS: Final = REPO_ROOT / "docs/architecture/requirements-analysis.md"

#: The two rows this module holds, each with the phrase its owner cite begins at.
#:
#: **The population is these two keys and nothing else.** They are the two rows
#: [#425](https://github.com/theurian/theurian/pull/425) corrected in the threat
#: model and left stale in the copy; the anchor is where each row's sentence about
#: the control's *reach beyond the gate it names* starts, because that sentence is
#: what carries the issue number a reader would go to. T-15's anchor moved with
#: [#329](https://github.com/theurian/theurian/issues/329): the sentence used to
#: be about an unshipped control and is now about a shipped one, and it still
#: carries the cite, so the key held while the claim under it inverted.
#:
#: An anchor is asserted present rather than defaulted to the empty string. A
#: missing anchor means the row was rewritten, and comparing the whole cell
#: instead would silently widen this module from "the cite agrees" to "the two
#: tables are byte-identical here", which they are not and are not meant to be.
TWIN_CITE_ANCHORS: Final = {
    "T-7": "scheme allowlist",
    "T-15": "`theurian index build` is SEC-11's second control",
}


def _summary_row(path: pathlib.Path, threat_id: str) -> str:
    """The one table row of ``path`` whose first cell is ``threat_id``.

    Anchored at the start of the line, so the key is the row's own id cell and
    not an occurrence of the same string inside another row's prose -- T-15's
    Control cell alone names seven issue numbers in
    ``requirements-analysis.md`` (#198, #329, #330, #336, #349, #360, #361) and
    four in the threat model's shorter copy (#198, #329, #330, #336), the owner
    cite's own pair included in both. Measured 2026-09-03; the two counts differ
    because the copy's Control column is the longer shape, which is the same
    reconciliation this module's docstring records as owed.
    """
    pattern = re.compile(rf"^\| {re.escape(threat_id)} \|", re.MULTILINE)
    rows = [line for line in path.read_text(encoding="utf-8").splitlines() if pattern.match(line)]

    assert len(rows) == 1, f"{path.name} has {len(rows)} rows keyed `| {threat_id} |`, expected 1"
    return rows[0]


def _owner_cite(path: pathlib.Path, threat_id: str) -> str:
    """The tail of that row's Control cell, from its anchor phrase onward.

    The Control cell is the last column, so it is taken as the text after the
    final ``" | "`` separator. That holds while no cell contains that separator
    itself, which a Markdown table cannot express unescaped anyway.
    """
    row = _summary_row(path, threat_id)
    control = row.rstrip().removesuffix("|").rsplit(" | ", 1)[-1].strip()

    anchor = TWIN_CITE_ANCHORS[threat_id]
    assert anchor in control, (
        f"{path.name}'s {threat_id} Control cell no longer contains the anchor "
        f"{anchor!r}, so this module has nothing to compare: {control}"
    )
    return control[control.index(anchor) :]


@pytest.mark.parametrize("threat_id", sorted(TWIN_CITE_ANCHORS))
def test_a_twin_threat_row_cites_the_same_owner_as_the_original(threat_id: str) -> None:
    """The copy names whoever the threat model names, or a reader is sent to a closed issue.

    Both sides are read from disk and compared to each other; neither is compared
    to a literal written here. A pin that quoted the expected cite would go RED
    when the *owner changes legitimately* -- exactly the case #425 was, and the
    one a future reassignment will be -- and would say nothing about whether the
    two documents agree, which is the only thing that was wrong.

    RED on either side moving alone. Whichever document is edited next, this
    fails until the other is edited too.
    """
    original = _owner_cite(THREAT_MODEL, threat_id)

    copy = _owner_cite(REQUIREMENTS_ANALYSIS, threat_id)

    assert copy == original, (
        f"{threat_id}'s owner cite disagrees between the two threat tables.\n"
        f"  {THREAT_MODEL.name}: {original}\n"
        f"  {REQUIREMENTS_ANALYSIS.name}: {copy}"
    )
