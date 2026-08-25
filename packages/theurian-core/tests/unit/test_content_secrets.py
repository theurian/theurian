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
import re
import string
from collections import Counter
from typing import Final

import pytest

from theurian.security.content_secrets import (
    _ASCII_DIGITS,
    _CANDIDATE_CLASS,
    _MIN_CANDIDATE_CHARS,
    _PATTERN_FAMILIES,
    FAMILIES,
    HIGH_ENTROPY,
    MAX_FINDINGS,
    REDACTED_PREFIX_CHARS,
    SecretFinding,
    _carries_a_digit,
    _looks_like_a_secret,
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


#: Characters to decide a regex character class's membership by matching, rather
#: than by comparing the class's source text. Two classes spelled differently can
#: admit the same characters -- ``[A-Za-z0-9_-]`` and ``[0-9A-Za-z_-]`` are the two
#: this file cares about -- and a comparison of sources would call them different
#: while the engine calls them the same.
#:
#: Printable ASCII, plus the three non-ASCII characters the reviews used: a kanji
#: (a word character to ``\b`` and not in the candidate class), a fullwidth zero
#: and a Devanagari one (both ``str.isdigit``, neither an ASCII digit).
#:
#: The two digits are written as escapes rather than as themselves. They are
#: deliberately confusable with ``0`` and ``1`` -- that confusability is the whole
#: reason :data:`_ASCII_DIGITS` is spelled out instead of left to ``str.isdigit`` --
#: and a reviewer cannot tell them from ASCII by looking, which is what ``RUF001``
#: says about the literal form.
_PROBE_ALPHABET: Final[tuple[str, ...]] = (
    *string.printable,
    "監",
    "\uff10",  # FULLWIDTH DIGIT ZERO
    "\u0967",  # DEVANAGARI DIGIT ONE
)

#: A negative lookahead over a character class, at the very end of a pattern.
#: Anchored on ``\Z`` so it can only match the pattern's own trailing assertion.
_TRAILING_LOOKAHEAD: Final = re.compile(r"\(\?!(\[[^\]]*\])\)\Z")

#: ``{n}`` or ``{n,m}``. Each specific family carries exactly one, which the case
#: below asserts before it reads one.
_REPETITION: Final = re.compile(r"\{(\d+)(?:,(\d+))?\}")


def _class_members(class_source: str) -> frozenset[str]:
    """Which probe characters ``class_source`` admits, decided by the engine."""
    compiled = re.compile(class_source)
    return frozenset(char for char in _PROBE_ALPHABET if compiled.fullmatch(char))


def _lookahead_relation(pattern: str) -> str:
    """How a pattern's trailing lookahead class relates to :data:`_CANDIDATE_CLASS`."""
    trailing = _TRAILING_LOOKAHEAD.search(pattern)
    if trailing is None:
        return "absent"
    admitted = _class_members(trailing.group(1))
    candidate = _class_members(_CANDIDATE_CLASS)
    if admitted == candidate:
        return "equal"
    if admitted < candidate:
        return "strict-subset"
    return "unclassified"


#: The geometry ``_families_inside``'s residual enumeration is *derived* from, as
#: ``(family, its credential is spelled inside the candidate class, how its
#: trailing lookahead relates to that class, its repetition's (min, max))``.
#:
#: Read the numbers as the source's own: ``{16}`` is ``(16, 16)`` and ``{36,255}``
#: is ``(36, 255)``. The 255s are written out rather than read from
#: :data:`_MAX_TOKEN_CHARS`, for the reason the ceiling cases record -- an
#: expectation that reads the constant it checks holds however the constant moves.
_PATTERN_GEOMETRY: Final[tuple[tuple[str, bool, str, tuple[int, int]], ...]] = (
    ("aws-access-key-id", True, "strict-subset", (16, 16)),
    ("github-token", True, "strict-subset", (36, 255)),
    ("google-api-key", True, "equal", (35, 35)),
    ("openai-api-key", True, "equal", (20, 255)),
    ("private-key-block", False, "absent", (0, 20)),
    ("slack-token", True, "strict-subset", (10, 255)),
    ("stripe-secret-key", True, "strict-subset", (16, 255)),
)


def test_the_pattern_geometry_the_residual_enumeration_is_derived_from() -> None:
    """The three properties ``_families_inside``'s residual is argued from, pinned live.

    That enumeration is not a list of observed misses. Members 2 and 3 are
    *derived*: a family whose credential needs a character outside
    :data:`_CANDIDATE_CLASS` can never match inside a run at all, and a family
    whose trailing lookahead admits exactly the candidate class can only ever end
    where the run ends, because every interior position is followed by a character
    that lookahead forbids. A fixed repetition then decides whether the family can
    reach that end from more than one starting distance.

    **So a pattern edit changes what the docstring proves, and this is the case
    that says so.** Narrowing ``google-api-key``'s lookahead to ``(?![0-9A-Za-z])``
    -- proposed in #356, and measured by the round-two review to cost no false
    positives -- moves it out of member 3 entirely. That change must arrive with a
    red test here, so that whoever makes it re-derives the enumeration instead of
    leaving prose that describes the pattern it used to have.

    Everything is computed from the live :data:`_PATTERN_FAMILIES` and from the
    live fixtures: nothing here restates a pattern, so a pattern that changes
    cannot leave a copy of itself behind agreeing with the old answer. Class
    membership is decided by the engine over :data:`_PROBE_ALPHABET` rather than by
    comparing source text, because ``[A-Za-z0-9_-]`` and ``[0-9A-Za-z_-]`` are the
    same class written twice and one comparison of strings would call them
    different.
    """
    credentials = {family: prefix + tail for family, prefix, tail in PATTERN_FAMILY_FIXTURES}
    candidate_characters = _class_members(_CANDIDATE_CLASS)
    assert len(candidate_characters) == len(string.ascii_letters + string.digits) + len("_-"), (
        f"the candidate class admits {len(candidate_characters)} of the probe alphabet, not the "
        f"64 base64url characters -- every subset relation below is measured against it, so a "
        f"class that changed silently would re-label the table rather than redden it"
    )
    specific = tuple(
        (family, pattern) for family, pattern in _PATTERN_FAMILIES if family != HIGH_ENTROPY
    )
    assert {family for family, _ in specific} == set(credentials), (
        f"the specific families {sorted(family for family, _ in specific)} and the fixtures "
        f"{sorted(credentials)} no longer name the same set -- a family with no representative "
        f"credential cannot be placed in the table below"
    )

    derived: list[tuple[str, bool, str, tuple[int, int]]] = []
    for family, pattern in specific:
        credential = credentials[family]
        assert re.fullmatch(pattern, credential), (
            f"the {family} fixture {credential[:REDACTED_PREFIX_CHARS]}... is no longer matched "
            f"whole by its own pattern, so it is not the representative this table reads it as"
        )
        repetitions = _REPETITION.findall(pattern)
        assert len(repetitions) == 1, (
            f"the {family} pattern carries {len(repetitions)} repetitions, not one; the "
            f"(min, max) below names a single quantifier and can no longer say which"
        )
        low, high = repetitions[0]
        derived.append(
            (
                family,
                set(credential) <= candidate_characters,
                _lookahead_relation(pattern),
                (int(low), int(high or low)),
            )
        )

    assert tuple(derived) == _PATTERN_GEOMETRY, (
        f"the pattern geometry moved:\n  derived  {tuple(derived)}\n"
        f"  recorded {_PATTERN_GEOMETRY}\n"
        f"`_families_inside`'s residual enumeration is derived from these three properties, so "
        f"re-derive members 2 and 3 there before recording the new table here."
    )


#: Two credentials at the lengths their issuers really publish, spelled the way
#: :data:`PATTERN_FAMILY_FIXTURES` is and for the same reason. A legacy OpenAI key
#: is ``sk-`` and 48 characters; a Slack bot token runs past fifty. Every tail
#: above stops at 36, so :data:`_MAX_TOKEN_CHARS` lowered to anywhere between 36
#: and these lengths keeps every case above green while the detector silently stops
#: reporting real credentials -- measured 2026-08-25 by the adversarial round, the
#: cap at 40 survived the entire suite.
#:
#: **Neither tail carries an upper-case character, and that is load-bearing.** It
#: denies the generic family its class gate, so the specific family is the only
#: thing that can report these at all. A fixture carrying upper case would be
#: reported as :data:`HIGH_ENTROPY` under a lowered cap and the case would stay
#: green while the family it names was lost.
LONG_CREDENTIAL_FIXTURES: Final[tuple[tuple[str, str, str], ...]] = (
    (
        "openai-api-key",
        "sk-",
        hashlib.sha256(b"legacy openai length fixture (#350)").hexdigest()[:48],
    ),
    ("slack-token", "xoxb-", "0123456789-0123456789-0123456789-abcdefghijklmno"),
)


@pytest.mark.parametrize(
    ("family", "prefix", "tail"),
    LONG_CREDENTIAL_FIXTURES,
    ids=[case[0] for case in LONG_CREDENTIAL_FIXTURES],
)
def test_a_credential_at_its_published_length_is_inside_the_repetition_cap(
    family: str, prefix: str, tail: str
) -> None:
    """The cap that bounds backtracking has to stay above the credentials it scans for.

    ``_MAX_TOKEN_CHARS`` is a ReDoS budget: it bounds how far a bounded repetition
    backtracks inside a run longer than itself. Lowering it costs detection, and the
    cost is invisible from the fixtures above because every one of them is short --
    a cap of 40 reports all seven families and misses both credentials here.

    That is the direction this case watches. It is not a claim that 255 is the right
    number; it is the claim that whatever the number is, it is above what the
    issuers ship, and moving it below is a decision taken with a red test in front
    of you rather than a constant nudged to buy back a measurement.
    """
    longest_tail_above = max(len(fixture_tail) for _, _, fixture_tail in PATTERN_FAMILY_FIXTURES)
    assert len(tail) > longest_tail_above, (
        f"the {family} tail is {len(tail)} characters, no longer than the {longest_tail_above} "
        f"the fixtures above already reach -- a cap lowered to between them is exactly what "
        f"this case exists to redden, and it no longer would"
    )
    assert not any(char.isupper() for char in f"{prefix}{tail}"), (
        f"the {family} fixture now carries an upper-case character, so the generic family's "
        f"class gate can accept the run and report it as {HIGH_ENTROPY}; this case would then "
        f"stay green with the specific family unreachable"
    )

    findings = scan_text(f"config:\n  key: {prefix}{tail}\n")

    assert [f.family for f in findings] == [family], (
        f"a {family} at its published length ({len(prefix) + len(tail)} characters) was "
        f"reported as {[f.family for f in findings]}. A bounded repetition that exhausts "
        f"before the run's end never satisfies its trailing lookahead, so the family matches "
        f"nothing at all -- check `_MAX_TOKEN_CHARS` before the pattern."
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

    Sixteen distinct characters in equal counts is uniform over sixteen symbols, so
    exactly 4.0 bits; fifteen gives log2(15) = 3.9069. The pair pins the constant
    to 0.093 bits *in both directions* and pins the comparison as ``>=`` rather
    than ``>``. Each is repeated three times, which does not move a uniform
    string's entropy and is what carries both past the candidate floor.

    **The repetition count is the whole case, and it was wrong.** Both fixtures
    were doubled rather than tripled, which left the under-floor one at 30
    characters -- below :data:`_MIN_CANDIDATE_CHARS`, so the generic family never
    matched it, no candidate was ever judged, and the entropy branch was not
    reached at all. Measured 2026-08-25 by the adversarial round: with that
    fixture, the floor was unpinned downward and every value from 0.0 to 4.0
    passed. The guards below are what makes the repetition load-bearing rather
    than incidental -- a fixture under the floor now reddens here instead of
    silently answering a question about length.

    Deliberately tight, for the reason its sibling in ``test_secret_detector.py``
    records: a heuristic threshold is exactly the kind of value that gets nudged
    to silence a false positive, and nudging it silences true positives too.
    """
    at_the_floor = (string.ascii_uppercase[:6] + string.ascii_lowercase[:6] + "0123") * 3
    under_the_floor = (string.ascii_uppercase[:6] + string.ascii_lowercase[:6] + "012") * 3

    assert round(_entropy(at_the_floor), 4) == 4.0, "the at-floor fixture is not at 4.0 bits"
    assert round(_entropy(under_the_floor), 4) == 3.9069, "the under-floor fixture moved"
    for role, fixture in (("at-floor", at_the_floor), ("under-floor", under_the_floor)):
        assert len(fixture) >= _MIN_CANDIDATE_CHARS, (
            f"the {role} fixture is {len(fixture)} characters, under the "
            f"{_MIN_CANDIDATE_CHARS}-character candidate floor -- the generic family never "
            f"matches it, so its verdict is about length and says nothing about entropy"
        )
        assert all(
            any(check(char) for char in fixture)
            for check in (str.isupper, str.islower, str.isdigit)
        ), (
            f"the {role} fixture no longer carries all three character classes, so the class "
            f"gate can refuse it and entropy stops being what separates the two"
        )
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


def test_a_name_carrying_two_ulids_is_still_clean() -> None:
    """The subtraction removes *every* identifier in a candidate, not the first one.

    ``re.sub`` replaces all occurrences by default, and that default is the whole
    guarantee here: a name carrying two ULIDs is an ordinary thing for this product
    to mint -- one migration superseding another puts both ids in one candidate
    run. Leave the second one in and what remains is 51 characters carrying upper
    case, lower case and digits, which is a candidate that clears every gate and is
    reported as a high-entropy token: an acceptance refused for a filename Theurian
    wrote itself.

    Measured 2026-08-25 by the adversarial round: ``re.sub(..., count=1)`` survived
    the entire suite, because every other ULID fixture in this file and in
    ``NEGATIVES`` carries exactly one. One is the count at which a bug of this
    shape is invisible, so the fixture uses two on purpose.

    The guards below are what make it a pin rather than a coincidence: they assert
    that removing *one* identifier leaves a run that would be reported, so the case
    is red exactly when the subtraction stops at the first match.
    """
    superseded = "01M0D5GSKA479Y85296S745521"  # both are already NEGATIVES fixtures
    superseding = f"01K1AAAAAA01234567890ABCDE-{superseded}-retry-policy-supersedes"
    after_one_removal = superseding.replace("01K1AAAAAA01234567890ABCDE", "", 1)
    assert len(after_one_removal) >= _MIN_CANDIDATE_CHARS, (
        f"removing one identifier leaves {len(after_one_removal)} characters, under the "
        f"{_MIN_CANDIDATE_CHARS}-character floor -- a subtraction that stopped at the first "
        f"match would be refused for length anyway and this case would pin nothing"
    )
    assert all(
        any(check(char) for char in after_one_removal)
        for check in (str.isupper, str.islower, str.isdigit)
    ), (
        "the remainder after one removal no longer carries all three character classes, so "
        "the class gate refuses it whatever the subtraction does and this case is vacuous"
    )

    findings = scan_text(f"see {superseding}.yaml for the current values\n")

    assert findings == (), (
        f"a name carrying two of the product's own identifiers is reported as "
        f"{[(f.family, f.redacted) for f in findings]}; the subtraction removed one of them "
        f"and judged the other as part of the candidate"
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
    outer break catches the ceiling first. This is the shape of input where the
    inner bound is what holds: one 1,008-character candidate carrying forty
    credentials, which without it returns forty findings at a published ceiling of
    twenty, each one paying the ``O(position)`` newline count the ceiling exists to
    cap.

    Re-measured 2026-08-25 with the detector at 1fa8417 and this file at this
    commit: deleting ``_families_inside``'s ``room`` bound reddens three cases here
    -- this one, :func:`test_a_finding_taken_before_a_crowded_run_leaves_that_run_less_room`
    and :func:`test_one_crowded_run_answers_the_published_ceiling_and_no_more` --
    and leaves the other 59 green. This paragraph read "all 48 other cases green"
    when this case was the only one of the three, which is what an unanchored count
    does after two commits: it describes a file that no longer exists.

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
    distinguishes the remaining room from the ceiling -- re-measured 2026-08-25 with
    the detector at 1fa8417 and this file at this commit, mutating the subtraction
    to ``room = max_findings`` returns four findings here for ``max_findings=3``
    and reddens this case alone, leaving the other 61 green.
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


def test_one_crowded_run_answers_the_published_ceiling_and_no_more() -> None:
    """The number a caller actually gets, at the ceiling the product publishes.

    The two cases above pass an explicit ``max_findings`` of three, which is what
    makes them precise -- and is also a shape a wrong implementation can satisfy. A
    rescan that truncated to some *other* small number, or that returned one finding
    whenever it found many, answers three for a ceiling of three and is caught by
    neither. Measured 2026-08-25 by the adversarial round: returning
    ``recovered[:1]`` above a threshold of three survived the whole suite.

    So this runs the default path, and asserts the absolute 20 rather than
    ``MAX_FINDINGS`` for the reason :func:`test_the_scan_stops_at_a_fixed_ceiling`
    records: a fixture and an expectation that both read the constant pass however
    it moves. The columns are asserted with it, so a truncation that keeps the count
    while dropping the *first* findings is red here too.
    """
    assert _MATCHES_INSIDE_ONE_RUN > MAX_FINDINGS, (
        f"one run holds {_MATCHES_INSIDE_ONE_RUN} credentials against a published ceiling of "
        f"{MAX_FINDINGS}; the fixture no longer exceeds the ceiling, so it exercises no "
        f"truncation on the default path at all"
    )
    body = f"config:\n  key: {_CROWDED_REFUSED_RUN}\n"
    first_credential_column = len("  key: ") + len(_STAGE_PREFIX) + 1
    stride = len(_FAMILY_CREDENTIALS["stripe-secret-key"]) + len("-")

    findings = scan_text(body)

    assert [(f.family, f.column) for f in findings] == [
        ("stripe-secret-key", first_credential_column + n * stride) for n in range(20)
    ], (
        f"{len(findings)} findings came back from one refused candidate holding "
        f"{_MATCHES_INSIDE_ONE_RUN} credentials at the default ceiling: "
        f"{[(f.family, f.column) for f in findings][:5]}... The published ceiling is a fixed "
        f"20, and what a caller renders is this list."
    )


#: The four English words the case below has always cited, each ending in the two
#: letters ``openai-api-key`` begins with and each written here with the same ``-``
#: that family looks for. ``risk-<hex40>`` and its siblings are 45 characters, all
#: lower case and digits, so every one reaches the same refused-candidate branch
#: the reported cases do -- the only thing between them and a false positive is
#: that ``\b`` requires a non-word character *before* the ``s``.
#:
#: All four are exercised because the docstring below names all four, and a
#: docstring is read as the claim: one fixture standing for a list is how a case
#: comes to assert less than it says.
_WORDS_ENDING_IN_A_PREFIX: Final[tuple[str, ...]] = ("risk", "task", "desk", "disk")


@pytest.mark.parametrize("word", _WORDS_ENDING_IN_A_PREFIX)
def test_a_prefix_that_is_part_of_a_word_is_not_reported(word: str) -> None:
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
    near_miss = f"{word}-{_HEX40}"
    assert len(near_miss) >= _MIN_CANDIDATE_CHARS, (
        f"{near_miss!r} is under the candidate floor, so it is never consumed and "
        f"never re-examined -- this case would be green without saying anything"
    )
    assert "sk-" in near_miss, (
        "the fixture no longer contains the family's own prefix, so it is not the near miss "
        "it claims to be"
    )

    findings = scan_text(f"the {near_miss} table is unchanged\n")

    assert findings == (), (
        f"{near_miss!r} is reported as "
        f"{[(f.family, f.redacted) for f in findings]}. `sk` here is the tail of an English "
        f"word, and the family's leading `\\b` is what tells the two apart -- dropping it to "
        f"reach inside a candidate run reports every word that ends in those letters."
    )


#: ``stagingsk-<hex40>``: the same credential with the delimiter removed, so there
#: is no word boundary anywhere in front of the ``sk``. 50 characters, all lower
#: case and digits, entropy 4.0791 -- over the floor, so the class gate's missing
#: upper case is what refuses it, exactly as in the reported cases.
_NO_BOUNDARY_BEFORE_THE_PREFIX: Final = f"staging{_OPENAI_SHAPED}"

#: The same credential glued to a CJK character instead of an ASCII one. ``\b`` is
#: Unicode-aware, so ``証`` is a word character exactly as ``g`` is and the ``sk``
#: after it has no document boundary in front of it either. One root cause with two
#: faces, which is how ``_families_inside`` enumerates it -- not two residuals.
#:
#: It is the face that came back, though. The rescan was written once as a search
#: over ``run.group()``, and slicing throws away the character before the run and
#: fabricates a boundary at offset 0, so this exact shape *was* reported for a
#: while. The ASCII face never was: its ``sk`` sits at offset 7 of the slice, where
#: the discarded character is not what decides.
_CJK_GLUED_CREDENTIAL: Final = f"証{_OPENAI_SHAPED}"

#: ``(what is glued in front, the whole fixture)`` for the two faces above.
_NO_BOUNDARY_FIXTURES: Final[tuple[tuple[str, str], ...]] = (
    ("ascii-glue", _NO_BOUNDARY_BEFORE_THE_PREFIX),
    ("cjk-glue", _CJK_GLUED_CREDENTIAL),
)


@pytest.mark.parametrize(
    "glued",
    [fixture for _, fixture in _NO_BOUNDARY_FIXTURES],
    ids=[n for n, _ in _NO_BOUNDARY_FIXTURES],
)
def test_a_credential_with_no_boundary_before_its_prefix_stays_unreported(glued: str) -> None:
    """The residual the repair leaves, recorded as behaviour rather than as a comment.

    Re-examining a refused candidate recovers a credential that sits at a word
    boundary *inside* the run -- which every delimiter provides, because ``-`` is a
    non-word character. It recovers nothing where the run has no boundary at all,
    and it must not: reaching that value means matching ``sk-`` at an arbitrary
    offset, which is the false positive the case above prices.

    **Both faces, because it is one member of the residual and not two.** ASCII glue
    and CJK glue differ only in which word character sits in front of the prefix,
    and a fix that reported one and not the other would be answering the shape of
    the input rather than the boundary rule. The CJK face is also the one that
    reappeared: a rescan over a *slice* of the run cannot see the character before
    it, invents a boundary at offset 0, and reports this fixture -- so this row goes
    red the moment anyone trades ``pos``/``endpos`` back for ``run.group()``.

    So this is a bound, not a miss to be fixed later. If it goes red, the repair
    has widened past word boundaries and
    :func:`test_a_prefix_that_is_part_of_a_word_is_not_reported` is the assertion
    to read next -- that is a decision somebody takes with both cases in front of
    them, not a regression to absorb.
    """
    assert len(_OPENAI_SHAPED) >= _MIN_CANDIDATE_CHARS, (
        "the credential alone is under the candidate floor, so neither fixture is consumed as "
        "a run and this case says nothing about the re-examination it is bounding"
    )

    findings = scan_text(f"key: {glued}\n")

    assert findings == (), (
        f"{glued[:REDACTED_PREFIX_CHARS]}... was reported as "
        f"{[f.family for f in findings]}. There is no word boundary before the `sk`, so "
        f"reaching it means matching the family's prefix at an arbitrary offset -- read the "
        f"false-positive case above before recording this as an improvement."
    )


#: A sentence of ordinary Japanese with a kebab-case slug in it, of the shape an
#: operations note is written in. The candidate run is the ASCII part alone --
#: :data:`_CANDIDATE_CLASS` is ASCII, so the run begins after ``象`` -- and it
#: carries a digit, which matters: the digit gate is *not* what refuses it.
#:
#: Nothing in this sentence is a credential.
_JAPANESE_PROSE_BEFORE_A_SLUG: Final = "監視対象sk-ingest-pipeline-primary-2026q1 を追加"

#: The candidate run inside it, named separately because the guards below are about
#: the run and not about the sentence.
_SLUG_IN_JAPANESE_PROSE: Final = "sk-ingest-pipeline-primary-2026q1"


def test_japanese_prose_before_a_slug_is_not_reported_as_a_credential() -> None:
    """The false positive a slice fabricates, in the language that produces it.

    ``\\b`` is Unicode-aware; :data:`_CANDIDATE_CLASS` is ASCII. A candidate run
    preceded by a CJK character therefore has a *word* character in front of it and
    no boundary at its start -- and a rescan that searches ``run.group()`` instead
    of the document under ``pos``/``endpos`` throws that character away and invents
    one. This sentence then reports an ``openai-api-key`` that the text does not
    contain, and under the default ``block`` policy an acceptance is refused with a
    message telling the author to rotate a secret that does not exist.

    **This is the case that goes red if anyone reverts to substring slicing.** The
    round-one adversarial review measured the class rather than the example: 1,680
    fabricated-boundary matches over 7,312 non-ASCII-preceded runs, against none
    over 5,551 ASCII-preceded ones. That the slug carries a digit is deliberate --
    it clears the digit gate, so the boundary rule is the only thing left that can
    refuse it and the case cannot pass for the neighbouring reason.
    """
    assert len(_SLUG_IN_JAPANESE_PROSE) >= _MIN_CANDIDATE_CHARS, (
        f"the slug is {len(_SLUG_IN_JAPANESE_PROSE)} characters, under the candidate floor, so "
        f"it is never consumed as a run and never rescanned -- a slice would have nothing to "
        f"fabricate a boundary in and this case would be green for the wrong reason"
    )
    assert any(char.isdigit() for char in _SLUG_IN_JAPANESE_PROSE), (
        "the slug no longer carries a digit, so the digit gate refuses it and this case stops "
        "being about the document boundary at all"
    )
    assert not any(char.isupper() for char in _SLUG_IN_JAPANESE_PROSE), (
        "the slug now carries an upper-case character, so the generic family's class gate may "
        "accept the run and report it -- the rescan is then never reached"
    )
    assert _JAPANESE_PROSE_BEFORE_A_SLUG.index(_SLUG_IN_JAPANESE_PROSE) > 0, (
        "the slug now starts the sentence, where the run's first character has a document "
        "boundary in front of it and a slice would decide the same way -- there is nothing "
        "left to fabricate"
    )

    findings = scan_text(f"{_JAPANESE_PROSE_BEFORE_A_SLUG}\n")

    assert findings == (), (
        f"ordinary Japanese prose was reported as {[(f.family, f.redacted) for f in findings]}. "
        f"The character before the run is a word character, so no family may match at the "
        f"run's first position -- a rescan that reads a slice cannot see it and invents a "
        f"boundary there."
    )


#: Kebab-case paths that really occur in a repository, each holding a family's
#: prefix at a *real* document boundary and no ASCII digit inside the match the
#: family would make. Measured 2026-08-25: before the digit gate, each was reported
#: as an ``openai-api-key``, and under the default ``block`` policy that refuses a
#: credential-free proposal and tells its author to rotate a secret that does not
#: exist.
#:
#: ``(what it is, the path as written, the match the family makes inside it)``. The
#: candidate run is the path's stem: ``/`` and ``.`` are outside
#: :data:`_CANDIDATE_CLASS`, so the run reaches neither the directory in front nor
#: the extension behind.
#:
#: The third row is the product's own, and it reaches the branch by a different
#: road: its run *does* carry upper case, all of it inside a ULID that
#: :func:`_looks_like_a_secret` subtracts before it looks at character classes. Two
#: ways to be refused, one gate to catch what follows -- which is why the guard
#: below asks the gate rather than looking for upper case.
DIGIT_FREE_SLUG_FIXTURES: Final[tuple[tuple[str, str, str], ...]] = (
    (
        "a source path",
        "website/src/lib/i18n-sk-locale-and-translation-notes.ts",
        "sk-locale-and-translation-notes",
    ),
    (
        "a backlog note",
        "backlog/task-sk-review-the-ranking-heuristics.md",
        "sk-review-the-ranking-heuristics",
    ),
    (
        "a migration filename",
        f"{_SYNTHETIC_ULID}-add-sk-localisation-notes-for-the-site.yaml",
        "sk-localisation-notes-for-the-site",
    ),
)


@pytest.mark.parametrize(
    ("path", "inner_match"),
    [(path, inner) for _, path, inner in DIGIT_FREE_SLUG_FIXTURES],
    ids=[label for label, _, _ in DIGIT_FREE_SLUG_FIXTURES],
)
def test_a_digit_free_slug_recovered_from_inside_a_run_is_not_reported(
    path: str, inner_match: str
) -> None:
    """What the rescan costs, and the gate that keeps the bill payable.

    Reaching inside a refused candidate reaches inside runs that are mostly English,
    and a kebab-case identifier is where a family's prefix turns up by accident. The
    ``-`` before ``sk`` is a real document boundary, so the boundary rule is right
    to find these and cannot be what refuses them: something else has to.

    A digit is what separates the two populations. Six of the seven declared
    families' fixtures carry one -- every family that can match inside a candidate
    run; none of these paths does. The one digit-free
    family fixture is ``private-key-block``, whose pattern needs spaces and so can
    never match inside a candidate run at all -- the gate costs it nothing.

    **The control below is what stops this being a test about nothing.** These same
    characters are still reported at the top level, because the gate applies to
    *recovered* matches only and the outer pass was deliberately left alone. So the
    case is red exactly when the gate stops working, not when the family stops
    matching -- and if someone deletes the gate, it is this assertion rather than a
    silent behaviour change that says what it cost.
    """
    run = path.rsplit("/", 1)[-1].rsplit(".", 1)[0]
    assert len(run) >= _MIN_CANDIDATE_CHARS, (
        f"{run!r} is {len(run)} characters, under the candidate floor -- the generic family "
        f"never consumes it, nothing is rescanned, and the gate under test is never reached"
    )
    assert not _looks_like_a_secret(run), (
        f"{run!r} now clears the class gate, so the outer pass reports the run whole and the "
        f"rescan -- with the digit gate under test in it -- is never reached at all"
    )
    assert not any(char.isdigit() for char in inner_match), (
        f"{inner_match!r} now carries a digit, so the gate admits it and this case is about "
        f"nothing -- the fixture, not the detector, is what changed"
    )
    control = scan_text(f"key: {inner_match}\n")
    assert [f.family for f in control] == ["openai-api-key"], (
        f"{inner_match!r} is not reported as an openai-api-key on its own "
        f"({[f.family for f in control]}), so the family no longer matches this shape at all "
        f"and the case below would be green whatever the digit gate does"
    )

    findings = scan_text(f"see {path} for the details\n")

    assert findings == (), (
        f"{path!r} is reported as {[(f.family, f.redacted) for f in findings]}. The prefix sits "
        f"at a real boundary inside the run, so only the digit gate can refuse it -- with "
        f"`block` as the default policy this refuses a proposal that contains no credential."
    )


def test_the_digit_gate_counts_exactly_the_ten_ascii_digits() -> None:
    """A constant pinned directly, because no input can isolate one of its members.

    Every other property in this file is asserted through :func:`scan_text`, and
    that is the right default: a behavioural assertion fails for the reason a user
    would notice. This one cannot be reached that way. Every digit-bearing fixture
    in the file carries several distinct digits, so no verdict anywhere turns on any
    *single* member -- measured 2026-08-25 by the round-two adversarial review,
    deleting nine of the ten digits from ``_ASCII_DIGITS`` survives the entire
    suite. Three mutants of one constant, none of them observable.

    So the set is pinned as a set. What that buys is the constant's own stated
    purpose, which is otherwise unpinned in both directions:

    * it must not shrink -- a gate missing ``7`` reads a credential carrying only
      sevens as digit-free and drops it, and nothing else here would say so;
    * it must not widen to ``str.isdigit``, which is ``True`` for a fullwidth zero
      and a Devanagari one. Nothing in :data:`_CANDIDATE_CLASS` reaches those
      codepoints today, so that drift would also be invisible -- until the class
      changes, at which point the gate would have quietly changed meaning too.

    **The constant and the gate are pinned separately, because pinning the constant
    alone leaves the drift alive.** Measured 2026-08-25 while writing this case:
    with only the set asserted, rewriting :func:`_carries_a_digit` to
    ``any(char.isdigit() ...)`` -- abandoning the constant entirely -- passed all 72
    cases in this file. It has to: the two definitions agree on every character a
    candidate run can hold, which is exactly why the constant exists and exactly
    why no input distinguishes them. So the gate is called here directly, on a
    character that run cannot hold today: a function that answers ``True`` for a
    fullwidth zero is ``str.isdigit`` under another name, whatever the constant
    beside it says.
    """
    assert frozenset(string.digits) == _ASCII_DIGITS, (
        f"the digit gate counts {sorted(_ASCII_DIGITS)}, not the ten ASCII digits. A member "
        f"removed makes a credential spelled with that digit read as digit-free and dropped "
        f"from a rescan; a member added is a character the candidate class cannot contain."
    )
    for digit in string.digits:
        assert _carries_a_digit(digit), (
            f"the gate does not count {digit!r} as a digit, so a recovered credential spelled "
            f"with it and no other digit is dropped as digit-free"
        )
    unicode_digits = tuple(
        char for char in _PROBE_ALPHABET if char.isdigit() and not char.isascii()
    )
    assert unicode_digits, (
        "the probe alphabet no longer carries a non-ASCII `str.isdigit` character, so the "
        "second half of this case -- that the gate is not `str.isdigit` -- probes nothing"
    )
    for unicode_digit in unicode_digits:
        assert not _carries_a_digit(unicode_digit), (
            f"the gate counts {unicode_digit!r} ({unicode_digit.isdigit()=}), so it has drifted "
            f"to `str.isdigit` and now changes meaning with the candidate class rather than "
            f"with a decision somebody takes here"
        )


def test_a_family_swallowed_by_a_longer_inner_match_is_not_reported_separately() -> None:
    """The rescan is leftmost-greedy inside the run, and that costs a second finding.

    ``backup-xoxb-<digits>-sk-<hex40>`` is one refused candidate holding two
    credentials. The slack family matches first and its repetition class covers
    ``-``, so the match runs to the end of the run and swallows the ``sk-`` inside
    it; ``finditer`` resumes after what was consumed and the second credential is
    never reported. One finding, not two.

    A recorded decision rather than a defect, and pinned as behaviour so it stays
    one: under ``block`` the refusal fires either way, so nothing is let through --
    under ``warn`` the published list undercounts what the run holds, which is the
    cost. It is member 4 of the residual enumeration in ``_families_inside``.

    The family is asserted, not just the count. A change that reported the ``sk-``
    credential *instead* would keep a count-only assertion green while quietly
    swapping which credential the operator is told about.
    """
    swallowing = f"backup-xoxb-0123456789-sk-{_HEX40}"
    assert len(swallowing) >= _MIN_CANDIDATE_CHARS, (
        f"{swallowing[:REDACTED_PREFIX_CHARS]}... is under the candidate floor, so the run is "
        f"never consumed and the two families are reached by the ordinary pass instead"
    )
    assert not any(char.isupper() for char in swallowing), (
        "the run now carries an upper-case character, so the class gate accepts it and it is "
        "reported whole as a high-entropy token; nothing is recovered from inside it"
    )
    assert swallowing.index("xoxb-") < swallowing.index("sk-"), (
        "the slack prefix no longer comes first, so it is not the match that swallows the "
        "other -- leftmost-greedy is what this case is about"
    )

    findings = scan_text(f"key: {swallowing}\n")

    assert [f.family for f in findings] == ["slack-token"], (
        f"the run reported {[f.family for f in findings]}. Two findings mean the rescan now "
        f"overlaps matches; a lone `openai-api-key` means the slack family stopped matching "
        f"the longer shape -- read `_MAX_TOKEN_CHARS` before recording either as an "
        f"improvement."
    )


# -- Member 3 of the residual: a lookahead that admits only the run's end -----
#
# `google-api-key` and `openai-api-key` are the two families whose trailing
# lookahead admits exactly `_CANDIDATE_CLASS`, which
# `test_the_pattern_geometry_the_residual_enumeration_is_derived_from` derives from
# the live patterns. Inside a maximal run every interior position is followed by a
# character that lookahead forbids, so such a family can only ever end where the
# run ends. The cases below are that geometry measured through `scan_text`.

#: 35 base64url characters from a fixed seed -- upper case, lower case and digits,
#: which is what a real Google API key's body carries. Derived rather than pasted,
#: and ``AIza`` is joined to it at run time: written whole, this is precisely the
#: shape gitleaks' own ``gcp-api-key`` rule matches, and an allowlist entry keyed on
#: a credential-shaped literal is a place a real credential can hide.
_GOOGLE_KEY_BODY: Final = (
    base64.urlsafe_b64encode(hashlib.sha256(b"google api key fixture (#350)").digest())
    .decode()
    .rstrip("=")[:35]
)

#: A realistic ``google-api-key``: ``AIza`` and those 35 characters.
_GOOGLE_SHAPED: Final = f"AIza{_GOOGLE_KEY_BODY}"

#: Sixty of one character. Its job is to drag a run's entropy under the floor so
#: the class gate refuses it -- which is what puts the run in this function's domain
#: while leaving the key it ends with untouched. A prefix rather than a suffix on
#: purpose: a suffix would answer the lookahead question instead of setting it up.
_ENTROPY_KILLING_PREFIX: Final = "a" * 60

#: What may follow the key inside the run, and what the scan then reports. The
#: empty tail is the run's end, where the lookahead is satisfied; every other row is
#: one candidate-class character further on, where it is not. ``-``, ``x`` and ``_``
#: because the class holds all three and a fix that noticed only the delimiter would
#: pass a test that used only ``-``.
_GOOGLE_TAIL_FIXTURES: Final[tuple[tuple[str, list[str]], ...]] = (
    ("", ["google-api-key"]),
    ("-x", []),
    ("x", []),
    ("_", []),
)


@pytest.mark.parametrize(
    ("tail", "expected"),
    _GOOGLE_TAIL_FIXTURES,
    ids=[f"tail={tail!r}" for tail, _ in _GOOGLE_TAIL_FIXTURES],
)
def test_a_google_key_is_recovered_only_where_the_run_itself_ends(
    tail: str, expected: list[str]
) -> None:
    """A fixed repetition plus a lookahead over the whole class leaves one landing place.

    This is the *fixed x equal* quadrant of member 3. ``google-api-key`` repeats
    ``{35}`` exactly, so its match ends 39 characters after ``AIza`` and cannot end
    anywhere else; its lookahead then admits only a character outside
    :data:`_CANDIDATE_CLASS`, which inside a maximal run means the run's end alone.
    One more candidate-class character and the family is not lost to a threshold or
    to a heuristic -- it has nowhere legal to finish. All 64 candidate characters
    kill it, which is what separates this quadrant from
    :func:`test_an_aws_key_is_recovered_where_its_lookahead_still_admits_the_glue`,
    where two of the 64 survive.

    Within this function's domain that is silence, and the rows below measure it as
    ``[]``: the run is refused by the class gate, so the generic family does not
    report it either. The neighbouring case is what says silence is not the general
    answer -- where the gate *passes*, the same tail costs the family's name and not
    the finding.

    **What #356 would change here is two rows of three, not three.** Narrowing this
    family's lookahead to ``(?![0-9A-Za-z])`` moves it from 64 killing characters to
    62: ``-`` and ``_`` become legal ends, so those two rows would report the family
    again, while ``x`` -- an alphanumeric, which the narrowed lookahead still forbids
    -- stays ``[]``. Measured 2026-08-25 by running that mutant against this file:
    the ``-x`` and ``_`` rows redden and the ``x`` row does not. The sentence here
    said "the last three rows" until round three measured it; the mistake was
    reading "narrower" as "admits everything it used to forbid".
    """
    run = f"{_ENTROPY_KILLING_PREFIX}-{_GOOGLE_SHAPED}{tail}"
    control = scan_text(f"key: {_GOOGLE_SHAPED}\n")
    assert [f.family for f in control] == ["google-api-key"], (
        f"the fixture is not reported as a google-api-key even on its own "
        f"({[f.family for f in control]}), so every row below would be measuring a value that "
        f"was never a credential"
    )
    assert any(char.isdigit() for char in _GOOGLE_SHAPED), (
        "the key carries no digit, so the digit gate is what drops it and these rows stop "
        "being about the lookahead at all"
    )
    assert not _looks_like_a_secret(run), (
        f"the run clears the class gate, so the generic family reports it whole and nothing is "
        f"rescanned -- lengthen {_ENTROPY_KILLING_PREFIX[:4]}... until its entropy is under the "
        f"floor again, or this case measures the neighbouring one"
    )
    assert len(run) >= _MIN_CANDIDATE_CHARS, (
        "the run is under the candidate floor, so it is never consumed and never rescanned"
    )

    findings = scan_text(f"key: {run}\n")

    assert [f.family for f in findings] == expected, (
        f"a run ending {tail!r} after the key reported {[f.family for f in findings]}, not "
        f"{expected}. The family's match ends a fixed 39 characters after `AIza` and its "
        f"lookahead admits only a non-candidate character, so any tail at all puts the end it "
        f"needs out of reach."
    )


def test_a_google_key_in_a_run_that_clears_the_gate_costs_the_family_not_the_finding() -> None:
    """Losing the family is not losing the finding, and the difference is the gate.

    The rows above measure silence, and they measure it *inside this function's
    domain* -- runs the class gate refused. Read alone they suggest a trailing
    character hides a Google key, and that is not what happens in general: a run
    carrying a real key usually carries the upper case, lower case and digits that
    take it past the gate, and then the generic family reports the run whole. The
    name in the report is wrong and the report is still there, so ``propose accept``
    at the default ``block`` still refuses.

    This is the case that keeps the docstring's "**Losing the family is not always
    losing the finding**" honest, and it is the one that makes the silence rows say
    something specific rather than something alarming.
    """
    run = f"{_STAGE_PREFIX}{_GOOGLE_SHAPED}-x"
    assert _looks_like_a_secret(run), (
        "the run no longer clears the class gate, so this case has become another copy of the "
        "silence rows above -- it exists to measure the *other* side of that gate"
    )

    findings = scan_text(f"key: {run}\n")

    assert [f.family for f in findings] == [HIGH_ENTROPY], (
        f"a gate-clearing run holding a Google key reported {[f.family for f in findings]}. "
        f"Nothing at all would mean a proposal carrying this key is accepted at the default "
        f"`block` policy; two findings would mean one value is reported twice."
    )


#: A real ``aws-access-key-id``, read from :data:`_FAMILY_CREDENTIALS` so the value
#: this quadrant is measured with and the value the family's own positive case
#: reports cannot drift apart. ``AKIA`` and sixteen upper-case characters and
#: digits: 20 characters, a *fixed* match length, which is the axis this case is
#: about.
_AWS_SHAPED: Final = _FAMILY_CREDENTIALS["aws-access-key-id"]

#: What may follow the key inside the run, and what the scan then reports. Two of
#: the 64 candidate characters leave the family reachable, and they are exactly the
#: two its lookahead omits; a letter and a digit stand for the other 62, which is
#: the whole difference between this quadrant and ``google-api-key``'s.
_AWS_GLUE_FIXTURES: Final[tuple[tuple[str, list[str]], ...]] = (
    ("", ["aws-access-key-id"]),
    ("x", []),
    ("7", []),
    ("-", ["aws-access-key-id"]),
    ("_", ["aws-access-key-id"]),
)


@pytest.mark.parametrize(
    ("glue", "expected"),
    _AWS_GLUE_FIXTURES,
    ids=[f"glue={glue!r}" for glue, _ in _AWS_GLUE_FIXTURES],
)
def test_an_aws_key_is_recovered_where_its_lookahead_still_admits_the_glue(
    glue: str, expected: list[str]
) -> None:
    """The *fixed x strict-subset* quadrant: one end offset, two characters that survive it.

    ``aws-access-key-id`` shares ``google-api-key``'s fixed repetition -- its match
    is always exactly 20 characters -- so it too has a single offset at which it can
    end. What it does not share is the lookahead: ``(?![0-9A-Za-z])`` omits ``-``
    and ``_``, so those two candidate characters are legal ends and the other 62 are
    not. Reading the lookahead column alone puts this family in
    ``google-api-key``'s bucket and reading the repetition column alone puts it in
    ``openai-api-key``'s; it belongs in neither, which is why member 3 is derived
    from both axes.

    The stakes are not theoretical: measured through the real CLI at the default
    ``block``, ``AKIA`` and sixteen characters is refused, and the same key followed
    by one letter of glue is **accepted and the body lands**. The last two rows are
    what keeps that from being read as "any glue hides an AWS key" -- glue it with
    ``-`` or ``_`` and the family is reported again.

    **The glue in front is identical characters on purpose, and that is the trap
    this case was nearly built on.** The run has to fail the class gate or the
    generic family reports it and nothing is rescanned -- and *distinct* lower-case
    glue does not fail it: 24 distinct lower-case characters after this key measure
    5.41 bits and clear the floor, where 24 identical ones measure 2.91 and do not.
    Lower case is not the property; low entropy is. The gate assertion below is what
    turns that mistake into a red test instead of a case that measures the
    neighbouring quadrant.
    """
    run = f"{_ENTROPY_KILLING_PREFIX}-{_AWS_SHAPED}{glue}"
    control = scan_text(f"key: {_AWS_SHAPED}\n")
    assert [f.family for f in control] == ["aws-access-key-id"], (
        f"the fixture is not reported as an aws-access-key-id even on its own "
        f"({[f.family for f in control]}), so every row below would be measuring a value that "
        f"was never a credential"
    )
    assert any(char.isdigit() for char in _AWS_SHAPED), (
        "the key carries no digit, so the digit gate is what drops it and these rows stop "
        "being about the lookahead at all"
    )
    assert not _looks_like_a_secret(run), (
        "the run clears the class gate, so the generic family reports it whole and nothing is "
        "rescanned. Distinct glue characters raise a run's entropy past the floor -- the "
        "prefix has to stay repetitive for this case to reach the branch it is about."
    )
    assert len(run) >= _MIN_CANDIDATE_CHARS, (
        "the run is under the candidate floor, so it is never consumed and never rescanned"
    )

    findings = scan_text(f"key: {run}\n")

    assert [f.family for f in findings] == expected, (
        f"a run ending {glue!r} after the key reported {[f.family for f in findings]}, not "
        f"{expected}. The match is a fixed 20 characters, so it ends at one offset only, and "
        f"its lookahead admits exactly `-` and `_` there -- 62 of the 64 candidate characters "
        f"put the family out of reach and two do not."
    )


# -- What each family is spelled with: its class, and its leading anchor -------
#
# Both cases below are false-positive bounds, and both were surviving mutants in
# round three: a widened repetition class and a dropped anchor each report a
# credential that is not there, and every positive case in this file stays green
# while they do -- a positive fixture matches a *wider* pattern just as happily.

#: ``AKIA``, read from the live fixture table rather than written again, and
#: sixteen lower-case letters. The length is read from the same row: a fixed
#: repetition only matches at its exact count, so a tail of any other length would
#: leave this case green under the widening it exists to catch.
_AWS_PREFIX: Final = next(
    prefix for family, prefix, _ in PATTERN_FAMILY_FIXTURES if family == "aws-access-key-id"
)
_AWS_TAIL_LENGTH: Final = len(_FAMILY_CREDENTIALS["aws-access-key-id"]) - len(_AWS_PREFIX)


def test_an_aws_key_id_spelled_in_lower_case_is_not_reported() -> None:
    """The repetition class is part of the credential's definition, not decoration.

    An AWS access key id is upper case and digits -- ``[0-9A-Z]{16}`` is the format,
    not a convenience. Widen that class to ``[0-9A-Za-z]`` and the family starts
    reporting any sixteen alphanumerics after the letters ``AKIA``, which is a false
    positive with the same cost as ``risk-`` being read as a credential: under the
    default ``block`` policy an acceptance is refused for text that holds no secret.

    Nothing else here notices. Every positive fixture in this file is upper case and
    digits, so it matches the widened class exactly as well -- measured 2026-08-25 by
    the round-three adversarial review, the widening survived the whole suite. A
    false-positive bound is the only kind of case that can catch a pattern getting
    *more* permissive.

    The fixture is deliberately shorter than the candidate floor, so the generic
    family cannot consume it: a run that reported ``high-entropy-token`` would be
    green here for a reason that has nothing to do with the class.
    """
    lower_case_tail = "notesandthoughts"
    not_a_key = f"{_AWS_PREFIX}{lower_case_tail}"
    assert len(lower_case_tail) == _AWS_TAIL_LENGTH, (
        f"the tail is {len(lower_case_tail)} characters and the family repeats a fixed "
        f"{_AWS_TAIL_LENGTH}; at any other length the widened class would not match either "
        f"and this case would be green without saying anything"
    )
    assert lower_case_tail.isalpha() and lower_case_tail.islower(), (
        "the tail is no longer all lower case, so it may sit inside the family's real class "
        "and this case stops being about the widening"
    )
    assert len(not_a_key) < _MIN_CANDIDATE_CHARS, (
        f"{not_a_key!r} is at or over the candidate floor, so the generic family can consume "
        f"it and report something whatever the specific family's class admits"
    )

    findings = scan_text(f"the {not_a_key} branch was merged\n")

    assert findings == (), (
        f"{not_a_key!r} is reported as {[(f.family, f.redacted) for f in findings]}. Sixteen "
        f"lower-case letters after `AKIA` are not an access key id -- the format is upper case "
        f"and digits, and a class that admits letters reports ordinary text as a credential."
    )


def test_a_google_key_glued_to_a_word_character_is_not_reported() -> None:
    """The leading anchor, on the family whose prefix is a word rather than a delimiter.

    ``\\bAIza`` requires a non-word character in front of the prefix, exactly as
    ``\\bsk-`` does -- and this is where that matters most, because the rescan looks
    at *every* position inside a refused run rather than only its first. Drop the
    anchor and ``AIza`` is found in the middle of a word, which is the same false
    positive :func:`test_a_prefix_that_is_part_of_a_word_is_not_reported` prices for
    ``sk-``: ordinary text refused under the default ``block``.

    That guard exists for ``openai-api-key`` and did not for this family. Measured
    2026-08-25 by the round-three adversarial review: dropping ``\\b`` from ``AIza``
    survived the whole suite, because every positive fixture puts the prefix at a
    boundary and a pattern that no longer requires one still matches there.

    The run has to fail the class gate, or the generic family reports it and the
    anchor is never consulted -- so the glue is the repetitive prefix the sibling
    cases use, with one ordinary letter between it and the key.
    """
    word_glued = f"{_ENTROPY_KILLING_PREFIX}x{_GOOGLE_SHAPED}"
    control = scan_text(f"key: {_GOOGLE_SHAPED}\n")
    assert [f.family for f in control] == ["google-api-key"], (
        f"the key is not reported as a google-api-key even at a boundary "
        f"({[f.family for f in control]}), so this case would be green with the anchor gone"
    )
    assert word_glued[word_glued.index(_GOOGLE_SHAPED) - 1].isalnum(), (
        "the character before the key is no longer a word character, so there is a boundary "
        "in front of the prefix after all and the anchor is not what refuses this"
    )
    assert not _looks_like_a_secret(word_glued), (
        "the run clears the class gate, so it is reported whole by the generic family and the "
        "rescan -- where the anchor decides -- is never reached"
    )
    assert len(word_glued) >= _MIN_CANDIDATE_CHARS, (
        "the run is under the candidate floor, so it is never consumed and never rescanned"
    )

    findings = scan_text(f"key: {word_glued}\n")

    assert findings == (), (
        f"a key glued to a word character is reported as "
        f"{[(f.family, f.redacted) for f in findings]}. The rescan tries every position inside "
        f"a refused run, so `\\b` in front of `AIza` is the only thing keeping the family out "
        f"of the middle of a word."
    )


#: How far the ``sk-`` may sit from a refused run's end and still be recovered:
#: ``len("sk-")`` plus the family's 255-character repetition cap. Written as the
#: number rather than derived from :data:`_MAX_TOKEN_CHARS`, for the reason the
#: ceiling cases record -- a fixture and an expectation that both read the constant
#: agree with each other however it moves.
_OPENAI_REACH: Final = 258

_OPENAI_DISTANCE_FIXTURES: Final[tuple[tuple[str, int, list[str]], ...]] = (
    ("at the reach", _OPENAI_REACH, ["openai-api-key"]),
    ("one past the reach", _OPENAI_REACH + 1, []),
)


@pytest.mark.parametrize(
    ("distance", "expected"),
    [(distance, expected) for _, distance, expected in _OPENAI_DISTANCE_FIXTURES],
    ids=[label for label, _, _ in _OPENAI_DISTANCE_FIXTURES],
)
def test_the_openai_family_reaches_a_runs_end_from_258_characters_and_no_further(
    distance: int, expected: list[str]
) -> None:
    """The same geometry with a capped repetition instead of a fixed one.

    ``openai-api-key``'s lookahead also admits exactly the candidate class, so it
    too can only end where the run ends -- but its repetition runs to
    ``_MAX_TOKEN_CHARS`` rather than to a fixed count, so it can *reach* that end
    from any distance up to ``len("sk-") + 255``. One character further and the
    repetition exhausts before the run's end, the lookahead is never satisfied, and
    the credential is not reported at all.

    The pair is the threshold itself, and the threshold is the ReDoS budget this
    module spends deliberately: the cap is what bounds backtracking on a run the
    input chooses. Moving it is a decision about that budget, so it goes red here
    rather than quietly changing what a scan finds.
    """
    tail = (hashlib.sha256(b"openai reach fixture (#350)").hexdigest() * 8)[: distance - len("sk-")]
    run = f"{_STAGE_PREFIX}sk-{tail}"
    assert len(run) - run.index("sk-") == distance, (
        f"the fixture puts the run's end {len(run) - run.index('sk-')} characters from `sk-`, "
        f"not {distance}; the case measures a distance and this one is not it"
    )
    assert not _looks_like_a_secret(run), (
        "the run clears the class gate, so the generic family reports it and the rescan -- "
        "where this threshold lives -- is never reached"
    )

    findings = scan_text(f"key: {run}\n")

    assert [f.family for f in findings] == expected, (
        f"a credential {distance} characters from its run's end reported "
        f"{[f.family for f in findings]}, not {expected}. The repetition caps at 255, so the "
        f"reach from `sk-` is 258; check `_MAX_TOKEN_CHARS` before recording a new number."
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
    believes it does.** Re-measured 2026-08-25 with the detector at 1fa8417:
    removing the specific families from the top-level alternation reddens **10**
    selected cases against this file as it stood at that commit, and **12** with
    this commit's additions -- six parameters of
    ``test_each_pattern_family_reports_its_own_shape``,
    ``test_findings_come_back_in_document_order``, the reachability guard's
    ``stripe-secret-key`` row, this case,
    :func:`test_a_finding_taken_before_a_crowded_run_leaves_that_run_less_room`,
    and both rows of
    :func:`test_a_digit_free_slug_recovered_from_inside_a_run_is_not_reported`.
    The ``slack-token`` parameter is *not* one of them: its fixture is over the
    candidate floor, so the run is refused and the rescan -- which reads
    ``_SPECIFIC_FAMILIES`` and is untouched by that mutation -- recovers it anyway.

    The 19 recorded here before does not reproduce at any version measured, and an
    unanchored count is why nobody noticed. What this case adds over the others is
    the boundary row of the table it belongs to -- a *glued* credential whose run is
    too short to consume -- and the column on that path, which nothing else pins. 27
    characters is short enough that a report at the run's start looks nearly right.
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
