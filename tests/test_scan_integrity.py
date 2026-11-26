from scan_integrity import assess_scan_coverage


def test_full_universe_with_completed_technical_rejections_is_complete():
    rows = [
        {"Stock": "RELIANCE", "Status": "Data OK"},
        {"Stock": "TCS", "Status": "No setup: LONG failed filters"},
        {"Stock": "SBIN", "Status": "No setup: SHORT failed filters"},
    ]
    result = assess_scan_coverage(rows, expected_count=3)
    assert result["complete"] is True
    assert result["processed"] == 3
    assert result["failed"] == 0


def test_missing_or_stale_initial_quote_makes_whole_scan_incomplete():
    rows = [
        {"Stock": "RELIANCE", "Status": "Data OK"},
        {"Stock": "TCS", "Status": "Rejected: no fresh NSE_EQ live quote — stale"},
        {"Stock": "SBIN", "Status": "No setup: LONG failed filters"},
    ]
    result = assess_scan_coverage(rows, expected_count=3)
    assert result["complete"] is False
    assert result["processed"] == 3
    assert result["failed"] == 1
    assert "data/history failures" in result["reason"]


def test_partial_worker_error_blocks_strongest_signal_publication():
    rows = [
        {"Stock": "RELIANCE", "Status": "Data OK"},
        {"Stock": "TCS", "Status": "SCAN ERROR: Dhan timeout"},
        {"Stock": "SBIN", "Status": "No setup"},
    ]
    result = assess_scan_coverage(rows, expected_count=3)
    assert result["complete"] is False
    assert result["failed"] == 1
    assert "data/history failures" in result["reason"]


def test_insufficient_history_is_not_reported_as_a_complete_no_trade():
    rows = [
        {"Stock": "RELIANCE", "Status": "Insufficient history: need 220"},
        {"Stock": "TCS", "Status": "No setup"},
    ]
    result = assess_scan_coverage(rows, expected_count=2)
    assert result["complete"] is False
    assert result["failed"] == 1


def test_missing_stock_diagnostic_is_incomplete():
    result = assess_scan_coverage([{"Stock": "RELIANCE", "Status": "Data OK"}], expected_count=2)
    assert result["complete"] is False
    assert "Processed 1 of 2" in result["reason"]
