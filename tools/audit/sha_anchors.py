"""Class 4: does every sha-like anchor in governed prose resolve? (#199 unit B, #463).

A commit anchor is the strongest citation this repository writes: it says *this
sentence was true at this tree, go and look*. An anchor that does not resolve on
``main`` says the same thing and cannot be checked, which is worse than no
anchor at all -- and two of them sat unqualified in ADR-0008 from the squash of
the branch that wrote them until #199 unit B named the pull requests they belong
to. Ten more like them are still open under #463.

**The key is deliberately dumb**: ``\\b[0-9a-f]{7,40}\\b`` over governed prose.
That matches English words spelled in hex -- ``defaced``, ``decade``, ``face`` --
and those are **classified, never special-cased**, the precedent #470 set for the
Mermaid hex colours it met in the same position. A word that reads as a sha is
not noise to be filtered out of the population; it is a member whose verdict is
"this is a word".

**Three ways an anchor discharges:**

1. ``git merge-base --is-ancestor <sha> main`` succeeds -- the commit is on the
   main line and a reader can check it;
2. the cite carries the **pull-request qualifier** -- a sha named as belonging to
   a branch or a PR, which the writer already flagged as not on ``main``;
3. it has a row in :data:`CLASSIFIED`, with the verdict a person reached.

Everything else is a dangling anchor and exit status 1.

**Route 2 is itself a promise, so its content is checked too.** Until #199 unit
B's third assignment the qualifier route accepted any text at all: ADR-0008 now
says its two branch commits "landed as ``56582b2``" and "landed as ``d1e79b1``",
and mutating either sha passed every audit here -- the writer's escape hatch had
become an unchecked assertion. A qualifier that hands a reader a landed commit
makes the same promise the anchor itself makes, so :data:`_LANDED_AS` extracts
that sha and holds it to the same reachability test. A named landed sha that
does not resolve to a commit, or that resolves and is not reachable from the
main reference, is a violation and exit status 1.

**What that check reaches, and what it does not -- stated here rather than left
to be discovered:**

* **Pull-request numbers are not verified.** ``([#142](.../pull/142))`` beside a
  landed sha is unchecked, so a qualifier naming the wrong pull request still
  passes. This audit shells out to ``git`` and to nothing else. The one
  network-capable helper in this directory, ``tools/audit/tracker_state.py``,
  answers issue and pull-request *state* and carries no landed-commit field, so
  settling ``#142`` would need a new GitHub query that no audit here makes. The
  check narrows the qualifier's escape space to the number; it does not close
  it.
* **The key is the phrase ``landed as``**, which is how all three shipped
  qualifiers write it (two in ADR-0008, one in the threat model's **T-16**
  release note at ``:2582`` -- the entry about a compromised release artifact,
  not T-14). A qualifier naming its landed commit some other way -- "merged to
  ``main`` as", "squashed to" -- is outside the key and discharges on route 2
  unexamined, exactly as it did before.
* It says nothing about whether the named sha is the *right* commit. It says a
  reader can reach the one that is named, which is the half a checkout can
  settle.

Run it::

    uv run --frozen python tools/audit/sha_anchors.py
    uv run --frozen python tools/audit/sha_anchors.py --positive-control
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from claim_surfaces import governed_paths, print_control_tally, repo_root

#: Where an anchor is governed. Prose that promises a commit, not source that
#: happens to hold a hex literal: a test fixture's digest is a value, not a cite.
GOVERNED_ROOTS: Final[tuple[str, ...]] = ("docs/",)

#: The key. Seven characters is git's own short-sha floor here.
_SHA_LIKE: Final = re.compile(r"\b[0-9a-f]{7,40}\b")

#: A URL, whose contents are link targets rather than prose. Removed from a line
#: **before** the key runs, and this is a structural exclusion rather than a
#: value one: every GitHub comment anchor is a ten-digit run inside an ``https://``
#: target, and a rule that dropped ten-digit runs *everywhere* would drop a real
#: short sha that happens to be all digits. Removing the URL removes the
#: container, which is what the tokens actually have in common.
_URL: Final = re.compile(r"https?://\S+")

#: A token carrying no ``a``--``f`` at all is a decimal quantity, not an object
#: id -- ``2000000`` in a measured table, a byte count, a comment id left bare.
#:
#: **The false-clear risk is stated rather than assumed**: a real short sha whose
#: seven characters are all digits would be classified as a number, which is one
#: tree in 16**7. Recorded here because the alternative -- hand-classifying every
#: measured quantity in the threat model -- makes the ledger unreadable and the
#: audit unrunnable.
_DECIMAL_ONLY: Final = re.compile(r"^[0-9]+$")

#: The qualifier that says a sha is *not* expected on ``main``: the writer named
#: the branch or the pull request it lives on. Matched within a window ahead of
#: or behind the token, because both orders occur ("on PR #470's branch, `abc123`"
#: and "`abc123` (PR #470)").
#:
#: **Phrases, not words, and round one's M-c is why.** The first version of this
#: key admitted a bare ``\bbranch\b``, a bare ``PR`` and a bare ``pull/\d+``
#: anywhere in a 240-character window. Every one of those occurs in ordinary
#: prose beside an anchor that is *not* a branch commit: "the same figure holds
#: on every long-lived branch of the tree" discharged a fabricated sha, and any
#: sentence citing a merged pull request beside a measurement anchor discharged
#: that anchor. The route is a writer's assertion that the reader cannot reach
#: this commit, so the key has to be a phrase making that assertion **about the
#: token**, not a word that happens to share a line with it.
#:
#: Measured at ``ef345c9``: the narrowed key admits **two** anchors, both of them
#: ADR-0008's, both written "a commit on the branch that landed as ...". Every
#: other unreachable anchor in governed prose is dangling and carries a row in
#: :data:`CLASSIFIED`.
_PULL_QUALIFIER: Final = re.compile(
    r"\bcommits?\s+on\s+(?:the|a|an|its|this|that|\w+'s)\s+branch\b"
    r"|\bon\s+(?:the|a|an|its|this|that|\w+'s)\s+branch\b"
    r"|\bbranch\s+commit\b"
    r"|\bbefore\s+the\s+squash\b|\bat\s+the\s+squash\b|\bsquashed\s+away\b"
    r"|\bunmerged\b"
    r"|\blanded\s+as\b"
    r"|\bnot\s+(?:on|reachable\s+from)\s+`?(?:origin/)?main`?\b",
    re.IGNORECASE,
)

#: How far either side of the token the qualifier may sit.
_QUALIFIER_WINDOW: Final = 120

#: The qualifier's *content*: the sha a writer says the branch landed as.
#:
#: **Wrap-aware, because governed prose here is hard-wrapped and this phrase sits
#: inside a ``>`` amendment block in both of ADR-0008's members.** ``[\\s>]+``
#: rather than ``\\s+`` between the words, so ``landed\\n> as `56582b2``` is one
#: claim rather than none -- the same reason ``claim_surfaces`` strips blockquote
#: markers before anything else looks at a line. A ``>`` between the words in
#: some other construction would be read as a wrap, which over-approximates in
#: the direction that costs a read.
#:
#: The delimiter in front of the sha is optional: all three shipped members
#: backtick it, and a bare one is the same claim.
#:
#: **No decimal-only filter here, deliberately**, unlike :data:`_DECIMAL_ONLY` on
#: the anchor key. That filter exists because the anchor key is a bare hex shape
#: that meets byte counts and comment ids; this key is a *phrase*, and a token a
#: sentence introduces as the commit something landed as is a claimed sha
#: whatever characters it happens to use. An all-digit one would be reported as
#: unresolvable, which is the correct verdict for an anchor a reader cannot
#: check.
_LANDED_AS: Final = re.compile(
    r"\blanded[\s>]+as\b[\s>]*[`\"'“]?(?P<sha>[0-9a-f]{7,40})\b",
    re.IGNORECASE,
)

#: How much of the sentence around a landed claim is printed with it.
_LANDED_CONTEXT: Final = 90

_GIT_TIMEOUT_SECONDS: Final = 30

_INHERITED_GIT_OVERRIDES: Final[frozenset[str]] = frozenset(
    {"GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR"}
)


@dataclass(frozen=True, slots=True)
class Anchor:
    path: str
    line: int
    token: str
    context: str


@dataclass(frozen=True, slots=True)
class LandedClaim:
    """One ``landed as <sha>`` promise, with the line it opens on.

    Separate from :class:`Anchor` because it is a different assertion about a
    different token: an anchor says *this sentence was true at this tree*, while
    a landed claim says *the branch this unreachable anchor came from is on the
    main line as this commit*. The second is what makes the first checkable, so
    it cannot be discharged by the route it exists to justify.
    """

    path: str
    line: int
    token: str
    context: str


def _governed(path: str) -> bool:
    return any(path.startswith(prefix) for prefix in GOVERNED_ROOTS)


def _git_environment() -> dict[str, str]:
    """The environment a ``git`` child may see.

    The overrides are dropped for the reason ``tools/corpus_drift.py`` records:
    an inherited ``GIT_DIR`` points the child at another repository, and every
    reachability verdict below would then describe that one.
    """
    return {
        name: value for name, value in os.environ.items() if name not in _INHERITED_GIT_OVERRIDES
    }


def anchors(root: Path) -> list[Anchor]:
    """Every sha-like token in governed prose, one row per occurrence."""
    found: list[Anchor] = []
    for path in governed_paths(root):
        if not _governed(path):
            continue
        text = (root / path).read_text(encoding="utf-8", errors="surrogateescape")
        for number, line in enumerate(text.splitlines(), start=1):
            outside_urls = _URL.sub(" ", line)
            for match in _SHA_LIKE.finditer(outside_urls):
                if _DECIMAL_ONLY.match(match.group(0)):
                    continue
                found.append(
                    Anchor(path=path, line=number, token=match.group(0), context=line.strip())
                )
    return found


def _qualified(anchor: Anchor) -> bool:
    start = anchor.context.find(anchor.token)
    window = anchor.context[
        max(0, start - _QUALIFIER_WINDOW) : start + len(anchor.token) + _QUALIFIER_WINDOW
    ]
    return bool(_PULL_QUALIFIER.search(window))


def main_reference(root: Path) -> str:
    """``origin/main`` when the remote-tracking ref exists, otherwise ``main``.

    The remote ref is preferred because a linked worktree's local ``main`` is
    whatever the main checkout last had -- which is behind the branch point often
    enough that "does not resolve on main" would be an artefact of the developer's
    own checkout rather than a property of the anchor.
    """
    for candidate in ("origin/main", "main"):
        if _resolves(root, candidate):
            return candidate
    message = "neither `origin/main` nor `main` resolves in this checkout"
    raise SystemExit(message)


def _resolves(root: Path, token: str) -> bool:
    """Whether ``token`` names a commit object in this checkout.

    Split out from :func:`_is_ancestor` because the two failure modes read
    differently to whoever has to fix the sentence: a sha that resolves and is
    unreachable is a real commit on a branch, while one that resolves to nothing
    was mistyped or invented. ``merge-base`` collapses both into one ``False``.
    """
    completed = subprocess.run(  # noqa: S603 - fixed argv, no shell, no caller input
        ["git", "rev-parse", "--verify", "--quiet", f"{token}^{{commit}}"],  # noqa: S607
        cwd=root,
        check=False,
        capture_output=True,
        env=_git_environment(),
        timeout=_GIT_TIMEOUT_SECONDS,
    )
    return completed.returncode == 0


def _is_ancestor(root: Path, token: str, reference: str) -> bool:
    completed = subprocess.run(  # noqa: S603 - fixed argv, no shell, no caller input
        ["git", "merge-base", "--is-ancestor", token, reference],  # noqa: S607 - via PATH
        cwd=root,
        check=False,
        capture_output=True,
        env=_git_environment(),
        timeout=_GIT_TIMEOUT_SECONDS,
    )
    return completed.returncode == 0


def landed_claims_in(text: str, path: str = "") -> list[LandedClaim]:
    """Every ``landed as <sha>`` promise in one document's text.

    Split from :func:`landed_claims` so the key can be run against a planted
    sentence without a file to put it in -- which is what
    :data:`LANDED_CLAIM_CONTROLS` needs, and what makes the extraction half of
    this check a measurement rather than an assumption.
    """
    return [
        LandedClaim(
            path=path,
            line=text.count("\n", 0, match.start()) + 1,
            token=match.group("sha"),
            context=" ".join(
                text[
                    max(0, match.start() - _LANDED_CONTEXT) : match.end() + _LANDED_CONTEXT
                ].split()
            ),
        )
        for match in _LANDED_AS.finditer(text)
    ]


def landed_claims(root: Path) -> list[LandedClaim]:
    """Every ``landed as <sha>`` promise in governed prose, one row per occurrence."""
    found: list[LandedClaim] = []
    for path in governed_paths(root):
        if not _governed(path):
            continue
        text = (root / path).read_text(encoding="utf-8", errors="surrogateescape")
        found.extend(landed_claims_in(text, path))
    return found


def _landed_verdict(root: Path, token: str, reference: str) -> str:
    """Empty when the promise holds, otherwise why it does not."""
    if not _resolves(root, token):
        return "names no commit in this checkout"
    if not _is_ancestor(root, token, reference):
        return f"resolves, but is not reachable from `{reference}`"
    return ""


#: The anchors a person has judged, as ``(token, path, occurrences, verdict, why)``.
#:
#: **Keyed per occurrence, and round one's H-C is why.** Keyed on the token alone,
#: a *new* dangling anchor reusing a token this ledger already carries was
#: absorbed: appending "Measured at ``1a37c86``, a tree this reader can go and
#: look at" to another document left the audit at exit 0, because the token was
#: recorded. The path and the count are the missing dimensions -- a new anchor in
#: a new file has no row, and a new anchor in a file that already has one moves
#: the count. Both are exit status 1.
#:
#: The count is the exact form the ``(token, path)`` key would otherwise leave
#: open, and it is cheap here because these are anchors rather than sentences:
#: three occurrences of ``61747b3`` in the threat model is a fact about the
#: document that only changes when somebody adds or removes an anchor.
#:
#: **Exact in both directions**, which every ledger in this directory is at
#: minimum: a row the sweep stops producing means the sentence moved and the row
#: has to go with it. The third direction here is the occurrence count; the three
#: siblings carry a *fragment* key instead and need cardinality rather than a
#: count, which is what their ``ambiguous`` direction is -- ``config_object_claims``,
#: ``controls_discharge`` and ``owner_position_cites``, which is three and not two:
#: ``controls_discharge`` reconciles in three directions like this one, its first
#: spelled *undischarged*. The README beside this file tabulates which audit has
#: which.
#: A hex-looking English word stays here rather than being filtered out of the
#: key, so the filter cannot quietly widen.
CLASSIFIED: Final[tuple[tuple[str, str, int, str, str], ...]] = (
    # -- Ten real commits that are not on `main`, in twelve places ------------
    # Every one of these resolves in a checkout that still has the branch and in
    # none that does not: `git cat-file -t` says `commit` here, and
    # `merge-base --is-ancestor <token> origin/main` says no. They are pre-squash
    # branch commits, which is what makes the anchor uncheckable for a reader who
    # cloned the repository -- #463's class. The two ADR-0008 members known when
    # #463 was filed are absent from this ledger because #199 unit B qualified
    # them in the ADR itself, so they discharge on the pull-request route above
    # rather than on a row here. The rows below stay with #463.
    (
        "1a37c86",
        "docs/adr/0007-state-hash-partitioned-databases.md",
        1,
        "DANGLING, #463",
        "A dated count anchored to a branch commit.",
    ),
    (
        "1a37c86",
        "docs/adr/0024-a-purge-is-a-build.md",
        1,
        "DANGLING, #463",
        "The same token, the same count, a second document. Two rows rather than one, "
        "because a token-keyed ledger absorbs the second.",
    ),
    (
        "857d3b0",
        "docs/adr/0029-review-findings-are-governed-knowledge.md",
        2,
        "DANGLING, #463",
        "Arrived at the #504 merge seam (2026-09-03): slice-3's ADR-0029 demonstration "
        "record anchors to a pre-squash branch commit of that PR.",
    ),
    (
        "394c850",
        "docs/roadmap.md",
        1,
        "DANGLING, #463",
        "Arrived at the same #504 merge seam: a roadmap-appendix population key "
        "anchored to the same PR's pre-squash branch.",
    ),
    (
        "857d3b0",
        "docs/security/threat-model.md",
        1,
        "DANGLING, #463",
        "The same #504 pre-squash token's third occurrence: a threat-model "
        "measurement from that PR's round 1.",
    ),
    (
        "67727eb",
        "docs/adr/0027-accept-validates-before-it-moves.md",
        1,
        "DANGLING, #463",
        "A corpus count anchored to a branch commit.",
    ),
    (
        "67727eb",
        "docs/security/threat-model.md",
        1,
        "DANGLING, #463",
        "The threat model's half of the same corpus count.",
    ),
    (
        "a8c1ce3",
        "docs/security/threat-model.md",
        3,
        "DANGLING, #463",
        "#26's branch commit; the squash that landed on main is not it.",
    ),
    (
        "b8d2030",
        "docs/security/threat-model.md",
        2,
        "DANGLING, #463",
        "A measurement anchor in T-6's concurrency reproduction.",
    ),
    (
        "db36089",
        "docs/security/threat-model.md",
        1,
        "DANGLING, #463",
        "Cited as what closed #17.",
    ),
    (
        "2793d7b",
        "docs/security/threat-model.md",
        1,
        "DANGLING, #463",
        "Cited as #19's commit.",
    ),
    (
        "b8fa3e3",
        "docs/security/threat-model.md",
        3,
        "DANGLING, #463",
        "Three remedy-string anchors in one entry.",
    ),
    (
        "61747b3",
        "docs/security/threat-model.md",
        3,
        "DANGLING, #463",
        "T-18's mechanism anchor, three occurrences.",
    ),
    (
        "6087be4",
        "docs/security/threat-model.md",
        1,
        "DANGLING, #463",
        "A dated real-CLI measurement anchor.",
    ),
    (
        "dc6aa79",
        "docs/security/threat-model.md",
        1,
        "DANGLING, #463",
        "Named as the 0.1.0.dev4 commit for the withdrawal purge.",
    ),
    # -- Eight tokens that are not object ids at all --------------------------
    # `git cat-file -t` finds no object for any of them, and reading the sentence
    # says why: each is an illustrative content or state hash inside a quoted
    # error message or a sample table. They are in the population because the key
    # is deliberately dumb, and they are classified rather than filtered -- the
    # #470 precedent for the Mermaid hex colours, applied to the same shape.
    (
        "abc7cdb70713",
        "docs/adr/0013-ai-writes-produce-proposals.md",
        1,
        "not an anchor",
        "A content hash inside ADR-0013's quoted refusal.",
    ),
    (
        "4f9c5503e198",
        "docs/adr/0013-ai-writes-produce-proposals.md",
        1,
        "not an anchor",
        "The pinned half of the same quoted refusal.",
    ),
    (
        "7e1eb70348da",
        "docs/protocol/migrations.md",
        1,
        "not an anchor",
        "The same example, in the migration protocol.",
    ),
    (
        "9a1584226439",
        "docs/protocol/migrations.md",
        1,
        "not an anchor",
        "The pinned half of it.",
    ),
    (
        "ee3ab796ab22f936",
        "docs/security/threat-model.md",
        1,
        "not an anchor",
        "A `stateHash` in a sample `doctor` table.",
    ),
    (
        "8624b114c4bc0017",
        "docs/security/threat-model.md",
        1,
        "not an anchor",
        "The differing half of the same row.",
    ),
    (
        "f1711b98d302",
        "docs/security/threat-model.md",
        1,
        "not an anchor",
        "A state hash inside a quoted database filename.",
    ),
    (
        "2e8880bf25be",
        "docs/security/threat-model.md",
        1,
        "not an anchor",
        "A new state hash in the same worked example.",
    ),
)


#: What the key and the reachability test must do before any count is read, as
#: ``(what it demonstrates, token, must it be reachable)``.
#:
#: The first two are the #463 members named in unit B's Definition of Ready: two
#: anchors in ADR-0008 written against a branch that was squashed away. If the key
#: stops producing them the ledger's zeros mean nothing, and the run says so
#: rather than reporting a clean tree.
#:
#: **They stay controls after being fixed, and that is the point.** #199 unit B
#: qualified both sentences rather than deleting the tokens, so each is still an
#: unreachable sha sitting in governed prose -- exactly the shape the key and the
#: reachability test have to keep separating. What changed is how they discharge,
#: not whether the instrument can see them.
POSITIVE_CONTROLS: Final[tuple[tuple[str, str, bool, bool], ...]] = (
    ("ADR-0008's first unreachable anchor, now qualified (#463)", "4bfec1d", False, True),
    ("ADR-0008's second unreachable anchor, now qualified (#463)", "1cc2fa8", False, True),
    ("a fabricated object id, which nothing writes", "0ff1ce0", False, False),
    # 141cf6f served here until the #504 merge wrote it into ADR-0029 and the
    # threat model as a measurement base (2026-09-03); an exemplar rots the day
    # someone anchors to it, so the row moved to a merge commit prose never cites.
    ("a reachable commit no governed prose cites", "8ff8c72", True, False),
)


#: What the qualifier-content check must do before its zero is read, as
#: ``(what it demonstrates, the planted sentence, the sha it must extract or
#: ``None``, must it be a violation)``.
#:
#: **Both halves, because either can fail silently.** A key that stopped matching
#: would report the same clean tree a repository with no landed claims reports,
#: which is the failure mode :data:`POSITIVE_CONTROLS` was written for one route
#: earlier. So each row states the token it must *extract* as well as the verdict
#: it must reach, and a row that extracts nothing says so with ``None`` rather
#: than passing as clean.
#:
#: The planted sentences are transcribed from ADR-0008's shipped qualifier, so a
#: key that stopped reaching the real surface is caught here rather than by the
#: real surface quietly emptying.
LANDED_CLAIM_CONTROLS: Final[tuple[tuple[str, str, str | None, bool], ...]] = (
    (
        "ADR-0008's shipped qualifier, whose landed sha is on the main line",
        "a commit on the branch that landed as `56582b2` ([#142](.../pull/142))",
        "56582b2",
        False,
    ),
    (
        "the same sentence with a fabricated landed sha -- the shape this route missed",
        "a commit on the branch that landed as `0ff1ce0` ([#142](.../pull/142))",
        "0ff1ce0",
        True,
    ),
    (
        "a real commit named as landed while it is only on a branch",
        "a commit on the branch that landed as `4bfec1d` ([#142](.../pull/142))",
        "4bfec1d",
        True,
    ),
    (
        "the phrase wrapped across a blockquote line, as ADR-0008 hard-wraps it",
        "a commit on the branch that landed\n> as `0ff1ce0` and was rewritten",
        "0ff1ce0",
        True,
    ),
    (
        "a route-2 qualifier that names no landed sha, which stays unexamined",
        "`4bfec1d`, a commit on the branch that was squashed away",
        None,
        False,
    ),
)


def ledger_drift(
    dangling: list[Anchor], ledger: tuple[tuple[str, str, int, str, str], ...]
) -> tuple[
    list[Anchor],
    list[tuple[str, str, int, str, str]],
    list[tuple[str, str, int, int]],
]:
    """``(unclassified, stale, occurrence drift)`` for one dangling set against one ledger.

    Three directions, one per way the ledger and the tree can disagree: an anchor
    at a ``(token, path)`` nobody judged, a judged ``(token, path)`` the sweep no
    longer produces, and a judged ``(token, path)`` whose occurrence count moved.
    The third is what a token-keyed ledger could not see at all.

    The ledger is a parameter so all three can be **driven** from planted input --
    :data:`LEDGER_CONTROLS`, and round one's code-M6 across the five audits here.
    """
    produced: dict[tuple[str, str], int] = {}
    for anchor in dangling:
        produced[anchor.token, anchor.path] = produced.get((anchor.token, anchor.path), 0) + 1
    recorded = {(token, path): count for token, path, count, _, _ in ledger}

    unclassified = [anchor for anchor in dangling if (anchor.token, anchor.path) not in recorded]
    stale = [entry for entry in ledger if (entry[0], entry[1]) not in produced]
    miscounted = [
        (token, path, count, produced[token, path])
        for (token, path), count in recorded.items()
        if (token, path) in produced and produced[token, path] != count
    ]
    return unclassified, stale, miscounted


#: What the ledger reconciliation must do, driven from synthetic anchors, as
#: ``(what it demonstrates, the dangling anchors as (token, path), the ledger,
#: unclassified, stale, miscounted)``.
#:
#: Round one's code-M6 and H-C together: no control drove either direction, and
#: the key had one dimension where it needed three. The second and third rows are
#: the absorption -- a token this ledger carries, reused in a new document and
#: reused again in the file it was recorded for.
LEDGER_CONTROLS: Final[
    tuple[
        tuple[
            str,
            tuple[tuple[str, str], ...],
            tuple[tuple[str, str, int, str, str], ...],
            int,
            int,
            int,
        ],
        ...,
    ]
] = (
    (
        "a dangling anchor its ledger row covers: no drift in any direction",
        (("1a37c86", "a.md"),),
        (("1a37c86", "a.md", 1, "DANGLING, #463", "why"),),
        0,
        0,
        0,
    ),
    (
        "the same token in a document nobody judged -- the unclassified direction",
        (("1a37c86", "a.md"), ("1a37c86", "b.md")),
        (("1a37c86", "a.md", 1, "DANGLING, #463", "why"),),
        1,
        0,
        0,
    ),
    (
        "a second occurrence in the file the row was written for -- the count moves",
        (("1a37c86", "a.md"), ("1a37c86", "a.md")),
        (("1a37c86", "a.md", 1, "DANGLING, #463", "why"),),
        0,
        0,
        1,
    ),
    (
        "a ledger row the sweep no longer produces -- the stale direction",
        (),
        (("1a37c86", "a.md", 1, "DANGLING, #463", "why"),),
        0,
        1,
        0,
    ),
)


def _run_ledger_controls() -> int:
    """Drive all three reconciliation directions from planted anchors and ledgers."""
    failures = 0
    ran = 0
    print("\n=== LEDGER CONTROLS (the reconciliation, driven) ===")
    for label, produced, ledger, want_new, want_stale, want_count in LEDGER_CONTROLS:
        ran += 1
        dangling = [
            Anchor(path=path, line=0, token=token, context=f"measured at `{token}`")
            for token, path in produced
        ]
        unclassified, stale, miscounted = ledger_drift(dangling, ledger)
        got = (len(unclassified), len(stale), len(miscounted))
        want = (want_new, want_stale, want_count)
        status = "OK  " if got == want else "FAIL"
        failures += status == "FAIL"
        print(f"  {status} {label}: (unclassified, stale, miscounted)={got}, expected {want}")
    print_control_tally("LEDGER_CONTROLS", ran, failures)
    return 1 if failures else 0


def _run_positive_controls(root: Path, reference: str) -> int:
    """Both halves: the key still produces the known members, and the reachability
    test still separates a commit on ``main`` from one that is not.

    A run that skipped the second half would report a clean tree from a
    ``merge-base`` that had started succeeding for everything.
    """
    found = {anchor.token for anchor in anchors(root)}
    failures = 0
    ran = 0
    print(f"=== POSITIVE CONTROLS (reachability measured against `{reference}`) ===")
    for label, token, reachable, in_prose in POSITIVE_CONTROLS:
        ran += 1
        resolves = _is_ancestor(root, token, reference)
        produced = token in found
        status = "OK  " if resolves == reachable and produced == in_prose else "FAIL"
        failures += status == "FAIL"
        print(
            f"  {status} {label}: token={token} "
            f"reachable={resolves} (expected {reachable}), "
            f"in governed prose={produced} (expected {in_prose})"
        )
    print_control_tally("POSITIVE_CONTROLS", ran, failures)
    return (1 if failures else 0) | _run_landed_controls(root, reference)


def _run_landed_controls(root: Path, reference: str) -> int:
    """The qualifier-content key and its verdict, against planted sentences."""
    failures = 0
    ran = 0
    print(f"\n=== QUALIFIER-CONTENT CONTROLS (measured against `{reference}`) ===")
    for label, sentence, expected, must_violate in LANDED_CLAIM_CONTROLS:
        ran += 1
        claims = landed_claims_in(sentence)
        extracted = claims[0].token if claims else None
        violated = any(_landed_verdict(root, claim.token, reference) for claim in claims)
        status = "OK  " if extracted == expected and violated == must_violate else "FAIL"
        failures += status == "FAIL"
        print(
            f"  {status} {label}: extracted={extracted} (expected {expected}), "
            f"violation={violated} (expected {must_violate})"
        )
    print_control_tally("LANDED_CLAIM_CONTROLS", ran, failures)
    return (1 if failures else 0) | _run_ledger_controls()


def main(argv: list[str]) -> int:
    root = repo_root()
    reference = main_reference(root)
    if "--positive-control" in argv:
        return _run_positive_controls(root, reference)
    rows = anchors(root)

    resolved: list[Anchor] = []
    qualified: list[Anchor] = []
    dangling: list[Anchor] = []
    for anchor in rows:
        if _is_ancestor(root, anchor.token, reference):
            resolved.append(anchor)
        elif _qualified(anchor):
            qualified.append(anchor)
        else:
            dangling.append(anchor)

    promises = landed_claims(root)
    broken = [
        (claim, verdict)
        for claim in promises
        if (verdict := _landed_verdict(root, claim.token, reference))
    ]

    print(f"=== SHA-LIKE ANCHORS in {'/'.join(GOVERNED_ROOTS)} ===")
    print(f"  occurrences: {len(rows)}   distinct tokens: {len({a.token for a in rows})}")
    print(f"  resolve on `{reference}`: {len(resolved)}")
    print(f"  carry the pull-request qualifier: {len(qualified)}")
    print(f"  neither: {len(dangling)}")
    print(f"  `landed as` promises in governed prose: {len(promises)}")
    print(f"  of those, unreachable or unresolvable: {len(broken)}")

    return _report(dangling, broken)


def _report(dangling: list[Anchor], broken: list[tuple[LandedClaim, str]]) -> int:
    """Print every way the tree and the ledger disagree, and grade the run."""
    unclassified, stale, miscounted = ledger_drift(dangling, CLASSIFIED)

    if dangling:
        print("\n=== NEITHER RESOLVED NOR QUALIFIED ===")
        for anchor in dangling:
            verdict = next(
                (
                    entry[3]
                    for entry in CLASSIFIED
                    if entry[0] == anchor.token and entry[1] == anchor.path
                ),
                "UNCLASSIFIED",
            )
            print(f"  {anchor.path}:{anchor.line}  {anchor.token}  [{verdict}]")
            print(f"      {anchor.context[:130]}")
    if unclassified:
        print("\nUNCLASSIFIED ANCHORS -- a dangling anchor at a place nobody judged:")
        for anchor in unclassified:
            print(f"  {anchor.path}:{anchor.line}  {anchor.token}")
    if stale:
        print("\nSTALE LEDGER ROWS -- the sweep no longer produces these:")
        for token, path, occurrences, verdict, _ in stale:
            print(f"  {token}  {path}  x{occurrences}  [{verdict}]")
    if miscounted:
        print("\nOCCURRENCE DRIFT -- an anchor was added to or removed from a judged file:")
        for token, path, recorded, produced in miscounted:
            print(f"  {token}  {path}  recorded x{recorded}, produced x{produced}")
    if broken:
        print("\n=== QUALIFIERS PROMISING A COMMIT A READER CANNOT REACH ===")
        for claim, verdict in broken:
            print(f"  {claim.path}:{claim.line}  {claim.token}  {verdict}")
            print(f"      {claim.context[:170]}")
    return 1 if unclassified or stale or miscounted or broken else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
