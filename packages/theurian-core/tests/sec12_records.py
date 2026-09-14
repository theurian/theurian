"""Reading the three records that carry #669's figures, for the pins that hold them.

``threat_model_claims`` slices one threat-model entry, which is the part every
entry's pin shares. #669's correction landed in three records of three different
shapes -- a threat-model entry's closing paragraph, an ADR amendment, and a set
of CHANGELOG bullets -- and two test modules now read all three: one holding the
figures against the live constants, one holding the retired claims against being
silently reasserted.

**The slicing lives here for the reason that module gives for its own.** Each
of these records has a locator that is easy to get subtly wrong, and every
mistake is silent in the same direction -- a slice that runs past its record
reads a neighbour's numbers as its own, and a slice that finds nothing reports a
clean record whatever it says. One implementation, called by both modules, is
one place for that to be right.

* **T-11's residual paragraph** runs from its ``**Residual risk:**`` marker to
  the next blank line. Sliced rather than scanned over the whole entry because
  T-11 names ``MAX_PARAMS_RENDERED_CHARS`` in a second place -- the
  bounded-refusal paragraph, which lists it beside ``MAX_PARAMS_NESTING`` and
  ``MAX_PARAMS_NODES`` -- and a rule keyed on that subject would pair the wrong
  sentence.
* **ADR-0031's Amendment 1** is a ``##`` section, anchored to a line start and
  to that level because the file refers to "Amendment 1" three times in running
  text.
* **The CHANGELOG's #669 entries** are bullets inside ``[Unreleased]`` rather
  than a section of their own, and that section carries #665's and #693's
  entries too -- so the bullets are selected by the issue they name, and a pin
  counting figures across the whole section would pair a neighbour's number
  with this issue's claim.

Every locator asserts what it found before returning it. An empty read is the
failure mode an absence arm cannot distinguish from safety.

Pure: two files read as text, no database, socket or temporary directory.
"""

from __future__ import annotations

import re
from typing import Final

from threat_model_claims import entry
from write_lock_claims import REPO_ROOT

#: The threat-model entry SEC-12's shipped-control statement and its residual
#: paragraph live in.
THREAT_ID: Final = "T-11"

#: Where T-11's residual risk starts.
RESIDUAL_MARKER: Final = "**Residual risk:**"

ADR: Final = REPO_ROOT / "docs/adr/0031-mcp-input-is-schema-validated-in-middleware.md"
CHANGELOG: Final = REPO_ROOT / "packages/theurian-core/CHANGELOG.md"

#: The amendment's heading, anchored to a line start and to the ``## `` level so
#: the three in-text references to "Amendment 1" cannot open the slice.
_AMENDMENT_HEAD: Final = "\n## Amendment 1 "

#: Where the unreleased section starts. Its bullets are split on a line-start
#: ``- ``, and the ones naming the issue are the record.
_UNRELEASED_HEAD: Final = "\n## [Unreleased]"


def t11_residual_paragraph() -> str:
    """T-11's *Residual risk* paragraph, raw.

    Raw rather than normalised, for :func:`~threat_model_claims.entry`'s reason:
    the slice is taken on a blank line, and normalising destroys the line
    structure it is taken on. Callers normalise afterwards.
    """
    text = entry(THREAT_ID)

    assert text.count(RESIDUAL_MARKER) == 1, (
        f"{THREAT_ID} carries {text.count(RESIDUAL_MARKER)} `{RESIDUAL_MARKER}` markers, "
        f"expected 1. With none of them every arm reading this paragraph reads an empty "
        f"string and reports it as safety; with two, whichever came first"
    )
    return text[text.index(RESIDUAL_MARKER) :].split("\n\n", 1)[0]


def amendment_one() -> str:
    """ADR-0031's Amendment 1, raw, from its heading to the next ``##``."""
    text = ADR.read_text(encoding="utf-8")

    assert text.count(_AMENDMENT_HEAD) == 1, (
        f"ADR-0031 carries {text.count(_AMENDMENT_HEAD)} lines starting "
        f"`{_AMENDMENT_HEAD.strip()}`, expected 1. With none of them every arm reading this "
        f"record reads an empty string and reports it as safety"
    )
    rest = text.split(_AMENDMENT_HEAD, 1)[1]
    end = rest.find("\n## ")
    return rest[:end] if end >= 0 else rest


def changelog_entries(issue: str) -> str:
    """The unreleased CHANGELOG bullets that name ``issue``, joined."""
    text = CHANGELOG.read_text(encoding="utf-8")

    assert text.count(_UNRELEASED_HEAD) == 1, (
        f"the CHANGELOG carries {text.count(_UNRELEASED_HEAD)} `[Unreleased]` headings, expected 1"
    )
    rest = text.split(_UNRELEASED_HEAD, 1)[1]
    end = rest.find("\n## ")
    unreleased = rest[:end] if end >= 0 else rest
    # Word-boundary, so `#669` does not match `#6691`: the CHANGELOG names
    # issues by number in running prose, and a substring match would fold a
    # neighbouring issue's bullet into this record the day one is filed.
    names_issue = re.compile(rf"{re.escape(issue)}(?![0-9])")
    bullets = [block for block in re.split(r"\n(?=- )", unreleased) if names_issue.search(block)]

    assert bullets, (
        f"the CHANGELOG's [Unreleased] section holds no bullet naming {issue}, so every arm "
        f"reading this record reads an empty string. Either the entries moved into a "
        f"released section -- in which case this locator follows them -- or they were dropped"
    )
    return "\n".join(bullets)
