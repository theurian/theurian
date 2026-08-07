"""A stateful stand-in for the GitHub releases API, reached as `gh` and `curl`.

Why a state machine and not canned responses
--------------------------------------------
The defect `release-core.yml` was rewritten to avoid is not "a command returned
the wrong JSON". It is that **a tag does not name one release**, so a lookup by
tag can resolve to a different object than the one a run created. A stub that
replays fixtures cannot express that: by-tag and by-id are indistinguishable
when the fixture holds one release, so an assertion against a replayed fixture
checks the *shape* of the command and calls it evidence.

So this keeps a real release list in a JSON file and every call mutates it. A
test can then assert the thing that decides whether the release was correct --
**which object ended up public** -- rather than how the command was spelled.

What is modelled, and where each rule comes from
------------------------------------------------
``POST /repos/{owner}/{repo}/releases``
    Returns the created release, id included, synchronously. This is why the
    workflow no longer reads the id back out of a list.

``GET /repos/{owner}/{repo}/releases``
    Eventually consistent: a release created at 12:38:09 was measured absent
    from the list at 12:38:10. ``list_lag`` hides the newest release from that
    many calls, which is what makes "the id never comes from here" testable.

``POST <upload_url>?name=...``
    The asset upload the workflow drives with `curl`, answering 201. Two
    failure modes are available: refusing after *n* assets, and answering 201
    without attaching -- the second is what the workflow's read-back check
    exists to catch.

``PATCH /releases/{id}`` with ``draft=false``
    HTTP 422 when another *published* release already carries the tag. Measured
    on GitHub Actions during #59's review. It is a real backstop, and the reason
    the guard now matches every release is that this one fires only after the
    PyPI upload.

    Every PATCH body is also appended to ``patches``, allowed or refused. That
    is where ``make_latest`` is readable, and it is the *only* place: what
    ``releases/latest`` points at is GitHub's decision, computed from a rule
    this model does not implement and could not establish. A test that reads
    ``patches`` is asserting what the API was asked for, not what it did, and
    says so.

``published_at``
    ``null`` until a release goes public, then :data:`PUBLISHED_AT`. The
    drafting guard prints it, because ``.draft`` alone cannot separate a draft
    that was never public from a published release *reverted* to a draft by
    deleting and re-pushing the tag -- and the second one's version is already
    on PyPI, so the two want opposite handling.

``gh release edit <tag>``
    Not used by the workflow any more, and kept because restoring it is the
    mutation that proves these tests can fail. ``FetchRelease``
    (``pkg/cmd/release/shared/fetch.go``) races a REST ``releases/tags/{tag}``
    lookup against a GraphQL draft lookup and takes whichever answers first;
    measured on real GitHub as the oldest draft, four times out of four. Both
    outcomes are reachable, so ``GH_RACE`` selects one and the tests run both.

Provenance for those measurements is #59's review, not this file. Nothing here
re-establishes them, and a test that leans on one says so.
"""

from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qs, urlparse

STATE = pathlib.Path(os.environ["GH_STATE"])


def _load() -> dict[str, Any]:
    data: dict[str, Any] = json.loads(STATE.read_text(encoding="utf-8"))
    return data


def _save(state: dict[str, Any]) -> None:
    STATE.write_text(json.dumps(state, indent=2), encoding="utf-8")


def _record(argv: list[str]) -> None:
    """Append this invocation to the ledger.

    Read back by the tests to assert that a refusal issued no write at all: an
    exit code alone does not separate "refused" from "did it, then complained".
    """
    state = _load()
    _save({**state, "calls": [*state.get("calls", []), argv]})


def _emit(payload: object, jq_filter: str | None) -> int:
    """Print through real jq, because the filters are the workflow's own text."""
    text = json.dumps(payload)
    if not jq_filter:
        sys.stdout.write(text + "\n")
        return 0
    completed = subprocess.run(  # noqa: S603 - fixed argv, no shell
        ["jq", "-r", jq_filter],  # noqa: S607 - jq is a documented prerequisite
        input=text,
        capture_output=True,
        text=True,
        check=False,
    )
    sys.stdout.write(completed.stdout)
    sys.stderr.write(completed.stderr)
    return completed.returncode


def _fail(message: str) -> int:
    sys.stderr.write(f"gh: {message}\n")
    return 1


def _replace(state: dict[str, Any], release: dict[str, Any]) -> dict[str, Any]:
    return {
        **state,
        "releases": [r if r["id"] != release["id"] else release for r in state["releases"]],
    }


# -- gh api ----------------------------------------------------------------


@dataclass(frozen=True)
class ApiCall:
    """One `gh api` invocation, as the model needs to see it."""

    method: str
    path: str | None
    jq_filter: str | None
    #: `-F key=value`, and `--input -` merged over it. A body arrives one way
    #: for creation and the other for publication, and both reach `body`.
    body: dict[str, Any]


def _parse_api(argv: list[str]) -> ApiCall:
    method, path, jq_filter, from_stdin = "GET", None, None, False
    fields: dict[str, Any] = {}
    index = 0
    while index < len(argv):
        argument = argv[index]
        if argument in {"-X", "--method"}:
            method, index = argv[index + 1], index + 2
        elif argument in {"--jq", "-q"}:
            jq_filter, index = argv[index + 1], index + 2
        elif argument == "--input":
            from_stdin, index = argv[index + 1] == "-", index + 2
        elif argument in {"-f", "-F", "--field", "--raw-field"}:
            key, _, value = argv[index + 1].partition("=")
            fields[key], index = value, index + 2
        elif argument.startswith("-"):
            index += 1
        else:
            path, index = argument, index + 1
    stdin_body = json.loads(sys.stdin.read() or "{}") if from_stdin else {}
    return ApiCall(method, path, jq_filter, {**fields, **stdin_body})


def _releases_newest_first(state: dict[str, Any]) -> list[dict[str, Any]]:
    """`GET /releases`, with the lag that made reading an id back unsafe."""
    releases = list(state["releases"])
    if state.get("list_lag", 0) > 0:
        _save({**state, "list_lag": state["list_lag"] - 1})
        if releases:
            newest = max(release["id"] for release in releases)
            releases = [release for release in releases if release["id"] != newest]
    return sorted(releases, key=lambda release: -release["id"])


def _git_ref(state: dict[str, Any], parts: list[str], jq_filter: str | None) -> int:
    tag = "/".join(parts[6:]) if len(parts) > 6 else parts[-1]
    if tag not in state.get("tags", []):
        return _fail("Not Found (HTTP 404)")
    return _emit({"ref": f"refs/tags/{tag}"}, jq_filter)


#: What `published_at` becomes when a release goes public. A fixed string, not
#: the clock: the guard prints this field, so a test that reads the printout
#: would otherwise depend on the second it ran in.
PUBLISHED_AT = "2026-08-06T12:38:09Z"


def _create(state: dict[str, Any], body: dict[str, Any], jq_filter: str | None) -> int:
    release_id = state["next_id"]
    draft = bool(body.get("draft", False))
    created = {
        "id": release_id,
        "tag_name": body.get("tag_name", ""),
        "name": body.get("name", ""),
        "body": body.get("body", ""),
        "draft": draft,
        # `null` on a draft that has never been public, a timestamp once it has.
        # The guard prints this because `.draft` alone cannot separate a draft
        # that was never published from a published release reverted to a draft
        # by deleting and re-pushing the tag -- and those two want opposite
        # handling, because the second one's version is already on PyPI.
        "published_at": None if draft else PUBLISHED_AT,
        "assets": [],
        "upload_url": f"https://uploads.invalid/releases/{release_id}/assets{{?name,label}}",
    }
    _save({**state, "next_id": release_id + 1, "releases": [*state["releases"], created]})
    return _emit(created, jq_filter)


def _tag_already_has_another_published_release(
    state: dict[str, Any], release: dict[str, Any]
) -> bool:
    """The condition behind the 422, wherever the PATCH is spelled from."""
    return any(
        r["id"] != release["id"] and r["tag_name"] == release["tag_name"] and not r["draft"]
        for r in state["releases"]
    )


def _one_release(
    state: dict[str, Any],
    release_id: int,
    method: str,
    body: dict[str, Any],
    jq_filter: str | None,
) -> int:
    found = next((r for r in state["releases"] if r["id"] == release_id), None)
    if found is None:
        return _fail("Not Found (HTTP 404)")
    if method == "DELETE":
        _save({**state, "releases": [r for r in state["releases"] if r["id"] != release_id]})
        return 0
    if method != "PATCH":
        return _emit(found, jq_filter)

    draft = body.get("draft")
    going_public = draft in {False, "false"} and found["draft"]
    rival = _tag_already_has_another_published_release(state, found)
    # Recorded whether or not the call is allowed to proceed, and keyed by the
    # release it named. `make_latest` has no consequence this model can compute
    # -- GitHub decides what `releases/latest` points at -- so the only place it
    # is observable is in what the API was asked for.
    state = {**state, "patches": [*state.get("patches", []), {"id": release_id, **body}]}
    if going_public and rival:
        _save(state)
        return _fail("Validation Failed (HTTP 422): a release for this tag already exists")
    updated = (
        found
        if draft is None
        else {
            **found,
            "draft": draft in {True, "true"},
            "published_at": PUBLISHED_AT if going_public else found.get("published_at"),
        }
    )
    _save(_replace(state, updated))
    return _emit(updated, jq_filter)


def _api(argv: list[str]) -> int:
    call = _parse_api(argv)
    if call.path is None:
        return _fail("no path given")
    method, jq_filter, body = call.method, call.jq_filter, call.body
    state = _load()
    parts = call.path.rstrip("/").split("/")

    if len(parts) >= 5 and parts[3:5] == ["git", "ref"]:
        return _git_ref(state, parts, jq_filter)
    # A trailing slash -- what an empty `${RELEASE_ID}` interpolates to -- lands
    # on the collection, which is why an unguarded empty id reaches a jq filter
    # written for a single release rather than reaching a lookup by tag.
    if parts[-1] == "releases":
        if method == "POST":
            return _create(state, body, jq_filter)
        return _emit(_releases_newest_first(state), jq_filter)
    if len(parts) == 5 and parts[3] == "releases":
        return _one_release(state, int(parts[4]), method, body, jq_filter)
    return _fail(f"stub: unsupported path {call.path}")


# -- gh release edit -------------------------------------------------------


def _release_edit(argv: list[str]) -> int:
    """FetchRelease resolves a *tag*, and a tag does not name one release.

    The write is the same ``PATCH /releases/{id}`` the workflow issues directly
    -- ``pkg/cmd/release/edit`` resolves the tag and then patches by id -- so it
    meets the same 422. Only the lookup differs, which is the whole point: this
    command chooses the object, and the workflow is given it.
    """
    tag = argv[0]
    publishing = "--draft=false" in argv
    state = _load()
    drafts = sorted(
        (r for r in state["releases"] if r["tag_name"] == tag and r["draft"]),
        key=lambda r: r["id"],
    )
    published = [r for r in state["releases"] if r["tag_name"] == tag and not r["draft"]]
    if os.environ.get("GH_RACE", "graphql") == "rest":
        target = published[0] if published else (drafts[0] if drafts else None)
    else:
        target = drafts[0] if drafts else (published[0] if published else None)
    if target is None:
        return _fail("release not found")
    if publishing and target["draft"]:
        if _tag_already_has_another_published_release(state, target):
            return _fail("Validation Failed (HTTP 422): a release for this tag already exists")
        _save(_replace(state, {**target, "draft": False, "published_at": PUBLISHED_AT}))
    sys.stdout.write(f"https://github.invalid/releases/tag/{tag}\n")
    return 0


def _gh(argv: list[str]) -> int:
    if not argv:
        return _fail("no command")
    if argv[0] == "api":
        return _api(argv[1:])
    if argv[:2] == ["release", "edit"]:
        return _release_edit(argv[2:])
    return _fail(f"stub: unsupported command {argv}")


# -- curl, for the asset uploads -------------------------------------------


def _parse_curl(argv: list[str]) -> tuple[str | None, pathlib.Path | None, pathlib.Path | None]:
    url, output, payload = None, None, None
    index = 0
    while index < len(argv):
        argument = argv[index]
        if argument == "-o":
            output, index = pathlib.Path(argv[index + 1]), index + 2
        elif argument == "--data-binary":
            payload, index = pathlib.Path(argv[index + 1].lstrip("@")), index + 2
        elif argument in {"-w", "-H", "-X"}:
            index += 2
        elif argument.startswith("-"):
            index += 1
        else:
            url, index = argument, index + 1
    return url, output, payload


def _curl(argv: list[str]) -> int:
    """Attach one asset, and answer with an HTTP status code on stdout.

    The step reads only ``%{http_code}``, so that is all this writes there; the
    response body goes to the ``-o`` file, which the step quotes in its error.
    """
    url, output, payload = _parse_curl(argv)
    if url is None or output is None or payload is None:
        return _fail("stub: unsupported curl invocation")
    parsed = urlparse(url)
    name = parse_qs(parsed.query).get("name", [payload.name])[0]
    release_id = int(pathlib.PurePosixPath(parsed.path).parts[2])

    state = _load()
    found = next((r for r in state["releases"] if r["id"] == release_id), None)
    if found is None:
        output.write_text(json.dumps({"message": "Not Found"}), encoding="utf-8")
        sys.stdout.write("404")
        return 0

    refuse_after = int(os.environ.get("UPLOAD_REFUSE_AFTER", "-1"))
    if 0 <= refuse_after <= len(found["assets"]):
        output.write_text(json.dumps({"message": "Bad gateway"}), encoding="utf-8")
        sys.stdout.write("502")
        return 0

    # 201 without attaching: the state the read-back check exists to catch.
    if name not in os.environ.get("UPLOAD_SILENTLY_DROPS", "").split(","):
        _save(_replace(state, {**found, "assets": [*found["assets"], {"name": name}]}))
    output.write_text(json.dumps({"name": name}), encoding="utf-8")
    sys.stdout.write("201")
    return 0


def main(argv: list[str]) -> int:
    tool, rest = argv[0], argv[1:]
    _record([tool, *rest])
    return _curl(rest) if tool == "curl" else _gh(rest)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
