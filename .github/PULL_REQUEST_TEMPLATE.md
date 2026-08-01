## What and why

<!-- What changed, and what problem it solves. The diff shows what; explain why. -->

## Scope

<!-- One topic per PR. If this touches two unrelated concerns, split it. -->

- [ ] Theurian Core
- [ ] Claude Code plugin
- [ ] Shared schemas (**needs both maintainer groups**)
- [ ] Documentation only

## Checks

- [ ] `uv run ruff format --check packages tests`
- [ ] `uv run ruff check packages tests`
- [ ] `uv run mypy`
- [ ] `uv run pytest --cov` (>= 80%)
- [ ] Every commit is signed off (`git commit -s`)
- [ ] Commit subjects follow Conventional Commits

## Tests

<!-- What did you add, and what failure would it catch?
     "Added tests" is not an answer; name the behaviour that is now covered. -->

## Architectural constraints

Confirm this change does not break any of these. If it must, link the ADR that
authorises it.

- [ ] `domain/` imports nothing from `application/` or `infrastructure/`
- [ ] `application/` depends on ports, not adapters
- [ ] No file under `plugins/` imports `theurian`
- [ ] The plugin manifest still declares no `mcpServers` entry
- [ ] The `SessionStart` hook still performs no install, rebuild, or mutation
- [ ] No test calls an external service
- [ ] Every new dependency is pinned with `==`

## Security

- [ ] No secret is logged, stored in a config file, or included in an error
- [ ] Every new filesystem path goes through `theurian.security.paths`
- [ ] Every new external call has a timeout
- [ ] New parser input is size- and depth-limited
- [ ] Nothing here lets an agent write approved knowledge

## Compatibility

- [ ] No change to the published contract
- [ ] Additive change (no protocol bump)
- [ ] **Breaking change** -- protocol bumped, clients listed below

<!-- If breaking: which clients must update, and in what order? -->

## ADR

- [ ] No architectural decision here
- [ ] ADR added or updated: <!-- ADR-NNNN -->

## Related

<!-- Closes #123 -->
