"""What the changelog's review-ingest entry claims, held to the tree (ADR-0030, #479).

``packages/theurian-core/CHANGELOG.md`` is where an operator learns what
``theurian review ingest`` does before they run it, and four of its sentences
say something the tree can be asked about:

- **Exit 1 carries the run document, or ``{error, remedy}``, or both** — the run
  document alone on stdout for a run that happened and was not clean,
  ``{error, remedy}`` alone on stderr for a refusal that fired before any report
  existed, and both when the records landed and the rebuild after them did not.
  The refusal shape has no ``clean`` field, so a caller scripting ``--json | jq
  .clean`` reads a refusal as *absent* rather than as *refused*; and a caller
  reading only stdout can now see a document at exit 1. This row has caught the
  entry understating that population **twice**. First it read *"1 when any record
  was withheld or any pull request could not be read"* — one document where there
  were two — and the command's own ``--help`` was understating it the same way.
  Then it read *"1 on either of two documents"*, which stopped being true when
  the post-landing rebuild moved into a ``try`` of its own and its three arms
  began publishing the run document before failing. **The quoted before-texts are
  the citation**: a branch commit's sha is orphaned by the squash that merges it,
  and a quotation survives.
- **A pull-request number that cannot be read is repository scope.** It sits in
  the halting enumeration, and the entry states the reason: ``--since`` is
  applied to a number, so a pull request whose number cannot be read is one no
  window can place. Moving it into the skip channel would make a run continue
  past a fault that denies the window's own key.
- **A ``warn`` run exits 0, and ``secretsWarned`` is the field that says a
  secret landed anyway.** ``clean`` reads ``true`` and ``refused`` is empty, both
  honest and both silent about the credential just written into
  ``.theurian/review/``.
- **The run document's emit is owed by a ``finally``, not by an enumeration of
  ``except`` arms.** That clause is what makes the "both" of the first claim a
  guarantee rather than a list of the failures somebody has already met: three
  enumerated arms each published the document before failing, and a ``ValueError``
  out of ``int`` landed outside all three and took the document with it. The drift
  this row guards is a reword *back* to the enumeration, which reads as the more
  precise sentence and carries the identical hole.

**Each claim is pinned from both sides, and the two sides fail differently on
purpose.** The prose pins hold *spelling*: they are blind to whether the
sentence is true, and every one of them would match word for word against a
build that had stopped behaving that way. The fact pins hold the *mechanism*:
they are blind to what any document says. That split is the one
``test_config_key_call_sites.py`` and ``test_adr_0030_claims.py`` record at
length, and it is why the rows here are not folded into one test.

The fact side of the third claim lives in this module, because the published
key set is reachable without a subprocess: :data:`_WARNED_FIELD` is the single
name both halves are keyed on, so renaming the field in
``cli/review_commands.py`` reddens the fact half and rewording the entry reddens
the prose half. The fact side of the second lives where the mechanism does —
``tests/integration/test_gh_review_provider.py::``
``test_an_unreadable_pull_request_number_denies_the_window_rather_than_being_skipped``
— because only a real adapter driving a real spawn can be said to have denied a
window. The first claim's behaviour half is the command's own exit-code tests in
``tests/integration/test_review_ingest_cli.py``, and the fourth's is one case in
that same file —
``test_a_rebuild_defect_outside_every_graded_arm_still_publishes_the_run_document``,
which raises the exception class no arm names and asserts the document is out
anyway. Each failure message below names the half that did not move.

**Both directions carry a positive control**, because a pin whose expected
answer is "the fragment is still there" and a pin that has stopped looking read
identically from the outside.
:func:`test_a_drifted_entry_is_reported_by_the_same_checker` mutates each
fragment into the drift it guards against and asserts the checker reports it;
:func:`test_the_field_pin_reddens_when_the_run_document_stops_publishing_it`
strips the key out of the live payload and asserts the fact pin reports that.
Both plant into a copy and neither is committed.

**Not scoped to ``[Unreleased]``**, for the reason
``test_census_record_claims.py`` records: a release cut moves these entries into
a dated section without changing a word, and a RED there would be noise at the
worst possible moment. What a release changes is their *status*, from a live
claim to a record, at which point a row may be retired with that reason on the
line.

Pure in the sense the other structural pins here are: it reads two repository
files as text and calls one pure function, and opens no database, no socket and
no temporary directory.
"""

from __future__ import annotations

import pathlib
import re
from typing import Final

import pytest

from theurian.application.review_ingest_service import ReviewIngestReport
from theurian.cli import review_commands

pytestmark = pytest.mark.unit

#: ``parents[4]`` is ``.../tests/unit/`` -> ``tests`` -> ``theurian-core`` ->
#: ``packages`` -> repo root, the reckoning ``test_config_key_call_sites.py``
#: and ``test_census_record_claims.py`` both use.
REPO_ROOT: Final = pathlib.Path(__file__).resolve().parents[4]

CORE_CHANGELOG: Final = REPO_ROOT / "packages" / "theurian-core" / "CHANGELOG.md"

#: The field the ``warn`` paragraph names, spelled **once** for both halves.
#:
#: The prose row below is built from this constant rather than transcribing the
#: sentence whole, so the entry and the published document cannot disagree about
#: the spelling without one of the two pins going RED. Renaming the key in
#: ``cli/review_commands.py`` reddens
#: :func:`test_the_run_document_publishes_the_field_this_entry_names`; rewording
#: the entry past it reddens the prose row.
_WARNED_FIELD: Final = "secretsWarned"


def _collapsed(text: str) -> str:
    """Runs of whitespace flattened to single spaces, case preserved.

    The changelog is line-wrapped Markdown, so every sentence below breaks
    across two or three source lines. A raw substring match would miss the real
    wording and pass vacuously against a document that had said the opposite.
    """
    return " ".join(text.split())


def _assert_the_entry_states(label: str, document: str, fragment: str, fact_side: str) -> None:
    """The prose check, in one place so its positive control drives the same path.

    Written as a helper rather than inline so that
    :func:`test_a_drifted_entry_is_reported_by_the_same_checker` exercises the
    identical comparison and the identical message, instead of a re-derived
    approximation of it that could pass while the real one had stopped looking.
    """
    assert fragment in document, (
        f"{label}: packages/theurian-core/CHANGELOG.md no longer states:\n\n  {fragment}\n\n"
        f"This is the RECORD half of the claim, and it is blind to behaviour: it "
        f"matches spelling and nothing else. So a RED here says the entry drifted, "
        f"not that the product did.\n\n"
        f"Settle which side moved before editing anything. The behaviour half is "
        f"{fact_side}. If that is GREEN, nothing in the tree moved and the entry is "
        f"what gets restored. If it is RED too, the product changed and the entry is "
        f"corrected in that same commit -- with the new wording brought here, never "
        f"by relaxing this row."
    )


#: Each claim the entry makes, the fragment that carries it, the drift the row
#: guards against, and where its behaviour half lives.
#:
#: **Whole clauses, not keywords**, which is the treatment
#: ``test_census_record_claims.py`` gives a changelog correction and for the same
#: reason: the failure these guard is a *reword* -- a sentence that keeps the
#: object spelled and loses what it says about it. A keyword match would pass a
#: rewrite that kept the keyword, which is the exact escape that lets a record go
#: quietly false.
#:
#: The ``drift`` column is not decoration. It is the specific reversion each row
#: exists to catch, and it is what the positive control plants into a copy of the
#: document -- so a row that has stopped discriminating fails there rather than
#: reporting a safety it does not have.
ENTRY_CLAIMS: Final[tuple[tuple[str, str, str, str], ...]] = (
    (
        "exit 1 names all three shapes",
        (
            "**1 on the run document, or `{error, remedy}`, or both** — the run document "
            "alone, on stdout, when the run happened and was not clean (a record `block` "
            "withheld, or a pull request the listing or a fetch could not read); "
            "`{error, remedy}` alone, on stderr, when the command refused before any "
            "report existed; and **both** when the records landed and the rebuild that "
            "follows them did not"
        ),
        # The understatement this row has now caught twice, in its second and
        # narrower form. The first was exit 1 described as the non-clean run
        # alone; this is the two-document wording, which was true until the
        # rebuild moved into a `try` of its own and its three arms began
        # publishing the run document before failing. A caller reading it would
        # not expect a document on stdout beside a refusal on stderr.
        (
            "**1 on either of two documents**, the run document when the run happened "
            "and was not clean (a record `block` withheld, or a pull request the listing "
            "or a fetch could not read) and `{error, remedy}` when the command refused "
            "before any report existed, which carries no `clean` field at all"
        ),
        (
            "`tests/integration/test_review_ingest_cli.py::"
            "test_the_help_says_which_documents_exit_one_carries`, "
            "`::test_a_rebuild_that_fails_after_landing_still_publishes_the_run_document` "
            "and `::test_an_unloadable_migration_is_reported_as_a_document`"
        ),
    ),
    (
        "the number is inside the halting enumeration",
        "a pull-request number it cannot read",
        # A reword that keeps the enumeration's shape and loses the one member
        # whose scope this entry argues about.
        "a pull-request title it cannot read",
        (
            "`tests/integration/test_gh_review_provider.py::"
            "test_an_unreadable_pull_request_number_denies_the_window_rather_than_being_skipped`"
        ),
    ),
    (
        "the entry states why the number is repository scope",
        (
            "That ordering is also why the **number** is on the repository side above and "
            "not here: a pull request whose number cannot be read is one no window can "
            "place."
        ),
        # The reversion itself: the number reassigned to the record-scope side,
        # which is the wording that would have to accompany moving the mechanism
        # into the skip channel.
        (
            "That ordering is also why the **number** is on the record side here rather "
            "than above: a pull request whose number cannot be read is skipped like any "
            "other."
        ),
        (
            "`tests/integration/test_gh_review_provider.py::"
            "test_an_unreadable_pull_request_number_denies_the_window_rather_than_being_skipped`"
        ),
    ),
    (
        "the warn paragraph names the published field",
        (
            f"**A `warn` run exits 0, and `{_WARNED_FIELD}` is the published field that "
            f"says a secret landed anyway.**"
        ),
        # The state the field exists to remove: the paragraph telling an operator
        # to derive the answer by joining two other values, which is the rule the
        # field was published to stop each reader re-deriving.
        (
            "**A `warn` run exits 0, and a caller joins `secretScanPolicy` against the "
            "finding count to see that a secret landed anyway.**"
        ),
        "`test_the_run_document_publishes_the_field_this_entry_names` in this module",
    ),
    (
        "the emit is owed by a `finally` and not by an enumeration",
        (
            "because that rebuild sits in a `try` of its own whose `finally` publishes "
            "the run document before any failure leaves the command"
        ),
        # The wording this replaced, and the hole it described. Three enumerated
        # `except` arms each published the document before failing, which is true
        # of every exception class somebody had already met and false of the one
        # nobody had: a `ValueError` out of `int` landed outside all three and took
        # the run document with it (#630's HIGH-1). A reword back to an
        # enumeration is the same promise with the same hole, and it reads as the
        # *more* precise sentence -- it names a number and three named arms --
        # which is why the drift column is written as the one somebody would
        # plausibly restore rather than as an obvious weakening.
        (
            "because that rebuild sits in a `try` of its own whose three `except` arms "
            "each publish the run document before they fail"
        ),
        (
            "`tests/integration/test_review_ingest_cli.py::"
            "test_a_rebuild_defect_outside_every_graded_arm_still_publishes_the_run_document`"
        ),
    ),
)


@pytest.mark.parametrize(
    ("label", "fragment", "drift", "fact_side"),
    ENTRY_CLAIMS,
    ids=[case[0] for case in ENTRY_CLAIMS],
)
def test_the_changelog_entry_still_states_the_claim(
    label: str, fragment: str, drift: str, fact_side: str
) -> None:
    """The record half: a reader has to keep being told what the tree does.

    A behaviour test holds the mechanism and is blind to what any document says
    about it. The failure that costs something is the other direction -- the tree
    stays correct, the sentence is softened or dropped in a rewrite, and the next
    reader has no reason to believe the property holds at all. The change after
    that removes the property, and nothing objects.

    Every fragment held here entered the changelog on **this branch**, and two of
    the three replaced a sentence that had been wrong rather than merely absent:
    the exit-code population named one document where there are two, and the
    containment paragraph called the split *"by call site"* and put record scope
    at the per-pull-request fetch alone, which left the listing seam — and with
    it the number — described nowhere. Newly corrected wording is the wording
    most easily lost in the next rewrite, because nobody rereads what was just
    written.

    **Each correction is cited by the text it replaced rather than by a commit**
    (round two). "The commit before this one" is an ambiguous referent as soon as
    a rebase reorders anything, and a branch sha is orphaned outright by the
    squash that merges it -- a fresh clone answers ``fatal: bad object`` over a
    citation that reads like a working one. A quotation survives both.

    ``drift`` is unused by this test and is the subject of its positive control
    below; it is carried in the same row so that the claim, the reversion it
    guards, and the behaviour half that settles a disagreement are read in one
    place.
    """
    entry = _collapsed(CORE_CHANGELOG.read_text(encoding="utf-8"))

    _assert_the_entry_states(label, entry, fragment, fact_side)


@pytest.mark.parametrize(
    ("label", "fragment", "drift", "fact_side"),
    ENTRY_CLAIMS,
    ids=[case[0] for case in ENTRY_CLAIMS],
)
def test_a_drifted_entry_is_reported_by_the_same_checker(
    label: str, fragment: str, drift: str, fact_side: str
) -> None:
    """The positive control for the prose direction: the row above can go RED.

    A pin whose expected answer is "the fragment is still there" fails silently
    in a way nothing else catches. Widen the fragment until it matches something
    every draft contains, or normalise the document until the comparison is
    trivially true, and the row keeps passing forever -- and a green that cannot
    go red looks exactly like a document nobody has touched.

    So each row's own reversion is planted into a copy of the document and the
    same checker is asked about it. The plant is in memory and the repository
    file is never written: a pin that had to edit the tree to prove it works
    would be a worse instrument than no pin.

    Two guards before the plant, because a substitution that does not land
    reports its own no-op as a pass: the drift must not itself contain the
    fragment, and the replacement must actually change the document.
    """
    entry = _collapsed(CORE_CHANGELOG.read_text(encoding="utf-8"))

    drifted = entry.replace(fragment, drift)

    assert fragment not in drift, (
        f"{label}: the drift contains the fragment it is supposed to displace, so this "
        f"control would assert nothing. Rewrite the `drift` column as the sentence the "
        f"reversion would leave behind."
    )
    assert drifted != entry, (
        f"{label}: planting the drift changed nothing, so this control passed without "
        f"exercising the checker. Either the fragment is absent -- in which case "
        f"`test_the_changelog_entry_still_states_the_claim` is the RED that matters -- "
        f"or the drift is byte-identical to it."
    )
    with pytest.raises(AssertionError, match=re.escape(label)):
        _assert_the_entry_states(label, drifted, fragment, fact_side)


#: One run of ``theurian review ingest`` as the service reports it, in the state
#: the entry's ``warn`` paragraph is about: a finding was reported, the record
#: landed, nothing was refused, and the run is clean.
#:
#: A real :class:`ReviewIngestReport` rather than a stand-in, because the pin
#: below is about what the published document actually carries. A mock would
#: confirm that :func:`_payload` was called with what the test handed it.
_A_WARNED_RUN: Final = ReviewIngestReport(
    repository="acme/order-service",
    policy="warn",
    redacted=False,
    secrets_warned=True,
    pull_requests=1,
    review_submissions=0,
    review_threads=0,
    new=1,
    updated=0,
    kept=0,
    refused=(),
    findings=(),
    skipped=(),
)


def _published_run_document_keys() -> tuple[str, ...]:
    """The keys ``theurian review ingest --json`` publishes, from the live builder.

    Derived by calling the shipped construction rather than transcribing a list,
    which is the property a transcription cannot honour: a transcribed set stays
    green while the document underneath it renames or drops the field, and the
    entry it is supposed to hold would then be describing a document that no
    longer exists.

    Looked up on the module at call time, so
    :func:`test_the_field_pin_reddens_when_the_run_document_stops_publishing_it`
    can substitute a payload builder and drive this same path.
    """
    return tuple(review_commands._payload(_A_WARNED_RUN))


def _assert_the_warned_field_is_among(keys: tuple[str, ...]) -> None:
    """The fact check, in one place so its positive control drives the same path."""
    assert _WARNED_FIELD in keys, (
        f"`theurian review ingest --json` no longer publishes `{_WARNED_FIELD}`; it "
        f"publishes {sorted(keys)}.\n\n"
        f"This is the BEHAVIOUR half. Two documents name that field as the thing that "
        f"says a secret landed under `warn` -- packages/theurian-core/CHANGELOG.md's "
        f"review-ingest entry, held by "
        f"`test_the_changelog_entry_still_states_the_claim[the warn paragraph names the "
        f"published field]` in this module, and "
        f"`security.secretScan`'s published description, held by "
        f"`tests/unit/test_config_key_call_sites.py`. Both are false the moment this is "
        f"RED, and both are corrected in the same commit as the rename.\n\n"
        f"Do not relax this row to make the rename land: `clean` reads true and "
        f"`refused` is empty on a warned run, so with this field gone nothing published "
        f"says a credential was written into `.theurian/review/` -- a directory "
        f"`theurian init` does not add to the managed `.gitignore` block."
    )


def test_the_run_document_publishes_the_field_this_entry_names() -> None:
    """SEC-11: the one state ``clean`` and ``refused`` are both honest about and both silent on.

    Under ``warn`` the project has recorded that a finding is reported and the
    record lands anyway, so the run is clean, refuses nothing and exits 0 --
    having just written a credential into a directory Theurian does not
    git-ignore. ``secretsWarned`` is the third boolean that says so, and the
    changelog entry and the ``security.secretScan`` schema description both send
    a reader to it by name.

    The key set is read from the shipped builder, so a rename lands here rather
    than in a reviewer's memory.
    """
    keys = _published_run_document_keys()

    _assert_the_warned_field_is_among(keys)


def test_the_field_pin_reddens_when_the_run_document_stops_publishing_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The positive control for the fact direction: the row above can go RED.

    The pin asserts a key is present in a set the product builds, and a check of
    that shape has two silent failure modes: the derivation could stop returning
    the real document, or the assertion could be widened to something every
    mapping satisfies. Either way it passes forever and reports a safety that is
    not there.

    So the field is stripped out of the **live** payload -- the real builder,
    called and then filtered -- and the pin's own assertion is asked about the
    result. The substitution is a ``monkeypatch`` on the module attribute and is
    undone when the test ends; nothing in ``src/`` is edited.

    The surviving keys are asserted too, because a stand-in that returned an
    empty mapping would redden the pin for the wrong reason and prove nothing
    about the one field.
    """
    live = review_commands._payload

    def _without_the_warned_field(report: ReviewIngestReport) -> dict[str, object]:
        return {key: value for key, value in live(report).items() if key != _WARNED_FIELD}

    monkeypatch.setattr(review_commands, "_payload", _without_the_warned_field)

    keys = _published_run_document_keys()

    assert "clean" in keys and "secretScanPolicy" in keys, (
        f"the planted payload lost more than `{_WARNED_FIELD}` ({sorted(keys)}), so a RED "
        f"below would not be evidence that the pin watches that one field."
    )
    with pytest.raises(AssertionError, match=_WARNED_FIELD):
        _assert_the_warned_field_is_among(keys)
