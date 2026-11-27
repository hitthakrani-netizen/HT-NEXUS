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
    assert result["eligible_for_confirmation"] == 3


def test_stale_quote_is_an_ineligible_stock_not_a_full_scan_failure():
    rows = [
        {"Stock": "RELIANCE", "Status": "Data OK"},
        {"Stock": "TCS", "Status": "Rejected: no fresh NSE_EQ live quote — INACTIVE: last trade is stale (420s)"},
        {"Stock": "SBIN", "Status": "No setup: LONG failed filters"},
    ]
    result = assess_scan_coverage(rows, expected_count=3)
    assert result["complete"] is True
    assert result["processed"] == 3
    assert result["failed"] == 0
    assert result["inactive_quotes"] == 1
    assert result["eligible_for_confirmation"] == 2


def test_zero_volume_quote_is_an_ineligible_stock_not_a_full_scan_failure():
    rows = [
        {"Stock": "RELIANCE", "Status": "Rejected: no fresh NSE_EQ live quote — INACTIVE: quote has zero day volume"},
        {"Stock": "TCS", "Status": "No setup: LONG failed filters"},
    ]
    result = assess_scan_coverage(rows, expected_count=2)
    assert result["complete"] is True
    assert result["inactive_quotes"] == 1
    assert result["eligible_for_confirmation"] == 1


def test_missing_quote_record_remains_a_full_scan_failure():
    rows = [
        {"Stock": "RELIANCE", "Status": "Data OK"},
        {"Stock": "TCS", "Status": "Rejected: no fresh NSE_EQ live quote — DATA GAP: no quote returned"},
    ]
    result = assess_scan_coverage(rows, expected_count=2)
    assert result["complete"] is False
    assert result["failed"] == 1
    assert "missing quote/data" in result["reason"]


def test_malformed_quote_data_remains_a_full_scan_failure():
    rows = [
        {"Stock": "RELIANCE", "Status": "Rejected: no fresh NSE_EQ live quote — DATA ERROR: missing last_trade_time"},
    ]
    result = assess_scan_coverage(rows, expected_count=1)
    assert result["complete"] is False
    assert result["failed"] == 1


def test_partial_worker_error_blocks_strongest_signal_publication():
    rows = [
        {"Stock": "RELIANCE", "Status": "Data OK"},
        {"Stock": "TCS", "Status": "SCAN ERROR: Dhan timeout"},
        {"Stock": "SBIN", "Status": "No setup"},
    ]
    result = assess_scan_coverage(rows, expected_count=3)
    assert result["complete"] is False
    assert result["failed"] == 1
    assert "processing failures" in result["reason"]


def test_insufficient_history_is_an_explicit_stock_exclusion_not_a_scan_error():
    rows = [
        {"Stock": "RELIANCE", "Status": "Insufficient history: need 220 intraday / 205 daily"},
        {"Stock": "TCS", "Status": "No setup"},
    ]
    result = assess_scan_coverage(rows, expected_count=2)
    assert result["complete"] is True
    assert result["failed"] == 0
    assert result["insufficient_history"] == 1
    assert result["eligible_for_confirmation"] == 1


def test_missing_stock_diagnostic_is_incomplete():
    result = assess_scan_coverage([{"Stock": "RELIANCE", "Status": "Data OK"}], expected_count=2)
    assert result["complete"] is False
    assert "Processed 1 of 2" in result["reason"]


def test_empty_universe_is_incomplete():
    result = assess_scan_coverage([], expected_count=0)
    assert result["complete"] is False
    assert "F&O universe is empty" in result["reason"]
