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
    _MIN_CANDIDATE_CHARS,
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


#: A 24-character token that clears every gate *but* the length floor: it carries
#: upper, lower and a digit and 4.335 bits of entropy (verified below), so only
#: its being shorter than :data:`_MIN_CANDIDATE_CHARS` keeps it unreported. Split
#: from its seed; 24 is a fixed length between the floor and the 20 a floor
#: mutation drops to, so this does not scale with the constant it checks.
_BELOW_FLOOR_TOKEN: Final = (
    base64.urlsafe_b64encode(hashlib.sha256(b"min-candidate pin fixture 0").digest()[:18])
    .decode()
    .rstrip("=")
)


def test_a_high_entropy_run_under_the_candidate_floor_is_not_reported() -> None:
    """The candidate floor is load-bearing, and lowering it is a decision.

    ``_MIN_CANDIDATE_CHARS`` is one definition in two places -- the regex
    ``{32,}`` and the remainder check in ``_looks_like_a_secret`` -- so a run of 24
    high-entropy, mixed-class characters clears every other gate and is refused
    only for its length. Dropping the floor to 20 (the mutation that survived the
    suite) makes the regex match it and every gate pass; this is where that lands.
    """
    assert len(_BELOW_FLOOR_TOKEN) == 24, "the fixture length moved off its 24-char design"
    assert len(_BELOW_FLOOR_TOKEN) < _MIN_CANDIDATE_CHARS
    assert _entropy(_BELOW_FLOOR_TOKEN) >= 4.0, "the fixture would not clear the floor if longer"
    assert all(
        any(check(char) for char in _BELOW_FLOOR_TOKEN)
        for check in (str.isupper, str.islower, str.isdigit)
    ), "the fixture no longer carries all three classes, so length is not the only thing left"

    assert scan_text(f"token = {_BELOW_FLOOR_TOKEN}") == (), (
        f"{_BELOW_FLOOR_TOKEN!r} is 24 characters, under the {_MIN_CANDIDATE_CHARS}-character "
        "floor, and was reported -- the floor was lowered"
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


def test_a_token_crafted_to_embed_a_ulid_evades_the_subtraction() -> None:
    """The honest bound of the ULID subtraction, pinned as the residual it is.

    The subtraction that keeps Theurian's own filenames from reading as secrets is
    best effort: a token *crafted* to embed a 26-character ULID-shaped run loses
    those characters and its remainder can fall below the candidate floor, so a
    value the detector would flag whole slips through. This backs the measured
    claim in ``_looks_like_a_secret``'s docstring -- the ULID plus ``abcDEF12``
    leaves eight characters and is not reported -- so that residual cannot go
    silently unmeasured. SEC-11 accepts it; the product disclaims being a complete
    scanner. The ULID and the suffix are joined at runtime so no crafted literal
    sits in the file for the repository's own scan to judge.
    """
    ulid = "01M0D5GSKA479Y85296S745521"  # 26 chars, ULID-shaped, already a NEGATIVES fixture
    crafted = ulid + "abcDEF12"  # a 34-char token, high-entropy and mixed-class whole
    assert _entropy(crafted) >= 4.0, "the whole crafted token would clear the entropy floor"

    assert scan_text(f"token = {crafted}") == (), (
        "the crafted ULID-prefixed token was reported; the subtraction residual the docstring "
        "records has changed, which is a decision, not a regression to absorb silently"
    )


# -- A credential glued behind a lower-case run (#350) ---------------------
#
# The scan as it ships misses a credential joined to a lower-case run by a
# delimiter. Measured 2026-08-25 on fb88c3f, with `hex40` standing for forty
# lower-case hexadecimal characters:
#
#   sk-<hex40>                              openai-api-key
#   staging-sk-<hex40>                      []
#   rotate staging-sk-<hex40>               []
#   <ulid>-staging-sk-<hex40>.yaml          []
#   staging-sk_live_<16>                    []
#   backup-xoxb-<34>                        []
#   abc-sk-<hex20>   (a 27-character run)   openai-api-key
#   STAGING9-sk-<hex40>                     high-entropy-token
#
# The mechanism is the single non-overlapping pass. At the run's first character
# the alternation's `high-entropy-token` branch matches the *whole* run of
# candidate-class characters; that branch is the one family whose regex match
# still has to clear a heuristic, and the heuristic refuses it -- nothing
# upper-case survives the ULID subtraction. `finditer` then resumes *after* the
# text the refused match consumed, so no position inside the run is retried, and
# the `openai-api-key` family that would have matched at the word boundary the
# internal `-` provides is never tried there at all.
#
# The last two rows are the two ways out of the class, and they are why the
# fixtures below carry the guards they do: a run under the 32-character floor is
# never consumed by the generic family (`abc-sk-<hex20>` is reported today), and a
# run that *passes* the class gate is reported as a high-entropy token (so the
# repair must not report it a second time under its specific family).
#
# This bounds the control #336 shipped. A proposal `title` of
# `rotate staging-sk-<hex40>` passes `theurian propose accept` at the default
# `block` policy, and `title` *is* scanned -- the gap is the detector's, not the
# scan wiring's. SEC-11 disclaims completeness, so this is a known-class
# limitation rather than a false published claim, but it is one an ordinary
# deployment-stage prefix reaches.

#: Forty lower-case hexadecimal characters, derived from a fixed seed rather than
#: pasted -- the same construction as ``_ID_SHAPED_TOKEN`` in
#: ``tests/integration/test_proposal_secret_scan.py``, which is the canonical
#: planted secret of the #336 metadata scan. Deliberately the same shape, so a
#: repair here is co-verified against the exact value that control plants.
_HEX40: Final = hashlib.sha256(b"theurian glued-prefix fixture (#350)").hexdigest()[:40]

#: ``sk-`` and those forty characters: 43 characters, all lower case and digits.
#: That is the property the class does not survive -- the generic family's gate
#: requires an upper-case character, so once anything is glued in front the run it
#: consumes is refused and the credential inside it goes unexamined.
#:
#: Joined at run time, for the reason :data:`PATTERN_FAMILY_FIXTURES` records: a
#: contiguous credential-shaped literal is a thing no reviewer can tell from a leak
#: by looking.
_OPENAI_SHAPED: Final = "sk-" + _HEX40

#: A deployment-stage prefix and the delimiter that joins it. Eight characters, so
#: the credential starts at offset 8 inside the candidate run and not at its
#: start, which is the whole difference between the two rows of the table above.
_STAGE_PREFIX: Final = "staging-"

#: ``staging-sk-<hex40>``: 51 characters of the candidate class, no upper case.
_GLUED_OPENAI: Final = f"{_STAGE_PREFIX}{_OPENAI_SHAPED}"

#: The credential each family is spelled with, read from
#: :data:`PATTERN_FAMILY_FIXTURES` so the value glued below and the value reported
#: unglued cannot drift apart.
_FAMILY_CREDENTIALS: Final = {
    family: prefix + tail for family, prefix, tail in PATTERN_FAMILY_FIXTURES
}

#: ``(family, the lower-case run glued in front, the credential)``. Three families
#: rather than one, because the defect is a property of the *pass*, not of any
#: pattern: whichever specific family sits inside a refused run is the one that is
#: lost.
#:
#: Only the families whose fixture is entirely lower case and digits can appear
#: here -- ``AKIA``, ``ghp_``'s mixed-case tail and ``AIza`` all carry an
#: upper-case character into the run, which makes the generic family accept it and
#: report *something*. Those are a different row of the table.
#:
#: The stripe run is exactly 32 characters, the floor itself. That is not slack to
#: be trimmed: shortening either half drops the run under the floor, the generic
#: family stops consuming it, and the case starts passing without exercising
#: anything. :func:`test_every_glued_fixture_reaches_the_branch_this_section_is_about`
#: is what notices.
GLUED_PREFIX_FIXTURES: Final[tuple[tuple[str, str, str], ...]] = (
    ("openai-api-key", _STAGE_PREFIX, _OPENAI_SHAPED),
    ("stripe-secret-key", _STAGE_PREFIX, _FAMILY_CREDENTIALS["stripe-secret-key"]),
    ("slack-token", "backup-", _FAMILY_CREDENTIALS["slack-token"]),
)

_GLUED_IDS: Final = [family for family, _, _ in GLUED_PREFIX_FIXTURES]

#: Crockford base32 with no ``I``, ``L``, ``O`` or ``U``, and obviously synthetic:
#: the alphabet in order after a fixed head. A migration is named
#: ``<ulid>-<slug>.yaml``, so a filename is one candidate run and the ULID is where
#: its only upper-case characters live -- which the detector subtracts before the
#: class gate sees them.
_SYNTHETIC_ULID: Final = "01K9ABCDEFGHJKMNPQRSTVWXYZ"


@pytest.mark.parametrize(("family", "prefix", "credential"), GLUED_PREFIX_FIXTURES, ids=_GLUED_IDS)
def test_every_glued_fixture_reaches_the_branch_this_section_is_about(
    family: str, prefix: str, credential: str
) -> None:
    """A fixture that never reaches the masking branch makes every case here vacuous.

    Three things have to hold at once before the tests below say anything, and
    each of them is one edit away from silently ceasing to hold:

    * the credential is reported *unglued*, or a red below would be a red about a
      value that was never a secret rather than about #350;
    * the glued run is at least :data:`_MIN_CANDIDATE_CHARS` characters, or the
      generic family never matches it, nothing is consumed, and the specific
      family is reported today -- ``abc-sk-<hex20>`` in the table above;
    * the run carries no upper-case character, so the generic family's gate is
      what refuses what it consumed.

    The second is not hypothetical for the stripe fixture, whose run is exactly 32
    characters. This is the guard that keeps a tail edit from turning that case
    into a test that passes because it stopped testing.
    """
    run = f"{prefix}{credential}"

    unglued = scan_text(f"config:\n  key: {credential}\n")

    assert [f.family for f in unglued] == [family], (
        f"{credential[:REDACTED_PREFIX_CHARS]}... is not reported as {family} even unglued "
        f"({[f.family for f in unglued]}), so the glued case would be red for a reason that "
        f"has nothing to do with #350 -- check the fixture before the detector"
    )
    assert len(run) >= _MIN_CANDIDATE_CHARS, (
        f"the {family} run is {len(run)} characters, under the {_MIN_CANDIDATE_CHARS}-character "
        f"floor, so the generic family never consumes it and the glued case below passes "
        f"without exercising the masking this section is about"
    )
    assert not any(char.isupper() for char in run), (
        f"the {family} run now carries an upper-case character, so the generic family's class "
        f"gate can accept it and report the run as {HIGH_ENTROPY}; the glued case below would "
        f"then be about a different row of the table"
    )
    assert run.index(credential) > 0, (
        f"the {family} credential sits at the start of the run, which is the shape that "
        f"already works -- there is nothing glued in front of it"
    )


@pytest.mark.parametrize(("family", "prefix", "credential"), GLUED_PREFIX_FIXTURES, ids=_GLUED_IDS)
def test_a_credential_glued_behind_a_lower_case_run_is_still_reported(
    family: str, prefix: str, credential: str
) -> None:
    """A prefix in front of a credential is not a way to get it past the gate.

    ``sk-<hex40>`` is reported; ``staging-sk-<hex40>`` is not, and the eight
    characters that make the difference are ones anybody would write. Nothing an
    author has to know about the detector is required to produce this -- an
    environment name, a service name or a ticket id in front of a pasted value is
    ordinary, and each of them defeats the control.

    Three families, because what is lost is decided by the *pass* and not by any
    pattern: the generic branch consumes the whole run wherever a run exists, so
    whichever specific family is inside one is the family that goes missing.
    """
    findings = scan_text(f"config:\n  key: {prefix}{credential}\n")

    assert [f.family for f in findings] == [family], (
        f"a {family} credential glued behind {prefix!r} was reported as "
        f"{[f.family for f in findings]}. The generic family consumed the whole "
        f"candidate run, its class gate refused what it consumed, and the single "
        f"non-overlapping pass never retried the word boundary inside the run where "
        f"{family} would have matched."
    )


def test_a_credential_glued_into_a_proposal_title_is_still_reported() -> None:
    """The input that bounds the control #336 shipped, written the way it arrives.

    ``theurian propose accept`` scans a migration document's metadata, ``title``
    included, and refuses at the default ``block`` policy. A title of *rotate
    staging-sk-<hex40>* is accepted anyway -- not because the title went unscanned,
    but because the detector reports nothing for it. Recorded as its own case so
    that the scan wiring and the detector cannot be confused for each other again
    when this goes red.

    Prose around the run on both sides, because a bare fixture would leave the
    run's boundaries at the ends of the string, where a lookaround can succeed for
    reasons that have nothing to do with the text.
    """
    findings = scan_text(f"title: rotate {_GLUED_OPENAI} before Friday\n")

    assert [f.family for f in findings] == ["openai-api-key"], (
        f"a credential in a proposal title is reported as {[f.family for f in findings]}, so "
        f"`propose accept` passes it at the default `block` policy"
    )


def test_a_credential_glued_into_a_migration_filename_is_still_reported() -> None:
    """The ULID subtraction must not become a second way to reach the same silence.

    This row of the table reaches the masking branch by a different mechanism than
    the ones above, and that is exactly why it is here. The run
    ``<ulid>-staging-sk-<hex40>`` *does* carry upper-case characters -- twenty-odd
    of them -- but every one belongs to the ULID, which
    :func:`_looks_like_a_secret` subtracts before it looks at character classes. So
    the gate sees ``-staging-sk-<hex40>``, refuses it for the missing upper case,
    and the run is consumed and dropped just as the lower-case ones are.

    A filename is where this shape actually turns up: a migration is
    ``<ulid>-<slug>.yaml`` and the slug is author-supplied, so the whole name is
    one candidate run with author text in the middle of it.
    """
    run = f"{_SYNTHETIC_ULID}-{_STAGE_PREFIX}{_OPENAI_SHAPED}"
    assert any(char.isupper() for char in run), (
        "the fixture no longer carries the upper case that makes this case distinct from "
        "the lower-case runs above"
    )
    assert not any(char.isupper() for char in run.replace(_SYNTHETIC_ULID, "")), (
        "an upper-case character now survives the ULID subtraction, so the generic family's "
        "gate may accept this run and this case stops being about masking"
    )

    findings = scan_text(f"proposal writes {run}.yaml\n")

    assert [f.family for f in findings] == ["openai-api-key"], (
        f"a credential inside a migration filename is reported as "
        f"{[f.family for f in findings]}; the ULID subtraction carried the run past the "
        f"class gate and the pass dropped it"
    )


def test_a_masked_finding_is_located_at_the_credential_not_at_the_run_it_hid_in() -> None:
    """A finding recovered from inside a run has to point at the credential.

    The obvious way to repair the class is to re-examine a refused candidate, and
    the obvious way to get *that* wrong is to report the position of the thing that
    was refused. A reader sent to column 15 finds ``staging-`` and concludes the
    report is noise; the credential is at column 23. The same holds for the quoted
    prefix, which would read ``stag...`` -- a locator pointing at the wrong four
    characters is worse than no locator, because it looks like one.

    Line 3 of a multi-line body, so a repair that reports offsets inside the
    candidate rather than inside the document fails here rather than in whichever
    caller renders it.
    """
    body = f"---\nsummary: clean\nnotes: rotate {_GLUED_OPENAI} before Friday\n"

    findings = scan_text(body)

    assert [f.family for f in findings] == ["openai-api-key"], (
        f"the masked credential on line 3 is not reported at all -- {[f.family for f in findings]} "
        f"-- so there is no position to check; this case is about *where* a recovered finding "
        f"points, and the one that says it must be recovered at all is "
        f"test_a_credential_glued_behind_a_lower_case_run_is_still_reported"
    )
    (finding,) = findings
    assert (finding.line, finding.column) == (3, len("notes: rotate ") + len(_STAGE_PREFIX) + 1), (
        f"the finding claims line {finding.line}, column {finding.column}. The candidate run "
        f"starts at column {len('notes: rotate ') + 1} and the credential at column "
        f"{len('notes: rotate ') + len(_STAGE_PREFIX) + 1}; a report at the run's start sends "
        f"a reader to {_STAGE_PREFIX!r}"
    )
    assert finding.redacted == f"{_OPENAI_SHAPED[:REDACTED_PREFIX_CHARS]}{'...'}", (
        f"the finding quotes {finding.redacted!r}, which is the start of the run rather than "
        f"of the credential -- the four characters a reader uses to pick this candidate out "
        f"of a line name the wrong string"
    )


#: A fixed number of masked lines, over the ceiling the test below passes and
#: independent of it, so the case cannot pass by having its fixture scale with the
#: number it checks.
_MASKED_LINES: Final = 5

#: The ceiling under test. A literal rather than :data:`MAX_FINDINGS`, for the
#: reason :func:`test_the_scan_stops_at_a_fixed_ceiling` records: a fixture and an
#: expectation that both read the constant pass however high it is raised.
_CEILING_UNDER_TEST: Final = 3


def test_the_ceiling_still_bounds_findings_recovered_from_inside_a_failed_candidate() -> None:
    """Truncation is what keeps a refusal message from being sized by its input.

    ``max_findings`` bounds the list a caller renders into a terminal and into an
    ``accept --json`` document, and it also bounds ``scan_text``'s quadratic
    line-number cost. A repair that recovers extra findings from inside refused
    candidates adds a second place a finding is appended, and a second place is a
    second chance to append past the bound -- the loop's ``break`` sits on the
    first path only.

    Asserted as an exact list rather than a length, so a repair that honours the
    count while returning the *last* three, or the same finding three times, fails
    here too.
    """
    assert _MASKED_LINES > _CEILING_UNDER_TEST, (
        "the fixture must carry more masked candidates than the ceiling, or it exercises no "
        "truncation at all"
    )
    crowded = "\n".join(f"key_{n}: {_GLUED_OPENAI}" for n in range(_MASKED_LINES))

    findings = scan_text(crowded, max_findings=_CEILING_UNDER_TEST)

    assert [(f.family, f.line) for f in findings] == [
        ("openai-api-key", line) for line in (1, 2, 3)
    ], (
        f"{len(findings)} findings came back from {_MASKED_LINES} masked candidates with the "
        f"ceiling at {_CEILING_UNDER_TEST}: {[(f.family, f.line) for f in findings]}"
    )


#: How many times the stripe credential is repeated inside the single candidate
#: run below. Over the ceiling and independent of it, for the reason the sibling
#: fixture records -- and over it *within one outer match*, which is the whole
#: difference between the two cases.
_MATCHES_INSIDE_ONE_RUN: Final = 40

#: ``staging-`` followed by forty ``sk_live_<16 chars>-`` repetitions: 1,008
#: candidate-class characters with no upper case at all, so the generic family
#: consumes the lot and its class gate refuses what it consumed. One refused
#: candidate holding forty ``stripe-secret-key`` matches, because that family's
#: repetition class excludes ``-``: each match ends at a delimiter, which leaves
#: ``\b`` satisfied for the next.
#:
#: **The prefix is load-bearing, not decoration.** Without it the run begins with
#: ``sk_live_``, where the stripe branch of the top-level alternation wins outright
#: -- the specific families are declared before the generic one -- so the outer pass
#: reports each credential directly and *no candidate is ever refused*. Measured
#: 2026-08-25: a bare ``sk_live_<16>-`` repeated forty times answers 20 with the
#: inner bound and 20 without it, because nothing ever calls the function the bound
#: lives in. Gluing a prefix in front is what moves the whole run onto the generic
#: branch, which is the same mechanism :data:`_GLUED_OPENAI` exercises one
#: credential at a time.
_CROWDED_REFUSED_RUN: Final = _STAGE_PREFIX + (
    f"{_FAMILY_CREDENTIALS['stripe-secret-key']}-" * _MATCHES_INSIDE_ONE_RUN
)


def test_the_ceiling_bounds_a_single_failed_candidate_that_hides_many_credentials() -> None:
    """One refused run can hold more credentials than the whole scan may report.

    ``scan_text``'s own ``break`` runs once per *outer* match, so it cannot bound
    what a single outer match contributes. The sibling case above plants one
    credential per line, so every refused run yields exactly one finding and the
    outer break catches the ceiling first -- measured 2026-08-25, deleting
    ``_families_inside``'s ``room`` bound leaves all 48 other cases in this file
    green. This is the input where that bound is the only thing holding: one
    1,008-character candidate carrying forty credentials, which without it returns
    forty findings at a published ceiling of twenty, each one paying the
    ``O(position)`` newline count the ceiling exists to cap.

    Asserted as an exact list of families and columns rather than a length. Every
    match here sits on one line, so the column is what says the three reported are
    the *first* three, rather than the last three or one finding three times.
    """
    assert _MATCHES_INSIDE_ONE_RUN > _CEILING_UNDER_TEST, (
        "one run must hold more credentials than the ceiling, or the outer break bounds this "
        "body too and the case says nothing the sibling above does not"
    )
    assert len(_CROWDED_REFUSED_RUN) >= _MIN_CANDIDATE_CHARS, (
        "the run is under the candidate floor, so the generic family never consumes it and "
        "nothing is ever refused or re-examined"
    )
    assert not any(char.isupper() for char in _CROWDED_REFUSED_RUN), (
        "the run now carries an upper-case character, so the generic family's class gate can "
        "accept it and report one high-entropy finding instead of refusing the run"
    )
    assert _CROWDED_REFUSED_RUN.index(_FAMILY_CREDENTIALS["stripe-secret-key"]) > 0, (
        "the run starts with the credential, where the stripe branch of the alternation wins "
        "outright and the outer pass reports each match itself -- no candidate is refused, "
        "nothing is recovered from inside one, and this case is green without exercising the "
        "bound it is about"
    )
    body = f"config:\n  key: {_CROWDED_REFUSED_RUN}\n"
    first_credential_column = len("  key: ") + len(_STAGE_PREFIX) + 1
    stride = len(_FAMILY_CREDENTIALS["stripe-secret-key"]) + len("-")

    findings = scan_text(body, max_findings=_CEILING_UNDER_TEST)

    assert [(f.family, f.column) for f in findings] == [
        ("stripe-secret-key", first_credential_column + n * stride)
        for n in range(_CEILING_UNDER_TEST)
    ], (
        f"{len(findings)} findings came back from one refused candidate holding "
        f"{_MATCHES_INSIDE_ONE_RUN} credentials with the ceiling at {_CEILING_UNDER_TEST}: "
        f"{[(f.family, f.column) for f in findings]}. The outer `break` cannot bound this -- "
        f"it runs once per outer match, and this body is one."
    )


#: The family reported *before* the crowded run in the case below, chosen because
#: it cannot be confused with what the run holds and cannot be swallowed by one.
#: ``AKIA`` plus sixteen characters is 20 -- under the candidate floor, so no
#: generic run exists to consume it and the outer pass reports it directly, taking
#: one of the ceiling's slots before ``_families_inside`` is reached at all.
_FINDING_BEFORE_THE_RUN: Final = "aws-access-key-id"


def test_a_finding_taken_before_a_crowded_run_leaves_that_run_less_room() -> None:
    """``max_findings`` is a bound on the returned list, not a per-run allowance.

    ``_families_inside`` is passed ``max_findings - len(findings)``, and the
    subtraction is the whole of what keeps the published contract: the outer
    ``break`` is checked *after* the extend, so a run allowed the full ceiling
    appends past it and ``scan_text`` returns more than the caller asked for. A
    caller sizing a terminal message or an ``accept --json`` document on
    ``max_findings`` gets a list longer than the number it set.

    The sibling cases cannot see this. The one above sends the run the whole
    ceiling legitimately, because nothing was taken first; the one before it plants
    one credential per line, where every run contributes a single finding and the
    outer break arrives in time. Only a finding *preceding* a crowded run
    distinguishes the remaining room from the ceiling -- measured 2026-08-25,
    mutating the subtraction to ``room = max_findings`` returns four findings here
    for ``max_findings=3`` and leaves every other case in this file green.
    """
    assert 1 < _CEILING_UNDER_TEST < _MATCHES_INSIDE_ONE_RUN, (
        f"the ceiling has to leave room for the run after the first finding is taken, and the "
        f"run has to hold more than that remainder; at {_CEILING_UNDER_TEST} against "
        f"{_MATCHES_INSIDE_ONE_RUN} it does not, so the remaining room is never the binding "
        f"bound and this case stops being about the subtraction"
    )
    assert len(_FAMILY_CREDENTIALS[_FINDING_BEFORE_THE_RUN]) < _MIN_CANDIDATE_CHARS, (
        f"the preceding credential is now at or over the {_MIN_CANDIDATE_CHARS}-character floor, "
        f"so it is a candidate run in its own right rather than a finding the outer pass reports "
        f"outright -- how many slots it takes before the crowded run is reached is no longer the "
        f"one this case counts on"
    )
    body = f"key: {_FAMILY_CREDENTIALS[_FINDING_BEFORE_THE_RUN]}\nkey: {_CROWDED_REFUSED_RUN}\n"
    first_credential_column = len("key: ") + len(_STAGE_PREFIX) + 1
    stride = len(_FAMILY_CREDENTIALS["stripe-secret-key"]) + len("-")

    findings = scan_text(body, max_findings=_CEILING_UNDER_TEST)

    assert [(f.family, f.line, f.column) for f in findings] == [
        (_FINDING_BEFORE_THE_RUN, 1, len("key: ") + 1),
        *(
            ("stripe-secret-key", 2, first_credential_column + n * stride)
            for n in range(_CEILING_UNDER_TEST - 1)
        ),
    ], (
        f"{len(findings)} findings came back for max_findings={_CEILING_UNDER_TEST}: "
        f"{[(f.family, f.line, f.column) for f in findings]}. One finding was taken before the "
        f"run, so the run may contribute {_CEILING_UNDER_TEST - 1} -- a run handed the whole "
        f"ceiling instead appends past it, and the outer `break` only notices afterwards."
    )


#: ``risk-<hex40>``. The letters ``sk`` appear inside a word, and the ``-`` after
#: them is the same delimiter the ``openai-api-key`` family looks for -- so the
#: only thing between this and a false positive is that ``\b`` requires a
#: non-word character *before* the ``s``. 45 characters, all lower case and
#: digits, so it reaches the same refused-candidate branch every case above does.
_PREFIX_INSIDE_A_WORD: Final = f"risk-{_HEX40}"


def test_a_prefix_that_is_part_of_a_word_is_not_reported() -> None:
    """The false positive the repair must not buy, pinned before the repair exists.

    Re-examining a refused candidate means running the specific families over a
    substring, and the cheap version of that drops the anchor -- searching for
    ``sk-`` anywhere inside the run rather than at a word boundary. This is what
    that costs: every word ending in ``sk`` followed by a hyphen and a long
    identifier becomes a reported credential, and ``risk-``, ``task-``, ``desk-``
    and ``disk-`` are all ordinary things to write in a knowledge document.

    With ``block`` as the default policy a false positive refuses acceptances
    until somebody turns the control off, which costs exactly what a false
    negative costs. This case is green today and must stay green: it is the bound
    on the repair, not a target for it.
    """
    assert len(_PREFIX_INSIDE_A_WORD) >= _MIN_CANDIDATE_CHARS, (
        f"{_PREFIX_INSIDE_A_WORD!r} is under the candidate floor, so it is never consumed and "
        f"never re-examined -- this case would be green without saying anything"
    )
    assert "sk-" in _PREFIX_INSIDE_A_WORD, (
        "the fixture no longer contains the family's own prefix, so it is not the near miss "
        "it claims to be"
    )

    findings = scan_text(f"the {_PREFIX_INSIDE_A_WORD} table is unchanged\n")

    assert findings == (), (
        f"{_PREFIX_INSIDE_A_WORD!r} is reported as "
        f"{[(f.family, f.redacted) for f in findings]}. `sk` here is the tail of an English "
        f"word, and the family's leading `\\b` is what tells the two apart -- dropping it to "
        f"reach inside a candidate run reports every word that ends in those letters."
    )


#: ``stagingsk-<hex40>``: the same credential with the delimiter removed, so there
#: is no word boundary anywhere in front of the ``sk``. 50 characters, all lower
#: case and digits, entropy 4.0791 -- over the floor, so the class gate's missing
#: upper case is what refuses it, exactly as in the reported cases.
_NO_BOUNDARY_BEFORE_THE_PREFIX: Final = f"staging{_OPENAI_SHAPED}"


def test_a_credential_with_no_boundary_before_its_prefix_stays_unreported() -> None:
    """The residual the repair leaves, recorded as behaviour rather than as a comment.

    Re-examining a refused candidate recovers a credential that sits at a word
    boundary *inside* the run -- which every delimiter provides, because ``-`` is a
    non-word character. It recovers nothing where the run has no boundary at all,
    and it must not: reaching that value means matching ``sk-`` at an arbitrary
    offset, which is the false positive the case above prices.

    So this is a bound, not a miss to be fixed later. If it goes red, the repair
    has widened past word boundaries and
    :func:`test_a_prefix_that_is_part_of_a_word_is_not_reported` is the assertion
    to read next -- that is a decision somebody takes with both cases in front of
    them, not a regression to absorb.
    """
    assert len(_NO_BOUNDARY_BEFORE_THE_PREFIX) >= _MIN_CANDIDATE_CHARS, (
        "the fixture is under the candidate floor, so it is never consumed and this case says "
        "nothing about the re-examination it is bounding"
    )

    findings = scan_text(f"key: {_NO_BOUNDARY_BEFORE_THE_PREFIX}\n")

    assert findings == (), (
        f"{_NO_BOUNDARY_BEFORE_THE_PREFIX[:REDACTED_PREFIX_CHARS]}... was reported as "
        f"{[f.family for f in findings]}. There is no word boundary before the `sk`, so "
        f"reaching it means matching the family's prefix at an arbitrary offset -- read the "
        f"false-positive case above before recording this as an improvement."
    )


def test_a_clean_migration_filename_is_still_not_reported() -> None:
    """The product's own filenames go through the re-examined branch too.

    ``<ulid>-retry-policy`` is 39 candidate-class characters, so the generic family
    consumes it and the gate refuses it -- which means a repair that re-examines
    refused candidates runs the specific families over every migration filename
    this repository writes, on every scan. Nothing may match.

    The measurement behind :data:`NEGATIVES` says all 26 committed migration
    filenames were reported as secrets before the ULID subtraction existed; this is
    the same class arriving from the other side, and it is worth its own case
    because :data:`NEGATIVES` was written when nothing re-examined anything.
    """
    filename = f"{_SYNTHETIC_ULID}-retry-policy"
    assert len(filename) >= _MIN_CANDIDATE_CHARS, (
        f"{filename!r} is under the candidate floor, so it is never consumed and never "
        f"re-examined -- this case would be green without exercising the branch"
    )

    findings = scan_text(f"see {filename}.yaml for the current values\n")

    assert findings == (), (
        f"a migration filename the product mints itself is reported as "
        f"{[(f.family, f.redacted) for f in findings]}; with `block` as the default policy "
        f"that refuses acceptances for a name Theurian wrote"
    )


#: ``STAGING9-sk-<hex40>``: the same gluing, on a run that *passes* the class gate.
#: Upper case, lower case and a digit all present and 4.1958 bits, so the generic
#: family accepts what it consumed and reports it -- no candidate is refused, and
#: nothing is re-examined.
_GATE_PASSING_RUN: Final = f"STAGING9-{_OPENAI_SHAPED}"


def test_a_run_that_clears_the_class_gate_is_reported_once_as_a_high_entropy_token() -> None:
    """Re-examination belongs to refused candidates only, or every accepted run reports twice.

    This run holds a credential at an internal word boundary exactly as the
    reported cases do. The difference is that the generic family accepts it, so it
    is already reported -- and a repair that re-examines every candidate rather
    than only the refused ones adds a second finding for the same characters, at a
    second position, under a second family name. Two findings for one value is a
    refusal message that overstates what it found and a JSON document that
    double-counts.

    The count is asserted, not just the family, because that is the half a
    family-only assertion cannot see.
    """
    assert round(_entropy(_GATE_PASSING_RUN), 4) == 4.1958, (
        f"the fixture carries {_entropy(_GATE_PASSING_RUN):.4f} bits, not the 4.1958 recorded "
        f"beside it; re-measure, and check it still clears the 4.0 floor"
    )
    assert all(
        any(check(char) for char in _GATE_PASSING_RUN)
        for check in (str.isupper, str.islower, str.isdigit)
    ), "the fixture no longer carries all three classes, so the gate no longer accepts it"

    findings = scan_text(f"key: {_GATE_PASSING_RUN}\n")

    assert [f.family for f in findings] == [HIGH_ENTROPY], (
        f"a run that clears the class gate was reported as {[f.family for f in findings]}. "
        f"Two findings mean a refused-candidate re-examination is running over accepted "
        f"candidates as well, and one value is being reported twice."
    )


#: ``abc-sk-<hex20>``: the same gluing again, on a run of 27 characters. Under the
#: candidate floor, so the generic family never matches and never consumes it, and
#: the specific family is reached at the internal boundary by the ordinary pass.
#: Reported today -- it is the row of the table above that already works.
_SUB_FLOOR_GLUED: Final = f"abc-sk-{_HEX40[:20]}"


def test_a_glued_credential_in_a_run_too_short_to_consume_is_still_reported() -> None:
    """The half of the class that already works, pinned before it is repaired around.

    Nothing consumes this run, so the ordinary left-to-right pass reaches the
    ``openai-api-key`` family at the boundary the ``-`` provides and reports it.
    That is the behaviour the repair has to leave alone, and the way to lose it is
    to move the specific families *out* of the top-level alternation and run them
    only over refused candidates -- every case above would still pass, because
    those runs are all refused.

    **It does not carry that mutation alone, and the note is here so nobody
    believes it does.** Measured 2026-08-25: removing the specific families from
    the alternation reddens 19 cases in this file, three of them pre-existing
    (``test_each_pattern_family_reports_its_own_shape``,
    ``test_findings_come_back_in_document_order`` and the reachability guard
    above). What this case adds over those is the boundary row of the table it
    belongs to -- a *glued* credential whose run is too short to consume -- and the
    column on that path, which nothing else pins. 27 characters is short enough
    that a report at the run's start looks nearly right.
    """
    assert len(_SUB_FLOOR_GLUED) < _MIN_CANDIDATE_CHARS, (
        f"{_SUB_FLOOR_GLUED!r} is {len(_SUB_FLOOR_GLUED)} characters, at or over the "
        f"{_MIN_CANDIDATE_CHARS}-character floor, so the generic family consumes it after all "
        f"and this case has become a duplicate of the recovered ones above"
    )
    at_the_credential = len("key: abc-") + 1

    findings = scan_text(f"key: {_SUB_FLOOR_GLUED}\n")

    assert [(f.family, f.column) for f in findings] == [("openai-api-key", at_the_credential)], (
        f"a credential glued into a run too short to be consumed is reported as "
        f"{[(f.family, f.column) for f in findings]}. This path needs no repair -- if it broke, "
        f"the specific families have been moved out of the top-level alternation and now run "
        f"only over refused candidates."
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
