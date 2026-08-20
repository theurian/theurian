"""The one place a CLI value is made safe to print to a terminal.

Every text-mode emitter in the CLI -- `commands._render`, `commands._fail`, and
`main._emit` -- routes each value it prints through :func:`escape_terminal_controls`
so that no string reaching a terminal, whatever its source, can move the cursor
or start a line. That is the whole of the "the sink is the closure" claim in the
threat model: it holds because there is exactly one sink and every emitter uses
it, not because each construction site remembered to quote its input.

A proposal directory is committed and contributor-controlled (ADR-0013 point 7),
so a body path or a `contentFile` a command prints is untrusted; an `ESC` or a
carriage return in one rewrites a line the tool has already drawn and forges the
tool's own output (T-3 at the CLI edge).
"""

from __future__ import annotations

from typing import Final

#: The control ranges escaped: the whole C0 block below ``U+0020`` -- ``\n`` and
#: ``\t`` included -- the single ``DEL``, and the C1 block. ``\n`` and ``\t`` are
#: escaped rather than kept because this runs on *values*, and a value's own
#: newline appends a line that reads as the tool's while a tab shifts one: the
#: structural whitespace of the output is the emitter's own f-strings, added
#: outside this function, never a value's. Printable non-ASCII -- a Japanese
#: title -- is below ``DEL`` in none of these ranges and is left untouched, which
#: is why this is not ``repr`` (that would escape it to ``\uXXXX``).
_C0_CEILING: Final = 0x20
_DEL: Final = 0x7F
_C1_FLOOR: Final = 0x80
_C1_CEILING: Final = 0x9F


def escape_terminal_controls(value: object) -> str:
    """``value`` as text with every terminal-control character escaped to ``\\xHH``.

    Idempotent on already-escaped text: the escape it writes is printable ASCII
    that this function then leaves alone, so applying it twice is applying it
    once. Non-string values are rendered with ``str`` first, which is what a
    caller printing an ``int`` or ``None`` expects; only strings can carry a
    control character, so the scan is a no-op for the rest.
    """
    text = value if isinstance(value, str) else str(value)
    if not any(_is_control(char) for char in text):
        return text
    return "".join(f"\\x{ord(char):02x}" if _is_control(char) else char for char in text)


def _is_control(char: str) -> bool:
    point = ord(char)
    return point < _C0_CEILING or point == _DEL or _C1_FLOOR <= point <= _C1_CEILING
