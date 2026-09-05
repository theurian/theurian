"""SEC-10's allowlist, driven with synthetic input (ADR-0030 decision 2).

Four properties, each with its own test because each can break on its own: the
pattern this module enforces is the one the schema publishes; a repository the
list does not name is refused; a case difference still matches; and a `.`/`..`
segment is refused on either side of the slash.

**The pattern pin is a derivation, not a transcription.** Nothing validates
``.theurian/config.yaml`` against ``project-config.schema.json`` at run time, so
a schema that rejects ``..`` and a reader that accepts it would leave the
tightening inert -- documentation with no enforcement behind it. The test reads
the schema file.

That "no process was spawned" is the *shape* of this refusal is held one layer
up, where a process could be spawned at all:
``tests/integration/test_gh_review_provider.py::test_an_unallowlisted_repository_starts_no_process``.
Here there is nothing to spawn, which is the point of putting the check in a
module that cannot.
"""

from __future__ import annotations

import json
import pathlib
from typing import Final

import pytest

from theurian.domain.errors import ProjectConfigError
from theurian.domain.review_ingest import RefusalGrade, ReviewIngestRefusedError
from theurian.security.project_config import PROJECT_CONFIG_FILE, read_review_repositories
from theurian.security.review_allowlist import (
    MAX_REPOSITORY_CHARS,
    REPOSITORY_PATTERN,
    allowlisted_repository,
    is_well_formed,
)

pytestmark = pytest.mark.unit

#: ``parents[4]`` is ``.../tests/unit/`` -> ``tests`` -> ``theurian-core`` ->
#: ``packages`` -> repo root, where the published schemas live.
REPO_ROOT = pathlib.Path(__file__).resolve().parents[4]

PROJECT_CONFIG_SCHEMA = REPO_ROOT / "schemas" / "config" / "project-config.schema.json"

#: Names GitHub issues that the allowlist must keep accepting. ``owner/.github``
#: is the one that makes "reject anything starting with a dot" wrong: it is a
#: real repository, and the special-repository convention every GitHub org uses.
_ACCEPTED: Final[tuple[str, ...]] = (
    "theurian/theurian",
    "acme/order-service",
    "acme/.github",
    "acme/order.service",
    "a_b-c.d/e_f-g.h",
    "Acme/Order-Service",
)

#: Names the tightened pattern must refuse. The first four are the traversal
#: shapes the old ``^[\w.-]+/[\w.-]+$`` accepted; the rest are shapes that were
#: never names.
_REFUSED: Final[tuple[str, ...]] = (
    "../..",
    "./acme",
    "acme/..",
    "acme/.",
    "..",
    ".",
    "acme",
    "acme/order/service",
    "acme /order",
    "acme/order service",
    "acme/order\n",
    "",
    "acme/",
    "/order",
)


def _project(tmp_path: pathlib.Path, body: str | None) -> tuple[pathlib.Path, pathlib.Path]:
    """A project root and its ``config.yaml``, written only when ``body`` is given."""
    knowledge = tmp_path / ".theurian"
    knowledge.mkdir()
    config = knowledge / PROJECT_CONFIG_FILE
    if body is not None:
        config.write_text(body, encoding="utf-8")
    return tmp_path, config


def _listing(*entries: str) -> str:
    """A configuration file whose allowlist is ``entries``."""
    listed = "\n".join(f"      - {entry}" for entry in entries)
    return "apiVersion: theurian.dev/v1\nproviders:\n  review:\n    repositories:\n" + listed + "\n"


def test_the_pattern_this_module_enforces_is_the_one_the_schema_publishes() -> None:
    """RED means the contract and the enforcement disagree about what a name is.

    Nothing validates ``.theurian/config.yaml`` against the published schema at
    run time, so the schema's ``pattern`` is enforced only because this module
    carries the same one. Transcribing it into a comment would let the two drift
    with every check green; reading the file is what makes the tightening real.
    """
    schema = json.loads(PROJECT_CONFIG_SCHEMA.read_text(encoding="utf-8"))
    published = schema["properties"]["providers"]["properties"]["review"]["properties"][
        "repositories"
    ]["items"]["pattern"]

    assert published == REPOSITORY_PATTERN, (
        f"the schema publishes {published!r} and this module enforces "
        f"{REPOSITORY_PATTERN!r}.\n\n"
        "Nothing validates a project's config.yaml against the schema at run time, so "
        "a pattern tightened in only one of the two places enforces nothing. Move both "
        "in the same change."
    )


@pytest.mark.parametrize("name", _ACCEPTED, ids=_ACCEPTED)
def test_a_name_github_issues_is_well_formed(name: str) -> None:
    """The positive control: without it the pattern could refuse everything and pass."""
    assert is_well_formed(name), (
        f"{name!r} is a name GitHub issues and the allowlist refused it. "
        "`acme/.github` in particular is why the pattern refuses a `.` *segment* "
        "rather than a leading dot."
    )


@pytest.mark.parametrize("name", _REFUSED, ids=[repr(name) for name in _REFUSED])
def test_a_traversal_or_malformed_name_is_refused(name: str) -> None:
    """The tightening, driven: the old pattern accepted the first four of these."""
    assert not is_well_formed(name), (
        f"{name!r} satisfied the allowlist pattern. The tightening exists because "
        "`^[\\w.-]+/[\\w.-]+$` accepted `../..` -- a value that is a path, not a "
        "repository -- while satisfying the published schema (ADR-0030 decision 3)."
    )


def test_a_name_longer_than_the_recorded_bound_is_refused_before_the_pattern_runs() -> None:
    """A caller cannot spend regex time on an unbounded string it chose."""
    assert not is_well_formed("a" * MAX_REPOSITORY_CHARS + "/b")


def test_an_unlisted_repository_is_refused_with_its_grade(tmp_path: pathlib.Path) -> None:
    """AC-1: a repository outside the allowlist is refused, before anything else happens."""
    root, config = _project(tmp_path, _listing("acme/order-service"))

    with pytest.raises(ReviewIngestRefusedError) as raised:
        allowlisted_repository(root, config, "acme/billing")

    assert raised.value.grade is RefusalGrade.REPOSITORY_NOT_ALLOWLISTED
    assert "acme/billing" in str(raised.value)
    assert raised.value.remedy


def test_a_refusal_does_not_publish_the_repositories_this_project_does_allow(
    tmp_path: pathlib.Path,
) -> None:
    """The refusal echoes the request and nothing the caller did not already send.

    A message that listed the allowlist back would tell whoever provoked the
    refusal which repositories this project ingests -- a fact about the operator's
    configuration, published by an error that fires for one input and not another.
    """
    root, config = _project(tmp_path, _listing("acme/order-service", "acme/private-plans"))

    with pytest.raises(ReviewIngestRefusedError) as raised:
        allowlisted_repository(root, config, "acme/billing")

    published = f"{raised.value} {raised.value.remedy} {raised.value.envelope.detail}"
    assert "order-service" not in published
    assert "private-plans" not in published


def test_a_traversal_request_is_refused_with_the_same_grade_as_an_unlisted_one(
    tmp_path: pathlib.Path,
) -> None:
    """One grade for both, so the refusal does not report which shape was sent."""
    root, config = _project(tmp_path, _listing("acme/order-service"))

    with pytest.raises(ReviewIngestRefusedError) as raised:
        allowlisted_repository(root, config, "../..")

    assert raised.value.grade is RefusalGrade.REPOSITORY_NOT_ALLOWLISTED


def test_a_case_difference_matches_and_answers_the_configured_spelling(
    tmp_path: pathlib.Path,
) -> None:
    """GitHub resolves names case-insensitively, so a byte comparison refuses a correct answer.

    The **configured** spelling comes back, not the request: the caller compares
    GitHub's resolved ``nameWithOwner`` against this value, and returning the
    request would compare the response against itself.
    """
    root, config = _project(tmp_path, _listing("Acme/Order-Service"))

    assert allowlisted_repository(root, config, "acme/order-service") == "Acme/Order-Service"


def test_an_empty_allowlist_allows_nothing(tmp_path: pathlib.Path) -> None:
    """An unconfigured project ingests no repository, not any repository."""
    root, config = _project(
        tmp_path, "apiVersion: theurian.dev/v1\nproviders:\n  review:\n    repositories: []\n"
    )

    with pytest.raises(ReviewIngestRefusedError) as raised:
        allowlisted_repository(root, config, "acme/order-service")

    assert raised.value.grade is RefusalGrade.REPOSITORY_NOT_ALLOWLISTED


@pytest.mark.parametrize(
    ("label", "body"),
    (
        ("no file at all", None),
        ("an empty file", ""),
        ("no providers block", "apiVersion: theurian.dev/v1\n"),
        ("an empty providers block", "apiVersion: theurian.dev/v1\nproviders:\n"),
        (
            "no review block",
            "apiVersion: theurian.dev/v1\nproviders:\n  embedding:\n    adapter: x\n",
        ),
        ("an empty review block", "apiVersion: theurian.dev/v1\nproviders:\n  review:\n"),
        (
            "a review block with no allowlist",
            "apiVersion: theurian.dev/v1\nproviders:\n  review:\n    adapter: none\n",
        ),
    ),
)
def test_a_project_that_states_no_allowlist_reads_as_empty(
    tmp_path: pathlib.Path, label: str, body: str | None
) -> None:
    """Every way of saying nothing means the same thing, and it is not "allow everything"."""
    root, config = _project(tmp_path, body)

    assert read_review_repositories(root, config) == (), label


def test_the_reader_answers_the_file_in_order(tmp_path: pathlib.Path) -> None:
    """Order is the file's, so a report that echoes the list is deterministic."""
    root, config = _project(tmp_path, _listing("z/last", "a/first", "m/middle"))

    assert read_review_repositories(root, config) == ("z/last", "a/first", "m/middle")


@pytest.mark.parametrize(
    ("label", "body"),
    (
        (
            "a bare string where a list belongs",
            "apiVersion: theurian.dev/v1\nproviders:\n  review:\n"
            "    repositories: acme/order-service\n",
        ),
        (
            "a mapping where a list belongs",
            "apiVersion: theurian.dev/v1\nproviders:\n  review:\n"
            "    repositories:\n      acme: order-service\n",
        ),
        (
            "an entry that is not a string",
            "apiVersion: theurian.dev/v1\nproviders:\n  review:\n    repositories:\n      - 7\n",
        ),
        (
            "a review block that is not a mapping",
            "apiVersion: theurian.dev/v1\nproviders:\n  review: none\n",
        ),
    ),
)
def test_a_malformed_allowlist_is_refused_rather_than_read(
    tmp_path: pathlib.Path, label: str, body: str
) -> None:
    """Refused, never coerced: guessing what an operator meant hides a security typo."""
    root, config = _project(tmp_path, body)

    with pytest.raises(ProjectConfigError) as raised:
        read_review_repositories(root, config)

    assert raised.value.remedy, label


def test_an_entry_the_pattern_refuses_fails_the_whole_list_rather_than_being_skipped(
    tmp_path: pathlib.Path,
) -> None:
    """Filtering would leave an operator reading a line in their own file that does nothing.

    The direction matters: skipping the bad entry and matching the good one is the
    *permissive* failure, and it is silent. Refusing the list names the entry and
    the cure.
    """
    root, config = _project(tmp_path, _listing("acme/order-service", "../.."))

    with pytest.raises(ProjectConfigError) as raised:
        allowlisted_repository(root, config, "acme/order-service")

    assert "../.." in str(raised.value)
    assert raised.value.remedy


def test_a_review_block_reached_through_providers_names_its_own_path(
    tmp_path: pathlib.Path,
) -> None:
    """A message naming `review` sends the reader to the wrong line; there are several."""
    root, config = _project(tmp_path, "apiVersion: theurian.dev/v1\nproviders:\n  review: 7\n")

    with pytest.raises(ProjectConfigError) as raised:
        read_review_repositories(root, config)

    assert "providers.review" in str(raised.value)
