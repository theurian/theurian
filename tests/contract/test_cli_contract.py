"""Contract tests: the CLI JSON surface the plugin depends on.

These invoke the **installed** ``theurian`` executable as a subprocess rather
than importing it. That is deliberate: the plugin's real integration path is a
process boundary, and a test that imports Core would pass even if the entry
point, the console script, or the packaging were broken.

Both artifacts consume ``schemas/``; neither owns the other (ADR-0001).
"""

from __future__ import annotations

import hashlib
import json
import os
import pathlib
import shutil
import subprocess
import sys

import pytest
from jsonschema import Draft202012Validator

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
SCHEMAS = REPO_ROOT / "schemas"

#: Core's exit code for a compatibility mismatch (see theurian.cli.main).
EXIT_INCOMPATIBLE = 3
EXIT_INVALID_INPUT = 2

THEURIAN = shutil.which("theurian")

pytestmark = [
    pytest.mark.contract,
    pytest.mark.skipif(THEURIAN is None, reason="theurian is not installed on PATH"),
]


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    """Invoke the installed CLI. Never falls back to `python -m`."""
    if THEURIAN is None:  # pragma: no cover - guarded by the skip below
        pytest.skip("theurian is not installed on PATH")
    return subprocess.run(  # noqa: S603 - fixed argv, no shell
        [THEURIAN, *args],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


def _schema(relative: str) -> Draft202012Validator:
    return Draft202012Validator(json.loads((SCHEMAS / relative).read_text(encoding="utf-8")))


# -- Version -------------------------------------------------------------


def test_version_json_matches_the_published_schema() -> None:
    result = _run("version", "--json")
    assert result.returncode == 0, result.stderr
    _schema("cli/version.schema.json").validate(json.loads(result.stdout))


def test_version_flag_and_subcommand_agree() -> None:
    """Two spellings of the same question must not drift apart."""
    assert json.loads(_run("--version", "--json").stdout) == json.loads(
        _run("version", "--json").stdout
    )


def test_json_output_is_the_only_thing_on_stdout() -> None:
    """A caller must be able to pipe stdout straight into a JSON parser."""
    result = _run("version", "--json")
    json.loads(result.stdout)  # raises if anything else was printed


def test_core_reports_the_protocol_version() -> None:
    payload = json.loads(_run("version", "--json").stdout)
    assert payload["protocolVersion"].startswith("theurian/v")


# -- Compatibility -------------------------------------------------------


def _compat(
    *,
    plugin: str = "0.1.0",
    minimum: str = "0.1.0-dev.0",
    maximum: str = "0.2.0",
    protocol: str = "theurian/v1",
) -> subprocess.CompletedProcess[str]:
    return _run(
        "compat",
        "check",
        "--plugin-version",
        plugin,
        "--core-minimum",
        minimum,
        "--core-maximum-exclusive",
        maximum,
        "--protocol-version",
        protocol,
        "--json",
    )


def test_compatible_declaration_exits_zero() -> None:
    result = _compat()
    assert result.returncode == 0, result.stdout
    assert json.loads(result.stdout)["compatible"] is True


def test_incompatible_declaration_uses_a_distinct_exit_code() -> None:
    """A caller must be able to tell "incompatible" from "the command failed"."""
    result = _compat(minimum="99.0.0", maximum="100.0.0")
    assert result.returncode == EXIT_INCOMPATIBLE
    assert json.loads(result.stdout)["outcome"] == "core-too-old"


def test_protocol_mismatch_is_reported_distinctly() -> None:
    result = _compat(protocol="theurian/v99")
    assert result.returncode == EXIT_INCOMPATIBLE
    assert json.loads(result.stdout)["outcome"] == "protocol-mismatch"


def test_malformed_declaration_is_a_different_failure_than_incompatibility() -> None:
    result = _compat(minimum="not-a-version")
    assert result.returncode == EXIT_INVALID_INPUT
    assert json.loads(result.stdout)["outcome"] == "invalid-declaration"


def test_every_incompatible_verdict_carries_an_actionable_remedy() -> None:
    cases: list[dict[str, str]] = [
        {"minimum": "99.0.0", "maximum": "100.0.0"},
        {"minimum": "0.0.1", "maximum": "0.0.2"},
        {"protocol": "theurian/v99"},
    ]
    for kwargs in cases:
        payload = json.loads(_compat(**kwargs).stdout)
        assert payload["compatible"] is False
        assert payload["remedy"].strip(), f"no remedy for {kwargs}"


def test_core_performs_the_comparison_so_clients_never_reimplement_it() -> None:
    """Core translates its own PEP 440 version into SemVer semantics.

    If a client had to do this, every client would do it slightly differently,
    and a development build would look like "Core not installed".
    """
    core_version = json.loads(_run("version", "--json").stdout)["version"]
    assert ".dev" in core_version or "-" not in core_version
    assert _compat().returncode == 0


# -- Shape stability -----------------------------------------------------


def test_no_command_writes_diagnostics_to_stdout() -> None:
    """Warnings belong on stderr; stdout is the machine channel."""
    result = _compat(minimum="99.0.0", maximum="100.0.0")
    json.loads(result.stdout)


def test_cli_is_reachable_as_an_installed_console_script() -> None:
    """Guards packaging: the plugin invokes `theurian`, not `python -m theurian`."""
    assert THEURIAN is not None
    assert pathlib.Path(THEURIAN).exists()
    assert _run("--version", "--json").returncode == 0


def test_cli_runs_on_the_supported_python() -> None:
    payload = json.loads(_run("version", "--json").stdout)
    major, minor, _ = payload["python"].split(".")
    assert (int(major), int(minor)) >= (3, 13)
    assert sys.version_info >= (3, 13)


# -- Exit codes the plugin branches on ------------------------------------

#: ``theurian index build``'s exit when it **published** an index and something
#: in it is credential-shaped under a ``block`` policy (SEC-11, #329).
#:
#: Pinned here because this is the boundary the plugin reads. Its three commands
#: -- ``index.md``, ``reindex.md``, ``propose.md`` -- branch on the number, and
#: the two outcomes it separates are opposite: 1 means nothing was published and
#: retrieval still uses the previous build, 6 means a *complete* index was
#: published and something in it needs rotating. Collapsing them makes a job that
#: stops on a secret also stop on a corrupt state database.
EXIT_SECRET_FOUND = 6

_SECRET = "AKIA" + "EXAMPLE012345678"
_BODY = f"# Legacy keys\n\nThe retired staging account used {_SECRET}. Rotate it.\n"
_MIGRATION = f"""apiVersion: theurian.dev/v1
id: 01K1AAAAAA01234567890ABCDE
createdAt: 2026-09-03T10:00:00+09:00
author: engineer@example.com
operations:
  - op: createItem
    itemId: architecture.legacy-keys
    kind: architecture
    namespace: backend
    owner: platform-team
  - op: upsertRevision
    itemId: architecture.legacy-keys
    revisionId: 01K1BREVBB01234567890ABCDE
    contentFile: ../knowledge/architecture/legacy-keys.md
    contentSha256: {hashlib.sha256(_BODY.encode("utf-8")).hexdigest()}
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
          sourceUri: git://demo/legacy-keys.md
"""


def _in_project(
    root: pathlib.Path, home: pathlib.Path, *args: str
) -> subprocess.CompletedProcess[str]:
    """Invoke the installed CLI **in** ``root``, with the machine state redirected.

    The working directory and the environment are set on this call and never
    inherited from an earlier one: every project command resolves the project
    from ``Path.cwd()``, and a leaked ``chdir`` initialises Theurian into
    whichever tree the test runner started in.
    """
    if THEURIAN is None:  # pragma: no cover - guarded by the module-level skip
        pytest.skip("theurian is not installed on PATH")
    environment = {
        **os.environ,
        "HOME": str(home),
        "THEURIAN_DATA_DIR": str(home / "datadir"),
    }
    return subprocess.run(  # noqa: S603 - fixed argv, no shell
        [THEURIAN, *args],
        cwd=root,
        env=environment,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )


def test_a_published_index_carrying_a_secret_exits_six_not_one(tmp_path: pathlib.Path) -> None:
    """Exit 6 is the plugin's signal that a build **succeeded** and needs attention.

    A row here rather than only in Core's own suite because the number crosses a
    process boundary: the plugin runs `theurian index build` and reads
    ``returncode``, so a change that collapsed 6 into 1 would be invisible to
    every in-process test while turning "published, rotate this" into "the build
    failed" at the consumer. Mutating the constant to 1 reddens exactly here.

    The payload is asserted with it: a caller that branches on 6 has to find the
    findings on **stdout**, because they are the only account of what was found.
    """
    home = tmp_path / "home"
    home.mkdir()
    root = tmp_path / "demo"
    root.mkdir()
    for argv in (
        ["git", "init", "-q", "-b", "main"],
        ["git", "config", "user.email", "contract@example.com"],
        ["git", "config", "user.name", "Contract"],
    ):
        subprocess.run(argv, cwd=root, check=True, capture_output=True)  # noqa: S603

    assert _in_project(root, home, "init").returncode == 0
    assert _in_project(root, home, "project", "register").returncode == 0
    (root / ".theurian/knowledge/architecture/legacy-keys.md").write_text(_BODY, encoding="utf-8")
    (root / ".theurian/migrations/01K1AAAAAA01234567890ABCDE-legacy.yaml").write_text(
        _MIGRATION, encoding="utf-8"
    )
    applied = _in_project(root, home, "migrate", "apply", "--json")
    assert applied.returncode == 0, applied.stderr

    result = _in_project(root, home, "index", "build", "--json")

    assert result.returncode == EXIT_SECRET_FOUND, (
        f"a published index carrying a secret exited {result.returncode}; the plugin reads "
        f"1 as 'nothing was published' and would report a complete build as a failure. "
        f"{result.stdout}{result.stderr}"
    )
    payload = json.loads(result.stdout)
    assert payload["published"] is True, payload
    assert payload["secretScanPolicy"] == "block", payload
    assert any("architecture.legacy-keys" in line for line in payload["secretFindings"]), payload
    assert _SECRET not in result.stdout + result.stderr, "the exit-6 surface echoed the credential"
