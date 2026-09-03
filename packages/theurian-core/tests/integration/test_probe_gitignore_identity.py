"""`gitignore` reports whether the managed block is *there*, not whether some strings are (#87).

The check the probe shipped with was ``entry not in contents`` over the whole
file, and a substring is not a rule. Two files satisfied it while Git ignored
nothing at all:

- every entry prefixed with ``!``, which is the syntax for *un*-ignoring a path;
- every entry prefixed with ``# ``, which is the syntax for not writing a rule.

Measured in #87's reproduction: against the first of those, `git check-ignore
.theurian/state/index.db` exits 1 -- the path is not ignored -- while the step
reported ``satisfied``.

What is at stake is ADR-0004/O-2: `.theurian/state/`, the SQLite artifacts, and
`.theurian/proposals-local/` -- a directory holding *authored* content kept off
Git deliberately (ADR-0028) -- become committable while `doctor` reports the
step converged. A wrong ``satisfied`` here is the one nobody re-reads.

A third file satisfied it for a different reason, and it is not a disclosure:
the managed entries written by hand, with no Theurian markers around them. Those
rules do ignore what they name. What is wrong there is the *next* `theurian
init`, which finds no block to rewrite, appends its own, and leaves the file
carrying two lists that drift apart with nothing to say so.

So the predicate is now **managed-block identity**: the file holds exactly one
well-formed marker pair, and the span between the markers is byte-for-byte the
block `theurian init` would write. That is deliberately the same predicate as
:func:`ensure_gitignore`'s own no-op condition, and
:func:`test_the_probe_is_satisfied_exactly_when_theurian_init_would_change_nothing`
holds the two together rather than trusting them to stay in step.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from fakes.setup import FakeMcpConfig, FakeService
from setup_migrations import state_hash_from_the_loader, unchecked_migrations

from theurian.application.project_service import ProjectError, ensure_gitignore
from theurian.application.setup_context import SetupContext
from theurian.application.setup_steps import probe_gitignore
from theurian.domain.project import (
    GITIGNORE_BLOCK_END,
    GITIGNORE_BLOCK_START,
    GITIGNORE_ENTRIES,
    GITIGNORE_SECTIONS,
)
from theurian.domain.setup import StepStatus
from theurian.infrastructure.claude.mcp_config import ConnectionSpec
from theurian.infrastructure.secrets.file_store import FileSecretStore

pytestmark = pytest.mark.integration

#: The remedy every arm but the malformed one offers.
_ADD_THE_BLOCK = "Add the Theurian block to .gitignore. Run `theurian init`."

#: The remedy the malformed arm offers instead. `theurian init` refuses a file
#: in that state, so telling the reader to run it is telling them to meet the
#: same refusal with no idea what to repair.
_REPAIR_THE_MARKERS = "Repair the Theurian block markers by hand, then run `theurian init`."


def _context(tmp_path: Path, project_root: Path | None) -> SetupContext:
    data_dir = tmp_path / "home" / ".theurian"
    return SetupContext(
        home=tmp_path / "home",
        data_dir=data_dir,
        port=7419,
        project_root=project_root,
        connection=ConnectionSpec(port=7419),
        mcp_config=FakeMcpConfig(),
        secrets=FileSecretStore(data_dir),
        health=lambda: None,
        service=FakeService(),
        executable="",
        # This step is the subject; the migrations step is not. A checker that
        # reads nothing keeps the fixture from failing for a reason next door.
        check_migrations=unchecked_migrations,
        current_state_hash=state_hash_from_the_loader,
    )


def _repository(tmp_path: Path, name: str = "repo") -> Path:
    root = tmp_path / name
    root.mkdir(parents=True, exist_ok=True)
    return root


def _probe(tmp_path: Path, root: Path) -> tuple[StepStatus, str, str]:
    """The three fields every assertion here reads: status, summary, action."""
    step = probe_gitignore(_context(tmp_path, root))
    return step.status, step.summary, step.action


def _rendered_block() -> str:
    """The block, composed here rather than imported from whatever renders it.

    Spelled out from :data:`GITIGNORE_SECTIONS` the way `ensure_gitignore` does
    -- a start marker, each section's comment followed by its entries, an end
    marker -- so that a renderer which stops writing the section labels, or
    reorders the sections, fails here instead of agreeing with itself. A test
    that reads its expectation out of the thing under test is green for whatever
    that thing says.
    """
    return "\n".join(
        [
            GITIGNORE_BLOCK_START,
            *(
                line
                for section in GITIGNORE_SECTIONS
                for line in (section.comment, *section.entries)
            ),
            GITIGNORE_BLOCK_END,
        ]
    )


# -- Rules that are not rules ------------------------------------------------


def test_a_gitignore_that_negates_every_theurian_entry_is_not_satisfied(tmp_path: Path) -> None:
    """`!.theurian/state/` is the syntax for *not* ignoring it (#87, ADR-0004).

    The measured face. Every managed entry appears in the file, so the old
    substring check reported ``satisfied`` -- while `git check-ignore` on this
    same file exits 1 for `.theurian/state/index.db`, which is the question the
    step exists to ask. Git is not consulted here: what this asserts is the
    probe's verdict, and the sentence it now offers the reader.
    """
    root = _repository(tmp_path)
    (root / ".gitignore").write_text(
        "\n".join(f"!{entry}" for entry in GITIGNORE_ENTRIES) + "\n", encoding="utf-8"
    )

    status, summary, action = _probe(tmp_path, root)

    assert status is StepStatus.MISSING
    assert summary == (
        f"{root / '.gitignore'} has no Theurian block. "
        f"Rules written by hand are not evaluated here."
    )
    assert action == _ADD_THE_BLOCK


def test_a_gitignore_that_comments_out_every_theurian_entry_is_not_satisfied(
    tmp_path: Path,
) -> None:
    """The same defect through the other syntax that makes a line inert.

    Written as its own case rather than parametrised with the negation above,
    because they are two different ways for a file to *contain* the entries and
    ignore nothing, and a fix that special-cased ``!`` would leave this one.
    """
    root = _repository(tmp_path)
    (root / ".gitignore").write_text(
        "\n".join(f"# {entry}" for entry in GITIGNORE_ENTRIES) + "\n", encoding="utf-8"
    )

    status, summary, _ = _probe(tmp_path, root)

    assert status is StepStatus.MISSING
    assert "has no Theurian block" in summary


def test_hand_written_entries_without_the_markers_are_not_the_managed_block(
    tmp_path: Path,
) -> None:
    """Real rules, and still not what this step checks.

    These entries *do* ignore what they name, so this is the arm where the new
    predicate is deliberately stricter than "is it ignored". The reason is the
    next `theurian init`: with no markers to rewrite it appends its own block,
    and the file then carries two lists that drift apart independently. The
    summary says so rather than reporting a bare absence, because the reader is
    looking at a file that plainly mentions Theurian.
    """
    root = _repository(tmp_path)
    (root / ".gitignore").write_text("\n".join(GITIGNORE_ENTRIES) + "\n", encoding="utf-8")

    status, summary, _ = _probe(tmp_path, root)

    assert status is StepStatus.MISSING
    assert "Rules written by hand are not evaluated here." in summary


# -- What the block being current means --------------------------------------


def test_the_block_theurian_init_writes_is_what_satisfies_the_step(tmp_path: Path) -> None:
    """The positive case, produced by the code that owns the block.

    Built with :func:`ensure_gitignore` rather than by writing the expected text
    here: this is the state a project is in after `theurian init`, and if that
    state does not satisfy the step then the step is unconvergeable by any
    command Theurian ships.
    """
    root = _repository(tmp_path)
    ensure_gitignore(root)

    status, summary, action = _probe(tmp_path, root)

    assert status is StepStatus.SATISFIED
    assert summary == f"{root / '.gitignore'}'s Theurian block is present and current."
    assert action == "", "a satisfied step offers no remedy"


def test_one_edited_character_inside_the_block_is_no_longer_the_block(tmp_path: Path) -> None:
    """Identity, not entry presence: the edit here removes no entry at all.

    A character taken out of a section *label* leaves every ignore rule intact
    and every entry findable by substring, so this is the case the old check
    could not see by construction. The next `theurian init` rewrites the span,
    which is the honest report: what is on disk is not what Theurian writes.
    """
    root = _repository(tmp_path)
    ensure_gitignore(root)
    gitignore = root / ".gitignore"
    label = GITIGNORE_SECTIONS[0].comment
    gitignore.write_text(
        gitignore.read_text(encoding="utf-8", newline="").replace(label, label.replace("#", "##")),
        encoding="utf-8",
        newline="",
    )

    status, summary, action = _probe(tmp_path, root)

    assert status is StepStatus.MISSING
    assert summary == (f"{gitignore}'s Theurian block differs from the one `theurian init` writes.")
    assert action == _ADD_THE_BLOCK


def test_a_block_whose_entries_were_reordered_is_no_longer_the_block(tmp_path: Path) -> None:
    """The other edit that keeps every entry: the same lines, in another order.

    `ensure_gitignore` rewrites the whole span, so the order on disk is
    Theurian's to state. A probe comparing *sets* of entries would call this
    current and then be surprised by a `theurian init` that reports a change.
    """
    root = _repository(tmp_path)
    lines = _rendered_block().split("\n")
    reordered = [lines[0], *sorted(lines[1:-1], reverse=True), lines[-1]]
    (root / ".gitignore").write_text("\n".join(reordered) + "\n", encoding="utf-8")

    status, summary, _ = _probe(tmp_path, root)

    assert status is StepStatus.MISSING
    assert "differs from the one `theurian init` writes." in summary


def test_a_block_written_with_crlf_endings_is_not_the_block_theurian_init_writes(
    tmp_path: Path,
) -> None:
    """The read has to see the bytes, or the two predicates part company here.

    `ensure_gitignore` reads and writes with ``newline=""`` so that a CRLF file
    is not silently rewritten end to end, and it rewrites this span -- measured:
    ``changed=True`` and the bytes on disk move. A probe that read the same file
    with Python's universal-newline translation would find the rendered block in
    what it read and report ``satisfied``, so `doctor` would call current a file
    that every `theurian init` changes.
    """
    root = _repository(tmp_path)
    (root / ".gitignore").write_text(
        _rendered_block().replace("\n", "\r\n") + "\r\n", encoding="utf-8", newline=""
    )

    status, _, action = _probe(tmp_path, root)

    assert status is StepStatus.MISSING
    assert action == _ADD_THE_BLOCK, "the markers are well formed; only the block differs"


def test_a_block_missing_an_entry_names_the_entry_a_rerun_brings_in(tmp_path: Path) -> None:
    """The stale block, and the sentence a reader acts on (#49's HIGH-2).

    Every project initialised before ADR-0028 has the block without
    `.theurian/proposals-local/`. Naming the absent entry is what tells the
    reader that a re-run is a small, known change rather than an unknown one.
    """
    root = _repository(tmp_path)
    absent = ".theurian/proposals-local/"
    kept = [line for line in _rendered_block().split("\n") if line != absent]
    (root / ".gitignore").write_text("\n".join(kept) + "\n", encoding="utf-8")

    status, summary, _ = _probe(tmp_path, root)

    assert status is StepStatus.MISSING
    assert summary == (
        f"{root / '.gitignore'}'s Theurian block is out of date: it does not ignore {absent}."
    )


def test_an_entry_outside_the_block_does_not_complete_the_block(tmp_path: Path) -> None:
    """What counts is what the *span* holds, because the span is what gets rewritten.

    A hand-written `.theurian/proposals-local/` below the end marker does ignore
    that directory today, and the next `theurian init` rewrites the block
    without it -- leaving the user's own line as the only thing ignoring an
    ADR-0028 directory, in a file they may reasonably tidy. The step reports the
    block it manages, so an entry outside it neither completes nor excuses one.
    """
    root = _repository(tmp_path)
    absent = ".theurian/proposals-local/"
    kept = [line for line in _rendered_block().split("\n") if line != absent]
    (root / ".gitignore").write_text("\n".join([*kept, absent]) + "\n", encoding="utf-8")

    status, summary, _ = _probe(tmp_path, root)

    assert status is StepStatus.MISSING
    assert f"does not ignore {absent}." in summary


# -- Markers Theurian cannot act on ------------------------------------------


def test_two_start_markers_are_reported_as_malformed_not_as_a_stale_block(
    tmp_path: Path,
) -> None:
    """The state `ensure_gitignore` refuses, reported as the refusal it will meet.

    Two start markers is what resolving a merge conflict by keeping both sides
    leaves behind, and `theurian init` will not touch such a file -- it cannot
    tell which of the rules between them are its own (#128). A step that
    answered ``missing`` here beside "Run `theurian init`" would send the reader
    at a command that refuses, with nothing said about what to repair.
    """
    root = _repository(tmp_path)
    (root / ".gitignore").write_text(
        f"{_rendered_block()}\n{_rendered_block()}\n", encoding="utf-8"
    )

    status, summary, action = _probe(tmp_path, root)

    assert status is StepStatus.MISSING
    assert summary == (
        f"{root / '.gitignore'}'s Theurian block markers are malformed; "
        f"`theurian init` refuses the file in this state."
    )
    assert action == _REPAIR_THE_MARKERS


def test_a_start_marker_with_no_end_marker_is_reported_as_malformed(tmp_path: Path) -> None:
    """The other refusal: a block whose end is gone, so its extent is unknown.

    Reached by a hand edit or a truncated merge. Nothing below the start marker
    can be assumed to be Theurian's, which is exactly why `ensure_gitignore`
    raises instead of rewriting to the end of the file.
    """
    root = _repository(tmp_path)
    (root / ".gitignore").write_text(
        _rendered_block().replace(f"\n{GITIGNORE_BLOCK_END}", "") + "\n", encoding="utf-8"
    )

    status, summary, action = _probe(tmp_path, root)

    assert status is StepStatus.MISSING
    assert "markers are malformed" in summary
    assert action == _REPAIR_THE_MARKERS


def test_a_gitignore_that_does_not_exist_says_nothing_ignores_the_artifacts(
    tmp_path: Path,
) -> None:
    """Unchanged, and pinned so the rewrite above cannot fold it into another arm.

    A file that is absent, one that is silent, and one whose block is out of
    date are different things to be told, and the reader acts on them
    differently.
    """
    root = _repository(tmp_path)

    status, summary, action = _probe(tmp_path, root)

    assert status is StepStatus.MISSING
    assert summary == (
        f"{root / '.gitignore'} does not exist, so nothing ignores the derived artifacts."
    )
    assert action == _ADD_THE_BLOCK


# -- The two predicates cannot drift apart -----------------------------------


def _would_change(source: Path, scratch: Path) -> bool:
    """Whether `theurian init` would rewrite this `.gitignore`, on a copy.

    On a copy because :func:`ensure_gitignore` *writes*: asking the question in
    place would repair the file the probe is about to be shown. A refusal counts
    as "would change" -- the file is not one `theurian init` leaves alone.
    """
    scratch.mkdir(parents=True, exist_ok=True)
    if (source / ".gitignore").exists():
        shutil.copyfile(source / ".gitignore", scratch / ".gitignore")
    try:
        changed, _ = ensure_gitignore(scratch)
    except ProjectError:
        return True
    return changed


def _states(tmp_path: Path) -> dict[str, Path]:
    """One repository per shape of `.gitignore` this step can be shown."""
    rendered = _rendered_block()
    entries = list(rendered.split("\n"))
    without_an_entry = [line for line in entries if line != ".theurian/proposals-local/"]

    written: dict[str, str | None] = {
        "no file at all": None,
        "an empty file": "",
        "rules of somebody else's": "*.log\nnode_modules/\n",
        "the current block": f"{rendered}\n",
        "the current block after other rules": f"*.log\n\n{rendered}\n",
        "a block with an entry taken out": "\n".join(without_an_entry) + "\n",
        "a block with its entries reordered": (
            "\n".join([entries[0], *sorted(entries[1:-1], reverse=True), entries[-1]]) + "\n"
        ),
        "the block with CRLF endings": rendered.replace("\n", "\r\n") + "\r\n",
        "the entries with no markers": "\n".join(GITIGNORE_ENTRIES) + "\n",
        "the entries, negated": "\n".join(f"!{entry}" for entry in GITIGNORE_ENTRIES) + "\n",
        "two blocks": f"{rendered}\n{rendered}\n",
        "a block with no end marker": rendered.replace(f"\n{GITIGNORE_BLOCK_END}", "") + "\n",
    }

    roots: dict[str, Path] = {}
    for label, content in written.items():
        root = _repository(tmp_path, label.replace(" ", "-").replace("'", ""))
        if content is not None:
            (root / ".gitignore").write_text(content, encoding="utf-8", newline="")
        roots[label] = root
    return roots


def test_the_probe_is_satisfied_exactly_when_theurian_init_would_change_nothing(
    tmp_path: Path,
) -> None:
    """The identity that keeps `doctor` and `theurian init` from disagreeing.

    ``satisfied`` means "a re-run would change nothing", and the only authority
    on that is the function that does the re-writing. Asserting the
    *equivalence* over a table of file states is what makes this a check on the
    predicate rather than on one arm: it fails if the probe accepts a file
    `ensure_gitignore` rewrites, and equally if it rejects one it leaves alone.

    Both sides of the equivalence are exercised -- the assertion below refuses a
    table where every state falls on one side, because such a table would be
    passed by a probe that answered the same thing every time.
    """
    states = _states(tmp_path)
    verdicts = {
        label: (
            probe_gitignore(_context(tmp_path, root)).status is StepStatus.SATISFIED,
            _would_change(root, tmp_path / "scratch" / label.replace(" ", "-")),
        )
        for label, root in states.items()
    }

    disagreed = {label: verdict for label, verdict in verdicts.items() if verdict[0] == verdict[1]}
    assert not disagreed, (
        f"the probe and `theurian init` disagree about these files "
        f"(satisfied, would_change): {disagreed}"
    )
    satisfied = {label for label, (ok, _) in verdicts.items() if ok}
    assert satisfied == {"the current block", "the current block after other rules"}, (
        "the table has to hold states on both sides of the equivalence, or a probe "
        "that answers the same thing every time passes it"
    )


def test_the_block_the_probe_accepts_is_the_one_the_sections_describe(tmp_path: Path) -> None:
    """The derivation, recomputed from the live constants rather than quoted.

    :data:`GITIGNORE_SECTIONS` is the source of both the entries and the labels
    above them, and the block is start marker, then each section's comment and
    its entries in order, then the end marker. Recomputing it here means a
    change to the sections -- a new entry, a renamed label, a reordered section
    -- is carried into this expectation automatically, while a *renderer* that
    stops agreeing with the sections fails.
    """
    root = _repository(tmp_path)
    (root / ".gitignore").write_text(f"{_rendered_block()}\n", encoding="utf-8", newline="")

    assert probe_gitignore(_context(tmp_path, root)).status is StepStatus.SATISFIED
    # The renderer's own output, so a disagreement is reported as the two
    # strings rather than as a probe verdict three layers away.
    assert ensure_gitignore(_repository(tmp_path, "rendered"))[1] == _rendered_block()
