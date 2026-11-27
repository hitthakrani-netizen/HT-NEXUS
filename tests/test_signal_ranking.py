from signal_ranking import select_strongest_signal


def test_selects_highest_rank_score_before_setup_score_and_risk_reward():
    signals = [
        {"symbol": "A", "rank_score": 90, "score": 80, "risk_reward": 1.7},
        {"symbol": "B", "rank_score": 94, "score": 80, "risk_reward": 1.7},
        {"symbol": "C", "rank_score": 94, "score": 85, "risk_reward": 1.2},
        {"symbol": "D", "rank_score": 94, "score": 85, "risk_reward": 1.9},
    ]
    assert select_strongest_signal(signals)["symbol"] == "D"


def test_returns_one_signal_only_and_none_for_empty_or_invalid_input():
    selected = select_strongest_signal([
        {"symbol": "A", "rank_score": 90},
        {"symbol": "B", "rank_score": 95},
    ])
    assert isinstance(selected, dict)
    assert selected["symbol"] == "B"
    assert select_strongest_signal([]) is None
    assert select_strongest_signal([None, "bad"]) is None


def test_invalid_rank_values_do_not_break_selection():
    signals = [
        {"symbol": "A", "rank_score": "not-a-number", "score": 100},
        {"symbol": "B", "rank_score": 1, "score": 80},
    ]
    assert select_strongest_signal(signals)["symbol"] == "B"
