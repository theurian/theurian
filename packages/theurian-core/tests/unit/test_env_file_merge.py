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

import typing
from itertools import product
from pathlib import Path

import pytest

from theurian.security.env_file import (
    ENV_BLOCK_END,
    ENV_BLOCK_START,
    EnvBlockFault,
    MalformedEnvBlockError,
    contains_current_block,
    contains_shadowing_assignment,
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

#: One line, shaped like the reason this file is 0600, for the shapes where the
#: question is only whether it is still there afterwards.
USER_LINE = "export AWS_SECRET_ACCESS_KEY=SentinelEnvMergeZZZZ\n"


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


def test_a_second_start_marker_above_the_block_is_refused_rather_than_swallowing_the_line() -> None:
    """The shape the first fix missed: ``S``, a user's line, ``S``, the block, ``E``.

    A person who finds an unterminated block and repairs it by pasting a fresh
    one underneath leaves exactly this. The guard then counted a second start
    only in what followed the *end* marker, so this second start was inside the
    span the first one opened: one start, one end, everything between them
    called Theurian's -- and the line in the middle gone, with the run
    reporting ``converged`` and the re-probe reporting ``satisfied``.

    Refused on the count of start markers over the whole file, before a span is
    chosen, which is what makes the position of the second one irrelevant.
    """
    existing = f"{ENV_BLOCK_START}\n{USER_LINE}{env_block(DATA_DIR)}\n"

    with pytest.raises(MalformedEnvBlockError) as raised:
        merge_env_file(existing, DATA_DIR)

    assert raised.value.fault is EnvBlockFault.REPEATED_START


def test_no_arrangement_of_the_markers_loses_a_line_outside_the_block() -> None:
    """The class, not the shape: every file three symbols can build, to five lines.

    One shape at a time is how #128 was found and re-found -- the guard that
    refused ``S E S E`` was written, reviewed and shipped while ``S U S E`` was
    still deleting the line in the middle. So the rule is asserted over the
    whole population instead, and the rule is stated here from the *symbols*
    rather than by asking the code where it thinks the block is, which would
    only ask the implementation to agree with itself.

    The rule: more than one start marker, or a start with no end after it, is
    refused; on every other file, a line outside the one delimited block
    survives verbatim and in its original order. Lines *between* a start and
    the end that closes it are Theurian's own and are replaced -- that is what
    the markers are for.

    363 arrangements, of which 229 are refusals. Measured against the guard
    reverted to its older after-the-end form: 39 arrangements take the wrong
    refusal decision and 16 of them report success while dropping 19 lines
    between them.
    """
    shapes = [shape for size in range(1, 6) for shape in product("SEU", repeat=size)]
    assert len(shapes) == 363, "the population this test claims to sweep"

    surviving = 0
    for shape in shapes:
        content, users = _render(shape)
        try:
            merged = merge_env_file(content, DATA_DIR)
        except MalformedEnvBlockError:
            assert _is_refused(shape), f"{shape} was refused and should not have been"
            continue

        assert not _is_refused(shape), f"{shape} should have been refused"
        surviving += 1
        owned = _owned_lines(shape)
        kept = [line for index, line in users.items() if index not in owned]
        assert all(merged.count(line) == 1 for line in kept), f"{shape} lost a line"
        assert [merged.index(line) for line in kept] == sorted(
            merged.index(line) for line in kept
        ), f"{shape} reordered the lines it kept"

    assert surviving == 134, "and the population that is merged rather than refused"


#: The three symbols any of these files is built from, one per line.
_SYMBOLS = {"S": ENV_BLOCK_START, "E": ENV_BLOCK_END}


def _render(shape: tuple[str, ...]) -> tuple[str, dict[int, str]]:
    """The file that arrangement describes, and the user lines in it by position."""
    lines: list[str] = []
    users: dict[int, str] = {}
    for index, symbol in enumerate(shape):
        line = _SYMBOLS.get(symbol) or f"export USER_{index}=keep"
        if symbol == "U":
            users[index] = line
        lines.append(line)
    return "\n".join(lines) + "\n", users


def _is_refused(shape: tuple[str, ...]) -> bool:
    """Whether SEC-18 says this file cannot be delimited, read off the symbols."""
    starts = [index for index, symbol in enumerate(shape) if symbol == "S"]
    if len(starts) > 1:
        return True
    return bool(starts) and "E" not in shape[starts[0] + 1 :]


def _owned_lines(shape: tuple[str, ...]) -> set[int]:
    """The positions Theurian's markers delimit, and may therefore rewrite."""
    if _is_refused(shape) or "S" not in shape:
        return set()
    start = shape.index("S")
    end = next(index for index, symbol in enumerate(shape) if symbol == "E" and index > start)
    return set(range(start, end + 1))


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


# -- A marker is a whole line, and a line ends at "\n" ------------------------


@pytest.mark.parametrize(
    ("shape", "existing"),
    [
        (
            "a marker quoted inside a line somebody wrote",
            f'echo "everything between {ENV_BLOCK_START} and here"\n',
        ),
        (
            "a marker after a vertical tab, which is not a line ending",
            f'echo "a\v{ENV_BLOCK_START}"\n',
        ),
        (
            "a marker with a trailing comment on the same line",
            f"{ENV_BLOCK_START} # I added this note\n",
        ),
        (
            "a quoted marker with a real end marker somewhere below it",
            f'echo "between {ENV_BLOCK_START} and here"\nexport MINE=1\n{ENV_BLOCK_END}\n',
        ),
    ],
    ids=["quoted", "vertical-tab", "trailing-comment", "quoted-with-an-end-below"],
)
def test_a_marker_that_is_not_the_whole_line_does_not_open_a_block(
    shape: str, existing: str
) -> None:
    """``echo "... # >>> theurian >>> ..."`` is a line somebody wrote, not a marker.

    The substring search took it for one, and both of its outcomes are wrong.
    Measured against the code as it shipped: with an end marker below, the span
    opened in the middle of that line and the rewrite cut it in half -- leaving
    an unclosed quote that poisons every line after it in a sourced file -- and
    took ``export MINE=1`` with it. With no end marker below, the same file was
    refused as an unterminated block, so the person could not set Theurian up at
    all over a line of their own.

    The vertical tab is the same defect through ``str.splitlines``, which breaks
    on ``\\v``, ``\\f``, ``\\x1c`` and ``\\u2028``. A shell ends a line at ``\\n``
    and nothing else, so a "line" invented at one of those is a substring match
    wearing a line's clothes.

    Asserted as the whole file, because "the block is present" is equally true
    of a file that lost the line above it.
    """
    assert find_theurian_block(existing) is None, shape

    merged = merge_env_file(existing, DATA_DIR)

    assert merged == existing + "\n" + env_block(DATA_DIR) + "\n", shape


def test_a_line_ending_carriage_return_still_lets_the_markers_delimit() -> None:
    """A file with CRLF endings is delimited, not appended to a second time.

    ``\\r`` is stripped from a line's text for the comparison and left in the
    content, so the markers match while the byte stays outside every span. Miss
    that and a Windows-edited env file grows a second block on every run, which
    the *next* run refuses as two start markers -- setup breaking a file it was
    asked to converge.
    """
    before, after = BEFORE.replace("\n", "\r\n"), AFTER.replace("\n", "\r\n")
    crlf = before + _stale_block().replace("\n", "\r\n") + "\r\n" + after

    merged = merge_env_file(crlf, DATA_DIR)

    assert merged.count(ENV_BLOCK_START) == 1, "the stale block was replaced, not joined"
    assert merged == before + env_block(DATA_DIR) + "\r\n" + after


def test_the_bytes_outside_the_block_survive_a_merge_of_a_crlf_file() -> None:
    """SEC-18 is about bytes, and a line ending is a byte somebody chose.

    Universal-newline translation is the quiet way to break this: read with it
    on, every ``\\r\\n`` is already ``\\n`` before the merge sees the file, and
    the rewrite hands back a file whose every line ending has changed -- on a
    run whose contract is to touch the lines between two markers. The ``\\r``
    inside the quoted value is the case that is not cosmetic: turned into a
    newline, it splits the assignment in two and the second half is a command.

    Asserted as the exact whole file, and on the count of ``\\r`` outside the
    block, which a global count would hide -- the block itself is rewritten with
    ``\\n`` endings, so the totals legitimately differ.
    """
    keep = 'export GREETING="hello\rworld"\r\n# a comment of mine\r\n'
    after = "export AFTER_THE_BLOCK=2\r\n"
    existing = keep + _stale_block().replace("\n", "\r\n") + "\r\n" + after

    merged = merge_env_file(existing, DATA_DIR)

    assert merged == keep + env_block(DATA_DIR) + "\r\n" + after
    assert merged[: len(keep)] == keep, "the bytes before the block, unchanged"
    assert merged.endswith(after), "and the bytes after it"
    assert merged.count("\r") == 5, "three of the user's line endings, its value's, and the tail"


def test_a_block_whose_markers_are_crlf_is_normalised_once_and_then_left_alone() -> None:
    """The upgrade a CRLF machine makes exactly once, and never again.

    The block is Theurian's own text and is written with ``\\n``, so a file
    whose block arrived with ``\\r\\n`` is *not* current and the merge rewrites
    it. What must not happen is that the same rewrite repeats forever: the
    probe would report ``Missing`` on every run, and every rewrite is another
    chance to lose the lines around it.

    So the sequence is pinned, not the step: not current, merged, current, and
    a fixed point afterwards -- with the user's own CRLF lines untouched
    throughout.
    """
    keep = "export MY_OTHER_VAR=1\r\n"
    existing = keep + env_block(DATA_DIR).replace("\n", "\r\n") + "\r\n"

    assert contains_current_block(existing, DATA_DIR) is False, "a CRLF block is not this block"

    merged = merge_env_file(existing, DATA_DIR)

    assert merged == keep + env_block(DATA_DIR) + "\r\n"
    assert contains_current_block(merged, DATA_DIR) is True
    assert merge_env_file(merged, DATA_DIR) == merged, "and the second run writes nothing new"


# -- The pre-marker rendering is matched as lines too -------------------------


@pytest.mark.parametrize(
    ("shape", "tail"),
    [
        ("a comment appended to the last export", "  # my note\n"),
        ("a longer variable name starting with it", "_EXTRA=1\n"),
    ],
    ids=["trailing-comment", "longer-name"],
)
def test_an_edited_pre_marker_rendering_is_left_alone_rather_than_half_replaced(
    shape: str, tail: str
) -> None:
    """A substring search found the old rendering inside a line somebody edited.

    The rendering ends in ``export THEURIAN_MCP_TOKEN``, and that is a prefix of
    ``export THEURIAN_MCP_TOKEN_EXTRA=1`` and of the same line with a comment
    after it. Replacing the match glued the rest of the person's line onto the
    end marker, producing ``# <<< theurian <<<  # my note`` -- a marker line
    that the *next* run cannot find, which turns the file into an unterminated
    block and takes the machine out of service.

    Matched as whole lines instead, so an edited rendering is simply not
    recognised and the block is appended below it. Both exports are then
    visible and the shell keeps the block, because it comes last -- the honest
    answer, and the one a person can see and tidy.
    """
    existing = legacy_env_file_contents(DATA_DIR).rstrip("\n") + tail

    merged = merge_env_file(existing, DATA_DIR)

    assert merged == existing + "\n" + env_block(DATA_DIR) + "\n", shape
    assert merged.count('THEURIAN_MCP_TOKEN="$(cat ') == 2, "both are visible, neither is cut"
    assert merged.rstrip("\n").endswith(ENV_BLOCK_END), "and the block is what the shell reads last"


def test_a_file_the_old_substring_replacement_produced_is_refused_not_rewritten() -> None:
    """The state a machine is actually left in by the version that shipped.

    ``export THEURIAN_MCP_TOKEN  # my note`` had the block spliced over its
    first half, leaving ``# <<< theurian <<<  # my note`` as the last line.
    Nothing on that machine is a marker any more, so the block is unterminated:
    refused, with the repair in the message, rather than rewritten from a start
    marker to the end of the file.
    """
    glued = env_block(DATA_DIR) + "  # my note\n"

    with pytest.raises(MalformedEnvBlockError) as raised:
        merge_env_file(glued, DATA_DIR)

    assert raised.value.fault is EnvBlockFault.UNTERMINATED


def test_an_end_marker_above_the_block_does_not_become_the_blocks_own_end() -> None:
    """The end is searched for *after* the start, and the order is the property.

    A stray end marker above a real block is a line somebody wrote -- a
    copy-paste out of a blog post, the leftovers of a block they deleted by
    hand. Take it as the block's end and the span runs backwards: the block
    stops being recognised as current, and the rewrite that follows duplicates
    everything between the two.
    """
    existing = f"# copied from a blog post\n{ENV_BLOCK_END}\n{env_block(DATA_DIR)}\n" + AFTER

    assert contains_current_block(existing, DATA_DIR) is True

    assert merge_env_file(existing, DATA_DIR) == existing


# -- What the refusal is allowed to say (SEC-6, O-3) --------------------------


def test_the_refusal_is_constructed_from_a_closed_set_and_never_from_a_string() -> None:
    """The detail is published, so its vocabulary is a type and not a habit.

    ``probe_env_reference`` puts this message in a step detail and `doctor
    --report` prints step details into an issue somebody pastes in public. Every
    other published probe failure is withheld through ``failure_detail``
    precisely because an exception carries whatever raised it; this one is
    exempt for exactly as long as it can carry nothing else.

    Pinned on the constructor's annotation, because that is what makes it
    structural: a widening to ``EnvBlockFault | str`` is the change that would
    let "line 14 says ``export AWS_SECRET…``" through, and it would otherwise
    land as a one-word diff nobody reads twice.
    """
    hints = typing.get_type_hints(MalformedEnvBlockError.__init__)

    assert hints["fault"] is EnvBlockFault


@pytest.mark.parametrize("fault", list(EnvBlockFault))
def test_every_refusal_says_which_markers_to_look_for_and_what_to_re_run(
    fault: EnvBlockFault,
) -> None:
    """The message is the entire remedy: nothing repairs this automatically.

    It reaches a person through ``probe_env_reference``'s conflict detail and
    through ``auth rotate``'s ``nextSteps``, and in both places it is all they
    get. So every member of the enum -- not only whichever one a test happens to
    trigger -- has to carry both markers, the strings they must go and look for,
    and the command to re-run once they have.
    """
    message = str(MalformedEnvBlockError(fault))

    assert fault.value in message
    assert ENV_BLOCK_START in message
    assert ENV_BLOCK_END in message
    assert "theurian setup" in message


def test_both_of_the_faults_a_real_file_can_raise_are_reachable() -> None:
    """A closed set is only closed if the parametrised sweep above is complete.

    Each fault is asserted through a file rather than by naming it, so a member
    that no input can produce -- or an input that produces a member the sweep
    never sees -- shows up here rather than as coverage nobody reads.
    """
    raised = {}
    for shape in (f"{ENV_BLOCK_START}\n", f"{ENV_BLOCK_START}\n{ENV_BLOCK_START}\n"):
        with pytest.raises(MalformedEnvBlockError) as caught:
            merge_env_file(shape, DATA_DIR)
        raised[caught.value.fault] = shape

    assert set(raised) == set(EnvBlockFault)


# -- A line below the block that assigns the same variable --------------------


@pytest.mark.parametrize(
    "line",
    [
        "export THEURIAN_MCP_TOKEN=pasted-years-ago",
        "THEURIAN_MCP_TOKEN=pasted-years-ago",
        "declare -x THEURIAN_MCP_TOKEN=pasted-years-ago",
        "readonly THEURIAN_MCP_TOKEN=pasted-years-ago",
        "  export THEURIAN_MCP_TOKEN=pasted-years-ago",
    ],
    ids=["export", "bare", "declare-x", "readonly", "indented"],
)
def test_a_later_assignment_of_the_token_is_what_the_shell_keeps(line: str) -> None:
    """A current block and a false report are not mutually exclusive.

    The probe's question -- is the block current? -- is deliberately blind to
    everything outside the markers, and a shell sourcing the file top to bottom
    keeps the *last* assignment it reads. Measured: a file holding a current
    block and, under it, a line pasted years ago exported that old literal,
    while setup reported the machine converged and the step summary said the
    file "exports THEURIAN_MCP_TOKEN by reference".
    """
    assert contains_shadowing_assignment(f"{env_block(DATA_DIR)}\n{line}\n") is True


@pytest.mark.parametrize(
    "line",
    [
        "export THEURIAN_MCP_TOKEN",
        "# THEURIAN_MCP_TOKEN=from-an-older-install",
        '#export THEURIAN_MCP_TOKEN="$(cat /somewhere)"',
        'echo "THEURIAN_MCP_TOKEN=$THEURIAN_MCP_TOKEN"',
        "export SOMETHING_ELSE=1",
        "export THEURIAN_MCP_TOKEN_EXTRA=1",
    ],
    ids=["bare-export", "comment", "commented-export", "echo", "another-var", "longer-name"],
)
def test_a_line_that_only_mentions_the_token_is_not_an_override(line: str) -> None:
    """An assignment, not any mention -- or the warning is noise and gets ignored.

    A bare ``export THEURIAN_MCP_TOKEN`` re-exports what the block just set and
    changes nothing. A commented-out line is a comment. ``echo`` is somebody
    talking about the variable rather than setting it, and only the first word
    of a line can assign on its own account. Reporting any of these would end
    every run DEGRADED on a machine that is converged.
    """
    assert contains_shadowing_assignment(f"{env_block(DATA_DIR)}\n{line}\n") is False


def test_an_assignment_above_the_block_is_not_an_override_because_the_block_wins() -> None:
    """Order is the whole mechanism, so the answer has to depend on it.

    A line above the block is overwritten by the block when the shell reaches
    it. Warning about it would send a person to delete a line that is already
    having no effect, and a check that ignored position would do exactly that.
    """
    above = f"export THEURIAN_MCP_TOKEN=pasted-years-ago\n{env_block(DATA_DIR)}\n"

    assert contains_shadowing_assignment(above) is False


def test_a_file_with_no_block_at_all_has_nothing_to_shadow() -> None:
    """Below the block needs a block. Without one, the question does not arise.

    The file is then ``Missing`` and setup is about to write the block anyway,
    so answering ``True`` here would attach a caveat to a step that has not
    happened yet.
    """
    assert contains_shadowing_assignment("export THEURIAN_MCP_TOKEN=mine\n") is False
