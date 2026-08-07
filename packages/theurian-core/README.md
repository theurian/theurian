# theurian (Theurian Core)

Git-native engineering knowledge for AI agents: versioned, governed context that
any MCP client can query and no AI agent can write to.

This file is the project description on
[PyPI](https://pypi.org/project/theurian/), so its links are absolute — a
relative one resolves against the repository and renders as a dead link there.

- [Repository and full README](https://github.com/theurian/theurian)
- [Changelog](https://github.com/theurian/theurian/blob/main/packages/theurian-core/CHANGELOG.md)
- [Threat model](https://github.com/theurian/theurian/blob/main/docs/security/threat-model.md)

## Install

```sh
uv tool install --python 3.13 'theurian[all]'
```

`[all]` carries the MCP daemon; without it you get the CLI and the migration
engine only. Every published version so far is a pre-release.

**Nothing verifies the artifact you just downloaded, including Theurian.** Each
release carries a `SHA256SUMS` on
[its release page](https://github.com/theurian/theurian/releases); checking your
download against it is a manual step, and it catches a substituted download
rather than a compromised release.
