"""What ADR-0018 says about NFS detection, against what the setup steps read.

ADR-0018's Consequences accepted the advisory lock's behaviour on network
filesystems on the grounds that a ``.theurian/state/`` directory on NFS "is
already outside the supported configuration, and ``doctor`` will warn about it".
The warning does not exist. No step reads a filesystem type, so an operator whose
project directory sits on NFS runs ``doctor``, sees nothing, and concludes the
configuration is supported -- the defect the correction removed
(https://github.com/theurian/theurian/issues/417).

**The bullet now states the absence, and this module holds it in both
directions.** Two things can each make ADR-0018 false again, and they fail here
separately:

- **Drift back.** The mitigation returns in some tense -- ``doctor`` warns, will
  warn, or detects NFS -- and a durable architectural record cites a control that
  does not exist. This is the third firing of that family
  (#252/#415 for ADR-0013, #195 still open), which is why it is pinned rather
  than merely corrected.
- **A probe lands.** Someone implements filesystem-type detection, and the ADR
  goes on saying nothing detects it. The fact half below reads the source of the
  two files a probe would land in, so the bullet is updated by the change that
  makes it wrong.

The fact is read from **source text**, not from a call. There is nothing to
invoke: the claim is about an absence, and an absence has no return value. What
makes that sweep mean something is the population control inside it -- ``STEPS``
must still cover ``set(StepId)`` and the swept text must be non-empty -- so a
registry that is emptied, or moved out from under the sweep, turns the premise
RED instead of quietly making the search succeed over nothing.

**What the fact pin actually enforces, which is narrower than "no probe
exists".** It is a source-text search for a fixed list of filesystem-type APIs
over two named files, so:

- A probe implemented with one of the listed APIs -- ``statvfs``,
  ``f_fstypename``, ``f_type``, ``fstype``, ``disk_partitions``, ``getmntent``,
  ``/proc/mounts``, ``mountpoint``, ``nfs`` -- inside ``domain/setup.py`` or
  ``application/setup_steps.py`` fails this module.
- A probe under an API not on that list escapes it, and so does one registered
  from any other file. The list is the shapes two reviewers named, not a
  characterisation of every way a filesystem type can be read; shelling out to
  ``mount``, reading a config, or importing a helper module would all pass.
- **The "no probe is planned" half has no fact side at all.** A plan is not a
  property of the source tree, so nothing here can read it and the prose pin
  alone holds it. The same is true of the ``doctor``-reports-the-whole-tuple
  citation, which is held where it is produced by
  ``tests/integration/test_setup_service.py::test_every_specified_step_is_reported``
  rather than restated here.

**Two named files, and the corpus twin is deliberately not one of them.**
``.theurian/knowledge/architecture/single-writer-synchronous-in-m1.<ulid>.md``
still carries the retracted sentence byte-identically. That is not drift: the
dogfood corpus is held byte-identical to its source anchor commit by
``test_dogfood_corpus_governance.py::test_every_pinned_body_is_byte_identical_to_its_source_anchor_commit``,
so only a governed re-seed can move it -- tracked as #199 unit C. A repo-wide
walker over this wording would go RED on that file on the day it was written.
Recorded here because a reader who greps the tree for the old sentence finds it
and needs to know why it stays.

**Neither prose half is a closure argument.** They are regression pins over the
wording this claim has actually taken, and a rule that pins grammar always has a
next grammar.
"""

from __future__ import annotations

import pathlib
import re
from typing import Final

from theurian.application import setup_steps
from theurian.application.setup_steps import STEPS
from theurian.domain import setup as setup_domain
from theurian.domain.setup import StepId

REPO_ROOT = pathlib.Path(__file__).resolve().parents[4]

ADR_0018 = REPO_ROOT / "docs" / "adr" / "0018-single-writer-synchronous-in-m1.md"

#: The two modules a filesystem-type probe would land in: the registry of step
#: identities and the table of steps themselves. Swept as text via their own
#: ``__file__`` rather than by a hardcoded path, so a module that moves takes the
#: sweep with it instead of leaving it reading a file nobody edits any more.
SWEPT_MODULES: Final = (setup_domain, setup_steps)

#: The negation the corrected bullet turns on, as one sentence rather than as the
#: two lines the file wraps it onto. Compared after :func:`_collapsed`: the claim
#: spans a line break -- "and nothing\n  detects that it is" -- and a substring
#: search over the raw text passes while the sentence is being rewritten around
#: it. The em dash before "and" is left out so the pin does not turn on
#: punctuation.
NOTHING_DETECTS: Final = "nothing detects that it is"

#: The issue that owns the exclusion. Asserted in the same paragraph as the
#: negation rather than merely somewhere in the file: an unenforced exclusion
#: with no tracking reference is a decision nobody can find again.
TRACKED_BY: Final = "issues/417"

#: Filesystem-type APIs, as the two reviewers named them. Matched
#: case-insensitively against source text -- see the module docstring for what
#: this list does and does not reach.
_FILESYSTEM_TYPE_API: Final = re.compile(
    r"statvfs|f_fstypename|f_type|fstype|disk_partitions|getmntent|/proc/mounts|mountpoint|nfs",
    re.IGNORECASE,
)

#: A finite-verb claim that something detects or warns. Only the finite forms the
#: retracted sentence used or could use -- ``warns``, ``will warn``, ``detects``,
#: ``will detect``. The bare infinitive is left out on purpose: the corrected
#: bullet says "for want of a portable **detection** design", and a pin that
#: fires on the noun would punish the wording that states the absence.
_DETECTION_CLAIM: Final = re.compile(
    r"(?P<lead>(?:\S+\s+){0,6})\b(?:warns|detects|will\s+warn|will\s+detect)\b"
)

#: Words that turn a detection claim into the sentence this module wants. Taken
#: from ``test_setup_claims.py``, along with its recorded weakness: ``nothing``
#: counts as a denial, so the rule is weakest exactly where the claim is most
#: likely to return.
_DENIAL: Final = re.compile(r"\b(?:not|never|cannot|no|nothing|neither|nor)\b")

#: The paragraphs this module reads at all. A detection verb elsewhere in
#: ADR-0018 is about locking or migrations, not about this bullet.
_NFS_OR_DOCTOR: Final = re.compile(r"\bnfs\b|\bdoctor\b")

#: A line that begins a new block rather than continuing the one above it.
#: Copied from ``test_setup_claims.py``, whose docstring records why a scan that
#: stops at every newline and a scan that ignores newlines are both wrong.
_BLOCK_START: Final = re.compile(r"\s*(?:#{1,6}\s|[-*+]\s|\d+\.\s|\||```|---\s*$|>\s)")


def _collapsed(text: str) -> str:
    """Lowercased with runs of whitespace flattened to single spaces."""
    return " ".join(text.lower().split())


def _paragraphs(text: str) -> list[str]:
    """The document's paragraphs, soft wraps joined and block boundaries kept.

    The NFS acceptance is one list item wrapped over fourteen lines, so a scan
    that stops at every newline never sees the sentence whole. A scan that
    ignores newlines entirely reads the next bullet into this one, which would
    let a detection claim in the following paragraph borrow this one's denial.
    """
    blocks: list[list[str]] = [[]]
    for line in text.splitlines():
        if not line.strip() or _BLOCK_START.match(line):
            blocks.append([])
        blocks[-1].append(line)

    return [collapsed for block in blocks if (collapsed := _collapsed(" ".join(block)))]


def _nfs_paragraphs(text: str) -> list[str]:
    """The paragraphs that talk about NFS or ``doctor``."""
    return [paragraph for paragraph in _paragraphs(text) if _NFS_OR_DOCTOR.search(paragraph)]


def _detection_claims_without_denial(text: str) -> list[str]:
    """Every "X warns/detects" in an NFS paragraph that does not deny it.

    "nothing detects that it is" is the sentence the ADR is supposed to contain,
    so a claim whose own lead carries a denial is exactly right. What is left
    over is a sentence telling a reader that something reports an NFS directory.
    """
    return [
        match.group(0)
        for paragraph in _nfs_paragraphs(text)
        for match in _DETECTION_CLAIM.finditer(paragraph)
        if not _DENIAL.search(match.group("lead"))
    ]


def _filesystem_type_apis(text: str) -> list[str]:
    """Every filesystem-type API named in a piece of source text."""
    return _FILESYSTEM_TYPE_API.findall(text)


# -- The prose: ADR-0018's NFS acceptance ------------------------------------


def test_adr_0018_says_nothing_detects_a_project_directory_on_nfs() -> None:
    """RED means the stated absence is gone -- the correction undone or reworded.

    The positive half. It is not the negative one restated: a rewrite that drops
    the sentence entirely, or that softens it to "NFS is outside the supported
    configuration" with no statement about detection, makes no false claim and
    would pass
    :func:`test_adr_0018_does_not_claim_anything_warns_about_or_detects_nfs`
    while leaving ADR-0018 silent on the thing an operator needs to know.

    The tracking reference is asserted in the same paragraph, because an
    exclusion enforced by nothing is a decision that has to stay findable.
    """
    paragraphs = [
        p for p in _nfs_paragraphs(ADR_0018.read_text(encoding="utf-8")) if NOTHING_DETECTS in p
    ]

    assert len(paragraphs) == 1, (
        f"ADR-0018 no longer states, exactly once, that nothing detects a project "
        f"directory on NFS: {paragraphs}"
    )
    assert TRACKED_BY in paragraphs[0], (
        f"the bullet states the absence without naming the issue that tracks it: {paragraphs[0]}"
    )


def test_adr_0018_does_not_claim_anything_warns_about_or_detects_nfs() -> None:
    """RED means the phantom mitigation is back, in some tense.

    The negative half, and it catches what the positive one cannot: a bullet that
    keeps "nothing detects that it is" and asserts the warning somewhere else in
    the file. The wording it took was ``doctor`` "will warn about it", so the
    future tense is refused alongside the present.

    Scoped to ADR-0018 alone. The governed corpus snapshot of this document still
    carries the retracted sentence by design -- see the module docstring -- so
    widening this to a tree scan would report that anchor as drift.
    """
    claims = _detection_claims_without_denial(ADR_0018.read_text(encoding="utf-8"))

    assert not claims, f"ADR-0018 claims something warns about or detects NFS: {claims}"


# -- The fact: what the setup steps read -------------------------------------


def test_the_filesystem_api_sweep_catches_a_probe_in_synthetic_source() -> None:
    """RED means the sweep stopped matching, so the test below passes over nothing.

    The one assertion here driven by synthetic input rather than by the shipped
    source, and it exists because the shipped source cannot drive it: the sweep's
    whole point is that it finds nothing today, so an implementation that always
    returned nothing would look identical. That is the mutation this catches --
    measured on the ADR-0013 module, where deleting a scan's core left every
    other test green.

    Both a plain call and a mixed-case, nested one are fed in, because the sweep
    is the only thing standing between a landed probe and an ADR that says none
    exists.
    """
    plain = "import os\n\ndef probe(path):\n    return os.statvfs(path).f_fstypename\n"
    mixed_case = "class Probe:\n    def read(self):\n        return MountPoint('NFS').FsType\n"

    assert _filesystem_type_apis(plain), "the sweep no longer matches a plain `statvfs` call"
    assert _filesystem_type_apis(mixed_case), (
        "the sweep no longer matches a mixed-case, nested filesystem-type read"
    )


def test_no_setup_step_source_reads_a_filesystem_type() -> None:
    """RED means a probe landed -- and ADR-0018 must stop saying nothing detects NFS.

    The fact half of the pin. It is a source-text search, because the claim is
    about an absence and there is no call whose return value could report one.

    The population is asserted first, and that is what stops this passing
    vacuously. A search over an empty string finds nothing exactly as a clean
    source file does, and a ``STEPS`` table that no longer covers ``StepId`` --
    emptied, split, or relocated -- would leave the ADR's "no step reads a
    filesystem type" true of a registry that is no longer the one shipping.
    """
    assert set(StepId), "the step registry is empty; this sweep would have nothing to be about"
    assert {step.step_id for step in STEPS} == set(StepId), (
        "STEPS no longer covers the StepId registry, so a probe could be registered "
        "in the gap without this sweep or the ADR's claim noticing"
    )

    found: dict[str, list[str]] = {}
    for module in SWEPT_MODULES:
        source = pathlib.Path(module.__file__ or "").read_text(encoding="utf-8")
        assert source.strip(), f"{module.__name__} has no source to sweep"
        if apis := _filesystem_type_apis(source):
            found[module.__name__] = sorted(set(apis))

    assert not found, (
        f"a setup step now reads a filesystem type: {found}. ADR-0018's Consequences "
        f"bullet must stop saying nothing detects a project directory on NFS"
    )
