"""``--allow-uncommitted`` is a flag, and no configuration key selects it (ADR-0034 decision 2).

Decision 2 is explicit that the escape hatch is a flag and **not** a configuration
key: a config default is invisible at the moment of use -- a project that set it
once would apply uncommitted migrations for ever, and nobody reviewing an incident
would see it in the command that ran. The control decision 1 adds is weak enough
(decision 1 says how weak) that a silent, sticky disable would leave nothing, so
the disable has to live in the command line.

This pins the negative half of that: the published config schema names no key that
would select the escape hatch. It reads ``schemas/config/project-config.schema.json``
rather than a transcribed key list -- the shape ``test_config_key_call_sites.py``
uses for config-key claims -- so a key *added* to the schema tomorrow is seen by
this test the day it lands, and reddens here.

**What this cannot see** is the same bound that file records for its own scans: it
matches published key *names*. A key assembled at runtime, or one whose reader
consulted a value under an unrelated name, would pass -- but there is no reader of
this file for the committed-check at all (the check takes its answer from a CLI
flag, not from ``.theurian/config.yaml``), which is what makes the name-level pin
the right floor here.
"""

from __future__ import annotations

import json
import pathlib
from collections.abc import Iterator
from typing import Final

import pytest

pytestmark = pytest.mark.unit

#: ``parents[4]`` is ``.../tests/unit/`` -> ``tests`` -> ``theurian-core`` ->
#: ``packages`` -> repo root, where the published schemas live.
REPO_ROOT: Final = pathlib.Path(__file__).resolve().parents[4]
PROJECT_CONFIG_SCHEMA: Final = REPO_ROOT / "schemas" / "config" / "project-config.schema.json"


def _normalize(name: str) -> str:
    """A key's identity ignoring case and word separators.

    ``allowUncommitted``, ``allow_uncommitted`` and ``allow-uncommitted`` are one
    concept spelled three ways; normalising to ``allowuncommitted`` lets the pin
    catch whichever a future author reaches for.
    """
    return name.lower().replace("_", "").replace("-", "")


#: Every normalised spelling that would represent the committed-check escape hatch
#: as a configuration key -- the flag's own name (``--allow-uncommitted``) and the
#: obvious sticky reframings of the same switch, positive and inverse. A published
#: key normalising to any of these is decision 2 being violated: the disable would
#: have become a config default rather than a flag.
FORBIDDEN: Final[frozenset[str]] = frozenset(
    _normalize(name)
    for name in (
        "allowUncommitted",
        "allowUncommittedMigrations",
        "skipCommittedCheck",
        "committedCheck",
        "requireCommitted",
        "requireCommittedMigrations",
        "enforceMerge",
        "enforceCommitted",
        "uncommitted",
    )
)


def _published_key_names(node: object, prefix: str = "") -> Iterator[str]:
    """Every property name the schema publishes, both as a leaf and as a dotted path.

    Yields the bare leaf (``allowUncommitted``) and the full dotted path
    (``security.allowUncommitted``) so a switch buried under any block is seen
    however it is nested.
    """
    if not isinstance(node, dict):
        return
    properties = node.get("properties")
    if isinstance(properties, dict):
        for key, subschema in properties.items():
            dotted = f"{prefix}{key}"
            yield key
            yield dotted
            yield from _published_key_names(subschema, f"{dotted}.")


def test_no_published_config_key_selects_the_committed_check_escape_hatch() -> None:
    """RED means a config key that would disable the committed check reached the schema.

    Decision 2's whole content is that the disable is a flag, visible in the
    command that ran. A config key that turns the check off (or, inverted, one that
    a project could set to *stop* requiring a commit) defeats that, silently and
    stickily. This reads the shipped schema and asserts no key normalises to the
    escape hatch.

    If this is RED because such a key was genuinely added, that is a design change
    against ADR-0034 decision 2, not a test to relax: the alternatives table
    rejects "a configuration key instead of a flag" for exactly this reason. Re-open
    the decision in the same change, or drop the key.
    """
    schema = json.loads(PROJECT_CONFIG_SCHEMA.read_text(encoding="utf-8"))
    published = {_normalize(name) for name in _published_key_names(schema)}

    assert FORBIDDEN, (
        "the forbidden-spelling set is empty, so this test asserts nothing -- a "
        "vacuous pass. Restore the concept spellings before trusting a green result."
    )
    assert _normalize("--allow-uncommitted") in FORBIDDEN, (
        "the escape hatch's own flag name does not normalise into the forbidden set, "
        "so this pin would not catch the very key decision 2 forbids"
    )

    offenders = sorted(published & FORBIDDEN)

    assert not offenders, (
        f"the published config schema names {offenders}, which select(s) the "
        "committed-check escape hatch. ADR-0034 decision 2 makes it a flag "
        "(`--allow-uncommitted`) and not a configuration key, so the choice to skip "
        "the check stays visible in the command that ran. If this key is intended, "
        "the decision has to be re-opened in the same change; the alternatives table "
        "rejects a configuration key here."
    )
