"""Building an index must not make search worse than having none (ADR-0023).

A trigram index stores three-character grams, so a term shorter than three
characters has no gram to look up, and including one makes FTS5 answer nothing
for the *whole* expression. The matcher therefore drops such a term — and for a
Japanese corpus that is not a degradation, it is a deletion. `unicode61` splits
on whitespace and punctuation only, so a Japanese sentence becomes one token and
the word index cannot answer either; the trigram retriever is the only one that
can. 認証, 決済, 監査, 契約 are two characters each, which is the most common noun
length in the language, and 鍵 is one.

So the state this file pins is the one that made the defect embarrassing rather
than merely wrong: *before* `theurian index build`, the unranked canonical scan
answers 認証 by substring, because Python's `in` needs no grams. *After* it, the
documented, recommended, one-way operation made the same query return nothing —
`count: 0, indexed: true`, no `fallbackReason`, which an agent reads as "this
team has made no such decision".

That comparison is the test. Not "認証 returns something", which a corpus can
satisfy by accident, but "the ranked path returns at least what the unranked one
does" — a property no fixture can fake, which stays meaningful when the
retrievers change underneath it.

**Asserted for the terms in `SHORT_QUERIES`, and it is not a law about all
strings.** The unranked scan takes the whole query as one literal substring, so
it answers things that are not questions: a lone `。` is in every Japanese
paragraph and a lone `#` in every Markdown heading, and both would satisfy
"returns at least as much" only by making the ranked path read the entire corpus
to say so. The ranked path declines them on purpose — see
`index_query._is_worth_scanning` — so the comparison holds for terms that are
words and is deliberately false for single characters that are punctuation.
Stated here because a claim of universality is how the next reader concludes a
regression is a rule.

Real repositories, a real index, and the real MCP entry point, all under
``tmp_path`` with ``THEURIAN_DATA_DIR`` redirected.
"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from migration_fixtures import body_pin
from typer.testing import CliRunner

from theurian.application.project_service import ProjectRegistry
from theurian.cli.main import app
from theurian.daemon.runner import build_server

pytestmark = pytest.mark.integration

runner = CliRunner()

BODY = """# 認証ポリシー

すべての受信呼び出しは署名付きトークンを運ぶ。ゲートウェイはハンドラの実行前に
署名を検証し、署名のない要求は401で拒否する。

## 鍵の交換

資格情報を交換すると、直前のものは即座に無効になる。交換の記録は監査ログに残る。

## 決済の扱い

決済に関わる操作はすべて監査対象とし、四半期ごとに棚卸しする。
"""

MIGRATION = f"""apiVersion: theurian.dev/v1
id: 01K1AAAAAA01234567890ABCDE
createdAt: 2026-08-02T10:00:00+09:00
author: engineer@example.com
operations:
  - op: createItem
    itemId: architecture.ninsho
    kind: architecture
    namespace: backend
    owner: platform-team
  - op: upsertRevision
    itemId: architecture.ninsho
    revisionId: 01K1AAAREV01234567890ABCDE
    contentFile: ../knowledge/architecture/ninsho.md
    contentSha256: {body_pin(BODY)}
    metadata:
      title: 認証ポリシー
      contentType: text/markdown
      kind: architecture
      namespace: backend
      status: approved
      owner: platform-team
      trustLevel: reviewed
      sourceAnchors:
        - provider: git
          sourceUri: git://demo/ninsho.md
"""

#: Every one of these is below the trigram floor and present in the body.
#: 鍵 is a single character, which is the hardest case and the one a floor
#: tightened "just a little" would take away first.
SHORT_QUERIES = ("認証", "決済", "鍵", "監査")

#: Three characters or more, so these reach the trigram index by lookup. They
#: are the control: if the short ones failed and these did too, the fixture
#: would be broken rather than the floor.
#:
#: `四半期` sits exactly on the floor, and it is here to say that three
#: characters is answered — **not** to bound the floor from above, which it
#: cannot do. Raising `_MIN_TRIGRAM_CHARS` to four empties the expression for a
#: lone three-character term, and an empty expression is the one case that falls
#: through to `_scan_below_the_trigram_floor`, so `四半期` is still answered by
#: `LIKE` and this parametrisation still passes. Measured, not assumed. What
#: does bound the floor is
#: `test_a_three_character_term_survives_a_query_that_also_carries_a_longer_one`
#: below, where the fall-through cannot fire.
LONG_QUERIES = ("トークン", "署名付きトークン", "資格情報", "四半期")

#: A term of exactly three characters, mixed with one that is longer and absent
#: from the corpus.
#:
#: `棚卸し` is in the body; `ロールバック` is in no document here, and is six
#: characters, so it survives any tightening of the floor a reader might attempt.
#: Its only job is to keep the trigram expression *non-empty*, which is what
#: shuts the scan fall-through off — see the test that uses it.
MIXED_FLOOR_QUERY = "ロールバック 棚卸し"


@pytest.fixture
def indexed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[ProjectRegistry]:
    """A Japanese-language project with a published index.

    **Synchronous**, because `theurian index build` embeds through
    `asyncio.run`, which raises inside an already-running loop.
    """
    root = tmp_path / "demo"
    root.mkdir()
    for args in (
        ["git", "init", "-q", "-b", "main"],
        ["git", "config", "user.email", "test@example.com"],
        ["git", "config", "user.name", "Test"],
    ):
        subprocess.run(args, cwd=root, check=True, capture_output=True)  # noqa: S603

    data_dir = tmp_path / "datadir"
    monkeypatch.setenv("THEURIAN_DATA_DIR", str(data_dir))
    monkeypatch.chdir(root)

    _run("init")
    (root / ".theurian/knowledge/architecture/ninsho.md").write_text(BODY, encoding="utf-8")
    (root / ".theurian/migrations/01K1AAAAAA01234567890ABCDE-ninsho.yaml").write_text(
        MIGRATION, encoding="utf-8"
    )
    _run("project", "register")
    _run("migrate", "apply")
    _run("index", "build")

    yield ProjectRegistry.default(data_dir)


def _run(*args: str) -> None:
    result = runner.invoke(app, [*args, "--json"], catch_exceptions=False)
    assert result.exit_code == 0, result.stdout + (result.stderr or "")


async def _search(registry: ProjectRegistry, query: str) -> dict[str, Any]:
    result = await build_server(registry).call_tool(
        "knowledge.search", {"projectId": "demo", "query": query}
    )
    structured = getattr(result, "structuredContent", None)
    if structured is not None:
        payload: dict[str, Any] = structured
        return payload
    content: Any = result.content  # type: ignore[union-attr]
    loaded: dict[str, Any] = json.loads(content[0].text)
    return loaded


def _drop_the_index(registry: ProjectRegistry) -> None:
    """Send the next search down the unranked canonical scan.

    Only the pointer is removed. The index is derived (ADR-0004), so this is a
    supported state rather than a corruption, and it is the state every project
    is in before its first build.
    """
    root = Path(registry.load()["demo"]["rootPath"])
    (root / ".theurian/state/active-index.json").unlink()


@pytest.mark.asyncio
@pytest.mark.parametrize("query", SHORT_QUERIES)
async def test_a_query_below_the_trigram_floor_is_answered_from_the_index(
    indexed: ProjectRegistry, query: str
) -> None:
    """The whole point of shipping a trigram index at all.

    Asserted with `indexed: true` because the honest-looking alternative is a
    fallback: an answer that arrives by standing the index aside satisfies
    "returns something" while leaving the ranked path exactly as blind as it was.
    """
    result = await _search(indexed, query)

    assert result["retrieval"]["indexed"] is True, "the ranked path must be what answered"
    assert result["count"] >= 1
    assert result["results"][0]["itemId"] == "architecture.ninsho"


@pytest.mark.asyncio
@pytest.mark.parametrize("query", SHORT_QUERIES)
async def test_building_an_index_never_answers_less_than_having_none(
    indexed: ProjectRegistry, query: str
) -> None:
    """The invariant, stated as the comparison that caught the defect.

    `theurian index build` is documented, recommended, and one-way in practice.
    A user who runs it and then finds their Japanese knowledge base unsearchable
    has no way to attribute that to the build — the response said `indexed:
    true` and carried no `fallbackReason`, so it read as "we have no such
    decision" rather than "this retriever cannot see your corpus".

    Both halves matter. The fallback count is asserted non-zero first, so the
    comparison cannot be satisfied by a query that matches nothing either way.
    """
    ranked = await _search(indexed, query)
    _drop_the_index(indexed)
    unranked = await _search(indexed, query)

    assert unranked["count"] >= 1, "the unranked scan finds it, as Python's `in` always could"
    assert ranked["count"] >= unranked["count"], "building an index must never take an answer away"


@pytest.mark.asyncio
@pytest.mark.parametrize("query", LONG_QUERIES)
async def test_a_query_above_the_floor_was_already_answered(
    indexed: ProjectRegistry, query: str
) -> None:
    """The control that locates the defect at the floor rather than in the
    fixture.

    These reach the trigram index by lookup and worked before the floor was
    given somewhere to fall through to. If they failed too, the corpus or the
    build would be broken and the tests above would be measuring that instead.
    """
    result = await _search(indexed, query)

    assert result["retrieval"]["indexed"] is True
    assert result["count"] >= 1


@pytest.mark.asyncio
async def test_a_three_character_term_survives_a_query_that_also_carries_a_longer_one(
    indexed: ProjectRegistry,
) -> None:
    """ADR-0023. The floor bounded from above, where nothing catches what it drops.

    A trigram is three characters, so three characters is the shortest term the
    index can look up and the floor has no business being higher. Nothing said
    so: `_MIN_TRIGRAM_CHARS` was moved from 3 to 4 and the whole suite passed,
    including every query in `SHORT_QUERIES` and `LONG_QUERIES`.

    It passes because the obvious control cannot see the change. A *lone*
    three-character term empties the expression, and an empty expression is the
    one case `to_trigram_expression` documents a fall-through for — the query
    reaches `_scan_below_the_trigram_floor` and `LIKE` answers it, at a cost, by
    a different mechanism than the one that was broken. So "四半期 returns
    something" stays true whatever the floor is.

    This query is the shape the fall-through does not cover, and it is the
    residual `to_trigram_expression` states in its own docstring: a short term
    *mixed with* a long one is dropped silently, because the expression is
    non-empty and the floor never fires. `ロールバック` matches no document here,
    so at a floor of three the answer comes entirely from `棚卸し` — and at a
    floor of four there is no answer at all, from any retriever: `unicode61`
    cannot segment the Japanese body, so the word index cannot cover for it.

    That failure is silent in the way this whole file exists to prevent —
    `count: 0`, `indexed: true`, no `fallbackReason` — which an agent reads as
    "this team has made no such decision".
    """
    result = await _search(indexed, MIXED_FLOOR_QUERY)

    assert result["retrieval"]["indexed"] is True, "the ranked path must be what answered"
    assert result["count"] >= 1, "the three-character half of the query must still be looked up"
    assert result["results"][0]["itemId"] == "architecture.ninsho"


@pytest.mark.asyncio
async def test_the_long_half_of_the_mixed_query_matches_nothing_on_its_own(
    indexed: ProjectRegistry,
) -> None:
    """Guards the test above, whose whole argument rests on this.

    If `ロールバック` were in the corpus, the mixed query would be answered
    through the long term at any floor and the assertion above would hold while
    the short term was being dropped — which is precisely the failure it claims
    to catch. Asserted rather than assumed, because a later edit to `BODY` is
    exactly how a corpus acquires a word.
    """
    result = await _search(indexed, "ロールバック")

    assert result["count"] == 0, "the long term must contribute nothing but a non-empty expression"


@pytest.mark.asyncio
@pytest.mark.parametrize("query", ["。", "#"])
async def test_a_lone_punctuation_mark_is_declined_rather_than_answered(
    indexed: ProjectRegistry, query: str
) -> None:
    """The one place the comparison above deliberately does not hold.

    Both characters are in the body — `。` ends every Japanese sentence and `#`
    opens every Markdown heading — so the unranked scan, which matches the whole
    query as one literal substring, answers them. The ranked path reads every row
    in the index to do the same, and returns "the fifty the sort favoured" with a
    fused score attached, which is a ranked answer to a question nobody asked.

    Asserted so that the exception to the invariant is a decision on record and
    not a regression someone later restores by lowering the floor again.
    """
    ranked = await _search(indexed, query)
    _drop_the_index(indexed)
    unranked = await _search(indexed, query)

    assert unranked["count"] >= 1, "the unranked scan takes the query as a literal substring"
    assert ranked["count"] == 0, "and the ranked path declines to scan the corpus for it"


@pytest.mark.asyncio
async def test_a_short_query_that_is_absent_still_returns_nothing(
    indexed: ProjectRegistry,
) -> None:
    """A scan that answered every short query would trade one broken search for
    another, and this branch has no index behind it — nothing but the `LIKE`
    pattern keeps it honest.

    `課金` is two characters, so it takes exactly the same path as `認証`.
    """
    result = await _search(indexed, "課金")

    assert result["count"] == 0
    assert result["retrieval"]["indexed"] is True, "and it is still the ranked path saying so"
