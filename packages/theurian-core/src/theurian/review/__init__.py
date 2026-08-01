"""Review knowledge: ingestion, classification, candidate generation.

Review history is evidence; approved knowledge is a generalisation of it. The
step between them is a human judgement, and there is no code path that skips it
(ADR-0013).

The promotion gate decides whether a thread deserves a human's attention, using
seven observed facts. It never decides whether the generalisation is true.
"""
