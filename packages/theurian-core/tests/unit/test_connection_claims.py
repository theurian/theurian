"""What ``infrastructure/sqlite/connection.py`` claims about the write path.

Two of this module's docstrings asserted the single-interface claim that
ADR-0018's Milestone 5 amendment had already retracted. The module docstring
said "Writes go through one interface holding an OS advisory lock"; the
``write_transaction`` docstring said it was "The only way to write" and that
"``CanonicalStore`` exposes no connection, so the single-writer guarantee lives
in one place". None of that is true of this codebase, and
https://github.com/theurian/theurian/issues/434 corrected both in place.

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
not to check.** So the correction is held here in both directions.

-- The prose ---------------------------------------------------------------

The retracted shapes are refused, and the shapes the correction landed are
required. Both halves are needed and neither implies the other: a rewrite that
deletes the docstrings entirely makes no false claim and would pass the negative
test while leaving a reader of ``write_transaction`` with nothing that says the
lock is held by convention rather than by construction.

The scan reads **docstrings only**, parsed out of the source with :mod:`ast`, so
a comment or an error message quoting the retracted wording to explain it does
not fire. Sentences carrying a denial before the match are left alone, which is
what lets the corrected module docstring say "exclusivity is held by convention
at each call site rather than behind a single interface" without punishing the
wording that states the fix.

**Measured escapes, recorded rather than chased** (against the compiled pattern,
2026-08-31): "This is the sole write path", "There is no other way to write",
"Every write is funnelled through a single entry point", "The single-writer
guarantee lives in one module", "Writes pass through one gateway" -- all five
pass. This is a regression pin over the wording the retracted claim actually
took, not a characterisation of every way the claim could return, and widening
the list is the same defect one conjugation further out.

-- The fact ----------------------------------------------------------------

The condition that makes the amended docstring true is read off the live
``CanonicalStore`` port: it publishes more than one public write method, the
three the docstrings name are among them, and none of their signatures asks for
anything only the write path can hand out. A caller holding domain values can
therefore call a write without entering ``write_transaction``, which is exactly
what "held by convention at each call site" means.

**It goes RED the day https://github.com/theurian/theurian/issues/439 lands** --
consolidating writes behind a single interface for both stores, the contract
ADR-0018 records as owed. That is the day these docstrings must move again, and
the RED is the notification: the write methods leave the port, or they grow a
transaction parameter, and both are refused here.

-- What this module does not hold ------------------------------------------

- **Nothing here proves the lock is taken, or taken on the lock file.** These
  are AST reads of docstrings and introspection of a Protocol; they would stay
  green against a build whose ``write_transaction`` computed the right lock path
  and never flocked it. ``test_adr_0018_claims.py`` disclaims the same about its
  own path arithmetic, and the behaviour is held by
  ``tests/integration/test_canonical_store.py``.
- **No test in this repository takes the write lock from a second OS process.**
  Measured at db7506f: ``git grep -n WriteLock packages/theurian-core/tests
  tests`` returns six lines, all in ``test_canonical_store.py``, and its
  ``test_a_second_writer_waits_rather_than_corrupting`` uses two ``WriteLock``
  objects **inside one process** -- which does exercise the real ``flock`` path,
  since contention is per open file description rather than per process. So the
  cross-process wording these docstrings carry ("two processes that both enter
  here serialise") is a property of ``fcntl.flock``, not something the suite
  measures.
- **The port is the surface the docstrings name, and the port is what is read.**
  The shipped SQLite adapter splits it: ``SqliteCanonicalStore`` implements the
  reads and holds no write method, while ``SqliteWriter`` is constructed from a
  ``sqlite3.Connection``. Measured at db7506f, both of its two construction
  sites in ``packages/theurian-core/src`` -- ``cli/commands.py:1219`` and
  ``cli/migration_pipeline.py:95`` -- sit inside a ``write_transaction`` block,
  so today's only in-tree writer does hold the convention. Nothing here enforces
  that, and nothing here would notice a third call site built outside the lock.
"""

from __future__ import annotations

import ast
import inspect
import pathlib
import re
import sqlite3
from typing import Any, Final, get_args, get_type_hints

from theurian.domain.knowledge import KnowledgeRevision
from theurian.domain.ports.canonical_store import CanonicalStore

REPO_ROOT = pathlib.Path(__file__).resolve().parents[4]

CONNECTION_MODULE = (
    REPO_ROOT
    / "packages"
    / "theurian-core"
    / "src"
    / "theurian"
    / "infrastructure"
    / "sqlite"
    / "connection.py"
)

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
RETRACTED_SINGLE_INTERFACE: Final = re.compile(
    r"only way to write"
    r"|exposes no connection"
    r"|(?:through|behind|in) (?:one|a single) interface"
    r"|guarantee lives in one place"
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

#: Reads used as the population premise. If the member walk stops returning
#: these, it is reading a narrowed surface and every conclusion drawn from it is
#: about something other than the port.
KNOWN_READS: Final = frozenset({"get_item", "list_items"})

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


def _collapsed(text: str) -> str:
    """Lowercased with runs of whitespace flattened to single spaces."""
    return " ".join(text.lower().split())


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
        for sentence in SENTENCE_END.split(_collapsed(docstring)):
            for match in RETRACTED_SINGLE_INTERFACE.finditer(sentence):
                if not DENIAL.search(sentence[: match.start()]):
                    claims.append((match.group(0), sentence.strip()))
    return claims


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


def _public_methods() -> dict[str, Any]:
    """Every public method the ``CanonicalStore`` Protocol declares."""
    return {
        name: member
        for name, member in vars(CanonicalStore).items()
        if not name.startswith("_") and inspect.isfunction(member)
    }


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
        for name, method in _public_methods().items()
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


# -- The prose: what connection.py's docstrings say --------------------------


def test_connection_py_does_not_claim_writes_go_through_one_interface() -> None:
    """RED means a shape #434 retracted is back in this module's docstrings.

    The negative half. It is the one that would have caught the defect: the
    Milestone 5 amendment retracted the single-interface claim in ADR-0018 and
    these docstrings went on repeating it for a milestone, because a correction
    to a record does not travel to the code that restates it.
    """
    claims = _retracted_claims(CONNECTION_MODULE.read_text(encoding="utf-8"))

    assert not claims, (
        f"connection.py's docstrings assert a claim ADR-0018's Milestone 5 "
        f"amendment retracted: {claims}"
    )


def test_connection_py_still_states_the_lock_holding_write_path() -> None:
    """RED means the correction was deleted rather than reworded.

    The positive half, and it is not the negative one restated: docstrings that
    are stripped back to "Open an exclusive write transaction." assert nothing
    false and would pass
    :func:`test_connection_py_does_not_claim_writes_go_through_one_interface`,
    while leaving a caller with no statement of the thing it has to hold up --
    that entering is what carries the guarantee, and that writing without
    entering is outside it.

    Each phrase is required *somewhere* in the module's docstrings rather than in
    a named one, so moving a sentence between the module docstring and
    ``write_transaction``'s is legal. Requiring them per-docstring would pin the
    layout as well as the claim.
    """
    docstrings = [
        _collapsed(text) for text in _docstrings(CONNECTION_MODULE.read_text(encoding="utf-8"))
    ]

    for phrase in (
        "on ``lock_path``",
        "for the duration of the transaction",
        "held by convention at each call site",
    ):
        assert any(phrase in docstring for docstring in docstrings), (
            f"connection.py's docstrings no longer say `{phrase}`, so the write "
            f"path's own record has stopped stating what #434 corrected it to say"
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
    """RED means #439 landed -- and connection.py's docstrings must move with it.

    The fact half. "Exclusivity is held by convention at each call site" is only
    true while there is no single interface to hold it instead, and that is a
    property of the port rather than of this prose: consolidating writes behind
    one interface (#439, the contract ADR-0018 records as owed) leaves at most
    one public write method here and takes this RED.

    The premises come first and they are what stop the assertion being vacuous.
    A member walk that returned nothing, or a classifier that called every read a
    write, would both satisfy "more than one" while measuring something else --
    so the walk is required to still find the reads, and the classification is
    required to still exclude them.
    """
    public = _public_methods()
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


def test_the_write_methods_connection_py_names_are_declared_on_the_port() -> None:
    """RED means the docstrings cite a write method the port does not have.

    The tie between the two halves. The corrected docstrings name
    ``append_revision``, ``put_item`` and ``add_relation`` as evidence for the
    claim they make, so a reader checks the claim by looking those up; a rename
    on either side turns that evidence into a dead reference. Both sides are
    asserted here, in one test, because either alone would let the disagreement
    stand.
    """
    docstrings = _collapsed(" ".join(_docstrings(CONNECTION_MODULE.read_text(encoding="utf-8"))))

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
