"""The published config keys that are reserved rather than in force (#198, #129).

``schemas/config/project-config.schema.json`` publishes two keys whose
descriptions say, in the present tense, that nothing reads them:

- ``security.secretScan`` — SEC-11's policy selector. No content secret scanner
  exists anywhere in ``src/``, so the key selects no behaviour and the schema
  deliberately publishes **no default**: a default would state a policy nothing
  applies (#198).
- ``providers.review.repositories`` — SEC-10's repository allowlist, owed with
  review ingestion in Milestone 7 (#129).

Those are not descriptions of a design. They are load-bearing security claims:
``SECURITY.md``, ``docs/security/threat-model.md`` (T-7 and T-15),
``docs/architecture/requirements-analysis.md``,
``docs/architecture/review-knowledge.md``,
``plugins/claude-code/commands/ingest.md`` and the sample project's
``config.yaml`` each tell a reader not to rely on a control **because the key is
inert**. The moment a reader is added, every one of those sentences becomes
false, and nothing in the suite said so — that is the gap round one of #198
reported (code review M-2, security review LOW-3).

**The intended failure mode is a Milestone 7 diff.** Whoever writes the loader
that reads ``.theurian/config.yaml`` will make this file red, and the assertion
messages say what has to happen in that same change: correct the schema
descriptions and the six prose surfaces, decide whether a default may now be
published, and only then record the new site here. A key that starts working
without those edits ships a security document that is wrong.

**The population key**, so a reader can attack the key rather than the number:
the scan walks every ``*.py`` under the *imported* ``theurian`` package —
``tools/``, ``plugins/`` and the tests themselves are outside it — and flags a
module for naming any spelling in :data:`WATCHED_SPELLINGS`. A spelling is
matched **exactly**, as a whole identifier or a whole string constant, never as a
substring. That is what separates the two occurrences that matter from the six
that do not: ``repositories`` appears five times in ``src/`` as an English word
inside a docstring and once inside a sentence-shaped f-string
(``application/setup_withholding.py``), and a substring scan would read all six
as readers and force this pin to be silenced on its first run. Both real shapes
have a negative case in :data:`SCANNER_CASES`.

**What this cannot see.** It reads names, so a key assembled at runtime
(``config["secret" + "Scan"]``), one reached through a variable whose value comes
from elsewhere, or a whole-mapping read that never names the key
(``for key, value in config["security"].items()``) all pass. It is a floor on the
review a new reader gets, not a proof that one cannot exist — the same bound
``test_network_call_sites.py`` records for its own scans.

Pure in the sense the other structural tests are: it parses the shipped ``.py``
files as text and opens no database, no socket, and no temporary directory.
"""

from __future__ import annotations

import ast
import json
import pathlib
from collections.abc import Iterator

import pytest

import theurian

pytestmark = pytest.mark.unit

#: The package as *imported*, not a path relative to this file — the reckoning
#: ``test_gate_call_sites.py`` and ``test_network_call_sites.py`` use, and for the
#: same reason: a hand-built relative path can drift from the installed package
#: and would then scan a directory with no reader in it whatever the source did.
SRC = pathlib.Path(theurian.__file__).resolve().parent

#: ``parents[4]`` is ``.../tests/unit/`` → ``tests`` → ``theurian-core`` →
#: ``packages`` → repo root, where the published schemas live.
REPO_ROOT = pathlib.Path(__file__).resolve().parents[4]

PROJECT_CONFIG_SCHEMA = REPO_ROOT / "schemas" / "config" / "project-config.schema.json"

#: Every spelling a reader of these keys would plausibly use, keyed by the config
#: path the schema publishes.
#:
#: Three spellings for ``secretScan``: the JSON key as it appears in the file, the
#: snake_case name Python would bind it to, and the module constant a loader would
#: hold it in. ``repositories`` is already snake_case, so it has two.
#:
#: Deliberately *not* a fuzzy match. ``secret``, ``scan`` and ``repository`` are
#: ordinary words in this codebase — ``security/tokens.py`` handles secrets and
#: ``cli/context.py`` talks about repository roots — and a scan that flagged them
#: would have to be silenced with an allowlist so long that nobody would read it.
WATCHED_SPELLINGS: dict[str, frozenset[str]] = {
    "security.secretScan": frozenset({"secretScan", "secret_scan", "SECRET_SCAN"}),
    "providers.review.repositories": frozenset({"repositories", "REPOSITORIES"}),
}

_ALL_SPELLINGS = frozenset().union(*WATCHED_SPELLINGS.values())

#: Every place in the shipped package that names one of the keys above, as
#: ``(module path under theurian/, the spelling it names)``.
#:
#: **Empty, and that emptiness is the claim.** ``rg 'secretScan|secret_scan'
#: packages/theurian-core/src/`` returns nothing, and the six ``repositories``
#: hits are all prose. Adding an entry here is not a bookkeeping edit: it says a
#: key the published schema calls inert is now read, which makes the schema
#: descriptions and the prose surfaces listed in this module's docstring false
#: until they are corrected in the same change.
CONFIG_KEY_READER_SITES: frozenset[tuple[str, str]] = frozenset()


#: Node types carrying exactly one name, and the attribute it lives on.
#:
#: A table rather than a branch per type, so adding a syntactic role is a row.
#: ``keyword.arg`` and ``ExceptHandler.name`` are ``str | None`` — ``**kwargs``
#: and a bare ``except:`` — which is why the reader below checks the type of what
#: it finds rather than assuming a string.
_SINGLE_NAME_NODES: tuple[tuple[type[ast.AST], str], ...] = (
    (ast.Name, "id"),
    (ast.Attribute, "attr"),
    (ast.arg, "arg"),
    (ast.keyword, "arg"),
    (ast.FunctionDef, "name"),
    (ast.AsyncFunctionDef, "name"),
    (ast.ClassDef, "name"),
    (ast.ExceptHandler, "name"),
)


def _spellings(node: ast.AST) -> Iterator[str]:
    """Every whole name ``node`` introduces, whatever syntactic role it plays.

    A config key reaches Python source in one of two shapes and this yields both:
    as a **string constant** — ``config["security"]["secretScan"]``,
    ``block.get("repositories", [])``, ``KEY = "secretScan"`` — or as an
    **identifier** the value is bound to, which covers an assignment target, a
    parameter, an attribute, a keyword argument, a function or class name, an
    imported name, and a ``global``/``nonlocal`` declaration.

    Dotted import names are split, so ``import theurian.config.repositories``
    yields its last component rather than a dotted string nothing would match.

    Docstrings are ``ast.Constant`` nodes like any other string and are not
    excluded — they do not need to be. Exact equality is what excludes them: a
    sentence containing the word ``repositories`` is not equal to
    ``"repositories"``, which is the whole reason this scan matches whole names.
    """
    if isinstance(node, ast.Constant):
        if isinstance(node.value, str):
            yield node.value
        return
    if isinstance(node, ast.alias):
        yield from node.name.split(".")
        if node.asname is not None:
            yield node.asname
        return
    if isinstance(node, ast.Global | ast.Nonlocal):
        yield from node.names
        return
    for node_type, attribute in _SINGLE_NAME_NODES:
        if isinstance(node, node_type):
            name = getattr(node, attribute)
            if isinstance(name, str):
                yield name
            return


def _key_references(source: str, module: str) -> Iterator[tuple[str, str]]:
    """Every watched spelling ``source`` names, as ``(module, the spelling)``."""
    for node in ast.walk(ast.parse(source, filename=module)):
        for name in _spellings(node):
            if name in _ALL_SPELLINGS:
                yield module, name


#: One case per form the scan claims to see, and per form it claims to let past.
#:
#: Without this the enumeration below could go green while the scanner resolved
#: nothing at all — a broken extractor and a product with no reader read
#: identically from the outside, and this is a test whose expected result is the
#: empty set, so it has no other way to tell them apart.
#:
#: The negatives are as load-bearing as the positives, and the first two are not
#: invented: they are the exact shapes ``rg repositories
#: packages/theurian-core/src/`` returns today. A scan that read either as a
#: reader would be red on a clean tree, and the only way to make it green again
#: would be to list a docstring as a config-key reader.
SCANNER_CASES: tuple[tuple[str, frozenset[str]], ...] = (
    # -- the real prose in src/, which must stay invisible ------------------
    (
        '"""Their account and their repositories; both are private."""',
        frozenset(),
    ),
    (
        'raise ValueError(f"repositories on this machine; {_SEE_THE_VALUES}.")',
        frozenset(),
    ),
    ('"""Repositories must be allowlisted in `.theurian/config.yaml`."""', frozenset()),
    # -- other ordinary code that names a neighbouring word ------------------
    ("from theurian.infrastructure.github import ReviewProvider", frozenset()),
    ('path = root / "repository"', frozenset()),
    ("root = self.repository_root", frozenset()),
    ("secret = token.value", frozenset()),
    ("scanner.scan(document)", frozenset()),
    ('policy = config["security"]["maxSourceFileBytes"]', frozenset()),
    # -- a string constant naming the key ------------------------------------
    ('policy = config["security"]["secretScan"]', frozenset({"secretScan"})),
    ('policy = block.get("secretScan", "block")', frozenset({"secretScan"})),
    ('allowlist = review.get("repositories", [])', frozenset({"repositories"})),
    ('if "repositories" in review: pass', frozenset({"repositories"})),
    # -- a constant holding the key, which names both spellings at once ------
    ('SECRET_SCAN = "secretScan"', frozenset({"SECRET_SCAN", "secretScan"})),
    # -- an identifier the value is bound to ---------------------------------
    ("secret_scan = policy", frozenset({"secret_scan"})),
    ("repositories = registry.entries()", frozenset({"repositories"})),
    ("def apply(secret_scan: str) -> None: ...", frozenset({"secret_scan"})),
    ("self.secret_scan = value", frozenset({"secret_scan"})),
    ("ingest(repositories=allowlist)", frozenset({"repositories"})),
    ("class SecretScanPolicy: repositories = ()", frozenset({"repositories"})),
    ("from theurian.config import secret_scan", frozenset({"secret_scan"})),
    ("import theurian.config.repositories", frozenset({"repositories"})),
    ("global SECRET_SCAN", frozenset({"SECRET_SCAN"})),
)


@pytest.mark.parametrize(
    ("source", "expected"),
    SCANNER_CASES,
    ids=[case[0] for case in SCANNER_CASES],
)
def test_the_config_key_scan_sees_each_naming_form_and_no_other(
    source: str, expected: frozenset[str]
) -> None:
    """Guards the enumeration below, which is worthless the moment its scanner stops seeing.

    A structural pin whose expected result is the empty set fails silently in a
    way nothing else catches: if :func:`_spellings` stops resolving names, the
    enumeration keeps passing forever and the green looks exactly like a product
    that never grew a reader. So every form the scan claims to catch is asserted
    against a snippet here, and every form it claims to let past is asserted to
    yield nothing.

    The negative cases carry the more interesting half. Three of them are
    transcribed from ``src/`` as it stands — a docstring about someone's
    repositories, an f-string that opens with the word, and the
    ``infrastructure/github`` package docstring — and each would be a false red on
    a clean tree under a substring scan.
    """
    found = {spelling for _, spelling in _key_references(source, "snippet.py")}

    assert found == set(expected), (
        f"the config-key scan read `{source}` as {sorted(found)}, expected "
        f"{sorted(expected)}. The scanner is broken, not the product: fix "
        f"`_spellings` before trusting a green result from "
        f"`test_no_shipped_module_reads_a_config_key_the_schema_publishes_as_not_in_force`, "
        f"which would keep passing with a scanner that sees nothing."
    )


def test_no_shipped_module_reads_a_config_key_the_schema_publishes_as_not_in_force() -> None:
    """SEC-11 and SEC-10: the schema says these keys are inert, and six documents rest on it.

    ``security.secretScan`` publishes no default *because* no code applies one
    (#198), and ``providers.review.repositories`` is published as an allowlist
    that is "not in force" (#129). Six surfaces tell a reader not to rely on a
    control on exactly that basis: ``SECURITY.md``'s "Theurian does not scan
    ingested content for secrets", ``docs/security/threat-model.md`` at T-15 and
    T-7, the T-15 row in ``docs/architecture/requirements-analysis.md``,
    ``docs/architecture/review-knowledge.md``,
    ``plugins/claude-code/commands/ingest.md``, and the annotated keys in
    ``examples/sample-project/.theurian/config.yaml``.

    Every one of those sentences is a claim about the *source tree*, and until
    now the source tree was under no obligation to keep it true. This is that
    obligation.

    The assertion is an equality against the whole enumeration rather than a
    count or a subset, so it fails in both directions — a reader added, and a
    recorded exception removed — and its message names the module and the
    spelling it found.
    """
    sites = sorted(
        {
            site
            for path in sorted(SRC.rglob("*.py"))
            for site in _key_references(
                path.read_text(encoding="utf-8"), path.relative_to(SRC).as_posix()
            )
        }
    )

    assert sites == sorted(CONFIG_KEY_READER_SITES), (
        f"{len(sites)} place(s) in the shipped package name a config key the "
        f"published schema calls inert, and the pinned set has "
        f"{len(CONFIG_KEY_READER_SITES)}:\n"
        + "\n".join(f"  {module} :: {spelling}" for module, spelling in sites)
        + "\n\nExpected exactly:\n"
        + (
            "\n".join(
                f"  {module} :: {spelling}" for module, spelling in sorted(CONFIG_KEY_READER_SITES)
            )
            or "  (nothing)"
        )
        + "\n\nIf you added the Milestone 7 loader, this test is doing its job "
        "and the fix is not to list the site. `security.secretScan` (SEC-11, "
        "#198) and `providers.review.repositories` (SEC-10, #129) are published "
        "as reserved, and six documents tell a reader not to rely on either "
        "control *because* nothing reads them: SECURITY.md, "
        "docs/security/threat-model.md (T-15 and T-7), "
        "docs/architecture/requirements-analysis.md, "
        "docs/architecture/review-knowledge.md, "
        "plugins/claude-code/commands/ingest.md, and "
        "examples/sample-project/.theurian/config.yaml.\n\n"
        "In the same change: correct those six and the two schema descriptions; "
        "decide explicitly whether `secretScan` may now publish a default, since "
        "it was dropped on the reasoning that a default states a policy nothing "
        "applies; and add a test that goes red when the key stops taking effect. "
        "Then record the site here."
    )


#: The exact sentences each reserved key's published description has to keep, and
#: the issue that owns it.
#:
#: The scan above holds one direction — code must not overtake the claim — and
#: leaves the other open: a description rewritten to say the key works would make
#: the schema false while the scan stayed green, because no reader was added.
#: Pinning the wording closes it, and pins the wording *deliberately*: these are
#: not stylistic sentences. "No shipped code reads this key" and "no default is
#: published" are the two statements #198 corrected, and a reword should visit
#: this table rather than slip past it.
RESERVED_KEY_DESCRIPTIONS: tuple[tuple[str, tuple[str, ...], tuple[str, ...]], ...] = (
    (
        "security.secretScan",
        ("properties", "security", "properties", "secretScan"),
        (
            "Not in force",
            "No shipped code reads this key",
            "no default is published",
            "https://github.com/theurian/theurian/issues/198",
        ),
    ),
    (
        "providers.review.repositories",
        ("properties", "providers", "properties", "review", "properties", "repositories"),
        (
            "Not in force",
            "Nothing reads it today",
            "https://github.com/theurian/theurian/issues/129",
        ),
    ),
)


@pytest.mark.parametrize(
    ("key", "pointer", "required"),
    RESERVED_KEY_DESCRIPTIONS,
    ids=[case[0] for case in RESERVED_KEY_DESCRIPTIONS],
)
def test_each_reserved_key_still_publishes_the_absence_the_scan_enforces(
    key: str, pointer: tuple[str, ...], required: tuple[str, ...]
) -> None:
    """The other direction of the same claim, which the scan above cannot reach.

    The scan goes red when code overtakes the published description. It stays
    green if the *description* moves instead — a key quietly re-described as
    working, with no reader anywhere, leaves the schema asserting a control that
    still does not exist and every document resting on it unchanged. That is the
    #198 defect exactly, arriving from the other side.

    So the two halves are pinned in one file: the description states the absence,
    and the source tree is held to it.
    """
    node: object = json.loads(PROJECT_CONFIG_SCHEMA.read_text(encoding="utf-8"))
    for step in pointer:
        assert isinstance(node, dict), f"{key}: the schema has no `{'/'.join(pointer)}`"
        node = node[step]

    assert isinstance(node, dict), f"{key}: `{'/'.join(pointer)}` is not a subschema"
    description = node.get("description", "")

    for sentence in required:
        assert sentence in description, (
            f"{key}: the published description no longer says {sentence!r}.\n\n"
            f"It reads:\n  {description!r}\n\n"
            f"That sentence is what six documents rest on — SECURITY.md, "
            f"docs/security/threat-model.md (T-15 and T-7), "
            f"docs/architecture/requirements-analysis.md, "
            f"docs/architecture/review-knowledge.md, "
            f"plugins/claude-code/commands/ingest.md and the sample project's "
            f"config.yaml all tell a reader not to rely on the control because "
            f"the key is inert (#198, #129). If the key now takes effect, "
            f"`test_no_shipped_module_reads_a_config_key_the_schema_publishes_as_not_in_force` "
            f"is where the reader gets recorded and those documents are where "
            f"the claim gets corrected; if it does not, restore the sentence."
        )
