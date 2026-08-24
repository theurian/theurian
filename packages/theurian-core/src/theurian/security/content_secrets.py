"""Scanning candidate knowledge for secrets (SEC-11, T-15, ADR-0027 decision 3).

The control T-15 names runs at the approval gate: ``theurian propose accept``
scans a proposal's body files before it moves anything. This module is the
detector; ``application/proposal_service.py`` is what applies it and
``security/project_config.py`` is what selects the policy.

**Best effort, and the product says so.** ``SECURITY.md`` publishes the stance --
*"Run a repository secret scanner -- Theurian is not one and is not a
replacement for one"* -- and shipping this does not change it. What it changes is
that the approval gate now has *an* automated control rather than none. Taking a
scanning dependency to raise the detection rate was rejected in ADR-0027:
ADR-0014 pins every dependency exactly and each one is a supply-chain surface,
which is a poor trade for a control the product deliberately disclaims
completeness on.

**The technique is the one this repository already tuned**, for SEC-5, against
its own plugin tree: pattern families for known token shapes, plus a Shannon
entropy floor over candidate tokens that also have to carry mixed case and a
digit. ``tests/unit/test_secret_detector.py`` is where that tuning and its
measurements live -- the class gate is what separates a real token from a
kebab-case ADR filename, and each of its three requirements was measured to be
load-bearing on its own.

This is a **new detector for a different population**, not that one moved. It
reads proposal bodies, which are prose, JSON and YAML a contributor wrote, where
the plugin walker reads a fixed tree of shell and Markdown. Two differences
follow from that and are deliberate:

* The candidate's boundaries are lookarounds over the candidate's own character
  class rather than ``\\b``. ``\\b`` is asymmetric about the ``-`` that base64url
  ends with, and -- the reason that matters here -- a greedy class followed by
  ``\\b`` backtracks one character at a time over a run this input controls.
  Every pattern below terminates in a negative lookahead over its own class, so
  the maximal match satisfies it immediately and, at the *end of a run*, no
  alternative is tried.
* The scan is linear in the body's length. A body reaches this function through
  ``read_source_file``, so it is at most ``MAX_SOURCE_FILE_BYTES``; an unbounded
  *backtracking* quantifier over 8 MiB of attacker-chosen text is the cost this
  avoids. The generic family's repetition is genuinely open-ended (``{32,}``) and
  safe anyway: its negative lookahead succeeds at the run's end, so the greedy
  match never backtracks into it. The specific families cap their repetition at
  :data:`_MAX_TOKEN_CHARS`, which bounds the backtracking a run *longer* than that
  cap costs -- at most that many steps to reject at each start, never the
  input-length backtracking a trailing ``\\b`` would incur.

**A finding never carries the secret.** It names the family, where the match
starts, and at most :data:`REDACTED_PREFIX_CHARS` leading characters. A refusal
is printed to a terminal and, under ``warn``, published into an ``accept
--json`` document that something will log: a finding that echoed the match would
make the report a second copy of what it is reporting.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass
from typing import Final

from theurian.domain.identifiers import ULID_CHARACTERS

#: How many leading characters of a match a finding may quote. Four is enough to
#: pick one candidate out of several on a line and, for a high-entropy token,
#: 24 bits -- a locator, not a recovery. It is an upper bound with teeth:
#: :class:`SecretFinding` refuses to be constructed with a longer one, so raising
#: it is a decision somebody makes here rather than a string that quietly grows.
REDACTED_PREFIX_CHARS: Final = 4

#: What follows the quoted prefix, so a reader can see the value was cut.
_ELISION: Final = "..."

#: How many findings one scan reports before it stops looking. The refusal is
#: actionable on the first one, and the list is otherwise sized by the input:
#: 8 MiB of base64 yields a quarter of a million candidates, each of which would
#: become an entry in an error message and in a published JSON document. The same
#: reckoning as ``_MAX_NAMES_LISTED`` in ``proposal_service.py``, which bounds a
#: refusal that lists a contributor's filenames.
MAX_FINDINGS: Final = 20

#: Bits per character below which a candidate is not CSPRNG output. Measured and
#: pinned in ``tests/unit/test_secret_detector.py``: 4.0 sits between a
#: 16-symbol uniform string (exactly 4.0) and a 15-symbol one (3.9069), and the
#: comparison is inclusive.
_ENTROPY_FLOOR: Final = 4.0

#: The generic family, named here because :func:`scan_text` treats it specially:
#: it is the only one whose regex match still has to clear a heuristic.
HIGH_ENTROPY: Final = "high-entropy-token"

#: Ceiling on a token-shaped repetition. No credential format below runs past a
#: couple of hundred characters, and an unbounded ``{n,}`` inside a pattern with
#: a literal suffix is a quadratic cost on input this module does not choose.
_MAX_TOKEN_CHARS: Final = 255

#: The characters a base64url credential is spelled in, as a regex class. Used
#: for both the candidate itself and for the lookarounds that bound it.
_CANDIDATE_CLASS: Final = r"[A-Za-z0-9_-]"

#: How long a run of those characters has to be before it is worth judging. The
#: floor and the regex's ``{32,}`` are one definition, because the remainder test
#: in :func:`_looks_like_a_secret` applies the same number to a *shortened*
#: candidate and the two must not drift.
_MIN_CANDIDATE_CHARS: Final = 32

#: Known token shapes, as ``(family, pattern)`` in the order the scanner tries
#: them. A prefix is worth more than entropy where one exists -- ``ghp_`` followed
#: by 36 characters is a GitHub token whatever its character frequencies say --
#: so every specific family is tried before :data:`HIGH_ENTROPY`, which is last.
#:
#: **JWTs are deliberately not a family of their own.** A real one's payload
#: segment is mixed-case base64url well past the candidate floor, so the generic
#: family already reports it, while a ``eyJ...\\.eyJ...\\.`` pattern needs two
#: bounded repetitions each followed by a literal -- the one shape here that
#: could not be made single-pass -- in exchange for reporting the same string
#: under a better name.
#:
#: Each pattern is anchored at the front on ``\\b`` or a literal, and terminated
#: by a negative lookahead over its own trailing class rather than by ``\\b``, so
#: the maximal match satisfies the lookahead at a run's end and the engine
#: backtracks only *inside* a bounded family's own ``{n,m}`` window: a run longer
#: than :data:`_MAX_TOKEN_CHARS` costs at most that many steps to reject at each
#: start position, never the input-length backtracking a trailing ``\\b`` would.
#: The generic ``{32,}`` family has no upper bound and needs none -- its lookahead
#: succeeds at the run's end without backtracking. There are no nested
#: quantifiers: ``-----BEGIN [A-Z ]{0,20}PRIVATE KEY-----`` is a bounded class
#: and not ``(?:[A-Z]+ )*``, which is the same header written as a ReDoS.
_PATTERN_FAMILIES: Final[tuple[tuple[str, str], ...]] = (
    ("aws-access-key-id", r"\b(?:AKIA|ASIA|ABIA|ACCA)[0-9A-Z]{16}(?![0-9A-Za-z])"),
    ("github-token", rf"\bgh[pousr]_[A-Za-z0-9]{{36,{_MAX_TOKEN_CHARS}}}(?![A-Za-z0-9])"),
    ("google-api-key", r"\bAIza[0-9A-Za-z_-]{35}(?![0-9A-Za-z_-])"),
    ("openai-api-key", rf"\bsk-{_CANDIDATE_CLASS}{{20,{_MAX_TOKEN_CHARS}}}(?!{_CANDIDATE_CLASS})"),
    ("private-key-block", r"-----BEGIN [A-Z ]{0,20}PRIVATE KEY-----"),
    ("slack-token", rf"\bxox[abeoprs]-[A-Za-z0-9-]{{10,{_MAX_TOKEN_CHARS}}}(?![A-Za-z0-9-])"),
    (
        "stripe-secret-key",
        rf"\b(?:sk|rk)_(?:live|test)_[A-Za-z0-9]{{16,{_MAX_TOKEN_CHARS}}}(?![A-Za-z0-9])",
    ),
    (
        HIGH_ENTROPY,
        rf"(?<!{_CANDIDATE_CLASS}){_CANDIDATE_CLASS}{{{_MIN_CANDIDATE_CHARS},}}"
        rf"(?!{_CANDIDATE_CLASS})",
    ),
)

#: Every family a finding may name. Published so a caller can render a legend,
#: and asserted at construction so a typo'd family is a failure here rather than
#: a string nobody recognises three layers up.
FAMILIES: Final = frozenset(family for family, _ in _PATTERN_FAMILIES)

#: One alternation over every family, tried left to right at each position.
#:
#: A single pass rather than one pass per family, and that is not only speed: the
#: regex engine takes the leftmost match and, among alternatives starting there,
#: the first that succeeds. So ``ghp_...`` is reported as a GitHub token and not
#: also as a high-entropy one, with no overlap bookkeeping to get wrong, purely
#: because the specific families are declared first. Group names replace ``-``
#: with ``_`` because a group name must be an identifier; :data:`_FAMILY_OF` maps
#: back.
_SCANNER: Final = re.compile(
    "|".join(f"(?P<{family.replace('-', '_')}>{pattern})" for family, pattern in _PATTERN_FAMILIES)
)

_FAMILY_OF: Final = {family.replace("-", "_"): family for family, _ in _PATTERN_FAMILIES}

#: A ULID anywhere inside a candidate. Unanchored, and read from the domain's own
#: definition rather than respelled, so this and ``ProposalId``/``MigrationId``
#: cannot disagree about what an identifier looks like. :func:`_looks_like_a_secret`
#: says why it is subtracted before anything is judged.
_THEURIAN_ULID: Final = re.compile(ULID_CHARACTERS)


@dataclass(frozen=True, slots=True)
class SecretFinding:
    """One secret-shaped string, described without reproducing it.

    ``line`` and ``column`` are 1-based and count characters, which is what an
    editor reports. Both are needed: a JSON or YAML body is routinely one long
    line, and "line 1" locates nothing in it.

    **Lines are delimited by ``\\n`` only** (:func:`scan_text` counts and rfinds
    it, nothing else). A body using a bare ``\\r`` (classic-Mac endings) or a
    Unicode line separator (U+2028, U+0085) has those counted as ordinary
    characters, so a finding in one is positioned within the single
    ``\\n``-delimited line rather than at an editor's line number. This is a
    stated bound, not a handled case: the bodies this scans are UTF-8 prose, JSON
    and YAML written for review, where ``\\n`` is the separator, and a locator a
    few characters off is a diagnostic-precision residual, not a leak or a miss.
    """

    #: Which shape matched, from :data:`FAMILIES`.
    family: str
    #: 1-based line of the match's first character.
    line: int
    #: 1-based column of the match's first character.
    column: int
    #: At most :data:`REDACTED_PREFIX_CHARS` leading characters, then
    #: :data:`_ELISION`. Never the whole match.
    redacted: str

    def __post_init__(self) -> None:
        if self.family not in FAMILIES:
            raise ValueError(f"{self.family!r} is not a family this scanner declares")
        if self.line < 1 or self.column < 1:
            raise ValueError(f"a finding sits at line {self.line}, column {self.column}")
        # The bound this class exists to hold. A finding is rendered into a
        # terminal and into an `accept --json` document, so the one thing that
        # must not drift is how much of the match rides along; an edit that
        # raised the prefix would otherwise turn every report into a partial
        # copy of the credential it reports.
        if len(self.redacted) > REDACTED_PREFIX_CHARS + len(_ELISION):
            raise ValueError(
                f"a finding quotes {len(self.redacted)} characters of the match, over the "
                f"{REDACTED_PREFIX_CHARS}-character limit this module publishes"
            )

    def describe(self, *, at: str) -> str:
        """One line naming this finding, for a message or a rendered list.

        ``at`` is where the body is, supplied by the caller because this module
        scans text and does not know what file it came from.
        """
        return f"{at}:{self.line}:{self.column}: {self.family} ({self.redacted})"


def scan_text(text: str, *, max_findings: int = MAX_FINDINGS) -> tuple[SecretFinding, ...]:
    """Every secret-shaped string in ``text``, in the order it appears.

    Args:
        text: The document to scan. Callers reach this with a body already
            bounded by ``read_source_file``'s ``MAX_SOURCE_FILE_BYTES``.
        max_findings: Stop after this many. Truncation is silent by design --
            a refusal is actionable on the first finding, and a caller that
            reported "and N more" would be publishing a count of the input's
            choosing.

    Returns:
        Findings in document order, which is a total order because the scan is
        a single non-overlapping left-to-right pass. Empty when nothing matched,
        which is the answer for the overwhelming majority of real bodies.
    """
    findings: list[SecretFinding] = []
    for match in _SCANNER.finditer(text):
        family = _matched_family(match)
        candidate = match.group()
        if family == HIGH_ENTROPY and not _looks_like_a_secret(candidate):
            continue
        findings.append(
            SecretFinding(
                family=family,
                line=text.count("\n", 0, match.start()) + 1,
                column=match.start() - (text.rfind("\n", 0, match.start()) + 1) + 1,
                redacted=f"{candidate[:REDACTED_PREFIX_CHARS]}{_ELISION}",
            )
        )
        if len(findings) >= max_findings:
            break
    return tuple(findings)


def _matched_family(match: re.Match[str]) -> str:
    """Which family ``match`` came from.

    ``lastgroup`` is the name of the group that participated, and exactly one
    can, because the alternation's branches are mutually exclusive at the top
    level and no branch contains a capturing group of its own. ``None`` is
    therefore unreachable, and refusing it is cheaper than a caller discovering
    that a branch grew an inner ``(...)`` and started reporting the wrong name.
    """
    name = match.lastgroup
    if name is None:  # pragma: no cover - every branch is a named group
        raise ValueError("the secret scanner matched no named family")
    return _FAMILY_OF[name]


def _looks_like_a_secret(token: str) -> bool:
    """Whether ``token`` resembles CSPRNG output rather than something a human typed.

    Length alone is not a signal -- a kebab-case ADR filename is long too, and
    so is a git object id. What separates a credential is that it carries an
    upper-case letter, a lower-case letter *and* a digit, and that its character
    frequencies are near uniform. Each of the three class requirements was
    measured to be load-bearing on its own; dropping any one turns ordinary
    identifiers into reported secrets, and a scan that cries wolf is a scan
    people switch off.

    **Theurian's own identifiers are subtracted first, and that is not a
    refinement -- without it this detector is unusable in this product.** A
    migration is named ``<ulid>-<slug>.yaml`` and a generated body
    ``<slug>-<ulid>.md``, so both are one candidate: 26 near-uniform Crockford
    base32 characters carrying the upper case and the digits, joined to a
    lowercase kebab slug. Measured 2026-08-24 against this repository's own
    corpus, **all 26 committed migration filenames were reported as secrets**, at
    4.59 to 4.95 bits, and so was every generated body path -- while the 26
    committed knowledge bodies themselves were clean. A knowledge document that
    quotes a migration filename is an ordinary thing to write; blocking its
    acceptance by default is how a control gets switched off.

    The subtraction is best effort, and its cost is a residual an adversary can
    pay rather than nothing. A ULID is upper case and digits only, so an
    *accidental* collision -- a credential that happens to contain 26 consecutive
    characters drawn from that 32-symbol subset -- is about 1.5e-8 per position,
    and a secret that is *entirely* ULID-shaped carries no lower-case letter and
    is refused by the class gate above whether it is subtracted or not. What the
    subtraction cannot catch is a token *crafted* to embed a 26-character
    ULID-shaped run: removing that run can drop the remainder below the candidate
    floor, so a value the detector would flag whole slips through (measured: a
    26-character ULID followed by ``abcDEF12`` leaves ``abcDEF12``, eight
    characters, and is not reported). That is the residual SEC-11 accepts -- the
    product disclaims being a complete secret scanner -- bounded to a value shaped
    on purpose to carry a credential past it.

    What remains after the subtraction is judged as a candidate in its own right,
    the length floor included: ``retry-policy-<ulid>`` leaves ``retry-policy-``,
    which is not a candidate at all.
    """
    remainder = _THEURIAN_ULID.sub("", token)
    if len(remainder) < _MIN_CANDIDATE_CHARS:
        return False
    if not (
        any(char.isupper() for char in remainder)
        and any(char.islower() for char in remainder)
        and any(char.isdigit() for char in remainder)
    ):
        return False
    return _shannon_entropy(remainder) >= _ENTROPY_FLOOR


def _shannon_entropy(token: str) -> float:
    """Bits per character, from ``token``'s own character frequencies."""
    counts = Counter(token)
    return -sum((n / len(token)) * math.log2(n / len(token)) for n in counts.values())
