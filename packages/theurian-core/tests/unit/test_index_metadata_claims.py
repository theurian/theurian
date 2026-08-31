"""Which ``index_metadata`` columns are read back, per column (ADR-0024 decision 2, #426).

ADR-0024 decision 2 records that a purge copies the published build with
``Connection.backup``, so the new file's ``index_metadata`` inherits the parent's
``index_build_id`` and ``built_at`` until ``_restamp`` overwrites them. The
decision used to say **nothing in ``src/`` reads either back**, which made a copy
that kept the parent's identity "latent rather than broken". #426 split that claim
per column, because one half stopped being true:

- **``index_build_id`` is read back.** ``SqliteIndexStore.add_nodes`` selects it
  out of the file it is writing into, to stamp each summary node with the build it
  belongs to rather than take an argument that could disagree with the file. The
  behaviour half of that is
  ``tests/integration/test_index_build_id_read_back.py``.
- **``built_at`` is written and never read.** For that column the original
  reasoning stands, and this module is what holds it.

**A never-claim needs a scan that can also say yes.** A search for an absence
reports success when it searches nothing, and this one has a second way to be
vacuous: ``metadata()`` does ``SELECT *``, so every column is *fetched* on that
path and the claim is about **consumption**, not about fetching. The scan is
therefore controlled against the shipped source rather than only against synthetic
input: the same pass that reports zero consumers for ``built_at`` must report
consumers for ``index_build_id``, for ``index_schema_version`` and for
``embedding_model``, and must find the ``SELECT *`` the decision's own sentence
rests on. Those are three real positives and a real premise from one scan; a
broken extractor cannot produce them.

**What this scan sees.** Two shapes, over every ``*.py`` in the *imported*
``theurian`` package:

1. **A SQL projection.** A string constant containing ``SELECT <projection> FROM
   index_metadata`` where the projection names the column. ``*`` is recorded as a
   star select and is deliberately **not** a consumer -- that is the distinction
   the decision turns on.
2. **A mapping access naming the key.** ``row["built_at"]``,
   ``metadata().get("built_at")``, ``block.pop("built_at", None)`` -- a string
   constant equal to a column name in subscript or ``get``/``pop`` position,
   whatever the object is. Deliberately over-approximate: an unrelated mapping
   with a colliding key would be a false RED, which costs a read, where a false
   green costs the claim.

**What it cannot see, and these are the limits the docstring is here to state.**
The first three are Python-side; the next three are SQL-side, were found by
round-one review, and are recorded rather than closed -- each has a case in
:data:`PROJECTION_CASES` or :data:`KEYED_CASES` asserting the miss, so the list
is a measurement and not a recollection.

- A ``SELECT *`` whose row is consumed without the key ever being written down --
  ``for column, value in store.metadata().items()``, or ``dict(row)`` handed to
  something positional. This is the nearest miss, because ``metadata()`` returns
  exactly such a mapping.
- A key assembled or indirected at runtime: ``row["built" + "_at"]``, or a column
  name held in a variable or a tuple.
- Attribute access -- ``row.built_at``. ``sqlite3.Row`` offers no attribute
  access and nothing maps this row onto a dataclass today, so the shape does not
  exist in the tree; if one lands, this scan is blind to it.
- A **schema-qualified table**: ``FROM main.index_metadata``. The projection
  pattern requires the bare table name directly after ``FROM``.
- ``index_metadata`` reached through a **join or a CTE** rather than named by the
  ``FROM`` the projection belongs to -- ``WITH m AS (SELECT * FROM
  index_metadata) SELECT built_at FROM m``, or ``FROM nodes JOIN index_metadata
  im`` with ``im.built_at`` in the projection. The inner ``SELECT *`` is still
  seen; the outer ``built_at`` is not.
- A **scalar subquery in a position that is not a projection**: ``UPDATE nodes
  SET x = (SELECT built_at FROM index_metadata)`` is seen, because the inner
  ``SELECT`` matches on its own, but a query whose only reference to the table is
  built by string formatting is not -- the constant never contains the finished
  statement.
- Anything outside the imported package: ``tools/``, ``plugins/`` and the tests
  themselves are not scanned, the same bound ``test_config_key_call_sites.py`` and
  ``test_network_call_sites.py`` each record for their own.

It is a floor on the review a new consumer gets, not a proof that one cannot
exist.

**The column population is derived from the DDL**, not transcribed: the
``index_metadata`` ``CREATE TABLE`` in ``index_schema.py`` is parsed, so a column
added to the table is a column this module is about from the moment it exists. A
hand-written list would leave a new column unwatched while every test here stayed
green -- the ``_swept_modules`` finding in ``test_adr_0018_claims.py``, in the
shape a schema takes.

Pure in the sense the other structural tests are: it parses the shipped ``.py``
files as text and opens no database, no socket, and no temporary directory.
"""

from __future__ import annotations

import ast
import pathlib
import re
from collections.abc import Iterator
from typing import Final

import pytest

import theurian
from theurian.infrastructure.sqlite.index_schema import INDEX_DDL

pytestmark = pytest.mark.unit

#: The package as *imported*, not a path relative to this file -- the reckoning
#: ``test_config_key_call_sites.py``, ``test_gate_call_sites.py`` and
#: ``test_network_call_sites.py`` all use, and for the same reason: a hand-built
#: relative path can drift from the installed package and would then scan a
#: directory with no consumer in it whatever the source did.
SRC: Final = pathlib.Path(theurian.__file__).resolve().parent

#: ``parents[4]`` is ``.../tests/unit/`` → ``tests`` → ``theurian-core`` →
#: ``packages`` → repo root.
REPO_ROOT: Final = pathlib.Path(__file__).resolve().parents[4]

ADR_0024: Final = REPO_ROOT / "docs" / "adr" / "0024-a-purge-is-a-build.md"

#: The ``index_metadata`` table's body in :data:`INDEX_DDL`.
#:
#: Non-greedy up to ``);``, which is what makes the inner ``CHECK (id = 1)``
#: harmless: its closing parenthesis is followed by a comma, not a semicolon.
_METADATA_TABLE: Final = re.compile(
    r"CREATE\s+TABLE\s+index_metadata\s*\((?P<body>.*?)\)\s*;",
    re.IGNORECASE | re.DOTALL,
)

#: Words that open a table constraint rather than a column. Without this a
#: ``PRIMARY KEY (a, b)`` line would be read as a column named ``primary``.
_CONSTRAINT_KEYWORDS: Final = frozenset(
    {"primary", "foreign", "unique", "check", "constraint", "key"}
)

#: A projection over ``index_metadata``, **bounded to a single statement and to a
#: single ``SELECT``**. ``DOTALL`` because a SQL string in this codebase is
#: routinely written across implicitly concatenated literals, which the parser
#: folds into one constant that may carry newlines.
#:
#: The tempered body -- no ``;``, and no ``SELECT`` or ``FROM`` inside the
#: projection -- is what keeps a *second* statement in the same constant from
#: being read into the first one's projection. With a plain ``.*?`` under
#: ``DOTALL``, ``"SELECT built_at FROM nodes UNION SELECT * FROM index_metadata"``
#: reports ``built_at`` as a column selected out of ``index_metadata``, which it
#: is not: the span from the first ``SELECT`` runs through ``FROM nodes UNION
#: SELECT *`` to the second ``FROM index_metadata``. That is the same
#: swallow-the-span defect the per-constant application below records, one level
#: down, and it is a false *consumer* -- the direction that makes
#: :func:`test_no_shipped_module_consumes_index_metadata_built_at` RED for a
#: reason that is not true.
_SELECT_FROM_METADATA: Final = re.compile(
    r"\bSELECT\b(?P<projection>(?:(?!\b(?:SELECT|FROM)\b)[^;])*?)\bFROM\s+index_metadata\b",
    re.IGNORECASE | re.DOTALL,
)

#: ``DISTINCT``/``ALL`` opens a projection and is not a column. Stripped from the
#: whole projection rather than per item, because that is where SQL puts it.
_LEADING_QUANTIFIER: Final = re.compile(r"^\s*(?:DISTINCT|ALL)\b", re.IGNORECASE)

#: An explicit alias and everything after it. ``SELECT built_at AS made`` names
#: ``built_at``; ``made`` is what the caller calls it, not a column of the table.
_ALIAS_TAIL: Final = re.compile(r"\bAS\b.*$", re.IGNORECASE | re.DOTALL)

#: A possibly table-qualified SQL name.
_IDENTIFIER: Final = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*")

#: Mapping accessors that take the key as their first positional argument.
_KEYED_ACCESSORS: Final = frozenset({"get", "pop", "setdefault"})


def _index_metadata_columns() -> tuple[str, ...]:
    """The ``index_metadata`` columns, parsed out of the shipped DDL.

    Read from :data:`INDEX_DDL` rather than transcribed, so that the population
    this module reasons about is the table the product actually creates. A
    transcribed list stays green when a column is added, renamed or dropped, which
    is precisely when ADR-0024 decision 2's per-column claim needs re-deriving.
    """
    match = _METADATA_TABLE.search(INDEX_DDL)
    assert match is not None, "INDEX_DDL no longer creates an `index_metadata` table"

    columns: list[str] = []
    for line in match.group("body").splitlines():
        stripped = line.strip().rstrip(",")
        if not stripped or stripped.startswith("--"):
            continue
        name = stripped.split()[0]
        if name.lower() in _CONSTRAINT_KEYWORDS or not name.isidentifier():
            continue
        columns.append(name)
    return tuple(columns)


def _projection_columns(item: str) -> Iterator[str]:
    """The column names one comma-separated projection item refers to.

    Four shapes, and each is a case in :data:`PROJECTION_CASES`:

    * ``*`` (or ``t.*``) yields ``*``, never the columns it stands for. A star
      select fetches every column and consumes none, which is the distinction the
      decision turns on.
    * A bare or table-qualified name yields its last dotted component, so
      ``index_metadata.built_at`` reads as ``built_at``.
    * An alias is dropped rather than reported: ``built_at AS made`` yields
      ``built_at``, and a bare alias (``built_at made``) does the same, because
      only the *first* name of a plain item is taken.
    * A function application yields every name it encloses and not the function:
      ``COUNT(built_at)`` is a consumer of ``built_at``, and it read as the
      literal string ``COUNT(built_at)`` until round one. ``COUNT(*)`` yields
      nothing, which is right -- a row count reads no column.

    An identifier immediately followed by ``(`` is the function, not a column, so
    an aggregate this code has never heard of still gives up its argument.
    """
    expression = _ALIAS_TAIL.sub("", item).strip()
    if expression == "*" or expression.endswith(".*"):
        yield "*"
        return

    names = [
        match
        for match in _IDENTIFIER.finditer(expression)
        if not expression[match.end() :].lstrip().startswith("(")
    ]
    if not names:
        return
    for match in names if "(" in expression else names[:1]:
        yield match.group().split(".")[-1]


def _projection_names(source: str) -> Iterator[str]:
    """Every column a ``SELECT ... FROM index_metadata`` in ``source`` names.

    Per-item resolution is :func:`_projection_columns`; this is the part that
    decides *which* text is a projection at all.

    **Applied to one string constant at a time, never to the module text**, and
    that is the difference between a scan and a coincidence. Run over raw source
    with ``DOTALL``, the pattern matches from *any* ``SELECT`` in the file to the
    next ``FROM index_metadata`` however many hundred lines later, and reads
    every column name in between as a projection. Measured: deleting
    ``add_nodes``'s real ``SELECT index_build_id`` left this reporting
    ``index_build_id`` as consumed anyway, because the span from a later
    ``SELECT`` to ``metadata()``'s ``SELECT *`` swallowed the ``INSERT INTO
    nodes`` column list. The control that is supposed to prove the scan can see
    would then have been satisfied by the accident rather than by the reader.

    **And never across a statement boundary inside one constant**, which is the
    same defect at the next scale down and is what :data:`_SELECT_FROM_METADATA`
    now forbids: one constant holding ``SELECT ... FROM nodes; SELECT * FROM
    index_metadata``, or the two arms of a ``UNION``, used to have the first
    statement's projection read into the second statement's table.

    Implicit concatenation is folded by the parser into a single constant, so a
    SQL string written across five adjacent literals arrives here whole. An
    f-string or a ``.format`` call does not, and a query assembled that way is
    outside what this reads -- recorded in the module docstring with the rest.
    """
    for node in ast.walk(ast.parse(source)):
        if not (isinstance(node, ast.Constant) and isinstance(node.value, str)):
            continue
        for match in _SELECT_FROM_METADATA.finditer(node.value):
            projection = _LEADING_QUANTIFIER.sub("", match.group("projection"))
            for item in projection.split(","):
                yield from _projection_columns(item)


def _keyed_names(source: str) -> Iterator[str]:
    """Every string constant ``source`` uses as a mapping key.

    Subscripts (``row["built_at"]``) and the keyed accessors
    (``metadata().get("built_at")``) are the two shapes a column read out of
    :meth:`~theurian.infrastructure.sqlite.index_store.SqliteIndexStore.metadata`
    can take today. The object is not inspected -- a scan that tried to prove the
    receiver was an index-metadata mapping would need type inference, and would
    fail open on exactly the indirection a real consumer introduces.
    """
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Constant):
            if isinstance(node.slice.value, str):
                yield node.slice.value
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in _KEYED_ACCESSORS
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
        ):
            yield node.args[0].value


def _consumers(columns: frozenset[str]) -> dict[str, set[str]]:
    """Every module that names one of ``columns`` as a projection or a mapping key.

    Keyed by column so a failure names the column and the modules, not a count.
    ``*`` rides along under its own key, which is why the parameter is a set of
    names rather than the tuple from the DDL.
    """
    found: dict[str, set[str]] = {column: set() for column in columns}
    for path in sorted(SRC.rglob("*.py")):
        source = path.read_text(encoding="utf-8")
        module = path.relative_to(SRC).as_posix()
        for name in (*_projection_names(source), *_keyed_names(source)):
            if name in found:
                found[name].add(module)
    return found


def _metadata_call_modules() -> set[str]:
    """Every module calling a no-argument ``.metadata()``.

    ADR-0024 decision 2 says ``metadata()`` has *two* callers and that neither
    reads ``built_at``. The second half is what :func:`_consumers` holds; this is
    the first. It matches by name and cannot tell an index store's ``metadata()``
    from any other object's, which over-approximates in the RED direction.
    """
    modules: set[str] = set()
    for path in sorted(SRC.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "metadata"
                and not node.args
                and not node.keywords
            ):
                modules.add(path.relative_to(SRC).as_posix())
    return modules


# -- The scanner, which the absence pin below is worthless without ------------

#: One case per shape the scan claims to see, and per shape it claims to let past.
#:
#: The negatives carry the load. ``SELECT *`` is the one that decides the claim's
#: meaning: the value *is* fetched there, and reading that as a consumer would
#: make ADR-0024's per-column split unstateable. The ``INSERT``/``UPDATE`` cases
#: are the writes the column has -- ``create``'s INSERT and ``_restamp``'s UPDATE
#: both name ``built_at`` -- and a scan that counted a write as a read would be
#: RED on a clean tree with no honest way back to green.
PROJECTION_CASES: Final[tuple[tuple[str, frozenset[str]], ...]] = (
    ('"SELECT built_at FROM index_metadata WHERE id = 1"', frozenset({"built_at"})),
    (
        '"SELECT index_build_id, built_at FROM index_metadata"',
        frozenset({"index_build_id", "built_at"}),
    ),
    ('"select index_metadata.built_at from index_metadata"', frozenset({"built_at"})),
    ('"SELECT built_at AS made FROM index_metadata"', frozenset({"built_at"})),
    ('"SELECT * FROM index_metadata WHERE id = 1"', frozenset({"*"})),
    # -- writes, which name the column and do not read it --------------------
    (
        '"INSERT INTO index_metadata (id, built_at) VALUES (1, ?)"',
        frozenset(),
    ),
    ('"UPDATE index_metadata SET built_at = ? WHERE id = 1"', frozenset()),
    # A write to another table whose value comes *out of* this one is a read of
    # this one, and the inner SELECT is matched on its own terms.
    (
        '"UPDATE nodes SET stamped_at = (SELECT built_at FROM index_metadata)"',
        frozenset({"built_at"}),
    ),
    # -- another table's column of the same name -----------------------------
    ('"SELECT built_at FROM findings_metadata WHERE id = 1"', frozenset()),
    # -- projections that are not a bare column name -------------------------
    # Each of these was read wrong until round one. `DISTINCT` and `COUNT(...)`
    # came back as the literal tokens `DISTINCT built_at` and `COUNT(built_at)`,
    # so a real consumer written either way was invisible to the pin below.
    ('"SELECT DISTINCT built_at FROM index_metadata"', frozenset({"built_at"})),
    ('"SELECT COUNT(built_at) FROM index_metadata"', frozenset({"built_at"})),
    (
        '"SELECT MAX(index_metadata.built_at) AS newest FROM index_metadata"',
        frozenset({"built_at"}),
    ),
    # A row count reads no column, so it is not a consumer of one.
    ('"SELECT COUNT(*) FROM index_metadata WHERE id = 1"', frozenset({})),
    # -- a second statement in the same constant -----------------------------
    # Both of these reported `built_at` before the statement-boundary guard: the
    # span from the first `SELECT` ran through `FROM nodes` to the *second*
    # `FROM index_metadata`, so a column selected out of another table read as a
    # column selected out of this one. That is a false consumer, which reddens
    # the absence pin for a reason that is not true.
    (
        '"SELECT built_at FROM nodes UNION SELECT * FROM index_metadata"',
        frozenset({"*"}),
    ),
    (
        '"SELECT built_at FROM nodes; SELECT * FROM index_metadata WHERE id = 1"',
        frozenset({"*"}),
    ),
    # -- recorded blind spots, asserted rather than only described -----------
    # The module docstring lists what this cannot see; these three pin the SQL
    # half of that list so it stays a measurement. A schema-qualified table name,
    # a table reached through a CTE, and a table reached through a JOIN alias all
    # leave `FROM index_metadata` unmatched by the projection that consumes the
    # column, so a consumer written any of those ways passes. The CTE case still
    # reports the inner `SELECT *`, which is why its expectation is `*` and not
    # the empty set.
    ('"SELECT built_at FROM main.index_metadata WHERE id = 1"', frozenset()),
    (
        '"WITH m AS (SELECT * FROM index_metadata) SELECT built_at FROM m"',
        frozenset({"*"}),
    ),
    ('"SELECT im.built_at FROM nodes JOIN index_metadata im ON 1 = 1"', frozenset()),
    # -- two statements, and the span between them ---------------------------
    # The regression this case exists for, measured rather than imagined: with
    # the pattern applied to module text instead of to one string constant at a
    # time, `DOTALL` let the first `SELECT` reach the *later* `FROM
    # index_metadata` and read everything in between as a projection. Deleting
    # `add_nodes`'s real `SELECT index_build_id` then left the scan still
    # reporting `index_build_id` as consumed, out of an unrelated INSERT column
    # list -- a control satisfied by the accident rather than by the reader.
    (
        'a = c.execute("SELECT node_id FROM nodes")\n'
        "built_at = None\n"
        'b = c.execute("INSERT INTO nodes (index_build_id) VALUES (?)")\n'
        'd = c.execute("SELECT * FROM index_metadata WHERE id = 1")\n',
        frozenset({"*"}),
    ),
)

#: One case per mapping-key shape, and per shape that is not one.
KEYED_CASES: Final[tuple[tuple[str, frozenset[str]], ...]] = (
    ('made = row["built_at"]', frozenset({"built_at"})),
    ('made = store.metadata().get("built_at")', frozenset({"built_at"})),
    ('made = block.pop("built_at", None)', frozenset({"built_at"})),
    # -- shapes that name no key --------------------------------------------
    ("made = row[0]", frozenset()),
    ("made = row[column]", frozenset()),
    ("made = store.metadata().get(column)", frozenset()),
    ('"""`built_at` records when that was made."""', frozenset()),
)


@pytest.mark.parametrize(
    ("source", "expected"),
    PROJECTION_CASES,
    ids=[case[0][:60] for case in PROJECTION_CASES],
)
def test_the_projection_scan_reads_a_select_and_not_a_write(
    source: str, expected: frozenset[str]
) -> None:
    """RED means the SQL half stopped resolving, so the absence pin passes over nothing.

    A pin whose expected result is the empty set cannot tell a clean tree from a
    dead extractor. Every shape the scan claims to catch is asserted here, and
    every shape it claims to let past is asserted to yield nothing -- most of all
    ``SELECT *``, which is what the column claim is *about*: the value is fetched
    on that path and consumed by nobody, and a scan that could not say so would
    make ADR-0024 decision 2's per-column split impossible to state.
    """
    found = set(_projection_names(source))

    assert found == set(expected), (
        f"the projection scan read {source} as {sorted(found)}, expected "
        f"{sorted(expected)}. The scanner is broken, not the product: fix "
        f"`_projection_names` before trusting a green result from "
        f"`test_no_shipped_module_consumes_index_metadata_built_at`."
    )


@pytest.mark.parametrize(
    ("source", "expected"),
    KEYED_CASES,
    ids=[case[0][:60] for case in KEYED_CASES],
)
def test_the_mapping_key_scan_reads_a_named_key_and_not_an_indirect_one(
    source: str, expected: frozenset[str]
) -> None:
    """RED means the Python half stopped resolving, so the absence pin passes over nothing.

    The negatives are the recorded blind spots made explicit rather than merely
    described: ``row[column]`` and ``metadata().get(column)`` yield nothing here
    **on purpose**, and that is the shape a future consumer of ``built_at`` could
    take without this module noticing. Asserting it is what keeps the module
    docstring's limits honest instead of aspirational.
    """
    found = set(_keyed_names(source))

    assert found == set(expected), (
        f"the mapping-key scan read {source!r} as {sorted(found)}, expected "
        f"{sorted(expected)}. The scanner is broken, not the product: fix "
        f"`_keyed_names` before trusting a green result from "
        f"`test_no_shipped_module_consumes_index_metadata_built_at`."
    )


# -- The fact: which columns have a consumer ---------------------------------


def test_the_index_metadata_columns_are_derived_from_the_shipped_ddl() -> None:
    """The population control, asserted before the search that rests on it.

    ADR-0024 decision 2 is a claim about two named columns of one table. If the
    DDL parse returns nothing -- a renamed table, a reformatted ``CREATE TABLE``,
    a move to a builder that no longer emits SQL text -- then every column-keyed
    assertion below iterates over an empty set and reports "no consumer found"
    about a table it never located.

    The three named columns are required because the decision, the ``metadata()``
    contract and this module's own controls each rest on one of them: ``built_at``
    is the claim, ``index_build_id`` is the inverse claim, and
    ``embedding_model`` is the live control proving the mapping-key half resolves
    against real source.
    """
    columns = _index_metadata_columns()

    assert columns, "no `index_metadata` columns were parsed out of INDEX_DDL"
    for required in ("built_at", "index_build_id", "embedding_model"):
        assert required in columns, (
            f"`index_metadata` no longer declares `{required}`, which ADR-0024 "
            f"decision 2 reasons about by name: {list(columns)}"
        )


def test_no_shipped_module_consumes_index_metadata_built_at() -> None:
    """RED means ``built_at`` gained a consumer -- and ADR-0024 decision 2 must say so.

    The claim: ``built_at`` is written by ``create`` and by ``_restamp`` and read
    by nothing, so a purge copy that kept the parent's timestamp until the restamp
    lands is latent rather than broken. The moment something consumes it, the
    decision's second bullet is false and the copy's inherited timestamp is a fact
    some caller can act on.

    **The same pass carries its own controls, from the shipped source.** A scan
    that resolved nothing would report this absence identically, so the assertions
    below require the scan to *find* the consumers the decision names --
    ``index_build_id`` (``add_nodes``), ``index_schema_version``
    (``schema_version``), ``embedding_model`` (``metadata().get`` in
    ``retrieval_service`` and ``withdrawal_purge``) -- and to find the ``SELECT *``
    that makes "fetched but not consumed" the right description of ``built_at``.
    Those controls run first, for the ordering reason
    ``test_adr_0018_claims.py`` records: a premise checked inside the loop it
    guards asserts nothing when the loop is empty.

    What this cannot see is in the module docstring and is not small: a ``SELECT
    *`` row consumed without naming the key would pass.
    """
    columns = frozenset(_index_metadata_columns())
    consumers = _consumers(columns | {"*"})

    named = {"built_at", "index_build_id", "index_schema_version", "embedding_model"}
    assert named <= consumers.keys(), (
        f"`index_metadata` no longer declares {sorted(named - consumers.keys())}, which "
        f"this pin and ADR-0024 decision 2 both reason about by name. Renaming or "
        f"dropping a column the decision cites is a documentation change: see "
        f"`test_the_index_metadata_columns_are_derived_from_the_shipped_ddl`"
    )

    assert consumers["*"], (
        "no `SELECT * FROM index_metadata` remains in the shipped package, so the "
        "SQL half of this scan matched nothing -- and ADR-0024 decision 2's "
        "`metadata() does SELECT *, so the value is fetched` no longer describes "
        "the code"
    )
    for control in ("index_build_id", "index_schema_version", "embedding_model"):
        assert consumers[control], (
            f"the scan found no consumer of `{control}`, which the shipped source "
            f"does have. The scanner is broken, not the product: a pass that "
            f"resolves nothing reports `built_at` as unconsumed for the wrong reason"
        )

    assert not consumers["built_at"], (
        f"`index_metadata.built_at` now has a consumer: "
        f"{sorted(consumers['built_at'])}.\n\n"
        f"ADR-0024 decision 2's second bullet says it is written and never read, "
        f"which is what makes a purge copy's inherited timestamp latent rather than "
        f"broken. A consumer makes that false: `Connection.backup` carries the "
        f"parent's `built_at` into the new file and `index_purge._restamp` is what "
        f"overwrites it, so whatever reads the column has to be correct across that "
        f"window. Correct the decision in the same change that adds the reader."
    )


def test_the_metadata_reader_still_has_the_two_callers_the_decision_names() -> None:
    """RED means a third caller of ``metadata()`` appeared, or one of the two left.

    ADR-0024 decision 2 bounds its ``built_at`` claim with a count: "``metadata()``
    does ``SELECT *``, so the value is fetched, but neither of its two callers
    (``retrieval_service.py``, ``withdrawal_purge.py``) reads that key". The
    per-column scan above holds "does not read the key"; this holds "two callers",
    which is the half a reader checks the sentence against.

    Matched by method name, so it cannot tell an index store's ``metadata()`` from
    any other object's -- an over-approximation in the RED direction, which costs
    a read rather than the claim.
    """
    callers = _metadata_call_modules()

    assert callers == {"application/retrieval_service.py", "application/withdrawal_purge.py"}, (
        f"the no-argument `.metadata()` call sites in the shipped package are "
        f"{sorted(callers)}, not the two ADR-0024 decision 2 names. A new caller "
        f"must be checked against the `built_at` claim and the sentence corrected "
        f"in the same change; a missing one means the sentence describes code that "
        f"is gone."
    )


# -- The prose: what ADR-0024 decision 2 says about each column ---------------

#: Leading Markdown blockquote markers, however deeply nested. Stripped before
#: block detection, so the dated correction note reads as one paragraph rather
#: than as eleven single-line ones -- the same reason
#: ``test_raptor_config_claims.py`` strips them.
_BLOCKQUOTE_MARKERS: Final = re.compile(r"^(?:[ \t]*>)+[ \t]?")

#: A line that begins a new block rather than continuing the one above it,
#: applied after the markers are stripped. Copied from ``test_setup_claims.py``,
#: whose docstring records why a scan that stops at every newline and a scan that
#: ignores newlines are both wrong.
_BLOCK_START: Final = re.compile(r"[ \t]*(?:#{1,6}\s|[-*+]\s|\d+\.\s|\||```|---\s*$)")

#: The dated correction note, keyed on the issue that owns it. Its whole job is to
#: quote the retracted sentence, so a scan that read the quotation would report
#: the amendment that fixed the defect as the defect -- the trap
#: ``test_adr_0018_claims.py`` records for ``_decision_point_two``.
_CORRECTION_NOTE: Final = re.compile(r"\bissues/426\b", re.IGNORECASE)

#: An HTML comment, removed before a positive pin reads Decision point 2.
#: Markdown renders nothing inside one, so a decision whose sentences live in a
#: comment is a silent decision that a substring pin over raw text still passes.
_HTML_COMMENT: Final = re.compile(r"<!--.*?-->", re.DOTALL)

#: Bold markers, removed before :data:`_QUOTATION` looks for italic spans:
#: ``**bold**`` would otherwise read as an italic span, and a live reassertion
#: written in bold inside a note would be excised with the quotations.
_EMPHASIS: Final = re.compile(r"\*\*")

#: A quoted span inside a dated correction note.
#:
#: The exclusion this feeds used to skip the whole note paragraph, which bought a
#: blind spot the size of the note: round-one mutation A7 put a *live*
#: reassertion inside ADR-0024's correction note and nothing saw it, while the
#: same sentence one paragraph away was caught. A note is now scanned with its
#: quoted spans excised -- ADR-0024's note quotes in double quotes, ADR-0008's
#: decision-10 note quotes in italics, so both forms are here -- which hides what
#: a note *quotes* and not what it *asserts*. The same construction and the same
#: fix are in ``test_raptor_config_claims.py``.
_QUOTATION: Final = re.compile(r"\*[^*]+\*|\"[^\"]+\"|“[^”]+”")

#: The retracted claim: that *both* columns are unread.
#:
#: ``reads`` is required with an "either/both/them" object, which is the shape the
#: sentence took ("Nothing in ``src/`` reads either back today"). The corrected
#: text says "neither of its two callers ... reads that key" a few lines below,
#: about ``built_at`` alone and about ``metadata()``'s callers rather than about
#: the columns; requiring the plural object is what separates the two.
_READS_NEITHER_COLUMN: Final = re.compile(
    r"\b(?:nothing|nobody|no code|no module)\b[^\n]{0,40}?"
    r"\breads\b\s+(?:either|both|them)\b",
    re.IGNORECASE,
)

#: The per-column claim in the direction that is now false: that
#: ``index_build_id`` has no reader. This is the sentence the integration test's
#: docstring carried until #426, and the one a future edit is most likely to
#: restore from memory.
_INDEX_BUILD_ID_UNREAD: Final = re.compile(
    r"\b(?:nothing|nobody|no code|no module)\b[^\n]{0,60}?"
    r"\breads\b\s+(?:the\s+)?`?(?:index_metadata\.)?index_build_id",
    re.IGNORECASE,
)

#: Sentences ADR-0024 decision 2 has to keep, whitespace-collapsed.
#:
#: Four, because the split is an argument with four moving parts: which column is
#: read, by what, which column is not, and the premise that makes "not read" the
#: right description of a column a ``SELECT *`` fetches on every call. Drop the
#: last one and the second bullet reads as though ``built_at`` never leaves the
#: database, which is not what the code does.
DECISION_TWO_SENTENCES: Final = (
    "**`index_build_id` is read back.**",
    "`SqliteIndexStore.add_nodes` selects it out of this file's own `index_metadata`",
    "**`built_at` is written and never read**",
    "`metadata()` does `SELECT *`, so the value is fetched",
)

#: One case per form the retraction scans claim to catch, and per form they claim
#: to let past. Without these, both absence pins below could go green against a
#: pattern that matches nothing -- and every negative here is transcribed from the
#: corrected ADR, so a scan that misread one would be RED on a clean tree.
RETRACTION_CASES: Final[tuple[tuple[str, bool], ...]] = (
    ("Nothing in `src/` reads either back today", True),
    ("nothing in `src/` reads both back", True),
    ("Nothing in `src/` reads `index_metadata.index_build_id` back today", True),
    ("no module reads index_build_id", True),
    # -- the corrected wording, which must keep passing ----------------------
    ("Neither column is *served* -- `mcp/search.py` publishes `indexBuildId`", False),
    ("**`built_at` is written and never read**", False),
    (
        "`metadata()` does `SELECT *`, so the value is fetched, but neither of its two "
        "callers (`retrieval_service.py`, `withdrawal_purge.py`) reads that key",
        False,
    ),
    ("**`index_build_id` is read back.**", False),
    ("No SELECT of it anywhere", False),
)


def _collapsed(text: str) -> str:
    """Runs of whitespace flattened to single spaces, case preserved.

    Case is kept because the pinned sentences carry identifiers -- ``built_at``,
    ``SqliteIndexStore.add_nodes``, ``SELECT *`` -- that a lowercasing collapse
    would render as spellings the source does not use. The patterns that need to
    ignore case say so with :data:`re.IGNORECASE`.
    """
    return " ".join(text.split())


def _without_quotations(text: str) -> str:
    """``text`` with its quoted spans replaced by a space.

    Applied to a dated correction note before the note is scanned, so that the
    retracted sentence the note quotes is invisible while anything the note
    *asserts* is not. Emphasis markers go first, for the reason
    :data:`_EMPHASIS` records.
    """
    return _QUOTATION.sub(" ", _EMPHASIS.sub("", text))


def _paragraphs(text: str) -> list[str]:
    """The document's paragraphs, blockquote markers stripped and soft wraps joined."""
    blocks: list[list[str]] = [[]]
    for raw in text.splitlines():
        line = _BLOCKQUOTE_MARKERS.sub("", raw)
        if not line.strip() or _BLOCK_START.match(line):
            blocks.append([])
        blocks[-1].append(line)

    return [collapsed for block in blocks if (collapsed := _collapsed(" ".join(block)))]


def _decision_two(text: str) -> list[str]:
    """ADR-0024 Decision point 2 and its bullets, as a list of collapsed paragraphs.

    Bounded by structure rather than by line numbers: it starts at the numbered
    item that says a purge build is derived from the previous build, and ends at
    the next heading or numbered item, so an amendment added inside the decision
    extends the region rather than escaping it.

    Asserted findable exactly once. A decision that cannot be located is not a
    decision whose wording passed -- it is a scan with nothing to read, which the
    pins below would report as compliance.

    HTML comments are removed first. A decision commented out renders as nothing
    while a substring pin over the raw text stays green -- round-one mutation A6,
    against ``raptor.md``, and the shape is not specific to that file. ADR-0024
    carries no ``<!-- -->`` today, so this is a no-op on the shipped document and
    a closed door on the mutation. ``test_raptor_config_claims.py`` takes the same
    closure for its two regions.
    """
    paragraphs = _paragraphs(_HTML_COMMENT.sub(" ", text))
    starts = [
        index
        for index, paragraph in enumerate(paragraphs)
        if paragraph.startswith("2.") and "A purge build is derived" in paragraph
    ]

    assert len(starts) == 1, (
        f"ADR-0024 Decision point 2 is not findable as exactly one numbered paragraph "
        f"(found {len(starts)}); the pins below would read the wrong region"
    )

    start = starts[0]
    end = next(
        (
            index
            for index in range(start + 1, len(paragraphs))
            if paragraphs[index].startswith("#") or re.match(r"\d+\.\s", paragraphs[index])
        ),
        len(paragraphs),
    )
    return paragraphs[start:end]


@pytest.mark.parametrize(
    ("sentence", "is_retracted"),
    RETRACTION_CASES,
    ids=[case[0][:60] for case in RETRACTION_CASES],
)
def test_the_retraction_scans_see_the_old_wording_and_not_the_split_one(
    sentence: str, is_retracted: bool
) -> None:
    """RED means a retraction scan stopped discriminating, so the pin below asserts nothing.

    Both patterns have an empty expected result against the shipped document, so
    neither can tell a corrected ADR from a dead regex. The pair that matters is
    "nothing ... reads either back" against "neither of its two callers ... reads
    that key": both are denials next to ``reads``, and what separates them is the
    object the verb takes. A looser rule reads the corrected sentence as the
    retracted one and goes RED on the fix.
    """
    collapsed = _collapsed(sentence)
    found = bool(
        _READS_NEITHER_COLUMN.search(collapsed) or _INDEX_BUILD_ID_UNREAD.search(collapsed)
    )

    assert found is is_retracted, (
        f"the retraction scans read {sentence!r} as "
        f"{'the retracted claim' if found else 'acceptable wording'}, expected the "
        f"opposite. The scanner is broken, not the document: fix the patterns before "
        f"trusting a green result from "
        f"`test_adr_0024_does_not_reassert_that_neither_metadata_column_is_read`."
    )


def test_the_correction_note_exclusion_hides_a_quotation_and_not_an_assertion() -> None:
    """RED means the note exclusion is back to skipping whole paragraphs.

    Round-one mutation A7: a live reassertion placed inside the correction note
    was invisible, while the identical sentence one paragraph away was caught. The
    exclusion is quotation-shaped now, so both halves of that shape are asserted
    here against synthetic notes rather than against the shipped ADR -- the
    shipped ADR has no live reassertion in it, and an exclusion that had stopped
    excluding anything would look identical there.
    """
    quoting = (
        "Corrected in the #199 unit-A follow-up "
        "(https://github.com/theurian/theurian/issues/426). This paragraph said "
        '"nothing in `src/` reads either back today". That was true of both columns.'
    )
    asserting = (
        "Corrected in the #199 unit-A follow-up "
        "(https://github.com/theurian/theurian/issues/426). Nothing in `src/` reads "
        "either back today, so this is latent rather than broken."
    )

    assert not _READS_NEITHER_COLUMN.search(_without_quotations(quoting)), (
        "the quoted retraction is reported as the merged claim returning, so the note "
        "that records the fix reads as the defect and this module is RED on a clean tree"
    )
    assert _READS_NEITHER_COLUMN.search(_without_quotations(asserting)), (
        "a live reassertion inside a correction note is invisible, which is round-one "
        "mutation A7. The exclusion must hide the note's quotations, not the note."
    )


def test_adr_0024_decision_two_splits_the_unread_claim_per_column() -> None:
    """RED means the per-column split is gone -- deleted, or softened back to one claim.

    The positive half. It is not the negative one restated: a rewrite that drops
    the bullets entirely asserts nothing false and would pass
    :func:`test_adr_0024_does_not_reassert_that_neither_metadata_column_is_read`
    while leaving the decision silent about a column that *is* read back on the
    purge path it governs.

    The ``SELECT *`` sentence is required with the rest, because it is what makes
    "written and never read" a statement about consumption rather than about
    fetching. Without it the second bullet invites the reading that ``built_at``
    never leaves the file, which is false on every ``metadata()`` call.
    """
    decision = " ".join(_decision_two(ADR_0024.read_text(encoding="utf-8")))

    for sentence in DECISION_TWO_SENTENCES:
        assert sentence in decision, (
            f"ADR-0024 Decision point 2 no longer states {sentence!r}.\n\n"
            f"#426 split this decision's `nothing in `src/` reads either back` claim "
            f"per column: `index_build_id` is read back by `add_nodes`, `built_at` is "
            f"not. `test_no_shipped_module_consumes_index_metadata_built_at` and "
            f"`tests/integration/test_index_build_id_read_back.py` hold the two halves "
            f"against the source. If the code has changed, correct the decision in the "
            f"same change; if it has not, restore the sentence."
        )


def test_adr_0024_does_not_reassert_that_neither_metadata_column_is_read() -> None:
    """RED means the merged claim is back, in one of the two forms it took.

    The negative half, and it catches what the positive one cannot: a decision that
    keeps both bullets and reasserts "nothing reads either back" somewhere else in
    the same point, which is how the paragraph read for a milestone after
    ``add_nodes`` started selecting the column out of the file it writes into.

    Scoped to Decision point 2, with the dated correction note's **quotations**
    excised rather than the note skipped. The note quotes the retracted sentence
    in order to retract it, so a scan that read the quotation would report the fix
    as the defect; skipping the paragraph closed that at the cost of a blind spot
    the size of the note, which round-one mutation A7 walked straight into. The
    served corpus twin ``.theurian/knowledge/architecture/a-purge-is-a-build.<ulid>.md``
    still carries the retracted sentence byte-identically by design (#199 unit C),
    which is why this is not a repo-wide walk either way.
    """
    paragraphs = _decision_two(ADR_0024.read_text(encoding="utf-8"))

    notes = [paragraph for paragraph in paragraphs if _CORRECTION_NOTE.search(paragraph)]
    assert len(notes) == 1, (
        f"ADR-0024 Decision point 2 carries {len(notes)} paragraphs naming issue #426, "
        f"expected exactly one -- the dated correction note recording what the "
        f"paragraph said and why the conclusion survives being split per column. The "
        f"excision below is defined by that note, so it cannot be checked without it"
    )

    claims = [
        scanned
        for paragraph in paragraphs
        if (
            scanned := (
                _without_quotations(paragraph) if _CORRECTION_NOTE.search(paragraph) else paragraph
            )
        )
        and (_READS_NEITHER_COLUMN.search(scanned) or _INDEX_BUILD_ID_UNREAD.search(scanned))
    ]

    assert not claims, (
        "ADR-0024 Decision point 2 asserts again that nothing in `src/` reads the "
        "metadata columns back, which is false of `index_build_id`: "
        "`SqliteIndexStore.add_nodes` selects it out of the file it is writing "
        f"into.\n\n{claims}\n\n"
        "Split the claim per column rather than deleting it -- `built_at` is still "
        "written and never read, and `test_no_shipped_module_consumes_index_metadata_built_at` "
        "is what holds that half."
    )
