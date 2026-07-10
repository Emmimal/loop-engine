"""
baseline.py

The comparison point for the article's benchmark. This is what most
one-shot / linear pipelines actually do: walk tasks in a fixed
topological order, attempt each one exactly once, and halt entirely
the first time something isn't immediately resolvable.

No retry, no re-routing around a blocked branch, no partial credit for
independent work that could still proceed. This is deliberately the
"static, front-loaded" approach the article's thesis argues against —
not a strawman, but the honest default behavior of code that doesn't
have a control loop.

One concession, and it's worth being explicit about it rather than
letting a reader catch it: tasks run in a valid topological order, not
insertion order. This is NOT the baseline being "smart" — it's the
minimum required for the comparison to mean anything. Without it, the
baseline would fail on task one of nearly any graph purely because its
dependency happened to be listed later, which would test list-ordering
luck rather than anything about linear execution. Topological order is
the floor a linear executor needs just to attempt the graph correctly;
everything past that (retry, re-routing, partial credit) is withheld
on purpose.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .graph import Status, Task, TaskGraph
from .resources import DecisionFixture, ResourceStore


@dataclass
class BaselineResult:
    completed: int
    halted_at: str | None
    reason: str | None
    final_counts: dict[str, int]
    trace: list[str] = field(default_factory=list)


def _topological_order(graph: TaskGraph) -> list[Task]:
    order: list[Task] = []
    visited: set[str] = set()

    def visit(tid: str) -> None:
        if tid in visited:
            return
        visited.add(tid)
        for dep in graph.tasks[tid].depends_on:
            visit(dep)
        order.append(graph.tasks[tid])

    for tid in graph.tasks:
        visit(tid)
    return order


def run_linear(
    graph: TaskGraph,
    resources: ResourceStore,
    decisions: DecisionFixture,
    context: dict | None = None,
) -> BaselineResult:
    context = context if context is not None else {}
    trace: list[str] = []
    completed = 0

    for task in _topological_order(graph):
        if task.requires_resource is not None:
            rstatus, value = resources.retrieve(task.requires_resource)
            # The linear executor gets exactly one attempt, full stop - it
            # doesn't get to distinguish "pending" from "missing" because
            # it never asks twice. Either way, one miss halts the pipeline.
            if rstatus != "resolved":
                trace.append(f"[{task.task_id}] HALT: resource not available on first attempt")
                task.status = Status.FAILED
                return BaselineResult(
                    completed, task.task_id, "resource not available",
                    graph.counts(), trace,
                )
            context[task.requires_resource] = value

        if task.requires_decision is not None:
            dstatus, value = decisions.ask(task.requires_decision)
            if dstatus != "resolved":
                trace.append(f"[{task.task_id}] HALT: unresolved decision")
                task.status = Status.FAILED
                return BaselineResult(
                    completed, task.task_id, "unresolved decision",
                    graph.counts(), trace,
                )
            context[task.requires_decision] = value

        valid = task.action(task, context) if task.action else True
        if not valid:
            trace.append(f"[{task.task_id}] HALT: validation failed (no retry)")
            task.status = Status.FAILED
            return BaselineResult(
                completed, task.task_id, "validation failed",
                graph.counts(), trace,
            )

        task.status = Status.DONE
        completed += 1
        trace.append(f"[{task.task_id}] done")

    return BaselineResult(completed, None, None, graph.counts(), trace)
