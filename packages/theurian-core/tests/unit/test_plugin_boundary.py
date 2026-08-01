"""The Core/plugin boundary (ADR-0001, ADR-0012, CP-2, CP-5).

The monorepo is only worth having if the boundary is enforced. Without these
tests, someone will import a Core module from a plugin script -- reasonably,
because it is right there -- and splitting the plugin into its own repository
stops being possible.
"""

from __future__ import annotations

import json
import math
import pathlib
import re
import secrets
from collections import Counter

import pytest
import yaml
from jsonschema import Draft202012Validator

REPO_ROOT = pathlib.Path(__file__).resolve().parents[4]
PLUGIN = REPO_ROOT / "plugins" / "claude-code"
SCHEMAS = REPO_ROOT / "schemas"

#: §9 of the brief. Each is a file under the plugin's ``commands/``.
REQUIRED_COMMANDS = (
    "setup",
    "status",
    "doctor",
    "register-project",
    "unregister-project",
    "index",
    "reindex",
    "migrate",
    "ingest",
    "propose",
    "upgrade",
    "uninstall",
)


# -- CP-2: no source-level dependency on Core ------------------------------


def test_plugin_contains_no_python() -> None:
    """A Python file in the plugin is one `import theurian` from a hard coupling."""
    python_files = [p for p in PLUGIN.rglob("*.py") if "/tests/" not in str(p)]
    assert not python_files, (
        "The plugin must reach Core only through the CLI, MCP, health API, and "
        f"public schemas: {[str(p.relative_to(PLUGIN)) for p in python_files]}"
    )


def test_no_plugin_file_imports_theurian() -> None:
    pattern = re.compile(r"^\s*(?:from|import)\s+theurian\b", re.MULTILINE)
    violations: list[str] = []
    for path in PLUGIN.rglob("*"):
        if not path.is_file() or path.suffix in {".png", ".jpg", ".svg"}:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:  # pragma: no cover - binary asset
            continue
        if pattern.search(text):
            violations.append(str(path.relative_to(PLUGIN)))

    assert not violations, f"Plugin files import Core modules: {violations}"


def test_plugin_scripts_only_invoke_the_published_cli() -> None:
    """Scripts shell out to `theurian`; they never execute Core's Python."""
    forbidden = re.compile(r"python[0-9.]*\s+-c|python[0-9.]*\s+-m\s+theurian")
    violations = [
        str(path.relative_to(PLUGIN))
        for path in (PLUGIN / "scripts").glob("*.sh")
        if forbidden.search(path.read_text(encoding="utf-8"))
    ]
    assert not violations, f"Scripts execute Core Python directly: {violations}"


# -- CP-5 / ADR-0012: install alone must be inert --------------------------


def test_manifest_declares_no_mcp_server() -> None:
    """Claude Code starts a plugin's MCP servers at enable time.

    Declaring one here would put a failed server in the user's session before
    they had ever been told `/theurian:setup` exists -- and FR-L3 requires that
    installing the plugin have no observable effect at all.
    """
    manifest = json.loads((PLUGIN / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
    assert "mcpServers" not in manifest


def test_no_autoloaded_mcp_config_at_plugin_root() -> None:
    assert not (PLUGIN / ".mcp.json").exists()
    assert not (PLUGIN / ".claude-plugin" / ".mcp.json").exists()


def test_connection_template_exists_and_uses_http() -> None:
    template = json.loads((PLUGIN / "mcp" / "theurian.mcp.json").read_text(encoding="utf-8"))
    server = template["mcpServers"]["theurian"]
    assert server["type"] == "http"
    assert server["url"] == "http://127.0.0.1:7419/mcp"


def test_connection_template_is_never_stdio() -> None:
    """A `command` key would make Claude Code spawn one Theurian per client:
    N writers on one SQLite database (ADR-0002)."""
    template = json.loads((PLUGIN / "mcp" / "theurian.mcp.json").read_text(encoding="utf-8"))
    server = template["mcpServers"]["theurian"]
    assert "command" not in server
    assert "args" not in server


def test_connection_template_binds_loopback_only() -> None:
    template = json.loads((PLUGIN / "mcp" / "theurian.mcp.json").read_text(encoding="utf-8"))
    url = template["mcpServers"]["theurian"]["url"]
    assert url.startswith("http://127.0.0.1:")


# -- SEC-5 / ADR-0011: no literal secret in configuration ------------------


def test_connection_template_references_the_token_by_environment_variable() -> None:
    """Config files get copied into gists, synced to dotfiles, pasted in issues."""
    template = json.loads((PLUGIN / "mcp" / "theurian.mcp.json").read_text(encoding="utf-8"))
    authorization = template["mcpServers"]["theurian"]["headers"]["Authorization"]
    assert authorization == "Bearer ${THEURIAN_MCP_TOKEN}"


def _looks_like_a_secret(token: str) -> bool:
    """Whether ``token`` resembles CSPRNG output rather than prose.

    Length alone is not a signal: a kebab-case ADR filename is long too. Theurian
    tokens come from ``secrets.token_urlsafe``, which yields base64url with mixed
    case, digits, and near-uniform character frequency. Requiring all three
    together separates a real token from an identifier a human typed.
    """
    if not (
        any(c.isupper() for c in token)
        and any(c.islower() for c in token)
        and any(c.isdigit() for c in token)
    ):
        return False

    counts = Counter(token)
    entropy = -sum((n / len(token)) * math.log2(n / len(token)) for n in counts.values())
    return entropy >= 4.0


def test_no_plugin_file_contains_a_high_entropy_secret() -> None:
    """Catches a token pasted in during debugging and forgotten."""
    candidate = re.compile(r"\b[A-Za-z0-9_\-]{32,}\b")
    violations: list[str] = []
    for path in PLUGIN.rglob("*"):
        if not path.is_file() or path.suffix in {".png", ".jpg", ".svg"} or path.name == "LICENSE":
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:  # pragma: no cover - binary asset
            continue
        for match in candidate.finditer(text):
            if _looks_like_a_secret(match.group()):
                violations.append(f"{path.relative_to(PLUGIN)}: {match.group()[:8]}...")

    assert not violations, f"Possible secrets in plugin files: {violations}"


def test_the_secret_detector_actually_detects_a_secret() -> None:
    """A detector nobody has proved works is a test that always passes."""
    assert _looks_like_a_secret(secrets.token_urlsafe(32))
    assert not _looks_like_a_secret("0012-plugin-does-not-autoregister-mcp-server")
    assert not _looks_like_a_secret("THEURIAN_MCP_TOKEN")


# -- CP-3: the twelve commands ---------------------------------------------


@pytest.mark.parametrize("command", REQUIRED_COMMANDS)
def test_command_exists_with_frontmatter(command: str) -> None:
    path = PLUGIN / "commands" / f"{command}.md"
    assert path.exists(), f"/theurian:{command} is missing"

    text = path.read_text(encoding="utf-8")
    assert text.startswith("---\n"), f"{command}.md has no frontmatter"
    frontmatter = yaml.safe_load(text.split("---", 2)[1])
    assert frontmatter.get("description"), f"{command}.md has no description"


def test_no_unexpected_commands() -> None:
    present = {p.stem for p in (PLUGIN / "commands").glob("*.md")}
    assert present == set(REQUIRED_COMMANDS)


@pytest.mark.parametrize("command", ["uninstall", "unregister-project"])
def test_destructive_commands_state_what_is_preserved(command: str) -> None:
    """A user running these needs to know their team's knowledge is safe."""
    text = (PLUGIN / "commands" / f"{command}.md").read_text(encoding="utf-8").lower()
    assert "never" in text
    assert "knowledge" in text


def test_propose_command_states_that_ai_cannot_approve() -> None:
    text = (PLUGIN / "commands" / "propose.md").read_text(encoding="utf-8").lower()
    assert "cannot approve" in text
    assert "proposal" in text


def test_doctor_command_never_auto_repairs() -> None:
    text = (PLUGIN / "commands" / "doctor.md").read_text(encoding="utf-8").lower()
    assert "never run a repair automatically" in text


# -- CP-6: compatibility declaration ---------------------------------------


def test_compatibility_declaration_matches_its_schema() -> None:
    declaration = yaml.safe_load((PLUGIN / "compatibility.yaml").read_text(encoding="utf-8"))
    schema = json.loads(
        (SCHEMAS / "protocol" / "compatibility.schema.json").read_text(encoding="utf-8")
    )
    Draft202012Validator(schema).validate(declaration)


def test_declared_protocol_matches_core() -> None:
    """The versions move independently; the protocol must not drift apart."""
    from theurian import __protocol_version__

    declaration = yaml.safe_load((PLUGIN / "compatibility.yaml").read_text(encoding="utf-8"))
    assert declaration["protocolVersion"] == __protocol_version__


def test_installed_core_is_inside_the_declared_range() -> None:
    """The plugin in this repository must work with the Core beside it."""
    from theurian import __version__
    from theurian.domain.compatibility import (
        CompatibilityDeclaration,
        Version,
        resolve_compatibility,
    )

    declaration_data = yaml.safe_load((PLUGIN / "compatibility.yaml").read_text(encoding="utf-8"))
    declaration = CompatibilityDeclaration(
        plugin_version=Version.parse(declaration_data["pluginVersion"]),
        core_minimum=Version.parse(declaration_data["coreCompatibility"]["minimum"]),
        core_maximum_exclusive=Version.parse(
            declaration_data["coreCompatibility"]["maximumExclusive"]
        ),
        protocol_version=declaration_data["protocolVersion"],
    )

    from theurian import __protocol_version__

    verdict = resolve_compatibility(
        declaration, Version.parse_python(__version__), __protocol_version__
    )
    assert verdict.is_compatible, verdict.message


def test_plugin_and_core_versions_are_independent() -> None:
    """ADR-0001: two release trains, not one artifact with two names."""
    from theurian import __version__

    manifest = json.loads((PLUGIN / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
    assert manifest["version"] != __version__


# -- FR-L4: the SessionStart hook stays cheap ------------------------------


def test_session_start_hook_is_registered_with_a_timeout() -> None:
    hooks = json.loads((PLUGIN / "hooks" / "hooks.json").read_text(encoding="utf-8"))
    entries = hooks["hooks"]["SessionStart"]
    assert len(entries) == 1
    hook = entries[0]["hooks"][0]
    assert hook["type"] == "command"
    assert 0 < hook["timeout"] <= 5, "SessionStart must be bounded (NFR-2)"


def test_session_start_hook_performs_no_heavy_or_mutating_work() -> None:
    """§8 of the brief, checked against the script rather than the docs.

    A hook that installs, rebuilds, or rotates anything is a hook that surprises
    the user on a session they started for an unrelated reason.
    """
    script = (PLUGIN / "scripts" / "session-start.sh").read_text(encoding="utf-8")
    body = "\n".join(line for line in script.splitlines() if not line.lstrip().startswith("#"))

    forbidden = (
        "theurian setup",
        "daemon install",
        "index rebuild",
        "index build",
        "migrate apply",
        "auth rotate",
        "pip install",
        "uv tool install",
        "brew install",
        "rm -rf",
    )
    found = [phrase for phrase in forbidden if phrase in body]
    assert not found, f"SessionStart performs forbidden work: {found}"


def test_session_start_hook_cannot_block_a_session() -> None:
    """A degraded Theurian must never stop a session from starting."""
    script = (PLUGIN / "scripts" / "session-start.sh").read_text(encoding="utf-8")
    assert script.rstrip().endswith("exit 0")


def test_only_session_start_is_hooked() -> None:
    hooks = json.loads((PLUGIN / "hooks" / "hooks.json").read_text(encoding="utf-8"))
    assert set(hooks["hooks"]) == {"SessionStart"}


# -- Artifact independence -------------------------------------------------


def test_plugin_has_its_own_release_metadata() -> None:
    for required in ("README.md", "CHANGELOG.md", "LICENSE", "compatibility.yaml"):
        assert (PLUGIN / required).exists(), f"plugin is missing {required}"


def test_plugin_documents_the_serena_split() -> None:
    """§23: users must be told which tool answers which question."""
    readme = (PLUGIN / "README.md").read_text(encoding="utf-8").lower()
    assert "serena" in readme
    assert "stdio" in readme, "the README must explain why Theurian is never stdio"
