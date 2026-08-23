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

import os
import sys
from pathlib import Path

import pytest

from theurian.domain.errors import ProjectConfigError
from theurian.security.paths import MAX_SOURCE_FILE_BYTES
from theurian.security.project_config import (
    PROJECT_CONFIG_FILE,
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
