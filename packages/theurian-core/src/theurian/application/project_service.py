"""Project registration, ``.theurian/`` initialisation, and state resolution.

The registry is per-user (``~/.theurian/projects.json``) rather than per-project,
because one daemon serves many projects (ADR-0002). Everything under a project's
``.theurian/`` belongs to the project and travels with it in Git.
"""

from __future__ import annotations

import json
import os
import posixpath
import re
import shlex
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Final

from theurian.application.authorization import decode_sensitivities, encode_sensitivities
from theurian.domain.enums import Sensitivity
from theurian.domain.errors import InvalidIdentifierError, TheurianError
from theurian.domain.identifiers import ProjectId
from theurian.domain.migration import LoadedMigrations
from theurian.domain.ports import Clock
from theurian.domain.project import (
    DEFAULT_KNOWLEDGE_DIRECTORY,
    GITIGNORE_BLOCK_END,
    GITIGNORE_BLOCK_START,
    GITIGNORE_SECTIONS,
    Project,
)
from theurian.domain.state import ActiveState, StateHash, compute_state_hash, state_inputs_from
from theurian.security.project_config import PROJECT_CONFIG_FILE

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


def _registry_reset_remedy(path: Path) -> str:
    """The remedy for a registry file whose *set of ids* cannot be trusted.

    Reached only when the top level of the file is not what every reader here
    assumes -- unparsable JSON, or JSON that is not an object -- because that is
    the one failure this module cannot recover from entry by entry: without a
    dict of ids to iterate, there is no way to say which registrations are fine
    and which are not. A malformed *entry* is a narrower problem with its own,
    narrower remedy: see :meth:`ProjectRegistry.load`,
    :meth:`ProjectRegistry.ids_for_root` and :meth:`ProjectRegistry.register`.
    """
    return (
        f"Delete {path} and re-register each project with `theurian project register`; "
        f"it is derived and holds nothing that is not also recoverable from each "
        f"project's own .theurian/."
    )


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


def _unregister_commands(project_ids: tuple[str, ...]) -> str:
    """``theurian project unregister`` for each id, quoted so it can be typed.

    An unreadable id is whatever a hand edit left behind, spaces and quotes
    included, and this string is a command a user copies. Unquoted,
    ``theurian project unregister Team One/API`` is three arguments to a command
    that takes one -- so the remedy for the id that broke the registry was itself
    unrunnable, on every surface that printed it.
    """
    return ", ".join(f"`theurian project unregister {shlex.quote(pid)}`" for pid in project_ids)


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


def _contain(root: Path, path: Path) -> Path:
    """Prove ``path`` stays inside ``root``, or refuse with the escape remedy.

    The one containment chokepoint under a project's ``.theurian``:
    :meth:`ProjectPaths._contained` routes every path the class hands out through
    here, and :func:`initialize_project` routes every directory and file it
    creates. A committed symbolic link at any level -- ``.theurian`` itself (also
    refused earlier, in :meth:`ProjectPaths.of`), a ``knowledge``/``state``
    directory a clone tracked, or a leaf a clone force-added past the ADR-0004
    ignore -- cannot redirect a read or write outside the working tree the clone
    gave the user (#237, T-5). That closes the *authored-symlink* class: a link
    delivered as tracked repository content. The derived-cache face the ``ingest``
    manifest opens through ``.theurian/cache`` (git-ignored state a repository
    should not carry at all) is a different root cause -- the GHSA-266v
    derived-state-trust class -- and is tracked separately as #394, not closed
    here.

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
        raise ProjectError(
            f"{path} does not resolve to a location inside {resolved_root}: {exc}",
            remedy=KNOWLEDGE_DIR_ESCAPE_REMEDY,
        ) from exc
    if not resolved.is_relative_to(resolved_root):
        raise ProjectError(
            f"{path} resolves outside the project root {resolved_root}, so a read or write "
            f"through it would land outside the working tree.",
            remedy=KNOWLEDGE_DIR_ESCAPE_REMEDY,
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
        sweep. The mechanism, the anchoring, and the class it closes (the
        authored-symlink #237/T-5 class, not the derived-cache #394 face) are all
        recorded on :func:`_contain`.
        """
        return _contain(self.root, path)

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

    def index_for(self, index_build_id: str) -> Path:
        """Where one index build lives.

        The prefix matters. Index builds share a directory with canonical state
        databases, and a glob that could not tell them apart would hand a
        retrieval index to the canonical store.

        Containment is checked because the id reaching here comes from
        `active-index.json` — a derived, git-ignored, unsigned file that any
        local process can edit. `../` in it resolves outside the project, and
        SEC-7 covers every path, not only the ones that look like user input.

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

        # Resolving succeeded, so the returned path is one the OS will accept --
        # a caller's later `is_file()` cannot raise the error just converted.
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
        try:
            escapes = not knowledge_dir.resolve().is_relative_to(resolved)
        except (OSError, ValueError) as exc:
            # A symlink cycle (`ELOOP`) or a name the platform rejects makes
            # `resolve` raise rather than answer a location. Neither is a
            # `TheurianError`, and a join that will not resolve to a place inside
            # the project is refused for the same reason one that resolves
            # outside is: nothing derived from it can be trusted to stay inside.
            raise ProjectError(
                f"{directory} does not resolve to a location inside {resolved}: {exc}",
                remedy=KNOWLEDGE_DIR_ESCAPE_REMEDY,
            ) from exc
        if escapes:
            raise ProjectError(
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
    """
    created: list[str] = []

    for relative in INITIAL_DIRECTORIES:
        directory = _contain(paths.root, paths.knowledge_dir / relative)
        if not directory.exists():
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

    Returns:
        ``(changed, rendered_block)``.

    Raises:
        ProjectError: The markers do not delimit exactly one block, as
            :func:`locate_gitignore_block` describes.
    """
    block = render_gitignore_block()
    gitignore = root / ".gitignore"
    existing = gitignore.read_text(encoding="utf-8", newline="") if gitignore.exists() else ""

    span = locate_gitignore_block(existing, gitignore)
    if span is not None:
        start, end = span
        if existing[start:end] == block:
            return False, block
        updated = existing[:start] + block + existing[end:]
    else:
        separator = "" if existing.endswith("\n") or not existing else "\n"
        updated = f"{existing}{separator}\n{block}\n" if existing else f"{block}\n"

    gitignore.write_text(updated, encoding="utf-8", newline="")
    return True, block


def resolve_state_hash(loaded: LoadedMigrations, schema_version: int) -> StateHash:
    """Compute the state hash for a loaded migration set (ADR-0016)."""
    return compute_state_hash(
        state_inputs_from(loaded.migration_set, loaded.content_checksums, schema_version)
    )


def read_active_state(paths: ProjectPaths) -> ActiveState | None:
    """Read the active state pointer, or ``None`` if there is none.

    Every way of failing to interpret the file lands on one ``ProjectError``
    carrying one remedy, because the file is derived and one cure covers all of
    them. Catching only the parse failures left the other two escaping raw, and
    the shape they escaped in was the same each time -- an OS-level string with
    no next action, at whichever surface asked:

    - ``UnicodeDecodeError`` is a ``ValueError`` and is not a subclass of
      ``JSONDecodeError``, so a pointer holding arbitrary bytes reached every MCP
      tool as ``'utf-8' codec can't decode byte 0xb9 in position 15``. Its
      sibling :func:`read_active_index_pointer` had caught this for a year; the
      same line eight functions up had not.
    - ``OSError`` covers the file the process cannot open at all -- mode ``000``,
      a directory in its place, a state directory whose permissions changed --
      which reached the same tools as ``[Errno 13] Permission denied`` naming the
      absolute path.

    ``Path.exists`` above is deliberately left as it is: it swallows the stat
    failure and answers ``False``, which would report "no state" for a project
    that has one. It does not, in practice, get the chance to -- an unreadable
    parent surfaces through ``read_text`` as the ``OSError`` this now converts --
    and narrowing it further would be a guess about which errno means absent.
    """
    pointer = paths.active_pointer
    if not pointer.exists():
        return None
    try:
        return ActiveState.from_json(json.loads(pointer.read_text(encoding="utf-8")))
    except (json.JSONDecodeError, OSError, UnicodeDecodeError, TheurianError) as exc:
        raise ProjectError(f"{pointer} is unreadable: {exc}", remedy=ACTIVE_POINTER_REMEDY) from exc


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

    Never raises. The index is derived (ADR-0004), so every problem here is a
    missing optimisation and the caller answers without one — but it still has
    to be able to say *which* problem, because that is what decides the remedy
    it prints.

    ``indexBuildId`` is required, not merely read. A pointer without one names no
    build, so it is not a usable pointer; accepting it built a path out of an
    empty id and reported `index-file-missing` — "the published index build is
    no longer on disk", about a build that was never named.
    """
    pointer = paths.active_index_pointer
    if not pointer.is_file():
        return ActiveIndexPointer()

    try:
        loaded = json.loads(pointer.read_text(encoding="utf-8"))
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
    temporary = pointer.with_suffix(".json.tmp")
    temporary.write_text(
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
        encoding="utf-8",
    )
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
    """
    active = ActiveState(
        state_hash=state_hash,
        database_filename=state_hash.database_filename,
        migration_count=migration_count,
        updated_at=clock.now().isoformat(),
    )

    paths.state.mkdir(parents=True, exist_ok=True)
    temporary = paths.active_pointer.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(active.to_json(), indent=2) + "\n", encoding="utf-8")
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
        :func:`_registry_reset_remedy` is the only remedy that applies -- and it
        is now attached to all three, rather than to the last alone. This
        docstring already claimed to cover unparsable JSON while that branch
        raised with no remedy at all, which reached the user as an error naming
        no way out, from the one class of registry failure with a completely
        reliable cure.

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
                remedy=_registry_reset_remedy(self.path),
            ) from exc
        if not exists:
            return {}
        try:
            loaded = json.loads(self.path.read_text(encoding="utf-8"))
        except OSError as exc:
            raise ProjectError(
                f"{self.path} cannot be opened: {exc}",
                remedy=_registry_reset_remedy(self.path),
            ) from exc
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ProjectError(
                f"{self.path} cannot be read as JSON: {exc}",
                remedy=_registry_reset_remedy(self.path),
            ) from exc

        if not isinstance(loaded, dict):
            raise ProjectError(
                f"{self.path} must hold a JSON object mapping project ids to registrations, "
                f"not a {type(loaded).__name__}.",
                remedy=_registry_reset_remedy(self.path),
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
        with one remedy, :func:`_registry_reset_remedy`: delete the file and
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
                    f"Remove the unreadable entries: {_unregister_commands(rootless)}. "
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
                    f"Remove the entry: {_unregister_commands(unusable)}, then run "
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
        (``migrate apply``, ``index build``) reaches the same file through
        :meth:`default`, because both resolve the same environment variable.
        """
        return cls(path=registry.path.parent / "provenance.json")

    def _load(self) -> dict[str, Any]:
        """The recorded builds, or an empty map on any failure to read them.

        **Fail closed.** A provenance file this process cannot read or parse
        vouches for nothing, so every artifact is refused until a local build
        rewrites it. The file is derived and lives in the user's own data
        directory, so `migrate apply` is always the cure and losing it costs only
        a re-apply -- the same trade the registry and the state pointer make.
        """
        if not self.path.exists():
            return {}
        try:
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

    def record_state(self, root: Path, state_hash: str) -> None:
        """Record that this installation built the canonical state ``state_hash``."""
        self._record(root, "state", state_hash)

    def record_index(self, root: Path, index_build_id: str) -> None:
        """Record that this installation built the retrieval index ``index_build_id``."""
        self._record(root, "index", index_build_id)

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

    The sibling gates, **as of this commit and pinned by nothing**: ``has_state``
    in ``cli/index_commands.py`` (``index build``) and ``cli/commands.py``
    (``migrate apply``); ``has_index`` in ``cli/commands.py`` and
    ``mcp/search.py``. This list is prose, so a gate added or moved will not
    redden anything -- an earlier revision of this docstring named the wrong
    function for the build path and stayed green for a milestone. Re-derive it
    from ``git grep`` rather than trusting it, and treat a disagreement as this
    sentence being stale rather than the code being wrong.

    Raises:
        ProjectError: If no out-of-tree record shows this installation built the
            state the in-tree pointer names. Carries :data:`UNBUILT_STATE_REMEDY`;
            quotes no cell content, only the state directory's own path.
    """
    if not provenance.has_state(paths.root, str(active.state_hash)):
        raise ProjectError(
            f"The derived knowledge state under {paths.state} was not built by this "
            f"Theurian installation, so it will not be served. It was delivered with the "
            f"project rather than rebuilt here from the Git-tracked migrations, which is "
            f"exactly what an untrusted repository must not be able to do (ADR-0004).",
            remedy=UNBUILT_STATE_REMEDY,
        )
