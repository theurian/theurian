"""`knowledge.search`'s withheld-content equality, stated without its build identities (#1000).

`test_a_withheld_document_changes_nothing_a_caller_can_see` runs one query against two
corpora that differ by a withheld document and asserts that the `retrieval` fields that
differ are exactly `BUILD_IDENTITY`: `indexBuildId`, a fresh ULID per build that a
withdrawal-purge publish also moves, and `snapshotId`, the canonical-state hash, withheld
items included. So a sentence giving `knowledge.search` whole-response or every-field
equality across withheld content, a source for every `retrieval` field among the results,
the index and the caller's parameters, or an exception set that leaves those two out, is
false unless it names them.

Population (`_population`): every `description` at any depth in `schemas/mcp/*.schema.json`;
every docstring under `packages/theurian-core/src` (module, class, function, nested), by
AST; the text of ADR-0021, ADR-0023 and `docs/security/threat-model.md`; and in
`tests/integration/test_mcp_tools.py` and `tests/unit/test_schemas.py`, every docstring,
every comment block (consecutive `#`/`#:` lines, joined) and the string constants of every
`assert` message. Everything else is outside it -- `docs/protocol/mcp-tools.md`, the
READMEs, the CHANGELOG, other tests, `@_tool(description=...)` strings, the governed twins
under `.theurian/knowledge/` and `tests/fixtures/eval/` -- and so is this file, whose
cases spell the class on purpose. Cost: a false claim in this file's own prose is unseen.

Excluded, as history: every double-quoted span after a `**Corrected in place.**` marker, to
the end of its paragraph, as in `test_adr_0032_decision2_prose.py`. Cost: a live claim in
ASCII double quotes in such a paragraph is unseen. Accepted: each sentence in `ACCEPTED`,
found by file and a span of it, true for the reason it gives; an entry that finds no
flagged sentence, or two, fails. Cost: an edit to an accepted sentence that keeps its span
is unseen.

Sentences (`_sentences`): text is cut at a blank line (a `>`-only line counts), before a
`- ` or `* ` list item and before a `|` table row; then flattened by `record_sentences.flat`,
with U+2019 read as `'`, U+00AD and word-edge `_` dropped, and cut after `.`, `?` or `!`
-- optionally closed by `**`, `*`, `)`, `"`, U+201D, `'`, `]` [and a `(…)` link target] or
a `[^n]` footnote -- that whitespace follows, except after `e.g.`, `i.e.`, `cf.`, `vs.` or
`etc.` and inside a backtick span. A `1. ` item needs no rule: the stop after its number
cuts its text from the line above.
"""

from __future__ import annotations

import ast
import importlib.util
import io
import json
import pathlib
import re
import sys
import tokenize
from collections.abc import Iterator, Sequence
from typing import Final

import pytest
from record_sentences import flat

pytestmark = pytest.mark.unit

REPO_ROOT: Final = pathlib.Path(__file__).resolve().parents[4]
TESTS: Final = REPO_ROOT / "packages" / "theurian-core" / "tests"
SCHEMAS: Final = REPO_ROOT / "schemas" / "mcp"
SRC: Final = REPO_ROOT / "packages" / "theurian-core" / "src"
DOCS: Final = (
    REPO_ROOT / "docs" / "adr" / "0021-rank-fusion-over-score-normalisation.md",
    REPO_ROOT / "docs" / "adr" / "0023-trigram-index-beside-the-word-index.md",
    REPO_ROOT / "docs" / "security" / "threat-model.md",
)
BATTERY: Final = TESTS / "integration" / "test_mcp_tools.py"
TEST_FILES: Final = (BATTERY, TESTS / "unit" / "test_schemas.py")
MARKER: Final = "**Corrected in place.**"


def _battery_build_identity() -> tuple[str, ...]:
    spec = importlib.util.spec_from_file_location("_search_prose_pin_battery", BATTERY)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return tuple(str(name) for name in module.BUILD_IDENTITY)


#: Imported, not retyped: the exemption is whatever the battery excludes.
BUILD_IDENTITY: Final = _battery_build_identity()
_EXEMPT: Final = re.compile(
    "|".join([*map(re.escape, BUILD_IDENTITY), r"build[- ]identit"]), re.IGNORECASE
)

_EVERY: Final = (
    r"\b(?:every|each|all)(?: of)?(?: the| its)?[ -](?:\w+'s )?(?:published )?(?:`?\w+`? )?"
    r"(?:value|field|number)s?\b"
)
_UNIVERSAL: Final = (
    rf"{_EVERY}|\b(?:whole|entire)[- ]response\b|\b(?:whole|entire) `?retrieval`? block\b"
    r"|\bcomparison (?:is )?total\b"
)
_EQUAL: Final = (
    r"\bequal|\bidentical\b|\bindistinguishable\b|\bsame\b|\bunchanged\b|\bunaffected\b"
    r"|\bdoes(?: not|n't) differ\b"
)
_CORPORA: Final = r"\b(?:both|two|across) corpora\b"
_ACROSS: Final = rf"{_EQUAL}|\bcompar|{_CORPORA}|\bnever (?:been )?indexed\b|\bthe property\b"
#: #1010's counterfactual, an index that never held the rows, is another class.
_NOT_NEVER_HELD: Final = r"^(?!.*\bnever held\b)"
_SOURCE: Final = (
    r"\bcaller(?:'s|s')? (?:own )?(?:parameters|arguments)\b"
    r"|\bpropert(?:y|ies) of the index\b"
    r"|\bread off (?:that object|the index)\b"
    r"|\b(?:computed|derived) from (?:the|visible) results\b"
    r"|\bthe request\b"
)
_NEG: Final = r"(?:\bno\b|\bnothing\b|\bnone\b|\bnever\b|\bnot\b|n't\b|\bcannot\b)"
_VERB: Final = r"\b(?:(?:deriv|var[yi]|mov|chang|depend|comput)\w* (?:from|with|on)|counts)\b"
_UNREADABLE: Final = r"(?:withheld|(?:not allowed to|may not|cannot|can't) (?:read|see))"
_SHIFT: Final = r"\b(?:mov|chang|affect)\w*"
_GAP: Final = r"(?:(?!match)[^;]){0,60}?"

#: A sentence is flagged when every pattern of one member matches it.
MEMBERS: Final = {
    "universal_equality": (_UNIVERSAL, _ACROSS, _NOT_NEVER_HELD),
    "response_across_corpora": (r"\bresponse", _EQUAL, _CORPORA, _NOT_NEVER_HELD),
    "source_list": (rf"{_EVERY}|\beverything\b", _SOURCE),
    "returned": (r"\bdescribes? what was \*?returned\b",),
    "negated": (rf"{_NEG}{_GAP}{_VERB}{_GAP}{_UNREADABLE}",),
    "reversed": (rf"{_UNREADABLE}{_GAP}(?:{_SHIFT} (?:no|nothing|none)\b|{_NEG}{_GAP}\baffect)",),
    "battery_holds": (r"changes_nothing_a_caller_can_see", r"\bholds?\b"),
    "exemption_set": (
        r"\bnames its exceptions\b"
        r"|\bthe (?:published )?(?:contract|schema) exempts\b"
        r"|\bstale`? is the (?:deliberate |only )?exception\b",
    ),
}
_COMPILED: Final = {
    name: tuple(re.compile(part, re.IGNORECASE) for part in parts)
    for name, parts in MEMBERS.items()
}

_PARAGRAPH: Final = re.compile(r"\n[ \t>]*\n")
_SEGMENT: Final = re.compile(r"\n[ \t>]*\n|\n(?=[ \t>]*(?:[-*] |\|))")
_STOP: Final = re.compile(r"[.?!](?:\*\*|[*)\"\u201d']|\](?:\([^)\s]*\))?|\[\^\w+\])?(?=\s|$)")
_ABBREVIATION: Final = re.compile(r"\b(?:e\.g|i\.e|cf|vs|etc)\.$")
_EDGE_UNDERSCORE: Final = re.compile(r"(?<!\w)_|_(?!\w)")

#: The true sentences the key flags at HEAD: (file, a span of the sentence, why it is true).
#: "One project" means two queries against one project, whose build identities agree.
ACCEPTED: Final = (
    ("okf_export.py", "the gate withheld moves none of them", "OKF export's values"),
    ("retrieval_service.py", "over every value this module computes", "the ranking's values"),
    ("review_commands.py", "nothing about it varies with anything", "the review CLI's remedies"),
    ("search.py", "What holds *this* call site", "a call-site check, recorded red"),
    ("knowledge-search-response.schema.json", "neither counts a row a", "false; filed as #1017"),
    ("visibility.py", "every field in the response has been arranged not", "timing, not equality"),
    ("admission.py", "handing out the same slot twice", "the admission lock, not search"),
    ("review_finding.py", "Every member value can stay byte-identical", "review findings"),
    ("0023-trigram-index-beside-the-word-index.md", "no whole-response comparison", "a negative"),
    ("threat-model.md", "comparison of the whole `retrieval` block", "one project; a recorded LOW"),
    ("threat-model.md", "It therefore falsifies, as an unqualified", "T-17a history"),
    ("threat-model.md", "| The **equality** — every published value", "T-17a history"),
    ("threat-model.md", "changes nothing, and that is false", "history, called false"),
    ("test_mcp_tools.py", "The whole `retrieval` block, compared field", "one project"),
    ("test_mcp_tools.py", "The whole response, compared field by field.", "one project"),
    ("test_mcp_tools.py", "The **whole response** must be identical --", "knowledge.get"),
    ("test_mcp_tools.py", "the two fields the published schema exempts", "knowledge.status"),
    ("test_mcp_tools.py", "The two `retrieval` blocks are compared whole", "one project"),
    ("test_mcp_tools.py", "and every field agrees", "one project"),
    ("test_mcp_tools.py", "must be identical, not merely the `retrieval`", "one project"),
    ("test_mcp_tools.py", "a whole-response comparison would fail", "a negative"),
    ("test_mcp_tools.py", "naming each field's role", "system.capabilities"),
    ("test_mcp_tools.py", "every other value in this response is swept", "a secret sweep"),
)


def _descriptions(node: object, where: str) -> Iterator[tuple[str, str]]:
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "description" and isinstance(value, str):
                yield f"{where}/description", value
            yield from _descriptions(value, f"{where}/{key}")
    elif isinstance(node, list):
        for index, value in enumerate(node):
            yield from _descriptions(value, f"{where}/{index}")


def _docstrings(tree: ast.AST, label: str) -> Iterator[tuple[str, str]]:
    hosts = ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef
    for node in ast.walk(tree):
        if isinstance(node, hosts) and (doc := ast.get_docstring(node)):
            yield f"{label}:{node.body[0].lineno}", doc


def _comments(source: str, label: str) -> Iterator[tuple[str, str]]:
    blocks: list[tuple[int, list[str]]] = []
    last = -1
    for token in tokenize.generate_tokens(io.StringIO(source).readline):
        if token.type == tokenize.COMMENT:
            if token.start[0] != last + 1:
                blocks.append((token.start[0], []))
            blocks[-1][1].append(re.sub(r"^#:?\s?", "", token.string))
            last = token.start[0]
    for line, lines in blocks:
        yield f"{label}:{line} comment", "\n".join(lines)


def _messages(tree: ast.AST, label: str) -> Iterator[tuple[str, str]]:
    for node in ast.walk(tree):
        if isinstance(node, ast.Assert) and node.msg is not None:
            parts = [
                part.value
                for part in ast.walk(node.msg)
                if isinstance(part, ast.Constant) and isinstance(part.value, str)
            ]
            yield f"{label}:{node.lineno} assert", " ".join(parts)


def _live(text: str) -> str:
    paragraphs = []
    for paragraph in _PARAGRAPH.split(text):
        head, marker, tail = paragraph.partition(MARKER)
        paragraphs.append(head + marker + re.sub(r'"[^"]*"', '""', tail))
    return "\n\n".join(paragraphs)


def _population(
    schemas: pathlib.Path = SCHEMAS,
    src: pathlib.Path = SRC,
    docs: Sequence[pathlib.Path] = DOCS,
    tests: Sequence[pathlib.Path] = TEST_FILES,
) -> dict[str, str]:
    texts = {
        f"{path.name}#{where}": text
        for path in sorted(schemas.glob("*.schema.json"))
        for where, text in _descriptions(json.loads(path.read_text(encoding="utf-8")), "")
    }
    for path in sorted(src.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        texts.update(_docstrings(tree, path.relative_to(src).as_posix()))
    texts.update((path.name, path.read_text(encoding="utf-8")) for path in docs)
    for path in tests:
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        texts.update(_docstrings(tree, path.name))
        texts.update(_messages(tree, path.name))
        texts.update(_comments(source, path.name))
    return {where: _live(text) for where, text in texts.items()}


def _sentences(text: str) -> Iterator[str]:
    for segment in _SEGMENT.split(text):
        flattened = flat(segment).replace("\u2019", "'").replace("\u00ad", "")
        flattened = _EDGE_UNDERSCORE.sub("", flattened)
        start = 0
        for stop in _STOP.finditer(flattened):
            inside_code = flattened[: stop.start()].count("`") % 2
            if inside_code or _ABBREVIATION.search(flattened[: stop.start() + 1]):
                continue
            yield flattened[start : stop.end()].strip()
            start = stop.end()
        if rest := flattened[start:].strip():
            yield rest


def _members(sentence: str) -> list[str]:
    return [name for name, parts in _COMPILED.items() if all(p.search(sentence) for p in parts)]


def _flagged(population: dict[str, str]) -> list[tuple[str, str]]:
    return [
        (where, sentence)
        for where, text in population.items()
        for sentence in _sentences(text)
        if _members(sentence) and not _EXEMPT.search(sentence)
    ]


def _file(where: str) -> str:
    return re.split(r"[#:]", where, maxsplit=1)[0].rsplit("/", 1)[-1]


def _accepted_by(flagged: list[tuple[str, str]]) -> dict[str, list[tuple[str, str]]]:
    return {
        span: [(where, s) for where, s in flagged if _file(where) == file and span in s]
        for file, span, _ in ACCEPTED
    }


def test_no_live_sentence_gives_search_the_equality_without_its_build_identities() -> None:
    """Key (case-insensitive, per sentence; each is a `MEMBERS` entry):

    - `universal_equality`: a universal -- `every`, `each` or `all` [`of`] [`the`|`its`], a
      space or hyphen, then [a `…'s` possessive] [`published`] [one word, backticked or not]
      `value`/`field`/`number`[`s`]; `whole`/`entire` `response` (or `whole-`/
      `entire-response`); `whole`/`entire` [`]retrieval[`] block`; `comparison [is] total`
      -- with an equality word (`equal…`, `identical`, `indistinguishable`, `same`,
      `unchanged`, `unaffected`, `does not`/`doesn't differ`), `compar…`, a corpus phrase
      (`both`/`two`/`across corpora`), `never [been] indexed` or `the property`; and no
      `never held`.
    - `response_across_corpora`: `response…` with an equality word and a corpus phrase, and
      no `never held`.
    - `source_list`: that `every`/`each`/`all` subject, or `everything`, with a source --
      `caller's`/`callers'`/`caller` [`own`] `parameters`/`arguments`;
      `property`/`properties of the index`; `read off that object`/`the index`;
      `computed`/`derived from the`/`visible results`; `the request`.
    - `returned`: `describe[s] what was [*]returned`.
    - `negated`: `no`, `nothing`, `none`, `never`, `not`, `n't` or `cannot`; then a verb --
      `deriv…`, `vary`/`vari…`, `mov…`, `chang…`, `depend…`, `comput…`, each before `from`,
      `with` or `on`, or `counts`; then content the caller may not read -- `withheld`, or
      `not allowed to`/`may not`/`cannot`/`can't` before `read`/`see`. Each gap is at
      most 60 characters and holds neither `;` nor `match`.
    - `reversed`: that unreadable content; a gap as above; `mov…`, `chang…` or `affect…`;
      then `no`, `nothing` or `none`; or that content, a gap, a negation, a gap, `affect…`.
    - `battery_holds`: the battery's name with `hold[s]`.
    - `exemption_set`: `names its exceptions`; `the [published] contract`/`schema exempts`;
      `stale[`] is the [deliberate|only] exception`.

    Exempt: a sentence naming a `BUILD_IDENTITY` member or `build identit…`/`build-identit…`
    -- so a false sentence that names either field, or the phrase, for any reason passes.
    `stateHash` is not an exemption: the restored "the third names its exceptions" sentence
    names status's `stateHash` while giving search none, and an exemption on it read that
    sentence as true.

    Not required: the bound the residuals T-17a records put on the universal (an
    `--include-unapproved` build's BM25 statistics, the windows a pre-purge build still
    answers in). A universal naming the build identities but not those residuals passes,
    and the query-matched rule ("no field varies with what a query matched among content
    the caller may not read") is not read, with or without them.

    Blind: every word outside the closed lists above. The universals are exactly those
    listed -- `any`, `full` and `complete` are not, and two or more modifiers before the
    noun pass -- and each needs an equality word, `compar…`, a corpus phrase,
    `never [been] indexed` or `the property` beside it. The
    equality words are exactly those listed -- `agrees`, `matches`, `coincides`,
    `is consistent` and `differ…` without `does not`/`doesn't` are not -- and the corpus
    phrases exactly `both`/`two`/`across corpora`. A sentence saying `never held` is read
    as #1010's counterfactual and passes, whatever else it claims. A source list needs the
    `every`/`each`/`all`/`everything` subject. A negation, verb and unreadable term more
    than 60 characters apart, or a verb or preposition outside the list (`carry`, `leak`,
    `reach`, `by`), pass; so does `match` between them, read as the query-matched rule.
    Anaphora other than the battery's name with `hold` ("search answers the same"), an
    exception set in other words ("only status has exceptions") and markup inside a phrase
    pass.

    Measured: zero unaccepted hits at 4d017afe plus this file. RED drivers, each alone in a
    private worktree at that tree: each of 23 sentences of #1000's faces F-a to F-m
    restored in its host from bd84393c.
    """
    flagged = _flagged(_population())
    accepted = {pair for pairs in _accepted_by(flagged).values() for pair in pairs}

    unaccepted = [pair for pair in flagged if pair not in accepted]

    assert not unaccepted, (
        "knowledge.search's battery excludes the build identities by name: name them, or "
        "state the query-matched rule the retrieval-metadata schema states (#1000).\n"
        + "\n".join(map(repr, unaccepted))
    )


def test_every_accepted_sentence_is_still_flagged_exactly_once() -> None:
    accepted = _accepted_by(_flagged(_population()))

    assert {span: len(pairs) for span, pairs in accepted.items() if len(pairs) != 1} == {}


def test_the_population_reaches_each_host_the_class_was_found_in() -> None:
    population = _population()
    tests = {path.name for path in TEST_FILES}
    hosts = {re.split(r"[#:]", where, maxsplit=1)[0] for where in population}
    kinds = {
        (_file(where), where.rpartition(" ")[2] if " " in where else "docstring")
        for where in population
        if _file(where) in tests
    }

    assert {path.name for path in SCHEMAS.glob("*.schema.json")} <= hosts
    assert {"theurian/application/retrieval_service.py", "theurian/mcp/search.py"} <= hosts
    assert {path.name for path in DOCS} <= hosts
    assert kinds == {(name, kind) for name in tests for kind in ("docstring", "comment", "assert")}


#: Each host's text, holding a neutral sentence or the false one wrapped across two lines.
_HOSTS: Final = {
    "schema": ("Neutral.", "Every published value is\nequal across two corpora."),
    "src": ("Neutral.", "Every published value is\n        equal across two corpora."),
    "adr": ("Neutral.", "Every published value is\nequal across two corpora."),
    "threat-model": ("Neutral.", "Every published value is\nequal across two corpora."),
    "docstring": ("Neutral.", "Every published value is\n    equal across two corpora."),
    "comment": ("Neutral.", "Every published value is\n    # equal across two corpora."),
    "assert": ("Neutral.", 'Every published value is "\n        "equal across two corpora.'),
}


@pytest.mark.parametrize("host", list(_HOSTS))
def test_a_planted_sentence_is_flagged_through_each_host(tmp_path: pathlib.Path, host: str) -> None:
    text = {name: pair[name == host] for name, pair in _HOSTS.items()}
    schemas, package = tmp_path / "schemas", tmp_path / "src" / "pkg"
    package.mkdir(parents=True)
    schemas.mkdir()
    nested = {"description": "Neutral.", "properties": {"x": {"description": text["schema"]}}}
    (schemas / "a.schema.json").write_text(json.dumps(nested), encoding="utf-8")
    (package / "m.py").write_text(
        f'def outer():\n    def inner():\n        """{text["src"]}"""\n', encoding="utf-8"
    )
    adr, threat_model = tmp_path / "adr.md", tmp_path / "threat-model.md"
    adr.write_text(text["adr"], encoding="utf-8")
    threat_model.write_text(text["threat-model"], encoding="utf-8")
    test = tmp_path / "test_x.py"
    test.write_text(
        f'def test_x():\n    assert True, ("{text["assert"]}")\n'
        f"    # {text['comment']}\n"
        f'\n\ndef test_y():\n    """{text["docstring"]}"""\n',
        encoding="utf-8",
    )

    hits = [
        where
        for where, _ in _flagged(_population(schemas, package.parent, (adr, threat_model), (test,)))
    ]

    assert hits == [
        {
            "schema": "a.schema.json#/properties/x/description",
            "src": "pkg/m.py:3",
            "adr": "adr.md",
            "threat-model": "threat-model.md",
            "assert": "test_x.py:2 assert",
            "comment": "test_x.py:3 comment",
            "docstring": "test_x.py:7",
        }[host]
    ]


def test_only_quoted_history_after_the_marker_is_excluded() -> None:
    history = f'{MARKER} This said: "every published value is equal".'
    live = f"{MARKER} Every published value is equal."

    assert not _flagged({"doc": _live(history)})
    assert _flagged({"doc": _live(live)})


#: One case per documented branch: deleting that branch from its member turns its case RED.
MEMBER_CASES: Final = [
    ("every", "universal_equality", "Every value is equal."),
    ("all", "universal_equality", "All fields are equal."),
    ("published", "universal_equality", "Every published number is equal."),
    ("whole-response", "universal_equality", "A whole-response comparison holds."),
    ("whole response", "universal_equality", "The whole response is equal."),
    ("whole block", "universal_equality", "The whole `retrieval` block is equal."),
    ("comparison total", "universal_equality", "The comparison total holds."),
    ("comparison is total", "universal_equality", "The comparison is total."),
    ("identical", "universal_equality", "Every field is identical."),
    ("indistinguishable", "universal_equality", "Every field is indistinguishable."),
    ("compar", "universal_equality", "Every field is compared."),
    ("two corpora", "universal_equality", "Every field agrees across two corpora."),
    ("both corpora", "universal_equality", "Every field agrees across both corpora."),
    ("across corpora", "universal_equality", "Every field agrees across corpora."),
    ("each", "universal_equality", "Each field is equal."),
    ("all of the", "universal_equality", "All of the fields are equal."),
    ("all its", "universal_equality", "All its values are equal."),
    ("possessive", "universal_equality", "Every response's field is equal."),
    ("every-field", "universal_equality", "Search gives every-field equality across two corpora."),
    ("each of the", "universal_equality", "Each of the fields is equal."),
    ("each of its", "universal_equality", "Each of its values is identical."),
    ("modifier", "universal_equality", "Every retrieval value stays the same."),
    ("backticked modifier", "universal_equality", "Every `retrieval` field is equal."),
    ("published modifier", "universal_equality", "All published retrieval fields are equal."),
    ("entire response", "universal_equality", "The entire response is equal."),
    ("entire block", "universal_equality", "The entire `retrieval` block is equal."),
    ("same", "universal_equality", "Every field is the same."),
    ("unchanged", "universal_equality", "The whole response is unchanged."),
    ("unaffected", "universal_equality", "Every field is unaffected."),
    ("does not differ", "universal_equality", "Every field does not differ."),
    ("doesn't differ", "universal_equality", "Every field doesn't differ."),
    (
        "response across corpora",
        "response_across_corpora",
        "The `knowledge.search` response is identical across both corpora.",
    ),
    ("never indexed", "universal_equality", "Every field is as if it was never indexed."),
    ("never been indexed", "universal_equality", "Every field is as if it had never been indexed."),
    ("the property", "universal_equality", "Every field keeps the property."),
    ("everything", "source_list", "Everything is read off the index."),
    ("caller's", "source_list", "Every field describes the caller's parameters."),
    ("callers'", "source_list", "Every field describes the callers' parameters."),
    ("own", "source_list", "Every field describes the caller's own parameters."),
    ("arguments", "source_list", "Every field describes the caller's arguments."),
    ("property of the index", "source_list", "Every field is a property of the index."),
    ("properties", "source_list", "All fields are properties of the index."),
    ("read off that object", "source_list", "Every field is read off that object."),
    ("computed from the results", "source_list", "Every field is computed from the results."),
    ("derived from the results", "source_list", "Every field is derived from the results."),
    ("visible results", "source_list", "Every field is computed from visible results."),
    ("the request", "source_list", "Every field describes the request."),
    ("returned", "returned", "It describes what was *returned*."),
    ("no", "negated", "No field is derived from withheld content."),
    ("nothing", "negated", "Nothing is derived from withheld content."),
    ("never", "negated", "A field is never derived from withheld content."),
    ("not", "negated", "A field is not derived from withheld content."),
    ("n't", "negated", "A field isn't derived from withheld content."),
    ("cannot", "negated", "A field cannot be derived from withheld content."),
    ("none", "negated", "None of the fields depends on withheld content."),
    ("vary", "negated", "No field may vary with withheld content."),
    ("varies", "negated", "No field varies with withheld content."),
    ("mov", "negated", "No field moves with withheld content."),
    ("chang", "negated", "No field changes with withheld content."),
    ("depend", "negated", "No field depends on withheld content."),
    ("comput", "negated", "No field is computed from withheld content."),
    ("counts", "negated", "No statistic counts a withheld row."),
    ("not allowed to", "negated", "No field is derived from what it is not allowed to read."),
    ("may not", "negated", "No field is derived from rows the caller may not read."),
    ("cannot read", "negated", "No field is derived from rows the caller cannot read."),
    ("can't", "negated", "No field is derived from rows the caller can't read."),
    ("see", "negated", "No field is derived from rows the caller may not see."),
    ("moves no", "reversed", "Withheld content moves no field."),
    ("changes nothing", "reversed", "A withheld document changes nothing."),
    ("affects none", "reversed", "Withheld content affects none of them."),
    ("does not affect", "reversed", "Withheld content does not affect any field."),
    (
        "battery holds",
        "battery_holds",
        "`test_a_withheld_document_changes_nothing_a_caller_can_see` holds that outright.",
    ),
    ("names its exceptions", "exemption_set", "The third names its exceptions."),
    ("contract exempts", "exemption_set", "Exactly the two fields the contract exempts."),
    ("schema exempts", "exemption_set", "Exactly the fields the schema exempts."),
    ("published contract", "exemption_set", "The fields the published contract exempts."),
    ("deliberate exception", "exemption_set", "`stale` is the deliberate exception."),
    ("only exception", "exemption_set", "stale is the only exception."),
]


@pytest.mark.parametrize(
    ("member", "sentence"),
    [case[1:] for case in MEMBER_CASES],
    ids=[case[0] for case in MEMBER_CASES],
)
def test_each_key_member_flags_its_phrasing(member: str, sentence: str) -> None:
    assert member in _members(sentence)


#: One case per documented boundary rule: a false sentence the rule keeps whole, or keeps
#: apart from a neighbour naming `snapshotId`.
SENTENCE_CASES: Final = [
    ("blank line", "Every published value is equal\n\n`snapshotId` moves"),
    ("quoted blank line", "> Every published value is equal\n>\n> `snapshotId` moves"),
    ("dash item", "Every published value is equal\n- `snapshotId` moves"),
    ("star item", "Every published value is equal\n* `snapshotId` moves"),
    ("table row", "| every published value is equal |\n| `snapshotId` moves |"),
    ("full stop", "Every published value is equal. `snapshotId` moves."),
    ("question mark", "Is every published value equal? `snapshotId` moves."),
    ("exclamation mark", "Every published value is equal! `snapshotId` moves."),
    ("bold close", "**Every published value is equal.** `snapshotId` moves."),
    ("italic close", "*Every published value is equal.* `snapshotId` moves."),
    ("paren close", "(Every published value is equal.) `snapshotId` moves."),
    ("quote close", '"Every published value is equal." `snapshotId` moves.'),
    ("e.g.", "No field, e.g. `count`, is derived from withheld content."),
    ("i.e.", "No field, i.e. `count`, is derived from withheld content."),
    ("code span", "No field `a. b` is derived from withheld content."),
    ("curly apostrophe", "The response doesn\u2019t vary with withheld content."),
    ("U+201D close", "\u201cEvery published value is equal.\u201d `snapshotId` moves."),
    ("U+2019 close", "\u2018Every published value is equal.\u2019 `snapshotId` moves."),
    ("apostrophe close", "'Every published value is equal.' `snapshotId` moves."),
    ("bracket close", "[Every published value is equal.] `snapshotId` moves."),
    ("link close", "[Every published value is equal.](x) `snapshotId` moves."),
    ("footnote", "Every published value is equal.[^1] `snapshotId` moves."),
    ("cf.", "No field (cf. T-17a) is derived from withheld content."),
    ("vs.", "No field (vs. the scan) is derived from withheld content."),
    ("etc.", "No field, nor its sources etc. is derived from withheld content."),
    ("soft hyphen", "No field is derived from with\u00adheld content."),
    ("underscore italics", "_Every published value is equal._ `snapshotId` moves."),
]


@pytest.mark.parametrize(
    "text", [case[1] for case in SENTENCE_CASES], ids=[case[0] for case in SENTENCE_CASES]
)
def test_each_sentence_boundary_rule_keeps_a_false_sentence_visible(text: str) -> None:
    assert _flagged({"case": text})


#: Green controls, outside the class: each turns RED if the rule its id names is deleted.
TRUE_CASES: Final = [
    *(
        (f"exempt {name}", f"Every published value is equal bar `{name}`.")
        for name in BUILD_IDENTITY
    ),
    ("exempt build identities", "Every published value is equal bar the build identities."),
    ("exempt build-identity", "Every published value is equal bar each build-identity field."),
    ("semicolon bounds a gap", "Nothing is derived from the index; withheld rows are dropped."),
    (
        "60-character gap",
        "No field may be derived from the index alone, since its build may have changed long "
        "before anything was withheld.",
    ),
    (
        "match keeps the rule",
        "No field varies with what a query matched among content the caller may not read.",
    ),
    (
        "one field's sources",
        "`embeddingModel` depends only on the index and on the caller's own parameters.",
    ),
    (
        "plural exceptions",
        "The equality covers three tools, and two of them name their exceptions.",
    ),
    (
        "never held (universal)",
        "The whole response is the same for an index that never held the withheld documents.",
    ),
    (
        "never held (corpora)",
        "The response is the same across two corpora, one of which never held the rows.",
    ),
    ("no response word", "The two corpora hold the same crowd."),
    ("no equality word", "The response is built from two corpora."),
    ("no corpus phrase", "The response is identical to the last one."),
    (
        "#1010's shape",
        "An index holding the document and an index that never held it return the same response.",
    ),
]


@pytest.mark.parametrize(
    "sentence", [case[1] for case in TRUE_CASES], ids=[case[0] for case in TRUE_CASES]
)
def test_a_true_sentence_is_not_flagged(sentence: str) -> None:
    assert not _flagged({"case": sentence})


def test_naming_status_s_state_hash_does_not_exempt_a_sentence() -> None:
    assert _flagged({"case": "The third names its exceptions: `stateHash` moves."})


#: The universal's load-bearing statements, each found by its lead phrase.
_STATEMENTS: Final = {
    "retrieval-metadata.schema.json#/description": "No field varies with what a query matched",
    "theurian/mcp/search.py:1": "The invariant this module exists to hold",
    "theurian/application/retrieval_service.py:1": "The property this module exists to hold",
}


@pytest.mark.parametrize(("where", "lead"), list(_STATEMENTS.items()), ids=list(_STATEMENTS))
def test_each_statement_of_the_universal_carries_t17a_s_reach(where: str, lead: str) -> None:
    """C8 (#1013 R1 H1): without "outside the residuals T-17a records" the universal is
    false for a default caller an `--include-unapproved` build serves. Key: the paragraph
    holding *lead*, whitespace collapsed. Out of scope: sentences on what the battery
    asserts, which describe its default-build fixture."""
    text = _population().get(where, "")
    paragraphs = [flat(part) for part in _PARAGRAPH.split(text) if lead in flat(part)]

    assert paragraphs, f"{where}: no paragraph holds {lead!r}"
    assert all("outside the residuals T-17a records" in part for part in paragraphs), where


def test_the_records_name_the_battery_s_build_identities() -> None:
    """The exemption above is `BUILD_IDENTITY`; the records must name the same pair. Key:
    `build identities[,]` then two backticked names joined by `[,] and`, over the
    population; and the `retrieval-metadata` description's list of fields that "move with
    project state, the build, or the caller's own parameters", which is `BUILD_IDENTITY`
    with `stale` and `note`.
    Measured at 4d017afe: eight pairs. RED drivers: `BUILD_IDENTITY` given `stale`, or
    without `snapshotId`; `note` dropped from that list.
    """
    text = flat("\n".join(_population().values()).replace("``", "`"))
    pairs = re.findall(r"build identities,? `(\w+)`,? and `(\w+)`", text)
    description = json.loads((SCHEMAS / "retrieval-metadata.schema.json").read_text("utf-8"))
    moving = re.search(
        r"((?:`\w+`(?:, | and ))+`\w+`) move with project state, the build",
        description["description"],
    )

    assert pairs
    assert {frozenset(pair) for pair in pairs} == {frozenset(BUILD_IDENTITY)}
    assert moving is not None
    assert set(re.findall(r"`(\w+)`", moving.group(1))) == {*BUILD_IDENTITY, "stale", "note"}
