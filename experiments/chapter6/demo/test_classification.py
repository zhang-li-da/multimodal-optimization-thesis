import copy

import pytest

from chapter6_demo.benchmarks import evaluate, behavior_distance
from chapter6_demo.classification import prepared, CLASS_FEATURES, _execute
from chapter6_demo.programs import Program


def test_four_split_disjoint_and_complete():
    for data in prepared():
        seen=set()
        for ids in data["split_ids"].values():
            assert not seen.intersection(ids)
            seen.update(ids)
        for split,case in data["cases"].items():
            assert len(case["features"])==len(case["labels"])
            for sample in case["features"]:
                for features in sample:
                    assert set(features)==set(CLASS_FEATURES)


def test_evaluation_labels_do_not_affect_predictions():
    case=prepared()[0]["cases"]["validation"]
    changed=copy.deepcopy(case)
    changed["labels"]=[(y+1)%len(case["classes"]) for y in case["labels"]]
    program=Program('def priority(f):\n    return -f["centroid"]',"classification")
    original=_execute(program,case)
    altered=_execute(program,changed)
    assert original["predictions"]==altered["predictions"]
    assert original["loss"]!=altered["loss"]


def test_score_monotone_transform_keeps_classification_behavior():
    a=evaluate('def priority(f):\n    return f["knn_fraction"]',"classification")
    b=evaluate('def priority(f):\n    renamed = f["knn_fraction"]\n    return 7 * renamed - 3',"classification")
    assert a["valid"] and b["valid"]
    assert a["loss"]==b["loss"]
    assert behavior_distance(a["behavior"],b["behavior"])==0
    assert 0 <= a["loss"] <= 1
