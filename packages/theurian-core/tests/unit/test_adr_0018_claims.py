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
  does not exist. Two nested classes meet here, and naming the wrong one is how
  this docstring first contradicted the CHANGELOG entry shipping beside it. The
  broad class is a **compliance claim a durable record makes about a component
  that does not exist** -- #129, #198, #252, #195, and this correction. The
  narrow one is **a corrected claim that shipped without a pin**, which is what
  this module is: three firings, burned into
  ``.claude/agents/theurian-docs.md`` as rule 6 -- #415 round one, pin absent;
  #415 round two, the pin's stated reach overclaimed; #420 round one, pin absent
  again.
- **A probe lands.** Someone implements filesystem-type detection, and the ADR
  goes on saying nothing detects it. The fact half below reads the source of the
  modules the step registry resolves to, so the bullet is updated by the change
  that makes it wrong.

The fact is read from **source text**, not from a call. There is nothing to
invoke: the claim is about an absence, and an absence has no return value. A
search for an absence reports success when it searches nothing, so what makes it
mean anything is the population control: ``STEPS`` must still cover
``set(StepId)``, every step's probe must resolve into the package, the swept
population must be non-empty, and every module in it must have source to read --
**each asserted before the search runs, not inside the loop that performs it.**

That ordering is not a detail. The first version of this module checked the
source of each module *within* the loop over the population, which asserts
nothing when the population is empty, and it named the two modules by hand while
claiming in a comment that a relocation would carry the sweep along. Both halves
were false together: emptying the swept set while a real ``statvfs`` probe sat in
``setup_steps`` left every test in this module green. The population is now
derived from the symbols -- the module defining ``StepId``, and the modules
defining each step's ``probe`` and ``apply`` -- so moving the registry moves the
sweep, and an empty population fails at the premise rather than passing at the
conclusion.

**What the fact pin actually enforces, which is narrower than "no probe
exists".** It is a source-text search for a fixed list of filesystem-type APIs
over the modules the step registry currently resolves to, so:

- A probe implemented with one of the listed APIs -- ``statvfs``,
  ``f_fstypename``, ``f_type``, ``fstype``, ``disk_partitions``, ``getmntent``,
  ``/proc/mounts``, ``mountpoint``, ``nfs`` -- inside a module that defines
  ``StepId`` or any step's ``probe`` or ``apply`` fails this module. Today that
  resolves to ``domain/setup.py`` and ``application/setup_steps.py``; the test
  asserts what it resolved to rather than assuming it.
- A probe under an API not on that list escapes it, and so does one whose
  callable is defined outside the swept modules -- a helper module imported and
  called from a step is not read here. The list is the shapes two reviewers
  named, not a characterisation of every way a filesystem type can be read;
  shelling out to ``mount`` or reading a config would both pass.
- **A probe that does not resolve into the package fails the premise**, naming
  the step. This is the silent-narrowing case, and the shape it takes is not the
  one first guessed: a ``functools.partial`` does *not* lack ``__module__`` --
  it inherits the attribute from its type and reports ``functools`` -- so a
  wrapped probe would have swept the standard library while the module holding
  the real code dropped out, with the population still non-empty and every test
  green. Partials are therefore unwrapped, and any probe still resolving outside
  ``theurian.`` is refused. An ``apply`` is not held to the same rule, since it
  may legitimately be absent, so a probe is the surface this guarantees.
- **The "no probe is planned" half has no fact side at all.** A plan is not a
  property of the source tree, so nothing here can read it and the prose pin
  alone holds it. The same is true of the ``doctor``-reports-the-whole-tuple
  citation, which is held where it is produced by
  ``tests/integration/test_setup_service.py::test_every_specified_step_is_reported``
  rather than restated here.

**One ADR file, and the corpus twin is deliberately not part of the prose scan.**
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

import functools
import pathlib
import re
import sys
from types import ModuleType
from typing import Final

from theurian.application.setup_steps import STEPS
from theurian.domain.setup import StepId

REPO_ROOT = pathlib.Path(__file__).resolve().parents[4]

ADR_0018 = REPO_ROOT / "docs" / "adr" / "0018-single-writer-synchronous-in-m1.md"


def _module_of(function: object) -> ModuleType | None:
    """The module a step's callable was defined in, or ``None`` if it cannot say.

    **Partials are unwrapped, and the reason is a measurement that refuted the
    obvious guess.** A ``functools.partial`` does not lack ``__module__``: the
    attribute resolves on its *type*, so a wrapped probe reports ``functools``
    and the module actually holding the probe's code drops out of the population
    without anything looking empty. Following ``.func`` is what keeps the sweep
    pointed at the code rather than at the standard library.

    Anything else that cannot name a module returns ``None``, and the caller
    refuses it rather than quietly narrowing the population.
    """
    target = function
    while isinstance(target, functools.partial):
        target = target.func
    name = getattr(target, "__module__", None)
    return sys.modules.get(name) if isinstance(name, str) else None


def _swept_modules() -> tuple[ModuleType, ...]:
    """The modules a filesystem-type probe would land in, derived from the symbols.

    Not a hand-maintained tuple. The first version of this module named
    ``domain.setup`` and ``application.setup_steps`` directly, and a hand-written
    list cannot honour the property the comment claimed for it: that a module
    which *moves* takes the sweep with it. This walks from ``StepId`` to the
    module that defines it, and from each step's own ``probe`` and ``apply`` to
    theirs, so relocating the registry or the step table moves the swept set with
    the code rather than leaving the sweep reading a file nobody edits any more.

    Sorted by name so the population, and any failure that reports it, is
    deterministic rather than set-ordered.
    """
    modules = {sys.modules[StepId.__module__]}
    for step in STEPS:
        for function in (step.probe, step.apply):
            if (module := _module_of(function)) is not None:
                modules.add(module)
    return tuple(sorted(modules, key=lambda module: module.__name__))


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
#:
#: **Every identifier-shaped token is word-bounded**, because without ``\b`` the
#: bare ``f_type`` fires inside ``conf_type`` and ``perf_type``, and ``nfs``
#: inside ``unfstype``. Those are false REDs on code that has nothing to do with
#: filesystems, and a pin that cries wolf on ordinary identifiers is one the next
#: author deletes rather than reads. ``/proc/mounts`` is a path rather than a
#: word, so only its tail is anchored.
#:
#: The boundaries are Python :mod:`re` semantics. This list is deliberately not
#: published anywhere as a shell search string: ``\b`` is not portable across
#: every ``grep`` a reader might paste it into, and a documented key that
#: silently means something else in the shell is worse than no key.
_FILESYSTEM_TYPE_API: Final = re.compile(
    r"\bstatvfs\b|\bf_fstypename\b|\bf_type\b|\bfstype\b|\bdisk_partitions\b"
    r"|\bgetmntent\b|/proc/mounts\b|\bmountpoint\b|\bnfs\b",
    re.IGNORECASE,
)

#: A finite-verb claim that something detects or warns: ``warns``, ``will warn``,
#: ``detects``, ``will detect``. These are **the forms this claim has actually
#: used**, not a characterisation of the ways it could return.
#:
#: Seven rephrasings were measured escaping it -- ``warned``, ``would warn``,
#: ``shall warn``, ``is warning``, ``reports``, ``is warned by``, ``flags``. They
#: are recorded rather than chased: a rule that pins grammar always has a next
#: grammar, and widening this list is the same defect one conjugation further
#: out. What it is, and all it is, is a regression pin over the wording the
#: retracted sentence took.
#:
#: The bare infinitive is left out on purpose: the corrected bullet says "for
#: want of a portable **detection** design", and a pin that fires on the noun
#: would punish the wording that states the absence.
_DETECTION_CLAIM: Final = re.compile(r"\b(?:warns|detects|will\s+warn|will\s+detect)\b")

#: Words that turn a detection claim into the sentence this module wants. Taken
#: from ``test_setup_claims.py``, along with its recorded weakness: ``nothing``
#: counts as a denial, so the rule is weakest exactly where the claim is most
#: likely to return.
_DENIAL: Final = re.compile(r"\b(?:not|never|cannot|no|nothing|neither|nor)\b")

#: The end of a sentence, which is not every period: the bullet closes on a
#: Markdown link, and ``https://github.com/...`` carries dots that end nothing.
#: The same trap the ADR-0013 module records, met again here.
_SENTENCE_END: Final = re.compile(r"\.(?=\s|$)")

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
    """Every "X warns/detects" in an NFS paragraph that its own sentence does not deny.

    "nothing detects that it is" is the sentence the ADR is supposed to contain,
    so a claim denied within its own sentence is exactly right. What is left over
    is a sentence telling a reader that something reports an NFS directory.

    **The denial must be in the claim's own sentence, before the verb.** An
    earlier version looked back a fixed six words, and a window that crosses a
    sentence boundary lets a re-added claim borrow the denial of the sentence in
    front of it: appending "`doctor` warns about it." after the bullet's own
    "...is therefore told nothing by `doctor`." was measured GREEN, as was one
    other of four attachment points. A sentence is the unit the denial actually
    governs, so it is the unit the rule uses.
    """
    claims: list[str] = []
    for paragraph in _nfs_paragraphs(text):
        for sentence in _SENTENCE_END.split(paragraph):
            for match in _DETECTION_CLAIM.finditer(sentence):
                if not _DENIAL.search(sentence[: match.start()]):
                    claims.append(sentence.strip())
                    break
    return claims


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

    The tracking reference is asserted in the same paragraph as the first
    statement of the absence, because an exclusion enforced by nothing is a
    decision that has to stay findable.

    **At least once, not exactly once.** An earlier version required a single
    paragraph and would have failed on a document that restated the absence in,
    say, a compliance section -- reporting "the correction is gone" about a file
    that had just made the point twice. Saying a true thing twice is not the
    drift this module exists to catch; the negative half below is what refuses
    the false version.
    """
    paragraphs = [
        p for p in _nfs_paragraphs(ADR_0018.read_text(encoding="utf-8")) if NOTHING_DETECTS in p
    ]

    assert paragraphs, "ADR-0018 no longer states that nothing detects a project directory on NFS"
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

    **The samples are real API shapes.** An earlier version paired
    ``os.statvfs(path)`` with ``.f_fstypename``, which that call does not expose
    -- ``statvfs`` returns block and inode counts, while ``f_fstypename`` belongs
    to the BSD ``statfs``. The sample was fiction, and a synthetic sample is
    exactly the thing a future reader copies when they write the probe this
    module is watching for.
    """
    plain = "import os\n\ndef probe(path):\n    return os.statvfs(path).f_blocks\n"
    nested_mixed_case = (
        "class Probe:\n"
        "    def read(self):\n"
        "        return [p for p in psutil.disk_partitions() if p.fstype.upper() == 'NFS']\n"
    )

    assert _filesystem_type_apis(plain), "the sweep no longer matches a plain `statvfs` call"
    assert _filesystem_type_apis(nested_mixed_case), (
        "the sweep no longer matches a mixed-case, nested filesystem-type read"
    )


def test_the_filesystem_api_sweep_ignores_identifiers_that_merely_contain_a_token() -> None:
    """RED means the sweep fires on ordinary code, and gets deleted for crying wolf.

    Without word boundaries ``f_type`` matches inside ``conf_type`` and
    ``perf_type``, and ``nfs`` inside ``unfstype``. None of those has anything to
    do with a filesystem, and a false RED on unrelated code is not a harmless
    over-approximation: it teaches whoever meets it that this pin is noise, and
    the next person deletes it rather than reading what it was for.
    """
    unrelated = "conf_type = 1\nperf_type = 2\nunfstype = 3\nmanifest_type = 4\n"

    assert not _filesystem_type_apis(unrelated), (
        "the sweep fires on identifiers that merely contain one of its tokens"
    )


def test_no_setup_step_source_reads_a_filesystem_type() -> None:
    """RED means a probe landed -- and ADR-0018 must stop saying nothing detects NFS.

    The fact half of the pin. It is a source-text search, because the claim is
    about an absence and there is no call whose return value could report one.

    **The population is asserted before it is swept, and that ordering is the
    finding this shape exists to prevent.** An earlier version checked that each
    module had source *inside* the loop over the population, which asserts
    nothing at all when the population is empty: emptying the swept set while a
    real ``statvfs`` probe sat in ``setup_steps`` left all four tests green. A
    search over no files reports the same "no probe found" as a search over clean
    ones, so the count of what is being searched has to be established first.
    """
    assert set(StepId), "the step registry is empty; this sweep would have nothing to be about"
    assert {step.step_id for step in STEPS} == set(StepId), (
        "STEPS no longer covers the StepId registry, so a probe could be registered "
        "in the gap without this sweep or the ADR's claim noticing"
    )

    foreign = sorted(
        step.step_id.value
        for step in STEPS
        if (module := _module_of(step.probe)) is None or not module.__name__.startswith("theurian.")
    )
    assert not foreign, (
        f"these steps' probes do not resolve to a module inside the package, so the "
        f"code behind them is not swept and a probe there would go unseen: {foreign}"
    )

    swept = _swept_modules()
    assert swept, (
        "the sweep population is empty, so the search below would pass over nothing "
        "and report a landed probe as an absence"
    )
    sources = {
        module.__name__: pathlib.Path(module.__file__ or "").read_text(encoding="utf-8")
        for module in swept
    }
    empty = sorted(name for name, text in sources.items() if not text.strip())
    assert not empty, f"swept modules have no source to search: {empty}"

    found = {
        name: sorted(set(apis))
        for name, text in sources.items()
        if (apis := _filesystem_type_apis(text))
    }

    assert not found, (
        f"a setup step now reads a filesystem type: {found}. ADR-0018's Consequences "
        f"bullet must stop saying nothing detects a project directory on NFS"
    )
