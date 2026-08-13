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

from pathlib import Path
from typing import Final

from theurian.domain.errors import SecurityError
from theurian.security.tokens import TOKEN_ENV_VAR

#: The token file's name inside the auth directory.
TOKEN_KEY = "mcp-token"  # noqa: S105 - a file name, not a secret

#: Delimits the lines setup owns. Everything outside the pair belongs to whoever
#: wrote it and is preserved byte for byte (SEC-18).
ENV_BLOCK_START: Final = "# >>> theurian >>>"
ENV_BLOCK_END: Final = "# <<< theurian <<<"


class MalformedEnvBlockError(SecurityError):
    """The markers in an env file do not delimit exactly one Theurian block.

    Refused rather than repaired. Once the delimiters disagree, setup cannot
    tell which lines are its own, and every way of guessing ends in editing
    lines a person wrote — which is the thing the block exists to prevent
    (SEC-18). The probe turns this into a ``Conflicting`` step, so the run asks
    instead of writing, and ``--approve-conflicts`` does not buy an overwrite
    either: approval is consent to proceed past a conflict.
    """

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(
            f"{reason} Theurian rewrites only the lines between "
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


def find_theurian_block(content: str) -> tuple[int, int] | None:
    """Locate the Theurian block, as ``(start, end)`` slice bounds.

    ``None`` when there is no block at all, which is an ordinary answer: an env
    file predating the markers, or one a person wrote themselves.

    Raises:
        MalformedEnvBlockError: A start marker with no end after it, or a second
            start marker. Both are states an edit by hand produces, and neither
            has a safe repair: an unterminated block has no boundary to stop a
            rewrite at, and with two blocks, rewriting either one leaves the
            other exporting a different token path — whichever comes last in the
            file is what the shell would end up with, and setup would report the
            machine converged.

    An *end* marker with no start before it is not malformed. It delimits
    nothing, the search below never begins inside it, and a stray comment line
    is not a reason to refuse a person their setup.
    """
    start = content.find(ENV_BLOCK_START)
    if start == -1:
        return None

    end_marker = content.find(ENV_BLOCK_END, start)
    if end_marker == -1:
        raise MalformedEnvBlockError("The Theurian block is never terminated.")

    end = end_marker + len(ENV_BLOCK_END)
    if ENV_BLOCK_START in content[end:]:
        raise MalformedEnvBlockError("The file holds more than one Theurian block.")
    return start, end


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


def merge_env_file(existing_content: str | None, data_dir: Path) -> str:
    """The file's new contents, with the Theurian block current and the rest kept.

    ``existing_content`` is ``None`` for a file that is not there. The five cases,
    in the order they are tried:

    ==============================  ========================================
    the file holds                  the result
    ==============================  ========================================
    nothing (absent or empty)       the block alone
    a current block                 unchanged — the merge is idempotent, and
                                    that is what lets the probe ask "would
                                    this write anything?"
    a stale block                   the block replaced where it stands, so
                                    lines before and after keep their order
    the pre-marker rendering        that rendering replaced in place by the
                                    block, alone or surrounded by the user's
                                    own lines — never duplicated beside it
    no Theurian material at all     the block appended after a blank line
    ==============================  ========================================

    Every byte outside the block or the legacy rendering survives, including
    trailing whitespace and a file that ends without a newline; the result
    always ends with one, because a block whose last line is a marker the next
    run has to find is not a place to economise.

    Raises:
        MalformedEnvBlockError: Propagated from :func:`find_theurian_block`.
            Raised before anything is written, so a caller that opens the file
            after computing this leaves it untouched.
    """
    block = env_block(data_dir)
    if not existing_content:
        return f"{block}\n"

    span = find_theurian_block(existing_content)
    if span is not None:
        start, end = span
        return _terminated(existing_content[:start] + block + existing_content[end:])

    legacy = legacy_env_file_contents(data_dir)
    # The rendering ends in a newline, so replacing it with the block plus one
    # keeps the surrounding lines separated. The trimmed form is the same file
    # after an editor that strips the final newline, and it can only match at
    # the end -- anywhere else, the full form matched first.
    for rendered, replacement in ((legacy, f"{block}\n"), (legacy.rstrip("\n"), block)):
        if rendered in existing_content:
            return _terminated(existing_content.replace(rendered, replacement, 1))

    separator = "" if existing_content.endswith("\n") else "\n"
    return f"{existing_content}{separator}\n{block}\n"


def _terminated(content: str) -> str:
    return content if content.endswith("\n") else f"{content}\n"
