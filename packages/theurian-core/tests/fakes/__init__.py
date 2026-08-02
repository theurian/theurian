"""Deterministic port fakes (ADR-0003, OSS-15).

Every port ships a fake so the suite runs offline, for free, on any machine. A
fake that drifts from its port is worse than none, so each implements the same
Protocol and a conformance test asserts it.
"""

from fakes.clock import FrozenClock
from fakes.ids import SeededIdGenerator
from fakes.setup import FakeMcpConfig, FakeService
from fakes.store import InMemoryWriter

__all__ = [
    "FakeMcpConfig",
    "FakeService",
    "FrozenClock",
    "InMemoryWriter",
    "SeededIdGenerator",
]
