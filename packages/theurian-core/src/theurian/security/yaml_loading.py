"""Predictable YAML loading (SEC-8).

Two separate problems, both solved here so no call site has to remember either.

**Arbitrary object construction.** ``yaml.load`` with the default loader
instantiates Python objects named in the document. Every load in Theurian uses a
safe loader.

**Silent type coercion.** ``yaml.safe_load`` applies implicit resolvers that turn
``2026-07-15T10:00:00+09:00`` into a ``datetime`` and ``2026-07-15`` into a
``date``. That breaks JSON Schema validation of a perfectly valid migration --
the schema declares ``type: string`` with ``format: date-time``, and the loader
has already replaced the string with an object. Worse, it is inconsistent:
whether a timestamp survives as a string depends on the exact format the author
used.

Theurian keeps timestamps as strings and parses them explicitly, so the document
that validates is the document that was written.
"""

from __future__ import annotations

from typing import Any, Final

import yaml

from theurian.domain.errors import InputTooLargeError

#: Maximum bytes for a YAML document. Migrations and configuration are small;
#: anything larger is a mistake or a resource-exhaustion attempt (T-6).
MAX_YAML_BYTES: Final = 4 * 1024 * 1024


class _StrictLoader(yaml.SafeLoader):
    """A safe loader that performs no implicit timestamp coercion.

    Subclassed rather than mutated in place so that removing a resolver here
    cannot change the behaviour of ``yaml.safe_load`` elsewhere in the process,
    including inside third-party libraries.
    """


def _drop_implicit_resolver(tag: str) -> None:
    """Remove ``tag`` from :class:`_StrictLoader`'s implicit resolvers.

    ``yaml_implicit_resolvers`` is a class attribute that a subclass inherits **by
    reference**, so mutating it in place would also change ``SafeLoader`` for
    every other user in the process. The dict and its lists are therefore copied
    onto the subclass first -- the same pattern PyYAML's own
    ``add_implicit_resolver`` uses.

    PyYAML keys implicit resolvers by the first character of the scalar, so the
    tag has to be removed from every bucket it appears in.
    """
    if "yaml_implicit_resolvers" not in _StrictLoader.__dict__:
        _StrictLoader.yaml_implicit_resolvers = {
            first_char: resolvers[:]
            for first_char, resolvers in _StrictLoader.yaml_implicit_resolvers.items()
        }

    for first_char, resolvers in list(_StrictLoader.yaml_implicit_resolvers.items()):
        remaining = [(t, regexp) for t, regexp in resolvers if t != tag]
        if remaining:
            _StrictLoader.yaml_implicit_resolvers[first_char] = remaining
        else:
            del _StrictLoader.yaml_implicit_resolvers[first_char]


_drop_implicit_resolver("tag:yaml.org,2002:timestamp")


def load_yaml(text: str, *, max_bytes: int = MAX_YAML_BYTES) -> Any:
    """Load a YAML document safely, leaving timestamps as strings.

    Args:
        text: The document source.
        max_bytes: Size ceiling, in UTF-8 bytes.

    Returns:
        The parsed document. Timestamps remain ``str`` and are parsed explicitly
        by whichever component needs a ``datetime``.

    Raises:
        InputTooLargeError: If the document exceeds ``max_bytes``.
        yaml.YAMLError: If the document is malformed.
    """
    size = len(text.encode("utf-8"))
    if size > max_bytes:
        raise InputTooLargeError("YAML document size", max_bytes, size)

    return yaml.load(text, Loader=_StrictLoader)  # noqa: S506 -- _StrictLoader is SafeLoader-derived


def load_yaml_mapping(text: str, *, max_bytes: int = MAX_YAML_BYTES) -> dict[str, Any]:
    """Load a YAML document that must be a mapping.

    Migrations and configuration files are always mappings. A document that
    parses to a list or a scalar is malformed, and saying so here produces a
    better error than a ``KeyError`` three layers up.
    """
    loaded = load_yaml(text, max_bytes=max_bytes)
    if not isinstance(loaded, dict):
        msg = f"Expected a YAML mapping at the document root, got {type(loaded).__name__}"
        raise ValueError(msg)
    return loaded
