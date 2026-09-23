"""Compatibility import path for the Chapter 6 demo sources.

The thesis repository keeps the implementation under ``experiments/chapter6``
so that source and experiment material stay together.  This package preserves
the documented ``chapter6_demo`` module name used by the run commands and
tests.
"""
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
_CHAPTER6 = _REPO / "experiments" / "chapter6"
__path__ = [str(_CHAPTER6 / "demo"), str(_CHAPTER6)]
