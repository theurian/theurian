# End-to-end tests

These drive the real CLI, the real daemon, and real OS service registration
inside a disposable user profile. They are the only tests that can catch a
packaging, service-registration, or single-instance failure.

**Status:** the suite is specified in
[requirements-analysis §20](../../docs/architecture/requirements-analysis.md#20-theuriansetup-test-strategy)
and lands with Milestones 3 and 4, when the daemon and setup service exist.

## What they will assert

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

They are excluded from the default CI test job (`-m "not e2e"`) and run in a
dedicated job on macOS and Linux runners. They mutate a temporary `HOME` and
register real OS services, so they must never run against a developer's own
profile — every fixture asserts an isolated `HOME` before doing anything.
