"""Truncating writes that refuse a symbolic link at the target (SEC-7, ADR-0004).

``Path.write_text`` and ``open(path, "w")`` follow a symbolic link and then
``O_TRUNC`` whatever it names, so a link planted where Theurian writes turns the
write into a destructive write somewhere else. That is one root cause with
several faces -- the write lock (#481), the two active pointers and the
secret-scan record (#523), the ingestion manifest (#394) and the local access
token (#371).

**This module is where the shared spellings live, and it is not the only place
``O_NOFOLLOW`` appears** -- the first draft of this paragraph said it was, and the
write lock it enumerates two lines up is one of the counter-examples. Run
``git grep -n O_NOFOLLOW -- packages/theurian-core/src`` rather than trusting a
sentence: two shipped call sites build the flags themselves, and both predate
this module and are correct to.
``infrastructure/sqlite/connection.py::WriteLock._open`` deliberately omits
``O_TRUNC`` (a lock file's bytes mean nothing, and truncation is what turns a
mis-aimed open into data loss), and
``application/proposal_service.py::_write_file`` swaps ``O_TRUNC`` for ``O_EXCL``
on the migration, whose name must never land on another file. Neither could use
:data:`WRITE_FLAGS` without losing the property it was written for.

**Three guards, and none replaces another.** A path derived from a contained one
must *also* be proved contained (``ProjectPaths._contained``): ``O_NOFOLLOW``
refuses a link **at the final component**, but a caller that never asked whether
the path stays inside the working tree has not asked the containment question at
all -- and containment alone waves through a link whose target is *inside* the
tree, which is the shape that truncates a file in the user's own checkout. The
third is the *shape* of what the open returned
(:func:`~theurian.security.regular_file.assert_a_regular_file`, #586): a
contained path that is no symbolic link can still be a named pipe, and the first
two say nothing about that.

**The refusal covers the final component only**, which is the bound
``WriteLock._open`` already records for #481 and this module does not widen:
``O_NOFOLLOW`` constrains the last path component, so an ordinary directory
symlink in the *prefix* is followed. That is not a corner case, and it reaches
both derived subdirectories -- measured 2026-09-05 against the real CLI, with a
fact-pin for each:

* ``.theurian/cache -> ../docs`` relocates the ingestion manifest onto a tracked
  ``docs/ingestion.json`` at exit 0
  (``test_a_contained_directory_link_still_relocates_the_manifest``);
* ``.theurian/state -> ../build`` relocates the whole state directory. Files
  under ``build/`` whose names do not collide simply survive beside it, and a
  colliding one does not: a tracked ``build/active.json.tmp`` was truncated by
  the pointer write and then **unlinked** by the ``os.replace`` that publishes
  over it, at exit 0 (``test_a_contained_state_link_destroys_a_colliding_name``).
  A tracked ``build/active.json`` instead ends the run at exit 1 -- but that is an
  *accident*, not a guard: the pointer read fails to parse it and aborts before
  anything is written, so the same plant with any other colliding name proceeds.

Nothing here refuses either, containment does not either -- both targets resolve
inside the tree -- and closing them needs ``openat`` against a directory
descriptor at every level, which nothing in this codebase does. Recorded as
[#577](https://github.com/theurian/theurian/issues/577).

**Why an ``ELOOP`` from one of these opens is the final component's. The write
side and the read side are answered by different mechanisms, and an earlier
version of this paragraph gave the write side's answer for both** (security round
two, H-B).

*Writes.* Every write caller ``mkdir(parents=True, exist_ok=True)``s the parent
before the open, so the prefix has already been walked by the time the open runs
-- but that ``mkdir`` does not always *return*: with a self-referential prefix
(``.theurian/state -> state``) it raises ``FileExistsError`` (errno 17, measured),
not ``ELOOP``, and the open never happens at all. The conclusion survives that
correction because it needs only the weaker premise: the open runs **when the
mkdir succeeded**, and a mkdir that succeeded resolved that prefix, so an
``ELOOP`` reaching the open cannot be from it.

*Reads.* :func:`open_for_reading_without_following_a_link` has no ``mkdir`` --
neither of its callers creates anything. ``git grep -n
'open_for_reading_without_following_a_link(' -- packages/theurian-core/src``
answers four lines on 2026-09-06, and two of them are calls --
``FileSecretStore.get`` and ``project_service._read_authored_file``; the other
two are this sentence and the definition below, which is what a line-grep over
prose always does. So none of that argument applies here. The attribution below is
about ``FileSecretStore.get`` and is not claimed of the other: what holds it is a
single call in that caller, ``Path.exists()``, which runs first, and ``pathlib``
swallows ``ELOOP`` among the errnos it treats as "not there", so a prefix loop
makes ``exists()`` answer ``False`` and ``get`` return ``None`` before any open
is attempted. Measured 2026-09-05 on a
self-loop (``auth -> auth``) and a mutual loop (``auth -> a -> auth``): both give
``exists() is False`` and ``get() is None``, while the raw open at the same path
raises errno 62. That barrier is a *side effect* of a probe written for a
different question, so it is named here and pinned by
``test_no_follow_writes.py::test_a_prefix_loop_is_answered_before_the_read_open``
-- if a future edit drops the ``exists()`` check, the read side loses its
attribution guarantee with no other line changing.

What the write side's mkdir failure does *not* get is a cure keyed on the real
cause -- ``migrate apply`` publishes "Check that ``.theurian/state/`` is
writable", which is a non-cause for a symlink loop. That is a wrong-remedy
residual, not a write escape, and it is recorded rather than fixed here.

A refusal arrives as a plain :class:`OSError` and is deliberately not translated
into a :class:`~theurian.domain.errors.TheurianError` here. Every caller of these
functions already grades ``OSError`` into its command's ``{error, remedy}``
envelope; a new exception type would slip past those handlers and reach a
``--json`` caller as a traceback, which is the CP-2 escape #549 closed. What the
call sites add is the *wording*: :func:`is_a_symbolic_link_refusal` tells the
link apart from the other ways a write fails, and :func:`symbolic_link_remedy`
gives it the cure.

**One caller departs from that, with its reason recorded there** (#571):
``project_service._gitignore_link_refusal`` converts the ``ELOOP`` -- and only
the ``ELOOP`` -- into a ``ProjectError``. The paragraph above holds because its
callers are composition roots that already grade ``OSError``;
``ensure_gitignore`` is an application function whose refusal has to survive two
of them and carry a cure neither could write. Named here so the exception is a
cross-reference rather than a contradiction a reader has to resolve alone.
"""

from __future__ import annotations

import errno
import os
from typing import TYPE_CHECKING, Final

from theurian.security.regular_file import assert_a_regular_file

if TYPE_CHECKING:
    from pathlib import Path

#: The flags a truncating write takes here. ``O_NOFOLLOW`` is the link guard;
#: ``O_NONBLOCK`` is the *waiting* guard (#586); everything else reproduces what
#: ``open(path, "w")`` does, so a caller that swaps one for the other changes
#: nothing but the refusal.
#:
#: ``O_NONBLOCK`` costs nothing on a regular file and is what keeps a named pipe
#: from holding the write. Re-measured on this branch, 2026-09-06, the whole flag
#: set: a fresh create still lands at the mode passed (``0600`` for the token);
#: an ``O_TRUNC`` write over ten existing bytes still leaves exactly the two
#: written; a symbolic link still refuses ``ELOOP``; a directory still refuses
#: ``EISDIR``; and a reader-less named pipe answers ``ENXIO`` instead of waiting,
#: which is the refusal ``daemon/instance.py`` and ``connection.LOCK_OPEN_FLAGS``
#: already rely on.
#:
#: **A pipe with a reader attached takes it without complaint**, which is why the
#: flag is not the whole guard: the open returns a descriptor and the write goes
#: into somebody's pipe. :func:`~theurian.security.regular_file.assert_a_regular_file`
#: is what answers that, from the descriptor.
WRITE_FLAGS: Final = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW | os.O_NONBLOCK

#: The flags a *read* takes. ``O_RDONLY``, the same two guards, and no
#: ``O_CREAT``: a read of a path that is not there is a missing file, not
#: something to make.
#:
#: ``O_NONBLOCK`` matters more here than on the write side, and differently. An
#: ``O_RDONLY`` open of a named pipe waits for a *writer*; with the flag it
#: returns at once -- measured 2026-09-06, on a pipe with no writer at either
#: end -- and the ``read`` behind it is then what would have waited. So on this
#: side the flag moves the stall one line down rather than removing it, and the
#: ``fstat`` is what removes it.
#:
#: A read through a planted link is not the mirror image of a write through one
#: -- nothing is destroyed -- and it is worse in the direction that matters for a
#: credential: the caller believes it is holding what Theurian stored, and it is
#: holding what the attacker chose. Measured on this branch before the read was
#: converted: ``FileSecretStore.get`` returned the attacker's value, the daemon
#: served it as its bearer token because ``ensure_token`` re-mints only when
#: there is *no* token, and ``theurian doctor`` reported the arrangement
#: satisfied (security round one, HIGH-1).
READ_FLAGS: Final = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK

#: The creation mode for a derived artefact, and **deliberately not the ``0o666``
#: that** ``open(path, "w")`` **passes**.
#:
#: The two are identical under the usual ``022`` umask -- both create ``0644`` --
#: so nothing moves for almost every reader. Where they differ is a umask looser
#: than that, and there ``0o666`` creates a **world-writable** ``active.json``:
#: any local account could then repoint the state pointer, which is the
#: derived-state-trust class (GHSA-266v) reached through a permission bit instead
#: of through a commit. Theurian is one process per user per machine (ADR-0002)
#: and nothing needs group or other write on a file it rebuilds, so the
#: conversion tightens rather than reproducing.
#:
#: Found by CodeQL (``py/overly-permissive-file``) on the first push of this
#: branch, over the ``0o666`` an earlier cut carried for exact parity.
#:
#: Masked by the umask's *complement* -- the file is created with
#: ``mode & ~umask``, so a bit absent here can never be granted and a bit present
#: here can still be taken away. "ANDed with the umask" is what this note said
#: until round one, and it is the opposite operation.
#:
#: Applied only when the open *creates* the file: an artefact an older build left
#: behind keeps the mode it was created with, and nothing here chmods a file it
#: did not create.
_DEFAULT_CREATE_MODE: Final = 0o644

#: The mode :func:`open_for_reading_without_following_a_link` passes and never
#: applies. Kept beside the create mode rather than inlined, so the two are read
#: together and the difference between them is visible: this one is unreachable
#: by construction, and the comment at the call site says why it is written at all.
_SECRET_READ_MODE: Final = 0o600


def _refuse_an_irregular_descriptor(descriptor: int, path: Path) -> None:
    """Close ``descriptor`` and re-raise if it is not a regular file.

    The shared tail of both openers below, so the ``fstat`` is not a line each of
    them could be edited out of separately, and the close-on-refusal is written
    once rather than twice.

    ``except BaseException``, not ``Exception``: an interrupt landing between the
    open and the check leaks the descriptor exactly as an error would, and this
    is the only place that can still close it.

    **It takes the descriptor and not the open, which is deliberate** (#586 round
    two). An earlier cut of this helper owned the ``os.open`` as well, with
    ``flags`` and ``mode`` as parameters -- and that hid the creation mode from
    static analysis: CodeQL raised ``py/overly-permissive-file`` at the shared
    line, because through a parameter it can no longer see *which* mode reaches
    it. The mode had not changed; only its visibility had. It is worth keeping
    visible, because CodeQL reading the literal is what caught the ``0o666`` that
    :data:`_DEFAULT_CREATE_MODE` records tightening. So each opener keeps its own
    ``os.open`` with its own constant, and shares the part that has no constant
    in it.
    """
    try:
        assert_a_regular_file(descriptor, path)
    except BaseException:
        os.close(descriptor)
        raise


def open_without_following_a_link(path: Path, *, mode: int = _DEFAULT_CREATE_MODE) -> int:
    """``os.open`` for a truncating write, refusing a link or a planted artefact.

    Returns the descriptor; the caller owns closing it.

    **Two guards, and the flags are only one of them** (#586). ``O_NOFOLLOW``
    refuses a symbolic link at the final component and ``O_NONBLOCK`` refuses a
    reader-less named pipe with ``ENXIO``, but a pipe *with a reader attached*
    takes both without complaint -- measured 2026-09-06, the open returned a
    descriptor -- and the write would then land in somebody's pipe rather than in
    the file this function promises. The ``fstat`` in
    :func:`_opened_regular_file` is what answers that, and it asks the
    **descriptor**: a path check answers about a name, and the name can be
    re-pointed between the answer and the open.

    Raises:
        IrregularArtefactError: If the descriptor is a named pipe, a socket or a
            device. An ``OSError``, so every caller's existing handler still
            grades it.
        OSError: Whatever the open refuses with. ``ELOOP`` is the symbolic link
            (:func:`is_a_symbolic_link_refusal`); ``EISDIR`` is a directory,
            which ``O_WRONLY`` refuses before the ``fstat`` can be reached; every
            other errno means what it always meant.
    """
    descriptor = os.open(path, WRITE_FLAGS, mode)
    _refuse_an_irregular_descriptor(descriptor, path)
    return descriptor


def open_for_reading_without_following_a_link(path: Path) -> int:
    """``os.open`` for a read, refusing a link or a planted artefact.

    The read twin, and it exists because the write guard alone leaves a
    credential readable through a plant (security round one, HIGH-1). Returns the
    descriptor; the caller owns closing it.

    The shape refusal matters more on this side than on the write side. A named
    pipe at the token's path made ``FileSecretStore.get`` wait inside the open
    for a writer that never came, and the real-CLI consequence was measured on
    2026-09-06: ``theurian daemon start --foreground --json`` published **zero
    bytes on both channels** and never returned, *after* taking the daemon lock,
    so every later starter read a stale holder (#586).

    Raises:
        IrregularArtefactError: If the descriptor is a named pipe, a socket or a
            device.
        OSError: ``ELOOP`` for the link (:func:`is_a_symbolic_link_refusal`),
            ``ENOENT`` for a path that is not there, and whatever else the open
            refuses with. A **directory** opens here and is refused by the
            caller's ``read`` with ``EISDIR``, which is where it was refused
            before.
    """
    # The mode is passed and is never applied: `READ_FLAGS` carries no `O_CREAT`,
    # so this call cannot create a file and the argument reaches nothing. It is
    # written out because `os.open`'s own default is `0o777`, and a reader -- a
    # person or a static analyser, and CodeQL's `py/overly-permissive-file` did --
    # takes an omitted mode for the mode this call would create with. Spelling the
    # restrictive one costs nothing and says which answer is intended if a future
    # edit ever adds the flag that would use it.
    descriptor = os.open(path, READ_FLAGS, _SECRET_READ_MODE)
    _refuse_an_irregular_descriptor(descriptor, path)
    return descriptor


def write_text_without_following_a_link(
    path: Path, text: str, *, mode: int = _DEFAULT_CREATE_MODE
) -> None:
    """Replace ``path``'s contents with ``text``, refusing a link at the target.

    The drop-in for ``Path.write_text(text, encoding="utf-8")`` at a path an
    attacker can plant. UTF-8 like every other file Theurian writes, and
    ``newline=""`` so the bytes on disk are the bytes given: these targets are
    JSON documents another process parses, and a platform that rewrote ``\\n``
    would make one machine's pointer differ from another's for no reason a reader
    could see.

    Raises:
        OSError: As :func:`open_without_following_a_link`, plus whatever the
            write itself refuses with.
    """
    descriptor = open_without_following_a_link(path, mode=mode)
    try:
        handle = os.fdopen(descriptor, "w", encoding="utf-8", newline="")
    except BaseException:
        # `os.fdopen` takes ownership of the descriptor only once it returns, so
        # this arm is the one place the descriptor would leak.
        os.close(descriptor)
        raise
    with handle:
        handle.write(text)


def is_a_symbolic_link_refusal(exc: OSError) -> bool:
    """Whether ``exc`` is ``O_NOFOLLOW`` declining a symbolic link.

    POSIX mandates ``ELOOP`` when ``O_NOFOLLOW`` is set and the final component
    is a symbolic link, and that is what this platform returns (measured on
    macOS 26.6: errno 62). Keyed on the errno rather than on an ``is_symlink()``
    probe beside the open, because a probe is a decision taken before the call it
    describes and the window between the two is a window an attacker picks; the
    kernel's own answer for *this* call has no such window.

    **Two platforms this project does not build on answer the same condition with
    a different errno**, recorded so nobody reads the check as portable: FreeBSD
    returns ``EMLINK`` and NetBSD ``EFTYPE``. Neither is measured here -- there is
    no such machine on this project -- and neither is in CI, whose matrix is
    ubuntu and macOS. A port to either needs this predicate widened, and would
    otherwise degrade silently: the refusal still happens (the open still
    declines), but it would be graded as an ordinary write fault and publish the
    wrong cure.
    """
    return exc.errno == errno.ELOOP


def symbolic_link_remedy(path: Path) -> str:
    """The cure for a symbolic link at a derived path **inside a project tree**.

    Every clause it publishes rests on that precondition: the artefact is derived
    (ADR-0004), so removing the link costs nothing that is not rebuilt; and a
    repository carrying one has force-added it past that ignore, which is only
    sayable of a path a clone can deliver.

    **Five call sites, not four** -- the count this docstring gave before round two
    omitted the write lock, which is the face the class started from. Reproduce
    with ``git grep -n 'symbolic_link_remedy(' -- packages/theurian-core/src``:
    the definition, plus ``connection.py`` (``.theurian/runtime/write.lock``),
    ``commands.py`` twice (the active pointer's temporary and the ingestion
    manifest), and ``index_commands.py`` twice (the index pointer's temporary and
    the secret-scan record's). All five are under ``.theurian/``.

    **Two faces are deliberately not call sites**, because the precondition fails
    for them and the text would be false:

    * the local access token
      (:class:`~theurian.infrastructure.secrets.file_store.SecretPathIsASymbolicLinkError`)
      -- per-user, not derived, reachable by no repository -- carries a cure of
      its own naming ``theurian auth rotate`` and the directory's permissions;
    * a section-B path that Theurian does **not** guard, such as
      ``<data_dir>/provenance.json.tmp``, gets ``commands.py``'s
      ``_UNGUARDED_LINK_REMEDY`` -- which claims no refusal and mentions no
      repository, both of which this text would get wrong (round two, H-D).

    Naming the leaf rather than the directory holding it is what the
    final-component bound above buys. The *containment* refusal is the other way
    round: its culprit can sit anywhere between the derived subdirectory and the
    leaf, so it can only name the directory, and the application layer spells that
    one beside the helper that raises it.
    """
    return (
        f"Remove the symbolic link at {path} and retry. It is derived state "
        f"(ADR-0004) that Theurian recreates, so nothing authored is lost -- and "
        f"a repository that carries one has committed it past that ignore."
    )


def irregular_artefact_remedy(path: Path, shape: str) -> str:
    """The cure for a named pipe, socket or device at a derived path.

    :func:`symbolic_link_remedy`'s sibling, and it lives beside it for that
    reason: both remove a derived artefact so Theurian can recreate what belongs
    there, and a cure that deletes something must have one spelling across every
    site that publishes it. The link keeps its own text because its danger is
    different -- opening it *writes through* to a file the operator did author --
    while these shapes destroy nothing and merely cannot be what was asked for.

    Shared by the three openers that meet these shapes: the write lock
    (``connection.WriteLockUnusableError``), the daemon's instance lock
    (``daemon/instance.py::InstanceLock``) and the review-finding store
    (``findings_store.SqliteReviewFindingStore._read``). The state database has
    its own, because its cure names ``theurian migrate apply`` rather than a
    plain retry.

    ``ls -l`` rather than a bare instruction to remove: an operator who did not
    put the artefact there needs to see what it is before deleting anything under
    a path Theurian owns.
    """
    return (
        f"Remove {path} and retry: it is {shape}, and Theurian needs a regular file "
        f"there. `ls -l {path}` shows what is at the path now. It is derived state "
        f"(ADR-0004) that Theurian recreates, so nothing authored is lost."
    )


__all__ = [
    "READ_FLAGS",
    "WRITE_FLAGS",
    "irregular_artefact_remedy",
    "is_a_symbolic_link_refusal",
    "open_for_reading_without_following_a_link",
    "open_without_following_a_link",
    "symbolic_link_remedy",
    "write_text_without_following_a_link",
]
