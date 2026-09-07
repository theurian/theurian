"""Reading `.theurian/config.yaml` (SEC-11, SEC-10, ADR-0027 decision 3, ADR-0030).

**This is the only code in ``src/`` that reads a project's configuration file.**
Until ADR-0027 decision 3 nothing did: the schema published
``security.secretScan`` and ``providers.review.repositories`` as reserved keys,
and half a dozen documents told a reader not to rely on either control *because*
the key was inert. ``tests/unit/test_config_key_call_sites.py`` exists to go RED
on the diff that changes that, and it is the record of which claims had to be
corrected alongside.

**Two keys now, and the second arrived with the code that needs it.**
``providers.review.repositories`` is SEC-10's repository allowlist, and ADR-0030
decision 2 makes it load-bearing: review ingestion consults it **before any
process is spawned**. This module reads the key; ``security/review_allowlist.py``
decides what a value means and refuses what is not listed. The split is the same
one ``secretScan`` has -- reading the file is one concern, and what a value
selects is another -- and it keeps the shape rules for a published key in the
module that also owns the pattern the schema publishes.

**Absent means ``block``, and unrecognised means refuse.** Those are two rules
and not one. A project with no configuration file, or one that says nothing
about secret scanning, gets the strictest policy -- which is what ADR-0027
records and what the schema's ``default`` now publishes. But a file that *does*
state a value the enum does not contain is a typo somebody made about a security
control, and coercing it to ``block`` would hide the mistake behind the very
behaviour that makes it invisible: the operator who wrote ``warn`` as ``warm``
would see acceptances refused and no reason why. The same reasoning covers a
file that will not parse.

**One published value cannot be written the obvious way**, and the refusal above
is what says so rather than guessing: PyYAML implements YAML 1.1, which reads a
bare ``off`` as the boolean ``False``. :data:`_QUOTING_CURE` records the
measurement and why translating it back is the wrong repair.
"""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path, PurePosixPath
from typing import Any, Final

import yaml

from theurian.domain.errors import ProjectConfigError, TheurianError
from theurian.security.paths import read_source_file
from theurian.security.yaml_loading import is_bounded_scalar, load_yaml

#: The configuration file's name inside the knowledge directory. One definition,
#: because ``ProjectPaths`` composes the path from it and a second spelling would
#: be a file the product half-reads.
PROJECT_CONFIG_FILE: Final = "config.yaml"

#: The published key this module reads, spelled exactly as the schema publishes
#: it. Named as a constant so the one place that has to match the contract is
#: visible in a diff.
SECRET_SCAN_KEY: Final = "secretScan"  # noqa: S105 - a published config key, not a secret

#: SEC-10's allowlist key, spelled exactly as the schema publishes it. The one
#: place in ``src/`` that names it, which is what
#: ``test_config_key_call_sites.py``'s reader enumeration records.
REVIEW_REPOSITORIES_KEY: Final = "repositories"

_SECURITY_BLOCK: Final = "security"
_PROVIDERS_BLOCK: Final = "providers"
_REVIEW_BLOCK: Final = "review"


class SecretScanPolicy(StrEnum):
    """What ``accept`` does when a body appears to contain a secret.

    The three values the published schema declares, and a ``StrEnum`` so the
    member and the string a caller reads out of the file are the same thing --
    there is no mapping table to disagree with the schema.
    """

    #: Refuse the acceptance. The default, including when nothing is configured.
    BLOCK = "block"
    #: Accept, and report every finding on the success result.
    WARN = "warn"
    #: Do not scan.
    OFF = "off"


#: What every refusal below tells the reader to do about the key itself. Repeated
#: into each remedy rather than referenced, because ``remedy`` is a field a caller
#: reads without parsing ``error`` and a partial one is worse than none.
_VALID_VALUES: Final = (
    f"`{_SECURITY_BLOCK}.{SECRET_SCAN_KEY}` takes "
    f"{', '.join(repr(policy.value) for policy in SecretScanPolicy)}, and defaults to "
    f"{SecretScanPolicy.BLOCK.value!r} when it is absent."
)

#: The cure for the one spelling a reader will get wrong by copying the published
#: enum, added to that remedy when the value arrived as a boolean.
#:
#: **PyYAML implements YAML 1.1, whose implicit resolver reads a bare ``off`` as
#: the boolean ``False``** -- measured: ``secretScan: off`` yields ``False``, and
#: so do ``no`` and ``false``. So the value the enum publishes, written the
#: obvious way, never reaches this module as the string it looks like.
#:
#: Refused rather than translated, and the reason is that the translation cannot
#: be narrowed. ``off``, ``no`` and ``false`` are indistinguishable once parsed,
#: so reading ``False`` as :attr:`SecretScanPolicy.OFF` would turn a security
#: control off for an operator who wrote ``no`` and meant something else. That is
#: precisely the "coerce it and hope" this module exists not to do, pointed at
#: the one policy where a wrong guess weakens the control rather than
#: strengthening it. Quoting is the cure, and the published schema already
#: implies it: a JSON Schema ``enum`` of ``"off"`` does not admit ``false``.
_QUOTING_CURE: Final = (
    f" YAML reads a bare `{SecretScanPolicy.OFF.value}` (and `no`, and `false`) as a boolean, "
    f'so write it quoted: `{SECRET_SCAN_KEY}: "{SecretScanPolicy.OFF.value}"`.'
)


#: The allowlist key's dotted path, composed from the block constants so the
#: message and the reader cannot disagree about where the key lives.
_ALLOWLIST_PATH: Final = f"{_PROVIDERS_BLOCK}.{_REVIEW_BLOCK}.{REVIEW_REPOSITORIES_KEY}"

#: What a reader does about a malformed allowlist. It names the artefact and a
#: command: the schema is what publishes the shape, and `gh repo view` is what
#: prints the spelling GitHub resolves, which is the spelling an entry must carry.
_ALLOWLIST_CURE: Final = (
    f"Write `{_ALLOWLIST_PATH}` as a list of `owner/repo` strings, as "
    f"`schemas/config/project-config.schema.json` publishes it -- "
    f"`gh repo view <owner>/<name> --json nameWithOwner` prints the spelling GitHub "
    f"resolves. The list is refused whole rather than filtered, so no entry is "
    f"silently dropped while an operator believes it is allowlisted."
)


def read_secret_scan_policy(root: Path, config_file: Path) -> SecretScanPolicy:
    """The secret-scan policy this project selects, or the default it inherits.

    Args:
        root: The project root. The containment boundary: ``config_file`` is read
            through :func:`~theurian.security.paths.read_source_file`, so a
            symlink pointing out of the project is refused rather than followed,
            and an oversized file is refused rather than parsed (SEC-7, SEC-8).
        config_file: Where the file is, which the caller composes from
            ``ProjectPaths`` rather than assembling here -- this function does
            not get to decide where a project keeps its configuration.

    Returns:
        The configured policy, or :attr:`SecretScanPolicy.BLOCK` when the file
        is absent or states nothing about secret scanning.

    Raises:
        ProjectConfigError: If the file exists and cannot be read, does not
            parse, is not a mapping, or states a ``security.secretScan`` value
            that is not one of the three. Never a raw ``OSError`` or
            ``YAMLError``: this is on the accept path, where an untranslated
            fault publishes no ``{error, remedy}`` document at all (#227).
    """
    document = _read_document(root, config_file)
    if document is None:
        return SecretScanPolicy.BLOCK

    security = _section(document, _SECURITY_BLOCK)
    if security is None or SECRET_SCAN_KEY not in security:
        return SecretScanPolicy.BLOCK

    stated = security[SECRET_SCAN_KEY]
    if not is_bounded_scalar(stated):
        # Refused *before* `SecretScanPolicy(stated)`, and the order is the whole
        # point: CPython's `Enum.__new__` builds its own `ValueError("%r is not a
        # valid ...")` -- rendering `stated` with `%r` -- before the `except`
        # below is ever reached, so guarding the message at :`{stated!r}` would be
        # too late. A `secretScan` pointing at a YAML alias graph re-expands under
        # that repr to gigabytes from a few hundred bytes of anchors (T-6); a
        # policy selector is a short scalar or it is a mistake, so `stated` is
        # never rendered here -- the key and the valid values are the diagnosis.
        raise ProjectConfigError(
            f"`{_SECURITY_BLOCK}.{SECRET_SCAN_KEY}` in {PROJECT_CONFIG_FILE} is not a simple "
            f"value a policy could be spelled as.",
            remedy=(
                f"Set it to one short value -- {_VALID_VALUES} Until it names one of them the "
                f"acceptance is refused rather than guessed at, because guessing would hide a "
                f"typo about a security control."
            ),
        )
    try:
        return SecretScanPolicy(stated)
    except ValueError as exc:
        raise ProjectConfigError(
            f"`{_SECURITY_BLOCK}.{SECRET_SCAN_KEY}` in {PROJECT_CONFIG_FILE} is "
            f"{stated!r}, which is not a policy this build recognises.",
            remedy=(
                f"Correct it -- {_VALID_VALUES}"
                f"{_QUOTING_CURE if isinstance(stated, bool) else ''}"
                f" Until it names one of them the acceptance is refused rather than guessed "
                f"at, because guessing would hide a typo about a security control."
            ),
        ) from exc


def read_review_repositories(root: Path, config_file: Path) -> tuple[str, ...]:
    """The repositories this project allowlists for review ingestion (SEC-10).

    Reads ``providers.review.repositories`` and answers what the file states, in
    file order. It does **not** decide whether a given repository is allowed --
    that is ``security/review_allowlist.py``'s, which also owns the pattern an
    entry must match. This function's whole job is "what does the file say, and
    is it the shape the schema publishes".

    Args:
        root: The project root, the containment boundary for the read.
        config_file: Where the file is, composed by the caller from
            ``ProjectPaths`` for the reason :func:`read_secret_scan_policy`
            records.

    Returns:
        The stated entries in the order the file lists them, or an empty tuple
        when the file, the ``providers`` block, the ``review`` block or the key
        is absent. **Empty is not "allow everything"**: the allowlist module
        refuses every repository against an empty list, which is what makes an
        unconfigured project ingest nothing rather than anything.

    Raises:
        ProjectConfigError: If the file cannot be read or parsed, or the key is
            present and is not a list of short strings. Refused rather than
            filtered: dropping the malformed entries would leave an operator
            believing a repository is allowlisted when the line that names it was
            silently discarded.
    """
    document = _read_document(root, config_file)
    if document is None:
        return ()

    providers = _section(document, _PROVIDERS_BLOCK)
    review = (
        None if providers is None else _section(providers, _REVIEW_BLOCK, under=_PROVIDERS_BLOCK)
    )
    if review is None or REVIEW_REPOSITORIES_KEY not in review:
        return ()

    stated = review[REVIEW_REPOSITORIES_KEY]
    if not isinstance(stated, list):
        raise ProjectConfigError(
            f"`{_ALLOWLIST_PATH}` in {PROJECT_CONFIG_FILE} is a "
            f"{type(stated).__name__}, not a list of `owner/repo` values.",
            remedy=_ALLOWLIST_CURE,
        )
    # Every entry is checked before any is returned, and `is_bounded_scalar`
    # runs before the `isinstance` message could render one: an entry pointing at
    # a YAML alias graph re-expands under `repr` to gigabytes from a few hundred
    # bytes (T-6), which is the ordering `read_secret_scan_policy` records above.
    for entry in stated:
        if not is_bounded_scalar(entry) or not isinstance(entry, str):
            raise ProjectConfigError(
                f"`{_ALLOWLIST_PATH}` in {PROJECT_CONFIG_FILE} lists an entry that is "
                f"not a short `owner/repo` string.",
                remedy=_ALLOWLIST_CURE,
            )
    return tuple(stated)


def _read_document(root: Path, config_file: Path) -> dict[str, Any] | None:
    """The parsed configuration file, or ``None`` when the project states nothing.

    Two cases answer ``None`` and they are both "the project has said nothing":
    the file is absent -- the ordinary case, since ``theurian init`` writes no
    configuration file -- or it is present and empty, which a file somebody
    created and left blank, or filled only with comments, parses to. Neither is a
    failure.

    Everything else that can go wrong is a failure with a remedy, including a
    file that is present and unreadable: concluding "no configuration" from
    "could not read it" would let a permission slip silently select a policy
    nobody chose.
    """
    try:
        relative = config_file.relative_to(root)
    except ValueError as exc:  # pragma: no cover - composed from the same root
        raise ProjectConfigError(
            f"{PROJECT_CONFIG_FILE} was looked for outside the project.",
            remedy=f"Keep the file at {PROJECT_CONFIG_FILE} inside the project's "
            "knowledge directory.",
            # The path is deliberately not interpolated: an absolute one carries
            # the machine's home directory into a published message.
        ) from exc

    try:
        raw = read_source_file(root, PurePosixPath(relative))
    except FileNotFoundError:
        return None
    except (OSError, TheurianError) as exc:
        raise _unreadable(exc) from exc

    try:
        document = load_yaml(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError, RecursionError, yaml.YAMLError) as exc:
        raise _unreadable(exc) from exc

    # `load_yaml` rather than `load_yaml_mapping`, so that an empty document --
    # which parses to `None` -- can be told apart from a document of the wrong
    # shape. The mapping helper collapses them into one `ValueError`, and a blank
    # `config.yaml` refusing every acceptance is not the behaviour a blank file
    # deserves.
    if document is None:
        return None
    if not isinstance(document, dict):
        raise ProjectConfigError(
            f"{PROJECT_CONFIG_FILE} is a {type(document).__name__} at its root, not a "
            f"mapping of settings.",
            remedy=(
                f"Correct {PROJECT_CONFIG_FILE} so it is a YAML mapping, or delete it to fall "
                f"back to the shipped defaults -- {_VALID_VALUES}"
            ),
        )
    typed: dict[str, Any] = document
    return typed


def _unreadable(error: Exception) -> ProjectConfigError:
    """The refusal for a configuration file that exists and cannot be used.

    The reason is the exception's own message for a parse failure and its
    ``strerror`` for a filesystem one, never ``str(exc)`` on an ``OSError`` --
    whose text carries the absolute filename, and with it the developer's home
    directory (the discipline ``proposal_service.py::_unreadable`` records).
    """
    reason = error.strerror or "it could not be read" if isinstance(error, OSError) else str(error)
    return ProjectConfigError(
        f"{PROJECT_CONFIG_FILE} is present but could not be read: {reason}.",
        remedy=(
            f"Make {PROJECT_CONFIG_FILE} readable and well-formed YAML, then run the command "
            f"again. Delete it to fall back to the shipped defaults -- {_VALID_VALUES}"
        ),
    )


def _section(
    document: dict[str, Any], name: str, *, under: str | None = None
) -> dict[str, Any] | None:
    """One block, or ``None`` when the file states none.

    A block written as ``security:`` with nothing under it parses to ``None``,
    which is somebody commenting a setting out rather than a malformed file, and
    it means the same as leaving the block off entirely. A block that is present
    and is a *scalar or a list* is a different thing: the file is not the shape
    the published schema declares, and reading a policy out of it would be
    inventing one.

    ``under`` names the block this one is nested in, so the refusal says
    ``providers.review`` rather than ``review`` -- a file has one ``review``
    block and several plausible places to have put it, and a message naming the
    leaf sends the reader to the wrong line.
    """
    dotted = name if under is None else f"{under}.{name}"
    if name not in document:
        return None
    block = document[name]
    if block is None:
        return None
    if not isinstance(block, dict):
        # The `security` block is the one whose values this module also decides,
        # so its refusal carries them; naming `secretScan`'s three values under
        # `providers.review` would send the reader to an unrelated key.
        values = f" {_VALID_VALUES}" if dotted == _SECURITY_BLOCK else ""
        raise ProjectConfigError(
            f"`{dotted}` in {PROJECT_CONFIG_FILE} is a {type(block).__name__}, not a block of "
            f"settings.",
            remedy=(
                f"Correct {PROJECT_CONFIG_FILE} so `{dotted}` is a mapping, or remove it -- "
                f"`schemas/config/project-config.schema.json` publishes the shape of every "
                f"block.{values}"
            ),
        )
    return block
