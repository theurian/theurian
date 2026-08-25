"""What a shared setup report may say about things Theurian did not write (O-3, SEC-6).

``doctor --report`` is redacted two ways, and only one of them lives in the CLI.
``cli/setup_commands._redacted`` substitutes the paths the local
:class:`SetupContext` holds -- an allowlist, so by construction it reaches only
values Theurian itself put into the payload. Every setup step also reports on
something it merely *read*: Claude Code's MCP entry, a LaunchAgent plist or
systemd unit, another daemon's reply, the project registry, an exception raised
by a library. A string from any of those was never held by this process, so
there is no anchor to substitute and it goes out verbatim -- which is how a
literal ``Authorization: Bearer <token>`` once left a payload that said
``redacted: true``.

This module is the other half: every sentence a step publishes *instead of* what
it read. They are gathered here rather than left beside each probe so that the
rule has one place to be read, one place to be tested, and one place for the
next person to look when they add a step.

**The rule, for whoever adds the next one.** If a value came from a file, a
process or an exception that Theurian did not author, it is withheld when
:attr:`SetupContext.for_publication` is set -- and a *name* read out of such a
place is a value too, not schema, because it is whatever string sat in key
position in somebody else's file (see :class:`DifferingFields`).
"""

from __future__ import annotations

from pathlib import Path
from typing import Final

from theurian.application.project_service import ProjectError, ProjectRegistry
from theurian.domain.setup import DifferingFields

#: Stands in for another daemon's data directory. That path came off the wire
#: from a process this one does not own, so it is a path the local context never
#: held; which directory it is only matters to the person who has to go and stop
#: it, and they are reading the unredacted output.
ANOTHER_DATA_DIRECTORY: Final = "<another data directory>"

_SEE_THE_VALUES: Final = "run `theurian doctor` without --report to see them"

#: Ceiling, in characters, on what :func:`failure_detail` returns for the
#: operator's own terminal.
#:
#: **A dependency's habits are not a limit this project has recorded.**
#: ``SetupService._probe`` catches ``Exception``, so the string is whatever the
#: raising library chose to build and nothing in Theurian bounded it. The widest
#: channel in practice is a migration refusal, and it is short only because
#: PyYAML truncates its own snippet -- a syntax error 5,000 characters into one
#: line renders at 169 (measured 2026-08-26, PyYAML 6). That is an
#: implementation detail, revisable in any release of somebody else's package,
#: and a channel whose width is decided elsewhere is a channel Theurian has not
#: decided about.
#:
#: 2,000 is an order of magnitude above every message measured on these paths and
#: well below what buries a terminal. It bounds the whole returned string, type
#: name and marker included, so the guarantee is the one the name states.
MAX_FAILURE_DETAIL_CHARS: Final = 2_000

#: Put in place of what was cut, so a bounded message cannot be read as a
#: complete one. Far shorter than :data:`MAX_FAILURE_DETAIL_CHARS`, which is what
#: keeps the slice in :func:`failure_detail` non-negative.
_TRUNCATION_MARKER: Final = " ... [truncated]"


def withheld_difference(subject: str, fields: DifferingFields) -> str:
    """What may be said about a configuration Theurian did not write.

    The whole difference is withheld and what is published is the names of the
    fields Theurian's own renderer produces. Anything else that differs is
    counted, never named -- :class:`DifferingFields` carries why that
    distinction is the load-bearing one.

    An unreadable definition gets its own sentence. A plist too damaged to parse
    produced the same words as a plist differing in eight keys, asserting that
    values existed and had been held back when in fact none had been parsed --
    which deleted the single most useful fact for whoever reads the issue, on an
    input that needed no withholding at all.
    """
    if fields.unreadable:
        return (
            f"{subject} {fields.unreadable}, so Theurian cannot say which fields "
            f"differ. Nothing is withheld here -- there were no values to read; run "
            f"`theurian doctor` without --report for the exact message."
        )

    said: list[str] = []
    if fields.named:
        said.append(f"Fields that differ: {', '.join(fields.named)}.")
    if fields.unnamed:
        one = fields.unnamed == 1
        said.append(
            f"{fields.unnamed} further {'field differs' if one else 'fields differ'} "
            f"under names Theurian does not write, so the names are withheld with "
            f"the values."
        )
    detail = f" {' '.join(said)}" if said else ""
    return (
        f"{subject} differs from what Theurian would install.{detail} The installed "
        f"values are withheld from a shared report because Theurian did not write "
        f"them; {_SEE_THE_VALUES}."
    )


def unreadable_registry_summary(registry: ProjectRegistry, root: Path) -> str:
    """Why the registry could not say, in terms that shape of failure allows.

    Two different refusals reach :meth:`ProjectRegistry.ids_for_root`'s caller and
    only one of them is about an *entry*. A file whose top level does not parse
    -- not JSON, a JSON array, arbitrary bytes -- has no entries to speak of, so
    "holds an entry that cannot be read" is a claim nothing supports: it invites
    the reader to go and find the offending line in a file that has none, and it
    disagreed in kind with the ``detail`` beside it, which already carried the
    file-level cure.

    Told apart by asking for the ids: an unreadable *entry* leaves the set
    computable and non-empty, an unreadable *file* leaves it uncomputable. A
    second read of a small file is the honest price -- the alternative is
    inferring the shape from the exception's message text.
    """
    try:
        registry.unreadable_ids()
    except ProjectError:
        return (
            f"Cannot tell whether {root} is registered: {registry.path} cannot be "
            f"read at all, so nothing in it can be checked."
        )
    return (
        f"Cannot tell whether {root} is registered: {registry.path} holds "
        f"an entry that cannot be read, and it might be this repository's own."
    )


def withheld_registry_detail(registry: ProjectRegistry) -> str:
    """What may be said about a project registry that cannot be read.

    :meth:`ProjectRegistry.ids_for_root` names the offending ids, and its remedy
    names them again inside the ``theurian project unregister`` commands that
    remove them -- correctly, because that is the only argument that fixes this.
    But a project id is derived from a repository's directory name, so those are
    the names of *other* repositories on this machine, and a bare name is not a
    path: nothing in ``cli/setup_commands._redacted`` has an anchor for it, and
    the ids of every unreadable registration went out with the report.

    A count carries what a reader of a public issue can act on -- whether this is
    one hand edit or a corrupted file -- and the ids stay where they are useful,
    on the terminal of the person who has to type them.
    """
    try:
        count = len(registry.unreadable_ids())
    except ProjectError:
        return (
            "The project registry cannot be read at all. Its contents are withheld "
            "from a shared report; run `theurian doctor` without --report to see why."
        )
    entries = "entry" if count == 1 else "entries"
    return (
        f"{count} registry {entries} cannot be read. The ids and the commands that "
        f"remove them are withheld from a shared report because they name other "
        f"repositories on this machine; {_SEE_THE_VALUES}."
    )


def failure_detail(exc: Exception, *, for_publication: bool) -> str:
    """What a broken probe may say, given where the report is going.

    The message an exception carries is whatever raised it, and every probe
    reads something Theurian did not write: a line of somebody's configuration,
    a reply from another process, a path outside the roots
    ``cli/setup_commands._redacted`` substitutes. There is no bound on it, so
    there is no argument that publishing it is safe -- and no need for one,
    because the type is what a reader of a shared issue acts on. The message
    stays on the terminal, where ``theurian doctor`` prints it in full.

    Recorded as a decision rather than left to a future reviewer to rediscover:
    an arbitrary exception string is not publishable, and the deliberate cost is
    that a bug report opens with a type name.

    **The terminal's copy is bounded too**, by
    :data:`MAX_FAILURE_DETAIL_CHARS`, with :data:`_TRUNCATION_MARKER` in place of
    what was dropped. Withholding decides *whether* the message travels;
    the bound decides *how wide the channel is*, and that had been left to
    whichever library raised -- see the constant for why a dependency's own
    truncation is not an answer. The publication branch is untouched: it carries
    no message at all, so there is nothing there to cut.
    """
    if for_publication:
        return (
            f"{type(exc).__name__}. The message is withheld from a shared report "
            f"because an exception carries whatever raised it; run `theurian doctor` "
            f"without --report to see it."
        )
    detail = f"{type(exc).__name__}: {exc}"
    if len(detail) <= MAX_FAILURE_DETAIL_CHARS:
        return detail
    kept = MAX_FAILURE_DETAIL_CHARS - len(_TRUNCATION_MARKER)
    return f"{detail[:kept]}{_TRUNCATION_MARKER}"
