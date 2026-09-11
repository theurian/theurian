"""Who imports ``WriteSection``, and why the count is the thing being watched.

``application/findings_builder.py`` declares ``WriteSection`` -- the factory for
the critical section a builder publishes inside -- and its own note records a
decision that is keyed on *how many* modules import it:

    Moving it to a neutral home was considered and not done: the alias is four
    tokens with no behaviour, the move touches two packages and two composition
    roots to buy a better filename, and a second builder is where that trade
    starts paying rather than where it has paid. **A third builder is where it
    should be made.**

That is a decision with a trigger, and nothing was watching the trigger. The
note also states the population in prose -- three modules, named -- which is a
count written into a comment, the shape this project has paid for once per
document (``test_documented_tool_set.py``'s opening records four of them). A
fourth importer would falsify the sentence and pass the trigger in one commit,
silently.

So the population is derived here rather than trusted, and it fails **in the
direction that matters**: an importer arriving is RED, and the change that adds
it has to come here, say so, and decide the home question the note defers. It is
not a lint rule against importing the name -- the three importers are correct,
and one of them is the review search builder this file set is about.

**What the key sees, stated as narrowly as it is true.** Every ``.py`` under
``packages/theurian-core/src`` is parsed, and a module joins the population when
its syntax tree either imports the name through a ``from ... import`` -- whatever
module it names, so a re-export is caught too -- or reaches it as an attribute
(``findings_builder.WriteSection``). A name reached by ``getattr`` with a
computed string is invisible to this and to every other reader, and is recorded
rather than chased. The scan is over ``src`` alone: a test naming the type is
exercising a builder, not deciding where the type should live.

Pure: it parses the package's own modules and opens no database, no socket and
no temporary directory.
"""

from __future__ import annotations

import ast
from typing import Final

import pytest
from write_lock_claims import REPO_ROOT

pytestmark = pytest.mark.unit

SRC: Final = REPO_ROOT / "packages/theurian-core/src"

#: The name under scan, spelled once so the key and the messages cannot drift.
_WRITE_SECTION: Final = "WriteSection"

#: Where the alias is declared. It is *not* in the importer population below --
#: naming a type you declare is not importing it -- and it is asserted separately,
#: because a population that had lost its home would otherwise read as "nobody
#: imports it", which is the cleanest-looking way for this pin to go blind.
_HOME: Final = "theurian/application/findings_builder.py"

#: Every module that imports the alias. The population and the key it was taken
#: under, measured 2026-09-11 at the commit that adds this pin:
#:
#:     $ git grep -c "WriteSection" -- packages/theurian-core/src
#:     packages/theurian-core/src/theurian/application/findings_builder.py:4
#:     packages/theurian-core/src/theurian/application/review_search_builder.py:2
#:     packages/theurian-core/src/theurian/cli/findings_commands.py:2
#:     packages/theurian-core/src/theurian/cli/review_commands.py:2
#:
#: Four files, of which the first is the declaring module -- so the importers are
#: the three below. **The population is the key, not a count of matching lines**:
#: ``findings_commands.py`` reaches the name through a *grouped* import, so its
#: import statement does not carry ``from theurian.application.findings_builder
#: import`` on one line at all, and a text search for that phrase answers two
#: where the answer is three. That is the miscount the declaring module's own note
#: had to write a clause around, and it is why this is derived from a syntax tree.
#:
#: Two composition roots, which build a real section out of the project's write
#: lock, and one other builder, which takes it for the identical purpose --
#: **the second builder**, the one the home decision says does not yet pay for a
#: move. A third is the trigger.
IMPORTERS: Final[frozenset[str]] = frozenset(
    {
        "theurian/application/review_search_builder.py",
        "theurian/cli/findings_commands.py",
        "theurian/cli/review_commands.py",
    }
)


def _modules() -> dict[str, ast.Module]:
    """Every shipped module, keyed by its path under ``src``."""
    return {
        path.relative_to(SRC).as_posix(): ast.parse(
            path.read_text(encoding="utf-8"), filename=path.name
        )
        for path in sorted(SRC.rglob("*.py"))
    }


def _reaches_the_alias(tree: ast.Module) -> bool:
    """Whether *tree* imports ``WriteSection`` or reaches it through a module object."""
    return any(
        (
            isinstance(node, ast.ImportFrom)
            and any(alias.name == _WRITE_SECTION for alias in node.names)
        )
        or (isinstance(node, ast.Attribute) and node.attr == _WRITE_SECTION)
        for node in ast.walk(tree)
    )


def test_exactly_three_shipped_modules_import_the_write_section_alias() -> None:
    """RED means a fourth importer landed, and a deferred decision came due.

    The alias lives in a module whose subject is findings while being shared by
    two builders, and ``findings_builder.py`` records why it was left there: the
    move costs two packages and two composition roots to buy a better filename,
    and **a third builder is where it should be made**. A fourth importer is that
    third builder arriving, or it is somebody reaching across a layer for a type
    they should have been handed -- and either way it is a decision somebody has
    to take rather than one that should happen by import.

    So this fails on the population moving in *either* direction. An importer
    gained: take the home decision, then add it here. An importer lost: the note
    over-counts, and either the move already happened or a builder stopped taking
    its section by injection -- which would be a real change in how that builder
    is testable, since ``nullcontext`` is what lets one run without a lock file.

    The premise comes first, twice over. The walk has to have found the declaring
    module, or an empty population would read as a clean one; and that module is
    asserted *outside* the importer set, because it names the alias on four lines
    and a key that counted a declaration as an import would report four importers
    where there are three.
    """
    modules = _modules()

    assert _HOME in modules, (
        f"the `src` walk no longer finds `{_HOME}`, where `{_WRITE_SECTION}` is "
        f"declared, so it is not reading the population it claims to read: "
        f"{len(modules)} modules found"
    )
    importers = frozenset(
        path for path, tree in modules.items() if path != _HOME and _reaches_the_alias(tree)
    )

    assert importers == IMPORTERS, (
        f"the `{_WRITE_SECTION}` importer population has moved.\n\n"
        f"  gained: {sorted(importers - IMPORTERS)}\n"
        f"  lost:   {sorted(IMPORTERS - importers)}\n\n"
        f"`{_HOME}` records that the alias stays in a findings-shaped module while "
        f"two builders share it, and that a third builder is where the move to a "
        f"neutral home should be made. Whichever way this moved, that note is now "
        f"describing a tree that is not here: update it, take the home decision it "
        f"defers, and bring the new population here in the same commit."
    )


def test_the_alias_is_declared_where_the_importers_reach_for_it() -> None:
    """The premise under the population: the home is a real module-level binding.

    The arm above excludes the declaring module by path, which is only honest
    while that module really is where the name comes from. If ``WriteSection``
    were moved and re-exported from its old home, the exclusion would quietly hide
    the new declaring module from the population *and* the old one would still
    look like the home -- a rename that this pin, read casually, would appear to
    have survived.

    So the binding is read out of the syntax tree, and the public export beside
    it: the three importers reach the name across a package boundary, so it being
    in ``__all__`` is part of what makes their import the supported form rather
    than a reach into a private name.
    """
    home = ast.parse((SRC / _HOME).read_text(encoding="utf-8"), filename="findings_builder.py")
    bound = [
        node
        for node in home.body
        if isinstance(node, ast.Assign)
        if any(
            isinstance(target, ast.Name) and target.id == _WRITE_SECTION for target in node.targets
        )
    ]

    assert len(bound) == 1, (
        f"`{_HOME}` binds `{_WRITE_SECTION}` at module level {len(bound)} times, "
        f"expected 1. Zero means the alias moved and this module is re-exporting it, "
        f"in which case the population above is excluding the wrong file"
    )
    exported = [
        element.value
        for node in ast.walk(home)
        if isinstance(node, ast.Assign)
        if any(isinstance(target, ast.Name) and target.id == "__all__" for target in node.targets)
        if isinstance(node.value, ast.List)
        for element in node.value.elts
        if isinstance(element, ast.Constant) and isinstance(element.value, str)
    ]

    assert _WRITE_SECTION in exported, (
        f"`{_HOME}` no longer exports `{_WRITE_SECTION}` ({sorted(exported)}), so the "
        f"three modules that import it are reaching for a name the module does not "
        f"publish"
    )


def test_the_population_key_reads_both_forms_an_importer_can_use() -> None:
    """The positive control: the key has to see a grouped import and an attribute.

    The population arm asserts a set *equals* three paths, and a key that had
    stopped matching would report an empty set -- which fails, but for a reason
    nobody could read off the message. Worse is a key that matches one form and
    not another: ``cli/findings_commands.py`` reaches the alias through a grouped
    ``from ... import (...)`` with the name on its own line, and
    ``review_search_builder.py`` through a single-name import, so a key written
    against either shape alone would report a population of two and look
    plausible.

    Both real forms are driven here, plus the attribute form no shipped module
    uses today -- kept because it is how a re-export or a module-object import
    would arrive, and a branch nothing reaches would otherwise survive its own
    deletion. The negative row is what makes the rest mean anything: a module that
    merely mentions the word in a string must not join the population.
    """
    forms = {
        "a single-name import": "from theurian.application.findings_builder import WriteSection\n",
        "a grouped import": (
            "from theurian.application.findings_builder import (\n"
            "    FindingsBuilder,\n"
            "    WriteSection,\n"
            ")\n"
        ),
        "a relative re-export": "from .findings_builder import WriteSection\n",
        "a module-object attribute": (
            "from theurian.application import findings_builder\n\n"
            "section: findings_builder.WriteSection = nullcontext\n"
        ),
    }

    for label, source in forms.items():
        assert _reaches_the_alias(ast.parse(source)), (
            f"the population key does not see {label}, so a module importing "
            f"`{_WRITE_SECTION}` that way joins the tree without reddening anything"
        )

    assert not _reaches_the_alias(ast.parse('NOTE = "WriteSection is a factory"\n')), (
        "the population key matches a module that only mentions the name in prose, so "
        "it would report importers that are not importers"
    )
