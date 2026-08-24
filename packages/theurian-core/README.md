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
uv tool install --python 3.13 'theurian[daemon]'
```

`[daemon]` carries the MCP daemon; without it you get the CLI and the migration
engine only. Every published version so far is a pre-release.

**Nothing verifies the artifact you just downloaded, including Theurian.** Each
release carries a `SHA256SUMS` on
[its release page](https://github.com/theurian/theurian/releases); checking your
download against it is a manual step, and it catches a substituted download
rather than a compromised release.

## What this daemon will disclose

`~/.theurian/auth/serving-profile` holds one word — `public`, `internal`,
`confidential` or `restricted` — and declares the highest sensitivity class this
deployment serves. Everything above it is withheld from `knowledge.search`, from
`knowledge.get`, from `knowledge.status`'s counts, and from the index build
itself, so the withheld text never reaches a file a query reads. It sits beside
the bearer token at mode 0600, deliberately outside any repository, and a word it
does not recognise refuses at startup rather than falling back to a wider
default.

**With no file the ceiling is `internal`**, so `confidential` and `restricted`
knowledge is withheld until an operator raises it. An installation upgrading past
`0.1.0.dev9` loses results it used to get, with no configuration change on its
part — a behaviour change inside a pre-release line, and the intended one
([#119](https://github.com/theurian/theurian/issues/119)). Raise the ceiling with
`echo restricted > ~/.theurian/auth/serving-profile`, then `theurian index
build`, because a build is specific to the ceiling it ran under. The reasoning is
[ADR-0025](https://github.com/theurian/theurian/blob/main/docs/adr/0025-sensitivity-is-enforced-before-0-1-0-stable.md).
