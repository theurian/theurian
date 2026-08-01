# Security Policy

## Reporting a vulnerability

**Do not open a public issue for a security vulnerability.**

Report privately through GitHub's ["Report a vulnerability"](https://github.com/theurian/theurian/security/advisories/new)
form, which creates a private advisory visible only to maintainers.

Please include: what you observed, how to reproduce it, the affected version
(`theurian version --json`), your platform, and the impact as you see it. A
proof of concept helps but is not required to report.

### What to expect

| Stage | Target |
| :-- | :-- |
| Acknowledgement | 3 business days |
| Initial assessment and severity | 7 business days |
| Fix or mitigation for critical issues | 30 days |
| Coordinated disclosure | by agreement, default 90 days |

We will credit you in the advisory unless you prefer otherwise. There is no bug
bounty; this is a volunteer project.

## Supported versions

Pre-1.0, only the latest MINOR release receives security fixes. Once 1.0 ships,
this table will list a supported window.

| Version | Supported |
| :-- | :-- |
| 0.1.x | ✅ |
| < 0.1 | ❌ |

## Theurian's security model

### What Theurian protects

- **The local endpoint.** The daemon binds `127.0.0.1` only, validates `Origin`
  and `Host` against DNS rebinding, and requires a bearer token with ≥256 bits of
  entropy on every request. `GET /health` is the sole unauthenticated endpoint
  and returns only liveness and version.
- **The token.** Stored in a 0600 file inside a 0700 directory, and in the OS
  secret store where one is available. A world-readable token file is refused,
  not used. The token never appears in a config file, a log, an error message, or
  `doctor` output — redaction happens at the logging sink, not at each call site.
- **The filesystem boundary.** Every path is resolved with `realpath` and checked
  for containment in the project root. `..` traversal, absolute paths, and
  symlinks that leave the root are all refused, including symlinks on
  intermediate path components.
- **Parser input.** Size, depth, and expansion-ratio limits, with safe loaders
  only (`yaml.safe_load`). External `$ref` targets are recorded as unresolved,
  never fetched.
- **Sensitivity boundaries.** A RAPTOR summary node's tree identity includes
  project, tenant, sensitivity, ACL group, and namespace. A node combining two
  different sensitivity levels has no tree it could belong to, so mixing is
  impossible by construction rather than prevented by a check.
- **Approved knowledge.** No MCP tool can write it. Write-intent tools emit
  proposal files that a human reviews and merges.

### What Theurian does not protect against

Stated plainly, because a security model with unstated gaps is worse than none.

- **A compromised user account.** Anything running as your user can read the
  token file. Theurian raises the bar from "any script" to "already has your
  filesystem access"; it does not eliminate that class.
- **Malicious content you choose to ingest.** Theurian labels every retrieval
  result `contentClassification: untrusted-knowledge`,
  `mayContainInstructions: true`, `executable: false`. **Enforcing that label is
  the calling agent's responsibility.** Theurian cannot stop an agent that treats
  document text as instructions, and no MCP server can. This is a shared
  responsibility, and it is the most important line in this document.
- **Secrets already committed to your repository.** Ingestion warns or blocks per
  policy, but Theurian is not a secret scanner and should not be your only one.
- **Network-level attackers.** The OSS Core is loopback-only by design. Exposing
  it to a network is unsupported. A hosted deployment requires TLS, OAuth 2.1,
  audience and scope validation, and tenant isolation — none of which the local
  daemon implements, because it does not need them and shipping half of them
  would be worse than shipping none.

## Personal data in review knowledge

Ingested review history contains author identity and opinions.

- Author identity is stored as the provider's stable ID plus a display name, so
  a display name can be redacted without breaking the identity graph.
- Redaction at ingestion is configurable.
- Review data is a derived artifact under `.theurian/cache/`, git-ignored and
  rebuildable from the source system.
- Deleting a project's cache removes the ingested copy; the source system remains
  the record.

If you operate Theurian somewhere with data-protection obligations, treat the
canonical store as containing personal data and apply your normal retention
policy to `.theurian/`.

## Sharing diagnostics safely

`theurian doctor --report --json` redacts credentials and knowledge bodies by
default. Review its output before posting it anywhere public — your knowledge
base may contain information your issue does not need.

## Dependencies

Every dependency is pinned exactly, `uv.lock` is committed, and CI runs
`uv sync --frozen`. Dependabot proposes upgrades; CI decides; a human merges.
Each release carries a CycloneDX SBOM and SHA-256 checksums.

## Further reading

- [Threat model](docs/security/threat-model.md) — trust boundaries and sixteen enumerated threats
- [Local MCP security](docs/security/local-mcp.md)
- [ADR-0011: local MCP authentication](docs/adr/0011-local-mcp-authentication.md)
- [ADR-0013: AI writes produce proposals](docs/adr/0013-ai-writes-produce-proposals.md)
