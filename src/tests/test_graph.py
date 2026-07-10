import sys
import pathlib
import pytest

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent / "src"))

from loop_engineering.graph import Task, TaskGraph, Status


def test_simple_graph_builds():
    g = TaskGraph([Task("a"), Task("b", depends_on=("a",))])
    assert set(g.tasks) == {"a", "b"}


def test_cycle_detected():
    with pytest.raises(ValueError, match="Cycle"):
        TaskGraph([
            Task("a", depends_on=("b",)),
            Task("b", depends_on=("a",)),
        ])


def test_unknown_dependency_raises():
    with pytest.raises(ValueError, match="unknown task"):
        TaskGraph([Task("a", depends_on=("ghost",))])


def test_dependencies_satisfied():
    g = TaskGraph([Task("a"), Task("b", depends_on=("a",))])
    b = g.tasks["b"]
    assert g.dependencies_satisfied(b) is False
    g.tasks["a"].status = Status.DONE
    assert g.dependencies_satisfied(b) is True


def test_blocked_dependency_detection():
    g = TaskGraph([Task("a"), Task("b", depends_on=("a",))])
    b = g.tasks["b"]
    assert g.blocked_dependency(b) is None
    g.tasks["a"].status = Status.FAILED
    assert g.blocked_dependency(b) == "a"


def test_is_terminal():
    g = TaskGraph([Task("a"), Task("b")])
    assert g.is_terminal() is False
    g.tasks["a"].status = Status.DONE
    g.tasks["b"].status = Status.FAILED
    assert g.is_terminal() is True


def test_counts():
    g = TaskGraph([Task("a"), Task("b")])
    g.tasks["a"].status = Status.DONE
    counts = g.counts()
    assert counts["DONE"] == 1
    assert counts["PENDING"] == 1
