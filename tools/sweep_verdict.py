"""What the night concluded, read out of ``tools/mutate.py``'s own record.

One rule, and everything here is an expression of it: **a sweep that did not
really run must not read clean.** A scheduled red-team job whose failure mode is
silence converts an absence of evidence into evidence of absence, run after run,
in a place nobody is watching.

So "clean" is defined positively rather than as the absence of bad news. The
harness exited 0, its unmutated control came back GREEN, and there is a KILLED
verdict for every mutation that was handed over. Anything else -- a missing
control, an empty outcome list, five verdicts for six mutations, an exit code
that disagrees with the record -- files an issue, because each of those reads
exactly like a clean night in a summary line and none of them is one.

``tools/mutate.py``'s exit codes are the primary input: 0 every mutation killed,
1 at least one survived or hung, 2 the run itself cannot be trusted.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Final

from sweep_census import SweepError

#: The label ``tools/mutate.py`` gives its unmutated baseline run. Pinned against
#: ``mutate._CONTROL_LABEL`` by ``tests/unit/tools/test_sweep_verdict.py``: a
#: sweep looking for a label nothing writes can never see a control-red.
CONTROL_LABEL: Final = "__control__"

#: Verdicts that mean the suite does not hold the mutated property. HUNG belongs
#: here for the harness's own reason: a suite that never finishes cannot go RED.
UNHELD_VERDICTS: Final = frozenset({"SURVIVED", "HUNG"})

SURVIVORS: Final = "survivors"
UNTRUSTED: Final = "run-untrusted"

#: ``tools/mutate.py``'s "the run itself cannot be trusted" exit code -- an
#: anchor that did not match, a restore that did not restore, a control that was
#: not green, or a mutation that reached the real checkout. Named here because
#: two modules branch on it and a bare 2 in either would read as an ordinary
#: count. Pinned against the harness's own documented codes in
#: ``tests/unit/tools/test_sweep_verdict.py``.
UNTRUSTED_EXIT: Final = 2


@dataclass(frozen=True)
class Outcome:
    """One line of the harness's record."""

    label: str
    verdict: str
    seconds: float
    summary: str

    @property
    def is_control(self) -> bool:
        return self.label == CONTROL_LABEL


@dataclass(frozen=True)
class Reading:
    """What this night amounts to, and why."""

    #: ``None`` when the night is clean. Otherwise :data:`SURVIVORS` or
    #: :data:`UNTRUSTED`, which is what the filed issue is titled by.
    reason: str | None
    detail: str
    unheld: tuple[str, ...] = ()


def read_results(document: str) -> tuple[Outcome, ...]:
    """The harness's JSON record, or a refusal.

    Refusing rather than returning an empty tuple is deliberate: empty routes
    into the untrusted path, which files an issue, and filing a red-team finding
    because a file was truncated spends a triage slot on a broken job. The driver
    turns this into exit 1, which is what the workflow alarms on.

    The document has been an object since #566, and the bare list it used to be
    is refused rather than read. Not because anything here consumes the options
    block -- this parser reads ``outcomes`` and nothing else -- but because the
    block is what records *what the verdicts cover*, ``deselect`` above all, and
    a run whose record cannot carry that is a run whose verdicts nobody can
    qualify afterwards. The filed issue and its reader are the consumers that
    matter; accepting the older shape would file verdicts stripped of the one
    caveat that can make "0 survived" mean less than it says.
    """
    try:
        loaded = json.loads(document)
    except json.JSONDecodeError as error:
        raise SweepError(f"the harness wrote a results file that is not JSON: {error}") from error
    if not isinstance(loaded, dict):
        raise SweepError(
            "the harness's results document is not an object; a bare list is the "
            "pre-#566 format, which carries no record of what the run covered"
        )
    raw = loaded.get("outcomes")
    if not isinstance(raw, list):
        raise SweepError("the harness's results document carries no `outcomes` list")
    outcomes: list[Outcome] = []
    for index, entry in enumerate(raw):
        if not isinstance(entry, dict):
            raise SweepError(f"outcome {index} in the harness's record is not an object")
        try:
            outcomes.append(
                Outcome(
                    label=str(entry["label"]),
                    verdict=str(entry["verdict"]),
                    seconds=float(entry.get("seconds", 0.0)),
                    summary=str(entry.get("summary", "")),
                )
            )
        except KeyError as missing:
            raise SweepError(f"outcome {index} in the harness's record is missing {missing}") from (
                missing
            )
    return tuple(outcomes)


def classify(*, mutate_exit: int, outcomes: tuple[Outcome, ...], submitted: int) -> Reading:
    """Whether tonight files, and under which heading.

    Written as a single positive test for "clean" with every other path falling
    through to a filing. The inverted form -- a chain of ``if something_bad``
    checks -- is how a new failure shape gets to be silent by default, which is
    the one outcome this module exists to prevent.
    """
    mutations = tuple(item for item in outcomes if not item.is_control)
    controls = tuple(item for item in outcomes if item.is_control)
    unheld = tuple(item.label for item in mutations if item.verdict in UNHELD_VERDICTS)

    green_control = len(controls) == 1 and controls[0].verdict == "control-green"
    complete = len(mutations) == submitted and submitted > 0
    all_killed = all(item.verdict == "KILLED" for item in mutations)

    if mutate_exit == 0 and green_control and complete and all_killed:
        return Reading(reason=None, detail=f"{submitted} mutation(s) killed over a GREEN control")

    if unheld and mutate_exit == 1 and green_control and complete:
        return Reading(
            reason=SURVIVORS,
            detail=f"{len(unheld)} of {submitted} mutation(s) the suite does not hold",
            unheld=unheld,
        )

    return Reading(
        reason=UNTRUSTED,
        detail=(
            f"the harness exited {mutate_exit} and reported {len(mutations)} of {submitted} "
            f"mutation(s) under {len(controls)} control run(s); no verdict in this batch "
            "can be read as a result"
        ),
        unheld=unheld,
    )
