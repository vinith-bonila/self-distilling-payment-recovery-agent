"""Evaluation harness, scenario generator and customer simulator.

This package depends on the production layers; the production layers must never
depend on it. In particular, no production module may import
:mod:`evals.ground_truth` (enforced by ``tests/test_no_ground_truth_import.py``).
"""
