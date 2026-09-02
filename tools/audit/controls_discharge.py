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
from claim_surfaces import load_json, repo_root

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

#: The markers that say a control is not (fully) in force, so somebody owes it.
_NOT_SHIPPED: Final = re.compile(
    r"\bdo(?:es)?\s+not\s+ship\b|\bnot\s+built\b|\bis\s+owed\b|\bare\s+owed\b|\bowed\s+(?:by|with|to)\b"
    r"|\bnone\s+that\s+remove\b|\bunbuilt\b|\bnot\s+in\s+force\b|\bfuture\s+controls\b"
    r"|\bdoes\s+not\s+exist\b|\bno\s+such\s+control\b|\bstill\s+owe",
    re.IGNORECASE,
)

#: Any tracker reference, bracketed or as a URL. Both spellings of one cite on
#: one line are one number, which is what the set comprehension collapses.
_CITE: Final = re.compile(r"\[#(?P<bracketed>\d+)\]|issues/(?P<url>\d+)|pull/(?P<pull>\d+)")

_MAX_EXCERPT: Final = 130


@dataclass(frozen=True, slots=True)
class Member:
    """One published control claim, and where a reader finds it."""

    population: str
    where: str
    label: str
    text: str


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
                where=f"{THREAT_MODEL}:{number}",
                label=line[:80],
                text=" ".join(lines[number - 1 : end]),
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
                    where=f"{PROJECT_CONFIG_SCHEMA}::{'.'.join(dotted) or '(root)'}",
                    label=".".join(dotted) or "(root)",
                    text=description,
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

    @property
    def discharged(self) -> bool:
        return (
            self.names_symbol
            or self.names_test
            or (self.not_shipped and bool(self.open_cites))
            or any(entry[0] == self.member.where for entry in PROSE_ONLY)
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
PROSE_ONLY: Final[tuple[tuple[str, str], ...]] = (
    (
        f"{THREAT_MODEL}:100",
        "T-1's transport controls are properties of the daemon's request handling, not of "
        "one symbol: a token length, a comparison, and two file modes.",
    ),
    (
        f"{THREAT_MODEL}:113",
        "T-2 names the SDK setting it passes rather than a symbol of this package.",
    ),
    (
        f"{THREAT_MODEL}:165",
        "T-4's control is the *absence* of a literal in generated configuration, asserted "
        "by a test this block describes without naming.",
    ),
    (
        f"{THREAT_MODEL}:204",
        "A list opener: the surfaces are enumerated in the rows beneath it, and the block "
        "extent rule stops at the blank line above them.",
    ),
    (
        f"{THREAT_MODEL}:393",
        "Three independent mechanisms (a file lock, a health probe, a handshake), named "
        "by what they are rather than by where they live.",
    ),
    (
        f"{THREAT_MODEL}:405",
        "T-5's controls are two standard-library calls and a cap, described by behaviour.",
    ),
    (
        f"{THREAT_MODEL}:416",
        "The same, for the resolution-order half of T-5.",
    ),
    (
        f"{THREAT_MODEL}:486",
        "A list opener whose own sentence promises the symbols in the rows beneath it.",
    ),
    (
        f"{THREAT_MODEL}:1690",
        "The control is a workflow file, linked rather than spelled as a `src/` symbol.",
    ),
    (
        f"{THREAT_MODEL}:5338",
        "The control is an absence -- no MCP tool reaches a write path -- and its pin is "
        "described as an enumeration test without a file name.",
    ),
    (
        f"{THREAT_MODEL}:5365",
        "A list opener: `append_revision`, `InvariantViolationError` and the pointer guard "
        "are named in the bullets beneath it, past the blank line the extent rule stops at.",
    ),
    (
        f"{THREAT_MODEL}:5791",
        "Setup's merge-never-replace controls are described by behaviour, with the test "
        "named as 'a test' rather than by path.",
    ),
    (
        f"{THREAT_MODEL}:5795",
        "The `~/.theurian/env` half of the same entry.",
    ),
    (
        f"{PROJECT_CONFIG_SCHEMA}::(root)",
        "The root description states what the file is and who reads it; the reader it "
        "names is a module path in prose, not in code markup. #455 is what rewrites this "
        "sentence, and this row is expected to be discharged with it.",
    ),
    (
        f"{PROJECT_CONFIG_SCHEMA}::providers",
        "A section header: 'every provider defaults to a deterministic in-tree "
        "implementation', which is a statement about the defaults below it.",
    ),
    (
        f"{PROJECT_CONFIG_SCHEMA}::providers.embedding.endpointEnv",
        "Names an environment variable convention, not a control.",
    ),
    (
        f"{PROJECT_CONFIG_SCHEMA}::providers.embedding.apiKeyEnv",
        "The same, for the key half.",
    ),
    (
        f"{PROJECT_CONFIG_SCHEMA}::retrieval.rrfK",
        "A tuning constant with no control claim to discharge.",
    ),
    (
        f"{PROJECT_CONFIG_SCHEMA}::retrieval.includeStatuses",
        "States the default's effect; the enforcing symbol is named in the threat model "
        "and in `test_config_key_call_sites.py`, not here.",
    ),
    (
        f"{PROJECT_CONFIG_SCHEMA}::raptor.enabled",
        "Names the CLI surface (`theurian index build`) and an ADR, which is the switch "
        "this key is *not* -- ADR-0008 decision 10.",
    ),
    (
        f"{PROJECT_CONFIG_SCHEMA}::raptor.minChildrenPerSummary",
        "Explains a threshold's meaning; `application/forest_builder.py` carries the "
        "constant, and ADR-0008 is where the claim is pinned.",
    ),
    (
        f"{PROJECT_CONFIG_SCHEMA}::traceabilityPolicy",
        "Points at a specification section rather than at a symbol.",
    ),
    (
        f"{PROJECT_CONFIG_SCHEMA}::security.secretScan",
        "Names the CLI gate (`theurian propose accept`) rather than the reader. The reader "
        "is `security/project_config.py::read_secret_scan_policy` and the pin is "
        "`test_config_key_call_sites.py`'s `WATCHED_KEY_DESCRIPTIONS` row; the description "
        "deliberately spells neither, because it is published to users.",
    ),
)


def verdict_for(member: Member, table: dict[str, str]) -> Verdict:
    cites = _cited(member.text)
    return Verdict(
        member=member,
        names_symbol=bool(_SRC_SYMBOL.search(member.text)),
        names_test=bool(_TEST.search(member.text)),
        not_shipped=bool(_NOT_SHIPPED.search(member.text)),
        cites=tuple(cites),
        open_cites=tuple(n for n in cites if tracker_state.is_open(table, n)),
        unknown_cites=tuple(n for n in cites if n not in table),
    )


def audit(root: Path, *, offline: bool = False) -> tuple[list[Verdict], str]:
    table, provenance = tracker_state.states(offline=offline)
    return [verdict_for(member, table) for member in members(root)], provenance


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


def _run_positive_controls(*, offline: bool) -> int:
    table, provenance = tracker_state.states(offline=offline)
    failures = 0
    print(f"=== POSITIVE CONTROLS (tracker states: {provenance}) ===")
    for label, text, discharged, dead in POSITIVE_CONTROLS:
        verdict = verdict_for(
            Member(population="control", where="control", label=label, text=text), table
        )
        ok = verdict.discharged is discharged and verdict.owner_is_dead is dead
        status = "OK  " if ok else "FAIL"
        failures += status == "FAIL"
        print(
            f"  {status} {label}: discharged={verdict.discharged} (expected {discharged}), "
            f"dead owner={verdict.owner_is_dead} (expected {dead})"
        )
    return 1 if failures else 0


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

    undischarged = [v for v in verdicts if not v.discharged]
    dead = [v for v in verdicts if v.owner_is_dead]
    unknown = [v for v in verdicts if v.unknown_cites]
    produced = {v.member.where for v in verdicts if not (v.names_symbol or v.names_test)}
    stale = [entry for entry in PROSE_ONLY if entry[0] not in produced]

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
    if stale:
        print("\nSTALE LEDGER ROWS -- these now name a symbol or a test; delete the row:")
        for where, _ in stale:
            print(f"  {where}")

    return 1 if undischarged or dead or unknown or stale else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
