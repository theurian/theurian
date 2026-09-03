"""The git-history trailer source against real repositories (ADR-0029).

Exercises the adapter's I/O contract -- ``refs/remotes/origin/main`` scoping (the
embargo linchpin), the loss-free mapping, provenance anchoring, and a total
deterministic order -- against real ``git`` repositories, because those are
exactly the properties a pure test cannot reach.

Two kinds of test live here. **Hermetic** tests build a throwaway git repository
in ``tmp_path`` and assert against trailers this file authored, so their oracle is
a hand-written expected value rather than a re-derivation of the adapter's own
algorithm. **Live-canary** tests read whatever ``refs/remotes/origin/main`` holds
now and assert a *property* (loss-free accounting, determinism, provenance) rather
than a count, so they cannot rot as the corpus grows. Both skip gracefully when
the checkout's git object store is unreachable -- the case inside
``tools/mutate.py``'s copied tree, which carries no ``.git`` -- so the mutation
harness's unmutated control stays GREEN.
"""

from __future__ import annotations

import os
import re
import subprocess
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Final

import pytest

from theurian.domain.review_finding import (
    TRAILER_KEY,
    FindingSeverity,
    ReviewerToken,
)
from theurian.infrastructure.git.trailer_source import (
    _FIELDS_PER_RECORD,
    _NUL,
    _UNDECODABLE_EXCERPT_BYTES,
    GitHistoryUnavailableError,
    GitOutputFramingError,
    GitTrailerFindingSource,
    _parse_committer_date,
    _split_records,
)

pytestmark = pytest.mark.integration

#: An immutable ancestor of ``origin/main`` (verified an ancestor 2026-08-26), so
#: the count pinned against it does not rot as new ``Review-Finding`` commits land
#: on the moving tip. Measured 2026-08-26 @ 4c4a784: 55 accepted trailers across 7
#: commits, token distribution 15 adversarial / 9 ``code`` (-> code-review) / 21
#: code-review / 10 security -> 30 code-review after alias normalisation.
FROZEN_SHA = "4c4a78475dc73d4689637fa995da76c4732c0511"


# --- git fixture helpers ---------------------------------------------------


def _child_env(env: dict[str, str] | None, *, hermetic: bool) -> dict[str, str]:
    """The environment one fixture ``git`` runs under.

    **A hermetic call ignores the developer's own git configuration**, not only
    the parts that set a commit identity: ``GIT_CONFIG_GLOBAL`` and
    ``GIT_CONFIG_SYSTEM`` are pinned to ``os.devnull``, applied *after* ``env`` is
    merged so a caller that forgets cannot override it. ``init`` and ``clone``
    read global config too, not only ``commit`` -- a ``core.hooksPath`` or a clone
    template would otherwise run under the developer's real settings, and a
    ``commit.gpgsign = true`` makes this file's fixture commits sign with their
    live key: a passphrase or hardware-token prompt with no test invoking one.
    Measured 2026-09-03 while building the #496 plants -- a fixture-shaped ``git
    commit -F`` without this pin produced a signed commit on this machine.
    ``test_findings_build_cli.py`` records the same finding from its own round-two
    review and pins the same two variables for the same reason.

    **``hermetic=False`` is for a read against the *real* checkout**, and the
    distinction is load-bearing rather than tidiness: ``safe.directory`` lives in
    global config, and a checkout owned by another user -- the offline CI job runs
    as root -- is refused without it. Pinning it away there would turn the live
    canary into a silent skip (its ``origin/main`` probe answering "absent") or a
    hard error, which is a guard that stops guarding. Five other modules in this
    suite pass ``-c safe.directory=`` for exactly that reason. Only the two calls
    that address the checkout these tests live in pass it.
    """
    base = env if env is not None else dict(os.environ)
    if not hermetic:
        return base
    return {**base, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull}


def _git(root: Path, *args: str, env: dict[str, str] | None = None, hermetic: bool = True) -> str:
    result = subprocess.run(  # noqa: S603
        ["git", *args],  # noqa: S607 - git resolved via PATH, args are test-controlled
        cwd=root,
        check=True,
        capture_output=True,
        env=_child_env(env, hermetic=hermetic),
    )
    return result.stdout.decode("utf-8")


def _git_ok(root: Path, *args: str, hermetic: bool = True) -> bool:
    """True when ``git *args`` exits zero -- for a ref/object existence probe."""
    return (
        subprocess.run(  # noqa: S603
            ["git", *args],  # noqa: S607 - git resolved via PATH, args are test-controlled
            cwd=root,
            check=False,
            capture_output=True,
            env=_child_env(None, hermetic=hermetic),
        ).returncode
        == 0
    )


def _identity_env(author_when: str, committer_when: str) -> dict[str, str]:
    return {
        **os.environ,
        "GIT_AUTHOR_NAME": "Tester",
        "GIT_AUTHOR_EMAIL": "tester@example.com",
        "GIT_COMMITTER_NAME": "Tester",
        "GIT_COMMITTER_EMAIL": "tester@example.com",
        "GIT_AUTHOR_DATE": author_when,
        "GIT_COMMITTER_DATE": committer_when,
    }


def _date_env(when: str) -> dict[str, str]:
    return _identity_env(when, when)


def _origin_and_clone(tmp_path: Path, name: str = "repo") -> tuple[Path, Path]:
    """A bare origin and a working clone, named so several can coexist in one test."""
    origin = tmp_path / f"{name}-origin.git"
    clone = tmp_path / f"{name}-clone"
    _git(tmp_path, "init", "--bare", "-b", "main", str(origin))
    _git(tmp_path, "clone", str(origin), str(clone))
    _git(clone, "config", "user.name", "Tester")
    _git(clone, "config", "user.email", "tester@example.com")
    return origin, clone


def _commit(clone: Path, subject: str, *trailers: str, when: str = "2026-03-01T12:00:00") -> str:
    message = subject if not trailers else subject + "\n\n" + "\n".join(trailers)
    _git(clone, "commit", "--allow-empty", "-m", message, env=_date_env(when))
    return _git(clone, "rev-parse", "HEAD").strip()


def _commit_split_date(
    clone: Path,
    subject: str,
    *trailers: str,
    author_when: str,
    committer_when: str,
) -> str:
    """A commit whose author and committer dates deliberately differ.

    The record must carry the *committer* date (``%cI``); this helper is what makes
    ``%cI`` distinguishable from ``%aI`` in a test.
    """
    message = subject if not trailers else subject + "\n\n" + "\n".join(trailers)
    _git(
        clone,
        "commit",
        "--allow-empty",
        "-m",
        message,
        env=_identity_env(author_when, committer_when),
    )
    return _git(clone, "rev-parse", "HEAD").strip()


def _commit_body_file(clone: Path, body_bytes: bytes, when: str = "2026-03-01T12:00:00") -> str:
    """Commit a message authored as raw bytes via ``-F`` (for RS/US byte bodies).

    "Raw" reaches as far as bytes git will *keep*: measured 2026-09-03 on git
    2.47.1, ``-F`` re-encodes a message that is not valid UTF-8 (a lone ``0x80``
    stored as ``0xc2 0x80``) and warns. The RS/US and lone-CR bodies below are
    ASCII, so they survive; a non-UTF-8 plant needs
    :func:`_commit_with_raw_message` instead.
    """
    message_file = clone / "_msg.bin"
    message_file.write_bytes(body_bytes)
    _git(clone, "commit", "--allow-empty", "-F", str(message_file), env=_date_env(when))
    message_file.unlink()
    return _git(clone, "rev-parse", "HEAD").strip()


def _git_bytes(root: Path, *args: str, stdin: bytes | None = None) -> bytes:
    """``git *args`` with its stdout left as raw bytes, optionally fed on stdin.

    The text-returning :func:`_git` cannot serve the #496 fixtures at either end: a
    hand-built commit object is written by feeding its raw bytes to ``git
    hash-object``, and the premise that git kept those bytes verbatim is read back
    with ``cat-file``/``log``, where decoding is the very thing under test.

    Always hermetic, with no opt-out: every caller addresses a repository this file
    built, so the live-checkout exemption :func:`_child_env` documents has nobody
    to serve here.
    """
    result = subprocess.run(  # noqa: S603
        ["git", *args],  # noqa: S607 - git resolved via PATH, args are test-controlled
        cwd=root,
        check=True,
        capture_output=True,
        input=stdin,
        env=_child_env(None, hermetic=True),
    )
    return result.stdout


#: The committer instant the hand-built commits below carry: 2026-02-01T00:00:00Z,
#: between the ``2026-01-01``/``2026-03-01`` siblings the tests commit around them,
#: so the accepted order is chronological rather than a sha tie-break. Written as an
#: epoch because a hand-built object carries git's raw ``<epoch> <±hhmm>`` stamp.
_RAW_COMMIT_EPOCH: Final = 1769904000


def _commit_with_raw_message(clone: Path, message: bytes, *, epoch: int = _RAW_COMMIT_EPOCH) -> str:
    """Commit a message git stores **verbatim**, however invalid its bytes (#496).

    Hand-builds the commit object and writes it with ``git hash-object -t commit -w
    --stdin --literally``, then moves ``refs/heads/main`` onto it, so the caller's
    usual :func:`_publish` pushes it like any other commit (measured 2026-09-03: a
    non-UTF-8-message object pushes and fetches without complaint).

    **The hand-build is the point, not an economy.** Measured on git 2.47.1
    (2026-09-03), both ``git commit -F`` and ``git commit-tree`` RE-ENCODE a message
    that is not UTF-8 -- a lone ``0x80`` is stored as ``0xc2 0x80`` -- and warn
    while doing it. So the file's own :func:`_commit_body_file`, and every other
    porcelain route, would produce a commit whose message is *valid* UTF-8 and a
    test that proves nothing about the containment it claims to drive. Each caller
    still asserts the premise against the object git actually stored, because a
    fixture that quietly stops exercising the case is exactly what this helper
    exists to prevent.
    """
    tree = _git(clone, "rev-parse", "HEAD^{tree}").strip()
    parent = _git(clone, "rev-parse", "HEAD").strip()
    who = f"Tester <tester@example.com> {epoch} +0000"
    payload = f"tree {tree}\nparent {parent}\nauthor {who}\ncommitter {who}\n\n".encode() + message
    sha = (
        _git_bytes(
            clone, "hash-object", "-t", "commit", "-w", "--stdin", "--literally", stdin=payload
        )
        .decode("utf-8")
        .strip()
    )
    _git(clone, "update-ref", "refs/heads/main", sha)
    return sha


def _stored_message_of(clone: Path, sha: str) -> bytes:
    """The raw ``%B`` bytes git emits for one commit -- the fixture's own premise."""
    return _git_bytes(clone, "log", "-1", "--format=format:%B", sha)


def _publish(clone: Path) -> None:
    """Push main and refresh the ``origin/main`` tracking ref the adapter reads."""
    _git(clone, "push", "origin", "main")
    _git(clone, "fetch", "origin")


def _live_repo_root() -> Path | None:
    """The checkout these tests live in, or ``None`` when there is none.

    ``None`` -- rather than a raised ``CalledProcessError`` -- when the tree is not
    inside a git repository, which is exactly ``tools/mutate.py``'s copied tree
    (it excludes ``.git``). The old helper used ``check=True`` and errored there,
    reddening the harness's unmutated control and voiding every KILLED verdict in
    the batch; returning ``None`` lets the live/hermetic tests skip instead.
    """
    here = Path(__file__).resolve().parent
    proc = subprocess.run(  # noqa: S603
        ["git", "-C", str(here), "rev-parse", "--show-toplevel"],  # noqa: S607
        check=False,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        return None
    return Path(proc.stdout.strip())


def _real_object_store() -> Path | None:
    """The shared object store of the checkout these tests live in, or ``None``.

    Borrowed via a git ``alternates`` file so a hermetic fixture can point
    ``refs/remotes/origin/main`` at the immutable :data:`FROZEN_SHA` and read its
    real trailers through the production adapter. ``None`` when the tree is not a
    git repository (``tools/mutate.py``'s copied tree), so the pin skips there and
    the mutation control stays GREEN.
    """
    here = Path(__file__).resolve().parent
    proc = subprocess.run(  # noqa: S603
        ["git", "-C", str(here), "rev-parse", "--git-common-dir"],  # noqa: S607
        check=False,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        return None
    common = Path(proc.stdout.strip())
    if not common.is_absolute():
        common = (here / common).resolve()
    objects = common / "objects"
    return objects if objects.is_dir() else None


def _origin_main_present(repo: Path) -> bool:
    # One of the two calls that address the *real* checkout, so it keeps the
    # developer's global config: without it a `safe.directory` refusal on a
    # differently-owned checkout answers "no origin/main" and the live canary
    # skips itself (see `_child_env`).
    return _git_ok(
        repo, "rev-parse", "--verify", "--quiet", "refs/remotes/origin/main", hermetic=False
    )


# --- AC-3 / D7: the source reads only public refs/remotes/origin/main -------


def test_source_reads_only_origin_main_not_local_branches(tmp_path: Path) -> None:
    """A trailer on a local, unpushed branch is not ingested -- only origin/main.

    This is decision 6's structural embargo protection: the embargoed finding
    lives off public ``main``, so it must not reach the source. The unpushed
    branch is reachable via ``git log --all`` and via ``HEAD`` while checked out,
    so an adapter that read either would leak it; reading ``origin/main`` does not.
    """
    _origin, clone = _origin_and_clone(tmp_path)
    _commit(clone, "fix: public change (#5)", "Review-Finding: security HIGH — a public finding")
    _publish(clone)

    _git(clone, "checkout", "-b", "embargo")
    _commit(
        clone,
        "fix: embargoed change (#6)",
        "Review-Finding: security CRITICAL — an embargoed finding",
    )
    # Deliberately NOT published. Leave HEAD on the embargo branch to prove the
    # adapter pins origin/main rather than reading the current branch.

    findings = GitTrailerFindingSource(clone).load_findings().accepted
    texts = [f.finding_text for f in findings]
    assert "a public finding" in texts
    assert "an embargoed finding" not in texts


def test_all_would_have_leaked_the_local_branch(tmp_path: Path) -> None:
    """The control for AC-3: the embargoed commit really is reachable via --all.

    Without this, the scoping test could pass merely because the branch commit
    was unreachable for an unrelated reason. Reading every ref finds it; the
    adapter's ``origin/main`` does not.
    """
    _origin, clone = _origin_and_clone(tmp_path)
    _commit(clone, "fix: public (#5)", "Review-Finding: security HIGH — a public finding")
    _publish(clone)
    _git(clone, "checkout", "-b", "embargo")
    _commit(
        clone, "fix: embargoed (#6)", "Review-Finding: security CRITICAL — an embargoed finding"
    )

    all_bodies = _git(clone, "log", "--all", "--format=%b")
    assert "an embargoed finding" in all_bodies  # --all sees it; the adapter must not


def _embargo_shadow(tmp_path: Path) -> tuple[Path, str]:
    """A published-public clone plus a *dangling* embargo commit's sha.

    The embargo commit carries a CRITICAL trailer and is reachable from no ref
    ``refs/remotes/origin/main`` points at; each D7 face then wires a locally
    forgeable name onto that sha and asserts the adapter still refuses it.
    """
    _origin, clone = _origin_and_clone(tmp_path)
    _commit(clone, "fix: public (#1)", "Review-Finding: security HIGH — a public finding")
    _publish(clone)
    _git(clone, "checkout", "-b", "embargo")
    embargo = _commit(
        clone, "fix: embargoed (#2)", "Review-Finding: security CRITICAL — an embargoed finding"
    )
    _git(clone, "checkout", "main")
    _git(clone, "branch", "-D", "embargo")  # unreferenced except by the shadow we add
    return clone, embargo


def _assert_only_public(clone: Path) -> None:
    texts = [f.finding_text for f in GitTrailerFindingSource(clone).load_findings().accepted]
    assert "a public finding" in texts
    assert "an embargoed finding" not in texts


def test_a_shadowing_local_branch_named_origin_main_is_not_read(tmp_path: Path) -> None:
    """D7: a local ``refs/heads/origin/main`` must not answer for the remote ref.

    gitrevisions resolves the short name ``origin/main`` through ``refs/heads``
    before ``refs/remotes``, so a mutation reading the short name would ingest this
    embargoed commit. The adapter reads the fully-qualified
    ``refs/remotes/origin/main``, so the shadow is invisible to it.
    """
    clone, embargo = _embargo_shadow(tmp_path)
    _git(clone, "update-ref", "refs/heads/origin/main", embargo)

    _assert_only_public(clone)


def test_a_shadowing_tag_named_origin_main_is_not_read(tmp_path: Path) -> None:
    """D7: a ``refs/tags/origin/main`` tag must not answer for the remote ref.

    ``refs/tags/<name>`` is tried ahead of ``refs/remotes/<name>``, so the short
    name would resolve to this tag. The fully-qualified read ignores it.
    """
    clone, embargo = _embargo_shadow(tmp_path)
    _git(clone, "update-ref", "refs/tags/origin/main", embargo)

    _assert_only_public(clone)


def test_a_bare_ref_origin_main_is_not_read(tmp_path: Path) -> None:
    """D7: a bare ``refs/origin/main`` must not answer for the remote ref.

    ``refs/<name>`` is the very first thing gitrevisions tries, so a bare ref is
    the highest-priority shadow of all. The fully-qualified read still ignores it.
    """
    clone, embargo = _embargo_shadow(tmp_path)
    _git(clone, "update-ref", "refs/origin/main", embargo)

    _assert_only_public(clone)


def test_a_git_replace_on_the_public_tip_is_not_read(tmp_path: Path) -> None:
    """D7: a ``git replace`` mapping the public tip onto an embargo body must not read.

    ``git replace`` rewrites what a sha *resolves to*, so a default ``git log``
    over ``refs/remotes/origin/main`` -- whose tip's sha is now replaced -- would
    serve the embargo body under the public provenance. The adapter disables
    replacement (``--no-replace-objects`` and ``GIT_NO_REPLACE_OBJECTS=1``), so it
    reads the real public tip.
    """
    _origin, clone = _origin_and_clone(tmp_path)
    _commit(clone, "fix: public (#1)", "Review-Finding: security HIGH — a public finding")
    _publish(clone)
    public_tip = _git(clone, "rev-parse", "refs/remotes/origin/main").strip()

    _git(clone, "checkout", "-b", "embargo")
    embargo = _commit(
        clone, "fix: embargoed (#2)", "Review-Finding: security CRITICAL — an embargoed finding"
    )
    _git(clone, "checkout", "main")
    _git(clone, "replace", public_tip, embargo)

    # Control: with replacement active, a naive read *does* serve the embargo body.
    replaced_bodies = _git(clone, "log", "refs/remotes/origin/main", "--format=%b")
    assert "an embargoed finding" in replaced_bodies

    _assert_only_public(clone)


def test_an_injected_git_dir_does_not_hijack_the_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """D7: an ambient ``GIT_DIR`` must not redirect what "public history" means.

    ``GIT_DIR`` binds the repository git reads regardless of ``cwd``, so an
    inherited one pointing at an attacker's repo would make the adapter ingest that
    repo's ``refs/remotes/origin/main`` while appearing to read the clone it was
    handed. The adapter strips every inherited ``GIT_*`` override, so the clone is
    what it reads.
    """
    _origin_pub, public = _origin_and_clone(tmp_path, "public")
    _commit(public, "fix: public (#1)", "Review-Finding: security HIGH — a public finding")
    _publish(public)

    _origin_evil, evil = _origin_and_clone(tmp_path, "evil")
    _commit(evil, "fix: evil (#2)", "Review-Finding: security CRITICAL — an embargoed finding")
    _publish(evil)

    # Set the override only now, after every fixture ``_git`` call has run, so it
    # reaches the adapter's child ``git`` and nothing else.
    monkeypatch.setenv("GIT_DIR", str(evil / ".git"))

    _assert_only_public(public)


def test_a_repo_local_show_signature_does_not_corrupt_the_parse(tmp_path: Path) -> None:
    """D7 hardening: a repo-level ``log.showSignature=true`` must not break the read.

    A repository config could switch signature display on to inject ``gpg:`` lines
    into the ``git log`` stream. The adapter forces ``log.showSignature=false`` on
    the command line, so the finding parses cleanly whatever the repo config says.
    """
    _origin, clone = _origin_and_clone(tmp_path)
    _commit(clone, "fix: signed-config (#1)", "Review-Finding: security HIGH — a public finding")
    _publish(clone)
    _git(clone, "config", "log.showSignature", "true")

    (finding,) = GitTrailerFindingSource(clone).load_findings().accepted
    assert finding.finding_text == "a public finding"
    assert finding.reviewer is ReviewerToken.SECURITY


def test_git_config_parameters_is_stripped_so_the_output_stays_utf8(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """D7 completeness: an inherited ``GIT_CONFIG_PARAMETERS`` must not redirect config.

    ``GIT_CONFIG_PARAMETERS`` injects config as if by ``-c`` -- a vector the
    ``GIT_CONFIG{,_COUNT,_GLOBAL,_SYSTEM}`` strip did not cover. An inherited
    ``i18n.logOutputEncoding=UTF-16`` makes ``git log`` emit UTF-16, and a load
    that reads such a stream is refused whole: UTF-16 carries NUL bytes of its own,
    so the stream does not partition into records and the framing guard raises --
    ``test_a_utf16_git_log_stream_is_a_typed_framing_error_not_an_uncaught_decode``
    drives exactly that. The adapter strips ``GIT_CONFIG_PARAMETERS`` with the rest
    of the ``GIT_*`` overrides, so the finding reads cleanly whatever config the
    ambient environment tried to inject, and that refusal stays the backstop rather
    than the behaviour.
    """
    _origin, clone = _origin_and_clone(tmp_path)
    _commit(clone, "fix: utf8 (#1)", "Review-Finding: security HIGH — a public finding")
    _publish(clone)

    # Set only now, after every fixture ``_git`` call, so it reaches the adapter's
    # child ``git`` and nothing else -- mirroring the injected-GIT_DIR test.
    monkeypatch.setenv("GIT_CONFIG_PARAMETERS", "'i18n.logOutputEncoding=UTF-16'")

    (finding,) = GitTrailerFindingSource(clone).load_findings().accepted
    assert finding.finding_text == "a public finding"
    assert finding.reviewer is ReviewerToken.SECURITY


def test_a_utf16_git_log_stream_is_a_typed_framing_error_not_an_uncaught_decode(
    tmp_path: Path,
) -> None:
    """UTF-16 output from ``git`` is a typed error with a remedy, not a raw crash.

    Defense in depth for the decode: stripping ``GIT_CONFIG_PARAMETERS`` closes the
    known ``i18n.logOutputEncoding=UTF-16`` vector, but any residual whole-stream
    encoding change must still degrade to a ``TheurianError`` carrying a remedy,
    never an uncaught ``UnicodeDecodeError``.

    **Which typed error moved with #496, and the reason is not cosmetic.** This
    used to be a :class:`GitHistoryUnavailableError` whose remedy named ``git
    fetch``, because the adapter decoded the whole stdout in one call and any
    failure there meant "no readable history". Since the stream is framed *before*
    it is decoded, a UTF-16 stream is refused as mis-framed: its UTF-16 code units
    embed the NUL bytes the framing partitions on, so ``b"\\xff\\xfe\\x00R\\x00e
    \\x00v"`` splits into four tokens -- not a multiple of three -- and the
    framing guard raises before any decode is attempted. That is the honest answer
    for a git whose output encoding is not the one this adapter asked for, and it
    is why the fetch remedy is no longer offered for it. The remedy that *is*
    offered names the installed git version.

    The four-token premise is asserted, so this cannot pass merely because some
    other guard happened to fire.
    """
    utf16 = b"\xff\xfe\x00R\x00e\x00v"
    assert len(utf16.split(_NUL)) == 4, "the premise: UTF-16 output carries NUL bytes of its own"

    with pytest.raises(GitOutputFramingError) as caught:
        _split_records(utf16, tmp_path)
    assert caught.value.remedy  # a non-empty remedy, not a bare stack trace
    assert "git version" in caught.value.remedy


# --- D4: NUL framing, not forgeable RS/US ----------------------------------


def test_rs_and_us_bytes_in_a_body_do_not_fabricate_a_record(tmp_path: Path) -> None:
    """D4: literal RS (0x1e)/US (0x1f) bytes in a body must not fabricate a record.

    An earlier design framed ``git log`` records with RS/US, which git *permits* in
    a commit body -- so an author could embed them to inject a fabricated finding
    carrying an attacker-chosen sha and date. The adapter frames with NUL, which no
    commit content can place in a field -- ``%B`` truncates a message at its first
    NUL rather than emitting it (D4, and the test below) -- so a body full of RS/US
    yields exactly the one real trailer, anchored to the real 40-hex sha, and
    nothing else.
    """
    _origin, clone = _origin_and_clone(tmp_path)
    body = (
        b"subject with control bytes\n\n"
        b"a body paragraph with \x1e record and \x1f unit separators embedded\n\n"
        b"Review-Finding: security HIGH \xe2\x80\x94 a real finding\n"
    )
    sha = _commit_body_file(clone, body)
    _publish(clone)

    load = GitTrailerFindingSource(clone).load_findings()
    assert [f.finding_text for f in load.accepted] == ["a real finding"]
    assert load.accepted[0].commit_sha == sha
    assert re.fullmatch(r"[0-9a-f]{40}", load.accepted[0].commit_sha)
    assert load.rejected == ()


#: The plant for the bound below: a commit message carrying a NUL, with a
#: well-formed keyed line **behind** it. Hand-built, because both porcelain routes
#: refuse a NUL outright (``error: a NUL byte in commit log message not allowed``,
#: measured 2026-09-03 on git 2.47.1 -- ``commit -F`` exit 128, ``commit-tree``
#: exit 1), while ``hash-object --literally`` writes it verbatim.
_NUL_IN_MESSAGE: Final = (
    b"chore: a hand-built commit with a NUL \x00 byte\n\n"
    b"Review-Finding: adversarial CRITICAL \xe2\x80\x94 behind the NUL, never read\n"
)


def test_a_nul_in_a_commit_object_truncates_the_message_git_emits(tmp_path: Path) -> None:
    """D4's framing survives an object-level NUL, and the cost is the recorded bound.

    **What the framing rests on, measured** (#496 R1-1). D4 justified NUL framing
    by "git rejects a NUL byte in a commit message"; that holds for porcelain and
    not for the object store, so this pins the ground that does hold for both:
    ``git log --format=%B`` **truncates** the message at the first NUL. The record
    partition therefore stays exact -- the adapter's own ``-z`` stream is still a
    multiple of :data:`_FIELDS_PER_RECORD` tokens -- and ``%H``/``%cI`` arrive
    untouched, so nothing an author writes into an object can reshape a record or
    forge a provenance anchor.

    **The cost is stated as the bound, not discovered as a bug.** The keyed line
    sits behind the NUL, so git never emits it and it reaches neither tuple: not
    accepted, and not rejected either. That is
    :class:`~theurian.domain.review_finding.FindingLoad`'s **third** population
    bound -- the load's population is what git emits, not what the object store
    holds -- and ADR-0029 D4 records why it is bounded rather than detected by a
    second ``cat-file`` read of every commit (porcelain cannot write such a commit,
    and a ``receive.fsckObjects`` origin refuses to take one). This test is what
    that bound is pinned by: if a future git stopped truncating, the framing
    assertion below is what would redden.

    The plant is byte-verified at the object store first, because the whole
    measurement is about the difference between what git *stores* and what it
    *emits* -- a plant that lost the NUL on the way in would make every assertion
    below vacuous.
    """
    _origin, clone = _origin_and_clone(tmp_path)
    _commit(
        clone,
        "fix: valid one (#1)",
        "Review-Finding: security HIGH — first valid",
        when="2026-01-01T00:00:00",
    )
    nul_sha = _commit_with_raw_message(clone, _NUL_IN_MESSAGE)
    _commit(
        clone,
        "fix: valid two (#2)",
        "Review-Finding: adversarial LOW — second valid",
        when="2026-03-01T00:00:00",
    )
    _publish(clone)

    # The premise, at the object store: git kept the NUL and every byte behind it.
    stored_object = _git_bytes(clone, "cat-file", "commit", nul_sha)
    assert stored_object.endswith(_NUL_IN_MESSAGE), "the plant lost its NUL on the way in"
    assert b"behind the NUL, never read" in stored_object

    source = GitTrailerFindingSource(clone)
    stdout = source._git_log()
    load = source.load_findings()

    # 1. The framing holds: the emitted stream still partitions into whole records.
    assert len(stdout.split(_NUL)) % _FIELDS_PER_RECORD == 0, (
        "an object-level NUL reshaped the record partition"
    )
    # 2. What git emits for that commit is the message truncated at the NUL.
    head, _sep, tail = _NUL_IN_MESSAGE.partition(_NUL)
    assert _stored_message_of(clone, nul_sha) == head
    assert tail, "the plant must have bytes behind the NUL for the truncation to bite"
    # 3. Its metadata is intact, so the record is still anchored and ordered.
    (record,) = [r for r in _split_records(stdout, clone) if r.sha == nul_sha]
    assert re.fullmatch(r"[0-9a-f]{40}", record.sha)
    assert record.committed_at == datetime(2026, 2, 1, tzinfo=UTC)
    assert record.message == head.decode("utf-8")
    # 4. The bound itself: the line behind the NUL is in NEITHER tuple, and the
    #    siblings around it load untouched.
    assert [f.finding_text for f in load.accepted] == ["first valid", "second valid"]
    assert "behind the NUL, never read" not in [f.finding_text for f in load.accepted]
    assert load.rejected == (), (
        "the truncated tail is outside the load's population (FindingLoad bound 3), "
        "so it is not accounted as a rejection either"
    )


# --- D3: a malformed keyed line is rejected, not fatal ----------------------


def test_a_keyed_but_malformed_trailer_is_rejected_not_raised(tmp_path: Path) -> None:
    """A keyed line failing the grammar is captured as rejected, not fatal (D3).

    The loss-free guarantee (AC-1) forbids silently skipping a keyed line; D3 also
    forbids aborting the whole load on one, since the corpus is append-only. So a
    malformed line lands in ``rejected`` while a well-formed sibling still loads.
    """
    _origin, clone = _origin_and_clone(tmp_path)
    _commit(clone, "fix: good (#2)", "Review-Finding: security HIGH — a valid finding")
    _commit(clone, "fix: bad trailer (#3)", "Review-Finding: reviewer-x HIGH — text")
    _publish(clone)

    load = GitTrailerFindingSource(clone).load_findings()
    assert [f.finding_text for f in load.accepted] == ["a valid finding"]
    assert len(load.rejected) == 1
    assert load.rejected[0].raw_line == "Review-Finding: reviewer-x HIGH — text"
    assert "reviewer-x" in load.rejected[0].reason


def test_a_far_future_committer_date_is_rejected_not_fatal(tmp_path: Path) -> None:
    """A committer date git emits beyond ``datetime``'s range rejects the record, never aborts.

    git emits a committer date of year >= 10000 for a crafted ``GIT_COMMITTER_DATE``
    (``@253402387200`` -> ``10000-01-02T00:00:00Z``), which ``datetime.fromisoformat``
    cannot parse. That parse runs for *every* record before any trailer is read, so
    an uncaught ``ValueError`` there would brick the entire corpus -- even a
    trailer-less commit does it -- falsifying D3's "never a fatal abort." The
    far-future record must be accounted as rejected (its ``%cI`` the offending
    value) while every valid finding still loads. The date is *not* clamped or
    sentinelled, because it is the total-order sort key and a published field.
    """
    _origin, clone = _origin_and_clone(tmp_path)
    _commit(
        clone,
        "fix: valid one (#1)",
        "Review-Finding: security HIGH — first valid",
        when="2026-01-01T00:00:00",
    )
    # A trailer-less commit whose committer date git emits as year 10000. Even with
    # no trailer, the record's date parse alone would abort the load before the fix.
    _commit_split_date(
        clone,
        "chore: a far-future commit with no trailer",
        author_when="2026-02-01T00:00:00+00:00",
        committer_when="@253402387200 +0000",
    )
    _commit(
        clone,
        "fix: valid two (#2)",
        "Review-Finding: adversarial LOW — second valid",
        when="2026-03-01T00:00:00",
    )
    _publish(clone)

    load = GitTrailerFindingSource(clone).load_findings()  # must not raise

    assert [f.finding_text for f in load.accepted] == ["first valid", "second valid"]
    # The far-future record is accounted, not silently dropped and not fatal.
    assert len(load.rejected) == 1
    rejected = load.rejected[0]
    assert "10000" in rejected.reason
    assert "committer date" in rejected.reason
    assert re.fullmatch(r"[0-9a-f]{40}", rejected.commit_sha)  # sha is git's own %H (D4)


def test_a_far_future_record_with_a_trailer_skips_it_but_keeps_siblings(tmp_path: Path) -> None:
    """A crafted-date record's own trailer is skipped, and its valid siblings load.

    A record whose committer date is unrepresentable cannot carry a valid finding
    date, so its trailer is skipped rather than accepted with a fabricated date --
    but that single crafted record must not cost the corpus its well-formed
    siblings. Exactly one record-level rejection is recorded for it.
    """
    _origin, clone = _origin_and_clone(tmp_path)
    _commit(
        clone,
        "fix: valid (#1)",
        "Review-Finding: security HIGH — a valid finding",
        when="2026-01-01T00:00:00",
    )
    _commit_split_date(
        clone,
        "fix: far-future with a trailer (#2)",
        "Review-Finding: adversarial CRITICAL — must not be accepted with a fake date",
        author_when="2026-02-01T00:00:00+00:00",
        committer_when="@253402387200 +0000",
    )
    _publish(clone)

    load = GitTrailerFindingSource(clone).load_findings()

    assert [f.finding_text for f in load.accepted] == ["a valid finding"]
    assert "must not be accepted" not in [f.finding_text for f in load.accepted]
    assert len(load.rejected) == 1
    assert "10000" in load.rejected[0].reason


def test_a_quoted_grammar_example_is_rejected_and_siblings_still_load(tmp_path: Path) -> None:
    """D3: a quoted grammar example cannot brick the corpus; it is one rejected line.

    ADR-0029's own grammar sentence ``Review-Finding: <reviewer> <SEVERITY> —
    <one-line finding>`` is a column-0 keyed line whose value fails the grammar. It
    must be captured as a :class:`RejectedTrailer` -- with its commit sha, raw line
    and reason -- while every well-formed sibling in the same corpus still loads,
    and the load must not raise. The accounting is loss-free: accepted plus
    rejected equals the keyed-line count.
    """
    _origin, clone = _origin_and_clone(tmp_path)
    valid_one = _commit(
        clone,
        "fix: a (#1)",
        "Review-Finding: adversarial HIGH — first valid",
        when="2026-01-01T00:00:00",
    )
    example = "Review-Finding: <reviewer> <SEVERITY> — <one-line finding>"
    example_sha = _commit(
        clone, "docs: quote the grammar (#2)", example, when="2026-02-01T00:00:00"
    )
    _commit(
        clone,
        "fix: b (#3)",
        "Review-Finding: security LOW — second valid",
        when="2026-03-01T00:00:00",
    )
    _publish(clone)

    load = GitTrailerFindingSource(clone).load_findings()

    assert [f.finding_text for f in load.accepted] == ["first valid", "second valid"]
    assert len(load.rejected) == 1
    rejected = load.rejected[0]
    assert rejected.commit_sha == example_sha
    assert rejected.raw_line == example
    assert rejected.reason  # a non-empty reason locates the failure
    # Loss-free accounting: three keyed lines, two accepted, one rejected, none lost.
    assert len(load.accepted) + len(load.rejected) == 3
    assert valid_one != example_sha  # the valid commit is a distinct object


# --- D3 / #496: a non-UTF-8 commit message is contained, not fatal ----------
#
# git validates nothing about a commit message, so a public commit can carry bytes
# that are not UTF-8. Decoding the whole `git log` stdout in one call made any such
# commit raise, so one of them anywhere on history took every well-formed sibling
# with it and answered with a `git fetch` remedy that could not help -- the D3
# "one commit bricks the entire corpus with no forward fix" abort. The stream is
# framed before it is decoded now, and the message is contained per record.

#: The plant: a hand-built commit message carrying a lone ``0x80`` **and** a
#: well-formed trailer. Both halves matter -- the raw byte is what git stores
#: verbatim and Python refuses to decode, and the trailer is what must NOT be
#: accepted out of a message whose bytes could not be read.
_UNDECODABLE_MESSAGE: Final = (
    b"chore: a hand-built commit with a raw \x80 byte\n\n"
    b"Review-Finding: adversarial CRITICAL \xe2\x80\x94 unreadable, never accepted\n"
)


def _corpus_with_an_undecodable_commit(tmp_path: Path) -> tuple[Path, str]:
    """A published clone whose middle commit's message is not valid UTF-8.

    Two well-formed trailer commits around one hand-built commit, so the assertions
    can say both that the corpus survived and that the contained record is
    accounted. Returns the clone and the offending commit's sha.
    """
    _origin, clone = _origin_and_clone(tmp_path)
    _commit(
        clone,
        "fix: valid one (#1)",
        "Review-Finding: security HIGH — first valid",
        when="2026-01-01T00:00:00",
    )
    undecodable = _commit_with_raw_message(clone, _UNDECODABLE_MESSAGE)
    _commit(
        clone,
        "fix: valid two (#2)",
        "Review-Finding: adversarial LOW — second valid",
        when="2026-03-01T00:00:00",
    )
    _publish(clone)
    return clone, undecodable


def test_a_non_utf8_commit_message_is_one_rejection_and_its_siblings_still_load(
    tmp_path: Path,
) -> None:
    """#496: a message git stored as non-UTF-8 bytes costs its record, not the corpus.

    The whole ``git log`` stdout used to be decoded in one call, so this commit
    aborted the load before a single trailer was read -- including the two
    well-formed ones beside it, which is the D3-forbidden shape: a commit that
    cannot be edited (history is signed and append-only) permanently bricking the
    corpus with no forward fix. Both valid findings must land, and the offending
    record must be accounted exactly once.

    Its own keyed line must **not** be accepted. A message whose bytes could not be
    decoded has no candidate lines at all, so the trailer sitting inside it is
    skipped with the record rather than parsed out of a partial decode -- the same
    rule the unrepresentable-date sibling above follows.

    The fixture's premise is asserted first and is not decoration: measured
    2026-09-03 on git 2.47.1, ``git commit -F`` and ``git commit-tree`` both
    re-encode a non-UTF-8 message to valid UTF-8, so a plant built with either
    would leave this test passing over a corpus with nothing wrong in it.
    """
    clone, undecodable = _corpus_with_an_undecodable_commit(tmp_path)
    stored = _stored_message_of(clone, undecodable)
    with pytest.raises(UnicodeDecodeError):  # the premise: git kept the raw byte
        stored.decode("utf-8")

    load = GitTrailerFindingSource(clone).load_findings()  # must not raise

    assert [f.finding_text for f in load.accepted] == ["first valid", "second valid"]
    assert "unreadable, never accepted" not in [f.finding_text for f in load.accepted]
    assert len(load.rejected) == 1
    rejected = load.rejected[0]
    assert rejected.commit_sha == undecodable  # git's own %H (D4), never forgeable
    assert "not valid UTF-8" in rejected.reason
    assert "0x80" in rejected.reason  # the offending byte is named, so it locates
    assert "�" in rejected.raw_line  # replacement-decoded, never the raw byte


def test_a_non_utf8_commit_message_is_not_reported_as_unreachable_history(
    tmp_path: Path,
) -> None:
    """#496: the load must not raise the fetch-remedy error for a readable history.

    The pre-fix failure was not only fatal, it was *misdescribed*:
    :class:`GitHistoryUnavailableError` says the history could not be reached and
    its remedy names ``git fetch origin main``, which cannot help when the ref
    resolves, the objects are local, and one message merely holds a byte Python
    will not decode. So this asserts the specific error's absence, not just that
    some load succeeded -- a fix that swapped the exception type for another fatal
    one would still brick the corpus and would still pass a bare "does not raise
    GitHistoryUnavailableError" check on a corpus with no findings in it. The
    accepted count is asserted too, so the corpus has to have actually loaded.
    """
    clone, _undecodable = _corpus_with_an_undecodable_commit(tmp_path)

    try:
        load = GitTrailerFindingSource(clone).load_findings()
    except GitHistoryUnavailableError as exc:  # pragma: no cover - the pre-#496 behaviour
        pytest.fail(
            f"one non-UTF-8 commit message bricked the whole corpus with advice that "
            f"cannot work: {exc} / remedy: {exc.remedy}"
        )

    assert len(load.accepted) == 2


def test_an_undecodable_message_excerpt_is_bounded_and_replacement_decoded(
    tmp_path: Path,
) -> None:
    """#496: an unbounded message cannot inflate the row that reports it.

    A commit message is unbounded author-controlled input, and the rejection is
    written to the store and counted in an operator's build report, so the excerpt
    that locates the failure is capped rather than copied. The plant is a
    ~8 KB message whose first 100 bytes are ordinary ASCII and whose tail is
    thousands of raw ``0x80`` bytes.

    Two assertions that are not the same one twice. The **bound** is written as a
    hard number the plant is far larger than, so it fires however the cap constant
    is spelled -- an assertion phrased only against :data:`_UNDECODABLE_EXCERPT_BYTES`
    would be satisfied by a cap of a megabyte. The **value** is then hand-written
    against the recorded cap -- the ASCII head, then replacement characters to 120
    -- rather than re-derived with the adapter's own slice-and-decode, which is why
    the cap itself is pinned first: it is a recorded decision, and moving it must
    redden here rather than quietly re-baseline the expectation.
    """
    _origin, clone = _origin_and_clone(tmp_path)
    _commit(clone, "fix: valid (#1)", "Review-Finding: security HIGH — a valid finding")
    head = b"chore: a huge message whose tail is undecodable" + b"x" * 53
    huge = _commit_with_raw_message(clone, head + b"\x80" * 8000)
    _publish(clone)
    with pytest.raises(UnicodeDecodeError):  # the premise: git kept the raw bytes
        _stored_message_of(clone, huge).decode("utf-8")

    load = GitTrailerFindingSource(clone).load_findings()

    assert [f.finding_text for f in load.accepted] == ["a valid finding"]
    (rejected,) = load.rejected
    assert rejected.commit_sha == huge
    assert len(rejected.raw_line) <= 1024, (
        f"a {len(head) + 8000}-byte commit message reached the rejection row "
        f"unbounded ({len(rejected.raw_line)} characters)"
    )
    assert _UNDECODABLE_EXCERPT_BYTES == 100 + 20, (
        "the recorded excerpt cap moved; the hand-written expectation below is "
        "written against 120 bytes and has to move with it"
    )
    assert rejected.raw_line == head.decode("ascii") + "�" * 20


def test_split_records_marks_an_undecodable_message_and_still_decodes_the_metadata(
    tmp_path: Path,
) -> None:
    """The boundary where the containment is set, beside its year-10000 sibling.

    ``_split_records`` does not raise on a message it cannot decode: it marks the
    message unreadable and carries the bounded excerpt and the reason that
    ``load_findings`` accounts as one rejection. The record's *metadata* still
    decodes -- that is the whole reason the split happens before the decode -- so
    the rejection can name the commit it is on.
    """
    stream = (
        b"a" * 40
        + _NUL
        + b"2026-01-01T00:00:00+00:00"
        + _NUL
        + b"Review-Finding: security HIGH \x80 not readable"
    )

    (record,) = _split_records(stream, tmp_path)

    assert record.message is None
    assert record.sha == "a" * 40  # metadata decoded, so the rejection has a commit
    assert record.date_iso == "2026-01-01T00:00:00+00:00"
    assert record.committed_at == datetime(2026, 1, 1, tzinfo=UTC)
    assert "�" in record.undecodable_excerpt
    assert "not valid UTF-8" in record.undecodable_reason


def test_a_record_failing_both_the_decode_and_the_date_is_accounted_exactly_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two record-level containments on one record are still one rejection.

    The accounting is what must not double-count: a record is skipped once,
    whichever reason skipped it. The decode is asked first, so the reason names it
    -- a message that could not be decoded has no candidate lines at all, where an
    unrepresentable date only says which record they were on.

    Driven at the subprocess seam because git will not emit both faults on one
    commit: a year-10000 committer date needs a crafted ``GIT_COMMITTER_DATE``,
    while the undecodable message needs a hand-built object that would carry its
    own stamp.
    """
    stream = (
        b"a" * 40
        + _NUL
        + b"10000-01-02T00:00:00Z"  # unrepresentable as well
        + _NUL
        + b"chore: \x80 and a far-future date"
    )
    source = GitTrailerFindingSource(tmp_path)
    monkeypatch.setattr(GitTrailerFindingSource, "_git_log", lambda _self: stream)

    load = source.load_findings()  # must not raise

    assert load.accepted == ()
    assert len(load.rejected) == 1
    assert "not valid UTF-8" in load.rejected[0].reason
    # The decode is the reason given, not the date. Keyed on the date rejection's
    # own phrase rather than on "10000": the excerpt cap's byte count is spelled in
    # this same reason, so a digit key collides with it (measured -- raising the cap
    # to 1000000 in a perturbation run made a "10000 not in reason" assertion fail
    # for a reason that had nothing to do with the date).
    assert "committer date" not in load.rejected[0].reason


#: The two git-generated metadata fields, each planted as bytes that cannot be
#: UTF-8. An author reaches neither -- ``%H`` is 40 hex characters and ``%cI`` an
#: ISO-8601 instant -- so bytes that fail to decode there mean the *stream* is
#: wrong, and the fatal framing error is the honest answer rather than a per-record
#: containment (a record whose own sha is unreadable cannot be accounted at all).
_UNDECODABLE_METADATA_STREAMS: tuple[tuple[str, bytes], ...] = (
    (
        "commit sha (%H)",
        b"\x80" * 40 + _NUL + b"2026-01-01T00:00:00+00:00" + _NUL + b"Review-Finding: x",
    ),
    (
        "committer date (%cI)",
        b"a" * 40 + _NUL + b"2026-01-01T00:00:00\x80" + _NUL + b"Review-Finding: x",
    ),
)


@pytest.mark.parametrize("field, stream", _UNDECODABLE_METADATA_STREAMS)
def test_an_undecodable_metadata_field_stays_fatal_with_the_framing_remedy(
    field: str, stream: bytes, tmp_path: Path
) -> None:
    """#496's bound: the containment covers the message, and *only* the message.

    The stream partitions into exactly one whole record here -- so the field-count
    guard does not fire -- and the failure is the metadata decode itself. It must
    stay fatal: these bytes are git's, not an author's, so they cannot be a
    per-record containment the way a message can, and the remedy that fits is the
    one naming the installed git version, never the ``git fetch`` of
    :class:`GitHistoryUnavailableError`.
    """
    assert len(stream.split(_NUL)) == 3, "the premise: a whole record, so only the decode fails"

    with pytest.raises(GitOutputFramingError) as caught:
        _split_records(stream, tmp_path)

    assert field in str(caught.value)  # the reason names which field, not just 'a field'
    assert "not valid UTF-8" in str(caught.value)
    assert "git version" in caught.value.remedy
    assert "fetch" not in caught.value.remedy


def test_an_undecodable_metadata_field_is_fatal_through_the_whole_load(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The same fatality, through ``load_findings`` rather than at the split.

    Pinned end-to-end as well as at the boundary: a later change that caught the
    framing error inside the load and returned a partial :class:`FindingLoad` would
    leave the parametrized pin above green while publishing a corpus silently
    missing every record after the broken one.
    """
    _field, stream = _UNDECODABLE_METADATA_STREAMS[0]
    source = GitTrailerFindingSource(tmp_path)
    monkeypatch.setattr(GitTrailerFindingSource, "_git_log", lambda _self: stream)

    with pytest.raises(GitOutputFramingError):
        source.load_findings()


# --- AC-1: the read is the whole message, so a folded trailer is accounted ---


def test_a_trailer_folded_into_the_subject_paragraph_is_accounted(tmp_path: Path) -> None:
    """A column-0 keyed line in the subject *paragraph* is read, not silently dropped (#410).

    git's ``%b`` excludes the first *paragraph*, not the first line: in a message
    whose subject is not followed by a blank line, every following line folds into
    the subject and ``%b`` is empty. A trailer there appeared in neither tuple --
    unaccounted, falsifying the loss-free invariant :class:`FindingLoad` publishes.
    The adapter reads ``%B`` (the whole message), so the folded trailer is a
    column-0 keyed line like any other.

    The ``%b``/``%B`` contrast is asserted first, so the fixture cannot quietly stop
    exercising the fold: without that control this test would still pass against a
    message git happened to split into subject and body normally.
    """
    _origin, clone = _origin_and_clone(tmp_path)
    folded = _commit_body_file(
        clone,
        # No blank line after the subject: the trailer folds into the subject
        # paragraph, which is exactly what `%b` drops.
        "fix: a subject with no blank line after it\n"
        "Review-Finding: security HIGH — folded into the subject paragraph\n".encode(),
        when="2026-01-01T00:00:00",
    )
    _commit(
        clone,
        "fix: an ordinary commit (#2)",
        "Review-Finding: security LOW — an ordinary body",
        # Dated after the folded commit, so the expected sequence below is the
        # chronological total order rather than a sha tie-break.
        when="2026-02-01T00:00:00",
    )
    _publish(clone)

    # The fixture's own premise: `%b` really does lose this line, and `%B` keeps it.
    body_only = _git(clone, "log", "refs/remotes/origin/main", "--format=%b")
    whole = _git(clone, "log", "refs/remotes/origin/main", "--format=%B")
    assert "folded into the subject paragraph" not in body_only
    assert "folded into the subject paragraph" in whole

    load = GitTrailerFindingSource(clone).load_findings()

    assert [f.finding_text for f in load.accepted] == [
        "folded into the subject paragraph",
        "an ordinary body",
    ]
    assert load.rejected == ()
    assert load.accepted[0].commit_sha == folded


def test_a_subject_that_is_itself_a_keyed_line_is_a_finding(tmp_path: Path) -> None:
    """The population is every column-0 keyed line in the *message*, subject included (#410).

    Reading ``%B`` makes the subject reachable, and a subject that is itself a
    column-0 keyed line is therefore a finding rather than an excluded special
    case. Pinned rather than left implicit: an "exclude the first line" refinement
    would re-open the accounting gap at a different offset -- the folded shape above
    is exactly a keyed line that a first-line rule would have to keep, and no rule
    that counts lines can tell the two apart.
    """
    _origin, clone = _origin_and_clone(tmp_path)
    _commit_body_file(
        clone, b"Review-Finding: adversarial LOW \xe2\x80\x94 the subject line itself\n"
    )
    _publish(clone)

    load = GitTrailerFindingSource(clone).load_findings()

    assert [f.finding_text for f in load.accepted] == ["the subject line itself"]
    assert load.rejected == ()


def test_a_lone_cr_message_with_an_unkeyed_first_line_holds_no_trailer(tmp_path: Path) -> None:
    """A CR-separated message is one line; an UNKEYED first line holds no trailer (#410).

    The second input shape #410 names, and the *unkeyed-first-line* half of the CR
    bound. A body whose separators are lone ``CR`` bytes is a *single* LF-delimited
    line whose column 0 here is the subject, so under the population the load
    publishes -- column-0 keyed lines of the whole message, split on ``\\n`` --
    there is no trailer to lose. This is a bound on the claim, not a hole in it: the
    same rule is what the loss-free canary's own baseline greps, so both sides agree
    the count is zero, and no line is silently dropped from a population that ever
    contained it.

    This test **cannot** exhibit the keyed-first-line counterexample -- its fixture
    opens with a subject, so its first line is never a candidate. The sibling below
    covers the case where the first line *is* keyed (#404 R1-4), which is why the
    old blanket "a CR message carries no keyed line at all" was false.

    Asserted here so a later "helpfully" CR-aware split cannot land without a
    recorded decision -- it would widen the accepted set while the population the
    invariant is stated over stayed LF-delimited.
    """
    _origin, clone = _origin_and_clone(tmp_path)
    _commit_body_file(
        clone,
        "fix: lone carriage returns\r\rReview-Finding: security HIGH — behind lone CRs\r".encode(),
        when="2026-01-01T00:00:00",
    )
    _commit(
        clone,
        "fix: a normal one (#2)",
        "Review-Finding: security LOW — a normal body",
        when="2026-02-01T00:00:00",
    )
    _publish(clone)

    whole = _git(clone, "log", "refs/remotes/origin/main", "--format=%B")
    keyed = [line for line in whole.split("\n") if line.startswith(TRAILER_KEY)]
    assert [text for text in keyed if "behind lone CRs" in text] == []  # not a column-0 line

    load = GitTrailerFindingSource(clone).load_findings()

    assert [f.finding_text for f in load.accepted] == ["a normal body"]
    assert load.rejected == ()
    assert len(load.accepted) + len(load.rejected) == len(keyed)


def test_a_keyed_first_line_after_a_lone_cr_swallows_the_remainder(tmp_path: Path) -> None:
    """#404 R1-4: a keyed FIRST line in a CR-separated message is one finding, not none.

    The counterexample the old bound denied. A CR-separated message is one
    ``\\n``-delimited line, so *at most its first line* is a candidate -- but when
    that first line is keyed it IS a candidate, and a trailer value is exactly one
    physical line (D2), so the CR-joined remainder (a second trailer, a sign-off)
    becomes that one finding's opaque, byte-preserved text rather than further
    findings. The blanket claim "a CR message carries no column-0 keyed line at
    all" was therefore false: it holds only when the first line is unkeyed.

    Pinned as the true behaviour, not a defect to change: the finding is
    well-formed, its text is byte-preserved (the ``\\r`` bytes survive), and the
    accounting still balances -- exactly one keyed line, one accepted finding.
    """
    _origin, clone = _origin_and_clone(tmp_path)
    _commit_body_file(
        clone,
        (
            "Review-Finding: security HIGH — the first finding\r"
            "Review-Finding: adversarial LOW — swallowed, not its own finding\r"
            "Signed-off-by: Tester <tester@example.com>"
        ).encode(),
        when="2026-01-01T00:00:00",
    )
    _publish(clone)

    whole = _git(clone, "log", "refs/remotes/origin/main", "--format=%B")
    keyed = [line for line in whole.split("\n") if line.startswith(TRAILER_KEY)]
    assert len(keyed) == 1, "the CR-separated message must be exactly one column-0 keyed line"

    load = GitTrailerFindingSource(clone).load_findings()

    # Exactly one finding: the second trailer is swallowed into the first's text.
    assert [(f.reviewer, f.severity) for f in load.accepted] == [
        (ReviewerToken.SECURITY, FindingSeverity.HIGH)
    ]
    assert load.rejected == ()
    swallowed = load.accepted[0].finding_text
    assert swallowed.startswith("the first finding\r")  # the CR bytes are byte-preserved (D2)
    assert "Review-Finding: adversarial LOW — swallowed, not its own finding" in swallowed
    assert "Signed-off-by: Tester" in swallowed
    # The accounting balances: one keyed line in, one finding out, none lost.
    assert len(load.accepted) + len(load.rejected) == len(keyed)


# --- D2: a trailer value is exactly one physical line -----------------------


def test_an_indented_continuation_line_is_body_text_not_folded(tmp_path: Path) -> None:
    """D2: a wrapped continuation is body text, never folded into the finding.

    A trailer value is exactly one physical line. An indented continuation line
    beneath a trailer does not begin with the key, so it is ordinary body text and
    must not be appended to ``finding_text``. Pinned with a hand-written expected
    value so a future "helpful" fold cannot silently change the recorded decision.
    """
    _origin, clone = _origin_and_clone(tmp_path)
    _commit(
        clone,
        "fix: wrapped (#1)",
        "Review-Finding: security HIGH — the first physical line only",
        "    a wrapped continuation that must not be folded in",
    )
    _publish(clone)

    load = GitTrailerFindingSource(clone).load_findings()
    assert [f.finding_text for f in load.accepted] == ["the first physical line only"]


# --- D5: pull_request is derived None, never guessed from the subject -------


def test_pull_request_is_none_even_when_the_subject_ends_in_a_ref(tmp_path: Path) -> None:
    """D5: a trailing ``(#N)`` on the subject must not resurrect a PR heuristic.

    The subject is not read at all in this slice, and the deleted heuristic guessed
    the PR from its trailing ``(#N)`` -- wrong 49.1% of the time on real history,
    where the token is often the *issue* closed. Every accepted finding's
    ``pull_request`` is ``None`` regardless of the subject.
    """
    _origin, clone = _origin_and_clone(tmp_path)
    _commit(clone, "docs: add ADR-0029 (#368)", "Review-Finding: code-review MEDIUM — a finding")
    _commit(clone, "fix: no ref in subject", "Review-Finding: security HIGH — another finding")
    _publish(clone)

    load = GitTrailerFindingSource(clone).load_findings()
    assert len(load.accepted) == 2
    for finding in load.accepted:
        assert finding.pull_request is None


# --- AC-4 / source-uri: each record anchors to its own commit ---------------


def test_each_finding_anchors_to_its_commit(tmp_path: Path) -> None:
    """AC-4: the anchor's provider is git and its sha/source_uri is *this* commit.

    ``source_uri`` is asserted equal to the commit sha, not to any constant, so a
    mutation pinning it to a literal is caught rather than passing on a field that
    merely exists.
    """
    _origin, clone = _origin_and_clone(tmp_path)
    sha = _commit(clone, "fix: a change (#7)", "Review-Finding: adversarial MEDIUM — a finding")
    _publish(clone)

    (finding,) = GitTrailerFindingSource(clone).load_findings().accepted
    assert finding.anchor.provider == "git"
    assert finding.provider == "git"
    assert finding.anchor.commit_sha == sha
    assert finding.commit_sha == sha
    assert finding.anchor.source_uri == sha  # source_uri is the sha, not a constant
    assert finding.pull_request is None  # derived None in this slice (D5)


# --- AC-6: a total, stable order; two runs are byte-identical ---------------


def test_two_runs_produce_a_byte_identical_sequence(tmp_path: Path) -> None:
    """Determinism, and a total order that is not merely git's emission order.

    The commits are made oldest-to-newest and git emits newest-first, so an
    adapter that returned git's order would produce ``[newer, older]``. The total
    sort key orders oldest-first, which is what this asserts -- so dropping the
    sort is a killable mutation, not a no-op against already-deterministic output.
    """
    _origin, clone = _origin_and_clone(tmp_path)
    older = _commit(
        clone, "fix: older (#1)", "Review-Finding: security LOW — older", when="2026-01-01T00:00:00"
    )
    newer = _commit(
        clone,
        "fix: newer (#2)",
        "Review-Finding: security HIGH — newer",
        when="2026-06-01T00:00:00",
    )
    _publish(clone)

    source = GitTrailerFindingSource(clone)
    first = source.load_findings()
    second = source.load_findings()
    assert first == second  # byte-identical result across runs
    assert [f.commit_sha for f in first.accepted] == [older, newer]  # oldest-first total order
    assert [f.finding_text for f in first.accepted] == ["older", "newer"]


def test_multiple_trailers_on_one_commit_all_map_in_body_order(tmp_path: Path) -> None:
    """Loss-free within a commit: N trailers on one commit map to N records.

    Their positions within the commit break the (date, sha) tie, so the order is
    the order they were authored -- a co-located-trailer commit is the standard
    shape here (17 on one commit in real history), not an edge case.
    """
    _origin, clone = _origin_and_clone(tmp_path)
    sha = _commit(
        clone,
        "fix: a cluster (#9)",
        "Review-Finding: code-review HIGH — first",
        "Review-Finding: security MEDIUM — second",
        "Review-Finding: adversarial LOW — third",
    )
    _publish(clone)

    findings = GitTrailerFindingSource(clone).load_findings().accepted
    assert [f.finding_text for f in findings] == ["first", "second", "third"]
    assert {f.commit_sha for f in findings} == {sha}


# --- Group 4: body reassembly and the committer date ------------------------


def test_a_trailer_below_many_body_lines_is_fully_read(tmp_path: Path) -> None:
    """A multi-line body's trailers are all read, wherever they sit in the body.

    ``git log %B`` returns the whole message as one NUL-framed field, so a trailer
    after several paragraphs -- and a second one before them -- are both column-0
    keyed lines. A mutation that truncated the message (an earlier ``fields[3:]``
    join, or reading only the first line) would drop the later trailer; this
    asserts both survive with their exact text.
    """
    _origin, clone = _origin_and_clone(tmp_path)
    _commit(
        clone,
        "fix: a long body (#1)",
        "Review-Finding: adversarial HIGH — near the top of the body",
        "",
        "a middle paragraph of ordinary prose that is not a trailer",
        "",
        "another paragraph, still not a trailer",
        "",
        "Review-Finding: security LOW — far below many body lines",
    )
    _publish(clone)

    load = GitTrailerFindingSource(clone).load_findings()
    assert [f.finding_text for f in load.accepted] == [
        "near the top of the body",
        "far below many body lines",
    ]


def test_mixed_offset_committer_dates_normalise_to_utc_in_chronological_order(
    tmp_path: Path,
) -> None:
    """#405: the parsed date is a UTC instant, and the accepted order is unchanged.

    Two halves, and only the first is new behaviour. **The date is normalised**:
    ``%cI`` carries the committer's own offset, and the store writes what it is
    handed as TEXT, where mixed offsets do not sort chronologically -- so the source
    hands over an instant, not a spelling. **The order is not touched**: aware
    datetimes already compared as instants, so the accepted sequence here is exactly
    what the offset-preserving parse produced, which is what this asserts against a
    hand-written expectation rather than against a re-run of the old code.

    The fixture is the inversion #405 measured: the ``+14:00`` commit is EARLIER in
    real time than the ``-11:00`` one, while its raw ISO text sorts after it. The
    raw-text control below is what keeps that premise honest.
    """
    _origin, clone = _origin_and_clone(tmp_path)
    _commit(
        clone,
        "fix: far east (#1)",
        "Review-Finding: security HIGH — earlier, written +14:00",
        when="2026-01-02T01:00:00+14:00",  # instant 2026-01-01T11:00:00Z
    )
    _commit(
        clone,
        "fix: far west (#2)",
        "Review-Finding: security LOW — later, written -11:00",
        when="2026-01-01T12:00:00-11:00",  # instant 2026-01-01T23:00:00Z
    )
    _publish(clone)

    # The fixture's premise: raw `%cI` text really does invert these two.
    raw_dates = _git(clone, "log", "refs/remotes/origin/main", "--format=%cI").split()
    assert sorted(raw_dates) == ["2026-01-01T12:00:00-11:00", "2026-01-02T01:00:00+14:00"]

    load = GitTrailerFindingSource(clone).load_findings()

    assert [f.finding_text for f in load.accepted] == [
        "earlier, written +14:00",
        "later, written -11:00",
    ]
    assert [f.date for f in load.accepted] == [
        datetime(2026, 1, 1, 11, 0, tzinfo=UTC),
        datetime(2026, 1, 1, 23, 0, tzinfo=UTC),
    ]
    # Normalised, not merely equal as instants: the offset the committer wrote is
    # gone from the value the store will record.
    assert all(f.date.utcoffset() == timedelta(0) for f in load.accepted)


def test_the_finding_date_is_the_committer_date_not_the_author_date(tmp_path: Path) -> None:
    """The record carries the committer date (``%cI``), not the author date.

    A validity window keyed to the wrong timestamp silently shifts when a finding
    is considered current. With author and committer dates deliberately far apart,
    the record must equal the committer date -- so the ``%cI`` -> ``%aI`` mutation
    is caught.
    """
    _origin, clone = _origin_and_clone(tmp_path)
    _commit_split_date(
        clone,
        "fix: split dates (#1)",
        "Review-Finding: security HIGH — dated",
        author_when="2020-02-02T02:02:02+00:00",
        committer_when="2026-06-15T00:00:00+00:00",
    )
    _publish(clone)

    (finding,) = GitTrailerFindingSource(clone).load_findings().accepted
    assert finding.date == datetime(2026, 6, 15, tzinfo=UTC)
    assert finding.date != datetime(2020, 2, 2, 2, 2, 2, tzinfo=UTC)


# --- no silent drop, and an unreachable ref is an error --------------------


def test_a_missing_public_ref_raises(tmp_path: Path) -> None:
    """A repo with no origin/main is an error with a fetch remedy, not empty output."""
    repo = tmp_path / "repo"
    _git(tmp_path, "init", "-b", "main", str(repo))
    _git(repo, "config", "user.name", "Tester")
    _git(repo, "config", "user.email", "tester@example.com")
    _commit(repo, "chore: local only", "Review-Finding: security LOW — unreachable")

    with pytest.raises(GitHistoryUnavailableError) as caught:
        GitTrailerFindingSource(repo).load_findings()
    assert "fetch" in caught.value.remedy


# --- Group 1: a hermetic pin on the frozen corpus, and a live-canary property


def test_frozen_4c4a784_pins_the_parsed_corpus(tmp_path: Path) -> None:
    """A hermetic pin on an immutable ancestor of origin/main (measured 2026-08-26).

    Reads the *real* frozen commit :data:`FROZEN_SHA` through the production
    adapter -- its objects borrowed via a git ``alternates`` file, its
    ``refs/remotes/origin/main`` pointed at that immutable sha -- so it exercises
    real historical trailers while pinning a commit that cannot move. The count
    therefore does not rot as new ``Review-Finding`` commits land on the live tip.

    At 4c4a784 the corpus is exactly 55 keyed trailers across 7 commits, all
    well-formed, with a token distribution of 15 adversarial, 9 ``code`` and 21
    ``code-review`` (so 30 code-review after the ``code`` alias normalises) and 10
    security. If the ``code`` alias stopped normalising, the 9 ``code`` lines would
    be rejected and every assertion here would fail -- so this also guards the
    alias. Skips where the frozen objects are unreachable (a shallow clone, or
    mutate.py's copied tree with no ``.git``), keeping the mutation control GREEN.
    """
    objects = _real_object_store()
    if objects is None:
        pytest.skip("no git object store here (a non-repo tree, e.g. mutate.py's copy)")

    fixture = tmp_path / "frozen"
    _git(tmp_path, "init", "-b", "main", str(fixture))
    (fixture / ".git" / "objects" / "info" / "alternates").write_text(f"{objects}\n")
    if not _git_ok(fixture, "cat-file", "-e", f"{FROZEN_SHA}^{{commit}}"):
        pytest.skip("the frozen commit 4c4a784 is not present (a shallow clone)")
    _git(fixture, "update-ref", "refs/remotes/origin/main", FROZEN_SHA)

    load = GitTrailerFindingSource(fixture).load_findings()

    assert len(load.accepted) == 55
    assert load.rejected == ()
    assert len({f.commit_sha for f in load.accepted}) == 7
    assert Counter(f.reviewer for f in load.accepted) == {
        ReviewerToken.CODE_REVIEW: 30,  # 21 canonical + 9 normalised from the 'code' alias
        ReviewerToken.ADVERSARIAL: 15,
        ReviewerToken.SECURITY: 10,
    }
    for finding in load.accepted:
        assert finding.pull_request is None


def test_a_hand_authored_corpus_maps_to_exactly_the_authored_findings(tmp_path: Path) -> None:
    """The loss-free mapping checked against hand-authored expectations, not a re-derivation.

    The prior live oracle recomputed each finding text with the adapter's own
    algorithm (strip the key, split on the separator) and re-applied its own
    ``startswith`` filter, so it agreed with the code by construction and could not
    redden on an extraction defect. Here the expected records -- reviewer, severity
    and byte-exact text, in total order -- are written out by hand from trailers
    this test authored, including a ``code`` alias line and an embedded em-dash, so
    a change to how the adapter extracts a value makes the assertion fail.
    """
    _origin, clone = _origin_and_clone(tmp_path)
    _commit(
        clone,
        "fix: older (#1)",
        "Review-Finding: adversarial HIGH — a — b — c",
        "Review-Finding: code MEDIUM — alias normalises here",
        when="2026-01-01T00:00:00",
    )
    _commit(
        clone,
        "fix: newer (#2)",
        "Review-Finding: security CRITICAL — a finding, with a comma and (#64)",
        when="2026-02-01T00:00:00",
    )
    _publish(clone)

    load = GitTrailerFindingSource(clone).load_findings()

    assert [(f.reviewer, f.severity, f.finding_text) for f in load.accepted] == [
        (ReviewerToken.ADVERSARIAL, FindingSeverity.HIGH, "a — b — c"),
        (ReviewerToken.CODE_REVIEW, FindingSeverity.MEDIUM, "alias normalises here"),
        (ReviewerToken.SECURITY, FindingSeverity.CRITICAL, "a finding, with a comma and (#64)"),
    ]
    assert load.rejected == ()
    assert len(load.accepted) + len(load.rejected) == 3  # loss-free against a known count


def test_live_origin_main_accounts_for_every_trailer_loss_free() -> None:
    """AC-1 canary on the live tip: a *property*, not a count, so it cannot rot.

    Deliberately non-hermetic -- it reads whatever ``refs/remotes/origin/main``
    holds now, which is what makes it the canary ADR-0029 decision 2 wants. It
    asserts loss-free accounting (accepted plus rejected equals the number of
    column-0 keyed lines, none dropped), determinism across two calls, and that
    every accepted finding anchors to an immutable 40-hex sha with ``pull_request``
    still ``None`` in this slice. It hard-codes no number and depends on no
    particular tip.

    If a novel reviewer or severity spelling lands on public ``main`` that the
    parser does not yet cover, that line moves from ``accepted`` to ``rejected``;
    the accounting still balances, but the corpus is no longer all-accepted, which
    is the recorded grammar-change signal (the fix is to widen the parser, not to
    loosen this test). Skips where the checkout or its remote-tracking ref is
    unresolvable, keeping the mutation control GREEN.

    **The baseline greps ``%B``, not ``%b`` (#410).** While the adapter read ``%b``
    this baseline read it too, so the equation compared the defect with itself: a
    trailer folded into the subject paragraph was missing from both sides and the
    balance held while the line was lost. ``%B`` is the whole message, which is the
    population the invariant is actually stated over. Measured on ``origin/main`` @
    ``266e6b6`` (2026-09-02) the two counts agree at 386, so this repointing changed
    no live number -- it removed the shared blind spot ahead of the first folded
    trailer, not a present miscount.
    """
    repo = _live_repo_root()
    if repo is None or not _origin_main_present(repo):
        pytest.skip("refs/remotes/origin/main is not resolvable in this checkout")

    source = GitTrailerFindingSource(repo)
    load = source.load_findings()
    again = source.load_findings()

    assert load == again  # deterministic across two calls

    # The second of the two calls that address the *real* checkout, so it keeps the
    # developer's global config for the same `safe.directory` reason (`_child_env`).
    raw = _git(repo, "log", "refs/remotes/origin/main", "--format=%B", hermetic=False)
    keyed = [line for line in raw.split("\n") if line.startswith(TRAILER_KEY)]
    # Loss-free population accounting: every column-0 keyed line is accounted for
    # in exactly one tuple, none silently dropped, and there really are some.
    assert len(load.accepted) + len(load.rejected) == len(keyed) > 0

    for finding in load.accepted:
        assert re.fullmatch(r"[0-9a-f]{40}", finding.commit_sha)
        assert finding.provider == "git"
        assert finding.date.tzinfo is not None
        assert finding.pull_request is None
        assert finding.family is None
        assert finding.specialist is None


# --- _split_records directly: framing branches and metadata rejection --------
#
# `_split_records` is exercised end-to-end above, but its framing branches (the
# terminator-variant `pop`, the framing-error raise) never fire on this adapter's
# separator-semantics `format:` output, so they would survive their own deletion
# without these direct drivers. The metadata-rejection path (a year-10000 date) is
# also pinned here at the split boundary where the mark is set.


def _record_stream(*fields: str) -> bytes:
    """Join raw fields with the adapter's real NUL separator (separator semantics).

    A well-formed n-record stream is exactly ``3n`` fields joined by ``3n - 1``
    NULs -- git's ``--format=format:`` output, which separates rather than
    terminates records (no trailing NUL).

    **Bytes, because that is what the adapter frames** (#496): ``_split_records``
    partitions the raw stdout and decodes each field afterwards, so a stream
    injected at that seam is bytes too. The fields are written as ``str`` here and
    encoded, since every field a *well-formed* stream carries is text; the
    undecodable ones are built as literal bytes at their own call sites.
    """
    return _NUL.join(field.encode("utf-8") for field in fields)


def test_split_records_normal_separator_stream_yields_whole_records(tmp_path: Path) -> None:
    """A ``3n``-token separator stream (real ``format:`` output) yields n parsed records.

    Each record's committer date parses to an aware datetime, and an empty final
    body is a legitimate field under separator semantics -- not a dropped terminator.
    """
    stream = _record_stream(
        "a" * 40,
        "2026-01-01T00:00:00+00:00",
        "Review-Finding: security HIGH — one",
        "b" * 40,
        "2026-02-01T00:00:00+00:00",
        "",
    )
    records = _split_records(stream, tmp_path)
    assert [r.sha for r in records] == ["a" * 40, "b" * 40]
    assert records[0].committed_at == datetime(2026, 1, 1, tzinfo=UTC)
    assert records[1].committed_at == datetime(2026, 2, 1, tzinfo=UTC)
    assert records[1].message == ""  # a legitimate empty final field, not a terminator artefact


def test_split_records_tolerates_a_terminator_variant_trailing_empty_token(tmp_path: Path) -> None:
    """A ``3n+1`` stream with a trailing empty token (a terminator ``-z``) drops exactly it.

    Drives the defensive ``tokens.pop()`` branch: a git whose ``-z`` *terminates*
    records would append one NUL after the last, leaving a trailing empty token. The
    split must still yield n whole records rather than raise -- so the branch that
    never fires on real ``format:`` output no longer survives its own deletion.
    """
    normal = _record_stream(
        "a" * 40,
        "2026-01-01T00:00:00+00:00",
        "one",
        "b" * 40,
        "2026-02-01T00:00:00+00:00",
        "two",
    )
    records = _split_records(normal + _NUL, tmp_path)  # the terminator variant's trailing NUL
    assert [r.sha for r in records] == ["a" * 40, "b" * 40]
    assert [r.message for r in records] == ["one", "two"]


def test_split_records_rejects_a_misframed_3n_plus_1_stream(tmp_path: Path) -> None:
    """A ``3n+1`` stream whose extra token is *non-empty* is genuinely mis-framed and refused.

    Unlike the terminator variant (a trailing *empty* token), a non-empty extra
    token cannot be a record boundary, so ``tokens.pop()`` must not fire and the
    framing guard must raise ``GitOutputFramingError`` with a remedy -- driving the
    raise branch that is otherwise dead against real output.
    """
    stream = _record_stream(
        "a" * 40, "2026-01-01T00:00:00+00:00", "body", "a dangling non-empty token"
    )
    with pytest.raises(GitOutputFramingError) as caught:
        _split_records(stream, tmp_path)
    assert caught.value.remedy  # a remedy, not a bare stack trace
    assert "4" in str(caught.value)  # the offending field count is reported


def test_split_records_rejects_a_3n_plus_2_stream(tmp_path: Path) -> None:
    """A ``3n+2`` stream partitions into no whole record and is refused.

    ``% width == 2``, so the terminator-drop branch (which fires only at ``% width
    == 1``) never touches it and the framing guard raises.
    """
    stream = _record_stream(
        "a" * 40, "2026-01-01T00:00:00+00:00", "body", "b" * 40, "2026-02-01T00:00:00+00:00"
    )
    with pytest.raises(GitOutputFramingError):
        _split_records(stream, tmp_path)


def test_split_records_marks_a_year_10000_date_unrepresentable(tmp_path: Path) -> None:
    """A record whose committer date is year 10000 is carried with ``committed_at=None``.

    This is the metadata-rejection signal ``load_findings`` accounts as a rejected
    record. ``_split_records`` does not raise on it; it marks the date
    unrepresentable and preserves the raw ``%cI`` verbatim for the rejection reason.
    """
    stream = _record_stream("a" * 40, "10000-01-02T00:00:00Z", "Review-Finding: security HIGH — x")
    (record,) = _split_records(stream, tmp_path)
    assert record.committed_at is None
    assert record.date_iso == "10000-01-02T00:00:00Z"
    assert record.sha == "a" * 40


def test_split_records_marks_an_offsetless_date_unrepresentable(tmp_path: Path) -> None:
    """A committer date with no offset is unrepresentable, never read as local time (#405).

    ``%cI`` always carries an offset, so this is unreachable from git -- which is
    exactly why it needs driving here. Two things would go wrong without the guard.
    ``astimezone`` on a naive value reads the *machine's* own offset, so the stored
    instant would depend on where the build ran; and before the normalisation the
    naive value flowed on to ``ReviewFinding.__post_init__``'s timezone-aware check
    and raised a ``DomainError`` that ``load_findings`` does not catch, aborting the
    whole load on one record -- the fatal abort D3 forbids.

    Pinned at the split boundary where the mark is set, beside the year-10000
    sibling, and end-to-end by the ``load_findings`` accounting below.
    """
    stream = _record_stream(
        "a" * 40, "2026-01-01T00:00:00", "Review-Finding: security HIGH — no offset"
    )
    (record,) = _split_records(stream, tmp_path)
    assert record.committed_at is None
    assert record.date_iso == "2026-01-01T00:00:00"  # kept verbatim for the rejection


def test_an_offsetless_date_is_accounted_as_a_rejection_not_a_fatal_abort(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The offsetless record becomes one rejection and its siblings still load (#405, D3).

    ``git log`` cannot be made to emit an offsetless ``%cI``, so the stream is
    injected at the one seam between the subprocess and the parse. That keeps the
    whole of ``load_findings`` -- the accounting, the sort keys, the rejection
    reason -- under test, which a direct ``_split_records`` call does not reach.
    """
    stream = _record_stream(
        "a" * 40,
        "2026-01-01T00:00:00",  # no offset
        "Review-Finding: security HIGH — on the offsetless record",
        "b" * 40,
        "2026-02-01T00:00:00+00:00",
        "Review-Finding: security LOW — on a well-formed sibling",
    )
    source = GitTrailerFindingSource(tmp_path)
    monkeypatch.setattr(GitTrailerFindingSource, "_git_log", lambda _self: stream)

    load = source.load_findings()  # must not raise

    assert [f.finding_text for f in load.accepted] == ["on a well-formed sibling"]
    assert len(load.rejected) == 1
    assert load.rejected[0].commit_sha == "a" * 40


# --- #405 R1-1: a UTC conversion that overflows is a rejection, not a crash ---
#
# `fromisoformat` guards year >= 10000, but `astimezone(UTC)` shifts a
# representable local datetime *across* datetime's range and raises OverflowError
# -- an ArithmeticError, not a ValueError -- at TWO boundaries: a max-year value
# with a NEGATIVE offset (23:59 -01:00 lands in year 10000), and a min-year value
# with a POSITIVE offset (00:00 +05:00 lands before year 1). Both are inward-safe
# with the opposite-sign offset. git can emit the first (verified) but refuses the
# second (a pre-year-1 epoch is rejected), so the second is driven at the seam.

_OVERFLOWING_COMMITTER_DATES: tuple[tuple[str, str], ...] = (
    ("9999-12-31T23:00:00-01:00", "max year, negative offset -> shifts past year 9999"),
    ("9999-12-31T23:00:00-14:00", "max year, extreme negative offset"),
    ("0001-01-01T00:00:00+05:00", "min year, positive offset -> shifts below year 1"),
    ("0001-01-01T00:00:00+14:00", "min year, extreme positive offset"),
)

_SAFE_EDGE_COMMITTER_DATES: tuple[tuple[str, str], ...] = (
    ("9999-01-01T00:00:00-11:00", "max year, negative offset shifts INWARD -> holds"),
    ("0001-12-31T00:00:00+05:00", "min year, positive offset shifts INWARD -> holds"),
)


@pytest.mark.parametrize("date_iso, why", _OVERFLOWING_COMMITTER_DATES)
def test_parse_committer_date_returns_none_when_utc_conversion_overflows(
    date_iso: str, why: str
) -> None:
    """R1-1: a date whose UTC instant is out of range is unrepresentable, never a raise.

    ``astimezone(UTC)`` raises ``OverflowError`` -- an ``ArithmeticError``, which the
    ``except ValueError`` above it does not catch -- when a representable local
    datetime shifts across ``datetime``'s range. Both boundaries are covered: a
    max-year value with a negative offset, and a min-year value with a positive one.
    The fixture's premise is asserted first (``astimezone`` really does raise for
    each), so a change that stopped raising would not leave this passing vacuously.
    """
    parsed = datetime.fromisoformat(date_iso)  # representable as a local datetime ...
    with pytest.raises(OverflowError):  # ... but not once shifted to UTC (the premise)
        parsed.astimezone(UTC)

    # The parse must return None rather than let the OverflowError escape.
    assert _parse_committer_date(date_iso) is None, why


@pytest.mark.parametrize("date_iso, why", _SAFE_EDGE_COMMITTER_DATES)
def test_parse_committer_date_holds_an_inward_shifting_edge(date_iso: str, why: str) -> None:
    """R1-1 control: an offset that shifts a boundary year INWARD is representable.

    Without this, the test above could pass for a parse that returned ``None`` for
    every extreme year regardless of the shift direction -- discarding a valid
    committer date. Here the offset moves the instant away from the boundary, so the
    conversion holds and the parse returns an aware UTC datetime.
    """
    result = _parse_committer_date(date_iso)
    assert result is not None, why
    assert result.tzinfo is UTC
    assert result == datetime.fromisoformat(date_iso).astimezone(UTC)


def test_an_overflowing_committer_date_is_a_rejection_and_siblings_still_load(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R1-1 end-to-end (min-year edge): one overflow rejects its record, others load.

    Drives the min-year positive-offset edge git cannot emit, through the whole of
    ``load_findings`` at the subprocess seam -- the accounting, the sort keys and
    the rejection reason -- so a crafted date that bricks the corpus at the parse is
    accounted as exactly one rejection (D3), never a fatal abort. The max-year edge,
    which git *can* emit, is driven end-to-end through the real CLI in
    ``test_findings_build_cli.py``.
    """
    stream = _record_stream(
        "a" * 40,
        "0001-01-01T00:00:00+05:00",  # overflows to before year 1 on the UTC shift
        "Review-Finding: security HIGH — on the overflowing record",
        "b" * 40,
        "2026-02-01T00:00:00+00:00",
        "Review-Finding: security LOW — on a well-formed sibling",
    )
    source = GitTrailerFindingSource(tmp_path)
    monkeypatch.setattr(GitTrailerFindingSource, "_git_log", lambda _self: stream)

    load = source.load_findings()  # must not raise

    assert [f.finding_text for f in load.accepted] == ["on a well-formed sibling"]
    assert len(load.rejected) == 1
    assert load.rejected[0].commit_sha == "a" * 40
    assert "0001-01-01T00:00:00+05:00" in load.rejected[0].raw_line
    assert "0001-01-01T00:00:00+05:00" in load.rejected[0].reason
    assert "committer date" in load.rejected[0].reason


def test_split_records_empty_stream_yields_no_records(tmp_path: Path) -> None:
    """An empty stdout partitions to zero records via the pop branch, not a framing error.

    ``b"".split(NUL)`` is a single empty token; the terminator-drop branch removes
    it (``1 % 3 == 1`` and it is empty), leaving zero tokens and zero records rather
    than a spurious ``GitOutputFramingError``.
    """
    assert _split_records(b"", tmp_path) == []
