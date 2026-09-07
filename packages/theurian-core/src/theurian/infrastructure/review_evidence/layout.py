"""Where one evidence record lives, derived from identifiers and never from a name.

ADR-0030 decision 3: *"An evidence file's path is derived from
provider-generated identifiers, or a hash of the repository identity -- never by
joining the configured ``owner/repo`` string into a filesystem path."* The reason
is measurable: ``providers.review.repositories``' published pattern accepts
``../..``, so a joined path leaves the directory while satisfying the contract.

Two rules, and they answer two different questions.

**A repository is always a hash.** The identity a caller has is a name or a node
id, both of which arrive from outside; hashing removes the question of what
either may contain rather than answering it per shape.

**A record is its provider id when that id is a name a filesystem should carry,
and its hash otherwise.** The verbatim form is not decoration: these files are
git-trackable by design, a project may commit them, and a directory of
sha256 digests is unreadable to the human who then has to review a diff. GitHub's
current global node ids -- ``PR_kwDOA...``, ``PRRT_kwDOA...`` -- are already
inside :data:`_FILESYSTEM_SAFE`; its **legacy** ids are standard base64 and carry
``=``, ``+`` and ``/``, and the last of those is a path separator, so a rule that
only validated would lose records the provider still answers with.

**The two shapes cannot collide, and that is by construction rather than by
improbability.** A hashed leaf always begins with :data:`_HASHED_PREFIX`; a
verbatim leaf never does, because an id that starts with that prefix is sent down
the hashing arm regardless of how safe the rest of it looks. So the verbatim
names and the hashed names are disjoint sets, and no id can be made to name
another id's file.
"""

from __future__ import annotations

import hashlib
import re
from enum import StrEnum
from pathlib import PurePosixPath
from typing import Final

#: The version stamped into every record this module's store writes, and refused
#: when read back at any other value. Slice 3's SQLite store is built from these
#: files, so the number is the contract between the two halves rather than a
#: courtesy: a build that read a shape it did not recognise would produce a store
#: whose rows nobody can account for.
EVIDENCE_FORMAT_VERSION: Final = 1


class EvidenceKind(StrEnum):
    """The three record kinds, which are also the three directory names.

    A ``StrEnum`` so the member and the path segment are one thing: a mapping
    table would be a second place the layout is decided, and the reader walks
    these directories by name.
    """

    #: A pull request together with its outcome -- one
    #: :class:`~theurian.domain.review.ReviewEvent`.
    PULL_REQUEST = "pull-request"
    #: One top-level review -- a
    #: :class:`~theurian.domain.review.ReviewSubmission`.
    REVIEW_SUBMISSION = "review-submission"
    #: One conversation and every comment in it -- a
    #: :class:`~theurian.domain.review.ReviewThread`.
    REVIEW_THREAD = "review-thread"


#: The suffix every evidence file carries.
EVIDENCE_SUFFIX: Final = ".json"

#: What a leaf named after a hash begins with. Load-bearing rather than
#: cosmetic: it is what keeps the verbatim and hashed name sets disjoint.
_HASHED_PREFIX: Final = "sha256-"

#: An identifier this module will spell into a filename unchanged.
#:
#: Deliberately narrower than "what the filesystem accepts". It admits no ``/``
#: (a separator), no ``.`` at the start (a hidden file, and the first half of
#: ``..``), no space and nothing outside ASCII -- because these names go into a
#: Git working tree that is checked out on macOS, Linux and Windows, and a name
#: one of those normalises is a name two clones disagree about. Bounded at 120
#: characters, comfortably inside every filesystem's 255-byte component limit
#: once the suffix is added, and far above the ~32 characters GitHub's node ids
#: occupy.
_FILESYSTEM_SAFE: Final = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,119}\Z", re.ASCII)


def _hashed(value: str) -> str:
    """``value`` as a name that is a hash, and says so.

    SHA-256 over the UTF-8 bytes. Not a security boundary -- what makes the write
    safe is ``security/paths.py``'s containment on top of this -- but a total
    function from any string to a name, which is what the caller needs.
    """
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()
    return f"{_HASHED_PREFIX}{digest}"


def repository_directory(provider: str, identity: str) -> str:
    """The directory name one repository's records live under.

    Args:
        provider: The provider that answered, ``"github"`` today.
        identity: How that provider identifies the repository -- its node id
            where one was fetched, otherwise the ``owner/name`` it resolved.

    Returns:
        A hashed name. Always hashed: the value arrives from outside, and a rule
        that hashed only what looked dangerous would be a judgement about
        ``owner/name`` strings rather than a property of the path.

    The two arguments are joined by a NUL, which neither can contain: a separator
    that either side could carry would let two different pairs hash to one
    directory.
    """
    return _hashed(f"{provider}\x00{identity}")


def record_leaf(provider_id: str) -> str:
    """The filename one record lands under, given the provider's id for it.

    Verbatim when the id matches :data:`_FILESYSTEM_SAFE` and does not begin with
    :data:`_HASHED_PREFIX`; the id's hash otherwise. The module docstring records
    why both arms exist and why they cannot collide.

    Raises:
        ValueError: If ``provider_id`` is empty. An empty id is not a record the
            caller can have received -- every domain type here refuses one at
            construction -- so this is the assertion that the caller did not
            build a path from a field it never read.
    """
    if not provider_id:
        raise ValueError("An evidence record's provider id must not be empty")
    if provider_id.startswith(_HASHED_PREFIX) or _FILESYSTEM_SAFE.match(provider_id) is None:
        return f"{_hashed(provider_id)}{EVIDENCE_SUFFIX}"
    return f"{provider_id}{EVIDENCE_SUFFIX}"


def record_path(*, provider: str, identity: str, kind: EvidenceKind, provider_id: str) -> str:
    """One record's path relative to ``.theurian/review/``.

    A POSIX-spelled string rather than a :class:`~pathlib.Path`, because it is
    handed to ``security/paths.py``'s containment next and that is the shape it
    takes -- the same reason ``read_source_file``'s callers pass a
    ``PurePosixPath``. Three components: the hashed repository, the kind, and the
    leaf.
    """
    return str(
        PurePosixPath(repository_directory(provider, identity))
        / kind.value
        / record_leaf(provider_id)
    )
