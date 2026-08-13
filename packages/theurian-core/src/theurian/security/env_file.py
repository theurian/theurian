"""The shell snippet that exports the token by reference (SEC-5, ADR-0011).

Pure text. It lives here rather than beside the file-backed secret store because
the setup steps in the application layer render it, and the application layer
depends on ports rather than adapters (ADR-0003).

**Theurian owns a marked block inside that file, not the file.** Its own header
says "Sourced by your shell profile", which is an invitation to add lines to it,
and until this module grew markers every apply opened it ``O_TRUNC`` and rewrote
the whole thing: a line the user had added was gone with no diff, no backup and
no mention in the report, on every ``theurian setup`` and every ``theurian auth
rotate`` (`#128 <https://github.com/theurian/theurian/issues/128>`_). §6.2 row 7
had required "rewrite the Theurian-owned block only" the whole time.

The markers are spelled exactly as the pair `theurian init` writes into a
repository's ``.gitignore`` (:data:`~theurian.domain.project.GITIGNORE_BLOCK_START`),
because someone who has seen one of Theurian's managed blocks should recognise
the other on sight. They are separate literals rather than one import: the two
files are edited by different code for different reasons, and a shared constant
would make renaming the marker in one of them silently rewrite the other.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Final

from theurian.domain.errors import SecurityError
from theurian.security.tokens import TOKEN_ENV_VAR

#: The token file's name inside the auth directory.
TOKEN_KEY = "mcp-token"  # noqa: S105 - a file name, not a secret

#: Delimits the lines setup owns. Everything outside the pair belongs to whoever
#: wrote it and is preserved byte for byte (SEC-18). A marker is recognised as a
#: whole *line* and never as a substring: see :func:`find_theurian_block`.
ENV_BLOCK_START: Final = "# >>> theurian >>>"
ENV_BLOCK_END: Final = "# <<< theurian <<<"


class EnvBlockFault(StrEnum):
    """Why the markers cannot be resolved, as a closed set of sentences.

    An enum rather than a string parameter because these sentences are
    *published*: ``probe_env_reference`` puts the exception's message in a step
    detail, and ``doctor --report`` prints step details into an issue somebody
    pastes in public. Every other published probe failure goes through
    :func:`~theurian.application.setup_withholding.failure_detail`, which
    withholds an exception's message precisely because an exception carries
    whatever raised it. This one is exempt only for as long as it can carry
    nothing else — so the constructor takes a member of this enum and not a
    string, and a future "line 14 says ``export AWS_SECRET…``" cannot be handed
    to it without changing this type first.
    """

    UNTERMINATED = "The Theurian block is never terminated."
    #: Not "more than one block": the second start marker is usually *not*
    #: terminated -- a person repairing an unterminated block by pasting a whole
    #: one below it leaves ``S … S … E`` -- and the file that holds two complete
    #: blocks is the rarer shape. What both have is a second start.
    REPEATED_START = "The file holds more than one Theurian start marker."


class MalformedEnvBlockError(SecurityError):
    """The markers in an env file do not delimit exactly one Theurian block.

    Refused rather than repaired. Once the delimiters disagree, setup cannot
    tell which lines are its own, and every way of guessing ends in editing
    lines a person wrote — which is the thing the block exists to prevent
    (SEC-18). The probe turns this into a ``Conflicting`` step, so the run asks
    instead of writing, and ``--approve-conflicts`` does not buy an overwrite
    either: approval is consent to proceed past a conflict.

    **The message is publishable, and stays that way by construction**: it is
    assembled from :class:`EnvBlockFault` and the two marker constants, none of
    which came out of the file being described. See :class:`EnvBlockFault`.
    """

    def __init__(self, fault: EnvBlockFault) -> None:
        self.fault = fault
        super().__init__(
            f"{fault.value} Theurian rewrites only the lines between "
            f"{ENV_BLOCK_START!r} and {ENV_BLOCK_END!r}. Repair the markers by "
            f"hand, or delete the block along with its lines, then re-run "
            f"`theurian setup`."
        )


def env_block(data_dir: Path) -> str:
    """The Theurian-owned block, marked, without a trailing newline.

    The header states the preservation rule inside the file itself. A person who
    is deciding whether it is safe to append to this file reads it there, not in
    the documentation.
    """
    token_path = data_dir / "auth" / TOKEN_KEY
    return (
        f"{ENV_BLOCK_START}\n"
        "# Written by `theurian setup`. Sourced by your shell profile so that\n"
        "# Claude Code can expand ${THEURIAN_MCP_TOKEN} in its MCP configuration\n"
        "# without the literal token ever entering a config file (ADR-0011).\n"
        "#\n"
        "# Theurian rewrites only the lines between these two markers. Anything\n"
        "# you add outside them is left exactly as you wrote it.\n"
        f'{TOKEN_ENV_VAR}="$(cat "{token_path}")"\n'
        f"export {TOKEN_ENV_VAR}\n"
        f"{ENV_BLOCK_END}"
    )


def legacy_env_file_contents(data_dir: Path) -> str:
    """What 0.1.0.dev0 through dev2 wrote as the *whole* file.

    Kept because every machine those versions set up has exactly this on disk,
    byte for byte — it is a function of ``data_dir`` alone — so :func:`merge_env_file`
    can recognise it and replace it with the marked block. Without that, the
    first run of a newer version would append a second copy of the same two
    exports and leave the file with both.

    Not a template for anything new. Nothing renders this any more.
    """
    token_path = data_dir / "auth" / TOKEN_KEY
    return (
        "# Written by `theurian setup`. Sourced by your shell profile so that\n"
        "# Claude Code can expand ${THEURIAN_MCP_TOKEN} in its MCP configuration\n"
        "# without the literal token ever entering a config file (ADR-0011).\n"
        f'{TOKEN_ENV_VAR}="$(cat "{token_path}")"\n'
        f"export {TOKEN_ENV_VAR}\n"
    )


def env_file_contents(data_dir: Path) -> str:
    """The whole contents of a *fresh* env file: the block and nothing else.

    Only correct where there is no file yet. Anywhere a file may already exist,
    :func:`merge_env_file` is the function to call — this one is that call with
    ``None``, and the difference between them is a user's appended lines.
    """
    return merge_env_file(None, data_dir)


@dataclass(frozen=True, slots=True)
class _Line:
    """One line of the file, located rather than copied.

    :attr:`end` stops at the last character of :attr:`text`, *before* any
    ``\\r\\n``. Every span in this module is built from these offsets, so a line
    terminator outside the block is never inside a span and never rewritten.
    """

    start: int
    #: The line without its terminator, and without a ``\\r`` that a CRLF file
    #: puts in front of it.
    text: str
    end: int


def _lines(content: str) -> list[_Line]:
    """Split into lines the way a shell reads them.

    ``str.split("\\n")`` and never ``str.splitlines``, which also breaks on
    ``\\v``, ``\\f``, ``\\x1c``, ``\\x85`` and ``\\u2028``. A shell ends a line at
    ``\\n`` and at nothing else, so a marker "line" invented at one of those
    characters would be exactly the mid-line match this scan exists to refuse.

    A trailing ``\\r`` is dropped from the text, so the markers in a file with
    CRLF endings still delimit. The byte stays in ``content`` and outside every
    span, which is what preserves it.
    """
    lines: list[_Line] = []
    offset = 0
    for line in content.split("\n"):
        text = line.rstrip("\r")
        lines.append(_Line(start=offset, text=text, end=offset + len(text)))
        offset += len(line) + 1  # the separator `split` removed
    return lines


def find_theurian_block(content: str) -> tuple[int, int] | None:
    """Locate the Theurian block, as ``(start, end)`` slice bounds.

    ``None`` when there is no block at all, which is an ordinary answer: an env
    file predating the markers, or one a person wrote themselves.

    **A marker is a whole line.** A substring search opens the span at any
    occurrence, and a line such as ``echo "everything between # >>> theurian
    >>> and here"`` is not a marker in any sense a shell would recognise — it is
    a line somebody wrote. Matching it deleted the lines under it *and* cut that
    line in half, leaving an unclosed quote that poisons every line after it in
    a sourced file.

    Raises:
        MalformedEnvBlockError: A start marker with no end after it, or a second
            start marker anywhere in the file. Both are states an edit by hand
            produces, and neither has a safe repair.

    **The start markers are counted over the whole file, before a span is
    chosen** — not, as this once did, over what follows the end marker. A person
    who finds an unterminated block and repairs it by pasting a fresh one
    underneath leaves ``S … S … E``, where the second start is *inside* the span
    the first one opens: the search found one start and one end, called
    everything between them Theurian's, and swallowed the lines in the middle.
    Measured over every arrangement of a start marker, an end marker and a user
    line up to five lines long: 16 of them lost a line that way, one of them an
    ``export AWS_SECRET_ACCESS_KEY``, with the run reporting ``converged`` and
    the re-probe reporting ``satisfied``.

    An *end* marker with no start before it is not malformed. It delimits
    nothing, the search never begins inside it, and a stray comment line is not
    a reason to refuse a person their setup. A second *end* marker is likewise
    left alone: it is a line outside the block, which is a line Theurian keeps.
    """
    lines = _lines(content)
    opened = [line for line in lines if line.text == ENV_BLOCK_START]
    if len(opened) > 1:
        raise MalformedEnvBlockError(EnvBlockFault.REPEATED_START)
    if not opened:
        return None

    start = opened[0]
    closed = next(
        (line for line in lines if line.start > start.start and line.text == ENV_BLOCK_END),
        None,
    )
    if closed is None:
        raise MalformedEnvBlockError(EnvBlockFault.UNTERMINATED)
    return start.start, closed.end


def _find_rendering(content: str, rendering: str) -> tuple[int, int] | None:
    """Locate a run of whole lines equal to ``rendering``, as slice bounds.

    Whole lines, for the reason :func:`find_theurian_block` matches markers as
    lines: the pre-marker rendering ends in ``export THEURIAN_MCP_TOKEN``, and a
    substring search found that inside ``export THEURIAN_MCP_TOKEN  # my note``
    and inside ``export THEURIAN_MCP_TOKEN_EXTRA=1``. Replacing the match glued
    the rest of somebody's line onto the end marker.

    A rendering that has been edited is therefore not recognised, and the block
    is appended below it instead — visible, and the shell keeps the block
    because it comes last. That is the honest answer: an edited line is a line
    somebody wrote.
    """
    wanted = rendering.rstrip("\n").split("\n")
    lines = _lines(content)
    for index in range(len(lines) - len(wanted) + 1):
        window = lines[index : index + len(wanted)]
        if [line.text for line in window] == wanted:
            return window[0].start, window[-1].end
    return None


def contains_current_block(content: str, data_dir: Path) -> bool:
    """Whether the file already holds this ``data_dir``'s block verbatim.

    The probe's whole question. Deliberately blind to everything outside the
    markers: lines a person added are none of setup's business, and treating
    them as a difference is what made the old whole-file comparison report
    ``Missing`` on a converged machine and then overwrite them.

    Raises:
        MalformedEnvBlockError: Propagated from :func:`find_theurian_block`.
    """
    span = find_theurian_block(content)
    return span is not None and content[span[0] : span[1]] == env_block(data_dir)


def contains_shadowing_assignment(content: str) -> bool:
    """Whether a line *below* the block assigns the same variable again.

    A shell sources this file top to bottom, so the last assignment of
    ``THEURIAN_MCP_TOKEN`` is the one it keeps — and the probe's question, "is
    the block current?", is deliberately blind to everything outside the
    markers. Measured: a file holding a current block and, under it, a line a
    person pasted years ago exported that old literal, while setup reported the
    machine converged and the step summary said the file "exports
    THEURIAN_MCP_TOKEN by reference". The block was current; the sentence was
    false anyway.

    Never repaired and never a conflict. That line belongs to whoever wrote it,
    and a conflict would stop the run over a file setup has no business editing
    (SEC-18). It is *reported* instead, which is the difference between a
    degraded report and a false one.

    An assignment, not any mention: a bare ``export THEURIAN_MCP_TOKEN`` below
    the block re-exports what the block just set and changes nothing, and a
    commented-out line is a comment.

    Raises:
        MalformedEnvBlockError: Propagated from :func:`find_theurian_block`.
    """
    span = find_theurian_block(content)
    if span is None:
        return False
    return any(_assigns_token(line.text) for line in _lines(content) if line.start > span[1])


#: Shell words that introduce names to be exported, after which the assignments
#: follow. ``declare``/``typeset``/``readonly`` are here because the point is to
#: notice the state, not to parse a shell.
_EXPORT_KEYWORDS: Final = frozenset({"export", "declare", "typeset", "readonly"})


def _assigns_token(line: str) -> bool:
    """Whether this line assigns :data:`TOKEN_ENV_VAR` in the sourcing shell.

    Only the first word can be an assignment on its own account; a name later
    on a line is an argument, and ``THEURIAN_MCP_TOKEN`` inside ``echo`` is
    somebody talking about the variable rather than setting it.
    """
    words = line.strip().split()
    if not words:
        return False
    candidates = words[1:] if words[0] in _EXPORT_KEYWORDS else words[:1]
    return any(word.startswith(f"{TOKEN_ENV_VAR}=") for word in candidates)


def merge_env_file(existing_content: str | None, data_dir: Path) -> str:
    """The file's new contents, with the Theurian block current and the rest kept.

    ``existing_content`` is ``None`` for a file that is not there. The five cases,
    in the order they are tried:

    ==============================  ========================================
    the file holds                  the result
    ==============================  ========================================
    nothing (absent or empty)       the block alone
    a current block                 unchanged, save for a final newline where
                                    the file had none
    a stale block                   the block replaced where it stands, so
                                    lines before and after keep their order
    the pre-marker rendering        that rendering replaced in place by the
                                    block, alone or surrounded by the user's
                                    own lines — never duplicated beside it
    no Theurian material at all     the block appended after a blank line
    ==============================  ========================================

    Row two is what lets the probe ask "would this write anything?", and the
    qualification is not a hole in that. The files it does not return unchanged
    are exactly the ones that do not end in a newline -- enumerated over a
    current block with lines before and after it, every such file and no other
    -- and :func:`contains_current_block` answers ``True`` for all of them, so
    the probe reports ``Satisfied`` and this function is never called on them.
    The fixed point the probe rests on is over files this function wrote, and
    every one of those ends in a newline.

    Every byte outside the block or the legacy rendering survives, including
    trailing whitespace, ``\\r`` bytes in a file with CRLF endings, and a file
    that ends without a newline; the result always ends with one, because a
    block whose last line is a marker the next run has to find is not a place to
    economise. Preserving those bytes is the *caller's* half too: a reader that
    translates newlines hands this function a file it has already changed, which
    is why both writers open with ``newline=""``.

    Raises:
        MalformedEnvBlockError: Propagated from :func:`find_theurian_block`.
            Raised before anything is written, so a caller that opens the file
            after computing this leaves it untouched.
    """
    block = env_block(data_dir)
    if not existing_content:
        return f"{block}\n"

    span = find_theurian_block(existing_content)
    if span is None:
        span = _find_rendering(existing_content, legacy_env_file_contents(data_dir))
    if span is not None:
        start, end = span
        # `end` stops before the matched last line's terminator, so whatever
        # followed it -- "\n", "\r\n", or nothing at the end of the file -- is
        # in the tail and comes through unchanged.
        return _terminated(existing_content[:start] + block + existing_content[end:])

    separator = "" if existing_content.endswith("\n") else "\n"
    return f"{existing_content}{separator}\n{block}\n"


def _terminated(content: str) -> str:
    return content if content.endswith("\n") else f"{content}\n"
