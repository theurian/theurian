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
import shutil
import subprocess
from collections import Counter
from typing import Final

import pytest
import yaml
from jsonschema import Draft202012Validator

REPO_ROOT = pathlib.Path(__file__).resolve().parents[4]
PLUGIN = REPO_ROOT / "plugins" / "claude-code"
SCHEMAS = REPO_ROOT / "schemas"
LIB_SH = PLUGIN / "scripts" / "lib.sh"

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


#: ``Bash(x:*)`` is a prefix pattern, so the text before the colon has to be a
#: whole command *and* its fixed arguments -- anything a user could append is
#: pre-approved. ``Bash(command:*)`` was the case that mattered: ``command`` is a
#: POSIX shell builtin that runs its argument, so it matched ``command curl … |
#: sh``. Every prefix any command document may hold is vetted here rather than
#: pattern-matched, because the question "is this prefix safe" has no general
#: answer and a reviewer reading a diff of this set is the control.
#:
#: This is the *vocabulary*. Which command may use which is
#: :data:`EXPECTED_BASH_GRANTS`, and the two are joined by
#: :func:`test_every_expected_grant_is_one_of_the_vetted_prefixes`.
PERMITTED_BASH_GRANTS = frozenset(
    {"Bash(theurian:*)", "Bash(git:*)", "Bash(curl:*)", "Bash(command -v:*)"}
)

#: Exactly the ``Bash`` prefixes each command carries -- not a ceiling, the
#: value. A subset check cannot see a grant that *vanished*: deleting
#: ``Bash(theurian:*)`` from ``index.md`` left the whole suite green, because
#: the empty set is a subset of everything. That is not a privilege escalation
#: -- the user simply gets an approval prompt the document was written to avoid
#: -- but it is the same root cause as the write-side hole below, so it is
#: closed the same way and in the same commit rather than left as its sibling.
EXPECTED_BASH_GRANTS = {
    "setup": frozenset({"Bash(theurian:*)", "Bash(command -v:*)"}),
    "status": frozenset({"Bash(theurian:*)", "Bash(curl:*)"}),
    "doctor": frozenset({"Bash(theurian:*)", "Bash(curl:*)"}),
    "register-project": frozenset({"Bash(theurian:*)", "Bash(git:*)"}),
    "unregister-project": frozenset({"Bash(theurian:*)"}),
    "index": frozenset({"Bash(theurian:*)"}),
    "reindex": frozenset({"Bash(theurian:*)"}),
    "migrate": frozenset({"Bash(theurian:*)"}),
    "ingest": frozenset({"Bash(theurian:*)"}),
    "propose": frozenset({"Bash(theurian:*)"}),
    "upgrade": frozenset({"Bash(theurian:*)"}),
    "uninstall": frozenset({"Bash(theurian:*)"}),
}

#: Tools that write, scoped to what the document says it writes.
#: ``/theurian:propose`` drafts a proposal, which is the one command whose whole
#: purpose is to produce a file -- and its own rules say so, in this order: "do
#: not write into ``.theurian/migrations/`` or ``.theurian/knowledge/``
#: directly", then "writing under ``.theurian/proposals/`` is the whole of your
#: authority here". An unscoped ``Write`` pre-approves exactly the two
#: directories that first rule forbids, plus the rest of the repository, plus
#: the user's dotfiles.
#:
#: ``Write(<path>)`` is Claude Code's tool-specific rule grammar --
#: ``Tool(specifier)``, where a file tool's specifier is a gitignore-style path
#: pattern -- and not something invented here; see the permissions section of
#: Claude Code's settings documentation. Recorded because a grant whose syntax
#: is folklore is a grant nobody dares narrow.
PERMITTED_WRITE_TOOLS = {"propose": frozenset({"Write(.theurian/proposals/**)"})}


#: The five documents that open a file, and what each opens. ``Read`` was the
#: one grant with no check in either direction: it was subtracted out of the
#: write comparison and never compared to anything, so deleting it from a
#: document that has it, or adding it to one that does not, left the suite
#: green. Measured from the twelve front-matters, not from a list somebody
#: remembered.
COMMANDS_THAT_READ_A_FILE = {
    "setup": "probes the machine and reads the Claude Code configuration it is about to change",
    "doctor": "reads the same configuration to diagnose it, and repairs nothing",
    "migrate": "reads the migration files whose checksums it reports on",
    "propose": "reads existing knowledge before drafting a proposal against it",
    "upgrade": "opens `compatibility.yaml` in its step 2",
}

#: Why each kind of grant is bounded the way it is. Selected by kind rather than
#: printed together: a failure on one half used to carry the other half's
#: justification, which reads as if both were wrong and sends the reader to the
#: table that was fine.
_GRANT_RATIONALE = {
    "Bash": (
        "A prefix goes into PERMITTED_BASH_GRANTS only after someone has checked it "
        "cannot be extended into another command -- `Bash(command:*)` matched "
        "`command curl … | sh`."
    ),
    "Read": (
        "`Read` is granted where the document tells the agent to open a file and "
        "withheld where it does not. A missing one makes the user approve a prompt "
        "the other file-reading commands do not raise; a spare one is a permission "
        "the document never uses. COMMANDS_THAT_READ_A_FILE says which is which, and "
        "why."
    ),
    "write": (
        "Every configuration write belongs to `theurian setup` (CP-7); a command "
        "document that edits one directly is the drift that rule exists to prevent."
    ),
}


def _mismatch(
    command: str, kind: str, found: set[str], expected: frozenset[str], note: str = ""
) -> str:
    """Name what is extra and what is absent, separately.

    The two directions are different defects and read as opposites: an extra
    entry is a permission nobody vetted, a missing one is a document that will
    stop and ask. Printing the whole granted set named the legitimate grants as
    violations alongside the real one, and printing only ``found - expected``
    would say nothing at all in the vanished-grant case -- which is the case
    equality was introduced to catch.
    """
    return (
        f"/theurian:{command}'s {kind} grants do not match what this file records.\n"
        f"  granted but not permitted: {sorted(found - expected)}\n"
        f"  permitted but absent:      {sorted(expected - found)}\n"
        f"{_GRANT_RATIONALE[kind]}{note}"
    )


@pytest.mark.parametrize("command", REQUIRED_COMMANDS)
def test_command_grants_exactly_the_tools_it_uses(command: str) -> None:
    """``allowed-tools`` is a permission grant, and nothing else read it.

    ``/theurian:setup`` carried ``Bash(command:*)`` and ``Edit`` -- arbitrary
    execution, and a write grant contradicted by the document's own rule that
    ``theurian setup`` owns every write. Narrowing them reverted with the whole
    suite green, because the only assertion over this frontmatter was that
    ``description`` is non-empty. A permission nothing holds is the weakest kind
    of fix.

    Equality rather than a subset, and on every kind of grant rather than some
    of them. A subset check answers "did anything unvetted get added" and is
    blind to "did something get removed" -- measured: deleting ``propose``'s
    ``Write`` grant, deleting the whole ``allowed-tools`` key, and deleting
    ``index``'s ``Bash(theurian:*)`` each left the suite green. ``Read`` was
    worse than a subset: it was subtracted out of the write comparison and
    compared to nothing at all, so it moved in *either* direction unnoticed.

    Three assertions rather than one over the whole set, so that a failure names
    which kind of permission moved and carries only that kind's reasoning.
    Together they partition ``granted``: anything that is neither a ``Bash``
    prefix nor ``Read`` lands in ``writes`` and has to be permitted there, so a
    grant of a kind nobody has thought of fails rather than escaping.
    """
    text = (PLUGIN / "commands" / f"{command}.md").read_text(encoding="utf-8")
    frontmatter = yaml.safe_load(text.split("---", 2)[1])
    # Split on commas, which is what the frontmatter format uses as its
    # separator. A specifier containing a comma -- `Write(a,b)` -- would be torn
    # in two and neither half would match; no grant here has one, and a new one
    # fails this test rather than passing quietly, but the parser is the reason
    # to keep specifiers comma-free.
    granted = {entry.strip() for entry in (frontmatter.get("allowed-tools") or "").split(",")}
    granted.discard("")

    bash = {entry for entry in granted if entry.startswith("Bash(")}
    assert bash == EXPECTED_BASH_GRANTS[command], _mismatch(
        command, "Bash", bash, EXPECTED_BASH_GRANTS[command]
    )

    read = granted & {"Read"}
    why = COMMANDS_THAT_READ_A_FILE.get(command)
    expected_read = frozenset({"Read"} if why else ())
    assert read == expected_read, _mismatch(
        command,
        "Read",
        read,
        expected_read,
        f"\nThis file records that /theurian:{command} {why}." if why else "",
    )

    writes = (granted - bash) - read
    permitted = PERMITTED_WRITE_TOOLS.get(command, frozenset())
    assert writes == permitted, _mismatch(command, "write", writes, permitted)


def test_every_expected_grant_is_one_of_the_vetted_prefixes() -> None:
    """The per-command table cannot smuggle in a prefix nobody reviewed.

    :data:`EXPECTED_BASH_GRANTS` is what each document must carry, and
    :data:`PERMITTED_BASH_GRANTS` is the set of prefixes someone has checked
    cannot be extended into another command. Without this, adding
    ``Bash(command:*)`` to one command's row would pass the equality check
    above -- the row is its own oracle -- and the vetting set would never be
    consulted.
    """
    used = frozenset().union(*EXPECTED_BASH_GRANTS.values())

    assert used == PERMITTED_BASH_GRANTS, (
        f"the vetted prefix set and the per-command table disagree.\n"
        f"  used but never vetted: {sorted(used - PERMITTED_BASH_GRANTS)}\n"
        f"  vetted but unused:     {sorted(PERMITTED_BASH_GRANTS - used)}"
    )
    assert set(EXPECTED_BASH_GRANTS) == set(REQUIRED_COMMANDS)
    assert set(COMMANDS_THAT_READ_A_FILE) <= set(REQUIRED_COMMANDS), (
        "COMMANDS_THAT_READ_A_FILE names something that is not a shipped command, "
        "so its `Read` grant is required of a document that does not exist: "
        f"{sorted(set(COMMANDS_THAT_READ_A_FILE) - set(REQUIRED_COMMANDS))}"
    )


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


def test_upgrade_command_names_the_same_flags_as_lib_sh() -> None:
    """`/theurian:upgrade` documents a call `lib.sh` already implements.

    The document spells out `theurian compat check` rather than calling
    `theurian::compat_check`, because a slash command is read by an agent that
    is not running the hook's shell. That makes it a second copy of one call,
    and the copy shipped with one of four values wrong: it read
    ``protocolVersion`` from under ``coreCompatibility``, where the schema puts
    ``additionalProperties: false`` and only ``minimum`` and
    ``maximumExclusive`` live. Omitted, the flag makes the CLI exit 2.

    Pinning the flag *names* is what catches that class -- the placeholder text
    is prose and will keep being reworded, but the moment the two lists disagree
    one of them is calling an interface that does not exist.
    """
    flag = re.compile(r"^\s*(--[a-z-]+)", re.MULTILINE)

    lib_sh = LIB_SH.read_text(encoding="utf-8")
    body = lib_sh.split("theurian::compat_check()", 1)[1].split("\n}", 1)[0]
    from_lib = set(flag.findall(body))

    document = (PLUGIN / "commands" / "upgrade.md").read_text(encoding="utf-8")
    block = document.split("theurian compat check", 1)[1].split("```", 1)[0]
    from_doc = set(flag.findall(block))

    assert from_lib == {
        "--plugin-version",
        "--core-minimum",
        "--core-maximum-exclusive",
        "--protocol-version",
        "--json",
    }, f"lib.sh's compat_check flags changed: {sorted(from_lib)}"
    assert from_doc == from_lib, (
        "/theurian:upgrade and lib.sh disagree about `theurian compat check`: "
        f"document has {sorted(from_doc - from_lib)}, lib.sh has {sorted(from_lib - from_doc)}"
    )


def test_upgrade_command_placeholders_name_keys_the_schema_declares() -> None:
    """Every placeholder has to resolve against `compatibility.yaml`.

    The first version of this pinned only ``--protocol-version``, which was the
    field that shipped wrong. That left the other three free: moving the same
    defect to ``<coreCompatibility.pluginVersion>``, inventing
    ``<coreCompatibility.floor>``, or swapping ``minimum`` and
    ``maximumExclusive`` all survived the suite. Pinning one member of a class
    and calling the class closed is the mistake this whole change is about.

    Checked against the schema rather than a literal list, so that a key moving
    between the top level and ``coreCompatibility`` fails here rather than in a
    user's session. The flag-to-key mapping itself is spelled out, because that
    is the semantic part -- ``--core-minimum`` must read ``minimum`` and not
    ``maximumExclusive``, and both are legal keys.
    """
    schema = json.loads(
        (SCHEMAS / "protocol" / "compatibility.schema.json").read_text(encoding="utf-8")
    )
    core = schema["properties"]["coreCompatibility"]
    assert core["additionalProperties"] is False
    assert "protocolVersion" not in core["properties"]

    expected = {
        "--plugin-version": "pluginVersion",
        "--core-minimum": "coreCompatibility.minimum",
        "--core-maximum-exclusive": "coreCompatibility.maximumExclusive",
        "--protocol-version": "protocolVersion",
    }

    document = (PLUGIN / "commands" / "upgrade.md").read_text(encoding="utf-8")
    block = document.split("theurian compat check", 1)[1].split("```", 1)[0]
    found = dict(re.findall(r"(--[a-z-]+) <([A-Za-z.]+)>", block))

    assert found == expected, f"placeholder drift: {found}"

    for flag, key in expected.items():
        head, _, leaf = key.rpartition(".")
        holder = core if head == "coreCompatibility" else schema
        assert leaf in holder["properties"], (
            f"{flag} reads `{key}`, which the schema does not declare. "
            f"An agent following this document gets nothing and the CLI exits 2."
        )


def test_upgrade_command_never_names_an_unregistered_theurian_subcommand() -> None:
    """#42 can come back through the document with the code still correct.

    Step 3 has the agent print the remedy verbatim *and* quotes it, so the
    plugin surface carries its own copy of the string
    ``domain/compatibility.py`` was fixed to stop emitting. Reverting that quote
    to "Upgrade Core with ``theurian upgrade``" survived the whole suite, which
    means the fix was pinned on the Core side only.

    ``theurian upgrade`` and ``/theurian:upgrade`` are matched as *invocations*:
    the heading ``# /theurian:upgrade`` names the command and is legitimate, so
    the check is for the backticked or command-position forms.
    """
    document = (PLUGIN / "commands" / "upgrade.md").read_text(encoding="utf-8")

    assert "theurian upgrade" not in document, (
        "`/theurian:upgrade`'s document names `theurian upgrade`, which is not a "
        "registered command (#42). The remedy delegates to `uv tool upgrade` / "
        "`pipx upgrade`; a copy of the old string here reaches users even though "
        "`CORE_UPGRADERS` is correct."
    )
    assert "run /theurian:upgrade" not in document


def test_upgrade_command_grants_read_because_it_reads_the_declaration() -> None:
    """The document's step 2 opens `compatibility.yaml`; the grant must match.

    ``allowed-tools`` is a permission grant, so a missing ``Read`` does not stop
    the command -- it makes the user approve a prompt the other file-reading
    commands do not raise. Dropping ``, Read`` survived the suite, and round one
    had graded the sentence about this front-matter HIGH, so the grant is pinned
    to the behaviour the document describes rather than left to review.
    """
    text = (PLUGIN / "commands" / "upgrade.md").read_text(encoding="utf-8")
    frontmatter = yaml.safe_load(text.split("---", 2)[1])
    granted = {entry.strip() for entry in frontmatter["allowed-tools"].split(",")}

    assert "compatibility.yaml" in text
    assert "Read" in granted, (
        "`/theurian:upgrade` tells the agent to read `compatibility.yaml` but "
        "does not grant `Read`, unlike every other command that reads a file."
    )
    assert "Bash(theurian:*)" in granted


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


def test_the_version_claude_code_caches_is_the_version_the_plugin_declares() -> None:
    """One artifact, two files that name its version, and nothing joined them.

    ``plugin.json``'s ``version`` is the only field that decides *delivery*:
    Claude Code caches a plugin by it, ``/plugin update`` compares it, and
    ``SECURITY.md`` makes bumping it the act that ships a plugin fix to users.
    ``compatibility.yaml``'s ``pluginVersion`` is the only field that decides
    what the plugin *says it is*: ``theurian compat check`` reports it, the
    SessionStart hook reads it through ``lib.sh``, and ``shared.yml`` passes it
    to ``--plugin-version``.

    A maintainer who bumps only the first delivers a new plugin that identifies
    itself as the old one everywhere a user, a compatibility verdict, or a
    security advisory would look. ``docs/contributing/release.md`` told them a
    test caught that. Until this one, none did.

    Compared as written text, not as parsed versions: the cache key is the
    literal string, so ``0.2.0`` and ``0.2.0+build.1`` are two different
    deliveries even though SemVer §10 excludes build metadata from precedence.
    That the declared value is well-formed SemVer at all is
    :func:`test_compatibility_declaration_matches_its_schema`'s question.
    """
    manifest = json.loads((PLUGIN / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
    declaration = yaml.safe_load((PLUGIN / "compatibility.yaml").read_text(encoding="utf-8"))

    cached_version = manifest["version"]
    declared_version = declaration["pluginVersion"]

    assert cached_version == declared_version, (
        f".claude-plugin/plugin.json declares version {cached_version!r} but "
        f"compatibility.yaml declares pluginVersion {declared_version!r}. Claude Code "
        f"would cache the plugin as {cached_version!r} while `theurian compat check` "
        f"and the SessionStart hook report {declared_version!r}. Bump both."
    )


def _shipped_reader(key: str) -> str:
    """What ``lib.sh``'s ``theurian::compat_value`` extracts, by running it.

    The shipped reader is a ``sed`` expression, not a YAML parser -- a
    deliberate trade recorded in ``lib.sh`` itself, because the SessionStart
    hook must finish in milliseconds. Re-implementing that expression in Python
    would test the re-implementation, so the real file is sourced and the real
    function called.

    ``lib.sh`` is sourced by path as ``$1`` rather than interpolated into the
    script text, and ``theurian::plugin_root`` then derives the plugin root from
    ``BASH_SOURCE`` exactly as it does under Claude Code.
    """
    bash = shutil.which("bash")
    assert bash is not None, "bash is required to run the plugin's own reader"

    completed = subprocess.run(  # noqa: S603 - argv is repository-owned, never user input
        [bash, "-c", '. "$1"; theurian::compat_value "$2"', "bash", str(LIB_SH), key],
        capture_output=True,
        text=True,
        check=True,
    )
    return completed.stdout.strip()


def test_the_shipped_hook_reads_the_same_version_the_yaml_parser_does() -> None:
    """Three programs read ``pluginVersion``, and only two of them parse YAML.

    :func:`test_the_version_claude_code_caches_is_the_version_the_plugin_declares`
    proves the declared *value* matches ``plugin.json``. It cannot prove the
    SessionStart hook obtains that value, because the hook does not parse YAML:
    ``lib.sh`` reads the line with ``sed``. Everywhere the two readers disagree,
    the value this repository validates is not the value that ships. Measured
    against the real file, with only the quoting changed:

    ===================  ==============  ==============
    ``pluginVersion:``   ``yaml``        ``lib.sh``
    ===================  ==============  ==============
    ``0.1.0``            ``0.1.0``       ``0.1.0``
    ``"0.1.0"``          ``0.1.0``       ``"0.1.0"``
    ``'0.1.0'``          ``0.1.0``       ``'0.1.0'``
    ``>-`` then indent   ``0.1.0``       ``>-``
    the key twice        the last        the first
    ===================  ==============  ==============

    None of those is perverse: the schema types the field as a string, so
    quoting it is correct YAML, and nothing in the repository normalises quotes.
    End to end, ``pluginVersion: "0.1.0"`` makes the hook pass
    ``--plugin-version '"0.1.0"'`` and Core answer ``invalid-declaration``.

    Pinned against the YAML parse rather than against ``plugin.json`` so that
    each failure names one cause: a bumped-alone version fails the test above,
    a re-quoted value fails this one. Composed, the two make all three readers
    agree -- ``shared.yml``'s ``yq`` follows YAML semantics, as PyYAML does.

    ``lib.sh``'s own comment justifies the narrow reader by saying the file is
    "validated in CI against ``compatibility.schema.json``". That validation
    runs on the *parsed* document, so it is blind to every row above. This test
    is what makes the justification true.
    """
    declaration = yaml.safe_load((PLUGIN / "compatibility.yaml").read_text(encoding="utf-8"))

    extracted = _shipped_reader("pluginVersion")

    assert extracted == declaration["pluginVersion"], (
        f"lib.sh's sed reader extracts {extracted!r} from compatibility.yaml while the "
        f"YAML parse yields {declaration['pluginVersion']!r}. The SessionStart hook would "
        f"pass {extracted!r} to `theurian compat check`. Write the value as a plain "
        f"unquoted scalar on one line."
    )


#: Every flag in the plugin README's ``compat check`` example, mapped to the
#: value in ``compatibility.yaml`` it restates. The README prints this repository's
#: real declaration rather than an invented one, so it is a fourth copy of the
#: version -- and unlike the other three it has no reader that would notice it
#: going stale.
_README_FLAG_SOURCES: Final = {
    "--plugin-version": ("pluginVersion",),
    "--core-minimum": ("coreCompatibility", "minimum"),
    "--core-maximum-exclusive": ("coreCompatibility", "maximumExclusive"),
    "--protocol-version": ("protocolVersion",),
}


def test_the_plugin_readme_prints_the_declaration_it_actually_ships() -> None:
    """The README's ``compat check`` example is live values, not an illustration.

    ``plugins/claude-code/README.md`` documents compatibility by printing the
    exact command for *this* plugin -- ``--plugin-version 0.1.0
    --core-minimum 0.1.0-dev.0 ...`` -- which is what ``compatibility.yaml``
    declares today. Bump the declaration and the README keeps telling users to
    run the old one, and a user pasting it gets a verdict about a plugin
    version they do not have.

    Two things make this worth a test rather than a checklist line. It is the
    same class as the failure this file's other version tests exist for -- a
    version restated somewhere with nothing joining the copies -- and it is the
    only member of that class that no program reads, so nothing else can catch
    it. The release checklist did not, either.

    Deliberately scoped to the plugin's own README. The example versions in
    ``docs/protocol/plugin-core-compatibility.md`` and
    ``docs/contributing/release.md`` are illustrations using a future ``0.2.x``
    on purpose, and pinning those would force every doc to be rewritten on a
    release for no gain.
    """
    declaration = yaml.safe_load((PLUGIN / "compatibility.yaml").read_text(encoding="utf-8"))
    readme = (PLUGIN / "README.md").read_text(encoding="utf-8")

    printed = {
        flag: match.group(1)
        for flag in _README_FLAG_SOURCES
        if (match := re.search(rf"{re.escape(flag)}[ \t]+(\S+)", readme))
    }
    declared = {flag: _dig(declaration, path) for flag, path in _README_FLAG_SOURCES.items()}

    assert printed == declared, (
        f"plugins/claude-code/README.md's `compat check` example does not match "
        f"compatibility.yaml. README prints {printed}, the plugin declares {declared}. "
        f"A missing flag means the example was reworded and this test no longer reads it."
    )


def _dig(document: object, path: tuple[str, ...]) -> object:
    for key in path:
        assert isinstance(document, dict)
        document = document[key]
    return document


# -- FR-L4: the SessionStart hook stays cheap ------------------------------


def test_session_start_hook_is_registered_with_a_timeout() -> None:
    hooks = json.loads((PLUGIN / "hooks" / "hooks.json").read_text(encoding="utf-8"))
    entries = hooks["hooks"]["SessionStart"]
    assert len(entries) == 1
    hook = entries[0]["hooks"][0]
    assert hook["type"] == "command"
    assert 0 < hook["timeout"] <= 5, "SessionStart must be bounded (NFR-2)"


#: A ``theurian::warn`` call *at the start of a line* and the quoted arguments it
#: is given, across the backslash continuations a multi-argument warning wraps
#: over. Group 1 is the call; group 2 is everything it prints.
#:
#: Every part of this is load-bearing, because the first version --
#: ``theurian::warn\b(?:[^\n]*\\\n)*[^\n]*`` -- ran to end of line from wherever
#: the name appeared and so deleted *executed* code from the scan below.
#: Measured, on the four shapes it swallowed:
#:
#: - ``theurian::warn "..." ; uv tool install theurian`` -> ``theurian::warn``
#: - the same with ``&&`` and with ``||``
#: - ``msg="see theurian::warn"; uv tool install theurian``, on a mere mention
#:
#: So: anchored at line start, so a mention inside another string is not a call;
#: only double-quoted arguments, so anything after the closing quote stays in
#: ``rest`` and is scanned; and ``[^"]*`` rather than ``[^"\n]*``, so an argument
#: containing a real newline is one span the guards below can inspect rather than
#: an unmatched fragment they cannot.
#:
#: ``arguments`` may be empty and ``rest`` carries whatever follows on the same
#: logical line. Both are needed: an exemption that only *recognised* quoted
#: arguments silently let an unquoted ``$(...)`` fall outside every check, which
#: is what :func:`test_a_session_start_warning_takes_only_quoted_arguments`
#: closes by requiring ``rest`` to begin a new command.
_WARNING_CALL = re.compile(
    r"(?m)^(?P<call>[ \t]*theurian::warn\b)"
    r'(?P<arguments>(?:[ \t]*(?:\\\n[ \t]*)?"[^"]*")*)'
    r"(?P<rest>(?:[^\n\\]|\\\n)*)"
)

#: Both spellings of command substitution, parameter expansion, and a bare
#: ``$name``. The first two execute. The others cannot on their own, and are
#: refused anyway because a warning built from a variable is not a literal, and
#: a non-literal warning can carry anything -- terminal escapes included -- to a
#: user who did not ask for a message at all.
_EXECUTES = re.compile(r"\$[({\w]|`")

#: What may follow a warning's arguments: nothing, or an operator that ends the
#: command. Anything else is a further argument the exemption did not see.
_ENDS_THE_COMMAND = frozenset(";&|)}#<>")


def _strip_comments(script: str) -> str:
    return "\n".join(line for line in script.splitlines() if not line.lstrip().startswith("#"))


def _warning_calls(script: str) -> list[re.Match[str]]:
    """Every ``theurian::warn`` call, as one match carrying all three parts.

    One regex for the exemption and for both guards over it, so that what is
    judged is what is hidden. The first version failed on exactly that
    separation: it inspected a span stopping at the first newline while
    exempting one that did not, so a substitution written after a real newline
    inside an argument was invisible to the guard *and* removed from the
    forbidden list.
    """
    return list(_WARNING_CALL.finditer(_strip_comments(script)))


def _executed(script: str) -> str:
    """The part of a hook that runs, with comments and warning arguments removed.

    The forbidden list below is substring-matched, so *mentioning* an installer
    in a message was indistinguishable from *running* one -- and the message is
    the whole point of the Core-absent branch, which has nothing else to offer a
    user whose ``PATH`` has no ``theurian`` on it. ``theurian::warn`` is
    ``printf 'Theurian: %s\\n' "$*" >&2``: it writes its argument to stderr and
    does nothing else, so its argument is not work.

    Only ``arguments`` is removed. The call and everything after it survive, so
    anything chained on with ``;``, ``&&`` or ``||`` is still scanned.
    """
    return _WARNING_CALL.sub(
        lambda match: match.group("call") + match.group("rest"), _strip_comments(script)
    )


def test_a_session_start_warning_cannot_execute_anything() -> None:
    """The first of two things that make the exemption in :func:`_executed` sound.

    A message is exempt from the forbidden list because it is printed rather
    than run. Command substitution inside one *would* run, so a warning holding
    ``$(...)`` or a backtick would carry an installer straight past the check
    the exemption exists to keep honest.
    """
    script = (PLUGIN / "scripts" / "session-start.sh").read_text(encoding="utf-8")

    substituting = [
        match.group("arguments")
        for match in _warning_calls(script)
        if _EXECUTES.search(match.group("arguments"))
    ]
    assert not substituting, f"a SessionStart warning can execute: {substituting}"


def test_a_session_start_warning_takes_only_quoted_arguments() -> None:
    """The second, and the one the first cannot cover for.

    :data:`_WARNING_CALL` recognises double-quoted arguments and nothing else,
    so ``theurian::warn "a" $(curl ... | sh)`` leaves the substitution outside
    ``arguments`` -- where the guard above does not look, and where the
    forbidden-word list sees no forbidden word. Bash runs it all the same.

    Requiring what follows the quotes to *end the command* is what makes
    ``arguments`` provably the whole argument list, and therefore makes the
    guard above complete rather than merely correct on what it happens to see.
    """
    script = (PLUGIN / "scripts" / "session-start.sh").read_text(encoding="utf-8")

    unquoted = [
        match.group(0)
        for match in _warning_calls(script)
        if (tail := match.group("rest").lstrip()) and tail[0] not in _ENDS_THE_COMMAND
    ]
    assert not unquoted, (
        f"a SessionStart warning is given an argument that is not a double-quoted "
        f"literal, so nothing checks it: {unquoted}"
    )


def test_a_session_start_warning_is_a_terminated_literal() -> None:
    """The precondition under which an argument span cannot run past its argument.

    :data:`_WARNING_CALL` matches ``"[^"]*"``, which crosses newlines so
    that a multi-line message is one inspectable span. That is safe only while
    every quote in the script closes: an unterminated one would let a span run
    on to the next quote in the file, taking real commands out of the scan with
    it. Escapes are refused rather than counted, because ``\\"`` makes the count
    lie.
    """
    script = (PLUGIN / "scripts" / "session-start.sh").read_text(encoding="utf-8")

    assert '\\"' not in script, "an escaped quote makes the quote count below meaningless"
    assert script.count('"') % 2 == 0, "an unterminated string would widen the warning exemption"


def test_session_start_hook_performs_no_heavy_or_mutating_work() -> None:
    """§8 of the brief, checked against the script rather than the docs.

    A hook that installs, rebuilds, or rotates anything is a hook that surprises
    the user on a session they started for an unrelated reason.
    """
    script = (PLUGIN / "scripts" / "session-start.sh").read_text(encoding="utf-8")
    body = _executed(script)

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


def test_nothing_the_session_start_hook_sources_enables_errexit() -> None:
    """errexit anywhere in the sourced chain makes the hook's final ``exit 0`` unreachable.

    This replaces an assertion that read ``script.rstrip().endswith("exit 0")``
    and was named for the property below. It checked the last *line* of the
    file, so it stayed green through the entire life of plugin 0.1.0 while
    ``lib.sh`` re-enabled errexit in the caller's shell and the unguarded
    ``verdict="$(theurian::compat_check ...)"`` assignment killed the hook on
    every non-zero verdict -- exit 3 to Claude Code, no warning, no session. A
    mutation putting ``return 1`` in ``main`` also survived it, along with all
    1593 other tests.

    The behaviour itself is now held by
    ``tests/integration/test_session_start_hook.py``, which runs the hook. This
    one stays because it is cheap and it covers the *class*: a file added to the
    sourced chain tomorrow, or a ``set -e`` restored by someone who reads it as
    ordinary shell hygiene, breaks every degraded path at once and the message
    here says why. ``set -uo pipefail`` is deliberately not flagged -- ``-u``
    and ``pipefail`` do not turn a handled non-zero result into a dead shell.
    """
    hook = PLUGIN / "scripts" / "session-start.sh"
    script = hook.read_text(encoding="utf-8")
    sourced = set(re.findall(r"(?m)^\s*(?:\.|source)\s+.*?([A-Za-z0-9_.-]+\.sh)", script))

    assert sourced == {"lib.sh"}, (
        f"the hook's sourced set changed to {sorted(sourced)}; this scan was written "
        "against lib.sh alone and would otherwise pass by looking at nothing"
    )

    errexit = re.compile(r"(?m)^\s*set\s+(?:-[a-zA-Z]*e[a-zA-Z]*\b|-o\s+errexit\b)")
    offenders = [
        name
        for name in sorted({hook.name, *sourced})
        if errexit.search((hook.parent / name).read_text(encoding="utf-8"))
    ]
    assert not offenders, (
        f"{offenders} enable errexit, so any unguarded command in the hook aborts "
        "the shell before `exit 0` and Claude Code refuses to start the session"
    )


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
