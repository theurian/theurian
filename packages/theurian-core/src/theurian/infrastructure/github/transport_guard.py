"""Refuse a ``gh`` configuration that would move the request, before any spawn.

ADR-0030's runs D-F measured it: with ``--hostname github.com`` pinned and the
child environment reduced to the enumerated constant, a ``http_unix_socket``
setting in the ``gh`` configuration file **still** sent the request to that
socket -- reached through ``GH_CONFIG_DIR``, through ``HOME`` and through
``XDG_CONFIG_HOME`` alike. And the exposure is not "an attacker who could already
read the token": writing the override into the operator's *own* located
directory leaves the keychain handing ``gh`` the credential as usual, so the
authenticated request is captured without the credential ever being read.

**This check is priced honestly, and the price is in the ADR.** It reduces the
accidental, pre-existing, single-well-formed-file case -- a typo'd or inherited
``http_unix_socket``, which is the case an operator actually meets -- and
**nothing above it**. It is not a control against an adversary. What survives is
ADR-0030 decision 1's four-member divergence class, derived from one fact: *this
check's read cannot be gh's read*. The four are (a) the race between the two
reads, (b) a key a newer ``gh`` understands and :data:`TRANSPORT_OVERRIDE_KEYS`
has never heard of, (c) parser divergence -- with the key present twice, PyYAML
takes the last occurrence while ``gh`` dials the first -- and (d) resolution
divergence. Members (c) and (d) need neither timing nor an unknown key.

**The parse-error arm fails open, and that is a decision.** Refusing to spawn on
any configuration this check cannot parse would deny the ingest to precisely the
operator it exists to help -- somebody whose config has a typo -- and would make
a YAML reader's strictness a gate on an unrelated capability. The exposure that
accepts is member (c), recorded above and in the ADR.

**Two properties of the read are load-bearing rather than incidental:**

* It resolves **the same precedence chain gh does** -- ``GH_CONFIG_DIR``, then
  ``$XDG_CONFIG_HOME/gh``, then ``$HOME/.config/gh`` -- reading **exactly one**
  directory, treating an **empty-string** variable as absent and falling through
  exactly as ``gh`` does. A check that read all three, or read them in the wrong
  order, checks a file ``gh`` will not open.
* It runs **before any binary probe**. A version read and an authentication probe
  are themselves spawns, and a check that runs after one has already handed the
  configuration a request.
"""

from __future__ import annotations

import contextlib
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Final

import yaml

from theurian.domain.errors import TheurianError
from theurian.domain.review_ingest import RefusalGrade, ReviewIngestRefusedError
from theurian.security.yaml_loading import load_yaml

#: ``gh``'s configuration file inside the directory it resolves. ``.yml``, not
#: ``.yaml`` -- that is the name ``gh`` writes and reads.
GH_CONFIG_FILE: Final = "config.yml"

#: The settings this check knows can move a request somewhere the argument vector
#: did not choose.
#:
#: **One member, and the set is bounded by measurement rather than by
#: imagination.** ``http_unix_socket`` is a real configuration key on ``gh``
#: 2.86.0; ``api_host``, which an earlier draft of this design assumed, is not.
#: Adding keys nobody has measured would be a universal with no authority behind
#: it. What the set cannot cover is ADR-0030's member (b) -- a setting a *newer*
#: ``gh`` understands -- and the standing obligation is to re-take this set
#: whenever :data:`~theurian.infrastructure.github.limits.GH_VERSION_FLOOR`
#: moves, because that constant is what bounds which binary may run at all.
TRANSPORT_OVERRIDE_KEYS: Final[frozenset[str]] = frozenset({"http_unix_socket"})

#: How much of ``gh``'s configuration file this check will read before giving up
#: on it. A configuration larger than this is treated exactly like one that will
#: not parse: the check fails open and says nothing, per the decision above.
MAX_GH_CONFIG_BYTES: Final = 256 * 1024


def resolved_config_directory(parent: Mapping[str, str]) -> tuple[Path, str] | None:
    """The one directory ``gh`` would read, and the variable that selected it.

    Args:
        parent: The environment the child will be constructed from. The three
            config-locating variables cross into the child by value, so this is
            the same input ``gh`` resolves against.

    Returns:
        ``(directory, the name of the variable that selected it)``, or ``None``
        when none of the three is set to a non-empty value -- in which case
        ``gh`` has no configuration directory this check can name either.

    **Precedence, not union.** ``gh`` reads exactly one directory. An earlier
    draft of this reasoning said the reach was "the union of what they resolve",
    which is not what ``gh`` does, and a check over the union would refuse on a
    file ``gh`` never opens while missing the one it does.

    A **relative** value resolves against the process's own working directory,
    which is the child's too: this adapter passes no ``cwd``, so the child
    inherits it and the two reads agree.
    """
    explicit = parent.get("GH_CONFIG_DIR", "")
    if explicit:
        return Path(explicit), "GH_CONFIG_DIR"
    xdg = parent.get("XDG_CONFIG_HOME", "")
    if xdg:
        return Path(xdg) / "gh", "XDG_CONFIG_HOME"
    home = parent.get("HOME", "")
    if home:
        return Path(home) / ".config" / "gh", "HOME"
    return None


def refuse_transport_overrides(parent: Mapping[str, str]) -> None:
    """Raise when the configuration ``gh`` would read carries a transport override.

    Raises:
        ReviewIngestRefusedError: Graded
            :attr:`~theurian.domain.review_ingest.RefusalGrade.TRANSPORT_OVERRIDE_CONFIGURED`.
            The summary names the **variable** that selected the directory and
            the key that was set, never the absolute path: an operator's home
            directory is not something a published message carries, and the
            variable locates the file just as well.

    Silent -- deliberately -- when the directory does not exist, the file is
    absent, the file cannot be read, it is too large, it does not parse, or it
    parses to something that is not a mapping. Every one of those is the
    fail-open arm this module's docstring records as a decision.
    """
    located = resolved_config_directory(parent)
    if located is None:
        return
    directory, selected_by = located

    document = _parsed_config(directory / GH_CONFIG_FILE)
    if not isinstance(document, dict):
        return

    for key in sorted(TRANSPORT_OVERRIDE_KEYS):
        value = document.get(key)
        # An empty value is how `gh` spells "unset" in this file, so refusing on
        # one would refuse a configuration that moves nothing.
        if isinstance(value, str) and value:
            raise ReviewIngestRefusedError(
                RefusalGrade.TRANSPORT_OVERRIDE_CONFIGURED,
                f"Review ingestion refused to spawn `gh`: the configuration directory "
                f"`{selected_by}` selects carries `{key}` in {GH_CONFIG_FILE}, which "
                f"sends the request somewhere the argument vector did not choose. "
                f"Nothing was spawned and nothing was written.",
            )


def _parsed_config(path: Path) -> Any:
    """``gh``'s configuration file as parsed data, or ``None`` if it cannot be.

    Every failure answers ``None``: this check fails open by decision, so an
    unreadable or unparseable configuration refuses nothing rather than denying
    the ingest to the operator whose file has a typo. The caught set is
    ``load_yaml``'s whole contract -- ``yaml.YAMLError`` for a malformed
    document, ``ValueError`` for one nested past the parser's safe depth, and
    ``TheurianError`` for its own size refusal -- plus the filesystem and decode
    failures of reading the file at all.
    """
    with contextlib.suppress(
        OSError, UnicodeDecodeError, ValueError, TheurianError, yaml.YAMLError
    ):
        with path.open("rb") as handle:
            raw = handle.read(MAX_GH_CONFIG_BYTES + 1)
        if len(raw) > MAX_GH_CONFIG_BYTES:
            return None
        return load_yaml(raw.decode("utf-8"))
    return None
