"""The issue premise sweep's driver: pull the tracker, then grade it offline.

The tracker holds roughly 128 open issues, and every one of their bodies and
comments cites files, line numbers, test names, constants, commit SHAs, ADRs
and cross-referenced issues. A later merge invalidates those citations
silently -- nobody edits an open issue when the line it points at moves. This
module re-verifies each citation against the current checkout and emits a
per-issue machine verdict with the command and captured output that produced
it, so a later agent pass (not this module) can grade PREMISE-MOOT versus
PREMISE-CHANGED and a human applies any closure. **This tool never writes to
the tracker** -- there is no code path here that constructs `gh issue close`,
`gh issue edit`, `gh issue comment`, or any other write.

Two subcommands, split because one needs the network and the other must not:

``fetch --json <snapshot.json> [--repo owner/name]``
    Two read-only calls, neither ``--paginate`` (the shipped package's own
    ``gh_cli`` clause 6 forbids it for the same reason it is forbidden here:
    the next page comes from a caller-supplied ``--limit``, not from a
    cursor the response hands back): ``gh issue list`` for every open
    issue's number, title, creation time, labels, body and comment bodies,
    and ``gh pr list --state all`` for every PR's number and state, so a
    cross-referenced PR (provenance, not a premise anchor) can be told apart
    from a genuinely closed or missing issue (round 2 HIGH-3). Both land in
    one snapshot file.

``check --snapshot <snapshot.json> --json <report.json> [--render report.md]``
    Offline against the snapshot and this checkout's own git -- no network
    call of any kind. For each issue: extract citations from body and
    comments (:mod:`premise_citations`), verify each one against a single
    git command, and scan history since the issue's own ``createdAt`` for
    commits touching any cited path. See :mod:`premise_verify` for the
    per-kind verification recipes and their exit-code semantics, each
    measured against this checkout rather than assumed from documentation.

Machine verdict
----------------
``PREMISE-HOLDS`` only when an issue has at least one citation, every
citation is INTACT, and no commit has touched a cited path since the issue
was opened. Everything else is ``NEEDS-AGENT``, carrying which of
``dangling-citation``, ``unknown-citation``, ``surface-touched``,
``no-citations``, ``check-error`` or ``reference-not-open`` applied.
``reference-not-open`` fires only when a cross-referenced number is neither
an open issue in the snapshot nor a known PR of any state -- a PR reference
is provenance for the issue that cites it, not a premise the issue made, and
grades INTACT instead (round 2 HIGH-3). This module never emits MOOT or
CHANGED: naming the commit that mooted an issue requires reading a diff,
which is judgement, not verification.

Determinism
-----------
Two runs of ``check`` against the same snapshot and the same ``HEAD`` produce
byte-identical JSON. Issues are processed in number order, citations in
``(kind, token)`` order (:mod:`premise_citations` already sorts them), and
the report is written with ``sort_keys=True`` and fixed separators so no
Python dict's insertion order or a hash-randomised set's iteration order can
reach the output.

Exit codes
----------
``0``
    The requested leg ran and wrote its output. A report that is entirely
    NEEDS-AGENT is a successful ``check`` -- the verdicts are the point.
``1``
    The leg could not run (a broken snapshot, `gh` unavailable or
    unauthenticated, `git` missing) or could not write its output.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final

import premise_report
from premise_citations import Citation, extract_citations, unique_basenames
from premise_verify import DANGLING, ERROR, INTACT, UNKNOWN, CommandResult, Runner, verify

REPO_ROOT: Final = Path(__file__).resolve().parents[1]

#: Well above the tracker's own size (~128 open issues at the time this was
#: written), so one call covers everything and pagination -- a cursor taken
#: from the response rather than asked for up front -- never enters the
#: picture.
FETCH_LIMIT: Final = 500

#: Every PR ever opened, closed or merged, well above this repository's own
#: PR count (309, measured 2026-09-20 via `gh pr list --state all`; issue and
#: PR numbers share one sequence, so this is well under the highest number
#: either has reached) -- a PR a still-open issue cites can be years old
#: (round 2 HIGH-3).
PR_FETCH_LIMIT: Final = 1000

DANGLING_CITATION: Final = "dangling-citation"
UNKNOWN_CITATION: Final = "unknown-citation"
SURFACE_TOUCHED: Final = "surface-touched"
NO_CITATIONS: Final = "no-citations"
CHECK_ERROR: Final = "check-error"
REFERENCE_NOT_OPEN: Final = "reference-not-open"

HOLDS: Final = "PREMISE-HOLDS"
NEEDS_AGENT: Final = "NEEDS-AGENT"


class PremiseError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class IssueRecord:
    number: int
    title: str
    created_at: str
    labels: tuple[str, ...]
    body: str
    comments: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Snapshot:
    issues: tuple[IssueRecord, ...]
    #: number -> `gh`'s own state string (``OPEN``/``CLOSED``/``MERGED``),
    #: sorted for determinism. Defaults empty so a snapshot fetched before
    #: round 2 HIGH-3 still loads: see :func:`snapshot_from_json`.
    pr_states: tuple[tuple[int, str], ...] = ()


@dataclass(frozen=True, slots=True)
class CitationResult:
    """``command`` is the argv(s) run, one per line, each reproducing the
    matching line of ``captured_output``; ``derivation`` is this tool's own
    sentence and never raw output (round 1 HIGH-2: the old ``output`` field
    mixed prose in, so a sha's exit-1 output could sit beside INTACT).
    """

    kind: str
    token: str
    command: str
    captured_output: str
    derivation: str
    status: str


@dataclass(frozen=True, slots=True)
class TouchingCommit:
    sha: str
    subject: str


@dataclass(frozen=True, slots=True)
class IssueReport:
    number: int
    title: str
    created_at: str
    labels: tuple[str, ...]
    citations: tuple[CitationResult, ...]
    touching_commits: tuple[TouchingCommit, ...]
    machine_verdict: str
    needs_agent_reasons: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Report:
    head_commit: str
    snapshot_path: str
    issues: tuple[IssueReport, ...]


def _argv_str(argv: Sequence[str]) -> str:
    return " ".join(argv)


# --------------------------------------------------------------------------
# The fetch leg
# --------------------------------------------------------------------------


def _list_argv(gh: str, repo: str | None) -> tuple[str, ...]:
    return (
        gh,
        "issue",
        "list",
        *(("--repo", repo) if repo else ()),
        "--state",
        "open",
        "--limit",
        str(FETCH_LIMIT),
        "--json",
        "number,title,createdAt,labels,body,comments",
    )


def _pr_list_argv(gh: str, repo: str | None) -> tuple[str, ...]:
    return (
        gh,
        "pr",
        "list",
        *(("--repo", repo) if repo else ()),
        "--state",
        "all",
        "--limit",
        str(PR_FETCH_LIMIT),
        "--json",
        "number,state",
    )


def _run_gh_list(runner: Runner, argv: tuple[str, ...], *, what: str) -> list[object]:
    result = runner(argv)
    if result.returncode != 0:
        raise PremiseError(
            f"`{_argv_str(argv)}` failed ({result.returncode}): {result.stderr.strip()}"
        )
    try:
        loaded = json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise PremiseError(f"`{what}` did not return JSON: {error}") from error
    if not isinstance(loaded, list):
        raise PremiseError(f"`{what}` returned something that is not a list")
    return loaded


def _label_names(raw: object) -> tuple[str, ...]:
    if not isinstance(raw, list):
        raise PremiseError("an issue's `labels` field in `gh issue list`'s output is not a list")
    names: list[str] = []
    for entry in raw:
        if not isinstance(entry, dict) or "name" not in entry:
            raise PremiseError("a label entry in `gh issue list`'s output has no `name` field")
        names.append(str(entry["name"]))
    return tuple(names)


def _comment_bodies(raw: object) -> tuple[str, ...]:
    if not isinstance(raw, list):
        raise PremiseError("an issue's `comments` field in `gh issue list`'s output is not a list")
    bodies: list[str] = []
    for entry in raw:
        if not isinstance(entry, dict) or "body" not in entry:
            raise PremiseError("a comment entry in `gh issue list`'s output has no `body` field")
        bodies.append(str(entry["body"]))
    return tuple(bodies)


def _issue_from_gh(entry: object) -> IssueRecord:
    if not isinstance(entry, dict):
        raise PremiseError("`gh issue list` returned an entry that is not an issue object")
    try:
        number = int(entry["number"])
        title = str(entry["title"])
        created_at = str(entry["createdAt"])
        body = str(entry["body"])
    except KeyError as missing:
        raise PremiseError(
            f"an issue in `gh issue list`'s output is missing {missing}"
        ) from missing
    return IssueRecord(
        number=number,
        title=title,
        created_at=created_at,
        labels=_label_names(entry.get("labels", [])),
        body=body,
        comments=_comment_bodies(entry.get("comments", [])),
    )


def _pr_state_from_gh(entry: object) -> tuple[int, str]:
    if not isinstance(entry, dict):
        raise PremiseError("`gh pr list` returned an entry that is not a pull-request object")
    try:
        number = int(entry["number"])
        state = str(entry["state"])
    except KeyError as missing:
        raise PremiseError(f"a PR in `gh pr list`'s output is missing {missing}") from missing
    return number, state


def fetch(runner: Runner, gh: str, *, repo: str | None = None) -> Snapshot:
    """Every open issue and every PR's state, in two read-only calls. Never mutates the tracker."""
    loaded_issues = _run_gh_list(runner, _list_argv(gh, repo), what="gh issue list")
    issues = tuple(
        sorted((_issue_from_gh(entry) for entry in loaded_issues), key=lambda issue: issue.number)
    )
    loaded_prs = _run_gh_list(runner, _pr_list_argv(gh, repo), what="gh pr list")
    pr_states = tuple(sorted(_pr_state_from_gh(entry) for entry in loaded_prs))
    return Snapshot(issues=issues, pr_states=pr_states)


def snapshot_to_json(snapshot: Snapshot) -> dict[str, object]:
    return {
        "issues": [
            {
                "number": issue.number,
                "title": issue.title,
                "createdAt": issue.created_at,
                "labels": list(issue.labels),
                "body": issue.body,
                "comments": list(issue.comments),
            }
            for issue in snapshot.issues
        ],
        "prStates": [{"number": number, "state": state} for number, state in snapshot.pr_states],
    }


def _string_tuple(raw: object, *, field: str) -> tuple[str, ...]:
    if not isinstance(raw, list):
        raise PremiseError(f"a snapshot issue's `{field}` field is not a list")
    return tuple(str(item) for item in raw)


def _issue_from_snapshot_json(entry: object) -> IssueRecord:
    if not isinstance(entry, dict):
        raise PremiseError("an entry in the snapshot's `issues` list is not an object")
    try:
        number = int(entry["number"])
        title = str(entry["title"])
        created_at = str(entry["createdAt"])
        body = str(entry["body"])
    except KeyError as missing:
        raise PremiseError(f"a snapshot issue is missing {missing}") from missing
    return IssueRecord(
        number=number,
        title=title,
        created_at=created_at,
        labels=_string_tuple(entry.get("labels", []), field="labels"),
        body=body,
        comments=_string_tuple(entry.get("comments", []), field="comments"),
    )


def _pr_state_from_snapshot_json(entry: object) -> tuple[int, str]:
    if not isinstance(entry, dict):
        raise PremiseError("an entry in the snapshot's `prStates` list is not an object")
    try:
        number = int(entry["number"])
        state = str(entry["state"])
    except KeyError as missing:
        raise PremiseError(f"a snapshot PR-state entry is missing {missing}") from missing
    return number, state


def snapshot_from_json(document: str) -> Snapshot:
    try:
        loaded = json.loads(document)
    except json.JSONDecodeError as error:
        raise PremiseError(f"the snapshot file is not JSON: {error}") from error
    if not isinstance(loaded, dict):
        raise PremiseError("the snapshot document is not an object")
    raw_issues = loaded.get("issues")
    if not isinstance(raw_issues, list):
        raise PremiseError("the snapshot document carries no `issues` list")
    # Absent in a snapshot fetched before round 2 HIGH-3 added the PR map --
    # an old snapshot still loads, degrading to today's issue-only grading.
    raw_pr_states = loaded.get("prStates", [])
    if not isinstance(raw_pr_states, list):
        raise PremiseError("the snapshot document's `prStates` field is not a list")
    return Snapshot(
        issues=tuple(_issue_from_snapshot_json(entry) for entry in raw_issues),
        pr_states=tuple(_pr_state_from_snapshot_json(entry) for entry in raw_pr_states),
    )


# --------------------------------------------------------------------------
# The check leg: per-kind verification lives in :mod:`premise_verify`
# --------------------------------------------------------------------------


def _underlying_path(kind: str, token: str) -> str:
    if kind == "path_line":
        path, _, _ = token.rpartition(":")
        return path
    return token


def _touching_commits(
    paths: Sequence[str], since: str, runner: Runner
) -> tuple[tuple[TouchingCommit, ...], bool]:
    """Commits since ``since`` touching any of ``paths`` -- the MOOT/CHANGED evidence.

    ``paths`` includes DANGLING as well as INTACT citations: a path `git
    cat-file` cannot find at HEAD may simply have been moved or deleted, and
    that history is exactly what tells the agent pass which. The bool is
    "the log call itself failed", kept separate from the empty tuple so a
    plumbing failure is distinguishable from "nothing touched this" -- see
    :data:`CHECK_ERROR`.
    """
    if not paths:
        return (), False
    # `--since-as-filter`, not `--since`, which is a traversal cutoff a rebased
    # or cherry-picked commit can hide later commits behind (round 1 MEDIUM-3).
    argv = (
        "git",
        "log",
        "--format=%H%x09%s",
        f"--since-as-filter={since}",
        "HEAD",
        "--",
        *sorted(paths),
    )
    result = runner(argv)
    if result.returncode != 0:
        return (), True
    commits = []
    for line in result.stdout.splitlines():
        sha, _, subject = line.partition("\t")
        if sha:
            commits.append(TouchingCommit(sha=sha, subject=subject))
    return tuple(commits), False


def _reasons(
    citations: Sequence[CitationResult], touching: Sequence[TouchingCommit], log_failed: bool
) -> tuple[str, ...]:
    reasons: list[str] = []
    if not citations:
        reasons.append(NO_CITATIONS)
    if any(citation.status == DANGLING for citation in citations):
        reasons.append(DANGLING_CITATION)
    if any(citation.status == UNKNOWN for citation in citations):
        reasons.append(UNKNOWN_CITATION)
    if any(citation.kind == "issue_ref" and citation.status == UNKNOWN for citation in citations):
        reasons.append(REFERENCE_NOT_OPEN)
    if log_failed or any(citation.status == ERROR for citation in citations):
        reasons.append(CHECK_ERROR)
    if touching:
        reasons.append(SURFACE_TOUCHED)
    return tuple(reasons)


def _citation_result(
    candidate: Citation,
    runner: Runner,
    snapshot_numbers: frozenset[int],
    pr_states: Mapping[int, str],
) -> CitationResult:
    result = verify(candidate.kind, candidate.token, runner, snapshot_numbers, pr_states)
    return CitationResult(
        kind=candidate.kind,
        token=candidate.token,
        command=result.command,
        captured_output=result.captured_output,
        derivation=result.derivation,
        status=result.status,
    )


def _check_issue(
    issue: IssueRecord,
    runner: Runner,
    snapshot_numbers: frozenset[int],
    tracked_basenames: Mapping[str, str],
    pr_states: Mapping[int, str],
) -> IssueReport:
    candidates = extract_citations(issue.body, issue.comments, tracked_basenames=tracked_basenames)
    results = tuple(_citation_result(c, runner, snapshot_numbers, pr_states) for c in candidates)
    paths = sorted(
        {
            _underlying_path(citation.kind, citation.token)
            for citation in results
            if citation.kind in ("path", "path_line") and citation.status in (INTACT, DANGLING)
        }
    )
    touching, log_failed = _touching_commits(paths, issue.created_at, runner)
    reasons = _reasons(results, touching, log_failed)
    return IssueReport(
        number=issue.number,
        title=issue.title,
        created_at=issue.created_at,
        labels=issue.labels,
        citations=results,
        touching_commits=touching,
        machine_verdict=NEEDS_AGENT if reasons else HOLDS,
        needs_agent_reasons=reasons,
    )


def _head_commit(runner: Runner) -> str:
    argv = ("git", "rev-parse", "HEAD")
    result = runner(argv)
    if result.returncode != 0:
        raise PremiseError(
            f"`{_argv_str(argv)}` failed ({result.returncode}): {result.stderr.strip()}"
        )
    return result.stdout.strip()


def _tracked_paths(runner: Runner) -> tuple[str, ...]:
    argv = ("git", "ls-tree", "-r", "--name-only", "HEAD")
    result = runner(argv)
    if result.returncode != 0:
        raise PremiseError(
            f"`{_argv_str(argv)}` failed ({result.returncode}): {result.stderr.strip()}"
        )
    return tuple(line for line in result.stdout.splitlines() if line)


def check(snapshot: Snapshot, runner: Runner, snapshot_path: str) -> Report:
    """Grade every issue in ``snapshot`` against this checkout's ``HEAD``. Offline."""
    head = _head_commit(runner)
    basenames = unique_basenames(_tracked_paths(runner))
    numbers = frozenset(issue.number for issue in snapshot.issues)
    pr_states = dict(snapshot.pr_states)
    issues = tuple(
        _check_issue(issue, runner, numbers, basenames, pr_states)
        for issue in sorted(snapshot.issues, key=lambda issue: issue.number)
    )
    return Report(head_commit=head, snapshot_path=snapshot_path, issues=issues)


def report_to_json(report: Report) -> dict[str, object]:
    return {
        "headCommit": report.head_commit,
        "snapshotPath": report.snapshot_path,
        "issues": [
            {
                "number": issue.number,
                "title": issue.title,
                "createdAt": issue.created_at,
                "labels": list(issue.labels),
                "citations": [
                    {
                        "kind": citation.kind,
                        "token": citation.token,
                        "command": citation.command,
                        "capturedOutput": citation.captured_output,
                        "derivation": citation.derivation,
                        "status": citation.status,
                    }
                    for citation in issue.citations
                ],
                "touchingCommits": [
                    {"sha": commit.sha, "subject": commit.subject}
                    for commit in issue.touching_commits
                ],
                "machineVerdict": issue.machine_verdict,
                "needsAgentReasons": list(issue.needs_agent_reasons),
            }
            for issue in report.issues
        ],
    }


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def run_command(argv: Sequence[str]) -> CommandResult:
    """The real runner: one process, no shell, rooted at this checkout."""
    try:
        completed = subprocess.run(  # noqa: S603 - argv is built by this module, never a shell
            list(argv), cwd=REPO_ROOT, capture_output=True, text=True, check=False
        )
    except OSError as error:
        raise PremiseError(f"could not run {argv[0]!r}: {error}") from error
    return CommandResult(
        returncode=completed.returncode, stdout=completed.stdout, stderr=completed.stderr
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tools/premise_check.py",
        description="Re-verify open issues' citations against the current checkout.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    fetch_parser = subparsers.add_parser(
        "fetch", help="pull every open issue and its comments into one snapshot file"
    )
    fetch_parser.add_argument(
        "--json", dest="snapshot_path", required=True, help="where to write it"
    )
    fetch_parser.add_argument("--repo", default=None, help="owner/name; omitted lets gh infer it")

    check_parser = subparsers.add_parser(
        "check", help="grade a snapshot's citations against this checkout's git, offline"
    )
    check_parser.add_argument("--snapshot", required=True, help="the fetch leg's output")
    check_parser.add_argument("--json", dest="report_path", required=True, help="where to write it")
    check_parser.add_argument(
        "--render", default=None, help="also write a Markdown triage report here"
    )

    return parser


def _write_json(path: str, payload: dict[str, object]) -> None:
    document = json.dumps(payload, indent=2, sort_keys=True, separators=(",", ": ")) + "\n"
    try:
        Path(path).write_text(document, encoding="utf-8")
    except OSError as error:
        raise PremiseError(f"could not write {path}: {error}") from error


def _run_fetch(args: argparse.Namespace) -> int:
    gh = shutil.which("gh") or "gh"
    snapshot = fetch(run_command, gh, repo=args.repo)
    _write_json(args.snapshot_path, snapshot_to_json(snapshot))
    print(
        f"fetched {len(snapshot.issues)} open issue(s) and {len(snapshot.pr_states)} pr state(s) "
        f"into {args.snapshot_path}"
    )
    return 0


def _run_check(args: argparse.Namespace) -> int:
    try:
        document = Path(args.snapshot).read_text(encoding="utf-8")
    except OSError as error:
        raise PremiseError(f"could not read {args.snapshot}: {error}") from error
    report = check(snapshot_from_json(document), run_command, args.snapshot)
    _write_json(args.report_path, report_to_json(report))
    if args.render:
        try:
            Path(args.render).write_text(premise_report.render(report), encoding="utf-8")
        except OSError as error:
            raise PremiseError(f"could not write {args.render}: {error}") from error
    holds = sum(1 for issue in report.issues if issue.machine_verdict == HOLDS)
    print(
        f"{len(report.issues)} issue(s) checked at {report.head_commit}: "
        f"{holds} {HOLDS}, {len(report.issues) - holds} {NEEDS_AGENT}"
    )
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        if args.command == "fetch":
            return _run_fetch(args)
        return _run_check(args)
    except PremiseError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
