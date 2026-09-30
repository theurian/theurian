"""The rules a post-gate record is held to, and the locators that find the record (#832).

`test_adr_0038_gate_read_records.py` holds the tables; this module holds what
they are held *by*, so `test_gate_read_rules.py` can drive every rule over
synthetic text beside a control. Nothing here reads a document by name.

**The checks of `_problems`,** over one record:

1. its scope spells none of `_FORBIDDEN`;
2. the body readers named in its scope equal the derived readers of the gate it
   describes (the scope is the whole record, or the sentences from an anchor);
3. it names the call sites derived for that gate, by symbol;
4. in each clause that names a call site, the reads it names are that site's: the
   reads it makes after a row clears and, for a site that also decides
   (`knowledge_get`, `_relation_is_visible`), the read it decides on. The content
   check may be named by its role phrase (`_ROLE`) as well as by symbol;
5. optionally, it names the read the gate decides on, and a clause naming
   `_relation_is_visible` names that read.

**Known-weak halves.** A clause ends at `record_sentences.clauses`: ``;``, ``--``,
an em dash or ``, and ``. The last is the Oxford comma: "A, B, and C" is cut before
C, while "A, B and C" is one clause, so a second read joined by a bare comma or by
a plain "and" is read with the first. Check 4 is exclusion within a clause, not
pairing: two call sites in one clause may swap their reads. `_FORBIDDEN` is the
phrases the pre-fix records used, and a false claim in other words passes it.
Reads are matched by name, so a record that says "the joined read" and names none
is held only by check 3. The rules hold reader *names*, not the quantifiers a
sentence attaches to them ("for every row that is served", "for the first
`limit`"): a record that names the right readers and says "served" of the wrong
rows passes. #870 (https://github.com/theurian/theurian/issues/870) owns holding
them.
"""

from __future__ import annotations

import ast
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Final, NamedTuple

from gate_read_facts import READ_NAMES, REPO_ROOT, Gate, tree
from record_sentences import clauses, flat, from_anchor, not_found, sentences

_READ_NAME: Final = re.compile(
    r"(?<![A-Za-z0-9_])(?:get_item[a-z_]*|get_revision|current_revision)(?![A-Za-z0-9_])"
)

#: Phrases the records used before #832 for what is false of them. Case-insensitive.
_FORBIDDEN: Final = (
    r"\bonce\b",
    r"going to be served",
    r"no extra per-row",
    r"\bthe gate read\b",
    r"\bgate-read\b",
    r"\bthe gate reads carry\b",
    r"\bthe gate, which always reads\b",
    r"\bread the serve gate uses\b",
)

#: How a record spells the call site that hashes served content, besides its symbol.
_ROLE: Final = (r"content(?:-identity)? check",)


def _names(text: str, universe: frozenset[str] = READ_NAMES) -> list[str]:
    return [name for name in _READ_NAME.findall(text) if name in universe]


# -- Locators: each returns the record's text or fails with "record not found" ----


def _module_doc(path: str) -> str:
    return ast.get_docstring(tree(REPO_ROOT / path)) or not_found(f"{path} has no docstring")


def _doc(path: str, function: str, cls: str | None = None) -> str:
    module = tree(REPO_ROOT / path)
    scope: ast.AST = module
    if cls is not None:
        owners = [n for n in ast.walk(module) if isinstance(n, ast.ClassDef) and n.name == cls]
        if len(owners) != 1:
            not_found(f"{len(owners)} classes named {cls} in {path}")
        scope = owners[0]
    found = [
        n
        for n in ast.walk(scope)
        if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef) and n.name == function
    ]
    if len(found) != 1:
        not_found(f"{len(found)} functions named {function} in {cls or path}")
    return ast.get_docstring(found[0]) or not_found(f"{function} has no docstring")


def _blocks(path_text: str) -> list[str]:
    return [block for block in re.split(r"\n[ \t]*\n", path_text) if block.strip()]


def _block(path_text: str, needle: str) -> str:
    found = [block for block in _blocks(path_text) if needle in block]
    if len(found) != 1:
        not_found(f"{len(found)} blocks carry {needle!r}")
    return found[0]


def _row(text: str, prefix: str, needle: str) -> str:
    found = [line for line in text.splitlines() if line.startswith(prefix) and needle in line]
    if len(found) != 1:
        not_found(f"{len(found)} rows start {prefix!r} and carry {needle!r}")
    return found[0]


def _lines(path: str) -> list[str]:
    return (REPO_ROOT / path).read_text(encoding="utf-8").splitlines()


def _comment(path: str, needle: str) -> str:
    """The one run of consecutive comment lines carrying *needle*."""
    runs: list[list[str]] = [[]]
    for line in _lines(path):
        if line.lstrip().startswith("#"):
            runs[-1].append(line)
        elif runs[-1]:
            runs.append([])
    found = ["\n".join(run) for run in runs if needle in "\n".join(run)]
    if len(found) != 1:
        not_found(f"{len(found)} comment runs in {path} carry {needle!r}")
    return found[0]


def _comment_above(path: str, statement: str) -> str:
    """The `#:` run directly above the line that starts with *statement*."""
    lines = _lines(path)
    starts = [i for i, line in enumerate(lines) if line.lstrip().startswith(statement)]
    if len(starts) != 1:
        not_found(f"{len(starts)} lines in {path} start {statement!r}")
    above: list[str] = []
    for line in reversed(lines[: starts[0]]):
        if not line.lstrip().startswith("#:"):
            break
        above.insert(0, line)
    return "\n".join(above)


def _bullet(block: str, head: str) -> str:
    found = [part for part in re.split(r"(?m)^(?=- )", block) if part.startswith(f"- {head}")]
    if len(found) != 1:
        not_found(f"{len(found)} bullets start {head!r}")
    return found[0]


def _lead(block: str) -> str:
    return re.split(r"(?m)^(?=- )", block)[0]


# -- The rules -------------------------------------------------------------------


@dataclass(frozen=True)
class PostGate:
    label: str
    text: Callable[[], str]
    gate: Callable[[], Gate]
    #: The anchor whose sentences are held to `gate`'s readers; empty holds the whole record.
    at: str = ""
    span: int = 1
    #: Whether the scope's body readers must equal the gate's; off where it names others too.
    exact: bool = True
    #: `all` names every derived call site of the gate, `some` at least one, `none` asks none.
    sites: str = "all"
    #: The record must also name the read the gate decides on.
    names_gate_read: bool = False
    #: A clause naming `_relation_is_visible` must name its read.
    names_relation_read: bool = False
    #: The record contrasts the deciding reads with others; clauses naming those gates are not read.
    contrast: bool = False


def _site_patterns(site: str) -> list[str]:
    """A call site as records spell it: its symbol, `knowledge.get` for the tool, its role."""
    spelled = [re.escape(site)]
    if not site.startswith("_"):
        spelled.append(re.escape(site.replace("_", ".", 1)))
    return spelled


def _symbol_mentions(text: str, site: str) -> list[re.Match[str]]:
    return [
        m
        for pattern in _site_patterns(site)
        for m in re.finditer(rf"(?<![A-Za-z0-9_]){pattern}(?![A-Za-z0-9_])", text)
    ]


def _mentions(sentence: str, gate: Gate, site: str, joined: frozenset[str]) -> list[re.Match[str]]:
    """Where *sentence* names *site*: by symbol, and for the content check by its role too."""
    found = _symbol_mentions(sentence, site)
    if gate.after.get(site, frozenset()) & joined:
        found += [m for role in _ROLE for m in re.finditer(role, sentence, re.IGNORECASE)]
    return found


class Facts(NamedTuple):
    """What a record is held to: every gate, the body readers, and the served-content join's."""

    every: Mapping[str, Gate]
    bodies: frozenset[str]
    joined: frozenset[str]


def _problems(text: str, record: PostGate, gate: Gate, facts: Facts) -> list[str]:
    every, bodies, joined = facts
    found: list[str] = []
    whole = flat(text)
    scope = from_anchor(text, record.at, span=record.span) if record.at else whole

    for phrase in _FORBIDDEN:
        if re.search(phrase, scope, re.IGNORECASE):
            found.append(f"spells {phrase!r}, which the pre-fix record used for what is false")

    named = set(_names(scope, bodies))
    if record.exact and named != gate.readers:
        found.append(
            f"names the body readers {sorted(named)}; the derived readers of the "
            f"{gate.name} gate are {sorted(gate.readers)}"
        )

    spelled = {site for site in gate.sites if _symbol_mentions(whole, site)}
    if record.sites == "all" and spelled != gate.sites:
        found.append(f"names {sorted(spelled)} of the call sites {sorted(gate.sites)}")
    if record.sites == "some" and not spelled:
        found.append(f"names none of the call sites {sorted(gate.sites)}")

    if record.names_gate_read and not gate.read <= set(_names(whole)):
        found.append(f"does not name the read the gate decides on, {sorted(gate.read)}")

    relation = every["relation"]
    relation_clauses: list[str] = []
    for sentence in sentences(text):
        for clause in clauses(sentence):
            mentioned = [
                (other, site)
                for other in every.values()
                for site in other.sites
                if _mentions(clause, other, site, joined)
                and not (record.contrast and other.decides)
            ]
            if not mentioned:
                continue
            if any(other is relation for other, _ in mentioned):
                relation_clauses.append(clause)
            universe = READ_NAMES if any(other.decides for other, _ in mentioned) else bodies
            allowed = frozenset().union(*(other.allowed_near(site) for other, site in mentioned))
            extra = set(_names(clause, universe)) - allowed
            if extra:
                sites = sorted({site for _, site in mentioned})
                found.append(f"a clause naming {sites} names {sorted(extra)}: {clause.strip()!r}")
    if record.names_relation_read and not any(
        relation.read <= set(_names(clause)) for clause in relation_clauses
    ):
        found.append(f"no clause naming `_relation_is_visible` names {sorted(relation.read)}")

    return found
