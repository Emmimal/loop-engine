"""
benchmark.py

Runs the loop controller and the linear baseline against the same set of
seeded scenarios, and reports the honest comparison: not "the loop solves
everything" (it doesn't - permanently missing resources and unanswerable
decisions stay unresolved in both), but "the loop keeps making progress
everywhere a linear pipeline would have already stopped."

The headline metric is INDEPENDENT BRANCHES PRESERVED, not raw completion
percentage - see sanity_check.py for why that framing is more defensible.

Run: python3 benchmark.py
"""

from __future__ import annotations

from loop_engineering.controller import LoopController
from loop_engineering.baseline import run_linear
from loop_engineering.scenarios import build_scenario


def print_progress_table(snapshots) -> None:
    header = f"{'Iter':>4} | {'Done':>5} | {'Blocked':>7} | {'Waiting':>7} | {'Retrieved':>9} | {'Asked':>5} | {'Revised':>7}"
    print(header)
    print("-" * len(header))
    for s in snapshots:
        print(
            f"{s.iteration:>4} | {s.completed_total:>5} | {s.blocked:>7} | "
            f"{s.waiting:>7} | {s.retrieved_this_iter:>9} | {s.asked_this_iter:>5} | "
            f"{s.revised_this_iter:>7}"
        )


def branches_fully_completed(graph, branch_task_ids: list[list[str]]) -> int:
    count = 0
    for branch in branch_task_ids:
        if all(graph.tasks[tid].status.name == "DONE" for tid in branch):
            count += 1
    return count


def run_one_config(n_branches: int, tasks_per_branch: int, seed: int) -> dict:
    graph_for_loop, res1, dec1, meta1 = build_scenario(n_branches, tasks_per_branch, seed)
    graph_for_linear, res2, dec2, meta2 = build_scenario(n_branches, tasks_per_branch, seed)

    total_tasks = len(graph_for_loop.tasks)

    loop_result = LoopController(graph_for_loop, res1, dec1, max_iterations=50).run()
    linear_result = run_linear(graph_for_linear, res2, dec2)

    loop_branches_done = branches_fully_completed(graph_for_loop, meta1.branch_task_ids)
    linear_branches_done = branches_fully_completed(graph_for_linear, meta2.branch_task_ids)

    return {
        "n_branches": n_branches,
        "tasks_per_branch": tasks_per_branch,
        "seed": seed,
        "total_tasks": total_tasks,
        "loop_completed": loop_result.completed,
        "loop_iterations": loop_result.iterations,
        "loop_progress_efficiency": round(loop_result.progress_efficiency, 3),
        "loop_recoveries": loop_result.total_recoveries,
        "loop_deadlock_detected_at": loop_result.deadlock_detected_at(),
        "loop_branches_done": loop_branches_done,
        "linear_completed": linear_result.completed,
        "linear_halted_at": linear_result.halted_at,
        "linear_halt_reason": linear_result.reason,
        "linear_branches_done": linear_branches_done,
        "total_branches": n_branches,
        "snapshots": loop_result.snapshots,
    }


def main() -> None:
    configs = [
        (6, 3, 0), (6, 3, 1), (6, 3, 2),
        (10, 4, 0), (10, 4, 1), (10, 4, 2),
        (15, 3, 0), (15, 3, 1), (15, 3, 2),
    ]

    print("=" * 100)
    print("LOOP CONTROLLER vs LINEAR BASELINE - honest comparison across 9 seeded configs")
    print("=" * 100)

    rows = []
    for n_branches, tasks_per_branch, seed in configs:
        r = run_one_config(n_branches, tasks_per_branch, seed)
        rows.append(r)
        pct_loop = 100 * r["loop_completed"] / r["total_tasks"]
        pct_linear = 100 * r["linear_completed"] / r["total_tasks"]
        print(
            f"branches={n_branches:>2} depth={tasks_per_branch} seed={seed} | "
            f"total={r['total_tasks']:>3} tasks | "
            f"controller: {r['loop_completed']:>3}/{r['total_tasks']:<3} ({pct_loop:5.1f}%) tasks, "
            f"{r['loop_branches_done']}/{r['total_branches']} branches fully done | "
            f"linear: {r['linear_completed']:>3}/{r['total_tasks']:<3} ({pct_linear:5.1f}%) tasks, "
            f"{r['linear_branches_done']}/{r['total_branches']} branches fully done "
            f"(halted at {r['linear_halted_at']})"
        )

    print()
    print("=" * 100)
    print("Detailed per-iteration progress table for ONE representative config (branches=10, depth=4, seed=0)")
    print("=" * 100)
    detailed = run_one_config(10, 4, 0)
    print_progress_table(detailed["snapshots"])

    print()
    print("=" * 100)
    print("SUMMARY")
    print("=" * 100)
    avg_loop_pct = sum(100 * r["loop_completed"] / r["total_tasks"] for r in rows) / len(rows)
    avg_linear_pct = sum(100 * r["linear_completed"] / r["total_tasks"] for r in rows) / len(rows)
    avg_loop_branches = sum(r["loop_branches_done"] for r in rows) / len(rows)
    avg_linear_branches = sum(r["linear_branches_done"] for r in rows) / len(rows)
    avg_total_branches = sum(r["total_branches"] for r in rows) / len(rows)

    print(f"Average branches fully completed, goal-directed controller: {avg_loop_branches:.1f} / {avg_total_branches:.1f}")
    print(f"Average branches fully completed, linear baseline:           {avg_linear_branches:.1f} / {avg_total_branches:.1f}")
    print()
    print(f"Average task completion, goal-directed controller: {avg_loop_pct:.1f}%")
    print(f"Average task completion, linear baseline:           {avg_linear_pct:.1f}%")
    print()
    print("The architectural claim: the controller wasn't better because it solved")
    print("impossible tasks. It was better because it refused to let one impossible")
    print("task stop every other possible task. Permanently missing resources and")
    print("unanswerable decisions stay unresolved in both systems, by design - what")
    print("differs is whether one blocker takes the rest of the graph down with it.")


if __name__ == "__main__":
    main()
