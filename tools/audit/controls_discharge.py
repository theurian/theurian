"""Class 2: does every published control claim discharge? (#199 unit B, AC-2).

Two populations, one question. The threat model's ``**Controls`` blocks and the
project-config schema's published descriptions both assert that something is in
force, or that something is owed; this audit asks, of each one, **what would make
the assertion checkable**.

A member discharges when it does one of:

1. **names the ``src/`` symbol that implements it** -- the form
   ``docs/security/threat-model.md:486`` sets out explicitly ("each named by the
   symbol in ``src/`` that implements it"), and the one a reader can follow;
2. **names the test that pins it**;
3. **says the control is not shipped, and cites an owner that is still open**;
4. **carries a row in** :data:`PROSE_ONLY` -- the recorded residue, each with the
   reason its discharge is prose rather than a name. This is a debt ledger, not
   an allowlist: it is exact in both directions, so a member that gains a symbol
   has to be struck off in the same change.

**Rule 3 is also a rule on its own, and that is what the mutation control
attacks.** A block that says a control is owed and cites a *closed* issue as its
owner is a residual with no owner, whatever else it names -- so the open-owner
check runs over every member that carries a not-shipped marker, not only over
those that would otherwise fail. Swapping one such cite for a closed number turns
this audit RED, which is AC-2's seeded violation.

The tracker states come from :mod:`tracker_state`, live by default; see its
docstring for why a stale snapshot is the false-green direction.

Run it::

    uv run --frozen python tools/audit/controls_discharge.py
    uv run --frozen python tools/audit/controls_discharge.py --offline
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Final

import tracker_state
from claim_surfaces import load_json, print_control_tally, repo_root

THREAT_MODEL: Final = "docs/security/threat-model.md"
PROJECT_CONFIG_SCHEMA: Final = "schemas/config/project-config.schema.json"

#: A ``**Controls`` block opener. ``Controls`` and not ``Control``: the singular
#: label opens a *different* population (``threat_model_census.py`` measures it),
#: and folding the two together here would change the count this audit's own
#: figures are anchored to without saying so.
_CONTROLS_OPENER: Final = re.compile(r"^\*\*Controls")

#: A symbol in the shipped package: a module path, a qualified name, the one
#: screaming constant this repository's prose names, or a call. The same shape
#: ``threat_model_census.py`` keys on, widened only by the call form, because
#: several blocks name ``read_secret_scan_policy()`` rather than its module.
#:
#: **A general screaming-constant alternative was tried and removed**, because it
#: is a false *discharge*: ``${THEURIAN_MCP_TOKEN}`` is an environment variable
#: and ``INV-2`` is an invariant label, and neither is a symbol a reader can open.
#: Three blocks read as discharged under it that nothing in ``src/`` backs.
_SRC_SYMBOL: Final = re.compile(
    r"`[^`]*(?:[A-Za-z_][\w/]*\.py|::[A-Za-z_]\w*|MAX_[A-Z_]+|[a-z_]{3,}\(\))[^`]*`"
)

#: A pinning test, however it is spelled.
_TEST: Final = re.compile(r"tests?/[\w/]+\.py|\btest_\w+\.py|::test_\w+", re.IGNORECASE)


def _repository_tests(root: Path) -> list[Path]:
    """This repository's own test files -- not its dependencies'.

    ``rglob`` descends into ``.venv``, where hundreds of installed packages ship
    their own ``test_*.py``. A citation naming one of those resolved, so the
    check could be satisfied by a name this repository does not define -- which
    is the same "shape, not existence" hole one level down.
    """
    return [
        path
        for path in root.rglob("test_*.py")
        if not any(part.startswith(".") or part == "node_modules" for part in path.parts)
    ]


def _test_names(root: Path) -> tuple[frozenset[str], frozenset[str]]:
    """Every test file name and every ``def test_*`` in the repository.

    Read off the tree rather than from a list, for the same reason every other
    population here is: a list is a claim about the repository that nothing
    recomputes.
    """
    files: set[str] = set()
    functions: set[str] = set()
    for path in _repository_tests(root):
        files.add(path.name)
        for match in re.finditer(
            r"^def (test_\w+)", path.read_text(encoding="utf-8", errors="replace"), re.MULTILINE
        ):
            functions.add(match.group(1))
    return frozenset(files), frozenset(functions)


def _unresolvable(text: str, files: frozenset[str], functions: frozenset[str]) -> tuple[str, ...]:
    """The test citations in ``text`` that name nothing in this repository.

    ``_TEST`` matches a citation's *shape*, never its existence, so a block
    citing ``::test_this_was_renamed_last_year`` counted as discharged exactly
    as one citing a test that runs. Measured on the tree at ``dcd11dcd``:
    replacing all three of T-5's cited names with nonsense left the audit at
    exit 0. A citation naming something that is not there is evidence of
    nothing, and this is what tells the two apart.

    Matching is on the **basename** for a path and on the function name for a
    ``::`` citation, deliberately: the documents' house spelling is
    ``tests/unit/test_x.py``, which resolves against no working directory, and
    tightening that to a full repository path is a separate edit to the prose
    rather than a thing this check should force. What it catches is the case
    that matters -- a name nothing in the tree defines.
    """
    missing: list[str] = []
    for match in _TEST.finditer(text):
        citation = match.group(0)
        if citation.startswith("::"):
            resolved = citation[2:] in functions
        else:
            resolved = Path(citation).name in files
        if not resolved:
            missing.append(citation)
    return tuple(missing)


#: The markers that say a control is not (fully) in force, so somebody owes it.
#:
#: **The active voice was missing, which is round one's M-d.** Every "owed"
#: alternative here was passive -- ``is owed``, ``are owed``, ``owed by`` -- so a
#: block writing the same obligation the other way round ("#429 owes the three
#: fetch controls") carried no not-shipped marker and never reached the
#: dead-owner check, whatever it cited. ``\bowes?\b`` closes that, and
#: ``no <reader|loader|consumer> yet`` closes the sibling shape: an absence stated
#: as a missing component rather than as an unshipped control.
#:
#: Measured at ``ef345c9``, both directions: the widening marks **no** additional
#: member of the tree today -- not-shipped stays 5 of 34 and the dead-owner count
#: stays 0 -- so what it changes is the *floor*, which is why the two shapes are
#: driven by controls rather than by a count that did not move.
_NOT_SHIPPED: Final = re.compile(
    r"\bdo(?:es)?\s+not\s+ship\b|\bnot\s+built\b|\bis\s+owed\b|\bare\s+owed\b|\bowed\s+(?:by|with|to)\b"
    r"|\bowes?\b"
    r"|\bnone\s+that\s+remove\b|\bunbuilt\b|\bnot\s+in\s+force\b|\bfuture\s+controls\b"
    r"|\bdoes\s+not\s+exist\b|\bno\s+such\s+control\b|\bstill\s+owe"
    r"|\bno\s+(?:reader|loader|consumer)\s+yet\b",
    re.IGNORECASE,
)

#: Any tracker reference, bracketed or as a URL. Both spellings of one cite on
#: one line are one number, which is what the set comprehension collapses.
_CITE: Final = re.compile(r"\[#(?P<bracketed>\d+)\]|issues/(?P<url>\d+)|pull/(?P<pull>\d+)")

_MAX_EXCERPT: Final = 130


@dataclass(frozen=True, slots=True)
class Member:
    """One published control claim, and where a reader finds it.

    ``where`` is for a person to open -- a path and a line, or a path and a
    dotted key. ``path`` and ``label`` are the *ledger* key, and they are separate
    from ``where`` for the reason round one's M-b records: a line number is
    invalidated by any edit above it, so a ledger keyed on ``where`` goes RED on a
    typo fix three thousand lines up. The sibling ledger in
    ``config_object_claims.py`` had already stated that rule about itself; this
    one was line-keyed against it.

    ``exact_key`` says which of the two label shapes this is. A schema
    description's label is the whole dotted key, so the ledger matches it by
    equality: ``providers`` is a *prefix* of ``providers.embedding.apiKeyEnv``,
    and a substring rule would let one row cover four members. A ``**Controls``
    opener is a truncated line of prose, where a fragment is the only stable
    handle.
    """

    population: str
    path: str
    where: str
    label: str
    text: str
    exact_key: bool

    def covered_by(self, fragment: str) -> bool:
        """Whether a ledger row's second field names this member."""
        return fragment == self.label if self.exact_key else fragment in self.label


def _controls_blocks(root: Path) -> list[Member]:
    """Every ``**Controls`` block of the threat model, opener through blank line.

    The extent rule is the one ``threat_model_census.py`` uses: a bold-labelled
    paragraph runs to the next blank line. It matters that the whole block is
    read rather than the opener line -- the symbol that discharges a claim is
    routinely three wrapped lines below the label.
    """
    lines = (root / THREAT_MODEL).read_text(encoding="utf-8").splitlines()
    found: list[Member] = []
    for number, line in enumerate(lines, start=1):
        if not _CONTROLS_OPENER.match(line):
            continue
        end = number
        while end < len(lines) and lines[end].strip():
            end += 1
        found.append(
            Member(
                population="threat-model `**Controls` block",
                path=THREAT_MODEL,
                where=f"{THREAT_MODEL}:{number}",
                label=line[:80],
                text=" ".join(lines[number - 1 : end]),
                exact_key=False,
            )
        )
    return found


def _schema_descriptions(root: Path) -> list[Member]:
    """Every published description in the project-config schema, root included.

    Derived by parsing, so the eleven key blocks and the root arrive from the
    same walk. The root is the member every key-block count left out, and it is
    where #455's false claim lives.
    """
    schema = load_json(root, PROJECT_CONFIG_SCHEMA)
    found: list[Member] = []

    def walk(node: object, dotted: tuple[str, ...]) -> None:
        if not isinstance(node, dict):
            return
        description = node.get("description")
        if isinstance(description, str):
            found.append(
                Member(
                    population="project-config schema description",
                    path=PROJECT_CONFIG_SCHEMA,
                    where=f"{PROJECT_CONFIG_SCHEMA}::{'.'.join(dotted) or '(root)'}",
                    label=".".join(dotted) or "(root)",
                    text=description,
                    exact_key=True,
                )
            )
        for key, value in node.items():
            if key == "properties" and isinstance(value, dict):
                for child, subschema in value.items():
                    walk(subschema, (*dotted, child))

    walk(schema, ())
    return found


def members(root: Path) -> list[Member]:
    return [*_controls_blocks(root), *_schema_descriptions(root)]


def _cited(text: str) -> list[str]:
    numbers: list[str] = []
    for match in _CITE.finditer(text):
        number = match.group("bracketed") or match.group("url") or match.group("pull")
        if number not in numbers:
            numbers.append(number)
    return numbers


@dataclass(frozen=True, slots=True)
class Verdict:
    member: Member
    names_symbol: bool
    names_test: bool
    not_shipped: bool
    cites: tuple[str, ...]
    open_cites: tuple[str, ...]
    unknown_cites: tuple[str, ...]
    #: Test citations in this block that name nothing in the repository.
    unresolvable_cites: tuple[str, ...] = ()

    @property
    def discharged(self) -> bool:
        return (
            self.names_symbol
            or self.names_test
            or (self.not_shipped and bool(self.open_cites))
            or any(
                entry[0] == self.member.path and self.member.covered_by(entry[1])
                for entry in PROSE_ONLY
            )
        )

    @property
    def owner_is_dead(self) -> bool:
        """A residual whose every cited owner is closed, or does not exist.

        The check rule 3 turns into an obligation. It runs over every member
        carrying a not-shipped marker and at least one cite, whatever else the
        member names, because a control that says "owed, see #N" with #N closed
        has nobody owing it -- and that is true regardless of whether some other
        sentence in the same block happens to name a symbol.
        """
        return self.not_shipped and bool(self.cites) and not self.open_cites


#: The members whose discharge is prose rather than a named symbol or test, with
#: the reason. Measured at ``141cf6f``; the count is what a reader re-derives, not
#: a number to trust.
#:
#: **A debt ledger, exact in both directions.** A member that gains a symbol or a
#: test stops being produced here and the row goes RED with an instruction to
#: delete it -- the discipline ``test_raptor_config_claims.py``'s
#: ``UNNARROWED_UNIVERSALS`` runs on, and for the same reason: a ledger nobody has
#: to empty is a ledger that grows.
PROSE_ONLY: Final[tuple[tuple[str, str, str], ...]] = (
    (
        THREAT_MODEL,
        "bearer token",
        "T-1's transport controls are properties of the daemon's request handling, not of "
        "one symbol: a token length, a comparison, and two file modes.",
    ),
    (
        THREAT_MODEL,
        "bind loopback only",
        "T-2 names the SDK setting it passes rather than a symbol of this package.",
    ),
    (
        THREAT_MODEL,
        "never a literal",
        "T-4's control is the *absence* of a literal in generated configuration, asserted "
        "by a test this block describes without naming.",
    ),
    (
        THREAT_MODEL,
        "**Controls that exist**",
        "A list opener: the surfaces are enumerated in the rows beneath it, and the block "
        "extent rule stops at the blank line above them.",
    ),
    (
        THREAT_MODEL,
        "an OS advisory file lock",
        "Three independent mechanisms (a file lock, a health probe, a handshake), named "
        "by what they are rather than by where they live.",
    ),
    (
        THREAT_MODEL,
        "every path resolved with `realpath`",
        "T-5's controls are two standard-library calls and a cap, described by behaviour.",
    ),
    (
        THREAT_MODEL,
        "**Controls at ingestion**",
        "A list opener whose own sentence promises the symbols in the rows beneath it.",
    ),
    (
        THREAT_MODEL,
        "release-core.yml",
        "The control is a workflow file, linked rather than spelled as a `src/` symbol.",
    ),
    (
        THREAT_MODEL,
        "no MCP tool reaches a write path",
        "The control is an absence -- no MCP tool reaches a write path -- and its pin is "
        "described as an enumeration test without a file name.",
    ),
    (
        THREAT_MODEL,
        "write-time enforcement of INV-2",
        "A list opener: `append_revision`, `InvariantViolationError` and the pointer guard "
        "are named in the bullets beneath it, past the blank line the extent rule stops at.",
    ),
    (
        THREAT_MODEL,
        "**Controls, the MCP configuration:**",
        "Setup's merge-never-replace controls are described by behaviour, with the test "
        "named as 'a test' rather than by path.",
    ),
    (
        THREAT_MODEL,
        "**Controls, `~/.theurian/env`:**",
        "The `~/.theurian/env` half of the same entry.",
    ),
    (
        PROJECT_CONFIG_SCHEMA,
        "providers",
        "A section header: 'every provider defaults to a deterministic in-tree "
        "implementation', which is a statement about the defaults below it.",
    ),
    (
        PROJECT_CONFIG_SCHEMA,
        "providers.embedding.endpointEnv",
        "Names an environment variable convention, not a control.",
    ),
    (
        PROJECT_CONFIG_SCHEMA,
        "providers.embedding.apiKeyEnv",
        "The same, for the key half.",
    ),
    (
        PROJECT_CONFIG_SCHEMA,
        "retrieval.rrfK",
        "A tuning constant with no control claim to discharge.",
    ),
    (
        PROJECT_CONFIG_SCHEMA,
        "retrieval.includeStatuses",
        "States the default's effect; the enforcing symbol is named in the threat model "
        "and in `test_config_key_call_sites.py`, not here.",
    ),
    (
        PROJECT_CONFIG_SCHEMA,
        "raptor.enabled",
        "Names the CLI surface (`theurian index build`) and an ADR, which is the switch "
        "this key is *not* -- ADR-0008 decision 10.",
    ),
    (
        PROJECT_CONFIG_SCHEMA,
        "raptor.minChildrenPerSummary",
        "Explains a threshold's meaning; `application/forest_builder.py` carries the "
        "constant, and ADR-0008 is where the claim is pinned.",
    ),
    (
        PROJECT_CONFIG_SCHEMA,
        "traceabilityPolicy",
        "Points at a specification section rather than at a symbol.",
    ),
    (
        PROJECT_CONFIG_SCHEMA,
        "security.secretScan",
        "Names the CLI gate (`theurian propose accept`) rather than the reader. The reader "
        "is `security/project_config.py::read_secret_scan_policy` and the pin is "
        "`test_config_key_call_sites.py`'s `WATCHED_KEY_DESCRIPTIONS` row; the description "
        "deliberately spells neither, because it is published to users.",
    ),
)


def verdict_for(
    member: Member,
    table: dict[str, str],
    *,
    test_files: frozenset[str] = frozenset(),
    test_functions: frozenset[str] = frozenset(),
) -> Verdict:
    cites = _cited(member.text)
    citations = tuple(match.group(0) for match in _TEST.finditer(member.text))
    unresolvable = _unresolvable(member.text, test_files, test_functions)
    return Verdict(
        member=member,
        names_symbol=bool(_SRC_SYMBOL.search(member.text)),
        # A citation nothing in the tree defines does not discharge anything: it
        # is the shape of evidence without the evidence (round 1, M-1). A block
        # whose *only* citations are unresolvable falls back to whatever else it
        # names, which is usually a symbol, so this tightens the floor without
        # reddening a block that was already carrying real evidence.
        names_test=bool(citations) and len(unresolvable) < len(citations),
        unresolvable_cites=unresolvable,
        not_shipped=bool(_NOT_SHIPPED.search(member.text)),
        cites=tuple(cites),
        open_cites=tuple(n for n in cites if tracker_state.is_open(table, n)),
        unknown_cites=tuple(n for n in cites if n not in table),
    )


def audit(root: Path, *, offline: bool = False) -> tuple[list[Verdict], str]:
    table, provenance = tracker_state.states(offline=offline)
    files, functions = _test_names(root)
    return [
        verdict_for(member, table, test_files=files, test_functions=functions)
        for member in members(root)
    ], provenance


#: Planted blocks run instead of the tree under ``--positive-control``, as
#: ``(what it demonstrates, block, discharged, dead owner)``.
#:
#: The fourth row is AC-2's seeded violation in miniature: a block that says its
#: control is owed and cites a closed issue has nobody owing it. The fifth is the
#: rule that makes the fourth mean something -- the dead-owner check runs whatever
#: else the block names, so a block cannot buy its way out by naming a symbol in
#: another sentence.
POSITIVE_CONTROLS: Final[tuple[tuple[str, str, bool, bool], ...]] = (
    (
        "a block naming the symbol that implements it",
        "**Controls:** `security/project_config.py::read_secret_scan_policy` reads it.",
        True,
        False,
    ),
    (
        "a block naming the test that pins it",
        "**Controls:** pinned by `tests/unit/test_config_key_call_sites.py`.",
        True,
        False,
    ),
    (
        "an owed control whose owner is open",
        "**Controls:** none yet; the three fetch controls are owed "
        "([#429](https://github.com/theurian/theurian/issues/429)).",
        True,
        False,
    ),
    (
        "an owed control whose owner is closed -- AC-2's seeded violation",
        "**Controls:** none yet; the three fetch controls are owed "
        "([#129](https://github.com/theurian/theurian/issues/129)).",
        False,
        True,
    ),
    (
        "a closed owner beside a named symbol: still a dead owner",
        "**Controls:** `security/project_config.py` reads it, and the rest is owed "
        "([#129](https://github.com/theurian/theurian/issues/129)).",
        True,
        True,
    ),
    (
        "prose with no symbol, no test and no cite",
        "**Controls:** bind loopback only; validate `Origin` against an allowlist.",
        False,
        False,
    ),
)


def ledger_drift(
    verdicts: list[Verdict], ledger: tuple[tuple[str, str, str], ...]
) -> tuple[list[tuple[str, str, str]], list[tuple[tuple[str, str, str], list[str]]]]:
    """``(stale rows, ambiguous rows)`` -- two of this audit's three directions.

    *Stale* is a row whose member now names a symbol or a test, so the debt it
    records has been paid and the row has to go. *Ambiguous* is the direction the
    fragment key opened and the line key did not have: one row covering two
    members reads as coverage of both while a person judged one.

    **The third is not missing, it is computed elsewhere.** *Undischarged* -- a
    member that names no ``src/`` symbol, no test, no open owner and no
    :data:`PROSE_ONLY` row -- is derived from the verdicts in :func:`_report`, and
    it is the same direction the sibling ledgers spell *unrecorded*. Reading this
    function's arity as the audit's direction count is what put "in two" in the
    README beside this file; the count is three, and the last
    :data:`POSITIVE_CONTROLS` row is what drives the third.

    The ledger is a parameter so both of these can be **driven** from planted input
    -- :data:`LEDGER_CONTROLS`, and round one's code-M6 across the five audits here.
    """
    owing = [v.member for v in verdicts if not (v.names_symbol or v.names_test)]
    every = [v.member for v in verdicts]
    stale = [
        entry
        for entry in ledger
        if not any(entry[0] == m.path and m.covered_by(entry[1]) for m in owing)
    ]
    ambiguous = [
        (entry, covered)
        for entry in ledger
        if len(covered := [m.where for m in every if entry[0] == m.path and m.covered_by(entry[1])])
        > 1
    ]
    return stale, ambiguous


#: What the ledger reconciliation must do, driven from planted members, as
#: ``(what it demonstrates, the members as (path, label, text, exact key), the
#: ledger, stale, ambiguous)``.
#:
#: Round one's code-M6 on this audit. The last row is the trap the fragment key
#: introduced and the line key could not have: ``providers`` is a prefix of
#: ``providers.embedding.apiKeyEnv``, so a substring rule applied to the schema
#: population lets one recorded row discharge four members nobody judged.
LEDGER_CONTROLS: Final[
    tuple[
        tuple[
            str,
            tuple[tuple[str, str, str, bool], ...],
            tuple[tuple[str, str, str], ...],
            int,
            int,
        ],
        ...,
    ]
] = (
    (
        "a prose-discharged member its ledger row covers: no drift",
        ((THREAT_MODEL, "**Controls:** bind loopback only; validate", "prose", False),),
        ((THREAT_MODEL, "bind loopback only", "why"),),
        0,
        0,
    ),
    (
        "a ledger row whose member now names a symbol -- the stale direction",
        (
            (
                THREAT_MODEL,
                "**Controls:** bind loopback only; validate",
                "`daemon/app.py` binds it",
                False,
            ),
        ),
        ((THREAT_MODEL, "bind loopback only", "why"),),
        1,
        0,
    ),
    (
        "a ledger row whose member is gone entirely -- also stale",
        (),
        ((THREAT_MODEL, "bind loopback only", "why"),),
        1,
        0,
    ),
    (
        "a fragment that is a prefix of three other schema keys, which exact matching "
        "must keep from covering them",
        (
            (PROJECT_CONFIG_SCHEMA, "providers", "a section header", True),
            (PROJECT_CONFIG_SCHEMA, "providers.embedding.apiKeyEnv", "an env var", True),
            (PROJECT_CONFIG_SCHEMA, "providers.embedding.endpointEnv", "an env var", True),
        ),
        ((PROJECT_CONFIG_SCHEMA, "providers", "why"),),
        0,
        0,
    ),
    (
        "the same fragment matched as a substring, which is what ambiguity looks like",
        (
            (THREAT_MODEL, "**Controls:** the token is required", "prose", False),
            (
                THREAT_MODEL,
                "**Controls:** the token is refused when world-readable",
                "prose",
                False,
            ),
        ),
        ((THREAT_MODEL, "the token is", "why"),),
        0,
        1,
    ),
)


def _run_ledger_controls(table: dict[str, str]) -> int:
    """Drive the two ledger-keyed directions from planted members and planted ledgers.

    The third, *undischarged*, needs no ledger and is driven by the last
    :data:`POSITIVE_CONTROLS` row -- prose with no symbol, no test and no cite.
    """
    failures = 0
    ran = 0
    print("\n=== LEDGER CONTROLS (the reconciliation, driven) ===")
    for label, planted, ledger, want_stale, want_ambiguous in LEDGER_CONTROLS:
        ran += 1
        verdicts = [
            verdict_for(
                Member(
                    population="control",
                    path=path,
                    where=f"{path}::{member_label}",
                    label=member_label,
                    text=text,
                    exact_key=exact,
                ),
                table,
            )
            for path, member_label, text, exact in planted
        ]
        stale, ambiguous = ledger_drift(verdicts, ledger)
        got = (len(stale), len(ambiguous))
        want = (want_stale, want_ambiguous)
        status = "OK  " if got == want else "FAIL"
        failures += status == "FAIL"
        print(f"  {status} {label}: (stale, ambiguous)={got}, expected {want}")
    print_control_tally("LEDGER_CONTROLS", ran, failures)
    return 1 if failures else 0


def _run_positive_controls(*, offline: bool) -> int:
    table, provenance = tracker_state.states(offline=offline)
    failures = 0
    ran = 0
    print(f"=== POSITIVE CONTROLS (tracker states: {provenance}) ===")
    for label, text, discharged, dead in POSITIVE_CONTROLS:
        ran += 1
        verdict = verdict_for(
            Member(
                population="control",
                path="control",
                where="control",
                label=label,
                text=text,
                exact_key=False,
            ),
            table,
        )
        ok = verdict.discharged is discharged and verdict.owner_is_dead is dead
        status = "OK  " if ok else "FAIL"
        failures += status == "FAIL"
        print(
            f"  {status} {label}: discharged={verdict.discharged} (expected {discharged}), "
            f"dead owner={verdict.owner_is_dead} (expected {dead})"
        )
    print_control_tally("POSITIVE_CONTROLS", ran, failures)
    return (1 if failures else 0) | _run_ledger_controls(table)


def main(argv: list[str]) -> int:
    if "--positive-control" in argv:
        return _run_positive_controls(offline="--offline" in argv)

    root = repo_root()
    verdicts, provenance = audit(root, offline="--offline" in argv)

    print(f"tracker states: {provenance}")
    print("\n=== POPULATIONS (derived at this checkout, never transcribed) ===")
    per_population: dict[str, int] = {}
    for verdict in verdicts:
        key = verdict.member.population
        per_population[key] = per_population.get(key, 0) + 1
    for population, count in sorted(per_population.items()):
        print(f"  {count:4}  {population}")

    print("\n=== DISCHARGE ===")
    for verdict in verdicts:
        how = (
            "symbol"
            if verdict.names_symbol
            else "test"
            if verdict.names_test
            else "open owner"
            if verdict.not_shipped and verdict.open_cites
            else "prose (recorded)"
            if verdict.discharged
            else "NONE"
        )
        print(f"  {how:<17} {verdict.member.where}  cites={list(verdict.cites)}")

    return _report(verdicts)


def _report(verdicts: list[Verdict]) -> int:
    """Print every way the tree and the ledger disagree, and grade the run."""
    undischarged = [v for v in verdicts if not v.discharged]
    dead = [v for v in verdicts if v.owner_is_dead]
    unknown = [v for v in verdicts if v.unknown_cites]
    unresolvable = [v for v in verdicts if v.unresolvable_cites]
    stale, ambiguous = ledger_drift(verdicts, PROSE_ONLY)

    if undischarged:
        print("\nUNDISCHARGED -- a published control claim nothing makes checkable:")
        for verdict in undischarged:
            print(f"  {verdict.member.where}  {verdict.member.text[:_MAX_EXCERPT]}")
    if dead:
        print("\nDEAD OWNER -- the block says the control is owed and every cite is closed:")
        for verdict in dead:
            print(f"  {verdict.member.where}  cites={list(verdict.cites)}")
            print(f"      {verdict.member.text[:_MAX_EXCERPT]}")
    if unknown:
        print("\nUNKNOWN CITE -- a number this repository's tracker does not carry:")
        for verdict in unknown:
            print(f"  {verdict.member.where}  {list(verdict.unknown_cites)}")
    if unresolvable:
        print("\nUNRESOLVABLE CITE -- a test citation naming nothing in this repository:")
        for verdict in unresolvable:
            print(f"  {verdict.member.where}  {list(verdict.unresolvable_cites)}")
    if stale:
        print("\nSTALE LEDGER ROWS -- these now name a symbol or a test; delete the row:")
        for path, fragment, _ in stale:
            print(f"  {path}  {fragment!r}")
    if ambiguous:
        print("\nAMBIGUOUS LEDGER ROWS -- one recorded judgement covering several members:")
        for (path, fragment, _), covered in ambiguous:
            print(f"  {path}  {fragment!r} covers {covered}")

    return 1 if undischarged or dead or unknown or unresolvable or stale or ambiguous else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
