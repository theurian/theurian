"""What ADR-0018 claims about its own mechanisms, against what the code does.

**Four corrected claims are held here, and they are independent.** The
Consequences bullet said ``doctor`` warns about NFS; the Decision said the write
lock is taken on the state database; the Compliance section pointed its owed
single-writer work at a closed issue; the Milestone 5 amendment said the port
publishes *twelve* write methods. All four were false, all four were corrected in
place, and each gets a prose pin and a fact pin below. They share this module
because they share a document and a failure mode -- a durable record describing a
mechanism nobody re-read against the code -- not because one implies another.

The fourth is the one whose fact side is an equality rather than a search, and it
is written up with claim 3 below because the same #436 repoint carried both.

-- 1. The NFS warning (#417) ------------------------------------------------

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

**Two records state this exclusion, and both prose halves read both of them.**
ADR-0018's Consequences bullet is where the decision lives; ``README.md``'s quick
start carries one line of it where an operator meets ``.theurian/`` for the first
time, citing the ADR rather than re-asserting the absence on its own authority
(https://github.com/theurian/theurian/issues/417). A copy nobody reads is a copy
that drifts -- the exact defect #433 fixed in a different pair of records, where
one document was corrected and its restatement left standing -- so
:data:`NFS_RECORDS` is the population of both halves and neither file can soften
alone.

**The two records are read on different keys, and the asymmetry is measured.**
ADR-0018 is swept on ``nfs`` *or* ``doctor``, because ``doctor`` is the command
the retracted sentence attached the phantom mitigation to and a re-added claim
could name it without naming the filesystem. README is swept on ``nfs`` alone:
it is not where the claim lived, its one line of the exclusion names NFS, and a
key that also selected ``doctor`` turned every true sentence about that command
into a false RED -- "``theurian doctor`` warns when the daemon is not running"
read as the mitigation returning. Both directions are held by
:func:`test_readme_is_not_swept_for_true_sentences_about_doctor`, so the
narrowing cannot travel to the ADR.

**Fenced code blocks are outside every prose population here.** README's quick
start runs ``theurian doctor`` inside an ``sh`` block, and until #446's first
review round that block was a member of the NFS population -- a shell transcript
in a scan whose rule is about what a *sentence* claims. Nothing in a command
sample can state or retract the exclusion, so :func:`_without_code_fences`
removes them before any key is applied.

**The corpus twin stays excluded, and its reason does not reach README.**
``.theurian/knowledge/architecture/single-writer-synchronous-in-m1.<ulid>.md`` is
held byte-identical to its source anchor commit by
``test_dogfood_corpus_governance.py::test_every_pinned_body_is_byte_identical_to_its_source_anchor_commit``,
so it still carries ``doctor`` "will warn about it" and only a governed re-seed
(#199 unit C) can move it: a scan that reached it would report the governance
guard doing its job as drift. ``README.md`` is under no such freeze, so nothing
about the twin argues for leaving it unread.

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

-- 2. What the write lock is taken on (#424) --------------------------------

Decision point 2 said Milestone 1 enforces exclusivity with an OS advisory file
lock **on the state database**. It never was: ``ProjectPaths.write_lock`` is
``.theurian/runtime/write.lock`` and ``ProjectPaths.database_for`` puts databases
under ``.theurian/state/``, so ``write_transaction(database_path, lock_path)``
flocks a file that is not a database. Exclusivity held throughout -- only the
object the record named was wrong -- so the clause was corrected in place.

This one is worth pinning for a reason the NFS claim did not have: **the document
disagreed with itself for a whole milestone.** The Milestone 5 amendment re-read
point 2 and called the mechanism "real and works", having checked that a lock is
taken and not what it is taken on, while the Negative consequence below it had
named both paths correctly since #420. A record that contradicts itself is read
by whichever half the reader reaches first.

The fact pin here is a genuine derivation rather than a search: the paths come
from a real ``ProjectPaths`` built on a throwaway root -- no fake, because
``ProjectPaths.of`` resolves a root that need not exist -- and the literals the
ADR names are asserted *equal* to what the code derives. That is the
``test_setup_claims.py`` shape: two independently written strings held equal, so
the pin fails when either side moves rather than being green for whatever the
code happens to say.

**That derivation lives in ``write_lock_claims.py``, not here, because a second
record names the same objects.** ADR-0027's decision-2 residue restates this
clause and carried the same retracted wording until
https://github.com/theurian/theurian/issues/433 corrected it; its pin is
``test_adr_0027_claims.py``. Both modules import ``LOCK_PATH``, ``STATE_DIR``
and :func:`find_lock_on_database` from that helper and both call the one
assertion function, so a lock that moves fails both records together instead of
failing whichever module its author remembered.

**What that fact pin enforces, and what it does not.** It holds where the lock
and the databases *resolve to*, and that they resolve apart:

- Move the lock under ``state/``, rename it to a database filename, or collapse
  ``runtime`` and ``state`` into one directory, and the fact pin goes RED and the
  Decision must move with it.
- It does **not** prove a lock is taken, or taken on that file. Whether
  ``write_transaction`` flocks ``lock_path`` rather than the database is a
  property of ``infrastructure/sqlite/connection.py``, held by its own tests, not
  by path arithmetic here. This module would stay green against a build that
  computed the right lock path and then never used it -- so the claim it holds is
  *where the lock file is*, not *that the lock works*.
- The prose half refuses the retracted attachment in the form it has actually
  taken -- a lock "on the state database". Measured escapes it does not catch:
  "on the SQLite file", "on the state db", "against the database", and
  "database-level lock". Recorded rather than chased, for the same reason as
  every other grammar pin in this file.

**One ADR file for this claim, and the corpus twin is deliberately not part of
the prose scan.** ``README.md`` names the same lock file in its NFS paragraph
("an OS advisory lock on ``.theurian/runtime/write.lock``") and attaches it to
nothing, so it is a third live record of *this* claim that no half below reads.
That is a gap rather than a decision, recorded here and not closed: the scope
this pin was written to is ADR-0018's Decision point 2 and ADR-0027's restatement
of it.
``.theurian/knowledge/architecture/single-writer-synchronous-in-m1.<ulid>.md``
still carries the retracted sentence byte-identically. That is not drift: the
dogfood corpus is held byte-identical to its source anchor commit by
``test_dogfood_corpus_governance.py::test_every_pinned_body_is_byte_identical_to_its_source_anchor_commit``,
so only a governed re-seed can move it -- tracked as #199 unit C. A repo-wide
walker over this wording would go RED on that file on the day it was written.
Recorded here because a reader who greps the tree for the old sentence finds it
and needs to know why it stays.

-- 3. Who owns the owed single-writer work (#436) ----------------------------

ADR-0018 cited https://github.com/theurian/theurian/issues/15 three times as the
Milestone-6 owner of work it records as owed. #15 closed on 2026-08-10
(``66a43ae``) by wiring ADR-0024 decision 5 -- the withdrawal-to-purge trigger --
which is none of the three, and Milestone 6 is past. A durable record pointing
owed work at a closed tracker fails the same way as one citing a control that
does not exist: a reader follows the pointer, finds it closed, and concludes the
work shipped.

**Two of the three cites are held here, and the third must not be.** The
Milestone-5 amendment's sentence stays verbatim under a dated repoint note,
because that blockquote is a record of what was believed then; so do the
correction notes, which quote ``#15`` in order to retract it. A file-wide scan
would read every one of those as the defect returning -- the trap this module
already meets inside a single file over :func:`_decision_point_two`. So the pins
read two Compliance bullets, isolated by :func:`_corrected_bullet` and keyed on
the claim each bullet opens with rather than on the pointer it carries:

- ``nothing holds point 1`` -- the ``CanonicalStore`` single write interface.
- ``the derived index has no single-writer contract at all`` -- the index's own
  contract.

**A third repointed bullet is outside this pin, and that is a gap rather than a
decision.** "Nothing runs two writers at once" gained #439 in the same commit but
never cited #15, so neither half below reads it and reverting that one bullet
alone leaves this module green. Recorded so the next reader does not take "the
corrected bullets" to mean "every bullet the repoint touched".

**What the prose halves enforce.**

- Each bullet still names :data:`LIVE_OWNER` -- the link, not a bare ``#439``, so
  the owner stays findable. Same rule and same reason as :data:`TRACKED_BY` on
  the NFS exclusion.
- No mention of the closed tracker inside those bullets stands unretracted. The
  discriminator is sentence-level and mirrors
  :func:`_detection_claims_without_denial`: a ``#15`` whose own sentence marks it
  as history -- ``closed``, ``named``, ``until``, ``no longer``, ``passed``,
  ``repointed`` -- is the correction working, and one with no such marker is an
  owner-form cite. Measured escapes, recorded rather than chased: "tracked in
  #15, which was named the owner in Milestone 6" and "#15 owns this; it was
  closed and reopened" each carry a marker and pass. This is a regression pin
  over the form the dead pointer actually took -- ``Milestone 6, with #15`` and
  ``(Milestone 6, #15)`` -- not a characterisation of every way one could return.

**What the fact half enforces, which is one of the two conditions the index
bullet turns on.** That bullet asserts both that what the purge writes through
"is still not an interface" and that no index write lock exists.
:func:`test_no_index_write_path_module_takes_a_lock` holds the lock half: a
source-text search for ``flock``, ``lockf`` and ``LOCK_EX`` over the modules the
published index is written through, derived from the symbols rather than named by
hand, so relocating a writer or the pointer swap moves the sweep with the code.

**That population covers both writers as of #446, and covered one before it.**
The sweep started at the withdrawal purge alone, which left
``application/index_builder.py`` and ``cli/index_commands.py`` -- the primary
build path, and the one the bullet's own first sentence names -- outside a set
whose stated population was "the modules the published index is written
through". Demonstrated in both directions rather than argued: a ``fcntl.flock``
planted in ``index_builder.py`` left the whole suite green, while the same lock
in the swept purge path was caught. Both writers are in the population now, and
:data:`REQUIRED_INDEX_WRITERS` is asserted before the search runs so an inlining
or a rename cannot drop one silently.

**What is still outside it, measured rather than guessed** (2026-08-31, at
``42befc6``):

- ``application/forest_builder.py`` derives summary nodes in memory and returns
  them; it opens no connection, executes no statement and touches no
  ``IndexStore``, so there is no write for a lock to guard.
- ``infrastructure/sqlite/index_schema.py`` is DDL text. Its statements are
  executed by ``index_store.py``, which *is* swept.
- ``infrastructure/sqlite/index_forest.py`` builds a read statement and walks a
  path; ``index_query.py`` and ``index_scan.py`` are the retrieval side.

So the reach of the claim is: the day an index write lock lands in either
writer, in the adapter that writes the rows, or in the pointer swap, this goes
RED and the bullet must move with it. A lock added in a module the derivation
reaches only through an import is still unseen -- that limit is stated again
below and has not changed.

``write_lock`` is dropped from ADR-0018's own pasted grep key, and that is not a
narrowing for convenience: ``ProjectPaths.write_lock`` is the *canonical* store's
lock path property, declared in one of the swept modules, so keeping the token
would make this sweep RED today against a package that takes no index lock at
all.

The other condition -- that no single write interface has appeared on the
``CanonicalStore`` port -- is already pinned on ``main`` by
``test_connection_claims.py::test_the_canonical_store_port_declares_no_single_write_interface``
and its ``..._publishes_more_than_one_write_method`` complement, both of which
read the live Protocol. It is cited rather than duplicated here: a second copy of
that derivation is the failure mode ``write_lock_claims.py`` exists to prevent.

**The tracker facts have no fact side at all.** That #15 is closed, that #439 is
open and carries no milestone, and that "twelve" was the figure written at
``f665ecf`` are properties of a tracker and of history, not of this source tree,
so nothing here can read them -- the same disclaimer the "no probe is planned"
half carries above. The prose alone holds them.

**The amendment's count is the one number here that does have a fact side.**
ADR-0018 says, in the present tense, that the port "publishes its thirteen write
methods directly", and
:func:`test_the_amendment_spells_the_write_method_count_the_port_publishes`
reads that word out of the record and asserts it equals ``len(write_methods())``
derived from the live ``CanonicalStore``. Two independently written things held
equal, so it fails whichever side moves. Until #446's first review round the
correction beside that sentence pointed a reader at a *failure message*, which
renders only on failure: reverting the word to *twelve* and adding a fourteenth
write method were both measured green.

The derivation is imported from ``canonical_store_surface.py`` rather than
repeated. ``test_connection_claims.py`` reads the same function for its own two
port assertions, so a write method that arrives or leaves fails both records
together -- ``write_lock_claims.py``'s reasoning, applied to a second derivation.

The neighbouring figures are deliberately **not** held to the port: the
correction note reports what the port published at ``261eff3`` and ``f665ecf``,
and quotes the *twelve* it retracted. Those are measurements of named moments,
which is the one form of a written number this file set accepts.

**Three correction blockquotes are presence-pinned, because deleting one is how
a corrected claim comes back for free.** Every bullet scan above is keyed on what
a bullet *asserts*, while a correction note carries the *retraction* -- so
removing a note leaves the bullet head's ``(owed, #439)`` satisfying
:data:`LIVE_OWNER` and the closed-tracker scan passing over a bullet that
retracts nothing. #446's first review round measured all three deletions green.
:data:`CORRECTION_NOTES` names what each one's removal resurrects, and the
point-1 bullet's re-measurement paragraph -- prose rather than a blockquote, and
free to delete for exactly the same reason -- is held by
:func:`test_the_point_1_bullet_keeps_the_re_measurement_that_narrows_its_own_claim`.
Which blockquotes are outside that set, and why, is recorded on
:data:`_UNPINNED_BLOCKQUOTES`.

**The lock sweep's reach, stated as narrowly as the filesystem one's.** It reads
whole module source, so a lock taken in a helper those modules merely import is
not seen, and a lock taken under any other API -- ``portalocker``,
``msvcrt.locking``, an advisory row in SQLite -- escapes the token list. It reads
``application/project_service.py`` because that is where the pointer swap is
published, which means a lock added there *for the canonical store* would fire
this pin: a false RED whose remedy is to narrow the population, recorded rather
than pre-empted.

**No prose half in this module is a closure argument.** They are regression pins
over the wording each claim has actually taken, and a rule that pins grammar
always has a next grammar.
"""

from __future__ import annotations

import functools
import pathlib
import re
import sys
from types import ModuleType
from typing import Final

from canonical_store_surface import write_methods
from write_lock_claims import (
    LOCK_PATH,
    REPO_ROOT,
    STATE_DIR,
    assert_the_lock_and_the_state_databases_resolve_apart,
    collapsed,
    find_lock_on_database,
)

from theurian.application.index_builder import IndexBuilder
from theurian.application.project_service import write_active_index_pointer
from theurian.application.setup_steps import STEPS
from theurian.application.withdrawal_purge import publish_purge_for_withdrawal
from theurian.cli.index_commands import _publish
from theurian.domain.ports.index_store import IndexStore
from theurian.domain.setup import StepId
from theurian.infrastructure.sqlite.index_purge import purge_into
from theurian.infrastructure.sqlite.index_store import SqliteIndexStore

ADR_0018 = REPO_ROOT / "docs" / "adr" / "0018-single-writer-synchronous-in-m1.md"
README = REPO_ROOT / "README.md"

#: The paragraphs of ADR-0018 this module reads at all. A detection verb
#: elsewhere in that file is about locking or migrations, not about this bullet.
#:
#: ``doctor`` is part of the key **there** because that is the command the
#: retracted sentence attached the phantom mitigation to -- "``doctor`` will warn
#: about it" -- so a re-added claim could name the command without naming the
#: filesystem and would still have to be caught.
_NFS_OR_DOCTOR: Final = re.compile(r"\bnfs\b|\bdoctor\b")

#: The key README is read on, and it is narrower for a measured reason. README is
#: not where the retracted claim lived: it carries one operator-facing line of the
#: exclusion, and that line names NFS. Keying it on ``doctor`` as well puts every
#: true sentence about that command into a population whose rule refuses an
#: undenied ``warns`` or ``detects`` -- so "``theurian doctor`` warns when the
#: daemon is not running", a sentence this README is entitled to write, went RED
#: as the phantom NFS mitigation returning. That false RED is what #446's first
#: review round reported, and a pin that fires on an unrelated true sentence is
#: one the next author deletes rather than reads.
#:
#: Measured 2026-08-31: README's only ``doctor`` mention is the comment inside the
#: quick start's ``sh`` block, which :func:`_without_code_fences` removes from the
#: population anyway, so the narrowing loses no paragraph this scan was reading.
_NFS_ONLY: Final = re.compile(r"\bnfs\b")

#: Every live record that states the NFS exclusion, and therefore every record
#: that can drift back, mapped to the key each one is read on. The ADR is where
#: the decision lives; README carries one line of it where an operator meets
#: ``.theurian/`` for the first time. Both prose halves below sweep this mapping
#: rather than one file, because a copy nobody reads is a copy that drifts. The
#: governed corpus twin is deliberately absent -- see the module docstring for
#: the reason, and for why that reason does not reach README.
#:
#: The keys are :data:`_NFS_OR_DOCTOR` and :data:`_NFS_ONLY`, and they differ for
#: the reason recorded on the second: only one of these two records is where the
#: phantom ``doctor`` warning lived.
NFS_RECORDS: Final = {ADR_0018: _NFS_OR_DOCTOR, README: _NFS_ONLY}


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
#: two lines the file wraps it onto. Compared after :func:`collapsed`: the claim
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

#: The spelled numbers the amendment's correction note could carry, mapped to
#: what they mean. Spelled rather than digits because that is how the record
#: writes it -- "it has published thirteen since ``261eff3``" -- and a pin that
#: read digits would pass over the sentence it exists to hold.
#:
#: The range brackets the live figure on both sides with room to move. A word
#: outside it fails loudly rather than being skipped: a note that starts spelling
#: the count some other way has stopped being the sentence this pin reads, and
#: that is a fact about the record its author has to hear.
_SPELLED_NUMBERS: Final = {
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
    "thirteen": 13,
    "fourteen": 14,
    "fifteen": 15,
    "sixteen": 16,
    "seventeen": 17,
    "eighteen": 18,
    "nineteen": 19,
    "twenty": 20,
}

#: The amendment note that carries the count, and the only sentence in ADR-0018
#: asserting it in the **present tense**. Its neighbours each name a number too
#: and neither is this claim: the correction note below it reports what the port
#: published at two named commits, which is a measurement of a moment, and the
#: retraction inside that note quotes the figure it removed. Anchoring on this
#: note is what keeps the pin pointed at the sentence a reader takes as current.
AMENDMENT_COUNT_NOTE: Final = "the port publishes its"

#: How that sentence states the count, as the phrase rather than as a bare number
#: word. A key matching any spelled number in the document would read the
#: retraction -- "the count above said *twelve*" -- as the claim.
#:
#: The caller asserts exactly one match, so a rewrite that made the key ambiguous
#: fails naming both rather than silently reading the first.
_SPELLED_COUNT: Final = re.compile(r"\bpublishes its ([a-z]+) write methods\b")

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

#: A fenced code block's delimiter, allowing the leading whitespace Markdown
#: allows. Used to take command samples out of every prose population below.
_CODE_FENCE: Final = re.compile(r"\s*```")

#: A line that begins a new block rather than continuing the one above it.
#: Copied from ``test_setup_claims.py``, whose docstring records why a scan that
#: stops at every newline and a scan that ignores newlines are both wrong.
_BLOCK_START: Final = re.compile(r"\s*(?:#{1,6}\s|[-*+]\s|\d+\.\s|\||```|---\s*$|>\s)")

#: The heading of the section that lists what ADR-0018 still owes, **anchored to
#: the start of a line**. The two bullets claim 3 reads live under it, and slicing
#: to it first is what keeps the scans off the Decision's amendment -- which cites
#: ``#15`` verbatim and is meant to.
#:
#: The leading newline is the anchor a Markdown heading actually has, and without
#: it the slice is a substring search that two ordinary edits move: a deeper
#: ``### Compliance`` subheading *contains* the bare string, and so does a
#: sentence that names the section in prose. Either one earlier in the file
#: silently starts the slice above the real section, widening every scan below it
#: without failing -- which is the same silent-narrowing shape the two sweeps in
#: this module assert their populations against.
_COMPLIANCE_HEADING: Final = "\n## Compliance"

#: The two "Still owed" bullets whose ``#15`` cite was corrected in place, keyed
#: on the claim each opens with. Keyed that way on purpose: a key built from the
#: pointer would stop matching the moment the pointer moved, which is the change
#: these pins exist to observe, and the bullet would drop out of the population
#: rather than fail.
POINT_1_BULLET: Final = "nothing holds point 1"
INDEX_CONTRACT_BULLET: Final = "the derived index has no single-writer contract at all"
CORRECTED_BULLETS: Final = (POINT_1_BULLET, INDEX_CONTRACT_BULLET)

#: The live owner of the owed single-writer work, as the link rather than the
#: bare ``#439``. Same rule as :data:`TRACKED_BY`: owed work whose owner is not
#: reachable from the record is work nobody can find again.
LIVE_OWNER: Final = "issues/439"

#: The correction blockquotes this module's claims rest on, keyed by an anchor
#: from each one's own load-bearing sentence and mapped to what deleting it would
#: silently undo. Every value below is a claim that goes back to being *true in
#: the record and false in the code* the moment its note is gone.
#:
#: They are presence-pinned because #446's first review round measured that all
#: three could be deleted with every other pin in this module green. The bullet
#: scans are the reason: they are keyed on what a bullet *asserts*, and a
#: correction note carries the retraction rather than the assertion, so the
#: bullet head's ``(owed, #439)`` alone satisfies :data:`LIVE_OWNER` and the
#: closed-tracker scan passes over a bullet that no longer retracts anything.
#:
#: The anchor is a sentence fragment rather than a date. A note that is
#: re-measured later gets a new date and must not go RED for it; a note that
#: stops saying the thing it exists to say must.
CORRECTION_NOTES: Final = {
    "the sentence above names a tracker that is closed": (
        "the Milestone-5 amendment's standing `#15` cite loses its retraction, so the "
        "ADR hands owed work to a closed tracker with nothing marking it dead"
    ),
    "the count above said": (
        "the amendment's write-method count loses the record that it was wrong when "
        "written rather than stale, and `twelve` reads as a figure that merely aged"
    ),
    "the index's only writer no longer holds": (
        "`theurian index build is today its only writer` is resurrected, in a section "
        "whose own paragraph predicts the second writer that has since landed"
    ),
}

#: Two blockquotes in ADR-0018 are deliberately **not** in the mapping above, and
#: neither is an oversight:
#:
#: - the blue/green note under the index bullet, which records a decision owned by
#:   ADR-0024 rather than correcting a claim this module holds;
#: - the Neutral point's Milestone 5 amendment about NFR-4, which is ADR-0022's
#:   subject and is cited from there.
#:
#: The #424 note that corrected Decision point 2 is also absent, and that one is
#: a judgement rather than a category: its deletion removes provenance but
#: resurrects nothing, because point 2 was corrected *in place* and is held in
#: both directions by
#: :func:`test_adr_0018_says_the_write_lock_is_a_separate_file_and_names_it` and
#: :func:`test_adr_0018_does_not_reattach_the_write_lock_to_a_database`.
_UNPINNED_BLOCKQUOTES: Final = "recorded above, not enumerated in code"

#: The two things the point-1 bullet's re-measurement has to keep saying. Not
#: blockquotes -- they are paragraphs *inside* the bullet, so
#: :func:`_corrected_bullet` already returns them -- but deletable in exactly the
#: same way and for the same reason: nothing below keys on them.
#:
#: What their deletion would leave is the falsified Milestone-5 sentence standing
#: alone: "adding a ``connection()`` method left ``test_ports.py`` and the whole
#: suite green, so the escape hatch could be added and nothing noticed." Two of
#: the three spellings are RED now, so that sentence needs the paragraph that
#: narrows it or it is a measurement the document has already refuted.
#:
#: Anchored on the marker and on the residual rather than on the date, so a later
#: re-measurement may move the date without going RED, and may not quietly drop
#: the residual it found.
POINT_1_REMEASUREMENT: Final = ("re-measured", "`-> object` is the residual")

#: The closed tracker the bullets used to name, in both the forms ADR-0018 writes
#: it -- the Markdown label ``[#15]`` and the bare ``#15`` -- and the link target
#: ``issues/15``, which is how a cite that drops the label would still read.
#: Word-bounded so ``#150`` and ``issues/150`` are not swept in.
#:
#: **A third escape family, measured 2026-08-31 and recorded rather than
#: chased.** The two already recorded on :data:`_RETRACTED` are about the
#: *sentence* around a cite; this one is about the cite's own spelling, and a
#: reference that names the tracker without either token escapes the scan
#: entirely -- ``tracked in issue 15``, ``tracked in GH-15``, ``github issue
#: number 15`` and a reference-style link (``[the interface tracker][iface]``,
#: whose definition carries ``issues/15`` in a different paragraph, outside the
#: bullet :func:`_corrected_bullet` scopes this scan to) were each measured
#: passing. Widening the token list is the same defect one spelling further out:
#: ``\b15\b`` alone would fire on a date, a count and a line number. What this is,
#: and all it is, is a regression pin over the two forms the dead pointer actually
#: took in this file.
_CLOSED_TRACKER: Final = re.compile(r"#15\b|issues/15\b")

#: Words that mark a mention of :data:`_CLOSED_TRACKER` as history rather than as
#: an owner. The mirror of :data:`_DENIAL`, and it carries the same recorded
#: weakness: these are the forms the retraction has actually taken, not a
#: characterisation of every sentence that could carry one, and a re-acquired cite
#: that happens to contain one of them escapes.
#:
#: The marker is required in the cite's own sentence and may sit on either side of
#: it -- ADR-0018 writes both "the tracker it named, #15, closed on ..." and
#: "this bullet named Milestone 6 and #15 until ...". A before-the-cite rule, the
#: one :func:`_detection_claims_without_denial` needs, would refuse the first of
#: those two on punctuation.
_RETRACTED: Final = re.compile(r"\b(?:closed|named|until|no longer|passed|repointed)\b")

#: The Milestone-5 amendment's standing cite of the closed tracker, which the
#: bullet scans must never reach: that sentence is a dated record of what was
#: believed in Milestone 5 and is left verbatim on purpose. Asserted absent from
#: every extracted bullet, so a slice that widens fails at the premise instead of
#: reporting the standing record as a defect.
_AMENDMENT_STANDING_CITE: Final = "tracked with the index writer in"

#: The lock APIs an index write lock would be taken through: ADR-0018's own
#: pasted grep key minus ``write_lock``. That token is dropped because
#: ``ProjectPaths.write_lock`` -- the *canonical* store's lock path -- is declared
#: in one of the swept modules, so keeping it would make the sweep RED against a
#: package that takes no index lock at all.
#:
#: Built from a token tuple rather than written as one alternation, because
#: ``r"\blockf\b"`` reads as the word "blockf" to everyone who meets it and the
#: next author to widen this list should not have to parse that.
_LOCK_API_TOKENS: Final = ("flock", "lockf", "LOCK_EX")

#: Matched case-insensitively and word-bounded, for the reason
#: :data:`_FILESYSTEM_TYPE_API` records: a pin that fires on ordinary identifiers
#: is one the next author deletes rather than reads.
_LOCK_API: Final = re.compile(
    "|".join(rf"\b{re.escape(token)}\b" for token in _LOCK_API_TOKENS), re.IGNORECASE
)

#: The modules :func:`_index_write_path_modules` must still resolve to, asserted
#: before the lock sweep runs. One per way a published index comes into
#: existence, so a rename that drops one fails naming it instead of narrowing the
#: search:
#:
#: - ``index_builder`` writes the rows of a new build (``theurian index build``).
#: - ``index_commands`` publishes that build's pointer. It is reached through a
#:   private helper, which is exactly why it is named here: inline ``_publish``
#:   into its caller and the symbol walk loses the module silently.
#: - ``withdrawal_purge`` is the second writer, and the module ADR-0018's index
#:   correction quotes its own "No new index-write lock is taken" from.
#:
#: The infrastructure modules the walk also resolves to are deliberately not
#: listed: they are reached through port methods that a rename would take with
#: them, and pinning every member of a derived population turns a premise into a
#: second hand-maintained list.
REQUIRED_INDEX_WRITERS: Final = (
    "theurian.application.index_builder",
    "theurian.application.withdrawal_purge",
    "theurian.cli.index_commands",
)


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

    return [flattened for block in blocks if (flattened := collapsed(" ".join(block)))]


def _without_code_fences(text: str) -> str:
    """*text* with every fenced code block removed, its delimiters included.

    A prose pin has no business reading a command sample. README's quick start
    runs ``theurian doctor`` inside an ``sh`` block, and until #446's first review
    round that block was a member of the NFS population -- a shell transcript in a
    scan whose rule is about what a sentence claims. Nothing there can state or
    retract the exclusion, and a rule that reads it can only fire on it wrongly.

    Removed rather than merely deprioritised, because the two failure directions
    are not symmetric: a scan that reads a code block can go RED on a command name
    that means nothing, while a scan that skips one loses no sentence a reader
    would take as a claim.

    An unterminated fence would swallow the rest of the document. That fails at
    the premise rather than passing quietly: every caller asserts its population
    is non-empty before searching it, so a record whose paragraphs all vanished
    reports "this record has no paragraph mentioning NFS" instead of "no claim
    found".
    """
    kept: list[str] = []
    inside = False
    for line in text.splitlines():
        if _CODE_FENCE.match(line):
            inside = not inside
            continue
        if not inside:
            kept.append(line)
    return "\n".join(kept)


def _nfs_paragraphs(text: str, key: re.Pattern[str]) -> list[str]:
    """The prose paragraphs of one record that its own *key* selects.

    The key is per-record rather than shared, and :data:`_NFS_ONLY` records the
    measurement behind that. Code blocks are out of the population before the key
    is applied at all.
    """
    return [
        paragraph for paragraph in _paragraphs(_without_code_fences(text)) if key.search(paragraph)
    ]


def _detection_claims_without_denial(paragraphs: list[str]) -> list[str]:
    """Every "X warns/detects" in an NFS paragraph that its own sentence does not deny.

    "nothing detects that it is" is the sentence both records are supposed to
    contain, so a claim denied within its own sentence is exactly right. What is
    left over is a sentence telling a reader that something reports an NFS
    directory.

    **The denial must be in the claim's own sentence, before the verb.** An
    earlier version looked back a fixed six words, and a window that crosses a
    sentence boundary lets a re-added claim borrow the denial of the sentence in
    front of it: appending "`doctor` warns about it." after the bullet's own
    "...is therefore told nothing by `doctor`." was measured GREEN, as was one
    other of four attachment points. A sentence is the unit the denial actually
    governs, so it is the unit the rule uses.

    Takes the paragraphs rather than the document, so the caller that asserts the
    scanned population is non-empty is holding the same list this reads. Handing
    in raw text again would let the premise and the scan disagree about what was
    swept, which is the shape the fact half's own history warns about.
    """
    claims: list[str] = []
    for paragraph in paragraphs:
        for sentence in _SENTENCE_END.split(paragraph):
            for match in _DETECTION_CLAIM.finditer(sentence):
                if not _DENIAL.search(sentence[: match.start()]):
                    claims.append(sentence.strip())
                    break
    return claims


def _filesystem_type_apis(text: str) -> list[str]:
    """Every filesystem-type API named in a piece of source text."""
    return _FILESYSTEM_TYPE_API.findall(text)


def _decision_point_two(text: str) -> str:
    """The Decision's second numbered point, as one collapsed paragraph.

    Isolated rather than scanned for across the document, because the correction
    note that *fixed* this claim quotes the retracted wording verbatim. A
    file-wide scan would read that quotation as the defect returning and go RED
    on the amendment -- the same trap the corpus twin sets for the NFS pin, met
    here inside a single file.

    ``_BLOCK_START`` treats a numbered item and a blockquote line as new blocks,
    so the point, the amendment and the correction note are separate paragraphs.
    """
    points = [
        paragraph
        for paragraph in _paragraphs(text)
        if paragraph.startswith("2.") and "enforces exclusivity" in paragraph
    ]

    assert len(points) == 1, f"Decision point 2 is not findable as one paragraph: {points}"
    return points[0]


def _compliance_section(text: str) -> str:
    """Everything from ADR-0018's Compliance heading to the end of the document.

    Sliced before any bullet is found, and that is the scoping decision claim 3
    turns on: the Decision's Milestone-5 amendment cites ``#15`` and is meant to,
    so a scan that could reach it would report a standing dated record as the
    defect returning.

    Compliance is the last section of this file, so the slice runs to the end
    unless a later ``## `` heading is added; that case is handled rather than
    assumed, because a slice that silently swallowed a new section would widen
    every scan below without failing.

    Both ends anchor on ``\\n``. The opening one did not until #446's first review
    round: a bare substring split would have started the slice at a
    ``### Compliance`` subheading or at a sentence naming the section, and
    :func:`test_the_compliance_slice_anchors_on_a_heading_and_not_a_mention` is
    what makes that difference observable.
    """
    assert _COMPLIANCE_HEADING in text, (
        f"ADR-0018 has no `{_COMPLIANCE_HEADING.strip()}` heading at the start of a "
        f"line, so the bullets claim 3 reads cannot be isolated and every scan over "
        f"them would pass over nothing"
    )
    rest = text.split(_COMPLIANCE_HEADING, 1)[1]
    following = rest.find("\n## ")
    return rest if following < 0 else rest[:following]


def _quoted_notes(text: str) -> list[str]:
    """Every paragraph inside a blockquote, one collapsed string each.

    Not :func:`_paragraphs`, and the difference is measured rather than
    stylistic. ``_BLOCK_START`` treats every ``> `` line as the start of a new
    block -- which is what keeps :func:`_decision_point_two` from reading the
    amendment into the point it amends -- and the consequence is that a
    blockquote comes back from that helper **one line at a time**. Measured
    2026-08-31 against ADR-0018: the correction note's own sentence is split
    across paragraphs, so ``"the count above said *twelve*, and no revision"``
    matches nothing there. A note wrapped over sixteen lines is unreadable in
    that form.

    A bare ``>`` separates notes inside one blockquote, and it is treated as a
    boundary. ADR-0018's Milestone 5 amendment is a single blockquote holding the
    original text, a dated repoint and a count correction; without that boundary
    the three come back as one string and a pin keyed on any of them would be
    satisfied by the other two.

    The ``> `` prefix is stripped rather than kept, so an anchor is written the
    way the sentence reads rather than with the quoting markup in it.
    """
    notes: list[str] = []
    current: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        quoted = stripped.startswith(">")
        content = stripped[1:].strip() if quoted else ""
        if quoted and content:
            current.append(content)
            continue
        if current:
            notes.append(collapsed(" ".join(current)))
            current = []
    if current:
        notes.append(collapsed(" ".join(current)))
    return notes


def _correction_note(text: str, anchor: str) -> str:
    """The one blockquote note carrying *anchor*, as a single collapsed string.

    Exactly one must match, the :func:`_corrected_bullet` rule met again. Zero
    means the note was deleted or rewritten past the sentence it exists to carry,
    which is the whole point of the presence pin; more than one means the anchor
    no longer identifies a single note and anything read out of it is about text
    this module was never scoped to.
    """
    notes = [note for note in _quoted_notes(text) if anchor in note]

    assert len(notes) == 1, (
        f"ADR-0018 has no single correction note carrying `{anchor}`: found {len(notes)}"
    )
    return notes[0]


def _owed_bullets(text: str) -> list[str]:
    """Every top-level bullet of the Compliance section, one collapsed string each.

    Not ``test_adr_0027_claims.py``'s ``_list_items``, and the difference is the
    reason this helper exists rather than importing that one. That version closes
    an item at the first blank line, which is right for the eleven-line residue it
    reads and wrong here: the point-1 bullet carries its repoint in a *second*
    paragraph ("Measured, not argued: ...") and the index bullet carries its
    correction in two indented blockquotes, so a blank-line rule would return the
    half of each bullet that does not contain the pointer and report it clean.

    An item therefore runs from a line starting with ``- `` at column 0 through
    every blank or indented line that follows, and ends at the next unindented
    non-blank line -- the next bullet, or the heading of whatever comes after.
    """
    bullets: list[str] = []
    current: list[str] | None = None
    for line in _compliance_section(text).splitlines():
        if line.startswith("- "):
            if current is not None:
                bullets.append(collapsed(" ".join(current)))
            current = [line]
        elif current is not None:
            if not line.strip() or line.startswith("  "):
                current.append(line)
            else:
                bullets.append(collapsed(" ".join(current)))
                current = None
    if current is not None:
        bullets.append(collapsed(" ".join(current)))
    return bullets


def _corrected_bullet(text: str, key: str) -> str:
    """The one Compliance bullet opening on *key*, as a single collapsed string.

    Exactly one must match. Zero means the bullet was rewritten past the claim it
    is keyed on and the scans below are about to pass over nothing; more than one
    means the key no longer identifies a single bullet and a scan would be
    reporting on text it was never scoped to. This is
    ``test_adr_0027_claims.py``'s ``_decision_two_residue`` rule, met again.

    The amendment guard is defence in depth rather than a live risk:
    :func:`_compliance_section` already cuts above the Decision, so the standing
    Milestone-5 cite is out of reach unless that slice breaks. Asserted anyway,
    because the failure it would cause -- a RED naming a sentence that is correct
    and must not move -- is the one a reader would act on wrongly.
    """
    bullets = [bullet for bullet in _owed_bullets(text) if key in bullet]

    assert len(bullets) == 1, (
        f"ADR-0018's Compliance section has no single owed bullet keyed on "
        f"`{key}`: found {len(bullets)}"
    )
    assert _AMENDMENT_STANDING_CITE not in bullets[0], (
        f"the bullet keyed on `{key}` now reaches the Milestone-5 amendment, whose "
        f"cite of the closed tracker stands by design; every scan below would "
        f"report that dated record as a defect"
    )
    return bullets[0]


def _owner_cites_of_the_closed_tracker(bullet: str) -> list[str]:
    """Every mention of the closed tracker whose own sentence does not retract it.

    The mirror of :func:`_detection_claims_without_denial`, and it discriminates
    the same way: the corrected bullets *keep* ``#15`` on purpose, inside the
    sentence that says it is dead, so a rule refusing the string outright would
    demand the history be deleted rather than corrected. What is left over is a
    sentence handing owed work to a closed issue.

    The sentence is the unit, for the reason that function's docstring records --
    a window that crosses a sentence boundary lets a re-acquired cite borrow the
    retraction of the sentence in front of it.
    """
    return [
        sentence.strip()
        for sentence in _SENTENCE_END.split(bullet)
        if _CLOSED_TRACKER.search(sentence) and not _RETRACTED.search(sentence)
    ]


def _lock_apis(text: str) -> list[str]:
    """Every lock API named in a piece of source text."""
    return _LOCK_API.findall(text)


def _index_write_path_modules() -> tuple[ModuleType, ...]:
    """The modules a published index is written through, derived from the symbols.

    Not a hand-maintained tuple, for the reason :func:`_swept_modules` records
    about the other sweep in this file: a written-down list cannot honour the
    property a comment would claim for it, that a module which moves takes the
    sweep with it.

    **Both writers, not one.** ADR-0018's index bullet is about the index as an
    artifact, and two entry points produce a published one: ``theurian index
    build``, which is the primary one and the only one the bullet's own first
    sentence ever named, and the withdrawal-triggered purge the correction
    blockquote adds. Until #446's first review round this walk started at the
    purge alone, so ``application/index_builder.py`` and ``cli/index_commands.py``
    -- the build path -- were outside a sweep whose stated population was "the
    modules the published index is written through". Demonstrated rather than
    argued: a ``fcntl.flock`` planted in ``index_builder.py`` left the suite
    green, while the same lock in the swept purge path was caught.

    The walk starts from one symbol per reason a module is in the population:

    - ``IndexBuilder.build`` -- the code that writes the rows of a new build.
    - ``_publish`` -- the CLI's pointer swap, which is what makes that build the
      *published* index. Private on purpose: it is the call site, and the
      module-level command around it would still resolve here if the helper were
      inlined, so keeping the premise below is what makes this choice safe.
    - ``publish_purge_for_withdrawal`` -- the use case ADR-0018's own correction
      names.
    - ``IndexStore.derive_purged`` and ``SqliteIndexStore.derive_purged`` -- both
      sides of the API the purge writes through.
    - ``purge_into`` -- the function that writes the purged file.
    - ``write_active_index_pointer`` -- the pointer swap both paths share.

    Sorted by name so the population, and any failure that reports it, is
    deterministic rather than set-ordered.
    """
    written_through = (
        IndexBuilder.build,
        _publish,
        publish_purge_for_withdrawal,
        write_active_index_pointer,
        IndexStore.derive_purged,
        SqliteIndexStore.derive_purged,
        purge_into,
    )
    modules = {
        module for function in written_through if (module := _module_of(function)) is not None
    }
    return tuple(sorted(modules, key=lambda module: module.__name__))


# -- The prose: ADR-0018's NFS acceptance ------------------------------------


def test_every_record_of_the_nfs_exclusion_says_nothing_detects_it() -> None:
    """RED means a record dropped the stated absence -- undone, reworded, or deleted.

    The positive half. It is not the negative one restated: a rewrite that drops
    the sentence entirely, or that softens it to "NFS is outside the supported
    configuration" with no statement about detection, makes no false claim and
    would pass
    :func:`test_no_record_of_the_nfs_exclusion_claims_anything_warns_about_it`
    while leaving the reader with no idea that nothing will tell them.

    **Both records, not just the ADR.** README carries the operator-facing copy,
    and the reason it is read here is the failure mode this whole file set is
    about: #432 corrected one document and left its restatement standing, and the
    two disagreed until #433 found the copy. README's line was written in the same
    PR as this pin for exactly that reason.

    The tracking reference is asserted in the same paragraph as the first
    statement of the absence, in each record, because an exclusion enforced by
    nothing is a decision that has to stay findable from wherever it is read.

    **At least once per record, not exactly once.** An earlier version required a
    single paragraph and would have failed on a document that restated the absence
    in, say, a compliance section -- reporting "the correction is gone" about a
    file that had just made the point twice. Saying a true thing twice is not the
    drift this module exists to catch; the negative half below refuses the false
    version.
    """
    scanned = {
        record.name: _nfs_paragraphs(record.read_text(encoding="utf-8"), key)
        for record, key in NFS_RECORDS.items()
    }
    assert len(scanned) == len(NFS_RECORDS), (
        f"two records share a filename, so one of them is not being read at all: "
        f"{sorted(record.name for record in NFS_RECORDS)}"
    )

    stating = {
        name: [paragraph for paragraph in paragraphs if NOTHING_DETECTS in paragraph]
        for name, paragraphs in scanned.items()
    }

    silent = sorted(name for name, paragraphs in stating.items() if not paragraphs)
    assert not silent, (
        f"these records no longer state that nothing detects a project directory on NFS: {silent}"
    )

    untracked = sorted(name for name, found in stating.items() if TRACKED_BY not in found[0])
    assert not untracked, (
        f"these records state the absence without naming the issue that tracks it, "
        f"so a reader who meets the exclusion cannot find the decision: {untracked}"
    )


def test_no_record_of_the_nfs_exclusion_claims_anything_warns_about_it() -> None:
    """RED means the phantom mitigation is back, in some tense, in some record.

    The negative half, and it catches what the positive one cannot: a record that
    keeps "nothing detects that it is" and asserts the warning somewhere else in
    the file. The wording it took was ``doctor`` "will warn about it", so the
    future tense is refused alongside the present.

    **README is swept as well as the ADR**, and the two need it for different
    reasons: the ADR is where the claim was made and retracted, while README is
    the file a later "quick start improvement" would helpfully add the mitigation
    to, having read the ADR's own account of ``doctor``.

    Scoped to those two records. The governed corpus snapshot of ADR-0018 still
    carries ``doctor`` "will warn about it" by design -- see the module docstring
    -- so widening this to a tree scan would report that anchor as drift.

    The scanned population is asserted before it is scanned, which is what stops
    a record that has lost its NFS paragraphs from passing as clean: a search over
    no paragraphs reports the same empty list as a search over correct ones.
    """
    scanned = {
        record.name: _nfs_paragraphs(record.read_text(encoding="utf-8"), key)
        for record, key in NFS_RECORDS.items()
    }

    unscanned = sorted(name for name, paragraphs in scanned.items() if not paragraphs)
    assert not unscanned, (
        f"these records have no paragraph mentioning NFS or `doctor`, so this scan "
        f"passes over nothing in them and would report a re-added claim as absent: "
        f"{unscanned}"
    )

    claims = {
        name: found
        for name, paragraphs in scanned.items()
        if (found := _detection_claims_without_denial(paragraphs))
    }

    assert not claims, f"a record claims something warns about or detects NFS: {claims}"


def test_the_prose_population_skips_command_samples_and_keeps_the_sentences() -> None:
    """RED means a code block is back in a prose population, or prose fell out of one.

    Driven by synthetic input because the shipped records cannot drive it: with
    README keyed on NFS alone, no live document has a fenced block the key would
    select, so a :func:`_without_code_fences` that did nothing -- or one that ate
    the whole document -- would be indistinguishable from a working one. That is
    the guard-no-input-reaches shape this file set keeps meeting.

    Both directions, and the second is the load-bearing one. A rule that removed
    everything would satisfy "the command sample is gone" while leaving every scan
    below reading an empty document and reporting a re-added claim as absent.

    The sample's fenced line is the exact shape README carries -- a comment
    beside a command -- because the false RED #446's first round found was a
    ``doctor`` line inside a shell block being read as a sentence about NFS.
    """
    document = (
        "Nothing detects that it is, and no probe is planned.\n"
        "\n"
        "```sh\n"
        "theurian doctor            # warns about an NFS directory\n"
        "```\n"
        "\n"
        "The exclusion is recorded rather than enforced.\n"
    )

    paragraphs = _paragraphs(_without_code_fences(document))

    assert any("nothing detects that it is" in paragraph for paragraph in paragraphs), (
        f"the prose population lost a sentence outside every code fence: {paragraphs}"
    )
    assert any("recorded rather than enforced" in paragraph for paragraph in paragraphs), (
        f"the prose population stops after the first fence, so everything below a "
        f"command sample is unread: {paragraphs}"
    )
    assert not any("theurian doctor" in paragraph for paragraph in paragraphs), (
        f"a fenced command sample is in the prose population, so a scan whose rule "
        f"is about what a sentence claims is reading a shell transcript: {paragraphs}"
    )


def test_readme_is_not_swept_for_true_sentences_about_doctor() -> None:
    """RED means README's key is back to selecting every ``doctor`` paragraph.

    The false RED #446's first review round reported, as a named test rather than
    something the next author discovers by writing an ordinary sentence. README is
    not where the phantom NFS mitigation lived; it carries one line of the
    exclusion, and that line names NFS. A key that also selects ``doctor`` puts a
    true, unrelated sentence about the command into a population whose rule
    refuses an undenied ``warns``.

    The probe is a sentence README is entitled to write, appended to the real file
    rather than to a synthetic one, so the assertion is about the key the shipped
    record is actually read on. ADR-0018 is checked in the same breath, because
    the narrowing must not travel: ``doctor`` is where its retracted claim
    attached, and a key change that removed it there would delete the pin.
    """
    a_true_doctor_sentence = "\n\n`theurian doctor` warns when the daemon is not running.\n"
    readme = README.read_text(encoding="utf-8") + a_true_doctor_sentence

    claims = _detection_claims_without_denial(_nfs_paragraphs(readme, NFS_RECORDS[README]))

    assert not claims, (
        f"README's NFS scan fires on a true sentence about `doctor`, so an ordinary "
        f"addition to the quick start goes RED as the phantom NFS mitigation "
        f"returning: {claims}"
    )

    adr = ADR_0018.read_text(encoding="utf-8") + a_true_doctor_sentence
    assert _detection_claims_without_denial(_nfs_paragraphs(adr, NFS_RECORDS[ADR_0018])), (
        "ADR-0018's key no longer selects a paragraph on `doctor` alone, so the "
        "record where the phantom mitigation actually lived is no longer swept for "
        "it returning"
    )


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


# -- The lock's location: prose ----------------------------------------------


def test_adr_0018_says_the_write_lock_is_a_separate_file_and_names_it() -> None:
    """RED means Decision point 2 stopped naming the file the lock is taken on.

    The positive half of the second claim. The mechanism phrase and the path are
    asserted together because either alone is what the record used to be: the
    retracted point named a mechanism ("an OS advisory file lock") and attached it
    to the wrong object, so a rewrite that keeps the mechanism and drops the path
    is the same defect with the evidence removed.

    ``STATE_DIR`` is required too -- the point says what the lock *guards*, and
    that half is what makes "separate" mean something rather than merely "not
    here".
    """
    point = _decision_point_two(ADR_0018.read_text(encoding="utf-8"))

    assert "separate lock file" in point, (
        "Decision point 2 no longer says the lock is taken on a separate lock file"
    )
    assert LOCK_PATH in point, f"Decision point 2 no longer names `{LOCK_PATH}`"
    assert STATE_DIR in point, f"Decision point 2 no longer names the `{STATE_DIR}` it guards"


def test_adr_0018_does_not_reattach_the_write_lock_to_a_database() -> None:
    """RED means the retracted attachment is back in Decision point 2.

    The negative half, and it catches what the positive one cannot: a point that
    names the lock file and *also* says the lock is taken on the database, which
    is how the document read for a milestone -- point 2 attached it to the
    database while the Negative consequence named both paths correctly.

    Scoped to point 2. The correction note quotes the retracted phrase to explain
    it, and a scan that read the whole document would report the fix as the
    defect.
    """
    point = _decision_point_two(ADR_0018.read_text(encoding="utf-8"))

    attachments = find_lock_on_database(point)

    assert not attachments, f"Decision point 2 attaches the write lock to a database again: {point}"


# -- The lock's location: fact -----------------------------------------------


def test_the_write_lock_resolves_outside_the_state_directory(tmp_path: pathlib.Path) -> None:
    """RED means the lock moved -- and Decision point 2 must move with it.

    Derived from a real ``ProjectPaths`` rather than from path strings, in
    ``write_lock_claims.py`` rather than here. The derivation is shared because a
    second record -- ADR-0027's decision-2 residue -- names the same two objects,
    and a copy of this arithmetic in each pin would fail whichever module its
    author remembered to update. ``test_adr_0027_claims.py`` calls the same
    function, so a lock that moves takes both documents RED together.

    The literals are asserted **equal** to the derivation rather than searched for
    in it. ``LOCK_PATH`` is written independently in the helper and required in
    the ADR by the prose test above, so the ADR, that helper and ``ProjectPaths``
    are held to one string: move the lock in the code and this goes RED; rename it
    in the ADR and the prose test does.
    """
    assert_the_lock_and_the_state_databases_resolve_apart(
        tmp_path / "repo", record="ADR-0018 Decision point 2"
    )


# -- The amendment's count, held against the port ----------------------------


def test_the_amendment_spells_the_write_method_count_the_port_publishes() -> None:
    """RED means the ADR's count and the live port disagree -- either side moved.

    ADR-0018's Milestone 5 amendment says, in the present tense, that
    ``CanonicalStore`` "publishes its thirteen write methods directly". That is a
    fact about today's code written into a durable record, and until #446's first
    review round nothing held it: the correction beside it pointed a reader at
    ``test_connection_claims.py``'s **failure message**, which renders only when
    that test fails, so both directions were measured green -- the word reverted
    to *twelve*, and a fourteenth write method added to the port.

    So the assertion is an equality between two independently written things, the
    ``test_setup_claims.py`` shape: the word this document spells, and
    ``len(write_methods())`` derived from the live Protocol. It fails whichever
    one moves, which is what "held against the port rather than by hand" has to
    mean.

    **The derivation is imported, not repeated.** ``write_methods`` lives in
    ``canonical_store_surface.py`` and ``test_connection_claims.py`` reads the
    same function -- a second copy of the member walk here would go RED in
    whichever module its author remembered, which is the defect
    ``write_lock_claims.py`` exists to prevent, met again on a second derivation.

    **The two neighbouring figures are deliberately not held to the port.** The
    correction note reports what the port published at ``261eff3`` and at
    ``f665ecf``, and the retraction inside it quotes the *twelve* it removed.
    Both are measurements of named moments, which is the one form of a written
    number this file set accepts; asserting them against today's port would
    demand that history be rewritten every time the port grows.

    The premises come first. A member walk that returned nothing would make the
    derived count zero, and a document whose sentence no longer spells a number
    at all would make the read side empty -- each has to fail naming itself
    rather than arriving at the comparison as a bare mismatch.
    """
    note = _correction_note(ADR_0018.read_text(encoding="utf-8"), AMENDMENT_COUNT_NOTE)

    spelled = _SPELLED_COUNT.findall(note)
    assert len(spelled) == 1, (
        f"the amendment no longer states the port's write-method count as one "
        f"spelled number, so this pin has nothing to hold against the port: {spelled}"
    )
    assert spelled[0] in _SPELLED_NUMBERS, (
        f"the amendment spells its write-method count as `{spelled[0]}`, which is "
        f"not a number this pin can read; the sentence has to say how many, or the "
        f"count is back to being a claim nobody can check"
    )

    derived = write_methods()
    assert derived, (
        "the `CanonicalStore` member walk found no write method at all, so the "
        "count below would be about a port this test never read"
    )

    assert _SPELLED_NUMBERS[spelled[0]] == len(derived), (
        f"ADR-0018's Milestone 5 amendment says the port publishes "
        f"`{spelled[0]}` write methods; it publishes {len(derived)}: "
        f"{sorted(derived)}. Whichever side moved, the record and the port have "
        f"to be brought back into step"
    )


# -- The owner of the owed work: prose ---------------------------------------


def test_every_correction_blockquote_is_still_in_the_record() -> None:
    """RED means a correction note was deleted, and the claim it retracts is back.

    Presence pins, and they hold what no scan below can. Every bullet scan in this
    module is keyed on what a bullet *asserts*; a correction note carries the
    *retraction*, so deleting one leaves the bullet head's ``(owed, #439)``
    satisfying :data:`LIVE_OWNER` and the closed-tracker scan passing over a
    bullet that no longer retracts anything. #446's first review round measured
    exactly that: all three notes could be removed with every other test in this
    file green.

    :data:`CORRECTION_NOTES` names, per anchor, what the deletion would silently
    undo, and the failure message prints it -- so a RED here says which claim just
    came back rather than only that a string is missing.

    Presence is all this asserts. A note reworded around its anchor still passes,
    which is the same limit every grammar pin in this file records; what it
    refuses is the deletion, which is the move that was measured to be free.
    """
    text = ADR_0018.read_text(encoding="utf-8")
    notes = _quoted_notes(text)

    assert notes, (
        "no blockquote was parsed out of ADR-0018, so this test would report every "
        "correction note as deleted, or none"
    )

    deleted = {
        anchor: resurrects
        for anchor, resurrects in CORRECTION_NOTES.items()
        if not any(anchor in note for note in notes)
    }

    assert not deleted, (
        f"these correction notes are gone from ADR-0018, and with each one the "
        f"claim it retracts is standing again: {deleted}"
    )


def test_the_point_1_bullet_keeps_the_re_measurement_that_narrows_its_own_claim() -> None:
    """RED means the bullet is back to a Milestone-5 measurement that no longer holds.

    The point-1 bullet states a measurement -- adding a ``connection()`` method
    left the suite green and nothing noticed -- and two of that measurement's
    three spellings are RED today. What keeps the bullet honest is the paragraph
    beside it recording the re-measurement and naming ``-> object`` as the
    residual, and that paragraph is deletable in exactly the way the correction
    blockquotes were: nothing else in this module keys on it, so removing it
    leaves the falsified sentence standing alone with every test green.

    Anchored on the marker and on the residual rather than on a date, so a later
    re-measurement may move the date freely and may not quietly drop the finding.
    """
    bullet = _corrected_bullet(ADR_0018.read_text(encoding="utf-8"), POINT_1_BULLET)

    missing = sorted(anchor for anchor in POINT_1_REMEASUREMENT if anchor not in bullet)

    assert not missing, (
        f"the point-1 bullet no longer carries its re-measurement: {missing}. "
        f"Without it the bullet's Milestone-5 sentence stands alone, and that "
        f"measurement was falsified on 2026-08-31 in two spellings of three"
    )


def test_the_compliance_slice_anchors_on_a_heading_and_not_a_mention() -> None:
    """RED means the section slice is back to a plain substring search.

    Driven by synthetic input because the shipped ADR cannot drive it: it carries
    no ``### Compliance`` subheading and names the section nowhere in prose, so a
    slice keyed on the bare substring and one anchored to a line start return the
    same text today. A guard no input reaches is a guard that survives its own
    deletion.

    Two shapes in one document, because they fail the same way and neither is
    exotic: a sentence that names the section, and a subheading one level deeper
    -- ``### Compliance notes`` *contains* ``## Compliance``. Either one earlier
    in the file starts a substring slice above the real section, and every scan
    built on it then reads bullets it was never scoped to without anything
    failing. That is the silent-widening twin of the empty-population defect the
    two sweeps in this module assert against.
    """
    document = (
        "## Decision\n"
        "\n"
        "1. The section named `## Compliance` below lists what is owed.\n"
        "\n"
        "### Compliance notes\n"
        "\n"
        "- A bullet that must stay outside the slice.\n"
        "\n"
        "## Compliance\n"
        "\n"
        "- The only bullet the slice may return.\n"
    )

    section = collapsed(_compliance_section(document))

    assert "the only bullet the slice may return" in section, (
        f"the Compliance slice no longer starts at the section heading: {section}"
    )
    assert "must stay outside" not in section, (
        f"the Compliance slice starts at a mention of the heading rather than at "
        f"the heading, so every scan below reads text it was never scoped to: "
        f"{section}"
    )


def test_both_corrected_compliance_bullets_name_the_live_owner_of_the_owed_work() -> None:
    """RED means a repointed bullet stopped naming #439 -- the repoint undone.

    The positive half of the third claim. It is not the negative one restated: a
    bullet rewritten to say only "owed, unscheduled" names nothing dead and would
    pass
    :func:`test_neither_corrected_bullet_cites_the_closed_tracker_as_an_owner`
    while leaving a reader who wants to know when the single write interface
    arrives with nowhere to look -- which is the state #15's closure put this
    document in.

    The link is required rather than the bare ``#439``, for the reason
    :data:`TRACKED_BY` is required of the NFS bullet: owed work whose owner is not
    reachable from the record is work nobody finds again.

    **"Names the live owner" is carried by this half and the next one together.**
    Neither the owner phrase nor its position is matched here, because grammar
    that is pinned has a next grammar; what makes ``#439`` the *owner* rather than
    a passing mention is that no cite of the closed tracker stands beside it
    unretracted, and that is what the negative half asserts.
    """
    text = ADR_0018.read_text(encoding="utf-8")

    unowned = sorted(
        key for key in CORRECTED_BULLETS if LIVE_OWNER not in _corrected_bullet(text, key)
    )

    assert not unowned, (
        f"these owed bullets no longer name `{LIVE_OWNER}`, the live owner of the "
        f"single-writer work: {unowned}"
    )


def test_neither_corrected_bullet_cites_the_closed_tracker_as_an_owner() -> None:
    """RED means a dead pointer is standing again in a bullet that was corrected.

    The negative half, and it catches what the positive one cannot: a bullet that
    names #439 *and* re-acquires "Milestone 6, with #15" beside it, which is
    exactly how both of these bullets read until 2026-08-31 -- #15 closed on
    2026-08-10 by wiring ADR-0024 decision 5, which is neither the store's write
    interface nor the index's contract.

    **The cite is kept and the retraction is what is asserted.** Each corrected
    bullet still contains ``#15``, inside the sentence recording what it named and
    why that tracker is dead; a rule that refused the string outright would demand
    the history be deleted rather than corrected, which is the opposite of what
    this document does.

    Scoped to the two bullets. The Milestone-5 amendment cites #15 in a sentence
    left standing verbatim as a dated record, and both correction notes quote it
    in order to retract it, so a file-wide scan would report three standing
    records as the defect returning.
    """
    text = ADR_0018.read_text(encoding="utf-8")

    standing = {
        key: cites
        for key in CORRECTED_BULLETS
        if (cites := _owner_cites_of_the_closed_tracker(_corrected_bullet(text, key)))
    }

    assert not standing, (
        f"these owed bullets hand work to the closed tracker again, in a sentence "
        f"that does not retract it: {standing}"
    )


def test_the_closed_tracker_scan_catches_an_owner_cite_and_spares_a_retraction() -> None:
    """RED means the scan stopped discriminating, so the test above passes over nothing.

    Driven by synthetic input because the shipped document cannot drive it: the
    scan's whole point is that it finds nothing today, so a
    :func:`_owner_cites_of_the_closed_tracker` gutted to return an empty list
    would be indistinguishable from a working one. That is the mutation this
    catches -- measured on the ADR-0013 module, where deleting a scan's core left
    every other test green.

    The positives are the two forms the dead pointer actually took in this file,
    quoted from the pre-correction revision, plus a bare link with no ``#15``
    label -- the form a cite would take if someone pasted the URL. The negatives
    are the two sentences the corrected bullets carry now, which must not fire:
    a pin that punished the correction would be read as telling its author to
    delete the history.
    """
    as_the_point_1_bullet_read = collapsed(
        "Milestone 6, with [#15](https://github.com/theurian/theurian/issues/15) "
        "— the interface has to exist before a test can pin its surface"
    )
    as_the_index_bullet_read = collapsed(
        "- **The derived index has no single-writer contract at all** (Milestone 6, "
        "[#15](https://github.com/theurian/theurian/issues/15))"
    )
    a_bare_link = collapsed("owed, tracked in https://github.com/theurian/theurian/issues/15")
    as_the_point_1_bullet_reads_now = collapsed(
        "This bullet named Milestone 6 and "
        "[#15](https://github.com/theurian/theurian/issues/15) until 2026-08-31; "
        "#15 closed on 2026-08-10 without shipping the interface"
    )
    as_the_index_bullet_reads_now = collapsed(
        "The tracker it named, [#15](https://github.com/theurian/theurian/issues/15), "
        "closed on 2026-08-10 by wiring ADR-0024 decision 5"
    )

    assert _owner_cites_of_the_closed_tracker(as_the_point_1_bullet_read), (
        "the scan no longer catches `Milestone 6, with #15`, which is how the "
        "point-1 bullet read until 2026-08-31"
    )
    assert _owner_cites_of_the_closed_tracker(as_the_index_bullet_read), (
        "the scan no longer catches the parenthesised `(Milestone 6, #15)`, which "
        "is how the index bullet read until 2026-08-31"
    )
    assert _owner_cites_of_the_closed_tracker(a_bare_link), (
        "the scan reads only the `#15` label, so a cite that pastes the issue URL "
        "instead escapes it"
    )

    assert not _owner_cites_of_the_closed_tracker(as_the_point_1_bullet_reads_now), (
        "the scan fires on the sentence that retracts the pointer, so it asks for "
        "the history to be deleted rather than corrected"
    )
    assert not _owner_cites_of_the_closed_tracker(as_the_index_bullet_reads_now), (
        "the scan fires on the correction note's own retraction, which names the "
        "tracker in order to say it is dead"
    )


# -- The owner of the owed work: fact ----------------------------------------


def test_the_lock_api_sweep_catches_a_taken_lock_in_synthetic_source() -> None:
    """RED means the sweep stopped matching, so the test below passes over nothing.

    Driven by synthetic source for the same reason
    :func:`test_the_filesystem_api_sweep_catches_a_probe_in_synthetic_source` is:
    the sweep finds nothing today, so an implementation that always returned
    nothing would look identical to a working one.

    The samples are the two real shapes -- ``fcntl``'s blocking flock, as
    ``connection.py`` and ``instance.py`` take it, and the POSIX ``lockf``
    spelling -- and the constant is fed in lowercase, because the sweep is
    case-insensitive and a probe written ``fcntl.lock_ex`` in a comment should
    still be read.
    """
    an_flock = "import fcntl\n\ndef _hold(handle):\n    fcntl.flock(handle, fcntl.LOCK_EX)\n"
    a_lockf = "    fcntl.lockf(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)\n"

    assert _lock_apis(an_flock), "the sweep no longer matches an `fcntl.flock` call"
    assert _lock_apis(a_lockf), "the sweep no longer matches the `lockf` spelling"


def test_the_lock_api_sweep_ignores_identifiers_that_merely_contain_a_token() -> None:
    """RED means the sweep fires on ordinary code, and gets deleted for crying wolf.

    ``lockf`` sits inside no word here by accident, but ``write_lock``,
    ``lock_path`` and ``blockfile`` are all names the index and store modules use
    or could use, and a sweep matching on substrings would fire on every one of
    them. ``write_lock`` is the load-bearing case: it is a real property of
    ``ProjectPaths``, declared in one of the swept modules, so an unbounded rule
    would report the canonical store's lock as an index lock.
    """
    unrelated = "write_lock = paths.write_lock\nlock_path = 1\nblockfile = 2\nunlocked = 3\n"

    assert not _lock_apis(unrelated), (
        "the sweep fires on identifiers that merely contain one of its tokens, "
        "starting with the canonical store's own `write_lock`"
    )


def test_no_index_write_path_module_takes_a_lock() -> None:
    """RED means an index write lock landed -- and ADR-0018's index bullet must move.

    The fact half of the third claim, and half of that claim: the bullet says the
    index has no single-writer contract, and its correction blockquote rests that
    on two absences. This holds the lock one. The other -- that no single write
    interface has appeared on the ``CanonicalStore`` port -- is held live by
    ``test_connection_claims.py::test_the_canonical_store_port_declares_no_single_write_interface``
    and is cited rather than copied here.

    A source-text search, because the claim is about an absence and an absence has
    no return value to read. What that means for its reach is in the module
    docstring, and it is narrower than "no index lock exists".

    **The population is asserted before it is swept**, which is the finding this
    shape exists to prevent: a search over no modules reports the same "no lock
    found" as a search over clean ones. The premise names
    :data:`REQUIRED_INDEX_WRITERS` -- both writers of a published index, and the
    CLI module that publishes the build path's pointer -- so a rename or an
    inlining that moved one of them out of this sweep fails here rather than
    passing quietly. Round one of #446 is why the list is three and not one: the
    build path was outside the population while the docstring above claimed it,
    and only the purge was pinned.
    """
    swept = _index_write_path_modules()
    assert swept, (
        "the index write path resolves to no module at all, so the search below "
        "would pass over nothing and report a landed lock as an absence"
    )

    names = {module.__name__ for module in swept}
    missing = sorted(set(REQUIRED_INDEX_WRITERS) - names)
    assert not missing, (
        f"these index writers are no longer in the swept set, so a lock taken in "
        f"one of them would go unseen while this test reported the index write "
        f"path clean: {missing}. Swept: {sorted(names)}"
    )

    foreign = sorted(name for name in names if not name.startswith("theurian."))
    assert not foreign, (
        f"these index write path modules resolve outside the package, so the code "
        f"behind them is not swept and a lock there would go unseen: {foreign}"
    )

    sources = {
        module.__name__: pathlib.Path(module.__file__ or "").read_text(encoding="utf-8")
        for module in swept
    }
    empty = sorted(name for name, text in sources.items() if not text.strip())
    assert not empty, f"swept modules have no source to search: {empty}"

    found = {
        name: sorted(set(apis)) for name, text in sources.items() if (apis := _lock_apis(text))
    }

    assert not found, (
        f"the index write path now takes a lock: {found}. ADR-0018's index bullet "
        f"must stop saying there is no index write lock, and the work it hands to "
        f"{LIVE_OWNER} has moved"
    )
