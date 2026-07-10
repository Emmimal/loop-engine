"""
scenarios.py

Generates synthetic task graphs with a controlled, seeded mix of failure
modes, so the benchmark is reproducible and the mix of obstacles is
known in advance (rather than incidentally emerging from real data).

Every graph is built from independent branches feeding into shared
"integration" tasks, which is what makes the loop's advantage visible:
a branch-local obstacle should not have to stop unrelated branches.

Failure modes injected, by design:
  - clean:              no obstacles, always succeeds first try
  - flaky:               validation fails once, then succeeds (revise fixes it)
  - slow_resource:       resource resolves after N retrieval attempts
  - missing_resource:    resource never resolves (permanent block)
  - answerable_decision:  ambiguity resolved via fixture
  - unanswerable_decision: ambiguity has no fixture answer (permanent block)
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field

from .graph import Task, TaskGraph
from .resources import DecisionFixture, ResourceStore


def _always_valid(task: Task, context: dict) -> bool:
    return True


def _flaky_once(task: Task, context: dict) -> bool:
    # fails on the first attempt, succeeds on every attempt after
    return task.attempts > 1


@dataclass
class ScenarioMetadata:
    """Ground truth about what was injected, so sanity checks and the
    benchmark can be verified against something other than themselves."""
    mode_by_task: dict[str, str] = field(default_factory=dict)
    mode_counts: dict[str, int] = field(default_factory=dict)
    branch_task_ids: list[list[str]] = field(default_factory=list)  # per-branch, excludes integration tasks
    integration_task_ids: list[str] = field(default_factory=list)
    direct_permanent_blockers: list[str] = field(default_factory=list)  # tasks assigned a permanent-block mode


def build_scenario(
    n_branches: int = 8,
    tasks_per_branch: int = 3,
    seed: int = 0,
) -> tuple[TaskGraph, ResourceStore, DecisionFixture, ScenarioMetadata]:
    rng = random.Random(seed)
    tasks: list[Task] = []
    resources = ResourceStore()
    decisions = DecisionFixture()
    meta = ScenarioMetadata()

    branch_heads: list[str] = []

    failure_modes = [
        "clean", "clean", "clean",           # majority of tasks are unobstructed
        "flaky",
        "slow_resource",
        "missing_resource",
        "answerable_decision",
        "unanswerable_decision",
    ]
    permanent_modes = {"missing_resource", "unanswerable_decision"}

    for b in range(n_branches):
        prev_id: str | None = None
        branch_ids: list[str] = []
        for t in range(tasks_per_branch):
            tid = f"b{b}_t{t}"
            mode = rng.choice(failure_modes)
            depends = (prev_id,) if prev_id else ()

            requires_resource = None
            requires_decision = None
            action = _always_valid

            if mode == "flaky":
                action = _flaky_once
            elif mode == "slow_resource":
                key = f"res_{tid}"
                resources.eventually_available[key] = rng.randint(2, 3)
                requires_resource = key
            elif mode == "missing_resource":
                requires_resource = f"missing_res_{tid}"  # never registered -> permanent
            elif mode == "answerable_decision":
                key = f"dec_{tid}"
                decisions.answers[key] = "resolved"
                requires_decision = key
            elif mode == "unanswerable_decision":
                requires_decision = f"unanswerable_dec_{tid}"  # never registered -> permanent

            tasks.append(Task(
                task_id=tid,
                depends_on=depends,
                requires_resource=requires_resource,
                requires_decision=requires_decision,
                action=action,
            ))
            meta.mode_by_task[tid] = mode
            meta.mode_counts[mode] = meta.mode_counts.get(mode, 0) + 1
            if mode in permanent_modes:
                meta.direct_permanent_blockers.append(tid)
            branch_ids.append(tid)
            prev_id = tid
        branch_heads.append(prev_id)  # last task of each branch
        meta.branch_task_ids.append(branch_ids)

    # integration tasks that fan-in from a few branches each, to show that
    # one blocked branch shouldn't need to block integration of the others
    n_integrations = max(1, n_branches // 3)
    for i in range(n_integrations):
        deps = tuple(branch_heads[i * 3:(i + 1) * 3]) or (branch_heads[-1],)
        tid = f"integrate_{i}"
        tasks.append(Task(task_id=tid, depends_on=deps, action=_always_valid))
        meta.integration_task_ids.append(tid)

    return TaskGraph(tasks), resources, decisions, meta
