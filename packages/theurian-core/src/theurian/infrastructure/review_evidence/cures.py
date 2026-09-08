"""What an operator does about each way a review-evidence file refuses.

Split out of :mod:`theurian.infrastructure.review_evidence.store`, which had
reached the file-size limit with the cures occupying its first two hundred
lines. The seam is a real one rather than a place to cut: nothing here opens,
reads or writes anything, every function is total over its arguments, and each
returns a **string a caller may paste into a terminal**. That last property is
what these share and what the store does not have, and it is why the two rules
below can be stated once for the module instead of per site.

**Every path is named relative to the review directory, never absolutely.** A
remedy is text a caller may paste and quote elsewhere -- into an issue, into a
chat with a colleague -- and an absolute one carries the machine's home
directory with it (GHSA-97q9 is the same class one surface over). The caller
holds the absolute path and deliberately does not pass it.

**No cure here offers to delete a landed record.** Review evidence is the source
rather than derived state (ADR-0030 decision 3): the upstream comment a record
copied may already have been edited or deleted, so no refetch recovers a deleted
file. A cure that says "remove it and run the ingestion again" is correct for
:mod:`theurian.security.no_follow`'s derived artefacts and is data loss here.
Both rules are checked rather than asserted --
``tests/unit/test_review_evidence_cures.py`` walks every public callable and
constant of this module and holds them over it, so a cure added later inherits
the check instead of needing its own.
"""

from __future__ import annotations

import json
from typing import Any, Final

from theurian.domain.review_ingest import bounded_echo

#: What a reader does about a file under ``.theurian/review/`` that this build
#: cannot read. It names the artefact -- the file, by its path relative to the
#: review directory -- and a command that shows how it got that way, because the
#: fault is in the bytes and the operator has to look at them.
UNREADABLE_CURE: Final = (
    "Open the file the message names, under `.theurian/review/`, and compare it "
    "against what this build writes -- `git log -p -- .theurian/review` shows how it "
    "got that way if the directory is committed. Review evidence is the source and "
    "not a cache, so deleting the file is data loss rather than a rebuild: an "
    "upstream comment may already have been edited or deleted, and no refetch "
    "recovers it (ADR-0030 decision 3)."
)

#: What an operator does about two records in one run that name one file. Nothing
#: was overwritten and there is no file to open: the fault is in what the
#: provider answered with, so the cure is the query that shows it.
COLLISION_CURE: Final = (
    "Report this against the provider adapter: two records naming one file is an "
    "answer no repository should give -- either one key returned twice, or two keys "
    "a case-insensitive filesystem cannot tell apart -- and writing the second over "
    "the first would discard evidence no refetch recovers. Run the same query by "
    "hand with `gh api graphql --hostname github.com` to see what the provider "
    "returned."
)

#: What a reader does about a directory it cannot write or list. The write is the
#: last step of an ingestion run, so the run is what gets repeated.
UNWRITABLE_CURE: Final = (
    "Make `.theurian/review/` and the directory the message names readable and "
    "writable -- `ls -ld .theurian/review` prints the mode and the owner -- then run "
    "the ingestion again."
)

#: What a refusal says about a record whose repository could not be read out of
#: its own file. Written once because both halves of
#: ``ReviewEvidenceStore._read_one`` publish it and the two must not drift into
#: two different sentences.
UNNAMED_REPOSITORY: Final = "whose own repository could not be read"


def planted_link_cure(relative: str) -> str:
    """The cure for a symbolic link where an evidence record belongs.

    **Deliberately not ``no_follow.symbolic_link_remedy``**, whose every clause
    rests on a precondition this path does not satisfy: that text says the
    artefact is derived state (ADR-0004) "that Theurian recreates, so nothing
    authored is lost". Review evidence is the opposite -- the source, with no
    replayable origin (ADR-0030 decision 3) -- so publishing that sentence here
    would tell an operator a deleted file comes back when it does not.
    """
    return (
        f"Remove the symbolic link at `.theurian/review/{relative}` and run the "
        f"ingestion again -- `ls -l .theurian/review/{relative}` prints where it "
        f"points. Nothing was written through it: unlike every other path Theurian "
        f"refuses a link at, this one is not derived state, so a write that followed "
        f"it would have truncated whatever it names and Theurian would recreate "
        f"neither."
    )


def relocated_directory_cure(relative: str) -> str:
    """The cure for a symbolic link standing in for a directory of records.

    It does not offer to remove anything: the directory the link points at may
    already hold records an earlier run wrote through it, and telling an operator
    to delete a link over evidence is the one instruction this module must never
    publish.
    """
    return (
        f"`ls -l .theurian/review/{relative}` prints where the link points. Move the "
        f"records it already holds back under `.theurian/review/` and replace the link "
        f"with a real directory, then run the ingestion again. Do not simply remove it: "
        f"review evidence is the source rather than derived state, so whatever it "
        f"points at is not something a refetch rebuilds (ADR-0030 decision 3)."
    )


def oversized_record_cure(source_uri: str) -> str:
    """The cure for a record larger than the reader that has to read it back.

    Names the **upstream** conversation rather than a file, because there is no
    file: the refusal fires before the write, so there is nothing on disk to
    open. What the operator can act on is the review the record came from, and
    the anchor's source URI is the pointer to it.

    **The URI and not the record**, which is what the move out of ``store.py``
    changed: taking an ``EvidenceRecord`` here would make this module import the
    store that imports it. Echoed through
    :func:`~theurian.domain.review_ingest.bounded_echo`, because a source URI is
    a value the provider chose and a refusal must not carry a megabyte of it.
    """
    return (
        f"Look at the review this record came from -- `{bounded_echo(source_uri)}` "
        f"is the pull request, and `gh api graphql --hostname github.com` re-runs the "
        f"read by hand -- then shorten or split the conversation there. Nothing was "
        f"written: the size this refuses at is the one the reader enforces, so landing "
        f"the file would have produced a record every later run refuses to read, and "
        f"review evidence has no rebuild that could clear it (ADR-0030 decision 3)."
    )


def repository_named_in(raw: bytes) -> str:
    """Which repository a failing file claims, when its bytes still say.

    The directory a record sits in is a **hash** of the provider and the
    repository, so a refusal that names only the path tells an operator with
    several repositories ingested nothing about which one to look at. The
    repository is inside the file, and for the failures that dominate -- a field
    the domain refuses, an identity that does not match its directory -- the file
    still parses, so it can be read out and said.

    Best-effort by construction: the cases where it cannot be read are exactly
    the ones where nothing could read it (bytes that are not UTF-8, a document
    that is not JSON, a file too large or too irregular to open at all), and the
    caller says so rather than guessing.
    """
    try:
        parsed = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return UNNAMED_REPOSITORY
    if not isinstance(parsed, dict):
        return UNNAMED_REPOSITORY
    document: dict[str, Any] = parsed
    repository = document.get("repository")
    if not isinstance(repository, str) or not repository.strip():
        return UNNAMED_REPOSITORY
    return f"which names {bounded_echo(repository)}"
