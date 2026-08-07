"""Which GitHub release `release-core.yml` makes public, and which it refuses to.

Why this file exists
--------------------
`release-core.yml`'s publication path has no other automated check: no test
anywhere else reads a workflow file, and the repository runs no actionlint,
zizmor or yamllint. Its steps execute once per tag push — in production, on a
release nobody can un-publish — and the `workflow_dispatch` rehearsal skips all
three publication jobs, so a green rehearsal is evidence about the build and
nothing downstream of it.

They have now run exactly once, for real. `theurian 0.1.0.dev0` is on PyPI and
`Theurian Core 0.1.0.dev0` is the GitHub release, so this file is no longer
written ahead of a path nobody has taken — it is written ahead of the *second*
time, which is the release where a re-run, a hand-published draft or a hotfix
can put a second release on one tag. The first release had a clean tag and could
not have shown the defect these steps guard against.

The defect they were rewritten to fix is not a wrong JSON field. It is that **a
tag does not name one release**: `gh release create --draft` accepts a tag that
already carries a release (its duplicate check sits behind
`hasAssets && !opts.Draft`), and `gh release edit <tag>` then resolves through a
race measured to return the *oldest* draft. A re-run of a partly-failed release
published the incomplete draft while the complete one stayed private, and every
job reported success.

What that means for a test
--------------------------
Asserting the *spelling* of the publishing command is not enough, and this is
the trap that has already caught two attempts at checking this workflow. On a
single-release happy path, `gh release edit <tag>` and
`gh api -X PATCH .../releases/<id>` reach the same object: a stub that records
argv sees two different commands, a repository sees one outcome, and only the
outcome is what shipped. So these tests drive the shipped steps against a
**stateful model of the releases API** (`github_api_model.py`) and assert which
release ended up public.

The load-bearing assertion is one sentence: **a run that reports success has
made public the release it created, and never another.**
`test_a_run_that_succeeds_publishes_its_own_release_however_the_tag_race_lands`
is that sentence. It needs a second release on the tag by the time publication
happens — the window is real, because `publish-pypi` waits for a human reviewer
between the two jobs — and without that fixture the assertion holds for by-tag
and by-id alike and proves nothing at all.

How the steps get here
----------------------
Extracted from the workflow YAML, never retyped, and run under real `bash`. The
jq filters, `set -euo pipefail` and the control flow are the shipped text.

They are located **structurally, not by step name**: the drafting job's first
`run:` step is the guard, and the step carrying `id: draft` is the one the job's
`outputs` reads. Names are prose and were edited during #59's review rounds;
these keys are load-bearing and cannot drift silently. The names are asserted
separately, so a rename is reported rather than fatal.

Each step's environment is built from **its own `env:` block in the YAML**.
Deleting `TAG:` from a step therefore leaves `${TAG}` unbound under `set -u` and
the step fails here, rather than passing because the test supplied a variable
the workflow no longer declares.

Not covered
-----------
* `--paginate` across more than one page. The model serves one list per call.
* How long anything takes. Nothing here measures wall clock, and nothing in
  the steps waits any more -- the retry loop that used to went away with the
  eventually-consistent read it existed for.
* The re-run rules beyond what the guard's own refusal says. Which jobs to
  re-run in each state is prose in release.md §5; only the sentence the guard
  prints is executable, and only that is pinned here.
* `gh`, `curl` and the REST API themselves. The model encodes rules read out of
  `cli/cli` and measurements taken during #59's review; nothing here
  re-establishes them, and a test that leans on one says so.
* What `releases/latest` ends up pointing at. `make_latest=legacy` is pinned as
  a *request*, in `test_publication_asks_github_to_choose_latest_by_version`,
  because the consequence is computed by GitHub from a rule this file does not
  implement. That test is the one shape assertion here, and it says why.
* `build`'s own steps. Only the three that publish are extracted; the
  `sha256sum` glob, the SBOM and the wheel install-back run in a job that a
  `workflow_dispatch` rehearsal does exercise, so they are not the blind spot
  this file exists for.
* Rehearsal run `31113740502` was green and verified none of this: the three
  publication jobs are `if: github.event_name == 'push'`, so a
  `workflow_dispatch` skips every step under test.

Requirements: T-16 (publish a checksum record that survives a failed run),
ADR-0001 (the `core-v*` tag namespace). Argued in docs/contributing/release.md §5.
"""

from __future__ import annotations

import contextlib
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, cast

import pytest
import yaml

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "release-core.yml"
#: The workflow that runs this file on a pull request. `release-core.yml` runs
#: it too, in `quality`, which is why a tag push cannot publish past a failure
#: here -- both are asserted below.
CORE_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "core.yml"
API_MODEL = pathlib.Path(__file__).parent / "github_api_model.py"

#: Real `bash` and real `jq`: the filters under test are the workflow's own
#: text, and reimplementing them in Python would test the reimplementation.
BASH = shutil.which("bash")
JQ = shutil.which("jq")

#: A contributor without jq gets a skip. **CI does not.** A silent skip on the
#: runner would leave the publication path with no check while reporting a green
#: suite, which is the failure this file exists to prevent.
_ON_CI = os.environ.get("CI") == "true"

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        (BASH is None or JQ is None) and not _ON_CI,
        reason="the shipped step text is bash calling jq; install jq to run these locally",
    ),
]

REPOSITORY = "theurian/theurian"
TAG = "core-v0.1.0.dev0"
VERSION = "0.1.0.dev0"

#: A `published_at` for releases a *fixture* puts in the repository. A literal,
#: never the clock: the drafting guard prints this field, so a test that reads
#: the printout must not depend on the second it ran in. The model has its own
#: constant for releases it creates; nothing needs the two to be equal, only
#: that both are fixed and non-null.
PUBLISHED_AT = "2026-08-06T12:38:09Z"

#: What `build` uploads. `SHA256SUMS` is the one `publish-release` checks for.
ARTIFACTS = (
    "SHA256SUMS",
    f"theurian-{VERSION}-py3-none-any.whl",
    f"theurian-{VERSION}.tar.gz",
    f"theurian-{VERSION}.cdx.json",
)

#: A PEP 440 local version, which is what makes `+` reach the upload URL. The
#: workflow percent-encodes the `?name=` value; without that the `+` arrives as
#: a space and the asset is stored under a different name than it was sent as.
LOCAL_VERSION_WHEEL = "theurian-0.2.0+local-py3-none-any.whl"


# -- locating the steps -----------------------------------------------------


def _yaml(path: pathlib.Path) -> dict[Any, Any]:
    return cast(dict[Any, Any], yaml.safe_load(path.read_text(encoding="utf-8")))


def _triggers(path: pathlib.Path) -> dict[str, Any]:
    """A workflow's `on:` block.

    YAML 1.1 reads a bare `on:` key as the boolean true, which is how PyYAML
    hands it back. Written once here so that `document[True]` does not appear at
    a call site, where it reads like a mistake.
    """
    return cast(dict[str, Any], _yaml(path)[True])


def _workflow() -> dict[str, Any]:
    return cast(dict[str, Any], _yaml(WORKFLOW))


def _job(name: str) -> dict[str, Any]:
    return cast(dict[str, Any], _workflow()["jobs"][name])


@dataclass(frozen=True)
class Step:
    """One `run:` step as shipped: its script and the variables it declares."""

    name: str
    script: str
    declared_env: tuple[str, ...]


def _as_step(raw: Mapping[str, Any]) -> Step:
    return Step(
        name=str(raw.get("name", "")),
        script=str(raw["run"]),
        declared_env=tuple(raw.get("env", {})),
    )


def _run_steps(job: str) -> tuple[Step, ...]:
    return tuple(_as_step(step) for step in _job(job)["steps"] if "run" in step)


def _step_with_id(job: str, step_id: str) -> Step:
    for step in _job(job)["steps"]:
        if step.get("id") == step_id:
            return _as_step(step)
    raise AssertionError(f"job {job!r} has no step with id {step_id!r}")


def _load_steps() -> tuple[Step, Step, Step]:
    drafting = _run_steps("draft-release")
    if len(drafting) != 2:
        raise AssertionError(
            f"draft-release has {len(drafting)} run steps; this file models a guard "
            "followed by the step that creates the release"
        )
    publishing = _run_steps("publish-release")
    if len(publishing) != 1:
        raise AssertionError(f"publish-release has {len(publishing)} run steps, not 1")
    return drafting[0], _step_with_id("draft-release", "draft"), publishing[0]


GUARD, CREATE, PUBLISH = _load_steps()

#: No upload failures. Named so the default reads as a choice.
MAPPING_EMPTY: Mapping[str, str] = {}

#: A value for every variable a step declares. `_run_step` raises on a key
#: missing here, so a new `env:` entry surfaces as a failure rather than as an
#: unset variable no test exercises.
_ENV_VALUES = {
    "GH_TOKEN": "not-a-real-token",
    "TAG": TAG,
    "VERSION": VERSION,
    "RELEASE_ID": "",
}


# -- the world the steps act on ---------------------------------------------


def _release(
    release_id: int,
    *,
    tag: str = TAG,
    draft: bool = True,
    assets: Sequence[str] = ARTIFACTS,
    published_at: str | None = None,
) -> dict[str, Any]:
    """One release already in the repository when a step runs.

    ``published_at`` defaults to ``None`` for a draft and to the model's fixed
    timestamp for a public release, which is what GitHub reports. Passing it
    explicitly builds the third state: a *draft that used to be public*, reached
    by deleting and re-pushing the tag.
    """
    return {
        "id": release_id,
        "tag_name": tag,
        "draft": draft,
        "published_at": published_at if draft else (published_at or PUBLISHED_AT),
        "assets": [{"name": name} for name in assets],
    }


class Repository:
    """The releases a run acts on, and what it did to them."""

    def __init__(self, root: pathlib.Path, releases: Sequence[dict[str, Any]]) -> None:
        self._root = root
        self._path = root / "releases.json"
        self._write(
            {
                "next_id": 1000,
                "releases": list(releases),
                "tags": [TAG],
                "list_lag": 0,
                "calls": [],
                "patches": [],
            }
        )

    def _write(self, state: Mapping[str, Any]) -> None:
        self._path.write_text(json.dumps(dict(state), indent=2), encoding="utf-8")

    @property
    def path(self) -> pathlib.Path:
        return self._path

    @property
    def workdir(self) -> pathlib.Path:
        """Where `draft-release` finds the downloaded artifacts."""
        return self._root

    @property
    def state(self) -> dict[str, Any]:
        return cast(dict[str, Any], json.loads(self._path.read_text(encoding="utf-8")))

    @property
    def releases(self) -> tuple[dict[str, Any], ...]:
        return tuple(self.state["releases"])

    @property
    def published(self) -> tuple[dict[str, Any], ...]:
        return tuple(r for r in self.releases if not r["draft"])

    @property
    def calls(self) -> tuple[tuple[str, ...], ...]:
        return tuple(tuple(call) for call in self.state["calls"])

    @property
    def patches(self) -> tuple[Mapping[str, Any], ...]:
        """Every `PATCH /releases/{id}` body the API received, allowed or not."""
        return tuple(self.state.get("patches", []))

    @property
    def drafts_by_id(self) -> Mapping[int, bool]:
        """`{id: is_a_draft}`, for asserting that a run moved only its own."""
        return {int(release["id"]): bool(release["draft"]) for release in self.releases}

    @property
    def writes(self) -> tuple[tuple[str, ...], ...]:
        """Every call that could change a release. A refusal must issue none.

        The ledger records the tool as argv[0] -- `("gh", "release", "edit", …)`,
        not `("release", "edit", …)`. An earlier version of this predicate
        compared `call[:2]` against `("release", "edit")`, which no recorded call
        can equal, so the two subcommands that mutate without an explicit `-X`
        were invisible to it and `assert repository.writes == ()` could not fail
        for them. That is the shape of defect this whole file exists to catch,
        arriving in the harness rather than in the workflow.
        """
        mutating = ("-X", "--method")
        return tuple(
            call
            for call in self.calls
            if call[1:3] in {("release", "create"), ("release", "edit"), ("release", "delete")}
            or any(
                flag in mutating and call[index + 1] != "GET"
                for index, flag in enumerate(call[:-1])
            )
        )

    def by_id(self, release_id: str) -> dict[str, Any] | None:
        return next((r for r in self.releases if str(r["id"]) == release_id), None)

    def record(self, *argv: str) -> None:
        """Append to the ledger as the model would, without running anything.

        Only :func:`test_the_write_ledger_counts_the_command_the_refusals_forbid`
        uses this. It exists because `writes` is the assertion that separates
        "refused" from "did it, then complained", and it was blind once.
        """
        self._write({**self.state, "calls": [*self.state["calls"], list(argv)]})

    def lag_the_list(self, calls: int) -> None:
        """Hide the newest release from that many `GET /releases` calls."""
        self._write({**self.state, "list_lag": calls})

    def add(self, release: Mapping[str, Any]) -> None:
        self._write({**self.state, "releases": [*self.releases, dict(release)]})

    def remove_tag(self, tag: str) -> None:
        self._write({**self.state, "tags": [t for t in self.state["tags"] if t != tag]})

    def replace_releases(self, releases: Sequence[Mapping[str, Any]]) -> None:
        self._write({**self.state, "releases": [dict(r) for r in releases]})


@dataclass(frozen=True)
class StepRun:
    exit_code: int
    log: str
    outputs: Mapping[str, str]

    @property
    def succeeded(self) -> bool:
        return self.exit_code == 0


def _parse_outputs(text: str) -> Mapping[str, str]:
    partitioned = (line.partition("=") for line in text.splitlines())
    return {key: value for key, separator, value in partitioned if separator}


def _run_step(
    step: Step,
    repository: Repository,
    *,
    env: Mapping[str, str] = _ENV_VALUES,
    uploads: Mapping[str, str] = MAPPING_EMPTY,
    tag_race: str = "graphql",
) -> StepRun:
    """Run the shipped step text against the modelled API."""
    assert BASH is not None and JQ is not None, (
        "bash and jq are required to run the shipped step text; on CI their "
        "absence is a failure rather than a skip, because a skipped run here "
        "leaves the publication path with no automated check at all"
    )

    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        stubs, home = root / "stubs", root / "home"
        stubs.mkdir()
        home.mkdir()
        # `gh` and `curl` both reach the same modelled repository, because
        # the step creates the release through one and attaches its assets
        # through the other -- they have to agree about what exists.
        for tool in ("gh", "curl"):
            (stubs / tool).write_text(
                f'#!/bin/sh\nexec "{sys.executable}" "{API_MODEL}" {tool} "$@"\n',
                encoding="utf-8",
            )
        for tool in ("gh", "curl"):
            (stubs / tool).chmod(0o755)

        outputs = root / "github-output.txt"
        outputs.touch()
        path = f"{stubs}{os.pathsep}{os.environ['PATH']}"
        full_env = {
            "PATH": path,
            "HOME": str(home),
            # Nothing here may reach the real GitHub API. `gh` on PATH is the
            # model and that is asserted below; these make a bug in the
            # assertion harmless rather than merely unlikely.
            "GH_CONFIG_DIR": str(root / "gh-config"),
            "GH_HOST": "github.invalid",
            "GH_STATE": str(repository.path),
            "GH_RACE": tag_race,
            # Runner-provided, present in every workflow run.
            "GITHUB_REPOSITORY": REPOSITORY,
            "GITHUB_SERVER_URL": "https://github.com",
            "GITHUB_REF_NAME": TAG,
            "GITHUB_OUTPUT": str(outputs),
            "RUNNER_TEMP": str(root),
            # Declared by the step's own `env:` block in the YAML.
            **{key: env[key] for key in step.declared_env},
        }
        full_env.update(uploads)
        for tool in ("gh", "curl"):
            assert shutil.which(tool, path=path) == str(stubs / tool), (
                f"the model must shadow any real {tool}; these tests must never reach the network"
            )

        script = root / "step.sh"
        script.write_text(step.script, encoding="utf-8")
        # `bash -e {0}` is what the runner uses for a `run:` step with no
        # explicit `shell:`.
        completed = subprocess.run(  # noqa: S603 - fixed argv, no shell
            [BASH, "-e", str(script)],
            cwd=repository.workdir,
            env=full_env,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        return StepRun(
            exit_code=completed.returncode,
            log=completed.stdout + completed.stderr,
            outputs=_parse_outputs(outputs.read_text(encoding="utf-8")),
        )


@dataclass(frozen=True)
class JobRun:
    """What `draft-release` did: both its steps, and the id it bound."""

    guard: StepRun
    create: StepRun | None

    @property
    def succeeded(self) -> bool:
        return self.guard.succeeded and self.create is not None and self.create.succeeded

    @property
    def release_id(self) -> str:
        return "" if self.create is None else self.create.outputs.get("release_id", "")

    @property
    def log(self) -> str:
        return self.guard.log + ("" if self.create is None else self.create.log)


@contextlib.contextmanager
def _artifacts(names: Sequence[str]) -> Iterator[pathlib.Path]:
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        dist = root / "artifacts" / "dist"
        dist.mkdir(parents=True)
        (root / "artifacts" / "release-notes.md").write_text("### Added\n\n- A thing.\n")
        for name in names:
            (dist / name).write_text(f"contents of {name}\n", encoding="utf-8")
        yield root


@pytest.fixture
def workdir() -> Iterator[pathlib.Path]:
    """`draft-release` runs with the downloaded artifacts in the working copy."""
    with _artifacts(ARTIFACTS) as root:
        yield root


def _repository(workdir: pathlib.Path, *releases: dict[str, Any]) -> Repository:
    return Repository(workdir, releases)


def _draft_release(repository: Repository, *, uploads: Mapping[str, str] = MAPPING_EMPTY) -> JobRun:
    """Run the drafting job: the guard, then — if it allows — the creation."""
    guard = _run_step(GUARD, repository)
    if not guard.succeeded:
        return JobRun(guard=guard, create=None)
    return JobRun(guard=guard, create=_run_step(CREATE, repository, uploads=uploads))


def _publish_release(
    repository: Repository, release_id: str, *, tag_race: str = "graphql"
) -> StepRun:
    return _run_step(
        PUBLISH, repository, env={**_ENV_VALUES, "RELEASE_ID": release_id}, tag_race=tag_race
    )


# -- the invariant ----------------------------------------------------------


@pytest.mark.parametrize("tag_race", ["graphql", "rest"])
@pytest.mark.parametrize("rival_is_a_draft", [True, False], ids=["older draft", "published"])
def test_a_run_that_succeeds_publishes_its_own_release_however_the_tag_race_lands(
    workdir: pathlib.Path, tag_race: str, rival_is_a_draft: bool
) -> None:
    """The sentence this whole file exists to hold.

    The tag is clean when the drafting job runs, and a rival release appears on
    it afterwards — the window is real, because `publish-pypi` sits between the
    two jobs waiting for a human reviewer. So by the time the release is
    published, the tag names two objects and `FetchRelease` races REST against
    GraphQL to pick one. Both rivals and both race outcomes are run.

    Addressing the numeric id makes the choice not arise. Addressing the tag
    makes this run report success while the release it built stays private and
    something else is the public record for the version. That is the defect, and
    it is invisible without a second release on the tag: with one, the two
    spellings reach the same object and every assertion passes either way.

    The rival is given a lower id than this run's release, because the race was
    measured resolving to the *oldest* draft.

    Restoring `gh release edit "$GITHUB_REF_NAME" --draft=false` turns three of
    these four cases RED. `[published-graphql]` stays green, and that is a
    property of the case rather than a hole: the lookup lands on this run's
    draft, the tag already carries a published release, and the REST API answers
    422 to *either* spelling. Both the shipped command and the mutation fail
    there, identically, so nothing separates them — which is why the fixture
    varies the rival and the race instead of picking one.

    The sentence has two halves and both are asserted, because either alone is
    weak. "Publishes its own" is vacuous on a run that failed — a workflow that
    always exited 1 would satisfy it in all four cases. "Never another" is
    vacuous on a run that published nothing. Together they say: whatever the
    step's exit code, the only release whose visibility changed is this run's,
    and it changed only if the step claimed success.
    """
    repository = _repository(workdir)
    drafted = _draft_release(repository)
    assert drafted.succeeded, drafted.log
    repository.add(_release(990, draft=rival_is_a_draft, assets=["SHA256SUMS"]))
    before = repository.drafts_by_id

    published = _publish_release(repository, drafted.release_id, tag_race=tag_race)

    mine = int(drafted.release_id)
    after = repository.drafts_by_id
    assert not published.succeeded or after[mine] is False, (
        f"the run reported success but release {mine} is still a draft; "
        f"public on this tag: {[r['id'] for r in repository.published]}"
    )
    moved = {rid for rid, draft in after.items() if before[rid] != draft}
    assert moved <= {mine}, (
        f"the run changed the visibility of {sorted(moved - {mine})}, which it did not create"
    )


def test_the_release_this_run_creates_starts_private(workdir: pathlib.Path) -> None:
    """T-16's ordering rests on this. A release that is public the moment it is
    created announces the version before `publish-pypi` has uploaded anything —
    the announcement arriving before the artifact, which is the inversion the job
    order exists to prevent.
    """
    repository = _repository(workdir)

    drafted = _draft_release(repository)

    assert drafted.succeeded, drafted.log
    assert repository.by_id(drafted.release_id) == {
        **cast(dict[str, Any], repository.by_id(drafted.release_id)),
        "draft": True,
    }
    assert repository.published == ()


def test_the_release_this_run_creates_carries_every_artifact(workdir: pathlib.Path) -> None:
    """The release body states that every artifact is covered by `SHA256SUMS`.
    A draft that carries a subset makes the product assert something false about
    its own supply chain (T-16), and `publish-release` checks only that
    `SHA256SUMS` is present — so the completeness has to be established here.
    """
    repository = _repository(workdir)

    drafted = _draft_release(repository)

    mine = repository.by_id(drafted.release_id)
    assert mine is not None
    assert sorted(asset["name"] for asset in mine["assets"]) == sorted(ARTIFACTS)


def test_an_artifact_whose_name_contains_a_plus_is_attached_under_that_name() -> None:
    """A PEP 440 local version puts `+` in a filename, and `+` is a space in a
    query string.

    The upload is a raw `curl` to `${upload_url}?name=${...}`; `gh release
    upload`, which used to do this, percent-encoded the value and the raw URL
    does not. Unencoded, `theurian-0.2.0+local-...whl` is received as
    `theurian-0.2.0 local-...whl`, so the asset is attached under a name nobody
    asked for and the read-back against `ls artifacts/dist` disagrees.

    That is worse than a plain failure. The step's own remedy is "delete this
    draft by id and re-run ALL jobs", and a re-run renames it again — the
    documented recovery never terminates. So this asserts the attached name, not
    merely that the job succeeded.

    `release.md` §7's hotfix procedure and any local build are how a `+` gets
    here; `0.1.0.dev0` has none, which is why the shipped release could not have
    shown this.
    """
    artifacts = ("SHA256SUMS", LOCAL_VERSION_WHEEL)
    with _artifacts(artifacts) as root:
        repository = _repository(root)

        drafted = _draft_release(repository)

        assert drafted.succeeded, drafted.log
        mine = repository.by_id(drafted.release_id)
        assert mine is not None
        assert sorted(asset["name"] for asset in mine["assets"]) == sorted(artifacts)


def test_the_emitted_id_survives_a_releases_list_that_never_catches_up(
    workdir: pathlib.Path,
) -> None:
    """`GET /releases` is eventually consistent — a release created at 12:38:09
    was measured absent from the list at 12:38:10.

    An earlier implementation read the id back from that list. It would have
    failed on every real release, and the retry it grew instead turned the lag
    into a *window*: whichever draft was visible during it became this run's id.

    The list is lagged past any conceivable retry here. The step must still
    succeed and still emit the id of the release it created, which is only
    possible if the id never came from the list at all.
    """
    repository = _repository(workdir)
    repository.lag_the_list(1000)

    drafted = _draft_release(repository)

    assert drafted.succeeded, drafted.log
    mine = repository.by_id(drafted.release_id)
    assert mine is not None
    assert mine["tag_name"] == TAG


# -- the drafting guard -----------------------------------------------------


def test_drafting_proceeds_when_the_tag_has_no_release(workdir: pathlib.Path) -> None:
    """The ordinary first release. A guard that refused this would block every
    one, and would be turned off."""
    repository = _repository(workdir)

    guard = _run_step(GUARD, repository)

    assert guard.succeeded, guard.log


def test_drafting_is_refused_when_a_draft_already_exists_for_the_tag(
    workdir: pathlib.Path,
) -> None:
    """The re-run that published an incomplete release.

    `gh release create --draft` accepts a duplicate tag, so without this the
    second attempt leaves two drafts and publication picks one — measured to be
    the older, incomplete one. Nothing else stops it.
    """
    repository = _repository(workdir, _release(1, assets=["SHA256SUMS"]))

    drafted = _draft_release(repository)

    assert not drafted.succeeded
    assert repository.writes == ()
    assert len(repository.releases) == 1


def test_the_refusal_names_the_id_and_the_command_that_removes_it(
    workdir: pathlib.Path,
) -> None:
    """A tag is not enough to act on: `gh release delete <tag>` was measured
    deleting the *published* release and leaving the draft. The remedy has to be
    executable, so the error carries the numeric id and a delete-by-id call.
    """
    repository = _repository(workdir, _release(98765))

    guard = _run_step(GUARD, repository)

    assert "98765" in guard.log
    assert f"repos/{REPOSITORY}/releases/" in guard.log
    assert "DELETE" in guard.log


def test_the_refusal_says_which_releases_are_drafts_and_to_re_run_all_jobs(
    workdir: pathlib.Path,
) -> None:
    """Two things a maintainer has to know before touching anything, and the
    guard is the only place either is said.

    Whether the release is a draft decides whether anything was published, and
    so which of the re-run rules applies. And after deleting a draft the re-run
    has to be of *all* jobs: re-running only the failed ones does not re-run the
    drafting job, so the publishing job keeps the id of the release that was
    just deleted -- measured, and the state release.md §5 exists to prevent.
    """
    repository = _repository(workdir, _release(4321, draft=True))

    guard = _run_step(GUARD, repository)

    assert "draft=true" in guard.log
    assert "ALL jobs" in guard.log


def test_the_refusal_separates_a_draft_that_was_once_public_from_one_that_never_was(
    workdir: pathlib.Path,
) -> None:
    """`draft=true` is the same for both, and they want opposite handling.

    Deleting and re-pushing a tag silently reverts its published release to a
    draft — measured during #59's review. That draft's version is already on
    PyPI, so deleting it is exactly how a run ends with an installable wheel and
    no release; a draft that was never public can be deleted freely. Nothing
    else in the refusal separates them, so `published_at` has to be printed,
    and a maintainer reading only `draft=true` would take the destructive branch.

    Both states are listed in one run, so the assertion is that the *printout*
    tells them apart rather than that some timestamp appears somewhere.
    """
    repository = _repository(
        workdir,
        _release(4321, draft=True),
        _release(4322, draft=True, published_at=PUBLISHED_AT),
    )

    guard = _run_step(GUARD, repository)

    reported = {
        int(line.split("id=")[1].split()[0]): line.split("published_at=")[1].split()[0]
        for line in guard.log.splitlines()
        if "id=" in line and "published_at=" in line
    }
    assert reported == {4321: "null", 4322: PUBLISHED_AT}


def test_a_draft_on_another_tag_does_not_block_this_one(workdir: pathlib.Path) -> None:
    """An unrelated draft — a release being prepared by hand — must not block
    this tag."""
    repository = _repository(workdir, _release(1, tag="core-v9.9.9"))

    guard = _run_step(GUARD, repository)

    assert guard.succeeded, guard.log


def test_drafting_is_refused_when_the_tag_already_has_a_published_release(
    workdir: pathlib.Path,
) -> None:
    """Reached by publishing a draft by hand and pressing "Re-run all jobs".

    An earlier version of this guard counted only `.draft`, so this state walked
    straight past it: the upload went to PyPI and the publishing step then took
    HTTP 422 from the REST API refusing a second published release on one tag.
    That 422 is a real backstop and it fires after the irreversible step, so the
    guard has to match every release on the tag, not only the drafts. Refusing
    here costs nothing; refusing after the upload costs a version that can only
    be yanked.
    """
    repository = _repository(workdir, _release(1, draft=False))

    guard = _run_step(GUARD, repository)

    assert not guard.succeeded
    assert repository.writes == ()


def test_a_concurrent_draft_never_becomes_this_runs_release_id(
    workdir: pathlib.Path,
) -> None:
    """The guard passed, so an earlier implementation assumed the one draft it
    later found on this tag was its own — an assumption resting on the same
    lagging list it was reading. A draft created by anything else during the
    window was therefore bindable, and publishing a stranger's draft under this
    version's notes is the original defect reached from the other side.

    Here the list both lags and holds a foreign draft, which is exactly the
    state that assumption fails in.
    """
    repository = _repository(workdir)
    guard = _run_step(GUARD, repository)
    assert guard.succeeded, guard.log

    repository.lag_the_list(1)
    repository.add(_release(990, assets=[]))
    create = _run_step(CREATE, repository)

    emitted = create.outputs.get("release_id", "")
    assert emitted != "990", "the step bound this run to a release it did not create"


# -- what the drafting job may claim about a place it cannot see ------------


def _refused_by_a_pre_existing_release(workdir: pathlib.Path) -> str:
    repository = _repository(workdir, _release(4321, draft=True))
    return _draft_release(repository).log


def _refused_by_an_incomplete_draft(workdir: pathlib.Path) -> str:
    repository = _repository(workdir)
    return _draft_release(repository, uploads={"UPLOAD_SILENTLY_DROPS": "SHA256SUMS"}).log


@pytest.mark.parametrize(
    "refuse",
    [_refused_by_a_pre_existing_release, _refused_by_an_incomplete_draft],
    ids=["the tag already has a release", "the draft is missing an artifact"],
)
def test_a_refusal_that_advises_deleting_a_release_says_where_to_check_pypi(
    workdir: pathlib.Path, refuse: Any
) -> None:
    """`draft-release` cannot see PyPI, and both of its refusals tell a
    maintainer to delete a release.

    The two facts together are the hazard. "This run uploaded nothing" is true
    and useless: the draft on the tag may be the record for a version a
    *previous* run already put on PyPI, and deleting it is precisely how a
    release ends as an installable wheel whose checksums exist nowhere — the one
    state T-16's ordering exists to prevent. Six messages that asserted
    publication state this job cannot observe were rewritten during #59's third
    round for exactly this reason.

    So a refusal that names a destructive command must also name the one place
    that settles whether it is safe. Asserting the URL rather than the prose is
    deliberate: the URL is the executable half, and wording is not.
    """
    log = refuse(workdir)

    assert "DELETE" in log or "delete" in log, "this path was expected to advise a deletion"
    assert f"https://pypi.org/project/theurian/{VERSION}/" in log


def test_no_step_in_the_drafting_job_states_flatly_that_nothing_was_published() -> None:
    """A tripwire for one retired sentence, and emphatically not a check that the
    surviving messages are correctly scoped.

    Two messages read "Nothing was published" before #59's third round. Neither
    step can know that: `publish-pypi` runs between this job's two invocations
    across a re-run, and nothing in `draft-release` queries PyPI. Both now scope
    the claim to *this run* and hand the rest to a human.

    The rule is a substring over English, so it decides nothing about a
    replacement wording — "the upload never happened" would pass it. What it
    buys is that the exact phrasing that was wrong cannot come back unnoticed,
    which is worth having and is all it is.
    """
    offenders = [
        step.name for step in (GUARD, CREATE) if "nothing was published" in step.script.lower()
    ]

    assert offenders == []


# -- what a partly-failed drafting job leaves behind ------------------------


def test_a_failed_upload_stops_the_job_and_blocks_a_second_attempt(
    workdir: pathlib.Path,
) -> None:
    """The release is created before any asset is attached, so a run that dies
    part-way through the upload leaves a partial draft behind — and the natural
    next move, "Re-run failed jobs", is what published the wrong release the
    first time this went wrong. The guard has to catch the wreckage the previous
    attempt left, which is why it runs before anything is created.

    `publish-pypi` waits on this job, so a failure here means nothing reached
    PyPI and the partial draft can simply be deleted.
    """
    repository = _repository(workdir)

    first = _draft_release(repository, uploads={"UPLOAD_REFUSE_AFTER": "1"})
    second = _draft_release(repository)

    assert not first.succeeded
    assert len(repository.releases) == 1
    assert not second.succeeded
    assert repository.published == ()


def test_drafting_is_refused_when_an_upload_reports_success_without_attaching(
    workdir: pathlib.Path,
) -> None:
    """An upload answering 201 without attaching the file is the one failure the
    per-asset status check cannot see. The maintainer approving `publish-pypi`
    reads the draft, so a draft that looks whole and is not would be approved on
    the strength of assets it does not carry — and the body it carries claims
    `SHA256SUMS` covers every one of them.

    Reading the release back by id and comparing against what was on disk is
    what closes that, so this drops exactly one file and expects a refusal.
    """
    repository = _repository(workdir)

    drafted = _draft_release(repository, uploads={"UPLOAD_SILENTLY_DROPS": "SHA256SUMS"})

    assert not drafted.succeeded
    assert drafted.release_id == ""
    assert repository.published == ()


def test_drafting_is_refused_when_the_tag_is_not_in_the_repository(
    workdir: pathlib.Path,
) -> None:
    """A release workflow that creates its own tag would publish a `core-v*`
    that the signature guard never saw (release.md §4). The tag must already
    exist, and be the one that was pushed.
    """
    repository = _repository(workdir)
    repository.remove_tag(TAG)

    drafted = _draft_release(repository)

    assert not drafted.succeeded
    assert repository.published == ()


# -- publish-release: flipping exactly the object that was created ----------


def test_publication_makes_public_the_release_whose_id_it_was_given(
    workdir: pathlib.Path,
) -> None:
    """The ordinary case, and the baseline the refusals are measured against."""
    repository = _repository(workdir)
    drafted = _draft_release(repository)

    published = _publish_release(repository, drafted.release_id)

    assert published.succeeded, published.log
    assert [r["id"] for r in repository.published] == [int(drafted.release_id)]


def test_publication_asks_github_to_choose_latest_by_version(workdir: pathlib.Path) -> None:
    """The one request-shape assertion in this file, and it says why it is one.

    `make_latest` defaults to `true` on this endpoint, which points the
    repository's "Latest" badge at whatever published most recently. Publishing a
    0.2.1 hotfix after 0.3.0 was measured moving `releases/latest` *backwards*,
    and release.md's Hotfixes procedure is exactly that path — a user following
    the Latest link would be handed the older release as the current one.
    `legacy` hands the choice to GitHub, which decides by version.

    There is no outcome here to assert. What `releases/latest` resolves to is
    computed by GitHub from a rule this model does not implement and could not
    establish from the repository, so the parameter is observable only in what
    the API was asked for. Reading it from the PATCH body the model parsed —
    rather than from argv — is what keeps this from also pinning the spelling:
    `-f`, `-F`, `--raw-field` and `--input` all arrive here the same way.
    """
    repository = _repository(workdir)
    drafted = _draft_release(repository)

    published = _publish_release(repository, drafted.release_id)

    assert published.succeeded, published.log
    assert [dict(patch) for patch in repository.patches] == [
        {"id": int(drafted.release_id), "draft": "false", "make_latest": "legacy"}
    ]


def test_nothing_is_published_when_no_id_arrived_from_the_drafting_job(
    workdir: pathlib.Path,
) -> None:
    """Fail closed. Falling back to the tag is precisely the defect this
    replaced, so an empty id must stop the job rather than degrade to a lookup.
    """
    repository = _repository(workdir)
    drafted = _draft_release(repository)
    before = repository.releases

    published = _publish_release(repository, "")

    assert not published.succeeded
    assert repository.releases == before
    assert drafted.release_id  # the run had an id; the job simply was not given it


def test_nothing_is_published_when_the_id_carries_a_different_tag(
    workdir: pathlib.Path,
) -> None:
    """The id was recorded in an earlier job. If the object it names is no
    longer this tag's release, publishing it announces some other version under
    this one's notes.
    """
    repository = _repository(workdir, _release(7, tag="core-v0.0.1"))

    published = _publish_release(repository, "7")

    assert not published.succeeded
    assert repository.published == ()


def test_nothing_is_published_when_the_release_is_already_public(
    workdir: pathlib.Path,
) -> None:
    """A draft published by hand between the jobs. Re-flipping it silently would
    make a run that changed nothing indistinguishable from one that published
    what it built.
    """
    repository = _repository(workdir)
    drafted = _draft_release(repository)
    repository.replace_releases([{**r, "draft": False} for r in repository.releases])

    published = _publish_release(repository, drafted.release_id)

    assert not published.succeeded


def test_nothing_is_published_when_the_draft_carries_no_checksums(
    workdir: pathlib.Path,
) -> None:
    """The release body states that every artifact is covered by `SHA256SUMS`.
    Publishing a release that does not carry that file makes the product assert
    something false about its own supply chain (T-16), so the claim is checked
    against the object before it goes public.
    """
    repository = _repository(workdir)
    drafted = _draft_release(repository)
    repository.replace_releases(
        [
            {**r, "assets": [a for a in r["assets"] if a["name"] != "SHA256SUMS"]}
            for r in repository.releases
        ]
    )

    published = _publish_release(repository, drafted.release_id)

    assert not published.succeeded
    assert repository.published == ()
    assert "SHA256SUMS" in published.log


def test_nothing_is_published_when_the_draft_has_been_deleted(
    workdir: pathlib.Path,
) -> None:
    """Deleting the draft and re-running only the *failed* jobs does not re-run
    the drafting job, so this one still carries the dead id. The wheel is on PyPI
    by then; what must not also happen is this job inventing a replacement.
    """
    repository = _repository(workdir)
    drafted = _draft_release(repository)
    repository.replace_releases([])

    published = _publish_release(repository, drafted.release_id)

    assert not published.succeeded
    assert repository.releases == ()


# -- keeping this file's own assertions able to fail ------------------------


def test_the_write_ledger_counts_the_command_the_refusals_forbid(
    workdir: pathlib.Path,
) -> None:
    """`repository.writes == ()` is what separates "refused" from "did it, then
    complained", and an exit code cannot tell those apart.

    It was blind. The ledger records the tool as argv[0] — `("gh", "release",
    "edit", …)` — and the predicate compared `call[:2]` against `("release",
    "edit")`, which no recorded call can equal. Every `gh release` subcommand
    was therefore invisible to it, including `release edit <tag> --draft=false`:
    the exact command the workflow was rewritten to stop using, and the one a
    refusal must be shown not to have issued. Only calls carrying an explicit
    `-X` were counted, so the assertion passed for the case it was written for.

    A GET must stay uncounted, or the predicate is trivially true instead of
    trivially false and nothing improves.
    """
    repository = _repository(workdir)
    repository.record("gh", "api", "repos/o/r/releases", "--paginate", "--jq", ".")
    repository.record("gh", "release", "edit", TAG, "--draft=false")
    repository.record("gh", "api", "-X", "PATCH", "repos/o/r/releases/1", "-F", "draft=false")

    assert repository.writes == (
        ("gh", "release", "edit", TAG, "--draft=false"),
        ("gh", "api", "-X", "PATCH", "repos/o/r/releases/1", "-F", "draft=false"),
    )


# -- the wiring between the jobs, which no step body can see ----------------


def test_the_steps_this_file_extracts_are_the_ones_it_names(workdir: pathlib.Path) -> None:
    """The steps are located structurally, so a rename cannot silently point
    these tests at different text. It also must not go unnoticed: these are the
    lines to update, deliberately, when a step is renamed.
    """
    assert workdir.exists()

    assert GUARD.name == "Refuse to draft when this tag already has a release"
    assert CREATE.name == "Create the release as a draft"
    assert PUBLISH.name == "Take the release out of draft"


def test_the_id_travels_from_the_job_that_created_the_release_to_the_one_that_publishes() -> None:
    """Every test above proves one half in isolation; none would notice the two
    halves being wired to different things. `RELEASE_ID` uses index syntax
    because `-` is also subtraction in a GitHub expression, and
    `needs.draft-release.outputs...` cannot be relied on to lex the intended way.
    """
    publish_step = _job("publish-release")["steps"][0]

    assert _job("draft-release")["outputs"]["release_id"] == "${{ steps.draft.outputs.release_id }}"
    assert publish_step["env"]["RELEASE_ID"] == "${{ needs['draft-release'].outputs.release_id }}"
    assert "draft-release" in _job("publish-release")["needs"]


def test_the_draft_exists_before_the_upload_and_is_published_after_it() -> None:
    """T-16's shipped half. `SHA256SUMS` and the SBOM must reach a durable place
    before the wheel is installable, because PyPI refuses a second upload of a
    filename it already holds — an upload cannot be undone, only yanked. Reorder
    these and a failure between them leaves an installable wheel whose checksums
    exist only in an expiring workflow artifact.
    """
    assert "draft-release" in _job("publish-pypi")["needs"]
    assert "publish-pypi" in _job("publish-release")["needs"]


def test_a_change_to_the_workflow_under_test_runs_this_file() -> None:
    """A test that does not run when its subject changes is not coverage.

    The subject here is a file, not a module, so nothing about editing it makes
    pytest run. `core.yml` filters on paths, and it listed `tests/**` — which
    covers editing the tests — but nothing that covers editing the thing they
    test. Both events matter: `pull_request` is where a change is reviewed, and
    `push` is what keeps main's badge honest about it.
    """
    triggers = _triggers(CORE_WORKFLOW)

    for event in ("push", "pull_request"):
        assert ".github/workflows/release-core.yml" in triggers[event]["paths"], event
        assert "tests/**" in triggers[event]["paths"], event


def test_a_tag_push_cannot_reach_the_publication_jobs_without_running_this_file() -> None:
    """The release workflow runs its own tests, and that is load-bearing.

    `core.yml`'s filters cover a change to the workflow or to these tests. They
    do not cover a tag pushed at a commit where either is already broken — a
    hotfix branch, a revert, a tag placed on an older commit. `quality` runs the
    whole suite, and the publication jobs sit behind it through `build`, so a
    red harness stops the release before `contents: write` is ever held.

    The chain is asserted rather than the step alone: `quality` running these
    tests means nothing if `draft-release` no longer waits on it.
    """
    quality_runs = [step.get("run") for step in _job("quality")["steps"] if "run" in step]

    assert "uv run pytest -q" in quality_runs
    assert _job("build")["needs"] == "quality"
    assert _job("draft-release")["needs"] == "build"


def test_no_publication_job_runs_outside_a_tag_push() -> None:
    """`workflow_dispatch` is documented as a rehearsal that publishes nothing.
    That claim is these three conditions and nothing else — there is no input,
    no flag, and no other guard behind it.
    """
    conditions = {
        job: _job(job)["if"] for job in ("draft-release", "publish-pypi", "publish-release")
    }

    assert conditions == dict.fromkeys(conditions, "github.event_name == 'push'")
