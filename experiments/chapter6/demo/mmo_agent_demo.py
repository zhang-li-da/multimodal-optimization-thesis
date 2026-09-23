"""Compatibility entry point for the real live program-discovery demo.

The initial stochastic TSP configuration prototype was withdrawn because route
randomness is not evidence of algorithm diversity. Development smoke outputs
are excluded from scientific analyses. See discovery.py and README.md.
"""
from .discovery import main


if __name__ == "__main__":
    main()
