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
could name it without naming the filesystem. README is swept on the filesystem
without the command -- ``nfs`` *or* ``network filesystem``: it is not where the
claim lived, its one line of the exclusion names the filesystem in both
spellings, and a key that also selected ``doctor`` turned every true sentence
about that command into a false RED -- "``theurian doctor`` warns when the daemon
is not running" read as the mitigation returning. Both directions are held by
:func:`test_readme_is_not_swept_for_true_sentences_about_doctor`, so the
narrowing cannot travel to the ADR. The second spelling is on README's key
because "network filesystem" is the wording an operator writing this hazard
reaches for first, and a key on the acronym alone would drop the whole paragraph
out of the population if a rewrite kept the hazard in words -- which every scan
below would report as the correction being absent rather than gone.

**Fenced code blocks are outside every prose population here.** README's quick
start runs ``theurian doctor`` inside an ``sh`` block, and until #446's first
review round that block was a member of the NFS population -- a shell transcript
in a scan whose rule is about what a *sentence* claims. Nothing in a command
sample can state or retract the exclusion, so :func:`_without_code_fences`
removes them before any key is applied.

That removal is toggle-driven, so an **odd** number of fence delimiters in a
record makes it swallow the whole tail of the document -- and the tail is where a
re-added claim would sit, below the paragraph the key selects. #446's second
review round demonstrated it: a phantom mitigation plus one stray ``` line left
both prose halves green. Each of them now asserts fence parity per record before
it reads a paragraph, so the unbalanced fence fails naming the file instead of
blinding the scan under it.

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

**The reach limit above is what the second lock pin exists for, and the two are
complements rather than copies.** The bullet's claim is about the *package* --
"There is no index write lock in the package", with an unanchored ``git grep``
over ``packages/theurian-core/src`` pasted beside it -- while the sweep above
starts from symbols and therefore cannot see a module nothing imports yet.
:func:`test_every_lock_in_the_package_belongs_to_one_of_the_two_known_families`
starts from the filesystem instead: every line under the package source matching
:data:`_LOCK_POPULATION_KEY` must fall in :data:`KNOWN_LOCK_FAMILIES`, which
names five files and the two reasons -- ``ProjectPaths.write_lock``'s
state-database family and ``daemon/instance.py``'s single-instance lock. A lock
anywhere else is one nobody has classified.

Neither sweep subsumes the other. The symbol walk moves with a renamed writer and
this one does not; this one sees a file no symbol reaches and the walk does not.
The population one restores ``write_lock`` to its key -- unanchored and
case-insensitively, so ``index_write_lock`` and the PascalCase class name built
on the same stem are both inside it -- for
the reason recorded on :data:`_LOCK_NAME_TOKENS`: the two sweeps ask different
questions, and in *this* one the canonical store's lock is not noise, it is one of
the two answers. Its own limit is that it classifies per file, so a lock added
inside an already-accepted file for an index would pass it; that is the shape the
symbol walk covers.

**That pin carries #445's fact side as well.** ADR-0024 point 4 says a purge
"goes through the same single-writer interface as a build", that "there is exactly
one such interface", and that "publishing is a separate step that takes the index
write lock". Measured 2026-09-01 against ``ec0dbcd``
(``docs/work-logs/2026-09-01-472-purged-build-re-measurement.md``), the third
clause has no referent: the pointer swap is a write-to-temp plus ``os.replace``,
the lock file is never created, and ``application/withdrawal_purge.py`` records
"No new index-write lock is taken" in its own source. So the day a lock does land,
that correction has to be re-decided rather than filed as pending -- and the pin
is what says so at the moment it happens.

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

**Eight correction paragraphs are pinned, because deleting one is how a
corrected claim comes back for free.** Every bullet scan above is keyed on what a
bullet *asserts*, while a correction note carries the *retraction* -- so removing
a note leaves the bullet head's ``(owed, #439)`` satisfying :data:`LIVE_OWNER`
and the closed-tracker scan passing over a bullet that retracts nothing. Seven of
the eight were measured deletable before they were pinned: three in #446's first
review round, three in its second -- the Neutral point's NFR-4 amendment heading
and the Milestone-5 amendment's two opening retraction paragraphs, materially the
pre-#441 record -- and one in its third, the NFR-4 amendment's *body*, which the
Compliance section's "NFR-4 is not discharged, per the amendment above" points
at. The third was measured at ``155dc08`` on 2026-08-31 with all twenty-five
tests of this module green; the earlier two rounds re-ran theirs against the tree
that preceded each fix. The eighth pin, the #468 narrowing, is not a measured
deletion: it arrived as new prose on this branch and was pinned with it.
:data:`CORRECTION_NOTES` names what each removal resurrects.

**That "eight" is derived, not typed.**
:func:`test_this_modules_count_of_its_own_pins_is_derived_from_the_mapping` reads
the word out of this docstring and asserts it equals ``len(CORRECTION_NOTES)``.
The rule the amendment's write-method count is held to, turned on this file:
#446's third review round found a hand-kept "six times" over a seven-item list
here, which is the same defect one layer in.

**Presence is half of it, and the second round found the other half.** An anchor
is a short fragment, so a paragraph cut down to it passed, and so did one
rewritten around it to assert the very diagnosis the correction retracted.
:data:`CorrectionNote.load_bearing` requires the content each correction turns
on, and
:func:`test_every_correction_note_still_carries_what_makes_it_a_correction`
holds it. Which paragraphs are outside the set, and why -- including the ones
*inside* pinned blockquotes, since the unit is a paragraph and not a quote -- is
recorded on :data:`_UNPINNED_BLOCKQUOTES`.

**Three corrections live outside a blockquote and need their own pins.** The
point-1 bullet's re-measurement paragraph is held by
:func:`test_the_point_1_bullet_keeps_the_re_measurement_that_narrows_its_own_claim`;
the #468 narrowing of Decision point 2 reached three places, and only one of
them is a blockquote. #468's fix closed the gap the narrowing described, so
the two non-blockquote places now hold the *closure* rather than the
narrowing:
:func:`test_decision_point_2_no_longer_carries_the_narrowing`
holds the absence of the boundary clause a reader used to meet in the
Decision itself, and
:func:`test_the_positive_consequence_records_that_already_safe_was_measured_false`
still holds the Consequences bullet's dated retraction -- which stays as
history, with a closure sentence appended -- rather than the claim it argued
for.

**The record's pointers are evidence, so they are resolved.**
:data:`ADR_SYMBOL_POINTERS` lists every ``module.py::symbol`` reference ADR-0018
makes and
:func:`test_every_symbol_pointer_in_the_adr_resolves_to_something_live` reads
each one out of the live module. #446's second review round renamed
``write_methods`` and found the record still naming the old symbol; re-run here
on 2026-08-31 against the pre-fix tree, every test in this module,
``test_connection_claims.py`` and ``test_ports.py`` was green while the ADR
pointed at a function that no longer existed.

That "every" is held rather than asserted. The list is compared for **equality**
against the references :data:`_SYMBOL_REFERENCE` harvests out of the record, so a
reference *added* to the ADR fails as loudly as one renamed out of the code.
#446's third review round appended a fabricated
``application/nonexistent_module.py`` reference to the Compliance section; re-run
here on 2026-08-31 against the pre-fix tree at ``155dc08``, all twenty-five tests
of this module were green.

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

import ast
import functools
import io
import pathlib
import re
import sys
import tokenize
from types import ModuleType
from typing import Final, NamedTuple

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
from theurian.domain import ports
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

#: The key README is read on: the filesystem, without ``doctor``. It drops the
#: command for a measured reason. README is not where the retracted claim lived:
#: it carries one operator-facing line of the exclusion, and that line names the
#: filesystem. Keying it on ``doctor`` as well puts every true sentence about that
#: command into a population whose rule refuses an undenied ``warns`` or
#: ``detects`` -- so "``theurian doctor`` warns when the daemon is not running", a
#: sentence this README is entitled to write, went RED as the phantom NFS
#: mitigation returning. That false RED is what #446's first review round
#: reported, and a pin that fires on an unrelated true sentence is one the next
#: author deletes rather than reads.
#:
#: **Both phrasings, and the second is not decoration.** README's paragraph opens
#: on "advisory locks behave inconsistently on network filesystems" and reaches
#: the acronym one clause later, and "network filesystem" is the wording an
#: operator writing the quick-start hazard would reach for first. A key on the
#: acronym alone selects that paragraph today only because the acronym happens to
#: be in it; a rewrite that kept the hazard in words would drop the whole
#: paragraph out of the population and every scan below would report the
#: correction as absent rather than as gone. Unbounded on the right, so the plural
#: README actually writes is matched.
#:
#: Measured 2026-08-31: README's only ``doctor`` mention is the comment inside the
#: quick start's ``sh`` block, which :func:`_without_code_fences` removes from the
#: population anyway, so the narrowing loses no paragraph this scan was reading;
#: and "network filesystem" appears in README only in that same NFS paragraph, so
#: widening to it adds no paragraph either.
_NFS_WITHOUT_DOCTOR: Final = re.compile(r"\bnfs\b|\bnetwork filesystem")

#: Every live record that states the NFS exclusion, and therefore every record
#: that can drift back, mapped to the key each one is read on. The ADR is where
#: the decision lives; README carries one line of it where an operator meets
#: ``.theurian/`` for the first time. Both prose halves below sweep this mapping
#: rather than one file, because a copy nobody reads is a copy that drifts. The
#: governed corpus twin is deliberately absent -- see the module docstring for
#: the reason, and for why that reason does not reach README.
#:
#: The keys are :data:`_NFS_OR_DOCTOR` and :data:`_NFS_WITHOUT_DOCTOR`, and they
#: differ for the reason recorded on the second: only one of these two records is
#: where the phantom ``doctor`` warning lived.
NFS_RECORDS: Final = {ADR_0018: _NFS_OR_DOCTOR, README: _NFS_WITHOUT_DOCTOR}


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

#: The false-RED probe's own words, so the diagnostic that appends it to a live
#: record can tell its own sample from the record's real prose.
#:
#: :func:`test_readme_is_not_swept_for_true_sentences_about_doctor` reads the
#: shipped README plus this sentence, and until #446's second review round it
#: asserted over the whole result -- so a genuine phantom mitigation written into
#: README would have failed the key diagnostic as well as the claim scan, sending
#: the next reader at a key that was fine. Chosen as a sentence about ``doctor``
#: that says nothing about a filesystem: it is exactly what the narrowed README
#: key must *not* select, and exactly what the ADR key must.
_PROBE_SENTENCE: Final = "warns when the daemon is not running"

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


class CorrectionNote(NamedTuple):
    """What one anchored blockquote paragraph owes the record.

    ``resurrects`` is what its deletion would silently undo -- printed in the
    failure so a RED says which claim just came back, not only that a string is
    missing. ``load_bearing`` is the content that makes it a *correction* rather
    than a heading: fragments from the same paragraph, so a note gutted to its
    anchor, or rewritten around the anchor to say something else, fails.
    """

    resurrects: str
    load_bearing: tuple[str, ...]


#: The correction blockquote paragraphs this module's claims rest on, keyed by an
#: anchor out of each one's own text. Every ``resurrects`` below is a claim that
#: goes back to being *true in the record and false in the code* the moment its
#: paragraph is gone.
#:
#: They are pinned because #446's first review round measured that three of them
#: could be deleted with every other pin in this module green, its second round
#: measured the same of three more, and its third of one more -- the NFR-4
#: amendment's body. The eighth, the #468 narrowing, arrived as new prose on this
#: branch and was pinned with it. The bullet scans are the reason: they
#: are keyed on what a bullet *asserts*, and a correction note carries the
#: retraction rather than the assertion, so the bullet head's ``(owed, #439)``
#: alone satisfies :data:`LIVE_OWNER` and the closed-tracker scan passes over a
#: bullet that no longer retracts anything.
#:
#: **Presence alone was not enough either, and that is the second round's other
#: finding.** An anchor is a short fragment, so a note cut down to just that
#: fragment passed, and so did one rewritten around it to assert the diagnosis the
#: correction had *retracted* -- "the count above said *twelve*, merely stale by
#: one and refreshed here". ``load_bearing`` is what closes that: each paragraph
#: must still carry the content its correction turns on. The count note's is the
#: retracted-versus-stale distinction and the pointer to what holds the number
#: against the port, which are exactly the two things a "merely stale" rewrite
#: drops.
#:
#: The anchor and the fragments are sentence text rather than dates. A note
#: re-measured later gets a new date and must not go RED for it.
#:
#: **Every string here is lowercase, and that is a requirement rather than a
#: style.** :func:`_quoted_notes` collapses through ``write_lock_claims.collapsed``,
#: which lowercases; a capital in an anchor makes its note report as *deleted*
#: whatever the record says. The premise in
#: :func:`test_every_correction_blockquote_is_still_in_the_record` refuses that
#: shape by name, because the failure it otherwise produces reads as prose drift.
#:
#: **``load_bearing`` is scoped to the anchor's own paragraph, not to its
#: blockquote**, and that is worth stating because it decides how a multi-paragraph
#: amendment is pinned. :func:`_correction_note` returns the one paragraph carrying
#: the anchor, so a fragment from a sibling paragraph of the same blockquote is
#: reported *missing* -- measured 2026-08-31 at ``155dc08`` by hanging the NFR-4
#: body's "is owed to milestone 6's ..." off the ``the citation of nfr-4`` anchor,
#: which went RED on the content pin. A second paragraph is therefore pinned by a
#: second entry with its own anchor, which is what the pair below does.
#:
#: **What this can and cannot hold, stated rather than implied.** It holds the
#: paragraph's presence and the presence of the fragments named here. It does not
#: hold the sentences around them: a paragraph reworded everywhere except these
#: fragments still passes, and no string pin can do better. What it converts is a
#: silent deletion into a RED that names the resurrected claim.
CORRECTION_NOTES: Final[dict[str, CorrectionNote]] = {
    "there is no such method, and there never has been": CorrectionNote(
        resurrects=(
            "the Milestone-5 amendment loses its own opening retraction, so points 1 and 3 "
            "name `CanonicalStore.transaction()` under an amendment that no longer says "
            "the method was never built"
        ),
        load_bearing=('git grep "def transaction"',),
    ),
    "writes do not go through one interface": CorrectionNote(
        resurrects=(
            "the amendment stops retracting point 1's durability claim, leaving the "
            "Decision's own rule -- a guarantee behind one interface can change mechanism "
            "-- reading as satisfied"
        ),
        load_bearing=("held by convention at each call site",),
    ),
    "the sentence above names a tracker that is closed": CorrectionNote(
        resurrects=(
            "the Milestone-5 amendment's standing `#15` cite loses its retraction, so the "
            "ADR hands owed work to a closed tracker with nothing marking it dead"
        ),
        load_bearing=(LIVE_OWNER, "left standing rather than rewritten"),
    ),
    "the count above said": CorrectionNote(
        resurrects=(
            "the amendment's write-method count loses the record that it was wrong when "
            "written rather than stale, and `twelve` reads as a figure that merely aged"
        ),
        load_bearing=(
            "wrong when written",
            "test_adr_0018_claims.py::"
            "test_the_amendment_spells_the_write_method_count_the_port_publishes",
        ),
    ),
    "point 2 said, without the boundary it now carries": CorrectionNote(
        resurrects=(
            "point 2's narrowing loses the measurement behind it, so `for the work that "
            "runs inside that transaction` reads as a clarification rather than as a "
            "boundary eight real two-process runs put there"
        ),
        load_bearing=("crashed in four of the eight", "issues/468"),
    ),
    "the citation of nfr-4": CorrectionNote(
        resurrects=(
            "the Neutral point's `WAL ... lets search keep serving during a rebuild "
            "(NFR-4)` stands unamended, and the Compliance section's `NFR-4 is not "
            "discharged, per the amendment above` points at nothing"
        ),
        load_bearing=("names a requirement that is currently unmet",),
    ),
    "what this point is right about is the canonical store": CorrectionNote(
        resurrects=(
            "the NFR-4 amendment keeps its heading and loses the half that says where the "
            "requirement actually belongs, so the Compliance section's `NFR-4 is not "
            "discharged, per the amendment above. It belongs with the same blue/green "
            "work` cites an amendment that no longer names the work or the milestone, and "
            "an unmet requirement is left with no owner a reader can follow"
        ),
        load_bearing=("is owed to milestone 6's blue/green work and is not discharged here",),
    ),
    "the index's only writer no longer holds": CorrectionNote(
        resurrects=(
            "`theurian index build is today its only writer` is resurrected, in a section "
            "whose own paragraph predicts the second writer that has since landed"
        ),
        load_bearing=("application/withdrawal_purge.py", LIVE_OWNER),
    ),
}

#: What is outside the mapping above, and why. The name says *blockquotes*, and
#: the unit is really a **paragraph**: :func:`_quoted_notes` breaks a blockquote at
#: every bare ``>``, so an anchor holds the paragraph it sits in and nothing else.
#: Both residues below are stated at that granularity.
#:
#: Whole blockquotes deliberately unpinned:
#:
#: - the blue/green note under the index bullet, which records a decision owned by
#:   ADR-0024 rather than correcting a claim this module holds;
#: - the #424 note that corrected Decision point 2, which is a judgement rather
#:   than a category: its deletion removes provenance but resurrects nothing,
#:   because point 2 was corrected *in place* and is held in both directions by
#:   :func:`test_adr_0018_says_the_write_lock_is_a_separate_file_and_names_it` and
#:   :func:`test_adr_0018_does_not_reattach_the_write_lock_to_a_database`.
#:
#: **The Neutral point's NFR-4 amendment used to be on that list, and the reason
#: given was refuted.** It said the amendment is ADR-0022's subject and cited from
#: there; #446's second review round deleted the blockquote and ran the whole
#: suite green, resurrecting "WAL ... (NFR-4)" with the Compliance bullet's "per
#: the amendment above" pointing at nothing. It is pinned above now.
#:
#: Unpinned **paragraphs inside pinned blockquotes**, which is the residue the
#: unit above creates. **Measured by deletion rather than derived by reading**, and
#: that distinction is the finding: #446's third review round found this
#: enumeration naming four when the sweep found six, and an enumeration that says
#: "measured" while it was reasoned is the same defect the pins above exist for.
#: The method, re-runnable: delete each of the paragraphs :func:`_quoted_notes`
#: returns for this record, one at a time, and run this module.
#:
#: At ``155dc08`` that was sixteen paragraphs, of which eight deletions stayed
#: green -- the two whole blockquotes listed above, plus six paragraphs inside a
#: pinned blockquote. One of those six is now pinned: the NFR-4 amendment's body,
#: which the Compliance section cites and which is anchored above on "what this
#: point is right about is the canonical store". Re-run after that pin against
#: this module's twenty-six tests, seven deletions stay green and the residue
#: inside pinned blockquotes is **five**:
#:
#: - the Milestone-5 amendment's "decision is not superseded" paragraph, carrying
#:   the standing ``#15`` cite, and its GOVERNANCE note. That amendment is seven
#:   paragraphs: four anchored above, a fifth held by
#:   :data:`AMENDMENT_COUNT_NOTE`, and these two;
#: - the #468 narrowing's mechanism and not-superseded paragraphs. That blockquote
#:   is three: one anchored, and these two;
#: - the NFR-4 amendment's "this point said WAL is ..." paragraph, which argues
#:   for the retraction its heading states. That blockquote is three, and the other
#:   two are anchored above.
#:
#: They are recorded rather than pinned because each restates
#: something an anchored paragraph or a live pin already carries, and a pin per
#: paragraph would go RED on every ordinary rewrite of a record that is rewritten
#: often.
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

#: What Decision point 2 must NOT say again, now that #468's fix closed the gap
#: it described. The 2026-08-31 narrowing added one boundary clause and one
#: pointer sentence to the numbered point, both inside it and neither a
#: blockquote -- so :data:`CORRECTION_NOTES` cannot reach them and nothing else
#: in this module keys on them.
#:
#: Either one returning -- even as an edit that reads like tightening prose --
#: would mean the single critical section (issue #468) regressed to covering
#: only the transaction again: the promise the boundary clause qualified is
#: what eight real two-process runs falsified before the fix, and what #468's
#: single-lock redesign closed for both `create_database` and
#: `write_active_state`.
#:
#: Held against the isolated point rather than the file, for the same reason as
#: before this was inverted: the narrowing blockquote below the point quotes
#: this exact boundary verbatim, to retract it as dated history, and a
#: file-wide search would read that quotation as the boundary returning.
#:
#: Lowercase, for the reason :data:`CORRECTION_NOTES` records: everything these
#: are matched against has been through ``collapsed``.
POINT_2_BOUNDARY: Final = (
    "for the work that runs inside that transaction",
    "does not serialise",
)

#: The Positive consequence the same narrowing corrected, keyed on the scenario
#: rather than on the retraction, for :func:`_the_one_paragraph_carrying`'s reason.
#: Lowercase, like every other key matched against collapsed prose here.
POSITIVE_CONSEQUENCE: Final = "two concurrent cli invocations"

#: What that bullet must keep saying. It called two concurrent CLI invocations
#: "already safe" until 2026-08-31 and the measurement refused it, so the quoted
#: phrase now has to appear *as something the bullet said*, with the refusal and
#: the owner beside it. Dropping any of the three leaves the bullet reading as a
#: plain statement of fact again -- which is what it was, and what was false.
POSITIVE_CONSEQUENCE_RETRACTION: Final = ('"already safe"', "measured false", "issues/468")

_SOURCE_ROOT: Final = REPO_ROOT / "packages" / "theurian-core" / "src" / "theurian"

_TESTS_ROOT: Final = REPO_ROOT / "packages" / "theurian-core" / "tests"


class SymbolPointer(NamedTuple):
    """One ``module.py::symbol`` reference ADR-0018 hands a reader.

    ``cited`` is the reference exactly as the record writes it, so the pin fails
    when the ADR stops carrying it rather than passing over a pointer that is no
    longer there. ``module`` and ``symbol`` are what it has to resolve to.
    """

    cited: str
    module: pathlib.Path
    symbol: str


#: Every ``module.py::symbol`` reference in ADR-0018, as a literal tuple.
#:
#: **Written out and then held equal to a harvest**, which is not the same thing as
#: choosing one over the other. Neither half is sufficient alone: a harvest by
#: itself checks whatever the record happens to contain, so a reference deleted
#: from the ADR leaves nothing to check and the test green; a hand-written tuple by
#: itself never sees a reference *added*, which is how a fabricated
#: ``application/nonexistent_module.py`` pointer passed #446's third review round.
#: An equality between the two fails in **both** directions and is vacuous in
#: neither: a removed reference fails as a listed entry with nothing to match, an
#: added one fails as a harvested reference nobody resolved, and each names which
#: side moved. The extra fields -- ``module`` and ``symbol`` -- are the part a
#: harvest cannot supply, and they are what turns a reference into a resolution.
#:
#: Nine references, measured 2026-08-31 at ``155dc08`` with
#: ``grep -oE '[A-Za-z0-9_/.]+\\.py::[A-Za-z0-9_]+(\\[[A-Za-z0-9_]+\\])?(\\(\\))?'``
#: over the record and deduplicated -- :data:`_SYMBOL_REFERENCE` is that pattern,
#: and the equality is what keeps this comment's "nine" from being a number kept in
#: step by hand. #446's second review round found the class open in the *rename*
#: direction: renaming ``write_methods`` left the suite green while the ADR went on
#: naming the old symbol, and nothing anywhere read the other eight either.
#:
#: ``write_methods()`` is cited with its call parentheses and the rest without,
#: which is why ``cited`` and ``symbol`` are separate fields rather than one
#: string split on ``::``. It is also why :data:`_SYMBOL_REFERENCE` carries an
#: optional ``()`` group: without it the harvest returns ``write_methods`` where
#: the record writes ``write_methods()``, and the equality would fail on a
#: difference the ADR does not have. ``[CanonicalStore]`` is a pytest
#: parametrisation id, not a symbol, and it is resolved separately against
#: ``ALL_PORTS`` --
#: :func:`test_every_symbol_pointer_in_the_adr_resolves_to_something_live` says
#: how.
ADR_SYMBOL_POINTERS: Final = (
    SymbolPointer(
        "application/setup_steps.py::STEPS",
        _SOURCE_ROOT / "application" / "setup_steps.py",
        "STEPS",
    ),
    SymbolPointer(
        "cli/setup_commands.py::doctor_command",
        _SOURCE_ROOT / "cli" / "setup_commands.py",
        "doctor_command",
    ),
    SymbolPointer(
        "canonical_store_surface.py::write_methods()",
        _TESTS_ROOT / "canonical_store_surface.py",
        "write_methods",
    ),
    SymbolPointer(
        "test_adr_0018_claims.py::"
        "test_the_amendment_spells_the_write_method_count_the_port_publishes",
        _TESTS_ROOT / "unit" / "test_adr_0018_claims.py",
        "test_the_amendment_spells_the_write_method_count_the_port_publishes",
    ),
    SymbolPointer(
        "test_connection_claims.py::"
        "test_the_canonical_store_port_declares_no_single_write_interface",
        _TESTS_ROOT / "unit" / "test_connection_claims.py",
        "test_the_canonical_store_port_declares_no_single_write_interface",
    ),
    SymbolPointer(
        "test_ports.py::test_port_methods_are_annotated[CanonicalStore]",
        _TESTS_ROOT / "unit" / "test_ports.py",
        "test_port_methods_are_annotated",
    ),
    SymbolPointer(
        "tests/integration/test_cli_commands.py::test_apply_is_idempotent",
        _TESTS_ROOT / "integration" / "test_cli_commands.py",
        "test_apply_is_idempotent",
    ),
    SymbolPointer(
        "tests/integration/test_setup_service.py::test_every_specified_step_is_reported",
        _TESTS_ROOT / "integration" / "test_setup_service.py",
        "test_every_specified_step_is_reported",
    ),
    SymbolPointer(
        "tests/unit/test_migration_engine.py::test_reapplying_the_same_set_is_a_no_op",
        _TESTS_ROOT / "unit" / "test_migration_engine.py",
        "test_reapplying_the_same_set_is_a_no_op",
    ),
)

#: One ``module.py::symbol`` reference as ADR-0018 writes it, so the record can be
#: harvested and the harvest held equal to :data:`ADR_SYMBOL_POINTERS`.
#:
#: Faithful to the record rather than normalising it, which is what makes the
#: equality mean something: the optional ``()`` keeps ``write_methods()`` whole and
#: the optional ``[...]`` keeps the parametrisation id on
#: ``test_port_methods_are_annotated[CanonicalStore]``. Comparing on a stripped key
#: instead would let ``cited`` drift on exactly the punctuation the field exists to
#: preserve.
#:
#: Reach: it reads the whole record, code fences included. A ``.py::`` reference
#: written into a command sample is harvested and has to join the list or leave the
#: sample -- a false RED with a visible remedy, chosen over a sweep that silently
#: skips the one place a pointer is most often pasted.
_SYMBOL_REFERENCE: Final = re.compile(
    r"[A-Za-z0-9_/.]+\.py::[A-Za-z0-9_]+(?:\[[A-Za-z0-9_]+\])?(?:\(\))?"
)

#: The one pytest parametrisation id ADR-0018 cites, and the population it has to
#: be a member of. ``test_ports.py`` parametrises over ``ports.ALL_PORTS`` with
#: ``ids=lambda p: p.__name__``, so ``[CanonicalStore]`` names a real test node
#: only while that port is in the list -- and the ADR's whole point 1 is about
#: that port, so a port dropped from ``ALL_PORTS`` would take the record's
#: evidence with it while every symbol above still resolved.
CITED_PARAMETRISATION: Final = "CanonicalStore"

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

#: Every key in this module that is matched against prose ``collapsed`` has
#: lowercased, keyed by the constant a failure has to name.
#:
#: **A capital in any of them is unmatchable, and the failure it produces names the
#: wrong cause.** ``collapsed`` lowercases both the record and, at every call site
#: below, nothing else -- so a key carrying a capital matches no paragraph however
#: intact the record is, and the pin that reads it reports the prose as deleted,
#: gutted or reworded. A reader acts on that by restoring text that is already
#: there. :func:`_assert_the_keys_are_lowercase` refuses the shape *before* the pin
#: runs, so the RED says "this key can never match" instead.
#:
#: The premise covered :data:`CORRECTION_NOTES` alone until #446's third review
#: round, which is why this registry exists: capitalising ``POINT_2_BOUNDARY[0]``
#: at ``155dc08`` produced "Decision point 2 no longer bounds what it serialises"
#: -- a report of prose drift over a record nothing had touched -- because the
#: premise lived in a different test. Ten entries here, eight of them families
#: that guard was reaching past; every one is now checked at the pin that reads
#: it.
#:
#: ``_AMENDMENT_STANDING_CITE`` is the worst of them and is guarded inside
#: :func:`_corrected_bullet`: it is asserted **absent**, so a capital there does
#: not produce a wrong-cause RED at all -- it produces a guard that passes over
#: everything.
#:
#: Reach: opt-in. A key added to this module and not to this mapping is unguarded
#: again, and nothing derives the membership -- there is no property of a string
#: that says it will be matched against collapsed prose.
#: How this module's own docstring states how many paragraphs it pins, as the
#: phrase rather than as a bare number word -- :data:`_SPELLED_COUNT`'s rule
#: applied to this file instead of to the record.
#:
#: It exists because #446's third review round found "measured exactly that, six
#: times" written over a seven-item list, two screens below a docstring that said
#: six and meant it. Both were hand-kept counts over a mapping that grows every
#: round, which is the defect every pin in this file exists to catch, committed in
#: the file that catches it.
_PINNED_PARAGRAPH_COUNT: Final = re.compile(r"\*\*([A-Za-z]+) correction paragraphs are pinned")

_LOWERCASE_REQUIRED: Final[dict[str, tuple[str, ...]]] = {
    "AMENDMENT_COUNT_NOTE": (AMENDMENT_COUNT_NOTE,),
    "CORRECTED_BULLETS": CORRECTED_BULLETS,
    "CORRECTION_NOTES anchors": tuple(CORRECTION_NOTES),
    "CORRECTION_NOTES load_bearing": tuple(
        fragment for note in CORRECTION_NOTES.values() for fragment in note.load_bearing
    ),
    "LIVE_OWNER": (LIVE_OWNER,),
    "POINT_1_REMEASUREMENT": POINT_1_REMEASUREMENT,
    "POINT_2_BOUNDARY": POINT_2_BOUNDARY,
    "POSITIVE_CONSEQUENCE": (POSITIVE_CONSEQUENCE,),
    "POSITIVE_CONSEQUENCE_RETRACTION": POSITIVE_CONSEQUENCE_RETRACTION,
    "_AMENDMENT_STANDING_CITE": (_AMENDMENT_STANDING_CITE,),
}

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

#: The two accepted reasons a file under ``packages/theurian-core/src`` may name
#: a lock at all, as the strings a failure quotes back.
STATE_DATABASE_LOCK: Final = "the ProjectPaths.write_lock state-database family"
SINGLE_INSTANCE_LOCK: Final = "the daemon's single-instance lock"

#: The whole-package lock population, classified. **Every** line under the
#: package source matching :data:`_LOCK_POPULATION_KEY` must sit in one of these
#: files, and a file that is not here is a lock nobody has classified.
#:
#: This is the *reach* complement of :func:`test_no_index_write_path_module_takes_a_lock`,
#: not a second copy of it. That sweep is symbol-derived and therefore narrow --
#: its own docstring records that "a lock added in a module the derivation
#: reaches only through an import is still unseen". This one starts from the
#: filesystem instead, so a lock in a module no index symbol reaches, or in a
#: module that does not exist yet, is inside it. Neither subsumes the other: the
#: narrow sweep moves with a renamed writer, this one does not; this one sees a
#: brand-new module, the narrow one does not.
#:
#: **Classified per file, and that is the stated limit.** A ``fcntl.flock`` added
#: *inside* ``connection.py`` or ``cli/commands.py`` for an index file would be
#: classified as the state-database family and pass -- the narrow sweep and
#: ADR-0024's own point 4 are what cover that shape. What this catches is the
#: realistic one: an index write lock lands where index writes live, which is any
#: file but these five.
#:
#: **The API coverage is narrower than "a lock", and this is where that is
#: written down because it is where ADR-0024's correction sends its reader.** That
#: correction cites this pin as the fact side of *"there is no index write lock"*,
#: so the reach of the key underneath has to be legible from here.
#: :data:`_LOCK_POPULATION_KEY` matches two ``fcntl`` calls (``flock``, ``lockf``),
#: three ``fcntl`` flags (``LOCK_EX``, ``LOCK_SH``, ``LOCK_NB``) and names built on
#: the existing lock's stem (``write_lock``, ``writelock``, unanchored and
#: case-insensitive, which is what catches ``index_write_lock`` and a PascalCase
#: class on the same stem). It matches **no other locking idiom**, and the misses
#: are not hypothetical -- measured at ``7d7d0d4``, three of them are in this
#: package today and this sweep is green over all three:
#:
#: - ``threading.Lock()`` at ``infrastructure/determinism.py:47``;
#: - ``threading.BoundedSemaphore`` at ``mcp/tools.py:505``;
#: - ``BEGIN IMMEDIATE`` at ``infrastructure/sqlite/connection.py:318`` -- the
#:   repo's *own* state-database lock, and invisible here.
#:
#: :func:`test_the_lock_population_key_ignores_names_that_merely_contain_a_token`
#: asserts the first of those must not fire, so the blindness is deliberate at the
#: identifier level and unexamined at the API level. Also uncovered:
#: ``filelock.FileLock``, ``os.O_EXLOCK``, ``BEGIN EXCLUSIVE``, ``msvcrt.locking``,
#: and the ``asyncio`` lock and semaphore types.
#:
#: **So the day an index write lock arrives in one of those idioms, this pin stays
#: green and ADR-0024's citation keeps pointing at a sweep that did not look.**
#: Widening the key is a change to this population -- every new match needs
#: classifying, starting with the three above -- so it is a separate piece of work
#: rather than a line edit, and stating the limit is what keeps the citation
#: honest until it happens.
KNOWN_LOCK_FAMILIES: Final[dict[str, str]] = {
    "application/project_service.py": STATE_DATABASE_LOCK,
    "cli/commands.py": STATE_DATABASE_LOCK,
    "cli/migration_pipeline.py": STATE_DATABASE_LOCK,
    "infrastructure/sqlite/connection.py": STATE_DATABASE_LOCK,
    "daemon/instance.py": SINGLE_INSTANCE_LOCK,
}

#: The two *name* tokens :data:`_LOCK_API_TOKENS` deliberately drops, restored
#: for the population sweep because the two sweeps ask different questions. The
#: narrow one asks "did an index writer take a lock", where ``write_lock`` names
#: a canonical-store property declared in a swept module and would make it RED
#: today against a package with no index lock at all. This one asks "is every
#: lock in the package accounted for", and there ``ProjectPaths.write_lock`` is
#: not noise -- it is one of the two families, and a *new* name built on it
#: (``index_write_lock``, or the PascalCase class name on the same stem) is
#: exactly the arrival to catch.
#:
#: Matched as substrings rather than word-bounded, and only these two.
#: ``\bwrite_lock\b`` does not match inside ``index_write_lock`` -- ``_`` is a
#: word character, so there is no boundary to find -- which would let the new
#: lock arrive under a name derived from the old one and be missed. ADR-0018's
#: own pasted key is an unanchored ``git grep -E``, so this matches what the
#: record's reader would get.
_LOCK_NAME_TOKENS: Final = ("write_lock", "writelock")

#: The API tokens, one wider than :data:`_LOCK_API_TOKENS`: ``LOCK_SH`` and
#: ``LOCK_NB`` are the shared and non-blocking flags, and a lock taken with
#: either is still a lock. They are absent from the narrow sweep's key because it
#: predates this population; adding them there is a change to that pin's
#: contract, so it is not made here.
_LOCK_POPULATION_APIS: Final = ("flock", "lockf", "LOCK_EX", "LOCK_SH", "LOCK_NB")

#: Word-bounded for the APIs, unanchored for the names, case-insensitive
#: throughout -- for the reasons on the two token tuples above.
_LOCK_POPULATION_KEY: Final = re.compile(
    "|".join(
        [
            *(rf"\b{re.escape(token)}\b" for token in _LOCK_POPULATION_APIS),
            *(re.escape(token) for token in _LOCK_NAME_TOKENS),
        ]
    ),
    re.IGNORECASE,
)


def _assert_the_keys_are_lowercase(*families: str) -> None:
    """Refuse a key ``collapsed`` can never match, before the pin reading it runs.

    Named families out of :data:`_LOWERCASE_REQUIRED`, called first in every test
    that reads one. Ordering is the whole point: the assertion below and the pin
    after it fail on the same edit, and only this one names the cause. A pin left
    to fail on its own reports the record as deleted or gutted, which sends a
    reader to restore prose that is sitting there untouched.
    """
    for family in families:
        assert family in _LOWERCASE_REQUIRED, (
            f"`{family}` is not a family in `_LOWERCASE_REQUIRED`, so this premise "
            f"checked nothing: {sorted(_LOWERCASE_REQUIRED)}"
        )

        miscased = sorted(key for key in _LOWERCASE_REQUIRED[family] if key != key.lower())
        assert not miscased, (
            f"these `{family}` keys carry capitals, and every paragraph they are "
            f"matched against has been lowercased by `collapsed`, so each would "
            f"report its prose as missing whatever ADR-0018 says: {miscased}"
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

    **An unterminated fence swallows the rest of the document, and the
    non-empty-population premise does not catch it.** This docstring said it did
    until #446's second review round, which measured the opposite: the fence has
    to sit *below* the paragraph the key selects for the damage to matter, and by
    then the population is non-empty and the premise is satisfied. A phantom
    mitigation plus one stray ``` line left both prose halves green. What catches
    it is :func:`_fence_delimiters` and the parity assertion every caller now
    makes before it reads a paragraph.
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


def _fence_delimiters(text: str) -> list[str]:
    """Every code-fence delimiter line in *text*, in document order.

    The parity of this list is what makes :func:`_without_code_fences` a filter
    rather than a truncation. An odd count means the last fence opens a block that
    never closes, so every line after it is dropped as fenced -- and a scan whose
    population was selected *above* that fence reports the tail as clean without
    ever having read it.
    """
    return [line for line in text.splitlines() if _CODE_FENCE.match(line)]


def _records_with_an_unbalanced_fence(sources: dict[str, str]) -> dict[str, int]:
    """Each named record whose fences do not close, mapped to its delimiter count.

    Split out from the premise below and given a mapping rather than the record
    paths, so
    :func:`test_an_unclosed_fence_eats_the_tail_and_the_parity_count_is_what_sees_it`
    can drive it with synthetic sources. Both shipped records are balanced today,
    so a classifier rewritten to answer "nothing is unbalanced" would be
    indistinguishable from this one against the tree it ships with -- the
    guard-no-input-reaches shape, met one layer in.
    """
    return {
        name: len(fences)
        for name, text in sources.items()
        if len(fences := _fence_delimiters(text)) % 2
    }


def _scanned_nfs_paragraphs() -> dict[str, list[str]]:
    """Each NFS record's selected paragraphs, keyed by filename, fences checked first.

    The parity check is the premise, and it is a premise rather than a test of its
    own because it has to hold *for the scan that is about to run*. Both prose
    halves below read this, so neither can scan a record whose tail
    :func:`_without_code_fences` has silently eaten.
    """
    sources = {record.name: record.read_text(encoding="utf-8") for record in NFS_RECORDS}

    unbalanced = _records_with_an_unbalanced_fence(sources)
    assert not unbalanced, (
        f"these records carry an odd number of code-fence delimiters, so the last "
        f"fence never closes and every line below it is dropped before any key is "
        f"applied: {unbalanced}. A claim re-added under that fence would be reported "
        f"as absent rather than found"
    )

    return {
        record.name: _nfs_paragraphs(sources[record.name], key)
        for record, key in NFS_RECORDS.items()
    }


def _nfs_paragraphs(text: str, key: re.Pattern[str]) -> list[str]:
    """The prose paragraphs of one record that its own *key* selects.

    The key is per-record rather than shared, and :data:`_NFS_WITHOUT_DOCTOR`
    records the measurement behind that. Code blocks are out of the population
    before the key is applied at all.
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


def _top_level_definitions(module: pathlib.Path) -> frozenset[str]:
    """Every name a module binds at module level: functions, classes, constants.

    Parsed rather than imported. Importing ``test_ports.py`` or
    ``test_migration_engine.py`` to ask ``hasattr`` would run their module-level
    code inside this test, and a pin that has side effects on the thing it reads
    is one the next author disables rather than debugs.

    Constants are read as well as callables because a pointer may name one:
    ADR-0018 cites ``application/setup_steps.py::STEPS``, which is an assignment.
    A definition set that held only ``def`` and ``class`` would report that
    reference as broken while it resolves perfectly well.
    """
    tree = ast.parse(module.read_text(encoding="utf-8"))

    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            names.update(target.id for target in node.targets if isinstance(target, ast.Name))
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
    return frozenset(names)


def _the_one_paragraph_carrying(text: str, key: str) -> str:
    """The single paragraph of *text* containing *key*, collapsed.

    :func:`_corrected_bullet`'s rule applied outside the Compliance section, for
    the prose the #468 narrowing corrected in place: the Consequences bullet is an
    ordinary list item and :func:`_paragraphs` already returns a bullet and its
    wrapped continuation as one string.

    Exactly one must match, and the key is chosen from the *scenario* the
    paragraph describes rather than from the correction it now carries -- the same
    reason :data:`CORRECTED_BULLETS` gives. A key built from the retraction stops
    matching the moment the retraction goes, so the paragraph drops out of the
    population instead of failing.
    """
    found = [paragraph for paragraph in _paragraphs(text) if key in paragraph]

    assert len(found) == 1, (
        f"`{key}` no longer identifies exactly one paragraph of ADR-0018: found "
        f"{len(found)}. Zero means the paragraph was deleted or reworded past the "
        f"scenario it is keyed on; more than one means anything read out of it is "
        f"about text this module never chose"
    )
    return found[0]


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

    Exactly one must match, the :func:`_corrected_bullet` rule met again.

    **Zero has two causes and the message names both**, because they call for
    opposite responses. The note may have been *deleted*, which is the drift these
    pins exist to catch; or the anchored sentence may have *moved* -- promoted
    into another blockquote, or reflowed so that :func:`_quoted_notes` returns it
    under a different paragraph -- in which case the record is intact and the
    anchor is what needs correcting. The message said "has no single correction
    note carrying" until #446's second review round, which reads as the first and
    sends a reader to restore prose that is already there.

    More than one means the anchor no longer identifies a single paragraph, and
    anything read out of it is about text this module never chose.
    """
    notes = [note for note in _quoted_notes(text) if anchor in note]

    assert len(notes) == 1, (
        f"`{anchor}` no longer identifies exactly one blockquote paragraph in "
        f"ADR-0018: found {len(notes)}. Zero means the paragraph was deleted, or "
        f"that its sentence moved into another note and this anchor is the thing "
        f"that has to be repointed; more than one means the anchor is shared and "
        f"neither paragraph is the one this pin is holding"
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

    The case premise is here rather than in the callers because this guard is a
    **negative** assertion: a capital in :data:`_AMENDMENT_STANDING_CITE` makes it
    match nothing, and a ``not in`` over a key that matches nothing passes
    silently. The other families in :data:`_LOWERCASE_REQUIRED` at least go RED.
    """
    _assert_the_keys_are_lowercase("_AMENDMENT_STANDING_CITE")

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


def _code_only_lines(source: str) -> list[str]:
    """*source*'s lines with every comment and every prose string blanked out.

    The population below asks whether a module **takes** a lock. A sentence
    *about* a lock is not one, and until PR #498's first review round the sweep
    could not tell them apart: planting the comment ``# The state database is
    guarded by ProjectPaths.write_lock; no index lock exists.`` in
    ``index_store.py`` reported that file as an unclassified lock family. A pin
    that fires on a correct comment is one the next author silences -- and the
    message it fires with used to offer ``KNOWN_LOCK_FAMILIES`` as the remedy,
    which would have exempted the very file a real index lock would land in.

    Blanked rather than dropped, so line numbers survive: a failure has to quote
    ``path:line`` a reader can open.

    **Two classes are removed and a third deliberately is not.**

    - ``COMMENT`` tokens, which is where prose lives in this package -- including
      the ``#:`` attribute docstrings this file is full of.
    - String constants standing alone as a statement: every docstring, plus a
      bare string used as a block comment. Taken from the AST rather than from
      the token stream, because "is this string a docstring" is a question about
      the position of the expression, not about the token.
    - **Not** string literals in general. ``BEGIN IMMEDIATE`` at
      ``infrastructure/sqlite/connection.py:318`` is SQL in a string and it is a
      real lock -- the repo's own state-database idiom. Blanking every string
      would delete exactly the shape a widened key would want to see.

    An unparseable file falls back to its raw lines. A file this function cannot
    read is not evidence of absence, and an absence sweep must fail loud rather
    than quiet.
    """
    lines = source.splitlines()
    spans: list[tuple[int, int, int, int]] = []

    try:
        for token in tokenize.generate_tokens(io.StringIO(source).readline):
            if token.type == tokenize.COMMENT:
                spans.append((*token.start, *token.end))
        for node in ast.walk(ast.parse(source)):
            if not isinstance(node, ast.Expr) or not isinstance(node.value, ast.Constant):
                continue
            constant = node.value
            if isinstance(constant.value, str) and constant.end_lineno is not None:
                spans.append(
                    (
                        constant.lineno,
                        constant.col_offset,
                        constant.end_lineno,
                        constant.end_col_offset or 0,
                    )
                )
    except (SyntaxError, tokenize.TokenError, ValueError):
        return lines

    blanked = list(lines)
    for start_row, start_column, end_row, end_column in spans:
        for row in range(start_row, min(end_row, len(blanked)) + 1):
            line = blanked[row - 1]
            start = start_column if row == start_row else 0
            end = end_column if row == end_row else len(line)
            blanked[row - 1] = line[:start] + " " * (end - start) + line[end:]
    return blanked


def _lock_population(root: pathlib.Path) -> dict[str, tuple[str, ...]]:
    """Every ``path:line`` under *root* whose **code** names a lock, by module path.

    Walks the filesystem rather than a symbol graph, which is the whole reason
    this population exists beside :func:`_index_write_path_modules`: a lock in a
    module nothing imports yet is still a lock, and a symbol walk cannot see one.

    Reads each file through :func:`_code_only_lines`, so a comment or a docstring
    *mentioning* a lock is not counted as one. That distinction is the finding
    this function was corrected for, and its own reasoning is on that helper.

    Keyed by the path *relative to root* so the result reads as the entries of
    :data:`KNOWN_LOCK_FAMILIES` and a failure quotes something a reader can grep.
    Line-granular rather than file-granular because a file that is expected to
    match tells the reader nothing about *which* line arrived.
    """
    population: dict[str, list[str]] = {}
    for path in sorted(root.rglob("*.py")):
        relative = path.relative_to(root).as_posix()
        source = path.read_text(encoding="utf-8")
        for number, line in enumerate(_code_only_lines(source), start=1):
            if _LOCK_POPULATION_KEY.search(line):
                population.setdefault(relative, []).append(f"{relative}:{number}")
    return {name: tuple(lines) for name, lines in population.items()}


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

    The paragraphs come from :func:`_scanned_nfs_paragraphs`, which asserts each
    record's code fences balance before it selects anything -- an odd fence count
    truncates a record silently, and a truncated record has no statement of the
    absence for this half to find.
    """
    scanned = _scanned_nfs_paragraphs()
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

    **A non-empty population is not enough, and #446's second review round proved
    it.** The selected paragraph sits near the top of each record, so a stray
    fence *below* it truncates everything after while leaving this premise
    satisfied -- a phantom mitigation plus one unmatched ``` line was measured
    green here. :func:`_scanned_nfs_paragraphs` asserts fence parity per record
    first, so that shape now fails naming the file.
    """
    scanned = _scanned_nfs_paragraphs()

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


def test_an_unclosed_fence_eats_the_tail_and_the_parity_count_is_what_sees_it() -> None:
    """RED means an unbalanced fence can blind a prose scan again.

    Driven by synthetic input because neither shipped record can drive it:
    measured 2026-08-31, ``README.md`` carries twenty-two fence delimiters and
    ADR-0018 none, both even, so a parity check that always answered "balanced"
    would be indistinguishable from a working one.

    **Two halves, and the second is why the first is worth asserting.** The
    classifier the premise actually calls is driven on both answers;
    :func:`_without_code_fences` is then shown actually losing the sentence under
    the unmatched fence. A test that asserted only the arithmetic would pin a
    number with no consequence attached, and the consequence is the finding:
    #446's second review round appended a phantom mitigation plus one stray ```
    line to a record and both prose halves stayed green, because the paragraph
    their key selects sits above the fence and the population premise was
    satisfied before the damage began.

    :func:`_records_with_an_unbalanced_fence` is called rather than
    :func:`_fence_delimiters`, so a premise rewritten to classify nothing as
    unbalanced fails here and not only in the record that meets it next.

    The truncating sample is that shape, minimised -- the claim the negative half
    exists to refuse, placed below a fence that never closes.
    """
    balanced = (
        "Nothing detects that it is.\n\n```sh\ntheurian doctor\n```\n\nNFS stays unsupported.\n"
    )
    truncating = (
        "Nothing detects that it is.\n\n```sh\ntheurian doctor\n\n`doctor` warns about NFS.\n"
    )

    assert _records_with_an_unbalanced_fence({"balanced.md": balanced}) == {}, (
        "the fence premise reads a closed block as unbalanced, so every record "
        "would fail it for a reason that is not there"
    )
    assert _records_with_an_unbalanced_fence({"truncating.md": truncating}) == {
        "truncating.md": 1
    }, (
        "the fence premise no longer sees an unmatched fence, so it passes over the "
        "shape it was written for and the scan below it goes back to reading a "
        "truncated document"
    )

    assert "NFS stays unsupported" in _without_code_fences(balanced), (
        "the fence filter drops prose below a closed block, so the parity premise "
        "would be guarding a truncation that happens either way"
    )
    assert "warns about NFS" not in _without_code_fences(truncating), (
        "the fence filter no longer swallows the tail after an unmatched fence, so "
        "the parity premise has nothing left to protect -- and this test, not the "
        "premise, is the one to delete if that is now true"
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

    **Both halves are scoped to the appended sentence, and that is #446's second
    review round.** Reading the whole document made this test fail for two
    unrelated reasons: a widened key, which is what it is about, and a genuine
    phantom mitigation added to README's own prose, which is
    :func:`test_no_record_of_the_nfs_exclusion_claims_anything_warns_about_it`'s.
    A diagnostic that fires on someone else's defect sends the next reader at the
    key when the key is fine. Filtering to :data:`_PROBE_SENTENCE` leaves each
    half answering exactly one question -- and it strengthens the ADR half, which
    previously asserted only that *some* claim was found and would have been
    satisfied by a real one.
    """
    a_true_doctor_sentence = f"\n\n`theurian doctor` {_PROBE_SENTENCE}.\n"
    readme = README.read_text(encoding="utf-8") + a_true_doctor_sentence

    claims = _detection_claims_without_denial(_nfs_paragraphs(readme, NFS_RECORDS[README]))
    from_the_probe = [claim for claim in claims if _PROBE_SENTENCE in claim]

    assert not from_the_probe, (
        f"README's NFS scan fires on a true sentence about `doctor`, so an ordinary "
        f"addition to the quick start goes RED as the phantom NFS mitigation "
        f"returning: {from_the_probe}"
    )

    adr = ADR_0018.read_text(encoding="utf-8") + a_true_doctor_sentence
    adr_claims = _detection_claims_without_denial(_nfs_paragraphs(adr, NFS_RECORDS[ADR_0018]))

    assert any(_PROBE_SENTENCE in claim for claim in adr_claims), (
        f"ADR-0018's key no longer selects a paragraph on `doctor` alone, so the "
        f"record where the phantom mitigation actually lived is no longer swept for "
        f"it returning: {adr_claims}"
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
    _assert_the_keys_are_lowercase("AMENDMENT_COUNT_NOTE")

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
    bullet that no longer retracts anything. #446's three review rounds measured
    exactly that, seven times: the three notes in the first round, in the second
    the NFR-4 amendment heading and the Milestone-5 amendment's two opening
    retraction paragraphs, and in the third the NFR-4 amendment's body -- each
    deletable with every other test in this file green. The eighth anchor, the
    #468 narrowing, is not one of the seven: it is pinned as new prose rather than
    as a measured deletion, having arrived on this branch already pinned.

    :data:`CORRECTION_NOTES` names, per anchor, what the deletion would silently
    undo, and the failure message prints it -- so a RED here says which claim just
    came back rather than only that a string is missing.

    **Deletion is all this half asserts, and the ambiguity check is why it can
    say so.** An anchor matching two paragraphs would report both as present while
    identifying neither, so the pin would be about text nobody chose;
    :func:`test_every_correction_note_still_carries_what_makes_it_a_correction`
    holds the other half, that the paragraph still says what it exists to say.

    **The case premise comes first, because it was hit while this pin was
    written.** ``collapsed`` lowercases, so an anchor carrying a capital matches
    nothing and the deletion assertion reports a note that is sitting in the record
    untouched -- a RED naming the wrong cause, which is the one a reader acts on
    wrongly. It lived inline here until #446's third review round found the same
    shape unguarded on eight further families; it is now
    :func:`_assert_the_keys_are_lowercase` over :data:`_LOWERCASE_REQUIRED`, called
    from each pin that reads a family.
    """
    _assert_the_keys_are_lowercase("CORRECTION_NOTES anchors", "CORRECTION_NOTES load_bearing")

    text = ADR_0018.read_text(encoding="utf-8")
    notes = _quoted_notes(text)

    assert notes, (
        "no blockquote was parsed out of ADR-0018, so this test would report every "
        "correction note as deleted, or none"
    )

    matched = {anchor: [note for note in notes if anchor in note] for anchor in CORRECTION_NOTES}

    deleted = {
        anchor: CORRECTION_NOTES[anchor].resurrects
        for anchor, found in matched.items()
        if not found
    }

    assert not deleted, (
        f"these anchors match no blockquote paragraph in ADR-0018 -- either the "
        f"paragraph was deleted, or its sentence moved and the anchor is what has to "
        f"be repointed. If it is the first, the claim each one retracts is standing "
        f"again: {deleted}"
    )

    ambiguous = {anchor: len(found) for anchor, found in matched.items() if len(found) > 1}

    assert not ambiguous, (
        f"these anchors now match more than one blockquote paragraph, so neither "
        f"this pin nor the content one below can say which paragraph it is holding: "
        f"{ambiguous}"
    )


def test_this_modules_count_of_its_own_pins_is_derived_from_the_mapping() -> None:
    """RED means this module's prose counts its own pins wrong.

    The count of pinned paragraphs is stated three times in this file and was kept
    in step by hand until #446's third review round, which found "six times" over a
    seven-item list contradicting a module docstring two screens up. A number a
    person maintains against a mapping that grows every round is the exact defect
    :func:`test_the_amendment_spells_the_write_method_count_the_port_publishes`
    holds ADR-0018 to -- so the same rule is turned on this file.

    The opening sentence is the one derived: the spelled number in "Eight
    correction paragraphs are pinned" must equal ``len(CORRECTION_NOTES)``. Adding
    an entry without moving the sentence fails here, naming both sides.

    Reach: one sentence, and only the total. The per-round breakdown beside it --
    three, three and one, with the #468 narrowing pinned as new prose rather than
    as a measured deletion -- is history, and nothing in the code knows which round
    pinned which anchor. The premises come first for
    :data:`AMENDMENT_COUNT_NOTE`'s reason: a docstring that stopped spelling the
    number at all would leave this comparing nothing.
    """
    assert __doc__ is not None, (
        "this module has no docstring, so the claim this test holds is about nothing"
    )

    spelled = _PINNED_PARAGRAPH_COUNT.findall(__doc__)
    assert len(spelled) == 1, (
        f"this module's docstring no longer states its pinned-paragraph count as one "
        f"spelled number, so nothing holds it against `CORRECTION_NOTES`: {spelled}"
    )
    assert spelled[0].lower() in _SPELLED_NUMBERS, (
        f"the docstring spells its pinned-paragraph count as `{spelled[0]}`, which is "
        f"not a number this pin can read"
    )

    assert _SPELLED_NUMBERS[spelled[0].lower()] == len(CORRECTION_NOTES), (
        f"this module's docstring says `{spelled[0]}` correction paragraphs are "
        f"pinned; `CORRECTION_NOTES` holds {len(CORRECTION_NOTES)}: "
        f"{sorted(CORRECTION_NOTES)}"
    )


def test_every_correction_note_still_carries_what_makes_it_a_correction() -> None:
    """RED means a correction note was gutted or rewritten past its own retraction.

    The other half, and it is not the first restated. #446's second review round
    measured two moves that the presence pin alone admits: a note **cut down to
    its anchor fragment**, and a note **rewritten around the anchor** to assert
    the diagnosis the correction had retracted -- "the count above said *twelve*,
    merely stale by one and refreshed here" passed while saying the opposite of
    what the note exists to say.

    :data:`CorrectionNote.load_bearing` is what closes both: the content each
    paragraph's correction turns on, required in that same paragraph. For the
    count note it is the wrong-when-written distinction and the pointer to the
    test that holds the number against the live port -- exactly the two things a
    "merely stale" rewrite drops.

    Reach: these are string fragments. A paragraph reworded everywhere except
    them still passes, and no presence pin can do better;
    :data:`_UNPINNED_BLOCKQUOTES` records the paragraph-level residue beside it.

    **Scoped to the anchor's own paragraph.** :func:`_correction_note` returns one
    paragraph, so a fragment belonging to a sibling paragraph of the same
    blockquote fails here -- which is why the NFR-4 amendment's body carries its
    own anchor rather than hanging off the heading's.
    """
    _assert_the_keys_are_lowercase("CORRECTION_NOTES anchors", "CORRECTION_NOTES load_bearing")

    text = ADR_0018.read_text(encoding="utf-8")

    gutted = {
        anchor: missing
        for anchor, note in CORRECTION_NOTES.items()
        if (missing := [f for f in note.load_bearing if f not in _correction_note(text, anchor)])
    }

    assert not gutted, (
        f"these correction notes have lost the content their correction rests on, "
        f"so each is now a heading over a claim it no longer retracts: {gutted}"
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
    _assert_the_keys_are_lowercase("CORRECTED_BULLETS", "POINT_1_REMEASUREMENT")

    bullet = _corrected_bullet(ADR_0018.read_text(encoding="utf-8"), POINT_1_BULLET)

    missing = sorted(anchor for anchor in POINT_1_REMEASUREMENT if anchor not in bullet)

    assert not missing, (
        f"the point-1 bullet no longer carries its re-measurement: {missing}. "
        f"Without it the bullet's Milestone-5 sentence stands alone, and that "
        f"measurement was falsified on 2026-08-31 in two spellings of three"
    )


def test_decision_point_2_no_longer_carries_the_narrowing() -> None:
    """RED means point 2 carries the narrowing again, which #468's fix closed.

    The claim as it stood -- two concurrent ``theurian migrate apply`` invocations
    serialise and the loser becomes a no-op -- was measured false by #446's second
    review round on eight real two-process runs: the loser crashed in four, on
    ``create_database`` before the transaction opens and ``write_active_state``
    after it commits, both outside the lock. #468 closed the gap: a single
    critical section now spans creation, the transaction, the provenance record
    and the pointer publish, so the promise covers everything ``migrate apply``
    writes, not only the migration content
    ([#468](https://github.com/theurian/theurian/issues/468)).

    So the 2026-08-31 boundary clause is no longer true, and its return -- even
    as an edit that reads like tightening prose -- would mean the fix
    regressed. The narrowing blockquote below the point is left standing as
    dated history and is pinned separately in :data:`CORRECTION_NOTES`; this
    holds the sentence a reader who stops at the Decision actually reads,
    which is the one no blockquote below it can correct on its own.

    Scoped to the isolated point rather than to the file, because that blockquote
    quotes the old boundary verbatim in order to retract it as history -- the
    trap :func:`_decision_point_two` exists for, met again.
    """
    _assert_the_keys_are_lowercase("POINT_2_BOUNDARY")

    point = _decision_point_two(ADR_0018.read_text(encoding="utf-8"))

    present = sorted(fragment for fragment in POINT_2_BOUNDARY if fragment in point)

    assert not present, (
        f"Decision point 2 carries the narrowing #468's fix closed: {present}. The "
        f"single critical section now spans creation, the transaction, the "
        f"provenance record and the pointer publish, so the promise covers the "
        f"whole write, not only what runs inside the transaction (#468)"
    )


def test_the_positive_consequence_records_that_already_safe_was_measured_false() -> None:
    """RED means the ADR drops the dated retraction that made the bullet honest.

    The Positive bullet is the second place a reader meets the claim #468
    falsified, and it is the place they meet it as a *reason to rely on it*: "two
    concurrent CLI invocations are already safe, which is a real scenario -- an
    editor plugin and a terminal". Narrowing the Decision and leaving this bullet
    alone would have left the ADR arguing for the behaviour on one screen and
    retracting it on another, which is the two-halves-disagreeing defect this
    module was written for.

    Three fragments, and each carries a different part of the retraction: the
    quoted phrase, so the bullet says what it *said*; the refusal, so a reader
    knows it was measured rather than reconsidered; and the owner, so the
    engineering that would restore the claim is findable. Any one of them dropped
    leaves the bullet reading as a plain statement of fact again -- as if
    "already safe" had never been checked, rather than checked, found false,
    and (since #468's fix) checked again and found true. The bullet now also
    carries a closure sentence past these three fragments -- "closed on
    2026-09-01 ... 'already safe' is true again" -- but nothing here pins that
    sentence specifically: the retraction is the permanent record, kept
    exactly as measured, and the closure is additive prose this test does not
    need to hold for the bullet to stay honest.

    Not a blockquote, so :data:`CORRECTION_NOTES` cannot reach it, and outside the
    Compliance section, so :func:`_corrected_bullet` cannot either.
    """
    _assert_the_keys_are_lowercase("POSITIVE_CONSEQUENCE", "POSITIVE_CONSEQUENCE_RETRACTION")

    bullet = _the_one_paragraph_carrying(ADR_0018.read_text(encoding="utf-8"), POSITIVE_CONSEQUENCE)

    missing = sorted(
        fragment for fragment in POSITIVE_CONSEQUENCE_RETRACTION if fragment not in bullet
    )

    assert not missing, (
        f"the Positive consequence no longer records that `already safe` was "
        f"measured false: {missing}. The bullet is where a reader is told to rely "
        f"on the behaviour, and four of eight real two-process runs refused it (#468)"
    )


def test_every_symbol_pointer_in_the_adr_resolves_to_something_live() -> None:
    """RED means ADR-0018 sends a reader to a symbol that is not there.

    The record's evidence is its pointers. "The count above is held against the
    port rather than by hand" is worth nothing to a reader who cannot find the
    test it names, and a rename is exactly the edit that breaks such a pointer
    without touching the record: #446's second review round renamed
    ``write_methods`` while ADR-0018 went on citing the old symbol, and the
    rename was re-run here on 2026-08-31 against the pre-fix tree with every test
    in this module, ``test_connection_claims.py`` and ``test_ports.py`` green.
    Nothing anywhere read the other eight references either.

    **Three assertions, in the order a failure should be read.** The population
    premise comes first, and it is an **equality** rather than a containment: the
    references :data:`_SYMBOL_REFERENCE` harvests out of the record must be exactly
    the set :data:`ADR_SYMBOL_POINTERS` lists. A containment held only one
    direction, and #446's third review round measured the open one: appending
    ``application/nonexistent_module.py::totally_made_up`` to the Compliance
    section left all twenty-five tests of this module green at ``155dc08``,
    because a fabricated reference is nobody's list entry and so nothing looked
    for it. Re-run against this assertion, the same append goes RED. Then the
    files, so a moved module fails naming the path rather than raising out of
    :func:`_top_level_definitions`. Then the symbols.

    The parametrisation id is the fourth, and it is the same claim rather than a
    different one: ``test_ports.py::test_port_methods_are_annotated[CanonicalStore]``
    resolves to a real test node only while ``CanonicalStore`` is in
    ``ALL_PORTS``, which is what makes ``[CanonicalStore]`` a pytest id and not
    decoration.

    Reach: a name existing at module level is not the same as a test that runs --
    a function collected under a different node id, or skipped, satisfies this.
    What it refuses is the pointer that resolves to nothing at all, which is the
    failure a rename produces. The equality's own reach is on
    :data:`_SYMBOL_REFERENCE`: it reads the record whole, code fences included.
    """
    adr = ADR_0018.read_text(encoding="utf-8")

    harvested = set(_SYMBOL_REFERENCE.findall(adr))
    listed = {pointer.cited for pointer in ADR_SYMBOL_POINTERS}

    assert harvested == listed, (
        f"ADR-0018's `module.py::symbol` references and this module's list have "
        f"diverged. In the record but unlisted, so nothing resolves them: "
        f"{sorted(harvested - listed)}. Listed but no longer in the record, so each "
        f"entry checks a pointer the ADR does not make: {sorted(listed - harvested)}"
    )

    missing_files = sorted(
        pointer.cited for pointer in ADR_SYMBOL_POINTERS if not pointer.module.is_file()
    )
    assert not missing_files, f"ADR-0018 points at modules that are not on disk: {missing_files}"

    unresolved = sorted(
        pointer.cited
        for pointer in ADR_SYMBOL_POINTERS
        if pointer.symbol not in _top_level_definitions(pointer.module)
    )
    assert not unresolved, (
        f"ADR-0018 names symbols that do not exist in the modules it names: "
        f"{unresolved}. Whichever side moved, the record and the code have to be "
        f"brought back into step -- a pointer that resolves to nothing is evidence "
        f"a reader cannot check"
    )

    port_ids = {port.__name__ for port in ports.ALL_PORTS}
    assert CITED_PARAMETRISATION in port_ids, (
        f"`{CITED_PARAMETRISATION}` is no longer a `test_ports.py` parametrisation "
        f"id, so ADR-0018's `test_port_methods_are_annotated[{CITED_PARAMETRISATION}]` "
        f"names a test node that is never collected: {sorted(port_ids)}"
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
    _assert_the_keys_are_lowercase("CORRECTED_BULLETS", "LIVE_OWNER")

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
    _assert_the_keys_are_lowercase("CORRECTED_BULLETS")

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


def test_the_lock_population_key_catches_the_fcntl_spellings_and_the_derived_names() -> None:
    """RED means the population sweep stopped matching, so the pin below passes over nothing.

    Driven by synthetic source for the reason
    :func:`test_the_lock_api_sweep_catches_a_taken_lock_in_synthetic_source` is:
    every real match today sits in an accepted file, so an implementation that
    returned nothing at all would look identical to a working one.

    **This test was named ``...catches_every_shape_a_new_lock_could_take`` and it
    never held that**, which PR #498's first review round caught. It holds the four
    arrivals below and no others; :data:`KNOWN_LOCK_FAMILIES` records what the key
    is blind to, including three idioms already in this package. The name is now
    what the samples are, because the old one was read as coverage by whoever
    cited the pin next -- and a name is the only part of a test most readers ever
    compare against a claim.

    The samples are four shapes a new lock could arrive in -- the two ``fcntl``
    spellings with each flag, a lock path named after the one that exists, and a
    class named after the class that exists. The last two are the ones word
    boundaries would miss: ``\\bwrite_lock\\b`` finds nothing inside
    ``index_write_lock``, because ``_`` is a word character and there is no
    boundary there to find. The class sample is also what the case-insensitive
    half is for: a new lock class is spelled in PascalCase, and the token it has
    to be matched against is not.

    **The class name is derived rather than typed, and neither half of that is
    style.** Deriving it -- PascalCase from the snake_case token the key already
    carries -- is what makes this leg unable to pass vacuously: the sample and the
    token that has to match it move together, so dropping the case-insensitive
    spelling from :data:`_LOCK_NAME_TOKENS` takes this RED instead of leaving a
    hand-typed string matching a hand-typed pattern. And it keeps the literal out
    of this file, which matters because
    ``test_connection_claims.py::test_the_only_test_that_constructs_the_write_lock_runs_in_one_process``
    keys on the lock class's PascalCase name as a plain substring over the whole
    test tree and permits exactly one member. That guard is about tests that
    *construct* a lock; this one only describes a string. For the same reason the
    name is absent from this module's docstrings -- do not spell it back in.
    """
    a_new_lock_class = "".join(part.capitalize() for part in _LOCK_NAME_TOKENS[0].split("_"))
    arrivals = (
        "    fcntl.flock(handle.fileno(), fcntl.LOCK_EX)",
        "    fcntl.lockf(handle.fileno(), fcntl.LOCK_SH | fcntl.LOCK_NB)",
        "        index_write_lock = self._paths.index_write_lock",
        f"class Index{a_new_lock_class}:",
    )

    unmatched = [line for line in arrivals if not _LOCK_POPULATION_KEY.search(line)]

    assert not unmatched, (
        f"the population key no longer matches these lock arrivals, so a new index "
        f"lock written this way would land outside every family and be reported as "
        f"an empty population: {unmatched}"
    )


def test_the_lock_population_key_ignores_names_that_merely_contain_a_token() -> None:
    """RED means the sweep fires on ordinary code, and gets deleted for crying wolf.

    The API half is word-bounded precisely so these pass. ``lockf`` sits inside
    ``blockfile`` and ``clockface``, and ``lock_path`` and ``self._lock`` are
    names any module may use for something that is not an OS advisory lock. A
    substring rule over the API tokens would report all five, and a pin that fires
    on unrelated true code is one the next author silences rather than reads.
    """
    innocent = (
        "blockfile = 2",
        "clockface = render()",
        "lock_path = paths.runtime / 'write.lock'",
        "self._lock = threading.Lock()",
        "unlocked = True",
    )

    firing = [line for line in innocent if _LOCK_POPULATION_KEY.search(line)]

    assert not firing, (
        f"the population key fires on identifiers that merely contain one of its "
        f"tokens, so every new file using one of these ordinary names would be "
        f"reported as an unclassified lock: {firing}"
    )


def test_a_lock_named_only_in_prose_is_not_counted_as_a_lock_that_was_taken() -> None:
    """RED means the sweep is back to reporting sentences about locks as locks.

    PR #498's first review round planted this comment in ``index_store.py`` -- ``#
    The state database is guarded by ProjectPaths.write_lock; no index lock
    exists.`` -- and the whole-package pin reported that file as an unclassified
    lock family. Two things were wrong with that, and the second is the worse one.
    The sweep's subject is whether a module *takes* a lock, and a correct sentence
    saying it does not is the opposite of the arrival it watches for. And the
    failure message offered ``KNOWN_LOCK_FAMILIES`` as the remedy, which exempts a
    whole *file* -- so the natural way to silence a false alarm was to blind the
    sweep in exactly the file a real index write lock would land in. The message
    now separates the three remedies and refuses that one by name.

    Synthetic source, carrying both halves in one module. The control comes first
    and it is what makes the claim mean anything: every prose sample here is
    reachable by the key, so "nothing fired" cannot be satisfied by samples the key
    was never going to match.

    The live ``fcntl.flock`` call carries a *trailing* comment that also names a
    lock, which is the case that decides the implementation: a filter that dropped
    any line containing ``#`` would take the call with it, and the last assertion
    is what says the surviving match is the call rather than the comment.
    """
    source = "\n".join(
        (
            '"""The state database is guarded by ProjectPaths.write_lock."""',
            "",
            "import fcntl",
            "",
            "#: No index write_lock is taken anywhere in this module.",
            'RUNTIME = "runtime"',
            "",
            "",
            "def acquire(handle):",
            '    """Takes ProjectPaths.write_lock, never an index lock.',
            "",
            "    A second paragraph mentioning fcntl.flock and LOCK_EX.",
            '    """',
            "    # The state database is guarded by ProjectPaths.write_lock; no index lock.",
            "    fcntl.flock(handle.fileno(), fcntl.LOCK_EX)  # the write_lock, taken here",
            "    return handle",
        )
    )
    prose_lines = (1, 5, 10, 12, 14)
    code_line = 15

    raw = [
        number
        for number, line in enumerate(source.splitlines(), start=1)
        if _LOCK_POPULATION_KEY.search(line)
    ]
    firing = [
        number
        for number, line in enumerate(_code_only_lines(source), start=1)
        if _LOCK_POPULATION_KEY.search(line)
    ]

    assert raw == [*prose_lines, code_line], (
        f"the positive control failed: read raw, this sample must fire on every "
        f"prose line {prose_lines} and on the call at {code_line}, which is what "
        f"makes the assertion below a measurement rather than a sample the key was "
        f"never going to match. Fired on {raw}"
    )

    assert firing == [code_line], (
        f"a lock named in a comment or a docstring is being counted as a lock that "
        f"was taken. That reports a correct sentence as an unclassified family, and "
        f"the remedy a reader reaches for -- an entry in `KNOWN_LOCK_FAMILIES` -- "
        f"exempts the whole file from the sweep. Fired on {firing}"
    )

    assert _LOCK_POPULATION_KEY.findall(_code_only_lines(source)[code_line - 1]) == [
        "flock",
        "LOCK_EX",
    ], (
        "the surviving match on the call line is its trailing comment rather than "
        "the call: blanking must remove the comment and leave the code, or a real "
        "lock taken on a commented line is reported for the wrong reason"
    )


def test_every_lock_in_the_package_belongs_to_one_of_the_two_known_families() -> None:
    """RED means a lock arrived that nobody has classified -- and two records must move.

    The fact side of ADR-0018's Compliance bullet, at the reach that bullet's own
    pasted key has: *"There is no index write lock in the package ... every one of
    them the canonical ``ProjectPaths.write_lock`` or the daemon's single-instance
    lock, and none of them in an index write path."* That is a claim about the
    **whole package**, and until this pin the only fact half in the suite was the
    symbol-derived sweep above, whose own docstring records that it does not reach
    a module no index symbol imports.

    It is also the fact side of the reconciliation
    https://github.com/theurian/theurian/issues/445 is doing on ADR-0024 point 4,
    which asserts that a purge "goes through the same single-writer interface as a
    build, and there is exactly one such interface", and that "publishing is a
    separate step that takes the index write lock". Measured 2026-09-01 against
    ``ec0dbcd``: there is no index write lock for publishing to take -- the pointer
    swap is a write-to-temp plus ``os.replace`` and the lock file is never created
    -- and ``application/withdrawal_purge.py`` says so in its own source. So the
    day one *is* taken, that ADR's correction has to be re-decided, and this test
    is what says so.

    **Three premises before the search**, because a search for an absence reports
    success when it searches nothing:

    1. the source root resolves and holds Python files;
    2. every accepted file still exists -- a rename must fail naming itself rather
       than quietly shrinking the accepted set to four;
    3. every accepted file still *matches*. An entry that has stopped matching is
       an allowlist that has gone stale, and a stale allowlist widens what the
       conclusion below covers without anyone deciding to.

    Only then is the population classified, and what is left is what nobody has
    accounted for.
    """
    assert _SOURCE_ROOT.is_dir(), f"the package source root does not resolve: {_SOURCE_ROOT}"
    modules = list(_SOURCE_ROOT.rglob("*.py"))
    assert modules, (
        f"there are no Python files under {_SOURCE_ROOT}, so the sweep below would "
        f"report a landed lock as an absence"
    )

    missing = sorted(name for name in KNOWN_LOCK_FAMILIES if not (_SOURCE_ROOT / name).is_file())
    assert not missing, (
        f"these accepted lock files no longer exist, so whatever they held has moved "
        f"somewhere this test would report as a new lock -- or has stopped being "
        f"classified at all: {missing}"
    )

    population = _lock_population(_SOURCE_ROOT)
    silent = sorted(set(KNOWN_LOCK_FAMILIES) - set(population))
    assert not silent, (
        f"these files are accepted as lock families but no longer name a lock, so "
        f"the accepted set is stale and covers more than it has evidence for. Either "
        f"the lock moved, or the entry should go: {silent}"
    )

    unclassified = {
        name: lines for name, lines in population.items() if name not in KNOWN_LOCK_FAMILIES
    }

    assert not unclassified, (
        f"a lock arrived outside {STATE_DATABASE_LOCK} and {SINGLE_INSTANCE_LOCK}: "
        f"{unclassified}. Three remedies, and they are not interchangeable. (1) If it "
        f"is an index write lock, ADR-0018's Compliance bullet must stop saying there "
        f"is none and the work it hands to {LIVE_OWNER} has landed; ADR-0024 point 4's "
        f"index single-writer claim has to be re-decided against it rather than "
        f"corrected as pending. (2) If it is a lock of some other kind, classify the "
        f"file in `KNOWN_LOCK_FAMILIES` with the reason. (3) If it is not a lock at "
        f"all -- a message or an identifier that merely says one -- reword the "
        f"mention, and do NOT take remedy (2): an entry there exempts the whole file, "
        f"so allowlisting a file for a sentence about a lock is what makes a real lock "
        f"landing in it later pass unseen. Comments and docstrings are already "
        f"excluded by `_code_only_lines`, so a prose match here is in live code"
    )
