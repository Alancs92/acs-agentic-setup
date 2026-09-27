"""Unit tests for the parts of metrics.py that decide verdicts."""
import math

import metrics as M


def P(label, p):
    return {"label": label, "p": p}


def test_wilson_lb_perfect_small_sample_is_well_below_one():
    # 28 of 28 correct -> LB ~0.88: why T1's 1.00 gate is unprovable with 28 positives
    assert 0.87 < M.wilson_lb(28, 28) < 0.89
    assert M.wilson_lb(0, 0) == 0.0


def test_threshold_takes_largest_prefix_meeting_precision():
    rows = [(P(True, 0.9), True), (P(True, 0.8), True), (P(True, 0.7), False), (P(True, 0.6), True)]
    thr, k = M.pick_threshold(rows, "noul", "T1", 1.0)
    assert (thr, k) == (0.8, 2)
    thr, k = M.pick_threshold(rows, "noul", "T5", 0.75)
    assert (thr, k) == (0.6, 4)


def test_ties_enter_together():
    rows = [(P(True, 0.9), True), (P(True, 0.9), False)]
    thr, _ = M.pick_threshold(rows, "noul", "T1", 1.0)
    assert thr == math.inf


def test_noul_coverage_is_recall_of_positives():
    rows = [(P(True, 0.9), True), (P(True, 0.2), True), (P(True, 0.1), False)]
    r = M.apply_threshold(rows, "noul", "T1", 0.5)
    assert r["coverage"] == 0.5 and r["precision"] == 1.0


def test_t2_dismiss_never_acts():
    d = {"dismiss": 0.99, "add": 0.01}
    rows = [(P("dismiss", d), "dismiss")]
    assert M.apply_threshold(rows, "choice", "T2", 0.5)["acted"] == 0
    assert M.apply_threshold(rows, "choice", "T3", 0.5)["acted"] == 1


def test_abstain_never_acts():
    assert not M.acts({"label": None, "p": None}, "choice", "T3")
