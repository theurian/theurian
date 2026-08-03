"""Splitting documents into retrievable passages (FR-R2)."""

from __future__ import annotations

import pytest

from theurian.domain.chunking import MIN_CHARS, ChunkingError, chunk_document

REVISION = "01K1AAAREV01234567890ABCDE"


def test_an_empty_body_yields_no_chunks() -> None:
    """An empty chunk matches nothing and costs a row."""
    assert chunk_document(REVISION, "   \n\n  ") == ()


def test_a_short_document_is_one_chunk() -> None:
    chunks = chunk_document(REVISION, "# Title\n\nA short policy statement about tokens.")

    assert len(chunks) == 1
    assert "short policy" in chunks[0].text


def test_chunk_ids_are_deterministic_and_scoped_to_the_revision() -> None:
    """FR-R7. An index rebuild over unchanged content must leave a pinned
    result resolvable, which requires the same text to produce the same ids."""
    body = "# A\n\n" + ("alpha " * 300) + "\n\n# B\n\n" + ("beta " * 300)

    first = chunk_document(REVISION, body)
    second = chunk_document(REVISION, body)

    assert [c.chunk_id for c in first] == [c.chunk_id for c in second]
    assert all(c.chunk_id.startswith(f"{REVISION}#") for c in first)


def test_splitting_prefers_headings_over_length() -> None:
    """A heading is a boundary the author chose. A character count is one we
    picked because it was cheap."""
    body = "# Authentication\n\n" + ("auth " * 100) + "\n\n# Caching\n\n" + ("cache " * 100)

    chunks = chunk_document(REVISION, body)

    headings = [c.heading for c in chunks]
    assert "Authentication" in headings
    assert "Caching" in headings
    assert not any("auth" in c.text and "cache" in c.text for c in chunks)


def test_an_over_long_section_is_split_on_paragraphs_not_mid_sentence() -> None:
    """A passage cut in half retrieves on terms it no longer explains."""
    paragraphs = "\n\n".join(f"Paragraph {i} about tokens and rotation." * 12 for i in range(6))

    chunks = chunk_document(REVISION, f"# Long\n\n{paragraphs}", target_chars=500)

    assert len(chunks) > 1
    assert all(not c.text.endswith(("token", "rotatio")) for c in chunks)


def test_a_heading_with_one_sentence_is_folded_into_what_follows() -> None:
    """Too little signal to rank on, too little context to read."""
    body = "# Intro\n\nShort.\n\n# Body\n\n" + ("detail " * 200)

    chunks = chunk_document(REVISION, body)

    assert all(c.char_count >= MIN_CHARS or len(chunks) == 1 for c in chunks)


def test_a_document_without_headings_still_chunks() -> None:
    prose = " ".join(f"Sentence {i} of plain prose about tokens." for i in range(80))

    chunks = chunk_document(REVISION, prose, target_chars=500)

    assert len(chunks) > 1
    assert all(c.heading == "" for c in chunks)


def test_text_with_no_sentence_or_paragraph_boundaries_is_still_bounded() -> None:
    """A base64 blob, a minified file, or a long token list yields to none of
    the structural boundaries. Returning it as one unbounded chunk would spend a
    caller's entire budget on a single hit they cannot use.
    """
    blob = "alpha " * 800  # no punctuation, no blank lines

    chunks = chunk_document(REVISION, blob, target_chars=500)

    assert len(chunks) > 1
    assert all(c.char_count <= 600 for c in chunks), "bounded even with no structure"
    assert all(not c.text.startswith("lpha") for c in chunks), "never mid-word"


def test_ordinals_are_contiguous_and_start_at_zero() -> None:
    chunks = chunk_document(REVISION, "# A\n\n" + ("x " * 600) + "\n\n# B\n\n" + ("y " * 600))

    assert [c.ordinal for c in chunks] == list(range(len(chunks)))


def test_no_content_is_lost_when_splitting() -> None:
    """The index is derived, but silently dropping a paragraph would make a
    document unfindable by the one sentence that mattered."""
    body = "# A\n\n" + "\n\n".join(f"unique-marker-{i} " * 40 for i in range(8))

    chunks = chunk_document(REVISION, body, target_chars=400)
    combined = " ".join(c.text for c in chunks)

    assert all(f"unique-marker-{i}" in combined for i in range(8))


def test_a_nonsensical_target_is_refused() -> None:
    with pytest.raises(ChunkingError, match="at least"):
        chunk_document(REVISION, "text", target_chars=10)


def test_japanese_prose_is_split_on_its_own_sentence_marks() -> None:
    """Japanese puts no space after a full stop.

    A sentence pattern that required trailing whitespace would match nothing,
    and the word fallback splits on spaces that are not there either -- so an
    entire Japanese document would come back as one chunk. This is the case that
    only showed up by running it.
    """
    body = "認証は必ずトークンを検証する。" * 60

    chunks = chunk_document(REVISION, body, target_chars=400)

    assert len(chunks) > 1
    assert all(c.char_count <= 400 for c in chunks)


def test_text_with_no_boundaries_of_any_kind_is_still_bounded() -> None:
    """Unbroken CJK prose has no spaces and no sentence marks. Nothing above the
    hard character cut can split it, and it must still be bounded."""
    chunks = chunk_document(REVISION, "認証" * 2000, target_chars=400)

    assert len(chunks) > 1
    assert all(c.char_count <= 400 for c in chunks)


def test_an_abbreviation_does_not_split_english_prose_mid_sentence() -> None:
    """`.` splits only when whitespace follows, so "e.g." and "3.14" stay put."""
    body = "The rate is 3.14 per second, e.g. under load. " * 40

    chunks = chunk_document(REVISION, body, target_chars=500)

    assert all(not c.text.startswith(("14", "g.")) for c in chunks)


def test_the_hard_character_cut_loses_no_content() -> None:
    """`_by_length` is the last resort and the only cut that always terminates.

    Advancing its window by two targets instead of one silently dropped half of
    every document that reached it, and the whole suite stayed green: every
    chunking test asserted a *bound* on the output and none compared the output
    against the input.
    """
    body = "".join(chr(0x3042 + (index % 80)) for index in range(5_000))

    chunks = chunk_document(REVISION, body, target_chars=400)

    assert "".join(chunk.text for chunk in chunks) == body, "no character is dropped"
    assert all(chunk.char_count <= 400 for chunk in chunks)
