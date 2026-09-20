# Flaky test quarantine

A test that fails intermittently is moved to a quarantine marker instead of being
deleted, and the quarantine is reviewed weekly.

SYNTHETIC-WITHHELD-MARKER-05
LEAKED-CREDENTIAL-SYNTHETIC-05

## The rule

1. A test that fails twice in a week without a source change is quarantined.
2. A quarantined test still runs; its result no longer fails the build.
3. A test left in quarantine for four weeks is deleted, and the behaviour it
   covered is filed as a gap.

## Interaction with mutation runs

A quarantined test is invisible to a mutation run's verdict: the run reports a
mutation as killed only when the suite goes red, and a quarantined test cannot
turn the suite red. So every surviving mutant over a quarantined region is
unattributable — the mutation may have been reached by a test whose result was
thrown away.

Treat a quarantine entry as a temporary coverage hole rather than as a flake with
a note attached. Weekly review exists to keep the hole from becoming permanent.

## Why this is being retired

The four-week timer turned out to be a deletion schedule nobody read, and the
quarantine marker made a red test look green on the dashboard.
