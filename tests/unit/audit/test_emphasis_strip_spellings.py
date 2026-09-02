"""The emphasis strip is keyed on a tag *spelling*, and both directions are driven.

``claim_surfaces.without_emphasis`` is the normalisation
``config_object_claims`` applies at ``as_read``, before its keys run, and its job
is to remove markup a reader of the rendered sentence sees no trace of.

Round three added ``<b>``, ``<i>``, ``<em>`` and ``<strong>`` to it as four
literal strings; round four planted ``<b class="x">``, ``<b >`` and ``<strong
id="a">`` in a wheel-shipped module and measured all three escaping -- the same
four tags, rendering identically, spelled with an attribute or a space.

So the pattern now matches the tag *name* plus whatever the tag carries up to its
``>``, and that widening has a cost in the other direction: ``<br>``, ``<img>``
and ``<blockquote>`` all begin with a letter the ``b``/``i`` alternatives spell.
The word boundary is what keeps them out, and a boundary is exactly the kind of
detail that survives a rewrite by accident.

Both directions are tabled here rather than asserted in a docstring, because a
docstring saying ``<br>`` is safe stays green when it stops being true.

These tests are ``unit``: one compiled pattern over strings, no filesystem and no
subprocess.
"""

from __future__ import annotations

import pytest
from claim_surfaces import without_emphasis

pytestmark = pytest.mark.unit

#: A claim whose object is wrapped in the tag under test, and the sentence the
#: strip has to leave behind when the tag is one it removes.
_WRAPPED = (
    "Nothing in `src/` reads {open}`.theurian/config.yaml`{close}, so no default is in force."
)
_BARE = _WRAPPED.format(open="", close="")


#: Tag spellings the strip removes, so the wrapped sentence reduces to the bare one.
#:
#: The bare forms are round three's; the attribute, whitespace and mixed-case rows
#: are round four's plants, each measured escaping the literal spelling this
#: pattern had before.
STRIPPED: tuple[tuple[str, str, str], ...] = (
    ("round three: bare <b>", "<b>", "</b>"),
    ("round three: bare <i>", "<i>", "</i>"),
    ("round three: bare <em>", "<em>", "</em>"),
    ("round three: bare <strong>", "<strong>", "</strong>"),
    ("round three: uppercase <B>", "<B>", "</B>"),
    ('round four B1: <b class="x">', '<b class="x">', "</b>"),
    ("round four B2: <b > with a trailing space", "<b >", "</b >"),
    ('round four B3: <strong id="a">', '<strong id="a">', "</strong>"),
    ("round four: <em> carrying a style attribute", '<em style="x: y">', "</em>"),
    ("round four: <B CLASS=x> uppercased whole", "<B CLASS=x>", "</B>"),
)

#: Tag spellings the strip leaves in place, and why each one is here.
#:
#: ``<br>``, ``<img>`` and ``<blockquote>`` are the word-boundary cases: each
#: opens with a letter the pattern's ``b`` or ``i`` alternative spells, and only
#: the boundary keeps the widened form from eating them. ``<code>`` and
#: ``<summary>`` are visible in the render, which is the criterion the strip is
#: chosen by. The six after them are HTML a writer can wrap a claim in without
#: reaching for one of the four, and each is a row in
#: ``config_object_claims.MEASURED_ESCAPES`` that says so at the census level;
#: this is the same fact one layer down, where the reason is the pattern rather
#: than the sweep.
KEPT: tuple[tuple[str, str, str], ...] = (
    ("word boundary: <br>", "<br>", "<br>"),
    ("word boundary: <br />", "<br />", "<br />"),
    ("word boundary: <img src=x>", "<img src=x>", ""),
    ("word boundary: <blockquote>", "<blockquote>", "</blockquote>"),
    ("visible in the render: <code>", "<code>", "</code>"),
    ("visible in the render: <summary>", "<summary>", "</summary>"),
    ("recorded escape: <span>", "<span>", "</span>"),
    ("recorded escape: <ins>", "<ins>", "</ins>"),
    ("recorded escape: <mark>", "<mark>", "</mark>"),
    ("recorded escape: <u>", "<u>", "</u>"),
    ("recorded escape: <s>", "<s>", "</s>"),
    ("recorded escape: <small>", "<small>", "</small>"),
)


@pytest.mark.parametrize(
    ("opening", "closing"),
    [(row[1], row[2]) for row in STRIPPED],
    ids=[row[0] for row in STRIPPED],
)
def test_a_tag_the_strip_covers_leaves_the_bare_sentence(opening: str, closing: str) -> None:
    """RED means a spelling of the four stripped tags moved back out of reach.

    A claim wrapped in one of these renders as the bare claim does, so a key that
    sees the wrapped form and not the bare one is a key a writer defeats by typing
    four characters. Round three measured that with ``<b>``; round four measured it
    again with ``<b class="x">``, because the fix was four literal strings.

    The assertion is the *bare sentence*, not "the tag is gone": a pattern that
    ate the path along with the tag would satisfy the weaker form.
    """
    wrapped = _WRAPPED.format(open=opening, close=closing)

    stripped = without_emphasis(wrapped)

    assert stripped == _BARE, (
        f"`without_emphasis` left {opening!r}/{closing!r} in the sentence.\n\n"
        f"  got     : {stripped!r}\n"
        f"  expected: {_BARE!r}\n\n"
        "This tag renders as `**` renders, so a census key applied to the result "
        "sees markup a reader of the rendered sentence never sees. That is the "
        "escape round three and round four each planted in a wheel-shipped module "
        "with every check green."
    )


@pytest.mark.parametrize(
    ("opening", "closing"),
    [(row[1], row[2]) for row in KEPT],
    ids=[row[0] for row in KEPT],
)
def test_a_tag_outside_the_strip_is_left_where_it_was(opening: str, closing: str) -> None:
    """RED means the strip widened past the four tags it is chosen to cover.

    Two different reasons live in one table. ``<br>``, ``<img>`` and
    ``<blockquote>`` open with a letter the ``b`` and ``i`` alternatives spell, and
    the word boundary after the tag name is the whole of what keeps them out --
    drop it while widening the attribute form and the pattern starts deleting
    line breaks and images out of governed prose. The rest are tags a reader *can*
    see, or tags recorded as escapes in
    ``config_object_claims.MEASURED_ESCAPES``; a strip that silently reached them
    would take rows out of that table without the table failing.
    """
    wrapped = _WRAPPED.format(open=opening, close=closing)

    stripped = without_emphasis(wrapped)

    assert stripped == wrapped, (
        f"`without_emphasis` removed {opening!r}/{closing!r}.\n\n"
        f"  got     : {stripped!r}\n"
        f"  expected: {wrapped!r}\n\n"
        "Either the word boundary after the tag name is gone -- in which case "
        "`<br>` and `<img>` are being deleted out of governed prose -- or a tag "
        "recorded as a measured escape is now reached, which is news that belongs "
        "in `MEASURED_ESCAPES` and in the changelog rather than here."
    )
