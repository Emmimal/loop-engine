"""
graph.py

Defines the environment the loop operates over: a directed acyclic graph
of tasks with dependencies, resource requirements, and validation rules.

The graph itself is intentionally dumb. It stores state and exposes
queries (is this task ready? what does it need?). All decision-making
lives in controller.py. This separation is the point: the graph is the
"world model," the controller is the "agent."
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Callable, Optional


class Status(Enum):
    PENDING = auto()         # not yet attempted
    NEEDS_RESOURCE = auto()  # blocked on a missing resource, retrieve() may help
    NEEDS_INPUT = auto()     # blocked on ambiguity, ask() may help
    READY = auto()           # dependencies satisfied, can attempt act()
    DONE = auto()            # completed and validated
    FAILED = auto()          # exhausted retries, permanently failed
    DEADLOCKED = auto()      # no forward progress possible (unresolvable block)


@dataclass
class Task:
    task_id: str
    depends_on: tuple[str, ...] = ()
    requires_resource: Optional[str] = None       # resource key needed before act()
    requires_decision: Optional[str] = None        # decision key needed before act()
    action: Optional[Callable[["Task", dict], bool]] = None  # returns True if valid
    max_retries: int = 2

    status: Status = Status.PENDING
    attempts: int = 0
    history: list[str] = field(default_factory=list)

    def log(self, event: str) -> None:
        self.history.append(event)


class TaskGraph:
    def __init__(self, tasks: list[Task]):
        self.tasks: dict[str, Task] = {t.task_id: t for t in tasks}
        self._validate_dag()

    def _validate_dag(self) -> None:
        # simple cycle check via DFS
        visiting, visited = set(), set()

        def dfs(tid: str) -> None:
            if tid in visited:
                return
            if tid in visiting:
                raise ValueError(f"Cycle detected involving task {tid!r}")
            visiting.add(tid)
            for dep in self.tasks[tid].depends_on:
                if dep not in self.tasks:
                    raise ValueError(f"Task {tid!r} depends on unknown task {dep!r}")
                dfs(dep)
            visiting.remove(tid)
            visited.add(tid)

        for tid in self.tasks:
            dfs(tid)

    def dependencies_satisfied(self, task: Task) -> bool:
        return all(self.tasks[d].status == Status.DONE for d in task.depends_on)

    def blocked_dependency(self, task: Task) -> Optional[str]:
        """Return the id of a dependency that is permanently stuck, if any."""
        for d in task.depends_on:
            dep = self.tasks[d]
            if dep.status in (Status.FAILED, Status.DEADLOCKED):
                return d
        return None

    def counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for t in self.tasks.values():
            out[t.status.name] = out.get(t.status.name, 0) + 1
        return out

    def is_terminal(self) -> bool:
        return all(
            t.status in (Status.DONE, Status.FAILED, Status.DEADLOCKED)
            for t in self.tasks.values()
        )
