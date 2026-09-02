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

import pathlib
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

#: Generous, because these read every tracked file. The slowest of the five runs
#: in a few seconds on this machine; this is a hang guard, not a budget.
_TIMEOUT_SECONDS: Final = 300


def _census_audits() -> tuple[pathlib.Path, ...]:
    """Every module in ``tools/audit/`` that offers a ``--positive-control`` mode."""
    found = tuple(
        sorted(
            path
            for path in AUDIT_DIR.glob("*.py")
            if CONTROL_FLAG in path.read_text(encoding="utf-8")
        )
    )
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
