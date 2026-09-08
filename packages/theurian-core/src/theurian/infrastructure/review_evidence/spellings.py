"""One spelling on disk: what is already there, and where two names differ.

Round two's R2-C, split out of
:mod:`theurian.infrastructure.review_evidence.store` when that module passed this
project's 800-line ceiling. The two halves of that rule -- *fold to find,
byte-compare to accept* -- are applied at opposite seams, so neither of these
belongs to one of them: :class:`OnDiskSpellings` is what the write consults
before it lets the filesystem resolve a derived name, and
:func:`first_differing_component` is what the read hands its cure so the rename
it publishes names a component pair rather than two whole paths.

Both lost a leading underscore on the way here, being spelled across modules
now: ``store.ReviewEvidenceStore.write`` constructs the first, and
``reader.EvidenceReader._read_one`` calls the second.
"""

from __future__ import annotations

import os
from pathlib import Path, PurePosixPath
from typing import final


@final
class OnDiskSpellings:
    """Which spelling of each derived path component the filesystem already holds.

    The write half of round two's R2-C. ``mkdir(exist_ok=True)`` and
    ``os.replace`` both let the *filesystem* resolve a name, and a filesystem
    that folds case resolves ``pull-request`` to a ``Pull-Request`` that is
    already there -- measured on APFS: the ``mkdir`` succeeds silently, the entry
    keeps its original spelling, and a rename onto ``42.json`` beside an existing
    ``42.JSON`` lands the bytes under ``42.JSON``. So the record is written and
    then sits under a name this build never chose.

    The answer is a refusal rather than a fold, for the reason ``reader._stored``
    records: the derived path is an opaque key two layers above, and accepting a
    second spelling for one record is what makes their arithmetic wrong.

    **Scanned once per parent per ``write`` call**, which is where the cost is:
    a run touches the root, one repository directory and at most three kind
    directories, so this is at most five ``scandir`` calls however many records
    it lands. A directory the run itself creates after its parent was scanned
    reads as absent, and that under-reports on purpose -- the only entries a run
    adds are the byte-exact derived spellings, so a name it created can never be
    the variant this looks for.

    ``OSError`` is swallowed per parent, because a directory that cannot be
    listed is the caller's next refusal with a better message: ``mkdir`` and the
    open both meet it a line later, and grading it here would publish a cure
    about a *spelling* over a permission fault.
    """

    def __init__(self, root: Path) -> None:
        self._root = root
        self._entries: dict[Path, dict[str, str]] = {}

    def differently_spelled(self, relative: str) -> tuple[str, str] | None:
        """The first component of ``relative`` the disk spells another way.

        Returns the on-disk spelling and the derived one, or ``None`` when every
        component either matches byte for byte or is not there at all.
        """
        here = self._root
        for component in PurePosixPath(relative).parts:
            existing = self._folded(here).get(component.casefold())
            if existing is not None and existing != component:
                return existing, component
            here = here / component
        return None

    def _folded(self, parent: Path) -> dict[str, str]:
        """``{casefolded name: on-disk name}`` for one directory, scanned once."""
        if parent not in self._entries:
            try:
                self._entries[parent] = {
                    entry.name.casefold(): entry.name for entry in os.scandir(parent)
                }
            except OSError:
                self._entries[parent] = {}
        return self._entries[parent]


def first_differing_component(on_disk: str, derived: str) -> tuple[str, str]:
    """The first component pair the two paths spell differently.

    Both are the same layout's three components, so the two agree up to the one
    the filesystem folded and the pair returned is the rename an operator can
    actually perform. The whole-path fallback is for a shape this layout does not
    emit -- paths of different depth, or two that differ nowhere -- where naming
    the pair given is better than naming nothing.
    """
    here = PurePosixPath(on_disk).parts
    there = PurePosixPath(derived).parts
    for found, wanted in zip(here, there, strict=False):
        if found != wanted:
            return found, wanted
    return on_disk, derived
