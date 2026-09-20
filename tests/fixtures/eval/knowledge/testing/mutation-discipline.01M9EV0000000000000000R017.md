# Mutation discipline

A test that cannot fail reports a safety that does not exist. Mutation runs are
how this project finds those before a reviewer has to.

## The run

A mutation run copies the checkout, perturbs one source file, and runs the suite
against the copy. A mutation that makes the suite go red is **killed**: some test
was actually exercising the behaviour. A mutation that leaves the suite green
**survived**, and a surviving mutant is a finding, not a statistic.

The copy matters. Perturbing a tree another agent is editing loses that agent's
uncommitted work, and restoring a perturbed file with a checkout discards every
uncommitted edit in it.

## Surviving mutants are findings

A survivor says one of two things, and the two are handled differently:

- **No test reaches the line.** The gap is a missing test, and the mutation is
  the specification for it.
- **A test reaches the line and asserts nothing that depends on it.** This is the
  worse case, because the coverage report is green. An assertion that holds
  regardless of the implementation — a count that cannot be negative, a field
  checked for existence rather than for value — is the shape to look for.

## Proving a new test can fail

After writing a test for a behaviour, break that behaviour in the source, run the
test, confirm it goes red, and revert. A test delivered without that step is an
untested test, and the report says so.

The discipline generalises past mutation runs: a fixture whose setup never
reaches the branch under test is the same defect wearing a different hat.
