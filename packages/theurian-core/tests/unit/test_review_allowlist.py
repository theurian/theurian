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
import re
from typing import Final

import pytest

from theurian.domain.errors import ProjectConfigError
from theurian.domain.review_ingest import (
    MAX_REFUSAL_SUMMARY_CHARS,
    RefusalGrade,
    ReviewIngestRefusedError,
)
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

#: How long a repository name may be before the reader refuses it without running
#: the pattern, **written out here and never imported**.
#:
#: A test whose input is ``"a" * MAX_REPOSITORY_CHARS`` cannot fail: the string
#: grows with the constant, so it is over the bound whatever the bound is. The
#: number is restated here and both boundary tests are built from *this* one,
#: which is the shape ``test_gh_child_environment.py`` uses for clause 4(i)'s
#: mapping.
RECORDED_REPOSITORY_CHARS: Final = 200

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


@pytest.mark.parametrize(
    "name",
    ("acme/日本語", "acme/ордер", "acmé/order", "acme/ordér"),
    ids=("cjk", "cyrillic", "accented-owner", "accented-name"),
)
def test_a_non_ascii_name_is_refused_as_the_schema_refuses_it(name: str) -> None:
    """The bytes of the pattern are the schema's; so must its **dialect** be.

    JSON Schema's ``pattern`` is ECMA-262, where ``\\w`` is ``[A-Za-z0-9_]``.
    Python's is Unicode-aware by default, so the same pattern string admitted
    every script's letters here while the published contract admitted none of
    them -- the reader wider than the schema, which is the direction that lets a
    name through. ``re.ASCII`` is what closes it, and this is what fails without
    the flag.
    """
    assert not is_well_formed(name), (
        f"{name!r} satisfied the allowlist pattern. The schema publishes an "
        "ECMA-262 pattern whose `\\w` is ASCII-only, and a reader that is wider "
        "than the contract it claims to enforce enforces something else."
    )


def test_the_name_length_bound_this_file_drives_is_the_one_the_reader_enforces() -> None:
    """The restated number and the enforced one are two things, so they are compared.

    :data:`RECORDED_REPOSITORY_CHARS` is what the two boundary tests below build
    their names from. If the reader's own constant moved and this one did not,
    both would be driving a boundary that is no longer the boundary -- passing,
    and about the wrong number.
    """
    assert MAX_REPOSITORY_CHARS == RECORDED_REPOSITORY_CHARS, (
        f"the reader bounds a name at {MAX_REPOSITORY_CHARS} characters and this file "
        f"drives {RECORDED_REPOSITORY_CHARS}. A bound is a recorded number: move the "
        f"prose that names it in the same change, and say what the new one costs."
    )


def test_a_name_at_the_recorded_bound_is_still_matched() -> None:
    """The positive control on the bound: at the boundary the pattern still runs.

    Without it the test below proves nothing -- a reader that refused *every*
    name would pass it, and so would a bound of zero. This is the input that
    makes the bound bite from the other side: one character shorter than the
    refusal case, and accepted.
    """
    name = "a" * (RECORDED_REPOSITORY_CHARS - len("/b")) + "/b"

    assert len(name) == RECORDED_REPOSITORY_CHARS
    assert is_well_formed(name)


def test_a_name_longer_than_the_recorded_bound_is_refused_before_the_pattern_runs() -> None:
    """A caller cannot spend regex time on an unbounded string it chose.

    **The name is otherwise well-formed**, one character past the bound, so a
    ``False`` here can only have come from the length check: the pattern's own
    verdict on this string is a match. That is how "before the pattern runs" is
    observed rather than asserted.

    **The length comes from this file rather than from the constant.** The
    earlier form was ``"a" * MAX_REPOSITORY_CHARS + "/b"``, which grows with
    whatever the constant is: lift the bound to 200,000,000 and the name grows to
    200 MB, stays over the bound, and the test passes for every value the
    constant can take -- while allocating the string that proves it.
    """
    name = "a" * (RECORDED_REPOSITORY_CHARS - len("/b") + 1) + "/b"

    assert len(name) == RECORDED_REPOSITORY_CHARS + 1
    assert not is_well_formed(name)


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


#: Characters whose ``repr`` is several times their own length.
#:
#: The point of each is the *expansion factor*, not the character: a value cut to
#: :data:`~theurian.domain.review_ingest.MAX_SUMMARY_ECHO_CHARS` and quoted
#: afterwards comes back that many times longer, which is what pushed this
#: refusal's summary past the type's cut. U+E0001 is the widest at ten characters
#: (``\U000e0001``); the surrogate is here because it is a value no encoder
#: accepts and a caller can still send it.
_EXPANDING: Final[tuple[tuple[str, str], ...]] = (
    ("a right-to-left override", "\u202e"),
    ("a left-to-right mark", "\u200e"),
    ("a lone surrogate", "\ud800"),
    ("a line separator", "\u2028"),
    ("a TAG character", "\U000e0001"),
    ("the largest code point", "\U0010ffff"),
)


@pytest.mark.parametrize("character", [c for _, c in _EXPANDING], ids=[n for n, _ in _EXPANDING])
def test_a_request_that_quotes_long_keeps_the_whole_refusal_sentence(
    tmp_path: pathlib.Path, character: str
) -> None:
    """The echo is quoted, so the bound has to be on the quoting, not on the value.

    ``repr`` is not length-preserving. This refusal used to cut the raw request at
    the repository-name bound and quote it afterwards, so a request made of
    characters that expand under quoting came back several times the bound, the
    summary ran past ``MAX_REFUSAL_SUMMARY_CHARS``, and the **type's** cut took
    the end of the sentence. What lives at that end is not decoration: it is the
    configuration key an operator has to edit and the assurance that no process
    was started. A refusal that loses both has kept its grade and thrown away
    everything the reader acts on.

    Driven through :func:`allowlisted_repository` rather than through the
    renderer, because the claim is about the published envelope: the summary a
    caller receives, and the remedy beside it.
    """
    root, config = _project(tmp_path, _listing("acme/order-service"))

    with pytest.raises(ReviewIngestRefusedError) as raised:
        allowlisted_repository(root, config, character * 100_000)

    summary = raised.value.envelope.summary
    assert raised.value.grade is RefusalGrade.REPOSITORY_NOT_ALLOWLISTED
    assert len(summary) <= MAX_REFUSAL_SUMMARY_CHARS, (
        f"the summary is {len(summary)} characters, so the type had to cut it. The "
        f"value-side bound is what keeps the sentence, and it only does that when "
        f"it is applied to the rendering."
    )
    assert "`providers.review.repositories`" in summary, (
        "the summary no longer names the configuration key to edit, which is the "
        "one thing an operator does about this refusal"
    )
    assert summary.endswith("so no process was started."), (
        f"the refusal lost its own tail to the type's cut; it ends {summary[-48:]!r}"
    )
    assert raised.value.remedy


def test_a_request_that_quotes_long_still_shows_the_caller_what_it_sent(
    tmp_path: pathlib.Path,
) -> None:
    """Cutting the echo may not turn the message into one that names nothing.

    The reason this refusal echoes at all is so an operator can see the typo they
    made. A bound that kept the sentence by dropping the value entirely would
    satisfy the test above and be useless, so this asserts the other direction:
    the quoted request is still there, escaped, and it says how much was cut.
    """
    root, config = _project(tmp_path, _listing("acme/order-service"))

    with pytest.raises(ReviewIngestRefusedError) as raised:
        allowlisted_repository(root, config, "\u202e" * 100_000)

    summary = raised.value.envelope.summary
    assert "\\u202e" in summary, "the request is not shown back in its escaped form"
    assert "\u202e" not in summary, (
        "a raw right-to-left override reached the published sentence, where it "
        "reorders the text around it in whatever prints the refusal"
    )
    assert re.search(r"cut from \d+ characters", summary), (
        "the echo was shortened without saying so"
    )


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
