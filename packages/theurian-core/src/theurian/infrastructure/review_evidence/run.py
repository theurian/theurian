"""The stamp that says which ingestion run last observed a record.

ADR-0030 decision 3: refetch is a best-effort refresh. A later run updates what
upstream still returns and **does not delete** what it no longer does, so a
record that vanished upstream stays on disk carrying the run that last saw it.
That stamp is the only thing distinguishing "still there" from "gone, and this is
when we last had it" -- without it, the survival guarantee would be unobservable.

**Both fields come from the determinism ports, never from ``datetime.now`` or
``uuid``**, for the reason :mod:`theurian.domain.ports.determinism` records: a
value taken from the wall clock inside a writer cannot be pinned by a test, and
these values are written into files a later test reads back. The ports are taken
by :func:`new_ingestion_run` rather than by the store, so one run stamps every
record it writes with one value -- a store that called the clock per record would
give two records from one run two different times and make "the same run" a thing
no reader could recognise.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from theurian.domain.errors import InvariantViolationError
from theurian.domain.ports.determinism import Clock, IdGenerator


@dataclass(frozen=True, slots=True)
class IngestionRun:
    """One ingestion run, as the record on disk names it."""

    #: A ULID, so runs sort in the order they happened -- which is what makes
    #: "this record's stamp did not advance" readable as "run two did not see it"
    #: rather than as an unordered difference.
    run_id: str
    #: When the run observed the records it wrote, timezone-aware.
    observed_at: datetime

    def __post_init__(self) -> None:
        if not self.run_id:
            raise InvariantViolationError("IngestionRun.run_id must not be empty")
        if self.observed_at.tzinfo is None:
            raise InvariantViolationError(
                f"IngestionRun {self.run_id} carries a naive observed_at. A stamp read "
                "back from a file is compared across machines, and a naive value "
                "compares wrong the moment two of them were written in different zones."
            )


def new_ingestion_run(*, clock: Clock, ids: IdGenerator) -> IngestionRun:
    """A fresh stamp for one run, from the injected ports."""
    return IngestionRun(run_id=str(ids.new_ulid()), observed_at=clock.now())
