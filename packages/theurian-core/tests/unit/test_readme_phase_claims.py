"""The README's shipped-phase paragraph, each item it names recomputed against its authority.

Reach: the flags ``### What comes next`` backticks; the commands, MCP tool names and
paths the paragraph backticks, with any other span there refused; Phase A's exit date
and Phase B's owed demonstration against the roadmap's literals; the schema's flag set.

Not run by a documentation-only pull request (#839); ``release-core.yml``'s quality
job runs the whole suite at every tag, so a drift cannot reach a release.
"""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path
from typing import Final

import pytest
from command_extraction import REGISTERED
from command_population import REPO_ROOT, _population

pytestmark = pytest.mark.unit

_README: Final = REPO_ROOT / "README.md"
_ROADMAP: Final = REPO_ROOT / "docs/roadmap.md"
_TOOLS: Final = REPO_ROOT / "packages/theurian-core/src/theurian/mcp/tools.py"
_SCHEMA: Final = REPO_ROOT / "schemas/mcp/system-capabilities-response.schema.json"

_ANCHOR: Final = "**Shipped from phases A to F, and what says so.**"
_SECTION_HEADING: Final = "### What comes next"
_OLD_UNIVERSAL: Final = "None of the above describes a shipped capability"

# At 855ebd87; a new flag reddens this so the paragraph's "no flag reports" is re-read.
_SCHEMA_FLAGS: Final = frozenset(
    {
        "hybridRetrieval",
        "knowledgeGet",
        "knowledgeSearch",
        "raptor",
        "reviewFindings",
        "reviewIngestion",
        "reviewIngestionScope",
        "sensitivityEnforcement",
        "traceability",
        "writeTools",
    }
)

_CODE_SPAN: Final = re.compile(r"`([^`]+)`")
_FLAG_CLAIM: Final = re.compile(r"(?P<name>[A-Za-z]+): (?P<value>true|false)")
_BOOLEAN: Final = re.compile(r"\b(?:true|false)\b")
_VERB_CLAIM: Final = re.compile(r"theurian (?P<group>[a-z][a-z0-9-]*) (?P<verb>[a-z][a-z0-9-]*)")
_TOOL_NAME: Final = re.compile(r"[a-z]+\.[a-z][A-Za-z]*")

_A_EXIT_DATE: Final = re.compile(r"\*\*All three met, (\d{4}-\d{2}-\d{2})\.\*\*")
_README_A_DATE: Final = re.compile(r"\bmet on (\d{4}-\d{2}-\d{2})\b")
_B_DEMONSTRATION_OWED: Final = "What is left of this row is the demonstration itself"
_B_NOT_RUN: Final = "not yet run"


def _flat(text: str) -> str:
    return " ".join(text.split())


def _section(text: str, heading: str) -> str:
    """The body under the one heading of *text* that starts with *heading*."""
    bodies = re.findall(
        rf"^{re.escape(heading)}[^\n]*\n(.*?)(?=^#{{1,3}} |\Z)", text, re.MULTILINE | re.DOTALL
    )

    assert len(bodies) == 1, f"{len(bodies)} headings start with {heading!r}, expected 1"
    return str(bodies[0])


def _paragraph_and_section() -> tuple[str, str]:
    text = _README.read_text(encoding="utf-8")
    paragraphs = [_flat(block) for block in re.split(r"\n[ \t]*\n", text)]
    holding = [paragraph for paragraph in paragraphs if _ANCHOR in paragraph]
    count = sum(paragraph.count(_ANCHOR) for paragraph in paragraphs)

    assert count == 1, f"README.md carries the anchor {_ANCHOR!r} {count} times, expected once"
    return holding[0], _flat(_section(text, _SECTION_HEADING))


def _exit_row(phase: str) -> str:
    """The roadmap's ``**Exit criteria**`` row under ``### Phase <phase>``."""
    body = _section(_ROADMAP.read_text(encoding="utf-8"), f"### Phase {phase} ")
    rows = [line for line in body.splitlines() if line.startswith("| **Exit criteria** |")]

    assert len(rows) == 1, f"the roadmap's Phase {phase} carries {len(rows)} Exit criteria rows"
    return rows[0]


def _published(flag: str) -> object:
    """The value ``mcp/tools.py`` writes for *flag*, read from its syntax tree.

    ``test_write_tools_flag_claims.py::_published_flag``, parameterised by flag. Copied
    rather than imported: it and ``test_review_ingestion_flag_claims.py``'s
    ``_published_capability`` are private to test modules, not in a shared helper.
    """
    tree = ast.parse(_TOOLS.read_text(encoding="utf-8"), filename=_TOOLS.name)
    values = [
        value
        for node in ast.walk(tree)
        if isinstance(node, ast.Dict)
        for key, value in zip(node.keys, node.values, strict=True)
        if isinstance(key, ast.Constant) and key.value == flag
    ]

    assert len(values) == 1, f"{_TOOLS.name} assigns `{flag}` in {len(values)} dict literals"
    value = values[0]
    assert isinstance(value, ast.Constant) and isinstance(value.value, bool), (
        f"`{flag}` is published as `{ast.unparse(value)}`, not a written boolean"
    )
    return value.value


def _registered_tools() -> frozenset[str]:
    """``test_write_tools_flag_claims.py::_registered_tool_names``, copied as ``_published`` is."""
    tree = ast.parse(_TOOLS.read_text(encoding="utf-8"), filename=_TOOLS.name)
    return frozenset(
        keyword.value.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "_tool"
        for keyword in node.keywords
        if keyword.arg == "name"
        and isinstance(keyword.value, ast.Constant)
        and isinstance(keyword.value.value, str)
    )


def test_the_readmes_shipped_phase_claims_match_system_capabilities_and_the_cli() -> None:
    paragraph, section = _paragraph_and_section()
    spans = _CODE_SPAN.findall(paragraph)
    section_spans = _CODE_SPAN.findall(section)
    flags = [match for span in section_spans if (match := _FLAG_CLAIM.fullmatch(span))]
    verbs = [span for span in spans if span.startswith("theurian ")]
    tools = [span for span in spans if _TOOL_NAME.fullmatch(span)]
    node_ids = [span for span in spans if "::" in span]
    paths = [span for span in spans if "/" in span and span not in node_ids]
    tracked = {path.relative_to(REPO_ROOT).as_posix() for path in _population(REPO_ROOT)}
    schema = json.loads(_SCHEMA.read_text(encoding="utf-8"))
    phase_a_dates = _A_EXIT_DATE.findall(_exit_row("A"))
    demonstration_owed = _B_DEMONSTRATION_OWED in _exit_row("B")
    this_test = test_the_readmes_shipped_phase_claims_match_system_capabilities_and_the_cli

    unread = [
        span
        for span in spans
        if span not in {*verbs, *tools, *node_ids, *paths} and not _FLAG_CLAIM.fullmatch(span)
    ]
    unread_flags = [
        span for span in section_spans if _BOOLEAN.search(span) and not _FLAG_CLAIM.fullmatch(span)
    ]
    stale = [
        f"{m['name']}: {m['value']} (published {_published(m['name'])})"
        for m in flags
        if _published(m["name"]) is not (m["value"] == "true")
    ]
    unregistered = [
        verb
        for verb in verbs
        if not (match := _VERB_CLAIM.fullmatch(verb))
        or match["verb"] not in REGISTERED.get(match["group"], frozenset())
    ]
    unknown_tools = sorted(set(tools) - _registered_tools())
    untracked = [
        path
        for path in paths
        if path.rstrip("/") not in tracked
        and not any(entry.startswith(path.rstrip("/") + "/") for entry in tracked)
    ]

    assert _ANCHOR in section
    assert _OLD_UNIVERSAL not in section
    assert not unread, f"README.md's paragraph backticks {unread}, which no arm here reads"
    assert not unread_flags, f"README.md backticks {unread_flags}, not as `flag: value`"
    assert not stale, f"README.md states flags mcp/tools.py does not publish: {stale}"
    assert {"writeTools", "reviewIngestion", "traceability"} <= {m["name"] for m in flags}
    assert not unregistered, f"README.md names unregistered command paths: {unregistered}"
    assert {"theurian review ingest", "theurian okf export", "theurian okf import"} <= set(verbs)
    assert not unknown_tools, (
        f"README.md names MCP tools `_tool` does not register: {unknown_tools} "
        f"(a `name.name` span is read as a tool; cite a file by its path)"
    )
    assert {"system.capabilities", "review.search"} <= set(tools)
    assert not untracked, f"README.md cites paths git does not track: {untracked}"
    assert "tools/eval/" in paths
    assert len(phase_a_dates) == 1, f"the roadmap's Phase A exit row dates {phase_a_dates}"
    assert _README_A_DATE.findall(paragraph) == phase_a_dates, (
        f"README.md dates Phase A's exit {_README_A_DATE.findall(paragraph)}; "
        f"the roadmap records {phase_a_dates}"
    )
    assert (_B_NOT_RUN in paragraph) == demonstration_owed, (
        f"README.md says B's exit demonstration is {_B_NOT_RUN!r}: {_B_NOT_RUN in paragraph}; "
        f"the roadmap's Phase B exit row carries {_B_DEMONSTRATION_OWED!r}: {demonstration_owed}"
    )
    assert frozenset(schema["properties"]["capabilities"]["properties"]) == _SCHEMA_FLAGS
    assert node_ids == [
        f"{Path(__file__).resolve().relative_to(REPO_ROOT).as_posix()}::{this_test.__name__}"
    ]
