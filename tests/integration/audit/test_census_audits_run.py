"""The suite runs the census audits, so the instruments cannot go silent (#199 unit B).

``tools/audit/`` holds five audits that each exit ``1`` on a violation, and until
this module existed **nothing ran them**. Round two measured what that cost: six
mutations reverting round-one fixes -- the delimiter run narrowed back to one
character, ``unreleased_lines`` gutted to ``return frozenset()``, and four more --
all survived the full test suite, and deleting a whole ``LEDGER_CONTROLS`` table
left the suite green. An instrument nobody runs reports a clean tree exactly the
way a clean tree does.

**Two claims, one per test, and they are not the same claim.**

* The real run says *this tree discharges*: no unrecorded suspect, no stale
  ledger row, no ambiguity, no dangling anchor.
* ``--positive-control`` says *the instrument can still fail*: every key is shown
  hitting a planted sentence, every ledger direction is driven from planted rows
  against a planted ledger, and each audit's recorded escape space is run. A zero
  from the first is only readable after the second has passed, which is this
  repository's own standing rule and the reason both are here.

**And a third claim, which round three is why.** A passing control run says
nothing about *how much* was run: five one-line edits took an audit's controls
down to a handful or to none while every check here stayed green -- a runner
opening ``return 0``, a loop rewritten to iterate ``()`` (twice), a table sliced
to its first row, and this guard's own required-table set emptied. So each audit now
counts the rows its loops **execute** and prints them, :data:`CONTROL_TALLIES`
pins those counts, the call graph is read to catch a runner nobody reaches, and
:func:`test_this_guards_own_control_keys_select_something` holds the guard to the
rule it holds the audits to.

**What none of it reaches**, so the bound is a sentence a reader can attack: a
control row that keeps its place in the count while it stops asserting anything
-- a row duplicated, or its expected value edited to match what the code now
does. Every check here is about whether a control *ran*; whether the row is worth
running is what review is for.

**The audit set is derived, not listed.** A module in ``tools/audit/`` is a
census audit when it offers ``--positive-control``; the population keys beside
them (``threat_model_*.py``) do not, because they report rather than gate. So a
sixth audit joins this guard on the commit that adds it, and a fifth that quietly
loses its control mode fails :func:`test_every_module_offering_a_control_is_run`
rather than disappearing from the parametrisation.

**``--offline`` is passed to every run**, and it is what makes this
deterministic: the two audits that classify by tracker state fall back to the
committed ``tracker-state.json`` instead of querying ``gh``. The three that do
not read the tracker ignore the flag. Nothing here reaches the network, and
nothing here writes: every audit reports to stdout and exits.

**Preconditions, stated because a failure here should be readable.**
``sha_anchors.py`` resolves commits against ``origin/main`` (or ``main``) and
needs the history behind them, so the CI job that runs this checks out with
``fetch-depth: 0`` -- the same reason ``test_dogfood_corpus_governance.py``
already needs. In a shallow clone the audit says so itself and this test relays
its output.

Marked ``integration`` rather than ``unit`` although it sits beside a unit
directory in spirit: it spawns five subprocesses and reads the whole tracked
tree, which is what this repository's ``integration`` marker is defined as.
"""

from __future__ import annotations

import ast
import pathlib
import re
import subprocess
import sys
from typing import Final

import pytest

pytestmark = pytest.mark.integration

#: ``parents[3]`` is ``tests/integration/audit`` -> ``integration`` -> ``tests``
#: -> the repository root.
REPO_ROOT: Final = pathlib.Path(__file__).resolve().parents[3]

AUDIT_DIR: Final = REPO_ROOT / "tools" / "audit"

#: The flag that distinguishes a census audit from a population key.
#:
#: Derived rather than transcribed, so the guard grows with the directory. The
#: population keys next door answer "what is the population" and always exit 0;
#: an audit answers "does every member discharge" and offers a control mode
#: precisely because its zero has to be readable.
CONTROL_FLAG: Final = "--positive-control"

#: Offline, always: see the module docstring.
OFFLINE: Final = "--offline"

#: The one function ``--positive-control`` reaches, and the root of the call graph
#: every other control runner has to hang off.
CONTROL_ENTRY: Final = "_run_positive_controls"

#: The control table every census audit has to bind, whatever else it carries:
#: the one that shows its keys hitting a planted sentence.
REQUIRED_CONTROL_TABLES: Final[frozenset[str]] = frozenset({"POSITIVE_CONTROLS"})

#: A control *runner*, and the table it exists to run, so each requirement is
#: derived from the module rather than from a list of module names kept in step by
#: hand. ``ref_field_pair.py`` defines neither and is asked for neither: it has no
#: ledger (each site discharges against its own text) and records no escape space.
#:
#: ``MEASURED_ESCAPES`` is here because of round three's code finding: the naming
#: key below was ``endswith("_CONTROLS")``, and the one table in this directory
#: that records what its key *cannot* see is not spelled that way. It was outside
#: the emptied-table check entirely, so gutting it passed both this guard and its
#: own audit.
REQUIRED_BY_RUNNER: Final[dict[str, str]] = {
    "_run_ledger_controls": "LEDGER_CONTROLS",
    "_run_escape_controls": "MEASURED_ESCAPES",
}

#: Every control table each audit must **run**, and how many rows of it, as the
#: audit itself counts them at run time.
#:
#: **Pinned rather than derived from the table's length, and that is the whole
#: point.** Each ``CONTROL-TALLY`` line reports rows the loop actually executed, so
#: a runner that iterates ``()``, slices its table to one row, or returns before
#: its loop prints a number that no longer matches. Deriving the expectation from
#: ``len(TABLE)`` would catch those three and miss the fourth -- a table, its
#: runner and the call to it all deleted together, which leaves nothing to be
#: inconsistent with. A pinned figure fails on that too: the tally line simply
#: stops being printed.
#:
#: The cost is that adding a control row is a two-file edit. That is deliberate:
#: the number here is the record of how much checking each audit does, and a
#: reviewer reading a diff that lowers one is reading the finding.
CONTROL_TALLIES: Final[dict[str, dict[str, int]]] = {
    "config_object_claims": {
        "POSITIVE_CONTROLS": 21,
        "MEASURED_ESCAPES": 6,
        "LEDGER_CONTROLS": 6,
    },
    "controls_discharge": {"POSITIVE_CONTROLS": 6, "LEDGER_CONTROLS": 5},
    "owner_position_cites": {
        "POSITIVE_CONTROLS": 12,
        "TREE_CONTROLS": 4,
        "LEDGER_CONTROLS": 7,
    },
    "ref_field_pair": {"POSITIVE_CONTROLS": 6},
    "sha_anchors": {
        "POSITIVE_CONTROLS": 4,
        "LANDED_CLAIM_CONTROLS": 5,
        "LEDGER_CONTROLS": 4,
    },
}

#: The line each audit prints per control table, as ``<prefix> <table> ran=<n>
#: failed=<m>``.
#:
#: Transcribed from ``claim_surfaces.CONTROL_TALLY`` rather than imported:
#: ``tools/audit/`` is a flat script directory rather than a package, and this
#: guard deliberately reads the audits the way a person does -- by running them and
#: reading their output. A rename on the other side makes *every* audit report no
#: tallies at all, which fails loudly here rather than quietly agreeing.
_TALLY_LINE: Final = re.compile(
    r"^CONTROL-TALLY (?P<table>\w+) ran=(?P<ran>\d+) failed=(?P<failed>\d+)$"
)

#: Generous, because these read every tracked file. The slowest of the five runs
#: in a few seconds on this machine; this is a hang guard, not a budget.
_TIMEOUT_SECONDS: Final = 300


def _offers_a_control_mode(path: pathlib.Path) -> bool:
    """Whether ``path`` is a module that *runs* controls, rather than one that says so.

    Two conditions, and the second is not decoration: the flag has to appear in the
    source **and** the module has to define :data:`CONTROL_ENTRY`. A text match
    alone reads a sentence about the flag as an offer of it -- ``claim_surfaces.py``
    documents the tally format the control runs print, and a spelled-name key put a
    shared helper module into this parametrisation, where every check below then
    failed on a module that has no controls to run.
    """
    source = path.read_text(encoding="utf-8")
    if CONTROL_FLAG not in source:
        return False
    module = ast.parse(source, filename=path.name)
    return any(
        isinstance(node, ast.FunctionDef) and node.name == CONTROL_ENTRY for node in module.body
    )


def _census_audits() -> tuple[pathlib.Path, ...]:
    """Every module in ``tools/audit/`` that offers a ``--positive-control`` mode."""
    found = tuple(sorted(path for path in AUDIT_DIR.glob("*.py") if _offers_a_control_mode(path)))
    assert found, (
        f"no module under {AUDIT_DIR} offers `{CONTROL_FLAG}`. Either the census "
        f"audits moved, or this key stopped selecting them -- and an empty "
        f"parametrisation is a guard that passes by running nothing."
    )
    return found


CENSUS_AUDITS: Final = _census_audits()

_IDS: Final = [path.stem for path in CENSUS_AUDITS]


def _run(script: pathlib.Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    """One audit, run the way a person runs it, from the repository root."""
    return subprocess.run(  # noqa: S603 - fixed argv, no shell, no caller input
        [sys.executable, str(script), *arguments],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=_TIMEOUT_SECONDS,
        check=False,
    )


def _report(done: subprocess.CompletedProcess[str], script: pathlib.Path, mode: str) -> str:
    tail = "\n".join(done.stdout.splitlines()[-40:])
    return (
        f"`{script.relative_to(REPO_ROOT)} {mode}` exited {done.returncode}, "
        f"and this guard exists because nothing else runs it.\n\n"
        f"--- stdout (last 40 lines) ---\n{tail}\n\n"
        f"--- stderr ---\n{done.stderr.strip()}\n"
    )


def test_every_module_offering_a_control_is_run() -> None:
    """RED means an audit joined or left ``tools/audit/`` without this guard noticing.

    The parametrised tests below derive their population from the directory, so
    they cannot miss a new audit -- but a derived population can also collapse
    silently, and a parametrisation over an empty tuple passes. This is the
    positive control on the key itself: the five known census audits are named
    here, and the derived set has to contain every one of them.

    A *sixth* audit is deliberately not a failure: it is picked up and run. What
    fails is one of these five vanishing from the derived set, which is what
    happens when a module loses its control mode.
    """
    derived = {path.stem for path in CENSUS_AUDITS}
    known = {
        "config_object_claims",
        "controls_discharge",
        "owner_position_cites",
        "ref_field_pair",
        "sha_anchors",
    }

    assert known <= derived, (
        f"these census audits are no longer selected by `{CONTROL_FLAG}`: "
        f"{sorted(known - derived)}.\n\n"
        f"The derived set is {sorted(derived)}. An audit that loses its control "
        f"mode drops out of the runs below without failing them, which is the "
        f"silence this whole module exists to prevent."
    )


def _module_bindings(script: pathlib.Path) -> tuple[dict[str, ast.expr | None], set[str]]:
    """One parse of ``script``: its module-level bindings, and the functions it defines."""
    module = ast.parse(script.read_text(encoding="utf-8"), filename=script.name)
    bindings: dict[str, ast.expr | None] = {}
    for node in module.body:
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            bindings[node.target.id] = node.value
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    bindings[target.id] = node.value
    defined = {node.name for node in module.body if isinstance(node, ast.FunctionDef)}
    return bindings, defined


def _is_control_table(name: str) -> bool:
    """Whether a module-level name is one of this guard's control tables.

    The suffix rule plus the tables :data:`REQUIRED_BY_RUNNER` names, because
    ``MEASURED_ESCAPES`` is a control table that is not spelled like one and was
    outside this key until round three said so.
    """
    return name.endswith("_CONTROLS") or name in set(REQUIRED_BY_RUNNER.values())


def _tallies(stdout: str) -> dict[str, int]:
    """The rows each control table reported **running**, parsed out of one run."""
    found: dict[str, int] = {}
    for line in stdout.splitlines():
        match = _TALLY_LINE.match(line.strip())
        if match is not None:
            found[match.group("table")] = int(match.group("ran"))
    return found


@pytest.mark.parametrize("script", CENSUS_AUDITS, ids=_IDS)
def test_each_census_audit_carries_control_rows_to_run(script: pathlib.Path) -> None:
    """RED means a control table was emptied, which its own audit reports as passing.

    ``--positive-control`` counts failures among the rows it has. Emptying a
    table therefore *passes* -- zero rows, zero failures, exit 0 -- and the run
    above cannot tell that from an instrument that checked something. Round two
    reported exactly this: "``LEDGER_CONTROLS`` itself deletes green".

    So the tables are read structurally, out of the source rather than by
    importing it, and every module-level name this guard reads as a control table
    has to bind a non-empty tuple.

    Names are required rather than merely checked-if-present, because a module
    could otherwise *drop* a table and satisfy a rule about the ones it still has.
    ``POSITIVE_CONTROLS`` is required of every audit; the rest is derived from the
    runners the module defines (:data:`REQUIRED_BY_RUNNER`) -- ``LEDGER_CONTROLS``
    of the four that carry a ledger, ``MEASURED_ESCAPES`` of the one that records
    an escape space.

    **The bound, and it moved in round three.** This check alone sees a table
    emptied and not a table deleted whole with its runner and the call to it,
    because nothing is then left to be inconsistent with. That hole is closed by
    :func:`test_each_census_audit_runs_the_control_rows_this_guard_records`, which
    pins the rows each audit reports running: a deleted table prints no tally line
    at all. What remains uncaught here is a table that keeps its row *count* while
    its rows stop asserting anything -- a row duplicated, or an expectation flipped
    to match what the code now does. Only the audit's own review catches that.
    """
    tables, defined = _module_bindings(script)

    required = REQUIRED_CONTROL_TABLES | {
        table for runner, table in REQUIRED_BY_RUNNER.items() if runner in defined
    }
    control_tables = {name: value for name, value in tables.items() if _is_control_table(name)}

    assert required <= control_tables.keys(), (
        f"{script.name} no longer binds {sorted(required - control_tables.keys())}. "
        f"Every census audit shows its keys hitting a planted sentence "
        f"(POSITIVE_CONTROLS), and every audit carrying a ledger drives its "
        f"reconciliation from planted rows (LEDGER_CONTROLS); a module missing one "
        f"has stopped checking that half and still exits 0."
    )

    empty = sorted(
        name
        for name, value in control_tables.items()
        if not (isinstance(value, ast.Tuple) and value.elts)
    )
    assert not empty, (
        f"{script.name}: {empty} bind no control rows.\n\n"
        "A control loop over an empty table reports zero failures, so the audit "
        "exits 0 and the run beside this one reads it as a working instrument. "
        "Emptying a table is how a control stops checking without anything "
        "going red."
    )


@pytest.mark.parametrize("script", CENSUS_AUDITS, ids=_IDS)
def test_every_control_runner_a_census_audit_defines_is_reached(script: pathlib.Path) -> None:
    """RED means a control runner is defined and nobody calls it, which exits 0.

    ``--positive-control`` is one entry point, and every other runner in these
    modules is reached from it by name. Deleting the ``| _run_escape_controls(...)``
    from that one return statement leaves the table bound, the runner defined and
    the audit exiting 0 with a whole family of controls never executed -- round
    three's D-class escape, and the sibling of the four cheap edits the tallies
    catch.

    So the call graph is read rather than the row count: every module-level
    ``_run_*_controls`` the module defines has to be **reachable** from
    ``_run_positive_controls``, transitively. Transitively because one of the five
    already chains -- ``sha_anchors.py`` reaches ``_run_ledger_controls`` through
    ``_run_landed_controls``, and nothing else here is more than one call deep --
    and a rule that demanded a direct mention would have to be relaxed for a shape
    that is correct.

    Reached by *name*, never through a variable: these are scripts, and an
    indirection here would be the finding rather than the fix.

    This is the structural half. The tallies beside it catch the same escape from
    the other side (an unreached runner prints no tally line), and neither is
    redundant: a runner reached but emptied prints a tally, and a runner deleted
    outright leaves nothing for this key to select.
    """
    _bindings, defined = _module_bindings(script)

    assert CONTROL_ENTRY in defined, (
        f"{script.name} defines no `{CONTROL_ENTRY}`, but it offers `{CONTROL_FLAG}` "
        f"-- so either the control mode moved to another name, in which case this "
        f"guard has to move with it, or the flag is parsed and does nothing."
    )

    module = ast.parse(script.read_text(encoding="utf-8"), filename=script.name)
    bodies = {node.name: node for node in module.body if isinstance(node, ast.FunctionDef)}
    reached: set[str] = set()
    frontier = [CONTROL_ENTRY]
    while frontier:
        name = frontier.pop()
        for node in ast.walk(bodies[name]):
            if isinstance(node, ast.Name) and node.id in bodies and node.id not in reached:
                reached.add(node.id)
                frontier.append(node.id)

    runners = {
        name for name in defined if name.startswith("_run_") and name.endswith("_controls")
    } - {CONTROL_ENTRY}

    assert runners <= reached, (
        f"{script.name}: {sorted(runners - reached)} is defined and nothing "
        f"`{CONTROL_ENTRY}` reaches ever calls it.\n\n"
        f"A control runner nobody calls checks nothing, and the audit still exits 0 "
        f"with every table intact -- which is what makes this shape cheaper to "
        f"produce, by accident or otherwise, than emptying a table. "
        f"`{CONTROL_ENTRY}` is the one entry point `{CONTROL_FLAG}` reaches; "
        f"whatever a module defines beside it has to be reachable from there."
    )


@pytest.mark.parametrize("script", CENSUS_AUDITS, ids=_IDS)
def test_each_census_audit_runs_the_control_rows_this_guard_records(
    script: pathlib.Path,
) -> None:
    """RED means an audit ran a different number of control rows than it records.

    Each control runner counts the rows its loop **executes** and prints one
    ``CONTROL-TALLY`` line per table; :data:`CONTROL_TALLIES` is what those numbers
    have to be. That pair is what closes the four cheap edits round three measured
    against the old guard, each of which left the audit exiting 0 with its tables
    bound and its docstrings unchanged:

    * a runner beginning ``return 0`` -- no tally line at all;
    * a loop rewritten to iterate ``()`` -- ``ran=0``;
    * a table sliced, ``POSITIVE_CONTROLS[:1]`` -- ``ran=1``;
    * a table, its runner and the call deleted together -- no tally line, which is
      the case the structural check beside this one cannot see, because nothing is
      left to be inconsistent with.

    The numbers are pinned rather than derived from ``len(TABLE)`` for that last
    reason, and the cost is that adding a control row is a two-file edit. Read a
    diff that *lowers* one of these as the finding it is.
    """
    done = _run(script, CONTROL_FLAG, OFFLINE)
    recorded = CONTROL_TALLIES[script.stem]

    assert _tallies(done.stdout) == recorded, (
        f"`{script.name} {CONTROL_FLAG}` ran {_tallies(done.stdout)} control rows, "
        f"and this guard records {recorded}.\n\n"
        f"A table missing from the run has stopped being executed -- its runner "
        f"returns early, or nothing calls it, or the table is gone. A count that "
        f"dropped means the loop no longer covers the table it names. A count that "
        f"rose means a control row was added, which is the one case where the fix "
        f"is to update the figure here.\n\n" + _report(done, script, f"{CONTROL_FLAG} {OFFLINE}")
    )


def test_this_guards_own_control_keys_select_something() -> None:
    """RED means this guard was emptied, which makes every check above vacuous.

    Every assertion here is driven by one of four module-level keys, and each of
    them passes trivially when it is empty: a required-table set with nothing in
    it is satisfied by any module, a runner map with nothing in it requires no
    table, and a tally record with an empty entry for an audit expects that audit
    to run no controls at all. Round three planted exactly that -- ``frozenset()``
    in place of :data:`REQUIRED_CONTROL_TABLES` -- and the suite stayed green.

    So the guard's own keys are held the way it holds the audits': they must
    select something, and the population they select must be the derived audit
    set rather than a list that can fall behind it.
    """
    assert "POSITIVE_CONTROLS" in REQUIRED_CONTROL_TABLES, (
        f"REQUIRED_CONTROL_TABLES is {sorted(REQUIRED_CONTROL_TABLES)}, and every "
        f"census audit must be required to bind POSITIVE_CONTROLS -- the table that "
        f"shows its keys hitting a planted sentence. Emptying this set makes "
        f"`test_each_census_audit_carries_control_rows_to_run` pass for a module "
        f"with no controls at all."
    )
    assert REQUIRED_BY_RUNNER, (
        "REQUIRED_BY_RUNNER is empty, so a module that defines `_run_ledger_controls` "
        "or `_run_escape_controls` is no longer required to bind the table it runs, "
        "and MEASURED_ESCAPES drops out of the naming key with it."
    )
    assert CONTROL_TALLIES.keys() == {path.stem for path in CENSUS_AUDITS}, (
        f"CONTROL_TALLIES records {sorted(CONTROL_TALLIES)} and the derived audit set "
        f"is {sorted(path.stem for path in CENSUS_AUDITS)}. An audit with no entry "
        f"has no pinned control count, which is the check that catches a runner "
        f"nobody calls; an entry with no audit is a record of an instrument that "
        f"left."
    )

    empty = sorted(
        stem
        for stem, tables in CONTROL_TALLIES.items()
        if not tables or any(count < 1 for count in tables.values())
    )
    assert not empty, (
        f"{empty}: the pinned control tally is empty or admits a table that runs no "
        f"rows. Either way the audit is recorded as checking nothing and passes by "
        f"agreeing with that record."
    )


@pytest.mark.parametrize("script", CENSUS_AUDITS, ids=_IDS)
def test_each_census_audit_reports_this_tree_discharged(script: pathlib.Path) -> None:
    """RED means a governed claim in this tree is unrecorded, stale or ambiguous.

    This is the audit's own verdict, relayed. Read its stdout in the failure
    message: each one names the rows it could not reconcile and says which
    direction the drift is in -- an unjudged suspect, a ledger row the sweep no
    longer produces, one judgement covering two members, or a recorded verdict
    the classifier disagrees with.

    Fixing it is never "delete the row". The audits are written so the good
    direction and the bad direction both fail, and the message says which one
    happened.
    """
    done = _run(script, OFFLINE)

    assert done.returncode == 0, _report(done, script, OFFLINE)


@pytest.mark.parametrize("script", CENSUS_AUDITS, ids=_IDS)
def test_each_census_audit_can_still_fail(script: pathlib.Path) -> None:
    """RED means an instrument stopped being able to report, which reads as clean.

    ``--positive-control`` plants what each key is supposed to catch and asserts
    it is caught, plants what it must not catch and asserts it is not, and drives
    every direction of that audit's ledger reconciliation from synthetic rows
    against a synthetic ledger. It is the check on the check.

    Round two is why this runs in the suite rather than by hand: six mutations
    reverting round-one fixes survived the whole suite, and deleting a
    ``LEDGER_CONTROLS`` table outright left it green. Every one of those is a
    failure here.
    """
    done = _run(script, CONTROL_FLAG, OFFLINE)

    assert done.returncode == 0, _report(done, script, f"{CONTROL_FLAG} {OFFLINE}")
