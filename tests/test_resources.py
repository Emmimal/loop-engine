import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent / "src"))

from loop_engineering.resources import ResourceStore, DecisionFixture


def test_immediately_available_resource():
    store = ResourceStore(available={"x": "value"})
    status, value = store.retrieve("x")
    assert status == "resolved"
    assert value == "value"


def test_missing_resource_never_resolves():
    store = ResourceStore()
    status, _ = store.retrieve("ghost")
    assert status == "missing"
    status, _ = store.retrieve("ghost")
    assert status == "missing"


def test_eventually_available_resource_takes_n_attempts():
    store = ResourceStore(eventually_available={"slow": 3})
    s1, _ = store.retrieve("slow")
    s2, _ = store.retrieve("slow")
    s3, val3 = store.retrieve("slow")
    assert (s1, s2, s3) == ("pending", "pending", "resolved")
    assert val3 == "resolved:slow"
    # once resolved, stays resolved
    s4, val4 = store.retrieve("slow")
    assert s4 == "resolved"
    assert val4 == "resolved:slow"


def test_answerable_decision():
    fixture = DecisionFixture(answers={"q": "42"})
    status, value = fixture.ask("q")
    assert status == "resolved"
    assert value == "42"


def test_unanswerable_decision():
    fixture = DecisionFixture()
    status, value = fixture.ask("mystery")
    assert status == "missing"
    assert value is None
