"""Each backticked claim in the README's shipped-phase paragraph recomputes against its authority.

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
_VERB_CLAIM: Final = re.compile(r"theurian (?P<group>[a-z][a-z0-9-]*) (?P<verb>[a-z][a-z0-9-]*)")


def _flat(text: str) -> str:
    return " ".join(text.split())


def _paragraph_and_section() -> tuple[str, str]:
    text = _README.read_text(encoding="utf-8")
    paragraphs = [_flat(block) for block in re.split(r"\n[ \t]*\n", text)]
    holding = [paragraph for paragraph in paragraphs if _ANCHOR in paragraph]
    count = sum(paragraph.count(_ANCHOR) for paragraph in paragraphs)

    assert count == 1, f"README.md carries the anchor {_ANCHOR!r} {count} times, expected once"
    section = re.search(
        rf"^{re.escape(_SECTION_HEADING)}$(?P<body>.*?)(?=^#{{1,3}} |\Z)",
        text,
        re.MULTILINE | re.DOTALL,
    )
    assert section, f"README.md has no {_SECTION_HEADING!r} section"
    return holding[0], _flat(section.group("body"))


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


def test_the_readmes_shipped_phase_claims_match_system_capabilities_and_the_cli() -> None:
    paragraph, section = _paragraph_and_section()
    spans = _CODE_SPAN.findall(paragraph)
    flags = [match for span in spans if (match := _FLAG_CLAIM.fullmatch(span))]
    verbs = [span for span in spans if span.startswith("theurian ")]
    node_ids = [span for span in spans if "::" in span]
    paths = [span for span in spans if "/" in span and span not in node_ids]
    tracked = {path.relative_to(REPO_ROOT).as_posix() for path in _population(REPO_ROOT)}
    schema = json.loads(_SCHEMA.read_text(encoding="utf-8"))
    this_test = test_the_readmes_shipped_phase_claims_match_system_capabilities_and_the_cli

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
    untracked = [
        path
        for path in paths
        if path.rstrip("/") not in tracked
        and not any(entry.startswith(path.rstrip("/") + "/") for entry in tracked)
    ]

    assert _ANCHOR in section
    assert _OLD_UNIVERSAL not in section
    assert not stale, f"README.md states flags mcp/tools.py does not publish: {stale}"
    assert {"writeTools", "reviewIngestion", "traceability"} <= {m["name"] for m in flags}
    assert not unregistered, f"README.md names unregistered command paths: {unregistered}"
    assert {"theurian review ingest", "theurian okf export", "theurian okf import"} <= set(verbs)
    assert not untracked, f"README.md cites paths git does not track: {untracked}"
    assert "tools/eval/" in paths
    assert frozenset(schema["properties"]["capabilities"]["properties"]) == _SCHEMA_FLAGS
    assert node_ids == [
        f"{Path(__file__).resolve().relative_to(REPO_ROOT).as_posix()}::{this_test.__name__}"
    ]
