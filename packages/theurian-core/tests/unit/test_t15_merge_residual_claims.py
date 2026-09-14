"""T-15's narrowed residual, held from both ends: the records and the build (ADR-0034, T-15).

Slice B3 shipped the committed-check -- ``migrate apply`` refuses, by default, a
migration that is not tracked and byte-identical to ``HEAD`` -- and cluster 4's
docs commit (``8f308564``) rewrote the four durable records that had said
``migrate apply`` "applies whatever is in ``.theurian/migrations/``, committed or
not". A correction of that shape owes a pin in **both** directions, and this
module is it (the docs specialist requested it, and ADR-0034 decision 5 records
that no test yet pins the narrowed residual against the check). The prose arms
hold that the four records no longer carry the pre-B3 claim *and* still state the
narrowing; the fact arms hold that the mechanism the narrowing names is still in
the build. They fail differently, which is the point: a prose arm RED while the
fact arms are green means a record drifted back to the pre-B3 world, and the
reverse means the product moved and the records now describe a check this build
no longer carries.

**The four records, and what each is held to.** Each carried the falsified
present-tense claim and was narrowed in ``8f308564``:

* ``README.md``'s approval-boundary paragraph.
* ``docs/index.md``'s *What is enforced, and what is convention* paragraph.
* ``docs/roadmap.md``'s *"AI proposes" enforced structurally* bullet.
* ``docs/security/threat-model.md``'s **T-15** entry, sliced by
  :func:`~threat_model_claims.entry_in`.

Each is held to three things: it does **not** pair a ``migrate apply`` subject
with the un-narrowed phrasing (``committed or not`` / ``nothing enforces the
merge``); its focused passage **does** state the narrowing (``migrate apply``
refuses an uncommitted / not-committed migration, or enforces the commit); and it
names the escape-hatch flag, read off the live CLI so the record and the command
move together.

**Why both ends and not one.** A record frozen against a product free to move is
the same defect one file over -- the reasoning ``test_sec12_shipped_claims.py``
and ``write_lock_claims`` apply to a control claim. So every symbol the fact side
rests on is read off a live object here, never transcribed: the check's source
path is derived from ``CommittedMigrationCheck.__module__``, and the flag string
is derived from the built ``migrate apply`` Click command. Rename the class or
move its module, and both the T-15 residual (which names
``infrastructure/git/committed_check.py``) and the T-7 spawn-site record go RED;
rename the flag, and the four records quoting ``--allow-uncommitted`` go RED with
the fact arm green.

**The exclusions, stated because a wider grep would hit them.** ADR-0034 decision
5's population key (``nothing enforces the merge`` / ``does not verify that a
migration was merged`` / ``committed or not``) matched seven files; this module
ranges over only the four that carried the *false* present-tense claim. The three
it leaves out are legitimately frozen or design records, and asserting the phrase
absent from them would be wrong:

* ``packages/theurian-core/CHANGELOG.md`` -- the ``[0.1.0.dev5]`` hit is a dated
  release section describing a shipped tree (ADR-0034 decision 5: "the dated
  CHANGELOG section is **not** a mover"), and the ``[Unreleased]`` hit is the
  BREAKING entry's *Old shape:* quote, correctly past-tense.
* ``docs/adr/0034-migrate-apply-enforces-the-merge.md`` -- its Context blockquote
  quotes the pre-B3 residual verbatim as the problem it solves, and decision 5
  reproduces the population's own ``git grep`` command.
* ``docs/protocol/mcp-tools.md`` -- "Core does not verify that a migration was
  merged" is **true** (the merge genuinely is not verified) and must not be
  asserted absent.
* ``packages/theurian-core/src/theurian/cli/commands.py`` -- the pre-apply band's
  comment describes, past-tense, what ``apply`` used to do.

**Reach statement.** This pin holds that the four records name the narrowed
control and that the control exists in the build. It does **not** hold that the
rest of cluster 4's rewrite is faithful -- whether each narrowed paragraph
describes what the adapter actually does, whether the residual's remaining owed
half is stated correctly. Faithfulness is a reading and no mechanical check
reaches it, which is exactly what ADR-0034 decision 5 and its Compliance section
say of the same rewrite ("Whether that rewrite is faithful is a reading and no
mechanical check reaches it"). A pin whose docstring claimed more would be its
own subject matter one level up.

**Not a unit of I/O.** The prose arms read four repository documents as text --
the shape the sibling ``test_threat_model_*_claims.py`` pins already run under
``pytest.mark.unit`` -- the fact arms build the ``migrate`` Click command in
process and read one test module's ``PROCESS_SPAWN_SITES`` off disk. No database,
no socket, no subprocess spawned, nothing written anywhere.
"""

from __future__ import annotations

import importlib.util
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Final

import pytest
import typer
from threat_model_claims import THREAT_MODEL, entry_in, prose
from typer.core import TyperGroup
from write_lock_claims import REPO_ROOT

from theurian.cli.commands import migrate_app
from theurian.infrastructure.git.committed_check import CommittedMigrationCheck

pytestmark = pytest.mark.unit

# -- the fact side: live symbols the records rest on ---------------------------

#: The import-path prefix stripped before a module is written the way a record
#: writes a source file. ``theurian.infrastructure.git.committed_check`` ->
#: ``infrastructure/git/committed_check.py``.
_PACKAGE_PREFIX: Final = "theurian."


def _module_source_path(module: str) -> str:
    """``CommittedMigrationCheck.__module__`` as the records spell a source file.

    Derived from the live ``__module__`` rather than typed here, because a
    transcribed path is correct on the day it is written and silent afterwards --
    which is the whole failure this module exists to close, so repeating it inside
    the pin would be the closure arguing against itself.
    """
    assert module.startswith(_PACKAGE_PREFIX), (
        f"`{module}` is not under `{_PACKAGE_PREFIX}`, so the source path the records "
        f"name cannot be derived from it; the package moved, and the records and this "
        f"derivation move together"
    )
    return module.removeprefix(_PACKAGE_PREFIX).replace(".", "/") + ".py"


#: The committed-check's source path, read off the live class. The T-15 residual
#: and the T-7 spawn-site set both name this string.
_CHECK_MODULE_PATH: Final = _module_source_path(CommittedMigrationCheck.__module__)

#: The unit module that owns the process-spawn allowlist. Read at run time rather
#: than imported: ``from test_network_call_sites import PROCESS_SPAWN_SITES`` fails
#: at run time (``tests/unit`` is not on ``sys.path``) and the namespace spelling
#: takes ``mypy`` down for the whole tree -- the reasoning
#: ``test_threat_model_t7_claims.py`` records. A ``spec_from_file_location`` load
#: has neither problem: no static import for ``mypy`` to double-resolve, and the
#: file is found by absolute path.
_SPAWN_SITES_FILE: Final = (
    REPO_ROOT / "packages/theurian-core/tests/unit/test_network_call_sites.py"
)


def _load_spawn_sites() -> frozenset[tuple[str, str]]:
    """``test_network_call_sites.PROCESS_SPAWN_SITES``, loaded from its file."""
    spec = importlib.util.spec_from_file_location(
        "_t15_committed_check_spawn_sites", _SPAWN_SITES_FILE
    )
    assert spec is not None and spec.loader is not None, (
        f"cannot build an import spec for {_SPAWN_SITES_FILE}, so the process-spawn "
        f"record the committed-check joins cannot be read"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return frozenset((str(path), str(name)) for path, name in module.PROCESS_SPAWN_SITES)


def _apply_option_specs() -> tuple[tuple[str | None, tuple[str, ...]], ...]:
    """Every option of the built ``migrate apply`` command, as ``(name, opts)``.

    Built through ``typer.main.get_command`` -- the real CLI surface a user sees,
    not the function's source -- so a flag the source defines but the command never
    wires would not show up here. Reduced to ``(name, opts)`` tuples so the callers
    depend on the CLI shape, not on typer's vendored command classes.
    """
    group = typer.main.get_command(migrate_app)
    assert isinstance(group, TyperGroup), (
        f"`migrate` no longer builds a TyperGroup ({type(group).__name__}), so the "
        f"escape-hatch flag the records quote cannot be read off the command"
    )
    apply = group.commands.get("apply")
    assert apply is not None, (
        "the `migrate` group no longer registers an `apply` command, so `migrate "
        "apply` -- the subject of every record this module pins -- is gone"
    )
    return tuple((param.name, tuple(param.opts)) for param in apply.params)


def _derive_escape_hatch_flag() -> str:
    """``--allow-uncommitted`` as the live command spells it, not as typed here.

    Read off the ``allow_uncommitted`` parameter of the built command, so a
    renamed flag reddens the four records (which quote the old name) rather than
    passing on a literal that drifted out from under them.
    """
    matches = sorted(
        {
            opt
            for name, opts in _apply_option_specs()
            if name == "allow_uncommitted"
            for opt in opts
            if opt.startswith("--")
        }
    )
    assert len(matches) == 1, (
        f"`migrate apply` exposes {matches} long options on its `allow_uncommitted` "
        f"parameter, expected exactly one. That flag is the escape hatch all four "
        f"records name; removed, the records are false, and with two the records "
        f"cannot say which one they mean"
    )
    return matches[0]


#: The escape-hatch flag, read off the built command. Computed at import so a flag
#: that was removed reddens the whole module (a legitimate RED: the records that
#: quote it are then false) and a flag that was renamed reddens the prose flag arm
#: while the fact arm below stays green.
_FLAG: Final = _derive_escape_hatch_flag()


# -- the prose side: the four records and how each is located ------------------

_BULLET_START: Final = re.compile(r"(?m)^- ")


def _paragraph_containing(text: str, anchor: str) -> str:
    """The one blank-line-delimited block of *text* carrying *anchor*.

    The anchor is a stable topic phrase, never the narrowing this module asserts:
    a locator keyed on the text a pin watches stops matching exactly when that
    text drifts, and the pin then reads nothing and reports it as safety.
    """
    blocks = [block for block in text.split("\n\n") if anchor in block]

    assert len(blocks) == 1, (
        f"`{anchor}` identifies {len(blocks)} paragraphs, expected 1. Zero means the "
        f"anchor was reworded and every arm over this passage would read an empty "
        f"string; more than one means the passage read below is not the one chosen"
    )
    return blocks[0]


def _bullet_containing(text: str, anchor: str) -> str:
    """The one column-anchored top-level bullet of *text* carrying *anchor*.

    Split on a line-start ``- `` so a bullet's two-space-indented continuation
    lines stay with the bullet they belong to rather than opening a new one.
    """
    bullets = [bullet for bullet in _BULLET_START.split(text) if anchor in bullet]

    assert len(bullets) == 1, (
        f"`{anchor}` identifies {len(bullets)} top-level bullets, expected 1. Zero "
        f"means the bullet was reworded past its own anchor and the arms over it read "
        f"nothing; more than one means what is read below is text this module never "
        f"chose"
    )
    return bullets[0]


@dataclass(frozen=True)
class _Record:
    """A durable record that carried the pre-B3 claim, and how to focus on it.

    ``path`` is scanned whole for the reversion and the flag; ``focus`` returns the
    passage the narrowing is asserted *in*, so the narrowing key runs against the
    one paragraph that must carry it rather than against the whole file, where
    ``refuses`` and ``migrate apply`` appear in a dozen unrelated sentences.
    """

    label: str
    path: Path
    focus: Callable[[str], str]

    def text(self) -> str:
        return self.path.read_text(encoding="utf-8")


RECORDS: Final[tuple[_Record, ...]] = (
    _Record(
        "README approval boundary",
        REPO_ROOT / "README.md",
        lambda text: _paragraph_containing(text, "Approval is the act of merging a pull request"),
    ),
    _Record(
        "docs/index enforced-vs-convention",
        REPO_ROOT / "docs/index.md",
        lambda text: _paragraph_containing(text, "What is enforced, and what is convention"),
    ),
    _Record(
        "roadmap AI-proposes bullet",
        REPO_ROOT / "docs/roadmap.md",
        lambda text: _bullet_containing(text, '"AI proposes" enforced structurally'),
    ),
    _Record(
        "threat-model T-15",
        THREAT_MODEL,
        lambda text: entry_in(text, "T-15"),
    ),
)

_RECORDS_BY_LABEL: Final = {record.label: record for record in RECORDS}


# -- the two scans, and the reason each is shaped the way it is ----------------

#: The subject every T-15 sentence is written about. Matched over normalised prose,
#: so the record's backticks are already gone.
_SUBJECT: Final = re.compile(r"migrate apply")

#: The pre-B3 phrasing, in its two spellings -- the present-tense behaviour claim
#: (``committed or not``) and the un-narrowed residual heading (``nothing enforces
#: the merge``). Paired with a :data:`_SUBJECT` within :data:`_REACH_CHARS`, the
#: shape ``test_sec12_shipped_claims.py`` uses: distinctive enough to key on, but
#: paired so an unrelated future ``committed or not`` costs a judgement rather than
#: a false RED. The narrowed line 2404 -- "the merge is not enforced" -- is a
#: different string and is deliberately not matched.
_UN_NARROWED: Final = re.compile(r"committed or not|nothing enforces the merge")

#: The narrowing, in the spellings the four passages actually use: ``refuses`` an
#: ``uncommitted`` / ``not committed`` migration, or a statement that the commit is
#: what the code now checks. The retired passages match none of it -- which is what
#: makes this a positive control for the reversion scan and not a second copy of
#: it.
_NARROWED: Final = re.compile(
    r"refuses\b.{0,80}?(?:uncommitted|not committed)"
    r"|enforces the commit|the commit is enforced|commit is (?:a |now a )?check"
)

#: How far a subject and the un-narrowed phrasing may sit before they stop being
#: one claim. Measured from the ends of the two matches in either order, because
#: the residual heading puts the phrasing *before* its subject and the behaviour
#: sentence puts it after.
_REACH_CHARS: Final = 240


def _un_narrowed_reversions(text: str) -> list[str]:
    """Every place *text* pairs a ``migrate apply`` subject with pre-B3 phrasing.

    Normalised first, which is not optional: the retired passages soft-wrap
    mid-claim -- the roadmap's wraps ``committed or\\n  not`` through the middle of
    the phrase -- and one writes its residual as ``*nothing enforces the merge*``,
    so a scan over raw bytes would pass over the sentences it exists to watch.
    :func:`~threat_model_claims.prose` is the shared normalisation.

    Returns the window around each pairing rather than a count, so a failure shows
    the sentence to judge.
    """
    normalised = prose(text)
    subjects = [match.span() for match in _SUBJECT.finditer(normalised)]

    found: list[str] = []
    for retraction in _UN_NARROWED.finditer(normalised):
        for start, end in subjects:
            if max(start - retraction.end(), retraction.start() - end, 0) <= _REACH_CHARS:
                opening = max(0, min(start, retraction.start()) - 40)
                found.append(normalised[opening : max(end, retraction.end()) + 40])
                break
    return found


# -- prose arms ----------------------------------------------------------------


@pytest.mark.parametrize("label", sorted(_RECORDS_BY_LABEL))
def test_no_record_reverts_to_the_committed_or_not_claim(label: str) -> None:
    """RED means a record went back to saying ``migrate apply`` applies whatever, committed or not.

    Slice B3 made that claim false: ``migrate apply`` refuses, by default, a
    migration that is not tracked and byte-identical to ``HEAD``. The four records
    were corrected in the commit that made them false (``8f308564``), and the
    failure this arm exists for is the correction being undone -- by a revert, by a
    merge that resolved the wrong way, or by somebody re-deriving the old sentence
    from an older record. An on-main claim that a shipped control does not run is
    read as an admission by anyone doing a security review and as an open work item
    by anyone planning one.

    Scanned whole-file, so a reversion in any surface -- a paragraph, a bullet, or a
    table cell -- is caught, not only the one the narrowing arm focuses on. The
    positive control comes first: the file must still carry the ``migrate apply``
    subject, or the read returned something empty and the absence below would be
    worthless whatever the file said.
    """
    record = _RECORDS_BY_LABEL[label]
    text = record.text()

    assert _SUBJECT.search(prose(text)), (
        f"{record.path.name} carries no `migrate apply` subject at all, so the "
        f"reversion scan has nothing to pair the pre-B3 phrasing with and would "
        f"report a clean record whatever it said. The file moved, or stopped naming "
        f"the command it is about"
    )

    reversions = _un_narrowed_reversions(text)

    assert not reversions, (
        f"the {label} record ({record.path.name}) says `migrate apply` applies "
        f"whatever is committed or not, within {_REACH_CHARS} characters of naming "
        f"it:\n"
        + "".join(f"\n  ...{window}..." for window in reversions)
        + f"\n\nSince ADR-0034's T-15 check (slice B3) that is false: `migrate apply` "
        f"refuses an uncommitted migration by default, and the fact arms in this "
        f"module hold that the check is in the build. If the check really was "
        f"removed, those arms are RED too and the record is right; if it was not, the "
        f"record is asserting a gap that does not exist. `--allow-uncommitted` "
        f"(`{_FLAG}`) is the escape hatch, not a reason to restore the old prose."
    )


@pytest.mark.parametrize("label", sorted(_RECORDS_BY_LABEL))
def test_each_record_states_the_narrowed_committed_check(label: str) -> None:
    """RED means a record dropped the narrowing that replaced the pre-B3 claim.

    The other half of :func:`test_no_record_reverts_to_the_committed_or_not_claim`,
    failing from the other side: that arm refuses the old sentence, this one refuses
    a passage that quietly stopped carrying the new one. A paragraph deleted in a
    tidy-up leaves the reversion scan nothing to fire on, so a record can go silent
    about the control without ever tripping the absence arm -- and a reader then
    finds no statement, in the project's own words, that the merge boundary gained
    a check at all.

    Held on the focused passage, where the narrowing must live, because ``refuses``
    and ``migrate apply`` appear across these files in unrelated sentences. The
    subject is asserted first as the locator's positive control:
    :func:`test_the_narrowing_scan_does_not_fire_on_the_retired_claim` proves the
    key discriminates, so a reverted passage fails this arm rather than passing it.

    It does not hold that the narrowing is *faithful* -- whether the paragraph
    describes what the adapter does is a reading, per the module docstring's reach
    statement.
    """
    record = _RECORDS_BY_LABEL[label]
    passage = prose(record.focus(record.text()))

    assert _SUBJECT.search(passage), (
        f"the focused passage for the {label} record names no `migrate apply` "
        f"subject, so its locator anchored on the wrong text and the narrowing check "
        f"below reads nothing:\n\n{passage[:400]}"
    )

    assert _NARROWED.search(passage), (
        f"the {label} record's passage no longer states the T-15 narrowing -- that "
        f"`migrate apply` refuses an uncommitted (or not-committed) migration, or "
        f"enforces the commit:\n\n{passage[:400]}\n\n"
        f"Either the narrowed claim was deleted, leaving the record silent about a "
        f"shipped control, or it was reworded past every spelling this key knows "
        f"(`{_NARROWED.pattern}`). If the wording moved on purpose, widen the key in "
        f"the same change; if the claim is gone, the record owes it back."
    )


@pytest.mark.parametrize("label", sorted(_RECORDS_BY_LABEL))
def test_each_record_names_the_escape_hatch_flag(label: str) -> None:
    """RED means a record dropped or misquotes ``--allow-uncommitted``.

    All four records name the flag that restores the old behaviour, and the flag is
    read off the live command rather than written here, so this arm fires in both
    directions -- a record that drops the flag, and a flag renamed in the CLI while
    a record keeps quoting the old spelling. A reader who takes a record's word for
    the escape hatch must be given the name the command actually accepts.

    It does not pin the grammar around the flag, only that the record carries the
    live spelling somewhere; what a record calls it is wording.
    """
    record = _RECORDS_BY_LABEL[label]

    assert prose(_FLAG) in prose(record.text()), (
        f"the {label} record ({record.path.name}) does not name `{_FLAG}`, the flag "
        f"`migrate apply` exposes to restore the pre-B3 behaviour. Either the record "
        f"dropped the escape hatch it must name to be actionable, or the flag was "
        f"renamed in the CLI and the record now quotes a spelling the command no "
        f"longer accepts -- the flag here is read off the built command, so the "
        f"second case is what this arm is for: move the record in the commit that "
        f"moves the flag."
    )


# -- fact arms -----------------------------------------------------------------


def test_the_t15_residual_names_the_module_the_committed_check_lives_in() -> None:
    """RED means T-15 quotes a source path this build no longer has.

    The T-15 residual reads its narrowing "through
    ``infrastructure/git/committed_check.py``". That path is derived here from the
    live ``CommittedMigrationCheck.__module__``, so the entry and the code move
    together: relocate or rename the adapter, and the entry is quoting a module the
    build does not carry, in the same commit rather than at whatever later moment a
    reader tries to open it.

    It is the fact leg that says the narrowing the prose arms hold is not three
    files agreeing with each other: the class the entry names exists, where it says.
    """
    entry_text = prose(entry_in(THREAT_MODEL.read_text(encoding="utf-8"), "T-15"))

    assert prose(_CHECK_MODULE_PATH) in entry_text, (
        f"T-15 no longer names `{_CHECK_MODULE_PATH}`, the source of the committed "
        f"check its narrowing rests on. Either the entry stopped naming the module -- "
        f"in which case its 'the commit is enforced' statement points at nothing a "
        f"reader can open -- or `CommittedMigrationCheck` was moved or renamed and the "
        f"entry quotes a path this build no longer has. The path is derived from the "
        f"live class, so move the record in the commit that moves the code."
    )


def test_the_committed_check_is_a_recorded_process_spawn_site() -> None:
    """RED means the committed check's ``git`` spawn left the pinned spawn-site set.

    ``migrate apply``'s check runs ``git rev-parse`` and ``git hash-object`` to
    decide whether a migration is committed, and those spawns are accounted for on
    ``test_network_call_sites.PROCESS_SPAWN_SITES`` (which T-7's spawn bullet is held
    against). If the adapter is deleted the set loses its member and the narrowing
    the four records state has nothing behind it; if it is relocated, the derived
    path stops matching the recorded one.

    The membership is derived from the live class's module path against the set read
    off the owning test module, so this reddens when the mechanism moves rather than
    when a literal is edited.
    """
    spawn_sites = _load_spawn_sites()

    assert (_CHECK_MODULE_PATH, "subprocess") in spawn_sites, (
        f"`({_CHECK_MODULE_PATH!r}, 'subprocess')` is not in PROCESS_SPAWN_SITES:\n  "
        + "\n  ".join(f"{path} :: {name}" for path, name in sorted(spawn_sites))
        + "\n\nThe committed check spawns `git` (rev-parse and hash-object) to "
        "answer whether a migration is committed at HEAD, so it must be a recorded "
        "spawn site. If it "
        "is MISSING here, the check was removed or its module moved -- in which case "
        "the T-15 narrowing the four records now state describes a control this build "
        "no longer carries, and the prose arms must revert with it."
    )


def test_migrate_apply_exposes_the_escape_hatch_flag() -> None:
    """RED means ``migrate apply`` stopped offering ``--allow-uncommitted``.

    The escape hatch is the half of the design that keeps the default flip usable
    for development and recovery (ADR-0034 decision 2), and all four records name
    it. This asserts the live CLI carries it -- built as the real command, not read
    from source, so a flag the source defines but the command never wires would not
    pass. ``_derive_escape_hatch_flag`` above raised at import if it were gone, so
    reaching this assertion already means the flag exists; the assertion records its
    shape and the command it sits on.
    """
    named = [opts for name, opts in _apply_option_specs() if name == "allow_uncommitted"]

    assert len(named) == 1 and _FLAG in named[0] and _FLAG.startswith("--"), (
        f"`migrate apply` exposes {len(named)} `allow_uncommitted` parameter(s) and a "
        f"derived flag `{_FLAG}`. Exactly one boolean flag is expected: it is the "
        f"escape hatch ADR-0034 decision 2 makes visible in the command line, and the "
        f"four records quote it. Removed, the records are false; and a config key in "
        f"its place is what `test_allow_uncommitted_is_not_a_config_key.py` refuses."
    )


# -- the scans' own positive controls ------------------------------------------

#: The four retired passages, verbatim at ``8f308564^`` -- the state immediately
#: before cluster 4's docs commit, so a squash leaves them findable through PR #683
#: rather than through the sha. Line breaks included, because three wrap mid-claim
#: and the roadmap wraps ``committed or\n  not`` through the middle of the phrase
#: itself: folding that wrap is what makes the reversion scan able to see it at all.
_RETIRED_PASSAGES: Final[tuple[tuple[str, str], ...]] = (
    (
        "README",
        "nothing in the code checks that the merge happened**: `migrate apply` applies\n"
        "whatever is in `.theurian/migrations/`, committed or not. The review is a\n",
    ),
    (
        "docs/index",
        "hold it. That a human merged the proposal is *not*: `migrate apply` applies\n"
        "whatever is in `.theurian/migrations/`, committed or not, so the review is a\n",
    ),
    (
        "roadmap",
        "  model records the residual under T-15: *nothing enforces the merge*.\n"
        "  `migrate apply` applies whatever is in `.theurian/migrations/`, committed or\n"
        "  not; the human's review is a workflow convention rather than a check the code\n"
        "  makes, and the actors table's untrusted same-UID process can run it directly.\n",
    ),
    (
        "threat-model",
        "**Residual: nothing enforces the merge.** `migrate apply` applies whatever is in\n"
        "`.theurian/migrations/`, committed or not — the human's review is a workflow\n"
        "convention, not a check the code makes, and the actors table's untrusted\n"
        "same-UID process can run it directly.\n",
    ),
)


@pytest.mark.parametrize(
    ("label", "passage"),
    _RETIRED_PASSAGES,
    ids=[case[0] for case in _RETIRED_PASSAGES],
)
def test_the_reversion_scan_reports_the_retired_passages(label: str, passage: str) -> None:
    """The premise under every absence arm: the reversion scan can still fire.

    An absence arm is green against a scan that has stopped matching -- a narrowed
    key, a normalisation that stopped folding wraps, a subject spelling that drifted
    -- and it is green most convincingly at exactly that moment. So the four retired
    passages are held here verbatim, and the scan is required to report each one.

    Verbatim including the line breaks, which a synthetic string would miss: the
    roadmap passage wraps ``committed or\\n  not`` through the middle of its own
    phrase, and a scan that stopped normalising would report nothing while the
    shipped records stayed green.
    """
    reported = _un_narrowed_reversions(passage)

    assert reported, (
        f"the retired {label} passage is no longer reported by the reversion scan.\n\n"
        f"The key that guards the four records has stopped matching the sentences it "
        f"was written for -- a narrowed pattern, a subject spelling that drifted, or a "
        f"normalisation that stopped folding the wrap this passage carries. Every "
        f"absence arm is green whatever the records now say.\n\n{passage}"
    )


@pytest.mark.parametrize(
    ("label", "passage"),
    _RETIRED_PASSAGES,
    ids=[case[0] for case in _RETIRED_PASSAGES],
)
def test_the_narrowing_scan_does_not_fire_on_the_retired_claim(label: str, passage: str) -> None:
    """The premise under every narrowing arm: the key discriminates the two claims.

    :func:`test_each_record_states_the_narrowed_committed_check` is worth nothing if
    its key also matches the pre-B3 sentence -- a reverted passage would then keep it
    green while the reversion arm alone caught the drift, and a passage that stated
    neither claim would read as narrowed. So the retired passages are driven through
    the narrowing key and required to match **none** of it: the two scans partition
    the old world from the new, which is what lets a reverted paragraph fail the
    narrowing arm rather than slip past it.
    """
    assert not _NARROWED.search(prose(passage)), (
        f"the narrowing key matches the retired {label} passage, so it no longer "
        f"tells the pre-B3 claim from the narrowed one -- a reverted record would pass "
        f"`test_each_record_states_the_narrowed_committed_check` on the strength of "
        f"the old sentence:\n\n{passage}"
    )
