"""``theurian index build`` scans the served corpus for secrets (SEC-11, #329).

``test_content_secrets.py`` proves the detector detects, ``test_project_config.py``
proves the policy reader reads, and ``test_proposal_secret_scan.py`` proves the two
are wired together on the **approval** path. This is the fourth thing, and none of
those can say it: that a body already in the corpus -- one that entered before the
scanner shipped, or through a hand-placed migration that never passed ``propose
accept`` -- is scanned when the index that serves it is built.

**Every fixture here is the pre-planted case (AC-5).** The migration and its body
are written straight into ``.theurian/`` and applied; nothing goes through
``propose accept``, so no approval-time scan has ever seen them. The build is the
first control that meets this content, which is exactly the population #329 exists
for -- and the build re-derives it from the whole canonical state on every run, so
this is the whole served population rather than a delta.

**The scan is a signal, not a gate (the issue's Option B).** ``block`` publishes
the index and exits non-zero; it does not withhold retrieval, because the content
is already readable through the canonical store whatever the index holds, and
halting the build would deny ranking without un-disclosing anything.
``test_block_publishes_the_index_it_found_the_secret_in`` is the companion that
holds that line.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from migration_fixtures import body_pin
from typer.testing import CliRunner

from theurian.application.project_service import (
    ProjectPaths,
    ProjectRegistry,
    read_active_index_pointer,
)
from theurian.cli.main import app
from theurian.daemon.runner import build_server
from theurian.security.content_secrets import MAX_FINDINGS, REDACTED_PREFIX_CHARS

pytestmark = pytest.mark.integration

runner = CliRunner()

#: The planted credential, **split into two literals and joined at run time**.
#:
#: The `Secret scan` job in ``security.yml`` runs gitleaks' default ruleset over
#: this repository's whole history, and a contiguous ``AKIA`` followed by sixteen
#: upper-case characters is exactly what its ``aws-access-token`` rule looks for.
#: Written whole it would need an entry in ``.gitleaks.toml``, and an allowlist
#: keyed on a credential-shaped literal is a place a real credential can hide.
#: ``test_content_secrets.py::PATTERN_FAMILY_FIXTURES`` records the same reasoning
#: for the same reason; this file plants one value rather than importing that
#: table, because a fixture shared across two test packages would have to move to
#: a ``conftest`` and take both modules' scanners with it.
#:
#: Obviously unreal on inspection: the word EXAMPLE and an ascending digit run. It
#: is not and has never been a credential. Twenty characters, which is under
#: ``_MIN_CANDIDATE_CHARS``, so exactly one family reports it and a test can name
#: that family without the generic one arriving alongside.
PLANTED = "AKIA" + "EXAMPLE012345678"

#: The family :data:`PLANTED` must be reported under. Named rather than inferred,
#: so a detector change that reclassified it fails here instead of quietly
#: weakening what this file claims to demonstrate.
PLANTED_FAMILY = "aws-access-key-id"

CLEAN_BODY = "# Authentication policy\n\nEvery call carries a signed token.\n"

#: A body that carries the credential in ordinary prose. The trailing period is
#: load-bearing: the family's pattern ends in a negative lookahead over
#: ``[0-9A-Za-z]``, so a value glued to a word would not match and this file would
#: be testing the detector's boundary handling rather than the build's wiring.
DIRTY_BODY = (
    "# Legacy key rotation\n\n"
    f"The retired staging account used {PLANTED}. Rotate before the next audit.\n"
)

#: A body whose *title* carries the credential and whose text does not. The index
#: chunks ``served_content_text(title, body)`` -- title first -- so a scan reading
#: the body alone would miss this one entirely (the GHSA-3f65 lesson: key the
#: control on the exact served string, never a subset of it).
TITLED_BODY = "# Placeholder\n\nNothing in this body is credential-shaped.\n"

CLEAN_ITEM = "architecture.auth-policy"
DIRTY_ITEM = "architecture.legacy-keys"
DRAFT_ITEM = "architecture.unreleased-keys"

_CLEAN_MIGRATION_ID = "01K1AAAAAA01234567890ABCDE"
_DIRTY_MIGRATION_ID = "01K1BBBBBB01234567890ABCDE"
_DRAFT_MIGRATION_ID = "01K1CCCCCC01234567890ABCDE"


def _migration(  # noqa: PLR0913, PLR0917 -- one argument per migration field
    migration_id: str, item: str, revision: str, title: str, status: str, body: str
) -> str:
    """One item, one revision, written by hand into the migrations directory.

    Deliberately not produced by ``propose accept``: the population this file is
    about is content the approval gate never saw.
    """
    filename = f"{item.split('.', 1)[1]}.md"
    return f"""apiVersion: theurian.dev/v1
id: {migration_id}
createdAt: 2026-09-03T10:00:00+09:00
author: engineer@example.com
operations:
  - op: createItem
    itemId: {item}
    kind: architecture
    namespace: backend
    owner: platform-team
  - op: upsertRevision
    itemId: {item}
    revisionId: {revision}
    contentFile: ../knowledge/architecture/{filename}
    contentSha256: {body_pin(body)}
    metadata:
      title: {title}
      contentType: text/markdown
      kind: architecture
      namespace: backend
      status: {status}
      owner: platform-team
      trustLevel: reviewed
      sourceAnchors:
        - provider: git
          sourceUri: git://demo/{filename}
"""


def _in(root: Path, *args: str) -> tuple[int, dict[str, Any]]:
    """Run a CLI command with ``root`` as the working directory.

    The working directory is set for this call rather than inherited, because
    ``theurian init`` and every project command resolve the project from
    ``Path.cwd()`` and a leaked ``chdir`` initialises Theurian into whichever tree
    the previous test left behind.
    """
    monkey = pytest.MonkeyPatch()
    monkey.chdir(root)
    try:
        result = runner.invoke(app, [*args, "--json"], catch_exceptions=False)
    finally:
        monkey.undo()
    stream = result.stdout if result.exit_code == 0 else (result.stderr or result.stdout)
    return result.exit_code, json.loads(stream) if stream.strip() else {}


def _streams(root: Path, *args: str) -> str:
    """Both streams of a command, JSON off, for a disclosure sweep.

    ``--json`` is deliberately absent: the human rendering is a second surface,
    it goes through a different formatter, and AC-4 is about *all* of the build's
    output rather than the machine channel alone.
    """
    monkey = pytest.MonkeyPatch()
    monkey.chdir(root)
    try:
        result = runner.invoke(app, list(args), catch_exceptions=False)
    finally:
        monkey.undo()
    return (result.stdout or "") + (result.stderr or "")


def _must(root: Path, *args: str) -> dict[str, Any]:
    code, payload = _in(root, *args)
    assert code == 0, payload
    return payload


def _write_policy(root: Path, value: str) -> None:
    """State ``security.secretScan`` in the project's configuration file.

    ``value`` arrives already quoted where it has to be: YAML 1.1 reads a bare
    ``off`` as the boolean ``False``, which ``read_secret_scan_policy`` refuses
    rather than translates.
    """
    (root / ".theurian/config.yaml").write_text(
        f"security:\n  secretScan: {value}\n", encoding="utf-8"
    )


def _corpus(
    root: Path, *, dirty: str = DIRTY_BODY, dirty_title: str = "Legacy key rotation"
) -> None:
    """Write one clean item and one carrying ``dirty``, and apply them."""
    knowledge = root / ".theurian/knowledge/architecture"
    (knowledge / "auth-policy.md").write_text(CLEAN_BODY, encoding="utf-8")
    (knowledge / "legacy-keys.md").write_text(dirty, encoding="utf-8")
    migrations = root / ".theurian/migrations"
    (migrations / f"{_CLEAN_MIGRATION_ID}-auth.yaml").write_text(
        _migration(
            _CLEAN_MIGRATION_ID,
            CLEAN_ITEM,
            "01K1AREVAA01234567890ABCDE",
            "Authentication policy",
            "approved",
            CLEAN_BODY,
        ),
        encoding="utf-8",
    )
    (migrations / f"{_DIRTY_MIGRATION_ID}-legacy.yaml").write_text(
        _migration(
            _DIRTY_MIGRATION_ID,
            DIRTY_ITEM,
            "01K1BREVBB01234567890ABCDE",
            dirty_title,
            "approved",
            dirty,
        ),
        encoding="utf-8",
    )


@pytest.fixture
def bare(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """An initialised, registered project with no knowledge and no index."""
    root = tmp_path / "demo"
    root.mkdir()
    for args in (
        ["git", "init", "-q", "-b", "main"],
        ["git", "config", "user.email", "test@example.com"],
        ["git", "config", "user.name", "Test"],
    ):
        subprocess.run(args, cwd=root, check=True, capture_output=True)  # noqa: S603

    monkeypatch.setenv("THEURIAN_DATA_DIR", str(tmp_path / "datadir"))
    _must(root, "init")
    _must(root, "project", "register")
    yield root


@pytest.fixture
def planted(bare: Path) -> Path:
    """The pre-planted corpus: one clean item, one carrying :data:`PLANTED`.

    Applied but **not** indexed, so each test decides the policy that is in force
    before the first build reads a single body.
    """
    _corpus(bare)
    _must(bare, "migrate", "apply")
    return bare


@pytest.fixture
def clean(bare: Path) -> Path:
    """The same corpus with nothing credential-shaped in it.

    The negative half of AC-1's positive control: a report of no findings means
    something only when the same command reports one on a corpus that carries a
    secret, and vice versa.
    """
    _corpus(bare, dirty=CLEAN_BODY.replace("Authentication", "Rotation"))
    _must(bare, "migrate", "apply")
    return bare


def _findings(payload: dict[str, Any]) -> list[str]:
    reported = payload.get("secretFindings")
    assert isinstance(reported, list), f"the build report carries no secretFindings: {payload}"
    return [str(line) for line in reported]


# -- AC-1 and AC-5: the positive control, over the pre-planted population -------


def test_a_secret_that_landed_before_this_control_shipped_is_caught_by_the_next_build(
    planted: Path,
) -> None:
    """AC-1 + AC-5: the planted credential is reported, by family and by item.

    The body was written into ``.theurian/knowledge/`` and applied through a
    hand-placed migration -- no ``propose accept``, so T-15's approval-time scan
    never ran over it -- and the index build is the first control to read it. That
    is the whole point of a build-time scan: it re-checks the served population on
    every rebuild rather than the delta since the last one.
    """
    code, payload = _in(planted, "index", "build")

    lines = _findings(payload)
    assert lines, "a corpus carrying a planted credential reported no findings"
    assert any(DIRTY_ITEM in line for line in lines), (
        f"no finding names the item whose body carries the secret: {lines}"
    )
    assert any(PLANTED_FAMILY in line for line in lines), (
        f"the finding does not name the family that matched: {lines}"
    )
    assert code != 0, "the default policy is `block`, whose signal is a non-zero exit"


def test_a_corpus_with_nothing_credential_shaped_reports_no_findings(clean: Path) -> None:
    """The other half of the control: the report is not constant-positive.

    Without this, every assertion above would pass against a build that reported
    a finding for each item it read.
    """
    code, payload = _in(clean, "index", "build")

    assert code == 0, payload
    assert _findings(payload) == [], "a clean corpus was reported as carrying a secret"
    assert payload["secretScanPolicy"] == "block", (
        "the scan must have run under the strict default, or the empty list above says nothing"
    )


def test_a_secret_in_the_title_is_caught_because_the_scan_reads_the_served_text(
    bare: Path,
) -> None:
    """The served string is ``title + body``, and the control keys on all of it.

    A scan reading revision bodies alone would miss this: the index chunks
    ``served_content_text(title, body)``, so a credential in a title is served and
    ranked exactly like one in the prose. GHSA-3f65 is the recorded instance of a
    gate keyed on a subset of what was served.
    """
    _corpus(bare, dirty=TITLED_BODY, dirty_title=f"Retired key {PLANTED}")
    _must(bare, "migrate", "apply")

    _, payload = _in(bare, "index", "build")

    assert any(DIRTY_ITEM in line for line in _findings(payload)), (
        f"a credential in the title went unreported: {payload}"
    )


# -- AC-2: block is a signal, and it does not withhold retrieval ---------------


def test_block_publishes_the_index_it_found_the_secret_in(planted: Path) -> None:
    """AC-2: the index is published, and the pointer names it.

    Blocking the build would deny ranking without un-disclosing anything -- the
    body is already readable through the canonical store and through
    ``knowledge.get`` whatever the index holds -- so ``block`` is a loud signal
    over a published build, not a refusal.
    """
    code, payload = _in(planted, "index", "build")

    assert code != 0, "block signals with a non-zero exit"
    assert payload["published"] is True, f"the build was not published: {payload}"
    paths = ProjectPaths.of(planted)
    pointer = read_active_index_pointer(paths).payload
    assert pointer is not None, "no index pointer was written"
    assert paths.index_for(str(pointer["indexBuildId"])).is_file(), (
        "the pointer names a build whose file is not on disk"
    )


def test_a_search_still_serves_the_corpus_a_block_build_reported(
    planted: Path, tmp_path: Path
) -> None:
    """AC-2's companion: blocking must not deny retrieval.

    The sharpest form of the claim -- the very document the scan reported is
    still ranked and returned. A build that withheld the index would answer this
    query from the unranked canonical scan with ``indexed: false``.
    """
    assert _in(planted, "index", "build")[0] != 0

    registry = ProjectRegistry.default(tmp_path / "datadir")
    response = asyncio.run(_search(registry, query="rotation"))

    assert response["retrieval"]["indexed"] is True, (
        f"the published index was not used: {response['retrieval']}"
    )
    served = {result["itemId"] for result in response["results"]}
    assert DIRTY_ITEM in served, (
        f"the item the scan reported is no longer served: {response['results']}"
    )


async def _search(registry: ProjectRegistry, **arguments: Any) -> dict[str, Any]:
    """Call ``knowledge.search`` through the same entry point the transport uses."""
    result = await build_server(registry, None).call_tool(
        "knowledge.search", {"projectId": "demo", **arguments}
    )
    structured = getattr(result, "structuredContent", None)
    if structured is not None:
        payload: dict[str, Any] = structured
        return payload
    content: Any = result.content  # type: ignore[union-attr]
    loaded: dict[str, Any] = json.loads(content[0].text)
    return loaded


def test_doctor_reports_the_published_builds_secret_as_degraded(planted: Path) -> None:
    """AC-2: ``doctor`` carries the signal past the build that raised it.

    The build's exit code is seen once, by whoever ran it. The operator who comes
    back to a machine hours later asks ``doctor``, and it has to still say that
    the published index carries a credential -- with the remedy that applies to
    content which has already landed.
    """
    assert _in(planted, "index", "build")[0] != 0

    _, payload = _in(planted, "doctor")

    scan = payload["indexSecretScan"]
    assert scan["status"] == "degraded", f"doctor did not report the landed secret: {scan}"
    assert scan["findings"] == 1, scan
    assert scan["policy"] == "block", scan
    remedy = str(scan["remedy"]).lower()
    assert "supersede" in remedy and "retire" in remedy, (
        f"the remedy for a landed secret must name supersede/retire: {scan['remedy']}"
    )


def test_a_degraded_scan_is_a_problem_doctor_would_not_otherwise_have_counted(
    planted: Path,
) -> None:
    """The verdict changes ``doctor``'s health, and it is the only thing that did.

    A tmp project reports plenty of MISSING setup steps, so ``healthy`` is False
    either way and asserting it proves nothing. The delta does: removing the scan
    record -- derived state that no state hash covers -- leaves every other step
    exactly where it was, so a ``problemCount`` that drops by one is this arm and
    nothing else.
    """
    assert _in(planted, "index", "build")[0] != 0
    _, degraded = _in(planted, "doctor")

    record = ProjectPaths.of(planted).index_secret_scan
    assert record.is_file(), "the build published no scan record for doctor to read"
    record.unlink()
    _, without = _in(planted, "doctor")

    assert without["indexSecretScan"]["status"] == "unrecorded", without["indexSecretScan"]
    assert degraded["problemCount"] == without["problemCount"] + 1, (
        f"the degraded verdict is not counted as a problem: "
        f"{degraded['problemCount']} against {without['problemCount']}"
    )


# -- AC-3: warn publishes, reports, and exits zero ------------------------------


def test_warn_publishes_the_finding_as_information_and_exits_zero(planted: Path) -> None:
    """AC-3: the same finding, the same published index, a zero exit."""
    _write_policy(planted, "warn")

    code, payload = _in(planted, "index", "build")

    assert code == 0, payload
    assert payload["published"] is True, payload
    assert payload["secretScanPolicy"] == "warn", payload
    assert any(DIRTY_ITEM in line for line in _findings(payload)), payload


def test_doctor_reports_a_warn_finding_without_counting_it_as_a_problem(
    planted: Path,
) -> None:
    """``warn`` is the operator's stated choice, so it informs rather than fails.

    The same delta as the degraded case, expected to be zero: an operator who set
    ``warn`` asked for the finding to be surfaced, not for the machine to be
    called unhealthy over it.
    """
    _write_policy(planted, "warn")
    assert _in(planted, "index", "build")[0] == 0
    _, warned = _in(planted, "doctor")

    ProjectPaths.of(planted).index_secret_scan.unlink()
    _, without = _in(planted, "doctor")

    assert warned["indexSecretScan"]["status"] == "warned", warned["indexSecretScan"]
    assert warned["problemCount"] == without["problemCount"], (
        "a warn-policy finding must not change doctor's problem count"
    )


# -- The policy knob, and what an empty list means under each posture -----------


def test_off_scans_nothing_and_says_so(planted: Path) -> None:
    """``off`` is distinguishable from ``clean``, which is why the policy rides along.

    The value is quoted because YAML 1.1 reads a bare ``off`` as ``False``, which
    ``read_secret_scan_policy`` refuses rather than guesses at.
    """
    _write_policy(planted, '"off"')

    code, payload = _in(planted, "index", "build")

    assert code == 0, payload
    assert payload["secretScanPolicy"] == "off", payload
    assert _findings(payload) == [], payload

    _, doctor = _in(planted, "doctor")
    assert doctor["indexSecretScan"]["status"] == "unscanned", doctor["indexSecretScan"]


def test_a_configuration_the_build_cannot_read_refuses_the_build(planted: Path) -> None:
    """A typo about a security control surfaces, rather than selecting a policy.

    The same rule ``propose accept`` holds: absent means ``block``, unrecognised
    means refuse. Guessing would hide the mistake behind the behaviour that makes
    it invisible.
    """
    _write_policy(planted, "warm")

    code, payload = _in(planted, "index", "build")

    assert code != 0, payload
    assert "secretScan" in payload["error"], payload
    assert "block" in payload["remedy"], payload


# -- AC-4: the report is bounded and redacted ----------------------------------


def test_no_build_or_doctor_output_carries_the_planted_credential(planted: Path) -> None:
    """AC-4: not the value, and not one character past the published bound.

    ``SecretFinding`` quotes at most :data:`REDACTED_PREFIX_CHARS` characters, so
    a report holding one more than that is a report that walked around the bound.
    Both streams of both commands, with and without ``--json``, because the human
    rendering is a second surface through a different formatter.
    """
    quoted = PLANTED[: REDACTED_PREFIX_CHARS + 1]

    surfaces = {
        "index build (rendered)": _streams(planted, "index", "build"),
        "index build (json)": json.dumps(_in(planted, "index", "build")[1]),
        "doctor (rendered)": _streams(planted, "doctor"),
        "doctor (json)": json.dumps(_in(planted, "doctor")[1]),
        "doctor --report": _streams(planted, "doctor", "--report"),
        "the scan record": ProjectPaths.of(planted).index_secret_scan.read_text(encoding="utf-8"),
    }

    for name, text in surfaces.items():
        assert PLANTED not in text, f"{name} echoed the planted credential"
        assert quoted not in text, (
            f"{name} quotes {REDACTED_PREFIX_CHARS + 1} characters of the match, past the "
            f"{REDACTED_PREFIX_CHARS}-character bound the detector publishes"
        )


def test_doctor_names_no_item_and_no_path_in_the_scan_it_reports(planted: Path) -> None:
    """``doctor --report`` is pasted into public issues, so the arm is constant-shaped.

    The count and the policy are what an operator needs to act; *which* item
    carries the credential varies with content the reader of a pasted report has
    no business learning, and it is the build's own terminal output that names it.
    """
    assert _in(planted, "index", "build")[0] != 0

    _, payload = _in(planted, "doctor", "--report")

    rendered = json.dumps(payload["indexSecretScan"])
    assert DIRTY_ITEM not in rendered, f"doctor named the item that carries the secret: {rendered}"
    assert str(planted) not in rendered, f"doctor published an absolute path: {rendered}"


#: How many credentials each of the two crowded bodies carries.
#:
#: **Neither body may reach :data:`MAX_FINDINGS` on its own**, and that is what
#: makes the ceiling's *scope* testable rather than only its value. With one body
#: carrying forty, a per-body budget and a per-build budget both answer twenty --
#: the outer ``len(found) < MAX_FINDINGS`` guard stops the walk either way -- so
#: the mutation that widens ``room`` from the remaining budget to the whole
#: ceiling survived (measured 2026-09-03). Twelve and twelve separates them: the
#: build's budget answers 20, and a per-body one answers 24.
_CROWDED_PER_BODY = 12


def _crowded(marker: str) -> str:
    """A body carrying :data:`_CROWDED_PER_BODY` distinct credential-shaped strings.

    Each is ``AKIA`` plus **exactly** sixteen upper-case characters, joined at run
    time for the reason :data:`PLANTED` records, and each is distinct so the
    detector reports one per line rather than folding them together.

    The tail's length is padded and asserted rather than counted by hand. A
    fifteen-character tail matches no family at all, so the body would be clean
    and the ceiling assertion below would pass against a build that had stopped
    scanning entirely -- which is what a first draft of this fixture did (0
    findings, measured 2026-09-03).
    """
    lines = []
    for index in range(_CROWDED_PER_BODY):
        tail = f"{marker}EXAMPLE{index:02d}".ljust(16, "Z")
        assert len(tail) == 16, f"the fixture's tail is {len(tail)} characters, not sixteen"
        lines.append(f"Retired key {'AKIA'}{tail}.\n")
    return f"# Crowded {marker}\n\n" + "".join(lines)


def test_the_report_is_bounded_across_every_body_by_the_detectors_ceiling(bare: Path) -> None:
    """One budget for the build, not one per document.

    A corpus can carry any number of credential-shaped strings; the report lists
    at most :data:`MAX_FINDINGS` of them **across every body**, which is the
    ceiling the accept path applies to its own five channels rather than to each.
    Two bodies of twelve is what tells the two readings apart -- see
    :data:`_CROWDED_PER_BODY`.
    """
    _corpus(bare, dirty=_crowded("A"))
    (bare / ".theurian/knowledge/architecture/unreleased-keys.md").write_text(
        _crowded("B"), encoding="utf-8"
    )
    (bare / f".theurian/migrations/{_DRAFT_MIGRATION_ID}-second.yaml").write_text(
        _migration(
            _DRAFT_MIGRATION_ID,
            DRAFT_ITEM,
            "01K1CREVCC01234567890ABCDE",
            "Second crowded body",
            "approved",
            _crowded("B"),
        ),
        encoding="utf-8",
    )
    _must(bare, "migrate", "apply")

    _, payload = _in(bare, "index", "build")

    lines = _findings(payload)
    assert 2 * _CROWDED_PER_BODY > MAX_FINDINGS, (
        "the fixture no longer plants more than the ceiling, so the assertion below "
        "would hold against a build that applied no ceiling at all"
    )
    assert len(lines) == MAX_FINDINGS, (
        f"the report is not bounded by the detector's ceiling across bodies: {len(lines)} findings"
    )


# -- The published count is a function of the indexed population ---------------


def test_a_row_the_build_withheld_is_never_scanned_and_never_counted(bare: Path) -> None:
    """The count must not move with content this build refused to index.

    A ``draft`` item is not written into a default build at all, so the scan --
    which sits inside the build's own loop, after both filters -- never reads it.
    A count computed before those filters would publish the existence of a
    withheld row through a number, which is the shape T-17 took.
    """
    _corpus(bare, dirty=CLEAN_BODY.replace("Authentication", "Rotation"))
    (bare / ".theurian/knowledge/architecture/unreleased-keys.md").write_text(
        DIRTY_BODY, encoding="utf-8"
    )
    (bare / f".theurian/migrations/{_DRAFT_MIGRATION_ID}-unreleased.yaml").write_text(
        _migration(
            _DRAFT_MIGRATION_ID,
            DRAFT_ITEM,
            "01K1CREVCC01234567890ABCDE",
            "Unreleased keys",
            "draft",
            DIRTY_BODY,
        ),
        encoding="utf-8",
    )
    _must(bare, "migrate", "apply")

    code, default_build = _in(bare, "index", "build")
    assert code == 0, default_build
    assert _findings(default_build) == [], (
        f"a draft this build never indexed moved the published count: {default_build}"
    )

    code, opted_in = _in(bare, "index", "build", "--include-unapproved")

    assert code != 0, "the same body under `--include-unapproved` is indexed, so it is scanned"
    assert any(DRAFT_ITEM in line for line in _findings(opted_in)), (
        f"the control is blind rather than the row being absent: {opted_in}"
    )


# -- The signal has to clear, and it has to say so when it cannot -------------

#: The migration that runs the remedy the refusal names: a new ``upsertRevision``
#: superseding the body that carried the credential.
#:
#: ``expectedRevision`` is not decoration here. An ``upsertRevision`` against an
#: item that already has one raises ``RevisionConflictError`` without it
#: (ADR-0006), which is how the first attempt at this fixture failed against the
#: real CLI -- so a remedy naming "supersede the revision" is only followed
#: correctly with it, and the fixture follows the remedy rather than a shortcut.
_ROTATED_BODY = (
    "# Legacy key rotation\n\nThe staging key was rotated out of band. Nothing here is a key.\n"
)
_ROTATION_MIGRATION_ID = "01K1DDDDDD01234567890ABCDE"


def _supersede_the_dirty_body(root: Path) -> None:
    """Land a clean revision over the one carrying :data:`PLANTED`."""
    (root / ".theurian/knowledge/architecture/legacy-keys-v2.md").write_text(
        _ROTATED_BODY, encoding="utf-8"
    )
    (root / f".theurian/migrations/{_ROTATION_MIGRATION_ID}-rotate.yaml").write_text(
        f"""apiVersion: theurian.dev/v1
id: {_ROTATION_MIGRATION_ID}
createdAt: 2026-09-03T11:00:00+09:00
author: engineer@example.com
operations:
  - op: upsertRevision
    itemId: {DIRTY_ITEM}
    revisionId: 01K1DREVDD01234567890ABCDE
    expectedRevision: 01K1BREVBB01234567890ABCDE
    contentFile: ../knowledge/architecture/legacy-keys-v2.md
    contentSha256: {body_pin(_ROTATED_BODY)}
    metadata:
      title: Legacy key rotation
      contentType: text/markdown
      kind: architecture
      namespace: backend
      status: approved
      owner: platform-team
      trustLevel: reviewed
      sourceAnchors:
        - provider: git
          sourceUri: git://demo/legacy-keys-v2.md
""",
        encoding="utf-8",
    )
    _must(root, "migrate", "apply")


def test_following_the_remedy_clears_the_degraded_verdict(planted: Path) -> None:
    """A signal that never clears is worse than no signal.

    ``block`` publishes the index and leaves ``doctor`` unhealthy, so the operator
    needs the state to come back on its own once the corpus is fixed. Nothing
    clears it but a build that finds nothing: the record is rewritten on every
    publish, including a clean one, which is the whole reason it is written then
    rather than only on trouble.

    The corpus is fixed by *running the remedy the refusal prints* -- superseding
    the revision -- rather than by editing the applied migration, which the
    checksum guard refuses. So this is also the check that the remedy names a
    route that exists.
    """
    assert _in(planted, "index", "build")[0] != 0
    assert _in(planted, "doctor")[1]["indexSecretScan"]["status"] == "degraded"

    _supersede_the_dirty_body(planted)
    code, rebuilt = _in(planted, "index", "build")

    assert code == 0, rebuilt
    assert _findings(rebuilt) == [], rebuilt
    assert "remedy" not in rebuilt, (
        f"a clean build still carries a remedy, so there is nothing to act on: {rebuilt}"
    )
    assert _in(planted, "doctor")[1]["indexSecretScan"] == {
        "status": "clean",
        "policy": "block",
        "findings": 0,
    }


def test_a_record_that_names_another_build_is_read_as_unrecorded(planted: Path) -> None:
    """Honest ignorance, not a clean bill.

    A withdrawal-triggered purge republishes the pointer at a build this control
    never wrote a record for, and a reader that took the record at face value
    would report a verdict about a build that is no longer served. The id is
    compared rather than trusted, so the answer is "nothing is known" -- which is
    not counted as a problem, because it is not evidence of one.
    """
    assert _in(planted, "index", "build")[0] != 0
    record = ProjectPaths.of(planted).index_secret_scan
    stale = json.loads(record.read_text(encoding="utf-8"))
    stale["indexBuildId"] = "01K1ZZZZZZ01234567890ABCDE"
    record.write_text(json.dumps(stale), encoding="utf-8")

    _, payload = _in(planted, "doctor")

    assert payload["indexSecretScan"] == {
        "status": "unrecorded",
        "policy": None,
        "findings": 0,
    }


#: One row per field of the record that can be wrong, with the shape that makes
#: it wrong. ``{build}`` is filled in with the id the pointer actually names.
#:
#: **Every row is wrong about exactly one thing, and that took two measurements.**
#: A record has several guards in sequence, so a row wrong about two fields is
#: rejected by whichever guard runs first and asserts nothing about the other:
#:
#: * written with a *placeholder* build id, every row was rejected by the build-id
#:   comparison before its own field was read -- deleting the ``bool`` guard left
#:   the boolean row green (2026-09-03, mutation M3 SURVIVED);
#: * with the id fixed but ``findings`` *omitted* from the bad-policy row, the
#:   findings guard rejected it first -- coercing an unrecognised policy to
#:   ``block`` left that row green too (2026-09-03, M3b SURVIVED).
#:
#: So each row names the published build **and** carries a well-formed value for
#: every field except the one it is about.
_UNTRUSTWORTHY_RECORDS: tuple[tuple[str, str], ...] = (
    # No braces in this one: the contents go through `str.format`, and a literal
    # `{` there is a format field the row cannot express.
    ("not JSON at all", "not json, just prose"),
    ("a JSON array", "[]"),
    (
        "a policy this build does not recognise",
        '{{"indexBuildId": "{build}", "policy": "warm", "findings": 1}}',
    ),
    (
        "a boolean findings count",
        '{{"indexBuildId": "{build}", "policy": "block", "findings": true}}',
    ),
    (
        "a negative findings count",
        '{{"indexBuildId": "{build}", "policy": "block", "findings": -1}}',
    ),
    ("no build id", '{{"policy": "block", "findings": 1}}'),
)

# A row this table deliberately does not carry, recorded because it was written
# and then removed rather than never considered. `{"indexBuildId": " ", ...}` is
# refused by `_read_record`'s own `.strip()` -- defensive parity with
# `read_active_index_pointer` -- but that guard is *unreachable*: a blank id can
# never equal the published one, so `published_index_secret_scan` answers
# `unrecorded` from the comparison whatever `_read_record` decided. Measured
# 2026-09-03: deleting the `.strip()` left the row green, which makes it a row
# that cannot fail rather than a guard that is held. The behaviour it looked like
# it covered is covered by
# `test_a_record_that_names_another_build_is_read_as_unrecorded`.


@pytest.mark.parametrize(
    ("label", "template"), _UNTRUSTWORTHY_RECORDS, ids=[row[0] for row in _UNTRUSTWORTHY_RECORDS]
)
def test_a_record_that_cannot_be_trusted_whole_reports_nothing_known(
    planted: Path, label: str, template: str
) -> None:
    """Every field is checked rather than coerced, and a bad one costs the record.

    The file is derived, git-ignored and local, so this is not a security
    boundary -- anything that can rewrite it can delete the index. What it is is
    the rule ``read_secret_scan_policy`` holds one layer up: a value the enum does
    not contain is a mistake somebody made about a security control, and reading a
    policy out of it would be inventing one. ``findings: true`` is listed because
    ``isinstance(True, int)`` is ``True`` in Python and would otherwise count as
    one finding.

    Each row names the **published** build, so the field under test is the only
    thing wrong with it; :data:`_UNTRUSTWORTHY_RECORDS` records the mutation that
    proved a placeholder id made most of these rows assert nothing.
    """
    assert _in(planted, "index", "build")[0] != 0
    paths = ProjectPaths.of(planted)
    pointer = read_active_index_pointer(paths).payload
    assert pointer is not None, "the build published no pointer for the record to name"
    paths.index_secret_scan.write_text(
        template.format(build=pointer["indexBuildId"]), encoding="utf-8"
    )

    _, payload = _in(planted, "doctor")

    assert payload["indexSecretScan"]["status"] == "unrecorded", (
        f"{label} was read as a verdict: {payload['indexSecretScan']}"
    )


def test_doctor_outside_a_project_says_there_is_nothing_to_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``doctor`` runs from anywhere, and the machine-wide half is the point.

    A verdict of ``clean`` here would be a claim about an index that does not
    exist, and a raise would take out a diagnostic whose whole job is to report on
    a broken machine.
    """
    monkeypatch.setenv("THEURIAN_DATA_DIR", str(tmp_path / "datadir"))
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()

    _, payload = _in(elsewhere, "doctor")

    assert payload["indexSecretScan"] == {
        "status": "not-applicable",
        "policy": None,
        "findings": 0,
    }


@pytest.mark.skipif(sys.platform == "win32", reason="symlinks need privileges on Windows")
def test_doctor_still_answers_when_the_state_directory_escapes_the_project(
    planted: Path,
) -> None:
    """A diagnostic must come back with a verdict, and a broken tree is when it is run.

    A clone can deliver ``.theurian/state`` as a symbolic link pointing out of
    the working tree (#237, T-5), and every ``ProjectPaths`` helper under it then
    refuses. The scan arm reaches two of those helpers, so guarding only
    ``ProjectPaths.of`` let a ``ProjectError`` out of ``active_index_pointer`` --
    a Rich traceback and **empty stdout under ``--json``**, for a tree the
    previous build reported on completely (measured 2026-09-03, and the reason
    this test exists rather than the docstring's "never raises" being taken at
    its word).

    The verdict is ``not-applicable``: nothing can be said about a published
    build that cannot be located. It is not counted as a problem, because the
    problem is the symlink and other surfaces are what name it.
    """
    assert _in(planted, "index", "build")[0] != 0
    state = planted / ".theurian/state"
    outside = planted.parent / "outside-the-tree"
    outside.mkdir()
    shutil.rmtree(state)
    state.symlink_to(outside, target_is_directory=True)

    code, payload = _in(planted, "doctor")

    assert payload, "doctor produced no payload at all, which is the CP-2 escape"
    assert payload["indexSecretScan"] == {
        "status": "not-applicable",
        "policy": None,
        "findings": 0,
    }
    assert code == 1, "an escaping tree is still an unhealthy machine"
