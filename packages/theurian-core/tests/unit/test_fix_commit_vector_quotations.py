"""The three places that quote ``FixCommitCheck.verify``'s git command quote what ships (#985).

Prose that quotes the argv drifts the moment a token is added: the module docstring,
the T-7 entry in the threat model and ``PROCESS_SPAWN_SITES``'s note each went stale
when ``--shallow-file``, ``log.mailmap=false`` and ``--no-renames`` joined the vector.
The shipped vector is read off the ``verify`` source, not restated here, so adding a
token reddens every quotation that has not followed.

Key: the span a quotation delimits, compared token for token. ``<sha>`` and
``<file_path>`` stand for the two runtime values; the empty string is quoted ``""``.
This holds those three quotations only; the CHANGELOG's is a released-entry decision.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[4]
SOURCE = ROOT / "packages/theurian-core/src/theurian/infrastructure/git/fix_commit_check.py"
RUNTIME_NAMES = {"commit": "<sha>", "file_path": "<file_path>"}
OPENING = "git -c core.precomposeunicode=false "


def _render(node: ast.expr) -> str:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value or '""'
    if isinstance(node, ast.Name):
        return RUNTIME_NAMES[node.id]
    if isinstance(node, ast.JoinedStr):
        return "".join(
            RUNTIME_NAMES[part.value.id]
            if isinstance(part, ast.FormattedValue) and isinstance(part.value, ast.Name)
            else _render(part)
            for part in node.values
        )
    raise AssertionError(f"the vector holds a node this pin cannot render: {ast.dump(node)}")


def _shipped_vector(tree: ast.Module) -> list[str]:
    verify = next(
        n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "verify"
    )
    call = next(
        n
        for n in ast.walk(verify)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "_run"
    )
    (argv,) = call.args
    assert isinstance(argv, ast.List)
    return [_render(element) for element in argv.elts]


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


QUOTATIONS = {
    "module-docstring": _docstring_quotations,
    "threat-model-T-7": _threat_model_quotations,
    "spawn-sites-note": _spawn_sites_note_quotations,
}


@pytest.mark.parametrize("where", QUOTATIONS)
def test_a_quotation_of_the_verify_command_is_the_command_that_ships(where: str) -> None:
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    shipped = _shipped_vector(tree)
    assert len(shipped) > 15 and "--shallow-file" in shipped, "the extractor read no real vector"
    quotations = QUOTATIONS[where](tree)
    assert quotations, f"{where} no longer quotes the command, or the key stopped matching it"

    for quoted in quotations:
        assert quoted.split() == ["git", *shipped], quoted
