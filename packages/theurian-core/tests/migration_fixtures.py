"""The body pin a migration fixture has to declare (ADR-0027 decision 1).

``contentSha256`` is required on every ``upsertRevision``, so a fixture that
writes a body file and a migration naming it now has to keep the two in step.
One helper rather than a ``hashlib`` line per fixture: the bytes and the digest
are one fact, and twenty fixtures each spelling it out is twenty places for the
two halves to drift apart.

**Deliberately ``hashlib`` rather than ``ContentHash.of_bytes``.** The product
derives the pin with the latter, both in ``ProposalService.draft`` and in the
loader that re-checks it. A fixture reaching for the same call would agree with
a broken implementation of it, and the digest comparison every one of these
fixtures now passes through would prove nothing.
"""

from __future__ import annotations

import hashlib
from typing import Final

#: A pin for a body the loader never reaches -- one whose ``contentFile``
#: escapes the project root, names a missing file, or resolves through a symlink
#: the containment check refuses. Those fixtures are refused before the digest
#: is compared, so the value only has to satisfy the schema's 64-hex pattern.
#: Named rather than spelled inline so a reader can tell "this digest is not the
#: thing under test" from "this digest is deliberately wrong".
#:
#: Not ``"0" * 64``, which was the first shape tried: YAML reads 64 zeros as the
#: *integer* 0, the schema then rejects a non-string, and every fixture using it
#: fails on a schema error instead of the containment refusal under test.
UNREACHED_BODY_PIN: Final = "deadbeef" * 8


def body_pin(body: str) -> str:
    """The ``contentSha256`` a fixture must declare for ``body``."""
    return hashlib.sha256(body.encode("utf-8")).hexdigest()
