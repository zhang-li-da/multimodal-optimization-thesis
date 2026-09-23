import pytest

from .analyze import holm, paired_interval, sign_flip


def test_sign_flip_known_uniform_effect_and_null():
    assert sign_flip([-1] * 10, "less") == 1 / 1024
    assert sign_flip([1] * 10, "less") == 1
    assert sign_flip([0] * 10, "less") == 1


def test_holm_retains_family_and_order():
    result = holm([.001, .03, .2])
    assert result == pytest.approx([.003, .06, .2])
    assert holm([.2, .001, .03]) == pytest.approx([.2, .003, .06])


def test_paired_interval_uses_blocks_not_nodes():
    assert paired_interval([1, 1, 1])["low"] == 1
    assert paired_interval([1, 1, 1])["high"] == 1
    interval = paired_interval([-1, 0, 1])
    assert interval["mean"] == 0 and interval["low"] < 0 < interval["high"]
    assert interval["n"] == 3
