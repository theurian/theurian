"""An anchor this tool cannot honestly compare is named, never quietly compared anyway.

Every clause in :func:`corpus_drift.anchor_refusal` is a case where comparing
would assert something the recorded data does not support, and each one fails in
a different direction if it is dropped:

- **provider / sourceUri** -- an anchor naming another repository names a
  ``docs/adr/0001-....md`` that is not *this* checkout's. Dropping the clause
  compares a same-named local file and reports drift or agreement about a
  document nobody pinned.
- **a line range** -- ``contentSha256`` digests the whole body, and no
  per-extent digest is recorded anywhere. Dropping the clause means inventing a
  slice-hashing convention the product does not produce, and every line-ranged
  anchor then reports drift the moment any other line of the file changes.
- **filePath** -- an absent, escaping, or corpus-internal path. The last is the
  quiet one: an anchor pointing back at ``.theurian/`` compares a snapshot
  against a copy of itself, which is a check that cannot fail.

The refusals are asserted on their *reason text*, not merely on being non-empty:
the reason is what the ``::notice`` annotation and the step summary print, and a
refusal that reaches a maintainer without saying which clause fired is a skip
they cannot act on. The first test guards the other direction -- a well-formed
anchor must still be comparable, or ``return "no"`` passes this whole file.
"""

from __future__ import annotations

import corpus_drift
import pytest
from corpus_drift import THIS_REPOSITORY, anchor_refusal

pytestmark = pytest.mark.unit


def _anchor(**overrides: object) -> dict[str, object]:
    """A well-formed anchor of the shape all 26 committed ones have, plus overrides."""
    return {
        "provider": "git",
        "sourceUri": THIS_REPOSITORY,
        "commitSha": "2a98d4c8963cdf46cc6169e43ac7add039745342",
        "filePath": "docs/adr/0005-yaml-knowledge-migrations.md",
    } | overrides


def test_a_git_anchor_naming_a_document_in_this_repository_is_comparable() -> None:
    """The shape every committed anchor has must survive all four refusal clauses.

    Without this, a regression that refuses everything would leave the whole
    check silently uncheckable while the tests below all still pass.
    """
    assert anchor_refusal(_anchor()) is None


def test_a_line_range_is_refused_because_no_per_extent_digest_was_ever_recorded() -> None:
    """`lineStart` narrows the anchor; `contentSha256` still digests the whole body.

    The published `sourceAnchor` schema allows the range, so this is reachable
    input rather than a hypothetical. Hashing the slice here would invent a
    convention `ProposalService.draft` does not produce.
    """
    refusal = anchor_refusal(_anchor(lineStart=10, lineEnd=42))

    assert refusal is not None
    assert "line range" in refusal
    assert "contentSha256 digests the whole body" in refusal


def test_a_line_end_without_a_line_start_is_refused_too() -> None:
    """Either bound alone still means the anchor speaks for an extent, not the file."""
    refusal = anchor_refusal(_anchor(lineEnd=42))

    assert refusal is not None
    assert "line range" in refusal


def test_an_anchor_naming_another_repository_is_not_compared_against_a_local_namesake() -> None:
    """`docs/adr/0001-....md` in another repository is not this checkout's file.

    Comparing anyway would report drift, or agreement, about a document this
    corpus never pinned.
    """
    refusal = anchor_refusal(_anchor(sourceUri="https://github.com/someone/else.git"))

    assert refusal is not None
    assert "not in this checkout" in refusal
    assert "https://github.com/someone/else.git" in refusal


def test_an_anchor_from_a_provider_other_than_git_names_no_file_in_this_tree() -> None:
    """A `confluence` or `http` anchor's `filePath` does not address this working copy."""
    refusal = anchor_refusal(_anchor(provider="confluence"))

    assert refusal is not None
    assert "only a 'git' anchor names a file in this tree" in refusal


def test_an_anchor_with_no_file_path_names_nothing_to_compare() -> None:
    """`filePath` is optional in the schema; absent, there is no document at all."""
    anchor = _anchor()
    del anchor["filePath"]

    refusal = anchor_refusal(anchor)

    assert refusal is not None
    assert "no document to compare against" in refusal


def test_an_empty_file_path_is_refused_rather_than_resolving_to_the_repository_root() -> None:
    """`repo_root / ""` is the root directory, which reads as a directory, not a document."""
    refusal = anchor_refusal(_anchor(filePath=""))

    assert refusal is not None
    assert "no document to compare against" in refusal


def test_a_file_path_that_climbs_out_of_the_repository_is_refused() -> None:
    """`..` in the anchor must not be resolved and then read.

    The path arithmetic is deliberately pure -- no filesystem call -- so a
    symlink cannot decide the answer.
    """
    refusal = anchor_refusal(_anchor(filePath="../../etc/passwd"))

    assert refusal is not None
    assert "resolves outside the repository" in refusal


def test_an_absolute_file_path_is_refused_rather_than_discarding_the_repository_root() -> None:
    """`PurePosixPath("") / "/etc/passwd"` is `/etc/passwd`: the left side is discarded.

    Without the absolute-path clause the join succeeds and the file is opened,
    so this is the one refusal whose absence reads an arbitrary path.
    """
    refusal = anchor_refusal(_anchor(filePath="/etc/passwd"))

    assert refusal is not None
    assert "resolves outside the repository" in refusal


def test_an_anchor_pointing_back_into_the_corpus_is_a_comparison_that_cannot_fail() -> None:
    """A snapshot pinned to a copy of itself always matches, and asserts nothing.

    Reported as uncheckable rather than counted as a pass, because a corpus that
    drifted into self-reference would otherwise read as 26 healthy anchors.
    """
    refusal = anchor_refusal(
        _anchor(filePath=".theurian/knowledge/architecture/yaml-knowledge-migrations.md")
    )

    assert refusal is not None
    assert "inside the corpus itself" in refusal


def test_an_anchor_that_is_not_a_mapping_is_refused_rather_than_raising() -> None:
    """`sourceAnchors: [docs/adr/0005.md]` is valid YAML and a list of strings.

    A malformed corpus file must produce a named skip, not an ``AttributeError``
    that takes the whole run down before the other 25 migrations are read.
    """
    refusal = anchor_refusal("docs/adr/0005-yaml-knowledge-migrations.md")

    assert refusal is not None
    assert "not a mapping" in refusal


def test_the_repository_the_anchors_name_is_this_project() -> None:
    """`THIS_REPOSITORY` is what every refusal above is measured against.

    Pinned so that a typo in the constant -- which would make all 26 committed
    anchors uncheckable at once and the run report `NOTHING_COMPARED` -- is a
    named failure rather than a mystery.
    """
    assert corpus_drift.THIS_REPOSITORY == "https://github.com/theurian/theurian.git"
