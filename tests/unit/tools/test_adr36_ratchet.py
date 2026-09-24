"""ADR-0036 held against the tree it discharges its "Still owed" pins into.

**Why this file exists.** ec4e324e moved ADR-0036's slice-S2 "Still owed"
items into *Compliance*, scoped Amendment 1's rider 1 to where the
disclosure-equality claim is actually measured, and cited the harness's
loader-rule and pin names by name. A discharge corrects the durable text;
nothing recomputes it against the tree it describes. These pins are that
recomputation, mirroring ``test_phase0_exit_records.py``'s own instrument: a
committed test reads the live document and a live authority (the loader
source, the harness's own AST, pytest's own collection), and asserts they
still agree, so the next edit that moves one without the other reddens here
instead of ageing quietly into a record nobody rechecks.

**These hold RECORD-truth, not behaviour.** They fail when the ADR disagrees
with a live measurement of the tree it describes -- a renamed pin, a rule tag
that moved, a metric key that appeared without the ADR's channel-report
sentence moving with it. They say nothing about whether the harness is
correct; ``test_harness_pins.py`` is that file.
"""

from __future__ import annotations

import ast
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[3]
ADR = REPO_ROOT / "docs" / "adr" / "0036-golden-judgements-are-committed-regression-fixtures.md"
_HARNESS_DIR = REPO_ROOT / "tools" / "eval"
if str(_HARNESS_DIR) not in sys.path:
    sys.path.insert(0, str(_HARNESS_DIR))

import corpus as harness_corpus  # noqa: E402
import report as harness_report  # noqa: E402

CORPUS_PY = _HARNESS_DIR / "corpus.py"
REPORT_PY = _HARNESS_DIR / "report.py"
JUDGEMENTS_SCHEMA = _HARNESS_DIR / "schemas" / "judgements.schema.json"


def _section(text: str, start_marker: str, end_marker: str | None) -> str:
    """The substring between two stable, unique markers -- the ADR's own words.

    ``end_marker=None`` bounds the section at end of text, for a block with no
    following heading or paragraph to end on.
    """
    start = text.index(start_marker)
    if end_marker is None:
        return text[start:]
    end = text.index(end_marker, start)
    return text[start:end]


def _flatten(text: str) -> str:
    """Collapse markdown blockquote line-wrapping (``> `` at a line start) and
    run-together whitespace into single spaces, so a substring check does not
    fail on a soft wrap the ADR's own line width introduced -- the same
    "rejoin soft-wrapped lines" discipline ``test_phase0_exit_records.py``'s
    ``_roadmap_blocks`` applies for the same reason.
    """
    without_quote_markers = re.sub(r"^>\s?", "", text, flags=re.MULTILINE)
    return re.sub(r"\s+", " ", without_quote_markers)


# -- 1a: rider 1's amendment block carries both halves ------------------------

_AMENDMENT_START = "> **Amended 2026-09-20, after slices S2"
_RIDER_2_START = "**2. The census is the test"

#: Short, distinctive substrings of the two bolded lead-ins inside rider 1's
#: amendment block -- not whole sentences, so a copy-edit that keeps the
#: substance but rewords around it does not falsely redden this. Checked
#: against the flattened block since the second phrase wraps mid-word
#: ("collection\n>   statistic") at the ADR's own line width.
_ASSERTED_WHERE_MEASURED = "Asserted where it is measured and pinned"
_RECORDED_CHANNEL = "recorded channel where a withheld row can move a collection statistic"


def test_rider_1s_amendment_carries_both_the_asserted_and_recorded_channel_halves(
    adr_path: Path = ADR,
) -> None:
    """Guards against the ADR reverting to unconditional whole-response equality.

    Rider 1's amendment corrected exactly this overclaim: the original text
    read as asserting the disclosure-equality set equality wherever the
    battery runs, and the fix was to split that into two halves -- asserted
    where it is measured and pinned (the smoke corpus), and recorded as an
    unasserted channel where BM25 collection statistics can still move (the
    S3 corpus, #787). Losing either half back into one unconditional claim is
    the exact regression this keys on both phrases coexisting in one block.
    """
    block = _flatten(
        _section(adr_path.read_text(encoding="utf-8"), _AMENDMENT_START, _RIDER_2_START)
    )

    assert _ASSERTED_WHERE_MEASURED in block
    assert _RECORDED_CHANNEL in block


# -- 1b: every test name cited in the ADR's S2 Compliance block AND rider 1's -
#         amendment block collects -------------------------------------------

_S2_COMPLIANCE_START = "Landed in Phase A slice S2 (`9cd9ee34`)"
_S2_COMPLIANCE_END = "\nMeasured now, and reproducible from this ADR"

#: The S4 Compliance block, appended after the S2 one and after
#: ``_S2_COMPLIANCE_END``'s own marker text -- outside both existing
#: populations, which is why its one citation (the S4b baseline pin) was
#: checked by hand rather than by this pin until now. No dedicated heading
#: follows the block to end on: the next stable, unique text is the
#: paragraph recording that nothing in this ADR remains owed to a later
#: phase, which is prose about the ADR's own structure rather than part of
#: the compliance record itself, so it is where the block ends.
_S4_COMPLIANCE_START = "Landed in Phase A slice S4 — the committed baseline and the advisory CI"
_S4_COMPLIANCE_END = "\n**Nothing in this ADR is owed to a later phase"

#: Amendment 2's block, appended after every marker above. It cites the pin
#: that exercises the restated sensitivity clause, so it joins this population
#: in the same commit that lands it rather than being hand-checked once. Ends
#: at end of file, not at its closing paragraph's opening bold: Amendment 2 is
#: the file's last section, with no following ``## `` heading, and bounding at
#: the paragraph's own opening words left that paragraph's body -- where a
#: cited test name would land -- outside the block (a code-review MEDIUM,
#: PR #803 round two: a citation appended there would escape this population
#: silently).
_AMENDMENT_2_START = "## Amendment 2 — `createItem.sensitivity` is the base"
_AMENDMENT_2_END: str | None = None

#: A backtick-quoted test identifier, ``path::test_name`` or a bare
#: ``test_name`` -- the two forms both blocks below actually use. Group 1 is
#: the optional path prefix (``""`` for a bare citation, since ``findall``
#: yields an empty string rather than ``None`` for a non-participating
#: group).
_CITED_TEST_NAME = re.compile(r"`(?:([\w./-]+)::)?(test_[A-Za-z0-9_]+)`")

#: The four blocks the ADR cites test names by name from, each enforced
#: independently. S4a's #787 append landed a new citation inside rider 1's
#: own amendment block (``_AMENDMENT_START``/``_RIDER_2_START``, pin 1a's
#: bounds), and the S4b docs pass landed another inside the new S4 Compliance
#: block -- each time a block only the earlier population(s) were checking,
#: so the new citation went unenforced and was verified by hand instead.
_CITED_TEST_SECTIONS = (
    pytest.param(_S2_COMPLIANCE_START, _S2_COMPLIANCE_END, id="s2-compliance"),
    pytest.param(_AMENDMENT_START, _RIDER_2_START, id="rider-1-amendment"),
    pytest.param(_S4_COMPLIANCE_START, _S4_COMPLIANCE_END, id="s4-compliance"),
    pytest.param(_AMENDMENT_2_START, _AMENDMENT_2_END, id="amendment-2"),
)


def _cited_test_citations(
    start_marker: str, end_marker: str | None, adr_path: Path = ADR
) -> list[tuple[str, str]]:
    """Every ``(path, test name)`` pair one block of the ADR cites -- path
    ``""`` for a bare citation -- parsed from the ADR's own text.
    """
    text = adr_path.read_text(encoding="utf-8")
    section = _section(text, start_marker, end_marker)
    return sorted(set(_CITED_TEST_NAME.findall(section)))


def _collect_only() -> subprocess.CompletedProcess[str]:
    """One ``pytest --collect-only`` pass over every root the cited names live in.

    One collection pass rather than one per name (or per cited block):
    collecting ``packages/theurian-core/tests`` alone takes a few seconds,
    and this file cites upward of a dozen names across two blocks, one of
    them from that tree (the product's own build-identity sibling test).
    """
    return subprocess.run(  # noqa: S603 - argv is module-owned, never user input
        [
            sys.executable,
            "-m",
            "pytest",
            "--collect-only",
            "-q",
            str(REPO_ROOT / "tests" / "unit" / "tools"),
            str(REPO_ROOT / "tests" / "integration" / "tools"),
            str(REPO_ROOT / "packages" / "theurian-core" / "tests"),
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


def _collected_node_id_components(stdout: str) -> list[tuple[str, str]]:
    """Each collected node id's ``(path, function name)``, split on its own ``::``.

    A parametrized id's ``[...]`` suffix is stripped from the function name,
    so a bare-name citation and a path-qualified one see the same base name
    either way. Compared as components rather than as one raw string below,
    so a citation cannot pass by substring-matching a different test's node
    id -- a shorter name inside a longer one, or a name that happens to occur
    inside some other test's path.
    """
    components: list[tuple[str, str]] = []
    for line in stdout.splitlines():
        if "::" not in line:
            continue
        path, _, rest = line.partition("::")
        name = rest.rsplit("::", 1)[-1].split("[", 1)[0]
        components.append((path, name))
    return components


@pytest.fixture(scope="module")
def collected_test_node_ids() -> list[tuple[str, str]]:
    """One collection pass, shared by every citation population below -- running
    it once per parametrized block instead of once overall would double the
    cost of ``_collect_only`` for no new signal.
    """
    collected = _collect_only()
    assert collected.returncode in {0, 5}, (
        f"pytest --collect-only exited {collected.returncode}, neither a clean "
        f"collection (0) nor a legitimately empty one (5) -- a collection error "
        f"would make the missing-name check below pass vacuously:\n{collected.stderr}"
    )
    return _collected_node_id_components(collected.stdout)


@pytest.mark.parametrize(("start_marker", "end_marker"), _CITED_TEST_SECTIONS)
def test_every_test_name_cited_in_the_adrs_s2_compliance_block_collects(
    start_marker: str, end_marker: str | None, collected_test_node_ids: list[tuple[str, str]]
) -> None:
    """A renamed, moved or deleted pin reddens the ADR's own citation record.

    All four blocks name pins by their test function name -- some
    path-qualified, most bare -- as the record of what each named claim is
    held by. A rename anywhere in ``tests/unit/tools/``,
    ``tests/integration/tools/`` or the product's own test tree would
    otherwise leave a citation pointing at a test that no longer exists,
    unnoticed. A path-qualified citation is held to that exact path, not
    merely to a same-named test living anywhere in the tree -- a move that
    renamed no function would otherwise pass silently. Parametrized over all
    four blocks rather than one shared scan: each was landed by a docs pass
    that appended a new citation outside every population this pin already
    covered -- rider 1's amendment block (id ``rider-1-amendment``, S4a) and
    the S4 Compliance block (id ``s4-compliance``, S4b) -- checked by hand,
    not by this pin, until each joined it. Exactly the check that rots.
    Amendment 2 (id ``amendment-2``) joined in the commit that landed it.
    """
    citations = _cited_test_citations(start_marker, end_marker)
    assert citations, "the population must be non-empty, or this pin checks nothing"

    missing: list[str] = []
    for path, name in citations:
        if path:
            if (path, name) not in collected_test_node_ids:
                missing.append(f"{path}::{name}")
        else:
            pattern = re.compile(rf"\b{re.escape(name)}\b")
            if not any(
                pattern.search(collected_name) for _, collected_name in collected_test_node_ids
            ):
                missing.append(name)
    assert missing == [], (
        f"the ADR cites {missing} by name, and pytest's collection over "
        f"tests/unit/tools, tests/integration/tools and "
        f"packages/theurian-core/tests contains no test with that name -- it was "
        f"renamed, moved or deleted without the ADR's record moving with it"
    )


# -- 1c: the five within-document rule names are live CorpusError tags ------

_WITHIN_DOCUMENT_BULLET_START = "**The loader's within-document obligations**"
_WITHIN_DOCUMENT_BULLET_END = "**The determinism pin for decisions 5 and 7**"

#: A kebab-case rule name immediately followed by its driving test in
#: parentheses -- the exact shape the within-document-obligations bullet
#: uses, which is distinctive enough not to also catch an unrelated
#: kebab-case token (a parametrize id, a filename) elsewhere in the ADR.
_RULE_NAME_WITH_TEST = re.compile(r"`([a-z]+(?:-[a-z]+)+)`\s*\n?\s*\(`test_[A-Za-z0-9_]+`\)")


def _cited_within_document_rule_names(adr_path: Path = ADR) -> list[str]:
    text = adr_path.read_text(encoding="utf-8")
    section = _section(text, _WITHIN_DOCUMENT_BULLET_START, _WITHIN_DOCUMENT_BULLET_END)
    return sorted(set(_RULE_NAME_WITH_TEST.findall(section)))


def _corpuserror_rule_tags() -> set[str]:
    """Every string literal ``corpus.py`` raises a ``CorpusError`` with."""
    source = CORPUS_PY.read_text(encoding="utf-8")
    return set(re.findall(r'CorpusError\(\s*\n?\s*"([a-z-]+)"', source))


def test_the_five_within_document_rule_names_the_adr_cites_are_live_corpuserror_tags() -> None:
    """A renamed loader rule reddens the ADR's own citation of it.

    The within-document-obligations bullet names five rule tags
    (``duplicate-query-id``, ``duplicate-judgement-query-id``,
    ``relevant-forbidden-overlap``, ``empty-judgement``,
    ``evidence-subsumption``) as the ones ``corpus.py`` raises for a
    within-document violation. Read from the ADR's own text against
    ``corpus.py``'s own ``CorpusError`` call sites, not restated as a literal
    list here, so a rename on either side is what this pin exists to catch.

    The population is pinned at exactly five: the within-document-obligations
    bullet enumerates exactly those five rules, and ``corpus.py``'s other
    eight ``CorpusError`` tags (``file-readable``, ``migration-order``,
    ``visible-depends-on-withheld``, ``migration-order-not-topological``,
    ``judgement-unknown-query``, ``query-missing-judgement``,
    ``withheld-item-disclosable``, ``relevant-item-unretrievable``) are
    cross-file or build-time rules outside that bullet's population -- a
    set-equality check against every live tag would fail on all eight of them
    and checks the wrong population.
    """
    cited = _cited_within_document_rule_names()
    assert cited, "the population must be non-empty, or this pin checks nothing"
    assert len(cited) == 5, (
        f"the within-document-obligations bullet is expected to name exactly "
        f"the five within-document rules, found {cited} -- either a rule was "
        f"added or removed from the bullet, or the regex is now over- or "
        f"under-matching it"
    )

    live_tags = _corpuserror_rule_tags()

    missing = [rule for rule in cited if rule not in live_tags]
    assert missing == [], (
        f"the ADR cites {missing} as a CorpusError rule tag in tools/eval/corpus.py, "
        f"but no CorpusError there raises with that tag now -- it was renamed"
    )


# -- 1d: Amendment 2's restated clause carries both the createItem base and --
#         DEFAULT_SENSITIVITY fallback halves --------------------------------

#: Short, distinctive substrings of the restated sensitivity clause's own
#: words -- not the whole enumerated item, so a copy-edit that keeps the
#: substance but rewords around it does not falsely redden this. Checked
#: against the flattened block since the enumerated item wraps mid-clause
#: ("`DEFAULT_SENSITIVITY`\n   (`theurian.domain.migration`") at the ADR's
#: own line width.
_CREATEITEM_SENSITIVITY_IS_THE_BASE = "`createItem.sensitivity` — the base"
_DEFAULT_SENSITIVITY_IS_THE_FALLBACK = (
    "`DEFAULT_SENSITIVITY` (`theurian.domain.migration`, `internal` today) "
    "when that operation omits the optional field"
)


def test_amendment_2s_restated_clause_carries_both_the_base_and_fallback_halves(
    adr_path: Path = ADR,
) -> None:
    """Guards against Amendment 2 reverting to Amendment 1's no-base clause.

    Amendment 2 corrected exactly this overclaim: Amendment 1's derivation
    rule began the sensitivity fold at the first revision, so a member
    created ``confidential`` and never revised read as the default,
    ``internal``. The FACT side of that correction is pinned by
    ``test_the_fold_credits_create_items_sensitivity_when_no_revision_overrides_it``
    (``tests/unit/tools/test_corpus_fixture_consistency.py``), which reddens
    if the fold's ``createItem`` branch is removed from the source. This pins
    the PROSE side -- the restated clause reverting to revision-only wording
    would leave the fold correct and the record wrong again, unnoticed, since
    pin 1b only checks that the citations inside this block still collect.
    """
    block = _flatten(
        _section(adr_path.read_text(encoding="utf-8"), _AMENDMENT_2_START, _AMENDMENT_2_END)
    )

    assert _CREATEITEM_SENSITIVITY_IS_THE_BASE in block, (
        f"Amendment 2's block no longer names createItem.sensitivity as the "
        f"sensitivity fold's base -- reverted toward Amendment 1's no-base "
        f"wording (from {_AMENDMENT_2_START!r} through end of file)"
    )
    assert _DEFAULT_SENSITIVITY_IS_THE_FALLBACK in block, (
        f"Amendment 2's block no longer names DEFAULT_SENSITIVITY as the "
        f"base's fallback when createItem omits the optional field "
        f"(from {_AMENDMENT_2_START!r} through end of file)"
    )


# -- 2: the #787 tripwire -- the published per-query metric key set ---------

#: Today's expected key set. A DELIBERATE snapshot, not a derivation: the
#: whole point is that it does NOT move when report.py gains a new key, so
#: that the disagreement is visible rather than silently absorbed.
EXPECTED_QUERY_METRIC_KEYS = frozenset(
    {
        "recallAtK",
        "mrr",
        "evidencePrecision",
        "forbiddenPresent",
        "abstentionCorrect",
        "forbiddenPresentCause",
        "abstentionCause",
    }
)


def _query_metrics_key_set() -> frozenset[str]:
    """Every key ``report._query_metrics`` can publish, read via its own AST.

    Instrument: parses ``report.py``, locates the ``_query_metrics`` function
    definition, and walks its body for (a) any dict-literal assigned there
    (``recallAtK``'s own comprehension is a ``DictComp``, not a ``Dict``, so
    its ``str(k)`` keys are correctly excluded) and (b) any
    ``metrics["key"] = ...`` subscript *store* (the conditional
    ``forbiddenPresentCause`` branch) -- never a subscript *read*. Scoped to
    this one function's body, so ``_query_entry``'s own ``atEqualityLimit``
    wrapper key, one function up, is correctly excluded: it is a nesting
    label, not a per-query metric.
    """
    tree = ast.parse(REPORT_PY.read_text(encoding="utf-8"), filename=str(REPORT_PY))
    function = next(
        (
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef) and node.name == "_query_metrics"
        ),
        None,
    )
    assert function is not None, (
        f"{REPORT_PY} no longer defines _query_metrics -- update this scan (and "
        f"EXPECTED_QUERY_METRIC_KEYS below it) to whatever replaced it"
    )
    keys: set[str] = set()
    for node in ast.walk(function):
        if isinstance(node, ast.Dict):
            for key_node in node.keys:
                if isinstance(key_node, ast.Constant) and isinstance(key_node.value, str):
                    keys.add(key_node.value)
        elif (
            isinstance(node, ast.Subscript)
            and isinstance(node.ctx, ast.Store)
            and isinstance(node.slice, ast.Constant)
            and isinstance(node.slice.value, str)
        ):
            keys.add(node.slice.value)
    return frozenset(keys)


def test_the_787_tripwire_published_per_query_metric_key_set_equals_todays_expected_set() -> None:
    """Not a frozen contract -- a deliberate tripwire, and #787 has already
    tripped it once (past tense, corrected: the ADR now records both members
    implemented, not owed).

    The tripwire's originally stated target -- the collection-statistics
    channel Amendment 1's rider 1 recorded as unasserted -- landed as
    ``equality.channel``, a member of ``build_report``'s own return dict built
    by ``_channel_summary``, never a key ``_query_metrics`` writes. This
    tripwire, scoped to ``_query_metrics``'s own AST (see
    :func:`_query_metrics_key_set`), could never have caught it landing --
    that claim was wrong from the start. What actually tripped it was
    ``abstentionCause``, #787's other member and a genuine per-query key,
    added to :data:`EXPECTED_QUERY_METRIC_KEYS` in the same commit that
    introduced it (5955989a), the owed-to-implemented signal this file's own
    module docstring describes. ``equality.channel`` is held instead by
    ``test_the_equality_channel_summary_carries_the_787_reason_verbatim_and_the_measured_counts``
    (``tests/integration/tools/test_harness_pins.py``), a section-level pin
    outside this snapshot's reach.

    The snapshot now guards whichever per-query key ``_query_metrics`` gains
    next: the moment one appears here uninvited, this reddens, and that RED
    is the signal to decide -- in the same commit -- whether the new key
    belongs in :data:`EXPECTED_QUERY_METRIC_KEYS` and whether some ADR
    sentence needs to move with it.
    """
    assert _query_metrics_key_set() == EXPECTED_QUERY_METRIC_KEYS


# -- 3: the no-target pin -- decision 4's harness half -----------------------

#: Unambiguous quality-gate vocabulary: nothing legitimate in this harness is
#: named with any of these words. Deliberately NOT ``MIN_``/``MAX_``: those
#: prefixes are shared by legitimate bounds (``MAX_TOKENS``, a request-
#: parameter cap; ``EQUALITY_LIMIT``, a harness width) and by a quality
#: floor/ceiling (a hypothetical ``MIN_RECALL``), and no rule distinguishes
#: them without an allowlist that would defeat the point of a ratchet -- an
#: allowlist is exactly the kind of key a future author routes a threshold
#: around. An honest narrow key beats a clever broad one that needs one.
_THRESHOLD_FAMILY = re.compile(r"THRESHOLD|FLOOR|TARGET", re.IGNORECASE)


def _identifiers(tree: ast.AST) -> list[str]:
    """Every real Python identifier in ``tree`` -- names, arguments, attributes,
    function/class names, keyword-argument names and import aliases. Never a
    string literal, comment or docstring: this is what makes the scan immune
    to a docstring saying "no threshold" (three of ``tools/eval``'s own
    module docstrings say exactly that).
    """
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            names.append(node.id)
        elif isinstance(node, ast.arg):
            names.append(node.arg)
        elif isinstance(node, ast.Attribute):
            names.append(node.attr)
        elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            names.append(node.name)
        elif isinstance(node, ast.keyword) and node.arg is not None:
            names.append(node.arg)
        elif isinstance(node, ast.alias):
            names.append(node.asname or node.name)
    return names


def test_no_threshold_floor_or_target_named_identifier_exists_in_the_harness() -> None:
    """ADR-0036 decision 4's harness half, held by a machine check rather than prose.

    **Reach, stated honestly.** This catches an identifier spelling
    THRESHOLD, FLOOR or TARGET anywhere in ``tools/eval/`` (recursively) -- a
    ``RECALL_THRESHOLD`` or a ``LATENCY_TARGET_MS`` would redden here, however
    deeply nested. It does **not** catch a quality gate authored under a
    ``MIN_``/``MAX_`` name (see the constant above for why): that shape is
    caught only if it also surfaces as a published report key, which
    :func:`test_a_built_report_carries_no_pass_fail_or_threshold_named_key`
    below checks. Between the two, an identifier-named threshold and a
    published one are covered; a threshold that is neither named plainly nor
    ever reaches the report is not something a machine check can see.
    """
    offenders = [
        f"{path.relative_to(_HARNESS_DIR)}:{identifier}"
        for path in sorted(_HARNESS_DIR.rglob("*.py"))
        for identifier in _identifiers(
            ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        )
        if _THRESHOLD_FAMILY.search(identifier)
    ]
    assert offenders == []


def _every_dict_key(value: Any) -> list[str]:
    keys: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            keys.append(key)
            keys.extend(_every_dict_key(item))
    elif isinstance(value, list):
        for item in value:
            keys.extend(_every_dict_key(item))
    return keys


def _synthetic_report() -> dict[str, Any]:
    """A real ``report.build_report()`` output, built from hand-constructed
    inputs rather than a real CLI/SQLite harness run.

    ``build_report`` is a pure function of a :class:`Corpus`, a
    :class:`HarnessConstants` and a list of :class:`QueryRun`\\ s (each just a
    dataclass wrapping a raw response dict) -- none of which need a real
    build to construct. This is what makes the report-key check below a
    UNIT-weight instrument: a genuine call into the module under test, not a
    guess about its shape, and no SQLite or subprocess required to make it.

    Three queries, not one: a single-corpus, non-abstention query alone never
    takes ``report.py``'s conditional branches -- the ``forbiddenPresentCause``
    cause, the ``atEqualityLimit`` wrapper key, the equality section's own
    ``limit``/``differingFields``/``atLimit`` keys, or ``abstentionCause`` and
    its sibling top-level ``abstentionProbe`` member -- so a verdict key added
    only inside one of them could hide from the no-verdict-key scan below by
    simply never being built. ``q-equality`` runs against ``("full",
    "clean")`` to take the equality branches; ``q``'s forbidden item is
    classified census-tested in ``withheld_coverage`` to take the
    ``forbiddenPresentCause`` branch; ``q-abstention`` pairs an empty
    default-flags ``full`` response with an ``include_unapproved=True`` probe
    run at the same limit (the NEW per-limit shape, e49c6520) returning a hit,
    so ``_abstention_cause`` takes its gate-earned branch and ``build_report``
    gains its ``abstentionProbe`` member. The reach test below checks this
    premise rather than assuming it.
    """
    manifest = harness_corpus.Manifest(
        contract_version=1,
        corpus_id="no-target-pin",
        k_values=(1,),
        migrations=(),
        census={},
        description=None,
    )
    base_query = harness_corpus.QueryEntry(
        id="q", query_class="exact-decision", query="text", enabled=True, corpora=("full",)
    )
    equality_query = harness_corpus.QueryEntry(
        id="q-equality",
        query_class="exact-decision",
        query="text",
        enabled=True,
        corpora=("full", "clean"),
    )
    abstention_query = harness_corpus.QueryEntry(
        id="q-abstention", query_class="unknown", query="text", enabled=True, corpora=("full",)
    )
    base_judgement = harness_corpus.JudgementEntry(
        query_id="q",
        relevant=(harness_corpus.JudgedItem(item_id="a"),),
        evidence=(),
        forbidden=(harness_corpus.JudgedItem(item_id="w"),),
        expect_abstention=False,
    )
    equality_judgement = harness_corpus.JudgementEntry(
        query_id="q-equality",
        relevant=(harness_corpus.JudgedItem(item_id="a"),),
        evidence=(),
        forbidden=(),
        expect_abstention=False,
    )
    abstention_judgement = harness_corpus.JudgementEntry(
        query_id="q-abstention",
        relevant=(),
        evidence=(),
        forbidden=(),
        expect_abstention=True,
    )
    withheld_coverage = (
        harness_corpus.WithheldItemCoverage(
            item_id="w",
            final_status="deprecated",
            final_sensitivity="internal",
            is_gate_tested=False,
        ),
    )
    loaded = harness_corpus.Corpus(
        root=Path(),
        manifest=manifest,
        queries=(base_query, equality_query, abstention_query),
        judgements=(base_judgement, equality_judgement, abstention_judgement),
        withheld_coverage=withheld_coverage,
    )
    constants = harness_report.HarnessConstants(
        limit=10,
        max_tokens=1000,
        include_unapproved=False,
        use_dense=False,
        equality_limit=50,
        build_ceiling="internal",
    )
    response: dict[str, Any] = {"count": 1, "results": [{"itemId": "a", "sourceAnchors": []}]}
    empty_response: dict[str, Any] = {"count": 0, "results": []}
    runs = [
        harness_report.QueryRun(
            query_id="q", corpus="full", limit=10, response=response, latency_ms=1.0
        ),
        harness_report.QueryRun(
            query_id="q-equality", corpus="full", limit=10, response=response, latency_ms=1.0
        ),
        harness_report.QueryRun(
            query_id="q-equality", corpus="full", limit=50, response=response, latency_ms=1.0
        ),
        harness_report.QueryRun(
            query_id="q-equality", corpus="clean", limit=10, response=response, latency_ms=1.0
        ),
        harness_report.QueryRun(
            query_id="q-equality", corpus="clean", limit=50, response=response, latency_ms=1.0
        ),
        harness_report.QueryRun(
            query_id="q-abstention",
            corpus="full",
            limit=10,
            response=empty_response,
            latency_ms=1.0,
        ),
        harness_report.QueryRun(
            query_id="q-abstention",
            corpus="full",
            limit=10,
            response=response,
            latency_ms=1.0,
            include_unapproved=True,
        ),
    ]
    census = {
        "full": harness_corpus.CorpusCensus(
            items=1, by_status={"approved": 1}, by_sensitivity={"public": 1}, chunks=1
        ),
        "clean": harness_corpus.CorpusCensus(
            items=1, by_status={"approved": 1}, by_sensitivity={"public": 1}, chunks=1
        ),
    }
    return harness_report.build_report(loaded, constants, runs, census)


#: Keys ``report.py`` only builds inside a conditional branch: the
#: ``forbiddenPresentCause`` cause, the equality-query ``atEqualityLimit``
#: wrapper, the equality section's own ``limit``/``differingFields``/
#: ``atLimit`` keys, ``abstentionCause`` (#787's gate-earned annotation), and
#: its sibling top-level ``abstentionProbe`` member (built only when some
#: ``QueryRun`` carries ``include_unapproved=True``). A one-query,
#: single-corpus, non-abstention fixture takes none of them.
_BRANCH_REACH_MARKERS = frozenset(
    {
        "forbiddenPresentCause",
        "atEqualityLimit",
        "limit",
        "differingFields",
        "atLimit",
        "abstentionCause",
        "abstentionProbe",
    }
)


def test_the_synthetic_report_fixture_reaches_every_conditional_report_branch() -> None:
    """The reach premise the no-verdict-key scan below depends on.

    A verdict key added only inside a conditional branch report.py never
    takes would pass the scan below by never being built, not by being
    absent -- the failure mode a one-query synthetic corpus previously left
    open. This asserts the fixture is not that: every key the widened report
    can only carry via a conditional branch is present in what
    :func:`_synthetic_report` actually returns, so the scan below is known to
    exercise the branches it claims to cover.
    """
    scanned = set(_every_dict_key(_synthetic_report()))
    missing = _BRANCH_REACH_MARKERS - scanned
    assert missing == set(), (
        f"the synthetic report fixture no longer reaches {sorted(missing)} -- "
        f"report.py's branch(es) publishing them went untaken, so the "
        f"no-verdict-key scan below would not see a verdict key added inside them"
    )


_PASS_FAIL_THRESHOLD = re.compile(r"pass|fail|threshold", re.IGNORECASE)


def test_a_built_report_carries_no_pass_fail_or_threshold_named_key() -> None:
    """The report-key half of decision 4's harness pin: a built report publishes
    counts and classifications, never a verdict. A ``passed``, ``failed`` or
    ``thresholdMet``-shaped key appearing anywhere in the report -- at any
    nesting depth -- would be the harness starting to assert rather than
    produce (decision 4's own words), and reddens here.
    """
    report = _synthetic_report()
    offending = [key for key in _every_dict_key(report) if _PASS_FAIL_THRESHOLD.search(key)]
    assert offending == []


# -- 4: the undriven exact-pair evidence case --------------------------------


def test_judgements_schema_refuses_two_evidence_entries_sharing_the_exact_pair() -> None:
    """Converts the ADR's Compliance record from schema-property to stated fact.

    The S1 compliance bullet on evidence-subsumption records that the exact-
    ``(sourceUri, filePath)``-pair case is undriven by any committed test --
    it follows from ``evidenceRef`` being closed at exactly
    ``{sourceUri, filePath}`` plus the ``evidence`` array's own
    ``uniqueItems``, never from a planted instance (the S1 pin,
    ``test_judgements_rejects_duplicate_evidence_entries``, plants two
    bare-``sourceUri`` entries only). This plants the pair case directly, and
    asserts ``uniqueItems`` is the validator doing the refusing -- the
    mechanism the Compliance record credits, not merely that validation fails
    for some reason. The distinct-pair control (same ``sourceUri``, different
    ``filePath``) is the shape the refusal must NOT catch: two entries each
    narrowing the URI to a different file are not the same object.
    """
    schema = json.loads(JUDGEMENTS_SCHEMA.read_text(encoding="utf-8"))
    validator = Draft202012Validator(schema)
    judgements = {
        "judgements": [
            {
                "queryId": "sample-query",
                "evidence": [
                    {"sourceUri": "https://example.com/doc", "filePath": "README.md"},
                    {"sourceUri": "https://example.com/doc", "filePath": "README.md"},
                ],
            }
        ]
    }

    errors = list(validator.iter_errors(judgements))
    assert errors, "the exact-pair case is expected to be refused, but validated cleanly"
    assert any(error.validator == "uniqueItems" for error in errors), (
        f"the exact-pair case was refused, but not by uniqueItems as the ADR's "
        f"Compliance record credits -- refused instead by "
        f"{sorted({error.validator for error in errors})}"
    )

    distinct_pair = {
        "judgements": [
            {
                "queryId": "sample-query",
                "evidence": [
                    {"sourceUri": "https://example.com/doc", "filePath": "README.md"},
                    {"sourceUri": "https://example.com/doc", "filePath": "OTHER.md"},
                ],
            }
        ]
    }
    assert validator.is_valid(distinct_pair), (
        "two evidence entries sharing a sourceUri but narrowing it to different "
        "files are a distinct pair each, not the same object -- uniqueItems must "
        "not refuse this shape, or the refusal above is broader than the ADR credits"
    )
