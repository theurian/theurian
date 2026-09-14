"""The first reader of ``.theurian/config.yaml`` (SEC-11, ADR-0027 decision 3, #198).

``security/project_config.py`` answers one question -- which secret-scan policy a
project has selected -- and everything interesting about it is what it does when
the answer is not simply there. Three rules, and they are not one rule:

* **Absent is ``block``.** No configuration file, or one that says nothing about
  secret scanning, gets the strictest policy. That is what the schema's
  ``default`` now publishes, so schema and code have to agree.
* **Unrecognised refuses.** A value the enum does not contain is a typo somebody
  made about a security control, and coercing it to ``block`` hides the mistake
  behind the very behaviour that makes it invisible.
* **Nothing escapes untranslated.** This runs on the accept path, where a raw
  ``OSError`` or ``YAMLError`` publishes no ``{error, remedy}`` document at all
  (CP-2, #227).

Marked ``unit`` and writes only under ``tmp_path``.
"""

from __future__ import annotations

import inspect
import os
import re
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Final

import pytest

from theurian.application.project_service import config_escape_remedy
from theurian.domain.errors import ProjectConfigError
from theurian.security import project_config
from theurian.security.paths import MAX_SOURCE_FILE_BYTES
from theurian.security.project_config import (
    PROJECT_CONFIG_FILE,
    SECRET_SCAN_KEY,
    SecretScanPolicy,
    read_secret_scan_policy,
)

pytestmark = pytest.mark.unit

#: A ``chmod 0o000`` denies nothing to root and nothing on Windows, so a test
#: that needs the mode to actually refuse cannot run there (the offline CI job
#: runs as root). Same guard the accept-path permission tests carry.
_CANNOT_BE_REFUSED_BY_A_MODE = sys.platform == "win32" or os.geteuid() == 0


def _project(tmp_path: Path, text: str | None) -> tuple[Path, Path]:
    """A project root and its config path, with ``text`` written or nothing at all."""
    root = tmp_path / "demo"
    (root / ".theurian").mkdir(parents=True)
    config = root / ".theurian" / PROJECT_CONFIG_FILE
    if text is not None:
        config.write_text(text, encoding="utf-8")
    return root, config


def test_a_project_with_no_config_file_blocks() -> None:
    """The ordinary case: ``theurian init`` writes no configuration file.

    Absence is an answer here and not a failure, and the answer is the strictest
    policy -- which is what makes ``default: "block"`` in the published schema a
    statement about the product rather than an aspiration.
    """
    root = Path("/nonexistent-project-root-for-this-test")

    assert read_secret_scan_policy(root, root / ".theurian" / PROJECT_CONFIG_FILE) is (
        SecretScanPolicy.BLOCK
    )


@pytest.mark.parametrize(
    ("label", "text"),
    [
        ("an empty file", ""),
        ("comments only", "# nothing configured yet\n"),
        ("no security block", "raptor:\n  enabled: false\n"),
        ("an empty security block", "security:\n"),
        ("a security block with other keys", "security:\n  maxSourceFileBytes: 8388608\n"),
    ],
)
def test_a_config_that_states_no_policy_blocks(tmp_path: Path, label: str, text: str) -> None:
    """Five ways of saying nothing, all of which mean the same thing.

    An empty file and a comment-only one both parse to ``None`` rather than to a
    mapping, and ``security:`` with nothing under it parses to ``None`` too.
    Every one of them is somebody who has not configured anything, or has
    commented a setting out; treating any as malformed would refuse acceptances
    over a hash mark. An unstated key is the default.
    """
    root, config = _project(tmp_path, text)

    assert read_secret_scan_policy(root, config) is SecretScanPolicy.BLOCK, label


@pytest.mark.parametrize("policy", list(SecretScanPolicy), ids=lambda p: p.value)
def test_each_published_policy_is_read_back(tmp_path: Path, policy: SecretScanPolicy) -> None:
    """Every value the schema's enum publishes has to be one the reader accepts.

    Parametrised over the enum rather than over three literals, so a policy added
    to :class:`SecretScanPolicy` without a way to spell it in the file fails here
    instead of at the first acceptance that tries.

    **Quoted, and that is not cosmetic** -- see
    :func:`test_a_bare_off_is_refused_with_the_quoting_cure`. A bare ``off``
    parses to the boolean ``False`` under PyYAML's YAML 1.1 resolver, so writing
    these unquoted would have made this test assert something about ``block`` and
    ``warn`` and nothing at all about ``off``.
    """
    root, config = _project(tmp_path, f'security:\n  secretScan: "{policy.value}"\n')

    assert read_secret_scan_policy(root, config) is policy


@pytest.mark.parametrize("spelling", ["off", "no", "false"])
def test_a_bare_off_is_refused_with_the_quoting_cure(tmp_path: Path, spelling: str) -> None:
    """The one published value that cannot be written the way the enum prints it.

    Measured 2026-08-24 against this repository's own loader: PyYAML implements
    YAML 1.1, so ``secretScan: off`` yields the boolean ``False``, and so do
    ``no`` and ``false``. A reader who copies ``off`` out of the published enum
    therefore writes something that never arrives here as a string.

    Refusing is the fix rather than translating ``False`` back to ``off``,
    because the three spellings are indistinguishable once parsed: a translation
    would turn the secret scan off for an operator who wrote ``no`` and meant
    something else, which is the one direction in which a wrong guess weakens the
    control. So the refusal carries the actual cure instead.
    """
    root, config = _project(tmp_path, f"security:\n  secretScan: {spelling}\n")

    with pytest.raises(ProjectConfigError) as caught:
        read_secret_scan_policy(root, config)

    assert '"off"' in caught.value.remedy, (
        f"the remedy for a bare `{spelling}` does not name the quoted spelling that works: "
        f"{caught.value.remedy!r}"
    )


#: The spelling a cure names, read out of the published remedy rather than out of
#: a literal written here. ``SECRET_SCAN_KEY`` composes the pattern for the reason
#: the constant exists: the one place that has to match the published contract is
#: visible in a diff. The backtick before the key is load-bearing -- it is what
#: keeps this off ``security.secretScan``, which the same remedy also names.
_CURED_SPELLING: Final = re.compile(rf"`{re.escape(SECRET_SCAN_KEY)}:\s*([^`]+)`")


@pytest.mark.parametrize("spelling", ["off", "no", "false"])
def test_an_unquoted_falsey_policy_refuses_and_names_a_spelling_that_works(
    tmp_path: Path, spelling: str
) -> None:
    """The cure for #614, held to being a cure rather than to being a sentence.

    Two failures, and they are not one. **The first is silent disablement**:
    PyYAML implements YAML 1.1, so ``secretScan: off`` -- and ``no``, and
    ``false`` -- arrives as the boolean ``False``, and the tempting repair is to
    read that back as :attr:`SecretScanPolicy.OFF`. It must not be: the three
    spellings are indistinguishable once parsed, so an operator who wrote ``no``
    about something else would have the secret scan turned off for them, in the
    one direction where a wrong guess weakens a security control. The
    ``pytest.raises`` here is what goes RED the day the reader starts coercing,
    which is why it is asserted rather than assumed from the exception the other
    assertions read a remedy off.

    **The second is a remedy that echoes the operator's own spelling** (#614's
    title). ``read_secret_scan_policy`` refuses with *"is False, which is not a
    policy this build recognises"* and a valid-values list containing ``off`` --
    so a remedy without the quoting clause tells the reader to write what they
    believe they already wrote, and the round trip teaches nothing.

    So this does not stop at the text. It reads the spelling **out of the remedy
    the reader just published**, writes that back into the file, and requires the
    answer to have moved to :attr:`SecretScanPolicy.OFF` -- the discipline
    ``tests/integration/test_published_cures_are_executable.py`` records, applied
    to the one cure on this path. A cure whose named spelling does not select the
    policy it claims to is the failure that survives every text assertion.

    Division of labour with its neighbour: ``test_a_bare_off_is_refused_with_the
    _quoting_cure`` pins that the quoted form is *named*; this pins that the named
    form is a *fix*, and that the refusal is a refusal.
    """
    root, config = _project(tmp_path, f"security:\n  secretScan: {spelling}\n")

    with pytest.raises(ProjectConfigError) as caught:
        read_secret_scan_policy(root, config)
    remedy = caught.value.remedy

    assert "quoted" in remedy, (
        f"the remedy for a bare `{spelling}` never says the value has to be quoted, so a "
        f"reader who copied `{spelling}` out of the schema is told to write it again: {remedy!r}"
    )
    named = _CURED_SPELLING.search(remedy)
    assert named is not None, (
        f"the remedy for a bare `{spelling}` names no `{SECRET_SCAN_KEY}: <value>` spelling to "
        f"write instead: {remedy!r}"
    )
    cured = named.group(1)
    assert cured != spelling, (
        f"the remedy hands back the operator's own spelling `{cured}`, which is the bare form "
        f"YAML 1.1 already read as a boolean -- following it lands on this same refusal"
    )
    config.write_text(f"security:\n  secretScan: {cured}\n", encoding="utf-8")
    try:
        cured_policy = read_secret_scan_policy(root, config)
    except ProjectConfigError as refused:  # pragma: no cover - the RED branch
        pytest.fail(
            f"the remedy told the reader to write `{SECRET_SCAN_KEY}: {cured}`, and following "
            f"it lands on another refusal: {refused}"
        )
    assert cured_policy is SecretScanPolicy.OFF, (
        f"the remedy told the reader to write `{SECRET_SCAN_KEY}: {cured}`, and that selects "
        f"{cured_policy.value!r} rather than {SecretScanPolicy.OFF.value!r}: the published cure "
        f"does not cure"
    )


@pytest.mark.parametrize(
    ("label", "value"),
    [
        ("a near miss", "warm"),
        ("the wrong case", "Block"),
        ("a boolean", "true"),
        ("a number", "1"),
        ("an explicit null", "null"),
        ("a list", "[block]"),
        ("an empty string", '""'),
        ("a single letter", "n"),
    ],
)
def test_an_unrecognised_policy_refuses_rather_than_falling_back(
    tmp_path: Path, label: str, value: str
) -> None:
    """The rule that is easy to get backwards, and expensive when it is.

    Falling back to ``block`` on a value nothing recognises looks safe -- the
    strictest policy, chosen when in doubt. It is not: the operator who wrote
    ``warm`` for ``warn`` would see acceptances refused with no reason given, and
    would have no way to tell a false positive from a typo. Refusing names the
    key and the three values, so the typo is the thing that gets fixed.

    An explicit ``null`` is in this list on purpose. It is not the same as an
    absent key: the schema's enum does not contain it, so a file that states it
    is stating something wrong, and the reader draws the line at *present* rather
    than at *truthy*.
    """
    root, config = _project(tmp_path, f"security:\n  secretScan: {value}\n")

    with pytest.raises(ProjectConfigError) as caught:
        read_secret_scan_policy(root, config)

    assert "secretScan" in str(caught.value), label
    for policy in SecretScanPolicy:
        assert repr(policy.value) in caught.value.remedy, (
            f"the remedy for {label} does not name {policy.value!r}: {caught.value.remedy!r}"
        )


def _alias_bomb_config(levels: int, fan: int) -> str:
    """A ``config.yaml`` whose ``secretScan`` aliases a ``fan**levels``-leaf DAG.

    The reviewers' ``repro_config_bomb.sh`` shape: parsing stays O(levels) because
    PyYAML collapses the aliases, but rendering the resulting container with
    ``repr`` re-expands it as a tree (T-6). ``secretScan: *aN`` is where the
    expanded value would reach ``SecretScanPolicy(...)``'s own ``%r``.
    """
    lines = ["a0: &a0 'x'"]
    for level in range(1, levels + 1):
        refs = ", ".join(f"*a{level - 1}" for _ in range(fan))
        lines.append(f"a{level}: &a{level} [{refs}]")
    lines.append(f"security:\n  secretScan: *a{levels}")
    return "\n".join(lines)


def test_an_alias_bomb_policy_is_refused_without_rendering_it(tmp_path: Path) -> None:
    """A ``secretScan`` pointing at a YAML alias graph is a T-6 denial of service.

    ``SecretScanPolicy(stated)`` builds ``ValueError("%r is not a valid ...")``
    *inside* CPython's ``Enum.__new__`` -- rendering ``stated`` with ``%r`` --
    before the reader's own ``{stated!r}`` is ever reached, so a container there
    re-expands the collapsed DAG to gigabytes from a few hundred bytes. The guard
    runs before the enum call and refuses without rendering the value at all.

    The message length is the pin, not just its type: the vulnerable form renders
    the whole expansion into the refusal (megabytes), so a short, key-only message
    is what says the value was refused before any render. ``fan=6, levels=6`` is
    ~1.5 MB expanded -- catastrophic enough to distinguish the two, bounded enough
    that even the vulnerable path cannot exhaust a mutation run's memory.
    """
    root, config = _project(tmp_path, _alias_bomb_config(levels=6, fan=6))

    with pytest.raises(ProjectConfigError) as caught:
        read_secret_scan_policy(root, config)

    assert len(str(caught.value)) < 1000, "the refusal rendered the expanded alias graph"
    assert "secretScan" in str(caught.value)
    assert caught.value.remedy


def test_a_config_that_does_not_parse_refuses(tmp_path: Path) -> None:
    """A malformed file is a fault with a remedy, never a silent default.

    The alternative -- treating "could not parse it" as "says nothing" -- is the
    collapse that lets a broken file select a policy nobody chose, and it is the
    same shape as concluding "not accepted" from a refused read on the accept
    path (#253).
    """
    root, config = _project(tmp_path, "security:\n  secretScan: [block\n")

    with pytest.raises(ProjectConfigError) as caught:
        read_secret_scan_policy(root, config)

    assert PROJECT_CONFIG_FILE in str(caught.value)
    assert caught.value.remedy


def test_a_config_whose_root_is_not_a_mapping_refuses(tmp_path: Path) -> None:
    """A YAML list at the document root is not the shape the schema declares."""
    root, config = _project(tmp_path, "- block\n- warn\n")

    with pytest.raises(ProjectConfigError):
        read_secret_scan_policy(root, config)


def test_a_security_block_of_the_wrong_shape_refuses(tmp_path: Path) -> None:
    """A scalar where a block of settings belongs is malformed, not empty.

    The line between this and ``security:`` with nothing under it is deliberate.
    ``None`` is somebody commenting out their settings; a string is a file that
    is not the document the published schema declares, and reading a policy out
    of it would be inventing one.
    """
    root, config = _project(tmp_path, "security: strict\n")

    with pytest.raises(ProjectConfigError) as caught:
        read_secret_scan_policy(root, config)

    assert "security" in str(caught.value)


@pytest.mark.skipif(_CANNOT_BE_REFUSED_BY_A_MODE, reason="chmod denies nothing to root")
def test_a_config_the_filesystem_refuses_is_a_fault_not_an_absence(tmp_path: Path) -> None:
    """Present-and-unreadable is a different fact from absent.

    Concluding "no configuration, so `block`" from a permission failure would be
    the safe-looking answer again, and it would be wrong for the same reason:
    a project that has selected ``off`` would silently start refusing, and a
    project that has selected ``block`` would learn nothing. Both want the file
    fixed.

    The message carries the OS's own category and never ``str(exc)``, whose text
    would put the machine's home directory into a published error.
    """
    root, config = _project(tmp_path, "security:\n  secretScan: warn\n")
    config.chmod(0o000)
    try:
        with pytest.raises(ProjectConfigError) as caught:
            read_secret_scan_policy(root, config)
    finally:
        config.chmod(0o600)

    assert str(root) not in str(caught.value), (
        f"the refusal interpolates an absolute path: {caught.value}"
    )
    assert caught.value.remedy


def test_a_config_over_the_size_cap_refuses(tmp_path: Path) -> None:
    """SEC-8 applies to the configuration file too, and its refusal is translated.

    ``read_source_file`` raises ``InputTooLargeError``, which is a
    ``TheurianError`` and not an ``OSError`` -- so a reader that caught only
    ``OSError`` would let it out with the wrong remedy on it.
    """
    root, config = _project(tmp_path, "security:\n  secretScan: warn\n")
    config.write_text("# " + "x" * (MAX_SOURCE_FILE_BYTES + 1) + "\n", encoding="utf-8")

    with pytest.raises(ProjectConfigError):
        read_secret_scan_policy(root, config)


def test_a_config_symlinked_out_of_the_project_refuses(tmp_path: Path) -> None:
    """SEC-7: the containment boundary is the project root, and it is enforced here.

    A ``.theurian/`` directory arrives through a clone, so a committed symlink
    pointing at a file outside the tree is input from whoever could commit. The
    read routes through ``read_source_file`` for exactly this, and the refusal is
    translated rather than escaping as a ``PathEscapeError`` nobody caught.
    """
    outside = tmp_path / "elsewhere.yaml"
    outside.write_text("security:\n  secretScan: off\n", encoding="utf-8")
    root, config = _project(tmp_path, None)
    config.symlink_to(outside)

    with pytest.raises(ProjectConfigError):
        read_secret_scan_policy(root, config)


def test_the_reader_leaves_the_project_alone(tmp_path: Path) -> None:
    """It answers a question; it does not create a configuration file.

    Worth pinning because the obvious convenience -- writing the default out on
    first read -- would put a file into `.theurian/` that `theurian init` did not
    create, on a path where nothing else writes, during a command whose whole
    contract is that a refusal consumes nothing.
    """
    root, config = _project(tmp_path, None)
    before = {p.relative_to(root).as_posix() for p in root.rglob("*")}

    read_secret_scan_policy(root, config)

    assert {p.relative_to(root).as_posix() for p in root.rglob("*")} == before


#: What each reader answers for a project with no configuration file, keyed by the
#: reader's own name. Written out rather than recomputed, so a default that *moves*
#: fails here instead of being re-derived into agreement with production -- and
#: each of these three values is a sentence in :func:`config_escape_remedy`, the
#: cure #652 published for an escaping ``config.yaml``. That cure names each of
#: them **with what it costs**, one of them being that an empty allowlist refuses
#: every ``theurian review ingest``; this mapping is what holds the values it
#: states, and
#: ``tests/integration/test_contained_path_envelope.py::
#: test_an_escaping_config_file_is_cured_by_removing_the_link_not_by_init`` is
#: where the consequences are run.
_DEFAULT_WHEN_ABSENT: Final[dict[str, object]] = {
    "read_secret_scan_policy": SecretScanPolicy.BLOCK,
    "read_review_repositories": (),
    "read_review_participant_redaction": False,
}


#: The two parameters a reader of the configuration file opens with. Compared as a
#: **prefix** rather than as the whole signature, which is the difference between
#: this key and the one round one demonstrated open: a reader spelled ``(root,
#: config_file, *, strict=False)`` is a reader, it is callable as ``read(root,
#: config)``, and an exact-signature key dropped it out of the population --
#: taking with it the promise :func:`config_escape_remedy` makes about absent
#: files. The demonstration was a fourth reader that *raised* without a file and
#: passed the suite.
_READER_LEAD: Final = ["root", "config_file"]

#: Public functions in ``security/project_config.py`` that are **not** readers of
#: the file, each with the reason it is not one. Empty today, and that is a
#: measurement rather than an omission: the module's public surface is the three
#: readers. It exists so the partition below can be asserted -- a public helper
#: added there (a validator, a renderer, a writer) is either a reader whose
#: absent-file answer is recorded, or a named entry here, and no third state is
#: green.
_NOT_A_CONFIGURATION_READER: Final[dict[str, str]] = {}


def _public_functions() -> dict[str, Callable[..., object]]:
    """Every public function ``security/project_config.py`` defines itself.

    Imported names are excluded by ``__module__``: the question is what this module
    publishes as its own surface, not what it happens to have in scope. This is the
    population the partition is asserted over, and it is deliberately wider than
    the readers -- a fail-open key is exactly what round one found here, so the set
    a reader can fall out of has to be visible.

    **The key is ``inspect.isfunction``, which means a plain ``def`` and nothing
    else**, and the limit is stated rather than implied because this population is
    argued over. A reader wrapped in ``functools.lru_cache`` or ``functools.partial``,
    or written as a callable instance, is not a function: it falls out of this set
    and out of the partition below with it, and no assertion here would say so.

    What stands behind that gap is the key side rather than the reader side.
    ``tests/unit/test_config_key_call_sites.py`` reads the configuration key
    spellings out of this package's syntax tree and holds the population this
    module names at exactly the three in :data:`_DEFAULT_WHEN_ABSENT`, so a reader
    of a *new* key reddens there in whatever shape it is written. The residual is a
    wrapped reader of one of those same three keys: it would not be driven here,
    and nothing else asks what it answers for an absent file. Widening the key to
    non-class callables is the fix if that residual ever becomes real -- it needs a
    carve-out for ``SecretScanPolicy``, the one public class this module defines.
    """
    return {
        name: member
        for name, member in vars(project_config).items()
        if not name.startswith("_")
        and inspect.isfunction(member)
        and member.__module__ == project_config.__name__
    }


def _configuration_readers() -> dict[str, Callable[[Path, Path], object]]:
    """Every public reader of the configuration file, off the module by reflection.

    Keyed on the shape rather than on a list kept by hand: a public function
    defined in this module whose first two parameters are
    :data:`_READER_LEAD`. A fourth key added with a reader of its own appears here
    the moment it is written, and fails the equality below until somebody records
    what it answers for an absent file -- which is the sentence
    :func:`config_escape_remedy` publishes to an operator whose ``config.yaml`` has
    just been removed.
    """
    found: dict[str, Callable[[Path, Path], object]] = {}
    for name, member in _public_functions().items():
        parameters = list(inspect.signature(member).parameters.values())
        if [parameter.name for parameter in parameters[:2]] == _READER_LEAD:
            found[name] = member
    return found


def _readers_not_callable_with_two_paths() -> list[str]:
    """Readers this file could not drive, which is a classification failure not a skip.

    A member of the population is called below as ``read(root, config_file)``. One
    that requires a third argument would raise ``TypeError`` there -- an error whose
    message says nothing about the promise being held -- so it is reported here
    instead, by name, with what to do about it.
    """
    return sorted(
        name
        for name, member in _configuration_readers().items()
        if any(
            parameter.default is inspect.Parameter.empty
            and parameter.kind
            not in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD)
            for parameter in list(inspect.signature(member).parameters.values())[2:]
        )
    )


def test_every_configuration_reader_answers_a_default_when_the_file_is_absent(
    tmp_path: Path,
) -> None:
    """The part of #652's cure that is this module's: no reader reads absence as a fault.

    ``config_escape_remedy`` tells an operator to delete an escaping
    ``.theurian/config.yaml`` and names the three defaults that come into force.
    Two claims about *this* module are in that: no reader may treat the absent file
    as a fault, and the value each one answers has to be the value the cure names.

    **And that is the whole of what this file can hold**, which round one made
    worth writing down. The cure's first cut went one step further -- the retry
    "runs on those rather than refusing" -- and this test was cited as holding it.
    It cannot: a reader answering ``()`` for an absent allowlist is a default, and
    an empty allowlist names no repository, so ``theurian review ingest`` refuses
    on exactly the answer recorded below as the good case. What a command does with
    a default is a claim at the command's layer, and
    ``tests/integration/test_contained_path_envelope.py::
    test_an_escaping_config_file_is_cured_by_removing_the_link_not_by_init`` is
    where the two commands are run.

    Both claims are asserted over a reflected population rather than over the three
    readers that exist today, because the defect the cure would develop is a fourth
    key whose reader *raises* without a file -- at which point the published cure
    sends an operator to delete something and then meet a refusal no command could
    have avoided.

    **And the population is closed the other way too, which round one is why.** The
    key used to be the *whole* parameter list, so a reader spelled ``(root,
    config_file, *, strict=False)`` was not in it: the demonstrated fourth reader
    raised on an absent file and this test stayed green. The key is now the first
    two parameters, and the module's public surface is partitioned -- reader, or a
    named entry in :data:`_NOT_A_CONFIGURATION_READER` -- so a public function that
    is neither fails here rather than falling out.
    """
    root, config = _project(tmp_path, None)
    assert not config.exists(), "the fixture wrote a configuration file, so this proves nothing"

    readers = _configuration_readers()
    public = _public_functions()

    assert set(public) == set(readers) | set(_NOT_A_CONFIGURATION_READER), (
        f"a public function in {project_config.__name__} is neither a reader of the "
        f"configuration file nor recorded as not being one: "
        f"{sorted(set(public) - set(readers) - set(_NOT_A_CONFIGURATION_READER))}. Give "
        "it `(root, config_file)` as its first two parameters and record what it "
        "answers for an absent file, or name it in `_NOT_A_CONFIGURATION_READER` with "
        "the reason it is not one -- `config_escape_remedy` tells operators to delete "
        "that file, and this is the population that promise ranges over."
    )
    assert not set(readers) & set(_NOT_A_CONFIGURATION_READER), (
        f"{sorted(set(readers) & set(_NOT_A_CONFIGURATION_READER))} are carved out of "
        "the reader population and match the reader shape, so the carve-out is hiding a "
        "reader from the equality below"
    )
    assert not _readers_not_callable_with_two_paths(), (
        f"{_readers_not_callable_with_two_paths()} take a required third argument, so "
        "this file cannot drive them with a root and a config file. Give the extra "
        "parameter a default -- the shipped call sites pass two -- or, if the reader is "
        "not one an operator's retry reaches, record it in "
        "`_NOT_A_CONFIGURATION_READER`."
    )

    assert set(readers) == set(_DEFAULT_WHEN_ABSENT), (
        f"the configuration readers and the recorded defaults have moved apart: "
        f"{sorted(set(readers) ^ set(_DEFAULT_WHEN_ABSENT))}. A reader added here without "
        "a default for the absent file leaves `config_escape_remedy` naming the defaults "
        "of a file it does not describe; record what it answers, and say so in that cure "
        "-- with what the answer costs, which is the clause round one found missing."
    )

    answered = {name: read(root, config) for name, read in sorted(readers.items())}

    assert answered == _DEFAULT_WHEN_ABSENT, (
        f"a reader answers something other than the default the published cure names:\n"
        f"  answered: {answered}\n  recorded: {_DEFAULT_WHEN_ABSENT}\n\n"
        "`config_escape_remedy` states these three values to an operator who has just "
        "removed the file, so a default that moves without that text moving is a cure "
        "describing a policy that is not in force."
    )

    cure = config_escape_remedy(".theurian")
    assert SecretScanPolicy.BLOCK.value in cure, (
        f"the cure no longer names the secret-scan policy an absent file selects: {cure!r}. "
        "That is the one default of the three with a security consequence, and an operator "
        "deleting the file is entitled to read which way it falls."
    )
