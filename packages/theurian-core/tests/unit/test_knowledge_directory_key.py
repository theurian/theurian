"""``knowledgeDirectory`` is withdrawn as a setting, and still accepted (#533).

The published schema carried ``knowledgeDirectory`` with a ``default`` of
``.theurian`` and no ``pattern`` for seven milestones, which reads as a knob: set
it, and the knowledge directory moves. Nothing honours it. ``ProjectPaths.of``
composes the directory from ``DEFAULT_KNOWLEDGE_DIRECTORY`` and the only caller
that passes the argument at all is ``cli/migration_pipeline.py``, which passes the
*registry's* value against a rehearsal tree -- never this file's.

**Two things had to be true at once, and they pull in opposite directions.** The
schema must stop advertising a setting that does not exist, and an operator who
wrote the key *because we published it* must not have their file start failing
validation for having believed us. Deleting the property does the first and
breaks the second: ``additionalProperties: false`` is deliberate throughout these
schemas, so a deleted property makes every file carrying it invalid. Measured
against this repository's own document, with the property deleted from a copy of
the schema::

    legacy-carrying: INVALID: Additional properties are not allowed
                              ('knowledgeDirectory' was unexpected)

So the landing is annotation-only: the ``default`` is gone -- that is the claim
being withdrawn -- and ``deprecated: true`` says what the key now is, while
``type: string`` is kept so that **no instance changes verdict**. The record of
why lives in the core changelog, where a correction to a published contract
belongs; this module is what holds the tree to it.

The same name is a *different, live* surface elsewhere and is deliberately out of
reach here: ``knowledgeDirectory`` is a field of every ``projects.json`` entry
(``application/project_service.py``) and of the ``project status`` payload
(``cli/commands.py``), pinned by ``tests/integration/test_wire_contract.py``.
Registry field, config key: one spelling, two contracts.

Marked ``unit`` and writes only under ``tmp_path``.
"""

from __future__ import annotations

import json
import pathlib
from typing import Any, Final

import pytest
from jsonschema import Draft202012Validator

from theurian.application.project_service import ProjectPaths
from theurian.domain.project import DEFAULT_KNOWLEDGE_DIRECTORY
from theurian.security import load_yaml_mapping
from theurian.security.project_config import (
    PROJECT_CONFIG_FILE,
    SecretScanPolicy,
    read_secret_scan_policy,
)

pytestmark = pytest.mark.unit

#: ``parents[4]`` is ``.../tests/unit/`` -> ``tests`` -> ``theurian-core`` ->
#: ``packages`` -> repo root, the reckoning every published-schema pin here uses.
REPO_ROOT: Final = pathlib.Path(__file__).resolve().parents[4]

PROJECT_CONFIG_SCHEMA: Final = REPO_ROOT / "schemas" / "config" / "project-config.schema.json"

EXAMPLE_CONFIG: Final = (
    REPO_ROOT / "examples" / "sample-project" / ".theurian" / PROJECT_CONFIG_FILE
)

#: The key this module is about, spelled once.
KEY: Final = "knowledgeDirectory"

#: A configuration written while the key was published as a setting, with the
#: value the schema's own ``default`` told its author to expect.
LEGACY_CARRYING: Final = f"""\
apiVersion: theurian.dev/v1
projectId: legacy-carrier
{KEY}: .theurian
security:
  secretScan: warn
"""

#: The same file with the line deleted, which is the remedy the changelog names.
LEGACY_ABSENT: Final = """\
apiVersion: theurian.dev/v1
projectId: legacy-carrier
security:
  secretScan: warn
"""


def _published_key() -> dict[str, Any]:
    """The subschema the wheel publishes for the key, read rather than transcribed."""
    schema = json.loads(PROJECT_CONFIG_SCHEMA.read_text(encoding="utf-8"))
    properties = schema["properties"]

    assert KEY in properties, (
        f"`{KEY}` is no longer a published property. Deleting it is the change "
        f"`test_a_configuration_written_while_the_key_was_a_setting_still_validates` "
        f"exists to refuse: `additionalProperties: false` turns the deletion into a "
        f"validation failure for every operator file that carries the key, which is "
        f"the one thing withdrawing the claim was not allowed to cost (#533)."
    )
    published: dict[str, Any] = properties[KEY]
    return published


def _document(text: str) -> dict[str, Any]:
    """A config document as the product's own loader hands it over.

    Parsed with ``load_yaml_mapping`` rather than written as a dict literal, so
    the thing validated here is the thing an operator's file becomes -- the same
    reckoning ``test_examples.py`` uses on the bundled example.
    """
    return load_yaml_mapping(text)


def test_the_schema_publishes_the_withdrawn_key_without_a_default() -> None:
    """RED means the key reads as a setting again.

    A ``default`` on a published property is an instruction: it tells a reader
    what they get if they say nothing, which is only meaningful for a key
    somebody reads. There is no reader, so the ``default`` was the whole of the
    false claim -- and ``deprecated`` is JSON Schema's own word for what is left,
    which schema-aware editors render rather than offer.
    """
    published = _published_key()

    assert "default" not in published, (
        f"`{KEY}` publishes a default again: {published!r}. A default announces "
        f"what an unset key selects, and nothing selects anything from this one -- "
        f"`ProjectPaths.of` composes the directory from `DEFAULT_KNOWLEDGE_DIRECTORY` "
        f"(#533). If a reader has been added, this module is where the posture "
        f"changes and the changelog is where the correction gets stated."
    )
    assert published.get("deprecated") is True, (
        f"`{KEY}` is no longer marked deprecated: {published!r}. The key is kept "
        f"accepted so an operator's file does not break; `deprecated` is what "
        f"stops it being offered to the next author as a setting."
    )


@pytest.mark.parametrize(
    ("label", "text"),
    [("carrying the key", LEGACY_CARRYING), ("with the line deleted", LEGACY_ABSENT)],
)
def test_a_configuration_written_while_the_key_was_a_setting_still_validates(
    label: str, text: str
) -> None:
    """An honest schema does not punish past honesty in its users.

    An operator who set this key set it because the contract published it, with
    the contract's own default as the value. Withdrawing the claim must leave
    their file valid -- and under ``additionalProperties: false`` that is a
    property of the *property still being published*, not of anything the reader
    does. Measured with the property deleted from a copy of this schema: the
    carrying document comes back ``Additional properties are not allowed
    ('knowledgeDirectory' was unexpected)`` while the deleted-line document stays
    valid, which is the asymmetry this row refuses.
    """
    schema = json.loads(PROJECT_CONFIG_SCHEMA.read_text(encoding="utf-8"))

    errors = sorted(Draft202012Validator(schema).iter_errors(_document(text)), key=str)

    assert not errors, (
        f"a configuration {label} no longer validates: {[error.message for error in errors]}.\n\n"
        f"`{KEY}` was published as a setting for seven milestones, so files carrying "
        f"it exist and were correct when they were written. Withdrawing the claim is "
        f"allowed; refusing the file that believed it is not (#533)."
    )


@pytest.mark.parametrize(
    ("label", "stated"),
    [
        ("the schema's own former default", ".theurian"),
        ("another directory name", "knowledge-base"),
        # A name with spaces is the value that would matter first if the key were
        # ever honoured: `derived_escape_remedy` renders the directory into an
        # `rm` command, and an unquoted name with a space splits into three wrong
        # paths there. Nothing honours the key, so this row asserts the path does
        # not move -- and it is the tripwire that reddens on the day it does,
        # while the quoting is still one line away (#533).
        ("a name that would need quoting in a rendered command", "my knowledge dir"),
    ],
)
def test_a_config_naming_another_knowledge_directory_moves_no_path(
    tmp_path: pathlib.Path, label: str, stated: str
) -> None:
    """Given a config that names a knowledge directory, the product uses ``.theurian``.

    The behavioural half of the withdrawal, and the half that would go RED the day
    somebody wires the key: ``ProjectPaths`` is where every knowledge path in the
    product comes from, and it takes the directory's name from a constant.

    A characterisation test on purpose. It cannot fail against the tree it was
    written for -- that is the claim -- so what it holds is the *next* change:
    honouring this key moves ``knowledge_dir`` here, and the failure message is
    where the quoting hazard is waiting.
    """
    root = tmp_path / "demo"
    (root / str(DEFAULT_KNOWLEDGE_DIRECTORY)).mkdir(parents=True)
    (root / str(DEFAULT_KNOWLEDGE_DIRECTORY) / PROJECT_CONFIG_FILE).write_text(
        f"apiVersion: theurian.dev/v1\nprojectId: demo\n{KEY}: {stated}\n", encoding="utf-8"
    )

    paths = ProjectPaths.of(root)

    assert paths.knowledge_dir == paths.root / str(DEFAULT_KNOWLEDGE_DIRECTORY), (
        f"a config naming {label} moved the knowledge directory to "
        f"{paths.knowledge_dir}. If `{KEY}` has been wired through, three things "
        f"land together: a `pattern` on the published key so the name is bounded, "
        f"`shlex.quote` on the path `derived_escape_remedy` renders into an `rm` "
        f"command, and the changelog correction saying the key is a setting again "
        f"(#533)."
    )


def test_the_withdrawn_key_does_not_change_what_the_config_reader_answers(
    tmp_path: pathlib.Path,
) -> None:
    """The other half of "behaviour is unchanged": the one reader the file has.

    ``security/project_config.py`` is the only module in ``src/`` that opens
    ``.theurian/config.yaml`` (ADR-0027 decision 3), so "an existing config still
    carrying the key behaves as it did" is settled by asking it, with the key and
    without, for the same file.
    """
    answers = []
    for index, text in enumerate((LEGACY_CARRYING, LEGACY_ABSENT)):
        root = tmp_path / f"project-{index}"
        (root / str(DEFAULT_KNOWLEDGE_DIRECTORY)).mkdir(parents=True)
        paths = ProjectPaths.of(root)
        paths.config.write_text(text, encoding="utf-8")
        answers.append(read_secret_scan_policy(paths.root, paths.config))

    assert answers == [SecretScanPolicy.WARN, SecretScanPolicy.WARN], (
        f"the reader answered {answers} for one file with `{KEY}` and one without. "
        f"The key has no reader, so the two documents state the same policy; a "
        f"difference means the file's meaning now depends on a line the contract "
        f"has withdrawn (#533)."
    )


def test_the_bundled_example_does_not_teach_the_withdrawn_key() -> None:
    """OSS-13: the example is copied, so it must not teach a key that does nothing.

    ``test_examples.py::test_config_matches_its_schema`` cannot catch this, and
    that is the point of keeping the key accepted: the example carrying it is
    still *valid*. Valid and instructive are different questions, and this is the
    second one.
    """
    lines = [
        line
        for line in EXAMPLE_CONFIG.read_text(encoding="utf-8").splitlines()
        if line.strip().startswith(f"{KEY}:")
    ]

    assert not lines, (
        f"{EXAMPLE_CONFIG.relative_to(REPO_ROOT)} sets `{KEY}` ({lines}). The key is "
        f"accepted so that nobody's existing file breaks, not so that the example "
        f"teaches it to the next reader -- a reader who copies this file copies "
        f"the line and believes the directory is theirs to name (#533)."
    )
