"""Contract tests: the CLI JSON surface the plugin depends on.

These invoke the **installed** ``theurian`` executable as a subprocess rather
than importing it. That is deliberate: the plugin's real integration path is a
process boundary, and a test that imports Core would pass even if the entry
point, the console script, or the packaging were broken.

Both artifacts consume ``schemas/``; neither owns the other (ADR-0001).
"""

from __future__ import annotations

import json
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
