"""What the README's quick start tells you to install, against what the extras hold.

The quick start recommends ``uv tool install --python 3.13 'theurian[daemon]'`` and
says, as its reason, that a plain install leaves ``theurian daemon start`` failing
on ``ModuleNotFoundError: No module named 'uvicorn'``. That sentence was written
from a measurement -- a real PyPI install of the base distribution -- and a
measurement is not a guard. Nothing stopped someone moving ``uvicorn`` into the
base dependencies, or dropping it from ``daemon``, and leaving the README
asserting a failure that no longer happens.

**Only the settleable half is pinned here.** The quick start also carries two
claims this repository cannot decide: which versions exist on PyPI, and how uv
selects an interpreter. Those are properties of PyPI and of uv, they are written
as observations rather than as mechanisms, and no test here can hold them. What
*is* decidable from this tree is the packaging: whether ``daemon`` carries the
module the daemon entry point imports. That is what these tests hold, and it is
the link that makes the README's reason true.

The direction that matters is the one nobody expects: these go RED when the
packaging becomes *friendlier*. Adding ``uvicorn`` to the base dependencies is a
perfectly reasonable change, and it silently makes a published sentence false.
"""

from __future__ import annotations

import pathlib
import tomllib
from collections.abc import Mapping
from typing import Final, cast

REPO_ROOT: Final = pathlib.Path(__file__).resolve().parents[4]
PYPROJECT: Final = REPO_ROOT / "packages" / "theurian-core" / "pyproject.toml"
README: Final = REPO_ROOT / "README.md"
DAEMON_RUNNER: Final = (
    REPO_ROOT / "packages" / "theurian-core" / "src" / "theurian" / "daemon" / "runner.py"
)

#: The distribution the README's quick start installs. Held as a literal because
#: the README names it as one, and a rename that misses the README is the defect.
QUICK_START_SPEC: Final = "theurian[daemon]"

#: The module whose absence the README quotes as the failure. ``daemon/runner.py``
#: imports it at module scope, so the import error is what a user sees.
DAEMON_MODULE: Final = "uvicorn"


def _project() -> Mapping[str, object]:
    data = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    return cast(Mapping[str, object], data["project"])


def _extras() -> Mapping[str, list[str]]:
    return cast(Mapping[str, list[str]], _project()["optional-dependencies"])


def _base_dependencies() -> list[str]:
    return cast(list[str], _project()["dependencies"])


def _requirement_names(specs: list[str]) -> set[str]:
    """Distribution names from a requirement list, extras and pins stripped."""
    names = set()
    for spec in specs:
        head = spec.split(";")[0].strip()
        for separator in ("==", ">=", "<=", "~=", "!=", ">", "<"):
            head = head.split(separator)[0]
        names.add(head.split("[")[0].strip().lower())
    return names


def test_the_daemon_module_is_an_extra_and_not_a_base_dependency() -> None:
    """The README's stated reason for `[daemon]` rests on this being true.

    If ``uvicorn`` moves into ``dependencies``, a plain ``uv tool install
    theurian`` starts working for the daemon and the quick start's paragraph
    becomes a warning about nothing.
    """
    base = _requirement_names(_base_dependencies())
    assert DAEMON_MODULE not in base, (
        f"`{DAEMON_MODULE}` is now a base dependency, so a plain install can start "
        f"the daemon. README.md's quick start says it cannot -- update it."
    )
    daemon_extra = _requirement_names(_extras()["daemon"])
    assert DAEMON_MODULE in daemon_extra, (
        f"`{DAEMON_MODULE}` left the `daemon` extra. README.md names `[daemon]` as "
        f"what carries the daemon."
    )


def test_the_quick_start_extra_carries_the_daemon_module() -> None:
    """`[daemon]` is the spelling the README publishes, so it has to carry `uvicorn`."""

    daemon_extra = _requirement_names(_extras()["daemon"])
    assert DAEMON_MODULE in daemon_extra, (
        f"`{DAEMON_MODULE}` left the `daemon` extra, so `{QUICK_START_SPEC}` does "
        f"not install a daemon. README.md's quick start says it does."
    )


def test_the_daemon_entry_point_imports_the_module_the_readme_names() -> None:
    """Why the failure is an import error rather than a graceful message.

    ``runner.py`` imports at module scope, so the traceback the README quotes is
    what a user gets. If that becomes a handled error with a readable message,
    the README's quoted failure is out of date -- and #78 is where that lands.
    """
    source = DAEMON_RUNNER.read_text(encoding="utf-8")
    assert f"\nimport {DAEMON_MODULE}" in source, (
        f"`{DAEMON_RUNNER.name}` no longer imports `{DAEMON_MODULE}` at module "
        f"scope. README.md quotes the resulting ModuleNotFoundError verbatim."
    )


def test_the_readme_quick_start_names_the_spec_and_the_failure() -> None:
    """The other end of the link: the prose these tests are protecting.

    Without this, the packaging could stay exactly as pinned above while the
    README stopped saying any of it, and the module would guard nothing anyone
    reads.
    """
    readme = README.read_text(encoding="utf-8")
    assert QUICK_START_SPEC in readme, f"README.md no longer names `{QUICK_START_SPEC}`"
    assert DAEMON_MODULE in readme, (
        f"README.md no longer names `{DAEMON_MODULE}`, which is the failure the "
        f"quick start uses to justify `[daemon]`"
    )
