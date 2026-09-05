"""SEC-10's repository allowlist, consulted before anything is spawned (ADR-0030).

``providers.review.repositories`` was published as a reserved key for four
milestones, and ADR-0030 decision 2 is what makes it load-bearing: the adapter
reads it **before** the process that would reach GitHub exists, so a repository
outside the list produces no spawn at all rather than a filtered result.

**The pattern is the schema's, and this module proves it.** Nothing validates
``.theurian/config.yaml`` against ``project-config.schema.json`` at runtime, so a
schema that rejects ``..`` and a reader that accepts it would leave the
tightening inert -- documentation with no enforcement behind it.
:data:`REPOSITORY_PATTERN` is therefore held equal to the published one by
``tests/unit/test_review_allowlist.py::test_the_pattern_this_module_enforces_is_the_one_the_schema_publishes``,
which reads the schema file rather than a transcription.

**Why the pattern rejects a dot segment at all.** ADR-0030 decision 3 puts the
evidence files under paths built from provider ids, never from the configured
string -- but the string reaches other places (a report, a refusal, a future
path), and the old pattern ``^[\\w.-]+/[\\w.-]+$`` accepts ``../..`` while
satisfying the schema. A leading dot is *not* refused wholesale: ``owner/.github``
is a real repository on GitHub, so what is refused is a segment that is **only**
one or two dots.

**Case folding, and why byte comparison would be wrong.** GitHub treats owner and
repository names case-insensitively -- ``Theurian/Theurian`` and
``theurian/theurian`` are one repository -- so an operator who lists one spelling
and asks for the other is asking for the repository they listed. The comparison
is :meth:`str.casefold`, which over the ASCII names GitHub issues is
ASCII-lowercasing; the stricter Unicode folding costs nothing here and is the
right default for a name that arrives as text.

**What an empty allowlist means: nothing is allowed.** A project that configures
no repositories ingests none. The refusal is the same one an unlisted repository
gets, because the two are the same fact from the caller's side.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Final

from theurian.domain.errors import ProjectConfigError
from theurian.domain.review_ingest import RefusalGrade, ReviewIngestRefusedError
from theurian.security.project_config import PROJECT_CONFIG_FILE, read_review_repositories

#: The shape a configured entry, and a requested repository, must have. Byte for
#: byte the ``pattern`` ``project-config.schema.json`` publishes for
#: ``providers.review.repositories``' items, which a test asserts rather than a
#: comment claims.
#:
#: The two lookaheads are what the tightening added: the first refuses a leading
#: ``.``/``..`` **segment** (``./x``, ``../x``), the second a trailing one
#: (``x/.``, ``x/..``). Neither refuses a dot that merely *starts* a longer name,
#: so ``owner/.github`` -- a repository that exists -- is still accepted.
REPOSITORY_PATTERN: Final = r"^(?!\.{1,2}(?:/|$))[\w.-]+/(?!\.{1,2}$)[\w.-]+$"

_REPOSITORY = re.compile(REPOSITORY_PATTERN)

#: A bound on the text this module will even try to match. ``re`` over an
#: unbounded string is work a caller chose, and a repository name is short by
#: construction: GitHub's own limits are 39 characters for an owner and 100 for a
#: repository, so 200 is generous and still nothing a caller can spend time on.
MAX_REPOSITORY_CHARS: Final = 200

#: What a reader does about an entry the pattern refuses. Names the artefact (the
#: key, in the file) and a command that prints the spelling that would work.
_MALFORMED_ENTRY_CURE: Final = (
    f"Correct the entry in {PROJECT_CONFIG_FILE} so it is an `owner/repo` name -- "
    f"`gh repo view <owner>/<name> --json nameWithOwner` prints the spelling GitHub "
    f"resolves, and a `.` or `..` segment is refused because such a value is a path "
    f"and not a repository."
)


def is_well_formed(repository: str) -> bool:
    """Whether ``repository`` is a name the allowlist could hold.

    Checked before the pattern is applied to anything: a caller-supplied string
    is matched only once it is short enough to be a name, so the regex is never
    handed an unbounded input.
    """
    # `fullmatch`, not `match`: Python's `$` also matches *before* a trailing
    # newline, so `"owner/repo\n"` satisfies the anchored pattern under `match`
    # and would reach an argument vector with a line break in it.
    return len(repository) <= MAX_REPOSITORY_CHARS and _REPOSITORY.fullmatch(repository) is not None


def allowlisted_repository(root: Path, config_file: Path, repository: str) -> str:
    """The allowlist entry ``repository`` names, or a graded refusal.

    Args:
        root: The project root, the containment boundary for reading the file.
        config_file: The project's ``config.yaml``, composed by the caller.
        repository: The ``owner/repo`` the caller asked to ingest.

    Returns:
        The **configured** entry, in the spelling the operator wrote. The caller
        compares GitHub's resolved ``nameWithOwner`` back against this value
        (ADR-0030 decision 2's rename-redirect check), so returning the request
        instead would compare the response against itself.

    Raises:
        ReviewIngestRefusedError: Graded
            :attr:`~theurian.domain.review_ingest.RefusalGrade.REPOSITORY_NOT_ALLOWLISTED`
            when the request is not a well-formed name, or names no configured
            entry. One grade for both, because a name outside the pattern can
            never match a validated entry: distinguishing them would report which
            shape the caller sent.
        ProjectConfigError: If the configuration file states an allowlist that is
            not a list of short strings. Raised rather than graded because it is
            the operator's own file being wrong, not the request.
    """
    entries = read_review_repositories(root, config_file)
    # The configured list is refused whole rather than filtered, and this is the
    # half a filter would hide: an entry that is not a well-formed name can never
    # match anything, so skipping it silently would leave an operator reading a
    # line in their own file that has no effect. `read_review_repositories` holds
    # the shape (a list of short strings); the *pattern* is this module's.
    malformed = [entry for entry in entries if not is_well_formed(entry)]
    if malformed:
        raise ProjectConfigError(
            f"`providers.review.repositories` in {PROJECT_CONFIG_FILE} lists "
            f"{malformed[0]!r}, which is not an `owner/repo` name the allowlist can "
            f"hold ({len(malformed)} of {len(entries)} entries).",
            remedy=_MALFORMED_ENTRY_CURE,
        )
    if is_well_formed(repository):
        wanted = repository.casefold()
        for entry in entries:
            if entry.casefold() == wanted:
                return entry
    raise ReviewIngestRefusedError(
        RefusalGrade.REPOSITORY_NOT_ALLOWLISTED,
        # The request is echoed and the configured list is not. What the caller
        # sent is already the caller's; printing the allowlist back would publish
        # which other repositories this project ingests to whoever provoked the
        # refusal.
        f"Review ingestion refused {_rendered(repository)}: it is not listed under "
        f"`providers.review.repositories` in this project's `.theurian/config.yaml`, "
        f"so no process was started.",
    )


def _rendered(repository: str) -> str:
    """The requested name, bounded, for a message.

    A refusal echoes what the caller sent so the operator can see the typo, and a
    caller can send a megabyte. The cut is at :data:`MAX_REPOSITORY_CHARS`, past
    which nothing could have been a repository name anyway.
    """
    if len(repository) <= MAX_REPOSITORY_CHARS:
        return repr(repository)
    return f"{repository[:MAX_REPOSITORY_CHARS]!r} (cut from {len(repository)} characters)"
