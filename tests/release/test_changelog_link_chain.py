"""The core changelog's heading-to-definition chain.

Why this file exists
--------------------
`[Unreleased]` compared `core-v0.1.0.dev2...main` while 0.1.0, 0.2.0 and 0.2.1
had all shipped, so the rendered page told a reader that three released
versions were still unreleased. Nineteen of the file's twenty-three headings
had drifted into that state -- resolving to nothing, or resolving to the wrong
range -- without anything noticing, because nothing in this repository read the
link block at all.

Fixing the three headings the review named then produced the *second* shape:
`[0.1.0]` was given `core-v0.1.0.dev2...core-v0.1.0`, which is defined and
wrong. Sixteen dev releases sit between dev2 and 0.1.0, so that link folds all
of them into 0.1.0's diff -- the same defect as the `[Unreleased]` one it was
part of fixing. A count of headings-without-a-definition reaches zero on that
file, which is why this module checks two different things and why the second
check is the one that catches a fold.

What settles each half
----------------------
Both halves are derivable from this one file, so both run offline in
milliseconds. A release's predecessor is the heading directly below it, and a
release's tag is `core-v` plus the heading text.

What is *not* derivable here is whether those tags exist on the remote: a
definition can name a perfectly-chained tag that nobody ever pushed, and this
file will call it correct. **Tag existence stays a cut-time human check**
(`git ls-remote --tags origin`, release.md section 4), deliberately -- the
division keeps this file offline and fast, and it is why a green run here is
not on its own a statement that every link resolves.

Both checks are exercised against synthetic defects below, so neither can
silently become a test that cannot fail.

Not covered
-----------
* Whether a tag named by a definition exists on the remote, per above.
* The link block's position or formatting; only its content is pinned.
* The root `CHANGELOG.md`, which carries no link block.
"""

from __future__ import annotations

import pathlib
import re
from typing import Final

import pytest

pytestmark = pytest.mark.integration

REPO_ROOT: Final = pathlib.Path(__file__).resolve().parents[2]
CORE_CHANGELOG: Final = REPO_ROOT / "packages" / "theurian-core" / "CHANGELOG.md"

#: Two release trains share this repository (ADR-0001), so a core heading maps
#: to `core-v<heading>` and never to a bare `v<heading>`.
TAG_PREFIX: Final = "core-v"

#: The one heading that names no release. Its definition compares the newest
#: release to `main`, and it is the definition the review finding caught being
#: false, so it is chained here like any other rather than exempted.
UNRELEASED: Final = "Unreleased"

_HEADING: Final = re.compile(r"^## \[([^\]]+)\]", re.MULTILINE)
_DEFINITION: Final = re.compile(r"^\[([^\]]+)\]: (\S+)$", re.MULTILINE)
_COMPARE: Final = re.compile(r"/compare/(\S+)\.\.\.(\S+)$")
_RELEASE_TAG_LINK: Final = re.compile(r"/releases/tag/(\S+)$")


def _headings(text: str) -> list[str]:
    """Every `## [X]` heading in file order, which is newest release first."""
    return _HEADING.findall(text)


def _definitions(text: str) -> list[tuple[str, str]]:
    """Every `[X]: <url>` link definition in file order."""
    return [(name, url) for name, url in _DEFINITION.findall(text)]


def _unpaired(text: str) -> tuple[list[str], list[str], list[str]]:
    """Headings with no definition, definitions with no heading, and duplicates.

    Three lists rather than one count: a heading that renders as plain text and
    a definition left behind by a renamed heading are different edits to make,
    and a reader fixing one should not have to work out which they have.
    """
    headings = _headings(text)
    defined = [name for name, _ in _definitions(text)]
    undefined = [name for name in headings if name not in set(defined)]
    orphaned = [name for name in defined if name not in set(headings)]
    duplicated = sorted({name for name in defined if defined.count(name) > 1})
    return undefined, orphaned, duplicated


def _mischained(text: str) -> list[str]:
    """Definitions whose endpoints disagree with the order of the headings.

    The rule each definition has to satisfy, read off the file itself:

    * the newest heading is `[Unreleased]`, which compares the release below it
      to `main`;
    * every other heading compares the release below it to itself;
    * the oldest heading has nothing below it to compare against, so it points
      at its own tag instead.

    A heading with no definition is skipped here and reported by
    :func:`_unpaired`, so one missing line produces one failure rather than two.
    """
    headings = _headings(text)
    urls = dict(_definitions(text))
    problems: list[str] = []

    for index, name in enumerate(headings):
        url = urls.get(name)
        if url is None:
            continue

        if index + 1 == len(headings):
            expected_tag = f"{TAG_PREFIX}{name}"
            found = _RELEASE_TAG_LINK.search(url)
            if found is None or found.group(1) != expected_tag:
                problems.append(
                    f"[{name}] is the oldest release, so it has no predecessor to "
                    f"compare against and must point at /releases/tag/{expected_tag}; "
                    f"it points at {url}"
                )
            continue

        predecessor = headings[index + 1]
        expected_from = f"{TAG_PREFIX}{predecessor}"
        expected_to = "main" if name == UNRELEASED else f"{TAG_PREFIX}{name}"

        found = _COMPARE.search(url)
        if found is None:
            problems.append(
                f"[{name}] must compare {expected_from}...{expected_to}, but its "
                f"definition is not a compare link at all: {url}"
            )
            continue

        actual_from, actual_to = found.groups()
        if (actual_from, actual_to) != (expected_from, expected_to):
            problems.append(
                f"[{name}] compares {actual_from}...{actual_to}, which is not what the "
                f"file's own order says: the heading directly below it is "
                f"[{predecessor}], so it must compare {expected_from}...{expected_to}"
            )

    return problems


# -- the standing checks, against the changelog this repository ships ---------


def test_every_changelog_heading_has_exactly_one_link_definition() -> None:
    """Every `## [X]` is defined, every `[X]:` is used, and nothing is defined twice.

    An undefined heading renders as literal `[0.1.0.dev7]` on the released page,
    which is how sixteen of them accumulated unnoticed.
    """
    text = CORE_CHANGELOG.read_text(encoding="utf-8")

    undefined, orphaned, duplicated = _unpaired(text)

    assert not undefined, (
        f"these changelog headings have no link definition and render as plain "
        f"text: {undefined}. Add `[X]: .../compare/core-v<predecessor>...core-v<X>` "
        f"to the block at the end of the file"
    )
    assert not orphaned, (
        f"these link definitions have no heading to attach to: {orphaned}. A "
        f"heading was renamed or removed and its definition stayed behind"
    )
    assert not duplicated, (
        f"these link definitions appear more than once: {duplicated}. Markdown "
        f"resolves one of them and silently discards the rest"
    )


def test_every_link_definition_compares_the_release_directly_below_it() -> None:
    """A definition's endpoints must match the file's own release order.

    This is the half a count cannot do. `[0.1.0]` was once defined -- so the
    pairing check above was green -- while comparing `core-v0.1.0.dev2`, which
    folded the sixteen dev releases between them into 0.1.0's diff.
    """
    text = CORE_CHANGELOG.read_text(encoding="utf-8")

    problems = _mischained(text)

    assert not problems, "the changelog's compare links disagree with its headings:\n- " + (
        "\n- ".join(problems)
    )


# -- the controls: both checks, driven against the defects that happened ------

#: A minimal, correct changelog in the shipped file's shape. Each control below
#: introduces exactly one defect into this, so a control that goes green is a
#: statement about the defect and not about the fixture.
_CORRECT: Final = """\
# Changelog

## [Unreleased]

## [0.2.0] - 2026-09-12

### Fixed

- A fix.

## [0.1.0] - 2026-09-05

### Added

- A feature.

## [0.1.0.dev1] - 2026-08-12

### Added

- The first thing.

[Unreleased]: https://github.com/theurian/theurian/compare/core-v0.2.0...main
[0.2.0]: https://github.com/theurian/theurian/compare/core-v0.1.0...core-v0.2.0
[0.1.0]: https://github.com/theurian/theurian/compare/core-v0.1.0.dev1...core-v0.1.0
[0.1.0.dev1]: https://github.com/theurian/theurian/releases/tag/core-v0.1.0.dev1
"""


def test_the_controls_fixture_is_itself_clean() -> None:
    """Neither check fires on the correct fixture, so a firing control means the defect."""
    assert _unpaired(_CORRECT) == ([], [], [])
    assert _mischained(_CORRECT) == []


def test_the_pairing_check_reports_a_heading_whose_definition_is_missing() -> None:
    """The first shape: 0.1.0.dev3 through 0.1.0.dev18, defined nowhere."""
    defective = _CORRECT.replace(
        "[0.1.0]: https://github.com/theurian/theurian/compare/core-v0.1.0.dev1...core-v0.1.0\n",
        "",
    )
    assert defective != _CORRECT, "the control did not remove anything"

    undefined, orphaned, duplicated = _unpaired(defective)

    assert undefined == ["0.1.0"]
    assert (orphaned, duplicated) == ([], [])


def test_the_chain_check_reports_a_definition_that_folds_two_releases() -> None:
    """The second shape: `[0.1.0]` defined, and comparing past the release below it.

    Reproduced here at the size the fixture allows -- 0.2.0 reaching over 0.1.0
    to dev1 -- which is the same edit as the shipped `[0.1.0]` reaching over
    sixteen dev releases to dev2.
    """
    defective = _CORRECT.replace(
        "[0.2.0]: https://github.com/theurian/theurian/compare/core-v0.1.0...core-v0.2.0",
        "[0.2.0]: https://github.com/theurian/theurian/compare/core-v0.1.0.dev1...core-v0.2.0",
    )
    assert defective != _CORRECT, "the control did not perturb anything"

    # The pairing check stays green on this input: the defect is a wrong
    # definition, not a missing one, which is why both checks exist.
    assert _unpaired(defective) == ([], [], [])

    problems = _mischained(defective)

    assert len(problems) == 1, problems
    assert "[0.2.0] compares core-v0.1.0.dev1...core-v0.2.0" in problems[0]
    assert "must compare core-v0.1.0...core-v0.2.0" in problems[0]


def test_the_chain_check_reports_an_unreleased_link_that_folds_a_shipped_release() -> None:
    """The finding as reported: `[Unreleased]` reaching back past a release that shipped."""
    defective = _CORRECT.replace(
        "[Unreleased]: https://github.com/theurian/theurian/compare/core-v0.2.0...main",
        "[Unreleased]: https://github.com/theurian/theurian/compare/core-v0.1.0.dev1...main",
    )
    assert defective != _CORRECT, "the control did not perturb anything"

    problems = _mischained(defective)

    assert len(problems) == 1, problems
    assert "must compare core-v0.2.0...main" in problems[0]
