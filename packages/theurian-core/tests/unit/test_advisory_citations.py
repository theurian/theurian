"""Keep source-code security advisory references unambiguous."""

from pathlib import Path


def test_source_has_no_ambiguous_ghsa_97q9_prefixes() -> None:
    source = Path(__file__).parents[2] / "src"
    ambiguous: list[str] = []
    for path in source.rglob("*.py"):
        if "GHSA-97q9" in path.read_text(encoding="utf-8").replace("GHSA-97q9-xxfg-33r6", ""):
            ambiguous.append(str(path.relative_to(source)))

    assert ambiguous == []
