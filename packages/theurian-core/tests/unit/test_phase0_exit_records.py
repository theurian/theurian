"""The Phase-0 exit records, held against the live measurements they state.

**Why this file exists.** PR #552 trued up seven record-truth defects in the
roadmap appendix, the README and SECURITY.md — counts and claims about *what the
codebase contains* that a merge had silently falsified. The sharpest of them is
appendix row 7: its discharge said "README set to 26 and there are 26" and, in
the same sentence, predicted its own falsification — "nothing pins the number, so
the next ADR falsifies it again". Three ADRs later it had, and nothing went RED,
because **no test recomputes an ADR count from the index** (`git grep -n
'adr/README' -- packages/theurian-core/tests/ tools/` was empty). These pins are
that missing recomputation, for the records #552 corrected.

**These hold RECORD-truth, not behaviour.** They fail when a durable document
disagrees with a live measurement of the tree — a stale count, a retracted claim
that crept back, a version literal that will rot. They say nothing about whether
the code is correct; that is other files' work. The instrument they mirror is
``test_documented_tool_set.py``'s roadmap row-6 pin: a figure the prose states,
recomputed here under the prose's own stated key, so the next drift reddens
instead of ageing quietly into a document people read to learn what is broken.

**What is derived, and from where.** The ADR population comes from ``git
ls-files`` and from the index table's own rows — two independent authorities that
must agree, which is exactly what nobody checked before row 7 rotted. Each
roadmap figure is recomputed by running the key the row itself writes down (the
grep command in row 3, the exact-string heading in row 10), never a key restated
here. The supported-versions series is checked against the live version in
``pyproject.toml``.

**Reach, stated as narrowly as it is true.**

- The ADR pins hold that the file set and the index table agree, that the README
  nav label names no count, and that every ADR-count the roadmap states is either
  the live count or carried beside a dated/sha anchor that frames it as a
  measurement. They do **not** hold that any ADR's *content* is right.
- Row 10's *literal* ``Still owed`` count is pinned; its *concept* count (22) and
  seven-opener spread are recorded-only, because the concept key is a human
  classification of seven hand-identified section spellings — encoding it here
  would re-litigate that judgement on every ADR edit rather than measure a fact.
- The SECURITY.md pin holds that no version literal sits in the supported-versions
  table's Version column — the one place #552's own reasoning names as where "dev0
  is current" would return. It **cannot** hold that the supported *window* is
  correct: which release actually receives fixes is settled by the ``core-v*`` tag
  list and PyPI, authorities this test does not read. It pins that no rotting
  literal came back, and nothing more.

Reads three files and shells to ``git`` against the checkout, the way
``test_command_population.py`` and ``test_documented_commands.py`` do; it builds
nothing and opens no socket.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from typing import Final

import pytest
from write_lock_claims import REPO_ROOT

#: Resolved once: ``shutil.which`` hands ``subprocess`` a full path, the way
#: ``command_population.py`` does, so no partial-executable lint fires and a
#: machine without git skips rather than erroring on a truthful measurement.
_GIT: Final = shutil.which("git")

#: The ADR template is a scaffold, not a decision, so it is excluded from every
#: ADR population — the same exclusion #552's own measurements make
#: (``grep -v 0000-adr-template``).
ADR_TEMPLATE_STEM: Final = "0000-adr-template"

ROADMAP: Final = REPO_ROOT / "docs/roadmap.md"
README: Final = REPO_ROOT / "README.md"
SECURITY: Final = REPO_ROOT / "SECURITY.md"
ADR_INDEX: Final = REPO_ROOT / "docs/adr/README.md"
CORE_PYPROJECT: Final = REPO_ROOT / "packages/theurian-core/pyproject.toml"

#: An index-table row: ``| [0001](0001-....md) | Title | accepted |``. The four
#: leading digits in a link are what makes a row an ADR entry rather than the
#: header or the ``:--`` separator.
_INDEX_ROW: Final = re.compile(r"^\| \[[0-9]{4}\]", re.MULTILINE)

#: The nav anchor pointing at the ADR index, and its visible label. #552 took the
#: count out of that label (it now reads ``ADRs``, like its ``Roadmap`` and
#: ``Threat model`` siblings) precisely so it cannot rot; this reads the label
#: back to hold that.
_ADR_NAV_ANCHOR: Final = re.compile(r'<a href="docs/adr/README\.md">([^<]*)</a>')

#: An ADR count anywhere in prose: digits, optional bold markers, then ``ADR``.
#: Row 7's own key — the bold-tolerant form, because ``[0-9]+ ADRs`` alone misses
#: row 8's ``**26** ADRs``.
_ADR_COUNT: Final = re.compile(r"([0-9]+)\*{0,2}\s+ADRs?")

#: A dated or sha anchor: an ISO date, or a git object name (7-40 hex). A roadmap
#: block that states a non-live ADR count must carry one, which is what turns the
#: number from a live claim into a dated measurement.
_DATED_OR_SHA_ANCHOR: Final = re.compile(r"\d{4}-\d{2}-\d{2}|\b[0-9a-f]{7,40}\b")

#: A full version literal — ``0.1.0``, ``0.1.0.dev18``, ``1.2.3rc1``. A MINOR
#: series (``0.1.x``) does not match it, which is the whole point: the supported
#: column names a series, and a literal here is the stale-count defect wearing a
#: security document.
_VERSION_LITERAL: Final = re.compile(r"\d+\.\d+\.\d+(?:\.?[a-z]+\d*)?")


def _git(*args: str) -> list[str]:
    """Run one read-only ``git`` command against the checkout, returning its lines.

    ``git grep`` exits 1 when nothing matches, which is not an error here, so the
    return code is not checked — only a crash (a bad pathspec) would raise, and
    that is the run being untrustworthy rather than a clean tree.
    """
    if _GIT is None:  # pragma: no cover - git is present in CI and dev
        pytest.skip("git is not on PATH, so the tree cannot be measured")
    completed = subprocess.run(  # noqa: S603 - argv is module-owned, never user input
        [_GIT, *args],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    return [line for line in completed.stdout.splitlines() if line]


def _adr_files() -> list[str]:
    """Every shipped ADR file, template excluded — the file-set authority."""
    return [path for path in _git("ls-files", "docs/adr/0*.md") if ADR_TEMPLATE_STEM not in path]


def _live_adr_count() -> int:
    return len(_adr_files())


def _roadmap_blocks() -> list[str]:
    """The roadmap split into logical units, soft wraps rejoined.

    A table row is one physical line and becomes its own block; a prose bullet and
    its continuation lines rejoin into one. The unit matters because the anchor a
    count leans on can sit on a *later* wrapped line: ``All 26 ADRs are accepted``
    opens a bullet whose ``(measured 2026-08-20; 24 at f702736 …)`` frame is three
    physical lines down, and a per-line check would call that count unanchored.
    """
    blocks: list[str] = []
    current: list[str] = []

    def flush() -> None:
        if current:
            blocks.append("\n".join(current))
            current.clear()

    for line in ROADMAP.read_text(encoding="utf-8").splitlines():
        stripped = line.lstrip()
        if not stripped:
            flush()
        elif stripped.startswith("|"):
            flush()
            blocks.append(line)
        elif stripped.startswith(("- ", "> ", "* ")):
            flush()
            current.append(line)
        else:
            current.append(line)
    flush()
    return blocks


def _roadmap_line(marker: str) -> str:
    """The one roadmap line carrying ``marker``, or a failure naming the drift."""
    matches = [line for line in ROADMAP.read_text(encoding="utf-8").splitlines() if marker in line]
    assert len(matches) == 1, (
        f"the roadmap line keyed on {marker!r} is not findable as exactly one line "
        f"(found {len(matches)}); the row was reworded past its key or duplicated"
    )
    return matches[0]


# -- Item 1: the ADR count, the gap that let row 7's discharge rot ----------


def test_the_adr_index_table_lists_exactly_the_adr_files_it_ships() -> None:
    """Two authorities for the ADR count must agree. RED when a new ADR lands unlisted.

    This is the recomputation whose absence let row 7 rot: the discharge trusted
    ``docs/adr/README.md`` as "a table that cannot disagree with itself", but
    nothing held the table against the *files*. ``git ls-files`` (minus the
    template) and the index table's own rows are two independent derivations of
    the same number; a new ADR file added without an index row — or an index row
    for a file that does not exist — moves them apart and reddens here, which is
    the event three ADRs' worth of drift slipped past.
    """
    files = _adr_files()
    index_rows = _INDEX_ROW.findall(ADR_INDEX.read_text(encoding="utf-8"))

    assert files, "git ls-files found no ADR files, so this test would assert nothing"
    assert len(files) == len(index_rows), (
        f"the ADR file set ({len(files)}) and the index table's rows "
        f"({len(index_rows)}) disagree: a decision landed as a file without a table "
        f"row, or a row outlives its file. Files: {sorted(files)}"
    )


def test_the_readme_adr_nav_label_carries_no_count() -> None:
    """The README's ADR link names no number, so it cannot go stale. RED if one returns.

    Row 7's durable fix was to delete the count from this label — it used to read
    ``26 ADRs`` and drifted with every decision. The label now names the register
    (``ADRs``) and hands the count to the index table, which
    :func:`test_the_adr_index_table_lists_exactly_the_adr_files_it_ships` keeps
    honest. This reddens the moment a number is written back into the label.
    """
    text = README.read_text(encoding="utf-8")
    match = _ADR_NAV_ANCHOR.search(text)

    assert match is not None, (
        'the README no longer carries an `<a href="docs/adr/README.md">…</a>` nav '
        "anchor; the label whose count #552 removed has moved past this key"
    )
    label = match.group(1)
    assert not re.search(r"\d", label), (
        f"the README's ADR nav label reads {label!r}, which carries a digit again — "
        f"the count #552 removed so it could not rot has come back"
    )


def test_every_roadmap_adr_count_is_the_live_count_or_a_dated_measurement() -> None:
    """A bare ADR count that disagrees with the tree is a defect; a dated one is a record.

    The roadmap keeps several ADR counts on purpose — row 7 quotes ``23 ADRs`` as
    history, row 8 states ``**26** ADRs`` under the appendix's ``f702736`` frame,
    the reconciliation bullet says ``26`` as of a dated snapshot. Each is correct
    *because* it is framed: it sits in a block that carries a date or a sha, which
    reads it as a measurement rather than a live claim. So the rule is
    per-block — every ADR count either equals the live count or is anchored — and
    it reddens when a *bare* present-tense count is written that disagrees with the
    tree, which is the shape row 7 shipped.

    The key detects drift: a planted ``30 ADRs`` in an unanchored block goes RED
    against the live count of 29 (measured while writing this).
    """
    live = _live_adr_count()
    unanchored: list[str] = []
    counted = 0
    for block in _roadmap_blocks():
        anchored = bool(_DATED_OR_SHA_ANCHOR.search(block))
        for match in _ADR_COUNT.finditer(block):
            counted += 1
            if int(match.group(1)) != live and not anchored:
                unanchored.append(f"{match.group()!r} in: {block.splitlines()[0][:70]}")

    assert counted, "no ADR count was found in the roadmap, so this test asserts nothing"
    assert not unanchored, (
        f"the roadmap states an ADR count that is neither the live count ({live}) "
        f"nor framed by a date or sha as a measurement: {unanchored}"
    )


# -- Item 2: the population counts the appendix rows measure ----------------


def test_the_roadmap_raptor_row_counts_the_forest_sites_it_names() -> None:
    """Row 3's ``4 lines in 4 files`` is recomputed under the row's own grep key.

    Row 3 records that the "forest is built but never read" claim is going stale
    as retrieval learns to read it: it states a figure and the exact
    ``git grep`` key that produced it. Both are read off the row here and the grep
    is re-run, so a docstring corrected (or one added) moves the live count and
    reddens the row rather than leaving its ``4 lines in 4 files`` behind.
    """
    row = _roadmap_line("state that the RAPTOR forest is built but never read")
    figure = re.search(r"returns \*\*(\d+) lines in (\d+) files\*\*", row)
    command = re.search(r'git grep -n "([^"]+)" -- packages/ docs/architecture/', row)

    assert figure is not None, "row 3 no longer states its figure as `**N lines in M files**`"
    assert command is not None, (
        "row 3 no longer embeds its `git grep` key, so nothing recomputes it"
    )
    stated_lines, stated_files = int(figure.group(1)), int(figure.group(2))
    phrases = command.group(1).split(r"\|")

    hits = _git(
        "grep",
        "-n",
        *[argument for phrase in phrases for argument in ("-e", phrase)],
        "--",
        "packages/",
        "docs/architecture/",
    )
    live_lines = len(hits)
    live_files = len({line.split(":", 1)[0] for line in hits})

    assert (live_lines, live_files) == (stated_lines, stated_files), (
        f"row 3 says its key returns {stated_lines} lines in {stated_files} files; "
        f"it returns {live_lines} lines in {live_files} files now. A forest-read "
        f"docstring was corrected or added and the row did not move with it"
    )


def test_the_roadmap_still_owed_row_counts_the_literal_sections() -> None:
    """Row 10's literal ``15 of 29`` is recomputed under its own exact-string key.

    Row 10 states two figures — a literal ``Still owed`` count under an exact
    heading, and a wider concept count across seven opener spellings. Only the
    literal one is recomputed here: it is a single ``git grep`` and cannot argue.
    The concept count and the opener spread are recorded-only, and deliberately —
    the concept is a human classification of seven hand-identified spellings, and
    encoding it here would re-run that judgement on every ADR edit rather than
    measure a fact (this module's docstring records the split).

    Both halves of the ``N of D`` are pinned: the numerator against the grep, and
    the denominator against the live ADR count, so the fraction cannot drift on
    either side.
    """
    row = _roadmap_line("still carry *Still owed* items")
    stated = re.search(r"under the exact string `([^`]+)` the count is \*\*(\d+) of (\d+)\*\*", row)

    assert stated is not None, "row 10 no longer states its literal `Still owed` key and `N of D`"
    heading, stated_count, stated_denominator = (
        stated.group(1),
        int(stated.group(2)),
        int(stated.group(3)),
    )

    live_count = len(_git("grep", "-c", f"^{heading}", "--", "docs/adr/0*.md"))
    live_adrs = _live_adr_count()

    assert live_count == stated_count, (
        f"row 10 says {stated_count} ADRs open a `{heading}` section; {live_count} do "
        f"now. A section was discharged or added and the row did not move with it"
    )
    assert stated_denominator == live_adrs, (
        f"row 10's `of {stated_denominator}` disagrees with the live ADR count "
        f"({live_adrs}); the denominator has gone stale"
    )


def test_the_retracted_write_time_sensitivity_wording_survives_only_in_its_own_cell() -> None:
    """Row 12's retraction must not have leaked back into SECURITY.md. RED if it does.

    Row 12 discharged a SECURITY.md self-contradiction — the passage saying
    ``sensitivity, tenant and ACL group`` are refused at write time, which the code
    never did. #552 kept that exact wording alive in *one* place, the discharged
    row itself, deliberately: a discharge that deletes the claim it discharged
    leaves a later reader no way to check it. So the phrase must appear exactly
    once, in the roadmap, and never in SECURITY.md — where its return would be the
    original contradiction, reopened inside a security document.
    """
    carriers = _git("grep", "-l", "sensitivity, tenant and ACL group")

    assert carriers == ["docs/roadmap.md"], (
        f"the retracted `sensitivity, tenant and ACL group` write-time wording is "
        f"carried by {carriers}, not by the roadmap cell alone. If SECURITY.md is "
        f"among them the discharged contradiction has reopened; if the roadmap is "
        f"absent the discharge has deleted the claim it was meant to preserve"
    )


# -- Item 3: no version literal rots inside SECURITY.md's supported column ---


def test_the_security_supported_versions_table_names_a_series_not_a_literal() -> None:
    """The supported column names a MINOR series, never a version. RED if a literal returns.

    SECURITY.md's own reasoning names this as the failure mode: naming a single
    version in the supported column "would go false at the next release, leaving
    the only installable Core outside both rows". #552 removed three ``0.1.0.dev0``
    current-release claims for the same reason. This holds the Version cells of
    that table to a MINOR series and to the live train's minor, read from
    ``pyproject.toml`` — so a future edit writing ``0.1.0.dev18`` (or any version
    literal) back into the supported column reddens.

    **What it does not hold**, stated because the reach is narrow: not that
    ``0.1.x`` is the *correct* supported window — which releases actually receive
    fixes is settled by the ``core-v*`` tag list and PyPI, which this test does not
    read — and not the dated ``0.1.0.dev0`` history the section keeps elsewhere on
    purpose. It holds that no rotting literal sits in the column, nothing more.
    """
    live_version = re.search(
        r'^version = "([^"]+)"', CORE_PYPROJECT.read_text(encoding="utf-8"), re.MULTILINE
    )
    assert live_version is not None, "the core pyproject no longer states a version to anchor to"
    major_minor = re.match(r"(\d+)\.(\d+)", live_version.group(1))
    assert major_minor is not None, f"the core version {live_version.group(1)!r} has no MINOR"
    expected_series = f"{major_minor.group(1)}.{major_minor.group(2)}.x"

    section = re.search(
        r"^## Supported versions\n(.*?)(?=\n## )",
        SECURITY.read_text(encoding="utf-8"),
        re.DOTALL | re.MULTILINE,
    )
    assert section is not None, "SECURITY.md no longer carries a `## Supported versions` section"
    rows = re.findall(r"^\| ([^|]+?) \| ([^|]+?) \| ([^|]+?) \|$", section.group(1), re.MULTILINE)

    core_series = [
        version.strip()
        for artifact, version, _ in rows
        if artifact.strip() == "Theurian Core" and not version.strip().startswith("<")
    ]
    assert core_series == [expected_series], (
        f"the Supported-versions table's live Theurian Core row names {core_series}, "
        f"not [{expected_series!r}] derived from pyproject's {live_version.group(1)!r}"
    )

    literals = {
        (artifact.strip(), version.strip())
        for artifact, version, _ in rows
        if _VERSION_LITERAL.search(version)
    }
    assert not literals, (
        f"the Supported-versions table names a version literal in its Version "
        f"column: {sorted(literals)}. A supported column names a MINOR series, or "
        f"it goes false at the next release — the class #552 removed from this file"
    )
