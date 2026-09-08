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
inside :data:`_FILESYSTEM_SAFE` and keep their spelling, at the cost of the short
case tag the paragraph below adds; its **legacy** ids are standard base64 and carry
``=``, ``+`` and ``/``, and the last of those is a path separator, so a rule that
only validated would lose records the provider still answers with.

**Two ids that differ as bytes get leaves that differ on a filesystem that folds
case**, which is the part this used to get wrong. macOS and Windows fold by
default, so keeping the *byte* spellings apart was not enough: ``PRRT_kwDOAbc``
and ``prrt_kwdoabc`` named one file, and so did ``SHA256-<hex>`` and the hashed
leaf whose digest is that hex. Three families of name, and the argument is per
pair rather than one sentence:

* A **hashed** leaf is :data:`_HASHED_PREFIX` and lowercase hex, so it is its own
  casefold, and two of them differ whenever their ids do (SHA-256).
* A **verbatim** leaf is an id that is already its own casefold, so two of them
  that fold together are equal.
* A **tagged** leaf is ``<id>~<case tag>``. :data:`_CASE_TAG_SEPARATOR` is
  outside :data:`_FILESYSTEM_SAFE`, so it occurs exactly once and the split is a
  fact rather than a guess; the tag is lowercase hex, so folding leaves it alone;
  and the tag names every position folding changes, so the folded spelling and
  the tag together **are** the id.

Across the families: a tagged leaf carries the separator and neither other family
can, and a hashed leaf begins with the prefix where a verbatim one cannot --
because the prefix is tested against the id's *casefold*, so ``SHA256-`` routes
down the hashing arm exactly as ``sha256-`` does.

The tag is exact rather than probable, and that rests on one property of
:data:`_FILESYSTEM_SAFE`: it admits ASCII only, where folding maps ``A``--``Z``
to ``a``--``z`` one character at a time and changes no length. A digest in the
tag's place would make a collision merely unlikely, and a truncated one not even
that. ``tests/unit/test_review_evidence_store.py`` holds both halves:
``test_no_two_ids_share_a_leaf_on_a_case_insensitive_filesystem`` is what fails
when a pair above stops holding, and
``test_the_verbatim_charset_folds_one_ascii_character_at_a_time`` is what fails
when the charset stops making the tag exact.
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
#: cosmetic: it is what keeps the hashed names apart from the other two families,
#: and an id that already carries it is sent down the hashing arm however safe
#: the rest of it looks -- the **escape prefix** rule. The test is against the
#: id's *casefold*, so a shouted spelling cannot cross the line either.
_HASHED_PREFIX: Final = "sha256-"

#: What stands between a verbatim id and its case tag. Chosen from **outside**
#: :data:`_FILESYSTEM_SAFE`, so an id can never contain one: that is what makes
#: the split between spelling and tag a fact about the name rather than a guess,
#: and the module docstring's per-pair argument rests on it.
_CASE_TAG_SEPARATOR: Final = "~"

#: An identifier this module will spell into a filename unchanged.
#:
#: Deliberately narrower than "what the filesystem accepts". It admits no ``/``
#: (a separator), no ``.`` at the start (a hidden file, and the first half of
#: ``..``), no space and nothing outside ASCII -- because these names go into a
#: Git working tree that is checked out on macOS, Linux and Windows, and a name
#: one of those normalises is a name two clones disagree about. ASCII-only is
#: also what makes :func:`_case_tag` exact rather than probable.
#:
#: Bounded at 120 characters: an id at that bound carrying the longest case tag
#: it can (30 hex digits) lands as a 156-byte leaf once the separator and the
#: suffix are added, inside every filesystem's 255-byte component limit, and the
#: bound is far above the ~32 characters GitHub's node ids occupy.
#: ``test_the_longest_leaf_this_layout_can_emit_fits_a_path_component`` measures
#: it rather than leaving the arithmetic here to be trusted.
_FILESYSTEM_SAFE: Final = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,119}\Z", re.ASCII)


def _hashed(value: str) -> str:
    """``value`` as a name that is a hash, and says so.

    SHA-256 over the UTF-8 bytes. Not a security boundary -- what makes the write
    safe is ``security/paths.py``'s containment on top of this.

    **Not total over ``str``, which this said until round two measured it.**
    ``json.loads`` decodes ``\\ud800`` into a lone surrogate and ``str.encode``
    declines one, so a provider id carrying one leaves here as a
    ``UnicodeEncodeError``. That is *not* fixed by encoding with
    ``surrogatepass``: the same code point in the same record still has no
    encoding when ``store._document`` writes the file, so a total leaf would buy
    a name for a record that cannot be written anyway. What holds the observable
    -- a run publishes a document rather than a traceback -- is
    ``ReviewEvidenceStore.write``'s landing seam, which is keyed on the
    complement of ``TheurianError`` and therefore covers this call and the
    encode a stage later with one guard. Reaching this function outside that seam
    is not something production does: ``git grep -n 'record_leaf(\\|record_path('
    -- packages/theurian-core/src`` answers five lines, all inside this module
    except ``EvidenceRecord.relative_path``, whose own two call sites are that
    seam and ``_stored`` -- and ``_stored``'s caller catches ``ValueError``,
    which a ``UnicodeEncodeError`` is.
    """
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()
    return f"{_HASHED_PREFIX}{digest}"


def _case_tag(provider_id: str) -> str:
    """Which positions of ``provider_id`` a case-folding filesystem would fold.

    A bitmask -- bit *i* for character *i* -- spelled as lowercase hex, so the tag
    is its own casefold and folding a leaf leaves the tag intact. Empty exactly
    when the id already is its own casefold, which is the case that needs no tag.

    **Exact, not a digest.** The ids that reach here matched
    :data:`_FILESYSTEM_SAFE` and are therefore ASCII, where folding is a
    one-character-to-one-character map, so the folded spelling plus this mask
    recover the id itself. A digest here would leave two case-variants of one id
    colliding with some probability instead of none -- and a *truncated* digest
    barely that: an ``sha256[:8]`` tag is a 32-bit birthday search over the case
    variants of a single id, which for the 18 letters of ``PRRT_kwDOABCD1M5abcde``
    produced a colliding pair after 5,201 candidates in 0.01 s (measured
    2026-09-08).
    """
    mask = 0
    for index, character in enumerate(provider_id):
        if character != character.casefold():
            mask |= 1 << index
    return format(mask, "x") if mask else ""


def repository_directory(provider: str, identity: str) -> str:
    """The directory name one repository's records live under.

    Args:
        provider: The provider that answered, ``"github"`` today.
        identity: How that provider identifies the repository -- its node id
            where one was fetched, otherwise the ``owner/name`` it resolved.

    Returns:
        A hashed name. Always hashed: the value arrives from outside, and a rule
        that hashed only what looked dangerous would be a judgement about
        ``owner/name`` strings rather than a property of the path. Being prefix
        and lowercase hex, the name is its own casefold, so two identities that
        differ get two directories on a filesystem that folds case as well as on
        one that does not -- the leaf's three families need an argument for that
        (see the module docstring) and this one does not.
        ``test_two_repositories_never_share_a_directory`` holds both halves.

    The two arguments are joined by a NUL, which neither can contain: a separator
    that either side could carry would let two different pairs hash to one
    directory.
    """
    return _hashed(f"{provider}\x00{identity}")


def record_leaf(provider_id: str) -> str:
    """The filename one record lands under, given the provider's id for it.

    The **hashing arm** when the id is not a name a filesystem should carry, or
    when its casefold begins with :data:`_HASHED_PREFIX` -- the fold is why
    ``SHA256-...`` cannot be spelled out to name a hashed leaf. Otherwise the
    **verbatim arm**: the id as it is, followed by :func:`_case_tag` when the id
    is not already its own casefold. The module docstring records why the three
    shapes stay apart on a filesystem that folds case, and which tests fail when
    they stop.

    Raises:
        ValueError: If ``provider_id`` is empty. An empty id is not a record the
            caller can have received -- every domain type here refuses one at
            construction -- so this is the assertion that the caller did not
            build a path from a field it never read.
    """
    if not provider_id:
        raise ValueError("An evidence record's provider id must not be empty")
    if (
        provider_id.casefold().startswith(_HASHED_PREFIX)
        or _FILESYSTEM_SAFE.match(provider_id) is None
    ):
        return f"{_hashed(provider_id)}{EVIDENCE_SUFFIX}"
    tag = _case_tag(provider_id)
    if not tag:
        return f"{provider_id}{EVIDENCE_SUFFIX}"
    return f"{provider_id}{_CASE_TAG_SEPARATOR}{tag}{EVIDENCE_SUFFIX}"


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
