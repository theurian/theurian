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
from hypothesis import given, settings
from hypothesis import strategies as st

from theurian.domain.knowledge import SourceAnchor
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
    ("/etc/passwd", "absolute-file"),
    ("./local.yaml#/S", "relative-file"),
    ("../../secrets.yaml", "relative-file"),
    ("evil.test/x.json", "relative-file"),
    ("HTTPS://EVIL.TEST/x", "https"),
    # Stripped by every URL parser before it looks, so stripped here before
    # anything classifies: each of these reaches the host `evil.test`.
    (" //evil.test/x.json", "protocol-relative"),
    ("\t//evil.test/x.json", "protocol-relative"),
    ("\n//evil.test/x.json", "protocol-relative"),
    ("/\t/evil.test/x.json", "protocol-relative"),
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


def test_the_local_and_network_ref_labels_are_disjoint() -> None:
    """Two sets that overlap would make the assertion above unfalsifiable."""
    assert not (LOCAL_PATH_SCHEMES & NETWORK_PATH_SCHEMES)


#: Deterministic for the reason ``test_absence_proof.py`` gives: a generated
#: example that only sometimes runs is a pin that only sometimes holds.
_GENERATED = settings(deadline=None, derandomize=True, database=None, max_examples=250)


@_GENERATED
@given(ref=st.text(alphabet="/\\.:-aCx", min_size=1, max_size=6))
def test_a_reference_opening_with_two_separators_is_never_local(ref: str) -> None:
    """The classification is structural, so it must hold past the table above.

    An enumerated blocklist of ``//`` and ``\\`` would satisfy every case in
    :data:`SCHEME_CASES` and still let the next spelling through. The alphabet
    carries both separators, a colon, a dot, a dash and three letters -- one of
    them a plausible drive letter -- so the generated corpus reaches the mixed
    separators, the one-letter scheme, and the scheme-with-no-authority forms.

    Whitespace is deliberately *outside* the alphabet: this test states its
    premise on the raw string, and it could not do that if the string it
    generated had to be normalised first. The normalising cases are pinned by
    name in :data:`SCHEME_CASES` instead.
    """
    scheme = _record(ref)["scheme"]

    assert scheme, f"{ref!r} recorded an empty scheme label"
    assert scheme == scheme.lower(), f"{ref!r} recorded {scheme!r}, which is not lowercased"
    # Membership in a *tuple*, not in the string "/\\": `"" in "/\\"` is True,
    # so the string spelling reads a one-character reference as opening with two
    # separators. Hypothesis found that on the first run, against `ref="\\"`.
    if ref[:1] in ("/", "\\") and ref[1:2] in ("/", "\\"):
        assert scheme in NETWORK_PATH_SCHEMES, (
            f"{ref!r} opens with two separators, so it names an authority "
            f"(RFC 3986 §3.2) -- it recorded {scheme!r}, which is not one of "
            f"{sorted(NETWORK_PATH_SCHEMES)}."
        )


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


def _nested(depth: int, ref: str) -> dict[str, Any]:
    """A ``$ref`` buried under ``depth`` mappings."""
    node: dict[str, Any] = {"$ref": ref}
    for _ in range(depth):
        node = {"x": node}
    return node


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
