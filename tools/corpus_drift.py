"""Has a `docs/` document moved out from under the corpus snapshot pinned to it?

This repository dogfoods Theurian: `.theurian/` holds the committed knowledge
items this repository dogfoods -- a floor of 26, measured 2026-08-31 at
6b83be1, the same lower bound :data:`MINIMUM_COMPARED` holds this tree to, and
one a re-seed grows rather than shrinks. Every one of them is a **verbatim,
byte-frozen snapshot** of a document under `docs/`.
Each one records the file it was taken from (`sourceAnchors[].filePath`) and the
commit it was taken at (`sourceAnchors[].commitSha`), plus the digest of the body
itself (`contentSha256`).

`test_dogfood_corpus_governance.py` already holds the *frozen* half of that
claim: the committed body hashes to its pin, and it is byte-identical to the blob
at its own anchor commit. Both of those compare a fixed object against a fixed
object, so both stay true forever -- and neither notices when somebody edits the
ADR. That is deliberate; that module says so in its own docstring and hands the
live comparison here (#263).

The live comparison is the one with the teeth. When `docs/adr/0013-...md` gains a
section and the corpus does not, Theurian's own agents keep retrieving the older
text -- still labelled `status: approved`, `trustLevel: reviewed`, with an anchor
naming a file whose current contents say something else.

**Measured 2026-08-22 at 64e33da, this had already happened**: 24 of 26 anchors
matched, 2 had drifted -- `docs/adr/0005-yaml-knowledge-migrations.md` and
`docs/adr/0013-ai-writes-produce-proposals.md`, 160 lines added across three
merged pull requests since the corpus was seeded at 2a98d4c.

Usage
-----
    uv run python tools/corpus_drift.py             # exit 1 on drift
    uv run python tools/corpus_drift.py --advisory  # exit 0 on drift, warn loudly
    uv run python tools/corpus_drift.py --format github --summary

Exit codes
----------
``0``
    Every comparable anchor still matches, or ``--advisory`` downgraded drift.
``1``
    Drift. Suppressed by ``--advisory``; see that flag's help for the call.
``2``
    **The check did not check enough to mean anything.** No tracked migrations,
    or git could not be asked what is tracked, or every anchor reached was
    uncheckable, or fewer anchors were compared than the floor the tree is held
    to -- 25 of 26 being the case a bare "compared nothing" never catches (see
    ``--minimum-compared``). Each of those four keeps its own diagnosis: the
    floor states its own only when the run had a verdict to overturn, never on
    top of one of the first three. ``--advisory`` does *not* suppress this: a
    checker that quietly stopped checking is a regression, not a pass, and it is
    the one outcome that must never read as green.

Corpus membership (mandatory declaration, per the class closure in f2f5d77)
--------------------------------------------------------------------------
This is a repo-wide walker, so it states which side of the frozen corpus it
walks rather than leaving a reader to find out.

**The committed corpus under `.theurian/migrations/` is IN -- it is the
subject.** Every tracked `*.yaml` directly under that directory is read.

**Read population and compared population are not the same set.** Every
tracked migration is read, and every ``upsertRevision`` in it is read too, but
only each item's *terminal* one is compared: the last ``upsertRevision`` for a
given ``itemId`` in application order (:func:`migration_paths`' own sort,
applied the way :func:`theurian.domain.migration.current_revision_in` applies
it for the state-rebuild path). A superseded ``upsertRevision`` is read, and
then dropped before it reaches an anchor -- it is not compared and it is not
reported, uncheckable included.

**One further file is read, conditionally: a pinned body.** For a revision that
declares no `contentSha256`, the body its own `contentFile` names is hashed
instead, so that deleting the pin line does not quietly turn that revision's
check off (:func:`_expected_digest`). `contentFile` is joined onto the
migration's own directory and the result is required to start with `.theurian/`,
so this reads exactly one file per such revision.

**That constraint binds the unresolved path; the read that follows it does
not.** :func:`_inside` is path arithmetic and deliberately makes no filesystem
call, so it settles where the *written* path lands and says nothing about where
the final component points: a committed symlink under `.theurian/` clears the
prefix check and is then followed by ``read_bytes()``, putting an out-of-tree
file's digest prefix into the report (measured 2026-08-22). The docs-side read
in :func:`_compare_one` is the same shape. Planting either takes commit access
to this repository and what surfaces is 12 hex characters, so the hardening --
resolve-and-compare, as `security/paths.py` already does in the product -- is
filed as #318 rather than taken here.

**Since ADR-0027 the pin is schema-required, so the code and not the corpus is
what makes every anchor pinned**, and the conditional read is exercised only by
the suite. It is still declared, and still implemented, because *this tool does
not validate*: it reads the tracked YAML directly, so a hand-edited migration
with its pin line deleted still reaches :func:`_expected_digest` here, ahead of
the `theurian migrate validate` run that refuses it. Falling back to the body's
own bytes keeps such a revision compared rather than silently unchecked, which
is what the fall-back was always for.

**Beyond those migrations and the conditional pinned body, nothing under
`.theurian/` is read here -- `evidence.json` included.** Those belong to the
governance test.

**On the `docs/` side there is no walk at all.** This tool opens exactly the
files the anchors name, one per anchor, and never enumerates `docs/`. A document
under `docs/` that no anchor names is not in the population and is not a finding
here.

**Tracked only, via `git ls-files --cached`, and never a filesystem glob.** This
is the same population key #262 adopted for the documented-command scan, and it
is load-bearing rather than tidy: measured 2026-08-22 on the maintainer's
dogfooding machine, `.theurian/migrations/` held **82** `*.yaml` files of which
**26** were tracked. The other 56 are local-only vault notes, deliberately fenced
in `.git/info/exclude`. A glob-based population would read all 82 -- so this tool
would be quiet in CI and noisy on the one machine that has the corpus, which is
#262's failure mode exactly.

**Git failing to answer is a refusal, not a fallback.** There is no
filesystem-walk last resort, because the only tree where the distinction matters
is the tree where the fallback would be wrong.

What this verifies, and what it does not
----------------------------------------
The comparison is **per anchor**, and the digest is recomputed with
:meth:`theurian.domain.values.ContentHash.of_bytes` -- the same call that
*produced* the recorded value (`ProposalService.draft`, and the same call the
migration loader re-derives it with). Not a byte-for-byte `filecmp`, and not
reimplemented here: a second implementation of the hash is a second thing that
can disagree with the product.

An anchor this tool cannot honestly compare is reported as **uncheckable** and
named, never silently skipped. Three shapes reach that, and **none of them
appears in the corpus as it stands** -- every committed anchor is compared
(measured 2026-08-22 at 64e33da: 26 revisions, 26 anchors, one anchor each, none
line-ranged, all 26 pinned, all 26 naming
``https://github.com/theurian/theurian.git`` and a path under ``docs/``; the run
reported 26 compared, 0 uncheckable). They are enumerated because each is a way
a future re-seed could take an item out of the compared set:

- an anchor naming another repository, another provider, or no file at all;
- **a line range.** `sourceAnchor` accepts `lineStart`/`lineEnd` (the published
  schema allows both), and `contentSha256` digests the *whole body*. No
  per-extent digest is recorded anywhere, so there is nothing a line range could
  be held to. Hashing the slice here would invent a convention the product does
  not produce;
- **more than one comparable anchor on one revision.** One recorded digest
  cannot speak for two source files, so neither of them is compared.

**Going uncheckable is not free.** A run that compares fewer anchors than the
floor (``--minimum-compared``, 26 for this tree) is exit 2 whatever else it
found. Without a floor, "compared nothing" fires only at zero, so the corpus can
go uncheckable one item at a time and every run in between reads *clean, exit
0*, reporting each loss as a single ``::notice`` on a job that is advisory by
design. The realistic way there is the first shape above: ``sourceUri`` is
matched exactly, so one re-seed under ``git@github.com:theurian/theurian.git``,
or under the same URL without the ``.git`` suffix, retires that item from the
check permanently.

**The compared population is each item's terminal revision, not every revision
ever recorded -- so a re-seed clears the finding it answers.** :func:`scan`
walks tracked migrations in application order and keeps, per ``itemId``, only
the *last* ``upsertRevision`` it sees; every earlier one for that item is
dropped before it ever reaches :func:`_compare`, so it is neither compared nor
reported (#317; reproduced on PR #440's branch before this fix landed: 27
anchors compared, 12 drifted, ADR-0013's warning still naming the superseded
migration and surviving a re-seed that should have cleared it). A superseded
revision's own pin is unaffected by this: `test_dogfood_corpus_governance.py`
holds every revision this tool ever reads to its frozen half -- the committed
body matches its ``contentSha256``, and matches the blob at its anchor commit
-- current or superseded alike. What changed here is narrower: this tool stops
holding a superseded snapshot to a document that has since moved on, which was
never a finding about drift, only a revision nobody can act on any more.

Not verified here at all: whether the corpus body still matches its own pin, and
whether it matches the blob at its anchor commit. Those are
`test_dogfood_corpus_governance.py`'s, they are hard failures there, and nothing
in this file weakens them.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from pathlib import Path, PurePosixPath
from typing import Any, Final

import yaml

from theurian.domain.values import ContentHash

#: ``parents[0]`` is ``tools/``, so ``parents[1]`` is the repository root.
REPO_ROOT: Final = Path(__file__).resolve().parents[1]

CORPUS_PREFIX: Final = ".theurian/"
MIGRATIONS_PREFIX: Final = ".theurian/migrations/"

#: The remote every committed anchor names (measured 2026-08-22: 26 of 26).
#: An anchor naming anything else describes a file this checkout does not hold,
#: so it is reported as uncheckable rather than compared against a same-named
#: local path that would mean something different.
THIS_REPOSITORY: Final = "https://github.com/theurian/theurian.git"

#: How many anchors a run against *this* tree has to compare before its verdict
#: means anything. 26 is what the corpus ships (measured 2026-08-22 at 64e33da:
#: 26 compared, 0 uncheckable), and it is the same number and the same shape as
#: ``MINIMUM_MIGRATIONS`` in
#: ``packages/theurian-core/tests/unit/test_dogfood_corpus_governance.py``: a
#: **lower bound, not an equality**, because the corpus is expected to grow and
#: the direction that is never routine is committed knowledge disappearing.
#:
#: What it buys that ``NOTHING_COMPARED`` alone does not: that status fires only
#: when the compared count reaches *zero*, so 25 of 26 anchors going uncheckable
#: -- one re-seed at a time, each reported as a single notice on an advisory job
#: -- reads clean and exits 0 the whole way down.
#:
#: Inherited, and stated rather than rounded up: like its counterpart, this is a
#: floor on a count and not on a ratio, so a corpus that grows to 40 while 14 go
#: uncheckable still clears it.
MINIMUM_COMPARED: Final = 26

_SHA256: Final = re.compile(r"\A[0-9a-f]{64}\Z")

#: What a maintainer does about drift. One string so the message is written once
#: and a test can hold it, and phrased as *re-seed*, never *edit the body*: the
#: committed body is pinned by `contentSha256` and by a byte-identity rule
#: against its anchor commit, so editing it in place turns the governance test
#: RED and destroys the thing that made it a snapshot.
#:
#: Ownership of the re-seed is recorded in the Milestone 7 dogfooding work log --
#: "re-seed the affected items through `propose` under a new `sourceAnchor`, or
#: record an exemption". CONTRIBUTING.md says nothing about the corpus (checked
#: 2026-08-22), so the work log is the citation and not a convenience.
#:
#: The two commands the block below prints are named here in code spans as well
#: -- `theurian propose` and `theurian propose accept` -- because the documented
#: command scan reads *code spans* inside Python strings and comments, never a
#: bare invocation line inside a literal (`command_extraction.python_command_lines`,
#: and `json_command_lines`' docstring for why). Without this line the scan opens
#: this file and still never checks that the remedy it prints is runnable.
#:
#: `--source-uri` is written out in full and interpolated from
#: :data:`THIS_REPOSITORY`, never elided into the trailing `...`, because it is
#: the one option that decides whether the re-seeded item is ever checked again:
#: :func:`anchor_refusal` matches it exactly, so a re-seed under an SSH remote or
#: a suffix-less URL retires that item from this check for good. Interpolated
#: rather than typed so the string a maintainer is told to pass cannot drift from
#: the string it will be compared against.
#:
#: **It now promises the warning clears, because it does.** :func:`scan`
#: compares only each item's terminal revision -- the last ``upsertRevision``
#: for that ``itemId`` in application order -- so the migration the commands
#: below add supersedes the one that pinned the stale digest, and the
#: superseded revision drops out of the run entirely rather than going on
#: reporting DRIFT against a document it no longer describes (#317).
#: Re-seeding was always the right first step -- it is what puts the current
#: document back under governance -- and now it is also the whole remedy.
REMEDY: Final = f"""\
Fix: propose an update revision for the drifted item -- do not edit the
committed body, which is pinned verbatim.

    theurian propose --item-id <itemId> --expected-revision <revisionId> \\
        --body-file <filePath> --source-path <filePath> \\
        --source-uri "{THIS_REPOSITORY}" \\
        --source-commit "$(git rev-parse HEAD)" ...
    theurian propose accept <proposal-id>

This re-seed clears the warning. Only each item's terminal revision -- the
last upsertRevision for that itemId -- is compared, so the migration you just
added supersedes the one that pinned the stale digest, and the superseded
revision stops being compared at all.

Who owns the re-seed, and why an in-place edit is not an option, is recorded in
docs/work-logs/2026-08-19-milestone-7-dogfooding-dev7-corpus.md."""


class Verdict(Enum):
    """What became of one anchor."""

    MATCHED = "matched"
    DRIFTED = "drifted"
    SOURCE_MISSING = "source-missing"
    UNCHECKABLE = "uncheckable"


class Status(Enum):
    """What became of the run."""

    CLEAN = "clean"
    DRIFTED = "drifted"
    NOTHING_COMPARED = "nothing-compared"


@dataclass(frozen=True, slots=True)
class Comparison:
    """One anchor, and what comparing it produced.

    ``expected`` and ``actual`` are hex digests or ``""``; ``detail`` is one
    sentence naming the reason, and is what the annotations and the summary
    print. ``file_path`` is the document the anchor names, empty when it names
    nothing readable.
    """

    migration: str
    item_id: str
    revision_id: str
    file_path: str
    verdict: Verdict
    expected: str = ""
    actual: str = ""
    detail: str = ""


@dataclass(frozen=True, slots=True)
class Report:
    """Every anchor the run reached, and whether the run itself meant anything."""

    comparisons: tuple[Comparison, ...]
    status: Status
    detail: str

    def _of(self, *verdicts: Verdict) -> tuple[Comparison, ...]:
        return tuple(item for item in self.comparisons if item.verdict in verdicts)

    @property
    def matched(self) -> tuple[Comparison, ...]:
        return self._of(Verdict.MATCHED)

    @property
    def drifted(self) -> tuple[Comparison, ...]:
        """Drift, in both its shapes: changed bytes and a source that is gone."""
        return self._of(Verdict.DRIFTED, Verdict.SOURCE_MISSING)

    @property
    def uncheckable(self) -> tuple[Comparison, ...]:
        return self._of(Verdict.UNCHECKABLE)

    @property
    def compared(self) -> tuple[Comparison, ...]:
        """The anchors a byte comparison was actually attempted for.

        The count that decides whether this run asserted anything. An
        uncheckable anchor is reported, but it is not evidence of anything.
        """
        return self._of(Verdict.MATCHED, Verdict.DRIFTED, Verdict.SOURCE_MISSING)


# -- Asking git --------------------------------------------------------------

#: Environment variables that make git answer for a *different* tree or index
#: than the one it was handed. Dropped for the reason
#: ``test_dogfood_corpus_governance._INHERITED_GIT_OVERRIDES`` records: nobody
#: exports these by hand, but git exports them to hooks, and an inherited
#: ``GIT_INDEX_FILE`` makes ``ls-files --cached`` report somebody else's index --
#: which here reads as "the committed corpus is gone".
_INHERITED_GIT_OVERRIDES: Final = frozenset(
    {
        "GIT_ALTERNATE_OBJECT_DIRECTORIES",
        "GIT_COMMON_DIR",
        "GIT_CONFIG",
        "GIT_CONFIG_COUNT",
        "GIT_CONFIG_GLOBAL",
        "GIT_CONFIG_SYSTEM",
        "GIT_DIR",
        "GIT_INDEX_FILE",
        "GIT_OBJECT_DIRECTORY",
        "GIT_WORK_TREE",
    }
)

_GIT_TIMEOUT_SECONDS: Final = 30


def tracked_paths(repo_root: Path) -> frozenset[str] | None:
    """Repository-relative paths git says are tracked, or ``None`` if it cannot say.

    ``-z`` rather than newline-separated output: without it git quotes and
    escapes any path holding a non-ASCII byte, and a corpus seeded from
    documents with CJK titles is exactly where such a name appears.

    ``safe.directory`` is passed on the command line so a run under a different
    uid than the checkout's owner -- a container mounting the tree, a CI step
    under ``sudo`` -- gets an answer instead of ``detected dubious ownership``.
    Git honours the setting from the command scope; it grants nothing beyond
    reading this path, and no hook runs.
    """
    environment = {
        name: value for name, value in os.environ.items() if name not in _INHERITED_GIT_OVERRIDES
    }
    try:
        completed = subprocess.run(  # noqa: S603 - fixed argv, no shell, no caller input
            ["git", "-c", f"safe.directory={repo_root}", "ls-files", "--cached", "-z"],  # noqa: S607
            cwd=repo_root,
            check=False,
            capture_output=True,
            env=environment,
            timeout=_GIT_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if completed.returncode != 0:
        return None
    listing = completed.stdout.decode("utf-8", "surrogateescape")
    paths = frozenset(entry for entry in listing.split("\0") if entry)
    return paths or None


def migration_paths(tracked: Iterable[str]) -> tuple[str, ...]:
    """Tracked ``*.yaml`` **directly** under ``.theurian/migrations/``, sorted.

    The same key the loader enumerates by, because anything the loader applies
    is knowledge this repository serves and so has to be checked here.
    ``load_migrations`` lists the directory with ``iterdir()`` and keeps every
    entry whose name ends ``.yaml``; ``_entry_is_migration_file`` then classifies
    the *entry* -- file, symlink, enumeration race -- and never looks at the
    shape of the name. A migration renamed ``seed-adr-0005.yaml`` therefore
    loads, and is in this population too.

    ``is_migration_file_name`` does require a ULID prefix, but it is a
    *proposal*-directory predicate: ``accept`` uses it to pick the migration out
    of a directory that also holds bodies (``ProposalService``), and it never
    runs over ``.theurian/migrations/``. Reading it as the loader's own filter
    is what makes this population look wider than the loader's; it is not.

    Non-recursive for the same reason: ``iterdir()`` does not descend, so a
    nested path is not something the loader would ever apply.
    """
    return tuple(
        sorted(
            path
            for path in tracked
            if path.startswith(MIGRATIONS_PREFIX)
            and path.endswith(".yaml")
            and "/" not in path.removeprefix(MIGRATIONS_PREFIX)
        )
    )


# -- Resolving what an anchor names ------------------------------------------


def _inside(base: PurePosixPath, reference: str) -> str | None:
    """``reference`` resolved against ``base``, or ``None`` if it leaves the tree.

    Pure path arithmetic, never a filesystem call, so a symlink cannot decide
    the answer. ``None`` covers both escapes: climbing above the root, and being
    absolute in the first place -- ``PurePosixPath.__truediv__`` *discards* the
    left side when the right is absolute, so ``/etc/passwd`` would otherwise
    resolve and then be read.

    The other half of that trade is what this does **not** give a caller: the
    path it returns is contained as *written*, and every caller here goes on to
    ``read_bytes()`` it, which follows symlinks. Containment of the bytes that
    are actually hashed needs resolve-and-compare, which is #318.
    """
    joined = base / reference
    if joined.is_absolute():
        return None
    parts: list[str] = []
    for part in joined.parts:
        if part == "..":
            if not parts:
                return None
            parts.pop()
        elif part != ".":
            parts.append(part)
    return "/".join(parts) or None


def anchor_refusal(anchor: Any) -> str | None:
    """Why this anchor cannot honestly be compared, or ``None`` if it can.

    Every clause is a case where comparing anyway would assert something the
    recorded data does not support. See the module docstring for why a line
    range is one of them.
    """
    if not isinstance(anchor, Mapping):
        return f"is a {type(anchor).__name__}, not a mapping"
    provider = anchor.get("provider")
    source_uri = anchor.get("sourceUri")
    refusals = (
        (
            provider != "git",
            f"names provider {provider!r}; only a 'git' anchor names a file in this tree",
        ),
        (
            source_uri != THIS_REPOSITORY,
            f"names sourceUri {source_uri!r}, not {THIS_REPOSITORY!r}, so the file it means "
            f"is not in this checkout",
        ),
        (
            "lineStart" in anchor or "lineEnd" in anchor,
            "carries a line range, and contentSha256 digests the whole body -- no per-extent "
            "digest is recorded anywhere, so there is nothing to hold that range to",
        ),
    )
    for failed, reason in refusals:
        if failed:
            return reason
    return _file_path_refusal(anchor.get("filePath"))


def _file_path_refusal(file_path: Any) -> str | None:
    """Why the document an anchor names cannot be opened here, or ``None``."""
    if not isinstance(file_path, str) or not file_path:
        return f"names filePath {file_path!r}, so there is no document to compare against"
    if _inside(PurePosixPath(), file_path) is None:
        return f"names filePath {file_path!r}, which resolves outside the repository"
    if file_path.startswith(CORPUS_PREFIX):
        return (
            f"names filePath {file_path!r}, inside the corpus itself -- a snapshot pinned to a "
            f"copy of itself is a comparison that cannot fail"
        )
    return None


# -- Reading the corpus ------------------------------------------------------


def _revisions(document: Any) -> tuple[Mapping[str, Any], ...]:
    """The ``upsertRevision`` operations a migration document declares."""
    if not isinstance(document, Mapping):
        return ()
    operations = document.get("operations")
    if not isinstance(operations, Sequence) or isinstance(operations, str):
        return ()
    return tuple(
        operation
        for operation in operations
        if isinstance(operation, Mapping) and operation.get("op") == "upsertRevision"
    )


def _expected_digest(repo_root: Path, migration: str, operation: Mapping[str, Any]) -> str | None:
    """The digest the corpus records for this revision's body, or ``None``.

    The declared ``contentSha256`` when there is one. ADR-0027 made it
    schema-required, but this tool reads the tracked YAML directly and runs no
    schema check, so a hand-edited migration missing the line still arrives
    here; the body file itself is then hashed with the same call, and deleting
    the pin does not quietly turn this revision's drift check off. ``None`` only
    when neither is available, and that is reported as uncheckable rather than
    passed.
    """
    declared = operation.get("contentSha256")
    if isinstance(declared, str) and _SHA256.match(declared):
        return declared

    content_file = operation.get("contentFile")
    if not isinstance(content_file, str) or not content_file:
        return None
    relative = _inside(PurePosixPath(migration).parent, content_file)
    if relative is None or not relative.startswith(CORPUS_PREFIX):
        return None
    try:
        return ContentHash.of_bytes((repo_root / relative).read_bytes()).value
    except OSError:
        return None


def _compare(repo_root: Path, migration: str, operation: Mapping[str, Any]) -> list[Comparison]:
    """Every comparison one ``upsertRevision`` produces -- one per anchor, at least one."""
    item_id = str(operation.get("itemId", ""))
    revision_id = str(operation.get("revisionId", ""))

    def unchecked(file_path: str, detail: str) -> Comparison:
        return Comparison(
            migration=migration,
            item_id=item_id,
            revision_id=revision_id,
            file_path=file_path,
            verdict=Verdict.UNCHECKABLE,
            detail=detail,
        )

    metadata = operation.get("metadata")
    raw = metadata.get("sourceAnchors") if isinstance(metadata, Mapping) else None
    anchors: tuple[Any, ...] = (
        tuple(raw) if isinstance(raw, Sequence) and not isinstance(raw, str) else ()
    )
    if not anchors:
        return [unchecked("", "declares no sourceAnchors, so it names no document to compare")]

    # Judged once per anchor, and both sides of the split test `is None`. A
    # refused side filtered on truthiness would drop an anchor whose refusal is
    # an empty string out of *both* lists at once -- unreachable today, because
    # every branch of `anchor_refusal` returns a written sentence, but the cost
    # of it becoming reachable is an anchor that is neither compared nor
    # reported, which is the one outcome this checker promises never to
    # produce. Evaluating once also stops a future `anchor_refusal` that is not
    # a pure function from answering the two questions differently.
    judged = [(anchor, anchor_refusal(anchor)) for anchor in anchors]
    comparable = [anchor for anchor, refusal in judged if refusal is None]
    results = [
        unchecked(_named_path(anchor), refusal) for anchor, refusal in judged if refusal is not None
    ]

    if len(comparable) > 1:
        return results + [
            unchecked(
                _named_path(anchor),
                f"is one of {len(comparable)} comparable anchors on a revision that records a "
                f"single contentSha256 -- one digest cannot speak for several source files",
            )
            for anchor in comparable
        ]
    if not comparable:
        return results

    expected = _expected_digest(repo_root, migration, operation)
    file_path = _named_path(comparable[0])
    if expected is None:
        return [
            *results,
            unchecked(
                file_path,
                "declares no contentSha256 and its pinned body could not be read, so nothing "
                "was recorded that the current document could be held to",
            ),
        ]
    return [
        *results,
        _compare_one(
            repo_root,
            migration=migration,
            item_id=item_id,
            revision_id=revision_id,
            file_path=file_path,
            expected=expected,
        ),
    ]


def _named_path(anchor: Any) -> str:
    path = anchor.get("filePath") if isinstance(anchor, Mapping) else None
    return path if isinstance(path, str) else ""


def _compare_one(  # noqa: PLR0913 -- every field is carried straight into the Comparison
    repo_root: Path,
    *,
    migration: str,
    item_id: str,
    revision_id: str,
    file_path: str,
    expected: str,
) -> Comparison:
    """One anchor's document, hashed now, against the digest the corpus recorded."""

    def result(verdict: Verdict, actual: str, detail: str) -> Comparison:
        return Comparison(
            migration=migration,
            item_id=item_id,
            revision_id=revision_id,
            file_path=file_path,
            verdict=verdict,
            expected=expected,
            actual=actual,
            detail=detail,
        )

    source = repo_root / file_path
    try:
        payload = source.read_bytes()
    except FileNotFoundError:
        return result(
            Verdict.SOURCE_MISSING,
            "",
            f"{file_path} is gone, so the corpus holds a snapshot of a document this "
            f"repository no longer publishes",
        )
    except OSError as error:
        return result(Verdict.UNCHECKABLE, "", f"{file_path} could not be read ({error.strerror})")

    actual = ContentHash.of_bytes(payload).value
    if actual == expected:
        return result(Verdict.MATCHED, actual, f"{file_path} still hashes to {expected[:12]}")
    return result(
        Verdict.DRIFTED,
        actual,
        f"{file_path} now hashes to {actual[:12]}, and the corpus pins {expected[:12]}",
    )


def scan(repo_root: Path = REPO_ROOT, *, tracked: Iterable[str] | None = None) -> Report:
    """Compare each item's terminal revision's anchors against `docs/` now.

    ``tracked`` is the population, as repository-relative paths. Left ``None``
    it is taken from ``git ls-files --cached`` -- see the module docstring for
    why that, and not a filesystem glob, is the key. Passing it explicitly is
    the seam a test drives synthetic corpora through, and the only way to run
    this against a tree with no git.

    Every tracked migration is read, but only each item's *terminal*
    ``upsertRevision`` -- the last one for a given ``itemId`` in application
    order, the rule :func:`_current_operations` applies -- reaches
    :func:`_compare`. A superseded revision is read and then dropped before it
    produces any :class:`Comparison` at all: not compared, not reported
    uncheckable, simply not in the output (#317).
    """
    if tracked is None:
        tracked = tracked_paths(repo_root)
        if tracked is None:
            return Report(
                (),
                Status.NOTHING_COMPARED,
                f"`git ls-files --cached` did not answer in {repo_root} (no git, a timeout, or "
                f"not a working copy), and there is no filesystem fallback on purpose: a glob "
                f"would read untracked local-only migrations that no clone has.",
            )

    paths = migration_paths(tracked)
    if not paths:
        return Report(
            (),
            Status.NOTHING_COMPARED,
            f"nothing tracked under {MIGRATIONS_PREFIX}. The committed corpus is gone -- which "
            f"is a finding, not a reason for this check to report drift-free.",
        )

    comparisons: list[Comparison] = []
    revisions_by_path: dict[str, tuple[Mapping[str, Any], ...]] = {}
    for path in paths:
        try:
            document = yaml.safe_load((repo_root / path).read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError) as error:
            comparisons.append(
                Comparison(path, "", "", "", Verdict.UNCHECKABLE, detail=f"unreadable: {error}")
            )
            continue
        revisions = _revisions(document)
        if not revisions:
            comparisons.append(
                Comparison(
                    path,
                    "",
                    "",
                    "",
                    Verdict.UNCHECKABLE,
                    detail="declares no upsertRevision, so it pins no body to any document",
                )
            )
            continue
        revisions_by_path[path] = revisions

    current = _current_operations(revisions_by_path)
    for path, revisions in revisions_by_path.items():
        for operation in revisions:
            item_id = str(operation.get("itemId", ""))
            if current[item_id] is not operation:
                continue  # superseded by a later upsertRevision on the same item
            comparisons.extend(_compare(repo_root, path, operation))

    return Report(tuple(comparisons), *_verdict(comparisons, len(paths)))


def _current_operations(
    revisions_by_path: Mapping[str, tuple[Mapping[str, Any], ...]],
) -> dict[str, Mapping[str, Any]]:
    """Each ``itemId``'s terminal ``upsertRevision`` operation, keyed by ``itemId``.

    ``revisions_by_path`` must already be in application order -- the same
    order :func:`migration_paths` sorts by, an operation's own position inside
    its migration preserved -- because "terminal" means the *last* one reached
    in that walk. This is the same rule
    :func:`theurian.domain.migration.current_revision_in` states for the
    state-rebuild path: only an ``upsertRevision`` moves an item's current
    revision, and the last one for a given ``itemId`` wins.

    Not derived from ``expectedRevision``: the field is optional on the
    schema, and the original 26 seed migrations carry none at all, so a rule
    keyed on it would leave the whole seeded corpus without a terminal
    revision. Returns operation objects by identity, not by value, so a caller
    can tell which physical operation in ``revisions_by_path`` is the winner
    even when two upserts happen to be byte-identical.
    """
    current: dict[str, Mapping[str, Any]] = {}
    for revisions in revisions_by_path.values():
        for operation in revisions:
            current[str(operation.get("itemId", ""))] = operation
    return current


def _verdict(comparisons: Sequence[Comparison], migrations: int) -> tuple[Status, str]:
    """The run's own status, which is not the same question as "is there drift?"."""
    compared = [item for item in comparisons if item.verdict is not Verdict.UNCHECKABLE]
    if not compared:
        return (
            Status.NOTHING_COMPARED,
            f"all {len(comparisons)} anchor(s) across {migrations} committed migration(s) were "
            f"uncheckable, so this run compared nothing and proves nothing.",
        )
    drifted = [item for item in compared if item.verdict is not Verdict.MATCHED]
    counted = (
        f"compared {len(compared)} anchor(s) across {migrations} committed migration(s); "
        f"{len(comparisons) - len(compared)} uncheckable"
    )
    if drifted:
        return Status.DRIFTED, f"{len(drifted)} drifted -- {counted}."
    return Status.CLEAN, f"no drift -- {counted}."


# -- The floor -----------------------------------------------------------------


def minimum_compared_for(repo_root: Path, requested: int | None) -> int:
    """The floor to hold a run against ``repo_root`` to.

    :data:`MINIMUM_COMPARED` is a **measurement of this repository's corpus**,
    so it is applied to this repository's tree and to nothing else. A run
    pointed at another checkout, or at a fixture, is a tree where 26 was never
    measured; asserting it there would be inventing a number, and it would take
    every small-corpus caller of the CLI to exit 2. ``requested`` -- the
    ``--minimum-compared`` flag -- overrides both, and is how any other tree
    states its own floor.
    """
    if requested is not None:
        return requested
    return MINIMUM_COMPARED if repo_root == REPO_ROOT else 0


def held_to_floor(report: Report, minimum: int) -> Report:
    """``report``, or a ``NOTHING_COMPARED`` one when it compared fewer than ``minimum``.

    A new report rather than a mutation, so the comparisons are carried through
    untouched: the drift lines, the annotations and the remedy are all still
    rendered from them, and only the run's own verdict changes.

    **A report that is already ``NOTHING_COMPARED`` is returned as it stands.**
    :func:`scan` reaches that status by three routes, each with a diagnosis of
    its own: nothing tracked under ``.theurian/migrations/`` at all, git
    declining to say what is tracked, and every anchor it did reach being
    uncheckable. All three compare zero anchors, so without this the floor fires
    over the top of them and replaces the one sentence saying what happened --
    with text that then claims "Every anchor that stopped being comparable is
    named in this report", when the first two routes name none because there are
    none, and that offers "restore them, or lower the floor" as the remedy for a
    git that would not answer. The exit status is 2 either way; what survives is *which*
    failure a maintainer is looking at.

    **The floor outranks drift**, which is deliberate and is the whole point of
    binding it here. ``--advisory`` turns drift into exit 0, so a run that found
    one drifted anchor and lost the other twenty-five to uncheckability would
    otherwise report a finding and a green tick. Exit 2 is the code advisory
    mode does not touch.

    Applied in :func:`main` and not inside :func:`scan`, because ``scan`` is the
    comparison over whatever population it is handed -- tests drive it with
    corpora of one or two -- while the floor is a claim about a specific corpus.
    Keeping them apart is what lets the floor be strict without making ``scan``
    refuse the small inputs it exists to be driven with.
    """
    if report.status is Status.NOTHING_COMPARED:
        return report
    compared = len(report.compared)
    if minimum <= 0 or compared >= minimum:
        return report
    return Report(
        report.comparisons,
        Status.NOTHING_COMPARED,
        f"compared {compared} anchor(s), fewer than the {minimum} this corpus is held to, so "
        f"most of it went unchecked and a clean result would prove almost nothing. Every anchor "
        f"that stopped being comparable is named in this report, one SKIP line each: restore "
        f"them, or lower the floor in the same change that says why. A governed withdrawal that "
        f"lowers the live-item count exits 2 by design -- that is this guard working, not "
        f"failing -- so lower the floor constant in the same change that withdraws the item, "
        f"with the reasoning recorded there.",
    )


# -- Rendering ---------------------------------------------------------------


def render_text(report: Report) -> str:
    """The human report, for a local run and for the CI job log."""
    lines = [f"Corpus drift: {report.status.value} -- {report.detail}"]
    for item in report.drifted:
        lines.append(f"  DRIFT  {item.item_id}: {item.detail}")
        lines.append(f"         pinned by {item.migration}")
    for item in report.uncheckable:
        lines.append(f"  SKIP   {item.migration}: {item.detail}")
    if report.drifted:
        lines.extend(("", REMEDY))
    return "\n".join(lines)


def render_github(report: Report) -> tuple[str, ...]:
    """Workflow commands, so the findings land in the job log and on the PR.

    Drift is a ``warning`` because this check is advisory (see ``--advisory``).
    A run that compared nothing, or too little, is an ``error`` whatever the
    flags say: it is the outcome that must never read as green.

    The error's title names the shape rather than the extreme case: a run that
    compared nothing at all reaches it, and so does one that compared less than
    :func:`held_to_floor` demands. Each arrives carrying its own detail -- which
    is what :func:`held_to_floor` leaving an already-``NOTHING_COMPARED`` report
    untouched is for, so that the sentence beside the title still says which one
    happened. Titled *ran empty*, a breach on a corpus with twenty-five healthy
    anchors in it would be announced as a corpus that had vanished.
    """
    commands = [
        f"::warning file={item.file_path},title=Corpus drift::"
        f"{item.item_id} is a snapshot of {item.file_path}, which has changed. {item.detail}. "
        f"Re-seed it with `theurian propose` -- see tools/corpus_drift.py."
        for item in report.drifted
    ]
    commands.extend(
        f"::notice file={item.migration},title=Corpus anchor not compared::{item.detail}"
        for item in report.uncheckable
    )
    if report.status is Status.NOTHING_COMPARED:
        commands.append(
            f"::error title=Corpus drift check compared too few anchors::{report.detail}"
        )
    return tuple(commands)


def render_summary(report: Report) -> str:
    """Markdown for ``$GITHUB_STEP_SUMMARY``: the findings, without the log dive."""
    lines = [
        "## Dogfood corpus drift",
        "",
        f"**{report.status.value}** -- {report.detail}",
        "",
    ]
    if report.drifted:
        lines.extend(
            (
                "| Item | Source document | State |",
                "| :-- | :-- | :-- |",
                *(
                    f"| `{item.item_id}` | `{item.file_path}` | {item.verdict.value} |"
                    for item in report.drifted
                ),
                "",
                "```",
                REMEDY,
                "```",
                "",
            )
        )
    if report.uncheckable:
        lines.extend(
            (
                f"<details><summary>{len(report.uncheckable)} anchor(s) not compared</summary>",
                "",
                *(f"- `{item.migration}`: {item.detail}" for item in report.uncheckable),
                "",
                "</details>",
                "",
            )
        )
    return "\n".join(lines)


# -- Entry point -------------------------------------------------------------


def exit_code(report: Report, *, advisory: bool) -> int:
    """0 clean, 1 drift, 2 too little compared. ``advisory`` suppresses only the 1.

    The asymmetry is the point. Drift is a maintenance signal on a normal
    action -- somebody edited an ADR -- so failing the build on it would train
    people to bypass the job. A run that compared nothing, or too little to mean
    anything (:func:`held_to_floor`), is not a signal about the corpus at all;
    it is this tool reporting that it has stopped working, and advisory mode
    must not launder that into a pass.
    """
    if report.status is Status.NOTHING_COMPARED:
        return 2
    if report.status is Status.DRIFTED:
        return 0 if advisory else 1
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument(
        "--repo-root", type=Path, default=REPO_ROOT, help="Tree to check. Defaults to this one."
    )
    parser.add_argument(
        "--advisory",
        action="store_true",
        help="Report drift and exit 0 anyway. Does not suppress exit 2.",
    )
    parser.add_argument(
        "--format",
        choices=("text", "github"),
        default="text",
        help="`github` adds ::warning/::notice/::error workflow commands.",
    )
    parser.add_argument(
        "--summary",
        action="store_true",
        help="Append a Markdown summary to $GITHUB_STEP_SUMMARY, when it is set.",
    )
    parser.add_argument(
        "--minimum-compared",
        type=int,
        default=None,
        metavar="N",
        help=(
            f"Exit 2 unless at least N anchors were compared, whatever else the run found. "
            f"Defaults to {MINIMUM_COMPARED} for this repository's own tree, where that number "
            f"was measured, and to 0 -- no floor -- for any other. 0 disables it."
        ),
    )
    arguments = parser.parse_args(argv)

    repo_root = arguments.repo_root.resolve()
    report = held_to_floor(
        scan(repo_root), minimum_compared_for(repo_root, arguments.minimum_compared)
    )
    print(render_text(report))
    if arguments.format == "github":
        for command in render_github(report):
            print(command)
    summary_file = os.environ.get("GITHUB_STEP_SUMMARY")
    if arguments.summary and summary_file:
        with Path(summary_file).open("a", encoding="utf-8") as handle:
            handle.write(render_summary(report) + "\n")
    return exit_code(report, advisory=arguments.advisory)


if __name__ == "__main__":
    sys.exit(main())
