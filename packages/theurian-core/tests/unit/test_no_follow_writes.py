"""``theurian.security.no_follow`` refuses a link, and the writers use it.

Two halves, and the second is the one a containment sweep cannot see.

``tests/integration/test_contained_path_envelope.py`` proves that every derived
path a writer builds goes through ``_contain``. That is half of the guard, and
containment is *right* to wave through a link whose target is inside the working
tree -- which is exactly the shape that truncated a tracked file in the user's own
checkout at exit 0 (#523's in-tree face, and #394's). What refuses that one is
``O_NOFOLLOW`` inside the write, so the completeness claim for it has to be keyed
on the *open*, not on the path.

The population that key ranges over is every write-to-temp-then-``os.replace``
publisher in the tree, read out of the source rather than listed: the ``os.replace``
call sites are the atomic publishers by definition, and a publisher whose temporary
is written with a following open is the defect. Four are excluded, each with the
measured reason it is not a member.
"""

from __future__ import annotations

import ast
import errno
import os
import sys
from pathlib import Path
from typing import Final

import pytest

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


#: The ``os.replace`` callers that are not members of this class, each with the
#: measured reason.
#:
#: Two kinds, and they fail differently, so they are told apart in the text rather
#: than pooled: a publisher that writes **no temporary of its own** has no open to
#: give ``O_NOFOLLOW`` to, and a publisher whose temporary is **not
#: clone-deliverable** has one but sits outside the delivery route this class is
#: about.
#:
#: Held as a subset guard in both directions by
#: :func:`test_every_atomic_publisher_writes_its_temporary_without_following_a_link`:
#: a member that stops writing through ``no_follow`` fails there, and an exclusion
#: whose function no longer exists fails there too.
_PUBLISHERS_OUTSIDE_THE_CLASS: Final[dict[str, str]] = {
    "cli/index_commands.py::index_build": (
        "writes no temporary of its own: it renames a finished index into place. "
        "The `.building` file is produced by `IndexBuilder` through SQLite, not by "
        "a text write this module makes, and its name is derived from `index_for` "
        "-- a `_contained` helper. There is no open here to give `O_NOFOLLOW` to."
    ),
    "infrastructure/sqlite/index_purge.py::purge_into": (
        "the same shape one layer down: the copy is produced by SQLite's own "
        "backup API under a `.building` name and then renamed. No open in this "
        "function writes bytes."
    ),
    "infrastructure/sqlite/findings_store.py::replace_all": (
        "the same again for the review-findings store."
    ),
    "application/project_service.py::_write": (
        "its temporary is not clone-deliverable. `ProjectRegistry._write` and "
        "`BuildProvenance._write` share this name and both write under the "
        "per-user data directory, whose parent this method itself creates at "
        "0700 -- so no repository can deliver a link there, and #523 records both "
        "as no capability increase. Recorded rather than converted, because "
        "converting is not free: `index_commands.py::index_build` calls "
        "`BuildProvenance.default().record_index(...)` with no `except OSError` "
        "around it (read 2026-09-05), so a refusal added here would reach a "
        "`--json` caller as a traceback -- the CP-2 class this branch is under "
        "instruction not to reopen."
    ),
}

#: The derivation that produced the uncontained leaf. ``pointer.with_suffix(
#: ".json.tmp")`` names a *different* file from the one containment was asked
#: about, so every check the published pointer passed said nothing about the file
#: the write actually opened (#523). A member of this class asks ``ProjectPaths``
#: for its temporary instead, which is what puts the derived leaf through the
#: chokepoint and into the reflection sweep.
_DERIVES_ITS_OWN_TEMPORARY: Final = "with_suffix"


def _enclosing_functions_of_os_replace(tree: ast.Module) -> set[str]:
    """Every function in ``tree`` whose body calls ``os.replace``.

    Nested functions are reported under their own name, which is what makes a
    helper extracted out of a publisher stay visible to the guard.
    """
    found: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        if any(
            isinstance(call, ast.Call)
            and isinstance(call.func, ast.Attribute)
            and call.func.attr == "replace"
            and isinstance(call.func.value, ast.Name)
            and call.func.value.id == "os"
            for call in ast.walk(node)
        ):
            found.add(node.name)
    return found


def _functions_named(tree: ast.Module, name: str) -> list[ast.FunctionDef | ast.AsyncFunctionDef]:
    """Every definition of ``name`` in ``tree``, because one module can hold two.

    ``project_service.py`` defines ``_write`` twice, on two classes. A helper that
    unpacked a single match would raise inside the guard the moment a second
    definition landed -- reading as a broken test rather than as the population
    growing, which is the failure mode this file exists to make legible.
    """
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.name == name
    ]


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


def test_the_publisher_key_hits_a_planted_positive(tmp_path: Path) -> None:
    """The zero below counts only because the key is demonstrated to hit.

    A source-level guard that matched nothing would report the same clean result
    whether the tree is clean or the key is broken -- an ``os.replace`` spelled
    through an alias, an AST shape the walker misses. So the identical functions
    are pointed at a module written here that is exactly the defect: a publisher
    deriving its own temporary and writing it with a following open.
    """
    planted = tmp_path / "planted.py"
    planted.write_text(
        "import os\n"
        "def publish(pointer, body):\n"
        '    temporary = pointer.with_suffix(".json.tmp")\n'
        '    temporary.write_text(body, encoding="utf-8")\n'
        "    os.replace(temporary, pointer)\n",
        encoding="utf-8",
    )
    tree = ast.parse(planted.read_text(encoding="utf-8"))

    assert _enclosing_functions_of_os_replace(tree) == {"publish"}
    (function,) = _functions_named(tree, "publish")
    assert _DERIVES_ITS_OWN_TEMPORARY in _calls_by_attribute(function)
    assert "write_text_without_following_a_link" not in _calls_by_name(function)


def test_every_atomic_publisher_writes_its_temporary_without_following_a_link() -> None:
    """The completeness claim a containment sweep cannot make.

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
        f"{path.relative_to(_SOURCE_ROOT)}::{name}": _functions_named(tree, name)
        for path, tree in _module_sources().items()
        for name in _enclosing_functions_of_os_replace(tree)
    }

    assert publishers, "no atomic publisher was found at all, so this asserts nothing"

    excluded = frozenset(_PUBLISHERS_OUTSIDE_THE_CLASS)
    assert excluded <= frozenset(publishers), (
        "an exclusion names a publisher that no longer exists: "
        f"{sorted(excluded - frozenset(publishers))}"
    )

    members = {
        position: functions
        for position, functions in publishers.items()
        if position not in excluded
    }
    following = {
        position
        for position, functions in members.items()
        if any(
            "write_text_without_following_a_link" not in _calls_by_name(function)
            for function in functions
        )
    }
    derives = {
        position
        for position, functions in members.items()
        if any(
            _DERIVES_ITS_OWN_TEMPORARY in _calls_by_attribute(function) for function in functions
        )
    }

    assert not following, (
        "an atomic publisher writes its temporary with an open that follows a "
        f"symbolic link, or is newly excluded without a recorded reason: {sorted(following)}"
    )
    assert not derives, (
        "an atomic publisher derives its temporary at the call site again, so the "
        f"leaf it opens passed no containment check: {sorted(derives)}"
    )
