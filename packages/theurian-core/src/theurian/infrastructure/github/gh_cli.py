"""The one module in the shipped package that reaches GitHub (ADR-0030 clause 1).

Every request this product makes to GitHub leaves through
:func:`run_bounded` here, and ``tests/unit/test_network_call_sites.py``'s
``PROCESS_SPAWN_SITES`` pins that by equality over the whole package -- so a
second fetch path added later on a page nobody re-reads reddens, and so does
this one being removed.

**What replaced the absence.** Until this module landed, what stood in for T-7's
three controls was that nothing in the shipped package could reach out at all. A
sentence with no successor: the first time it is false it is retired, and
whatever it was protecting is unprotected. The clauses that take its place are
properties with tests, and the ones this module carries are:

* **clause 2** -- the endpoint is the literal ``graphql``; identity travels as
  typed variables, and no vector element is built by formatting a repository
  name into a path;
* **clause 3** -- ``--hostname github.com`` is present in every spawned vector
  **that makes a request**, which is what holds against an inherited ``GH_HOST``
  (ADR-0030 run B measured the threat, run A the pin). The exemption is
  ``gh --version``, which prints a compiled-in string, parses no host and opens
  no connection. The population is read off this module's source by
  ``test_the_hostname_is_pinned_in_every_vector_that_makes_a_request``, so a
  fourth vector added later is judged rather than assumed to be one or the
  other;
* **clause 4** -- the child environment is the closed constant in
  :mod:`~theurian.infrastructure.github.environment`, constructed and never
  inherited;
* **clause 5** -- the binary is resolved to an absolute path and the vector is
  spawned directly: there is no shell anywhere in this module, and
  ``asyncio.create_subprocess_shell`` is never called;
* **clause 6** -- no ``--paginate``. Every page after the first is asked for by
  handing back a GraphQL cursor in a typed variable. ``--paginate`` follows a
  next-page reference the **response** supplies, and a destination the response
  chooses is the shape T-7 names;
* **clause 7** -- a request timeout, and caps counted in named constants;
* **clause 9** -- ``gh`` absent, or present and unauthenticated, is a graded
  refusal envelope with a remedy, never a traceback. The child's stderr surfaces
  **only inside that envelope**: it reaches no logger, no stream and no caller
  by any other route, which is why nothing here prints or re-raises it.
* **clause 10** -- the response is read **incrementally against a byte cap**
  rather than accumulated and measured afterwards.

**Why ``asyncio`` and not ``subprocess``.** ``ReviewProvider`` is an async port,
and an ``async def`` wrapping a blocking ``subprocess.run`` would block whatever
loop it is called on. ``asyncio.create_subprocess_exec`` also gives the bounded
incremental read clause 10 requires without a ``selectors`` loop: a deadline is
``asyncio.wait_for`` around each chunk, and the cap is checked between chunks.

**What this module cannot see, stated because the absence control could.** Once
the vector is handed over, what ``gh`` does with it is outside every instrument
this suite has: a socket watch cannot see into another process, and
:mod:`~theurian.infrastructure.github.transport_guard` reduces one measured way
the child's own configuration redirects it without closing the class.
"""

from __future__ import annotations

import asyncio
import contextlib
import re
import shutil
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Protocol, final

from theurian.domain.review_ingest import (
    MAX_REFUSAL_DETAIL_CHARS,
    RefusalGrade,
    ReviewIngestRefusedError,
)
from theurian.infrastructure.github.limits import (
    GH_VERSION_FLOOR,
    MAX_CHILD_STDERR_BYTES,
    MAX_PROBE_STDOUT_BYTES,
    MAX_RESPONSE_BYTES,
    REAP_SECONDS,
    REQUEST_TIMEOUT_SECONDS,
    rendered_version,
)

#: The host every request is pinned to, as the value of ``--hostname``.
GITHUB_HOSTNAME: Final = "github.com"

#: The ``gh api`` endpoint, byte for byte. A literal, and the whole of clause 2's
#: first half: there is no path segment here for an owner or a repository name to
#: escape into, because there is no path at all.
GRAPHQL_ENDPOINT: Final = "graphql"

#: The executable name looked up on the *operator's* ``PATH`` -- not the child's
#: fixed one. How ``gh`` was installed is the operator's business; which binaries
#: the child may reach is not.
GH_EXECUTABLE: Final = "gh"

#: How much stdout is taken per read. Large enough that a normal response costs a
#: handful of reads, small enough that the cap is noticed within one of them.
_CHUNK_BYTES: Final = 64 * 1024

_VERSION = re.compile(r"(\d+)\.(\d+)\.(\d+)")

#: The most digits one version component may carry and still be read as a number.
#:
#: ``gh`` prints small integers, so nine is generous by any measure a release has
#: ever needed. The bound exists because ``int()`` **is not total**: CPython
#: refuses to convert a string past ``sys.get_int_max_str_digits()``, 4300 by
#: default, and a binary printing more than that -- a broken one, or one chosen
#: to be -- would put a ``ValueError`` out of the version probe. That is the
#: traceback clause 9 forbids, on the path whose whole job is to answer with a
#: graded envelope instead. Past this bound the output is not a version this
#: adapter can read, which is a state it already grades.
_MAX_VERSION_DIGITS: Final = 9


def _is_convertible(match: re.Match[str]) -> bool:
    """Whether a matched version's three components can be converted at all.

    Asked before :func:`int` rather than caught after it: a refusal that names
    the version it could not read is the answer here, and an exception escaping
    the probe is not.
    """
    return all(len(match[part]) <= _MAX_VERSION_DIGITS for part in (1, 2, 3))


class _Closable(Protocol):
    """The one method :func:`_release` needs from a child's transport."""

    def close(self) -> None: ...


@dataclass(frozen=True, slots=True)
class ChildOutcome:
    """One finished child: its exit status, its bounded stdout and its stderr.

    ``stderr`` is already decoded with replacement and sliced to
    :data:`~theurian.domain.review_ingest.MAX_REFUSAL_DETAIL_CHARS`, because the
    only place it may travel is a refusal envelope's ``detail`` and that field
    refuses an oversized value at construction.
    """

    returncode: int
    stdout: bytes
    stderr: str


def locate_binary(parent: Mapping[str, str]) -> Path:
    """The absolute path of the operator's ``gh``, or a graded refusal.

    Args:
        parent: The environment whose ``PATH`` is searched. The operator's, not
            the child's: the child's ``PATH`` is a fixed literal that decides
            which helpers ``gh`` may run, and using it to find ``gh`` itself
            would refuse every installation outside those five directories.

    Raises:
        ReviewIngestRefusedError: Graded
            :attr:`~theurian.domain.review_ingest.RefusalGrade.TOOL_MISSING`.
            Nothing is spawned to discover this -- ``which`` is a lookup.
    """
    found = shutil.which(GH_EXECUTABLE, path=parent.get("PATH"))
    if found is None:
        raise ReviewIngestRefusedError(
            RefusalGrade.TOOL_MISSING,
            "Review ingestion needs the GitHub CLI and found no `gh` on this "
            "machine's PATH. Nothing was spawned, and local knowledge is "
            "unaffected -- ingestion is the optional capability.",
        )
    # Resolved to an absolute path here, once, so no later vector's first element
    # is a bare name the child's PATH would have to resolve (clause 5).
    return Path(found).resolve()


async def run_bounded(
    args: Sequence[str],
    *,
    env: Mapping[str, str],
    timeout: float = REQUEST_TIMEOUT_SECONDS,
    byte_cap: int = MAX_RESPONSE_BYTES,
) -> ChildOutcome:
    """Spawn ``args``, read its output against ``byte_cap``, and stop it at ``timeout``.

    **The read shape is the property, not just the number** (clause 10). Output
    is taken a chunk at a time and the running total is compared against
    ``byte_cap`` *between* chunks, so a child that emits more than the cap is
    refused at the moment it does -- not after it finishes, and not after this
    process has held the whole thing. A child that overruns the cap and then
    blocks for ever is refused inside the deadline rather than waited on, which
    is what
    ``test_a_child_that_overruns_the_cap_is_refused_without_waiting_for_it_to_finish``
    drives under a bounded wait: an implementation that accumulates first misses
    the deadline, one that reads incrementally returns inside it.

    stderr is drained concurrently and **capped in memory while still being
    consumed to EOF**: a child whose stderr pipe filled would block on the write
    and never reach the exit this waits for, so the drain cannot simply stop at
    the cap.

    **Three awaits sit between the spawn and the answer, and one deadline bounds
    all three**: each stdout read, the child's exit, and the stderr drain. The
    drain is the one that has to be said out loud, because a *descendant* of the
    child inherits fd 2 and can hold the stderr pipe open after the child itself
    has exited and stdout has reached EOF -- an unbounded ``await`` on the drain
    task then returns a **success** whenever the descendant finally lets go.
    ``test_a_descendant_that_holds_stderr_open_is_refused_at_the_deadline``
    drives exactly that shape.

    What outlives ``timeout`` is the reap, deliberately and by a recorded amount:
    once the deadline expires the child is killed and given at most
    :data:`REAP_SECONDS` to die, so the wall-clock ceiling on this call is
    ``timeout + REAP_SECONDS`` plus whatever the spawn itself costs. Waiting for
    a killed child is what keeps a refusal from leaving a process behind, and
    :func:`_end` runs from a ``finally`` so a cancellation or an exception this
    function does not name cannot skip it.

    Raises:
        ReviewIngestRefusedError: Graded ``LIMIT_EXCEEDED`` when the response
            passes ``byte_cap``, and ``TOOL_FAILED`` when the child cannot be
            spawned, or when the reads, the exit or the drain do not finish
            inside ``timeout``. Both kill the child first, so no refusal leaves a
            process behind.
    """
    child, stdout, errors = await _start(args, env)
    draining = asyncio.create_task(_drain_capped(errors, MAX_CHILD_STDERR_BYTES))
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    chunks: list[bytes] = []
    total = 0
    answered = False
    try:
        while True:
            remaining = deadline - loop.time()
            if remaining <= 0:
                raise TimeoutError
            chunk = await asyncio.wait_for(stdout.read(_CHUNK_BYTES), remaining)
            if not chunk:
                break
            total += len(chunk)
            if total > byte_cap:
                raise ReviewIngestRefusedError(
                    RefusalGrade.LIMIT_EXCEEDED,
                    f"Review ingestion refused a GitHub CLI answer larger than the "
                    f"recorded {byte_cap}-byte cap. It was refused at the cap rather "
                    f"than read to the end and truncated afterwards, so nothing past "
                    f"it was held.",
                )
            chunks.append(chunk)
        await asyncio.wait_for(child.wait(), max(deadline - loop.time(), 0.0))
        contained = await asyncio.wait_for(draining, max(deadline - loop.time(), 0.0))
        answered = True
        return ChildOutcome(
            returncode=child.returncode or 0, stdout=b"".join(chunks), stderr=contained
        )
    except TimeoutError as exc:
        raise ReviewIngestRefusedError(
            RefusalGrade.TOOL_FAILED,
            f"The GitHub CLI did not answer within the recorded "
            f"{timeout:g}-second request timeout (SEC-19), so it was stopped.",
        ) from exc
    finally:
        # Every exit but the answered one: the byte cap, the deadline, a
        # cancellation from the caller's own `wait_for`, and any exception this
        # function does not name. A cap refusal used to clean up on its own line
        # and the other three did not, which is how a cancelled call left both a
        # live child and a pending drain task behind.
        if not answered:
            await _end(child, draining)


async def _start(
    args: Sequence[str], env: Mapping[str, str]
) -> tuple[asyncio.subprocess.Process, asyncio.StreamReader, asyncio.StreamReader]:
    """Spawn ``args`` and hand back the child with its two pipes, narrowed.

    Split out of :func:`run_bounded` so that "start a process" and "read its
    output against a cap" are two things a reader can check separately -- the
    second is the property clause 10 is about, and it is easier to see when it is
    not sharing a function with the spawn's own failure handling.

    **This is the seam every argv element passes through**, which is why the
    catch below is on the exception *types* a spawn declines with rather than on
    the byte values a particular caller was known to produce.
    """
    try:
        # The vector is the adapter's: an absolute binary path, literal flags, a
        # literal GraphQL document, and `name=value` variable bindings. No
        # element is derived by formatting a repository name into a path, and no
        # shell is involved -- `create_subprocess_shell` appears nowhere here.
        # `stdin` is closed for the same reason the environment is (clause 4):
        # what the child may reach is this adapter's decision, not the calling
        # process's. Left unset it is *inherited*, and a spawned `gh` could then
        # read whatever the parent's fd 0 happens to be -- a terminal, a pipe
        # carrying somebody else's data, a file. Prompt-blocking rested on
        # `GH_PROMPT_DISABLED` alone until this; now a prompt has nothing to read
        # from either.
        child = await asyncio.create_subprocess_exec(
            *args,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=dict(env),
        )
    except (OSError, ValueError) as exc:
        # **Two types, because a spawn declines an argument for two reasons and
        # only one of them is an `OSError`.** An element carrying a NUL raises a
        # bare `ValueError`, and one carrying an unpaired surrogate raises
        # `UnicodeEncodeError` -- itself a `ValueError` -- while the vector is
        # being encoded for `execve`. Catching the pair *here* is what closes the
        # class rather than one member of it: it holds for every element of every
        # vector, including values a later caller builds that this module never
        # sees. The same pair is named and closed on two other boundaries in this
        # repository, `infrastructure/sqlite/index_query.py` and
        # `application/proposal_service.py`, for the same reason.
        #
        # `strerror` and nothing else. The fallback used to be `exc` itself, and
        # `str()` of an `OSError` appends its `filename` -- which here is the
        # absolute path of the operator's `gh`, inside their home directory, in a
        # published envelope. An `OSError` raised with a single argument carries
        # no `strerror` at all, so that branch was reachable rather than
        # theoretical; the class name locates the failure without a path, and it
        # is also what names a `ValueError`, which has no `strerror` to read.
        named = exc.strerror if isinstance(exc, OSError) else None
        raise ReviewIngestRefusedError(
            RefusalGrade.TOOL_FAILED,
            f"Review ingestion could not start the GitHub CLI: {named or type(exc).__name__}.",
        ) from exc

    if child.stdout is None or child.stderr is None:  # pragma: no cover - PIPE is requested above
        raise ReviewIngestRefusedError(
            RefusalGrade.TOOL_FAILED,
            "Review ingestion could not read the GitHub CLI's output streams.",
        )
    return child, child.stdout, child.stderr


async def _drain_capped(stream: asyncio.StreamReader, cap: int) -> str:
    """Read ``stream`` to EOF, keeping the first ``cap`` bytes and discarding the rest.

    Reading to EOF is what keeps the child from blocking on a full stderr pipe;
    keeping only a prefix is what keeps an unbounded child from being held in
    memory. The result is decoded with replacement -- these bytes are the child's
    and nothing about them is guaranteed -- and sliced again so it fits the
    envelope field that will carry it.
    """
    kept = bytearray()
    while True:
        chunk = await stream.read(_CHUNK_BYTES)
        if not chunk:
            break
        if len(kept) < cap:
            kept.extend(chunk[: cap - len(kept)])
    return kept.decode("utf-8", errors="replace")[:MAX_REFUSAL_DETAIL_CHARS]


async def _end(child: asyncio.subprocess.Process, draining: asyncio.Task[str]) -> None:
    """Kill ``child`` and stop draining it, so no refusal leaves a process behind.

    **The two statements that release the resources are synchronous and run
    first** -- cancelling the drain task, signalling the child. Nothing that can
    go wrong afterwards can then skip them, and what can go wrong afterwards is
    ordinary: a second cancellation arriving while this unwinds, or the reap
    raising something the suppressions below do not name. Either leaves the rest
    of this function unrun, and by then the two that matter have happened.

    **Each await after them is wrapped on its own**, which is what makes
    :func:`_release` -- the held-file-descriptor fix -- reachable however the
    cancellation is delivered. A cancelled coroutine's ``finally`` is ordinary
    code and an ``await`` inside one does *not* raise on sight: measured on
    CPython 3.13, one completes and the statement after it runs, on both delivery
    paths (cancelled at a pending future, and cancelled while already scheduled).
    An earlier version of this docstring claimed the opposite and gave it as the
    reason for the ordering above; the ordering is right and the reason was
    false. But "does not raise on sight" is not "always completes" -- a second
    cancellation can arrive mid-unwind -- so the suppressions are per-statement
    rather than one around the group, and the last line is reached either way.

    **What a caller pays for it**: cancelling a ``run_bounded`` no longer returns
    at once. The canceller waits for this unwind, **up to** :data:`REAP_SECONDS`
    -- an upper bound and not a promise about how much of it is spent.
    ``Process.wait()`` returns as soon as the child's exit has already been
    observed, and waits for the exit *and* every pipe disconnection only when it
    is called before that; which of the two happens is a race with a callback,
    so the same cancellation can cost the whole reap on one machine and nothing
    on another. Both are correct, and
    ``test_a_cancelled_call_runs_end_to_its_last_line`` asserts what holds on
    both: the unwind finishes, it reaches ``_release``, and it is bounded. That
    is the deliberate trade -- the same cancellation used to return immediately
    and leave a live child and a pending drain task behind, and what replaces it
    is a wait with a recorded ceiling.
    """
    draining.cancel()
    # The child can exit between the `returncode` read and the signal, and a
    # `ProcessLookupError` raised while unwinding a refusal would replace the
    # graded envelope with the traceback clause 9 forbids.
    with contextlib.suppress(ProcessLookupError):
        if child.returncode is None:
            child.kill()
    with contextlib.suppress(TimeoutError, asyncio.CancelledError):
        await asyncio.wait_for(child.wait(), REAP_SECONDS)
    with contextlib.suppress(asyncio.CancelledError):
        await draining
    _release(child)


def _release(child: asyncio.subprocess.Process) -> None:
    """Close the child's transport, so an abandoned pipe is not left open.

    ``asyncio`` finishes a subprocess transport once the process has exited *and*
    every one of its pipes has disconnected. A descendant that inherited fd 2
    keeps the stderr pipe open past both, so after a deadline refusal the
    transport is still open when the collector reaches it: a held file descriptor
    per abandoned child, reported as the ``ResourceWarning`` this suite raises as
    an error.

    ``Process`` publishes no way to say this, so the transport is reached by name
    through a ``Protocol`` -- the edge this project admits an untyped value at --
    and with ``getattr``'s default rather than an attribute access, so a runtime
    that renames it leaves the transport to the collector instead of raising
    ``AttributeError`` out of a cleanup path.
    """
    transport: _Closable | None = getattr(child, "_transport", None)
    if transport is not None:
        transport.close()


def _binding(name: str, value: str | int) -> str:
    """One ``name=value`` vector element, or a graded refusal if it cannot be built.

    **The construction seam, and the sibling of the one in :func:`_start`.** That
    one catches what ``execve`` declines; this one catches what *rendering*
    declines, and the two failures are a whole stage apart. An element that
    cannot be built never reaches a spawn at all, so the spawn's catch cannot
    see it: ``str()`` of an integer past ``sys.get_int_max_str_digits()`` -- 4300
    by default -- raises ``ValueError`` here, inside the f-string, and
    ``get_threads`` used to leave that as a traceback after its two probes had
    already run.

    It is a catch on the **exception type at the one place every element is
    built**, deliberately, and not a digit bound at the call sites. A bound
    checked per caller is an enumeration the next caller escapes; this holds for
    every variable of every vector, including ones a later change adds. Size is a
    separate question with a separate seam -- an element too long for ``execve``
    is ``E2BIG``, an ``OSError``, which :func:`_start` already grades.
    """
    try:
        return f"{name}={value}"
    except ValueError as exc:
        # The value is not echoed, because the reason it is refused is that it
        # cannot be rendered. The variable name is this adapter's own literal.
        raise ReviewIngestRefusedError(
            RefusalGrade.TOOL_FAILED,
            f"Review ingestion could not build the `{name}` argument for a GitHub "
            f"request: the value cannot be rendered as text, so no vector was "
            f"assembled and nothing was spawned with it.",
        ) from exc


@final
class GhCli:
    """A resolved ``gh`` binary, the environment it runs under, and the two probes.

    One instance per provider, so the version and authentication probes -- which
    are spawns, and clause 9's second one is a request -- happen once rather than
    per call.

    ``environment`` is the **already-constructed child** environment, not a
    parent to derive one from: which variables a child may see is
    :mod:`~theurian.infrastructure.github.environment`'s decision, and this class
    passes what it is given rather than having a second opinion about it.
    """

    def __init__(self, *, binary: Path, environment: Mapping[str, str]) -> None:
        self._binary = binary
        self._environment = dict(environment)

    @property
    def binary(self) -> Path:
        """The absolute path spawned as every vector's first element."""
        return self._binary

    def vector(self, *arguments: str) -> tuple[str, ...]:
        """The argument vector for one ``gh`` invocation.

        The first element is the absolute binary path (clause 5). Nothing else
        here is derived from caller data: the callers below pass literals and
        ``name=value`` variable bindings.
        """
        return (str(self._binary), *arguments)

    async def _probe(self, *arguments: str) -> ChildOutcome:
        """One probe spawn, read against the probes' own stdout cap.

        **The overrun is a broken tool, not a caller's limit**, which is why the
        grade is re-stated here rather than left as ``run_bounded``'s. That
        function refuses ``LIMIT_EXCEEDED`` because for a *response* the cap is a
        bound a caller can act on -- ask for fewer pull requests, narrow the run.
        A probe takes no bounds from anybody: ``gh --version`` prints one line
        and ``gh auth status`` a short report, so 64 KiB from either says the
        binary is not the one this adapter is written against. Sending that
        operator to adjust `limit` is a remedy for a cause they do not have.

        The refusal happened at the cap either way -- nothing past it was read --
        and what changes is the sentence and the cure a reader is handed.
        """
        try:
            return await run_bounded(
                self.vector(*arguments),
                env=self._environment,
                byte_cap=MAX_PROBE_STDOUT_BYTES,
            )
        except ReviewIngestRefusedError as exc:
            if exc.grade is not RefusalGrade.LIMIT_EXCEEDED:
                raise
            raise ReviewIngestRefusedError(
                RefusalGrade.TOOL_FAILED,
                f"`gh {arguments[0]}` printed more than the recorded "
                f"{MAX_PROBE_STDOUT_BYTES}-byte probe cap, which is not something the "
                f"GitHub CLI does. It was stopped at the cap, so nothing past it was "
                f"read, and no request was made with the answer.",
            ) from exc

    async def version(self) -> tuple[int, int, int]:
        """The installed ``gh``'s version, refusing below the recorded floor.

        Raises:
            ReviewIngestRefusedError: ``TOOL_TOO_OLD`` below
                :data:`~theurian.infrastructure.github.limits.GH_VERSION_FLOOR`,
                and when the version cannot be read at all -- an output this
                adapter cannot parse is a binary it has no measurement of, which
                is the same position as one below the floor. "Cannot be read"
                includes a component past :data:`_MAX_VERSION_DIGITS`, which is
                not fussiness: ``int()`` refuses a longer one and the
                ``ValueError`` would leave this probe as a traceback.
        """
        outcome = await self._probe("--version")
        match = _VERSION.search(outcome.stdout.decode("utf-8", errors="replace"))
        if outcome.returncode != 0 or match is None or not _is_convertible(match):
            raise ReviewIngestRefusedError(
                RefusalGrade.TOOL_TOO_OLD,
                f"Review ingestion could not read a version from `gh --version`, so it "
                f"cannot tell whether this binary is at or above the "
                f"{rendered_version(GH_VERSION_FLOOR)} floor this adapter was measured "
                f"against.",
                detail=outcome.stderr,
            )
        found = (int(match[1]), int(match[2]), int(match[3]))
        if found < GH_VERSION_FLOOR:
            raise ReviewIngestRefusedError(
                RefusalGrade.TOOL_TOO_OLD,
                f"Review ingestion needs GitHub CLI {rendered_version(GH_VERSION_FLOOR)} "
                f"or newer and found {rendered_version(found)}. The flag and "
                f"environment behaviours this adapter relies on were measured against "
                f"{rendered_version(GH_VERSION_FLOOR)}, and running below it would be "
                f"reaching GitHub through a binary nobody measured.",
            )
        return found

    async def require_authenticated(self) -> None:
        """Refuse unless ``gh`` confirms a session for ``github.com``.

        Raises:
            ReviewIngestRefusedError: ``TOOL_UNAUTHENTICATED``. The grade does
                **not** distinguish "not signed in" from "could not check",
                because the probe cannot: both are a session this run may not
                assume, and grading them apart would report which of the two the
                machine is in.
        """
        outcome = await self._probe("auth", "status", "--hostname", GITHUB_HOSTNAME)
        if outcome.returncode != 0:
            raise ReviewIngestRefusedError(
                RefusalGrade.TOOL_UNAUTHENTICATED,
                f"The GitHub CLI did not confirm a session for {GITHUB_HOSTNAME}, so "
                f"review ingestion was not attempted. Local knowledge is unaffected.",
                detail=outcome.stderr,
            )

    def graphql_vector(
        self, *, document: str, variables: Mapping[str, str | int]
    ) -> tuple[str, ...]:
        """The exact vector one ``gh api graphql`` request is spawned as.

        Separate from :meth:`graphql` so the properties clauses 2, 3, 5 and 6
        name are asserted against **this** function's output rather than against
        a test's transcription of it: a transcribed vector agrees with itself
        however the adapter changes.

        ``<binary> api graphql --hostname github.com`` followed by one flag pair
        per variable, and **the flag is chosen by the value's type**:

        * a **string** goes on ``-f``, ``gh``'s raw-field form, which sends the
          value verbatim. Every caller-derived value -- the owner, the repository
          name, a pagination cursor -- is a string, and this is the form that
          keeps it one: a ``-F`` value opening with ``@`` is read as a *filename
          to send*, which is not a shape a repository name may reach.
        * an **integer** goes on ``-F``, the typed form, because the documents
          declare ``$first`` and ``$number`` as ``Int!`` and a raw field arrives
          as a ``String``. Measured against the live API on 2026-09-06: with
          every variable on ``-f``, ``gh`` answers ``Variable $first of type
          Int! was provided invalid value`` and the request fails. ``-F`` is safe
          for exactly these because the value is an integer this adapter
          produced -- ``str(int)`` can never open with ``@``.

        ``--paginate`` is absent, deliberately (clause 6): every page after the
        first is this adapter handing back a cursor in ``after``.

        Variables are emitted in sorted order, so two runs over the same input
        produce byte-identical vectors and a recorded argv can be compared
        against one built here.
        """
        arguments = [
            "api",
            GRAPHQL_ENDPOINT,
            "--hostname",
            GITHUB_HOSTNAME,
            "-f",
            f"query={document}",
        ]
        for name in sorted(variables):
            value = variables[name]
            flag = "-F" if isinstance(value, int) and not isinstance(value, bool) else "-f"
            arguments += [flag, _binding(name, value)]
        return self.vector(*arguments)

    async def graphql(self, *, document: str, variables: Mapping[str, str | int]) -> ChildOutcome:
        """One ``gh api graphql`` request, spawned as :meth:`graphql_vector` describes."""
        return await run_bounded(
            self.graphql_vector(document=document, variables=variables), env=self._environment
        )
