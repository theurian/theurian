"""A build whose withdrawal purge failed must not answer at all (GHSA-97q9-xxfg-33r6).

``migrate apply`` publishes a purged copy of the live index the moment a
withdrawal or a reclassification puts a row outside the build's own flavor
(ADR-0024 decision 5, ADR-0025 part 2). That purge can fail -- a disk error, a
verify failure, any adapter's exception -- and when it does, the use case
deliberately *reports* rather than raises, because the migration is already
committed and the apply is not the thing that failed
(``withdrawal_purge.publish_purge_for_withdrawal``, ``PURGE_FAILED_REMEDY``).

What it leaves behind is the whole of this file's subject: **the stale build
stays published, and until this fix it went on being served.** One root cause --
*the purge failed, so the index still holds the withdrawn rows, and the build is
still answering* -- with two observable faces, both driven here:

- **Verbatim disclosure through a sibling's ``raptorPath``.** A ``--raptor``
  build's summary nodes are built over *all* the leaves in a scope, so a summary
  standing above the now-withheld document keeps that document's text baked into
  its own. The canonical gate withholds the document's own hit and cannot touch a
  *visible* sibling's ``raptorPath[].title``, which carries the withheld text
  verbatim to an ordinary ``knowledge.search``. Reproduced before this file
  existed: a visible "Cache Policy" hit's path title contained the withheld
  document's marker.
- **The T-17a channel the same state feeds.** The stale build's four FTS5 tables
  still count the withheld rows, so every visible row it returns is priced
  against text no caller may read -- exactly what
  ``test_sensitivity_purge.py`` closes for the *successful* purge and what a
  failed one silently re-opens.

Because both faces are the same state, the fix is one thing and not two: the
pointer is tainted when the purge fails, and a tainted build is stood aside to
the unranked canonical scan. So the assertions divide the same way -- one over
what the response may carry (face 1, mechanism-agnostic, so it stays true under
any later fix), one over *why* it may not (face 2, the reason code a client
branches on), one over the taint not being sticky, and one over ``index status``
telling an operator the same thing ``knowledge.search`` tells an agent.

**The failure is injected at exactly one point and it is the documented one.**
``SqliteIndexStore.derive_purged`` raises ``IndexPurgeError``, which is the
failure ``test_withdrawal_purge.py::test_a_purge_that_raises_leaves_the_old_build_serving``
already pins as reachable and reported. Everything else is the real thing: the
real CLI for ``init``/``register``/``migrate apply``/``index build --raptor``,
the real daemon-side ``knowledge.search`` through ``build_server``, real index
files under ``tmp_path``, with ``HOME`` and ``THEURIAN_DATA_DIR`` redirected in
the same call that changes directory. Nothing here reaches the developer's own
machine, and nothing starts a daemon or registers a service.

**The corpus harness is deliberately a copy of ``test_sensitivity_purge.py``'s**
rather than an import. That file is the successful-purge half of the same
trigger and must keep working unchanged while this one is written against a fix
that does not exist yet; a shared module would have coupled the two through a
file neither owns. If both survive the fix, consolidating them into a
``tests/`` -level fixture module is the follow-up, not a prerequisite.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import subprocess
from collections.abc import Iterator, Sequence
from contextlib import closing, contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

import pytest
from migration_fixtures import body_pin
from typer.testing import CliRunner

from theurian.application.authorization import (
    SERVING_PROFILE_FILENAME,
    StaticAuthorizationProvider,
    load_serving_profile,
)
from theurian.application.project_service import (
    ProjectPaths,
    ProjectRegistry,
    read_active_index_pointer,
)
from theurian.cli.main import app
from theurian.daemon.runner import build_server
from theurian.domain.enums import Sensitivity
from theurian.infrastructure.sqlite.index_purge import IndexPurgeError
from theurian.infrastructure.sqlite.index_store import SqliteIndexStore

pytestmark = pytest.mark.integration

runner = CliRunner()

PROJECT: Final = "demo"

#: The wire value of ``retrieval.fallbackReason``, written out rather than
#: imported from ``mcp.search``. This is a client-facing contract test: a client
#: branches on the string it receives, not on a Python constant, and a rename
#: that kept the constant's name while changing its value would be a break this
#: file has to see. ``tests/unit/test_schemas.py`` is where the constant and the
#: published schema are held to each other.
INDEX_PURGE_FAILED: Final = "index-purge-failed"

#: A query every document in the corpus answers, so an empty response is never
#: mistaken for a withholding -- and so the unranked canonical scan the fix falls
#: back to has something to return. Measured: the scan answers it with the three
#: visible documents and carries no ``raptorPath`` at all.
QUERY: Final = "quarantine ledger"


def _ulid(tag: str) -> str:
    """A valid ULID literal, padded to 26 Crockford base32 characters.

    Crockford base32 excludes I, L, O and U; ``tests/unit/test_test_fixtures.py``
    guards *quoted* 26-character literals for exactly that, and an id assembled at
    runtime slips past that guard -- so the charset is asserted here rather than
    assumed.
    """
    value = f"01K1{tag}".ljust(26, "Z")
    assert len(value) == 26, f"{value!r} is not a 26-character ULID"
    assert not set(value) & set("ILOU"), f"{value!r} is not Crockford base32"
    return value


@dataclass(frozen=True, slots=True)
class Doc:
    """One knowledge item, with identifiers and a unique marker derived from ``slug``."""

    slug: str
    code: str
    sensitivity: str = "internal"
    kind: str = "architecture"

    @property
    def item_id(self) -> str:
        return f"{self.kind}.{self.slug}"

    @property
    def migration_id(self) -> str:
        return _ulid(f"M{self.code}")

    @property
    def revision_id(self) -> str:
        return _ulid(f"R{self.code}")

    @property
    def heading(self) -> str:
        return self.slug.replace("-", " ").title()

    @property
    def marker(self) -> str:
        """A token in every sentence of this document and in no other.

        Delimited at both ends so no marker is a substring of another: a summary
        node built from the wrong children would otherwise pass the check that
        exists to catch exactly that.
        """
        return f"mk-{self.slug}-mk"


#: Three headed sections per document, each long enough to be its own chunk. The
#: count is load-bearing: a RAPTOR tier is skipped below
#: ``minChildrenPerSummary``, and a corpus that produced no summary node at all
#: could show nothing about the ``raptorPath`` channel.
_SECTIONS: Final = (
    ("Tokens", "Every call carries a signed token issued by the gateway service."),
    ("Rotation", "Tokens rotate on restart and expire after one hour of idle time."),
    ("Revocation", "The quarantine ledger records every revoked token and its reason."),
)


def _body(doc: Doc) -> str:
    sections = "\n\n".join(
        f"## {heading}\n\n" + f"{doc.marker} {sentence} " * 4 for heading, sentence in _SECTIONS
    )
    return f"{doc.heading}\n\n{sections}\n"


def _migration(doc: Doc) -> str:
    """One document's ``createItem``/``upsertRevision`` pair.

    ``contentSha256`` is derived from :func:`_body`, the same function
    :func:`_write_corpus` writes the file with, so the pin and the bytes cannot
    drift (ADR-0027 decision 1). It is required on every ``upsertRevision``
    since #342, and an absent one is a schema refusal at ``migrate apply``.
    """
    return f"""apiVersion: theurian.dev/v1
id: {doc.migration_id}
createdAt: 2026-08-05T10:00:00+09:00
author: engineer@example.com
operations:
  - op: createItem
    itemId: {doc.item_id}
    kind: {doc.kind}
    namespace: backend
    owner: platform-team
  - op: upsertRevision
    itemId: {doc.item_id}
    revisionId: {doc.revision_id}
    contentFile: ../knowledge/{doc.kind}/{doc.slug}.md
    contentSha256: {body_pin(_body(doc))}
    metadata:
      title: {doc.heading}
      contentType: text/markdown
      kind: {doc.kind}
      namespace: backend
      status: approved
      sensitivity: {doc.sensitivity}
      owner: platform-team
      trustLevel: reviewed
      sourceAnchors:
        - provider: git
          sourceUri: git://demo/{doc.slug}.md
"""


def _reclassification(doc: Doc, level: Sensitivity) -> str:
    """A migration moving one item's disclosure class, and nothing else.

    Its id starts ``01K1W`` so it sorts after every create migration
    (``01K1M...``) and therefore applies last, whatever order the loader reads
    the directory in.
    """
    return f"""apiVersion: theurian.dev/v1
id: {_ulid(f"W{doc.code}")}
createdAt: 2026-08-05T11:00:00+09:00
author: engineer@example.com
operations:
  - op: changeSensitivity
    itemId: {doc.item_id}
    sensitivity: {level.value}
    reason: reclassified after the index was built
"""


def _write_corpus(root: Path, docs: Sequence[Doc]) -> None:
    for doc in docs:
        knowledge = root / ".theurian/knowledge" / doc.kind
        knowledge.mkdir(parents=True, exist_ok=True)
        (knowledge / f"{doc.slug}.md").write_text(_body(doc), encoding="utf-8")
        (root / f".theurian/migrations/{doc.migration_id}-{doc.slug}.yaml").write_text(
            _migration(doc), encoding="utf-8"
        )


def _write_reclassification(root: Path, doc: Doc, level: Sensitivity) -> None:
    (root / f".theurian/migrations/{_ulid(f'W{doc.code}')}-reclassify.yaml").write_text(
        _reclassification(doc, level), encoding="utf-8"
    )


# -- The project -------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Project:
    """One initialised, registered project in its own ``HOME`` and data directory."""

    root: Path
    datadir: Path
    home: Path


def _cli(project: Project, *args: str) -> tuple[int, dict[str, Any]]:
    """Invoke the real CLI inside ``project`` with its environment redirected.

    ``HOME`` is redirected beside ``THEURIAN_DATA_DIR`` even though nothing this
    file runs reads it directly: the fixture shells out to `git`, and a test that
    reaches the developer's real home directory is a defect that surfaces
    somewhere else entirely. Both are set in the same call that changes
    directory, never in an earlier one.
    """
    monkey = pytest.MonkeyPatch()
    monkey.setenv("HOME", str(project.home))
    monkey.setenv("THEURIAN_DATA_DIR", str(project.datadir))
    monkey.chdir(project.root)
    try:
        result = runner.invoke(app, [*args, "--json"], catch_exceptions=False)
    finally:
        monkey.undo()
    stream = result.stdout if result.exit_code == 0 else (result.stderr or result.stdout)
    return result.exit_code, json.loads(stream) if stream.strip() else {}


def _must(project: Project, *args: str) -> dict[str, Any]:
    code, payload = _cli(project, *args)
    assert code == 0, f"{' '.join(args)}: {payload}"
    return payload


@pytest.fixture
def project(tmp_path: Path) -> Project:
    built = Project(
        root=tmp_path / "demo", datadir=tmp_path / "demo-data", home=tmp_path / "demo-home"
    )
    for directory in (built.root, built.datadir, built.home):
        directory.mkdir(parents=True)
    for args in (
        ["git", "init", "-q", "-b", "main"],
        ["git", "config", "user.email", "test@example.com"],
        ["git", "config", "user.name", "Test"],
    ):
        subprocess.run(args, cwd=built.root, check=True, capture_output=True)  # noqa: S603
    _must(built, "init")
    _must(built, "project", "register", "--project-id", PROJECT)
    return built


def _declare_a_ceiling(project: Project, ceiling: Sensitivity) -> None:
    """Write the deployment serving profile the CLI and the server both read.

    The modes are not tidiness. ``load_serving_profile`` refuses a profile other
    local users can reach -- both the file and the directory holding it -- so a
    test that skipped them would exercise the refusal rather than the ceiling,
    and would say "the build failed" while looking like a withholding.
    """
    auth = project.datadir / "auth"
    auth.mkdir(parents=True, exist_ok=True, mode=0o700)
    auth.chmod(0o700)
    profile = auth / SERVING_PROFILE_FILENAME
    profile.write_text(f"{ceiling.value}\n", encoding="utf-8")
    profile.chmod(0o600)


# -- Reading a published build -----------------------------------------------


def _pointer(project: Project) -> dict[str, Any]:
    payload = read_active_index_pointer(ProjectPaths.of(project.root)).payload
    assert payload is not None, "the project must have a published index"
    return dict(payload)


def _published_index(project: Project) -> Path:
    return ProjectPaths.of(project.root).index_for(str(_pointer(project)["indexBuildId"]))


def _taint_the_pointer(project: Project) -> None:
    """Record a purge failure against the published build, and change nothing else.

    The pointer is a derived, git-ignored, unsigned JSON object (SEC-7), so this
    is the same edit the purge's failure path makes -- the key added, every other
    field left exactly as the last publish wrote it.
    """
    pointer = ProjectPaths.of(project.root).active_index_pointer
    payload = json.loads(pointer.read_text(encoding="utf-8"))
    pointer.write_text(json.dumps({**payload, "purgeFailed": True}, indent=2), encoding="utf-8")


def _node_text(path: Path) -> str:
    """Every summary node's text in one string.

    ``closing`` rather than ``with sqlite3.connect(...)``: that context manager
    commits and does not close, and ``filterwarnings = error`` turns the leaked
    handle's ``ResourceWarning`` into a failure in whichever test is running.
    """
    with closing(sqlite3.connect(path)) as connection:
        return " ".join(str(row[0]) for row in connection.execute("SELECT text FROM nodes"))


# -- Searching the way the daemon does ---------------------------------------


def _search(project: Project, query: str = QUERY) -> dict[str, Any]:
    """One ``knowledge.search`` under the grant this project's own profile grants.

    The grant is resolved from the profile file through the same
    ``StaticAuthorizationProvider(load_serving_profile(...))`` the daemon uses and
    ``theurian index build`` read a moment earlier, so the build's flavor and the
    serving grant are one derivation and not two. A second spelling here is how a
    test ends up measuring ``serving-profile-mismatch`` while claiming to measure
    a purge failure.
    """
    grant = StaticAuthorizationProvider(load_serving_profile(project.datadir)).deployment_grant()
    registry = ProjectRegistry.default(project.datadir)

    async def invoke() -> Any:
        return await build_server(registry, grant).call_tool(
            "knowledge.search", {"projectId": PROJECT, "query": query}
        )

    result = asyncio.run(invoke())
    structured = getattr(result, "structuredContent", None)
    if structured is not None:
        payload: dict[str, Any] = structured
        return payload
    loaded: dict[str, Any] = json.loads(result.content[0].text)
    return loaded


def _answered_from_the_index(answer: dict[str, Any]) -> None:
    """Assert this response came off the published build, not the canonical scan.

    Used only where a test needs the *build* to have answered -- the
    arrangements, and the rebuild. Where the claim is that the build was stood
    aside, the assertion is the opposite one and is written out inline, because
    an absence read off the wrong path is exactly the mistake this file is about.
    """
    retrieval = answer["retrieval"]
    assert retrieval["indexed"] is True, (
        f"the response did not come from the published build, so what it does or does not "
        f"carry says nothing about that build: {retrieval}"
    )
    assert not retrieval["fallbackReason"], (
        f"the ranked path stood the build aside ({retrieval['fallbackReason']}), so this "
        f"measures the canonical scan"
    )


def _raptor_titles(answer: dict[str, Any]) -> list[str]:
    """Every ``raptorPath[].title`` in a response, across every hit.

    The channel itself. A summary node's ``title`` is the node's own text, and a
    node above a withheld leaf was built from that leaf's words -- so a title is
    where a *visible* document's provenance trail carries a withheld document's
    content.
    """
    return [
        str(segment.get("title", ""))
        for hit in answer.get("results", [])
        for segment in (hit.get("raptorPath") or [])
    ]


def _titles_of_other_documents(answer: dict[str, Any], doc: Doc) -> list[str]:
    """``raptorPath`` titles belonging to hits that are *not* ``doc``.

    The document's own hit is excluded on purpose: while it is still visible, its
    own path legitimately carries its own text. Only a *sibling's* path carrying
    it is the channel, both as the arrangement's non-vacuity check and as what
    must be gone afterwards.
    """
    return [
        str(segment.get("title", ""))
        for hit in answer.get("results", [])
        if hit["itemId"] != doc.item_id
        for segment in (hit.get("raptorPath") or [])
    ]


# -- The corpus --------------------------------------------------------------

#: Four documents in one scope. Four rather than three so the RAPTOR tiers have a
#: Domain node standing over more than one survivor -- a corpus that lost its only
#: summary node when one document went could not show anything about a sibling's
#: path.
_VISIBLE: Final = (
    Doc("auth-policy", code="AA"),
    Doc("quota-policy", code="AB"),
    Doc("cache-policy", code="AC"),
)
_RECLASSIFIED: Final = Doc("payroll-bands", code="AD")
_ALL: Final = (*_VISIBLE, _RECLASSIFIED)


def _built_under_an_internal_ceiling(project: Project) -> Path:
    """Apply the corpus and publish a ``--raptor`` build under a declared ceiling.

    ``internal`` rather than the shipped default, because the shipped default
    serves every level and a build made under it holds every level -- against
    which no reclassification is ever outside the flavor, so no purge would be
    triggered and there would be nothing here to fail.
    """
    _declare_a_ceiling(project, Sensitivity.INTERNAL)
    _write_corpus(project.root, _ALL)
    _must(project, "migrate", "apply")
    _must(project, "index", "build", "--raptor")
    return _published_index(project)


@contextmanager
def _a_purge_that_fails() -> Iterator[None]:
    """Make the next ``migrate apply``'s index purge raise, and only that.

    ``IndexPurgeError`` out of ``derive_purged`` is the documented failure --
    ``withdrawal_purge`` catches every exception from a ``PurgeableIndex`` and
    reports it through ``failed``/``remedy``, and
    ``test_withdrawal_purge.py::test_a_purge_that_raises_leaves_the_old_build_serving``
    already pins that path. Injecting here rather than corrupting a file keeps
    the arrangement independent of *why* a real purge would fail: a disk error, a
    verify failure and a future adapter's exception all arrive at the same place.
    """

    def _raise(_self: object, *_args: object, **_kwargs: object) -> int:
        raise IndexPurgeError("the copy could not be verified")

    with pytest.MonkeyPatch.context() as patched:
        patched.setattr(SqliteIndexStore, "derive_purged", _raise)
        yield


def _apply_with_a_failing_purge(project: Project, doc: Doc = _RECLASSIFIED) -> dict[str, Any]:
    """Reclassify ``doc`` above the build's ceiling with the purge failing.

    Returns the ``migrate apply`` payload, having first asserted that the
    arrangement it describes is the one every test below reasons from: the apply
    itself succeeded, the purge failed, nothing was published, and the pointer
    still names the same stale build. Without those four, a green assertion
    downstream could be measuring a purge that quietly worked.
    """
    before = _published_index(project)
    _write_reclassification(project.root, doc, Sensitivity.CONFIDENTIAL)

    with _a_purge_that_fails():
        applied = _must(project, "migrate", "apply")

    purge = applied["indexPurge"]
    assert purge["failed"] is True, f"the purge did not fail as arranged: {purge}"
    assert purge["published"] is False, f"a purge published despite failing: {purge}"
    assert _published_index(project) == before, (
        "the pointer moved off the stale build, so there is no still-published build here "
        "to be wrong about"
    )
    return applied


# -- Face 1: the response may not carry the withheld text ---------------------


def test_a_purge_failed_raptor_build_does_not_leak_withheld_text_in_a_raptor_path_title(
    project: Project,
) -> None:
    """The disclosure this whole class is graded on (GHSA-97q9-xxfg-33r6, T-17a).

    A ``--raptor`` build's summary nodes are derived over every leaf in a scope,
    so a node above the reclassified document holds that document's words in its
    own ``text`` -- which is what a hit's ``raptorPath[].title`` publishes. The
    canonical gate re-checks each *result* against the item's current
    sensitivity, which withholds the document's own hit and does nothing at all
    about a **visible sibling's** provenance trail. So a build that still holds
    the withheld leaf hands its text, verbatim, to an ordinary
    ``knowledge.search`` by a caller who is entitled to the sibling.

    Two arrangements make the absence below mean something, and they are asserted
    rather than assumed:

    - a summary node's ``text`` really carries the withheld document's marker, so
      there is text in the file to leak; and
    - before the reclassification, a *sibling's* ``raptorPath`` title really
      carries that marker, so the channel is open on this corpus rather than
      merely conceivable.

    And the response afterwards must still be an answer: the visible documents
    are asserted present, because "no title carries the marker" is satisfied
    perfectly by a search that returns nothing, and a fix that withheld
    everything would satisfy this file while destroying the product.

    Deliberately says nothing about *how* the text stops arriving -- no reason
    code, no ``indexed`` flag. That is the next test's subject. This one is the
    property that must survive any later change to the mechanism.
    """
    published = _built_under_an_internal_ceiling(project)
    assert _RECLASSIFIED.marker in _node_text(published), (
        "no summary node was derived over the reclassified document, so this build has no "
        "withheld text in a node to leak and the absence below would prove nothing"
    )
    before = _search(project)
    _answered_from_the_index(before)
    siblings = _titles_of_other_documents(before, _RECLASSIFIED)
    assert any(_RECLASSIFIED.marker in title for title in siblings), (
        "no sibling's raptorPath title carried the document's text while it was still "
        "visible, so this corpus does not open the channel under test"
    )

    _apply_with_a_failing_purge(project)

    served = _search(project)

    leaked = [title for title in _raptor_titles(served) if _RECLASSIFIED.marker in title]
    assert not leaked, (
        f"a raptorPath title carried the reclassified document's text verbatim after its "
        f"purge failed: {leaked}"
    )
    assert _RECLASSIFIED.marker not in json.dumps(served), (
        "the withheld document's text reached the caller somewhere else in the response -- "
        "an excerpt, a title or a snippet carries it as surely as a raptorPath does"
    )
    assert {doc.item_id for doc in _VISIBLE} <= {hit["itemId"] for hit in served["results"]}, (
        f"the visible documents stopped being answerable, which satisfies every absence "
        f"above by returning nothing: {served['results']}"
    )


# -- Face 2: and it may not be served at all ----------------------------------


def test_a_purge_failed_build_is_not_served(project: Project) -> None:
    """The sibling channel the same state feeds, closed by standing the build aside.

    Suppressing the ``raptorPath`` alone would not be a fix. The stale build's
    four FTS5 tables still hold the withheld document's postings, and an
    external-content FTS5 table scores every row it returns against collection
    statistics computed over every row it holds -- so a visible result's rank
    moves with content the caller may not read (T-17a). That is the very channel
    ``test_sensitivity_purge.py`` closes for a purge that *worked*, and a failed
    one re-opens it silently.

    There is no read-time filter for it: the only correct answer is that the
    build does not answer. So this asserts the ranked path stood aside, and
    asserts the specific reason code rather than merely "some fallback" -- a
    build stood aside for ``serving-profile-mismatch`` or ``index-file-missing``
    would satisfy a vaguer assertion while saying nothing about a purge, and a
    client branching on the code needs the one that means "rebuild, your index
    still holds withdrawn rows".

    The precondition is the same build answering the same query a moment earlier,
    so ``indexed: false`` here is the reclassification's doing and not a project
    that never had a usable index.
    """
    _built_under_an_internal_ceiling(project)
    before = _search(project)
    _answered_from_the_index(before)

    _apply_with_a_failing_purge(project)

    served = _search(project)

    assert served["retrieval"]["indexed"] is False, (
        f"a build whose purge failed answered the query from its own index, so every "
        f"visible row it returned was priced against the withheld rows: {served['retrieval']}"
    )
    assert served["retrieval"]["fallbackReason"] == INDEX_PURGE_FAILED, (
        f"the ranked path stood aside for the wrong stated reason, so a client cannot tell "
        f"a failed purge from an ordinary rebuild: {served['retrieval']}"
    )
    assert served["count"] > 0, (
        "standing the build aside took the answer with it -- the unranked canonical scan "
        "must still answer a query the corpus matches"
    )


# -- The taint is not sticky --------------------------------------------------


def test_a_rebuild_clears_the_purge_failed_taint(project: Project) -> None:
    """The remedy every failure message names must actually work.

    ``PURGE_FAILED_REMEDY`` and ``theurian index status`` both tell an operator to
    run ``theurian index build``. If the taint outlived the build it was written
    against, that instruction would leave the project permanently on the unranked
    scan, and the only visible symptom would be worse results -- which is exactly
    the shape of failure nobody reports. So the fix's own remedy is pinned: a
    rebuild republishes, and the ranked path comes back.

    It has to come back *clean*, and that is asserted in the same breath rather
    than in a separate test, because the two together are the claim. A rebuild
    re-derives from canonical state, where the document is now ``confidential``
    and above the deployment's ``internal`` ceiling, so no chunk and no summary
    node is written for it -- and a fresh build that served the ranked path again
    while still carrying the withheld text in a summary would be the original
    disclosure with an extra step.
    """
    _built_under_an_internal_ceiling(project)
    _apply_with_a_failing_purge(project)
    tainted = _search(project)
    assert tainted["retrieval"]["indexed"] is False, (
        "the build was still being served, so a rebuild cannot be shown to have cleared anything"
    )

    _must(project, "index", "build", "--raptor")

    rebuilt = _search(project)

    _answered_from_the_index(rebuilt)
    assert rebuilt["count"] > 0, "the rebuilt index answers nothing, so it is not serving"
    assert _RECLASSIFIED.marker not in json.dumps(rebuilt), (
        "the rebuilt index carried the reclassified document's text back into the response, "
        "so the taint was cleared by publishing the same disclosure again"
    )


# -- `index status` says the same thing to an operator ------------------------


def test_index_status_reports_a_purge_failed_build_as_stale(project: Project) -> None:
    """Whatever the ranked path refuses, ``index status`` reports as stale.

    The invariant ``index_status``' own docstring states, extended to this
    failure. An operator whose purge failed learns about it from ``migrate
    apply``'s ``indexPurge.remedy`` once, in one command's output, and then never
    again -- ``index status`` is the surface they come back to, and it must not
    call a build fresh that ``knowledge.search`` has stopped using.

    **``stale`` is not the discriminating assertion here and must not be read as
    one.** A purge is triggered by a migration, so a purge-failed build always
    has a state hash behind the current one, and ``stale`` is already ``true``
    from that comparison alone -- measured on the unfixed code, which reports
    ``stale: true`` with the rebuild remedy for exactly this arrangement. It is
    asserted anyway because a fix that reported ``stale: false`` would be a
    regression, but the load-bearing assertions are ``purgeFailed`` -- which is
    the only thing here that distinguishes "your index is one migration behind"
    from "your index still holds rows that were withdrawn from it" -- and the
    remedy. ``stale``'s own dependence on the taint is pinned separately by
    :func:`test_the_purge_failed_flag_alone_makes_an_otherwise_fresh_build_stale`,
    where nothing else has moved.
    """
    _built_under_an_internal_ceiling(project)
    fresh = _must(project, "index", "status")
    assert fresh["stale"] is False, (
        f"the build was already stale before the reclassification, so nothing below is "
        f"about the purge: {fresh}"
    )
    assert fresh.get("purgeFailed") is False, (
        f"a build that has never had a purge fail is reported as purge-failed: {fresh}"
    )

    _apply_with_a_failing_purge(project)

    status = _must(project, "index", "status")

    assert status["purgeFailed"] is True, (
        f"`index status` does not tell the operator the published build still holds the "
        f"withdrawn rows: {status}"
    )
    assert status["stale"] is True, f"a build knowledge.search refuses is reported fresh: {status}"
    assert "index build" in status["remedy"], (
        f"the remedy does not name the rebuild that is the only cure: {status['remedy']!r}"
    )


def test_the_purge_failed_flag_alone_makes_an_otherwise_fresh_build_stale(
    project: Project,
) -> None:
    """``stale`` moves on the taint itself, with nothing else changed.

    The companion above cannot show this: it reclassifies, which shifts the state
    hash, and ``stale`` is ``true`` from that comparison whatever the taint says.
    So the taint is set here on its own, against a build ``index status`` has just
    reported fresh, and nothing else about the project moves: same migrations,
    same state hash, same build id, same file.

    That makes the ``stale`` flip attributable to one input, which is the whole
    point. Without it, ``index status``' purge-failed arm could be deleted
    entirely and the companion test would stay green.

    The pointer is edited here rather than driven through
    ``mark_active_index_purge_failed``, and deliberately: this is a test of the
    *reader*, so its arrangement belongs at the file the reader reads. The other
    half -- that the production writer puts exactly this key and this JSON
    boolean there -- is pinned by
    ``tests/unit/test_active_index_purge_taint.py::test_the_taint_is_json_the_pointer_contract_can_carry``,
    so the two halves meet on the file rather than on a shared helper that could
    drift with them.
    """
    _built_under_an_internal_ceiling(project)
    before = _must(project, "index", "status")
    assert before["stale"] is False, f"the arrangement is not a fresh build: {before}"

    _taint_the_pointer(project)

    after = _must(project, "index", "status")
    assert after["indexBuildId"] == before["indexBuildId"], (
        "the taint moved the published build, so this compares two different builds"
    )
    assert after["indexStateHash"] == before["indexStateHash"], (
        "the taint rewrote the recorded state hash, so `stale` could have flipped on that instead"
    )
    assert after["purgeFailed"] is True, f"the taint was not reported: {after}"
    assert after["stale"] is True, (
        f"a build whose purge failed is reported fresh when its state hash happens to match: "
        f"{after}"
    )
    assert "index build" in after["remedy"], (
        f"a purge-failed build is reported stale with no command to fix it: {after['remedy']!r}"
    )
