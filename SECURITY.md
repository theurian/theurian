# Security Policy

## Reporting a vulnerability

**Do not open a public issue for a security vulnerability.**

Report privately through GitHub's ["Report a vulnerability"](https://github.com/theurian/theurian/security/advisories/new)
form, which creates a private advisory visible only to maintainers.

Please include: what you observed, how to reproduce it, the affected version,
your platform, and the impact as you see it. For Core that is
`theurian version --json`; for the Claude Code plugin it is the version `/plugin`
shows, or `version` in `plugins/claude-code/.claude-plugin/plugin.json` — the
Core command reports Core and the protocol, and the plugin is not in it. A proof
of concept helps but is not required to report.

### What to expect

| Stage | Target |
| :-- | :-- |
| Acknowledgement | 3 business days |
| Initial assessment and severity | 7 business days |
| Fix or mitigation for critical issues | 30 days |
| Coordinated disclosure | by agreement, default 90 days |

We will credit you in the advisory unless you prefer otherwise. There is no bug
bounty; this is a volunteer project.

## How we handle a vulnerability

The section above is what a reporter does; this is what we do once a
vulnerability is known. **It does not change what a reporter should do: report
privately, whether or not a release exists.** A defect reported privately is not
described in public before its fix is delivered or the clock below runs out, and
we name a reporter only with their agreement.

What decides our handling is whether the artifact holding the defect has been
released. Theurian ships two on independent release trains
([ADR-0001](docs/adr/0001-monorepo-with-independent-artifacts.md)), and **they
are not in the same state**:

| Artifact | State | What settles it |
| :-- | :-- | :-- |
| Claude Code plugin | **Released**, at `0.1.0` | the `theurian` entry in [theurian-plugins](https://github.com/theurian/theurian-plugins)' `marketplace.json` |
| Theurian Core | **Pre-release**, until the first `core-v*` tag is pushed | `git ls-remote --tags https://github.com/theurian/theurian 'refs/tags/core-v*'` |

Core tags are `core-v*` and plugin tags are `plugin-v*` (release process:
[Core](docs/contributing/release.md#4-tag),
[plugin](docs/contributing/release.md#4-tag-and-publish)) — pushed to that URL
rather than created, because a tag in a local clone or a fork distributes
nothing. No output from that command means no such tag; it exits 0 either way, so
a network failure reads the same as an answer.

**A defect is routed by whose users it exposes, not by which tree it sits in**,
and where the two differ the users decide: a control living in Core's test tree
that keeps credentials out of what the plugin ships is routed by the plugin's
users. Which tree ships with which artifact is a fact about the build, not about
ownership — Core's wheel force-includes `schemas/` and an installed build reads
that packaged copy first, so a defect there is Core's, while `tests/contract/`,
`tests/e2e/` and `docs/` are shipped by neither, as are this file, the README and
the workflows. A document that states a control wrongly is corrected in public:
the correction is the mitigation, and a reader acting on a false claim is the
harm — what that correction may *say* is still the conditions' question. Where no
artifact's users are reached, the defect is public.

**The plugin's delivery point is not a merge here.** The marketplace entry pins a
commit sha, so a fix on `theurian/theurian@main` reaches nobody until the pin
moves *and* the declared version is bumped with it (below) — and what travels
through it includes `hooks/hooks.json`, which wires `SessionStart` to a shell
script that runs in the user's session.

Either side of the boundary, the fix is recorded in the Security section of its
artifact's changelog ([Core](packages/theurian-core/CHANGELOG.md),
[Claude Code plugin](plugins/claude-code/CHANGELOG.md)) and in the
[threat model](docs/security/threat-model.md).

### A vulnerability in a released, supported artifact

Whoever found it: a private advisory before the fix is public, a release that
carries the fix, and the advisory published with it. Which releases are supported
is the table below. There is no bar on this side, and the asymmetry is
deliberate: once a release exists there are installs that a reader cannot fix by
updating a checkout, so the default flips to private.

The fix is written in the temporary private fork a
[security advisory](https://github.com/theurian/theurian/security/advisories/new)
provides — the only route this repository uses to hold one back. The advisory is
published when the fix is **delivered, or ninety days after we confirmed the
vulnerability, whichever comes first — whether or not there is a reporter.** Most
of what this section governs we found ourselves, so a clock that starts at an
agreed date does not start at all; and delivery is a hand-driven act, so an
embargo without a deadline is one that ends when somebody remembers.

Delivered means something different on each train, and for the plugin it is not
the pin:

- **Core** — the release that carries the fix.
- **The plugin** — a bumped `version` in
  `plugins/claude-code/.claude-plugin/plugin.json`, published by moving the
  marketplace pin. Claude Code caches a plugin by its declared version
  ([release process](docs/contributing/release.md#2-version-and-compatibility)),
  so a pin that moves under an unchanged version reaches new installs only and
  leaves every existing install on the cached copy. `compatibility.yaml`'s
  `pluginVersion` moves with it; `release.md` holds that procedure. Nothing runs
  on a `plugin-v*` tag today, so that train is hand-driven end to end.

**Delivered means fetchable, not arrived.** Both trains end at something the user
does — an install command, `/plugin update` — and this repository documents no
push channel. The trigger is our act, not their upgrade; the reason private is
the default here is that a release leaves installs a reader cannot fix by
updating a checkout.

An advisory published here carries no package ecosystem, so it reaches people who
read this repository and no dependency scanner. It is a public record, not a
notification.

A gap in the process around a release — a train with no CI, a marketplace
repository without required review, an unverified download in a workflow — has no
affected version an advisory can name, so its record is a public issue rather
than an advisory. **That is a statement about the mechanism, not a second routing
rule**: routing is still by whose users are exposed. Where such a gap has already
delivered something to those users, the fix takes the private fork above and the
public record follows it.

Yanking a broken release is
[`release.md`'s procedure](docs/contributing/release.md#yanking), and this file is
what its advisory step refers back to. Its "yank from PyPI" and "untag" steps are
Core's alone: the plugin has neither, and untagging cancels nothing that a sha
pin distributes.

### A vulnerability in Core while it is pre-release

No released version of Core exists for a GitHub advisory to name. That settles
only that an advisory is not the mechanism; it does not make public handling
automatic. A defect is fixed on a public branch — and, where it is not fixed at
once, tracked as a public issue or described in the
[threat model](docs/security/threat-model.md), this project's most detailed
public security channel — only when **both** of these hold:

- **It exposes no credential, and no content held behind a boundary this product
  enforces.** Four things are out, each taken from where the product enumerates
  it rather than listed here from memory:

  - the MCP token, and anything weakening the 0600 file inside a 0700 directory
    that protects it (SEC-4);
  - knowledge read without presenting the token by any actor the
    [threat model's actor table](docs/security/threat-model.md) marks untrusted —
    another local process, a visited web page, a repository contributor, an
    external system, an agent reasoning over untrusted input (T-1, T-2);
  - content read by an authorized caller from a project, tenant, sensitivity
    level, ACL group or namespace it is not authorized for — the five components
    of tree identity, filtered before ranking (T-11, SEC-13, FR-R1);
  - content withheld by approval state, supersession or retirement, read
    directly or **recovered** from a published value that moves with it (T-17).

  What decides this is exposure or weakened protection, not whether a credential
  appears in the story: a defect that leaves a token where it belongs, still
  0600, has not exposed it. Only the residual of the first — an account already
  compromised — is the row this file puts out of scope further down.
- **A public description hands nobody reach they did not already have.** Measured
  from an attacker holding exactly what the defect requires, and the possible
  holdings are that same actor table plus one it does not list: **nothing at
  all.** That last is the case this condition exists for. `theurian doctor
  --report` output is what people paste into public issues, so describing a
  defect in that payload publishes a way to collect from strangers, and searching
  public issues needs no access. A defect an attacker can exercise only against a
  machine, a token or a session they already hold gives them no such reach.

**Recovered is the load-bearing word above, and it is measured rather than
argued.** T-17 recovered a sixteen-character credential in 203 calls — an
arbitrary secret, not a yes-or-no about a known one — and is Critical and
private. T-17a moves published values too, but what it yields is bounded to a
vocabulary the caller already has: it can confirm that a withheld document holds
a term they have seen elsewhere, and cannot produce one they do not have. That is
why it is described further down this file and measured in the threat model
rather than withheld. **A new member of this family is private until somebody has
measured which side of that line it falls on**, because the measurement is the
argument.

Both conditions ask about disclosure, so an integrity or availability defect
clears them whatever its severity — a corrupted store and an unbounded query are
public at HIGH. That is deliberate: withholding a defect that exposes nothing
protects nobody, and the description is what lets a user judge their own
exposure.

Anything else — and anything where the answer is unclear — takes the same private
fork, and is described in public once the fix is on `theurian/theurian@main`,
which is what an install from source gets.

None of this rests on nobody running the code. Theurian installs from source
before any tag, and the threat model reasons about what a user has on that basis.

### The decision this records

[#51](https://github.com/theurian/theurian/pull/51) fixed a `theurian doctor
--report` that could publish a bearer token it had read out of a client
configuration, while this file said no credential value entered that payload. No
advisory was filed, because Core has no released version an advisory could name.
That was a decision, and this section exists so that it reads as one.

**It would clear neither condition above** — and its own pull request sat public
for the two and three quarter hours before the fix merged, carrying a
reproduction. A source install from before `a5f1f18` still has the defect and
should update past it. The conditions are written down because judgement in the
moment produced that.

## Supported versions

Pre-1.0, only an artifact's latest MINOR release receives security fixes. Once
1.0 ships, this table will list a supported window.

| Artifact | Version | Supported |
| :-- | :-- | :-- |
| Claude Code plugin | 0.1.x | ✅ |
| Claude Code plugin | < 0.1 | ❌ |
| Theurian Core | none released | — |

Core has no supported release until its first `core-v*` tag. The version the form
above asks for, `theurian version --json`, reports `0.1.0.dev0` from a source
install — that is a checkout, not a release.

## Theurian's security model

### What Theurian protects

- **The local endpoint.** The daemon binds `127.0.0.1` only, validates `Origin`
  and `Host` against DNS rebinding on every request the MCP app serves, and
  requires a bearer token with ≥256 bits of entropy on all of them. `GET /health`
  is the sole endpoint outside both, and it is outside both rather than only
  outside the token: it answers a cross-origin request with `{status, version,
  protocolVersion, dataDir, startedAt}`. That is nothing about projects or
  knowledge, and it is not nothing — `dataDir` is `~/.theurian`, so it names the
  OS user. See T-2 in the threat model.
- **The token.** Stored in a 0600 file inside a 0700 directory, and in the OS
  secret store where one is available. A world-readable token file is refused,
  not used. The token appears in no config file, no response body, and not in the
  daemon's log — the last asserted against a real daemon, and true because
  nothing in the stack logs request headers rather than because access logging is
  off. Redaction at a logging sink is the design ADR-0011 records and it is **not
  implemented**; there is no sink yet to apply it at.
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
- **Search ranking, while the retrieval index is out of date.** A document you
  retire or supersede is withheld from results immediately, but it stays in the
  index file until the next `theurian index build` — and BM25 scores every result
  against statistics computed over that whole file. So a withheld document can
  change the relative order of two documents you *can* see, and which paragraph
  of one of them is excerpted.

  **Any withheld content can do that, whatever it says.** One of those statistics
  is the average document length, so a withheld document that shares not one word
  with your query still changes the score of every visible row — by a different
  amount for each, which is why the order moves. This was measured against SQLite
  FTS5 rather than reasoned about; an earlier version of this section claimed the
  opposite.

  Reading content back out of the ranking is narrower. That needs a term which
  also occurs in content you *can* read, so what it can answer is whether a
  withheld document contains a term you have already seen somewhere — not a term
  you do not already have.

  `theurian index build` closes both, along with every other consequence of a
  stale index. It is accepted for now rather than fixed for now — the reasoning,
  the measurements and the scheduled fix are T-17a in
  [the threat model](docs/security/threat-model.md). **If a project's ranking
  order must not depend on retired content at all, rebuild the index as part of
  retiring it rather than on a schedule.**

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

`theurian doctor --report --json` redacts by default rather than on request,
because its output is what people paste into public issues. Two different
mechanisms do that, and they cover different things.

**Paths Theurian itself put in the payload are substituted**, wherever they
appear: your home directory becomes `~`, the repository root `<repository>`, the
token file `<token file>`, the `theurian` executable `<executable>`, and a data
directory you chose yourself `<data directory>` — only the default
`~/.theurian` is left legible, because `~` is already anonymous. Those name your
account and your repositories, which is someone's private information even though
none of them is a credential.

**Values Theurian did not write are withheld rather than substituted.** A
diagnostic reports on configuration somebody else owns — Claude Code's MCP entry,
a LaunchAgent plist or systemd unit, another daemon's reply, the project
registry — and substitution cannot reach any of it, because a string the local
process never held has nothing to match against. So under `--report` those steps
say what differs without saying what it holds: a count of registry entries that
cannot be read, `<another data directory>` for a daemon serving somewhere else,
an exception's type without its message, and — for a configuration that differs —
**only the names of the fields Theurian itself writes**, with anything else
reported as a count.

That last rule is narrower than "names, not values", and deliberately.
A field *name* is only Theurian's to publish if Theurian defined it: a name read
out of your `~/.claude.json`, plist or unit file is whatever string you put in
key position, and one of them was a bearer token on a continuation line. So the
published vocabulary is fixed and already public in this repository.

Plain `theurian doctor`, read by the person who ran it, still prints all of it in
full — that is where the values belong, and where the remedy needs them.

This is not a general credential filter, and knowing what it does not cover is
part of using it:

- No knowledge body enters the payload for it to remove.
- Two credentials could have reached it, both from configuration Theurian reads
  and never writes: an `Authorization` header holding a literal token instead of
  `${THEURIAN_MCP_TOKEN}`, and a token pasted into a service unit's environment.
  Both are now withheld under `--report`. Theurian never creates either state;
  it is what it finds when someone else has.
- Still published, and **this list is not exhaustive**: a path outside the
  anchors above, a revealing filename, the mode of your data directory, how many
  migrations your repository has, whether Serena is configured, and Theurian's
  own field names. Every one of those is a fact about your machine that the
  diagnostic exists to report.

**Review its output before posting it anywhere public.** Redaction narrows what
needs that review; it does not replace it.

## Dependencies

Every dependency is pinned exactly, `uv.lock` is committed, and CI runs
`uv sync --frozen`. Dependabot proposes upgrades; CI decides; a human merges.
Each release carries a CycloneDX SBOM and SHA-256 checksums.

## Further reading

- [Threat model](docs/security/threat-model.md) — trust boundaries and eighteen enumerated threats (T-1..T-17, plus T-17a)
- [Local MCP security](docs/security/local-mcp.md)
- [ADR-0011: local MCP authentication](docs/adr/0011-local-mcp-authentication.md)
- [ADR-0013: AI writes produce proposals](docs/adr/0013-ai-writes-produce-proposals.md)
