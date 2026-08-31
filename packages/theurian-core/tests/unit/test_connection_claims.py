"""What the SQLite write path's two modules claim about themselves.

``connection.py`` and ``store.py`` both asserted the single-interface claim that
ADR-0018's Milestone 5 amendment had already retracted, and
https://github.com/theurian/theurian/issues/434 corrected both in place.

- ``connection.py``: the module docstring said "Writes go through one interface
  holding an OS advisory lock"; ``write_transaction``'s said it was "The only way
  to write" and that "``CanonicalStore`` exposes no connection, so the
  single-writer guarantee lives in one place".
- ``store.py``: three docstrings, one face each. The module's said "Writes happen
  only inside :func:`write_transaction`"; ``SqliteCanonicalStore``'s said
  "Read-only by construction: every write goes through :class:`SqliteWriter`,
  which requires an open write transaction"; ``SqliteWriter``'s said "There is no
  way to build one otherwise, so the single-writer guarantee cannot be
  sidestepped by reaching for this class".

The last of those was not merely overstated but demonstrably false --
``SqliteWriter(sqlite3.connect(":memory:"))`` builds one from any connection,
with no lock and no transaction -- and the two above it rest on it.

The mechanism was never the false part. ``write_transaction`` does take an OS
advisory flock on ``lock_path`` and hold it for the transaction. What was false
is the *durability* argument built on top of it -- that the guarantee sits
behind one interface and can therefore change mechanism in Milestone 3 without
touching application code -- because the ``CanonicalStore`` port publishes its
write methods directly, so a caller can reach a write without entering
``write_transaction`` at all. ADR-0018's closing sentence names the difference:
a guarantee implemented behind a single interface can change mechanism; a
guarantee implemented by convention at each call site cannot.

**A docstring that says a guarantee is stronger than it is, is read as a licence
not to check.** So the correction is held here in both directions, over both
files. One population, one pattern: the claim moved between these two modules
once already -- ``connection.py`` was corrected a commit before ``store.py`` was
even read -- and a scan that covered one file would have reported the write path
as clean while three faces of the same claim stood in the other.

-- The prose ---------------------------------------------------------------

The retracted shapes are refused, and the shape the correction landed --
"held by convention at each call site" -- is required of **both** modules. Both
halves are needed and neither implies the other: a rewrite that deletes the
docstrings entirely makes no false claim and would pass the negative test while
leaving a reader of ``write_transaction`` or of ``SqliteWriter`` with nothing
that says the lock is held by convention rather than by construction.

The scan reads **docstrings only**, parsed out of the source with :mod:`ast`, so
a comment or an error message quoting the retracted wording to explain it does
not fire. Sentences carrying a denial before the match are left alone, which is
what lets the corrected module docstring say "exclusivity is held by convention
at each call site rather than behind a single interface" without punishing the
wording that states the fix.

**Measured escapes, recorded rather than chased** (against the compiled pattern,
2026-08-31, re-measured after the ``store.py`` shapes were added). For
``connection.py``'s claim: "This is the sole write path", "There is no other way
to write", "Every write is funnelled through a single entry point", "The
single-writer guarantee lives in one module", "Writes pass through one gateway".
For ``store.py``'s: "Writes occur exclusively within write_transaction", "A
writer is only meaningful inside an open transaction", "It needs a live write
transaction", "There is no other route to one, so the guarantee cannot be
evaded", "Read-only by design: every write goes through SqliteWriter". All ten
pass. This is a regression pin over the wording the retracted claim actually
took, not a characterisation of every way the claim could return, and widening
the list is the same defect one conjugation further out.

**A denial excuses a match from anywhere in front of it in the same sentence,
including from a clause that is not about the claim.** Measured 2026-08-31:
"This does not open a read connection, and it is the only way to write." is one
sentence whose first clause carries the denial and whose second carries the
retracted claim, and the scan reads the first as excusing the second. Narrowing
the denial window from a sentence to a clause is not the fix -- it reopens the
escape this rule exists to close, where a re-added claim borrows the denial of
the sentence in front of it, which is what ``test_adr_0018_claims.py`` measured
on ADR-0018's NFS bullet. Recorded as a stated limit, not chased: a compound
sentence that denies one thing and asserts another is a shape a *reviewer*
catches, and a pin that tried to would fire on the corrected wording instead.

-- The fact ----------------------------------------------------------------

The condition that makes the amended docstrings true is read off the live
``CanonicalStore`` port: it publishes more than one public write method, the
three the docstrings name are among them, and none of their signatures asks for
anything only the write path can hand out. A caller holding domain values can
therefore call a write without entering ``write_transaction``, which is exactly
what "held by convention at each call site" means.

**The port is held by two assertions, because either alone has a hole.**
https://github.com/theurian/theurian/issues/439 consolidates writes behind a
single interface -- the contract ADR-0018 records as owed, spelled in its point 1
as ``CanonicalStore.transaction()``, a context manager yielding a write handle.
That interface can *land beside* the existing write methods rather than replacing
them, which is precisely the state ADR-0018's own Compliance section measured: a
contract recorded as owed while the port went on publishing every way round it.
A pin that only counts write methods stays green through that. So:

- ``test_the_canonical_store_port_publishes_more_than_one_write_method`` goes RED
  when the write methods **leave** the port or grow a transaction parameter.
- ``test_the_canonical_store_port_declares_no_single_write_interface`` goes RED
  the day a member shaped like that interface **appears**, whatever happens to
  the methods.

Between them the reach is: these docstrings must move on either move. The member
walk under both is over the **MRO**, so an interface reached through a base
Protocol is caught rather than reported clean -- a ``vars(port)`` read saw only a
class's own body, and #441's second review round found the shape a split port
would most naturally take sitting straight through the net.

What the pair still does not cover, re-derived 2026-08-31 against the walk as it
now stands, is three spellings:

- an interface under a name holding no "transaction" that returns something which
  is not a context manager -- a plain ``begin()`` handing back a writer object
  passes both nets;
- ``transaction`` declared as a ``@property``, because ``inspect.isfunction`` is
  ``False`` for the ``property`` object the class body holds;
- ``transaction`` declared as a bare attribute annotation
  (``transaction: Callable[[], AbstractContextManager[object]]``), which lands in
  ``__annotations__`` and never in ``vars`` as a function at all.

The handle-shaped parameter rule below is the only net left under the first of
those; the other two are recorded, not chased, for the reason every grammar rule
in this file set is: a walk widened to properties and annotations classifies a
great deal of ordinary port surface, and a pin that fires on it gets deleted.

-- What this module does not hold ------------------------------------------

- **Nothing here proves the lock is taken, or taken on the lock file.** These
  are AST reads of docstrings and introspection of a Protocol; they would stay
  green against a build whose ``write_transaction`` computed the right lock path
  and never flocked it. ``test_adr_0018_claims.py`` disclaims the same about its
  own path arithmetic, and the behaviour is held by
  ``tests/integration/test_canonical_store.py``.
- **No test in this repository contends the write lock across two OS
  processes.** *Contends*, not *takes*: the sentence that stood here until #441's
  second review round said "takes ... from a second OS process", and it was
  false. ``tests/e2e/test_migration_workflow.py`` runs ``theurian migrate apply``
  as a child, and that child does take the lock -- ``cli/commands.py`` and
  ``cli/migration_pipeline.py`` are the only two ``write_transaction`` call sites
  in ``src`` and both are on the CLI's own path (measured 2026-08-31,
  ``git grep -n 'write_transaction' -- packages/theurian-core/src``). What no
  test does is hold it from two processes **at once**, and that is the claim the
  two population tests below hold.

  They hold it in two tiers, because one key cannot do both jobs:

  - ``test_the_only_test_that_constructs_the_write_lock_runs_in_one_process``
    keys on ``WriteLock`` -- an exact population of one file,
    ``test_canonical_store.py``, which names no process-spawning API at all. Its
    ``test_a_second_writer_waits_rather_than_corrupting`` uses two ``WriteLock``
    objects **inside one interpreter**, which does exercise the real ``flock``
    path, since contention is per open file description rather than per process.
  - ``test_no_test_that_enters_the_write_path_runs_a_process_alongside_itself``
    keys on ``WriteLock|write_transaction`` -- nine files, measured 2026-08-31 --
    and refuses only a construct by which a second OS process can be **running
    while the test is**. Four of the nine name ``subprocess``, and that is not a
    finding: every one of them uses ``subprocess.run``, which blocks until the
    child exits, so the child cannot be holding the lock while the parent is. A
    rule that refused ``subprocess`` outright would report four false positives
    and teach the next author to delete it. This tier asserts a property rather
    than an exact file list, because ``write_transaction`` is how an ordinary
    integration test seeds a database and pinning that list would churn on every
    new one.

  So the cross-process wording these docstrings carry ("two processes that both
  enter here serialise") remains a property of ``fcntl.flock`` rather than
  something the suite measures.

  **The blindness, stated rather than papered over.** Both keys are text searches
  for two symbols, and a test whose only acquisition happens inside a spawned
  CLI names neither. ``tests/e2e/test_migration_workflow.py`` is exactly that
  file: it is in neither population, and widening the key does not reach it --
  what it names is the installed entry point. Its serialness is established by
  reading it (``subprocess.run``, which waits), not by anything asserted here.
  Nor can a text scan see a thread that runs ``subprocess.run`` concurrently: a
  ``\\bThread\\b`` token was measured against the tree on 2026-08-31 and fires on
  this module's own English ("Thread a connection ... through these signatures"),
  which is the cry-wolf shape these rules refuse. Both residues are recorded, not
  chased.

  The self-exclusion is the whole reason this is a test and not a number. The
  pasted count that stood here said six lines over a key
  (``git grep -n WriteLock packages/theurian-core/tests tests``) that reads this
  file's own prose, so writing the claim down was what made the claim false: the
  answer became eight the moment the sentence describing it landed. The key
  worth quoting is the self-excluding one --
  ``git grep -n WriteLock -- packages/theurian-core/tests tests
  ':!*test_connection_claims.py'`` -- and the population it returns is asserted
  below rather than transcribed, so it is measured on the tree the reader has
  rather than on a commit that no longer exists. The self-exclusion matters twice
  as much for the wider key, which this file's own samples would otherwise trip.
- **The port is the surface the docstrings name, and the port is what is read.**
  The shipped SQLite adapter splits it: ``SqliteCanonicalStore`` implements the
  reads and holds no write method, while ``SqliteWriter`` is constructed from a
  ``sqlite3.Connection`` -- from *any* connection, which is the face #434
  removed. Which in-tree call sites build one is a question with a live answer,
  ``git grep -n 'SqliteWriter(' -- packages/theurian-core/src
  ':!*/sqlite/store.py'``, and ``store.py``'s own docstring sends the reader to
  that command rather than to a line number. **The exclusion is the same
  self-excluding-key discipline the population test below applies to itself**:
  without it the key matches the docstring that quotes it, so the command reports
  three sites where the tree has two (measured 2026-08-31). Nothing here enforces
  that those sites sit inside a ``write_transaction`` block, and nothing here
  would notice one that did not.
"""

from __future__ import annotations

import ast
import contextlib
import inspect
import pathlib
import re
import sqlite3
from collections.abc import AsyncIterator, Iterator
from contextlib import AbstractContextManager
from typing import Any, Final, Protocol, get_args, get_origin, get_type_hints

from write_lock_claims import REPO_ROOT, collapsed

from theurian.domain.knowledge import KnowledgeRevision
from theurian.domain.ports.canonical_store import CanonicalStore

_SQLITE_PACKAGE = (
    REPO_ROOT / "packages" / "theurian-core" / "src" / "theurian" / "infrastructure" / "sqlite"
)

CONNECTION_MODULE = _SQLITE_PACKAGE / "connection.py"

STORE_MODULE = _SQLITE_PACKAGE / "store.py"

#: The write path's two modules, labelled so a failure names the file rather than
#: an absolute path. Both are scanned by one pattern and both are held to one
#: positive phrase: #434 corrected ``connection.py`` a commit before anyone read
#: ``store.py``, and a population of one would have called the write path clean
#: with three faces of the claim still standing.
WRITE_PATH_MODULES: Final = {
    "connection.py": CONNECTION_MODULE,
    "store.py": STORE_MODULE,
}

#: The shapes #434 retracted, as one pattern over collapsed docstring text.
#:
#: Line wraps are flattened before matching, because every one of these spans a
#: line break in the file it was removed from -- "the\n    single-writer
#: guarantee lives in one place" -- and a substring search over raw source passes
#: while the sentence is being rewritten around it.
#:
#: ``behind`` is listed beside ``through`` deliberately, even though it is the
#: preposition the *corrected* sentence uses. Matching it is safe because a
#: denial in front of the match excuses it, and it is worth matching because
#: "the guarantee lives behind a single interface" is the claim's most natural
#: return.
#:
#: **The second group is ``store.py``'s, and the population without it pins
#: nothing.** Extending the scanned files to ``store.py`` while leaving the
#: pattern at ``connection.py``'s four shapes was measured to match ``store.py``'s
#: pre-#434 text zero times: the same claim wore an entirely different grammar
#: there, so the file would have been read and reported clean.
#:
#: ``cannot be sidestepped`` is a phrase and not the bare word, deliberately.
#: ``store.py`` legitimately says "So a caller cannot write by accident through
#: this type" -- a true claim resting on two named facts, a read class publishing
#: no write method and an ``open_read_connection`` that passes ``mode=ro`` -- and
#: a rule keyed on ``cannot`` would fail the commit that landed the correction.
#: The sentence-scoped denial does **not** save it, because there the denial *is*
#: the match.
#:
#: ``by construction`` is scoped the same way, by proximity to a write rather
#: than on its own: the retracted sentence was "Read-only by construction: every
#: write goes through :class:`SqliteWriter`", and the phrase alone is ordinary
#: English about anything a type makes impossible.
RETRACTED_SINGLE_INTERFACE: Final = re.compile(
    # connection.py's shapes
    r"only way to write"
    r"|exposes no connection"
    r"|(?:through|behind|in) (?:one|a single) interface"
    r"|guarantee lives in one place"
    # store.py's shapes
    r"|happens? only inside"
    r"|valid only inside"
    r"|requires an open write transaction"
    r"|way to build one otherwise"
    r"|cannot be sidestepped"
    r"|by construction[^.]{0,60}?\bwrit"
)

#: Words that turn one of the shapes above into a sentence this module wants.
#:
#: ``rather than`` is the one the correction actually uses. The bare ``no`` that
#: ``test_setup_claims.py`` and ``test_adr_0018_claims.py`` both carry is left
#: **out** here, and that is a measured choice rather than an omission: with it,
#: the retracted "``CanonicalStore`` exposes no connection, so the single-writer
#: guarantee lives in one place" excuses its own second clause, because the first
#: clause's ``no`` sits in front of it.
DENIAL: Final = re.compile(
    r"\bnot\b|\bnever\b|\bno longer\b|\brather than\b|\binstead of\b|\bused to\b|\bretracted\b"
)

#: The end of a sentence, which is not every period. The same trap the ADR-0013
#: and ADR-0018 modules record: ``ADR-0018`` and ``(ADR-0018)`` carry no
#: sentence-ending dot, but ``Milestone 3.`` does.
SENTENCE_END: Final = re.compile(r"\.(?=\s|$)")

#: The port write methods the corrected docstrings name. Read off the docstring
#: by hand and asserted on **both** sides below -- present in the prose, and
#: declared on the port -- so a rename on either side is a RED rather than a
#: quiet disagreement between a docstring and the thing it describes.
CITED_WRITE_METHODS: Final = frozenset({"append_revision", "put_item", "add_relation"})

#: The modules the MRO scaffolding comes from. ``typing`` holds ``Protocol`` and
#: ``Generic``; ``builtins`` holds ``object``. A port's own bases -- the shipped
#: ones under ``theurian.domain.ports``, the synthetic ones in this module --
#: come from neither, so :func:`_public_methods` reads them and skips these.
_MRO_LIBRARY: Final = frozenset({"typing", "builtins"})

#: Reads used as the population premise. If the member walk stops returning
#: these, it is reading a narrowed surface and every conclusion drawn from it is
#: about something other than the port.
KNOWN_READS: Final = frozenset({"get_item", "list_items"})

#: The two roots ``testpaths`` names, which is the whole of "this repository's
#: tests". Walked on disk rather than asked of git, because the mutation harness
#: copies the tree without a ``.git`` and a git-keyed population would fail every
#: mutation run for a reason that has nothing to do with the mutation.
TEST_ROOTS: Final = (
    REPO_ROOT / "packages" / "theurian-core" / "tests",
    REPO_ROOT / "tests",
)

#: This module, as a repository-relative path, so the population below can
#: exclude the file that describes it. Derived rather than written out: a
#: hard-coded name goes stale on a rename and takes the self-exclusion with it,
#: which is how the claim it replaces came to count its own prose.
_THIS_MODULE: Final = pathlib.Path(__file__).resolve().relative_to(REPO_ROOT).as_posix()

#: The one test file that constructs a ``WriteLock``, and the only member the
#: narrow population below may have. Written as a path rather than as a count: a
#: count goes stale silently, while a path that moves fails naming what moved.
WRITE_LOCK_EXERCISE: Final = "packages/theurian-core/tests/integration/test_canonical_store.py"

#: The wider key: every test that enters the write path **in this process**,
#: either by building the lock itself or by opening the transaction that builds
#: it. ``write_transaction`` is what ``WriteLock`` alone was blind to -- the
#: acquisition it mediates names no lock class at all, which is how a claim about
#: the lock came to be measured over a population that could not contain most of
#: its holders.
#:
#: A text key, so a file that merely *writes about* ``write_transaction`` is in
#: it too. That is the safe direction: the rule below refuses a shape, and a
#: prose-only member has no shape to refuse.
_ENTERS_THE_WRITE_PATH: Final = re.compile(r"\bWriteLock\b|\bwrite_transaction\b")

#: How a Python test starts a second OS process at all. The rule under the narrow
#: population: if nothing in the ``WriteLock`` population names one of these, no
#: test in it can be holding the lock from another process, whether concurrently
#: or not.
#:
#: Word-bounded for the reason ``test_adr_0018_claims.py`` records about its own
#: token list -- an unbounded ``fork`` fires inside ``forked`` and a pin that
#: cries wolf on ordinary identifiers is one the next author deletes.
_SPAWNS_A_PROCESS: Final = re.compile(
    r"\bsubprocess\b|\bmultiprocessing\b|\bos\.fork\b|\bposix_spawn\b"
    r"|\bProcessPoolExecutor\b|\bpexpect\b"
)

#: How a Python test gets a second OS process running **alongside itself**. The
#: rule under the wider population, and the distinction is the whole content of
#: the corrected claim: two processes that take the lock one after another do not
#: contend for it, and only contention is what this repository does not test.
#:
#: ``subprocess.run`` is deliberately absent. It waits for the child, so the child
#: has released the lock before the parent's next statement -- and it is what all
#: four ``subprocess``-naming members of the wider population use, for git setup
#: and for running the CLI under test. ``Popen`` is the same module's
#: non-blocking spelling and is refused; so are the process pools, the forks and
#: the asyncio spawners, each of which leaves a child alive across the caller's
#: own execution.
#:
#: What is *not* here is any thread token. ``\bThread\b`` was measured against
#: the two test roots on 2026-08-31 and matched this module's own prose -- "Thread
#: a connection, a session or a transaction token through these signatures" --
#: which is the false RED the word-bounding rule above exists to avoid. A thread
#: calling ``subprocess.run`` is therefore a recorded residue of this rule, not
#: something it catches.
_RUNS_A_PROCESS_ALONGSIDE_ITSELF: Final = re.compile(
    r"\bPopen\b|\bmultiprocessing\b|\bos\.fork\b|\bposix_spawn\b"
    r"|\bProcessPoolExecutor\b|\bpexpect\b|\bcreate_subprocess_\w+\b"
)

#: The phrase the correction landed in **both** modules, and the one positive
#: anchor required of each. It is the shortest sentence fragment that states the
#: amended guarantee rather than the retracted one, and it survives a rewrite of
#: everything around it -- which is what a positive pin has to do, since its job
#: is to fail on deletion rather than on rewording.
HELD_BY_CONVENTION: Final = "held by convention at each call site"

#: The call-site key ``store.py``'s docstring hands the reader, in the form that
#: excludes the docstring quoting it. Required of ``store.py`` **and** of this
#: module's own prose, because the two are a mirrored pair and #441's second round
#: found them mirroring the wrong form: without the pathspec the key matches its
#: own line, so it reported three call sites where the tree has two.
#:
#: Pinned as text rather than by running the command. That is the same choice
#: :data:`TEST_ROOTS` records -- the mutation harness copies the tree without a
#: ``.git``, so a git-keyed assertion would fail every mutation run for a reason
#: unrelated to the mutation.
STORE_CALL_SITE_KEY: Final = (
    "``git grep -n 'SqliteWriter(' -- packages/theurian-core/src ':!*/sqlite/store.py'``"
)

#: Required of ``connection.py`` alone, because they name that module's own
#: mechanism: the object flocked, and how long it is held.
CONNECTION_MECHANISM_PHRASES: Final = (
    "on ``lock_path``",
    "for the duration of the transaction",
)

#: The module docstring as it stood before #434, quoted so the scan can be shown
#: to fire on it.
#: The line breaks are the ones the file carried, kept because they are what the
#: scan has to see through: every retracted phrase here spans one.
RETRACTED_MODULE_DOCSTRING: Final = (
    "Connection management and the single-writer guarantee (ADR-0018, NFR-7).\n"
    "\n"
    "Reads use independent WAL connections. Writes go through one interface holding\n"
    "an OS advisory lock, so two concurrent processes serialise rather than corrupt.\n"
    "Milestone 3 replaces the lock with a daemon-owned queue without changing the\n"
    "interface.\n"
)

#: ``write_transaction``'s docstring as it stood before #434, for the same reason.
RETRACTED_WRITE_TRANSACTION_DOCSTRING: Final = (
    "Open an exclusive write transaction.\n"
    "\n"
    "    The only way to write. ``CanonicalStore`` exposes no connection, so the\n"
    "    single-writer guarantee lives in one place and can change mechanism in\n"
    "    Milestone 3 without touching application code (ADR-0018).\n"
    "    "
)

#: ``store.py``'s three faces as they stood before #434, one per docstring,
#: quoted from ``499d225^`` with the line breaks that file carried. Held as three
#: entries rather than one blob because they are three separate assertions and any
#: one of them returning alone describes a guarantee this codebase does not have
#: -- so the firing test requires each to be matched on its own.
#:
#: Each is keyed by the phrase that identifies it, so a pattern narrowed to two of
#: the three fails naming the face it stopped seeing.
RETRACTED_STORE_FACES: Final = {
    "store.py's module docstring": (
        "SQLite implementation of the CanonicalStore port.\n"
        "\n"
        "Writes happen only inside :func:`write_transaction` (ADR-0018). Reads open their\n"
        "own WAL connection, so a search never blocks on a running rebuild (NFR-4, NFR-7).\n",
        "happen only inside",
    ),
    "SqliteCanonicalStore": (
        "Reads canonical state from one state database.\n"
        "\n"
        "    Read-only by construction: every write goes through\n"
        "    :class:`SqliteWriter`, which requires an open write transaction. Splitting\n"
        "    them means a caller cannot write by accident, and the single-writer rule is\n"
        "    visible in the type rather than in a comment.\n"
        "    ",
        "by construction",
    ),
    "SqliteWriter": (
        "Append-only writes, valid only inside an open write transaction.\n"
        "\n"
        "    Constructed from a connection that the caller obtained via\n"
        "    ``write_transaction``. There is no way to build one otherwise, so the\n"
        "    single-writer guarantee cannot be sidestepped by reaching for this class.\n"
        "    ",
        "cannot be sidestepped",
    ),
}

#: The sentence ``store.py`` says today that a careless widening of the pattern
#: would fail on. It is *true* -- the read class publishes no write method and
#: reaches the database through ``open_read_connection``, which passes ``mode=ro``
#: -- and it is phrased as a denial, so the sentence-scoped ``DENIAL`` rule cannot
#: save it: there the denial is the match. Quoted here so the false RED is a
#: named test rather than something the next author discovers by breaking the
#: build.
TRUE_DENIAL_STORE_PY_MAKES: Final = (
    "So a caller cannot write by accident through this type; reaching for "
    ":class:`SqliteWriter` is a deliberate act."
)


def _docstrings(source: str) -> list[str]:
    """Every docstring in a module's source -- module, class and function alike.

    Parsed rather than searched, which is what makes "docstrings only" true
    rather than approximate: a comment quoting the retracted wording to explain
    why it was removed, an error message, or a string constant all sit outside
    what :func:`ast.get_docstring` returns, and none of them is a claim the
    module makes about itself.
    """
    found: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(
            node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef
        ) and (docstring := ast.get_docstring(node)):
            found.append(docstring)
    return found


def _retracted_claims(source: str) -> list[tuple[str, str]]:
    """Every retracted shape a docstring asserts, as ``(matched phrase, sentence)``.

    **The denial must be in the claim's own sentence and in front of the match.**
    A window that crosses a sentence boundary lets a re-added claim borrow the
    denial of the sentence before it, which is the escape ``test_adr_0018_claims``
    measured on ADR-0018's NFS bullet. A sentence is the unit a denial governs, so
    it is the unit this rule uses.
    """
    claims: list[tuple[str, str]] = []
    for docstring in _docstrings(source):
        for sentence in SENTENCE_END.split(collapsed(docstring)):
            for match in RETRACTED_SINGLE_INTERFACE.finditer(sentence):
                if not DENIAL.search(sentence[: match.start()]):
                    claims.append((match.group(0), sentence.strip()))
    return claims


def _test_module_sources() -> dict[str, str]:
    """Every test module under the two roots, keyed by repository-relative path.

    Sorted, so the population and any failure reporting it are deterministic
    rather than filesystem-ordered.

    Dot directories and ``__pycache__`` are pruned. Both are the same class of
    error and both have bitten this repository's scans before: a machine that
    dogfoods Theurian keeps untracked state under dot directories, and a mutation
    run once put thousands of fixture files inside its copy of the tree and turned
    the unmutated control RED. Neither is a test anybody wrote.
    """
    sources: dict[str, str] = {}
    for root in TEST_ROOTS:
        for path in sorted(root.rglob("*.py")):
            parts = path.relative_to(root).parts
            if any(part == "__pycache__" or part.startswith(".") for part in parts):
                continue
            sources[path.relative_to(REPO_ROOT).as_posix()] = path.read_text(encoding="utf-8")
    return sources


def _module_source(*docstrings: str) -> str:
    """A synthetic module whose docstrings are the ones given.

    The first becomes the module docstring and the rest become function
    docstrings, so a sample can be fed to :func:`_retracted_claims` through the
    same AST path the real file takes.
    """
    parts = [f'"""{docstrings[0]}"""']
    parts.extend(
        f'def _sample_{index}():\n    """{docstring}"""'
        for index, docstring in enumerate(docstrings[1:])
    )
    return "\n\n".join(parts) + "\n"


def _public_methods(port: type) -> dict[str, Any]:
    """Every public method a port Protocol declares, its base Protocols included.

    Takes the port rather than reading ``CanonicalStore`` directly, so the rules
    built on it can be driven by a synthetic Protocol through the same walk the
    shipped one takes. That matters for
    :func:`_transaction_shaped_members`, whose whole point is that it finds
    nothing today: a rule that always returned nothing would be indistinguishable
    from it unless something can be shown to make it fire.

    **The walk is over the MRO, not over ``vars(port)``, and that is the whole
    difference between a net and a hole.** ``vars`` reports only what a class
    body declares, so a ``transaction()`` reached through a base Protocol --
    ADR-0018 point 1's own spelling, and the natural shape for the port #439
    splits into reads and writes -- was invisible to every rule built on this
    function. Measured 2026-08-31 on a two-line Protocol pair: ``"transaction" in
    vars(port)`` is ``False`` while ``"transaction" in dir(port)`` is ``True``,
    so :func:`_transaction_shaped_members` reported a clean port that declared
    exactly the member it watches for.

    The scaffolding is skipped by the module it comes from, :data:`_MRO_LIBRARY`.
    Every Protocol drags ``typing.Protocol``, ``typing.Generic`` and
    ``builtins.object`` into its MRO, and none of the three is anything a port
    declares. They are excluded by name rather than left to the public-function
    filter because "they happen to expose none" is the sort of unstated premise
    this file set exists to refuse -- measured 2026-08-31 they expose zero apiece,
    and the skip means a future one that did would still not be read as a port
    member. The MRO is walked in reverse so a subclass declaration overwrites the
    base's, which is the resolution order Python itself uses.
    """
    methods: dict[str, Any] = {}
    for klass in reversed(port.__mro__):
        if klass.__module__ in _MRO_LIBRARY:
            continue
        for name, member in vars(klass).items():
            if not name.startswith("_") and inspect.isfunction(member):
                methods[name] = member
    return methods


def _returns_a_context_manager(annotation: object) -> bool:
    """Whether an annotation says the member hands back a context manager."""
    origin = get_origin(annotation) or annotation
    return isinstance(origin, type) and issubclass(
        origin, contextlib.AbstractContextManager | contextlib.AbstractAsyncContextManager
    )


def _is_a_decorated_generator(method: object, annotation: object) -> bool:
    """Whether a member is a ``@contextmanager``-shaped generator.

    The second spelling of the same thing. ``@contextlib.contextmanager`` wraps
    the generator with :func:`functools.wraps`, which leaves ``__wrapped__``
    behind and copies the annotation through unchanged -- so the member reports
    ``Iterator[X]`` while behaving as a context manager.

    Both halves are required. A bare ``-> Iterator[X]`` is a streaming *read*,
    which is an ordinary thing for a port to grow, and firing on it would make
    this pin noise; a decorator with a non-generator return is any other
    decorator.
    """
    if getattr(method, "__wrapped__", None) is None:
        return False
    origin = get_origin(annotation) or annotation
    return isinstance(origin, type) and issubclass(origin, Iterator | AsyncIterator)


def _transaction_shaped_members(port: type) -> dict[str, str]:
    """Public members shaped like the single write interface #439 owes, and why.

    ADR-0018 point 1 spells that interface as ``CanonicalStore.transaction()``, a
    context manager yielding a write handle, so the rule reads both halves of
    that sentence and either alone is enough: the name, because a
    ``transaction``-shaped member is the thing whatever it returns; and a
    context-manager return, because the interface could land under any name.

    Names, not signatures, are what a rename escapes -- and a return annotation is
    what an untyped stub escapes. Neither reaches a ``begin()`` that hands back a
    writer object, nor a ``transaction`` spelled as a ``@property`` or as a bare
    attribute annotation, because :func:`_public_methods` classifies functions;
    the module docstring records all three as the shapes both nets miss.

    Both nets read :func:`_public_methods`, so both see a member declared on a
    base Protocol. That is not incidental: the interface #439 owes lands beside
    the reads most plausibly by splitting the port, and a rule reading only a
    class's own body would report exactly that arrangement as clean.
    """
    shaped: dict[str, str] = {}
    for name, method in sorted(_public_methods(port).items()):
        annotation = get_type_hints(method).get("return")
        reasons = [
            reason
            for reason, holds in (
                ("named for a transaction", "transaction" in name),
                ("returns a context manager", _returns_a_context_manager(annotation)),
                (
                    "is a `@contextmanager` generator",
                    _is_a_decorated_generator(method, annotation),
                ),
            )
            if holds
        ]
        if reasons:
            shaped[name] = ", ".join(reasons)
    return shaped


def _write_methods() -> dict[str, Any]:
    """The public methods that declare no return value.

    The classification is derived from the live annotations rather than from a
    list of names, and its reach is exactly that: on this port every mutating
    method is annotated ``-> None`` and every read returns a value, so "declares
    no return value" and "writes" coincide today. A future write that returned
    the id it wrote would drop out of this population, and the count assertion
    below would go RED rather than silently narrow -- which is the direction an
    imprecise rule should fail in.
    """
    return {
        name: method
        for name, method in _public_methods(CanonicalStore).items()
        if get_type_hints(method).get("return") is type(None)
    }


def _mentioned_types(annotation: object) -> list[object]:
    """Every type an annotation mentions, unions and generic arguments unwrapped."""
    arguments = get_args(annotation)
    if not arguments:
        return [annotation]
    mentioned: list[object] = []
    for argument in arguments:
        mentioned.extend(_mentioned_types(argument))
    return mentioned


def _names_a_write_path_handle(annotation: object) -> bool:
    """Whether an annotation names something only the write path can hand out.

    A denylist, not an allowlist, and deliberately: a write method that grew a
    ``Path`` or a ``datetime`` argument would be an ordinary change, and a pin
    that went RED on it would be deleted by whoever met it rather than read. What
    is refused is a ``sqlite3`` object -- the connection ``write_transaction``
    yields -- and any Theurian type from outside ``theurian.domain``, which is
    where a session, a writer or a transaction token introduced by #439 would
    have to come from.

    A handle smuggled in as ``object``, or wearing a domain type, escapes it. The
    method-count assertion is the primary trigger; this is the second.
    """
    for mentioned in _mentioned_types(annotation):
        module = getattr(mentioned, "__module__", "")
        if not isinstance(module, str):
            continue
        if module == "sqlite3" or module.startswith("sqlite3."):
            return True
        if module.startswith("theurian.") and not module.startswith("theurian.domain"):
            return True
    return False


class _PortWithAdrPoint1Transaction(Protocol):
    """ADR-0018 point 1's own spelling of the interface #439 owes.

    Written out as a Protocol rather than described, because the shipped port
    cannot drive the rule that watches for it: ``CanonicalStore`` has no such
    member, so a rule that always reported nothing would look exactly like a
    working one.
    """

    def transaction(self) -> AbstractContextManager[object]:
        """A context manager yielding a write handle."""
        ...

    def get_item(self, item_id: str) -> object | None: ...


class _PortWithAnInheritedTransaction(_PortWithAdrPoint1Transaction, Protocol):
    """The same declaration reached through a base Protocol rather than declared here.

    The shape a port takes when it is split -- a write-side base holding
    ``transaction()`` and a concrete port inheriting it -- which is how #439's
    interface most plausibly lands beside the reads. Nothing in its own class body
    is transaction-shaped, so a rule reading ``vars`` reports it clean; that is
    the hole this sample exists to keep closed.
    """

    def list_items(self) -> tuple[object, ...]: ...


class _PortWithARenamedWriteInterface(Protocol):
    """The same interface under a name holding no ``transaction``."""

    def writing(self) -> AbstractContextManager[object]: ...


class _PortWithAContextManagerDecorator:
    """The ``@contextmanager`` spelling, whose annotation says ``Iterator``."""

    @contextlib.contextmanager
    def unit_of_work(self) -> Iterator[object]:
        yield object()


class _PortWithReadsOnly(Protocol):
    """Reads, including a streaming one, and nothing shaped like a transaction.

    ``stream_items`` is the false RED this rule has to avoid: a plain
    ``Iterator`` return is an ordinary way for a port to grow a streaming read,
    and a pin that fired on it would be deleted by whoever met it.
    """

    def get_item(self, item_id: str) -> object | None: ...

    def list_items(self) -> tuple[object, ...]: ...

    def stream_items(self) -> Iterator[object]: ...


# -- The prose: what the write path's docstrings say -------------------------


def test_neither_write_path_module_claims_writes_go_through_one_interface() -> None:
    """RED means a shape #434 retracted is back in connection.py or store.py.

    The negative half. It is the one that would have caught the defect: the
    Milestone 5 amendment retracted the single-interface claim in ADR-0018 and
    these docstrings went on repeating it for a milestone, because a correction
    to a record does not travel to the code that restates it.

    Both files, one assertion. #434 corrected ``connection.py`` first and left
    ``store.py`` carrying three faces of the same claim -- a scan scoped to one
    module would have reported the write path clean for exactly as long as it
    took someone to read the other file by hand.
    """
    offenders = {
        label: claims
        for label, path in WRITE_PATH_MODULES.items()
        if (claims := _retracted_claims(path.read_text(encoding="utf-8")))
    }

    assert not offenders, (
        f"a write-path module's docstrings assert a claim ADR-0018's Milestone 5 "
        f"amendment retracted: {offenders}"
    )


def test_both_write_path_modules_say_the_guarantee_is_held_by_convention() -> None:
    """RED means the correction was deleted from one of the two files.

    The shared positive half. Requiring it of both is what stops the pair drifting
    apart again: the claim was corrected in one module while the other went on
    asserting it, and a positive pin on ``connection.py`` alone would have been
    green throughout.

    Required *somewhere* in the module's docstrings rather than in a named one,
    so moving the sentence between the module docstring and a class's is legal.
    Requiring it per-docstring would pin the layout as well as the claim.

    The docstring list is asserted non-empty first. A parse that returned nothing
    would make ``any(...)`` false and report "the phrase is gone" about a file
    that still says it, which is the wrong diagnosis pointed at the wrong author.
    """
    for label, path in WRITE_PATH_MODULES.items():
        docstrings = [collapsed(text) for text in _docstrings(path.read_text(encoding="utf-8"))]

        assert docstrings, f"no docstring was parsed out of {label}; this test read nothing"
        assert any(HELD_BY_CONVENTION in docstring for docstring in docstrings), (
            f"{label}'s docstrings no longer say `{HELD_BY_CONVENTION}`, so the "
            f"write path's own record has stopped stating what #434 corrected it to say"
        )


def test_connection_py_still_states_the_lock_holding_write_path() -> None:
    """RED means the correction was deleted rather than reworded.

    The positive half for ``connection.py``'s own mechanism, and it is not the
    negative test restated: docstrings that are stripped back to "Open an
    exclusive write transaction." assert nothing false and would pass
    :func:`test_neither_write_path_module_claims_writes_go_through_one_interface`,
    while leaving a caller with no statement of the thing it has to hold up --
    that entering is what carries the guarantee, and that writing without
    entering is outside it.

    These two phrases are this module's alone, which is why they are not in the
    shared requirement above: ``store.py`` names neither the object flocked nor
    how long it is held, and should not be made to.

    Each phrase is required *somewhere* in the module's docstrings rather than in
    a named one, so moving a sentence between the module docstring and
    ``write_transaction``'s is legal. Requiring them per-docstring would pin the
    layout as well as the claim.
    """
    docstrings = [
        collapsed(text) for text in _docstrings(CONNECTION_MODULE.read_text(encoding="utf-8"))
    ]

    for phrase in CONNECTION_MECHANISM_PHRASES:
        assert any(phrase in docstring for docstring in docstrings), (
            f"connection.py's docstrings no longer say `{phrase}`, so the write "
            f"path's own record has stopped stating what #434 corrected it to say"
        )


def test_both_copies_of_the_call_site_key_exclude_the_docstring_that_quotes_them() -> None:
    """RED means a mirrored call-site key is back to counting its own prose.

    ``store.py`` sends the reader to a live command instead of a line number,
    which is right -- and the command it sent them to matched the very docstring
    line printing it, so it reported three call sites where the tree has two. The
    same key is quoted again in this module's own docstring, which is what makes
    it a *mirrored* string and therefore the shape this whole file set exists for:
    a correction applied to one copy and not the other.

    Both copies are asserted here rather than one, and that is the point. Fixing
    ``store.py`` alone would leave a reader of this module running the unexcluded
    key; fixing this module alone would leave the one the shipped docstring
    publishes. Requiring the pathspec in both is what stops them drifting apart
    the way ``connection.py`` and ``store.py`` did before #434.

    The premise comes first: the parse must have returned docstrings at all,
    otherwise ``any(...)`` reports "the key is gone" about a file that still
    carries it.
    """
    expected = collapsed(STORE_CALL_SITE_KEY)

    store_docstrings = [collapsed(text) for text in _docstrings(STORE_MODULE.read_text("utf-8"))]
    assert store_docstrings, "no docstring was parsed out of store.py; this test read nothing"
    assert any(expected in docstring for docstring in store_docstrings), (
        f"store.py's docstring no longer hands the reader the self-excluding call "
        f"site key `{STORE_CALL_SITE_KEY}`; without the pathspec the command "
        f"matches the docstring line that prints it"
    )

    assert __doc__ is not None, "this module has no docstring, so the mirror is unread"
    assert expected in collapsed(__doc__), (
        f"this module's docstring quotes a call-site key that is not the "
        f"self-excluding one store.py publishes: `{STORE_CALL_SITE_KEY}`"
    )


def test_the_docstring_scan_reads_docstrings_and_not_code() -> None:
    """RED means the scan started reading comments and string constants.

    The premise of "docstrings only". A scan built on a plain text search would
    fire on the comment and on the message below -- both of which quote the
    retracted wording in order to explain it, which is exactly what a file
    recording a correction does. A pin that punishes the explanation is one the
    next author deletes.

    Exactly one claim, not "at least one": three matches would mean the comment
    and the constant were read too, and this test is as much about what the scan
    ignores as about what it finds.
    """
    source = (
        '"""A module docstring that claims nothing."""\n'
        "\n"
        "# The retracted wording said writes go through one interface.\n"
        'MESSAGE = "``CanonicalStore`` exposes no connection"\n'
        "\n"
        "def write():\n"
        '    """The only way to write."""\n'
    )

    claims = _retracted_claims(source)

    assert [phrase for phrase, _ in claims] == ["only way to write"], (
        f"the docstring scan no longer reads docstrings only: {claims}"
    )


def test_the_docstring_scan_fires_on_the_wording_the_correction_removed() -> None:
    """RED means the scan stopped matching, so the negative test passes over nothing.

    The other half of the premise, and the mutation it catches is the one that
    matters: a pattern gutted to match nothing leaves the negative test above
    green forever, reporting a safety that is not there. The sample is the two
    docstrings as they stood before #434, so the pin is shown to fail against the
    exact text it was written to refuse.

    All three retracted shapes are required, because they were three separate
    assertions and only one of them needs to return for the module to describe a
    guarantee it does not have.
    """
    source = _module_source(RETRACTED_MODULE_DOCSTRING, RETRACTED_WRITE_TRANSACTION_DOCSTRING)

    phrases = {phrase for phrase, _ in _retracted_claims(source)}

    assert {"through one interface", "only way to write", "exposes no connection"} <= phrases, (
        f"the scan no longer matches the docstrings #434 corrected: {sorted(phrases)}"
    )


def test_the_docstring_scan_fires_on_each_of_store_pys_three_retracted_faces() -> None:
    """RED means the scan reads store.py without being able to refuse anything in it.

    This is the test the population extension needed and did not have. Adding
    ``store.py`` to the scanned files while the pattern still held only
    ``connection.py``'s four shapes was measured to match ``store.py``'s pre-#434
    text **zero** times -- the file would have been opened, searched and reported
    clean, which is a worse state than not scanning it at all because it looks
    like coverage.

    Each face is required **on its own**, keyed by the docstring it came from, so
    a pattern narrowed to two of the three names the one it stopped seeing rather
    than passing on the other two.
    """
    unmatched = {}
    for face, (docstring, expected) in RETRACTED_STORE_FACES.items():
        phrases = [phrase for phrase, _ in _retracted_claims(_module_source(docstring))]
        if not any(expected in phrase for phrase in phrases):
            unmatched[face] = phrases

    assert not unmatched, (
        f"the scan no longer refuses store.py's retracted faces, so extending it "
        f"to that file pins nothing: {unmatched}"
    )


def test_the_docstring_scan_leaves_store_pys_true_denial_alone() -> None:
    """RED means the scan fires on a true sentence store.py makes today.

    The named false-RED hazard of the store.py extension. "So a caller cannot
    write by accident through this type" is correct -- the read class publishes no
    write method and reaches the database through ``open_read_connection``, which
    passes ``mode=ro``, so SQLite refuses a write on that connection -- and a rule
    keyed on the bare word ``cannot`` would fail the commit that landed the
    correction.

    The sentence-scoped :data:`DENIAL` cannot rescue it, and that is the point
    worth writing down: the denial here *is* the match, so there is nothing in
    front of it to excuse it. Only a shape-specific pattern keeps this green,
    which is why ``cannot be sidestepped`` is a phrase rather than a word.
    """
    source = _module_source(TRUE_DENIAL_STORE_PY_MAKES)

    assert not _retracted_claims(source), (
        "the scan fires on the true claim store.py makes about its read type"
    )


def test_the_docstring_scan_leaves_the_corrected_wording_alone() -> None:
    """RED means the scan fires on the sentence that states the fix.

    A false RED here is not a harmless over-approximation. The corrected module
    docstring says exclusivity is held by convention "rather than behind a single
    interface", and a scan that reads the denied mention as the claim would fail
    on the very commit that removed the claim -- teaching whoever met it that the
    pin is noise.
    """
    source = _module_source(
        "Exclusivity is held by convention at each call site rather than behind a "
        "single interface, which ADR-0018 records in its Milestone 5 amendment."
    )

    assert not _retracted_claims(source), "the scan fires on the wording that states the correction"


# -- The fact: what the CanonicalStore port publishes -------------------------


def test_the_canonical_store_port_publishes_more_than_one_write_method() -> None:
    """RED means the write methods left the port -- one of the two ways #439 lands.

    The fact half, and **half of the fact**. "Exclusivity is held by convention at
    each call site" is only true while there is no single interface to hold it
    instead, and that is a property of the port rather than of this prose.

    What this one measures is exactly the *removal*: writes consolidated behind
    one interface leave at most one public write method here and take it RED. It
    does **not** notice an interface that lands *beside* the write methods it
    counts -- which is the state ADR-0018's own Compliance section measured, a
    contract recorded as owed while the port went on publishing every way round
    it. How many those are is left to :func:`_write_methods` and printed by the
    complement's failure message; a number written into this prose would be a
    second record of it, drifting the day the port grows a write.
    :func:`test_the_canonical_store_port_declares_no_single_write_interface` is
    the other half, and neither is a substitute for the other.

    The premises come first and they are what stop the assertion being vacuous.
    A member walk that returned nothing, or a classifier that called every read a
    write, would both satisfy "more than one" while measuring something else --
    so the walk is required to still find the reads, and the classification is
    required to still exclude them.
    """
    public = _public_methods(CanonicalStore)
    assert set(public) >= KNOWN_READS, (
        f"the Protocol member walk no longer finds {sorted(KNOWN_READS)}, so it is "
        f"not reading the port this test claims to read: {sorted(public)}"
    )

    writes = _write_methods()
    assert not (KNOWN_READS & set(writes)), (
        f"the write classification now admits reads, so its count says nothing "
        f"about writes: {sorted(KNOWN_READS & set(writes))}"
    )

    assert len(writes) > 1, (
        f"`CanonicalStore` no longer publishes more than one write method "
        f"({sorted(writes)}). If #439 has landed, connection.py's docstrings must "
        f"stop saying exclusivity is held by convention at each call site"
    )


def test_the_canonical_store_port_declares_no_single_write_interface() -> None:
    """RED means the interface appeared -- the other way #439 lands.

    The complement, and the reason the count above is not enough on its own. #439
    can add ``CanonicalStore.transaction()`` -- ADR-0018 point 1's own spelling, a
    context manager yielding a write handle -- **without removing a single one of
    the write methods the port already publishes**, and the "more than one write
    method" pin stays green through it while the docstrings' "held by convention
    at each call site" quietly stops being the right description. That is not a
    hypothetical shape: it is the state ADR-0018's Compliance section measured and
    recorded.

    The count is derived and printed rather than written down here. It was pasted
    as "the thirteen" until #441's second review round, where it had already
    drifted from the twelve ADR-0018 records; a number that appears in prose is a
    copy of a measurement, and this file set exists because copies drift.

    The premise comes first. A member walk that found nothing would report "no
    transaction-shaped member" about a port it never read, so the reads are
    required to still be there before their absence means anything.
    """
    public = _public_methods(CanonicalStore)
    assert set(public) >= KNOWN_READS, (
        f"the Protocol member walk no longer finds {sorted(KNOWN_READS)}, so it is "
        f"not reading the port this test claims to read: {sorted(public)}"
    )

    shaped = _transaction_shaped_members(CanonicalStore)

    assert not shaped, (
        f"`CanonicalStore` now declares a member shaped like the single write "
        f"interface ADR-0018 records as owed: {shaped}. It has landed beside the "
        f"{len(_write_methods())} write methods the port still publishes, which "
        f"is the arrangement the write-method count cannot see. connection.py's "
        f"and store.py's docstrings must stop saying exclusivity is held by "
        f"convention at each call site"
    )


def test_the_interface_shape_rule_catches_adr_0018_point_1_and_spares_a_streaming_read() -> None:
    """RED means the shape rule stopped discriminating, so the test above is vacuous.

    Driven by synthetic Protocols because the shipped port cannot drive it: it
    declares no transaction-shaped member, so a rule that always returned nothing
    would be indistinguishable from a working one -- the exact mutation that left
    nineteen tests green over a changed response shape.

    Four positives and one negative, and the negative is the one that keeps the
    pin alive. ``stream_items() -> Iterator[object]`` is an ordinary streaming
    read, and a rule that read every ``Iterator`` return as a context manager
    would fire on the next one of those anybody adds.

    The first sample is checked against the spelling that matters: ADR-0018 point
    1 writes the owed interface as ``CanonicalStore.transaction()``, so the rule
    is required to catch a member by that exact name.

    The **inherited** sample is the one that was missing until #441's second
    review round, and it is not a variation on the first: the member is declared
    on a base Protocol and nothing in the port's own class body is
    transaction-shaped, so a rule reading ``vars(port)`` catches the first sample
    and reports this one clean. That is the shape a split port takes, which makes
    it the likelier of the two ways #439 lands.
    """
    by_the_adr_spelling = _transaction_shaped_members(_PortWithAdrPoint1Transaction)
    inherited = _transaction_shaped_members(_PortWithAnInheritedTransaction)
    renamed = _transaction_shaped_members(_PortWithARenamedWriteInterface)
    decorated = _transaction_shaped_members(_PortWithAContextManagerDecorator)
    reads_only = _transaction_shaped_members(_PortWithReadsOnly)

    assert "transaction" in by_the_adr_spelling, (
        f"the shape rule no longer catches ADR-0018 point 1's own spelling, "
        f"`CanonicalStore.transaction()`: {by_the_adr_spelling}"
    )
    assert "transaction" in inherited, (
        f"the shape rule reads only what a port's own class body declares, so the "
        f"interface escapes it on a base Protocol -- which is the shape a port "
        f"split into reads and writes takes: {inherited}"
    )
    assert "writing" in renamed, (
        f"the shape rule now depends on the name, so the same interface escapes it "
        f"under any other one: {renamed}"
    )
    assert "unit_of_work" in decorated, (
        f"the shape rule no longer catches the `@contextmanager` spelling, whose "
        f"annotation says `Iterator` rather than a context manager: {decorated}"
    )
    assert not reads_only, (
        f"the shape rule fires on a port of ordinary reads, including a streaming "
        f"one -- a false RED that gets the pin deleted rather than read: {reads_only}"
    )


def test_the_write_methods_connection_py_names_are_declared_on_the_port() -> None:
    """RED means the docstrings cite a write method the port does not have.

    The tie between the two halves. The corrected docstrings name
    ``append_revision``, ``put_item`` and ``add_relation`` as evidence for the
    claim they make, so a reader checks the claim by looking those up; a rename
    on either side turns that evidence into a dead reference. Both sides are
    asserted here, in one test, because either alone would let the disagreement
    stand.
    """
    docstrings = collapsed(" ".join(_docstrings(CONNECTION_MODULE.read_text(encoding="utf-8"))))

    for name in sorted(CITED_WRITE_METHODS):
        assert name in docstrings, (
            f"connection.py's docstrings no longer name `{name}` as evidence that "
            f"the port publishes its write methods directly"
        )

    writes = set(_write_methods())
    assert writes >= CITED_WRITE_METHODS, (
        f"connection.py's docstrings name write methods `CanonicalStore` does not "
        f"declare: {sorted(CITED_WRITE_METHODS - writes)}"
    )


def test_no_canonical_store_write_method_asks_for_a_handle_from_the_write_path() -> None:
    """RED means a write now needs something only ``write_transaction`` can give.

    The second half of "reachable without the lock". A caller holding domain
    values can call every write method the port declares, which is what makes the
    guarantee a convention rather than a construction. Thread a connection, a
    session or a transaction token through these signatures -- the shape #439
    would most likely take -- and the docstring's "held by convention at each call
    site" stops being the right description.

    The premise is that the rule reads something: a walk that returned no
    annotations would report every write as clean.
    """
    writes = _write_methods()
    assert writes, "no write method was found; the rule below would read nothing"

    inspected: list[object] = []
    offenders: dict[str, list[str]] = {}
    for name, method in sorted(writes.items()):
        parameters = [
            annotation
            for parameter, annotation in get_type_hints(method).items()
            if parameter != "return"
        ]
        inspected.extend(parameters)
        if named := [str(a) for a in parameters if _names_a_write_path_handle(a)]:
            offenders[name] = named

    assert inspected, "no write method declares a parameter, so the rule read nothing"
    assert not offenders, (
        f"a `CanonicalStore` write method now asks for a handle from inside the "
        f"write path: {offenders}. connection.py's docstrings say exclusivity is "
        f"held by convention at each call site, which is a claim about writes a "
        f"caller can reach without entering `write_transaction`"
    )


def test_the_handle_rule_refuses_a_connection_and_admits_a_domain_value() -> None:
    """RED means the handle rule stopped discriminating, so the test above is vacuous.

    Driven by synthetic input because the shipped port cannot drive it: every
    write signature is clean today, so a rule that always returned ``False``
    would look identical. Both directions are asserted -- a rule that always
    returned ``True`` would be just as broken, and would fail loudly rather than
    silently, which is why the negative case is the cheaper of the two to lose.
    """
    assert _names_a_write_path_handle(sqlite3.Connection), (
        "the handle rule no longer refuses the connection `write_transaction` yields"
    )
    assert not _names_a_write_path_handle(KnowledgeRevision), (
        "the handle rule refuses an ordinary domain value, so it would fire on the "
        "signatures the port has today"
    )


# -- The suite: what actually exercises the lock ------------------------------


def test_the_only_test_that_constructs_the_write_lock_runs_in_one_process() -> None:
    """RED means the suite's write-lock coverage moved, and this module's disclaimer must too.

    Both docstrings under scan say two processes entering ``write_transaction``
    serialise, and nothing in this repository measures that. The claim is
    inherited from ``fcntl.flock`` -- contention is per open file description
    rather than per process, so the one test that builds the lock directly
    exercises the real path with two ``WriteLock`` objects inside a single
    interpreter -- and the module docstring above says so. **This test is what
    keeps that disclaimer true.**

    It replaces a pasted count that could not be. The count said six lines over
    ``git grep -n WriteLock packages/theurian-core/tests tests``, a key that reads
    this very file, so the sentence stating the measurement was what made it wrong
    -- the answer is eight in any tree containing the claim. The key that means
    something excludes the claimant::

        git grep -n WriteLock -- packages/theurian-core/tests tests \\
            ':!*test_connection_claims.py'

    and this test is that key, run against the tree the reader has rather than
    against a commit that has since been rebased away.

    **The premises come first.** A walk that found no files, or that missed this
    file, would report an empty population as a clean one -- so the walk is
    required to have found this module before its absence from the result means
    anything.

    Reach: this is the *narrow* tier, and it is deliberately narrow. Its
    population is exact, so any new file naming ``WriteLock`` fails here and gets
    read by a person -- but ``WriteLock`` is not how most of the suite reaches the
    lock, and a claim resting on this key alone would be a claim about one file.
    :func:`test_no_test_that_enters_the_write_path_runs_a_process_alongside_itself`
    is the tier that covers ``write_transaction``, and the module docstring
    records what neither key can see.
    """
    sources = _test_module_sources()

    assert _THIS_MODULE in sources, (
        f"the test-tree walk no longer finds this module ({_THIS_MODULE}), so it is "
        f"not reading the population it claims to read: {len(sources)} files found"
    )

    holders = sorted(
        path for path, text in sources.items() if "WriteLock" in text and path != _THIS_MODULE
    )

    assert holders == [WRITE_LOCK_EXERCISE], (
        f"the suite's `WriteLock` population has moved: {holders}. This module's "
        f"docstring says the one test that builds the lock directly runs in a "
        f"single interpreter, and that claim now has to be re-read against "
        f"whatever is here"
    )

    spawners = {
        path: sorted(set(found))
        for path in holders
        if (found := _SPAWNS_A_PROCESS.findall(sources[path]))
    }

    assert not spawners, (
        f"a test that builds a `WriteLock` also starts a process: {spawners}. If it "
        f"takes the lock from that process, this module and connection.py's "
        f"docstrings can stop calling the cross-process wording an inherited claim"
    )


def test_no_test_that_enters_the_write_path_runs_a_process_alongside_itself() -> None:
    """RED means a test can now hold the write lock in two processes at once.

    The wider tier, and the one that answers what the narrow key could not. Every
    in-process acquisition of the lock goes through ``write_transaction``, which
    names no lock class, so a population keyed on ``WriteLock`` was blind to all
    of them -- which is how "no test takes the write lock from a second OS
    process" survived here while ``tests/e2e/test_migration_workflow.py`` was
    running ``theurian migrate apply`` in a child that does exactly that.

    **The claim this holds is contention, not acquisition**, because acquisition
    is not what the docstrings under scan are inherited from. Two processes taking
    the lock one after another never meet; ``fcntl.flock`` only becomes the
    unmeasured premise when they overlap. So the rule refuses a construct that
    leaves a child *alive across the caller's own execution* and admits
    ``subprocess.run``, which waits. That distinction is load-bearing rather than
    lenient: four members of this population name ``subprocess`` -- for ``git
    init`` and for the CLI under test -- and a rule that refused the module
    outright would report four false positives on the first run.

    A property, not an exact file list. ``write_transaction`` is how an ordinary
    integration test seeds a database, so pinning the membership would go RED on
    every new one and be deleted; the exact-list discipline lives in the narrow
    tier, where the population is one file.

    **The premises come first**, and one of them is that this tier is not the
    narrow one wearing a different name. A population that came back empty would
    satisfy "no member spawns" while measuring nothing; one that had lost the file
    which actually exercises the lock would not be reading the write path at all;
    and one reaching nothing the ``WriteLock`` key already reaches would leave
    every ``write_transaction`` acquisition unwatched while looking like coverage,
    which is the state this test was written to end.

    Reach: it is a text search over two symbols. A test whose only acquisition is
    inside a spawned CLI names neither and is invisible here -- the e2e migration
    workflow is that file, and the module docstring says so and says why widening
    the key does not reach it.
    """
    sources = _test_module_sources()

    assert _THIS_MODULE in sources, (
        f"the test-tree walk no longer finds this module ({_THIS_MODULE}), so it is "
        f"not reading the population it claims to read: {len(sources)} files found"
    )

    population = sorted(
        path
        for path, text in sources.items()
        if _ENTERS_THE_WRITE_PATH.search(text) and path != _THIS_MODULE
    )

    assert WRITE_LOCK_EXERCISE in population, (
        f"the write-path population no longer contains {WRITE_LOCK_EXERCISE}, the "
        f"file that actually exercises the lock, so it is not the population this "
        f"test claims to read: {population}"
    )

    beyond_the_narrow_key = [path for path in population if "WriteLock" not in sources[path]]
    assert beyond_the_narrow_key, (
        f"the write-path population reaches nothing the `WriteLock` key does not, "
        f"so this tier has collapsed into the narrow one and every "
        f"`write_transaction` acquisition is unwatched again: {population}"
    )

    concurrent = {
        path: sorted(set(found))
        for path in population
        if (found := _RUNS_A_PROCESS_ALONGSIDE_ITSELF.findall(sources[path]))
    }

    assert not concurrent, (
        f"a test that enters the write path also runs a process alongside itself: "
        f"{concurrent}. If both take the lock, this module and connection.py's "
        f"docstrings can stop calling the cross-process wording an inherited claim "
        f"-- and if they do not, this rule needs the reason written down"
    )


def test_the_spawn_rules_tell_a_concurrent_child_from_a_serial_one_and_from_a_lock_object() -> None:
    """RED means a spawn rule stopped discriminating, so a population test is vacuous.

    Driven by synthetic input because the shipped suite cannot drive either rule:
    nothing in the narrow population spawns anything and nothing in the wider one
    runs a child concurrently, so a rule that always returned nothing would look
    exactly like a working one.

    The middle sample is the one #441's second round added, and it is what the
    corrected claim rests on. ``subprocess.run`` waits for the child, so it cannot
    produce two processes holding the lock at once -- and four members of the
    wider population use it. The narrow rule refuses it (it is still *a* second
    process) while the concurrency rule admits it, and asserting both directions
    on the same sample is what stops the two rules quietly collapsing into one.

    The last sample is the shipped test's own shape, quoted rather than invented
    -- two ``WriteLock`` objects in one interpreter. A rule that fired on it would
    report the very test whose single-process nature this module documents as a
    cross-process one.
    """
    a_concurrent_child = "child = subprocess.Popen([sys.executable, '-c', 'take_the_lock()'])\n"
    a_serial_child = (
        "import subprocess\n\nsubprocess.run([sys.executable, '-c', 'take_the_lock()'])\n"
    )
    a_second_lock_object = (
        "outer = WriteLock(lock, timeout=0.2)\ninner = WriteLock(lock, timeout=0.2)\n"
    )

    assert _SPAWNS_A_PROCESS.findall(a_serial_child), (
        "the spawn rule no longer recognises a test that starts a second process"
    )
    assert not _SPAWNS_A_PROCESS.findall(a_second_lock_object), (
        "the spawn rule reads two lock objects in one interpreter as two processes, "
        "so it would fire on the shipped test this module describes as single-process"
    )

    assert _RUNS_A_PROCESS_ALONGSIDE_ITSELF.findall(a_concurrent_child), (
        "the concurrency rule no longer recognises a child left running alongside "
        "the test, which is the only shape that can contend for the lock"
    )
    assert not _RUNS_A_PROCESS_ALONGSIDE_ITSELF.findall(a_serial_child), (
        "the concurrency rule reads a blocking `subprocess.run` as a concurrent "
        "child, so it would fire on the four write-path tests that shell out and "
        "wait -- a false RED on the population it exists to clear"
    )
    assert not _RUNS_A_PROCESS_ALONGSIDE_ITSELF.findall(a_second_lock_object), (
        "the concurrency rule reads two lock objects in one interpreter as two "
        "processes, so it would fire on the shipped single-process test"
    )
