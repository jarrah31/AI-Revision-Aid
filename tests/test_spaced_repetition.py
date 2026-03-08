"""Unit tests for the SM-2 spaced repetition algorithm."""
import pytest
from datetime import date, timedelta

from backend.services.spaced_repetition import sm2_update, SRSUpdate


# ── Return type ───────────────────────────────────────────────────────────────

def test_sm2_returns_srs_update():
    result = sm2_update(5)
    assert isinstance(result, SRSUpdate)
    assert isinstance(result.easiness_factor, float)
    assert isinstance(result.interval_days, int)
    assert isinstance(result.repetitions, int)
    assert isinstance(result.next_review_date, date)


# ── Correct answers (quality >= 3) ───────────────────────────────────────────

def test_first_correct_answer_interval_is_1(dummy_db=None):
    """First ever correct answer: reps=0 → interval=1, reps becomes 1."""
    result = sm2_update(quality=5, current_ef=2.5, current_interval=0, current_reps=0)
    assert result.interval_days == 1
    assert result.repetitions == 1


def test_second_correct_answer_interval_is_6():
    """Second correct answer: reps=1 → interval=6, reps becomes 2."""
    result = sm2_update(quality=5, current_ef=2.5, current_interval=1, current_reps=1)
    assert result.interval_days == 6
    assert result.repetitions == 2


def test_third_correct_answer_uses_ef_multiplier():
    """Third+ correct answer: interval = round(current_interval * new_ef)."""
    ef = 2.5
    result = sm2_update(quality=5, current_ef=ef, current_interval=6, current_reps=2)
    # new_ef after quality=5: ef + (0.1 - 0*0.08 - 0*0.02) = ef + 0.1 = 2.6
    expected_ef = round(ef + 0.1 - (5 - 5) * (0.08 + (5 - 5) * 0.02), 2)
    expected_interval = round(6 * expected_ef)
    assert result.interval_days == expected_interval
    assert result.repetitions == 3
    assert abs(result.easiness_factor - expected_ef) < 0.01


def test_quality_3_still_counts_as_correct():
    result = sm2_update(quality=3, current_ef=2.5, current_interval=0, current_reps=0)
    assert result.repetitions == 1
    assert result.interval_days == 1


# ── Incorrect answers (quality < 3) ──────────────────────────────────────────

def test_incorrect_resets_repetitions_to_0():
    result = sm2_update(quality=2, current_ef=2.5, current_interval=10, current_reps=5)
    assert result.repetitions == 0


def test_incorrect_resets_interval_to_1():
    result = sm2_update(quality=1, current_ef=2.5, current_interval=20, current_reps=8)
    assert result.interval_days == 1


def test_quality_0_resets_correctly():
    result = sm2_update(quality=0, current_ef=2.5, current_interval=15, current_reps=4)
    assert result.repetitions == 0
    assert result.interval_days == 1


# ── Easiness factor adjustments ───────────────────────────────────────────────

def test_ef_increases_on_perfect_answer():
    result = sm2_update(quality=5, current_ef=2.5)
    assert result.easiness_factor > 2.5


def test_ef_decreases_on_difficult_correct_answer():
    """Quality 3 (barely correct) should decrease EF."""
    result = sm2_update(quality=3, current_ef=2.5)
    assert result.easiness_factor < 2.5


def test_ef_decreases_on_incorrect_answer():
    result = sm2_update(quality=1, current_ef=2.5)
    assert result.easiness_factor < 2.5


def test_ef_never_drops_below_1_3():
    """EF should be clamped to a minimum of 1.3."""
    # Repeatedly apply quality=0 to drive EF down
    ef = 2.5
    for _ in range(20):
        result = sm2_update(quality=0, current_ef=ef)
        ef = result.easiness_factor
    assert ef >= 1.3


def test_ef_clamped_to_1_3_on_single_very_bad_answer():
    result = sm2_update(quality=0, current_ef=1.31)
    assert result.easiness_factor >= 1.3


# ── Quality clamping ──────────────────────────────────────────────────────────

def test_quality_above_5_is_clamped_to_5():
    result_5 = sm2_update(quality=5)
    result_10 = sm2_update(quality=10)  # should be treated as 5
    assert result_5.easiness_factor == result_10.easiness_factor
    assert result_5.interval_days == result_10.interval_days


def test_quality_below_0_is_clamped_to_0():
    result_0 = sm2_update(quality=0)
    result_neg = sm2_update(quality=-5)
    assert result_0.easiness_factor == result_neg.easiness_factor


# ── Next review date ──────────────────────────────────────────────────────────

def test_next_review_date_is_today_plus_interval():
    result = sm2_update(quality=5, current_ef=2.5, current_interval=0, current_reps=0)
    expected = date.today() + timedelta(days=result.interval_days)
    assert result.next_review_date == expected


def test_next_review_date_after_incorrect():
    result = sm2_update(quality=0, current_ef=2.5, current_interval=30, current_reps=10)
    expected = date.today() + timedelta(days=1)
    assert result.next_review_date == expected


# ── EF rounding ───────────────────────────────────────────────────────────────

def test_ef_rounded_to_2_decimal_places():
    result = sm2_update(quality=4, current_ef=2.5)
    # Result should be rounded to 2 decimal places
    assert result.easiness_factor == round(result.easiness_factor, 2)
