"""Commit a project's migrations so ``migrate apply`` accepts them (ADR-0034).

ADR-0034 makes ``migrate apply`` refuse a migration file that is not committed
-- tracked by git *and* byte-identical to ``HEAD``. Most harnesses in this
suite ``git init`` a throwaway repository and write a migration into
``.theurian/migrations/`` purely as *setup* for what they actually assert
(retrieval, purge, absence proofs, index state, the apply's own locking, and so
on). Once that check lands, an uncommitted setup apply would be refused.

Committing the migration (and any body files it references under
``.theurian/knowledge/``) before the apply is a behavioural no-op today -- the
apply still succeeds -- and keeps the setup green after the check lands. It does
not paper over the behaviour ADR-0034 ships: a test whose *subject* is applying
an uncommitted migration would be given the escape-hatch flag instead, not this
helper.

The commit is made with an explicit identity and ``commit.gpgsign=false`` so it
never depends on -- nor is broken by -- a developer's ambient ``~/.gitconfig``
(a global ``commit.gpgsign = true`` would otherwise make it fail). ``--allow-
empty`` lets it be called again after a re-apply with nothing new to stage.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

_IDENTITY = (
    "-c",
    "user.email=test@example.com",
    "-c",
    "user.name=Test",
    "-c",
    "commit.gpgsign=false",
)


def commit_migrations(root: Path | None = None, message: str = "apply migrations") -> None:
    """Stage everything under ``root`` and commit it, so the migration files are
    tracked and byte-identical to ``HEAD`` (ADR-0034's committed-check).

    ``root`` defaults to the current working directory -- the harnesses that
    drive the CLI in-process ``chdir`` into the project first, so ``migrate
    apply`` and this commit see the same tree without the caller naming it.

    ``git add -A`` is tolerant of the irregular entries a few harnesses plant
    (a symlink, a FIFO): git stages the regular migration file regardless, which
    is all the check reads. The commit itself is required to succeed.

    A no-op when ``root`` is not a git working tree, so a harness that drives
    ``migrate apply`` outside a repository -- which already refuses at
    ``resolve_context`` (ADR-0034 decision 3), before the committed-check -- is
    left exactly as it was.
    """
    root = Path.cwd() if root is None else Path(root)
    if not (root / ".git").exists():
        return
    subprocess.run(["git", "add", "-A"], cwd=root, capture_output=True, check=False)  # noqa: S607
    subprocess.run(  # noqa: S603
        ["git", *_IDENTITY, "commit", "-q", "--allow-empty", "-m", message],  # noqa: S607
        cwd=root,
        capture_output=True,
        check=True,
    )
