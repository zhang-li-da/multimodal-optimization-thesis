import copy

import pytest

from chapter6_demo.providers import Completion
from chapter6_demo.test_science import node
from chapter6_demo.benchmarks import SEEDS
from chapter6_demo.classification import CLASS_FEATURES, _execute
from chapter6_demo.programs import Program
from .benchmarks import Benchmark
from .budget import BudgetStop, TokenBudget, input_upper_bound
from .protocol import fixed_archive


class FakeClient:
    def __init__(self, input_tokens=100, output_tokens=100):
        self.calls = 0
        self.input_tokens, self.output_tokens = input_tokens, output_tokens

    def complete(self, system, prompt, max_tokens):
        self.calls += 1
        return Completion("{}", "test", self.input_tokens, self.output_tokens, .1, "test")


def test_insufficient_budget_never_calls_the_model():
    client = FakeClient()
    budget = TokenBudget(100)
    with pytest.raises(BudgetStop):
        budget.request(client, "system", "prompt", 2000, "planner", 0)
    assert client.calls == 0 and budget.used == 0


def test_admission_uses_actual_spend_and_charges_partial_iteration():
    client = FakeClient(100, 100)
    bound = input_upper_bound("system", "prompt")
    budget = TokenBudget(bound + 100)
    _, record = budget.request(client, "system", "prompt", 100, "planner", 0)
    assert record["usage_contract_valid"] and budget.used == 200
    with pytest.raises(BudgetStop):
        budget.request(client, "system", "prompt", 100, "coder", 0)
    assert client.calls == 1 and budget.used == 200


def test_provider_bound_violation_is_explicitly_invalid():
    client = FakeClient(2000, 100)
    budget = TokenBudget(10000)
    _, record = budget.request(client, "system", "prompt", 100, "planner", 0)
    assert not record["usage_contract_valid"]
    assert budget.used == 2100


def test_common_gate_cannot_expand_when_one_methods_best_is_worse():
    good = [node(0, [1, 0, 0], .01), node(1, [0, 1, 0], .04)]
    worse = [node(0, [1, 0, 0], .04), node(1, [0, 0, 1], .075)]
    assert len(fixed_archive(good, .05)) == 2
    assert len(fixed_archive(worse, .05)) == 1


@pytest.mark.parametrize("task", ["tsp", "binpack", "classification"])
def test_fresh_partitions_and_splits_are_distinct(task):
    first, second = Benchmark(task, 1), Benchmark(task, 2)
    assert first.fingerprint("validation") != second.fingerprint("validation")
    seen = set()
    if task == "classification":
        for dataset in first.classification:
            seen = set()
            for ids in dataset["split_ids"].values():
                assert not seen.intersection(ids)
                seen.update(ids)
            for case in dataset["cases"].values():
                assert all(set(f) == set(CLASS_FEATURES) for sample in case["features"] for f in sample)
    else:
        for instances in first.data.values():
            ids = {x["id"] for x in instances}
            assert not seen.intersection(ids)
            seen.update(ids)


def test_new_classification_labels_cannot_change_predictions():
    benchmark = Benchmark("classification", 1)
    first = benchmark.data["validation"][0]
    second = copy.deepcopy(first)
    second["labels"] = [(x + 1) % 3 for x in second["labels"]]
    program = Program(SEEDS["classification"][0][2], "classification")
    assert _execute(program, first)["predictions"] == _execute(program, second)["predictions"]


def test_new_evaluator_keeps_seed_code_equivalence():
    benchmark = Benchmark("binpack", 1)
    first = benchmark.evaluate('def priority(f):\n    return -f["gap"]')
    second = benchmark.evaluate('def priority(f):\n    return 2 - 3 * f["gap"]')
    assert first["behavior"] == second["behavior"]
    assert first["loss"] == second["loss"]
