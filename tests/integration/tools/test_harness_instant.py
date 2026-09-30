"""The eval report is a function of the instant the harness injects (ADR-0036, ADR-0003).

On 2026-09-30 the committed baseline went RED with no code or corpus change:
each hit's ``freshness.ageDays`` was computed against the wall clock and reached
10, ``retrieval.usedTokens`` prices each hit on its serialised length, and
``equality`` ``differingFields`` lists gained or lost it. ``run.main`` now
composes one clock into the build and the searches.
"""

from __future__ import annotations

import json
import sys
import tempfile
from collections.abc import Callable, Iterator
from contextlib import ExitStack
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.eval]

REPO_ROOT = Path(__file__).resolve().parents[3]
_HARNESS_DIR = REPO_ROOT / "tools" / "eval"
if str(_HARNESS_DIR) not in sys.path:
    sys.path.insert(0, str(_HARNESS_DIR))

import corpus as harness_corpus  # noqa: E402
import corpus_build as harness_build  # noqa: E402
import run as harness_run  # noqa: E402

from theurian.application.project_service import ProjectPaths, read_active_state  # noqa: E402
from theurian.domain.context import RequestContext  # noqa: E402
from theurian.domain.identifiers import ProjectId  # noqa: E402
from theurian.domain.ports.determinism import Clock  # noqa: E402
from theurian.infrastructure.determinism import SystemClock  # noqa: E402
from theurian.infrastructure.sqlite.store import SqliteCanonicalStore  # noqa: E402
from theurian.security.yaml_loading import load_yaml_mapping  # noqa: E402

CORPUS = REPO_ROOT / "tests" / "fixtures" / "eval"
SMOKE_CORPUS = REPO_ROOT / "tests" / "fixtures" / "eval-smoke"

UNDER_TEN_DAYS: Final = harness_run.PINNED_NOW
TEN_DAYS: Final = datetime(2026, 9, 30, 0, 0, 38, tzinfo=UTC)

#: What ``SystemClock`` answers during a run: one before every injected instant
#: and one after, so a build stamping ``validFrom`` from it, or a search computing
#: ``freshness`` from it, changes hit lengths between the two.
SYSTEM_CLOCK_BEFORE: Final = datetime(2000, 1, 1, tzinfo=UTC)
SYSTEM_CLOCK_AFTER: Final = datetime(2099, 1, 1, tzinfo=UTC)

#: Neither ``PINNED_NOW``'s day nor any day from 2026-09-30, when this was written,
#: so a search reading either answers another ``ageDays``.
BUILT_AND_SEARCHED_AT: Final = datetime(2026, 9, 21, 12, 0, tzinfo=UTC)

_DIFFERING_FIELDS_PARENTS: Final = frozenset(
    {("equality", "queries"), ("raptor", "equality", "queries")}
)
_EQUALITY_LABELS: Final = frozenset({"atLimit", "atEqualityLimit"})


def _report(instant: datetime, system_clock: datetime) -> bytes:
    with (
        pytest.MonkeyPatch.context() as patch,
        tempfile.TemporaryDirectory(prefix="theurian-eval-instant-") as out,
    ):
        patch.setattr(SystemClock, "now", lambda _self: system_clock)
        assert harness_run.main(["--corpus", str(CORPUS), "--out", out], instant=instant) == 0
        return (Path(out) / "report.json").read_bytes()


def _differences(
    before: Any, after: Any, path: tuple[str | int, ...] = ()
) -> Iterator[tuple[tuple[str | int, ...], Any, Any]]:
    """Every leaf where two parsed reports disagree; a ``differingFields`` list is one leaf."""
    if isinstance(before, dict) and isinstance(after, dict) and before.keys() == after.keys():
        for key in before:
            yield from _differences(before[key], after[key], (*path, key))
    elif (
        isinstance(before, list)
        and isinstance(after, list)
        and len(before) == len(after)
        and path[-1:] != ("differingFields",)
    ):
        for index, (left, right) in enumerate(zip(before, after, strict=True)):
            yield from _differences(left, right, (*path, index))
    elif type(before) is not type(after) or before != after:
        yield path, before, after


def _is_a_differing_fields_list(path: tuple[str | int, ...]) -> bool:
    return (
        path[:-3] in _DIFFERING_FIELDS_PARENTS
        and path[-2] in _EQUALITY_LABELS
        and path[-1] == "differingFields"
    )


def test_the_two_instants_put_every_fixture_revision_either_side_of_ten_days() -> None:
    """The pin below crosses every hit's ``ageDays`` from one digit to two."""
    loaded = harness_corpus.load_corpus(CORPUS)
    created = [
        datetime.fromisoformat(
            load_yaml_mapping((CORPUS / "migrations" / entry.file).read_text(encoding="utf-8"))[
                "createdAt"
            ]
        )
        for entry in loaded.manifest.migrations
    ]

    assert created
    assert all(0 <= (UNDER_TEN_DAYS - at).days <= 9 for at in created)
    assert all(10 <= (TEN_DAYS - at).days <= 99 for at in created)


def test_the_report_is_a_function_of_the_injected_instant_not_of_system_clock() -> None:
    """RED three ways: the two runs at one instant differ, so a seam reads
    ``SystemClock`` or the run is not deterministic; the runs either side of ten
    days agree in the base arm or under ``raptor``, so the instant no longer
    reaches that arm's searches; or they differ beyond ``usedTokens``, so the
    instant reaches something else."""
    first = _report(UNDER_TEN_DAYS, SYSTEM_CLOCK_BEFORE)
    crossed = _report(TEN_DAYS, SYSTEM_CLOCK_BEFORE)
    again = _report(UNDER_TEN_DAYS, SYSTEM_CLOCK_AFTER)

    assert again == first
    differences = list(_differences(json.loads(first), json.loads(crossed)))
    arms = {"raptor" if path[0] == "raptor" else "base" for path, _, _ in differences}
    assert arms == {"base", "raptor"}, f"the injected instant moves only {sorted(arms)}"
    assert [path for path, _, _ in differences if not _is_a_differing_fields_list(path)] == []
    assert all(
        set(before) ^ set(after) == {"retrieval.usedTokens"} for _, before, after in differences
    ), differences


def _stamped_valid_from(project: harness_build.BuiltProject) -> set[datetime]:
    """Every item's and revision's ``validFrom`` in ``project``'s built canonical store."""
    paths = ProjectPaths.of(project.root)
    active = read_active_state(paths)
    assert active is not None, project.root
    context = RequestContext(project_id=ProjectId(project.project_id))
    with SqliteCanonicalStore(paths.database_for(active.state_hash)) as store:
        items = store.list_items(context)
        revisions = [
            revision for item in items for revision in store.list_revisions(context, item.item_id)
        ]
    assert items and revisions, project.root
    return {item.validity.valid_from for item in items} | {
        revision.validity.valid_from for revision in revisions
    }


def test_the_harness_search_answers_on_the_clock_its_build_was_given() -> None:
    """Held below ``report.json``, which cannot see this: a search pinned to
    ``PINNED_NOW`` over a build at the ten-day instant reproduces correct wiring's
    report byte for byte, because ``isWithinValidity`` turning false adds the one
    character ``ageDays`` going 4 -> 10 adds. So both fields, on every hit -- and
    the build's ``validFrom`` stamps read from its store, because
    ``isWithinValidity`` holds them only at or before the search instant."""
    loaded = harness_corpus.load_corpus(SMOKE_CORPUS)
    with (
        tempfile.TemporaryDirectory(prefix="theurian-eval-wiring-") as workspace,
        ExitStack() as sessions,
    ):
        built = harness_build.build_both(
            loaded, Path(workspace), clock=harness_run.PinnedClock(BUILT_AND_SEARCHED_AT)
        )
        calls = harness_run._open_sessions(sessions, built)
        freshness = [
            hit["freshness"]
            for name, project in built.projects.items()
            for hit in calls[name](
                "knowledge.search", {"projectId": project.project_id, "query": "policy"}
            )["results"]
        ]
        stamped = {name: _stamped_valid_from(project) for name, project in built.projects.items()}

    assert stamped == {name: {BUILT_AND_SEARCHED_AT} for name in built.projects}
    assert freshness
    assert [(each["ageDays"], each["isWithinValidity"]) for each in freshness] == [
        ((BUILT_AND_SEARCHED_AT - datetime.fromisoformat(each["revisionCreatedAt"])).days, True)
        for each in freshness
    ]


def test_every_build_the_harness_makes_is_stamped_with_the_injected_instant(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Both pairs ``run.main`` builds, raptor and not, read through ``run.build_both``
    as ``main`` calls it. Each pair's searches see its stamps only through
    ``isWithinValidity``, true for a build at any instant up to the search's."""
    stamped: dict[tuple[bool, str], set[datetime]] = {}
    # `main`'s own binding; `run` does not re-export it to a type checker.
    build_both: Callable[..., harness_build.BuildResult] = vars(harness_run)["build_both"]

    def reading_the_stamps(
        loaded: harness_corpus.Corpus, workspace: Path, *, clock: Clock, raptor: bool = False
    ) -> harness_build.BuildResult:
        built = build_both(loaded, workspace, clock=clock, raptor=raptor)
        for name, project in built.projects.items():
            stamped[raptor, name] = _stamped_valid_from(project)
        return built

    monkeypatch.setattr(harness_run, "build_both", reading_the_stamps)
    with tempfile.TemporaryDirectory(prefix="theurian-eval-stamps-") as out:
        code = harness_run.main(
            ["--corpus", str(SMOKE_CORPUS), "--out", out], instant=BUILT_AND_SEARCHED_AT
        )

    assert code == 0
    assert stamped == {
        (raptor, name): {BUILT_AND_SEARCHED_AT}
        for raptor in (False, True)
        for name in harness_build.PLANES
    }
