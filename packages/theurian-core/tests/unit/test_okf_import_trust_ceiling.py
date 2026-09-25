"""ADR-0037 decision 6's trust ceiling: no caller can raise `ImportedConcept.trust_level`.

`ImportedConcept.trust_level` is declared `field(default=TrustLevel.INFERRED,
init=False)` -- the `KnowledgeCandidate` precedent (`domain/review.py`)
applied to a second on-ramp. `init=False` is a stronger guarantee than "raises
if you pass something else": it is not an accepted parameter at all, so no
call site can even *attempt* a higher trust level, checked or not.
"""

from __future__ import annotations

import pytest

from theurian.application.okf_import import ImportedConcept
from theurian.domain.enums import KnowledgeKind, TrustLevel
from theurian.domain.identifiers import ItemId
from theurian.domain.values import MARKDOWN

pytestmark = pytest.mark.unit


def _fields(**overrides: object) -> dict[str, object]:
    fields: dict[str, object] = {
        "item_id": ItemId("architecture.policy"),
        "title": "A policy",
        "kind": KnowledgeKind.DECISION,
        "body": "body",
        "content_type": MARKDOWN,
        "labels": (),
        "source_anchors": (),
        "relations": (),
    }
    fields.update(overrides)
    return fields


def test_an_imported_concept_defaults_to_inferred_trust() -> None:
    concept = ImportedConcept(**_fields())  # type: ignore[arg-type]

    assert concept.trust_level == TrustLevel.INFERRED


def test_no_caller_can_pass_a_trust_level_to_an_imported_concept() -> None:
    """`init=False` refuses the keyword outright -- there is no branch to bypass.

    Distinct from a constructor that accepts and then validates: a validated
    field can be validated wrong, once, by a future edit. A field absent from
    `__init__` cannot be raised by any caller regardless of what the caller
    computed, because there is no parameter to hand a computed value to.
    """
    with pytest.raises(TypeError, match="trust_level"):
        ImportedConcept(**_fields(trust_level=TrustLevel.REVIEWED))  # type: ignore[arg-type]
