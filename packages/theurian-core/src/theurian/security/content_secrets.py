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
  Every pattern below that ends in an *open-ended* repetition terminates in a
  negative lookahead over exactly that repetition's class, so the maximal match
  satisfies it immediately and, at the *end of a run*, no alternative is tried.
  Three sit outside that shape, and none of them can cost more than a bounded
  number of steps. ``aws-access-key-id`` and ``google-api-key`` repeat a *fixed*
  count -- ``{16}`` and ``{35}`` -- which leaves no window to retreat through, so
  it does not matter that the first one's lookahead is a superset of its
  repetition's class where the second one's equals it. ``private-key-block`` has
  no trailing lookahead at all, ending in the literal ``-----``; its ``[A-Z ]{0,20}``
  genuinely does backtrack, by design and by at most twenty steps, which is how
  ``-----BEGIN RSA PRIVATE KEY-----`` matches at all.
* The scan is linear in the body's length, and stays linear now that a refused
  candidate is scanned a second time. A body reaches this function through
  ``read_source_file``, so it is at most ``MAX_SOURCE_FILE_BYTES``; an unbounded
  *backtracking* quantifier over 8 MiB of attacker-chosen text is the cost this
  avoids. The generic family's repetition is genuinely open-ended (``{32,}``) and
  safe anyway: its negative lookahead succeeds at the run's end, so the greedy
  match never backtracks into it. The specific families cap their repetition at
  :data:`_MAX_TOKEN_CHARS`, which bounds the backtracking a run *longer* than that
  cap costs -- at most that many steps to reject at each start, never the
  input-length backtracking a trailing ``\\b`` would incur.

  **The second pass moved that per-start bound from once per run to once per
  position inside a refused run, and its constant is large.** Measured 2026-08-25
  on CPython 3.13 (Apple silicon), at the 8 MiB ceiling, single pass then single
  pass plus rescan:

  =========================================== ========== ==========
  8 MiB input                                 before     after
  =========================================== ========== ==========
  one run of ``a`` (nothing to find inside)      0.280 s    0.811 s
  254,200 runs of 32 ``a``                       0.489 s    0.887 s
  one run of ``xoxb-`` repeated                  0.282 s    5.721 s
  one run of ``sk-`` repeated (worst measured)   0.287 s    8.678 s
  =========================================== ========== ==========

  The last row is the shape an adversary would pick: ``\\bsk-`` is satisfied at
  every third character, and each of those starts pays the bounded
  :data:`_MAX_TOKEN_CHARS` rejection the paragraph above prices. It is a constant
  factor, not a new complexity class -- the same input at 1, 2, 4 and 8 MiB cost
  1.088, 1.082, 1.090 and 1.088 seconds per MiB, a spread of 0.7% across three
  doublings. **Nothing shaped like a real body pays it**: this repository's
  largest committed knowledge body, 132,811 characters, measured 0.0185 s before
  and 0.0186 s after, because the cost lands only on runs the heuristic refuses
  and only where a family's anchor is repeatedly satisfied inside one. The
  ceiling is 8 MiB and ``propose accept`` is a local, interactive command, so a
  bounded ~9 s on a body crafted to provoke it is a recorded cost rather than a
  denial of service; a scan that silently misses a credential behind an ordinary
  ``staging-`` prefix is the worse trade.

  **Every figure above is per body, and the accept path multiplies it.**
  ``proposal_service``'s ``_scan_for_secrets`` calls this once per body file plus
  once over the migration document's own fields, and neither the schema's
  ``operations`` array nor the service bounds how many bodies one proposal
  carries -- so the accept-path total is that per-body cost times the number of
  bodies. Measured 2026-08-25, eight scans of a 2 MiB body in the worst-case
  ``sk-`` shape above: 17.36 s. Same reckoning as that row -- a local,
  interactive command, recorded rather than bounded, and the bound that does
  exist is ``MAX_SOURCE_FILE_BYTES`` on each body rather than on their number.

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
#:
#: **ASCII, where ``\\b`` is Unicode-aware.** The two disagree about what precedes
#: a run in Japanese prose, which is why :func:`_families_inside` bounds a search
#: over the document instead of matching a slice of it.
_CANDIDATE_CLASS: Final = r"[A-Za-z0-9_-]"

#: What :func:`_carries_a_digit` counts. Spelled out rather than left to
#: ``str.isdigit``, which is ``True`` for Devanagari and fullwidth digits: nothing
#: in :data:`_CANDIDATE_CLASS` reaches it today, and a gate that changes meaning
#: when that class does is a gate nobody re-reads.
_ASCII_DIGITS: Final = frozenset("0123456789")

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

#: The specific families alone, in declaration order and with their patterns
#: untouched. Derived from :data:`_PATTERN_FAMILIES` by subtraction rather than
#: listed a second time, because a family reachable from one pass and not the
#: other is a detector that reports a credential in one position and not in
#: another, and nothing about a second literal list would say so.
_SPECIFIC_FAMILIES: Final[tuple[tuple[str, str], ...]] = tuple(
    (family, pattern) for family, pattern in _PATTERN_FAMILIES if family != HIGH_ENTROPY
)


def _alternation(families: tuple[tuple[str, str], ...]) -> re.Pattern[str]:
    """One named-group alternation over ``families``, tried left to right.

    Group names replace ``-`` with ``_`` because a group name must be an
    identifier; :data:`_FAMILY_OF` maps back. Both scanners are built here rather
    than spelled out twice so that transliteration and its inverse cannot drift:
    a family whose group name one of them spelled differently would raise a
    :class:`KeyError` from :func:`_matched_family` on whichever input reached it
    first.
    """
    return re.compile(
        "|".join(f"(?P<{family.replace('-', '_')}>{pattern})" for family, pattern in families)
    )


#: One alternation over every family, tried left to right at each position.
#:
#: One pass rather than one pass per family, and that is not only speed: the
#: regex engine takes the leftmost match and, among alternatives starting there,
#: the first that succeeds. So ``ghp_...`` is reported as a GitHub token and not
#: also as a high-entropy one, with no overlap bookkeeping to get wrong, purely
#: because the specific families are declared first.
#:
#: **Leftmost-first is also what hid a credential behind a prefix (#350)**, and
#: :data:`_SPECIFIC_SCANNER` is the answer to it: at a candidate run's first
#: character the generic branch matches the *whole* run, ``finditer`` resumes
#: after what that branch consumed, and so when the heuristic then refuses the
#: run, no position inside it was ever tried. ``staging-sk-<hex40>`` went
#: unreported for exactly that reason.
_SCANNER: Final = _alternation(_PATTERN_FAMILIES)

#: The same alternation with the generic family removed: what :func:`scan_text`
#: runs *inside* a run whose :func:`_looks_like_a_secret` verdict was ``False``.
#:
#: Every pattern keeps its leading ``\\b`` or literal exactly as written, and that
#: is the whole reason this is a compiled alternation rather than a substring
#: search for each family's prefix. ``sk-`` occurs inside ``risk-``, ``task-``,
#: ``desk-`` and ``disk-``; a search that reached it would report every kebab-case
#: word ending in those two letters, and with ``block`` as the default policy a
#: false positive costs what a false negative costs.
_SPECIFIC_SCANNER: Final = _alternation(_SPECIFIC_FAMILIES)

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
        Findings in document order, which is a total order: the outer pass is
        non-overlapping and left to right, and the findings recovered from inside
        a refused candidate all sit within the run being visited, in their own
        left-to-right order. Empty when nothing matched, which is the answer for
        the overwhelming majority of real bodies.

    **A refused candidate is scanned again before it is dropped (#350).** A
    credential joined to a lower-case run by a delimiter -- ``staging-sk-<hex40>``,
    or a migration filename with an author-supplied slug in the middle of it --
    is one candidate run to the generic family, which consumes it whole and is
    then refused by :func:`_looks_like_a_secret` for want of an upper-case
    character. Dropping it there discards the ``sk-`` inside, because the
    non-overlapping pass never retried a position within what it consumed. So the
    specific families get their own look at the run, at every position rather
    than only its first.

    Only *refused* candidates are re-examined. A run that clears the heuristic is
    already reported as :data:`HIGH_ENTROPY`, and reporting it a second time under
    an inner family would make one value two findings, at two positions, in a
    refusal message and in a published ``accept --json`` document.

    **The two passes do not apply the same test.** A match recovered from inside a
    refused run must also carry an ASCII digit, where a match the outer pass finds
    need not; :func:`_carries_a_digit` records what that buys and what it costs.
    Said here because this is the entry point a caller reads, and a caller
    comparing two findings has no other way to learn that one of them cleared a
    gate the other never faced.
    """
    findings: list[SecretFinding] = []
    for match in _SCANNER.finditer(text):
        family = _matched_family(match)
        if family == HIGH_ENTROPY and not _looks_like_a_secret(match.group()):
            findings.extend(_families_inside(text, match, room=max_findings - len(findings)))
        else:
            findings.append(_finding_at(text, match.start(), family, match.group()))
        if len(findings) >= max_findings:
            break
    return tuple(findings)


def _families_inside(text: str, run: re.Match[str], *, room: int) -> list[SecretFinding]:
    """Specific families inside ``run``, a candidate the entropy heuristic refused.

    ``room`` is how many findings the caller may still take, so the ceiling bounds
    the *total* rather than the outer pass alone. :func:`scan_text`'s own ``break``
    cannot do it: it runs once per outer match, and *one* refused run can hold many
    inner matches. Measured 2026-08-25 -- ``staging-`` followed by forty repetitions
    of ``sk_live_``, sixteen characters and ``-`` is a single 1,008-character
    candidate the class gate refuses, holding forty ``stripe-secret-key`` matches,
    because that family's repetition class excludes ``-`` so each match ends at a
    delimiter that leaves ``\\b`` satisfied for the next. It answers twenty findings
    at the default :data:`MAX_FINDINGS`, each one paying the ``O(position)`` newline
    count the ceiling exists to cap.

    **The prefix is what routes that run here at all, and without it the input
    measures something else.** Leftmost-first gives a run's first position to
    whichever family matches there, and the specific families are declared before
    the generic one -- so a run *beginning* with ``sk_live_`` is reported by the
    stripe branch of the top-level alternation directly, and no candidate is ever
    refused. Measured the same day: the bare repetition does not reach this function
    once. Something upstream has to deny the specific family its anchor at the run's
    start before anything arrives here at all.
    ``test_the_ceiling_bounds_a_single_failed_candidate_that_hides_many_credentials``
    and ``test_a_finding_taken_before_a_crowded_run_leaves_that_run_less_room`` are
    the durable pin: the per-run ceiling, and ``room`` being the *remaining* room
    rather than the whole of it.

    **The rescan bounds a search over the whole document; it does not match a
    slice of it, and the difference is a false-positive class.** ``pos`` and
    ``endpos`` restrict where a match may begin and end while leaving the engine
    able to read ``text[pos - 1]``, so ``\\b`` and any lookbehind still answer
    against the *document*. Slicing throws that character away and fabricates a
    word boundary at position 0 -- and ``\\b`` is Unicode-aware where
    :data:`_CANDIDATE_CLASS` is ASCII, so a run preceded by a non-ASCII word
    character has no boundary in the document and gained one in a slice. Measured
    2026-08-25 on the slicing version: ``監視対象sk-ingest-pipeline-primary-2026q1``
    reported an ``openai-api-key`` that the same text does not contain. That it is
    a class rather than one example is the round-one adversarial review's
    measurement, recorded here as theirs: 1,680 fabricated-boundary matches over
    7,312 non-ASCII-preceded runs, against none over 5,551 ASCII-preceded ones.
    Ordinary Japanese prose, under a ``block`` policy, refused for a credential
    that is not there.

    **What ``endpos`` is for is confinement.** It is not what makes the trailing
    lookaheads succeed: every specific family *that has one* admits a subset of
    :data:`_CANDIDATE_CLASS`, and ``run`` is maximal in that class, so the
    character at ``run.end()`` is outside every one of them and each lookahead
    succeeds on the document's own text -- measured 2026-08-25, the rescan returns
    identical matches with ``endpos`` and without it for a run followed by a
    space, by a period, and at end of text.

    It does two things instead, and the second is easy to miss because the
    lookahead argument does not cover it:

    * It stops the rescan running on to the end of the *document*. Without it the
      first refused run's rescan reported the second run's credential as well
      (measured: two findings from one run, the second at another run's offset) --
      a duplicate, and out of the total document order :func:`scan_text` publishes
      and the outer loop's left-to-right progress is what establishes.
    * It is what actually enforces member 2 for ``private-key-block``, the one
      family whose *body* leaves :data:`_CANDIDATE_CLASS` rather than merely its
      lookahead. A match can begin inside a run and end past it, so the lookahead
      argument says nothing here. Measured on
      ``staging-deployment-secrets-----BEGIN RSA PRIVATE KEY-----``, whose run the
      gate refuses: bounded, the rescan finds nothing; unbounded, it finds a
      ``private-key-block`` at offset 26 running to 57, well past the run that
      admitted it.

    **What this cannot reach, by root cause.** None is a miss to be tuned away
    later; each is a trade with something on the other side.

    1. *No word boundary before the family's prefix in the document.* ASCII glue
       (``stagingsk-<hex40>``) and non-ASCII glue (``証sk-<hex40>``) are one member,
       not two -- the second is what the fix above correctly stops reporting.
       Reaching either means matching a prefix at an arbitrary offset, which
       reports ``risk-``, ``task-`` and ``disk-`` as credentials.
    2. *A family whose pattern needs characters outside* :data:`_CANDIDATE_CLASS`
       *can never match inside a run.* Today that is ``private-key-block`` alone,
       whose pattern requires spaces. It costs little: a real PEM key's base64
       payload is long, mixed-case and near-uniform, so the generic family reports
       the body even when the header is unreachable.
    3. *A credential inside a run is reached only if the family can end where the
       run lets it, from a distance the family can span.* Two independent
       properties of :data:`_PATTERN_FAMILIES` decide it, and reading either one
       alone gets the membership wrong:

       * **Where a match may end** -- its trailing lookahead. A family whose
         lookahead admits exactly :data:`_CANDIDATE_CLASS` can end only at the
         run's end, because every interior position is followed by a character it
         forbids: measured over all 64 candidate characters, that is
         ``openai-api-key`` and ``google-api-key``. The other four also end at any
         character their lookahead omits -- ``-`` for ``aws-access-key-id``,
         ``github-token`` and ``stripe-secret-key``, ``_`` for ``slack-token``.
       * **How far it can reach** -- its repetition. A *fixed* count can end at
         exactly one offset: ``aws-access-key-id`` matches 20 characters and
         ``google-api-key`` 39, always. An *open* count reaches ``len(prefix) +
         255``: 258 for ``openai-api-key``, 259 for ``github-token``, 260 for
         ``slack-token``, 263 for ``stripe-secret-key``.

       The four quadrants, each measured 2026-08-25:

       * *Fixed and equal* -- ``google-api-key``. One end offset, and the lookahead
         forbids every candidate character there: **all 64** kill it. Any glue at
         all and the family is gone.
       * *Fixed and strict-subset* -- ``aws-access-key-id``. One end offset, but
         two characters survive there: **62 of 64** kill it, and only ``-`` and
         ``_`` do not. The difference from ``google-api-key`` is two characters,
         not a difference of kind, which is why reading the lookahead column alone
         put this family in the wrong bucket.
       * *Open and equal* -- ``openai-api-key``. Must reach the run's end and can
         span 258 from ``sk-``: a run of 258 reports, 259 reports nothing.
       * *Open and strict-subset* -- ``github-token``, ``slack-token``,
         ``stripe-secret-key``. Either an omitted delimiter appears, or the run's
         end must fall within reach. Measured at each boundary: 259/260 for
         ``github-token``, 260/261 for ``slack-token``, 263/264 for
         ``stripe-secret-key``.

       **Losing the family is not always losing the finding**, and the difference
       is the generic gate. This function only runs on runs
       :func:`_looks_like_a_secret` refused, so *within its domain* a lost family
       is silence. Measured through the real CLI at the default ``block``: ``AKIA``
       and sixteen characters is refused as an ``aws-access-key-id``, while the
       same key followed by twenty-four identical lower-case characters is
       **accepted, and the body lands**. The two survivors behave as the geometry
       says they must -- glue that key with ``-`` or ``_`` instead of a letter and
       it is reported again.

       Where the gate passes, the generic family reports the run and the loss costs
       precision rather than the finding: ``AIza<35>-x`` reports
       ``high-entropy-token``, and that proposal is **refused**, not accepted. So
       silence needs a run the credential's own characters do not carry past the
       gate -- a *low-entropy* tail (twenty-four identical characters after that
       AWS key measure 2.67 bits; a short cycle after a Google key, 3.51) or a run
       with no digit at all. **Lower case is not the property, and assuming it was
       is how this paragraph was wrong before:** twenty-four *distinct* lower-case
       characters after the same AWS key measure 5.17 bits, clear the gate and are
       reported, as is an ordinary English kebab slug after a Google key at 5.04.

       All of it is pre-existing behaviour, not something the rescan introduced.
       For the *open* families the reach is :data:`_MAX_TOKEN_CHARS`, which is the
       ReDoS budget this module's own cost reasoning spends, so it is not moved
       here. **For the two *fixed* families that excuse does not apply and should
       not be offered:** a fixed count consumes no backtracking budget at all, so
       nothing is being bought with what they lose. The round-two adversarial
       review measured that narrowing ``google-api-key``'s lookahead to
       ``(?![0-9A-Za-z])`` costs no new false positives over 9.5M characters and no
       measurable time; that would move it into ``aws-access-key-id``'s quadrant,
       from 64 killing characters to 62, rather than out of this member. Filed
       as #356, and deliberately not made in the change that recorded it.
    4. *A match is leftmost-greedy and non-overlapping, at either pass, so a
       credential inside a span another match consumed is not reported.* In the
       rescan, ``backup-xoxb-<digits>-sk-<hex40>`` reports one ``slack-token`` and
       not the ``sk-`` credential inside it. The outer pass has the same face and
       had it before this module grew a rescan: ``sk-<hex40>-ghp_<36>`` reports one
       ``openai-api-key``, and the GitHub token inside the span it consumed is
       never reported, though it reports on its own. Under ``block`` the refusal
       still fires; under ``warn`` the published list under-reports.
    5. *The digit gate*, in both directions -- see :func:`_carries_a_digit`.
    """
    recovered: list[SecretFinding] = []
    for inner in _SPECIFIC_SCANNER.finditer(text, run.start(), run.end()):
        if len(recovered) >= room:
            break
        if not _carries_a_digit(inner.group()):
            continue
        recovered.append(_finding_at(text, inner.start(), _matched_family(inner), inner.group()))
    return recovered


def _carries_a_digit(matched: str) -> bool:
    """Whether ``matched`` holds an ASCII digit, which recovered matches must.

    **The rescan reaches inside runs that are mostly English**, and a kebab-case
    identifier is where its families' prefixes turn up by accident: measured
    2026-08-25, ``website/src/lib/i18n-sk-locale-and-translation-notes.ts``,
    ``backlog/task-sk-review-the-ranking-heuristics.md`` and the product's own
    ``<ulid>-add-sk-localisation-notes-for-the-site.yaml`` were each reported as an
    ``openai-api-key``. The internal ``-`` is a real document boundary, so the
    ``pos``-based search above is right to find them and cannot be what refuses
    them. Under the default ``block`` that refuses a credential-free proposal and
    tells its author to rotate a secret that does not exist.

    A digit separates the two populations here. Measured the same day, six of the
    seven declared families' fixtures carry one -- every family that can match
    inside a candidate run -- and in each example above it is the family's *matched
    substring* that carries none. The surrounding text is not the test and often
    does have digits: ``i18n`` and the leading ULID both do, while the substrings
    ``sk-locale-and-translation-notes`` and ``sk-localisation-notes-for-the-site``
    do not. The one digit-less fixture is ``private-key-block``, which can never
    match inside a candidate run at all (its pattern requires spaces), so the gate
    costs that family nothing.

    Both directions cost something, and both are accepted rather than unnoticed:

    * A real credential whose recovered match happens to carry no ASCII digit is
      dropped, and **the rate is per family rather than one number** -- it falls
      with the token's length and rises with the share of letters in the issuer's
      alphabet. Round two's adversarial review simulated 200,000 draws per family
      against *assumed* issuer alphabets (2026-08-25): about 0.02% for a 48-character
      alphanumeric OpenAI legacy key, about 1.4% for a 24-character Stripe legacy
      key, and about 3.6% for a 16-character AWS id under a base32 ``[A-Z2-7]``
      assumption. The alphabets are assumptions, not published formats, so read
      these as an order of magnitude: percent-level for the short-token families,
      negligible for the long ones. (The 0.065% figure in
      ``test_secret_detector.py`` is a different population -- a 43-character
      ``token_urlsafe`` draw -- and is not what this gate costs.)
    * A digit-*bearing* English-ish slug still reports: ``...-sk-ingest-primary-
      2026q1`` is a false positive this gate does not catch. Bounded by measurement
      rather than by argument -- zero findings across every committed migration
      document, filename, knowledge body and body path (:func:`_looks_like_a_secret`
      records that sweep).

    **Recovered matches only.** The outer pass is deliberately left alone: it
    already reports a short top-level ``sk-`` shape such as
    ``x.sk-locale-and-translation-notes``, and it did so before this module grew a
    rescan. That is a pre-existing class with its own trade, and quietly changing
    it here would hide a behaviour change inside a false-positive fix.
    """
    return any(char in _ASCII_DIGITS for char in matched)


def _finding_at(text: str, start: int, family: str, matched: str) -> SecretFinding:
    """One finding for ``matched``, whose first character is at ``start`` in ``text``.

    Both passes hand it a document offset directly -- the rescan searches ``text``
    under ``pos``/``endpos`` rather than a substring, so there is no run-relative
    arithmetic to get wrong. That is what lets a finding recovered from inside a
    candidate run point at the credential rather than at the run that hid it: a
    reader sent to the run's start finds ``staging-`` and reads the report as
    noise.
    """
    return SecretFinding(
        family=family,
        line=text.count("\n", 0, start) + 1,
        column=start - (text.rfind("\n", 0, start) + 1) + 1,
        redacted=f"{matched[:REDACTED_PREFIX_CHARS]}{_ELISION}",
    )


def _matched_family(match: re.Match[str]) -> str:
    """Which family ``match`` came from, whichever scanner produced it.

    ``lastgroup`` is the name of the group that participated, and exactly one
    can, because the alternation's branches are mutually exclusive at the top
    level and no branch contains a capturing group of its own. ``None`` is
    therefore unreachable, and refusing it is cheaper than a caller discovering
    that a branch grew an inner ``(...)`` and started reporting the wrong name.

    That argument is about how :func:`_alternation` builds a pattern, not about
    which families went into it, so it holds for :data:`_SPECIFIC_SCANNER`
    unchanged: dropping a branch from an alternation of mutually exclusive
    branches leaves them mutually exclusive. :data:`_FAMILY_OF` is built from the
    full set, so it names every group either scanner can report.
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

    **Returning ``False`` here now costs a second look rather than silence**, so
    the corpus that motivated the subtraction is precisely the corpus
    :data:`_SPECIFIC_SCANNER` runs over: every migration filename is a refused
    candidate, and a family matching inside one would refuse an acceptance for a
    name the product minted itself. Re-measured 2026-08-25 with that pass in
    place, over ``git ls-files .theurian/migrations .theurian/knowledge`` read at
    ``HEAD`` rather than from the working tree, which holds machine-local notes CI
    cannot see: zero findings across all 26 migration documents (32,325
    characters), all 26 migration filenames, all 26 knowledge body paths, and all
    26 committed body texts (443,608 characters).

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
