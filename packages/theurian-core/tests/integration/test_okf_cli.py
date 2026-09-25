"""``theurian okf import``, invoked in-process (ADR-0037).

The adapter's own tests: that the option surface reaches the service, that a
refusal arrives as JSON with a remedy rather than a traceback, and that the
JSON payload names what a caller needs -- the drafted proposal ids, the
per-kind refusal counts, and each refusal's kind, key and literal string. The
service's own acceptance criteria are ``test_okf_import.py``'s.
"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from theurian.cli.main import app

pytestmark = pytest.mark.integration

runner = CliRunner()

EXIT_INVALID_INPUT = 2

_VANILLA_CONCEPT = """---
type: decision
title: A vanilla concept
status: stable
---

Body prose with no theurian_* keys at all.
"""

_BUNDLE_OPTIONS: tuple[str, ...] = (
    "--owner",
    "platform-team",
    "--author",
    "dana@example.com",
    "--agent-id",
    "claude-code",
    "--task-id",
    "task-okf",
    "--model",
    "claude-opus-5",
    "--reasoning",
    "Importing a bundle a teammate shared.",
)


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    root = tmp_path / "demo"
    root.mkdir()
    for args in (
        ["git", "init", "-q", "-b", "main"],
        ["git", "config", "user.email", "test@example.com"],
        ["git", "config", "user.name", "Test"],
    ):
        subprocess.run(args, cwd=root, check=True, capture_output=True)  # noqa: S603

    monkeypatch.setenv("THEURIAN_DATA_DIR", str(tmp_path / "datadir"))
    monkeypatch.chdir(root)
    _invoke("init")
    yield root


def _invoke(*args: str) -> tuple[int, dict[str, Any]]:
    result = runner.invoke(app, [*args, "--json"], catch_exceptions=False)
    stream = result.stdout if result.exit_code == 0 else (result.stderr or result.stdout)
    return result.exit_code, json.loads(stream) if stream.strip() else {}


def _write(bundle: Path, relative: str, text: str) -> None:
    target = bundle / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")


def test_okf_import_reports_the_drafted_proposal_and_the_counts(project: Path) -> None:
    bundle = project.parent / "bundle"
    _write(bundle, "vanilla.md", _VANILLA_CONCEPT)

    code, payload = _invoke("okf", "import", str(bundle), *_BUNDLE_OPTIONS)

    assert code == 0, payload
    assert payload["conceptsAdmitted"] == 1
    assert payload["refusalsByKind"] == {}
    assert payload["refusals"] == []
    assert len(payload["proposalIds"]) == 1


def test_okf_import_reports_a_refusal_by_kind_key_and_literal_never_a_resolved_path(
    project: Path,
) -> None:
    bundle = project.parent / "bundle"
    _write(
        bundle,
        "bad.md",
        "---\n"
        "type: decision\n"
        "title: Bad body file\n"
        "status: stable\n"
        "theurian_content_type: application/json\n"
        "theurian_body_file: ../../../../etc/passwd\n"
        "---\n\nbody\n",
    )
    _write(bundle, "vanilla.md", _VANILLA_CONCEPT)

    code, payload = _invoke("okf", "import", str(bundle), *_BUNDLE_OPTIONS)

    assert code == 0, payload
    assert payload["conceptsAdmitted"] == 1
    assert payload["refusalsByKind"] == {"reference": 1}
    [refusal] = payload["refusals"]
    assert refusal == {
        "kind": "reference",
        "key": "theurian_body_file",
        "literal": "../../../../etc/passwd",
    }
    assert str(project) not in json.dumps(payload)


def test_okf_import_text_output_renders_each_refusal_as_its_own_line(project: Path) -> None:
    """`_emit`'s text renderer prints an untouched list entry with `str()`,
    which would read as a Python dict repr for a refusal; the command formats
    each one into a line first.
    """
    bundle = project.parent / "bundle"
    _write(
        bundle,
        "bad.md",
        "---\n"
        "type: decision\n"
        "title: Bad body file\n"
        "status: stable\n"
        "theurian_content_type: application/json\n"
        "theurian_body_file: ../../../../etc/passwd\n"
        "---\n\nbody\n",
    )
    _write(bundle, "vanilla.md", _VANILLA_CONCEPT)

    result = runner.invoke(
        app, ["okf", "import", str(bundle), *_BUNDLE_OPTIONS], catch_exceptions=False
    )

    assert result.exit_code == 0, result.stdout
    assert "  - reference theurian_body_file: ../../../../etc/passwd" in result.stdout
    assert "{'kind'" not in result.stdout


def test_okf_import_item_filter_admits_only_the_named_concept(project: Path) -> None:
    bundle = project.parent / "bundle"
    _write(bundle, "vanilla.md", _VANILLA_CONCEPT)
    _write(
        bundle,
        "other.md",
        "---\ntype: decision\ntitle: Another concept\nstatus: stable\n---\n\nbody\n",
    )

    code, payload = _invoke("okf", "import", str(bundle), *_BUNDLE_OPTIONS, "--item", "vanilla")

    assert code == 0, payload
    assert payload["conceptsAdmitted"] == 1


def test_okf_import_over_the_operation_cap_refuses_with_a_remedy_naming_item(
    project: Path,
) -> None:
    bundle = project.parent / "bundle"
    for index in range(126):
        _write(
            bundle,
            f"concept-{index}.md",
            f"---\ntype: decision\ntitle: Concept {index}\nstatus: stable\n---\n\nbody\n",
        )

    code, payload = _invoke("okf", "import", str(bundle), *_BUNDLE_OPTIONS)

    assert code == EXIT_INVALID_INPUT
    assert "--item" in payload["remedy"]
