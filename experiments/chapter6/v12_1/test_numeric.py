"""Tolerance accepts scalar roundoff, never a changed route or nonfinite loss."""
from copy import deepcopy

from .verify_numeric import compare_evaluation


def test_roundoff_is_reported_but_route_changes_fail():
    expected = {"valid": True, "loss": .1, "per_instance_loss": [.1],
                "behavior": [[1, 0]], "solutions": [[0, 1]], "features_called": 7}
    actual = deepcopy(expected)
    actual["loss"] += 2.2e-16
    report = compare_evaluation(actual, expected)
    assert report["within_numeric_contract"] and not report["strict_equal"]
    assert len(report["numeric_differences"]) == 1
    actual["solutions"] = [[1, 0]]
    assert not compare_evaluation(actual, expected)["within_numeric_contract"]


def test_large_numeric_errors_missing_values_and_nan_fail():
    expected = {"valid": True, "loss": .1, "per_instance_loss": [.1]}
    for replacement in (.1001, float("nan"), float("inf"), None):
        assert not compare_evaluation({**expected, "loss": replacement}, expected)["within_numeric_contract"]
    assert not compare_evaluation({"valid": True, "loss": .1}, expected)["within_numeric_contract"]
