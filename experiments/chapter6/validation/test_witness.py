import pytest

from .witness_mechanism import interval, program_pairs


def test_enumerated_program_disagreements_are_known():
    pairs = program_pairs()
    assert [int(p["disagreement"].sum()) for p in pairs] == [0, 3, 13, 51]


def test_exact_interval_edge_cases_and_symmetry():
    assert interval(0, 10, .05)[0] == 0
    assert interval(10, 10, .05)[1] == 1
    a, b = interval(2, 10, .05), interval(8, 10, .05)
    assert a[0] == pytest.approx(1 - b[1])
    assert a[1] == pytest.approx(1 - b[0])
