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

Nothing here asserts anything about message *content*. The class defines no
``__init__`` and no ``__str__``, which is itself pinned below: what a caller
reads is what the raise site built, and the messages themselves are pinned
where they are produced -- at the wire, through the SDK's re-raise, by
``tests/integration/test_unreadable_registry_surface.py`` and
``tests/integration/test_mcp_tools.py``.
"""

from __future__ import annotations

import pytest
from mcp.server.mcpserver.exceptions import ResourceError as SdkResourceError
from mcp.server.mcpserver.exceptions import ToolError as SdkToolError

from theurian.domain.errors import TheurianError
from theurian.mcp.tools import ToolError

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
