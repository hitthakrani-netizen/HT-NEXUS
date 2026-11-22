from strategy_engine import MIN_QUALITY_SCORE, validate_btst_confirmation


def _signals(score=80, pre_rank=82, final_rank=70, side="LONG", final_side=None):
    return (
        {"side": side, "rank_score": pre_rank},
        {"side": final_side or side, "rank_score": final_rank, "score": score},
    )


def test_btst_accepts_documented_quality_floor_with_fresh_same_direction_confirmation():
    pre, final = _signals(score=MIN_QUALITY_SCORE)
    passed, reason = validate_btst_confirmation(pre, final, candle_age_min=2.0, interval_minutes=1)
    assert passed is True
    assert "same direction" in reason
    assert "score 80.0/80.0+" in reason


def test_btst_rejects_score_below_shared_strategy_floor():
    pre, final = _signals(score=MIN_QUALITY_SCORE - 1)
    passed, reason = validate_btst_confirmation(pre, final, candle_age_min=2.0, interval_minutes=1)
    assert passed is False
    assert "below 80.0" in reason


def test_btst_rejects_direction_change_between_phases():
    pre, final = _signals(final_side="SHORT")
    passed, reason = validate_btst_confirmation(pre, final, candle_age_min=2.0, interval_minutes=1)
    assert passed is False
    assert "direction changed" in reason


def test_btst_rejects_rank_collapse_beyond_allowed_tolerance():
    pre, final = _signals(pre_rank=90, final_rank=74)
    passed, reason = validate_btst_confirmation(pre, final, candle_age_min=2.0, interval_minutes=1)
    assert passed is False
    assert "rank fell more than 15" in reason


def test_btst_rejects_stale_or_not_yet_closed_confirmation_candles():
    pre, final = _signals()
    stale, stale_reason = validate_btst_confirmation(pre, final, candle_age_min=9.0, interval_minutes=1)
    future, future_reason = validate_btst_confirmation(pre, final, candle_age_min=-0.1, interval_minutes=1)
    assert stale is False and "outside 0–8m" in stale_reason
    assert future is False and "outside 0–8m" in future_reason


def test_btst_reports_missing_shortlist_and_confirmation_separately():
    passed, reason = validate_btst_confirmation(None, None, candle_age_min=2.0, interval_minutes=1)
    assert passed is False
    assert "shortlist did not qualify" in reason
