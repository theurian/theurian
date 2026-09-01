"""``theurian.mcp.tools.ToolError`` is two things at once, and both are pinned.

Issue https://github.com/theurian/theurian/issues/469. This class carried the
SDK's *name* -- ``ToolError`` -- without its identity for three milestones, and
nothing noticed, because mcp 2.0.0's tool dispatcher wraps every escaping
exception the same way:

    except Exception as e:
        raise ToolError(f"Error executing tool {self.name}: {e}") from e

From mcp 2.1.0 (upstream PR #3314) that arm split in two. The SDK's own
``ToolError``/``ResourceError`` keep forwarding ``str(exc)``; everything else is
treated as a crash, logged with its traceback server-side, and answered with a
bare ``Error executing tool <name>``. Every remedy this daemon writes travels
through the one local class, so the whole surface fell into the crash arm at
once: 44 assertions on message text went RED on the 2.0.0 -> 2.1.1 bump
(https://github.com/theurian/theurian/pull/460).

**Why a type test and not a wire test.** Under the pinned mcp 2.0.0 the second
base is inert -- both arms of that dispatcher produce the same bytes -- so no
end-to-end call against the pinned SDK can distinguish the fixed class from the
broken one. The 44-assertion wire evidence exists, but it only exists against
mcp >= 2.1, which this repository does not pin yet. What is pinnable *here* is
the property those 44 assertions actually depend on, and it is a property of
the type: theurian's ``ToolError`` must satisfy both ``isinstance`` questions
the two consumers ask of it. Dropping either base makes this module RED
immediately rather than at the next dependency bump.

Nothing in the first half asserts anything about message *content*. The class
defines no ``__init__`` and no ``__str__``, which is itself pinned below: what a
caller reads is what the raise site built, and the messages themselves are
pinned where they are produced -- at the wire, through the SDK's re-raise, by
``tests/integration/test_unreadable_registry_surface.py`` and
``tests/integration/test_mcp_tools.py``.

**The second half is issue
https://github.com/theurian/theurian/issues/491 -- the other face.** Fixing the
class fixes every refusal ``theurian/mcp/tools.py`` raises and nothing else: the
connection layer raises its own ``TheurianError`` subclasses, they travel up
through a tool body that never converts them, and 2.1 treats them as crashes
too. ``_forwarding`` is the one seam that converts them, and what it must do is
*restore*, never *enrich* -- the tests below pin the byte-identity that says so,
and pin that a real crash is still withheld.
"""

from __future__ import annotations

import ast
import functools
import inspect
import pathlib
from collections.abc import AsyncIterator, Iterator
from typing import Any

import pytest
from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ResourceError as SdkResourceError
from mcp.server.mcpserver.exceptions import ToolError as SdkToolError

import theurian.mcp.tools as tools_module
from theurian.application.project_service import ProjectError
from theurian.domain.errors import TheurianError
from theurian.infrastructure.sqlite.connection import (
    SchemaVersionMismatchError,
    StateDatabaseUnreadableError,
    WriteLockTimeoutError,
)
from theurian.mcp.tools import DEFERRED_RESULT_REFUSAL, ToolError, _forwarding, _tool

pytestmark = pytest.mark.unit


def test_a_tool_error_is_a_theurian_error() -> None:
    """The base that makes it a deliberate refusal rather than a crash.

    ``remedy`` lives on :class:`~theurian.domain.errors.TheurianError`, and the
    ``except TheurianError`` clauses across ``cli/`` and ``application/`` name
    that type. RED if the base is dropped in favour of the SDK's alone.
    """
    assert issubclass(ToolError, TheurianError)
    assert isinstance(ToolError("a refusal"), TheurianError)


def test_a_tool_error_is_the_sdk_s_tool_error() -> None:
    """The base that makes the message reach the caller (#469).

    RED if the SDK base is dropped -- which is the exact state this repository
    was in before #469, and the state in which mcp >= 2.1 withholds every
    remedy in ``theurian/mcp/tools.py``.
    """
    assert issubclass(ToolError, SdkToolError)
    assert isinstance(ToolError("a refusal"), SdkToolError)


def test_the_theurian_base_comes_first_in_the_mro() -> None:
    """Order, not just membership.

    Both bases could in principle answer for an attribute. ``TheurianError``
    winning is what keeps ``remedy`` this project's attribute rather than
    whatever a future SDK release might put on its own error classes, and it is
    what keeps ``except TheurianError`` the narrower, more specific catch at
    every site that already names it.
    """
    mro = ToolError.__mro__
    assert mro.index(TheurianError) < mro.index(SdkToolError), (
        f"`TheurianError` must precede the SDK's `ToolError` in the MRO; got "
        f"{[cls.__name__ for cls in mro]}"
    )


def test_it_is_not_a_resource_error() -> None:
    """The sibling arm, deliberately not joined.

    mcp 2.1's dispatcher forwards ``ToolError`` *and* ``ResourceError``, and the
    SDK's ``MCPServer.read_resource`` has its own ``except ResourceError`` arm
    (``mcp/server/mcpserver/server.py``) that a tool refusal must not enter. The
    two bases above are the whole of the widening; this pins that it is.
    """
    assert not issubclass(ToolError, SdkResourceError)


def test_the_class_adds_no_message_machinery() -> None:
    """The class header is the whole change (#469, and AC-5 of its brief).

    ``ToolError`` defines no ``__init__``, ``__str__``, ``__repr__`` or
    ``args``: the bytes a caller reads are the ones the raise site built, and
    adding a base cannot have moved them. RED if a later change starts
    reshaping messages inside the class, which is where a refusal-wording
    regression would hide from the message pins at the wire.
    """
    own = set(vars(ToolError))
    assert not (own & {"__init__", "__str__", "__repr__", "args"}), (
        f"`ToolError` must not define message machinery of its own; found {sorted(own)}"
    )
    assert str(ToolError("a refusal naming its remedy")) == "a refusal naming its remedy"
    assert ToolError("a refusal").remedy == ""


def test_a_2_1_shaped_dispatcher_forwards_the_message() -> None:
    """The mechanism, driven rather than merely asserted.

    A local model of the arm mcp 2.1.0 introduced -- not the SDK's code, and
    not a substitute for running against 2.1.1, which is what
    https://github.com/theurian/theurian/pull/460 does. It exists so this
    module fails with the *symptom* the issue describes, not only with an
    ``issubclass`` assertion: drop the SDK base and the remedy below is
    replaced by the bare crash text, exactly as 44 assertions saw it.
    """

    def dispatch(exc: Exception) -> str:
        try:
            raise exc
        except (SdkToolError, SdkResourceError) as forwarded:
            return f"Error executing tool knowledge.search: {forwarded}"
        except Exception:
            # The crash arm, modelled deliberately: 2.1 answers with the tool
            # name alone and keeps the exception's text off the wire.
            return "Error executing tool knowledge.search"

    remedy = "Project 'x' is not registered. Run `theurian project register`."
    assert dispatch(ToolError(remedy)) == f"Error executing tool knowledge.search: {remedy}"

    # The control: a class shaped the way `ToolError` was before #469 really
    # does lose its text through the same dispatcher, so the assertion above is
    # not passing for some reason unrelated to the base.
    class UnadaptedToolError(TheurianError):
        pass

    assert dispatch(UnadaptedToolError(remedy)) == "Error executing tool knowledge.search"


# -- issue #491: the refusals raised below this module -------------------------

#: The below-surface ``TheurianError`` subclasses this seam converts, each built
#: exactly as its own raise site builds it.
#:
#: The first two are the *proven reachable* set: they are what
#: ``tests/integration/test_canonical_store_corruption.py`` drives through
#: ``knowledge.get``, ``knowledge.search`` and ``knowledge.status``, and their
#: three assertions are the ones that stay RED under mcp 2.1.1 when this seam is
#: absent. ``ProjectError`` reaches the boundary too but is normally folded
#: earlier by ``_with_remedy``; it is here because "normally" is not "always".
#:
#: ``WriteLockTimeoutError`` is **not** reachable from this tool set and is
#: included deliberately as the negative-space case: Milestone 3 registers no
#: write-intent tool (ADR-0013), and ``git grep write_transaction`` over
#: ``mcp/``, ``retrieval_service.py`` and ``project_service.py`` returns nothing,
#: so nothing here can take the write lock. It is pinned anyway because the
#: property under test belongs to ``_forwarding`` -- which converts any
#: ``TheurianError`` -- rather than to the reachable subset, and because the day
#: a write tool is added is the day the reachable subset changes without this
#: file being touched.
#: ``ProjectError`` is built *with* a remedy on purpose, and it is the only
#: fixture here that has one. A first draft of this file gave it none, and a
#: mutation that folded ``exc.remedy`` into the forwarded message -- the exact
#: enrichment ``_with_remedy`` performs deliberately elsewhere, and the one this
#: seam must not perform -- survived the whole module: every fixture's
#: ``remedy`` was ``''``, so "adds nothing" and "adds the remedy" produced
#: identical bytes. The distinguishing input is the fixture, not the assertion.
BELOW_SURFACE_REFUSALS: dict[str, TheurianError] = {
    "SchemaVersionMismatchError": SchemaVersionMismatchError(pathlib.Path("state.sqlite"), 2, 4),
    "StateDatabaseUnreadableError": StateDatabaseUnreadableError("ValueError"),
    "ProjectError": ProjectError(
        "the registry cannot be read as JSON",
        remedy="Run `theurian project unregister demo` and register it again.",
    ),
    "WriteLockTimeoutError": WriteLockTimeoutError(pathlib.Path("state.sqlite"), 30.0),
}


def test_at_least_one_fixture_carries_a_remedy_the_message_does_not() -> None:
    """The distinguishing input, pinned so it cannot be tidied away.

    Every assertion about "the seam adds nothing" is vacuous over refusals whose
    ``remedy`` is empty, because folding an empty string changes no bytes. This
    module needs at least one fixture where the two differ, or a remedy-folding
    regression passes it silently -- which is what a surviving mutation showed
    before this test existed.
    """
    distinguishing = [
        name
        for name, exc in BELOW_SURFACE_REFUSALS.items()
        if getattr(exc, "remedy", "") and getattr(exc, "remedy", "") not in str(exc)
    ]
    assert distinguishing, (
        "no fixture carries a remedy absent from its own message, so every "
        "'the seam adds nothing' assertion below is vacuous against a "
        "remedy-folding change -- give one of them a `remedy=`"
    )


@pytest.mark.parametrize("name", sorted(BELOW_SURFACE_REFUSALS))
def test_a_below_surface_refusal_reaches_the_wire_with_its_own_text_and_nothing_added(
    name: str,
) -> None:
    """Parity, stated as byte-identity (#491).

    mcp 2.0.0 folded ``str(exc)`` into ``Error executing tool {name}: {e}`` for
    *any* escaping exception. The seam's whole job is to put these back in that
    arm under 2.1 and to add nothing while doing it, so the pin is an equality
    rather than a containment: ``==`` forbids a remedy fold, a path, a class
    name and a traceback all at once, where ``in`` would wave every one of them
    through.

    This matters most for ``StateDatabaseUnreadableError``, whose own docstring
    records that it carries the failing exception's *type* and never the
    corrupted cell -- a deliberate withholding under SEC-13. The seam preserves
    it for the only reason that is durable: it does not look inside the message.
    """
    original = BELOW_SURFACE_REFUSALS[name]

    def raises() -> None:
        raise original

    with pytest.raises(ToolError) as converted:
        _forwarding(raises)()

    assert str(converted.value) == str(original), (
        "the seam must forward the refusal's own text unchanged; anything else "
        "publishes bytes this wire has never carried"
    )
    # `from exc`, not `from None`. The reason is *not* the server-side log:
    # measured against mcp 2.1.1, a forwarded refusal takes the SDK's info arm
    # (`logger.info("Tool %r failed: %r", name, str(exc))`, server.py:438) which
    # passes no `exc_info`, so the chain is never rendered there -- only the
    # crash arm's `logger.exception` would render it, and a converted refusal
    # never reaches that arm. What `__cause__` is actually worth: it keeps the
    # original object recoverable in-process, which is what lets a test compare
    # the forwarded text against the refusal that produced it rather than
    # against a copy of its own expectation; and `from None` would additionally
    # suppress the chain for a crash *inside* the conversion, which is the one
    # case that does reach `logger.exception`.
    assert converted.value.__cause__ is original, (
        "the refusal that produced this text must stay recoverable on `__cause__`; "
        "`from None` would erase the only in-process link back to it"
    )


@pytest.mark.parametrize("name", sorted(BELOW_SURFACE_REFUSALS))
def test_the_seam_adds_no_identifier_of_its_own(name: str) -> None:
    """The enrichment the equality above already forbids, said in its own words.

    Byte-identity is the real pin; this one exists so a reader who weakens the
    equality to a containment still trips something. The three things a
    restoring seam is most tempted to add are the exception's class name, the
    file path it was raised about, and the remedy attribute -- named here so a
    failure says which one arrived.
    """
    original = BELOW_SURFACE_REFUSALS[name]

    def raises() -> None:
        raise original

    with pytest.raises(ToolError) as converted:
        _forwarding(raises)()

    text = str(converted.value)
    assert type(original).__name__ not in text, "the seam leaked the exception class name"
    assert "Traceback" not in text, "the seam leaked a traceback"
    remedy = getattr(original, "remedy", "")
    if remedy and remedy not in str(original):
        assert remedy not in text, (
            "the seam folded in `remedy`, which mcp 2.0.0 dropped -- that is new "
            "information on the wire, not restored information"
        )


def test_a_refusal_this_module_already_worded_passes_through_untouched() -> None:
    """``ToolError`` is re-raised, not rebuilt.

    A rebuild would produce the same bytes today, which is exactly why it is
    worth pinning: identity is what guarantees the seam cannot reword a refusal
    this module already chose, however the class later grows.
    """
    original = ToolError("a refusal naming its remedy.")

    def raises() -> None:
        raise original

    with pytest.raises(ToolError) as passed:
        _forwarding(raises)()

    assert passed.value is original


def test_a_genuine_crash_is_not_converted_and_stays_withheld() -> None:
    """AC-8: upstream's crash hardening is left in force (#491).

    A ``TypeError`` is a defect in this codebase, not a refusal addressed to the
    caller, and mcp 2.1 withholding its text is hardening this project agrees
    with. The seam must therefore let it past unconverted -- being a
    non-``ToolError`` is *precisely* what makes 2.1 withhold it.

    **Why this is asserted on the exception and not on the wire.** Under the
    pinned mcp 2.0.0 every escaping exception's text reaches the wire, so an
    end-to-end "the text is absent" assertion cannot pass here at all: it would
    be a test of the SDK version, not of this seam. The type is the thing the
    2.1 dispatcher branches on, so the type is what this pins. Measured
    end-to-end against 2.1.1 separately, in PR #489.

    RED if the seam widens to ``except Exception``.
    """
    crash = TypeError("an internal call was made with the wrong arity")

    def raises() -> None:
        raise crash

    with pytest.raises(TypeError) as escaped:
        _forwarding(raises)()

    assert escaped.value is crash
    assert not isinstance(escaped.value, SdkToolError), (
        "a crash converted here would be forwarded by mcp 2.1 with its text, "
        "defeating the hardening that surfaced this bug"
    )


def test_the_seam_returns_a_healthy_call_untouched() -> None:
    """The other half of the wrapper, which the refusal tests never exercise."""

    def answer(a: int, *, b: str) -> str:
        return f"{a}{b}"

    assert _forwarding(answer)(1, b="x") == "1x"


def test_the_seam_keeps_the_signature_the_sdk_builds_schemas_from() -> None:
    """A wrapper that hid the signature would silently reshape the wire contract.

    The SDK derives each tool's input schema from the wrapped function's
    signature and annotations. ``tests/integration/test_wire_contract.py``
    validates real responses against the published schemas and would fail if
    this broke, but it would fail a long way from the cause; this says it here.
    """

    def answer(projectId: str, limit: int = 10) -> dict[str, str]:  # noqa: N803
        return {}

    wrapped = _forwarding(answer)
    assert inspect.signature(wrapped) == inspect.signature(answer)
    assert wrapped.__doc__ == answer.__doc__
    assert wrapped.__name__ == answer.__name__


def test_the_seam_leaves_the_sdk_derived_tool_metadata_byte_identical() -> None:
    """The stronger property, over what the SDK *derived* rather than what it read.

    ``inspect.signature`` following ``__wrapped__`` is exactly what
    ``functools.wraps`` promises, so the comparison above can pass while the SDK
    still built something different -- it does its own ``get_type_hints`` and
    pydantic model construction, and those read attributes ``wraps`` does not
    copy. Comparing the *products* closes that gap: register one function through
    the seam and its twin raw, then compare every field the SDK derived.

    ``arg_model`` is compared by JSON schema rather than by identity, because
    pydantic builds a distinct model class per registration; the schema is the
    thing that reaches the wire.
    """

    def answer(projectId: str, limit: int = 10) -> dict[str, str]:  # noqa: N803
        """A tool docstring the SDK turns into a description."""
        return {}

    through_the_seam = MCPServer("seam")
    raw = MCPServer("raw")
    _tool(through_the_seam, name="probe")(answer)
    raw.tool(name="probe")(answer)

    seamed_tool = through_the_seam._tool_manager.get_tool("probe")
    raw_tool = raw._tool_manager.get_tool("probe")
    assert seamed_tool is not None and raw_tool is not None

    # Every field the SDK models, rather than a list someone remembered to
    # extend. `fn` is necessarily different -- that is the whole change -- and
    # `fn_metadata` holds pydantic model *classes* built fresh per registration,
    # so it is compared through the schemas it derives instead.
    derived = set(type(seamed_tool).model_fields) - {"fn", "fn_metadata"}
    assert {"parameters", "is_async", "context_kwarg", "description"} <= derived, (
        "the SDK's Tool model no longer carries the fields this comparison exists "
        "to check; re-read it rather than trusting the loop below"
    )
    for field in sorted(derived):
        assert getattr(seamed_tool, field) == getattr(raw_tool, field), (
            f"the seam changed the SDK-derived `{field}`, so the published tool "
            f"contract is not the one the raw registration would have produced"
        )

    seamed_meta, raw_meta = seamed_tool.fn_metadata, raw_tool.fn_metadata
    assert seamed_meta.arg_model.model_json_schema() == raw_meta.arg_model.model_json_schema()
    assert seamed_meta.output_schema == raw_meta.output_schema
    assert seamed_meta.wrap_output == raw_meta.wrap_output

    # The control: the two really are different objects, so the equalities above
    # are comparing two derivations rather than one object with itself.
    assert seamed_tool is not raw_tool
    assert seamed_tool.fn is not raw_tool.fn


def _deferring_shapes() -> dict[str, object]:
    """Every callable shape that returns before its body runs.

    Built in a function rather than at module scope so the generator and
    async-generator definitions are not module-level statements pytest collects.
    """

    async def coroutine_function() -> str:  # pragma: no cover - only registered
        return "x"

    async def async_generator_function() -> AsyncIterator[str]:  # pragma: no cover
        yield "x"

    def generator_function() -> Iterator[str]:  # pragma: no cover
        yield "x"

    class AsyncCallable:
        async def __call__(self) -> str:  # pragma: no cover
            return "x"

    class AsyncGenCallable:
        async def __call__(self) -> AsyncIterator[str]:  # pragma: no cover
            yield "x"

    class GenCallable:
        def __call__(self) -> Iterator[str]:  # pragma: no cover
            yield "x"

    return {
        "coroutine function": coroutine_function,
        "async generator function": async_generator_function,
        "generator function": generator_function,
        "object with an async __call__": AsyncCallable(),
        "object with an async-generator __call__": AsyncGenCallable(),
        "object with a generator __call__": GenCallable(),
        "functools.partial of a coroutine function": functools.partial(coroutine_function),
    }


@pytest.mark.parametrize("shape", sorted(_deferring_shapes()))
def test_registering_a_deferring_callable_through_the_seam_is_refused(shape: str) -> None:
    """The guard, driven over every shape it claims -- not merely present.

    ``_forwarding``'s wrapper is synchronous, and its ``except`` arms only see
    what is raised *while it is on the stack*. Any callable that hands back an
    object and runs its body later escapes them, so the #491 conversion silently
    stops applying -- and only under mcp >= 2.1, where the un-converted refusal
    is withheld.

    The first version of this guard asked ``inspect.iscoroutinefunction`` alone,
    which is one of these seven. A generator function is not async at all; a
    callable *object* carries its deferral on ``type(fn).__call__`` rather than
    on the instance, so the instance answers ``False`` to every ``inspect``
    predicate asked of it directly.

    ``functools.partial`` is here for the message rather than the predicate: it
    has no ``__name__``, and a guard that built its refusal from ``fn.__name__``
    raised ``AttributeError`` from inside itself. That still refused the
    registration -- it failed closed -- but named nothing.
    """
    server = MCPServer("guard-probe")
    fn = _deferring_shapes()[shape]

    with pytest.raises(TypeError, match="defers its body"):
        _tool(server, name="probe")(fn)  # type: ignore[arg-type]


def test_a_plain_def_returning_a_coroutine_is_refused_at_call_time() -> None:
    """The fourth face, which no registration-time predicate can see.

    A plain ``def`` that *returns* a coroutine is indistinguishable from a
    well-behaved tool until it is called: its body has not run when
    ``_forwarding``'s ``except`` arms are on the stack, so they protect nothing,
    and the SDK does not await a synchronous tool's return value. Measured, the
    result was serialised as a *success* carrying ``<coroutine object ...>`` --
    a caller receiving a Python repr as knowledge.

    Chosen over recording it as a residual because the information exists at the
    one moment it is needed, in the wrapper, at no cost to any real tool: the
    five registered tools return ``dict``, and an awaitable return is never a
    legitimate result on this surface.
    """
    coroutine_returned: Any = None

    def looks_synchronous() -> Any:
        nonlocal coroutine_returned

        async def body() -> str:
            return "x"

        coroutine_returned = body()
        return coroutine_returned

    try:
        with pytest.raises(ToolError, match="un-awaited object"):
            _forwarding(looks_synchronous)()
    finally:
        if coroutine_returned is not None:
            coroutine_returned.close()  # or Python warns about it, and warnings are errors


def test_the_deferred_result_refusal_carries_nothing_but_the_tool_name() -> None:
    """The new refusal is a constant, like ``SEARCH_CAPACITY_REFUSAL`` before it.

    A refusal that varied with the request would be the
    "an error that fires for one input and not another" channel SEC-13 closes
    everywhere else on this surface. This one interpolates the tool function's
    own name and nothing more -- and a tool's name is what ``tools/list``
    already publishes.
    """
    assert DEFERRED_RESULT_REFUSAL.count("{") == 1
    assert "{tool}" in DEFERRED_RESULT_REFUSAL
    rendered = DEFERRED_RESULT_REFUSAL.format(tool="knowledge.search")
    assert "knowledge.search" in rendered
    for leaked in ("projectId", "query", "coroutine object", "Traceback"):
        assert leaked not in rendered


def test_every_tool_is_registered_through_the_one_seam() -> None:
    """No *decorator* in ``register`` names anything but ``_tool``.

    **This is the narrower of two pins, and its limits are the point.** It reads
    spelling: the decorator each tool carries. That catches a sixth tool added
    with ``@server.tool`` and nothing else. It does **not** catch a bypass inside
    ``_tool`` itself -- deleting ``_forwarding``'s application leaves every
    decorator identical, and the adversarial round confirmed the full suite
    stayed green (4632 passed) under exactly that mutation while the #491
    remedies were withheld again under mcp 2.1.1.

    The pin that closes that is
    ``tests/integration/test_mcp_tools.py::test_every_registered_tool_goes_through_the_forwarding_seam``,
    which asks the built server whether each ``Tool.fn`` is actually the
    wrapper. This one is kept because it fails *earlier* and names the offending
    decorator, which the runtime pin cannot do -- but the universality claim
    rests on the runtime pin, not on this.
    """
    source = pathlib.Path(inspect.getsourcefile(tools_module) or "").read_text(encoding="utf-8")
    tree = ast.parse(source)
    register = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "register"
    )

    decorators = [
        ast.unparse(decorator)
        for node in ast.walk(register)
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
        for decorator in node.decorator_list
    ]
    through_the_seam = [d for d in decorators if d.startswith("_tool(")]
    bypassing = [d for d in decorators if "server.tool" in d]

    assert bypassing == [], (
        f"these tools bypass `_forwarding`, so a refusal raised below the surface "
        f"is withheld from their callers under mcp >= 2.1: {bypassing}"
    )
    assert len(through_the_seam) == 5, (
        f"expected the five registered tools to go through `_tool`; found "
        f"{len(through_the_seam)}. If a tool was added or removed, update this "
        f"count deliberately -- it is what makes the assertion above meaningful."
    )


# -- AC-8's smuggling family: what the seam must and must not let through ------
#
# The seam's rule is one line -- convert `TheurianError`, leave everything else
# alone -- and every variant below is a way that line could be got wrong in a
# direction nobody would notice from the happy path. They are the *enumerated
# floor*, not the boundary: the adversarial round is expected to bring variants
# these four do not name, and the property to attack is always the same one --
# the seam decides by type and never by content.


def test_an_exception_that_is_also_a_theurian_error_forwards_by_design() -> None:
    """(b) Mixed ancestry resolves to "it is a TheurianError", deliberately.

    A class inheriting both ``TheurianError`` and something else is still a
    deliberate refusal that this codebase raised on purpose -- ancestry is not
    a smuggling channel, because writing the class *is* the decision to make it
    a refusal. ``StateDatabaseUnreadableError`` is already the shape this
    describes in spirit: a value error surfaced as a governed refusal.

    Pinned so the behaviour is a recorded choice rather than an accident of
    ``except`` ordering, and pinned with the same 2.0-parity equality as every
    other forwarded refusal -- mixed ancestry must not become a way to add text
    the wire never carried.
    """

    class MixedRefusalError(TheurianError, ValueError):
        pass

    original = MixedRefusalError("a governed refusal that is also a ValueError")

    def raises() -> None:
        raise original

    with pytest.raises(ToolError) as converted:
        _forwarding(raises)()

    assert str(converted.value) == str(original)
    assert isinstance(original, ValueError), "the premise of this test"


def test_the_seam_forwards_only_its_own_str_never_a_cause_or_context() -> None:
    """(c) A chained cause is not a licence to publish the cause's text.

    ``StateDatabaseUnreadableError`` exists precisely because the *cause* --
    ``Invalid isoformat string: '<the corrupted cell>'`` -- must not reach a
    caller, while the refusal wrapping it must. mcp 2.0.0 published
    ``str(exc)`` and never walked ``__cause__``; anything this seam adds from
    the chain is new information on the wire and, in that class's case, the
    exact bytes SEC-13 withholds.

    Both links are covered: ``__cause__`` (explicit ``raise ... from``) and
    ``__context__`` (implicit, set by raising inside an ``except`` block),
    because they are set by different syntax and a seam could easily walk one
    and not the other.
    """
    withheld_cell = "Invalid isoformat string: 'SECRET-CELL-CONTENTS'"

    def raises_with_cause() -> None:
        try:
            raise OSError(withheld_cell)
        except OSError as cause:
            raise StateDatabaseUnreadableError("ValueError") from cause

    def raises_with_context() -> None:
        try:
            raise OSError(withheld_cell)
        except OSError:
            raise StateDatabaseUnreadableError("ValueError")  # noqa: B904 -- implicit chain is the point

    for raises in (raises_with_cause, raises_with_context):
        with pytest.raises(ToolError) as converted:
            _forwarding(raises)()
        text = str(converted.value)
        assert text == str(StateDatabaseUnreadableError("ValueError")), (
            "the seam must publish the refusal's own text and nothing from its chain"
        )
        assert "SECRET-CELL-CONTENTS" not in text, (
            "the corrupted cell reached the wire through the exception chain -- "
            "the disclosure `StateDatabaseUnreadableError` exists to prevent"
        )


def test_an_oserror_raised_directly_stays_withheld() -> None:
    """(d) The infrastructure failure that is not a governed refusal.

    An ``OSError`` off a filesystem or socket call carries paths and errno text
    that nobody wrote for a caller to read. It is not a ``TheurianError``, so it
    is not converted, so mcp 2.1 withholds it -- and that is the intended
    outcome, not a gap. RED if the seam widens to ``except Exception``.
    """
    original = OSError(13, "Permission denied", "/Users/someone/.theurian/state/db.sqlite")

    def raises() -> None:
        raise original

    with pytest.raises(OSError) as escaped:
        _forwarding(raises)()

    assert escaped.value is original
    assert not isinstance(escaped.value, SdkToolError), (
        "an OSError converted here would be forwarded by mcp 2.1 with its path"
    )


def test_the_seam_decides_by_type_and_not_by_content() -> None:
    """The property the four variants above are each an instance of.

    Stated once, over a matrix, so a future variant is checked against the rule
    rather than against the list. A refusal's *text* -- including text shaped to
    look like a crash, or a crash shaped to look like a refusal -- must never
    move the decision.
    """
    disguised_refusal = ProjectError("Traceback (most recent call last): fake")
    disguised_crash = RuntimeError(
        "Project 'x' is not registered. Run `theurian project register`."
    )

    def raise_it(exc: BaseException) -> None:
        raise exc

    with pytest.raises(ToolError) as forwarded:
        _forwarding(lambda: raise_it(disguised_refusal))()
    assert str(forwarded.value) == str(disguised_refusal)

    with pytest.raises(RuntimeError) as withheld:
        _forwarding(lambda: raise_it(disguised_crash))()
    assert withheld.value is disguised_crash
