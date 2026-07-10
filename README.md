# loop-engine

A pure-Python goal-directed execution controller for agent workflows — deterministic failure isolation, retry, and recovery in one control loop, with zero LLM calls and zero external dependencies.

![Python Version](https://img.shields.io/badge/python-3.9%2B-blue) ![License](https://img.shields.io/badge/license-MIT-green)

Most agent loop write-ups assume an LLM sits inside every decision, which makes it impossible to separate "the loop design is good" from "the model got lucky this run." This library isolates the control loop itself — deterministic, testable, with the reasoning step behind a single swappable function call, so the architecture can be verified independently of whatever makes the decisions inside it.

Read the full write-up on Towards Data Science → *[Context Engineering Isn't Enough — A Loop Engineering Experiment With No LLM Inside the Loop](https://towardsdatascience.com/author/emmimalp-alexander/)*

## What It Does

```
TaskGraph (DAG) → LoopController → RETRIEVE / ASK / EXECUTE / REVISE → DONE / FAILED / DEADLOCKED
                        ↑
          ResourceStore (tri-state) / DecisionFixture (tri-state)
```

Six components, one `run()` call:

| Component | Job |
|---|---|
| `TaskGraph` | dependency DAG with cycle detection and status queries |
| `LoopController` | goal-directed state machine: observe, retrieve, ask, execute, revise, block |
| `ResourceStore` | tri-state `retrieve()` — resolved / pending / missing, simulating slow and permanently missing resources |
| `DecisionFixture` | tri-state `ask()` — resolved / missing, simulating resolvable and genuinely unanswerable ambiguity |
| `run_linear` (baseline) | naive one-shot linear executor for comparison, correctness floor only |
| `build_scenario` | seeded synthetic task graph generator with a controlled mix of failure modes |

## Installation

```bash
git clone https://github.com/Emmimal/loop-engine.git
cd loop-engine
pip install -e .
```

No external dependencies. Runs entirely on the Python standard library.

## Quick Start

```python
from loop_engineering import Task, TaskGraph, LoopController, ResourceStore, DecisionFixture

def always_valid(task, context):
    return True

def flaky_once(task, context):
    return task.attempts > 1  # fails first attempt, succeeds after

tasks = [
    Task("fetch_config", requires_resource="config"),
    Task("validate", depends_on=("fetch_config",), action=flaky_once, max_retries=2),
    Task("independent_branch", action=always_valid),  # unrelated to the above
]

resources = ResourceStore(eventually_available={"config": 2})  # resolves after 2 polls
decisions = DecisionFixture()

result = LoopController(TaskGraph(tasks), resources, decisions, max_iterations=50).run()

print(f"Completed: {result.completed}/3 in {result.iterations} iterations")
print(f"Recoveries: {result.total_recoveries}")
```

## Running the Benchmark and Sanity Checks

| Command | What It Shows |
|---|---|
| `python -m pytest tests/ -v` | 30 tests covering the graph, resources, controller state machine, and linear baseline |
| `python sanity_check.py` | 5 validation checks against the benchmark's own ground truth before trusting any number from it |
| `python benchmark.py` | Goal-directed controller vs. linear baseline across 9 seeded configurations, plus a per-iteration progress table |

The sanity checks answer five specific questions: are failure modes injected at the intended rate, does every downstream deadlock trace back to a real blocker, do recovery events occur in the expected proportions, does a bigger iteration budget ever change which tasks get marked permanently stuck, and is the result stable across 300 random seeds rather than a lucky sample.

## Configuration Reference

```python
LoopController(
    graph,               # TaskGraph
    resources,            # ResourceStore
    decisions,             # DecisionFixture
    max_iterations=100,    # safety budget; exhausting it is reported distinctly from a proven deadlock
)

Task(
    task_id,
    depends_on=(),          # tuple of task_ids this task waits on
    requires_resource=None,  # key looked up via ResourceStore.retrieve()
    requires_decision=None,  # key looked up via DecisionFixture.ask()
    action=None,              # Callable[[Task, dict], bool] — the swappable reasoning step
    max_retries=2,
)
```

Swapping `action` for an LLM call (same signature: task and context in, success/fail out) changes nothing else in the control flow. That seam is the entire architectural point of this repo.


## Project Structure
 
```
loop_engineering/
├── src/
│   ├── loop_engineering/
│   │   ├── __init__.py
│   │   ├── baseline.py
│   │   ├── controller.py
│   │   ├── graph.py
│   │   ├── resources.py
│   │   └── scenarios.py
│   └── loop_engineering.egg-info/
│       ├── dependency_links.txt
│       ├── PKG-INFO
│       ├── SOURCES.txt
│       └── top_level.txt
├── tests/
│   ├── test_baseline.py
│   ├── test_controller.py
│   ├── test_graph.py
│   └── test_resources.py
├── benchmark.py
├── pyproject.toml
└── sanity_check.py
```

## Benchmark Results (deterministic, no LLM, 300 seeds)

| Metric | Goal-Directed Controller | Linear Baseline |
|---|---|---|
| Branches fully completed (avg) | 3.3 / 10.3 | 0.4 / 10.3 |
| Task completion, 300-seed mean | 47.7% (stdev 11.8) | 2.1% (stdev 2.7) |
| Task completion, 9-config sample | 46.7% | 8.3% |
| Injection rate accuracy | within 0.5% of design on all 6 failure modes | — |
| Premature-deadlock false positives | 0 across 9 configs at 50 vs. 2000 iterations | — |

The controller wasn't better because it solved impossible tasks. It was better because it refused to let one impossible task stop every other possible task. Permanently missing resources and unanswerable decisions stay unresolved in both systems, by design.

## When to Use This

Worth it when you have:

- A pipeline with genuinely independent branches, where one branch's failure shouldn't take down the rest
- A need to distinguish "still resolving" from "permanently stuck" instead of guessing from a single pass
- A control-flow claim you want to verify without the variance an LLM would introduce

Skip it when you have:

- A strictly sequential pipeline with no independent branches to isolate
- A short-running job where a simple top-level retry is cheaper to maintain than a state machine
- A bottleneck in decision *quality* rather than in what happens after a step fails. That's a reasoning problem, and no control-flow pattern fixes it.

## Known Limitations

- This is one implementation of one piece of loop engineering (the control loop itself), not a full system. There's no persistent memory across runs, no scheduling, and no discovery step that finds new work on its own.
- `DecisionFixture` has no "pending" state. An answer either exists or it doesn't. A real human-in-the-loop or LLM-backed version would need a third state for "queued, not yet answered."
- The linear baseline gets exactly one attempt per task in a valid topological order. That's the honest default behavior of code with no control loop, not a strawman, but it also means the completion gap partly reflects the baseline's all-or-nothing failure mode by design, not a general claim about capability.
- All scenario generation is synthetic and seeded. It has not been validated against a real production pipeline's failure distribution.

## Resources

- Addy Osmani, "Loop Engineering," June 2026. https://addyosmani.com/blog/loop-engineering/
- Reporting on the loop engineering discourse (Cherny, Steinberger), via Let's Data Science. https://letsdatascience.com/news/engineers-embrace-loop-engineering-for-ai-agents-cb1a1d6a
- Geoffrey Huntley's "Ralph" technique, a predecessor pattern, via Tosea.ai. https://tosea.ai/blog/loop-engineering-ai-agents-complete-guide-2026

## License

MIT
