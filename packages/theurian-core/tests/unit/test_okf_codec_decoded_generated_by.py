"""`DecodedGeneratedBy` is unconstrained where `GeneratedBy` raises (#810 round 3).

`okf_codec.py::GeneratedBy.by` now raises `InvariantViolationError` on anything
but the export tool's own actor form, `theurian/\\S+` -- pinned on `main` by
`test_okf_codec.py`'s `test_the_generated_actor_refuses_anything_but_the_tool_form`
and `test_the_generated_actor_accepts_the_tool_form`, not duplicated here. The
same commit added `DecodedGeneratedBy`, deliberately without that check: a
vanilla bundle's `generated.by` can be `human:<id>`, `process:<id>`, or another
producer's own tool form -- decision 1 also applies here, since a decoded actor
string is untrusted data the importer reads, never a fact this module verifies.

This is the decode-side half of that asymmetry, and it has no existing pin:
`test_okf_codec.py`'s own decode tests only ever decode a Theurian-exported
`generated` block (`theurian/<version>`, satisfying both types) or none at all.
"""

from __future__ import annotations

import pytest

from theurian.application.okf_codec import DecodedConceptDocument, decode_concept_document

pytestmark = pytest.mark.unit

_BODY = "\nBody prose.\n"


def _document(generated_by: str) -> str:
    return (
        "---\n"
        "type: decision\n"
        "title: A concept with a foreign actor\n"
        "status: stable\n"
        "generated:\n"
        f"  by: {generated_by}\n"
        "  at: '2026-01-01'\n"
        "---\n" + _BODY
    )


#: `(YAML literal for generated.by, the string it decodes to)`. The last case's
#: literal is a double-quoted YAML scalar with `\\x00`/`\\u200b` escapes; the
#: expected value is the actual NUL and zero-width-space characters those
#: escapes name, written out rather than re-derived from the literal.
_NON_THEURIAN_ACTORS: tuple[tuple[str, str], ...] = (
    ('"alice"', "alice"),
    ('"human:alice"', "human:alice"),
    ('"process:ci-runner"', "process:ci-runner"),
    ('"attacker\\x00\\u200b"', "attacker\x00\u200b"),
)


@pytest.mark.parametrize(
    ("generated_by", "expected"),
    _NON_THEURIAN_ACTORS,
    ids=[
        "a-bare-human-name",
        "okf-human-actor-form",
        "okf-process-actor-form",
        "control-characters",
    ],
)
def test_a_non_theurian_actor_decodes_as_data_rather_than_raising(
    generated_by: str, expected: str
) -> None:
    """None of these satisfy `theurian/\\S+`; the encoder's `GeneratedBy` would
    raise on every one (`test_the_generated_actor_refuses_anything_but_the_tool_form`).
    The decoder is a different type for exactly this case: it stores the actor
    verbatim rather than validating it.
    """
    decoded = decode_concept_document(_document(generated_by))

    assert isinstance(decoded, DecodedConceptDocument), decoded
    assert decoded.front_matter.generated is not None
    assert decoded.front_matter.generated.by == expected
