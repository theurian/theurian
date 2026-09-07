"""The secret detector that guards the plugin tree (SEC-5, ADR-0011, #201, #43).

``test_plugin_boundary.py`` asks whether the plugin can reach Core. This asks a
different question -- whether a credential can reach the repository -- and it lived
there only because the scan happens to walk the plugin tree. Split out at 1,449
lines against this project's 800-line guidance, with nothing shared but that
directory: the detector, the scan that applies it, and the self-tests that prove
both can fail are all here.
"""

from __future__ import annotations

import base64
import hashlib
import math
import pathlib
import re
import string
from collections import Counter
from typing import Final

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[4]
PLUGIN = REPO_ROOT / "plugins" / "claude-code"


def _shannon_entropy(token: str) -> float:
    """Bits per character, from ``token``'s own character frequencies.

    Extracted from :func:`_looks_like_a_secret` so a fixture's entropy can be
    asserted with the arithmetic the detector actually applies. A second
    implementation could agree with the comment beside a fixture while disagreeing
    with the code that judges it.
    """
    counts = Counter(token)
    return -sum((n / len(token)) * math.log2(n / len(token)) for n in counts.values())


def _looks_like_a_secret(token: str) -> bool:
    """Whether ``token`` resembles CSPRNG output rather than prose.

    Length alone is not a signal: a kebab-case ADR filename is long too. Theurian
    tokens come from ``secrets.token_urlsafe``, which yields base64url with mixed
    case, digits, and near-uniform character frequency. Requiring all three
    together separates a real token from an identifier a human typed.
    """
    if not (
        any(c.isupper() for c in token)
        and any(c.islower() for c in token)
        and any(c.isdigit() for c in token)
    ):
        return False

    return _shannon_entropy(token) >= 4.0


#: A run of base64url-ish characters long enough to be a credential. The ``{32,}``
#: floor is why ``THEURIAN_MCP_TOKEN`` never reaches the detector at all -- see
#: :func:`test_the_detector_refuses_the_name_of_the_variable_that_carries_a_token`.
_CANDIDATE: Final = re.compile(r"\b[A-Za-z0-9_\-]{32,}\b")

#: Suffixes the scan does not open. Only formats that are actually binary belong
#: here. ``.svg`` was on this list and was the one *text* format on it, so a token
#: pasted into an asset was invisible; measured, a planted token in an ``.svg`` was
#: not reported. Removing it changed no verdict today -- the plugin tree holds no
#: image files at all, and the only file the scan skips is ``LICENSE`` (re-measured
#: at ``0af1568``, a commit on #501's branch and not on ``main``) -- which is the
#: argument for closing the hole before an asset arrives rather than after.
#:
#: :func:`test_no_plugin_file_imports_theurian` keeps its own ``.svg`` skip on
#: purpose: an ``import theurian`` inside a drawing is not a source-level
#: dependency, while a credential inside one is still a credential.
_UNSCANNED_SUFFIXES: Final = frozenset({".png", ".jpg"})


def _readable_text(path: pathlib.Path) -> str | None:
    """``path``'s text, or ``None`` where it is not UTF-8.

    The one decode policy, used by both walks in this file. They are deliberately
    *separate* walks -- see
    :func:`test_this_file_still_knows_what_the_scan_meets` -- but a file that is
    unreadable to one and readable to the other is a difference with no reason, and
    there was one: the scan skipped on ``UnicodeDecodeError`` while the pin test
    read with ``errors="ignore"``. Nothing in the tree decodes badly today, so the
    two agreed by luck; the day a binary lands they would have disagreed about what
    the tree contains.
    """
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:  # pragma: no cover - no such file in the tree today
        return None


def _secrets_in(tree: pathlib.Path) -> list[str]:
    """Every candidate under ``tree`` that :func:`_looks_like_a_secret` accepts.

    The root is a parameter so that the code scanning the real plugin can be
    pointed at a tree with a token planted in it. Until it was, the scan below
    could not fail: measured on ``486bb99``, a commit on #244's branch and not on
    ``main``, when this section still lived in ``test_plugin_boundary.py``,
    replacing its body with ``assert True`` and making it skip every file both left
    that file green, because no file in the plugin tree holds a candidate that
    reaches the detector's positive path. The nine it does hold are
    :data:`_TREE_CANDIDATES`, and not one carries an upper-case letter.
    """
    violations: list[str] = []
    for path in tree.rglob("*"):
        if not path.is_file() or path.suffix in _UNSCANNED_SUFFIXES or path.name == "LICENSE":
            continue
        text = _readable_text(path)
        if text is None:
            continue
        violations.extend(
            f"{path.relative_to(tree)}: {match.group()[:8]}..."
            for match in _CANDIDATE.finditer(text)
            if _looks_like_a_secret(match.group())
        )
    return violations


def _suffixes_present_in(tree: pathlib.Path) -> tuple[str, ...]:
    """Every file suffix under ``tree``, whether or not the scan opens it.

    Deliberately not filtered by :data:`_UNSCANNED_SUFFIXES`. Deriving the planting
    sites from the scan's own skip list would make the two agree by construction --
    one edit would add a suffix to the skip list and delete its planting site in the
    same stroke, and both would go quiet together.

    That is not hypothetical. The site list was written by hand as ``.sh``, ``.md``,
    ``.json`` and ``.svg`` while the tree carries ``.md``, ``.json``, ``.sh`` and
    ``.yaml``; measured, adding ``.yaml`` to the skip list then left all twenty
    tests green with a token sitting in the real ``compatibility.yaml``.
    """
    return tuple(sorted({path.suffix for path in tree.rglob("*") if path.is_file()}))


def test_no_plugin_file_contains_a_high_entropy_secret() -> None:
    """Catches a token pasted in during debugging and forgotten.

    Bounded by what the detector requires, and the residual is recorded rather than
    left to be discovered: a real ``secrets.token_urlsafe(32)`` that happens to
    contain no digit is invisible here. That is 0.065% of tokens --
    ``(54/64)**42 * 13/16``, because the 43rd character carries only four bits, so
    it draws from sixteen symbols of which three are digits -- measured at 10,315
    in 16,000,000 samples (0.0645%). Whether this repository's gitleaks scan would
    catch such a token has not been measured, so nothing here claims it does.
    Threat model T-8 carries the same note.
    """
    violations = _secrets_in(PLUGIN)

    assert not violations, f"Possible secrets in plugin files: {violations}"


# -- The detector's own self-tests (#201, #43) -----------------------------
#
# What the single random assertion left unpinned. Measured on c872ab9, when this
# section still lived in `test_plugin_boundary.py`, by changing one thing in the
# detector at a time and running that file:
#
#   drop the digit requirement            SURVIVED
#   drop the lower-case requirement       SURVIVED
#   drop the upper-case requirement       KILLED, by an ADR filename that clears
#                                         the entropy floor by 0.064 bits
#   lower the floor from 4.0 to 0.0       SURVIVED
#   lower the floor to anything above 1.6 SURVIVED
#   raise the floor                       NO DETERMINISTIC VERDICT -- see below
#
# The last row is the one that has to be said carefully, because c872ab9's positive
# fixture was a fresh `secrets.token_urlsafe(32)` on every run and its entropy is a
# draw, so a raised floor makes the *verdict itself* random rather than making the
# mutation survive. Over 200,000 draws (2026-08-18): minimum 4.1711, median 4.8506,
# maximum 5.2867, so a floor at 4.18 reddened 1 run in 200,000, one at 4.5 reddened
# 0.43%, and one at 4.8 reddened 33.13% -- a coin flip reported as a verdict. The
# minimum is a sample minimum and not a bound; #201's own 200,000-draw run put it at
# 4.190. Recording "survived up to 4.8" as though it were deterministic would be
# precisely the defect this file exists to remove, so it is not recorded that way.
#
# In the present tense, and deterministically: `_AT_THE_FLOOR` and
# `_UNDER_THE_FLOOR` pin the threshold to the half-open window (log2(15), 4.0] --
# 3.9069 exclusive to 4.0 inclusive -- for every run, because no fixture below is
# drawn.
#
# Every fixture below exists to turn one of those rows red, and each docstring names
# which rather than restating this table.


#: A fixed byte string, hashed into :data:`_TOKEN_SHAPED` below. Nothing about the
#: value matters except that it never changes; it names the issues so that whoever
#: edits it knows which measurements go stale.
_FIXTURE_SEED: Final = b"theurian plugin-boundary secret-detector fixture (#201, #43)"


def _deterministic_token(seed: bytes) -> str:
    """A 43-character base64url string derived from ``seed`` rather than drawn.

    The two obvious alternatives fail in opposite ways.

    ``secrets.token_urlsafe(32)`` -- what the self-test below used until #201 --
    contains no digit in 0.065% of draws, and :func:`_looks_like_a_secret` requires
    one, so the assertion reddened on roughly one run in 1,500 with nothing wrong.
    The rate is ``(54/64)**42 * 13/16`` rather than ``(54/64)**43``: 32 bytes is
    256 bits and 43 base64url characters hold 258, so the *last* character carries
    only four bits and draws from sixteen symbols -- measured, exactly
    ``048AEIMQUYcgkosw`` -- of which three are digits. Observed 10,315 in 16,000,000
    samples (0.0645%) against 10,350 predicted; the superseded model predicted
    10,748, which the same data rejects at 4.2 standard deviations. Every failure
    was the missing digit; the entropy floor never fired.

    That is worse than noise here, because ``tools/mutate.py`` reads a red suite as
    a killed mutant: #43 caught one such red inside a seventeen-run mutation sweep,
    where it would otherwise have turned a *surviving* mutant into a false claim
    that something is pinned.

    A pasted literal would be deterministic too, and the scanner is no help. CI's
    `Secret scan` job runs gitleaks over the full history, but measured with
    gitleaks 8.30.1 and this repository's ``.gitleaks.toml``, a token written in
    this file's own notation -- ``NAME: Final = "..."`` -- is *not* reported, while
    the same token in an unannotated assignment beside a keyword is. So a real
    credential pasted here would reach ``main`` with CI green, and no allowlist
    entry would ever have been owed. What is left is a reviewer, who cannot tell a
    fixture from a credential somebody leaked by looking. Deriving the characters
    at run time removes the question rather than answering it: there is no literal
    to judge, by either.

    A SHA-256 digest is 32 bytes -- exactly what ``token_urlsafe(32)`` encodes --
    so base64url of one carries the length and alphabet of a real Theurian token.
    ``hexdigest()`` would not: its output is lower case and digits only, so the
    *class gate* refuses it for the missing upper-case letter, and the entropy
    branch is never reached. (It would fail there too -- 64 characters over 16
    symbols is 3.8369 bits for this seed -- but that is not the reason.)
    """
    return base64.urlsafe_b64encode(hashlib.sha256(seed).digest()).decode().rstrip("=")


#: Every condition :func:`_looks_like_a_secret` imposes, satisfied at once: an
#: upper-case letter, a lower-case letter, a digit, and 32 distinct characters over
#: 43 positions giving 4.8307 bits against a floor of 4.0. Those two numbers are a
#: pure function of :data:`_FIXTURE_SEED`, and they are assertions rather than
#: narration -- :func:`test_the_deterministic_fixture_keeps_the_shape_of_a_real_token`
#: reddens if a seed edit falsifies either.
_TOKEN_SHAPED: Final = _deterministic_token(_FIXTURE_SEED)

#: One candidate per character class the detector requires, each missing that class
#: and nothing else. Pinning a requirement takes a candidate that satisfies every
#: *other* condition, so that deleting the requirement is the only thing that could
#: change its verdict -- and neither negative this file shipped with was one: an ADR
#: filename is refused on its missing upper case whatever the digit rule says, and
#: ``THEURIAN_MCP_TOKEN`` is refused three ways at once (no lower case, no digit,
#: 3.7255 bits).
#:
#: Each candidate holds 32 distinct characters exactly once, so its entropy is
#: exactly ``log2(32)`` = 5.0 bits: over the floor *by construction*, which is what
#: makes the missing class provably the only thing that can refuse it.
_MISSING_ONE_CLASS: Final = {
    "no-digit": string.ascii_uppercase[:16] + string.ascii_lowercase[:16],
    "no-upper-case-letter": string.ascii_lowercase[:22] + string.digits,
    "no-lower-case-letter": string.ascii_uppercase[:22] + string.digits,
}

#: Upper, lower and digit all present, so the character-class gate passes and the
#: entropy floor is the only thing left that can refuse it: three distinct
#: characters repeated twelve times is ``log2(3)`` = 1.585 bits. No fixture was ever
#: *refused* by that branch before this one -- the random token cleared it and both
#: negatives stopped at the class gate above.
_LOW_ENTROPY: Final = "Aa1" * 12

#: The floor from both sides, one distinct character apart. :data:`_LOW_ENTROPY`
#: proves a floor exists; it cannot say *where*, and the table at the top of this
#: section is what happens to a threshold nothing pins.
#:
#: Sixteen distinct characters twice each is uniform over sixteen symbols, so its
#: entropy is ``log2(16)`` = 4.0, and 1/16 and its logarithm are both exactly
#: representable, so the computed value is exactly 4.0 on this platform -- measured,
#: with ``math.log2`` and with the ``log(x)/log(2)`` fallback CPython uses where
#: ``log2`` is absent. The margin above the floor is therefore 0 ulps, not a
#: tolerance: a libm returning ``log2(0.0625)`` one ulp high would yield
#: 3.9999999999999996 and redden :func:`test_the_entropy_floor_is_where_the_detector_says_it_is`.
#: Fifteen distinct characters give ``log2(15)`` = 3.9069, so the pair pins the
#: constant to 0.093 bits and pins the comparison as ``>=`` rather than ``>``.
_AT_THE_FLOOR: Final = (string.ascii_uppercase[:6] + string.ascii_lowercase[:6] + "0123") * 2
_UNDER_THE_FLOOR: Final = (string.ascii_uppercase[:6] + string.ascii_lowercase[:6] + "012") * 2

#: Every candidate the scan actually meets in the plugin tree -- measured rather
#: than remembered, and re-measured at ``0af1568``, a commit on #501's branch and
#: not on ``main``. There are nine, and an earlier version of this file claimed
#: two, one of which the scan never sees at all. Four are ADR filenames quoted in
#: documents (``0002-`` and ``0012-`` in ``README.md``, ``0013-`` in ``README.md``,
#: ``CHANGELOG.md`` and ``commands/propose.md``, and ``0030-`` in
#: ``CHANGELOG.md``); two are the names of tests in ``test_plugin_boundary.py``,
#: quoted by ``/theurian:upgrade``'s document; and three are the names of tests in
#: ``test_config_key_call_sites.py``, quoted by the plugin ``CHANGELOG.md``'s
#: mutation record. The count was five until that record named its three tests, and
#: eight until the same file's ``[Unreleased]`` correction linked ADR-0030 -- which
#: is how a measurement moves without anything being wrong.
#:
#: Not one carries an upper-case letter, so the detector's positive path never
#: executes against the real tree. That is why the scan needs
#: :func:`test_the_scan_reports_a_token_planted_in_any_text_file` to be able to fail
#: at all, and why these eight are held here as the negative population rather than
#: standing in for one.
#:
#: :func:`test_this_file_still_knows_what_the_scan_meets` fails if the tree and this
#: tuple disagree, so the count above stays a measurement rather than a memory.
_TREE_CANDIDATES: Final = (
    "0002-single-local-daemon-over-streamable-http",
    "0012-plugin-does-not-autoregister-mcp-server",
    "0013-ai-writes-produce-proposals",
    "0030-github-review-ingestion-spawns-gh",
    "test_upgrade_command_names_the_same_flags_as_lib_sh",
    "test_upgrade_command_placeholders_name_keys_the_schema_declares",
    "test_the_ingest_command_states_the_config_bound_and_nothing_beside_it",
    "test_the_scan_bound_is_byte_identical_where_two_surfaces_publish_it",
    "test_the_secret_scan_description_is_exactly_what_this_file_records",
)

#: One planted token per suffix the plugin tree actually carries -- measured at
#: import rather than listed -- plus ``.svg``, which the tree does not carry and
#: which is kept as the regression pin for the skip list that once hid it.
#:
#: Derived, so that adding *any* present format to :data:`_UNSCANNED_SUFFIXES`
#: reddens a planting case. The hand-written version covered four suffixes and the
#: tree carries a fifth, so the claim "adding a text format reddens the planting
#: test" was false for every format nobody had thought of, ``.yaml`` included.
#:
#: A suffix-less site is included because the tree has one file with no suffix
#: (``LICENSE``, skipped by *name*), so this also pins that a suffix-less file that
#: is not the licence is still read.
_PLANTING_SITES: Final = tuple(
    f"planted/leaked{suffix}" for suffix in sorted({*_suffixes_present_in(PLUGIN), ".svg"})
)


def test_the_secret_detector_actually_detects_a_secret() -> None:
    """A detector nobody has proved works is a test that always passes.

    :func:`test_no_plugin_file_contains_a_high_entropy_secret` is worth exactly as
    much as :func:`_looks_like_a_secret`: a detector that answered ``False`` for
    everything would report a clean plugin tree over a pasted token for as long as
    anyone cared to look. This is the assertion that stops it.
    """
    detected = _looks_like_a_secret(_TOKEN_SHAPED)

    assert detected, (
        "the detector no longer fires on a 43-character base64url string with "
        "mixed case, a digit and 4.83 bits of entropy per character -- the shape "
        "of `secrets.token_urlsafe(32)`, which is what a leaked Theurian token in "
        "the plugin tree would look like. If this reddened because _FIXTURE_SEED "
        "changed, re-measure the fixture before suspecting the detector: 0.065% of "
        "tokens of this length contain no digit (#201)."
    )


@pytest.mark.parametrize(
    ("missing", "candidate"),
    list(_MISSING_ONE_CLASS.items()),
    ids=list(_MISSING_ONE_CLASS),
)
def test_the_secret_detector_refuses_a_candidate_missing_one_character_class(
    missing: str, candidate: str
) -> None:
    """Mixed case and a digit together are what separate a token from a name.

    The detector's own docstring says why: length alone is not a signal, because a
    kebab-case ADR filename is long too. Each of the three requirements therefore
    has to be load-bearing on its own, and two of them were not -- rows one and two
    of the table at the top of this section.

    A detector that stops requiring a class does not go quiet, it goes loud: it
    begins reporting ordinary identifiers as secrets, and a scan that cries wolf is
    a scan people learn to override.
    """
    detected = _looks_like_a_secret(candidate)

    assert not detected, (
        f"a 32-character candidate with {missing} -- entropy 5.0 bits, so the floor "
        f"is not what refuses it -- is reported as a secret: {candidate!r}"
    )


def test_the_secret_detector_refuses_a_low_entropy_candidate() -> None:
    """The entropy floor is the half of the detector that no fixture ever reached.

    Character classes alone would call any capitalised word with a digit on the end
    a secret. The floor is what makes the answer a statement about *randomness*, and
    it was the one condition nothing here tested -- row four of the table at the top
    of this section -- because every negative fixture was already refused at the
    class gate above.
    """
    detected = _looks_like_a_secret(_LOW_ENTROPY)

    assert not detected, (
        f"{_LOW_ENTROPY!r} carries three distinct characters -- 1.585 bits, far "
        "under the 4.0 floor -- and is reported as CSPRNG output, so the detector "
        "has stopped measuring entropy and is judging character classes alone"
    )


def test_the_entropy_floor_is_where_the_detector_says_it_is() -> None:
    """Where the floor sits is a tuning decision, so moving it should take one.

    The test above proves a floor exists. It cannot say where, and neither could
    anything else here -- row five of the table at the top of this section, where
    ``4.0`` was a number a refactor could have carried off. These two candidates
    differ by one distinct character, exactly 4.0 bits against 3.9069, which pins
    the constant to 0.093 bits and pins the comparison as inclusive.

    Deliberately tight. A heuristic threshold is exactly the kind of value that gets
    nudged to silence a false positive, and nudging it silences true positives too;
    that belongs in a diff someone reads, not in a green suite.
    """
    at_the_floor = _looks_like_a_secret(_AT_THE_FLOOR)
    under_the_floor = _looks_like_a_secret(_UNDER_THE_FLOOR)

    assert at_the_floor, (
        f"{_AT_THE_FLOOR!r} carries exactly 4.0 bits, which the detector documents "
        "as detectable (`entropy >= 4.0`); it is now refused, so either the floor "
        "has been raised or the comparison has become exclusive"
    )
    assert not under_the_floor, (
        f"{_UNDER_THE_FLOOR!r} carries 3.9069 bits, under the documented 4.0 floor, "
        "and is reported as a secret; the floor has been lowered"
    )


@pytest.mark.parametrize("candidate", _TREE_CANDIDATES)
def test_the_secret_detector_ignores_the_identifiers_it_actually_meets(candidate: str) -> None:
    """A false positive costs the same as a false negative, in trust.

    These eight are what the scan really passes to the detector on every run:
    three ADR filenames quoted in documents, two test names quoted by
    ``/theurian:upgrade``'s document, and three more quoted by the plugin
    changelog, which names the tests that hold its measured claims. Any one of
    them reported as a secret makes the whole scan noise, and a noisy scan gets
    switched off.

    They are also the reason the detector's requirements are not interchangeable.
    Two of the eight clear the entropy floor -- 4.0389 and 4.0643 bits -- and are
    refused only because they carry no upper-case letter. The three newest are
    refused twice over -- 3.7119, 3.7777 and 3.9317 bits, and no upper-case
    letter either -- so they exercise neither gate on its own. Across these
    eight, every snake_case test name sits below the floor while two of the three
    kebab-case filenames clear it; that is a measurement of the eight rather than
    a rule about the two shapes.
    """
    detected = _looks_like_a_secret(candidate)

    assert not detected, f"an identifier the scan meets is reported as a secret: {candidate!r}"


def test_this_file_still_knows_what_the_scan_meets() -> None:
    """:data:`_TREE_CANDIDATES` is a measurement, and measurements go stale.

    The population above decides what the test before it proves; if an ADR is
    renamed or a document quotes a new long identifier, the negative cases silently
    stop describing the tree. Compared as a set rather than as a count, because
    "nine" is the part a reader can check and the part that rots first -- it was
    "five" until the plugin changelog quoted three more test names, and "eight"
    until the same file linked ADR-0030.

    This walk is deliberately its own rather than :func:`_secrets_in`'s. A shared
    walker would be a shared blind spot, and the one piece of state the two did
    share -- :data:`_UNSCANNED_SUFFIXES` -- is exactly where one edit blinded both at
    once: adding ``.yaml`` to it hid a token in the real ``compatibility.yaml`` from
    the scan *and* from this test together. So this reads every file in the tree
    with no skip list at all, which is measured to change nothing today: ``LICENSE``
    and the skipped suffixes contribute no candidates, and the set is the same nine
    either way. What the two walks do share is :func:`_readable_text`, which is a
    rule about how one file is decoded rather than about which files exist.
    """
    measured = {
        match.group()
        for path in PLUGIN.rglob("*")
        if path.is_file() and (text := _readable_text(path)) is not None
        for match in _CANDIDATE.finditer(text)
    }

    assert measured == set(_TREE_CANDIDATES), (
        f"the plugin tree's candidate set has moved.\n"
        f"  in the tree, not in _TREE_CANDIDATES: {sorted(measured - set(_TREE_CANDIDATES))}\n"
        f"  in _TREE_CANDIDATES, not in the tree: {sorted(set(_TREE_CANDIDATES) - measured)}\n"
        f"Update the tuple and re-measure the entropy figures quoted beside it."
    )


def test_the_detector_refuses_the_name_of_the_variable_that_carries_a_token() -> None:
    """``THEURIAN_MCP_TOKEN`` is a name, and a name must not read as a secret.

    It is *not* something the scan meets, which an earlier version of this file
    claimed: at 18 characters it never matches :data:`_CANDIDATE`'s ``{32,}``, and
    that alone is what excludes it. Re-measured at ``0af1568``, a commit on #501's
    branch and not on ``main``: seven occurrences in the plugin tree, six of them
    inside ``${...}`` or a shell assignment -- where the word boundaries would break
    it in any case -- and one a bare mention in ``mcp/theurian.mcp.json``'s own
    description, which is the occurrence the older wording did not account for.

    It is worth holding all the same, because the detector is what would judge it if
    the candidate floor ever moved. SEC-5 and ADR-0011 require the configuration to
    carry this name in place of the value, so a detector that called the name a
    secret would report the very control that keeps the literal out of the file. The
    name is read from Core rather than quoted, so renaming the variable cannot leave
    this test asserting something about a string nobody uses.
    """
    from theurian.security.tokens import TOKEN_ENV_VAR

    detected = _looks_like_a_secret(TOKEN_ENV_VAR)

    assert not detected, (
        f"{TOKEN_ENV_VAR!r} -- the variable name SEC-5 puts in the connection "
        f"template instead of the token -- is reported as a secret"
    )


@pytest.mark.parametrize("relative_path", _PLANTING_SITES)
def test_the_scan_reports_a_token_planted_in_any_text_file(
    tmp_path: pathlib.Path, relative_path: str
) -> None:
    """The scan's positive path never runs against the real tree, so run it here.

    A guard no input reaches survives its own deletion, and this one did: measured
    on ``486bb99``, a commit on #244's branch and not on ``main``, replacing the
    scan's body with ``assert True`` and making it skip every file both left the
    suite green, because the eight candidates the tree holds all stop at the
    detector's class gate. This is the only test that makes
    the scan execute the branch it exists for.

    The token is planted in a temporary tree, never in the repository. The sites
    are derived from the suffixes the plugin tree really carries
    (:func:`_suffixes_present_in`), so every format the scan can meet has a case
    here whether or not anyone remembered it -- which is how ``.yaml`` was missed.
    ``.svg`` is kept on top of those as the pin for the skip list that once hid it.

    One consequence, stated because it is a failure someone will meet: adding a
    genuinely binary format to the plugin tree reddens this test, because the site
    list follows the tree while :data:`_UNSCANNED_SUFFIXES` does not. That is the
    decision it should force -- either the format is text and the scan must read it,
    or it is binary and belongs on the skip list beside
    :func:`test_the_scan_deliberately_does_not_open_binary_assets`.
    """
    planted = tmp_path / relative_path
    planted.parent.mkdir(parents=True, exist_ok=True)
    planted.write_text(f"# left over from debugging\nTOKEN={_TOKEN_SHAPED}\n", encoding="utf-8")

    violations = _secrets_in(tmp_path)

    assert violations == [f"{relative_path}: {_TOKEN_SHAPED[:8]}..."], (
        f"a token planted in {relative_path} is not reported: {violations}. The scan "
        f"walks that file in the real plugin tree too, so it would miss a real one."
    )


def test_the_scan_deliberately_does_not_open_binary_assets(tmp_path: pathlib.Path) -> None:
    """The blind spot that remains, recorded as behaviour rather than as a comment.

    ``.png`` and ``.jpg`` are skipped by suffix, before anything is read, so the
    scan answers "no secrets" about a file it never opened. A real binary would be
    skipped by the ``UnicodeDecodeError`` guard anyway, which is why the cost is
    nil today -- but the suffix list is the part a future asset format gets added
    to, and ``.svg`` shows how that goes.

    Deleting a suffix from the list reddens nothing. Adding one reddens the planting
    test above for every suffix the plugin tree carries -- which is true by
    construction now that the sites are derived from the tree, and was false before:
    the hand-written sites made that claim hold for four suffixes and fail for
    ``.yaml``, which the tree does carry.
    """
    planted = tmp_path / "logo.png"
    planted.write_text(f"TOKEN={_TOKEN_SHAPED}\n", encoding="utf-8")

    violations = _secrets_in(tmp_path)

    assert violations == [], (
        f"the scan now opens {sorted(_UNSCANNED_SUFFIXES)} files. That is an "
        f"improvement, not a failure -- delete this test and say so in T-8."
    )


def test_the_deterministic_fixture_keeps_the_shape_of_a_real_token() -> None:
    """A stand-in stands in only while it is shaped like the thing it replaces.

    Every claim the tests above make is a claim about what the detector does to a
    real Theurian token, which ``security.tokens`` mints as
    ``secrets.token_urlsafe(TOKEN_BYTES)``: 32 CSPRNG bytes, base64url, padding
    stripped. :func:`_deterministic_token` reproduces that shape because a SHA-256
    digest is also 32 bytes.

    Both halves are read from Core rather than written down here, because a written
    constant cannot notice the thing it copies moving. ``TOKEN_BYTES`` is compared
    through ``base64`` rather than through the number 43, so raising it to 64 fails
    here -- where the message says to re-derive the fixture -- instead of leaving a
    fixture that is the shape of a token Theurian no longer issues. ``is_well_formed``
    is Core's own shape check, so the alphabet is whatever it accepts rather than
    whatever this file believes base64url to be.

    The composition is pinned here too, because it is quoted as fact beside
    :data:`_TOKEN_SHAPED` and in the detection test's failure message. A one-word
    edit to :data:`_FIXTURE_SEED` yields a different token that still passes every
    other test in this section 99.9% of the time, while making both statements
    false; the two assertions below are what notice.

    Measured: swapping the derivation to ``hexdigest()`` reddens this test and the
    detection test together. Hex output is lower case and digits only, so the class
    gate refuses it before entropy is computed -- 64 characters over 16 symbols
    would also fall under the floor at 3.8369 bits, but that branch is never
    reached. The self-test would fail while the detector was entirely correct, which
    is #201's failure arriving from the other direction.
    """
    from theurian.security.tokens import TOKEN_BYTES, is_well_formed

    real_length = len(base64.urlsafe_b64encode(bytes(TOKEN_BYTES)).rstrip(b"="))

    assert len(_TOKEN_SHAPED) == real_length, (
        f"the fixture is {len(_TOKEN_SHAPED)} characters, but a token from "
        f"`secrets.token_urlsafe(TOKEN_BYTES)` is {real_length}; re-derive it so the "
        f"tests above keep saying something about the shape Theurian actually issues"
    )
    assert is_well_formed(_TOKEN_SHAPED), (
        "Core's own token shape check rejects the fixture, so the detection test "
        f"above is no longer about anything Theurian would issue: {_TOKEN_SHAPED!r}"
    )
    assert len(set(_TOKEN_SHAPED)) == 32, (
        f"the fixture holds {len(set(_TOKEN_SHAPED))} distinct characters, not the 32 "
        f"recorded beside _TOKEN_SHAPED; re-measure that comment"
    )
    assert round(_shannon_entropy(_TOKEN_SHAPED), 4) == 4.8307, (
        f"the fixture carries {_shannon_entropy(_TOKEN_SHAPED):.4f} bits, not the "
        f"4.8307 recorded beside _TOKEN_SHAPED and quoted by the detection test's "
        f"failure message; re-measure both. Pinned to the exact recorded value rather "
        f"than to a floor, because a floor of 4.8 leaves a seed yielding 4.81 green "
        f"while the recorded figure goes false -- which is the class of drift this "
        f"file exists to catch. Rounded to four places so that a libm differing in "
        f"the last ulp of log2 does not decide it."
    )
