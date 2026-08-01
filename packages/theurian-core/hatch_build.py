"""Build hook that ships the published JSON Schemas inside the wheel.

The migration loader validates against these at runtime, so an installed
``theurian`` that lacks them cannot read a migration at all.

The schemas live at the repository root because they are the shared contract
between Core and every client (ADR-0001) -- duplicating them under the package
would create a second source of truth that drifts.

That leaves a packaging problem specific to monorepos: ``uv build`` builds an
sdist from the source tree, then builds the wheel *from the sdist*. A static
``force-include`` of ``../../schemas`` works for the first and fails for the
second, because the sdist has no parent repository around it. This hook resolves
the location per build instead of assuming one.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from hatchling.builders.hooks.plugin.interface import BuildHookInterface

#: Presence of this file proves we found the real schema directory rather than
#: an empty one left behind by a partial checkout.
_SENTINEL = Path("migrations") / "migration.schema.json"

#: Where the schemas land inside the distribution.
_TARGET = "theurian/schemas"


class CustomBuildHook(BuildHookInterface):  # type: ignore[type-arg]
    """Locate ``schemas/`` and force-include it under the package."""

    def initialize(self, version: str, build_data: dict[str, Any]) -> None:  # noqa: ARG002
        root = Path(self.root)

        candidates = (
            # Building from the monorepo source tree.
            root.parent.parent / "schemas",
            # Building the wheel from an unpacked sdist. This hook also runs for
            # the sdist target, so the schemas are already at the layout below --
            # the same constant is used for both, which is what keeps the two
            # build paths from disagreeing.
            root / _TARGET,
        )

        for candidate in candidates:
            if (candidate / _SENTINEL).is_file():
                build_data.setdefault("force_include", {})[str(candidate)] = _TARGET
                return

        looked = ", ".join(str(c) for c in candidates)
        msg = (
            f"Cannot find the published JSON Schemas. Looked in: {looked}. "
            f"The wheel would install a theurian that cannot validate migrations, "
            f"so this build is failed deliberately rather than shipping it."
        )
        raise FileNotFoundError(msg)
