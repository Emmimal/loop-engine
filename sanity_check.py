"""
sanity_check.py

Validates the benchmark against its own ground truth before any number
from it gets frozen into the article. Answers five specific questions:

  1. Are permanent blockers injected at the intended rate?
  2. How many downstream tasks become unreachable because of those
     blockers (cascade), versus directly injected?
  3. Are retrieve/ask/revise events occurring in the expected proportions
     given the injected failure-mode counts?
  4. Does the controller ever mark a task DEADLOCKED that could have
     eventually completed with more iteration budget?
  5. Across many random seeds, is the completion percentage stable, or
     is the headline number a lucky/unlucky sample?

Run: python3 sanity_check.py
"""

from __future__ import annotations

import statistics

from loop_engineering.controller import LoopController
from loop_engineering.baseline import run_linear
from loop_engineering.scenarios import build_scenario


PASS = "PASS"
FAIL = "FAIL"
WARN = "WARN"


def check_injection_rates(n_seeds: int = 200) -> None:
    print("\n[1] Injection rate check")
    print("-" * 70)
    expected_frac = 1 / 8  # uniform choice over 8 modes
    totals: dict[str, int] = {}
    grand_total = 0

    for seed in range(n_seeds):
        _, _, _, meta = build_scenario(n_branches=8, tasks_per_branch=3, seed=seed)
        for mode, count in meta.mode_counts.items():
            totals[mode] = totals.get(mode, 0) + count
            grand_total += count

    print(f"Aggregated over {n_seeds} seeds, {grand_total} total task-mode assignments:")
    ok = True
    for mode in sorted(totals):
        observed_frac = totals[mode] / grand_total
        # "clean" has 3x the weight in the choice list by design
        expected = 3 * expected_frac if mode == "clean" else expected_frac
        deviation = abs(observed_frac - expected)
        status = PASS if deviation < 0.02 else WARN
        if status == WARN:
            ok = False
        print(f"  {mode:<22} observed={observed_frac:6.1%}  expected~={expected:6.1%}  [{status}]")

    permanent = totals.get("missing_resource", 0) + totals.get("unanswerable_decision", 0)
    print(f"  permanent-blocker modes combined: {permanent}/{grand_total} = {permanent/grand_total:.1%} "
          f"(expected ~25.0%)")
    print(f"Overall: {'PASS - injection rates match design' if ok else 'WARN - see deviations above'}")


def check_cascade_vs_direct(seed: int = 0) -> None:
    print("\n[2] Downstream cascade check")
    print("-" * 70)
    graph, res, dec, meta = build_scenario(n_branches=10, tasks_per_branch=4, seed=seed)
    LoopController(graph, res, dec, max_iterations=50).run()

    deadlocked = [t.task_id for t in graph.tasks.values() if t.status.name == "DEADLOCKED"]
    direct = set(meta.direct_permanent_blockers)
    cascade = [tid for tid in deadlocked if tid not in direct]

    print(f"Directly injected permanent blockers: {len(direct)} -> {sorted(direct)}")
    print(f"Total DEADLOCKED tasks after run:     {len(deadlocked)}")
    print(f"Of which cascade (downstream of a blocker, not injected themselves): {len(cascade)}")
    if cascade:
        print(f"  cascade tasks: {sorted(cascade)}")
    # sanity: every deadlocked task should be either directly injected,
    # or depend (transitively) on one that is
    unexplained = []
    for tid in cascade:
        task = graph.tasks[tid]
        # walk dependency chain to confirm it traces back to a direct blocker
        stack = list(task.depends_on)
        found_direct = False
        seen = set()
        while stack:
            dep = stack.pop()
            if dep in seen:
                continue
            seen.add(dep)
            if dep in direct:
                found_direct = True
                break
            stack.extend(graph.tasks[dep].depends_on)
        if not found_direct:
            unexplained.append(tid)

    status = PASS if not unexplained else FAIL
    print(f"Cascade tasks with no traceable direct blocker upstream: {len(unexplained)} [{status}]")
    if unexplained:
        print(f"  UNEXPLAINED: {unexplained} - this would indicate a controller bug")


def check_event_proportions(seed: int = 0) -> None:
    print("\n[3] Retrieve/ask/revise event proportion check")
    print("-" * 70)
    graph, res, dec, meta = build_scenario(n_branches=10, tasks_per_branch=4, seed=seed)
    result = LoopController(graph, res, dec, max_iterations=50).run()

    expected_retrievals = meta.mode_counts.get("slow_resource", 0)
    expected_asks = meta.mode_counts.get("answerable_decision", 0)
    expected_flaky = meta.mode_counts.get("flaky", 0)

    actual_retrievals = sum(s.retrieved_this_iter for s in result.snapshots)
    actual_asks = sum(s.asked_this_iter for s in result.snapshots)
    actual_revisions = sum(s.revised_this_iter for s in result.snapshots)

    # A mode-tagged task that never gets reached (because an earlier task
    # in the same branch deadlocked first) will show attempts == 0 and
    # never produced a 'retrieved resource' / 'resolved decision' history
    # entry. Verify that every shortfall is explained this way, rather
    # than assuming it - an unexplained shortfall would be a real bug.
    def unreached(task_ids: list[str], history_needle: str) -> list[str]:
        out = []
        for tid in task_ids:
            t = graph.tasks[tid]
            reached = any(history_needle in h for h in t.history) or t.status.name == "DONE"
            if not reached:
                out.append(tid)
        return out

    slow_resource_tasks = [tid for tid, m in meta.mode_by_task.items() if m == "slow_resource"]
    answerable_tasks = [tid for tid, m in meta.mode_by_task.items() if m == "answerable_decision"]
    flaky_tasks = [tid for tid, m in meta.mode_by_task.items() if m == "flaky"]

    unreached_retrievals = unreached(slow_resource_tasks, "retrieved resource")
    unreached_asks = unreached(answerable_tasks, "resolved decision")
    unreached_flaky = [tid for tid in flaky_tasks if graph.tasks[tid].attempts == 0]

    for label, expected, actual, unreached_list in [
        ("slow_resource -> retrieved", expected_retrievals, actual_retrievals, unreached_retrievals),
        ("answerable_decision -> asked", expected_asks, actual_asks, unreached_asks),
        ("flaky -> revised (>=1 each reached)", expected_flaky, actual_revisions, unreached_flaky),
    ]:
        shortfall = expected - actual if label.startswith("flaky") else expected - actual
        explained = shortfall == len(unreached_list) if label.startswith("flaky") else shortfall == len(unreached_list)
        status = PASS if (actual == expected or explained) else FAIL
        print(f"  {label}: injected={expected}, observed={actual}, "
              f"shortfall={expected - actual}, explained-by-cascade={len(unreached_list)} [{status}]")
        if unreached_list:
            print(f"    never reached (blocked upstream first): {unreached_list}")
        if status == FAIL:
            print(f"    UNEXPLAINED SHORTFALL - this would indicate a real controller bug")


def check_no_premature_deadlock(configs: list[tuple[int, int, int]]) -> None:
    print("\n[4] Premature-deadlock check (would more budget have helped?)")
    print("-" * 70)
    print("Re-running each config at max_iterations=50 vs max_iterations=2000.")
    print("If the set of DEADLOCKED tasks differs, the smaller budget was")
    print("mislabeling 'still working' as 'proven stuck' - exactly the bug")
    print("this benchmark fixed once already (see resources.py tri-state).\n")

    all_ok = True
    for n_branches, tasks_per_branch, seed in configs:
        g1, r1, d1, _ = build_scenario(n_branches, tasks_per_branch, seed)
        g2, r2, d2, _ = build_scenario(n_branches, tasks_per_branch, seed)

        LoopController(g1, r1, d1, max_iterations=50).run()
        LoopController(g2, r2, d2, max_iterations=2000).run()

        deadlocked_50 = {tid for tid, t in g1.tasks.items() if t.status.name == "DEADLOCKED"}
        deadlocked_2000 = {tid for tid, t in g2.tasks.items() if t.status.name == "DEADLOCKED"}
        done_50 = {tid for tid, t in g1.tasks.items() if t.status.name == "DONE"}
        done_2000 = {tid for tid, t in g2.tasks.items() if t.status.name == "DONE"}

        match = deadlocked_50 == deadlocked_2000 and done_50 == done_2000
        status = PASS if match else FAIL
        if not match:
            all_ok = False
        print(f"  branches={n_branches} depth={tasks_per_branch} seed={seed}: [{status}]")
        if not match:
            print(f"    DEADLOCKED differs: only-at-50={deadlocked_50 - deadlocked_2000} "
                  f"only-at-2000={deadlocked_2000 - deadlocked_50}")
            print(f"    DONE differs: only-at-50={done_50 - done_2000} only-at-2000={done_2000 - done_50}")

    print(f"\nOverall: {'PASS - no task was ever mislabeled as permanently stuck' if all_ok else 'FAIL - see above'}")


def check_stability_across_seeds(n_seeds: int = 300) -> None:
    print(f"\n[5] Stability across {n_seeds} random seeds")
    print("-" * 70)
    print("We ran many random seeds specifically to check whether the benchmark")
    print("depended on a favorable graph topology, rather than trusting a single run.")
    loop_pcts, linear_pcts = [], []

    for seed in range(n_seeds):
        graph_loop, res1, dec1, _ = build_scenario(n_branches=10, tasks_per_branch=4, seed=seed)
        graph_linear, res2, dec2, _ = build_scenario(n_branches=10, tasks_per_branch=4, seed=seed)
        total = len(graph_loop.tasks)

        loop_result = LoopController(graph_loop, res1, dec1, max_iterations=50).run()
        linear_result = run_linear(graph_linear, res2, dec2)

        loop_pcts.append(100 * loop_result.completed / total)
        linear_pcts.append(100 * linear_result.completed / total)

    print(f"Loop controller completion %:  mean={statistics.mean(loop_pcts):.1f}  "
          f"stdev={statistics.stdev(loop_pcts):.1f}  min={min(loop_pcts):.1f}  max={max(loop_pcts):.1f}")
    print(f"Linear baseline completion %:  mean={statistics.mean(linear_pcts):.1f}  "
          f"stdev={statistics.stdev(linear_pcts):.1f}  min={min(linear_pcts):.1f}  max={max(linear_pcts):.1f}")

    overlap = max(loop_pcts) >= min(linear_pcts) and min(loop_pcts) <= max(linear_pcts)
    # what matters more than "do the ranges overlap" is whether the loop's
    # WORST seed still beats the linear baseline's BEST seed
    loop_worst_beats_linear_best = min(loop_pcts) > max(linear_pcts)
    print(f"\nDo the two distributions overlap at all? {overlap}")
    print(f"Does the loop's worst seed still beat the linear baseline's best seed? "
          f"{loop_worst_beats_linear_best} "
          f"[{PASS if loop_worst_beats_linear_best else WARN}]")
    if not loop_worst_beats_linear_best:
        print("  This would mean the 9-config sample earlier could have been lucky.")
        print("  Report the mean +/- stdev in the article rather than a single sample.")


def main() -> None:
    print("=" * 70)
    print("BENCHMARK SANITY CHECK")
    print("=" * 70)
    check_injection_rates()
    check_cascade_vs_direct()
    check_event_proportions()
    check_no_premature_deadlock([
        (6, 3, 0), (6, 3, 1), (6, 3, 2),
        (10, 4, 0), (10, 4, 1), (10, 4, 2),
        (15, 3, 0), (15, 3, 1), (15, 3, 2),
    ])
    check_stability_across_seeds(n_seeds=300)


if __name__ == "__main__":
    main()
