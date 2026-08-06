# End-to-end tests

These drive the real CLI, the real daemon, and real OS service registration
inside a disposable user profile. They are the only tests that can catch a
packaging, service-registration, or single-instance failure.

**Status:** partial. The suite is specified in
[requirements-analysis §20](../../docs/architecture/requirements-analysis.md#20-theuriansetup-test-strategy)
and this said it "lands with Milestones 3 and 4, when the daemon and setup
service exist". Both milestones are done and it did not land. What exists is
`test_daemon_single_instance.py` and `test_migration_workflow.py`; of the
acceptance criteria below, the daemon ones are covered under different names —
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

They mutate a temporary `HOME` and register real OS services, so they must never
run against a developer's own profile — every fixture asserts an isolated `HOME`
before doing anything.

**No CI job runs them.** This paragraph said they "run in a dedicated job on
macOS and Linux runners". Both occurrences of `e2e` in `.github/workflows/`
*exclude* the marker (`-m "not e2e"`, in `core.yml`'s `test` and `offline`
jobs); the dedicated job has never existed. So the 25 tests in the two files
here run only when someone runs them by hand — including the SEC-7 path- and
symlink-containment cases, and the tests ADR-0002, ADR-0004, ADR-0011 and
ADR-0013 each cite by name to discharge a compliance item. Tracked as
[#65](https://github.com/theurian/theurian/issues/65).
