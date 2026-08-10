"""``_repository_relative``'s three refusals are what make the integrity check sound.

tools/mutate.py's own module docstring: "The narrowing is only sound because
a mutation cannot reach a file the check is not watching, so anchor paths
that are absolute-outside-the-repository or contain ``..`` are rejected
before anything is copied." Before this test file, none of the three
refusals -- ``..`` traversal, an absolute path outside the repository, and
an empty path -- had a single test. A silent regression here would not fail
loudly: it would let a mutation spec write outside the isolated copy while
the checkout-integrity check kept watching only the paths it was told about,
which is exactly the security boundary this function exists to hold.
"""

from __future__ import annotations

import mutate_edits
import pytest

pytestmark = pytest.mark.unit


def test_a_relative_path_that_climbs_out_with_dotdot_is_refused() -> None:
    """``tree / "../x"`` climbs out of the copy; ``..`` anywhere must be refused."""
    with pytest.raises(mutate_edits.HarnessError, match="climbs out of the repository"):
        mutate_edits._repository_relative("../etc/passwd", "test-label")


def test_a_dotdot_in_the_middle_of_an_otherwise_normal_path_is_also_refused() -> None:
    """The check is on every path segment, not just a leading ``..``."""
    with pytest.raises(mutate_edits.HarnessError, match="climbs out of the repository"):
        mutate_edits._repository_relative("tools/../../etc/passwd", "test-label")


def test_an_absolute_path_outside_the_repository_is_refused() -> None:
    """``tree / "/etc/passwd"`` is ``/etc/passwd``: absolute paths must resolve inside."""
    with pytest.raises(mutate_edits.HarnessError, match="is outside"):
        mutate_edits._repository_relative("/etc/passwd", "test-label")


def test_an_empty_path_is_refused() -> None:
    """An empty anchor path names nothing to mutate."""
    with pytest.raises(mutate_edits.HarnessError, match="empty path cannot be mutated"):
        mutate_edits._repository_relative("", "test-label")


def test_an_ordinary_relative_path_inside_the_repository_is_accepted() -> None:
    """Regression guard: the three refusals must not reject legitimate anchors."""
    assert mutate_edits._repository_relative("tools/mutate.py", "test-label") == "tools/mutate.py"


def test_an_absolute_path_inside_the_repository_is_accepted_and_made_relative() -> None:
    """An absolute path that does resolve inside REPO_ROOT is normalised, not refused."""
    absolute = str(mutate_edits.REPO_ROOT / "tools" / "mutate.py")

    assert mutate_edits._repository_relative(absolute, "test-label") == "tools/mutate.py"
