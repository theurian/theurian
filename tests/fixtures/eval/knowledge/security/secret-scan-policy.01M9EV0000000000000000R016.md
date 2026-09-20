# Secret scanning policy

Two secret scans run over knowledge content, at two different moments, with two
different dispositions. Confusing them is how a report-only scan gets read as a
control that blocks.

## Block by default, at accept

When a proposal is accepted, the body is scanned before it is written to the
canonical store, and a detector hit **blocks the accept**. The operator is shown
what matched and where, and the accept is refused until the content is edited or
the finding is explicitly overridden.

Blocking is correct here because nothing has been published yet. Refusing costs
one round trip; accepting costs a rewrite of history.

## Report only, at index time

The index build scans every projected body again and writes a report beside the
build. That second scan **does not refuse**, and the reason is timing: by the
moment the index builder sees the text, the content is already in the canonical
store, so refusing would leave a corpus that cannot be indexed rather than a
corpus without a secret in it.

The report names the item, the revision and the detector that fired. It is a
prompt for a withdrawal, not a gate.

## What the detectors cover

Detectors look for high-entropy tokens near credential-shaped keywords, for known
provider prefixes, and for private key headers. They do not recognise a secret
that looks like prose, and no detector set ever will. A reviewer reading the diff
is still the control that catches those.

A scan configured with no rules is a green job that has stopped checking, so the
rule set is extended and never replaced.
