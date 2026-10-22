"""The four places that quote ``FixCommitCheck.verify``'s git command quote what ships (#985).

Prose that quotes the argv drifts the moment a token is added: the module docstring,
the T-7 entry in the threat model, ``PROCESS_SPAWN_SITES``'s note and the CHANGELOG each went stale
when ``--shallow-file``, ``log.mailmap=false`` and ``--no-renames`` joined the vector.
The shipped vector is the argv one real ``verify`` spawns, not restated here, so adding a
token reddens every quotation that has not followed.

Key: the span a quotation delimits, compared token for token. ``<sha>`` and
``<file_path>`` stand for the two runtime values; the empty string is quoted ``""``.
The CHANGELOG is read from its ``## [Unreleased]`` section only, so released history
never has to move; ``test_threat_model_t7_claims`` holds the same quote as a token set.
"""

from __future__ import annotations

import ast
import re
import subprocess
from pathlib import Path
from typing import Any

import pytest

from theurian.infrastructure.git.fix_commit_check import FixCommitCheck

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[4]
SOURCE = ROOT / "packages/theurian-core/src/theurian/infrastructure/git/fix_commit_check.py"
OPENING = "git -c core.precomposeunicode=false "
SHA = "a" * 40


def _shipped_vector(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> list[str]:
    """The argv one real ``verify`` spawns, with the two runtime values named."""
    calls: list[list[str]] = []
    real = subprocess.run

    def recording(args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        calls.append(list(args))
        return real(args, **kwargs)

    monkeypatch.setattr(subprocess, "run", recording)
    FixCommitCheck(tmp_path).verify(SHA, "<file_path>")
    (argv,) = calls
    return ["<sha>^{commit}" if t == f"{SHA}^{{commit}}" else t or '""' for t in argv[1:]]


def _docstring_quotations(tree: ast.Module) -> list[str]:
    docstring = ast.get_docstring(tree) or ""
    return [docstring.split("shipped form is ``", 1)[1].split("``", 1)[0]]


def _threat_model_quotations(_tree: ast.Module) -> list[str]:
    text = (ROOT / "docs/security/threat-model.md").read_text(encoding="utf-8")
    return re.findall(rf"`({re.escape(OPENING)}[^`]+)`", text)


def _spawn_sites_note_quotations(_tree: ast.Module) -> list[str]:
    lines = (ROOT / "packages/theurian-core/tests/unit/test_network_call_sites.py").read_text(
        encoding="utf-8"
    )
    note = " ".join(line[2:].strip() for line in lines.splitlines() if line.startswith("#:"))
    return re.findall(rf"``({re.escape(OPENING)}.+?)``", note)


def _changelog_quotations(_tree: ast.Module) -> list[str]:
    text = (ROOT / "packages/theurian-core/CHANGELOG.md").read_text(encoding="utf-8")
    unreleased = text.split("## [Unreleased]", 1)[1].split("\n## [", 1)[0]
    return re.findall(rf"`({re.escape(OPENING)}[^`]+)`", unreleased)


QUOTATIONS = {
    "module-docstring": _docstring_quotations,
    "threat-model-T-7": _threat_model_quotations,
    "spawn-sites-note": _spawn_sites_note_quotations,
    "changelog-unreleased": _changelog_quotations,
}


@pytest.mark.parametrize("where", QUOTATIONS)
def test_a_quotation_of_the_verify_command_is_the_command_that_ships(
    where: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    shipped = _shipped_vector(monkeypatch, tmp_path)
    assert len(shipped) > 15 and "--shallow-file" in shipped, "the extractor read no real vector"
    quotations = QUOTATIONS[where](tree)
    assert quotations, f"{where} no longer quotes the command, or the key stopped matching it"

    for quoted in quotations:
        assert quoted.split() == ["git", *shipped], quoted
