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

1. *Every rename-into-place* -- the atomic publishers by definition, in **both**
   spellings: ``os.replace(temporary, final)`` and ``temporary.replace(final)``.
   The second was missing until round one, and it hid two real publishers
   (``launchagent.py`` and ``systemd_user.py``, both ``with_suffix`` + write +
   rename); they are recorded exclusions now rather than invisible. A publisher
   whose temporary is written with a following open, or whose temporary it
   derives itself with ``with_suffix``, is the defect. Seven exclusions.
2. *Every function that names a ``ProjectPaths`` helper and opens something for
   writing.* This is the universal the module actually claims, and the first key
   is not it: membership there is an *atomicity* property, so ``ingest_command``
   -- which writes its manifest with no temporary at all -- sat outside it while
   the docstring claimed otherwise (round one, code review M-1). One exclusion.

**The bound on the second key, stated rather than left to an absence.** Two
writers in this class name no ``ProjectPaths`` helper and are covered elsewhere:
``FileSecretStore`` writes under the per-user data directory, driven by
``tests/integration/test_derived_path_symlink_writes.py``; and
``ensure_gitignore`` writes the repository's own ``.gitignore``, reached as
``root / ".gitignore"``. Both are still invisible to this key, and neither is a
gap any more: the ``.gitignore`` escape this paragraph used to record as live was
filed as #571 and closed there, under the #237 authored-symlink root cause rather
than this one, with its own refusal type and its own cure. The bound is stated so
nobody reads this file as covering them; where each *is* covered is named beside
it.
"""

from __future__ import annotations

import ast
import asyncio
import errno
import functools
import inspect
import os
import sys
from pathlib import Path
from typing import Final

import pytest

from theurian.application.project_service import ProjectPaths
from theurian.infrastructure.secrets.file_store import TOKEN_KEY, FileSecretStore
from theurian.security import no_follow
from theurian.security.no_follow import (
    WRITE_FLAGS,
    is_a_symbolic_link_refusal,
    open_for_reading_without_following_a_link,
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


@_NEEDS_SYMLINKS
@pytest.mark.parametrize(
    "plant",
    [
        pytest.param(("auth",), id="self-loop"),
        pytest.param(("a", "auth"), id="mutual-loop"),
    ],
)
def test_a_prefix_loop_is_answered_before_the_read_open(
    tmp_path: Path, plant: tuple[str, ...]
) -> None:
    """Security round two, H-B: what stops a *prefix* loop reaching the read open.

    The write side's answer is the ``mkdir`` that runs first, and the module
    docstring used to give it for both directions. ``FileSecretStore.get`` never
    mkdirs anything. What actually holds the read side is ``Path.exists()``, which
    ``pathlib`` implements over an ``os.stat`` whose ``ELOOP`` it swallows into
    ``False`` -- so ``get`` returns ``None`` and no open is attempted.

    That barrier is a **side effect of a probe written for a different question**
    ("is there a secret yet?"), which is exactly why it is pinned: an edit that
    reordered or dropped that check would take the read side's ELOOP attribution
    with it and change no other line. Both loop shapes are driven, because a
    self-loop and a mutual loop reach ``stat`` differently and only one of them
    was measured when the finding was written.

    The raw open is asserted beside it, so the test cannot pass by the loop having
    been harmless: it must be the ``exists()`` call that answered, not the
    absence of anything to trip over.
    """
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    if len(plant) == 1:
        (data_dir / "auth").symlink_to("auth")
    else:
        (data_dir / "a").symlink_to("auth")
        (data_dir / "auth").symlink_to("a")
    token = data_dir / "auth" / TOKEN_KEY

    assert token.exists() is False, (
        "`Path.exists()` no longer swallows the prefix loop, so the read side's "
        "ELOOP attribution now rests on something this test does not describe"
    )
    assert asyncio.run(FileSecretStore(data_dir).get(TOKEN_KEY)) is None

    with pytest.raises(OSError) as excinfo:
        open_for_reading_without_following_a_link(token)
    assert is_a_symbolic_link_refusal(excinfo.value), (
        "the raw open did not trip on the loop, so `exists()` was not what "
        "prevented it and this test proves nothing"
    )


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
    "infrastructure/services/launchagent.py::LaunchAgentManager.install": (
        "its temporary is not clone-deliverable: `plist_path` is "
        "`self._home / 'Library/LaunchAgents' / f'{LABEL}.plist'` (read "
        "2026-09-05), so the `.plist.tmp` beside it sits in the user's own "
        "`$HOME` and no repository reaches it. This one is a genuine "
        "`with_suffix` + write + rename publisher and was **invisible** to the "
        "key until it learned the `Path.replace` spelling (round one, "
        "adversarial M-3) -- it is recorded here rather than left unseen, which "
        "is the difference between a bound and a hole."
    ),
    "infrastructure/services/systemd_user.py::SystemdUserManager.install": (
        "the same shape and the same reason on Linux: `unit_path` is "
        "`self._home / '.config/systemd/user' / UNIT_NAME`, so its "
        "`.service.tmp` is a per-user path outside any project tree."
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

#: The subset of :data:`_WRITE_CALLS` that does not follow a link.
#:
#: The judgment below is ``every write call this function makes is one of these``,
#: not ``it calls the text writer somewhere`` (round two, code review M-4). The
#: earlier spelling was wrong in both directions: a function using only
#: ``open_without_following_a_link`` -- which ``FileSecretStore`` does -- read as
#: unconverted, and a function that called the text writer *and* a bare
#: ``write_text`` read as converted while half its writes followed links.
_NO_FOLLOW_SPELLINGS: Final = frozenset(
    {"write_text_without_following_a_link", "open_without_following_a_link"}
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


def _enclosing_functions_of_an_atomic_replace(tree: ast.Module) -> set[str]:
    """Every function in ``tree`` that publishes by rename, by qualified name.

    **Two spellings, because the key saw only one** (round one, adversarial M-3).
    ``os.replace(temporary, final)`` is what the state and index publishers use;
    ``temporary.replace(final)`` is ``Path.replace``, the same syscall through the
    other API, and it is what the two service-unit publishers use. Keyed on
    ``os.replace`` alone, ``launchagent.py::install`` and
    ``systemd_user.py::install`` were invisible: ``with_suffix(".plist.tmp")`` +
    ``write_bytes`` + rename, the exact shape #523 is about, and neither a member
    nor a recorded exclusion.

    ``Path.replace`` cannot be told from an unrelated ``x.replace(...)`` -- a
    string replace, say -- by AST alone, so this over-includes on purpose: a
    false member has to be classified by a human, which is the completeness
    guarantee, while a false *non*-member is a silent hole.

    Nested and method definitions are reported under the dotted path that reaches
    them, which is what makes two same-named methods on two classes two entries
    rather than one (round one, M-3).
    """
    return {
        qualname
        for qualname, node in _qualified_functions(tree).items()
        if any(_is_an_atomic_replace(call) for call in ast.walk(node))
    }


#: Every way this codebase could publish by rename. ``replace`` and ``rename`` are
#: the same syscall with different clobber semantics; ``shutil.move`` falls back
#: to a copy across devices but is a rename within one, and a publisher that
#: switched to it would be just as much a member.
#:
#: **The zero this widening reports has its own control**, because a key extended
#: to shapes nothing uses is indistinguishable from a key that is broken. The
#: search ``git grep -nE '"'"'os[.]rename[(]|[.]rename[(]|shutil[.]move'"'"'`` over
#: ``packages/theurian-core/src`` returned **no lines** on 2026-09-05, so no
#: shipped member arrives through the new spellings today -- and
#: :func:`test_the_publisher_key_hits_a_planted_positive` drives each of them
#: against a planted module instead, which is what makes that zero readable.
_ATOMIC_RENAME_NAMES: Final = frozenset({"replace", "rename", "move"})


def _is_an_atomic_replace(node: ast.AST) -> bool:
    """Whether ``node`` is a rename-into-place, in any of its spellings.

    Four shapes, and the last three were added in round two after the docstring
    said "either spelling" while knowing two (adversarial M-2):

    * ``os.replace(temporary, final)`` -- two arguments, on the ``os`` module;
    * ``temporary.replace(final)`` -- ``Path.replace``, one argument;
    * ``os.rename(...)`` / ``temporary.rename(final)`` -- the same syscall with
      different clobber semantics, and just as much a publish;
    * ``shutil.move(...)`` -- a rename within one filesystem.

    **The arity is the discriminator for the bare-attribute forms, and it is exact
    rather than heuristic.** ``str.replace`` takes two arguments, so a
    one-positional-argument ``.replace`` on this tree is a ``Path.replace``.

    Measured 2026-09-05 by walking this tree's AST for a call whose function is an
    attribute named ``replace`` -- the same key this predicate uses, so the number
    is reproducible rather than eyeballed: **23** such calls, of which the filter
    keeps **10** (eight ``os.replace`` and two ``Path.replace``, which are exactly
    the publishers) and **one** is a qualified ``dataclasses.replace``, excluded
    from the other side because it is called with keywords and this requires none.
    The other twelve are string replaces. A line-based
    ``git grep -nE`` over the same pattern answers 24, because it also counts a
    prose mention; the AST number is the one stated, with its key. Without the
    arity filter every one of those twelve becomes something the exclusion list
    has to bury.

    ``shutil.move`` and ``os.rename`` take a module qualifier, so they are matched
    on that rather than on arity -- ``move`` in particular is a common enough verb
    that a bare one-argument ``.move(...)`` would be a poor key.
    """
    if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
        return False
    if node.func.attr not in _ATOMIC_RENAME_NAMES:
        return False
    if isinstance(node.func.value, ast.Name) and node.func.value.id in {"os", "shutil"}:
        return True
    if node.func.attr == "move":
        # Only ever counted through its module qualifier: `.move(x)` on an
        # arbitrary object is not a filesystem publish.
        return False
    return len(node.args) == 1 and not node.keywords


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
    """Every public path member ``ProjectPaths`` exposes, by reflection.

    The same source of truth ``test_project_paths_containment.py`` sweeps, asked
    the same way, so a member added later joins this key without anyone editing
    it. Read off the class rather than listed, because a list is a claim about the
    class that nothing recomputes.

    **The plain dataclass fields are included, and were not** (round two, code
    review M-3). ``ProjectPaths`` is ``slots=True``, so ``root`` and
    ``knowledge_dir`` appear in ``vars()`` as ``member_descriptor`` objects, which
    are neither a ``property`` nor a function -- the shape filter dropped both.
    They are the two members every other helper is *built from*, so a writer that
    joined ``knowledge_dir`` directly was invisible to a key whose whole subject is
    writers of project paths. That is not hypothetical: joining ``knowledge_dir``
    at the call site is exactly how the ingestion manifest escaped containment in
    the first place (#394).

    ``__annotations__`` rather than a third ``isinstance`` arm, because it names
    the declared fields whatever the descriptor machinery turns them into.
    """
    found = {name for name in ProjectPaths.__annotations__ if not name.startswith("_")}
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

    assert _enclosing_functions_of_an_atomic_replace(tree) == {"Publisher.publish"}, (
        "the qualified key does not reach a method, so every method is invisible to it"
    )
    assert _enclosing_functions_of_an_atomic_replace(
        ast.parse(
            "class Service:\n"
            "    def install(self, plist, body):\n"
            '        temporary = plist.with_suffix(".plist.tmp")\n'
            "        temporary.write_bytes(body)\n"
            "        temporary.replace(plist)\n"
        )
    ) == {"Service.install"}, (
        "the key misses `Path.replace`, which is the spelling the two service-unit "
        "publishers use and the one it was blind to in round one"
    )
    assert not _enclosing_functions_of_an_atomic_replace(
        ast.parse('def clean(text):\n    return text.replace("a", "b")\n')
    ), "the key counts a string replace as a publisher, so its exclusions bury them"

    # Round two, adversarial M-2. `git grep` finds no shipped member through any
    # of these, so without a planted control the widening would be a key nothing
    # exercises -- which reads identically to a key that does not work.
    for spelling in (
        "        temporary.rename(final)",
        "        os.rename(temporary, final)",
        "        shutil.move(temporary, final)",
    ):
        planted_source = (
            "import os\nimport shutil\ndef publish(temporary, final):\n" + spelling + "\n"
        )
        assert _enclosing_functions_of_an_atomic_replace(ast.parse(planted_source)) == {
            "publish"
        }, f"the key does not see {spelling.strip()!r}, so a publisher using it is invisible"

    assert not _enclosing_functions_of_an_atomic_replace(
        ast.parse("def go(widget):\n    widget.move(3)\n")
    ), "a bare `.move(x)` on an arbitrary object counts as a filesystem publish"
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

    assert _enclosing_functions_of_an_atomic_replace(tree) == {"A._write", "B._write"}


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
    # Derived from the exclusion map rather than written out again (round two,
    # LOW): a literal set here would be a second place to update, and the one
    # that got forgotten would be the one that stayed green.
    excluded = {
        position.split("::", 1)[1]
        for position in _PUBLISHERS_OUTSIDE_THE_CLASS
        if position.startswith("application/project_service.py::")
    }

    assert writes == excluded, (
        f"`project_service.py`'s `_write` methods and the exclusions naming them "
        f"have moved apart: methods {sorted(writes)}, excluded {sorted(excluded)}"
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
        for qualname in _enclosing_functions_of_an_atomic_replace(tree)
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
        if (_calls_by_attribute(function) | _calls_by_name(function))
        & _WRITE_CALLS - _NO_FOLLOW_SPELLINGS
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
    * ``ensure_gitignore`` writes the repository's ``.gitignore``. An earlier
      version of this paragraph named ``initialize_project`` -- its *caller* --
      and gave the wrong reason for the invisibility too (round two, code review
      M-3). The real one: ``ensure_gitignore(root: Path)`` takes a bare ``Path``
      parameter and never touches ``ProjectPaths`` at all, so no reflection over
      that class can reach it, and widening the key to the plain fields does not
      change that. **It is still invisible to this key and is no longer a gap**:
      the escape this paragraph recorded (measured 2026-09-05: ``theurian init
      --json`` exit 0, the managed block appended to a file outside the working
      tree) was filed as #571 for its own root cause -- ``.gitignore`` is
      authored, Git-tracked content, the #237 class rather than this one -- and
      is closed there, with both faces driven by
      ``test_init_gitignore_block.py::test_init_refuses_a_gitignore_that_is_a_symbolic_link``.
      Its refusal carries its own cure and deliberately not
      :func:`~theurian.security.no_follow.symbolic_link_remedy`, whose every
      clause is false of an authored file.
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
        position: sorted(writes - _NO_FOLLOW_SPELLINGS)
        for position, function in writers.items()
        if position not in excluded
        and (writes := (_calls_by_attribute(function) | _calls_by_name(function)) & _WRITE_CALLS)
        - _NO_FOLLOW_SPELLINGS
    }

    assert not following, (
        "a function that writes to a `ProjectPaths`-derived path opens it with a "
        f"call that follows a symbolic link, or is newly excluded without a "
        f"recorded reason: {following}"
    )
