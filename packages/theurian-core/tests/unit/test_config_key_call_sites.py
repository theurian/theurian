"""Which published config keys have readers, and which are still reserved (#198, #129).

``schemas/config/project-config.schema.json`` publishes two keys this module
holds to their own descriptions, and since ADR-0027 decision 3 they are in
opposite states:

- ``security.secretScan`` — SEC-11's policy selector. **In force.**
  ``security/project_config.py`` reads it and ``application/proposal_service.py``
  applies it at ``theurian propose accept``, so the schema now publishes
  ``default: "block"`` — the policy an absent key and an absent config file both
  select (#198).
- ``providers.review.repositories`` — SEC-10's repository allowlist. **Still
  reserved**, owed with review ingestion (#129). Nothing reads it, its
  description says so, and this module is what holds the source tree to that.

Those are not descriptions of a design. They are load-bearing security claims,
and both directions of the claim can go wrong. ``SECURITY.md``,
``docs/security/threat-model.md`` (T-7 and T-15),
``docs/architecture/requirements-analysis.md``,
``docs/architecture/review-knowledge.md``,
``plugins/claude-code/commands/ingest.md`` and the sample project's
``config.yaml`` each tell a reader how far a control reaches. When ``secretScan``
was inert they said so *because it was*; a reader arriving without those edits
would have shipped a security document that was wrong, which is the gap round one
of #198 reported (code review M-2, security review LOW-3).

**That failure mode fired as designed.** The Milestone 7 diff that added the
first reader of ``.theurian/config.yaml`` made this file red, and the same change
corrected the surfaces, published the default, and recorded the sites. What is
left is the standing obligation, now pointing both ways: a reader added for
``repositories`` must redden this file, and so must a reader for ``secretScan``
being *removed* while the prose still describes a shipped control.

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
#: **Three entries, and exactly one of them reads the file.** The scan matches
#: whole names and not semantics -- deliberately, see the population key above --
#: so it cannot tell a reader from a field named after one, and this list is
#: therefore the honest output of the scan rather than a curated set of readers:
#:
#: * ``security/project_config.py :: secretScan`` **is** the reader. It is the
#:   only place in ``src/`` that names the published JSON key, and the only place
#:   that opens ``.theurian/config.yaml``.
#: * ``application/proposal_service.py :: secret_scan`` and
#:   ``cli/propose_commands.py :: secret_scan`` are the ``AcceptedProposal``
#:   field carrying what the scan did, and the local the accept path binds it to.
#:   They name the *outcome*, never the file.
#:
#: Adding a fourth entry is not a bookkeeping edit. For ``repositories`` it says
#: a key the published schema still calls inert is now read, which makes the
#: schema description and the prose surfaces in this module's docstring false
#: until they are corrected in the same change.
CONFIG_KEY_READER_SITES: frozenset[tuple[str, str]] = frozenset(
    {
        ("application/proposal_service.py", "secret_scan"),
        ("cli/propose_commands.py", "secret_scan"),
        ("security/project_config.py", "secretScan"),
    }
)


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


def test_the_shipped_modules_that_name_a_watched_config_key_are_the_recorded_ones() -> None:
    """SEC-11 and SEC-10: the schema says how far each key reaches, and six documents rest on it.

    ``security.secretScan`` publishes ``default: "block"`` *because* code applies
    it (#198, ADR-0027 decision 3), and ``providers.review.repositories`` is
    published as an allowlist that is "not in force" (#129). Six surfaces tell a
    reader how far to trust each: ``SECURITY.md``'s bullet on secrets already in
    a repository, ``docs/security/threat-model.md`` at T-15 and T-7, the T-15 row
    in ``docs/architecture/requirements-analysis.md``,
    ``docs/architecture/review-knowledge.md``,
    ``plugins/claude-code/commands/ingest.md``, and the annotated keys in
    ``examples/sample-project/.theurian/config.yaml``.

    Every one of those sentences is a claim about the *source tree*, and the
    source tree is under no obligation to keep it true unless something makes it
    one. This is that obligation.

    The assertion is an equality against the whole enumeration rather than a
    count or a subset, so it fails in both directions — a reader added, and a
    recorded one removed — and its message names the module and the spelling it
    found. The removing direction is the one that matters now that ``secretScan``
    works: a diff that deletes the reader while the schema still publishes a
    default and six documents still describe a shipped control reddens here.
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
        f"{len(sites)} place(s) in the shipped package name a watched config key, "
        f"and the pinned set has {len(CONFIG_KEY_READER_SITES)}:\n"
        + "\n".join(f"  {module} :: {spelling}" for module, spelling in sites)
        + "\n\nExpected exactly:\n"
        + (
            "\n".join(
                f"  {module} :: {spelling}" for module, spelling in sorted(CONFIG_KEY_READER_SITES)
            )
            or "  (nothing)"
        )
        + "\n\nThe fix is not to edit the list until you know which direction "
        "moved, because six documents describe how far each control reaches: "
        "SECURITY.md, docs/security/threat-model.md (T-15 and T-7), "
        "docs/architecture/requirements-analysis.md, "
        "docs/architecture/review-knowledge.md, "
        "plugins/claude-code/commands/ingest.md, and "
        "examples/sample-project/.theurian/config.yaml.\n\n"
        "A NEW site for `repositories`: `providers.review.repositories` (SEC-10, "
        "#129) is published as reserved and those documents say so *because* "
        "nothing reads it. In the same change, correct its schema description and "
        "the surfaces that rest on it, then record the site here.\n\n"
        "A MISSING site for `secretScan`: SEC-11's scanner (#198, ADR-0027 "
        "decision 3) is in force at `theurian propose accept`, the schema "
        'publishes `default: "block"` on that basis, and those same documents '
        "describe a shipped control. If the reader is gone, all of that is now "
        "false and has to be corrected in the same change -- do not simply drop "
        "the entry."
    )


#: The exact sentences each watched key's published description has to keep, and
#: the issue that owns it.
#:
#: The scan above holds one direction — code must not overtake the claim — and
#: leaves the other open: a description rewritten to say a key works would make
#: the schema false while the scan stayed green, because no reader was added.
#: Pinning the wording closes it, and pins the wording *deliberately*: these are
#: not stylistic sentences.
#:
#: ``secretScan``'s row is the one that turned over with ADR-0027 decision 3. It
#: used to require "No shipped code reads this key" and "no default is
#: published"; it now requires the opposite claim and the reach that bounds it,
#: because the sentence a reader has to be able to trust is no longer "this does
#: nothing" but "this does exactly this much".
WATCHED_KEY_DESCRIPTIONS: tuple[tuple[str, tuple[str, ...], tuple[str, ...]], ...] = (
    (
        "security.secretScan",
        ("properties", "security", "properties", "secretScan"),
        (
            "In force",
            "theurian propose accept",
            "best effort",
            "`theurian ingest` and index building run no scan",
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
    WATCHED_KEY_DESCRIPTIONS,
    ids=[case[0] for case in WATCHED_KEY_DESCRIPTIONS],
)
def test_each_watched_key_still_publishes_the_reach_the_scan_enforces(
    key: str, pointer: tuple[str, ...], required: tuple[str, ...]
) -> None:
    """The other direction of the same claim, which the scan above cannot reach.

    The scan goes red when the source tree stops matching the published
    description. It stays green if the *description* moves instead — a key
    quietly re-described as working, with no reader anywhere, leaves the schema
    asserting a control that does not exist and every document resting on it
    unchanged. That is the #198 defect exactly, arriving from the other side, and
    it has a mirror now that one of the two keys works: a description that stops
    bounding ``secretScan``'s reach lets a reader believe ingest is covered.

    So the two halves are pinned in one file: the description states the reach,
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
            f"config.yaml all tell a reader how far to trust the control (#198, "
            f"#129). If what the key does has changed, "
            f"`test_the_shipped_modules_that_name_a_watched_config_key_are_the_recorded_ones` "
            f"is where the readers get recorded and those documents are where "
            f"the claim gets corrected; if it has not, restore the sentence."
        )


#: The prose surfaces that state how far SEC-11's control reaches, as
#: ``(label, repo-relative path, the load-bearing sentence)``.
#:
#: The schema-description pins above and the reader scan hold the claim *inside
#: the contract and the source tree*. These are the documents a human reads, and
#: this table has now been wrong in both directions, which is why each row keeps
#: two sentences rather than one:
#:
#: * **Over-claiming.** Every one of these once said secret scanning happened
#:   when nothing did: ``SECURITY.md`` told users "ingestion warns or blocks per
#:   policy", the threat model listed a configurable ``block``/``warn``/``off``
#:   scanner as T-15's control, the requirements table cited SEC-11 with no
#:   qualification. #198 corrected all of it, and round-two mutations B1-B4
#:   showed a revert stayed green against the code and schema pins alone.
#: * **Under-claiming, and then over-claiming again by omission.** ADR-0027
#:   decision 3 shipped the control, so "no content scanner ships" became the
#:   false sentence -- and a document that says only "a scanner runs" invites the
#:   reading that content is now screened, which is the risk the ADR names
#:   directly. Each row therefore pins a sentence asserting the control **and** a
#:   sentence bounding it.
#:
#: Matched after collapsing runs of whitespace to a single space, because these
#: are line-wrapped Markdown: a sentence routinely breaks across two source
#: lines, and a raw substring match would miss the real wording and pass
#: vacuously.
SECRET_SCAN_PROSE_SURFACES: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    (
        "SECURITY.md",
        "SECURITY.md",
        (
            "`theurian propose accept` scans the bodies a proposal would land",
            "That is one gate and a best-effort detector, not coverage",
            "Theurian does not scan ingested content for secrets",
            "Theurian is not one and is not a replacement for one",
        ),
    ),
    (
        "docs/security/threat-model.md (T-15 Controls)",
        "docs/security/threat-model.md",
        (
            "**Controls: `theurian propose accept` scans every body before it moves it**",
            "It is best effort and the product says so",
            "`theurian ingest` runs no scan",
        ),
    ),
    (
        "docs/architecture/requirements-analysis.md (T-15 row)",
        "docs/architecture/requirements-analysis.md",
        (
            "scans every body it would land",
            "best-effort in-house detector",
            "Ingest-time and index-time scanning are separate controls and do not ship",
        ),
    ),
    (
        "plugins/claude-code/commands/ingest.md",
        "plugins/claude-code/commands/ingest.md",
        # Unchanged by ADR-0027 decision 3, and that is the claim: `ingest`
        # stores no content and runs no scan, so the sentence that was true when
        # nothing scanned anywhere is still exactly true.
        ("stores no content",),
    ),
)


@pytest.mark.parametrize(
    ("label", "relative_path", "sentences"),
    SECRET_SCAN_PROSE_SURFACES,
    ids=[case[0] for case in SECRET_SCAN_PROSE_SURFACES],
)
def test_each_secret_scan_prose_surface_states_the_control_and_its_bound(
    label: str, relative_path: str, sentences: tuple[str, ...]
) -> None:
    """SEC-11, #198: the human-facing documents must describe the control as it is.

    The schema-description and reader pins in this module hold the machine side;
    nothing held the prose, so reverting ``SECURITY.md`` to "ingestion warns or
    blocks per policy" -- a claim that a control runs that did not -- stayed green
    (round-two mutations B1-B4 all survived).

    Two failures, and each row guards both. A document that **under**-claims
    tells an operator to do work the product already does. A document that
    **over**-claims is worse: an operator who reads "Theurian blocks secrets"
    ships one into the canonical store believing it was caught, and ADR-0027
    names that specifically -- "a best-effort detector shipping as the SEC-11
    control invites the reading that content is now screened", which is why the
    disclaimer has to survive into every surface that describes it.
    """
    normalized = " ".join((REPO_ROOT / relative_path).read_text(encoding="utf-8").split())

    for sentence in sentences:
        assert sentence in normalized, (
            f"{label} no longer states {sentence!r}.\n\n"
            "These sentences are what tell a reader how far SEC-11's control "
            "reaches: `theurian propose accept` scans the bodies it would move, "
            "`block` by default (#198, ADR-0027 decision 3), with a best-effort "
            "detector that covers no other entry point. Dropping the first half "
            "under-claims a shipped control; dropping the second half promises "
            "screening the product does not do. If the control itself has "
            "changed, this document and the readers recorded in "
            "`test_the_shipped_modules_that_name_a_watched_config_key_are_the_recorded_ones` "
            "belong in the same change; otherwise restore the sentence."
        )
