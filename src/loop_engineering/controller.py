r"""
controller.py

This is the article's actual subject. Everything else in this package
(graph.py, resources.py) is scaffolding to give this loop something real
to act on.

Each task, each iteration, moves through this state machine:

    OBSERVE
       |
    READY? --yes--> EXECUTE --pass--> DONE
       |no                  \--fail--> REVISE --budget left--> (retry)
       |                                   \--exhausted--> FAILED
       WHY?
       |-- missing resource --> RETRIEVE --resolved--> (recheck)
       |-- ambiguity         --> ASK      --resolved--> (recheck)
       |-- dependency pending--> WAIT     (no-op this pass)
       |-- unresolvable      --> BLOCK    --> DEADLOCKED

It is deliberately NOT a fixed sequence of steps. Each task independently
lands in whichever branch its current state calls for, every iteration,
until the whole graph reaches a terminal state (all tasks DONE, FAILED,
or DEADLOCKED) or a max-iteration safety budget is hit.

Where a real reasoning engine would plug in: the `act()` step below calls
`task.action(task, context)`, a plain deterministic function in this
implementation. Swap that call for an LLM invocation (with the same
signature: take task + context, return success/fail) and every other
line in this file is unchanged. The loop does not know or care whether
the thing making the decision is a rule, a heuristic, or a model — that
is the separation this whole package exists to demonstrate.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .graph import Status, Task, TaskGraph
from .resources import DecisionFixture, ResourceStore


@dataclass
class IterationSnapshot:
    iteration: int
    ready_or_done_this_iter: int
    completed_total: int
    blocked: int
    waiting: int
    retrieved_this_iter: int
    asked_this_iter: int
    revised_this_iter: int


@dataclass
class LoopResult:
    iterations: int
    final_counts: dict[str, int]
    trace: list[str] = field(default_factory=list)
    snapshots: list[IterationSnapshot] = field(default_factory=list)
    total_recoveries: int = 0  # resource/decision blocks successfully resolved
    skipped_work_avoided: int = 0  # tasks completed AFTER an unrelated deadlock existed

    @property
    def completed(self) -> int:
        return self.final_counts.get("DONE", 0)

    @property
    def progress_efficiency(self) -> float:
        """Completed tasks per iteration spent. Higher is better."""
        return self.completed / self.iterations if self.iterations else 0.0

    def deadlock_detected_at(self) -> int | None:
        """First iteration at which a permanent deadlock existed."""
        for s in self.snapshots:
            if s.blocked > 0:
                return s.iteration
        return None


class LoopController:
    def __init__(
        self,
        graph: TaskGraph,
        resources: ResourceStore,
        decisions: DecisionFixture,
        max_iterations: int = 100,
    ):
        self.graph = graph
        self.resources = resources
        self.decisions = decisions
        self.max_iterations = max_iterations

    def run(self, context: dict | None = None) -> LoopResult:
        context = context if context is not None else {}
        trace: list[str] = []
        snapshots: list[IterationSnapshot] = []
        iteration = 0
        total_recoveries = 0
        first_deadlock_completed = None
        skipped_work_avoided = 0

        while not self.graph.is_terminal() and iteration < self.max_iterations:
            iteration += 1
            retrieved = asked = revised = 0

            for task in self.graph.tasks.values():
                if task.status in (Status.DONE, Status.FAILED, Status.DEADLOCKED):
                    continue

                outcome = self._step(task, context, trace)
                if outcome == "retrieved":
                    retrieved += 1
                elif outcome == "asked":
                    asked += 1
                elif outcome == "revised":
                    revised += 1

            total_recoveries += retrieved + asked
            counts = self.graph.counts()
            blocked = counts.get("DEADLOCKED", 0)
            waiting = (
                counts.get("PENDING", 0)
                + counts.get("NEEDS_RESOURCE", 0)
                + counts.get("NEEDS_INPUT", 0)
            )

            if blocked > 0 and first_deadlock_completed is None:
                first_deadlock_completed = counts.get("DONE", 0)
            if first_deadlock_completed is not None:
                skipped_work_avoided = counts.get("DONE", 0) - first_deadlock_completed

            snapshots.append(IterationSnapshot(
                iteration=iteration,
                ready_or_done_this_iter=counts.get("DONE", 0),
                completed_total=counts.get("DONE", 0),
                blocked=blocked,
                waiting=waiting,
                retrieved_this_iter=retrieved,
                asked_this_iter=asked,
                revised_this_iter=revised,
            ))

        if not self.graph.is_terminal():
            # Budget exhausted without every task reaching a terminal state.
            # This is deliberately NOT relabeled as DEADLOCKED: those tasks
            # were still making legitimate progress (e.g. a resource still
            # polling toward availability) when the clock ran out. Calling
            # that "deadlocked" would misrepresent a slow convergence as a
            # proven permanent block, which is exactly the kind of
            # unsubstantiated claim this whole exercise is trying to avoid.
            for task in self.graph.tasks.values():
                if task.status not in (Status.DONE, Status.FAILED, Status.DEADLOCKED):
                    task.log("budget exhausted before this task could resolve")
                    trace.append(f"[{task.task_id}] BUDGET EXHAUSTED (still {task.status.name})")

        return LoopResult(
            iterations=iteration,
            final_counts=self.graph.counts(),
            trace=trace,
            snapshots=snapshots,
            total_recoveries=total_recoveries,
            skipped_work_avoided=max(skipped_work_avoided, 0),
        )

    def _step(self, task: Task, context: dict, trace: list[str]) -> str | None:
        # observe: has a dependency permanently failed upstream?
        blocked_dep = self.graph.blocked_dependency(task)
        if blocked_dep is not None:
            task.status = Status.DEADLOCKED
            task.log(f"deadlocked: dependency {blocked_dep!r} never resolved")
            trace.append(f"[{task.task_id}] DEADLOCKED (blocked by {blocked_dep})")
            return None

        if not self.graph.dependencies_satisfied(task):
            task.status = Status.PENDING
            return None  # waiting on upstream, nothing to do yet

        # retrieve: does this task need a resource it doesn't have?
        if task.requires_resource is not None:
            rstatus, value = self.resources.retrieve(task.requires_resource)
            if rstatus == "resolved":
                context[task.requires_resource] = value
                task.log(f"retrieved resource {task.requires_resource!r}")
                task.requires_resource = None  # satisfied, don't re-check
                return "retrieved"
            elif rstatus == "pending":
                task.status = Status.NEEDS_RESOURCE
                trace.append(f"[{task.task_id}] waiting on resource (still resolving)")
                return None
            else:  # "missing" - proven, not guessed
                task.status = Status.DEADLOCKED
                task.log(f"deadlocked: resource {task.requires_resource!r} does not exist")
                trace.append(f"[{task.task_id}] DEADLOCKED (resource permanently missing)")
                return None

        # ask: does this task need a decision it doesn't have?
        if task.requires_decision is not None:
            dstatus, value = self.decisions.ask(task.requires_decision)
            if dstatus == "resolved":
                context[task.requires_decision] = value
                task.log(f"resolved decision {task.requires_decision!r}")
                task.requires_decision = None
                return "asked"
            else:  # "missing" - proven unanswerable
                task.status = Status.DEADLOCKED
                task.log("deadlocked: decision has no answer available")
                trace.append(f"[{task.task_id}] DEADLOCKED (decision unanswerable)")
                return None

        # act: attempt the task's deterministic action
        task.status = Status.READY
        task.attempts += 1
        try:
            valid = task.action(task, context) if task.action else True
        except Exception as exc:  # noqa: BLE001 - validation failures are data, not crashes
            valid = False
            task.log(f"action raised: {exc!r}")

        if valid:
            task.status = Status.DONE
            task.log(f"done on attempt {task.attempts}")
            trace.append(f"[{task.task_id}] DONE (attempt {task.attempts})")
            return None

        # revise: retry if budget remains, else permanently fail
        if task.attempts <= task.max_retries:
            task.status = Status.PENDING
            task.log(f"validation failed on attempt {task.attempts}, retrying")
            trace.append(f"[{task.task_id}] revise: retry {task.attempts}/{task.max_retries}")
            return "revised"
        else:
            task.status = Status.FAILED
            task.log(f"failed permanently after {task.attempts} attempts")
            trace.append(f"[{task.task_id}] FAILED after {task.attempts} attempts")
            return None


