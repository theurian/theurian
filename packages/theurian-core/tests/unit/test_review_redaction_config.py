"""R-12's ingestion-time redaction switch, read from the file (ADR-0030 decision 3).

``providers.review.redactParticipantNames`` selects whether review ingestion
replaces a participant's display name before a record becomes a file. This module
drives the *reader* only: what the file says, and what happens when it says
something that is not a boolean. What a ``True`` then does to a record belongs to
the landing gate and is driven in ``tests/unit/test_review_landing_gate.py``.

Three rules, and they are not one rule:

* **Absent is off.** No file, no ``providers`` block, no ``review`` block or no
  key: the answer is ``False``, which is what the schema's ``default`` publishes.
  The direction matters -- redacting by default would drop the names a review's
  evidence is about, silently, on projects that never asked.
* **A non-boolean refuses.** YAML 1.1 already reads a bare ``yes``, ``on`` and
  ``true`` as ``True``, so a value that arrives here as anything else is a quoted
  spelling or another type entirely; guessing which of two values such an operator
  meant would turn a privacy control on or off for somebody who wrote neither.
* **Nothing escapes untranslated.** Every refusal carries a ``remedy``, for the
  reason :mod:`theurian.security.project_config` records about ``{error,
  remedy}`` documents.

Marked ``unit`` and writes only under ``tmp_path``.
"""

from __future__ import annotations

import json
import pathlib
from typing import Final

import pytest

from theurian.domain.errors import ProjectConfigError
from theurian.security.project_config import (
    PROJECT_CONFIG_FILE,
    REVIEW_REDACT_PARTICIPANT_NAMES_KEY,
    read_review_participant_redaction,
)

pytestmark = pytest.mark.unit

#: ``parents[4]`` is ``.../tests/unit/`` -> ``tests`` -> ``theurian-core`` ->
#: ``packages`` -> repo root, where the published schemas live.
REPO_ROOT = pathlib.Path(__file__).resolve().parents[4]

PROJECT_CONFIG_SCHEMA = REPO_ROOT / "schemas" / "config" / "project-config.schema.json"

#: Where the switch lives in the published schema.
_SCHEMA_POINTER: Final[tuple[str, ...]] = (
    "properties",
    "providers",
    "properties",
    "review",
    "properties",
    REVIEW_REDACT_PARTICIPANT_NAMES_KEY,
)


def _project(tmp_path: pathlib.Path, body: str | None) -> tuple[pathlib.Path, pathlib.Path]:
    """A project root and its ``config.yaml``, written only when ``body`` is given."""
    knowledge = tmp_path / ".theurian"
    knowledge.mkdir()
    config = knowledge / PROJECT_CONFIG_FILE
    if body is not None:
        config.write_text(body, encoding="utf-8")
    return tmp_path, config


def _stating(value: str) -> str:
    """A configuration file whose redaction switch is spelled ``value``."""
    return (
        "apiVersion: theurian.dev/v1\nproviders:\n  review:\n"
        f"    {REVIEW_REDACT_PARTICIPANT_NAMES_KEY}: {value}\n"
    )


def _subschema() -> dict[str, object]:
    """The published subschema for the switch, read rather than transcribed."""
    node: object = json.loads(PROJECT_CONFIG_SCHEMA.read_text(encoding="utf-8"))
    for step in _SCHEMA_POINTER:
        assert isinstance(node, dict), f"the schema has no `{'/'.join(_SCHEMA_POINTER)}`"
        node = node[step]
    assert isinstance(node, dict), "the switch is not published as a subschema"
    return node


def test_the_key_this_module_reads_is_the_one_the_schema_publishes() -> None:
    """RED means the contract and the reader disagree about the key's name or type.

    Nothing validates ``.theurian/config.yaml`` against the published schema at
    run time, so a key renamed on one side alone is a setting an operator writes
    and nothing reads. The name comes from the reader's own constant and the type
    from the schema file, so neither is a transcription.
    """
    published = _subschema()

    assert published["type"] == "boolean", (
        f"the schema publishes `{REVIEW_REDACT_PARTICIPANT_NAMES_KEY}` as "
        f"{published['type']!r}, and the reader refuses everything that is not a "
        "boolean. Move both in the same change."
    )


def test_the_published_default_is_the_one_an_absent_key_selects(tmp_path: pathlib.Path) -> None:
    """A ``default`` is honest only where a test pins it to what the product does.

    The schema's root description says exactly that of every ``default`` it
    publishes, so this is the pin that entitles this one to be there: the value in
    the contract and the value an unconfigured project gets are compared rather
    than assumed equal.
    """
    root, config = _project(tmp_path, "apiVersion: theurian.dev/v1\n")

    assert _subschema()["default"] == read_review_participant_redaction(root, config)


@pytest.mark.parametrize(
    ("label", "body"),
    (
        ("no file at all", None),
        ("an empty file", ""),
        ("comments only", "# nothing configured yet\n"),
        ("no providers block", "apiVersion: theurian.dev/v1\n"),
        ("an empty providers block", "apiVersion: theurian.dev/v1\nproviders:\n"),
        (
            "no review block",
            "apiVersion: theurian.dev/v1\nproviders:\n  embedding:\n    adapter: x\n",
        ),
        ("an empty review block", "apiVersion: theurian.dev/v1\nproviders:\n  review:\n"),
        (
            "a review block with no switch",
            "apiVersion: theurian.dev/v1\nproviders:\n  review:\n    adapter: none\n",
        ),
    ),
)
def test_a_project_that_states_nothing_does_not_redact(
    tmp_path: pathlib.Path, label: str, body: str | None
) -> None:
    """Every way of saying nothing means the same thing, and it is "off"."""
    root, config = _project(tmp_path, body)

    assert read_review_participant_redaction(root, config) is False, label


@pytest.mark.parametrize(
    ("spelling", "expected"),
    (
        ("true", True),
        ("True", True),
        ("yes", True),
        ("on", True),
        ("false", False),
        ("False", False),
        ("no", False),
        ("off", False),
    ),
)
def test_every_boolean_spelling_yaml_resolves_is_read_back(
    tmp_path: pathlib.Path, spelling: str, expected: bool
) -> None:
    """The positive control, and it is wider than the schema's two words.

    PyYAML implements YAML 1.1, whose implicit resolver reads ``yes``, ``on`` and
    ``true`` as ``True`` and ``no``, ``off`` and ``false`` as ``False``. An
    operator who writes ``yes`` has written a boolean whatever the schema's
    examples say, so refusing it would refuse a value the loader already resolved
    -- the opposite of ``secretScan``'s problem one block over, where the loader
    resolves a *string* the enum publishes into a boolean nobody meant.
    """
    root, config = _project(tmp_path, _stating(spelling))

    assert read_review_participant_redaction(root, config) is expected


@pytest.mark.parametrize(
    ("label", "value"),
    (
        ("a quoted true", '"true"'),
        ("a quoted false", '"false"'),
        ("a quoted yes", '"yes"'),
        ("the integer one", "1"),
        ("the integer zero", "0"),
        ("a word that is not a boolean", "redact"),
        ("a list", "[true]"),
        ("a mapping", "{on: true}"),
        ("a null", "null"),
    ),
)
def test_a_value_that_is_not_a_boolean_is_refused_rather_than_coerced(
    tmp_path: pathlib.Path, label: str, value: str
) -> None:
    """Refused, never coerced: a wrong guess turns a privacy control the wrong way.

    ``1`` and ``"true"`` are the two an operator is most likely to write, and both
    are truthy in Python -- which is why the reader tests the *type* rather than
    the truthiness. Guessing would redact for somebody who wrote ``0`` as ``false``
    and, worse, would fail to redact for somebody who wrote ``"true"``.
    """
    root, config = _project(tmp_path, _stating(value))

    with pytest.raises(ProjectConfigError) as raised:
        read_review_participant_redaction(root, config)

    assert REVIEW_REDACT_PARTICIPANT_NAMES_KEY in str(raised.value), label
    assert raised.value.remedy, label


def test_the_refusal_names_the_values_the_key_takes_and_a_command(
    tmp_path: pathlib.Path,
) -> None:
    """A remedy that is merely non-empty sends the reader back into the source.

    So this asserts what the remedy has to *contain*: the dotted key, both values
    it accepts, the schema that publishes the shape, and a command the reader can
    run to find the line. A placeholder passes a truthiness assertion and none of
    these.
    """
    root, config = _project(tmp_path, _stating('"true"'))

    with pytest.raises(ProjectConfigError) as raised:
        read_review_participant_redaction(root, config)

    remedy = raised.value.remedy or ""
    assert "providers.review.redactParticipantNames" in remedy
    assert "`true`" in remedy
    assert "`false`" in remedy
    assert "schemas/config/project-config.schema.json" in remedy
    assert "git diff" in remedy


def test_the_refusal_does_not_render_the_value_it_refused(tmp_path: pathlib.Path) -> None:
    """The value is named by its *type*, because a value can be an alias graph.

    A YAML alias bomb re-expands under ``repr`` to gigabytes from a few hundred
    bytes (T-6), which is why the sibling readers in this module refuse before
    rendering. The switch is a boolean or a mistake, so the type name is the whole
    diagnosis and the value is never interpolated at all.
    """
    anchors = "\n".join(f"  - &a{n} [*a{n - 1}, *a{n - 1}]" for n in range(1, 12))
    body = (
        "apiVersion: theurian.dev/v1\n"
        "seed:\n"
        "  - &a0 [x, x]\n"
        f"{anchors}\n"
        "providers:\n"
        "  review:\n"
        f"    {REVIEW_REDACT_PARTICIPANT_NAMES_KEY}: *a11\n"
    )
    root, config = _project(tmp_path, body)

    with pytest.raises(ProjectConfigError) as raised:
        read_review_participant_redaction(root, config)

    published = f"{raised.value} {raised.value.remedy}"
    assert "list" in published, "the refusal does not say what shape it found"
    assert "x" not in published.replace("schemas/config/project-config.schema.json", ""), (
        "the refused value was rendered into the message"
    )


def test_a_review_block_of_the_wrong_shape_names_its_own_path(tmp_path: pathlib.Path) -> None:
    """A message naming `review` sends the reader to the wrong line; there are several."""
    root, config = _project(tmp_path, "apiVersion: theurian.dev/v1\nproviders:\n  review: 7\n")

    with pytest.raises(ProjectConfigError) as raised:
        read_review_participant_redaction(root, config)

    assert "providers.review" in str(raised.value)


def test_the_reader_leaves_the_project_alone(tmp_path: pathlib.Path) -> None:
    """Reading a setting writes nothing, including no file the absent case would create."""
    root, config = _project(tmp_path, None)
    before = sorted(path.relative_to(tmp_path) for path in tmp_path.rglob("*"))

    assert read_review_participant_redaction(root, config) is False
    assert sorted(path.relative_to(tmp_path) for path in tmp_path.rglob("*")) == before
