"""The environment a spawned ``gh`` runs under, constructed and closed (ADR-0030 clause 4).

**Constructed, never inherited and never merely scrubbed.** A scrub is a
blocklist over a set this adapter does not control, and it would have to be right
about every variable ``gh`` and its transport stack read. ADR-0030 measured three
independent inputs that move the destination -- ``GH_HOST`` (run B),
``HTTPS_PROXY`` past a pinned ``--hostname`` (run C), and the ``gh`` config file
reached through any of three locating variables (runs D-F) -- so the defence
inverts the burden: the child gets exactly the rows below and nothing else.

**Three admission grounds, because the table has three kinds of value**, and each
row's ground is read off its own ``Value`` rather than asserted over the table:

* **forwarded by value** -- ``HOME``, ``GH_CONFIG_DIR``, ``XDG_CONFIG_HOME``.
  Admitted on **necessity**: ``gh`` cannot locate the operator's persisted login
  without them. They are also the rows that reach the config file, which is the
  reach ``transport_guard`` reduces and ADR-0030 records the residual of.
* **set to the literal ``1``** -- ``NO_COLOR``, ``GH_NO_UPDATE_NOTIFIER``,
  ``GH_PROMPT_DISABLED``, ``GH_NO_EXTENSION_UPDATE_NOTIFIER``. A constant
  carrying no parent data, admitted on a named operational property. Two of them
  *remove* an outbound request ``gh`` would otherwise make on its own.
* **set by Theurian to a fixed value** -- ``PATH``. The load-bearing half is
  **never derived from the parent's**: building the child's ``PATH`` by
  filtering or prepending to the parent's would honour every other word of the
  rule while letting the parent choose the helper binaries ``gh`` runs.

**Nothing else, and the tokens in particular.** ``GH_TOKEN``, ``GITHUB_TOKEN``,
``GH_ENTERPRISE_TOKEN`` and ``GITHUB_ENTERPRISE_TOKEN`` would enter as
*forwarded by value* and fail that ground: ``gh`` finds the operator's persisted
login without them. So identity never comes from a caller's environment.
Headless environment-token authentication is a recorded non-goal (ADR-0030),
not an omission.

**An empty string is a present key, not an absent one.** ``gh`` treats an empty
config-locating variable as absent and falls through to the next in its
precedence chain, so the two are behaviourally the same *to gh* and are not the
same *mapping*. Forwarding by value means exactly that: the parent's value
crosses unchanged, empty string included, and a variable the parent does not set
is absent from the child. ``transport_guard`` resolves the same chain with the
same empty-is-absent rule, so the check and the child read one directory.

The expected mapping is written out **test-side** in
``tests/unit/test_gh_child_environment.py`` rather than imported from here. A
test that reads this module's constants moves with them, so mutating one changes
both sides and the test survives; a test-side expectation is what makes it
killable, and its companion demonstrates that.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Final

#: The child's ``PATH``: a fixed literal this project chose, never derived from
#: the parent's.
#:
#: ``gh`` shells out -- ``git``, and on macOS the ``security`` binary its
#: credential store reaches through -- and an inherited ``PATH`` would let the
#: parent environment choose those. The value is the POSIX system locations plus
#: ``/usr/local/bin``: ``git`` ships at ``/usr/bin/git`` and ``security`` at
#: ``/usr/bin/security`` on macOS, and ``git`` at ``/usr/bin/git`` on mainstream
#: Linux.
#:
#: **Its recorded limit**: a helper installed only somewhere else -- a Homebrew
#: ``git`` at ``/opt/homebrew/bin`` with no system ``git`` beside it -- is not
#: found by the child. That is the price of not letting the parent choose, and it
#: is stated rather than discovered: the failure is a graded ``gh`` refusal
#: carrying the child's own stderr, not a silent wrong answer.
FIXED_PATH: Final = "/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"

#: The variables whose parent value crosses into the child unchanged. Admitted on
#: necessity: they are how ``gh`` locates its configuration directory and the
#: operator's persisted login.
FORWARDED_BY_VALUE: Final[tuple[str, ...]] = (
    "GH_CONFIG_DIR",
    "HOME",
    "XDG_CONFIG_HOME",
)

#: The variables set to the literal ``1``, each with its own operational reason.
#:
#: * ``NO_COLOR`` -- machine-readable output.
#: * ``GH_NO_UPDATE_NOTIFIER`` -- **set, not merely absent**: without it ``gh``
#:   performs its own 24-hour release check, an outbound request no argument
#:   vector of ours chose.
#: * ``GH_PROMPT_DISABLED`` -- a spawned ``gh`` must never block on an
#:   interactive prompt.
#: * ``GH_NO_EXTENSION_UPDATE_NOTIFIER`` -- the same class as the update
#:   notifier: a check nobody asked for.
SET_TO_ONE: Final[tuple[str, ...]] = (
    "GH_NO_EXTENSION_UPDATE_NOTIFIER",
    "GH_NO_UPDATE_NOTIFIER",
    "GH_PROMPT_DISABLED",
    "NO_COLOR",
)


def child_environment(parent: Mapping[str, str]) -> dict[str, str]:
    """The whole environment a spawned ``gh`` receives.

    Args:
        parent: The environment to forward the three config-locating variables
            *from*. Taken as an argument rather than read from ``os.environ``
            here so a test can drive the construction against a synthetic parent
            without touching the process it runs in.

    Returns:
        A fresh mapping: the four literals, the fixed ``PATH``, and whichever of
        the three forwarded variables the parent sets -- by value, empty string
        included. Nothing else, ever.
    """
    child = dict.fromkeys(SET_TO_ONE, "1")
    child["PATH"] = FIXED_PATH
    for name in FORWARDED_BY_VALUE:
        if name in parent:
            child[name] = parent[name]
    return child
