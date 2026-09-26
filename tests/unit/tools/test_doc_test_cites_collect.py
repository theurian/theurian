"""Every `` `path.py::test_name` `` (or bare `` `::test_name` ``) citation in this
repo's living governed docs must still collect.

**Why this file exists.** The class fired three times with zero machine checks:
a predecessor inside PR #811's own review rounds, and two more in ADR-0037's S3
Compliance block that PR #818 corrected -- a test renamed in #811 round two
(``by_key`` -> ``by_kind_key``) and a bare ``::test_name`` cite that read as the
wrong file by proximity to an unrelated citation just before it. Nothing
extracted a ``file::test`` cite from governed prose and checked it collects;
``tools/audit/owner_position_cites.py`` audits ISSUE numbers, a different class.

**Population, measured, not hand-chosen.**
``git grep -lE '\\S+\\.py::test_[A-Za-z0-9_]+|::test_[A-Za-z0-9_]+' -- docs`` on
this tree (at ``5f7a9d91``, #818's tip) finds every ``docs/**/*.md`` carrying the
citation grammar below:

    docs/adr/0001-monorepo-with-independent-artifacts.md
    docs/adr/0002-single-local-daemon-over-streamable-http.md
    docs/adr/0004-sqlite-is-a-derived-artifact.md
    docs/adr/0006-immutable-revisions-and-optimistic-concurrency.md
    docs/adr/0007-state-hash-partitioned-databases.md
    docs/adr/0008-raptor-forest.md
    docs/adr/0009-no-llm-vendor-lock-in.md
    docs/adr/0010-three-layer-knowledge-model.md
    docs/adr/0011-local-mcp-authentication.md
    docs/adr/0012-plugin-does-not-autoregister-mcp-server.md
    docs/adr/0013-ai-writes-produce-proposals.md
    docs/adr/0014-dependency-pinning-and-pre-1-0-isolation.md
    docs/adr/0016-state-hash-covers-the-working-tree.md
    docs/adr/0017-sqlite-schema-versioning.md
    docs/adr/0018-single-writer-synchronous-in-m1.md
    docs/adr/0021-rank-fusion-over-score-normalisation.md
    docs/adr/0022-index-lives-in-its-own-database.md
    docs/adr/0023-trigram-index-beside-the-word-index.md
    docs/adr/0024-a-purge-is-a-build.md
    docs/adr/0025-sensitivity-is-enforced-before-0-1-0-stable.md
    docs/adr/0026-evidence-plane-not-control-plane.md
    docs/adr/0027-accept-validates-before-it-moves.md
    docs/adr/0028-a-local-proposal-is-a-different-directory.md
    docs/adr/0029-review-findings-are-governed-knowledge.md
    docs/adr/0030-github-review-ingestion-spawns-gh.md
    docs/adr/0031-mcp-input-is-schema-validated-in-middleware.md
    docs/adr/0032-the-write-intent-mcp-tool-surface.md
    docs/adr/0033-knowledge-candidate-generation.md
    docs/adr/0034-migrate-apply-enforces-the-merge.md
    docs/adr/0035-interactive-source-curation-is-agent-mediated.md
    docs/adr/0036-golden-judgements-are-committed-regression-fixtures.md
    docs/adr/0037-okf-is-the-knowledge-layer-interchange.md
    docs/architecture/cloud-ready-design.md
    docs/architecture/raptor.md
    docs/architecture/requirements-analysis.md
    docs/architecture/review-knowledge.md
    docs/architecture/traceability.md
    docs/contributing/orchestration.md
    docs/contributing/release.md
    docs/integrations/claude-code.md
    docs/roadmap.md
    docs/security/local-mcp.md
    docs/security/threat-model.md
    docs/work-logs/2026-08-31-427-owner-cite-sweep.md
    docs/work-logs/2026-09-01-428-closed-owner-aggregate.md
    docs/work-logs/2026-09-01-472-purged-build-re-measurement.md
    docs/work-logs/2026-09-16-t26-timing.md

**One deliberate exclusion: ``docs/work-logs/``.** A work-log is a point-in-time
record of what a session found -- it is amended, never trimmed (CLAUDE.md,
*Standing conventions*), and a test it names may legitimately no longer exist by
the time the record is read. Every other hit is checked, including the
architecture, contributing, integrations, roadmap and non-threat-model security
docs above: none of those is a filing-time snapshot, so none earns the same
exemption.

**The citation grammar requires a literal ``::``.** A plain `` `test_x` `` with
no colon at all (``docs/contributing/development.md``'s style-guide example
``test_resolve_2``, deliberately a name nobody should write, and
``docs/contributing/orchestration.md``'s possessive `` `test_bare_install`'s ``
naming a *file* rather than a function) is informal prose, not a citation, and
is outside this grammar for that reason -- matching the two grep patterns above,
neither of which matches a colon-less mention. An ellipsis-elided path (`` `…/
test_x.py::test_name` ``, a handful of threat-model table cells) also falls
outside it: ``…`` is not a path character, so the citation is simply uncounted
rather than falsely matched -- an omission, never a false pass.

**A bare citation must resolve to exactly one collected test.** Zero matches is
the dangling shape this file exists for. More than one is the shape #818's bare
fix closed one instance of: a bare name that reads as the file cited moments
before it while the same-named function actually lives elsewhere, so a rename
anywhere in the tree can point a reader at the wrong test without anything
turning red. Both need the same fix, an explicit path.

**Mechanism reused, not reinvented**, from ``test_adr36_ratchet.py``'s own
citation-collect instrument: one ``pytest --collect-only`` pass, and a citation
resolves by exact ``(path, name)`` match or by basename (the ``…/x.py`` and
bare-filename-without-directory house styles this corpus already writes in).

**``KNOWN_EXCEPTIONS`` is a debt ledger, exact in both directions** -- the same
discipline ``tools/audit/controls_discharge.py``'s ``PROSE_ONLY`` holds itself
to. Every row names a citation that will never collect on purpose, because the
prose around it explicitly retires it as part of an "Amended ..." governed
record (CLAUDE.md: amendment blocks are amended, never trimmed):

- ADR-0013's and ADR-0032's Milestone-3/4 tool-set names, each superseded in the
  same sentence that cites them ("it became ...", "(it was ...)"), landing at
  today's ``test_the_tool_set_is_exactly_the_published_ten``.
- ADR-0033's "not files that exist today" B5 residue
  (``test_nothing_in_the_shipped_package_constructs_a_knowledge_candidate``,
  discharged by slice B5 into
  ``test_the_only_construction_site_of_a_knowledge_candidate_is_the_candidate_generator``)
  and its 0.4.0-cut fixture correction (the ``errors="replace"`` fixture that
  could not produce the surrogate shape its own name claimed, this project's own
  burned-in lesson).
- The threat model's T-17a correction note: "Nothing in the suite stood behind
  the deleted prose."

A row that stops being a genuine violation (the prose was edited to drop the
stale name) is stale and must be deleted --
:func:`test_the_known_exception_ledger_has_no_stale_rows` holds that direction.
"""

from __future__ import annotations

import bisect
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[3]
DOCS_ROOT = REPO_ROOT / "docs"
_EXCLUDED_DIR_NAME = "work-logs"

#: `` `path.py::test_name` `` (group 1 non-empty) or `` `::test_name` `` (group 1
#: empty) -- the literal ``::`` is mandatory in both, matching the two grep
#: patterns the population above was measured with. ``[\w./-]`` is the path
#: alphabet this corpus's citations actually use: word characters, dots,
#: slashes and hyphens.
_CITE = re.compile(r"`([\w./-]*)::(test_[A-Za-z0-9_]+)`")

#: A prefix carrying no path character at all -- an elided ``...`` -- is not a
#: path; it is treated as bare.
_NO_PATH_CHAR = re.compile(r"[A-Za-z0-9_]")


@dataclass(frozen=True, slots=True)
class Citation:
    doc: str
    line: int
    prefix: str
    name: str

    @property
    def text(self) -> str:
        return f"{self.prefix}::{self.name}"


def population(root: Path = DOCS_ROOT) -> list[Path]:
    """Every governed doc this check covers -- every ``*.md`` under ``root``
    except ``work-logs/`` (point-in-time records, amended never trimmed).
    """
    return [
        path
        for path in sorted(root.rglob("*.md"))
        if _EXCLUDED_DIR_NAME not in path.relative_to(root).parts
    ]


def citations_in(path: Path, doc: str) -> list[Citation]:
    """Every citation ``path`` carries, with its 1-based line number.

    ``doc`` is the caller-supplied label (a path relative to whatever root
    ``population`` was called with) -- never re-derived from ``path`` itself,
    so a scratch tree under ``tmp_path`` labels its citations without needing
    to sit inside :data:`REPO_ROOT`.
    """
    text = path.read_text(encoding="utf-8")
    line_starts = [0]
    for line in text.splitlines(keepends=True):
        line_starts.append(line_starts[-1] + len(line))
    found = []
    for match in _CITE.finditer(text):
        line_number = bisect.bisect_right(line_starts, match.start())
        found.append(Citation(doc, line_number, match.group(1), match.group(2)))
    return found


def _stem(path: str) -> str:
    if path.endswith(".py"):
        path = path[:-3]
    return path.rsplit("/", 1)[-1]


def _has_real_path(prefix: str) -> bool:
    return bool(_NO_PATH_CHAR.search(prefix))


def resolves(citation: Citation, collected: list[tuple[str, str]]) -> bool:
    """Whether ``citation`` names a real, collected test.

    A path-qualified citation resolves by an exact ``(path, name)`` match or by
    basename -- the ``…/x.py`` and bare-filename-without-directory house styles
    already in this corpus, the same tolerance ``controls_discharge.py``
    documents for exactly this ambiguity. A bare citation resolves only when the
    name matches **exactly one** collected test; zero or several is unresolvable
    as a node id either way.
    """
    if citation.prefix and _has_real_path(citation.prefix):
        if (citation.prefix, citation.name) in collected:
            return True
        stem = _stem(citation.prefix)
        return any(name == citation.name and _stem(path) == stem for path, name in collected)
    matches = {path for path, name in collected if name == citation.name}
    return len(matches) == 1


def _collect_only() -> subprocess.CompletedProcess[str]:
    """One ``pytest --collect-only`` pass over every root a citation could
    name -- the mechanism ``test_adr36_ratchet.py``'s own ``_collect_only``
    uses, widened to the whole suite: this population's citations span every
    subsystem, not one ADR's own test roots.
    """
    return subprocess.run(  # noqa: S603 - argv is module-owned, never user input
        [
            sys.executable,
            "-m",
            "pytest",
            "--collect-only",
            "-q",
            str(REPO_ROOT / "packages" / "theurian-core" / "tests"),
            str(REPO_ROOT / "tests"),
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


def _collected_pairs(stdout: str) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    for line in stdout.splitlines():
        if "::" not in line:
            continue
        path, _, rest = line.partition("::")
        name = rest.rsplit("::", 1)[-1].split("[", 1)[0]
        pairs.append((path, name))
    return pairs


@pytest.fixture(scope="module")
def collected_pairs() -> list[tuple[str, str]]:
    result = _collect_only()
    assert result.returncode in {0, 5}, (
        f"pytest --collect-only exited {result.returncode}, neither a clean "
        f"collection (0) nor a legitimately empty one (5) -- a collection error "
        f"would make every citation below look unresolvable for the wrong "
        f"reason:\n{result.stderr}"
    )
    collected = _collected_pairs(result.stdout)
    assert collected, "the collection pass returned no test at all -- something is wrong upstream"
    return collected


#: (doc path relative to the repo root, cited test name) -- see the module
#: docstring for why each row will never collect.
KNOWN_EXCEPTIONS: frozenset[tuple[str, str]] = frozenset(
    {
        ("docs/adr/0013-ai-writes-produce-proposals.md", "test_the_tool_set_is_read_only"),
        (
            "docs/adr/0013-ai-writes-produce-proposals.md",
            "test_the_tool_set_is_exactly_the_published_nine",
        ),
        (
            "docs/adr/0032-the-write-intent-mcp-tool-surface.md",
            "test_capabilities_report_no_write_tools",
        ),
        (
            "docs/adr/0032-the-write-intent-mcp-tool-surface.md",
            "test_the_tool_set_is_exactly_the_published_nine",
        ),
        ("docs/adr/0032-the-write-intent-mcp-tool-surface.md", "test_the_tool_set_is_read_only"),
        (
            "docs/adr/0033-knowledge-candidate-generation.md",
            "test_nothing_in_the_shipped_package_constructs_a_knowledge_candidate",
        ),
        (
            "docs/adr/0033-knowledge-candidate-generation.md",
            "test_a_non_utf8_disk_path_never_verifies_a_utf8_anchor",
        ),
        (
            "docs/security/threat-model.md",
            "test_the_visibility_asks_about_every_row_not_only_the_first_fifty",
        ),
    }
)


def _all_citations(root: Path) -> list[Citation]:
    return [
        citation
        for path in population(root)
        for citation in citations_in(path, path.relative_to(root.parent).as_posix())
    ]


def violations(
    root: Path,
    collected: list[tuple[str, str]],
    known: frozenset[tuple[str, str]] = KNOWN_EXCEPTIONS,
) -> list[Citation]:
    return [
        citation
        for citation in _all_citations(root)
        if not resolves(citation, collected) and (citation.doc, citation.name) not in known
    ]


def test_every_governed_test_cite_collects(collected_pairs: list[tuple[str, str]]) -> None:
    """A renamed, moved or deleted test leaves a dangling citation, unnoticed --
    the class #811's predecessor and #818's two fixes each caught by hand.
    """
    found = violations(DOCS_ROOT, collected_pairs)
    assert found == [], "\n".join(f"{c.doc}:{c.line}  `{c.text}` does not collect" for c in found)


def test_the_known_exception_ledger_has_no_stale_rows(
    collected_pairs: list[tuple[str, str]],
) -> None:
    """A ledger row whose citation now resolves (the prose was corrected, or the
    test came back under that name) has paid its debt and must be deleted --
    the same "exact in both directions" discipline
    ``controls_discharge.py.PROSE_ONLY`` holds itself to.
    """
    by_key = {(citation.doc, citation.name): citation for citation in _all_citations(DOCS_ROOT)}
    stale = [
        row
        for row in KNOWN_EXCEPTIONS
        if row not in by_key or resolves(by_key[row], collected_pairs)
    ]
    assert stale == [], f"KNOWN_EXCEPTIONS rows no longer needed: {stale}"


# -- positive controls: the matching logic itself, over fabricated input ------
# No dependency on real doc content or a nested pytest invocation -- a
# synthetic `collected` list drives `resolves` directly.

_FAKE_COLLECTED = [
    ("tests/unit/test_thing.py", "test_a_thing_holds"),
    ("tests/integration/test_other.py", "test_a_thing_holds"),
    ("packages/theurian-core/tests/unit/test_widget.py", "test_the_widget_spins"),
]


@pytest.mark.parametrize(
    ("prefix", "name", "expected"),
    (
        ("tests/unit/test_widget.py", "test_the_widget_spins", True),  # any dir, real basename
        ("test_widget.py", "test_the_widget_spins", True),  # bare-filename house style
        ("tests/unit/test_thing.py", "test_a_thing_holds", True),  # exact match
        ("...", "test_a_thing_holds", False),  # elided path, but the name is ambiguous
        ("", "test_the_widget_spins", True),  # bare, resolves uniquely
        ("", "test_a_thing_holds", False),  # bare, ambiguous -- two files define it
        ("", "test_nobody_wrote_this", False),  # bare, dangling
        ("somefile.py", "test_nobody_wrote_this", False),  # full-form, dangling
    ),
)
def test_resolves_matches_the_stated_grammar(prefix: str, name: str, expected: bool) -> None:
    citation = Citation(doc="scratch.md", line=1, prefix=prefix, name=name)

    assert resolves(citation, _FAKE_COLLECTED) is expected


def test_a_planted_dangling_citation_is_reported_by_doc_line_and_text(tmp_path: Path) -> None:
    """The failure message names the doc, the line and the cite -- the shape a
    reader needs to fix the drift, not just know it exists.
    """
    scratch_docs = tmp_path / "docs"
    scratch_docs.mkdir()
    doc = scratch_docs / "example.md"
    doc.write_text("line one\nline two\n`somefile.py::test_that_nobody_wrote` cites nothing.\n")

    found = violations(scratch_docs, _FAKE_COLLECTED)

    assert len(found) == 1
    citation = found[0]
    assert citation.line == 3
    assert citation.text == "somefile.py::test_that_nobody_wrote"


def test_818s_stale_by_key_spelling_is_reported_as_a_violation(tmp_path: Path) -> None:
    """Reproduces #818's exact drift shape in a scratch copy: a citation
    naming the pre-rename function is a dangling cite, unresolvable against a
    collected suite that only holds the post-rename name.
    """
    scratch_docs = tmp_path / "docs"
    scratch_docs.mkdir()
    doc = scratch_docs / "adr-0037-scratch.md"
    doc.write_text(
        "`tests/integration/test_okf_cli.py::"
        "test_okf_import_reports_a_refusal_by_key_and_literal_never_a_resolved_path`\n"
    )
    collected_after_rename = [
        (
            "packages/theurian-core/tests/integration/test_okf_cli.py",
            "test_okf_import_reports_a_refusal_by_kind_key_and_literal_never_a_resolved_path",
        )
    ]

    found = violations(scratch_docs, collected_after_rename)

    assert len(found) == 1
    assert (
        found[0].name
        == "test_okf_import_reports_a_refusal_by_key_and_literal_never_a_resolved_path"
    )


def test_an_unperturbed_scratch_doc_with_a_real_citation_is_not_a_violation(tmp_path: Path) -> None:
    scratch_docs = tmp_path / "docs"
    scratch_docs.mkdir()
    doc = scratch_docs / "clean.md"
    doc.write_text("`tests/unit/test_thing.py::test_a_thing_holds` pins it.\n")

    assert violations(scratch_docs, _FAKE_COLLECTED) == []
