"""The grammar ``fixCommit`` has to satisfy, as one corpus two seams are held to.

``fixCommit`` is a **git revision expression** unless something stops it being
one, and until the entry funnel landed nothing did. ``HEAD^{/planted}`` asks git
to search history for a commit whose message matches, ``:/<text>`` searches every
ref, ``HEAD~2..HEAD`` is a *range*, and a branch name resolves like any other
name -- so a caller who knows no sha at all could still make ``diff-tree`` print
a path and satisfy ``fix_commit_present`` (ADR-0033 decision 3). Two reviewers
recovered through that independently; measured on git 2.47.1, 2026-09-19, every
resolvable member below answered ``VERIFIED`` against a repository whose HEAD
touched the thread's file.

**The suffix is not the foreclosure it reads as.** The adapter appends
``^{commit}`` to the caller's token, which looks like it would break a search
expression -- and it does break ``:/planted``, which becomes the regex
``planted^{commit}`` and matches nothing. It does **not** break
``:/planted|zzzz``: the alternation makes the appended text part of the second
branch and the first branch still matches. That is why the corpus carries both
spellings; a battery holding only the first would report the family closed.

**Two seams, one corpus.** SEC-12 refuses at the wire with the key path that
broke (``schemas/mcp/review-generate-knowledge-candidate-input.schema.json``'s
``pattern``), and the adapter refuses at entry without spawning
(``infrastructure/git/fix_commit_check.py``). Two defences written in two
dialects drift apart silently, so both are driven from this table rather than
from two lists: ``tests/unit/test_candidate_input_schema.py`` asks the published
pattern, and ``tests/integration/test_fix_commit_check_adapter.py`` asks the
adapter against a real repository.

**They disagree on exactly one member, and it is measured rather than assumed.**
``jsonschema`` evaluates ``pattern`` with Python's ``re``, whose ``$`` matches
*before* a trailing newline; ECMA-262's does not. So the published
``^([0-9a-f]{40}|[0-9a-f]{64})$`` admits a forty-hex sha with a newline glued to
it, and the adapter's ``fullmatch`` is what refuses it -- which is the
measurement that makes the second defence load-bearing rather than a restatement
of the first. :data:`REFUSED` records that member's wire verdict on the member
itself, so neither seam's pin has to know about the other's dialect.

No member here is resolvable by construction: the two that have to *resolve* to
make a verdict change meaningful -- an abbreviation of a real sha, and a real
sha upper-cased -- are built from the fixture's own HEAD in the adapter battery,
because a constant cannot name a commit a fixture has not made yet.
"""

from __future__ import annotations

from typing import Final, NamedTuple


class RefusedFixCommit(NamedTuple):
    """One value the grammar refuses, and what a caller reaches for it with."""

    label: str
    value: str
    why: str

    #: Whether the **published schema** refuses it too. False on exactly one
    #: member, for the dialect reason this module's docstring measures.
    at_the_wire: bool = True


#: The two shapes the grammar admits: a SHA-1 object name and a SHA-2 one.
#:
#: Both, because ``git init --object-format=sha256`` is a supported repository
#: and a forty-only funnel would refuse every commit in one. Lower case only:
#: git resolves an upper-cased sha (measured ``VERIFIED``), and admitting both
#: cases would mean two spellings of one object name reaching the verification.
ADMITTED: Final[tuple[str, ...]] = ("a" * 40, "b" * 64)

#: The widest admitted value, which is the ``maxLength`` the schema publishes.
#: Derived rather than written twice.
MAX_CHARS: Final = max(len(value) for value in ADMITTED)

#: Every value the grammar refuses, with what a caller would be reaching for.
#:
#: The resolvable members come first: each of those answered ``VERIFIED`` before
#: the funnel, so each carries a behavioural change and not only a spawn count.
#: The rest were already refused by git and are here for the spawn -- a refusal
#: that costs a process is a refusal a caller can time, and ADR-0033 decision 5
#: binds the duration as well as the text.
REFUSED: Final[tuple[RefusedFixCommit, ...]] = (
    RefusedFixCommit(
        "message-search",
        "HEAD^{/planted}",
        "`<rev>^{/<text>}` searches history for a commit whose message matches, so a "
        "caller who knows no sha names whichever commit touched the thread's file",
    ),
    RefusedFixCommit(
        "ref-wide-search-with-an-alternation",
        ":/planted|zzzz",
        "`:/<text>` searches every ref, and the alternation puts the appended "
        "`^{commit}` inside the second branch, so the suffix forecloses nothing",
    ),
    RefusedFixCommit(
        "ref-wide-search",
        ":/planted",
        "the same family without the alternation; git refuses this one on its own, "
        "which is exactly why the member above is in this table",
    ),
    RefusedFixCommit(
        "branch-name",
        "main",
        "a branch name resolves to its tip, so `fix_commit_present` is satisfiable "
        "without naming any commit",
    ),
    RefusedFixCommit(
        "head-relative",
        "HEAD~0",
        "`HEAD` and its `~`/`^` arithmetic name commits by position rather than by id",
    ),
    RefusedFixCommit(
        "revision-range",
        "HEAD~2..HEAD",
        "`diff-tree` accepts a range, so one token asks about a *set* of commits and "
        "the verdict is about none of them in particular",
    ),
    RefusedFixCommit(
        "full-ref",
        "refs/heads/main",
        "a fully-qualified ref is the branch member spelled the way the funnel's "
        "first draft would have missed",
    ),
    RefusedFixCommit(
        "reflog",
        "HEAD@{0}",
        "`@{n}` reads this machine's reflog, which is local history no caller was "
        "granted and which no other installation shares",
    ),
    RefusedFixCommit(
        "abbreviation",
        "a" * 7,
        "an abbreviated object name resolves, and is refused now: full length only, "
        "so an abbreviation that becomes ambiguous later cannot change a verdict",
    ),
    RefusedFixCommit(
        "upper-case",
        "A" * 40,
        "git resolves an upper-cased sha; admitting it would make two spellings of "
        "one object name reach the verification",
    ),
    RefusedFixCommit("empty", "", "the empty string is not a name"),
    RefusedFixCommit("one-short", "a" * 39, "thirty-nine hex digits are not an object name"),
    RefusedFixCommit("one-long", "a" * 65, "sixty-five hex digits are not one either"),
    RefusedFixCommit(
        "interior-length",
        "a" * 52,
        "fifty-two hex digits are neither of the two object-name widths, and this is the "
        "member that pins the grammar as exactly {40, 64} rather than a range: a regression "
        "to `[0-9a-f]{40,64}` admits everything from 40 to 64 and would ship green without a "
        "value strictly between the two lengths to refuse. Adapter and schema at once.",
    ),
    RefusedFixCommit(
        "trailing-space",
        "a" * 40 + " ",
        "a sha with whitespace glued to it is what a copy-paste produces, and a "
        "funnel anchored with `^...$` on the value alone would still refuse it",
    ),
    RefusedFixCommit(
        "trailing-newline",
        "a" * 40 + "\n",
        "the member that separates `fullmatch` from `match(...$)`: Python's `$` "
        "matches before a trailing newline, so a funnel written that way admits this",
        at_the_wire=False,
    ),
    RefusedFixCommit(
        "embedded-nul",
        "a" * 40 + "\x00",
        "a NUL cannot cross `subprocess`, which raises `ValueError` rather than "
        "returning; the funnel is what stops it reaching the spawn at all",
    ),
    RefusedFixCommit(
        "option-shaped",
        "--upload-pack=touch pwned",
        "the vector's `--end-of-options` already made this a non-resolving revision; "
        "the funnel is what stops it being spent as one",
    ),
)
