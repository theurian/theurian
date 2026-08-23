"""The loader always sets ``content_identity`` on every ``upsertRevision`` (#210).

The migration loader is the sole *production* constructor of
:class:`~theurian.domain.migration.UpsertRevision`, and the body-sharing guards'
None-skip rests on that: an operation reaching
:func:`~theurian.application.migration_body_guards.refuse_duplicate_content_files`
with ``content_identity is None`` is skipped rather than compared, on the
reasoning that no gate ever sees one -- the loader takes the identity from the
same ``stat`` that read the body, so a loaded operation always carries it.

This pins that reasoning at its source. If the loader ever stops setting the
identity -- a new construction path, a refactor that drops the ``stat`` -- the
skip becomes a live disclosure hole (a second revision's shared body slips past
the refusal as a None-carrying operation). This test goes red at the loader
then, not at the leak much later.

The equality assertion also fixes the identity to the body's *real*
``(st_dev, st_ino)``: a loader that substituted a placeholder device
(``(0, st_ino)``) would fail here without needing a second filesystem to
disagree with the first.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from migration_fixtures import body_pin

from theurian.cli.context import schema_root as real_schema_root
from theurian.domain.migration import UpsertRevision
from theurian.infrastructure.filesystem.migration_loader import load_migrations

pytestmark = pytest.mark.unit

_FIRST_BODY = "# First\n\nThe first body.\n"
_SECOND_BODY = "# Second\n\nThe second body.\n"

#: Two items, each revised from its own body file, so "every produced
#: ``UpsertRevision``" is more than one operation. ``contentFile`` resolves
#: relative to the migration file, so ``../knowledge/<name>.md`` lands under the
#: project root the loader is given as its containment boundary.
_TWO_UPSERTS = f"""apiVersion: theurian.dev/v1
id: 01K1DDDDDD01234567890ABCDE
createdAt: 2026-08-02T10:00:00+09:00
author: engineer@example.com
operations:
  - op: createItem
    itemId: architecture.first
    kind: architecture
    namespace: backend
    owner: platform-team
  - op: upsertRevision
    itemId: architecture.first
    revisionId: 01K1DDDRV101234567890ABCDE
    contentFile: ../knowledge/first.md
    contentSha256: {body_pin(_FIRST_BODY)}
    metadata:
      title: First
      contentType: text/markdown
      kind: architecture
      namespace: backend
      status: approved
      owner: platform-team
      trustLevel: reviewed
      sourceAnchors:
        - provider: git
          sourceUri: git://demo/first.md
  - op: createItem
    itemId: architecture.second
    kind: architecture
    namespace: backend
    owner: platform-team
  - op: upsertRevision
    itemId: architecture.second
    revisionId: 01K1DDDRV201234567890ABCDE
    contentFile: ../knowledge/second.md
    contentSha256: {body_pin(_SECOND_BODY)}
    metadata:
      title: Second
      contentType: text/markdown
      kind: architecture
      namespace: backend
      status: approved
      owner: platform-team
      trustLevel: reviewed
      sourceAnchors:
        - provider: git
          sourceUri: git://demo/second.md
"""


def _project_with_two_bodies(tmp_path: Path) -> Path:
    root = tmp_path / "project"
    (root / ".theurian" / "migrations").mkdir(parents=True)
    (root / ".theurian" / "knowledge").mkdir(parents=True)
    (root / ".theurian" / "knowledge" / "first.md").write_text(_FIRST_BODY)
    (root / ".theurian" / "knowledge" / "second.md").write_text(_SECOND_BODY)
    (root / ".theurian" / "migrations" / "01K1DDDDDD01234567890ABCDE-two.yaml").write_text(
        _TWO_UPSERTS
    )
    return root


def test_the_loader_sets_a_real_filesystem_identity_on_every_upsert(tmp_path: Path) -> None:
    root = _project_with_two_bodies(tmp_path)

    loaded = load_migrations(root, root / ".theurian" / "migrations", real_schema_root())

    upserts = [
        operation
        for migration in loaded.migration_set
        for operation in migration.operations
        if isinstance(operation, UpsertRevision)
    ]
    assert len(upserts) == 2, "both revisions loaded, or the corpus below proves nothing"

    for operation in upserts:
        identity = operation.content_identity
        assert identity is not None, (
            f"{operation.revision_id} loaded with no content_identity; the None-skip's safety "
            f"rests on the loader never producing this"
        )
        assert operation.resolved_content_path is not None, "and its resolved path, for the stat"
        body_stat = (root / operation.resolved_content_path).stat()
        assert identity == (body_stat.st_dev, body_stat.st_ino), (
            "the identity is the resolved body's real (st_dev, st_ino), not a placeholder"
        )
