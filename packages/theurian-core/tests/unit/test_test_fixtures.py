"""The test fixtures must themselves be valid.

Every ULID literal in the suite is checked here. Three separate test failures
during Milestone 1 turned out to be invalid fixture ULIDs containing I, L, O, or
U -- characters Crockford base32 excludes -- rather than defects in the code
under test. A wrong fixture that makes a real test fail for the wrong reason
costs more time than the test saves.
"""

from __future__ import annotations

import pathlib
import re

import pytest

from theurian.domain.errors import InvalidIdentifierError
from theurian.domain.identifiers import Ulid

TESTS_ROOT = pathlib.Path(__file__).resolve().parents[1]
REPO_TESTS = pathlib.Path(__file__).resolve().parents[4] / "tests"

#: A 26-character token that looks like someone meant it to be a ULID.
_CANDIDATE = re.compile(r"[\"\']([0-9A-Z]{26})[\"\']")

#: Marks a literal that is invalid *on purpose*, in a test asserting rejection.
#: Explicit rather than inferred: guessing which literals are deliberate would
#: make this guard unreliable in both directions.
_DELIBERATE = "# invalid-ulid"


def _python_files() -> list[pathlib.Path]:
    return sorted(TESTS_ROOT.rglob("*.py")) + sorted(REPO_TESTS.rglob("*.py"))


@pytest.mark.parametrize("path", _python_files(), ids=lambda p: p.name)
def test_every_ulid_literal_is_valid(path: pathlib.Path) -> None:
    invalid: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if _DELIBERATE in line:
            continue
        for candidate in _CANDIDATE.findall(line):
            try:
                Ulid(candidate)
            except InvalidIdentifierError:
                invalid.append(candidate)

    assert not invalid, (
        f"{path.name} contains invalid ULID literals: {invalid}. "
        f"Crockford base32 excludes I, L, O, and U. If a literal is invalid on "
        f"purpose, mark its line with `{_DELIBERATE}`."
    )
