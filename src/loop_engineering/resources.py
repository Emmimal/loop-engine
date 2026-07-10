"""
resources.py

Deterministic stand-ins for the two "external world" operations a real
agent loop would need: retrieve() and ask().

retrieve(): in a real system this might be a database read, an API call,
or a file fetch. Here it's a dict lookup against a resource store, with
support for resources that only become available after N attempts
(simulating latency/eventual availability) and resources that are
permanently missing (simulating a genuine hard blocker).

ask(): in a real system this might be a human-in-the-loop prompt or an
LLM call. Here it's a lookup against a fixture of predefined answers,
again with support for genuinely unanswerable questions (no fixture
entry exists) to simulate real ambiguity that the loop cannot resolve
on its own.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ResourceStore:
    # resource_key -> available immediately
    available: dict[str, object] = field(default_factory=dict)
    # resource_key -> attempts required before it becomes available
    eventually_available: dict[str, int] = field(default_factory=dict)
    # tracks how many times each eventually-available resource has been polled
    _poll_counts: dict[str, int] = field(default_factory=dict)

    def retrieve(self, key: str) -> tuple[str, object]:
        """Returns (status, value) where status is one of:
        'resolved' - value is ready, use it
        'pending'  - not ready yet, but will resolve after more polling
        'missing'  - permanently unavailable, no amount of polling will help

        A plain boolean return here was the original design and it was a
        real bug: the controller could not tell 'ask again next iteration'
        apart from 'never going to happen', so it either declared victory
        too early or stalled the whole graph waiting on something that was
        never coming. The three-state signal is the fix.
        """
        if key in self.available:
            return "resolved", self.available[key]
        if key in self.eventually_available:
            self._poll_counts[key] = self._poll_counts.get(key, 0) + 1
            if self._poll_counts[key] >= self.eventually_available[key]:
                value = f"resolved:{key}"
                self.available[key] = value
                return "resolved", value
            return "pending", None
        # unknown key: permanently missing
        return "missing", None


@dataclass
class DecisionFixture:
    # decision_key -> predefined answer. Absence means genuinely unanswerable.
    answers: dict[str, object] = field(default_factory=dict)

    def ask(self, key: str) -> tuple[str, object]:
        """Returns (status, value) where status is 'resolved' or 'missing'.
        Decisions have no 'pending' state in this fixture model: either an
        answer exists or it genuinely does not. (A real human-in-the-loop
        or LLM-backed version would need a 'pending' state too, e.g. a
        question queued for a person who hasn't answered yet.)
        """
        if key in self.answers:
            return "resolved", self.answers[key]
        return "missing", None
