"""``theurian.security.no_follow`` refuses a link, and the writers use it.

Two halves, and the second is the one a containment sweep cannot see.

``tests/integration/test_contained_path_envelope.py`` proves that every derived
path a writer builds goes through ``_contain``. That is half of the guard, and
containment is *right* to wave through a link whose target is inside the working
tree -- which is exactly the shape that truncated a tracked file in the user's own
checkout at exit 0 (#523's in-tree face, and #394's). What refuses that one is
``O_NOFOLLOW`` inside the write, so the completeness claim for it has to be keyed
on the *open*, not on the path.

**Two keys, because one of them is not the claim.** Both are read out of the
source rather than listed, and both carry their exclusions with a measured
reason:

1. *Every ``os.replace`` caller* -- the atomic publishers by definition. A
   publisher whose temporary is written with a following open, or whose temporary
   it derives itself with ``with_suffix``, is the defect. Five exclusions.
2. *Every function that names a ``ProjectPaths`` helper and opens something for
   writing.* This is the universal the module actually claims, and the first key
   is not it: membership there is an *atomicity* property, so ``ingest_command``
   -- which writes its manifest with no temporary at all -- sat outside it while
   the docstring claimed otherwise (round one, code review M-1). One exclusion.

**The bound on the second key, stated rather than left to an absence.** Two
writers in this class name no ``ProjectPaths`` helper and are covered elsewhere:
``FileSecretStore`` writes under the per-user data directory, driven by
``tests/integration/test_derived_path_symlink_writes.py``; and
``initialize_project`` writes the repository's own ``.gitignore``, reached as
``root / ".gitignore"``. That last one **does** still follow a planted link
(measured), and it is deliberately out of scope here -- ``.gitignore`` is
authored, Git-tracked content, the #237 authored-symlink root cause rather than
this one. The gap is named on the test that would otherwise be read as covering
it.
"""

from __future__ import annotations

import ast
import errno
import functools
import inspect
import os
import sys
from pathlib import Path
from typing import Final

import pytest

from theurian.application.project_service import ProjectPaths
from theurian.security import no_follow
from theurian.security.no_follow import (
    WRITE_FLAGS,
    is_a_symbolic_link_refusal,
    open_without_following_a_link,
    symbolic_link_remedy,
    write_text_without_following_a_link,
)

pytestmark = pytest.mark.unit

_NEEDS_SYMLINKS = pytest.mark.skipif(
    sys.platform == "win32", reason="symlinks need privileges on Windows"
)

#: Root can write through any mode, so a refusal driven by permission bits is not
#: a refusal there -- and offline CI runs as root.
_CANNOT_BE_REFUSED_BY_A_MODE = sys.platform == "win32" or os.geteuid() == 0

#: The tree the source-level guards below read, taken from the **imported**
#: package rather than from this file's own location: a sweep that walked a
#: sibling checkout would report a clean tree while the code under test was
#: somewhere else.
_SOURCE_ROOT: Final = Path(no_follow.__file__).resolve().parents[1]


# -- The mechanism -----------------------------------------------------------


def test_an_ordinary_write_lands_and_reads_back(tmp_path: Path) -> None:
    """The control. Without it every refusal below could be a writer that never writes."""
    target = tmp_path / "pointer.json"

    write_text_without_following_a_link(target, '{"a": 1}\n')

    assert target.read_text(encoding="utf-8") == '{"a": 1}\n'
    assert not target.is_symlink()


@pytest.mark.skipif(_CANNOT_BE_REFUSED_BY_A_MODE, reason="POSIX permission bits, and not as root")
def test_a_created_artefact_is_not_group_or_world_writable(tmp_path: Path) -> None:
    """The creation mode, driven under a umask that would expose the difference.

    ``open(path, "w")`` passes ``0o666``, and under the usual ``022`` umask that
    and ``0o644`` create the same file -- so a test run at the default umask
    cannot tell the two apart, and this one would have passed against the
    ``0o666`` an earlier cut of the writer carried. Set to ``0`` for the duration,
    where ``0o666`` produces a world-writable ``active.json`` any local account
    could repoint (CodeQL ``py/overly-permissive-file``, and the derived-state-
    trust class reached through a permission bit rather than a commit).
    """
    target = tmp_path / "pointer.json"
    previous = os.umask(0)
    try:
        write_text_without_following_a_link(target, "{}")
    finally:
        os.umask(previous)

    assert target.stat().st_mode & 0o022 == 0, (
        f"a derived artefact was created group- or world-writable: "
        f"{target.stat().st_mode & 0o777:04o}"
    )


def test_an_existing_regular_file_is_truncated_not_appended(tmp_path: Path) -> None:
    """``O_TRUNC`` survives the conversion.

    The temporaries these writers build are reused under one fixed name, so a
    second, shorter write that left the first one's tail behind would publish a
    pointer with trailing bytes -- valid JSON followed by garbage, which the
    reader rejects as a corrupt pointer rather than as the bug it is.
    """
    target = tmp_path / "pointer.json"
    target.write_text("a" * 500, encoding="utf-8")

    write_text_without_following_a_link(target, "{}")

    assert target.read_text(encoding="utf-8") == "{}"


@_NEEDS_SYMLINKS
def test_a_link_pointing_outside_the_directory_is_refused_and_its_target_untouched(
    tmp_path: Path,
) -> None:
    """#523's out-of-tree face at the mechanism.

    The victim's bytes are compared, not merely its existence: the defect was a
    write *through* the link, so a fix that unlinked the link and wrote a fresh
    file would satisfy "the link is gone" while destroying nothing -- and one that
    truncated the victim to zero would satisfy "the file still exists".
    """
    victim = tmp_path / "outside" / "victim.txt"
    victim.parent.mkdir()
    victim.write_bytes(b"VICTIM BODY\n")
    target = tmp_path / "state" / "active.json.tmp"
    target.parent.mkdir()
    target.symlink_to(victim)

    with pytest.raises(OSError) as excinfo:
        write_text_without_following_a_link(target, "REPLACED")

    assert is_a_symbolic_link_refusal(excinfo.value)
    assert victim.read_bytes() == b"VICTIM BODY\n"
    assert target.is_symlink(), "the refusal must leave the evidence in place"


@_NEEDS_SYMLINKS
def test_a_link_pointing_inside_the_same_tree_is_refused_too(tmp_path: Path) -> None:
    """The face containment cannot reach, and the reason both halves exist.

    A relative link to a sibling inside the project resolves inside the project,
    so ``_contain`` passes it -- correctly, it is not an escape. It is still a
    write through a link onto a file the operator authored.
    """
    tracked = tmp_path / "runbook.md"
    tracked.write_bytes(b"RUNBOOK\n")
    target = tmp_path / "state" / "active.json.tmp"
    target.parent.mkdir()
    target.symlink_to(Path("..") / "runbook.md")

    with pytest.raises(OSError) as excinfo:
        write_text_without_following_a_link(target, "REPLACED")

    assert is_a_symbolic_link_refusal(excinfo.value)
    assert tracked.read_bytes() == b"RUNBOOK\n"


@_NEEDS_SYMLINKS
def test_a_dangling_link_is_refused_rather_than_creating_its_target(tmp_path: Path) -> None:
    """The shape #371 measured: the link names nothing *yet*.

    Without ``O_NOFOLLOW`` this is the worst member of the family rather than the
    mildest -- ``O_CREAT`` follows the link and *creates* the attacker's file,
    with the mode this open was chosen for, so every permission guarantee the
    caller makes ends up being about a file in somebody else's directory.
    """
    loot = tmp_path / "attacker" / "loot"
    loot.parent.mkdir()
    target = tmp_path / "auth" / "mcp-token"
    target.parent.mkdir()
    target.symlink_to(loot)

    with pytest.raises(OSError) as excinfo:
        write_text_without_following_a_link(target, "s3cret")

    assert is_a_symbolic_link_refusal(excinfo.value)
    assert not loot.exists()


def test_a_directory_at_the_target_is_not_reported_as_a_symbolic_link(tmp_path: Path) -> None:
    """The discriminator has to say *no* to something, or every caller's branch is dead.

    A directory where a file belongs is the other artefact a clone delivers, and
    it must keep reaching the ordinary write-fault remedy: routing it to "remove
    the symbolic link at ..." would send an operator to delete a link that is not
    there.
    """
    target = tmp_path / "pointer.json"
    target.mkdir()

    with pytest.raises(OSError) as excinfo:
        write_text_without_following_a_link(target, "{}")

    assert not is_a_symbolic_link_refusal(excinfo.value)
    assert excinfo.value.errno == errno.EISDIR


@pytest.mark.skipif(_CANNOT_BE_REFUSED_BY_A_MODE, reason="POSIX permission bits, and not as root")
def test_an_unwritable_directory_is_not_reported_as_a_symbolic_link(tmp_path: Path) -> None:
    """The second negative, on the errno an operator's umask actually produces."""
    directory = tmp_path / "state"
    directory.mkdir(mode=0o500)
    try:
        with pytest.raises(OSError) as excinfo:
            write_text_without_following_a_link(directory / "pointer.json", "{}")
    finally:
        directory.chmod(0o700)

    assert not is_a_symbolic_link_refusal(excinfo.value)
    assert excinfo.value.errno in (errno.EACCES, errno.EPERM)


def test_the_flags_are_the_ones_that_refuse(tmp_path: Path) -> None:
    """``O_NOFOLLOW`` is asserted on the constant, because the open cannot report it.

    A build whose flags lost ``O_NOFOLLOW`` writes through every link and raises
    nothing, so no behavioural test on this module's happy path can see the loss;
    the symlink tests above are what catch it, and this is the direct reading
    beside them.
    """
    assert WRITE_FLAGS & os.O_NOFOLLOW
    assert WRITE_FLAGS & os.O_TRUNC
    assert WRITE_FLAGS & os.O_CREAT


def test_the_descriptor_is_closed_when_wrapping_it_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The one arm that leaks a file descriptor if it is deleted.

    ``os.fdopen`` takes ownership only once it returns, so a failure inside it
    leaves the descriptor open and owned by nobody. A daemon that met this on
    every publish would run out of descriptors; nothing else in the suite drives
    it, because nothing makes ``fdopen`` fail by accident.
    """
    captured: list[int] = []
    real_fdopen = os.fdopen

    def refuse(descriptor: int, *args: object, **kwargs: object) -> object:
        captured.append(descriptor)
        raise RuntimeError("wrapping refused")

    monkeypatch.setattr(os, "fdopen", refuse)
    with pytest.raises(RuntimeError):
        write_text_without_following_a_link(tmp_path / "pointer.json", "{}")
    monkeypatch.setattr(os, "fdopen", real_fdopen)

    assert captured, "the descriptor was never opened, so this asserts nothing"
    with pytest.raises(OSError) as excinfo:
        os.close(captured[0])
    assert excinfo.value.errno == errno.EBADF, "the descriptor was still open"


def test_the_opener_hands_back_a_usable_descriptor(tmp_path: Path) -> None:
    """``FileSecretStore.set`` uses the opener rather than the text writer.

    It writes bytes it never wants encoded twice and re-asserts the mode
    afterwards, so it owns the descriptor itself. That path needs its own control.
    """
    target = tmp_path / "mcp-token"

    descriptor = open_without_following_a_link(target, mode=0o600)
    try:
        os.write(descriptor, b"token")
    finally:
        os.close(descriptor)

    assert target.read_bytes() == b"token"
    assert target.stat().st_mode & 0o777 == 0o600


# -- The remedy --------------------------------------------------------------


def test_the_remedy_names_the_file_to_remove_and_a_command_to_run(tmp_path: Path) -> None:
    """A remedy is a cure, not a non-empty string (the recurring finding).

    Asserted as content rather than as truthiness: it must name the artefact to
    act on -- the exact path, because a leaf is what ``O_NOFOLLOW``'s
    final-component bound lets this one name -- and something the reader can
    actually do about it.
    """
    target = tmp_path / ".theurian" / "state" / "active.json.tmp"

    remedy = symbolic_link_remedy(target)

    assert str(target) in remedy
    assert "Remove the symbolic link" in remedy
    assert "ADR-0004" in remedy, "the reader has to be told what removing it costs"


def test_the_remedy_is_keyed_on_the_path_and_not_one_fixed_sentence(tmp_path: Path) -> None:
    """A remedy flattened to one text names the wrong file for five of six members.

    The mutation this pins is real: the six writers in this class sit at six
    different paths, and a cure naming a fixed one sends the operator to inspect
    something that is not there.
    """
    first = symbolic_link_remedy(tmp_path / "state" / "active.json.tmp")
    second = symbolic_link_remedy(tmp_path / "cache" / "ingestion.json")

    assert first != second


# -- The population, read out of the source ----------------------------------


def _module_sources() -> dict[Path, ast.Module]:
    """Every shipped module, parsed once.

    Keyed by path so a failure names the file. ``sorted`` because the failure
    message is a list a person reads twice on two machines.
    """
    return {
        path: ast.parse(path.read_text(encoding="utf-8"))
        for path in sorted(_SOURCE_ROOT.rglob("*.py"))
    }


#: The ``os.replace`` callers that are not members of the atomic-publisher
#: derivation, each with the measured reason and keyed by ``module::qualname``.
#:
#: **Qualified, because ``project_service.py`` defines ``_write`` twice** and a
#: bare-name key excluded both of them plus every future one (round one, code
#: review M-3, converged with the security review). ``ProjectRegistry._write``
#: and ``BuildProvenance._write`` are two decisions, and a third ``_write``
#: landing on a third class would have inherited an exclusion nobody made for it.
#:
#: Two kinds, told apart in the text rather than pooled: a publisher that writes
#: **no temporary of its own** has no open to give ``O_NOFOLLOW`` to, and a
#: publisher whose temporary is **not clone-deliverable** has one but sits outside
#: the delivery route this class is about.
#:
#: Held as a subset guard in both directions by
#: :func:`test_every_atomic_publisher_writes_its_temporary_without_following_a_link`:
#: a member that stops writing through ``no_follow`` fails there, and an exclusion
#: whose function no longer exists fails there too.
_PUBLISHERS_OUTSIDE_THE_CLASS: Final[dict[str, str]] = {
    "cli/index_commands.py::index_build": (
        "writes no temporary of its own: it renames a finished index into place, "
        "and the `.building` file is produced by `IndexBuilder` through SQLite "
        "rather than by any open in this function. What keeps a clone from "
        "planting a link at that name is that the name is unpredictable: it is "
        "`index_for(index_build_id)` where `index_build_id` is a ULID minted by "
        "this run (`context.ids.new_ulid()`), so the path does not exist until "
        "the moment it is written. NOT 'derived from a `_contained` helper' -- "
        "that reasoning is the one #523 disproved, since a contained parent says "
        "nothing about a leaf the check never saw."
    ),
    "infrastructure/sqlite/index_purge.py::purge_into": (
        "the same shape one layer down, and it goes further: the copy is produced "
        "by SQLite under `f'{target}.building'`, where `target` carries a ULID "
        "minted for this purge, and the function *refuses outright* if that path "
        "already exists (`if building.exists(): raise`). So a pre-planted name is "
        "answered rather than followed. No open in this function writes bytes."
    ),
    "infrastructure/sqlite/findings_store.py::SqliteReviewFindingStore.replace_all": (
        "unlinks before it opens. `_unlink_sidecars`/`_unlink_with_sidecars` runs "
        "on `building_path` immediately before `sqlite3.connect(building)`, and "
        "the code there records that removal's containment role explicitly -- a "
        "planted link at that name is removed, not written through. `unlink` acts "
        "on the link itself, never on its target."
    ),
    "application/project_service.py::ProjectRegistry._write": (
        "its temporary is not clone-deliverable: this method writes under the "
        "per-user data directory, whose parent it creates itself at 0700, so no "
        "repository can deliver a link there and #523 records it as no capability "
        "increase. Recorded rather than converted, because converting is not "
        "free: `index_commands.py::index_build` calls "
        "`BuildProvenance.default().record_index(...)` with no `except OSError` "
        "around it (read 2026-09-05), so a refusal added on this shape would reach "
        "a `--json` caller as a traceback."
    ),
    "application/project_service.py::BuildProvenance._write": (
        "the same method on the other class, for the same reason, and named "
        "separately because the two are two decisions."
    ),
}

#: The derivation that produced the uncontained leaf. ``pointer.with_suffix(
#: ".json.tmp")`` names a *different* file from the one containment was asked
#: about, so every check the published pointer passed said nothing about the file
#: the write actually opened (#523). A member of this class asks ``ProjectPaths``
#: for its temporary instead, which is what puts the derived leaf through the
#: chokepoint and into the reflection sweep.
_DERIVES_ITS_OWN_TEMPORARY: Final = "with_suffix"

#: What counts as opening a path for writing, for the widened key below. The
#: ``no_follow`` spellings are in the set on purpose: the guard is "every writer
#: is found", and finding only the *unconverted* ones would make the population
#: shrink to nothing the moment the fix landed -- a sweep that passes by being
#: empty.
_WRITE_CALLS: Final = frozenset(
    {
        "write_text",
        "write_bytes",
        "open",
        "write_text_without_following_a_link",
        "open_without_following_a_link",
    }
)

#: The writers that name a ``ProjectPaths`` helper and are **not** required to go
#: through ``no_follow``, with the measured reason.
_PROJECT_WRITERS_OUTSIDE_THE_CLASS: Final[dict[str, str]] = {
    "application/proposal_service.py::ProposalService.draft": (
        "every write it makes lands in a directory this same call just created. "
        "`directory.mkdir(parents=True)` carries no `exist_ok`, and `Path.mkdir` "
        "with `parents=True` over an existing directory raises `FileExistsError` "
        "(measured 2026-09-05), so a proposal directory a clone delivered is "
        "refused rather than reused -- and the body's own parent is created "
        "underneath that fresh directory. There is no path here a repository can "
        "pre-plant a link at."
    ),
}


def _enclosing_functions_of_os_replace(tree: ast.Module) -> set[str]:
    """Every function in ``tree`` whose body calls ``os.replace``, by qualified name.

    Nested and method definitions are reported under the dotted path that reaches
    them, which is what makes two same-named methods on two classes two entries
    rather than one (round one, M-3).
    """
    return {
        qualname
        for qualname, node in _qualified_functions(tree).items()
        if any(
            isinstance(call, ast.Call)
            and isinstance(call.func, ast.Attribute)
            and call.func.attr == "replace"
            and isinstance(call.func.value, ast.Name)
            and call.func.value.id == "os"
            for call in ast.walk(node)
        )
    }


def _qualified_functions(
    tree: ast.Module,
) -> dict[str, ast.FunctionDef | ast.AsyncFunctionDef]:
    """Every function in ``tree``, keyed by the dotted path that reaches it.

    ``ast.walk`` loses the enclosing class, which is what made ``_write`` one key
    for two methods. This descends deliberately so ``ProjectRegistry._write`` and
    ``BuildProvenance._write`` are distinct, and so a third one arrives as a name
    nobody has classified.
    """
    found: dict[str, ast.FunctionDef | ast.AsyncFunctionDef] = {}

    def descend(body: list[ast.stmt], prefix: str) -> None:
        for node in body:
            if isinstance(node, ast.ClassDef):
                descend(node.body, f"{prefix}{node.name}.")
            elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                found[f"{prefix}{node.name}"] = node
                descend(node.body, f"{prefix}{node.name}.")

    descend(tree.body, "")
    return found


def _calls_by_name(node: ast.AST) -> set[str]:
    return {
        call.func.id
        for call in ast.walk(node)
        if isinstance(call, ast.Call) and isinstance(call.func, ast.Name)
    }


def _calls_by_attribute(node: ast.AST) -> set[str]:
    return {
        call.func.attr
        for call in ast.walk(node)
        if isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute)
    }


def _attributes_named(node: ast.AST) -> set[str]:
    return {member.attr for member in ast.walk(node) if isinstance(member, ast.Attribute)}


def _project_paths_helpers() -> frozenset[str]:
    """Every public path helper ``ProjectPaths`` exposes, by reflection.

    The same source of truth ``test_project_paths_containment.py`` sweeps, asked
    the same way, so a helper added later joins this key without anyone editing
    it. Read off the class rather than listed, because a list is a claim about the
    class that nothing recomputes.
    """
    found: set[str] = set()
    for name, member in vars(ProjectPaths).items():
        if name.startswith("_"):
            continue
        if isinstance(member, property | functools.cached_property) or inspect.isfunction(member):
            found.add(name)
    return frozenset(found)


def test_the_publisher_key_hits_a_planted_positive(tmp_path: Path) -> None:
    """The zeros below count only because the keys are demonstrated to hit.

    A source-level guard that matched nothing would report the same clean result
    whether the tree is clean or the key is broken -- an ``os.replace`` spelled
    through an alias, an AST shape the walker misses. So the identical functions
    are pointed at a module written here that is exactly the defect: a publisher
    deriving its own temporary, naming a ``ProjectPaths`` helper, and writing it
    with a following open.
    """
    planted = tmp_path / "planted.py"
    planted.write_text(
        "import os\n"
        "class Publisher:\n"
        "    def publish(self, paths, body):\n"
        "        pointer = paths.active_pointer\n"
        '        temporary = pointer.with_suffix(".json.tmp")\n'
        '        temporary.write_text(body, encoding="utf-8")\n'
        "        os.replace(temporary, pointer)\n",
        encoding="utf-8",
    )
    tree = ast.parse(planted.read_text(encoding="utf-8"))

    assert _enclosing_functions_of_os_replace(tree) == {"Publisher.publish"}, (
        "the qualified key does not reach a method, so every method is invisible to it"
    )
    function = _qualified_functions(tree)["Publisher.publish"]
    assert _DERIVES_ITS_OWN_TEMPORARY in _calls_by_attribute(function)
    assert "write_text_without_following_a_link" not in _calls_by_name(function)
    assert _attributes_named(function) & _project_paths_helpers() == {"active_pointer"}
    assert _calls_by_attribute(function) & _WRITE_CALLS == {"write_text"}


def test_two_methods_sharing_a_name_are_two_entries(tmp_path: Path) -> None:
    """M-3's own control: a bare-name key excluded both ``_write`` methods at once.

    ``project_service.py`` defines ``_write`` on ``ProjectRegistry`` and on
    ``BuildProvenance``, and the first cut of the exclusion set keyed on the bare
    name -- so one entry silenced two functions, and a third ``_write`` on a third
    class would have been silenced by an exclusion nobody wrote for it.

    Asserted here on a planted module rather than on the real one, so the guard
    survives ``project_service.py`` growing or losing a ``_write``.
    """
    planted = tmp_path / "planted_two.py"
    planted.write_text(
        "import os\n"
        "class A:\n"
        "    def _write(self):\n"
        "        os.replace(self.tmp, self.path)\n"
        "class B:\n"
        "    def _write(self):\n"
        "        os.replace(self.tmp, self.path)\n",
        encoding="utf-8",
    )
    tree = ast.parse(planted.read_text(encoding="utf-8"))

    assert _enclosing_functions_of_os_replace(tree) == {"A._write", "B._write"}


def test_project_service_still_defines_exactly_the_two_write_methods_excluded() -> None:
    """The hardening the security review added to M-3.

    The exclusion set names ``ProjectRegistry._write`` and
    ``BuildProvenance._write`` individually, which is what makes a *third*
    ``_write`` arrive as an unclassified member. This asserts the count directly
    as well, so the two entries cannot quietly become a description of three
    methods if the qualified key is ever loosened again.
    """
    tree = _module_sources()[_SOURCE_ROOT / "application/project_service.py"]
    writes = {name for name in _qualified_functions(tree) if name.endswith("._write")}

    assert writes == {"ProjectRegistry._write", "BuildProvenance._write"}, (
        f"`project_service.py`'s `_write` methods have moved: {sorted(writes)}"
    )


def test_every_atomic_publisher_writes_its_temporary_without_following_a_link() -> None:
    """The completeness claim a containment sweep cannot make, first derivation.

    The population is derived, not listed: an ``os.replace`` call is what makes a
    function an atomic publisher, so every one of them in the shipped tree is a
    member and a new one arrives here as a failure rather than as a silent
    exclusion. Each member must write its temporary through
    :mod:`theurian.security.no_follow`, or carry a measured reason it is outside
    the class.

    **Two properties, because the two faces are refused by different guards.**
    The write must not follow a link (``O_NOFOLLOW``, this file's subject), and
    the temporary must not be derived at the call site (``with_suffix``), because
    a derived leaf is one no containment check ever saw -- which is the *other*
    half, and the one that let the out-of-tree face escape the working tree at
    exit 0. A publisher could satisfy either alone and still reopen #523.

    This is the half ``test_contained_path_envelope.py`` cannot see. That sweep
    proves the *path* was checked; a link whose target is inside the working tree
    passes that check and is still a write onto a file the operator authored. What
    refuses it is the open, so this guard is keyed on the open.
    """
    publishers = {
        f"{path.relative_to(_SOURCE_ROOT)}::{qualname}": _qualified_functions(tree)[qualname]
        for path, tree in _module_sources().items()
        for qualname in _enclosing_functions_of_os_replace(tree)
    }

    assert publishers, "no atomic publisher was found at all, so this asserts nothing"

    excluded = frozenset(_PUBLISHERS_OUTSIDE_THE_CLASS)
    assert excluded <= frozenset(publishers), (
        "an exclusion names a publisher that no longer exists: "
        f"{sorted(excluded - frozenset(publishers))}"
    )

    members = {
        position: function for position, function in publishers.items() if position not in excluded
    }
    following = {
        position
        for position, function in members.items()
        if "write_text_without_following_a_link" not in _calls_by_name(function)
    }
    derives = {
        position
        for position, function in members.items()
        if _DERIVES_ITS_OWN_TEMPORARY in _calls_by_attribute(function)
    }

    assert not following, (
        "an atomic publisher writes its temporary with an open that follows a "
        f"symbolic link, or is newly excluded without a recorded reason: {sorted(following)}"
    )
    assert not derives, (
        "an atomic publisher derives its temporary at the call site again, so the "
        f"leaf it opens passed no containment check: {sorted(derives)}"
    )


def test_every_writer_of_a_project_path_opens_it_without_following_a_link() -> None:
    """The second derivation, and the wider one (round one, M-1).

    The key above is "calls ``os.replace``", which makes membership an
    *atomicity* property rather than a *writing* one -- so ``ingest_command``,
    which writes its manifest without a temporary at all, sat outside the
    universal the module docstring claimed. This key is the claim itself: a
    function that names a ``ProjectPaths`` helper **and** opens something for
    writing must do that through :mod:`theurian.security.no_follow`.

    The helper set is reflected off ``ProjectPaths`` rather than listed, so a
    helper added later joins this key with no edit here -- the same source of
    truth ``test_project_paths_containment.py`` sweeps, asked the same way.

    **The bound, stated because a universal with an unstated bound is worse than
    a narrower one.** Two writers this file cares about are outside *this* key by
    construction, and each is covered elsewhere rather than forgotten:

    * ``FileSecretStore`` names no ``ProjectPaths`` helper -- it writes under the
      per-user data directory, which is not a project tree -- so its
      ``O_NOFOLLOW`` on both the read and the write is driven by
      ``test_derived_path_symlink_writes.py`` instead.
    * ``initialize_project`` writes the repository's ``.gitignore``, which it
      reaches as ``root / ".gitignore"`` and not through a helper. That write
      **does** follow a planted link (measured 2026-09-05: `theurian init --json`
      exit 0, the managed block appended to a file outside the working tree), and
      it is deliberately not fixed here: `.gitignore` is authored, Git-tracked
      content, which is the #237 authored-symlink root cause rather than this
      one. Named here so the gap is recorded rather than implied by an absence.
    """
    helpers = _project_paths_helpers()
    assert helpers, "the helper reflection returned nothing, so this key matches nothing"

    writers = {
        f"{path.relative_to(_SOURCE_ROOT)}::{qualname}": function
        for path, tree in _module_sources().items()
        for qualname, function in _qualified_functions(tree).items()
        if (_attributes_named(function) & helpers)
        and (
            (_calls_by_attribute(function) & _WRITE_CALLS)
            or (_calls_by_name(function) & _WRITE_CALLS)
        )
    }

    assert writers, "no writer of a project path was found at all, so this asserts nothing"

    excluded = frozenset(_PROJECT_WRITERS_OUTSIDE_THE_CLASS)
    assert excluded <= frozenset(writers), (
        "an exclusion names a writer that no longer exists: "
        f"{sorted(excluded - frozenset(writers))}"
    )

    following = {
        position: sorted((_calls_by_attribute(function) | _calls_by_name(function)) & _WRITE_CALLS)
        for position, function in writers.items()
        if position not in excluded
        and "write_text_without_following_a_link" not in _calls_by_name(function)
    }

    assert not following, (
        "a function that writes to a `ProjectPaths`-derived path opens it with a "
        f"call that follows a symbolic link, or is newly excluded without a "
        f"recorded reason: {following}"
    )
