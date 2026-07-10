import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent / "src"))

from loop_engineering.graph import Task, TaskGraph, Status
from loop_engineering.resources import ResourceStore, DecisionFixture
from loop_engineering.controller import LoopController


def run(tasks, resources=None, decisions=None, max_iterations=50):
    g = TaskGraph(tasks)
    ctrl = LoopController(
        g,
        resources or ResourceStore(),
        decisions or DecisionFixture(),
        max_iterations=max_iterations,
    )
    return ctrl.run(), g


def test_simple_chain_completes():
    result, g = run([Task("a"), Task("b", depends_on=("a",))])
    assert result.completed == 2
    assert g.tasks["a"].status == Status.DONE
    assert g.tasks["b"].status == Status.DONE


def test_flaky_task_recovers_via_revise():
    def flaky(task, ctx):
        return task.attempts > 1

    result, g = run([Task("a", action=flaky, max_retries=2)])
    assert g.tasks["a"].status == Status.DONE
    assert g.tasks["a"].attempts == 2


def test_task_fails_after_exhausting_retries():
    def always_fail(task, ctx):
        return False

    result, g = run([Task("a", action=always_fail, max_retries=1)])
    assert g.tasks["a"].status == Status.FAILED
    assert g.tasks["a"].attempts == 2  # initial + 1 retry


def test_missing_resource_deadlocks_only_dependent_branch():
    resources = ResourceStore()  # 'ghost' never registered
    tasks = [
        Task("blocked", requires_resource="ghost"),
        Task("independent"),  # no relation to 'blocked'
    ]
    result, g = run(tasks, resources=resources)
    assert g.tasks["blocked"].status == Status.DEADLOCKED
    assert g.tasks["independent"].status == Status.DONE


def test_slow_resource_eventually_resolves():
    resources = ResourceStore(eventually_available={"res": 3})
    result, g = run([Task("a", requires_resource="res")], resources=resources)
    assert g.tasks["a"].status == Status.DONE
    assert result.total_recoveries >= 1


def test_unanswerable_decision_deadlocks():
    decisions = DecisionFixture()  # no answer registered
    result, g = run([Task("a", requires_decision="mystery")], decisions=decisions)
    assert g.tasks["a"].status == Status.DEADLOCKED


def test_downstream_task_deadlocks_when_dependency_deadlocked():
    resources = ResourceStore()
    tasks = [
        Task("upstream", requires_resource="ghost"),
        Task("downstream", depends_on=("upstream",)),
    ]
    result, g = run(tasks, resources=resources)
    assert g.tasks["upstream"].status == Status.DEADLOCKED
    assert g.tasks["downstream"].status == Status.DEADLOCKED


def test_independent_branches_both_complete_despite_one_blocker():
    resources = ResourceStore()
    tasks = [
        Task("branch_a_1", requires_resource="ghost"),
        Task("branch_a_2", depends_on=("branch_a_1",)),
        Task("branch_b_1"),
        Task("branch_b_2", depends_on=("branch_b_1",)),
    ]
    result, g = run(tasks, resources=resources)
    assert g.tasks["branch_b_1"].status == Status.DONE
    assert g.tasks["branch_b_2"].status == Status.DONE
    assert g.tasks["branch_a_1"].status == Status.DEADLOCKED
    assert result.completed == 2


def test_max_iterations_budget_is_respected():
    # a task that never resolves and never deadlocks would spin forever
    # without the no-progress-pass deadlock rule; verify that rule fires
    # well before max_iterations
    resources = ResourceStore(eventually_available={"res": 1000})
    result, g = run([Task("a", requires_resource="res")], resources=resources, max_iterations=10)
    assert result.iterations <= 10


def test_snapshots_recorded_per_iteration():
    result, g = run([Task("a"), Task("b", depends_on=("a",))])
    assert len(result.snapshots) == result.iterations
    assert result.snapshots[-1].completed_total == 2


def test_progress_efficiency_property():
    result, g = run([Task("a"), Task("b")])
    assert result.progress_efficiency == result.completed / result.iterations


def test_slow_resource_does_not_falsely_deadlock_across_multiple_silent_polls():
    # Regression test: a resource that takes several polls to resolve used
    # to get force-deadlocked after just one iteration with no visible
    # status change, because the old heuristic couldn't tell "still
    # resolving" apart from "never will". This is the bug that motivated
    # the tri-state retrieve()/ask() signal.
    resources = ResourceStore(eventually_available={"res": 5})
    result, g = run([Task("a", requires_resource="res")], resources=resources, max_iterations=20)
    assert g.tasks["a"].status == Status.DONE
    assert result.iterations >= 5


def test_permanently_missing_resource_deadlocks_promptly_not_via_heuristic():
    resources = ResourceStore()  # 'ghost' never registered anywhere
    result, g = run([Task("a", requires_resource="ghost")], resources=resources, max_iterations=20)
    assert g.tasks["a"].status == Status.DEADLOCKED
    # should be caught on the very first attempt, not after burning iterations
    assert result.iterations == 1


def test_budget_exhausted_is_distinct_from_deadlocked():
    # A resource that would eventually resolve, but not within the budget
    # given, should NOT be mislabeled DEADLOCKED - it never got a definitive
    # proof of being permanently blocked, it just ran out of time.
    resources = ResourceStore(eventually_available={"res": 1000})
    result, g = run([Task("a", requires_resource="res")], resources=resources, max_iterations=5)
    assert g.tasks["a"].status == Status.NEEDS_RESOURCE  # not DEADLOCKED
    assert result.iterations == 5
    assert any("BUDGET EXHAUSTED" in line for line in result.trace)
