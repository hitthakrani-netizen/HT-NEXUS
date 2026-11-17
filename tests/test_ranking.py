from strategy_engine import best_signal


def test_best_signal_uses_rank_score_before_raw_score():
    candidates = [
        {"symbol": "A", "rank_score": 70, "score": 99, "risk_reward": 2},
        {"symbol": "B", "rank_score": 90, "score": 88, "risk_reward": 1.7},
    ]
    assert best_signal(candidates)["symbol"] == "B"


def test_best_signal_returns_none_for_empty_candidates():
    assert best_signal([]) is None
