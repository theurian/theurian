"""Merging the Theurian block into a file somebody else also writes (#128, SEC-18).

`theurian setup` and `theurian auth rotate` both rendered the *whole* env file
and truncated whatever else was in it, so a line added to a file whose own
header says "Sourced by your shell profile" disappeared with no diff, no backup
and no mention in the report. §6.2 row 7 had required "rewrite the
Theurian-owned block only" throughout.

Pure text in, pure text out: :func:`merge_env_file` touches no disk, and the
cases below are the states a real machine actually presents -- a file written
before the markers existed, a stale block with lines on both sides of it, and
markers a hand edit left in a state with no safe repair. The two writers that
consume this decision are pinned in
``tests/integration/test_setup_env_file.py``; what is here is the decision.

**Every survival assertion is on the whole result, never on a substring.** The
defect being pinned *removed* bytes, and "the block is present" is equally true
of a file that lost everything else -- which is precisely how it shipped.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from theurian.security.env_file import (
    ENV_BLOCK_END,
    ENV_BLOCK_START,
    MalformedEnvBlockError,
    contains_current_block,
    env_block,
    env_file_contents,
    find_theurian_block,
    legacy_env_file_contents,
    merge_env_file,
)

pytestmark = pytest.mark.unit

#: Never resolved, never opened. The merge is a function of this path's *text*,
#: which is what lets these tests state their expectations as whole strings.
DATA_DIR = Path("/home/example/.theurian")

BEFORE = "export MY_OTHER_VAR=1\n# a comment of mine\n\n"
AFTER = "export AFTER_THE_BLOCK=2\n  trailing whitespace kept  \n"


def _stale_block() -> str:
    """This machine's block as an older install left it: a different data dir."""
    return env_block(Path("/somewhere/else/.theurian"))


# -- What survives a rewrite -------------------------------------------------


def test_a_stale_block_is_replaced_where_it_stands() -> None:
    """#128. The bytes on either side of the block are not setup's to move.

    Asserted as the exact whole file, so the two ways of "keeping" the user's
    lines that are not keeping them -- dropping one side, or re-emitting them in
    a different order after the block -- both fail here. A membership check on
    ``BEFORE`` and ``AFTER`` would pass on a file that had reordered them, and
    order is meaning in a shell snippet: a later assignment wins.
    """
    existing = BEFORE + _stale_block() + "\n" + AFTER

    merged = merge_env_file(existing, DATA_DIR)

    assert merged == BEFORE + env_block(DATA_DIR) + "\n" + AFTER


def test_a_file_with_no_theurian_material_keeps_all_of_it() -> None:
    """A file a person wrote themselves is appended to, never replaced.

    The env file is created by setup, but it is not the only thing that ever
    creates it -- a dotfiles repository can carry one, and on that machine the
    first `theurian setup` is the run that would have deleted it.
    """
    existing = "export SOMETHING_ENTIRELY_MINE=1\n"

    merged = merge_env_file(existing, DATA_DIR)

    assert merged == existing + "\n" + env_block(DATA_DIR) + "\n"


def test_a_file_that_ends_without_a_newline_keeps_its_last_line() -> None:
    """An editor that strips the final newline must not cost the user a line.

    Without the separator this appends the marker to the end of whatever the
    last line was, producing ``export MINE=1# >>> theurian >>>`` -- one line the
    shell reads as neither, and the user's assignment silently gone.
    """
    merged = merge_env_file("export MINE=1", DATA_DIR)

    assert merged == "export MINE=1\n\n" + env_block(DATA_DIR) + "\n"


def test_a_current_block_is_left_exactly_as_it_is() -> None:
    """The fixed point the probe's whole question rests on.

    ``probe_env_reference`` asks "would applying this write anything?" by asking
    whether the current block is already there. If the merge were not a fixed
    point over its own output, a converged machine would rewrite the file on
    every run -- and every rewrite is another chance to lose the lines around it.
    """
    existing = BEFORE + env_block(DATA_DIR) + "\n" + AFTER

    assert merge_env_file(existing, DATA_DIR) == existing
    assert merge_env_file(merge_env_file(existing, DATA_DIR), DATA_DIR) == existing


# -- Upgrading a file written before the markers existed ---------------------


def test_the_pre_marker_rendering_is_replaced_rather_than_appended_beside() -> None:
    """0.1.0.dev0 through dev2 wrote this as the whole file, on every machine.

    Appending the block beside it leaves the file exporting the variable twice.
    That is not merely untidy: the two assignments can name different token
    paths after a data directory moves, and the last one is what the shell ends
    up with -- so the machine would export a path setup did not choose while
    setup reported it converged.

    The count is asserted on the export lines rather than on the block, because
    a duplicate is what the append-only mistake produces and a block check does
    not see one.
    """
    merged = merge_env_file(legacy_env_file_contents(DATA_DIR), DATA_DIR)

    assert merged == env_block(DATA_DIR) + "\n"
    assert merged.count("export THEURIAN_MCP_TOKEN\n") == 1
    assert merged.count('THEURIAN_MCP_TOKEN="$(cat ') == 1


def test_upgrading_the_pre_marker_rendering_keeps_the_lines_around_it() -> None:
    """The machine that has both: a dev2 file, and lines added to it since.

    Replaced *in place*, so a line the user put before the exports stays before
    them. The same-order argument as the stale-block case, on the population
    that actually exists in the wild -- every machine set up by a released
    version carries this rendering byte for byte.
    """
    existing = "export FIRST=1\n" + legacy_env_file_contents(DATA_DIR) + "export LAST=2\n"

    merged = merge_env_file(existing, DATA_DIR)

    assert merged == "export FIRST=1\n" + env_block(DATA_DIR) + "\n" + "export LAST=2\n"
    assert merged.count('THEURIAN_MCP_TOKEN="$(cat ') == 1


def test_the_pre_marker_rendering_is_recognised_without_its_final_newline() -> None:
    """The same file after an editor that strips the last newline.

    Missing this arm is not cosmetic: the rendering would not match, the merge
    would take the append branch, and the file would export the variable twice
    -- the exact duplication the recognition exists to prevent.
    """
    merged = merge_env_file(legacy_env_file_contents(DATA_DIR).rstrip("\n"), DATA_DIR)

    assert merged == env_block(DATA_DIR) + "\n"
    assert merged.count("export THEURIAN_MCP_TOKEN\n") == 1


def test_another_installations_pre_marker_rendering_is_not_recognised() -> None:
    """It is recognised by naming *this* token path, not by looking like it.

    A rendering that points somewhere else was written for a different data
    directory, and replacing it would silently take away the second machine's
    export. The block is appended instead, which leaves both lines visible and
    lets the person choose.
    """
    foreign = legacy_env_file_contents(Path("/opt/another-install/.theurian"))

    merged = merge_env_file(foreign, DATA_DIR)

    assert merged == foreign + "\n" + env_block(DATA_DIR) + "\n"
    assert "/opt/another-install/.theurian/auth/mcp-token" in merged


# -- Markers that cannot be resolved (SEC-18) --------------------------------


def test_an_unterminated_block_is_refused_rather_than_guessed_at() -> None:
    """A start marker with no end has no boundary to stop a rewrite at.

    Every way of guessing where the block ends is a way of deleting lines
    somebody wrote, which is the thing the block exists to prevent. Raised
    rather than repaired, and raised *before* any caller opens the file.
    """
    existing = "export MINE=1\n" + ENV_BLOCK_START + "\nexport THEURIAN_MCP_TOKEN=mine\n"

    with pytest.raises(MalformedEnvBlockError, match="never terminated"):
        merge_env_file(existing, DATA_DIR)


def test_a_second_block_is_refused_because_the_shell_would_use_the_last_one() -> None:
    """Two blocks is not a tidiness problem; it is a wrong answer waiting.

    Rewriting either one leaves the other exporting a different token path, and
    a shell sourcing the file top to bottom ends on whichever comes last. Setup
    would report the machine converged while it exported a path setup did not
    choose -- which is why this is refused and not merged.
    """
    existing = env_block(Path("/a")) + "\n" + env_block(Path("/b")) + "\n"

    with pytest.raises(MalformedEnvBlockError, match="more than one"):
        merge_env_file(existing, DATA_DIR)


def test_the_refusal_says_how_to_repair_it_by_hand() -> None:
    """The message is the entire remedy: nothing repairs this automatically.

    It reaches a person through ``probe_env_reference``'s conflict detail and
    through ``auth rotate``'s ``nextSteps``, and in both places it is all they
    get. So it has to carry both markers -- the strings they must go and look
    for -- and the command to re-run once they have.
    """
    with pytest.raises(MalformedEnvBlockError) as raised:
        merge_env_file(ENV_BLOCK_START + "\n", DATA_DIR)

    message = str(raised.value)
    assert ENV_BLOCK_START in message
    assert ENV_BLOCK_END in message
    assert "theurian setup" in message


def test_an_end_marker_with_no_start_delimits_nothing_and_is_no_reason_to_refuse() -> None:
    """Refusing here would take a person's setup away over a stray comment.

    A lone end marker bounds nothing: there is no block above it to rewrite and
    no line below it in danger. The block is appended and the line is kept, so
    the file ends up with both -- which is honest and which the person can then
    tidy.
    """
    existing = f"# I copied this line out of a blog post\n{ENV_BLOCK_END}\n"

    merged = merge_env_file(existing, DATA_DIR)

    assert merged == existing + "\n" + env_block(DATA_DIR) + "\n"
    assert find_theurian_block(existing) is None


# -- The probe's question ----------------------------------------------------


def test_the_convergence_question_is_blind_to_lines_outside_the_block() -> None:
    """#128's other half: what "already done" is compared against.

    The probe used to compare the whole file, so every user who had appended a
    line to it was told ``Missing`` on a converged machine -- and the apply that
    followed is what destroyed the line. Answering on the block alone is what
    makes the report true and the rewrite unnecessary.
    """
    with_extras = BEFORE + env_block(DATA_DIR) + "\n" + AFTER

    assert contains_current_block(with_extras, DATA_DIR) is True
    assert contains_current_block(BEFORE + _stale_block() + "\n" + AFTER, DATA_DIR) is False
    assert contains_current_block("export NOTHING_OF_OURS=1\n", DATA_DIR) is False


def test_a_fresh_file_is_the_block_and_nothing_else() -> None:
    """The absent-file case, stated where both callers can see it.

    ``env_file_contents`` is the merge with ``None``; the difference between
    them is exactly a user's appended lines, and a fresh install has none.
    """
    assert env_file_contents(DATA_DIR) == env_block(DATA_DIR) + "\n"
    assert merge_env_file(None, DATA_DIR) == env_file_contents(DATA_DIR)
    assert merge_env_file("", DATA_DIR) == env_file_contents(DATA_DIR)
