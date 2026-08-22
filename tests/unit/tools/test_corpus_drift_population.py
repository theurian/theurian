"""Which files are the committed corpus, given what git says the repository ships.

:func:`corpus_drift.migration_paths` is the whole population rule, and it is
deliberately neither the loader's nor the directory's:

- **wider than the loader**, which only reads a ULID-prefixed name. A YAML the
  loader would ignore is still a file this repository publishes, and a checker
  that inherited the loader's predicate would go quiet on exactly the corpus
  file somebody renamed by hand.
- **narrower than the directory**, on two axes. A nested path is not a migration
  -- ``.theurian/migrations/archive/old.yaml`` is somebody's holding area, not a
  document this tool is entitled to hold ``docs/`` to -- and a non-``.yaml``
  entry is not one either, which is what keeps the tracked ``.gitkeep``
  placeholder out of the count.

The count matters as much as the membership: it is printed in the run's own
detail line ("compared N anchor(s) across M committed migration(s)"), and it is
the number that decides ``NOTHING_COMPARED``. A population rule that silently
admitted the ``.gitkeep`` would report a migration that pins nothing as an
uncheckable anchor, in perpetuity.

The sort is pinned for the reason every ordered output here is: the ``::notice``
annotations and the summary table are emitted in this order, and a set's
iteration order is not stable across runs.
"""

from __future__ import annotations

import pytest
from corpus_drift import migration_paths

pytestmark = pytest.mark.unit

_ADR_0005 = ".theurian/migrations/01M0D5GVEH61CTEGASP9T8BDJW-adr-0005-knowledge-migrations.yaml"
_ADR_0013 = ".theurian/migrations/01M0D5GZD9VSWC2JNVH51P8K3N-adr-0013-ai-writes-proposals.yaml"


def test_a_tracked_yaml_directly_under_the_migrations_directory_is_a_migration() -> None:
    """The ordinary member. Without it, `return ()` satisfies every other test here."""
    assert migration_paths({_ADR_0005}) == (_ADR_0005,)


def test_the_gitkeep_placeholder_holding_the_directory_open_is_not_a_migration() -> None:
    """`.theurian/migrations/.gitkeep` is tracked, and pins no body to any document.

    Admitting it would report one permanently uncheckable anchor on every run,
    and inflate the migration count the run's own detail line prints.
    """
    assert migration_paths({".theurian/migrations/.gitkeep"}) == ()


def test_a_yaml_nested_below_the_migrations_directory_is_not_a_migration() -> None:
    """The loader reads the directory itself, not a tree; this population matches it."""
    assert migration_paths({".theurian/migrations/archive/01M0D5GVEH61CTEGASP9T8BDJW.yaml"}) == ()


def test_a_yaml_elsewhere_under_the_corpus_is_not_a_migration() -> None:
    """`.theurian/config.yaml` is configuration, and pins no snapshot to any source.

    Held by the single-directory clause rather than by the prefix: any path
    under `.theurian/` that is not under `.theurian/migrations/` still carries a
    `/` after the prefix is stripped, so no single-clause mutation isolates this
    one. It is kept as defence in depth, not as the prefix's driving case --
    which is the test below.
    """
    assert migration_paths({".theurian/config.yaml"}) == ()


def test_a_yaml_at_the_repository_root_is_not_a_migration() -> None:
    """The driving case for the `.theurian/migrations/` prefix itself.

    A root-level YAML has no directory component, so it is the one shape the
    single-directory clause does not already exclude. Written from nested paths
    alone -- `examples/sample-project/.theurian/config.yaml` and friends -- this
    test passes against a population rule with no prefix check at all: measured,
    by replacing the prefix condition with `True` and watching all 62 tests stay
    green.
    """
    assert migration_paths({"mkdocs.yaml", "examples/sample-project/.theurian/x.yaml"}) == ()


def test_a_yml_suffix_is_not_a_migration_because_the_loader_does_not_read_one_either() -> None:
    """`migration_loader` filters on `endswith(".yaml")`; a `.yml` is never applied.

    Holding `docs/` to a file the product itself would not load asserts a pin
    that nothing in the running system honours.
    """
    assert migration_paths({".theurian/migrations/01M0D5GVEH61CTEGASP9T8BDJW-adr.yml"}) == ()


def test_a_migration_whose_name_is_not_ulid_prefixed_is_still_in_the_population() -> None:
    """Wider than the loader on purpose: a hand-renamed corpus file must not go quiet.

    The loader ignores it, so nothing else in this repository would notice it
    had stopped being checked.
    """
    hand_named = ".theurian/migrations/seed-adr-0005.yaml"

    assert migration_paths({hand_named}) == (hand_named,)


def test_the_population_is_sorted_so_the_report_does_not_reorder_between_runs() -> None:
    """Annotations and the summary table are emitted in this order.

    `tracked` arrives as a `frozenset` from `git ls-files`, whose iteration
    order is not stable across processes.
    """
    assert migration_paths({_ADR_0013, _ADR_0005}) == (_ADR_0005, _ADR_0013)
