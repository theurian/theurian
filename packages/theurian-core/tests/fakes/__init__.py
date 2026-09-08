"""Deterministic port fakes (ADR-0003, OSS-15).

Every port ships a fake so the suite runs offline, for free, on any machine. A
fake that drifts from its port is worse than none, so each implements the same
Protocol and a conformance test asserts it.
"""

from fakes.clock import FrozenClock
from fakes.finding_source import FakeReviewFindingSource
from fakes.ids import SeededIdGenerator
from fakes.pages import truncating, whole
from fakes.review_provider import CannedReviewProvider, ReadKey
from fakes.setup import FakeMcpConfig, FakeService
from fakes.store import InMemoryWriter

__all__ = [
    "CannedReviewProvider",
    "FakeMcpConfig",
    "FakeReviewFindingSource",
    "FakeService",
    "FrozenClock",
    "InMemoryWriter",
    "ReadKey",
    "SeededIdGenerator",
    "truncating",
    "whole",
]
