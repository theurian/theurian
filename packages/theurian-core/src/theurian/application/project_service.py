"""Project registration, ``.theurian/`` initialisation, and state resolution.

The registry is per-user (``~/.theurian/projects.json``) rather than per-project,
because one daemon serves many projects (ADR-0002). Everything under a project's
``.theurian/`` belongs to the project and travels with it in Git.
"""

from __future__ import annotations

import errno
import json
import os
import posixpath
import re
import shlex
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path, PurePosixPath
from typing import Any, Final, Never, NoReturn

from theurian.application.authorization import decode_sensitivities, encode_sensitivities
from theurian.domain.enums import Sensitivity
from theurian.domain.errors import InvalidIdentifierError, TheurianError
from theurian.domain.identifiers import ProjectId
from theurian.domain.migration import LoadedMigrations
from theurian.domain.ports import Clock
from theurian.domain.project import (
    DEFAULT_KNOWLEDGE_DIRECTORY,
    DERIVED_SUBDIRECTORIES,
    GITIGNORE_BLOCK_END,
    GITIGNORE_BLOCK_START,
    GITIGNORE_SECTIONS,
    Project,
)
from theurian.domain.state import ActiveState, StateHash, compute_state_hash, state_inputs_from
from theurian.security.no_follow import (
    is_a_symbolic_link_refusal,
    open_for_reading_without_following_a_link,
    open_without_following_a_link,
    write_text_without_following_a_link,
)
from theurian.security.project_config import PROJECT_CONFIG_FILE
from theurian.security.regular_file import read_text_from_a_regular_file

#: Directories `theurian init` creates. The derived ones are created too, so a
#: fresh clone has somewhere to put state without a later mkdir race.
INITIAL_DIRECTORIES: Final = (
    "knowledge/architecture",
    "knowledge/domain",
    "knowledge/operations",
    "knowledge/security",
    "knowledge/testing",
    "migrations",
    "specifications",
    "evaluations",
    "proposals",
    "proposals-local",
    "schema",
    "state",
    "cache",
    "runtime",
    "generated",
)

_SLUG_INVALID: Final = re.compile(r"[^a-z0-9]+")

#: Every failure to read the index pointer has the same cure, because the index
#: is derived (ADR-0004): throw the pointer away and build again.
#:
#: Public rather than module-private: `theurian index status` names the same
#: cure for the same file, through `cli.index_commands`, and a private name
#: re-typed there is a private name that drifts from this one the first time
#: either wording changes.
INDEX_POINTER_REMEDY: Final = (
    "Delete .theurian/state/active-index.json and run `theurian index build`; "
    "the index is derived, so nothing is lost."
)

#: The same statement for the canonical state pointer, which is derived from
#: Git-tracked migrations exactly as the index is (ADR-0004): `migrate apply`
#: rewrites it, and it holds nothing that is not recomputable.
#:
#: Public for the reason `INDEX_POINTER_REMEDY` is: the CLI and the MCP surface
#: both report this file, and two copies of one cure drift the first time either
#: is reworded.
#:
#: Names the *pointer*, never the state directory beside it. The databases there
#: cost a full re-apply to rebuild and are not what failed; a remedy that swept
#: them away would charge for damage the user did not have.
ACTIVE_POINTER_REMEDY: Final = (
    "Delete .theurian/state/active.json and run `theurian migrate apply`; "
    "the pointer is derived, so nothing is lost."
)

#: The same cure for the pointer the *operating system* refused, rather than the
#: one whose bytes will not parse.
#:
#: The delete above is a cure for a file whose contents are wrong, and it is one
#: the reader can carry out only if the directory holding it is theirs to write.
#: With `.theurian/state` at mode `000` -- #389's third face, and the arrangement
#: this constant exists for -- the delete is refused too, so publishing that text
#: alone sends the operator to run a command that fails the same way the one they
#: just ran did. Which of the two is in the way depends on where the permission
#: sits: a mode-`000` *file* in a writable directory is deletable, because
#: `unlink` is governed by the parent's bits, so the delete is named first and
#: the `chmod` is named as what to reach for when it is refused. Both acts are
#: runnable and both name the artefact, which is what a remedy owes
#: (`test_derived_state_value_envelope.py` pins the `chmod` half against the
#: mode-`000` plant).
ACTIVE_POINTER_UNREADABLE_REMEDY: Final = (
    "Delete .theurian/state/active.json and run `theurian migrate apply`. If the "
    "delete is refused too, run `chmod u+rwx .theurian/state` first -- the pointer "
    "is derived, so nothing is lost either way."
)

#: The cure for derived state that this installation did not build. Names the
#: whole `.theurian/state/` directory rather than the pointer alone: the
#: databases there are what carry the untrusted bytes, they are named by a hash
#: an attacker can compute, and `migrate apply` reuses a database file by name
#: (`database_for`) -- so deleting the pointer alone leaves the untrusted
#: database in place for the next apply to open. Also names the Git escape,
#: because the shape this closes is a repository contributor who force-added the
#: directory past its ADR-0004 ignore: a working-tree delete comes straight back
#: on the next checkout until the file is untracked.
UNBUILT_STATE_REMEDY: Final = (
    "Delete .theurian/state/ and run `theurian migrate apply` (then "
    "`theurian index build`) to rebuild it locally from the Git-tracked migrations. "
    "If .theurian/state/ is tracked by Git, also run `git rm --cached -r .theurian/state`: "
    "derived state must never be version-controlled (ADR-0004)."
)

#: What :func:`verify_state_provenance` refuses with. Interpolates nothing.
#:
#: **The path this used to name was the operator's resolved filesystem layout**
#: (GHSA-923w-f36f-jcfq). It read ``f"The derived knowledge state under {paths.state} ..."``,
#: and :attr:`ProjectPaths.state` is built from a *resolved* root -- so for a
#: project registered through a symbolic link it named the physical directory
#: behind ``rootPath``, which is a string ``project.list`` does not hand out: that
#: tool republishes the registry's own spelling verbatim
#: (``_publishable_field(e.get("rootPath", ""))``). This refusal has one consumer
#: and it is that same surface: ``git grep -n 'verify_state_provenance(paths' --
#: packages/theurian-core/src`` answers two lines, this note's quotation of the
#: key and the call in ``mcp/tools.py``'s ``_resolve`` -- which every
#: project-scoped tool resolves through. So the interpolation reached only readers
#: it was a disclosure to.
#:
#: **A constant here, rather than a suppressing handler at that call site**, which
#: was the other shape available. A handler covers the seam it is written on, and
#: a second caller would inherit the message without it; the guard belongs in the
#: thing that renders the claim, and a string with no ``f`` prefix has nothing left
#: to re-acquire a path through. A reader for whom the layout is *not* a
#: disclosure holds ``paths`` at its own call site and can name the directory
#: itself -- ``cli/index_commands.py``'s twin refusal for this same condition
#: names no path at all.
#:
#: Spells the directory as :data:`UNBUILT_STATE_REMEDY` above does, so the refusal
#: and its cure name one location rather than two spellings of it. **That spelling
#: is hardcoded where the old message derived it** (round one, code review LOW):
#: ``.theurian/state/`` is written out here, so a deployment that renamed its
#: knowledge directory reads a relative path it does not have -- correct only
#: while the default holds. It is the trade :data:`UNBUILT_STATE_REMEDY` already
#: makes one constant up, taken for the same reason: deriving it means
#: interpolating from ``paths``, and interpolating from ``paths`` is what put the
#: operator's resolved layout on this wire. A relative name that is wrong for a
#: renamed directory sends a reader to a path that is not there; the derived form
#: sent every reader the operator's machine layout. The class-level
#: claim -- that no response from any registered tool carries the resolved layout
#: -- is not asserted here; it is measured by
#: ``tests/integration/test_resolved_layout_never_crosses.py``, whose
#: ``delivered-state`` plant is the cell this constant turns green and which goes
#: RED if an interpolation returns.
_UNBUILT_STATE_REFUSAL: Final = (
    "This project's derived knowledge state under .theurian/state/ was not built by this "
    "Theurian installation, so it will not be served. It was delivered with the project "
    "rather than rebuilt here from the Git-tracked migrations, which is exactly what an "
    "untrusted repository must not be able to do (ADR-0004)."
)

#: The one review-finding store a project has (ADR-0029 phase-2).
#:
#: Findings are a wholesale projection of the repository's public history, so a
#: rebuild overwrites a single artifact rather than minting a build id per run --
#: which is why this is a constant and not a value read from a pointer file, and
#: why :meth:`ProjectPaths.findings_for` needs no state-scoped containment check
#: for it (see that method).
#:
#: **Public, and beside the method that spends it, because two surfaces now name
#: it.** ``theurian findings build`` writes this store and the ``review.findings``
#: MCP tool reads it; a private constant re-typed in the second place is a
#: constant that drifts the first time either is reworded, and the failure it
#: causes is silent -- the reader would open a path nothing writes and report an
#: empty or missing store for a project that has one.
FINDINGS_STORE_ID: Final = "local"

#: The one review search store a project has (ADR-0030 slice 3).
#:
#: A constant for :data:`FINDINGS_STORE_ID`'s reason: the store is a wholesale
#: projection of the evidence files under ``.theurian/review/``, rebuilt under one
#: name rather than minting a build id per run. Public, and beside the method that
#: spends it, because two surfaces name it -- ``theurian review build`` writes the
#: store and the serving surface reads it -- and a private constant re-typed in the
#: second place fails silently: the reader would open a path nothing writes and
#: report a missing store for a project that has one.
REVIEW_SEARCH_STORE_ID: Final = "local"


def _review_search_store_filename(store_id: str) -> str:
    """The name the review search store is written and read under.

    One spelling for two callers that must not drift:
    :meth:`ProjectPaths.review_search_for`, which derives the path, and
    :data:`REVIEW_SEARCH_STORE_FILENAME`, which the refusal and the cure below
    name. Two literals would eventually send a reader to remove a file nothing
    writes -- :data:`FINDINGS_STORE_ID`'s note records the same failure for the
    id itself.
    """
    return f"theurian-review-{store_id}.sqlite"


#: The file this project's one review search store lives in. Derived from
#: :data:`REVIEW_SEARCH_STORE_ID` rather than typed out, so the cure below names
#: the file ``theurian review build`` really writes.
REVIEW_SEARCH_STORE_FILENAME: Final = _review_search_store_filename(REVIEW_SEARCH_STORE_ID)

#: The cure when the review search store cannot be named at all -- a store id that
#: resolves outside ``.theurian/state/``, or that is not a usable filename.
#:
#: **The removal is published as something to run, and that is the fix for a
#: closed loop** (verdict pass, adversarial HIGH). This used to open "Remove the
#: file this names from .theurian/state/" -- prose, naming no file -- and back it
#: with ``theurian review build``. The backticked spans are what an automated
#: consumer lifts out of a cure, which is the key
#: ``test_published_cures_are_executable.py`` runs a published cure by, and the
#: one command this text backticked resolves the store through
#: :meth:`ProjectPaths.review_search_for` before it reads an evidence file: it
#: meets the same fault that produced the refusal and hands the caller back a
#: byte-identical one. The removal is the step that clears it, so the removal is
#: named as a command, over the file :data:`REVIEW_SEARCH_STORE_FILENAME` spells.
#:
#: **The operand is this module's own constant, never the id that was refused.**
#: By the time this text is read, that id is a string that climbed out of
#: ``.theurian/state/``, and ``rm`` is not a command to build out of input like
#: that. What the cure names is the file ``theurian review build`` writes, which
#: is the file to clear for the one id this composition passes --
#: :meth:`ProjectPaths.review_search_for` records why that id is a constant, and
#: the day a pointer supplies it this cure has to be keyed on the refused file
#: instead.
#:
#: **``rm`` alone, with no ``rm -rf`` twin, and the shape argument is what makes
#: that safe.** :func:`derived_escape_remedy` publishes both forms because it
#: names a *directory*, which plain ``rm`` cannot remove and whose link form
#: ``rm -rf`` follows through a trailing slash. This names a leaf, and a
#: directory sitting at it reaches neither arm that carries this cure: it
#: resolves inside ``.theurian/state/``, so the containment arm does not fire,
#: and it makes ``resolve`` raise nothing, so the unusable-name arm does not
#: either. What is left is a link -- which holds no bytes of its own -- and a
#: regular file, and plain ``rm`` removes both without the force ``-rf`` adds.
#:
#: **What the removal costs is stated for each of those two shapes rather than
#: waved past**: nothing for a link, and for the store itself only a projection
#: the next command rebuilds from the evidence files still on disk (ADR-0004). It
#: deliberately does **not** tell anyone to delete anything under
#: ``.theurian/review/``: that directory is the source and has no rebuild
#: (ADR-0030 decision 3).
REVIEW_SEARCH_STORE_REMEDY: Final = (
    f"Remove `.theurian/state/{REVIEW_SEARCH_STORE_FILENAME}` if it is there -- run "
    f"`rm .theurian/state/{REVIEW_SEARCH_STORE_FILENAME}` -- then run `theurian review "
    f"build` to rebuild the search store from the evidence files under .theurian/review/. "
    f"A clone can deliver that file as a symbolic link pointing out of the working tree: "
    f"removing a link costs nothing, and removing the store itself costs only the "
    f"projection the rebuild recreates. The evidence files are the source and are not "
    f"touched by a rebuild."
)

#: The half of "rename a project" that is easy to omit and impossible to notice.
#: Canonical rows are stamped with the id in force at `migrate apply`, and
#: `migrate apply` is idempotent, so it will not restamp them. An id changed
#: without this reads a store that holds nothing under the new id -- and reports
#: itself indexed while doing it.
_REBUILD_STATE_CLAUSE: Final = (
    "then delete .theurian/state/ and run `theurian migrate apply` followed by "
    "`theurian index build`. Canonical rows and index chunks carry the project id they "
    "were written with, so changing the id without rebuilding them addresses an empty project."
)

#: The cure for a knowledge directory that resolves outside the project root.
#: A clone can deliver ``.theurian`` as a committed symbolic link pointing out of
#: the working tree (``.theurian -> ../elsewhere``), and every path Theurian reads
#: or writes derives from it -- so a link that escapes turns `migrate apply`'s
#: state database, active pointer and write lock, and every read that follows,
#: into files outside the tree the clone gave the user (#237, T-5). Names the
#: escape rather than a file to delete, the shape :class:`PathEscapeError`'s
#: remedy takes: the cure is to make the knowledge directory a real directory
#: inside the project again, not to remove content a link happens to point at.
KNOWLEDGE_DIR_ESCAPE_REMEDY: Final = (
    "Replace the knowledge directory with a regular directory inside the project. A "
    "clone may have delivered it as a symbolic link pointing outside the working tree; "
    "remove the link, run `theurian init` to recreate the directory, then retry."
)

#: The cure for a symbolic link at ``.gitignore``, and **deliberately not**
#: :func:`~theurian.security.no_follow.symbolic_link_remedy` (#571).
#:
#: That text is written for a link at a *derived* path and says three things
#: this one cannot: that removing the link costs nothing because Theurian
#: recreates the artefact, that the artefact is ADR-0004 derived state, and that
#: a repository carrying the link force-added it past that ignore. ``.gitignore``
#: is authored, Git-tracked content that no ignore covers, and a clone carries a
#: symlinked one with nothing forced -- so all three clauses would be false, and
#: the first would tell an operator to delete something they wrote.
#:
#: What it says instead is what Theurian actually knows: which path is the link,
#: that nothing was written through it, and the two acts that resolve it -- look
#: at where it points, and replace it with a real file if those rules belong to
#: this repository. Both are runnable and neither destroys authored content.
#: ``{path}`` is filled by the caller so the cure names the file to act on.
#:
#: **Published to the whole reader population, not to the writer alone.** Round
#: one caught the class enumerated at two of three members: reproduce it with
#: ``git grep -n '\.gitignore' -- packages/theurian-core/src``, which on
#: 2026-09-06 returned three functions that *open* the file --
#: :func:`ensure_gitignore` (the writer), ``setup_steps.probe_gitignore`` (what
#: ``theurian doctor`` and ``theurian setup`` report), and
#: ``proposal_service._ensure_local_is_ignored`` (which calls the writer). The
#: probe was the missed one, and it published ``satisfied`` for a repository
#: where Git ignores nothing; it now takes this text as its action, so no reader
#: of the file sends an operator to a different cure than another.
GITIGNORE_LINK_REMEDY: Final = (
    "Inspect {path} with `ls -l` to see what it points at, then replace it with a "
    "regular file holding the rules this repository should ignore -- copy them from "
    "the link's target if that is where they live. Re-run `theurian init` afterwards "
    "to add Theurian's own block. Nothing was written through the link."
)

#: A path must name a derived subdirectory *and* something inside it before this
#: module can say the escape happened under a rebuildable artifact.
_MIN_PARTS_UNDER_A_DERIVED_SUBDIRECTORY: Final = 2


#: What a reader has to run *after* removing each derived subdirectory, keyed on
#: the subdirectory and not on the refused leaf -- the leaf is not what gets
#: removed, and four artifact families share ``state/``.
#:
#: ``state`` names four rebuilds because it holds four things: the canonical
#: database (``theurian-state-*``), the retrieval index (``theurian-index-*``),
#: the review-findings store (``theurian-findings-*``) and the review search
#: store (``theurian-review-*``, ADR-0030 slice 3), plus both pointers.
#: ``migrate apply`` rebuilds the first and republishes its pointer and is
#: therefore unconditional; the other three are named conditionally because a
#: project that never built them has nothing to rebuild. Keying on the
#: subdirectory cannot tell those four apart -- ``findings_for`` and
#: ``review_search_for`` both resolve under ``state`` too -- so the tail covers
#: the union rather than guessing which artifact the refused leaf was, and the
#: union has to grow with the family list: the fourth family landed with this
#: tail still naming three, which left a reader who had removed ``state/``
#: rebuilding everything except their review evidence's projection.
#:
#: ``runtime`` names none, because nothing there needs rebuilding: it holds the
#: advisory lock file and nothing else, and the next command recreates it.
#: Saying "run `theurian migrate apply` to rebuild the state" there -- which the
#: first cut of this remedy did for every subdirectory alike -- sends a reader to
#: rebuild something that was never lost.
#:
#: ``cache`` joined on #394's fix, which gave it the first ``ProjectPaths``
#: helper resolving under it (:attr:`ProjectPaths.ingestion_manifest`). Until
#: then no refusal could reach this mapping for it, and the note here said so;
#: leaving that sentence standing would have published the no-tail fallback for
#: a subdirectory that now has a cure to name.
#:
#: ``generated`` is still a member of ``DERIVED_SUBDIRECTORIES`` that no helper
#: resolves under, so no refusal can reach this mapping for it. It falls through
#: to no tail, which is the safe direction: an absent sentence tells a reader
#: nothing false, and a wrong rebuild command would.
_REBUILD_AFTER_REMOVING: Final[dict[str, str]] = {
    "state": (
        " Then run `theurian migrate apply` to rebuild the canonical state. If this "
        "project had also built a retrieval index, a review-findings store or a "
        "review search store, `theurian index build`, `theurian findings build` and "
        "`theurian review build` rebuild those."
    ),
    "runtime": (
        " Nothing needs rebuilding afterwards: the only thing Theurian keeps there is "
        "the advisory lock file, and the next command recreates it."
    ),
    "cache": (
        " Then run `theurian ingest` to rebuild the manifest. Nothing is lost by "
        "removing it: it records which sources have already been parsed, so the "
        "next run reparses them all and rewrites it."
    ),
}


def derived_escape_remedy(knowledge_directory_name: str, subdirectory: str) -> str:
    """The cure for a containment refusal on something under a derived directory.

    :data:`KNOWLEDGE_DIR_ESCAPE_REMEDY` was published for these too, and it is
    wrong twice over (#483 round one). It names *the knowledge directory* -- the
    operator's authored source -- for a refusal that is about
    ``.theurian/state/`` or ``.theurian/runtime/``, and it sends the reader to
    ``theurian init``, which meets the identical refusal: measured on a
    ``.theurian/runtime`` symlink out of the tree, ``migrate apply`` exits 4
    naming ``write.lock``, the remedy says to run ``init``, ``init`` exits 1 with
    the same containment error, and ``migrate apply`` still exits 4. A remedy
    whose instruction returns the reader to where they started is worse than
    none: it costs a round trip to learn nothing.

    **Names the directory, never the leaf, and that is the** :class:`EscapeSite`
    **rule applied here.** The refused path is the *leaf* Theurian asked for, but
    a link anywhere between the derived subdirectory and that leaf produces the
    same refusal -- in the measured case the culprit is ``runtime`` while the
    refused path is ``runtime/write.lock``, so telling the reader to remove the
    refused path would name a file inside the link's target and cure nothing.
    What this code *can* prove is the range the culprit lies in:
    :meth:`ProjectPaths.of` has already refused an escaping ``.theurian``, so the
    link is at the derived subdirectory or below it, and removing that
    subdirectory removes it wherever it sits.

    **The path is rendered without a trailing slash, and that is a safety
    property rather than a formatting choice.** The first cut of this remedy read
    ``Delete `.theurian/runtime/` `` -- and BSD ``rm -rf`` on a trailing-slash
    symlink *follows the link*. Measured end to end: following that instruction
    literally destroyed the entire directory the link pointed at, outside the
    working tree, **and left the link in place**, so the retry met the identical
    refusal. Every claim about ``rm``'s safety here is therefore scoped to the
    no-slash form, which is the only form measured safe:

    ==========================================  ==========================
    form                                        measured outcome
    ==========================================  ==========================
    ``rm -rf .theurian/runtime/`` (link)        target destroyed, link kept
    ``rm -rf .theurian/runtime`` (link)         link removed, target intact
    ``rm .theurian/runtime`` (link)             link removed, target intact
    ``rm -rf .theurian/runtime`` (real dir)     directory removed
    ==========================================  ==========================

    So the instruction is given in **both forms**, because one command does not
    honestly cover both shapes: plain ``rm`` cannot remove a real directory, and
    ``rm -rf`` is more force than a link needs. The trailing slash is called out
    explicitly rather than merely omitted -- a reader who "tidies" the path by
    adding one gets the destructive form back.

    **What the removal costs, stated without the sentence that was false.** This
    text used to promise "nothing authored is lost", which is not this function's
    to promise: when the culprit is a link, its *target* is arbitrary and need not
    be derived at all -- the measured case pointed at a directory outside the
    tree. What is true, and is what the text says now, is narrower and enough:
    removing a link costs nothing, and removing a real directory here costs only
    artifacts Theurian rebuilds, because everything under a member of
    ``DERIVED_SUBDIRECTORIES`` is rebuildable by definition (ADR-0004) -- exactly
    what :data:`KNOWLEDGE_DIR_ESCAPE_REMEDY`'s own subject is not.

    The rebuild tail is keyed per subdirectory by
    :data:`_REBUILD_AFTER_REMOVING`, which records why ``state`` names four
    commands and ``runtime`` names none.
    """
    path = f"{knowledge_directory_name}/{subdirectory}"
    return (
        f"Remove `{path}` -- run `rm {path}` if it is a symbolic link, or "
        f"`rm -rf {path}` if it is a real directory. Do not add a trailing slash: with "
        f"one, `rm -rf` follows the link and deletes what it points at while leaving the "
        f"link in place. A clone may have delivered part of that path as a symbolic link "
        f"force-added past the ADR-0004 ignore; removing a link costs nothing, and "
        f"removing a real directory here costs only artifacts Theurian rebuilds."
        f"{_REBUILD_AFTER_REMOVING.get(subdirectory, '')}"
    )


#: The child of the knowledge directory **this change carves out**: neither
#: authored source in the sense :data:`KNOWLEDGE_DIR_ESCAPE_REMEDY` is written
#: for nor a member of ``DERIVED_SUBDIRECTORIES``, so neither existing cure
#: describes it.
#:
#: Not the only member of that class, and an earlier version of this note called
#: it the one. ``theurian init`` writes no ``.theurian/config.yaml`` either, so
#: :attr:`ProjectPaths.config` still publishes the fallback's "run `theurian
#: init` to recreate the directory" clause against a file ``init`` does not
#: create. That face is filed as
#: `#652 <https://github.com/theurian/theurian/issues/652>`_ and is deliberately
#: not carved out here.
_REVIEW_SUBDIRECTORY: Final = "review"


def review_escape_remedy(knowledge_directory_name: str) -> str:
    """The cure for a containment refusal on the review-evidence directory (#602).

    :data:`KNOWLEDGE_DIR_ESCAPE_REMEDY` was published here, and its middle
    clause is false for this artefact. ``review`` is not in
    :data:`INITIAL_DIRECTORIES`, so ``theurian init`` creates nothing at this
    path -- an operator who follows that cure literally runs a command that does
    nothing and has no way to tell that from a command that failed silently.
    What creates the directory is the evidence store, at its first write, so
    this text names ``theurian review ingest`` instead. The gap in between is
    not a broken state: ``EvidenceReader`` reads an absent directory as an empty
    corpus, which is why the cure can say so --
    ``tests/unit/test_review_evidence_store.py::test_a_store_over_a_directory_that_does_not_exist_reads_as_empty``
    is the key, and it is the only claim here resting on a behaviour rather than
    on a shape.

    **Plain ``rm`` with no ``rm -rf`` twin, which is a shape argument rather
    than a style one.** :func:`derived_escape_remedy` publishes both forms
    because the culprit it names may be a real directory. Here it may not be:
    :meth:`ProjectPaths.of` refuses before this class exists unless the resolved
    knowledge directory is inside the resolved root, so by the time ``review`` is
    resolved the only object on that path that can still leave the tree is a
    symbolic link -- and plain ``rm`` removes a link without the force ``-rf``
    adds. Offering ``-rf`` would be worse than redundant: ADR-0030 decision 3
    makes review evidence canonical with no replayable source, so a reader who
    reaches for the force arm over a real directory destroys records no rebuild
    recovers. Against a real directory plain ``rm`` fails and removes nothing,
    which is the safe direction, and the ``ls -l`` that opens the cure is what
    lets a reader see which shape they have before they type anything.

    **Where that link can be is a dated measurement, and the cure names one
    place.** The ``rm`` below is rendered against the directory itself
    (``rm .theurian/review``, for the default basename) and so removes a link
    *at* ``review`` and nothing deeper, which holds only while ``review`` is the
    deepest thing any helper resolves under that name. It is, today: ``git grep -nE
    '_contained\\(self\\.knowledge_dir / _REVIEW_SUBDIRECTORY' --
    packages/theurian-core/src/theurian/application/project_service.py`` printed
    one line on 2026-09-12, :attr:`ProjectPaths.review` itself, and the key does
    not hit this paragraph because the text above spells the call with
    backslashes. :meth:`ProjectPaths._escape_remedy` keys its carve-out on the
    *first component at any depth* on purpose, so a helper added later for
    something beneath the evidence directory inherits this arm rather than
    falling back to a cure naming ``theurian init``. **Whoever adds that helper
    revisits this cure in the same change**: the link would then be able to sit
    at an interior component, and a single ``rm .theurian/review`` would name the
    wrong object.

    **The path is rendered without a trailing slash**, for the outcome
    :func:`derived_escape_remedy`'s own measured table records: BSD ``rm -rf``
    on a trailing-slash symlink follows the link, destroys the target and leaves
    the link standing. The slashed spelling is not printed here at all, not even
    inside the warning against it, so a reader cannot copy it out of the cure.

    **Relative names only, enforced in the cure rather than at the seam.** A
    ``ProjectError``'s remedy crosses the MCP boundary unmodified --
    ``mcp/tools.py``'s ``_with_remedy`` replaces the *message* with
    ``PATH_ESCAPE_REFUSAL`` and republishes the *remedy* -- and that fold is
    generic over the exception, not over which helper raised. No registered tool
    resolves :attr:`ProjectPaths.review` today: ``git grep -nE
    'paths\\.review($|[^_])' -- packages/theurian-core/src/theurian/mcp/``
    printed nothing on 2026-09-12. Both halves of that pattern are load-bearing,
    and the key this replaces -- ``paths\\.review\\b``, in the house
    ``git grep -E`` idiom -- had neither. ``\\b`` is not a POSIX ERE construct:
    ``git grep -cE 'REVIEW_SEARCH_STORE_ID\\b' --
    packages/theurian-core/src/theurian/mcp/`` printed nothing on the same day,
    while the identical pattern without it answered 4, so the old key was silent
    whether or not a tool resolved this path. ``[^_]`` is what keeps
    ``paths.review_search_for``, which ``mcp/tools.py`` does call, from answering
    as a hit, and the ``$`` alternative is what keeps an occurrence at end of
    line from being missed -- measured by planting both shapes in a scratch file
    under that pathspec and running the key with ``--untracked``, where
    ``paths\\.review[^_]`` alone found the mid-line one and not the other. The
    discipline therefore lives here, where a later tool inherits it,
    rather than in a seam a later tool would bypass: this interpolates the
    knowledge directory's *basename* and this module's own literal, and nothing
    that came out of a ``resolve()`` (GHSA-923w-f36f-jcfq).
    """
    path = f"{knowledge_directory_name}/{_REVIEW_SUBDIRECTORY}"
    return (
        f"Inspect `{path}` with `ls -l {path}` -- a clone may have delivered it as a "
        f"symbolic link pointing outside the working tree. Remove that link with "
        f"`rm {path}`, written without a trailing slash: with one, `rm -rf` follows the "
        f"link and deletes what it points at while leaving the link in place. Plain "
        f"`rm` removes the link and touches nothing at its target. Nothing has to be "
        f"recreated by hand afterwards -- the evidence store makes the directory again "
        f"at the next `theurian review ingest`, and until then an absent directory is "
        f"read as an empty corpus rather than as a fault, so `theurian review build` "
        f"still answers."
    )


class RegistryFailureArm(StrEnum):
    """Which whole-registry failure :func:`registry_deletion_remedy` is writing for.

    The arm is passed in rather than inferred, because the thing that decides it
    -- the condition the raise sits in -- is knowable only at the raise. Naming
    it makes each caller state the condition its reader is standing in, and a
    fifth member added here has to be given a lead of its own rather than
    inheriting whichever branch a fallback happened to be: the dispatch in
    :func:`_registry_failure_lead` ends in a wildcard that accepts only
    ``Never``, so the type checker refuses the module until the new member is
    answered. Measured rather than assumed: adding a ``FIFTH_MEMBER`` to this
    class and running ``uv run mypy`` refuses
    ``application/project_service.py`` at that wildcard with ``Argument 1 to
    "_refuse_an_unclassified_arm" has incompatible type
    "Literal[RegistryFailureArm.FIFTH_MEMBER]"; expected "Never"  [arg-type]``.

    **The condition, not the ``except`` clause the raise sits under.** Two of
    these arms are about a *mode*, and an ``OSError`` says only where the call
    failed, never why: :func:`_arm_for_a_refused_registry` is what turns the
    ``errno`` into the arm, so that a reader whose registry path holds a
    directory is not told to ``chmod`` it.
    """

    #: The file was read and its top level is not a mapping of ids: undecodable
    #: bytes, JSON that will not parse, or JSON that parses to something else.
    #: The bytes are in front of the reader, so inspection can lead.
    UNPARSABLE = "unparsable"

    #: The read of the file itself was refused for ``EACCES`` -- a registry at
    #: mode ``000``. Nothing can be read out of it until that is undone, so the
    #: ``chmod`` leads and the inspection follows it.
    #:
    #: **``EACCES``, not "the read raised ``OSError``".** The arm's lead states a
    #: cause, and that cause is true only of a refused mode. An ``EISDIR`` -- a
    #: directory sitting where the registry file belongs, measured -- reaches the
    #: same ``except`` with no mode bit to restore, as would an ``EIO`` or an
    #: ``ESTALE`` from the storage under it, and ``chmod u+r`` would send every
    #: one of those readers to correct a permission that was never the problem:
    #: the "remedy that names a non-cause" defect
    #: :func:`~theurian.cli.commands._state_probe_remedy` records one layer up.
    #: Those errnos take :attr:`UNKNOWN`; :func:`_arm_for_a_refused_registry` is
    #: the split.
    FILE_UNREADABLE = "file-unreadable"

    #: The ``.exists()`` probe was refused for ``EACCES``: this process could not
    #: *reach* the file. Deletion is blocked with it, so the cure cannot open with
    #: either reading *or* removing the file -- and its ``chmod`` has to grant what
    #: the deletion needs, which is write and search on the directory rather than
    #: read on the file. The same ``EACCES`` split as :attr:`FILE_UNREADABLE`: an
    #: ``ENAMETOOLONG`` reaching the probe has no directory mode to restore either.
    #:
    #: **Two members, and this said "one level up" while covering both.** The
    #: refusal is at the data directory *or* at any directory above it, and which
    #: one is not knowable here. Measured on macOS at ``b9e8296b``, once with the
    #: data directory at ``000`` and once with an ancestor at ``000`` and the data
    #: directory at ``0700``: both reach this arm, and the ``OSError`` is the same
    #: shape both times -- ``errno`` 13, ``filename`` the whole registry path,
    #: ``filename2`` ``None``, ``strerror`` ``'Permission denied'``. The kernel
    #: names no component, so no code here can name the refuser and the cure must
    #: be true of both members. That is why its cause reads "could not reach"
    #: rather than "could not look inside that directory", which was false for the
    #: ancestor member, and why its steps run top-down: ``chmod u+rwx`` on the data
    #: directory -- the shipped text's *first* command -- exits 1 with ``Permission
    #: denied`` while a directory above it denies the search (measured), so the
    #: ancestors' ``u+rx`` has to come first.
    #:
    #: **The cure locates the culprit by ``cd``, not by ``ls``, and that is a
    #: portability decision.** What the probe needs from every component is the
    #: *search* bit, which is exactly what ``cd`` tests. Measured on macOS at
    #: ``b9e8296b`` over six ancestor modes with the data directory at ``0700``:
    #: ``0000``, ``0400`` and ``0600`` reach this arm and each refuses ``cd``
    #: (exit 1); ``0100``, ``0500`` and ``0700`` reach it not at all and each
    #: accepts ``cd`` -- a locator with no false negative and no false positive.
    #: ``ls`` happened to agree on that platform, but its exit status for a
    #: read-without-search directory is an implementation's choice rather than a
    #: POSIX one, and there is no Linux machine here to measure it on, so keying
    #: the instruction on it would publish an unmeasured claim.
    DIRECTORY_UNREADABLE = "directory-unreadable"

    #: The arm for a reader whose condition this module cannot name, and there
    #: are two ways to become one. The CLI's two whole-registry surfaces pass it
    #: as ``_context_remedy``'s ``default`` -- ``project list`` and
    #: ``_RegistryRead.failure_fields`` -- where it renders only for a registry
    #: error carrying no ``remedy`` of its own, which no refusal below raises
    #: today: an error those surfaces have never heard of, which could have come
    #: from a registry that opens perfectly or from one at mode ``000``. The two
    #: ``OSError`` raises below reach it through
    #: :func:`_arm_for_a_refused_registry`, which hands this arm back for any
    #: errno that is not ``EACCES``.
    #:
    #: **So the lead prescribes nothing**, and that is what the second
    #: population changed. While this arm rendered only for the CLI ``default``
    #: it could offer the ``chmod`` conditionally -- "if the file or its
    #: directory refuses to open" -- because either half might be the reader's
    #: case. Now that the errno split routes every non-``EACCES`` refusal here,
    #: that antecedent is *true* for an ``EISDIR`` reader while the ``chmod``
    #: cures nothing they have: a stated-false cause wearing a conditional,
    #: which is the same defect the split was written to end -- and the
    #: arrivals a ``chmod`` does cure are the ones the split sends to the two
    #: arms above. The lead therefore prescribes nothing. It invites the read
    #: and sends the reader to the message this cure travels with, which is
    #: where the cause is named: both ``default`` sites publish that message
    #: beside the cure (``error`` at ``project list``, ``reason`` or
    #: ``registryReason`` at ``project status``), and both raises below put the
    #: errno's own words in it.
    UNKNOWN = "unknown"


#: What deleting the registry actually removes, stated beside the offer rather
#: than at the seam that chooses it (PR #596's family, met again at this one).
#: Two things go, and neither is recoverable from any project's own
#: ``.theurian/``: every *other* project's registration, since the deletion is
#: not scoped to the one the reader came here about, and each entry's
#: ``registeredAt``, which :meth:`ProjectRegistry.register` preserves from the
#: existing entry and never recomputes -- so re-registering restamps it with
#: today's date rather than restoring it.
_WHAT_DELETING_THE_REGISTRY_COSTS: Final = (
    "The file records every project you have registered, so deleting it unregisters all "
    "of them, not only this one, and re-registering stamps today's date over each entry's "
    "original registeredAt."
)

#: The way back, and the half of this cure that has to survive every rewrite: a
#: reader told to delete a file and given no invocation has no way back. Pinned
#: by name across the suite, under two spellings, so the key that enumerates the
#: pins is both of them::
#:
#:     git grep -n -e "re-register each project" -e RE_REGISTER_INVOCATION \
#:         packages/theurian-core/tests
#:
#: The literal alone misses the assertions that read the tests' own constant
#: instead of spelling it.
#:
#: **The ids are what recovery has to preserve, and the text used to say "roots"
#: and name the one invocation that cannot preserve them.** An id is not stored
#: anywhere else: :func:`derive_project_id` slugs the *directory name*, and the
#: registry is what overrides it. So deleting the file frees every id, and a bare
#: re-registration silently re-derives each one. Measured through the real CLI
#: against a redirected ``HOME`` and ``THEURIAN_DATA_DIR`` on 2026-09-09 at
#: ``b9e8296b``: a project in a directory named ``gamma`` registered as
#: ``gamma-prod``, its ``projects.json`` removed, then ``theurian project
#: register`` -- exit 0, and the entry came back keyed ``gamma``. With two
#: checkouts both named ``api``, re-registering ``two/api`` first took the id
#: ``api`` at exit 0 and ``one/api`` was then refused at exit 1: the id had been
#: re-pointed at a different repository, which is the SEC-13 shape
#: :meth:`ProjectRegistry.register` refuses to *do* -- it has nothing to refuse
#: against once the file is gone.
#:
#: **What ``--project-id`` restores, field by field**, measured the same way and
#: the same day. The field set is :meth:`ProjectRegistry.register`'s own ``entry``
#: literal plus the key it is stored under -- re-derive it there rather than
#: trusting this list, because a field added to that dict is a field this text
#: silently stops accounting for:
#:
#: ==================  =========================================================
#: field               ``register --project-id <id>`` run inside ``rootPath``
#: ==================  =========================================================
#: ``projectId``       restored -- by the flag, and by nothing else
#: ``rootPath``        restored -- by where the command is run
#: ``registeredAt``    restamped with today (named in the cost sentence above)
#: ``repositoryUrl``   re-read from ``git remote get-url origin``: measured
#:                     ``https://...`` -> ``""`` with the remote removed
#: ``defaultBranch``   re-read from ``git symbolic-ref --short HEAD``: measured
#:                     ``develop`` -> ``hotfix`` from another branch, and ->
#:                     ``main`` on a detached HEAD
#: ``knowledgeDirectory``  rewritten as ``DEFAULT_KNOWLEDGE_DIRECTORY``: measured
#:                     ``.knowledge-custom`` -> ``.theurian``
#: ==================  =========================================================
#:
#: The last three are the "other three fields" the text names. They are re-read
#: from the tree rather than restored from the record, so they come back changed
#: wherever the tree has moved -- which is why the text says they are not
#: restored rather than promising they are the same.
_HOW_TO_RECOVER_FROM_THE_DELETION: Final = (
    "Deleting it also frees every id: `theurian project register` with no `--project-id` "
    "derives the id from the directory name, so a project registered under any other id comes "
    "back under a different one, and two checkouts whose directories share a name compete for "
    "a single id -- whichever re-registers first takes it. So read out every entry's projectId "
    "(the key it sits under) and its rootPath first; then delete it and re-register each "
    "project with `theurian project register`, passing `--project-id <its projectId>` and "
    "running it inside that rootPath. The other three fields are not restored from what you "
    "deleted: repositoryUrl and defaultBranch are re-read from Git as the tree stands then, "
    "and knowledgeDirectory comes back as the default."
)


def _refuse_an_unclassified_arm(arm: Never) -> NoReturn:
    """Refuse a value that is not a :class:`RegistryFailureArm` member.

    Typed ``Never`` so it doubles as the exhaustiveness check
    :class:`RegistryFailureArm`'s docstring measures: a fifth member reaching
    the wildcard is a fifth member mypy refuses to pass here. What it adds at
    *runtime* is the half mypy cannot cover -- ``RegistryFailureArm`` is a
    :class:`~enum.StrEnum`, so an untyped caller can hand
    :func:`registry_deletion_remedy` a bare string, a member's value routes on
    the value patterns exactly as the member does, and anything else used to
    fall out of the ``match`` with no case taken. That returned ``None``, which
    the caller's f-string rendered as the literal word ``None`` in front of a
    cure offering to delete the reader's registry.
    """
    raise ValueError(
        f"{arm!r} is not a RegistryFailureArm, so there is no cure written for the condition "
        f"it names. Pass a member of RegistryFailureArm -- `registry_deletion_remedy` renders "
        f"one lead per member and has none for anything else."
    )


def _registry_failure_lead(path: Path, arm: RegistryFailureArm) -> str:
    """The first sentence of the cure, which is the one that differs per arm.

    Everything after it is shared, because the cost and the recovery do not
    depend on how the reader got here. What does depend on it is whether they
    can act on the file at all -- and the shipped text assumed they could, for
    all four arrivals at once: "Inspect {path} ... Once you have read the roots
    you need out of it" was published verbatim over a registry at mode ``000``
    whose own message says it "cannot be opened". A cure that opens by telling
    the reader to read what the payload beside it says is unreadable is not a
    remedy; it is the message contradicting itself.

    **Each ``chmod`` grants what the sentences around it promise**, which is not
    the same mode on both unreadable arms. Read on the file is what makes the
    inspection possible; write *and* search on the directory are what make the
    ``rm`` in the shared tail possible, and read on the file buys nothing there.
    Measured over a data directory at mode ``000``, running the arm's own
    instruction and then the cure's own tail -- ``chmod <mode> <dir>``, ``cat
    <registry>``, ``rm <registry>``: at ``u+rx`` the ``chmod`` and the ``cat``
    exited 0 and the ``rm`` exited 1 with ``Permission denied``, leaving the
    reader at exactly the refusal the arm was written to lift; at ``u+rwx`` all
    three exited 0.

    **And the directory arm's steps are ordered, because one of its two members
    cannot run them in the other order.** That arm is reached both by a data
    directory at ``000`` and by an *ancestor* at ``000``
    (:attr:`RegistryFailureArm.DIRECTORY_UNREADABLE` records the measurement that
    they are indistinguishable at the raise). Measured in both, at ``b9e8296b``:
    ``chmod u+rwx <data dir>`` -- the shipped text's first command -- exited 0 in
    the first and 1 with ``Permission denied`` in the second, so the arm's own
    opening step was unrunnable for half its readers. Top-down, ``chmod u+rx
    <ancestor>`` then ``chmod u+rwx <data dir>``, exited 0 in both, and the
    ``cat`` and ``rm`` after it exited 0 in both.
    """
    match arm:
        case RegistryFailureArm.UNPARSABLE:
            # "ids and roots", not "roots": the tail below now asks the reader to
            # carry the ids across too, and this is the sentence that says the
            # inspection is possible at all. Promising less than the instruction
            # needs is how the tail's own "read out the roots" came to name half
            # of what recovery preserves.
            return (
                f"Inspect {path} before removing it -- the ids and roots it lists are legible "
                f"by eye even where its top level is not something this build can read."
            )
        case RegistryFailureArm.FILE_UNREADABLE:
            return (
                f"Restore read access to {path} first -- `chmod u+r` on it -- because this "
                f"process could not open the file, so nothing in it can be read before it is "
                f"destroyed. Then inspect it."
            )
        case RegistryFailureArm.DIRECTORY_UNREADABLE:
            return (
                f"Restore access to {path.parent} first, working from the top down: `chmod "
                f"u+rx` on each directory above it you cannot `cd` into, then `chmod u+rwx` on "
                f"{path.parent} itself. This process could not reach {path} -- the refusal is "
                f"at {path.parent} or at a directory above it, and it names the whole path "
                f"rather than the component that denied it. The order matters: `chmod` on "
                f"{path.parent} is itself refused while a directory above it denies the "
                f"search. Until the refusal is lifted, {path} can be neither read nor deleted, "
                f"and removing the file needs the write bit on {path.parent} as well as the "
                f"search bit, so `u+rx` alone would restore the read and leave the deletion "
                f"below refused. Then inspect it."
            )
        case RegistryFailureArm.UNKNOWN:
            return (
                f"Read {path} before removing it. What refused it is named in the message "
                f"beside this remedy rather than here, so read that first: it is not always a "
                f"permission, and neither a directory sitting where the file belongs nor a "
                f"path the filesystem will not accept is cured by a mode change."
            )
        case _:
            _refuse_an_unclassified_arm(arm)


def registry_deletion_remedy(path: Path, arm: RegistryFailureArm) -> str:
    """The remedy for a registry file whose *set of ids* cannot be trusted.

    **Three causes, four arms**, which is what the sentence here used to get
    wrong: it said "reached only when the top level of the file is not what every
    reader here assumes -- unparsable JSON, or JSON that is not an object",
    naming two of the three while the two ``OSError`` arms carried this very
    text, and contradicting :meth:`ProjectRegistry._raw_entries`' own docstring
    one screen away. The three are: the file cannot be opened at all, its bytes
    do not decode or parse as JSON, or its top level is not an object. Each
    leaves this module with no dict of ids to iterate and so no way to say which
    registrations are fine and which are not. The first splits at the reader's
    end -- the file's own mode, or the data directory's one level up -- and that
    is the fourth arm, because only one of them also blocks the deletion. It
    splits once more into no arm of its own: an ``open`` refused for anything but
    a mode leaves the reader with nothing to ``chmod``, and takes the
    :attr:`RegistryFailureArm.UNKNOWN` arm, which prescribes nothing and points
    at the message instead (:func:`_arm_for_a_refused_registry`). A malformed
    *entry* is a narrower
    problem with its own, narrower remedy: see :meth:`ProjectRegistry.load`,
    :meth:`ProjectRegistry.ids_for_root` and :meth:`ProjectRegistry.register`.

    One cure with one per-arm lead, rather than one text per layer. It used to be
    two: this function's private predecessor, carried by the four raises in
    :meth:`ProjectRegistry._raw_entries`, and a near-duplicate in
    ``cli/commands.py`` written as ``_context_remedy``'s ``default`` at the two
    surfaces that read the whole registry. Two spellings of one destructive offer
    in two layers is the drift PR #596 watched reach four faces, and the fix that
    stuck there is this one: the claim lives inside the cure, and a caller that
    never heard of the split inherits it.

    **The deletion is offered with its cost rather than as a free action**
    (issue #381). This text used to close with "it is derived and holds nothing
    that is not also recoverable from each project's own .theurian/" -- a
    costless-removal claim over the file that *is* the enumeration of the
    registrations. A removal is honestly called free only over something holding
    no bytes and no names; this file holds both, so
    :data:`_WHAT_DELETING_THE_REGISTRY_COSTS` names the loss instead, on every
    arm.

    **The lead is what the arm decides**, and it is the half that was wrong:
    inspection can only come first where the reader can inspect. See
    :func:`_registry_failure_lead`.

    Raises:
        ValueError: If ``arm`` is not a :class:`RegistryFailureArm` member. The
            annotation says it cannot be, and an untyped caller is what the
            annotation does not reach: because the enum is a
            :class:`~enum.StrEnum`, a bare string carrying a member's *value*
            routes exactly as the member does, while any other string used to
            take no case at all and hand this f-string a ``None`` to render --
            the word ``None``, printed in front of an offer to delete the
            reader's registry. :func:`_refuse_an_unclassified_arm` is where that
            now stops.
    """
    return (
        f"{_registry_failure_lead(path, arm)} "
        f"{_WHAT_DELETING_THE_REGISTRY_COSTS} "
        f"{_HOW_TO_RECOVER_FROM_THE_DELETION}"
    )


def _arm_for_a_refused_registry(
    exc: OSError, *, when_refused: RegistryFailureArm
) -> RegistryFailureArm:
    """Which arm an ``OSError`` earns -- decided by its ``errno``.

    The two ``except OSError`` clauses in :meth:`ProjectRegistry._raw_entries`
    know *where* the call failed, and each used to publish that as *why*:
    whatever the kernel refused for, the reader was handed the arm written for a
    refused mode. Measured through ``ProjectRegistry.load`` at ``2d1f60c2``, a
    *directory* at the registry path answered ``cannot be opened: [Errno 21] Is
    a directory`` beside "Restore read access ... ``chmod u+r`` on it", and an
    ``ENAMETOOLONG`` at the ``.exists()`` probe took the data directory's
    ``chmod`` story the same way. Neither reader has a mode bit to restore, so
    each was told to correct a permission that was never their problem.

    So ``EACCES`` -- the errno a refused mode actually produces, measured at both
    of the mode-``000`` conditions ``tests/unit/test_project_registry_errors.py``
    drives -- takes the arm written for it, and every other errno takes
    :attr:`RegistryFailureArm.UNKNOWN`, whose lead prescribes no cure at all and
    sends the reader to the message this cure travels with. That second half is
    not decoration: routing an errno to an arm that would still have named a
    ``chmod`` -- even conditionally -- would move the false cause rather than
    remove it, which is why the split and the ``UNKNOWN`` lead changed together.
    ``errno`` is the key :func:`~theurian.cli.commands._state_probe_remedy` uses
    one layer up for the same choice, and this is the narrower set of the two:
    that seam's sibling in ``proposal_service`` pairs ``EACCES`` with ``EPERM``,
    which is not the mode refusal these two leads describe.

    ``exc.errno`` is ``None`` for an ``OSError`` that never reached the kernel;
    that is not ``EACCES`` either, so it takes ``UNKNOWN`` with the rest.
    """
    return when_refused if exc.errno == errno.EACCES else RegistryFailureArm.UNKNOWN


def entry_root(entry: object) -> Path | None:
    """The absolute root a raw registry entry names, or ``None`` if it names none.

    One predicate in one place, because readers that partition the same file on
    it must never disagree: :meth:`ProjectRegistry.load` keeps the entries that
    pass and :meth:`ProjectRegistry.unreadable_ids` reports the ids that do not,
    so a second copy of this test would eventually admit an entry ``load`` skips
    -- or skip one it admits -- and root resolution would go back to guessing at
    the difference.

    Six call sites in five readers as of ``67a781d``: those two,
    :meth:`ids_for_root` twice -- once for the rootless refusal, once for the
    match set its second refusal filters -- :meth:`register`, and, outside this
    module and the reason this is public rather than module-private,
    ``_RegistryRead.holds_root`` in ``cli/commands.py``, where ``theurian
    project status`` decides whether the registry holds *this* root. Re-count
    rather than trusting the number; it rots on the next caller::

        git grep -nE 'entry_root\\(' -- packages/theurian-core/src \\
            | grep -v 'def entry_root'

    The escaped parenthesis is load-bearing: it keeps this very line out of the
    result, which an unescaped pattern counts as a seventh site.

    **The property under all of it is that the answer must not depend on where
    the command was run.** An entry naming a root is a claim about a directory;
    an entry whose ``rootPath`` resolves to the *caller's* directory is a claim
    about whoever asks, and one such entry answers for every repository on the
    machine at once. Two spellings reach that, and both are refused before
    ``resolve()`` -- the call that would otherwise supply the missing half from
    the working directory rather than refusing:

    *Not absolute.* ``Path("").resolve()`` is the calling process's working
    directory, and so are ``Path(".")``, ``Path("./")``, ``Path("demo/../.")``
    and plain ``Path("demo/sub")``, none of which the old blank-string test
    caught. Measured against a registry hand-edited to ``"rootPath": "."``, two
    unrelated repositories both reported ``registered: true`` under that single
    entry's id -- :meth:`id_for_root`'s misrouting, arriving through the file
    rather than through the directory-name fallback.

    *Absolute, and still the caller's directory.* On Linux ``/proc/self/cwd`` is
    a symlink to exactly that, so it passes an absolute-spelling test and lands
    on the same defect; measured in a Linux container, one such entry made every
    repository report ``registered: true`` under its id. The whole
    ``/proc/self`` family is per-process this way and macOS has no member of it,
    which is why this is keyed on the *spelling* rather than probed: a guard
    that only fires on the platform nobody develops on is a guard nobody's tests
    reach. The first component of the lexically normalised path is what is
    tested, so ``//proc/self/cwd`` and ``/tmp/../proc/self/cwd`` are refused
    too, while ``/procession`` is not.

    That guard is lexical and therefore bounded: a symlink on disk pointing into
    ``/proc`` would still resolve there, and no test of the string can see it.
    Deciding that would mean resolving first, which is the operation that
    produces the cwd-valued answer in the first place. The threat this predicate
    is sized for is a hand edit of the registry file, and against a hand edit the
    spelling is the whole attack surface.

    Nothing legitimate is refused by either: ``register`` writes
    ``str(context.paths.root)``, :class:`Project` rejects a ``root_path`` that is
    not absolute at construction, and a Git working tree does not live under
    ``/proc``.

    **Resolved here rather than by each caller, which is the third way an entry
    can name no root.** ``Path.resolve`` raises ``ValueError`` on an embedded NUL
    and ``OSError`` on a name the platform rejects, and neither is a
    ``TheurianError``: a hand edit putting ``"/tmp/\\x00nul"`` in one entry's
    ``rootPath`` reached ``theurian migrate status`` as a Rich traceback with an
    empty stdout, from a *different* project that had done nothing wrong. The
    same string then reached ``ProjectPaths.of`` through the MCP surface, where
    ``ProjectPaths.index_for`` had already converted this exact pair of
    exceptions for the index pointer and nothing had for the registry. An entry
    the OS will not turn into a path names no root, so it is unreadable for the
    same reason a missing ``rootPath`` is, and it is reported the same way.
    """
    if not isinstance(entry, dict):
        return None
    root_path = entry.get("rootPath")
    if not isinstance(root_path, str):
        return None
    candidate = Path(root_path)
    # `""` and `"   "` need no test of their own: neither is absolute.
    if not candidate.is_absolute():
        return None
    # Normalised first, so the component test cannot be walked around with a
    # `..` or a second leading slash. `posixpath` rather than `os.path` because
    # the stored form is a POSIX absolute path on every platform.
    normalised = PurePosixPath(posixpath.normpath(root_path))
    if normalised.parts[1:2] == ("proc",):
        return None
    try:
        return candidate.resolve()
    except (ValueError, OSError):
        return None


def _usable_id(project_id: str) -> bool:
    """Whether a registry *key* is an id a consumer can actually be handed.

    Keys went unvalidated because nothing writes them but ``register``, which
    only ever writes a :class:`ProjectId`. The file is hand-editable, though,
    which is the premise ``unreadable`` exists for -- and a key that is not a
    slug is not a cosmetic defect. Measured against a registry hand-edited to
    hold ``"Team One/API"``: ``project.list`` published it as a project, every
    project-scoped tool refused it with ``ProjectId must be lowercase
    kebab-case``, and ``theurian project unregister 'Team One/API'`` refused it
    too -- pointing the user back at the listing that had printed it.

    It did not stop at that id. The entry named a valid ``rootPath``, so it was
    not unreadable by the test above, and :meth:`ids_for_root` therefore reported
    *two* ids for one root: every command that resolves a project from the
    working directory refused, in a repository whose own registration was intact.

    So the key is checked by the same construction every consumer performs, and
    a key that fails it is unreadable rather than published. That is what makes
    :meth:`load`'s result true to its one published promise: an id it returns is
    an id every project-scoped surface will accept.
    """
    try:
        ProjectId(project_id)
    except InvalidIdentifierError:
        return False
    return True


def _unreadable_ids(entries: Mapping[str, object]) -> tuple[str, ...]:
    """The ids whose entries are not usable registrations, sorted.

    Two ways to fail and both land here, because both make an entry something no
    surface can serve: the entry names no root (:func:`entry_root`), or its key
    is not an id anything accepts (:func:`_usable_id`). ``theurian project
    unregister`` is the cure for either, and it is the only cure either has.

    Sorted because this reaches both an error message and a command the user
    retypes from it; ids in JSON-file order would read differently on two
    machines holding the same registry.
    """
    return tuple(
        sorted(
            pid
            for pid, entry in entries.items()
            if entry_root(entry) is None or not _usable_id(pid)
        )
    )


def unregister_commands(project_ids: tuple[str, ...]) -> str:
    """``theurian project unregister`` for each id, as words a reader can type.

    An unreadable id is whatever a hand edit left behind, spaces and quotes
    included, and this string is a command a user copies. Unquoted,
    ``theurian project unregister Team One/API`` is three arguments to a command
    that takes one -- so the remedy for the id that broke the registry was itself
    unrunnable, on every surface that printed it.

    **``shlex.quote`` buys one shell word; it does not buy one positional
    argument, and ``--`` is what does** (PR #626 round one). A key of ``--help``
    is already a safe word, so it quotes to itself and this rendered
    ``theurian project unregister --help`` -- which prints usage at exit 0 and
    leaves the entry exactly where it was, while ``--json`` and ``-x`` exit 2.
    Measured against the real CLI over a planted registry: followed verbatim the
    old text left the key in the file, and
    ``theurian project unregister -- --help`` answered ``removed: True`` with the
    key gone. The marker is rendered here rather than at each call site for the
    reason the quoting is -- one renderer is what stops one surface forgetting
    what the others remember.

    ``--`` ends option parsing for everything after it, so a reader who wants the
    machine channel puts the flag *before* it: ``theurian project unregister
    --json -- --help`` answers a document, while the same flag written after the
    id is a second positional and exits 2. Measured in the same run.

    Public, and imported by
    ``cli/commands.py::_RegistryRead.unreadable_entry_fields`` for that reason
    (#384): ``project status``'s resolved branch names the same entries this
    module's refusals do, and a second ``shlex.quote`` spelled at the CLI layer
    is how one of the two comes to forget the quoting the other remembers. The
    registry cure next door (:func:`registry_deletion_remedy`) already crosses
    the boundary this way.

    **A backtick inside an id breaks the code span around the invocation, and
    that is recorded rather than escaped.** ``shlex.quote`` single-quotes such a
    key correctly, so the shell word is right; the delimiters wrapped around it
    here are backticks too, so an id of ``x`id`y`` renders as::

        `theurian project unregister -- 'x`id`y'`

    and whatever reads the text between the first pair of backticks -- a
    Markdown renderer, or the suite's own span regex -- gets half a command.
    Widening the fence per id would make the delimiters of a string that reaches
    Markdown, JSON and a bare terminal alike depend on the registry's contents,
    which is a worse trade than saying so: such an id is still removable by hand
    from the ``unreadable`` list ``theurian project list`` prints. What is pinned
    instead is the runnable class -- one invocation per id, and a reader who
    follows it recovers.
    """
    return ", ".join(f"`theurian project unregister -- {shlex.quote(pid)}`" for pid in project_ids)


class ProjectError(TheurianError):
    """A project could not be registered, resolved, or initialised.

    ``remedy`` carries the command that fixes it, separately from the message
    that says what was refused. A CLI reporting the failure must not have to
    infer the cure from the exception's type: ``resolve_context`` alone can fail
    because there is no Git repository, because the registry is ambiguous, or
    because a migration does not validate, and one fixed remedy for all three
    sends two thirds of its readers to look in the wrong place.
    """

    def __init__(self, message: str, *, remedy: str = "") -> None:
        self.remedy = remedy
        super().__init__(message)


class ProjectPathEscapeError(ProjectError):
    """A path under ``.theurian/`` could not be proved to stay inside the tree.

    The refusal :func:`_contain` and :meth:`ProjectPaths.of` raise, given a name
    so the CLI can grade it without asking *which* guard raised (#525, #550).
    Reproduce the population with
    ``git grep -n 'raise ProjectPathEscapeError' -- packages/theurian-core/src``.
    Run 2026-09-07 it returned five lines: this sentence quoting the key, two
    ``raise`` statements inside :func:`_contain`, and two inside
    :meth:`ProjectPaths.of`. The self-hit is why the count is stated rather than
    the command's output pasted -- the claimant reads itself, the trap
    ``test_connection_claims.py::test_the_only_test_that_constructs_the_write_lock_runs_in_one_process``
    records for its own key.

    One root cause -- a working tree carrying a symbolic link force-added past
    ADR-0004's ignore -- was answered exit 0, exit 1 and exit 4 by different
    handlers, decided by whichever ``ProjectPaths`` helper resolved first rather
    than by what was wrong; the sweep in
    ``tests/integration/test_contained_path_envelope.py`` measured which, at
    ``491bded6``. The handlers narrowed to ``ProjectError`` or to
    ``TheurianError``, so none could tell this apart from an ``active.json``
    holding four bytes of text -- a *different* root cause, derived state to
    delete, whose exit 1 and whose ``project status`` degradation are correct and
    had to survive the unification.

    **Not :class:`~theurian.domain.errors.PathEscapeError`, which is a different
    class with a different subject.** That one is the migration loader's and the
    proposal service's, over a ``contentFile`` an author chose: its whole message
    discipline exists because ``requested`` is attacker-controlled text (SEC-7),
    and its remedy names no file to delete because the culprit can sit anywhere
    on the resolution chain. This one is over a path *Theurian itself* built from
    ``knowledge_dir`` and constant child names, so the path is safe to print and
    the cure is keyed on it (:meth:`ProjectPaths._escape_remedy`).

    Carries no fields of its own: the message and the remedy :class:`ProjectError`
    already holds are the whole payload, and the type is what the CLI grades on.

    **The join check in :meth:`ProjectPaths.of` raises it too, since #550**, and
    that is the second half of one grading rather than a widening. It guards
    ``knowledge_dir`` itself -- ``.theurian`` shipped as a symbolic link to
    somewhere outside the tree -- with its own anchor, and it fires while the
    command context is still resolving, before a single helper derives a path.
    While it raised the bare base class its refusal fell past every handler keyed
    on the escape into whatever each caller assigns to "could not resolve a
    project", so *which level of the tree the delivered link sat at* decided the
    exit code. Its callers are wide: ``git grep -n 'ProjectPaths.of(' --
    packages/theurian-core/src`` returned ten lines on 2026-09-07, eight of them
    calls, reaching ``setup_steps``, ``setup_commands``, ``migration_pipeline``
    and ``mcp/tools`` as well as ``cli/context``. That population is derived from
    the source and classified -- graded, or absorbed into a diagnostic verdict --
    by ``test_escaping_knowledge_dir_grading.py``, which drives each graded member
    and fails when a new call site appears unclassified.
    """


class GitignoreIsASymbolicLinkError(ProjectError):
    """``.gitignore`` is a symbolic link, so nothing was read or written through it.

    A type rather than a message, because the two callers of
    :func:`ensure_gitignore` word their own failure and cannot tell this apart
    from the marker refusal beside it otherwise. ``propose --local``'s existing
    text says the file "has a Theurian block that cannot be rewritten safely",
    which is a claim about the block and is false of a link -- and sniffing the
    message string for the difference is the kind of coupling
    :class:`ProjectPathEscapeError` exists to avoid one class over.

    **A different root cause from that class, and from #569's.** Containment
    refuses a path that leaves the tree; ``.gitignore`` is at the root and leaves
    nothing. ``no_follow``'s remedy speaks for a *derived* path a rebuild
    replaces; this file is authored, Git-tracked content (the #237 authored
    class), so it carries :data:`GITIGNORE_LINK_REMEDY` instead.

    Carries no fields of its own: the message and :data:`GITIGNORE_LINK_REMEDY`
    that :class:`ProjectError` already holds are the whole payload.
    """


def _contain(root: Path, path: Path, *, remedy: str = KNOWLEDGE_DIR_ESCAPE_REMEDY) -> Path:
    """Prove ``path`` stays inside ``root``, or refuse with the escape remedy.

    The one containment chokepoint under a project's ``.theurian``:
    :meth:`ProjectPaths._contained` routes every path the class hands out through
    here, and :func:`initialize_project` routes every directory and file it
    creates. A committed symbolic link at any level -- ``.theurian`` itself (also
    refused earlier, in :meth:`ProjectPaths.of`), a ``knowledge``/``state``
    directory a clone tracked, or a leaf a clone force-added past the ADR-0004
    ignore -- cannot redirect a read or write outside the working tree the clone
    gave the user (#237, T-5). That closes the *authored-symlink* class: a link
    delivered as tracked repository content.

    The **derived**-symlink class -- a link force-added past ADR-0004's ignore,
    the GHSA-266v derived-state-trust root cause -- reaches this same chokepoint
    through the helpers added for it: :attr:`ProjectPaths.ingestion_manifest`
    (#394) and the three ``*_temporary`` leaves the atomic publishers build
    (#523). Containment is only half of that one, and the half it is not covers
    the shape that does the damage in a user's own checkout: a link whose target
    is *inside* the tree resolves inside it and passes here, correctly. The write
    itself refuses that one, with ``O_NOFOLLOW``
    (:mod:`theurian.security.no_follow`).

    Anchored to ``root``, never to a nearer ancestor. ``index_for`` compared a
    candidate to ``self.state.resolve()``, but when ``.theurian/state`` is itself
    an escaping symlink ``self.state.resolve()`` *is* the escaped location, so the
    check was trivially satisfied and a descendant symlink walked straight through
    it -- the root-join check in :meth:`ProjectPaths.of` misses it too, because
    ``.theurian`` there is an honest directory.

    ``root`` is re-resolved rather than trusted. Every ``ProjectPaths`` is built
    through :meth:`ProjectPaths.of`, which resolves it, so the re-resolve is a
    no-op there -- but a caller holding an *unresolved* root (a symlinked ``/tmp``
    on macOS, a repository under a symlinked home) would otherwise compare a
    resolved ``path`` against an unresolved ``root`` and refuse every legitimate
    access. Dropping it is pinned against by
    ``test_project_paths_containment.py``'s symlinked-root case.

    ``resolve`` (non-strict) is what handles the creation case correctly. A first
    write into a not-yet-created target has no inode of its own, but ``resolve``
    follows every *existing* symlink on the way down -- the ``state`` directory
    the write lands in -- and normalises only the missing tail, so a target under
    an escaping (or even dangling) symlinked parent still resolves outside and is
    refused before it is created (measured, for both an existing and a dangling
    parent). Walking up to the deepest existing ancestor instead was wrong in the
    other direction: when the whole project tree does not exist yet, that walk
    climbs *above* the root to its parent and refuses a path that is lexically
    inside an as-yet-uncreated ``.theurian``.

    A symbolic link is refused only when it *escapes*: a link whose resolved
    target is inside ``root`` is legitimate and passes untouched. A symlink *loop*
    inside the tree resolves lexically inside it and is not an escape; the
    operation that dereferences it fails downstream with the ``ELOOP`` its own
    error path already names.
    """
    resolved_root = root.resolve()
    try:
        resolved = path.resolve()
    except (OSError, ValueError) as exc:
        # An embedded NUL raises `ValueError`, a name the platform rejects raises
        # `OSError`; neither is a `TheurianError`, and callers only narrow to that
        # (the conversion `index_for` and `entry_root` make). Defensive parity
        # with those: every path reaching here is built from the already-validated
        # `knowledge_dir` and constant child names, so this arm is a contract
        # guarantee, not a branch real data drives.
        #
        # Graded as the escape it cannot rule out, not one grade softer: a path
        # whose containment could not be *decided* is a path no read or write may
        # be attempted through, and a caller reading the exit code has the same
        # thing to do about it.
        raise ProjectPathEscapeError(
            f"{path} does not resolve to a location inside {resolved_root}: {exc}",
            remedy=remedy,
        ) from exc
    if not resolved.is_relative_to(resolved_root):
        raise ProjectPathEscapeError(
            f"{path} resolves outside the project root {resolved_root}, so a read or write "
            f"through it would land outside the working tree.",
            remedy=remedy,
        )
    return path


@dataclass(frozen=True, slots=True)
class ProjectPaths:
    """Absolute paths derived from a project root.

    Centralised so no caller assembles a state path by string concatenation and
    quietly disagrees with another caller about where state lives.
    """

    root: Path
    knowledge_dir: Path

    def _contained(self, path: Path) -> Path:
        """Prove ``path`` cannot deliver a read or write outside the tree.

        Every filesystem path this class hands out routes through here, and so
        does every directory :func:`initialize_project` creates, so the
        containment is a property of one chokepoint (:func:`_contain`) rather than
        a check duplicated per helper -- a helper added later that forgets it is
        caught by ``tests/unit/test_project_paths_containment.py``'s reflection
        sweep. The mechanism, the anchoring, and the two classes that reach it --
        the authored-symlink #237/T-5 class, and the derived one #394 and #523
        route here through helpers of their own -- are recorded on
        :func:`_contain`, which also states the half containment does *not* cover.
        """
        return _contain(self.root, path, remedy=self._escape_remedy(path))

    def _escape_remedy(self, path: Path) -> str:
        """Which cure a refusal of ``path`` should publish (#483 round one, H-1).

        Keyed on the refused path rather than fixed per raise site, so the
        remedy is chosen once for every helper that routes through
        :meth:`_contained` -- including helpers added later, and including the
        call sites #525 widened the *handling* of. That issue re-keyed which
        refusals reach a caller as an envelope and unified their exit code; this
        decides what the envelope says, so the two compose rather than collide.

        Lexical, deliberately. ``path`` is the requested location, always built
        here as ``knowledge_dir / <child> / ...``, and reading its shape is a
        question about which artifact was asked for -- not about what is on
        disk, which is what the resolution inside :func:`_contain` is for. It
        cannot raise, and it answers the same before and after the escape it
        describes.

        ``review`` is carved out ahead of the derived check and answered by
        :func:`review_escape_remedy`, because the fallback told a reader to run
        ``theurian init`` and ``review`` is not in :data:`INITIAL_DIRECTORIES`
        (#602). The cures this method can return are read from its own ``return``
        arms rather than counted in prose -- the sentence this replaces opened
        "three cures now, not two", which is a number that goes stale silently.
        ``mcp/tools.py``'s ``PATH_ESCAPE_REFUSAL`` note carries the key that
        prints them (case-insensitively, so the constant answers beside the two
        functions) and records what each one interpolates; that note is where a
        fourth cure has to re-establish the claim it argues.

        The carve-out is keyed on the first component **at any depth**, so a
        later helper resolving something *beneath* the evidence directory
        inherits this arm rather than falling back. The arm is ready for that;
        :func:`review_escape_remedy`'s text is not, and says so -- it names
        ``rm .theurian/review``, which cures a link at ``review`` and nothing
        deeper.

        Everything else falls back to :data:`KNOWLEDGE_DIR_ESCAPE_REMEDY`, and
        that population is re-derived rather than transcribed -- the sentence
        this replaces had listed four members and omitted ``proposals-local``,
        which predates both this change and the one before it. The key is
        ``git grep -n 'self\\._contained(' --
        packages/theurian-core/src/theurian/application/project_service.py``,
        which printed 18 lines on 2026-09-12. **Eight** of them build a relative
        path of a single component; the other ten open with ``state``, ``cache``
        or ``runtime``, each a member of ``DERIVED_SUBDIRECTORIES``, so the
        predicate's other disjunct -- a first component outside it -- selects
        none of them today. **Seven** of the eight are the fallback's
        population: :attr:`knowledge`, :attr:`specifications`,
        :attr:`proposals`, :attr:`proposals_local`, :attr:`config`, and
        :attr:`state` and :attr:`runtime` asked for *as themselves* rather than
        as a parent. :attr:`review` was the eighth until this change. The key
        does not hit this sentence: the text above spells the call with a
        backslash, so it is not the string the pattern matches.

        **That last group survives only at the helper level, and an earlier note
        here described it as though a user could meet it.** Asking this class for
        ``state`` or ``runtime`` *itself* does still return the older text -- but
        planting an escaping ``.theurian/state`` and running the CLI does not
        produce it, because a helper resolving something *under* ``state``
        (``database_for``, ``active_pointer``) raises first and its refusal is
        what gets published. Measured, and the branch's own CLI tests assert it:
        ``test_apply_refuses_an_escaping_state_symlink_and_writes_nothing_outside_the_tree``
        and its ``status`` sibling both expect
        :func:`derived_escape_remedy`. So the CLI-reachable face of *that plant*
        is the derived remedy.

        **The older text is reachable, and an earlier version of this paragraph
        said it was not.** ``initialize_project`` calls :func:`_contain` directly
        for each directory it creates, passing
        :data:`KNOWLEDGE_DIR_ESCAPE_REMEDY`, so ``theurian init`` over an
        escaping ``.theurian/knowledge`` publishes it -- measured 2026-09-05
        against the real CLI. What is true is narrower: no *swept* command
        produces it for a plant under ``state`` or ``runtime``, because a helper
        resolving something beneath those raises first.

        Unifying the two texts was left where it is; the exit-code grading that
        differed beside them is #525's, and the sweep in
        ``tests/integration/test_contained_path_envelope.py`` measures that the
        refusals it reaches now report one code. The population that closure ranges over is
        recorded on :class:`ProjectPathEscapeError` and driven by
        ``tests/integration/test_contained_path_envelope.py``, rather than here
        where only this one seam is visible.
        """
        if not path.is_relative_to(self.knowledge_dir):  # pragma: no cover - see docstring
            # No helper builds such a path; `initialize_project` calls `_contain`
            # directly rather than coming through here. Kept as the contract
            # guarantee the rest of this method's reasoning rests on.
            return KNOWLEDGE_DIR_ESCAPE_REMEDY
        parts = path.relative_to(self.knowledge_dir).parts
        if parts and parts[0] == _REVIEW_SUBDIRECTORY:
            # Ahead of the derived check and keyed on the first component alone:
            # `review` is not in `DERIVED_SUBDIRECTORIES` and never may be
            # (ADR-0030 decision 3), so the order is for the reader rather than
            # for correctness -- but the depth is not. A helper added later for
            # something under the evidence directory would otherwise fall back to
            # a cure naming `theurian init`, which creates nothing here.
            #
            # The arm reaches that helper; the cure's *text* does not yet. It
            # names `rm .theurian/review`, which is written for the only site
            # this arm has today -- `ProjectPaths.review`, the directory itself.
            # Adding a helper beneath it means revisiting `review_escape_remedy`
            # in the same change, and its docstring carries the key that says so.
            return review_escape_remedy(self.knowledge_dir.name)
        under_a_derived_subdirectory = (
            len(parts) >= _MIN_PARTS_UNDER_A_DERIVED_SUBDIRECTORY
            and parts[0] in DERIVED_SUBDIRECTORIES
        )
        if not under_a_derived_subdirectory:
            return KNOWLEDGE_DIR_ESCAPE_REMEDY
        return derived_escape_remedy(self.knowledge_dir.name, parts[0])

    @property
    def migrations(self) -> Path:
        # The one helper deliberately *not* routed through `_contained`. It is
        # consumed inside `resolve_context` (`load_migrations(paths.root,
        # paths.migrations, ...)`), where the migration loader already contains it
        # -- `_refuse_unusable_migrations_directory_symlink` proves the migrations
        # directory resolves inside the root -- and does so with a culprit-naming
        # remedy the CLI deliberately grades EXIT_STATE_ERROR (`_require_project`'s
        # `except PathEscapeError`, issue #233). Routing it here would pre-empt
        # that richer refusal with `_contained`'s coarser one and regrade a
        # deliberate exit 4 to exit 1. The containment property the sweep asserts
        # still holds for it -- just via the loader, pinned by
        # `tests/unit/test_migration_loader_errors.py` and the CLI validate tests.
        return self.knowledge_dir / "migrations"

    @property
    def knowledge(self) -> Path:
        return self._contained(self.knowledge_dir / "knowledge")

    @property
    def specifications(self) -> Path:
        return self._contained(self.knowledge_dir / "specifications")

    @property
    def proposals(self) -> Path:
        """Where agent-drafted, unapproved changes wait for a human (ADR-0013).

        Not derived, and so not git-ignored: a proposal directory is review
        input, and it is the one thing under ``.theurian/`` written by an agent
        and read by a person.
        """
        return self._contained(self.knowledge_dir / "proposals")

    @property
    def proposals_local(self) -> Path:
        """Where ``theurian propose --local`` drafts instead (ADR-0028).

        The other half of :attr:`proposals`' sentence: not derived either, and
        git-ignored anyway -- for a reason that is not ADR-0004's. A local
        proposal is authored content whose *bytes* must not leave the machine,
        so ``theurian init`` writes this path into the managed ``.gitignore``
        block, which every clone inherits. Nothing rebuilds it, which is why it
        must never join ``DERIVED_SUBDIRECTORIES``.

        The layout inside is identical to :attr:`proposals`. Only the parent
        differs, and ``propose accept`` reads both through one implementation:
        a second location must not become a second reader (SEC-7).
        """
        return self._contained(self.knowledge_dir / "proposals-local")

    @property
    def review(self) -> Path:
        """Where ingested review evidence lands (ADR-0030 decision 3).

        **Not derived, and that is the decision rather than an oversight.**
        ADR-0004's *Never Git-tracked* list named "raw GitHub review caches" and
        ADR-0030 withdraws that entry in place: upstream comments are editable
        and deletable, so a discarded local copy of a deleted comment is data
        loss and no refetch recovers it. It must therefore never join
        ``DERIVED_SUBDIRECTORIES``, and ``theurian init`` does not write it into
        the managed ``.gitignore`` block -- whether a project commits its review
        evidence is the project's decision.

        A helper rather than a join at the call site, for the reason
        :attr:`ingestion_manifest` records: a path assembled by the writer never
        passes :meth:`_contained`, and a clone can force-add
        ``.theurian/review -> ../../shared`` and put every evidence file outside
        the working tree. The per-record leaf beneath this directory is contained
        again by ``infrastructure/review_evidence``, which resolves every
        relative path against *this* root through ``security/paths.py`` -- so a
        hostile provider id is refused there and an escaping ``review`` symlink
        is refused here.

        Composed from :data:`_REVIEW_SUBDIRECTORY` rather than from a literal,
        the way :attr:`config` is composed from ``PROJECT_CONFIG_FILE``. What
        that constant decides is not this path but what a *refusal* of it says:
        :meth:`_escape_remedy` compares it against ``parts[0]`` to pick the arm,
        and :func:`review_escape_remedy` renders it into the cure. A literal here
        could drift out of step with either, and the visible symptom would be an
        escaping evidence directory published with the fallback's ``theurian
        init`` clause again -- the defect #602 fixed, returning by a different
        door.
        """
        return self._contained(self.knowledge_dir / _REVIEW_SUBDIRECTORY)

    @property
    def config(self) -> Path:
        """The project's own settings, if it has written any.

        Optional, unlike every other path here: ``theurian init`` writes no such
        file and every key it can carry has a shipped default. Composed from
        :data:`~theurian.security.project_config.PROJECT_CONFIG_FILE` rather than
        from a literal, so the path and its only reader cannot end up meaning
        different files.
        """
        return self._contained(self.knowledge_dir / PROJECT_CONFIG_FILE)

    @property
    def state(self) -> Path:
        return self._contained(self.knowledge_dir / "state")

    @property
    def runtime(self) -> Path:
        return self._contained(self.knowledge_dir / "runtime")

    @property
    def active_pointer(self) -> Path:
        # Built from `knowledge_dir`, not from `self.state`, so accessing it runs
        # `_contained` once over the whole path rather than twice (once for the
        # `state` property, again for the leaf). One `resolve` of the full path
        # follows a symlink at `state` *or* the leaf, so the single check is no
        # weaker -- an escaping `state` is still caught here.
        return self._contained(self.knowledge_dir / "state" / "active.json")

    @property
    def active_index_pointer(self) -> Path:
        """Which index build retrieval should read.

        Separate from ``active_pointer`` because an index is rebuilt on its own
        schedule: re-embedding a corpus with a different model changes nothing
        canonical, and swapping the pointer is what makes a blue/green index
        build a rename rather than an outage.
        """
        return self._contained(self.knowledge_dir / "state" / "active-index.json")

    @property
    def index_secret_scan(self) -> Path:
        """What the published build's SEC-11 scan found (#329).

        Beside ``active-index.json`` and derived exactly like it: one record per
        project, rewritten by every publish, naming the build it describes.
        Composed from ``knowledge_dir`` rather than from :attr:`state` for the
        reason :attr:`active_pointer` is -- one ``_contained`` over the whole path
        catches an escaping ``state`` symlink *and* an escaping leaf, where two
        checks would run the same resolve twice.

        The record's shape and why it is a count rather than a list of findings
        live in :mod:`theurian.application.index_secret_scan`.
        """
        return self._contained(self.knowledge_dir / "state" / "index-secret-scan.json")

    # -- The temporary leaves the atomic writers create --------------------------
    #
    # Every write-to-temp-then-`os.replace` publisher below used to derive its
    # temporary with `pointer.with_suffix(".json.tmp")`, which produces a
    # *different* leaf -- `active.json.tmp` next to `active.json` -- that no
    # containment check ever saw. `Path.write_text` then followed a symbolic link
    # planted there and truncated whatever it named, at exit 0 with a success
    # report: measured on all three, in-tree and out (#523). They are helpers now
    # so the derived leaf is proved contained by the same chokepoint the published
    # name is, and so the reflection sweep in
    # `tests/unit/test_project_paths_containment.py` enumerates them -- the write
    # itself refuses the in-tree link separately, through
    # `theurian.security.no_follow`.
    #
    # The name is spelled here and nowhere else. A writer that recomputed
    # `with_suffix` beside this would be back to two definitions of one path, and
    # the one that skipped containment would be the one doing the writing.

    @property
    def active_pointer_temporary(self) -> Path:
        """Where :func:`write_active_state` builds the pointer before publishing it."""
        return self._contained(self.knowledge_dir / "state" / "active.json.tmp")

    @property
    def active_index_pointer_temporary(self) -> Path:
        """Where :func:`write_active_index_pointer` builds the pointer."""
        return self._contained(self.knowledge_dir / "state" / "active-index.json.tmp")

    @property
    def index_secret_scan_temporary(self) -> Path:
        """Where :func:`~theurian.application.index_secret_scan.write_index_secret_scan` builds."""
        return self._contained(self.knowledge_dir / "state" / "index-secret-scan.json.tmp")

    @property
    def ingestion_manifest(self) -> Path:
        """The content-hash manifest ``theurian ingest`` writes (#394).

        ``.theurian/cache/`` is derived and git-ignored (ADR-0004), and the
        command built this path by joining ``knowledge_dir`` directly -- so it
        never passed the containment chokepoint, and a clone carrying a
        force-added ``.theurian/cache -> ../../shared`` put the manifest outside
        the working tree at exit 0 (measured, both faces). A helper rather than a
        join at the call site is what puts it in the swept population; the write
        refuses an in-tree link **at this leaf** on its own.

        A link at ``cache`` *itself* pointing somewhere inside the tree is refused
        by neither guard, and that is the recorded bound rather than an oversight:
        ``O_NOFOLLOW`` constrains the manifest's own final component, and
        containment is satisfied because the target resolves inside the root. A
        clone carrying ``.theurian/cache -> ../docs`` therefore still writes the
        manifest onto ``docs/ingestion.json`` at exit 0
        ([#577](https://github.com/theurian/theurian/issues/577), measured and
        pinned as a fact by
        ``test_derived_path_symlink_writes.py::test_a_contained_directory_link_still_relocates_the_manifest``).
        """
        return self._contained(self.knowledge_dir / "cache" / "ingestion.json")

    def state_database_named(self, database_filename: str) -> Path:
        """The canonical store a *pointer* names, proved to stay inside the tree.

        The sibling of :meth:`index_for` for the other derived value.
        :meth:`database_for` takes a :class:`StateHash` this build computed and so
        needs no such proof; this one takes ``active.json``'s ``databaseFilename``
        verbatim -- ``ActiveState.from_json`` only ``str()``s it -- and any local
        process can put ``../`` in that file (SEC-7).

        **State-scoped, not merely root-scoped, and the first cut was only the
        latter** (round two, security H-1). ``_contained`` proves the path stays
        inside the *project root*, which leaves the whole checkout reachable:
        with ``databaseFilename`` set to ``../../decoy.sqlite`` -- a file at the
        root, a path a clone delivers as ordinary tracked content --
        ``knowledge.search`` served the decoy's rows at exit 0 (measured
        2026-09-06, the decoy's own title came back). The second check is the one
        :meth:`index_for` has carried since it was written, and
        :meth:`findings_for`'s docstring records it as the obligation of every
        helper that takes a *value* rather than a computed name.

        **What the escape costs when it is not refused, measured rather than
        reasoned** (2026-09-06, real CLI plus the MCP entry point). Provenance
        binds ``(root, state_hash)`` and not the filename, so it passes; the
        read-back integrity guard is what fires, and only when the *content*
        differs. With the pointer aimed at a doctored copy outside the tree,
        ``knowledge.search`` refused with ``InvariantViolationError``. With it
        aimed at a **byte-identical** copy, nothing refused at all: exit 0, one
        result served, out of a file the working tree does not contain. So the
        bound was on what an escape could *say*, never on whether it happened --
        the gap this method closes, and the reason a comment at the call site
        naming provenance as the refuser was wrong twice over.

        Raises:
            ProjectPathEscapeError: The filename resolves outside the project.
            ProjectError: The filename resolves inside the project but outside
                ``.theurian/state/``, or cannot name a path at all.
        """
        state = self.state  # one `_contained`; an escaping `state` refuses here
        try:
            candidate = (state / database_filename).resolve()
            contained = candidate.is_relative_to(state.resolve())
        except (ValueError, OSError) as exc:
            # An embedded NUL makes `resolve` raise `ValueError` and a name the
            # platform rejects makes it raise `OSError`; neither is a
            # `TheurianError`, and the conversion happens here for the reason
            # `index_for`'s does -- callers may only ever need to catch one type.
            raise ProjectError(
                f"The state pointer names {database_filename!r}, which is not a usable filename.",
                remedy=ACTIVE_POINTER_REMEDY,
            ) from exc
        if not contained:
            raise ProjectError(
                f"The state pointer names {database_filename!r}, which resolves outside {state}.",
                remedy=ACTIVE_POINTER_REMEDY,
            )
        return candidate

    def index_for(self, index_build_id: str) -> Path:
        """Where one index build lives.

        The prefix matters. Index builds share a directory with canonical state
        databases, and a glob that could not tell them apart would hand a
        retrieval index to the canonical store.

        Containment is checked because the id reaching here comes from
        `active-index.json` — a derived, git-ignored, unsigned file that any
        local process can edit. `../` in it resolves outside the project, and
        SEC-7 covers every path, not only the ones that look like user input.

        **Returning does not make the path one the OS will answer for**, which is
        the correction #388 records and the comment below carries in full: a
        caller that stats what this hands back grades ``OSError`` beside the
        ``ProjectError``, or the stat escapes as a traceback.

        Raises:
            ProjectError: If the id would escape the state directory, or cannot
                name a path at all.
        """
        state = self.state  # one `_contained`; an escaping `state` refuses here
        try:
            candidate = (state / f"theurian-index-{index_build_id}.sqlite").resolve()
            contained = candidate.is_relative_to(state.resolve())
        except (ValueError, OSError) as exc:
            # An embedded NUL makes `resolve` raise `ValueError`, and a name the
            # platform rejects makes it raise `OSError`. Neither is a
            # `TheurianError`, so both escaped callers that had correctly
            # narrowed to one: `knowledge.search` failed permanently for the
            # project instead of degrading to an answer (ADR-0004), and the
            # OS-level message reached the client. Callers may only ever need to
            # catch `TheurianError`, so the conversion happens here.
            raise ProjectError(
                f"The index pointer names {index_build_id!r}, which is not a usable filename.",
                remedy=INDEX_POINTER_REMEDY,
            ) from exc

        # Resolving succeeded, and that is **all** it establishes. This said "the
        # returned path is one the OS will accept -- a caller's later `is_file()`
        # cannot raise the error just converted", and #388 measured it false:
        # `Path.resolve()` in non-strict mode never stats, so an
        # `index_build_id` of 234 characters or more (15 + 234 + 7 past a
        # 255-byte `NAME_MAX`) comes back from here as a `Path` whose first
        # `os.stat` raises `OSError` errno 63, `ENAMETOOLONG`. CPython 3.13's
        # `pathlib._abc._IGNORED_ERRNOS` is `{ENOENT, EBADF, ENOTDIR, ELOOP}`
        # (measured 2026-09-05), so `is_file()` does not swallow it either.
        # Callers therefore grade `OSError` beside `TheurianError` at the probe,
        # and which of them do is derived from the source rather than listed:
        # `test_derived_state_value_envelope.py`'s
        # `test_every_index_for_caller_grades_the_stat_beside_the_call`.
        if not contained:
            raise ProjectError(
                f"The index pointer names {index_build_id!r}, which resolves outside {state}.",
                remedy=INDEX_POINTER_REMEDY,
            )
        return candidate

    def findings_for(self, build_id: str) -> Path:
        """Where the review-finding store lives (ADR-0029 phase-2 slice-2).

        The ``theurian-findings-`` prefix matters for the reason ``index_for``'s
        does: this file shares ``.theurian/state/`` with canonical state databases
        (``theurian-state-``) and retrieval indexes (``theurian-index-``), and a
        glob that could not tell the three apart would hand one artifact's reader
        another's file.

        Routed through :meth:`_contained` like :meth:`database_for`, not through
        ``index_for``'s bespoke state-scoped check. That richer check exists because
        the index id arrives from ``active-index.json`` -- a derived, git-ignored
        file any process can edit with ``../``. This slice has **no findings
        pointer**: ``build_id`` is a trusted constant supplied by ``theurian
        findings build``, so the root-level containment ``_contained`` proves is
        sufficient. When a serving slice adds an untrusted pointer, the
        state-scoped check ``index_for`` carries becomes owed here too.
        """
        filename = f"theurian-findings-{build_id}.sqlite"
        return self._contained(self.knowledge_dir / "state" / filename)

    def review_search_for(self, store_id: str) -> Path:
        """Where the review search store lives (ADR-0030 slice 3).

        The **fourth** artifact family under ``.theurian/state/``, and the
        ``theurian-review-`` prefix is what keeps it apart from the other three.
        ``index gc`` globs ``theurian-index-*`` and must not reclaim this file;
        ``_applied_migration_ids`` globs ``theurian-state-*`` and would open
        whatever matches as a canonical database. A prefix that collided with, or
        was a prefix of, any of the other three would hand one artifact's reader
        another's file --
        ``test_project_paths_containment.py::test_the_findings_index_and_state_filename_prefixes_are_pairwise_disjoint``
        holds the four apart.

        **State-scoped, not merely root-scoped, and that is deliberate even though
        no untrusted pointer feeds it today.** :meth:`findings_for` routes through
        :meth:`_contained` alone and records why that is sufficient *for it*: its
        ``build_id`` is a trusted constant, so root-level containment is enough
        until a serving slice adds a pointer a local process can edit. This is that
        serving slice. It still ships no pointer -- ``store_id`` is
        :data:`REVIEW_SEARCH_STORE_ID`, a constant -- so what the check buys now is
        not a closed hole but the ordering: the moment a pointer is added, the
        containment it needs is already the one in force. The measured cost of
        getting that ordering wrong is on :meth:`state_database_named`, where
        root-scoped containment served a decoy *inside* the checkout at exit 0.

        Raises:
            ProjectError: If ``store_id`` would escape ``.theurian/state/``, or
                cannot name a path at all. Converted here for the reason
                :meth:`index_for`'s is: a ``ValueError`` from an embedded NUL or an
                ``OSError`` from a name the platform rejects are neither of them a
                ``TheurianError``, and callers may only ever need to catch one type.
            ProjectPathEscapeError: If ``.theurian/state`` itself resolves outside
                the project, which the ``self.state`` access below refuses first.
        """
        state = self.state  # one `_contained`; an escaping `state` refuses here
        filename = _review_search_store_filename(store_id)
        try:
            candidate = (state / filename).resolve()
            contained = candidate.is_relative_to(state.resolve())
        except (ValueError, OSError) as exc:
            raise ProjectError(
                f"The review search store id {store_id!r} is not a usable filename.",
                remedy=REVIEW_SEARCH_STORE_REMEDY,
            ) from exc
        if not contained:
            # **Layout-free by construction, which is why the boundary above this
            # needs no fold for it** (GHSA-923w-f36f-jcfq, and the closed loop that fold
            # cost: verdict pass, adversarial HIGH). This message used to end
            # *"resolves outside {state}"* -- the resolved `.theurian/state`, and
            # for a project registered through a symbolic link the physical
            # directory behind `rootPath`, which no tool publishes. `review.search`
            # answered the whole refusal with an availability constant to keep that
            # string off the wire, and the constant's cure -- `theurian review
            # build` -- resolves this same file through this same method and exits
            # 1 on the fault that produced the refusal, so the caller was handed
            # back a byte-identical refusal. While the message carried a location,
            # the fold was the only place to fix that; the message no longer does,
            # and the cure crosses beside it.
            #
            # Two things are named and neither is a path this process resolved: the
            # store's own file name, built from `store_id` by the same helper that
            # built the candidate above, and the project-relative
            # `.theurian/state/`, spelled as `REVIEW_SEARCH_STORE_REMEDY` spells
            # it. The docstring above records why `store_id` is a constant in this
            # composition; `!r` is what keeps it escape-safe whatever supplies it
            # later.
            raise ProjectError(
                f"The review search store file {filename!r} does not resolve to a "
                f"location inside .theurian/state/.",
                remedy=REVIEW_SEARCH_STORE_REMEDY,
            )
        return candidate

    @property
    def write_lock(self) -> Path:
        return self._contained(self.knowledge_dir / "runtime" / "write.lock")

    def database_for(self, state_hash: StateHash) -> Path:
        return self._contained(self.knowledge_dir / "state" / state_hash.database_filename)

    @classmethod
    def of(cls, root: Path, knowledge_directory: PurePosixPath | None = None) -> ProjectPaths:
        directory = knowledge_directory or DEFAULT_KNOWLEDGE_DIRECTORY
        resolved = root.resolve()
        knowledge_dir = resolved / str(directory)

        # Contain the `.theurian` join itself, not only the index-pointer id that
        # later resolves under it. `resolved` resolves the *root*; the join is
        # not, so a clone that ships `.theurian` as a symbolic link to
        # `../elsewhere` puts every state read and write outside the working tree
        # (#237, T-5) -- `migrate apply` writing its state database, active
        # pointer and write lock into the link's target and returning 0, and the
        # `migrate status` after it reading `stateBuilt: true` back from there.
        #
        # Kept, not subsumed into `_contain`. `_contain` guards every path
        # *derived* from `knowledge_dir`, but `knowledge_dir` is a field the class
        # also hands out directly -- `project status` reads `knowledge_dir.is_dir()`
        # (cli/commands.py) without going through a helper -- so a symlinked
        # `.theurian` would follow the link on those direct uses if this check were
        # removed. It is also the earliest refusal: it fires while resolving the
        # command context, before a single helper is touched. `_contain` closes the
        # complementary face a descendant symlink opens (`.theurian/state ->
        # ../elsewhere`), which this check misses because `.theurian` there is an
        # honest directory.
        #
        # Checked against `resolved`, never against the join's own resolution:
        # `index_for` compares a candidate to `self.state.resolve()`, but when
        # `.theurian` itself escapes, `self.state.resolve()` *is* the escaped
        # location and the comparison is trivially satisfied -- which is why the
        # containment must live at the join and be anchored to the true root.
        # Resolving the whole join (not just its last component) also follows a
        # symlinked ancestor of `.theurian`, so a link anywhere on its path is
        # caught the same way.
        #
        # `ProjectPathEscapeError` and not the bare `ProjectError` this raised
        # until #550. The type is what the CLI grades on, and while this check
        # raised the base class its refusal fell through every handler keyed on
        # the escape and landed in whatever each caller assigns to "could not
        # resolve a project" -- so one root cause answered two codes depending on
        # *which level* of the tree the delivered link sat at. Measured at
        # `8372cc8c` against the real CLI, `.theurian/state` escaping against
        # `.theurian` escaping: `migrate status`, `migrate validate`, `migrate
        # apply`, `index build`, `index status`, `index gc` and `project status`
        # each answered 4 for the leaf and 1 -- 0, for `project status`, with a
        # payload calling the project registered -- for the directory above it.
        try:
            escapes = not knowledge_dir.resolve().is_relative_to(resolved)
        except (OSError, ValueError) as exc:
            # Defensive parity with `_contain`'s own arm, and reachable through the
            # same one door: an embedded NUL in `directory` raises `ValueError` from
            # the syscall layer (measured, ``lstat: embedded null character``). The
            # `OSError` half is a contract guarantee, not a POSIX branch -- `resolve`
            # here is non-strict, so a symlink cycle, a dangling link, an
            # over-`NAME_MAX` component and a path past `PATH_MAX` were each measured
            # 2026-09-07 to resolve without raising (Darwin, CPython 3.13.3); only a
            # platform that rejects the name outright (Windows, or a future stricter
            # resolver) takes this arm by `OSError`. Every `directory` reaching here
            # is the validated `knowledge_directory` or the constant default, so no
            # real caller drives it -- but a path that will not resolve to a location
            # is refused for the same reason one that resolves outside is: nothing
            # derived from it can be trusted to stay inside. Driven by
            # `test_project_paths_containment.py`'s embedded-NUL case.
            raise ProjectPathEscapeError(
                f"{directory} does not resolve to a location inside {resolved}: {exc}",
                remedy=KNOWLEDGE_DIR_ESCAPE_REMEDY,
            ) from exc
        if escapes:
            raise ProjectPathEscapeError(
                f"{directory} resolves outside the project root {resolved}, so every file "
                f"Theurian would read or write under it is outside the working tree.",
                remedy=KNOWLEDGE_DIR_ESCAPE_REMEDY,
            )

        return cls(root=resolved, knowledge_dir=knowledge_dir)


def derive_project_id(root: Path) -> ProjectId:
    """Propose a stable, readable project id from a directory name.

    Deliberately derived from the *name* rather than the absolute path: moving a
    repository must not change its identity, and a path-derived id would leak a
    machine-specific value into a shared registry.

    **A proposal, not an identity.** Directory names are not unique — a user with
    both ``team-one/api`` and ``team-two/api`` gets ``api`` twice — so what this
    returns is only the default offered at registration. The registry is the
    authority for a project that has been registered, and it refuses both
    directions of ambiguity: a second root taking an id that is already spoken
    for, and a second id naming a root that already has one
    (:meth:`ProjectRegistry.register`).
    """
    slug = _SLUG_INVALID.sub("-", root.resolve().name.lower()).strip("-")
    if not slug:
        raise ProjectError(f"Cannot derive a project id from {root}")
    return ProjectId(slug)


def initialize_project(paths: ProjectPaths) -> tuple[str, ...]:
    """Create the ``.theurian/`` layout.

    Never overwrites. Returns the project-relative paths it created, so setup can
    report exactly what changed rather than claiming success vaguely (§34).

    A *writer* under the project tree, so it belongs to the same population
    ``ProjectPaths``'s helpers do and routes every ``mkdir``/``touch`` target
    through the same :func:`_contain` chokepoint. It does not go through a
    ``ProjectPaths`` helper because its targets are arbitrary subpaths of
    ``knowledge_dir`` rather than the named paths the class exposes -- but a
    clone can track ``.theurian/knowledge`` as a symbolic link to outside the
    tree exactly as it can ``.theurian`` itself, and without this ``init`` would
    ``mkdir`` the knowledge subtree at the link's target and report the paths as
    if in-tree (#237, T-5). Refusing before the create keeps a partial run inside
    the tree: nothing is created outside it, whichever target the link sits on.

    **``is_dir()`` and not ``exists()``, which is what round one caught.** The
    probe used to ask whether *something* was there, so a regular file at
    ``.theurian/cache`` or ``.theurian/state`` -- a clone can track either -- was
    read as "already created": `theurian init` skipped it, left the file where it
    was, and reported ``changed: true`` at exit 0 with the path absent from
    ``createdPaths`` (measured at ``8f975d50``, both leaves). Nothing then
    ignores the derived artefacts that belong there, and the next command writes
    into a directory that does not exist. Only a *prefix* file reached the
    ``mkdir``'s own refusal, so the arm added for the dangling link answered one
    face of two.

    **A symbolic link to a real directory still passes**, and that is #577's
    recorded bound rather than a gap in this check: ``is_dir()`` follows the
    link to answer, so a clone carrying ``.theurian/state -> ../elsewhere``
    resolves to a directory, `theurian init` exits 0, and the derived state is
    written **through** the link -- measured 2026-09-06, with ``git add -A``
    then staging it. Containment waves it through for the same reason it waves
    through the ingestion manifest's: the target resolves inside the working
    tree. Closing it needs ``openat`` against a directory descriptor at every
    level, which nothing in this codebase does
    ([#577](https://github.com/theurian/theurian/issues/577)).

    Raises:
        ProjectError: A path the layout needs is occupied by something that is
            not a directory. Named rather than skipped, because "it is already
            there" and "something else is there" have opposite cures.
    """
    created: list[str] = []

    for relative in INITIAL_DIRECTORIES:
        directory = _contain(paths.root, paths.knowledge_dir / relative)
        if directory.is_dir():
            continue
        if directory.exists() or directory.is_symlink():
            # `is_symlink` beside `exists`: a *dangling* link answers `False` to
            # both `is_dir` and `exists` -- `exists` follows it to decide -- and
            # would otherwise fall through to the `mkdir`, which refuses it as a
            # bare `FileExistsError` carrying no cure of its own.
            raise ProjectError(
                f"{directory} is not a directory, so Theurian cannot create the "
                f"`.theurian/` layout there. Nothing was changed.",
                remedy=(
                    f"Remove or rename {directory} -- a clone can deliver it as a regular "
                    f"file or as a symbolic link -- then re-run `theurian init`."
                ),
            )
        directory.mkdir(parents=True)
        created.append(str(Path(paths.knowledge_dir.name) / relative))

    # `.gitkeep` only where Git must carry an otherwise-empty directory. Derived
    # directories are git-ignored, so marking them would commit a path that is
    # supposed to be absent from the repository (ADR-0004). `proposals-local/`
    # is git-ignored for a different reason -- authored content deliberately
    # kept off Git (ADR-0028) -- and the argument lands the same way: a
    # `.gitkeep` there would commit the one directory a clone must not carry.
    for relative in ("migrations", "specifications", "proposals"):
        keep = _contain(paths.root, paths.knowledge_dir / relative / ".gitkeep")
        if not keep.exists():
            keep.touch()
            created.append(str(Path(paths.knowledge_dir.name) / relative / ".gitkeep"))

    return tuple(created)


def _gitignore_marker_lines(content: str, marker: str) -> list[tuple[int, int]]:
    """Every whole line equal to ``marker``, as ``(start, end)`` slice bounds.

    ``end`` stops before the line's terminator, so a ``\\r\\n`` outside the block
    stays outside every span built from these bounds.

    Split on ``\\n`` alone rather than with ``str.splitlines``, which also breaks
    on ``\\v``, ``\\f`` and ``\\u2028`` -- none of which end a line for Git, so a
    marker "line" found at one of those would not be one.

    Deliberately not shared with the identically-spelled scan in
    :mod:`theurian.security.env_file`. Those markers are separate literals for a
    stated reason -- different files, edited by different code, where renaming
    one must not silently rewrite the other -- and a shared scanner would put
    them back in one place through the back door.
    """
    found: list[tuple[int, int]] = []
    offset = 0
    for line in content.split("\n"):
        text = line.rstrip("\r")
        if text == marker:
            found.append((offset, offset + len(text)))
        offset += len(line) + 1  # the separator `split` removed
    return found


def render_gitignore_block() -> str:
    """The managed block exactly as `theurian init` writes it.

    Split out of :func:`ensure_gitignore` so that ``probe_gitignore`` can ask the
    same question the writer answers. The step used to decide with a substring
    search for each managed entry over the whole file, and a substring is not a
    rule: a ``.gitignore`` with every entry prefixed by ``!`` -- the syntax for
    *un*-ignoring -- satisfied it while ``git check-ignore`` said the paths were
    not ignored at all (#87). Two predicates written apart drifted; sharing this
    is what stops them.
    """
    # One comment per section, not one per block: the block carries two
    # categories since ADR-0028 -- derived artifacts, and authored content kept
    # out of Git on purpose -- and a single "Derived artifacts" header would be
    # false for the second in the direction that loses work.
    return "\n".join(
        [
            GITIGNORE_BLOCK_START,
            *(
                line
                for section in GITIGNORE_SECTIONS
                for line in (section.comment, *section.entries)
            ),
            GITIGNORE_BLOCK_END,
        ]
    )


def locate_gitignore_block(content: str, gitignore: Path) -> tuple[int, int] | None:
    """Slice bounds of the one managed block in *content*, or ``None`` if absent.

    Read-only, and shared with ``probe_gitignore`` for the reason
    :func:`render_gitignore_block` is: what the probe reports and what the writer
    rewrites have to be the same span, or `doctor` calls a file current that
    every `theurian init` changes.

    *gitignore* is named only to build the refusals below. Nothing here opens it.

    Raises:
        ProjectError: The markers do not delimit exactly one block -- a second
            start marker, or a start with no end after it. Each arm names what
            to look for and the command to re-run.
    """
    opened = _gitignore_marker_lines(content, GITIGNORE_BLOCK_START)
    if len(opened) > 1:
        raise ProjectError(
            f"{gitignore} holds more than one {GITIGNORE_BLOCK_START!r} line, so Theurian "
            f"cannot tell which of the rules between them are its own.",
            remedy=(
                "Delete the block you do not want -- markers and all -- then re-run "
                "`theurian init`."
            ),
        )
    if not opened:
        return None

    start = opened[0][0]
    closing = next(
        (span for span in _gitignore_marker_lines(content, GITIGNORE_BLOCK_END) if span[0] > start),
        None,
    )
    if closing is None:
        raise ProjectError(
            f"{gitignore} has an unterminated Theurian block, so Theurian cannot tell "
            f"where its own rules end.",
            # "Add the end marker" reads as unactionable to the person whose
            # file already appears to have one: a marker is matched as a whole
            # line, so a trailing space, an indent or a comment after it is not
            # one, and that is the likeliest way to arrive here. The remedy
            # therefore says what the line must be rather than only what it says
            # -- the same honesty as the env file's "Repair the markers by hand".
            remedy=(
                f"End the block with a line that is exactly {GITIGNORE_BLOCK_END!r} and "
                f"nothing else -- a trailing space is enough to stop it counting -- or "
                f"remove the block along with its rules. Then re-run `theurian init`."
            ),
        )
    return start, closing[1]


def ensure_gitignore(root: Path) -> tuple[bool, str]:
    """Append Theurian's ignore block to ``.gitignore`` if it is missing.

    Written between markers so a re-run rewrites only Theurian's own lines and
    never touches a rule the user wrote (SEC-18). That sentence was false until
    #128's class was swept here too: the search was ``str.find`` with no count
    of the start markers, so a file holding two of them -- what resolving a
    merge conflict by keeping both sides leaves behind -- had every rule between
    them swallowed by the rewrite, reported as ``changed: true`` and nothing
    else. A marker is now a whole *line*, and a second start marker anywhere in
    the file is refused rather than guessed at.

    Unlike the env file, this one is tracked by Git, so a rule lost here shows
    in ``git diff`` and is recoverable. That is a mitigation and not the fix:
    the loss is still silent when it happens, and whoever runs `theurian init`
    in a tree that already has changes in it is not looking at that diff.

    ``newline=""`` on both the read and the write, so a ``.gitignore`` with CRLF
    endings does not come back with every line ending rewritten by a run that
    was supposed to touch Theurian's own lines only.

    **``errors="surrogateescape"`` on both, too (#367).** A non-UTF-8 byte used
    to raise ``UnicodeDecodeError`` out of the read, ending `theurian init
    --json` in a traceback with an empty machine channel over a byte the
    Theurian block's own markers -- pure ASCII -- never touch. Surrogate-
    escaping decodes every byte, valid or not, into a codepoint the merge above
    can slice and concatenate like any other; encoding the result back with the
    same error handler turns each escaped codepoint back into the exact byte it
    came from, so a file this function never needed to understand round-trips
    losslessly through it. `probe_gitignore` reads the same way, for the same
    reason: block identity is a question about the markers, not about whether
    the rest of the file happens to be valid UTF-8.

    **Neither the read nor the write follows a symbolic link at the leaf**
    (#571). ``Path.write_text`` follows one and then writes through it, so a
    clone carrying ``.gitignore -> ../victim`` -- Git tracks a symlinked
    ``.gitignore`` like any other -- made `theurian init` merge the managed block
    into a file outside the working tree and report ``gitignoreUpdated: true`` at
    exit 0, link intact (measured at ``75fe9b4f``, both an out-of-tree target and
    a tracked in-tree one). The read is guarded beside the write because it is
    the read that decides what gets written back: through a link, the merge is
    computed over somebody else's file.

    **The bound: ``O_NOFOLLOW`` does not see a hard link**, and nothing here
    does. A second directory entry for the same inode is indistinguishable from
    the file itself at every syscall this uses, so a hard link at ``.gitignore``
    still merges the block into whatever else names that inode. It is recorded
    rather than closed because a clone cannot deliver one -- Git stores no hard
    links, only regular files, symbolic links and gitlinks -- so the delivery
    route this whole class turns on does not exist for it.

    **The refusal's cure is this function's own and never
    :func:`~theurian.security.no_follow.symbolic_link_remedy`.** That text says
    the link is derived state Theurian recreates and that a repository carrying
    one has committed it past an ignore -- and ``.gitignore`` is authored,
    tracked content that no ignore covers, so every clause of it would be false
    here. :data:`GITIGNORE_LINK_REMEDY` says what is true instead, and does not
    tell the operator to delete a file they wrote.

    Returns:
        ``(changed, rendered_block)``.

    Raises:
        ProjectError: The markers do not delimit exactly one block, as
            :func:`locate_gitignore_block` describes; or ``.gitignore`` is a
            symbolic link, which this refuses rather than writes through.
        OSError: The read or the write fails at the *filesystem* -- a read-only
            file, a directory in its place, a full disk. Left as it is and graded
            by the callers, which is the contract
            :mod:`theurian.security.no_follow` records for every user of these
            openers. No longer raised for a non-UTF-8 byte (#367): that used to
            surface here as ``UnicodeDecodeError``, a ``ValueError`` an
            ``except OSError`` does not see, which is what an earlier version of
            this section got wrong by folding it into "any other way the read
            fails" -- measured at ``8f975d50``, a ``.gitignore`` holding one such
            byte ended `theurian init --json` in a traceback with an empty
            machine channel. Surrogate-escaping the read retires the whole class
            rather than adding a second caught type.
    """
    block = render_gitignore_block()
    gitignore = root / ".gitignore"
    existing = _read_authored_file(gitignore)

    span = locate_gitignore_block(existing, gitignore)
    if span is not None:
        start, end = span
        if existing[start:end] == block:
            return False, block
        updated = existing[:start] + block + existing[end:]
    else:
        separator = "" if existing.endswith("\n") or not existing else "\n"
        updated = f"{existing}{separator}\n{block}\n" if existing else f"{block}\n"

    try:
        _write_gitignore(gitignore, updated)
    except OSError as exc:
        raise _gitignore_link_refusal(gitignore, exc) from exc
    return True, block


def _write_gitignore(gitignore: Path, text: str) -> None:
    """The ``.gitignore``-specific twin of ``write_text_without_following_a_link``.

    That helper hardcodes strict UTF-8, which is right for every other caller --
    JSON documents Theurian itself renders, always valid UTF-8 by construction --
    and wrong for this one: *text* can carry the surrogate codepoints
    :func:`_read_authored_file` produced for a byte outside UTF-8, and encoding
    those in strict mode raises ``UnicodeEncodeError`` ("surrogates not
    allowed"), the write-side mirror of the read-side defect #367 closes.
    ``errors="surrogateescape"`` is the same handler the read used, so each
    escaped codepoint becomes the exact byte it came from rather than being
    substituted or refused.
    """
    descriptor = open_without_following_a_link(gitignore)
    try:
        handle = os.fdopen(descriptor, "w", encoding="utf-8", newline="", errors="surrogateescape")
    except BaseException:
        # `os.fdopen` takes ownership of the descriptor only once it returns, so
        # this arm is the one place the descriptor would leak -- the same guard
        # `write_text_without_following_a_link` carries for its own `fdopen`.
        os.close(descriptor)
        raise
    with handle:
        handle.write(text)


def _read_authored_file(gitignore: Path) -> str:
    """``.gitignore``'s current bytes, or ``""`` when there is no file there.

    ``newline=""`` for the reason :func:`ensure_gitignore` records, and
    ``O_NOFOLLOW`` so the merge below is computed over *this* repository's rules
    and not over whatever a planted link names. ``ENOENT`` is the ordinary case
    -- a repository with no ``.gitignore`` yet -- and is the empty string, which
    is what the ``exists()`` probe here used to answer.

    Raises:
        ProjectError: The path is a symbolic link.
        OSError: Anything else the open or the read refuses with.

    No longer raises ``UnicodeDecodeError`` for a non-UTF-8 file (#367): the
    open decodes with ``errors="surrogateescape"``, which cannot fail to
    decode. What used to be the caller's `except (OSError, UnicodeDecodeError)`
    arm is no longer reachable through this function for that reason -- the
    exception type is not removed from those `except` clauses because they are
    outside this module, but nothing this function does can raise it any more.
    """
    try:
        descriptor = open_for_reading_without_following_a_link(gitignore)
    except FileNotFoundError:
        return ""
    except OSError as exc:
        raise _gitignore_link_refusal(gitignore, exc) from exc
    try:
        handle = os.fdopen(descriptor, encoding="utf-8", newline="", errors="surrogateescape")
    except BaseException:
        # `os.fdopen` takes ownership of the descriptor only once it returns, so
        # this arm is the one place the descriptor would leak -- the same guard
        # `write_text_without_following_a_link` carries for its own `fdopen`
        # (round one, LOW).
        os.close(descriptor)
        raise
    with handle:
        return handle.read()


def _gitignore_link_refusal(gitignore: Path, exc: OSError) -> GitignoreIsASymbolicLinkError:
    """Convert an ``O_NOFOLLOW`` refusal at ``.gitignore``; re-raise anything else.

    A :class:`TheurianError` rather than the bare ``OSError``
    :mod:`~theurian.security.no_follow` hands back, and that is a departure from
    that module's stated contract with a stated reason: its callers there are
    composition roots that already grade ``OSError``, while this one is an
    application function whose refusal has to survive two of them and carry a
    cure neither could write for itself. Every other errno stays an ``OSError``
    and keeps whatever those callers already do with it.
    """
    if not is_a_symbolic_link_refusal(exc):
        raise exc
    return GitignoreIsASymbolicLinkError(
        ".gitignore is a symbolic link, so Theurian refused to read or write through "
        "it -- the managed block would have gone to whatever it names, and the rules "
        "already there would have been merged out of that file rather than this one.",
        remedy=GITIGNORE_LINK_REMEDY.format(path=gitignore),
    )


def resolve_state_hash(loaded: LoadedMigrations, schema_version: int) -> StateHash:
    """Compute the state hash for a loaded migration set (ADR-0016)."""
    return compute_state_hash(
        state_inputs_from(loaded.migration_set, loaded.content_checksums, schema_version)
    )


def _under_the_project(path: Path, root: Path) -> str:
    """``path`` written relative to ``root``, or its bare name if it is not under it.

    For messages that reach an MCP client, where an absolute path is the
    operator's machine layout and not the reader's business (GHSA-923w-f36f-jcfq). The
    fallback is the leaf rather than the absolute path: a path outside the root
    has its own refusal with its own wording, and this helper is not the place to
    decide that a caller reached it by mistake.
    """
    try:
        return str(path.relative_to(root))
    except ValueError:
        return path.name


def read_active_state(paths: ProjectPaths) -> ActiveState | None:
    """Read the active state pointer, or ``None`` if there is none.

    Every way of failing to interpret the file lands on one ``ProjectError``.
    **Two remedies, not one**, and the sentence here said "one remedy, because
    the file is derived and one cure covers all of them" until the ``OSError``
    arm below reached a case it is false of: deleting the pointer is a cure the
    reader can carry out only while the directory holding it is theirs to write,
    and #389's third face is precisely the one where it is not
    (:data:`ACTIVE_POINTER_UNREADABLE_REMEDY`, which names both acts). The parse
    failures keep :data:`ACTIVE_POINTER_REMEDY` unchanged.

    Catching only the parse failures left the other two escaping raw, and the
    shape they escaped in was the same each time -- an OS-level string with no
    next action, at whichever surface asked:

    - ``UnicodeDecodeError`` is a ``ValueError`` and is not a subclass of
      ``JSONDecodeError``, so a pointer holding arbitrary bytes reached every MCP
      tool as ``'utf-8' codec can't decode byte 0xb9 in position 15``. Its
      sibling :func:`read_active_index_pointer` had caught this for a year; the
      same line eight functions up had not.
    - ``OSError`` covers the file the process cannot open at all -- mode ``000``,
      a directory in its place, a state directory whose permissions changed --
      which reached the same tools as ``[Errno 13] Permission denied`` naming the
      absolute path.

    **The ``Path.exists`` probe is inside the ``try``, and moving it there is
    what #389's last face needed.** This docstring used to say it "swallows the
    stat failure and answers ``False``" and "does not, in practice, get the
    chance to", and both are measured false: CPython 3.13's
    ``pathlib._abc._IGNORED_ERRNOS`` is ``{ENOENT, EBADF, ENOTDIR, ELOOP}``
    (measured 2026-09-05), so ``EACCES`` is re-raised, and the probe runs
    *before* the ``read_text`` the sentence deferred to. With
    ``.theurian/state`` at mode ``000`` the ``PermissionError`` left this
    function above the handler written for it, and seven of the nine commands in
    ``test_canonical_store_corruption.py``'s ``CLI_SWEEP`` ended in a Rich
    traceback at exit 1 with **zero bytes on stdout** -- measured at ``75fe9b4f``
    through the real CLI, everything but ``project list`` and ``version``.
    Inside the ``try`` the probe's failure takes the same conversion the read's
    already did; ``False`` still means "no pointer", and now only when the OS
    said so.

    **A fourth way to fail arrives as a ``TypeError``, and the guard below is
    what stops it.** ``json.loads`` answers any JSON value, not only an object:
    ``[]``, ``null``, ``7`` and ``"hi"`` all parse, and
    :meth:`ActiveState.from_json` then subscripts them. ``TypeError`` is in none
    of the families above, so a pointer holding one of those -- a truncated
    write, a hand edit, a restored fragment -- escaped every caller as a crash:
    measured, seven CLI positions exited 1 with **both channels empty**, and the
    MCP tools raised with no remedy at all.

    Refused with the same message and the same cure as the parse failures: a
    pointer holding a JSON array is a file whose *bytes* are wrong, which is what
    deleting it fixes. The shape is taken from
    :func:`read_active_index_pointer`, which has had this ``isinstance`` guard
    since it was written -- the same defect, caught in the sibling and missed
    here, and the whole reason a family is swept rather than reasoned about.

    **A fifth way to fail did not fail at all: it never returned** (#586). A
    named pipe at this path answers ``exists()`` with ``True`` and then holds
    ``read_text`` inside ``read()`` until a writer appears. Measured 2026-09-06
    against the real CLI: ``migrate status``, ``project status``, ``index
    status`` and ``findings build`` each ran until a 12-second kill, with zero
    bytes on stdout and stderr -- a `--json` caller cannot tell that from a
    daemon that is merely slow. :func:`~theurian.security.regular_file
    .read_text_from_a_regular_file` is the read now, and it refuses by asking the
    **descriptor** what it opened rather than asking the name beforehand, so the
    swap window a path check leaves is not reopened here. The refusal arrives as
    an ``OSError`` and so takes :data:`ACTIVE_POINTER_UNREADABLE_REMEDY`, which
    is the cure that names both the delete and the directory's permissions.
    """
    pointer = paths.active_pointer
    try:
        if not pointer.exists():
            return None
        loaded = json.loads(read_text_from_a_regular_file(pointer))
        if not isinstance(loaded, dict):
            # `raise ... from None` is not used: the `except` below re-raises with
            # the message and the remedy every other failure here carries, so the
            # one exit stays one exit.
            msg = f"the pointer holds a JSON {type(loaded).__name__}, not an object"
            raise TypeError(msg)
        return ActiveState.from_json(loaded)
    except (json.JSONDecodeError, OSError, TypeError, UnicodeDecodeError, TheurianError) as exc:
        # One exit, two cures, keyed on which of the two questions failed: an
        # `OSError` is the OS declining to hand the bytes over, and the delete
        # that fixes wrong bytes may be declined the same way.
        cure = (
            ACTIVE_POINTER_UNREADABLE_REMEDY if isinstance(exc, OSError) else ACTIVE_POINTER_REMEDY
        )
        # **Project-relative, and the cause without its filename** (round one,
        # LOW). This message reaches MCP clients verbatim -- `_resolve` publishes
        # it through `_with_remedy` -- and both halves used to carry the
        # operator's absolute path: `{pointer}` directly, and `{exc}`, because an
        # `OSError`'s `str` appends the filename its `strerror` leaves out. The
        # rule the diff around this states three times (GHSA-923w-f36f-jcfq) is that a
        # reply keeps them out; the relative path names the same file and reads
        # the same to whoever has the checkout.
        where = _under_the_project(pointer, paths.root)
        cause = exc.strerror if isinstance(exc, OSError) and exc.strerror else exc
        raise ProjectError(f"{where} is unreadable: {cause}", remedy=cure) from exc


@dataclass(frozen=True, slots=True)
class ActiveIndexPointer:
    """What ``active-index.json`` said, and whether it said anything usable.

    Two failures, not one. "No pointer" and "a pointer that names no build" have
    different remedies — `theurian index build` against *delete the pointer,
    then* build — and collapsing both into ``None`` told a user who had built an
    index that they never had, then handed them the one remedy that leaves the
    file in place.

    Measured across eight ways of corrupting the file, only the one that made the
    id escape the project reached the right remedy: three (a JSON array, unparsed
    text, an empty file) reported `no-index`, three (no ``indexBuildId``, a null
    one, a blank one) reported `index-file-missing`, and one — arbitrary bytes —
    escaped as a `UnicodeDecodeError` at the agent.
    """

    #: The pointer's contents, or ``None`` when there is nothing usable to read.
    payload: Mapping[str, Any] | None = None
    #: A pointer file exists and does not name a build: unparseable, not a JSON
    #: object, or without a non-empty ``indexBuildId``.
    unreadable: bool = False

    def __post_init__(self) -> None:
        if self.payload is not None and self.unreadable:
            # Not a user-facing error: the two states are exclusive by
            # construction, and a caller branching on `unreadable` would
            # otherwise silently ignore a payload.
            msg = "an unreadable index pointer cannot also carry a payload"
            raise ValueError(msg)


def read_active_index_pointer(paths: ProjectPaths) -> ActiveIndexPointer:
    """Read the published retrieval index pointer, distinguishing its failures.

    Every problem *with the file* is answered rather than raised: the index is
    derived (ADR-0004), so a pointer that cannot be interpreted is a missing
    optimisation and the caller answers without one — but it still has to be
    able to say *which* problem, because that is what decides the remedy it
    prints. The ``except`` below names the failures that get that treatment.

    **One failure is not answered here, and it is not a file problem: the
    containment refusal raised while deriving the path** (round one, security
    and code review, correcting a "Never raises." this replaces).
    :attr:`ProjectPaths.active_index_pointer` resolves before this function has
    a file to probe, so a ``.theurian/state`` or an ``active-index.json``
    delivered as a link out of the tree leaves here as
    :class:`ProjectPathEscapeError`. **Deliberately: the CLI grades it, and
    absorbing it here was measured undoing #525.**

    The recipe that reproduces that measurement is **two edits, not one**: move
    the ``pointer =`` resolution below into the ``try`` *and* widen the
    ``except`` by :class:`ProjectPathEscapeError`. Widening the ``except``
    alone changes nothing, because the resolution that raises sits above the
    ``try`` -- which is the whole reason the refusal escapes this function, and
    a sentence here used to publish that half-recipe as the reproduction
    (verdict pass, adversarial MEDIUM). Under the two-edit recipe,
    ``tests/integration/test_contained_path_envelope.py`` turns three of its
    cases red for the ``active_index_pointer`` plant alone -- ``{'no longer
    refusing': ['index status', 'migrate apply', 'project status']}``,
    ``containment refusals graded something other than 4:
    {('active_index_pointer', 'index gc'): 1}``, and ``index gc`` publishing
    the delete-the-pointer cure in place of the cure for what escaped. That
    split of one class across two exit codes is the thing #525 unified. What a
    path leaving the working tree means cannot be said by a reader of its
    contents, and "delete the pointer" is the wrong cure for a
    ``.theurian/state`` that escaped.

    The MCP surface makes the opposite trade for the same refusal, and makes it
    at its own consumer: :func:`~theurian.mcp.search._published_index` converts
    it to ``pointer-invalid`` there, because a tool caller is not the reader who
    owns the checkout and the paths inside the message are the operator's
    layout (GHSA-923w-f36f-jcfq).

    ``indexBuildId`` is required, not merely read. A pointer without one names no
    build, so it is not a usable pointer; accepting it built a path out of an
    empty id and reported `index-file-missing` — "the published index build is
    no longer on disk", about a build that was never named.

    **The presence probe is inside the ``try``, for the reason its sibling
    :func:`read_active_state` records in full.** It sat above it until #389's
    third face, and a ``.theurian/state`` at mode ``000`` made it raise
    ``PermissionError`` past the ``except`` written for exactly that errno one
    line down -- ``pathlib`` re-raises ``EACCES``. A probe that cannot be
    answered is now ``unreadable`` rather than a crash.

    **``unreadable`` over ``None``, and the reason is not that one cure works
    here** (round one, code review M-5, correcting the sentence this replaces).
    Neither does: ``INDEX_POINTER_REMEDY`` says to delete the pointer, and under
    a mode-``000`` directory the delete is refused exactly as the read was, so
    the contrast this paragraph drew -- one honest cure against one misleading
    one -- was false. What actually separates them is what each *claims*.
    ``None`` is the assertion "this project has no index", which the probe did
    not establish and which sends a reader to build one they may already have;
    ``unreadable`` asserts only that the file could not be interpreted, which is
    exactly what happened. The cure being imperfect for one errno is a smaller
    fault than the report being wrong for every reader.

    **The probe is ``exists()`` and was ``is_file()``, and the swap is the whole
    of #586's index-pointer face.** ``is_file()`` answers ``False`` for a named
    pipe, so a FIFO planted here never blocked -- it was reported as *no pointer
    at all*: measured 2026-09-06 through the real CLI, ``index status``
    published ``built: false``, ``indexBuildId: null`` and
    ``indexPointerCorrupt: false`` at exit 0 for a project whose index was built
    and whose pointer file was sitting right there. That is the answer the
    paragraph above rules out, arriving through a probe instead of through a
    branch. ``exists()`` is ``True`` for the pipe, so the read below runs and
    :func:`~theurian.security.regular_file.read_text_from_a_regular_file` refuses it as
    an ``OSError`` -- landing on ``unreadable``, which publishes
    ``indexPointerCorrupt`` and the delete-the-pointer cure. Two other shapes
    move with it, both from ``None`` to ``unreadable``: a **directory** (the
    ``read`` answers ``EISDIR``) and a **socket** or **device**. A dangling
    symbolic link stays ``None`` under either probe, which is right -- nothing
    was ever stored there.
    """
    pointer = paths.active_index_pointer
    try:
        if not pointer.exists():
            return ActiveIndexPointer()
        loaded = json.loads(read_text_from_a_regular_file(pointer))
    except (json.JSONDecodeError, OSError, UnicodeDecodeError):
        # `UnicodeDecodeError` is a `ValueError`, not an `OSError`, and
        # `JSONDecodeError` is not its parent: a pointer holding arbitrary bytes
        # -- a partially overwritten file, a restored binary -- escaped the
        # previous handler entirely and reached the caller as a crash.
        return ActiveIndexPointer(unreadable=True)

    if not isinstance(loaded, dict):
        return ActiveIndexPointer(unreadable=True)
    build_id = loaded.get("indexBuildId")
    if not isinstance(build_id, str) or not build_id.strip():
        return ActiveIndexPointer(unreadable=True)
    return ActiveIndexPointer(payload=loaded)


def read_active_index(paths: ProjectPaths) -> dict[str, Any] | None:
    """The published retrieval index pointer's contents, or ``None``.

    For callers that only need the payload. Anything that reports a remedy to a
    user should call :func:`read_active_index_pointer` instead and say which of
    the two failures it hit.
    """
    payload = read_active_index_pointer(paths).payload
    return dict(payload) if payload is not None else None


def write_active_index_pointer(  # noqa: PLR0913 - one keyword per published pointer field
    paths: ProjectPaths,
    *,
    index_build_id: str,
    state_hash: str,
    project_id: str,
    indexes_unapproved: bool,
    indexed_sensitivities: frozenset[Sensitivity],
    purge_failed: bool = False,
) -> None:
    """Point retrieval at a finished build, atomically (ADR-0007, ADR-0024).

    Write-to-temp then ``os.replace``, which is atomic on POSIX. A reader must
    never observe a half-written pointer, because that would send it to an index
    that does not exist.

    In the application layer rather than the index CLI because two composition
    roots publish now: ``theurian index build`` (:mod:`cli.index_commands`) and
    the withdrawal-triggered purge (:mod:`application.withdrawal_purge`). A pointer
    written two different ways drifts in one of them, and the field set is a wire
    contract the retrieval side reads back key by key
    (:func:`theurian.mcp.search._published_index`).

    Every chunk in the file is stamped with the ``project_id`` that built it, and
    nothing else records which one that was: without it an index orphaned by an id
    change is indistinguishable from a project that simply has no knowledge.
    ``indexesUnapproved`` lets a search say *why* an ``includeUnapproved`` query
    returned nothing rather than looking like an empty result.

    ``indexedSensitivities`` is the second build flavor, recorded for a stronger
    reason than the first (#119, ADR-0025 part 1). A build writes no row for an
    item above the deployment's disclosure ceiling, so which ceiling was in force
    decides *which rows the file holds* -- and an FTS5 external-content table
    scores every row it holds against statistics computed over all of them. A
    build kept from an era with a different ceiling therefore cannot merely
    over-return; it prices the rows it does return against text this deployment
    does not serve. The serve path compares this against the grant in force and
    stands aside when they differ (``mcp.search._published_index``), which is why
    it is written here rather than derived from the file: the file cannot say what
    was *excluded* from it.

    ``purge_failed`` records that a withdrawal-triggered purge against *this*
    build did not complete, so the build still holds rows a migration removed from
    canonical state (:func:`mark_active_index_purge_failed`, GHSA-97q9-xxfg-33r6).
    A published build the serve path reads is clean by default; it is set only on
    the failure path, and one rebuild clears it because a fresh publish writes the
    default. The key is always present -- ``false`` on a healthy build -- so no
    reader has to branch on its absence, the discipline every other pointer field
    already holds to.
    """
    pointer = paths.active_index_pointer
    pointer.parent.mkdir(parents=True, exist_ok=True)
    # `paths.active_index_pointer_temporary`, never `pointer.with_suffix(...)`:
    # the property is the contained one, and the derivation is what skipped the
    # check (#523). `write_text_without_following_a_link` refuses the in-tree
    # link containment is right to wave through.
    temporary = paths.active_index_pointer_temporary
    write_text_without_following_a_link(
        temporary,
        json.dumps(
            {
                "indexBuildId": index_build_id,
                "stateHash": state_hash,
                "projectId": project_id,
                "indexesUnapproved": indexes_unapproved,
                "indexedSensitivities": encode_sensitivities(indexed_sensitivities),
                "purgeFailed": purge_failed,
            },
            indent=2,
        ),
    )
    # `os.replace` renames the temporary over the pointer; `rename(2)` operates on
    # the link itself and never follows one, so a link at *this* name is replaced
    # rather than written through.
    os.replace(temporary, pointer)  # noqa: PTH105 - os.replace is the atomic primitive


def mark_active_index_purge_failed(paths: ProjectPaths, *, expected_build_id: str) -> bool:
    """Record that the published build's withdrawal purge failed, if it is still it.

    A withdrawal's or reclassification's index purge can fail while the migration
    it follows is already committed (:func:`~theurian.application.withdrawal_purge.
    publish_purge_for_withdrawal`). The stale build then stays published and goes
    on holding rows the withdrawal removed from canonical state, so a ``--raptor``
    build hands a withheld document's text to a caller through a visible sibling's
    ``raptorPath`` and the FTS5 collection statistics still price every visible
    row against it (GHSA-97q9-xxfg-33r6, T-17a). This taints the pointer so the
    serve path stands the build aside whole (``mcp.search._published_index``).

    Returns whether the taint applied, and never raises:

    - ``False`` when there is no pointer, or it does not parse: a build that names
      nothing serves nothing, and overwriting a corrupt pointer would replace a
      diagnosable fault with a fabricated build id (see
      :func:`read_active_index_pointer`).
    - ``False`` when the pointer no longer names ``expected_build_id``. The purge
      holds no index-write lock (ADR-0022, #113), so a concurrent ``index build``
      may have published a clean build in the window between the purge failing and
      this write. Tainting whatever the pointer names at that moment would condemn
      the clean build and drop retrieval onto the unranked scan until someone
      rebuilt again -- a self-inflicted outage whose only symptom is worse results.
      But this check is best-effort, not a compare-and-swap: it reads the pointer
      once and writes with a plain ``os.replace``, so it only protects a clean
      build published *before* that read. One published in the window between this
      read and this write is still reverted to the stale build with
      ``purge_failed=True``. That reversion is SAFE-DIRECTION -- the reverted
      pointer carries the taint, so the serve path stands the build aside and
      serves no withheld content; the only symptom is the same self-inflicted
      degradation to the unranked canonical scan until the next rebuild. It is the
      success purge path's lock-free-write class (``withdrawal_purge`` publishing
      ``new_id`` under no index-write lock), and a compare-and-swap pointer write
      belongs to the derived index's single-writer contract, which ADR-0018
      records as owed and #439 owns. This sentence said "#113's scope" until
      2026-09-01: #113 is the merged pull request that shipped the
      purge-is-a-build model on 2026-08-10, so it can hold no owed work. The cite
      at the head of this bullet names the same pull request as the *mechanism*
      this check rests on, which is history and is correct (#444).
    - ``False`` when the recorded ``indexedSensitivities`` cannot be decoded: such
      a build is already stood aside by the flavor gate, so it serves nobody the
      withdrawn rows and there is nothing here to close by tainting it.
    - ``False`` when the write itself is refused (``OSError``). The only caller is
      already inside the purge's failure path handling one exception; a second one
      escaping here would report a ``migrate apply`` failed for a migration that is
      already committed. A double disk failure degrades to today's not-silent
      behaviour -- the purge's own ``failed``/``remedy`` still stands.

    Otherwise re-publishes the pointer with every field preserved from the current
    payload and ``purge_failed=True``, and returns ``True``.
    """
    payload = read_active_index_pointer(paths).payload
    if payload is None:
        return False
    if payload.get("indexBuildId") != expected_build_id:
        return False
    indexed = decode_sensitivities(payload.get("indexedSensitivities"))
    if indexed is None:
        return False
    try:
        write_active_index_pointer(
            paths,
            index_build_id=str(payload["indexBuildId"]),
            state_hash=str(payload.get("stateHash", "")),
            project_id=str(payload.get("projectId", "")),
            indexes_unapproved=bool(payload.get("indexesUnapproved", False)),
            indexed_sensitivities=indexed,
            purge_failed=True,
        )
    except OSError:
        return False
    return True


def write_active_state(
    paths: ProjectPaths, state_hash: StateHash, migration_count: int, clock: Clock
) -> ActiveState:
    """Publish a new active state, atomically.

    Write-to-temp then ``os.replace``, which is atomic on POSIX. A reader must
    never observe a half-written pointer, because that would send it to a
    database that does not exist (ADR-0007).

    The temporary is :attr:`ProjectPaths.active_pointer_temporary` and is written
    with ``O_NOFOLLOW`` for the reason recorded on
    :func:`write_active_index_pointer`: the derived ``.json.tmp`` leaf passed no
    containment check and no open refused a link at it, so ``migrate apply``
    truncated whatever a planted one named -- out of the tree and in it -- and
    reported success (#523).
    """
    active = ActiveState(
        state_hash=state_hash,
        database_filename=state_hash.database_filename,
        migration_count=migration_count,
        updated_at=clock.now().isoformat(),
    )

    paths.state.mkdir(parents=True, exist_ok=True)
    temporary = paths.active_pointer_temporary
    write_text_without_following_a_link(temporary, json.dumps(active.to_json(), indent=2) + "\n")
    os.replace(temporary, paths.active_pointer)  # noqa: PTH105 -- atomic replace
    return active


@dataclass(frozen=True, slots=True)
class ProjectRegistry:
    """The per-user record of which projects exist.

    Separate from any state database: a project must be listable without opening
    -- or building -- its state.
    """

    path: Path

    @classmethod
    def default(cls, data_dir: Path | None = None) -> ProjectRegistry:
        base = data_dir or Path(os.environ.get("THEURIAN_DATA_DIR", Path.home() / ".theurian"))
        return cls(path=base / "projects.json")

    def _raw_entries(self) -> dict[str, Any]:
        """The file's top level, parsed but with no entry validated yet.

        Shared by :meth:`load`, which validates each entry and *skips* the ones
        that fail, and by :meth:`register` and :meth:`unregister`, which need to
        know whether an id is *present* at all -- valid or not -- because
        skipping a malformed entry from ``load``'s result must not also make its
        id look available to a new registration (see :meth:`register`).

        Raises only for a failure entry-by-entry validation cannot recover
        from: the file cannot be read at all, it is not JSON, or its top level is
        not an object. Each means the set of ids itself is unknown, so
        :func:`registry_deletion_remedy` is the only remedy that applies -- and it
        is now attached to all three, rather than to the last alone. This
        docstring already claimed to cover unparsable JSON while that branch
        raised with no remedy at all, which reached the user as an error naming
        no way out, from the one class of registry failure with a completely
        reliable cure.

        **One cure, four arms, and the arm is passed at the raise.** The three
        causes above arrive at the reader in four different conditions, and only
        the last two leave them able to open the file: an unreadable *file*, an
        unreadable *data directory*, undecodable or unparsable bytes, and a top
        level that is not a mapping. :class:`RegistryFailureArm` is how each
        raise says which, because the cure's first sentence -- inspect, or
        ``chmod`` and then inspect -- is the only part that differs and this is
        the only place that knows.

        **Which of the two ``OSError`` arms is not the ``except``'s to decide.**
        A clause knows where the call failed; only ``errno`` knows why, and both
        clauses used to pass their permission-shaped arm whatever the kernel had
        refused for -- a directory at the registry path was answered "``chmod
        u+r`` on it", and an ``ENAMETOOLONG`` at the probe was answered with the
        data directory's ``chmod``. :func:`_arm_for_a_refused_registry` is the
        split, and it routes every non-``EACCES`` errno to the arm that assumes
        neither.

        ``UnicodeDecodeError`` is caught beside ``JSONDecodeError`` for the same
        reason ``read_active_index_pointer`` catches it: it is a ``ValueError``
        and not a subclass of ``JSONDecodeError``, so a registry holding
        arbitrary bytes -- a partial overwrite, a restored binary -- escaped this
        handler entirely and surfaced as a traceback. Same file, same corruption,
        same remedy; only the first byte differed.

        ``OSError`` is the third, from the read below -- and, one level up,
        from the ``.exists()`` probe that used to sit ahead of the ``try``
        entirely. That docstring claim was false: ``.exists()`` swallows
        ``ENOENT`` but re-raises ``EACCES`` the same way ``Path.is_dir()``
        does, so a *registry file* at mode ``000`` was already covered by the
        read below raising on it, while a *data directory* at mode ``000`` --
        `.exists()` must traverse it to stat the file inside -- escaped
        every reader here, one directory level up from where this docstring
        said the gap was closed (issue #205's Class 1c, measured against the
        real CLI). Because *every* reader reaches this method both escaped all
        of them at once -- ``project.list`` and every project-scoped MCP tool
        as ``[Errno 13] Permission denied``, ``theurian project list`` and
        ``project status`` as a traceback with an empty stdout. Both are
        separated from the parse failures only so the message can say which
        happened; the cure is the same, because a file this process cannot
        open is a file whose ids it cannot know.
        """
        try:
            exists = self.path.exists()
        except OSError as exc:
            raise ProjectError(
                f"{self.path} cannot be opened: {exc}",
                remedy=registry_deletion_remedy(
                    self.path,
                    _arm_for_a_refused_registry(
                        exc, when_refused=RegistryFailureArm.DIRECTORY_UNREADABLE
                    ),
                ),
            ) from exc
        if not exists:
            return {}
        try:
            loaded = json.loads(self.path.read_text(encoding="utf-8"))
        except OSError as exc:
            raise ProjectError(
                f"{self.path} cannot be opened: {exc}",
                remedy=registry_deletion_remedy(
                    self.path,
                    _arm_for_a_refused_registry(
                        exc, when_refused=RegistryFailureArm.FILE_UNREADABLE
                    ),
                ),
            ) from exc
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ProjectError(
                f"{self.path} cannot be read as JSON: {exc}",
                remedy=registry_deletion_remedy(self.path, RegistryFailureArm.UNPARSABLE),
            ) from exc

        if not isinstance(loaded, dict):
            raise ProjectError(
                f"{self.path} must hold a JSON object mapping project ids to registrations, "
                f"not a {type(loaded).__name__}.",
                remedy=registry_deletion_remedy(self.path, RegistryFailureArm.UNPARSABLE),
            )
        return loaded

    def load(self) -> dict[str, dict[str, str]]:
        """The registry contents, project id to registration fields.

        Validated at read time rather than trusted. The file is annotated
        ``dict[str, dict[str, str]]``, but it lives in the user's home directory
        and nothing stops a hand edit from breaking that shape -- and a
        malformed entry used to escape as a bare ``AttributeError`` at whichever
        caller first called ``.get()`` on it, rather than the ``{error, remedy}``
        contract every other failure in this module honours.

        That validation covers the *type* of an entry but, until this method also
        checked ``rootPath``, not its *contents*: every reader of this method's
        return value indexes straight into ``entry["rootPath"]``, so an entry a
        dict but missing the key reached one of them as the same bare
        ``AttributeError``-shaped escape this docstring says is closed, and an
        entry holding ``""`` did not raise at all -- it resolved to the calling
        process's current working directory, so a corrupt registry answered as
        the wrong project rather than as an error.

        **A malformed entry is skipped, not fatal to every other one.** This
        used to raise on the first entry that failed either check, which meant
        one hand-edit anywhere in the file made every registered project
        unreadable -- ``theurian project list``, every MCP tool's project
        resolution, and `setup_steps`'s registry scan all failed together, on a
        machine that had done nothing to the *other* registrations. The registry
        is per-user and one daemon serves many projects at once (module
        docstring, ADR-0002); refusing all of them for a defect in one repeats,
        at the whole-machine scale, exactly the failure `IndexUnreadableError`
        exists to avoid at the one-project scale: a single bad row answering for
        rows that are fine.

        The decision this reverses -- and the risk that made it look safe --
        was never really the protection it claimed. The refusal always paired
        with one remedy, :func:`registry_deletion_remedy`: delete the file and
        re-register *everything*. That remedy destroys the very "this id is
        already spoken for" information the whole-file refusal was meant to
        preserve, so it never actually stopped an id from being reclaimed --
        it only stopped anyone from using the registry at all until they had
        thrown away the fact along with the file. What genuinely protects
        against reclaiming a malformed entry's id is kept, and moved to where
        it can still see the entry that failed here: :meth:`register` checks
        :meth:`_raw_entries` directly, so an id that exists in the file --
        readable or not -- is still refused to a new root, and
        :meth:`unregister` can still remove the one entry that is actually
        broken, which ``theurian project register`` names as the escape.

        **What tolerance here costs, and where it is paid.** Skipping an entry
        makes it absent, and absent is a *claim*: for a question keyed by id it
        is the true one, but for a question keyed by root path it is a guess, and
        :meth:`ids_for_root` refuses to make it rather than answering "not
        registered" about a registration it simply cannot read. Callers that
        name an id -- ``theurian project list``, ``project unregister``,
        ``setup``'s registry scan, every MCP tool -- are served by this method
        and keep working.

        Reporting the skipped ids is :meth:`unreadable_ids`, and something a user
        looks at has to call it: an entry silently missing from ``project list``
        is a project that vanished with nothing said.

        **What an id in this result promises.** It resolves to a root path, and
        it is an id every project-scoped surface will accept -- because it is
        checked by the same :class:`ProjectId` construction those surfaces
        perform (:func:`_usable_id`). Until the key was checked, the second half
        was false: ``project.list`` published ids that every tool behind it then
        refused, and ``project unregister`` refused them too.
        """
        return {
            project_id: entry
            for project_id, entry in self._raw_entries().items()
            if entry_root(entry) is not None and _usable_id(project_id)
        }

    def unreadable_ids(self) -> tuple[str, ...]:
        """Ids present in the file that :meth:`load` skips, sorted.

        Public because a skipped entry that nothing reports is a project that
        disappeared in silence. ``theurian project list`` counted only what it
        could read, so the one command a user runs to find out what is registered
        was also the command that hid the problem -- and the id it hid is the
        argument ``theurian project unregister`` needs. A remedy naming an id
        that no surface prints is not a remedy.
        """
        return _unreadable_ids(self._raw_entries())

    def ids_for_root(self, root: Path) -> tuple[str, ...]:
        """Every id this root is registered under, sorted.

        Sorted because the answer reaches an error message, and a message that
        names ids in JSON-file order is a message that reads differently on two
        machines holding the same registry.

        Raises:
            ProjectError: If any entry in the file names no root -- not only one
                that might plausibly belong to this root.
            ProjectError: If an entry naming *this* root is keyed by an id no
                consumer accepts.

        **Why one rootless entry refuses every root, when :meth:`load` tolerates
        it.** An entry names no root exactly when :func:`entry_root` returns
        ``None``. So "is that unreadable entry this directory's registration?"
        has no answer: the field that would settle it is the field that is
        missing. Per-root decidability is not expensive here, it is unavailable,
        so the honest refusal is the broad one.

        **And why an unusable *key* refuses only its own root.** There the field
        that decides is present and valid: the entry says exactly which
        directory it belongs to, so every other directory is answerable and gets
        its answer. Refusing them too is what made this the milestone's own
        regression -- ``resolve_context`` began consulting the registry by root
        path, so one hand-edited key stopped every root-anchored command on the
        machine, in repositories whose registrations were intact. What is left is
        the refusal that is actually undecidable: this root is registered, and
        under an id nothing can address, so neither serving it nor deriving a
        fresh id from the directory name is honest -- the derived id may belong
        to a different project, which is the misrouting the paragraph below
        describes.

        Answering ``()`` anyway is what made per-entry tolerance dangerous.
        :meth:`id_for_root` turned it into ``None``, ``resolve_context`` read
        ``None`` as "never registered" and fell back to
        :func:`derive_project_id` -- and a project registered under a
        disambiguated id *because its derived default collided* was then
        addressed by the id belonging to the project it collided with.
        Reproduced end to end: commands run in one working tree wrote knowledge
        into a different, readable, still-registered project, and nothing said so
        (SEC-13). Whole-file rejection was loud about this; tolerance was not.
        The regression was the silence, not the tolerance, so what is restored
        here is only the loudness.

        Deliberately narrower than the whole-file rejection it replaces, but not
        free, and the boundary is worth stating exactly because the tempting
        summary of it is wrong. What refuses is every question keyed by a root
        path: resolving the project for the working directory, and
        :meth:`register`, which asks this to enforce "one root, one id" and so
        inherits the refusal even when given an explicit id. What keeps working
        is every question keyed by an *id*, because those go through
        :meth:`load`: ``theurian project list``, ``theurian project unregister``
        -- the command that fixes it -- ``setup``'s registry scan, and every MCP
        tool, so the daemon carries on serving every readable project rather than
        the whole machine stopping for one hand-edited line.

        Registration being blocked machine-wide until the entry is removed is
        accepted rather than worked around. It could be allowed for a re-run that
        creates no new (root, id) pairing, but that buys almost nothing: the
        plain ``theurian project register`` resolves its context from the working
        directory first and refuses there regardless, so the exception would only
        ever apply to the ``--project-id`` form, in exchange for a special case
        whose safety argument is harder to check than the refusal it removes.
        """
        entries = self._raw_entries()
        rootless = tuple(sorted(pid for pid, entry in entries.items() if entry_root(entry) is None))
        if rootless:
            raise ProjectError(
                f"Cannot say which project {root.resolve()} belongs to: {self.path} holds "
                f"entries that cannot be read ({', '.join(rootless)}). An unreadable entry "
                f"is one that names no root path, so there is no way to tell whether one of "
                f"them is this directory's registration -- and treating this directory as "
                f"unregistered would address it by the id derived from its name, which may "
                f"already belong to a different project.",
                remedy=(
                    f"Remove the unreadable entries: {unregister_commands(rootless)}. "
                    f"`theurian project list` shows them under `unreadable`. Meanwhile "
                    f"anything that names a project id still works, including every daemon "
                    f"tool; anything that resolves the project from the current directory, "
                    f"`theurian project register` included, refuses rather than guesses."
                ),
            )

        wanted = root.resolve()
        named = tuple(sorted(pid for pid, entry in entries.items() if entry_root(entry) == wanted))

        # Only this root's own entries are consulted, which is what keeps the
        # refusal local: an unusable key elsewhere in the file says nothing about
        # this directory, because its entry says which directory it does mean.
        unusable = tuple(pid for pid in named if not _usable_id(pid))
        if unusable:
            raise ProjectError(
                f"{wanted} is registered under an id no command accepts "
                f"({', '.join(repr(pid) for pid in unusable)}): a project id must be lowercase "
                f"kebab-case. This directory is registered, so treating it as unregistered "
                f"would address it by the id derived from its name, which may already belong "
                f"to a different project.",
                remedy=(
                    f"Remove the entry: {unregister_commands(unusable)}, then run "
                    f"`theurian project register` here again. `theurian project list` shows "
                    f"it under `unreadable`. Every other project on this machine is "
                    f"unaffected."
                ),
            )
        return named

    def id_for_root(self, root: Path) -> ProjectId | None:
        """The single id this root is registered under, or ``None``.

        Root path, not directory name, is what identifies a registration: the
        name is only how an id gets *proposed*. Callers resolving "which project
        am I in" must ask this before falling back to :func:`derive_project_id`,
        or a project registered under a disambiguated id would be addressed by
        the colliding default instead.

        ``None`` therefore means one thing only: every entry in the file was
        readable and none of them named this root. "There is an entry here that
        cannot be read" is a different answer and raises instead
        (:meth:`ids_for_root`), because a caller cannot distinguish the two from
        ``None`` and the fallback it would reach for is the misrouting itself.

        Raises:
            ProjectError: If more than one id names this root. :meth:`register`
                refuses to create that state, so reaching it means the registry
                was edited by hand -- and picking the first match would answer a
                question the registry no longer has one answer to, sending the
                CLI to one project while every agent naming the other id reads
                an empty one.
            ProjectError: If any entry in the file is unreadable, via
                :meth:`ids_for_root`.
        """
        found = self.ids_for_root(root)
        if len(found) > 1:
            raise ProjectError(
                f"{root.resolve()} is registered under more than one project id "
                f"({', '.join(found)}), so which project this directory is cannot be answered.",
                remedy=(
                    f"Keep one: run `theurian project unregister <id>` for each of the "
                    f"others, {_REBUILD_STATE_CLAUSE}"
                ),
            )
        return ProjectId(found[0]) if found else None

    def register(self, project: Project) -> bool:
        """Add or update a registration.

        Returns:
            ``True`` if anything changed. Re-registering an identical project is
            a no-op, so setup can run repeatedly without churn (FR-L2).

        Raises:
            ProjectError: If the id is already registered to a different root,
                or this root is already registered under a different id, or the
                id already has an entry that cannot be read, or *any* id does
                (via :meth:`ids_for_root`).

        ``registeredAt`` records when the project was *first* registered and is
        preserved across re-registration. Refreshing it would make every re-run
        report a change and defeat the idempotence FR-L2 requires.

        **Why a collision is refused rather than resolved.** Ids default to the
        directory name, and directory names repeat: ``team-one/api`` and
        ``team-two/api`` both propose ``api``. This method used to overwrite,
        which silently re-pointed the id at the newer root — and since every MCP
        tool resolves a project by asking this registry for a root path, an agent
        working in ``team-one`` that asked for ``api`` was served ``team-two``'s
        knowledge, with no error and nothing in the answer saying which
        repository it came from (SEC-13).

        Picking a suffix automatically would be worse than either: an already
        configured agent keeps naming ``api`` and would silently follow the id to
        whichever project kept it. So the collision is surfaced to the person who
        can actually decide, at the one moment they are present.

        **And the mirror image, which is worse.** Refusing "one id, two roots"
        while permitting "one root, two ids" left the documented escape from a
        collision — ``--project-id`` — walking into a second, quieter failure. A
        user who wanted a clearer name got a *duplicate* registration rather than
        a rename, and the new id addressed a project with no knowledge in it:
        canonical rows and index chunks are stamped with the id in force when
        they were written, and ``migrate apply`` is idempotent, so nothing
        restamps them. Every search under the new id answered ``count: 0`` while
        reporting ``indexed: true``, and ``theurian index status`` said there was
        nothing to do. Nothing short of deleting the state database recovered,
        and nothing said so — which is why the refusal carries that instruction.

        **Checks the raw file, not :meth:`load`'s validated result.** ``load``
        now skips a malformed entry rather than raising for it (see its
        docstring), and a skipped entry must not read as an *available* id: a
        new registration that only checked ``load`` would silently overwrite
        whatever the broken entry held, which is the exact misrouting the
        collision refusal above exists to prevent — just reached through an
        unreadable entry instead of a readable one. So this method asks
        :meth:`_raw_entries` whether the id is present at all, and refuses a
        malformed hit with its own remedy rather than folding it into either
        collision message above, both of which assume a readable ``rootPath``
        to report.

        That check covers *this* id. Some other id's unreadable entry is the
        mirror image and is refused by :meth:`ids_for_root` below: it may be this
        very root's registration, and registering over it would produce the "one
        root, two ids" duplicate the paragraph above exists to prevent -- an
        addressable, empty project -- with the difference that nothing could
        report the clash, because the entry that clashed was the unreadable one.
        The specific check runs first so the more precise remedy wins when both
        apply.
        """
        raw = self._raw_entries()
        existing_raw = raw.get(project.project_id.value)
        root = Path(project.root_path).resolve()

        existing: dict[str, Any] | None = None
        if existing_raw is not None:
            # The same predicate `load` and `unreadable_ids` partition on, rather
            # than a third hand-rolled copy of it: "this id's entry is
            # unreadable" and "this id is one of the ids `project list` reports
            # as unreadable" have to be the same statement, or the remedy below
            # names an id the user cannot see.
            registered_root = entry_root(existing_raw)
            if registered_root is None:
                raise ProjectError(
                    f"Project id {project.project_id.value!r} already has an entry in "
                    f"{self.path} that cannot be read, so registering it now would silently "
                    f"discard whatever that entry held.",
                    remedy=(
                        f"Run `theurian project unregister {project.project_id.value}` to "
                        f"remove the unreadable entry, then register again."
                    ),
                )
            existing = existing_raw
            if registered_root != root:
                raise ProjectError(
                    f"Project id {project.project_id.value!r} is already registered to "
                    f"{registered_root}, so it cannot also name {project.root_path}.",
                    remedy=(
                        "Register this one under a distinct id: "
                        "`theurian project register --project-id <id>`."
                    ),
                )

        held = tuple(pid for pid in self.ids_for_root(root) if pid != project.project_id.value)
        if held:
            raise ProjectError(
                f"{root} is already registered as {', '.join(held)}, so it cannot also be "
                f"registered as {project.project_id.value!r}. A project id is an identity, "
                f"not a label.",
                remedy=(
                    f"To rename it, run "
                    f"{', '.join(f'`theurian project unregister {pid}`' for pid in held)}, "
                    f"{_REBUILD_STATE_CLAUSE}"
                ),
            )

        entry = {
            "rootPath": project.root_path,
            "repositoryUrl": project.repository_url or "",
            "defaultBranch": project.default_branch,
            "knowledgeDirectory": str(project.knowledge_directory),
            "registeredAt": (
                existing["registeredAt"]
                if existing and "registeredAt" in existing
                else project.registered_at.isoformat()
            ),
        }
        if existing == entry:
            return False

        # Built from the raw file, not from `load`'s validated subset, so
        # registering one id never erases some *other* id's malformed entry --
        # that entry stays exactly as broken as it was until someone names it,
        # via `register` or `unregister`, rather than being deleted as a side
        # effect of an unrelated write.
        updated = dict(raw)
        updated[project.project_id.value] = entry
        self._write(updated)
        return True

    def unregister(self, project_id: str) -> bool:
        """Remove one registration, whether or not it was readable.

        Reads :meth:`_raw_entries` rather than :meth:`load`, on purpose: the
        entry ``load`` would skip for being malformed is exactly the one this
        method has to be able to remove -- it is the remedy :meth:`register`
        names when an id is already held by an entry that cannot be read.

        **A raw key, not a :class:`ProjectId`.** The parameter used to be one,
        which asserted about the file a property the file does not have: keys are
        whatever a hand edit left behind, and the entry keyed ``"Team One/API"``
        was refused by the very command whose whole purpose is removing entries
        nothing else can serve. Requiring a valid id here made the terminal
        command of every remedy chain unable to name its own argument -- so the
        listing said "remove this", and removing it said "check the id with the
        listing".

        Nothing is loosened by that: this method only ever deletes, and deleting
        a key that is not a valid id is precisely the operation being asked for.
        Writes still go through :meth:`register`, which takes a
        :class:`~theurian.domain.project.Project` and therefore a validated id,
        so no unusable key can enter the file through Theurian.
        """
        raw = self._raw_entries()
        if project_id not in raw:
            return False
        remaining = {pid: entry for pid, entry in raw.items() if pid != project_id}
        self._write(remaining)
        return True

    def _write(self, entries: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        temporary = self.path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(entries, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        os.replace(temporary, self.path)  # noqa: PTH105 -- atomic replace


@dataclass(frozen=True, slots=True)
class BuildProvenance:
    """This installation's record of the derived state it built (ADR-0004, SEC-7).

    The class this closes: everything under `.theurian/state/` -- the active
    pointers and the SQLite databases they name -- is derived and git-ignored
    (ADR-0004), but nothing stops a repository contributor from force-adding a
    doctored copy (`git add -f`, past the ignore) and a victim who clones (or
    downloads the ZIP/tarball) + `project register` + serves over MCP, *without
    ever running `migrate apply`*, from being served the attacker's bytes. The
    trust was filesystem presence: a database file whose name matched the
    pointer's hash was opened and read as authoritative.

    Presence cannot be the discriminator, because `active.json`'s ``stateHash``
    binds the migration *set*, not the database bytes, and the database filename
    is derived from that hash (:meth:`StateHash.database_filename`). A
    self-consistent doctored pair -- status flipped, rows injected, every
    integrity record recomputed to match -- has no internal inconsistency to
    catch. The only thing an attacker who authored the repository cannot forge is
    whether *this installation* built the artifact, so provenance is recorded
    here, out of the repository tree, in ``THEURIAN_DATA_DIR`` beside the
    registry -- the one place a repository contributor cannot write to.

    **Delivery-independent by construction.** The discriminator is "did this
    install build it", not "is it tracked by Git", so it refuses a clone, a ZIP
    download and a repackaged tarball alike -- a `git ls-files` probe would catch
    only the clone, since repackaging strips the tracking metadata and leaves the
    file present-but-untracked.

    **Four artifact families, one record.** ``state`` and ``index`` are the
    canonical state database and the retrieval index; ``findings`` is the
    review-finding store ``theurian findings build`` writes (ADR-0029 phase-2);
    ``review`` is the review search store ``theurian review build`` writes
    (ADR-0030 slice 3) -- named for the **filename infix**, not for the store, as
    ``findings`` keys ``theurian-findings-`` and ``index`` keys
    ``theurian-index-``; see :meth:`record_review`, whose docstring records what a
    family called ``review_search`` would cost T-19. All four sit under
    `.theurian/state/`, all four are
    git-ignored, and all four are therefore force-addable by a repository
    contributor -- so each later family inherits the class this record closes
    rather than a milder version of it: a clone shipping a fabricated
    ``theurian-findings-local.sqlite`` would otherwise have `review.findings` serve
    its rows as the repository's own review history, and a fabricated
    ``theurian-review-local.sqlite`` would have a review search serve comments
    nobody ever wrote as the project's own review evidence. Both are keyed by a
    constant -- :data:`FINDINGS_STORE_ID`, :data:`REVIEW_SEARCH_STORE_ID` -- rather
    than by a per-build id, because each store is a wholesale rebuild under one
    name: there is no build id to record, and "this installation has built this
    project's store" is the whole question.

    **Keyed by resolved root path, not project id.** The resolution layer that
    enforces the check holds the root (:attr:`ProjectPaths.root`) but not always
    the id; the root is the physical location the victim's own machine chose for
    the checkout; and two directories that would derive the same id from their
    name (:func:`derive_project_id` collides on directory name) stay distinct
    here.

    **What it does not close, recorded rather than hidden.** The record vouches
    for a *hash*, not for the database bytes -- verifying bytes would mean hashing
    the whole database on every query. So an attacker who can replace a database
    *after* this install built the matching hash (a tracked sidecar overwriting a
    local build on the next `git pull`, or local filesystem write access) is out
    of scope for this control and left to the read-back integrity guards (#30
    PR2) and the schema-version and corruption checks. The primary vector -- a
    build this installation never produced -- is closed outright, because no
    record exists for it at all.
    """

    path: Path

    @classmethod
    def default(cls, data_dir: Path | None = None) -> BuildProvenance:
        base = data_dir or Path(os.environ.get("THEURIAN_DATA_DIR", Path.home() / ".theurian"))
        return cls(path=base / "provenance.json")

    @classmethod
    def for_registry(cls, registry: ProjectRegistry) -> BuildProvenance:
        """The provenance store beside a registry, in the same data directory.

        Derived from the registry rather than re-reading ``THEURIAN_DATA_DIR`` so
        the serve-side check reads exactly the directory the registry it was
        handed lives in, however that directory was resolved. The build side
        (``migrate apply``, ``index build``, ``findings build``) reaches the same
        file through :meth:`default`, because all three resolve the same
        environment variable.
        """
        return cls(path=registry.path.parent / "provenance.json")

    def _load(self) -> dict[str, Any]:
        """The recorded builds, or an empty map on any failure to read them.

        **Fail closed.** A provenance file this process cannot read or parse
        vouches for nothing, so every artifact is refused until a local build
        rewrites it. The file is derived and lives in the user's own data
        directory, so `migrate apply` is always the cure and losing it costs only
        a re-apply -- the same trade the registry and the state pointer make.

        **The ``exists`` probe is inside the ``try``, swept here with the three
        pointer readers under ``.theurian/state/``** (round one, MEDIUM-4). Same
        shape: ``pathlib`` re-raises ``EACCES`` rather than answering "not
        there", so an unreadable *data* directory would raise past the ``except``
        written for it. Unreachable today -- every caller resolves the registry
        out of the same directory first, and that read refuses earlier -- so this
        is the one member of the family that is a guard rather than a fix, moved
        because a class swept at three of four members is a class whose next
        edit reopens it. The **data**-directory mode axis is otherwise untested
        here: ``.theurian/state``'s is driven end to end, this one is not.
        """
        try:
            if not self.path.exists():
                return {}
            loaded = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeDecodeError):
            return {}
        return loaded if isinstance(loaded, dict) else {}

    def _built(self, raw: dict[str, Any], root: Path, kind: str) -> list[str]:
        entry = raw.get(str(root.resolve()))
        recorded = entry.get(kind) if isinstance(entry, dict) else None
        if not isinstance(recorded, list):
            return []
        return [value for value in recorded if isinstance(value, str)]

    def has_state(self, root: Path, state_hash: str) -> bool:
        """Whether this installation built the canonical state named by ``state_hash``."""
        return state_hash in self._built(self._load(), root, "state")

    def has_index(self, root: Path, index_build_id: str) -> bool:
        """Whether this installation built the retrieval index named by ``index_build_id``."""
        return index_build_id in self._built(self._load(), root, "index")

    def has_findings(self, root: Path, findings_store_id: str) -> bool:
        """Whether this installation built the review-finding store ``findings_store_id``."""
        return findings_store_id in self._built(self._load(), root, "findings")

    def has_review(self, root: Path, store_id: str) -> bool:
        """Whether this installation built the review search store ``store_id``."""
        return store_id in self._built(self._load(), root, "review")

    def record_state(self, root: Path, state_hash: str) -> None:
        """Record that this installation built the canonical state ``state_hash``."""
        self._record(root, "state", state_hash)

    def record_index(self, root: Path, index_build_id: str) -> None:
        """Record that this installation built the retrieval index ``index_build_id``."""
        self._record(root, "index", index_build_id)

    def record_findings(self, root: Path, findings_store_id: str) -> None:
        """Record that this installation built the review-finding store ``findings_store_id``."""
        self._record(root, "findings", findings_store_id)

    def record_review(self, root: Path, store_id: str) -> None:
        """Record that this installation built the review search store ``store_id``.

        Named for the **filename infix**, not for the store: the family key is
        ``review`` because the artifact is ``theurian-review-<id>.sqlite``, exactly
        as ``findings`` keys ``theurian-findings-`` and ``index`` keys
        ``theurian-index-``. That is a convention a record depends on --
        ``test_threat_model_t19_claims.py`` derives ``theurian-{family}-`` from
        these method names and asserts the threat model names the artifact -- so a
        family called ``review_search`` here would have T-19 vouching for a
        ``theurian-review_search-`` that nothing writes.
        """
        self._record(root, "review", store_id)

    def _record(self, root: Path, kind: str, value: str) -> None:
        """Append one built artifact to a root's record, atomically.

        Accumulates rather than replaces: a prior build's state may still be
        served (a pinned snapshot, a not-yet-collected build), so the record
        keeps every hash this installation produced rather than only the latest.
        Read-modify-write with an :func:`os.replace` swap, the same discipline as
        the registry; a lost update under concurrent writers drops a hash and so
        fails closed -- the artifact is refused until the next build re-records
        it -- rather than vouching for one this installation did not build.
        """
        raw = self._load()
        key = str(root.resolve())
        entry_value = raw.get(key)
        entry = dict(entry_value) if isinstance(entry_value, dict) else {}
        built = self._built(raw, root, kind)
        if value not in built:
            built = [*built, value]
        entry[kind] = built
        updated = dict(raw)
        updated[key] = entry
        self._write(updated)

    def _write(self, entries: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        temporary = self.path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(entries, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        os.replace(temporary, self.path)  # noqa: PTH105 -- atomic replace


def verify_state_provenance(
    paths: ProjectPaths, active: ActiveState, provenance: BuildProvenance
) -> None:
    """Refuse canonical state this installation did not build (ADR-0004, SEC-7).

    The serve path's enforcement point for :class:`BuildProvenance`, and its only
    caller is the MCP tools' ``_resolve``. ``theurian index build`` refuses the
    same doctored state one step earlier and by a different route --
    ``cli/index_commands.py``'s ``_require_buildable_state`` calls
    :meth:`BuildProvenance.has_state` directly, because it has to ``_fail`` with a
    CLI exit code rather than raise -- so a database this installation never
    produced influences no served result whatever put it on disk, but this
    function is not what holds the build side.

    The sibling gates, **as of this commit and pinned by nothing**, re-derived
    from ``git grep -nE '\\.has_(state|index|findings|review)\\(' --
    packages/theurian-core/src``, which answers eight lines -- the seven below and
    this function's own check. The call form matters to that search: one of the
    seven reaches the record through ``BuildProvenance.default()`` rather than
    through a ``provenance`` name, so a key written as ``provenance.has_`` would
    answer six and miss it. ``has_state`` in
    ``cli/index_commands.py`` (``index build``) and ``cli/commands.py``
    (``migrate apply``, which discards rather than refuses); ``has_index`` in
    ``cli/commands.py`` (the withdrawal purge's copy-forward, answering
    ``UNTRUSTED_SOURCE_INDEX``), ``mcp/search.py`` (the ranked path standing
    aside) and ``application/index_secret_scan.py`` (the scan verdict reading
    ``UNRECORDED``); ``has_findings`` in ``mcp/tools.py`` (``review.findings``);
    and ``has_review`` in ``mcp/tools.py`` (``review.search``).

    **The two review families have one gate each and no build-side twin, which is
    a difference rather than a gap.** ``index build`` and ``migrate apply`` gate
    because each *consumes* an artifact this record vouches for -- an index built
    over a doctored state would launder it. ``findings build`` consumes nothing
    derived: it reads git history and rebuilds the store wholesale, so there is no
    delivered artifact for it to be fooled by. ``review build`` reads the evidence
    files under ``.theurian/review/``, and those are **source rather than derived**
    (ADR-0030 decision 3) -- nothing here vouches for them, deliberately, and what
    that costs is recorded as threat-model T-24 rather than left to be inferred
    from this list. What both build commands do instead is *record*, at
    ``cli/findings_commands.py``'s ``record_findings`` call and at
    ``cli/review_commands.py``'s ``record_review`` call, and that is what makes the
    store each has just built servable.

    This list is prose, so a gate added or moved will not
    redden anything -- an earlier revision of this docstring named the wrong
    function for the build path and stayed green for a milestone, a later one
    enumerated two families after the third had shipped, and the one before this
    enumerated three after the fourth had, while also omitting
    ``index_secret_scan.py``'s ``has_index`` call. Three drifts in a row is the
    argument for the search
    above being written down beside the list: re-derive it from ``git grep``
    rather than trusting it, and treat a disagreement as this sentence being stale
    rather than the code being wrong.

    Raises:
        ProjectError: If no out-of-tree record shows this installation built the
            state the in-tree pointer names. The message is
            :data:`_UNBUILT_STATE_REFUSAL` and the cure is
            :data:`UNBUILT_STATE_REMEDY` -- both constants, so neither can quote a
            cell, a pointer field, or a path on the machine serving the request.
    """
    if not provenance.has_state(paths.root, str(active.state_hash)):
        raise ProjectError(_UNBUILT_STATE_REFUSAL, remedy=UNBUILT_STATE_REMEDY)
