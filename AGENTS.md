# Theurian — repository agent rules

## Product truth
Public claims must be grounded in current implementation, tests, approved ADRs, schemas,
security documentation, and released behavior. Never present roadmap work as shipped.

## AI contribution policy
AI-assisted changes are welcome, but they are proposals. Humans approve merges and releases.

## Public-content rules
- Never fabricate customers, adoption, benchmarks, testimonials, certifications, or audits.
- Security/performance/compatibility claims require evidence.
- Competitor comparisons must use current primary sources and comparable scope.
- Prefer concrete engineering examples over generic AI claims.
- Keep core engineering decisions authoritative; never rewrite ADR conclusions for marketing.

## Ownership
Core implementation is owned by the core engineering workflow.
Marketing/documentation agents may propose changes to README, website, demos, and public docs,
but must not modify product behavior merely to make a marketing claim true.

## Validation
Run the relevant repository tests/build/lint checks and `git diff --check`.
