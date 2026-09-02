"""Which published config keys have readers, and which are still reserved (#198, #129, #426).

``schemas/config/project-config.schema.json`` publishes the keys this module
holds to their own descriptions, and since ADR-0027 decision 3 they are in
opposite states:

- ``security.secretScan`` — SEC-11's policy selector. **In force.**
  ``security/project_config.py`` reads it and ``application/proposal_service.py``
  applies it at ``theurian propose accept``, so the schema now publishes
  ``default: "block"`` — the policy an absent key and an absent config file both
  select (#198).
- ``providers.review.repositories`` — SEC-10's repository allowlist. **Still
  reserved**, owed with review ingestion. Nothing reads it, its description says
  so, and this module is what holds the source tree to that. The description's
  ``#129`` cite is **stale**: #129 closed on the wording rather than on the
  control, so the live owner is #429 (the T-7 fetch controls) and the repointing
  belongs to #428's stale-owner sweep — with the same schema file's *root*
  description, which still carries an unnarrowed "Nothing in src/ reads this
  file" (#455). Both stay outside this PR because they land on
  ``project-config.schema.json``, which #199 unit B owns.
- **Every key in the ``raptor`` block** — ADR-0008 decision 10's switch. **Still
  reserved.** ``docs/architecture/raptor.md`` and ADR-0008 decision 10 used to
  say nothing in ``src/`` read ``.theurian/config.yaml`` at all; ADR-0027
  decision 3 falsified that and #426 narrowed both sentences to *the ``raptor``
  block is unread*, which is the claim this scan now holds. The block's keys are
  **derived from the schema** rather than listed here (see
  :func:`_published_keys`), so a fourth key added to the block is watched by the
  change that adds it. The prose halves live in
  ``tests/unit/test_raptor_config_claims.py``.

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
substring.

That is what keeps the prose out of the enumeration, and there is a lot of it.
Counted with ``git grep -o -n -i -E "(^|[^A-Za-z0-9_])repositories([^A-Za-z0-9_]|$)"
-- packages/theurian-core/src`` at ``5a14145``: **ten** occurrences of
``repositories``, none of them a whole name. Seven are the English word in a
docstring or a comment; one is inside a sentence-shaped f-string
(``application/setup_withholding.py``); two name the dotted path
``providers.review.repositories`` in ``security/project_config.py``'s own
docstring, which describes the key precisely because nothing reads it. A
substring scan would read all ten as readers and force this pin to be silenced on
its first run. The three real shapes have a negative case in
:data:`SCANNER_CASES`.

The ``raptor`` block pays the same rent. The same key over ``enabled`` returns
**four** occurrences, on four lines in three modules: ``systemd_user.py``'s
"without lingering enabled", ``index_scan.py``'s "an ICU-enabled build", and two
in ``application/forest_builder.py`` (a ``#:`` comment at line 139 and an
error-message f-string at line 173) that name ``raptor.enabled`` itself. None is
a whole name — the f-string's own constant is the sentence around the word, not
the word — so none is seen.

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
import re
from collections.abc import Iterator
from typing import Final

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

#: Where the ``raptor`` block's keys live in the published schema.
_RAPTOR_PROPERTIES: Final = ("properties", "raptor", "properties")

_CAMEL_HUMP: Final = re.compile(r"(?<!^)(?=[A-Z])")


def _published_keys(*pointer: str) -> tuple[str, ...]:
    """The property names the schema publishes under ``pointer``, sorted.

    Read from ``project-config.schema.json`` rather than transcribed, because a
    hand-written key list cannot honour the property this scan claims: that a key
    *added* to the block is watched by the change that adds it. A transcribed
    list would leave the new key unwatched while every test here stayed green —
    the ``_swept_modules`` finding in ``test_adr_0018_claims.py``, in the shape a
    config block takes.
    """
    node: object = json.loads(PROJECT_CONFIG_SCHEMA.read_text(encoding="utf-8"))
    for step in pointer:
        assert isinstance(node, dict), f"the schema has no `{'/'.join(pointer)}`"
        node = node[step]
    assert isinstance(node, dict), f"`{'/'.join(pointer)}` is not a mapping of properties"
    return tuple(sorted(node))


def _plausible_spellings(key: str) -> frozenset[str]:
    """The three shapes a published JSON key takes once a loader binds it.

    The key as the file spells it, the snake_case name Python would bind it to,
    and the SCREAMING_SNAKE module constant a loader would hold it in — the same
    three ``secretScan`` is watched under, and for the same reason: a loader
    names the key at least once in one of them.
    """
    snake = _CAMEL_HUMP.sub("_", key).lower()
    return frozenset({key, snake, snake.upper()})


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
#:
#: ``enabled`` sits awkwardly on that rule and is watched anyway. It *is* an
#: ordinary word, and as a whole identifier — ``enabled = True``, a keyword
#: argument ``enabled=...``, a dataclass field — it would be flagged with nothing
#: to do with ``raptor``. It is kept because it is also the published JSON key
#: exactly, so ``config["raptor"]["enabled"]`` is the shape a loader takes, and
#: this scan does not distinguish a string constant from an identifier (see
#: :func:`_spellings`, which yields both from one stream). The cost is a false RED
#: on an unrelated ``enabled``: a read, in the direction that keeps the claim. The
#: enumeration's failure message therefore reports what was found and what each
#: possibility would mean, rather than announcing a loader.
_RECORDED_KEYS: Final[dict[str, frozenset[str]]] = {
    "security.secretScan": frozenset({"secretScan", "secret_scan", "SECRET_SCAN"}),
    "providers.review.repositories": frozenset({"repositories", "REPOSITORIES"}),
}

#: The ``raptor`` block, derived. Two of these keys already have a snake_case
#: twin in ``src/`` — ``ForestOptions.max_levels`` and
#: ``ForestOptions.min_children_per_summary`` carry the schema's *defaults*, not
#: the file's values — and both are recorded in :data:`CONFIG_KEY_READER_SITES`
#: as the fields they are. What has no site at all is the JSON spelling of any of
#: them, which is what a loader would have to name.
_RAPTOR_KEYS: Final[dict[str, frozenset[str]]] = {
    f"raptor.{key}": _plausible_spellings(key) for key in _published_keys(*_RAPTOR_PROPERTIES)
}

WATCHED_SPELLINGS: dict[str, frozenset[str]] = _RECORDED_KEYS | _RAPTOR_KEYS

_ALL_SPELLINGS = frozenset().union(*WATCHED_SPELLINGS.values())

#: Every place in the shipped package that names one of the keys above, as
#: ``(module path under theurian/, the spelling it names)``.
#:
#: **Five entries, and exactly one of them reads the file.** The scan matches
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
#: * ``application/forest_builder.py :: max_levels`` and
#:   ``:: min_children_per_summary`` are ``ForestOptions`` fields. They are named
#:   after ``raptor.maxLevels`` and ``raptor.minChildrenPerSummary`` and carry the
#:   schema's *defaults* -- pinned against the schema by
#:   ``test_forest_derivation.py::test_the_option_defaults_are_the_config_schemas_own``
#:   -- which is the opposite of reading the file: a default is what applies
#:   *because* nothing read a value.
#:
#: Adding a sixth entry is not a bookkeeping edit. For ``repositories`` it says a
#: key the published schema still calls inert is now read, which makes the schema
#: description and the prose surfaces in this module's docstring false until they
#: are corrected in the same change. For anything under ``raptor.`` it says
#: ADR-0008 decision 10's "Nothing in ``src/`` reads ``raptor.enabled``, nor any
#: other key in the ``raptor`` block" and ``docs/architecture/raptor.md``'s "no
#: ``raptor`` key is read" have become false, and
#: ``tests/unit/test_raptor_config_claims.py`` is what holds those two sentences.
CONFIG_KEY_READER_SITES: frozenset[tuple[str, str]] = frozenset(
    {
        ("application/forest_builder.py", "max_levels"),
        ("application/forest_builder.py", "min_children_per_summary"),
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
    # -- the real prose the `raptor` block's keys collide with ---------------
    # All four occurrences of `enabled` in `src/` (population key in the module
    # docstring), transcribed. The first two use it as an ordinary English word;
    # the last two name `raptor.enabled` itself, in `application/forest_builder.py`
    # -- a `#:` comment, which never reaches the AST at all, and one arm of an
    # implicitly concatenated f-string, whose constant is the sentence around the
    # word rather than the word. A substring scan would read all four as readers
    # of `raptor.enabled` and make this pin red on a clean tree.
    ('"""Without lingering enabled, closing the last session stops it."""', frozenset()),
    ('"""`lower()` is one of the functions an ICU-enabled build replaces."""', frozenset()),
    ("#: (`raptor.enabled: false` is that). The schema admits up to 8", frozenset()),
    (
        'raise InvariantViolationError(f"max_levels must be at least 1, got "'
        'f"{n} -- a forest of zero tiers is `raptor.enabled: false`")',
        frozenset(),
    ),
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
    # -- the raptor loader that does not exist, in the shape it would take ---
    ('on = config["raptor"]["enabled"]', frozenset({"enabled"})),
    ('levels = block.get("maxLevels", 3)', frozenset({"maxLevels"})),
    (
        'floor = block.get("minChildrenPerSummary", 3)',
        frozenset({"minChildrenPerSummary"}),
    ),
    # -- a constant holding the key, which names both spellings at once ------
    ('SECRET_SCAN = "secretScan"', frozenset({"SECRET_SCAN", "secretScan"})),
    # -- an identifier the value is bound to ---------------------------------
    ("secret_scan = policy", frozenset({"secret_scan"})),
    ("repositories = registry.entries()", frozenset({"repositories"})),
    ("tiers = min(self._options.max_levels, MAX_LEVEL)", frozenset({"max_levels"})),
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


def test_the_raptor_block_still_publishes_the_keys_the_scan_is_derived_from() -> None:
    """The population control for the derived half, asserted before it is searched.

    ADR-0008 decision 10 and ``docs/architecture/raptor.md`` claim that no key in
    the ``raptor`` block is read. The enumeration below reports that as an
    absence — and a search over an empty key set reports exactly the same
    absence, so the key set has to be established first. Emptying
    ``properties.raptor.properties`` in the schema, or renaming the block, would
    otherwise leave every test in this module green while the claim it enforces
    covered nothing.

    ``raptor.enabled`` is required by name because it is the key ADR-0008
    decision 10 is *about*: the decision is phrased in terms of that key's
    default, and its correction note says "Nothing in ``src/`` reads
    ``raptor.enabled``". A block that no longer publishes it makes the record's
    subject vanish, which is a documentation change and not a schema tidy-up.
    """
    keys = _published_keys(*_RAPTOR_PROPERTIES)

    assert keys, (
        "the schema's `raptor` block publishes no properties, so the reader scan "
        "below would enforce ADR-0008 decision 10's claim over nothing"
    )
    assert "enabled" in keys, (
        f"the schema's `raptor` block no longer publishes `enabled`, which is the key "
        f"ADR-0008 decision 10 decides the default of: {list(keys)}"
    )
    assert set(_RAPTOR_KEYS) == {f"raptor.{key}" for key in keys}, (
        "the watched raptor keys are not the ones the schema publishes"
    )
    assert _RAPTOR_KEYS.keys() <= WATCHED_SPELLINGS.keys(), (
        "the derived raptor keys were dropped on the way into WATCHED_SPELLINGS, so "
        "the scan below would not look for them"
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

    **The ``raptor`` block rides the same enumeration** (#426). ADR-0008 decision
    10 and ``docs/architecture/raptor.md`` say no key in that block is read, and
    the day a config loader names ``"enabled"``, ``"maxLevels"`` or
    ``"minChildrenPerSummary"`` a sixth site appears here and both records have
    to be narrowed again in the same change.

    **One measured gap in that tripwire.** This is an equality over ``(module,
    spelling)`` pairs, and ``application/forest_builder.py`` already owns the
    pairs ``max_levels`` and ``min_children_per_summary`` as ``ForestOptions``
    fields. A loader added *in that one module* binding only those two snake_case
    names adds no new pair and stays green -- round-one mutation A1 SURVIVED for
    exactly that reason, and A2, the same read in another module, was KILLED. Any
    spelling of ``enabled``, any JSON or SCREAMING spelling anywhere, and either
    snake name in any other module all trip it. The gap is recorded rather than
    closed: telling a ``ForestOptions`` field from a config read inside one module
    needs the semantics this scan refuses on purpose (see the ordinary-words rule
    on :data:`_RECORDED_KEYS`).

    What this cannot see is otherwise unchanged and stated in the module
    docstring: a key assembled at runtime, or a whole-mapping read that never
    names it, still passes.
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
        "the entry.\n\n"
        "A NEW site under `raptor.`: check which of two things happened before "
        "editing anything. If the site is a config read, ADR-0008 decision 10's "
        "`Nothing in `src/` reads `raptor.enabled`, nor any other key in the "
        "`raptor` block` and docs/architecture/raptor.md's `no `raptor` key is "
        "read` are both false (#426); narrow them in the same change, and check "
        "whether `raptor.enabled`'s published default is now the switch the "
        "decision says it must default to. If it is an unrelated use of an "
        "ordinary word -- `enabled` in particular is the JSON key and an English "
        "word at once, and this scan cannot tell a string constant from an "
        "identifier -- then no record moved and the honest fix is to record the "
        "site here with a note saying which it is."
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
            (
                "`theurian propose accept` scans both the bodies a proposal would land "
                "and the migration document"
            ),
            "That is one gate and a best-effort detector, not coverage",
            # The metadata channel (#336). The scan *used* to read bodies only,
            # so a secret in the revision's own title/description/labels or
            # source anchors was unscanned while the title and the source anchors
            # are published on every result (verified 2026-08-24 in
            # mcp/results.py). It now reads the migration document's
            # author-written strings too, and a surface that describes SEC-11's
            # reach without both halves over-claims by omission -- the same B1-B4
            # failure the rest of this row guards, applied to *which channels* the
            # scan covers. The anchor field list is the completeness marker:
            # dropping the source anchors, the sharpest published channel, reddens
            # here. The two exclusion pins are the other direction, and fix on the
            # enumerated exclusion list rather than an adjective like "complete"
            # that can pin a false claim: the first holds that the date fields are
            # *scanned* -- the round-two false claim was that they were excluded --
            # and the second holds a concrete excluded field with its mechanism.
            # #349 moved `contentFile` into the scanned set -- its parsed value is a
            # document field now, so it is no longer the concrete excluded field
            # this pins. The ULID/`contentSha256` identifiers are, since their
            # class gate cannot fire; that is a claim #349 does not change.
            "provider, sourceUri, repository, filePath, externalId, commitSha, blobSha",
            "`contentType` and the `createdAt`/`validFrom`/`validTo` dates",
            (
                "the ULID- and `contentSha256`-shaped identifiers, which the "
                "detector's class gate cannot fire on"
            ),
            "`evidence.json` is not scanned",
            "Theurian does not scan ingested content for secrets",
            "Theurian is not one and is not a replacement for one",
        ),
    ),
    (
        "docs/security/threat-model.md (T-15 Controls)",
        "docs/security/threat-model.md",
        (
            (
                "**Controls: `theurian propose accept` scans every body and the migration "
                "document itself before it moves anything**"
            ),
            "It is best effort and the product says so",
            # The metadata channel (#336); see the SECURITY.md row above. The
            # anchor field list guards that the source anchors -- a published
            # channel as sharp as the title -- stay enumerated alongside it. The
            # two exclusion pins guard the other direction on the enumerated list
            # rather than an adjective: that the date fields are scanned, and the
            # per-field mechanism framing.
            (
                "`provider`, `sourceUri`, `filePath`, `repository`, `externalId`, "
                "`commitSha`, `blobSha`"
            ),
            "`aclGroup`, `contentType`, `validFrom`, `validTo`",
            "each field excluded by a mechanism rather than by choice",
            "`theurian ingest` runs no scan",
        ),
    ),
    (
        "docs/architecture/requirements-analysis.md (T-15 row)",
        "docs/architecture/requirements-analysis.md",
        (
            "scans every body it would land",
            "best-effort in-house detector",
            # The metadata channel (#336); see the SECURITY.md row above. This
            # T-15 row is the one that enumerates what the control does and does
            # not reach, so an omission either way reads as a different control.
            # The anchor field list pins the sharpest channel the round-two review
            # of #198 found. The date-field and mechanism pins fix on the
            # enumerated exclusion list: that the date fields are scanned, and that
            # each excluded field is barred by a mechanism, not by "nothing to put".
            "and the migration document's own author-written fields with it",
            "provider, sourceUri, filePath, repository, externalId, commitSha, blobSha",
            "`contentType` and the date fields",
            "What it does not reach: the document's derived fields",
            "each barred by a mechanism rather than by choice",
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
