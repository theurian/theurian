# End-to-end tests

These drive the installed `theurian` binary as real OS processes, against real
Git repositories and SQLite files under pytest's `tmp_path`: real daemons
serving real ports, and two `migrate apply` processes racing one project. What
they reach that an in-process test cannot is the process boundary — the
installed entry point, a second process, a daemon over a socket. None of them
registers an OS service: no test here runs `setup` or `uninstall`, or calls
`launchctl` or `systemctl` (key:
`git grep -n -E 'launchctl|systemctl|LaunchAgentManager|SystemdUserManager|"(setup|uninstall)"' -- 'tests/e2e/*.py'`,
which prints nothing).

**Status:** partial. The suite is specified in
[requirements-analysis §20](../../docs/architecture/requirements-analysis.md#20-theuriansetup-test-strategy)
and this said it "lands with Milestones 3 and 4, when the daemon and setup
service exist". Both milestones are done and it did not land. What exists is
five test modules (key: `git ls-files 'tests/e2e/test_*.py'`) —
`test_accept_operation_bounds.py`, `test_daemon_single_instance.py`,
`test_migrate_apply_concurrency.py`, `test_migration_workflow.py` and
`test_write_intent_session.py`; of the acceptance criteria below, the daemon
ones are covered under different names —
`test_many_concurrent_clients_share_one_daemon` and
`test_concurrent_starts_produce_one_winner` — and the setup, uninstall and
coexistence rows have nothing. Those need a disposable machine to register a
real LaunchAgent against, which is the same obstacle ADR-0012 records for its
own Still-owed E2E item.

## What they will assert

None of the test names in this table exists yet; the table is the specification,
not an index.

| Acceptance criterion | Test |
| :-- | :-- |
| Setup works from a clean machine | `test_setup_first_run` |
| Setup is idempotent | `test_setup_idempotence` |
| Plugin install alone does nothing | `test_install_is_inert` |
| One daemon for 10+ clients | `test_single_daemon` |
| Projects do not cross-contaminate | `test_project_isolation` |
| A pinned snapshot is stable | `test_snapshot_pinning` |
| Concurrent starts produce one winner | `test_concurrent_start` |
| Files outside the root are unreachable | `test_path_containment` |
| SessionStart stays within budget | `test_session_start_is_cheap` |
| Approved knowledge survives plugin removal | `test_uninstall_preserves_knowledge` |
| Uninstall scopes are independent | `test_uninstall_matrix` |
| Serena and Theurian coexist | `test_serena_coexistence` |
| Incompatible versions are detected | `test_version_incompatibility` |

## Running them

```sh
uv run pytest -m e2e
```

They must never run against a developer's own Theurian profile, and what keeps
them off it is `THEURIAN_DATA_DIR`, not `HOME`: every CLI call here
(population: `git grep -n -E 'THEURIAN[],)]' -- 'tests/e2e/*.py'`) runs with it
pointed at a directory under pytest's `tmp_path`. This said they "mutate a
temporary `HOME`" and that "every fixture asserts an isolated `HOME`"; no file
here sets `HOME` (key: `git grep -n '"HOME"' -- 'tests/e2e/*.py'`, which prints
nothing). Nothing checks the redirect before a test acts —
`test_health_is_reachable_without_a_credential` asserts afterwards that one
running daemon reports the redirected `dataDir` — so a new test that forgets it
writes to the real `~/.theurian`. With it in place, the full local gate
(`uv run pytest -q`, no marker filter) runs them on a development machine
without touching the login session: no test registers a service (above), and
every `daemon start` here carries `--foreground` and a port `_free_port()`
picked (key: `git grep -n '"daemon", "start"' -- 'tests/e2e/*.py'`).

**`core.yml`'s `e2e` job runs them on pull requests (#634).** This paragraph
once said they "run in a dedicated job on macOS and Linux runners"; until #634
that job did not exist, and `core.yml`'s `test` and `offline` jobs *exclude* the
marker (`-m "not e2e"`). The job runs `tests/e2e`, so the SEC-7 path- and
symlink-containment cases, and the tests ADR-0002, ADR-0004, ADR-0011, ADR-0013
and ADR-0016 each cite by name to discharge a compliance item, are now exercised
on a pull request. Tracked as [#65](https://github.com/theurian/theurian/issues/65) (closed); the job landed with #634.
