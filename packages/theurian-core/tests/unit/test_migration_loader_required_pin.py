"""An ``upsertRevision`` that declares no ``contentSha256`` is refused at load.

ADR-0027 decision 1, closing [#210](https://github.com/theurian/theurian/issues/210).
The pin is what freezes a body: FR-K5 checksums the migration YAML, and that
checksum does not cover the file the YAML points at. While the field was
optional the loader adopted whatever bytes the body held at load time, so an
out-of-band edit to an unpinned body was invisible -- ``migrate validate``
reported ``valid: true`` at exit 0 and a second ``migrate apply`` recorded the
edited bytes under the same revision id.

``tests/unit/test_schemas.py`` holds the published half of the requirement --
that ``contentSha256`` is in ``$defs.opUpsertRevision``'s ``required``, and that
the schema refuses a document without it. This file holds the half a schema
cannot state: that the *loader* a user's ``migrate validate`` runs through
reaches that refusal, on a real file, and says which operation of which file is
at fault.

**The refusal does not name the field, and that is measured rather than
assumed.** An operation is validated through ``$defs.operation``'s ``oneOf``
over the fourteen operation types, so dropping a required field does not produce
a "required property" error: it stops the object matching ``opUpsertRevision``
and surfaces as a ``oneOf`` non-match. ``docs/protocol/migrations.md`` ("Why the
pin is required rather than recommended") quotes that message, from the same
migration id, filename and ``contentFile`` this file's fixture uses, so the two
can be diffed by eye. **If the message ever improves to name the field, that doc
quote is false and changes with it** -- which is what the negative assertion
below is for.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from migration_fixtures import body_pin

from theurian.cli.context import schema_root as real_schema_root
from theurian.domain.errors import MigrationError
from theurian.infrastructure.filesystem.migration_loader import load_migrations

pytestmark = pytest.mark.unit

_MIGRATION_ID = "01K1AAAAAA01234567890ABCDE"
_REVISION_ID = "01K1AAAREV01234567890ABCDE"

#: Named to match the console transcript in ``docs/protocol/migrations.md``.
_FILENAME = f"{_MIGRATION_ID}-auth.yaml"

_CONTENT_FILE = "../knowledge/architecture/auth-policy.md"

_BODY = "# Authentication policy\n\nEvery call carries a signed token.\n"


def _migration(*, pinned: bool) -> str:
    """The same document twice, differing only in the line under test.

    Two operations, so the ``operations/1`` in the refusal is a real index into
    the document rather than the only place it could have pointed.
    """
    pin = f"    contentSha256: {body_pin(_BODY)}\n" if pinned else ""
    return f"""apiVersion: theurian.dev/v1
id: {_MIGRATION_ID}
createdAt: 2026-08-02T10:00:00+09:00
author: engineer@example.com
operations:
  - op: createItem
    itemId: architecture.auth-policy
    kind: architecture
    namespace: backend
    owner: platform-team
  - op: upsertRevision
    itemId: architecture.auth-policy
    revisionId: {_REVISION_ID}
    contentFile: {_CONTENT_FILE}
{pin}    metadata:
      title: Authentication policy
      contentType: text/markdown
      kind: architecture
      namespace: backend
      status: approved
      owner: platform-team
"""


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """A project holding the body the migration names, and nothing else.

    The body is written in both cases. A refusal driven by a *missing* body
    would be :class:`MigrationContentUnreadableError` from a later call site,
    which is a different fault reported by a different branch.
    """
    root = tmp_path / "project"
    (root / ".theurian" / "migrations").mkdir(parents=True)
    knowledge = root / ".theurian" / "knowledge" / "architecture"
    knowledge.mkdir(parents=True)
    (knowledge / "auth-policy.md").write_text(_BODY, encoding="utf-8")
    return root


def _write(root: Path, *, pinned: bool) -> Path:
    migrations_dir = root / ".theurian" / "migrations"
    (migrations_dir / _FILENAME).write_text(_migration(pinned=pinned), encoding="utf-8")
    return migrations_dir


def test_a_revision_that_declares_no_body_pin_is_refused_at_load(project: Path) -> None:
    """The refusal ADR-0027 decision 1 bought, driven through the real loader.

    Goes RED if ``contentSha256`` leaves ``$defs.opUpsertRevision``'s
    ``required``: the document then validates and ``load_migrations`` returns a
    migration set, so nothing raises at all.

    The message assertions pin what a reader is given -- which file, which
    operation, and the failing keyword -- against the transcript
    ``docs/protocol/migrations.md`` publishes. They stop short of the ``oneOf``
    expectation's full text, which is a bounded render of all fourteen operation
    ``$ref``s and would move for reasons that have nothing to do with the pin.

    The echoed operation's key order is ``reprlib``'s, which sorts, so this does
    not depend on the order the fields were authored in or on YAML's parse order.
    """
    migrations_dir = _write(project, pinned=False)

    with pytest.raises(MigrationError) as excinfo:
        load_migrations(project, migrations_dir, real_schema_root())

    message = str(excinfo.value)
    assert message.startswith(
        f"{_FILENAME} is invalid at operations/1: does not satisfy 'oneOf' "
        f"(expected [{{'$ref': '#/$defs/opCreateItem'}}, "
    ), f"the refusal no longer opens the way docs/protocol/migrations.md quotes it: {message}"
    assert f"; the value there is {{'contentFile': {_CONTENT_FILE!r}, " in message, (
        "the reader is shown the operation that failed, not only its index"
    )
    assert "contentSha256" not in message, (
        "the refusal now names the missing field. That is an improvement, and it "
        "makes docs/protocol/migrations.md false: the section 'Why the pin is "
        "required rather than recommended' tells the reader the refusal arrives "
        "as a `oneOf` failure rather than a 'required property' message, and "
        "quotes it. Update that section in the same change."
    )


def test_the_same_revision_loads_once_it_pins_its_body(project: Path) -> None:
    """The control that makes the refusal above evidence about the pin.

    Without it, ``pytest.raises(MigrationError)`` passes for any reason the
    document is invalid -- a typo in the fixture's metadata, a ULID with an
    ``I`` in it, an ``apiVersion`` that moved -- and the test would report the
    tightening working while measuring something else entirely. The two
    documents differ by one line, so a load here and a refusal there isolates
    that line.
    """
    migrations_dir = _write(project, pinned=True)

    loaded = load_migrations(project, migrations_dir, real_schema_root())

    assert [str(migration.migration_id) for migration in loaded.migration_set] == [_MIGRATION_ID]
