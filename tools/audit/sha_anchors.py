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

from claim_surfaces import governed_paths, repo_root

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
_PULL_QUALIFIER: Final = re.compile(
    r"\b(?:pull\s*request|PR)\b|\bbranch\b|\bpull/\d+|\bunmerged\b|\bbefore\s+the\s+squash\b",
    re.IGNORECASE,
)

#: How far either side of the token the qualifier may sit.
_QUALIFIER_WINDOW: Final = 120

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


def _governed(path: str) -> bool:
    return any(path.startswith(prefix) for prefix in GOVERNED_ROOTS)


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
    environment = {
        name: value for name, value in os.environ.items() if name not in _INHERITED_GIT_OVERRIDES
    }
    for candidate in ("origin/main", "main"):
        completed = subprocess.run(  # noqa: S603 - fixed argv, no shell, no caller input
            ["git", "rev-parse", "--verify", "--quiet", f"{candidate}^{{commit}}"],  # noqa: S607
            cwd=root,
            check=False,
            capture_output=True,
            env=environment,
            timeout=_GIT_TIMEOUT_SECONDS,
        )
        if completed.returncode == 0:
            return candidate
    message = "neither `origin/main` nor `main` resolves in this checkout"
    raise SystemExit(message)


def _is_ancestor(root: Path, token: str, reference: str) -> bool:
    environment = {
        name: value for name, value in os.environ.items() if name not in _INHERITED_GIT_OVERRIDES
    }
    completed = subprocess.run(  # noqa: S603 - fixed argv, no shell, no caller input
        ["git", "merge-base", "--is-ancestor", token, reference],  # noqa: S607 - via PATH
        cwd=root,
        check=False,
        capture_output=True,
        env=environment,
        timeout=_GIT_TIMEOUT_SECONDS,
    )
    return completed.returncode == 0


#: The tokens a person has judged, as ``(token, verdict, why)``.
#:
#: **Exact in both directions**, like every ledger in this directory: a token the
#: sweep stops producing means the sentence moved and the row has to go with it.
#: A hex-looking English word stays here rather than being filtered out of the
#: key, so the filter cannot quietly widen.
CLASSIFIED: Final[tuple[tuple[str, str, str], ...]] = (
    # -- Ten real commits that are not on `main` ------------------------------
    # Every one of these resolves in a checkout that still has the branch and in
    # none that does not: `git cat-file -t` says `commit` here, and
    # `merge-base --is-ancestor <token> origin/main` says no. They are pre-squash
    # branch commits, which is what makes the anchor uncheckable for a reader who
    # cloned the repository -- #463's class. The census measured that class at
    # twelve, not the two ADR-0008 members known when #463 was filed; those two
    # are absent from this ledger because #199 unit B qualified them in the ADR
    # itself, so they now discharge on the pull-request route above rather than
    # on a row here. The ten below stay with #463.
    (
        "1a37c86",
        "DANGLING, #463",
        "ADR-0007 and ADR-0024 both anchor a dated count to it; a branch commit.",
    ),
    ("67727eb", "DANGLING, #463", "ADR-0027 and the threat model both anchor a corpus count."),
    ("a8c1ce3", "DANGLING, #463", "#26's branch commit; the squash that landed on main is not it."),
    ("b8d2030", "DANGLING, #463", "A measurement anchor in T-6's concurrency reproduction."),
    ("db36089", "DANGLING, #463", "Cited as what closed #17."),
    ("2793d7b", "DANGLING, #463", "Cited as #19's commit."),
    ("b8fa3e3", "DANGLING, #463", "Three remedy-string anchors in one entry."),
    ("61747b3", "DANGLING, #463", "T-18's mechanism anchor, three occurrences."),
    ("6087be4", "DANGLING, #463", "A dated real-CLI measurement anchor."),
    ("dc6aa79", "DANGLING, #463", "Named as the 0.1.0.dev4 commit for the withdrawal purge."),
    # -- Eight tokens that are not object ids at all --------------------------
    # `git cat-file -t` finds no object for any of them, and reading the sentence
    # says why: each is an illustrative content or state hash inside a quoted
    # error message or a sample table. They are in the population because the key
    # is deliberately dumb, and they are classified rather than filtered -- the
    # #470 precedent for the Mermaid hex colours, applied to the same shape.
    ("abc7cdb70713", "not an anchor", "A content hash inside ADR-0013's quoted refusal."),
    ("4f9c5503e198", "not an anchor", "The pinned half of the same quoted refusal."),
    ("7e1eb70348da", "not an anchor", "The same example, in the migration protocol."),
    ("9a1584226439", "not an anchor", "The pinned half of it."),
    ("ee3ab796ab22f936", "not an anchor", "A `stateHash` in a sample `doctor` table."),
    ("8624b114c4bc0017", "not an anchor", "The differing half of the same row."),
    ("f1711b98d302", "not an anchor", "A state hash inside a quoted database filename."),
    ("2e8880bf25be", "not an anchor", "A new state hash in the same worked example."),
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
    ("this branch's own base commit", "141cf6f", True, False),
)


def _run_positive_controls(root: Path, reference: str) -> int:
    """Both halves: the key still produces the known members, and the reachability
    test still separates a commit on ``main`` from one that is not.

    A run that skipped the second half would report a clean tree from a
    ``merge-base`` that had started succeeding for everything.
    """
    found = {anchor.token for anchor in anchors(root)}
    failures = 0
    print(f"=== POSITIVE CONTROLS (reachability measured against `{reference}`) ===")
    for label, token, reachable, in_prose in POSITIVE_CONTROLS:
        resolves = _is_ancestor(root, token, reference)
        produced = token in found
        status = "OK  " if resolves == reachable and produced == in_prose else "FAIL"
        failures += status == "FAIL"
        print(
            f"  {status} {label}: token={token} "
            f"reachable={resolves} (expected {reachable}), "
            f"in governed prose={produced} (expected {in_prose})"
        )
    return 1 if failures else 0


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

    print(f"=== SHA-LIKE ANCHORS in {'/'.join(GOVERNED_ROOTS)} ===")
    print(f"  occurrences: {len(rows)}   distinct tokens: {len({a.token for a in rows})}")
    print(f"  resolve on `{reference}`: {len(resolved)}")
    print(f"  carry the pull-request qualifier: {len(qualified)}")
    print(f"  neither: {len(dangling)}")

    classified = {token for token, _, _ in CLASSIFIED}
    unclassified = [anchor for anchor in dangling if anchor.token not in classified]
    stale = [entry for entry in CLASSIFIED if entry[0] not in {a.token for a in dangling}]

    if dangling:
        print("\n=== NEITHER RESOLVED NOR QUALIFIED ===")
        for anchor in dangling:
            verdict = next(
                (entry[1] for entry in CLASSIFIED if entry[0] == anchor.token), "UNCLASSIFIED"
            )
            print(f"  {anchor.path}:{anchor.line}  {anchor.token}  [{verdict}]")
            print(f"      {anchor.context[:130]}")
    if stale:
        print("\nSTALE LEDGER ROWS -- the sweep no longer produces these tokens:")
        for token, verdict, _ in stale:
            print(f"  {token}  [{verdict}]")
    return 1 if unclassified or stale else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
