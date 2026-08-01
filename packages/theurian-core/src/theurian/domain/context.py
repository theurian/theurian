"""Per-request context.

There is no process-global ``currentProject`` and no connection-scoped state.
Every call carries its own context, which is what makes cross-project isolation
testable rather than aspirational (ADR-0002, SEC-13).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import override

from theurian.domain.identifiers import AgentId, ProjectId, TaskId
from theurian.domain.values import ContentHash


@dataclass(frozen=True, slots=True)
class SnapshotId:
    """A pinned canonical state, identified by its state hash (ADR-0007).

    Pinning one for the duration of a task means the knowledge base cannot shift
    under an agent mid-run, even if the developer switches branches.
    """

    state_hash: ContentHash

    @classmethod
    def parse(cls, value: str) -> SnapshotId:
        return cls(ContentHash(value))

    @override
    def __str__(self) -> str:
        return self.state_hash.value


@dataclass(frozen=True, slots=True)
class RequestContext:
    """The explicit context accompanying every project-scoped operation.

    ``project_id`` is required. There is deliberately no "last used project"
    fallback: with many agents sharing one daemon, an implicit default resolves
    one agent's query against another agent's project.
    """

    project_id: ProjectId
    snapshot_id: SnapshotId | None = None
    agent_id: AgentId | None = None
    task_id: TaskId | None = None

    @property
    def is_pinned(self) -> bool:
        return self.snapshot_id is not None

    def redacted(self) -> dict[str, str | None]:
        """A logging-safe view. Carries no secrets, so it is safe as-is."""
        return {
            "projectId": self.project_id.value,
            "snapshotId": str(self.snapshot_id) if self.snapshot_id else None,
            "agentId": self.agent_id.value if self.agent_id else None,
            "taskId": self.task_id.value if self.task_id else None,
        }
