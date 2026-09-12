"""``knowledgeDirectory`` is withdrawn as a setting, and still accepted (#533).

From the commit that first published the contract (``0f8c387d``, 2026-08-01) the
schema carried ``knowledgeDirectory`` with a ``default`` of ``.theurian`` and no
``pattern``, which reads as a knob: set it, and the knowledge directory moves.
Nothing honours it. ``ProjectPaths.of`` composes the directory from
``DEFAULT_KNOWLEDGE_DIRECTORY`` and the only caller that passes the argument at
all is ``cli/migration_pipeline.py``, which passes the *registry's* value against
a rehearsal tree -- never this file's.

**Two things had to be true at once, and they pull in opposite directions**, which
is what :data:`COMPATIBILITY_PRINCIPLE` states and what
:func:`test_the_changelog_states_the_compatibility_principle_in_the_one_wording`
holds to a single wording. The schema must stop advertising a setting that does
not exist, and an operator who wrote the key *because we published it* must not
have their file start failing validation for having believed us. Deleting the
property does the first and breaks the second: ``additionalProperties: false`` is
deliberate throughout these schemas, so a deleted property makes every file
carrying it invalid. Measured against this repository's own document, with the
property deleted from a copy of the schema::

    legacy-carrying: INVALID: Additional properties are not allowed
                              ('knowledgeDirectory' was unexpected)

So the landing is annotation-only: the ``default`` is gone -- that is the claim
being withdrawn -- while ``deprecated: true`` and a ``"Never in force"``
description say what the key now is, and ``type: string`` is kept so that **no
instance changes verdict**. All three keywords that moved are annotations, which
JSON Schema does not let decide validity; the property's one assertion keyword is
untouched. The record of why lives in the core changelog, where a correction to a
published contract belongs; this module is what holds the tree to it.

**The same spelling is a different, live surface, and it is deliberately out of
reach here.** ``knowledgeDirectory`` is emitted twice in ``src/`` and neither is
this key: it is a field of every ``projects.json`` registry entry
(``application/project_service.py``), served by ``project list`` and the
``project.list`` MCP tool -- which is the face
``tests/integration/test_wire_contract.py`` pins -- and a field of ``theurian
init --json``'s payload (``cli/commands.py``), which **nothing pins at all**:
there is no ``schemas/cli`` document for ``init`` and no test asserts the field in
that payload. ``theurian project status --json`` carries neither. Registry field,
config key: one spelling, two contracts.

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

CORE_CHANGELOG: Final = REPO_ROOT / "packages" / "theurian-core" / "CHANGELOG.md"

#: The key this module is about, spelled once.
KEY: Final = "knowledgeDirectory"

#: Why the key is withdrawn rather than deleted, in **one** wording.
#:
#: A principle restated in three places drifts into three principles, and the
#: drift is invisible from inside any one of them: the first draft of this change
#: carried "does not punish past honesty in its users" here, "must not punish past
#: honesty in users" in the changelog, and a third paraphrase in the pull request.
#: So there is one string, this one, and
#: :func:`test_the_changelog_states_the_compatibility_principle_in_the_one_wording`
#: is what keeps the durable copy equal to it. The module docstring above and the
#: pull-request description refer to this constant rather than restating it.
COMPATIBILITY_PRINCIPLE: Final = (
    "An honest schema must not punish past honesty in its users: an operator who set "
    "the key because we published it must not have their config broken by our "
    "correction; the removal makes the key absent-or-ignored, never a validation "
    "failure."
)

#: How to read which cures render the knowledge directory's name into a shell
#: command, instead of naming them.
#:
#: Both sites below used to name ``derived_escape_remedy`` as *the* function that
#: does it. That was true when it was written and false by #602, which added
#: ``review_escape_remedy`` beside it -- in a pull request that had no reason to
#: re-read this module, which is exactly how the instruction below would have been
#: followed to a half-quoted product. So the population is read rather than
#: transcribed, the way ``mcp/tools.py``'s ``PATH_ESCAPE_REFUSAL`` note reads the
#: same arms.
#:
#: Run against this tree on 2026-09-12 the key printed ``derived_escape_remedy``
#: and ``review_escape_remedy``. It is scoped by its pathspec to the module it asks
#: about, so quoting it in this file cannot make it match itself, and its pattern
#: is spelled on one line -- a key folded mid-regex does not run when it is copied
#: out. It keys on the parameter name those cures share, which is therefore
#: load-bearing: a later cure taking the directory's name under another spelling
#: joins that convention or is found instead by ``git grep -n 'knowledge_dir\.name'
#: -- packages/theurian-core/src``.
BASENAME_RENDERING_CURES_KEY: Final = (
    "git grep -nE '^def [a-z_]+\\(knowledge_directory_name' -- "
    "packages/theurian-core/src/theurian/application/project_service.py"
)

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
    """The subschema the repository's published schema carries for the key.

    Read from the repository tree rather than transcribed, and the wheel's copy is
    the same bytes by construction: ``packages/theurian-core/hatch_build.py``
    force-includes this directory under ``theurian/schemas`` at build time, so
    there is one document and not two to keep in step.
    """
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
    false claim, and ``deprecated`` is JSON Schema's own word for what is left.

    **That argument reaches further than this key**, which is why the schema's
    root description now carries the rule rather than this docstring: a published
    ``default`` is honest where a named test pins it to the constant the product
    uses -- ``tests/unit/test_forest_derivation.py`` does that for the ``raptor``
    block -- and is a false claim otherwise. Eight siblings fail that rule today,
    ``defaultBranch`` among them, and they are
    `#592 <https://github.com/theurian/theurian/issues/592>`_ rather than this
    module's business.
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
    """:data:`COMPATIBILITY_PRINCIPLE`, run.

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
        # ever honoured: the escape cures render the directory's name straight into
        # shell commands, and this name splits into three operands in every one of
        # them. Measured 2026-09-12 by calling them with it -- `rm my knowledge
        # dir/state` parses as `rm`, `my`, `knowledge`, `dir/state`, and
        # `ls -l my knowledge dir/review` the same way. Which cures those are is
        # read with BASENAME_RENDERING_CURES_KEY rather than listed here, because
        # the sentence that listed one of them went false inside the pull request
        # that added the second. Nothing honours the key, so this row asserts the
        # path does not move -- and it is the tripwire that reddens on the day it
        # does, while the quoting is still one line away in each cure (#533).
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
        f"`shlex.quote` on the path *every* cure this key prints renders into a "
        f"shell command -- `{BASENAME_RENDERING_CURES_KEY}` -- and the changelog "
        f"correction saying the key is a setting again (#533). Read that "
        f"population rather than trusting a list: this line named only "
        f"`derived_escape_remedy` until #602 added a second cure, which would "
        f"have left one of them rendering an unquoted name."
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
    """The example is copied, so it must not teach a key that does nothing.

    **This module's own rule, not a citation.** OSS-13 is discharged by the row
    that already exists -- ``test_examples.py`` asserts that the bundled example is
    present and that its config validates -- and neither answers this question:
    ``test_config_matches_its_schema`` *cannot* catch a withdrawn key here, which
    is the point of keeping the key accepted. The example carrying it stays valid.
    Valid and instructive are different questions, and this is the second one.
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


def test_the_changelog_states_the_compatibility_principle_in_the_one_wording() -> None:
    """RED means the principle has two spellings again, which is how it becomes two rules.

    :data:`COMPATIBILITY_PRINCIPLE` is the reason this key was withdrawn instead of
    deleted, and it is the sentence the *next* schema correction will be read
    against. A principle that appears in a changelog, a docstring and a pull
    request in three near-identical wordings has already started to drift: each
    copy looks authoritative, and nothing says which one is the rule.

    So the durable copy -- the changelog, which outlives the branch -- is held
    equal to the constant, and everything else refers to the constant rather than
    restating it. If the wording genuinely improves, it improves in one place and
    this row carries it into the other.
    """
    changelog = " ".join(CORE_CHANGELOG.read_text(encoding="utf-8").split())

    assert " ".join(COMPATIBILITY_PRINCIPLE.split()) in changelog, (
        f"packages/theurian-core/CHANGELOG.md no longer states the compatibility "
        f"principle in the wording this module records:\n\n"
        f"  {COMPATIBILITY_PRINCIPLE}\n\n"
        f"This is the sentence a later schema correction will be argued from. Do not "
        f"repair this by loosening the match to a fragment: a fragment match is what "
        f"lets a second wording live beside the first, which is the drift the pin "
        f"exists to stop (#533)."
    )
