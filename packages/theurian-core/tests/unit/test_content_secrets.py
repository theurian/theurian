"""The content secret detector SEC-11 ships (ADR-0027 decision 3, #198).

Its sibling ``test_secret_detector.py`` guards the *plugin tree* and owns the
tuning these tests inherit -- the entropy floor's position, why each character
class is load-bearing, and the measurement behind every number quoted there.
This module is about the detector that runs at ``theurian propose accept`` over a
different population: bodies a contributor wrote, in prose, JSON and YAML.

**The failure mode this class has is silence.** A detector that answered
``False`` for everything would report a clean proposal over a pasted credential
for as long as anyone cared to look, and every "no secrets here" assertion in the
suite would keep passing. So the positives come first and are per family, and
:func:`test_the_detector_can_fail` is the one that says the scan executes the
branch it exists for.

Pure: it constructs strings and calls one function. No filesystem, no database.
"""

from __future__ import annotations

import base64
import hashlib
import math
import string
from collections import Counter
from typing import Final

import pytest

from theurian.security.content_secrets import (
    FAMILIES,
    HIGH_ENTROPY,
    MAX_FINDINGS,
    REDACTED_PREFIX_CHARS,
    SecretFinding,
    scan_text,
)

pytestmark = pytest.mark.unit


def _entropy(token: str) -> float:
    """Bits per character, computed here so a fixture's figure can be asserted.

    A second implementation of the arithmetic on purpose, for the reason
    ``test_secret_detector.py`` gives: a fixture's recorded entropy is a claim
    about the number, and reading it back out of the detector would make every
    such claim true by construction.
    """
    counts = Counter(token)
    return -sum((n / len(token)) * math.log2(n / len(token)) for n in counts.values())


#: A fixed byte string, hashed into :data:`TOKEN_SHAPED`. Nothing about the value
#: matters except that it never changes.
_FIXTURE_SEED: Final = b"theurian content-secrets fixture (#198, ADR-0027 decision 3)"

#: A 43-character base64url string derived from :data:`_FIXTURE_SEED` rather than
#: drawn, which is the shape ``secrets.token_urlsafe(32)`` produces -- a SHA-256
#: digest is 32 bytes, exactly what that call encodes.
#:
#: Derived rather than pasted for the reason ``test_secret_detector.py`` measured
#: at length: a fresh draw contains no digit in 0.065% of runs, which reddens the
#: suite for nothing and, inside a mutation sweep, turns a *surviving* mutant into
#: a false claim that something is pinned. A pasted literal would be deterministic
#: too, and the problem with one is that no reviewer can tell a fixture from a
#: credential somebody leaked by looking.
TOKEN_SHAPED: Final = (
    base64.urlsafe_b64encode(hashlib.sha256(_FIXTURE_SEED).digest()).decode().rstrip("=")
)

#: ``(family, the prefix that identifies it, the characters that follow)``.
#:
#: **Split into two literals, and that is not style.** The `Secret scan` job in
#: ``security.yml`` runs gitleaks' default ruleset over this repository's whole
#: history, and a contiguous ``AKIA`` followed by sixteen upper-case characters is
#: exactly what its ``aws-access-token`` rule looks for; the same holds for
#: ``ghp_``, ``AIza``, ``sk-``, ``xox``, ``sk_live_`` and a private key header.
#: Written whole, each of these would need an allowlist entry in
#: ``.gitleaks.toml`` -- and an allowlist keyed on a credential-shaped literal is
#: a place a real credential can hide. Joined at run time, there is no literal for
#: either scanner to judge, which is the reasoning that file already records for
#: its sibling's token fixture.
#:
#: Every value is obviously unreal on inspection: ascending digit runs and the
#: alphabet in order. None is or has been a credential.
PATTERN_FAMILY_FIXTURES: Final[tuple[tuple[str, str, str], ...]] = (
    ("aws-access-key-id", "AKIA", "WXYZ0123456789PQ"),
    ("github-token", "ghp_", "aBcDeFgHiJkLmNoPqRsTuVwXyZ0123456789"),
    ("google-api-key", "AIza", "SyB0123456789abcdefghijklmnopqrstuv"),
    ("openai-api-key", "sk-", "proj0123456789abcdefghij"),
    ("private-key-block", "-----BEGIN ", "RSA PRIVATE KEY-----"),
    ("slack-token", "xoxb-", "0123456789-0123456789-abcdefghijkl"),
    ("stripe-secret-key", "sk_live_", "0123456789abcdef"),
)

#: Strings that must never be reported, with why each one is refused.
#:
#: The first four are not invented. They are measured against this repository on
#: 2026-08-24: a real migration filename and a real generated body path, both of
#: which the detector reported as secrets before Theurian's own ULIDs were
#: subtracted from a candidate, and two identifier shapes that a knowledge
#: document quotes constantly.
#:
#: A false positive costs the same as a false negative, in trust: ``block`` is the
#: default policy, so a detector that fires on the product's own filenames refuses
#: acceptances until somebody turns the control off.
NEGATIVES: Final[tuple[tuple[str, str], ...]] = (
    (
        "a real migration filename",
        "01M0D5GSKA479Y85296S745521-adr-0001-monorepo-with-independently-released-artifacts",
    ),
    ("a generated body path", "architecture/retry-policy-01K1AAAAAA01234567890ABCDE.md"),
    ("a bare ULID", "01K1AAAAAA01234567890ABCDE"),
    ("an ADR filename", "0027-accept-validates-before-it-moves"),
    ("a sha256 digest", "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"),
    ("a git commit sha", "0123456789abcdef0123456789abcdef01234567"),
    ("a uuid", "550e8400-e29b-41d4-a716-446655440000"),
    ("a long test name", "test_no_registered_tool_can_reach_a_canonical_write"),
    ("an issue link", "https://github.com/theurian/theurian/issues/198"),
    ("a screaming-snake constant", "MAX_SOURCE_FILE_BYTES_AND_MAX_PROJECTION_CHARS"),
    # Upper, lower and digit all present, so the class gate passes and the
    # entropy floor is the only thing that can refuse it: three distinct
    # characters repeated is log2(3) = 1.585 bits.
    ("a low-entropy candidate", "Aa1" * 12),
)


def test_the_detector_can_fail() -> None:
    """A detector nobody has proved works is a scan that always passes.

    Every "this body is clean" assertion in the suite is worth exactly what this
    one is: a :func:`scan_text` that returned ``()`` unconditionally would let a
    pasted token through ``theurian propose accept`` with the policy on ``block``
    and nothing anywhere would say so. This is the assertion that stops it.
    """
    findings = scan_text(f"# notes\n\nleft over from debugging: TOKEN={TOKEN_SHAPED}\n")

    assert [f.family for f in findings] == [HIGH_ENTROPY], (
        f"the detector no longer fires on a 43-character base64url string with mixed case, "
        f"a digit and {_entropy(TOKEN_SHAPED):.4f} bits of entropy per character -- the shape "
        f"`secrets.token_urlsafe(32)` produces, which is what a leaked Theurian token in a "
        f"proposal body would look like. It reported {[f.family for f in findings]}."
    )


@pytest.mark.parametrize(
    ("family", "prefix", "tail"),
    PATTERN_FAMILY_FIXTURES,
    ids=[case[0] for case in PATTERN_FAMILY_FIXTURES],
)
def test_each_pattern_family_reports_its_own_shape(family: str, prefix: str, tail: str) -> None:
    """One positive per family, so no family can be deleted without a red test.

    A prefix outranks entropy where one exists: ``ghp_`` followed by 36
    characters is a GitHub token whatever its character frequencies say, and
    ``AKIA`` plus sixteen is twenty characters -- well under the generic
    candidate floor, so nothing else here would report it at all.

    The family is asserted rather than the count, because the alternation's
    *order* is what keeps a specific family from being reported as
    ``high-entropy-token``. Deleting a family and letting the generic one pick up
    the slack would keep a count-only assertion green for four of the seven, and
    would silently lose the other three.
    """
    findings = scan_text(f"config:\n  key: {prefix}{tail}\n")

    assert [f.family for f in findings] == [family], (
        f"a {family} fixture was reported as {[f.family for f in findings]}. Either the "
        f"family is gone, or it is now declared after `{HIGH_ENTROPY}` in the alternation, "
        f"which makes the generic branch win at the same position."
    )


@pytest.mark.parametrize(
    "candidate", [value for _, value in NEGATIVES], ids=[n for n, _ in NEGATIVES]
)
def test_the_detector_ignores_a_string_a_knowledge_document_really_contains(candidate: str) -> None:
    """A false positive costs the same as a false negative, in trust.

    The first four rows are measurements rather than guesses. Before Theurian's
    own ULIDs were subtracted from a candidate, **all 26 committed migration
    filenames in this repository were reported as secrets** (4.59 to 4.95 bits),
    and so was every generated body path -- both of which a knowledge document
    quotes as a matter of course. With ``block`` as the default policy, that is
    an acceptance refused for a filename the product minted itself, which is how
    a control gets switched off.
    """
    findings = scan_text(f"The document names {candidate} in passing.\n")

    assert findings == (), (
        f"{candidate!r} is reported as a secret: "
        f"{[(f.family, f.redacted) for f in findings]}. Secret scanning blocks acceptances "
        f"by default, so a detector that fires on the identifiers a knowledge document "
        f"quotes is one an operator turns off."
    )


# A standing sweep of this repository's own knowledge bodies is deliberately
# *not* here, and the reason is a hazard rather than a preference. The dogfood
# project's working tree holds 82 bodies -- the 26 tracked ones and 56
# machine-local operator notes fenced in `.git/info/exclude` -- so a walker over
# `.theurian/knowledge/` scans a different population on a maintainer's machine
# than it does in CI, and a private note is exactly where a real credential would
# be pasted. That is a red nobody else can reproduce. The measurement it would
# hold is recorded instead: zero findings across all 26 tracked bodies, all of
# `docs/`, and `examples/`, on 2026-08-24 -- and `docs/` was *not* zero before the
# ULID subtraction, which is why the two shapes that tripped it are pinned in
# NEGATIVES above rather than left to a walk.


def test_a_finding_never_carries_more_than_the_published_prefix() -> None:
    """The bound that makes a finding safe to print, and to log.

    A refusal reaches a terminal and, under ``warn``, an ``accept --json``
    document something will keep. Four characters of a base64url token is 24
    bits: a locator, not a recovery. This is what stops the report from becoming
    a second copy of what it reports.
    """
    (finding,) = scan_text(f"TOKEN={TOKEN_SHAPED}\n")

    assert finding.redacted == f"{TOKEN_SHAPED[:REDACTED_PREFIX_CHARS]}...", (
        f"a finding quotes {finding.redacted!r} of a {len(TOKEN_SHAPED)}-character token"
    )
    assert TOKEN_SHAPED not in finding.describe(at="body.md"), (
        f"the rendered finding contains the whole token: {finding.describe(at='body.md')!r}"
    )


def test_a_finding_refuses_to_be_built_with_a_longer_prefix() -> None:
    """The limit above is enforced at construction, not by whoever remembers it.

    Raising :data:`REDACTED_PREFIX_CHARS` is a decision somebody takes here. A
    caller assembling its own finding with half the credential in it is not, and
    without this the class would be a convention rather than a bound.
    """
    with pytest.raises(ValueError, match="over the"):
        SecretFinding(family=HIGH_ENTROPY, line=1, column=1, redacted=TOKEN_SHAPED)


def test_a_finding_refuses_a_family_the_scanner_does_not_declare() -> None:
    """A family name reaches a published JSON document, so it is a closed set."""
    with pytest.raises(ValueError, match="not a family"):
        SecretFinding(family="totally-made-up", line=1, column=1, redacted="Ab1...")

    assert HIGH_ENTROPY in FAMILIES, "the generic family is not in the published set"


def test_a_finding_locates_itself_by_line_and_column() -> None:
    """Both are needed, and "line" alone is the one that looks sufficient.

    A JSON or YAML body is routinely one long line, so a report that gave only a
    line number would point at the whole document. The numbers are 1-based, which
    is what an editor shows.
    """
    body = f"---\nfirst: ok\nsecond: TOKEN={TOKEN_SHAPED}\n"

    (finding,) = scan_text(body)

    assert (finding.line, finding.column) == (3, len("second: TOKEN=") + 1), (
        f"the finding claims line {finding.line}, column {finding.column}"
    )


#: A fixed number of candidates, well over the ceiling and independent of it, so
#: the ceiling test below cannot pass by having its fixture scale with the
#: constant it checks (adversarial M-1).
_CANDIDATES_OVER_THE_CEILING: Final = 50


def test_the_scan_stops_at_a_fixed_ceiling() -> None:
    """The list a caller renders is bounded at a *fixed* number, not one the input scales.

    ``MAX_FINDINGS`` is the only bound on ``scan_text``'s quadratic line-number
    cost: every finding pays ``text.count("\\n", 0, start)``, O(position), so N
    findings over a large body is O(N x bytes) -- a quarter-million candidates
    over a published-size document was measured at 319 s. The cap is what stops
    that, and it is a *number*, not a ratio: asserted as the absolute 20 rather
    than as ``MAX_FINDINGS``, because a fixture and an expectation that both read
    the constant pass however high it is raised (a mutation to 1000 survived that
    shape). Raising the ceiling now lands here, which is where the cost the ceiling
    protects gets re-measured.
    """
    assert _CANDIDATES_OVER_THE_CEILING > MAX_FINDINGS, (
        "the fixture must carry more candidates than the ceiling, or it exercises no truncation "
        "-- raising MAX_FINDINGS past the fixture size lands here"
    )
    crowded = "\n".join(f"TOKEN_{n}={TOKEN_SHAPED}" for n in range(_CANDIDATES_OVER_THE_CEILING))

    findings = scan_text(crowded)

    assert len(findings) == 20, (
        f"{len(findings)} findings came back from a body holding "
        f"{_CANDIDATES_OVER_THE_CEILING} candidates; the ceiling is a fixed 20 and raising it "
        f"is a decision that re-measures the quadratic line-number cost it bounds"
    )


def test_findings_come_back_in_document_order() -> None:
    """A total order, so two runs over one body produce one report.

    The scan is a single non-overlapping left-to-right pass, which is what makes
    document order total: no two findings can share a position. A refusal message
    that reordered between runs would make two identical acceptances read as two
    different faults.
    """
    prefixed = "".join(f"line {n}: {p}{t}\n" for n, (_, p, t) in enumerate(PATTERN_FAMILY_FIXTURES))

    findings = scan_text(prefixed)

    assert [f.line for f in findings] == sorted(f.line for f in findings)
    assert [f.family for f in findings] == [family for family, _, _ in PATTERN_FAMILY_FIXTURES]


def test_the_entropy_floor_is_where_the_detector_says_it_is() -> None:
    """Where the floor sits is a tuning decision, so moving it should take one.

    Sixteen distinct characters twice each is uniform over sixteen symbols, so
    exactly 4.0 bits; fifteen gives log2(15) = 3.9069. The pair pins the constant
    to 0.093 bits and pins the comparison as ``>=`` rather than ``>``. Both are
    padded past the 32-character candidate floor by repetition, which does not
    move a uniform string's entropy.

    Deliberately tight, for the reason its sibling in ``test_secret_detector.py``
    records: a heuristic threshold is exactly the kind of value that gets nudged
    to silence a false positive, and nudging it silences true positives too.
    """
    at_the_floor = (string.ascii_uppercase[:6] + string.ascii_lowercase[:6] + "0123") * 2
    under_the_floor = (string.ascii_uppercase[:6] + string.ascii_lowercase[:6] + "012") * 2

    assert round(_entropy(at_the_floor), 4) == 4.0, "the at-floor fixture is not at 4.0 bits"
    assert round(_entropy(under_the_floor), 4) == 3.9069, "the under-floor fixture moved"
    assert scan_text(at_the_floor), (
        f"{at_the_floor!r} carries exactly 4.0 bits, which the detector documents as "
        "detectable; it is now refused, so either the floor has been raised or the "
        "comparison has become exclusive"
    )
    assert not scan_text(under_the_floor), (
        f"{under_the_floor!r} carries 3.9069 bits, under the documented 4.0 floor, and is "
        "reported as a secret; the floor has been lowered"
    )


@pytest.mark.parametrize(
    ("missing", "candidate"),
    [
        ("no-digit", string.ascii_uppercase[:16] + string.ascii_lowercase[:16]),
        ("no-upper-case-letter", string.ascii_lowercase[:22] + string.digits),
        ("no-lower-case-letter", string.ascii_uppercase[:22] + string.digits),
    ],
)
def test_the_detector_refuses_a_candidate_missing_one_character_class(
    missing: str, candidate: str
) -> None:
    """Each class requirement has to be load-bearing on its own.

    Each candidate holds 32 distinct characters exactly once, so its entropy is
    exactly log2(32) = 5.0 bits: over the floor *by construction*, which is what
    makes the missing class provably the only thing that can refuse it. Two of
    the three requirements survived their own deletion in the sibling detector
    until fixtures of this shape existed.
    """
    assert round(_entropy(candidate), 4) == 5.0, "the fixture is no longer uniform over 32 symbols"

    assert not scan_text(candidate), (
        f"a 32-character candidate with {missing} -- entropy 5.0 bits, so the floor is not "
        f"what refuses it -- is reported as a secret: {candidate!r}"
    )


def test_a_theurian_token_beside_an_identifier_is_still_reported() -> None:
    """The ULID subtraction must not become a way to hide a credential.

    Subtracting Theurian's own identifiers is what stops a migration filename
    reading as a secret, and the obvious way to get that wrong is to let anything
    *near* a ULID go quiet with it. What is left after the subtraction is judged
    as a candidate in its own right, so a real token joined to a ULID is still a
    real token.
    """
    findings = scan_text(f"01K1AAAAAA01234567890ABCDE-{TOKEN_SHAPED}\n")

    assert [f.family for f in findings] == [HIGH_ENTROPY], (
        "a token concatenated with a ULID went unreported, so the subtraction is "
        "silencing the candidate rather than shortening it"
    )


def test_the_fixture_keeps_the_shape_of_a_real_theurian_token() -> None:
    """A stand-in stands in only while it is shaped like the thing it replaces.

    Every claim above is a claim about what the detector does to a real Theurian
    token, which ``security.tokens`` mints as
    ``secrets.token_urlsafe(TOKEN_BYTES)``. Both halves are read from Core rather
    than written down here, so raising ``TOKEN_BYTES`` reddens *here*, where the
    message says to re-derive the fixture, instead of leaving one shaped like a
    token Theurian no longer issues.
    """
    from theurian.security.tokens import TOKEN_BYTES, is_well_formed

    real_length = len(base64.urlsafe_b64encode(bytes(TOKEN_BYTES)).rstrip(b"="))

    assert len(TOKEN_SHAPED) == real_length, (
        f"the fixture is {len(TOKEN_SHAPED)} characters, but a token from "
        f"`secrets.token_urlsafe(TOKEN_BYTES)` is {real_length}; re-derive it"
    )
    assert is_well_formed(TOKEN_SHAPED), (
        f"Core's own token shape check rejects the fixture: {TOKEN_SHAPED!r}"
    )
