"""Traceability graph and drift detection.

Typed, evidenced edges along requirement -> spec -> ADR -> PR -> review -> code
-> test -> operational evidence.

Confidence separates a human assertion from a heuristic match, because drift
reporting has to distinguish 'we know' from 'we guessed' -- presenting a guess as
a fact is how a team stops trusting the tool.

Evaluates the per-change-type traceability policy. Unconfigured change types
default to permissive: a policy that blocks work it was not written for gets
disabled entirely, which is strictly worse than a partial one (FR-T5).
"""
