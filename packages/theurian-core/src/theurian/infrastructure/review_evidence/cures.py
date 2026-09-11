"""What an operator does about each way a review-evidence file refuses.

Split out of :mod:`theurian.infrastructure.review_evidence.store`, which had
reached the file-size limit with the cures occupying its first two hundred
lines. The seam is a real one rather than a place to cut: nothing here opens,
reads or writes anything, every function is total over its arguments, and each
returns a **string a caller may paste into a terminal**. That last property is
what these share and what the store does not have, and it is why the rules below
can be stated once for the module instead of per site.

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

**A cure that says a removal costs nothing takes the shape and does not say it
over a directory.** That is a safety predicate rather than a third house rule,
and it is enforced *here* rather than at the seams because a seam is where it
was missed. The first fix put the split in the store:
``ReviewEvidenceStore._publish`` grew a ``stat.S_ISDIR`` branch, and
``_temporary_refusal`` -- one seam over, and given a shape rather than a mode --
kept publishing :func:`planted_temporary_cure`'s "removing it loses nothing"
over a directory holding an operator's file. So both costless-claiming cures now
take the shape and route a container of other names to a move-style sibling
themselves, and a caller that never heard of the split inherits the guard.
``test_review_evidence_cures.py``'s
``test_no_cure_claims_a_costless_removal_outside_the_shape_guard`` renders every
cure this module publishes and reddens on one that makes the claim without it.
"""

from __future__ import annotations

import json
import stat
from typing import Any, Final

from theurian.domain.review_ingest import bounded_quote
from theurian.security.regular_file import shape_that_is_not_a_regular_file

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

#: What a reader does about a path under the review directory the filesystem
#: refused. The write is the last step of an ingestion run, so the run is what
#: gets repeated.
#:
#: **"the directory the message names" is what this said, and two of its three
#: sites name a file.** ``fingerprints`` publishes it over a directory that
#: could not be listed; ``_temporary_refusal``'s errno fallback names
#: ``<record>.writing`` and :meth:`~..store.ReviewEvidenceStore._write_one`'s
#: rename arm names the record -- and for either of those the mode to look at is
#: the *parent's*. The sentence now covers both rather than describing one.
UNWRITABLE_CURE: Final = (
    "Make `.theurian/review/` readable and writable, and the same for the path the "
    "message names -- if that path is a file, it is the directory holding it whose "
    "mode decides. `ls -ld .theurian/review` prints the mode and the owner. Then run "
    "the ingestion again."
)

#: What an operator does about something that is not a directory standing where
#: ``.theurian/review/`` belongs.
#:
#: **It offers a move and never a removal**, which is this module's safety
#: predicate met one level up from the record: whatever is at that path holds
#: bytes or holds names -- a regular file, an archive somebody unpacked wrong, a
#: directory link -- and nothing here can tell which or whose. The sibling cures
#: below reach the same conclusion per *shape*; this one cannot ask, because the
#: refusal that publishes it fires on ``Path.is_dir`` and never takes a mode.
#: So the sentence is written for the widest case and says so.
#:
#: ``ls -ld`` rather than ``ls -l``: the subject is the path itself and not what
#: a directory at it would contain, and ``ls -l`` on a directory lists its
#: entries instead of describing it.
MISPLACED_ROOT_CURE: Final = (
    "`ls -ld .theurian/review` prints what is at that path -- it is not a directory, "
    "so no evidence record can be listed and none can be written. Move it somewhere "
    "outside `.theurian/`, then run the ingestion again. Do not delete it: nothing "
    "here can tell what it holds or whose it is, and review evidence that was already "
    "under that name has no rebuild (ADR-0030 decision 3)."
)

#: What a refusal says about a record whose repository could not be read out of
#: its own file. Written once because both halves of
#: ``EvidenceReader._read_one`` publish it and the two must not drift into
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


def _shape_holds_other_names(shape: str) -> bool:
    """Whether ``shape`` names a container whose entries somebody else may own.

    The question the two costless-claiming cures below ask before they say a
    removal costs nothing, and the answer is ``stat.S_ISDIR`` asked one layer up.
    What a cure is handed is the *word*
    :func:`~theurian.security.regular_file.shape_that_is_not_a_regular_file`
    produced, so the word to compare against is recomputed by calling that same
    function on ``stat``'s own directory constant rather than spelt out here: a
    literal ``"a directory"`` would silently stop matching the day the prober
    reworded its answer, and a guard that stops matching is a guard that is off.

    ``S_IFDIR`` rather than a walk over every constant, because the property is
    about *this* type: a pipe, a socket and a device node hold no entries, and a
    symbolic link -- which the store's temporary seam also names, outside the
    prober's own range -- holds only itself, so removing one loses the link and
    not what it points at.

    **This is a shape comparison where ``ReviewEvidenceStore._publish`` had a
    ``stat.S_ISDIR`` branch, and the trade was taken deliberately.** That branch
    read the mode "so the split does not rest on a sentence matching", and it was
    right about literals -- but it was also unreachable from
    ``_temporary_refusal``, whose own value is a shape *string*: one of its two
    sources is an ``IrregularArtefactError`` measured from a descriptor, which
    carries no mode at all. That is the seam where the claim shipped over an
    operator's directory. What is compared here is not a sentence somebody typed:
    it is the prober's own answer, recomputed, so the two sides move together and
    a cure that a fifth seam calls carries the guard with it.
    """
    return shape == shape_that_is_not_a_regular_file(stat.S_IFDIR)


def planted_temporary_cure(opened: str, shape: str) -> str:
    """The cure for a planted artefact at a record's ``.writing`` **temporary**.

    :func:`planted_link_cure`'s sibling, and the difference between them is why
    it exists at all (round two, R2-B). The temporary is not the record: this
    store creates it, writes it and renames it away inside one call, so removing
    a link, a pipe, a socket or a device node sitting in its place costs nothing
    and is the actual cure. Publishing the record's cure here named the
    **landed** evidence file instead and instructed its deletion -- the one
    instruction this package must never publish -- over a link planted at the
    temporary beside it.

    ``opened`` is the temporary's own path, ``<record>.writing``, and naming it
    rather than the record is the whole correction: told the record's path, an
    operator runs ``ls -l`` on a file that is perfectly fine and finds nothing
    wrong with it.

    **``shape`` is taken so the costless clause can be withheld**, and a
    directory is the shape it is withheld for. The name being Theurian's
    temporary says nothing about what is standing at it: a directory there holds
    other names, and the measured plant was a directory carrying a file somebody
    wrote, published with "removing it loses nothing".
    :func:`occupied_temporary_cure` is where that shape goes.
    """
    if _shape_holds_other_names(shape):
        return occupied_temporary_cure(opened)
    return (
        f"`ls -l .theurian/review/{opened}` prints what is at that path. Remove it and "
        f"run the ingestion again: that name is a temporary this store creates and "
        f"renames away within a single write, so it holds no review evidence and "
        f"removing it loses nothing. The record beside it -- the same path without the "
        f"`.writing` suffix -- is the source and has no rebuild (ADR-0030 decision 3), "
        f"so do not remove that one."
    )


def occupied_temporary_cure(opened: str) -> str:
    """The cure for a **directory** at a record's ``.writing`` temporary.

    :func:`occupied_directory_cure`'s discipline over the other seam, and the two
    are separate because the true half of each sentence is different. There the
    artefact stands where the *record* belongs; here it stands where a temporary
    this store creates and renames away belongs -- which stays true and stays
    worth saying, because it is what tells the operator the record beside it was
    not written over.

    What does *not* follow from it is that removing the artefact is free. A
    directory holds other names whatever the name it sits under means to
    Theurian, so this asks for ``ls -la`` and offers a move, exactly as the
    record-path sibling does.
    """
    return (
        f"`ls -la .theurian/review/{opened}` prints what is inside it. That name is a "
        f"temporary this store creates and renames away within a single write, but what "
        f"is standing at it is a directory, and a directory holds other names. Move "
        f"whatever is under it somewhere outside `.theurian/review/`, remove the "
        f"directory once it is empty, and run the ingestion again. Do not delete it as "
        f"it stands -- the entries may be an operator's own files, and nothing here can "
        f"tell whose they are. The record beside it -- the same path without the "
        f"`.writing` suffix -- was not written over."
    )


def planted_artefact_cure(relative: str, shape: str) -> str:
    """The cure for a named pipe, socket or device at a record's **own** path.

    Distinct from :func:`planted_link_cure` because the danger is different and
    so is the sentence: a symbolic link is followed and a write through one
    truncates whatever it names, while these shapes destroy nothing and simply
    cannot be what was asked for. Distinct from
    ``no_follow.irregular_artefact_remedy``, whose closing clause -- "it is
    derived state (ADR-0004) that Theurian recreates, so nothing authored is
    lost" -- is false of everything under this directory.

    What makes the removal safe here is a property of the *shape* rather than of
    the directory: a pipe, a socket and a device node hold no bytes of their own
    to lose. That is why this may say "remove it" where
    :func:`relocated_directory_cure`, over a link that may already have records
    behind it, must not.

    **A directory is the shape that claim is false of**, and it used to arrive
    here: ``shape_that_is_not_a_regular_file`` answers ``"a directory"`` and the
    ``_publish`` refusal published this sentence over one, telling an operator
    that removing a directory holding their own files "loses nothing".
    :func:`occupied_directory_cure` is where that shape goes, and the routing is
    **here rather than at the caller**, which is the correction the temporary
    seam forced: ``_publish`` had its own ``stat.S_ISDIR`` branch and
    ``_temporary_refusal`` did not, so the same claim shipped one path over.
    ``tests/unit/test_review_evidence_cures.py`` recomputes the prober's range
    from ``stat``'s own file-type constants so a shape added to it without a
    cure of its own reddens.
    """
    if _shape_holds_other_names(shape):
        return occupied_directory_cure(relative)
    return (
        f"Remove `.theurian/review/{relative}` and run the ingestion again -- "
        f"`ls -l .theurian/review/{relative}` shows what is at the path now. It is "
        f"{shape}, which holds no bytes of its own, so removing it loses nothing; what "
        f"it is standing in the way of is a review evidence record, which is the source "
        f"and has no rebuild (ADR-0030 decision 3), so nothing was written over it."
    )


def occupied_directory_cure(relative: str) -> str:
    """The cure for a **directory** where an evidence record belongs.

    :func:`planted_artefact_cure`'s sibling, split off because the one sentence
    that makes that cure safe is false here. A pipe, a socket and a device node
    hold no bytes of their own; a directory is a container of other names, and
    the ones under it may be an operator's own files -- nothing at this seam can
    tell whose they are.

    So this offers a **move** and never an unqualified removal, and it asks for
    ``ls -la`` rather than ``ls -l``: what the reader has to see is what is
    *inside*, which is the question that decides whether the removal is safe.

    Reached through :func:`planted_artefact_cure`'s own guard rather than chosen
    by the store, so a caller that only knows the one entry point still gets it.
    """
    return (
        f"`ls -la .theurian/review/{relative}` prints what is inside it: a directory "
        f"sits where a review evidence record belongs, and a directory holds other "
        f"names. Move whatever is under it somewhere outside `.theurian/review/`, "
        f"remove the directory once it is empty, and run the ingestion again. Do not "
        f"delete it as it stands -- unlike a pipe or a socket at this path it may hold "
        f"an operator's own files, and nothing here can tell whose they are. The record "
        f"it is standing in the way of was not written over it."
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


def folded_component_cure(on_disk: str, derived: str) -> str:
    """The cure for a path component the disk spells otherwise than this build derives.

    Names **both** spellings, because on a filesystem that folds case the two
    reach one object: an operator told only the derived one lists the directory,
    sees the name they already have, and concludes the message is wrong.

    A rename and never a deletion. On a folding filesystem the differently-spelt
    directory *is* the one this store has been writing into, so it may already
    hold every record an earlier run landed -- and review evidence is the source
    rather than derived state (ADR-0030 decision 3). The two-step note is not
    padding: ``mv Pull-Request pull-request`` is a no-op on such a filesystem,
    which is exactly the shape an operator would try first.
    """
    return (
        f"`ls -l .theurian/review/` and its subdirectories print what is there. Rename "
        f"`{on_disk}` to `{derived}` -- on a filesystem that folds case that needs two "
        f"steps, through a third name, because the shell sees the two as one. Do not "
        f"delete it: it may already hold records an earlier run wrote, and review "
        f"evidence is the source rather than derived state (ADR-0030 decision 3), so "
        f"nothing rebuilds them."
    )


def oversized_record_cure(source_uri: str) -> str:
    """The cure for a record larger than the reader that has to read it back.

    Names the **upstream** conversation rather than a file, because there is no
    file: the refusal fires before the write, so there is nothing on disk to
    open. What the operator can act on is the review the record came from, and
    the anchor's source URI is the pointer to it.

    **The URI and not the record**, which is what the move out of ``store.py``
    changed: taking an ``EvidenceRecord`` here would make this module import the
    store that imports it.

    **Quoted rather than echoed**, matching :func:`unwritable_record_cure` over
    the same value. ``bounded_echo`` bounds the length and renders nothing, so a
    U+202E in a pull-request URL reached the terminal raw and reordered every
    line printed around it -- measured, and ``cli/output.escape_terminal_controls``
    does not catch it either, since that function escapes C0, C1 and DEL and
    U+202E is none of those. ``repr`` escapes it to ``\\u202e``.

    **"Nothing was written" is what this said until round two**, and it was false
    of the run: ``store.write`` is atomic per record and not across a call, so the
    records ahead of this one are on disk. The cure now scopes the claim to the
    record and leaves the run-level count to the refusal, which is the one place
    that knows it.
    """
    return (
        f"Look at the review this record came from -- {bounded_quote(source_uri)} "
        f"is the pull request, and `gh api graphql --hostname github.com` re-runs the "
        f"read by hand -- then shorten or split the conversation there. **This record** "
        f"was not written -- the refusal that carries this cure says what the run had "
        f"already landed -- because the size this refuses at is the one the reader "
        f"enforces: landing the file would have produced a record every later run "
        f"refuses to read, and review evidence has no rebuild that could clear it "
        f"(ADR-0030 decision 3)."
    )


def unwritable_record_cure(source_uri: str) -> str:
    """The cure for a record this build could not turn into bytes at all.

    The landing seam's residual (round two, R2-A): something the provider
    answered with reached an operation that is not total over a Python ``str``
    -- a lone surrogate, which ``json.loads`` decodes and UTF-8 cannot encode, is
    the measured member -- so there is no file to open and no size to shrink.
    What the operator can act on is the upstream conversation, and what they can
    run is the query that shows what came back.

    :func:`bounded_quote` rather than :func:`bounded_echo`, and **the reason
    written here for two rounds was wrong**. It said an echoed lone surrogate
    would raise a second ``UnicodeEncodeError`` out of ``cli.commands._fail``'s
    non-JSON branch. It cannot: ``sys.stderr`` has carried
    ``errors="backslashreplace"`` since CPython 3.5, so that write is total and
    renders a lone surrogate as ``\\ud800`` whichever helper produced it --
    measured, and it is why a test asserting ``\\ud800`` in the published text
    could not tell the two apart.

    What ``repr`` actually buys is the class ``escape_terminal_controls`` does
    **not** cover. That function escapes C0, C1 and DEL; U+202E is none of those
    and rides through it, reordering every line printed around it. ``repr``
    escapes it to ``\\u202e``, which is why quoting is right at a site handed a
    provider-chosen URL.
    """
    return (
        f"Look at the review this record came from -- {bounded_quote(source_uri)} is the "
        f"pull request -- and re-run the read by hand with `gh api graphql --hostname "
        f"github.com` to see what the provider answered with. Report it against the "
        f"provider adapter: a record this build cannot render is an answer it does not "
        f"know how to store, not something an operator can correct under "
        f"`.theurian/review/`."
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

    **Quoted rather than echoed**, for :func:`oversized_record_cure`'s reason:
    the repository is a string out of a landed file, so it may carry a U+202E,
    which ``bounded_echo`` bounds and does not render -- measured reaching the
    terminal raw through this clause.

    **This parse is the one that already failed, run again inside the handler
    grading it**, which is why ``RecursionError`` is caught here and not only at
    ``reader._stored``'s own ``json.loads``. Both calls decode the same bytes at
    the same depth, so a landed file of 20,000 nested arrays raised a second
    ``RecursionError`` out of the arm that was composing the refusal about the
    first -- measured, and the reason a fix applied only at ``_stored`` leaves
    the run publishing nothing.
    """
    try:
        parsed = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError, RecursionError):
        return UNNAMED_REPOSITORY
    if not isinstance(parsed, dict):
        return UNNAMED_REPOSITORY
    document: dict[str, Any] = parsed
    repository = document.get("repository")
    if not isinstance(repository, str) or not repository.strip():
        return UNNAMED_REPOSITORY
    return f"which names {bounded_quote(repository)}"
