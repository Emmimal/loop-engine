import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent / "src"))

from loop_engineering.graph import Task, TaskGraph, Status
from loop_engineering.resources import ResourceStore, DecisionFixture
from loop_engineering.baseline import run_linear


def test_linear_completes_clean_graph():
    g = TaskGraph([Task("a"), Task("b", depends_on=("a",))])
    result = run_linear(g, ResourceStore(), DecisionFixture())
    assert result.completed == 2
    assert result.halted_at is None


def test_linear_halts_on_missing_resource_and_does_not_touch_rest():
    tasks = [
        Task("blocked", requires_resource="ghost"),
        Task("independent"),
    ]
    g = TaskGraph(tasks)
    result = run_linear(g, ResourceStore(), DecisionFixture())
    assert result.halted_at == "blocked"
    assert result.reason == "resource not available"
    # independent never even attempted, unlike the loop controller
    assert g.tasks["independent"].status.name == "PENDING"


def test_linear_does_not_retry_flaky_task():
    def flaky(task, ctx):
        return task.attempts > 1  # would succeed on 2nd attempt, never gets one

    g = TaskGraph([Task("a", action=flaky)])
    result = run_linear(g, ResourceStore(), DecisionFixture())
    assert result.halted_at == "a"
    assert result.reason == "validation failed"


def test_linear_respects_topological_order_not_insertion_order():
    # 'b' is listed first but depends on 'a' which is listed second
    tasks = [
        Task("b", depends_on=("a",)),
        Task("a"),
    ]
    g = TaskGraph(tasks)
    result = run_linear(g, ResourceStore(), DecisionFixture())
    assert result.completed == 2
    assert result.halted_at is None
