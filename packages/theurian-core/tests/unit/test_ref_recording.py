r"""What an external ``$ref`` records, and whether it records at all (T-7, #203).

The other half of T-7's shipped control. ``test_network_call_sites.py`` holds
*never fetched*; this file holds *faithfully recorded*, and the two fail for
different reasons. A parser that records nothing at all passes every test in that
file, because a document that reaches no network while quietly dropping its
references is exactly as silent as one that has none.

Fidelity matters here for a reason that has not arrived yet: the scheme allowlist
T-7 owes (#129, Milestone 7) will key on the recorded ``scheme``. Every defect
below was measured against ``_external_refs`` before it was fixed, and the first
two are the ones that would have made that future gate fail *open* --

- ``//evil.test/x.json`` and ``\\smb-host\share\x.json`` both name a host, and
  both recorded ``relative-file``, the one label a gate for repository-local
  references is most likely to accept;
- ``C:\Windows\system32\x.json`` recorded the scheme ``c``, an artifact of
  ``urlsplit`` reading a drive letter as a scheme;
- a ``$ref`` nested past ``MAX_REF_DEPTH`` vanished, and the document reported
  ``unresolvedRefCount`` 0 -- indistinguishable from a document with no external
  references at all;
- ``http://[::1`` made ``urlsplit`` raise ``ValueError("Invalid IPv6 URL")``,
  which escaped ``parse`` and discarded the entire document.

Nothing here fetches, and nothing here may start to: these tests drive the same
parser the socket watch in ``test_network_call_sites.py`` watches, and that watch
is what proves the recording stayed home.
"""

from __future__ import annotations

import json
from typing import Any, Final, cast

import pytest
from hypothesis import given, seed, settings
from hypothesis import strategies as st

from theurian.application.ingestion_service import _to_document
from theurian.domain.knowledge import SourceAnchor
from theurian.domain.values import ContentHash
from theurian.infrastructure.filesystem.parsers.openapi import (
    LOCAL_PATH_SCHEMES,
    MAX_REF_DEPTH,
    MAX_REFS,
    NETWORK_PATH_SCHEMES,
    OpenApiParser,
)
from theurian.infrastructure.filesystem.parsers.registry import OPENAPI

pytestmark = pytest.mark.unit

ANCHOR = SourceAnchor(provider="git", source_uri="git://demo/a", file_path="openapi.yaml")


def _index(document: dict[str, Any]) -> dict[str, Any]:
    parsed = OpenApiParser().parse(json.dumps(document).encode(), media_type=OPENAPI, anchor=ANCHOR)
    structured = cast("dict[str, Any]", parsed.structured)
    return cast("dict[str, Any]", structured["_index"])


def _metadata(document: dict[str, Any]) -> dict[str, str]:
    parsed = OpenApiParser().parse(json.dumps(document).encode(), media_type=OPENAPI, anchor=ANCHOR)
    return dict(parsed.metadata)


def _one_ref(ref: str) -> dict[str, Any]:
    """A minimal document whose only external reference is ``ref``."""
    return {"openapi": "3.1.0", "components": {"schemas": {"S": {"$ref": ref}}}}


def _record(ref: str) -> dict[str, str]:
    recorded = _index(_one_ref(ref))["externalRefs"]
    assert len(recorded) == 1, (
        f"the parser recorded {recorded} for {ref!r}, expected exactly one entry. "
        f"Fix the fixture before reading any assertion about the entry's fields: "
        f"a reference that is not recognised proves nothing by having no scheme."
    )
    return cast("dict[str, str]", recorded[0])


# ==========================================================================
# Scheme fidelity -- the #203 repro table
# ==========================================================================

#: Every row measured against the shipped parser. The four marked below are the
#: ones that changed in #203; the rest are here so a future edit to the
#: classifier cannot move them unremarked.
SCHEME_CASES: Final[tuple[tuple[str, str], ...]] = (
    ("https://evil.test/x.json", "https"),
    ("http://127.0.0.1:7419/health", "http"),
    ("file:///etc/passwd", "file"),
    ("urn:isbn:0451450523", "urn"),
    # Was `relative-file` (#203): a network-path reference names a host.
    ("//evil.test/x.json", "protocol-relative"),
    # Was `relative-file` (#203): UNC is Windows spelling the same thing.
    ("\\\\smb-host\\share\\x.json", "unc"),
    # Was the scheme `c` (#203): a drive letter is not a scheme.
    ("C:\\Windows\\system32\\x.json", "absolute-file"),
    ("c:/Windows/x.json", "absolute-file"),
    # Mixed separators, which Windows and every browser accept as `//`.
    ("/\\evil.test\\x.json", "protocol-relative"),
    ("\\/evil.test/x.json", "unc"),
    # An empty authority, read as though it had one: the failing-closed reading.
    ("///evil.test/x.json", "protocol-relative"),
    # A one-letter *scheme* that names an authority keeps its scheme, so the
    # drive-letter reading above cannot be used to smuggle a host past a gate.
    ("x://evil.test/y.json", "x"),
    # The same rule applied to a drive letter, and deliberate: what follows the
    # colon reads as an authority, so the drive restoration is suppressed and
    # these keep `c`. `c` is in neither published group, so an allowlist of
    # either refuses them -- wrong-looking, and fail-closed.
    ("C:\\\\Windows\\x.json", "c"),
    ("C://Windows/x.json", "c"),
    ("/etc/passwd", "absolute-file"),
    ("./local.yaml#/S", "relative-file"),
    ("../../secrets.yaml", "relative-file"),
    ("evil.test/x.json", "relative-file"),
    ("HTTPS://EVIL.TEST/x", "https"),
    # Every character of RFC 3986 §3.1's scheme production, each in a row of its
    # own: dropping `-`, `+` or `.` from the class turns these into local labels.
    ("x-scheme://evil.test/a.json", "x-scheme"),
    ("coap+tcp://evil.test/a.json", "coap+tcp"),
    ("soap.beep://evil.test/a.json", "soap.beep"),
    # The scheme is matched at the *start* or not at all. Unanchored, the colon
    # in the middle of this relative path reads as a scheme.
    ("./a:b.yaml", "relative-file"),
    # A single separator is a reference in its own right, and the check that
    # decides it needs exact membership: `"" in "/\\"` is True, so a `_SEPARATORS`
    # spelled as a string reads these as opening with two separators.
    ("/", "absolute-file"),
    ("\\", "absolute-file"),
    # Stripped by every URL parser before it looks, so stripped here before
    # anything classifies: each of these reaches the host `evil.test`.
    (" //evil.test/x.json", "protocol-relative"),
    ("\t//evil.test/x.json", "protocol-relative"),
    ("\n//evil.test/x.json", "protocol-relative"),
    ("/\t/evil.test/x.json", "protocol-relative"),
    # Removed *anywhere*, not only at the ends and not only tab: a newline or a
    # carriage return inside the reference is dropped by `urlsplit` too, so a
    # fetcher sees the host and the scheme these spell out.
    ("/\n/evil.test/x.json", "protocol-relative"),
    ("/\r/evil.test/x.json", "protocol-relative"),
    ("ht\ntps://evil.test/x.json", "https"),
    ("ht\rtps://evil.test/x.json", "https"),
    # `urlsplit` raised `ValueError` on this one and took the document with it.
    ("http://[::1", "http"),
)


@pytest.mark.parametrize(("ref", "expected"), SCHEME_CASES, ids=[case[0] for case in SCHEME_CASES])
def test_a_ref_records_the_scheme_a_fetcher_would_use(ref: str, expected: str) -> None:
    """The recorded ``scheme`` is what T-7's future allowlist will read (#129)."""
    assert _record(ref)["scheme"] == expected, (
        f"{ref!r} recorded scheme {_record(ref)['scheme']!r}, expected {expected!r}. "
        f"This field is what the Milestone 7 scheme allowlist keys on: a target "
        f"destined for a host recorded under a local-file label is that gate "
        f"failing open, which is why #203 was fixed before the gate was built."
    )


#: References that reach a *host*, spelled every way this parser has met. The
#: table above pins which label each gets; this pins the property that matters
#: whatever the labels are called.
NETWORK_DESTINED: Final[tuple[str, ...]] = (
    "//evil.test/x.json",
    "\\\\smb-host\\share\\x.json",
    "/\\evil.test\\x.json",
    "\\/evil.test/x.json",
    "///evil.test/x.json",
    " //evil.test/x.json",
    "\t//evil.test/x.json",
    "/\t/evil.test/x.json",
    "x://evil.test/y.json",
    "https://evil.test/x.json",
)


@pytest.mark.parametrize("ref", NETWORK_DESTINED, ids=list(NETWORK_DESTINED))
def test_no_network_destined_ref_records_a_local_file_scheme(ref: str) -> None:
    """The #203 invariant, stated independently of what the labels are named."""
    scheme = _record(ref)["scheme"]

    assert scheme not in LOCAL_PATH_SCHEMES, (
        f"{ref!r} names a host and recorded {scheme!r}, which is one of the "
        f"local-file labels {sorted(LOCAL_PATH_SCHEMES)}. An allowlist of local "
        f"schemes would pass it (T-7, #129)."
    )


def test_the_published_label_groups_hold_exactly_their_labels() -> None:
    """The contents, not only the disjointness.

    Only ``NETWORK_PATH_SCHEMES`` is load-bearing through an ``in`` above, so
    emptying ``LOCAL_PATH_SCHEMES`` -- or dropping ``absolute-file`` from it --
    left every other test in this file green while the group a future gate reads
    said something different. Both mutations are killed here.
    """
    assert sorted(LOCAL_PATH_SCHEMES) == ["absolute-file", "relative-file"]
    assert sorted(NETWORK_PATH_SCHEMES) == ["protocol-relative", "unc"]
    assert not (LOCAL_PATH_SCHEMES & NETWORK_PATH_SCHEMES)


def test_the_caps_are_the_numbers_the_documents_quote() -> None:
    """Asserted as literals, because every other test reads them through the
    symbol and so cannot see the cap move.

    ``MAX_REF_DEPTH`` 64 and ``MAX_REFS`` 5000 are quoted as numbers in this
    module's own comments, in `docs/security/threat-model.md` (T-7) and in the
    CHANGELOG, and "both walk caps stay where they were" is a claim #203 makes
    outright. Halving either kept the whole suite green.
    """
    assert MAX_REF_DEPTH == 64
    assert MAX_REFS == 5000


#: ``derandomize`` alone is not determinism: hypothesis derives the corpus from
#: the test's own identity, so *editing this docstring* reshuffles which examples
#: run -- measured at 41 of 250 shared with the previous corpus. ``@seed`` is what
#: makes the examples a property of the decorator instead, which is why
#: ``test_absence_proof.py`` pins ``EXAMPLE_SEED`` rather than relying on
#: ``derandomize``. The number is the issue, as it is there.
_GENERATED = settings(deadline=None, derandomize=True, database=None, max_examples=250)

#: The four spellings of RFC 3986 §3.2's authority prefix, including the two
#: mixed forms Windows and browsers accept.
_AUTHORITY_PREFIXES = ("//", "\\\\", "/\\", "\\/")


@seed(203)
@_GENERATED
@given(
    prefix=st.sampled_from(_AUTHORITY_PREFIXES),
    tail=st.text(alphabet="/\\.:-aCx", min_size=0, max_size=6),
)
def test_a_reference_opening_with_two_separators_is_never_local(prefix: str, tail: str) -> None:
    """The classification is structural, so it must hold past the table above.

    An enumerated blocklist of ``//`` and ``\\`` would satisfy every case in
    :data:`SCHEME_CASES` and still let the next spelling through.

    The prefix is composed rather than generated so that **every** example bears
    the claim. Drawing the whole reference from an alphabet reached the
    two-separator branch in 10 of 250 examples, which left the other 240 paying
    for a conditional that asserted nothing -- a corpus can be large and still
    test one thing forty times.

    Whitespace is deliberately outside the tail's alphabet: this test states its
    premise on the raw string, and it could not do that if the string had to be
    normalised first. The normalising cases are pinned by name in
    :data:`SCHEME_CASES` instead.
    """
    ref = prefix + tail
    scheme = _record(ref)["scheme"]

    assert scheme in NETWORK_PATH_SCHEMES, (
        f"{ref!r} opens with two separators, so it names an authority "
        f"(RFC 3986 §3.2) -- it recorded {scheme!r}, which is not one of "
        f"{sorted(NETWORK_PATH_SCHEMES)}."
    )


@seed(203)
@_GENERATED
@given(ref=st.text(alphabet="/\\.:-aCx", min_size=1, max_size=6))
def test_every_reference_records_a_usable_scheme_label(ref: str) -> None:
    """What holds for *any* reference, so every example bears this claim too.

    A gate keying on an empty string, or on a label whose case depends on how the
    document spelled it, is a gate keying on nothing. Separate from the test
    above because these two claims have different populations, and folding them
    together is what made 240 of 250 examples assert only this much.
    """
    scheme = _record(ref)["scheme"]

    assert scheme, f"{ref!r} recorded an empty scheme label"
    assert scheme == scheme.lower(), f"{ref!r} recorded {scheme!r}, which is not lowercased"


@seed(203)
@_GENERATED
@given(
    first=st.sampled_from("abcxzABXZ"),
    rest=st.text(alphabet="abcxz019+-.", min_size=0, max_size=8),
)
def test_a_scheme_bearing_reference_records_that_scheme(first: str, rest: str) -> None:
    """RFC 3986 §3.1's whole character class, generated rather than sampled.

    The table pins one row per character; this pins the class. Dropping ``-``,
    ``+`` or ``.`` from the pattern makes the scheme unmatchable and the whole
    reference read as a local relative path -- which is the fail-open direction
    for a hostile input, since these all name ``evil.test``.
    """
    scheme = first + rest
    record = _record(f"{scheme}://evil.test/a.json")

    assert record["scheme"] == scheme.lower(), (
        f"a reference opening {scheme}:// recorded {record['scheme']!r}"
    )
    assert record["scheme"] not in LOCAL_PATH_SCHEMES, (
        f"{scheme}://evil.test/a.json names a host and recorded a local-file label"
    )


def test_a_ref_inside_an_array_is_recorded_with_its_index_path() -> None:
    """The list branch of the walk, which no other test drove.

    ``parameters: [{"$ref": ...}]`` is the commonest place a real OpenAPI
    document puts one, so deleting that branch removes recording for the shape
    most likely to carry a hostile reference -- and every other test here kept
    passing, because all of them put their ``$ref`` under a mapping.
    """
    document = {
        "openapi": "3.1.0",
        "paths": {
            "/orders": {
                "get": {"parameters": [{"$ref": "https://evil.test/param.json"}]},
            }
        },
        "nested": [[{"$ref": "https://evil.test/deep.json"}]],
    }

    by_ref = {record["ref"]: record for record in _index(document)["externalRefs"]}

    assert set(by_ref) == {"https://evil.test/param.json", "https://evil.test/deep.json"}
    assert by_ref["https://evil.test/param.json"]["at"] == "paths./orders.get.parameters[0]"
    assert by_ref["https://evil.test/deep.json"]["at"] == "nested[0][0]", (
        "an index appears per level, so a reader can find the reference again"
    )


def test_the_same_reference_written_twice_is_recorded_once() -> None:
    """The record is per distinct reference string, which is what
    ``unresolvedRefCount`` counts. Without the dedup a document repeating one
    ``$ref`` inflates the count and fills the cap with copies."""
    repeated = "https://evil.test/x.json"
    document = {
        "openapi": "3.1.0",
        "components": {"schemas": {"A": {"$ref": repeated}, "B": {"$ref": repeated}}},
    }

    recorded = _index(document)["externalRefs"]

    assert [record["ref"] for record in recorded] == [repeated]
    assert recorded[0]["at"] == "components.schemas.A", "the first occurrence is the one kept"
    assert _metadata(document)["unresolvedRefCount"] == "1"


def test_a_file_url_with_a_host_records_its_scheme_and_leaves_the_authority_alone() -> None:
    """A recorded decision, not an oversight (T-7).

    ``file://evil.test/share/x.json`` is network-destined and records ``file``,
    because that *is* its scheme and this function's job is to say what the
    reference is, not to decide what may be fetched. A gate that allows ``file``
    at all has to inspect the authority -- exactly as it has to inspect the path
    of a ``file:///etc/shadow``, which is equally local and equally unwanted.
    ``docs/security/threat-model.md`` (T-7) records the same residual.
    """
    assert _record("file://evil.test/share/x.json")["scheme"] == "file"


def test_the_reference_itself_is_recorded_verbatim() -> None:
    """Classification normalises; the record does not.

    A reader chasing a reported reference has to find the string the document
    actually contains, and the whitespace that changes how it is classified is
    part of what makes it worth looking at.
    """
    ref = "\t//evil.test/x.json"
    record = _record(ref)

    assert record["ref"] == ref
    assert record["scheme"] == "protocol-relative"


@pytest.mark.parametrize(
    "ref",
    ["#/components/schemas/Other", " #/components/schemas/Other", "", "   "],
    ids=["fragment", "padded-fragment", "empty", "blank"],
)
def test_a_same_document_reference_is_not_recorded_as_external(ref: str) -> None:
    """RFC 3986 §4.4: the empty reference means *this* document, as a fragment
    does. Neither can send anyone anywhere, so neither is worth a note."""
    assert _index(_one_ref(ref))["externalRefs"] == []


# ==========================================================================
# The walk caps -- bounded, and no longer silent
# ==========================================================================


def _buried(depth: int, leaf: object) -> dict[str, Any]:
    """``leaf`` under ``depth`` mappings.

    Takes the leaf whole rather than always burying a ``$ref``: the cap tests
    below turn on *what kind of node* the walk declines to enter, and a helper
    that wrapped everything in ``{"$ref": ...}`` would hand an "empty container"
    case a container holding one key. It did, on the first run of the HIGH-1
    pins, and the product was right where the fixture was wrong.
    """
    node: object = leaf
    for _ in range(depth):
        node = {"x": node}
    return cast("dict[str, Any]", node)


def _nested(depth: int, ref: str) -> dict[str, Any]:
    """A ``$ref`` buried under ``depth`` mappings."""
    return _buried(depth, {"$ref": ref})


def test_a_ref_past_the_depth_cap_is_counted_rather_than_dropped() -> None:
    """#203's silent half: the count said 0, and 0 is what a reader acts on."""
    document = {"openapi": "3.1.0", "deep": _nested(200, "https://evil.test/deep.json")}

    index = _index(document)
    metadata = _metadata(document)

    assert index["externalRefs"] == [], "the cap still stops the walk descending"
    assert metadata["unresolvedRefCount"] != "0", (
        "a document whose references all sit past MAX_REF_DEPTH reported "
        "`unresolvedRefCount` 0 -- the same answer a document with no external "
        "references gives (#203). The cap is allowed to stop the walk; it is not "
        "allowed to make the document look clean."
    )
    assert metadata["refWalkTruncated"] == "true"
    assert [cut["reason"] for cut in index["refWalkTruncations"]] == ["depth"]
    assert index["refWalkTruncations"][0]["limit"] == str(MAX_REF_DEPTH)
    assert index["refWalkTruncations"][0]["at"].startswith("deep.x"), (
        "the truncation names where the walk stopped, so the subtree can be found"
    )


def test_the_depth_cap_is_where_it_was_and_marks_nothing_below_it() -> None:
    """The caps themselves do not move, and a document that lost nothing must
    not claim it did -- the boundary case where a ``$ref`` sits at exactly the
    limit and its own string value is the node the walk declines to enter."""
    at_the_cap = {"openapi": "3.1.0", "deep": _nested(MAX_REF_DEPTH - 1, "https://evil.test/x")}
    past_it = {"openapi": "3.1.0", "deep": _nested(MAX_REF_DEPTH, "https://evil.test/x")}

    index = _index(at_the_cap)
    assert [ref["ref"] for ref in index["externalRefs"]] == ["https://evil.test/x"]
    assert index["refWalkTruncations"] == [], (
        "nothing was cut: the deepest node the walk declined to enter was the "
        "reference's own string value, which can hold no reference of its own."
    )
    assert _metadata(at_the_cap)["refWalkTruncated"] == "false"

    assert _index(past_it)["externalRefs"] == [], "one level deeper is still cut"


@pytest.mark.parametrize(
    ("leaf", "label"),
    [({}, "empty dict"), ([], "empty list"), ("text", "scalar")],
    ids=["empty-dict", "empty-list", "scalar"],
)
def test_a_cap_does_not_claim_it_cut_a_node_that_could_hide_nothing(
    leaf: object, label: str
) -> None:
    """Round-one HIGH-1, reproduced: an empty container past the depth cap made a
    document with *no* external references publish ``unresolvedRefCount`` 1 and
    ``refWalkTruncated`` true -- a warning about a subtree that was provably
    empty. The scalar case was already right, and is kept here so the three sit
    under one statement: emptiness is answerable without descending, so it is
    answerable in front of a cap that forbids descending."""
    document = {"openapi": "3.1.0", "deep": _buried(MAX_REF_DEPTH, leaf)}

    assert _index(document)["refWalkTruncations"] == [], f"an {label} hides nothing"
    assert _metadata(document)["unresolvedRefCount"] == "0"
    assert _metadata(document)["refWalkTruncated"] == "false"


def test_a_non_empty_node_past_the_cap_is_still_marked() -> None:
    """The other side of HIGH-1's fix, so it cannot be satisfied by never marking.

    A container that holds *something* stays marked even when what it holds is a
    scalar: knowing better means reading its children, which is the descent the
    cap refused, so "we did not look" is the honest answer.
    """
    hiding_a_ref = {
        "openapi": "3.1.0",
        "deep": _buried(MAX_REF_DEPTH, {"$ref": "https://evil.test/x.json"}),
    }
    holding_only_a_scalar = {"openapi": "3.1.0", "deep": _buried(MAX_REF_DEPTH, {"a": 1})}

    for document in (hiding_a_ref, holding_only_a_scalar):
        assert [cut["reason"] for cut in _index(document)["refWalkTruncations"]] == ["depth"]
        assert _metadata(document)["refWalkTruncated"] == "true"


def _exactly_at_the_ref_cap(**extra: object) -> dict[str, Any]:
    """A document holding exactly ``MAX_REFS`` references, plus ``extra`` keys.

    The extra keys sort after ``components``, so the walk reaches them with the
    cap already full -- which is the boundary M-3 asked for and the one the
    round-one fix had to get right in both directions.
    """
    return {
        "openapi": "3.1.0",
        "components": {
            "schemas": {
                f"S{index}": {"$ref": f"https://evil.test/{index}.json"}
                for index in range(MAX_REFS)
            }
        },
        **extra,
    }


@pytest.mark.parametrize(
    ("extra", "label"),
    [({}, "nothing after"), ({"zz": "text"}, "trailing scalar"), ({"zz": {}}, "trailing empty")],
    ids=["nothing-after", "trailing-scalar", "trailing-empty"],
)
def test_exactly_the_ref_cap_is_an_exact_count(extra: dict[str, object], label: str) -> None:
    """``MAX_REFS`` refs and nothing that could hold a further one is a *total*.

    Before the HIGH-1 fix the trailing-empty case reported 5001 and
    ``refWalkTruncated`` true: the cap was full, the next node was an empty
    mapping, and the marker fired on a subtree that could hide nothing.
    """
    document = _exactly_at_the_ref_cap(**extra)

    index = _index(document)
    metadata = _metadata(document)

    assert len(index["externalRefs"]) == MAX_REFS
    assert index["refWalkTruncations"] == [], f"with {label}, nothing was cut"
    assert metadata["unresolvedRefCount"] == str(MAX_REFS)
    assert metadata["refWalkTruncated"] == "false"


def test_exactly_the_ref_cap_with_more_to_look_at_is_a_floor() -> None:
    """The same boundary from the other side: a node that *could* hold a
    reference is reached with the cap full, so the walk stopped without knowing,
    and the count says so."""
    document = _exactly_at_the_ref_cap(zz={"a": 1})

    index = _index(document)
    metadata = _metadata(document)

    assert len(index["externalRefs"]) == MAX_REFS
    assert [cut["reason"] for cut in index["refWalkTruncations"]] == ["refCount"]
    assert metadata["unresolvedRefCount"] == str(MAX_REFS + 1)
    assert metadata["refWalkTruncated"] == "true"


def test_the_ref_cap_records_that_it_stopped_counting() -> None:
    """``MAX_REFS`` is a bound by design (#203) -- but a bound that reports its
    ceiling as a total is a document claiming it holds exactly 5000."""
    over_the_cap = MAX_REFS + 25
    document = {
        "openapi": "3.1.0",
        "components": {
            "schemas": {
                f"S{index}": {"$ref": f"https://evil.test/{index}.json"}
                for index in range(over_the_cap)
            }
        },
    }

    index = _index(document)
    metadata = _metadata(document)

    assert len(index["externalRefs"]) == MAX_REFS, "the cap does not move"
    assert [cut["reason"] for cut in index["refWalkTruncations"]] == ["refCount"]
    assert index["refWalkTruncations"][0]["limit"] == str(MAX_REFS)
    assert metadata["refWalkTruncated"] == "true"
    assert metadata["unresolvedRefCount"] == str(MAX_REFS + 1), (
        "the count carries the truncation, so it reads as a floor rather than a "
        "total; `refWalkTruncated` is what says which it is."
    )


def test_a_document_within_both_caps_reports_an_exact_count() -> None:
    """The other side of the flag: without this, `refWalkTruncated` could be
    hard-coded ``true`` and every assertion above would still pass."""
    document = {
        "openapi": "3.1.0",
        "components": {
            "schemas": {
                "A": {"$ref": "https://evil.test/a.json"},
                "B": {"$ref": "./b.yaml"},
            }
        },
    }

    metadata = _metadata(document)

    assert metadata["unresolvedRefCount"] == "2"
    assert metadata["refWalkTruncated"] == "false"
    assert _index(document)["refWalkTruncations"] == []


def test_the_parser_metadata_stops_at_the_parser_boundary() -> None:
    """A recorded decision, pinned so a future change has to face it.

    ``unresolvedRefCount`` and ``refWalkTruncated`` are useful at the parser's
    own boundary and go no further: ``_to_document`` carries ``structured`` into
    an ``IngestedDocument`` that has no metadata field, and nothing in ``src/``
    reads either value. Threading parser metadata through the ingestion port to
    carry a value no consumer wants would widen that port for nothing, so the
    downstream record is deliberately ``structured["_index"]`` --
    ``refWalkTruncations`` is non-empty for exactly the documents the flag calls
    truncated, which is the same fact in the form that survives.

    If this goes red, the decision changed: say so in
    ``docs/security/threat-model.md`` (T-7), which states it.
    """
    normalized = OpenApiParser().parse(
        json.dumps({"openapi": "3.1.0", "deep": _buried(MAX_REF_DEPTH, {"a": 1})}).encode(),
        media_type=OPENAPI,
        anchor=ANCHOR,
    )
    assert normalized.metadata["refWalkTruncated"] == "true", (
        "the parser must publish the flag for this fixture, or the assertion "
        "below proves nothing by finding it absent downstream"
    )

    document = _to_document(
        normalized,
        path="openapi.yaml",
        source_hash=ContentHash.of_text("raw bytes"),
        parser=OpenApiParser(),
        warnings=(),
    )

    assert not hasattr(document, "metadata"), (
        "IngestedDocument grew a metadata field. If the parser's counts now "
        "travel with it, T-7's statement that they stop at the parser boundary "
        "is false and the threat model needs correcting in the same change."
    )
    index = cast("dict[str, Any]", document.structured)["_index"]
    assert [cut["reason"] for cut in index["refWalkTruncations"]] == ["depth"], (
        "the fact itself survives ingestion, in the field that does travel"
    )


def test_a_malformed_ipv6_ref_does_not_discard_the_document() -> None:
    """Measured before the fix: ``urlsplit`` raised ``ValueError("Invalid IPv6
    URL")`` from inside the recording branch, that exception left ``parse``, and
    the caller lost the whole document -- operations, schemas, and every other
    reference in it -- over one malformed string it did not write."""
    document = {
        "openapi": "3.1.0",
        "paths": {"/a": {"get": {"operationId": "getA", "responses": {"200": {}}}}},
        "components": {
            "schemas": {
                "Bad": {"$ref": "http://[::1"},
                "Good": {"$ref": "https://evil.test/x.json"},
            }
        },
    }

    index = _index(document)

    assert index["operationIds"] == ["getA"], "the rest of the document survives"
    assert {ref["ref"] for ref in index["externalRefs"]} == {
        "http://[::1",
        "https://evil.test/x.json",
    }
